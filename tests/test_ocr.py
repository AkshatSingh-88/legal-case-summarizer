import fitz
import pytest
from pathlib import Path
from unittest.mock import patch, Mock

from backend.app.ingestion import ingest_pdf, ingest_pdf_bytes

LONG_GOOD = (
    "This is a comprehensive legal judgment text containing sufficient length to exceed thresholds. "
    "The court considered facts, procedural history, arguments from both parties, relevant statutes, "
    "and precedent before delivering a detailed reasoning and final order with citations and findings."
)
# Exactly 14 words (<15) and <100 chars for char test, word test etc
SHORT_CHAR = "Hi"  # 2 chars
SHORT_WORDS = "Hello world test"  # 3 words, <15


def create_pdf(path: Path, texts: list[str | None]):
    doc = fitz.open()
    for t in texts:
        page = doc.new_page()
        if t is not None:
            page.insert_text((72, 72), t)
        else:
            page.draw_rect(fitz.Rect(50, 50, 200, 200), color=(0, 0, 0))
    doc.save(str(path))
    doc.close()


def test_good_native_text_skips_ocr(tmp_path):
    path = tmp_path / "good.pdf"
    create_pdf(path, [LONG_GOOD])
    with patch("backend.app.ingestion.pdf._ocr_page") as mock_ocr:
        pages = ingest_pdf(path, document_id="d1", filename="good.pdf")
    mock_ocr.assert_not_called()
    assert pages[0].text.strip().startswith("This is a comprehensive")
    assert pages[0].ocr_used is False
    assert pages[0].ocr_error is None
    assert pages[0].error is None
    assert pages[0].char_count > 100
    assert pages[0].word_count >= 15


def test_empty_page_triggers_ocr(tmp_path):
    path = tmp_path / "empty.pdf"
    create_pdf(path, [None])
    with patch("backend.app.ingestion.pdf._ocr_page", return_value="OCR recovered text for empty page that is sufficiently long to pass thresholds and be useful for downstream processing in legal summarization.") as mock:
        pages = ingest_pdf(path, document_id="d2", filename="empty.pdf")
    mock.assert_called_once()
    assert pages[0].ocr_used is True
    assert pages[0].ocr_error is None
    assert "OCR recovered" in pages[0].text
    assert pages[0].char_count > 100


def test_char_count_below_100_triggers_ocr(tmp_path):
    path = tmp_path / "short_char.pdf"
    create_pdf(path, [SHORT_CHAR])
    with patch("backend.app.ingestion.pdf._ocr_page", return_value="OCR text for short char page that is now long enough to exceed the one hundred character threshold and fifteen word threshold for validation.") as mock:
        pages = ingest_pdf(path, document_id="d3", filename="short.pdf")
    mock.assert_called_once()
    assert pages[0].ocr_used is True
    assert pages[0].ocr_error is None
    # OCR result updates quality
    assert pages[0].char_count > 100


def test_word_count_below_15_triggers_ocr(tmp_path):
    path = tmp_path / "short_words.pdf"
    create_pdf(path, [SHORT_WORDS])
    with patch("backend.app.ingestion.pdf._ocr_page", return_value="OCR generated content that contains more than fifteen words to demonstrate that word count threshold triggers OCR and the resulting metadata is updated correctly for the page."):
        pages = ingest_pdf(path, document_id="d4", filename="short_words.pdf")
    assert pages[0].ocr_used is True
    assert pages[0].word_count >= 15


def test_good_text_skipped_even_when_other_pages_need_ocr(tmp_path):
    path = tmp_path / "mixed2.pdf"
    create_pdf(path, [LONG_GOOD, SHORT_CHAR])
    # OCR should be called only for second page
    with patch("backend.app.ingestion.pdf._ocr_page", return_value="OCR for second page only that is long enough") as mock:
        pages = ingest_pdf(path, document_id="d5", filename="mixed2.pdf")
    assert mock.call_count == 1
    assert pages[0].ocr_used is False
    assert pages[0].ocr_error is None
    assert pages[1].ocr_used is True


