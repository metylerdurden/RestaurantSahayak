from __future__ import annotations

import uuid

from pydantic import BaseModel, ConfigDict


class RestaurantDTO(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    timezone: str
