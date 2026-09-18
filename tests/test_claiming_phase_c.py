"""Focused tests for Phase C — Part 2: Guest Case Claiming."""

from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch
import uuid
from fastapi import HTTPException
from fastapi.testclient import TestClient
import pytest

from backend.app.api.schemas.case import CaseClaimResponse, RetentionType
from backend.app.core.auth import SupabaseJWTVerifier
from backend.app.main import app
from backend.app.services.case_service import CaseService, get_case_service
from backend.app.services.models import CallerContext

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


@pytest.fixture
def mock_supabase_for_claim():
    mock_client = MagicMock()
    session_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc)
    exp = now + timedelta(hours=24)

    # Mock guest_sessions table query
    def fake_table(name):
        tbl = MagicMock()
        if name == "guest_sessions":
            tbl.select.return_value = _mock_query_builder([
                {"id": session_id, "expires_at": exp.isoformat()}
            ])
            tbl.update.return_value = _mock_query_builder([])
        return tbl

    mock_client.table.side_effect = fake_table
    return mock_client, session_id


# =========================================================================
# 1. Service Layer Tests: claim_case & RPC result mapping
# =========================================================================

def test_claim_case_success(mock_supabase_for_claim):
    mock_client, session_id = mock_supabase_for_claim
    case_id = str(uuid.uuid4())
    user_id = str(uuid.uuid4())
    caller = CallerContext(user_id=user_id, role="authenticated")
    now_iso = datetime.now(timezone.utc).isoformat()

    mock_client.rpc.return_value.execute.return_value = MagicMock(data={
        "code": "OK",
        "case_id": case_id,
        "user_id": user_id,
        "claimed_at": now_iso,
        "retention_type": "persistent",
    })

    svc = CaseService(client=mock_client)
    res = svc.claim_case(
        case_id=case_id,
        caller=caller,
        guest_token="gst_dummyvalidtoken12345",
    )

    assert isinstance(res, CaseClaimResponse)
    assert str(res.case_id) == case_id
    assert str(res.user_id) == user_id
    assert res.retention_type == RetentionType.PERSISTENT
    # Verify RPC arguments passed to client
    mock_client.rpc.assert_called_once_with(
        "claim_guest_case",
        {
            "p_case_id": case_id,
            "p_guest_session_id": session_id,
            "p_user_id": user_id,
        },
    )


def test_claim_case_missing_jwt_rejected(mock_supabase_for_claim):
    mock_client, _ = mock_supabase_for_claim
    case_id = str(uuid.uuid4())
    svc = CaseService(client=mock_client)

    # Caller is guest, not authenticated
    guest_caller = CallerContext(guest_session_id=str(uuid.uuid4()), role="guest")
    with pytest.raises(HTTPException) as exc:
        svc.claim_case(case_id=case_id, caller=guest_caller, guest_token="gst_token")
    assert exc.value.status_code == 401
    assert exc.value.detail["error"]["code"] == "UNAUTHORIZED"


def test_claim_case_missing_guest_token_rejected(mock_supabase_for_claim):
    mock_client, _ = mock_supabase_for_claim
    case_id = str(uuid.uuid4())
    user_id = str(uuid.uuid4())
    caller = CallerContext(user_id=user_id, role="authenticated")
    svc = CaseService(client=mock_client)

    with pytest.raises(HTTPException) as exc:
        svc.claim_case(case_id=case_id, caller=caller, guest_token="")
    assert exc.value.status_code == 400
    assert exc.value.detail["error"]["code"] == "MISSING_GUEST_CREDENTIAL"


def test_claim_case_expired_guest_token_rejected():
    mock_client = MagicMock()
    now = datetime.now(timezone.utc)
    past = now - timedelta(hours=2)

    # Guest session expired in DB
    mock_client.table.return_value.select.return_value = _mock_query_builder([
        {"id": str(uuid.uuid4()), "expires_at": past.isoformat()}
    ])

    case_id = str(uuid.uuid4())
    user_id = str(uuid.uuid4())
    caller = CallerContext(user_id=user_id, role="authenticated")
    svc = CaseService(client=mock_client)

    with pytest.raises(HTTPException) as exc:
        svc.claim_case(case_id=case_id, caller=caller, guest_token="gst_expired")
    assert exc.value.status_code == 401
    assert exc.value.detail["error"]["code"] == "UNAUTHORIZED"


