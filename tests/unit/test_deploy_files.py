"""Static security checks on docker-compose.yml and the Dockerfile (no Docker needed)."""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
COMPOSE = (ROOT / "docker-compose.yml").read_text(encoding="utf-8")
DOCKERFILE = (ROOT / "Dockerfile").read_text(encoding="utf-8")
DOCKERIGNORE = (ROOT / ".dockerignore").read_text(encoding="utf-8").splitlines()


def test_published_ports_bind_to_loopback_only() -> None:
    mappings = re.findall(r'^\s*-\s*"([^"]*:\d+)"\s*$', COMPOSE, flags=re.MULTILINE)
    assert mappings, "expected published ports in docker-compose.yml"
    for mapping in mappings:
        assert mapping.startswith("127.0.0.1:"), mapping


def test_db_uses_pgvector_image_volume_and_healthcheck() -> None:
    assert "image: pgvector/pgvector:pg16" in COMPOSE
    assert "pgdata:/var/lib/postgresql/data" in COMPOSE
    assert "pg_isready" in COMPOSE
    assert "condition: service_healthy" in COMPOSE


def test_no_default_database_password() -> None:
    # Required with :? (no fallback), never a literal value.
    assert "POSTGRES_PASSWORD: ${POSTGRES_PASSWORD:?" in COMPOSE
    assert not re.search(r"POSTGRES_PASSWORD:-", COMPOSE)


def test_image_runs_as_non_root_without_dev_dependencies() -> None:
    final_stage = DOCKERFILE.rsplit("\nFROM ", 1)[1]
    users = re.findall(r"^USER\s+(\S+)", final_stage, flags=re.MULTILINE)
    assert users
    assert users[-1].split(":")[0] not in {"root", "0"}
    assert DOCKERFILE.count("--no-dev") >= 2
    assert "--locked" in DOCKERFILE
    assert "/uv" not in final_stage  # uv itself stays in the builder


def test_build_context_excludes_secrets_and_local_state() -> None:
    for entry in (".env", ".env.*", ".venv", "data", "runs", ".git"):
        assert entry in DOCKERIGNORE
