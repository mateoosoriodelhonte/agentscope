"""AgentScope command-line interface.

Exit codes: 0 success · 1 checks or gate failed · 2 invalid input · 3 runtime/harness error.
"""

from __future__ import annotations

import asyncio
import json
import sys
from enum import IntEnum
from typing import Annotated, Any

import typer
from rich.console import Console
from rich.table import Table
from rich.text import Text

from agentscope import __version__
from agentscope.cli.output import clean_text, emit_json
from agentscope.domain.datasets import EvalCase, ExpectedAnswer, MatchMode
from agentscope.domain.tools import FaultKind, FaultSpec

app = typer.Typer(
    name="agentscope",
    help="Understand, test, and improve AI agents.",
    no_args_is_help=True,
    add_completion=False,
    pretty_exceptions_enable=False,
)
traces_app = typer.Typer(help="Inspect stored traces.", no_args_is_help=True)
app.add_typer(traces_app, name="traces")

console = Console(highlight=False)
err = Console(stderr=True, highlight=False)


class Exit(IntEnum):
    OK = 0
    FAILED = 1
    INVALID = 2
    ERROR = 3


def _fail(message: str, code: Exit) -> typer.Exit:
    err.print(Text(f"error: {clean_text(message)}", style="red"))
    return typer.Exit(int(code))


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


@app.command()
def run(
    task: Annotated[str, typer.Option("--task", "-t", help="Task for the agent.")],
    agent: Annotated[
        str, typer.Option("--agent", "-a", help="agent or agent@version")
    ] = "research_assistant",
    fault: Annotated[
        list[str] | None, typer.Option("--fault", help="tool:kind[:on_call]; repeatable")
    ] = None,
    expect: Annotated[str | None, typer.Option(help="Expected answer (normalized match)")] = None,
    save: Annotated[bool, typer.Option("--save/--no-save", help="Persist the trace")] = True,
    as_json: Annotated[bool, typer.Option("--json", help="Machine-readable output")] = False,
) -> None:
    """Run one agent task and print a summary of its trace."""
    from agentscope.agents.base import UnknownAgentError
    from agentscope.runtime.runner import RunRequest
    from agentscope.services import build_services

    try:
        faults = [_parse_fault(f) for f in fault or []]
        services = build_services()
        config = services.catalog.resolve(agent)
    except (ValueError, UnknownAgentError) as exc:
        raise _fail(str(exc), Exit.INVALID) from exc

    case = (
        EvalCase(
            id="adhoc",
            task=task,
            expected=ExpectedAnswer(match=MatchMode.NORMALIZED, value=expect),
            faults=faults,
        )
        if expect is not None
        else None
    )
    try:
        trace, evaluation = asyncio.run(
            services.run_case(RunRequest(config=config, task=task, faults=faults), case, save=save)
        )
    except LookupError as exc:
        raise _fail(str(exc), Exit.INVALID) from exc

    if as_json:
        emit_json(
            {
                "run_id": trace.run_id,
                "trace_id": trace.trace_id,
                "agent": config.ref,
                "status": trace.status.value,
                "steps": trace.steps,
                "final_output": trace.final_output,
                "tool_calls": [
                    {"tool": i.tool, "outcome": i.outcome.value, "executed": i.executed}
                    for i in trace.tool_invocations
                ],
                "evaluation": evaluation.model_dump(mode="json") if evaluation else None,
                "saved": save,
            }
        )
    else:
        _print_trace_summary(trace, evaluation)
    if evaluation is not None and not evaluation.passed:
        raise typer.Exit(int(Exit.FAILED))


