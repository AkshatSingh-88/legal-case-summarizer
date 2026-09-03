"""V1 Documents router skeleton."""

from datetime import datetime, timezone
from uuid import UUID, uuid4
from fastapi import APIRouter, Header, HTTPException, Request, Response, status

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

router = APIRouter(tags=["Documents"])


@router.post(
    "/cases/{case_id}/documents",
    response_model=DocumentUploadResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Upload Documents to Case",
)
def upload_documents(
    case_id: UUID,
    request: Request,
    authorization: str | None = Header(default=None, description="Bearer <supabase_jwt_token>"),
    x_guest_session_id: str | None = Header(default=None, description="Guest Session Token/ID"),
) -> DocumentUploadResponse:
    """Uploads one or more legal PDF files to a Case workspace with per-file result tracking."""
    now = datetime.now(timezone.utc)
    sample_doc = DocumentResponse(
        id=uuid4(),
        case_id=case_id,
        filename="petition.pdf",
        content_type="application/pdf",
        file_size=245120,
        document_type=DocumentType.PETITION,
        document_type_confidence=None,
        page_count=None,
        processing_status=DocumentProcessingStatus.UPLOADED,
        retention_type=RetentionType.PERSISTENT,
        expires_at=None,
        created_at=now,
        updated_at=now,
    )
    result_item = DocumentUploadResultItem(
        filename="petition.pdf",
        status="uploaded",
        document=sample_doc,
        error=None,
    )
    return DocumentUploadResponse(
        results=[result_item],
        accepted_count=1,
        failed_count=0,
    )


@router.get(
    "/cases/{case_id}/documents",
    response_model=DocumentListResponse,
    summary="List Case Documents",
)
def list_case_documents(
    case_id: UUID,
    authorization: str | None = Header(default=None, description="Bearer <supabase_jwt_token>"),
    x_guest_session_id: str | None = Header(default=None, description="Guest Session Token/ID"),
) -> DocumentListResponse:
    """Lists all documents registered in a case."""
    now = datetime.now(timezone.utc)
    sample_doc = DocumentResponse(
        id=UUID("9b1deb4d-3b7d-4bad-9bdd-2b0d7b3dcb6d"),
        case_id=case_id,
        filename="petition.pdf",
        content_type="application/pdf",
        file_size=245120,
        document_type=DocumentType.PETITION,
        document_type_confidence=None,
        page_count=12,
        processing_status=DocumentProcessingStatus.PROCESSED,
        retention_type=RetentionType.PERSISTENT,
        expires_at=None,
        created_at=now,
        updated_at=now,
    )
    return DocumentListResponse(items=[sample_doc])


@router.get(
    "/documents/{document_id}",
    response_model=DocumentResponse,
    summary="Get Document Details",
)
def get_document(
    document_id: UUID,
    authorization: str | None = Header(default=None, description="Bearer <supabase_jwt_token>"),
    x_guest_session_id: str | None = Header(default=None, description="Guest Session Token/ID"),
) -> DocumentResponse:
    """Retrieves metadata and processing status for a single document."""
    now = datetime.now(timezone.utc)
    return DocumentResponse(
        id=document_id,
        case_id=UUID("c8f3b174-8b6b-4e12-8821-49fa5cf10321"),
        filename="petition.pdf",
        content_type="application/pdf",
        file_size=245120,
        document_type=DocumentType.PETITION,
        document_type_confidence=None,
        page_count=12,
        processing_status=DocumentProcessingStatus.PROCESSED,
        retention_type=RetentionType.PERSISTENT,
        expires_at=None,
        created_at=now,
        updated_at=now,
    )


@router.get(
    "/documents/{document_id}/access",
    response_model=DocumentAccessResponse,
    summary="Get Document Signed Access URL",
)
def get_document_access(
    document_id: UUID,
    authorization: str | None = Header(default=None, description="Bearer <supabase_jwt_token>"),
    x_guest_session_id: str | None = Header(default=None, description="Guest Session Token/ID"),
) -> DocumentAccessResponse:
    """Generates a short-lived signed URL for accessing the raw PDF in the viewer."""
    now = datetime.now(timezone.utc)
    return DocumentAccessResponse(
        document_id=document_id,
        access_url="https://storage.supabase.co/storage/v1/object/sign/legal-docs/petition.pdf?token=placeholder_token",
        expires_at=now,
    )


@router.delete(
    "/documents/{document_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete Document",
)
def delete_document(
    document_id: UUID,
    authorization: str | None = Header(default=None, description="Bearer <supabase_jwt_token>"),
    x_guest_session_id: str | None = Header(default=None, description="Guest Session Token/ID"),
) -> Response:
    """Deletes a document and purges its blob from storage."""
    return Response(status_code=status.HTTP_204_NO_CONTENT)
