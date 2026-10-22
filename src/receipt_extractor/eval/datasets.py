"""Case sources for the runner: a dataset lists its case ids and loads one case at a time.

Loading by id (instead of materialising every case) keeps runner memory proportional to its
concurrency. Two kinds exist so far:

- ``synthetic`` — deterministic toy cases generated from a seed (no files); used by the M0.4
  acceptance run and tests.
- ``jsonl`` — a file under ``data/`` with one ``{"case_id", "input", "gold"}`` object per line,
  indexed by byte offset in one streaming pass.

- ``cord`` — a CORD-v2 split prepared by ``cli data prepare`` (M1.1): input is the image path
  (project-relative) and its SHA-256, gold is the normalised ``Receipt``. Only receipts with gold
  are cases; unmappable ones are listed by ``cli data stats``.
"""

from __future__ import annotations

import hashlib
import json
import random
from pathlib import Path
from typing import Annotated, Any, Final, Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from receipt_extractor.eval.errors import DatasetError
from receipt_extractor.eval.io import MAX_LINE_BYTES, canonical_json
from receipt_extractor.eval.paths import DATA_DIR, confine, relative_posix

NAME_PATTERN: Final = r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$"
CASE_ID_PATTERN: Final = r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,255}$"
MAX_CASES: Final = 1_000_000
SYNTHETIC_FORMAT_VERSION: Final = 1

Name = Annotated[str, Field(pattern=NAME_PATTERN)]
CaseId = Annotated[str, Field(pattern=CASE_ID_PATTERN)]


class Case(BaseModel):
    """One evaluation case. ``input`` and ``gold`` are JSON-compatible values."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    case_id: CaseId
    input: Any
    gold: Any


class SyntheticDatasetSpec(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    kind: Literal["synthetic"]
    name: Name = "toy"
    split: Name = "synthetic"
    size: int = Field(ge=1, le=100_000)
    seed: int = 0


class JSONLDatasetSpec(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    kind: Literal["jsonl"]
    name: Name
    split: Name
    revision: str | None = Field(default=None, max_length=128)
    path: str = Field(min_length=1, max_length=1024)


class CordDatasetSpec(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    kind: Literal["cord"]
    name: Name = "cord-v2"
    split: Literal["train", "validation", "test"]
    path: str = Field(default="data/cord", min_length=1, max_length=1024)


DatasetSpec = Annotated[
    SyntheticDatasetSpec | JSONLDatasetSpec | CordDatasetSpec, Field(discriminator="kind")
]
AnyDatasetSpec = SyntheticDatasetSpec | JSONLDatasetSpec | CordDatasetSpec


class Dataset(Protocol):
    name: str
    split: str
    revision: str | None

    def manifest_hash(self) -> str:
        """SHA-256 identifying the dataset content; part of the run id."""
        ...

    def case_ids(self) -> list[str]:
        """Every case id, in dataset order."""
        ...

    def load_case(self, case_id: str) -> Case:
        """Load one case; raises :class:`DatasetError` for an unknown id."""
        ...


class SyntheticDataset:
    """``size`` cases ``<name>-00000…``: input ``{"a", "b"}``, gold ``{"sum": a + b}``."""

    revision: str | None = None

    def __init__(self, spec: SyntheticDatasetSpec) -> None:
        self.spec = spec
        self.name = spec.name
        self.split = spec.split
        self._ids = [f"{spec.name}-{i:05d}" for i in range(spec.size)]
        self._index = {case_id: i for i, case_id in enumerate(self._ids)}

    def manifest_hash(self) -> str:
        payload = {"generator": SYNTHETIC_FORMAT_VERSION, **self.spec.model_dump(mode="json")}
        return hashlib.sha256(canonical_json(payload).encode("utf-8")).hexdigest()

    def case_ids(self) -> list[str]:
        return list(self._ids)

    def load_case(self, case_id: str) -> Case:
        index = self._index.get(case_id)
        if index is None:
            raise DatasetError(f"case {case_id!r} is not in dataset {self.name}/{self.split}")
        rng = random.Random(f"{self.spec.seed}:{index}")  # noqa: S311 - toy data, not crypto
        a, b = rng.randint(0, 999), rng.randint(0, 999)
        return Case(case_id=case_id, input={"a": a, "b": b}, gold={"sum": a + b})


class JSONLDataset:
    """Cases from a JSONL file under ``data/``; the file's SHA-256 is its manifest hash."""

    def __init__(self, spec: JSONLDatasetSpec, path: Path) -> None:
        self.name = spec.name
        self.split = spec.split
        self.revision = spec.revision
        self.path = path
        self._offsets: dict[str, int] = {}
        self._hash = self._index()

    def _index(self) -> str:
        """One streaming pass: hash the bytes and record each case id's line offset."""
        digest = hashlib.sha256()
        try:
            handle = self.path.open("rb")
        except OSError:
            raise DatasetError(f"dataset file {self.path.name} cannot be opened") from None
        with handle:
            lineno = 0
            while True:
                offset = handle.tell()
                line = handle.readline(MAX_LINE_BYTES + 1)
                if not line:
                    break
                lineno += 1
                digest.update(line)
                if len(line) > MAX_LINE_BYTES:
                    raise DatasetError(f"{self.path.name}:{lineno}: line too long")
                if not line.strip():
                    continue
                case_id = _parse_line(line, self.path.name, lineno).case_id
                if case_id in self._offsets:
                    raise DatasetError(f"{self.path.name}:{lineno}: duplicate case id {case_id!r}")
                if len(self._offsets) >= MAX_CASES:
                    raise DatasetError(f"{self.path.name}: more than {MAX_CASES} cases")
                self._offsets[case_id] = offset
        if not self._offsets:
            raise DatasetError(f"dataset file {self.path.name} has no cases")
        return digest.hexdigest()

    def manifest_hash(self) -> str:
        return self._hash

    def case_ids(self) -> list[str]:
        return list(self._offsets)

    def load_case(self, case_id: str) -> Case:
        offset = self._offsets.get(case_id)
        if offset is None:
            raise DatasetError(f"case {case_id!r} is not in dataset {self.name}/{self.split}")
        with self.path.open("rb") as handle:
            handle.seek(offset)
            line = handle.readline(MAX_LINE_BYTES + 1)
        case = _parse_line(line, self.path.name, None)
        if case.case_id != case_id:
            raise DatasetError(f"dataset file {self.path.name} changed while the run was active")
        return case


