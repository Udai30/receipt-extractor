"""v2 has no database (BACKEND_SCHEMA §S5): the package must import without any DB driver."""

from __future__ import annotations

import subprocess
import sys

# Runs in a fresh interpreter so already-imported modules cannot hide a dependency. A meta
# path finder makes every database package unimportable, then every package module is imported.
_SCRIPT = """
import importlib, pkgutil, sys

BLOCKED = {"sqlalchemy", "asyncpg", "alembic", "psycopg", "psycopg2", "testcontainers"}

class Block:
    def find_spec(self, name, path=None, target=None):
        if name.split(".")[0] in BLOCKED:
            raise ModuleNotFoundError(f"blocked: {name}")
        return None

sys.meta_path.insert(0, Block())
import receipt_extractor
for info in pkgutil.walk_packages(receipt_extractor.__path__, "receipt_extractor."):
    importlib.import_module(info.name)
print("ok")
"""


def test_package_imports_without_database_libraries() -> None:
    result = subprocess.run(  # noqa: S603 - fixed script, current interpreter
        [sys.executable, "-c", _SCRIPT], capture_output=True, text=True, check=False
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "ok"
