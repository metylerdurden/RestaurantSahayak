# DineOps Development Roadmap

This document records how DineOps was actually built: 20 sequential, numbered
increments, each with an objective, the real engineering decisions made, the
technologies involved, and what it delivered. Status for every step is based on
inspecting this repository directly (commit history, current source tree, test suite) —
not on a plan written before the fact.

## How the numbering relates to the repo's actual history

The first five steps below are genuine [GitHub Spec Kit](https://github.com/github/spec-kit)
artifacts, produced by `/speckit-constitution`, `/speckit-specify`, and `/speckit-plan`
(twice — an initial pass and a follow-up elaboration of the data model), and
`/speckit-tasks`. `/speckit-tasks` produced [`specs/001-core-platform-mvp/tasks.md`](../specs/001-core-platform-mvp/tasks.md),
a **12-phase** implementation breakdown — not 20 phases. Steps 6–19 below implement
those 12 phases, but not in a clean 1:1 fashion:

- **Phase 5** in `tasks.md` ("Implement and Test the Inventory Agent") was not delivered
  as its own commit. Its tools and service already existed from Phase 3, and the
  `InventoryAgent` class itself was committed together with the Orchestrator (Step 14
  below) rather than on its own — visible directly in `git log -- app/agents/inventory_agent.py`,
  which shows exactly one commit, shared with the orchestrator.
- **Phase 7** ("Staffing and Analytics Agents") was split into two separate commits —
  Steps 12 and 13 below.
- **Phase 10** ("Event-Driven Workflows and Background Jobs") was likewise split into
  two commits — Steps 16 and 17 below.
- From that point on, commit messages themselves switched from "Phase N" to "Step N"
  language and adopted the numbering this document uses (Observability became Step 18,
  Manager API/Dashboard became Step 19).

This 20-step framing is this repository's own retrospective account of its history,
reconstructed from `git log` for the purpose of this document — not a second Spec Kit
artifact. It is presented here because it is what actually happened, split and combined
commits included, rather than a tidier account than the real one.

Each step below names the actual commit(s) that delivered it. Step 20 (this hardening
and documentation pass) is still in progress in the working tree at the time of writing
and has not been committed.

---

## Step 1 — Project Initialization

**Status: IMPLEMENTED** · Commit: `ae40891 Initialize DineOps with Spec Kit and project constitution`

**Objective**: Stand up the repository and its governing principles before any product
or technical decision was made.

**Engineering decisions**: Adopted GitHub Spec Kit's slash-command workflow
(`/speckit-specify` → `/speckit-plan` → `/speckit-tasks` → `/speckit-implement`) as the
development methodology for this project, and wrote a project constitution
up front — non-negotiable principles (agent-first/tool-mediated access, no RAG, a
custom MemoryService, human approval for high-impact actions) that every later step is
still checked against.

**Technologies**: GitHub Spec Kit tooling (`.claude/skills/speckit-*`).

**Deliverables**: `.specify/` configuration and templates,
[`.specify/memory/constitution.md`](../.specify/memory/constitution.md), `.gitignore`.

---

## Step 2 — Product Specification

**Status: IMPLEMENTED** · Commit: `33f142f Add core platform MVP specification (Spec Kit)`

**Objective**: Define what DineOps must do — in product/requirements terms, before any
architecture — via `/speckit-specify`.

**Engineering decisions**: Scoped the MVP explicitly: one restaurant, one manager
role, exactly five specialist domains (Reservation, Customer, Inventory, Staffing,
Analytics) plus the Orchestrator, no document upload/RAG capability. Requirements were
written to be testable, not aspirational.

**Technologies**: Spec Kit spec template.

**Deliverables**: [`specs/001-core-platform-mvp/spec.md`](../specs/001-core-platform-mvp/spec.md),
[`specs/001-core-platform-mvp/checklists/requirements.md`](../specs/001-core-platform-mvp/checklists/requirements.md).

---

## Step 3 — Technical Architecture

**Status: IMPLEMENTED** · Commit: `bd3b1f1 Add technical architecture (Spec Kit plan) for core platform MVP`

**Objective**: Translate the spec into a concrete technical design via `/speckit-plan`:
stack choices, layering rules, and contracts.

**Engineering decisions**: Committed to the layered boundary that the rest of the
project enforces mechanically (`Agent → Tool → Service → Repository → Database`, no
agent/tool touches SQLAlchemy directly); chose LangGraph specifically for the
Orchestrator rather than for every agent; chose a custom MemoryService over any
off-the-shelf vector-memory library; resolved open technical questions (Phase 0
research) before design.

**Technologies**: FastAPI, PostgreSQL, SQLAlchemy, Alembic, LangGraph, Docker.

**Deliverables**: [`specs/001-core-platform-mvp/plan.md`](../specs/001-core-platform-mvp/plan.md),
[`research.md`](../specs/001-core-platform-mvp/research.md), an initial
[`data-model.md`](../specs/001-core-platform-mvp/data-model.md),
[`contracts/`](../specs/001-core-platform-mvp/contracts/) (`api.md`, `events.md`, `tools.md`),
[`quickstart.md`](../specs/001-core-platform-mvp/quickstart.md).

---

## Step 4 — Domain and Database Design

**Status: IMPLEMENTED** · Commit: `7418829 Design full domain model and PostgreSQL schema for core platform MVP`

**Objective**: Elaborate the plan phase's initial data model into a complete,
implementation-ready schema for every entity the MVP needs.

**Engineering decisions**: Fully specified every table (reservations, customers,
inventory, staff/shifts, sales, memory, approvals, events, agent runs) including the
`pgvector` column for memory embeddings, before any SQLAlchemy model or migration
existed.

**Technologies**: PostgreSQL, pgvector.

**Deliverables**: `data-model.md` expanded from 163 to 767 lines — the schema every
model in `app/models/` and every Alembic migration was later built from.

---

## Step 5 — Implementation Plan

**Status: IMPLEMENTED** · Commit: `cff1fb9 Add 12-phase implementation roadmap (Spec Kit tasks)`

**Objective**: Break the full architecture and data model into an ordered, actionable
task list via `/speckit-tasks`, so implementation could proceed incrementally rather
than all at once.

**Engineering decisions**: Ordered phases so each built on a working previous one
(foundation → data → services/tools → one agent at a time → orchestration → approval →
events/background → observability → API/dashboard), rather than a big-bang build.

**Technologies**: Spec Kit tasks template.

**Deliverables**: [`specs/001-core-platform-mvp/tasks.md`](../specs/001-core-platform-mvp/tasks.md)
(12 phases; see the numbering note above for how these map onto this 20-step account).

---

## Step 6 — Project Foundation and Model Abstraction

**Status: IMPLEMENTED** · Commit: `aa61651 Implement Phase 1: project foundation, config, LLM/embedding providers`

**Objective**: Stand up the runnable skeleton — config, database connectivity, and the
provider abstractions every agent and the memory subsystem would later depend on.

**Engineering decisions**: `LLMProvider` and `EmbeddingProvider` defined as abstract
interfaces from day one (`app/llm/base.py`, `app/embeddings/base.py`), with Ollama/
Qwen3-8B and BGE-M3 as the concrete implementations selected via `Settings` — no agent
or service code ever names a model directly. All configuration centralized in
`app/core/config.py` (`Settings`, `pydantic-settings`) — no module reads `os.environ`
directly.

**Technologies**: FastAPI, `pydantic-settings`, SQLAlchemy (async), Alembic, Docker,
structlog.

**Deliverables**: `app/core/` (config, db, logging), `app/llm/` (`LLMProvider` +
`OllamaLLMProvider`), `app/embeddings/` (`EmbeddingProvider` + `BGEEmbeddingProvider`),
initial FastAPI app + health checks, `docker/Dockerfile`, `docker/docker-compose.yml`.

---

## Step 7 — Database and Persistent Memory Storage

**Status: IMPLEMENTED** · Commit: `e5e8bc5 Implement Phase 2: domain models, migrations, seed data, pgvector memory`

**Objective**: Turn the Step 4 schema into real SQLAlchemy models and migrations,
including the `memories` table's `pgvector` column — the storage layer memory would
later be built on.

**Engineering decisions**: Every domain entity became its own SQLAlchemy model under
`app/models/`; the `pgvector` extension and the `memories.embedding` column were added
at this stage even though `MemoryService` itself didn't exist yet (delivered in Step
10), so the storage contract was fixed early.

