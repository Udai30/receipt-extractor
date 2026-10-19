"""SHA-256 keyed disk cache for model responses (TRD §S3).

Layout: ``<root>/<key[:2]>/<key>.json`` — a lookup is a single path computation and one file
read (O(1) by key path). Writes are atomic (temp file + ``os.replace``), so a crash or a
concurrent writer never leaves a half-written entry. Corrupt or oversized entries are treated
as misses. Keys are validated as 64 hex characters, so they can never escape ``root``.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import re
import tempfile
from collections.abc import Mapping
from pathlib import Path
from typing import Any, Final

logger = logging.getLogger(__name__)

CACHE_FORMAT_VERSION: Final = 1
MAX_ENTRY_BYTES: Final = 8 * 1024 * 1024
_KEY_RE: Final = re.compile(r"^[0-9a-f]{64}$")


def cache_key(payload: Mapping[str, Any]) -> str:
    """SHA-256 of the canonical JSON of ``payload`` (sorted keys, no whitespace)."""
    canonical = json.dumps(
        {"format": CACHE_FORMAT_VERSION, **payload},
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        default=str,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


class DiskCache:
    """JSON-object cache on the local filesystem."""

    def __init__(self, root: Path, *, max_entry_bytes: int = MAX_ENTRY_BYTES) -> None:
        self.root = root
        self.max_entry_bytes = max_entry_bytes

    def path_for(self, key: str) -> Path:
        if not _KEY_RE.fullmatch(key):
            raise ValueError("cache key must be 64 lowercase hex characters")
        return self.root / key[:2] / f"{key}.json"

    async def get(self, key: str) -> dict[str, Any] | None:
        """Return the stored object for ``key`` or ``None`` on a miss / unreadable entry."""
        return await asyncio.to_thread(self._read, self.path_for(key))

    async def set(self, key: str, value: Mapping[str, Any]) -> None:
        """Store ``value`` under ``key`` atomically (last writer wins; entries are idempotent)."""
        data = json.dumps(value, ensure_ascii=False, separators=(",", ":"), default=str)
        encoded = data.encode("utf-8")
        if len(encoded) > self.max_entry_bytes:
            logger.warning("llm cache entry not stored: %d bytes exceeds limit", len(encoded))
            return
        await asyncio.to_thread(self._write, self.path_for(key), encoded)

    def _read(self, path: Path) -> dict[str, Any] | None:
        try:
            if path.stat().st_size > self.max_entry_bytes:
                logger.warning("llm cache entry ignored (too large): %s", path.name)
                return None
            value = json.loads(path.read_bytes())
        except FileNotFoundError:
            return None
        except (OSError, ValueError):
            logger.warning("llm cache entry unreadable, treated as miss: %s", path.name)
            return None
        return value if isinstance(value, dict) else None

    @staticmethod
    def _write(path: Path, data: bytes) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".tmp-", suffix=".json")
        tmp_path = Path(tmp)
        try:
            with os.fdopen(fd, "wb") as handle:
                handle.write(data)
            tmp_path.replace(path)
        except BaseException:
            tmp_path.unlink(missing_ok=True)
            raise
