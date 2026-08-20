"""ApprovalService state machine, risk-level validation, and the optional
execute+remember pipeline — all against a mocked repository (no database). Real
persistence/execution against real domain services and real Postgres is covered by
tests/integration/test_approval_workflow.py's end-to-end scenarios."""

from __future__ import annotations

import uuid
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.repositories.approval_repo import ApprovalRepository
from app.services.approval_service import ApprovalService
from app.services.memory_service import MemoryService
from app.tools.base import ToolError

RESTAURANT_ID = uuid.uuid4()


def _mock_repo() -> AsyncMock:
    """ApprovalService._execute()/_remember() run their best-effort work inside a
    SAVEPOINT (`self.repo.session.begin_nested()`, Step 20 reliability hardening)
    so a real database-level failure there can't poison the rest of the request's
    transaction — see tests/integration/test_approval_workflow.py for a test that
    exercises that with a real Postgres session. Here, with a mocked repository,
    `.session.begin_nested()` just needs to behave like a real no-op async
    context manager so these unit tests keep testing application-level behavior
    only, not transaction mechanics."""
    repo = AsyncMock(spec=ApprovalRepository)

    @asynccontextmanager
    async def _noop_savepoint():
        yield

    repo.session = SimpleNamespace(begin_nested=_noop_savepoint)
    return repo


def _approval(**overrides):
    defaults = dict(
        id=uuid.uuid4(),
        restaurant_id=RESTAURANT_ID,
        domain="reservation",
        action="cancel_reservation",
        agent_name="reservation",
        proposed_by_agent_run_id=uuid.uuid4(),
        reason="Cancel a large party",
        risk_level="MEDIUM",
        status="pending",
        execution_result=None,
    )
    defaults.update(overrides)
    return type("Approval", (), defaults)()


@pytest.mark.asyncio
async def test_create_approval_request_persists_pending_with_risk_level():
    repo = _mock_repo()
    repo.create.return_value = _approval()
    service = ApprovalService(repo)

    await service.create_approval_request(
        restaurant_id=RESTAURANT_ID,
        domain="reservation",
        action="cancel_reservation",
        agent_name="reservation",
        proposed_by_agent_run_id=uuid.uuid4(),
        parameters={"action": "cancel_reservation"},
        reason="Cancel a large party",
        risk_level="MEDIUM",
    )
    repo.create.assert_called_once()
    kwargs = repo.create.call_args.kwargs
    assert kwargs["status"] == "pending"
    assert kwargs["risk_level"] == "MEDIUM"
    assert kwargs["agent_name"] == "reservation"


@pytest.mark.asyncio
async def test_create_approval_request_rejects_low_risk():
    """LOW-risk actions execute automatically and must never create an approval
    record — this is the service-level guard behind "automatic low-risk action"."""
    repo = _mock_repo()
    service = ApprovalService(repo)

    with pytest.raises(ToolError) as exc_info:
        await service.create_approval_request(
            restaurant_id=RESTAURANT_ID,
            domain="inventory",
            action="get_inventory",
            agent_name="inventory",
            proposed_by_agent_run_id=uuid.uuid4(),
            parameters={},
            reason="Just reading",
            risk_level="LOW",
        )
    assert exc_info.value.code == "low_risk_does_not_require_approval"
    repo.create.assert_not_called()


@pytest.mark.asyncio
async def test_create_approval_request_rejects_invalid_domain():
    repo = _mock_repo()
    service = ApprovalService(repo)
    with pytest.raises(ToolError) as exc_info:
        await service.create_approval_request(
            restaurant_id=RESTAURANT_ID,
            domain="not_a_domain",
            action="x",
            agent_name="x",
            proposed_by_agent_run_id=uuid.uuid4(),
            parameters={},
            reason="x",
            risk_level="MEDIUM",
        )
    assert exc_info.value.code == "invalid_domain"


@pytest.mark.asyncio
async def test_create_approval_request_rejects_invalid_risk_level():
    repo = _mock_repo()
    service = ApprovalService(repo)
    with pytest.raises(ToolError) as exc_info:
        await service.create_approval_request(
            restaurant_id=RESTAURANT_ID,
            domain="reservation",
            action="x",
            agent_name="x",
            proposed_by_agent_run_id=uuid.uuid4(),
            parameters={},
            reason="x",
            risk_level="EXTREME",
        )
    assert exc_info.value.code == "invalid_risk_level"


@pytest.mark.asyncio
async def test_get_pending_approvals_delegates_to_repo():
    repo = _mock_repo()
    pending = [_approval(), _approval()]
    repo.list_pending.return_value = pending
    service = ApprovalService(repo)

    result = await service.get_pending_approvals(RESTAURANT_ID)
    assert result == pending
    repo.list_pending.assert_called_once_with(RESTAURANT_ID)


@pytest.mark.asyncio
async def test_approve_transitions_status_and_sets_approved_at():
    approval = _approval(status="pending")
    repo = _mock_repo()
    repo.get_by_id.return_value = approval
    repo.save.side_effect = lambda a: a
    service = ApprovalService(repo)

    result = await service.approve(approval.id, uuid.uuid4())
    assert result.status == "approved"
    assert result.approved_at is not None


@pytest.mark.asyncio
async def test_approve_unknown_approval_raises_not_found():
    repo = _mock_repo()
    repo.get_by_id.return_value = None
    service = ApprovalService(repo)
    with pytest.raises(ToolError) as exc_info:
        await service.approve(uuid.uuid4(), uuid.uuid4())
    assert exc_info.value.code == "approval_not_found"


