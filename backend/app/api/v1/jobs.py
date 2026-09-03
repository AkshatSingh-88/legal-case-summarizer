"""V1 Processing Jobs router skeleton."""

from datetime import datetime, timezone
from uuid import UUID, uuid4
from fastapi import APIRouter, Header, HTTPException, status

from backend.app.api.schemas.error import StandardErrorResponse
from backend.app.api.schemas.job import (
    JobCancelResponse,
    JobResponse,
    JobStatus,
    JobType,
    PipelineStage,
    ProcessCaseRequest,
)

router = APIRouter(tags=["Processing Jobs"])


@router.post(
    "/cases/{case_id}/process",
    response_model=JobResponse,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Enqueue Case Processing Job",
    responses={
        409: {"model": StandardErrorResponse, "description": "Active job already exists or no documents uploaded."},
    },
)
def process_case(
    case_id: UUID,
    payload: ProcessCaseRequest = ProcessCaseRequest(),
    authorization: str | None = Header(default=None, description="Bearer <supabase_jwt_token>"),
    x_guest_session_id: str | None = Header(default=None, description="Guest Session Token/ID"),
) -> JobResponse:
    """Enqueues an asynchronous processing job (Summary) for a Case. Returns immediately with 202 Accepted."""
    now = datetime.now(timezone.utc)
    return JobResponse(
        id=uuid4(),
        case_id=case_id,
        job_type=payload.job_type,
        status=JobStatus.QUEUED,
        progress=0.0,
        current_stage=PipelineStage.QUEUED,
        error_message=None,
        cancel_requested=False,
        created_at=now,
        started_at=None,
        completed_at=None,
    )


@router.get(
    "/jobs/{job_id}",
    response_model=JobResponse,
    summary="Get Job Status & Progress",
)
def get_job(
    job_id: UUID,
    authorization: str | None = Header(default=None, description="Bearer <supabase_jwt_token>"),
    x_guest_session_id: str | None = Header(default=None, description="Guest Session Token/ID"),
) -> JobResponse:
    """Polls the status, progress, and stage of a processing job."""
    now = datetime.now(timezone.utc)
    return JobResponse(
        id=job_id,
        case_id=UUID("c8f3b174-8b6b-4e12-8821-49fa5cf10321"),
        job_type=JobType.SUMMARY,
        status=JobStatus.PROCESSING,
        progress=0.65,
        current_stage=PipelineStage.FILE_SYNTHESIS,
        error_message=None,
        cancel_requested=False,
        created_at=now,
        started_at=now,
        completed_at=None,
    )


@router.post(
    "/jobs/{job_id}/cancel",
    response_model=JobCancelResponse,
    summary="Cancel Processing Job",
    responses={
        409: {"model": StandardErrorResponse, "description": "Job already completed, failed, or cancelled."},
    },
)
def cancel_job(
    job_id: UUID,
    authorization: str | None = Header(default=None, description="Bearer <supabase_jwt_token>"),
    x_guest_session_id: str | None = Header(default=None, description="Guest Session Token/ID"),
) -> JobCancelResponse:
    """Requests cooperative cancellation of an active processing job."""
    return JobCancelResponse(
        id=job_id,
        status=JobStatus.PROCESSING,
        cancel_requested=True,
        message="Cancellation request submitted.",
    )
