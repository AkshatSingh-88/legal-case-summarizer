"""Unit and integration tests for JobService."""

from datetime import datetime, timezone
from unittest.mock import MagicMock, patch
from uuid import UUID, uuid4

import pytest
from fastapi import HTTPException

from backend.app.api.schemas.case import (
    CaseDetailDocumentItem,
    CaseDetailResponse,
    CaseStatus,
    RetentionType,
)
from backend.app.api.schemas.job import (
    JobResponse,
    JobStatus,
    JobType,
    PipelineStage,
)
from backend.app.services.case_service import CaseService
from backend.app.services.job_service import JobService, get_job_service
from backend.app.services.models import CallerContext


def _create_query_mock(data_list):
    """Creates a mock query builder supporting chained .eq(), .in_(), .select(), etc."""
    builder = MagicMock()
    builder.eq.return_value = builder
    builder.in_.return_value = builder
    builder.select.return_value = builder
    builder.order.return_value = builder
    builder.update.return_value = builder
    builder.insert.return_value = builder
    builder.execute.return_value = MagicMock(data=data_list)
    return builder


def _make_dummy_doc():
    return CaseDetailDocumentItem(
        id=uuid4(),
        filename="test.pdf",
        content_type="application/pdf",
        file_size=1024,
        page_count=2,
        processing_status="uploaded",
        created_at=datetime.now(timezone.utc),
    )


@pytest.fixture
def caller_user():
    return CallerContext(user_id="00000000-0000-0000-0000-000000000001", role="authenticated")


@pytest.fixture
def mock_case_service():
    service = MagicMock(spec=CaseService)
    return service


def test_unconfigured_supabase_raises_500():
    with patch("backend.app.services.job_service.get_supabase_client", return_value=None):
        job_svc = JobService(client=None)
        caller = CallerContext(user_id="u1", role="authenticated")
        with pytest.raises(HTTPException) as exc:
            job_svc.get_active_job(str(uuid4()), JobType.SUMMARY)
        assert exc.value.status_code == 500
        assert exc.value.detail["error"]["code"] == "DATABASE_UNAVAILABLE"


def test_create_job_success(caller_user, mock_case_service):
    case_id = uuid4()
    now = datetime.now(timezone.utc)

    # Case exists with 1 document
    mock_case_service.get_case.return_value = CaseDetailResponse(
        id=case_id,
        title="Test Case",
        status=CaseStatus.DRAFT,
        retention_type=RetentionType.TEMPORARY,
        created_at=now,
        updated_at=now,
        documents=[_make_dummy_doc()],
    )

    mock_client = MagicMock()
    # No active jobs exist
    active_mock = _create_query_mock([])
    # Insert returns new job row
    job_id = str(uuid4())
    inserted_row = {
        "id": job_id,
        "case_id": str(case_id),
        "job_type": "summary",
        "status": "queued",
        "progress": 0.0,
        "current_stage": "queued",
        "error_message": None,
        "cancel_requested": False,
        "created_at": now.isoformat(),
        "started_at": None,
        "completed_at": None,
    }
    insert_mock = _create_query_mock([inserted_row])

    # Wire client table routing
    def table_router(table_name):
        tbl = MagicMock()
        if table_name == "processing_jobs":
            tbl.select.return_value = active_mock
            tbl.insert.return_value = insert_mock
        return tbl

    mock_client.table.side_effect = table_router

    job_service = JobService(client=mock_client, case_service=mock_case_service)
    resp = job_service.create_job(case_id, JobType.SUMMARY, caller_user)

    assert isinstance(resp, JobResponse)
    assert resp.id == UUID(job_id)
    assert resp.case_id == case_id
    assert resp.status == JobStatus.QUEUED
    assert resp.progress == 0.0
    assert resp.current_stage == PipelineStage.QUEUED
    assert resp.cancel_requested is False
    assert resp.started_at is None
    assert resp.completed_at is None


