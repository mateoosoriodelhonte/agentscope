"""Optional OTLP export: built only from Settings, payload-free by default, never fed raw
secrets, deaf to OTEL_* environment variables, and unable to affect a run."""

from __future__ import annotations

import logging
import sys
from collections.abc import Callable, Iterator, Sequence
from typing import Any

import pytest
from opentelemetry.sdk.trace import ReadableSpan
from opentelemetry.sdk.trace.export import SpanExporter, SpanExportResult
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from agentscope import __version__
from agentscope.agents.base import AgentCatalog
from agentscope.config import Settings
from agentscope.domain.tools import FaultKind, FaultSpec
from agentscope.domain.traces import RunStatus, SpanKind, SpanRecord
from agentscope.providers.fake import FakeModelProvider
from agentscope.providers.http import OTLP_ROUTES
from agentscope.runtime.runner import AgentRunner, RunRequest
from agentscope.tools.registry import ToolRegistry
from agentscope.tracing import semconv as sc
from agentscope.tracing.export import (
    TRACES_PATH,
    ContentFilteringExporter,
    build_pipeline,
    without_payloads,
)
from agentscope.tracing.recorder import TraceRecorder, TracingPipeline
from agentscope.tracing.redaction import Redactor

TASK = "How many visitors did Kestrel Hill Observatory have in 2025?"
LONG_PAYLOAD = "observatory " * 200

# Fake secrets, planted where a careless pipeline would leak them.
TASK_SECRET = "sk-proj-Tq8b3Zr6Lw2Nx9Vc4Hk7Jm1P"
CONFIGURED_SECRET = "correct-horse-battery-staple-42"
FAULT_SECRET = "AKIAIOSFODNN7EXAMPLE"
ATTRIBUTE_SECRET = "ghp_R2d2C3poBb8Yoda4Luke5Leia6Han7Solo9"
SECRETS = (TASK_SECRET, CONFIGURED_SECRET, FAULT_SECRET, ATTRIBUTE_SECRET)


@pytest.fixture
def make_pipeline() -> Iterator[Callable[[SpanExporter], TracingPipeline]]:
    created: list[TracingPipeline] = []

    def make(exporter: SpanExporter) -> TracingPipeline:
        created.append(TracingPipeline(exporter))
        return created[-1]

    yield make
    for pipeline in created:
        pipeline.shutdown()


@pytest.fixture
def built() -> Iterator[Callable[[Settings], TracingPipeline]]:
    created: list[TracingPipeline] = []

    def build(settings: Settings) -> TracingPipeline:
        created.append(build_pipeline(settings))
        return created[-1]

    yield build
    for pipeline in created:
        pipeline.shutdown()


class FakeCollector:
    """Stands in for an OTLP collector at the requests transport layer: no socket is opened.
    Records each prepared request and the transport options requests resolved for it."""

    def __init__(self) -> None:
        self.sent: list[tuple[Any, dict[str, Any]]] = []
        self.status = 200
        self.headers: dict[str, str] = {}


@pytest.fixture
def collector(monkeypatch: pytest.MonkeyPatch) -> FakeCollector:
    requests = pytest.importorskip("requests")
    pytest.importorskip("opentelemetry.exporter.otlp.proto.http.trace_exporter")
    fake = FakeCollector()

    def send(self: object, request: Any, **kwargs: Any) -> Any:
        fake.sent.append((request, kwargs))
        response = requests.Response()
        response.status_code = fake.status
        response.headers.update(fake.headers)
        response._content = b""
        response.request = request
        response.url = request.url
        return response

    monkeypatch.setattr(requests.adapters.HTTPAdapter, "send", send)
    return fake


