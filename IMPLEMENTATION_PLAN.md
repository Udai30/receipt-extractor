# Implementation Plan — Receipt-to-JSON Extractor

> A receipt photo goes in; validated JSON (line items, prices, totals) comes out — OCR+LLM vs a vision LLM, compared on accuracy, latency and cost.

Project documentation: [PRD.md](PRD.md) · [TRD.md](TRD.md) · [APPFLOW.md](APPFLOW.md) · [UI_UX_DESIGN.md](UI_UX_DESIGN.md) · [BACKEND_SCHEMA.md](BACKEND_SCHEMA.md) · **IMPLEMENTATION_PLAN.md** · [WORKLOG.md](WORKLOG.md) · [IMPLEMENTATION_PROMPTS.md](IMPLEMENTATION_PROMPTS.md) · [CLAUDE.md](CLAUDE.md) · [PROMPT_PLAYBOOK.md](PROMPT_PLAYBOOK.md)

| Field | Value |
|---|---|
| Project | P01 · `01-receipt-extractor` |
| Type | Quick win 1 of 3 |
| Skill it adds | Multimodal, structured output |
| Target role signal | AI/GenAI Engineer · document AI · structured outputs · evaluation |
| Effort | ≈ 3.75 focused days (sum of task estimates; the ideas doc says 2–3 days) |
| Public artefact | Hugging Face Space (upload a receipt, see both pipelines side by side) |
| Interface | Gradio 5 → Hugging Face Space |
| Status | Specification v2 (lean) · 2026-10-22 |

---

## 1. How to use this plan

- Tasks are executed **in order**. Reordering requires the user's approval (PROMPT_PLAYBOOK §13).
- Each task has a ready-to-paste prompt in [IMPLEMENTATION_PROMPTS.md](IMPLEMENTATION_PROMPTS.md).
- "d" = one focused day (~6–8 h). Total: **≈ 3.75 focused days** (stretch tasks excluded).
  At ~1–1.5 h on weekdays plus longer weekends, budget roughly 2 weekends plus a few weekday evenings.
- A task is complete only when its acceptance criteria are verified and WORKLOG.md is updated.
- **This project already has code.** v1's M0.1–M0.3 (scaffold, Postgres layer, LLM provider layer; 144 tests green on 2026-10-19) were copied in from `D:\Project\01-receipt-extractor`. M0.R slims that to the v2 lean stack before anything else; its `llm/` package is the foundation every later project ports.

## 2. Milestones and tasks

| Task | Title | Depends on | Effort |
|---|---|---|---|
| M0.R | Re-baseline the ported v1 code to the lean stack | — | 0.25 d |
| M0.4 | Evaluation runner, reports and prediction import (lean) | M0.R | 0.25 d |
| M1.1 | CORD-v2 download and normalisation to `Receipt` | M0.4 | 0.5 d |
| M1.2 | Field-level scorer with order-insensitive item matching | M1.1 | 0.25 d |
| M1.3 | Pipeline A — RapidOCR + Groq structuring (naive and engineered modes) | M1.2 | 0.5 d |
| M2.1 | Pipeline B — Gemini Flash vision | M1.3 | 0.25 d |
| M2.2 | Retries, arithmetic reconciliation and review flag | M2.1 | 0.25 d |
| M2.3 | Full 100-receipt test runs for A-naive, A and B | M2.2 | 0.25 d |
| M2.4 | Pipeline C — Qwen2.5-VL-3B on Kaggle + import | M2.3 | 0.5 d (stretch) |
| M3.1 | Failure analysis and one round of fixes | M2.3 | 0.5 d |
| M3.2 | Gradio Space (Results, Try it, Failures) | M3.1 | 0.5 d |
| MF.1 | README, resume bullet and LinkedIn draft | M3.2 | 0.25 d |

After MF.1: the database, API and Docker work removed in M0.R is tracked as Future improvements in
[PRD.md](PRD.md) §8 — not scheduled until the human approves it.


