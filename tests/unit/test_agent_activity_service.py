"""AgentActivityService against a mocked AgentRunRepository — no database. Proves
the trace tree-building itself: an orchestrator run's specialists nest under it via
parent_run_id, a specialist run requested directly is a valid single-node trace,
and an unknown run raises the not-found error the API route maps to a 404."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.repositories.agent_run_repo import AgentRunRepository
from app.services.agent_activity_service import AgentActivityService
from app.tools.base import ToolError

RESTAURANT_ID = uuid.uuid4()


def _run(**overrides):
    now = datetime.now(timezone.utc)
    defaults = dict(
        id=uuid.uuid4(),
        restaurant_id=RESTAURANT_ID,
        agent_name="orchestrator",
        model_name="fake-model",
        parent_run_id=None,
        correlation_id=uuid.uuid4(),
        trigger_type="manager_request",
        status="completed",
        outcome_summary="Done.",
        started_at=now,
        completed_at=now,
    )
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


def _message(run_id, **overrides):
    now = datetime.now(timezone.utc)
    defaults = dict(
        id=uuid.uuid4(),
        run_id=run_id,
        sequence_number=1,
        role="tool_result",
        tool_name="get_reservations",
        content={"result": "ok"},
        created_at=now,
    )
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


@pytest.mark.asyncio
async def test_get_trace_nests_a_specialist_under_its_orchestrator():
    correlation_id = uuid.uuid4()
    orchestrator_run = _run(agent_name="orchestrator", parent_run_id=None, correlation_id=correlation_id)
    specialist_run = _run(agent_name="reservation", parent_run_id=orchestrator_run.id, correlation_id=correlation_id)

    repo = AsyncMock(spec=AgentRunRepository)
    repo.get_by_id.return_value = orchestrator_run
    repo.list_by_correlation_id.return_value = [orchestrator_run, specialist_run]
    repo.list_messages.side_effect = lambda run_id: (
        [_message(run_id, tool_name="search_memory")] if run_id == specialist_run.id else []
    )

    service = AgentActivityService(repo)
    node = await service.get_trace(orchestrator_run.id)

    assert node.agent_name == "orchestrator"
    assert len(node.children) == 1
    assert node.children[0].agent_name == "reservation"
    assert node.children[0].messages[0].tool_name == "search_memory"


@pytest.mark.asyncio
async def test_get_trace_for_a_specialist_run_requested_directly_has_no_children():
    run = _run(agent_name="staffing", parent_run_id=None)
    repo = AsyncMock(spec=AgentRunRepository)
    repo.get_by_id.return_value = run
    repo.list_by_correlation_id.return_value = [run]
    repo.list_messages.return_value = []

    service = AgentActivityService(repo)
    node = await service.get_trace(run.id)

    assert node.agent_name == "staffing"
    assert node.children == []


@pytest.mark.asyncio
async def test_get_trace_for_unknown_run_raises_not_found():
    repo = AsyncMock(spec=AgentRunRepository)
    repo.get_by_id.return_value = None
    service = AgentActivityService(repo)

    with pytest.raises(ToolError) as exc_info:
        await service.get_trace(uuid.uuid4())
    assert exc_info.value.code == "agent_run_not_found"


@pytest.mark.asyncio
async def test_list_recent_delegates_to_the_repository():
    repo = AsyncMock(spec=AgentRunRepository)
    repo.list_recent.return_value = [_run()]
    service = AgentActivityService(repo)

    runs = await service.list_recent(RESTAURANT_ID, agent_name="orchestrator", limit=5)

    assert len(runs) == 1
    repo.list_recent.assert_called_once_with(RESTAURANT_ID, agent_name="orchestrator", limit=5)