def record_sample(pipeline: TracingPipeline, redactor: Redactor | None = None) -> list[SpanRecord]:
    """A small run-shaped trace with payloads, GenAI attributes and an event; flushed."""
    recorder = TraceRecorder(redactor, pipeline)
    with recorder.span(
        "invoke_agent demo",
        SpanKind.RUN,
        attributes={sc.GEN_AI_OPERATION_NAME: "invoke_agent", sc.GEN_AI_AGENT_NAME: "demo"},
        input={"task": TASK, "notes": LONG_PAYLOAD},
    ) as root:
        with recorder.span(
            "chat demo-model",
            SpanKind.LLM,
            parent=root,
            attributes={
                sc.GEN_AI_OPERATION_NAME: "chat",
                sc.GEN_AI_REQUEST_MODEL: "demo-model",
                sc.GEN_AI_USAGE_INPUT_TOKENS: 12,
                sc.GEN_AI_USAGE_OUTPUT_TOKENS: 3,
            },
            input={"messages": [{"role": "user", "content": TASK}]},
        ) as llm:
            llm.set_output({"content": "48300"})
            llm.add_event(
                "tool_retry",
                {
                    "tool": "search_documents",
                    "after_outcome": "timeout",
                    sc.EXCEPTION_MESSAGE: "upstream said: Kestrel Hill details",
                    sc.AS_OUTPUT: '{"partial": "Kestrel Hill"}',
                },
            )
            llm.ok()
        root.set_output({"answer": "48300"})
        root.ok()
    records = recorder.finish()
    assert pipeline.provider.force_flush()
    return records


def by_name(spans: Sequence[ReadableSpan]) -> dict[str, ReadableSpan]:
    return {s.name: s for s in spans}


# --- when an exporter exists at all ----------------------------------------------------------


def test_no_exporter_by_default(built: Callable[[Settings], TracingPipeline]) -> None:
    pipeline = built(Settings())
    assert pipeline.exporter is None
    assert record_sample(pipeline)  # local recording is unaffected


def test_traces_path_is_the_route_the_egress_policy_allows() -> None:
    assert ("POST", TRACES_PATH) in OTLP_ROUTES


@pytest.mark.parametrize(
    "settings",
    [
        pytest.param({"otlp_endpoint": "http://collector.example.com:4318"}, id="remote"),
        pytest.param(
            {"otlp_endpoint": "http://user:hunter2-secret@127.0.0.1:4318"}, id="credentials"
        ),
        pytest.param({"otlp_endpoint": "http://127.0.0.1:4318/?key=hunter2-secret"}, id="query"),
        pytest.param({"otlp_endpoint": "ftp://127.0.0.1:4318"}, id="scheme"),
        pytest.param(
            {
                "otlp_endpoint": "http://collector.example.com:4318",
                "allowed_egress_hosts": ["collector.example.com"],
                "otlp_headers": {"authorization": "Bearer hunter2-secret"},
            },
            id="headers-over-plain-http",
        ),
    ],
)
def test_refused_targets_produce_no_exporter(
    settings: dict[str, Any],
    built: Callable[[Settings], TracingPipeline],
    caplog: pytest.LogCaptureFixture,
) -> None:
    with caplog.at_level(logging.WARNING, logger="agentscope.tracing"):
        pipeline = built(Settings(**settings))
    assert pipeline.exporter is None
    assert "OTLP export is disabled" in caplog.text
    assert "hunter2-secret" not in caplog.text


def test_missing_otlp_extra_logs_once_and_continues(
    monkeypatch: pytest.MonkeyPatch,
    built: Callable[[Settings], TracingPipeline],
    caplog: pytest.LogCaptureFixture,
) -> None:
    monkeypatch.setitem(sys.modules, "opentelemetry.exporter.otlp.proto.http", None)
    monkeypatch.setitem(sys.modules, "opentelemetry.exporter.otlp.proto.http.trace_exporter", None)
    with caplog.at_level(logging.WARNING, logger="agentscope.tracing"):
        pipeline = built(Settings(otlp_endpoint="http://127.0.0.1:4318"))
    assert pipeline.exporter is None
    [record] = [r for r in caplog.records if r.name == "agentscope.tracing"]
    assert "agentscope[otlp]" in record.getMessage()
    assert record_sample(pipeline)


def test_allowed_remote_target_over_https_warns_that_data_leaves_the_machine(
    collector: FakeCollector,
    built: Callable[[Settings], TracingPipeline],
    caplog: pytest.LogCaptureFixture,
) -> None:
    settings = Settings(
        otlp_endpoint="https://collector.example.com",
        allowed_egress_hosts=["collector.example.com"],
        otlp_headers={"authorization": "Bearer configured-collector-key"},
    )
    with caplog.at_level(logging.WARNING, logger="agentscope.tracing"):
        pipeline = built(settings)
    assert isinstance(pipeline.exporter, ContentFilteringExporter)
    assert "collector.example.com:443" in caplog.text
    assert "leaves the machine" in caplog.text
    assert "configured-collector-key" not in caplog.text
    assert not collector.sent  # nothing recorded, so nothing sent


