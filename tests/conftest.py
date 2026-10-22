"""Shared pytest fixtures."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest

from receipt_extractor.config import Settings, get_settings


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    """Live tests spend free-tier quota and need the network: run them only when selected
    explicitly with ``-m live`` (they still skip individually when a key is missing)."""
    if "live" in (config.getoption("markexpr", "") or ""):
        return
    skip = pytest.mark.skip(reason="live provider test; run with `uv run pytest -m live`")
    for item in items:
        if item.get_closest_marker("live"):
            item.add_marker(skip)


@pytest.fixture
def clean_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Iterator[pytest.MonkeyPatch]:
    """Isolate a test from the developer's real environment and ``.env`` file.

    Removes every variable ``Settings`` reads, runs from an empty temp directory (so no
    ``.env`` is found) and clears the cached settings before and after.
    """
    for name in Settings.model_fields:
        monkeypatch.delenv(name.upper(), raising=False)
    monkeypatch.chdir(tmp_path)
    get_settings.cache_clear()
    yield monkeypatch
    get_settings.cache_clear()
