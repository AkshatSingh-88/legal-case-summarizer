"""Guest session service handling secure token generation, hashing, and validation."""

from datetime import datetime, timedelta, timezone
import hashlib
import logging
import secrets
from typing import Any
import uuid

from fastapi import HTTPException, status

from backend.app.api.schemas.guest import GuestSessionResponse
from backend.app.config import get_settings
from backend.app.core.supabase import get_supabase_client

logger = logging.getLogger(__name__)


def hash_guest_token(raw_token: str) -> str:
    """Computes SHA-256 hex digest of the raw guest session token."""
    return hashlib.sha256(raw_token.strip().encode("utf-8")).hexdigest()


def _parse_dt(val: Any) -> datetime | None:
    if val is None:
        return None
    if isinstance(val, datetime):
        return val
    if isinstance(val, str):
        v = val.replace("Z", "+00:00")
        try:
            dt = datetime.fromisoformat(v)
        except ValueError:
            import re
            m = re.match(r"^(\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2})(?:\.(\d+))?((?:[+-]\d{2}:?\d{2})|Z)?$", v)
            if m:
                base, frac, tz = m.groups()
                frac_str = f".{frac.ljust(6, '0')[:6]}" if frac else ""
                tz_str = tz if tz else ""
                if tz_str == "Z":
                    tz_str = "+00:00"
                elif tz_str and ":" not in tz_str and len(tz_str) == 5:
                    tz_str = f"{tz_str[:3]}:{tz_str[3:]}"
                dt = datetime.fromisoformat(f"{base}{frac_str}{tz_str}")
            else:
                return None
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    return None


class GuestService:
    def __init__(self, client: Any = None):
        self._client = client

    @property
    def client(self) -> Any:
        if self._client is not None:
            return self._client
        return get_supabase_client()

    def _require_client(self) -> Any:
        client = self.client
        if client is None:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail={
                    "error": {
                        "code": "DATABASE_UNAVAILABLE",
                        "message": "Supabase infrastructure is not configured or unavailable.",
                        "details": {},
                    }
                },
            )
        return client

    def create_session(self) -> GuestSessionResponse:
        """Generates a high-entropy guest token, hashes it for persistence, and returns the response."""
        settings = get_settings()
        raw_token = f"gst_{secrets.token_hex(32)}"
        token_hash = hash_guest_token(raw_token)
        now = datetime.now(timezone.utc)
        expires_at = now + timedelta(hours=settings.guest_session_ttl_hours)

        client = self._require_client()
        res = client.table("guest_sessions").insert({
            "token_hash": token_hash,
            "last_activity_at": now.isoformat(),
            "expires_at": expires_at.isoformat(),
            "created_at": now.isoformat(),
        }).execute()

        if not res.data:
            raise RuntimeError("Failed to persist guest session in database")
        session_row = res.data[0]
        session_id = uuid.UUID(str(session_row["id"]))
        last_activity = _parse_dt(session_row["last_activity_at"]) or now
        exp = _parse_dt(session_row["expires_at"]) or expires_at
        created = _parse_dt(session_row.get("created_at")) or now

        return GuestSessionResponse(
            guest_session_id=session_id,
            session_token=raw_token,
            last_activity_at=last_activity,
            expires_at=exp,
            created_at=created,
        )

    def validate_session(self, raw_token: str) -> dict[str, Any] | None:
        """Validates a guest session token against the database.

        Returns session record dict if valid and unexpired; None otherwise.
        Updates last_activity_at on successful validation.
        """
        if not raw_token or not raw_token.strip():
            return None

        token_hash = hash_guest_token(raw_token)
        client = self.client
        if client is None:
            return None

        try:
            res = client.table("guest_sessions").select("*").eq("token_hash", token_hash).execute()
            if not res.data:
                return None

            session = res.data[0]
            expires_at = _parse_dt(session["expires_at"])
            if expires_at is None:
                return None

            now = datetime.now(timezone.utc)
            if expires_at <= now:
                logger.info("Guest session %s is expired (expired at %s)", session["id"], expires_at)
                return None

            # Update last activity only for valid, unexpired session
            client.table("guest_sessions").update({
                "last_activity_at": now.isoformat()
            }).eq("id", session["id"]).execute()

            return session
        except Exception as e:
            logger.error("Error validating guest session: %s", e)
            return None


_guest_service = GuestService()


def get_guest_service(client: Any = None) -> GuestService:
    if client is not None:
        return GuestService(client)
    return _guest_service
