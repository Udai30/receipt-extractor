# Product Requirements Document — Receipt-to-JSON Extractor

> A receipt photo goes in; validated JSON (line items, prices, totals) comes out — OCR+LLM vs a vision LLM, compared on accuracy, latency and cost.

Project documentation: **PRD.md** · [TRD.md](TRD.md) · [APPFLOW.md](APPFLOW.md) · [UI_UX_DESIGN.md](UI_UX_DESIGN.md) · [BACKEND_SCHEMA.md](BACKEND_SCHEMA.md) · [IMPLEMENTATION_PLAN.md](IMPLEMENTATION_PLAN.md) · [WORKLOG.md](WORKLOG.md) · [IMPLEMENTATION_PROMPTS.md](IMPLEMENTATION_PROMPTS.md) · [CLAUDE.md](CLAUDE.md) · [PROMPT_PLAYBOOK.md](PROMPT_PLAYBOOK.md)

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

## 1. Problem

Document extraction (invoices, receipts, KYC, claims) is one of the most common paid GenAI use cases.
Teams have to choose between a cheap OCR + text-LLM pipeline and a vision LLM, usually on vibes.
This project measures the choice on a labelled receipt dataset — field-level accuracy, how often the
JSON is valid, whether the arithmetic reconciles, latency and cost — and adds the engineering that
turns a demo into something you could put in front of a finance team: one schema, retries on
schema failure, an arithmetic check and a human-review flag.

## 2. Users

| User | Need |
|---|---|
| Recruiter / hiring manager | A results table that shows model choice by measurement, plus a demo they can click |
| Engineer building document AI | A reusable schema + scorer + reconciliation pattern and honest cost numbers |
| Demo visitor | Upload a receipt, see both pipelines' JSON side by side with latency and cost |

## 3. Goals and non-goals

**Goals**
- G1. Two pipelines (A: OCR + LLM, B: vision LLM) producing the same `Receipt` schema; C (open VLM on Kaggle) as stretch.
- G2. A field-level scorer and a fixed 100-receipt test run for every pipeline.
- G3. Show what the engineering buys: naive A vs engineered A (retries + arithmetic check).
- G4. A public Space and a README that opens with the results table.

**Non-goals**
- Multi-page invoices, handwriting, languages other than CORD's.
- Training an extraction model (Donut from v1 is dropped from v2 scope).

## 4. Data

| Dataset | Source | Licence | Use |
|---|---|---|---|
| CORD-v2 | Hugging Face `naver-clova-ix/cord-v2` — ~1,000 receipts, 800 train / 100 validation / 100 test, field-level labels | CC-BY-4.0, not gated | Test split = the benchmark; validation = prompt/threshold tuning; train = few-shot examples |

Receipts are Indonesian: prices use `1.000`-style thousands separators. Normalise numbers before
comparing (one function, tested). Facts carried from the ideas doc (2026-10-22); M1.1 re-verifies them.

## 5. Success metrics

| Metric | Definition |
|---|---|
| Field-level F1 (primary) | precision/recall/F1 per field (items, subtotal, tax, total); `field_f1` is micro-averaged over a receipt's fields and then averaged over receipts (TRD §4.4) |
| Valid JSON % | share of receipts whose output validates against `Receipt` |
| Reconcile % | share where line items sum to subtotal and subtotal + tax ≈ total |
| Latency p50/p95 | per receipt, end to end |
| List cost / 1k receipts | tokens × dated price; OCR CPU time reported separately |

## 6. Functional requirements

| ID | Requirement |
|---|---|
| FR-1 | `cli data prepare` downloads CORD-v2 (revision pinned) and normalises ground truth into `Receipt`. |
| FR-2 | One Pydantic `Receipt` schema: `items[name, qty, unit_price, price]`, `subtotal`, `tax`, `total` (Decimal). |
| FR-3 | Pipeline A: RapidOCR text → Groq `gpt-oss-20b` → `Receipt`. Naive mode (no retries/checks) is kept as the baseline. |
| FR-4 | Pipeline B: image → Gemini 3.5 Flash-Lite (`gemini-flash-lite`) → `Receipt` (CORD images only — never user uploads). |
| FR-5 | Retry on schema failure (≤ 2 tries) with the validation error sent back; arithmetic check; `needs_review` flag. |
| FR-6 | Every model response cached to disk so re-running the eval costs nothing. |
| FR-7 | Scorer with order-insensitive line-item matching; failure export; 20–30 failures grouped by cause. |
| FR-8 | Gradio Space: Results tab, Try it (upload → A; sample receipts → A and B side by side), Failures tab. |
| FR-9 (stretch) | Pipeline C: Qwen2.5-VL-3B on Kaggle, predictions imported into the same table. Also `C-local` (added 2026-10-22 on request): Qwen3-VL-4B-Instruct on the local CPU via Ollama, same table. |

## 7. Acceptance criteria (project level)

