"""`agentscope ablate`: which configuration changes explain the difference between A and B.

Exit codes: 0 done · 2 invalid input (checked before anything runs, the output destination
included) · 3 harness error (a case AgentScope or the model provider could not judge in any
suite of the ablation, an unexpected exception, or a result file that could not be written).
"""

from __future__ import annotations

import asyncio
import json
from typing import TYPE_CHECKING, Annotated

import typer
from rich.table import Table

from agentscope.cli.common import (
    SIMULATED,
    Exit,
    check_writable,
    console,
    err,
    fail,
    load_dataset,
    open_services,
)
from agentscope.cli.evaluate import JsonOption, OutputOption, written
from agentscope.cli.output import emit_json, safe, table, write_json
from agentscope.domain.ablations import MAX_PAIRWISE_DIMENSIONS

if TYPE_CHECKING:
    from rich.text import Text

    from agentscope.domain.ablations import Ablation, AblationPair, AblationVariant
    from agentscope.services import AblationPlan, Services


def ablate_command(
    experiment: Annotated[
        str | None,
        typer.Option(
            "--experiment", "-e", help="A stored experiment id: explain it, reusing its suites"
        ),
    ] = None,
    dataset: Annotated[
        str | None,
        typer.Option(
            "--dataset",
            "-d",
            # No square brackets: Rich would read "[@VERSION]" as markup and drop it.
            help="With --a and --b: a dataset file or directory, or a stored dataset NAME or "
            "NAME@VERSION (e.g. research-assistant@1).",
        ),
    ] = None,
    a: Annotated[str | None, typer.Option("--a", help="Reference agent@version")] = None,
    b: Annotated[str | None, typer.Option("--b", help="Candidate agent@version")] = None,
    pairs: Annotated[
        bool,
        typer.Option(
            "--pairs/--no-pairs",
            help=f"Also evaluate every two dimensions together (up to "
            f"{MAX_PAIRWISE_DIMENSIONS} dimensions) to locate interactions",
        ),
    ] = True,
    as_json: JsonOption = False,
    save: Annotated[
        bool, typer.Option("--save/--no-save", help="Persist the variant suites and the result")
    ] = True,
    output: OutputOption = None,
) -> None:
    """Attribute the change from A to B to the configuration dimensions behind it: evaluate
    the dataset with one dimension changed at a time, from A ("A + d") and from B ("B - d").

    Exit codes: 0 done · 2 invalid input · 3 a case could not be judged (harness error).
    """
    from agentscope.datasets.issues import DatasetError

    services = open_services(recover=True)
    try:
        if output is not None:
            check_writable(output, "--output")
        plan = _plan(services, experiment, dataset, a, b, pairs=pairs)
    except DatasetError as exc:
        raise fail("invalid dataset input", Exit.INVALID, exc.issues) from exc
    except (LookupError, ValueError) as exc:
        # Unknown experiments, agents or datasets, and ablations that cannot explain anything.
        raise fail(str(exc), Exit.INVALID) from exc
    err.print(safe(f"evaluating {plan.suites} suites ({plan.total} cases), one at a time", "dim"))
    try:
        ablation = asyncio.run(services.execute_ablation(plan, save=save))
    except Exception as exc:
        message = services.redactor.redact_text(str(exc))
        raise fail(f"ablation failed: {type(exc).__name__}: {message}", Exit.ERROR) from exc

    variants: list[AblationVariant | AblationPair] = [*ablation.variants, *ablation.pairs]
    unjudged = sum(v.harness_errors for v in variants)
    if unjudged or ablation.a.metrics.harness_errors or ablation.b.metrics.harness_errors:
        code = Exit.ERROR
    else:
        code = Exit.OK
    document = {"ablation": ablation.model_dump(mode="json"), "exit_code": int(code)}
    if output is not None and not written(lambda: write_json(output, document)):
        code = Exit.ERROR
        document["exit_code"] = int(code)
    if as_json:
        emit_json(document)
    else:
        _render_ablation(ablation, saved=save)
    raise typer.Exit(int(code))


def _plan(
    services: Services,
    experiment: str | None,
    dataset: str | None,
    a: str | None,
    b: str | None,
    *,
    pairs: bool,
) -> AblationPlan:
    if experiment is not None:
        if dataset is not None or a is not None or b is not None:
            raise ValueError("use either --experiment, or --dataset with --a and --b")
        return services.plan_experiment_ablation(experiment, pairs=pairs)
    if dataset is None or a is None or b is None:
        raise ValueError("pass --experiment ID, or --dataset with --a and --b")
    source = load_dataset(services, dataset)
    a_config = services.resolve_agent(agent_ref=a)
    b_config = services.resolve_agent(agent_ref=b)
    name = f"{a_config.ref} vs {b_config.ref}"
    return services.plan_ablation(name, source.dataset, a_config, b_config, pairs=pairs)


