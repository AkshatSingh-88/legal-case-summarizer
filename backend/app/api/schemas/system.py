"""Pydantic schemas for system health and diagnostics."""

from datetime import datetime
from pydantic import BaseModel, ConfigDict


class SystemHealthResponse(BaseModel):
    model_config = ConfigDict(extra="ignore")

    status: str
    version: str
    environment: str
    timestamp: datetime
