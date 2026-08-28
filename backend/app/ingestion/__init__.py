"""PDF page-level ingestion — public surface for Phase 2."""

from backend.app.ingestion.models import IngestedPage
from backend.app.ingestion.pdf import ingest_pdf, ingest_pdf_bytes, ingest_pdfs
from backend.app.ingestion.quality import QualityInfo, analyze_quality

__all__ = [
    "IngestedPage",
    "QualityInfo",
    "analyze_quality",
    "ingest_pdf",
    "ingest_pdf_bytes",
    "ingest_pdfs",
]
