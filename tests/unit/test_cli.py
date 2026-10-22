"""CLI smoke tests: help output works without any configuration."""

from __future__ import annotations

import pytest
from typer.testing import CliRunner

from receipt_extractor import __version__
from receipt_extractor.cli import app

runner = CliRunner()


def test_help_lists_command_groups(clean_env: pytest.MonkeyPatch) -> None:
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0, result.output
    for group in ("data", "eval", "report", "llm"):
        assert group in result.output


@pytest.mark.parametrize("group", ["data", "eval", "report", "llm"])
def test_each_group_has_help(clean_env: pytest.MonkeyPatch, group: str) -> None:
    result = runner.invoke(app, [group, "--help"])
    assert result.exit_code == 0, result.output


def test_version(clean_env: pytest.MonkeyPatch) -> None:
    result = runner.invoke(app, ["--version"])
    assert result.exit_code == 0
    assert result.output.strip() == __version__


def test_ingest_group_is_gone(clean_env: pytest.MonkeyPatch) -> None:
    result = runner.invoke(app, ["ingest", "--help"])
    assert result.exit_code != 0
