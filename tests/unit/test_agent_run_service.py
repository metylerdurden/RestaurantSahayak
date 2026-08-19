"""AgentRunService against a mocked repository — no database."""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock

import pytest

from app.repositories.agent_run_repo import AgentRunRepository
from app.services.agent_run_service import AgentRunService
from app.tools.base import ToolError

RESTAURANT_ID = uuid.uuid4()


def _run(**overrides):
    defaults = dict(id=uuid.uuid4(), status="running", outcome_summary=None, completed_at=None)
    defaults.update(overrides)
    return type("AgentRun", (), defaults)()


@pytest.mark.asyncio
async def test_start_run_creates_a_running_run_with_a_fresh_correlation_id():
    repo = AsyncMock(spec=AgentRunRepository)
    repo.create.return_value = _run()
    service = AgentRunService(repo)

    await service.start_run(
        restaurant_id=RESTAURANT_ID,
        agent_name="reservation",
        model_name="qwen3:8b",
        trigger_type="manager_request",
        initiated_by_user_id=uuid.uuid4(),
    )

    repo.create.assert_called_once()
    kwargs = repo.create.call_args.kwargs
    assert kwargs["status"] == "running"
    assert kwargs["model_name"] == "qwen3:8b"
    assert isinstance(kwargs["correlation_id"], uuid.UUID)


@pytest.mark.asyncio
async def test_log_message_assigns_next_sequence_number():
    repo = AsyncMock(spec=AgentRunRepository)
    repo.next_sequence_number.return_value = 3
    repo.add_message.side_effect = lambda **kwargs: kwargs
    service = AgentRunService(repo)

    message = await service.log_message(
        run_id=uuid.uuid4(), role="tool_result", content={"ok": True}, tool_name="get_reservations"
    )

    assert message["sequence_number"] == 3
    assert message["tool_name"] == "get_reservations"


@pytest.mark.asyncio
async def test_complete_run_sets_status_and_completed_at():
    run = _run(status="running")
    repo = AsyncMock(spec=AgentRunRepository)
    repo.get_by_id.return_value = run
    repo.save.side_effect = lambda r: r
    service = AgentRunService(repo)

    result = await service.complete_run(run_id=run.id, status="completed", outcome_summary="All good")

    assert result.status == "completed"
    assert result.outcome_summary == "All good"
    assert result.completed_at is not None


@pytest.mark.asyncio
async def test_complete_run_unknown_run_raises():
    repo = AsyncMock(spec=AgentRunRepository)
    repo.get_by_id.return_value = None
    service = AgentRunService(repo)

    with pytest.raises(ToolError) as exc_info:
        await service.complete_run(run_id=uuid.uuid4(), status="failed", outcome_summary=None)
    assert exc_info.value.code == "agent_run_not_found"
