"""In-process A2A simulation harness (no real socket) backing the unit
tests. See simulation_a2a.py for the real over-the-wire version.
"""

import os
import asyncio
import json
import httpx
from dotenv import load_dotenv

os.environ.setdefault("OTEL_SERVICE_NAME", "agent-contracts-simulation")  # before importing agents
load_dotenv()  # must run before importing agents (see agents/__init__.py)

from config import STRATEGY_MAP
from agents import A2AClientRunner, build_supplier_app
from agents.logic import DEFAULT_SCHEMA_PATH
from agents.telemetry import setup_tracing


class SimulationCoordinator:

    def __init__(self, num_calls: int = 5, delay_factor: float = 0.0, stream: bool = False,
                 schema_path: str = DEFAULT_SCHEMA_PATH):
        self.num_calls = num_calls
        self.delay_factor = delay_factor
        self.stream = stream
        self.schema_path = schema_path

    async def run_scenario(self, name: str, data: list, repair_strategy: str = "standard",
                            message_log_dir: str | None = None):
        """Runs a single scenario against an in-process A2A supplier agent."""
        base_url = "http://supplier.local"
        app = build_supplier_app(schema_path=self.schema_path, base_url=base_url + "/", max_turns=self.num_calls)
        transport = httpx.ASGITransport(app=app)

        async with httpx.AsyncClient(transport=transport, base_url=base_url) as httpx_client:
            runner = A2AClientRunner(
                initial_data=data,
                repair_strategy=repair_strategy,
                schema_path=self.schema_path,
            )
            success, calls, response = await runner.run(httpx_client, base_url, message_log_dir=message_log_dir)

        if self.stream:
            outcome = "PASSED" if success else "FAILED"
            print(f"----- Scenario: {name} -> {outcome} (calls={calls}) -----")
            if self.delay_factor:
                await asyncio.sleep(self.delay_factor)

        return success, calls, response


async def main():
    setup_tracing("agent-contracts-simulation")
    coordinator = SimulationCoordinator(num_calls=5, delay_factor=0.0, stream=True)
    data_dir = "data"
    scenario_files = sorted(f for f in os.listdir(data_dir) if f.endswith(".json"))

    results = []
    for scenario_file in scenario_files:
        scenario_name = os.path.splitext(scenario_file)[0]
        with open(os.path.join(data_dir, scenario_file)) as f:
            data = json.load(f)
        strategy = STRATEGY_MAP.get(scenario_file, "standard")
        success, calls, response = await coordinator.run_scenario(scenario_name, data, strategy)
        results.append((scenario_name, success, calls))

    print("\n===== Simulation Summary =====")
    for scenario_name, success, calls in results:
        print(f"{scenario_name:25s} {'PASS' if success else 'FAIL':4s} (calls={calls})")


if __name__ == "__main__":
    asyncio.run(main())
