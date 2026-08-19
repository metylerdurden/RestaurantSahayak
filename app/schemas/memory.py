from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

MemoryTypeLiteral = Literal[
    "CUSTOMER_PREFERENCE",
    "BUSINESS_RULE",
    "MANAGER_PREFERENCE",
    "PAST_DECISION",
    "OPERATIONAL_FACT",
    "AGENT_EXPERIENCE",
]
MemorySourceLiteral = Literal["manager_stated", "agent_inferred", "system_derived"]


class MemoryDTO(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    customer_id: uuid.UUID | None
    agent_name: str | None
    memory_type: str
    topic: str
    content: dict[str, Any]
    importance: int
    confidence: float
    source: str
    is_active: bool
    access_count: int
    last_accessed_at: datetime | None
    created_at: datetime
    updated_at: datetime


# --- add_memory ---


class AddMemoryInput(BaseModel):
    memory_type: MemoryTypeLiteral
    topic: str = Field(description='Short, stable, snake_case label, e.g. "seating_preference"')
    content: dict[str, Any] = Field(
        description='The fact itself, e.g. {"text": "Prefers a quiet table away from the kitchen."}'
    )
    source: MemorySourceLiteral
    importance: int = Field(default=3, ge=1, le=5)
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)
    customer_id: uuid.UUID | None = None
    agent_name: str | None = None


class AddMemoryOutput(BaseModel):
    memory: MemoryDTO


# --- search_memory ---


class SearchMemoryInput(BaseModel):
    query: str = Field(description="Natural-language description of what to recall")
    memory_type: MemoryTypeLiteral | None = None
    customer_id: uuid.UUID | None = None
    agent_name: str | None = None
    top_k: int = Field(default=5, ge=1, le=20)
    min_similarity: float = Field(default=0.0, ge=0.0, le=1.0)


class MemorySearchResultDTO(BaseModel):
    memory: MemoryDTO
    similarity: float


class SearchMemoryOutput(BaseModel):
    results: list[MemorySearchResultDTO]


# --- get_memory ---


class GetMemoryInput(BaseModel):
    memory_id: uuid.UUID


class GetMemoryOutput(BaseModel):
    memory: MemoryDTO


# --- update_memory ---


class UpdateMemoryInput(BaseModel):
    memory_id: uuid.UUID
    content: dict[str, Any] | None = None
    topic: str | None = None
    importance: int | None = Field(default=None, ge=1, le=5)
    confidence: float | None = Field(default=None, ge=0.0, le=1.0)


class UpdateMemoryOutput(BaseModel):
    memory: MemoryDTO


# --- reinforce_memory ---


class ReinforceMemoryInput(BaseModel):
    memory_id: uuid.UUID
    confidence_step: float = Field(default=0.1, ge=0.0, le=1.0)


class ReinforceMemoryOutput(BaseModel):
    memory: MemoryDTO


# --- forget_memory ---


class ForgetMemoryInput(BaseModel):
    memory_id: uuid.UUID
    reason: str | None = None


class ForgetMemoryOutput(BaseModel):
    memory: MemoryDTO


# --- delete_memory ---


class DeleteMemoryInput(BaseModel):
    memory_id: uuid.UUID


class DeleteMemoryOutput(BaseModel):
    deleted: bool = True
