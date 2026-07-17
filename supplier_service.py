"""Launches the forecasting supplier as a real A2A server over HTTP.

Serves the AgentCard at /.well-known/agent-card.json and the A2A JSON-RPC
endpoint at / via uvicorn.
"""

import os
import argparse
import uvicorn
from dotenv import load_dotenv

os.environ.setdefault("OTEL_SERVICE_NAME", "forecasting-supplier")  # before importing agents
load_dotenv()  # must run before importing agents (see agents/__init__.py)

from agents import build_supplier_app
from agents.logic import DEFAULT_SCHEMA_PATH
from agents.supplier import DEFAULT_MAX_TURNS
from agents.telemetry import setup_tracing


def main():
    setup_tracing("forecasting-supplier")

    parser = argparse.ArgumentParser(description="Run the Forecasting Supplier as an A2A server.")
    parser.add_argument("--host", type=str, default="127.0.0.1", help="Host to bind the A2A server to.")
    parser.add_argument("--port", type=int, default=8128, help="Port to bind the A2A server to.")
    parser.add_argument("--public-url", type=str, default=None,
                        help="Base URL advertised in the AgentCard for clients to connect back to -- "
                             "distinct from --host/--port (the bind address), since e.g. binding to "
                             "0.0.0.0 to accept container/network traffic is not itself a reachable "
                             "address. Defaults to http://<host>:<port>/ (fine for local use).")
    parser.add_argument("--schema-path", type=str, default=DEFAULT_SCHEMA_PATH, help="Path to the data contract JSON schema.")
    parser.add_argument("--max-turns", type=int, default=DEFAULT_MAX_TURNS,
                        help="Maximum negotiation turns allowed per session before the supplier ends it. "
                             "Tracked in Redis, keyed by A2A context_id.")
    parser.add_argument("--redis-url", type=str, default=None,
                        help="Redis URL used to track per-session turn counts "
                             "(defaults to redis://localhost:6379/0 or $REDIS_URL).")
    args = parser.parse_args()

    base_url = args.public_url or f"http://{args.host}:{args.port}/"
    app = build_supplier_app(schema_path=args.schema_path, base_url=base_url,
                              max_turns=args.max_turns, redis_url=args.redis_url)

    uvicorn.run(app, host=args.host, port=args.port, log_level="warning")


if __name__ == "__main__":
    main()
