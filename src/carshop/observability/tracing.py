"""One trace per run, one span per dealer and node. Attributes carry hashes, counts,
latency and cost, never cookies or bodies (requirements 7.5, 8)."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

from opentelemetry import trace
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor

_configured = False


def configure(endpoint: str | None, service: str = "carshop") -> None:
    global _configured  # noqa: PLW0603
    if _configured:
        return
    provider = TracerProvider(resource=Resource.create({"service.name": service}))
    if endpoint:
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter

        provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter(endpoint=endpoint)))
    trace.set_tracer_provider(provider)
    _configured = True


@contextmanager
def span(name: str, **attrs: Any) -> Iterator[trace.Span]:
    tracer = trace.get_tracer("carshop")
    with tracer.start_as_current_span(name) as sp:
        for k, v in attrs.items():
            if v is not None:
                sp.set_attribute(k, v)
        yield sp
