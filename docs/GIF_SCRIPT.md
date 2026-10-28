# GIF script — Receipt-to-JSON Extractor (20–30 s)

The human records this (the agent cannot run a screen recorder). Output: `docs/demo.gif`, ≤ 3 MB,
about 960 px wide. Record only **after** the benchmark and the Space are final, so the Results tab
shows the real numbers.

## Setup

- Run the local Gradio app (`uv run python app.py`), at desktop width (~1,200 px). The shots below
  follow its tabs; the deployed Space is a static page (Results · Samples · Failures, no upload).
- Use a **public CORD sample receipt** from the Try it tab. Do not record a personal receipt.
- Close other tabs and notifications. Light theme. Zoom the browser to 110 %.
- Tools: ScreenToGif (Windows) or any recorder; export 12–15 fps, loop forever.

## Shots

| Time | What is on screen | Action |
|---|---|---|
| 0–4 s | **Results** tab: the table with A-naive, A and B, and the headline sentence | Hold still, then slowly scroll to the F1 chart |
| 4–8 s | Switch to **Try it** | Click a sample receipt in the gallery |
| 8–16 s | Two columns appear: OCR + LLM (A) and Vision LLM (B) with line items, totals and the badge (✓ totals add up or ⚠ needs review) | Hold on the badge, then hover the metadata strip (model · tokens · time · cost) |
| 16–22 s | Click a second sample where the two pipelines disagree | Pause on the differing line items |
| 22–27 s | **Failures** tab: failure causes and one detail with gold vs predicted JSON | Click one row |
| 27–30 s | Back to Results | End on the headline sentence |

## Notes

- No audio. Add no captions longer than 6 words; the page already carries the text.
- Pick the second sample from `reports/failures.csv` so the GIF shows a real difference.
- Check the final frame contains no API keys, file paths or personal data.
- Then put `docs/demo.gif` under the Results table in the README (the placeholder is already there).
