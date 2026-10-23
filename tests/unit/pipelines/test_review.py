"""M2.2: up to two repairs, arithmetic reconciliation and the review flag, in A and B."""

from __future__ import annotations

import json
from collections.abc import Callable
from decimal import Decimal
from typing import Any

import pytest

from receipt_extractor.core.receipt import Receipt
from receipt_extractor.core.reconcile import reconcile, tolerance
from receipt_extractor.llm.client import LLMClient
from receipt_extractor.llm.fake import FakeProvider, FakeReply
from receipt_extractor.llm.sinks import MemorySink
from receipt_extractor.pipelines.ocr import OcrStage
from receipt_extractor.pipelines.pipeline_a import PipelineA
from receipt_extractor.pipelines.pipeline_b import PipelineB
from receipt_extractor.pipelines.replies import review
from tests.unit.pipelines.conftest import png_bytes
from tests.unit.pipelines.test_pipeline_a import INVALID, VALID

Factory = Callable[..., LLMClient]


def receipt_json(
    items: list[str], total: str, subtotal: str | None = None, tax: str | None = None
) -> str:
    return json.dumps(
        {
            "items": [{"name": f"ITEM {i}", "price": p} for i, p in enumerate(items)],
            "subtotal": subtotal,
            "tax": tax,
            "total": total,
        }
    )


def receipt(items: list[str], total: str, **kw: Any) -> Receipt:
    return Receipt.model_validate_json(receipt_json(items, total, **kw))


# --- review() ------------------------------------------------------------------------------


def test_review_of_a_receipt_that_adds_up() -> None:
    checked = review(receipt(["36.000", "15.000"], "51.000"))
    assert (checked.reconciled, checked.needs_review, checked.reconcile_diff) == (
        True,
        False,
        Decimal(0),
    )


def test_review_flags_items_that_do_not_add_up_to_the_subtotal() -> None:
    # Σ items 50.000 vs subtotal 51.000: the item check fails, its diff is reported.
    checked = review(receipt(["36.000", "14.000"], "56.100", subtotal="51.000", tax="5.100"))
    assert checked.reconciled is False and checked.needs_review is True
    assert checked.reconcile_diff == Decimal(-1000)


def test_review_flags_a_total_that_does_not_add_up() -> None:
    checked = review(receipt(["36.000", "15.000"], "60.000"))
    assert checked.needs_review is True
    assert checked.reconcile_diff == Decimal(-9000)  # 51.000 - 60.000


def test_rounding_within_tolerance_reconciles() -> None:
    # 0.5 % of 100.000 = 500: a 300 difference (tax rounded per line) is accepted…
    assert review(receipt(["100.300"], "100.000")).reconciled is True
    # …unless the pipeline is configured stricter.
    strict = review(receipt(["100.300"], "100.000"), rel_tolerance=Decimal("0.001"))
    assert (strict.reconciled, strict.reconcile_diff) == (False, Decimal(300))


def test_tax_included_layout_reconciles_with_zero_diff() -> None:
    checked = review(receipt(["60.000"], "60.000", subtotal="60.000", tax="5.455"))
    assert (checked.reconciled, checked.reconcile_diff) == (True, Decimal(0))


def test_no_receipt_needs_review_with_nothing_to_reconcile() -> None:
    checked = review(None)
    assert (checked.reconciled, checked.reconcile_diff, checked.needs_review) == (None, None, True)


def test_tolerance_is_configurable_and_validated() -> None:
    assert tolerance(Decimal(1000), rel_tolerance=Decimal("0.01")) == Decimal(10)
    assert tolerance(Decimal(0)) == Decimal("0.01")
    with pytest.raises(ValueError, match="non-negative"):
        tolerance(Decimal(1), rel_tolerance=Decimal(-1))
    # The scorer's default is untouched by pipeline settings.
    assert reconcile(receipt(["100.300"], "100.000")).ok is True


# --- pipelines (FakeProvider) ----------------------------------------------------------------


def a(make_client: Factory, provider: FakeProvider, ocr: OcrStage, mode: str, **kw: Any) -> Any:
    return PipelineA(
        client=make_client(provider), ocr=ocr, mode=mode, model_alias="test-chat", **kw
    )