def test_create_job_case_unauthorized_or_not_found(caller_user, mock_case_service):
    case_id = uuid4()
    mock_case_service.get_case.side_effect = HTTPException(
        status_code=404,
        detail={"error": {"code": "CASE_NOT_FOUND", "message": "Case not found."}},
    )

    mock_client = MagicMock()
    job_service = JobService(client=mock_client, case_service=mock_case_service)

    with pytest.raises(HTTPException) as exc:
        job_service.create_job(case_id, JobType.SUMMARY, caller_user)

    assert exc.value.status_code == 404
    assert exc.value.detail["error"]["code"] == "CASE_NOT_FOUND"


def test_create_job_zero_documents_rejected(caller_user, mock_case_service):
    case_id = uuid4()
    now = datetime.now(timezone.utc)

    # Document count is 0
    mock_case_service.get_case.return_value = CaseDetailResponse(
        id=case_id,
        title="Empty Case",
        status=CaseStatus.DRAFT,
        retention_type=RetentionType.TEMPORARY,
        created_at=now,
        updated_at=now,
        documents=[],
    )

    mock_client = MagicMock()
    job_service = JobService(client=mock_client, case_service=mock_case_service)

    with pytest.raises(HTTPException) as exc:
        job_service.create_job(case_id, JobType.SUMMARY, caller_user)

    assert exc.value.status_code == 409
    assert exc.value.detail["error"]["code"] == "NO_DOCUMENTS_IN_CASE"


def test_create_job_duplicate_active_conflict_via_active_check(caller_user, mock_case_service):
    case_id = uuid4()
    now = datetime.now(timezone.utc)

    mock_case_service.get_case.return_value = CaseDetailResponse(
        id=case_id,
        title="Test Case",
        status=CaseStatus.DRAFT,
        retention_type=RetentionType.TEMPORARY,
        created_at=now,
        updated_at=now,
        documents=[_make_dummy_doc()],
    )

    mock_client = MagicMock()
    active_row = {
        "id": str(uuid4()),
        "case_id": str(case_id),
        "job_type": "summary",
        "status": "processing",
        "progress": 50.0,
        "current_stage": "chunking",
        "error_message": None,
        "cancel_requested": False,
        "created_at": now.isoformat(),
        "started_at": now.isoformat(),
        "completed_at": None,
    }
    mock_client.table().select.return_value = _create_query_mock([active_row])

    job_service = JobService(client=mock_client, case_service=mock_case_service)

    with pytest.raises(HTTPException) as exc:
        job_service.create_job(case_id, JobType.SUMMARY, caller_user)

    assert exc.value.status_code == 409
    assert exc.value.detail["error"]["code"] == "ACTIVE_JOB_EXISTS"


def test_create_job_duplicate_active_conflict_via_unique_constraint(caller_user, mock_case_service):
    case_id = uuid4()
    now = datetime.now(timezone.utc)

    mock_case_service.get_case.return_value = CaseDetailResponse(
        id=case_id,
        title="Test Case",
        status=CaseStatus.DRAFT,
        retention_type=RetentionType.TEMPORARY,
        created_at=now,
        updated_at=now,
        documents=[_make_dummy_doc()],
    )

    mock_client = MagicMock()
    # get_active_job returns None initially (e.g. race condition)
    mock_client.table().select.return_value = _create_query_mock([])
    # But insert raises PostgreSQL unique violation
    mock_client.table().insert.return_value.execute.side_effect = Exception(
        'duplicate key value violates unique constraint "uq_active_job_per_case_and_type"'
    )

    job_service = JobService(client=mock_client, case_service=mock_case_service)

    with pytest.raises(HTTPException) as exc:
        job_service.create_job(case_id, JobType.SUMMARY, caller_user)

    assert exc.value.status_code == 409
    assert exc.value.detail["error"]["code"] == "ACTIVE_JOB_EXISTS"


