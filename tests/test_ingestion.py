import pymupdf
import pytest
from pathlib import Path
from unittest.mock import patch

from backend.app.ingestion import analyze_quality, ingest_pdf, ingest_pdf_bytes, ingest_pdfs
from backend.app.ingestion.quality import QualityInfo

# Long legal paragraph >100 chars and >=15 words to avoid OCR trigger
LONG_TEXT = (
    "This is a comprehensive legal case summary concerning breach of contract where the petitioner "
    "alleged failure to deliver goods as per agreement and the court examined evidence, arguments, "
    "and statutory provisions in detail before pronouncing judgment on the merits with final order."
)
LONG_TEXT_2 = (
    "The judgment discusses procedural history, issues framed, relevant laws under Indian Contract Act, "
    "court reasoning based on precedent, and the final decision ordering relief to the petitioner with costs."
)


def create_pdf(path: Path, page_texts: list[str | None]):
    """Create a PDF with one entry per page. None = empty page, string = text page."""
    doc = pymupdf.open()
    for text in page_texts:
        page = doc.new_page()
        if text is not None:
            page.insert_text((72, 72), text)
        else:
            page.draw_rect(pymupdf.Rect(50, 50, 200, 200), color=(0, 0, 0))
    doc.save(str(path))
    doc.close()


def test_single_page_normal_text(tmp_path):
    path = tmp_path / "normal.pdf"
    create_pdf(path, [LONG_TEXT])

    pages = ingest_pdf(path, document_id="doc-1", filename="normal.pdf")

    assert len(pages) == 1
    p = pages[0]
    assert p.page_number == 1
    assert p.document_id == "doc-1"
    assert p.filename == "normal.pdf"
    assert LONG_TEXT[:20] in p.text
    assert p.ocr_used is False
    assert p.error is None
    assert p.ocr_error is None
    assert p.is_empty is False
    assert p.char_count > 100
    assert p.word_count >= 15


def test_empty_page_detection(tmp_path):
    path = tmp_path / "empty.pdf"
    create_pdf(path, [None])

    with patch("backend.app.ingestion.pdf._ocr_page", return_value="") as mock_ocr:
        pages = ingest_pdf(path, document_id="doc-2", filename="empty.pdf")

    assert len(pages) == 1
    p = pages[0]
    assert p.is_empty is True
    assert p.word_count == 0
    assert p.char_count == len(p.text)
    assert p.ocr_used is True
    assert p.ocr_error is None
    assert p.error is None
    assert p.char_count < 100
    assert p.word_count < 15
    mock_ocr.assert_called_once()


def test_multiple_pages_and_page_numbering(tmp_path):
    path = tmp_path / "multi.pdf"
    create_pdf(path, [LONG_TEXT, LONG_TEXT_2, LONG_TEXT])

    pages = ingest_pdf(path, document_id="doc-3", filename="multi.pdf")

    assert len(pages) == 3
    assert [p.page_number for p in pages] == [1, 2, 3]
    assert LONG_TEXT[:10] in pages[0].text
    assert LONG_TEXT_2[:10] in pages[1].text
    for p in pages:
        assert p.document_id == "doc-3"
        assert p.filename == "multi.pdf"
        assert p.ocr_used is False
        assert p.error is None
        assert p.ocr_error is None


def test_document_id_and_filename_propagation(tmp_path):
    path = tmp_path / "prop.pdf"
    create_pdf(path, [LONG_TEXT])

    pages = ingest_pdf(path, document_id="my-doc-xyz", filename="myfile.pdf")
    assert pages[0].document_id == "my-doc-xyz"
    assert pages[0].filename == "myfile.pdf"

    data = path.read_bytes()
    pages2 = ingest_pdf_bytes(data, document_id="bytes-id", filename="bytes.pdf")
    assert pages2[0].document_id == "bytes-id"
    assert pages2[0].filename == "bytes.pdf"
    assert pages2[0].ocr_used is False


