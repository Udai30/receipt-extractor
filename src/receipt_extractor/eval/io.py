"""File primitives of the run directory: JSONL streaming, atomic writes, crash repair, run lock.

Results are appended one line per case and flushed immediately, so a crash loses at most the
line being written; :func:`repair_tail` cuts that partial line before a resume appends again.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import sys
import tempfile
from collections.abc import Iterator
from pathlib import Path
from types import TracebackType
from typing import IO, Any, Final, Self

from receipt_extractor.eval.errors import RunCorruptError, RunLockedError

MAX_LINE_BYTES: Final = 16 * 1024 * 1024
MAX_JSON_FILE_BYTES: Final = 16 * 1024 * 1024
_CHUNK: Final = 64 * 1024


def sha256_file(path: Path) -> str:
    """SHA-256 of a file, read in chunks (constant memory)."""
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(_CHUNK):
            digest.update(chunk)
    return digest.hexdigest()


def canonical_json(value: Any) -> str:
    """Deterministic JSON (sorted keys, no whitespace) used for hashing."""
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str)


def atomic_write_text(path: Path, text: str) -> None:
    """Write ``text`` to ``path`` via a temp file + rename, so readers never see half a file."""
    atomic_write_bytes(path, text.encode("utf-8"))


def atomic_write_bytes(path: Path, data: bytes) -> None:
    """Write ``data`` to ``path`` via a temp file + rename, so readers never see half a file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".tmp-", suffix=path.suffix)
    tmp_path = Path(tmp)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
        tmp_path.replace(path)
    except BaseException:
        tmp_path.unlink(missing_ok=True)
        raise


def read_json_object(path: Path) -> dict[str, Any]:
    """Parse a JSON object file of bounded size, raising :class:`RunCorruptError` on problems."""
    try:
        if path.stat().st_size > MAX_JSON_FILE_BYTES:
            raise RunCorruptError(f"{path.name} is larger than {MAX_JSON_FILE_BYTES} bytes")
        value = json.loads(path.read_bytes())
    except (OSError, ValueError) as exc:
        raise RunCorruptError(
            f"{path.name} cannot be read as JSON ({type(exc).__name__})"
        ) from None
    if not isinstance(value, dict):
        raise RunCorruptError(f"{path.name} must contain a JSON object")
    return value


def iter_jsonl(path: Path) -> Iterator[tuple[int, dict[str, Any]]]:
    """Yield ``(line_number, object)`` for each non-blank line, streaming the file.

    Lines over :data:`MAX_LINE_BYTES` or lines that are not JSON objects raise
    :class:`RunCorruptError` naming the file and line.
    """
    with path.open("rb") as handle:
        lineno = 0
        while True:
            line = handle.readline(MAX_LINE_BYTES + 1)
            if not line:
                return
            lineno += 1
            if len(line) > MAX_LINE_BYTES:
                raise RunCorruptError(f"{path.name}:{lineno}: line exceeds {MAX_LINE_BYTES} bytes")
            if not line.strip():
                continue
            try:
                value = json.loads(line)
            except ValueError:
                raise RunCorruptError(f"{path.name}:{lineno}: not valid JSON") from None
            if not isinstance(value, dict):
                raise RunCorruptError(f"{path.name}:{lineno}: expected a JSON object")
            yield lineno, value


def repair_tail(path: Path) -> bool:
    """Truncate a trailing partial line left by a crash. Returns whether anything was cut."""
    if not path.is_file():
        return False
    with path.open("r+b") as handle:
        size = handle.seek(0, os.SEEK_END)
        if size == 0:
            return False
        handle.seek(size - 1)
        if handle.read(1) == b"\n":
            return False
        # Walk back in chunks to the last newline; everything after it is the partial line.
        end = size
        keep = 0
        while end > 0:
            start = max(0, end - _CHUNK)
            handle.seek(start)
            chunk = handle.read(end - start)
            index = chunk.rfind(b"\n")
            if index != -1:
                keep = start + index + 1
                break
            end = start
        handle.truncate(keep)
    return True


class JSONLAppender:
    """Appends whole lines to one file from concurrent tasks; each line is flushed at once."""

    def __init__(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self._handle: IO[str] = path.open("a", encoding="utf-8", newline="\n")
        self._lock = asyncio.Lock()

    async def write_line(self, line: str) -> None:
        if "\n" in line:
            raise ValueError("a JSONL record must not contain a newline")
        async with self._lock:
            await asyncio.to_thread(self._write, line + "\n")

    def _write(self, text: str) -> None:
        self._handle.write(text)
        self._handle.flush()

    def close(self) -> None:
        self._handle.close()


class RunLock:
    """Exclusive, non-blocking OS file lock on ``path`` held for the life of a run.

    The OS releases it when the process exits, so a crashed run never leaves a stale lock.
    """

    def __init__(self, path: Path) -> None:
        self.path = path
        self._handle: IO[bytes] | None = None

    def __enter__(self) -> Self:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        handle = self.path.open("a+b")
        try:
            _lock_file(handle)
        except OSError:
            handle.close()
            raise RunLockedError(
                f"run directory {self.path.parent.name} is in use by another process"
            ) from None
        self._handle = handle
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        if self._handle is not None:
            try:
                _unlock_file(self._handle)
            finally:
                self._handle.close()
                self._handle = None


def _lock_file(handle: IO[bytes]) -> None:
    if sys.platform == "win32":
        import msvcrt  # noqa: PLC0415 - platform-specific

        handle.seek(0)
        msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
    else:
        import fcntl  # noqa: PLC0415 - platform-specific

        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)


def _unlock_file(handle: IO[bytes]) -> None:
    if sys.platform == "win32":
        import msvcrt  # noqa: PLC0415 - platform-specific

        handle.seek(0)
        msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
    else:
        import fcntl  # noqa: PLC0415 - platform-specific

        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
