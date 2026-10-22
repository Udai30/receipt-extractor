"""The one receipt scorer every variant is judged by (PRD §5, FR-7, TRD §4.4).

``score_receipt(prediction, gold)`` compares a prediction with a gold :class:`Receipt` in their
stored JSON forms (``results.jsonl``), so run-time and imported predictions score identically.

- **Prediction form.** An ``ExtractionResult`` object (its ``receipt`` key; BACKEND_SCHEMA §1.2)
  or a bare ``Receipt`` object. Anything that does not validate as a ``Receipt`` — ``null``,
  malformed JSON values, wrong types — is an *invalid* prediction: ``valid_json = 0`` and it is
  scored as an empty receipt (no items, no amounts). Scoring never raises on a prediction; a gold
  value that is not a ``Receipt`` raises :class:`ValueError` (a dataset bug).
- **Scalar fields** (``subtotal``, ``tax``, ``total``): exact match after normalisation — both
  sides go through ``Receipt`` validation, so ``"Rp 1.000"``, ``1000`` and ``"1000.00"`` are the
  same ``Decimal``. Per field: equal → TP; a wrong value → FP + FN; only predicted → FP; only
  in gold → FN; absent on both sides → not applicable (``None``).
- **Items**: an optimal one-to-one assignment (Hungarian, ``scipy.optimize.linear_sum_assignment``)
  between predicted and gold items. A pair is a match (TP) when the normalised names are similar
  (:data:`NAME_MATCH_THRESHOLD`, normalised Indel similarity) **and** the prices are equal. The
  assignment maximises, in this order, matches, name-only agreements, price-only agreements, and
  total name similarity — so item order never matters, and a split or merged line costs exactly
  the lines that no longer agree. Precision = TP / predicted items, recall = TP / gold items
  (0 when the other side has items but this one has none; ``None`` when both are empty).
- **Field F1** (the primary metric): micro F1 over the receipt's items and scalar fields
  (TP/FP/FN summed), so each receipt gets one number; the summary averages it over receipts.
- **Reconcile**: whether the *prediction* adds up (:func:`receipt_extractor.core.reconcile`);
  not a comparison with gold, so gold scored against itself reconciles only as often as the
  printed receipts do.

Item ``qty``/``unit_price`` and ``currency`` are not scored (PRD §5 names items, subtotal, tax and
total).
"""

from __future__ import annotations

import functools
import json
import unicodedata
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Final

import numpy as np
from pydantic import ValidationError
from rapidfuzz import fuzz, process
from scipy.optimize import linear_sum_assignment

from receipt_extractor.core.receipt import Item, Receipt
from receipt_extractor.core.reconcile import reconcile
from receipt_extractor.eval.io import canonical_json
from receipt_extractor.eval.metrics import register_metric

__all__ = [
    "NAME_MATCH_THRESHOLD",
    "SCALAR_FIELDS",
    "FieldScore",
    "ItemMatching",
    "ItemPair",
    "ItemScore",
    "ReceiptMetric",
    "ReceiptScore",
    "coerce_prediction",
    "match_items",
    "name_similarity",
    "normalise_name",
    "score_receipt",
]

NAME_MATCH_THRESHOLD: Final = 0.8
"""Minimum normalised Indel similarity of two item names to count as the same item."""
SCALAR_FIELDS: Final = ("subtotal", "tax", "total")
_SIMILARITY_DIGITS: Final = 4
_CACHE_SIZE: Final = 8


# --- Items -----------------------------------------------------------------------------------


def normalise_name(name: str) -> str:
    """NFKC, case-folded, whitespace collapsed: ``" Es  TEH "`` → ``"es teh"``."""
    return " ".join(unicodedata.normalize("NFKC", name).casefold().split())


def name_similarity(a: str, b: str) -> float:
    """Normalised Indel similarity in [0, 1] of the normalised names."""
    return fuzz.ratio(normalise_name(a), normalise_name(b)) / 100.0


@dataclass(frozen=True)
class ItemPair:
    gold: int
    pred: int
    name_similarity: float
    name_ok: bool
    price_ok: bool

    @property
    def match(self) -> bool:
        return self.name_ok and self.price_ok


@dataclass(frozen=True)
class ItemMatching:
    """Assigned pairs that agree on the name or the price; every other item is unmatched."""

    pairs: tuple[ItemPair, ...]
    unmatched_gold: tuple[int, ...]
    unmatched_pred: tuple[int, ...]

    @property
    def true_positives(self) -> int:
        return sum(pair.match for pair in self.pairs)


