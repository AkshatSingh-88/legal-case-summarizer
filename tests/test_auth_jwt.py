"""Unit tests for SupabaseJWTVerifier enforcing ES256 algorithm safety and claims validation."""

from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock
import uuid
from cryptography.hazmat.primitives.asymmetric import ec
from fastapi import HTTPException
import jwt
import pytest

from backend.app.config import Settings
from backend.app.core.auth import SupabaseJWTVerifier


@pytest.fixture
def ec_key_pair():
    """Generates an ephemeral EC key pair for testing ES256 tokens."""
    private_key = ec.generate_private_key(ec.SECP256R1())
    public_key = private_key.public_key()
    kid = "test-key-id-12345"
    return private_key, public_key, kid


@pytest.fixture
def jwt_verifier(ec_key_pair):
    _, public_key, kid = ec_key_pair
    mock_jwk_client = MagicMock()
    mock_signing_key = MagicMock()
    mock_signing_key.key = public_key
    mock_jwk_client.get_signing_key_from_jwt.return_value = mock_signing_key

    settings = Settings(
        supabase_url="https://testproject.supabase.co",
        supabase_jwt_algorithm="ES256",
        supabase_jwt_audience="authenticated",
        supabase_jwt_leeway_seconds=10,
    )
    return SupabaseJWTVerifier(settings=settings, jwks_client=mock_jwk_client)


def _mint_token(private_key, kid, claims, alg="ES256", headers=None):
    hdrs = {"kid": kid, "alg": alg}
    if headers:
        hdrs.update(headers)
    return jwt.encode(claims, private_key, algorithm=alg, headers=hdrs)


def test_jwt_verification_valid_es256(jwt_verifier, ec_key_pair):
    private_key, _, kid = ec_key_pair
    user_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc)
    claims = {
        "sub": user_id,
        "email": "attorney@example.com",
        "aud": "authenticated",
        "iss": "https://testproject.supabase.co/auth/v1",
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(hours=1)).timestamp()),
    }
    token = _mint_token(private_key, kid, claims)

    payload = jwt_verifier.verify_token(token)
    assert payload["sub"] == user_id
    assert payload["email"] == "attorney@example.com"
    assert payload["aud"] == "authenticated"


def test_jwt_rejects_algorithm_mismatch_hs256(jwt_verifier):
    # Attempting to forge token with symmetric HS256 must be rejected immediately
    claims = {
        "sub": str(uuid.uuid4()),
        "aud": "authenticated",
        "exp": int((datetime.now(timezone.utc) + timedelta(hours=1)).timestamp()),
    }
    hs256_token = jwt.encode(claims, "fake-secret-key-12345-32-bytes-ok!", algorithm="HS256", headers={"kid": "k1", "alg": "HS256"})

    with pytest.raises(HTTPException) as exc_info:
        jwt_verifier.verify_token(hs256_token)

    assert exc_info.value.status_code == 401
    assert exc_info.value.detail["error"]["code"] == "INVALID_CREDENTIALS"
    assert "Invalid token algorithm: HS256" in exc_info.value.detail["error"]["message"]


def test_jwt_rejects_algorithm_none(jwt_verifier):
    # Unsigned 'none' algorithm must be rejected
    claims = {
        "sub": str(uuid.uuid4()),
        "aud": "authenticated",
        "exp": int((datetime.now(timezone.utc) + timedelta(hours=1)).timestamp()),
    }
    token_none = jwt.encode(claims, key="", algorithm="none", headers={"alg": "none"})

    with pytest.raises(HTTPException) as exc_info:
        jwt_verifier.verify_token(token_none)

    assert exc_info.value.status_code == 401
    assert exc_info.value.detail["error"]["code"] == "INVALID_CREDENTIALS"


def test_jwt_rejects_expired_token(jwt_verifier, ec_key_pair):
    private_key, _, kid = ec_key_pair
    now = datetime.now(timezone.utc)
    # Expired 30 seconds ago (exceeding 10s leeway)
    claims = {
        "sub": str(uuid.uuid4()),
        "aud": "authenticated",
        "iss": "https://testproject.supabase.co/auth/v1",
        "exp": int((now - timedelta(seconds=30)).timestamp()),
    }
    token = _mint_token(private_key, kid, claims)

    with pytest.raises(HTTPException) as exc_info:
        jwt_verifier.verify_token(token)

    assert exc_info.value.status_code == 401
    assert exc_info.value.detail["error"]["code"] == "INVALID_CREDENTIALS"
    assert "expired" in exc_info.value.detail["error"]["message"].lower()


