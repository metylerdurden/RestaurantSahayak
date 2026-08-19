"""OrchestratorAgent: coordinates the specialist agents. The one agent in this
package built on LangGraph rather than the hand-rolled ToolCallingAgent loop, because
coordinating other agents — decide who's needed, delegate, decide if someone else is
now needed, loop, combine — is a genuinely graph-shaped control problem, not a flat
tool-calling loop.

    Orchestrator -> LangGraph(decide -> delegate -> decide -> ... -> combine)
                 -> specialist.handle() for each ReservationAgent/CustomerAgent/
                    InventoryAgent/StaffingAgent/AnalyticsAgent instance it holds

The orchestrator never imports sqlalchemy/app.models/app.repositories (enforced by
test_layer_boundaries.py) and never touches a specialist's tools or services
directly — its only interface to a specialist is that specialist's own
`.handle(task, ...) -> AgentResult`, exactly the interface a manager would use. It
does not decide *how* a reservation gets booked or a memory gets recalled; it only
decides *which* specialist to ask and *when* it has enough to answer.
"""

from __future__ import annotations

import time
import uuid
from typing import Any, Literal

from pydantic import ValidationError

from langgraph.graph import END, START, StateGraph

from app.agents.orchestrator_state import (
    DelegateDecision,
    FinishDecision,
    OrchestratorGraphState,
    OrchestratorResult,
    SpecialistInvocationRecord,
    SpecialistInvocationState,
)
from app.agents.prompts import ORCHESTRATOR_COMBINE_SYSTEM_PROMPT, ORCHESTRATOR_DECIDE_SYSTEM_PROMPT
from app.agents.tool_calling_agent import ToolCallingAgent
from app.core.logging import get_logger
from app.llm.base import LLMMessage, LLMProvider
from app.services.agent_run_service import AgentRunService
from app.tools.base import utcnow

_logger = get_logger(__name__)

SPECIALIST_DESCRIPTIONS: dict[str, str] = {
    "reservation": (
        "Table reservations — finding availability, creating, modifying, "
        "cancelling, and listing reservations."
    ),
    "customer": (
        "Customer identity and persistent memory — looking up customers and their "
        "history, and recording/recalling long-term preferences and facts about them."
    ),
    "inventory": "Inventory — stock levels, whether items are low or out of stock, and requesting purchases.",
    "staffing": (
        "Staff scheduling — who's scheduled, who's available, and whether a shift "
        "is adequately staffed for expected demand."
    ),
    "analytics": (
        "Historical operational performance — revenue, item sales, no-show rates — "
        "from past data only, not real-time status."
    ),
}

_DELEGATE_SCHEMA = {
    "type": "function",
    "function": {
        "name": "delegate",
        "description": "Send a specific task to one specialist agent and wait for its result.",
        "parameters": {
            "type": "object",
            "properties": {
                "agent_name": {
                    "type": "string",
                    "enum": ["reservation", "customer", "inventory", "staffing", "analytics"],
                },
                "instruction": {
                    "type": "string",
                    "description": "The specific, self-contained task to give that specialist.",
                },
            },
            "required": ["agent_name", "instruction"],
        },
    },
}

_FINISH_SCHEMA = {
    "type": "function",
    "function": {
        "name": "finish",
        "description": "Stop delegating — enough has been learned to answer the manager.",
        "parameters": {"type": "object", "properties": {}},
    },
}


