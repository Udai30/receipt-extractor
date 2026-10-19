"""Unit tests of the LLM layer building blocks: registry, pricing, cache, limits, retry, errors."""

from __future__ import annotations

import json
import re
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest

from receipt_extractor.llm.cache import DiskCache, cache_key
from receipt_extractor.llm.errors import (
    LLMConfigError,
    LLMError,
    UnknownModelError,
    sanitize_error_text,
)
from receipt_extractor.llm.limits import BudgetTracker, RateLimiter, TokenBucket
from receipt_extractor.llm.registry import (
    ModelRegistry,
    PriceTable,
    RateLimits,
    check_pricing_covers,
)
from receipt_extractor.llm.retry import RetryPolicy, parse_retry_after
from receipt_extractor.llm.sinks import JSONLSink
from receipt_extractor.llm.types import LLMCallRecord

PROJECT_ROOT = Path(__file__).resolve().parents[2]
MODELS_YAML = PROJECT_ROOT / "configs" / "models.yaml"
PRICING_YAML = PROJECT_ROOT / "configs" / "pricing.yaml"


# --- registry and pricing ------------------------------------------------------------------


def test_project_configs_load_and_every_callable_alias_is_priced() -> None:
    registry = ModelRegistry.from_yaml(MODELS_YAML)
    pricing = PriceTable.from_yaml(PRICING_YAML)
    check_pricing_covers(registry, pricing)
    # TRD §3 aliases.
    assert {"groq-oss-20b", "groq-oss-120b", "gemini-flash", "donut-cord", "qwen-vl-3b"} <= set(
        registry.aliases
    )
    assert registry.get("gemini-flash").data_policy == "public_only"
    for spec in registry:
        if spec.callable:
            assert spec.provider in {"groq", "gemini", "ollama"}
            if spec.provider != "ollama":
                assert spec.limits.rpm and spec.limits.rpd, spec.alias


def test_only_gemini_aliases_are_public_only_and_all_gemini_aliases_are() -> None:
    registry = ModelRegistry.from_yaml(MODELS_YAML)
    for spec in registry:
        assert (spec.provider == "gemini") == (spec.data_policy == "public_only"), spec.alias


def test_no_model_strings_outside_models_yaml() -> None:
    pattern = re.compile(r"""["'](groq|gemini|ollama)/[\w.\-/]+["']""")
    offenders = [
        f"{path.relative_to(PROJECT_ROOT)}: {match.group(0)}"
        for path in (PROJECT_ROOT / "src").rglob("*.py")
        for match in pattern.finditer(path.read_text(encoding="utf-8"))
    ]
    assert offenders == []


def write(tmp_path: Path, name: str, text: str) -> Path:
    path = tmp_path / name
    path.write_text(text, encoding="utf-8")
    return path


@pytest.mark.parametrize(
    ("body", "message"),
    [
        ("version: 1\nmodels: {}\n", "models"),
        ("version: 2\nmodels: {a: {provider: groq, kind: chat, model: groq/x}}\n", "version"),
        ("version: 1\nmodels: {a: {provider: groq, kind: chat, model: gemini/x}}\n", "groq/"),
        ("version: 1\nmodels: {a: {provider: local, kind: chat, model: x}}\n", "not valid"),
        ("version: 1\nmodels: {Bad_Alias: {provider: groq, kind: chat, model: groq/x}}\n", "alias"),
        ("version: 1\nmodels: {a: {provider: groq, kind: chat, model: groq/x, oops: 1}}\n", "oops"),
        ("version: 1\nmodels: [unclosed\n", "cannot parse"),
        ("!!python/object/apply:os.system ['echo pwned']\n", "cannot parse"),
    ],
)
def test_invalid_models_yaml_is_rejected(tmp_path: Path, body: str, message: str) -> None:
    with pytest.raises(LLMConfigError, match=re.escape(message)):
        ModelRegistry.from_yaml(write(tmp_path, "models.yaml", body))


