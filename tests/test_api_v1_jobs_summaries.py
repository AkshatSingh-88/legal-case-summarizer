"""Tests for Phase D Part 3: API Router Wiring & Background Execution Boundary."""

from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch
from uuid import UUID, uuid4
import pytest
from fastapi.testclient import TestClient

from backend.app.api.schemas.job import JobStatus, JobType, PipelineStage
from backend.app.api.schemas.summary import SummaryStatus, SummaryType
from backend.app.main import app
from backend.app.pipeline.orchestrator import run_case_job_background
from backend.app.services.guest_service import hash_guest_token

client = TestClient(app)


class MockDatabase:
    """Stateful mock for Supabase PostgreSQL tables in API tests."""

    def __init__(self):
        self.cases: dict[str, dict] = {}
        self.documents: dict[str, list[dict]] = {}
        self.guest_sessions: dict[str, dict] = {}
        self.jobs: dict[str, dict] = {}
        self.summaries: dict[str, dict] = {}

    def add_case(
        self,
        case_id: str,
        user_id: str | None = None,
        guest_session_id: str | None = None,
        title: str = "Test Case",
    ) -> dict:
        now = datetime.now(timezone.utc).isoformat()
        row = {
            "id": case_id,
            "title": title,
            "status": "draft",
            "retention_type": "persistent",
            "user_id": user_id,
            "guest_session_id": guest_session_id,
            "created_at": now,
            "updated_at": now,
            "expires_at": None,
        }
        self.cases[case_id] = row
        return row

    def add_document(
        self,
        case_id: str,
        doc_id: str | None = None,
        filename: str = "petition.pdf",
    ) -> dict:
        now = datetime.now(timezone.utc).isoformat()
        row = {
            "id": doc_id or str(uuid4()),
            "case_id": case_id,
            "filename": filename,
            "content_type": "application/pdf",
            "file_size": 1024,
            "page_count": 2,
            "processing_status": "uploaded",
            "created_at": now,
            "updated_at": now,
        }
        if case_id not in self.documents:
            self.documents[case_id] = []
        self.documents[case_id].append(row)
        return row

    def make_client(self):
        mock_client = MagicMock()

        def table_dispatch(name: str):
            tbl = MagicMock()
            if name == "cases":
                return self._cases_builder()
            elif name == "documents":
                return self._documents_builder()
            elif name == "guest_sessions":
                return self._guest_sessions_builder()
            elif name == "processing_jobs":
                return self._jobs_builder()
            elif name == "summaries":
                return self._summaries_builder()
            return tbl

        mock_client.table.side_effect = table_dispatch
        return mock_client

    def _cases_builder(self):
        b = MagicMock()
        _filters = {}

        def eq(col, val):
            _filters[col] = str(val)
            return b

        b.eq.side_effect = eq
        b.select.return_value = b
        b.order.return_value = b

        def execute_select():
            res = list(self.cases.values())
            for col, val in _filters.items():
                res = [r for r in res if str(r.get(col)) == val]
            enriched = []
            for r in res:
                c_id = r["id"]
                doc_count = len(self.documents.get(c_id, []))
                enriched.append({**r, "documents": [{"count": doc_count}]})
            return MagicMock(data=enriched)

        b.execute.side_effect = execute_select
        return b

    def _documents_builder(self):
        b = MagicMock()
        _filters = {}

        def eq(col, val):
            _filters[col] = str(val)
            return b

        def order_fn(*args, **kwargs):
            return b

        b.eq.side_effect = eq
        b.select.return_value = b
        b.order.side_effect = order_fn

        def execute_select():
            all_docs = []
            for dlist in self.documents.values():
                all_docs.extend(dlist)
            res = all_docs
            for col, val in _filters.items():
                res = [r for r in res if str(r.get(col)) == val]
            return MagicMock(data=res)

        b.execute.side_effect = execute_select
        return b

    def _guest_sessions_builder(self):
        b = MagicMock()
        _filters = {}

        def eq(col, val):
            _filters[col] = str(val)
            return b

        b.eq.side_effect = eq
        b.select.return_value = b

        def execute_select():
            res = list(self.guest_sessions.values())
            for col, val in _filters.items():
                res = [r for r in res if str(r.get(col)) == val]
            return MagicMock(data=res)

        def update(data):
            upd = MagicMock()
            def execute_update():
                res = list(self.guest_sessions.values())
                for col, val in _filters.items():
                    res = [r for r in res if str(r.get(col)) == val]
                for r in res:
                    r.update(data)
                return MagicMock(data=res)
            upd.execute.side_effect = execute_update
            return upd

        b.execute.side_effect = execute_select
        b.update.side_effect = update
        return b

    def _jobs_builder(self):
        b = MagicMock()
        _filters = {}
        _in_filters = {}

        def eq(col, val):
            _filters[col] = str(val)
            return b

        def in_(col, vals):
            _in_filters[col] = [str(v) for v in vals]
            return b

        b.eq.side_effect = eq
        b.in_.side_effect = in_
        b.select.return_value = b
        b.order.return_value = b

        def execute_select():
            res = list(self.jobs.values())
            for col, val in _filters.items():
                res = [r for r in res if str(r.get(col)) == val]
            for col, vals in _in_filters.items():
                res = [r for r in res if str(r.get(col)) in vals]
            return MagicMock(data=res)

        def insert(data):
            ins = MagicMock()
            def execute_insert():
                jid = str(data.get("id", uuid4()))
                self.jobs[jid] = dict(data)
                return MagicMock(data=[dict(data)])
            ins.execute.side_effect = execute_insert
            return ins

        def update(data):
            upd = MagicMock()
            _upd_filters = dict(_filters)
            def upd_eq(col, val):
                _upd_filters[col] = str(val)
                return upd
            upd.eq.side_effect = upd_eq
            def execute_update():
                res = list(self.jobs.values())
                for col, val in _upd_filters.items():
                    res = [r for r in res if str(r.get(col)) == val]
                for r in res:
                    r.update(data)
                return MagicMock(data=res)
            upd.execute.side_effect = execute_update
            return upd

        b.execute.side_effect = execute_select
        b.insert.side_effect = insert
        b.update.side_effect = update
        return b

    def _summaries_builder(self):
        b = MagicMock()
        _filters = {}

        def eq(col, val):
            _filters[col] = str(val)
            return b

        b.eq.side_effect = eq
        b.select.return_value = b

        def execute_select():
            res = list(self.summaries.values())
            for col, val in _filters.items():
                res = [r for r in res if str(r.get(col)) == val]
            return MagicMock(data=res)

        def insert(data):
            ins = MagicMock()
            def execute_insert():
                cid = str(data["case_id"])
                sid = str(data.get("id", uuid4()))
                self.summaries[cid] = dict(data)
                return MagicMock(data=[dict(data)])
            ins.execute.side_effect = execute_insert
            return ins

        def update(data):
            upd = MagicMock()
            _upd_filters = dict(_filters)
            def upd_eq(col, val):
                _upd_filters[col] = str(val)
                return upd
            upd.eq.side_effect = upd_eq
            def execute_update():
                res = list(self.summaries.values())
                for col, val in _upd_filters.items():
                    res = [r for r in res if str(r.get(col)) == val]
                for r in res:
                    r.update(data)
                return MagicMock(data=res)
            upd.execute.side_effect = execute_update
            return upd

        b.execute.side_effect = execute_select
        b.insert.side_effect = insert
        b.update.side_effect = update
        return b


