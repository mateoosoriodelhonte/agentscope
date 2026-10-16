"""Records agent runs as OpenTelemetry spans and converts them to domain records.

One TracerProvider serves the whole process and is never installed as the OTel global, so
AgentScope cannot interfere with (or be hijacked by) other instrumentation. A collecting
span processor buffers finished spans per trace id; each run drains its own buffer.

Payloads are redacted *when they are written to a span*, so neither persistence nor an
optional exporter ever sees raw secrets.
"""

from __future__ import annotations

import asyncio
import json
import threading
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from datetime import UTC, datetime
from typing import Any

from opentelemetry import trace
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import ReadableSpan, SpanLimits, SpanProcessor, TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor, SpanExporter
from opentelemetry.trace import Status, StatusCode

from agentscope import __version__
from agentscope.domain.traces import SpanEvent, SpanKind, SpanRecord, SpanStatus, StateTransition
from agentscope.tracing import semconv
from agentscope.tracing.redaction import Redactor

MAX_PAYLOAD_CHARS = 64 * 1024
MAX_STRING_LEAF = 8 * 1024

AttributeValue = str | bool | int | float


def _ns_to_datetime(ns: int | None) -> datetime:
    return datetime.fromtimestamp((ns or 0) / 1e9, tz=UTC)


def _shrink(value: Any) -> Any:
    """Truncate long string leaves while keeping the JSON structure intact."""
    if isinstance(value, str):
        if len(value) <= MAX_STRING_LEAF:
            return value
        return value[:MAX_STRING_LEAF] + f"… [truncated {len(value) - MAX_STRING_LEAF} chars]"
    if isinstance(value, dict):
        return {k: _shrink(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_shrink(v) for v in value]
    return value


def encode_payload(value: Any) -> tuple[str, bool]:
    """Serialize a (redacted) payload for a span attribute; returns (text, truncated)."""
    text = json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)
    if len(text) <= MAX_PAYLOAD_CHARS:
        return text, False
    text = json.dumps(_shrink(value), ensure_ascii=False, sort_keys=True, default=str)
    if len(text) <= MAX_PAYLOAD_CHARS:
        return text, True
    summary = {"_truncated": True, "_original_chars": len(text), "preview": text[:4096]}
    return json.dumps(summary, ensure_ascii=False), True


def _decode_payload(raw: object) -> Any:
    if not isinstance(raw, str):
        return None
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return raw


def _plain(value: Any) -> Any:
    return list(value) if isinstance(value, tuple) else value


def to_record(span: ReadableSpan) -> SpanRecord:
    attrs = dict(span.attributes or {})
    payload_in = _decode_payload(attrs.pop(semconv.AS_INPUT, None))
    payload_out = _decode_payload(attrs.pop(semconv.AS_OUTPUT, None))
    kind = SpanKind(str(attrs.pop(semconv.AS_SPAN_KIND, SpanKind.NODE.value)))
    status = {StatusCode.OK: SpanStatus.OK, StatusCode.ERROR: SpanStatus.ERROR}.get(
        span.status.status_code, SpanStatus.UNSET
    )
    ctx = span.get_span_context()
    assert ctx is not None
    start = span.start_time or 0
    end = span.end_time or start
    return SpanRecord(
        trace_id=format(ctx.trace_id, "032x"),
        span_id=format(ctx.span_id, "016x"),
        parent_span_id=format(span.parent.span_id, "016x") if span.parent else None,
        name=span.name,
        kind=kind,
        status=status,
        status_message=span.status.description,
        start_time=_ns_to_datetime(start),
        end_time=_ns_to_datetime(end),
        duration_ms=max(0.0, (end - start) / 1e6),
        attributes={k: _plain(v) for k, v in attrs.items()},
        input=payload_in,
        output=payload_out,
        events=[
            SpanEvent(
                name=event.name,
                timestamp=_ns_to_datetime(event.timestamp),
                attributes={k: _plain(v) for k, v in (event.attributes or {}).items()},
            )
            for event in span.events
        ],
    )


