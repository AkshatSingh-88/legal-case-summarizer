"""V1 Cases router skeleton."""

from datetime import datetime, timezone
from uuid import UUID, uuid4
from fastapi import APIRouter, Header, HTTPException, Query, Response, status
from fastapi.responses import JSONResponse

from backend.app.api.schemas.case import (
    CaseCreateRequest,
    CaseDetailDocumentItem,
    CaseDetailResponse,
    CaseListResponse,
    CaseResponse,
    CaseStatus,
    CaseUpdateRequest,
    RetentionType,
)
from backend.app.api.schemas.error import StandardErrorResponse

router = APIRouter(prefix="/cases", tags=["Cases"])


def _resolve_ownership_context(authorization: str | None, x_guest_session_id: str | None) -> tuple[str, bool]:
    """Validates ownership credential headers according to Phase A contract rules.
    
    Rules:
    - JWT only -> authenticated ownership context
    - X-Guest-Session-ID only -> guest ownership context
    - both credentials present -> 400 Bad Request ("Ambiguous ownership context.")
    - neither credential present -> 401 Unauthorized
    """
    has_jwt = bool(authorization and authorization.startswith("Bearer "))
    has_guest = bool(x_guest_session_id and x_guest_session_id.strip())

    if has_jwt and has_guest:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "error": {
                    "code": "AMBIGUOUS_OWNERSHIP_CONTEXT",
                    "message": "Ambiguous ownership context.",
                    "details": None,
                }
            },
        )
    if not has_jwt and not has_guest:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={
                "error": {
                    "code": "UNAUTHORIZED",
                    "message": "Missing authentication or guest session credentials.",
                    "details": None,
                }
            },
        )

    is_guest = has_guest
    context = "guest" if is_guest else "authenticated"
    return context, is_guest


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
    _, is_guest = _resolve_ownership_context(authorization, x_guest_session_id)
    now = datetime.now(timezone.utc)
    retention = RetentionType.TEMPORARY if is_guest else payload.retention_type

    return CaseResponse(
        id=uuid4(),
        title=payload.title,
        status=CaseStatus.DRAFT,
        retention_type=retention,
        expires_at=now if is_guest else None,
        document_count=0,
        created_at=now,
        updated_at=now,
    )


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
    _resolve_ownership_context(authorization, x_guest_session_id)
    now = datetime.now(timezone.utc)
    sample_case = CaseResponse(
        id=UUID("c8f3b174-8b6b-4e12-8821-49fa5cf10321"),
        title="Alpha Corp v. Beta LLC — Commercial Dispute",
        status=CaseStatus.READY,
        retention_type=RetentionType.PERSISTENT,
        expires_at=None,
        document_count=2,
        created_at=now,
        updated_at=now,
    )
    return CaseListResponse(
        items=[sample_case],
        total=1,
        limit=limit,
        offset=offset,
    )


@router.get("/{case_id}", response_model=CaseDetailResponse, summary="Get Case Details")
def get_case(
    case_id: UUID,
    authorization: str | None = Header(default=None, description="Bearer <supabase_jwt_token>"),
    x_guest_session_id: str | None = Header(default=None, description="Guest Session Token/ID"),
) -> CaseDetailResponse:
    """Retrieves full case details and associated documents overview."""
    now = datetime.now(timezone.utc)
    return CaseDetailResponse(
        id=case_id,
        title="Alpha Corp v. Beta LLC",
        status=CaseStatus.READY,
        retention_type=RetentionType.PERSISTENT,
        expires_at=None,
        documents=[
            CaseDetailDocumentItem(
                id=UUID("9b1deb4d-3b7d-4bad-9bdd-2b0d7b3dcb6d"),
                filename="petition.pdf",
                content_type="application/pdf",
                file_size=245120,
                page_count=12,
                processing_status="processed",
                created_at=now,
            )
        ],
        created_at=now,
        updated_at=now,
    )


@router.patch("/{case_id}", response_model=CaseResponse, summary="Update Case")
def update_case(
    case_id: UUID,
    payload: CaseUpdateRequest,
    authorization: str | None = Header(default=None, description="Bearer <supabase_jwt_token>"),
    x_guest_session_id: str | None = Header(default=None, description="Guest Session Token/ID"),
) -> CaseResponse:
    """Updates case title or retention settings."""
    now = datetime.now(timezone.utc)
    return CaseResponse(
        id=case_id,
        title=payload.title or "Updated Case Title",
        status=CaseStatus.READY,
        retention_type=payload.retention_type or RetentionType.PERSISTENT,
        expires_at=None,
        document_count=1,
        created_at=now,
        updated_at=now,
    )


@router.delete("/{case_id}", status_code=status.HTTP_204_NO_CONTENT, summary="Delete Case")
def delete_case(
    case_id: UUID,
    authorization: str | None = Header(default=None, description="Bearer <supabase_jwt_token>"),
    x_guest_session_id: str | None = Header(default=None, description="Guest Session Token/ID"),
) -> Response:
    """Deletes case and cascades to all documents, storage blobs, summaries, and jobs."""
    return Response(status_code=status.HTTP_204_NO_CONTENT)