@pytest.fixture
def mock_db():
    return MockDatabase()


@pytest.fixture(autouse=True)
def setup_api_env(mock_db, monkeypatch):
    """Wires unified mock database and verifiers across all dependencies."""
    mock_client = mock_db.make_client()

    mock_verifier = MagicMock()
    def verify(token):
        if token == "user_a_jwt":
            return {"sub": "00000000-0000-0000-0000-000000000001", "role": "authenticated"}
        elif token == "user_b_jwt":
            return {"sub": "00000000-0000-0000-0000-000000000002", "role": "authenticated"}
        raise Exception("Invalid token")

    mock_verifier.verify_token.side_effect = verify

    monkeypatch.setattr("backend.app.core.auth.get_jwt_verifier", lambda: mock_verifier)
    monkeypatch.setattr("backend.app.api.deps.get_jwt_verifier", lambda: mock_verifier)
    monkeypatch.setattr("backend.app.api.v1.auth.get_jwt_verifier", lambda: mock_verifier)

    monkeypatch.setattr("backend.app.core.supabase.get_supabase_client", lambda settings=None: mock_client)
    monkeypatch.setattr("backend.app.services.case_service.get_supabase_client", lambda settings=None: mock_client)
    monkeypatch.setattr("backend.app.services.document_service.get_supabase_client", lambda settings=None: mock_client)
    monkeypatch.setattr("backend.app.services.guest_service.get_supabase_client", lambda settings=None: mock_client)
    monkeypatch.setattr("backend.app.services.job_service.get_supabase_client", lambda settings=None: mock_client)
    monkeypatch.setattr("backend.app.services.summary_service.get_supabase_client", lambda settings=None: mock_client)
    monkeypatch.setattr("backend.app.pipeline.orchestrator.get_supabase_client", lambda settings=None: mock_client)
    monkeypatch.setattr("backend.app.api.v1.jobs.run_case_job_background", lambda jid: None)

    return mock_db


