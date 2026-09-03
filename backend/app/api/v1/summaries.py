"""V1 Summaries router skeleton."""

from datetime import datetime, timezone
from uuid import UUID
from fastapi import APIRouter, Header, HTTPException, Response, status
from fastapi.responses import JSONResponse

from backend.app.api.schemas.error import StandardErrorResponse
from backend.app.api.schemas.summary import (
    SummaryResponse,
    SummaryStatus,
    SummaryType,
)
from backend.app.presentation.citations import (
    CitedAnalysisItem,
    ResolvedCitation,
)
from backend.app.presentation.models import (
    DetailedAnalysis,
    SummarySection,
)

router = APIRouter(tags=["Summaries"])


@router.get(
    "/cases/{case_id}/summary",
    response_model=SummaryResponse,
    summary="Get Case Detailed Summary",
)
def get_case_summary(
    case_id: UUID,
    authorization: str | None = Header(default=None, description="Bearer <supabase_jwt_token>"),
    x_guest_session_id: str | None = Header(default=None, description="Guest Session Token/ID"),
) -> SummaryResponse:
    """Retrieves final structured DetailedAnalysis summary content with citations."""
    now = datetime.now(timezone.utc)
    sample_citation = ResolvedCitation(
        source_ref="DOC-001:SRC-001",
        doc_label="DOC-001",
        document_id="9b1deb4d-3b7d-4bad-9bdd-2b0d7b3dcb6d",
        filename="petition.pdf",
        page_start=1,
        page_end=2,
        pages=[1, 2],
    )
    sample_item = CitedAnalysisItem(
        text="Petitioner alleges non-payment of invoice dues under the supply contract.",
        source_refs=["DOC-001:SRC-001"],
        citations=[sample_citation],
    )
    sample_overview = SummarySection(
        section_id="sec_overview",
        title="Executive Overview",
        section_type="text",
        order=1,
        text="Commercial dispute regarding non-delivery of goods.",
        items=None,
        relationships=None,
        timeline_events=None,
        source_refs=["DOC-001:SRC-001"],
    )
    sample_facts = SummarySection(
        section_id="sec_facts",
        title="Key Facts",
        section_type="items",
        order=2,
        text=None,
        items=[sample_item],
        relationships=None,
        timeline_events=None,
        source_refs=["DOC-001:SRC-001"],
    )

    detailed_content = DetailedAnalysis(
        case_id=str(case_id),
        section_count=2,
        sections=[sample_overview, sample_facts],
        case_coverage=1.0,
        status="complete",
        confidence=0.95,
        uncertainty=None,
        meta={"document_count": 1},
        analysis_mode="detailed",
        is_preliminary=False,
    )

    return SummaryResponse(
        id=UUID("e1f2a3b4-5c6d-7e8f-9a0b-1c2d3e4f5a6b"),
        case_id=case_id,
        summary_type=SummaryType.DETAILED,
        status=SummaryStatus.READY,
        content=detailed_content,
        created_at=now,
        updated_at=now,
    )


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