def test_create_job_after_terminal_job_succeeds(caller_user, mock_case_service):
    case_id = uuid4()
    now = datetime.now(timezone.utc)

    mock_case_service.get_case.return_value = CaseDetailResponse(
        id=case_id,
        title="Test Case",
        status=CaseStatus.COMPLETED,
        retention_type=RetentionType.PERSISTENT,
        created_at=now,
        updated_at=now,
        documents=[_make_dummy_doc()],
    )

    mock_client = MagicMock()
    # Active query returns empty list because previous job was completed
    mock_client.table().select.return_value = _create_query_mock([])
    new_job_id = str(uuid4())
    inserted_row = {
        "id": new_job_id,
        "case_id": str(case_id),
        "job_type": "summary",
        "status": "queued",
        "progress": 0.0,
        "current_stage": "queued",
        "error_message": None,
        "cancel_requested": False,
        "created_at": now.isoformat(),
        "started_at": None,
        "completed_at": None,
    }
    mock_client.table().insert.return_value = _create_query_mock([inserted_row])

    job_service = JobService(client=mock_client, case_service=mock_case_service)
    resp = job_service.create_job(case_id, JobType.SUMMARY, caller_user)

    assert resp.status == JobStatus.QUEUED
    assert resp.id == UUID(new_job_id)


def test_get_job_progress_normalization(caller_user, mock_case_service):
    job_id = str(uuid4())
    case_id = str(uuid4())
    now = datetime.now(timezone.utc)

    mock_client = MagicMock()
    row = {
        "id": job_id,
        "case_id": case_id,
        "job_type": "summary",
        "status": "processing",
        "progress": 65.0,  # Stored as 65.0 in DB
        "current_stage": "file_synthesis",
        "error_message": None,
        "cancel_requested": False,
        "created_at": now.isoformat(),
        "started_at": now.isoformat(),
        "completed_at": None,
    }
    mock_client.table().select.return_value = _create_query_mock([row])

    # Caller authorized for case
    mock_case_service.get_case.return_value = MagicMock()

    job_service = JobService(client=mock_client, case_service=mock_case_service)
    resp = job_service.get_job(job_id, caller_user)

    # API response must be normalized to 0.65
    assert resp.progress == 0.65
    assert resp.current_stage == PipelineStage.FILE_SYNTHESIS
    assert resp.status == JobStatus.PROCESSING


def test_get_job_ownership_enforcement(caller_user, mock_case_service):
    job_id = str(uuid4())
    case_id = str(uuid4())
    now = datetime.now(timezone.utc)

    mock_client = MagicMock()
    row = {
        "id": job_id,
        "case_id": case_id,
        "job_type": "summary",
        "status": "queued",
        "progress": 0.0,
        "current_stage": "queued",
        "error_message": None,
        "cancel_requested": False,
        "created_at": now.isoformat(),
        "started_at": None,
        "completed_at": None,
    }
    mock_client.table().select.return_value = _create_query_mock([row])

    # CaseService returns 404 for unauthorized caller
    mock_case_service.get_case.side_effect = HTTPException(status_code=404, detail="Inaccessible")

    job_service = JobService(client=mock_client, case_service=mock_case_service)

    with pytest.raises(HTTPException) as exc:
        job_service.get_job(job_id, caller_user)

    # Must return 404 JOB_NOT_FOUND (not leaking existence)
    assert exc.value.status_code == 404
    assert exc.value.detail["error"]["code"] == "JOB_NOT_FOUND"


def test_mark_processing_transitions_queued():
    job_id = str(uuid4())
    case_id = str(uuid4())
    now = datetime.now(timezone.utc)

    queued_row = {
        "id": job_id,
        "case_id": case_id,
        "job_type": "summary",
        "status": "queued",
        "progress": 0.0,
        "current_stage": "queued",
        "error_message": None,
        "cancel_requested": False,
        "created_at": now.isoformat(),
        "started_at": None,
        "completed_at": None,
    }
    processing_row = dict(queued_row)
    processing_row["status"] = "processing"
    processing_row["current_stage"] = "ingestion"
    processing_row["progress"] = 10.0
    processing_row["started_at"] = now.isoformat()

    mock_client = MagicMock()
    mock_client.table().select.return_value = _create_query_mock([queued_row])
    mock_client.table().update.return_value = _create_query_mock([processing_row])

    job_service = JobService(client=mock_client)
    resp = job_service.mark_processing(job_id)

    assert resp.status == JobStatus.PROCESSING
    assert resp.current_stage == PipelineStage.INGESTION
    assert resp.progress == 0.10
    assert resp.started_at is not None


