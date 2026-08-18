# Feature Specification: DineOps Core Platform MVP

**Feature Branch**: `001-core-platform-mvp`

**Created**: 2026-08-19

**Status**: Draft

**Input**: User description: "DineOps MVP: a multi-agent AI restaurant operations platform for restaurant managers/operators. The manager interacts with an Orchestrator Agent (via chat-style interface) that understands a request and delegates to the right specialist agent(s): a Reservation Agent, an Inventory Agent, a Customer Agent, a Staffing Agent, and an Analytics Agent. All agents share a persistent long-term MemoryService. Every agent action against the system goes through typed tools with defined inputs/outputs. Actions with real operational or financial consequence require explicit human approval before they take effect. The system reacts to events and can trigger agent workflows in response. The platform must be observable and every agent/tool must be testable in isolation. No document upload, document search, or retrieval-augmented generation. MVP scope: one restaurant location, one manager-level user role, the five specialist agents plus the orchestrator — no additional agents or features."

## Overview

**Problem**: Running a restaurant's day-to-day operations means juggling several
disconnected concerns — who's booked in tonight, what's running low in the walk-in,
which regulars want a booth by the window, whether tomorrow's dinner shift is covered,
and whether last week's numbers were any good. A manager currently has to context-switch
across separate systems (or separate mental models) to answer these questions and act on
them, and nothing remembers what was learned yesterday. DineOps gives the manager a single
point of interaction — an Orchestrator Agent backed by specialist agents — that
understands operational requests, acts on them through governed tools, remembers
relevant facts over time, and asks for a human's sign-off before anything consequential
happens.

**Target User**: A restaurant manager/operator — the person responsible for a single
restaurant location's day-to-day floor, kitchen-supply, staffing, and guest-relationship
decisions. This person is not a developer and does not want to learn separate tools for
reservations, inventory, customer notes, scheduling, and reporting; they want to ask a
question or give an instruction and trust that the right thing happens, with a
human checkpoint before anything risky.

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Manage a reservation through the Orchestrator (Priority: P1)

A manager tells the Orchestrator what they want done about a reservation — book a table,
change a party size, or cancel a booking — in plain language, without knowing which
specialist agent handles it. The Orchestrator identifies the request as reservation-related,
delegates to the Reservation Agent, and returns a clear confirmation or a proposed action
awaiting approval. If the guest is a returning customer with a stored preference (e.g.,
"always seat by the window"), that preference is surfaced automatically.

**Why this priority**: This is the smallest end-to-end slice that proves the entire
platform shape at once: orchestration and delegation, a specialist agent acting only
through typed tools, memory recall, and the human-approval gate for a high-impact case
(e.g., cancelling a large party). Without this working, nothing else in DineOps matters.

**Independent Test**: Can be fully tested by sending a handful of reservation requests
(routine booking, modification, and a large-party cancellation) to the Orchestrator and
verifying correct delegation, correct tool use, correct memory recall, and that the
large-party cancellation is held for approval before taking effect.

**Acceptance Scenarios**:

1. **Given** an open table matching the requested time and party size, **When** the
   manager asks the Orchestrator to book a reservation, **Then** the Reservation Agent
   creates the reservation and the Orchestrator confirms the booking back to the manager.
2. **Given** a returning customer with a stored seating preference, **When** the manager
   books a reservation for that customer by name, **Then** the response surfaces the
   stored preference without the manager having to ask for it.
3. **Given** an existing reservation for a large party, **When** the manager asks to
   cancel it, **Then** the system presents the cancellation as a proposed action awaiting
   human approval rather than cancelling it immediately.
4. **Given** a proposed cancellation awaiting approval, **When** the manager approves it,
   **Then** the reservation is cancelled and the manager is told it's done; **When** the
   manager rejects it, **Then** the reservation is left unchanged.
5. **Given** no table is available for the requested time and party size, **When** the
   manager asks to book, **Then** the Orchestrator explains the conflict and offers
   alternatives instead of silently failing.

---

### User Story 2 - Look up and update a customer profile (Priority: P2)

A manager asks about a customer ("What do we know about the Patels?") or tells the system
something worth remembering ("The Patels are allergic to shellfish, note that"). The
Customer Agent retrieves or updates the customer's profile, preferences, and visit
history, and what it learns becomes available to other agents (e.g., the Reservation
Agent) without the manager repeating themselves.

