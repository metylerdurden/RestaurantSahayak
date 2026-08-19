"""ToolCallingAgent: the generic bounded ReAct-style loop shared by every DineOps
specialist agent.

    <Agent> -> LLMProvider -> Qwen3-8B (via Ollama) -> typed Tools -> domain
    Services -> Repositories -> PostgreSQL

A concrete agent (ReservationAgent, CustomerAgent, ...) is this class fixed to a
name, a system prompt, and a tool set — nothing about the loop itself is domain
specific. Never imports sqlalchemy, app.models, or app.repositories (enforced by
tests/unit/test_layer_boundaries.py) and never calls an embedding provider directly —
this is tool-calling reasoning, not retrieval. It holds Tool instances (never Service
or Repository instances for the domain it acts on) and does exactly this: ask the
model for the next step, execute whatever tool calls it made, feed results back,
repeat until the model produces a final answer or the iteration cap is hit.
"""

from __future__ import annotations

import json
import time
import uuid
from typing import Any, Literal

from pydantic import ValidationError

from app.agents.state import AgentErrorInfo, AgentResult, AgentState, ToolCallRecord
from app.core.logging import get_logger
from app.llm.base import LLMMessage, LLMProvider, ToolCall as LLMToolCall
from app.services.agent_run_service import AgentRunService
from app.tools.base import Tool, ToolContext, ToolError, ToolErrorOutput, utcnow

_logger = get_logger(__name__)


