"""Tests for Phase A /api/v1 Contract Router Skeletons and Schemas."""

from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch
import uuid
from fastapi.testclient import TestClient
import pytest

from backend.app.api.schemas.document import DocumentType
from backend.app.api.schemas.summary import SummaryStatus
from backend.app.main import app

client = TestClient(app)


def _mock_query_builder(data_list):
    builder = MagicMock()
    builder.eq.return_value = builder
    builder.select.return_value = builder
    builder.order.return_value = builder
    builder.update.return_value = builder
    builder.delete.return_value = builder
    builder.insert.return_value = builder
    builder.execute.return_value = MagicMock(data=data_list)
    return builder


@pytest.fixture(autouse=True)
def mock_supabase_for_contract_tests(monkeypatch):
    """Provides a consistent mock Supabase client for contract schema and router tests."""
    now = datetime.now(timezone.utc)
    exp = now + timedelta(hours=48)
    case_id = "c8f3b174-8b6b-4e12-8821-49fa5cf10321"
    doc_id = "9b1deb4d-3b7d-4bad-9bdd-2b0d7b3dcb6d"
    session_id = "550e8400-e29b-41d4-a716-446655440000"

    mock_client = MagicMock()

    case_data = [{
        "id": case_id,
        "title": "Alpha Corp v. Beta LLC — Commercial Dispute",
        "status": "ready",
        "retention_type": "persistent",
        "expires_at": None,
        "user_id": "00000000-0000-0000-0000-000000000001",
        "guest_session_id": session_id,
        "created_at": now.isoformat(),
        "updated_at": now.isoformat(),
        "documents": [{"count": 2}],
    }]

    doc_data = [{
        "id": doc_id,
        "case_id": case_id,
        "filename": "petition.pdf",
        "content_type": "application/pdf",
        "file_size": 245120,
        "document_type": "petition",
        "document_type_confidence": None,
        "storage_path": f"cases/{case_id}/{doc_id}/original.pdf",
        "page_count": 12,
        "processing_status": "processed",
        "retention_type": "persistent",
        "expires_at": None,
        "created_at": now.isoformat(),
        "updated_at": now.isoformat(),
        "cases": {"user_id": "00000000-0000-0000-0000-000000000001", "guest_session_id": session_id},
    }]

    session_data = [{
        "id": session_id,
        "token_hash": "hash_123",
        "last_activity_at": now.isoformat(),
        "expires_at": exp.isoformat(),
        "created_at": now.isoformat(),
    }]

    def fake_table(name):
        tbl = MagicMock()
        if name == "cases":
            tbl.select.return_value = _mock_query_builder(case_data)
            tbl.insert.side_effect = lambda data: _mock_query_builder([{**case_data[0], **data, "created_at": now.isoformat(), "updated_at": now.isoformat()}])
            tbl.update.return_value = _mock_query_builder(case_data)
            tbl.delete.return_value = _mock_query_builder([])
        elif name == "documents":
            tbl.select.return_value = _mock_query_builder(doc_data)
            tbl.insert.side_effect = lambda data: _mock_query_builder([{**doc_data[0], **data, "created_at": now.isoformat(), "updated_at": now.isoformat()}])
            tbl.update.return_value = _mock_query_builder(doc_data)
            tbl.delete.return_value = _mock_query_builder([])
        elif name == "guest_sessions":
            tbl.select.return_value = _mock_query_builder(session_data)
            tbl.insert.side_effect = lambda data: _mock_query_builder([{**session_data[0], **data, "created_at": now.isoformat()}])
            tbl.update.return_value = _mock_query_builder(session_data)
        return tbl

    mock_client.table.side_effect = fake_table
    mock_client.storage.from_().create_signed_url.return_value = {
        "signedURL": "https://storage.supabase.co/storage/v1/object/sign/legal-docs/petition.pdf?token=placeholder_token"
    }

    monkeypatch.setattr("backend.app.core.supabase.get_supabase_client", lambda settings=None: mock_client)
    monkeypatch.setattr("backend.app.services.case_service.get_supabase_client", lambda settings=None: mock_client)
    monkeypatch.setattr("backend.app.services.document_service.get_supabase_client", lambda settings=None: mock_client)
    monkeypatch.setattr("backend.app.services.guest_service.get_supabase_client", lambda settings=None: mock_client)
    return mock_client


def test_legacy_health_routes_remain_intact():
    r1 = client.get("/api/health")
    assert r1.status_code == 200
    assert r1.json() == {"status": "ok"}

    r2 = client.get("/api/healthz")
    assert r2.status_code == 200
    assert r2.json() == {"status": "ok"}


