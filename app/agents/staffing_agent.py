"""StaffingAgent: the staffing-domain specialist. All loop mechanics live in
ToolCallingAgent — this class only fixes the agent's name, system prompt, and (via
the caller-supplied `tools` list) which typed tools it may use.

    StaffingAgent -> LLMProvider -> Qwen3-8B -> typed Tools (get_staff_schedule,
    get_staff_availability, calculate_staff_requirement) -> StaffingService ->
    Repositories -> PostgreSQL

Comparing required staffing against what's actually scheduled, and deciding whether
that's a shortage, adequate, or excess capacity, is Qwen3-8B's reasoning over the
three tools' results — there is no fourth tool that does the comparison or files a
staffing request on the agent's behalf; the "staffing request" is the concrete
recommendation the agent states in its final answer.
"""

from __future__ import annotations

from app.agents.prompts import STAFFING_AGENT_SYSTEM_PROMPT
from app.agents.tool_calling_agent import ToolCallingAgent
from app.llm.base import LLMProvider
from app.services.agent_run_service import AgentRunService
from app.tools.base import Tool


class StaffingAgent(ToolCallingAgent):
    def __init__(
        self,
        *,
        llm: LLMProvider,
        tools: list[Tool],
        agent_run_service: AgentRunService,
        max_iterations: int = 8,
    ) -> None:
        super().__init__(
            name="staffing",
            system_prompt=STAFFING_AGENT_SYSTEM_PROMPT,
            llm=llm,
            tools=tools,
            agent_run_service=agent_run_service,
            max_iterations=max_iterations,
        )