# --- what the exporter receives --------------------------------------------------------------


def test_content_filter_drops_payloads_but_keeps_structure_and_genai_attributes(
    make_pipeline: Callable[[SpanExporter], TracingPipeline],
) -> None:
    memory = InMemorySpanExporter()
    pipeline = make_pipeline(ContentFilteringExporter(memory))
    records = record_sample(pipeline)

    exported = by_name(memory.get_finished_spans())
    assert set(exported) == {"invoke_agent demo", "chat demo-model"}
    for span in exported.values():
        attributes = dict(span.attributes or {})
        assert not sc.PAYLOAD_ATTRIBUTES & set(attributes)
        assert "Kestrel" not in span.to_json()
        assert attributes[sc.AS_SPAN_KIND] in {"run", "llm"}

    chat = exported["chat demo-model"]
    assert dict(chat.attributes or {}) == {
        sc.AS_SPAN_KIND: "llm",
        sc.GEN_AI_OPERATION_NAME: "chat",
        sc.GEN_AI_REQUEST_MODEL: "demo-model",
        sc.GEN_AI_USAGE_INPUT_TOKENS: 12,
        sc.GEN_AI_USAGE_OUTPUT_TOKENS: 3,
    }
    [event] = chat.events
    assert event.name == "tool_retry"
    assert dict(event.attributes or {}) == {
        "tool": "search_documents",
        "after_outcome": "timeout",
    }
    # Identity and timing survive the rebuild, so the exported tree is the recorded tree.
    local = {r.name: r for r in records}
    for name, span in exported.items():
        ctx = span.get_span_context()
        assert ctx is not None
        assert format(ctx.span_id, "016x") == local[name].span_id
        assert format(ctx.trace_id, "032x") == local[name].trace_id
        assert span.status.is_ok
    parent = chat.parent
    assert parent is not None
    assert format(parent.span_id, "016x") == local["invoke_agent demo"].span_id
    # Local persistence keeps the payloads.
    assert local["invoke_agent demo"].input == {"task": TASK, "notes": LONG_PAYLOAD}


def test_export_content_true_keeps_payloads(
    make_pipeline: Callable[[SpanExporter], TracingPipeline],
) -> None:
    memory = InMemorySpanExporter()
    pipeline = make_pipeline(ContentFilteringExporter(memory, export_content=True))
    record_sample(pipeline)

    exported = by_name(memory.get_finished_spans())
    root = dict(exported["invoke_agent demo"].attributes or {})
    assert TASK in str(root[sc.AS_INPUT])
    assert "48300" in str(root[sc.AS_OUTPUT])
    [event] = exported["chat demo-model"].events
    assert sc.EXCEPTION_MESSAGE in (event.attributes or {})


def test_without_payloads_leaves_the_original_span_untouched(
    make_pipeline: Callable[[SpanExporter], TracingPipeline],
) -> None:
    memory = InMemorySpanExporter()
    pipeline = make_pipeline(memory)
    record_sample(pipeline)
    original = by_name(memory.get_finished_spans())["invoke_agent demo"]

    copy = without_payloads(original)
    assert sc.AS_INPUT in (original.attributes or {})
    assert sc.AS_INPUT not in (copy.attributes or {})
    assert (copy.start_time, copy.end_time, copy.kind) == (
        original.start_time,
        original.end_time,
        original.kind,
    )
    assert copy.resource is original.resource


