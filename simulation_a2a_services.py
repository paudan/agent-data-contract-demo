"""Same as simulation.py, but drives the negotiation over REST against
an already-running, Docker Compose-deployed client service (client_service.py)
and supplier service (supplier_service.py) -- see docker-compose.yml.

Bring the stack up yourself first:  docker compose up -d --build
This script only makes HTTP calls; it never manages the Compose lifecycle.
"""

import argparse
import json
import os
import sys
import time
import httpx

from config import EXPECTED_SUCCESS, STRATEGY_MAP

SUPPLIER_URL = os.environ.get("SUPPLIER_URL", "http://localhost:8128/")
CLIENT_SERVICE_URL = os.environ.get("CLIENT_SERVICE_URL", "http://localhost:8127/")
SUPPLIER_INTERNAL_URL = "http://supplier:8000/"
MESSAGES_BASE_DIR = "messages"


def wait_for(url: str, timeout: float = 60.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            resp = httpx.get(url, timeout=1.0)
            if resp.status_code == 200:
                return True
        except httpx.HTTPError:
            pass
        time.sleep(0.5)
    return False


def run_simulation(data_dir: str = 'data') -> int:
    data_dir = "data"
    scenario_files = sorted(f for f in os.listdir(data_dir) if f.endswith(".json"))

    # Messages are kept for later review; never deleted, only added to.
    os.makedirs(MESSAGES_BASE_DIR, exist_ok=True)

    print("Checking that agents are already up...")
    if not wait_for(SUPPLIER_URL + ".well-known/agent-card.json", timeout=15.0):
        print(f"ERROR: Supplier not reachable at {SUPPLIER_URL}")
        return 1
    if not wait_for(CLIENT_SERVICE_URL + "health", timeout=15.0):
        print(f"ERROR: Client REST service not reachable at {CLIENT_SERVICE_URL}")
        return 1

    card = httpx.get(SUPPLIER_URL + ".well-known/agent-card.json", timeout=5.0).json()
    print(f"Supplier is up. name: {card.get('name')!r}  skills: {[s.get('name') for s in card.get('skills', [])]}")
    print("Client REST service is up.")

    results = []
    with httpx.Client(timeout=60.0) as client:
        for scenario_file in scenario_files:
            scenario_name = os.path.splitext(scenario_file)[0]
            strategy = STRATEGY_MAP.get(scenario_file, "standard")
            message_dir = os.path.join(MESSAGES_BASE_DIR, scenario_name)
            os.makedirs(message_dir, exist_ok=True)
            print(f"\n----- Running Scenario: {scenario_name} (strategy={strategy}) -----")

            with open(os.path.join(data_dir, scenario_file)) as f:
                data = json.load(f)

            resp = client.post(CLIENT_SERVICE_URL + "run-scenario", json={
                "data": data,
                "strategy": strategy,
                "supplier_url": SUPPLIER_INTERNAL_URL,
                "message_dir": message_dir,
            })
            resp.raise_for_status()
            result = resp.json()

            print(f"status: {result['response'].get('status')} | calls used: {result['calls']}")

            success = result["success"]
            expected = EXPECTED_SUCCESS[scenario_file]
            results.append((scenario_name, success, expected))

    print("\n===== A2A Simulation Summary (Docker, REST) =====")
    all_as_expected = True
    for scenario_name, success, expected in results:
        outcome = "SUCCESS" if success else "FAILED (contract unmet)"
        as_expected = success == expected
        all_as_expected &= as_expected
        flag = "OK" if as_expected else "UNEXPECTED"
        print(f"{scenario_name:25s} {outcome:24s} [{flag}]")

    print(f"\nPer-turn A2A request/response messages were saved under '{MESSAGES_BASE_DIR}/<scenario>/' for review.")
    if all_as_expected:
        print("\nAll scenarios behaved as expected over the A2A protocol.")
        return 0

    print("\nSome scenarios did not behave as expected.")
    return 1


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Run the Forecasting Supplier as an A2A server.")
    parser.add_argument("--data-path", type=str, default='data', help="Path to the senario data")
    args = parser.parse_args()
    sys.exit(run_simulation(args.data_path))
