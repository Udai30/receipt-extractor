"""CORD-v2 download, normalisation to gold :class:`Receipt` files, and the dataset manifest.

``prepare_cord`` (``cli data prepare``) fetches the pinned revision's parquet files into
``data/cord/raw/``, checks each file's size and SHA-256 against the values pinned here, then
streams the rows and writes, per receipt (BACKEND_SCHEMA §2):

    data/cord/images/<case_id>.<png|jpg>   the receipt image, bytes as published
    data/cord/gold/<case_id>.json          the normalised gold Receipt (mapped receipts only)
    data/cord/splits/<split>.txt           the split's case ids, one per line
    data/cord/manifest.json                revision, licence, files, ids, mapping report, hashes

Case ids are ``<split>-<image_id:04d>`` (``test-0000`` … ``test-0099``). Datasets are downloaded
only by this explicit command (CLAUDE.md §3), never at import time or app start.
"""

from __future__ import annotations

import hashlib
import json
from collections import Counter
from collections.abc import Callable, Iterable, Iterator, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final, Literal

from receipt_extractor.core.receipt import Receipt
from receipt_extractor.data.cord_mapping import MAPPING_VERSION, map_cord_gold
from receipt_extractor.eval.errors import DatasetError
from receipt_extractor.eval.io import (
    MAX_JSON_FILE_BYTES,
    atomic_write_bytes,
    atomic_write_text,
    canonical_json,
    sha256_file,
)
from receipt_extractor.eval.paths import DATA_DIR, confine, relative_posix

__all__ = [
    "CORD_DIR",
    "CORD_FILES",
    "CORD_LICENSE",
    "CORD_REPO",
    "CORD_REVISION",
    "DEFAULT_SPLITS",
    "EXPECTED_ROWS",
    "MANIFEST_FILENAME",
    "PrepareResult",
    "SourceFile",
    "SplitName",
    "case_id_for",
    "load_manifest",
    "prepare_cord",
    "read_gold",
]

SplitName = Literal["train", "validation", "test"]

CORD_REPO: Final = "naver-clova-ix/cord-v2"
CORD_REVISION: Final = "7f0115a4b758a71d6473b8d085751692da2fef98"
"""Commit of the dataset repo (last modified 2022-07-19), checked 2026-10-22."""
CORD_LICENSE: Final = "CC-BY-4.0"
"""From the dataset card front matter (``license: cc-by-4.0``) at :data:`CORD_REVISION`."""
CORD_DIR: Final = "data/cord"
MANIFEST_FILENAME: Final = "manifest.json"
MANIFEST_FORMAT_VERSION: Final = 1
EXPECTED_ROWS: Final[dict[str, int]] = {"train": 800, "validation": 100, "test": 100}
DEFAULT_SPLITS: Final[tuple[SplitName, ...]] = ("validation", "test")

MAX_GROUND_TRUTH_BYTES: Final = 4 * 1024 * 1024
MAX_IMAGE_BYTES: Final = 32 * 1024 * 1024
MAX_IMAGE_ID: Final = 9_999  # case ids are <split>-<4 digits>
_BATCH_ROWS: Final = 8  # rows per parquet batch: ~2-3 MB images each, bounded memory
_META_SPLIT: Final = {"validation": ("valid", "dev")}  # meta.split spellings in the records


@dataclass(frozen=True)
class SourceFile:
    """One pinned parquet file of the dataset repo (sizes and LFS SHA-256 from the Hub API)."""

    split: SplitName
    path: str
    size: int
    sha256: str


CORD_FILES: Final[tuple[SourceFile, ...]] = (
    SourceFile(
        "train",
        "data/train-00000-of-00004-b4aaeceff1d90ecb.parquet",
        490_224_630,
        "da3994eee1bf9bd3c57f0d53a72c3a6812c8696c5ba26245987949ddf73483cc",
    ),
    SourceFile(
        "train",
        "data/train-00001-of-00004-7dbbe248962764c5.parquet",
        441_418_432,
        "cce4def16a0d6a6c75f80be712f7494c56c318a8829b712f5c62650155c9e58e",
    ),
    SourceFile(
        "train",
        "data/train-00002-of-00004-688fe1305a55e5cc.parquet",
        443_802_181,
        "591e2db8fe8b1d364b054f46e8c375b7f00e72578914ba46a573b6858162cab2",
    ),
    SourceFile(
        "train",
        "data/train-00003-of-00004-2d0cd200555ed7fd.parquet",
        455_555_434,
        "1ffd9de8d6fbcee7630fd4cdfedff05b9b7fabc0fae4fc17557c5fe7cf178748",
    ),
    SourceFile(
        "validation",
        "data/validation-00000-of-00001-cc3c5779fe22e8ca.parquet",
        242_080_800,
        "0d0f6dac11fdcc549de2746aa9f53136a3bc22a2a1aff2b0b847f7622ad60c15",
    ),
    SourceFile(
        "test",
        "data/test-00000-of-00001-9c204eb3f4e11791.parquet",
        234_202_795,
        "51c65f1788faff392abe2a0b55b023eb23e9be551c509138eaa3a832514224e7",
    ),
)

