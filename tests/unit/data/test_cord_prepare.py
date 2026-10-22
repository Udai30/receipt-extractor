"""``data prepare``, ``data stats`` and the ``cord`` dataset kind on small synthetic parquet files.

No network: the downloader is a fake that writes a parquet file shaped like CORD-v2's
(``image: struct<bytes, path>``, ``ground_truth: string``), and the pinned file list and expected
row counts are patched to match it.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pyarrow as pa
import pyarrow.parquet as pq
import pytest
from typer.testing import CliRunner

from receipt_extractor.cli import app
from receipt_extractor.data import cord
from receipt_extractor.data.cord import SourceFile, load_manifest, prepare_cord, read_gold
from receipt_extractor.data.stats import split_stats
from receipt_extractor.eval.datasets import CordDatasetSpec, open_dataset
from receipt_extractor.eval.errors import DatasetError, PathConfinementError

PNG = b"\x89PNG\r\n\x1a\n" + b"fake-png-body"
JPEG = b"\xff\xd8\xff\xe0" + b"fake-jpeg-body"
runner = CliRunner()


def gt(image_id: int, split: str = "test", **gt_parse: Any) -> dict[str, Any]:
    parse = gt_parse or {
        "menu": [{"nm": "Nasi", "cnt": "2", "price": "30.000"}, {"nm": "Teh", "price": "5.000"}],
        "sub_total": {"subtotal_price": "35.000", "tax_price": "3.500"},
        "total": {"total_price": "38.500"},
    }
    return {"gt_parse": parse, "meta": {"split": split, "image_id": image_id}}


def build_parquet(path: Path, rows: list[tuple[bytes, dict[str, Any] | str]]) -> None:
    images = [{"bytes": image, "path": None} for image, _ in rows]
    truths = [g if isinstance(g, str) else json.dumps(g) for _, g in rows]
    table = pa.table(
        {
            "image": pa.array(images, pa.struct([("bytes", pa.binary()), ("path", pa.string())])),
            "ground_truth": pa.array(truths, pa.string()),
        }
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    pq.write_table(table, path)


class FakeHub:
    """Serves prebuilt parquet files by repo path; counts downloads; can corrupt one."""

    def __init__(self, store: Path) -> None:
        self.store = store
        self.calls: list[str] = []
        self.corrupt = False

    def __call__(self, source: SourceFile, raw_dir: Path) -> Path:
        self.calls.append(source.path)
        target = raw_dir / source.path
        target.parent.mkdir(parents=True, exist_ok=True)
        data = (self.store / source.path).read_bytes()
        target.write_bytes(data + b"tampered" if self.corrupt else data)
        return target


Publish = Callable[..., FakeHub]


@pytest.fixture
def publish(
    project: Path, monkeypatch: pytest.MonkeyPatch, tmp_path_factory: pytest.TempPathFactory
) -> Publish:
    """Build parquet files per split and pin them in place of the real CORD file list."""
    store = tmp_path_factory.mktemp("hub")

    def make(**splits: list[tuple[bytes, dict[str, Any] | str]]) -> FakeHub:
        files = []
        for split, rows in splits.items():
            path = f"data/{split}-00000-of-00001.parquet"
            build_parquet(store / path, rows)
            data = (store / path).read_bytes()
            files.append(SourceFile(split, path, len(data), hashlib.sha256(data).hexdigest()))  # type: ignore[arg-type]
            monkeypatch.setitem(cord.EXPECTED_ROWS, split, len(rows))
        monkeypatch.setattr(cord, "CORD_FILES", tuple(files))
        return FakeHub(store)

    return make


def three_test_rows() -> list[tuple[bytes, dict[str, Any] | str]]:
    return [
        (PNG, gt(0)),
        (
            JPEG,
            gt(
                1,
                menu={"nm": "Kopi", "price": "19,000"},
                total={"cashprice": "20,000", "changeprice": "1,000"},
            ),
        ),
        (PNG, gt(2, menu={"nm": "X", "price": "1"}, total={"changeprice": "0"})),  # no total
    ]


def test_prepare_writes_images_gold_split_ids_and_manifest(project: Path, publish: Publish) -> None:
    hub = publish(test=three_test_rows())
    result = prepare_cord(project, splits=["test"], downloader=hub)
    out = project / "data" / "cord"
    assert result.manifest_path == out / "manifest.json"

    assert sorted(p.name for p in (out / "images").iterdir()) == [
        "test-0000.png",
        "test-0001.jpg",
        "test-0002.png",
    ]
    assert (out / "images" / "test-0001.jpg").read_bytes() == JPEG
    assert sorted(p.name for p in (out / "gold").iterdir()) == ["test-0000.json", "test-0001.json"]
    assert (out / "splits" / "test.txt").read_text() == "test-0000\ntest-0001\ntest-0002\n"

    manifest = load_manifest(out)
    assert manifest["dataset"] == "naver-clova-ix/cord-v2"
    assert manifest["revision"] == cord.CORD_REVISION
    assert manifest["license"] == "CC-BY-4.0"
    split = manifest["splits"]["test"]
    assert (split["n_rows"], split["n_gold"]) == (3, 2)
    assert split["ids"] == ["test-0000", "test-0001", "test-0002"]
    assert split["unmappable"] == [
        {"id": "test-0002", "reason": "no total_price and no paid amount to derive it from"}
    ]
    assert split["mapping_notes"] == {"total = cashprice - changeprice": 1}
    assert split["amount_formats"]["9.999"] == 5
    assert manifest["cases"]["test-0001"]["image_sha256"] == hashlib.sha256(JPEG).hexdigest()
    assert manifest["cases"]["test-0002"]["gold"] is None

    gold = read_gold(out, manifest, "test-0000")
    assert gold.model_dump(mode="json") == {
        "items": [
            {"name": "Nasi", "qty": "2", "unit_price": None, "price": "30000"},
            {"name": "Teh", "qty": None, "unit_price": None, "price": "5000"},
        ],
        "subtotal": "35000",
        "tax": "3500",
        "total": "38500",
        "currency": "IDR",
    }
    assert read_gold(out, manifest, "test-0001").total == 19000


def test_rerun_reuses_verified_files_and_is_identical(project: Path, publish: Publish) -> None:
    hub = publish(test=three_test_rows())
    first = prepare_cord(project, splits=["test"], downloader=hub).manifest_path.read_bytes()
    second = prepare_cord(project, splits=["test"], downloader=hub).manifest_path.read_bytes()
    assert hub.calls == ["data/test-00000-of-00001.parquet"]  # downloaded once
    assert first == second


def test_preparing_another_split_keeps_the_first(project: Path, publish: Publish) -> None:
    hub = publish(test=three_test_rows(), validation=[(PNG, gt(0, split="valid"))])
    prepare_cord(project, splits=["test"], downloader=hub)
    prepare_cord(project, splits=["validation"], downloader=hub)
    manifest = load_manifest(project / "data" / "cord")
    assert set(manifest["splits"]) == {"test", "validation"}
    assert manifest["splits"]["validation"]["ids"] == ["validation-0000"]
    assert {"test-0000", "validation-0000"} <= set(manifest["cases"])


def test_a_download_not_matching_the_pin_is_removed(project: Path, publish: Publish) -> None:
    hub = publish(test=three_test_rows())
    hub.corrupt = True
    with pytest.raises(DatasetError, match="does not match the pinned size/SHA-256"):
        prepare_cord(project, splits=["test"], downloader=hub)
    assert not (project / "data/cord/raw/data/test-00000-of-00001.parquet").exists()
    assert not (project / "data/cord/manifest.json").exists()


@pytest.mark.parametrize(
    ("rows", "message"),
    [
        ([(PNG, gt(0)), (PNG, gt(0))], "duplicate image id"),
        ([(b"GIF89a", gt(0))], "neither PNG nor JPEG"),
        ([(PNG, gt(0, split="train"))], "belongs to split 'train'"),
        ([(PNG, "not json")], "ground truth is not JSON"),
        ([(PNG, "[1]")], "ground truth is not an object"),
        ([(PNG, {"gt_parse": {}, "meta": {"image_id": "7"}})], "image_id is not a small integer"),
    ],
)
def test_malformed_rows_are_rejected(
    project: Path, publish: Publish, rows: list[tuple[bytes, Any]], message: str
) -> None:
    hub = publish(test=rows)
    with pytest.raises(DatasetError, match=message):
        prepare_cord(project, splits=["test"], downloader=hub)


def test_row_count_must_match_the_pinned_split_size(
    project: Path, publish: Publish, monkeypatch: pytest.MonkeyPatch
) -> None:
    hub = publish(test=three_test_rows())
    monkeypatch.setitem(cord.EXPECTED_ROWS, "test", 100)
    with pytest.raises(DatasetError, match="3 rows, expected 100"):
        prepare_cord(project, splits=["test"], downloader=hub)


def test_output_directory_is_confined_to_data(project: Path, publish: Publish) -> None:
    hub = publish(test=three_test_rows())
    with pytest.raises(PathConfinementError):
        prepare_cord(project, splits=["test"], out_dir="../elsewhere", downloader=hub)
    with pytest.raises(DatasetError, match="unknown or empty split"):
        prepare_cord(project, splits=["dev"], downloader=hub)  # type: ignore[list-item]


def test_stats(project: Path, publish: Publish) -> None:
    prepare_cord(project, splits=["test"], downloader=publish(test=three_test_rows()))
    stats = split_stats(project / "data" / "cord", "test")
    assert (stats.n_rows, stats.n_gold, len(stats.unmappable)) == (3, 2, 1)
    assert (stats.items_min, stats.items_median, stats.items_max) == (1, 1.5, 2)
    assert stats.items_histogram == {"1": 1, "2": 1}
    assert (stats.n_with_subtotal, stats.n_with_tax, stats.n_reconciled) == (1, 1, 2)
    with pytest.raises(DatasetError, match="split 'train' is not prepared"):
        split_stats(project / "data" / "cord", "train")


def test_cord_dataset_serves_mapped_cases_and_detects_tampering(
    project: Path, publish: Publish
) -> None:
    prepare_cord(project, splits=["test"], downloader=publish(test=three_test_rows()))
    dataset = open_dataset(CordDatasetSpec(kind="cord", split="test"), project)
    assert dataset.case_ids() == ["test-0000", "test-0001"]  # the unmappable one is not a case
    assert dataset.revision == cord.CORD_REVISION
    manifest = load_manifest(project / "data" / "cord")
    assert dataset.manifest_hash() == manifest["splits"]["test"]["hash"]

    case = dataset.load_case("test-0001")
    assert case.input == {
        "image": "data/cord/images/test-0001.jpg",
        "image_sha256": hashlib.sha256(JPEG).hexdigest(),
    }
    assert case.gold["total"] == "19000"
    with pytest.raises(DatasetError, match="not in dataset"):
        dataset.load_case("test-0002")

    gold = project / "data" / "cord" / "gold" / "test-0000.json"
    gold.write_text(gold.read_text().replace("38500", "99999"))
    with pytest.raises(DatasetError, match="changed since `data prepare`"):
        dataset.load_case("test-0000")


def test_cord_dataset_errors(project: Path, publish: Publish) -> None:
    with pytest.raises(DatasetError, match="does not exist"):
        open_dataset(CordDatasetSpec(kind="cord", split="test"), project)
    with pytest.raises(PathConfinementError):
        open_dataset(CordDatasetSpec(kind="cord", split="test", path="../x"), project)
    prepare_cord(project, splits=["test"], downloader=publish(test=three_test_rows()))
    with pytest.raises(DatasetError, match="not prepared"):
        open_dataset(CordDatasetSpec(kind="cord", split="validation"), project)


def test_cli_prepare_and_stats(
    project: Path, publish: Publish, monkeypatch: pytest.MonkeyPatch
) -> None:
    hub = publish(test=three_test_rows())
    tokens: list[str | None] = []

    def fake_downloader(token: str | None = None) -> FakeHub:
        tokens.append(token)
        return hub

    monkeypatch.setattr(cord, "hf_downloader", fake_downloader)
    result = runner.invoke(app, ["data", "prepare", "--split", "test"])
    assert result.exit_code == 0, result.output
    assert "test: 3 rows, 2 gold, 1 unmappable" in result.output
    assert "wrote data/cord/manifest.json" in result.output
    assert tokens == [None]

    result = runner.invoke(app, ["data", "stats", "--split", "test"])
    assert result.exit_code == 0, result.output
    assert "3 rows, 2 gold, 1 unmappable" in result.output
    assert "min 1, median 1.5, mean 1.50, max 2" in result.output
    assert "9.999" in result.output
    assert "test-0002: no total_price" in result.output
    assert "@ 7f0115a4b758 (CC-BY-4.0)" in result.output


def test_cli_errors(project: Path) -> None:
    result = runner.invoke(app, ["data", "prepare", "--split", "dev"])
    assert result.exit_code == 1
    assert "unknown split(s): dev" in result.output
    result = runner.invoke(app, ["data", "stats"])
    assert result.exit_code == 1
    assert "run `receipt_extractor data prepare` first" in result.output


def test_hub_errors_never_echo_their_message(
    project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import huggingface_hub  # noqa: PLC0415

    def boom(*_args: Any, **_kwargs: Any) -> str:
        raise OSError("Authorization: Bearer hf_secret_token")

    monkeypatch.setattr(huggingface_hub, "hf_hub_download", boom)
    download = cord.hf_downloader("hf_secret_token")
    with pytest.raises(DatasetError) as info:
        download(cord.CORD_FILES[-1], project / "data" / "cord" / "raw")
    assert "hf_secret_token" not in str(info.value)
    assert "OSError" in str(info.value)