**Technologies**: SQLAlchemy, Alembic, PostgreSQL, pgvector.

**Deliverables**: `app/models/` (all entities), `alembic/versions/` initial migrations,
`scripts/seed.py`.

---

## Step 8 — Domain Services and Typed Tools

**Status: IMPLEMENTED** · Commit: `1786447 Implement Phase 3: domain services, repositories, and typed tools`

**Objective**: Build the two layers between the database and any future agent: business
logic (services) and the typed capability surface agents would eventually be allowed to
call (tools) — before any agent existed to call them.

**Engineering decisions**: Established the `Tool` base contract (`app/tools/base.py`) —
one tool validates typed input, calls exactly one service method, returns typed output,
and never sees SQLAlchemy — and the repository layer as the only code permitted to
issue SQL. This ordering (tools before agents) meant the typed-tool contract was
designed against real business logic, not against an imagined agent's needs.

**Technologies**: Pydantic, SQLAlchemy.

**Deliverables**: `app/repositories/`, `app/services/` (reservation, inventory,
staffing, customer, analytics — business logic only, no LLM), `app/tools/` (reservation,
inventory, staffing, customer, analytics tool implementations).

---

## Step 9 — Reservation Agent

**Status: IMPLEMENTED** · Commit: `f4cd881 Implement Phase 4: Reservation Agent`

