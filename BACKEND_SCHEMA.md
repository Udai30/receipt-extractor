# Backend Schema — Receipt-to-JSON Extractor

> A receipt photo goes in; validated JSON (line items, prices, totals) comes out — OCR+LLM vs a vision LLM, compared on accuracy, latency and cost.

Project documentation: [PRD.md](PRD.md) · [TRD.md](TRD.md) · [APPFLOW.md](APPFLOW.md) · [UI_UX_DESIGN.md](UI_UX_DESIGN.md) · **BACKEND_SCHEMA.md** · [IMPLEMENTATION_PLAN.md](IMPLEMENTATION_PLAN.md) · [WORKLOG.md](WORKLOG.md) · [IMPLEMENTATION_PROMPTS.md](IMPLEMENTATION_PROMPTS.md) · [CLAUDE.md](CLAUDE.md) · [PROMPT_PLAYBOOK.md](PROMPT_PLAYBOOK.md)

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

## 1. Domain contracts

### 1.1 `Receipt`
| Field | Type | Notes |
|---|---|---|
| items | list[`Item`] | may be empty |
| subtotal | Decimal \| null | |
| tax | Decimal \| null | |
| total | Decimal | required |
| currency | str | default `IDR` for CORD |

`Item`: `name: str`, `qty: Decimal | null`, `unit_price: Decimal | null`, `price: Decimal`.

Implemented in `core/receipt.py` (M1.1): models are strict (`extra="forbid"`), frozen and strip
whitespace; ≤ 500 items, names ≤ 300 chars, |amount| < 10¹⁵. Amount fields accept `Decimal`/`int`,
printed text (through `core.amounts.parse_amount`) or a JSON float (via its shortest `repr`);
booleans, NaN/∞ and non-amount text are rejected. `currency` is a 3-letter upper-case code.

**`parse_amount()` rules.** `None`/blank/`-` → `None`. Currency prefixes (`Rp`, `Rp.`, `IDR`, `$`, …),
`@`, `*`, `[…]` are stripped; leading `-`, trailing `-` and `(…)` mean negative. When both `.` and
`,` occur the last is the decimal separator; with one kind it is a thousands separator if repeated
or followed by exactly three digits (`1.000` = 1000), else decimal; spaces are thousands separators.
Decimals have 1–2 digits and groups exactly three; anything else raises `AmountParseError`.

**CORD gold mapping** (`data/cord_mapping.py`, `mapping_version` 1):
- `menu` entries (dict or list) → items: `nm` → name (word lists joined), `cnt` → qty (`2`, `2x`,
  `x2`, `*2`, `1Prs`), `unitprice` → unit_price, `price` → price; without `price`: `itemsubtotal`,
  else `unitprice × cnt`; entries with no derivable price are dropped (modifier lines).
- `sub` entries with a printed price (incl. `0`) become items after their parent; `sub` entries
  without a price, or with a price but no name, are dropped.
- `sub_total.subtotal_price` → subtotal; `sub_total.tax_price` → tax (several tax lines summed);
  `total.total_price` → total, else derived from `total_etc`, `cashprice − changeprice`,
  `creditcardprice`, `emoneyprice` (in that order).
- Unmappable (reported by `cli data stats`, no gold file, not a case): no total derivable, a
  required amount unparseable or ambiguous, or a priced item without a name. Every derivation
  or drop is a per-case note in the manifest. At revision `7f0115a…`: 0 unmappable in test and
  validation.

### 1.2 `ExtractionResult` (`ResultRow.prediction`)
`pipeline, receipt (Receipt | null), schema_valid, attempts, reconciled, needs_review, reconcile_diff,
ocr_text_chars, stage_latency_ms{ocr, llm, validate}`.