class RunCollector(SpanProcessor):
    """Buffers finished spans per registered trace id. Unregistered spans are dropped."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._buffers: dict[int, list[ReadableSpan]] = {}
        self.dropped = 0

    def register(self, trace_id: int) -> None:
        with self._lock:
            self._buffers.setdefault(trace_id, [])

    def on_end(self, span: ReadableSpan) -> None:
        ctx = span.get_span_context()
        if ctx is None:
            return
        with self._lock:
            buffer = self._buffers.get(ctx.trace_id)
            if buffer is None:
                self.dropped += 1
                return
            buffer.append(span)

    def drain(self, trace_id: int) -> list[ReadableSpan]:
        with self._lock:
            return self._buffers.pop(trace_id, [])


class TracingPipeline:
    """Process-wide tracer provider plus the run collector. Never registered globally."""

    def __init__(self, exporter: SpanExporter | None = None) -> None:
        self.collector = RunCollector()
        self.provider = TracerProvider(
            resource=Resource.create(
                {"service.name": "agentscope", "service.version": __version__}
            ),
            span_limits=SpanLimits(
                max_span_attributes=256,
                max_events=256,
                max_event_attributes=64,
                max_attribute_length=None,
            ),
        )
        self.provider.add_span_processor(self.collector)
        if exporter is not None:
            self.provider.add_span_processor(SimpleSpanProcessor(exporter))
        self.tracer = self.provider.get_tracer("agentscope", __version__)

    def shutdown(self) -> None:
        self.provider.shutdown()


_default_pipeline: TracingPipeline | None = None
_pipeline_lock = threading.Lock()


def default_pipeline() -> TracingPipeline:
    global _default_pipeline
    with _pipeline_lock:
        if _default_pipeline is None:
            _default_pipeline = TracingPipeline()
        return _default_pipeline


def configure_pipeline(pipeline: TracingPipeline) -> None:
    """Replace the process pipeline (e.g. to attach an OTLP exporter at startup)."""
    global _default_pipeline
    with _pipeline_lock:
        _default_pipeline = pipeline


class SpanHandle:
    """Wrapper that redacts everything written to the underlying OTel span."""

    def __init__(self, span: trace.Span, redactor: Redactor) -> None:
        self.span = span
        self._redactor = redactor

    @property
    def span_id(self) -> str:
        return format(self.span.get_span_context().span_id, "016x")

    @property
    def trace_id(self) -> str:
        return format(self.span.get_span_context().trace_id, "032x")

    def _payload(self, key: str, value: Any) -> None:
        text, truncated = encode_payload(self._redactor.redact(value))
        self.span.set_attribute(key, text)
        if truncated:
            self.span.set_attribute(semconv.AS_PAYLOAD_TRUNCATED, True)

    def set_input(self, value: Any) -> None:
        self._payload(semconv.AS_INPUT, value)

    def set_output(self, value: Any) -> None:
        self._payload(semconv.AS_OUTPUT, value)

    def set_attributes(self, attributes: Mapping[str, AttributeValue | None]) -> None:
        for key, value in attributes.items():
            if value is None:
                continue  # absent means "not reported" — never write a placeholder
            if isinstance(value, str):
                value = self._redactor.redact_text(value)[:4096]
            self.span.set_attribute(key, value)

    def add_event(self, name: str, attributes: Mapping[str, AttributeValue] | None = None) -> None:
        clean = {
            k: self._redactor.redact_text(v)[:2048] if isinstance(v, str) else v
            for k, v in (attributes or {}).items()
        }
        self.span.add_event(name, clean)

    def ok(self) -> None:
        self.span.set_status(Status(StatusCode.OK))

    def fail(self, message: str) -> None:
        self.span.set_status(Status(StatusCode.ERROR, self._redactor.redact_text(message)[:500]))


class TraceRecorder:
    """Records one run. The first span opened becomes the run's root span."""

    def __init__(self, redactor: Redactor | None = None, pipeline: TracingPipeline | None = None):
        self._redactor = redactor or Redactor()
        self._pipeline = pipeline or default_pipeline()
        self._root: SpanHandle | None = None
        self._trace_id: int | None = None
        self.transitions: list[StateTransition] = []

    @property
    def redactor(self) -> Redactor:
        return self._redactor

    @contextmanager
    def span(
        self,
        name: str,
        kind: SpanKind,
        *,
        parent: SpanHandle | None = None,
        attributes: Mapping[str, AttributeValue | None] | None = None,
        input: Any = None,
    ) -> Iterator[SpanHandle]:
        anchor = parent or self._root
        # The root starts a fresh trace (an empty context); others hang off their parent.
        context = (
            trace.set_span_in_context(anchor.span)
            if anchor
            else trace.set_span_in_context(trace.INVALID_SPAN)
        )
        otel_kind = trace.SpanKind.CLIENT if kind is SpanKind.LLM else trace.SpanKind.INTERNAL
        with self._pipeline.tracer.start_as_current_span(
            name,
            context=context,
            kind=otel_kind,
            record_exception=False,
            set_status_on_exception=False,
        ) as span:
            handle = SpanHandle(span, self._redactor)
            if self._root is None:
                self._root = handle
                self._trace_id = span.get_span_context().trace_id
                self._pipeline.collector.register(self._trace_id)
            handle.set_attributes({semconv.AS_SPAN_KIND: kind.value, **(attributes or {})})
            if input is not None:
                handle.set_input(input)
            try:
                yield handle
            except asyncio.CancelledError:
                handle.set_attributes({semconv.AS_CANCELLED: True})
                handle.fail("cancelled: run deadline")
                raise
            except BaseException as exc:
                # Record the failure type only: messages and tracebacks can carry data.
                handle.fail(type(exc).__name__)
                raise

    def transition(self, source: str, target: str, step: int, reason: str) -> None:
        self.transitions.append(
            StateTransition(
                seq=len(self.transitions),
                step=step,
                source=source,
                target=target,
                reason=self._redactor.redact_text(reason)[:500],
                timestamp=datetime.now(UTC),
            )
        )

    def finish(self) -> list[SpanRecord]:
        """Return this run's spans ordered by start time. Call after the root span ended."""
        if self._trace_id is None:
            return []
        spans = self._pipeline.collector.drain(self._trace_id)
        return sorted((to_record(s) for s in spans), key=lambda r: (r.start_time, r.span_id))