def test_claim_case_not_found_mapped_to_404(mock_supabase_for_claim):
    mock_client, session_id = mock_supabase_for_claim
    case_id = str(uuid.uuid4())
    user_id = str(uuid.uuid4())
    caller = CallerContext(user_id=user_id, role="authenticated")

    mock_client.rpc.return_value.execute.return_value = MagicMock(data={
        "code": "CASE_NOT_FOUND",
    })

    svc = CaseService(client=mock_client)
    with pytest.raises(HTTPException) as exc:
        svc.claim_case(case_id=case_id, caller=caller, guest_token="gst_valid")
    assert exc.value.status_code == 404
    assert exc.value.detail["error"]["code"] == "RESOURCE_NOT_FOUND"


def test_claim_case_ownership_mismatch_mapped_to_409(mock_supabase_for_claim):
    mock_client, session_id = mock_supabase_for_claim
    case_id = str(uuid.uuid4())
    user_id = str(uuid.uuid4())
    caller = CallerContext(user_id=user_id, role="authenticated")

    mock_client.rpc.return_value.execute.return_value = MagicMock(data={
        "code": "GUEST_OWNERSHIP_MISMATCH",
    })

    svc = CaseService(client=mock_client)
    with pytest.raises(HTTPException) as exc:
        svc.claim_case(case_id=case_id, caller=caller, guest_token="gst_valid")
    assert exc.value.status_code == 409
    assert exc.value.detail["error"]["code"] == "CLAIM_OWNERSHIP_MISMATCH"


def test_claim_case_already_claimed_by_self_mapped_to_409(mock_supabase_for_claim):
    mock_client, session_id = mock_supabase_for_claim
    case_id = str(uuid.uuid4())
    user_id = str(uuid.uuid4())
    caller = CallerContext(user_id=user_id, role="authenticated")

    mock_client.rpc.return_value.execute.return_value = MagicMock(data={
        "code": "ALREADY_CLAIMED_BY_SELF",
    })

    svc = CaseService(client=mock_client)
    with pytest.raises(HTTPException) as exc:
        svc.claim_case(case_id=case_id, caller=caller, guest_token="gst_valid")
    assert exc.value.status_code == 409
    assert exc.value.detail["error"]["code"] == "CASE_ALREADY_CLAIMED"


def test_claim_case_already_claimed_by_other_mapped_to_409(mock_supabase_for_claim):
    mock_client, session_id = mock_supabase_for_claim
    case_id = str(uuid.uuid4())
    user_id = str(uuid.uuid4())
    caller = CallerContext(user_id=user_id, role="authenticated")

    mock_client.rpc.return_value.execute.return_value = MagicMock(data={
        "code": "ALREADY_CLAIMED_BY_OTHER",
    })

    svc = CaseService(client=mock_client)
    with pytest.raises(HTTPException) as exc:
        svc.claim_case(case_id=case_id, caller=caller, guest_token="gst_valid")
    assert exc.value.status_code == 409
    assert exc.value.detail["error"]["code"] == "CASE_ALREADY_CLAIMED"


# =========================================================================
# 2. Authoritative API Route Tests: POST /api/v1/cases/{case_id}/claim
# =========================================================================

def test_api_claim_route_success(monkeypatch):
    case_id = str(uuid.uuid4())
    user_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc)

    # Mock JWT verifier
    mock_verifier = MagicMock(spec=SupabaseJWTVerifier)
    mock_verifier.verify_token.return_value = {"sub": user_id, "aud": "authenticated"}
    monkeypatch.setattr("backend.app.api.deps.get_jwt_verifier", lambda: mock_verifier)

    # Mock CaseService
    mock_service = MagicMock(spec=CaseService)
    mock_service.claim_case.return_value = CaseClaimResponse(
        case_id=uuid.UUID(case_id),
        user_id=uuid.UUID(user_id),
        claimed_at=now,
        retention_type=RetentionType.PERSISTENT,
    )
    monkeypatch.setattr("backend.app.api.v1.cases.get_case_service", lambda: mock_service)

    res = client.post(
        f"/api/v1/cases/{case_id}/claim",
        headers={
            "Authorization": "Bearer real.signed.jwt",
            "X-Guest-Session-ID": "gst_sampletoken123",
        },
    )

    assert res.status_code == 200
    data = res.json()
    assert data["case_id"] == case_id
    assert data["user_id"] == user_id
    assert data["retention_type"] == "persistent"
    # Ensure no previous_guest_session_id is exposed in client response
    assert "previous_guest_session_id" not in data


def test_api_claim_route_missing_jwt_returns_401(monkeypatch):
    case_id = str(uuid.uuid4())
    res = client.post(
        f"/api/v1/cases/{case_id}/claim",
        headers={"X-Guest-Session-ID": "gst_sampletoken"},
    )
    assert res.status_code == 401


