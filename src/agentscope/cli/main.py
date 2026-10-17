"""AgentScope command-line interface. Every command goes through `agentscope.services`, the
engine the API uses too.

Exit codes: 0 success · 1 checks or gate failed · 2 invalid input · 3 runtime/harness error.
"""

from __future__ import annotations

import asyncio
import json
import sys
from typing import TYPE_CHECKING, Annotated, Any

import typer
from pydantic import JsonValue, ValidationError
from rich.text import Text

from agentscope import __version__
from agentscope.cli.common import SIMULATED, Exit, concise, console, err, fail, open_services
from agentscope.cli.database import db_app
from agentscope.cli.evaluate import compare_command, eval_command
from agentscope.cli.library import agents_app, datasets_app, demo_app
from agentscope.cli.output import emit_json, safe, table
from agentscope.domain.agents import ProviderName
from agentscope.domain.datasets import EvalCase, ExpectedAnswer, MatchMode
from agentscope.domain.tools import FaultKind, FaultSpec

if TYPE_CHECKING:
    from agentscope.domain.evaluation import CaseResult
    from agentscope.domain.traces import TraceRecord

app = typer.Typer(
    name="agentscope",
    help="Understand, test, and improve AI agents.",
    no_args_is_help=True,
    add_completion=False,
    pretty_exceptions_enable=False,
)
traces_app = typer.Typer(help="Inspect stored traces.", no_args_is_help=True)
app.add_typer(traces_app, name="traces")
app.add_typer(datasets_app, name="datasets", no_args_is_help=True)
app.add_typer(agents_app, name="agents", no_args_is_help=True)
app.add_typer(db_app, name="db", no_args_is_help=True)
app.add_typer(demo_app, name="demo", no_args_is_help=True)
app.command("eval")(eval_command)
app.command("compare")(compare_command)


# Remote mode refuses shorter API tokens.
MIN_API_TOKEN_CHARS = 16
# Container mode listens on every interface of the container's own network namespace.
CONTAINER_BIND = "0.0.0.0"  # noqa: S104 - container mode only; the Host check stays loopback


def _parse_fault(raw: str) -> FaultSpec:
    """`tool:kind[:on_call]`, e.g. `search_documents:timeout` or `retrieve_document:error:2`."""
    parts = raw.split(":")
    if len(parts) not in (2, 3):
        raise ValueError(f"invalid fault {raw!r}; expected tool:kind[:on_call]")
    tool, kind = parts[0], parts[1]
    on_call = int(parts[2]) if len(parts) == 3 else 1
    return FaultSpec(tool=tool, kind=FaultKind(kind), on_call=on_call)


@app.callback()
def _root() -> None:
    """AgentScope command-line interface."""


@app.command()
def version() -> None:
    """Print the installed AgentScope version."""
    typer.echo(__version__)


def _schema_variants(node: Any, root: dict[str, Any]) -> list[dict[str, Any]]:
    """The alternatives a JSON schema node allows ($ref and anyOf/oneOf resolved)."""
    if not isinstance(node, dict):
        return []
    ref = node.get("$ref")
    if isinstance(ref, str) and ref.startswith("#/$defs/"):
        node = root.get("$defs", {}).get(ref.removeprefix("#/$defs/"), {})
    options = node.get("anyOf") or node.get("oneOf")
    if isinstance(options, list):
        return [variant for option in options for variant in _schema_variants(option, root)]
    return [node]


def _field_types(schema: dict[str, Any], path: str) -> set[str] | None:
    """JSON types an output schema allows at a dotted field path; None when it has no such
    field (so an expectation on it could only ever fail)."""
    nodes: list[Any] = [schema]
    for part in path.split("."):
        nodes = [
            variant["properties"][part]
            for node in nodes
            for variant in _schema_variants(node, schema)
            if isinstance(variant.get("properties"), dict) and part in variant["properties"]
        ]
        if not nodes:
            return None
    types: set[str] = set()
    for node in nodes:
        for variant in _schema_variants(node, schema):
            declared = variant.get("type")
            types.update(declared if isinstance(declared, list) else [declared] if declared else [])
    return types


