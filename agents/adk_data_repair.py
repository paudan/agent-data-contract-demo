"""ClientADKAgent's data-repair sub-agent (see README.md). Reads the
supplier's last response from the shared session and yields repaired
records via the schema-driven agents.logic.fix_data.
"""

import json
from typing import AsyncGenerator
from pydantic import Field
from google.adk.agents import BaseAgent
from google.adk.agents.invocation_context import InvocationContext
from google.adk.events import Event

from agents.adk_utils import extract_event_text
from agents.logic import DEFAULT_SCHEMA_PATH, fix_data, load_schema


class DataRepairADKAgent(BaseAgent):
    """Repairs the supplier's latest validation response, yielding
    {"data": [...]}."""

    name: str = "data_repair_agent"
    description: str = "Repairs data-contract violations found in a supplier's validation response."

    repair_strategy: str = Field(default="standard")
    schema_path: str = Field(default=DEFAULT_SCHEMA_PATH)

    async def _run_async_impl(self, ctx: InvocationContext) -> AsyncGenerator[Event, None]:
        latest_event = ctx.session.events[-1] if ctx.session.events else None
        input_text = extract_event_text(latest_event)

        try:
            supplier_response = json.loads(input_text) if input_text else None
        except Exception:
            supplier_response = None

        annotated_data = supplier_response.get("data", []) if isinstance(supplier_response, dict) else []
        supplier_calls = sum(1 for e in ctx.session.events if e.author == "supplier_agent")

        schema = load_schema(self.schema_path)
        fixed_data = fix_data(annotated_data, supplier_calls, self.repair_strategy, schema)

        yield Event(author=self.name, message=json.dumps({"data": fixed_data}))