# --- Tests for POST /cases/{case_id}/process ---

def test_process_case_success_enqueues_background_task(mock_db, monkeypatch):
    case_id = str(uuid4())
    mock_db.add_case(case_id, user_id="00000000-0000-0000-0000-000000000001", title="Contract Dispute")
    mock_db.add_document(case_id)

    scheduled_tasks = []
    monkeypatch.setattr(
        "backend.app.api.v1.jobs.run_case_job_background",
        lambda jid: scheduled_tasks.append(jid),
    )

    res = client.post(
        f"/api/v1/cases/{case_id}/process",
        json={"job_type": "summary"},
        headers={"Authorization": "Bearer user_a_jwt"},
    )

    assert res.status_code == 202
    data = res.json()
    assert data["case_id"] == case_id
    assert data["status"] == "queued"
    assert data["current_stage"] == "queued"
    assert data["progress"] == 0.0
    assert data["id"] in mock_db.jobs

    # Verify background task scheduled with correct job ID
    assert len(scheduled_tasks) == 1
    assert scheduled_tasks[0] == data["id"]


def test_process_case_inaccessible_case_returns_404(mock_db, monkeypatch):
    case_id = str(uuid4())
    # Case owned by User B
    mock_db.add_case(case_id, user_id="00000000-0000-0000-0000-000000000002", title="User B Case")
    mock_db.add_document(case_id)

    scheduled_tasks = []
    monkeypatch.setattr(
        "backend.app.api.v1.jobs.run_case_job_background",
        lambda jid: scheduled_tasks.append(jid),
    )

    # User A attempts to process
    res = client.post(
        f"/api/v1/cases/{case_id}/process",
        json={"job_type": "summary"},
        headers={"Authorization": "Bearer user_a_jwt"},
    )

    assert res.status_code == 404
    assert res.json()["detail"]["error"]["code"] == "CASE_NOT_FOUND"
    assert len(scheduled_tasks) == 0


def test_process_case_nonexistent_returns_404():
    res = client.post(
        f"/api/v1/cases/{uuid4()}/process",
        json={"job_type": "summary"},
        headers={"Authorization": "Bearer user_a_jwt"},
    )
    assert res.status_code == 404
    assert res.json()["detail"]["error"]["code"] == "CASE_NOT_FOUND"


