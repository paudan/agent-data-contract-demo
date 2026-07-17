"""A2A-native Forecasting Service supplier simulation agent."""

import json
import os
import redis.asyncio as redis_asyncio
from opentelemetry import trace
from a2a_redis import RedisTaskStore, RedisStreamsQueueManager, RedisPushNotificationConfigStore
from a2a.server.agent_execution import AgentExecutor, RequestContext
from a2a.server.apps.jsonrpc import A2AStarletteApplication
from a2a.server.events import EventQueue
from a2a.server.request_handlers import DefaultRequestHandler
from a2a.server.tasks import TaskUpdater
from a2a.types import (
    AgentCapabilities,
    AgentCard,
    AgentSkill,
    DataPart,
    Part,
    TextPart,
    UnsupportedOperationError,
)
from a2a.utils import get_data_parts, get_text_parts, new_task
from a2a.utils.errors import ServerError
from google.adk.agents.invocation_context import InvocationContext
from google.adk.events import Event as AdkEvent
from google.adk.sessions.in_memory_session_service import InMemorySessionService
from google.adk.sessions.session import Session

from agents import a2a_redis_patches
from agents.adk_supplier import DEFAULT_SCHEMA_PATH, SupplierADKAgent
from agents.adk_utils import extract_event_text
from agents.telemetry import get_tracer
from agents.turn_tracker import DEFAULT_REDIS_URL, TurnTracker

a2a_redis_patches.apply()

DEFAULT_MAX_TURNS = 5

_tracer = get_tracer()


class SupplierAgentExecutor(AgentExecutor):
    """Adapts the ADK `SupplierADKAgent` to the A2A protocol.

    Maintains one ADK `Session` per A2A task, so the multi-turn
    input-required negotiation maps onto the ADK agent's own session-event
    history exactly as it would running purely in-process.
    """

    def __init__(self, schema_path: str = DEFAULT_SCHEMA_PATH, max_turns: int = DEFAULT_MAX_TURNS,
                 redis_url: str | None = None):
        self.schema_path = schema_path
        self._agent = SupplierADKAgent(name="supplier_agent", schema_path=schema_path)
        self._session_service = InMemorySessionService()
        self._sessions: dict[str, Session] = {}
        self._turns = TurnTracker(max_turns=max_turns, redis_url=redis_url)

    def _get_or_create_session(self, task_id: str) -> Session:
        session = self._sessions.get(task_id)
        if session is None:
            session = Session(id=f"a2a-task-{task_id}", app_name="agent-contracts-a2a", user_id="a2a-client")
            self._sessions[task_id] = session
        return session

    async def execute(self, context: RequestContext, event_queue: EventQueue) -> None:
        with _tracer.start_as_current_span("supplier.execute") as span:
            span.set_attribute("task_id", context.task_id or "")
            span.set_attribute("context_id", context.context_id or "")
            await self._execute_impl(context, event_queue)

    async def _execute_impl(self, context: RequestContext, event_queue: EventQueue) -> None:
        span = trace.get_current_span()

        if not context.current_task:
            await event_queue.enqueue_event(new_task(context.message))

        updater = TaskUpdater(event_queue, context.task_id, context.context_id)
        await updater.start_work()

        # --- Turn budget: recorded in Redis before any decision logic runs ---
        turn_count = self._turns.record_turn(context.context_id)
        span.set_attribute("turn_count", turn_count)
        span.set_attribute("max_turns", self._turns.max_turns)
        if self._turns.is_exceeded(turn_count):
            span.set_attribute("status", "max_turns_exceeded")
            self._turns.reset(context.context_id)
            status_message = updater.new_agent_message([Part(root=TextPart(text=(
                f"Maximum number of negotiation turns ({self._turns.max_turns}) exceeded for this "
                "session. Stopping negotiation."
            )))])
            await updater.add_artifact(
                parts=[Part(root=DataPart(data={"data": [], "global_errors": ["max_turns_exceeded"]}))],
                name="max_turns_exceeded",
            )
            await updater.failed(message=status_message)
            return

        # --- Communication processing: A2A Message -> ADK Event ---
        message = context.message
        data_parts = get_data_parts(message.parts) if message else []
        payload = data_parts[0] if data_parts else {}
        data = payload.get("data", []) if isinstance(payload, dict) else []
        text_parts = get_text_parts(message.parts) if message else []
        chat_message = text_parts[0] if text_parts else ""

        session = self._get_or_create_session(context.task_id)
        session.events.append(
            AdkEvent(author="client_agent", message=json.dumps({"chat_message": chat_message, "data": data}))
        )

        # --- Agent running: pure ADK, no knowledge of A2A ---
        invocation_ctx = InvocationContext(
            invocation_id=f"inv-{context.task_id}",
            session=session,
            session_service=self._session_service,
            user_content=None,
        )
        adk_events = [e async for e in self._agent.run_async(invocation_ctx)]
        adk_event = adk_events[-1]
        session.events.append(adk_event)

        response = json.loads(extract_event_text(adk_event))

        # --- Communication processing: ADK Event -> A2A Task update ---
        status_message = updater.new_agent_message([Part(root=TextPart(text=response["chat_message"]))])

        span.set_attribute("status", response["status"])

        if response["status"] == "success":
            self._turns.reset(context.context_id)
            await updater.add_artifact(
                parts=[Part(root=DataPart(data={"data": response["data"], "global_errors": []}))],
                name="forecast_result",
            )
            await updater.complete(message=status_message)
        else:
            await updater.add_artifact(
                parts=[Part(root=DataPart(data={"data": response["data"], "global_errors": response.get("global_errors", [])}))],
                name="validation_result",
            )
            await updater.requires_input(message=status_message)

    async def cancel(self, context: RequestContext, event_queue: EventQueue) -> None:
        raise ServerError(UnsupportedOperationError(message='Cancellation is not supported by the supplier agent.'))


