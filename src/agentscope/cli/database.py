"""`agentscope db`: migrations, inventory and purge."""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from typing import Annotated

import typer

from agentscope.cli.common import Exit, console, err, fail, open_services
from agentscope.cli.output import emit_json, safe, table

db_app = typer.Typer(help="Database maintenance.")


class Scope(StrEnum):
    ALL = "all"
    RUNS = "runs"
    EVALUATIONS = "evaluations"
    EXPERIMENTS = "experiments"
    DATASETS = "datasets"


@db_app.command("upgrade")
def db_upgrade() -> None:
    """Bring the database schema to the latest revision."""
    from agentscope.config import get_settings
    from agentscope.storage.db import DatabaseAheadError, create_db_engine, upgrade

    settings = get_settings()
    engine = create_db_engine(settings.effective_database_url)
    try:
        revision = upgrade(engine)
    except DatabaseAheadError as exc:
        raise fail(str(exc), Exit.ERROR) from exc
    finally:
        engine.dispose()
    console.print(
        safe(f"database at revision {revision}: {settings.database_location}"), soft_wrap=True
    )


@db_app.command("info")
def db_info(as_json: Annotated[bool, typer.Option("--json")] = False) -> None:
    """Where the data lives (password masked), its schema revision, and what it holds."""
    from agentscope.config import get_settings
    from agentscope.storage.db import create_db_engine, current_revision, head_revision
    from agentscope.storage.repository import Repository

    settings = get_settings()
    engine = create_db_engine(settings.effective_database_url)
    try:
        revision, head = current_revision(engine), head_revision()
        # Only an up-to-date schema can be inventoried; info itself never migrates.
        inventory = Repository(engine).inventory() if revision == head else None
    finally:
        engine.dispose()
    if as_json:
        emit_json(
            {
                "location": settings.database_location,
                "revision": revision,
                "head": head,
                "up_to_date": revision == head,
                "storage": inventory.model_dump(mode="json") if inventory else None,
            }
        )
        return
    console.print(safe(f"database: {settings.database_location}"), soft_wrap=True)
    console.print(safe(f"revision: {revision or 'none'} (latest {head})"))
    if inventory is None:
        console.print(safe("schema is not up to date: run `agentscope db upgrade`", "yellow"))
        return
    size = f"{inventory.size_bytes} bytes" if inventory.size_bytes is not None else "unknown"
    console.print(safe(f"backend: {inventory.backend} · size: {size}"))
    grid = table("table", "rows", "contents", keys=2)
    for item in inventory.tables:
        grid.add_row(safe(item.name), safe(item.rows), safe(item.description))
    console.print(grid)


@db_app.command("purge")
def db_purge(
    scope: Annotated[Scope, typer.Option("--scope", help="What to delete")] = Scope.ALL,
    before: Annotated[
        str | None,
        typer.Option("--before", help="Only rows created before this ISO date/time (UTC if naive)"),
    ] = None,
    yes: Annotated[bool, typer.Option("--yes", help="Confirm the deletion")] = False,
    as_json: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    """Delete stored data and compact the database so it is gone from disk."""
    from agentscope.storage.repository import PurgeIncomplete

    if not yes:
        raise fail("purging deletes data permanently; pass --yes to confirm", Exit.INVALID)
    moment: datetime | None = None
    if before is not None:
        try:
            moment = datetime.fromisoformat(before)
        except ValueError as exc:
            raise fail(f"--before: not an ISO date or date-time: {before}", Exit.INVALID) from exc
        if moment.tzinfo is None:
            moment = moment.replace(tzinfo=UTC)
    services = open_services()
    code = Exit.OK
    try:
        result = services.repository.purge(scope.value, moment)
    except PurgeIncomplete as exc:
        result, code = exc.result, Exit.ERROR
        # stderr: with --json, stdout carries exactly one JSON document.
        err.print(safe(str(exc), "yellow"))
    if as_json:
        emit_json(result.model_dump(mode="json"))
    else:
        deleted = ", ".join(f"{name} {count}" for name, count in result.deleted.items() if count)
        console.print(safe(f"deleted: {deleted or 'nothing'}"))
        console.print(
            safe(f"size: {result.size_bytes_before} -> {result.size_bytes_after} bytes", "dim")
        )
    raise typer.Exit(int(code))
