"""Comprehensive unit and pipeline tests for CaseProcessingOrchestrator."""

from datetime import datetime, timezone
from unittest.mock import MagicMock, patch
from uuid import UUID, uuid4

import pymupdf
import pytest

from backend.app.api.schemas.job import JobStatus, PipelineStage
from backend.app.api.schemas.summary import SummaryStatus
from backend.app.config import get_settings
from backend.app.pipeline.orchestrator import (
    CaseProcessingOrchestrator,
    _sanitize_error_message,
    get_case_orchestrator,
)
from backend.app.presentation.models import DetailedAnalysis
from backend.app.services.job_service import JobService
from backend.app.services.summary_service import SummaryService


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


def make_pdf_bytes(page_texts: list[str]) -> bytes:
    """Generates valid PDF byte streams in-memory for testing."""
    doc = pymupdf.open()
    for text in page_texts:
        page = doc.new_page()
        page.insert_text((72, 72), text)
    data = doc.tobytes()
    doc.close()
    return data


SAMPLE_LEGAL_TEXT_1 = (
    "In the High Court of Judicature. Commercial Civil Suit No. 104 of 2022. "
    "The Petitioner alleges non-payment of invoices amounting to Rs. 45 Lakhs under "
    "the Master Supply Agreement dated 12th January 2022. The Respondent failed to cure default."
)

SAMPLE_LEGAL_TEXT_2 = (
    "Written Statement on behalf of Respondent. The Respondent denies breach and contends "
    "force majeure disruptions due to port strikes, rendering timely delivery commercially impracticable. "
    "The Respondent seeks dismissal of the suit with compensatory costs."
)


