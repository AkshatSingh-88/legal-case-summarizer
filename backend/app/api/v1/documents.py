"""V1 Documents router implementing multipart upload, document listing, signed URLs, and deletion."""

from uuid import UUID
from fastapi import APIRouter, File, Header, Response, UploadFile, status

from backend.app.api.schemas.document import (
    DocumentAccessResponse,
    DocumentListResponse,
    DocumentResponse,
    DocumentUploadResponse,
)
from backend.app.api.schemas.error import StandardErrorResponse
from backend.app.services.case_service import resolve_ownership_context
from backend.app.services.document_service import get_document_service

router = APIRouter(tags=["Documents"])


@router.post(
    "/cases/{case_id}/documents",
    response_model=DocumentUploadResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Upload Documents to Case",
    responses={
        400: {"model": StandardErrorResponse, "description": "Bad Request or Ambiguous Ownership."},
        401: {"model": StandardErrorResponse, "description": "Unauthorized."},
        404: {"model": StandardErrorResponse, "description": "Case Not Found."},
    },
)
async def upload_documents(
    case_id: UUID,
    files: list[UploadFile] = File(default=[]),
    authorization: str | None = Header(default=None, description="Bearer <supabase_jwt_token>"),
    x_guest_session_id: str | None = Header(default=None, description="Guest Session Token/ID"),
) -> DocumentUploadResponse:
    """Uploads one or more legal PDF files to a Case workspace with per-file result tracking."""
    caller = resolve_ownership_context(authorization, x_guest_session_id)
    service = get_document_service()
    return await service.upload_documents(str(case_id), files, caller)


@router.get(
    "/cases/{case_id}/documents",
    response_model=DocumentListResponse,
    summary="List Case Documents",
    responses={
        400: {"model": StandardErrorResponse, "description": "Ambiguous ownership context."},
        401: {"model": StandardErrorResponse, "description": "Unauthorized."},
        404: {"model": StandardErrorResponse, "description": "Case Not Found."},
    },
)
def list_case_documents(
    case_id: UUID,
    authorization: str | None = Header(default=None, description="Bearer <supabase_jwt_token>"),
    x_guest_session_id: str | None = Header(default=None, description="Guest Session Token/ID"),
) -> DocumentListResponse:
    """Lists all documents registered in a case."""
    caller = resolve_ownership_context(authorization, x_guest_session_id)
    service = get_document_service()
    return service.list_documents(str(case_id), caller)


@router.get(
    "/documents/{document_id}",
    response_model=DocumentResponse,
    summary="Get Document Details",
    responses={
        400: {"model": StandardErrorResponse, "description": "Ambiguous ownership context."},
        401: {"model": StandardErrorResponse, "description": "Unauthorized."},
        404: {"model": StandardErrorResponse, "description": "Document Not Found."},
    },
)
def get_document(
    document_id: UUID,
    authorization: str | None = Header(default=None, description="Bearer <supabase_jwt_token>"),
    x_guest_session_id: str | None = Header(default=None, description="Guest Session Token/ID"),
) -> DocumentResponse:
    """Retrieves metadata and processing status for a single document."""
    caller = resolve_ownership_context(authorization, x_guest_session_id)
    service = get_document_service()
    return service.get_document(str(document_id), caller)


@router.get(
    "/documents/{document_id}/access",
    response_model=DocumentAccessResponse,
    summary="Get Document Signed Access URL",
    responses={
        400: {"model": StandardErrorResponse, "description": "Ambiguous ownership context."},
        401: {"model": StandardErrorResponse, "description": "Unauthorized."},
        404: {"model": StandardErrorResponse, "description": "Document Not Found."},
    },
)
def get_document_access(
    document_id: UUID,
    authorization: str | None = Header(default=None, description="Bearer <supabase_jwt_token>"),
    x_guest_session_id: str | None = Header(default=None, description="Guest Session Token/ID"),
) -> DocumentAccessResponse:
    """Generates a short-lived signed URL for accessing the raw PDF in the viewer."""
    caller = resolve_ownership_context(authorization, x_guest_session_id)
    service = get_document_service()
    return service.get_document_access(str(document_id), caller)


@router.delete(
    "/documents/{document_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete Document",
    responses={
        400: {"model": StandardErrorResponse, "description": "Ambiguous ownership context."},
        401: {"model": StandardErrorResponse, "description": "Unauthorized."},
        404: {"model": StandardErrorResponse, "description": "Document Not Found."},
    },
)
def delete_document(
    document_id: UUID,
    authorization: str | None = Header(default=None, description="Bearer <supabase_jwt_token>"),
    x_guest_session_id: str | None = Header(default=None, description="Guest Session Token/ID"),
) -> Response:
    """Deletes a document and purges its blob from storage."""
    caller = resolve_ownership_context(authorization, x_guest_session_id)
    service = get_document_service()
    service.delete_document(str(document_id), caller)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
