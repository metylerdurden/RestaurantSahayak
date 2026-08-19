"""Agent layer — implemented starting Phase 4.

Each agent is a bounded tool-calling (ReAct-style) loop: LLMProvider.generate() with a
`tools=[...]` schema list, execute whatever the model calls via typed Tool instances,
feed results back, repeat until a final answer or an iteration cap. Hand-rolled rather
than LangGraph for now — a single specialist agent doesn't need a graph runtime, and
the loop shape (app.agents.state.AgentState/*Result) is the stable seam a LangGraph
graph could sit behind later without touching the Tool/Service/Repository layers.

Rule (Constitution I, enforced by tests/unit/test_layer_boundaries.py): nothing in this
package may import sqlalchemy, app.models, or app.repositories. Agents call tools only.
"""

from app.agents.reservation_agent import ReservationAgent

__all__ = ["ReservationAgent"]
