"""V1 Cases router implementing Case CRUD and ownership validation via CaseService."""

from uuid import UUID
from fastapi import APIRouter, Header, Query, Response, status

from backend.app.api.deps import require_authenticated_caller
from backend.app.api.schemas.case import (
    CaseClaimResponse,
    CaseCreateRequest,
    CaseDetailResponse,
    CaseListResponse,
    CaseResponse,
    CaseUpdateRequest,
)
from backend.app.api.schemas.error import StandardErrorResponse
from backend.app.services.case_service import get_case_service, resolve_ownership_context
from backend.app.services.models import CallerContext

router = APIRouter(prefix="/cases", tags=["Cases"])


@router.post(
    "",
    response_model=CaseResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create Case",
    responses={
        400: {"model": StandardErrorResponse, "description": "Ambiguous ownership context."},
        401: {"model": StandardErrorResponse, "description": "Missing credentials."},
    },
)
def create_case(
    payload: CaseCreateRequest,
    authorization: str | None = Header(default=None, description="Bearer <supabase_jwt_token>"),
    x_guest_session_id: str | None = Header(default=None, description="Guest Session Token/ID"),
) -> CaseResponse:
    """Creates a new Case workspace for an authenticated user or guest session."""
    caller = resolve_ownership_context(authorization, x_guest_session_id)
    service = get_case_service()
    return service.create_case(payload, caller)


@router.get(
    "",
    response_model=CaseListResponse,
    summary="List Cases",
    responses={
        400: {"model": StandardErrorResponse, "description": "Ambiguous ownership context."},
        401: {"model": StandardErrorResponse, "description": "Missing credentials."},
    },
)
def list_cases(
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    authorization: str | None = Header(default=None, description="Bearer <supabase_jwt_token>"),
    x_guest_session_id: str | None = Header(default=None, description="Guest Session Token/ID"),
) -> CaseListResponse:
    """Lists all cases owned by the authenticated caller or guest session."""
    caller = resolve_ownership_context(authorization, x_guest_session_id)
    service = get_case_service()
    res = service.list_cases(caller)
    res.limit = limit
    res.offset = offset
    return res


@router.get("/{case_id}", response_model=CaseDetailResponse, summary="Get Case Details")
def get_case(
    case_id: UUID,
    authorization: str | None = Header(default=None, description="Bearer <supabase_jwt_token>"),
    x_guest_session_id: str | None = Header(default=None, description="Guest Session Token/ID"),
) -> CaseDetailResponse:
    """Retrieves full case details and associated documents overview."""
    caller = resolve_ownership_context(authorization, x_guest_session_id)
    service = get_case_service()
    return service.get_case(str(case_id), caller)


@router.patch("/{case_id}", response_model=CaseResponse, summary="Update Case")
def update_case(
    case_id: UUID,
    payload: CaseUpdateRequest,
    authorization: str | None = Header(default=None, description="Bearer <supabase_jwt_token>"),
    x_guest_session_id: str | None = Header(default=None, description="Guest Session Token/ID"),
) -> CaseResponse:
    """Updates case title or retention settings."""
    caller = resolve_ownership_context(authorization, x_guest_session_id)
    service = get_case_service()
    return service.update_case(str(case_id), payload, caller)


@router.delete("/{case_id}", status_code=status.HTTP_204_NO_CONTENT, summary="Delete Case")
def delete_case(
    case_id: UUID,
    authorization: str | None = Header(default=None, description="Bearer <supabase_jwt_token>"),
    x_guest_session_id: str | None = Header(default=None, description="Guest Session Token/ID"),
) -> Response:
    """Deletes case and explicitly cleans up storage objects and database records."""
    caller = resolve_ownership_context(authorization, x_guest_session_id)
    service = get_case_service()
    service.delete_case(str(case_id), caller)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post(
    "/{case_id}/claim",
    response_model=CaseClaimResponse,
    status_code=status.HTTP_200_OK,
    summary="Claim Guest Case to Authenticated User",
    responses={
        400: {"model": StandardErrorResponse, "description": "Missing guest credentials header."},
        401: {"model": StandardErrorResponse, "description": "Unauthorized or invalid credentials."},
        404: {"model": StandardErrorResponse, "description": "Case not found."},
        409: {"model": StandardErrorResponse, "description": "Case already claimed or guest ownership mismatch."},
    },
)
def claim_case(
    case_id: UUID,
    authorization: str | None = Header(default=None, description="Bearer <supabase_jwt_token>"),
    x_guest_session_id: str | None = Header(default=None, description="Guest Session Token/ID"),
) -> CaseClaimResponse:
    """Atomically transfers ownership of a temporary guest case to the authenticated caller.

    Requires:
    - Authorization Bearer JWT (verified Supabase access token)
    - X-Guest-Session-ID (valid unexpired guest credential token)
    - Target case must belong to the guest session
    - Target case must not already be claimed by another user
    """
    caller = require_authenticated_caller(authorization)
    service = get_case_service()
    return service.claim_case(
        case_id=str(case_id),
        caller=caller,
        guest_token=x_guest_session_id or "",
    )
