---
title: Receipt to JSON
emoji: 🧾
colorFrom: indigo
colorTo: gray
sdk: gradio
sdk_version: 6.30.0
python_version: "3.12"
app_file: app.py
pinned: false
short_description: OCR + LLM vs a vision LLM for receipt extraction (CORD-v2)
---

# Receipt → JSON

A receipt photo goes in; validated JSON (line items, prices, totals) comes out. Two pipelines are
benchmarked on CORD-v2: **A** — RapidOCR on the CPU → Groq `gpt-oss-20b`, with schema re-asks and
an arithmetic check; **B** — the image → Gemini Flash-Lite (vision).

- **Results** — the benchmark table and charts, generated from the project's eval runs.
- **Try it** — upload a receipt (read live by pipeline A only; your image is processed in memory
  and never sent to Gemini), or open a sample receipt to compare A and B side by side.
- **Failures** — where each pipeline goes wrong, grouped by cause.

Source, method and reproduction: see the project repository.
