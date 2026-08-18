# Contract: EventBus Events

Event types are Pydantic models (`events/types.py`). Every event carries a common
envelope; domain-specific payloads extend it.

## Common envelope

```python
class Event(BaseModel):
    event_id: str
    event_type: str
    occurred_at: datetime
    published_by: str          # service name that published it
```

## MVP event types

### `StockLevelChanged`

Published by `InventoryService` after any `StockAdjustment` is applied — the raw fact, not
yet a judgment about whether it's alert-worthy.

```python
class StockLevelChanged(Event):
    item_id: str
    quantity_on_hand: Decimal
    previous_status: Literal["ok", "low", "out_of_stock"]
    new_status: Literal["ok", "low", "out_of_stock"]
```

### `StockLowDetected`

Published by the handler in `events/handlers.py` that watches `StockLevelChanged` and
fires when `new_status != previous_status and new_status in {"low", "out_of_stock"}`.
This is the event that triggers the Inventory Agent's proactive-notification tool
(FR-030, spec User Story 3).

```python
class StockLowDetected(Event):
    item_id: str
    item_name: str
    new_status: Literal["low", "out_of_stock"]
```

### `ShiftUnderstaffedDetected`

Published by the handler watching `ShiftAssignment` create/delete operations, when a
shift's assigned count drops below `required_staff_count`. Triggers the Staffing Agent's
proactive-notification tool.

```python
class ShiftUnderstaffedDetected(Event):
    shift_id: str
    shift_date: date
    assigned_count: int
    required_count: int
```

### `ApprovalRequested`

Published by `ApprovalService.propose()` for every high-impact action proposal, regardless
of domain — lets a single notification path inform the manager of any pending decision
without each domain needing its own "tell the manager" logic.

```python
class ApprovalRequested(Event):
    approval_request_id: str
    domain: str
    summary: str
```

### `ApprovalDecided`

Published by `ApprovalService.decide()`. Feeds the activity log and any future
notification of the outcome back to whoever/whatever proposed it.

```python
class ApprovalDecided(Event):
    approval_request_id: str
    decision: Literal["approved", "rejected"]
    decided_by: str
```

## Handler registration contract

`events/handlers.py` maps event type → handler function(s). A handler:

- receives the typed event (never a raw dict),
- may call exactly one specialist agent's tool (via the normal Tool layer — not a
  shortcut into a service), so event-triggered actions produce the same
  `AgentActivityRecord` trail as manager-requested ones (FR-032),
- must not publish the same event type it's handling (no direct handler self-loops); any
  chain of events (e.g., `StockLevelChanged → StockLowDetected → notification tool call`)
  is expected to be shallow and one-directional for MVP.

## Durability

Every published event is written to `EventLog` ([data-model.md](../data-model.md#eventlog))
before handlers run, so a crash mid-handling doesn't silently lose the event — replay/
reprocessing tooling is out of scope for MVP but the log makes it possible later without a
schema change.