Downloader = Callable[[SourceFile, Path], Path]
"""``(file, raw_dir) -> local path``: fetches ``file.path`` at :data:`CORD_REVISION`."""
Progress = Callable[[str], None]


@dataclass(frozen=True)
class PrepareResult:
    manifest_path: Path
    splits: dict[str, dict[str, Any]]


def case_id_for(split: str, image_id: int) -> str:
    return f"{split}-{image_id:04d}"


def hf_downloader(token: str | None = None) -> Downloader:
    """The real downloader: ``huggingface_hub`` at the pinned revision (imported lazily)."""

    def download(source: SourceFile, raw_dir: Path) -> Path:
        from huggingface_hub import hf_hub_download  # noqa: PLC0415 - heavy, only when used

        try:
            return Path(
                hf_hub_download(
                    CORD_REPO,
                    source.path,
                    repo_type="dataset",
                    revision=CORD_REVISION,
                    local_dir=raw_dir,
                    token=token,
                )
            )
        except Exception as exc:  # network, HTTP and disk errors from the Hub client
            # The type name only: Hub error messages can embed request headers or URLs.
            raise DatasetError(
                f"download of {source.path} failed ({type(exc).__name__}); "
                "check the network connection and retry"
            ) from None

    return download


def prepare_cord(
    root: Path,
    *,
    splits: Sequence[SplitName] = DEFAULT_SPLITS,
    out_dir: str = CORD_DIR,
    downloader: Downloader | None = None,
    progress: Progress | None = None,
) -> PrepareResult:
    """Download (or reuse verified) parquet files and write images, gold and the manifest.

    Re-running is idempotent: verified files are not downloaded again and outputs are rewritten
    atomically with identical content. Other splits already in the manifest are kept.
    """
    out = confine(out_dir, root, [DATA_DIR])
    download = downloader or hf_downloader()
    say = progress or (lambda _message: None)
    unknown = sorted(set(splits) - set(EXPECTED_ROWS))
    if unknown or not splits:
        raise DatasetError(f"unknown or empty split selection: {', '.join(unknown) or '(none)'}")

    manifest = _existing_manifest(out)
    for split in dict.fromkeys(splits):  # de-duplicate, keep order
        files = [source for source in CORD_FILES if source.split == split]
        paths = [_fetch(source, out / "raw", download, say) for source in files]
        say(f"{split}: normalising {EXPECTED_ROWS[split]} receipts")
        entry, cases = _prepare_split(split, files, paths, out)
        manifest["splits"][split] = entry
        manifest["cases"] = {
            case_id: case
            for case_id, case in manifest["cases"].items()
            if not case_id.startswith(f"{split}-")
        } | cases
        atomic_write_text(out / "splits" / f"{split}.txt", "".join(f"{i}\n" for i in entry["ids"]))
        say(
            f"{split}: {entry['n_rows']} rows, {entry['n_gold']} gold, "
            f"{len(entry['unmappable'])} unmappable"
        )

    manifest["hash"] = _hash({k: v for k, v in manifest.items() if k != "hash"})
    path = out / MANIFEST_FILENAME
    atomic_write_text(path, json.dumps(manifest, indent=2, ensure_ascii=False) + "\n")
    return PrepareResult(path, dict(manifest["splits"]))


