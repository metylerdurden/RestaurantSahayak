# Phase 1 Data Model: DineOps Core Platform MVP

Entities below correspond to spec.md's Key Entities section, expanded with fields,
relationships, validation rules, and state transitions needed to implement the services
in [plan.md](./plan.md). Two supporting entities (`Staff`, `EventLog`) are added because
Staffing and the EventBus need them structurally, even though the spec described them at
a higher level (staff assignments, event-driven workflows) rather than as named entities.

Fields are described at the domain level (types like "text", "timestamp", "money") — the
concrete SQLAlchemy column types are an implementation detail for `/speckit-tasks` /
implementation, not fixed here.

## Reservation domain

### Table

- `id`, `label` (e.g., "T12"), `seat_capacity` (int, > 0)
- Relationship: referenced by `Reservation.table_id` (nullable until assigned)

### Reservation

- `id`, `customer_id` (FK → Customer), `table_id` (FK → Table, nullable)
- `party_size` (int, > 0), `requested_time` (timestamp), `status`
- `created_via` (`manager_request` | `event`), `approval_request_id` (nullable FK →
  ApprovalRequest, set only when the action that produced/changed this row was
  high-impact)
- **Status state machine**: `requested → booked → {modified, cancelled}`;
  `booked → cancelled` and `booked → modified` are the only transitions gated by
  ApprovalService when the cancellation/modification meets the high-impact threshold
  (party size ≥ configured threshold, per spec Assumptions); smaller changes transition
  directly.
- **Validation**: `party_size` must not exceed the assigned `table.seat_capacity` at
  booking time; `requested_time` must be a future time at creation.

## Customer domain

### Customer

- `id`, `name`, `contact_info` (phone/email, at least one required)
- `created_at`
- Relationship: has many `Reservation`, has many `MemoryRecord` (scope `customer:<id>`)