class ToolCallingAgent:
    def __init__(
        self,
        *,
        name: str,
        system_prompt: str,
        llm: LLMProvider,
        tools: list[Tool],
        agent_run_service: AgentRunService,
        max_iterations: int = 8,
    ) -> None:
        self.name = name
        self.system_prompt = system_prompt
        self.llm = llm
        self.tools: dict[str, Tool] = {tool.name: tool for tool in tools}
        self._tool_schemas = [tool.to_llm_schema() for tool in tools]
        self.agent_run_service = agent_run_service
        self.max_iterations = max_iterations

    async def handle(
        self,
        task: str,
        *,
        restaurant_id: uuid.UUID,
        trigger_type: Literal["manager_request", "event"] = "manager_request",
        initiated_by_user_id: uuid.UUID | None = None,
        triggering_event_id: uuid.UUID | None = None,
        parent_run_id: uuid.UUID | None = None,
        correlation_id: uuid.UUID | None = None,
    ) -> AgentResult:
        """Receive a natural-language task, run the tool-calling loop, and return a
        structured result. Always returns (never raises) — failures come back as a
        `status="error"` result, and the AgentRun row is still recorded as failed.

        `parent_run_id`/`correlation_id` are for OrchestratorAgent: when it delegates
        to a specialist, it passes its own run id and correlation id so the specialist's
        AgentRun is linked into the same trace (parent_run_id) and the same
        correlation_id — both no-ops for a specialist invoked directly, as before."""
        started_at = time.monotonic()
        run = await self.agent_run_service.start_run(
            restaurant_id=restaurant_id,
            agent_name=self.name,
            model_name=self.llm.model_name,
            trigger_type=trigger_type,
            initiated_by_user_id=initiated_by_user_id,
            triggering_event_id=triggering_event_id,
            parent_run_id=parent_run_id,
            correlation_id=correlation_id,
        )
        log = _logger.bind(agent_run_id=str(run.id), agent_name=self.name, model=self.llm.model_name)
        log.info("agent.started", task=task)

        context = ToolContext(
            restaurant_id=restaurant_id,
            correlation_id=str(run.correlation_id),
            acting_agent=self.name,
            trigger_type=trigger_type,
            agent_run_id=run.id,
        )

        await self.agent_run_service.log_message(run_id=run.id, role="user", content={"task": task})

        state = AgentState(task=task)
        try:
            status = await self._run_loop(task, state, context=context, run_id=run.id, log=log)
        except Exception as exc:  # the LLM/HTTP boundary — never let a live-model failure crash the caller
            log.error("agent.unexpected_failure", error=str(exc), exc_info=True)
            state.error = AgentErrorInfo(code="internal_error", message="Unexpected internal error.")
            status = "error"

        latency_ms = int((time.monotonic() - started_at) * 1000)
        summary = state.final_content or (state.error.message if state.error else "No result produced.")

        await self.agent_run_service.complete_run(
            run_id=run.id,
            status="completed" if status != "error" else "failed",
            outcome_summary=summary,
        )
        log.info("agent.finished", status=status, latency_ms=latency_ms, tool_calls=len(state.tool_calls))

        return AgentResult(
            agent_run_id=run.id,
            status=status,
            summary=summary,
            tool_calls=state.tool_calls,
            data=state.tool_calls[-1].output if state.tool_calls else None,
            error=state.error,
            latency_ms=latency_ms,
        )

    async def _run_loop(
        self,
        task: str,
        state: AgentState,
        *,
        context: ToolContext,
        run_id: uuid.UUID,
        log: Any,
    ) -> Literal["completed", "pending_approval", "error"]:
        system_content = (
            f"{self.system_prompt}\n\n"
            f"The current date and time (UTC) is {utcnow().isoformat()}. Interpret relative "
            'references like "tonight", "tomorrow", or "in an hour" relative to this.'
        )
        messages: list[LLMMessage] = [
            LLMMessage(role="system", content=system_content),
            LLMMessage(role="user", content=task),
        ]

        while state.iterations < self.max_iterations:
            state.iterations += 1
            # think=False: Ollama-specific — qwen3's default chain-of-thought mode adds
            # tens of seconds of reasoning tokens per turn with no benefit for picking a
            # tool and its arguments, and repeatedly risked exceeding the request timeout
            # during testing. Providers that don't recognize the kwarg simply ignore it.
            response = await self.llm.generate(messages, tools=self._tool_schemas, think=False)

            if not response.tool_calls:
                state.final_content = (response.content or "").strip() or "Done."
                await self.agent_run_service.log_message(
                    run_id=run_id, role="assistant", content={"content": state.final_content}
                )
                return self._final_status(state)

            messages.append(
                LLMMessage(role="assistant", content=response.content, tool_calls=response.tool_calls)
            )
            await self.agent_run_service.log_message(
                run_id=run_id,
                role="assistant",
                content={
                    "content": response.content,
                    "tool_calls": [{"name": tc.name, "arguments": tc.arguments} for tc in response.tool_calls],
                },
            )

            for call in response.tool_calls:
                await self._execute_tool_call(call, state=state, messages=messages, context=context, run_id=run_id, log=log)

        state.error = AgentErrorInfo(
            code="max_iterations_exceeded",
            message=f"Could not complete the task within {self.max_iterations} tool-calling steps.",
        )
        await self.agent_run_service.log_message(
            run_id=run_id, role="assistant", content={"error": state.error.model_dump()}
        )
        return "error"

    async def _execute_tool_call(
        self,
        call: LLMToolCall,
        *,
        state: AgentState,
        messages: list[LLMMessage],
        context: ToolContext,
        run_id: uuid.UUID,
        log: Any,
    ) -> None:
        tool = self.tools.get(call.name)

        if tool is None:
            error = ToolErrorOutput(
                code="unknown_tool", message=f"No tool named {call.name!r} is available to this agent."
            )
            log.warning("agent.unknown_tool", tool_name=call.name)
        else:
            try:
                output = await tool(call.arguments, context=context)
            except ToolError as exc:
                error = ToolErrorOutput.from_error(exc)
                log.warning("agent.tool_error", tool_name=call.name, code=exc.code)
            except ValidationError as exc:
                error = ToolErrorOutput(code="invalid_arguments", message=str(exc))
                log.warning("agent.invalid_arguments", tool_name=call.name)
            else:
                result = output.model_dump(mode="json")
                state.tool_calls.append(ToolCallRecord(tool_name=call.name, input=call.arguments, output=result))
                messages.append(
                    LLMMessage(role="tool", content=json.dumps({"tool": call.name, "result": result}))
                )
                await self.agent_run_service.log_message(
                    run_id=run_id,
                    role="tool_result",
                    tool_name=call.name,
                    content={"input": call.arguments, "output": result},
                )
                return

        messages.append(
            LLMMessage(role="tool", content=json.dumps({"tool": call.name, "error": error.model_dump()}))
        )
        await self.agent_run_service.log_message(
            run_id=run_id, role="tool_result", tool_name=call.name, content={"error": error.model_dump()}
        )

    def _final_status(self, state: AgentState) -> Literal["completed", "pending_approval", "error"]:
        if state.tool_calls and state.tool_calls[-1].output.get("status") == "pending_approval":
            return "pending_approval"
        return "completed"
