# LinkedIn draft — outline (about the finding, not the build)

**Status: outline only.** Every `[bracket]` is filled from `reports/results.md` and
`reports/failures.csv` after M2.3 and M3.1 finish. Do not publish with brackets left in.
The Space link goes in the **first comment**, not in the post body.

## Hook (first two lines, before the "see more" fold)

[One surprising, measured result, for example which pipeline won on which field and by how much.
Candidates, depending on what the numbers show:]
- "I compared an OCR + LLM pipeline with a vision LLM on [100] real receipts. [Result]."
- "The vision model [won / lost] on [field], and the reason was not what I expected."

## Body (aim for 900–1,300 characters)

1. **The setup in one sentence.** Same 100 CORD receipts, same output schema, same scorer.
   Three variants: naive OCR + LLM, engineered OCR + LLM (retries, arithmetic check), vision LLM.
2. **The finding.** [Field F1 for each variant with the 95 % range. Name the biggest gap.]
3. **Where it breaks.** [Top cause from the 20–30 labelled failures, with its share, e.g. "[N] of
   [M] failures were [cause]". Mention one concrete example.]
4. **What one fix bought.** [Before/after on the same test set from M3.1, in the cause's own metric.]
5. **The cost and speed trade.** [p50 latency and list cost per 1k receipts for each variant,
   noting that the runs themselves were free.]
6. **The honest caveat.** [One limitation: CORD is Indonesian receipts, n = 100, free-tier models.]

## Close

[One question that invites an answer, e.g. when would you route a receipt to a human?]

## First comment

Live demo (upload a receipt, see the pipelines side by side): [Space URL]
Code and the full results: [GitHub URL]

## Resume bullet (from PRD §7; fill only with measured numbers)

Built a receipt-to-JSON pipeline comparing OCR+LLM and Gemini vision on CORD-v2; reached [X]
field-level F1, with schema retries and arithmetic checks cutting invalid outputs from [A]% to [B]%
at [cost] per 1k receipts.

## Checklist before posting

- [ ] Every number traces to a run id in `reports/`.
- [ ] No screenshot shows keys, paths or personal receipts.
- [ ] The GIF (`docs/demo.gif`) is attached or linked.
- [ ] Space is live and the link works.
