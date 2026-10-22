"""The receipt scorer (M1.2): hand-built cases, gold-vs-gold, registry metrics and run details."""

from __future__ import annotations

import json
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from receipt_extractor.core.receipt import Receipt
from receipt_extractor.data.cord_mapping import map_cord_gold
from receipt_extractor.eval.config import RunConfig
from receipt_extractor.eval.errors import RunConfigError
from receipt_extractor.eval.metrics import case_details, get_metric, metric_names
from receipt_extractor.eval.records import SUMMARY_FILENAME, Summary
from receipt_extractor.eval.runner import execute_run, prepare_config
from receipt_extractor.eval.scorer import (
    NAME_MATCH_THRESHOLD,
    coerce_prediction,
    match_items,
    name_similarity,
    normalise_name,
    score_receipt,
)
from receipt_extractor.eval.tasks import build_task

from .conftest import base_config, read_rows

FIXTURES = Path(__file__).resolve().parents[2] / "fixtures" / "cord"
RECEIPT_METRICS = (
    "field_f1",
    "items_precision",
    "items_recall",
    "items_f1",
    "subtotal_f1",
    "tax_f1",
    "total_f1",
    "valid_json",
    "reconcile",
)
COMPARATIVE_METRICS = tuple(name for name in RECEIPT_METRICS if name != "reconcile")


def item(name: str, price: str, **extra: Any) -> dict[str, Any]:
    return {"name": name, "price": price, **extra}


def receipt(
    items: list[dict[str, Any]],
    total: str,
    subtotal: str | None = None,
    tax: str | None = None,
) -> dict[str, Any]:
    return {"items": items, "subtotal": subtotal, "tax": tax, "total": total, "currency": "IDR"}


# Four lines, subtotal 100000 + tax 10000 = total 110000.
GOLD = receipt(
    [
        item("NASI GORENG", "40000"),
        item("ES TEH MANIS", "10000"),
        item("AYAM BAKAR", "35000"),
        item("KERUPUK", "15000"),
    ],
    total="110000",
    subtotal="100000",
    tax="10000",
)


def metrics_of(prediction: Any, gold: Any = GOLD) -> dict[str, float | None]:
    return {name: get_metric(name).compute(prediction, gold) for name in RECEIPT_METRICS}


# --- Hand-built cases (IMPLEMENTATION_PLAN M1.2) -------------------------------------------------


def test_perfect_prediction_scores_one_everywhere() -> None:
    assert metrics_of(GOLD) == dict.fromkeys(RECEIPT_METRICS, 1.0)


def test_reordered_items_score_the_same_as_in_order() -> None:
    reordered = {**GOLD, "items": list(reversed(GOLD["items"]))}
    assert metrics_of(reordered) == metrics_of(GOLD)
    pairs = score_receipt(reordered, GOLD).matching.pairs
    assert [(p.gold, p.pred) for p in pairs] == [(0, 3), (1, 2), (2, 1), (3, 0)]


def test_split_item_loses_only_that_line() -> None:
    # NASI GORENG 40000 printed as two lines of 20000: neither agrees on the price.
    split = {
        **GOLD,
        "items": [
            item("NASI GORENG", "20000"),
            item("NASI GORENG", "20000"),
            *GOLD["items"][1:],
        ],
    }
    score = score_receipt(split, GOLD)
    assert (score.items.tp, score.items.n_pred, score.items.n_gold) == (3, 5, 4)
    assert score.items.precision == pytest.approx(3 / 5)
    assert score.items.recall == pytest.approx(3 / 4)
    assert score.items.f1 == pytest.approx(2 * 3 / (5 + 4))
    # One split half pairs with the gold line by name (a near miss), the other is unmatched.
    near_miss = [p for p in score.matching.pairs if not p.match]
    assert len(near_miss) == 1
    assert near_miss[0].gold == 0
    assert near_miss[0].name_ok
    assert not near_miss[0].price_ok
    assert len(score.matching.unmatched_pred) == 1
    # Totals are untouched: field F1 = (3 items + 3 scalars) TP, 2 FP, 1 FN.
    assert score.field_f1 == pytest.approx(2 * 6 / (2 * 6 + 2 + 1))
    assert score.reconciled  # the split lines still add up


def test_merged_items_lose_both_lines() -> None:
    # ES TEH MANIS + KERUPUK read as one line priced at their sum.
    merged = {
        **GOLD,
        "items": [GOLD["items"][0], item("ES TEH MANIS KERUPUK", "25000"), GOLD["items"][2]],
    }
    score = score_receipt(merged, GOLD)
    assert (score.items.tp, score.items.n_pred, score.items.n_gold) == (2, 3, 4)
    assert score.items.precision == pytest.approx(2 / 3)
    assert score.items.recall == pytest.approx(2 / 4)
    assert len(score.matching.unmatched_gold) + sum(not p.match for p in score.matching.pairs) == 2
    assert score.subtotal.ok
    assert score.total.ok