### M0 — Foundation (re-baseline the ported v1 code)

#### M0.R · Re-baseline the ported v1 code to the lean stack

**Objective:** Turn the copied v1 code (scaffold + Postgres layer + LLM layer) into the v2 lean baseline without losing the LLM layer's tested behaviour.

**Scope:**
- Run `uv sync` and the full gate suite; record the starting state in WORKLOG.
- Remove the database layer: `db/`, `migrations/`, `alembic.ini`, `docker-compose.yml`, the DB sink, DB settings (`DATABASE_URL`, `DB_*`, `POSTGRES_*`), `asyncpg`/`sqlalchemy`/`alembic`/`testcontainers` deps, and DB-only tests.
- Keep `llm/` intact apart from removing `DatabaseSink`; keep `config.py`, `cli.py` (drop the `ingest` group), `api/` removed (no FastAPI in v2).
- Decide on `Dockerfile`/`.dockerignore`: remove unless the Space needs Docker SDK (it does not; Gradio SDK is used). Record the decision.
- Add `ui/` and `app.py` stubs per TRD §S2; update `.env.example`, `README.md` and `.gitignore` (keep `IMPLEMENTATION_PROMPTS.md` and `PROMPT_PLAYBOOK.md` local-only).

**Done when:**
- No DB code, deps or settings remain; all §S8 gates green.
- WORKLOG lists exactly what was removed and the test count before/after.

#### M0.4 · Evaluation runner, reports and prediction import (lean)

**Objective:** Make every experiment a resumable, reproducible run whose numbers flow into committed reports — the runner every later project ports.

**Scope:**
- Run config schema (Pydantic) + `safe_load` loader; run id = hash(resolved config + dataset manifest hash).
- Async runner: bounded concurrency, per-case exception capture, immediate JSONL append, resume by case id; treats `BudgetExhaustedError` as pause.
- Metric registry (`Metric.compute(prediction, gold)`), summary with bootstrap CIs, `manifest.json`.
- `report` → `reports/results.csv|md` + charts; `failures` → `reports/failures.csv`; `import-predictions <dir>` for Kaggle outputs.

**Done when:**
- A toy run over 20 synthetic cases completes, resumes after interruption and appears in `reports/results.csv`.


### M1 — Day 1 — data, schema, scorer, pipeline A

#### M1.1 · CORD-v2 download and normalisation to `Receipt`

**Objective:** A fixed, reproducible benchmark: CORD test ids with gold `Receipt` objects.

**Scope:**
- `cli data prepare` downloads `naver-clova-ix/cord-v2` (revision pinned) into `data/cord/`, writes manifest + split ids.
- `Receipt`/`Item` models with Decimal money; `parse_amount()` for `1.000`, `1,000.50`, currency prefixes, blanks.
- Map CORD gold (`menu`, `sub_total`, `total`) into `Receipt`; report receipts that cannot be mapped.
- `cli data stats`: items per receipt, currency formats seen, unmappable count.

**Done when:**
- Gold JSON for all 100 test receipts; split sizes and licence confirmed in WORKLOG.

#### M1.2 · Field-level scorer with order-insensitive item matching

**Objective:** One trusted scorer so every pipeline is judged identically.

**Scope:**
- Scalar field exact-match after normalisation; items via Hungarian matching on name similarity + price.
- Item P/R/F1, per-field F1, valid-JSON %, reconcile % as registry metrics.

**Done when:**
- Scorer tests green; gold-vs-gold run in `reports/` shows 1.0.

#### M1.3 · Pipeline A — RapidOCR + Groq structuring (naive and engineered modes)

**Objective:** The cheap pipeline end to end, first on 10 receipts.

**Scope:**
- Pipeline protocol; RapidOCR stage (CPU) with per-stage latency.
- Prompt (versioned) → `groq-oss-20b` with `Receipt` as `response_model`; naive mode = single call, no repair.
- Run `A-naive` and `A` on 10 validation receipts; eyeball outputs; fix obvious prompt issues on validation only.