def test_process_case_no_documents_returns_409(mock_db):
    case_id = str(uuid4())
    mock_db.add_case(case_id, user_id="00000000-0000-0000-0000-000000000001", title="Empty Case")
    # No documents added

    res = client.post(
        f"/api/v1/cases/{case_id}/process",
        json={"job_type": "summary"},
        headers={"Authorization": "Bearer user_a_jwt"},
    )

    assert res.status_code == 409
    assert res.json()["detail"]["error"]["code"] == "NO_DOCUMENTS_IN_CASE"


def test_process_case_active_job_conflict_returns_409(mock_db):
    case_id = str(uuid4())
    now = datetime.now(timezone.utc).isoformat()
    mock_db.add_case(case_id, user_id="00000000-0000-0000-0000-000000000001", title="Busy Case")
    mock_db.add_document(case_id)

    # Active processing job already exists
    active_job_id = str(uuid4())
    mock_db.jobs[active_job_id] = {
        "id": active_job_id,
        "case_id": case_id,
        "job_type": "summary",
        "status": "processing",
        "progress": 30.0,
        "current_stage": "ocr",
        "error_message": None,
        "cancel_requested": False,
        "created_at": now,
        "started_at": now,
        "completed_at": None,
    }

    res = client.post(
        f"/api/v1/cases/{case_id}/process",
        json={"job_type": "summary"},
        headers={"Authorization": "Bearer user_a_jwt"},
    )

    assert res.status_code == 409
    assert res.json()["detail"]["error"]["code"] == "ACTIVE_JOB_EXISTS"


def test_process_case_unauthenticated_returns_401():
    res = client.post(
        f"/api/v1/cases/{uuid4()}/process",
        json={"job_type": "summary"},
    )
    assert res.status_code == 401
    assert res.json()["detail"]["error"]["code"] == "UNAUTHORIZED"


def test_process_case_ambiguous_credentials_returns_400():
    res = client.post(
        f"/api/v1/cases/{uuid4()}/process",
        json={"job_type": "summary"},
        headers={
            "Authorization": "Bearer user_a_jwt",
            "X-Guest-Session-ID": "gst_123",
        },
    )
    assert res.status_code == 400
    assert res.json()["detail"]["error"]["code"] == "AMBIGUOUS_OWNERSHIP_CONTEXT"


# --- Tests for GET /jobs/{job_id} ---

def test_get_job_status_success(mock_db):
    case_id = str(uuid4())
    job_id = str(uuid4())
    now = datetime.now(timezone.utc).isoformat()

    mock_db.add_case(case_id, user_id="00000000-0000-0000-0000-000000000001")
    mock_db.jobs[job_id] = {
        "id": job_id,
        "case_id": case_id,
        "job_type": "summary",
        "status": "processing",
        "progress": 65.0,  # Stored in DB as 65%
        "current_stage": "file_synthesis",
        "error_message": None,
        "cancel_requested": False,
        "created_at": now,
        "started_at": now,
        "completed_at": None,
    }

    res = client.get(f"/api/v1/jobs/{job_id}", headers={"Authorization": "Bearer user_a_jwt"})
    assert res.status_code == 200
    data = res.json()
    assert data["id"] == job_id
    assert data["case_id"] == case_id
    assert data["status"] == "processing"
    assert data["current_stage"] == "file_synthesis"
    # Converted from DB 65.0 to API 0.65
    assert data["progress"] == 0.65


def test_get_job_status_foreign_job_returns_404(mock_db):
    case_id = str(uuid4())
    job_id = str(uuid4())
    now = datetime.now(timezone.utc).isoformat()

    # Owned by User B
    mock_db.add_case(case_id, user_id="00000000-0000-0000-0000-000000000002")
    mock_db.jobs[job_id] = {
        "id": job_id,
        "case_id": case_id,
        "job_type": "summary",
        "status": "processing",
        "progress": 50.0,
        "current_stage": "llm_chunk",
        "error_message": None,
        "cancel_requested": False,
        "created_at": now,
        "started_at": now,
        "completed_at": None,
    }

    # User A requests foreign job
    res = client.get(f"/api/v1/jobs/{job_id}", headers={"Authorization": "Bearer user_a_jwt"})
    assert res.status_code == 404
    assert res.json()["detail"]["error"]["code"] == "JOB_NOT_FOUND"


