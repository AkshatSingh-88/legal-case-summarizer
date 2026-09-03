"""Pydantic schemas for Guest sessions and claiming."""

from datetime import datetime
from uuid import UUID
from pydantic import BaseModel, ConfigDict, Field


class GuestSessionResponse(BaseModel):
    model_config = ConfigDict(extra="ignore")

    guest_session_id: UUID
    session_token: str = Field(..., description="High-entropy, cryptographically secure guest credential token")
    last_activity_at: datetime
    expires_at: datetime
    created_at: datetime


class GuestClaimResponse(BaseModel):
    model_config = ConfigDict(extra="ignore")

    claimed_case_ids: list[UUID] = Field(default_factory=list)
    claimed_count: int = 0
    user_id: UUID
    claimed_at: datetime
