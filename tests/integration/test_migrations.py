"""Migration reversibility: upgrade -> downgrade -> upgrade round-trips cleanly
against the test database.

Deliberately a *synchronous* test, not `async def`: Alembic's `command.upgrade`/
`command.downgrade` call `asyncio.run()` internally (see alembic/env.py), which
raises if invoked from within an already-running event loop — so this can't share
the session-scoped async loop the rest of the suite uses. It also uses its own
short-lived engine for table introspection rather than `app.core.db.get_engine()`,
since that engine is bound to the shared async test loop and asyncpg connections
cannot cross event loops.
"""

from __future__ import annotations

import asyncio

from alembic import command
from sqlalchemy import inspect
from sqlalchemy.ext.asyncio import create_async_engine

from app.core.config import get_settings
from app.models import Base
from tests.conftest import _alembic_config


async def _table_names() -> set[str]:
    engine = create_async_engine(get_settings().database_url)
    try:
        async with engine.connect() as connection:
            return set(await connection.run_sync(lambda sync_conn: inspect(sync_conn).get_table_names()))
    finally:
        await engine.dispose()


def test_migration_round_trip(_migrated_test_db):
    cfg = _alembic_config()

    command.downgrade(cfg, "base")
    remaining = asyncio.run(_table_names())
    assert remaining - {"alembic_version"} == set(), (
        f"Expected only alembic_version to remain after downgrade base, found: {remaining}"
    )

    command.upgrade(cfg, "head")
    tables_after = asyncio.run(_table_names())
    expected_tables = set(Base.metadata.tables.keys())
    assert expected_tables.issubset(tables_after), (
        f"Missing tables after re-upgrade: {expected_tables - tables_after}"
    )
