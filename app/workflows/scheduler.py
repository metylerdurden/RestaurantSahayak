"""Scheduler: minimal in-process periodic-job abstraction for autonomous background
workflows. `Scheduler` is a small ABC specifically so it can later be swapped for a
production job system (Celery beat, APScheduler with a durable store, a cloud
scheduler firing into a queue) without any BackgroundWorkflow or caller needing to
change. AsyncIOScheduler is the local-development/MVP implementation: plain asyncio
tasks, no persistence across restarts, a fixed interval per job (not calendar-aware —
a production scheduler would add "every morning at 7am"-style triggers behind the
same interface; documented as a real gap, not hidden).
"""

from __future__ import annotations

import asyncio
from abc import ABC, abstractmethod
from typing import Any, Awaitable, Callable

from app.core.logging import get_logger

_logger = get_logger(__name__)

JobFunc = Callable[[], Awaitable[Any]]


class Scheduler(ABC):
    @abstractmethod
    def register(self, job_id: str, *, interval_seconds: float, func: JobFunc) -> None:
        """Register a job to run every `interval_seconds` once the scheduler starts."""

    @abstractmethod
    async def start(self) -> None:
        """Begin running every registered job on its own interval, concurrently."""

    @abstractmethod
    async def stop(self) -> None:
        """Cancel all running jobs and wait for them to unwind cleanly."""


class AsyncIOScheduler(Scheduler):
    def __init__(self, *, run_immediately: bool = True) -> None:
        self._jobs: dict[str, tuple[float, JobFunc]] = {}
        self._tasks: dict[str, asyncio.Task] = {}
        self._run_immediately = run_immediately

    def register(self, job_id: str, *, interval_seconds: float, func: JobFunc) -> None:
        if job_id in self._jobs:
            raise ValueError(f"A job named {job_id!r} is already registered")
        self._jobs[job_id] = (interval_seconds, func)

    async def start(self) -> None:
        for job_id, (interval, func) in self._jobs.items():
            self._tasks[job_id] = asyncio.create_task(self._loop(job_id, interval, func), name=f"scheduler:{job_id}")
        _logger.info("scheduler.started", job_ids=list(self._jobs))

    async def stop(self) -> None:
        for task in self._tasks.values():
            task.cancel()
        await asyncio.gather(*self._tasks.values(), return_exceptions=True)
        self._tasks.clear()
        _logger.info("scheduler.stopped")

    async def _loop(self, job_id: str, interval_seconds: float, func: JobFunc) -> None:
        if not self._run_immediately:
            await asyncio.sleep(interval_seconds)
        while True:
            try:
                await func()
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # one bad tick must never kill the whole scheduler loop
                _logger.error("scheduler.job_failed", job_id=job_id, error=str(exc), exc_info=True)
            await asyncio.sleep(interval_seconds)
