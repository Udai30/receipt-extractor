"""Run config loading (safe_load, strict schema), run id identity and path confinement."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import pytest

from receipt_extractor.eval.config import RunConfig, compute_run_id, load_run_config
from receipt_extractor.eval.errors import PathConfinementError, RunConfigError
from receipt_extractor.eval.paths import CONFIG_ROOTS, confine
from receipt_extractor.eval.runner import PreparedRun, prepare_run

from .conftest import base_config

WriteConfig = Callable[..., Path]
Prepare = Callable[..., PreparedRun]


def test_loads_valid_config(write_config: WriteConfig, project: Path) -> None:
    config = load_run_config(write_config(base_config()), project)
    assert config.variant == "toy"
    assert config.bootstrap.resamples == 1000


def test_python_yaml_tags_are_rejected(write_config: WriteConfig, project: Path) -> None:
    path = write_config("variant: !!python/object/apply:os.system ['echo pwned']\n")
    with pytest.raises(RunConfigError, match="not valid YAML"):
        load_run_config(path, project)


@pytest.mark.parametrize(
    ("override", "message"),
    [
        ({"unknown_key": 1}, "unknown_key"),
        ({"primary_metric": "f1"}, "primary_metric"),
        ({"metrics": ["exact_match", "exact_match"]}, "duplicates"),
        ({"variant": "../escape"}, "variant"),
        ({"concurrency": 0}, "concurrency"),
        ({"sample": {"ids": ["toy-00001"], "size": 1}}, "either"),
    ],
)
def test_invalid_configs_are_rejected(
    write_config: WriteConfig, project: Path, override: dict[str, object], message: str
) -> None:
    with pytest.raises(RunConfigError, match=message):
        load_run_config(write_config(base_config(**override)), project)


def test_non_mapping_and_missing_config(write_config: WriteConfig, project: Path) -> None:
    with pytest.raises(RunConfigError, match="mapping"):
        load_run_config(write_config("- just\n- a list\n"), project)
    with pytest.raises(RunConfigError, match="does not exist"):
        load_run_config("configs/runs/nope.yaml", project)


def test_run_id_is_stable_and_ignores_execution_knobs() -> None:
    config = RunConfig.model_validate(base_config())
    same = RunConfig.model_validate(base_config(concurrency=16, runs_dir="runs/other"))
    changed = RunConfig.model_validate(base_config(seed=1))
    assert compute_run_id(config, "h") == compute_run_id(same, "h")
    assert compute_run_id(config, "h") != compute_run_id(changed, "h")
    assert compute_run_id(config, "h") != compute_run_id(config, "other-dataset-hash")
    assert compute_run_id(config, "h").startswith("toy-")


def test_prepare_run_from_yaml(write_config: WriteConfig, project: Path) -> None:
    prepared = prepare_run(write_config(base_config(sample={"size": 5, "seed": 3})), project)
    again = prepare_run(write_config(base_config(sample={"size": 5, "seed": 3}), "copy"), project)
    assert prepared.run_id == again.run_id
    assert prepared.case_ids == again.case_ids  # fixed seed -> fixed sample
    assert len(prepared.case_ids) == 5
    assert prepared.run_dir == project / "runs" / prepared.run_id


def test_sample_ids_must_exist(prepare: Prepare) -> None:
    with pytest.raises(RunConfigError, match="not in the dataset"):
        prepare(5, sample={"ids": ["toy-00099"]})
    with pytest.raises(RunConfigError, match="exceeds"):
        prepare(5, sample={"size": 6})


# --- path traversal -----------------------------------------------------------------------


@pytest.mark.parametrize(
    "path",
    ["../outside.yaml", "configs/../../outside.yaml", "data/x.jsonl", "/etc/passwd", "C:/x.yaml"],
)
def test_config_file_must_be_under_configs(project: Path, path: str) -> None:
    with pytest.raises(PathConfinementError):
        load_run_config(path, project)


@pytest.mark.parametrize("path", ["../secret.jsonl", "data/../../secret.jsonl", "configs/x.jsonl"])
def test_dataset_path_is_confined_to_data(prepare: Prepare, path: str) -> None:
    with pytest.raises(PathConfinementError, match="data/"):
        prepare(dataset={"kind": "jsonl", "name": "d", "split": "s", "path": path})


def test_dataset_absolute_path_outside_project_is_rejected(
    prepare: Prepare, tmp_path_factory: pytest.TempPathFactory
) -> None:
    outside = tmp_path_factory.mktemp("outside") / "cases.jsonl"
    outside.write_text('{"case_id": "a", "input": 1, "gold": 1}\n', encoding="utf-8")
    with pytest.raises(PathConfinementError):
        prepare(dataset={"kind": "jsonl", "name": "d", "split": "s", "path": str(outside)})


@pytest.mark.parametrize("runs_dir", ["../runs", "data", "runs/../../x", "/var/runs"])
def test_runs_dir_is_confined_to_runs(prepare: Prepare, runs_dir: str) -> None:
    with pytest.raises(PathConfinementError):
        prepare(runs_dir=runs_dir)


def test_symlink_escaping_data_is_rejected(
    project: Path, prepare: Prepare, tmp_path_factory: pytest.TempPathFactory
) -> None:
    outside = tmp_path_factory.mktemp("outside")
    (outside / "cases.jsonl").write_text('{"case_id":"a","input":1,"gold":1}\n')
    (project / "data").mkdir()
    link = project / "data" / "linked"
    try:
        link.symlink_to(outside, target_is_directory=True)
    except OSError:
        pytest.skip("creating symlinks needs extra privileges on this platform")
    with pytest.raises(PathConfinementError):
        prepare(
            dataset={"kind": "jsonl", "name": "d", "split": "s", "path": "data/linked/cases.jsonl"}
        )


def test_confine_accepts_allowed_paths(project: Path) -> None:
    assert confine("data/a/b.jsonl", project, CONFIG_ROOTS) == project / "data" / "a" / "b.jsonl"
    assert confine(project / "runs" / "x", project, CONFIG_ROOTS) == project / "runs" / "x"
    with pytest.raises(PathConfinementError):
        confine("", project, CONFIG_ROOTS)
    with pytest.raises(PathConfinementError):
        confine("data/\x00evil", project, CONFIG_ROOTS)
