from dotenv import load_dotenv

# Must run before anything below that reads LANGFUSE_*/TRACING_BACKEND env
# vars at import time -- if .env hasn't been loaded yet, those read as unset
# regardless of what callers do afterwards.
load_dotenv()

from .telemetry import bootstrap, TRACING_BACKEND
bootstrap()

# TRACING_BACKEND ("langfuse", the default, or "jaeger" -- see .env) picks
# one exporter, since both trying to own the TracerProvider's spans at once
# is more confusing than useful. Jaeger is wired up per-entry-point instead,
# via agents.telemetry.setup_tracing(service_name).
if TRACING_BACKEND == "langfuse":
    from langfuse import get_client
    langfuse = get_client()

    if langfuse.auth_check():
        print("Langfuse client is authenticated and ready!")
    else:
        print("Authentication failed. Please check your credentials and host.")

from openinference.instrumentation.google_adk import GoogleADKInstrumentor
GoogleADKInstrumentor().instrument()

from .adk_supplier import SupplierADKAgent
from .adk_client import ClientADKAgent
from .adk_data_repair import DataRepairADKAgent
from .supplier import SupplierAgentExecutor, build_supplier_agent_card, build_supplier_app
from .client import A2AClientRunner
from .logic import validate_data, fix_data, generate_client_chat_message, generate_supplier_chat_message

__all__ = [
    "SupplierADKAgent",
    "ClientADKAgent",
    "DataRepairADKAgent",
    "SupplierAgentExecutor",
    "build_supplier_agent_card",
    "build_supplier_app",
    "A2AClientRunner",
    "validate_data",
    "fix_data",
    "generate_client_chat_message",
    "generate_supplier_chat_message",
]