def test_missing_and_oversized_config_files(tmp_path: Path) -> None:
    with pytest.raises(LLMConfigError, match="not found"):
        ModelRegistry.from_yaml(tmp_path / "absent.yaml")
    big = write(tmp_path, "big.yaml", "#" * 1_000_001)
    with pytest.raises(LLMConfigError, match="too large"):
        PriceTable.from_yaml(big)


def test_unknown_alias_lists_known_ones() -> None:
    registry = ModelRegistry.from_yaml(MODELS_YAML)
    with pytest.raises(UnknownModelError, match="groq-oss-20b"):
        registry.get("gpt-5")


PRICING_BODY = """\
version: 1
currency: USD
models:
  groq/x:
    - {effective_from: 2026-10-19, input_per_mtok: "1.00", output_per_mtok: "2.00",
       source: s, checked: 2026-10-19}
    - {effective_from: 2027-01-01, input_per_mtok: "3.00", output_per_mtok: "4.00",
       source: s, checked: 2026-10-19}
"""


def test_pricing_picks_entry_in_force_on_the_call_date(tmp_path: Path) -> None:
    table = PriceTable.from_yaml(write(tmp_path, "pricing.yaml", PRICING_BODY))
    assert table.cost("groq/x", 1_000_000, 0, date(2026, 12, 31)) == Decimal("1.00000000")
    assert table.cost("groq/x", 1_000_000, 0, date(2027, 1, 1)) == Decimal("3.00000000")
    assert table.cost("groq/x", 0, 1_000_000, date(2025, 1, 1)) == Decimal("2.00000000")  # earliest
    assert table.cost("groq/x", None, None, date(2027, 1, 1)) is None
    # Rounded half-up to 8 dp (llm_call.list_cost_usd numeric(12,8)).
    assert table.cost("groq/x", 1, 0, date(2026, 10, 19)) == Decimal("0.00000100")
    assert table.cost("groq/x", 0, 3, date(2026, 10, 19)) == Decimal("0.00000600")
    with pytest.raises(LLMConfigError, match="no price"):
        table.cost("groq/other", 1, 1, date(2026, 10, 19))


def test_project_gemini_price_rises_on_2027_01_01() -> None:
    table = PriceTable.from_yaml(PRICING_YAML)
    before = table.cost("gemini/gemini-3.8-flash", 1_000_000, 1_000_000, date(2026, 12, 31))
    after = table.cost("gemini/gemini-3.8-flash", 1_000_000, 1_000_000, date(2027, 1, 1))
    assert (before, after) == (Decimal("4.50000000"), Decimal("9.00000000"))


def test_float_prices_are_rejected(tmp_path: Path) -> None:
    body = PRICING_BODY.replace('"1.00"', "1.00")
    with pytest.raises(LLMConfigError, match="quote prices as strings"):
        PriceTable.from_yaml(write(tmp_path, "pricing.yaml", body))


def test_duplicate_price_dates_are_rejected(tmp_path: Path) -> None:
    body = PRICING_BODY.replace("2027-01-01", "2026-10-19")
    with pytest.raises(LLMConfigError, match="unique"):
        PriceTable.from_yaml(write(tmp_path, "pricing.yaml", body))


# --- disk cache ------------------------------------------------------------------------------


def test_cache_key_is_canonical_sha256() -> None:
    a = cache_key({"model": "m", "messages": [{"role": "user", "content": "x"}], "t": 0})
    b = cache_key({"t": 0, "messages": [{"content": "x", "role": "user"}], "model": "m"})
    assert a == b
    assert re.fullmatch(r"[0-9a-f]{64}", a)
    assert cache_key({"model": "m", "t": 0.1}) != cache_key({"model": "m", "t": 0.2})


async def test_cache_round_trip_and_layout(tmp_path: Path) -> None:
    cache = DiskCache(tmp_path)
    key = cache_key({"x": 1})
    assert await cache.get(key) is None
    await cache.set(key, {"text": "héllo", "n": 1})
    assert await cache.get(key) == {"text": "héllo", "n": 1}
    assert cache.path_for(key) == tmp_path / key[:2] / f"{key}.json"
    assert not list(tmp_path.rglob(".tmp-*"))  # atomic write left no temp files