def test_mark_processing_rejects_non_queued():
    job_id = str(uuid4())
    completed_row = {
        "id": job_id,
        "case_id": str(uuid4()),
        "job_type": "summary",
        "status": "completed",
        "progress": 100.0,
        "current_stage": "completed",
        "error_message": None,
        "cancel_requested": False,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "started_at": datetime.now(timezone.utc).isoformat(),
        "completed_at": datetime.now(timezone.utc).isoformat(),
    }

    mock_client = MagicMock()
    mock_client.table().select.return_value = _create_query_mock([completed_row])

    job_service = JobService(client=mock_client)

    with pytest.raises(HTTPException) as exc:
        job_service.mark_processing(job_id)

    assert exc.value.status_code == 409
    assert exc.value.detail["error"]["code"] == "INVALID_JOB_STATE_TRANSITION"


def test_update_progress_and_terminal_guard():
    job_id = str(uuid4())
    case_id = str(uuid4())
    now = datetime.now(timezone.utc)

    processing_row = {
        "id": job_id,
        "case_id": case_id,
        "job_type": "summary",
        "status": "processing",
        "progress": 10.0,
        "current_stage": "ingestion",
        "error_message": None,
        "cancel_requested": False,
        "created_at": now.isoformat(),
        "started_at": now.isoformat(),
        "completed_at": None,
    }
    updated_row = dict(processing_row)
    updated_row["progress"] = 75.0
    updated_row["current_stage"] = "file_synthesis"

    mock_client = MagicMock()
    mock_client.table().select.return_value = _create_query_mock([processing_row])
    mock_client.table().update.return_value = _create_query_mock([updated_row])

    job_service = JobService(client=mock_client)
    # Pass 0.75 ratio
    resp = job_service.update_progress(job_id, PipelineStage.FILE_SYNTHESIS, 0.75)

    assert resp.progress == 0.75
    assert resp.current_stage == PipelineStage.FILE_SYNTHESIS

    # Terminal state rejection
    terminal_row = dict(processing_row)
    terminal_row["status"] = "failed"
    mock_client.table().select.return_value = _create_query_mock([terminal_row])

    with pytest.raises(HTTPException) as exc:
        job_service.update_progress(job_id, PipelineStage.CASE_SYNTHESIS, 0.85)

    assert exc.value.status_code == 409
    assert exc.value.detail["error"]["code"] == "JOB_ALREADY_TERMINAL"


def test_request_cancellation_queued(caller_user, mock_case_service):
    job_id = str(uuid4())
    case_id = str(uuid4())
    now = datetime.now(timezone.utc)

    queued_row = {
        "id": job_id,
        "case_id": case_id,
        "job_type": "summary",
        "status": "queued",
        "progress": 0.0,
        "current_stage": "queued",
        "error_message": None,
        "cancel_requested": False,
        "created_at": now.isoformat(),
        "started_at": None,
        "completed_at": None,
    }

    mock_client = MagicMock()
    mock_client.table().select.return_value = _create_query_mock([queued_row])
    mock_client.table().update.return_value = _create_query_mock([])
    mock_case_service.get_case.return_value = MagicMock()

    job_service = JobService(client=mock_client, case_service=mock_case_service)
    cancel_resp = job_service.request_cancellation(job_id, caller_user)

    assert cancel_resp.id == UUID(job_id)
    assert cancel_resp.status == JobStatus.CANCELLED
    assert cancel_resp.cancel_requested is True