def test_v1_system_health():
    res = client.get("/api/v1/system/health")
    assert res.status_code == 200
    data = res.json()
    assert data["status"] == "ok"
    assert "version" in data
    assert "environment" in data
    assert "timestamp" in data


def test_v1_auth_me_unauthorized_and_authorized():
    # Without header -> 401
    res_unauth = client.get("/api/v1/auth/me")
    assert res_unauth.status_code == 401

    # With Bearer header -> 200
    res_auth = client.get("/api/v1/auth/me", headers={"Authorization": "Bearer fake_jwt_token"})
    assert res_auth.status_code == 200
    data = res_auth.json()
    assert data["email"] == "lawyer@example.com"
    assert data["role"] == "authenticated"


def test_v1_cases_ownership_context_ambiguity_rules():
    # 1. Neither credential present -> 401 Unauthorized
    res_neither = client.post("/api/v1/cases", json={"title": "Test Case"})
    assert res_neither.status_code == 401
    assert res_neither.json()["detail"]["error"]["code"] == "UNAUTHORIZED"

    # 2. Both credentials present -> 400 Bad Request
    res_both = client.post(
        "/api/v1/cases",
        json={"title": "Test Case"},
        headers={
            "Authorization": "Bearer fake_jwt",
            "X-Guest-Session-ID": "gst_token_123",
        },
    )
    assert res_both.status_code == 400
    assert res_both.json()["detail"]["error"]["code"] == "AMBIGUOUS_OWNERSHIP_CONTEXT"
    assert "Ambiguous ownership context" in res_both.json()["detail"]["error"]["message"]

    # 3. Authenticated only -> 201 Created
    res_auth = client.post(
        "/api/v1/cases",
        json={"title": "Authenticated Case", "retention_type": "persistent"},
        headers={"Authorization": "Bearer fake_jwt"},
    )
    assert res_auth.status_code == 201
    assert res_auth.json()["retention_type"] == "persistent"

    # 4. Guest only -> 201 Created (forced temporary)
    res_guest = client.post(
        "/api/v1/cases",
        json={"title": "Guest Case"},
        headers={"X-Guest-Session-ID": "gst_token_123"},
    )
    assert res_guest.status_code == 201
    assert res_guest.json()["retention_type"] == "temporary"


def test_v1_cases_crud_skeleton():
    case_id = "c8f3b174-8b6b-4e12-8821-49fa5cf10321"

    # List Cases (authenticated)
    res_list = client.get("/api/v1/cases", headers={"Authorization": "Bearer fake_jwt"})
    assert res_list.status_code == 200
    assert res_list.json()["total"] >= 1

    # Get Case
    res_get = client.get(f"/api/v1/cases/{case_id}", headers={"Authorization": "Bearer fake_jwt"})
    assert res_get.status_code == 200
    assert "documents" in res_get.json()

    # Update Case
    res_patch = client.patch(
        f"/api/v1/cases/{case_id}",
        json={"title": "Updated Title"},
        headers={"Authorization": "Bearer fake_jwt"},
    )
    assert res_patch.status_code == 200
    assert res_patch.json()["title"] == "Alpha Corp v. Beta LLC — Commercial Dispute"

    # Delete Case
    res_del = client.delete(f"/api/v1/cases/{case_id}", headers={"Authorization": "Bearer fake_jwt"})
    assert res_del.status_code == 204


def test_v1_documents_endpoints_and_types():
    case_id = "c8f3b174-8b6b-4e12-8821-49fa5cf10321"

    # Upload files with multipart/form-data
    files = [("files", ("petition.pdf", b"%PDF-1.4 sample content", "application/pdf"))]
    res_up = client.post(
        f"/api/v1/cases/{case_id}/documents",
        files=files,
        headers={"Authorization": "Bearer fake_jwt"},
    )
    assert res_up.status_code == 201
    up_data = res_up.json()
    assert "results" in up_data
    assert up_data["accepted_count"] == 1
    assert up_data["results"][0]["status"] == "uploaded"
    assert up_data["results"][0]["document"]["document_type"] == "unknown"

    # List documents
    res_list = client.get(f"/api/v1/cases/{case_id}/documents", headers={"Authorization": "Bearer fake_jwt"})
    assert res_list.status_code == 200
    assert len(res_list.json()["items"]) >= 1

    # Get single doc
    doc_id = "9b1deb4d-3b7d-4bad-9bdd-2b0d7b3dcb6d"
    res_doc = client.get(f"/api/v1/documents/{doc_id}", headers={"Authorization": "Bearer fake_jwt"})
    assert res_doc.status_code == 200
    assert res_doc.json()["filename"] == "petition.pdf"
    assert res_doc.json()["document_type"] == "petition"

    # Access URL
    res_acc = client.get(f"/api/v1/documents/{doc_id}/access", headers={"Authorization": "Bearer fake_jwt"})
    assert res_acc.status_code == 200
    assert "access_url" in res_acc.json()

    # Delete doc
    res_del = client.delete(f"/api/v1/documents/{doc_id}", headers={"Authorization": "Bearer fake_jwt"})
    assert res_del.status_code == 204


