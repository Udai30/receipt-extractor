"""Map one CORD-v2 ``gt_parse`` record onto :class:`~receipt_extractor.core.receipt.Receipt`.

Pure and deterministic: no I/O, the same record always gives the same receipt, notes and issue.
The rules (``MAPPING_VERSION``; BACKEND_SCHEMA §1.1, "CORD gold mapping"):

- **Items** — every ``menu`` entry (a dict or a list of dicts) becomes an :class:`Item`:
  ``nm`` → name, ``cnt`` → qty (``2``, ``2x``, ``x2``, ``1 X``), ``unitprice`` → unit price,
  ``price`` → price. Without ``price`` the line total is taken from ``itemsubtotal``, else
  ``unitprice x cnt``; an entry with no derivable price is a modifier line and is dropped.
- **Sub-items** — ``sub`` entries with a printed price (``0`` included) are add-on lines and follow
  their parent as items; ``sub`` entries without a price (``Ice``, ``Less sugar``) are modifiers
  and are dropped, as are priced ``sub`` entries without a name (a repeated price line).
- **Totals** — ``sub_total.subtotal_price`` → subtotal, ``sub_total.tax_price`` → tax (several
  printed tax lines are summed), ``total.total_price`` → total. Without ``total_price`` the
  total is derived, in order, from ``total_etc``, ``cashprice - changeprice``,
  ``creditcardprice``, ``emoneyprice`` (the amount paid).
- **Unmappable** — a receipt is reported (``issue``), not guessed, when the total cannot be found
  or derived, a used amount is ambiguous or unparseable, or an item has a price but no name.
- Every derivation and drop is recorded in ``notes`` so gold provenance stays auditable.
"""

from __future__ import annotations

import re
from collections import Counter
from collections.abc import Mapping
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any, Final

from pydantic import ValidationError

from receipt_extractor.core.amounts import AmountParseError, amount_format, parse_amount
from receipt_extractor.core.receipt import DEFAULT_CURRENCY, MAX_NAME_CHARS, Item, Receipt

__all__ = ["MAPPING_VERSION", "MappingResult", "UnmappableError", "map_cord_gold"]

MAPPING_VERSION: Final = 1

_PAID_FALLBACKS: Final = ("creditcardprice", "emoneyprice")
# "2", "2x", "x2", "1 X", "*2", "1Prs", "1Gls", "2.00", "(2": a count with markers or a unit.
_QTY_RE: Final = re.compile(r"^\(?[xX*]?\s*(\d+(?:[.,]\d+)?)\s*(?:[xX]|[A-Za-z]{1,5}\.?)?\)?$")


class UnmappableError(ValueError):
    """The record cannot be turned into a trustworthy gold receipt; the message says why."""


@dataclass(frozen=True)
class MappingResult:
    """The mapped receipt (``None`` when unmappable) and how it was obtained."""

    receipt: Receipt | None
    issue: str | None = None
    notes: tuple[str, ...] = ()
    amount_formats: Counter[str] = field(default_factory=Counter)


@dataclass
class _Context:
    notes: list[str] = field(default_factory=list)
    formats: Counter[str] = field(default_factory=Counter)


def map_cord_gold(gt_parse: Any) -> MappingResult:
    """Map a ``gt_parse`` object. Never raises for bad data: problems become ``issue``."""
    ctx = _Context()
    try:
        if not isinstance(gt_parse, Mapping):
            raise UnmappableError("gt_parse is not an object")
        items = _map_items(gt_parse.get("menu"), ctx)
        sub_total = _section(gt_parse, "sub_total")
        total_section = _section(gt_parse, "total")
        receipt = Receipt(
            items=items,
            subtotal=_optional_amount(sub_total, "subtotal_price", ctx),
            tax=_tax(sub_total, ctx),
            total=_total(total_section, ctx),
            currency=DEFAULT_CURRENCY,
        )
    except UnmappableError as exc:
        return MappingResult(None, str(exc), tuple(ctx.notes), ctx.formats)
    except ValidationError as exc:
        first = exc.errors(include_input=False, include_url=False)[0]
        loc = ".".join(str(part) for part in first["loc"])
        return MappingResult(None, f"invalid receipt: {loc}: {first['msg']}", tuple(ctx.notes))
    return MappingResult(receipt, None, tuple(ctx.notes), ctx.formats)


def _section(gt_parse: Mapping[str, Any], key: str) -> Mapping[str, Any]:
    value = gt_parse.get(key)
    if value is None:
        return {}
    if not isinstance(value, Mapping):
        raise UnmappableError(f"{key} is not an object")
    return value


def _entries(value: Any, where: str) -> list[Mapping[str, Any]]:
    if value is None:
        return []
    entries = value if isinstance(value, list) else [value]
    if not all(isinstance(entry, Mapping) for entry in entries):
        raise UnmappableError(f"{where} holds a non-object entry")
    return entries


def _map_items(menu: Any, ctx: _Context) -> list[Item]:
    items: list[Item] = []
    for index, entry in enumerate(_entries(menu, "menu")):
        parent = _map_entry(entry, f"menu[{index}]", ctx)
        if parent is not None:
            items.append(parent)
        for sub_index, sub in enumerate(_entries(entry.get("sub"), f"menu[{index}].sub")):
            where = f"menu[{index}].sub[{sub_index}]"
            if _text(sub, "price", where) is None:
                ctx.notes.append(f"{where}: modifier without price dropped")
                continue
            if _text(sub, "nm", where) is None:
                ctx.notes.append(f"{where}: price line without name dropped")
                continue
            item = _map_entry(sub, where, ctx)
            if item is not None:
                items.append(item)
    return items


