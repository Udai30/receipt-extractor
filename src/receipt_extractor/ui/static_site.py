"""Static demo page for a free Hugging Face Static Space (WORKLOG 2026-10-28, TRD §S7).

Gradio Spaces on free CPU now need a PRO subscription; Static Spaces stay free. This module
renders the same Results, sample and Failures views as the Gradio app into one ``index.html``
plus copied images, reusing the app's view-models (``reports``, ``views``, ``showcase``), so the
page can never show a number the app would not (CLAUDE.md §5: nothing is typed by hand).

A static page cannot run a pipeline: there is no live upload. Samples show the cached A/B
results; the live demo runs locally (``uv run python app.py``).

Output layout (``out_dir``, uploaded as the Space root)::

    index.html      the page (inline CSS and a small filter script; no external requests)
    README.md       the Space card (copied from ``space/README.md``)
    assets/         sample receipt images and the report charts
"""

from __future__ import annotations

import json
import shutil
from collections import Counter
from collections.abc import Iterable, Sequence
from html import escape
from pathlib import Path
from typing import Final

from receipt_extractor.core.receipt import Receipt
from receipt_extractor.ui.presenters import A_TITLE, B_TITLE
from receipt_extractor.ui.reports import FailureView, ResultsView, load_failures, load_results
from receipt_extractor.ui.showcase import SHOWCASE_DIR, Showcase, ShowcaseCase, load_showcase
from receipt_extractor.ui.views import ITEM_COLUMNS, ColumnView, column_view, format_money

__all__ = ["ASSETS", "INDEX", "build_static_site", "render_page"]

INDEX: Final = "index.html"
ASSETS: Final = "assets"
SPACE_README: Final = "README.md"
_TITLES: Final = {"A": A_TITLE.removeprefix("### "), "B": B_TITLE.removeprefix("### ")}
_LOCAL_DEMO: Final = "uv run python app.py"

_CSS: Final = """
:root { --bg:#f8fafc; --panel:#ffffff; --text:#0f172a; --muted:#475569; --line:#e2e8f0;
  --accent:#4f46e5; --ok:#15803d; --warn:#b45309; --error:#b91c1c; --code:#f1f5f9; }
@media (prefers-color-scheme: dark) {
  :root { --bg:#0b1120; --panel:#111827; --text:#e2e8f0; --muted:#94a3b8; --line:#1f2937;
    --accent:#818cf8; --ok:#4ade80; --warn:#fbbf24; --error:#f87171; --code:#1e293b; } }
* { box-sizing: border-box; }
body { margin:0; background:var(--bg); color:var(--text);
  font:16px/1.5 Inter, ui-sans-serif, system-ui, sans-serif; }
main { max-width:1100px; margin:0 auto; padding:24px 16px 64px; }
h1 { font-size:1.75rem; margin:0 0 4px; } h2 { margin:40px 0 8px; } h3 { margin:0 0 8px; }
p.lead { color:var(--muted); margin:0 0 16px; }
nav { position:sticky; top:0; background:var(--bg); padding:8px 0; border-bottom:1px solid
  var(--line); display:flex; gap:16px; z-index:1; }
nav a, a { color:var(--accent); }
.headline { font-size:1.15rem; font-weight:600; padding:12px 16px; background:var(--panel);
  border-left:4px solid var(--accent); border-radius:6px; }
.note { color:var(--muted); font-size:.9rem; }
.scroll { overflow-x:auto; }
table { border-collapse:collapse; width:100%; background:var(--panel); font-size:.92rem; }
th, td { padding:6px 10px; border-bottom:1px solid var(--line); text-align:left;
  vertical-align:top; }
td.num, th.num { text-align:right; font-variant-numeric:tabular-nums; white-space:nowrap; }
.charts { display:grid; grid-template-columns:repeat(auto-fit, minmax(280px, 1fr)); gap:16px;
  margin-top:16px; }
.charts img, .sample img { width:100%; height:auto; border:1px solid var(--line);
  border-radius:6px; background:#fff; }
details.sample { background:var(--panel); border:1px solid var(--line); border-radius:8px;
  margin:12px 0; padding:8px 16px; }
details.sample > summary { cursor:pointer; font-weight:600; padding:4px 0; }
.sample-body { display:grid; grid-template-columns:minmax(200px, 280px) 1fr 1fr; gap:16px;
  margin-top:8px; }
@media (max-width: 860px) { .sample-body { grid-template-columns:1fr; } }
.badge { font-weight:600; margin:4px 0 8px; }
.badge.ok { color:var(--ok); } .badge.warn { color:var(--warn); }
.badge.error { color:var(--error); } .badge.neutral { color:var(--muted); }
.meta, pre, code { font-family:"JetBrains Mono", ui-monospace, Consolas, monospace; }
.meta { font-size:.8rem; color:var(--muted); margin-top:8px; }
pre { background:var(--code); padding:8px; border-radius:6px; overflow:auto; max-height:320px;
  font-size:.8rem; white-space:pre-wrap; }
code { background:var(--code); padding:1px 4px; border-radius:4px; }
details.more summary { cursor:pointer; color:var(--muted); font-size:.85rem; margin-top:8px; }
.filters { display:flex; flex-wrap:wrap; gap:12px; margin:8px 0; }
select { font:inherit; padding:4px 8px; background:var(--panel); color:var(--text);
  border:1px solid var(--line); border-radius:6px; }
footer { margin-top:48px; color:var(--muted); font-size:.85rem; }
"""

