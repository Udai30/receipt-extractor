"""Settings: fail-fast validation and secret masking."""

from __future__ import annotations

from pathlib import Path

import pytest

from receipt_extractor.config import ConfigError, Settings, get_settings, load_settings

DB_URL = "postgresql+asyncpg://user:db-password-sentinel@localhost:5432/receipts"
SECRETS = {
    "DATABASE_URL": DB_URL,
    "GROQ_API_KEY": "groq-key-sentinel",
    "GEMINI_API_KEY": "gemini-key-sentinel",
    "HF_TOKEN": "hf-token-sentinel",
}
SENTINELS = (
    "db-password-sentinel",
    "groq-key-sentinel",
    "gemini-key-sentinel",
    "hf-token-sentinel",
)


def test_missing_required_key_fails_fast_with_clear_message(clean_env: pytest.MonkeyPatch) -> None:
    with pytest.raises(ConfigError) as excinfo:
        load_settings()
    message = str(excinfo.value)
    assert "DATABASE_URL" in message
    assert ".env.example" in message
    assert excinfo.value.__cause__ is None


def test_loads_with_required_key_and_defaults(clean_env: pytest.MonkeyPatch) -> None:
    clean_env.setenv("DATABASE_URL", DB_URL)
    settings = load_settings()
    assert settings.database_url.get_secret_value() == DB_URL
    assert settings.groq_api_key is None
    assert str(settings.ollama_base_url) == "http://localhost:11434/"
    assert settings.log_level == "INFO"
    assert settings.demo_mode is False


def test_reads_dotenv_file(clean_env: pytest.MonkeyPatch, tmp_path: Path) -> None:
    (tmp_path / ".env").write_text(f"DATABASE_URL={DB_URL}\nDEMO_MODE=true\n", encoding="utf-8")
    settings = load_settings()
    assert settings.database_url.get_secret_value() == DB_URL
    assert settings.demo_mode is True


def test_secrets_never_appear_in_repr_or_str(clean_env: pytest.MonkeyPatch) -> None:
    for name, value in SECRETS.items():
        clean_env.setenv(name, value)
    settings = load_settings()
    rendered = [repr(settings), str(settings), settings.model_dump_json()]
    for text in rendered:
        for sentinel in SENTINELS:
            assert sentinel not in text


def test_invalid_value_error_does_not_echo_input(clean_env: pytest.MonkeyPatch) -> None:
    clean_env.setenv("DATABASE_URL", DB_URL)
    clean_env.setenv("OLLAMA_BASE_URL", "not-a-url-db-password-sentinel")
    with pytest.raises(ConfigError) as excinfo:
        load_settings()
    message = str(excinfo.value)
    assert "OLLAMA_BASE_URL" in message
    assert "db-password-sentinel" not in message


def test_invalid_log_level_rejected(clean_env: pytest.MonkeyPatch) -> None:
    clean_env.setenv("DATABASE_URL", DB_URL)
    clean_env.setenv("LOG_LEVEL", "VERBOSE")
    with pytest.raises(ConfigError, match="LOG_LEVEL"):
        load_settings()


def test_get_settings_is_cached(clean_env: pytest.MonkeyPatch) -> None:
    clean_env.setenv("DATABASE_URL", DB_URL)
    assert get_settings() is get_settings()


def test_settings_are_immutable(clean_env: pytest.MonkeyPatch) -> None:
    clean_env.setenv("DATABASE_URL", DB_URL)
    settings = load_settings()
    with pytest.raises(ValueError, match="frozen"):
        settings.demo_mode = True  # type: ignore[misc]


def test_env_example_documents_every_setting() -> None:
    env_example = Path(__file__).resolve().parents[2] / ".env.example"
    keys = {
        line.split("=", 1)[0].strip()
        for line in env_example.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    }
    assert {name.upper() for name in Settings.model_fields} == keys