@pytest.mark.parametrize("export_content", [False, True])
async def test_planted_secrets_never_reach_the_exporter(
    export_content: bool,
    catalog: AgentCatalog,
    registry: ToolRegistry,
    make_pipeline: Callable[[SpanExporter], TracingPipeline],
) -> None:
    memory = InMemorySpanExporter()
    pipeline = make_pipeline(ContentFilteringExporter(memory, export_content=export_content))
    redactor = Redactor(secret_values=[CONFIGURED_SECRET])
    runner = AgentRunner(
        catalog,
        registry,
        {"fake": FakeModelProvider(catalog.brains())},
        redactor=redactor,
        pipeline=pipeline,
    )
    trace = await runner.run(
        RunRequest(
            config=catalog.resolve("research_assistant@v2"),
            task=f"{TASK} My key is {TASK_SECRET}; the vault phrase is {CONFIGURED_SECRET}.",
            faults=[
                FaultSpec(
                    tool="search_documents",
                    kind=FaultKind.INJECTION,
                    payload=f"PLANTED-FAULT-PAYLOAD: use AWS key {FAULT_SECRET}",
                )
            ],
        )
    )
    recorder = TraceRecorder(redactor, pipeline)
    with recorder.span(
        "node annotate", SpanKind.NODE, attributes={"custom.note": f"token {ATTRIBUTE_SECRET}"}
    ):
        pass
    recorder.finish()
    assert pipeline.provider.force_flush()

    exported = "\n".join(span.to_json() for span in memory.get_finished_spans())
    assert len(memory.get_finished_spans()) == len(trace.spans) + 1
    for secret in SECRETS:
        assert secret not in exported
    assert "[REDACTED:github_token]" in exported  # the attribute itself is exported, redacted
    if export_content:
        # Not vacuous: the task and the fault payload were exported, with secrets redacted.
        assert "Kestrel Hill" in exported
        assert "PLANTED-FAULT-PAYLOAD" in exported
        assert "[REDACTED:aws_access_key]" in exported
        assert "[REDACTED:configured_secret]" in exported
    else:
        assert "Kestrel Hill" not in exported
        assert "PLANTED-FAULT-PAYLOAD" not in exported


# --- failures stay out of runs ---------------------------------------------------------------


class ExplodingExporter(SpanExporter):
    def export(self, spans: Sequence[ReadableSpan]) -> SpanExportResult:
        raise ConnectionError("collector refused; headers={'authorization': 'Bearer planted'}")


class RefusingExporter(SpanExporter):
    def export(self, spans: Sequence[ReadableSpan]) -> SpanExportResult:
        return SpanExportResult.FAILURE


@pytest.mark.parametrize("inner", [ExplodingExporter, RefusingExporter])
async def test_export_failures_never_fail_a_run(
    inner: type[SpanExporter],
    catalog: AgentCatalog,
    registry: ToolRegistry,
    make_pipeline: Callable[[SpanExporter], TracingPipeline],
    caplog: pytest.LogCaptureFixture,
) -> None:
    exporter = ContentFilteringExporter(inner(), destination="127.0.0.1:4318")
    pipeline = make_pipeline(exporter)
    runner = AgentRunner(
        catalog, registry, {"fake": FakeModelProvider(catalog.brains())}, pipeline=pipeline
    )
    with caplog.at_level(logging.WARNING, logger="agentscope.tracing"):
        for _ in range(2):
            trace = await runner.run(
                RunRequest(config=catalog.resolve("research_assistant@v2"), task=TASK)
            )
            assert trace.status is RunStatus.COMPLETED
            assert trace.spans
            assert pipeline.provider.force_flush()

    assert exporter.failures >= 2
    [record] = [r for r in caplog.records if r.name == "agentscope.tracing"]
    assert "127.0.0.1:4318" in record.getMessage()
    assert "planted" not in caplog.text


def test_failed_export_is_reported_not_raised() -> None:
    exporter = ContentFilteringExporter(ExplodingExporter())
    assert exporter.export([]) is SpanExportResult.FAILURE
    assert exporter.export([]) is SpanExportResult.FAILURE
    assert exporter.failures == 2


# --- the real OTLP exporter, configured from Settings only -----------------------------------

