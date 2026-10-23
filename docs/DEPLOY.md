# Deploying the demo to a Hugging Face Space

Publishing is a **human action** (CLAUDE.md §4). Everything below is run by you; nothing here
is automated. Free hosting only: a CPU Basic Space.

## What the Space runs

- **Results** and **Failures** read the committed `reports/` files (no runs or data on the Space).
- **Try it**: samples are the bundled `src/receipt_extractor/ui/assets/showcase/` (cached A/B
  results, CORD-v2 validation images, CC-BY-4.0). Uploads run **pipeline A live** (RapidOCR on the
  Space's CPU + Groq). **Pipeline B never runs on the Space** and uploads never reach Gemini, so
  no Gemini key is set there.
- Limits: 5 MB / 4,096 px per side per upload, 2 concurrent jobs, 20 queued, and with
  `DEMO_MODE=true` at most `DEMO_DAILY_REQUESTS` live extractions per UTC day. After that the tab
  shows "Live calls paused for today" and samples keep working.

## Why the package is not pip-installed

Every Gradio release caps Pillow below 13, while this project pins Pillow ≥ 13.0 for security
(CVE-2026-96636/37/43/44/78, GHSA-78g4-xqwv-q3g8; decision in WORKLOG M3.2). The Space installs
Gradio itself from `sdk_version`; `requirements.txt` therefore lists the runtime dependencies
**without** Gradio's subtree, and `app.py` puts `src/` on the import path. Pip may print
`gradio … requires pillow<13` during the build: that warning is expected.

**Risk to check on the first build:** if the Space builder installs Gradio and `requirements.txt`
in one resolver run, the build fails on that conflict. Fallback, in order: (1) drop the
`pillow==13.0.0` line from `requirements.txt` for the Space only and record that the Space runs
Pillow 12 (known CVEs, mitigated by the 5 MB / 4,096 px header checks before decoding); or (2) ask
for a Docker Space. Tell the agent which happened so WORKLOG and TRD §S7 stay true.

## 1. Refresh what the Space shows (repository root)

```powershell
uv run receipt_extractor report build
uv run receipt_extractor ui build-showcase
uv export --frozen --no-dev --no-hashes --no-emit-project --prune gradio --format requirements-txt -o requirements.txt
uv run pytest -q tests/unit/ui
```

## 2. Log in and create the Space (once)

Create a **write** token at https://huggingface.co/settings/tokens, then:

```powershell
uv run hf auth login
```

```powershell
uv run hf repos create <your-user>/receipt-extractor --type space --sdk gradio
```

## 3. Set variables and secrets (once)

Space → Settings → Variables and secrets:

| Name | Kind | Value |
|---|---|---|
| `GROQ_API_KEY` | **Secret** | a Groq free-plan key (consider a separate key for the demo) |
| `DEMO_MODE` | Variable | `true` |
| `DEMO_DAILY_REQUESTS` | Variable | `100` (lower it if Groq's free budget is needed for runs) |
| `LOG_LEVEL` | Variable | `INFO` |

Do **not** add `GEMINI_API_KEY`: B never runs on the Space.

## 4. Upload (an explicit list: `.env`, `data/`, `runs/`, `.cache/` are never sent)

```powershell
uv run hf upload <your-user>/receipt-extractor space/README.md README.md --type space
```

```powershell
uv run hf upload <your-user>/receipt-extractor app.py app.py --type space
```

```powershell
uv run hf upload <your-user>/receipt-extractor requirements.txt requirements.txt --type space
```

```powershell
uv run hf upload <your-user>/receipt-extractor src src --type space --exclude "**/__pycache__/**"
```

```powershell
uv run hf upload <your-user>/receipt-extractor configs/models.yaml configs/models.yaml --type space
```

```powershell
uv run hf upload <your-user>/receipt-extractor configs/pricing.yaml configs/pricing.yaml --type space
```

```powershell
uv run hf upload <your-user>/receipt-extractor reports reports --type space
```

## 5. Check

1. Space → Logs: the build finishes and the app starts (RapidOCR's models ship in its wheel; no
   download at start-up).
2. Results tab shows the table; Try it → a sample receipt shows A and B; an upload returns
   pipeline A's JSON with a badge; Failures tab lists failures (after M3.1).
3. Add the Space URL to WORKLOG.md (M3.2 entry → Open), and tell the agent.

To update later, repeat steps 1 and 4. To take the demo down: Space → Settings → Delete.
