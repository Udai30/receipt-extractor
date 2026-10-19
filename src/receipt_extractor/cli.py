"""Typer command-line interface: ``uv run receipt_extractor --help``.

Command groups are registered here; their commands are added by the tasks that own them
(IMPLEMENTATION_PLAN.md). Keep this module free of import-time side effects so ``--help``
works without a ``.env`` file.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

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
ingest_app = typer.Typer(
    help="Load finished runs into Postgres (database write — run by the human).",
    no_args_is_help=True,
)
report_app = typer.Typer(help="Render leaderboards and failure reports.", no_args_is_help=True)

app.add_typer(data_app, name="data")
app.add_typer(eval_app, name="eval")
app.add_typer(ingest_app, name="ingest")
app.add_typer(report_app, name="report")

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
