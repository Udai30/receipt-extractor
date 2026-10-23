"""Pure view-models for the demo: ``ExtractionResult`` → what a column of the Try it tab shows.

No Gradio here (UI_UX_DESIGN: no business logic in callbacks; this is formatting only), so every
state — reconciled, not reconciled, invalid, not checked — is unit-tested without a browser.
Badges always carry text, never colour alone (UI_UX_DESIGN §S4).
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any, Final, Literal

from receipt_extractor.core.extraction import ExtractionResult

__all__ = [
    "ITEM_COLUMNS",
    "Badge",
    "ColumnView",
    "column_view",
    "format_money",
]

ITEM_COLUMNS: Final = ("Item", "Qty", "Price")
BadgeLevel = Literal["ok", "warn", "error", "neutral"]


@dataclass(frozen=True, slots=True)
class Badge:
    level: BadgeLevel
    text: str


@dataclass(frozen=True, slots=True)
class ColumnView:
    """Everything one pipeline column shows (UI_UX_DESIGN §2.2)."""

    title: str
    items: list[list[str]]
    totals: list[list[str]]
    badge: Badge
    metadata: str
    raw_json: str
    ocr_text: str
    attempts: int


def _decimal(value: Any) -> Decimal | None:
    if value is None:
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None


def format_money(value: Any, currency: str = "IDR") -> str:
    """The normalised amount with the receipt's grouping (IDR: ``51.000``) and the plain number.

    ``Receipt`` stores normalised ``Decimal`` amounts, so the "as printed" style is rebuilt from
    the currency's convention (CORD is Indonesian: ``.`` thousands, no cents), next to the
    normalised value (UI_UX_DESIGN §3).
    """
    amount = _decimal(value)
    if amount is None:
        return "—"
    plain = format(amount.normalize(), "f") if amount == amount.to_integral() else str(amount)
    if currency == "IDR" and amount == amount.to_integral():
        grouped = f"{int(amount):,}".replace(",", ".")
        return f"Rp {grouped} ({plain})"
    return f"{currency} {amount:,} ({plain})"


def _badge(result: ExtractionResult) -> Badge:
    if not result.schema_valid:
        reason = "truncated reply" if result.failure == "truncated" else "invalid JSON"
        if result.failure == "ocr_empty":
            reason = "no text found in the image"
        return Badge("error", f"✕ Couldn't produce a valid receipt ({reason})")
    if result.reconciled is True:
        return Badge("ok", "✓ Totals add up")
    if result.reconciled is False:
        diff = format_money(result.reconcile_diff, _currency(result))
        return Badge("warn", f"⚠ Totals don't add up — needs review (off by {diff})")
    return Badge("neutral", "• Arithmetic not checked for this variant")


def _currency(result: ExtractionResult) -> str:
    return result.receipt.currency if result.receipt is not None else "IDR"


def _metadata(result: ExtractionResult) -> str:
    stages = result.stage_latency_ms
    measured = [("OCR", stages.ocr), ("prepare", stages.prepare)]
    if not result.cache_hit:
        # A cache hit replays the stored reply: its model/validate times (~0) are not latency.
        measured += [("model", stages.llm), ("validate", stages.validate_)]
    timings = [f"{name} {ms / 1000:.1f} s" for name, ms in measured if ms is not None]
    parts = [
        result.model_alias or "—",
        f"{result.tokens_in:,} in / {result.tokens_out:,} out tokens",
        *timings,
        f"${result.list_cost_usd:.5f} list",
        f"{result.attempts} call{'s' if result.attempts != 1 else ''}",
    ]
    if result.cache_hit:
        parts.append("model reply replayed from cache (no model time)")
    return " · ".join(parts)


def column_view(
    title: str, prediction: dict[str, Any], *, ocr_text: str | None = None
) -> ColumnView:
    """Build the view from a stored prediction (``ExtractionResult`` JSON); validates it first."""
    result = ExtractionResult.model_validate(prediction)
    currency = _currency(result)
    items: list[list[str]] = []
    totals: list[list[str]] = []
    if result.receipt is not None:
        receipt = result.receipt
        items = [
            [
                item.name,
                "" if item.qty is None else format(item.qty.normalize(), "f"),
                format_money(item.price, currency),
            ]
            for item in receipt.items
        ]
        totals = [
            ["Subtotal", format_money(receipt.subtotal, currency)],
            ["Tax", format_money(receipt.tax, currency)],
            ["Total", format_money(receipt.total, currency)],
        ]
        raw = json.dumps(receipt.model_dump(mode="json"), indent=2, ensure_ascii=False)
    else:
        raw = result.raw_text or ""
    return ColumnView(
        title=title,
        items=items,
        totals=totals,
        badge=_badge(result),
        metadata=_metadata(result),
        raw_json=raw,
        ocr_text=ocr_text or result.ocr_text or "",
        attempts=result.attempts,
    )
