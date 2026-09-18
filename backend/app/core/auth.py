"""Algorithm-safe JWT verifier for Supabase Auth using JWKS (ES256)."""

import logging
from typing import Any
import uuid

from fastapi import HTTPException, status
import jwt
from jwt import PyJWKClient

from backend.app.config import Settings, get_settings

logger = logging.getLogger(__name__)


class SupabaseJWTVerifier:
    """Verifies Supabase access tokens using project JWKS and strict algorithm enforcement."""

    def __init__(self, settings: Settings | None = None, jwks_client: PyJWKClient | None = None) -> None:
        self.settings = settings or get_settings()
        self._jwks_client = jwks_client

    @property
    def jwks_client(self) -> PyJWKClient:
        if self._jwks_client is None:
            if not self.settings.supabase_url:
                raise HTTPException(
                    status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                    detail={
                        "error": {
                            "code": "AUTH_NOT_CONFIGURED",
                            "message": "Supabase URL is not configured.",
                            "details": {},
                        }
                    },
                )
            base_url = self.settings.supabase_url.rstrip("/")
            jwks_url = f"{base_url}/auth/v1/.well-known/jwks.json"
            self._jwks_client = PyJWKClient(
                uri=jwks_url,
                cache_keys=True,
                cache_jwk_set=True,
                lifespan=self.settings.supabase_jwks_cache_seconds,
            )
        return self._jwks_client

    def verify_token(self, token: str) -> dict[str, Any]:
        """Cryptographically verifies a Supabase JWT and returns validated payload claims.

        Enforces:
        - Algorithm must strictly match configured algorithm (default: ES256).
        - Signature verified against project JWKS.
        - Audience must match configured audience (default: authenticated).
        - Expiration time (with configurable leeway).
        - Issuer prefix matches Supabase auth issuer.
        - Subject must be present and a valid UUID.
        """
        if not token or not token.strip():
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail={
                    "error": {
                        "code": "UNAUTHORIZED",
                        "message": "Bearer token is empty or missing.",
                        "details": {},
                    }
                },
            )

        # 1. Inspect unverified header for algorithm locking
        try:
            unverified_header = jwt.get_unverified_header(token)
        except Exception:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail={
                    "error": {
                        "code": "INVALID_CREDENTIALS",
                        "message": "Malformed token header.",
                        "details": {},
                    }
                },
            )

        token_alg = unverified_header.get("alg")
        expected_alg = self.settings.supabase_jwt_algorithm

        # Strict algorithm lock: reject algorithm confusion (HS256, none, etc.)
        if token_alg != expected_alg:
            logger.warning("Rejected token with mismatched algorithm: %s (expected: %s)", token_alg, expected_alg)
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail={
                    "error": {
                        "code": "INVALID_CREDENTIALS",
                        "message": f"Invalid token algorithm: {token_alg}. Expected: {expected_alg}.",
                        "details": {},
                    }
                },
            )

        # 2. Retrieve signing key from JWKS using kid
        kid = unverified_header.get("kid")
        try:
            signing_key = self.jwks_client.get_signing_key_from_jwt(token)
            key = signing_key.key
        except Exception as e:
            logger.warning("Failed to obtain signing key for kid '%s': %s", kid, e)
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail={
                    "error": {
                        "code": "INVALID_CREDENTIALS",
                        "message": "Invalid token key ID or key not found in JWKS.",
                        "details": {},
                    }
                },
            )

        # 3. Verify signature and claims
        expected_issuer = self.settings.supabase_jwt_issuer or (
            f"{self.settings.supabase_url.rstrip('/')}/auth/v1" if self.settings.supabase_url else None
        )

        try:
            payload = jwt.decode(
                token,
                key=key,
                algorithms=[expected_alg],
                audience=self.settings.supabase_jwt_audience,
                leeway=self.settings.supabase_jwt_leeway_seconds,
                options={
                    "verify_signature": True,
                    "verify_aud": bool(self.settings.supabase_jwt_audience),
                    "verify_exp": True,
                    "require": ["exp", "sub"],
                },
            )
        except jwt.ExpiredSignatureError:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail={
                    "error": {
                        "code": "INVALID_CREDENTIALS",
                        "message": "Token has expired.",
                        "details": {},
                    }
                },
            )
        except jwt.InvalidAudienceError:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail={
                    "error": {
                        "code": "INVALID_CREDENTIALS",
                        "message": "Token audience mismatch.",
                        "details": {},
                    }
                },
            )
        except jwt.PyJWTError as e:
            logger.warning("Token verification failed: %s", type(e).__name__)
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail={
                    "error": {
                        "code": "INVALID_CREDENTIALS",
                        "message": "Token verification failed.",
                        "details": {},
                    }
                },
            )

        # 4. Mandatory Exact Issuer validation
        if expected_issuer:
            iss = payload.get("iss")
            if not iss:
                logger.warning("Token missing mandatory issuer claim (expected '%s')", expected_issuer)
                raise HTTPException(
                    status_code=status.HTTP_401_UNAUTHORIZED,
                    detail={
                        "error": {
                            "code": "INVALID_CREDENTIALS",
                            "message": "Token missing issuer claim.",
                            "details": {},
                        }
                    },
                )
            if iss != expected_issuer:
                logger.warning("Token issuer '%s' does not match expected exact issuer '%s'", iss, expected_issuer)
                raise HTTPException(
                    status_code=status.HTTP_401_UNAUTHORIZED,
                    detail={
                        "error": {
                            "code": "INVALID_CREDENTIALS",
                            "message": "Token issuer mismatch.",
                            "details": {},
                        }
                    },
                )

        # 5. Subject UUID validation
        sub = payload.get("sub")
        if not sub:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail={
                    "error": {
                        "code": "INVALID_CREDENTIALS",
                        "message": "Token missing subject claim.",
                        "details": {},
                    }
                },
            )

        try:
            uuid.UUID(str(sub))
        except ValueError:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail={
                    "error": {
                        "code": "INVALID_CREDENTIALS",
                        "message": "Token subject is not a valid UUID.",
                        "details": {},
                    }
                },
            )

        return payload


_verifier: SupabaseJWTVerifier | None = None


def get_jwt_verifier() -> SupabaseJWTVerifier:
    global _verifier
    if _verifier is None:
        _verifier = SupabaseJWTVerifier()
    return _verifier


def set_jwt_verifier(verifier: SupabaseJWTVerifier | None) -> None:
    """Setter for test fixtures and overrides."""
    global _verifier
    _verifier = verifier