def _print_trace_summary(trace: Any, evaluation: Any) -> None:
    console.print(
        Text.assemble(
            ("run ", "dim"), (trace.run_id, "bold"), ("  ", ""), (trace.status.value, "cyan")
        )
    )
    console.print(
        Text(
            f"agent {trace.agent}@{trace.agent_version} · {trace.provider}/{trace.model} · "
            f"{trace.steps} steps · {trace.duration_ms:.0f} ms"
        )
    )
    table = Table(show_header=True, header_style="bold", box=None)
    table.add_column("#", justify="right")
    table.add_column("tool")
    table.add_column("outcome")
    table.add_column("executed")
    for index, inv in enumerate(trace.tool_invocations, 1):
        table.add_row(
            str(index),
            clean_text(inv.tool),
            inv.outcome.value,
            "yes" if inv.executed else "blocked",
        )
    console.print(table)
    console.print(
        Text("final output: ", style="bold") + Text(clean_text(json.dumps(trace.final_output)))
    )
    if evaluation is not None:
        verdict = "PASSED" if evaluation.passed else "FAILED"
        console.print(Text(f"evaluation: {verdict}", style="green" if evaluation.passed else "red"))
        for check in evaluation.checks:
            console.print(
                Text(
                    f"  {check.status.value:<15} {check.name.value:<22} {clean_text(check.message)}"
                )
            )


@traces_app.command("list")
def traces_list(
    limit: Annotated[int, typer.Option(min=1, max=200)] = 20,
    agent: str | None = None,
    as_json: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    """List recent runs."""
    from agentscope.services import build_services

    items, total = build_services().repository.list_runs(agent=agent, limit=limit)
    if as_json:
        emit_json({"total": total, "items": [i.model_dump(mode="json") for i in items]})
        return
    table = Table(box=None, header_style="bold")
    for column in ("run_id", "agent", "status", "steps", "verdict", "task"):
        table.add_column(column)
    for item in items:
        verdict = "-" if item.passed is None else ("pass" if item.passed else "fail")
        table.add_row(
            item.run_id,
            f"{item.agent}@{item.agent_version}",
            item.status.value,
            str(item.steps),
            verdict,
            clean_text(item.task)[:60],
        )
    console.print(table)
    console.print(Text(f"{len(items)} of {total} runs", style="dim"))


@traces_app.command("show")
def traces_show(run_id: str, as_json: Annotated[bool, typer.Option("--json")] = False) -> None:
    """Show one stored trace."""
    from agentscope.services import build_services

    found = build_services().repository.get_run(run_id)
    if found is None:
        raise _fail(f"run {run_id} not found", Exit.INVALID)
    trace, _case, evaluation = found
    if as_json:
        emit_json(
            {
                "trace": trace.model_dump(mode="json"),
                "evaluation": evaluation.model_dump(mode="json") if evaluation else None,
            }
        )
        return
    _print_trace_summary(trace, evaluation)


@app.command()
def serve(
    host: Annotated[str | None, typer.Option(help="Bind address (default 127.0.0.1)")] = None,
    port: Annotated[int | None, typer.Option(help="Port (default 8765)")] = None,
    allow_remote: Annotated[
        bool, typer.Option("--allow-remote", help="Permit a non-loopback bind")
    ] = False,
) -> None:
    """Serve the API and dashboard on localhost."""
    import uvicorn

    from agentscope.api.app import create_app
    from agentscope.config import LOOPBACK_HOSTS, get_settings

    settings = get_settings()
    bind = host or settings.host
    if bind not in LOOPBACK_HOSTS and not (allow_remote or settings.allow_remote):
        raise _fail(
            f"refusing to bind {bind}: pass --allow-remote to expose AgentScope "
            "beyond this machine",
            Exit.INVALID,
        )
    err.print(
        Text(
            f"AgentScope {__version__} on http://{bind}:{port or settings.port} "
            f"(data: {settings.effective_database_url.split('://', 1)[0]})",
            style="dim",
        )
    )
    uvicorn.run(create_app(), host=bind, port=port or settings.port, log_level="warning")


def main() -> None:
    try:
        app()
    except KeyboardInterrupt:  # pragma: no cover
        sys.exit(130)
