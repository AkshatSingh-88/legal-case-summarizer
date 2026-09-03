"""Pydantic schemas for Processing Jobs."""

from datetime import datetime
from enum import Enum
from uuid import UUID
from pydantic import BaseModel, ConfigDict, Field


class JobType(str, Enum):
    SUMMARY = "summary"
    EMBEDDING = "embedding"
    RAG = "rag"


class JobStatus(str, Enum):
    QUEUED = "queued"
    PROCESSING = "processing"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class PipelineStage(str, Enum):
    QUEUED = "queued"
    INGESTION = "ingestion"
    OCR = "ocr"
    EVIDENCE = "evidence"
    CHUNKING = "chunking"
    LLM_CHUNK = "llm_chunk"
    FILE_SYNTHESIS = "file_synthesis"
    CASE_SYNTHESIS = "case_synthesis"
    PRESENTATION = "presentation"
    COMPLETED = "completed"
    FAILED = "failed"


class ProcessCaseRequest(BaseModel):
    model_config = ConfigDict(extra="ignore")

    job_type: JobType = Field(default=JobType.SUMMARY, description="Job type to execute")


class JobResponse(BaseModel):
    model_config = ConfigDict(extra="ignore")

    id: UUID
    case_id: UUID
    job_type: JobType
    status: JobStatus
    progress: float = Field(default=0.0, ge=0.0, le=1.0)
    current_stage: PipelineStage
    error_message: str | None = None
    cancel_requested: bool = False
    created_at: datetime
    started_at: datetime | None = None
    completed_at: datetime | None = None


class JobCancelResponse(BaseModel):
    model_config = ConfigDict(extra="ignore")

    id: UUID
    status: JobStatus
    cancel_requested: bool = True
    message: str = "Cancellation request submitted."
