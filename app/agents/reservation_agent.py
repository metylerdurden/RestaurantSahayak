"""ReservationAgent: the reservation-domain specialist. All loop mechanics live in
ToolCallingAgent — this class only fixes the agent's name, system prompt, and (via
the caller-supplied `tools` list) which typed tools it may use."""

from __future__ import annotations

from app.agents.prompts import RESERVATION_AGENT_SYSTEM_PROMPT
from app.agents.tool_calling_agent import ToolCallingAgent
from app.llm.base import LLMProvider
from app.services.agent_run_service import AgentRunService
from app.tools.base import Tool


class ReservationAgent(ToolCallingAgent):
    def __init__(
        self,
        *,
        llm: LLMProvider,
        tools: list[Tool],
        agent_run_service: AgentRunService,
        max_iterations: int = 8,
    ) -> None:
        super().__init__(
            name="reservation",
            system_prompt=RESERVATION_AGENT_SYSTEM_PROMPT,
            llm=llm,
            tools=tools,
            agent_run_service=agent_run_service,
            max_iterations=max_iterations,
        )
