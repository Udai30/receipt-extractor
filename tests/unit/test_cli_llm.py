"""`receipt_extractor llm ping` against FakeProvider (acceptance-criterion output, offline)."""

from __future__ import annotations

from pathlib import Path

import pytest
from typer.testing import CliRunner

from receipt_extractor import cli
from receipt_extractor.llm.fake import FakeFailure, FakeProvider, FakeReply

PROJECT_ROOT = Path(__file__).resolve().parents[2]
GROQ_TEST_KEY = "gsk_TESTKEYtestkey0123456789"  # fake value

runner = CliRunner()


@pytest.fixture
def llm_env(clean_env: pytest.MonkeyPatch, tmp_path: Path) -> pytest.MonkeyPatch:
    clean_env.setenv("DATABASE_URL", "postgresql+asyncpg://u:p@127.0.0.1:5432/db")
    clean_env.setenv("LLM_MODELS_PATH", str(PROJECT_ROOT / "configs" / "models.yaml"))
    clean_env.setenv("LLM_PRICING_PATH", str(PROJECT_ROOT / "configs" / "pricing.yaml"))
    clean_env.setenv("LLM_BUDGET_PATH", str(tmp_path / "budget.json"))
    clean_env.setenv("LLM_CACHE_DIR", str(tmp_path / "cache"))
    return clean_env


def use_provider(monkeypatch: pytest.MonkeyPatch, provider: FakeProvider) -> None:
    monkeypatch.setattr(cli, "_provider_factory", lambda: provider)


def test_ping_prints_tokens_latency_and_cost(llm_env: pytest.MonkeyPatch) -> None:
    llm_env.setenv("GROQ_API_KEY", GROQ_TEST_KEY)
    provider = FakeProvider([FakeReply(text="pong", tokens_in=80, tokens_out=20)])
    use_provider(llm_env, provider)

    result = runner.invoke(cli.app, ["llm", "ping", "--model", "groq-oss-20b"])

    assert result.exit_code == 0, result.output
    lines = dict(line.split(maxsplit=1) for line in result.output.splitlines() if line.strip())
    assert lines["model"] == "groq/openai/gpt-oss-20b"
    assert lines["tokens"] == "in=80 out=20"
    assert lines["latency"].endswith(" ms")
    assert "cost" in result.output
    # 80 * $0.075/M + 20 * $0.30/M = $0.000012
    assert "$0.00001200" in result.output
    assert lines["reply"] == "pong"
    assert provider.calls[0].model == "groq/openai/gpt-oss-20b"
    assert GROQ_TEST_KEY not in result.output
    assert (Path.cwd() / ".cache").exists() is False  # ping never writes the response cache


def test_ping_embedding_alias(llm_env: pytest.MonkeyPatch) -> None:
    use_provider(llm_env, FakeProvider(embed_dim=16))
    result = runner.invoke(cli.app, ["llm", "ping", "-m", "ollama-embed"])
    assert result.exit_code == 0, result.output
    assert "16-dim vector" in result.output


def test_ping_without_key_fails_cleanly(llm_env: pytest.MonkeyPatch) -> None:
    provider = FakeProvider()
    use_provider(llm_env, provider)
    result = runner.invoke(cli.app, ["llm", "ping", "--model", "groq-oss-20b"])
    assert result.exit_code == 1
    assert "GROQ_API_KEY is not set" in result.output
    assert provider.calls == []


def test_ping_unknown_alias_and_provider_error(llm_env: pytest.MonkeyPatch) -> None:
    llm_env.setenv("GROQ_API_KEY", GROQ_TEST_KEY)
    use_provider(llm_env, FakeProvider([FakeFailure("bad_request")]))
    unknown = runner.invoke(cli.app, ["llm", "ping", "--model", "gpt-5"])
    assert unknown.exit_code == 1 and "unknown model alias" in unknown.output
    failed = runner.invoke(cli.app, ["llm", "ping", "--model", "groq-oss-20b"])
    assert failed.exit_code == 1 and "400 Bad Request" in failed.output
    assert "Traceback" not in failed.output


def test_ping_gemini_uses_synthetic_origin(llm_env: pytest.MonkeyPatch) -> None:
    llm_env.setenv("GEMINI_API_KEY", "AIzaTESTtestTESTtest0123456789")
    provider = FakeProvider([FakeReply(text="pong")])
    use_provider(llm_env, provider)
    result = runner.invoke(cli.app, ["llm", "ping", "--model", "gemini-flash"])
    assert result.exit_code == 0, result.output
    assert provider.calls[0].model == "gemini/gemini-3.8-flash"


def test_ping_missing_database_url_reports_config_error(clean_env: pytest.MonkeyPatch) -> None:
    result = runner.invoke(cli.app, ["llm", "ping", "--model", "groq-oss-20b"])
    assert result.exit_code == 1
    assert "DATABASE_URL" in result.output