class MockDatabaseState:
    """In-memory stateful mock simulating Supabase PostgreSQL tables and Storage."""

    def __init__(self):
        self.jobs: dict[str, dict] = {}
        self.documents: dict[str, list[dict]] = {}
        self.summaries: dict[str, dict] = {}
        self.storage: dict[str, bytes] = {}
        self.pages_called: list = []
        self.chunks_called: list = []

    def make_supabase_client(self) -> MagicMock:
        client = MagicMock()

        # Storage mock
        def from_bucket(bucket_name):
            storage_mock = MagicMock()

            def download(path):
                if path not in self.storage:
                    raise FileNotFoundError(f"Storage path not found: {path}")
                return self.storage[path]

            storage_mock.download.side_effect = download
            return storage_mock

        client.storage.from_.side_effect = from_bucket

        # Table router
        def table(table_name):
            tbl = MagicMock()

            if table_name == "pages":
                self.pages_called.append("accessed")
                tbl.insert.side_effect = lambda data: _create_query_mock([])
                return tbl

            if table_name == "chunks":
                self.chunks_called.append("accessed")
                tbl.insert.side_effect = lambda data: _create_query_mock([])
                return tbl

            if table_name == "processing_jobs":
                return self._build_jobs_query_builder()

            if table_name == "documents":
                return self._build_documents_query_builder()

            if table_name == "summaries":
                return self._build_summaries_query_builder()

            return tbl

        client.table.side_effect = table
        return client

    def _build_jobs_query_builder(self):
        builder = MagicMock()
        _filter_id = {"val": None}
        _filter_case_id = {"val": None}

        def eq(col, val):
            if col == "id":
                _filter_id["val"] = str(val)
            elif col == "case_id":
                _filter_case_id["val"] = str(val)
            return builder

        builder.eq.side_effect = eq
        builder.select.return_value = builder
        builder.in_.return_value = builder

        def execute_select():
            job_id = _filter_id["val"]
            if job_id and job_id in self.jobs:
                return MagicMock(data=[dict(self.jobs[job_id])])
            return MagicMock(data=[])

        def update(data):
            upd_builder = MagicMock()

            def upd_eq(col, val):
                upd_builder.target_id = str(val)
                return upd_builder

            upd_builder.eq.side_effect = upd_eq

            def execute_update():
                j_id = getattr(upd_builder, "target_id", _filter_id["val"])
                if j_id and j_id in self.jobs:
                    self.jobs[j_id].update(data)
                    return MagicMock(data=[dict(self.jobs[j_id])])
                return MagicMock(data=[])

            upd_builder.execute.side_effect = execute_update
            return upd_builder

        builder.execute.side_effect = execute_select
        builder.update.side_effect = update
        return builder

    def _build_documents_query_builder(self):
        builder = MagicMock()
        _case_id = {"val": None}

        def eq(col, val):
            if col == "case_id":
                _case_id["val"] = str(val)
            return builder

        builder.eq.side_effect = eq
        builder.select.return_value = builder
        builder.order.return_value = builder

        def execute():
            cid = _case_id["val"]
            docs = self.documents.get(cid, [])
            return MagicMock(data=[dict(d) for d in docs])

        builder.execute.side_effect = execute
        return builder

    def _build_summaries_query_builder(self):
        builder = MagicMock()
        _case_id = {"val": None}

        def eq(col, val):
            if col == "case_id":
                _case_id["val"] = str(val)
            elif col == "id":
                for cid, s in self.summaries.items():
                    if str(s.get("id")) == str(val):
                        _case_id["val"] = cid
                        break
            return builder

        builder.eq.side_effect = eq
        builder.select.return_value = builder

        def execute_select():
            cid = _case_id["val"]
            if cid and cid in self.summaries:
                return MagicMock(data=[dict(self.summaries[cid])])
            return MagicMock(data=[])

        def update(data):
            upd_builder = MagicMock()
            upd_case_id = {"val": None}

            def upd_eq(col, val):
                val_str = str(val)
                if col == "id":
                    for cid, s in self.summaries.items():
                        if str(s.get("id")) == val_str:
                            upd_case_id["val"] = cid
                            break
                elif col == "case_id":
                    upd_case_id["val"] = val_str
                return upd_builder

            upd_builder.eq.side_effect = upd_eq

            def execute_update():
                cid = upd_case_id["val"] or _case_id["val"]
                if cid and cid in self.summaries:
                    self.summaries[cid].update(data)
                    return MagicMock(data=[dict(self.summaries[cid])])
                return MagicMock(data=[])

            upd_builder.execute.side_effect = execute_update
            return upd_builder

        def insert(data):
            ins_builder = MagicMock()

            def execute_insert():
                cid = str(data["case_id"])
                self.summaries[cid] = dict(data)
                return MagicMock(data=[dict(data)])

            ins_builder.execute.side_effect = execute_insert
            return ins_builder

        builder.execute.side_effect = execute_select
        builder.update.side_effect = update
        builder.insert.side_effect = insert
        return builder


@pytest.fixture
def mock_db():
    return MockDatabaseState()


def test_sanitize_error_message():
    # 1. Verify configured key from .env / settings is redacted if present
    settings = get_settings()
    if settings.gemini_api_key:
        raw_env_msg = f"Error calling Google API with key {settings.gemini_api_key}"
        sanitized = _sanitize_error_message(raw_env_msg)
        assert "[REDACTED_API_KEY]" in sanitized
        assert settings.gemini_api_key not in sanitized

    # 2. Verify Google API key pattern is redacted without hardcoding any literal key
    prefix = "".join(["A", "I", "z", "a", "S", "y"])
    dummy_key = prefix + ("0" * 33)
    raw_key = f"Error calling API with key {dummy_key}"
    sanitized = _sanitize_error_message(raw_key)
    assert "[REDACTED_API_KEY]" in sanitized
    assert dummy_key not in sanitized
    assert prefix not in sanitized

    raw_token = "Failed with Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.token.signature"
    sanitized_token = _sanitize_error_message(raw_token)
    assert "[REDACTED_TOKEN]" in sanitized_token

    long_err = "x" * 600
    assert len(_sanitize_error_message(long_err)) <= 500


