# Phase 0 Research: DineOps Core Platform MVP

No `NEEDS CLARIFICATION` markers remained in the Technical Context — the stack was
specified explicitly by the project owner. This document instead records the design
decisions made while turning that stack into an architecture, with rationale and
alternatives considered, so future amendments have context.

## 1. Agent orchestration pattern

**Decision**: One LangGraph graph per specialist agent, plus a top-level Orchestrator
graph whose only "tool" is a delegation step that invokes one or more specialist graphs
and merges their typed outputs.

**Rationale**: Matches Constitution V directly — each specialist agent owns a narrow
domain and its own tools; the Orchestrator routes and composes rather than reimplementing
domain logic. Keeping specialist agents as independent graphs also satisfies FR-038
(Orchestrator delegation testable independent of specialist internals) — the Orchestrator
can be tested against fake specialist graphs.

**Alternatives considered**:
- *Single monolithic graph with all tools attached*: rejected — would let any agent reach
  any tool, violating Constitution V and FR-037 (specialist agents independently
  testable) since nothing would isolate one domain's failures from another's.
- *One agent process per specialist (separate deployables)*: rejected for MVP — scale
  (spec Assumptions: single restaurant, single concurrent manager) doesn't justify
  operating five separate services; revisit if/when multi-location scale is in scope.

## 2. Typed tool-calling mechanism

**Decision**: A project-owned `Tool` protocol (`tools/base.py`) wrapping Pydantic
`input_model`/`output_model`, adapted into LangGraph/LangChain's native tool-calling
format at the graph boundary, rather than agents/business code depending on LangChain's
tool base classes directly.

**Rationale**: Keeps the typed contract (Constitution VI) framework-agnostic — if LangGraph's
tool-calling API changes or the project swaps orchestration frameworks later, only the
thin adapter changes, not every tool. It also lets `tests/contract/` validate tools
without spinning up a graph at all.

**Alternatives considered**:
- *Use LangChain `BaseTool` subclasses directly as the canonical tool definition*:
  rejected — couples the typed-contract guarantee (a core constitutional requirement) to a
  third-party library's evolution.

## 3. MemoryService storage & retrieval shape

**Decision**: MemoryService is backed by ordinary relational tables (`memory.py`) in the
same Postgres instance, with structured scoping (`scope_type`, `scope_id`, `topic`) and
exact/filtered lookup — no embeddings, no similarity search.

**Rationale**: Directly required by Constitution II (no RAG) and III (custom
MemoryService, Mem0-inspired but not Mem0). Mem0's *interface idea* — scoped,
agent-writable long-term facts — is worth borrowing; its typical *vector-backed retrieval
implementation* is exactly what's excluded here. Structured scoping is sufficient for the
MVP's memory use cases (customer preferences, recurring operational notes), which are
lookup-by-entity, not semantic search over prose.

**Alternatives considered**:
- *Embedding-backed semantic recall (Mem0-style default)*: explicitly rejected per
  Constitution II regardless of quality tradeoffs.
- *Separate memory datastore (e.g., a document store)*: rejected — adds an operational
  dependency with no MVP-scale justification; Postgres already holds everything else.

## 4. EventBus implementation

**Decision**: In-process publish/subscribe (`services/event_bus.py`), synchronous within
a request/transaction boundary, backed by a durable `EventLog` table for audit/replay —
not an external message broker.

**Rationale**: FR-030–FR-032 require event detection and agent-triggered reactions, not
distributed/cross-service messaging — MVP runs as a single process. A durable log
satisfies observability (FR-033) and gives a replay/debug path without operational
overhead of Kafka/RabbitMQ/Redis Streams.

**Alternatives considered**:
- *External broker (Kafka, RabbitMQ, Redis Streams)*: rejected for MVP — no
  multi-service consumers exist yet; revisit if DineOps splits into independently
  deployed services or needs cross-restaurant fan-out.
- *Direct service-to-service calls instead of an event abstraction*: rejected — would
  couple, e.g., `InventoryService` to knowing that `Reservation` or notification logic
  cares about low stock, defeating the point of decoupled event-driven workflows (FR-030).

## 5. ApprovalService execution model

