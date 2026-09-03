"""Pydantic schemas for Case resources."""

from datetime import datetime
from enum import Enum
from uuid import UUID
from pydantic import BaseModel, ConfigDict, Field


class CaseStatus(str, Enum):
    DRAFT = "draft"
    READY = "ready"
    PROCESSING = "processing"
    COMPLETED = "completed"
    FAILED = "failed"


class RetentionType(str, Enum):
    TEMPORARY = "temporary"
    PERSISTENT = "persistent"


class CaseCreateRequest(BaseModel):
    model_config = ConfigDict(extra="ignore")

    title: str = Field(default="Untitled Case", min_length=1, max_length=255)
    retention_type: RetentionType = RetentionType.PERSISTENT


class CaseUpdateRequest(BaseModel):
    model_config = ConfigDict(extra="ignore")

    title: str | None = Field(default=None, min_length=1, max_length=255)
    retention_type: RetentionType | None = None


class CaseResponse(BaseModel):
    model_config = ConfigDict(extra="ignore")

    id: UUID
    title: str
    status: CaseStatus
    retention_type: RetentionType
    expires_at: datetime | None = None
    document_count: int = 0
    created_at: datetime
    updated_at: datetime


class CaseDetailDocumentItem(BaseModel):
    model_config = ConfigDict(extra="ignore")

    id: UUID
    filename: str
    content_type: str
    file_size: int
    page_count: int | None = None
    processing_status: str
    created_at: datetime


class CaseDetailResponse(BaseModel):
    model_config = ConfigDict(extra="ignore")

    id: UUID
    title: str
    status: CaseStatus
    retention_type: RetentionType
    expires_at: datetime | None = None
    documents: list[CaseDetailDocumentItem] = Field(default_factory=list)
    created_at: datetime
    updated_at: datetime


class CaseListResponse(BaseModel):
    model_config = ConfigDict(extra="ignore")

    items: list[CaseResponse]
    total: int
    limit: int
    offset: int
