"""`agentscope datasets`, `agentscope agents` and `agentscope demo`."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Annotated

import typer

from agentscope.cli.common import Exit, console, fail, load_path, open_services
from agentscope.cli.output import emit_json, safe, table

datasets_app = typer.Typer(help="Import, validate and export evaluation datasets.")
agents_app = typer.Typer(help="Inspect the built-in agents.")
demo_app = typer.Typer(help="Demo data.")

JsonOption = Annotated[bool, typer.Option("--json", help="Machine-readable output")]


@datasets_app.command("list")
def datasets_list(as_json: JsonOption = False) -> None:
    """List stored datasets (the built-in ones are registered automatically)."""
    items = open_services().repository.list_datasets()
    if as_json:
        emit_json([item.model_dump(mode="json") for item in items])
        return
    grid = table("dataset", "agent", "cases", "content hash", "description", keys=2)
    for item in items:
        grid.add_row(
            safe(f"{item.name}@{item.version}"),
            safe(item.agent or "-"),
            safe(item.case_count),
            safe(item.content_hash[:19]),
            safe(item.description[:60]),
        )
    console.print(grid)


@datasets_app.command("validate")
def datasets_validate(
    path: Annotated[Path, typer.Argument(help="A dataset file or directory")],
    as_json: JsonOption = False,
) -> None:
    """Load and lint a dataset without importing it. Exit 2 when it is invalid."""
    from agentscope.datasets.issues import DatasetError
    from agentscope.services import validation_report

    services = open_services()
    try:
        dataset = load_path(path)
    except DatasetError as exc:
        report = validation_report(None, exc.issues)
    else:
        report = validation_report(dataset, services.validate_dataset(dataset))
    if as_json:
        emit_json(report.model_dump(mode="json"))
    else:
        for issue in report.issues:
            style = "red" if issue.severity == "error" else "yellow"
            console.print(
                safe(f"{issue.severity}: {issue.path or '(document)'}: {issue.message}", style)
            )
        summary = f"valid: {report.case_count} cases, {report.content_hash}"
        console.print(safe(summary, "green") if report.valid else safe("invalid", "red bold"))
    if not report.valid:
        raise typer.Exit(int(Exit.INVALID))


@datasets_app.command("import")
def datasets_import(
    path: Annotated[Path, typer.Argument(help="A dataset file or directory")],
    as_json: JsonOption = False,
) -> None:
    """Store a dataset version. Versions are immutable: other content under an existing
    name@version is refused (exit 2)."""
    from agentscope.datasets.issues import DatasetError
    from agentscope.storage.repository import DatasetConflict

    services = open_services()
    try:
        summary = services.import_dataset(load_path(path))
    except DatasetError as exc:
        raise fail("the dataset is invalid", Exit.INVALID, exc.issues) from exc
    except DatasetConflict as exc:
        raise fail(str(exc), Exit.INVALID) from exc
    if as_json:
        emit_json(summary.model_dump(mode="json"))
        return
    console.print(
        safe(
            f"imported {summary.name}@{summary.version}: {summary.case_count} cases, "
            f"{summary.content_hash}"
        )
    )


@datasets_app.command("export")
def datasets_export(
    # No square brackets: Rich would read "[@VERSION]" as markup and drop it.
    ref: Annotated[
        str, typer.Argument(help="A stored dataset: NAME (latest version) or NAME@VERSION")
    ],
    output: Annotated[
        Path,
        typer.Option("--output", "-o", help="Directory to write <name>@<version>.dataset.json"),
    ] = Path(),
) -> None:
    """Write a stored dataset as canonical JSON (identical content gives identical bytes)."""
    from agentscope.datasets.io import export_dataset, export_path
    from agentscope.datasets.packaged import UnknownDatasetError

    services = open_services()
    name, _, version = ref.partition("@")
    try:
        dataset = services.stored_dataset(name, version or None)
        output.mkdir(parents=True, exist_ok=True)
        target = export_path(output, dataset)
    except (UnknownDatasetError, ValueError, OSError) as exc:
        raise fail(str(exc), Exit.INVALID) from exc
    target.write_bytes(export_dataset(dataset))
    console.print(safe(f"wrote {target}"))


@agents_app.command("list")
def agents_list(as_json: JsonOption = False) -> None:
    """List every built-in agent version and what distinguishes it."""
    agents = open_services().agents()
    if as_json:
        emit_json(agents.model_dump(mode="json"))
        return
    grid = table("agent", "label", "model", "graph", "isolation", "retries")
    for info in agents.agents:
        for config in info.versions:
            grid.add_row(
                safe(config.ref),
                safe(config.label),
                safe(f"{config.model.provider.value}/{config.model.model}"),
                safe(config.graph.value),
                safe("yes" if config.isolate_untrusted else "no"),
                safe(config.retry.max_tool_retries),
            )
    console.print(grid)


def _counted(count: int, kind: str) -> str:
    """`1 ablation`, `2 ablations`: every kind the demo counts is a plural ending in s."""
    return f"{count} {kind.removesuffix('s') if count == 1 else kind}"


@demo_app.command("seed")
def demo_seed(as_json: JsonOption = False) -> None:
    """Seed the demo story (idempotent: seeding again adds nothing)."""
    from agentscope.demo import seed_demo

    services = open_services(recover=True)
    summary = asyncio.run(seed_demo(services))
    if as_json:
        emit_json(summary.model_dump(mode="json"))
        return
    created = ", ".join(_counted(count, kind) for kind, count in summary.created.items())
    reused = ", ".join(_counted(count, kind) for kind, count in summary.reused.items())
    console.print(safe(f"demo seeded: created {created}; already present {reused}"))
    console.print(
        safe(f"open http://127.0.0.1:{services.settings.port}/ after `agentscope serve`", "dim")
    )