def _expectation(
    schema: dict[str, Any], agent: str, value: str, field: str | None, match: MatchMode | None
) -> ExpectedAnswer:
    """`--expect` as an ExpectedAnswer on a field the agent's output really has: `answer`
    unless --expect-field names another, compared numerically when it holds a number
    (unless --match says otherwise). Raises ValueError for anything that could never pass."""
    field = field or "answer"
    types = _field_types(schema, field)
    if types is None:
        fields = ", ".join(sorted(schema.get("properties", {}))) or "none"
        raise ValueError(
            f"{agent} outputs have no field {field!r}; choose one with --expect-field "
            f"(fields: {fields})"
        )
    numeric = bool(types & {"number", "integer"})
    mode = match or (MatchMode.NUMERIC if numeric else MatchMode.NORMALIZED)
    if mode is MatchMode.ONE_OF:
        raise ValueError("--match one_of needs a list of values; put the case in a dataset")
    expected: JsonValue = value
    if mode is MatchMode.NUMERIC or (mode is MatchMode.EXACT and numeric):
        try:
            number = float(value)
        except ValueError:
            raise ValueError(f"--expect {value!r} is not a number") from None
        expected = int(number) if number.is_integer() and "number" not in types else number
    elif mode is MatchMode.EXACT and "boolean" in types and value in ("true", "false"):
        expected = value == "true"
    return ExpectedAnswer(field=field, match=mode, value=expected)


@app.command()
def run(
    task: Annotated[str, typer.Option("--task", "-t", help="Task for the agent.")],
    agent: Annotated[
        str, typer.Option("--agent", "-a", help="agent or agent@version")
    ] = "research_assistant",
    fault: Annotated[
        list[str] | None, typer.Option("--fault", help="tool:kind[:on_call]; repeatable")
    ] = None,
    expect: Annotated[
        str | None, typer.Option(help="Expected value of the output field (see --expect-field)")
    ] = None,
    expect_field: Annotated[
        str | None,
        typer.Option(
            "--expect-field",
            help="Output field --expect is checked against, as a dotted path (default: answer; "
            "e.g. total_hours for task_planner)",
        ),
    ] = None,
    match: Annotated[
        MatchMode | None,
        typer.Option(
            "--match",
            help="How --expect is compared (default: numeric for number fields, else normalized)",
        ),
    ] = None,
    save: Annotated[bool, typer.Option("--save/--no-save", help="Persist the trace")] = True,
    as_json: Annotated[bool, typer.Option("--json", help="Machine-readable output")] = False,
) -> None:
    """Run one agent task and print a summary of its trace.

    Exit codes: 0 done (and --expect matched) · 1 --expect did not match · 2 invalid input ·
    3 the harness or the model provider failed, so the agent was never judged.
    """
    from agentscope.api.schemas import RunCreate
    from agentscope.runtime.runner import RunRequest
    from agentscope.suite_runner import HARNESS_ERROR_CODES

    services = open_services(recover=True)
    try:
        faults = [_parse_fault(f) for f in fault or []]
        config = services.resolve_agent(agent_ref=agent)
        # The bounds POST /api/runs enforces, so both accept exactly the same runs.
        RunCreate(agent_ref=agent, task=task, faults=faults)
        case = None
        if expect is not None:
            schema = services.catalog.get(config.agent).output_schema()
            expected = _expectation(schema, config.agent, expect, expect_field, match)
            case = EvalCase(id="adhoc", task=task, expected=expected, faults=faults)
        elif expect_field is not None or match is not None:
            raise ValueError("--expect-field and --match need --expect")
    except ValidationError as exc:
        raise fail(f"invalid input: {concise(exc)}", Exit.INVALID) from exc
    except (ValueError, LookupError) as exc:
        raise fail(str(exc), Exit.INVALID) from exc

    # Both come back redacted: evaluation saw the raw trace, the terminal never does.
    trace, evaluation = asyncio.run(
        services.run_case(RunRequest(config=config, task=task, faults=faults), case, save=save)
    )
    if trace.error_code in HARNESS_ERROR_CODES:
        code = Exit.ERROR
    elif evaluation is not None and not evaluation.passed:
        code = Exit.FAILED
    else:
        code = Exit.OK
    if as_json:
        emit_json(
            {
                "run_id": trace.run_id,
                "trace_id": trace.trace_id,
                "agent": config.ref,
                "status": trace.status.value,
                "error_code": trace.error_code,
                "simulated": _simulated(trace),
                "steps": trace.steps,
                "final_output": trace.final_output,
                "tool_calls": [
                    {"tool": i.tool, "outcome": i.outcome.value, "executed": i.executed}
                    for i in trace.tool_invocations
                ],
                "evaluation": evaluation.model_dump(mode="json") if evaluation else None,
                "saved": save,
                "exit_code": int(code),
            }
        )
    else:
        _print_trace_summary(trace, evaluation)
    if code is Exit.ERROR:
        err.print(
            safe(
                f"the run did not complete ({trace.error_code}): the harness or the model "
                "provider failed, so the agent was not judged",
                "red",
            )
        )
    raise typer.Exit(int(code))


