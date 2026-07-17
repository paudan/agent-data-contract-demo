"""Runs the client-side A2A negotiation against a real, running supplier
A2A server (see supplier_app.py) over HTTP, using the a2a-sdk JSON-RPC
client transport.
"""

import os
import argparse
import asyncio
import json
import sys
import httpx
from dotenv import load_dotenv

os.environ.setdefault("OTEL_SERVICE_NAME", "forecasting-client")  # before importing agents
load_dotenv()  # must run before importing agents (see agents/__init__.py)

from agents import A2AClientRunner
from agents.logic import DEFAULT_SCHEMA_PATH
from agents.telemetry import setup_tracing


async def run(data_file: str, base_url: str, strategy: str, schema_path: str, message_dir: str | None) -> int:
    try:
        with open(data_file, 'r') as f:
            initial_data = json.load(f)
    except FileNotFoundError:
        print(f"Error: The file '{data_file}' was not found.")
        return 1
    except json.JSONDecodeError:
        print(f"Error: Could not decode JSON from the file '{data_file}'.")
        return 1

    runner = A2AClientRunner(initial_data=initial_data, repair_strategy=strategy, schema_path=schema_path)

    async with httpx.AsyncClient(timeout=30.0) as httpx_client:
        success, calls, response = await runner.run(httpx_client, base_url, message_log_dir=message_dir)

    print(f"chat_message: {response.get('chat_message')}")
    print(f"status: {response.get('status')} | calls used: {calls}")
    print(json.dumps(response, indent=2))

    return 0 if success else 2


def main():
    setup_tracing("forecasting-client")

    parser = argparse.ArgumentParser(description="Run the Client Agent against a live A2A supplier server.")
    parser.add_argument("data_file", type=str, help="Path to the initial JSON data file.")
    parser.add_argument("--url", type=str, default="http://127.0.0.1:8000/", help="Base URL of the supplier A2A server.")
    parser.add_argument("--strategy", type=str, default="standard",
                        choices=["standard", "gradual", "partial", "none"],
                        help="The client's data repair strategy.")
    parser.add_argument("--schema-path", type=str, default=DEFAULT_SCHEMA_PATH,
                         help="Path to the data contract JSON schema (the client repairs against its own "
                              "declared types/constraints, so this must match the supplier's contract).")
    parser.add_argument("--message-dir", type=str, default=None,
                         help="If set, writes each turn's request/response A2A messages here for later review.")

    args = parser.parse_args()
    exit_code = asyncio.run(run(args.data_file, args.url, args.strategy, args.schema_path, args.message_dir))
    sys.exit(exit_code)


if __name__ == "__main__":
    main()
