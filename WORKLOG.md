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
| Current task | M0.2 — done · next: M0.3 |
| Last verified gate run | 2026-10-19 — ruff, ruff format, mypy --strict, pytest (43 passed incl. 16 testcontainers integration), pip-audit: all green |
| Pending human actions | compose DB migrated to `0001_baseline` (2026-10-19); others: see the entries below |

---

## 2026-10-19 — M0.2 · Docker Compose, Postgres/pgvector and baseline migration

**Done:**
- Verified M0.1 was complete in the repo (package layout, `config.Settings`, CLI, gates) before starting; reused `Settings`/`get_settings`.
- Deps: `sqlalchemy[asyncio]` 2.1.5, `asyncpg` 0.32.0, `alembic` 1.20.0 (runtime); `testcontainers[postgres]` 4.15.0 (dev). Lockfile updated with `uv add`.
- `db/models.py`: SQLAlchemy 2 typed models `EvalRun`, `EvalResult`, `LLMCall` exactly as BACKEND_SCHEMA §S2 (uuid PK `gen_random_uuid()`, `created_at timestamptz default now()`, text+CHECK statuses, jsonb payloads, `Decimal` money columns, the three §S2 indexes, unique `run_key` and `(run_id, case_id)`, FK cascade only run→results). Deterministic constraint naming convention.
- `db/session.py`: `create_engine` (asyncpg-only URL check that never echoes the URL; `pool_pre_ping`, bounded `pool_timeout`, `application_name`), `create_engine_from_settings`, `create_session_factory` (`expire_on_commit=False`). No import-time connection.
- `db/repositories.py`: insert/select for each table (`add_eval_run`, `get_eval_run`, `get_eval_run_by_key`, `list_eval_runs`, `add_eval_results`, `list_eval_results`, `add_llm_calls`, `list_llm_calls`). They flush but never commit (caller owns the unit of work); every list query is bounded (1–1000) and index-backed.
- Settings: `DB_POOL_SIZE` (default 5, 1–50), `DB_MAX_OVERFLOW` (5), `DB_POOL_TIMEOUT_S` (10), documented in `.env.example`.
- Alembic (async): `alembic.ini` with no URL; `migrations/env.py` takes the URL from `config.attributes` (tests) or `DATABASE_URL` via Settings; offline `--sql` mode needs no DB. Revision `0001_baseline`: `CREATE EXTENSION vector` + 3 tables + indexes; downgrade drops them all.
- `docker-compose.yml`: `db` = `pgvector/pgvector:pg16`, named volume `pgdata`, `pg_isready` healthcheck, `127.0.0.1` port; `api` built from the Dockerfile, `depends_on: db: service_healthy`, read-only rootfs, `cap_drop: ALL`, `no-new-privileges`, loopback port. Credentials only via `.env` with `${VAR:?}` (no default password).
- Multi-stage `Dockerfile` (uv 0.13.1 builder → `python:3.12-slim-bookworm` runtime, `uv sync --locked --no-dev --no-editable`, non-root uid 10001, root-owned venv) + `.dockerignore` (excludes `.env*`, `.venv`, `data`, `runs`, `.git`, tests).
- `tests/conftest.py`: session-scoped testcontainers pgvector Postgres (skips with a reason if Docker is down); `migrated_database_url` (alembic upgrade head); `empty_database_url` (fresh DB per test, dropped after); `db_engine`/`db_session` (outer transaction rolled back, `join_transaction_mode="create_savepoint"`).
- Tests: `tests/integration/test_migrations.py` (upgrade on empty DB; downgrade→upgrade round trip with identical schema snapshot; autogenerate diff between migration and models is empty), `tests/integration/test_repositories.py` (round trip per table plus unique/CHECK/FK/cascade/no-cascade constraints, bounds, filters), `tests/unit/test_db_session.py`, `tests/unit/test_deploy_files.py` (loopback ports, no default password, non-root, no dev deps), pool settings test.
- Verification evidence: `ruff check` ✔, `ruff format --check` ✔, `mypy src` (strict) ✔, `pytest -q` **43 passed** (16 integration tests ran against real containers, confirmed with `-m integration -v`), coverage 99 % total / 100 % on `db/`, `pip-audit` no known vulnerabilities. The drift test was checked by changing `list_cost_usd` to `Numeric(12,4)`: it failed with `modify_type`, then the change was reverted. No test containers were left running afterwards (`docker ps -a` empty).
- Image check: `docker build` succeeded (296 MB). In the container: `id` → `uid=10001(app)`, no pytest/ruff/mypy/testcontainers/coverage and no `uv`, `alembic upgrade head --sql` renders 4 `CREATE TABLE`. The check image was removed afterwards. `docker compose config` shows both ports bound to `127.0.0.1` and fails without `POSTGRES_PASSWORD`. **The compose stack was never started and no migration ran against it.**
- Security: loopback-only ports; secrets only from `.env`; URL/password never echoed in errors (tested); non-root, read-only, capability-dropped container; build context excludes secrets; DDL in tests uses only generated identifiers. Performance: §S2 indexes present and verified; pool sized via settings with bounded wait; repository list queries capped.
- Docker Desktop was not running; the agent started the Desktop app (not the compose stack) to run the testcontainers tests.