def test_get_job_status_nonexistent_returns_404():
    res = client.get(f"/api/v1/jobs/{uuid4()}", headers={"Authorization": "Bearer user_a_jwt"})
    assert res.status_code == 404
    assert res.json()["detail"]["error"]["code"] == "JOB_NOT_FOUND"


# --- Tests for POST /jobs/{job_id}/cancel ---

def test_cancel_queued_job_immediate_cancellation(mock_db):
    case_id = str(uuid4())
    job_id = str(uuid4())
    now = datetime.now(timezone.utc).isoformat()

    mock_db.add_case(case_id, user_id="00000000-0000-0000-0000-000000000001")
    mock_db.jobs[job_id] = {
        "id": job_id,
        "case_id": case_id,
        "job_type": "summary",
        "status": "queued",
        "progress": 0.0,
        "current_stage": "queued",
        "error_message": None,
        "cancel_requested": False,
        "created_at": now,
        "started_at": None,
        "completed_at": None,
    }

    res = client.post(f"/api/v1/jobs/{job_id}/cancel", headers={"Authorization": "Bearer user_a_jwt"})
    assert res.status_code == 200
    data = res.json()
    assert data["id"] == job_id
    assert data["status"] == "cancelled"
    assert data["cancel_requested"] is True
    assert mock_db.jobs[job_id]["status"] == "cancelled"


def test_cancel_processing_job_sets_cancel_requested(mock_db):
    case_id = str(uuid4())
    job_id = str(uuid4())
    now = datetime.now(timezone.utc).isoformat()

    mock_db.add_case(case_id, user_id="00000000-0000-0000-0000-000000000001")
    mock_db.jobs[job_id] = {
        "id": job_id,
        "case_id": case_id,
        "job_type": "summary",
        "status": "processing",
        "progress": 50.0,
        "current_stage": "llm_chunk",
        "error_message": None,
        "cancel_requested": False,
        "created_at": now,
        "started_at": now,
        "completed_at": None,
    }

    res = client.post(f"/api/v1/jobs/{job_id}/cancel", headers={"Authorization": "Bearer user_a_jwt"})
    assert res.status_code == 200
    data = res.json()
    assert data["id"] == job_id
    assert data["status"] == "processing"
    assert data["cancel_requested"] is True
    assert mock_db.jobs[job_id]["cancel_requested"] is True


def test_cancel_terminal_job_returns_409(mock_db):
    case_id = str(uuid4())
    job_id = str(uuid4())
    now = datetime.now(timezone.utc).isoformat()

    mock_db.add_case(case_id, user_id="00000000-0000-0000-0000-000000000001")
    mock_db.jobs[job_id] = {
        "id": job_id,
        "case_id": case_id,
        "job_type": "summary",
        "status": "completed",
        "progress": 100.0,
        "current_stage": "completed",
        "error_message": None,
        "cancel_requested": False,
        "created_at": now,
        "started_at": now,
        "completed_at": now,
    }

    res = client.post(f"/api/v1/jobs/{job_id}/cancel", headers={"Authorization": "Bearer user_a_jwt"})
    assert res.status_code == 409
    assert res.json()["detail"]["error"]["code"] == "JOB_ALREADY_TERMINAL"


def test_cancel_foreign_job_returns_404(mock_db):
    case_id = str(uuid4())
    job_id = str(uuid4())
    now = datetime.now(timezone.utc).isoformat()

    mock_db.add_case(case_id, user_id="00000000-0000-0000-0000-000000000002")
    mock_db.jobs[job_id] = {
        "id": job_id,
        "case_id": case_id,
        "status": "queued",
        "error_message": None,
        "cancel_requested": False,
        "created_at": now,
    }

    res = client.post(f"/api/v1/jobs/{job_id}/cancel", headers={"Authorization": "Bearer user_a_jwt"})
    assert res.status_code == 404
    assert res.json()["detail"]["error"]["code"] == "JOB_NOT_FOUND"


