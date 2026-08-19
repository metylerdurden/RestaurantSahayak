"""FastAPI app factory. Uvicorn target: `app.main:app`."""

from __future__ import annotations

import uuid
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from opentelemetry import trace

from app.api.routes.agent_runs import router as agent_runs_router
from app.api.routes.approvals import router as approvals_router
from app.api.routes.customers import router as customers_router
from app.api.routes.dashboard import router as dashboard_router
from app.api.routes.health import router as health_router
from app.api.routes.inventory import router as inventory_router
from app.api.routes.manager_workflows import router as manager_workflows_router
from app.api.routes.reservations import router as reservations_router
from app.api.routes.restaurants import router as restaurants_router
from app.api.routes.workflows import router as workflows_router
from app.core.config import get_settings
from app.core.db import dispose_engine, get_engine
from app.core.logging import bind_correlation_id, configure_logging, get_logger
from app.core.telemetry import configure_telemetry, instrument_fastapi, instrument_sqlalchemy

STATIC_DIR = Path(__file__).parent / "static"


@asynccontextmanager
async def lifespan(app: FastAPI):  # noqa: ANN201, ARG001
    settings = get_settings()
    configure_logging(settings)
    configure_telemetry(settings)
    instrument_sqlalchemy(get_engine(settings))
    logger = get_logger(__name__)
    logger.info(
        "app_startup",
        environment=settings.environment,
        llm_provider=settings.llm_provider,
        llm_model=settings.llm_model,
        embedding_provider=settings.embedding_provider,
        embedding_model=settings.embedding_model,
    )
    yield
    await dispose_engine()
    # Deliberately does NOT call shutdown_telemetry() here — see its docstring.
    # The provider this process actually configured (if any) already registered
    # its own atexit flush; create_app() can run more than once per process.
    logger.info("app_shutdown")


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(title="DineOps", version="0.1.0", lifespan=lifespan)
    instrument_fastapi(app)

    @app.middleware("http")
    async def correlation_id_middleware(request: Request, call_next):  # noqa: ANN001, ANN202
        incoming = request.headers.get("X-Correlation-Id")
        correlation_id = bind_correlation_id(incoming or uuid.uuid4().hex)
        trace.get_current_span().set_attribute("correlation_id", correlation_id)
        response = await call_next(request)
        response.headers["X-Correlation-Id"] = correlation_id
        return response

    @app.exception_handler(Exception)
    async def unhandled_exception_handler(request: Request, exc: Exception):  # noqa: ANN001, ANN202, ARG001
        logger = get_logger(__name__)
        logger.error("unhandled_exception", error=str(exc), error_type=type(exc).__name__)
        return JSONResponse(
            status_code=500,
            content={"error": {"code": "internal_error", "message": "An unexpected error occurred."}},
        )

    app.include_router(health_router)
    app.include_router(workflows_router)
    app.include_router(dashboard_router)
    app.include_router(agent_runs_router)
    app.include_router(reservations_router)
    app.include_router(inventory_router)
    app.include_router(customers_router)
    app.include_router(approvals_router)
    app.include_router(manager_workflows_router)
    app.include_router(restaurants_router)

    # The manager dashboard (Step 19): a small static single-page app, no build
    # step. Mounted last and at "/" so it never shadows an API route registered
    # above — Starlette matches routes in registration order, and `html=True`
    # serves static/index.html for "/" and any unmatched sub-path.
    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
    app.mount("/", StaticFiles(directory=STATIC_DIR, html=True), name="dashboard")

    return app


app = create_app()
