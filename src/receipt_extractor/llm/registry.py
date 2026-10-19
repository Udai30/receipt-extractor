"""Model registry (``configs/models.yaml``) and dated price table (``configs/pricing.yaml``).

Both files are parsed with ``yaml.safe_load`` (no object construction), size-capped, and
validated with strict Pydantic models, so a typo fails at load time with a clear message.
"""

from __future__ import annotations

import re
from collections.abc import Iterator, Mapping
from datetime import date
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path
from typing import Any, Final, Literal

import yaml
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    NonNegativeInt,
    PositiveInt,
    ValidationError,
    field_validator,
    model_validator,
)

from receipt_extractor.llm.errors import LLMConfigError, UnknownModelError

MAX_CONFIG_BYTES: Final = 1_000_000
COST_QUANTUM: Final = Decimal("0.00000001")  # matches llm_call.list_cost_usd numeric(12,8)
_PER_MTOK: Final = Decimal(1_000_000)
_ALIAS_RE: Final = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")

ProviderName = Literal["groq", "gemini", "ollama", "local", "kaggle"]
ModelKind = Literal["chat", "embedding", "local", "offline"]
DataPolicy = Literal["any", "public_only"]

CALLABLE_KINDS: Final = frozenset({"chat", "embedding"})
CALLABLE_PROVIDERS: Final = frozenset({"groq", "gemini", "ollama"})

_STRICT = ConfigDict(extra="forbid", frozen=True)


class RateLimits(BaseModel):
    """Free-plan limits of one alias; ``None`` means unlimited/not applicable."""

    model_config = _STRICT

    rpm: PositiveInt | None = None
    tpm: PositiveInt | None = None
    rpd: PositiveInt | None = None
    tpd: PositiveInt | None = None


class ModelSpec(BaseModel):
    """One alias in ``models.yaml``."""

    model_config = _STRICT

    alias: str
    provider: ProviderName
    kind: ModelKind
    model: str = Field(min_length=1, max_length=200)
    context_window: PositiveInt | None = None
    max_output_tokens: PositiveInt | None = None
    data_policy: DataPolicy = "any"
    limits: RateLimits = RateLimits()
    limits_checked: date | None = None
    source: str | None = None

    @field_validator("alias")
    @classmethod
    def _alias_shape(cls, value: str) -> str:
        if not _ALIAS_RE.fullmatch(value):
            raise ValueError("alias must be lowercase letters, digits and '-' (max 64)")
        return value

    @model_validator(mode="after")
    def _consistent(self) -> ModelSpec:
        callable_kind = self.kind in CALLABLE_KINDS
        callable_provider = self.provider in CALLABLE_PROVIDERS
        if callable_kind != callable_provider:
            raise ValueError(f"kind '{self.kind}' is not valid for provider '{self.provider}'")
        if callable_provider and not self.model.startswith(f"{self.provider}/"):
            # LiteLLM routes on this prefix; a mismatch would silently call another provider.
            raise ValueError(
                f"model for provider '{self.provider}' must start with '{self.provider}/'"
            )
        return self

    @property
    def callable(self) -> bool:
        """Whether the LLM layer can call this alias."""
        return self.kind in CALLABLE_KINDS


class PriceEntry(BaseModel):
    """List price in force from ``effective_from`` (USD per 1M tokens)."""

    model_config = _STRICT

    effective_from: date
    input_per_mtok: Decimal = Field(ge=0)
    output_per_mtok: Decimal = Field(ge=0)
    source: str = Field(min_length=1)
    checked: date

    @field_validator("input_per_mtok", "output_per_mtok", mode="before")
    @classmethod
    def _no_float(cls, value: Any) -> Any:
        if isinstance(value, float):
            raise ValueError("quote prices as strings (money is Decimal, never float)")
        return value


class _ModelsFile(BaseModel):
    model_config = _STRICT

    version: Literal[1]
    models: dict[str, dict[str, Any]] = Field(min_length=1)


class _PricingFile(BaseModel):
    model_config = _STRICT

    version: Literal[1]
    currency: Literal["USD"]
    models: dict[str, list[PriceEntry]] = Field(min_length=1)