# --- Tests for GET /cases/{case_id}/summary ---

def test_get_summary_ready_returns_full_analysis(mock_db):
    case_id = str(uuid4())
    now = datetime.now(timezone.utc).isoformat()

    mock_db.add_case(case_id, user_id="00000000-0000-0000-0000-000000000001")

    sample_content = {
        "case_id": case_id,
        "section_count": 1,
        "sections": [{
            "section_id": "sec_overview",
            "title": "Case Overview",
            "section_type": "text",
            "order": 1,
            "text": "Detailed commercial case summary.",
            "source_refs": ["DOC-001:SRC-001"],
        }],
        "case_coverage": 1.0,
        "status": "complete",
        "confidence": 0.95,
        "uncertainty": None,
        "meta": {"document_count": 1},
        "analysis_mode": "detailed",
        "is_preliminary": False,
    }

    mock_db.summaries[case_id] = {
        "id": str(uuid4()),
        "case_id": case_id,
        "summary_type": "detailed",
        "status": "ready",
        "content": sample_content,
        "created_at": now,
        "updated_at": now,
    }

    res = client.get(f"/api/v1/cases/{case_id}/summary", headers={"Authorization": "Bearer user_a_jwt"})
    assert res.status_code == 200
    data = res.json()
    assert data["case_id"] == case_id
    assert data["status"] == "ready"
    assert data["content"]["analysis_mode"] == "detailed"
    assert len(data["content"]["sections"]) == 1
    assert data["content"]["sections"][0]["title"] == "Case Overview"


def test_get_summary_generating_returns_null_content(mock_db):
    case_id = str(uuid4())
    now = datetime.now(timezone.utc).isoformat()

    mock_db.add_case(case_id, user_id="00000000-0000-0000-0000-000000000001")
    mock_db.summaries[case_id] = {
        "id": str(uuid4()),
        "case_id": case_id,
        "summary_type": "detailed",
        "status": "generating",
        "content": None,
        "created_at": now,
        "updated_at": now,
    }

    res = client.get(f"/api/v1/cases/{case_id}/summary", headers={"Authorization": "Bearer user_a_jwt"})
    assert res.status_code == 200
    data = res.json()
    assert data["status"] == "generating"
    assert data["content"] is None


def test_get_summary_missing_returns_404(mock_db):
    case_id = str(uuid4())
    mock_db.add_case(case_id, user_id="00000000-0000-0000-0000-000000000001")

    res = client.get(f"/api/v1/cases/{case_id}/summary", headers={"Authorization": "Bearer user_a_jwt"})
    assert res.status_code == 404
    assert res.json()["detail"]["error"]["code"] == "SUMMARY_NOT_FOUND"


def test_get_summary_inaccessible_case_returns_404(mock_db):
    case_id = str(uuid4())
    # Owned by User B
    mock_db.add_case(case_id, user_id="00000000-0000-0000-0000-000000000002")

    res = client.get(f"/api/v1/cases/{case_id}/summary", headers={"Authorization": "Bearer user_a_jwt"})
    assert res.status_code == 404
    assert res.json()["detail"]["error"]["code"] == "CASE_NOT_FOUND"


# --- Guest Session Tests ---