# Progressive enhancement: without JavaScript the whole failure table shows.
_FILTER_JS: Final = """
(() => {
  const rows = [...document.querySelectorAll('#failures tbody tr')];
  const cause = document.getElementById('cause'), pipe = document.getElementById('pipeline');
  const count = document.getElementById('shown');
  const apply = () => {
    let n = 0;
    for (const r of rows) {
      const ok = (cause.value === 'All' || r.dataset.cause === cause.value) &&
                 (pipe.value === 'All' || r.dataset.pipeline === pipe.value);
      r.hidden = !ok; n += ok;
    }
    count.textContent = n;
  };
  cause.addEventListener('change', apply); pipe.addEventListener('change', apply);
  document.querySelector('.filters').hidden = false;
})();
"""


def _cells(values: Iterable[str], *, header: bool = False, numeric_from: int = 1) -> str:
    tag = "th" if header else "td"
    return "".join(
        f"<{tag}{' class=num' if i >= numeric_from else ''}>{escape(v)}</{tag}>"
        for i, v in enumerate(values)
    )


def _table(columns: Sequence[str], rows: Sequence[Sequence[str]], *, numeric_from: int) -> str:
    body = "".join(f"<tr>{_cells(r, numeric_from=numeric_from)}</tr>" for r in rows)
    return (
        f'<div class="scroll"><table><thead><tr>'
        f"{_cells(columns, header=True, numeric_from=numeric_from)}</tr></thead>"
        f"<tbody>{body}</tbody></table></div>"
    )


def _results_section(view: ResultsView, chart_names: Sequence[tuple[str, str]]) -> str:
    charts = "".join(
        f'<img src="{ASSETS}/{escape(name)}" alt="{escape(alt)}" loading="lazy">'
        for name, alt in chart_names
    )
    return (
        '<section id="results"><h2>Results</h2>'
        "<p>CORD-v2 test split, the same 100 receipts for every pipeline. Field F1 with a "
        "95 % bootstrap interval.</p>"
        f"{_table(view.columns, view.rows, numeric_from=1)}"
        f'<p class="note">{escape(view.note)}</p>'
        f"{f'<div class=charts>{charts}</div>' if charts else ''}</section>"
    )