Implemented in `core/extraction.py` (M1.3), strict and frozen:
| Field | Type | Notes |
|---|---|---|
| pipeline | str | variant name: `A-naive`, `A`, `B`, `C-local` (later `C`) |
| receipt | `Receipt` \| null | null when invalid or nothing to structure |
| schema_valid | bool | `receipt` is not null |
| attempts | int | LLM calls: 0 (no OCR text), 1, or up to 3 (≤ 2 repairs, M2.2) |
| reconciled | bool \| null | arithmetic check passed (TRD §4.3); null for `A-naive` or without a valid receipt |
| reconcile_diff | Decimal \| null | signed diff of the failing check (item sum − subtotal if that fails, else (subtotal or Σ items) + tax − total); 0 when reconciled |
| needs_review | bool \| null | invalid after retries, nothing extracted, or not reconciled; null for `A-naive` |
| failure | `ocr_empty` \| `schema_invalid` \| `truncated` \| null | why `receipt` is null |
| model_alias, prompt_version | str \| null | |
| ocr_text_chars, ocr_lines, ocr_truncated | int, int, bool | OCR metadata (A only) |
| image_width, image_height | int \| null | the image as sent to a vision model, after downscaling (B, C-local) |
| image_tokens_in | int \| null | the part of `tokens_in` spent on the image, as reported by the provider (B; null when not reported) |
| stage_latency_ms | `{ocr, prepare, llm, llm_wait, validate}` (ms \| null) | `prepare` = B's decode + downscale + re-encode; `llm` excludes free-tier pacing waits, which are `llm_wait` (TRD §S3) |
| tokens_in, tokens_out, list_cost_usd | int, int, Decimal string | summed over the receipt's calls |
| cache_hit | bool | served from the disk cache (latencies then ≈ 0) |
| raw_text | str \| null | the model reply, only when it did not validate (≤ 8,000 chars) |

The OCR text is **not** serialised in `ExtractionResult`; eval tasks store it as
`ResultRow.details.ocr_text` (CORD is public; the UI never persists it).

### 1.3 `ResultRow.details`
`field_scores{items_p, items_r, items_f1, subtotal_ok, tax_ok, total_ok, field_f1, valid_json,
reconciled}`, `item_matches{pairs[{gold, pred, name_similarity, name_ok, price_ok, match}],
unmatched_gold[], unmatched_pred[]}` (indices into the gold / predicted `items`), plus
`metrics{name: value}`. Written by the scorer (`eval/scorer.py`, M1.2) for every scored row; P/R/F1
are `null` when not applicable (TRD §4.4). Pairs are assigned items that agree on the name or the
price; `match` = both.

## 2. Files

- `data/cord/manifest.json` — `format_version, dataset, revision, license, mapping_version,
  splits{<split>: {n_rows, n_gold, ids, files[{path, size, sha256}], unmappable[{id, reason}],
  amount_formats{label: n}, mapping_notes{note: n}, hash}}, cases{<id>: {image, image_sha256,
  gold, gold_sha256, notes}}, hash`. A split's `hash` covers revision, mapping version, ids and
  every case's image/gold SHA-256; it is the dataset manifest hash in run ids.
- `data/cord/gold/<id>.json` — normalised gold `Receipt` (money as strings); ids are
  `<split>-<image_id:04d>` (`test-0000` … `test-0099`).
- `data/cord/images/<id>.png|jpg` — receipt image bytes as published.
- `data/cord/splits/<split>.txt` — the split's ids, one per line.
- `data/cord/raw/data/*.parquet` — the pinned source files (verified by size + SHA-256).
- `configs/runs/{a-naive,a,b,c}.yaml` — run configs (test split, M2.3+);
  `configs/runs/{a-naive,a,b,c-local}-val10.yaml` — 10 validation receipts for prompt tuning
  (M1.3, M2.1).
- `data/imports/qwen_vl/` — Kaggle predictions + manifest (stretch).

---

## S1. Conventions (v2 has no database)

- Contracts are Pydantic v2 models in `src/<pkg>/core/` and `src/<pkg>/eval/`; files on disk are
  their JSON/JSONL serialisations. A model change and its file format change land together.
- Money is `Decimal` serialised as a string; times are UTC ISO-8601; ids are strings.
- Raw artefacts (`runs/`, `data/`) are gitignored; derived results (`reports/`) are committed.

## S2. Shared records

