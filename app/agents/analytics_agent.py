"""AnalyticsAgent: the analytics-domain specialist. All loop mechanics live in
ToolCallingAgent — this class only fixes the agent's name, system prompt, and (via
the caller-supplied `tools` list) which typed tools it may use.

    AnalyticsAgent -> LLMProvider -> Qwen3-8B -> typed Tools (get_daily_sales,
    get_item_sales, get_no_show_rate) -> AnalyticsService -> Repositories -> PostgreSQL

Comparing periods, spotting patterns, and separating facts from conclusions is
Qwen3-8B's reasoning over the three tools' results — there is no tool that computes a
comparison or a trend for it. No fourth tool is added: the three requested tools
(each already able to take any date range, so a comparison is just two calls) are
architecturally sufficient for every example request this agent needs to handle.
"""

from __future__ import annotations

from app.agents.prompts import ANALYTICS_AGENT_SYSTEM_PROMPT
from app.agents.tool_calling_agent import ToolCallingAgent
from app.llm.base import LLMProvider
from app.services.agent_run_service import AgentRunService
from app.tools.base import Tool


class AnalyticsAgent(ToolCallingAgent):
    def __init__(
        self,
        *,
        llm: LLMProvider,
        tools: list[Tool],
        agent_run_service: AgentRunService,
        max_iterations: int = 8,
    ) -> None:
        super().__init__(
            name="analytics",
            system_prompt=ANALYTICS_AGENT_SYSTEM_PROMPT,
            llm=llm,
            tools=tools,
            agent_run_service=agent_run_service,
            max_iterations=max_iterations,
        )
