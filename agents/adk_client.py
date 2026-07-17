"""The client's negotiation logic (when to submit/stop), as a genuine ADK
agent. Delegates the actual repair decision to its sub-agent,
DataRepairADKAgent (see agents/adk_data_repair.py and README.md).
"""

import json
from typing import AsyncGenerator
from pydantic import Field
from google.adk.agents import BaseAgent
from google.adk.agents.invocation_context import InvocationContext
from google.adk.events import Event

from agents.adk_data_repair import DataRepairADKAgent
from agents.adk_utils import extract_event_text
from agents.logic import DEFAULT_SCHEMA_PATH, generate_client_chat_message


class ClientADKAgent(BaseAgent):
    """A client agent that uses forecasting services and corrects invalid data."""

    name: str = "client_agent"
    description: str = "A client agent that uses forecasting services and corrects invalid data."

    repair_strategy: str = Field(default="standard")
    initial_data: list = Field(default_factory=list)
    new_invocation: bool = Field(default=True)
    schema_path: str = Field(default=DEFAULT_SCHEMA_PATH)

    def model_post_init(self, __context) -> None:
        super().model_post_init(__context)
        if not self.sub_agents:
            repair_agent = DataRepairADKAgent(
                name=f"{self.name}_data_repair",
                repair_strategy=self.repair_strategy,
                schema_path=self.schema_path,
            )
            repair_agent.parent_agent = self
            self.sub_agents = [repair_agent]

    async def _run_async_impl(self, ctx: InvocationContext) -> AsyncGenerator[Event, None]:
        latest_event = ctx.session.events[-1] if ctx.session.events else None

        if self.new_invocation or not latest_event or latest_event.author == self.name:
            self.new_invocation = False
            chat_msg = generate_client_chat_message(None, 1)
            yield Event(
                author=self.name,
                message=json.dumps({"chat_message": chat_msg, "data": self.initial_data}),
            )
            return

        input_text = extract_event_text(latest_event)
        try:
            supplier_response = json.loads(input_text) if input_text else None
        except Exception:
            supplier_response = None

        if not isinstance(supplier_response, dict):
            chat_msg = generate_client_chat_message(None, 1)
            yield Event(
                author=self.name,
                message=json.dumps({"chat_message": chat_msg, "data": self.initial_data}),
            )
            return

        status = supplier_response.get("status")
        if status == "success":
            chat_msg = generate_client_chat_message(supplier_response, 1)
            yield Event(author=self.name, message=chat_msg)
            return

        if status == "max_turns_exceeded":
            # Supplier owns the turn budget; just stop.
            chat_msg = generate_client_chat_message(supplier_response, 1)
            yield Event(author=self.name, message=chat_msg)
            return

        supplier_calls = sum(1 for e in ctx.session.events if e.author == "supplier_agent")

        # Delegate the repair decision to the sub-agent (reads ctx itself).
        repair_agent = self.sub_agents[0]
        repair_events = [e async for e in repair_agent.run_async(ctx)]
        repair_output = json.loads(extract_event_text(repair_events[-1])) if repair_events else {}
        fixed_data = repair_output.get("data", supplier_response.get("data", []))

        chat_msg = generate_client_chat_message(supplier_response, supplier_calls + 1)

        yield Event(
            author=self.name,
            message=json.dumps({"chat_message": chat_msg, "data": fixed_data}),
        )
