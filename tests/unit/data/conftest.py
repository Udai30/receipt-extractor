"""Fixtures for the dataset preparation tests: an isolated project root."""

from __future__ import annotations

from pathlib import Path

import pytest


@pytest.fixture
def project(clean_env: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    """The project root for this test (also the working directory)."""
    return tmp_path.resolve()
