"""Arithmetic reconciliation of a :class:`Receipt` (PRD §5 "reconcile %", TRD §4.3).

A receipt reconciles when its line items add up to the subtotal and the subtotal plus tax adds
up to the total, each within :func:`tolerance`. Two printed layouts are accepted for the second
check: tax added on top (``subtotal + tax = total``) and tax already included in the prices
(``subtotal = total`` with the tax line shown for information, common on Indonesian receipts).
Without a subtotal the item sum stands in for it. Service charges and discounts are not part of
the schema, so receipts that print them do not reconcile — on purpose: the check is about the
fields a pipeline returns, not about the paper.

The scorer (``eval/scorer.py``) uses this for the reconcile metric; pipelines use it for
``needs_review`` (M2.2), so both apply the same rule.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Final

from receipt_extractor.core.receipt import Receipt

__all__ = ["ABS_TOLERANCE", "REL_TOLERANCE", "Reconciliation", "reconcile", "tolerance"]

REL_TOLERANCE: Final = Decimal("0.005")
"""Share of the total allowed as rounding difference (tax rounded per line, …)."""
ABS_TOLERANCE: Final = Decimal("0.01")
"""Smallest tolerance, so zero or tiny totals still compare by the cent."""


@dataclass(frozen=True)
class Reconciliation:
    """Outcome of :func:`reconcile`; the diffs are signed (``computed - printed``)."""

    ok: bool
    items_diff: Decimal | None
    """``Σ item prices - subtotal``; ``None`` when no subtotal is printed."""
    total_diff: Decimal
    """``(subtotal or Σ items) + tax - total`` (tax counts as 0 when absent)."""
    tax_included: bool
    """True when only the tax-inclusive reading (``subtotal = total``) reconciles."""
    allowed: Decimal
    """The tolerance both checks used."""


def tolerance(
    total: Decimal,
    *,
    rel_tolerance: Decimal = REL_TOLERANCE,
    abs_tolerance: Decimal = ABS_TOLERANCE,
) -> Decimal:
    if rel_tolerance < 0 or abs_tolerance < 0:
        raise ValueError("tolerances must be non-negative")
    return max(abs_tolerance, abs(total) * rel_tolerance)


def reconcile(
    receipt: Receipt,
    *,
    rel_tolerance: Decimal = REL_TOLERANCE,
    abs_tolerance: Decimal = ABS_TOLERANCE,
) -> Reconciliation:
    """Check ``receipt``'s arithmetic. The defaults are the scorer's; pipelines may pass their own
    tolerance (a run param), which never changes how the ``reconcile`` metric is scored."""
    allowed = tolerance(receipt.total, rel_tolerance=rel_tolerance, abs_tolerance=abs_tolerance)
    items_sum = sum((item.price for item in receipt.items), Decimal(0))
    items_diff = None if receipt.subtotal is None else items_sum - receipt.subtotal
    base = items_sum if receipt.subtotal is None else receipt.subtotal
    total_diff = base + (receipt.tax or Decimal(0)) - receipt.total

    items_ok = items_diff is None or abs(items_diff) <= allowed
    tax_on_top = abs(total_diff) <= allowed
    tax_included = (
        not tax_on_top and receipt.tax is not None and abs(base - receipt.total) <= allowed
    )
    return Reconciliation(
        ok=items_ok and (tax_on_top or tax_included),
        items_diff=items_diff,
        total_diff=total_diff,
        tax_included=tax_included,
        allowed=allowed,
    )
