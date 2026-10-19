"""Typer command-line interface: ``uv run receipt_extractor --help``.

Command groups are registered here; their commands are added by the tasks that own them
(IMPLEMENTATION_PLAN.md). Keep this module free of import-time side effects so ``--help``
works without a ``.env`` file.
"""

from __future__ import annotations

import typer

from receipt_extractor import __version__

app = typer.Typer(
    name="receipt_extractor",
    help="Receipt-to-JSON Extractor: data preparation, evaluation runs and reports.",
    no_args_is_help=True,
    add_completion=False,
    pretty_exceptions_show_locals=False,  # locals may hold secrets
)

data_app = typer.Typer(help="Download and inspect the CORD dataset.", no_args_is_help=True)
eval_app = typer.Typer(help="Run and import evaluation runs.", no_args_is_help=True)
ingest_app = typer.Typer(
    help="Load finished runs into Postgres (database write — run by the human).",
    no_args_is_help=True,
)
report_app = typer.Typer(help="Render leaderboards and failure reports.", no_args_is_help=True)

app.add_typer(data_app, name="data")
app.add_typer(eval_app, name="eval")
app.add_typer(ingest_app, name="ingest")
app.add_typer(report_app, name="report")


def _version_callback(value: bool) -> None:
    if value:
        typer.echo(__version__)
        raise typer.Exit


@app.callback()
def _root(
    version: bool = typer.Option(
        False,
        "--version",
        callback=_version_callback,
        is_eager=True,
        help="Show the version and exit.",
    ),
) -> None:
    """Receipt-to-JSON Extractor."""


def main() -> None:
    """Console-script entry point."""
    app()