class OrchestratorAgent:
    name = "orchestrator"

    def __init__(
        self,
        *,
        llm: LLMProvider,
        specialists: dict[str, ToolCallingAgent],
        agent_run_service: AgentRunService,
        max_delegations: int = 6,
        max_retries_per_agent: int = 1,
    ) -> None:
        self.llm = llm
        self.specialists = specialists
        self.agent_run_service = agent_run_service
        self.max_delegations = max_delegations
        self.max_retries_per_agent = max_retries_per_agent

        listing = "\n".join(
            f"- {name}: {SPECIALIST_DESCRIPTIONS.get(name, 'no description available')}"
            for name in specialists
        )
        self._decide_system_prompt = ORCHESTRATOR_DECIDE_SYSTEM_PROMPT.format(specialists_listing=listing)
        self._graph = self._build_graph()

    # --- public interface, same shape as ToolCallingAgent.handle() ---

    async def handle(
        self,
        task: str,
        *,
        restaurant_id: uuid.UUID,
        trigger_type: Literal["manager_request", "event"] = "manager_request",
        initiated_by_user_id: uuid.UUID | None = None,
        triggering_event_id: uuid.UUID | None = None,
    ) -> OrchestratorResult:
        started_at = time.monotonic()
        run = await self.agent_run_service.start_run(
            restaurant_id=restaurant_id,
            agent_name=self.name,
            model_name=self.llm.model_name,
            trigger_type=trigger_type,
            initiated_by_user_id=initiated_by_user_id,
            triggering_event_id=triggering_event_id,
        )
        log = _logger.bind(agent_run_id=str(run.id), agent_name=self.name, model=self.llm.model_name)
        log.info("orchestrator.started", task=task)
        await self.agent_run_service.log_message(run_id=run.id, role="user", content={"task": task})

        initial_state: OrchestratorGraphState = {
            "task": task,
            "restaurant_id": str(restaurant_id),
            "trigger_type": trigger_type,
            "initiated_by_user_id": str(initiated_by_user_id) if initiated_by_user_id else None,
            "triggering_event_id": str(triggering_event_id) if triggering_event_id else None,
            "orchestrator_run_id": str(run.id),
            "correlation_id": str(run.correlation_id),
            "invocations": [],
            "remaining_steps": self.max_delegations,
            "next_agent": None,
            "next_instruction": None,
            "finished": False,
            "final_response": None,
            "error": None,
        }

        invocations: list[SpecialistInvocationRecord] = []
        try:
            final_state = await self._graph.ainvoke(initial_state)
            status: Literal["completed", "error"] = "completed"
            summary = final_state.get("final_response") or "Done."
            invocations = [
                SpecialistInvocationRecord(
                    agent_name=inv["agent_name"],
                    task=inv["task"],
                    status=inv["result"]["status"],
                    summary=inv["result"]["summary"],
                    data=inv["result"].get("data"),
                )
                for inv in final_state.get("invocations", [])
            ]
        except Exception as exc:  # the LLM/HTTP/graph boundary — never let this crash the caller
            log.error("orchestrator.unexpected_failure", error=str(exc), exc_info=True)
            status = "error"
            summary = "The orchestrator hit an unexpected internal error and could not complete the request."

        pending_approvals = [inv for inv in invocations if inv.status == "pending_approval"]
        latency_ms = int((time.monotonic() - started_at) * 1000)

        await self.agent_run_service.log_message(run_id=run.id, role="assistant", content={"content": summary})
        await self.agent_run_service.complete_run(
            run_id=run.id,
            status="completed" if status != "error" else "failed",
            outcome_summary=summary,
        )
        log.info("orchestrator.finished", status=status, latency_ms=latency_ms, delegations=len(invocations))

        return OrchestratorResult(
            orchestrator_run_id=run.id,
            status=status,
            summary=summary,
            invocations=invocations,
            pending_approvals=pending_approvals,
            latency_ms=latency_ms,
            error=summary if status == "error" else None,
        )

    # --- LangGraph wiring ---

    def _build_graph(self):
        graph = StateGraph(OrchestratorGraphState)
        graph.add_node("decide", self._decide_node)
        graph.add_node("delegate", self._delegate_node)
        graph.add_node("combine", self._combine_node)
        graph.add_edge(START, "decide")
        graph.add_conditional_edges(
            "decide", self._route_after_decide, {"delegate": "delegate", "combine": "combine"}
        )
        graph.add_conditional_edges(
            "delegate", self._route_after_delegate, {"decide": "decide", "combine": "combine"}
        )
        graph.add_edge("combine", END)
        return graph.compile()

    def _route_after_decide(self, state: OrchestratorGraphState) -> str:
        return "combine" if state.get("finished") or not state.get("next_agent") else "delegate"

    def _route_after_delegate(self, state: OrchestratorGraphState) -> str:
        return "combine" if state["remaining_steps"] <= 0 else "decide"

    # --- nodes ---

    async def _decide_node(self, state: OrchestratorGraphState) -> dict[str, Any]:
        if state["remaining_steps"] <= 0:
            return {"next_agent": None, "next_instruction": None, "finished": True}

        progress_lines = [
            f'- {inv["agent_name"]} (asked: "{inv["task"]}") -> '
            f'status={inv["result"].get("status")}: {inv["result"].get("summary")}'
            for inv in state["invocations"]
        ]
        progress_text = "\n".join(progress_lines) if progress_lines else "(nothing delegated yet)"

        system_content = (
            f"{self._decide_system_prompt}\n\n"
            f"The current date and time (UTC) is {utcnow().isoformat()}."
        )
        user_content = (
            f"Manager's request: {state['task']}\n\n"
            f"Progress so far:\n{progress_text}\n\n"
            "Call delegate(agent_name, instruction) for the next specialist needed, "
            "or finish() if you already have enough to answer."
        )
        messages = [
            LLMMessage(role="system", content=system_content),
            LLMMessage(role="user", content=user_content),
        ]

        response = await self.llm.generate(messages, tools=[_DELEGATE_SCHEMA, _FINISH_SCHEMA], think=False)

        if not response.tool_calls:
            # answered in plain text instead of calling a routing function — nothing
            # more to delegate to, so treat this as done.
            return {"next_agent": None, "next_instruction": None, "finished": True}

        call = response.tool_calls[0]
        if call.name == "finish":
            try:
                FinishDecision.model_validate(call.arguments)
            except ValidationError:
                pass
            return {"next_agent": None, "next_instruction": None, "finished": True}

        if call.name == "delegate":
            try:
                decision = DelegateDecision.model_validate(call.arguments)
            except ValidationError:
                # malformed routing call — don't loop forever on bad model output
                return {"next_agent": None, "next_instruction": None, "finished": True}
            if decision.agent_name not in self.specialists:
                return {"next_agent": None, "next_instruction": None, "finished": True}
            return {
                "next_agent": decision.agent_name,
                "next_instruction": decision.instruction,
                "finished": False,
            }

        return {"next_agent": None, "next_instruction": None, "finished": True}

    async def _delegate_node(self, state: OrchestratorGraphState) -> dict[str, Any]:
        agent_name = state["next_agent"]
        assert agent_name is not None
        instruction = state["next_instruction"] or state["task"]
        specialist = self.specialists[agent_name]

        restaurant_id = uuid.UUID(state["restaurant_id"])
        orchestrator_run_id = uuid.UUID(state["orchestrator_run_id"])
        correlation_id = uuid.UUID(state["correlation_id"])
        initiated_by_user_id = (
            uuid.UUID(state["initiated_by_user_id"]) if state["initiated_by_user_id"] else None
        )
        triggering_event_id = (
            uuid.UUID(state["triggering_event_id"]) if state["triggering_event_id"] else None
        )

        async def _call() -> Any:
            return await specialist.handle(
                instruction,
                restaurant_id=restaurant_id,
                trigger_type=state["trigger_type"],
                initiated_by_user_id=initiated_by_user_id,
                triggering_event_id=triggering_event_id,
                parent_run_id=orchestrator_run_id,
                correlation_id=correlation_id,
            )

        result = await _call()
        attempt = 1
        # Bounded retry: a delegated specialist's own LLM call can fail transiently
        # (e.g. a live-model hiccup); one retry with the same instruction before
        # accepting the failure and moving on.
        while result.status == "error" and attempt <= self.max_retries_per_agent:
            result = await _call()
            attempt += 1

        invocation: SpecialistInvocationState = {
            "agent_name": agent_name,
            "task": instruction,
            "result": result.model_dump(mode="json"),
            "attempt": attempt,
        }
        return {
            "invocations": [invocation],
            "remaining_steps": state["remaining_steps"] - 1,
            "next_agent": None,
            "next_instruction": None,
        }

    async def _combine_node(self, state: OrchestratorGraphState) -> dict[str, Any]:
        if not state["invocations"]:
            return {"final_response": "I wasn't able to determine which specialist should handle this request."}

        blocks = [
            f'{inv["agent_name"]} was asked: "{inv["task"]}"\n'
            f'Result (status={inv["result"].get("status")}): {inv["result"].get("summary")}'
            for inv in state["invocations"]
        ]
        invocations_text = "\n\n".join(blocks)

        messages = [
            LLMMessage(role="system", content=ORCHESTRATOR_COMBINE_SYSTEM_PROMPT),
            LLMMessage(
                role="user",
                content=(
                    f"Manager's original request: {state['task']}\n\n"
                    f"Specialist results:\n\n{invocations_text}\n\n"
                    "Write the final response to the manager."
                ),
            ),
        ]
        response = await self.llm.generate(messages, think=False)
        return {"final_response": (response.content or "").strip() or "Done."}
