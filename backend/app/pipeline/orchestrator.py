"""Case processing orchestrator connecting documents to the legal summarization pipeline."""

from datetime import datetime, timezone
import logging
import re
from typing import Any
from uuid import UUID

from fastapi import HTTPException

from backend.app.api.schemas.job import JobStatus, PipelineStage
from backend.app.case.analyze import analyze_case
from backend.app.chunking.chunk import build_chunks
from backend.app.config import get_settings
from backend.app.core.supabase import get_supabase_client
from backend.app.file.analyze import analyze_file
from backend.app.file.models import FileAnalysis
from backend.app.ingestion.pdf import ingest_pdf_bytes
from backend.app.llm.analyze import analyze_chunks
from backend.app.nlp.evidence import build_evidence
from backend.app.presentation.builder import build_detailed_analysis
from backend.app.presentation.models import DetailedAnalysis
from backend.app.services.job_service import JobService, get_job_service
from backend.app.services.summary_service import SummaryService, get_summary_service

logger = logging.getLogger(__name__)


def _sanitize_error_message(err: Any) -> str:
    """Sanitizes error messages to prevent leaking API keys, secrets, or raw auth tokens."""
    msg = str(err)
    try:
        settings = get_settings()
        secret_keys = [
            settings.gemini_api_key,
            settings.mistral_api_key,
            settings.anthropic_api_key,
            settings.supabase_service_role_key,
        ]
        for key in secret_keys:
            if key and len(key) >= 8 and key in msg:
                msg = msg.replace(key, "[REDACTED_API_KEY]")
    except Exception:
        pass

    msg = re.sub(r"AIzaSy[A-Za-z0-9_\-]{33}", "[REDACTED_API_KEY]", msg)
    msg = re.sub(r"Bearer\s+[A-Za-z0-9_\-\.]+", "Bearer [REDACTED_TOKEN]", msg, flags=re.IGNORECASE)
    msg = re.sub(r"gst_[A-Za-z0-9]{32,}", "gst_[REDACTED_TOKEN]", msg)
    if len(msg) > 500:
        msg = msg[:497] + "..."
    return msg