- `reports/results.csv` has naive A, engineered A and B (and C if done) on the same 100 test receipts.
- README opens with F1 / valid JSON / reconcile rate / latency / cost per pipeline.
- `reports/failures.csv` has ≥ 20 labelled failures grouped by cause (OCR misread, wrong item split, hallucinated item, number format, …).
- Space live; its URL in the README.

**Resume bullet (filled 2026-10-27 from `reports/results.md`, 100 held-out CORD-v2 receipts):**
Built a receipt-to-JSON pipeline comparing OCR+LLM and Gemini vision on CORD-v2; the vision model
reached 0.957 field-level F1 against 0.846 for OCR+LLM, with schema retries cutting invalid outputs
from 6% to 2%, at $0.98 per 1k receipts (list price) against $0.39.

_Template: Built a receipt-to-JSON pipeline comparing OCR+LLM and Gemini vision on CORD-v2; reached
[X] field-level F1, with schema retries and arithmetic checks cutting invalid outputs from [A]% to
[B]% at [cost] per 1k receipts. (The arithmetic checks set a review flag; they do not change the
output, so the filled bullet credits the retries only.)_

**Interview questions to prepare**
- Why did the vision model beat (or lose to) OCR+LLM, and on which fields?
- How do you score a line-item list where the model splits or merges items?
- When would you route a receipt to a human?
- How would this scale to 1M invoices a month, and what would it cost?

## 8. Future improvements (after the short-term tasks)

v1 built a database/service layer that v2 removed in M0.R (2026-10-22) to stay lean. These come
back only after MF.1 ships, and each one is a PRD change the human approves first (BACKEND_SCHEMA §S5).
They should be rebuilt properly then, not restored as-is. The v1 code is in git history for reference.

| ID | Improvement | v1 reference (git) |
|---|---|---|
| FI-1 | PostgreSQL 16 + pgvector persistence for runs, results and `llm_call` rows, with Alembic migrations | `2e15449` — `db/`, `migrations/`, `alembic.ini` |
| FI-2 | Database sink for live LLM-call accounting alongside the JSONL sink | `983410f` — `DatabaseSink` in `llm/sinks.py` |
| FI-3 | FastAPI service exposing the pipelines as an HTTP API | `2e15449` — `api/` (package stub only) |
| FI-4 | Docker image and Compose stack (app + DB) for local and hosted deployment | `2e15449` — `Dockerfile`, `.dockerignore`, `docker-compose.yml` |
| FI-5 | Docker-backed integration tests (testcontainers Postgres) | `2e15449`, `983410f` — `tests/integration/` |

---

## S1. Portfolio done bar (applies to every project in this portfolio)

Cut scope before cutting any of these. The project is done when it clears all six:

| # | Criterion | Concrete check for this repo |
|---|---|---|
| 1 | A measured result on a fixed test set | The eval split (ids + manifest hash) is committed or reproducibly downloadable; no metric is reported without it. Never "looks good". |
| 2 | A baseline you beat — and an honest note where you didn't | The results table contains at least one simple baseline; the README says where it wins |
| 3 | Latency and cost measured | p50/p95 latency and list-price cost per 1k requests, **even when the run was free** |
| 4 | A short failure list | 20–30 errors grouped by cause, with what each fix bought (before/after) |
| 5 | It runs somewhere a stranger can click | The public artefact in the header table exists and its link is in the README |
| 6 | A README that opens with the results table | First screen = a number a hiring manager can react to; then architecture, limitations, "what I'd do differently", reproduce steps |

Resume bullets carry `[brackets]` until filled — **only with numbers you measured**.

## S2. Constraints shared by the portfolio

- **Zero paid services.** Groq free plan, Gemini API free tier, local models (Ollama / llama.cpp on
  CPU, 1–4B at Q4), Kaggle free GPUs (T4/P100, ~30 h/week), Hugging Face Hub + Spaces (free CPU).
  Nothing that needs a payment card to run the core experiments.
- **Free-tier limits decide sample sizes.** LLM methods run on fixed 200–300-item subsets (Groq is
  roughly 1K requests and ~200K tokens per model per day); non-LLM methods run on the full test set
  and are *also* scored on the subset for a fair comparison. The README says so.
- **Public data only**, licences respected (no redistribution where prohibited — ship download
  scripts instead). Gemini's free tier may use submitted data: send it public dataset content only.
- **No employer code, prompts or data** in any repository.
- **Developer machine:** Windows, Intel i5 12th gen, 16 GB RAM, no CUDA GPU. Anything GPU-bound
  runs on Kaggle as a notebook the human runs.
- **Lean stack (v2):** no database, no Docker requirement, no web framework unless the product
  needs one. Results live in files (`runs/`, `reports/`) under git.

## S3. Out of scope for every project (v2)

- User accounts, authentication, multi-tenancy, billing.
- Storing user-submitted content (uploads and pasted text are processed in memory only).
- Databases, Docker Compose stacks, mobile apps.
- Paid observability, vector-DB or hosting products.
