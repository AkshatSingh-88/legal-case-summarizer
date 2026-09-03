"""Pydantic schemas for authentication and identity."""

from datetime import datetime
from uuid import UUID
from pydantic import BaseModel, ConfigDict


class UserProfileResponse(BaseModel):
    model_config = ConfigDict(extra="ignore")

    id: UUID
    email: str
    role: str = "authenticated"
    created_at: datetime