@pytest.mark.parametrize("key", ["../../etc/passwd", "ABC", "a" * 63, "g" * 64, ""])
def test_cache_rejects_keys_that_could_escape_root(tmp_path: Path, key: str) -> None:
    with pytest.raises(ValueError, match="64 lowercase hex"):
        DiskCache(tmp_path).path_for(key)


async def test_corrupt_or_oversized_entries_are_misses(tmp_path: Path) -> None:
    cache = DiskCache(tmp_path, max_entry_bytes=100)
    key = cache_key({"x": 2})
    path = cache.path_for(key)
    path.parent.mkdir(parents=True)
    path.write_text("{not json", encoding="utf-8")
    assert await cache.get(key) is None
    path.write_text("[1, 2]", encoding="utf-8")
    assert await cache.get(key) is None
    path.write_text(json.dumps({"x": "y" * 200}), encoding="utf-8")
    assert await cache.get(key) is None
    await cache.set(key, {"x": "y" * 200})  # too large: not stored
    assert path.read_text(encoding="utf-8").startswith('{"x": "yyy')


# --- limiter and budget ------------------------------------------------------------------------


class Clock:
    def __init__(self) -> None:
        self.now = 0.0
        self.sleeps: list[float] = []

    def __call__(self) -> float:
        return self.now

    async def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += seconds


def test_token_bucket_refills_and_reports_wait() -> None:
    bucket = TokenBucket(capacity=10, rate_per_s=1, now=0)
    assert bucket.wait_time(10, now=0) == 0
    bucket.take(10, now=0)
    assert bucket.wait_time(4, now=0) == pytest.approx(4)
    assert bucket.wait_time(4, now=2) == pytest.approx(2)
    bucket.adjust(5, now=2)  # used more than reserved → debt
    assert bucket.tokens == pytest.approx(-3)
    bucket.adjust(-100, now=2)  # refunds never exceed capacity
    assert bucket.tokens == 10


async def test_tpm_limiter_sleeps_exactly_the_deficit_without_busy_waiting() -> None:
    clock = Clock()
    limiter = RateLimiter(RateLimits(tpm=600), clock=clock, sleep=clock.sleep)
    assert await limiter.acquire(600) == 600
    assert await limiter.acquire(300) == 300  # 10 tokens/s → wait 30 s
    assert clock.sleeps == [pytest.approx(30.0)]
    limiter.settle(reserved=300, actual=100)  # 200 refunded
    assert await limiter.acquire(200) == 200
    assert len(clock.sleeps) == 1


async def test_oversized_request_is_clamped_to_tpm_capacity() -> None:
    clock = Clock()
    limiter = RateLimiter(RateLimits(tpm=100), clock=clock, sleep=clock.sleep)
    assert await limiter.acquire(10_000) == 100


async def test_unlimited_alias_never_waits() -> None:
    clock = Clock()
    limiter = RateLimiter(RateLimits(), clock=clock, sleep=clock.sleep)
    for _ in range(100):
        await limiter.acquire(10_000)
    assert clock.sleeps == []


async def test_budget_persists_across_instances_and_resets_daily(tmp_path: Path) -> None:
    from receipt_extractor.llm.errors import BudgetExhaustedError  # noqa: PLC0415

    state = tmp_path / "budget.json"
    limits = RateLimits(rpd=5, tpd=1000)
    day = [date(2026, 10, 19)]
    tracker = BudgetTracker(state, today=lambda: day[0])
    await tracker.reserve("a", limits, 600)
    await tracker.settle("a", reserved=600, actual=400)
    assert await tracker.usage("a") == {"requests": 1, "tokens": 400}

    reloaded = BudgetTracker(state, today=lambda: day[0])
    assert await reloaded.usage("a") == {"requests": 1, "tokens": 400}
    with pytest.raises(BudgetExhaustedError, match="token budget"):
        await reloaded.reserve("a", limits, 601)
    await reloaded.reserve("a", limits, 600)  # exactly at the limit is allowed

    day[0] = date(2026, 10, 20)
    assert await reloaded.usage("a") == {"requests": 0, "tokens": 0}


