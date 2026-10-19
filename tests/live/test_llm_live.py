"""Live smoke tests against the real free-tier providers.

Opt-in: they run only with ``uv run pytest -m live`` and skip unless the provider's key is set
(in the environment or ``.env``). Each makes one tiny call with synthetic content and no cache;
nothing here sends user data, and Gemini receives only the fixed ping prompt.
"""

from __future__ import annotations

import pytest

from receipt_extractor.config import get_settings
from receipt_extractor.llm import EmbedRequest, LLMClient, LLMRequest, Message, build_client

pytestmark = pytest.mark.live


def _client_or_skip(key_field: str | None) -> LLMClient:
    settings = get_settings()
    secret = getattr(settings, key_field) if key_field else None
    if key_field is not None and (secret is None or not secret.get_secret_value()):
        pytest.skip(f"{key_field.upper()} not set")
    return build_client(settings, cache=False, budget=True)


@pytest.mark.parametrize(
    ("alias", "key_field"),
    [
        ("groq-oss-20b", "groq_api_key"),
        ("groq-oss-120b", "groq_api_key"),
        ("gemini-flash", "gemini_api_key"),
    ],
)
async def test_live_chat_ping(alias: str, key_field: str) -> None:
    client = _client_or_skip(key_field)
    response = await client.complete(
        LLMRequest(
            model_alias=alias,
            messages=(Message(role="user", content="Reply with exactly one word: pong"),),
            max_tokens=512,
            data_origin="synthetic",
        )
    )
    assert response.tokens_in and response.tokens_in > 0
    assert response.latency_ms > 0
    assert response.list_cost_usd is not None and response.list_cost_usd > 0
    assert "pong" in response.text.lower()


async def test_live_ollama_embedding() -> None:
    import httpx  # noqa: PLC0415

    settings = get_settings()
    try:
        httpx.get(f"{str(settings.ollama_base_url).rstrip('/')}/api/tags", timeout=2)
    except httpx.HTTPError:
        pytest.skip("Ollama is not running")
    client = _client_or_skip(None)
    response = await client.embed(
        EmbedRequest(model_alias="ollama-embed", inputs=("pong",), data_origin="synthetic")
    )
    assert len(response.vectors[0]) > 0
