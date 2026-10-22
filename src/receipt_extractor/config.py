"""Application settings loaded from the environment and ``.env`` (TRD §S6).

Secrets are typed as :class:`pydantic.SecretStr`, so they are masked in ``repr``/``str``
and must be unwrapped explicitly with ``get_secret_value()`` at the point of use.

Settings are never loaded at import time. Call :func:`get_settings` where configuration
is needed; it validates once per process and fails fast with :class:`ConfigError`.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import AnyHttpUrl, Field, SecretStr, ValidationError
from pydantic_settings import BaseSettings, SettingsConfigDict

LogLevel = Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]


class ConfigError(RuntimeError):
    """Raised when the environment does not provide a valid configuration."""


class Settings(BaseSettings):
    """Process-wide configuration. Every field is documented in ``.env.example``."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        frozen=True,
    )

    # Provider credentials — optional here; the LLM layer checks the ones a call needs.
    groq_api_key: SecretStr | None = None
    gemini_api_key: SecretStr | None = None
    hf_token: SecretStr | None = None

    # LLM provider layer (TRD §S3). Relative paths resolve against the working directory.
    llm_models_path: Path = Path("configs/models.yaml")
    llm_pricing_path: Path = Path("configs/pricing.yaml")
    llm_cache_dir: Path = Path(".cache/llm")
    llm_budget_path: Path = Path(".cache/llm_budget.json")
    llm_max_concurrency: int = Field(default=4, ge=1, le=64)
    llm_timeout_s: float = Field(default=60.0, gt=0, le=600)

    # Local services
    ollama_base_url: AnyHttpUrl = AnyHttpUrl("http://localhost:11434")

    # Runtime behaviour
    log_level: LogLevel = "INFO"
    demo_mode: bool = False


def _describe(error: ValidationError) -> str:
    """Summarise validation errors by variable name and reason only.

    Pydantic's own message echoes the offending input, which may be a secret, so it is
    never surfaced.
    """
    missing: list[str] = []
    invalid: list[str] = []
    for item in error.errors(include_input=False, include_url=False):
        name = "_".join(str(part) for part in item["loc"]).upper() or "<settings>"
        if item["type"] == "missing":
            missing.append(name)
        else:
            invalid.append(f"{name} ({item['msg']})")
    parts: list[str] = []
    if missing:
        parts.append("missing required variable(s): " + ", ".join(sorted(missing)))
    if invalid:
        parts.append("invalid variable(s): " + "; ".join(invalid))
    return (
        "Configuration error — "
        + " | ".join(parts)
        + ". Copy .env.example to .env and fill in the values."
    )


def load_settings() -> Settings:
    """Build and validate a fresh :class:`Settings`, raising :class:`ConfigError` on failure."""
    try:
        return Settings()
    except ValidationError as exc:
        # `from None`: the chained ValidationError would carry the raw input values.
        raise ConfigError(_describe(exc)) from None


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return the process-wide settings, validated on first use."""
    return load_settings()