**Objective**: Build the first real agent end-to-end — reasoning loop, LLM integration,
and one specialist domain — proving the whole stack works before repeating the pattern
four more times.

**Engineering decisions**: Introduced `ToolCallingAgent`, the generic bounded
tool-calling loop every specialist agent (not the Orchestrator) is built on: ask the
model for the next step, execute whatever tool calls it made, feed results back, repeat
until a final answer or an iteration cap. `ReservationAgent` itself only fixes a name,
system prompt, and tool set on top of that shared loop — no domain-specific loop logic.

**Technologies**: Qwen3-8B (via Ollama), function-calling/tool-use prompting.

**Deliverables**: `app/agents/tool_calling_agent.py`, `app/agents/reservation_agent.py`,
`app/agents/prompts.py` (initial), `app/agents/state.py` (`AgentResult`).

---

## Step 10 — Persistent Memory + Customer Agent

**Status: IMPLEMENTED** · Commit: `f78f5a7 Implement Phase 6: persistent MemoryService and Customer Agent`

**Objective**: Build the persistent long-term memory subsystem and the Customer Agent
that relies on it most — the mechanism that lets DineOps recall a fact across separate
conversations without RAG.

**Engineering decisions**: `MemoryService` designed around structured facts (one
`Memory` row = one typed fact, with `memory_type`/`topic`/`source`/`importance`/
`confidence`), not document chunks — embeddings (BGE-M3) are used only to search those
facts by meaning, never to retrieve document text into a prompt. Built a full memory
lifecycle (dedup on exact key conflict, reinforcement, soft-delete/forgetting), exposed
to agents only through typed memory tools, never called directly by other services.

**Technologies**: BGE-M3 (`sentence-transformers`), pgvector, Qwen3-8B.

**Deliverables**: `app/services/memory_service.py`, `app/tools/memory_tools.py`,
`app/agents/customer_agent.py`.

---

