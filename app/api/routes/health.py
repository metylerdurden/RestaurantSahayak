"""Liveness/readiness endpoints.

GET /health/live  — process is up. Always 200 once the app has started.
GET /health/ready — dependencies DineOps needs for ordinary agent-request traffic are
                    reachable: the database, and the Ollama LLM server (a cheap
                    tag-list check, not a full generation). The embedding model is
                    deliberately NOT checked here — loading BGE-M3 takes real time and
                    memory, which would make a readiness probe expensive; use
                    `scripts/verify_providers.py` to check it explicitly.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import LLMProvider, get_db_session, get_llm_provider

router = APIRouter(prefix="/health", tags=["health"])


@router.get("/live")
async def live() -> dict[str, str]:
    return {"status": "ok"}


@router.get("/ready", response_model=None)
async def ready(
    session: AsyncSession = Depends(get_db_session),
    llm: LLMProvider = Depends(get_llm_provider),
) -> dict | JSONResponse:
    checks: dict[str, bool] = {}

    try:
        await session.execute(text("SELECT 1"))
        checks["database"] = True
    except Exception:
        checks["database"] = False

    checks["llm"] = await llm.health_check()

    all_ok = all(checks.values())
    body = {"status": "ok" if all_ok else "degraded", "checks": checks}
    if not all_ok:
        return JSONResponse(status_code=503, content=body)
    return body