def test_request_cancellation_processing(caller_user, mock_case_service):
    job_id = str(uuid4())
    case_id = str(uuid4())
    now = datetime.now(timezone.utc)

    proc_row = {
        "id": job_id,
        "case_id": case_id,
        "job_type": "summary",
        "status": "processing",
        "progress": 40.0,
        "current_stage": "chunking",
        "error_message": None,
        "cancel_requested": False,
        "created_at": now.isoformat(),
        "started_at": now.isoformat(),
        "completed_at": None,
    }

    mock_client = MagicMock()
    mock_client.table().select.return_value = _create_query_mock([proc_row])
    mock_client.table().update.return_value = _create_query_mock([])
    mock_case_service.get_case.return_value = MagicMock()

    job_service = JobService(client=mock_client, case_service=mock_case_service)
    cancel_resp = job_service.request_cancellation(job_id, caller_user)

    assert cancel_resp.id == UUID(job_id)
    assert cancel_resp.status == JobStatus.PROCESSING
    assert cancel_resp.cancel_requested is True


def test_request_cancellation_terminal_rejected(caller_user, mock_case_service):
    job_id = str(uuid4())
    case_id = str(uuid4())
    now = datetime.now(timezone.utc)

    completed_row = {
        "id": job_id,
        "case_id": case_id,
        "job_type": "summary",
        "status": "completed",
        "progress": 100.0,
        "current_stage": "completed",
        "error_message": None,
        "cancel_requested": False,
        "created_at": now.isoformat(),
        "started_at": now.isoformat(),
        "completed_at": now.isoformat(),
    }

    mock_client = MagicMock()
    mock_client.table().select.return_value = _create_query_mock([completed_row])
    mock_case_service.get_case.return_value = MagicMock()

    job_service = JobService(client=mock_client, case_service=mock_case_service)

    with pytest.raises(HTTPException) as exc:
        job_service.request_cancellation(job_id, caller_user)

    assert exc.value.status_code == 409
    assert exc.value.detail["error"]["code"] == "JOB_ALREADY_TERMINAL"


def test_mark_completed_and_failed():
    job_id = str(uuid4())
    case_id = str(uuid4())
    now = datetime.now(timezone.utc)

    proc_row = {
        "id": job_id,
        "case_id": case_id,
        "job_type": "summary",
        "status": "processing",
        "progress": 95.0,
        "current_stage": "presentation",
        "error_message": None,
        "cancel_requested": False,
        "created_at": now.isoformat(),
        "started_at": now.isoformat(),
        "completed_at": None,
    }
    completed_row = dict(proc_row)
    completed_row["status"] = "completed"
    completed_row["current_stage"] = "completed"
    completed_row["progress"] = 100.0
    completed_row["completed_at"] = now.isoformat()

    mock_client = MagicMock()
    mock_client.table().select.return_value = _create_query_mock([proc_row])
    mock_client.table().update.return_value = _create_query_mock([completed_row])

    job_service = JobService(client=mock_client)
    resp = job_service.mark_completed(job_id)

    assert resp.status == JobStatus.COMPLETED
    assert resp.progress == 1.0
    assert resp.current_stage == PipelineStage.COMPLETED
    assert resp.completed_at is not None

    # Test mark_failed
    failed_row = dict(proc_row)
    failed_row["status"] = "failed"
    failed_row["current_stage"] = "failed"
    failed_row["error_message"] = "LLM rate limit exceeded"
    failed_row["completed_at"] = now.isoformat()

    mock_client.table().select.return_value = _create_query_mock([proc_row])
    mock_client.table().update.return_value = _create_query_mock([failed_row])

    fail_resp = job_service.mark_failed(job_id, "LLM rate limit exceeded")
    assert fail_resp.status == JobStatus.FAILED
    assert fail_resp.current_stage == PipelineStage.FAILED
    assert fail_resp.error_message == "LLM rate limit exceeded"
    assert fail_resp.completed_at is not None


def test_is_cancellation_requested_and_factory():
    job_id = str(uuid4())
    row_cancelled = {"id": job_id, "cancel_requested": True}
    mock_client = MagicMock()
    mock_client.table().select.return_value = _create_query_mock([row_cancelled])

    svc = get_job_service(client=mock_client)
    assert svc.is_cancellation_requested(job_id) is True

    row_not_cancelled = {"id": job_id, "cancel_requested": False}
    mock_client.table().select.return_value = _create_query_mock([row_not_cancelled])
    assert svc.is_cancellation_requested(job_id) is False
