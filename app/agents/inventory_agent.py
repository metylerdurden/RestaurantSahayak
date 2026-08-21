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
from app.agents.state import AgentResult
from app.agents.tool_calling_agent import ToolCallingAgent
from app.llm.base import LLMProvider
from app.schemas.inventory import InventoryRunSummary
from app.services.agent_run_service import AgentRunService
from app.tools.base import Tool


def summarize_inventory_run(result: AgentResult) -> InventoryRunSummary:
    """Derives observations/recommendations/actions_taken/pending_approvals purely
    from the run's own recorded tool calls (AgentResult.tool_calls) — never from the
    model's prose, and never invented. Additive: does not change what handle()
    returns, so every other agent's AgentResult contract is untouched; a caller that
    wants this structured view calls this separately."""
    observations: list[dict] = []
    recommendations: list[dict] = []
    actions_taken: list[dict] = []
    pending_approvals: list[dict] = []

    for call in result.tool_calls:
        output = call.output
        if call.tool_name == "analyze_inventory":
            for alert in output.get("alerts", []):
                item = alert.get("item", {})
                observations.append(
                    {
                        "item_id": item.get("id"),
                        "item_name": item.get("name"),
                        "unit": item.get("unit"),
                        "quantity_on_hand": item.get("quantity_on_hand"),
                        "low_stock_threshold": item.get("low_stock_threshold"),
                        "status": alert.get("status"),
                        "severity": alert.get("severity"),
                    }
                )
                if alert.get("action_required") and alert.get("recommended_reorder_quantity") is not None:
                    recommendations.append(
                        {
                            "item_id": item.get("id"),
                            "item_name": item.get("name"),
                            "recommended_order_quantity": alert.get("recommended_reorder_quantity"),
                            "unit": item.get("unit"),
                        }
                    )
        elif call.tool_name in ("calculate_reorder_quantity", "calculate_required_inventory"):
            recommendations.append(
                {
                    "item_id": output.get("item_id") or (output.get("item") or {}).get("id"),
                    "item_name": output.get("item_name") or (output.get("item") or {}).get("name"),
                    "recommended_order_quantity": output.get("recommended_order_quantity"),
                    "unit": output.get("unit") or (output.get("item") or {}).get("unit"),
                }
            )
        elif call.tool_name == "create_purchase_request":
            if output.get("status") == "pending_approval":
                pending_approvals.append({"approval_id": output.get("approval_id"), "summary": output.get("summary")})
            else:
                purchase_request = output.get("purchase_request", {})
                actions_taken.append(
                    {
                        "purchase_request_id": purchase_request.get("id"),
                        "item_id": purchase_request.get("item_id"),
                        "requested_quantity": purchase_request.get("requested_quantity"),
                        "status": purchase_request.get("status"),
                    }
                )

    return InventoryRunSummary(
        observations=observations,
        recommendations=recommendations,
        actions_taken=actions_taken,
        pending_approvals=pending_approvals,
    )


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