def test_hallucinated_item_costs_precision_not_recall() -> None:
    hallucinated = {**GOLD, "items": [*GOLD["items"], item("TEH BOTOL", "8000")]}
    values = metrics_of(hallucinated)
    assert values["items_recall"] == 1.0
    assert values["items_precision"] == pytest.approx(4 / 5)
    assert values["items_f1"] == pytest.approx(8 / 9)
    assert values["subtotal_f1"] == values["tax_f1"] == values["total_f1"] == 1.0
    assert values["reconcile"] == 0.0  # 108000 ≠ subtotal 100000
    assert score_receipt(hallucinated, GOLD).matching.unmatched_pred == (4,)


def test_missing_item_costs_recall_not_precision() -> None:
    missing = {**GOLD, "items": GOLD["items"][:3]}
    values = metrics_of(missing)
    assert values["items_precision"] == 1.0
    assert values["items_recall"] == pytest.approx(3 / 4)


# --- Normalisation and matching ------------------------------------------------------------------


def test_scalar_fields_compare_after_amount_normalisation() -> None:
    printed = {**GOLD, "subtotal": "Rp 100.000", "tax": 10000, "total": "110,000.00"}
    assert metrics_of(printed) == dict.fromkeys(RECEIPT_METRICS, 1.0)


def test_item_names_match_despite_case_spacing_and_small_ocr_noise() -> None:
    noisy = {
        **GOLD,
        "items": [
            item("Nasi  Goreng", "40000"),
            item("es teh manis", "10.000"),
            item("AYAM BAKAR.", "35000"),
            item("KERUPUK", "15,000"),
        ],
    }
    assert metrics_of(noisy)["items_f1"] == 1.0


def test_wrong_price_or_unrelated_name_is_not_a_match() -> None:
    wrong = {
        **GOLD,
        "items": [
            item("NASI GORENG", "41000"),  # name agrees, price does not
            item("SOTO DAGING", "10000"),  # price agrees, name does not
            *GOLD["items"][2:],
        ],
    }
    score = score_receipt(wrong, GOLD)
    assert score.items.tp == 2
    by_gold = {pair.gold: pair for pair in score.matching.pairs}
    assert by_gold[0].name_ok and not by_gold[0].price_ok
    assert by_gold[1].price_ok and not by_gold[1].name_ok


def test_matching_prefers_full_matches_over_name_similarity() -> None:
    # Both predictions are named like gold 0; only the second one also has its price.
    gold = [Receipt.model_validate(receipt([item("KOPI SUSU", "18000")], "18000")).items[0]]
    pred = Receipt.model_validate(
        receipt([item("KOPI SUSU", "20000"), item("KOPI SUSU.", "18000")], "38000")
    ).items
    matching = match_items(pred, gold)
    assert matching.true_positives == 1
    assert matching.pairs[0].pred == 1
    assert matching.unmatched_pred == (0,)


def test_duplicate_lines_match_one_to_one() -> None:
    gold = receipt([item("AQUA", "5000"), item("AQUA", "5000")], "10000")
    pred = receipt([item("AQUA", "5000"), item("AQUA", "5000"), item("AQUA", "5000")], "15000")
    score = score_receipt(pred, gold)
    assert (score.items.tp, score.items.n_pred, score.items.n_gold) == (2, 3, 2)


def test_name_similarity_and_normalisation() -> None:
    assert normalise_name(f"  Es{chr(0x3000)}TEH	Manis ") == "es teh manis"
    full_width = "".join(chr(ord(c) + 0xFEE0) for c in "COFFEE")
    assert normalise_name(full_width) == "coffee"  # NFKC
    assert name_similarity("NASI GORENG", "nasi goreng") == 1.0
    assert name_similarity("NASI GORENG", "NASI GORENG SPC") >= NAME_MATCH_THRESHOLD
    assert name_similarity("NASI GORENG", "ES TEH") < NAME_MATCH_THRESHOLD


def test_empty_item_lists() -> None:
    no_items = receipt([], total="5000", subtotal=None)
    score = score_receipt(no_items, no_items)
    assert (score.items.precision, score.items.recall, score.items.f1) == (None, None, None)
    assert score.field_f1 == 1.0
    predicted_nothing = score_receipt({**GOLD, "items": []}, GOLD)
    assert (predicted_nothing.items.precision, predicted_nothing.items.recall) == (0.0, 0.0)
    only_predicted = score_receipt(GOLD, {**GOLD, "items": []})
    assert (only_predicted.items.precision, only_predicted.items.recall) == (0.0, 0.0)


