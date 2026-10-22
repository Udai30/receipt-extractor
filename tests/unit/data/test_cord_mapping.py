"""CORD ``gt_parse`` → ``Receipt``: golden test on 3 real records plus every mapping rule."""

from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest

from receipt_extractor.core.receipt import Receipt
from receipt_extractor.data.cord_mapping import map_cord_gold

FIXTURES = Path(__file__).resolve().parents[2] / "fixtures" / "cord"

# Expected gold, checked by hand against each record (and its receipt image):
GOLDEN: dict[str, dict[str, Any]] = {
    # A drink with a priced add-on in `sub`: the add-on is its own line item.
    "test-0002": {
        "items": [
            {"name": "JASMINE MT ( L )", "qty": "1", "unit_price": None, "price": "24000"},
            {"name": "COCONUT JELLY ( L )", "qty": None, "unit_price": None, "price": "4000"},
        ],
        "subtotal": "28000",
        "tax": None,
        "total": "28000",
        "currency": "IDR",
    },
    # No total_price: total = cashprice 50000 - changeprice 14000.
    "test-0009": {
        "items": [
            {"name": "Bumbu Kaldu Ayam 1", "qty": None, "unit_price": "36000", "price": "36000"}
        ],
        "subtotal": "36000",
        "tax": None,
        "total": "36000",
        "currency": "IDR",
    },
    # A bundle with six zero-priced `sub` lines; tax_price is ["6,818", "0"] → 6818.
    "test-0099": {
        "items": [
            {"name": "FL-Xmas 30 Off", "qty": "1", "unit_price": None, "price": "68180"},
            {"name": "PAKET SLICES", "qty": "1", "unit_price": None, "price": "0"},
            {"name": "FL Cake - French Vanilla SLC", "qty": "1", "unit_price": None, "price": "0"},
            {"name": "PAKET SLICES", "qty": "1", "unit_price": None, "price": "0"},
            {"name": "FL Cake - Green Tea SLC", "qty": "1", "unit_price": None, "price": "0"},
            {"name": "PAKET SLICES", "qty": "1", "unit_price": None, "price": "0"},
            {"name": "FL Cake - Belgium Choco SLC", "qty": "1", "unit_price": None, "price": "0"},
        ],
        "subtotal": "68180",
        "tax": "6818",
        "total": "75000",
        "currency": "IDR",
    },
}
GOLDEN_NOTES = {
    "test-0002": (),
    "test-0009": ("total = cashprice - changeprice",),
    "test-0099": ("tax = sum of several tax_price values",),
}


def _record(case_id: str) -> dict[str, Any]:
    data: dict[str, Any] = json.loads((FIXTURES / f"{case_id}.json").read_text(encoding="utf-8"))
    return data


@pytest.mark.parametrize("case_id", sorted(GOLDEN))
def test_golden_real_cord_records(case_id: str) -> None:
    record = _record(case_id)
    assert record["meta"]["split"] == "test"
    result = map_cord_gold(record["gt_parse"])
    assert result.issue is None
    assert result.receipt is not None
    assert result.receipt.model_dump(mode="json") == GOLDEN[case_id]
    assert result.notes == GOLDEN_NOTES[case_id]
    # The gold file format round-trips exactly.
    assert Receipt.model_validate_json(result.receipt.model_dump_json()) == result.receipt


def test_mapping_is_deterministic() -> None:
    record = _record("test-0099")["gt_parse"]
    assert map_cord_gold(record) == map_cord_gold(json.loads(json.dumps(record)))


def _map(gt: dict[str, Any]) -> Receipt:
    result = map_cord_gold(gt)
    assert result.receipt is not None, result.issue
    return result.receipt


def test_single_menu_dict_and_list_are_equivalent() -> None:
    entry = {"nm": "A", "price": "1.000"}
    total = {"total_price": "1.000"}
    assert _map({"menu": entry, "total": total}) == _map({"menu": [entry], "total": total})


@pytest.mark.parametrize(
    ("cnt", "qty"),
    [
        ("2", "2"),
        ("2x", "2"),
        ("x2", "2"),
        ("1 X", "1"),
        ("*2", "2"),
        ("1Prs", "1"),
        ("2.00", "2.00"),
    ],
)
def test_quantity_markers_and_units(cnt: str, qty: str) -> None:
    receipt = _map({"menu": {"nm": "A", "cnt": cnt, "price": "1"}, "total": {"total_price": "1"}})
    assert receipt.items[0].qty == Decimal(qty)


def test_unparseable_quantity_is_dropped_with_a_note() -> None:
    result = map_cord_gold(
        {"menu": {"nm": "A", "cnt": "lots", "price": "1"}, "total": {"total_price": "1"}}
    )
    assert result.receipt is not None
    assert result.receipt.items[0].qty is None
    assert result.notes == ("menu[0].cnt: unparseable quantity ignored",)