def build_supplier_agent_card(base_url: str) -> AgentCard:
    """Builds the public AgentCard advertising the forecasting service's skill."""
    rpc_url = base_url.rstrip('/') + '/'
    return AgentCard(
        name="forecasting-supplier-agent",
        description="Validates sales records against the forecasting data contract and, once compliant, returns forecasts.",
        version="1.0.0",
        url=rpc_url,
        default_input_modes=["application/json"],
        default_output_modes=["application/json"],
        capabilities=AgentCapabilities(streaming=False, push_notifications=False),
        skills=[
            AgentSkill(
                id="validate-and-forecast",
                name="Validate and Forecast",
                description="Validates a batch of sales records against the forecasting data contract; returns validation errors or forecasts.",
                tags=["forecasting", "data-contract", "validation"],
                input_modes=["application/json"],
                output_modes=["application/json"],
            )
        ],
    )


def build_supplier_app(schema_path: str = DEFAULT_SCHEMA_PATH, base_url: str = "http://supplier.local/",
                        max_turns: int = DEFAULT_MAX_TURNS, redis_url: str | None = None):
    """Builds the ASGI app hosting the supplier's A2A endpoints. `redis_url`
    defaults to redis://localhost:6379/0 or $REDIS_URL if unset."""
    resolved_redis_url = redis_url or os.environ.get("REDIS_URL", DEFAULT_REDIS_URL)
    redis_client = redis_asyncio.Redis.from_url(resolved_redis_url)

    agent_card = build_supplier_agent_card(base_url)
    request_handler = DefaultRequestHandler(
        agent_executor=SupplierAgentExecutor(schema_path=schema_path, max_turns=max_turns, redis_url=resolved_redis_url),
        task_store=RedisTaskStore(redis_client, prefix="a2a:tasks:"),
        queue_manager=RedisStreamsQueueManager(redis_client, prefix="a2a:queues:"),
        push_config_store=RedisPushNotificationConfigStore(redis_client, prefix="a2a:push:")
    )

    return A2AStarletteApplication(agent_card=agent_card, http_handler=request_handler).build()
