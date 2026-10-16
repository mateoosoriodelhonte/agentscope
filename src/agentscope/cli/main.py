"""AgentScope command-line interface."""

from __future__ import annotations

import typer

from agentscope import __version__

app = typer.Typer(
    name="agentscope",
    help="Understand, test, and improve AI agents.",
    no_args_is_help=True,
    add_completion=False,
)


@app.callback()
def _root() -> None:
    """AgentScope command-line interface."""


@app.command()
def version() -> None:
    """Print the installed AgentScope version."""
    typer.echo(__version__)


def main() -> None:
    app()