class CaseProcessingOrchestrator:
    """Orchestrates end-to-end legal case summarization pipeline execution."""

    def __init__(
        self,
        client: Any = None,
        job_service: JobService | None = None,
        summary_service: SummaryService | None = None,
    ):
        self._client = client
        self._job_service = job_service or get_job_service(client=self._client)
        self._summary_service = summary_service or get_summary_service(client=self._client)

    @property
    def client(self) -> Any:
        if self._client is not None:
            return self._client
        return get_supabase_client()

    def _require_client(self) -> Any:
        client = self.client
        if client is None:
            raise HTTPException(
                status_code=500,
                detail={
                    "error": {
                        "code": "DATABASE_UNAVAILABLE",
                        "message": "Supabase infrastructure is not configured or unavailable.",
                        "details": {},
                    }
                },
            )
        return client

    def _is_cancelled(self, job_id_str: str, case_id_str: str) -> bool:
        """Checks if cancellation was requested; if so, marks cancelled and returns True."""
        if self._job_service.is_cancellation_requested(job_id_str):
            logger.info("Cancellation detected for job %s; aborting pipeline execution.", job_id_str)
            try:
                self._job_service.mark_cancelled(job_id_str)
            except Exception as e:
                logger.warning("Error marking job %s cancelled: %s", job_id_str, e)
            try:
                self._summary_service.mark_failed(case_id_str)
            except Exception as e:
                logger.warning("Error marking summary failed for case %s: %s", case_id_str, e)
            return True
        return False

    def process_case_job(self, job_id: UUID | str) -> DetailedAnalysis | None:
        """Executes the full pipeline for a processing job.

        Returns DetailedAnalysis on success, or None if the job was cancelled.
        Raises an exception on fatal failure.
        """
        job_id_str = str(job_id)
        client = self._require_client()
        settings = get_settings()
        bucket = settings.supabase_documents_bucket

        # 1. Fetch raw job row and validate initial state
        job_row = self._job_service._fetch_raw_job(job_id_str)
        case_id_str = str(job_row["case_id"])
        curr_status = job_row["status"]

        if curr_status in (JobStatus.COMPLETED.value, JobStatus.FAILED.value, JobStatus.CANCELLED.value):
            logger.warning("Job %s is already in terminal state '%s'. Aborting execution.", job_id_str, curr_status)
            return None

        # Check early cancellation before marking processing
        if job_row.get("cancel_requested"):
            logger.info("Job %s had cancellation requested prior to execution.", job_id_str)
            self._job_service.mark_cancelled(job_id_str)
            self._summary_service.mark_failed(case_id_str)
            return None

        if curr_status != JobStatus.QUEUED.value:
            raise ValueError(f"Job {job_id_str} is in invalid state '{curr_status}' (expected 'queued').")

        # 2. Transition job to processing (stage: ingestion, progress: 0.10)
        self._job_service.mark_processing(job_id_str)

        # 3. Mark summary generating (resets existing summary for reprocessing)
        self._summary_service.mark_generating(case_id_str)

        try:
            # 4. Load case documents deterministically
            docs_res = client.table("documents").select("*").eq("case_id", case_id_str).execute()
            raw_docs = docs_res.data or []

            if not raw_docs:
                err_msg = "Cannot process case with no uploaded documents."
                self._job_service.mark_failed(job_id_str, err_msg)
                self._summary_service.mark_failed(case_id_str)
                raise ValueError(err_msg)

            # Deterministic ordering by (created_at, filename, id)
            docs = sorted(
                raw_docs,
                key=lambda d: (
                    str(d.get("created_at") or ""),
                    str(d.get("filename") or ""),
                    str(d.get("id") or ""),
                ),
            )

            # Check cancellation before download/ingestion
            if self._is_cancelled(job_id_str, case_id_str):
                return None

            # 5. Download and Ingest PDFs
            all_pages = []
            doc_errors: dict[str, str] = {}
            successful_doc_ids: set[str] = set()

            for doc in docs:
                doc_id = str(doc["id"])
                filename = doc.get("filename", "document.pdf")
                storage_path = doc.get("storage_path")

                if not storage_path:
                    doc_errors[doc_id] = f"Document {doc_id} missing storage_path"
                    continue

                try:
                    pdf_bytes = client.storage.from_(bucket).download(storage_path)
                    pages = ingest_pdf_bytes(pdf_bytes, doc_id, filename)
                    del pdf_bytes  # Free raw bytes immediately to control memory footprint
                    all_pages.extend(pages)
                    successful_doc_ids.add(doc_id)
                except Exception as exc:
                    sanitized_err = _sanitize_error_message(exc)
                    logger.warning("Failed to ingest document %s (%s): %s", doc_id, filename, sanitized_err)
                    doc_errors[doc_id] = sanitized_err

            # If all documents failed, fail the job
            if not all_pages or not successful_doc_ids:
                err_msg = f"All documents failed to ingest: {'; '.join(doc_errors.values())}"
                self._job_service.mark_failed(job_id_str, _sanitize_error_message(err_msg))
                self._summary_service.mark_failed(case_id_str)
                raise RuntimeError(err_msg)

            # Update progress: OCR complete
            self._job_service.update_progress(job_id_str, PipelineStage.OCR, 0.20)

            if self._is_cancelled(job_id_str, case_id_str):
                return None

            # 6. Evidence extraction
            evidence = build_evidence(all_pages)
            self._job_service.update_progress(job_id_str, PipelineStage.EVIDENCE, 0.30)

            if self._is_cancelled(job_id_str, case_id_str):
                return None

            # 7. Adaptive chunking
            chunks = build_chunks(all_pages, evidence)
            self._job_service.update_progress(job_id_str, PipelineStage.CHUNKING, 0.40)

            if self._is_cancelled(job_id_str, case_id_str):
                return None

            # 8. LLM Chunk Analysis
            chunk_analyses = analyze_chunks(chunks, evidence)
            self._job_service.update_progress(job_id_str, PipelineStage.LLM_CHUNK, 0.60)

            if self._is_cancelled(job_id_str, case_id_str):
                return None

            # 9. File-Level Synthesis
            file_analyses: list[FileAnalysis] = []
            for doc in docs:
                doc_id = str(doc["id"])
                filename = doc.get("filename", "document.pdf")

                if doc_id not in successful_doc_ids:
                    # Ingestion failed for this document: produce a structured failed FileAnalysis
                    fa_failed = FileAnalysis(
                        document_id=doc_id,
                        filename=filename,
                        chunk_ids=[],
                        chunk_count=0,
                        pages=[],
                        page_start=0,
                        page_end=0,
                        analyzed_chunk_ids=[],
                        failed_chunk_ids=[],
                        coverage=0.0,
                        status="failed",
                        document_type=doc.get("document_type") or "unknown",
                        uncertainty=doc_errors.get(doc_id, "Document ingestion failed"),
                        meta={"error": doc_errors.get(doc_id)},
                        model=settings.llm_model,
                        provider=settings.llm_provider,
                    )
                    file_analyses.append(fa_failed)
                else:
                    doc_chunks = [c for c in chunks if c.document_id == doc_id]
                    doc_analyses = [a for a in chunk_analyses if a.document_id == doc_id]
                    fa = analyze_file(doc_id, doc_chunks, doc_analyses)
                    file_analyses.append(fa)

            self._job_service.update_progress(job_id_str, PipelineStage.FILE_SYNTHESIS, 0.75)

            if self._is_cancelled(job_id_str, case_id_str):
                return None

            # 10. Case-Level Synthesis
            case_analysis = analyze_case(case_id_str, file_analyses)
            self._job_service.update_progress(job_id_str, PipelineStage.CASE_SYNTHESIS, 0.85)

            if self._is_cancelled(job_id_str, case_id_str):
                return None

            # 11. Presentation Builder (DetailedAnalysis)
            detailed_analysis = build_detailed_analysis(case_analysis)
            self._job_service.update_progress(job_id_str, PipelineStage.PRESENTATION, 0.95)

            if self._is_cancelled(job_id_str, case_id_str):
                return None

            # 12. Validate presentation output
            if detailed_analysis is None or (case_analysis.status == "failed" and case_analysis.case_coverage == 0.0):
                err_msg = case_analysis.uncertainty or "Analysis resulted in zero coverage summary."
                self._job_service.mark_failed(job_id_str, _sanitize_error_message(err_msg))
                self._summary_service.mark_failed(case_id_str)
                raise RuntimeError(err_msg)

            # 13. Persist summary through SummaryService
            self._summary_service.upsert_summary(case_id_str, detailed_analysis)

            # 14. Mark job completed
            self._job_service.mark_completed(job_id_str)

            return detailed_analysis

        except Exception as exc:
            sanitized = _sanitize_error_message(exc)
            logger.error("Pipeline failure for job %s: %s", job_id_str, sanitized, exc_info=True)
            # Ensure job does not overwrite cancelled state
            try:
                if self._job_service.is_cancellation_requested(job_id_str):
                    self._job_service.mark_cancelled(job_id_str)
                else:
                    self._job_service.mark_failed(job_id_str, sanitized)
            except Exception as e:
                logger.warning("Error marking job %s failed: %s", job_id_str, e)

            try:
                self._summary_service.mark_failed(case_id_str)
            except Exception as e:
                logger.warning("Error marking summary failed for case %s: %s", case_id_str, e)
            raise


