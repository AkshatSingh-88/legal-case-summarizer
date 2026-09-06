"""Integration tests against live Supabase instance (Gated by environment variables)."""

from datetime import datetime, timedelta, timezone
import os
import uuid
import pytest

from backend.app.config import get_settings
from backend.app.core.supabase import get_supabase_client
from backend.app.api.schemas.case import CaseCreateRequest, CaseUpdateRequest
from backend.app.services.case_service import CaseService
from backend.app.services.document_service import DocumentService
from backend.app.services.guest_service import GuestService
from backend.app.services.models import CallerContext

from dotenv import load_dotenv

load_dotenv()

settings = get_settings()
# Gated: Only runs when SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY are explicitly provided
is_live_configured = bool(
    os.environ.get("SUPABASE_URL") and os.environ.get("SUPABASE_SERVICE_ROLE_KEY")
)


@pytest.fixture(scope="module")
def live_client():
    if not is_live_configured:
        pytest.skip("Live Supabase credentials not set in environment")
    from supabase import create_client
    url = os.environ["SUPABASE_URL"]
    key = os.environ["SUPABASE_SERVICE_ROLE_KEY"]
    client = create_client(url, key)
    return client


def test_live_database_connectivity(live_client):
    res = live_client.table("cases").select("id").limit(1).execute()
    assert res is not None


def test_live_guest_session_lifecycle(live_client):
    service = GuestService(client=live_client)
    session = service.create_session()

    assert session.guest_session_id is not None
    assert session.session_token.startswith("gst_")

    # Verify query by token
    validated = service.validate_session(session.session_token)
    assert validated is not None
    assert str(validated["id"]) == str(session.guest_session_id)

    # Cleanup test session
    live_client.table("guest_sessions").delete().eq("id", str(session.guest_session_id)).execute()


def test_live_xor_check_constraint(live_client):
    # 1. Neither user_id nor guest_session_id -> Check constraint violation
    invalid_case_id = str(uuid.uuid4())
    with pytest.raises(Exception):
        live_client.table("cases").insert({
            "id": invalid_case_id,
            "title": "Invalid XOR Neither Case",
            "user_id": None,
            "guest_session_id": None,
            "status": "draft",
            "retention_type": "persistent",
        }).execute()

    # 2. Both user_id and guest_session_id -> Check constraint violation
    with pytest.raises(Exception):
        live_client.table("cases").insert({
            "id": invalid_case_id,
            "title": "Invalid XOR Both Case",
            "user_id": str(uuid.uuid4()),
            "guest_session_id": str(uuid.uuid4()),
            "status": "draft",
            "retention_type": "temporary",
            "expires_at": (datetime.now(timezone.utc) + timedelta(hours=24)).isoformat(),
        }).execute()


def test_live_retention_check_constraints(live_client):
    # Temporary case without expires_at -> Check constraint violation
    temp_case_id = str(uuid.uuid4())
    with pytest.raises(Exception):
        live_client.table("cases").insert({
            "id": temp_case_id,
            "title": "Invalid Temp Case",
            "guest_session_id": str(uuid.uuid4()),
            "status": "draft",
            "retention_type": "temporary",
            "expires_at": None,
        }).execute()


def test_live_updated_at_trigger(live_client):
    guest_service = GuestService(client=live_client)
    session = guest_service.create_session()
    case_service = CaseService(client=live_client)
    caller = CallerContext(guest_session_id=str(session.guest_session_id), role="guest")

    case = case_service.create_case(
        CaseCreateRequest(title="Initial Title"),
        caller,
    )
    initial_updated_at = case.updated_at

    # Update title
    updated_case = case_service.update_case(
        str(case.id),
        CaseUpdateRequest(title="Updated Title"),
        caller,
    )
    assert updated_case.title == "Updated Title"
    assert updated_case.updated_at >= initial_updated_at

    # Cleanup
    case_service.delete_case(str(case.id), caller)
    live_client.table("guest_sessions").delete().eq("id", str(session.guest_session_id)).execute()


def test_live_storage_upload_signed_url_and_cleanup(live_client):
    bucket = "legal-case-documents"
    case_id = str(uuid.uuid4())
    doc_id = str(uuid.uuid4())
    storage_path = f"cases/{case_id}/{doc_id}/original.pdf"
    pdf_bytes = b"%PDF-1.4 live test document payload"

    # 1. Upload to private bucket
    upload_res = live_client.storage.from_(bucket).upload(
        path=storage_path,
        file=pdf_bytes,
        file_options={"content-type": "application/pdf"},
    )
    assert upload_res is not None

    # 2. Generate signed URL
    signed_url_data = live_client.storage.from_(bucket).create_signed_url(
        path=storage_path,
        expires_in=settings.signed_url_expiration_seconds,
    )
    signed_url = signed_url_data.get("signedURL") or signed_url_data.get("signedUrl")
    assert signed_url is not None
    assert "token=" in signed_url

    # 3. Cleanup storage object
    remove_res = live_client.storage.from_(bucket).remove([storage_path])
    assert remove_res is not None
