"""DashboardService: assembles the one-screen operational snapshot the Manager API's
`GET /api/v1/dashboard` (Step 19) serves. Purely a read-side aggregator — it calls
existing domain services/repositories and shapes their results into
`DashboardResponse`; it contains no business rules of its own (alert thresholds,
understaffed detection, etc. all stay exactly where they already lived).

Deliberately does NOT call any LLM: the "daily briefing" panel is the most recent
already-computed `DailyBriefingWorkflow` run's result, not a fresh agent invocation on
every dashboard load (that belongs to the explicit
`POST /api/v1/workflows/daily-briefing` trigger).
"""

from __future__ import annotations

import uuid
from datetime import datetime, time, timezone

from app.repositories.event_repo import EventRepository
from app.repositories.reservation_repo import ACTIVE_RESERVATION_STATUSES
from app.repositories.user_repo import UserRepository
from app.repositories.workflow_run_repo import WorkflowRunRepository
from app.schemas.approval import ApprovalDTO
from app.schemas.dashboard import DashboardResponse, EventSummaryDTO
from app.schemas.inventory import InventoryItemDTO
from app.schemas.reservation import ReservationDTO
from app.schemas.staffing import ShiftAssignmentDTO, StaffShiftDTO
from app.schemas.workflow_run import AgentRunSummaryDTO
from app.services.agent_activity_service import AgentActivityService
from app.services.approval_service import ApprovalService
from app.services.inventory_service import InventoryService
from app.services.reservation_service import ReservationService
from app.services.staffing_service import StaffingService


def _today_window(now: datetime) -> tuple[datetime, datetime]:
    start = datetime.combine(now.date(), time.min, tzinfo=now.tzinfo)
    end = datetime.combine(now.date(), time.max, tzinfo=now.tzinfo)
    return start, end


class DashboardService:
    def __init__(
        self,
        *,
        reservation_service: ReservationService,
        inventory_service: InventoryService,
        staffing_service: StaffingService,
        approval_service: ApprovalService,
        agent_activity_service: AgentActivityService,
        workflow_run_repo: WorkflowRunRepository,
        event_repo: EventRepository,
        user_repo: UserRepository,
    ) -> None:
        self.reservation_service = reservation_service
        self.inventory_service = inventory_service
        self.staffing_service = staffing_service
        self.approval_service = approval_service
        self.agent_activity_service = agent_activity_service
        self.workflow_run_repo = workflow_run_repo
        self.event_repo = event_repo
        self.user_repo = user_repo

    async def get_dashboard(self, restaurant_id: uuid.UUID) -> DashboardResponse:
        now = datetime.now(timezone.utc)
        day_start, day_end = _today_window(now)

        reservations = await self.reservation_service.get_reservations(
            restaurant_id=restaurant_id, date_from=day_start, date_to=day_end, status=None, customer_id=None
        )
        expected_covers = sum(r.party_size for r in reservations if r.status in ACTIVE_RESERVATION_STATUSES)

        inventory_alerts = await self.inventory_service.list_alerts(restaurant_id=restaurant_id)

        shift_rows = await self.staffing_service.get_staff_schedule(
            restaurant_id=restaurant_id, date_from=day_start, date_to=day_end
        )
        staffing_alerts = [
            StaffShiftDTO(
                id=shift.id,
                start_at=shift.start_at,
                end_at=shift.end_at,
                required_staff_count=shift.required_staff_count,
                is_published=shift.is_published,
                status=shift.status,
                assignments=[
                    ShiftAssignmentDTO(staff_id=staff.id, staff_name=staff.name, staff_role=staff.role)
                    for staff, _assignment in assignments
                ],
            )
            for shift, assignments in shift_rows
            if shift.status == "understaffed"
        ]

        pending_approvals = await self.approval_service.get_pending_approvals(restaurant_id)
        recent_agent_activity = await self.agent_activity_service.list_recent(restaurant_id, limit=10)

        daily_briefing, daily_briefing_generated_at = await self._latest_daily_briefing(restaurant_id)

        recent_events = await self.event_repo.list_recent(restaurant_id, limit=20)
        manager = await self.user_repo.get_first(restaurant_id)

        return DashboardResponse(
            restaurant_id=restaurant_id,
            generated_at=now,
            manager_user_id=manager.id if manager else None,
            today_reservations=[ReservationDTO.model_validate(r) for r in reservations],
            expected_covers=expected_covers,
            inventory_alerts=[InventoryItemDTO.model_validate(i) for i in inventory_alerts],
            staffing_alerts=staffing_alerts,
            pending_approvals=[ApprovalDTO.model_validate(a) for a in pending_approvals],
            recent_agent_activity=[AgentRunSummaryDTO.model_validate(r) for r in recent_agent_activity],
            daily_briefing=daily_briefing,
            daily_briefing_generated_at=daily_briefing_generated_at,
            recent_events=[
                EventSummaryDTO(
                    event_id=e.id,
                    event_type=e.event_type,
                    entity_id=e.entity_id,
                    payload=e.payload,
                    created_at=e.created_at,
                    handled=e.handled,
                )
                for e in recent_events
            ],
        )

    async def _latest_daily_briefing(self, restaurant_id: uuid.UUID) -> tuple[str | None, datetime | None]:
        runs = await self.workflow_run_repo.list_recent(restaurant_id, workflow_type="daily_briefing", limit=1)
        if not runs or runs[0].status != "completed" or not runs[0].final_result:
            return None, None
        return runs[0].final_result.get("briefing"), runs[0].completed_at
