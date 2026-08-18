# Contract: Typed Tool Interface

This defines the shape every tool implements (see [plan.md](../plan.md#typed-tool-interface))
and the representative tool set per agent for MVP. This is an interface contract, not an
implementation — field types are named at the domain level; concrete Pydantic field
definitions are an implementation-phase task.

## Common shape

```python
class ToolContext(BaseModel):
    correlation_id: str
    acting_agent: str
    trigger_type: Literal["manager_request", "event"]
    trigger_ref: str

class Tool(Protocol):
    name: str
    description: str
    input_model: type[BaseModel]
    output_model: type[BaseModel]
    high_impact: bool

    async def run(self, input: BaseModel, *, context: ToolContext) -> BaseModel: ...
```

**High-impact contract**: if `high_impact is True`, `run()` MUST NOT perform the
underlying mutation. It MUST call `ApprovalService.propose(...)` and return a
`PendingApprovalOutput` (see below). The mutation happens only when `ApprovalService.decide()`
later approves it — enforced structurally, not by convention (see
[research.md §5](../research.md#5-approvalservice-execution-model)).

```python
class PendingApprovalOutput(BaseModel):
    approval_request_id: str
    status: Literal["pending"]
    summary: str          # human-readable description of the proposed action
```

## Reservation Agent tools

| Tool | Input | Output | high_impact |
|---|---|---|---|
| `find_availability` | date/time window, party_size | list of available (table, time) options | False |
| `create_reservation` | customer identifier, party_size, requested_time, table_id | Reservation (booked) | False |
| `modify_reservation` | reservation_id, changed fields | Reservation (updated) or `PendingApprovalOutput` | Conditional* |
| `cancel_reservation` | reservation_id | Reservation (cancelled) or `PendingApprovalOutput` | Conditional* |

\* `high_impact` is evaluated by `ReservationService` per call based on the reservation's
`party_size` against the configured threshold (spec Assumptions) — the tool itself always
declares `high_impact=True` for `cancel_reservation`/`modify_reservation` at the contract
level (worst case), and the service decides at runtime whether to actually route through
approval or execute directly, returning the corresponding output type either way.

## Inventory Agent tools

| Tool | Input | Output | high_impact |
|---|---|---|---|
| `get_stock_level` | item identifier | InventoryItem (quantity, status) | False |
| `record_stock_adjustment` | item identifier, delta, reason | StockAdjustment (applied) or `PendingApprovalOutput` | Conditional (by adjustment size/value) |
| `list_low_stock_items` | (none) | list of InventoryItem where status in {low, out_of_stock} | False |

## Customer Agent tools

| Tool | Input | Output | high_impact |
|---|---|---|---|
| `get_customer_profile` | customer identifier | Customer + recent visit history | False |
| `find_customer` | name/contact search terms | list of matching Customer summaries | False |

Preference/note recording is handled by the shared **Memory tools** below, not a
Customer-domain tool — a "note about a customer" is a `MemoryRecord` scoped to that
customer (see [data-model.md](../data-model.md#customer-domain)).

## Staffing Agent tools

| Tool | Input | Output | high_impact |
|---|---|---|---|
| `get_shift_schedule` | date range | list of StaffShift (with assignments) | False |
| `assign_staff_to_shift` | shift_id, staff_id | ShiftAssignment (created) or `PendingApprovalOutput` | Conditional (by `shift.is_published`) |
| `list_understaffed_shifts` | date range | list of StaffShift where status = understaffed | False |

## Analytics Agent tools

| Tool | Input | Output | high_impact |
|---|---|---|---|
| `get_performance_metric` | metric name (covers \| revenue \| no_show_rate \| popular_items), period | metric result, scoped to the requested period | False |

`get_performance_metric` is read-only by construction — Analytics has no mutating tools
(consistent with FR-017/FR-018 and the absence of owned Analytics tables in
[data-model.md](../data-model.md#analytics-domain)).

## Memory tools (shared across all agents)

| Tool | Input | Output | high_impact |
|---|---|---|---|
| `remember_fact` | scope (type + id), topic, content | MemoryRecord (created/updated) | False |
| `recall_facts` | scope (type + id), optional topic filter | list of MemoryRecord | False |
| `forget_fact` | memory_id | confirmation | False |

Memory tools are never high-impact — they change what the system *remembers*, not
restaurant operations state, and are explicitly excluded from the approval gate for that
reason (FR-021 still lets a manager override/delete a memory directly, independent of
this tool set, via the API — see [plan.md](../plan.md#memoryservice)).

## Orchestrator's tool

| Tool | Input | Output | high_impact |
|---|---|---|---|
| `delegate` | manager message, conversation context | one or more specialist-agent results, merged | False |

`delegate` is the Orchestrator's only tool (Constitution V) — it does not call domain
tools itself; it invokes specialist agent graphs, which in turn call their own tools.
