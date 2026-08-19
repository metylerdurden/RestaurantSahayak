"""Manager API (Step 19): the Approval view — list pending approvals and let the
manager approve/reject them. Calls ApprovalService.approve()/reject() directly, the
exact same deterministic, non-LLM decision path every other approval channel in
this codebase uses (Constitution IV) — this route does not, and structurally
cannot, execute a gated action itself."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_db_session
from app.api.stack import build_agent_stack
from app.schemas.approval import ApprovalDTO, DecideApprovalInput
from app.tools.base import ToolError

router = APIRouter(prefix="/api/v1", tags=["approvals"])


@router.get("/approvals", response_model=list[ApprovalDTO])
async def list_pending_approvals(
    restaurant_id: uuid.UUID,
    session: AsyncSession = Depends(get_db_session),
) -> list[ApprovalDTO]:
    stack = build_agent_stack(session)
    approvals = await stack.approval_service.get_pending_approvals(restaurant_id)
    return [ApprovalDTO.model_validate(a) for a in approvals]


@router.post("/approvals/{approval_id}/approve", response_model=ApprovalDTO)
async def approve_approval(
    approval_id: uuid.UUID,
    body: DecideApprovalInput,
    session: AsyncSession = Depends(get_db_session),
) -> ApprovalDTO:
    stack = build_agent_stack(session)
    try:
        approval = await stack.approval_service.approve(approval_id, body.decided_by_user_id)
    except ToolError as exc:
        raise HTTPException(status_code=409, detail=exc.message) from exc
    return ApprovalDTO.model_validate(approval)


@router.post("/approvals/{approval_id}/reject", response_model=ApprovalDTO)
async def reject_approval(
    approval_id: uuid.UUID,
    body: DecideApprovalInput,
    session: AsyncSession = Depends(get_db_session),
) -> ApprovalDTO:
    stack = build_agent_stack(session)
    try:
        approval = await stack.approval_service.reject(approval_id, body.decided_by_user_id, reason=body.reason)
    except ToolError as exc:
        raise HTTPException(status_code=409, detail=exc.message) from exc
    return ApprovalDTO.model_validate(approval)
