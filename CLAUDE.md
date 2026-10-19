# Working Agreements for Claude Code — Receipt-to-JSON Extractor

> Photo of a receipt in, validated JSON out — with four pipelines benchmarked on accuracy, latency and cost.

Project documentation: [PRD.md](PRD.md) · [TRD.md](TRD.md) · [APPFLOW.md](APPFLOW.md) · [UI_UX_DESIGN.md](UI_UX_DESIGN.md) · [BACKEND_SCHEMA.md](BACKEND_SCHEMA.md) · [IMPLEMENTATION_PLAN.md](IMPLEMENTATION_PLAN.md) · [WORKLOG.md](WORKLOG.md) · [IMPLEMENTATION_PROMPTS.md](IMPLEMENTATION_PROMPTS.md) · **CLAUDE.md** · [PROMPT_PLAYBOOK.md](PROMPT_PLAYBOOK.md)

| Field | Value |
|---|---|
| Project | P01 · `01-receipt-extractor` |
| Target role signal | AI/GenAI Engineer · document AI · structured outputs · evaluation |
| Difficulty / effort | ≈ 8 focused days (Phase 1) |
| Frontend | Gradio 5 (Python) mounted on the FastAPI app |
| Backend | FastAPI + PostgreSQL 16 (pgvector) via Docker Compose |
| Status | Specification v1 · 2026-10-19 |

---

These rules override the generic playbook wherever they are stricter.

## 1. Git: read-only, except commit/push on `test`

Allowed: `git status`, `log`, `diff`, `show`, `blame`, `rev-parse`.
**On the `test` branch only, and only when the human asks:** `git add`, `git commit`, and a
plain (non-force) `git push origin test`. Never push to, commit on, or delete any other branch.
Forbidden: `init`, `pull`, `branch`, `checkout`/`switch`, `merge`, `rebase`, `reset`, `tag`,
`stash`, force pushes (`-f`, `--force*`, `+refspec`), `--no-verify`, `--all`/`--tags`/`--mirror`,
any `config` change, and `gh pr merge` / `gh repo sync`. Merging `test` into other branches is
the human's job.

Enforced by `.claude/hooks/git_guard.py` (PreToolUse hook in `.claude/settings.json`); the agent
is denied edits to `.claude/settings*.json` and `.claude/hooks/**`. If the hook blocks something,
report it — never try to work around it.

## 2. Databases

- Never execute migrations, `cli ingest`, seed scripts or SQL against the `docker compose`
  database or any hosted (demo) database. Write the migration/script, then hand it over.
- **Permitted:** ephemeral PostgreSQL containers created and destroyed by the test suite
  (`testcontainers`), and read-only queries when the human has given access.
- Every required human DB action goes in WORKLOG.md under **Open → Human action**, with the exact command.

## 3. Environments

- Python: `uv` only (`uv sync`, `uv run …`, `uv add …`). No global `pip install`.
- No Node toolchain in this project.
- If a global install is genuinely unavoidable, say so explicitly and state how to remove it.
- Datasets and model weights are downloaded only by explicit CLI commands into `data/` or the
  model cache — never at import time or app startup.

## 4. Free tier and external services

- Only the providers in `configs/models.yaml` (Groq free plan, Gemini API free tier, local Ollama)
  and free hosting (HF Spaces, Neon free). Never add a paid API, even "just for a baseline".
- Use the disk cache and sampling for experiments; respect daily budgets. If a sweep would exceed
  a free-tier daily limit, plan it across days and record that in WORKLOG.md.
- Gemini free tier may use submitted data — send it public dataset content only.
  This includes tooling: never run graphify with `--backend gemini` / `GRAPHIFY_BACKEND=gemini`.
  Rebuild the graph offline with `graphify update .` (code-only, no LLM).
- The machine has no CUDA GPU. GPU work is written as a Kaggle notebook under `notebooks/` for the human to run.

## 5. Project invariants

- The canonical `Receipt` Pydantic model (BACKEND_SCHEMA §1.1) is the single output contract for every pipeline; pipelines never return ad-hoc dicts.
- Uploaded images are processed in memory and never persisted or logged; only metadata goes to `extraction_log`.
- User uploads are never sent to the Gemini free tier (its data may be used by Google); Gemini is used only on CORD dataset images.
- Money values are `Decimal`, never `float`; parsing of CORD's `1.000`-style thousands separators lives in one function with tests.

## 6. Commands

```powershell
uv sync                                   # install
uv run ruff check . ; uv run mypy src ; uv run pytest -q
uv run receipt_extractor --help                  # CLI
uv run uvicorn receipt_extractor.api.main:app --reload

# Human-only:
docker compose up -d db
uv run alembic upgrade head
uv run receipt_extractor ingest runs/<run_id>
```

## 7. WORKLOG format

Newest first: `## YYYY-MM-DD — Task ID · Short title`, then **Done / Files / Decisions / Open**.
Tests, verification, security and performance notes go under Done/Open — they must be recorded.
A task is not finished until its entry exists.

## 8. Documentation stays true

When a decision changes, update its owning document (PRD/TRD/APPFLOW/UI_UX_DESIGN/BACKEND_SCHEMA)
in the same task and log the change in WORKLOG.md.

## graphify

This project has a knowledge graph at graphify-out/ with god nodes, community structure, and cross-file relationships.

Rules:
- For codebase questions, first run `graphify query "<question>"` when graphify-out/graph.json exists. Use `graphify path "<A>" "<B>"` for relationships and `graphify explain "<concept>"` for focused concepts. These return a scoped subgraph, usually much smaller than GRAPH_REPORT.md or raw grep output.
- If graphify-out/wiki/index.md exists, use it for broad navigation instead of raw source browsing.
- Read graphify-out/GRAPH_REPORT.md only for broad architecture review or when query/path/explain do not surface enough context.
- After modifying code, run `graphify update .` to keep the graph current (AST-only, no API cost).
