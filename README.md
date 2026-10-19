# Receipt-to-JSON Extractor

> Photo of a receipt in, validated JSON out — with four pipelines benchmarked on accuracy, latency and cost.

## Results

| Pipeline | Model | Field F1 | Total exact | Valid % | p50 / p95 latency | List cost / 1k |
|---|---|---|---|---|---|---|

_Generated from `reports/` once the benchmark runs exist._

## Quickstart

Requires [uv](https://docs.astral.sh/uv/) and Python 3.12 (uv installs it if missing). Windows PowerShell:

```powershell
uv sync                                   # creates .venv and installs locked dependencies
Copy-Item .env.example .env               # then set POSTGRES_PASSWORD and the same value in DATABASE_URL
docker compose up -d db                   # Postgres 16 + pgvector on 127.0.0.1:5432
uv run alembic upgrade head               # apply migrations
uv run receipt_extractor --help
uv run ruff check . ; uv run mypy src ; uv run pytest -q   # integration tests need Docker running
```

## Architecture

See [TRD.md](TRD.md) §2.

## Evaluation

See [TRD.md](TRD.md) §5.

## Limitations

_To be written after the benchmark._
