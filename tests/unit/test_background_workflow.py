"""BackgroundWorkflow against a mocked WorkflowRunRepository — no database, no real
agent. Proves the shared run-tracking machinery every concrete workflow relies on:
status transitions, correlation_id generation and passthrough to _execute(),
triggered_by recording, and that a workflow's own failure is contained rather than
propagated. Each concrete workflow's actual domain logic (which agent it calls, with
what instruction) is covered in tests/integration/test_background_workflows.py."""

from __future__ import annotations

import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.repositories.workflow_run_repo import WorkflowRunRepository
from app.workflows.background_workflow import BackgroundWorkflow

RESTAURANT_ID = uuid.uuid4()


class FakeWorkflow(BackgroundWorkflow):
    workflow_type = "daily_briefing"

    def __init__(self, *, workflow_run_repo, result=None, error: Exception | None = None) -> None:
        super().__init__(workflow_run_repo=workflow_run_repo)
        self._result = result or {"summary": "all good"}
        self._error = error
        self.captured_correlation_id: uuid.UUID | None = None

    async def _execute(self, *, restaurant_id, correlation_id):
        self.captured_correlation_id = correlation_id
        if self._error is not None:
            raise self._error
        return self._result


def _workflow_run_row(**overrides):
    defaults = dict(
        id=uuid.uuid4(),
        workflow_type="daily_briefing",
        restaurant_id=RESTAURANT_ID,
        status="running",
        triggered_by="manual",
        final_result=None,
        error=None,
        completed_at=None,
    )
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


@pytest.fixture
def repo():
    r = AsyncMock(spec=WorkflowRunRepository)
    r.create.side_effect = lambda **kwargs: _workflow_run_row(**kwargs)
    r.save.side_effect = lambda run: run
    return r


@pytest.mark.asyncio
async def test_run_creates_a_running_record_then_marks_completed(repo):
    workflow = FakeWorkflow(workflow_run_repo=repo, result={"briefing": "quiet day ahead"})

    run = await workflow.run(restaurant_id=RESTAURANT_ID, triggered_by="manual")

    repo.create.assert_called_once()
    create_kwargs = repo.create.call_args.kwargs
    assert create_kwargs["status"] == "running"
    assert create_kwargs["workflow_type"] == "daily_briefing"
    assert create_kwargs["triggered_by"] == "manual"
    assert isinstance(create_kwargs["correlation_id"], uuid.UUID)

    assert run.status == "completed"
    assert run.final_result == {"briefing": "quiet day ahead"}
    assert run.completed_at is not None


@pytest.mark.asyncio
async def test_run_passes_the_same_correlation_id_used_to_create_the_record(repo):
    workflow = FakeWorkflow(workflow_run_repo=repo)
    run = await workflow.run(restaurant_id=RESTAURANT_ID)
    assert workflow.captured_correlation_id == run.correlation_id


@pytest.mark.asyncio
async def test_execute_failure_is_contained_and_recorded_not_raised(repo):
    workflow = FakeWorkflow(workflow_run_repo=repo, error=RuntimeError("agent blew up"))

    run = await workflow.run(restaurant_id=RESTAURANT_ID)  # must not raise

    assert run.status == "failed"
    assert "agent blew up" in run.error
    assert run.completed_at is not None


@pytest.mark.asyncio
async def test_triggered_by_scheduler_is_recorded(repo):
    workflow = FakeWorkflow(workflow_run_repo=repo)
    await workflow.run(restaurant_id=RESTAURANT_ID, triggered_by="scheduler")
    assert repo.create.call_args.kwargs["triggered_by"] == "scheduler"


@pytest.mark.asyncio
async def test_default_triggered_by_is_manual(repo):
    workflow = FakeWorkflow(workflow_run_repo=repo)
    await workflow.run(restaurant_id=RESTAURANT_ID)
    assert repo.create.call_args.kwargs["triggered_by"] == "manual"
