"""Supabase Auth service wrapping GoTrue user operations."""

from datetime import datetime, timezone
import logging
from typing import Any
import uuid

from fastapi import HTTPException, status

from backend.app.api.schemas.auth import (
    AuthSessionResponse,
    PasswordResetResponse,
    UserProfileResponse,
    UserLoginRequest,
    UserRegisterRequest,
)
from backend.app.config import get_settings
from backend.app.core.supabase import get_supabase_client

logger = logging.getLogger(__name__)


def _parse_dt(val: Any) -> datetime:
    if isinstance(val, datetime):
        return val
    if isinstance(val, str):
        v = val.replace("Z", "+00:00")
        try:
            return datetime.fromisoformat(v)
        except Exception:
            pass
    return datetime.now(timezone.utc)


class AuthService:
    """Encapsulates Supabase Auth operations using the official client's gotrue auth module."""

    def __init__(self, client: Any = None) -> None:
        self._client = client

    @property
    def client(self) -> Any:
        if self._client is not None:
            return self._client
        return get_supabase_client()

    def _require_client(self) -> Any:
        client = self.client
        if client is None:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail={
                    "error": {
                        "code": "AUTH_UNAVAILABLE",
                        "message": "Authentication service is unavailable or unconfigured.",
                        "details": {},
                    }
                },
            )
        return client

    def register(self, req: UserRegisterRequest) -> AuthSessionResponse:
        """Signs up a new user with email and password via Supabase Auth."""
        client = self._require_client()
        try:
            res = client.auth.sign_up({"email": req.email, "password": req.password})
        except Exception as e:
            logger.warning("Supabase registration failed for %s: %s", req.email, e)
            err_str = str(e).lower()
            if "already registered" in err_str or "user already exists" in err_str:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail={
                        "error": {
                            "code": "USER_ALREADY_EXISTS",
                            "message": "A user with this email address already exists.",
                            "details": {},
                        }
                    },
                )
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail={
                    "error": {
                        "code": "REGISTRATION_FAILED",
                        "message": f"Registration failed: {str(e)}",
                        "details": {},
                    }
                },
            )

        user = getattr(res, "user", None) or (res.get("user") if isinstance(res, dict) else None)
        session = getattr(res, "session", None) or (res.get("session") if isinstance(res, dict) else None)

        if not user:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail={
                    "error": {
                        "code": "REGISTRATION_ERROR",
                        "message": "User record was not created.",
                        "details": {},
                    }
                },
            )

        user_id = getattr(user, "id", None) or user.get("id")
        email = getattr(user, "email", None) or user.get("email") or req.email
        created_at = getattr(user, "created_at", None) or user.get("created_at")

        user_profile = UserProfileResponse(
            id=uuid.UUID(str(user_id)),
            email=str(email),
            role="authenticated",
            created_at=_parse_dt(created_at),
        )

        access_token = getattr(session, "access_token", None) if session else None
        expires_in = getattr(session, "expires_in", 3600) if session else 3600
        refresh_token = getattr(session, "refresh_token", None) if session else None

        # If email confirmation is required, session may be None
        if not access_token:
            access_token = ""
            expires_in = 0

        return AuthSessionResponse(
            access_token=access_token,
            token_type="bearer",
            expires_in=expires_in,
            refresh_token=refresh_token,
            user=user_profile,
        )

    def login(self, req: UserLoginRequest) -> AuthSessionResponse:
        """Authenticates user with email and password, returning session access and refresh tokens."""
        client = self._require_client()
        try:
            res = client.auth.sign_in_with_password({"email": req.email, "password": req.password})
        except Exception as e:
            logger.warning("Supabase login failed for %s: %s", req.email, e)
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail={
                    "error": {
                        "code": "INVALID_CREDENTIALS",
                        "message": "Invalid email or password.",
                        "details": {},
                    }
                },
            )

        user = getattr(res, "user", None) or (res.get("user") if isinstance(res, dict) else None)
        session = getattr(res, "session", None) or (res.get("session") if isinstance(res, dict) else None)

        if not session or not user:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail={
                    "error": {
                        "code": "INVALID_CREDENTIALS",
                        "message": "Authentication failed to produce a valid session.",
                        "details": {},
                    }
                },
            )

        user_id = getattr(user, "id", None) or user.get("id")
        email = getattr(user, "email", None) or user.get("email") or req.email
        created_at = getattr(user, "created_at", None) or user.get("created_at")

        user_profile = UserProfileResponse(
            id=uuid.UUID(str(user_id)),
            email=str(email),
            role="authenticated",
            created_at=_parse_dt(created_at),
        )

        return AuthSessionResponse(
            access_token=getattr(session, "access_token", ""),
            token_type="bearer",
            expires_in=getattr(session, "expires_in", 3600),
            refresh_token=getattr(session, "refresh_token", None),
            user=user_profile,
        )

    def reset_password(self, email: str) -> PasswordResetResponse:
        """Triggers a password recovery email for the provided email address."""
        client = self._require_client()
        try:
            client.auth.reset_password_for_email(email)
        except Exception as e:
            logger.warning("Supabase reset_password failed for %s: %s", email, e)
            # Always return success to prevent email enumeration
        return PasswordResetResponse()


_auth_service = AuthService()


def get_auth_service(client: Any = None) -> AuthService:
    if client is not None:
        return AuthService(client)
    return _auth_service
