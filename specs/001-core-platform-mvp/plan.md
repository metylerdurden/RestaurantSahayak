# Implementation Plan: DineOps Core Platform MVP

**Branch**: `001-core-platform-mvp` | **Date**: 2026-08-19 | **Spec**: [spec.md](./spec.md)

**Input**: Feature specification from `/specs/001-core-platform-mvp/spec.md`

## Summary

Build the DineOps backend as a strictly layered, agent-first system: a conversational
Orchestrator Agent (LangGraph) delegates to five specialist agents (Reservation,
Inventory, Customer, Staffing, Analytics). Agents never touch the database or business
logic directly — they call **typed tools**, tools call **domain services**, services call
**repositories**, and only repositories talk to PostgreSQL (via SQLAlchemy). Three
cross-cutting subsystems sit alongside the domain layer: a custom **MemoryService**
(long-term memory, exposed to agents only as tools — not a shared table agents query
freely), an **ApprovalService** (intercepts high-impact tool calls and holds them for
human sign-off before they reach a repository), and an **EventBus** (detects operational
events and triggers agent workflows without manager input). FastAPI exposes the
conversational, approval, and activity surfaces. No RAG, no vector store, no document
ingestion — this is architecture and interface design only; no application code is
written in this phase.

## Technical Context

**Language/Version**: Python 3.12+ (3.13.2 confirmed available in the dev environment)

**Primary Dependencies**: FastAPI (API layer), SQLAlchemy 2.x async ORM + `asyncpg`
driver (repository layer), Alembic (migrations), LangGraph + `langchain-core` (agent
graphs and typed tool-calling primitives), Pydantic v2 (all typed contracts: tool I/O,
API schemas, memory records, events), `pydantic-settings` (configuration), `structlog`
(structured logging)

