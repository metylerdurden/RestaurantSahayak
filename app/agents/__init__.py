"""Agent layer — implemented starting Phase 4.

Each specialist agent is a bounded tool-calling (ReAct-style) loop:
LLMProvider.generate() with a `tools=[...]` schema list, execute whatever the model
called via typed Tool instances, feed results back, repeat until a final answer or an
iteration cap. Hand-rolled rather than LangGraph — a single specialist agent doesn't
need a graph runtime, and the loop shape (app.agents.state.AgentState/AgentResult) is
the stable seam OrchestratorAgent's LangGraph graph sits behind: the orchestrator
calls each specialist's `.handle()` exactly like a manager would, never touching its
tools/services directly.

OrchestratorAgent is the one agent that does use LangGraph (app.agents.orchestrator):
its job is coordinating *specialist agents*, not calling typed tools itself, which is
a genuinely graph-shaped problem (decide which agent is needed, delegate, decide
whether another is needed, loop, combine) rather than a flat ReAct loop.

Rule (Constitution I, enforced by tests/unit/test_layer_boundaries.py): nothing in this
package may import sqlalchemy, app.models, or app.repositories. Agents call tools only.
"""

from app.agents.analytics_agent import AnalyticsAgent
from app.agents.customer_agent import CustomerAgent
from app.agents.inventory_agent import InventoryAgent
from app.agents.orchestrator_agent import OrchestratorAgent
from app.agents.reservation_agent import ReservationAgent
from app.agents.staffing_agent import StaffingAgent

__all__ = [
    "ReservationAgent",
    "CustomerAgent",
    "InventoryAgent",
    "StaffingAgent",
    "AnalyticsAgent",
    "OrchestratorAgent",
]
