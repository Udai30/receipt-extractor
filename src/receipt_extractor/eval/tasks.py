"""Task registry: the variant implementation a run config names with ``task:``.

A task turns one :class:`Case` into a :class:`CaseOutput`. Pipelines (M1.3+) register a
factory under their name; the factory validates the config's ``params``. Exceptions raised by
a task become ``status = error`` rows, except :class:`~receipt_extractor.llm.BudgetExhaustedError`,
which pauses the run.
"""

from __future__ import annotations

import importlib
import uuid
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import Any, Final, Protocol

from pydantic import BaseModel, ConfigDict, Field

from receipt_extractor.eval.datasets import Case
from receipt_extractor.eval.errors import UnknownTaskError

_BUILTIN_MODULES: Final = (
    "receipt_extractor.eval.toy",
    "receipt_extractor.eval.oracle",
    "receipt_extractor.pipelines.tasks",
)


@dataclass(frozen=True)
class CaseContext:
    """What a task may need besides the case: correlation ids and where LLM calls are logged.

    ``run_uuid`` is derived from ``run_id`` for :class:`~receipt_extractor.llm.CallTags`
    (whose ``run_id`` is a UUID); ``llm_calls_path`` is ``runs/<run_id>/llm_calls.jsonl``;
    ``root`` is the project root that project-relative case inputs (image paths) resolve against.
    """

    run_id: str
    run_uuid: uuid.UUID
    run_dir: Path
    llm_calls_path: Path
    case_id: str
    seed: int
    root: Path


class CaseOutput(BaseModel):
    """A task's result for one case. ``prediction`` must be JSON-serialisable (or a model)."""

    model_config = ConfigDict(extra="forbid", arbitrary_types_allowed=True)

    prediction: Any
    details: dict[str, Any] = Field(default_factory=dict)
    tokens_in: int = Field(default=0, ge=0)
    tokens_out: int = Field(default=0, ge=0)
    list_cost_usd: Decimal = Field(default=Decimal(0), ge=0)
    wait_ms: int = Field(
        default=0,
        ge=0,
        description="Time the case spent queued for free-tier pacing (budget, rate limiter, "
        "concurrency slot); reported apart from latency (TRD §S5)",
    )


class CaseTask(Protocol):
    async def __call__(self, case: Case, ctx: CaseContext) -> CaseOutput: ...


TaskFactory = Callable[[Mapping[str, Any]], CaseTask]

_REGISTRY: dict[str, TaskFactory] = {}
_builtins_loaded = False


def register_task(name: str, factory: TaskFactory, *, replace: bool = False) -> None:
    if name in _REGISTRY and not replace:
        raise ValueError(f"task {name!r} is already registered")
    _REGISTRY[name] = factory


def _load_builtins() -> None:
    global _builtins_loaded  # noqa: PLW0603 - one-time lazy registration
    if not _builtins_loaded:
        for module in _BUILTIN_MODULES:
            importlib.import_module(module)
        _builtins_loaded = True


def get_task_factory(name: str) -> TaskFactory:
    _load_builtins()
    try:
        return _REGISTRY[name]
    except KeyError:
        known = ", ".join(sorted(_REGISTRY)) or "none"
        raise UnknownTaskError(f"unknown task {name!r} (registered: {known})") from None


def build_task(name: str, params: Mapping[str, Any]) -> CaseTask:
    return get_task_factory(name)(params)
