"""Service for persisting and retrieving structured Case Summaries."""

from datetime import datetime, timezone
from typing import Any
from uuid import UUID, uuid4

from fastapi import HTTPException, status

from backend.app.api.schemas.summary import (
    SummaryResponse,
    SummaryStatus,
    SummaryType,
)
from backend.app.core.supabase import get_supabase_client
from backend.app.presentation.models import DetailedAnalysis
from backend.app.services.case_service import CaseService, get_case_service
from backend.app.services.models import CallerContext


def _parse_dt(val: Any) -> datetime | None:
    if val is None:
        return None
    if isinstance(val, datetime):
        return val
    try:
        return datetime.fromisoformat(str(val).replace("Z", "+00:00"))
    except Exception:
        return None


class SummaryService:
    """Manages Case Summary persistence, JSONB serialization, and retrieval."""

    def __init__(self, client: Any = None, case_service: CaseService | None = None):
        self._client = client
        self._case_service = case_service or CaseService(client=self._client)

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

    def upsert_summary(
        self,
        case_id: UUID | str,
        content: DetailedAnalysis,
    ) -> SummaryResponse:
        """Saves or updates a DetailedAnalysis summary for a case.

        Serializes the canonical presentation model into JSONB, setting status to 'ready'.
        """
        client = self._require_client()
        case_id_str = str(case_id)
        now = datetime.now(timezone.utc)

        # Serialize Pydantic V2 model to JSON-safe dictionary
        if isinstance(content, DetailedAnalysis):
            content_dict = content.model_dump(mode="json")
        elif isinstance(content, dict):
            content_dict = content
        else:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail={
                    "error": {
                        "code": "SUMMARY_SERIALIZATION_ERROR",
                        "message": "Invalid summary content type for serialization.",
                        "details": {"type": type(content).__name__},
                    }
                },
            )

        # Check if row exists for this case (one summary per case constraint)
        existing = client.table("summaries").select("*").eq("case_id", case_id_str).execute()

        if existing.data:
            summary_id = existing.data[0]["id"]
            update_data = {
                "summary_type": SummaryType.DETAILED.value,
                "status": SummaryStatus.READY.value,
                "content": content_dict,
                "updated_at": now.isoformat(),
            }
            res = client.table("summaries").update(update_data).eq("id", summary_id).execute()
            row = res.data[0] if res.data else existing.data[0]
            # Ensure updated fields in returned row
            row["content"] = content_dict
            row["status"] = SummaryStatus.READY.value
            row["updated_at"] = now.isoformat()
        else:
            summary_id = str(uuid4())
            insert_data = {
                "id": summary_id,
                "case_id": case_id_str,
                "summary_type": SummaryType.DETAILED.value,
                "status": SummaryStatus.READY.value,
                "content": content_dict,
                "created_at": now.isoformat(),
                "updated_at": now.isoformat(),
            }
            res = client.table("summaries").insert(insert_data).execute()
            if not res.data:
                raise RuntimeError(f"Failed to insert summary for case {case_id_str}.")
            row = res.data[0]

        return SummaryResponse(
            id=UUID(str(row["id"])),
            case_id=UUID(case_id_str),
            summary_type=SummaryType.DETAILED,
            status=SummaryStatus.READY,
            content=content if isinstance(content, DetailedAnalysis) else DetailedAnalysis.model_validate(content_dict),
            created_at=_parse_dt(row.get("created_at")) or now,
            updated_at=_parse_dt(row.get("updated_at")) or now,
        )

    def get_summary(
        self,
        case_id: UUID | str,
        caller: CallerContext,
    ) -> SummaryResponse:
        """Retrieves summary for a case, enforcing ownership and deserializing content."""
        client = self._require_client()
        case_id_str = str(case_id)

        # 1. Enforce caller ownership via CaseService
        self._case_service.get_case(case_id_str, caller)

        # 2. Query summaries table
        res = client.table("summaries").select("*").eq("case_id", case_id_str).execute()
        if not res.data:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail={
                    "error": {
                        "code": "SUMMARY_NOT_FOUND",
                        "message": f"Summary not found for case {case_id_str}.",
                        "details": {},
                    }
                },
            )

        row = res.data[0]
        status_val = SummaryStatus(row["status"])
        now = datetime.now(timezone.utc)

        parsed_content: DetailedAnalysis | None = None
        if status_val == SummaryStatus.READY:
            raw_content = row.get("content")
            if raw_content:
                try:
                    parsed_content = DetailedAnalysis.model_validate(raw_content)
                except Exception as exc:
                    raise HTTPException(
                        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                        detail={
                            "error": {
                                "code": "SUMMARY_DESERIALIZATION_ERROR",
                                "message": f"Failed to deserialize stored summary content: {exc}",
                                "details": {},
                            }
                        },
                    )

        return SummaryResponse(
            id=UUID(str(row["id"])),
            case_id=UUID(case_id_str),
            summary_type=SummaryType(row.get("summary_type", SummaryType.DETAILED.value)),
            status=status_val,
            content=parsed_content,
            created_at=_parse_dt(row.get("created_at")) or now,
            updated_at=_parse_dt(row.get("updated_at")) or now,
        )

    def mark_generating(self, case_id: UUID | str) -> None:
        """Sets summary status to 'generating' and clears existing content (used for reprocessing)."""
        client = self._require_client()
        case_id_str = str(case_id)
        now = datetime.now(timezone.utc)

        existing = client.table("summaries").select("id").eq("case_id", case_id_str).execute()
        if existing.data:
            summary_id = existing.data[0]["id"]
            update_data = {
                "status": SummaryStatus.GENERATING.value,
                "content": None,
                "updated_at": now.isoformat(),
            }
            client.table("summaries").update(update_data).eq("id", summary_id).execute()
        else:
            insert_data = {
                "id": str(uuid4()),
                "case_id": case_id_str,
                "summary_type": SummaryType.DETAILED.value,
                "status": SummaryStatus.GENERATING.value,
                "content": None,
                "created_at": now.isoformat(),
                "updated_at": now.isoformat(),
            }
            client.table("summaries").insert(insert_data).execute()

    def mark_failed(self, case_id: UUID | str) -> None:
        """Sets summary status to 'failed' and clears content."""
        client = self._require_client()
        case_id_str = str(case_id)
        now = datetime.now(timezone.utc)

        existing = client.table("summaries").select("id").eq("case_id", case_id_str).execute()
        if existing.data:
            summary_id = existing.data[0]["id"]
            update_data = {
                "status": SummaryStatus.FAILED.value,
                "content": None,
                "updated_at": now.isoformat(),
            }
            client.table("summaries").update(update_data).eq("id", summary_id).execute()
        else:
            insert_data = {
                "id": str(uuid4()),
                "case_id": case_id_str,
                "summary_type": SummaryType.DETAILED.value,
                "status": SummaryStatus.FAILED.value,
                "content": None,
                "created_at": now.isoformat(),
                "updated_at": now.isoformat(),
            }
            client.table("summaries").insert(insert_data).execute()


def get_summary_service(client: Any = None) -> SummaryService:
    """Factory helper for SummaryService."""
    return SummaryService(client=client)
