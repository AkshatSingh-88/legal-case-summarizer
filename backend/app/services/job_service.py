"""Service for managing processing jobs, lifecycle state transitions, and progress."""

from datetime import datetime, timezone
from typing import Any
from uuid import UUID, uuid4

from fastapi import HTTPException, status

from backend.app.api.schemas.job import (
    JobCancelResponse,
    JobResponse,
    JobStatus,
    JobType,
    PipelineStage,
)
from backend.app.core.supabase import get_supabase_client
from backend.app.services.case_service import CaseService, get_case_service
from backend.app.services.models import CallerContext


def _parse_dt(val: Any) -> datetime | None:
    if val is None:
        return None
    if isinstance(val, datetime):
        return val
    try:
        return datetime.fromisoformat(str(val).replace("Z", "+00:00"))
    except Exception:
        return None


class JobService:
    """Manages processing jobs persistence, state transitions, and cancellation."""

    def __init__(self, client: Any = None, case_service: CaseService | None = None):
        self._client = client
        self._case_service = case_service or CaseService(client=self._client)

    @property
    def client(self) -> Any:
        if self._client is not None:
            return self._client
        return get_supabase_client()

    def _require_client(self) -> Any:
        client = self.client
        if client is None:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail={
                    "error": {
                        "code": "DATABASE_UNAVAILABLE",
                        "message": "Supabase infrastructure is not configured or unavailable.",
                        "details": {},
                    }
                },
            )
        return client

    def create_job(
        self,
        case_id: UUID | str,
        job_type: JobType | str,
        caller: CallerContext,
    ) -> JobResponse:
        """Enqueues a processing job for a case.

        Invariants enforced:
        - Case ownership checked via CaseService.get_case (raises 404 CASE_NOT_FOUND if unauthorized/missing).
        - Case must have at least one uploaded document (raises 409 NO_DOCUMENTS_IN_CASE if 0).
        - At most one active job (queued/processing) per case and type (raises 409 ACTIVE_JOB_EXISTS).
        """
        case_id_str = str(case_id)
        # 1. Authorize caller and verify case exists & active
        case = self._case_service.get_case(case_id_str, caller)

        # 2. Verify case has uploaded documents
        doc_count = len(case.documents) if hasattr(case, "documents") and case.documents is not None else getattr(case, "document_count", 0)
        if doc_count <= 0:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={
                    "error": {
                        "code": "NO_DOCUMENTS_IN_CASE",
                        "message": "Cannot process case with no uploaded documents.",
                        "details": {"case_id": case_id_str},
                    }
                },
            )

        # Normalize job_type
        if isinstance(job_type, str):
            job_type = JobType(job_type)

        # 3. Check for existing active job proactively
        active = self.get_active_job(case_id_str, job_type)
        if active is not None:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={
                    "error": {
                        "code": "ACTIVE_JOB_EXISTS",
                        "message": "An active processing job is already in progress for this case.",
                        "details": {
                            "case_id": case_id_str,
                            "job_type": job_type.value,
                            "active_job_id": str(active.id),
                        },
                    }
                },
            )

        client = self._require_client()
        job_id = str(uuid4())
        now = datetime.now(timezone.utc)

        data_to_insert = {
            "id": job_id,
            "case_id": case_id_str,
            "job_type": job_type.value,
            "status": JobStatus.QUEUED.value,
            "progress": 0.0,
            "current_stage": PipelineStage.QUEUED.value,
            "error_message": None,
            "cancel_requested": False,
            "created_at": now.isoformat(),
            "started_at": None,
            "completed_at": None,
        }

        try:
            res = client.table("processing_jobs").insert(data_to_insert).execute()
        except Exception as exc:
            err_str = str(exc).lower()
            if "uq_active_job_per_case_and_type" in err_str or "duplicate" in err_str or "23505" in err_str:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail={
                        "error": {
                            "code": "ACTIVE_JOB_EXISTS",
                            "message": "An active processing job is already in progress for this case.",
                            "details": {"case_id": case_id_str, "job_type": job_type.value},
                        }
                    },
                )
            raise

        if not res.data:
            raise RuntimeError("Failed to insert processing job record in database.")

        return self._row_to_response(res.data[0])

    def get_job(self, job_id: UUID | str, caller: CallerContext) -> JobResponse:
        """Retrieves a processing job, enforcing ownership through the parent case."""
        client = self._require_client()
        job_id_str = str(job_id)

        res = client.table("processing_jobs").select("*").eq("id", job_id_str).execute()
        if not res.data:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail={
                    "error": {
                        "code": "JOB_NOT_FOUND",
                        "message": f"Processing job {job_id_str} not found or inaccessible.",
                        "details": {},
                    }
                },
            )

        row = res.data[0]
        case_id = row["case_id"]

        # Enforce caller authorization via parent case
        try:
            self._case_service.get_case(case_id, caller)
        except HTTPException as exc:
            if exc.status_code == status.HTTP_404_NOT_FOUND:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail={
                        "error": {
                            "code": "JOB_NOT_FOUND",
                            "message": f"Processing job {job_id_str} not found or inaccessible.",
                            "details": {},
                        }
                    },
                )
            raise

        return self._row_to_response(row)

    def get_active_job(
        self,
        case_id: UUID | str,
        job_type: JobType | str,
    ) -> JobResponse | None:
        """Finds any currently active (queued or processing) job for the given case and type."""
        client = self._require_client()
        case_id_str = str(case_id)
        job_type_str = job_type.value if isinstance(job_type, JobType) else str(job_type)

        res = (
            client.table("processing_jobs")
            .select("*")
            .eq("case_id", case_id_str)
            .eq("job_type", job_type_str)
            .in_("status", [JobStatus.QUEUED.value, JobStatus.PROCESSING.value])
            .execute()
        )

        if not res.data:
            return None

        return self._row_to_response(res.data[0])

    def mark_processing(self, job_id: UUID | str) -> JobResponse:
        """Transitions a queued job to processing."""
        client = self._require_client()
        job_id_str = str(job_id)

        job_row = self._fetch_raw_job(job_id_str)
        curr_status = job_row["status"]

        if curr_status != JobStatus.QUEUED.value:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={
                    "error": {
                        "code": "INVALID_JOB_STATE_TRANSITION",
                        "message": f"Cannot transition job from '{curr_status}' to 'processing'.",
                        "details": {"job_id": job_id_str, "current_status": curr_status},
                    }
                },
            )

        now = datetime.now(timezone.utc)
        update_data = {
            "status": JobStatus.PROCESSING.value,
            "current_stage": PipelineStage.INGESTION.value,
            "progress": 10.0,
            "started_at": now.isoformat(),
        }

        res = client.table("processing_jobs").update(update_data).eq("id", job_id_str).execute()
        if not res.data:
            raise RuntimeError(f"Failed to update job {job_id_str} to processing.")

        return self._row_to_response(res.data[0])

    def update_progress(
        self,
        job_id: UUID | str,
        stage: PipelineStage | str,
        progress: float,
    ) -> JobResponse:
        """Updates stage and progress for an active processing job.

        Progress conversion: accepts normalized 0.0-1.0 or percentage 0.0-100.0,
        and stores 0.0-100.0 in the database.
        """
        client = self._require_client()
        job_id_str = str(job_id)

        job_row = self._fetch_raw_job(job_id_str)
        curr_status = job_row["status"]

        if curr_status in (JobStatus.COMPLETED.value, JobStatus.FAILED.value, JobStatus.CANCELLED.value):
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={
                    "error": {
                        "code": "JOB_ALREADY_TERMINAL",
                        "message": f"Cannot update progress for job {job_id_str} in terminal state '{curr_status}'.",
                        "details": {"job_id": job_id_str, "status": curr_status},
                    }
                },
            )

        stage_str = stage.value if isinstance(stage, PipelineStage) else str(stage)

        # Convert to DB range 0.0-100.0
        if 0.0 <= progress <= 1.0:
            db_progress = round(progress * 100.0, 2)
        else:
            db_progress = round(progress, 2)
        db_progress = max(0.0, min(100.0, db_progress))

        update_data = {
            "current_stage": stage_str,
            "progress": db_progress,
        }

        res = client.table("processing_jobs").update(update_data).eq("id", job_id_str).execute()
        if not res.data:
            raise RuntimeError(f"Failed to update progress for job {job_id_str}.")

        return self._row_to_response(res.data[0])

    def request_cancellation(
        self,
        job_id: UUID | str,
        caller: CallerContext,
    ) -> JobCancelResponse:
        """Requests cooperative cancellation of a processing job.

        Rules:
        - Must be authorized through parent case.
        - Terminal jobs (completed/failed/cancelled) -> 409 JOB_ALREADY_TERMINAL.
        - Queued job -> Immediately transitions to 'cancelled'.
        - Processing job -> Sets cancel_requested = True.
        """
        client = self._require_client()
        job_id_str = str(job_id)

        job_row = self._fetch_raw_job(job_id_str)
        case_id = job_row["case_id"]

        # Enforce caller authorization
        try:
            self._case_service.get_case(case_id, caller)
        except HTTPException as exc:
            if exc.status_code == status.HTTP_404_NOT_FOUND:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail={
                        "error": {
                            "code": "JOB_NOT_FOUND",
                            "message": f"Processing job {job_id_str} not found or inaccessible.",
                            "details": {},
                        }
                    },
                )
            raise

        curr_status = job_row["status"]
        now = datetime.now(timezone.utc)

        if curr_status in (JobStatus.COMPLETED.value, JobStatus.FAILED.value, JobStatus.CANCELLED.value):
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={
                    "error": {
                        "code": "JOB_ALREADY_TERMINAL",
                        "message": "Cannot cancel a job that is already completed, failed, or cancelled.",
                        "details": {"job_id": job_id_str, "status": curr_status},
                    }
                },
            )

        if curr_status == JobStatus.QUEUED.value:
            # Immediate cancellation
            update_data = {
                "status": JobStatus.CANCELLED.value,
                "cancel_requested": True,
                "completed_at": now.isoformat(),
            }
            client.table("processing_jobs").update(update_data).eq("id", job_id_str).execute()
            return JobCancelResponse(
                id=UUID(job_id_str),
                status=JobStatus.CANCELLED,
                cancel_requested=True,
                message="Job cancelled successfully before execution.",
            )

        # Processing state: request cancellation flag
        update_data = {
            "cancel_requested": True,
        }
        client.table("processing_jobs").update(update_data).eq("id", job_id_str).execute()
        return JobCancelResponse(
            id=UUID(job_id_str),
            status=JobStatus.PROCESSING,
            cancel_requested=True,
            message="Cancellation request submitted.",
        )

    def is_cancellation_requested(self, job_id: UUID | str) -> bool:
        """Polls whether cancellation has been requested for a job."""
        job_row = self._fetch_raw_job(str(job_id))
        return bool(job_row.get("cancel_requested", False))

    def mark_completed(self, job_id: UUID | str) -> JobResponse:
        """Marks a job as successfully completed."""
        client = self._require_client()
        job_id_str = str(job_id)

        job_row = self._fetch_raw_job(job_id_str)
        curr_status = job_row["status"]

        if curr_status in (JobStatus.COMPLETED.value, JobStatus.FAILED.value, JobStatus.CANCELLED.value):
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={
                    "error": {
                        "code": "JOB_ALREADY_TERMINAL",
                        "message": f"Cannot complete job {job_id_str} in terminal state '{curr_status}'.",
                        "details": {"job_id": job_id_str, "status": curr_status},
                    }
                },
            )

        now = datetime.now(timezone.utc)
        update_data = {
            "status": JobStatus.COMPLETED.value,
            "current_stage": PipelineStage.COMPLETED.value,
            "progress": 100.0,
            "completed_at": now.isoformat(),
        }

        res = client.table("processing_jobs").update(update_data).eq("id", job_id_str).execute()
        if not res.data:
            raise RuntimeError(f"Failed to complete job {job_id_str}.")

        return self._row_to_response(res.data[0])

    def mark_failed(self, job_id: UUID | str, error_message: str) -> JobResponse:
        """Marks a job as failed with an error message."""
        client = self._require_client()
        job_id_str = str(job_id)

        job_row = self._fetch_raw_job(job_id_str)
        curr_status = job_row["status"]

        if curr_status in (JobStatus.COMPLETED.value, JobStatus.FAILED.value, JobStatus.CANCELLED.value):
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={
                    "error": {
                        "code": "JOB_ALREADY_TERMINAL",
                        "message": f"Cannot fail job {job_id_str} in terminal state '{curr_status}'.",
                        "details": {"job_id": job_id_str, "status": curr_status},
                    }
                },
            )

        now = datetime.now(timezone.utc)
        update_data = {
            "status": JobStatus.FAILED.value,
            "current_stage": PipelineStage.FAILED.value,
            "error_message": str(error_message),
            "completed_at": now.isoformat(),
        }

        res = client.table("processing_jobs").update(update_data).eq("id", job_id_str).execute()
        if not res.data:
            raise RuntimeError(f"Failed to record failure for job {job_id_str}.")

        return self._row_to_response(res.data[0])

    def mark_cancelled(self, job_id: UUID | str) -> JobResponse:
        """Marks a processing job as cancelled (called by worker acknowledging cancellation)."""
        client = self._require_client()
        job_id_str = str(job_id)

        job_row = self._fetch_raw_job(job_id_str)
        curr_status = job_row["status"]

        if curr_status in (JobStatus.COMPLETED.value, JobStatus.FAILED.value):
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={
                    "error": {
                        "code": "JOB_ALREADY_TERMINAL",
                        "message": f"Cannot mark job {job_id_str} as cancelled; it is '{curr_status}'.",
                        "details": {"job_id": job_id_str, "status": curr_status},
                    }
                },
            )

        now = datetime.now(timezone.utc)
        update_data = {
            "status": JobStatus.CANCELLED.value,
            "cancel_requested": True,
            "completed_at": now.isoformat(),
        }

        res = client.table("processing_jobs").update(update_data).eq("id", job_id_str).execute()
        if not res.data:
            raise RuntimeError(f"Failed to cancel job {job_id_str}.")

        return self._row_to_response(res.data[0])

    def _fetch_raw_job(self, job_id_str: str) -> dict[str, Any]:
        client = self._require_client()
        res = client.table("processing_jobs").select("*").eq("id", job_id_str).execute()
        if not res.data:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail={
                    "error": {
                        "code": "JOB_NOT_FOUND",
                        "message": f"Processing job {job_id_str} not found or inaccessible.",
                        "details": {},
                    }
                },
            )
        return res.data[0]

    def _row_to_response(self, row: dict[str, Any]) -> JobResponse:
        db_progress = float(row.get("progress", 0.0))
        api_progress = round(db_progress / 100.0, 4)
        api_progress = max(0.0, min(1.0, api_progress))

        return JobResponse(
            id=UUID(str(row["id"])),
            case_id=UUID(str(row["case_id"])),
            job_type=JobType(row["job_type"]),
            status=JobStatus(row["status"]),
            progress=api_progress,
            current_stage=PipelineStage(row["current_stage"]),
            error_message=row.get("error_message"),
            cancel_requested=bool(row.get("cancel_requested", False)),
            created_at=_parse_dt(row["created_at"]) or datetime.now(timezone.utc),
            started_at=_parse_dt(row.get("started_at")),
            completed_at=_parse_dt(row.get("completed_at")),
        )


def get_job_service(client: Any = None) -> JobService:
    """Factory helper for JobService."""
    return JobService(client=client)
