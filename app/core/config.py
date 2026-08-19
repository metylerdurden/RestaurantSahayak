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

    # --- Observability (OpenTelemetry tracing) ---
    otel_enabled: bool = True
    otel_service_name: str = "dineops"
    # "console": spans print to stdout, no extra infrastructure needed (default).
    # "otlp": spans are sent to a local collector/Jaeger, see otel_exporter_otlp_endpoint.
    # "none": spans are created but never exported (used by the test suite, which
    # installs its own InMemorySpanExporter-backed provider before app code runs).
    otel_exporter: Literal["console", "otlp", "none"] = "console"
    otel_exporter_otlp_endpoint: str = "http://localhost:4318"

    # --- LLM provider (agent reasoning) ---
    llm_provider: str = "ollama"
    llm_model: str = "qwen3:8b"
    ollama_base_url: str = "http://localhost:11434"
    llm_request_timeout_seconds: float = 120.0

    # --- Embedding provider (MemoryService semantic search only — NOT RAG, NOT the agent LLM) ---
    embedding_provider: str = "bge"
    embedding_model: str = "BAAI/bge-m3"
    embedding_device: str = "cpu"

    # --- High-impact thresholds (Constitution IV / spec Assumptions) ---
    # Reservation cancellation/modification at or above this party size requires approval.
    reservation_high_impact_party_size: int = 6
    default_reservation_duration_minutes: int = 90
    # Inventory write-offs (waste/correction) at or above this quantity require approval.
    # No per-unit cost field exists on InventoryItem in the MVP schema, so this is a
    # quantity threshold rather than a dollar-value one — documented simplification.
    inventory_high_impact_quantity_threshold: float = 10.0
    # Purchase requests at or above this estimated cost require approval.
    purchase_request_high_impact_cost_threshold: float = 200.0
    # Staffing: any change to an already-published shift is high-impact (binary, no
    # threshold needed — see StaffShift.is_published).

    # --- Deterministic business-rule defaults used by calculation tools ---
    # calculate_staff_requirement: servers scale with covers, plus fixed roles.
    covers_per_server: int = 15
    covers_per_cook: int = 20
    minimum_servers_per_shift: int = 1
    minimum_cooks_per_shift: int = 1
    # calculate_required_inventory: lookback window used to estimate daily usage rate.
    inventory_usage_lookback_days: int = 14


@lru_cache
def get_settings() -> Settings:
    """Process-wide cached Settings instance."""
    return Settings()  # type: ignore[call-arg]