@pytest.mark.parametrize(
    ("pred_tax", "gold_tax", "expected_f1", "expected_ok"),
    [
        (None, None, None, True),  # absent on both sides: not applicable
        ("10000", "10000", 1.0, True),
        ("9000", "10000", 0.0, False),  # wrong value: FP + FN
        (None, "10000", 0.0, False),  # missed: FN
        ("10000", None, 0.0, False),  # invented: FP
    ],
)
def test_scalar_field_cases(
    pred_tax: str | None, gold_tax: str | None, expected_f1: float | None, expected_ok: bool
) -> None:
    score = score_receipt({**GOLD, "tax": pred_tax}, {**GOLD, "tax": gold_tax})
    assert score.tax.f1 == expected_f1
    assert score.tax.ok is expected_ok
    assert score.details()["field_scores"]["tax_ok"] is expected_ok


# --- Prediction forms and invalid output ---------------------------------------------------------


def test_extraction_result_and_bare_receipt_score_identically() -> None:
    wrapped = {"pipeline": "A", "receipt": GOLD, "schema_valid": True, "attempts": 1}
    assert metrics_of(wrapped) == metrics_of(GOLD)
    assert coerce_prediction(wrapped) == Receipt.model_validate(GOLD)
    assert coerce_prediction(Receipt.model_validate(GOLD)) == Receipt.model_validate(GOLD)


@pytest.mark.parametrize(
    "prediction",
    [
        None,
        {"pipeline": "A", "receipt": None, "schema_valid": False},
        {"items": [], "total": "not a number"},
        {"items": [{"name": "X"}], "total": "1000"},  # item without a price
        {**GOLD, "extra_field": 1},
        {"pipeline": "A", "receipt": {"total": True}},
        "{'total': 1000}",
        [GOLD],
        42,
    ],
)
def test_invalid_prediction_scores_as_an_empty_receipt(prediction: Any) -> None:
    values = metrics_of(prediction)
    assert values["valid_json"] == 0.0
    assert values["reconcile"] == 0.0
    assert values["field_f1"] == 0.0
    assert values["items_precision"] == values["items_recall"] == values["items_f1"] == 0.0
    assert values["subtotal_f1"] == values["tax_f1"] == values["total_f1"] == 0.0
    assert score_receipt(prediction, GOLD).matching.unmatched_gold == (0, 1, 2, 3)


def test_invalid_gold_is_a_dataset_error() -> None:
    with pytest.raises(ValueError, match="gold is not a valid Receipt"):
        score_receipt(GOLD, {"items": []})


# --- Gold vs gold --------------------------------------------------------------------------------


@pytest.mark.parametrize("case_id", ["test-0002", "test-0009", "test-0099"])
def test_real_cord_gold_scored_against_itself_is_one(case_id: str) -> None:
    record = json.loads((FIXTURES / f"{case_id}.json").read_text(encoding="utf-8"))
    mapped = map_cord_gold(record["gt_parse"]).receipt
    assert mapped is not None
    gold = mapped.model_dump(mode="json")
    values = metrics_of(gold, gold)
    for name in COMPARATIVE_METRICS:
        assert values[name] in (1.0, None), name  # None only for a field absent from the receipt
    assert values["field_f1"] == values["items_f1"] == values["total_f1"] == 1.0


_names = st.text(alphabet="ABCDEFGHIJKLMNOPQRSTUVWXYZ abcdefghij0123456789()", min_size=1)
_amounts = st.integers(min_value=0, max_value=10**9).map(str)
_items = st.lists(st.builds(item, _names.filter(str.strip), _amounts), max_size=15)
_receipts = st.builds(
    lambda items, sub, tax, total: receipt(items, total, sub, tax),
    _items,
    st.none() | _amounts,
    st.none() | _amounts,
    _amounts,
)


@settings(max_examples=150, deadline=None)
@given(gold=_receipts, data=st.data())
def test_any_gold_against_itself_is_one_in_any_order(gold: dict[str, Any], data: Any) -> None:
    shuffled = {**gold, "items": data.draw(st.permutations(gold["items"]))}
    score = score_receipt(shuffled, gold)
    assert score.valid
    assert score.field_f1 == 1.0
    assert score.items.f1 in (1.0, None)
    for name in ("subtotal", "tax", "total"):
        assert score.field(name).ok
        assert score.field(name).f1 in (1.0, None)