def load_manifest(directory: Path) -> dict[str, Any]:
    """Read and sanity-check ``manifest.json`` in a prepared CORD directory."""
    path = directory / MANIFEST_FILENAME
    if not path.is_file():
        raise DatasetError(
            f"{path.name} not found in the CORD directory; "
            "run `receipt_extractor data prepare` first"
        )
    if path.stat().st_size > MAX_JSON_FILE_BYTES:
        raise DatasetError(f"{path.name} is too large")
    try:
        manifest = json.loads(path.read_bytes())
    except ValueError:
        raise DatasetError(f"{path.name} is not valid JSON") from None
    if (
        not isinstance(manifest, dict)
        or manifest.get("format_version") != MANIFEST_FORMAT_VERSION
        or not isinstance(manifest.get("splits"), dict)
        or not isinstance(manifest.get("cases"), dict)
    ):
        raise DatasetError(f"{path.name} has an unexpected format; re-run `data prepare`")
    return manifest


def _existing_manifest(out: Path) -> dict[str, Any]:
    fresh: dict[str, Any] = {
        "format_version": MANIFEST_FORMAT_VERSION,
        "dataset": CORD_REPO,
        "revision": CORD_REVISION,
        "license": CORD_LICENSE,
        "mapping_version": MAPPING_VERSION,
        "splits": {},
        "cases": {},
    }
    if not (out / MANIFEST_FILENAME).is_file():
        return fresh
    try:
        old = load_manifest(out)
    except DatasetError:
        return fresh
    same = all(old.get(key) == fresh[key] for key in ("dataset", "revision", "mapping_version"))
    if not same:
        return fresh  # another revision or mapping: earlier splits must be prepared again
    fresh["splits"], fresh["cases"] = old["splits"], old["cases"]
    return fresh


def _fetch(source: SourceFile, raw_dir: Path, download: Downloader, say: Progress) -> Path:
    local = raw_dir / source.path
    if local.is_file() and _verified(local, source):
        say(f"{source.path}: already downloaded and verified")
        return local
    say(f"{source.path}: downloading {source.size / 1e6:.0f} MB")
    path = download(source, raw_dir)
    if not _verified(path, source):
        path.unlink(missing_ok=True)
        raise DatasetError(
            f"{source.path} does not match the pinned size/SHA-256; the file was removed"
        )
    return path


def _verified(path: Path, source: SourceFile) -> bool:
    return path.stat().st_size == source.size and sha256_file(path) == source.sha256


def _prepare_split(
    split: str, files: Sequence[SourceFile], paths: Sequence[Path], out: Path
) -> tuple[dict[str, Any], dict[str, dict[str, Any]]]:
    images_dir, gold_dir = out / "images", out / "gold"
    ids: list[str] = []
    cases: dict[str, dict[str, Any]] = {}
    unmappable: list[dict[str, str]] = []
    formats: Counter[str] = Counter()
    notes: Counter[str] = Counter()
    for row_index, (image, ground_truth) in enumerate(_rows(paths)):
        gt = _parse_ground_truth(ground_truth, split, row_index)
        case_id = case_id_for(split, _image_id(gt, split, row_index))
        if case_id in cases:
            raise DatasetError(f"{split}: duplicate image id in row {row_index}")
        ext = _image_extension(image, case_id)
        image_path = images_dir / f"{case_id}.{ext}"
        atomic_write_bytes(image_path, image)
        _remove_other_images(images_dir, case_id, keep=image_path)

        result = map_cord_gold(gt.get("gt_parse"))
        formats.update(result.amount_formats)
        notes.update(_note_kind(note) for note in result.notes)
        gold_path = gold_dir / f"{case_id}.json"
        gold_sha: str | None = None
        if result.receipt is None:
            gold_path.unlink(missing_ok=True)
            unmappable.append({"id": case_id, "reason": result.issue or "unknown"})
        else:
            gold_bytes = (result.receipt.model_dump_json(indent=2) + "\n").encode("utf-8")
            atomic_write_bytes(gold_path, gold_bytes)
            gold_sha = hashlib.sha256(gold_bytes).hexdigest()
        ids.append(case_id)
        cases[case_id] = {
            "image": relative_posix(image_path, out),
            "image_sha256": hashlib.sha256(image).hexdigest(),
            "gold": relative_posix(gold_path, out) if gold_sha else None,
            "gold_sha256": gold_sha,
            "notes": list(result.notes),
        }

    expected = EXPECTED_ROWS[split]
    if len(ids) != expected:
        raise DatasetError(f"{split}: {len(ids)} rows, expected {expected} at the pinned revision")
    entry: dict[str, Any] = {
        "n_rows": len(ids),
        "n_gold": len(ids) - len(unmappable),
        "ids": ids,
        "files": [{"path": s.path, "size": s.size, "sha256": s.sha256} for s in files],
        "unmappable": unmappable,
        "amount_formats": dict(formats.most_common()),
        "mapping_notes": dict(notes.most_common()),
    }
    entry["hash"] = _hash(
        {
            "dataset": CORD_REPO,
            "revision": CORD_REVISION,
            "mapping_version": MAPPING_VERSION,
            "ids": ids,
            "cases": cases,
        }
    )
    return entry, cases


