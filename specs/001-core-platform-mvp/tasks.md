# Implementation Roadmap: DineOps Core Platform MVP

**Input**: [spec.md](./spec.md), [plan.md](./plan.md), [research.md](./research.md),
[data-model.md](./data-model.md), [contracts/](./contracts/), [quickstart.md](./quickstart.md)

**Organization**: This roadmap is organized by **build phase** (1–12, as specified),
not by user story priority as Spec Kit's default template would — the phases are
sequenced by architectural layer (foundation → data → services/tools → agents one at a
time → orchestration → approval/events → observability → manager surface) because that
is how this system must be *built and proven*, even though the *spec* is organized by
user value. Every phase cross-references the FR-xxx / SC-xxx / User Story it advances so
traceability back to [spec.md](./spec.md) is never lost. No implementation has been done
yet — this is the plan only.

**Task ID format**: `P<phase>.<seq>`. **[P]** = can run in parallel with sibling tasks
(different files, no ordering dependency). Exact file paths match
[plan.md's Project Structure](./plan.md#project-structure) and
[data-model.md](./data-model.md).

## Important cross-phase note: intentional deferrals

Because agents are built one at a time (Phases 4–7) before the Orchestrator (Phase 8),
and because MemoryService/EventBus are scheduled in Phases 6/10 rather than Phase 3, a
few spec requirements are **structurally split across two phases** — implemented
partially where a domain first needs them, completed once their subsystem exists. Each
is called out explicitly in the phase where it's opened and the phase where it's closed,
so no phase's acceptance criteria overclaim:

| Capability | Opened in | Closed in |
|---|---|---|
| Reservation Agent surfaces stored customer preference (spec US1 Scenario 2) | Phase 4 (not yet possible) | Phase 6 (MemoryService + registry retrofit) |
| Approval propose/decide mechanism | Phase 3 (core service, no API) | Phase 9 (manager-facing API + expiry) |
| Proactive low-stock / understaffed-shift alerts (US3) | Phase 5 / Phase 7 (not yet possible) | Phase 10 (EventBus + handlers) |
| Manager-visible activity feed / memory review UI | data exists from Phase 2 onward | Phase 12 (API + dashboard) |

---

## Phase 1: Project Foundation, Configuration, Docker, FastAPI, PostgreSQL, SQLAlchemy, Alembic, Logging, Health Checks

**Depends on**: nothing — first phase.

**Advances**: Constitution's Technology Stack Constraints; plan.md Technical Context,
Docker Development Environment, Configuration Management, Logging & Observability
sections. No functional requirements yet (pure infrastructure).

### Files to create

| Path | Purpose |
|---|---|
| `pyproject.toml` | Project metadata + dependencies, managed with `uv` |
| `.env.example` | Documented env vars (copied to `.env` locally, never committed) |
| `.gitignore` (update) | Confirm `.env`, `__pycache__`, etc. already covered (done at project init) |
| `src/dineops/__init__.py` | Package root |
| `src/dineops/config/__init__.py`, `settings.py` | `pydantic-settings` `Settings`: `DATABASE_URL`, `API_HOST`, `API_PORT`, `LOG_LEVEL`, `LOG_FORMAT`, `ENVIRONMENT` |
| `src/dineops/logging/__init__.py`, `setup.py`, `context.py` | `structlog` configuration; `context.py` holds the correlation-id `contextvars` helper |
| `src/dineops/db/__init__.py`, `base.py`, `session.py` | `Base` declarative class; async engine + `async_sessionmaker`; `get_db_session()` FastAPI dependency |
| `src/dineops/api/__init__.py`, `main.py`, `deps.py` | App factory (`create_app()`), settings/logging wiring, exception handlers, CORS |
| `src/dineops/api/routes/__init__.py`, `health.py` | `GET /health/live`, `GET /health/ready` |
| `src/dineops/agents/__init__.py`, `tools/__init__.py`, `services/__init__.py`, `repositories/__init__.py`, `models/__init__.py`, `events/__init__.py` | Empty stub packages so the layer-boundary test has something to inspect from day one |
| `alembic.ini`, `alembic/env.py` | Alembic harness wired to `Settings.DATABASE_URL` and `Base.metadata` (empty until Phase 2) |
| `docker/docker-compose.yml` | `postgres` (16, healthcheck, named volume) + `api` (builds from `docker/Dockerfile`, `depends_on: postgres: condition: service_healthy`) |
| `docker/Dockerfile` | `python:3.12-slim`, installs `uv`, `uv sync`, runs `uvicorn` |
| `docker/postgres/init/.gitkeep` | Placeholder — schema always comes from Alembic, not init SQL (per plan.md) |
| `tests/__init__.py`, `tests/conftest.py` | Shared fixtures: test `Settings`, event loop, (later phases add DB fixtures here) |
| `tests/unit/test_settings.py` | Settings loads/validates required env vars |
| `tests/unit/test_layer_boundaries.py` | Import-inspection: nothing under `agents/`/`tools/` imports `sqlalchemy`, `dineops.models`, or `dineops.repositories` |
| `tests/integration/test_health.py` | `GET /health/live` and `/health/ready` via `httpx.AsyncClient` |

### Implementation tasks

1. **P1.1** [P] Initialize `pyproject.toml` with `uv`; add `fastapi`, `uvicorn[standard]`,
   `sqlalchemy[asyncio]`, `asyncpg`, `alembic`, `pydantic`, `pydantic-settings`,
   `structlog`, `pytest`, `pytest-asyncio`, `httpx` as dependencies.
2. **P1.2** [P] Write `config/settings.py` — typed `Settings(BaseSettings)`, fails fast on
   missing `DATABASE_URL` at import time.
3. **P1.3** [P] Write `logging/setup.py` + `context.py` — `structlog` JSON renderer for
   non-local `ENVIRONMENT`, console renderer for local; `get_correlation_id()` /
   `bind_correlation_id()` via `contextvars`.
4. **P1.4** Write `db/base.py` (`Base = DeclarativeBase`) and `db/session.py` (async
   engine from `Settings.DATABASE_URL`, `async_sessionmaker`, `get_db_session()`
   dependency yielding a session per request, committing/rolling back).
5. **P1.5** Write `api/main.py` (`create_app()`: instantiate `Settings`, configure
   logging, register a correlation-id middleware that binds a UUID per request and
   returns it as `X-Correlation-Id`, register the `health` router, generic exception
   handler returning the `{"error": {...}}` shape from contracts/api.md) and `api/deps.py`
   (re-exports `get_db_session`, placeholder `get_settings`).
6. **P1.6** [P] Write `api/routes/health.py` — `/health/live` always 200 once the process
   is up; `/health/ready` runs `SELECT 1` through `get_db_session()`, 200 if it succeeds,
   503 otherwise.
7. **P1.7** Create empty stub packages: `agents/__init__.py`, `tools/__init__.py`,
   `services/__init__.py`, `repositories/__init__.py`, `models/__init__.py`,
   `events/__init__.py`.
8. **P1.8** Initialize Alembic (`alembic init -t async alembic`), point `env.py` at
   `Settings.DATABASE_URL` and `Base.metadata`.
9. **P1.9** [P] Write `docker/docker-compose.yml` and `docker/Dockerfile`.
10. **P1.10** [P] Write `.env.example` documenting every `Settings` field.
11. **P1.11** [P] Write `tests/unit/test_layer_boundaries.py` (parses `agents/` and
    `tools/` source with `ast`, asserts no `import sqlalchemy`, `from dineops.models`, or
    `from dineops.repositories`).
12. **P1.12** [P] Write `tests/unit/test_settings.py` and
    `tests/integration/test_health.py`.

### Dependencies (internal ordering)

P1.1 → P1.2/P1.3/P1.4 (need dependencies installed) → P1.5 (needs settings/logging/db) →
P1.6 (needs `main.py`'s app + `get_db_session`) → P1.9/P1.8 can happen any time after
P1.1. P1.11/P1.12 can be written anytime but only pass once P1.5–P1.7 exist.

### Tests

- `tests/unit/test_settings.py` — missing `DATABASE_URL` raises a clear validation error.
- `tests/unit/test_layer_boundaries.py` — passes trivially now (empty packages); becomes
  a real guardrail from Phase 3 onward.
- `tests/integration/test_health.py` — `/health/live` → 200 always; `/health/ready` → 200
  when `docker compose up -d postgres` is running.

### Acceptance criteria

- `docker compose -f docker/docker-compose.yml up -d postgres` yields a healthy container.
- `alembic upgrade head` runs cleanly (no-op, zero versions yet) with no errors.
- `uv run uvicorn dineops.api.main:app --reload` starts; `curl localhost:8000/health/live`
  → `200`; `curl localhost:8000/health/ready` → `200` while Postgres is up, `503` if
  stopped.
- Every request logs one structured JSON line (non-local env) including a
  `correlation_id` that matches the `X-Correlation-Id` response header.
- No business logic exists yet — `models/`, `services/`, `tools/`, `agents/`,
  `repositories/` contain only `__init__.py`.

### Commands to run

```bash
uv sync
docker compose -f docker/docker-compose.yml up -d postgres
uv run alembic upgrade head
uv run uvicorn dineops.api.main:app --reload
curl -s localhost:8000/health/live && curl -s localhost:8000/health/ready
uv run pytest tests/unit tests/integration -k "settings or health or layer_boundaries"
```

### Definition of Done

- [ ] `docker compose up -d postgres` + `alembic upgrade head` succeed from a clean clone.
- [ ] App boots locally and in the `api` Docker container.
- [ ] Both health endpoints behave per acceptance criteria.
- [ ] Structured logging with correlation IDs confirmed via the integration test.
- [ ] `test_layer_boundaries.py` passes and is wired into the tests that later phases
      must keep passing.
- [ ] Committed: `git commit` with all files above.

---

## Phase 2: Database Models, Migrations, and Realistic Restaurant Seed Data

**Depends on**: Phase 1 (db harness, Alembic wiring).

**Advances**: all 18 entities in [data-model.md](./data-model.md); no FRs directly
(schema is a prerequisite), but unblocks every subsequent phase.

### Files to create

| Path | Purpose |
|---|---|
| `src/dineops/models/restaurant.py`, `user.py`, `customer.py`, `table.py`, `reservation.py`, `menu_item.py`, `inventory.py`, `staff.py`, `staffing.py`, `sale.py`, `purchase_request.py`, `approval.py`, `event.py`, `agent_run.py`, `agent_message.py`, `memory.py` | One SQLAlchemy 2.x declarative model module per entity (or entity pair — `inventory.py` holds `InventoryItem`+`InventoryTransaction`, `staffing.py` holds `StaffShift`+`ShiftAssignment`), matching data-model.md field-for-field |
| `src/dineops/models/__init__.py` (update) | Import every model so `Base.metadata` is complete before `alembic revision --autogenerate` |
| `alembic/versions/0001_initial_schema.py` | Autogenerated, then hand-reviewed migration |
| `src/dineops/db/seed.py` | Idempotent seed script (truncates + reseeds, or upserts by natural key) |
| `tests/unit/test_models.py` | Constraint tests: CHECK constraints reject bad data (spot-check the trickiest ones) |
| `tests/integration/test_migrations.py` | `upgrade head` → `downgrade base` → `upgrade head` round-trips cleanly |
| `tests/integration/test_seed.py` | Seed script produces expected, referentially-consistent row counts |

### Implementation tasks

1. **P2.1** [P] Model `Restaurant`, `User` (table name `users`) — the two tables
   everything else's `restaurant_id`/`decided_by_user_id` FKs will reference.
2. **P2.2** [P] Model `Customer`, `Table` (table name `restaurant_tables`).
3. **P2.3** Model `Reservation` (depends on Customer/Table existing as importable
   modules for the FK relationship declarations, not on DB creation order).
4. **P2.4** [P] Model `MenuItem`.
5. **P2.5** [P] Model `InventoryItem` + `InventoryTransaction` in `inventory.py`.
6. **P2.6** [P] Model `Staff`; `StaffShift` + `ShiftAssignment` in `staffing.py`.
7. **P2.7** Model `Sale` (`total_price` as `sqlalchemy.Computed("quantity * unit_price")`
   — flag for manual verification in the Alembic migration, computed-column autogenerate
   support is inconsistent across SQLAlchemy/Alembic versions).
8. **P2.8** [P] Model `PurchaseRequest`.
9. **P2.9** Model `Approval` (referenced by nullable FKs on Reservation/
   InventoryTransaction/ShiftAssignment/PurchaseRequest — those FK columns were declared
   in P2.3/P2.5/P2.6/P2.8 as forward references, resolved here).
10. **P2.10** [P] Model `Event`.
11. **P2.11** Model `AgentRun` (self-referential `parent_run_id`), then `AgentMessage`.
12. **P2.12** Model `Memory` — including the generated `scope_key` column
    (`Computed(...)`) and the partial unique index
    (`Index(..., postgresql_where=..., unique=True)`) plus the multi-branch scope
    consistency `CheckConstraint` from data-model.md. **Flag for manual review**: both
    the generated column and the partial index are the two riskiest things for
    `--autogenerate` to get wrong — verify by hand against data-model.md §17 before
    committing the migration.
13. **P2.13** Run `alembic revision --autogenerate -m "initial schema"`; hand-review the
    diff against every table in data-model.md (column types, CHECK bodies, FKs, indexes);
    fix anything autogenerate missed (expected: computed columns, partial indexes,
    multi-column CHECK constraints).
14. **P2.14** Write `db/seed.py`: 1 `Restaurant`, 1 `User` (manager), 8 `Table`s (mixed
    capacities 2–8), 15+ `MenuItem`s across 4+ categories, 10+ `InventoryItem`s
    (**seed at least one already at/below its `low_stock_threshold`** — makes Phase 5/10
    testing trivial without extra setup), 5 `Staff` across roles, a published shift
    schedule for the next 7 days (**seed at least one shift intentionally understaffed**
    — same reasoning), 12+ `Customer`s, a mix of past (`completed`/`no_show`) and
    upcoming (`booked`) `Reservation`s, matching `Sale` rows for completed reservations.
    No `Approval`/`Event`/`AgentRun` rows — those are runtime-generated, not seed data.
15. **P2.15** [P] Write `tests/unit/test_models.py`, `tests/integration/test_migrations.py`,
    `tests/integration/test_seed.py`.

### Dependencies (internal ordering)

P2.1 → P2.2/P2.4/P2.5/P2.6/P2.8/P2.10 (all reference `restaurant_id`) → P2.3 (needs
Customer+Table), P2.9 (needs the four domain tables' FK columns declared), P2.11/P2.12 →
P2.13 (needs every model imported) → P2.14 (needs a migrated schema to insert into).

### Tests

- `test_models.py`: inserting an `InventoryItem` with negative `quantity_on_hand` raises
  `IntegrityError`; inserting a `Memory` row with `scope_type='customer'` but
  `customer_id IS NULL` raises `IntegrityError`; inserting two active `Memory` rows for
  the same `(restaurant, memory_type, scope, topic)` raises a unique-violation.
- `test_migrations.py`: full up/down/up round trip against a throwaway database.
- `test_seed.py`: row counts match the minimums above; every FK resolves (no orphans);
  the seeded low-stock item's `status='low'` or `'out_of_stock'`; the seeded understaffed
  shift's `status='understaffed'`.

### Acceptance criteria

- `alembic upgrade head` on a clean database creates all 18 entity tables + the
  `shift_assignments` join table, matching data-model.md exactly (verified by
  `test_models.py` + manual `\d` spot-check during P2.13).
- Every CHECK constraint from data-model.md exists and is enforced (unit-tested for
  Memory's scope-consistency and Reservation/Approval status constraints as the
  representative hard cases).
- `python -m dineops.db.seed` run twice does not error and does not duplicate data
  (idempotent by design, not by accident).
- Seed data is realistic enough that Phases 4–10's manual/exploratory testing needs no
  additional fixtures for the common cases (available tables, low-stock item,
  understaffed shift, known repeat customer).

### Commands to run

```bash
uv run alembic revision --autogenerate -m "initial schema"
# hand-review the generated file against data-model.md before continuing
uv run alembic upgrade head
uv run python -m dineops.db.seed
uv run pytest tests/unit/test_models.py tests/integration/test_migrations.py tests/integration/test_seed.py
uv run alembic downgrade base && uv run alembic upgrade head   # reversibility check
```

### Definition of Done

- [ ] All 18 entities + `shift_assignments` exist post-migration, field-for-field
      matching data-model.md.
- [ ] Migration is reversible (`downgrade base` → `upgrade head` succeeds).
- [ ] Seed script produces realistic, referentially-consistent data including the two
      "pre-triggered" fixtures (low stock item, understaffed shift) later phases rely on.
- [ ] `test_layer_boundaries.py` (Phase 1) still passes — models exist now, but nothing
      under `agents/`/`tools/` imports them yet.
- [ ] Committed.

---

## Phase 3: Domain Services and Typed Tools

**Depends on**: Phase 2 (models + seeded DB to test repositories against).

**Advances**: FR-005–FR-025 (all five domains' core capability + typed tool contract),
FR-026 (high-impact classification), the **core** (non-API) half of FR-027–FR-029 (see
cross-phase note above — full manager-facing workflow is Phase 9); SC-007 groundwork
(100% tool test coverage).

### Files to create

| Path | Purpose |
|---|---|
| `src/dineops/repositories/base.py` | Shared repository helpers (session-scoped query helpers) |
| `src/dineops/repositories/reservation_repo.py`, `inventory_repo.py`, `customer_repo.py`, `staffing_repo.py`, `analytics_repo.py`, `approval_repo.py` | One repository per domain, plus `approval_repo.py` (needed now — see below) |
| `src/dineops/services/reservation_service.py`, `inventory_service.py`, `customer_service.py`, `staffing_service.py`, `analytics_service.py` | Domain business rules + high-impact classification |
| `src/dineops/services/approval_service.py` | **Core only**: `propose()`, `decide()`, `get_pending()` — no API, no notifications (EventBus doesn't exist until Phase 10) |
| `src/dineops/tools/base.py` | `Tool` protocol, `ToolContext`, `PendingApprovalOutput` per [contracts/tools.md](./contracts/tools.md) |
| `src/dineops/tools/registry.py` | Agent → tool-subset binding mechanism (bindings populated incrementally in Phases 4–8) |
| `src/dineops/tools/reservation_tools.py`, `inventory_tools.py`, `customer_tools.py`, `staffing_tools.py`, `analytics_tools.py` | All five domains' tools, built now even though their agents come later |
| `tests/unit/test_{reservation,inventory,customer,staffing,analytics}_service.py` | Business rules against a fake repository (no DB) |
| `tests/contract/test_{reservation,inventory,customer,staffing,analytics}_tools.py` | Input/output schema conformance; `high_impact` tools return `PendingApprovalOutput` |
| `tests/integration/test_{reservation,inventory,customer,staffing,analytics}_repo.py` | Real Postgres (seeded), repository CRUD correctness |
| `tests/unit/test_approval_service.py` | propose→pending, decide(approve)→re-invokes captured intent, decide(reject)→terminal, no-op |

**Why `approval_service.py`/`approval_repo.py` now, not Phase 9**: `ReservationService`,
`InventoryService`, and `StaffingService` structurally depend on ApprovalService to
satisfy Constitution IV — a tool can't correctly implement "high-impact → don't mutate"
without something to call. Phase 9 adds the *manager-facing* half (API routes, expiry
sweep, notification integration) on top of this working core.

### Implementation tasks

1. **P3.1** Write `tools/base.py` (`Tool` Protocol, `ToolContext`, `PendingApprovalOutput`).
2. **P3.2** [P] Write `repositories/base.py`.
3. **P3.3** Write `services/approval_service.py` + `repositories/approval_repo.py`
   (`propose()` persists a `pending` `Approval` row with the captured intent as
   `proposed_action` JSON; `decide()` re-invokes that captured intent on approval,
   updates status on either outcome).
4. **P3.4** [P] Reservation: `repositories/reservation_repo.py` →
   `services/reservation_service.py` (party-size high-impact threshold from
   [spec Assumptions](./spec.md#assumptions), calls `ApprovalService.propose()` when
   met) → `tools/reservation_tools.py` (`find_availability`, `create_reservation`,
   `modify_reservation`, `cancel_reservation`).
5. **P3.5** [P] Inventory: `repositories/inventory_repo.py` →
   `services/inventory_service.py` (maintains denormalized `InventoryItem.status`;
   write-off-value high-impact threshold) → `tools/inventory_tools.py`
   (`get_stock_level`, `record_stock_adjustment`, `list_low_stock_items`).
6. **P3.6** [P] Customer: `repositories/customer_repo.py` →
   `services/customer_service.py` → `tools/customer_tools.py` (`get_customer_profile`,
   `find_customer`). No preference/notes methods — that's `Memory`, Phase 6.
7. **P3.7** [P] Staffing: `repositories/staffing_repo.py` →
   `services/staffing_service.py` (maintains denormalized `StaffShift.status`;
   `is_published` as the high-impact discriminator) → `tools/staffing_tools.py`
   (`get_shift_schedule`, `assign_staff_to_shift`, `list_understaffed_shifts`).
8. **P3.8** [P] Analytics: `repositories/analytics_repo.py` (read-only aggregate queries
   across Reservation/Sale/MenuItem/StaffShift) → `services/analytics_service.py` →
   `tools/analytics_tools.py` (`get_performance_metric`).
9. **P3.9** [P] Write all `tests/unit/test_*_service.py` (fake repos).
10. **P3.10** [P] Write all `tests/contract/test_*_tools.py`.
11. **P3.11** [P] Write all `tests/integration/test_*_repo.py` (against Phase 2's seeded
    DB).
12. **P3.12** Write `tests/unit/test_approval_service.py`.
13. **P3.13** Re-run `test_layer_boundaries.py` — now meaningful for the first time,
    since `tools/` contains real code that must not import `sqlalchemy`/`models`/
    `repositories`.

### Dependencies (internal ordering)

P3.1 before any tool file. P3.3 before P3.4/P3.5/P3.7 (they call `ApprovalService`).
P3.4–P3.8 are independent of each other (`[P]`) and can be built by five people/agents in
parallel once P3.1/P3.3 exist.

### Tests

- Service unit tests: e.g. cancelling a 4-top executes immediately; cancelling an 8-top
  (≥ threshold) calls `ApprovalService.propose()` and does not mutate the reservation.
- Contract tests: every tool's declared `input_model`/`output_model` validates
  representative payloads; every `high_impact=True` tool path returns
  `PendingApprovalOutput`, never a direct mutation result, when the service classifies
  the call as high-impact.
- Repository integration tests: CRUD + the domain-specific query patterns from
  data-model.md's index list (availability search, low-stock listing, etc.) against real
  Postgres.
- `test_approval_service.py`: `decide(approve)` on a captured `create_reservation`-style
  intent actually creates/mutates the target row; `decide(reject)` leaves it untouched;
  deciding an already-decided `Approval` raises (FR-028 terminal-decision guarantee, unit
  level — the API-level 409 comes in Phase 9).

### Acceptance criteria

- 100% of tools defined in [contracts/tools.md](./contracts/tools.md) (minus
  `memory_tools`/orchestrator's `delegate`, deferred) have a passing contract test —
  progress toward SC-007.
- Every domain service correctly classifies routine vs. high-impact per spec Assumptions'
  thresholds and routes accordingly (FR-026, FR-027 core mechanism).
- `test_layer_boundaries.py` passes with real `tools/`/`agents/`-adjacent code present.
- No agent/LangGraph code exists yet — this phase is service/tool layer only, callable
  directly from tests, not yet from a conversation.

### Commands to run

```bash
uv run pytest tests/unit/test_reservation_service.py tests/unit/test_inventory_service.py \
  tests/unit/test_customer_service.py tests/unit/test_staffing_service.py \
  tests/unit/test_analytics_service.py tests/unit/test_approval_service.py
uv run pytest tests/contract/
uv run pytest tests/integration/test_reservation_repo.py tests/integration/test_inventory_repo.py \
  tests/integration/test_customer_repo.py tests/integration/test_staffing_repo.py \
  tests/integration/test_analytics_repo.py
uv run pytest tests/unit/test_layer_boundaries.py
```

### Definition of Done

- [ ] All five domains' repositories, services, and tools implemented per
      contracts/tools.md.
- [ ] `ApprovalService` core (propose/decide/get_pending) implemented and unit-tested.
- [ ] Every tool has a passing contract test; every service has a passing unit test;
      every repository has a passing integration test.
- [ ] `test_layer_boundaries.py` still passes.
- [ ] Committed.

---

## Phase 4: Implement and Test the Reservation Agent

**Depends on**: Phase 3 (`tools/reservation_tools.py`, `services/reservation_service.py`).

**Advances**: FR-001–FR-002 (partially — single-agent invocation, not yet Orchestrator
routing), spec **User Story 1**, scenarios **1, 3, 4, 5** (booking, high-impact
cancellation held for approval, approve/reject outcomes, no-availability messaging).
**Not yet**: User Story 1 scenario 2 (memory preference surfacing) — deferred to Phase 6.

### Files to create/change

| Path | Purpose |
|---|---|
| `src/dineops/agents/reservation/graph.py` | LangGraph graph wrapping `reservation_tools` via the registry |
| `src/dineops/tools/registry.py` (modify) | Bind `reservation_tools.*` to `agent_name="reservation"` |
| `tests/agent/test_reservation_agent.py` | Graph tool-selection correctness with a scripted/fake LLM (no live LLM call) |
| `tests/integration/test_reservation_agent_e2e.py` | Real DB + real tools + graph (fake LLM), full flow per US1 scenarios 1/3/4/5 |

### Implementation tasks

1. **P4.1** Write `agents/reservation/graph.py`: a small LangGraph graph whose only
   callable surface is the tools registered to `"reservation"`.
2. **P4.2** Register the reservation tool subset in `tools/registry.py`.
3. **P4.3** [P] Write `tests/agent/test_reservation_agent.py` with a deterministic fake
   LLM response sequence, asserting correct tool selection for: book / modify / cancel /
   query-availability requests.
4. **P4.4** Write `tests/integration/test_reservation_agent_e2e.py` against Phase 2's
   seeded DB:
   - book an open table/time → `create_reservation` succeeds.
   - cancel a large-party reservation → `PendingApprovalOutput`, row unchanged.
   - `ApprovalService.decide(approve)` on that proposal → row transitions to
     `cancelled`.
   - request a conflicting time/party size → clear conflict messaging, no exception.
5. **P4.5** Run the full test suite from Phases 1–4 together to confirm no regressions.

### Dependencies

P4.1 needs P3.4's tools; P4.2 needs P4.1; P4.4 needs both.

### Tests

- `test_reservation_agent.py` (fast, no DB, no live LLM): delegation-shape correctness.
- `test_reservation_agent_e2e.py` (real DB, fake LLM): the four in-scope US1 scenarios.

### Acceptance criteria

- All of spec US1's **Independent Test** passes except the memory-preference part:
  "sending a handful of reservation requests (routine booking, modification, and a
  large-party cancellation) ... verifying correct delegation, correct tool use, ... and
  that the large-party cancellation is held for approval."
- FR-005, FR-006, FR-007 satisfied and tested.
- The Reservation Agent is invocable directly (not yet via `/chat` — that's Phase 8) for
  manual/exploratory testing.

### Commands to run

```bash
uv run pytest tests/agent/test_reservation_agent.py
uv run pytest tests/integration/test_reservation_agent_e2e.py
uv run pytest tests/unit tests/contract tests/agent -x   # full regression check
```

### Definition of Done

- [ ] Reservation Agent graph implemented, registered, tested at both the agent-shape and
      end-to-end levels.
- [ ] US1 scenarios 1, 3, 4, 5 demonstrably pass; scenario 2 explicitly documented as
      pending Phase 6.
- [ ] No regressions in Phases 1–3's test suites.
- [ ] Committed.

---

## Phase 5: Implement and Test the Inventory Agent

**Depends on**: Phase 3 (`tools/inventory_tools.py`, `services/inventory_service.py`).

**Advances**: spec **User Story 4**, scenarios 1–3; FR-008–FR-010.
**Not yet**: proactive low-stock alerts (User Story 3) — needs EventBus, deferred to
Phase 10. This phase is on-demand query/adjustment via conversation only.

### Files to create/change

| Path | Purpose |
|---|---|
| `src/dineops/agents/inventory/graph.py` | LangGraph graph for the Inventory domain |
| `src/dineops/tools/registry.py` (modify) | Bind `inventory_tools.*` to `agent_name="inventory"` |
| `tests/agent/test_inventory_agent.py` | Tool-selection correctness (fake LLM) |
| `tests/integration/test_inventory_agent_e2e.py` | Real DB, US4 scenarios 1–3 |

### Implementation tasks

1. **P5.1** Write `agents/inventory/graph.py`.
2. **P5.2** Register the inventory tool subset in `tools/registry.py`.
3. **P5.3** [P] Write `tests/agent/test_inventory_agent.py`.
4. **P5.4** Write `tests/integration/test_inventory_agent_e2e.py`:
   - query stock level for the seeded low-stock item → correct quantity + `status='low'`.
   - record a routine delivery → immediate stock update, no approval.
   - record a large write-off (≥ threshold) → `PendingApprovalOutput`, stock unchanged
     until `ApprovalService.decide(approve)`.
5. **P5.5** Full regression run (Phases 1–5).

### Dependencies

Same pattern as Phase 4, against Phase 3's inventory tools/service.

### Tests

Same two-tier structure as Phase 4 (agent-shape + e2e), scoped to Inventory.

### Acceptance criteria

- FR-008–FR-010 satisfied and tested.
- US4's Independent Test passes: "asking about a specific item's stock level, recording
  an adjustment, and confirming the new level and the flag state ... update accordingly,
  with a large write-off routed through approval."
- Explicitly **not** claimed: proactive notification when stock crosses the threshold
  (US3) — that requires Phase 10.

### Commands to run

```bash
uv run pytest tests/agent/test_inventory_agent.py
uv run pytest tests/integration/test_inventory_agent_e2e.py
uv run pytest tests/unit tests/contract tests/agent -x
```

### Definition of Done

- [ ] Inventory Agent implemented, registered, tested.
- [ ] US4 scenarios 1–3 pass.
- [ ] No regressions.
- [ ] Committed.

---

## Phase 6: Implement and Test the Customer Agent and Persistent MemoryService

**Depends on**: Phase 3 (`tools/customer_tools.py`), Phase 4 (Reservation Agent, which
this phase retrofits with memory).

**Advances**: spec **User Story 2** (all 3 scenarios); **closes User Story 1 scenario
2** (deferred from Phase 4); FR-011–FR-013, FR-019–FR-022; Constitution III end-to-end.

### Files to create/change

| Path | Purpose |
|---|---|
| `src/dineops/repositories/memory_repo.py` | Repository for `Memory`, including the scope-key upsert query and the recall-with-access-tracking query |
| `src/dineops/services/memory_service.py` | `remember()`, `recall()`, `forget()` per [plan.md](./plan.md#memoryservice) |
| `src/dineops/tools/memory_tools.py` | `remember_fact`, `recall_facts`, `forget_fact` — the only agent-facing path to Memory |
| `src/dineops/agents/customer/graph.py` | LangGraph graph for the Customer domain |
| `src/dineops/tools/registry.py` (modify) | Bind `customer_tools.*` + `memory_tools.*` to `"customer"`; **also** bind `memory_tools.recall_facts` to `"reservation"` (the retrofit) |
| `src/dineops/agents/reservation/graph.py` (modify) | Call `recall_facts` (scope=customer) when a booking names a known customer, and surface the result in the response |
| `tests/unit/test_memory_service.py` | Upsert-on-scope+topic semantics; `recall()` bumps `access_count`/`last_accessed_at`, `remember()` does not; `forget()` soft-deletes |
| `tests/integration/test_memory_repo.py` | The partial unique index behaves correctly against real Postgres (the exact NULL-collision scenario from data-model.md) |
| `tests/contract/test_memory_tools.py` | Tool I/O schema conformance |
| `tests/agent/test_customer_agent.py` | Tool-selection correctness |
| `tests/integration/test_customer_agent_e2e.py` | US2 scenarios 1–3 |
| `tests/integration/test_reservation_memory_cross_agent.py` | **The key proof**: Customer Agent writes a preference → Reservation Agent's booking flow surfaces it, without it being restated (closes US1 scenario 2) |

### Implementation tasks

1. **P6.1** Write `repositories/memory_repo.py`: an upsert method keyed on
   `(restaurant_id, memory_type, scope_type, scope_key, topic) WHERE is_active`, and a
   recall method that filters by scope/type/topic and bumps `access_count`/
   `last_accessed_at` in the same transaction.
2. **P6.2** Write `services/memory_service.py` wrapping the repository with the typed
   `remember`/`recall`/`forget` interface from plan.md.
3. **P6.3** Write `tools/memory_tools.py`.
4. **P6.4** Write `agents/customer/graph.py`; bind `customer_tools` + `memory_tools` to
   `"customer"` in `tools/registry.py`.
5. **P6.5** **Retrofit**: bind `memory_tools.recall_facts` to `"reservation"`; modify
   `agents/reservation/graph.py` so a booking request for a named/known customer calls
   `recall_facts(scope=customer:<id>, memory_type="customer_preference")` and includes
   any result in its response — this is what closes US1 scenario 2.
6. **P6.6** [P] Write `tests/unit/test_memory_service.py`,
   `tests/integration/test_memory_repo.py`, `tests/contract/test_memory_tools.py`.
7. **P6.7** [P] Write `tests/agent/test_customer_agent.py`,
   `tests/integration/test_customer_agent_e2e.py`.
8. **P6.8** Write `tests/integration/test_reservation_memory_cross_agent.py`.
9. **P6.9** Full regression run (Phases 1–6), specifically re-running Phase 4's
   reservation e2e tests to confirm the retrofit didn't break existing scenarios.

### Dependencies

P6.1 → P6.2 → P6.3 → P6.4/P6.5 (P6.5 depends on P6.3 existing and on Phase 4's
`agents/reservation/graph.py`). P6.8 depends on both P6.4 and P6.5 being done.

### Tests

- `test_memory_service.py`: writing the same `(scope, topic)` twice updates in place, not
  duplicates; a `recall()` call increments `access_count` and sets `last_accessed_at`;
  writing does neither.
- `test_memory_repo.py`: attempting to insert a second **active** row for an identical
  scope+topic raises a unique-violation; after `forget()` (soft delete), a new row for
  that same scope+topic succeeds (the partial index's `WHERE is_active` doing its job).
- `test_reservation_memory_cross_agent.py`: end-to-end proof of Constitution III — memory
  written by one agent is readable by another *only* through `memory_tools`, never a
  direct cross-service call.

### Acceptance criteria

- All of US2's Independent Test passes.
- US1 scenario 2 now passes (previously explicitly deferred).
- FR-011–FR-013, FR-019–FR-022 satisfied and tested.
- SC-002 ("preference surfaced in ≥95% of interactions") — the **mechanism** is proven
  correct here via integration test; the statistical 95% claim itself is a live-LLM
  evaluation concern outside a single phase's CI-blocking Definition of Done, noted here
  rather than silently claimed.

### Commands to run

```bash
uv run pytest tests/unit/test_memory_service.py tests/integration/test_memory_repo.py \
  tests/contract/test_memory_tools.py
uv run pytest tests/agent/test_customer_agent.py tests/integration/test_customer_agent_e2e.py
uv run pytest tests/integration/test_reservation_memory_cross_agent.py
uv run pytest tests/integration/test_reservation_agent_e2e.py   # Phase 4 regression check
uv run pytest tests/unit tests/contract tests/agent -x
```

### Definition of Done

- [ ] MemoryService fully implemented and independently tested.
- [ ] Customer Agent implemented and tested (US2 complete).
- [ ] Reservation Agent retrofitted; US1 scenario 2 now passes.
- [ ] Cross-agent memory sharing proven by a dedicated integration test.
- [ ] No regressions in Phases 1–5.
- [ ] Committed.

---

## Phase 7: Implement and Test the Staffing and Analytics Agents

**Depends on**: Phase 3 (`tools/staffing_tools.py`, `tools/analytics_tools.py`).

**Advances**: spec **User Story 5** (all 3 scenarios), **User Story 6** (both scenarios);
FR-014–FR-018. **Not yet**: proactive understaffed-shift alerts (US3, second half) —
Phase 10.

### Files to create/change

| Path | Purpose |
|---|---|
| `src/dineops/agents/staffing/graph.py`, `agents/analytics/graph.py` | LangGraph graphs for the two domains |
| `src/dineops/tools/registry.py` (modify) | Bind `staffing_tools.*` to `"staffing"`, `analytics_tools.*` to `"analytics"` |
| `tests/agent/test_staffing_agent.py`, `test_analytics_agent.py` | Tool-selection correctness |
| `tests/integration/test_staffing_agent_e2e.py`, `test_analytics_agent_e2e.py` | US5/US6 scenarios against seeded data |

### Implementation tasks

1. **P7.1** [P] Write `agents/staffing/graph.py`; register in `tools/registry.py`.
2. **P7.2** [P] Write `agents/analytics/graph.py`; register in `tools/registry.py`.
3. **P7.3** [P] Write `tests/agent/test_staffing_agent.py`,
   `tests/agent/test_analytics_agent.py`.
4. **P7.4** Write `tests/integration/test_staffing_agent_e2e.py`:
   - request the schedule for a date range → correct assignments returned.
   - assign staff to an unpublished shift → immediate.
   - change an assignment on the seeded **published** shift → `PendingApprovalOutput`;
     approve → change applied.
5. **P7.5** Write `tests/integration/test_analytics_agent_e2e.py`:
   - ask for covers/revenue over the seeded reservation/sale data → correct figures.
   - ask for a metric the seed data can't answer (e.g., a future period with no data) →
     explicit "can't answer," not a fabricated number.
6. **P7.6** Full regression run (Phases 1–7).

### Dependencies

Each agent's tasks are independent of the other's (`[P]` at the phase level); both
depend only on their respective Phase 3 tools.

### Tests

Two-tier (agent-shape + e2e) per domain, as in prior phases.

### Acceptance criteria

- US5 and US6 Independent Tests both pass.
- FR-014–FR-018 satisfied and tested.
- Explicitly **not** claimed: proactive understaffed-shift notification (US3) — Phase 10.

### Commands to run

```bash
uv run pytest tests/agent/test_staffing_agent.py tests/agent/test_analytics_agent.py
uv run pytest tests/integration/test_staffing_agent_e2e.py tests/integration/test_analytics_agent_e2e.py
uv run pytest tests/unit tests/contract tests/agent -x
```

### Definition of Done

- [ ] Staffing Agent and Analytics Agent implemented, registered, tested.
- [ ] US5, US6 complete.
- [ ] No regressions.
- [ ] Committed.

---

## Phase 8: Implement the Orchestrator Agent Using LangGraph

**Depends on**: Phases 4–7 (all five specialist agents must exist to delegate to).

**Advances**: FR-001–FR-004 fully; makes User Stories 1, 2, 4, 5, 6 reachable through a
**single conversational entry point** for the first time (previously each agent was
invoked directly in tests); FR-038 (delegation testable independent of specialist
internals).

### Files to create/change

| Path | Purpose |
|---|---|
| `src/dineops/agents/orchestrator/graph.py` | Top-level graph: the `delegate` tool routes to 1+ specialist graphs and merges typed results |
| `src/dineops/tools/registry.py` (modify) | Orchestrator's own tool set = `delegate` only (no domain tools) |
| `src/dineops/api/routes/chat.py` | `POST /chat` per [contracts/api.md](./contracts/api.md) |
| `src/dineops/api/main.py` (modify) | Register the `chat` router |
| `tests/agent/test_orchestrator_delegation.py` | Routing correctness using **fake specialist agents** (no DB, no live LLM) — proves FR-038 |
| `tests/contract/test_chat_api.py` | Request/response shape per contracts/api.md |
| `tests/integration/test_chat_e2e.py` | Real Postgres, real agents, deterministic/stubbed LLM routing — full multi-domain conversations through `/chat` |

### Implementation tasks

1. **P8.1** Write `agents/orchestrator/graph.py`: a `delegate` step that classifies a
   message's target domain(s), invokes the corresponding specialist graph(s), and merges
   their typed outputs into one response; returns a clear "doesn't match any domain"
   response when confidence is low (FR-004) instead of guessing.
2. **P8.2** Set the Orchestrator's tool binding in `tools/registry.py` to `delegate` only.
3. **P8.3** Write `api/routes/chat.py`; register in `api/main.py`.
4. **P8.4** Write `tests/agent/test_orchestrator_delegation.py` using fake specialist
   agents standing in for all five, covering: single-domain routing (5 cases, one per
   domain), a multi-domain request combining two agents' results (FR-003), an
   out-of-domain request (FR-004).
5. **P8.5** [P] Write `tests/contract/test_chat_api.py`.
6. **P8.6** Write `tests/integration/test_chat_e2e.py`: re-run representative scenarios
   from US1, US2, US4, US5, US6 — but now via `POST /chat` end-to-end instead of calling
   each agent's graph directly, confirming the Orchestrator's routing doesn't change any
   specialist's behavior.
7. **P8.7** Full regression run (Phases 1–8).

### Dependencies

P8.1 depends on all of Phases 4–7's specialist graphs existing. P8.3 depends on P8.1.
P8.4 can be written in parallel with P8.1 against fakes, then run once P8.1 lands.

### Tests

- `test_orchestrator_delegation.py`: the FR-038 test — correct routing verified without
  needing any specialist agent's internals to be correct, using fakes.
- `test_chat_api.py`: request/response schema conformance, including the `results[]`
  array shape with `pending_approval` outcomes.
- `test_chat_e2e.py`: full-stack conversations across all five domains through one
  endpoint.

### Acceptance criteria

- FR-001–FR-004 satisfied and tested.
- `POST /chat` is now the single entry point demonstrated in quickstart.md steps 3–4 (the
  routine and high-impact reservation paths) — re-run those steps against the running
  API, not just internal tests.
- Tool round-trip latency budget from plan.md (**p95 < 300ms** excluding LLM inference)
  verified via timing assertions in `test_chat_e2e.py`.
- SC-006 (Orchestrator delegates correctly ≥95% of the time) — `test_orchestrator_
  delegation.py` proves correctness on the fixed test set; the statistical claim over a
  broader representative sample is a live-LLM eval concern, same caveat as SC-002.

### Commands to run

```bash
uv run pytest tests/agent/test_orchestrator_delegation.py
uv run pytest tests/contract/test_chat_api.py
uv run pytest tests/integration/test_chat_e2e.py
uv run uvicorn dineops.api.main:app --reload
curl -s -X POST localhost:8000/chat -H 'content-type: application/json' \
  -d '{"message": "book a table for 4 tonight at 7pm"}'
uv run pytest tests/unit tests/contract tests/agent -x
```

### Definition of Done

- [ ] Orchestrator Agent implemented; delegates to all five specialists correctly.
- [ ] `POST /chat` live and matching contracts/api.md.
- [ ] quickstart.md steps 1–4 pass against the running system end-to-end for the first
      time.
- [ ] No regressions.
- [ ] Committed.

---

## Phase 9: Implement Human Approval Workflows

**Depends on**: Phase 3 (ApprovalService core), Phase 8 (`/chat` producing real
`pending_approval` outcomes to approve/reject against).

**Advances**: closes FR-026–FR-029 (manager-facing half), FR-035; contracts/api.md's
`/approvals` routes.

### Files to create/change

| Path | Purpose |
|---|---|
| `src/dineops/api/routes/approvals.py` | `GET /approvals`, `POST /approvals/{id}/decision` |
| `src/dineops/api/main.py` (modify) | Register the `approvals` router |
| `src/dineops/api/deps.py` (modify) | `get_current_user()` — resolves to the seeded manager for MVP; **explicitly not real authentication** (documented limitation, consistent with spec Assumptions' single-manager-session scope) |
| `src/dineops/services/approval_service.py` (modify) | Add `expire_overdue()` — flips `pending` past `expires_at` to `expired`; invoked by a scheduled job in Phase 10, callable directly/manually here |
| `tests/contract/test_approvals_api.py` | Request/response shape |
| `tests/integration/test_approval_workflow_e2e.py` | Full propose → list → decide → verify lifecycle through the API |

### Implementation tasks

1. **P9.1** Write `api/deps.py`'s `get_current_user()` stub, clearly commented as an MVP
   simplification (no session/auth system built — see spec Assumptions).
2. **P9.2** Write `api/routes/approvals.py`: `GET /approvals` (filterable by status/domain,
   paginated), `POST /approvals/{id}/decision` (calls `ApprovalService.decide()`, returns
   409 if the approval is already decided — FR-028).
3. **P9.3** Add `ApprovalService.expire_overdue()`.
4. **P9.4** Register the router in `api/main.py`.
5. **P9.5** [P] Write `tests/contract/test_approvals_api.py`.
6. **P9.6** Write `tests/integration/test_approval_workflow_e2e.py`: propose (via
   `POST /chat` on a high-impact request) → `GET /approvals?status=pending` includes it →
   `POST /approvals/{id}/decision` approve → underlying row mutated, `GET /approvals`
   shows `approved`/`decided_by`/`decided_at` → a second decision attempt on the same id
   → `409`. Repeat the rejection path (row stays unchanged).
7. **P9.7** Full regression run.

### Dependencies

P9.2 depends on P9.1 and Phase 3's `ApprovalService`. P9.6 depends on Phase 8's `/chat`
existing to generate a real `pending_approval` to act on.

### Tests

- `test_approvals_api.py`: schema conformance for both routes, including the error shape
  for the conflict case.
- `test_approval_workflow_e2e.py`: the full lifecycle exactly as quickstart.md step 4
  describes it, now exercised through real HTTP calls rather than direct service calls
  (as Phase 4 tested it).

### Acceptance criteria

- FR-026–FR-029, FR-035 fully satisfied (mechanism from Phase 3 + manager-facing surface
  now complete).
- quickstart.md step 4 passes verbatim against the running API.
- SC-003 ("100% of high-impact actions held for approval") verifiable end-to-end via the
  API for all domains that have high-impact paths (Reservation, Inventory, Staffing).

### Commands to run

```bash
uv run pytest tests/contract/test_approvals_api.py
uv run pytest tests/integration/test_approval_workflow_e2e.py
uv run pytest tests/unit tests/contract tests/agent -x
```

### Definition of Done

- [ ] `/approvals` routes live, matching contracts/api.md.
- [ ] Full propose→approve/reject lifecycle verified end-to-end via HTTP.
- [ ] Double-decision conflict handling verified.
- [ ] No regressions.
- [ ] Committed.

---

## Phase 10: Implement Event-Driven Workflows and Background Jobs

**Depends on**: Phase 5 (Inventory), Phase 7 (Staffing), Phase 9 (`expire_overdue()` to
schedule).

**Advances**: closes FR-030–FR-032; **closes User Story 3** (both scenarios, deferred
from Phases 5/7); contracts/events.md fully implemented.

### Files to create/change

| Path | Purpose |
|---|---|
| `src/dineops/services/event_bus.py` | In-process pub/sub, `subscribe()`/`publish()` |
| `src/dineops/repositories/event_repo.py` | Persist every published `Event` (durability) |
| `src/dineops/events/types.py` | Pydantic event schemas per [contracts/events.md](./contracts/events.md) |
| `src/dineops/events/handlers.py` | `StockLevelChanged → StockLowDetected`, shift-assignment-change → `ShiftUnderstaffedDetected`, `ApprovalRequested`/`ApprovalDecided` passthrough |
| `src/dineops/tools/inventory_tools.py` (modify) | Add `notify_low_stock` tool, invoked by the `StockLowDetected` handler |
| `src/dineops/tools/staffing_tools.py` (modify) | Add `notify_understaffed_shift` tool, invoked by the `ShiftUnderstaffedDetected` handler |
| `src/dineops/services/inventory_service.py` (modify) | Publish `StockLevelChanged` after every applied `InventoryTransaction` |
| `src/dineops/services/staffing_service.py` (modify) | Publish an assignment-changed event after every `ShiftAssignment` create/delete |
| `src/dineops/background/__init__.py`, `jobs.py` | Periodic `asyncio` task (started in `api/main.py`'s lifespan) running `ApprovalService.expire_overdue()` on an interval |
| `src/dineops/api/main.py` (modify) | Start/stop the background task in the app lifespan |
| `tests/unit/test_event_bus.py` | Publish/subscribe mechanics |
| `tests/integration/test_event_driven_workflows.py` | Stock-threshold and understaffed-shift scenarios, end-to-end |
| `tests/integration/test_approval_expiry_job.py` | An overdue `pending` `Approval` gets swept to `expired` |

### Implementation tasks

1. **P10.1** Write `events/types.py` (all five event types from contracts/events.md).
2. **P10.2** Write `services/event_bus.py` and `repositories/event_repo.py` — every
   `publish()` call persists the `Event` row first, then invokes subscribed handlers.
3. **P10.3** Write `events/handlers.py`: register the `StockLevelChanged` handler (checks
   `previous_status`/`new_status`, publishes `StockLowDetected` on a
   `ok→{low,out_of_stock}` or `low→out_of_stock` transition) and the shift-assignment
   handler (publishes `ShiftUnderstaffedDetected` when `assigned_count <
   required_staff_count`).
4. **P10.4** Add `notify_low_stock`/`notify_understaffed_shift` tools; each handler calls
   its tool through the normal Tool layer (not a shortcut), so the resulting `AgentRun`
   has `trigger_type="event"` and `triggering_event_id` set.
5. **P10.5** Modify `InventoryService`/`StaffingService` to publish their raw-fact events
   after a committed change.
6. **P10.6** Write `background/jobs.py` (an `asyncio.create_task` loop calling
   `ApprovalService.expire_overdue()` every N minutes, configurable via `Settings`); wire
   start/cancel into `api/main.py`'s lifespan.
7. **P10.7** [P] Write `tests/unit/test_event_bus.py`.
8. **P10.8** Write `tests/integration/test_event_driven_workflows.py`:
   - record a `StockAdjustment` that crosses the low-stock threshold (via
     `record_stock_adjustment`, no manager "ask") → `Event` rows for both
     `StockLevelChanged` and `StockLowDetected` exist → an `AgentRun` with
     `trigger_type="event"` exists → visible via a direct `AgentRun` query (Phase 12
     exposes this over HTTP).
   - remove a staff assignment such that a shift becomes understaffed → equivalent chain
     for `ShiftUnderstaffedDetected`.
9. **P10.9** Write `tests/integration/test_approval_expiry_job.py`.
10. **P10.10** Full regression run (Phases 1–10).

### Dependencies

P10.1 → P10.2 → P10.3 (needs the bus to register against) → P10.4/P10.5 → P10.6
(independent of P10.1–P10.5, only needs Phase 9's `expire_overdue()`).

### Tests

- `test_event_bus.py`: a published event reaches all subscribed handlers; an unsubscribed
  event type is a no-op, not an error.
- `test_event_driven_workflows.py`: the two FR-030 trigger conditions produce the correct
  event chain and agent-tool invocation, with **no** manager request involved.
- `test_approval_expiry_job.py`: an `Approval` with `expires_at` in the past transitions
  to `expired` after the job runs; one with a future `expires_at` does not.

### Acceptance criteria

- FR-030–FR-032 satisfied and tested.
- US3 both scenarios now pass (previously deferred from Phases 5 and 7).
- Every event-triggered action is distinguishable from a manager-requested one in the
  `AgentRun` trail (`trigger_type`), satisfying FR-032.
- quickstart.md step 6 passes for real.

### Commands to run

```bash
uv run pytest tests/unit/test_event_bus.py
uv run pytest tests/integration/test_event_driven_workflows.py
uv run pytest tests/integration/test_approval_expiry_job.py
uv run pytest tests/unit tests/contract tests/agent -x
```

### Definition of Done

- [ ] EventBus, event types, and handlers implemented per contracts/events.md.
- [ ] Inventory and Staffing services publish their raw-fact events.
- [ ] Both proactive-alert scenarios (US3) pass end-to-end.
- [ ] Background approval-expiry job running and tested.
- [ ] No regressions.
- [ ] Committed.

---

## Phase 11: Add OpenTelemetry Observability

**Depends on**: Phase 3 (tool layer to instrument), fully meaningful once Phase 8's
Orchestrator/`/chat` exists to produce end-to-end traces.

**Advances**: extends plan.md's Logging & Observability section (this task adds
distributed tracing/metrics on top of the structured-logging + `AgentRun`/`AgentMessage`
DB trail already built — a complementary observability surface, not a replacement for
FR-033–FR-035, which remain satisfied by the DB trail from Phases 2–3 onward).

### Files to create/change

| Path | Purpose |
|---|---|
| `src/dineops/observability/__init__.py`, `tracing.py`, `metrics.py` | OTel `TracerProvider`/`MeterProvider` setup, exporter selection |
| `src/dineops/config/settings.py` (modify) | Add `OTEL_EXPORTER_OTLP_ENDPOINT` (optional), `OTEL_SERVICE_NAME`, `OTEL_ENABLED` |
| `src/dineops/api/main.py` (modify) | Instrument FastAPI (`FastAPIInstrumentor`) and the SQLAlchemy engine (`SQLAlchemyInstrumentor`) on startup; expose `GET /metrics` (Prometheus exporter) |
| `src/dineops/tools/base.py` (modify) | Wrap `Tool.run()` in a span (`agent_name`, `tool_name`, `high_impact`, `outcome` attributes); **unify** `ToolContext.correlation_id` with the OTel `trace_id` for that request, rather than maintaining two parallel identifiers |
| `tests/unit/test_tracing.py` | Using OTel's `InMemorySpanExporter`: a tool call produces a span with expected attributes |
| `tests/integration/test_observability_e2e.py` | A full `/chat` request produces a connected trace (API → orchestrator run → specialist run → tool → repository spans, correctly parented); `AgentRun.correlation_id == trace_id` for that request |

### Implementation tasks

1. **P11.1** Add `opentelemetry-sdk`, `opentelemetry-instrumentation-fastapi`,
   `opentelemetry-instrumentation-sqlalchemy`, `opentelemetry-exporter-prometheus` (and
   `opentelemetry-exporter-otlp` as optional) to `pyproject.toml`.
2. **P11.2** Write `observability/tracing.py`: `TracerProvider` with a console exporter by
   default, OTLP exporter if `OTEL_EXPORTER_OTLP_ENDPOINT` is set; `observability/
   metrics.py`: counters (`tool_calls_total{agent,tool,outcome}`,
   `approvals_total{domain,status}`) and a histogram (`tool_call_duration_seconds`).
3. **P11.3** Instrument `api/main.py`: `FastAPIInstrumentor.instrument_app(app)`,
   `SQLAlchemyInstrumentor().instrument(engine=...)`, mount the Prometheus `/metrics`
   endpoint.
4. **P11.4** Modify `tools/base.py`'s `Tool.run()` wrapper to open a span per call and set
   `ToolContext.correlation_id` from the active trace's `trace_id`.
5. **P11.5** [P] Write `tests/unit/test_tracing.py` using `InMemorySpanExporter`.
6. **P11.6** Write `tests/integration/test_observability_e2e.py`.
7. **P11.7** Full regression run.

### Dependencies

P11.1 → P11.2 → P11.3/P11.4 (independent of each other) → P11.5/P11.6.

### Tests

- `test_tracing.py`: span attributes match what FR-034 needs to answer "which agent,
  which tool, why, outcome" from a trace alone.
- `test_observability_e2e.py`: parent-child span structure mirrors the
  Agent→Tool→Service→Repository call chain; trace_id/correlation_id unification holds.

### Acceptance criteria

- Every tool invocation and API request produces a span.
- `GET /metrics` exposes Prometheus-format counters/histograms for tool calls and
  approvals.
- `AgentRun.correlation_id` equals the OTel `trace_id` for requests made after this
  phase (verified in the integration test) — the two observability mechanisms
  (structured logs/DB trail from Phase 1–3, tracing from this phase) are now unified by
  one identifier rather than parallel, uncorrelated ones.

### Commands to run

```bash
uv run pytest tests/unit/test_tracing.py
uv run pytest tests/integration/test_observability_e2e.py
uv run uvicorn dineops.api.main:app --reload
curl -s localhost:8000/metrics | head -30
uv run pytest tests/unit tests/contract tests/agent -x
```

### Definition of Done

- [ ] OTel tracing + metrics wired into the API and tool layers.
- [ ] `/metrics` endpoint live.
- [ ] `correlation_id`/`trace_id` unification verified.
- [ ] No regressions.
- [ ] Committed.

---

## Phase 12: Build the Manager API/Dashboard

**Depends on**: Phase 6 (Memory), Phase 9 (Approvals), Phase 10 (Events — makes the
activity feed meaningful), Phase 11 (nice-to-have, not blocking).

**Advances**: closes FR-021, FR-033–FR-034 at the product-surface level; completes
**User Story 7**; completes contracts/api.md; this is the phase after which DineOps' MVP
is reachable end-to-end by a manager, not just by tests.

**Design note on "dashboard"**: plan.md's Structure Decision explicitly scoped this
project as a single backend service with no separate frontend project. To honor that
decision rather than silently reopening it, the dashboard is built as **server-rendered
HTML (Jinja2 templates) inside the existing FastAPI app** — no new frontend toolchain,
no SPA framework, no separate deployable. This is the lightest-weight option consistent
with "keep the stack appropriate for an MVP." If a richer client is wanted later, that's
a deliberate architecture change to flag and plan separately, not an incidental outcome
of this phase.

### Files to create/change

| Path | Purpose |
|---|---|
| `src/dineops/api/routes/activity.py` | `GET /activity` per contracts/api.md |
| `src/dineops/api/routes/memories.py` | `GET /memories`, `DELETE /memories/{id}` per contracts/api.md (FR-021) |
| `src/dineops/api/main.py` (modify) | Register both routers |
| `src/dineops/api/templates/base.html`, `dashboard.html`, `chat.html`, `approvals.html`, `activity.html` | Minimal Jinja2 dashboard pages |
| `src/dineops/api/routes/dashboard.py` | Serves the HTML pages, calling the same underlying services as the JSON API |
| `src/dineops/api/static/style.css` | Minimal styling, no JS framework |
| `tests/contract/test_activity_api.py`, `test_memories_api.py` | Schema conformance |
| `tests/integration/test_dashboard_smoke.py` | Each dashboard page returns 200 with expected content |
| `tests/integration/test_manager_journey_e2e.py` | quickstart.md steps 1–8, walked end-to-end via HTTP only — the MVP-complete acceptance test |

### Implementation tasks

1. **P12.1** [P] Write `api/routes/activity.py` (filterable, paginated, per
   contracts/api.md).
2. **P12.2** [P] Write `api/routes/memories.py`.
3. **P12.3** Register both routers in `api/main.py`.
4. **P12.4** Write the Jinja2 templates + `dashboard.py`: a chat box (`POST /chat` via a
   small vanilla-JS fetch, no framework), a pending-approvals list with approve/reject
   buttons, an activity feed (filterable by `trigger_type`, doubling as the proactive-
   alert view for event-triggered entries), a memory review list with a forget action.
5. **P12.5** [P] Write `tests/contract/test_activity_api.py`,
   `tests/contract/test_memories_api.py`.
6. **P12.6** Write `tests/integration/test_dashboard_smoke.py`.
7. **P12.7** Write `tests/integration/test_manager_journey_e2e.py`, walking quickstart.md
   end to end: infra up → health → routine booking → high-impact cancellation + approval
   → cross-agent memory recall → event-triggered alert → layer-boundary check → full
   suite.
8. **P12.8** Full regression run — the entire test suite, Phases 1–12.

### Dependencies

P12.1/P12.2 depend on Phase 9/6 services respectively. P12.4 depends on P12.1–P12.3.
P12.7 depends on everything.

### Tests

- `test_activity_api.py`/`test_memories_api.py`: schema conformance.
- `test_dashboard_smoke.py`: pages render without error against seeded/live data.
- `test_manager_journey_e2e.py`: the full quickstart, as one test — the closest thing
  this roadmap has to a single "is DineOps MVP done" assertion.

### Acceptance criteria

- contracts/api.md is now **100% implemented** (chat, approvals, activity, memories).
- FR-021, FR-033, FR-034 satisfied at the product surface (previously satisfied only at
  the data layer).
- **User Story 7** (review what an agent did and why) fully passes.
- quickstart.md is runnable exactly as written, end to end, against the live system.
- All of spec.md's Success Criteria SC-001, SC-003, SC-004, SC-005, SC-007, SC-008 are
  now directly verifiable through the product surface (SC-002/SC-006's statistical
  claims remain a live-LLM evaluation exercise beyond this roadmap's CI-blocking scope,
  as noted in Phases 6 and 8).

### Commands to run

```bash
uv run pytest tests/contract/test_activity_api.py tests/contract/test_memories_api.py
uv run pytest tests/integration/test_dashboard_smoke.py
uv run pytest tests/integration/test_manager_journey_e2e.py
uv run uvicorn dineops.api.main:app --reload
open http://localhost:8000/dashboard   # or curl, manual smoke check
uv run pytest   # entire suite, all phases
```

### Definition of Done

- [ ] `/activity` and `/memories` routes live and tested.
- [ ] Minimal manager dashboard functional for: chatting with the Orchestrator, deciding
      approvals, reviewing activity, reviewing/forgetting memories.
- [ ] `test_manager_journey_e2e.py` passes — the MVP-complete milestone.
- [ ] Full test suite (all 12 phases) green.
- [ ] Committed.

---

## Overall Dependency Graph

```
Phase 1 (foundation)
  └─> Phase 2 (models/migrations/seed)
        └─> Phase 3 (services + tools, all 5 domains + ApprovalService core)
              ├─> Phase 4 (Reservation Agent)      ─┐
              ├─> Phase 5 (Inventory Agent)          │
              ├─> Phase 6 (Customer Agent + Memory,   ├─> Phase 8 (Orchestrator)
              │     retrofits Phase 4)                │        └─> Phase 9 (Approval API,
              └─> Phase 7 (Staffing + Analytics)     ─┘                needs Phase 3 core)
                                                                          └─> Phase 10 (Events,
                                                                                needs Phases 5+7)
                                                                                └─> Phase 11 (OTel,
                                                                                      meaningful from Ph.8)
                                                                                      └─> Phase 12 (Dashboard,
                                                                                            needs Ph.6+9+10)
```

Phases 4, 5, and 7 have no dependency on each other and can be built in parallel once
Phase 3 is done; Phase 6 depends on Phase 4 specifically (it modifies the Reservation
Agent). Phase 8 needs all of 4–7. Everything from Phase 9 onward is strictly sequential
as listed.

## MVP-Complete Checklist (after Phase 12)

- [ ] All 39 functional requirements (FR-001–FR-039) satisfied and tested.
- [ ] All 7 user stories' acceptance scenarios pass.
- [ ] All 8 success criteria are either directly verified (SC-001, SC-003, SC-004,
      SC-005, SC-007, SC-008) or have their mechanism proven with the statistical claim
      flagged as a separate live-LLM evaluation exercise (SC-002, SC-006).
- [ ] No RAG, no document ingestion, no vector search anywhere in the codebase
      (FR-039) — confirmed by the absence of any such dependency in `pyproject.toml` and
      by `test_layer_boundaries.py`'s continued passing.
- [ ] Constitution Check (plan.md) still passes for the built system, not just the
      design.
