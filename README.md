# DineOps

Multi-agent AI restaurant operations platform. An Orchestrator Agent coordinates
specialized agents for reservations, inventory, customers, staffing, and analytics.

This project is agent-first: agents act on the system exclusively through typed tools,
never through direct SQL or document retrieval. See
[`.specify/memory/constitution.md`](.specify/memory/constitution.md) for the
non-negotiable principles (no RAG, custom MemoryService, human approval for high-impact
actions, tech stack).

## Status

Project structure and Spec Kit workflow are set up. No agents, tools, or infrastructure
have been implemented yet — that begins once the first feature spec and plan exist.

## Planned stack

- Python + FastAPI (backend)
- PostgreSQL (primary database)
- LangGraph (agent orchestration)
- Docker / Docker Compose (local infrastructure)

## Manager Dashboard

A small manager-facing dashboard (`app/static/`, no build step) is served at `/` by
the same FastAPI app — dashboard, reservations, inventory, customers, approvals, an
Agent Activity view (trigger the orchestrator/a specialist and watch its trace:
Manager Request → Orchestrator → Specialist Agent → Tool → Memory → Result), and
manual workflow triggers. Backed by `GET/POST /api/v1/*` (see `app/api/routes/`).
Run the API (`uv run uvicorn app.main:app --reload`) and open http://localhost:8000/.

## Observability

Every agent run, tool call, LLM call, memory operation, event, approval decision, and
background workflow run is traced with OpenTelemetry, and every log line is tagged
with a correlation id (`app/core/logging.py`, `app/core/telemetry.py`).

By default (`OTEL_EXPORTER=console`) traces print to stdout — no extra infrastructure
needed. To browse them in a UI instead:

```
docker compose -f docker/docker-compose.observability.yml up -d
# then in .env: OTEL_EXPORTER=otlp, OTEL_EXPORTER_OTLP_ENDPOINT=http://localhost:4318
```

Open http://localhost:16686 (Jaeger) once the app or a workflow has run.

## Workflow

This repo uses [Spec Kit](https://github.com/github/spec-kit) for spec-driven
development:

```
/speckit-constitution   # already established, see .specify/memory/constitution.md
/speckit-specify        # create a feature spec
/speckit-clarify        # (optional) de-risk ambiguous areas
/speckit-plan           # implementation plan
/speckit-tasks          # actionable task breakdown
/speckit-analyze        # (optional) cross-artifact consistency check
/speckit-implement      # execute
```
