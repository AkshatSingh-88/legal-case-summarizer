"""Services catalog for Legal Case Summarizer."""

from backend.app.services.case_service import CaseService, get_case_service, resolve_ownership_context
from backend.app.services.document_service import DocumentService, get_document_service
from backend.app.services.guest_service import GuestService, get_guest_service
from backend.app.services.job_service import JobService, get_job_service
from backend.app.services.models import CallerContext
from backend.app.services.summary_service import SummaryService, get_summary_service

__all__ = [
    "CallerContext",
    "CaseService",
    "DocumentService",
    "GuestService",
    "JobService",
    "SummaryService",
    "get_case_service",
    "get_document_service",
    "get_guest_service",
    "get_job_service",
    "get_summary_service",
    "resolve_ownership_context",
]
