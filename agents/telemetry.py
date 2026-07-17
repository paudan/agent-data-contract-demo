"""OpenTelemetry tracing setup (console and/or Jaeger). google-adk, a2a-sdk,
and Langfuse's own client all touch the global TracerProvider themselves;
whichever runs first "owns" it, so this adds span processors onto whatever
provider already exists (creating one only if none does yet) instead of
fighting over `set_tracer_provider`.
"""

import os
from opentelemetry import trace
from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk.resources import SERVICE_NAME, Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor, ConsoleSpanExporter, SimpleSpanProcessor

TRACER_NAME = "agent-contracts"

# Jaeger's default OTLP/gRPC receiver. OTLPSpanExporter() with no args
# already falls back to this (or $OTEL_EXPORTER_OTLP_ENDPOINT) on its own.
DEFAULT_JAEGER_OTLP_ENDPOINT = "http://localhost:4317"

# Mutually exclusive backend switch (they'd otherwise both try to own the
# global TracerProvider's spans) -- "langfuse" or "jaeger", set in .env.
TRACING_BACKEND = os.environ.get("TRACING_BACKEND", "langfuse").strip().lower()

_console_added = False
_jaeger_added = False


def _get_or_create_provider(service_name: str) -> TracerProvider:
    provider = trace.get_tracer_provider()
    if isinstance(provider, trace.ProxyTracerProvider):
        provider = TracerProvider(resource=Resource.create({SERVICE_NAME: service_name}))
        trace.set_tracer_provider(provider)
    return provider


def bootstrap(default_service_name: str = "agent-contracts") -> None:
    """Creates the global TracerProvider as early as possible -- call this
    before anything that might grab the default provider itself (Langfuse's
    `get_client()`, ADK/A2A auto-instrumentation), so *our* resource
    (service.name) wins instead of theirs. Reads $OTEL_SERVICE_NAME if an
    entry-point script set it before importing `agents`; otherwise falls
    back to `default_service_name`. Idempotent.
    """
    _get_or_create_provider(os.environ.get("OTEL_SERVICE_NAME", default_service_name))


def setup_console_tracing(service_name: str) -> None:
    """Adds a span processor that prints spans to stdout. Idempotent."""
    global _console_added
    if _console_added:
        return
    _get_or_create_provider(service_name).add_span_processor(SimpleSpanProcessor(ConsoleSpanExporter()))
    _console_added = True


def setup_jaeger_tracing(service_name: str, endpoint: str | None = None) -> None:
    """Adds a span processor exporting to a local Jaeger instance over
    OTLP/gRPC (default localhost:4317, overridable via `endpoint` or
    $OTEL_EXPORTER_OTLP_ENDPOINT). Idempotent. Export runs on a background
    thread; if Jaeger isn't reachable, export attempts just log warnings.
    """
    global _jaeger_added
    if _jaeger_added:
        return
    exporter = OTLPSpanExporter(endpoint=endpoint, insecure=True)
    _get_or_create_provider(service_name).add_span_processor(BatchSpanProcessor(exporter))
    _jaeger_added = True


def setup_tracing(service_name: str, endpoint: str | None = None) -> None:
    """Sets up whichever backend $TRACING_BACKEND selects. For "langfuse"
    (the default), this is a no-op -- Langfuse's own client (see
    agents/__init__.py) already attaches itself; for "jaeger", wires up
    `setup_jaeger_tracing`."""
    if TRACING_BACKEND == "jaeger":
        setup_jaeger_tracing(service_name, endpoint=endpoint)


def get_tracer():
    return trace.get_tracer(TRACER_NAME)