**Why this priority**: Customer knowledge is the connective tissue that makes the rest of
the platform feel intelligent rather than transactional; it's the clearest demonstration
of the shared MemoryService working across agents, not just within one.

**Independent Test**: Can be fully tested by asking about a known customer, recording a
new preference or note through the Customer Agent, and then verifying that a later,
unrelated interaction (e.g., a reservation booking in User Story 1) reflects that same
fact without it being re-entered.

**Acceptance Scenarios**:

1. **Given** an existing customer record, **When** the manager asks what's known about
   that customer, **Then** the Customer Agent returns their profile, preferences, and
   recent visit history.
2. **Given** a manager states a new preference or note about a customer, **When** the
   Customer Agent records it, **Then** that fact is retrievable in later conversations and
   by other agents that need it.
3. **Given** a request about a person with no existing record, **When** the manager asks
   about them, **Then** the system clearly states no profile exists rather than guessing.

---

### User Story 3 - Get proactively alerted to operational issues (Priority: P2)

Rather than the manager having to ask, the system watches for operationally meaningful
events — a stock item crossing its low-stock threshold, an upcoming shift becoming
understaffed — and proactively surfaces them to the manager as soon as they're detected.

**Why this priority**: This is what makes DineOps feel like an operations partner rather
than a chatbot that only answers when spoken to; event-driven behavior is a distinct
interaction mode from the request/response flow in Stories 1, 2, 4–6 and needs to be
proven independently.

