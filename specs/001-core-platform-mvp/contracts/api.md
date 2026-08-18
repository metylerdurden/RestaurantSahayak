# Contract: API Layer (FastAPI)

Three route groups. All request/response bodies are Pydantic models (Constitution VI) —
no endpoint accepts or returns an untyped dict. This is an interface contract; handler
implementations belong to a later task phase.

## `POST /chat`

Manager's conversational entry point → Orchestrator Agent.

**Request**

```json
{
  "message": "string",
  "conversation_id": "string | null"
}
```

**Response**

```json
{
  "conversation_id": "string",
  "reply": "string",
  "results": [
    {
      "agent": "reservation | inventory | customer | staffing | analytics",
      "tool": "string",
      "outcome": "success | pending_approval | declined",
      "approval_request_id": "string | null"
    }
  ]
}
```

- `results` mirrors, at API granularity, the tool calls the Orchestrator's delegation made
  for this message (one entry per specialist tool call) — this is what lets a thin client
  show "this is waiting on your approval" inline with the reply, without a second round
  trip.
- Every `POST /chat` call produces at least one `AgentActivityRecord`
  (`trigger_type=manager_request`), even if the Orchestrator declines the request
  (FR-004, FR-033).

## `GET /approvals`

List pending (and, with a filter, decided) approval requests.

**Query params**: `status` (`pending` default | `approved` | `rejected` | `all`),
`domain` (optional filter), pagination params.

**Response**

```json
{
  "items": [
    {
      "id": "string",
      "domain": "string",
      "proposed_by_tool": "string",
      "summary": "string",
      "status": "pending | approved | rejected",
      "created_at": "timestamp",
      "decided_at": "timestamp | null"
    }
  ],
  "next_page_token": "string | null"
}
```

## `POST /approvals/{id}/decision`

Record a manager decision on a pending approval.

**Request**

```json
{
  "decision": "approve | reject",
  "decided_by": "string"
}
```

**Response**: the updated `ApprovalRequest` (same shape as one item in `GET /approvals`).

- Approving re-invokes the originally proposed action via `ApprovalService.decide()`
  ([plan.md](../plan.md#approvalservice)); the response reflects the post-execution state
  once that completes, or an error if execution failed after approval (a distinct,
  reportable failure mode from rejection).
- A request whose `status` is already `approved`/`rejected` MUST reject a further decision
  with a conflict error (FR-028 — decisions are terminal).

## `GET /activity`

Manager/admin-facing agent activity log (FR-034).

**Query params**: `agent`, `tool`, `trigger_type`, `from`/`to` timestamp range,
pagination params.

**Response**

```json
{
  "items": [
    {
      "id": "string",
      "correlation_id": "string",
      "agent_name": "string",
      "tool_name": "string",
      "trigger_type": "manager_request | event",
      "trigger_ref": "string",
      "outcome": "success | failure | pending_approval",
      "approval_request_id": "string | null",
      "occurred_at": "timestamp"
    }
  ],
  "next_page_token": "string | null"
}
```

## Memory review routes (manager-direct, not agent-mediated — FR-021)

## `GET /memories?scope_type=customer&scope_id={id}`

Returns `MemoryRecord`s for a scope, for manager review.

## `DELETE /memories/{id}`

Removes a memory record directly (manager correcting/removing an outdated fact), bypassing
agent tool-calling entirely since this is a human acting on the system's memory, not an
agent action.

## Cross-cutting conventions

- All timestamps are UTC, ISO-8601.
- All list endpoints are paginated (`next_page_token` cursor style); no unbounded list
  responses.
- Error responses follow a single shape: `{"error": {"code": "string", "message": "string"}}`.
- `correlation_id` (generated per request, per [plan.md](../plan.md#logging--observability))
  is returned in a response header (`X-Correlation-Id`) on every response, including
  errors, so a manager-reported issue can be traced through logs and the activity log.
