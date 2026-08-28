import fitz
import pytest
from pathlib import Path

from backend.app.ingestion import analyze_quality, ingest_pdf, ingest_pdf_bytes, ingest_pdfs
from backend.app.ingestion.quality import QualityInfo


def create_pdf(path: Path, page_texts: list[str | None]):
    """Create a PDF with one entry per page. None = empty page, string = text page."""
    doc = fitz.open()
    for text in page_texts:
        page = doc.new_page()
        if text is not None:
            # Insert text at a reasonable position
            page.insert_text((72, 72), text)
        else:
            # Leave empty — optionally draw a rectangle to simulate image-only
            page.draw_rect(fitz.Rect(50, 50, 200, 200), color=(0, 0, 0))
    doc.save(str(path))
    doc.close()


def test_single_page_normal_text(tmp_path):
    path = tmp_path / "normal.pdf"
    text = "This is a legal case summary about contract breach and court reasoning."
    create_pdf(path, [text])

    pages = ingest_pdf(path, document_id="doc-1", filename="normal.pdf")

    assert len(pages) == 1
    p = pages[0]
    assert p.page_number == 1
    assert p.document_id == "doc-1"
    assert p.filename == "normal.pdf"
    assert text in p.text
    assert p.ocr_used is False
    assert p.error is None
    assert p.is_empty is False
    assert p.char_count > 0
    assert p.word_count >= 10


def test_empty_page_detection(tmp_path):
    path = tmp_path / "empty.pdf"
    create_pdf(path, [None])

    pages = ingest_pdf(path, document_id="doc-2", filename="empty.pdf")

    assert len(pages) == 1
    p = pages[0]
    assert p.is_empty is True
    assert p.word_count == 0
    assert p.char_count == len(p.text)  # should be 0 or minimal whitespace
    assert p.ocr_used is False
    assert p.error is None
    # Empty page qualifies for future OCR gate (<100 chars, <15 words)
    assert p.char_count < 100
    assert p.word_count < 15


def test_multiple_pages_and_page_numbering(tmp_path):
    path = tmp_path / "multi.pdf"
    create_pdf(path, ["Page one content", "Page two content", "Page three content"])

    pages = ingest_pdf(path, document_id="doc-3", filename="multi.pdf")

    assert len(pages) == 3
    assert [p.page_number for p in pages] == [1, 2, 3]
    assert pages[0].text.strip().startswith("Page one")
    assert pages[1].text.strip().startswith("Page two")
    assert pages[2].text.strip().startswith("Page three")
    # Each page retains document_id/filename
    for p in pages:
        assert p.document_id == "doc-3"
        assert p.filename == "multi.pdf"
        assert p.ocr_used is False
        assert p.error is None


def test_document_id_and_filename_propagation(tmp_path):
    path = tmp_path / "prop.pdf"
    create_pdf(path, ["Hello world"])

    pages = ingest_pdf(path, document_id="my-doc-xyz", filename="myfile.pdf")
    assert pages[0].document_id == "my-doc-xyz"
    assert pages[0].filename == "myfile.pdf"

    # Also test bytes path
    data = path.read_bytes()
    pages2 = ingest_pdf_bytes(data, document_id="bytes-id", filename="bytes.pdf")
    assert pages2[0].document_id == "bytes-id"
    assert pages2[0].filename == "bytes.pdf"


def test_char_and_word_counts(tmp_path):
    path = tmp_path / "counts.pdf"
    text = "Hello world 123"
    create_pdf(path, [text])
    pages = ingest_pdf(path, document_id="doc-4", filename="counts.pdf")
    p = pages[0]
    # Directly compare against quality function contract
    # fitz may add newline, so counts should match len(text) within tolerance
    assert p.char_count == len(p.text)
    # Word count from stripped text
    expected_words = len(p.text.strip().split())
    assert p.word_count == expected_words
    # Also test detached quality function
    q = analyze_quality("  hello   world  ")
    assert q == QualityInfo(char_count=len("  hello   world  "), word_count=2, is_empty=False)
    q2 = analyze_quality("   ")
    assert q2.is_empty is True
    assert q2.word_count == 0


def test_low_text_detection(tmp_path):
    """Very short text should be flagged as low-quality for future OCR gate."""
    path = tmp_path / "low.pdf"
    create_pdf(path, ["Hi", "This is a slightly longer text but still short."])
    pages = ingest_pdf(path, document_id="doc-5", filename="low.pdf")

    p0 = pages[0]
    assert p0.word_count < 15
    assert p0.char_count < 100
    assert p0.is_empty is False  # has some text but still low
    # Second page also below threshold
    p1 = pages[1]
    assert p1.char_count < 100
    assert p1.word_count < 15


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

    # Wrong extension should be rejected even if content is PDF-like
    txt_path = tmp_path / "notpdf.txt"
    txt_path.write_text("hello")
    with pytest.raises(ValueError, match="Not a PDF"):
        ingest_pdf(txt_path, document_id="doc-8", filename="notpdf.txt")

    # Empty bytes should fail
    with pytest.raises(ValueError, match="Empty PDF data"):
        ingest_pdf_bytes(b"", document_id="doc-9", filename="empty.pdf")


def test_multi_file_ingestion(tmp_path):
    p1 = tmp_path / "a.pdf"
    p2 = tmp_path / "b.pdf"
    create_pdf(p1, ["Doc A page 1", "Doc A page 2"])
    create_pdf(p2, ["Doc B page 1"])

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
    # No OCR in Phase 2
    for batch in results:
        for p in batch:
            assert p.ocr_used is False


def test_ocr_used_always_false(tmp_path):
    path = tmp_path / "ocr.pdf"
    create_pdf(path, ["Some text", None, "More text"])
    pages = ingest_pdf(path, document_id="doc-10", filename="ocr.pdf")
    for p in pages:
        assert p.ocr_used is False
        # error should be None for successful pages
        assert p.error is None