def test_document_type_enum_completeness():
    assert DocumentType.UNKNOWN == "unknown"
    assert DocumentType.PETITION == "petition"
    assert DocumentType.APPEAL == "appeal"
    assert DocumentType.APPLICATION == "application"
    assert DocumentType.INTERLOCUTORY_APPLICATION == "interlocutory_application"
    assert DocumentType.AFFIDAVIT == "affidavit"
    assert DocumentType.REPLY == "reply"
    assert DocumentType.WRITTEN_STATEMENT == "written_statement"
    assert DocumentType.EVIDENCE == "evidence"
    assert DocumentType.JUDGMENT == "judgment"
    assert DocumentType.ORDER == "order"
    assert DocumentType.CHRONOLOGY == "chronology"
    assert DocumentType.REPORT == "report"
    assert DocumentType.MEMO == "memo"
    assert DocumentType.OTHER == "other"


def test_v1_jobs_endpoints():
    case_id = "c8f3b174-8b6b-4e12-8821-49fa5cf10321"

    # Process case
    res_proc = client.post(
        f"/api/v1/cases/{case_id}/process",
        json={"job_type": "summary"},
        headers={"Authorization": "Bearer fake_jwt"},
    )
    assert res_proc.status_code == 202
    job_id = res_proc.json()["id"]
    assert res_proc.json()["status"] == "queued"

    # Get job status
    res_job = client.get(f"/api/v1/jobs/{job_id}", headers={"Authorization": "Bearer fake_jwt"})
    assert res_job.status_code == 200
    assert res_job.json()["job_type"] == "summary"

    # Cancel job
    res_cancel = client.post(f"/api/v1/jobs/{job_id}/cancel", headers={"Authorization": "Bearer fake_jwt"})
    assert res_cancel.status_code == 200
    assert res_cancel.json()["cancel_requested"] is True


def test_v1_summary_endpoints_and_lifecycle():
    case_id = "c8f3b174-8b6b-4e12-8821-49fa5cf10321"

    # Verify SummaryStatus includes stale
    assert SummaryStatus.STALE == "stale"

    # Get Summary JSON
    res_sum = client.get(f"/api/v1/cases/{case_id}/summary", headers={"Authorization": "Bearer fake_jwt"})
    assert res_sum.status_code == 200
    data = res_sum.json()
    assert data["summary_type"] == "detailed"
    assert data["status"] == "ready"
    assert data["content"]["analysis_mode"] == "detailed"
    assert len(data["content"]["sections"]) >= 1

    # Get Summary PDF -> 501 Not Implemented in Phase A
    res_pdf = client.get(f"/api/v1/cases/{case_id}/summary/pdf", headers={"Authorization": "Bearer fake_jwt"})
    assert res_pdf.status_code == 501
    assert res_pdf.json()["detail"]["error"]["code"] == "NOT_IMPLEMENTED"


def test_v1_guest_endpoints():
    # Create guest session (returns session_token and last_activity_at)
    res_sess = client.post("/api/v1/guest/sessions")
    assert res_sess.status_code == 201
    sess_data = res_sess.json()
    assert "guest_session_id" in sess_data
    assert "session_token" in sess_data
    assert "last_activity_at" in sess_data
    assert "expires_at" in sess_data
    session_token = sess_data["session_token"]

    # Claim guest cases without JWT -> 401
    res_claim_no_jwt = client.post(
        "/api/v1/guest/claim",
        headers={"X-Guest-Session-ID": session_token},
    )
    assert res_claim_no_jwt.status_code == 401

    # Claim guest cases without guest header -> 400
    res_claim_no_hdr = client.post(
        "/api/v1/guest/claim",
        headers={"Authorization": "Bearer fake_jwt"},
    )
    assert res_claim_no_hdr.status_code == 400

    # Claim guest cases with both -> 200
    res_claim = client.post(
        "/api/v1/guest/claim",
        headers={
            "Authorization": "Bearer fake_jwt",
            "X-Guest-Session-ID": session_token,
        },
    )
    assert res_claim.status_code == 200
    assert res_claim.json()["claimed_count"] >= 1
