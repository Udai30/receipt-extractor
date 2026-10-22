"""The ``gold`` task: predicts each case's own gold — the scorer's calibration run (M1.2).

``configs/runs/gold.yaml`` scores the CORD test gold against itself; every comparative metric
must come out at 1.0, so a lower number for a real variant is the variant's error, not the
scorer's. No model calls, no cost.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from receipt_extractor.eval.datasets import Case
from receipt_extractor.eval.errors import RunConfigError
from receipt_extractor.eval.tasks import CaseContext, CaseOutput, register_task


class GoldTask:
    async def __call__(self, case: Case, ctx: CaseContext) -> CaseOutput:
        return CaseOutput(prediction=case.gold)


def _factory(params: Mapping[str, Any]) -> GoldTask:
    if params:
        raise RunConfigError("task 'gold' takes no params")
    return GoldTask()


register_task("gold", _factory)
