"""Pydantic schemas for Document resources."""

from datetime import datetime
from enum import Enum
from uuid import UUID
from pydantic import BaseModel, ConfigDict, Field
from backend.app.api.schemas.case import RetentionType
from backend.app.api.schemas.error import ErrorDetail


class DocumentProcessingStatus(str, Enum):
    PENDING = "pending"
    UPLOADED = "uploaded"
    PROCESSED = "processed"
    FAILED = "failed"


class OcrStatus(str, Enum):
    NOT_NEEDED = "not_needed"
    PERFORMED = "performed"
    FAILED = "failed"


class DocumentType(str, Enum):
    UNKNOWN = "unknown"
    PETITION = "petition"
    APPEAL = "appeal"
    APPLICATION = "application"
    INTERLOCUTORY_APPLICATION = "interlocutory_application"
    AFFIDAVIT = "affidavit"
    REPLY = "reply"
    WRITTEN_STATEMENT = "written_statement"
    EVIDENCE = "evidence"
    JUDGMENT = "judgment"
    ORDER = "order"
    CHRONOLOGY = "chronology"
    REPORT = "report"
    MEMO = "memo"
    OTHER = "other"


class DocumentResponse(BaseModel):
    model_config = ConfigDict(extra="ignore")

    id: UUID
    case_id: UUID
    filename: str
    content_type: str
    file_size: int
    document_type: DocumentType = DocumentType.UNKNOWN
    document_type_confidence: float | None = None
    page_count: int | None = None
    processing_status: DocumentProcessingStatus
    retention_type: RetentionType
    expires_at: datetime | None = None
    created_at: datetime
    updated_at: datetime


class DocumentUploadResultItem(BaseModel):
    model_config = ConfigDict(extra="ignore")

    filename: str
    status: str = Field(..., description="'uploaded' or 'failed'")
    document: DocumentResponse | None = None
    error: ErrorDetail | None = None


class DocumentUploadResponse(BaseModel):
    model_config = ConfigDict(extra="ignore")

    results: list[DocumentUploadResultItem] = Field(default_factory=list)
    accepted_count: int = 0
    failed_count: int = 0


class DocumentListResponse(BaseModel):
    model_config = ConfigDict(extra="ignore")

    items: list[DocumentResponse] = Field(default_factory=list)


class DocumentAccessResponse(BaseModel):
    model_config = ConfigDict(extra="ignore")

    document_id: UUID
    access_url: str
    expires_at: datetime
