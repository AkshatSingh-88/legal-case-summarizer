"""Standardized error response schemas."""

from typing import Any
from pydantic import BaseModel, ConfigDict, Field


class ErrorDetail(BaseModel):
    model_config = ConfigDict(extra="ignore")

    code: str
    message: str
    details: dict[str, Any] | None = None


class StandardErrorResponse(BaseModel):
    model_config = ConfigDict(extra="ignore")

    error: ErrorDetail
