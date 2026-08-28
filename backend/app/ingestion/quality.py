"""Basic text-quality signals for page-level OCR gating.

Phase 2 keeps this intentionally small: char/word counts and empty detection.
Phase 3 will apply the agreed OCR rule (empty/error or <100 chars or <15 words).
"""

from dataclasses import dataclass


@dataclass
class QualityInfo:
    char_count: int
    word_count: int
    is_empty: bool


def analyze_quality(text: str) -> QualityInfo:
    """Calculate basic quality signals from extracted text."""
    if text is None:
        text = ""

    # Preserve text as-is for counting; strip only for emptiness check
    stripped = text.strip()
    char_count = len(text)
    # Word count based on whitespace split of stripped text
    word_count = len(stripped.split()) if stripped else 0
    is_empty = word_count == 0

    return QualityInfo(
        char_count=char_count,
        word_count=word_count,
        is_empty=is_empty,
    )