def test_mixed_pdf_independent_decisions(tmp_path):
    path = tmp_path / "mixed.pdf"
    create_pdf(path, [LONG_GOOD, None, SHORT_WORDS, LONG_GOOD])
    ocr_results = [
        "OCR text for empty page that is long enough to pass thresholds for testing mixed pdf handling correctly.",
        "OCR text for short words page that is also long enough to pass thresholds.",
    ]
    with patch("backend.app.ingestion.pdf._ocr_page", side_effect=ocr_results) as mock:
        pages = ingest_pdf(path, document_id="d6", filename="mixed.pdf")
    # Pages 0 and 3 are good -> no OCR, pages 1 and 2 -> OCR
    assert pages[0].ocr_used is False
    assert pages[1].ocr_used is True
    assert pages[2].ocr_used is True
    assert pages[3].ocr_used is False
    assert mock.call_count == 2
    assert pages[0].page_number == 1
    assert pages[1].page_number == 2
    assert pages[2].page_number == 3
    assert pages[3].page_number == 4
    # Source fidelity preserved
    for p in pages:
        assert p.document_id == "d6"
        assert p.filename == "mixed.pdf"


def test_ocr_failure_preserved(tmp_path):
    path = tmp_path / "fail.pdf"
    create_pdf(path, [None, LONG_GOOD])
    with patch("backend.app.ingestion.pdf._ocr_page", side_effect=Exception("tesseract not found")) as mock:
        pages = ingest_pdf(path, document_id="d7", filename="fail.pdf")
    # First page OCR attempted but failed -> preserved with ocr_error, doc not crashed
    assert len(pages) == 2
    assert pages[0].ocr_used is True
    assert pages[0].ocr_error == "tesseract not found"
    assert pages[0].is_empty is True  # still empty native
    assert pages[0].error is None
    # Second page good -> not affected
    assert pages[1].ocr_used is False
    assert pages[1].ocr_error is None
    assert mock.call_count == 1


def test_ocr_result_updates_metadata(tmp_path):
    path = tmp_path / "result.pdf"
    create_pdf(path, [SHORT_CHAR])
    ocr_text = "This is OCR extracted text from a scanned legal document that contains sufficient length and word count to be considered high quality and should update the page metadata correctly for downstream processing."
    with patch("backend.app.ingestion.pdf._ocr_page", return_value=ocr_text):
        pages = ingest_pdf(path, document_id="d8", filename="result.pdf")
    p = pages[0]
    assert p.ocr_used is True
    assert p.ocr_error is None
    assert p.text == ocr_text
    assert p.char_count == len(ocr_text)
    assert p.word_count == len(ocr_text.strip().split())
    assert p.is_empty is False
    assert p.error is None
    # Ensure ocr_used True only when actually used
    path2 = tmp_path / "good2.pdf"
    create_pdf(path2, [LONG_GOOD])
    with patch("backend.app.ingestion.pdf._ocr_page") as mock2:
        pages2 = ingest_pdf(path2, document_id="d9", filename="good2.pdf")
    mock2.assert_not_called()
    assert pages2[0].ocr_used is False


def test_ocr_bytes_path(tmp_path):
    path = tmp_path / "bytes.pdf"
    create_pdf(path, [SHORT_CHAR])
    data = path.read_bytes()
    with patch("backend.app.ingestion.pdf._ocr_page", return_value="OCR for bytes path that is long enough to exceed thresholds correctly."):
        pages = ingest_pdf_bytes(data, document_id="d10", filename="bytes.pdf")
    assert pages[0].ocr_used is True
    assert pages[0].document_id == "d10"


def test_ocr_empty_result_preserved(tmp_path):
    path = tmp_path / "ocr_empty.pdf"
    create_pdf(path, [None])
    with patch("backend.app.ingestion.pdf._ocr_page", return_value=""):
        pages = ingest_pdf(path, document_id="d11", filename="ocr_empty.pdf")
    assert pages[0].ocr_used is True
    assert pages[0].ocr_error is None
    assert pages[0].text == ""
    assert pages[0].is_empty is True


@pytest.mark.integration
def test_real_tesseract_integration(tmp_path):
    """Requires local Tesseract installed. Run with: pytest -m integration"""
    import shutil

    if shutil.which("tesseract") is None:
        pytest.skip("Tesseract not installed")
    path = tmp_path / "real.pdf"
    create_pdf(path, [None])
    # Draw some text as image? For real OCR we need rendered image with text,
    # but empty page will OCR to empty; just verify no crash
    pages = ingest_pdf(path, document_id="d-int", filename="real.pdf")
    assert pages[0].ocr_used is True
