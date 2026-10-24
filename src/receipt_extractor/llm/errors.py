"""Exception hierarchy and error-text sanitising for the LLM layer (TRD §S3, §S6).

Every :class:`LLMError` message passes through :func:`sanitize_error_text` when the exception
is built, so API keys and long provider bodies cannot leak through ``str(exc)``, logs or
records. Provider adapters additionally redact the exact key they used.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from typing import Final

MAX_ERROR_CHARS: Final = 300
REDACTED: Final = "[REDACTED]"
_MIN_SECRET_LEN: Final = 4

# Shapes of credentials that can appear in provider error bodies or request URLs
# (Gemini sends ?key=..., OpenAI-style APIs echo "Bearer ...").
_SECRET_PATTERNS: Final = (
    re.compile(r"(?i)(bearer\s+)[^\s\"',;]+"),
    re.compile(
        r"(?i)((?:api[_-]?key|x-goog-api-key|access[_-]?token|token|authorization|key)"
        r"[\"']?\s*[:=]\s*[\"']?)[^\s\"'&,;}]+"
    ),
    re.compile(r"\bgsk_[A-Za-z0-9]{8,}"),
    re.compile(r"\bAIza[0-9A-Za-z_\-]{16,}"),
    re.compile(r"\bsk-[A-Za-z0-9_\-]{8,}"),
    re.compile(r"\bhf_[A-Za-z0-9]{8,}"),
)
_CONTROL_CHARS: Final = re.compile(r"[\x00-\x1f\x7f]+")


def sanitize_error_text(
    text: str, secrets: Iterable[str] = (), *, limit: int = MAX_ERROR_CHARS
) -> str:
    """Redact credentials, strip control characters and truncate ``text`` to ``limit``.

    Exact ``secrets`` are removed first, then credential-shaped substrings. Redaction runs
    before truncation so a key cut in half by the limit is never partially shown.
    """
    for secret in secrets:
        if secret and len(secret) >= _MIN_SECRET_LEN:
            text = text.replace(secret, REDACTED)
    for pattern in _SECRET_PATTERNS:
        text = pattern.sub(
            lambda m: (m.group(1) if m.groups() else "") + REDACTED,
            text,
        )
    text = _CONTROL_CHARS.sub(" ", text)
    text = " ".join(text.split())
    if len(text) > limit:
        text = text[: limit - 1].rstrip() + "…"
    return text


class LLMError(RuntimeError):
    """Base class. The message is sanitised on construction."""

    def __init__(self, message: str) -> None:
        super().__init__(sanitize_error_text(message))


class LLMConfigError(LLMError):
    """``models.yaml`` / ``pricing.yaml`` is missing or invalid."""


class UnknownModelError(LLMError):
    """The alias is not in the registry or cannot be called for this operation."""


class MissingCredentialsError(LLMError):
    """The API key an alias needs is not configured."""


class DataPolicyError(LLMError):
    """The request's data origin is not allowed for the alias (e.g. user upload → Gemini)."""


class BudgetExhaustedError(LLMError):
    """The alias's daily request or token budget would be exceeded; resume on another day."""

    def __init__(self, alias: str, resource: str, used: int, limit: int) -> None:
        self.alias = alias
        self.resource = resource
        self.used = used
        self.limit = limit
        super().__init__(
            f"daily {resource} budget for '{alias}' exhausted ({used}/{limit} used); "
            "the run can resume after the UTC day rolls over"
        )


class ProviderQuotaExhaustedError(BudgetExhaustedError):
    """The *provider* refused for its daily quota (a per-day 429 or a far ``Retry-After``).

    Its window need not match the local UTC tracker (Groq's tokens-per-day is not reset at UTC
    midnight), so the local budget can say "fine" while the provider says no. Being a
    :class:`BudgetExhaustedError`, it pauses an eval run instead of recording error rows.
    """

    def __init__(self, alias: str, retry_after_s: float | None) -> None:
        self.retry_after_s = retry_after_s
        LLMError.__init__(
            self,
            f"provider daily quota for '{alias}' exhausted"
            + (f" (retry after {retry_after_s:.0f} s)" if retry_after_s is not None else "")
            + "; resume the run later",
        )
        self.alias, self.resource, self.used, self.limit = alias, "provider quota", 0, 0


class ProviderError(LLMError):
    """A provider call failed. ``retryable`` decides whether the client retries it."""

    retryable: bool = False

    def __init__(
        self,
        message: str,
        *,
        status_code: int | None = None,
        retry_after_s: float | None = None,
    ) -> None:
        self.status_code = status_code
        self.retry_after_s = retry_after_s
        super().__init__(message)


class RateLimitedError(ProviderError):
    """HTTP 429 from the provider."""

    retryable = True


class ProviderUnavailableError(ProviderError):
    """5xx, connection failure or similar transient condition."""

    retryable = True


class ProviderTimeoutError(ProviderError):
    """The call exceeded its hard timeout."""

    retryable = True


class ProviderRequestError(ProviderError):
    """Non-retryable 4xx (bad request, auth, context window, …)."""


class RetriesExhaustedError(LLMError):
    """Every attempt failed with a retryable error."""

    def __init__(self, alias: str, attempts: int, last_error: ProviderError) -> None:
        self.alias = alias
        self.attempts = attempts
        self.last_error = last_error
        super().__init__(
            f"'{alias}' failed after {attempts} attempts; last error: "
            f"{type(last_error).__name__}: {last_error}"
        )
