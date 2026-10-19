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
