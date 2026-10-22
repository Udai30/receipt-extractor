"""LLM provider layer: client, disk cache, rate limiting, retries, pricing (TRD §S3).

Every model call in the project goes through :class:`LLMClient` (built with
:func:`build_client`). Model strings live only in ``configs/models.yaml``.
"""

from receipt_extractor.llm.client import Credentials, LLMClient, build_client
from receipt_extractor.llm.errors import (
    BudgetExhaustedError,
    DataPolicyError,
    LLMConfigError,
    LLMError,
    MissingCredentialsError,
    ProviderError,
    RetriesExhaustedError,
    UnknownModelError,
)
from receipt_extractor.llm.sinks import CallSink, JSONLSink, MemorySink
from receipt_extractor.llm.types import (
    CallTags,
    EmbedRequest,
    EmbedResponse,
    ImagePart,
    LLMCallRecord,
    LLMRequest,
    LLMResponse,
    Message,
    TextPart,
)

__all__ = [
    "BudgetExhaustedError",
    "CallSink",
    "CallTags",
    "Credentials",
    "DataPolicyError",
    "EmbedRequest",
    "EmbedResponse",
    "ImagePart",
    "JSONLSink",
    "LLMCallRecord",
    "LLMClient",
    "LLMConfigError",
    "LLMError",
    "LLMRequest",
    "LLMResponse",
    "MemorySink",
    "Message",
    "MissingCredentialsError",
    "ProviderError",
    "RetriesExhaustedError",
    "TextPart",
    "UnknownModelError",
    "build_client",
]
