"""Unit and service tests for Phase B: Database & Storage Foundation."""

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch
import uuid
from fastapi import HTTPException, UploadFile
import pytest

from backend.app.api.schemas.case import CaseCreateRequest, CaseUpdateRequest, RetentionType
from backend.app.services.case_service import CaseService, resolve_ownership_context
from backend.app.services.document_service import DocumentService
from backend.app.services.guest_service import GuestService, hash_guest_token
from backend.app.services.models import CallerContext


@pytest.fixture
def anyio_backend():
    return "asyncio"


def _create_query_mock(data_list):
    """Creates a mock query builder that supports chained .eq(), .select(), .order(), etc."""
    builder = MagicMock()
    builder.eq.return_value = builder
    builder.select.return_value = builder
    builder.order.return_value = builder
    builder.update.return_value = builder
    builder.execute.return_value = MagicMock(data=data_list)
    return builder


def test_guest_token_hashing_and_security():
    token = "gst_1234567890abcdef1234567890abcdef1234567890abcdef1234567890abcdef"
    token_hash = hash_guest_token(token)

    assert len(token_hash) == 64
    assert token_hash != token
    assert hash_guest_token(token) == token_hash  # deterministic


def test_unconfigured_supabase_raises_500():
    # Calling service with None client must fail with 500 DATABASE_UNAVAILABLE
    with patch("backend.app.services.guest_service.get_supabase_client", return_value=None):
        guest_svc = GuestService(client=None)
        with pytest.raises(HTTPException) as exc:
            guest_svc.create_session()
        assert exc.value.status_code == 500
        assert exc.value.detail["error"]["code"] == "DATABASE_UNAVAILABLE"

    with patch("backend.app.services.case_service.get_supabase_client", return_value=None):
        case_svc = CaseService(client=None)
        caller = CallerContext(user_id="u1", role="authenticated")
        with pytest.raises(HTTPException) as exc:
            case_svc.create_case(CaseCreateRequest(title="Test"), caller)
        assert exc.value.status_code == 500
        assert exc.value.detail["error"]["code"] == "DATABASE_UNAVAILABLE"


def test_guest_service_create_and_validate_session():
    mock_client = MagicMock()
    now = datetime.now(timezone.utc)
    exp = now + timedelta(hours=48)
    session_id = str(uuid.uuid4())

    mock_client.table().insert().execute.return_value = MagicMock(
        data=[{
            "id": session_id,
            "last_activity_at": now.isoformat(),
            "expires_at": exp.isoformat(),
            "created_at": now.isoformat(),
        }]
    )

    service = GuestService(client=mock_client)
    resp = service.create_session()

    assert resp.guest_session_id == uuid.UUID(session_id)
    assert resp.session_token.startswith("gst_")
    assert resp.last_activity_at is not None
    assert resp.created_at is not None

    # Validate active session
    mock_client.table().select.return_value = _create_query_mock([{"id": session_id, "expires_at": exp.isoformat()}])
    mock_client.table().update.return_value = _create_query_mock([])
    validated = service.validate_session(resp.session_token)
    assert validated is not None
    assert validated["id"] == session_id

    # Validate expired session returns None
    past_exp = now - timedelta(hours=1)
    mock_client.table().select.return_value = _create_query_mock([{"id": session_id, "expires_at": past_exp.isoformat()}])
    expired = service.validate_session(resp.session_token)
    assert expired is None


def test_ownership_ambiguity_rule():
    # 1. Both present -> 400
    with pytest.raises(HTTPException) as exc_info:
        resolve_ownership_context(
            authorization="Bearer jwt_token",
            x_guest_session_id="gst_token",
        )
    assert exc_info.value.status_code == 400
    assert exc_info.value.detail["error"]["code"] == "AMBIGUOUS_OWNERSHIP_CONTEXT"

    # 2. Neither present -> 401
    with pytest.raises(HTTPException) as exc_info:
        resolve_ownership_context(
            authorization=None,
            x_guest_session_id=None,
        )
    assert exc_info.value.status_code == 401
    assert exc_info.value.detail["error"]["code"] == "UNAUTHORIZED"

    # 3. Authenticated only -> CallerContext(role="authenticated")
    mock_verifier = MagicMock()
    mock_verifier.verify_token.return_value = {"sub": "00000000-0000-0000-0000-000000000001"}
    with patch("backend.app.core.auth.get_jwt_verifier", return_value=mock_verifier):
        caller_auth = resolve_ownership_context(authorization="Bearer valid_jwt")
    assert caller_auth.is_authenticated is True
    assert caller_auth.is_guest is False
    assert caller_auth.user_id == "00000000-0000-0000-0000-000000000001"


