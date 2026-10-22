"""The single output contract of every pipeline (BACKEND_SCHEMA §1.1, CLAUDE.md §5).

Money is :class:`~decimal.Decimal` and serialises as a JSON string (BACKEND_SCHEMA §S1), so a
round trip through ``model_dump_json`` / ``model_validate_json`` is exact. Amount fields accept
``Decimal``/``int`` values or printed text, which goes through :func:`parse_amount` — the one
place number formats are interpreted. A float (a JSON number with a fraction, e.g. from a
model reply) is converted through its shortest ``repr`` — ``45000.5`` becomes
``Decimal("45000.5")``, never the binary expansion — and is never used for arithmetic.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Annotated, Any, Final

from pydantic import (
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Field,
    PlainSerializer,
    field_validator,
)

from receipt_extractor.core.amounts import AmountParseError, parse_amount

__all__ = ["DEFAULT_CURRENCY", "MAX_ITEMS", "Item", "Money", "OptionalMoney", "Receipt"]

DEFAULT_CURRENCY: Final = "IDR"
MAX_ITEMS: Final = 500
MAX_NAME_CHARS: Final = 300
MAX_ABS_AMOUNT: Final = Decimal("1e15")


def _to_decimal(value: Any) -> Decimal | None:
    if value is None or isinstance(value, Decimal):
        decimal = value
    elif isinstance(value, bool):
        raise ValueError("amount must be a number or text, not a boolean")
    elif isinstance(value, int):
        decimal = Decimal(value)
    elif isinstance(value, str):
        try:
            decimal = parse_amount(value)
        except AmountParseError as exc:
            raise ValueError(str(exc)) from None
    elif isinstance(value, float):
        decimal = Decimal(repr(value))
    else:
        raise ValueError("amount must be a number or text")
    if decimal is not None and (not decimal.is_finite() or abs(decimal) >= MAX_ABS_AMOUNT):
        raise ValueError("amount must be finite and below 10^15")
    return decimal


def _serialise(value: Decimal | None) -> str | None:
    return None if value is None else str(value)


OptionalMoney = Annotated[
    Decimal | None,
    BeforeValidator(_to_decimal),
    PlainSerializer(_serialise, return_type=str | None, when_used="json"),
]
Money = Annotated[
    Decimal,
    BeforeValidator(_to_decimal),
    PlainSerializer(_serialise, return_type=str, when_used="json"),
]


class Item(BaseModel):
    """One line item. ``price`` is the line total as printed; ``qty``/``unit_price`` if shown."""

    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)

    name: str = Field(min_length=1, max_length=MAX_NAME_CHARS)
    qty: OptionalMoney = None
    unit_price: OptionalMoney = None
    price: Money


class Receipt(BaseModel):
    """A normalised receipt: line items plus the printed totals."""

    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)

    items: list[Item] = Field(default_factory=list, max_length=MAX_ITEMS)
    subtotal: OptionalMoney = None
    tax: OptionalMoney = None
    total: Money
    currency: str = Field(default=DEFAULT_CURRENCY, pattern=r"^[A-Z]{3}$")

    @field_validator("currency", mode="before")
    @classmethod
    def _upper_currency(cls, value: Any) -> Any:
        return value.strip().upper() if isinstance(value, str) else value
