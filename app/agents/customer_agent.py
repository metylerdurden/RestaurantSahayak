"""CustomerAgent: the customer-domain specialist — customer profiles plus DineOps'
persistent memory subsystem (Constitution III). All loop mechanics live in
ToolCallingAgent; this class only fixes the agent's name, system prompt, and (via the
caller-supplied `tools` list) which typed tools it may use.

    CustomerAgent -> LLMProvider -> Qwen3-8B -> typed Tools (customer + memory) ->
    CustomerService / MemoryService -> Repositories -> PostgreSQL

Qwen3-8B does all reasoning: identifying the customer, deciding whether something is
worth remembering, classifying it, and deciding when an existing memory should be
searched, reused, or corrected. BGE-M3 (via MemoryService, never called directly by
the agent) only turns text into vectors for similarity search — it never reasons
about content, and there is no document store or chunk retrieval anywhere in this
path: not RAG.
"""

from __future__ import annotations

from app.agents.prompts import CUSTOMER_AGENT_SYSTEM_PROMPT
from app.agents.tool_calling_agent import ToolCallingAgent
from app.llm.base import LLMProvider
from app.services.agent_run_service import AgentRunService
from app.tools.base import Tool


class CustomerAgent(ToolCallingAgent):
    def __init__(
        self,
        *,
        llm: LLMProvider,
        tools: list[Tool],
        agent_run_service: AgentRunService,
        max_iterations: int = 8,
    ) -> None:
        super().__init__(
            name="customer",
            system_prompt=CUSTOMER_AGENT_SYSTEM_PROMPT,
            llm=llm,
            tools=tools,
            agent_run_service=agent_run_service,
            max_iterations=max_iterations,
        )