def test_expired_guest_token_rejected_in_ownership_resolution():
    mock_client = MagicMock()
    now = datetime.now(timezone.utc)
    past_exp = now - timedelta(hours=2)

    # Mock expired session in database
    mock_client.table().select.return_value = _create_query_mock([
        {"id": str(uuid.uuid4()), "expires_at": past_exp.isoformat()}
    ])

    with pytest.raises(HTTPException) as exc_info:
        resolve_ownership_context(
            authorization=None,
            x_guest_session_id="gst_expired_token",
            client=mock_client,
        )
    assert exc_info.value.status_code == 401
    assert exc_info.value.detail["error"]["code"] == "UNAUTHORIZED"
    assert "Invalid or expired" in exc_info.value.detail["error"]["message"]


def test_case_service_retention_invariants():
    mock_client = MagicMock()
    now = datetime.now(timezone.utc)
    case_id = str(uuid.uuid4())

    def fake_insert(data):
        return MagicMock(
            data=[{
                "id": case_id,
                "title": data["title"],
                "user_id": data.get("user_id"),
                "guest_session_id": data.get("guest_session_id"),
                "status": data["status"],
                "retention_type": data["retention_type"],
                "expires_at": data.get("expires_at"),
                "created_at": now.isoformat(),
                "updated_at": now.isoformat(),
            }]
        )

    mock_client.table().insert.return_value.execute.side_effect = lambda: fake_insert({
        "title": "Case",
        "retention_type": "temporary",
        "status": "draft",
        "expires_at": now.isoformat(),
    })

    service = CaseService(client=mock_client)

    # Guest case -> Forced temporary retention
    guest_caller = CallerContext(guest_session_id=str(uuid.uuid4()), role="guest")
    guest_req = CaseCreateRequest(title="Guest Case", retention_type=RetentionType.PERSISTENT)
    guest_res = service.create_case(guest_req, guest_caller)

    assert guest_res.retention_type == RetentionType.TEMPORARY
    assert guest_res.expires_at is not None

    # Authenticated case -> Persistent retention allowed
    mock_client.table().insert.return_value.execute.side_effect = lambda: fake_insert({
        "title": "User Case",
        "retention_type": "persistent",
        "status": "draft",
        "expires_at": None,
    })
    user_caller = CallerContext(user_id=str(uuid.uuid4()), role="authenticated")
    user_req = CaseCreateRequest(title="User Case", retention_type=RetentionType.PERSISTENT)
    user_res = service.create_case(user_req, user_caller)

    assert user_res.retention_type == RetentionType.PERSISTENT
    assert user_res.expires_at is None


@pytest.mark.anyio
async def test_document_upload_inherits_case_retention():
    mock_client = MagicMock()
    now = datetime.now(timezone.utc)
    case_id = str(uuid.uuid4())
    case_exp = now + timedelta(hours=48)

    # 1. Temporary Case
    case_row_temp = {
        "id": case_id,
        "title": "Temporary Guest Case",
        "status": "draft",
        "retention_type": "temporary",
        "expires_at": case_exp.isoformat(),
        "user_id": None,
        "guest_session_id": "gst-1",
        "created_at": now.isoformat(),
        "updated_at": now.isoformat(),
    }

    inserted_records = []

    def fake_table(table_name):
        tbl = MagicMock()
        if table_name == "cases":
            tbl.select.return_value = _create_query_mock([case_row_temp])
        elif table_name == "documents":
            tbl.select.return_value = _create_query_mock([])

            def on_doc_insert(rec):
                inserted_records.append(rec)
                ret_rec = dict(rec)
                ret_rec["created_at"] = now.isoformat()
                ret_rec["updated_at"] = now.isoformat()
                return MagicMock(data=[ret_rec])

            tbl.insert.side_effect = lambda rec: MagicMock(execute=lambda: on_doc_insert(rec))
        return tbl

    mock_client.table.side_effect = fake_table

    doc_service = DocumentService(client=mock_client)
    caller = CallerContext(guest_session_id="gst-1", role="guest")

    file_valid = MagicMock(spec=UploadFile)
    file_valid.filename = "petition.pdf"
    file_valid.read = AsyncMock(return_value=b"%PDF-1.4 test temporary")

    resp = await doc_service.upload_documents(case_id, [file_valid], caller)
    assert resp.accepted_count == 1
    assert resp.results[0].document.retention_type == RetentionType.TEMPORARY
    assert resp.results[0].document.expires_at is not None
    assert inserted_records[0]["retention_type"] == "temporary"
    assert inserted_records[0]["expires_at"] == case_exp.isoformat()


