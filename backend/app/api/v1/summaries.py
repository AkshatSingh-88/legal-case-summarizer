"""V1 Summaries router."""

from uuid import UUID
from fastapi import APIRouter, Header, HTTPException, Response, status

from backend.app.api.schemas.error import StandardErrorResponse
from backend.app.api.schemas.summary import SummaryResponse
from backend.app.services.case_service import resolve_ownership_context
from backend.app.services.summary_service import get_summary_service

router = APIRouter(tags=["Summaries"])


@router.get(
    "/cases/{case_id}/summary",
    response_model=SummaryResponse,
    summary="Get Case Detailed Summary",
    responses={
        400: {"model": StandardErrorResponse, "description": "Ambiguous ownership context."},
        401: {"model": StandardErrorResponse, "description": "Missing credentials."},
        404: {"model": StandardErrorResponse, "description": "Case or summary not found."},
    },
)
def get_case_summary(
    case_id: UUID,
    authorization: str | None = Header(default=None, description="Bearer <supabase_jwt_token>"),
    x_guest_session_id: str | None = Header(default=None, description="Guest Session Token/ID"),
) -> SummaryResponse:
    """Retrieves final structured DetailedAnalysis summary content with citations."""
    caller = resolve_ownership_context(authorization, x_guest_session_id)
    summary_service = get_summary_service()
    return summary_service.get_summary(str(case_id), caller)


@router.get(
    "/cases/{case_id}/summary/pdf",
    summary="Download Case Summary PDF Report",
    responses={
        200: {
            "content": {"application/pdf": {}},
            "description": "Downloadable PDF summary report (production contract).",
        },
        501: {
            "model": StandardErrorResponse,
            "description": "PDF export generation is not yet implemented (Phase G).",
        },
    },
)
def download_summary_pdf(
    case_id: UUID,
    authorization: str | None = Header(default=None, description="Bearer <supabase_jwt_token>"),
    x_guest_session_id: str | None = Header(default=None, description="Guest Session Token/ID"),
) -> Response:
    """PDF generation placeholder returning 501 Not Implemented for Phase A."""
    raise HTTPException(
        status_code=status.HTTP_501_NOT_IMPLEMENTED,
        detail={
            "error": {
                "code": "NOT_IMPLEMENTED",
                "message": "PDF summary export generation is not yet implemented (scheduled for Phase G).",
                "details": None,
            }
        },
    )