def _simulated(trace: TraceRecord) -> bool:
    """Runs of the deterministic fake model show harness mechanics, not model behaviour (the
    same rule as a suite's `deterministic` fingerprint)."""
    return trace.config.model.provider is ProviderName.FAKE


def _print_trace_summary(trace: TraceRecord, evaluation: CaseResult | None) -> None:
    console.print(
        Text.assemble(
            ("run ", "dim"), (trace.run_id, "bold"), ("  ", ""), (trace.status.value, "cyan")
        )
    )
    console.print(
        safe(
            f"agent {trace.agent}@{trace.agent_version} · {trace.provider}/{trace.model} · "
            f"{trace.steps} steps · {trace.duration_ms:.0f} ms"
        )
    )
    if _simulated(trace):
        console.print(safe(SIMULATED, "dim"))
    if trace.error_code is not None:
        console.print(safe(f"error: {trace.error_code}: {trace.error or 'no detail'}", "red"))
    calls = table("#", "tool", "outcome", "executed", keys=2)
    for index, inv in enumerate(trace.tool_invocations, 1):
        calls.add_row(
            safe(index),
            safe(inv.tool),
            safe(inv.outcome.value),
            safe("yes" if inv.executed else "blocked"),
        )
    console.print(calls)
    console.print(Text("final output: ", style="bold") + safe(json.dumps(trace.final_output)))
    if evaluation is not None:
        verdict = "PASSED" if evaluation.passed else "FAILED"
        console.print(Text(f"evaluation: {verdict}", style="green" if evaluation.passed else "red"))
        for check in evaluation.checks:
            console.print(
                safe(f"  {check.status.value:<15} {check.name.value:<22} {check.message}")
            )


