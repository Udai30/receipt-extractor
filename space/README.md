---
title: Receipt to JSON
emoji: 🧾
colorFrom: indigo
colorTo: gray
sdk: static
app_file: index.html
pinned: false
short_description: OCR + LLM vs a vision LLM for receipt extraction (CORD-v2)
---

# Receipt → JSON

A receipt photo goes in; validated JSON (line items, prices, totals) comes out. Two pipelines are
benchmarked on CORD-v2: **A** — RapidOCR on the CPU → Groq `gpt-oss-20b`, with schema re-asks and
an arithmetic check; **B** — the image → Gemini Flash-Lite (vision).

This is a static page (free Static Space): it runs no model.

- **Results** — the benchmark table and charts, generated from the project's eval runs.
- **Samples** — CORD-v2 validation receipts with the cached A and B answers side by side.
- **Failures** — where each pipeline goes wrong, grouped by cause.

To try your own receipt, run the Gradio demo locally (`uv run python app.py`, pipeline A only).
Source, method and reproduction: see the project repository.