def test_api_claim_route_missing_guest_header_returns_400(monkeypatch):
    case_id = str(uuid.uuid4())
    user_id = str(uuid.uuid4())

    mock_verifier = MagicMock(spec=SupabaseJWTVerifier)
    mock_verifier.verify_token.return_value = {"sub": user_id}
    monkeypatch.setattr("backend.app.api.deps.get_jwt_verifier", lambda: mock_verifier)

    res = client.post(
        f"/api/v1/cases/{case_id}/claim",
        headers={"Authorization": "Bearer real.signed.jwt"},
    )
    assert res.status_code == 400
    assert res.json()["detail"]["error"]["code"] == "MISSING_GUEST_CREDENTIAL"


def test_api_claim_route_user_id_in_payload_is_ignored(monkeypatch):
    # Request-supplied user_id in body must NOT override verified JWT identity
    case_id = str(uuid.uuid4())
    verified_user_id = str(uuid.uuid4())
    attacker_user_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc)

    mock_verifier = MagicMock(spec=SupabaseJWTVerifier)
    mock_verifier.verify_token.return_value = {"sub": verified_user_id}
    monkeypatch.setattr("backend.app.api.deps.get_jwt_verifier", lambda: mock_verifier)

    mock_service = MagicMock(spec=CaseService)
    mock_service.claim_case.return_value = CaseClaimResponse(
        case_id=uuid.UUID(case_id),
        user_id=uuid.UUID(verified_user_id),
        claimed_at=now,
        retention_type=RetentionType.PERSISTENT,
    )
    monkeypatch.setattr("backend.app.api.v1.cases.get_case_service", lambda: mock_service)

    # Post with malicious user_id in body
    res = client.post(
        f"/api/v1/cases/{case_id}/claim",
        json={"user_id": attacker_user_id},
        headers={
            "Authorization": "Bearer real.signed.jwt",
            "X-Guest-Session-ID": "gst_sampletoken",
        },
    )

    assert res.status_code == 200
    # Service was called with verified caller, not attacker_user_id
    caller_arg = mock_service.claim_case.call_args[1]["caller"]
    assert caller_arg.user_id == verified_user_id
    assert caller_arg.user_id != attacker_user_id


# =========================================================================
# 3. Deprecated Route Tests: POST /api/v1/guest/claim
# =========================================================================

def test_deprecated_guest_claim_performs_zero_db_mutations(monkeypatch):
    mock_client = MagicMock()
    monkeypatch.setattr("backend.app.core.supabase.get_supabase_client", lambda: mock_client)

    res = client.post(
        "/api/v1/guest/claim",
        headers={
            "Authorization": "Bearer any_token",
            "X-Guest-Session-ID": "gst_anysession",
        },
    )

    assert res.status_code == 200
    assert res.headers.get("Deprecation") == "true"
    # Ensure zero DB calls were made
    assert mock_client.table.call_count == 0
    assert mock_client.rpc.call_count == 0


# =========================================================================
# 4. Concurrency & Atomicity Simulation Tests
# =========================================================================

