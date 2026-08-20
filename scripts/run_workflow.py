"""Manually trigger one autonomous background workflow immediately, for developer
testing — the CLI counterpart to POST /workflows/{workflow_type}/trigger.

Usage:
    uv run python -m scripts.run_workflow daily_briefing
    uv run python -m scripts.run_workflow inventory_monitoring --restaurant-id <uuid>

If --restaurant-id is omitted, the first Restaurant row found is used (convenient
against a freshly `scripts/seed.py`-ed dev database, which has exactly one).
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import uuid

from sqlalchemy import select

from app.api.stack import build_agent_stack
from app.core.config import get_settings
from app.core.db import get_engine, get_session_factory
from app.core.logging import configure_logging
from app.core.telemetry import configure_telemetry, instrument_sqlalchemy, shutdown_telemetry
from app.models import Restaurant
from app.models.workflow_run import WORKFLOW_TYPES


async def _run(workflow_type: str, restaurant_id: uuid.UUID | None) -> None:
    session_factory = get_session_factory()
    async with session_factory() as session:
        if restaurant_id is None:
            restaurant = (await session.execute(select(Restaurant).limit(1))).scalar_one_or_none()
            if restaurant is None:
                print("No restaurant found — seed one first (uv run python -m scripts.seed) or pass --restaurant-id.")
                sys.exit(1)
            restaurant_id = restaurant.id

        stack = build_agent_stack(session)
        workflow = stack.background_workflows[workflow_type]
        run = await workflow.run(restaurant_id=restaurant_id, triggered_by="manual")
        await session.commit()

        print(f"workflow_run_id: {run.id}")
        print(f"status:          {run.status}")
        print(f"started_at:      {run.started_at}")
        print(f"completed_at:    {run.completed_at}")
        if run.error:
            print(f"error:           {run.error}")
        print("final_result:")
        print(json.dumps(run.final_result, indent=2, default=str))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("workflow_type", choices=WORKFLOW_TYPES)
    parser.add_argument("--restaurant-id", type=uuid.UUID, default=None)
    args = parser.parse_args()

    settings = get_settings()
    configure_logging(settings)
    configure_telemetry(settings)
    instrument_sqlalchemy(get_engine(settings))
    try:
        asyncio.run(_run(args.workflow_type, args.restaurant_id))
    finally:
        shutdown_telemetry()


if __name__ == "__main__":
    main()
