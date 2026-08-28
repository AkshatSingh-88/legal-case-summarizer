"""Page-level PDF ingestion using PyMuPDF with conditional OCR.

Each page is processed independently so mixed native/scanned PDFs are
handled correctly. A single page failure does not abort the document.
"""

import logging
from pathlib import Path

import fitz  # PyMuPDF

from backend.app.config import get_settings
from backend.app.ingestion.models import IngestedPage
from backend.app.ingestion.quality import analyze_quality

logger = logging.getLogger(__name__)


def _validate_pdf_path(path: Path) -> None:
    if not path.exists():
        raise FileNotFoundError(f"PDF not found: {path}")
    if not path.is_file():
        raise ValueError(f"Not a file: {path}")
    if path.suffix.lower() != ".pdf":
        raise ValueError(f"Not a PDF file (expected .pdf): {path}")
    try:
        with path.open("rb") as f:
            header = f.read(4)
            if header and not header.startswith(b"%PDF"):
                raise ValueError(f"File does not appear to be a PDF: {path}")
    except OSError as e:
        raise ValueError(f"Cannot read file: {path} ({e})") from e


def _extract_text_from_page(page) -> str:
    try:
        return page.get_text() or ""
    except Exception as e:
        logger.warning("Failed to extract text from page %s: %s", page.number, e)
        raise


def _needs_ocr(
    quality,
    error: str | None,
    char_threshold: int,
    word_threshold: int,
) -> bool:
    if error is not None:
        return True
    if quality.is_empty:
        return True
    if quality.char_count < char_threshold:
        return True
    if quality.word_count < word_threshold:
        return True
    return False


def _ocr_page(page, dpi: int, lang: str, tesseract_cmd: str | None) -> str:
    # Lazy imports to keep module importable without tesseract installed
    from backend.app.ingestion.ocr import ocr_image, pixmap_to_image

    pix = page.get_pixmap(dpi=dpi)
    image = pixmap_to_image(pix)
    return ocr_image(image, lang=lang, tesseract_cmd=tesseract_cmd)


def ingest_pdf(
    pdf_path: str | Path,
    document_id: str,
    filename: str,
) -> list[IngestedPage]:
    """Ingest a single PDF file page-by-page with conditional OCR.

    Returns a list ordered by page_number (1-indexed). Per-page failures
    are captured as IngestedPage with error set, not raised.
    Document-level failures (missing/unreadable/malformed) are raised.
    """
    path = Path(pdf_path)
    _validate_pdf_path(path)
    settings = get_settings()

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
            native_text = ""
            native_error: str | None = None
            try:
                page = doc.load_page(idx)
                native_text = _extract_text_from_page(page)
            except Exception as e:
                native_error = str(e)
                native_text = ""

            native_quality = analyze_quality(native_text)

            if _needs_ocr(
                native_quality,
                native_error,
                settings.ocr_char_threshold,
                settings.ocr_word_threshold,
            ):
                # Attempt OCR for poor pages
                try:
                    page = doc.load_page(idx)  # ensure page loaded
                    ocr_text = _ocr_page(
                        page,
                        dpi=settings.ocr_dpi,
                        lang=settings.ocr_language,
                        tesseract_cmd=settings.tesseract_cmd,
                    )
                    ocr_quality = analyze_quality(ocr_text)
                    pages.append(
                        IngestedPage(
                            document_id=document_id,
                            filename=filename,
                            page_number=page_number,
                            text=ocr_text,
                            char_count=ocr_quality.char_count,
                            word_count=ocr_quality.word_count,
                            is_empty=ocr_quality.is_empty,
                            ocr_used=True,
                            error=native_error,
                            ocr_error=None,
                        )
                    )
                except Exception as e:
                    logger.warning(
                        "OCR failed for page %s of %s: %s", page_number, filename, e
                    )
                    pages.append(
                        IngestedPage(
                            document_id=document_id,
                            filename=filename,
                            page_number=page_number,
                            text=native_text,
                            char_count=native_quality.char_count,
                            word_count=native_quality.word_count,
                            is_empty=native_quality.is_empty,
                            ocr_used=True,
                            error=native_error,
                            ocr_error=str(e),
                        )
                    )
            else:
                pages.append(
                    IngestedPage(
                        document_id=document_id,
                        filename=filename,
                        page_number=page_number,
                        text=native_text,
                        char_count=native_quality.char_count,
                        word_count=native_quality.word_count,
                        is_empty=native_quality.is_empty,
                        ocr_used=False,
                        error=native_error,
                        ocr_error=None,
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
    """Ingest PDF from bytes with same conditional OCR guarantees."""
    if not data:
        raise ValueError(f"Empty PDF data for {filename}")
    if not data.startswith(b"%PDF"):
        raise ValueError(f"Data does not appear to be a PDF: {filename}")

    settings = get_settings()
    try:
        doc = fitz.open(stream=data, filetype="pdf")
    except Exception as e:
        raise ValueError(f"Failed to open PDF bytes {filename}: {e}") from e

    pages: list[IngestedPage] = []
    try:
        for idx in range(doc.page_count):
            page_number = idx + 1
            native_text = ""
            native_error: str | None = None
            try:
                page = doc.load_page(idx)
                native_text = _extract_text_from_page(page)
            except Exception as e:
                native_error = str(e)
                native_text = ""

            native_quality = analyze_quality(native_text)

            if _needs_ocr(
                native_quality,
                native_error,
                settings.ocr_char_threshold,
                settings.ocr_word_threshold,
            ):
                try:
                    page = doc.load_page(idx)
                    ocr_text = _ocr_page(
                        page,
                        dpi=settings.ocr_dpi,
                        lang=settings.ocr_language,
                        tesseract_cmd=settings.tesseract_cmd,
                    )
                    ocr_quality = analyze_quality(ocr_text)
                    pages.append(
                        IngestedPage(
                            document_id=document_id,
                            filename=filename,
                            page_number=page_number,
                            text=ocr_text,
                            char_count=ocr_quality.char_count,
                            word_count=ocr_quality.word_count,
                            is_empty=ocr_quality.is_empty,
                            ocr_used=True,
                            error=native_error,
                            ocr_error=None,
                        )
                    )
                except Exception as e:
                    logger.warning(
                        "OCR failed for page %s of %s (bytes): %s",
                        page_number,
                        filename,
                        e,
                    )
                    pages.append(
                        IngestedPage(
                            document_id=document_id,
                            filename=filename,
                            page_number=page_number,
                            text=native_text,
                            char_count=native_quality.char_count,
                            word_count=native_quality.word_count,
                            is_empty=native_quality.is_empty,
                            ocr_used=True,
                            error=native_error,
                            ocr_error=str(e),
                        )
                    )
            else:
                pages.append(
                    IngestedPage(
                        document_id=document_id,
                        filename=filename,
                        page_number=page_number,
                        text=native_text,
                        char_count=native_quality.char_count,
                        word_count=native_quality.word_count,
                        is_empty=native_quality.is_empty,
                        ocr_used=False,
                        error=native_error,
                        ocr_error=None,
                    )
                )
    finally:
        doc.close()

    return pages


def ingest_pdfs(
    files: list[tuple[str | Path, str, str]],
) -> list[list[IngestedPage]]:
    """Process multiple PDFs sequentially (no parallelism in Phase 3)."""
    results: list[list[IngestedPage]] = []
    for pdf_path, document_id, filename in files:
        results.append(ingest_pdf(pdf_path, document_id, filename))
    return results
