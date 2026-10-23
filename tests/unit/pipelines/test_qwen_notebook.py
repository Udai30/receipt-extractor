"""M2.4: the generated Kaggle notebook stays in sync with this repository's prompt, data and ids."""

from __future__ import annotations

import ast
import copy
import json
import re
from pathlib import Path
from typing import Any

import pytest

from receipt_extractor.data.cord import CORD_REVISION
from receipt_extractor.eval.importer import ImportManifest
from receipt_extractor.llm.registry import ModelRegistry
from receipt_extractor.pipelines import qwen_notebook
from receipt_extractor.pipelines.prompts import VISION_PROMPTS

ROOT = Path(__file__).resolve().parents[3]
CORD_MANIFEST = ROOT / "data" / "cord" / "manifest.json"


def fake_cord_manifest(n: int = 3) -> dict[str, Any]:
    ids = [f"test-{i:04d}" for i in range(n)]
    return {
        "revision": CORD_REVISION,
        "splits": {"test": {"ids": ids}},
        "cases": {c: {"image_sha256": f"{i:064x}"} for i, c in enumerate(ids)},
    }


def constants(notebook: dict[str, Any]) -> dict[str, Any]:
    cell = next(
        "".join(c["source"])
        for c in notebook["cells"]
        if "MODEL_REVISION =" in "".join(c["source"])
    )
    namespace: dict[str, Any] = {}
    exec(cell, namespace)  # noqa: S102 - our own generated constants cell
    return namespace


def test_every_code_cell_is_valid_python() -> None:
    notebook = qwen_notebook.build_notebook(fake_cord_manifest())
    for cell in notebook["cells"]:
        source = "".join(cell["source"])
        if cell["cell_type"] == "code" and not source.startswith("!"):
            ast.parse(source)


def test_notebook_uses_the_same_prompt_and_pins_everything() -> None:
    notebook = qwen_notebook.build_notebook(fake_cord_manifest())
    values = constants(notebook)
    assert values["SYSTEM_PROMPT"] == VISION_PROMPTS["b-v1"].system
    assert values["USER_PROMPT"] == VISION_PROMPTS["b-v1"].user
    assert re.fullmatch(r"[0-9a-f]{40}", values["MODEL_REVISION"])
    assert values["CORD_REVISION"] == CORD_REVISION
    assert values["CORD_TEST_FILES"] and all(
        re.fullmatch(r"[0-9a-f]{64}", f["sha256"]) for f in values["CORD_TEST_FILES"]
    )
    pip = next(
        "".join(c["source"]) for c in notebook["cells"] if "".join(c["source"]).startswith("!pip")
    )
    assert all(re.search(r"==\d", pin) for pin in qwen_notebook.PIP_PINS)
    assert all(pin in pip for pin in qwen_notebook.PIP_PINS)


def test_cases_map_image_hashes_to_our_ids() -> None:
    manifest = fake_cord_manifest(5)
    values = constants(qwen_notebook.build_notebook(manifest))
    assert values["CASES"] == {
        case["image_sha256"]: case_id for case_id, case in manifest["cases"].items()
    }


def test_manifest_written_by_the_notebook_is_importable() -> None:
    values = constants(qwen_notebook.build_notebook(fake_cord_manifest()))
    manifest = ImportManifest.model_validate(
        {
            **values["MANIFEST_BASE"],
            "kaggle": {"notebook": "x.ipynb", "gpu": "Tesla T4", "gpu_seconds": 1},
        }
    )
    assert manifest.variant == "C" and manifest.prediction_format == "receipt_reply"
    assert manifest.prompt_version == "b-v1"
    registry = ModelRegistry.from_yaml(ROOT / "configs" / "models.yaml")
    assert registry.get(manifest.models[0]).kind == "offline"


def test_refuses_a_cord_prepared_at_another_revision_or_ambiguous_hashes() -> None:
    other = {**fake_cord_manifest(), "revision": "0" * 40}
    with pytest.raises(ValueError, match="different CORD revision"):
        qwen_notebook.build_notebook(other)
    clash = copy.deepcopy(fake_cord_manifest(2))
    clash["cases"]["test-0001"]["image_sha256"] = clash["cases"]["test-0000"]["image_sha256"]
    with pytest.raises(ValueError, match="share an image SHA-256"):
        qwen_notebook.build_notebook(clash)


def test_render_is_deterministic() -> None:
    manifest = fake_cord_manifest()
    assert qwen_notebook.render(manifest) == qwen_notebook.render(manifest)


@pytest.mark.skipif(not CORD_MANIFEST.is_file(), reason="CORD not prepared")
def test_committed_notebook_is_up_to_date() -> None:
    expected = qwen_notebook.render(json.loads(CORD_MANIFEST.read_text(encoding="utf-8")))
    committed = (ROOT / qwen_notebook.NOTEBOOK_PATH).read_text(encoding="utf-8")
    assert committed == expected, "run: uv run python -m receipt_extractor.pipelines.qwen_notebook"
