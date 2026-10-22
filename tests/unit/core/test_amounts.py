"""``parse_amount()``: the one tested place receipt number formats are interpreted."""

from __future__ import annotations

from decimal import Decimal

import pytest
from hypothesis import example, given
from hypothesis import strategies as st

from receipt_extractor.core.amounts import (
    MAX_AMOUNT_CHARS,
    AmountParseError,
    amount_format,
    parse_amount,
)


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("1.000", "1000"),
        ("60.000", "60000"),
        ("1,000.50", "1000.50"),
        ("1,000", "1000"),
        ("9.500,00", "9500.00"),
        ("1,944,000", "1944000"),
        ("1.380.000", "1380000"),
        ("2 600", "2600"),
        ("47619.00", "47619.00"),
        ("0.00", "0.00"),
        ("99,99", "99.99"),
        ("0", "0"),
        ("909", "909"),
        ("Rp 12.000", "12000"),
        ("Rp. 111,000", "111000"),
        ("Rp.56.000", "56000"),
        ("rp 5.000", "5000"),
        ("IDR 1.500", "1500"),
        ("Rp 5.000,-", "5000"),
        ("@ 12.000", "12000"),
        ("@880", "880"),
        ("*46000", "46000"),
        ("[145,900]", "145900"),
        ("  24,000  ", "24000"),
        ("-60.000", "-60000"),
        ("-2 600", "-2600"),
        ("−7.200", "-7200"),  # noqa: RUF001 - Unicode minus sign
        ("7.200-", "-7200"),
        ("(5.000)", "-5000"),
        ("-Rp 9,999", "-9999"),
        ("Rp -5.000", "-5000"),
        ("+1.000", "1000"),
        ("$12.50", "12.50"),
    ],
)
def test_printed_formats(text: str, expected: str) -> None:
    value = parse_amount(text)
    assert value == Decimal(expected)
    assert str(value) == expected  # printed precision is kept


@pytest.mark.parametrize("text", [None, "", "   ", "-", "--", "—", "Rp", "Rp. ", "@"])
def test_blanks_are_none(text: str | None) -> None:
    assert parse_amount(text) is None


def test_accounting_zero_is_not_negative_zero() -> None:
    assert str(parse_amount("(0)")) == "0"


@pytest.mark.parametrize(
    "text",
    ["100%", "abc", "x1", "1.2.3", "1,00,000", "1.000.00", "12,3456", "1..000", "1,", ".5", "1e5"],
)
def test_non_amounts_raise(text: str) -> None:
    with pytest.raises(AmountParseError):
        parse_amount(text)


def test_overlong_input_is_rejected_without_echo() -> None:
    text = "9" * (MAX_AMOUNT_CHARS + 1)
    with pytest.raises(AmountParseError) as info:
        parse_amount(text)
    assert text not in str(info.value)


# --- properties ----------------------------------------------------------------------------

_PREFIXES = ["", "Rp ", "Rp", "Rp.", "Rp. ", "RP ", "IDR ", "@", "@ ", "*"]


def _group(n: int, sep: str) -> str:
    return f"{n:,}".replace(",", sep)


@st.composite
def printed_amounts(draw: st.DrawFn) -> tuple[str, Decimal]:
    """An amount printed in one of the receipt conventions, plus its exact value."""
    whole = draw(st.integers(min_value=0, max_value=10**12))
    cents = draw(st.none() | st.integers(min_value=0, max_value=99))
    style = draw(st.sampled_from(["dot", "comma", "space", "plain"]))
    thousands, decimal_sep = {
        "dot": (".", ","),
        "comma": (",", "."),
        "space": (" ", draw(st.sampled_from([",", "."]))),
        "plain": ("", "."),
    }[style]
    body = _group(whole, thousands) if thousands else str(whole)
    value = Decimal(whole)
    if cents is not None:
        body += f"{decimal_sep}{cents:02d}"
        value = Decimal(f"{whole}.{cents:02d}")
    body = draw(st.sampled_from(_PREFIXES)) + body
    sign = draw(st.sampled_from(["", "minus", "paren", "trailing"]))
    if sign:
        body = {"minus": f"-{body}", "paren": f"({body})", "trailing": f"{body}-"}[sign]
        value = -value
    return body, value


@given(printed_amounts())
@example(("1.000", Decimal(1000)))
@example(("1,000.50", Decimal("1000.50")))
@example(("-1.000.000,25", Decimal("-1000000.25")))
@example(("(Rp 2 600)", Decimal(-2600)))
def test_round_trip_of_every_convention(case: tuple[str, Decimal]) -> None:
    text, value = case
    assert parse_amount(text) == value


@given(printed_amounts(), st.sampled_from(["", " ", "  ", "\t"]))
def test_surrounding_whitespace_is_ignored(case: tuple[str, Decimal], pad: str) -> None:
    text, value = case
    assert parse_amount(f"{pad}{text}{pad}") == value


@given(st.integers(min_value=1000, max_value=10**12), st.sampled_from([".", ",", " "]))
def test_thousands_separators_never_change_the_value(whole: int, sep: str) -> None:
    assert parse_amount(_group(whole, sep)) == parse_amount(str(whole)) == Decimal(whole)


@given(printed_amounts())
def test_negation_is_symmetric(case: tuple[str, Decimal]) -> None:
    text, value = case
    if text.startswith("-"):
        return
    assert parse_amount(f"-{text}") == -value


@given(st.text(max_size=MAX_AMOUNT_CHARS + 8))
def test_arbitrary_text_is_an_amount_a_blank_or_a_parse_error(text: str) -> None:
    try:
        value = parse_amount(text)
    except AmountParseError:
        return
    assert value is None or (isinstance(value, Decimal) and value.is_finite())


@given(printed_amounts())
def test_format_label_hides_the_value(case: tuple[str, Decimal]) -> None:
    label = amount_format(case[0])
    assert set(label) & set("012345678") == set()


@pytest.mark.parametrize(
    ("text", "label"),
    [
        ("60.000", "9.999"),
        ("1.380.000", "9.999"),
        ("24,000", "9,999"),
        ("1,000.50", "9,999.99"),
        ("47619.00", "9.99"),
        ("Rp 12.000", "Rp 9.999"),
        ("@880", "@9"),
    ],
)
def test_format_labels(text: str, label: str) -> None:
    assert amount_format(text) == label