def b(make_client: Factory, provider: FakeProvider, **kw: Any) -> PipelineB:
    return PipelineB(client=make_client(provider), model_alias="test-public", **kw)


async def test_invalid_then_valid_on_retry_reconciles(
    make_client: Factory, sink: MemorySink, receipt_ocr: OcrStage
) -> None:
    provider = FakeProvider(
        [FakeReply(text=INVALID), FakeReply(text=INVALID), FakeReply(text=VALID)]
    )
    result = await a(make_client, provider, receipt_ocr, "engineered").extract(
        png_bytes(), origin="public_dataset"
    )

    assert result.attempts == 3 and result.schema_valid
    assert (result.reconciled, result.needs_review) == (True, False)
    assert [r.stage for r in sink.records] == ["llm", "llm:repair", "llm:repair2"]
    # The repair prompt carries the Pydantic errors.
    assert "did not match the required JSON schema" in provider.calls[1].messages[-1]["content"]


async def test_invalid_twice_with_one_repair_needs_review(
    make_client: Factory, receipt_ocr: OcrStage
) -> None:
    provider = FakeProvider([FakeReply(text=INVALID), FakeReply(text=INVALID)])
    result = await a(make_client, provider, receipt_ocr, "engineered", max_repairs=1).extract(
        png_bytes(), origin="public_dataset"
    )
    assert result.attempts == 2 and result.receipt is None
    assert (result.needs_review, result.reconciled, result.failure) == (
        True,
        None,
        "schema_invalid",
    )


async def test_valid_but_sums_off_needs_review_in_a_and_b(
    make_client: Factory, receipt_ocr: OcrStage
) -> None:
    off = receipt_json(["36.000", "15.000"], "61.000")
    for pipeline in (
        a(make_client, FakeProvider([FakeReply(text=off)]), receipt_ocr, "engineered"),
        b(make_client, FakeProvider([FakeReply(text=off)])),
    ):
        result = await pipeline.extract(png_bytes(), origin="public_dataset")
        assert result.schema_valid and result.attempts == 1
        assert (result.reconciled, result.needs_review) == (False, True)
        assert result.reconcile_diff == Decimal(-10000)


async def test_rounding_within_tolerance_is_reconciled_in_b(make_client: Factory) -> None:
    near = receipt_json(["36.000", "15.200"], "51.000")  # 200 off, 0.39 % of the total
    result = await b(make_client, FakeProvider([FakeReply(text=near)])).extract(
        png_bytes(), origin="public_dataset"
    )
    assert (result.reconciled, result.needs_review, result.reconcile_diff) == (
        True,
        False,
        Decimal(200),
    )
    strict = await b(
        make_client, FakeProvider([FakeReply(text=near)]), reconcile_tolerance=Decimal("0.001")
    ).extract(png_bytes(), origin="public_dataset")
    assert (strict.reconciled, strict.needs_review) == (False, True)


async def test_naive_bypasses_retries_and_checks(
    make_client: Factory, receipt_ocr: OcrStage
) -> None:
    off = receipt_json(["36.000", "15.000"], "61.000")
    provider = FakeProvider([FakeReply(text=INVALID), FakeReply(text=off)])
    invalid = await a(make_client, provider, receipt_ocr, "naive").extract(
        png_bytes(), origin="public_dataset"
    )
    assert len(provider.calls) == 1 and invalid.attempts == 1
    assert (invalid.reconciled, invalid.needs_review, invalid.reconcile_diff) == (None,) * 3

    wrong_sum = await a(make_client, provider, receipt_ocr, "naive").extract(
        png_bytes(), origin="public_dataset"
    )
    assert wrong_sum.schema_valid
    assert (wrong_sum.reconciled, wrong_sum.needs_review) == (None, None)  # not checked


async def test_empty_ocr_in_engineered_mode_needs_review(make_client: Factory) -> None:
    from tests.unit.pipelines.conftest import FakeOcrEngine  # noqa: PLC0415

    provider = FakeProvider()
    result = await a(make_client, provider, OcrStage(FakeOcrEngine([])), "engineered").extract(
        png_bytes(), origin="public_dataset"
    )
    assert provider.calls == []
    assert (result.failure, result.needs_review, result.reconciled) == ("ocr_empty", True, None)