def test_simulated_concurrent_claims_lock_and_conflict():
    # Note: Actual PostgreSQL row-level locking (SELECT ... FOR UPDATE) is enforced at the database level.
    # This unit test simulates the race condition window:
    # First claim succeeds with 'OK'; second concurrent claim encounters row lock and returns 'ALREADY_CLAIMED_BY_SELF' or 'ALREADY_CLAIMED_BY_OTHER'
    mock_client = MagicMock()
    case_id = str(uuid.uuid4())
    user_id_1 = str(uuid.uuid4())
    user_id_2 = str(uuid.uuid4())
    session_id = str(uuid.uuid4())
    now_iso = datetime.now(timezone.utc).isoformat()

    # Setup session lookup
    mock_client.table.return_value.select.return_value = _mock_query_builder([
        {"id": session_id, "expires_at": (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()}
    ])

    # First claim call response
    mock_client.rpc.return_value.execute.side_effect = [
        MagicMock(data={"code": "OK", "case_id": case_id, "user_id": user_id_1, "claimed_at": now_iso}),
        MagicMock(data={"code": "ALREADY_CLAIMED_BY_OTHER"}),
    ]

    svc = CaseService(client=mock_client)

    # Claim 1 by user 1
    res1 = svc.claim_case(case_id, CallerContext(user_id=user_id_1, role="authenticated"), "gst_token")
    assert str(res1.user_id) == user_id_1

    # Claim 2 by user 2 on the locked/updated row
    with pytest.raises(HTTPException) as exc:
        svc.claim_case(case_id, CallerContext(user_id=user_id_2, role="authenticated"), "gst_token")
    assert exc.value.status_code == 409
    assert exc.value.detail["error"]["code"] == "CASE_ALREADY_CLAIMED"


# =========================================================================
# 5. RPC Migration & Permissions Verification (Static & Structural)
# =========================================================================

def test_rpc_migration_sql_permissions_and_invariants():
    """Statically verifies 20260918123000_claim_guest_case_rpc.sql guarantees:
    - SECURITY DEFINER
    - SET search_path = public, pg_temp
    - Execution revoked from PUBLIC, anon, and authenticated
    - Execution granted strictly to service_role
    - In-transaction session expiration check
    - Explicit row lock (FOR UPDATE)
    """
    from pathlib import Path
    migration_file = Path("supabase/migrations/20260918123000_claim_guest_case_rpc.sql")
    assert migration_file.exists(), "RPC migration file must exist"
    sql = migration_file.read_text(encoding="utf-8")

    # Invariant: SECURITY DEFINER with fixed search_path
    assert "SECURITY DEFINER" in sql
    assert "SET search_path = public, pg_temp" in sql

    # Invariant: revokes from unprivileged roles
    assert "REVOKE ALL ON FUNCTION claim_guest_case(UUID, UUID, UUID) FROM PUBLIC;" in sql
    assert "REVOKE ALL ON FUNCTION claim_guest_case(UUID, UUID, UUID) FROM anon;" in sql
    assert "REVOKE ALL ON FUNCTION claim_guest_case(UUID, UUID, UUID) FROM authenticated;" in sql

    # Invariant: granted strictly to service_role
    assert "GRANT EXECUTE ON FUNCTION claim_guest_case(UUID, UUID, UUID) TO service_role;" in sql

    # Invariant: Concurrency lock
    assert "FOR UPDATE;" in sql

    # Invariant: In-database expiration check
    assert "v_session.expires_at <= v_now" in sql
    assert "'GUEST_SESSION_EXPIRED'" in sql


def test_claim_case_guest_session_expires_during_rpc_race(mock_supabase_for_claim):
    """Verifies that if the guest token passes initial check but the session expires
    before/during the atomic database RPC transaction, the RPC's internal check rejects the claim."""
    mock_client, session_id = mock_supabase_for_claim
    case_id = str(uuid.uuid4())
    user_id = str(uuid.uuid4())
    caller = CallerContext(user_id=user_id, role="authenticated")

    # Simulate: RPC returns GUEST_SESSION_EXPIRED because in-DB check failed
    mock_client.rpc.return_value.execute.return_value = MagicMock(data={
        "code": "GUEST_SESSION_EXPIRED"
    })

    svc = CaseService(client=mock_client)
    with pytest.raises(HTTPException) as exc:
        svc.claim_case(
            case_id=case_id,
            caller=caller,
            guest_token="gst_just_expired_during_transaction",
        )
    assert exc.value.status_code == 401
    assert exc.value.detail["error"]["code"] == "UNAUTHORIZED"
    assert "expired" in exc.value.detail["error"]["message"].lower()


def test_api_claim_route_requires_both_jwt_and_guest_header(monkeypatch):
    """Verifies that the claim endpoint cannot be invoked with only a JWT or only a guest token."""
    # 1. With neither -> 401
    res = client.post(f"/api/v1/cases/{uuid.uuid4()}/claim")
    assert res.status_code == 401

    # 2. With guest token only (no Authorization) -> 401
    res = client.post(
        f"/api/v1/cases/{uuid.uuid4()}/claim",
        headers={"X-Guest-Session-ID": "gst_testtoken"},
    )
    assert res.status_code == 401

    # 3. With Authorization only (no X-Guest-Session-ID) -> 400
    mock_verifier = MagicMock(spec=SupabaseJWTVerifier)
    mock_verifier.verify_token.return_value = {"sub": str(uuid.uuid4()), "role": "authenticated"}
    monkeypatch.setattr("backend.app.api.deps.get_jwt_verifier", lambda: mock_verifier)

    res = client.post(
        f"/api/v1/cases/{uuid.uuid4()}/claim",
        headers={"Authorization": "Bearer valid.mock.jwt"},
    )
    assert res.status_code == 400
    assert res.json()["detail"]["error"]["code"] == "MISSING_GUEST_CREDENTIAL"


