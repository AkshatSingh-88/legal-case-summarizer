"""V1 Auth router skeleton."""

from datetime import datetime, timezone
from uuid import UUID
from fastapi import APIRouter, Header, HTTPException, status

from backend.app.api.schemas.auth import UserProfileResponse

router = APIRouter(prefix="/auth", tags=["Auth"])


@router.get("/me", response_model=UserProfileResponse, summary="Get Current User Profile")
def get_current_user(
    authorization: str | None = Header(default=None, description="Bearer <supabase_jwt_token>"),
) -> UserProfileResponse:
    """Resolves authenticated user profile from verified Supabase JWT.
    
    In Phase A contract skeleton, returns a typed placeholder schema. Real Supabase JWT validation is implemented in a later phase.
    """
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing or invalid Authorization Bearer header",
        )

    # Skeleton placeholder response
    return UserProfileResponse(
        id=UUID("7a3068f8-3e4b-47e1-8848-3606fbfd7541"),
        email="lawyer@example.com",
        role="authenticated",
        created_at=datetime.now(timezone.utc),
    )
