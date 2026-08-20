"""OrchestratorAgent: coordinates the specialist agents. The one agent in this
package built on LangGraph rather than the hand-rolled ToolCallingAgent loop, because
coordinating other agents — decide who's needed, delegate, decide if someone else is
now needed, loop, combine — is a genuinely graph-shaped control problem, not a flat
tool-calling loop.

    Orchestrator -> LangGraph(decide -> delegate -> [await_approval] -> decide ->
                    ... -> combine)
                 -> specialist.handle() for each ReservationAgent/CustomerAgent/
                    InventoryAgent/StaffingAgent/AnalyticsAgent instance it holds

The orchestrator never imports sqlalchemy/app.models/app.repositories (enforced by
test_layer_boundaries.py) and never touches a specialist's tools or services
directly — its only interface to a specialist is that specialist's own
`.handle(task, ...) -> AgentResult`, exactly the interface a manager would use. It
does not decide *how* a reservation gets booked or a memory gets recalled; it only
decides *which* specialist to ask and *when* it has enough to answer.

Pause/resume: when a delegated specialist comes back with `status="pending_approval"`,
the graph does not treat that as a normal result to report and move past — it
genuinely pauses (LangGraph's `interrupt()`, backed by an in-memory checkpointer
keyed by the orchestrator's own run id) and `handle()` returns a
`status="pending_approval"` result instead of a final answer. The workflow resumes
only via `resume()`, which records the manager's decision through ApprovalService
(approve()/reject() — the same deterministic, non-LLM path used anywhere else an
approval is decided) and continues the graph from exactly where it paused. Nothing
in this class ever calls a specialist's tools directly to force an approved action
through some other way; execution happens only inside ApprovalService.approve().
"""

from __future__ import annotations

import time
import uuid
from typing import Any, Literal, cast

from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command, interrupt
from pydantic import ValidationError

from app.agents.orchestrator_state import (
    DelegateDecision,
    FinishDecision,
    OrchestratorGraphState,
    OrchestratorResult,
    RequiredDomainsDecision,
    SpecialistInvocationRecord,
    SpecialistInvocationState,
)
from app.agents.prompts import (
    ORCHESTRATOR_COMBINE_SYSTEM_PROMPT,
    ORCHESTRATOR_DECIDE_SYSTEM_PROMPT,
    ORCHESTRATOR_SCOPE_SYSTEM_PROMPT,
)
from app.agents.tool_calling_agent import ToolCallingAgent
from app.core.config import get_settings
from app.core.logging import get_logger
from app.core.telemetry import get_tracer, start_span
from app.llm.base import LLMMessage, LLMProvider
from app.services.agent_run_service import AgentRunService
from app.services.approval_service import ApprovalService
from app.tools.base import ToolError, utcnow

_logger = get_logger(__name__)
_tracer = get_tracer(__name__)

