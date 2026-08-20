"""app.core.db.get_db_session — the actual FastAPI dependency used in production.
Every other test in this suite overrides it with tests/conftest.py's own
transaction/savepoint-based `db_session` fixture (deliberately, for test isolation —
see that fixture's docstring), so the real function's commit/rollback contract was
never directly exercised anywhere. This closes that gap: commit on a clean
completion, rollback (and re-raise, never swallow) on an exception raised while the
session was in use — e.g. an unhandled error inside a route handler."""

from __future__ import annotations

from contextlib import asynccontextmanager
from unittest.mock import AsyncMock

import pytest

import app.core.db as db_module


def _patch_session_factory(monkeypatch, session: AsyncMock) -> None:
    @asynccontextmanager
    async def _session_context():
        yield session

    monkeypatch.setattr(db_module, "get_session_factory", lambda: lambda: _session_context())


@pytest.mark.asyncio
async def test_get_db_session_commits_on_clean_completion(monkeypatch):
    session = AsyncMock()
    _patch_session_factory(monkeypatch, session)

    async for yielded in db_module.get_db_session():
        assert yielded is session

    session.commit.assert_awaited_once()
    session.rollback.assert_not_awaited()


@pytest.mark.asyncio
async def test_get_db_session_rolls_back_and_reraises_on_exception(monkeypatch):
    """Simulates what FastAPI does when a route handler raises while this
    dependency's session is in use: the exception is thrown back into the
    generator at the yield point."""
    session = AsyncMock()
    _patch_session_factory(monkeypatch, session)

    gen = db_module.get_db_session()
    await gen.__anext__()

    with pytest.raises(RuntimeError, match="route handler blew up"):
        await gen.athrow(RuntimeError("route handler blew up"))

    session.rollback.assert_awaited_once()
    session.commit.assert_not_awaited()