### `ResultRow` — one line of `runs/<run_id>/results.jsonl`
| Field | Type | Notes |
|---|---|---|
| case_id | str | unique within a run |
| status | `pass` \| `fail` \| `error` | `error` = exception, message kept in `details.error` |
| score | float \| null | the project's primary per-case metric |
| prediction | object | project-specific (§1) |
| gold | object | project-specific (§1) |
| details | object | `metrics{name: value}` (every configured metric), plus task details; `error` on error rows |
| latency_ms | int | wall clock for the case |
| wait_ms | int | part of `latency_ms` spent queued for free-tier pacing (budget, rate limiter, concurrency slot); 0 in rows before M2.3 |
| tokens_in / tokens_out | int | summed over the case's LLM calls |
| list_cost_usd | str (Decimal) | |

### `LLMCallRecord` — one line of `runs/<run_id>/llm_calls.jsonl`
`provider, model, alias, case_id, stage, tokens_in, tokens_out, image_tokens_in (null unless the
provider reports it), latency_ms, wait_ms, cache_hit, retries, schema_valid (null if no schema),
list_price_usd, actual_cost_usd, error (null on success), ts`.

### `summary.json`
`run_id, variant, dataset_name, dataset_split, sample_size, primary_metric,
metrics{name: {value, ci_low, ci_high, n}}, status_counts{pass, fail, error}, latency_ms{p50, p95, p99?}
(wall clock), service_latency_ms{p50, p95, p99?} (wall clock − `wait_ms`; absent before M2.3),
tokens{in, out}, list_cost_usd_per_1k, n_errors, started_at, finished_at`.
Written only when every selected case has a row. Error rows count as 0 for every metric; CIs are
95 % percentile bootstrap (seeded); p99 only from 500 cases.

### `manifest.json`
`run_id, resolved_config, dataset{name, revision, split, manifest_hash, ids_hash, n_cases}, git_sha?,
packages{name: version}, python, platform, started_at, kaggle?{notebook, gpu, gpu_seconds}`.
Written when the run starts and kept on resume.

### Kaggle import (`data/imports/<name>/`, read by `cli eval import-predictions`)
- `manifest.json`: `format_version (1), variant, dataset, sample, models, metrics, primary_metric,
  pass_threshold, seed, bootstrap, kaggle{notebook, gpu, gpu_seconds}, prediction_format
  (prediction | receipt_reply, default prediction), prompt_version?` — `dataset`/`sample` exactly as
  in a run config.
- `predictions.jsonl`: `case_id, prediction | error, latency_ms, tokens_in?, tokens_out?,
  finish_reason?, details?`. With `receipt_reply`, `prediction` is the model's raw reply text; the
  importer validates it against `Receipt` and stores an `ExtractionResult` (attempts 1, review flags
  set, `raw_text` kept when invalid, $0). Selected cases without a line become `error` rows;
  duplicates, a case id outside the sample (M2.4) or unknown keys reject the whole import before
  anything is written.

## S3. Reports (committed)

- `reports/results.csv` — one row per (variant, subset): run id, primary metric ± CI, secondary
  metrics, `p50_ms`/`p95_ms` (service latency: excluding free-tier pacing waits; wall clock for
  summaries written before M2.3), `wall_p50_ms`/`wall_p95_ms`, $/1k, notes. Drives the README
  table and the Space. Regenerates byte-identically from the same runs (tested).
- `reports/failures.csv` — `run_id, case_id, variant, status, score, failure_label, note, fixed_by`
  (labels hand-written; `note` is pre-filled with the error message; labels survive re-export).
- `reports/*.png` — `primary_metric.png`, `cost_per_1k.png`, regenerated by `cli report build`.

## S4. Error format (UI and any programmatic interface)

`{"error": {"code": "snake_case", "message": "plain language", "request_id": "…"}}`; provider and
internal errors never leak raw messages.

## S5. No-database rule

v2 deliberately has no database. If a future feature needs one, it is a PRD change first, and the
CLAUDE.md §2 rules apply: the agent writes migrations, the human applies them. The planned return of
Postgres, FastAPI and Docker is listed in PRD §8 (Future improvements).
