"""Request spans from HTTP through the agent. One request_id on every span.

OTLP export turns on when OTEL_EXPORTER_OTLP_ENDPOINT is set and the exporter
package is installed. Without that, spans stay in-process and in the log.
"""

import json
import logging
import os
import time
from contextlib import contextmanager
from contextvars import ContextVar

logger = logging.getLogger("querymesh.trace")

_request_id: ContextVar[str] = ContextVar("querymesh_request_id", default="")
_stack: ContextVar[tuple] = ContextVar("querymesh_span_stack", default=())

recent: list[dict] = []
_tracer = None
_ready = False


def set_request_id(value: str) -> None:
    _request_id.set(value or "")


def current_request_id() -> str:
    return _request_id.get()


def setup_tracing() -> None:
    """Install a tracer once. Safe to call from import and from the first span."""
    global _tracer, _ready
    if _ready:
        return
    _ready = True
    try:
        from opentelemetry import trace
        from opentelemetry.sdk.resources import Resource
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import SimpleSpanProcessor, SpanExporter, SpanExportResult
    except ImportError:
        return

    class _Memory(SpanExporter):
        def export(self, spans):
            for item in spans:
                parent = item.parent
                recent.append(
                    {
                        "name": item.name,
                        "request_id": str((item.attributes or {}).get("request_id", "")),
                        "trace_id": format(item.context.trace_id, "032x"),
                        "span_id": format(item.context.span_id, "016x"),
                        "parent_id": format(parent.span_id, "016x") if parent else "",
                        "duration_ms": round((item.end_time - item.start_time) / 1e6, 2),
                        "attrs": dict(item.attributes or {}),
                    }
                )
            if len(recent) > 500:
                del recent[:-500]
            return SpanExportResult.SUCCESS

        def shutdown(self):
            return None

    provider = TracerProvider(resource=Resource.create({"service.name": "querymesh"}))
    provider.add_span_processor(SimpleSpanProcessor(_Memory()))
    endpoint = os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT", "").strip()
    if endpoint:
        try:
            from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
            from opentelemetry.sdk.trace.export import BatchSpanProcessor

            provider.add_span_processor(
                BatchSpanProcessor(OTLPSpanExporter(endpoint=endpoint.rstrip("/") + "/v1/traces"))
            )
        except ImportError:
            logger.warning("OTEL_EXPORTER_OTLP_ENDPOINT is set but the OTLP exporter is not installed")
    trace.set_tracer_provider(provider)
    _tracer = trace.get_tracer("querymesh")


def configure_llm_export() -> None:
    """LangSmith stores prompts, tokens, and tool calls when a key is in the environment."""
    key = os.environ.get("LANGSMITH_API_KEY", "").strip() or os.environ.get("LANGCHAIN_API_KEY", "").strip()
    if not key:
        return
    os.environ.setdefault("LANGCHAIN_TRACING_V2", "true")
    os.environ.setdefault("LANGCHAIN_PROJECT", "querymesh")
    os.environ.setdefault("LANGCHAIN_API_KEY", key)


def annotate(current, key: str, value) -> None:
    if current is None:
        return
    rendered = value if isinstance(value, (bool, int, float)) else str(value)
    if hasattr(current, "set_attribute"):
        current.set_attribute(key, rendered)
        return
    current.setdefault("attrs", {})[key] = rendered


@contextmanager
def span(name: str, **attrs):
    setup_tracing()
    started = time.perf_counter()
    if _tracer is not None:
        from opentelemetry.trace import Status, StatusCode

        with _tracer.start_as_current_span(name) as current:
            current.set_attribute("request_id", current_request_id())
            for key, value in attrs.items():
                annotate(current, key, value)
            try:
                yield current
            except Exception as exc:
                current.record_exception(exc)
                current.set_status(Status(StatusCode.ERROR))
                raise
        return

    parent = _stack.get()
    record = {
        "name": name,
        "request_id": current_request_id(),
        "parent": parent[-1] if parent else "",
        "attrs": {"request_id": current_request_id()},
    }
    for key, value in attrs.items():
        annotate(record, key, value)
    token = _stack.set(parent + (name,))
    try:
        yield record
    except Exception as exc:
        record["error"] = type(exc).__name__
        raise
    finally:
        record["duration_ms"] = round((time.perf_counter() - started) * 1000, 2)
        recent.append(record)
        if len(recent) > 500:
            del recent[:-500]
        _stack.reset(token)
        logger.info(
            json.dumps(
                {
                    "event": "span",
                    "name": name,
                    "request_id": record["request_id"],
                    "duration_ms": record["duration_ms"],
                }
            )
        )
