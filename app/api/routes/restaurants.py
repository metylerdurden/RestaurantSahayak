"""Not one of Step 19's explicitly listed endpoints, but necessary plumbing for the
dashboard to be usable at all: every other Manager API endpoint is scoped by
`restaurant_id`, and with no auth system in this MVP (see app.models.user.User's own
docstring) there's no session to derive it from. This lets the frontend populate a
restaurant picker instead of requiring a manager to paste a UUID by hand."""

from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_db_session
from app.repositories.restaurant_repo import RestaurantRepository
from app.schemas.restaurant import RestaurantDTO

router = APIRouter(prefix="/api/v1", tags=["restaurants"])


@router.get("/restaurants", response_model=list[RestaurantDTO])
async def list_restaurants(session: AsyncSession = Depends(get_db_session)) -> list[RestaurantDTO]:
    restaurants = await RestaurantRepository(session).list_all()
    return [RestaurantDTO.model_validate(r) for r in restaurants]