class CordDataset:
    """A prepared CORD split; its manifest's per-split hash is the dataset manifest hash."""

    def __init__(self, spec: CordDatasetSpec, directory: Path, root: Path) -> None:
        from receipt_extractor.data.cord import load_manifest  # noqa: PLC0415 - avoid a cycle

        self.name = spec.name
        self.split: str = spec.split
        self.directory = directory
        self._root = root
        self._manifest = load_manifest(directory)
        self.revision: str | None = self._manifest.get("revision")
        entry = self._manifest["splits"].get(spec.split)
        if entry is None:
            raise DatasetError(
                f"CORD split {spec.split!r} is not prepared; "
                f"run `receipt_extractor data prepare --split {spec.split}`"
            )
        self._hash: str = entry["hash"]
        unmappable = {item["id"] for item in entry["unmappable"]}
        self._ids = [case_id for case_id in entry["ids"] if case_id not in unmappable]

    def manifest_hash(self) -> str:
        return self._hash

    def case_ids(self) -> list[str]:
        return list(self._ids)

    def load_case(self, case_id: str) -> Case:
        from receipt_extractor.data.cord import read_gold  # noqa: PLC0415

        if case_id not in self._ids:
            raise DatasetError(f"case {case_id!r} is not in dataset {self.name}/{self.split}")
        gold = read_gold(self.directory, self._manifest, case_id)
        case = self._manifest["cases"][case_id]
        image = confine(case["image"], self.directory, ["images"])
        return Case(
            case_id=case_id,
            input={
                "image": relative_posix(image, self._root),
                "image_sha256": case["image_sha256"],
            },
            gold=gold.model_dump(mode="json"),
        )


def _parse_line(line: bytes, filename: str, lineno: int | None) -> Case:
    where = f"{filename}:{lineno}" if lineno is not None else filename
    try:
        return Case.model_validate(json.loads(line))
    except ValueError:  # JSONDecodeError and pydantic's ValidationError
        raise DatasetError(f"{where}: expected {{case_id, input, gold}}") from None


def open_dataset(spec: AnyDatasetSpec, root: Path) -> Dataset:
    """Open the dataset a config names; ``jsonl`` and ``cord`` paths are confined to ``data/``."""
    if isinstance(spec, SyntheticDatasetSpec):
        return SyntheticDataset(spec)
    path = confine(spec.path, root, [DATA_DIR])
    if isinstance(spec, CordDatasetSpec):
        if not path.is_dir():
            raise DatasetError(
                f"CORD directory {relative_posix(path, root)} does not exist; "
                "run `receipt_extractor data prepare`"
            )
        return CordDataset(spec, path, root)
    if not path.is_file():
        raise DatasetError(f"dataset file {relative_posix(path, root)} does not exist")
    return JSONLDataset(spec, path)


def describe_validation_error(error: ValidationError, source: str) -> str:
    """Field locations and reasons only — never the offending input values."""
    problems = []
    for item in error.errors(include_input=False, include_url=False)[:10]:
        loc = ".".join(str(part) for part in item["loc"]) or "<root>"
        problems.append(f"{loc}: {item['msg']}")
    return f"{source} is invalid — " + "; ".join(problems)
