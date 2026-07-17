"""End-to-end A2A simulation: real supplier_service.py server process, real
client_app.py process, real A2A JSON-RPC wire protocol, every scenario in data/.
"""

import os
import subprocess
import sys
import time
from dotenv import load_dotenv

load_dotenv()  # must run before importing simulation (see agents/__init__.py)

import httpx

from simulation import EXPECTED_SUCCESS, STRATEGY_MAP

HOST = "127.0.0.1"
PORT = 8123
BASE_URL = f"http://{HOST}:{PORT}/"
MESSAGES_BASE_DIR = "messages"
TURNS = 5


def wait_for_server(timeout: float = 15.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            resp = httpx.get(BASE_URL + ".well-known/agent-card.json", timeout=1.0)
            if resp.status_code == 200:
                return True
        except httpx.HTTPError:
            pass
        time.sleep(0.3)
    return False


def run_simulation() -> int:
    data_dir = "data"
    scenario_files = sorted(f for f in os.listdir(data_dir) if f.endswith(".json"))

    # Messages are kept for later review; never deleted, only added to.
    os.makedirs(MESSAGES_BASE_DIR, exist_ok=True)

    print(f"Starting supplier A2A server on {BASE_URL} ...")
    # Explicit env: importing `simulation` above already set OTEL_SERVICE_NAME
    # (for this process) via setdefault, which subprocesses would otherwise
    # inherit -- overriding it here keeps each subprocess's own name distinct.
    server_process = subprocess.Popen([
        sys.executable, "supplier_service.py",
        "--host", HOST,
        "--port", str(PORT),
        "--max-turns", str(TURNS),
    ], env={**os.environ, "OTEL_SERVICE_NAME": "forecasting-supplier"})

    results = []
    try:
        if not wait_for_server():
            print("ERROR: Supplier A2A server did not become ready in time.")
            return 1
        print("Supplier A2A server is up. Fetching agent card...")
        card = httpx.get(BASE_URL + ".well-known/agent-card.json", timeout=5.0).json()
        print(f"  name: {card.get('name')!r}  skills: {[s.get('name') for s in card.get('skills', [])]}")

        for scenario_file in scenario_files:
            scenario_name = os.path.splitext(scenario_file)[0]
            strategy = STRATEGY_MAP.get(scenario_file, "standard")
            message_dir = os.path.join(MESSAGES_BASE_DIR, scenario_name)
            os.makedirs(message_dir, exist_ok=True)
            print(f"\n----- Running Scenario: {scenario_name} (strategy={strategy}) -----")

            client_process = subprocess.run([
                sys.executable, "client_app.py",
                os.path.join(data_dir, scenario_file),
                "--url", BASE_URL,
                "--strategy", strategy,
                "--message-dir", message_dir,
            ], capture_output=True, text=True, env={**os.environ, "OTEL_SERVICE_NAME": "forecasting-client"})

            print(client_process.stdout.strip())
            if client_process.returncode not in (0, 2):
                print(client_process.stderr)

            success = client_process.returncode == 0
            expected = EXPECTED_SUCCESS[scenario_file]
            results.append((scenario_name, success, expected))
    finally:
        server_process.terminate()
        try:
            server_process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            server_process.kill()
        print("\nSupplier A2A server terminated.")

    print("\n===== A2A Simulation Summary =====")
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
    sys.exit(run_simulation())