def test_guest_session_lifecycle_and_isolation(mock_db):
    now = datetime.now(timezone.utc)
    exp = now + timedelta(hours=48)

    session_a_id = str(uuid4())
    token_a = "gst_session_a_secret_token_12345"
    mock_db.guest_sessions[session_a_id] = {
        "id": session_a_id,
        "token_hash": hash_guest_token(token_a),
        "last_activity_at": now.isoformat(),
        "expires_at": exp.isoformat(),
        "created_at": now.isoformat(),
    }

    session_b_id = str(uuid4())
    token_b = "gst_session_b_secret_token_67890"
    mock_db.guest_sessions[session_b_id] = {
        "id": session_b_id,
        "token_hash": hash_guest_token(token_b),
        "last_activity_at": now.isoformat(),
        "expires_at": exp.isoformat(),
        "created_at": now.isoformat(),
    }

    case_a_id = str(uuid4())
    mock_db.add_case(case_a_id, guest_session_id=session_a_id, title="Guest Case A")
    mock_db.add_document(case_a_id)

    # 1. Guest A creates job successfully
    res_proc = client.post(
        f"/api/v1/cases/{case_a_id}/process",
        json={"job_type": "summary"},
        headers={"X-Guest-Session-ID": token_a},
    )
    assert res_proc.status_code == 202
    job_id = res_proc.json()["id"]

    # 2. Guest A polls own job successfully
    res_job = client.get(f"/api/v1/jobs/{job_id}", headers={"X-Guest-Session-ID": token_a})
    assert res_job.status_code == 200

    # 3. Guest B cannot access Guest A's job -> 404
    res_foreign_job = client.get(f"/api/v1/jobs/{job_id}", headers={"X-Guest-Session-ID": token_b})
    assert res_foreign_job.status_code == 404

    # 4. Guest B cannot cancel Guest A's job -> 404
    res_foreign_cancel = client.post(f"/api/v1/jobs/{job_id}/cancel", headers={"X-Guest-Session-ID": token_b})
    assert res_foreign_cancel.status_code == 404

    # 5. Guest A cancels own job -> 200
    res_cancel = client.post(f"/api/v1/jobs/{job_id}/cancel", headers={"X-Guest-Session-ID": token_a})
    assert res_cancel.status_code == 200
    assert res_cancel.json()["status"] == "cancelled"


# --- Background Runner Unit Tests ---

def test_run_case_job_background_success(monkeypatch):
    mock_orchestrator = MagicMock()
    monkeypatch.setattr("backend.app.pipeline.orchestrator.get_case_orchestrator", lambda: mock_orchestrator)

    job_id = str(uuid4())
    run_case_job_background(job_id)

    mock_orchestrator.process_case_job.assert_called_once_with(job_id)


def test_run_case_job_background_fatal_exception_handled_safely(monkeypatch):
    mock_orchestrator = MagicMock()
    mock_orchestrator.process_case_job.side_effect = RuntimeError("Fatal crash in orchestrator")
    monkeypatch.setattr("backend.app.pipeline.orchestrator.get_case_orchestrator", lambda: mock_orchestrator)

    mock_job_svc = MagicMock()
    mock_job_svc.is_cancellation_requested.return_value = False
    monkeypatch.setattr("backend.app.services.job_service.get_job_service", lambda: mock_job_svc)

    job_id = str(uuid4())
    # Must NOT raise unhandled exception
    run_case_job_background(job_id)

    # JobService.mark_failed must be called
    mock_job_svc.mark_failed.assert_called_once()
    args, kwargs = mock_job_svc.mark_failed.call_args
    assert args[0] == job_id
    assert "Fatal crash in orchestrator" in args[1]


def test_run_case_job_background_does_not_overwrite_terminal_job(monkeypatch):
    from fastapi import HTTPException
    mock_orchestrator = MagicMock()
    mock_orchestrator.process_case_job.side_effect = RuntimeError("Crash after completion")
    monkeypatch.setattr("backend.app.pipeline.orchestrator.get_case_orchestrator", lambda: mock_orchestrator)

    mock_job_svc = MagicMock()
    mock_job_svc.is_cancellation_requested.return_value = False
    mock_job_svc.mark_failed.side_effect = HTTPException(status_code=409, detail={"error": {"code": "JOB_ALREADY_TERMINAL"}})
    monkeypatch.setattr("backend.app.services.job_service.get_job_service", lambda: mock_job_svc)

    job_id = str(uuid4())
    # Must swallow the 409 exception cleanly and not crash
    run_case_job_background(job_id)
    mock_job_svc.mark_failed.assert_called_once()