def match_items(pred: Sequence[Item], gold: Sequence[Item]) -> ItemMatching:
    """Optimal one-to-one item assignment; independent of the order of either list."""
    if not pred or not gold:
        return ItemMatching((), tuple(range(len(gold))), tuple(range(len(pred))))

    similarity = (
        process.cdist(
            [normalise_name(item.name) for item in pred],
            [normalise_name(item.name) for item in gold],
            scorer=fuzz.ratio,
            dtype=np.float64,
        )
        / 100.0
    )
    price_ids: dict[Decimal, int] = {}  # equal Decimals (1000 == 1000.00) share an id
    pred_prices = np.array([price_ids.setdefault(i.price, len(price_ids)) for i in pred])
    gold_prices = np.array([price_ids.setdefault(i.price, len(price_ids)) for i in gold])
    price_ok = pred_prices[:, None] == gold_prices[None, :]
    name_ok = similarity >= NAME_MATCH_THRESHOLD

    # Lexicographic objective as one weight: each level outweighs everything below it summed
    # over a full assignment (at most `n` pairs, similarity ≤ 1 each).
    level = float(max(len(pred), len(gold)) + 1)
    weight = (name_ok & price_ok) * level**3 + name_ok * level**2 + price_ok * level + similarity
    rows, cols = linear_sum_assignment(weight, maximize=True)

    pairs: list[ItemPair] = []
    for p, g in zip(rows.tolist(), cols.tolist(), strict=True):
        if name_ok[p, g] or price_ok[p, g]:
            pairs.append(
                ItemPair(
                    gold=g,
                    pred=p,
                    name_similarity=round(float(similarity[p, g]), _SIMILARITY_DIGITS),
                    name_ok=bool(name_ok[p, g]),
                    price_ok=bool(price_ok[p, g]),
                )
            )
    pairs.sort(key=lambda pair: pair.gold)
    paired_gold = {pair.gold for pair in pairs}
    paired_pred = {pair.pred for pair in pairs}
    return ItemMatching(
        pairs=tuple(pairs),
        unmatched_gold=tuple(i for i in range(len(gold)) if i not in paired_gold),
        unmatched_pred=tuple(i for i in range(len(pred)) if i not in paired_pred),
    )


# --- Scores ----------------------------------------------------------------------------------


def _f1(tp: int, fp: int, fn: int) -> float | None:
    denominator = 2 * tp + fp + fn
    return None if denominator == 0 else 2 * tp / denominator


@dataclass(frozen=True)
class FieldScore:
    """One scalar field of one receipt; ``f1`` is ``None`` when absent on both sides."""

    tp: int
    fp: int
    fn: int

    @property
    def ok(self) -> bool:
        return self.fp == 0 and self.fn == 0

    @property
    def f1(self) -> float | None:
        return _f1(self.tp, self.fp, self.fn)

    @classmethod
    def compare(cls, pred: Decimal | None, gold: Decimal | None) -> FieldScore:
        if pred is None or gold is None:
            return cls(tp=0, fp=int(pred is not None), fn=int(gold is not None))
        if pred == gold:
            return cls(tp=1, fp=0, fn=0)
        return cls(tp=0, fp=1, fn=1)


@dataclass(frozen=True)
class ItemScore:
    tp: int
    n_pred: int
    n_gold: int

    @property
    def precision(self) -> float | None:
        if self.n_pred:
            return self.tp / self.n_pred
        return 0.0 if self.n_gold else None

    @property
    def recall(self) -> float | None:
        if self.n_gold:
            return self.tp / self.n_gold
        return 0.0 if self.n_pred else None

    @property
    def f1(self) -> float | None:
        return _f1(self.tp, self.n_pred - self.tp, self.n_gold - self.tp)