@settings(max_examples=100, deadline=None)
@given(pred=_receipts, gold=_receipts, data=st.data())
def test_scores_are_bounded_and_order_insensitive(
    pred: dict[str, Any], gold: dict[str, Any], data: Any
) -> None:
    first = score_receipt(pred, gold)
    shuffled = {**pred, "items": data.draw(st.permutations(pred["items"]))}
    second = score_receipt(shuffled, gold)
    assert first.items.tp == second.items.tp
    assert first.field_f1 == second.field_f1
    for value in (first.field_f1, first.items.precision, first.items.recall, first.items.f1):
        assert value is None or 0.0 <= value <= 1.0


# --- Registry, details and the runner ------------------------------------------------------------


def test_receipt_metrics_are_registered_with_bootstrap_cis() -> None:
    assert set(RECEIPT_METRICS) <= set(metric_names())
    assert all(get_metric(name).bootstrap_ci for name in RECEIPT_METRICS)


def test_case_details_follow_backend_schema() -> None:
    metrics = {name: get_metric(name) for name in ("exact_match", "field_f1", "items_f1")}
    details = case_details(GOLD, GOLD, metrics)
    assert set(details) == {"field_scores", "item_matches"}
    assert {"items_p", "items_r", "items_f1", "subtotal_ok", "tax_ok", "total_ok"} <= set(
        details["field_scores"]
    )
    assert details["item_matches"]["unmatched_gold"] == []
    assert len(details["item_matches"]["pairs"]) == 4
    json.dumps(details)  # JSON-serialisable as stored in results.jsonl
    assert case_details(GOLD, GOLD, {"exact_match": get_metric("exact_match")}) == {}


def test_worst_case_receipt_scores_quickly() -> None:
    # 500 x 500 items with 300-character names: the Receipt maximum.
    many = receipt([item(f"{i:04d} " + "X" * 295, str(i)) for i in range(500)], "1")
    reverse = {**many, "items": list(reversed(many["items"]))}
    start = time.perf_counter()
    score = score_receipt(reverse, many)
    elapsed = time.perf_counter() - start
    assert score.items.tp == 500
    assert elapsed < 10.0


async def test_gold_run_scores_one_and_records_details(
    project: Path, write_jsonl: Callable[[list[dict[str, Any]]], str]
) -> None:
    cases = [
        {"case_id": "r-1", "input": {}, "gold": GOLD},
        {"case_id": "r-2", "input": {}, "gold": receipt([item("KOPI", "18000")], "18000")},
    ]
    path = write_jsonl(cases)
    config = base_config(
        variant="gold",
        task="gold",
        params={},
        dataset={"kind": "jsonl", "name": "receipts", "split": "test", "path": path},
        metrics=list(RECEIPT_METRICS),
        primary_metric="field_f1",
    )
    prepared = prepare_config(RunConfig.model_validate(config), project)
    outcome = await execute_run(prepared, build_task("gold", {}))
    assert outcome.status == "completed"

    rows = read_rows(prepared.run_dir)
    assert {row.status for row in rows} == {"pass"}
    for row in rows:
        assert row.details["field_scores"]["valid_json"] is True
        assert row.details["item_matches"]["unmatched_pred"] == []
        assert set(row.details["metrics"]) == set(RECEIPT_METRICS)
    summary = Summary.model_validate_json((prepared.run_dir / SUMMARY_FILENAME).read_text())
    for name in COMPARATIVE_METRICS:
        assert summary.metrics[name].value == 1.0, name


def test_gold_task_rejects_params() -> None:
    with pytest.raises(RunConfigError, match="no params"):
        build_task("gold", {"x": 1})


@pytest.fixture
def write_jsonl(project: Path) -> Callable[[list[dict[str, Any]]], str]:
    def write(cases: list[dict[str, Any]]) -> str:
        path = project / "data" / "receipts.jsonl"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("".join(json.dumps(case) + "\n" for case in cases), encoding="utf-8")
        return "data/receipts.jsonl"

    return write


def test_one_full_match_beats_two_name_only_pairs() -> None:
    # Pairing by name alone (p0-g1, p1-g0) agrees on two names but on no price; the scorer
    # must prefer the assignment with a full match (p0-g0) even though it pairs fewer names.
    gold = receipt([item("NASI GORENG", "25000"), item("NASI GORENG AYAM", "30000")], "55000")
    pred = receipt([item("NASI GORENG AYAM", "25000"), item("NASI GORENG.", "99999")], "55000")
    assert name_similarity("NASI GORENG.", "NASI GORENG AYAM") < NAME_MATCH_THRESHOLD
    score = score_receipt(pred, gold)
    assert score.items.tp == 1
    assert [(p.gold, p.pred, p.match) for p in score.matching.pairs] == [(0, 0, True)]