## Step 11 — Inventory Agent

**Status: IMPLEMENTED** · No standalone commit — delivered together with Step 14
(`4ab11f8 Implement the Orchestrator Agent using LangGraph`).

**Objective**: Per `tasks.md` Phase 5, this was planned as its own step. In practice,
`app/tools/inventory_tools.py` and `InventoryService` already existed from Step 8, and
`InventoryAgent` itself (the thin `ToolCallingAgent` subclass) was committed alongside
the Orchestrator rather than separately — confirmed directly by `git log --
app/agents/inventory_agent.py`, which shows exactly one commit, shared with the
orchestrator's. This document states that plainly rather than implying a commit that
doesn't exist.

**Engineering decisions**: Same pattern as every other specialist — `InventoryAgent`
fixes a name, system prompt, and tool set (`GetInventoryTool`, `CheckStockTool`,
`CalculateRequiredInventoryTool`, `CreatePurchaseRequestTool`) on `ToolCallingAgent`;
`CreatePurchaseRequestTool` is the one that can return a `pending_approval` result once
the human-approval system (Step 15) exists.

**Technologies**: Qwen3-8B.

**Deliverables**: `app/agents/inventory_agent.py`.

---

## Step 12 — Staffing Agent

**Status: IMPLEMENTED** · Commit: `d4f83b2 Implement the Staffing Agent`

**Objective**: Add the staffing specialist — shift schedules, staff availability, and
requirement calculations.

**Engineering decisions**: Same `ToolCallingAgent` pattern; no new architectural
decisions were needed by this point, which is itself evidence the Step 9 abstraction
was doing its job.

**Technologies**: Qwen3-8B.

**Deliverables**: `app/agents/staffing_agent.py`, live integration test
(`tests/integration/test_staffing_agent_live.py`) proving the agent against a real
running model.

---

## Step 13 — Analytics Agent

**Status: IMPLEMENTED** · Commit: `b89cda0 Implement the Analytics Agent`

**Objective**: Add the analytics specialist — sales, item performance, no-show rate —
answering strictly from existing structured data.

**Engineering decisions**: Deliberately no document or report ingestion — every
analytics answer is grounded in a real tool call over structured tables
(`GetDailySalesTool`, `GetItemSalesTool`, `GetNoShowRateTool`), consistent with the
project's no-RAG constitution even for a domain (analytics) where a lesser design might
have reached for document summarization.

**Technologies**: Qwen3-8B.

**Deliverables**: `app/agents/analytics_agent.py`,
`tests/integration/test_analytics_agent_live.py`.

---

## Step 14 — Multi-Agent Orchestrator

**Status: IMPLEMENTED** · Commit: `4ab11f8 Implement the Orchestrator Agent using LangGraph`

**Objective**: Build the coordinator that lets a manager ask one question spanning
multiple domains, without knowing which specialist(s) can answer it.

**Engineering decisions**: The one agent built on LangGraph rather than the shared
`ToolCallingAgent` loop — coordinating other agents (decide who's needed, delegate,
decide if someone else is now needed, loop, combine) is a genuinely graph-shaped
control problem. The Orchestrator's only interface to a specialist is that specialist's
own `.handle()` — it never imports a specialist's tools, services, or repositories, so
it cannot duplicate a specialist's domain logic even by accident.

**Technologies**: LangGraph (`StateGraph`, `InMemorySaver` checkpointer).

**Deliverables**: `app/agents/orchestrator_agent.py`, `app/agents/orchestrator_state.py`,
`app/agents/inventory_agent.py` (see Step 11).

---

## Step 15 — Human Approval System

**Status: IMPLEMENTED** · Commit: `7c48818 Implement the Human Approval system: risk levels, execution pipeline, and LangGraph pause/resume`

**Objective**: Ensure actions with real operational/financial consequence require a
human decision before they take effect, instead of an agent executing them
unilaterally.

