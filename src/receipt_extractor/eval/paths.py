"""Confinement of paths named in configs and CLI arguments (TRD §S4, path traversal).

Every path a config, import manifest or CLI option names is resolved against the project root
(the working directory, as for :mod:`receipt_extractor.config`) with symlinks followed, and
must land inside one of the allowed top-level directories. ``..``, absolute paths, other
drives and symlinks pointing elsewhere are all rejected the same way.
"""

from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path
from typing import Final

from receipt_extractor.eval.errors import PathConfinementError

DATA_DIR: Final = "data"
RUNS_DIR: Final = "runs"
CONFIGS_DIR: Final = "configs"
REPORTS_DIR: Final = "reports"

CONFIG_ROOTS: Final = (DATA_DIR, RUNS_DIR, CONFIGS_DIR)
"""Directories a run config may point into."""

_MAX_SHOWN_CHARS: Final = 120


def confine(path: str | Path, root: Path, allowed: Iterable[str]) -> Path:
    """Resolve ``path`` against ``root`` and return it if it lies inside an allowed directory.

    Raises :class:`PathConfinementError` otherwise. The returned path is absolute and resolved.
    """
    allowed = tuple(allowed)
    raw = str(path)
    if not raw.strip() or "\x00" in raw:
        raise PathConfinementError("path is empty or contains a NUL character")
    root_resolved = root.resolve()
    candidate = Path(raw)
    if not candidate.is_absolute():
        candidate = root_resolved / candidate
    resolved = candidate.resolve()
    for name in allowed:
        base = (root_resolved / name).resolve()
        if resolved.is_relative_to(base):
            return resolved
    shown = raw if len(raw) <= _MAX_SHOWN_CHARS else raw[: _MAX_SHOWN_CHARS - 1] + "…"
    dirs = ", ".join(f"{name}/" for name in allowed)
    raise PathConfinementError(f"path {shown!r} must stay inside {dirs} of the project")


def relative_posix(path: Path, root: Path) -> str:
    """``path`` relative to ``root`` in POSIX form, so run ids match across operating systems."""
    return path.resolve().relative_to(root.resolve()).as_posix()