def _load_yaml(path: Path) -> Any:
    try:
        size = path.stat().st_size
    except OSError:
        raise LLMConfigError(f"config file not found: {path}") from None
    if size > MAX_CONFIG_BYTES:
        raise LLMConfigError(f"config file too large ({size} bytes): {path}")
    try:
        return yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, yaml.YAMLError) as exc:
        raise LLMConfigError(f"cannot parse {path}: {type(exc).__name__}") from None


def _describe(error: ValidationError, path: Path) -> str:
    problems = [
        ".".join(str(p) for p in item["loc"]) + f": {item['msg']}"
        for item in error.errors(include_input=False, include_url=False)[:5]
    ]
    return f"invalid {path.name}: " + "; ".join(problems)


class ModelRegistry:
    """Alias → :class:`ModelSpec` lookup."""

    def __init__(self, specs: Mapping[str, ModelSpec]) -> None:
        self._specs = dict(specs)

    @classmethod
    def from_yaml(cls, path: Path) -> ModelRegistry:
        """Load and validate ``models.yaml``."""
        raw = _load_yaml(path)
        try:
            parsed = _ModelsFile.model_validate(raw)
            specs = {
                alias: ModelSpec.model_validate({**body, "alias": alias})
                for alias, body in parsed.models.items()
            }
        except ValidationError as exc:
            raise LLMConfigError(_describe(exc, path)) from None
        return cls(specs)

    def get(self, alias: str) -> ModelSpec:
        """Return the spec for ``alias`` or raise :class:`UnknownModelError`."""
        try:
            return self._specs[alias]
        except KeyError:
            raise UnknownModelError(
                f"unknown model alias '{alias[:64]}'; known: {', '.join(sorted(self._specs))}"
            ) from None

    def __iter__(self) -> Iterator[ModelSpec]:
        return iter(self._specs.values())

    @property
    def aliases(self) -> list[str]:
        return sorted(self._specs)


class PriceTable:
    """Dated list prices keyed by LiteLLM model string."""

    def __init__(self, prices: Mapping[str, list[PriceEntry]]) -> None:
        self._prices = {
            model: sorted(entries, key=lambda e: e.effective_from)
            for model, entries in prices.items()
        }
        for model, entries in self._prices.items():
            dates = [e.effective_from for e in entries]
            if not entries or len(set(dates)) != len(dates):
                raise LLMConfigError(f"pricing for '{model}' needs unique effective_from dates")

    @classmethod
    def from_yaml(cls, path: Path) -> PriceTable:
        """Load and validate ``pricing.yaml``."""
        raw = _load_yaml(path)
        try:
            parsed = _PricingFile.model_validate(raw)
        except ValidationError as exc:
            raise LLMConfigError(_describe(exc, path)) from None
        return cls(parsed.models)

    def has(self, model: str) -> bool:
        return model in self._prices

    def entry_for(self, model: str, on: date) -> PriceEntry:
        """Price entry in force for ``model`` on ``on``."""
        try:
            entries = self._prices[model]
        except KeyError:
            raise LLMConfigError(f"no price for model '{model}' in pricing.yaml") from None
        current = entries[0]
        for entry in entries:
            if entry.effective_from <= on:
                current = entry
        return current

    def cost(
        self,
        model: str,
        tokens_in: NonNegativeInt | None,
        tokens_out: NonNegativeInt | None,
        on: date,
    ) -> Decimal | None:
        """List cost in USD (8 dp) of a call, or ``None`` when token counts are unknown."""
        if tokens_in is None and tokens_out is None:
            return None
        entry = self.entry_for(model, on)
        total = (
            Decimal(tokens_in or 0) * entry.input_per_mtok
            + Decimal(tokens_out or 0) * entry.output_per_mtok
        ) / _PER_MTOK
        return total.quantize(COST_QUANTUM, rounding=ROUND_HALF_UP)


def check_pricing_covers(registry: ModelRegistry, pricing: PriceTable) -> None:
    """Fail fast when a callable alias has no price (cost accounting must never be missing)."""
    missing = [s.alias for s in registry if s.callable and not pricing.has(s.model)]
    if missing:
        raise LLMConfigError("pricing.yaml has no entry for alias(es): " + ", ".join(missing))
