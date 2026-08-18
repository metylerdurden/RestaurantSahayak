# Quickstart: Validating the DineOps Core Platform Architecture

This describes how, once implementation exists, a developer proves the architecture in
[plan.md](./plan.md) actually holds together end-to-end. It is a validation guide, not
implementation code — commands and expected outcomes only.

## Prerequisites

- Docker + Docker Compose available (see project README for the environment gap noted at
  project setup — Docker must be installed locally before this guide is runnable).
- Python 3.12+ environment with project dependencies installed.
- `.env` populated from `.env.example` (database URL, LLM provider credentials).

## 1. Bring up local infrastructure

```bash
docker compose -f docker/docker-compose.yml up -d postgres
alembic upgrade head
```

**Expected outcome**: Postgres is running and its schema matches
[data-model.md](./data-model.md) — every table listed there exists.

## 2. Start the API

```bash
docker compose -f docker/docker-compose.yml up api
# or, for local iteration: uvicorn dineops.api.main:app --reload
```

**Expected outcome**: `GET /activity` (empty list) responds successfully — confirms the
API layer, config loading, and DB connectivity all wired correctly before touching agents.

## 3. Exercise a routine (non-high-impact) path

Send a `POST /chat` request asking to book a reservation for an open table/time (see
[contracts/api.md](./contracts/api.md)).

**Expected outcome**:
- Response `results` shows one entry with `agent: reservation`, `tool:
  create_reservation`, `outcome: success`.
- `GET /activity` now shows exactly one new `AgentActivityRecord` for that tool call,
  `trigger_type: manager_request`.
- No entry appears in `GET /approvals` — routine actions never create one.

This validates: Agent → Tool → Service → Repository → DB for a non-gated action, and that
Observability captures it (FR-033).

## 4. Exercise a high-impact path (human approval)

Send a `POST /chat` request to cancel a reservation whose `party_size` is at/above the
configured high-impact threshold.

**Expected outcome**:
- Response `results` shows `outcome: pending_approval` with a populated
  `approval_request_id`; the reservation's status is **unchanged** in the database at this
  point — proves `high_impact` tools do not mutate before approval
  ([contracts/tools.md](./contracts/tools.md)).
- `GET /approvals?status=pending` includes that request.
- `POST /approvals/{id}/decision` with `{"decision": "approve", ...}` then transitions
  the reservation to `cancelled`.
- `GET /activity` shows the approval decision linked (`approval_request_id`) to the
  original tool-call activity record (FR-035).

This validates the full ApprovalService lifecycle
([research.md §5](./research.md#5-approvalservice-execution-model)).

## 5. Exercise memory recall across agents

1. Send a `POST /chat` message to the Customer Agent recording a preference for a named
   customer (e.g., "note that the Patels prefer a window table").
2. Send a separate `POST /chat` message booking a reservation for that same customer.

**Expected outcome**: the booking response/reply references the stored preference without
it being restated in step 2's message — proves `MemoryRecord`s written by one agent
(Customer) are readable by another (Reservation) exclusively through
`tools/memory_tools.py`, never a direct cross-service read
([plan.md](./plan.md#memoryservice)).

## 6. Exercise an event-triggered workflow

Directly record a `StockAdjustment` that drops an `InventoryItem` at/below its
`low_stock_threshold` (via the Inventory Agent's `record_stock_adjustment` tool, or a
seeded test fixture).

**Expected outcome**:
- An `EventLog` row for `StockLevelChanged`, then `StockLowDetected`, appears
  ([contracts/events.md](./contracts/events.md)).
- A new `AgentActivityRecord` appears with `trigger_type: event`,
  `trigger_ref` pointing at the `StockLowDetected` event — with **no** corresponding
  `POST /chat` request having been made — proving the EventBus can trigger agent tool
  calls independent of manager conversation (FR-030–FR-032).

## 7. Confirm the layer boundary is enforced, not just documented

```bash
pytest tests/unit/test_layer_boundaries.py
```

**Expected outcome**: passes, confirming (by import inspection) that no module under
`agents/` or `tools/` imports `sqlalchemy`, `dineops.models`, or `dineops.repositories` —
the Constitution I gate as a running check, not just a plan.md claim.

## 8. Full test suite

```bash
pytest tests/unit tests/contract tests/agent   # fast, no external LLM calls
pytest tests/integration                        # requires docker compose postgres running
```

**Expected outcome**: all tiers pass locally and in CI, per
[plan.md's Testing Architecture](./plan.md#testing-architecture).
