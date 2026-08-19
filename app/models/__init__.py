"""SQLAlchemy ORM models. Import every model module here so Base.metadata is
complete for Alembic autogenerate and for `Base.metadata.create_all()` in tests."""

from app.models.base import Base
from app.models.restaurant import Restaurant
from app.models.user import User
from app.models.customer import Customer
from app.models.table import Table
from app.models.reservation import Reservation
from app.models.menu_item import MenuItem
from app.models.inventory import InventoryItem, InventoryTransaction
from app.models.staff import Staff
from app.models.staffing import StaffShift, ShiftAssignment
from app.models.sale import Sale
from app.models.purchase_request import PurchaseRequest
from app.models.approval import Approval
from app.models.event import Event
from app.models.agent_run import AgentRun, AgentMessage
from app.models.memory import Memory
from app.models.workflow_run import WorkflowRun

__all__ = [
    "Base",
    "Restaurant",
    "User",
    "Customer",
    "Table",
    "Reservation",
    "MenuItem",
    "InventoryItem",
    "InventoryTransaction",
    "Staff",
    "StaffShift",
    "ShiftAssignment",
    "Sale",
    "PurchaseRequest",
    "Approval",
    "Event",
    "AgentRun",
    "AgentMessage",
    "Memory",
    "WorkflowRun",
]