**Independent Test**: Can be fully tested by triggering a qualifying condition (e.g.,
adjusting stock below its threshold, or removing a staff member from a shift such that
it's understaffed) and verifying a proactive notification is generated without any manager
request.

**Acceptance Scenarios**:

1. **Given** an inventory item's stock level drops to or below its configured threshold,
   **When** the drop is recorded, **Then** the manager is proactively notified that the
   item is low, including which agent detected it.
2. **Given** a shift's assigned staff count falls below its required minimum, **When** the
   shortfall occurs, **Then** the manager is proactively notified of the understaffed
   shift.
3. **Given** multiple qualifying events occur close together, **When** notifications are
   generated, **Then** each is delivered as a distinct, attributable alert rather than
   merged or dropped.

---

### User Story 4 - Track and adjust inventory on demand (Priority: P3)

A manager asks about current stock levels or records a stock adjustment (receiving a
delivery, logging waste/spoilage) through the Inventory Agent.

**Why this priority**: Valuable on its own, but the platform is still coherent without it
if Stories 1–3 exist; inventory queries are additive rather than foundational.

**Independent Test**: Can be fully tested by asking about a specific item's stock level,
recording an adjustment, and confirming the new level and the flag state (low/out of
stock) update accordingly, with a large write-off routed through approval.

**Acceptance Scenarios**:

1. **Given** a manager asks the current stock level of an item, **When** the Inventory
   Agent looks it up, **Then** it returns the current quantity and low/out-of-stock status.
2. **Given** a manager logs a routine stock adjustment (e.g., a delivery received),
   **When** the Inventory Agent records it, **Then** the item's stock level updates
   immediately and no approval is required.
3. **Given** a manager logs a large write-off (e.g., spoilage above the high-impact
   threshold), **When** the Inventory Agent processes it, **Then** the adjustment is held
   for human approval before the stock level changes.

---

### User Story 5 - Manage staff shift schedules (Priority: P3)

A manager views the current shift schedule or asks the Staffing Agent to create or adjust
a shift assignment.

**Why this priority**: Complements Story 3's understaffing alerts with the ability to act
on them, but is not required for the platform's core value loop to be demonstrable.

**Independent Test**: Can be fully tested by requesting the current schedule, creating a
new shift assignment, and modifying a published shift — confirming the published-schedule
change is routed through approval while the new (unpublished) assignment is not.

**Acceptance Scenarios**:

1. **Given** a date range, **When** the manager asks for the shift schedule, **Then** the
   Staffing Agent returns the assigned staff per shift for that range.
2. **Given** an open, unpublished shift, **When** the manager assigns a staff member to
   it, **Then** the Staffing Agent records the assignment immediately.
3. **Given** an already-published shift schedule, **When** the manager asks to change a
   staff assignment on it, **Then** the change is held for human approval before the
   published schedule is altered.

---

### User Story 6 - Ask analytics questions about performance (Priority: P3)

A manager asks a question about how the restaurant is performing — covers, revenue,
no-show rate, most popular items — over a given period, and the Analytics Agent answers
using existing structured operational data.

**Why this priority**: High value for decision-making but depends on the other agents'
data existing first; it is the natural last domain to validate.

**Independent Test**: Can be fully tested by asking a handful of representative
performance questions over a known data set and verifying the answers match the
underlying records.

**Acceptance Scenarios**:

1. **Given** a manager asks for a performance metric (e.g., covers or revenue) over a
   specified period, **When** the Analytics Agent computes it, **Then** it returns a
   correct, clearly-scoped answer (what period, what metric).
2. **Given** a manager asks a question the available structured data cannot answer,
   **When** the Analytics Agent processes it, **Then** it says so explicitly rather than
   fabricating a figure.

---

### User Story 7 - Review what an agent did and why (Priority: P3)

A manager (or an admin) reviews a log of recent agent activity — which agent acted, which
tool it used, what triggered it, and what the outcome was — to understand and trust what
the system has been doing on its own, including proactive alerts and approved actions.

**Why this priority**: Trust and accountability are prerequisites for a manager delegating
real operational actions to agents, but the activity log is a lens on behavior the other
stories already produce, not a new capability on its own.

**Independent Test**: Can be fully tested by performing a mix of manager-requested and
event-triggered agent actions from the stories above, then verifying each appears in the
activity log with agent, tool, trigger, and outcome.

**Acceptance Scenarios**:

1. **Given** an agent has taken an action (manager-requested or event-triggered), **When**
   the manager reviews recent activity, **Then** they can see which agent acted, which
   tool was used, what triggered it, and the result.
2. **Given** an action was held for approval, **When** the manager reviews activity,
   **Then** the log shows the proposal, who approved or rejected it, and when.

---

### Edge Cases

- What happens when a request is ambiguous and could plausibly belong to more than one
  specialist agent (e.g., a question that touches both reservations and staffing)?
- What happens when the manager rejects a proposed high-impact action — is the original
  request discarded, or can the manager revise and resubmit it?
- What happens when a request falls outside all five agents' domains?
- What happens when the MemoryService holds a fact that is now outdated or contradicted by
  a new statement from the manager (e.g., a changed customer preference)?
- What happens when a time-sensitive proposed action (e.g., a cancellation ahead of a
  deadline) is not approved or rejected in time?
- What happens when two events qualify for a proactive alert at nearly the same moment?
- What happens when a specialist agent's underlying data is missing or incomplete for a
  request it otherwise understands (e.g., an analytics question about a period with no
  recorded data)?

## Requirements *(mandatory)*

### Functional Requirements

**Orchestration & Delegation**

- **FR-001**: The system MUST provide a single conversational entry point (the
  Orchestrator) through which the manager expresses requests in plain language, without
  needing to know which specialist agent handles them.
- **FR-002**: The Orchestrator MUST determine which specialist agent(s) — Reservation,
  Inventory, Customer, Staffing, Analytics — a request belongs to and delegate accordingly.
- **FR-003**: The Orchestrator MUST be able to involve more than one specialist agent for
  a single request when the request spans domains, and combine their results into one
  coherent response.
- **FR-004**: When a request does not clearly match any specialist agent's domain, the
  Orchestrator MUST say so rather than guessing or silently failing.

**Reservation Agent**

- **FR-005**: The Reservation Agent MUST support viewing, creating, modifying, and
  cancelling reservations.
- **FR-006**: The Reservation Agent MUST track table availability sufficient to confirm or
  reject a requested booking time and party size.
- **FR-007**: When a requested reservation cannot be fulfilled as asked, the Reservation
  Agent MUST communicate the conflict and, where possible, offer alternatives.

**Inventory Agent**

- **FR-008**: The Inventory Agent MUST support querying current stock levels for
  operational items.
- **FR-009**: The Inventory Agent MUST support recording stock adjustments (e.g.,
  deliveries received, waste/spoilage, manual corrections).
- **FR-010**: The Inventory Agent MUST flag items that are at or below a low-stock
  threshold, and separately flag items that are out of stock.

**Customer Agent**

- **FR-011**: The Customer Agent MUST support retrieving a customer's profile, stated
  preferences, and visit history.
- **FR-012**: The Customer Agent MUST support recording new or updated preferences and
  free-form notes about a customer.
- **FR-013**: Facts recorded by the Customer Agent MUST be available to other specialist
  agents that need them (e.g., the Reservation Agent surfacing a seating preference).

**Staffing Agent**

- **FR-014**: The Staffing Agent MUST support viewing the shift schedule for a given date
  range.
- **FR-015**: The Staffing Agent MUST support creating and adjusting shift assignments.
- **FR-016**: The Staffing Agent MUST flag shifts where assigned staff count falls below
  the shift's required minimum.

**Analytics Agent**

- **FR-017**: The Analytics Agent MUST answer manager questions about operational
  performance (at minimum: covers, revenue, no-show rate, popular items) over a specified
  period, using only existing structured operational data produced by the other agents'
  domains.
- **FR-018**: When a requested metric cannot be computed from available structured data,
  the Analytics Agent MUST say so rather than producing an unsupported figure.

**Persistent Long-Term Memory**

- **FR-019**: The system MUST persist relevant facts learned during interactions (e.g.,
  customer preferences, recurring operational notes) beyond a single conversation, through
  the MemoryService.
- **FR-020**: Memories MUST be scoped (e.g., to a specific customer, agent domain, or
  topic) so agents retrieve only relevant facts, not an undifferentiated dump.
- **FR-021**: The manager MUST be able to see and correct/remove a stored memory that is
  wrong or outdated.
- **FR-022**: Memory retrieval and storage MUST occur only through defined MemoryService
  operations — no agent persists "remembered" state through any other path.

**Typed Tools**

- **FR-023**: Every action an agent takes against reservations, inventory, customers,
  staffing, or analytics data MUST occur through a named tool with a defined input and
  output structure — never through an open-ended or unstructured access path.
- **FR-024**: A request an agent cannot express as one or more defined tool calls MUST be
  declined or escalated, not improvised.
- **FR-025**: Each tool's inputs, outputs, and side effects (including whether it is
  high-impact / approval-gated) MUST be documented before the tool is available for an
  agent to use.

