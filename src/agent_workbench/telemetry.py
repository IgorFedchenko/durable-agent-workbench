"""Local real OpenTelemetry spans; OTLP requires explicit loopback opt-in."""

from __future__ import annotations

import json
import logging
import threading
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor, SpanExporter, SpanExportResult


class LocalExporter(SpanExporter):
    def __init__(self, path: Path):
        self.path = path
        self.lock = threading.Lock()

    def export(self, spans: Any) -> SpanExportResult:
        with self.lock, self.path.open("a") as file:
            for span in spans:
                file.write(span.to_json(indent=None) + "\n")
        return SpanExportResult.SUCCESS

    def shutdown(self) -> None:
        pass


def setup(path: Path, otlp_endpoint: str | None = None) -> TracerProvider:
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(LocalExporter(path)))
    if otlp_endpoint:
        parsed = urlparse(otlp_endpoint)
        if parsed.hostname not in ("127.0.0.1", "localhost", "::1") or parsed.scheme != "http":
            raise ValueError("optional telemetry endpoint must be explicit local HTTP")
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter

        provider.add_span_processor(
            SimpleSpanProcessor(OTLPSpanExporter(endpoint=otlp_endpoint, timeout=1))
        )
    return provider


def trace_ids(span: trace.Span) -> dict[str, str]:
    context = span.get_span_context()
    return {"trace_id": f"{context.trace_id:032x}", "span_id": f"{context.span_id:016x}"}


def log_event(kind: str, *, run_id: str, category: str = "info", **fields: Any) -> None:
    # Allowlisted metadata only; no raw prompts, dataset strings, arguments or exception text.
    safe = {
        k: v
        for k, v in fields.items()
        if k
        in {
            "step",
            "attempt",
            "trace_id",
            "span_id",
            "duration_seconds",
            "tool",
            "synthetic_tokens",
            "reservation",
        }
    }
    logging.getLogger("workbench").info(
        json.dumps({"kind": kind, "run_id": run_id, "category": category, **safe})
    )
