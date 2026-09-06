"""Document service implementing multipart upload, limits, private storage, and signed access."""

from datetime import datetime, timedelta, timezone
import logging
from typing import Any
import uuid

from fastapi import HTTPException, UploadFile, status

from backend.app.api.schemas.case import RetentionType
from backend.app.api.schemas.document import (
    DocumentAccessResponse,
    DocumentListResponse,
    DocumentProcessingStatus,
    DocumentResponse,
    DocumentType,
    DocumentUploadResponse,
    DocumentUploadResultItem,
)
from backend.app.api.schemas.error import ErrorDetail
from backend.app.config import get_settings
from backend.app.core.supabase import get_supabase_client
from backend.app.services.case_service import get_case_service
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


class DocumentService:
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

    async def upload_documents(
        self,
        case_id: str,
        files: list[UploadFile],
        caller: CallerContext,
    ) -> DocumentUploadResponse:
        """Processes multipart PDF uploads independently, enforcing per-file and aggregate limits."""
        settings = get_settings()
        client = self._require_client()
        bucket = settings.supabase_documents_bucket
        case_service = get_case_service(client)

        # 1. Authorize case ownership and retrieve case record
        case_detail = case_service.get_case(case_id, caller)
        now = datetime.now(timezone.utc)

        # 2. Query existing documents in the case for aggregate limit checks
        res = client.table("documents").select("file_size").eq("case_id", case_id).execute()
        existing_docs = res.data or []
        existing_count = len(existing_docs)
        existing_total_size = sum(int(d.get("file_size", 0)) for d in existing_docs)

        results: list[DocumentUploadResultItem] = []
        accepted_count = 0
        failed_count = 0

        for file in files:
            filename = file.filename or "unnamed.pdf"

            try:
                content = await file.read()
            except Exception as e:
                logger.error("Failed to read file %s: %s", filename, e)
                results.append(
                    DocumentUploadResultItem(
                        filename=filename,
                        status="failed",
                        error=ErrorDetail(
                            code="READ_ERROR",
                            message=f"Failed to read uploaded file: {str(e)}",
                            details={},
                        ),
                    )
                )
                failed_count += 1
                continue

            file_size = len(content)

            # Validation 1: Empty file
            if file_size == 0:
                results.append(
                    DocumentUploadResultItem(
                        filename=filename,
                        status="failed",
                        error=ErrorDetail(
                            code="EMPTY_FILE",
                            message="Uploaded file is empty (0 bytes).",
                            details={"filename": filename},
                        ),
                    )
                )
                failed_count += 1
                continue

            # Validation 2: PDF magic bytes validation
            if not content.startswith(b"%PDF"):
                results.append(
                    DocumentUploadResultItem(
                        filename=filename,
                        status="failed",
                        error=ErrorDetail(
                            code="INVALID_FILE_TYPE",
                            message="File is not a valid PDF document (missing PDF magic header).",
                            details={"filename": filename},
                        ),
                    )
                )
                failed_count += 1
                continue

            # Validation 3: Per-file size limit
            if file_size > settings.max_upload_file_size:
                results.append(
                    DocumentUploadResultItem(
                        filename=filename,
                        status="failed",
                        error=ErrorDetail(
                            code="FILE_SIZE_EXCEEDED",
                            message=f"File exceeds maximum allowed size of {settings.max_upload_file_size} bytes.",
                            details={"file_size": file_size, "max_size": settings.max_upload_file_size},
                        ),
                    )
                )
                failed_count += 1
                continue

            # Validation 4: Aggregate document count limit
            if existing_count + 1 > settings.max_documents_per_case:
                results.append(
                    DocumentUploadResultItem(
                        filename=filename,
                        status="failed",
                        error=ErrorDetail(
                            code="DOCUMENT_COUNT_EXCEEDED",
                            message=f"Adding this file exceeds the limit of {settings.max_documents_per_case} documents per case.",
                            details={"current_count": existing_count, "limit": settings.max_documents_per_case},
                        ),
                    )
                )
                failed_count += 1
                continue

            # Validation 5: Aggregate case size limit
            if existing_total_size + file_size > settings.max_case_total_size:
                results.append(
                    DocumentUploadResultItem(
                        filename=filename,
                        status="failed",
                        error=ErrorDetail(
                            code="TOTAL_SIZE_EXCEEDED",
                            message=f"Adding this file exceeds total case size limit of {settings.max_case_total_size} bytes.",
                            details={"current_total_size": existing_total_size, "file_size": file_size, "limit": settings.max_case_total_size},
                        ),
                    )
                )
                failed_count += 1
                continue

            # File is valid: generate ID and storage path
            doc_id = str(uuid.uuid4())
            storage_path = f"cases/{case_id}/{doc_id}/original.pdf"

            # 3. Upload binary to private Supabase Storage
            try:
                client.storage.from_(bucket).upload(
                    path=storage_path,
                    file=content,
                    file_options={"content-type": "application/pdf"},
                )
            except Exception as upload_err:
                logger.error("Storage upload error for %s: %s", storage_path, upload_err)
                results.append(
                    DocumentUploadResultItem(
                        filename=filename,
                        status="failed",
                        error=ErrorDetail(
                            code="STORAGE_UPLOAD_FAILED",
                            message="Failed to upload file to storage.",
                            details={"error": str(upload_err)},
                        ),
                    )
                )
                failed_count += 1
                continue

            # 4. Insert metadata in PostgreSQL (inheriting case retention)
            doc_record = {
                "id": doc_id,
                "case_id": case_id,
                "filename": filename,
                "content_type": "application/pdf",
                "file_size": file_size,
                "document_type": "unknown",
                "document_type_confidence": None,
                "storage_path": storage_path,
                "processing_status": "uploaded",
                "retention_type": case_detail.retention_type.value,
                "expires_at": case_detail.expires_at.isoformat() if case_detail.expires_at else None,
            }
            try:
                insert_res = client.table("documents").insert(doc_record).execute()
                if not insert_res.data:
                    raise RuntimeError("Database insertion returned empty data")
                inserted = insert_res.data[0]
                doc_resp = DocumentResponse(
                    id=uuid.UUID(str(inserted["id"])),
                    case_id=uuid.UUID(str(inserted["case_id"])),
                    filename=inserted["filename"],
                    content_type=inserted["content_type"],
                    file_size=inserted["file_size"],
                    document_type=DocumentType(inserted.get("document_type", "unknown")),
                    document_type_confidence=inserted.get("document_type_confidence"),
                    page_count=inserted.get("page_count"),
                    processing_status=DocumentProcessingStatus(inserted.get("processing_status", "uploaded")),
                    retention_type=RetentionType(inserted.get("retention_type", "persistent")),
                    expires_at=_parse_dt(inserted.get("expires_at")),
                    created_at=_parse_dt(inserted["created_at"]) or now,
                    updated_at=_parse_dt(inserted.get("updated_at")) or now,
                )
            except Exception as db_err:
                # 5. Orphan rollback: delete the storage object if DB insert failed
                logger.error("Database insert failed for %s. Triggering orphan storage cleanup: %s", storage_path, db_err)
                try:
                    client.storage.from_(bucket).remove([storage_path])
                except Exception as cleanup_err:
                    logger.error("Failed to clean up orphaned storage object %s: %s", storage_path, cleanup_err)

                results.append(
                    DocumentUploadResultItem(
                        filename=filename,
                        status="failed",
                        error=ErrorDetail(
                            code="METADATA_PERSISTENCE_FAILED",
                            message="Failed to record document metadata in database.",
                            details={"error": str(db_err)},
                        ),
                    )
                )
                failed_count += 1
                continue

            # Success
            existing_count += 1
            existing_total_size += file_size
            accepted_count += 1
            results.append(
                DocumentUploadResultItem(
                    filename=filename,
                    status="uploaded",
                    document=doc_resp,
                )
            )

        return DocumentUploadResponse(
            results=results,
            accepted_count=accepted_count,
            failed_count=failed_count,
        )

    def list_documents(self, case_id: str, caller: CallerContext) -> DocumentListResponse:
        """Lists all documents in a case, verifying caller ownership."""
        client = self._require_client()
        case_service = get_case_service(client)
        case_service.get_case(case_id, caller)
        now = datetime.now(timezone.utc)

        res = client.table("documents").select("*").eq("case_id", case_id).order("created_at").execute()
        items = []
        for d in (res.data or []):
            items.append(
                DocumentResponse(
                    id=uuid.UUID(str(d["id"])),
                    case_id=uuid.UUID(str(d["case_id"])),
                    filename=d["filename"],
                    content_type=d["content_type"],
                    file_size=d["file_size"],
                    document_type=DocumentType(d.get("document_type", "unknown")),
                    document_type_confidence=d.get("document_type_confidence"),
                    page_count=d.get("page_count"),
                    processing_status=DocumentProcessingStatus(d.get("processing_status", "uploaded")),
                    retention_type=RetentionType(d.get("retention_type", "persistent")),
                    expires_at=_parse_dt(d.get("expires_at")),
                    created_at=_parse_dt(d["created_at"]) or now,
                    updated_at=_parse_dt(d.get("updated_at")) or now,
                )
            )
        return DocumentListResponse(items=items)

    def get_document(self, document_id: str, caller: CallerContext) -> DocumentResponse:
        """Retrieves a single document, verifying caller ownership."""
        client = self._require_client()
        now = datetime.now(timezone.utc)

        res = client.table("documents").select("*, cases(user_id, guest_session_id)").eq("id", document_id).execute()
        if not res.data:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail={"error": {"code": "DOCUMENT_NOT_FOUND", "message": f"Document {document_id} not found.", "details": {}}},
            )

        row = res.data[0]
        case_info = row.get("cases", {})
        # Ownership check
        if caller.is_authenticated and case_info.get("user_id") != caller.user_id:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail={"error": {"code": "DOCUMENT_NOT_FOUND", "message": "Document not found.", "details": {}}})
        if caller.is_guest and case_info.get("guest_session_id") != caller.guest_session_id:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail={"error": {"code": "DOCUMENT_NOT_FOUND", "message": "Document not found.", "details": {}}})

        return DocumentResponse(
            id=uuid.UUID(str(row["id"])),
            case_id=uuid.UUID(str(row["case_id"])),
            filename=row["filename"],
            content_type=row["content_type"],
            file_size=row["file_size"],
            document_type=DocumentType(row.get("document_type", "unknown")),
            document_type_confidence=row.get("document_type_confidence"),
            page_count=row.get("page_count"),
            processing_status=DocumentProcessingStatus(row.get("processing_status", "uploaded")),
            retention_type=RetentionType(row.get("retention_type", "persistent")),
            expires_at=_parse_dt(row.get("expires_at")),
            created_at=_parse_dt(row["created_at"]) or now,
            updated_at=_parse_dt(row.get("updated_at")) or now,
        )

    def get_document_access(self, document_id: str, caller: CallerContext) -> DocumentAccessResponse:
        """Generates a temporary signed URL for private PDF access."""
        settings = get_settings()
        client = self._require_client()
        bucket = settings.supabase_documents_bucket
        now = datetime.now(timezone.utc)
        expires_at = now + timedelta(seconds=settings.signed_url_expiration_seconds)

        res = client.table("documents").select("storage_path, cases(user_id, guest_session_id)").eq("id", document_id).execute()
        if not res.data:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail={"error": {"code": "DOCUMENT_NOT_FOUND", "message": f"Document {document_id} not found.", "details": {}}},
            )

        row = res.data[0]
        case_info = row.get("cases", {})
        if caller.is_authenticated and case_info.get("user_id") != caller.user_id:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail={"error": {"code": "DOCUMENT_NOT_FOUND", "message": "Document not found.", "details": {}}})
        if caller.is_guest and case_info.get("guest_session_id") != caller.guest_session_id:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail={"error": {"code": "DOCUMENT_NOT_FOUND", "message": "Document not found.", "details": {}}})

        storage_path = row["storage_path"]
        try:
            signed_url_data = client.storage.from_(bucket).create_signed_url(
                path=storage_path,
                expires_in=settings.signed_url_expiration_seconds,
            )
            signed_url = signed_url_data.get("signedURL") or signed_url_data.get("signedUrl") or str(signed_url_data)
        except Exception as e:
            logger.error("Failed to generate signed URL for %s: %s", storage_path, e)
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail={"error": {"code": "STORAGE_ACCESS_FAILED", "message": "Failed to generate signed document access URL.", "details": {}}},
            )

        return DocumentAccessResponse(
            document_id=uuid.UUID(str(document_id)),
            access_url=signed_url,
            expires_at=expires_at,
        )

    def delete_document(self, document_id: str, caller: CallerContext) -> None:
        """Deletes document storage object and database record."""
        settings = get_settings()
        client = self._require_client()
        bucket = settings.supabase_documents_bucket

        res = client.table("documents").select("storage_path, cases(user_id, guest_session_id)").eq("id", document_id).execute()
        if not res.data:
            return

        row = res.data[0]
        case_info = row.get("cases", {})
        if caller.is_authenticated and case_info.get("user_id") != caller.user_id:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail={"error": {"code": "DOCUMENT_NOT_FOUND", "message": "Document not found.", "details": {}}})
        if caller.is_guest and case_info.get("guest_session_id") != caller.guest_session_id:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail={"error": {"code": "DOCUMENT_NOT_FOUND", "message": "Document not found.", "details": {}}})

        storage_path = row["storage_path"]

        # 1. Attempt storage removal
        try:
            client.storage.from_(bucket).remove([storage_path])
        except Exception as e:
            err_msg = str(e).lower()
            if "not found" in err_msg or "404" in err_msg:
                logger.info("Storage object already absent for %s: %s", storage_path, e)
            else:
                logger.error("Fatal storage deletion error for %s: %s", storage_path, e)
                raise HTTPException(
                    status_code=status.HTTP_502_BAD_GATEWAY,
                    detail={
                        "error": {
                            "code": "STORAGE_DELETION_FAILED",
                            "message": f"Storage cleanup failed for document {document_id}: {str(e)}",
                            "details": {},
                        }
                    },
                )

        # 2. Delete DB record
        client.table("documents").delete().eq("id", document_id).execute()


_document_service = DocumentService()


def get_document_service(client: Any = None) -> DocumentService:
    if client is not None:
        return DocumentService(client)
    return _document_service