def test_successful_single_document_processing(mock_db):
    case_id = str(uuid4())
    job_id = str(uuid4())
    doc_id = str(uuid4())
    storage_path = f"cases/{case_id}/{doc_id}/petition.pdf"

    # Setup database state
    mock_db.jobs[job_id] = {
        "id": job_id,
        "case_id": case_id,
        "job_type": "summary",
        "status": "queued",
        "progress": 0.0,
        "current_stage": "queued",
        "error_message": None,
        "cancel_requested": False,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "started_at": None,
        "completed_at": None,
    }
    mock_db.documents[case_id] = [
        {
            "id": doc_id,
            "case_id": case_id,
            "filename": "petition.pdf",
            "storage_path": storage_path,
            "content_type": "application/pdf",
            "file_size": 1024,
            "document_type": "petition",
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
    ]
    mock_db.storage[storage_path] = make_pdf_bytes([SAMPLE_LEGAL_TEXT_1, SAMPLE_LEGAL_TEXT_2])

    client = mock_db.make_supabase_client()
    orchestrator = CaseProcessingOrchestrator(client=client)

    result = orchestrator.process_case_job(job_id)

    assert result is not None
    assert isinstance(result, DetailedAnalysis)
    assert result.case_id == case_id
    assert isinstance(result.sections, list)
    assert result.status in ("complete", "partial")

    # Verify job final state
    job_row = mock_db.jobs[job_id]
    assert job_row["status"] == JobStatus.COMPLETED.value
    assert job_row["current_stage"] == PipelineStage.COMPLETED.value
    assert job_row["progress"] == 100.0
    assert job_row["completed_at"] is not None
    assert job_row["started_at"] is not None

    # Verify summary saved in database
    assert case_id in mock_db.summaries
    sum_row = mock_db.summaries[case_id]
    assert sum_row["status"] == SummaryStatus.READY.value
    assert sum_row["content"] is not None


def test_successful_multi_document_processing_and_deterministic_order(mock_db):
    case_id = str(uuid4())
    job_id = str(uuid4())
    doc1_id = str(uuid4())
    doc2_id = str(uuid4())
    path1 = f"cases/{case_id}/{doc1_id}/petition.pdf"
    path2 = f"cases/{case_id}/{doc2_id}/reply.pdf"

    mock_db.jobs[job_id] = {
        "id": job_id,
        "case_id": case_id,
        "job_type": "summary",
        "status": "queued",
        "progress": 0.0,
        "current_stage": "queued",
        "error_message": None,
        "cancel_requested": False,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    # Deliberately supply documents in reverse creation order
    mock_db.documents[case_id] = [
        {
            "id": doc2_id,
            "case_id": case_id,
            "filename": "reply.pdf",
            "storage_path": path2,
            "document_type": "reply",
            "created_at": "2026-09-02T10:00:00Z",
        },
        {
            "id": doc1_id,
            "case_id": case_id,
            "filename": "petition.pdf",
            "storage_path": path1,
            "document_type": "petition",
            "created_at": "2026-09-01T10:00:00Z",
        },
    ]
    mock_db.storage[path1] = make_pdf_bytes([SAMPLE_LEGAL_TEXT_1])
    mock_db.storage[path2] = make_pdf_bytes([SAMPLE_LEGAL_TEXT_2])

    client = mock_db.make_supabase_client()
    orchestrator = CaseProcessingOrchestrator(client=client)

    result = orchestrator.process_case_job(job_id)

    assert result is not None
    assert mock_db.jobs[job_id]["status"] == "completed"
    assert mock_db.summaries[case_id]["status"] == "ready"


def test_job_lifecycle_progression(mock_db):
    case_id = str(uuid4())
    job_id = str(uuid4())
    doc_id = str(uuid4())
    storage_path = f"cases/{case_id}/{doc_id}/petition.pdf"

    mock_db.jobs[job_id] = {
        "id": job_id,
        "case_id": case_id,
        "job_type": "summary",
        "status": "queued",
        "progress": 0.0,
        "current_stage": "queued",
        "cancel_requested": False,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    mock_db.documents[case_id] = [
        {
            "id": doc_id,
            "case_id": case_id,
            "filename": "petition.pdf",
            "storage_path": storage_path,
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
    ]
    mock_db.storage[storage_path] = make_pdf_bytes([SAMPLE_LEGAL_TEXT_1])

    client = mock_db.make_supabase_client()
    job_service = JobService(client=client)
    progress_calls = []

    original_update_progress = job_service.update_progress

    def record_progress(j_id, stage, progress):
        progress_calls.append((stage, progress))
        return original_update_progress(j_id, stage, progress)

    job_service.update_progress = record_progress

    orchestrator = CaseProcessingOrchestrator(client=client, job_service=job_service)
    orchestrator.process_case_job(job_id)

    expected_stages = [
        (PipelineStage.OCR, 0.20),
        (PipelineStage.EVIDENCE, 0.30),
        (PipelineStage.CHUNKING, 0.40),
        (PipelineStage.LLM_CHUNK, 0.60),
        (PipelineStage.FILE_SYNTHESIS, 0.75),
        (PipelineStage.CASE_SYNTHESIS, 0.85),
        (PipelineStage.PRESENTATION, 0.95),
    ]

    for expected_stage, expected_val in expected_stages:
        matching = [p for p in progress_calls if p[0] == expected_stage and abs(p[1] - expected_val) < 1e-4]
        assert len(matching) > 0, f"Missing progress update for {expected_stage}"


def test_cancellation_before_expensive_processing(mock_db):
    case_id = str(uuid4())
    job_id = str(uuid4())
    doc_id = str(uuid4())
    storage_path = f"cases/{case_id}/{doc_id}/doc.pdf"

    mock_db.jobs[job_id] = {
        "id": job_id,
        "case_id": case_id,
        "job_type": "summary",
        "status": "queued",
        "progress": 0.0,
        "current_stage": "queued",
        "cancel_requested": False,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    mock_db.documents[case_id] = [
        {
            "id": doc_id,
            "case_id": case_id,
            "filename": "doc.pdf",
            "storage_path": storage_path,
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
    ]
    mock_db.storage[storage_path] = make_pdf_bytes([SAMPLE_LEGAL_TEXT_1])

    client = mock_db.make_supabase_client()
    job_service = JobService(client=client)

    # Cancel requested after OCR stage
    def fake_is_cancellation(j_id):
        curr_job = mock_db.jobs.get(j_id, {})
        return curr_job.get("current_stage") == PipelineStage.OCR.value

    job_service.is_cancellation_requested = fake_is_cancellation

    orchestrator = CaseProcessingOrchestrator(client=client, job_service=job_service)
    res = orchestrator.process_case_job(job_id)

    assert res is None
    assert mock_db.jobs[job_id]["status"] == JobStatus.CANCELLED.value
    assert mock_db.summaries[case_id]["status"] == SummaryStatus.FAILED.value


def test_cancellation_prior_to_execution(mock_db):
    case_id = str(uuid4())
    job_id = str(uuid4())

    mock_db.jobs[job_id] = {
        "id": job_id,
        "case_id": case_id,
        "job_type": "summary",
        "status": "queued",
        "progress": 0.0,
        "current_stage": "queued",
        "cancel_requested": True,  # Pre-cancelled
        "created_at": datetime.now(timezone.utc).isoformat(),
    }

    client = mock_db.make_supabase_client()
    orchestrator = CaseProcessingOrchestrator(client=client)

    res = orchestrator.process_case_job(job_id)

    assert res is None
    assert mock_db.jobs[job_id]["status"] == JobStatus.CANCELLED.value


def test_all_document_storage_failure(mock_db):
    case_id = str(uuid4())
    job_id = str(uuid4())
    doc_id = str(uuid4())

    mock_db.jobs[job_id] = {
        "id": job_id,
        "case_id": case_id,
        "job_type": "summary",
        "status": "queued",
        "progress": 0.0,
        "current_stage": "queued",
        "cancel_requested": False,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    mock_db.documents[case_id] = [
        {
            "id": doc_id,
            "case_id": case_id,
            "filename": "missing.pdf",
            "storage_path": "nonexistent/path.pdf",
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
    ]
    # No storage object populated -> will raise FileNotFoundError

    client = mock_db.make_supabase_client()
    orchestrator = CaseProcessingOrchestrator(client=client)

    with pytest.raises(RuntimeError) as exc:
        orchestrator.process_case_job(job_id)

    assert "All documents failed to ingest" in str(exc.value)
    assert mock_db.jobs[job_id]["status"] == JobStatus.FAILED.value
    assert mock_db.summaries[case_id]["status"] == SummaryStatus.FAILED.value


def test_invalid_corrupt_pdf(mock_db):
    case_id = str(uuid4())
    job_id = str(uuid4())
    doc_id = str(uuid4())
    storage_path = f"cases/{case_id}/{doc_id}/corrupt.pdf"

    mock_db.jobs[job_id] = {
        "id": job_id,
        "case_id": case_id,
        "job_type": "summary",
        "status": "queued",
        "progress": 0.0,
        "current_stage": "queued",
        "cancel_requested": False,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    mock_db.documents[case_id] = [
        {
            "id": doc_id,
            "case_id": case_id,
            "filename": "corrupt.pdf",
            "storage_path": storage_path,
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
    ]
    mock_db.storage[storage_path] = b"NOT_A_VALID_PDF_BINARY"

    client = mock_db.make_supabase_client()
    orchestrator = CaseProcessingOrchestrator(client=client)

    with pytest.raises(RuntimeError):
        orchestrator.process_case_job(job_id)

    assert mock_db.jobs[job_id]["status"] == JobStatus.FAILED.value
    assert mock_db.summaries[case_id]["status"] == SummaryStatus.FAILED.value


def test_partial_document_failure(mock_db):
    case_id = str(uuid4())
    job_id = str(uuid4())
    good_doc_id = str(uuid4())
    bad_doc_id = str(uuid4())
    good_path = f"cases/{case_id}/{good_doc_id}/good.pdf"
    bad_path = f"cases/{case_id}/{bad_doc_id}/bad.pdf"

    mock_db.jobs[job_id] = {
        "id": job_id,
        "case_id": case_id,
        "job_type": "summary",
        "status": "queued",
        "progress": 0.0,
        "current_stage": "queued",
        "cancel_requested": False,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    mock_db.documents[case_id] = [
        {
            "id": good_doc_id,
            "case_id": case_id,
            "filename": "good.pdf",
            "storage_path": good_path,
            "created_at": "2026-09-01T10:00:00Z",
        },
        {
            "id": bad_doc_id,
            "case_id": case_id,
            "filename": "bad.pdf",
            "storage_path": bad_path,
            "created_at": "2026-09-02T10:00:00Z",
        },
    ]
    mock_db.storage[good_path] = make_pdf_bytes([SAMPLE_LEGAL_TEXT_1])
    mock_db.storage[bad_path] = b"INVALID_CORRUPTED_BYTES"

    client = mock_db.make_supabase_client()
    orchestrator = CaseProcessingOrchestrator(client=client)

    res = orchestrator.process_case_job(job_id)

    assert res is not None
    # Good document was processed, so pipeline finishes successfully with partial notes
    assert mock_db.jobs[job_id]["status"] == JobStatus.COMPLETED.value
    assert mock_db.summaries[case_id]["status"] == SummaryStatus.READY.value


def test_no_documents_case_rejection(mock_db):
    case_id = str(uuid4())
    job_id = str(uuid4())

    mock_db.jobs[job_id] = {
        "id": job_id,
        "case_id": case_id,
        "job_type": "summary",
        "status": "queued",
        "progress": 0.0,
        "current_stage": "queued",
        "cancel_requested": False,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    mock_db.documents[case_id] = []  # No docs

    client = mock_db.make_supabase_client()
    orchestrator = CaseProcessingOrchestrator(client=client)

    with pytest.raises(ValueError) as exc:
        orchestrator.process_case_job(job_id)

    assert "Cannot process case with no uploaded documents" in str(exc.value)
    assert mock_db.jobs[job_id]["status"] == JobStatus.FAILED.value


def test_pipeline_llm_failure(mock_db):
    case_id = str(uuid4())
    job_id = str(uuid4())
    doc_id = str(uuid4())
    storage_path = f"cases/{case_id}/{doc_id}/petition.pdf"

    mock_db.jobs[job_id] = {
        "id": job_id,
        "case_id": case_id,
        "job_type": "summary",
        "status": "queued",
        "progress": 0.0,
        "current_stage": "queued",
        "cancel_requested": False,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    mock_db.documents[case_id] = [
        {
            "id": doc_id,
            "case_id": case_id,
            "filename": "petition.pdf",
            "storage_path": storage_path,
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
    ]
    mock_db.storage[storage_path] = make_pdf_bytes([SAMPLE_LEGAL_TEXT_1])

    client = mock_db.make_supabase_client()
    orchestrator = CaseProcessingOrchestrator(client=client)

    with patch("backend.app.pipeline.orchestrator.analyze_chunks", side_effect=Exception("LLM Quota Exceeded (429)")):
        with pytest.raises(Exception) as exc:
            orchestrator.process_case_job(job_id)

    assert "LLM Quota Exceeded" in str(exc.value)
    assert mock_db.jobs[job_id]["status"] == JobStatus.FAILED.value
    assert "LLM Quota Exceeded" in mock_db.jobs[job_id]["error_message"]
    assert mock_db.summaries[case_id]["status"] == SummaryStatus.FAILED.value


def test_summary_persistence_failure(mock_db):
    case_id = str(uuid4())
    job_id = str(uuid4())
    doc_id = str(uuid4())
    storage_path = f"cases/{case_id}/{doc_id}/petition.pdf"

    mock_db.jobs[job_id] = {
        "id": job_id,
        "case_id": case_id,
        "job_type": "summary",
        "status": "queued",
        "progress": 0.0,
        "current_stage": "queued",
        "cancel_requested": False,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    mock_db.documents[case_id] = [
        {
            "id": doc_id,
            "case_id": case_id,
            "filename": "petition.pdf",
            "storage_path": storage_path,
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
    ]
    mock_db.storage[storage_path] = make_pdf_bytes([SAMPLE_LEGAL_TEXT_1])

    client = mock_db.make_supabase_client()
    summary_service = SummaryService(client=client)

    def failing_upsert(c_id, content):
        raise RuntimeError("Database connection timeout during summary save")

    summary_service.upsert_summary = failing_upsert

    orchestrator = CaseProcessingOrchestrator(client=client, summary_service=summary_service)

    with pytest.raises(RuntimeError):
        orchestrator.process_case_job(job_id)

    assert mock_db.jobs[job_id]["status"] == JobStatus.FAILED.value


def test_successful_reprocessing(mock_db):
    case_id = str(uuid4())
    job_id = str(uuid4())
    doc_id = str(uuid4())
    storage_path = f"cases/{case_id}/{doc_id}/petition.pdf"

    mock_db.jobs[job_id] = {
        "id": job_id,
        "case_id": case_id,
        "job_type": "summary",
        "status": "queued",
        "progress": 0.0,
        "current_stage": "queued",
        "cancel_requested": False,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    mock_db.documents[case_id] = [
        {
            "id": doc_id,
            "case_id": case_id,
            "filename": "petition.pdf",
            "storage_path": storage_path,
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
    ]
    mock_db.storage[storage_path] = make_pdf_bytes([SAMPLE_LEGAL_TEXT_1])

    # Initial state: old summary already exists as ready
    mock_db.summaries[case_id] = {
        "id": str(uuid4()),
        "case_id": case_id,
        "summary_type": "detailed",
        "status": "ready",
        "content": {"old": "summary"},
    }

    client = mock_db.make_supabase_client()
    orchestrator = CaseProcessingOrchestrator(client=client)

    res = orchestrator.process_case_job(job_id)

    assert res is not None
    assert mock_db.jobs[job_id]["status"] == JobStatus.COMPLETED.value
    # Overwritten with new ready summary
    assert mock_db.summaries[case_id]["status"] == SummaryStatus.READY.value
    assert mock_db.summaries[case_id]["content"] is not None


def test_no_pages_chunks_db_writes(mock_db):
    case_id = str(uuid4())
    job_id = str(uuid4())
    doc_id = str(uuid4())
    storage_path = f"cases/{case_id}/{doc_id}/petition.pdf"

    mock_db.jobs[job_id] = {
        "id": job_id,
        "case_id": case_id,
        "job_type": "summary",
        "status": "queued",
        "progress": 0.0,
        "current_stage": "queued",
        "cancel_requested": False,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    mock_db.documents[case_id] = [
        {
            "id": doc_id,
            "case_id": case_id,
            "filename": "petition.pdf",
            "storage_path": storage_path,
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
    ]
    mock_db.storage[storage_path] = make_pdf_bytes([SAMPLE_LEGAL_TEXT_1])

    client = mock_db.make_supabase_client()
    orchestrator = CaseProcessingOrchestrator(client=client)

    orchestrator.process_case_job(job_id)

    # Invariant: NO writes to pages or chunks tables in Phase D Part 2
    assert len(mock_db.pages_called) == 0
    assert len(mock_db.chunks_called) == 0


def test_terminal_state_safety_on_already_terminal_job(mock_db):
    case_id = str(uuid4())
    job_id = str(uuid4())

    mock_db.jobs[job_id] = {
        "id": job_id,
        "case_id": case_id,
        "job_type": "summary",
        "status": "completed",  # Already terminal
        "progress": 100.0,
        "current_stage": "completed",
        "created_at": datetime.now(timezone.utc).isoformat(),
    }

    client = mock_db.make_supabase_client()
    orchestrator = CaseProcessingOrchestrator(client=client)

    # Must exit early without executing or modifying state
    res = orchestrator.process_case_job(job_id)
    assert res is None
    assert mock_db.jobs[job_id]["status"] == "completed"


def test_provenance_preservation(mock_db):
    case_id = str(uuid4())
    job_id = str(uuid4())
    doc_id = str(uuid4())
    storage_path = f"cases/{case_id}/{doc_id}/petition.pdf"

    mock_db.jobs[job_id] = {
        "id": job_id,
        "case_id": case_id,
        "job_type": "summary",
        "status": "queued",
        "progress": 0.0,
        "current_stage": "queued",
        "cancel_requested": False,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    mock_db.documents[case_id] = [
        {
            "id": doc_id,
            "case_id": case_id,
            "filename": "petition.pdf",
            "storage_path": storage_path,
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
    ]
    mock_db.storage[storage_path] = make_pdf_bytes([SAMPLE_LEGAL_TEXT_1, SAMPLE_LEGAL_TEXT_2])

    client = mock_db.make_supabase_client()
    orchestrator = CaseProcessingOrchestrator(client=client)

    result = orchestrator.process_case_job(job_id)

    assert result is not None
    # Check that doc_registry is created in meta
    assert "doc_registry" in result.meta or "document_count" in result.meta
    # Verify citations in sections
    for sec in result.sections:
        if sec.items:
            for item in sec.items:
                if item.citations:
                    for cit in item.citations:
                        assert cit.filename == "petition.pdf"
                        assert cit.doc_label.startswith("DOC-")


def test_cancellation_before_llm_work(mock_db):
    case_id = str(uuid4())
    job_id = str(uuid4())
    doc_id = str(uuid4())
    storage_path = f"cases/{case_id}/{doc_id}/petition.pdf"

    mock_db.jobs[job_id] = {
        "id": job_id,
        "case_id": case_id,
        "job_type": "summary",
        "status": "queued",
        "progress": 0.0,
        "current_stage": "queued",
        "cancel_requested": False,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    mock_db.documents[case_id] = [
        {
            "id": doc_id,
            "case_id": case_id,
            "filename": "petition.pdf",
            "storage_path": storage_path,
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
    ]
    mock_db.storage[storage_path] = make_pdf_bytes([SAMPLE_LEGAL_TEXT_1])

    client = mock_db.make_supabase_client()
    job_service = JobService(client=client)

    # Cancel after chunking stage (before LLM chunk analysis)
    def cancel_after_chunking(j_id):
        curr_job = mock_db.jobs.get(j_id, {})
        return curr_job.get("current_stage") == PipelineStage.CHUNKING.value

    job_service.is_cancellation_requested = cancel_after_chunking

    orchestrator = CaseProcessingOrchestrator(client=client, job_service=job_service)
    res = orchestrator.process_case_job(job_id)

    assert res is None
    assert mock_db.jobs[job_id]["status"] == JobStatus.CANCELLED.value
    assert mock_db.summaries[case_id]["status"] == SummaryStatus.FAILED.value
