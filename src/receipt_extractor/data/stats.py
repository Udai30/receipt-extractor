"""``cli data stats``: what a prepared CORD split looks like, computed from the files on disk.

Items per receipt come from the gold files (each verified against the manifest SHA-256); amount
formats, mapping notes and unmappable receipts come from the manifest written by ``data prepare``.
"""

from __future__ import annotations

import statistics
from collections import Counter
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import Any

from receipt_extractor.data.cord import load_manifest, read_gold
from receipt_extractor.eval.errors import DatasetError

__all__ = ["SplitStats", "split_stats"]


@dataclass(frozen=True)
class SplitStats:
    split: str
    revision: str
    license: str
    n_rows: int
    n_gold: int
    unmappable: list[dict[str, str]]
    items_min: int
    items_median: float
    items_mean: float
    items_max: int
    items_histogram: dict[str, int]
    n_with_subtotal: int
    n_with_tax: int
    n_reconciled: int
    amount_formats: dict[str, int]
    mapping_notes: dict[str, int]


def split_stats(directory: Path, split: str) -> SplitStats:
    manifest = load_manifest(directory)
    entry: dict[str, Any] | None = manifest["splits"].get(split)
    if entry is None:
        prepared = ", ".join(sorted(manifest["splits"])) or "none"
        raise DatasetError(
            f"split {split!r} is not prepared (prepared: {prepared}); "
            f"run `receipt_extractor data prepare --split {split}`"
        )
    unmappable_ids = {u["id"] for u in entry["unmappable"]}
    counts: list[int] = []
    with_subtotal = with_tax = reconciled = 0
    for case_id in entry["ids"]:
        if case_id in unmappable_ids:
            continue
        receipt = read_gold(directory, manifest, case_id)
        counts.append(len(receipt.items))
        with_subtotal += receipt.subtotal is not None
        with_tax += receipt.tax is not None
        items_sum = sum((item.price for item in receipt.items), Decimal(0))
        reconciled += items_sum in {receipt.subtotal, receipt.total}
    histogram = Counter(_bucket(n) for n in counts)
    return SplitStats(
        split=split,
        revision=manifest["revision"],
        license=manifest["license"],
        n_rows=entry["n_rows"],
        n_gold=entry["n_gold"],
        unmappable=list(entry["unmappable"]),
        items_min=min(counts, default=0),
        items_median=float(statistics.median(counts)) if counts else 0.0,
        items_mean=statistics.fmean(counts) if counts else 0.0,
        items_max=max(counts, default=0),
        items_histogram={key: histogram[key] for key in _BUCKETS if histogram[key]},
        n_with_subtotal=with_subtotal,
        n_with_tax=with_tax,
        n_reconciled=reconciled,
        amount_formats=dict(entry["amount_formats"]),
        mapping_notes=dict(entry["mapping_notes"]),
    )


_BUCKETS = ("0", "1", "2", "3", "4", "5", "6-10", "11-20", ">20")


def _bucket(n: int) -> str:
    if n <= 5:  # noqa: PLR2004
        return str(n)
    if n <= 10:  # noqa: PLR2004
        return "6-10"
    return "11-20" if n <= 20 else ">20"  # noqa: PLR2004