**Engineering decisions**: Risk classification (`LOW`/`MEDIUM`/`HIGH`) and the decision
of *whether* an action is high-impact live in the domain service (e.g.
`ReservationService`, `InventoryService`), based on concrete configurable thresholds
(`reservation_high_impact_party_size`, `purchase_request_high_impact_cost_threshold`) —
not in the agent or the tool. `ApprovalService.approve()`/`reject()` is the only code
path that ever executes a gated action. When a gated tool call happens inside an
Orchestrator-delegated request, the LangGraph run genuinely pauses (`interrupt()`,
backed by an in-memory checkpointer) rather than reporting a false completion, and
resumes only via `OrchestratorAgent.resume()`.

**Technologies**: LangGraph `interrupt()`/`Command(resume=...)`.

**Deliverables**: `app/models/approval.py`, `app/services/approval_service.py`,
`app/services/approval_execution.py`, `app/api/routes/approvals.py`, Orchestrator
pause/resume graph nodes.

---

## Step 16 — Event-Driven Agent Communication

**Status: IMPLEMENTED** · Commit: `5cac9ee Implement Step 16: Event-Driven Workflows`

**Objective**: Let the system react to what already happened (a reservation created,
stock dropping low) without a manager asking, instead of only responding to direct
chat requests.

**Engineering decisions**: Synchronous, in-process `EventBus` dispatch, deliberately —
this codebase uses one shared `AsyncSession` per request/transaction, and a
fire-and-forget background task sharing that session would be a real correctness bug,
not a style choice; publishing and dispatch happen in the same transaction as the
mutation that triggered them. Each handler is isolated and retried independently, and
publishing never raises because a handler failed. Documented explicitly as an MVP
tradeoff, built behind an interface a production deployment could move to a real broker
behind without touching any publisher or handler.

**Technologies**: In-process pub/sub (`app/services/event_bus.py`).

**Deliverables**: `app/services/event_bus.py`, `app/models/event.py`,
`app/workflows/inventory_workflow.py`, `app/workflows/registry.py`.

---

## Step 17 — Autonomous Background Workflows

**Status: IMPLEMENTED** · Commit: `1e99f04 Implement Step 17: Background and Autonomous Agent Workflows`

**Objective**: Let DineOps do work on a schedule, with no trigger at all from a
manager or an event.

**Engineering decisions**: `BackgroundWorkflow` mirrors `ToolCallingAgent`'s role for
specialist agents — it owns generic run-tracking (creation, status, timing, error
containment) so each concrete workflow only implements its own domain logic, and can
never call a Tool or Service directly, only an agent's `.handle()` — so a scheduled
workflow can no more bypass the approval gate than a manager-initiated request can.
`AsyncIOScheduler` is fixed-interval, not calendar-aware, documented as a real gap
rather than hidden — a production scheduler would sit behind the same `Scheduler`
interface.

**Technologies**: `asyncio`-based in-process scheduler.

**Deliverables**: `app/workflows/scheduler.py`, `app/workflows/background_workflow.py`,
`app/workflows/background_registry.py`, `daily_briefing_workflow.py`,
`inventory_monitoring_workflow.py`, `reservation_monitoring_workflow.py`,
`staffing_monitoring_workflow.py`.

---

## Step 18 — OpenTelemetry Observability

**Status: IMPLEMENTED** · Commit: `9bb9a89 Implement Step 18: Observability with OpenTelemetry`

**Objective**: Make every agent run, tool call, LLM call, memory operation, event, and
approval decision inspectable — what happened and why — not just logged as text.

**Engineering decisions**: Every operation traced as a span with a consistent set of
identifying attributes (restaurant/agent/correlation id), while explicitly excluding
free-text operational/customer content (memory `topic`/`content`, tool `parameters`,
approval `reason`) from span attributes — observability without leaking sensitive
content into a tracing backend. Console export by default (no extra infrastructure);
OTLP/Jaeger as an opt-in for a UI.

