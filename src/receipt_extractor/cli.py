"""Typer command-line interface: ``uv run receipt_extractor --help``.

Command groups are registered here; their commands are added by the tasks that own them
(IMPLEMENTATION_PLAN.md). Keep this module free of import-time side effects so ``--help``
works without a ``.env`` file.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Annotated, Any

import typer

from receipt_extractor import __version__

if TYPE_CHECKING:
    from receipt_extractor.llm.providers import Provider

app = typer.Typer(
    name="receipt_extractor",
    help="Receipt-to-JSON Extractor: data preparation, evaluation runs and reports.",
    no_args_is_help=True,
    add_completion=False,
    pretty_exceptions_show_locals=False,  # locals may hold secrets
)

data_app = typer.Typer(help="Download and inspect the CORD dataset.", no_args_is_help=True)
eval_app = typer.Typer(help="Run and import evaluation runs.", no_args_is_help=True)
report_app = typer.Typer(help="Render leaderboards and failure reports.", no_args_is_help=True)

app.add_typer(data_app, name="data")
app.add_typer(eval_app, name="eval")
app.add_typer(report_app, name="report")
ui_app = typer.Typer(help="Demo app assets (Gradio Space).", no_args_is_help=True)
app.add_typer(ui_app, name="ui")

llm_app = typer.Typer(
    help="Call configured models through the LLM provider layer.", no_args_is_help=True
)
app.add_typer(llm_app, name="llm")

PING_PROMPT = "Reply with exactly one word: pong"
PING_MAX_TOKENS = 512  # reasoning models spend tokens before answering
_REPLY_PREVIEW_CHARS = 80


def _provider_factory() -> Provider | None:
    """Provider for CLI commands; ``None`` selects LiteLLM. Tests replace this."""
    return None


@llm_app.command("ping")
def llm_ping(
    model: str = typer.Option(..., "--model", "-m", help="Alias from configs/models.yaml."),
) -> None:
    """Send one tiny request to MODEL and print tokens, latency and cost (no cache)."""
    import asyncio  # noqa: PLC0415 - keep `--help` free of the LLM stack

    from receipt_extractor.config import ConfigError, get_settings  # noqa: PLC0415
    from receipt_extractor.llm import (  # noqa: PLC0415
        EmbedRequest,
        LLMError,
        LLMRequest,
        Message,
        build_client,
    )

    try:
        settings = get_settings()
        client = build_client(settings, cache=False, provider=_provider_factory())
        spec = client.registry.get(model)
        if spec.kind == "embedding":
            embedded = asyncio.run(
                client.embed(
                    EmbedRequest(model_alias=model, inputs=("pong",), data_origin="synthetic")
                )
            )
            tokens_out: int | None = None
            reply = f"{len(embedded.vectors[0])}-dim vector"
            response: Any = embedded
        else:
            response = asyncio.run(
                client.complete(
                    LLMRequest(
                        model_alias=model,
                        messages=(Message(role="user", content=PING_PROMPT),),
                        max_tokens=PING_MAX_TOKENS,
                        data_origin="synthetic",
                    )
                )
            )
            tokens_out = response.tokens_out
            reply = " ".join(response.text.split())[:_REPLY_PREVIEW_CHARS] or "(empty)"
    except (ConfigError, LLMError) as exc:
        typer.echo(f"error: {exc}", err=True)
        raise typer.Exit(code=1) from None

    cost = "n/a" if response.list_cost_usd is None else f"${response.list_cost_usd}"
    rows = [
        ("alias", response.model_alias),
        ("model", response.model),
        ("tokens", f"in={response.tokens_in} out={tokens_out}"),
        ("latency", f"{response.latency_ms} ms"),
        ("list cost", cost),
        ("actual cost", f"${response.actual_cost_usd}"),
        ("retries", str(response.retries)),
        ("reply", reply),
    ]
    for label, value in rows:
        typer.echo(f"{label:<12} {value}")


_SPLITS = ("train", "validation", "test")
_SHOWN_FORMATS = 15


@data_app.command("prepare")
def data_prepare(
    split: Annotated[
        list[str] | None,
        typer.Option(
            "--split",
            help="Split to prepare (repeatable): train, validation, test. "
            "Default: validation and test (train is ~1.8 GB).",
        ),
    ] = None,
) -> None:
    """Download CORD-v2 (pinned revision) into data/cord/ and write gold Receipt JSON."""
    from receipt_extractor.config import ConfigError, get_settings  # noqa: PLC0415
    from receipt_extractor.data.cord import (  # noqa: PLC0415
        DEFAULT_SPLITS,
        hf_downloader,
        prepare_cord,
    )
    from receipt_extractor.eval.errors import EvalError  # noqa: PLC0415

    chosen = split or list(DEFAULT_SPLITS)
    unknown = [name for name in chosen if name not in _SPLITS]
    if unknown:
        typer.echo(f"error: unknown split(s): {', '.join(unknown)}", err=True)
        raise typer.Exit(code=1)
    try:
        token = get_settings().hf_token
        result = prepare_cord(
            Path.cwd(),
            splits=chosen,  # type: ignore[arg-type]  # checked against _SPLITS above
            downloader=hf_downloader(token.get_secret_value() if token else None),
            progress=typer.echo,
        )
    except (ConfigError, EvalError) as exc:
        typer.echo(f"error: {exc}", err=True)
        raise typer.Exit(code=1) from None
    typer.echo(f"wrote {result.manifest_path.relative_to(Path.cwd().resolve()).as_posix()}")


@data_app.command("stats")
def data_stats(
    split: str = typer.Option("test", "--split", help="Prepared split to describe."),
) -> None:
    """Items per receipt, amount formats seen and unmappable receipts of a prepared split."""
    from receipt_extractor.data.cord import CORD_DIR  # noqa: PLC0415
    from receipt_extractor.data.stats import split_stats  # noqa: PLC0415
    from receipt_extractor.eval.errors import EvalError  # noqa: PLC0415
    from receipt_extractor.eval.paths import DATA_DIR, confine  # noqa: PLC0415

    try:
        stats = split_stats(confine(CORD_DIR, Path.cwd(), [DATA_DIR]), split)
    except EvalError as exc:
        typer.echo(f"error: {exc}", err=True)
        raise typer.Exit(code=1) from None
    rows = [
        ("split", f"{stats.split} @ {stats.revision[:12]} ({stats.license})"),
        (
            "receipts",
            f"{stats.n_rows} rows, {stats.n_gold} gold, {len(stats.unmappable)} unmappable",
        ),
        (
            "items/rcpt",
            f"min {stats.items_min}, median {stats.items_median:g}, "
            f"mean {stats.items_mean:.2f}, max {stats.items_max}",
        ),
        ("histogram", ", ".join(f"{k}: {v}" for k, v in stats.items_histogram.items())),
        ("subtotal", f"{stats.n_with_subtotal} of {stats.n_gold} receipts"),
        ("tax", f"{stats.n_with_tax} of {stats.n_gold} receipts"),
        ("items sum", f"= subtotal or total on {stats.n_reconciled} of {stats.n_gold} receipts"),
    ]
    for label, text in rows:
        typer.echo(f"{label:<12} {text}")
    typer.echo(f"amount formats seen ({len(stats.amount_formats)} distinct, most common first):")
    for label, count in list(stats.amount_formats.items())[:_SHOWN_FORMATS]:
        typer.echo(f"  {count:>5}  {label}")
    if len(stats.amount_formats) > _SHOWN_FORMATS:
        typer.echo(f"  ... {len(stats.amount_formats) - _SHOWN_FORMATS} more")
    if stats.mapping_notes:
        typer.echo("mapping notes:")
        for note, count in stats.mapping_notes.items():
            typer.echo(f"  {count:>5}  {note}")
    if stats.unmappable:
        typer.echo("unmappable receipts:")
        for item in stats.unmappable:
            typer.echo(f"  {item['id']}: {item['reason']}")


EXIT_PAUSED = 3
EXIT_INTERRUPTED = 130


def _echo_outcome(outcome: Any) -> None:
    """Print a :class:`~receipt_extractor.eval.runner.RunOutcome` as aligned rows."""
    rows = [
        ("run id", outcome.run_id),
        ("status", outcome.status),
        (
            "cases",
            f"{outcome.previously_done + outcome.executed}/{outcome.total} done "
            f"({outcome.executed} this time, {outcome.errors} errors)",
        ),
        ("run dir", outcome.run_dir.as_posix()),
    ]
    summary = outcome.summary
    if summary is not None:
        primary = summary.metrics[summary.primary_metric]
        value = "n/a" if primary.value is None else f"{primary.value:.4f}"
        if primary.ci_low is not None and primary.ci_high is not None:
            value += f" [{primary.ci_low:.4f}, {primary.ci_high:.4f}]"
        rows.append((summary.primary_metric, value))
    for label, text in rows:
        typer.echo(f"{label:<12} {text}")


@eval_app.command("run")
def eval_run(
    config: str = typer.Option(
        ..., "--config", "-c", help="Run config under configs/ (e.g. configs/runs/toy.yaml)."
    ),
    concurrency: int | None = typer.Option(
        None, "--concurrency", min=1, max=64, help="Override the config's concurrency."
    ),
) -> None:
    """Run (or resume) an evaluation; results stream to runs/<run_id>/results.jsonl."""
    import asyncio  # noqa: PLC0415 - keep `--help` light

    from receipt_extractor.eval.errors import EvalError  # noqa: PLC0415
    from receipt_extractor.eval.runner import execute_run, prepare_run  # noqa: PLC0415
    from receipt_extractor.eval.tasks import build_task  # noqa: PLC0415

    try:
        prepared = prepare_run(config, Path.cwd())
        task = build_task(prepared.config.task, prepared.config.params)
        outcome = asyncio.run(execute_run(prepared, task, concurrency=concurrency))
    except EvalError as exc:
        typer.echo(f"error: {exc}", err=True)
        raise typer.Exit(code=1) from None
    except KeyboardInterrupt:
        typer.echo(
            "interrupted: finished cases are saved; re-run the same command to resume", err=True
        )
        raise typer.Exit(code=EXIT_INTERRUPTED) from None
    _echo_outcome(outcome)
    if outcome.status == "paused":
        typer.echo(f"paused: {outcome.paused_reason}", err=True)
        raise typer.Exit(code=EXIT_PAUSED)


@eval_app.command("import-predictions")
def eval_import_predictions(
    directory: str = typer.Argument(
        ..., help="Kaggle output directory under data/ (manifest.json + predictions.jsonl)."
    ),
) -> None:
    """Turn Kaggle notebook outputs into a normal, scored run directory."""
    import asyncio  # noqa: PLC0415

    from receipt_extractor.eval.errors import EvalError  # noqa: PLC0415
    from receipt_extractor.eval.importer import import_predictions  # noqa: PLC0415

    try:
        outcome = asyncio.run(import_predictions(directory, Path.cwd()))
    except EvalError as exc:
        typer.echo(f"error: {exc}", err=True)
        raise typer.Exit(code=1) from None
    _echo_outcome(outcome)


def _report_dirs(runs_dir: str, out_dir: str) -> tuple[Path, Path]:
    from receipt_extractor.eval.paths import REPORTS_DIR, RUNS_DIR, confine  # noqa: PLC0415

    root = Path.cwd()
    return confine(runs_dir, root, [RUNS_DIR]), confine(out_dir, root, [REPORTS_DIR])


@report_app.command("build")
def report_build(
    runs_dir: str = typer.Option("runs", "--runs-dir", help="Directory of run dirs."),
    out_dir: str = typer.Option("reports", "--out", help="Report directory."),
) -> None:
    """Rebuild reports/results.csv, results.md and charts from all completed runs."""
    from receipt_extractor.eval.errors import EvalError  # noqa: PLC0415
    from receipt_extractor.eval.report import build_reports  # noqa: PLC0415

    try:
        runs, out = _report_dirs(runs_dir, out_dir)
        result = build_reports(runs, out)
    except EvalError as exc:
        typer.echo(f"error: {exc}", err=True)
        raise typer.Exit(code=1) from None
    typer.echo(f"{result.rows} run(s) reported")
    for path in result.files:
        typer.echo(f"wrote {path.relative_to(Path.cwd().resolve()).as_posix()}")


@report_app.command("failures")
def report_failures(
    runs_dir: str = typer.Option("runs", "--runs-dir", help="Directory of run dirs."),
    out_dir: str = typer.Option("reports", "--out", help="Report directory."),
    run_id: Annotated[
        list[str] | None,
        typer.Option(
            "--run-id", help="Export these runs instead of the reported ones (repeatable)."
        ),
    ] = None,
) -> None:
    """Export fail/error cases to reports/failures.csv, keeping existing hand labels."""
    from receipt_extractor.eval.errors import EvalError  # noqa: PLC0415
    from receipt_extractor.eval.report import export_failures  # noqa: PLC0415

    try:
        runs, out = _report_dirs(runs_dir, out_dir)
        path, count = export_failures(runs, out, run_ids=run_id or ())
    except EvalError as exc:
        typer.echo(f"error: {exc}", err=True)
        raise typer.Exit(code=1) from None
    typer.echo(
        f"{count} failure(s) exported to {path.relative_to(Path.cwd().resolve()).as_posix()}"
    )


@ui_app.command("build-showcase")
def ui_build_showcase(
    case_id: Annotated[
        list[str] | None,
        typer.Option("--case-id", help="Validation receipt to include (repeatable)."),
    ] = None,
) -> None:
    """Bundle sample receipts with their stored A/B results for the demo's Try it tab."""
    from receipt_extractor.ui.showcase import (  # noqa: PLC0415
        DEFAULT_SAMPLE_IDS,
        SHOWCASE_DIR,
        build_showcase,
    )

    try:
        showcase = build_showcase(Path.cwd(), case_ids=case_id or DEFAULT_SAMPLE_IDS)
    except (OSError, ValueError, KeyError) as exc:
        typer.echo(f"error: {exc}", err=True)
        raise typer.Exit(code=1) from None
    for case in showcase.cases:
        runs = ", ".join(f"{v}={r.run_id}" for v, r in case.variants.items())
        typer.echo(f"{case.case_id}: {runs}")
    typer.echo(f"wrote {len(showcase.cases)} sample(s) to {SHOWCASE_DIR}")


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