def get_case_orchestrator(client: Any = None) -> CaseProcessingOrchestrator:
    """Factory helper for CaseProcessingOrchestrator."""
    return CaseProcessingOrchestrator(client=client)


def run_case_job_background(job_id: str) -> None:
    """Background execution runner for CaseProcessingOrchestrator.

    Executes asynchronously via FastAPI BackgroundTasks in a worker thread.
    Catches any unexpected errors escaping the orchestrator to prevent
    unhandled thread crashes, and ensures the job is left in a valid terminal state.

    Limitation note: BackgroundTasks runs in-process. If the server process
    terminates while processing, the task dies and the job remains in 'processing'.
    Automated stale-job reconciliation is deferred to Phase E.
    """
    try:
        orchestrator = get_case_orchestrator()
        orchestrator.process_case_job(job_id)
    except Exception as exc:
        sanitized = _sanitize_error_message(exc)
        logger.error(
            "Background task runner encountered fatal error for job %s: %s",
            job_id,
            sanitized,
            exc_info=True,
        )
        try:
            from backend.app.services.job_service import get_job_service

            job_svc = get_job_service()
            if job_svc.is_cancellation_requested(job_id):
                try:
                    job_svc.mark_cancelled(job_id)
                except Exception:
                    pass
            else:
                try:
                    job_svc.mark_failed(job_id, f"Fatal background runner error: {sanitized}")
                except Exception:
                    # If already in terminal state (e.g. JOB_ALREADY_TERMINAL), do not overwrite
                    pass
        except Exception as inner_e:
            logger.warning("Failed in fallback terminal error handler for job %s: %s", job_id, inner_e)
