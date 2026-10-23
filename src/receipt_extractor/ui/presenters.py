"""Try it tab: sample or live outcome → the values its components show (no Gradio here).

A column is rendered as ``(title, badge, items, totals, metadata, raw, ocr)``; the app only maps
these tuples onto components, so callbacks carry no logic (UI_UX_DESIGN, TRD §S7).
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Final

from receipt_extractor.ui.live import LiveOutcome
from receipt_extractor.ui.showcase import SHOWCASE_DIR, Showcase
from receipt_extractor.ui.views import ColumnView, column_view, format_money

__all__ = [
    "B_UPLOAD_NOTE",
    "ColumnOutput",
    "TryItOutput",
    "present_live",
    "present_sample",
    "sample_choices",
]

B_UPLOAD_NOTE: Final = "Vision model runs on sample receipts only (privacy)."
A_TITLE: Final = "### A · OCR + LLM (RapidOCR → Groq gpt-oss-20b)"
B_TITLE: Final = "### B · Vision LLM (Gemini Flash-Lite)"

ColumnOutput = tuple[str, str, list[list[str]], list[list[str]], str, str, str]


@dataclass(frozen=True, slots=True)
class TryItOutput:
    banner: str
    image: str | None  # path of a sample's display image; None for uploads (already shown)
    a: ColumnOutput
    b: ColumnOutput


def _render(view: ColumnView) -> ColumnOutput:
    return (
        view.title,
        f"**{view.badge.text}**",
        view.items,
        view.totals,
        view.metadata,
        view.raw_json,
        view.ocr_text,
    )


def _placeholder(title: str, note: str) -> ColumnOutput:
    return (title, f"**{note}**", [], [], "", "", "")


def sample_choices(showcase: Showcase | None) -> list[str]:
    return [case.case_id for case in showcase.cases] if showcase else []


def present_sample(
    showcase: Showcase | None, case_id: str | None, *, assets: Path = SHOWCASE_DIR
) -> TryItOutput:
    """A sample receipt with its cached A and B results (never a live call)."""
    case = showcase.get(case_id) if showcase and case_id else None
    if case is None:
        empty = "Pick a sample receipt or upload your own."
        return TryItOutput("", None, _placeholder(A_TITLE, empty), _placeholder(B_TITLE, empty))
    columns: list[ColumnOutput] = []
    for variant, title in (("A", A_TITLE), ("B", B_TITLE)):
        stored = case.variants.get(variant)
        if stored is None:
            columns.append(_placeholder(title, "No stored result for this sample."))
            continue
        view = column_view(
            title, stored.prediction.model_dump(mode="json"), ocr_text=stored.ocr_text
        )
        columns.append(_render(view))
    runs = ", ".join(f"{v} `{r.run_id}`" for v, r in case.variants.items())
    banner = (
        f"Sample **{case.case_id}** from CORD-v2 (validation) — cached results from runs {runs}. "
        f"Printed total (gold): {format_money(case.gold.total, case.gold.currency)}."
    )
    return TryItOutput(banner, str(assets / case.image), columns[0], columns[1])


def present_live(outcome: LiveOutcome) -> TryItOutput:
    """An upload: pipeline A's answer, or the reason there is none. B never runs on uploads."""
    b = _placeholder(B_TITLE, B_UPLOAD_NOTE)
    if outcome.kind != "ok" or outcome.result is None:
        prefix = "⏸" if outcome.kind == "paused" else "⚠"
        return TryItOutput(
            f"{prefix} {outcome.message}", None, _placeholder(A_TITLE, outcome.message), b
        )
    view = column_view(
        A_TITLE, outcome.result.model_dump(mode="json"), ocr_text=outcome.result.ocr_text
    )
    banner = f"Your receipt, read live by pipeline A (request id `{outcome.request_id}`)."
    return TryItOutput(banner, None, _render(view), b)
