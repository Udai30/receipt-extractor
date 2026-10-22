# Receipt-to-JSON Extractor

> A receipt photo goes in; validated JSON (line items, prices, totals) comes out — OCR+LLM vs a vision LLM, compared on accuracy, latency and cost.

## Results

| Pipeline | Model | Field F1 | Total exact | Valid % | p50 / p95 latency | List cost / 1k |
|---|---|---|---|---|---|---|

_Generated from `reports/` once the benchmark runs exist._

## Quickstart

Requires [uv](https://docs.astral.sh/uv/) and Python 3.12 (uv installs it if missing). Windows PowerShell:

```powershell
uv sync                                   # creates .venv and installs locked dependencies
Copy-Item .env.example .env               # then set GROQ_API_KEY / GEMINI_API_KEY
uv run receipt_extractor --help
uv run receipt_extractor llm ping --model groq-oss-20b
uv run ruff check . ; uv run mypy src ; uv run pytest -q
```

No database or Docker is needed: results are files under `runs/` and `reports/`.

## Architecture

See [TRD.md](TRD.md) §2.

## Evaluation

See [TRD.md](TRD.md) §5.

## Limitations

_To be written after the benchmark._