@dataclass(frozen=True)
class ReceiptScore:
    valid: bool
    reconciled: bool
    items: ItemScore
    matching: ItemMatching
    subtotal: FieldScore
    tax: FieldScore
    total: FieldScore

    def field(self, name: str) -> FieldScore:
        if name not in SCALAR_FIELDS:
            raise KeyError(name)
        score: FieldScore = getattr(self, name)
        return score

    @property
    def field_f1(self) -> float | None:
        """Micro F1 over items and scalar fields of this receipt."""
        fields = [self.field(name) for name in SCALAR_FIELDS]
        tp = self.items.tp + sum(f.tp for f in fields)
        fp = (self.items.n_pred - self.items.tp) + sum(f.fp for f in fields)
        fn = (self.items.n_gold - self.items.tp) + sum(f.fn for f in fields)
        return _f1(tp, fp, fn)

    def details(self) -> dict[str, Any]:
        """``ResultRow.details`` entries (BACKEND_SCHEMA §1.3)."""
        return {
            "field_scores": {
                "items_p": self.items.precision,
                "items_r": self.items.recall,
                "items_f1": self.items.f1,
                **{f"{name}_ok": self.field(name).ok for name in SCALAR_FIELDS},
                "field_f1": self.field_f1,
                "valid_json": self.valid,
                "reconciled": self.reconciled,
            },
            "item_matches": {
                "pairs": [
                    {
                        "gold": pair.gold,
                        "pred": pair.pred,
                        "name_similarity": pair.name_similarity,
                        "name_ok": pair.name_ok,
                        "price_ok": pair.price_ok,
                        "match": pair.match,
                    }
                    for pair in self.matching.pairs
                ],
                "unmatched_gold": list(self.matching.unmatched_gold),
                "unmatched_pred": list(self.matching.unmatched_pred),
            },
        }


def coerce_prediction(prediction: Any) -> Receipt | None:
    """The prediction's ``Receipt``, or ``None`` when it has none or it does not validate."""
    candidate = prediction
    if isinstance(prediction, Mapping) and "receipt" in prediction:
        candidate = prediction["receipt"]
    if candidate is None or isinstance(candidate, Receipt):
        return candidate
    try:
        return Receipt.model_validate(candidate)
    except ValidationError:
        return None


def _coerce_gold(gold: Any) -> Receipt:
    if isinstance(gold, Receipt):
        return gold
    try:
        return Receipt.model_validate(gold)
    except ValidationError:
        raise ValueError("gold is not a valid Receipt") from None


def _amount(receipt: Receipt | None, name: str) -> Decimal | None:
    value: Decimal | None = None if receipt is None else getattr(receipt, name)
    return value


def score_receipt(prediction: Any, gold: Any) -> ReceiptScore:
    gold_receipt = _coerce_gold(gold)
    receipt = coerce_prediction(prediction)
    pred_items: Sequence[Item] = receipt.items if receipt is not None else ()
    matching = match_items(pred_items, gold_receipt.items)
    return ReceiptScore(
        valid=receipt is not None,
        reconciled=receipt is not None and reconcile(receipt).ok,
        items=ItemScore(
            tp=matching.true_positives, n_pred=len(pred_items), n_gold=len(gold_receipt.items)
        ),
        matching=matching,
        subtotal=FieldScore.compare(_amount(receipt, "subtotal"), gold_receipt.subtotal),
        tax=FieldScore.compare(_amount(receipt, "tax"), gold_receipt.tax),
        total=FieldScore.compare(_amount(receipt, "total"), gold_receipt.total),
    )


@functools.lru_cache(maxsize=_CACHE_SIZE)
def _score_json(prediction_json: str, gold_json: str) -> ReceiptScore:
    return score_receipt(json.loads(prediction_json), json.loads(gold_json))


def _cached_score(prediction: Any, gold: Any) -> ReceiptScore:
    """Score once per case although every receipt metric asks (keys: canonical JSON)."""
    return _score_json(canonical_json(prediction), canonical_json(gold))


# --- Registry metrics ------------------------------------------------------------------------


@dataclass(frozen=True)
class ReceiptMetric:
    """A registry metric that reads one number off the case's :class:`ReceiptScore`."""

    name: str
    select: Callable[[ReceiptScore], float | None]
    bootstrap_ci: bool = True

    def compute(self, prediction: Any, gold: Any) -> float | None:
        return self.select(_cached_score(prediction, gold))

    def explain(self, prediction: Any, gold: Any) -> dict[str, Any]:
        return _cached_score(prediction, gold).details()


def _scalar_f1(name: str) -> Callable[[ReceiptScore], float | None]:
    return lambda score: score.field(name).f1


for _metric in (
    ReceiptMetric("field_f1", lambda s: s.field_f1),
    ReceiptMetric("items_precision", lambda s: s.items.precision),
    ReceiptMetric("items_recall", lambda s: s.items.recall),
    ReceiptMetric("items_f1", lambda s: s.items.f1),
    *(ReceiptMetric(f"{name}_f1", _scalar_f1(name)) for name in SCALAR_FIELDS),
    ReceiptMetric("valid_json", lambda s: float(s.valid)),
    ReceiptMetric("reconcile", lambda s: float(s.reconciled)),
):
    register_metric(_metric)
