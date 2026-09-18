"""Unit tests for CallerContext dependency resolution and endpoint authorization."""

from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch
import uuid
from fastapi import HTTPException
import pytest

from backend.app.api.deps import get_caller_context, require_authenticated_caller
from backend.app.core.auth import SupabaseJWTVerifier


def _create_query_mock(data_list):
    builder = MagicMock()
    builder.eq.return_value = builder
    builder.select.return_value = builder
    builder.order.return_value = builder
    builder.update.return_value = builder
    builder.execute.return_value = MagicMock(data=data_list)
    return builder


def test_caller_context_rejects_ambiguous_credentials():
    # Both Bearer JWT and X-Guest-Session-ID present -> 400 AMBIGUOUS_OWNERSHIP_CONTEXT
    with pytest.raises(HTTPException) as exc:
        get_caller_context(
            authorization="Bearer test_token",
            x_guest_session_id="gst_12345",
        )
    assert exc.value.status_code == 400
    assert exc.value.detail["error"]["code"] == "AMBIGUOUS_OWNERSHIP_CONTEXT"


def test_caller_context_rejects_missing_credentials():
    # Neither credential present -> 401 UNAUTHORIZED
    with pytest.raises(HTTPException) as exc:
        get_caller_context(
            authorization=None,
            x_guest_session_id=None,
        )
    assert exc.value.status_code == 401
    assert exc.value.detail["error"]["code"] == "UNAUTHORIZED"


def test_caller_context_valid_jwt():
    user_id = str(uuid.uuid4())
    mock_verifier = MagicMock(spec=SupabaseJWTVerifier)
    mock_verifier.verify_token.return_value = {
        "sub": user_id,
        "email": "lawyer@example.com",
        "aud": "authenticated",
    }

    with patch("backend.app.api.deps.get_jwt_verifier", return_value=mock_verifier):
        caller = get_caller_context(
            authorization="Bearer valid.signed.jwt",
            x_guest_session_id=None,
        )

    assert caller.is_authenticated is True
    assert caller.is_guest is False
    assert caller.user_id == user_id
    assert caller.guest_session_id is None
    assert caller.role == "authenticated"


def test_caller_context_valid_guest_session():
    session_id = str(uuid.uuid4())
    mock_client = MagicMock()
    now = datetime.now(timezone.utc)
    exp = now + timedelta(hours=24)

    mock_client.table().select.return_value = _create_query_mock([
        {"id": session_id, "expires_at": exp.isoformat()}
    ])
    mock_client.table().update.return_value = _create_query_mock([])

    caller = get_caller_context(
        authorization=None,
        x_guest_session_id="gst_validtoken",
        client=mock_client,
    )

    assert caller.is_guest is True
    assert caller.is_authenticated is False
    assert caller.guest_session_id == session_id
    assert caller.user_id is None
    assert caller.role == "guest"


def test_caller_context_rejects_expired_guest_session():
    session_id = str(uuid.uuid4())
    mock_client = MagicMock()
    now = datetime.now(timezone.utc)
    exp = now - timedelta(hours=1)  # expired

    mock_client.table().select.return_value = _create_query_mock([
        {"id": session_id, "expires_at": exp.isoformat()}
    ])

    with pytest.raises(HTTPException) as exc:
        get_caller_context(
            authorization=None,
            x_guest_session_id="gst_expiredtoken",
            client=mock_client,
        )

    assert exc.value.status_code == 401
    assert exc.value.detail["error"]["code"] == "UNAUTHORIZED"
    assert "expired" in exc.value.detail["error"]["message"].lower()


def test_require_authenticated_caller():
    user_id = str(uuid.uuid4())
    mock_verifier = MagicMock(spec=SupabaseJWTVerifier)
    mock_verifier.verify_token.return_value = {"sub": user_id}

    # Missing header -> 401
    with pytest.raises(HTTPException) as exc1:
        require_authenticated_caller(authorization=None)
    assert exc1.value.status_code == 401

    # Valid header -> caller context
    with patch("backend.app.api.deps.get_jwt_verifier", return_value=mock_verifier):
        caller = require_authenticated_caller(authorization="Bearer valid.jwt")
    assert caller.is_authenticated is True
    assert caller.user_id == user_id


def test_caller_context_rejects_fake_and_invalid_tokens():
    # Attempting to supply unverified fake tokens like 'fake_jwt_token' without mock verifier must raise 401
    with pytest.raises(HTTPException) as exc:
        get_caller_context(authorization="Bearer fake_jwt_token")
    assert exc.value.status_code == 401
    assert exc.value.detail["error"]["code"] == "INVALID_CREDENTIALS"


def test_resolve_ownership_context_rejects_fake_tokens():
    from backend.app.services.case_service import resolve_ownership_context

    with pytest.raises(HTTPException) as exc:
        resolve_ownership_context(authorization="Bearer fake_jwt")
    assert exc.value.status_code == 401
    assert exc.value.detail["error"]["code"] == "INVALID_CREDENTIALS"


def test_auth_me_rejects_unverified_token():
    from backend.app.api.v1.auth import get_current_user

    with pytest.raises(HTTPException) as exc:
        get_current_user(authorization="Bearer fake_jwt_token")
    assert exc.value.status_code == 401
    assert exc.value.detail["error"]["code"] == "INVALID_CREDENTIALS"

