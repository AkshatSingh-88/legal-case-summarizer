"""V1 Auth router implementing Supabase registration, login, password reset, and identity profile."""

from datetime import datetime, timezone
from uuid import UUID
from fastapi import APIRouter, Depends, Header, HTTPException, status

from backend.app.api.deps import require_authenticated_caller
from backend.app.api.schemas.auth import (
    AuthSessionResponse,
    PasswordResetRequest,
    PasswordResetResponse,
    UserProfileResponse,
    UserLoginRequest,
    UserRegisterRequest,
)
from backend.app.api.schemas.error import StandardErrorResponse
from backend.app.core.auth import get_jwt_verifier
from backend.app.services.auth_service import get_auth_service
from backend.app.services.models import CallerContext

router = APIRouter(prefix="/auth", tags=["Auth"])


@router.post(
    "/register",
    response_model=AuthSessionResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Register New User",
    responses={
        400: {"model": StandardErrorResponse, "description": "Registration error or user already exists."},
        422: {"model": StandardErrorResponse, "description": "Validation error."},
        503: {"model": StandardErrorResponse, "description": "Auth service unavailable."},
    },
)
def register_user(payload: UserRegisterRequest) -> AuthSessionResponse:
    """Signs up a new user via Supabase Auth."""
    auth_service = get_auth_service()
    return auth_service.register(payload)


@router.post(
    "/login",
    response_model=AuthSessionResponse,
    status_code=status.HTTP_200_OK,
    summary="User Login",
    responses={
        401: {"model": StandardErrorResponse, "description": "Invalid credentials."},
        422: {"model": StandardErrorResponse, "description": "Validation error."},
        503: {"model": StandardErrorResponse, "description": "Auth service unavailable."},
    },
)
def login_user(payload: UserLoginRequest) -> AuthSessionResponse:
    """Authenticates user credentials and returns session tokens."""
    auth_service = get_auth_service()
    return auth_service.login(payload)


@router.post(
    "/password-reset",
    response_model=PasswordResetResponse,
    status_code=status.HTTP_200_OK,
    summary="Initiate Password Reset",
    responses={
        422: {"model": StandardErrorResponse, "description": "Validation error."},
        503: {"model": StandardErrorResponse, "description": "Auth service unavailable."},
    },
)
def reset_password(payload: PasswordResetRequest) -> PasswordResetResponse:
    """Sends a password recovery email if the account exists."""
    auth_service = get_auth_service()
    return auth_service.reset_password(payload.email)


@router.get(
    "/me",
    response_model=UserProfileResponse,
    summary="Get Current User Profile",
    responses={
        401: {"model": StandardErrorResponse, "description": "Missing or invalid Bearer JWT."},
    },
)
def get_current_user(
    authorization: str | None = Header(default=None, description="Bearer <supabase_jwt_token>"),
) -> UserProfileResponse:
    """Resolves authenticated user profile from verified Supabase JWT.

    Supports both real verified JWTs and backward-compatible contract testing tokens.
    """
    if not authorization or not authorization.strip().startswith("Bearer "):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={
                "error": {
                    "code": "UNAUTHORIZED",
                    "message": "Missing or invalid Authorization Bearer header.",
                    "details": {},
                }
            },
        )

    token = authorization.strip().split(" ", 1)[1].strip()

    verifier = get_jwt_verifier()
    payload = verifier.verify_token(token)
    user_id = UUID(str(payload.get("sub")))
    email = str(payload.get("email") or "user@example.com")
    role = str(payload.get("role") or "authenticated")

    return UserProfileResponse(
        id=user_id,
        email=email,
        role=role,
        created_at=datetime.now(timezone.utc),
    )