def _column(view: ColumnView) -> str:
    items = (
        _table(ITEM_COLUMNS, view.items, numeric_from=1)
        if view.items
        else '<p class="note">No line items.</p>'
    )
    totals = _table(("", "Amount"), view.totals, numeric_from=1) if view.totals else ""
    ocr = (
        f'<details class="more"><summary>OCR text</summary><pre>{escape(view.ocr_text)}</pre>'
        "</details>"
        if view.ocr_text
        else ""
    )
    raw = (
        f'<details class="more"><summary>Raw JSON</summary><pre>{escape(view.raw_json)}</pre>'
        "</details>"
        if view.raw_json
        else ""
    )
    return (
        f"<div><h3>{escape(view.title)}</h3>"
        f'<p class="badge {view.badge.level}">{escape(view.badge.text)}</p>'
        f'{items}{totals}<p class="meta">{escape(view.metadata)}</p>{ocr}{raw}</div>'
    )


def _gold(receipt: Receipt) -> str:
    rows = [
        [
            item.name,
            "" if item.qty is None else format(item.qty.normalize(), "f"),
            format_money(item.price, receipt.currency),
        ]
        for item in receipt.items
    ]
    raw = json.dumps(receipt.model_dump(mode="json"), indent=2, ensure_ascii=False)
    return (
        '<details class="more"><summary>Gold label (what is printed)</summary>'
        f"{_table(ITEM_COLUMNS, rows, numeric_from=1)}<pre>{escape(raw)}</pre></details>"
    )


def _sample(case: ShowcaseCase, *, open_: bool) -> str:
    columns = []
    for variant, title in _TITLES.items():
        stored = case.variants.get(variant)
        if stored is None:
            columns.append(
                f'<div><h3>{escape(title)}</h3><p class="note">No stored result.</p></div>'
            )
            continue
        view = column_view(
            title, stored.prediction.model_dump(mode="json"), ocr_text=stored.ocr_text
        )
        columns.append(_column(view))
    runs = ", ".join(f"{v} {r.run_id}" for v, r in case.variants.items())
    total = format_money(case.gold.total, case.gold.currency)
    return (
        f'<details class="sample"{" open" if open_ else ""}><summary>{escape(case.case_id)} · '
        f"printed total {escape(total)}</summary>"
        f'<div class="sample-body"><div><img src="{ASSETS}/{escape(case.image)}" '
        f'alt="Receipt {escape(case.case_id)} from CORD-v2" loading="lazy">'
        f"{_gold(case.gold)}</div>{''.join(columns)}</div>"
        f'<p class="meta">Cached results from runs {escape(runs)}.</p></details>'
    )


def _samples_section(showcase: Showcase | None) -> str:
    if showcase is None or not showcase.cases:
        body = '<p class="note">No sample receipts bundled.</p>'
    else:
        body = "".join(_sample(c, open_=i == 0) for i, c in enumerate(showcase.cases))
    return (
        '<section id="samples"><h2>Sample receipts</h2>'
        "<p>CORD-v2 validation receipts (never test), each with the stored answer of both "
        "pipelines. These are cached results: a static page runs no model. To try your own "
        f"receipt, run the Gradio demo locally: <code>{escape(_LOCAL_DEMO)}</code> (pipeline A "
        "only; uploads are never sent to Gemini).</p>"
        f"{body}</section>"
    )


