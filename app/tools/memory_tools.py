"""Memory tools — the only surface an agent has onto DineOps' persistent memory
subsystem. Each tool validates typed input, calls exactly one MemoryService method,
and returns typed output. Not RAG: there is no document/chunk retrieval anywhere in
this module — search_memory does semantic similarity search over small structured
facts, nothing else."""

from __future__ import annotations

from app.schemas.memory import (
    AddMemoryInput,
    AddMemoryOutput,
    DeleteMemoryInput,
    DeleteMemoryOutput,
    ForgetMemoryInput,
    ForgetMemoryOutput,
    GetMemoryInput,
    GetMemoryOutput,
    MemoryDTO,
    MemorySearchResultDTO,
    ReinforceMemoryInput,
    ReinforceMemoryOutput,
    SearchMemoryInput,
    SearchMemoryOutput,
    UpdateMemoryInput,
    UpdateMemoryOutput,
)
from app.services.memory_service import MemoryService
from app.tools.base import Tool, ToolContext


class AddMemoryTool(Tool[AddMemoryInput, AddMemoryOutput]):
    name = "add_memory"
    description = (
        "Record a new fact in persistent memory (a customer preference, a business "
        "rule, a past decision, ...). Use this for something genuinely new — if a "
        "related memory may already exist, search_memory first and use "
        "update_memory instead so facts don't accumulate as duplicates."
    )
    input_model = AddMemoryInput
    output_model = AddMemoryOutput

    def __init__(self, service: MemoryService) -> None:
        self.service = service

    async def run(self, input: AddMemoryInput, *, context: ToolContext) -> AddMemoryOutput:
        memory = await self.service.add_memory(
            restaurant_id=context.restaurant_id,
            memory_type=input.memory_type,
            topic=input.topic,
            content=input.content,
            source=input.source,
            importance=input.importance,
            confidence=input.confidence,
            customer_id=input.customer_id,
            agent_name=input.agent_name,
            source_agent_run_id=context.agent_run_id,
        )
        return AddMemoryOutput(memory=MemoryDTO.model_validate(memory))


class SearchMemoryTool(Tool[SearchMemoryInput, SearchMemoryOutput]):
    name = "search_memory"
    description = (
        "Semantically search persistent memory for facts relevant to a natural-"
        "language query (e.g. a customer's seating preference). Returns the closest "
        "matches ranked by similarity, not an exact-text match."
    )
    input_model = SearchMemoryInput
    output_model = SearchMemoryOutput

    def __init__(self, service: MemoryService) -> None:
        self.service = service

    async def run(self, input: SearchMemoryInput, *, context: ToolContext) -> SearchMemoryOutput:
        results = await self.service.search_memory(
            restaurant_id=context.restaurant_id,
            query=input.query,
            memory_type=input.memory_type,
            customer_id=input.customer_id,
            agent_name=input.agent_name,
            top_k=input.top_k,
            min_similarity=input.min_similarity,
        )
        return SearchMemoryOutput(
            results=[
                MemorySearchResultDTO(memory=MemoryDTO.model_validate(memory), similarity=similarity)
                for memory, similarity in results
            ]
        )


class GetMemoryTool(Tool[GetMemoryInput, GetMemoryOutput]):
    name = "get_memory"
    description = "Fetch a single memory by its id."
    input_model = GetMemoryInput
    output_model = GetMemoryOutput

    def __init__(self, service: MemoryService) -> None:
        self.service = service

    async def run(self, input: GetMemoryInput, *, context: ToolContext) -> GetMemoryOutput:
        memory = await self.service.get_memory(restaurant_id=context.restaurant_id, memory_id=input.memory_id)
        return GetMemoryOutput(memory=MemoryDTO.model_validate(memory))


class UpdateMemoryTool(Tool[UpdateMemoryInput, UpdateMemoryOutput]):
    name = "update_memory"
    description = (
        "Correct an existing memory's content, topic, importance, or confidence "
        "in place. Use this instead of add_memory when the manager is updating or "
        "contradicting something already remembered."
    )
    input_model = UpdateMemoryInput
    output_model = UpdateMemoryOutput

    def __init__(self, service: MemoryService) -> None:
        self.service = service

    async def run(self, input: UpdateMemoryInput, *, context: ToolContext) -> UpdateMemoryOutput:
        memory = await self.service.update_memory(
            restaurant_id=context.restaurant_id,
            memory_id=input.memory_id,
            content=input.content,
            topic=input.topic,
            importance=input.importance,
            confidence=input.confidence,
        )
        return UpdateMemoryOutput(memory=MemoryDTO.model_validate(memory))


class ReinforceMemoryTool(Tool[ReinforceMemoryInput, ReinforceMemoryOutput]):
    name = "reinforce_memory"
    description = (
        "Increase a memory's confidence because it was just reconfirmed (the "
        "manager restated the same fact, or it proved correct in use)."
    )
    input_model = ReinforceMemoryInput
    output_model = ReinforceMemoryOutput

    def __init__(self, service: MemoryService) -> None:
        self.service = service

    async def run(self, input: ReinforceMemoryInput, *, context: ToolContext) -> ReinforceMemoryOutput:
        memory = await self.service.reinforce_memory(
            restaurant_id=context.restaurant_id,
            memory_id=input.memory_id,
            confidence_step=input.confidence_step,
        )
        return ReinforceMemoryOutput(memory=MemoryDTO.model_validate(memory))


class ForgetMemoryTool(Tool[ForgetMemoryInput, ForgetMemoryOutput]):
    name = "forget_memory"
    description = (
        "Deactivate a memory that is no longer true or no longer useful, without "
        "erasing its history. This is the normal way to retire an outdated fact."
    )
    input_model = ForgetMemoryInput
    output_model = ForgetMemoryOutput

    def __init__(self, service: MemoryService) -> None:
        self.service = service

    async def run(self, input: ForgetMemoryInput, *, context: ToolContext) -> ForgetMemoryOutput:
        memory = await self.service.forget_memory(
            restaurant_id=context.restaurant_id, memory_id=input.memory_id, reason=input.reason
        )
        return ForgetMemoryOutput(memory=MemoryDTO.model_validate(memory))


class DeleteMemoryTool(Tool[DeleteMemoryInput, DeleteMemoryOutput]):
    name = "delete_memory"
    description = (
        "Permanently erase a memory (e.g. it was recorded by mistake). Prefer "
        "forget_memory for facts that were once true but no longer apply."
    )
    input_model = DeleteMemoryInput
    output_model = DeleteMemoryOutput

    def __init__(self, service: MemoryService) -> None:
        self.service = service

    async def run(self, input: DeleteMemoryInput, *, context: ToolContext) -> DeleteMemoryOutput:
        await self.service.delete_memory(restaurant_id=context.restaurant_id, memory_id=input.memory_id)
        return DeleteMemoryOutput(deleted=True)
