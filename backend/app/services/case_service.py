"""Case service implementing Case CRUD, ownership resolution, and explicit storage cleanup."""

from datetime import datetime, timedelta, timezone
import logging
from typing import Any
import uuid

from fastapi import HTTPException, status

from backend.app.api.schemas.case import (
    CaseClaimResponse,
    CaseCreateRequest,
    CaseDetailDocumentItem,
    CaseDetailResponse,
    CaseListResponse,
    CaseResponse,
    CaseStatus,
    CaseUpdateRequest,
    RetentionType,
)
from backend.app.config import get_settings
from backend.app.core.supabase import get_supabase_client
from backend.app.services.guest_service import get_guest_service
from backend.app.services.models import CallerContext

logger = logging.getLogger(__name__)


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


def _parse_case_status(val: Any) -> CaseStatus:
    if isinstance(val, CaseStatus):
        return val
    if isinstance(val, str):
        try:
            return CaseStatus(val)
        except ValueError:
            pass
    return CaseStatus.DRAFT


def _parse_retention_type(val: Any) -> RetentionType:
    if isinstance(val, RetentionType):
        return val
    if isinstance(val, str):
        try:
            return RetentionType(val)
        except ValueError:
            pass
    return RetentionType.PERSISTENT


def resolve_ownership_context(
    authorization: str | None = None,
    x_guest_session_id: str | None = None,
    client: Any = None,
) -> CallerContext:
    """Enforces Phase A Ownership Ambiguity Rule and resolves caller identity.

    Rules:
    - Both credentials provided -> 400 Bad Request (AMBIGUOUS_OWNERSHIP_CONTEXT)
    - Neither credential provided -> 401 Unauthorized (UNAUTHORIZED)
    - Valid JWT -> Authenticated caller
    - Valid X-Guest-Session-ID -> Guest caller (validates hash & expiry)
    """
    has_jwt = bool(authorization and authorization.strip().startswith("Bearer "))
    has_guest = bool(x_guest_session_id and x_guest_session_id.strip())

    if has_jwt and has_guest:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "error": {
                    "code": "AMBIGUOUS_OWNERSHIP_CONTEXT",
                    "message": "Ambiguous ownership context. Both Authorization header and X-Guest-Session-ID were provided.",
                    "details": {},
                }
            },
        )

    if not has_jwt and not has_guest:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={
                "error": {
                    "code": "UNAUTHORIZED",
                    "message": "Authentication required. Provide either a Bearer JWT or an X-Guest-Session-ID header.",
                    "details": {},
                }
            },
        )

    if has_jwt:
        token = authorization.strip().split(" ", 1)[1].strip() if authorization else ""
        from backend.app.core.auth import get_jwt_verifier
        verifier = get_jwt_verifier()
        payload = verifier.verify_token(token)
        user_id = str(payload.get("sub"))
        return CallerContext(user_id=user_id, guest_session_id=None, role="authenticated")

    if has_guest:
        guest_service = get_guest_service(client)
        session = guest_service.validate_session(x_guest_session_id or "")
        if not session:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail={
                    "error": {
                        "code": "UNAUTHORIZED",
                        "message": "Invalid or expired guest session credential.",
                        "details": {},
                    }
                },
            )
        return CallerContext(
            user_id=None,
            guest_session_id=str(session["id"]),
            role="guest",
        )

    raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail={"error": {"code": "UNAUTHORIZED", "message": "Unauthorized"}})


