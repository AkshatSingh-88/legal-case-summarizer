"""V1 Guest router skeleton."""

from datetime import datetime, timezone
import secrets
from uuid import UUID, uuid4
from fastapi import APIRouter, Header, HTTPException, status

from backend.app.api.schemas.error import StandardErrorResponse
from backend.app.api.schemas.guest import (
    GuestClaimResponse,
    GuestSessionResponse,
)

router = APIRouter(prefix="/guest", tags=["Guest"])


@router.post(
    "/sessions",
    response_model=GuestSessionResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create Guest Session",
)
def create_guest_session() -> GuestSessionResponse:
    """Initializes a new temporary guest session with high-entropy credential token and server-side activity/expiry tracking."""
    now = datetime.now(timezone.utc)
    # Generate high-entropy, cryptographically secure opaque credential token
    token = f"gst_{secrets.token_urlsafe(32)}"
    return GuestSessionResponse(
        guest_session_id=uuid4(),
        session_token=token,
        last_activity_at=now,
        expires_at=now,
        created_at=now,
    )


@router.post(
    "/claim",
    response_model=GuestClaimResponse,
    summary="Claim Guest Cases to Authenticated User",
    responses={
        400: {"model": StandardErrorResponse, "description": "Missing guest credential header."},
        401: {"model": StandardErrorResponse, "description": "Missing or invalid authenticated JWT."},
    },
)
def claim_guest_cases(
    authorization: str | None = Header(default=None, description="Bearer <supabase_jwt_token>"),
    x_guest_session_id: str | None = Header(default=None, description="Guest Session Token/ID"),
) -> GuestClaimResponse:
    """Transfers ownership of all cases created under a guest session to the authenticated user."""
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={
                "error": {
                    "code": "UNAUTHORIZED",
                    "message": "Valid Authorization Bearer JWT is required to claim guest cases.",
                    "details": None,
                }
            },
        )
    if not x_guest_session_id or not x_guest_session_id.strip():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "error": {
                    "code": "MISSING_GUEST_CREDENTIAL",
                    "message": "X-Guest-Session-ID header is required to claim guest cases.",
                    "details": None,
                }
            },
        )

    now = datetime.now(timezone.utc)
    return GuestClaimResponse(
        claimed_case_ids=[UUID("c8f3b174-8b6b-4e12-8821-49fa5cf10321")],
        claimed_count=1,
        user_id=UUID("7a3068f8-3e4b-47e1-8848-3606fbfd7541"),
        claimed_at=now,
    )
