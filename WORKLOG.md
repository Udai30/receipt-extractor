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
| Interface | Gradio 6 → Hugging Face Space |
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
| Current task | (2026-10-27) M2.3 done; M3.1 done (A: 0.8274 → 0.8464 after the quota-repair fix); next MF.1 write-up · earlier: M2.3 in progress: **A-naive done** (field_f1 0.820 [0.765, 0.870]); A 23/100 (77 error rows to retry), B 16/100 — day-2 job resumes both from 00:05 UTC 2026-10-24 · M3.2 done locally (deploy = human action) · M2.1, M2.2 done · M2.4 waiting for the human's Kaggle run · next: M3.1 when M2.3 completes |
| Last verified gate run | 2026-10-23 (M2.3 quota fix): ruff check + format, mypy, pytest 600 passed / 9 skipped (8 live, 1 symlink), pip-audit — all green |
| Pending human actions | Deploy the Space (`docs/DEPLOY.md`); run `notebooks/qwen_vl_cord.ipynb` on Kaggle and import it (M2.4); confirm `gemini-3.5-flash-lite` free-tier RPD in AI Studio; decide on a C-local test row, on publishing row C (Qwen Research licence) and on a LICENSE; delete the v1 DB lines from `.env` |

---

## 2026-10-24 — M2.3 · Pipeline B test run complete (Gemini limits raised by the human)