class CaseService:
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

    def create_case(self, req: CaseCreateRequest, caller: CallerContext) -> CaseResponse:
        """Persists a new Case respecting the XOR ownership and retention invariants."""
        settings = get_settings()
        now = datetime.now(timezone.utc)

        if caller.is_guest:
            # Invariant: Guest cases must be temporary and have expires_at
            retention_type = "temporary"
            expires_at = now + timedelta(hours=settings.guest_session_ttl_hours)
            user_id = None
            guest_session_id = caller.guest_session_id
        else:
            # Authenticated user
            user_id = caller.user_id
            guest_session_id = None
            retention_type = req.retention_type.value if hasattr(req.retention_type, "value") else str(req.retention_type)
            if retention_type == "temporary":
                expires_at = now + timedelta(hours=settings.guest_session_ttl_hours)
            else:
                expires_at = None

        case_id = str(uuid.uuid4())
        client = self._require_client()

        data_to_insert = {
            "id": case_id,
            "title": req.title,
            "user_id": user_id,
            "guest_session_id": guest_session_id,
            "status": "draft",
            "retention_type": retention_type,
            "expires_at": expires_at.isoformat() if expires_at else None,
        }
        res = client.table("cases").insert(data_to_insert).execute()
        if not res.data:
            raise RuntimeError("Failed to insert case record in database")
        row = res.data[0]
        return CaseResponse(
            id=uuid.UUID(str(row["id"])),
            title=row["title"],
            status=_parse_case_status(row["status"]),
            retention_type=_parse_retention_type(row["retention_type"]),
            created_at=_parse_dt(row["created_at"]) or now,
            updated_at=_parse_dt(row["updated_at"]) or now,
            expires_at=_parse_dt(row.get("expires_at")),
            document_count=0,
        )

    def list_cases(self, caller: CallerContext) -> CaseListResponse:
        """Queries cases belonging to the caller and active (non-expired)."""
        client = self._require_client()
        now = datetime.now(timezone.utc)

        query = client.table("cases").select("*, documents(count)")
        if caller.is_authenticated:
            query = query.eq("user_id", caller.user_id)
        else:
            query = query.eq("guest_session_id", caller.guest_session_id)

        res = query.order("created_at", desc=True).execute()
        items: list[CaseResponse] = []

        for row in (res.data or []):
            exp = _parse_dt(row.get("expires_at"))
            if exp and exp <= now:
                continue  # Invariant: expired cases are inaccessible

            doc_count = 0
            if "documents" in row and isinstance(row["documents"], list) and len(row["documents"]) > 0:
                doc_count = row["documents"][0].get("count", 0)

            items.append(
                CaseResponse(
                    id=uuid.UUID(str(row["id"])),
                    title=row["title"],
                    status=_parse_case_status(row["status"]),
                    retention_type=_parse_retention_type(row["retention_type"]),
                    created_at=_parse_dt(row["created_at"]) or now,
                    updated_at=_parse_dt(row["updated_at"]) or now,
                    expires_at=exp,
                    document_count=doc_count,
                )
            )

        return CaseListResponse(items=items, total=len(items), limit=20, offset=0)

    def get_case(self, case_id: str, caller: CallerContext) -> CaseDetailResponse:
        """Retrieves single case with document summaries, enforcing ownership."""
        client = self._require_client()
        now = datetime.now(timezone.utc)

        query = client.table("cases").select("*").eq("id", case_id)
        if caller.is_authenticated and caller.user_id:
            query = query.eq("user_id", caller.user_id)
        elif caller.is_guest and caller.guest_session_id:
            query = query.eq("guest_session_id", caller.guest_session_id)

        res = query.execute()
        if not res.data:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail={"error": {"code": "CASE_NOT_FOUND", "message": f"Case {case_id} not found or inaccessible.", "details": {}}},
            )

        row = res.data[0]
        exp = _parse_dt(row.get("expires_at"))
        if exp and exp <= now:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail={"error": {"code": "CASE_NOT_FOUND", "message": f"Case {case_id} has expired.", "details": {}}},
            )

        # Query documents for this case
        docs_res = client.table("documents").select("*").eq("case_id", case_id).order("created_at").execute()
        docs: list[CaseDetailDocumentItem] = []
        for d in (docs_res.data or []):
            docs.append(
                CaseDetailDocumentItem(
                    id=uuid.UUID(str(d["id"])),
                    filename=d["filename"],
                    content_type=d["content_type"],
                    file_size=d["file_size"],
                    page_count=d.get("page_count"),
                    processing_status=d.get("processing_status", "uploaded"),
                    created_at=_parse_dt(d["created_at"]) or now,
                )
            )

        return CaseDetailResponse(
            id=uuid.UUID(str(row["id"])),
            title=row["title"],
            status=_parse_case_status(row["status"]),
            retention_type=_parse_retention_type(row["retention_type"]),
            created_at=_parse_dt(row["created_at"]) or now,
            updated_at=_parse_dt(row["updated_at"]) or now,
            expires_at=exp,
            documents=docs,
        )

    def update_case(self, case_id: str, req: CaseUpdateRequest, caller: CallerContext) -> CaseResponse:
        """Updates case title, enforcing ownership."""
        client = self._require_client()
        now = datetime.now(timezone.utc)

        # Verify case exists and caller owns it
        detail = self.get_case(case_id, caller)

        update_payload: dict[str, Any] = {}
        if req.title is not None:
            update_payload["title"] = req.title

        if not update_payload:
            return CaseResponse(
                id=detail.id,
                title=detail.title,
                status=detail.status,
                retention_type=detail.retention_type,
                created_at=detail.created_at,
                updated_at=detail.updated_at,
                expires_at=detail.expires_at,
                document_count=len(detail.documents),
            )

        res = client.table("cases").update(update_payload).eq("id", case_id).execute()
        if not res.data:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail={"error": {"code": "CASE_NOT_FOUND", "message": f"Case {case_id} not found.", "details": {}}},
            )
        row = res.data[0]
        return CaseResponse(
            id=uuid.UUID(str(row["id"])),
            title=row["title"],
            status=_parse_case_status(row["status"]),
            retention_type=_parse_retention_type(row["retention_type"]),
            created_at=_parse_dt(row["created_at"]) or now,
            updated_at=_parse_dt(row["updated_at"]) or now,
            expires_at=_parse_dt(row.get("expires_at")),
            document_count=len(detail.documents),
        )

    def delete_case(self, case_id: str, caller: CallerContext) -> None:
        """Explicitly cleans up Supabase Storage objects and deletes the database case record."""
        client = self._require_client()

        # 1. Authorize ownership and verify case exists
        self.get_case(case_id, caller)

        # 2. Collect all affected storage paths
        settings = get_settings()
        bucket = settings.supabase_documents_bucket
        docs_res = client.table("documents").select("storage_path").eq("case_id", case_id).execute()
        paths_to_delete = [d["storage_path"] for d in (docs_res.data or []) if d.get("storage_path")]

        # 3. Attempt storage cleanup
        if paths_to_delete:
            try:
                client.storage.from_(bucket).remove(paths_to_delete)
                logger.info("Removed %d storage objects for case %s", len(paths_to_delete), case_id)
            except Exception as e:
                err_msg = str(e).lower()
                # If object is already absent / 404, treat as idempotent cleanup
                if "not found" in err_msg or "404" in err_msg:
                    logger.info("Storage objects already absent for case %s: %s", case_id, e)
                else:
                    logger.error("Fatal Storage deletion error for case %s: %s", case_id, e)
                    raise HTTPException(
                        status_code=status.HTTP_502_BAD_GATEWAY,
                        detail={
                            "error": {
                                "code": "STORAGE_DELETION_FAILED",
                                "message": f"Storage cleanup failed for case {case_id}: {str(e)}",
                                "details": {},
                            }
                        },
                    )

        # 4. Delete database record (cascades to child tables)
        client.table("cases").delete().eq("id", case_id).execute()

    def claim_case(
        self,
        case_id: str,
        caller: CallerContext,
        guest_token: str,
    ) -> CaseClaimResponse:
        """Atomically claims a guest case for an authenticated user via the claim_guest_case RPC.

        Requirements:
        - Authenticated caller with verified user_id (extracted from JWT).
        - Valid guest_token validated via GuestService to obtain guest_session_id.
        - Calls claim_guest_case RPC using the service-role client.
        - Translates RPC result codes into standard API HTTP errors.
        - Returns typed CaseClaimResponse without exposing previous guest session ID.
        """
        if not caller or not caller.is_authenticated or not caller.user_id:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail={
                    "error": {
                        "code": "UNAUTHORIZED",
                        "message": "Valid authenticated bearer credentials required to claim case.",
                        "details": {},
                    }
                },
            )

        if not guest_token or not guest_token.strip():
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail={
                    "error": {
                        "code": "MISSING_GUEST_CREDENTIAL",
                        "message": "X-Guest-Session-ID header is required to claim a guest case.",
                        "details": {},
                    }
                },
            )

        client = self._require_client()
        guest_service = get_guest_service(client)
        session = guest_service.validate_session(guest_token.strip())
        if not session:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail={
                    "error": {
                        "code": "UNAUTHORIZED",
                        "message": "Invalid or expired guest session credential.",
                        "details": {},
                    }
                },
            )

        guest_session_id = str(session["id"])

        try:
            rpc_res = client.rpc(
                "claim_guest_case",
                {
                    "p_case_id": case_id,
                    "p_guest_session_id": guest_session_id,
                    "p_user_id": caller.user_id,
                },
            ).execute()
        except Exception as e:
            logger.error("Error executing claim_guest_case RPC for case %s: %s", case_id, e)
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail={
                    "error": {
                        "code": "DATABASE_ERROR",
                        "message": f"Failed to execute claim operation: {str(e)}",
                        "details": {},
                    }
                },
            )

        data = rpc_res.data if hasattr(rpc_res, "data") else rpc_res
        if not data or not isinstance(data, dict):
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail={
                    "error": {
                        "code": "CLAIM_FAILED",
                        "message": "Claim RPC returned an empty or invalid response.",
                        "details": {},
                    }
                },
            )

        code = data.get("code")

        if code == "CASE_NOT_FOUND":
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail={
                    "error": {
                        "code": "RESOURCE_NOT_FOUND",
                        "message": f"Case with ID '{case_id}' was not found.",
                        "details": {},
                    }
                },
            )
        elif code == "GUEST_SESSION_EXPIRED":
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail={
                    "error": {
                        "code": "UNAUTHORIZED",
                        "message": "Guest session has expired.",
                        "details": {},
                    }
                },
            )
        elif code == "GUEST_SESSION_NOT_FOUND":
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail={
                    "error": {
                        "code": "UNAUTHORIZED",
                        "message": "Guest session record not found.",
                        "details": {},
                    }
                },
            )
        elif code == "ALREADY_CLAIMED_BY_SELF":
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={
                    "error": {
                        "code": "CASE_ALREADY_CLAIMED",
                        "message": "This case has already been claimed by your account.",
                        "details": {},
                    }
                },
            )
        elif code == "ALREADY_CLAIMED_BY_OTHER":
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={
                    "error": {
                        "code": "CASE_ALREADY_CLAIMED",
                        "message": "This case is already claimed by another user.",
                        "details": {},
                    }
                },
            )
        elif code == "GUEST_OWNERSHIP_MISMATCH":
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={
                    "error": {
                        "code": "CLAIM_OWNERSHIP_MISMATCH",
                        "message": "This case does not belong to the supplied guest session.",
                        "details": {},
                    }
                },
            )
        elif code != "OK":
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail={
                    "error": {
                        "code": "CLAIM_REJECTED",
                        "message": f"Claim operation rejected with status: {code}",
                        "details": {},
                    }
                },
            )

        claimed_at = _parse_dt(data.get("claimed_at")) or datetime.now(timezone.utc)

        return CaseClaimResponse(
            case_id=uuid.UUID(str(data["case_id"])),
            user_id=uuid.UUID(str(data["user_id"])),
            claimed_at=claimed_at,
            retention_type=RetentionType.PERSISTENT,
        )


_case_service = CaseService()


def get_case_service(client: Any = None) -> CaseService:
    if client is not None:
        return CaseService(client)
    return _case_service