**Human Approval for High-Impact Actions**

- **FR-026**: The system MUST classify actions as high-impact or routine, based on
  criteria defined per domain (see Assumptions for MVP default thresholds).
- **FR-027**: A high-impact action MUST be presented to the manager as a proposal and MUST
  NOT take effect until the manager approves it.
- **FR-028**: The manager MUST be able to reject a proposed high-impact action, in which
  case it does not take effect and the rejection is recorded.
- **FR-029**: Routine (non-high-impact) actions MUST NOT require approval, to avoid
  approval fatigue that would undermine the gate's purpose for genuinely high-impact cases.

**Event-Driven Workflows**

- **FR-030**: The system MUST detect defined operational events (at minimum: stock
  crossing its low-stock threshold, a shift becoming understaffed) as they occur, without
  requiring a manager request.
- **FR-031**: A detected event MUST be able to trigger the relevant specialist agent to
  act (e.g., generate a proactive notification) without manager-initiated conversation.
- **FR-032**: Each event-triggered action MUST be distinguishable from a manager-requested
  action in how it's presented and logged (see Observability).

**Observability**

- **FR-033**: The system MUST record, for every agent action (manager-requested or
  event-triggered), which agent acted, which tool(s) were used, what triggered the action,
  and its outcome.
- **FR-034**: The manager or an admin MUST be able to review recent agent activity in
  plain terms without inspecting raw system internals.
- **FR-035**: For any action that went through the approval gate, the activity record MUST
  show the original proposal, the approval/rejection decision, who made it, and when.

**Testing**

- **FR-036**: Every tool MUST be verifiable in isolation, independent of any specific
  agent or conversation flow.
- **FR-037**: Every specialist agent's delegated behavior MUST be verifiable independent
  of the other specialist agents (per the Independent Test defined in each user story).
- **FR-038**: The Orchestrator's delegation logic MUST be verifiable independent of the
  specialist agents' internal behavior (i.e., correct routing can be confirmed without
  needing every agent fully implemented).

**Explicit Exclusions**

- **FR-039**: The system MUST NOT provide document upload, document search, or any
  retrieval-augmented generation capability. Agents reason only over structured
  operational data (via typed tools) and the MemoryService.