@pytest.mark.asyncio
async def test_approve_already_decided_raises():
    approval = _approval(status="approved")
    repo = _mock_repo()
    repo.get_by_id.return_value = approval
    service = ApprovalService(repo)
    with pytest.raises(ToolError) as exc_info:
        await service.approve(approval.id, uuid.uuid4())
    assert exc_info.value.code == "approval_already_decided"


@pytest.mark.asyncio
async def test_approve_expired_approval_raises_distinct_error():
    approval = _approval(status="expired")
    repo = _mock_repo()
    repo.get_by_id.return_value = approval
    service = ApprovalService(repo)
    with pytest.raises(ToolError) as exc_info:
        await service.approve(approval.id, uuid.uuid4())
    assert exc_info.value.code == "approval_expired"


@pytest.mark.asyncio
async def test_reject_transitions_status_and_sets_rejected_at():
    approval = _approval(status="pending")
    repo = _mock_repo()
    repo.get_by_id.return_value = approval
    repo.save.side_effect = lambda a: a
    service = ApprovalService(repo)

    result = await service.reject(approval.id, uuid.uuid4())
    assert result.status == "rejected"
    assert result.rejected_at is not None


@pytest.mark.asyncio
async def test_expire_marks_overdue_pending_approvals():
    overdue = [_approval(status="pending"), _approval(status="pending")]
    repo = _mock_repo()
    repo.list_overdue.return_value = overdue
    service = ApprovalService(repo)

    result = await service.expire(RESTAURANT_ID)
    assert all(a.status == "expired" for a in result)


# --- execution pipeline ---


@pytest.mark.asyncio
async def test_approve_executes_registered_executor_and_records_success():
    approval = _approval(status="pending", domain="reservation")
    repo = _mock_repo()
    repo.get_by_id.return_value = approval
    repo.save.side_effect = lambda a: a

    executor = AsyncMock(return_value={"id": "res-1", "status": "cancelled"})
    service = ApprovalService(repo, executors={"reservation": executor})

    result = await service.approve(approval.id, uuid.uuid4())

    executor.assert_called_once_with(approval)
    assert result.execution_result == {"status": "success", "result": {"id": "res-1", "status": "cancelled"}}
    assert result.executed_at is not None


@pytest.mark.asyncio
async def test_approve_records_failed_execution_without_undoing_the_decision():
    approval = _approval(status="pending", domain="reservation")
    repo = _mock_repo()
    repo.get_by_id.return_value = approval
    repo.save.side_effect = lambda a: a

    async def _failing_executor(_approval):
        raise ToolError("reservation_not_found", "No reservation found")

    service = ApprovalService(repo, executors={"reservation": _failing_executor})

    result = await service.approve(approval.id, uuid.uuid4())

    # The decision itself is not rolled back — a manager did approve it — only
    # execution failed.
    assert result.status == "approved"
    assert result.execution_result["status"] == "failed"
    assert (
        "reservation_not_found" in result.execution_result["error"]
        or "No reservation found" in result.execution_result["error"]
    )


@pytest.mark.asyncio
async def test_approve_without_executor_configured_leaves_execution_result_unset():
    approval = _approval(status="pending", domain="reservation")
    repo = _mock_repo()
    repo.get_by_id.return_value = approval
    repo.save.side_effect = lambda a: a
    service = ApprovalService(repo)  # no executors — preserves original propose/decide-only behavior

    result = await service.approve(approval.id, uuid.uuid4())

    assert result.status == "approved"
    assert result.execution_result is None


# --- memory ---


@pytest.mark.asyncio
async def test_approve_stores_a_past_decision_memory_when_memory_service_configured():
    approval = _approval(status="pending", domain="reservation")
    repo = _mock_repo()
    repo.get_by_id.return_value = approval
    repo.save.side_effect = lambda a: a
    memory_service = AsyncMock(spec=MemoryService)
    service = ApprovalService(repo, memory_service=memory_service)

    await service.approve(approval.id, uuid.uuid4())

    memory_service.add_memory.assert_called_once()
    kwargs = memory_service.add_memory.call_args.kwargs
    assert kwargs["memory_type"] == "PAST_DECISION"
    assert kwargs["topic"] == f"approval_{approval.id}"
    assert kwargs["agent_name"] == approval.agent_name


@pytest.mark.asyncio
async def test_reject_stores_a_memory_too():
    approval = _approval(status="pending", domain="reservation")
    repo = _mock_repo()
    repo.get_by_id.return_value = approval
    repo.save.side_effect = lambda a: a
    memory_service = AsyncMock(spec=MemoryService)
    service = ApprovalService(repo, memory_service=memory_service)

    await service.reject(approval.id, uuid.uuid4(), reason="Manager decided against it")

    memory_service.add_memory.assert_called_once()
    content_text = memory_service.add_memory.call_args.kwargs["content"]["text"]
    assert "rejected" in content_text.lower()
    assert "Manager decided against it" in content_text


@pytest.mark.asyncio
async def test_memory_recording_failure_does_not_break_approve():
    approval = _approval(status="pending", domain="reservation")
    repo = _mock_repo()
    repo.get_by_id.return_value = approval
    repo.save.side_effect = lambda a: a
    memory_service = AsyncMock(spec=MemoryService)
    memory_service.add_memory.side_effect = RuntimeError("embedding provider unavailable")
    service = ApprovalService(repo, memory_service=memory_service)

    result = await service.approve(approval.id, uuid.uuid4())

    assert result.status == "approved"  # unaffected by the memory failure
