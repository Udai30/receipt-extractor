# Worklog — Receipt-to-JSON Extractor

> Photo of a receipt in, validated JSON out — with four pipelines benchmarked on accuracy, latency and cost.

Project documentation: [PRD.md](PRD.md) · [TRD.md](TRD.md) · [APPFLOW.md](APPFLOW.md) · [UI_UX_DESIGN.md](UI_UX_DESIGN.md) · [BACKEND_SCHEMA.md](BACKEND_SCHEMA.md) · [IMPLEMENTATION_PLAN.md](IMPLEMENTATION_PLAN.md) · **WORKLOG.md** · [IMPLEMENTATION_PROMPTS.md](IMPLEMENTATION_PROMPTS.md) · [CLAUDE.md](CLAUDE.md) · [PROMPT_PLAYBOOK.md](PROMPT_PLAYBOOK.md)

| Field | Value |
|---|---|
| Project | P01 · `01-receipt-extractor` |
| Target role signal | AI/GenAI Engineer · document AI · structured outputs · evaluation |
| Difficulty / effort | ≈ 8 focused days (Phase 1) |
| Frontend | Gradio 5 (Python) mounted on the FastAPI app |
| Backend | FastAPI + PostgreSQL 16 (pgvector) via Docker Compose |
| Status | Specification v1 · 2026-10-19 |

---

Format: **newest first**, one entry per task session (see CLAUDE.md §7).

```text
## YYYY-MM-DD — Task ID · Short title

**Done:** what was actually completed (incl. tests run + results, verification evidence)
**Files:** paths touched
**Decisions:** anything decided, and why
**Open:** anything unresolved — incl. "Human action:" items (migrations, ingest, accounts, secrets)
```

## Current state

| Item | Value |
|---|---|
| Current task | M0.1 — done · next: M0.2 |
| Last verified gate run | 2026-10-19 — ruff, ruff format, mypy --strict, pytest (15 passed), pip-audit: all green |
| Pending human actions | see the entries below |

---

## 2026-10-19 — M0.1 · Repository scaffold and quality tooling

**Done:**
- Installed `uv` 0.13.1 (was missing; required by CLAUDE.md §3) — the only global install, via `winget install --id astral-sh.uv --scope user`. Remove with `winget uninstall astral-sh.uv`.
- `uv init --package --vcs none` src-layout package `receipt_extractor`, Python 3.12 (`.python-version`, `requires-python = ">=3.12,<3.13"`). Project venv at `.venv/` (CPython 3.12.5); `uv.lock` committed-ready.
- Runtime deps: typer, pydantic, pydantic-settings. Dev group: ruff, mypy, pytest, pytest-asyncio, coverage, pip-audit. Later tasks add their own deps with `uv add` (FastAPI, SQLAlchemy, LiteLLM, Gradio, … land with M0.2–M4.2).
- Layout per TRD §S2: `api/ core/ llm/ eval/ db/` (docstring-only `__init__`), `cli.py`, `config.py`, `py.typed`; `migrations/ configs/ reports/` (`.gitkeep`); `tests/{unit,integration,e2e}`.
- `config.Settings` (pydantic-settings, frozen): `DATABASE_URL` required; Groq/Gemini/HF keys optional `SecretStr`; `OLLAMA_BASE_URL`, `LOG_LEVEL`, `DEMO_MODE`. `get_settings()` validates lazily once per process and raises `ConfigError` naming the missing/invalid variables.
- Typer CLI `receipt_extractor` with groups `data`, `eval`, `ingest`, `report` and `--version`; no import-time settings load.
- `.env.example`, `.gitignore`, `.gitattributes` (`* text=auto eol=lf`), README skeleton (Results header, Quickstart, Architecture, Evaluation, Limitations).
- Verification: `ruff check .` ✔, `ruff format --check .` ✔, `mypy src` (strict) ✔, `pytest -q` 15 passed, coverage 99 % (only `main()` uncovered), `pip-audit --skip-editable` no known vulnerabilities. `uv run receipt_extractor --help` lists data/eval/ingest/report. Fresh-clone simulation (copy without `.venv` → `uv sync --locked` → `pytest`) passed.
- Security: secrets are `SecretStr` (masked in `repr`/`str`/JSON dump — tested with sentinels); `ConfigError` builds its message from variable names only and suppresses the chained `ValidationError` (which would echo input values) — tested; Typer `pretty_exceptions_show_locals=False` so tracebacks never print locals; `.env*` gitignored except `.env.example`; ruff `S` (bandit) rules enabled. Performance: not relevant for this task.

**Files:** `pyproject.toml`, `uv.lock`, `.python-version`, `.env.example`, `.gitignore`, `.gitattributes`, `README.md`, `src/receipt_extractor/{__init__,cli,config}.py`, `src/receipt_extractor/{api,core,llm,eval,db}/__init__.py`, `src/receipt_extractor/py.typed`, `tests/conftest.py`, `tests/unit/test_{config,cli}.py`, `tests/**/__init__.py`, `migrations/.gitkeep`, `configs/.gitkeep`, `reports/.gitkeep`.

**Decisions:**
- Only `DATABASE_URL` is required at startup; provider keys are optional because CLI data/metric commands and offline tests don't need them — the LLM layer (M0.3) will fail fast per alias when a key it needs is missing.
- `ingest` is registered as a command group now so `--help` shows it; M0.4 may turn it into a single command `ingest <run_dir>` as TRD §S4 describes.
- Ruff excludes `*.md` (newer ruff formats Python code blocks in markdown; the spec docs are illustrative, not source).
- `pytest` runs with `filterwarnings = error` and `--strict-markers`; `integration`/`e2e` markers declared for M0.2+.

**Open:**
- Human action: `uv` was added to the user PATH by winget — open a new terminal before running `uv` commands.
- Human action: `git init` and connect the GitHub remote (the agent does not run git). `.gitignore` already excludes `.venv/`, `.env`, `data/`, `runs/`, `.cache/`.
- Note: the uv cache (C:) and the project (D:) are on different drives, so uv prints a hardlink warning; harmless. Silence it with `[Environment]::SetEnvironmentVariable('UV_LINK_MODE','copy','User')` if desired.

## 2026-10-19 — DOCS · Project documentation created

**Done:** Created PRD, TRD, APPFLOW, UI_UX_DESIGN, BACKEND_SCHEMA, IMPLEMENTATION_PLAN,
IMPLEMENTATION_PROMPTS, CLAUDE.md and the project copy of PROMPT_PLAYBOOK.md. No code exists yet.
Datasets were checked for availability and licence on 2026-10-19 (see PRD §4).
**Files:** all `*.md` in the project root.
**Decisions:** Gradio UI (benchmark-style project). FastAPI + Postgres per portfolio baseline. Donut (CORD-finetuned) chosen as the specialist baseline; RapidOCR (pip-only, CPU) instead of Tesseract to avoid a system binary on Windows. Qwen2.5-VL-3B runs on Kaggle and is imported as offline predictions.
**Open:**
- Human action: create a free Groq API key (console.groq.com) and a Gemini API key (aistudio.google.com) → put both in `.env`.
- Human action: install Docker Desktop (WSL2) and Ollama for Windows if not already installed.
- Human action: create a free Kaggle account and verify phone (required for GPU notebooks) before M2.4.
