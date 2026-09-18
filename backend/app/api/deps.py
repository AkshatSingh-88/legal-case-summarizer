"""FastAPI dependencies for authentication, caller context, and route authorization."""

from typing import Any
from fastapi import Header, HTTPException, status

from backend.app.core.auth import get_jwt_verifier
from backend.app.services.guest_service import get_guest_service
from backend.app.services.models import CallerContext


def get_caller_context(
    authorization: str | None = Header(default=None, description="Bearer <supabase_jwt_token>"),
    x_guest_session_id: str | None = Header(default=None, description="Guest Session Token/ID"),
    client: Any = None,
) -> CallerContext:
    """FastAPI dependency resolving unified CallerContext.

    Enforces mutual exclusion:
    - Both credentials present -> 400 Bad Request (AMBIGUOUS_OWNERSHIP_CONTEXT)
    - Neither credential present -> 401 Unauthorized (UNAUTHORIZED)
    - Bearer JWT -> Cryptographically verified via SupabaseJWTVerifier -> CallerContext(role="authenticated")
    - X-Guest-Session-ID -> Validated via GuestService -> CallerContext(role="guest")
    """
    auth_str = authorization if isinstance(authorization, str) else None
    guest_str = x_guest_session_id if isinstance(x_guest_session_id, str) else None

    has_jwt = bool(auth_str and auth_str.strip().startswith("Bearer "))
    has_guest = bool(guest_str and guest_str.strip())

    if has_jwt and has_guest:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "error": {
                    "code": "AMBIGUOUS_OWNERSHIP_CONTEXT",
                    "message": "Ambiguous ownership context. Both Authorization header and X-Guest-Session-ID were provided.",
                    "details": {},
                }
            },
        )

    if not has_jwt and not has_guest:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={
                "error": {
                    "code": "UNAUTHORIZED",
                    "message": "Authentication required. Provide either a Bearer JWT or an X-Guest-Session-ID header.",
                    "details": {},
                }
            },
        )

    if has_jwt:
        token = auth_str.strip().split(" ", 1)[1].strip() if auth_str else ""
        verifier = get_jwt_verifier()
        payload = verifier.verify_token(token)
        user_id = str(payload.get("sub"))
        return CallerContext(user_id=user_id, guest_session_id=None, role="authenticated")

    if has_guest:
        guest_service = get_guest_service(client)
        session = guest_service.validate_session(guest_str or "")
        if not session:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail={
                    "error": {
                        "code": "UNAUTHORIZED",
                        "message": "Invalid or expired guest session credential.",
                        "details": {},
                    }
                },
            )
        return CallerContext(
            user_id=None,
            guest_session_id=str(session["id"]),
            role="guest",
        )

    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail={"error": {"code": "UNAUTHORIZED", "message": "Unauthorized", "details": {}}},
    )


def require_authenticated_caller(
    authorization: str | None = Header(default=None, description="Bearer <supabase_jwt_token>"),
) -> CallerContext:
    """FastAPI dependency requiring a strictly authenticated caller."""
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
    user_id = str(payload.get("sub"))
    return CallerContext(user_id=user_id, guest_session_id=None, role="authenticated")