**Technologies**: OpenTelemetry SDK, OTLP exporter, Jaeger (optional, via
`docker-compose.observability.yml`), structlog (correlation-id-tagged logging).

**Deliverables**: `app/core/telemetry.py`, span instrumentation across
`app/tools/base.py`, `app/services/memory_service.py`, `app/services/event_bus.py`, and
every agent, `docker/docker-compose.observability.yml`.

---

## Step 19 — Manager API and Dashboard

**Status: IMPLEMENTED** · Commit: `f572927 Implement Step 19: DineOps Manager API and Dashboard`

**Objective**: Give a human a way to actually use everything built so far — trigger
agents, review approvals, watch traces — without a raw API client.

**Engineering decisions**: A no-build-step static dashboard (`app/static/`, plain
HTML/CSS/JS) served by the same FastAPI app rather than a separate frontend project —
appropriate for this MVP's scope. The Agent Activity view renders the OpenTelemetry-adjacent
trace tree (Orchestrator → Specialist → Tool → Result) built from `AgentRun`/
`AgentMessage` records via `AgentActivityService`, not from the trace backend directly —
so it works even with `OTEL_EXPORTER=none`.

**Technologies**: FastAPI, plain HTML/CSS/JS.

**Deliverables**: `app/api/routes/` (agent_runs, reservations, inventory, customers,
approvals, workflows, dashboard, restaurants), `app/services/agent_activity_service.py`,
`app/services/dashboard_service.py`, `app/static/`.

---

## Step 20 — Production Hardening and GitHub Release

**Status: PARTIALLY IMPLEMENTED — in progress, not yet committed or pushed.**

**Objective**: Harden the implementation for correctness and safety, validate it end to
end, make it reproducible, document it thoroughly, and prepare it for a public GitHub
release — without changing the model architecture or adding major features.

**Sub-steps completed so far (as uncommitted working-tree changes)**:

- **20.1 — Security audit and hardening**: an opt-in shared-secret API boundary
  (`app/api/security.py`, `require_api_key`), wired onto every Manager API route except
  `/health`; `.env.example`/`.gitignore` hardening.
- **20.2 — Reliability hardening and complete testing**: bounded LLM-call retries
  (never infinite), broadened exception handling so a best-effort side effect (e.g.
  recording a completion) can never poison an otherwise-successful run, SAVEPOINT
  isolation for `ApprovalService`'s best-effort DB work, and a full test-coverage
  review across every architectural component.
- **20.3 — Docker and CI/CD validation**: switched the Postgres image to
  `pgvector/pgvector:pg16` (plain `postgres:16` lacks the extension the `memories`
  table needs), added a real `.github/workflows/ci.yml` (lint, format check, type
  check, non-live test suite against a real Postgres service container, Docker build
  verification). The full live-model suite run performed as part of this validation
  surfaced two genuine agent-behavior bugs no earlier step had caught: a readiness
  check that could finish after consulting only one specialist, and a reservation
  workflow that didn't reliably surface a customer's persisted preference. Both were
  fixed in a dedicated follow-up pass — a deterministic completeness guarantee layered
  onto the Orchestrator's existing LLM-driven delegation (not a rewrite of it) for the
  first, and a broadened memory-recall trigger condition for the second — verified with
  5 repeated live-model runs of each previously-failing test before being accepted as
  fixed.
- **20.4 — Professional documentation**: rewrote `README.md` (architecture, model
  architecture, persistent-memory-vs-RAG, setup, four worked examples, limitations,
  roadmap, project structure), validated against the actual codebase.
- **20.5 — Spec-driven development documentation** (this pass): this document, plus
  the README's "Spec-Driven Development" and "How This Project Was Built" sections.

**Not yet done**: none of the above has been committed; nothing has been pushed to a
remote; no GitHub release/tag exists. `git remote -v` shows no configured remote at the
time of writing.

**Technologies**: `ruff`, `mypy`, GitHub Actions, `pgvector/pgvector:pg16`.
