"""InventoryAgent: the inventory-domain specialist. All loop mechanics live in
ToolCallingAgent — this class only fixes the agent's name, system prompt, and (via
the caller-supplied `tools` list) which typed tools it may use.

    InventoryAgent -> LLMProvider -> Qwen3-8B -> typed Tools (get_inventory,
    check_stock, calculate_required_inventory, create_purchase_request) ->
    InventoryService -> Repositories -> PostgreSQL

create_purchase_request is high-impact-gated by InventoryService itself
(Constitution IV) exactly like reservation cancellation — the agent doesn't
implement or duplicate that approval logic, it just reports a pending_approval
result truthfully when it gets one.
"""

from __future__ import annotations

from app.agents.prompts import INVENTORY_AGENT_SYSTEM_PROMPT
from app.agents.tool_calling_agent import ToolCallingAgent
from app.llm.base import LLMProvider
from app.services.agent_run_service import AgentRunService
from app.tools.base import Tool


class InventoryAgent(ToolCallingAgent):
    def __init__(
        self,
        *,
        llm: LLMProvider,
        tools: list[Tool],
        agent_run_service: AgentRunService,
        max_iterations: int = 8,
    ) -> None:
        super().__init__(
            name="inventory",
            system_prompt=INVENTORY_AGENT_SYSTEM_PROMPT,
            llm=llm,
            tools=tools,
            agent_run_service=agent_run_service,
            max_iterations=max_iterations,
        )
