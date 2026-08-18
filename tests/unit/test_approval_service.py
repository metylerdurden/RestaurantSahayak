"""ApprovalService state machine against a mocked repository — no database. Real
persistence/constraint behavior is covered by the domain integration tests (each of
which exercises a full propose -> decide round trip against real Postgres)."""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock

import pytest

from app.repositories.approval_repo import ApprovalRepository
from app.services.approval_service import ApprovalService
from app.tools.base import ToolError

RESTAURANT_ID = uuid.uuid4()


def _approval(**overrides):
    defaults = dict(id=uuid.uuid4(), status="pending")
    defaults.update(overrides)
    return type("Approval", (), defaults)()


@pytest.mark.asyncio
async def test_propose_creates_a_pending_approval():
    repo = AsyncMock(spec=ApprovalRepository)
    repo.create.return_value = _approval()
    service = ApprovalService(repo)

    await service.propose(
        restaurant_id=RESTAURANT_ID,
        domain="reservation",
        proposed_by_tool="cancel_reservation",
        proposed_by_agent_run_id=uuid.uuid4(),
        proposed_action={"action": "cancel_reservation"},
        summary="Cancel a large party",
    )
    repo.create.assert_called_once()
    assert repo.create.call_args.kwargs["status"] == "pending"


@pytest.mark.asyncio
async def test_decide_approve_transitions_status():
    approval = _approval(status="pending")
    repo = AsyncMock(spec=ApprovalRepository)
    repo.get_by_id.return_value = approval
    repo.save.side_effect = lambda a: a
    service = ApprovalService(repo)

    result = await service.decide(approval.id, "approved", uuid.uuid4())
    assert result.status == "approved"
    assert result.decided_at is not None


@pytest.mark.asyncio
async def test_decide_unknown_approval_raises():
    repo = AsyncMock(spec=ApprovalRepository)
    repo.get_by_id.return_value = None
    service = ApprovalService(repo)

    with pytest.raises(ToolError) as exc_info:
        await service.decide(uuid.uuid4(), "approved", uuid.uuid4())
    assert exc_info.value.code == "approval_not_found"


@pytest.mark.asyncio
async def test_decide_already_decided_raises():
    approval = _approval(status="approved")
    repo = AsyncMock(spec=ApprovalRepository)
    repo.get_by_id.return_value = approval
    service = ApprovalService(repo)

    with pytest.raises(ToolError) as exc_info:
        await service.decide(approval.id, "rejected", uuid.uuid4())
    assert exc_info.value.code == "approval_already_decided"


@pytest.mark.asyncio
async def test_expire_overdue_marks_matching_approvals():
    overdue = [_approval(status="pending"), _approval(status="pending")]
    repo = AsyncMock(spec=ApprovalRepository)
    repo.list_overdue.return_value = overdue
    service = ApprovalService(repo)

    result = await service.expire_overdue(RESTAURANT_ID)
    assert all(a.status == "expired" for a in result)
