"""Sample receipts for the Try it tab: CORD images with the stored A and B results (FR-8).

Sample receipts show **cached** results only: B never runs live on the Space (Gemini's free tier
and RPD), and the samples stay available when live budgets run out (TRD §S7). The bundle is
built from run artefacts by ``receipt_extractor ui build-showcase``, so every number on a sample
traces to a run id; nothing is typed by hand. CORD-v2 is CC-BY-4.0 (attributed in the app).

Bundle layout (package data, shipped with the Space)::

    ui/assets/showcase/showcase.json   Showcase: cases → image file, gold, per-variant result
    ui/assets/showcase/<case_id>.jpg   display copy (long side ≤ 1,024 px, metadata stripped)
"""

from __future__ import annotations

import json
import re
from collections.abc import Sequence
from datetime import datetime
from pathlib import Path
from typing import Any, Final, Literal

from pydantic import BaseModel, ConfigDict, Field

from receipt_extractor.core.extraction import ExtractionResult
from receipt_extractor.core.receipt import Receipt
from receipt_extractor.eval.io import iter_jsonl, read_json_object
from receipt_extractor.eval.records import MANIFEST_FILENAME, RESULTS_FILENAME, Manifest, ResultRow
from receipt_extractor.pipelines.images import ModelImageSettings, prepare_model_image

__all__ = [
    "DEFAULT_SAMPLE_IDS",
    "SHOWCASE_DIR",
    "Showcase",
    "ShowcaseCase",
    "build_showcase",
    "load_showcase",
]

SHOWCASE_DIR: Final = Path(__file__).resolve().parent / "assets" / "showcase"
SHOWCASE_FILE: Final = "showcase.json"
# Validation receipts (never test: the demo must not leak test data into tuning).
DEFAULT_SAMPLE_IDS: Final = (
    "validation-0005",
    "validation-0033",
    "validation-0038",
    "validation-0049",
    "validation-0051",
    "validation-0053",
)
DEFAULT_VARIANTS: Final = ("A", "B")
DISPLAY = ModelImageSettings(max_side=1024, format="jpeg", jpeg_quality=85)
_CASE_ID = re.compile(r"^[a-z]+-\d{4}$")


class VariantResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    run_id: str = Field(min_length=1, max_length=128)
    prediction: ExtractionResult
    ocr_text: str | None = Field(default=None, max_length=16_000)


class ShowcaseCase(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    case_id: str = Field(pattern=_CASE_ID.pattern)
    image: str = Field(pattern=r"^[a-z]+-\d{4}\.jpg$")
    gold: Receipt
    variants: dict[str, VariantResult]


class Showcase(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    format_version: Literal[1] = 1
    dataset: str = "naver-clova-ix/cord-v2 (CC-BY-4.0)"
    cases: tuple[ShowcaseCase, ...]

    def get(self, case_id: str) -> ShowcaseCase | None:
        return next((c for c in self.cases if c.case_id == case_id), None)


def load_showcase(directory: Path = SHOWCASE_DIR) -> Showcase | None:
    """The bundled samples, or ``None`` when none are bundled (the tab then says so)."""
    path = directory / SHOWCASE_FILE
    if not path.is_file():
        return None
    return Showcase.model_validate_json(path.read_text(encoding="utf-8"))


def _rows_by_variant(runs_dir: Path, variants: Sequence[str]) -> dict[str, dict[str, Any]]:
    """For each variant, case id → (run id, row) from its runs, newest run first (paused ok)."""
    runs: list[tuple[datetime, str, Path, Manifest]] = []
    for run_dir in runs_dir.iterdir() if runs_dir.is_dir() else ():
        manifest_path = run_dir / MANIFEST_FILENAME
        if not manifest_path.is_file():
            continue
        manifest = Manifest.model_validate(read_json_object(manifest_path))
        variant = manifest.resolved_config.get("variant")
        if variant in variants:
            runs.append((manifest.started_at, str(variant), run_dir, manifest))
    found: dict[str, dict[str, Any]] = {v: {} for v in variants}
    for _, variant, run_dir, manifest in sorted(runs, key=lambda r: r[0], reverse=True):
        results = run_dir / RESULTS_FILENAME
        if not results.is_file():
            continue
        for _, obj in iter_jsonl(results):
            row = ResultRow.model_validate(obj)
            if row.status != "error" and row.case_id not in found[variant]:
                found[variant][row.case_id] = (manifest.run_id, row)
    return found


def build_showcase(
    root: Path,
    *,
    case_ids: Sequence[str] = DEFAULT_SAMPLE_IDS,
    variants: Sequence[str] = DEFAULT_VARIANTS,
    out_dir: Path = SHOWCASE_DIR,
) -> Showcase:
    """Copy the samples' images (display size) and their stored results into the bundle."""
    from receipt_extractor.data.cord import load_manifest, read_gold  # noqa: PLC0415

    cord_dir = root / "data" / "cord"
    cord = load_manifest(cord_dir)
    found = _rows_by_variant(root / "runs", variants)
    out_dir.mkdir(parents=True, exist_ok=True)
    cases: list[ShowcaseCase] = []
    for case_id in case_ids:
        if not _CASE_ID.fullmatch(case_id) or case_id.startswith("test-"):
            raise ValueError(f"{case_id!r}: samples must be validation receipts")
        missing = [v for v in variants if case_id not in found[v]]
        if missing:
            raise ValueError(f"{case_id}: no stored result for {', '.join(missing)}")
        entry = cord["cases"][case_id]
        image = (cord_dir / entry["image"]).read_bytes()
        display = prepare_model_image(image, DISPLAY)
        (out_dir / f"{case_id}.jpg").write_bytes(display.data)
        per_variant: dict[str, VariantResult] = {}
        for variant in variants:
            run_id, row = found[variant][case_id]
            per_variant[variant] = VariantResult(
                run_id=run_id,
                prediction=ExtractionResult.model_validate(row.prediction),
                ocr_text=row.details.get("ocr_text"),
            )
        cases.append(
            ShowcaseCase(
                case_id=case_id,
                image=f"{case_id}.jpg",
                gold=read_gold(cord_dir, cord, case_id),
                variants=per_variant,
            )
        )
    showcase = Showcase(cases=tuple(cases))
    (out_dir / SHOWCASE_FILE).write_text(
        json.dumps(showcase.model_dump(mode="json"), indent=1, ensure_ascii=False) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    return showcase
