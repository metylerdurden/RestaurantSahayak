"""Shared repository helpers.

Repositories are the ONLY layer permitted to import sqlalchemy/app.models. Every
repository takes an AsyncSession at construction and does nothing beyond translating
domain calls into queries/mutations — no business rules, no approval/event logic.
"""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession


class BaseRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session
