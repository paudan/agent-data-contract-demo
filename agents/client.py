"""Runs the client-side A2A negotiation against a real supplier server.
No client-side turn limit -- see README.md for the turn-budget design.
"""

import json
import os
import uuid
from a2a.client import A2ACardResolver, ClientConfig, ClientFactory
from a2a.types import DataPart, Message, MessageSendConfiguration, Part, Role, TaskState, TextPart
from a2a.utils import get_data_parts, get_message_text
from google.adk.agents.invocation_context import InvocationContext
from google.adk.events import Event as AdkEvent
from google.adk.sessions.in_memory_session_service import InMemorySessionService
from google.adk.sessions.session import Session

from agents.adk_client import ClientADKAgent
from agents.adk_utils import extract_event_text
from agents.logic import DEFAULT_SCHEMA_PATH
from agents.telemetry import get_tracer

_tracer = get_tracer()


class A2AClientRunner:
    """Drives one client<->supplier negotiation over the A2A protocol."""

    def __init__(self, initial_data: list, repair_strategy: str = "standard", schema_path: str = DEFAULT_SCHEMA_PATH):
        self.initial_data = initial_data
        self.repair_strategy = repair_strategy
        self.schema_path = schema_path

    @staticmethod
    def _log(message_log_dir: str | None, filename: str, model) -> None:
        if not message_log_dir:
            return
        os.makedirs(message_log_dir, exist_ok=True)
        with open(os.path.join(message_log_dir, filename), "w") as f:
            json.dump(model.model_dump(mode="json", exclude_none=True), f, indent=2)

    async def run(self, httpx_client, base_url: str, message_log_dir: str | None = None) -> tuple[bool, int, dict]:
        """Runs the negotiation loop against the agent hosted at base_url.

        Caller owns `httpx_client`'s lifecycle. If `message_log_dir` is set,
        writes each turn's request/response JSON there for later review.

        Returns (success, calls_used, last_response).
        """
        resolver = A2ACardResolver(httpx_client, base_url)
        card = await resolver.get_agent_card()
        factory = ClientFactory(ClientConfig(streaming=False, httpx_client=httpx_client))
        client = factory.create(card)

        # --- Agent running: pure ADK, no knowledge of A2A ---
        # Unique per negotiation (unlike a fixed literal), so client-side ADK
        # spans are distinguishable run-to-run, mirroring how the supplier
        # keys its own session/invocation off the real task_id.
        run_id = str(uuid.uuid4())
        session = Session(id=f"a2a-client-{run_id}", app_name="agent-contracts-a2a", user_id="user-123")
        session_service = InMemorySessionService()
        agent = ClientADKAgent(
            name="client_agent",
            repair_strategy=self.repair_strategy,
            initial_data=self.initial_data,
            new_invocation=True,
            schema_path=self.schema_path,
        )
        invocation_ctx = InvocationContext(
            invocation_id=f"inv-{run_id}",
            session=session,
            session_service=session_service,
            user_content=None,
        )

        task_id = None
        context_id = None
        last_response = None
        turn = 1

        while True:
            with _tracer.start_as_current_span("client.turn") as span:
                span.set_attribute("turn", turn)
                # Empty for turn 1 (not yet assigned by the supplier); set
                # for real below once known, so this span carries the same
                # task_id/context_id as the supplier's `supplier.execute`
                # span for the same negotiation, for cross-service tracing.
                span.set_attribute("task_id", task_id or "")
                span.set_attribute("context_id", context_id or "")

                adk_events = [e async for e in agent.run_async(invocation_ctx)]
                if not adk_events:
                    return False, turn - 1, last_response
                client_event = adk_events[-1]
                session.events.append(client_event)

                try:
                    request_payload = json.loads(extract_event_text(client_event))
                    chat_msg = request_payload.get("chat_message", "")
                    current_data = request_payload.get("data", [])
                except Exception:
                    # The agent produced its plain-text closing acknowledgment
                    # (e.g. after a prior success) rather than a submission.
                    return False, turn - 1, last_response

                # --- Communication processing: ADK Event -> A2A Message -> send ---
                request_message = Message(
                    role=Role.user,
                    parts=[Part(root=TextPart(text=chat_msg)), Part(root=DataPart(data={"data": current_data}))],
                    message_id=str(uuid.uuid4()),
                    task_id=task_id,
                    context_id=context_id,
                )
                self._log(message_log_dir, f"request_{turn}.json", request_message)

                result_task = None
                async for event in client.send_message(
                    request_message, configuration=MessageSendConfiguration(blocking=True)
                ):
                    if isinstance(event, tuple):
                        task, _update = event
                        result_task = task
                if result_task is None:
                    raise RuntimeError("Supplier agent did not return a task.")

                self._log(message_log_dir, f"response_{turn}.json", result_task)

                task_id = result_task.id
                context_id = result_task.context_id
                span.set_attribute("task_id", task_id or "")
                span.set_attribute("context_id", context_id or "")

                status_text = ""
                if result_task.status.message:
                    status_text = get_message_text(result_task.status.message)

                artifact_payload = {}
                if result_task.artifacts:
                    data_parts = get_data_parts(result_task.artifacts[-1].parts)
                    if data_parts:
                        artifact_payload = data_parts[0]

                resp_data = artifact_payload.get("data", []) if isinstance(artifact_payload, dict) else []
                global_errors = artifact_payload.get("global_errors", []) if isinstance(artifact_payload, dict) else []
                state = result_task.status.state
                success = state == TaskState.completed

                if success:
                    resp_status = "success"
                elif "max_turns_exceeded" in global_errors:
                    resp_status = "max_turns_exceeded"
                else:
                    resp_status = "invalid"

                span.set_attribute("status", resp_status)

                last_response = {
                    "status": resp_status,
                    "data": resp_data,
                    "global_errors": global_errors,
                    "chat_message": status_text,
                }

                # --- Communication processing: A2A Task -> ADK Event, feed back to agent ---
                session.events.append(AdkEvent(author="supplier_agent", message=json.dumps(last_response)))

                if success:
                    return True, turn, last_response

                # Any terminal (non-input-required) state ends the negotiation.
                if state != TaskState.input_required:
                    return False, turn, last_response

            turn += 1