def test_char_and_word_counts(tmp_path):
    path = tmp_path / "counts.pdf"
    create_pdf(path, [LONG_TEXT])
    pages = ingest_pdf(path, document_id="doc-4", filename="counts.pdf")
    p = pages[0]
    assert p.char_count == len(p.text)
    expected_words = len(p.text.strip().split())
    assert p.word_count == expected_words
    q = analyze_quality("  hello   world  ")
    assert q == QualityInfo(char_count=len("  hello   world  "), word_count=2, is_empty=False)
    q2 = analyze_quality("   ")
    assert q2.is_empty is True
    assert q2.word_count == 0


def test_low_text_detection(tmp_path):
    """Very short text should be flagged as low-quality and trigger OCR."""
    path = tmp_path / "low.pdf"
    create_pdf(path, ["Hi", "This is a slightly longer text but still short."])
    # Mock OCR to return similarly short text so counts remain < thresholds
    with patch("backend.app.ingestion.pdf._ocr_page", side_effect=["Hi", "This is a slightly longer text but still short."]):
        pages = ingest_pdf(path, document_id="doc-5", filename="low.pdf")

    p0 = pages[0]
    assert p0.word_count < 15
    assert p0.char_count < 100
    assert p0.is_empty is False
    assert p0.ocr_used is True
    assert p0.ocr_error is None
    p1 = pages[1]
    assert p1.char_count < 100
    assert p1.word_count < 15
    assert p1.ocr_used is True


def test_malformed_pdf_raises(tmp_path):
    path = tmp_path / "bad.pdf"
    path.write_bytes(b"This is not a PDF content at all")

    with pytest.raises(ValueError, match="Failed to open|does not appear to be a PDF"):
        ingest_pdf(path, document_id="doc-6", filename="bad.pdf")

    with pytest.raises(ValueError):
        ingest_pdf_bytes(b"not a pdf bytes", document_id="doc-6", filename="bad2.pdf")


def test_missing_and_unreadable_file(tmp_path):
    missing = tmp_path / "missing.pdf"
    with pytest.raises(FileNotFoundError):
        ingest_pdf(missing, document_id="doc-7", filename="missing.pdf")

    txt_path = tmp_path / "notpdf.txt"
    txt_path.write_text("hello")
    with pytest.raises(ValueError, match="Not a PDF"):
        ingest_pdf(txt_path, document_id="doc-8", filename="notpdf.txt")

    with pytest.raises(ValueError, match="Empty PDF data"):
        ingest_pdf_bytes(b"", document_id="doc-9", filename="empty.pdf")


def test_multi_file_ingestion(tmp_path):
    p1 = tmp_path / "a.pdf"
    p2 = tmp_path / "b.pdf"
    create_pdf(p1, [LONG_TEXT, LONG_TEXT_2])
    create_pdf(p2, [LONG_TEXT])

    results = ingest_pdfs(
        [
            (p1, "doc-A", "a.pdf"),
            (p2, "doc-B", "b.pdf"),
        ]
    )

    assert len(results) == 2
    assert len(results[0]) == 2
    assert len(results[1]) == 1
    assert results[0][0].page_number == 1
    assert results[0][1].page_number == 2
    assert results[1][0].page_number == 1
    assert results[0][0].document_id == "doc-A"
    assert results[1][0].document_id == "doc-B"
    for batch in results:
        for p in batch:
            assert p.ocr_used is False
            assert p.ocr_error is None


def test_ocr_used_for_mixed_pdf(tmp_path):
    """Good pages keep native, poor/empty pages trigger OCR independently."""
    path = tmp_path / "ocr.pdf"
    create_pdf(path, [LONG_TEXT, None, LONG_TEXT_2])
    # Mock OCR only for the empty page
    with patch("backend.app.ingestion.pdf._ocr_page", return_value="Recovered OCR text for scanned legal page that is sufficiently long to pass quality thresholds and be useful for downstream processing."):
        pages = ingest_pdf(path, document_id="doc-10", filename="ocr.pdf")

    assert pages[0].ocr_used is False
    assert pages[0].error is None
    assert pages[0].ocr_error is None
    assert pages[1].ocr_used is True
    assert pages[1].ocr_error is None
    assert pages[1].char_count > 100
    assert pages[2].ocr_used is False
