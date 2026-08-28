"""Tesseract OCR behind a simple function boundary.

Single responsibility: image -> text. No managers/factories.
Replaceable by swapping this module's implementation.
"""

import io
import logging

from PIL import Image

logger = logging.getLogger(__name__)


def _configure_tesseract(cmd: str | None) -> None:
    if cmd:
        try:
            import pytesseract

            pytesseract.pytesseract.tesseract_cmd = cmd
        except Exception:
            pass


def _preprocess_image(image: Image.Image) -> Image.Image:
    # Simple preprocessing: grayscale is sufficient for Phase 3 without over-engineering.
    # Keep resolution/DPI handling in the render step (pdf.py).
    if image.mode != "L":
        image = image.convert("L")
    return image


def ocr_image(image: Image.Image, lang: str = "eng", tesseract_cmd: str | None = None) -> str:
    """OCR a PIL Image using Tesseract. Raises on failure for caller to record."""
    _configure_tesseract(tesseract_cmd)

    import pytesseract

    processed = _preprocess_image(image)
    # Use --psm 6 (uniform block of text) suitable for legal documents
    text = pytesseract.image_to_string(processed, lang=lang, config="--psm 6")
    # Normalize: preserve line structure, strip trailing whitespace per line
    if text is None:
        return ""
    # Normalize line endings and strip trailing spaces, keep useful whitespace
    lines = [line.rstrip() for line in text.splitlines()]
    return "\n".join(lines).strip()


def pixmap_to_image(pix) -> Image.Image:
    """Convert PyMuPDF Pixmap to PIL Image via PNG bytes (handles any colorspace)."""
    png_bytes = pix.tobytes("png")
    return Image.open(io.BytesIO(png_bytes))