**Files:** `pyproject.toml`, `uv.lock`, `src/receipt_extractor/config.py`, `src/receipt_extractor/db/{models,session,repositories}.py`, `alembic.ini`, `migrations/{env.py,script.py.mako}`, `migrations/versions/0001_baseline.py` (removed `migrations/.gitkeep`), `docker-compose.yml`, `Dockerfile`, `.dockerignore`, `.env.example`, `tests/conftest.py`, `tests/integration/test_{migrations,repositories}.py`, `tests/unit/test_{db_session,deploy_files,config}.py`, `README.md`, `TRD.md` (§S6), `BACKEND_SCHEMA.md` (§S2 notes), `WORKLOG.md`.

**Decisions:**
- `eval_result.run_id` gets no separate single-column index: `(run_id, status)` and unique `(run_id, case_id)` both lead with it (satisfies §S1 "every FK has an index"). Recorded in BACKEND_SCHEMA §S2.
- `llm_call.run_id` uses the default `NO ACTION` (not cascade, not SET NULL): §S1 allows cascade only run→results, and SET NULL would mislabel eval calls as live traffic. Deleting a run that has calls fails loudly (tested). Recorded in BACKEND_SCHEMA §S2.
- Hand-written baseline (not autogenerate) with a drift test in its place, so the migration and models can't silently diverge.
- Compose-only variables (`POSTGRES_*`, `API_PORT`) live in `.env.example` but not in `Settings`; the `.env.example` completeness test allow-lists them explicitly.
- Integration tests skip (with a reason, visible through `-ra`) when Docker is unreachable, rather than erroring. The TRD §S8 gate run requires Docker running.
- The `api` image's CMD targets `receipt_extractor.api.main:app` with uvicorn. Both arrive in M0.5, so `docker compose up api` won't work until then (the `db` service works now).
- `downgrade` of the baseline drops the `vector` extension. The DB is project-owned, and later revisions downgrade first.

**Open:**
- Human action (start + migrate the local DB):
  1. `Copy-Item .env.example .env`, then set `POSTGRES_PASSWORD` (URL-safe characters) and put the same password in `DATABASE_URL`.
  2. `docker compose up -d db`
  3. `uv run alembic upgrade head`
  Optional preview of the DDL first: `uv run alembic upgrade head --sql`.
- 2026-10-19 check after the human ran the steps: `db` container is up and healthy on `127.0.0.1:5432`. `.env` DATABASE_URL has the right driver/user/db/port, but its password doesn't parse: the password contains `@` (not URL-safe), so the URL splits at the wrong `@` and the host is wrong. `alembic upgrade head` cannot have connected with this URL. The agent ran no SQL and did not check `alembic_version`. Mistake: the check printed the parsed host, which showed a fragment of the password in the session transcript. It has not been written to any file.
- Fixed by the agent at the human's request: `.env` `DATABASE_URL` was rebuilt from `POSTGRES_USER/PASSWORD/DB/PORT` with each part percent-encoded and host `127.0.0.1`. A round-trip check confirmed it decodes to `POSTGRES_PASSWORD`, and `load_settings()` parses it. No secret was printed, and only that one line changed.
- Human action: run `uv run alembic upgrade head` and `uv run alembic current` (expect `0001_baseline (head)`). Optional, since part of it was shown in a transcript: change the password (needs `ALTER USER`, or `docker compose down -v` on the still-empty volume — both human-run).
- 2026-10-19, read-only check run with the human's permission: `uv run alembic current` connected to the compose DB with the fixed URL but printed no revision, so the database is **not migrated yet** (the only `upgrade` run visible was the failed one from before the fix). Human action still open: `uv run alembic upgrade head`.
- 2026-10-19: the human ran `uv run alembic upgrade head`. A read-only `uv run alembic current` (run with permission) now reports `0001_baseline (head)`. **The compose DB is migrated; M0.2 human actions are complete.**
- Follow-up (M0.5): the compose `api` service builds DATABASE_URL from the raw `POSTGRES_PASSWORD`, which breaks for non-URL-safe passwords. Pass a dedicated, pre-encoded URL variable instead.
- Follow-up (M0.5): add FastAPI/uvicorn and `api/main.py` so the `api` compose service runs; add an image HEALTHCHECK against `/health` then.
- Note: base images are pinned by tag (`pgvector/pgvector:pg16`, `python:3.12-slim-bookworm`, `uv:0.13.1`), not digest. Consider digest pinning before MF.1 deploy.

