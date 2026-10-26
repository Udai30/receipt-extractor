# LinkedIn draft (about the finding, not the build)

**Status: written from measured numbers (2026-10-27, `reports/results.md` and `reports/failures.csv`).**
Placeholders left for you: the Space URL and the GitHub URL (first comment) and the GIF. The post
should not go out before the Space is live. The link goes in the **first comment**, not the body.

## Hook (first two lines, before the "see more" fold)

I gave 100 receipts to an OCR + LLM pipeline and to a vision model. The vision model got 84 of them
fully right. The OCR pipeline got 58.

## Body (about 1,200 characters)

Same receipts, same output schema, same scorer. Three setups: naive OCR + LLM, OCR + LLM with schema
retries and arithmetic checks, and Gemini Flash-Lite reading the image.

Field-level F1: 0.957 for vision, 0.846 for OCR + LLM with retries, 0.820 for the naive version. The
vision model's confidence interval does not overlap with either.

The retries earned less than I expected. They cut invalid JSON from 6% to 2%, but the accuracy gain
is inside the noise at 100 receipts.

The failures were the useful part. I labelled all 103 by hand. The OCR pipelines break on reading (a
54,000 total read as 5,000) and on items split across lines. Every pipeline, vision included,
sometimes reads a discount line or a "** FOOD" subtotal as an item.

Reading failures also found a bug of mine. Five of the pipeline's failures had been scored without
their repair step: the retry call hit a rate limit and the client returned the unrepaired reply.
Fixing it moved F1 from 0.827 to 0.846.

Cost: about $0.98 per 1k receipts for vision against $0.39 for OCR + LLM, at list price.

Caveat: one dataset, 100 Indonesian receipts, and the two pipelines differ in model as well as input.

## Close

When would you send a receipt to a person instead of trusting either one?

## First comment

Live demo (upload a receipt, see the pipelines side by side): [Space URL, after you deploy]
Code and the full results: [GitHub URL]

## Resume bullet

Built a receipt-to-JSON pipeline comparing OCR+LLM and Gemini vision on CORD-v2; the vision model
reached 0.957 field-level F1 against 0.846 for OCR+LLM, with schema retries cutting invalid outputs
from 6% to 2%, at $0.98 per 1k receipts (list price) against $0.39.

## Where each number comes from

| Claim | Source |
|---|---|
| 84 / 58 / 55 receipts fully right | 100 minus the totals in the failures table (16 / 42 / 45) |
| 0.957, 0.846, 0.820 and the intervals | `reports/results.md`, runs `B-cea8aeff49d5`, `A-e8c045ee22e6`, `A-naive-5ed10911e9ae` |
| Invalid JSON 6% to 2% | `valid_json` 0.94 (A-naive) and 0.98 (A) |
| 103 failures labelled | `reports/failures.csv` |
| Five cases scored without repair; 0.827 to 0.846 | WORKLOG 2026-10-27, README "One fix" |
| $0.98 and $0.39 per 1k | `list_cost_usd_per_1k`, 0.9751 and 0.3930 |

## Checklist before posting

- [ ] The Space is deployed and the link works.
- [ ] The GIF (`docs/demo.gif`) is recorded and attached.
- [ ] No screenshot shows keys, paths or personal receipts.
- [ ] The numbers still match `reports/results.md` if any run was repeated after 2026-10-27.