def _rows(paths: Iterable[Path]) -> Iterator[tuple[bytes, str]]:
    """Stream ``(image bytes, ground_truth json)`` over the split's parquet files in order."""
    import pyarrow.parquet as pq  # noqa: PLC0415 - heavy, only when preparing

    for path in paths:
        parquet = pq.ParquetFile(path)
        names = set(parquet.schema_arrow.names)
        if not {"image", "ground_truth"} <= names:
            raise DatasetError(f"{path.name}: expected columns image and ground_truth")
        for batch in parquet.iter_batches(
            batch_size=_BATCH_ROWS, columns=["image", "ground_truth"]
        ):
            for image, ground_truth in zip(
                batch.column("image").to_pylist(),
                batch.column("ground_truth").to_pylist(),
                strict=True,
            ):
                data = image.get("bytes") if isinstance(image, dict) else None
                if not isinstance(data, bytes) or not isinstance(ground_truth, str):
                    raise DatasetError(f"{path.name}: a row lacks image bytes or ground truth")
                yield data, ground_truth


def _parse_ground_truth(text: str, split: str, row: int) -> dict[str, Any]:
    if len(text) > MAX_GROUND_TRUTH_BYTES:
        raise DatasetError(f"{split} row {row}: ground truth is too large")
    try:
        value = json.loads(text)
    except ValueError:
        raise DatasetError(f"{split} row {row}: ground truth is not JSON") from None
    if not isinstance(value, dict):
        raise DatasetError(f"{split} row {row}: ground truth is not an object")
    meta = value.get("meta")
    if isinstance(meta, dict) and meta.get("split") not in (
        None,
        split,
        *_META_SPLIT.get(split, ()),
    ):
        raise DatasetError(f"{split} row {row}: record belongs to split {meta.get('split')!r}")
    return value


def _image_id(gt: dict[str, Any], split: str, row: int) -> int:
    meta = gt.get("meta")
    image_id = meta.get("image_id") if isinstance(meta, dict) else None
    if image_id is None:
        return row
    if (
        not isinstance(image_id, int)
        or isinstance(image_id, bool)
        or not 0 <= image_id <= MAX_IMAGE_ID
    ):
        raise DatasetError(f"{split} row {row}: meta.image_id is not a small integer")
    return image_id


def _image_extension(data: bytes, case_id: str) -> str:
    if len(data) > MAX_IMAGE_BYTES:
        raise DatasetError(f"{case_id}: image larger than {MAX_IMAGE_BYTES} bytes")
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "png"
    if data.startswith(b"\xff\xd8\xff"):
        return "jpg"
    raise DatasetError(f"{case_id}: image is neither PNG nor JPEG")


def _remove_other_images(images_dir: Path, case_id: str, keep: Path) -> None:
    for other in images_dir.glob(f"{case_id}.*"):
        if other != keep and other.suffix in {".png", ".jpg"}:
            other.unlink(missing_ok=True)


def _note_kind(note: str) -> str:
    """``menu[3].sub[0]: modifier without price dropped`` → ``modifier without price dropped``."""
    return note.split(": ", 1)[1] if ": " in note and note.startswith("menu[") else note


def _hash(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def read_gold(directory: Path, manifest: dict[str, Any], case_id: str) -> Receipt:
    """Load one gold receipt, checking it against the SHA-256 recorded in the manifest."""
    case = manifest["cases"].get(case_id)
    if not isinstance(case, dict) or not case.get("gold"):
        raise DatasetError(f"case {case_id!r} has no gold receipt")
    path = confine(case["gold"], directory, ["gold"])
    try:
        data = path.read_bytes()
    except OSError:
        raise DatasetError(f"gold file for {case_id} cannot be read") from None
    if hashlib.sha256(data).hexdigest() != case.get("gold_sha256"):
        raise DatasetError(f"gold file for {case_id} changed since `data prepare`; re-run it")
    return Receipt.model_validate_json(data)
