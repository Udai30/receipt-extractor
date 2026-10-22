"""The ``toy`` task: adds the two numbers of a synthetic case, wrong on purpose for some cases.

Used by the M0.4 acceptance run (``configs/runs/toy.yaml``) and tests to exercise the runner,
reports and resume without any model call. Which cases are wrong depends only on the run seed
and case id, so runs are reproducible.
"""

from __future__ import annotations

import asyncio
import hashlib
from collections.abc import Mapping
from decimal import Decimal
from typing import Any, Final

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from receipt_extractor.eval.datasets import Case, CaseId, describe_validation_error
from receipt_extractor.eval.errors import RunConfigError
from receipt_extractor.eval.tasks import CaseContext, CaseOutput, register_task

TOY_TOKENS_IN: Final = 12
TOY_TOKENS_OUT: Final = 3
TOY_COST_USD: Final = Decimal("0.000002")
_HASH_SCALE: Final = float(2**64)


class ToyParams(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    error_rate: float = Field(default=0.2, ge=0.0, le=1.0)
    raise_ids: tuple[CaseId, ...] = ()
    delay_ms: int = Field(default=0, ge=0, le=10_000)


class ToyTask:
    def __init__(self, params: ToyParams) -> None:
        self.params = params
        self._raise_ids = frozenset(params.raise_ids)

    async def __call__(self, case: Case, ctx: CaseContext) -> CaseOutput:
        if self.params.delay_ms:
            await asyncio.sleep(self.params.delay_ms / 1000)
        if case.case_id in self._raise_ids:
            raise ValueError("toy task: simulated failure")
        total = int(case.input["a"]) + int(case.input["b"])
        if _unit_interval(ctx.seed, case.case_id) < self.params.error_rate:
            total += 1
        return CaseOutput(
            prediction={"sum": total},
            tokens_in=TOY_TOKENS_IN,
            tokens_out=TOY_TOKENS_OUT,
            list_cost_usd=TOY_COST_USD,
        )


def _unit_interval(seed: int, case_id: str) -> float:
    digest = hashlib.sha256(f"{seed}:{case_id}".encode()).digest()
    return int.from_bytes(digest[:8], "big") / _HASH_SCALE


def _factory(params: Mapping[str, Any]) -> ToyTask:
    try:
        return ToyTask(ToyParams.model_validate(dict(params)))
    except ValidationError as exc:
        raise RunConfigError(describe_validation_error(exc, "params of task 'toy'")) from None


register_task("toy", _factory)
