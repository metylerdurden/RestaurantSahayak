# DineOps Constitution

## Core Principles

### I. Agent-First, Tool-Mediated Access
DineOps is an agent-first platform. All application behavior — reservations, inventory,
customers, staffing, analytics — is reached by agents exclusively through typed tools
(explicit input/output schemas, one tool = one well-defined capability). Agents MUST NOT
execute SQL directly, construct raw queries, or receive an open database connection.
Every read or write path an agent can take is a named, reviewable, independently testable
tool function. If a capability doesn't exist as a tool, an agent cannot perform it.

### II. No RAG, No Document Pipelines (NON-NEGOTIABLE)
This project does not implement retrieval-augmented generation. There is no document
ingestion, no chunking, no embedding-based document retrieval, and no vector search over
unstructured documents. Agents reason over structured application state (via typed tools)
and over structured memories (via the MemoryService), not over a document corpus. Any
future proposal resembling RAG requires an explicit constitution amendment before any code
is written.

### III. Custom Persistent MemoryService
Long-term agent memory is a first-class subsystem, not an afterthought bolted onto a
vector store. It is our own implementation (design inspired by Mem0, not a dependency on
it) exposed to agents as typed tools, not as free-form retrieval. Memory writes and reads
are structured, scoped (e.g., per-customer, per-agent, per-conversation), and auditable.
No agent or tool bypasses MemoryService to persist state elsewhere for the purpose of
"remembering."

### IV. Human Approval for High-Impact Actions
Actions with material real-world consequence (e.g., cancellations affecting revenue,
large inventory writes, staffing changes, anything irreversible or costly to reverse) MUST
be gated behind human approval before execution. This gate is a first-class part of the
tool/orchestration contract, not a UI afterthought — tools that perform high-impact
actions are designed from the start to support a "propose → approve → execute" flow, even
before the approval UI itself is built.

### V. Orchestrator + Specialized Agents
The system is composed of an Orchestrator Agent and specialized agents (reservations,
inventory, customers, staffing, analytics), coordinated via LangGraph. Each specialized
agent owns a narrow domain and its own tool set; the Orchestrator routes and composes, it
does not reimplement domain logic. Cross-domain work happens through explicit
orchestration, not by giving one agent another domain's tools.

### VI. Typed Contracts Everywhere
Tool inputs/outputs, MemoryService records, and inter-agent messages are typed and
validated (e.g., Pydantic models), not loose dicts or free-text protocols. Types are the
source of truth for what an agent can request and what it will get back.

## Technology Stack Constraints

- **Backend**: Python + FastAPI.
- **Database**: PostgreSQL is the primary and only system-of-record database. All access
  from agents goes through typed tools that call into a service/repository layer — never
  raw SQL from agent-facing code.
- **Orchestration**: LangGraph coordinates the Orchestrator Agent and specialized agents.
- **Local infrastructure**: Docker (and Docker Compose) for local development
  infrastructure (Postgres, and any supporting services).
- **No RAG-adjacent infrastructure**: no vector database, no document store, no chunking
  library is to be introduced as part of the core platform (see Principle II).

## Development Workflow

- This project follows Spec Kit's spec-driven workflow: `/speckit-specify` →
  (optional `/speckit-clarify`) → `/speckit-plan` → `/speckit-tasks` →
  (optional `/speckit-analyze` / `/speckit-checklist`) → `/speckit-implement`.
- Agent implementation (Orchestrator Agent, specialized agents, LangGraph graphs) does not
  begin until specs and plans for that scope exist and have been reviewed.
- New tools are specified (name, purpose, input/output schema, side effects, whether they
  are high-impact/approval-gated) before they are implemented.
- Infrastructure (Docker Compose services, Postgres schema/migrations) is planned as part
  of the relevant feature's plan, not improvised during implementation.

## Governance

This constitution supersedes ad-hoc practice for DineOps. Any change to Principles I–VI
(agent-first tool access, no RAG, custom MemoryService, human approval for high-impact
actions, orchestrator/specialized-agent structure, typed contracts) requires an explicit
amendment to this document with a stated rationale before implementation proceeds. All
specs and plans MUST be checked against this constitution; a plan that conflicts with it
must either be revised or the constitution amended first — implementation never silently
overrides it.

**Version**: 1.0.0 | **Ratified**: 2026-08-19 | **Last Amended**: 2026-08-19