**Decision**: `propose()` persists the pending action's *replayable intent* (domain,
method reference, validated arguments) rather than executing anything; `decide()` on
approval re-invokes that captured intent through the normal service method.

**Rationale**: Guarantees a high-impact action can never partially execute before
approval (Constitution IV, FR-027) — the mutation path is identical whether approved
immediately-adjacent-in-time or after a delay, so there's no special "post-approval"
execution code to drift out of sync with the regular path.

**Alternatives considered**:
- *Two-phase DB transaction held open until approval*: rejected — approval may take
  arbitrarily long (spec Edge Cases: "not approved/rejected in time"), and holding a DB
  transaction open across that is operationally unsafe.
- *Approval as a UI-only soft-confirmation before the tool call is even made*: rejected —
  doesn't satisfy "MUST NOT take effect until approved" as a system guarantee (FR-027);
  would rely on the caller behaving correctly rather than the system enforcing it.

## 6. Async SQLAlchemy + Alembic workflow

**Decision**: SQLAlchemy 2.x async ORM with `asyncpg`, one declarative `Base`, Alembic
autogenerate-assisted migrations reviewed by hand before commit.

**Rationale**: FastAPI is async end-to-end; an async DB driver avoids blocking the event
loop under concurrent tool calls. Alembic is the de facto standard companion to
SQLAlchemy and keeps schema evolution auditable and reversible, consistent with treating
infrastructure as planned rather than improvised (constitution's Development Workflow
section).

**Alternatives considered**:
- *Sync SQLAlchemy + a thread pool*: rejected — adds complexity to avoid what an async
  driver solves directly, with no offsetting benefit at MVP scale.
- *Hand-written SQL migrations without Alembic*: rejected — loses autogenerate diffing and
  the standard up/down migration story for no benefit.

## 7. Cross-domain reads (Analytics)

**Decision**: `analytics_repo.py` queries other domains' tables directly (read-only
aggregate queries) rather than `AnalyticsService` calling four other domain services.

**Rationale**: Analytics is inherently cross-cutting (FR-017 spans covers, revenue,
no-shows, popular items — reservation, customer, and menu/inventory data). Routing every
analytics query through four service calls would add latency and coupling without adding
safety, since repositories already are "the only layer that touches SQL" — the
constitutional boundary is about **agents/tools/services never touching SQL**, not about
services never reading another domain's tables read-only.

**Alternatives considered**:
- *Analytics Service calls Reservation/Customer/Inventory/Staffing Services*: rejected
  for MVP — unnecessary chattiness; reconsider if analytics logic needs those services'
  business rules (not just their data) or if domains move to separate databases later.

## 8. Testing infrastructure

**Decision**: Docker Compose–provisioned Postgres, migrated with Alembic, reused across
`tests/unit/` (repository tests) and `tests/integration/`; `tests/contract/` and
`tests/agent/` run without a database at all (fakes/mocks only).

**Rationale**: Matches FR-036–FR-038 (tools, agents, and delegation each independently
verifiable) by tiering tests to match exactly what they need — no test tier requires more
infrastructure than its job demands, keeping the fast tiers (contract, agent) fast.

**Alternatives considered**:
- *`testcontainers-python` instead of Compose*: viable alternative, not chosen for MVP
  only because the project already standardizes local infra on Docker Compose
  (constitution's Technology Stack Constraints) — one Postgres definition serves both dev
  and test.
- *SQLite for tests*: rejected — would test against different SQL semantics than
  production Postgres, risking false confidence.

## 9. Configuration & logging

**Decision**: `pydantic-settings` for typed, env-sourced configuration; `structlog` for
structured logs with a per-request correlation ID threaded through `contextvars`.

**Rationale**: Consistent with Constitution VI (typed contracts) applied to configuration
itself — invalid config fails fast at startup rather than surfacing as a runtime error
mid-request. Structured logs are a prerequisite for the correlation-ID tracing that makes
FR-033's activity trail debuggable at the log level, distinct from (but linked to) the
manager-facing `AgentActivityRecord` table.

**Alternatives considered**:
- *Plain `os.environ` + `python-dotenv`*: rejected — no validation, easy to typo an env
  var name silently.
- *stdlib `logging` with manual JSON formatting*: viable but more boilerplate for the same
  outcome; `structlog` is a small, well-established dependency.