def _map_entry(entry: Mapping[str, Any], where: str, ctx: _Context) -> Item | None:
    qty = _quantity(entry, where, ctx)
    unit_price = _amount(entry, "unitprice", where, ctx, required=False)
    price = _amount(entry, "price", where, ctx, required=True)
    if price is None:
        price = _amount(entry, "itemsubtotal", where, ctx, required=True)
        if price is not None:
            ctx.notes.append(f"{where}: price from itemsubtotal")
    if price is None and unit_price is not None and qty is not None:
        price = unit_price * qty
        ctx.notes.append(f"{where}: price = unitprice x cnt")
    if price is None:
        ctx.notes.append(f"{where}: entry without price dropped")
        return None
    name = _text(entry, "nm", where)
    if not name:
        raise UnmappableError(f"{where}: priced entry without a name")
    if len(name) > MAX_NAME_CHARS:
        raise UnmappableError(f"{where}: name longer than {MAX_NAME_CHARS} characters")
    return Item(name=name, qty=qty, unit_price=unit_price, price=price)


def _total(section: Mapping[str, Any], ctx: _Context) -> Decimal:
    total = _amount(section, "total_price", "total", ctx, required=True)
    if total is not None:
        return total
    total = _amount(section, "total_etc", "total", ctx, required=True)
    if total is not None:
        ctx.notes.append("total from total_etc")
        return total
    cash = _amount(section, "cashprice", "total", ctx, required=False, ambiguous_ok=True)
    if cash is not None:
        change = _amount(section, "changeprice", "total", ctx, required=False, ambiguous_ok=True)
        ctx.notes.append("total = cashprice - changeprice" if change else "total from cashprice")
        return cash - (change or Decimal(0))
    for key in _PAID_FALLBACKS:
        total = _amount(section, key, "total", ctx, required=True)
        if total is not None:
            ctx.notes.append(f"total from {key}")
            return total
    raise UnmappableError("no total_price and no paid amount to derive it from")


def _tax(section: Mapping[str, Any], ctx: _Context) -> Decimal | None:
    """``tax_price``; several printed tax lines (``["6,818", "0"]``) add up."""
    raw = section.get("tax_price")
    if not isinstance(raw, list):
        return _optional_amount(section, "tax_price", ctx)
    values = [_parse_or_none(v, "tax_price", "sub_total", ctx, required=False) for v in raw]
    present = [value for value in values if value is not None]
    if not present:
        return None
    ctx.notes.append("tax = sum of several tax_price values")
    return sum(present, Decimal(0))


def _optional_amount(section: Mapping[str, Any], key: str, ctx: _Context) -> Decimal | None:
    return _amount(section, key, "sub_total", ctx, required=False)


def _amount(
    section: Mapping[str, Any],
    key: str,
    where: str,
    ctx: _Context,
    *,
    required: bool,
    ambiguous_ok: bool = False,
) -> Decimal | None:
    """Parse ``section[key]``. A bad required amount is unmappable; a bad optional one → None."""
    raw = section.get(key)
    if isinstance(raw, list):
        values = {_parse_or_none(v, key, where, ctx, required=required) for v in raw}
        values.discard(None)
        if len(values) == 1:
            return values.pop()
        if not values:
            return None
        if ambiguous_ok:
            ctx.notes.append(f"{where}.{key}: several different values, ignored")
            return None
        raise UnmappableError(f"{where}.{key}: several different values")
    return _parse_or_none(raw, key, where, ctx, required=required)


def _parse_or_none(
    raw: Any, key: str, where: str, ctx: _Context, *, required: bool
) -> Decimal | None:
    if raw is None:
        return None
    if not isinstance(raw, str):
        if required:
            raise UnmappableError(f"{where}.{key}: not text")
        ctx.notes.append(f"{where}.{key}: not text, ignored")
        return None
    try:
        value = parse_amount(raw)
    except AmountParseError:
        if required:
            raise UnmappableError(f"{where}.{key}: unparseable amount") from None
        ctx.notes.append(f"{where}.{key}: unparseable amount ignored")
        return None
    if value is not None:
        ctx.formats[amount_format(raw)] += 1
    return value


def _quantity(entry: Mapping[str, Any], where: str, ctx: _Context) -> Decimal | None:
    raw = _text(entry, "cnt", where)
    if raw is None:
        return None
    match = _QTY_RE.match(raw)
    try:
        qty = parse_amount(match.group(1)) if match else None
    except AmountParseError:
        qty = None
    if qty is None or qty < 0:
        ctx.notes.append(f"{where}.cnt: unparseable quantity ignored")
        return None
    return qty


def _text(entry: Mapping[str, Any], key: str, where: str) -> str | None:
    """A string field; lists of strings (split OCR words) are joined with spaces."""
    value = entry.get(key)
    if value is None:
        return None
    if isinstance(value, list) and all(isinstance(part, str) for part in value):
        value = " ".join(part.strip() for part in value)
    if not isinstance(value, str):
        raise UnmappableError(f"{where}.{key}: not text")
    return value.strip() or None
