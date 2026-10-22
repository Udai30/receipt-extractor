# Worklog — Receipt-to-JSON Extractor

> A receipt photo goes in; validated JSON (line items, prices, totals) comes out — OCR+LLM vs a vision LLM, compared on accuracy, latency and cost.

Project documentation: [PRD.md](PRD.md) · [TRD.md](TRD.md) · [APPFLOW.md](APPFLOW.md) · [UI_UX_DESIGN.md](UI_UX_DESIGN.md) · [BACKEND_SCHEMA.md](BACKEND_SCHEMA.md) · [IMPLEMENTATION_PLAN.md](IMPLEMENTATION_PLAN.md) · **WORKLOG.md** · [IMPLEMENTATION_PROMPTS.md](IMPLEMENTATION_PROMPTS.md) · [CLAUDE.md](CLAUDE.md) · [PROMPT_PLAYBOOK.md](PROMPT_PLAYBOOK.md)

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

Format: **newest first**, one entry per task session (see CLAUDE.md §7).

```text
## YYYY-MM-DD — Task ID · Short title

**Done:** what was actually completed (incl. tests run + results, verification evidence)
**Files:** paths touched
**Decisions:** anything decided, and why
**Open:** anything unresolved — incl. "Human action:" items (Kaggle runs, publishing, accounts, secrets)
```

## Current state

| Item | Value |
|---|---|
| Current task | M1.2 — done · next: M1.3 |
| Last verified gate run | 2026-10-22 (M1.2): ruff check + format, mypy, pytest 367 passed / 5 skipped (4 live, 1 symlink), pip-audit — all green |
| Pending human actions | API keys in `.env`; delete the v1 DB lines from `.env`; see the entries below |

---

## 2026-10-22 — M1.2 · Field-level scorer with order-insensitive item matching

