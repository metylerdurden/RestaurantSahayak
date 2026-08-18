import pytest
from pydantic import ValidationError

from app.core.config import Settings


def test_settings_loads_from_explicit_values():
    settings = Settings(database_url="postgresql+asyncpg://u:p@localhost:5432/db")
    assert settings.llm_provider == "ollama"
    assert settings.llm_model == "qwen3:8b"
    assert settings.embedding_provider == "bge"
    assert settings.embedding_model == "BAAI/bge-m3"


def test_settings_requires_database_url(monkeypatch):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    with pytest.raises(ValidationError):
        Settings(_env_file=None)  # type: ignore[call-arg]


def test_settings_model_names_are_configurable_not_hardcoded():
    settings = Settings(
        database_url="postgresql+asyncpg://u:p@localhost:5432/db",
        llm_model="a-different-model:latest",
        embedding_model="some-org/some-embedding-model",
    )
    assert settings.llm_model == "a-different-model:latest"
    assert settings.embedding_model == "some-org/some-embedding-model"
