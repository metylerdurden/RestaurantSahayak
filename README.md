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
