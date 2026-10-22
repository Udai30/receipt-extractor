"""The one place receipt amounts are parsed (CLAUDE.md §5: money is ``Decimal``, never ``float``).

Receipts print money in several conventions, often on the same dataset:

==================  ==========================  ==================
Printed             Convention                  Parsed
==================  ==========================  ==================
``60.000``          Indonesian thousands (dot)  ``60000``
``24,000``          comma thousands             ``24000``
``1,000.50``        comma thousands, dot cents  ``1000.50``
``9.500,00``        dot thousands, comma cents  ``9500.00``
``2 600``           space thousands             ``2600``
``47619.00``        plain, dot cents            ``47619.00``
``Rp. 12.000``      currency prefix             ``12000``
``@20,000``         unit-price marker           ``20000``
``-7.200`` ``(5)``  negative / accounting       ``-7200`` ``-5``
``""`` ``-``        blank                       ``None``
==================  ==========================  ==================

Separator rule: when both ``.`` and ``,`` occur, the last one is the decimal separator. When only
one kind occurs, it is a thousands separator if it occurs more than once or is followed by
exactly three digits, otherwise a decimal separator. Spaces are only ever thousands separators
(``1 000,50``). Decimals have one or two digits; digit groups after the first have exactly three.
So ``1.000`` is one thousand — the CORD (Indonesian) reading; a three-decimal amount such as
``1.500`` meaning 1.5 cannot be expressed, which no IDR receipt needs. Anything else (``1e5``,
``12,3456``, ``1.000.00``) is an :class:`AmountParseError`, never a guess.
"""

from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation
from typing import Final

__all__ = ["AmountParseError", "amount_format", "parse_amount"]

MAX_AMOUNT_CHARS: Final = 64
"""Longer input is rejected before any regex runs (bounded work on hostile model output)."""

# Currency codes and symbols seen on receipts; longest first so "Rp." wins over "Rp".
_CURRENCY: Final = r"(?:IDR|USD|SGD|EUR|RP\.?|S\$|US\$|\$|€|£|¥)"
_PREFIX_RE: Final = re.compile(rf"^(?:{_CURRENCY}|@|\*|=|:)\s*", re.IGNORECASE)
_SUFFIX_RE: Final = re.compile(rf"\s*(?:{_CURRENCY}|,-|\.-)$", re.IGNORECASE)
_BLANKS: Final = frozenset({"", "-", "--", "—", "–"})  # noqa: RUF001 - em/en dash on purpose

_DIGITS_RE: Final = re.compile(r"^\d+$")
_NUMBER_CHARS_RE: Final = re.compile(r"^\d(?:[\d., ]*\d)?$")
_FRACTION_RE: Final = re.compile(r"^\d{1,2}$")
_GROUPED_RE: Final = re.compile(r"^\d{1,3}(?:[., ]\d{3})+$")


class AmountParseError(ValueError):
    """The text is not a monetary amount (e.g. ``100%``, ``abc``, ``1.2.3,4,5``).

    The message never echoes the input beyond its length, since it may be model output.
    """


def parse_amount(text: str | None) -> Decimal | None:
    """Parse a printed amount into a :class:`~decimal.Decimal`; ``None`` for a blank.

    Raises :class:`AmountParseError` for anything that is neither blank nor an amount.
    The result keeps the printed precision (``"0.00"`` → ``Decimal("0.00")``).
    """
    if text is None:
        return None
    if len(text) > MAX_AMOUNT_CHARS:
        raise AmountParseError(f"amount text is too long ({len(text)} characters)")
    body = text.strip()
    if body in _BLANKS:
        return None

    negative, body = _strip_sign_and_wrappers(body)
    body = _strip_affixes(body)
    if body.startswith("-"):  # "Rp -5.000": sign after the currency prefix
        negative = not negative
        body = body[1:].lstrip()
    elif body.endswith("-"):  # "7.200-": trailing minus on POS discount lines
        negative = not negative
        body = body[:-1].rstrip()
    if body in _BLANKS:
        return None

    number = _normalise_number(body)
    try:
        value = Decimal(number)
    except InvalidOperation:
        raise AmountParseError("amount text is not a number") from None
    return -value if negative and value else value  # no "-0" for "(0)"


def amount_format(text: str) -> str:
    """A short label of the printed convention, for dataset statistics.

    Digits become ``9``, the leading digit run collapses to one ``9`` and repeated thousands
    groups to one, so ``"Rp 12.000"`` and ``"Rp 1.380.000"`` share ``"Rp 9.999"`` while
    ``"47619.00"`` is ``"9.99"``; the label shows prefix and separators, never the value.
    """
    shape = re.sub(r"\d", "9", text.strip()[:MAX_AMOUNT_CHARS])
    shape = re.sub(r"9+", "9", shape, count=1)
    return re.sub(r"(?P<sep>[., ])999(?:(?P=sep)999)+", r"\g<sep>999", shape)


def _strip_sign_and_wrappers(body: str) -> tuple[bool, str]:
    negative = False
    while True:
        if len(body) > 1 and body[0] in "([" and body[-1] == {"(": ")", "[": "]"}[body[0]]:
            negative ^= body[0] == "("  # accounting negative; brackets are decoration
            body = body[1:-1].strip()
        elif body.startswith(("-", "−")):  # noqa: RUF001 - ASCII or Unicode minus
            negative = not negative
            body = body[1:].lstrip()
        elif body.startswith("+"):
            body = body[1:].lstrip()
        else:
            return negative, body


def _strip_affixes(body: str) -> str:
    previous = None
    while previous != body:
        previous = body
        body = _PREFIX_RE.sub("", body, count=1)
        body = _SUFFIX_RE.sub("", body, count=1) if len(body) > 1 else body
    return body


def _normalise_number(body: str) -> str:
    """Rewrite ``body`` as a plain ``1234.56`` string or raise :class:`AmountParseError`."""
    if not _NUMBER_CHARS_RE.match(body):
        raise AmountParseError("amount text is not a number")
    if _DIGITS_RE.match(body):
        return body

    integer, fraction = body, ""
    last = max(body.rfind("."), body.rfind(","))
    if last != -1:
        tail = body[last + 1 :]
        both_kinds = "." in body and "," in body
        single = body.count(body[last]) == 1
        # One "." or "," followed by exactly three digits is a thousands group ("60.000").
        if both_kinds or (single and len(tail) != 3):  # noqa: PLR2004
            integer, fraction = body[:last], tail
    if fraction and not _FRACTION_RE.match(fraction):
        raise AmountParseError("amount text has a malformed decimal part")
    integer = _integer_part(integer)
    return f"{integer}.{fraction}" if fraction else integer


def _integer_part(text: str) -> str:
    if _DIGITS_RE.match(text):
        return text
    if not _GROUPED_RE.match(text) or len({c for c in text if not c.isdigit()}) != 1:
        raise AmountParseError("amount text has malformed digit groups")
    return re.sub(r"\D", "", text)