**Done when:**
- 10-receipt validation run for `A-naive` and `A` in `runs/`; per-stage latency recorded.


### M2 — Day 2 — pipeline B, retries, reconciliation, full run

#### M2.1 · Pipeline B — Gemini Flash vision

**Objective:** The vision-LLM pipeline on the same schema and ids.

**Scope:**
- Image → `gemini-flash` with `Receipt` schema; `data_origin=public_dataset` enforced.
- Downscale images to the model's useful resolution; record input image tokens.

**Done when:**
- 10-receipt validation run for `B` in `runs/`.

#### M2.2 · Retries, arithmetic reconciliation and review flag

**Objective:** The engineering that turns a demo into a dependable extractor.

**Scope:**
- Retry on schema failure (≤ 2 attempts) with Pydantic errors in the repair prompt.
- Arithmetic check with tolerance (configurable); `reconciled`, `reconcile_diff`, `needs_review` in `ExtractionResult`.
- Applies to A and B; `A-naive` bypasses it.

**Done when:**
- Validation runs show attempts and reconcile flags populated.

#### M2.3 · Full 100-receipt test runs for A-naive, A and B

**Objective:** The benchmark numbers the README leads with.

**Scope:**
- Run configs for `a-naive`, `a`, `b` on the 100 test ids; Gemini spread across days if its RPD requires (plan in WORKLOG).
- `cli report build` → `reports/results.csv|md`, F1 and cost charts.

**Done when:**
- `reports/results.csv` has all three variants on the same 100 ids with CIs, p50/p95 and $/1k.

#### M2.4 · Pipeline C — Qwen2.5-VL-3B on Kaggle + import *(stretch — skip if behind)*

**Objective:** An open-weights VLM point on the same table, without a local GPU.

**Scope:**
- `notebooks/qwen_vl_cord.ipynb` (pinned versions, T4, same prompt and schema, writes predictions + manifest + GPU seconds).
- `cli eval import-predictions data/imports/qwen_vl` → run dir → report row `C`.

**Done when:**
- Notebook ready; human action logged; after the human's run, row `C` exists.


### M3 — Day 3 — failures, demo, write-up

#### M3.1 · Failure analysis and one round of fixes

**Objective:** Know why each pipeline fails and show what one fix bought.

**Scope:**
- `cli report failures` export; human + agent label 20–30 failures (OCR misread, wrong item split, hallucinated item, number format, other).
- Pick the biggest cause, implement one fix (validated on the validation split), re-run affected variants on test, record before/after.

**Done when:**
- `reports/failures.csv` labelled; before/after row in `reports/results.md`.

#### M3.2 · Gradio Space (Results, Try it, Failures)

**Objective:** Something a stranger can click.

**Scope:**
- `ui/` Blocks app with the three tabs; Try it runs A live on uploads and shows cached A/B for sample receipts (B live on samples if budget allows).
- `app.py`, `requirements.txt` for the Space (exported from uv), Space README front-matter; `DEMO_MODE` fallbacks.
- `docs/DEPLOY.md` with every human step (create Space, set secrets, push).

**Done when:**
- Local app works end to end; DEPLOY.md complete; WORKLOG lists the Space URL once the human deploys.


### MF — Ship — write-up and distribution

#### MF.1 · README, resume bullet and LinkedIn draft

**Objective:** Turn measurements into the artefact recruiters read.

**Scope:**
- README per §S5 with the results table generated from `reports/` (no hand-typed numbers) and a Mermaid architecture diagram.
- Fill the resume bullet's brackets from measured numbers; `docs/LINKEDIN_DRAFT.md` about the finding, not the build (link goes in the first comment).
- 20–30 s GIF script in `docs/GIF_SCRIPT.md` (human records it).

**Done when:**
- README renders on GitHub with results above the fold; reproduce commands verified from a clean clone.