def test_jwt_rejects_audience_mismatch(jwt_verifier, ec_key_pair):
    private_key, _, kid = ec_key_pair
    now = datetime.now(timezone.utc)
    claims = {
        "sub": str(uuid.uuid4()),
        "aud": "wrong-audience",
        "iss": "https://testproject.supabase.co/auth/v1",
        "exp": int((now + timedelta(hours=1)).timestamp()),
    }
    token = _mint_token(private_key, kid, claims)

    with pytest.raises(HTTPException) as exc_info:
        jwt_verifier.verify_token(token)

    assert exc_info.value.status_code == 401
    assert exc_info.value.detail["error"]["code"] == "INVALID_CREDENTIALS"


def test_jwt_rejects_issuer_mismatch(jwt_verifier, ec_key_pair):
    private_key, _, kid = ec_key_pair
    now = datetime.now(timezone.utc)
    claims = {
        "sub": str(uuid.uuid4()),
        "aud": "authenticated",
        "iss": "https://evil-unauthorized-issuer.com/auth/v1",
        "exp": int((now + timedelta(hours=1)).timestamp()),
    }
    token = _mint_token(private_key, kid, claims)

    with pytest.raises(HTTPException) as exc_info:
        jwt_verifier.verify_token(token)

    assert exc_info.value.status_code == 401
    assert exc_info.value.detail["error"]["code"] == "INVALID_CREDENTIALS"
    assert "issuer" in exc_info.value.detail["error"]["message"].lower()


def test_jwt_rejects_issuer_sharing_prefix_with_suffix(jwt_verifier, ec_key_pair):
    # Tests that an issuer sharing the expected prefix with extra suffix (e.g. /extra) is strictly rejected
    private_key, _, kid = ec_key_pair
    now = datetime.now(timezone.utc)
    claims = {
        "sub": str(uuid.uuid4()),
        "aud": "authenticated",
        "iss": "https://testproject.supabase.co/auth/v1/unexpected_suffix",
        "exp": int((now + timedelta(hours=1)).timestamp()),
    }
    token = _mint_token(private_key, kid, claims)

    with pytest.raises(HTTPException) as exc_info:
        jwt_verifier.verify_token(token)

    assert exc_info.value.status_code == 401
    assert exc_info.value.detail["error"]["code"] == "INVALID_CREDENTIALS"
    assert "issuer" in exc_info.value.detail["error"]["message"].lower()


def test_jwt_rejects_missing_issuer_claim(jwt_verifier, ec_key_pair):
    # Tests that a token completely lacking the 'iss' claim is strictly rejected with 401
    private_key, _, kid = ec_key_pair
    now = datetime.now(timezone.utc)
    claims = {
        "sub": str(uuid.uuid4()),
        "aud": "authenticated",
        # iss intentionally omitted
        "exp": int((now + timedelta(hours=1)).timestamp()),
    }
    token = _mint_token(private_key, kid, claims)

    with pytest.raises(HTTPException) as exc_info:
        jwt_verifier.verify_token(token)

    assert exc_info.value.status_code == 401
    assert exc_info.value.detail["error"]["code"] == "INVALID_CREDENTIALS"
    assert "missing issuer claim" in exc_info.value.detail["error"]["message"].lower()


def test_jwt_rejects_non_uuid_subject(jwt_verifier, ec_key_pair):
    private_key, _, kid = ec_key_pair
    now = datetime.now(timezone.utc)
    claims = {
        "sub": "not-a-valid-uuid",
        "aud": "authenticated",
        "iss": "https://testproject.supabase.co/auth/v1",
        "exp": int((now + timedelta(hours=1)).timestamp()),
    }
    token = _mint_token(private_key, kid, claims)

    with pytest.raises(HTTPException) as exc_info:
        jwt_verifier.verify_token(token)

    assert exc_info.value.status_code == 401
    assert "UUID" in exc_info.value.detail["error"]["message"]


def test_jwt_rejects_empty_or_malformed_string(jwt_verifier):
    with pytest.raises(HTTPException) as exc1:
        jwt_verifier.verify_token("")
    assert exc1.value.status_code == 401

    with pytest.raises(HTTPException) as exc2:
        jwt_verifier.verify_token("definitely.not.a.jwt")
    assert exc2.value.status_code == 401
