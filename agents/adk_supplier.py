"""The supplier's decision logic, as a genuine ADK agent. Knows nothing
about A2A/EventQueues/Tasks -- see agents/supplier.py for that layer.
"""

import json

from typing import AsyncGenerator

from google.adk.agents import BaseAgent
from google.adk.agents.invocation_context import InvocationContext
from google.adk.events import Event

from agents.adk_utils import extract_event_text
from agents.logic import DEFAULT_SCHEMA_PATH, generate_supplier_chat_message, validate_data


class SupplierADKAgent(BaseAgent):
    """Validates submitted records against the data contract and yields
    {"status", "chat_message", "data", "global_errors"}."""

    name: str = "supplier_agent"
    description: str = "A forecasting service supplier agent that validates data against the schema."
    schema_path: str = DEFAULT_SCHEMA_PATH

    async def _run_async_impl(self, ctx: InvocationContext) -> AsyncGenerator[Event, None]:
        latest_event = ctx.session.events[-1] if ctx.session.events else None
        input_text = extract_event_text(latest_event)

        if not input_text:
            yield Event(
                author=self.name,
                message=json.dumps({
                    "status": "invalid",
                    "chat_message": "Hello. The input was empty. Please provide the JSON dataset.",
                    "data": [],
                    "global_errors": ["Empty input."],
                }),
            )
            return

        try:
            payload = json.loads(input_text)
            data = payload["data"] if isinstance(payload, dict) and "data" in payload else payload
        except Exception as e:
            yield Event(
                author=self.name,
                message=json.dumps({
                    "status": "invalid",
                    "chat_message": f"Hello. I failed to parse the input as JSON. Details: {e}",
                    "data": [],
                    "global_errors": ["JSON parse failure."],
                }),
            )
            return

        is_valid, annotated_data, global_errors = validate_data(data, self.schema_path)
        chat_msg = generate_supplier_chat_message(is_valid, annotated_data, global_errors)

        if is_valid:
            forecasted_data = []
            for record in data:
                clean_rec = {k: v for k, v in record.items() if k != "_validation_meta"}
                clean_rec["forecast"] = 1.0
                forecasted_data.append(clean_rec)

            response = {
                "status": "success",
                "chat_message": chat_msg,
                "data": forecasted_data,
                "global_errors": [],
            }
        else:
            response = {
                "status": "invalid",
                "chat_message": chat_msg,
                "data": annotated_data,
                "global_errors": global_errors,
            }

        yield Event(author=self.name, message=json.dumps(response))