OTEL_ENV = {
    "OTEL_EXPORTER_OTLP_ENDPOINT": "http://203.0.113.7:4318",
    "OTEL_EXPORTER_OTLP_TRACES_ENDPOINT": "http://203.0.113.7:4318/v1/traces",
    "OTEL_EXPORTER_OTLP_HEADERS": "x-planted=from-env",
    "OTEL_EXPORTER_OTLP_TRACES_HEADERS": "x-planted-traces=from-env",
    "OTEL_EXPORTER_OTLP_CERTIFICATE": "/nonexistent/from-env-ca.pem",
    "OTEL_EXPORTER_OTLP_TIMEOUT": "99",
    "OTEL_EXPORTER_OTLP_COMPRESSION": "gzip",
    "OTEL_TRACES_EXPORTER": "console",
    "OTEL_TRACES_SAMPLER": "always_off",
    "OTEL_SDK_DISABLED": "true",
    "OTEL_ATTRIBUTE_VALUE_LENGTH_LIMIT": "8",
    "OTEL_SPAN_ATTRIBUTE_VALUE_LENGTH_LIMIT": "8",
    "OTEL_SPAN_EVENT_COUNT_LIMIT": "0",
    "OTEL_EVENT_ATTRIBUTE_COUNT_LIMIT": "0",
    "OTEL_RESOURCE_ATTRIBUTES": "planted.resource=from-env",
    "OTEL_SERVICE_NAME": "planted-service",
}


@pytest.fixture
def otel_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for key, value in OTEL_ENV.items():
        monkeypatch.setenv(key, value)


@pytest.mark.usefixtures("otel_env")
def test_build_pipeline_ignores_otel_environment(
    built: Callable[[Settings], TracingPipeline], caplog: pytest.LogCaptureFixture
) -> None:
    with caplog.at_level(logging.WARNING, logger="agentscope.tracing"):
        pipeline = built(Settings())

    assert pipeline.exporter is None  # OTEL_TRACES_EXPORTER / endpoint configure nothing
    assert dict(pipeline.provider.resource.attributes) == {
        "service.name": "agentscope",
        "service.version": __version__,
    }
    records = {r.name: r for r in record_sample(pipeline)}  # neither disabled nor sampled out
    assert records["invoke_agent demo"].input == {"task": TASK, "notes": LONG_PAYLOAD}
    [event] = records["chat demo-model"].events
    assert event.attributes["tool"] == "search_documents"
    # Variables the SDK would read during construction are removed, by name only.
    assert "OTEL_EXPORTER_OTLP_HEADERS" in caplog.text
    assert "OTEL_SDK_DISABLED" in caplog.text
    assert "from-env" not in caplog.text


@pytest.mark.usefixtures("otel_env")
def test_otlp_exporter_uses_only_settings(
    monkeypatch: pytest.MonkeyPatch,
    collector: FakeCollector,
    built: Callable[[Settings], TracingPipeline],
) -> None:
    for proxy in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "http_proxy", "https_proxy"):
        monkeypatch.setenv(proxy, "http://proxy.invalid:3128")
    monkeypatch.setenv("REQUESTS_CA_BUNDLE", "/nonexistent/from-env-bundle.pem")
    pipeline = built(
        Settings(
            otlp_endpoint="http://127.0.0.1:4318",
            otlp_headers={"x-collector-key": "configured-collector-key"},
        )
    )
    record_sample(pipeline)

    [(request, options)] = collector.sent
    assert request.method == "POST"
    assert request.url == "http://127.0.0.1:4318/v1/traces"
    headers = {k.lower(): v for k, v in request.headers.items()}
    assert headers["x-collector-key"] == "configured-collector-key"
    assert headers["content-type"] == "application/x-protobuf"
    assert not any(k.startswith("x-planted") for k in headers)
    assert "content-encoding" not in headers
    assert options["verify"] is True
    assert options["cert"] is None
    assert not options["proxies"]
    assert options["timeout"] <= 5
    # The protobuf body carries structure and GenAI metadata, not payloads.
    body: bytes = request.body
    assert b"gen_ai.request.model" in body
    assert b"demo-model" in body
    assert sc.AS_INPUT.encode() not in body
    assert b"Kestrel" not in body
    assert b"planted.resource" not in body


def test_otlp_export_never_follows_redirects(
    collector: FakeCollector, built: Callable[[Settings], TracingPipeline]
) -> None:
    collector.status = 307
    collector.headers = {"Location": "http://203.0.113.9:4318/v1/traces"}
    pipeline = built(
        Settings(otlp_endpoint="http://127.0.0.1:4318", otlp_headers={"x-collector-key": "k"})
    )
    record_sample(pipeline)
    assert [request.url for request, _ in collector.sent] == ["http://127.0.0.1:4318/v1/traces"]