### Key Entities

- **Reservation**: A booking for a party at the restaurant — party size, requested time,
  table, status (booked/modified/cancelled), and the guest it's associated with.
- **Table**: A bookable unit of seating capacity used to determine availability.
- **Customer**: A guest profile — identity, stated preferences, free-form notes, and visit
  history — shared across the Customer and Reservation domains.
- **Memory Record**: A persisted fact managed by the MemoryService, scoped to an entity
  (e.g., a customer) or a topic, with enough context to know why it was stored and by
  which agent/interaction.
- **Inventory Item**: A tracked stock item — current quantity, low-stock threshold, and
  current flag state (ok/low/out of stock).
- **Stock Adjustment**: A recorded change to an inventory item's quantity, with a reason
  (delivery, waste, correction, etc.) and whether it required approval.
- **Staff Shift**: A scheduled block of time on a given date requiring a minimum number of
  staff, with the staff members currently assigned to it and its published/unpublished
  state.
- **Approval Request**: A proposed high-impact action awaiting a manager decision —
  originating agent/tool, the action proposed, and its eventual approve/reject outcome.
- **Agent Activity Record**: A log entry capturing one agent action — acting agent, tool(s)
  used, trigger (manager request or event), and outcome, including a link to any
  associated Approval Request.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: A manager can complete a routine reservation request (book, modify, or
  cancel) through a single conversation with the Orchestrator in under 2 minutes, without
  needing to know which agent handles it.
- **SC-002**: When a returning customer with a stored preference is involved in a
  reservation, that preference is surfaced without the manager restating it, in at least
  95% of such interactions during testing.
- **SC-003**: 100% of actions classified as high-impact are held for approval and never
  take effect without an explicit manager decision, verified across all five domains.
- **SC-004**: A manager can find out which agent did what, using which tool, and why, for
  any action from the last 24 hours, within under 1 minute, without help from a developer.
- **SC-005**: When a monitored condition (low stock or understaffed shift) occurs, a
  proactive notification reaches the manager without any request from them, in 100% of
  tested trigger cases.
- **SC-006**: Across a representative test set of requests for each of the five domains,
  the Orchestrator delegates to the correct specialist agent at least 95% of the time.
- **SC-007**: 100% of typed tools have at least one passing automated test that exercises
  them independent of any specific agent conversation.
- **SC-008**: Zero instances of document upload, document search, or retrieval-based
  answers appear anywhere in the MVP — every agent answer is traceable to a typed tool
  call, a memory record, or a stated inability to answer.

## Assumptions

- **Scope**: MVP covers exactly one restaurant location and one user role (manager); no
  multi-location support, staff/kitchen self-service roles, or guest-facing surfaces are
  in scope.
- **Agents**: MVP includes exactly the Orchestrator plus the five named specialist agents.
  No additional specialist agents (e.g., marketing, procurement) are in scope for MVP.
- **High-impact thresholds (default, configurable later)**: a reservation cancellation is
  high-impact above a party-size threshold (default: parties of 6 or more); an inventory
  write-off is high-impact above a value/quantity threshold to be set per item category;
  any change to an already-published staff shift schedule is high-impact; routine,
  unpublished-schedule staffing changes and small inventory adjustments are not. Exact
  numeric thresholds are configurable and may be tuned after MVP without being a spec
  change.
- **Approval channel**: for MVP, proposed high-impact actions are reviewed and
  approved/rejected by the manager within the same interface used for conversation with
  the Orchestrator; separate out-of-band notification channels (e.g., SMS, email) are not
  required for MVP.
- **Memory retention**: memories persist indefinitely until explicitly corrected or
  removed by the manager; no automatic expiry is required for MVP.
- **Data availability**: baseline structured operational data (tables, menu/inventory
  catalog, staff roster) is assumed to exist or be seeded ahead of these workflows;
  bulk data migration from any prior system is out of scope for MVP.
- **Analytics depth**: Analytics Agent answers descriptive questions over existing
  structured data (covers, revenue, no-show rate, popular items); predictive/forecasting
  analytics are out of scope for MVP.
- **Concurrency**: MVP assumes a single manager interacting with the system at a time per
  restaurant; multi-user concurrent approval workflows are not required for MVP.