SPECIALIST_DESCRIPTIONS: dict[str, str] = {
    "reservation": (
        "Table reservations — finding availability, creating, modifying, cancelling, and listing reservations."
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

_REQUIRED_DOMAINS_SCHEMA = {
    "type": "function",
    "function": {
        "name": "identify_required_domains",
        "description": "Declare which specialist domains are essential to answer this request completely.",
        "parameters": {
            "type": "object",
            "properties": {
                "domains": {
                    "type": "array",
                    "items": {
                        "type": "string",
                        "enum": ["reservation", "customer", "inventory", "staffing", "analytics"],
                    },
                },
            },
            "required": ["domains"],
        },
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
        approval_service: ApprovalService | None = None,
        max_delegations: int = 6,
        max_retries_per_agent: int = 1,
    ) -> None:
        self.llm = llm
        self.specialists = specialists
        self.agent_run_service = agent_run_service
        self.approval_service = approval_service
        self.max_delegations = max_delegations
        self.max_retries_per_agent = max_retries_per_agent

        listing = "\n".join(
            f"- {name}: {SPECIALIST_DESCRIPTIONS.get(name, 'no description available')}" for name in specialists
        )
        self._decide_system_prompt = ORCHESTRATOR_DECIDE_SYSTEM_PROMPT.format(specialists_listing=listing)
        self._scope_system_prompt = ORCHESTRATOR_SCOPE_SYSTEM_PROMPT.format(specialists_listing=listing)
        # In-memory checkpointer: state for a paused workflow lives only in this
        # OrchestratorAgent instance/process for now — the same simplification every
        # other subsystem here makes (e.g. the LLM/embedding providers are also
        # process-local). A persistent checkpointer would be a drop-in swap here for
        # durability across restarts, not a redesign.
        self._checkpointer = InMemorySaver()
        self._graph = self._build_graph()

    # --- public interface ---

    async def handle(
        self,
        task: str,
        *,
        restaurant_id: uuid.UUID,
        trigger_type: Literal["manager_request", "event", "scheduled"] = "manager_request",
        initiated_by_user_id: uuid.UUID | None = None,
        triggering_event_id: uuid.UUID | None = None,
        correlation_id: uuid.UUID | None = None,
    ) -> OrchestratorResult:
        """`correlation_id` lets a caller (a background workflow) thread its own id
        through the orchestrator's own run and every specialist it delegates to, the
        same way OrchestratorAgent already does for the specialists it calls — a
        no-op (a fresh one is generated) when not given, as before."""
        started_at = time.monotonic()
        run = await self.agent_run_service.start_run(
            restaurant_id=restaurant_id,
            agent_name=self.name,
            model_name=self.llm.model_name,
            trigger_type=trigger_type,
            initiated_by_user_id=initiated_by_user_id,
            triggering_event_id=triggering_event_id,
            correlation_id=correlation_id,
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
            "required_domains": [],
            "remaining_steps": self.max_delegations,
            "next_agent": None,
            "next_instruction": None,
            "finished": False,
            "final_response": None,
            "error": None,
            "pending_approval_id": None,
            "pending_approval_agent": None,
        }
        config = {"configurable": {"thread_id": str(run.id)}}

        with start_span(
            _tracer,
            "orchestrator.run",
            agent_name=self.name,
            model_name=self.llm.model_name,
            model_provider=get_settings().llm_provider,
            restaurant_id=str(restaurant_id),
            correlation_id=str(run.correlation_id),
            trigger_type=trigger_type,
        ) as span:
            try:
                final_state = await self._graph.ainvoke(initial_state, config=config)
            except Exception as exc:  # the LLM/HTTP/graph boundary — never let this crash the caller
                log.error("orchestrator.unexpected_failure", error=str(exc), exc_info=True)
                span.set_attribute("success", False)
                return await self._finalize_error(run.id, started_at, log)

            result = await self._finalize(final_state, run.id, started_at, log)
            span.set_attribute("success", result.status not in ("error",))
            span.set_attribute("status", result.status)
            span.set_attribute("latency_ms", result.latency_ms)
            span.set_attribute("delegation_count", len(result.invocations))
            return result

    async def resume(
        self,
        orchestrator_run_id: uuid.UUID,
        *,
        decision: Literal["approved", "rejected"],
        decided_by_user_id: uuid.UUID | None = None,
    ) -> OrchestratorResult:
        """Continues a workflow paused by handle() returning status="pending_approval".
        Must be called on the same OrchestratorAgent instance that paused it (the
        in-memory checkpointer is scoped to this instance)."""
        started_at = time.monotonic()
        log = _logger.bind(agent_run_id=str(orchestrator_run_id), agent_name=self.name)
        config = {"configurable": {"thread_id": str(orchestrator_run_id)}}

        # Best-effort — the paused workflow's own state (persisted by the in-memory
        # checkpointer) already has restaurant_id/correlation_id, so pull them for
        # span attributes rather than requiring the caller to pass them again.
        snapshot = await self._graph.aget_state(config)
        snapshot_values = snapshot.values if snapshot else {}

        with start_span(
            _tracer,
            "orchestrator.run",
            agent_name=self.name,
            model_name=self.llm.model_name,
            model_provider=get_settings().llm_provider,
            restaurant_id=snapshot_values.get("restaurant_id"),
            correlation_id=snapshot_values.get("correlation_id"),
            resumed=True,
        ) as span:
            try:
                final_state = await self._graph.ainvoke(
                    Command(
                        resume={
                            "decision": decision,
                            "decided_by_user_id": str(decided_by_user_id) if decided_by_user_id else None,
                        }
                    ),
                    config=config,
                )
            except Exception as exc:
                log.error("orchestrator.unexpected_failure", error=str(exc), exc_info=True)
                span.set_attribute("success", False)
                return await self._finalize_error(orchestrator_run_id, started_at, log)

            result = await self._finalize(final_state, orchestrator_run_id, started_at, log)
            span.set_attribute("success", result.status not in ("error",))
            span.set_attribute("status", result.status)
            span.set_attribute("latency_ms", result.latency_ms)
            return result

    # --- shared result-building ---

    async def _record_message(self, *, run_id: uuid.UUID, content: dict[str, Any], log: Any) -> None:
        """Best-effort: persisting a trace message must never stop handle()/resume()
        from returning a result to the caller — e.g. if the database connection
        was lost partway through, the OrchestratorResult already built in memory
        is still correct and should still reach the caller."""
        try:
            await self.agent_run_service.log_message(run_id=run_id, role="assistant", content=content)
        except Exception as exc:
            log.error("orchestrator.message_recording_failed", error=str(exc), exc_info=True)

    async def _record_completion(
        self, *, run_id: uuid.UUID, status: Literal["completed", "failed"], summary: str, log: Any
    ) -> None:
        """Same rationale as _record_message — this is the terminal-state write,
        so it matters even more that a failure here degrades gracefully (the run's
        AgentRun row stays "running" forever, a known gap, rather than the caller
        never getting an answer at all)."""
        try:
            await self.agent_run_service.complete_run(run_id=run_id, status=status, outcome_summary=summary)
        except Exception as exc:
            log.error("orchestrator.completion_recording_failed", error=str(exc), exc_info=True)

    async def _finalize(
        self, final_state: dict[str, Any], run_id: uuid.UUID, started_at: float, log: Any
    ) -> OrchestratorResult:
        latency_ms = int((time.monotonic() - started_at) * 1000)
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
        pending_approvals = [inv for inv in invocations if inv.status == "pending_approval"]

        if "__interrupt__" in final_state:
            payload = final_state["__interrupt__"][0].value
            summary = (
                f"Waiting for manager approval on a {payload.get('agent_name', 'specialist')} action "
                f"(approval_id={payload.get('approval_id')}) before continuing."
            )
            log.info("orchestrator.paused_for_approval", approval_id=payload.get("approval_id"))
            await self._record_message(run_id=run_id, content={"pending_approval": payload}, log=log)
            # Deliberately does NOT call complete_run() — the AgentRun stays
            # "running" because the workflow itself is still in progress, just
            # paused, not finished.
            return OrchestratorResult(
                orchestrator_run_id=run_id,
                status="pending_approval",
                summary=summary,
                invocations=invocations,
                pending_approvals=pending_approvals,
                latency_ms=latency_ms,
                awaiting_approval_id=uuid.UUID(payload["approval_id"]) if payload.get("approval_id") else None,
            )

        summary = final_state.get("final_response") or "Done."
        await self._record_message(run_id=run_id, content={"content": summary}, log=log)
        await self._record_completion(run_id=run_id, status="completed", summary=summary, log=log)
        log.info("orchestrator.finished", status="completed", latency_ms=latency_ms, delegations=len(invocations))

        return OrchestratorResult(
            orchestrator_run_id=run_id,
            status="completed",
            summary=summary,
            invocations=invocations,
            pending_approvals=pending_approvals,
            latency_ms=latency_ms,
        )

    async def _finalize_error(self, run_id: uuid.UUID, started_at: float, log: Any) -> OrchestratorResult:
        summary = "The orchestrator hit an unexpected internal error and could not complete the request."
        latency_ms = int((time.monotonic() - started_at) * 1000)
        await self._record_completion(run_id=run_id, status="failed", summary=summary, log=log)
        log.info("orchestrator.finished", status="error", latency_ms=latency_ms, delegations=0)
        return OrchestratorResult(
            orchestrator_run_id=run_id,
            status="error",
            summary=summary,
            invocations=[],
            latency_ms=latency_ms,
            error=summary,
        )

    # --- LangGraph wiring ---

    def _build_graph(self):
        graph = StateGraph(OrchestratorGraphState)
        graph.add_node("identify_scope", self._identify_scope_node)
        graph.add_node("decide", self._decide_node)
        graph.add_node("delegate", self._delegate_node)
        graph.add_node("await_approval", self._await_approval_node)
        graph.add_node("combine", self._combine_node)
        graph.add_edge(START, "identify_scope")
        graph.add_edge("identify_scope", "decide")
        graph.add_conditional_edges("decide", self._route_after_decide, {"delegate": "delegate", "combine": "combine"})
        graph.add_conditional_edges(
            "delegate",
            self._route_after_delegate,
            {"await_approval": "await_approval", "decide": "decide", "combine": "combine"},
        )
        graph.add_edge("await_approval", "decide")
        graph.add_edge("combine", END)
        return graph.compile(checkpointer=self._checkpointer)

    def _route_after_decide(self, state: OrchestratorGraphState) -> str:
        return "combine" if state.get("finished") or not state.get("next_agent") else "delegate"

    def _route_after_delegate(self, state: OrchestratorGraphState) -> str:
        if state.get("pending_approval_id"):
            return "await_approval"
        return "combine" if state["remaining_steps"] <= 0 else "decide"

    # --- nodes ---

    async def _identify_scope_node(self, state: OrchestratorGraphState) -> dict[str, Any]:
        """Runs once, before any delegation. Asks the model which specialist
        domains are essential for this specific request — genuine LLM reasoning,
        captured up front rather than re-judged turn by turn. _decide_node then
        treats the result as a completeness floor it enforces deterministically:
        see its own docstring for why a purely per-turn "do I have enough now?"
        judgment (the previous design) proved unreliable for broad questions."""
        messages = [
            LLMMessage(role="system", content=self._scope_system_prompt),
            LLMMessage(role="user", content=f"Manager's request: {state['task']}"),
        ]
        response = await self.llm.generate(messages, tools=[_REQUIRED_DOMAINS_SCHEMA], think=False)

        required: list[str] = []
        if response.tool_calls and response.tool_calls[0].name == "identify_required_domains":
            try:
                decision = RequiredDomainsDecision.model_validate(response.tool_calls[0].arguments)
                required = [d for d in decision.domains if d in self.specialists]
            except ValidationError:
                required = []
        return {"required_domains": required}

    def _missing_required_domains(self, state: OrchestratorGraphState) -> list[str]:
        covered = {inv["agent_name"] for inv in state["invocations"]}
        return [d for d in state.get("required_domains", []) if d not in covered]

    def _force_delegate(self, agent_name: str, state: OrchestratorGraphState) -> dict[str, Any]:
        """The deterministic completeness guarantee: a domain _identify_scope_node
        marked essential gets delegated to even if the decide call tries to finish
        (or answers malformed) before covering it. The specialist itself still does
        all the real reasoning/tool-calling — only the routing decision "must this
        domain be checked at all" is enforced here rather than left purely to a
        per-turn model judgment, which this project's own history has shown is not
        reliable enough for broad readiness-style questions on its own."""
        return {
            "next_agent": agent_name,
            "next_instruction": (
                f'As part of fully answering the manager\'s request — "{state["task"]}" — '
                f"report the current {agent_name} status relevant to it."
            ),
            "finished": False,
        }

    async def _decide_node(self, state: OrchestratorGraphState) -> dict[str, Any]:
        missing_required = self._missing_required_domains(state)

        if state["remaining_steps"] <= 0:
            # Bounded budget wins even over an unmet requirement — this is a safety
            # valve (max_delegations), never expected to bind in practice since
            # required_domains is normally 1-3 entries well within budget.
            return {"next_agent": None, "next_instruction": None, "finished": True}

        progress_lines = [
            f'- {inv["agent_name"]} (asked: "{inv["task"]}") -> '
            f"status={inv['result'].get('status')}: {inv['result'].get('summary')}"
            for inv in state["invocations"]
        ]
        progress_text = "\n".join(progress_lines) if progress_lines else "(nothing delegated yet)"

        system_content = f"{self._decide_system_prompt}\n\nThe current date and time (UTC) is {utcnow().isoformat()}."
        requirement_note = (
            f"\n\nYou still MUST delegate to each of these domains before you may call "
            f"finish(): {', '.join(missing_required)}. Choose one of them now.\n"
            if missing_required
            else ""
        )
        user_content = (
            f"Manager's request: {state['task']}\n\n"
            f"Progress so far:\n{progress_text}\n"
            f"{requirement_note}\n"
            "Call delegate(agent_name, instruction) for the next specialist needed, "
            "or finish() if you already have enough to answer."
        )
        messages = [
            LLMMessage(role="system", content=system_content),
            LLMMessage(role="user", content=user_content),
        ]

        response = await self.llm.generate(messages, tools=[_DELEGATE_SCHEMA, _FINISH_SCHEMA], think=False)

        if not response.tool_calls:
            # answered in plain text instead of calling a routing function.
            if missing_required:
                return self._force_delegate(missing_required[0], state)
            return {"next_agent": None, "next_instruction": None, "finished": True}

        call = response.tool_calls[0]
        if call.name == "finish":
            # Deterministic completeness guarantee — see _force_delegate.
            if missing_required:
                return self._force_delegate(missing_required[0], state)
            try:
                FinishDecision.model_validate(call.arguments)
            except ValidationError:
                pass
            return {"next_agent": None, "next_instruction": None, "finished": True}

        if call.name == "delegate":
            try:
                decision = DelegateDecision.model_validate(call.arguments)
            except ValidationError:
                # malformed routing call — force the requirement if one is
                # outstanding rather than giving up; otherwise don't loop forever
                # on bad model output.
                if missing_required:
                    return self._force_delegate(missing_required[0], state)
                return {"next_agent": None, "next_instruction": None, "finished": True}
            if decision.agent_name not in self.specialists:
                if missing_required:
                    return self._force_delegate(missing_required[0], state)
                return {"next_agent": None, "next_instruction": None, "finished": True}
            return {
                "next_agent": decision.agent_name,
                "next_instruction": decision.instruction,
                "finished": False,
            }

        if missing_required:
            return self._force_delegate(missing_required[0], state)
        return {"next_agent": None, "next_instruction": None, "finished": True}

    async def _delegate_node(self, state: OrchestratorGraphState) -> dict[str, Any]:
        agent_name = state["next_agent"]
        assert agent_name is not None
        instruction = state["next_instruction"] or state["task"]
        specialist = self.specialists[agent_name]

        restaurant_id = uuid.UUID(state["restaurant_id"])
        orchestrator_run_id = uuid.UUID(state["orchestrator_run_id"])
        correlation_id = uuid.UUID(state["correlation_id"])
        initiated_by_user_id = uuid.UUID(state["initiated_by_user_id"]) if state["initiated_by_user_id"] else None
        triggering_event_id = uuid.UUID(state["triggering_event_id"]) if state["triggering_event_id"] else None

        async def _call() -> Any:
            return await specialist.handle(
                instruction,
                restaurant_id=restaurant_id,
                # OrchestratorGraphState stores trigger_type as plain str (LangGraph
                # state favors simple JSON-serializable types) — it's always one of
                # the 3 literals handle() itself put there when building initial_state.
                trigger_type=cast(Literal["manager_request", "event", "scheduled"], state["trigger_type"]),
                initiated_by_user_id=initiated_by_user_id,
                triggering_event_id=triggering_event_id,
                parent_run_id=orchestrator_run_id,
                correlation_id=correlation_id,
            )

        result = await _call()
        attempt = 1
        # Bounded retry: a delegated specialist's own LLM call can fail transiently
        # (e.g. a live-model hiccup); one retry with the same instruction before
        # accepting the failure and moving on. A pending_approval result is never
        # retried — retrying would just create a second, duplicate approval request.
        while result.status == "error" and attempt <= self.max_retries_per_agent:
            result = await _call()
            attempt += 1

        invocation: SpecialistInvocationState = {
            "agent_name": agent_name,
            "task": instruction,
            "result": result.model_dump(mode="json"),
            "attempt": attempt,
        }
        update: dict[str, Any] = {
            "invocations": [invocation],
            "remaining_steps": state["remaining_steps"] - 1,
            "next_agent": None,
            "next_instruction": None,
        }
        if result.status == "pending_approval":
            approval_id = (result.data or {}).get("approval_id")
            update["pending_approval_id"] = approval_id
            update["pending_approval_agent"] = agent_name
        return update

    async def _await_approval_node(self, state: OrchestratorGraphState) -> dict[str, Any]:
        # Nothing before interrupt() may have side effects — LangGraph re-runs this
        # node from the top on resume, and interrupt() is what makes that safe: the
        # first time through it pauses the graph, the second time (after resume) it
        # returns the value passed to Command(resume=...) instead of pausing again.
        decision_payload = interrupt(
            {
                "type": "approval_required",
                "approval_id": state["pending_approval_id"],
                "agent_name": state["pending_approval_agent"],
            }
        )

        if self.approval_service is None:
            raise RuntimeError(
                "OrchestratorAgent received a pending_approval result but has no ApprovalService "
                "configured to resolve it — construct it with approval_service=... to support this."
            )

        approval_id = uuid.UUID(state["pending_approval_id"])
        decided_by_user_id = (
            uuid.UUID(decision_payload["decided_by_user_id"]) if decision_payload.get("decided_by_user_id") else None
        )

        # ApprovalService.approve()/reject() require a real deciding user — matches
        # the Manager API's own requirement (app.api.routes.approvals) that every
        # decision is attributable to someone, never silently anonymous.
        if decided_by_user_id is None:
            raise ToolError(
                "decided_by_user_id_required",
                "Resuming a paused orchestrator run requires decided_by_user_id.",
            )

        if decision_payload.get("decision") == "approved":
            approval = await self.approval_service.approve(approval_id, decided_by_user_id)
            execution_status = (approval.execution_result or {}).get("status")
            status = "error" if execution_status == "failed" else "completed"
            summary = f"Approved: {approval.reason}."
            if approval.execution_result is not None:
                summary += f" Execution {execution_status}."
        else:
            approval = await self.approval_service.reject(approval_id, decided_by_user_id)
            status = "completed"
            summary = f"Rejected: {approval.reason}."

        invocation: SpecialistInvocationState = {
            "agent_name": state["pending_approval_agent"] or self.name,
            "task": f"Resolve pending approval {approval_id}",
            "result": {"status": status, "summary": summary, "data": approval.execution_result},
            "attempt": 1,
        }
        return {
            "invocations": [invocation],
            "pending_approval_id": None,
            "pending_approval_agent": None,
        }

    async def _combine_node(self, state: OrchestratorGraphState) -> dict[str, Any]:
        if not state["invocations"]:
            return {"final_response": "I wasn't able to determine which specialist should handle this request."}

        blocks = [
            f'{inv["agent_name"]} was asked: "{inv["task"]}"\n'
            f"Result (status={inv['result'].get('status')}): {inv['result'].get('summary')}"
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
