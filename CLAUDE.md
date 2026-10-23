# Working Agreements for Claude Code — Receipt-to-JSON Extractor

> A receipt photo goes in; validated JSON (line items, prices, totals) comes out — OCR+LLM vs a vision LLM, compared on accuracy, latency and cost.

Project documentation: [PRD.md](PRD.md) · [TRD.md](TRD.md) · [APPFLOW.md](APPFLOW.md) · [UI_UX_DESIGN.md](UI_UX_DESIGN.md) · [BACKEND_SCHEMA.md](BACKEND_SCHEMA.md) · [IMPLEMENTATION_PLAN.md](IMPLEMENTATION_PLAN.md) · [WORKLOG.md](WORKLOG.md) · [IMPLEMENTATION_PROMPTS.md](IMPLEMENTATION_PROMPTS.md) · **CLAUDE.md** · [PROMPT_PLAYBOOK.md](PROMPT_PLAYBOOK.md)

| Field | Value |
|---|---|
| Project | P01 · `01-receipt-extractor` |
| Type | Quick win 1 of 3 |
| Skill it adds | Multimodal, structured output |
| Target role signal | AI/GenAI Engineer · document AI · structured outputs · evaluation |
| Effort | ≈ 3.75 focused days (sum of task estimates; the ideas doc says 2–3 days) |
| Public artefact | Hugging Face Space (upload a receipt, see both pipelines side by side) |
| Interface | Gradio 6 → Hugging Face Space |
| Status | Specification v2 (lean) · 2026-10-22 |

---

These rules override the generic playbook wherever they are stricter.

## 1. Git: read-only, except commit/push on `test`

Carried over from v1 (this folder's code came from `D:\Project\01-receipt-extractor`).
Allowed: `git status`, `log`, `diff`, `show`, `blame`, `rev-parse`.
**On the `test` branch only, and only when the human asks:** `git add`, `git commit`, and a plain
(non-force) `git push origin test`. Never push to, commit on, or delete any other branch.
Forbidden: `init`, `pull`, `branch`, `checkout`/`switch`, `merge`, `rebase`, `reset`, `tag`, `stash`,
force pushes, `--no-verify`, `--all`/`--tags`/`--mirror`, any `config` change, `gh pr merge` /
`gh repo sync`. Enforced by `.claude/hooks/git_guard.py` (PreToolUse hook in `.claude/settings.json`);
the agent may not edit `.claude/settings*.json` or `.claude/hooks/**`. If the hook blocks something,
report it — never work around it.

## 2. No database

v2 has no database. Results are files (TRD §S4). Do not add Postgres, SQLite-as-app-state, Docker
Compose or an ORM without a PRD change approved by the human. (Read-only SQLite *datasets* used by an
experiment are data, not app state.)

## 3. Environments

- Python: `uv` only (`uv sync`, `uv run …`, `uv add …`). No global `pip install`. No Node toolchain.
- If a global install is genuinely unavoidable, say so explicitly and state how to remove it.
- Datasets and model weights are downloaded only by explicit CLI commands into `data/` or the model
  cache — never at import time or app startup.

## 4. Free tier and external services

- Only the providers in `configs/models.yaml` (Groq free plan, Gemini API free tier, local
  Ollama/llama.cpp) and free hosting (Hugging Face Hub + Spaces). Never add a paid API, even "just
  for a baseline".
- Use the disk cache and fixed subsets; respect daily budgets. If a sweep would exceed a free-tier
  daily limit, plan it across days and record that in WORKLOG.md.
- Gemini's free tier may use submitted data — send it public dataset content only, never user input.
- The machine has no CUDA GPU. GPU work is written as a Kaggle notebook under `notebooks/` for the
  human to run; its outputs come back through `cli import-predictions`.
- Publishing (HF Hub pushes, Space creation, PyPI uploads) is a **human action**: prepare it, list
  the exact commands in WORKLOG, and stop.

## 5. Project invariants

- The `Receipt` Pydantic model is the single output contract for every pipeline; pipelines never return ad-hoc dicts.
- Uploaded images are processed in memory and never persisted or logged; only metadata is logged.
- User uploads are never sent to Gemini's free tier; Gemini sees CORD images only.
- Money is `Decimal`, never `float`; `1.000`-style parsing lives in one tested function.
- `A-naive` (no retries, no arithmetic check) stays in every results table as the baseline.
- All model calls go through the provider layer (TRD §S3); all experiments go through the eval runner
  (TRD §S4). A number that is not produced by a run does not go in the README.

## 6. Commands

```powershell
uv sync                                   # install
uv run ruff check . ; uv run mypy src ; uv run pytest -q
uv run receipt_extractor --help                  # CLI
uv run python app.py                      # local Gradio demo
uv run receipt_extractor llm ping --model groq-oss-20b
```

## 7. WORKLOG format

Newest first: `## YYYY-MM-DD — Task ID · Short title`, then **Done / Files / Decisions / Open**.
Tests, verification, security and performance notes go under Done/Open — they must be recorded.
A task is not finished until its entry exists.

## 8. Documentation stays true

When a decision changes, update its owning document (PRD/TRD/APPFLOW/UI_UX_DESIGN/BACKEND_SCHEMA)
in the same task and log the change in WORKLOG.md.

## graphify

v1 set up a graphify knowledge graph (`graphify-out/`, local only, not copied). Rebuild it offline
with `graphify update .` after M0.R; never use the Gemini backend for graphify.