def _pp(value: float) -> Text:
    style = "green" if value > 0 else "red" if value < 0 else ""
    return safe(f"{value:+.1f} pp", style)


def _cells(variant: AblationVariant | None) -> tuple[Text, Text]:
    if variant is None:
        return safe("skipped", "dim"), safe("-")
    return _pp(variant.effect_pp), safe(f"{len(variant.gained)}/{len(variant.lost)}")


def _render_ablation(ablation: Ablation, *, saved: bool) -> None:
    from agentscope.domain.ablations import AblationDirection

    console.print(
        safe(
            f"{ablation.name} · {ablation.dataset_name}@{ablation.dataset_version} · "
            f"{ablation.n} cases compared",
            "bold",
        )
    )
    for side, summary in (("A", ablation.a), ("B", ablation.b)):
        m = summary.metrics
        console.print(safe(f"  {side} {summary.agent_ref}: passed {m.passed}/{m.total}"))
    if ablation.deterministic:
        console.print(safe(SIMULATED))
    for warning in ablation.warnings:
        console.print(safe(f"warning: {warning}", "yellow"))
    if ablation.not_ablated:
        console.print(safe("not ablated (no configuration field changes it on its own):", "bold"))
        for diff in ablation.not_ablated:
            console.print(
                safe(
                    f"  {diff.dimension:<11} {diff.path}: "
                    f"{json.dumps(diff.a)} -> {json.dumps(diff.b)}"
                )
            )

    console.print(safe("alone from A: A + d against A · removed from B: B against B - d", "dim"))
    grid = Table(box=None, header_style="bold", pad_edge=False)
    for column in ("dimension", "alone from A", "gained/lost", "removed from B", "gained/lost"):
        grid.add_column(column, justify="left" if column == "dimension" else "right")
    forward = {
        v.dimension: v for v in ablation.variants if v.direction is AblationDirection.FORWARD
    }
    backward = {
        v.dimension: v for v in ablation.variants if v.direction is AblationDirection.BACKWARD
    }
    for dimension in ablation.dimensions:
        grid.add_row(
            safe(dimension), *_cells(forward.get(dimension)), *_cells(backward.get(dimension))
        )
    console.print(grid)

    if ablation.pairs_skipped:
        console.print(
            safe(
                f"pairs not evaluated (--no-pairs, or more than {MAX_PAIRWISE_DIMENSIONS} "
                "dimensions)",
                "dim",
            )
        )
    elif ablation.pairs:
        interacting = sorted(
            (p for p in ablation.pairs if p.interaction_pp != 0),
            key=lambda p: (-abs(p.interaction_pp), p.dimensions),
        )
        if not interacting:
            console.print(safe(f"no interaction in any of {len(ablation.pairs)} pairs", "dim"))
        else:
            console.print(safe("pair interactions (pair effect minus both solo effects):", "bold"))
        for pair in interacting:
            console.print(
                safe(f"  {' + '.join(pair.dimensions):<26} effect ")
                + _pp(pair.effect_pp)
                + safe("   interaction ")
                + _pp(pair.interaction_pp)
                + safe(f"   ({pair.passed}/{pair.total} passed)")
            )

    console.print(
        safe("total ", "bold")
        + _pp(ablation.total_effect_pp)
        + safe(" = sum of solo effects ")
        + _pp(ablation.sum_of_solo_effects_pp)
        + safe(" + interaction ")
        + _pp(ablation.interaction_pp)
    )
    console.print(
        safe(
            "The interaction is the part of the change no single dimension makes on its own: "
            "positive when dimensions only work together, negative when they cancel out.",
            "dim",
        )
    )

    if ablation.cases:
        console.print(
            safe(
                "flipped cases · sufficient: A + d already has B's verdict · necessary: B - d "
                "is back to A's verdict",
                "dim",
            )
        )
        flipped = table("case", "change", "sufficient", "necessary")
        for case in ablation.cases:
            sufficient = ", ".join(case.sufficient) or "none"
            if case.sufficient_pairs:
                together = "; ".join(" + ".join(pair) for pair in case.sufficient_pairs)
                sufficient += f" (together: {together})"
            flipped.add_row(
                safe(case.case_id),
                safe(case.change.value, "red" if case.change.value == "regression" else ""),
                safe(sufficient),
                safe(", ".join(case.necessary) or "none"),
            )
        console.print(flipped)
    console.print(
        safe(f"ablation {ablation.ablation_id} saved" if saved else "not saved (--no-save)", "dim")
    )