@pytest.mark.anyio
async def test_document_upload_partial_success_and_validations():
    mock_client = MagicMock()
    now = datetime.now(timezone.utc)
    case_id = str(uuid.uuid4())

    case_row = {
        "id": case_id,
        "title": "Test Case",
        "status": "draft",
        "retention_type": "persistent",
        "expires_at": None,
        "user_id": "user-1",
        "guest_session_id": None,
        "created_at": now.isoformat(),
        "updated_at": now.isoformat(),
    }

    def fake_table(table_name):
        tbl = MagicMock()
        if table_name == "cases":
            tbl.select.return_value = _create_query_mock([case_row])
        elif table_name == "documents":
            tbl.select.return_value = _create_query_mock([])
            tbl.insert.return_value.execute.return_value = MagicMock(
                data=[{
                    "id": str(uuid.uuid4()),
                    "case_id": case_id,
                    "filename": "appeal.pdf",
                    "content_type": "application/pdf",
                    "file_size": 33,
                    "document_type": "unknown",
                    "document_type_confidence": None,
                    "page_count": None,
                    "processing_status": "uploaded",
                    "retention_type": "persistent",
                    "expires_at": None,
                    "created_at": now.isoformat(),
                    "updated_at": now.isoformat(),
                }]
            )
        return tbl

    mock_client.table.side_effect = fake_table

    doc_service = DocumentService(client=mock_client)
    caller = CallerContext(user_id="user-1", role="authenticated")

    # Create dummy upload files:
    # 1. Invalid non-PDF file
    file_invalid = MagicMock(spec=UploadFile)
    file_invalid.filename = "notes.txt"
    file_invalid.read = AsyncMock(return_value=b"This is plain text not PDF")

    # 2. Empty file
    file_empty = MagicMock(spec=UploadFile)
    file_empty.filename = "empty.pdf"
    file_empty.read = AsyncMock(return_value=b"")

    # 3. Valid PDF file
    file_valid = MagicMock(spec=UploadFile)
    file_valid.filename = "appeal.pdf"
    file_valid.read = AsyncMock(return_value=b"%PDF-1.4 valid legal pdf content")

    resp = await doc_service.upload_documents(case_id, [file_invalid, file_empty, file_valid], caller)

    assert resp.accepted_count == 1
    assert resp.failed_count == 2
    assert len(resp.results) == 3

    assert resp.results[0].status == "failed"
    assert resp.results[0].error.code == "INVALID_FILE_TYPE"

    assert resp.results[1].status == "failed"
    assert resp.results[1].error.code == "EMPTY_FILE"

    assert resp.results[2].status == "uploaded"
    assert resp.results[2].document.filename == "appeal.pdf"


@pytest.mark.anyio
async def test_orphan_storage_cleanup_on_db_insert_failure():
    mock_client = MagicMock()
    now = datetime.now(timezone.utc)
    case_id = str(uuid.uuid4())

    case_row = {
        "id": case_id,
        "title": "Test Case",
        "status": "draft",
        "retention_type": "persistent",
        "expires_at": None,
        "user_id": "user-1",
        "guest_session_id": None,
        "created_at": now.isoformat(),
        "updated_at": now.isoformat(),
    }

    def fake_table(table_name):
        tbl = MagicMock()
        if table_name == "cases":
            tbl.select.return_value = _create_query_mock([case_row])
        elif table_name == "documents":
            tbl.select.return_value = _create_query_mock([])
            tbl.insert.return_value.execute.side_effect = RuntimeError("DB connection failure")
        return tbl

    mock_client.table.side_effect = fake_table

    # Storage upload succeeds, but DB insert raises exception
    mock_client.storage.from_().upload.return_value = {"Key": "uploaded_key"}

    doc_service = DocumentService(client=mock_client)
    caller = CallerContext(user_id="user-1", role="authenticated")

    file_valid = MagicMock(spec=UploadFile)
    file_valid.filename = "doc.pdf"
    file_valid.read = AsyncMock(return_value=b"%PDF-1.4 test")

    resp = await doc_service.upload_documents(case_id, [file_valid], caller)

    assert resp.accepted_count == 0
    assert resp.failed_count == 1
    assert resp.results[0].error.code == "METADATA_PERSISTENCE_FAILED"

    # Verify storage.remove was called for orphan cleanup
    mock_client.storage.from_().remove.assert_called_once()