**Done:**
- The human read the free-tier limits in AI Studio (chat, 2026-10-24): `gemini-3.5-flash-lite` allows 10 requests/minute and about 250/day. `configs/models.yaml` now has `rpm: 10, rpd: 250` (`limits_checked: 2026-10-24`; `tpm` is still LiteLLM's figure). This replaces the conservative 20/day placeholder.
- Ping `llm ping --model gemini-flash-lite`: reachable ("pong", 7.7 s, $0).
- `B-cea8aeff49d5` resumed and **completed: 100/100, 0 errors, 84 pass / 16 fail, field_f1 0.9572 [0.9305, 0.9782]** (68 cases run this session, in one pass). Remaining B metrics, latency and cost: read from `reports/results.csv` after `report build`.
- LiteLLM warns that `temperature` on Gemini 3+ is planned for removal; it still works. Watch for it on a LiteLLM upgrade.

**Open:**
- A (`A-e8c045ee22e6`) still has error rows to retry (Groq daily quota); then `report build`.
- Not committed.

---

## 2026-10-27 — MF.1 · README, resume bullet and LinkedIn draft written from the final reports

**Done:**
- **README** reordered to the UI_UX_DESIGN §S5 order: headline → results table → (GIF placeholder, commented out so no broken image shows) → architecture → how it works → evaluation method → what the numbers say (with the one-fix before/after) → failures by cause → limitations → what I'd do differently → reproduce (setup, demo app, commands). The results and failure tables are still generated (`report readme`); the prose numbers were checked against `reports/results.csv|md` and `reports/failures.csv` (field F1, intervals, per-field F1, valid JSON, reconcile, cost, latency, failure counts, and the pass counts 84 / 58 / 55 = 100 minus the failure totals). One overstatement was caught and fixed during that check (OCR-type failures are 23 of A's 42 and 19 of A-naive's 45, not "most"). A stale line (B "uses about half of 20 requests/day") was corrected.
- **Headline:** B 0.957 field F1 vs A 0.846 vs A-naive 0.820; B costs about 2.5× A at list price; retries fixed validity (6 % → 2 %), not accuracy.
- **Resume bullet** filled in `PRD.md` §7 (0.957 vs 0.846 F1; invalid outputs 6 % → 2 % from the schema retries; $0.98 vs $0.39 per 1k at list price). The template credited "retries and arithmetic checks" for fewer invalid outputs, but the arithmetic checks only set a review flag, so the filled bullet credits the retries only; the template is kept beneath it.
- **`docs/LINKEDIN_DRAFT.md`** rewritten as a finished post (≈ 1,170 characters; hook, body, close, first comment) with a "where each number comes from" table. Placeholders left on purpose: Space URL, GitHub URL, GIF.
- **TRD §6:** the instruction was to relax the latency budgets to the measured numbers. Measuring again showed the first live check was contaminated (OCR alone is 1.3–1.9 s), so the budgets were met and were **not** relaxed; the clean measurement is recorded there (see the entry below).

**Files:** `README.md`, `PRD.md` (§7 bullet), `TRD.md` (§6 measurement), `docs/LINKEDIN_DRAFT.md`, `WORKLOG.md`.

**Decisions:**
- Did not relax TRD §6: relaxing a budget that the clean measurement meets would misstate the system. The user asked for the relaxation on the strength of my first (contaminated) measurement; this is flagged in the chat summary.
- The review-flag statistics (A flags 37 receipts and catches 29 of its 42 wrong ones; B flags 15 and catches 4 of its 16) are **not** in the README: they come from an ad-hoc script, and TRD §S5 allows only numbers produced by a documented CLI command. They are a candidate for `report build`.

**Open:**
- Human actions: deploy the Space and add its URL to the README and LinkedIn first comment; record the GIF and uncomment the image line; run the Kaggle notebook (row C); choose a LICENSE; decide on publishing row C.
- Re-measure latency on the deployed Space; update the README limitation and TRD §6.
- Optional: add the review-flag capture rate to `report build`, then to the README.
- Not committed.

---

## 2026-10-27 — M3.1 · quota-repair fix re-run and measured (before/after) + live latency check

**Done:**
- **M3.1 fix (human's choice, chat 2026-10-24): the repair/quota bug fix, no prompt changes.** After `LLMClient._repair` was changed to pause on a quota refusal and `eval run --rerun-case` was added, the 5 affected A cases (test-0012, -0026, -0061, -0066, -0068) were re-run through a retry loop (Groq's rolling daily quota blocked it for a day; no error rows were written meanwhile). New rows were appended; every reader takes the last row per case. Run `A-e8c045ee22e6`, 100 test receipts, same ids:

  | A (engineered) | before | after |
  |---|---|---|
  | field_f1 [95 % CI] | 0.8274 [0.7741, 0.8768] | **0.8464 [0.8014, 0.8894]** |
  | items_f1 | 0.8141 | 0.8358 |
  | subtotal_f1 / total_f1 | 0.8060 / 0.8700 | 0.8358 / 0.8900 |
  | valid_json | 0.95 | **0.98** |
  | reconcile | 0.63 | 0.63 |
  | latency p50 / p95 (excl. pacing) | 7,560 / 50,029 ms | 7,148 / 31,914 ms |
  | list cost per 1k | $0.3592 | $0.3930 |

  Per case, now valid: test-0026 0.00 → 0.80, -0066 0.00 → 0.60, -0068 0.00 → 0.50 (its first reply was valid this time, so it needed no repair), -0061 valid but still 0.00 (two zero-price items dropped, a price kept as "18.0001"); test-0012 is still invalid after 3 attempts (an unparseable fused amount in the OCR text survives both repairs). A-naive is unchanged (0.8200 [0.7653, 0.8702], valid_json 0.94, baseline), so A now beats the baseline by 0.026 field F1 and 4 points of valid JSON, with overlapping CIs: **the engineering (repairs + checks) helps on validity; its effect on accuracy is small and not statistically separable at n = 100.** B is unchanged (0.9572).
- The 5 A failure labels were rewritten from the new predictions (`reports/failures.csv`, 103 rows, 0 unlabelled); README tables regenerated with `report readme`.
- **Live latency check (TRD §6, pipeline A on uploads, `ui.live.default_live_service`, no cache, validation receipts 0010–0029, one at a time).** Two runs the same day, 20 uploads each (16 ok, 4 refused by the 5 MB upload cap: PNG files of 6.7–10.7 MB, as designed):
  - **First run — discarded as contaminated.** Service time (excluding Groq pacing waits) p50 7.2 s, p95 15.0 s, with RapidOCR at 1.5–7.1 s. Cause not identified (CPU contention is the likely one). It briefly led to a draft TRD edit relaxing the budgets; that edit was reverted before commit.
  - **Check:** RapidOCR alone on 16 validation receipts, warm: median 1.6 s, max 1.9 s (within the 2 s budget).
  - **Second run (clean): service time median 3.2 s, p95 7.1 s (linear interpolation), max 11.6 s** (the first request, with a 9.8 s model call); OCR 1.3–1.9 s, model call 1.1–4.4 s; wall clock including pacing waits (mean 8.2 s) median 13.7 s, p95 18.4 s. **The TRD budgets (OCR < 2 s, p95 < 8 s) are met on this laptop, narrowly** (n = 16, and a nearest-rank p95 would be the 11.6 s maximum). A free Space CPU is likely slower; re-measure after deploying. TRD §6 now records the measurement; the budgets are unchanged.

**Files:** `reports/failures.csv`, `reports/results.csv|md`, charts, `README.md` (regenerated tables), `WORKLOG.md`. No source change in this entry.

**Open:**
- Fill the README headline, "how it works", limitations, "what I'd do differently" and the resume bullet and LinkedIn brackets from the final numbers (MF.1).
- Human actions unchanged: Kaggle run (M2.4), deploy the Space, record the GIF, publish; choose a LICENSE and whether to publish row C.
- Not committed.

---

## 2026-10-24 — M3.1 (labelling done; fix pending) + M2.3 closed · all three test runs complete

**Done:**
- **M2.3 acceptance met.** `reports/results.csv|md` has A-naive, A and B on the same 100 test ids (run ids `A-naive-5ed10911e9ae`, `A-e8c045ee22e6`, `B-cea8aeff49d5`). A finished through the 4-minute retry loop (19 cases in one attempt, 0 errors). B reached 100/100 after the human read the real free-tier limits in AI Studio and set `rpd: 250` / `rpm: 10` for `gemini-flash-lite` in `configs/models.yaml` (`limits_checked: 2026-10-24`; the earlier 20/day was a placeholder). A full B re-run therefore fits in one day. Field F1 [95 % CI]: A-naive 0.820 [0.765, 0.870], A 0.827 [0.774, 0.877], B 0.957 [0.931, 0.978]; valid_json 0.94 / 0.95 / 1.00; reconcile 0.59 / 0.63 / 0.85; list cost per 1k $0.352 / $0.359 / $0.975; p50/p95 ms 2,368/8,364 · 7,560/50,029 · 4,142/17,049.
- **All 103 failures hand-labelled** in `reports/failures.csv` (A-naive 45, A 42, B 16) from gold vs prediction (+ OCR text for A):

  | failure_label | A-naive | A | B |
  |---|---|---|---|
  | other | 20 | 18 | 10 |
  | ocr_misread | 8 | 9 | 0 |
  | wrong_item_split | 7 | 7 | 0 |
  | hallucinated_item | 6 | 5 | 6 |
  | number_format | 4 | 3 | 0 |

  `hallucinated_item` (discount lines, sub-lines, size labels, summary lines read as items) is the only cause present in all three variants (B: 6 of 16). In B, `other` is mostly item codes kept in names (3) and a printed subtotal not extracted (3).
- **Defect found in A (engineered) and fixed in code; the 5 affected rows are not re-run yet.** In 5 of A's 42 failures (test-0012, -0026, -0061, -0066, -0068) the first reply was invalid, and the repair re-ask was refused by Groq's daily quota. `LLMClient._repair` swallowed that `ProviderQuotaExhaustedError` and returned the invalid first reply, which the run scored as a final answer (`attempts = 1`, `schema_valid = false`) instead of pausing. The repair itself works: test-0095 was repaired when quota allowed (attempts 2, valid). The fix I made after M2.3's quota work: `_repair` now re-raises `BudgetExhaustedError`, so the run pauses and the case is retried later. Test added (`test_provider_quota_during_repair_is_raised_not_scored`); ruff, mypy and the affected test modules pass (302 passed). **A's current numbers are therefore understated for these 5 cases** and the "retries and checks" comparison against A-naive is not yet fair; the same quota issue can apply to A-naive's rows only through the first call, which the runner already pauses on.
- Order: A-naive labels were written earlier today (partial entry below).

- **`--rerun-case`** (`eval run --rerun-case <id>`, repeatable; appends a new row, last row wins; an id outside the run is rejected): runner `_finished_ids(rerun=)`, `open_session`/`execute_run(rerun_cases=)`, CLI option; 2 tests in `tests/unit/eval/test_runner.py`. Chosen by the human (chat 2026-10-24) as M3.1's one fix: the repair/quota bug fix, no prompt changes. A's "before" numbers (field_f1 0.8274 [0.7741, 0.8768], valid_json 0.95, reconcile 0.63, 5 invalid rows) were saved before the re-run.
- **Re-run status:** the first attempts at the 5 cases were refused by Groq's rolling daily quota and paused cleanly (no rows written); a retry loop (every 4 min, only cases without a new row) is running. After it finishes: rebuild reports, record before/after for A, update the 5 failure labels, run `report readme`.
- **README generator** `eval/readme.py` + `report readme`: fills the `<!-- results -->` and `<!-- failures -->` blocks of README.md from `reports/results.csv` (test rows, model from each run's manifest) and `reports/failures.csv` (counts by label and variant); fails if a marker is missing; idempotent. 5 tests in `tests/unit/eval/test_readme.py`. The README currently shows A's pre-fix numbers; rerun `report readme` after the 5 cases are re-run.

**Open:**
- Re-run those 5 A cases (≈ 10–15K Groq tokens). The runner skips cases that already have a row, so this needs either a small `--rerun-case` option (append-only, last row wins, like `--retry-errors`) or the human's decision to edit the run. Then rebuild `reports/`, and record before/after for A.
- Choose the one M3.1 fix (human decision; see the chat summary). Anything that changes a prompt means re-running every variant: A-naive and A are limited by Groq's 200K tokens/day (about 2 days for both), B fits in one day at 250 requests/day.
- Nothing committed since `933c0f9`.

---

## 2026-10-24 — M3.1 (partial) + MF.1 (drafts) · A-naive failure labels and write-up scaffolding

**Done:**
- **Order:** started while M2.3's A and B runs wait on free-tier budgets, by the human's instruction in chat (2026-10-24: start M3.1 labelling on A-naive, draft MF.1, review the Qwen notebook). M3.1 is **not** finished: it needs A and B failures and the one fix with a before/after.
- **Failure labels (A-naive `A-naive-5ed10911e9ae`, 45 failures, all hand-labelled** by reading gold vs prediction vs OCR text; `uv run receipt_extractor report failures --run-id A-naive-5ed10911e9ae`, then the labels were written into `reports/failures.csv`; a re-export kept them):

  | failure_label | count |
  |---|---|
  | other | 20 |
  | ocr_misread | 8 |
  | wrong_item_split | 7 |
  | hallucinated_item | 6 |
  | number_format | 4 |

  `other` is the largest and splits (see each row's `note`) into: invalid JSON, no receipt returned (6, matching valid_json 0.94); item lines dropped, no items returned (6); leading quantity left in item names (about 6); the rest single cases (cash taken as total, tax line not recognised, duplicate lines collapsed).
- **Candidates for the one fix (not chosen yet; decide once A and B are labelled too):** invalid JSON and dropped items (the repair loop in engineered A targets these); quantity left in names (a prompt rule); summary lines taken as items (`ITEMS 1.00`, category subtotals, barcodes).
- **MF.1 drafts (no measured numbers typed):** README now has a headline placeholder, a GIF placeholder, a Mermaid architecture diagram, an evaluation/failures placeholder and a "what I'd do differently" section; `docs/GIF_SCRIPT.md` (shot list, human records); `docs/LINKEDIN_DRAFT.md` (outline with `[brackets]`, resume bullet, checklist).
- **Qwen notebook generator:** reviewed, no change. It has not been executed (no CUDA GPU), so a fix without a failing run would be a guess. One risk to watch on Kaggle: fp16 on a T4 (no bfloat16) can give degraded output for Qwen2.5-VL; if so, fix it in `pipelines/qwen_notebook.py` and regenerate.
- Tests: no code changed; docs and a report CSV only.

**Files:** modified `README.md`, `WORKLOG.md`; added `docs/GIF_SCRIPT.md`, `docs/LINKEDIN_DRAFT.md`; `reports/failures.csv` (generated, hand-labelled).

**Open:**
- Label A's and B's failures once those runs complete, then pick and implement one fix (validated on the validation split), re-run the affected variants on test, and add the before/after row to `reports/results.md`.
- Fill the README headline, results table, failures and limitations from the final reports (MF.1 proper).
- Human action: record the GIF from `docs/GIF_SCRIPT.md`.
- A (67/100, 33 error rows) and B (32/100) are paused; the background loop that resumed them is no longer running and must be restarted.
- Nothing committed.

---

## 2026-10-23 — M3.2 · Gradio Space (Results, Try it, Failures)

**Done:**
- **Order:** M3.2 was started before M3.1, by the human's explicit decision in chat (2026-10-23: "start M3.2 now while M2.3's runs wait on budgets; do M3.1 once the test results exist, and wire the Results/Failures tabs to whatever reports exist then"). M3.1 still follows when M2.3's test runs exist. The Results and Failures tabs read whatever `reports/` holds and show in-progress/empty states until then.
- **Gradio/Pillow (human decision, chat 2026-10-23, option "Gradio 6.30 + Pillow 13"):**
  - Gradio 5.50 (the spec's major version) requires `pillow<12` and has **6 known advisories** (PYSEC-2026-64/65/66/211/2178/2179, fixed in 6.6–6.16).
  - **Every** Gradio release, including the latest 6.30.0 (2026-10-08), caps Pillow `<13`, while Pillow 11/12 have **6 known CVEs** (CVE-2026-96636/37/43/44/78, GHSA-78g4-xqwv-q3g8; fixed in 13.0). Pillow is on the untrusted-upload decoding path.
  - Probed first in a throwaway venv in the scratchpad: Gradio 6.30 + Pillow 13.0 passed an upload → server-side PIL → image round-trip via `gradio_client`.
  - Adopted: `gradio>=6.30,<7` plus `[tool.uv] override-dependencies = ["pillow>=13.0.0"]` (commented; remove once Gradio allows Pillow 13).
  - `pip-audit`: no known vulnerabilities. New packages (Gradio's tree): fastapi 0.161.1, starlette 1.9.0, uvicorn 0.54.0, pandas 3.1.0, gradio-client 2.7.2, python-multipart 0.0.32, orjson, safehttpx, brotli, pydub, groovy, hf-gradio, opentelemetry-api, semantic-version, tomlkit, pytz, tzdata.
  - "Gradio 5" → "Gradio 6" in every document header table, TRD §S1 and §S7.
- **UI package** `src/receipt_extractor/ui/`. Gradio-free logic, tested without a browser:
  - `views.py`: a column view-model per pipeline — line items, totals, badge with text (✓ reconciled / ⚠ needs review with the diff / ✕ invalid with the reason / • not checked), metadata strip (model · tokens · stage times · list cost · calls), and evidence (JSON or the invalid reply, OCR text). Money is shown as `Rp 51.000 (51000)` (the receipt's grouping next to the normalised number). A **cache-hit row says "replayed from cache" instead of showing ~0 s as model time**; the first version displayed "model 0.0 s", seen in the browser and fixed.
  - `reports.py`: Results and Failures read only `reports/results.csv`, the report PNGs (alt text; the table is their equivalent), `reports/failures.csv` and `reports/failure_examples.jsonl` (written in M3.1). Fixed pipeline order; headline "Gemini vision: X field F1 vs OCR + LLM Y at Z× the cost" only when A and B rows exist, otherwise "Benchmark in progress…". Size-capped reads; malformed numbers show "—".
  - `showcase.py` + CLI `receipt_extractor ui build-showcase`: bundles 6 CORD **validation** receipts (test ids refused), as display JPEGs ≤ 1,024 px with metadata stripped, plus their stored A and B results and run ids, taken from the newest run with a row (paused runs included). Built now from `A-24c9db2a7d6d` and `B-8ddf1a98d199` (both with M2.2 review flags; ≈ 0.5 MB).
  - `live.py`: uploads. In order: `check_upload` (≤ 5 MB, PNG/JPEG/WebP magic, ≤ 4,096 px per side from the header, before decoding) → `DemoBudget` (per UTC day with `DEMO_MODE`, plus a provider-stop flag) → **pipeline A only, `origin="user_upload"`**. Async, so all calls share the event loop the LLM client's limiters live on. Outcomes are ok / rejected / paused / error, with an 8-char request id and plain-language messages; logs carry metadata only.
  - `presenters.py`: maps a sample or a live outcome to the Try it outputs. B's column on an upload always reads "Vision model runs on sample receipts only (privacy)".
- **App** `ui/app.py` + `theme.py`:
  - Blocks with Results (default) · Try it · Failures · About, one builder per tab; callbacks only move values.
  - `queue(default_concurrency_limit=2, max_size=20)`; `launch(max_file_size="5mb")`; `delete_cache=(300, 300)`; analytics off.
  - Uploads arrive as a Gradio temp file, which `consume_upload` reads into memory and deletes at once (only inside temp dirs). Share/download buttons are removed from the upload, fullscreen only on samples.
  - Theme: a `gr.themes.Base` subclass (slate + indigo, Inter / JetBrains Mono, radius md).
  - Settings: `DEMO_DAILY_REQUESTS` (default 100, in `.env.example`).
  - `app.py` puts `src/` on the path when the package is not installed (Space).
- **Space packaging:**
  - `requirements.txt` from `uv export --frozen --no-dev --no-hashes --no-emit-project --prune gradio` (Pillow 13 pinned; Gradio's subtree is left to the Space's `sdk_version`).
  - `space/README.md`: front matter with `sdk_version: 6.30.0`, `app_file: app.py`, Python 3.12.
  - `docs/DEPLOY.md`: every human step — refresh reports/showcase/requirements, `hf auth login`, `hf repos create … --type space --sdk gradio`, variables/secrets table (`GROQ_API_KEY` secret, `DEMO_MODE=true`, `DEMO_DAILY_REQUESTS`; **no** Gemini key), upload as an explicit per-path list so `.env`, `data/`, `runs/` and `.cache/` are never sent, checks, and the first-build risk with its fallbacks.
  - `.claude/launch.json` for the local preview.
- **Local app verified end to end** (`uv run python app.py`, browser pane + `gradio_client`):
  - Results tab: in-progress headline, gold row, both charts.
  - Try it: samples validation-0005 and -0033 show A and B with badges, IDR amounts and metadata.
  - Failures: empty state. Phone width **360 px**: no horizontal overflow (`scrollWidth` 360), columns stack, all 3 images loaded with alt text.
  - **3 real live uploads** of public CORD validation images through the running app (real RapidOCR + Groq, no cache): validation-0061 banner ok; validation-0062 **2.8 s** end to end (OCR 0.9 s, model 1.4 s, ✓ totals add up, 3 items); validation-0097 22.6 s, of which only 8.3 s was OCR + model — the rest was waiting on Groq's TPM pacing, shared with the A-naive eval running at the same time.
  - After each upload, **no copy of the image remained** in Gradio's temp dir.
- Tests (+50: 45 unit + 5 e2e):
  - `tests/unit/ui/test_views_and_reports.py` (18): every badge state; money formats; malformed prediction rejected; cached replay shows no model time; Results headline/table/order/percent/seconds/cost from a CSV; in-progress and empty states; charts with alt text; Failures filters, detail and empty state.
  - `tests/unit/ui/test_live.py` (22): input caps (empty, > 5 MB, GIF, corrupt, > 4,096 px); budget cap/reset/provider stop; **upload runs pipeline A as `user_upload`**; rejected and over-budget uploads never reach the pipeline; provider budget/429 → paused; missing key → "not configured" without details; unexpected errors are generic, with the request id and no exception text in the message or logs; the default service builds engineered PipelineA on `groq-oss-20b` with the demo cap; the live presenter never shows B; bundled samples (6, validation only, images present); temp upload read then deleted, refused outside temp dirs, oversize not read into memory; Space requirements keep Pillow 13 and leave Gradio to `sdk_version`, matching the tested Gradio.
  - `tests/unit/ui/test_app_smoke.py` (5, `e2e`, **gradio_client against the real app on 127.0.0.1**): sample receipt (no model call); upload → pipeline A only; > 4,096 px rejected server-side; **> 5 MB upload refused**; **budget-exhausted banner** (samples still work).
  - The obsolete stub test `tests/unit/test_ui_stub.py` was removed.
- **Bugs caught by the tests and fixed:** with no demo cap, a provider "budget exhausted" did not stop live calls (`DemoBudget` now has an explicit stopped flag); Gradio did not inject `SelectData` while it was imported inside a function (Gradio is now imported at module level in `ui/app.py` only).
- Gates: `ruff check` clean, `ruff format --check` clean (105 files), `mypy src` clean (57 files, strict), `pytest -q` **595 passed, 9 skipped** (8 live, 1 symlink; the 5 e2e Gradio smoke tests run in the default suite), `pip-audit` no known vulnerabilities.
- Security:
  - Uploads are untrusted: caps are checked before decode, Pillow is 13, there is no persistence beyond Gradio's temp file (deleted at once, plus the sweep), and logs carry metadata only.
  - **No path from an upload to Gemini** (code plus the layer's data policy, tested); no Gemini key on the Space.
  - Errors carry no stack traces or provider text.
  - Queue and size limits bound resource use; the daily demo cap bounds the free-tier budget.
  - The deploy commands upload an explicit allow-list (no secrets).
  - The Google Fonts in the theme load from the visitor's browser (no server request).
- Performance: samples are instant (cached); the live A path measured 2.8 s when Groq was not pacing.

**Files:**
- Added: `src/receipt_extractor/ui/{views,reports,showcase,live,presenters,app,theme}.py`, `src/receipt_extractor/ui/assets/showcase/` (`showcase.json` + 6 JPEGs), `requirements.txt`, `space/README.md`, `docs/DEPLOY.md`, `.claude/launch.json`, `tests/unit/ui/{__init__,test_views_and_reports,test_live,test_app_smoke}.py`.
- Modified: `src/receipt_extractor/ui/__init__.py`, `src/receipt_extractor/cli.py` (`ui build-showcase`), `src/receipt_extractor/config.py` (`demo_daily_requests`), `app.py`, `.env.example`, `pyproject.toml` + `uv.lock` (gradio, Pillow override), `configs/runs/b-val10.yaml` (M2.2 params, after M2.1 closed), the header table of every doc, `TRD.md` §S1/§S7, `UI_UX_DESIGN.md` §2.3, `README.md`, `WORKLOG.md`.
- Removed: `tests/unit/test_ui_stub.py`.

**Decisions:**
- Gradio 6.30 + Pillow 13 override (human decision above).
- On the Space the package is not pip-installed (`src/` on the path instead), so pip never re-resolves Gradio's Pillow cap.
- Samples are **validation** receipts with **stored** results; B never runs live on the Space (Gemini RPD, and a free tier that may use data), so the demo needs no Gemini key.
- The Failures tab shows gold vs predicted JSON but not the image (no test images on the Space); UI_UX_DESIGN §2.3 updated.
- No 2,000-char text cap: there is no text input.

**Open:**
- **Human action — deploy:** follow `docs/DEPLOY.md` (create the Space, set `GROQ_API_KEY` as a secret with `DEMO_MODE=true`, upload the listed paths), then add the Space URL here. **Check the first build:** if the Space builder resolves Gradio and `requirements.txt` together, the Pillow pin conflicts; the fallbacks are in DEPLOY.md.
- **p95 < 8 s for pipeline A (TRD §S7) not yet verified:** only 3 live samples, taken while an eval run shared Groq's TPM. Measure ≥ 20 live uploads when no eval is running (≈ 40K Groq tokens), or on the Space after deploy.
- Refresh before deploying: `report build` and `ui build-showcase` once M2.3/M3.1 produce test rows and `failure_examples.jsonl`. M3.1 must write `reports/failure_examples.jsonl` (`variant, case_id, gold, prediction`) for the Failures detail.
- The project has no LICENSE file; the Space front matter therefore claims none (human decision).
- Nothing committed.

---

## 2026-10-23 — M2.3 · Full 100-receipt test runs for A-naive, A and B (in progress: runs spread over free-tier days)

**Done:**
- Verified M2.2 first: its entry, the code and the gates (534 passed at its close). Test split touched for the first time here (TRD §5): no prompt or parsing change has been made since, and none will be without re-running every variant.
- **Configs** `configs/runs/{a-naive,a,b}.yaml`: CORD test, all 100 receipts (sample seed 0), the 9 metrics, primary `field_f1`. Every behaviour-changing param is spelled out (prompt version, model alias, `max_tokens`, OCR settings, `max_repairs: 2`, `reconcile_tolerance: "0.005"`, image settings), because run ids hash params as written. Verified before running: all three resolve to the **same 100 ids** (ids hash `26b37b49a2bc…`, identical to `gold-9988f5dc0330`). Run ids: `A-naive-5ed10911e9ae`, `A-e8c045ee22e6`, `B-cea8aeff49d5`.
- **Latency definition** (the open item from M1.3, settled before any test number exists):
  - `CaseOutput.wait_ms` → `ResultRow.wait_ms` (time queued for free-tier pacing, capped at the case's wall clock) → `summary.service_latency_ms`;
  - `reports/results.csv|md` `p50_ms`/`p95_ms` = latency **excluding pacing waits**, plus new `wall_p50_ms`/`wall_p95_ms`; the Markdown table says so in a sentence above it;
  - provider retry backoff (429/503) stays in the headline (a real caller sees it);
  - pre-M2.3 rows and summaries load unchanged (wait 0; the report falls back to wall clock);
  - TRD §S5 and BACKEND_SCHEMA §S2/§S3 updated.
- **Determinism check (testing requirement):** `test_report_regenerates_identically_from_the_same_runs` builds the reports twice from the same runs; `results.csv`, `results.md` and both PNG charts are **byte-identical**. Also new: the wait is recorded and excluded from service latency (and the report shows it), a wait longer than the case is capped, and an old summary falls back to wall clock.
- **Runs so far:** `A-naive-5ed10911e9ae` **paused at 34/100**, 0 errors (22 pass, 12 fail; no metric quoted until complete): `daily token budget for 'groq-oss-20b' exhausted (195317/200000 used)`. ≈ 1.9K tokens per receipt (65,203 for 34). A and B not started (Groq and Gemini daily budgets used up for 2026-10-22 UTC).
- Gates (final): `ruff check` clean, `ruff format --check` clean (95 files), `mypy src` clean (50 files, strict), `pytest -q` **551 passed, 9 skipped** (8 live, 1 symlink), `pip-audit` no known vulnerabilities.
- Security: no new input; run configs are YAML via `safe_load`, paths confined (existing). Performance: the runs pause on budget instead of failing (respecting the free tiers) and resume by case id.

- **Incident (2026-10-23, 02:19–03:29 UTC) and fix.** The day's chain reported A-naive `A-naive-5ed10911e9ae` **completed with 11 error rows** (field_f1 0.7346, counting errors as 0) and A `A-e8c045ee22e6` **completed with 93 error rows** (field_f1 0.0449) — not usable numbers. Every error was Groq HTTP 429 *"on tokens per day (TPD): Limit 200000, Used 199749"* while the local tracker showed ~143K for the UTC day: Groq's TPD is a **rolling window** (yesterday evening's ~195K still counted). The client correctly refused to wait hours inside a call but raised a plain `RateLimitedError`, which the runner records as a case error, so A raced through 93 cases in minutes.
  - Fix: a 429 with `Retry-After` above the in-call cap or a message naming a per-day limit now raises `ProviderQuotaExhaustedError` (a `BudgetExhaustedError`) without retries, and the run **pauses** instead of writing error rows.
  - New `eval run --retry-errors` re-runs cases whose last row is an error, append-only; `export_failures` now takes each case's last row (a retried pass drops out of `failures.csv`).
  - Tests: quota by `Retry-After`, quota by per-day text without `Retry-After` (1 call, no sleeps), per-minute 429 still retried, the runner pauses with no error rows, retry-errors re-runs only error rows and the summary uses the last row, failures export uses the last row. Gates after the fix: ruff/format/mypy clean, `pytest -q` **600 passed, 9 skipped**.
  - Verified live: `eval run --config configs/runs/a-naive.yaml --retry-errors` at 03:36 UTC ran 1 case, then Groq refused ("retry after 763 s") and the run **paused with 0 new error rows** (A-naive now 90/100 done by last row: 50 pass, 40 fail, 10 error waiting for retry).
  - A background loop now resumes A-naive, then A, with `--retry-errors`, every 15 min while paused (≤ 48 attempts each). The error rows stay in the files as the record of the incident; summaries and reports use each case's last row.
- **A-naive complete (2026-10-23, by each case's last row):** `A-naive-5ed10911e9ae`, 100/100, **0 errors** (55 pass, 45 fail): **field_f1 0.8200 [0.7653, 0.8702]**, items_f1 0.7934 [0.7267, 0.8624], total_f1 0.87 [0.80, 0.93], valid_json 0.94 [0.89, 0.98], reconcile 0.59 [0.49, 0.68].
  - Latency excluding pacing: p50 2,368 ms, p95 8,364 ms; wall clock: p50 28,894 ms, p95 54,282 ms.
  - Tokens: 104,102 in / 91,401 out; list cost $0.352282 per 1k (actual $0).
  - The 11 error rows were retried by the loop (111 rows for 100 cases; the incident rows remain as the record).
- **A** `A-e8c045ee22e6`: 23/100 done by last row (12 pass, 11 fail), **77 error rows still to retry**. The loop's 48 attempts ended 18:07 UTC, paused on the local UTC-day budget (196,972/200,000).
- **Day 2 scheduled** (background job, starts 00:05 UTC 2026-10-24): `b.yaml` (its 20 Gemini requests), then `a.yaml --retry-errors` every 15 min while paused (≤ 96 attempts). Estimated A completion 2026-10-24 (77 cases × ≈ 2–2.5K tokens).
- B `B-cea8aeff49d5` started after the UTC reset: **16/100, 0 errors, paused** on the local 20 requests/day (as planned).
**Files:** added `configs/runs/{a-naive,a,b}.yaml`; modified `src/receipt_extractor/eval/{tasks,records,runner,summary,report}.py`, `src/receipt_extractor/pipelines/tasks.py` (passes `llm_wait` as `wait_ms`), `tests/unit/eval/test_import_report.py`, `TRD.md` §S5, `BACKEND_SCHEMA.md` §S2/§S3, `WORKLOG.md`. Run artefacts (gitignored): `runs/A-naive-5ed10911e9ae/` (paused).

**Decisions:**
- Headline latency excludes free-tier pacing: on the free plan, waits measure Groq's token-per-minute limit, not the pipeline (M1.3: ≈ 2 s of model time showed as a ≈ 29 s case p50). Wall clock stays in the report, so nothing is hidden.
- Retry backoff on provider errors (Gemini 503s) is *not* excluded: unlike pacing, it is the provider's behaviour as experienced by any caller.

**Open — budget plan across days (CLAUDE.md §4; the same commands resume, each run picks up at the first missing case):**

| UTC day | Groq (200K tokens/day) | Gemini Flash-Lite (20 requests/day, conservative) |
|---|---|---|
| 2026-10-23 | `a-naive.yaml` (66 left ≈ 125K tokens), then `a.yaml` until paused (first replies are new requests: A's cache key differs from A-naive's) | `b-val10.yaml` (4 left; closes M2.1), then `b.yaml` (~15) |
| 2026-10-24 | `a.yaml` (≈ 190K tokens incl. re-asks) | `b.yaml` (~20) |
| 2026-10-25 | `a.yaml` remainder if any | `b.yaml` (~20) |
| 2026-10-26 → 28 | — | `b.yaml` (~20/day; 503 retries count against the budget) |

Commands (run each day in this order; a paused run exits cleanly):
```powershell
uv run receipt_extractor eval run --config configs/runs/b-val10.yaml
uv run receipt_extractor eval run --config configs/runs/a-naive.yaml
uv run receipt_extractor eval run --config configs/runs/a.yaml
uv run receipt_extractor eval run --config configs/runs/b.yaml
uv run receipt_extractor report build
```
- Expected completion: A-naive 2026-10-23, A ≈ 2026-10-24/25, B ≈ 2026-10-28 (sooner if AI Studio shows a higher RPD for `gemini-3.5-flash-lite`; then raise `rpd` in `configs/models.yaml`).
- Acceptance (`reports/results.csv` with A-naive, A and B on the same 100 ids, CIs, p50/p95 and $/1k) is **not met yet**.
- **For the human:** whether `C-local` (local Qwen3-VL-4B, $0, no limits, ≈ 45 min CPU for 100 receipts at the clean median) should also get a test-split row. It is outside the plan's M2.3 scope, so it was not run on test.

---

## 2026-10-23 — M2.4 · Pipeline C — Qwen2.5-VL-3B on Kaggle + import (notebook ready; waiting for the human's Kaggle run)

**Done:**
- **Order:** prepared while M2.3's runs wait for free-tier budgets that reset daily. M2.3 is not done or skipped: it stays first and is closed when its runs complete. The notebook needs none of M2.3's numbers (same prompt, schema and ids). Human instruction in chat (2026-10-23): continue through the plan without waiting for approval.
- **Notebook** `notebooks/qwen_vl_cord.ipynb`, **generated** by `src/receipt_extractor/pipelines/qwen_notebook.py` (`uv run python -m receipt_extractor.pipelines.qwen_notebook`), never edited by hand. From the repository it takes:
  - prompt `b-v1` verbatim (the same as pipeline B);
  - `CORD_REVISION` plus the test parquet's path and SHA-256 (`data.cord.CORD_FILES`);
  - each test case's image SHA-256 → case id from `data/cord/manifest.json`.

  The notebook finds our 100 ids **by image content** and stops if the parquet hash or any image differs or is missing. It also refuses to generate from a CORD prepared at another revision, or one where two cases share an image hash.
- **Pinned:**
  - `Qwen/Qwen2.5-VL-3B-Instruct` at commit `66285546d2b821cf421d4f5eb2576359d3770cd3` (Hub API, last modified 2025-04-06);
  - `transformers==4.51.3` and `accelerate==1.6.0` (both checked on PyPI; Qwen2.5-VL needs transformers ≥ 4.49);
  - torch, pyarrow and huggingface_hub come from Kaggle's image.
- **What the notebook does:**
  - float16 weights on a T4 (no bfloat16 there);
  - processor `max_pixels` = 1,536²; the same upright + long-side ≤ 1,536 LANCZOS downscale as B;
  - greedy decoding, ≤ 2,048 new tokens, `finish_reason` = `length` at the cap;
  - CUDA-synchronised per-receipt timing, summed into `gpu_seconds`; out-of-memory becomes an `error` line;
  - flushes `predictions.jsonl` after every receipt and skips done ids on re-run (resumable within a session);
  - writes `manifest.json` (variant `C`, CORD test, sample seed 0, alias `qwen-vl-3b`, the 9 metrics, `prediction_format: receipt_reply`, `prompt_version: b-v1`, `kaggle{notebook, gpu, gpu_seconds}`).
- **Import** (`eval/importer.py`):
  - new manifest field `prediction_format` (`prediction` default | `receipt_reply`) and `prompt_version`; optional `finish_reason` on prediction lines;
  - with `receipt_reply`, each reply text goes through `pipelines/replies.extraction_from_reply` → `ExtractionResult`. That uses the same `parse_receipt_text` (code fence tolerated, `Receipt` validation, so only `parse_amount` interprets numbers) and the same `review()` as A and B: attempts 1, `raw_text` kept when invalid, `truncated` at the token cap, $0, `stage_latency_ms.llm` = notebook latency;
  - an invalid reply is an invalid *prediction* (`valid_json = 0`), not an import error;
  - a non-string prediction under `receipt_reply` rejects the import;
  - **Mismatched ids:** a case id outside the manifest's sample now **rejects the whole import** (previously skipped with a warning). The id and duplicate checks run as a pre-pass, so a bad file leaves no partial run.
- `pipelines/replies.py`: `parse_receipt_reply` now delegates to a text-based `parse_receipt_text` (behaviour unchanged).
- Tests (+17 unit):
  - `tests/unit/eval/test_import_receipt_replies.py` (6): a code-fenced valid reply → pass with `ExtractionResult` fields (amounts "51.000" → 51000, model/prompt version, reconciled, latency, tokens, $0); valid but sums off → needs_review; invalid → `valid_json` false + raw_text; truncated; a non-text prediction rejected; **id not in sample and duplicate id → rejected before anything is written**; missing cases → error rows; the default format keeps predictions as given.
  - `tests/unit/pipelines/test_qwen_notebook.py` (7): every code cell parses; prompt equals `b-v1`; revisions and SHA-256s pinned; pip pins exact; case map equals the manifest; the manifest the notebook writes validates as `ImportManifest` and names the `offline` alias; wrong revision / ambiguous hashes refused; deterministic render; **the committed notebook equals the generator's output**.
  - The shared Kaggle fixture lost its deliberate out-of-sample line (now its own rejection test).
- Lint: ruff lints `notebooks/*.ipynb` with T201/S101/E501/PTH123 ignored (progress prints, fail-loud asserts, generated constant lines) and does not format it (the generator owns its layout).
- Gates: `ruff check` clean, `ruff format --check` clean (95 files), `mypy src` clean (50 files, strict), `pytest -q` **551 passed, 9 skipped**. `pip-audit`: no dependency changed since the last clean audit; re-run at M2.3's close.
- Security: the import is a trust boundary (a file from Kaggle).
  - Paths are confined to `data/` (existing).
  - Lines are size-capped and parsed as JSON only.
  - Reply text is validated by the strict `Receipt` model (≤ 500 items, bounded strings and amounts).
  - Error texts are sanitised.
  - A wrong or duplicate id aborts before any write.

  The notebook downloads only pinned revisions and checks the dataset parquet by SHA-256. Model weights are pinned by commit but not hash-checked file by file; the Hub serves them by commit, and LFS files are content-addressed. The notebook sends no data anywhere.
- Licence: `Qwen/Qwen2.5-VL-3B-Instruct` is under the **Qwen Research licence** (`qwen-research`, not gated). That suits a non-commercial benchmark; the human decides before publishing row C (noted in the notebook header too).

**Files:** added `src/receipt_extractor/pipelines/qwen_notebook.py`, `notebooks/qwen_vl_cord.ipynb`, `tests/unit/eval/test_import_receipt_replies.py`, `tests/unit/pipelines/test_qwen_notebook.py`; modified `src/receipt_extractor/eval/importer.py`, `src/receipt_extractor/pipelines/replies.py`, `tests/unit/eval/test_import_report.py`, `pyproject.toml` (ruff settings for the notebook only), `TRD.md` §3/§4.2, `BACKEND_SCHEMA.md` §S2 (Kaggle import), `README.md`, `WORKLOG.md`.

**Decisions:**
- The notebook returns **raw replies** and the repository validates them, so the notebook carries no copy of `Receipt` or `parse_amount`, and C can never disagree with A and B on how "1.000" reads.
- Ids are mapped by **image SHA-256**, not by re-implementing CORD's id derivation in the notebook (one source of truth, integrity check included).
- Same prompt as B, with no JSON-schema constrained decoding (plain transformers has none); invalid output therefore counts against C, which is the comparison the table should show.
- Out-of-sample ids reject the import (M2.4 testing requirement) instead of being skipped.

**Open:**
- **Human action (Kaggle):**
  1. kaggle.com → New Notebook → File → Import Notebook → upload `notebooks/qwen_vl_cord.ipynb`.
  2. Settings: Accelerator **GPU T4 ×1**, Internet **On**.
  3. Run All (≈ 3 GB of downloads, then ~100 receipts; resumable).
  4. From Output `/kaggle/working/qwen_vl/`, download `predictions.jsonl` and `manifest.json` into `data/imports/qwen_vl/`.
  5. Run:
     ```powershell
     uv run receipt_extractor eval import-predictions data/imports/qwen_vl
     uv run receipt_extractor report build
     ```
  Then row `C` exists and M2.4 closes (record the run id, field_f1 and GPU seconds here).
- The notebook has not been executed here (no CUDA GPU); its code cells are syntax-checked and its data/ids logic is tested only through the generator. First-run issues on Kaggle (e.g. a transformers API change) should be fixed in the generator, not in Kaggle.
- Decide on publishing row C under the Qwen Research licence.

---

## 2026-10-23 — M2.2 · Retries, arithmetic reconciliation and review flag

**Done:**
- Verified M2.1 first: code, tests and gates are in place (509 passed before M2.2) and its C-local run is complete. **M2.1 itself is still open on one external step** — B's validation run is paused at 6/10 on the Gemini free-tier daily budget, which resets at 00:00 UTC. M2.2 needs no Gemini call, so it went ahead in plan order. The human's instruction in chat (2026-10-23) was to continue through the plan without waiting for approval.
- **Up to 2 re-asks** (FR-5, TRD §4.3): `LLMRequest.max_repairs` (1–2; default 1 keeps every pre-M2.2 caller and cache entry unchanged). The client's single repair became a bounded loop in a new private `LLMClient._repair` (extracted to stay within the statement limit, not suppressed): each re-ask appends the previous reply plus its Pydantic errors to the conversation so far; stages `llm:repair`, `llm:repair2`; a failed repair *call* returns the last invalid reply uncached, with every call so far in the accounting. `LLMResponse.attempts` is the exact number of calls (cached too; for older entries it is inferred from `repaired`).
- **Cache rule:** `max_repairs` is **not** part of the cache key. A cached entry is reused only when consistent with the request's budget (a valid reply that needed no more repairs than allowed, or an invalid reply after exactly the allowed number); otherwise it is a miss and the new result replaces it. So switching A and B to 2 repairs re-sends nothing that was already valid. A first version put `max_repairs` in the key, which would have re-sent every A and B call (Gemini allows only 20 requests/day); caught before any run used it.
- **Review:** `core/reconcile.reconcile`/`tolerance` take `rel_tolerance`/`abs_tolerance` (defaults unchanged, validated non-negative) and report `allowed`. `pipelines/replies.review()` sets `reconciled` (null without a valid receipt), `reconcile_diff` (item sum − subtotal when that check fails, else totals; 0 when reconciled) and `needs_review` (invalid after retries, nothing extracted, or not reconciled). Applied in `PipelineA` (engineered only — `A-naive` keeps all three null) and `PipelineB` (B and C-local). There is no re-ask on an arithmetic mismatch; the flag is for a human (TRD §4.3).
- Run params `max_repairs` (1–2, default 2) and `reconcile_tolerance` (0–0.1, default 0.005) on `pipeline_a` and `pipeline_b`; `ExtractionResult.attempts` allows up to 3.
- **Acceptance evidence** (validation, same 10 ids). The params are written explicitly into `configs/runs/{a-val10,c-local-val10}.yaml`, because run ids hash the params *as written*: without them the configs would have resumed the finished M1.3/M2.1 runs. Results:
  - `A-24c9db2a7d6d`: 10/10, 0 errors, field_f1 0.9389 [0.8389, 1.0000].
  - `C-local-f570f3318f3d`: 10/10, 0 errors, field_f1 0.9000 [0.7000, 1.0000].
  - Every row has `attempts`, `reconciled`, `needs_review` and `reconcile_diff` populated. Both runs were served entirely from the cache (10/10 hits, **0 provider calls**), which confirms the cache rule. All first replies were valid, so `attempts` = 1 everywhere: no re-ask happened on real data, and the repair path is evidenced by the unit tests.
  - 9/10 reconciled in each run; validation-0005 is `needs_review` with `reconcile_diff` 727 in both.
- **validation-0005 is a false positive of the specified rule:** the gold itself does not reconcile. The item price 8,000 includes tax, while the subtotal is printed net (7,273 + 727 tax = 8,000). On validation gold this "item prices include tax" layout accounts for **2 of the 18** non-reconciling receipts (18/100 overall). The rule was left as TRD §4.3 specifies; this is an M3.1 candidate.
- The flag cannot catch consistent-but-wrong extractions: validation-0038 (A, C-local) and -0065 (A) are wrong yet reconcile.
- Tests (+25 unit):
  - `tests/unit/test_llm_client.py`: second repair runs with the full conversation and numbered stages; stops at the first valid reply; gives up after `max_repairs`; a failed second repair returns parts so far, uncached; a valid cached reply is reused under a larger budget; an invalid cached reply is retried when more repairs are allowed and the better answer replaces it; a reply that needed 2 repairs is not served to a 1-repair request; attempts kept on cache hits and inferred for old entries; `max_repairs` bounded.
  - `tests/unit/pipelines/test_review.py` (new): `review()` for a receipt that adds up, item sum off (diff = item diff), total off, **rounding within tolerance → reconciled** and stricter tolerance → not, tax-included layout → diff 0, no receipt → needs review; tolerance configurable and validated, scorer default untouched. Pipelines: **invalid→valid on retry** (3 calls, repair prompt carries the errors); **invalid twice with one repair → needs_review**; **valid but sums off → needs_review** in A and B; rounding within tolerance reconciled in B; naive bypasses retries and checks; empty OCR in engineered mode → needs review.
  - `tests/unit/pipelines/test_tasks.py`: the two params validated on both tasks.
  - Updated for the new contract: A's placeholder "flags null until M2.2", and the "gives up after one repair" tests in A, B and the B runner pass (now 3 calls).
- Gates: `ruff check` clean, `ruff format --check` clean, `mypy src` clean (49 files, strict), `pytest -q` **534 passed, 9 skipped** (8 live, 1 symlink), `pip-audit` no known vulnerabilities.
- Security: no new input or trust boundary. Repair prompts carry only Pydantic error locations and messages (`include_input=False`), never the invalid values. Each re-ask goes through the same data-policy check, budget, limiter and accounting as the first call, and the loop is capped at 2 by schema validation on `LLMRequest`.
- Performance: at most 2 extra calls, only on invalid replies; the review is O(items). Free-tier impact is limited to receipts that need a re-ask, and the cache rule avoids re-sending valid ones.

**Files:** added `tests/unit/pipelines/test_review.py`; modified `src/receipt_extractor/llm/{client,types}.py`, `src/receipt_extractor/core/{reconcile,extraction}.py`, `src/receipt_extractor/pipelines/{replies,pipeline_a,pipeline_b,tasks}.py`, `configs/runs/{a-val10,c-local-val10}.yaml`, `tests/unit/test_llm_client.py`, `tests/unit/pipelines/{test_pipeline_a,test_pipeline_b,test_tasks}.py`, `TRD.md` §4.3/§S3, `BACKEND_SCHEMA.md` §1.2, `APPFLOW.md` §3, `WORKLOG.md`. Run artefacts (gitignored): `runs/A-24c9db2a7d6d/`, `runs/C-local-f570f3318f3d/`.

**Decisions:**
- "≤ 2 attempts" means up to 2 re-asks (≤ 3 calls), following M1.3's recorded reading; it is a run param, so a 1-re-ask variant stays one config line away.
- The loop lives in the LLM layer (one audited path; accounting per call), and the review lives in `pipelines/replies.py`, shared by every pipeline.
- `needs_review` is true for anything without a valid, reconciling receipt, including `ocr_empty`; `reconciled` stays null when there is nothing to check, so "not checked" never reads as "doesn't add up".
- The pipeline's tolerance is separate from the scorer's: tuning the flag never changes the `reconcile` metric.
- The reconcile rule was not extended for tax-inclusive item prices (2/100 on validation): that would change the scorer's calibrated metric, and it belongs to M3.1's "one fix" if it ranks highest.

**Open:**
- **M2.1 completion (blocked until 00:00 UTC, 05:30 local):** `uv run receipt_extractor eval run --config configs/runs/b-val10.yaml` resumes `B-661916019452` (4 receipts left), then M2.1 is closed in its entry.
- ~~B's M2.2 validation run~~ — done 2026-10-23: `b-val10.yaml` now spells out `max_repairs: 2` and `reconcile_tolerance: "0.005"`; run **`B-8ddf1a98d199`: 10/10, 0 errors, field_f1 0.9500 [0.8500, 1.0000]**, 10/10 served from the cache (**0 Gemini requests**), flags populated: 9 reconciled, validation-0005 `needs_review` (diff 727, the same tax-inclusive false positive as A).
- Process note: run ids hash params as written, not code defaults. A code change to defaults plus an unchanged config would resume an old run with mixed behaviour, so M2.3's test configs must spell out `max_repairs`/`reconcile_tolerance` (and other behaviour-changing params).
- M3.1 candidates: tax-inclusive item prices (reconcile false positive); consistent-but-wrong extractions that the flag cannot catch (validation-0038, -0065).
- No human action needed for M2.2 itself. Nothing committed.

---

## 2026-10-22 — M2.1 · Pipeline B — Gemini Flash-Lite vision + local C-local variant (done 2026-10-23)

**Done:**
- Verified M1.3 first: commit `74b7534`, its WORKLOG entry, `pipelines/` (protocol, OCR, prompts, pipeline A, eval task), `ExtractionResult`; gates green at start (436 passed). Reused the `Pipeline` protocol, `ExtractionResult`, the LLM layer (data policy, cache, budget, repair, accounting, `FakeProvider`), the eval runner/task registry, `confine` + manifest SHA-256 checks, and A's untrusted-image checks.
- **Shared image checks** `pipelines/images.py`: the size/format/dimension checks moved out of `ocr.py` unchanged (OCR re-imports them; its 25 tests pass as before) plus `prepare_model_image`: decode → EXIF transpose → downscale to `max_side` (default 1,536 px on the long side, LANCZOS, never upscaled) → re-encode in memory (JPEG q90 default, PNG optional), which also drops EXIF/GPS metadata; fails if the encoded copy exceeds 8 MB.
- **Pipeline B** `pipelines/pipeline_b.py` (variant `B`): refuses any `origin` other than `public_dataset` with `DataPolicyError` **before the image is decoded**; image → `prepare_model_image` → `gemini-flash-lite` with `Receipt` as `response_model`, temperature 0, run seed, `max_tokens` 8,192, the layer's one repair. Records `image_width`/`image_height` (as sent), `image_tokens_in` and a new `prepare` stage latency. Prompt `b-v1` (`pipelines/prompts.py`): a-v3's field rules for an image, printed text declared untrusted, amounts as printed strings so `parse_amount` stays the only interpreter; the image goes first, then one instruction.
- `pipelines/replies.py`: reply → `Receipt` | `schema_invalid` / `truncated` + capped `raw_text`, now shared by A and B (A's behaviour unchanged; M2.2 builds on it).
- **Eval task** `pipeline_b` (`pipelines/tasks.py`): strict params (`model` alias, `prompt_version` must be a vision prompt, `max_tokens`, `image` settings); A and B now share one lazy-build/case base (`_PipelineTask`). `configs/runs/b-val10.yaml`: same split, sample (10, seed 0) and metrics as `a-val10`, so the same 10 ids.
- **LLM layer (supporting changes):** `image_tokens_in` on `ChatResult` → `LLMResponse` (summed over a repair) → `LLMCallRecord`/`llm_calls.jsonl` and the cache entry (optional, so entries written before M2.1 still hit; tested). LiteLLM reports Gemini's per-modality usage as `usage.prompt_tokens_details.image_tokens`; `None` = not reported (never 0). **Gemini schema fix:** Gemini answered every `Receipt` request with HTTP 400 `INVALID_ARGUMENT`. Bisected with tiny synthetic text requests: plain text OK, seed OK, a one-field schema OK, `Receipt` schema 400; number/string unions normalised → still 400; `$defs` inlined → still 400; without `maxItems`/`minLength`/`maxLength` → OK. The adapter now strips `maxItems`/`minItems`/`minLength`/`maxLength` from the schema **sent** to `gemini/` models (`provider_schema`); property names and data values (`default`, `enum`, …) are untouched, the client still validates replies against the full Pydantic model, and the cache key still uses the full schema. Groq requests are unchanged.
- **Model switch (human request in chat):** B uses the new alias `gemini-flash-lite` → `gemini/gemini-3.5-flash-lite` (`public_only`). `gemini-flash` (3.8 Flash) stays registered but unused: on 2026-10-22 it returned sustained HTTP 503 "model is currently experiencing high demand" (10 requests, all 503). Pricing page (checked 2026-10-22): free tier available, free-tier data may be used by Google, paid list price $0.30 in / $2.50 out per 1M tokens (`configs/pricing.yaml`). Limits: `rpm 15, tpm 250000` from LiteLLM's model map; **`rpd 20` is a conservative placeholder** copied from `gemini-flash` (Google shows free-tier limits only in AI Studio).
- **Live smoke test** `tests/live/test_pipeline_b_live.py` (`-m live`; real CORD `validation-0000`, cache off): **1 passed** (schema valid, total = gold, `image_tokens_in` reported and < `tokens_in`).
- **Validation run (acceptance — not yet complete):** `uv run receipt_extractor eval run --config configs/runs/b-val10.yaml` → run `B-661916019452`, **status `paused` at 6/10 cases, 0 errors**: `daily request budget for 'gemini-flash-lite' exhausted (20/20 used)`. Gemini 503s (and one timeout) were retried with backoff, and every attempt counts against the local daily budget. Preliminary per-case (not a result; final numbers come from the completed run's `summary.json`):

  | case | status | field_f1 | attempts | image sent | image tokens | tokens in/out | list cost |
  |---|---|---|---|---|---|---|---|
  | validation-0005 | pass | 1.000 | 1 | 960×1280 | 1,064 | 1,584 / 102 | $0.00073 |
  | validation-0033 | pass | 1.000 | 1 | 576×864 | 1,080 | 1,600 / 147 | $0.00085 |
  | validation-0038 | fail | 0.500 | 1 | 864×1536 | 1,100 | 1,620 / 84 | $0.00070 |
  | validation-0049 | pass | 1.000 | 1 | 1108×1478 | 1,064 | 1,584 / 164 | $0.00089 |
  | validation-0051 | pass | 1.000 | 1 | 960×1280 | 1,064 | 1,584 / 154 | $0.00086 |
  | validation-0053 | pass | 1.000 | 1 | 576×864 | 1,080 | 1,600 / 220 | $0.00103 |

  All 6 schema-valid on the first attempt. validation-0038 is the receipt A also misses (`TT` item vs tax).
- **Local vision variant `C-local` (human request in chat, 2026-10-22/23):** Qwen3-VL-4B on the laptop CPU through Ollama (0.32.6, already installed by the human), on B's code path. Findings while wiring it:
  - The human's `qwen3-vl:4b` has the digest of **`qwen3-vl:4b-thinking`** (`a1b29c246319`); under a JSON schema it answers in Ollama's `thinking` field (content empty) with only `{total, currency}`, and `think: false` does not change that; without a schema the reply ran past a 15-min probe. The human pulled **`qwen3-vl:4b-instruct`** (`84f7ed8042fa`, Q4_K_M, capabilities completion/vision/tools).
  - The instruct build *also* returned only `{total, currency}` (23 tokens): Ollama decodes against the schema as a grammar and the small model writes only the required keys (`Receipt` requires just `total`). With every property marked required in the schema sent, the same receipt came back complete (3 items, null subtotal/tax, correct total). Fixed in the adapter (`provider_schema` → `require_all_properties` for `ollama/`/`ollama_chat/`); replies are still validated against the real `Receipt`.
  - Layer support: `ollama_chat/` (chat route, needed for images) accepted for provider `ollama`; per-alias `options` from an allow-list (`num_ctx`, `num_thread`, `keep_alive`) that cannot override model/key/endpoint/timeout/retries, in the cache key only when set; `PipelineB` takes `variant` and `timeout_s` (≤ 600 s); task params `variant`, `timeout_s`.
  - Alias `ollama-qwen3-vl-4b` → `ollama_chat/qwen3-vl:4b-instruct`, `options: {num_ctx: 8192}`, `data_policy: any`, $0 price entry; run config `configs/runs/c-local-val10.yaml` (images ≤ 1,024 px, timeout 600 s, concurrency 1). The dataset-only origin guard still applies to `C-local`.
  - Live smoke test `tests/live/test_local_vision_live.py` (skips without Ollama/the model/CORD): **1 passed** (47 s incl. model load; items present, total = gold).
  - **Validation run `C-local-121f112aed1c`: completed 10/10, 0 errors, field_f1 0.9000 [0.7000, 1.0000]**, items P/R/F1 0.90, subtotal_f1 0.857 (n 7), tax_f1 0.833 (n 6), total_f1 0.90, valid_json 1.00, reconcile 0.90, 15,618 in / 1,348 out tokens, $0. The miss is validation-0038 (field_f1 0), the receipt A and B also miss.
  - **Latency:** the run's p50 144 s / p95 155 s is **not representative** — the full test suite was running on the same CPU at the time. Clean re-timing, nothing else running, cache off, warm model: validation-0005 26.8 s, -0053 32.7 s, -0097 16.3 s → **median 26.8 s per receipt** (≈ 1.55K input incl. image, 80–190 output tokens). The first call after start-up adds ≈ 2.5 min to load the model.
- **Acceptance met (2026-10-23, after the UTC budget reset):** `B-661916019452` resumed and **completed 10/10, 0 errors, field_f1 0.9500 [0.8500, 1.0000]** (4 new Gemini requests). Same 10 ids (validation, M2.1 code): A-naive 0.9389, A 0.9389, **B 0.9500**, C-local 0.9000.
- **Same 10 ids so far (validation, M2.1 code), as recorded before the resume:** A-naive `A-naive-1a8804eab2a8` 0.9389, A `A-a5618f0e9121` 0.9389, C-local 0.9000; B 6/10 done (5 at 1.0, validation-0038 0.5) — final B number when the run completes.
- **Image tokens:** ≈ 1,064–1,100 per receipt whether 576×864 or 1108×1478 was sent: Gemini 3.x bills a roughly fixed per-image budget here, so downscaling reduces the payload and the `prepare` time, not the token cost. 1,536 px kept (CORD images are ≤ 1,536 on the long side after resize; a lower cap would only risk legibility).
- Tests (+57: 56 unit + 1 live): `test_images.py` (14: long-side downscale, no upscale, PNG/custom size, WebP/JPEG sources, EXIF rotation + metadata stripped, bad bytes, oversize, huge header dimensions, bounded settings). `test_pipeline_b.py` (16, FakeProvider): valid reply + image metadata + record, request shape (downscaled JPEG data URL, schema, temperature, seed, prompt), image settings applied, repair once (image tokens summed), invalid after repair, truncated; **policy refusal**: `user_upload` and `synthetic` raise `DataPolicyError` before the image is decoded (decoder patched to fail) with no provider call or record, also when B is pointed at a model without a policy; the layer refuses a user upload to a `public_only` alias on its own; every Gemini alias in `configs/models.yaml` is `public_only` and the default is `gemini-flash-lite`; cache re-run keeps image tokens; rejected image never reaches the model; prompt version checks; serialised form. `test_tasks.py` (+12: registration/defaults, 6 bad params, case image sent as `public_dataset`, tampered image, 3 full runner passes). `test_llm_client.py` (+4: image tokens over repair and per record, unknown stays `None`, cache hit keeps them, pre-M2.1 cache entry still hits). `test_llm_providers.py` (+10: image-token parsing ×6, Gemini schema stripping keeps names/data and does not mutate, `Receipt` sent to Gemini has no length bounds while Groq gets the full schema, adapter sends the provider schema ×2). Two existing assertions updated for the new `prepare` stage key.
- Gates (B code, 2026-10-22): `ruff check` clean, `ruff format --check` clean (90 files), `mypy src` clean (49 files, strict; checks `PipelineB` against the `Pipeline` protocol), `pytest -q` **492 passed, 8 skipped** (7 live, 1 symlink), `pip-audit` no known vulnerabilities (no new dependencies).
- Gates after the C-local work (2026-10-23): `ruff check`/`format --check` clean, `mypy src` clean, `pytest -q` **509 passed, 9 skipped** (8 live, 1 symlink) (+17 unit over 492: registry prefixes and option allow-list, client options and cache key, adapter options and Ollama required-schema, B variant/timeout, task params and the C-local config), live C-local smoke 1 passed.
- Security: two independent guards keep user content away from Gemini (pipeline: origin check before decoding; layer: `public_only` on every Gemini alias), both tested; the eval task only ever passes `public_dataset` for manifest-verified CORD images confined to `data/`; images are validated before decoding, processed in memory, re-encoded without metadata, never logged (logs/records carry sizes, tokens, ms only); printed text in the image is declared untrusted in the prompt; the schema stripping only relaxes what is *sent* — replies are still validated against the full `Receipt` bounds (≤ 500 items, names ≤ 300 chars). Bisect probes used synthetic text only.
- Performance: `prepare` 19–609 ms per receipt. `stage_latency_ms.llm` was 32–102 s on this run, but that includes 503/timeout retry backoff (the layer counts backoff as provider time), so it is not model latency.

**Files:** added `src/receipt_extractor/pipelines/{images,pipeline_b,replies}.py`, `configs/runs/{b-val10,c-local-val10}.yaml`, `tests/unit/pipelines/{test_images,test_pipeline_b}.py`, `tests/live/{test_pipeline_b_live,test_local_vision_live}.py`; modified `src/receipt_extractor/llm/registry.py`, `tests/unit/test_llm_components.py` (C-local), and `src/receipt_extractor/pipelines/{__init__,ocr,pipeline_a,prompts,tasks}.py`, `src/receipt_extractor/core/extraction.py`, `src/receipt_extractor/llm/{client,fake,providers,types}.py`, `configs/models.yaml`, `configs/pricing.yaml`, `tests/unit/pipelines/{test_pipeline_a,test_tasks}.py`, `tests/unit/{test_llm_client,test_llm_providers}.py`, `PRD.md` FR-4, `TRD.md` §2/§3/§4.2/§S2/§S3, `BACKEND_SCHEMA.md` §1.2/§2/§S2, `APPFLOW.md` §2, `README.md`, `WORKLOG.md`. Run artefacts (gitignored): `runs/B-661916019452/` (paused).

**Decisions:**
- B refuses everything but `public_dataset`, stricter than the alias policy (which also allows `synthetic`): FR-4 says CORD images only. Hence the live smoke test uses a real CORD receipt, not a synthetic one.
- B uses the engineered path (one repair) like `A`; there is no `B-naive` (not in the plan). M2.2 adds retries/reconciliation to both.
- `gemini-flash-lite` as a new alias rather than repointing `gemini-flash`, so the alias name keeps meaning one model and earlier references stay valid.
- Gemini's schema limits are handled in the provider adapter (per provider), not by weakening `Receipt`: the contract keeps its bounds and they are still enforced.
- `image_tokens_in` is `None` when the provider does not report it, so "unknown" never reads as "free".
- `C-local` is an **extra** variant next to the planned Kaggle M2.4 (`C`), not a replacement: the human did not choose between the two, and keeping both changes no task order. PRD FR-9 and TRD §3/§4.2 record it.
- Provider quirks are handled in the adapter per provider (Gemini: strip length bounds; Ollama: require every property), never by weakening `Receipt`.
- Temperature 0 kept for comparability with A, although LiteLLM warns that Gemini 3+ plans to remove `temperature` (still honoured today).

**Open:**
- ~~Acceptance not met yet~~ — met 2026-10-23 (`B-661916019452` completed, see Done).
- **Human action:** confirm `gemini-3.5-flash-lite`'s free-tier RPM/RPD at https://aistudio.google.com/rate-limit and update `configs/models.yaml` (`limits`, `limits_checked`). If the real RPD is higher than 20, the run can resume today.
- **M2.3 budget plan:** 100 test receipts for B ≈ 100–130 requests incl. repairs and 503 retries → **≈ 5–7 days at 20 RPD**, fewer once the real RPD is confirmed. Failed attempts count against the local budget; consider not counting 503 "high demand" attempts if Google confirms they are not billed against RPD (needs evidence first).
- **Latency reporting (M2.3):** B's `llm` stage includes retry backoff for 503s; report model time separately (e.g. from successful attempts only) or define latency explicitly before publishing p50/p95.
- `gemini-flash` (3.8 Flash) is unusable today (sustained 503s); left registered.
- C-local on the 100 test receipts ≈ 45 min of CPU at the clean median (run it with nothing else on the machine, or the latency numbers are meaningless). Whether C-local goes into the M2.3 table is open for the human; it is not in the plan's M2.3 scope (A-naive, A, B).
- Nothing committed.

---

## 2026-10-22 — M1.3 · Pipeline A — RapidOCR + Groq structuring (naive and engineered modes)

**Done:**
- Verified M1.2 first: commit `075c3c3`, its WORKLOG entry, `eval/scorer.py` + registry metrics, `core/reconcile.py`, the `gold` task; gates green at start. Reused `Receipt`/`parse_amount` (the only amount interpreter), the LLM layer (cache, limits, repair, accounting, `FakeProvider`), the eval runner/task registry, `eval.paths.confine` and the `cord` dataset kind.
- **Pipeline protocol** `pipelines/base.py`: `async extract(image: bytes, *, origin, tags) -> ExtractionResult`; `origin` flows into every `LLMRequest.data_origin`, so `public_only` models refuse user uploads. `core/extraction.py`: strict `ExtractionResult` (BACKEND_SCHEMA §1.2) with `stage_latency_ms{ocr, llm, llm_wait, validate}`, tokens, list cost, `cache_hit`, `failure` (`ocr_empty`/`schema_invalid`/`truncated`), `raw_text` only for invalid replies; OCR text excluded from the serialised form.
- **OCR stage** `pipelines/ocr.py`: untrusted-image checks before decoding (≤ 32 MB, PNG/JPEG/WebP magic, ≤ 8,192 px/side and ≤ 40 MP from the header, EXIF transpose, in memory only, decoder messages never echoed); RapidOCR 3.10 with the PP-OCRv6 small det/rec models **bundled in the wheel**, passed by explicit path (no download branch can run; engine built lazily); one engine call at a time, latency measured inside the lock; > 2,000 ms → metadata-only warning; boxes → printed lines (vertical overlap) joined with ` | `.
- **Prompts** `pipelines/prompts.py`: versioned (`a-v1`, `a-v2`, `a-v3`; the version is a run param → part of the run id; published versions never edited). OCR text between `<ocr_text>` tags, tag look-alikes replaced, capped at 16,000 chars; the system prompt says it is untrusted data and its instructions must not be followed; braces are safe (no `str.format`). Amounts asked for as printed strings so `parse_amount` stays the single interpreter.
- **Pipeline A** `pipelines/pipeline_a.py`: OCR → `groq-oss-20b` (alias from `models.yaml`) with `Receipt` as `response_model`, temperature 0, run seed, `max_tokens` 4,096. `naive` (`A-naive`) = one call, `repair=False`, invalid reply final; `engineered` (`A`) = the layer's one repair (M2.2 adds retries ≤ 2, reconciliation, `needs_review`). Empty OCR → no model call.
- **Eval task** `pipelines/tasks.py` (`pipeline_a`, registered lazily): strict params (`mode`, `model` alias pattern — a LiteLLM model string is rejected, `prompt_version`, `max_tokens`, `ocr` settings); client built on first case with the disk cache **ON** and `runs/<run_id>/llm_calls.jsonl` as sink; image path confined to `data/` and its manifest SHA-256 checked; `details.ocr_text` stored (public CORD data). `CaseContext` gained `root`.
- **LLM layer (supporting changes):** `LLMRequest.repair` (default `True`; added to the cache key only when `False`, so existing entries stay valid). `wait_ms` on `LLMCallRecord`/`LLMResponse`/`EmbedResponse`: time queued for the daily budget, rate limiter or a concurrency slot; `latency_ms` now excludes it. Groq HTTP 400 `json_validate_failed` on a structured request is returned by the adapter as an invalid reply (`failed_generation`, `finish_reason=schema_rejected`) instead of raising.
- **Acceptance evidence** (`configs/runs/a-naive-val10.yaml`, `configs/runs/a-val10.yaml`: CORD validation, `sample.size 10, seed 0`, concurrency 2). Same 10 ids for both variants:

  | Prompt | Run | field_f1 [95 % CI] | valid_json | items_f1 | subtotal_f1 | tax_f1 | total_f1 |
  |---|---|---|---|---|---|---|---|
  | a-v1 | `A-naive-0b8b3d119990` | 0.8309 [0.5906, 1.0000] | 0.90 | 0.80 | 0.625 | 0.667 | 0.90 |
  | a-v1 | `A-4ec4fcf34606` | 0.8400 [0.6200, 1.0000] | 0.90 | 0.80 | 0.714 | 0.667 | 0.90 |
  | a-v2 | `A-naive-0bc459683abc` | 1.0000 [1.0000, 1.0000] | 1.00 | 1.00 | 1.000 | 1.000 | 1.00 |
  | a-v2 | `A-f4cfab44855e` | 0.9000 [0.7000, 1.0000] | 0.90 | 0.90 | 0.833 | 0.800 | 0.90 |
  | **a-v3** | **`A-naive-1a8804eab2a8`** | **0.9389 [0.8389, 1.0000]** | 1.00 | 0.90 | 0.833 | 0.833 | 1.00 |
  | **a-v3** | **`A-a5618f0e9121`** | **0.9389 [0.8389, 1.0000]** | 1.00 | 0.90 | 0.833 | 0.833 | 1.00 |

  10/10 rows, 0 error rows in every run. Per-stage latency recorded per case (`prediction.stage_latency_ms`) and per call (`llm_calls.jsonl`); final runs: OCR 1.35–1.99 s per receipt, LLM 0.8–12.3 s (median ≈ 2 s), `llm_wait` up to 46.6 s (Groq TPM pacing, now kept apart). The v1 runs were measured before the `wait_ms` split, so their `llm` includes pacing. ≈ 1.07K input + 0.2–2.7K output tokens per receipt (gpt-oss reasoning), list cost ≈ $0.0003/receipt, actual $0.
- **Eyeballed outputs (validation only) and prompt fixes:** v1 → v2: validation-0005 OCR read `8.000` as `B.000` and "copy exactly as printed" kept the letter → invalid (the repair repeated it); validation-0038 an unlabelled header number `820.000` became the subtotal and the item `TT` became tax; validation-0053 (naive) a subtotal equal to the total was invented. v2 → v3: validation-0049 the model put `1×` (OCR `1×52.000`) into `qty`, which is not a number, so the whole receipt failed validation in `A`; that `A` repair call also came back as Groq 400 `json_validate_failed`, which surfaced the adapter gap fixed above. Remaining v3 misses: validation-0038 `TT` read as tax again (v2 had it right in both modes: model variance at temperature 0 with a seed), validation-0065 the gold treats `TOTAL` before `TAX` and `GRAND TOTAL` as the subtotal. Stopped tuning here to avoid fitting 10 receipts.
- **FR-6 cache evidence:** the v3 `A` config re-run into a scratch run dir (removed afterwards): 10/10 `cache_hit`, Groq daily budget unchanged (65 requests / 120,167 tokens before and after), receipts identical to `A-a5618f0e9121`.
- **OCR performance (TRD §6, < 2 s):** all 100 validation images through the final OCR stage: mean 1,638 ms, p50 1,668 ms, p95 2,133 ms, max 2,483 ms (validation-0041); **13/100 over 2 s**, each logged. Default RapidOCR settings measured ≈ 4.0 s mean on 6 receipts (detection 2–3 s); no angle classifier saved ≈ 0.7 s; detection at max side 960 and 4 ORT threads brought it to ≈ 1.6 s and found more boxes (129 vs 71 on 10 receipts). 0/100 empty OCR.
- Tests (71 new: 69 unit + 2 live): `tests/unit/pipelines/test_ocr.py` (25: PNG/JPEG/WebP → BGR, EXIF rotation, 5 bad byte inputs, magic/content mismatch, oversize bytes, 3 huge-dimension headers rejected before decoding, no content echo, line grouping/order/skew/blank boxes, stage latency + over-budget log with metadata only, rejected image never reaches the engine, **real RapidOCR on a synthetic receipt with sockets blocked**). `test_pipeline_a.py` (18, FakeProvider + mocked OCR): valid reply in both modes (amount normalisation `51.000` → 51000, tokens, cost, latencies, schema/temperature/seed sent, call tags), naive invalid reply final after exactly 1 call, engineered repair (2 calls, repair prompt, stages `llm`/`llm:repair`), give-up after one repair, truncated, schema-rejected reply in both modes, empty OCR → no call, delimiter/injection neutralisation, user upload refused by a `public_only` alias, cache re-run, naive/engineered separate cache entries, serialised form, prompt version checks. `test_tasks.py` (16): registry + param validation (incl. raw model strings rejected), image SHA-256 tamper, path confinement (`..`, `configs/`, absolute), malformed input, full runner pass for naive valid/invalid and engineered repair (results, `details.ocr_text`, `llm_calls.jsonl`, summary). `test_llm_client.py` (+4: repair off, cache separation, key unchanged without schema, queue wait vs latency), `test_llm_providers.py` (+6: schema rejection → reply, empty generation, other 400s still raise, non-400/garbage ignored). Live: `tests/live/test_pipeline_a_live.py` (`@pytest.mark.live`, synthetic receipt, real OCR + Groq, both modes) — **2 passed** on the final code.
- Gates: `ruff check` clean, `ruff format --check` clean (84 files), `mypy src` clean (46 files, strict; it also checks that `PipelineA` satisfies the `Pipeline` protocol), `pytest -q` **436 passed, 7 skipped** (6 live, 1 symlink), `pip-audit` no known vulnerabilities. Coverage: `pipelines/ocr.py` 91 %, `pipelines/tasks.py` 89 %, `pipeline_a.py`/`prompts.py`/`core/extraction.py` 100 %, `llm/client.py` 95 %, `llm/providers.py` 91 %. Importing `pipelines.tasks` or the CLI loads none of rapidocr/onnxruntime/cv2/litellm/PIL/numpy.
- Security: images are untrusted (size/format/dimension checks before decode, decompression-bomb warnings promoted to errors, in memory only, never logged); OCR text is untrusted prompt content (delimited, neutralised, capped, "data only" instruction); OCR models load from the wheel by path, never downloaded; the eval task confines image paths to `data/` and verifies the manifest SHA-256; logs carry metadata only (case id, ms, pixel size); `raw_text` is kept only for invalid replies and capped. No user input reaches Gemini (pipeline A uses Groq; `origin` is enforced by the layer's data policy, tested). `pip-audit` clean after adding `rapidocr` 3.10.0, `onnxruntime` 1.31.0 (+ `opencv-python-headless` 5.0.0.93, `shapely`, `pyclipper`, `omegaconf`, `protobuf`).
- Performance: OCR as above; the engine is built once per process and serialised (ONNX Runtime already multi-threads a call), so case concurrency only overlaps OCR with LLM calls. Groq free tier: ≈ 2–4K tokens per call incl. reasoning; this session used 67 requests / 122,653 of the 200K TPD.

**Files:** added `src/receipt_extractor/core/extraction.py`, `src/receipt_extractor/pipelines/{__init__,base,ocr,prompts,pipeline_a,tasks}.py`, `configs/runs/{a-naive-val10,a-val10}.yaml`, `tests/unit/pipelines/{__init__,conftest,test_ocr,test_pipeline_a,test_tasks}.py`, `tests/live/test_pipeline_a_live.py`; modified `src/receipt_extractor/llm/{client,providers,types}.py`, `src/receipt_extractor/eval/{tasks,runner}.py`, `tests/unit/{test_llm_client,test_llm_providers}.py`, `pyproject.toml` + `uv.lock` (`rapidocr`, `onnxruntime`, and `pillow`/`numpy` declared because they are now imported directly), `TRD.md` §4.2/§S1/§S2/§S3, `BACKEND_SCHEMA.md` §1.2/§2/§S2, `APPFLOW.md` §2, `README.md`, `WORKLOG.md`. Run artefacts (gitignored): `runs/A-naive-{0b8b3d119990,0bc459683abc,1a8804eab2a8}/`, `runs/A-{4ec4fcf34606,f4cfab44855e,a5618f0e9121}/`.

**Decisions:**
- New package `pipelines/` (TRD §S2 updated): pipelines do I/O (OCR, model calls), so they don't belong in the pure `core/`; `ExtractionResult` is a domain contract and lives in `core/`.
- Same prompt and model for `A-naive` and `A`; the only difference is the validation machinery, so the comparison isolates it. Naive needed a `repair` switch in the LLM layer (it always repaired).
- RapidOCR 3 (not the deprecated `rapidocr_onnxruntime`): its wheel ships the det/rec ONNX models, so pinning them by path keeps the "no download outside explicit commands" rule. OCR settings (no angle classifier, det max side 960, 4 threads) are run params, chosen on validation timing.
- Free-tier pacing waits are reported as `wait_ms`/`llm_wait`, not latency: otherwise the LLM stage showed 9–48 s for 1–2 s of model time. `ResultRow.latency_ms` (case wall clock) still includes them.
- A provider-side schema rejection counts as an invalid reply, not a request error, so `A-naive` reports it as invalid JSON (not an error row) and `A` repairs it.
- Amounts are asked for "as printed" so `parse_amount` interprets every number; v2 adds the narrow OCR-confusion rule (B→8, O→0, …) only inside amounts.
- Prompt tuning stopped at v3 (validation only): the remaining misses are model variance or a receipt-semantics rule better judged on more failures (M3.1).

**Open:**
- **M2.3 budget plan:** 100 test receipts × (`A-naive` + `A`) ≈ 200+ calls × ≈ 2–4K tokens ≈ 400–600K tokens against Groq's 200K TPD, so the A runs need **≈ 2–3 days**; the budget tracker pauses and `eval run` resumes. Plan Gemini (B, RPD 20/day) separately.
- **M2.3 latency reporting:** `summary.latency_ms` p50/p95 is case wall clock and includes free-tier pacing (case p50 ≈ 29 s here). For the README, report per-stage sums (`ocr + llm + validate`) or define latency explicitly before M2.3.
- OCR budget missed on 13/100 validation receipts (max 2.48 s); M3.1 can try det max side 736/800 against accuracy if OCR time matters for the Space p95 < 8 s.
- M3.1 candidates: "Total" followed by tax and "Grand Total" = subtotal (validation-0065); short item names vs tax labels (validation-0038); run-to-run variance at temperature 0 (validation-0038 flipped between v2 and v3).
- `reconciled`/`needs_review` stay `null` until M2.2.
- No human action required for M1.3. Nothing committed.

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
