"""Test-session-wide setup.

The whole automated suite runs against a dedicated `dineops_test` database (never the
`dineops` dev database with its seeded demo data) — DATABASE_URL is overridden here,
before anything imports app.core.config, so pydantic-settings picks it up in place of
whatever `.env` has (real env vars outrank `.env` file values).
"""

import os

os.environ.setdefault(
    "DATABASE_URL",
    "postgresql+asyncpg://dineops:dineops_dev_password@localhost:5432/dineops_test",
)

import pytest
import pytest_asyncio
from alembic.config import Config
from opentelemetry import trace
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from sqlalchemy.ext.asyncio import AsyncSession

from alembic import command

# One real SDK TracerProvider for the whole test session, backed by an in-memory
# exporter instead of the app's normal console/OTLP exporter. Installed at import
# time (before any app module runs) so app.core.telemetry.configure_telemetry() sees
# a real provider already in place and is a no-op for every create_app()/script the
# suite exercises — tests get every span the app would otherwise print/ship, just
# captured for assertions instead. `set_tracer_provider` only succeeds once per
# process, which is exactly what "one provider for the whole session" needs.
_otel_test_exporter = InMemorySpanExporter()
_otel_test_provider = TracerProvider(resource=Resource.create({"service.name": "dineops-test"}))
_otel_test_provider.add_span_processor(SimpleSpanProcessor(_otel_test_exporter))
trace.set_tracer_provider(_otel_test_provider)


@pytest.fixture
def otel_spans():
    """Spans captured during this test only — cleared before and after so tests
    never see spans left over from a previous or concurrent test."""
    _otel_test_exporter.clear()
    yield _otel_test_exporter
    _otel_test_exporter.clear()


@pytest.fixture(autouse=True)
def _clear_settings_cache():
    """Settings/provider factories are @lru_cache'd for prod use; tests that patch
    env vars need a clean slate before and after."""
    from app.core.config import get_settings
    from app.embeddings.factory import get_embedding_provider
    from app.llm.factory import get_llm_provider

    get_settings.cache_clear()
    get_llm_provider.cache_clear()
    get_embedding_provider.cache_clear()
    yield
    get_settings.cache_clear()
    get_llm_provider.cache_clear()
    get_embedding_provider.cache_clear()


def _alembic_config() -> Config:
    from app.core.config import get_settings

    repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    cfg = Config(os.path.join(repo_root, "alembic.ini"))
    cfg.set_main_option("script_location", os.path.join(repo_root, "alembic"))
    cfg.set_main_option("sqlalchemy.url", get_settings().database_url)
    return cfg


@pytest.fixture(scope="session")
def _migrated_test_db():
    """Ensures the test database schema is at `head` once per test session."""
    command.upgrade(_alembic_config(), "head")
    yield
    from app.core.db import get_engine

    get_engine().sync_engine.dispose()


@pytest_asyncio.fixture
async def db_session(_migrated_test_db) -> AsyncSession:
    """A session-per-test wrapped in a transaction that's always rolled back, so
    tests never leave data behind for each other — even ones that insert Memory
    rows with embeddings or trip CHECK constraints deliberately."""
    from app.core.db import get_engine

    engine = get_engine()
    async with engine.connect() as connection:
        transaction = await connection.begin()
        session = AsyncSession(bind=connection, join_transaction_mode="create_savepoint")
        try:
            yield session
        finally:
            await session.close()
            await transaction.rollback()
