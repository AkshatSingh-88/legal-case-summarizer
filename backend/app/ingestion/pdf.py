"""Page-level PDF ingestion using PyMuPDF.

Each page is processed independently so mixed native/scanned PDFs are
handled correctly. A single page failure does not abort the document.
"""

import logging
from pathlib import Path

import fitz  # PyMuPDF

from backend.app.ingestion.models import IngestedPage
from backend.app.ingestion.quality import analyze_quality

logger = logging.getLogger(__name__)


def _validate_pdf_path(path: Path) -> None:
    if not path.exists():
        raise FileNotFoundError(f"PDF not found: {path}")
    if not path.is_file():
        raise ValueError(f"Not a file: {path}")
    # Basic extension + magic check — not a full security boundary
    if path.suffix.lower() != ".pdf":
        raise ValueError(f"Not a PDF file (expected .pdf): {path}")
    # Check header if file is non-empty
    try:
        with path.open("rb") as f:
            header = f.read(4)
            if header and not header.startswith(b"%PDF"):
                raise ValueError(f"File does not appear to be a PDF: {path}")
    except OSError as e:
        raise ValueError(f"Cannot read file: {path} ({e})") from e


def _extract_text_from_page(page) -> str:
    # Default "text" preserves useful whitespace; avoid aggressive stripping
    try:
        return page.get_text() or ""
    except Exception as e:
        logger.warning("Failed to extract text from page %s: %s", page.number, e)
        raise


def ingest_pdf(
    pdf_path: str | Path,
    document_id: str,
    filename: str,
) -> list[IngestedPage]:
    """Ingest a single PDF file page-by-page.

    Returns a list ordered by page_number (1-indexed). Per-page failures
    are captured as IngestedPage with error set, not raised.
    Document-level failures (missing/unreadable/malformed) are raised.
    """
    path = Path(pdf_path)
    _validate_pdf_path(path)

    try:
        doc = fitz.open(str(path))
    except Exception as e:
        raise ValueError(f"Failed to open PDF {filename}: {e}") from e

    pages: list[IngestedPage] = []
    try:
        page_count = doc.page_count
        if page_count == 0:
            logger.warning("PDF has no pages: %s", filename)

        for idx in range(page_count):
            page_number = idx + 1
            try:
                page = doc.load_page(idx)
                text = _extract_text_from_page(page)
                quality = analyze_quality(text)
                pages.append(
                    IngestedPage(
                        document_id=document_id,
                        filename=filename,
                        page_number=page_number,
                        text=text,
                        char_count=quality.char_count,
                        word_count=quality.word_count,
                        is_empty=quality.is_empty,
                        ocr_used=False,
                        error=None,
                    )
                )
            except Exception as e:
                logger.warning(
                    "Page %s extraction failed for %s: %s", page_number, filename, e
                )
                pages.append(
                    IngestedPage(
                        document_id=document_id,
                        filename=filename,
                        page_number=page_number,
                        text="",
                        char_count=0,
                        word_count=0,
                        is_empty=True,
                        ocr_used=False,
                        error=str(e),
                    )
                )
    finally:
        doc.close()

    return pages


def ingest_pdf_bytes(
    data: bytes,
    document_id: str,
    filename: str,
) -> list[IngestedPage]:
    """Ingest PDF from bytes (e.g., uploaded file). Same page-level guarantees."""
    if not data:
        raise ValueError(f"Empty PDF data for {filename}")
    if not data.startswith(b"%PDF"):
        raise ValueError(f"Data does not appear to be a PDF: {filename}")

    try:
        doc = fitz.open(stream=data, filetype="pdf")
    except Exception as e:
        raise ValueError(f"Failed to open PDF bytes {filename}: {e}") from e

    pages: list[IngestedPage] = []
    try:
        for idx in range(doc.page_count):
            page_number = idx + 1
            try:
                page = doc.load_page(idx)
                text = _extract_text_from_page(page)
                quality = analyze_quality(text)
                pages.append(
                    IngestedPage(
                        document_id=document_id,
                        filename=filename,
                        page_number=page_number,
                        text=text,
                        char_count=quality.char_count,
                        word_count=quality.word_count,
                        is_empty=quality.is_empty,
                        ocr_used=False,
                        error=None,
                    )
                )
            except Exception as e:
                logger.warning(
                    "Page %s extraction failed for %s (bytes): %s",
                    page_number,
                    filename,
                    e,
                )
                pages.append(
                    IngestedPage(
                        document_id=document_id,
                        filename=filename,
                        page_number=page_number,
                        text="",
                        char_count=0,
                        word_count=0,
                        is_empty=True,
                        ocr_used=False,
                        error=str(e),
                    )
                )
    finally:
        doc.close()

    return pages


def ingest_pdfs(
    files: list[tuple[str | Path, str, str]],
) -> list[list[IngestedPage]]:
    """Process multiple PDFs sequentially (no parallelism in Phase 2).

    Each tuple is (pdf_path, document_id, filename).
    Returns list of page-lists in the same order as input.
    """
    results: list[list[IngestedPage]] = []
    for pdf_path, document_id, filename in files:
        results.append(ingest_pdf(pdf_path, document_id, filename))
    return results