Preferences and notes are **not** a separate relational entity — they are represented as
`MemoryRecord`s scoped to the customer (see below), per Constitution III ("no agent
persists 'remembered' state through any other path"). `Customer` itself holds only
identity/contact data that domain services (not memory) are responsible for.

## Memory subsystem

### MemoryRecord

- `id`, `scope_type` (e.g., `customer`, `domain`), `scope_id` (e.g., a customer id, or a
  domain name like `inventory`), `topic` (short tag, e.g., `seating_preference`)
- `content` (structured value — short text + optional structured payload), `source`
  (which agent/tool wrote it), `created_at`, `updated_at`
- **Validation**: `scope_type` + `scope_id` + `topic` uniquely identify the "current"
  fact for that topic — writing a new value for the same triple updates rather than
  duplicates, so recall doesn't need to reconcile conflicting memories at read time.
- **Lifecycle**: created via `MemoryService.remember`, updated via the same call
  (upsert on the scope+topic key), removed via `MemoryService.forget` — either
  agent-initiated (via `tools/memory_tools.py`) or manager-initiated (via the direct
  API route in [plan.md](./plan.md#memoryservice)) for FR-021 (manager corrects a
  wrong/outdated memory).

## Inventory domain

### InventoryItem

- `id`, `name`, `unit` (e.g., "kg", "each"), `quantity_on_hand` (numeric, ≥ 0)
- `low_stock_threshold` (numeric, ≥ 0), `status` (derived: `ok` | `low` | `out_of_stock`)
- **Derived status rule**: `out_of_stock` if `quantity_on_hand == 0`; `low` if
  `0 < quantity_on_hand <= low_stock_threshold`; else `ok`. Recomputed whenever a
  `StockAdjustment` is applied; a transition into `low`/`out_of_stock` is what
  `InventoryService` publishes as a `StockLowDetected`/`StockOutDetected` event (FR-030).

### StockAdjustment

- `id`, `item_id` (FK → InventoryItem), `delta` (numeric, may be negative), `reason`
  (`delivery` | `waste` | `correction` | `other`), `recorded_at`
- `approval_request_id` (nullable FK → ApprovalRequest — set when `abs(delta)` or its
  estimated value exceeds the high-impact threshold per spec Assumptions)
- **Validation**: applying `delta` must not take `quantity_on_hand` below 0.

## Staffing domain

### Staff

- `id`, `name`, `role` (e.g., server, cook, host)

### StaffShift

- `id`, `date`, `start_time`, `end_time`, `required_staff_count` (int, > 0)
- `is_published` (bool), `status` (derived: `understaffed` if assigned count <
  `required_staff_count`, else `staffed`)
- Relationship: has many `ShiftAssignment`

### ShiftAssignment

- `id`, `shift_id` (FK → StaffShift), `staff_id` (FK → Staff)
- `approval_request_id` (nullable FK → ApprovalRequest — set when the assignment change
  is made against an already-`is_published=true` shift, per spec Assumptions)
- **Validation**: a `Staff` member cannot be double-booked to two `StaffShift`s with
  overlapping `date`/time ranges.
- **State rule**: creating/removing an assignment on an unpublished shift applies
  immediately; on a published shift it routes through ApprovalService first
  (`StaffShift.is_published` is the high-impact discriminator for this domain, not a
  numeric threshold, per FR-015/FR-026).

## Analytics domain

Analytics has no owned tables — `analytics_repo.py` runs read-only aggregate queries
across `Reservation`, `Customer`, `InventoryItem`/`StockAdjustment` (for cost/waste
figures if needed), and `StaffShift` (for labor-vs-covers questions), per
[research.md §7](./research.md#7-cross-domain-reads-analytics). No new entity is
introduced for MVP; if a materialized/pre-aggregated reporting table becomes necessary for
performance, that is a later addition, not part of this MVP data model.

## Approval subsystem

### ApprovalRequest

- `id`, `domain` (`reservation` | `inventory` | `staffing` | ...), `proposed_by_tool`
  (tool name), `proposed_action` (structured description of the captured intent — see
  [research.md §5](./research.md#5-approvalservice-execution-model))
- `status` (`pending` | `approved` | `rejected`), `decided_by` (manager identifier),
  `decided_at` (nullable), `created_at`
- **State machine**: `pending → approved` (triggers re-invocation of the captured intent,
  then the originating row, e.g. `Reservation.status`, transitions accordingly) or
  `pending → rejected` (terminal; originating row is left unchanged). No further
  transitions once decided (FR-028).

## Observability subsystem

### AgentActivityRecord

- `id`, `correlation_id`, `agent_name`, `tool_name`, `trigger_type`
  (`manager_request` | `event`), `trigger_ref` (request id or event id)
- `input_summary`, `outcome` (`success` | `failure` | `pending_approval`),
  `approval_request_id` (nullable FK → ApprovalRequest), `occurred_at`
- **Validation**: written for **every** tool invocation regardless of outcome (FR-033) —
  this table has no "skip on success" path, since FR-034/FR-035 require reviewing both
  approved and routine actions.

### EventLog

- `id`, `event_type`, `payload` (structured, matches the Pydantic `Event` subtype in
  `events/types.py`), `published_by` (service name), `occurred_at`, `handled` (bool)
- Exists to make the in-process EventBus durable/replayable
  ([research.md §4](./research.md#4-eventbus-implementation)) and to give Observability a
  record of event-triggered chains independent of the `AgentActivityRecord`s they caused
  (one event may cause zero, one, or multiple activity records).

## Cross-entity relationship summary

```
Customer 1───* Reservation *───1 Table
Customer 1───* MemoryRecord (scope_type=customer)
InventoryItem 1───* StockAdjustment
StaffShift 1───* ShiftAssignment *───1 Staff
ApprovalRequest 1───{0,1} Reservation | StockAdjustment | ShiftAssignment  (via approval_request_id)
ApprovalRequest 1───* AgentActivityRecord
EventLog 1───* AgentActivityRecord (via trigger_ref, when trigger_type=event)
```
