"""Typed, environment-sourced application configuration.

No module outside this file should read ``os.environ`` directly. Model names in
particular (``LLM_MODEL``, ``EMBEDDING_MODEL``) live here and only here — providers
receive them as constructor arguments, never hard-coded.
"""

from functools import lru_cache
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # --- Runtime environment ---
    environment: Literal["local", "test", "staging", "production"] = "local"

    # --- API ---
    api_host: str = "0.0.0.0"
    api_port: int = 8000

    # --- Database ---
    database_url: str = Field(
        ...,
        description="Async SQLAlchemy URL, e.g. postgresql+asyncpg://user:pass@host:5432/dineops",
    )

    # --- Logging ---
    log_level: str = "INFO"
    log_format: Literal["console", "json"] = "console"

    # --- LLM provider (agent reasoning) ---
    llm_provider: str = "ollama"
    llm_model: str = "qwen3:8b"
    ollama_base_url: str = "http://localhost:11434"
    llm_request_timeout_seconds: float = 120.0

    # --- Embedding provider (MemoryService semantic search only — NOT RAG, NOT the agent LLM) ---
    embedding_provider: str = "bge"
    embedding_model: str = "BAAI/bge-m3"
    embedding_device: str = "cpu"


@lru_cache
def get_settings() -> Settings:
    """Process-wide cached Settings instance."""
    return Settings()  # type: ignore[call-arg]
