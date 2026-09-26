"""V1 Processing Jobs router."""

from uuid import UUID
from fastapi import APIRouter, BackgroundTasks, Header, status

from backend.app.api.schemas.error import StandardErrorResponse
from backend.app.api.schemas.job import (
    JobCancelResponse,
    JobResponse,
    ProcessCaseRequest,
)
from backend.app.pipeline.orchestrator import run_case_job_background
from backend.app.services.case_service import resolve_ownership_context
from backend.app.services.job_service import get_job_service

router = APIRouter(tags=["Processing Jobs"])


@router.post(
    "/cases/{case_id}/process",
    response_model=JobResponse,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Enqueue Case Processing Job",
    responses={
        400: {"model": StandardErrorResponse, "description": "Ambiguous ownership context."},
        401: {"model": StandardErrorResponse, "description": "Missing credentials."},
        404: {"model": StandardErrorResponse, "description": "Case not found or inaccessible."},
        409: {"model": StandardErrorResponse, "description": "Active job already exists or no documents uploaded."},
    },
)
def process_case(
    case_id: UUID,
    background_tasks: BackgroundTasks,
    payload: ProcessCaseRequest = ProcessCaseRequest(),
    authorization: str | None = Header(default=None, description="Bearer <supabase_jwt_token>"),
    x_guest_session_id: str | None = Header(default=None, description="Guest Session Token/ID"),
) -> JobResponse:
    """Enqueues an asynchronous processing job (Summary) for a Case. Returns immediately with 202 Accepted."""
    caller = resolve_ownership_context(authorization, x_guest_session_id)
    job_service = get_job_service()
    job = job_service.create_job(case_id, payload.job_type, caller)
    background_tasks.add_task(run_case_job_background, str(job.id))
    return job


@router.get(
    "/jobs/{job_id}",
    response_model=JobResponse,
    summary="Get Job Status & Progress",
    responses={
        400: {"model": StandardErrorResponse, "description": "Ambiguous ownership context."},
        401: {"model": StandardErrorResponse, "description": "Missing credentials."},
        404: {"model": StandardErrorResponse, "description": "Job not found or inaccessible."},
    },
)
def get_job(
    job_id: UUID,
    authorization: str | None = Header(default=None, description="Bearer <supabase_jwt_token>"),
    x_guest_session_id: str | None = Header(default=None, description="Guest Session Token/ID"),
) -> JobResponse:
    """Polls the status, progress, and stage of a processing job."""
    caller = resolve_ownership_context(authorization, x_guest_session_id)
    job_service = get_job_service()
    return job_service.get_job(str(job_id), caller)


@router.post(
    "/jobs/{job_id}/cancel",
    response_model=JobCancelResponse,
    summary="Cancel Processing Job",
    responses={
        400: {"model": StandardErrorResponse, "description": "Ambiguous ownership context."},
        401: {"model": StandardErrorResponse, "description": "Missing credentials."},
        404: {"model": StandardErrorResponse, "description": "Job not found or inaccessible."},
        409: {"model": StandardErrorResponse, "description": "Job already completed, failed, or cancelled."},
    },
)
def cancel_job(
    job_id: UUID,
    authorization: str | None = Header(default=None, description="Bearer <supabase_jwt_token>"),
    x_guest_session_id: str | None = Header(default=None, description="Guest Session Token/ID"),
) -> JobCancelResponse:
    """Requests cooperative cancellation of an active processing job."""
    caller = resolve_ownership_context(authorization, x_guest_session_id)
    job_service = get_job_service()
    return job_service.request_cancellation(str(job_id), caller)
