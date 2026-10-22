"""``Receipt``/``Item``: Decimal money, exact JSON round trip, strict validation."""

from __future__ import annotations

from decimal import Decimal

import pytest
from pydantic import ValidationError

from receipt_extractor.core.receipt import MAX_ITEMS, Item, Receipt


def test_amounts_are_decimals_parsed_from_printed_text() -> None:
    receipt = Receipt(
        items=[Item(name="  Es Teh  ", qty="2", unit_price="@5.000", price="10.000")],
        subtotal="Rp 10.000",
        tax="1,000",
        total="11.000",
    )
    item = receipt.items[0]
    assert item.name == "Es Teh"
    assert (item.qty, item.unit_price, item.price) == (Decimal(2), Decimal(5000), Decimal(10000))
    assert (receipt.subtotal, receipt.tax, receipt.total) == (
        Decimal(10000),
        Decimal(1000),
        Decimal(11000),
    )
    assert receipt.currency == "IDR"


def test_json_round_trip_is_exact_and_money_is_a_string() -> None:
    receipt = Receipt(items=[Item(name="a", price=Decimal("0.10"))], total=Decimal("1000.50"))
    data = receipt.model_dump(mode="json")
    assert data["total"] == "1000.50"
    assert data["items"][0]["price"] == "0.10"
    assert Receipt.model_validate_json(receipt.model_dump_json()) == receipt


def test_float_input_uses_its_shortest_repr() -> None:
    receipt = Receipt(total=0.1, items=[Item(name="a", price=45000.5)])  # type: ignore[arg-type]
    assert receipt.total == Decimal("0.1")
    assert receipt.items[0].price == Decimal("45000.5")


def test_json_numbers_from_a_model_reply_validate() -> None:
    receipt = Receipt.model_validate_json(
        '{"total": 24000, "items": [{"name": "x", "price": 1.5}]}'
    )
    assert receipt.total == Decimal(24000)
    assert receipt.items[0].price == Decimal("1.5")


@pytest.mark.parametrize("bad", [True, "100%", "abc", float("nan"), float("inf"), "1" * 16, [1]])
def test_invalid_amounts_are_rejected(bad: object) -> None:
    with pytest.raises(ValidationError):
        Receipt(total=bad)  # type: ignore[arg-type]


def test_total_and_item_price_are_required_and_blank_is_missing() -> None:
    with pytest.raises(ValidationError):
        Receipt()  # type: ignore[call-arg]
    with pytest.raises(ValidationError):
        Receipt(total="-")
    with pytest.raises(ValidationError):
        Item(name="a", price="")


def test_optional_amounts_accept_blanks() -> None:
    receipt = Receipt(total="5", subtotal="-", tax="")
    assert receipt.subtotal is None
    assert receipt.tax is None


def test_extra_fields_and_empty_names_are_rejected() -> None:
    with pytest.raises(ValidationError):
        Receipt.model_validate({"total": "1", "store": "x"})
    with pytest.raises(ValidationError):
        Item(name="   ", price="1")


def test_currency_is_an_upper_case_iso_code() -> None:
    assert Receipt(total="1", currency=" usd ").currency == "USD"
    with pytest.raises(ValidationError):
        Receipt(total="1", currency="Rupiah")


def test_item_count_is_capped() -> None:
    items = [Item(name="a", price="1")] * (MAX_ITEMS + 1)
    with pytest.raises(ValidationError):
        Receipt(items=items, total="1")


def test_validation_errors_do_not_echo_the_amount_text() -> None:
    with pytest.raises(ValidationError) as info:
        Receipt(total="secret-ish 100%")
    assert "secret-ish" not in str(info.value.errors(include_input=False))
