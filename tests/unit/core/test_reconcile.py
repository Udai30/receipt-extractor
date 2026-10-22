"""Arithmetic reconciliation: items → subtotal → total, tax on top or included."""

from __future__ import annotations

from decimal import Decimal
from typing import Any

import pytest

from receipt_extractor.core.receipt import Receipt
from receipt_extractor.core.reconcile import reconcile, tolerance


def build(
    prices: list[str], total: str, subtotal: str | None = None, tax: str | None = None
) -> Receipt:
    items: list[dict[str, Any]] = [{"name": f"item {i}", "price": p} for i, p in enumerate(prices)]
    return Receipt.model_validate(
        {"items": items, "subtotal": subtotal, "tax": tax, "total": total}
    )


@pytest.mark.parametrize(
    ("receipt", "ok"),
    [
        (build(["40000", "60000"], "110000", "100000", "10000"), True),  # tax on top
        (build(["30000", "30000"], "60000", "60000", "5455"), True),  # tax included (CORD 0000)
        (build(["28000"], "28000", "28000"), True),  # no tax
        (build(["20000", "16000"], "36000"), True),  # no subtotal: items stand in
        (build(["20000", "16000"], "39600", tax="3600"), True),  # no subtotal, tax on top
        (build(["194000"], "174600", "194000"), False),  # a discount (CORD test-0004)
        (build(["238000"], "281435", "238000", "25585"), False),  # a service charge
        (build(["40000", "50000"], "110000", "100000", "10000"), False),  # an item missing
        (build([], "5000", "5000"), False),  # no items but a subtotal
        (build([], "0"), True),
    ],
)
def test_reconcile(receipt: Receipt, ok: bool) -> None:
    assert reconcile(receipt).ok is ok


def test_diffs_are_signed_and_tax_inclusive_is_flagged() -> None:
    on_top = reconcile(build(["40000", "59000"], "110000", "100000", "10000"))
    assert on_top.items_diff == Decimal(-1000)
    assert on_top.total_diff == Decimal(0)
    assert not on_top.tax_included
    included = reconcile(build(["60000"], "60000", "60000", "5455"))
    assert included.ok
    assert included.tax_included
    assert included.total_diff == Decimal(5455)
    assert reconcile(build(["5"], "5")).items_diff is None


def test_tolerance_is_half_a_percent_with_a_cent_floor() -> None:
    assert tolerance(Decimal(110000)) == Decimal(550)
    assert tolerance(Decimal(0)) == Decimal("0.01")
    assert tolerance(Decimal(-1000)) == Decimal(5)
    # Tax rounded per line: 4 off on 54,545 is within tolerance; 1,000 off is not.
    assert reconcile(build(["54545"], "59996", "54545", "5455")).ok
    assert not reconcile(build(["54545"], "61000", "54545", "5455")).ok