@traces_app.command("list")
def traces_list(
    limit: Annotated[int, typer.Option(min=1, max=200)] = 20,
    agent: str | None = None,
    as_json: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    """List recent runs."""
    items, total = open_services().repository.list_runs(agent=agent, limit=limit)
    if as_json:
        emit_json({"total": total, "items": [i.model_dump(mode="json") for i in items]})
        return
    runs = table("run_id", "agent", "status", "steps", "verdict", "task", keys=2)
    for item in items:
        verdict = "-" if item.passed is None else ("pass" if item.passed else "fail")
        runs.add_row(
            safe(item.run_id),
            safe(f"{item.agent}@{item.agent_version}"),
            safe(item.status.value),
            safe(item.steps),
            safe(verdict),
            safe(item.task[:60]),
        )
    console.print(runs)
    console.print(Text(f"{len(items)} of {total} runs", style="dim"))


@traces_app.command("show")
def traces_show(run_id: str, as_json: Annotated[bool, typer.Option("--json")] = False) -> None:
    """Show one stored trace."""
    services = open_services()
    found = services.repository.get_run(run_id)
    if found is None:
        raise fail(f"run {run_id} not found", Exit.INVALID)
    trace, _case, evaluation = found
    if as_json:
        emit_json(
            {
                "trace": trace.model_dump(mode="json"),
                "evaluation": evaluation.model_dump(mode="json") if evaluation else None,
                "simulated": _simulated(trace),
            }
        )
        return
    _print_trace_summary(trace, evaluation)
    link = f"http://127.0.0.1:{services.settings.port}/runs/{trace.run_id}"
    console.print(safe(link, "dim"), soft_wrap=True)


@app.command()
def serve(
    host: Annotated[str | None, typer.Option(help="Bind address (default 127.0.0.1)")] = None,
    port: Annotated[int | None, typer.Option(help="Port (default 8765)")] = None,
    allow_remote: Annotated[
        bool,
        typer.Option(
            "--allow-remote",
            help="Remote mode: permit a non-loopback bind; every request must then carry "
            "AGENTSCOPE_API_TOKEN as a Bearer token",
        ),
    ] = False,
    allowed_host: Annotated[
        list[str] | None,
        typer.Option("--allowed-host", help="Extra Host header value to accept; repeatable"),
    ] = None,
    container: Annotated[
        bool,
        typer.Option(
            "--container",
            envvar="AGENTSCOPE_CONTAINER",
            help="Container mode: bind 0.0.0.0 inside a container without an API token. Only "
            "loopback Host headers are accepted, so publish the port on 127.0.0.1 only "
            "(e.g. -p 127.0.0.1:8765:8765)",
        ),
    ] = False,
) -> None:
    """Serve the API and dashboard on localhost."""
    import uvicorn

    from agentscope.api.app import create_app
    from agentscope.config import LOOPBACK_HOSTS, get_settings
    from agentscope.services import egress_destinations

    settings = get_settings()
    bind = host or (CONTAINER_BIND if container else settings.host)
    bind_port = port or settings.port
    remote = allow_remote or settings.allow_remote
    if container and not remote and allowed_host:
        # Without a token, the loopback Host check is what keeps a container reachable only
        # through a port published on the host's loopback interface.
        raise fail(
            "container mode accepts only loopback Host headers; to accept other names, "
            "use --allow-remote with AGENTSCOPE_API_TOKEN",
            Exit.INVALID,
        )
    if bind not in LOOPBACK_HOSTS and not remote and not container:
        raise fail(
            f"refusing to bind {bind}: pass --allow-remote to expose AgentScope "
            "beyond this machine",
            Exit.INVALID,
        )
    token = settings.api_token.get_secret_value() if settings.api_token else ""
    if remote and not token:
        raise fail(
            "remote mode needs AGENTSCOPE_API_TOKEN: every request must then send it as "
            "'Authorization: Bearer <token>'",
            Exit.INVALID,
        )
    if remote and len(token) < MIN_API_TOKEN_CHARS:
        # A short token on a network-exposed server can be guessed.
        raise fail(
            f"AGENTSCOPE_API_TOKEN must be at least {MIN_API_TOKEN_CHARS} characters for "
            "remote mode (e.g. `python -c 'import secrets; print(secrets.token_urlsafe(32))'`)",
            Exit.INVALID,
        )
    services = open_services()
    err.print(safe(f"AgentScope {__version__} on http://{bind}:{bind_port}", "bold"))
    err.print(safe(f"data: {settings.database_location}", "dim"), soft_wrap=True)
    destinations = egress_destinations(settings)
    err.print(
        safe(
            "egress (every destination AgentScope may contact):"
            if destinations
            else "egress: none",
            "dim",
        )
    )
    for destination in destinations:
        err.print(safe(f"  {destination}", "dim"), soft_wrap=True)
    if container and not remote:
        err.print(
            safe(
                f"container mode: listening on {bind}:{bind_port} inside the container without "
                "an API token. Publish this port on 127.0.0.1 only (e.g. -p "
                f"127.0.0.1:{bind_port}:{bind_port}), never on 0.0.0.0 or a LAN address: the "
                "loopback Host check stops browsers, not clients that reach the port directly. "
                "For network access, use --allow-remote with AGENTSCOPE_API_TOKEN.",
                "yellow",
            ),
            soft_wrap=True,
        )
    if remote:
        err.print(safe("remote mode: every request requires the API token", "yellow"))
        if not allowed_host:
            err.print(
                safe(
                    "only loopback Host headers are accepted; pass --allowed-host with the "
                    "name or address remote clients use",
                    "yellow",
                )
            )
    uvicorn.run(
        create_app(services, api_token=token if remote else None, allowed_hosts=allowed_host or ()),
        host=bind,
        port=bind_port,
        log_level="warning",
    )


def main() -> None:
    try:
        app()
    except KeyboardInterrupt:  # pragma: no cover
        sys.exit(130)
    except Exception as exc:  # pragma: no cover - a defect: exit 3, never a traceback dump
        err.print(Text(f"internal error: {type(exc).__name__}", style="red"))
        sys.exit(int(Exit.ERROR))
