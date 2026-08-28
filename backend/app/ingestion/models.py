from dataclasses import dataclass


@dataclass
class IngestedPage:
    """Normalized representation of a single PDF page.

    Produced independently per page so a PDF with mixed native/scanned
    content is handled correctly. `ocr_used` is always False in Phase 2;
    Phase 3 will set it for pages sent through OCR.
    """

    document_id: str
    filename: str
    page_number: int  # 1-indexed, preserves original PDF ordering
    text: str
    char_count: int
    word_count: int
    is_empty: bool
    ocr_used: bool
    error: str | None = None