def _failures_section(view: FailureView) -> str:
    if not view.rows:
        note = f'<p class="note">{escape(view.note)}</p>'
        return f'<section id="failures"><h2>Failures</h2>{note}</section>'
    pipelines = [p for p in view.pipelines if p != "All"]
    causes = [c for c in view.causes if c != "All"]
    counts = Counter((row[2], row[0]) for row in view.rows)
    by_cause = sorted(causes, key=lambda c: (-sum(counts[(c, p)] for p in pipelines), c))
    summary = [[c, *(str(counts[(c, p)]) for p in pipelines)] for c in by_cause]
    summary.append(["total", *(str(sum(counts[(c, p)] for c in causes)) for p in pipelines)])

    def options(select_id: str, label: str, values: Sequence[str]) -> str:
        opts = "".join(f"<option>{escape(v)}</option>" for v in ("All", *values))
        return f'<label>{label} <select id="{select_id}">{opts}</select></label>'

    rows = "".join(
        f'<tr data-pipeline="{escape(r[0])}" data-cause="{escape(r[2])}">'
        f"{_cells(r, numeric_from=3)}</tr>"
        for r in view.rows
    )
    return (
        '<section id="failures"><h2>Failures</h2>'
        "<p>Every test receipt a pipeline did not get fully right, hand-labelled by cause.</p>"
        f"{_table(('Cause', *pipelines), summary, numeric_from=1)}"
        f'<div class="filters" hidden>{options("cause", "Cause", causes)}'
        f"{options('pipeline', 'Pipeline', pipelines)}"
        f'<span class="note">showing <span id="shown">{len(view.rows)}</span></span></div>'
        f'<div class="scroll"><table><thead><tr>{_cells(view.columns, header=True, numeric_from=3)}'
        f"</tr></thead><tbody>{rows}</tbody></table></div></section>"
    )


def render_page(
    results: ResultsView,
    showcase: Showcase | None,
    failures: FailureView,
    chart_names: Sequence[tuple[str, str]] = (),
) -> str:
    """The whole page as one HTML string (pure: no file access)."""
    return (
        '<!doctype html><html lang="en"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width, initial-scale=1">'
        "<title>Receipt to JSON</title>"
        '<meta name="description" content="OCR + LLM vs a vision LLM for receipt extraction, '
        'benchmarked on CORD-v2.">'
        f"<style>{_CSS}</style></head><body><main>"
        "<h1>Receipt → JSON</h1>"
        '<p class="lead">A receipt photo goes in; validated JSON (line items, prices, totals) '
        "comes out. Pipeline A: RapidOCR → Groq gpt-oss-20b with schema re-asks and an "
        "arithmetic check. Pipeline B: the image → Gemini Flash-Lite. A-naive is A without "
        "re-asks or checks (the baseline).</p>"
        f'<p class="headline">{escape(results.headline)}</p>'
        '<nav><a href="#results">Results</a><a href="#samples">Samples</a>'
        '<a href="#failures">Failures</a></nav>'
        f"{_results_section(results, chart_names)}{_samples_section(showcase)}"
        f"{_failures_section(failures)}"
        "<footer>Every number on this page is generated from the project's evaluation runs by "
        "<code>receipt_extractor ui build-static</code>. Receipt images and labels: CORD-v2 "
        "(naver-clova-ix/cord-v2, CC-BY-4.0).</footer>"
        f"</main><script>{_FILTER_JS}</script></body></html>\n"
    )


def build_static_site(
    reports_dir: Path,
    out_dir: Path,
    *,
    showcase_dir: Path = SHOWCASE_DIR,
    space_readme: Path | None = None,
) -> list[Path]:
    """Write ``index.html``, the Space card and the images into ``out_dir``; return the files."""
    results = load_results(reports_dir)
    showcase = load_showcase(showcase_dir)
    failures = load_failures(reports_dir)
    assets = out_dir / ASSETS
    assets.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []
    chart_names: list[tuple[str, str]] = []
    for path, alt in results.charts:
        written.append(Path(shutil.copyfile(path, assets / path.name)))
        chart_names.append((path.name, alt))
    for case in showcase.cases if showcase else ():
        written.append(Path(shutil.copyfile(showcase_dir / case.image, assets / case.image)))
    if space_readme is not None:
        written.append(Path(shutil.copyfile(space_readme, out_dir / SPACE_README)))
    index = out_dir / INDEX
    index.write_text(
        render_page(results, showcase, failures, chart_names), encoding="utf-8", newline="\n"
    )
    written.append(index)
    return written