**Storage**: PostgreSQL 16, running in Docker for local development. Single database for
MVP; MemoryService, ApprovalService, and EventBus persistence live in their own schemas/
tables in the same Postgres instance (not a separate datastore) — see [Constitution
Check](#constitution-check) and [data-model.md](./data-model.md).

**Testing**: `pytest` + `pytest-asyncio`, `httpx` (ASGI test client for the API layer), a
disposable Postgres instance via Docker Compose for integration tests. See [Testing
Architecture](#testing-architecture).

**Target Platform**: Linux containers (Docker for local dev; container-portable for later
deployment). No frontend is built in this phase — FastAPI exposes a conversational HTTP
API consumed by a thin client later.

**Project Type**: Single backend service (web-service), layered internally per the
boundaries mandated below. Not a frontend+backend split — no UI is in scope for this plan.

**Performance Goals**: Typed tool calls (Tool → Service → Repository → DB round trip)
complete in **p95 < 300ms**, excluding LLM inference time. API endpoints that don't invoke
an agent (approvals, activity log) complete in **p95 < 500ms**. These budgets exist so
that SC-001 (manager completes a reservation request in under 2 minutes) is dominated by
LLM think time, not system overhead.

**Constraints**: Agents and tools MUST NOT hold a database session or SQLAlchemy handle
of any kind (architectural constraint, not a performance one — see Constitution Check).
High-impact tool calls MUST NOT reach a repository until ApprovalService records an
approval. Single restaurant, single concurrent manager session for MVP (per spec
Assumptions) — no multi-tenancy or multi-user concurrency control is designed in this
phase.

**Scale/Scope**: One restaurant location; five specialist agents + one Orchestrator; nine
key entities (see [data-model.md](./data-model.md)); MVP data volumes (hundreds of
reservations/customers, not millions) — no sharding, caching tier, or read-replica design
is warranted at this scale.

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-checked after Phase 1 design below.*

| # | Principle | Gate | Status |
|---|-----------|------|--------|
| 1 | I. Agent-First, Tool-Mediated Access | Agents/LangGraph nodes import **only** the Tool layer. Tools import **only** Services (never a SQLAlchemy session, model, or Repository class; never raw SQL). | **PASS** — enforced by the layer boundary in [Component Architecture](#component-architecture) and by import-direction rules in [Testing Architecture](#testing-architecture). |
| 2 | II. No RAG, No Document Pipelines | No vector database, embedding store, chunker, or document-ingestion component appears anywhere in this design. | **PASS** — confirmed absent from Technical Context, dependencies, and data model. |
| 3 | III. Custom Persistent MemoryService | Memory is our own component with its own schema, reached by agents **only** via memory tools — not a table other domain code queries directly, and not a wrapped third-party product. | **PASS** — see [MemoryService](#memoryservice). |
| 4 | IV. Human Approval for High-Impact Actions | Every high-impact tool call is intercepted by ApprovalService **before** any repository mutation; the propose → approve/reject → execute flow is structural, not a UI-only afterthought. | **PASS** — see [ApprovalService](#approvalservice). |
| 5 | V. Orchestrator + Specialized Agents | Orchestrator holds **no** domain tools itself (only a routing/delegation tool); each specialist agent is scoped to its own domain's tool subset only. | **PASS** — see [Agent Layer](#agent-layer). |
| 6 | VI. Typed Contracts Everywhere | Tool inputs/outputs, memory records, events, and API schemas are all Pydantic models with explicit types — no loose dicts crossing a layer boundary. | **PASS** — see [Typed Tool Interface](#typed-tool-interface) and [data-model.md](./data-model.md). |

No violations. [Complexity Tracking](#complexity-tracking) is not needed — the layering
below implements the constitution rather than deviating from it.

## Project Structure

### Documentation (this feature)

```text
specs/001-core-platform-mvp/
├── plan.md              # This file
├── research.md          # Phase 0 output
├── data-model.md         # Phase 1 output
├── quickstart.md          # Phase 1 output
├── contracts/               # Phase 1 output
│   ├── tools.md               # Typed tool interface contract
│   ├── api.md                   # FastAPI endpoint contract
│   └── events.md                  # EventBus event contract
└── tasks.md               # Phase 2 output (/speckit-tasks — not created here)
```

### Source Code (repository root)

```text
src/dineops/
├── api/                        # API LAYER — FastAPI app, routers, request/response schemas
│   ├── main.py                    # app factory, router registration, startup wiring
│   ├── deps.py                     # DI: session-scoped services, current-agent-graph, auth stub
│   └── routes/
│       ├── chat.py                    # POST /chat — conversational entry to the Orchestrator
│       ├── approvals.py                # GET/POST /approvals — review, approve, reject
│       └── activity.py                  # GET /activity — agent activity log (observability)
│
├── agents/                      # AGENT LAYER — LangGraph graphs only. No DB imports allowed here.
│   ├── orchestrator/
│   │   └── graph.py                   # routes a request to 1+ specialist agents, merges results
│   ├── reservation/graph.py
│   ├── inventory/graph.py
│   ├── customer/graph.py
│   ├── staffing/graph.py
│   └── analytics/graph.py
│
├── tools/                        # TOOL LAYER — the only thing agent graphs are allowed to call
│   ├── base.py                        # Tool protocol/base class: name, description, input/output
│   │                                    #   Pydantic models, high_impact flag, .run()
│   ├── registry.py                     # binds each agent to its permitted tool subset
│   ├── reservation_tools.py
│   ├── inventory_tools.py
│   ├── customer_tools.py
│   ├── staffing_tools.py
│   ├── analytics_tools.py
│   └── memory_tools.py                  # remember / recall / forget — the ONLY memory access path
│
├── services/                       # DOMAIN SERVICE LAYER — business rules, no SQL, no HTTP
│   ├── reservation_service.py
│   ├── inventory_service.py
│   ├── customer_service.py
│   ├── staffing_service.py
│   ├── analytics_service.py
│   ├── memory_service.py                 # MemoryService implementation
│   ├── approval_service.py                # ApprovalService implementation
│   └── event_bus.py                         # EventBus implementation + publish/subscribe API
│
├── repositories/                    # REPOSITORY LAYER — the ONLY layer that imports SQLAlchemy
│   ├── base.py                          # generic repository helpers, unit-of-work/session scope
│   ├── reservation_repo.py
│   ├── inventory_repo.py
│   ├── customer_repo.py
│   ├── staffing_repo.py
│   ├── analytics_repo.py                  # read-oriented queries over other domains' tables
│   ├── memory_repo.py
│   ├── approval_repo.py
│   ├── event_repo.py
│   └── agent_run_repo.py               # covers both AgentRun and AgentMessage persistence
│
├── models/                          # SQLAlchemy ORM models (persistence schema), one module/domain
│   ├── reservation.py, inventory.py, customer.py, staffing.py
│   ├── memory.py, approval.py, agent_run.py, agent_message.py, event.py
│
├── events/                            # Event type definitions + handler registration
│   ├── types.py                          # Pydantic event schemas (StockLowEvent, ShiftUnderstaffedEvent, ...)
│   └── handlers.py                        # maps event types -> which agent/tool responds
│
├── db/                                  # engine/session factory, Base, migration helpers
│   └── session.py
│
├── config/                              # pydantic-settings Settings, environment loading
│   └── settings.py
│
└── logging/                              # structlog configuration, request/agent correlation IDs
    └── setup.py

alembic/
├── versions/
└── env.py

tests/
├── unit/                 # services, tools, repositories in isolation (mocked/fake dependencies)
├── contract/               # tool & API contract tests: schema conformance, high_impact flag behavior
├── integration/              # real Postgres (Docker) + real repositories + services; approval flow;
│                                event bus end-to-end
└── agent/                       # LangGraph delegation tests using fake tools (no DB, no LLM calls)

docker/
├── docker-compose.yml       # postgres + api services for local dev
├── Dockerfile                  # api service image
└── postgres/
    └── init/                    # any local-only bootstrap SQL (not a substitute for Alembic)
```

**Structure Decision**: Single backend project (`src/dineops/`) with the boundary
expressed as directory structure and import-direction rules, not as separate deployable
services — MVP scale (one location, one concurrent user) does not justify splitting agents,
tools, services, or repositories into independently deployed processes. The layering is
enforced structurally (see [Component Architecture](#component-architecture)) and checked
by import-linting tests in `tests/unit/test_layer_boundaries.py` (see [Testing
Architecture](#testing-architecture)), not just by convention.

## Component Architecture

### Layer boundary (non-negotiable)

```
┌─────────────┐
│   API Layer  │  FastAPI routers — translate HTTP <-> typed calls into the Agent layer
└──────┬──────┘
       │ invokes
┌──────▼──────┐
│ Agent Layer  │  LangGraph graphs — reason, decide which tool(s) to call, in what order
└──────┬──────┘
       │ calls ONLY
┌──────▼──────┐
│  Tool Layer  │  Typed functions — the sole surface agents can act through
└──────┬──────┘
       │ calls ONLY
┌──────▼──────┐
│Service Layer │  Domain/business rules, cross-entity logic, approval/event integration
└──────┬──────┘
       │ calls ONLY
┌──────▼──────┐
│  Repository  │  SQLAlchemy queries/mutations — the ONLY layer that imports SQLAlchemy
│    Layer     │
└──────┬──────┘
       │
┌──────▼──────┐
│  PostgreSQL  │
└─────────────┘
```

Rules, stated explicitly because they are the point of this architecture:

- **Agents never import `sqlalchemy`, a model class, or a repository.** An agent's only
  capability is calling tools registered to it (`tools/registry.py`).
- **Tools never import `sqlalchemy` or a model class, and never open a DB session.** A
  tool's job is: validate its typed input, call exactly one service method (or the
  ApprovalService, or MemoryService), and return its typed output.
- **Services never write raw SQL and never import `asyncpg`/driver internals.** They call
  repository methods and enforce business rules (e.g., "a cancellation of ≥6 covers is
  high-impact") independent of how data is stored.
  Services are the only layer permitted to call ApprovalService or publish to the
  EventBus.
- **Only repositories construct SQLAlchemy queries or sessions.** Repositories know
  nothing about agents, tools, approval, or events — they are the thinnest possible layer
  translating domain calls to persistence.
- **MemoryService is reached only via `tools/memory_tools.py`.** No domain service reads
  or writes memory directly; if a reservation needs a customer's stored preference, the
  Reservation Agent calls a memory tool itself (or the Orchestrator passes memory context
  along) — the Reservation Service never queries the memory tables.

### Agent Layer

- **Orchestrator Agent** (`agents/orchestrator/graph.py`): the only agent exposed to the
  API layer's `/chat` route. Holds no domain tools — its only tool is a routing/delegation
  capability that invokes one or more specialist agent graphs and merges their typed
  results into one response (FR-001–FR-004). It resolves ambiguous requests (Edge Cases in
  spec) by asking a clarifying question rather than guessing when confidence is low.
- **Specialist Agents** (`agents/{reservation,inventory,customer,staffing,analytics}/
  graph.py`): each is a small LangGraph graph bound to exactly its domain's tool subset
  via `tools/registry.py`. A specialist agent never receives another domain's tools.
- Every agent graph node that produces a user-facing or state-changing result does so by
  calling a tool — agents are not permitted to fabricate a result or bypass a tool "for a
  simple case."

### Typed Tool Interface

All tools implement a common shape (`tools/base.py`):

```python
class Tool(Protocol):
    name: str
    description: str                 # shown to the LLM for tool selection
    input_model: type[BaseModel]      # Pydantic model — validated before .run() is called
    output_model: type[BaseModel]     # Pydantic model — the only thing .run() may return
    high_impact: bool                 # if True, ApprovalService gates execution (see below)

    async def run(self, input: BaseModel, *, context: ToolContext) -> BaseModel: ...
```

`ToolContext` carries request-scoped identifiers (correlation ID, acting agent name,
triggering request or event ID) used purely for observability — never a DB session.

- Every tool call is validated against `input_model` before it executes and against
  `output_model` before it's returned to the agent (FR-023, FR-025).
- `high_impact=True` tools do not execute their effect directly; `Tool.run()` for such
  tools calls `ApprovalService.propose(...)` and returns a `PendingApproval` output instead
  of performing the mutation. The actual mutation runs only when ApprovalService later
  confirms approval (see [ApprovalService](#approvalservice)) — this makes the approval gate
  a property of the tool contract, not something each service has to remember to do.
- Full example tool signatures (per agent) are in [contracts/tools.md](./contracts/tools.md).

### MemoryService

- Its own service + repository + tables (`memory.py` model, `memory_repo.py`), separate
  from every domain's own tables (FR-019–FR-022, Constitution III).
- Interface (`services/memory_service.py`):
  ```python
  class MemoryService:
      async def remember(self, scope: MemoryScope, content: MemoryContent) -> MemoryRecord: ...
      async def recall(self, scope: MemoryScope, query: MemoryQuery) -> list[MemoryRecord]: ...
      async def forget(self, memory_id: MemoryID) -> None: ...
  ```
  `MemoryScope` identifies *who/what* a memory is about (e.g., `customer:<id>`,
  `domain:inventory`) — memories are always scoped, never a global bag (FR-020). `recall`
  is a structured lookup by scope (+ optional topic/tag filter) — **not** semantic/vector
  search over free text; this is a deliberate boundary against becoming RAG (Constitution
  II). If future recall needs richer matching, that's an amendment to research, not an
  implicit upgrade.
- Reached by agents exclusively through `tools/memory_tools.py` (`remember_fact`,
  `recall_facts`, `forget_fact`), never called directly by a domain service — this keeps
  memory access visible, auditable (every memory read/write is a logged tool call, feeding
  Observability/FR-033), and swappable later without touching domain services.
- The manager-facing "see and correct a memory" requirement (FR-021) is served by a small
  API route backed by `MemoryService` directly from the API layer (read/delete), not
  through an agent — a human editing their own system's memory is not an agent action.

### ApprovalService

- Owns the propose → approve/reject → execute lifecycle for high-impact tool calls
  (FR-026–FR-029, Constitution IV):
  ```python
  class ApprovalService:
      async def propose(self, request: ApprovalProposal) -> ApprovalRequest: ...
      async def decide(self, approval_id: ApprovalID, decision: ApprovalDecision) -> ApprovalRequest: ...
      async def get_pending(self) -> list[ApprovalRequest]: ...
  ```
- `propose()` persists an `Approval` row (status `pending`) via `approval_repo.py` and
  publishes an `ApprovalRequested` event on the EventBus (so the manager can be notified) —
  it does **not** execute the underlying action.
- `decide()` on approval re-invokes the original tool's effect (via the originating
  service method captured in the proposal) and updates the request to `approved`/executed;
  on rejection it marks `rejected` and nothing changes downstream. Both outcomes are
  recorded to the activity log (FR-035).
- High-impact classification itself (thresholds from spec Assumptions — party size ≥6,
  inventory write-off value, published-schedule changes) lives in each **domain service**,
  not in ApprovalService — ApprovalService is generic machinery; domain services decide
  *when* to invoke it. This keeps ApprovalService reusable across all five domains without
  hardcoding domain rules into it.

### EventBus

- In-process publish/subscribe used to decouple "something happened" from "something reacts
  to it" (FR-030–FR-032):
  ```python
  class EventBus:
      def subscribe(self, event_type: type[Event], handler: EventHandler) -> None: ...
      async def publish(self, event: Event) -> None: ...
  ```
- Domain services publish events after a state change they own commits (e.g.,
  `InventoryService` publishes `StockLevelChanged`; a handler in `events/handlers.py`
  checks the threshold and, if crossed, publishes `StockLowDetected`).
- Handlers registered in `events/handlers.py` invoke the relevant specialist agent's
  notification tool (not the Orchestrator) — event-triggered actions go through the same
  Tool layer as manager-requested ones, so they're logged identically (FR-032) and subject
  to the same approval gate if they happen to be high-impact.
- For MVP scale (single process, single restaurant), the bus is an in-process
  implementation backed by an `Event` table for durability/replay and audit — not an
  external broker (Kafka/RabbitMQ); see [research.md](./research.md) for the alternatives
  considered and why this is deferred.

### API Layer

Three route groups, thin by design — they translate HTTP to typed calls and back, no
business logic:

- `POST /chat` — manager's message → Orchestrator graph → response (may include one or
  more `PendingApproval` results).
- `GET /approvals`, `POST /approvals/{id}/decision` — list pending approvals, record a
  decision (FR-027, FR-028).
- `GET /activity` — paginated agent activity log with filters (agent, tool, trigger type,
  time range) (FR-033–FR-035).

Full request/response schemas are in [contracts/api.md](./contracts/api.md).

### Domain Services & Repository Layer

One service + one repository per domain (Reservation, Inventory, Customer, Staffing,
Analytics), following the same shape:

- **Service**: business rules, high-impact classification, calls its own repository (and,
  where a request spans domains — e.g., Analytics reading Reservation + Inventory data —
  calls the *other domain's repository interface*, never its service, to avoid circular
  service dependencies), and publishes domain events.
- **Repository**: SQLAlchemy queries/mutations for exactly one domain's tables, returning
  domain objects (not raw ORM rows) to the service layer.

Analytics is a read-only special case: `analytics_repo.py` runs aggregate queries across
other domains' tables directly (it's still a repository, still the only layer touching
SQLAlchemy) rather than the Analytics Service calling four other services — this avoids
forcing cross-domain analytics through chatty service-to-service calls while still
respecting "only repositories touch the DB."

### Configuration Management

`config/settings.py` — a single `pydantic-settings` `Settings` class loading from
environment variables (with a `.env` for local dev, `.env.example` committed). Covers:
database URL, API host/port, logging level/format, feature-level toggles (e.g.,
high-impact thresholds sourced here so they're configurable per spec Assumptions without a
code change), and LangGraph/LLM provider configuration (API keys via env, never
hardcoded). One `Settings` instance is constructed at startup and passed via FastAPI
dependency injection — no module reaches into `os.environ` directly outside this module.

### Logging & Observability

- `structlog`-based structured logging (JSON in non-local environments), with a
  correlation ID generated per API request and threaded through
  Agent → Tool → Service → Repository calls via `ToolContext` / a `contextvars`-backed
  request context — so one manager request's full call chain is traceable.
- Every tool invocation (regardless of outcome) is recorded via `agent_run_repo.py` as an
  `AgentRun` (acting agent, trigger, outcome) with its step-by-step `AgentMessage` trace
  (tool name, input/output, and — if high-impact — the linked `Approval` id) — see
  [data-model.md](./data-model.md#15-agentrun) (FR-033–FR-035). This is durable, queryable
  storage, not just log lines, because FR-034 requires the manager to review it through
  the product, not by reading logs.
- Structured logs remain the mechanism for developer-facing debugging (stack traces,
  timing); the activity log table is the mechanism for manager-facing observability. They
  share a correlation ID so one can jump from a log line to its activity record.

### Testing Architecture

Four test tiers, matching the layer boundary so a broken boundary rule shows up as a
failing test, not a code review comment:

- **`tests/unit/`** — repositories tested against a real (Dockerized, migrated) Postgres
  per FR-036 "in isolation," but with no service/tool/agent involved; services tested with
  a fake repository (no DB) to isolate business rules; tools tested with a fake service.
  Includes `test_layer_boundaries.py`, which statically asserts (via import inspection)
  that `agents/` and `tools/` modules never import `sqlalchemy` or anything under
  `repositories/`/`models/` — turning the Constitution I gate into a running test.
- **`tests/contract/`** — every tool's `input_model`/`output_model` validated against
  representative payloads, including that `high_impact=True` tools return a
  `PendingApproval` rather than a direct effect (FR-025). API route request/response
  bodies validated against `contracts/api.md`.
- **`tests/integration/`** — real Postgres (via Docker Compose), real repositories and
  services wired together: full propose→approve→execute approval flow, full
  publish→handle event flow, cross-domain memory recall (Customer Agent writes, Reservation
  Agent reads).
- **`tests/agent/`** — LangGraph delegation correctness (FR-002, FR-038): given a request,
  does the Orchestrator pick the right specialist agent(s)? Uses fake tools (no DB, no
  live LLM call in CI) so this tier is fast and deterministic; a small separate
  eval-style suite (outside CI-blocking tests) may exercise real LLM calls for delegation
  accuracy per SC-006, run on demand.

CI gate: unit + contract + agent tiers run on every change; integration tier runs against
the Dockerized Postgres in CI as well before merge.

### Docker Development Environment

`docker/docker-compose.yml` defines two services for local dev:

- `postgres` — Postgres 16, named volume for persistence, exposes the port used by both
  the app and the test suite's integration tier; an `init/` folder for one-time local
  bootstrap only (extensions, roles) — schema itself always comes from Alembic, never from
  init SQL, so dev and prod stay on the same migration history.
- `api` — builds from `docker/Dockerfile`, runs the FastAPI app with reload for local dev,
  depends on `postgres` being healthy, reads configuration from `.env` via `config/
  settings.py`.

`alembic upgrade head` is a documented, explicit step (via `quickstart.md`) rather than
auto-run on container start, so migrations are always a visible, deliberate action.

## Constitution Check (post-design re-check)

Re-checked after Phase 1 design ([data-model.md](./data-model.md),
[contracts/](./contracts/)):

| # | Principle | Result |
|---|-----------|--------|
| 1 | Agent-First, Tool-Mediated Access | **PASS** — `agents/` and `tools/` contain zero references to `sqlalchemy`, `models/`, or `repositories/` in the designed module boundaries; enforced by `test_layer_boundaries.py`. |
| 2 | No RAG | **PASS** — `data-model.md` and `contracts/` introduce no vector/embedding/document types. `MemoryService.recall` is confirmed structured-lookup only. |
| 3 | Custom MemoryService | **PASS** — `memory.py`/`memory_repo.py` are a dedicated schema; only `tools/memory_tools.py` reaches `MemoryService`. |
| 4 | Human Approval | **PASS** — `contracts/tools.md` shows `high_impact` tools returning `PendingApproval`; `data-model.md` defines `Approval` state transitions. |
| 5 | Orchestrator + Specialized Agents | **PASS** — `tools/registry.py` binds tool subsets per agent; Orchestrator's own tool set is delegation-only. |
| 6 | Typed Contracts | **PASS** — all cross-layer payloads in `contracts/` are Pydantic models with named fields, no `dict[str, Any]` payloads at a layer boundary. |

No violations; no complexity to justify.

## Complexity Tracking

*Not applicable — no constitution violations were introduced by this design.*
