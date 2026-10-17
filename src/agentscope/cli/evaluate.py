"""`agentscope eval` and `agentscope compare`: evaluation suites from the command line.

Exit codes for both: 0 passed · 1 gate failed (eval), or a regression with
--fail-on-regression (compare) · 2 invalid input (checked before anything runs, output
destinations included) · 3 harness error (a case the harness could not judge because
AgentScope or the model provider failed, an unexpected exception, or a result file that
could not be written). A harness error wins over a gate failure.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import TYPE_CHECKING, Annotated

import typer
from rich.table import Table
from rich.text import Text

from agentscope.cli.common import (
    SIMULATED,
    DatasetSource,
    Exit,
    check_writable,
    console,
    err,
    escapes,
    fail,
    load_dataset,
    open_services,
)
from agentscope.cli.output import emit_json, safe, table, write_json

if TYPE_CHECKING:
    from agentscope.domain.agents import AgentConfig
    from agentscope.domain.datasets import Dataset
    from agentscope.domain.evaluation import CaseResult, GateThresholds, SuiteResult
    from agentscope.domain.experiments import Comparison
    from agentscope.evaluation.baseline import Baseline
    from agentscope.services import Services

DatasetOption = Annotated[
    str,
    typer.Option(
        "--dataset",
        "-d",
        # No square brackets: Rich would read "[@VERSION]" as markup and drop it.
        help="A dataset file, a dataset directory (*.dataset.json, gate.json, baselines), "
        "or a stored dataset NAME or NAME@VERSION (e.g. research-assistant@1).",
    ),
]
JsonOption = Annotated[bool, typer.Option("--json", help="Print one JSON document")]
OutputOption = Annotated[
    Path | None, typer.Option("--output", "-o", help="Also write the JSON document here")
]


def _select_config(
    services: Services, dataset: Dataset, agent: str | None, config_file: Path | None
) -> AgentConfig:
    from agentscope.domain.agents import AgentConfig

    if agent is not None and config_file is not None:
        raise ValueError("use either --agent or --config, not both")
    if config_file is not None:
        try:
            raw = config_file.read_bytes()
        except OSError as exc:
            raise ValueError(f"cannot read {config_file}: {exc.strerror}") from None
        return services.resolve_agent(config=AgentConfig.model_validate_json(raw, strict=True))
    ref = agent or dataset.agent
    if ref is None:
        raise ValueError("the dataset names no agent; pass --agent or --config")
    # A bare agent name resolves to its latest built-in version.
    return services.resolve_agent(agent_ref=ref)


def _thresholds(
    source: DatasetSource, path: Path | None, check: bool, baseline: Path | None = None
) -> GateThresholds | None:
    from agentscope.datasets.loader import GATE_FILE, load_gate_file
    from agentscope.domain.evaluation import GateThresholds

    if path is not None:
        return load_gate_file(path)
    if not check:
        # An explicit baseline asks for a comparison with it: gate on cases that passed
        # there and fail now, rather than loading it only to ignore it.
        return GateThresholds(max_new_regressions=0) if baseline is not None else None
    if source.directory is not None:
        candidate = source.directory / GATE_FILE
        if escapes(candidate, source.directory):
            err.print(
                safe(f"warning: ignoring {candidate}: symlink resolves outside the directory")
            )
        elif candidate.is_file():
            return load_gate_file(candidate)
    # No thresholds anywhere: --check then requires every case to pass.
    return GateThresholds(min_pass_rate=1.0)


def _baseline(
    source: DatasetSource, config: AgentConfig, path: Path | None, check: bool
) -> Baseline | None:
    from agentscope.datasets.loader import BASELINE_SUFFIX
    from agentscope.evaluation.baseline import read_baseline

    if path is not None:
        return read_baseline(path)
    if check and source.directory is not None:
        candidate = source.directory / f"{config.ref}{BASELINE_SUFFIX}"
        if escapes(candidate, source.directory):
            err.print(
                safe(f"warning: ignoring {candidate}: symlink resolves outside the directory")
            )
        elif candidate.is_file():
            return read_baseline(candidate)
    return None


def failed_checks(case: CaseResult) -> list[str]:
    from agentscope.domain.evaluation import CheckStatus

    failed = [c.name.value for c in case.checks if c.status is CheckStatus.FAILED]
    return (["harness_error"] if case.harness_error is not None else []) + failed


def _written(write: Callable[[], object]) -> bool:
    """Run a file write after the work is done. A failure (the destination vanished since it
    was checked) is reported, and the result is still printed rather than lost."""
    try:
        write()
    except OSError as exc:
        err.print(safe(f"error: could not write {exc.filename or 'a file'}: {exc.strerror}", "red"))
        return False
    return True


def suite_exit_code(result: SuiteResult) -> Exit:
    if any(case.harness_error is not None for case in result.cases):
        return Exit.ERROR
    if result.gate is not None and not result.gate.passed:
        return Exit.FAILED
    return Exit.OK


def eval_command(
    dataset: DatasetOption,
    agent: Annotated[
        str | None,
        typer.Option(
            "--agent", "-a", help="agent@version (default: the dataset's agent, latest version)"
        ),
    ] = None,
    config_file: Annotated[
        Path | None, typer.Option("--config", help="An AgentConfig JSON file instead of --agent")
    ] = None,
    check: Annotated[
        bool,
        typer.Option(
            "--check",
            help="Gate the result: thresholds from --thresholds or DIR/gate.json, the baseline "
            "from --baseline or DIR/<agent>@<version>.baseline.json; with no thresholds at "
            "all, every case must pass.",
        ),
    ] = False,
    thresholds: Annotated[
        Path | None, typer.Option("--thresholds", help="Gate thresholds (gate.json format)")
    ] = None,
    baseline: Annotated[
        Path | None,
        typer.Option(
            "--baseline",
            help="Baseline file; without --check or --thresholds, the gate is then: no case "
            "that passed in the baseline may fail now",
        ),
    ] = None,
    write_baseline: Annotated[
        Path | None, typer.Option("--write-baseline", help="Write this result as a baseline")
    ] = None,
    output: OutputOption = None,
    as_json: JsonOption = False,
    save: Annotated[bool, typer.Option("--save/--no-save", help="Persist the suite")] = True,
    label: Annotated[str, typer.Option("--label", help="A label for the suite")] = "",
) -> None:
    """Evaluate an agent on a dataset; with --check, gate the result (for CI)."""
    from agentscope.datasets.issues import DatasetError
    from agentscope.evaluation.baseline import BaselineError, build_baseline
    from agentscope.evaluation.baseline import write_baseline as store_baseline

    services = open_services(recover=True)
    try:
        for destination, option in ((write_baseline, "--write-baseline"), (output, "--output")):
            if destination is not None:
                check_writable(destination, option)
        source = load_dataset(services, dataset)
        config = _select_config(services, source.dataset, agent, config_file)
        gate = _thresholds(source, thresholds, check, baseline)
        reference = _baseline(source, config, baseline, check)
        services.check_suite_inputs(source.dataset, config, baseline=reference)
    except DatasetError as exc:
        raise fail("invalid dataset input", Exit.INVALID, exc.issues) from exc
    except (BaselineError, LookupError, ValueError) as exc:
        # Incompatible baselines, unknown agents or datasets, disabled providers, bad flags.
        raise fail(str(exc), Exit.INVALID) from exc
    try:
        result = asyncio.run(
            services.run_suite(
                source.dataset, config, label=label, thresholds=gate, baseline=reference, save=save
            )
        )
    except Exception as exc:
        message = services.redactor.redact_text(str(exc))
        raise fail(f"evaluation failed: {type(exc).__name__}: {message}", Exit.ERROR) from exc

    code = suite_exit_code(result)
    if write_baseline is not None:
        if code is Exit.ERROR:
            err.print(safe("baseline not written: the suite has harness errors", "yellow"))
        elif not _written(lambda: store_baseline(build_baseline(result), write_baseline)):
            code = Exit.ERROR
    document = {
        "suite": result.model_dump(mode="json"),
        "gate": result.gate.model_dump(mode="json") if result.gate else None,
        "exit_code": int(code),
    }
    if output is not None and not _written(lambda: write_json(output, document)):
        code = Exit.ERROR
        document["exit_code"] = int(code)
    if as_json:
        emit_json(document)
    else:
        _render_suite(result, saved=save, port=services.settings.port)
    raise typer.Exit(int(code))


def _render_suite(result: SuiteResult, *, saved: bool, port: int) -> None:
    m = result.metrics
    env = result.environment
    console.print(
        safe(
            f"{result.dataset_name}@{result.dataset_version} · {result.agent_config.ref} · "
            f"{env.provider}/{env.model} · {m.total} cases",
            "bold",
        )
    )
    if env.deterministic:
        console.print(safe(SIMULATED, "dim"))

    cases = table("case", "verdict", "run status", "failed checks")
    for case in result.cases:
        cases.add_row(
            safe(case.case_id),
            safe("pass", "green") if case.passed else safe("FAIL", "red"),
            safe(case.status.value if case.status else "-"),
            safe(", ".join(failed_checks(case)) or "-"),
        )
    console.print(cases)

    console.print(
        safe(
            f"passed {m.passed}/{m.total} · harness errors {m.harness_errors} · invalid outputs "
            f"{m.invalid_outputs}/{m.total} · policy violations {m.policy_violation_cases}/"
            f"{m.total} · unauthorized tool executions {m.unauthorized_tool_executions} "
            f"(attempts {m.unauthorized_tool_attempts})"
        )
    )
    checks = [
        f"{name.value} {stats.passed}/{stats.applicable}"
        for name, stats in m.checks.items()
        if stats.applicable
    ]
    if checks:
        console.print(safe("checks: " + " · ".join(checks), "dim"))

    if result.gate is not None:
        verdict = "PASSED" if result.gate.passed else "FAILED"
        console.print(safe(f"gate {verdict}", "green" if result.gate.passed else "red bold"))
        for rule in result.gate.rules:
            line = (
                f"  {'pass' if rule.passed else 'FAIL'}  {rule.name}: threshold "
                f"{json.dumps(rule.threshold)}, actual {json.dumps(rule.actual)}"
            )
            console.print(safe(line + (f" ({rule.detail})" if rule.detail else "")))

    for case in result.cases:
        if case.harness_error is not None:
            console.print(safe(f"harness error in {case.case_id}: {case.harness_error}", "red"))
    failures = [c for c in result.cases if not c.passed and c.run_id is not None]
    if saved and failures:
        console.print(safe("failed cases:", "bold"))
        for case in failures:
            check = next((n for n in failed_checks(case) if n != "harness_error"), None)
            link = f"http://127.0.0.1:{port}/runs/{case.run_id}"
            query = f"?check={check}" if check else ""
            console.print(safe(f"  {case.case_id}  {link}{query}"), soft_wrap=True)
    console.print(
        safe(
            f"evaluation {result.suite_id} saved" if saved else "not saved (--no-save)",
            "dim",
        )
    )


def compare_command(
    dataset: DatasetOption,
    a: Annotated[str, typer.Option("--a", help="Reference agent@version")],
    b: Annotated[str, typer.Option("--b", help="Candidate agent@version")],
    as_json: JsonOption = False,
    output: OutputOption = None,
    fail_on_regression: Annotated[
        bool, typer.Option("--fail-on-regression", help="Exit 1 when any case regressed")
    ] = False,
) -> None:
    """Evaluate two agent versions on one dataset and compare them case by case."""
    from agentscope.datasets.issues import DatasetError
    from agentscope.domain.agents import ProviderName
    from agentscope.domain.experiments import CaseChange

    services = open_services(recover=True)
    try:
        if output is not None:
            check_writable(output, "--output")
        source = load_dataset(services, dataset)
        a_config = services.resolve_agent(agent_ref=a)
        b_config = services.resolve_agent(agent_ref=b)
        for config in (a_config, b_config):
            services.check_suite_inputs(source.dataset, config)
    except DatasetError as exc:
        raise fail("invalid dataset input", Exit.INVALID, exc.issues) from exc
    except (LookupError, ValueError) as exc:
        raise fail(str(exc), Exit.INVALID) from exc
    try:
        comparison = asyncio.run(
            services.run_experiment(
                f"{a_config.ref} vs {b_config.ref}", source.dataset, a_config, b_config
            )
        )
    except Exception as exc:
        message = services.redactor.redact_text(str(exc))
        raise fail(f"comparison failed: {type(exc).__name__}: {message}", Exit.ERROR) from exc

    regressions = sum(c.change is CaseChange.REGRESSION for c in comparison.cases)
    if comparison.a.metrics.harness_errors or comparison.b.metrics.harness_errors:
        code = Exit.ERROR
    elif fail_on_regression and regressions:
        code = Exit.FAILED
    else:
        code = Exit.OK
    document = {"comparison": comparison.model_dump(mode="json"), "exit_code": int(code)}
    if output is not None and not _written(lambda: write_json(output, document)):
        code = Exit.ERROR
        document["exit_code"] = int(code)
    if as_json:
        emit_json(document)
    else:
        simulated = [
            side
            for side, config in (("A", a_config), ("B", b_config))
            if config.model.provider is ProviderName.FAKE
        ]
        _render_comparison(comparison, simulated)
    raise typer.Exit(int(code))


# Format spec and suffix per MetricDelta unit.
_NUMBER_FORMATS = {"pp": (".1f", " pp"), "count": ("g", ""), "steps": (".2f", "")}
_CHANGE_ORDER = (
    "regression",
    "fix",
    "still_failing_changed",
    "still_failing_same",
    "content_changed",
    "added",
    "removed",
)


def _render_comparison(comparison: Comparison, simulated: Sequence[str] = ()) -> None:
    """`simulated` names the sides ("A", "B") that ran on the fake model: with only one of
    them, the comparison is not deterministic, but that side still shows no model behaviour."""
    a, b = comparison.a, comparison.b
    console.print(
        safe(
            f"{comparison.name} · {comparison.dataset_name}@{comparison.dataset_version}",
            "bold",
        )
    )
    for side, summary in (("A", a), ("B", b)):
        m = summary.metrics
        console.print(safe(f"  {side} {summary.agent_ref}: passed {m.passed}/{m.total}"))
    if comparison.deterministic:
        console.print(safe(SIMULATED))
    else:
        if simulated:
            console.print(safe(f"{' and '.join(simulated)}: {SIMULATED}"))
        console.print(safe("Non-deterministic: flips may be noise."))
    if comparison.dataset_mismatch:
        console.print(safe("dataset content differs: only shared, unchanged cases compared", "red"))

    if comparison.config_diff:
        note = " (confounded: attribution not possible)" if comparison.confounded else ""
        console.print(safe(f"config changes{note}:", "bold"))
        for diff in comparison.config_diff:
            console.print(
                safe(
                    f"  {diff.dimension:<11} {diff.path}: "
                    f"{json.dumps(diff.a)} -> {json.dumps(diff.b)}"
                )
            )

    metrics = Table(box=None, header_style="bold", pad_edge=False)
    for column in ("metric", "A", "B", "delta", "n"):
        metrics.add_column(column, justify="left" if column == "metric" else "right")
    for delta in comparison.metric_deltas:
        number, unit = _NUMBER_FORMATS[delta.unit]
        improved = delta.improved
        style = "" if improved is None else ("green" if improved else "red")
        metrics.add_row(
            safe(delta.metric),
            safe(f"{delta.a:{number}}{unit}"),
            safe(f"{delta.b:{number}}{unit}"),
            safe(f"{delta.delta:+{number}}{unit}", style),
            safe(delta.n),
        )
    console.print(metrics)

    for change in _CHANGE_ORDER:
        for case in (c for c in comparison.cases if c.change.value == change):
            line = Text(f"  {change:<22} ", style="red" if change == "regression" else "")
            failed = (
                f"  A: {', '.join(case.a_failed_checks) or '-'}"
                f" | B: {', '.join(case.b_failed_checks) or '-'}"
            )
            console.print(line + safe(case.case_id + failed))
    passing = sum(c.change.value == "still_passing" for c in comparison.cases)
    console.print(safe(f"  still passing: {passing}", "dim"))
    console.print(safe(f"experiment {comparison.experiment_id} saved", "dim"))
