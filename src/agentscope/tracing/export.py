"""Optional OTLP/HTTP export of trace spans, configured from Settings alone.

Nothing is exported unless AGENTSCOPE_OTLP_ENDPOINT is set and the egress policy accepts it
(loopback, or a host listed in AGENTSCOPE_ALLOWED_EGRESS_HOSTS). What leaves the process:

- Spans are already redacted: payloads, attributes, events and status messages pass through
  the Redactor when they are written to a span (see recorder.py), so the exporter only ever
  sees redacted data, whatever the settings below. Build that Redactor with
  `redaction.settings_secrets(settings)` so configured secrets, AGENTSCOPE_OTLP_HEADERS
  values included, are redacted wherever they appear.
- By default `ContentFilteringExporter` also drops payload attributes (semconv.
  PAYLOAD_ATTRIBUTES: task, prompts, tool arguments and results), so only span structure
  and GenAI metadata are exported. AGENTSCOPE_OTLP_EXPORT_CONTENT=true opts in to payloads.
  Span names and status descriptions are structure and are always exported: error types,
  synthetic fault messages and short tool or provider error summaries (at most 500
  characters, redacted).
- OTEL_* variables are removed before anything is built, because the exporter library
  reads them during construction and merges OTEL_EXPORTER_OTLP_HEADERS into explicitly
  passed headers. The HTTP session ignores proxy, CA-bundle and netrc settings from the
  environment and never follows redirects, like every other AgentScope client.

Export runs on a worker thread and its failures are counted, never raised: a run's outcome
and its locally stored trace never depend on the collector.
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Sequence

from opentelemetry.sdk.trace import Event, ReadableSpan
from opentelemetry.sdk.trace.export import SpanExporter, SpanExportResult
from opentelemetry.util.types import Attributes, AttributeValue

from agentscope import _bootstrap
from agentscope.config import Settings
from agentscope.providers.http import EgressDenied, EgressPolicy, EgressTarget
from agentscope.tracing import semconv
from agentscope.tracing.recorder import TracingPipeline

logger = logging.getLogger("agentscope.tracing")

TRACES_PATH = "/v1/traces"  # the only OTLP route the egress policy allows
EXPORT_TIMEOUT_S = 5.0  # per batch, retries included


def _structural(attributes: Attributes) -> dict[str, AttributeValue]:
    return {k: v for k, v in (attributes or {}).items() if k not in semconv.PAYLOAD_ATTRIBUTES}


def without_payloads(span: ReadableSpan) -> ReadableSpan:
    """A copy of `span` without payload attributes on the span or on its events.

    ReadableSpan is immutable and shared by every span processor, so it is rebuilt rather
    than edited; local persistence keeps the payloads.
    """
    return ReadableSpan(
        name=span.name,
        context=span.get_span_context(),
        parent=span.parent,
        resource=span.resource,
        attributes=_structural(span.attributes),
        events=[Event(e.name, _structural(e.attributes), e.timestamp) for e in span.events],
        links=span.links,
        kind=span.kind,
        status=span.status,
        start_time=span.start_time,
        end_time=span.end_time,
        instrumentation_scope=span.instrumentation_scope,
    )


class ContentFilteringExporter(SpanExporter):
    """Wraps the real exporter: drops payloads unless content export is enabled, and turns
    every export failure into a counter so that it can never affect a run."""

    def __init__(
        self, inner: SpanExporter, *, export_content: bool = False, destination: str = "OTLP"
    ) -> None:
        self._inner = inner
        self._export_content = export_content
        self._destination = destination
        self._lock = threading.Lock()
        self.failures = 0  # failed export batches

    def export(self, spans: Sequence[ReadableSpan]) -> SpanExportResult:
        error: str | None = None
        try:
            batch = spans if self._export_content else [without_payloads(s) for s in spans]
            result = self._inner.export(batch)
        except Exception as exc:
            # The type only: messages from HTTP libraries can embed request details.
            error = type(exc).__name__
            result = SpanExportResult.FAILURE
        if result is not SpanExportResult.SUCCESS:
            self._failed(error)
        return result

    def _failed(self, error: str | None) -> None:
        with self._lock:
            self.failures += 1
            first = self.failures == 1
        if first:
            logger.warning(
                "Exporting spans to %s failed%s; is the collector running and reachable? Runs "
                "are unaffected and still recorded locally; further export failures are "
                "counted, not logged.",
                self._destination,
                f" ({error})" if error else "",
            )

    def force_flush(self, timeout_millis: int = 30000) -> bool:
        try:
            return self._inner.force_flush(timeout_millis)
        except Exception:
            return False

    def shutdown(self) -> None:
        try:
            self._inner.shutdown()
        except Exception as exc:
            logger.debug("OTLP exporter shutdown failed (%s)", type(exc).__name__)


def build_pipeline(settings: Settings) -> TracingPipeline:
    """The process tracing pipeline for `settings`, exporting only when they ask for it.

    Install it with `recorder.configure_pipeline` at startup.
    """
    ignored = _bootstrap.remove_otel_settings()
    if ignored:
        logger.warning(
            "Ignoring OpenTelemetry environment settings (trace export is configured only "
            "through AGENTSCOPE_* settings): %s",
            ", ".join(ignored),
        )
    return TracingPipeline(exporter=_exporter(settings))


def _exporter(settings: Settings) -> ContentFilteringExporter | None:
    if not settings.otlp_endpoint:
        return None
    try:
        target = EgressPolicy.from_settings(settings).target("otlp")
    except EgressDenied as exc:
        logger.warning("OTLP export is disabled: %s", exc)
        return None
    headers = {name: value.get_secret_value() for name, value in settings.otlp_headers.items()}
    if headers and target.scheme != "https" and not target.loopback:
        logger.warning(
            "OTLP export is disabled: refusing to send AGENTSCOPE_OTLP_HEADERS to %s over "
            "plain http; use https",
            target.address,
        )
        return None
    inner = _otlp_exporter(target, headers)
    if inner is None:
        return None
    if not target.loopback:
        logger.warning(
            "OTLP export sends span %s to %s: this data leaves the machine.",
            "structure and payloads" if settings.otlp_export_content else "structure",
            target.address,
        )
    return ContentFilteringExporter(
        inner, export_content=settings.otlp_export_content, destination=target.address
    )


def _otlp_exporter(target: EgressTarget, headers: dict[str, str]) -> SpanExporter | None:
    try:
        import requests
        from opentelemetry.exporter.otlp.proto.http import Compression
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
    except ImportError:
        logger.warning(
            "AGENTSCOPE_OTLP_ENDPOINT is set but the OTLP exporter is not installed; install "
            "'agentscope[otlp]' to export traces. Continuing without export."
        )
        return None
    session = requests.Session()
    session.trust_env = False  # no proxy, CA-bundle or netrc settings from the environment
    # Never follow a redirect: it would carry the headers to another origin. The exporter
    # already passes allow_redirects=False, but then counts a 3xx answer as a successful
    # export, silently dropping the batch; with max_redirects=0 requests raises on any
    # redirect instead, so it is counted and reported as a failure.
    session.max_redirects = 0
    # The library logs every failed batch; ContentFilteringExporter logs the first one.
    logging.getLogger("opentelemetry.exporter.otlp.proto.http").setLevel(logging.CRITICAL)
    exporter: SpanExporter = OTLPSpanExporter(
        endpoint=target.url(TRACES_PATH),
        headers=headers,
        timeout=EXPORT_TIMEOUT_S,
        compression=Compression.NoCompression,
        session=session,
    )
    return exporter
