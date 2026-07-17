"""Exposes the client agent's negotiation as a REST service, so an external
orchestrator (see simulation_a2a_services.py) can trigger scenario runs
over HTTP against an already-running Docker Compose stack, instead of
spawning a fresh client process per scenario.
"""

import os
import argparse
import httpx
import uvicorn
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route
from dotenv import load_dotenv

os.environ.setdefault("OTEL_SERVICE_NAME", "forecasting-client")  # before importing agents
load_dotenv()  # must run before importing agents (see agents/__init__.py)

from agents import A2AClientRunner
from agents.logic import DEFAULT_SCHEMA_PATH
from agents.telemetry import setup_tracing

DEFAULT_SUPPLIER_URL = os.environ.get("SUPPLIER_URL", "http://supplier:8000/")


async def health(request: Request) -> JSONResponse:
    return JSONResponse({"status": "ok"})


async def run_scenario(request: Request) -> JSONResponse:
    """Body: {"data": [...], "strategy": "standard", "supplier_url": "...",
    "schema_path": "...", "message_dir": "..."}. Only "data" is required.
    Returns {"success": bool, "calls": int, "response": {...}}.
    """
    payload = await request.json()
    data = payload.get("data")
    if not isinstance(data, list):
        return JSONResponse({"error": "'data' must be a list of records."}, status_code=400)

    strategy = payload.get("strategy", "standard")
    schema_path = payload.get("schema_path", DEFAULT_SCHEMA_PATH)
    supplier_url = payload.get("supplier_url", DEFAULT_SUPPLIER_URL)
    message_dir = payload.get("message_dir")

    print(payload)

    runner = A2AClientRunner(initial_data=data, repair_strategy=strategy, schema_path=schema_path)
    async with httpx.AsyncClient(timeout=30.0) as httpx_client:
        success, calls, response = await runner.run(httpx_client, supplier_url, message_log_dir=message_dir)

    return JSONResponse({"success": success, "calls": calls, "response": response})


def build_app() -> Starlette:
    return Starlette(routes=[
        Route("/health", health, methods=["GET"]),
        Route("/run-scenario", run_scenario, methods=["POST"]),
    ])


def main():
    setup_tracing("forecasting-client")

    parser = argparse.ArgumentParser(description="Run the Client Agent as a REST service.")
    parser.add_argument("--host", type=str, default="127.0.0.1", help="Host to bind the REST service to.")
    parser.add_argument("--port", type=int, default=8127, help="Port to bind the REST service to.")
    args = parser.parse_args()

    uvicorn.run(build_app(), host=args.host, port=args.port, log_level="warning")


if __name__ == "__main__":
    main()