def test_price_fallbacks_and_priceless_entries() -> None:
    result = map_cord_gold(
        {
            "menu": [
                {"nm": "Blus", "unitprice": "@120,000", "cnt": "1", "itemsubtotal": "120,000"},
                {"nm": "Teh Tawar", "unitprice": "4.000", "cnt": "2"},
                {"nm": "=*LARGE*==", "cnt": "2"},
            ],
            "total": {"total_price": "128,000"},
        }
    )
    assert result.receipt is not None
    assert [(i.name, i.price) for i in result.receipt.items] == [
        ("Blus", Decimal(120000)),
        ("Teh Tawar", Decimal(8000)),
    ]
    assert result.notes == (
        "menu[0]: price from itemsubtotal",
        "menu[1]: price = unitprice x cnt",
        "menu[2]: entry without price dropped",
    )


def test_sub_entries_priced_become_items_unpriced_are_dropped() -> None:
    result = map_cord_gold(
        {
            "menu": {
                "nm": "Kopi",
                "price": "19.000",
                "sub": [{"nm": "Ice"}, {"nm": "Extra shot", "price": "5.000"}, {"price": "19.000"}],
            },
            "total": {"total_price": "24.000"},
        }
    )
    assert result.receipt is not None
    assert [i.name for i in result.receipt.items] == ["Kopi", "Extra shot"]
    assert result.notes == (
        "menu[0].sub[0]: modifier without price dropped",
        "menu[0].sub[2]: price line without name dropped",
    )


@pytest.mark.parametrize(
    ("total", "expected", "note"),
    [
        ({"total_etc": "20.000"}, "20000", "total from total_etc"),
        (
            {"cashprice": "50.000", "changeprice": "30.000"},
            "20000",
            "total = cashprice - changeprice",
        ),
        ({"cashprice": "42,000"}, "42000", "total from cashprice"),
        ({"cashprice": "0", "changeprice": "0"}, "0", "total from cashprice"),
        ({"creditcardprice": "3,112,800"}, "3112800", "total from creditcardprice"),
        ({"emoneyprice": "Rp 39.600"}, "39600", "total from emoneyprice"),
    ],
)
def test_total_fallbacks(total: dict[str, str], expected: str, note: str) -> None:
    result = map_cord_gold({"menu": {"nm": "A", "price": "1"}, "total": total})
    assert result.receipt is not None
    assert result.receipt.total == Decimal(expected)
    assert result.notes == (note,)


def test_total_price_wins_over_fallbacks() -> None:
    receipt = _map({"total": {"total_price": "10", "cashprice": "50", "changeprice": "40"}})
    assert receipt.total == Decimal(10)
    assert receipt.items == []


@pytest.mark.parametrize(
    ("gt", "issue"),
    [
        (
            {"menu": {"nm": "A", "price": "1"}},
            "no total_price and no paid amount to derive it from",
        ),
        ({"total": {"total_price": "100%"}}, "total.total_price: unparseable amount"),
        ({"total": {"total_price": ["1", "2"]}}, "total.total_price: several different values"),
        (
            {"menu": {"price": "1"}, "total": {"total_price": "1"}},
            "menu[0]: priced entry without a name",
        ),
        (
            {"menu": {"nm": "A", "price": "x"}, "total": {"total_price": "1"}},
            "menu[0].price: unparseable amount",
        ),
        ({"menu": ["oops"], "total": {"total_price": "1"}}, "menu holds a non-object entry"),
        ({"sub_total": "5", "total": {"total_price": "1"}}, "sub_total is not an object"),
        ({"menu": {"nm": 5, "price": "1"}, "total": {"total_price": "1"}}, "menu[0].nm: not text"),
        ("not a dict", "gt_parse is not an object"),
    ],
)
def test_unmappable_records_are_reported_not_guessed(gt: Any, issue: str) -> None:
    result = map_cord_gold(gt)
    assert result.receipt is None
    assert result.issue == issue


def test_optional_amounts_degrade_to_none_with_notes() -> None:
    result = map_cord_gold(
        {"sub_total": {"subtotal_price": "n/a", "tax_price": "-"}, "total": {"total_price": "5"}}
    )
    assert result.receipt is not None
    assert (result.receipt.subtotal, result.receipt.tax) == (None, None)
    assert result.notes == ("sub_total.subtotal_price: unparseable amount ignored",)


def test_split_name_words_are_joined_and_repeated_list_values_collapse() -> None:
    receipt = _map(
        {
            "menu": {"nm": ["NASI", "GORENG"], "price": ["15.000", "15.000"]},
            "total": {"total_price": "15.000"},
        }
    )
    assert receipt.items[0].name == "NASI GORENG"
    assert receipt.items[0].price == Decimal(15000)


def test_amount_formats_are_counted_without_values() -> None:
    result = map_cord_gold(
        {
            "menu": [{"nm": "A", "price": "24,000"}, {"nm": "B", "price": "Rp 12.000"}],
            "total": {"total_price": "36,000"},
        }
    )
    assert dict(result.amount_formats) == {"9,999": 2, "Rp 9.999": 1}