## 2026-10-19 — TOOLING · graphify knowledge graph + ponytail plugin

**Done:**
- graphify: `graphifyy` 0.9.44 was already installed as a user-level uv tool, and the `/graphify` skill was already in `~/.claude/skills/`, so nothing new was installed. Project setup: `.graphifyignore` (venv, caches, `data/`, `runs/`, `uv.lock`, binaries/images) and `graphify-out/` gitignored.
- First graph built offline: `GRAPHIFY_BACKEND=none graphify extract . --code-only` → 81 nodes, 114 edges, 15 communities; `graphify cluster-only . --no-label` → `GRAPH_REPORT.md` + `graph.html`. Verified with `graphify query "how are settings loaded and validated" --budget 400` (it returned `config.py` / `Settings` / `get_settings` / `load_settings` and the related tests).
- CLAUDE.md §4: graphify must never use the Gemini backend (free-tier data rule).
- ponytail: Claude Code plugin; installation needs the interactive `/plugin` command, so it's listed under Human action.

**Files:** `.graphifyignore`, `.gitignore`, `CLAUDE.md`, `WORKLOG.md`. `graphify-out/` is generated and ignored.

**Decisions:**
- `graphify-out/` is not committed: the graph can embed content from the local-only planning docs, and it's cheap to rebuild.
- The graph follows `.gitignore`, so the local-only planning docs aren't in it. Code is parsed locally by tree-sitter, and no LLM was used for this build.
- The agent did not run `graphify claude install` (it writes a PreToolUse hook into the edit-locked `.claude/settings.json`) or `graphify hook install` (it writes git hooks and a merge driver, a git config change forbidden by CLAUDE.md §1). The human runs both.

**Open:**
- Human action: in the project, run `graphify claude install` (adds the query-graph-first section to CLAUDE.md and its PreToolUse hook to `.claude/settings.json`). Then check that `.claude/settings.json` still contains the git-guard hook.
- Human action (optional): `graphify hook install` to rebuild the graph automatically after each commit/checkout. After `git pull`, run `graphify update .`.
- Human action: install ponytail from an interactive `claude` terminal: `/plugin marketplace add DietrichGebert/ponytail`, then `/plugin install ponytail@ponytail`. Its "write less code" style can pull against this project's production-grade bar (validation, tests, error handling), so consider `/ponytail lite`.

## 2026-10-19 — TOOLING · Agent may push only to `test`

**Done:**
- Human request: the agent may commit/push only on the `test` branch. CLAUDE.md §1 updated accordingly (commit/push only when asked; no force, delete, branch switching, history rewriting or server-side merges).
- Enforcement: `.claude/hooks/git_guard.py` (stdlib Python) runs as a PreToolUse hook on every Bash/PowerShell call (`onFailure: block`, so a broken hook fails closed). It denies `git push` to any destination other than `test` (including `src:dst`, `refs/heads/…`, `HEAD` from another branch, multiple refspecs), force/delete/mirror/all/tags/`--no-verify`, bare `git push` unless on `test` tracking `*/test`, `git commit` unless on `test` (unborn/detached HEAD also denied), inline `-c alias.*`, `git -C <dir>` targets, commands nested in `bash -c "…"` strings, and `gh pr merge` / `gh repo sync`.
- `.claude/settings.json` also deny-lists editing `.claude/settings*.json` and `.claude/hooks/**` (so the agent cannot disable its own guard) plus `git config/checkout/switch/reset/rebase/merge/branch` and force pushes.
- Verification: 27 pipe-tested commands (7 allowed, 20 denied as expected); branch-dependent cases re-checked in a throwaway repo on `main` (bare push and commit denied). Live check: `git push --dry-run origin main` was blocked by the hook in this session. Gates: ruff ✔, ruff format ✔, mypy ✔, pytest 15 passed.

**Files:** `.claude/settings.json`, `.claude/hooks/git_guard.py`, `CLAUDE.md`, `pyproject.toml` (ruff excludes `.claude`), `WORKLOG.md`.

**Decisions:**
- Ruff excludes `.claude/`: the hook is agent tooling, not project source, and is edit-locked for the agent. The remaining findings were style-only (explicit `check=False`, branch count, a magic number).
- The guard is a client-side safety net for the agent only. It does not restrict the human's own git use.

**Open:**
- Human action (recommended): add a GitHub branch protection rule for your default branch (Settings → Branches) as the server-side guarantee. The hook only covers commands run through Claude Code.
- Note: commit `.claude/` so the guard travels with the repo. (The human later removed `CLAUDE.md` from `.gitignore`, so the rules text is committed too.)

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
- Deferred to MF.2: the human keeps the planning docs (PRD, TRD, … CLAUDE.md) local-only via `.gitignore`, so the README's `TRD.md` links and the WORKLOG header links are dead on GitHub. Fix them when the README is rewritten.
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
