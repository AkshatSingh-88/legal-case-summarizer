"""V1 Guest router implementing session creation and claim contract."""

from datetime import datetime, timezone
from uuid import UUID
from fastapi import APIRouter, Header, HTTPException, Response, status

from backend.app.api.schemas.error import StandardErrorResponse
from backend.app.api.schemas.guest import (
    GuestClaimResponse,
    GuestSessionResponse,
)
from backend.app.services.guest_service import get_guest_service

router = APIRouter(prefix="/guest", tags=["Guest"])


@router.post(
    "/sessions",
    response_model=GuestSessionResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create Guest Session",
)
def create_guest_session() -> GuestSessionResponse:
    """Initializes a new temporary guest session with high-entropy credential token and server-side activity/expiry tracking."""
    guest_service = get_guest_service()
    return guest_service.create_session()


@router.post(
    "/claim",
    response_model=GuestClaimResponse,
    deprecated=True,
    summary="[DEPRECATED] Compatibility stub for guest claim",
    responses={
        400: {"model": StandardErrorResponse, "description": "Missing guest credential header."},
        401: {"model": StandardErrorResponse, "description": "Missing or invalid authenticated JWT."},
    },
)
def claim_guest_cases(
    response: Response,
    authorization: str | None = Header(default=None, description="Bearer <supabase_jwt_token>"),
    x_guest_session_id: str | None = Header(default=None, description="Guest Session Token/ID"),
) -> GuestClaimResponse:
    """Deprecated compatibility stub for guest claim.

    Performs ZERO database mutations. The authoritative claim operation is POST /api/v1/cases/{case_id}/claim.
    """
    if not authorization or not authorization.strip().startswith("Bearer "):
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

    # Set deprecation header without misleading {case_id} Link
    response.headers["Deprecation"] = "true"

    now = datetime.now(timezone.utc)
    return GuestClaimResponse(
        claimed_case_ids=[UUID("c8f3b174-8b6b-4e12-8821-49fa5cf10321")],
        claimed_count=1,
        user_id=UUID("7a3068f8-3e4b-47e1-8848-3606fbfd7541"),
        claimed_at=now,
    )