async def test_budget_ignores_corrupt_state(tmp_path: Path) -> None:
    state = tmp_path / "budget.json"
    state.write_text('{"date": "2026-10-19", "usage": {"a": {"requests": "x"}}}', encoding="utf-8")
    tracker = BudgetTracker(state, today=lambda: date(2026, 10, 19))
    assert await tracker.usage("a") == {"requests": 0, "tokens": 0}
    state.write_text("garbage", encoding="utf-8")
    tracker = BudgetTracker(state, today=lambda: date(2026, 10, 19))
    assert await tracker.usage("a") == {"requests": 0, "tokens": 0}


# --- retry ---------------------------------------------------------------------------------


def test_retry_delay_grows_exponentially_and_is_capped() -> None:
    import random  # noqa: PLC0415

    policy = RetryPolicy(base_delay_s=1, max_delay_s=8)
    rng = random.Random(1)  # noqa: S311 - deterministic jitter
    for failures, step in [(1, 1), (2, 2), (3, 4), (4, 8), (10, 8)]:
        delay = policy.delay(failures, None, rng)
        assert step / 2 <= delay <= step
    assert policy.delay(1, 20.0, rng) == 20.0
    with pytest.raises(ValueError, match="invalid retry policy"):
        RetryPolicy(max_attempts=0)


def test_parse_retry_after() -> None:
    now = datetime(2026, 10, 19, 12, 0, tzinfo=UTC)
    http_date = (now + timedelta(seconds=90)).strftime("%a, %d %b %Y %H:%M:%S GMT")
    assert parse_retry_after("7") == 7.0
    assert parse_retry_after(" 1.5 ") == 1.5
    assert parse_retry_after(http_date, now=now) == pytest.approx(90)
    assert parse_retry_after("-3") == 0.0
    for bad in (None, "", "soon", "nan", "inf"):
        assert parse_retry_after(bad) is None


# --- error sanitising ------------------------------------------------------------------------


def test_sanitize_redacts_keys_and_truncates() -> None:
    secret = "my-very-own-secret-value"  # noqa: S105 - test value
    text = (
        "401 for https://generativelanguage.googleapis.com/v1/models"
        "?key=AIzaSyA1234567890abcdefghij "
        f"Authorization: Bearer gsk_abcdefghijklmnop12345 api_key='{secret}' raw={secret}\n\x00"
        + "x"
        * 1000
    )
    clean = sanitize_error_text(text, [secret])
    assert secret not in clean
    assert "AIzaSy" not in clean and "gsk_abc" not in clean
    assert "[REDACTED]" in clean
    assert len(clean) <= 300 and clean.endswith("…")
    assert "\x00" not in clean and "\n" not in clean


def test_llm_error_messages_are_always_sanitised() -> None:
    err = LLMError("boom gsk_abcdefghijklmnop12345 " + "y" * 500)
    assert "gsk_abc" not in str(err)
    assert len(str(err)) <= 300


# --- JSONL sink ------------------------------------------------------------------------------


async def test_jsonl_sink_appends_one_record_per_line(tmp_path: Path) -> None:
    sink = JSONLSink.for_run(tmp_path, "run-001")
    records = [
        LLMCallRecord(
            model_alias="a",
            provider="groq",
            model="groq/x",
            latency_ms=5,
            cache_hit=False,
            list_cost_usd=Decimal("0.00000123"),
        ),
        LLMCallRecord(
            model_alias="a", provider="groq", model="groq/x", latency_ms=1, cache_hit=True
        ),
    ]
    for record in records:
        await sink.write(record)

    path = tmp_path / "run-001" / "llm_calls.jsonl"
    lines = path.read_text(encoding="utf-8").splitlines()
    assert [LLMCallRecord.model_validate_json(line) for line in lines] == records
    assert json.loads(lines[0])["list_cost_usd"] == "0.00000123"  # Decimal kept exact


@pytest.mark.parametrize("run_id", ["..", "../x", "a/b", "a\\b", "", "x" * 200])
def test_jsonl_sink_rejects_unsafe_run_ids(tmp_path: Path, run_id: str) -> None:
    with pytest.raises(ValueError, match="run_id"):
        JSONLSink.for_run(tmp_path, run_id)
