"""Pydantic schemas for authentication and identity."""

from datetime import datetime
import re
from uuid import UUID
from pydantic import BaseModel, ConfigDict, Field, field_validator


def _validate_email(v: str) -> str:
    cleaned = v.strip().lower()
    if not re.match(r"^[^@\s]+@[^@\s]+\.[^@\s]+$", cleaned):
        raise ValueError("Invalid email format")
    return cleaned


class UserRegisterRequest(BaseModel):
    model_config = ConfigDict(extra="ignore")

    email: str = Field(..., min_length=3, max_length=255)
    password: str = Field(..., min_length=8, description="User password (minimum 8 characters)")

    @field_validator("email")
    @classmethod
    def check_email(cls, v: str) -> str:
        return _validate_email(v)


class UserLoginRequest(BaseModel):
    model_config = ConfigDict(extra="ignore")

    email: str = Field(..., min_length=3, max_length=255)
    password: str = Field(..., min_length=1)

    @field_validator("email")
    @classmethod
    def check_email(cls, v: str) -> str:
        return _validate_email(v)


class UserProfileResponse(BaseModel):
    model_config = ConfigDict(extra="ignore")

    id: UUID
    email: str
    role: str = "authenticated"
    created_at: datetime


class AuthSessionResponse(BaseModel):
    model_config = ConfigDict(extra="ignore")

    access_token: str
    token_type: str = "bearer"
    expires_in: int
    refresh_token: str | None = None
    user: UserProfileResponse


class PasswordResetRequest(BaseModel):
    model_config = ConfigDict(extra="ignore")

    email: str = Field(..., min_length=3, max_length=255)

    @field_validator("email")
    @classmethod
    def check_email(cls, v: str) -> str:
        return _validate_email(v)


class PasswordResetResponse(BaseModel):
    model_config = ConfigDict(extra="ignore")

    message: str = "If the email is registered, password reset instructions have been sent."