**Done:**
- Verified M1.1 first: commit `a53a775`, its WORKLOG entry, `core/receipt.py` + `core/amounts.py`, the `cord` dataset kind, `data/cord/` prepared (100 test + 100 validation gold); gates green at start (315 passed). Reused `Receipt` validation (and through it `parse_amount`) as the only normaliser, the metric registry, `canonical_json`, the task registry and the runner/importer.
- `eval/scorer.py`: `score_receipt(prediction, gold)` on the stored JSON forms. Prediction = `ExtractionResult` (its `receipt`) or a bare `Receipt`; anything that does not validate → `valid_json = 0`, scored as an empty receipt (never raises; invalid gold raises `ValueError`). Scalars `subtotal`/`tax`/`total`: `Decimal` equality after validation (TP / FP+FN / FP / FN / n/a). Items: Hungarian (`scipy.optimize.linear_sum_assignment`) on one lexicographic weight — full matches (name similarity ≥ 0.8 **and** equal price) ≻ name-only ≻ price-only ≻ Σ similarity; names NFKC + casefold + collapsed whitespace, rapidfuzz Indel ratio. Per-case `field_f1` = micro F1 over items + scalars.
- Registry metrics (bootstrap CIs, loaded lazily on first `get_metric`/`metric_names`): `field_f1`, `items_precision`, `items_recall`, `items_f1`, `subtotal_f1`, `tax_f1`, `total_f1`, `valid_json`, `reconcile`. Metrics may offer `explain()`; `runner.score_case` now also returns the scorer details, so the runner **and** the Kaggle importer write `details.field_scores` + `details.item_matches` (BACKEND_SCHEMA §1.3) next to `details.metrics`. One score per case: the metrics share an 8-entry LRU cache keyed by canonical JSON.
- `core/reconcile.py`: Σ items = subtotal (when printed) and subtotal (or Σ items) + tax = total, or subtotal = total with tax included; tolerance max(0.01, 0.5 % of total). Shared with M2.2's `needs_review`. Chosen from evidence on the gold: tolerance 0 → 79/100 test receipts reconcile, 0.5 %/1 %/2 % → 82 (no gain beyond 0.5 %), plus the tax-included reading → 84. The other 16 print service charges, discounts or rounding the schema has no field for (e.g. test-0004 discount, test-0014 service charge).
- `eval/oracle.py`: `gold` task (prediction = the case's gold, no params) + `configs/runs/gold.yaml` (CORD test, all 9 metrics, primary `field_f1`).
- **Acceptance evidence:** `uv run receipt_extractor eval run --config configs/runs/gold.yaml` → run `gold-9988f5dc0330`, 100/100, 0 errors, `field_f1 1.0000 [1.0000, 1.0000]`, 100 pass. `uv run receipt_extractor report build` → `reports/results.csv|md` + charts: `field_f1`, `items_f1`, `items_precision`, `items_recall`, `subtotal_f1` (n = 65), `tax_f1` (n = 41), `total_f1`, `valid_json` all **1.0000**; `reconcile` **0.8400** (the gold's own arithmetic, see above; not a comparison with gold). No row's `details.item_matches` has an unmatched item.
- Tests (52 new): `tests/unit/eval/test_scorer.py` (40) — hand-built **perfect, split item, merged items, hallucinated item, reordered items**, missing item, amount normalisation (`Rp 100.000` = `100000`), name noise, wrong price vs wrong name, duplicates one-to-one, full match preferred over name similarity and over two name-only pairs, empty item lists, the 5 scalar cases, `ExtractionResult` vs bare `Receipt`, 9 invalid prediction shapes, invalid gold; gold-vs-gold on the 3 real CORD fixtures and as a Hypothesis property (150 random receipts, items shuffled → 1.0); order-insensitivity + bounds property; registry and `case_details` shape; runner end to end (`jsonl` dataset + `gold` task → details written, summary 1.0); the 500 × 500 worst case. `tests/unit/core/test_reconcile.py` (12) — tax on top/included, no subtotal, discount, service charge, missing item, empty, signed diffs, tolerance edge.
- Mutation check (temporary edits, reverted): ignoring the price in a match → 2 tests fail. Removing the full-match tier from the weight first survived every test, so `test_one_full_match_beats_two_name_only_pairs` was added; it now fails on that mutant as it should.
- Gates: `ruff check` clean, `ruff format --check` clean (71 files), `mypy src` clean (39 files, strict, with `scipy-stubs`), `pytest -q` **367 passed, 5 skipped** (4 live, 1 symlink), `pip-audit` no known vulnerabilities. Coverage: `eval/scorer.py` 98 %, `core/reconcile.py` and `eval/oracle.py` 100 %.
- Security: no new external input or trust boundary; the scorer reads predictions/gold already in memory. Predictions are untrusted model output: they go through the strict `Receipt` model (≤ 500 items, names ≤ 300 chars, |amount| < 10¹⁵), never `eval`/pickle; malformed values become `valid_json = 0` instead of exceptions. Details store indices and similarities only (names stay in `prediction`/`gold`). No model calls, so the Gemini/user-upload invariant is untouched.
- Performance: a typical receipt (11 items) scores in 0.13 ms; the schema's worst case (500 × 500 items, 300-char names) in 0.28 s (rapidfuzz `cdist` + scipy LSA, O(n³)); the 100-receipt gold run took 0.28 s wall clock. The LRU cache holds ≤ 8 scored cases.
**Files:** added `src/receipt_extractor/core/reconcile.py`, `src/receipt_extractor/eval/{scorer,oracle}.py`, `configs/runs/gold.yaml`, `tests/unit/eval/test_scorer.py`, `tests/unit/core/test_reconcile.py`, `reports/{results.csv,results.md,primary_metric.png,cost_per_1k.png}`; modified `src/receipt_extractor/eval/{metrics,runner,importer,tasks}.py`, `pyproject.toml` + `uv.lock` (`scipy` 1.18.1, `rapidfuzz` 3.14.6; dev `scipy-stubs` 1.18.1.1), `TRD.md` §4.4/§S1/§S4, `BACKEND_SCHEMA.md` §1.3, `PRD.md` §5 (how `field_f1` is averaged), `README.md`, `WORKLOG.md`.
**Decisions:**
- `scipy` for the Hungarian step rather than a hand-written one (reference implementation, rectangular matrices, O(n³)); `rapidfuzz` over `difflib` because the schema allows 500 × 500 items with 300-char names, where `difflib` would take seconds to minutes.
- A match needs name **and** price, so splits and merges lose exactly the lines that no longer agree. No partial credit (e.g. split prices summing to the gold price): strict and predictable for every variant. Near misses stay visible in `item_matches` (`name_ok`/`price_ok`) for failure analysis (M3.1).
- `field_f1` is micro F1 within a receipt, then averaged over receipts: the registry aggregates per-case values and the bootstrap CI needs one number per receipt. PRD §5 and TRD §4.4 updated to say so. Per-field F1 is likewise per receipt (1/0, `None` when the field is absent on both sides).
- Invalid output is scored as an empty receipt (0 on every field the gold has) instead of `None`, so an invalid answer can never raise an average.
- `reconcile` judges the prediction, not agreement with gold, so gold-vs-gold shows 0.84 there; "1.0 everywhere" holds for every comparative metric. Accepting tax-included totals follows the CORD receipts (e.g. test-0000: subtotal 60000 = total, tax 5455 shown).
- `item_matches` stores indices, not names (names are already in the row).
- The `gold` row in `reports/results.csv` is the committed calibration row and stays next to the real variants (it shows the scorer's ceiling and the gold's reconcile rate).
**Open:**
- `reports/` now holds its first committed outputs (gold row only); M2.3 adds `A-naive`, `A`, `B`.
- The 0.8 name threshold is untested against real pipeline output; revisit in M3.1 on validation if OCR spelling misses dominate (changing it means re-scoring stored runs, no new model calls).
- No human action required for M1.2. Nothing committed.

---

## 2026-10-22 — M1.1 · CORD-v2 download and normalisation to `Receipt`

**Done:**
- Verified M0.4 first (WORKLOG entry + code: `eval/` runner, `datasets.py` with `synthetic`/`jsonl`, path confinement, io helpers; gates green at start). Reused `eval.paths.confine`/`relative_posix`, `eval.io` (`sha256_file`, `canonical_json`, atomic writes), `eval.errors.DatasetError` and the `Dataset` protocol.
- **Dataset card checks (evidence):** Hub API for `naver-clova-ix/cord-v2` → sha `7f0115a4b758a71d6473b8d085751692da2fef98` (last modified 2022-07-19), tag `license:cc-by-4.0`, card front matter `license: cc-by-4.0`, `dataset_infos.json` "Creative Commons Attribution 4.0 International License", not gated. **Licence: CC-BY-4.0 confirmed.** Split sizes: `dataset_infos.json` train 800 / validation 100 / test 100; confirmed independently by reading every parquet footer at the pinned revision (train 4 × 200 = **800**, validation **100**, test **100**), and the downloaded validation/test files stream exactly 100 rows each (`prepare` enforces the expected count).
- `core/amounts.py`: `parse_amount()` (Decimal; `1.000`, `1,000.50`, `9.500,00`, `2 600`, `Rp`/`Rp.`/`IDR`/`$` prefixes, `@`/`*`/`[…]` markers, `-x`/`x-`/`(x)` negatives, blanks → `None`, 64-char cap, strict `AmountParseError` otherwise) and `amount_format()` (value-free format label for stats). `core/receipt.py`: strict frozen `Receipt`/`Item` per BACKEND_SCHEMA §1.1, money serialised as JSON strings, exact round trip.
- `data/cord_mapping.py` (pure): CORD `gt_parse` → `Receipt` with recorded notes and an `issue` for unmappable records (rules in BACKEND_SCHEMA §1.1). `data/cord.py`: `prepare_cord` downloads via `huggingface_hub` at the pinned revision, verifies each file's size + LFS SHA-256 (pinned in code; a mismatch deletes the file), streams parquet in 8-row batches, writes `images/`, `gold/`, `splits/<split>.txt`, `manifest.json` atomically; idempotent re-runs. `data/stats.py` + CLI `data prepare [--split …]` (default validation + test; train opt-in, 1.8 GB) and `data stats [--split test]`. New dataset kind `cord` in `eval/datasets.py` (gold verified against the manifest SHA-256 on every load; unmappable receipts are not cases).
- **Acceptance evidence:** `uv run receipt_extractor data prepare` → `validation: 100 rows, 100 gold, 0 unmappable`, `test: 100 rows, 100 gold, 0 unmappable`; `data/cord/gold/` holds 100 `test-*.json` (+100 validation), 200 images. Manifest hash `06b5ba17ef83…` (test split hash `c7c2a8a8758e…`), byte-identical on a second run (files reused, not re-downloaded). All 100 test cases load through `open_dataset({kind: cord, split: test})`, every image path exists.
- `data stats --split test`: items/receipt min 1, median 2, mean 2.69, max 11; subtotal on 65, tax on 41 receipts; items sum = subtotal or total on 96/100 (test-0031/-0033 print a discount, -0039 discounted item subtotals, -0061 a subtotal below the items — kept as printed). 16 distinct amount formats; most common `9,999` 313, `9.999` 114, `9` 59, `9.99` 15, `Rp 9.999` 8. Mapping notes: 16 priceless modifiers dropped, 2 price from itemsubtotal, 2 priceless entries dropped, 1 unitprice × cnt, 5 derived totals (2 cashprice, 1 cashprice − changeprice, 1 total_etc, 1 creditcardprice), 1 summed tax list. Validation: mean 2.34 items, 15 formats, 0 unmappable.
- Tests (137 new): `tests/unit/core/test_amounts.py` — Hypothesis property tests (round trip across dot/comma/space thousands, comma/dot decimals, 10 prefixes, minus/paren/trailing negatives; separator invariance; negation symmetry; arbitrary text never raises anything but `AmountParseError`; labels hide values) + example tables. They found three real bugs, fixed before completion: `1e5` parsed as 1.5, `12,3456` accepted, `1 000,00` rejected. `test_receipt.py` (Decimal/JSON round trip, floats via repr, rejects bool/NaN/∞/overflow/extra fields). `tests/unit/data/test_cord_mapping.py` — **golden test on 3 real CORD test records** (`tests/fixtures/cord/test-0002|0009|0099.json`: priced add-on, derived total, summed tax list) + every rule and unmappable reason. `test_cord_prepare.py` — synthetic parquet + fake downloader: outputs and manifest, idempotent re-run, multi-split merge, SHA mismatch removal, malformed rows, row-count check, path confinement, stats, `cord` dataset kind + gold tampering, CLI, Hub errors never echo their message/token.
- Gates: `ruff check` clean, `ruff format --check` clean (66 files), `mypy src` clean (36 files), `pytest -q` **315 passed, 5 skipped** (4 live, 1 symlink), `pip-audit` no known vulnerabilities. Coverage: `core/amounts` 98 %, `core/receipt` 100 %, `data/*` 90–91 %, `eval/datasets` 89 %.
- Security: the only external input is the pinned Hub download — revision and per-file SHA-256 pinned, so a changed or tampered file is rejected; parquet read with pyarrow (no pickle), ground truth `json.loads` with a 4 MB cap, images ≤ 32 MB and PNG/JPEG magic only (never decoded), case ids built from validated integers, all paths confined to `data/`; Hub errors reported by type only (no URLs/headers/token); optional `HF_TOKEN` read as `SecretStr`. Gemini/user-upload invariant untouched (no model calls).
- Performance: download once (~476 MB, ~1 min here); normalisation streams 8 rows at a time (memory ≈ a few images); a full re-run (re-hash both files + re-normalise 200 receipts, no download) took 2.6 s wall clock. `data stats` reads 100 small JSON files.
**Files:** added `src/receipt_extractor/core/{amounts,receipt}.py`, `src/receipt_extractor/data/{__init__,cord,cord_mapping,stats}.py`, `tests/unit/core/{__init__,test_amounts,test_receipt}.py`, `tests/unit/data/{__init__,conftest,test_cord_mapping,test_cord_prepare}.py`, `tests/fixtures/cord/test-{0002,0009,0099}.json`; modified `src/receipt_extractor/cli.py` (`data prepare`, `data stats`), `src/receipt_extractor/eval/{datasets,config,io}.py` (`cord` kind, its path normalisation, `atomic_write_bytes`), `pyproject.toml` + `uv.lock` (`huggingface-hub` 1.33.0 (was transitive), `pyarrow` 26.0.0, dev `hypothesis` 6.170.5; mypy override for untyped pyarrow), `.gitignore` (`.hypothesis/`), `TRD.md` §4.1/§S1/§S2/§S4, `BACKEND_SCHEMA.md` §1.1/§2, `README.md`, `WORKLOG.md`.
**Decisions:**
- `huggingface_hub` + `pyarrow` instead of `datasets` + pandas (TRD §S1 updated): explicit files under `data/cord/`, a pinned per-file SHA-256, streamed reading and no extra cache dir; `datasets` adds nothing here.
- Default `data prepare` = validation + test (the benchmark and tuning splits); train (1.8 GB, PRD §4 few-shot pool) is opt-in with `--split train`; its size was verified from the remote footers instead.
- Images are extracted to `data/cord/images/` (as published, not re-encoded) so pipelines read one file per case instead of a 240 MB parquet.
- Gold mapping rules (BACKEND_SCHEMA §1.1): priced `sub` lines are items, unpriced ones modifiers; missing `total_price` (5 test receipts) derived from the paid amount with a note rather than dropping receipts; several tax lines summed (test-0099); a priced `sub` line without a name is a repeated price (validation-0086). Each derivation is a per-case note, so later failure analysis can separate derived-gold cases.
- `parse_amount` accepts 1–2 decimals only and reads a single `.`/`,` + 3 digits as thousands (`1.000` = 1000, the CORD reading); floats are accepted via `repr` so model replies with JSON numbers validate exactly.
- The 3 golden fixtures are committed excerpts of CORD-v2 (CC-BY-4.0 allows redistribution with attribution; source line in each file and the README).
**Open:**
- `data/` is gitignored; anyone reproducing runs `uv run receipt_extractor data prepare` (≈ 480 MB). Unauthenticated Hub downloads work (a rate-limit warning is printed); `HF_TOKEN` in `.env` is optional.
- Removed the stale, gitignored `.coverage` file while measuring coverage (regenerable).
- No human action required for M1.1. Nothing committed.

---

## 2026-10-22 — M0.4 · Evaluation runner, reports and prediction import (lean)

**Done:**
- Verified M0.R first: no DB code/deps, `llm/` intact, `eval/` was an empty stub; gates green (116 passed). Reused `llm.errors.BudgetExhaustedError` (pause), `llm.errors.sanitize_error_text` (error rows) and `llm.sinks.LLM_CALLS_FILENAME` (tasks get `runs/<run_id>/llm_calls.jsonl` via `CaseContext`).
- New package `src/receipt_extractor/eval/`:
  - `config.py`: strict Pydantic `RunConfig` (variant, task + params, dataset, sample ids/size/seed, models, metrics, primary metric, pass threshold, seed, bootstrap, concurrency, case timeout, runs_dir); `yaml.safe_load`, 1 MB cap, unknown keys rejected. Run id = `<variant>-<12 hex>` of SHA-256(resolved config + dataset manifest hash).
  - `paths.py`: every config/CLI path resolved with symlinks followed and confined (configs → `configs/`, datasets/imports → `data/`, runs → `runs/`, reports → `reports/`).
  - `runner.py`: queue of size `concurrency` + `concurrency` workers, immediate flushed JSONL append, resume by case id, partial-last-line repair, per-case exception → `status=error` (sanitised message, no traceback), per-case timeout, `BudgetExhaustedError` → `paused`, per-run OS file lock (msvcrt/fcntl; released by the OS on crash), `summary.json` on completion, `manifest.json` at start (git SHA read from `.git` files, package versions, dataset hashes).
  - `metrics.py`: registry with `Metric.compute(prediction, gold)` on stored JSON forms, `exact_match` built in, seeded percentile bootstrap, linear-interpolation quantiles.
  - `summary.py`, `report.py` (`results.csv|md`, `primary_metric.png`, `cost_per_1k.png`; `failures.csv` keeping hand labels; spreadsheet-formula neutralising), `importer.py` (Kaggle `manifest.json` + `predictions.jsonl` → scored run dir), `datasets.py` (`synthetic`, `jsonl` with byte-offset index), `tasks.py` (task registry, `CaseContext`/`CaseOutput`), `toy.py` + `configs/runs/toy.yaml`.
- CLI: `eval run --config … [--concurrency N]` (exit 0 completed / 3 paused / 130 interrupted / 1 error), `eval import-predictions <dir>`, `report build`, `report failures [--run-id …]`.
- Tests (`tests/unit/eval/`, 62 new): resume skips finished ids after a simulated crash; partial last line cut on resume; case exception → `status=error`, run continues, key redacted; budget pause → resume; concurrency bound; in-memory cases ≤ 2 × concurrency over 200 cases; timeout → error; second session refused by the lock; bootstrap CI deterministic with a fixed seed (and different with another seed); summary counts errors as 0 and dedupes; fake Kaggle import → valid run dir (20 rows, missing/errored cases, $0, kaggle block, idempotent re-import), malformed imports rejected; path traversal (`..`, absolute, other drive, `configs/`↔`data/` crossing, runs_dir escape, import dir, report `--out`; symlink case skips on Windows without privilege); `!!python/object` YAML rejected; CLI acceptance (interrupt → resume → `reports/results.csv`).
- Gates: `ruff check` clean, `ruff format --check` clean (53 files), `mypy src` clean (30 files), `pytest -q` **178 passed, 5 skipped** (4 live, 1 symlink), `pip-audit` no known vulnerabilities. Coverage of `eval/`: 91 %.
- **Acceptance evidence** (scratch project dir, real process): toy config with 300 ms/case, concurrency 1, process **SIGKILLed after 11 rows** → re-run: `20/20 done (9 this time, 0 errors)`, 20 unique case ids, `exact_match 0.7500 [0.5500, 0.9500]` — identical to an uninterrupted run of the same config → `report build` wrote `results.csv` row `toy,toy,synthetic,toy-6fdb552f8696,20,exact_match,0.7500,0.5500,0.9500,306,309,0.001900,1,`. Charts inspected visually.
- Security: safe_load only; no pickle/eval; strict schemas with size caps on config (1 MB), JSON files and JSONL lines (16 MB); paths confined after symlink resolution; error text sanitised; CSV formula injection neutralised; run lock prevents interleaved writers. No model calls in M0.4, so the Gemini/user-upload invariant is untouched.
- Performance: runner memory O(concurrency) for cases/results; O(n) only for case-id strings (sample + finished set). Summary/report keep one compact record per case (needed for bootstrap/percentiles), never predictions. Bootstrap is O(resamples × n) (1000 × 100 ≈ instant).
**Files:** added `src/receipt_extractor/eval/{errors,paths,io,records,datasets,config,metrics,tasks,toy,summary,runner,importer,report}.py`, `configs/runs/toy.yaml`, `tests/unit/eval/{__init__,conftest,test_runner,test_config_paths,test_metrics_summary,test_import_report,test_cli_eval}.py`; modified `src/receipt_extractor/cli.py`, `pyproject.toml`, `uv.lock` (matplotlib 3.11.2 + numpy, pillow, kiwisolver, …), `TRD.md` §S4, `APPFLOW.md` J3/§S1/§S3, `BACKEND_SCHEMA.md` §S2/§S3, `UI_UX_DESIGN.md`, `IMPLEMENTATION_PLAN.md` (command names in M2.3/M2.4/M3.1 only; no reordering), `README.md`, `.gitignore`, `WORKLOG.md`.
**Decisions:**
- CLI names: `report` stays a group (M0.1), so the commands are `report build`, `report failures` and `eval import-predictions`; TRD/APPFLOW/plan updated.
- `concurrency` and `runs_dir` are not part of the run id (they do not change what is measured), so a resume with another concurrency continues the same run.
- Error rows count as 0 for every metric in the summary (otherwise errors flatter a variant); `n_errors` is reported next to it. Error rows stay "finished" on resume (spec: resume skips finished ids); only budget pauses leave cases to re-run.
- Run id is a hash, but `llm.CallTags.run_id` is a UUID: `CaseContext.run_uuid = uuid5(namespace, run_id)` bridges them without touching `llm/`.
- Reports show the latest completed run per (variant, dataset, split); paused runs (no `summary.json`) never appear.
- Kaggle imports: $0 list cost (TRD §S5), GPU-seconds in the manifest and the report notes; selected cases without a prediction become error rows so a partial notebook run cannot score a smaller sample.
- Charts: matplotlib (Agg, imported lazily), dot + CI whisker and single-series bars in the dataviz reference palette; no dual axes.
- Added runtime dependency `matplotlib` (TRD §S4 requires PNG charts). No global installs.
- `.gitignore`: `data/` and `runs/` anchored to the root (`/data/`, `/runs/`); the unanchored `runs/` also ignored `configs/runs/`, which holds the committed run configs (TRD §S2).
**Open:**
- `failures.csv` keeps rows of runs no longer reported (to protect hand labels); prune by hand if needed.
- Running `configs/runs/toy.yaml` inside this checkout and then `report build` would add a `toy` row to the committed `reports/`; the acceptance run was done in a scratch directory, so `reports/` is unchanged.
- Nothing committed.

---

## 2026-10-22 — M0.R · Re-baseline the ported v1 code to the lean stack

**Done:**
- Starting state (SETUP entry below): 148 tests collected → 126 passed, 22 skipped (18 Docker-only DB tests, 4 live); ruff, mypy clean.
- **Removed (database layer):** `src/receipt_extractor/db/` (`models.py`, `repositories.py`, `session.py`), `migrations/` (`env.py`, `script.py.mako`, `versions/0001_baseline.py`), `alembic.ini`, `docker-compose.yml`; `DatabaseSink` (+ its `logging`/SQLAlchemy imports) from `llm/sinks.py` and its export from `llm/__init__.py`; settings `DATABASE_URL`, `DB_POOL_SIZE`, `DB_MAX_OVERFLOW`, `DB_POOL_TIMEOUT_S` from `config.py`; `POSTGRES_*`, `API_PORT`, `DATABASE_URL`, `DB_*` from `.env.example`; deps `alembic`, `asyncpg`, `sqlalchemy[asyncio]` and dev dep `testcontainers[postgres]` (uv also dropped `mako`, `wrapt`, `docker`, etc.); the `integration` pytest marker and the `migrations/versions` ruff exclude.
- **Removed (API/Docker):** `src/receipt_extractor/api/` (stub package), `Dockerfile`, `.dockerignore`; the `ingest` CLI group.
- **Removed tests (29):** `tests/integration/` (18: DB sink 2, migrations 3, repositories 13), `tests/unit/test_db_session.py` (6), `tests/unit/test_deploy_files.py` (5).
- **Changed tests (DB references only):** `tests/conftest.py` keeps `clean_env` and the live-test gate, drops the testcontainers fixtures. `test_config.py`: the "missing DATABASE_URL" and "pool bounds" tests removed (no required/DB settings left); added "v1 DB variables in `.env` are ignored"; `.env.example` must now match `Settings` exactly. `test_cli.py`: `ingest` removed from the group lists; added "ingest group is gone". `test_cli_llm.py`: no `DATABASE_URL` in the fixture; the config-error test now uses an invalid `LOG_LEVEL`. All `llm/` tests unchanged.
- **Added:** `src/receipt_extractor/ui/__init__.py` (stub `launch()` raising `NotImplementedError` until M3.2; no Gradio dependency yet), `app.py` (Space entry point → `receipt_extractor.ui.launch`), `tests/unit/test_no_database.py` (fresh interpreter, SQLAlchemy/asyncpg/alembic/psycopg/testcontainers blocked, every `receipt_extractor.*` module imports), `tests/unit/test_ui_stub.py`.
- README quickstart rewritten (no Docker/alembic); `pyproject.toml` description updated; stale `llm_call`/Postgres docstrings in `llm/types.py` and `llm/sinks.py` point to BACKEND_SCHEMA §S2 JSONL.
- **Gates after:** `ruff check` clean, `ruff format --check` clean (33 files), `mypy src` clean (17 files), `pytest -q` **116 passed, 4 skipped** (live only; 120 collected = 148 − 29 removed − 1 config + 2 new files), `pip-audit` no known vulnerabilities. `uv run receipt_extractor --help` lists data, eval, report, llm. `python -c "import sqlalchemy"` → ModuleNotFoundError in the venv.
- Security: `pip-audit` flagged certifi 2026.10.18 (PYSEC-2026-4320); bumped to 2026.10.19 with `uv lock --upgrade-package certifi`. Secret masking still covered by `test_secrets_never_appear_in_repr_or_str`.
**Files:** deleted as listed above; modified `.env.example`, `README.md`, `pyproject.toml`, `uv.lock`, `src/receipt_extractor/{cli.py,config.py,llm/__init__.py,llm/sinks.py,llm/types.py}`, `tests/conftest.py`, `tests/unit/{test_cli.py,test_cli_llm.py,test_config.py}`, `PRD.md`, `BACKEND_SCHEMA.md`, `IMPLEMENTATION_PLAN.md`; added `app.py`, `src/receipt_extractor/ui/__init__.py`, `tests/unit/{test_no_database.py,test_ui_stub.py}`.
**Decisions:**
- Dockerfile/.dockerignore removed: the Space uses the Gradio SDK, not the Docker SDK.
- Human decision (2026-10-22): remove the DB/API/Docker layer now, but keep it as **Future improvements** to rebuild properly after the short-term tasks. Recorded as PRD §8 (FI-1…FI-5, with the v1 commits `2e15449` / `983410f` as reference), linked from BACKEND_SCHEMA §S5 and IMPLEMENTATION_PLAN. Bringing any back is a PRD change the human approves first.
- `Settings` now has no required variables, so `--help`, tests and the stub app run without a `.env`; keys are checked by the LLM layer when a call needs them (unchanged behaviour).
- `tests/e2e/` (empty package, `e2e` marker) kept for M3.2.
- `.gitignore` needed no change (already keeps only `IMPLEMENTATION_PROMPTS.md`/`PROMPT_PLAYBOOK.md` local).
**Open:**
- Human action: delete `DATABASE_URL`, `DB_*`, `POSTGRES_*` and `API_PORT` from your `.env` (now ignored, but they hold the old DB password).
- Human action (if not done): `docker compose down -v` in `D:\Project\01-receipt-extractor` to stop and remove the v1 database.
- `graphify update .` run after M0.R (offline, code-only; graph is local and gitignored). Its tip about `GEMINI_API_KEY` must be ignored (CLAUDE.md: never the Gemini backend).
- Nothing committed.

---

## 2026-10-22 — SETUP · v2 folder setup finished (`setup_v2.ps1`)

**Done:**
- Human ran `setup_v2.ps1` (transcript in `setup_log.txt`): `.claude/` (settings + `git_guard.py`), `.env` and `.git` copied from `D:\Project\01-receipt-extractor`; branch `test`, remote `origin` = `github.com/Udai30/receipt-extractor`; `uv sync` exit 0. The script was stopped during step 5 (pytest), so the gates were run here instead.
- Gates in this folder, Docker Desktop not running: `ruff check` clean, `ruff format --check` clean (41 files), `mypy src` clean (21 files), `pytest -q -ra` **126 passed, 22 skipped** — 18 testcontainers DB tests skipped (Docker unreachable), 4 live provider tests skipped (opt-in). v1 recorded 144 passed with Docker up; 126 + 18 = 144, so nothing regressed. The 18 DB tests are deleted in M0.R anyway.
- `llm ping --model groq-oss-20b` exited 1: `GROQ_API_KEY`, `GEMINI_API_KEY` and `HF_TOKEN` are empty in the copied `.env` (checked by name only; values not printed).
- `.gitignore`: the six v2 planning docs are no longer ignored (v2 commits them); only `IMPLEMENTATION_PROMPTS.md` and `PROMPT_PLAYBOOK.md` stay local. Removed the leftover blank line and fixed the section comment.
**Files:** `.gitignore`, `WORKLOG.md`.
**Decisions:** none beyond the above; pip-audit is left for M0.R's gate run after the DB deps are removed.
**Open:**
- Human action: put your keys into `.env` (`GROQ_API_KEY` from https://console.groq.com/keys, `GEMINI_API_KEY` from https://aistudio.google.com/apikey), then `uv run receipt_extractor llm ping --model groq-oss-20b`.
- `setup_v2.ps1` and `setup_log.txt` are one-off and untracked; delete them once you no longer need the transcript (the log holds no secret values).
- Nothing committed yet: the v2 docs, CLAUDE.md, WORKLOG.md and `.gitignore` changes are uncommitted on `test`.

---

## 2026-10-22 — DOCS · Project documentation created (v2, lean)

**Done:** Created PRD, TRD, APPFLOW, UI_UX_DESIGN, BACKEND_SCHEMA, IMPLEMENTATION_PLAN,
IMPLEMENTATION_PROMPTS, CLAUDE.md and the project copy of PROMPT_PLAYBOOK.md from the ideas doc
"Portfolio Projects: 3 Quick Wins + 3 Flagships" (2026-10-22). v1 code (src/, tests/, configs/, pyproject.toml, uv.lock) was copied from `D:\Project\01-receipt-extractor`; caches, `.claude/`, `.env`, `.git` and `graphify-out/` were not (the human copied them later, see SETUP).
**Files:** all `*.md` in the project root.
**Decisions:**
- v2 follows the ideas doc: pipelines A (OCR+LLM) and B (Gemini vision); C (Qwen2.5-VL-3B, Kaggle) is stretch. v1's Donut baseline is dropped; `A-naive` is the baseline instead.
- Lean stack: no Postgres/FastAPI/Docker. The v1 DB layer is removed in M0.R; the v1 LLM layer is kept untouched.
- RapidOCR (pip-only ONNX) instead of Tesseract, to avoid a system install on Windows (v1 decision kept; the doc allows 'Tesseract or PaddleOCR').
- Space: uploads go only to pipeline A (Groq); Gemini runs only on CORD sample receipts.
**Open:**
- ~~Human action: copy `.env`, `.claude\` and `.git` from `D:\Project\01-receipt-extractor`, then `uv sync`.~~ Done 2026-10-22 via `setup_v2.ps1` (see SETUP entry). Still to do after M0.R: delete the `DATABASE_URL`/`POSTGRES_*`/`DB_*`/`API_PORT` lines from `.env`.
- Human action: if you no longer need it, stop the v1 compose database: `docker compose down` in `D:\Project\01-receipt-extractor`.
- Human action: confirm Gemini free-tier limits for the pinned Flash model at https://aistudio.google.com/rate-limit and update `configs/models.yaml`.
- Human action: create a Hugging Face account + write token (`HF_TOKEN`) before M3.2; Kaggle account with phone verification before M2.4 (stretch).

---

## v1 history (from `D:\Project\01-receipt-extractor\WORKLOG.md`, copied 2026-10-22)

> The entries below describe the v1 spec (Postgres + FastAPI). They are kept so the copied code's decisions stay traceable. v2's M0.R removes the database layer.

### 2026-10-19 — M0.3 · LLM provider layer (cache, rate limits, retries, accounting)

**Done:**
- Verified M0.1 (and M0.2, needed for the DB sink) in the repo before starting; reused `config.Settings`/`get_settings`, the CLI app, `db.models.LLMCall`, `db.repositories.add_llm_calls` and the session factory.
- Deps: `litellm` 1.106.0, `pyyaml` 6.0.3 (runtime); `types-pyyaml` (dev). LiteLLM is imported lazily (≈ 12 s import) with `LITELLM_LOCAL_MODEL_COST_MAP=True`, so importing the package or running `--help` never imports it or touches the network (tested in a subprocess; `--help` takes 0.9 s).
- `configs/models.yaml`: aliases `groq-oss-20b`, `groq-oss-120b` (30 RPM / 8K TPM / 1K RPD / 200K TPD), `gemini-flash` → **`gemini/gemini-3.8-flash`** (pinned; `public_only`; 5 RPM / 250K TPM / 20 RPD, conservative), `ollama-embed` (local `nomic-embed-text`), plus `donut-cord` / `qwen-vl-3b` as non-callable `local`/`offline` entries. Validated strictly (provider/kind consistency, the LiteLLM prefix must match the provider, alias shape).
- `configs/pricing.yaml`: dated list prices per LiteLLM model string, quoted as strings (floats are rejected). gpt-oss-20b $0.075/$0.30, gpt-oss-120b $0.15/$0.60, gemini-3.8-flash $0.75/$3.75 until 2026-12-31 then $1.50/$7.50 from 2027-01-01 (announced), Ollama $0. Sources: console.groq.com/docs/models and ai.google.dev/gemini-api/docs/pricing, checked 2026-10-19.
- The `llm/` package:
  - `types.py`: `LLMRequest`/`LLMResponse`/`EmbedRequest`/`EmbedResponse`, text and inline image parts, `CallTags`, `LLMCallRecord`.
  - `registry.py`: model registry, price table, `check_pricing_covers`.
  - `cache.py`: SHA-256 key over model string + messages + params + schema, `<key[:2]>/<key>.json`, atomic writes, corrupt entries are misses.
  - `limits.py`: token-bucket RPM/TPM limiter, persisted UTC-day RPD/TPD budget.
  - `retry.py`: equal-jitter backoff, `Retry-After` parsing.
  - `errors.py`: exception hierarchy and sanitiser.
  - `providers.py`: provider protocol, LiteLLM adapter, exception mapping.
  - `fake.py`: `FakeProvider`, which simulates latency, 429 + Retry-After, 5xx, timeout, hang, 400, and any reply text including invalid JSON.
  - `sinks.py`: `JSONLSink`, `DatabaseSink`, `MemorySink`.
  - `client.py`: `LLMClient.complete`/`embed`, `build_client`.
- Structured output: the `response_model`'s JSON schema is sent as `response_format`, and the reply is validated with Pydantic (code fences are tolerated). On failure there is exactly one repair call with the validation problems appended. `schema_valid` reflects the final outcome.
- CLI: `receipt_extractor llm ping --model <alias>` prints alias, model, tokens, latency, list cost, actual cost, retries and a reply preview. Embedding aliases work too. It uses no cache but does track the budget; errors print one sanitised line and exit 1.
- Settings/env: `LLM_MODELS_PATH`, `LLM_PRICING_PATH`, `LLM_CACHE_DIR`, `LLM_BUDGET_PATH`, `LLM_MAX_CONCURRENCY` (1–64, default 4) and `LLM_TIMEOUT_S` (default 60), in `config.py` and `.env.example`. The Dockerfile copies `configs/` into the runtime image.
- Tests (offline, using FakeProvider and a simulated clock):
  - `tests/unit/test_llm_client.py`:
    - Cache: a hit returns an identical response without calling the provider. The key covers params, schema and image bytes, but not tags. A failed repair is not cached.
    - Retries: 429 then success records 1 retry and honours Retry-After. 5 consecutive failures raise `RetriesExhaustedError` after 4 jittered backoffs and write an error record. A 400 is not retried. A huge Retry-After fails fast. The hard timeout cancels a hung call.
    - Structured output: invalid JSON → repair → `schema_valid` true or false.
    - Accounting and policy: cost = tokens × dated price; data-origin guard; missing key.
    - Limits: bounded concurrency; RPM limiter; daily budget; embeddings.
  - `tests/unit/test_llm_components.py`: config validation (including rejecting `!!python/object`), dated pricing, float prices rejected, cache key/traversal/corruption, token bucket, budget persistence and day rollover, retry math, Retry-After parsing, sanitising, JSONL sink.
  - `tests/unit/test_llm_providers.py`: LiteLLM request shape and response parsing; real LiteLLM exception classes mapped to ours; no exception chaining; Gemini `?key=` redaction; litellm not imported on package import.
  - `tests/unit/test_cli_llm.py`: ping output, embedding ping, missing key, unknown alias, provider error, config error.
  - `tests/integration/test_llm_db_sink.py`: row written to a fresh migrated testcontainers DB; an FK failure is logged, not raised.
  - `tests/live/test_llm_live.py`: `@pytest.mark.live`.
- Verification evidence:
  - Gates: `ruff check` ✔, `ruff format --check` ✔, `mypy src` (strict) ✔, `pip-audit` no known vulnerabilities.
  - `pytest -q`: **144 passed, 4 skipped** (the skips are the opt-in live tests). Integration tests ran against real containers. Coverage 96 % total (91–98 % per `llm/` module).
  - Real CLI: `llm ping --model groq-oss-20b` without a key → `error: GROQ_API_KEY is not set; alias 'groq-oss-20b' needs it` (exit 1); `--model donut-cord` is refused as a local model.
  - Real LiteLLM→Groq round trip with a deliberately invalid key (fixed "pong" prompt only): Groq's `invalid_api_key` was mapped to a non-retryable error with no retries, and the key was absent from the output.
- Security:
  - Keys come only from Settings and are passed per call (never via env to LiteLLM). They are excluded from dataclass `repr`.
  - Every `LLMError` message is sanitised on construction (exact keys; `Bearer`; `key=`/`api_key=`; `gsk_`/`AIza`/`sk-`/`hf_` shapes; control characters) and truncated to 300 characters.
  - Provider exceptions are re-raised `from None`, so no chained LiteLLM exception carries URLs.
  - Records hold no prompts or outputs. The DB sink logs only the exception type.
  - YAML is read with `safe_load` and capped at 1 MB. Cache keys are validated as hex (no path traversal), and the JSONL `run_id` is validated.
  - Gemini refuses `user_upload` content before any network call; the default origin is `user_upload`.
  - LiteLLM: `drop_params=True`, debug info suppressed, telemetry off, its own retries disabled.
- Performance:
  - The limiter sleeps exactly the computed deficit under a FIFO lock, with no busy-waiting. Tested with a fake clock: the 3rd call at 2 RPM triggers a single 30 s sleep.
  - A global `asyncio.Semaphore` bounds concurrent provider calls (tested: peak 2 of 8).
  - A cache lookup is one path computation plus one file read. File I/O runs in `asyncio.to_thread`.
  - The timeout covers only the provider call, not the semaphore wait.

**Files:** `pyproject.toml`, `uv.lock`, `configs/models.yaml`, `configs/pricing.yaml`, `src/receipt_extractor/llm/{__init__,types,registry,errors,cache,limits,retry,providers,fake,sinks,client}.py`, `src/receipt_extractor/config.py`, `src/receipt_extractor/cli.py`, `.env.example`, `Dockerfile`, `tests/conftest.py` (live opt-in), `tests/unit/conftest.py`, `tests/unit/test_{llm_client,llm_components,llm_providers,cli_llm,cli}.py`, `tests/integration/test_llm_db_sink.py`, `tests/live/{__init__,test_llm_live}.py`, `TRD.md` (§3, §S3 implementation notes, §S6), `BACKEND_SCHEMA.md` (§S5), `WORKLOG.md`.

**Decisions:**
- The Gemini alias is pinned to `gemini-3.8-flash`, the newest stable free-tier Flash model listed on 2026-10-19. Google publishes free-tier limits only per project in AI Studio. RPD 20 is the figure reported for this model; it stays as the safe default until the human confirms the real limit.
- Live tests are opt-in (`-m live`) and also skip without keys. Otherwise, with keys in `.env`, a plain `pytest -q` would spend free-tier quota and make the gate depend on the network.
- One record per provider call (a repair is its own record), not one per `complete()`. This matches the pipeline protocol's `llm_calls: list[str]` and keeps per-call tokens and latency honest. Cache-hit records keep tokens and list cost, so a pipeline's cost stays comparable across reruns, but their latency is the lookup time.
- Added `ollama-embed` (local) so `embed()` has a callable alias; documented in TRD §3. No pipeline needs embeddings yet.
- A failed repair call returns the invalid first reply with `schema_valid = false` instead of raising, and the result is not cached. The error is still recorded.
- `actual_cost_usd` is in the JSONL record and `LLMResponse`, but not in the `llm_call` table: the schema has no column for it, and it is always 0. No migration is needed.
- The DB sink swallows DB errors, because live requests must not fail on accounting. The JSONL sink propagates them, because an eval run must not continue without accounting.
- Not committed: this task's prompt says the user handles Git, which overrides the "push after each task" preference here.

**Open:**
- Human action (acceptance criterion with real keys): put `GROQ_API_KEY` and `GEMINI_API_KEY` in `.env`, then run `uv run receipt_extractor llm ping --model groq-oss-20b`, `--model groq-oss-120b` and `--model gemini-flash`. Optionally also run `uv run pytest -m live -ra`. Expect tokens, latency and a non-zero list cost. **Not verified by the agent: no keys are configured.**
- Human action: confirm the free-tier limits for `gemini-3.8-flash` at https://aistudio.google.com/rate-limit. If your project allows more than 5 RPM / 20 RPD, update `configs/models.yaml`. Re-check the Groq limits in the console before sweeps.
- Optional: `ollama pull nomic-embed-text` makes `llm ping --model ollama-embed` and the live embedding test work.
- Follow-up (M0.5/M4.1): the `api` container's root filesystem is read-only. Live traffic uses `cache=False`, but the budget file defaults to `.cache/llm_budget.json`. When wiring `/extract`, point `LLM_BUDGET_PATH` at a tmpfs or volume, or build the live client with `budget=False`.
- Follow-up (M0.4): the runner should treat `BudgetExhaustedError` as "pause, resume later", and build its client with `cache=True` and `JSONLSink.for_run(...)`.
- Note: gpt-oss models spend output tokens on reasoning, so `llm ping` uses `max_tokens=512` to make sure a reply is produced. Pipelines should size `max_tokens` with that in mind; `max_tokens` is also reserved against TPM before the call and refunded afterwards.

### 2026-10-19 — M0.2 · Docker Compose, Postgres/pgvector and baseline migration

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

### 2026-10-19 — TOOLING · graphify knowledge graph + ponytail plugin

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

### 2026-10-19 — TOOLING · Agent may push only to `test`

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

### 2026-10-19 — M0.1 · Repository scaffold and quality tooling

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

### 2026-10-19 — DOCS · Project documentation created

**Done:** Created PRD, TRD, APPFLOW, UI_UX_DESIGN, BACKEND_SCHEMA, IMPLEMENTATION_PLAN,
IMPLEMENTATION_PROMPTS, CLAUDE.md and the project copy of PROMPT_PLAYBOOK.md. No code exists yet.
Datasets were checked for availability and licence on 2026-10-19 (see PRD §4).
**Files:** all `*.md` in the project root.
**Decisions:** Gradio UI (benchmark-style project). FastAPI + Postgres per portfolio baseline. Donut (CORD-finetuned) chosen as the specialist baseline; RapidOCR (pip-only, CPU) instead of Tesseract to avoid a system binary on Windows. Qwen2.5-VL-3B runs on Kaggle and is imported as offline predictions.
**Open:**
- Human action: create a free Groq API key (console.groq.com) and a Gemini API key (aistudio.google.com) → put both in `.env`.
- Human action: install Docker Desktop (WSL2) and Ollama for Windows if not already installed.
- Human action: create a free Kaggle account and verify phone (required for GPU notebooks) before M2.4.