def test_storage_deletion_failure_behavior():
    mock_client = MagicMock()
    now = datetime.now(timezone.utc)
    case_id = str(uuid.uuid4())
    doc_id = str(uuid.uuid4())

    case_row = {
        "id": case_id,
        "title": "Test Case",
        "status": "ready",
        "retention_type": "persistent",
        "expires_at": None,
        "user_id": "user-1",
        "created_at": now.isoformat(),
        "updated_at": now.isoformat(),
    }
    doc_row = {
        "id": doc_id,
        "case_id": case_id,
        "filename": "doc.pdf",
        "content_type": "application/pdf",
        "file_size": 1024,
        "storage_path": f"cases/{case_id}/{doc_id}/original.pdf",
        "cases": {"user_id": "user-1", "guest_session_id": None},
        "created_at": now.isoformat(),
    }

    tbl_cases = MagicMock()
    tbl_cases.select.return_value = _create_query_mock([case_row])
    tbl_cases.delete.return_value = _create_query_mock([])

    tbl_docs = MagicMock()
    tbl_docs.select.return_value = _create_query_mock([doc_row])
    tbl_docs.delete.return_value = _create_query_mock([])

    def fake_table(table_name):
        if table_name == "cases":
            return tbl_cases
        return tbl_docs

    mock_client.table.side_effect = fake_table
    caller = CallerContext(user_id="user-1", role="authenticated")
    doc_service = DocumentService(client=mock_client)

    # 1. Fatal storage error -> raises 502 STORAGE_DELETION_FAILED and does NOT delete DB record
    mock_client.storage.from_().remove.side_effect = RuntimeError("S3 Connection timeout / Access Denied")
    with pytest.raises(HTTPException) as exc:
        doc_service.delete_document(doc_id, caller)
    assert exc.value.status_code == 502
    assert exc.value.detail["error"]["code"] == "STORAGE_DELETION_FAILED"

    # 2. Already absent (404/not found) -> proceeds to delete DB record
    mock_client.storage.from_().remove.side_effect = RuntimeError("404: Not Found")
    doc_service.delete_document(doc_id, caller)
    tbl_docs.delete().eq.assert_called_with("id", doc_id)


def test_explicit_case_deletion_storage_cleanup():
    mock_client = MagicMock()
    now = datetime.now(timezone.utc)
    case_id = str(uuid.uuid4())

    case_row = {
        "id": case_id,
        "title": "Test Case",
        "status": "ready",
        "retention_type": "persistent",
        "expires_at": None,
        "user_id": "user-1",
        "created_at": now.isoformat(),
        "updated_at": now.isoformat(),
    }

    def fake_table(table_name):
        tbl = MagicMock()
        if table_name == "cases":
            tbl.select.return_value = _create_query_mock([case_row])
            tbl.delete.return_value.eq.return_value.execute.return_value = MagicMock(data=[])
        elif table_name == "documents":
            tbl.select.return_value = _create_query_mock([
                {
                    "id": str(uuid.uuid4()),
                    "case_id": case_id,
                    "filename": "doc1.pdf",
                    "content_type": "application/pdf",
                    "file_size": 1024,
                    "storage_path": f"cases/{case_id}/doc1/original.pdf",
                    "created_at": now.isoformat(),
                },
                {
                    "id": str(uuid.uuid4()),
                    "case_id": case_id,
                    "filename": "doc2.pdf",
                    "content_type": "application/pdf",
                    "file_size": 2048,
                    "storage_path": f"cases/{case_id}/doc2/original.pdf",
                    "created_at": now.isoformat(),
                },
            ])
        return tbl

    mock_client.table.side_effect = fake_table

    service = CaseService(client=mock_client)
    caller = CallerContext(user_id="user-1", role="authenticated")

    service.delete_case(case_id, caller)

    # Verify storage.remove called with both paths
    mock_client.storage.from_().remove.assert_called_once_with([
        f"cases/{case_id}/doc1/original.pdf",
        f"cases/{case_id}/doc2/original.pdf",
    ])


def test_document_download_url_uses_configured_expiration():
    mock_client = MagicMock()
    now = datetime.now(timezone.utc)
    doc_id = str(uuid.uuid4())
    case_id = str(uuid.uuid4())

    doc_row = {
        "id": doc_id,
        "case_id": case_id,
        "filename": "appeal.pdf",
        "storage_path": f"cases/{case_id}/{doc_id}/original.pdf",
        "cases": {"user_id": "user-123", "guest_session_id": None},
        "created_at": now.isoformat(),
    }

    mock_client.table().select.return_value = _create_query_mock([doc_row])
    mock_client.storage.from_().create_signed_url.return_value = {"signedURL": "https://storage.example.com/signed?token=abc"}

    service = DocumentService(client=mock_client)
    caller = CallerContext(user_id="user-123", role="authenticated")

    with patch("backend.app.services.document_service.get_settings") as mock_settings:
        mock_settings.return_value.supabase_documents_bucket = "legal-case-documents"
        mock_settings.return_value.signed_url_expiration_seconds = 7200

        resp = service.get_document_access(doc_id, caller)

        assert resp.document_id == uuid.UUID(doc_id)
        assert resp.access_url == "https://storage.example.com/signed?token=abc"
        assert resp.expires_at > now
        mock_client.storage.from_().create_signed_url.assert_called_once_with(
            path=f"cases/{case_id}/{doc_id}/original.pdf",
            expires_in=7200,
        )

