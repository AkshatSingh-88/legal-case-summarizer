"""API request and response schemas catalog."""

from backend.app.api.schemas.auth import UserProfileResponse
from backend.app.api.schemas.case import (
    CaseCreateRequest,
    CaseDetailDocumentItem,
    CaseDetailResponse,
    CaseListResponse,
    CaseResponse,
    CaseStatus,
    CaseUpdateRequest,
    RetentionType,
)
from backend.app.api.schemas.document import (
    DocumentAccessResponse,
    DocumentListResponse,
    DocumentProcessingStatus,
    DocumentResponse,
    DocumentType,
    DocumentUploadResponse,
    DocumentUploadResultItem,
    OcrStatus,
)
from backend.app.api.schemas.error import (
    ErrorDetail,
    StandardErrorResponse,
)
from backend.app.api.schemas.guest import (
    GuestClaimResponse,
    GuestSessionResponse,
)
from backend.app.api.schemas.job import (
    JobCancelResponse,
    JobResponse,
    JobStatus,
    JobType,
    PipelineStage,
    ProcessCaseRequest,
)
from backend.app.api.schemas.summary import (
    SummaryResponse,
    SummaryStatus,
    SummaryType,
)
from backend.app.api.schemas.system import SystemHealthResponse

__all__ = [
    "SystemHealthResponse",
    "UserProfileResponse",
    "ErrorDetail",
    "StandardErrorResponse",
    "CaseStatus",
    "RetentionType",
    "CaseCreateRequest",
    "CaseUpdateRequest",
    "CaseResponse",
    "CaseDetailDocumentItem",
    "CaseDetailResponse",
    "CaseListResponse",
    "DocumentProcessingStatus",
    "OcrStatus",
    "DocumentType",
    "DocumentResponse",
    "DocumentUploadResultItem",
    "DocumentUploadResponse",
    "DocumentListResponse",
    "DocumentAccessResponse",
    "JobType",
    "JobStatus",
    "PipelineStage",
    "ProcessCaseRequest",
    "JobResponse",
    "JobCancelResponse",
    "SummaryType",
    "SummaryStatus",
    "SummaryResponse",
    "GuestSessionResponse",
    "GuestClaimResponse",
]
