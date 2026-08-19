"""AsyncIOScheduler with very short real intervals — proves registration, concurrent
execution of multiple jobs, that one job's repeated failure doesn't stop it (or any
other job) from continuing to tick, and clean start/stop. Intervals are small
(hundredths of a second) so this stays fast without needing a fake clock."""

from __future__ import annotations

import asyncio

import pytest

from app.workflows.scheduler import AsyncIOScheduler


@pytest.mark.asyncio
async def test_register_rejects_duplicate_job_id():
    scheduler = AsyncIOScheduler()

    async def noop():
        pass

    scheduler.register("job-a", interval_seconds=1, func=noop)
    with pytest.raises(ValueError):
        scheduler.register("job-a", interval_seconds=1, func=noop)


@pytest.mark.asyncio
async def test_a_registered_job_runs_immediately_and_repeatedly():
    calls: list[int] = []

    async def tick():
        calls.append(1)

    scheduler = AsyncIOScheduler(run_immediately=True)
    scheduler.register("tick", interval_seconds=0.02, func=tick)

    await scheduler.start()
    await asyncio.sleep(0.09)
    await scheduler.stop()

    assert len(calls) >= 3  # immediate run + a few ticks in ~90ms at a 20ms interval


@pytest.mark.asyncio
async def test_multiple_jobs_run_independently_and_concurrently():
    calls: dict[str, int] = {"a": 0, "b": 0}

    async def job_a():
        calls["a"] += 1

    async def job_b():
        calls["b"] += 1

    scheduler = AsyncIOScheduler()
    scheduler.register("job-a", interval_seconds=0.02, func=job_a)
    scheduler.register("job-b", interval_seconds=0.02, func=job_b)

    await scheduler.start()
    await asyncio.sleep(0.07)
    await scheduler.stop()

    assert calls["a"] >= 2
    assert calls["b"] >= 2


@pytest.mark.asyncio
async def test_a_job_that_always_fails_keeps_ticking_and_does_not_affect_other_jobs():
    attempts = []
    other_calls = []

    async def failing_job():
        attempts.append(1)
        raise RuntimeError("boom")

    async def healthy_job():
        other_calls.append(1)

    scheduler = AsyncIOScheduler()
    scheduler.register("failing", interval_seconds=0.02, func=failing_job)
    scheduler.register("healthy", interval_seconds=0.02, func=healthy_job)

    await scheduler.start()
    await asyncio.sleep(0.07)
    await scheduler.stop()

    assert len(attempts) >= 2  # kept retrying on schedule despite always failing
    assert len(other_calls) >= 2  # unaffected by the other job's failures


@pytest.mark.asyncio
async def test_stop_cancels_jobs_so_they_do_not_keep_running():
    calls: list[int] = []

    async def tick():
        calls.append(1)

    scheduler = AsyncIOScheduler()
    scheduler.register("tick", interval_seconds=0.02, func=tick)

    await scheduler.start()
    await asyncio.sleep(0.05)
    await scheduler.stop()
    count_after_stop = len(calls)
    await asyncio.sleep(0.05)

    assert len(calls) == count_after_stop  # no further ticks after stop()


@pytest.mark.asyncio
async def test_run_immediately_false_waits_one_interval_before_first_tick():
    calls: list[int] = []

    async def tick():
        calls.append(1)

    scheduler = AsyncIOScheduler(run_immediately=False)
    scheduler.register("tick", interval_seconds=0.05, func=tick)

    await scheduler.start()
    await asyncio.sleep(0.01)
    assert calls == []  # hasn't fired yet — still waiting out the first interval
    await asyncio.sleep(0.06)
    await scheduler.stop()
    assert len(calls) >= 1
