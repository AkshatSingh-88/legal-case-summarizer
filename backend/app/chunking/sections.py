"""Simple regex heading detection — no NLP model."""

import re

# Patterns:
#  - "Facts:" / "PROCEDURAL HISTORY:"  -> title ending with colon
#  - "1. Introduction" / "2.1 Background" -> numbered heading
#  - "SECTION 1 — ..." not common but covered by colon rule
_HEADING_COLON = re.compile(r"^[A-Z][A-Za-z0-9 ,\-–—()]{2,80}:\s*$")
_HEADING_NUMBERED = re.compile(r"^\d+(\.\d+)*\.\s+[A-Z].{2,80}$")
_HEADING_ALLCAPS = re.compile(r"^[A-Z][A-Z0-9 \-,]{5,80}:?\s*$")


def is_heading(text: str) -> bool:
    """Return True if text looks like a heading/section boundary."""
    if not text or not text.strip():
        return False
    s = text.strip()
    # Single line heading test — if paragraph has multiple sentences, not a heading
    if "\n" in s or len(s.split(".")) > 2:
        # Use first line only for heading check on multi-line paragraph unlikely
        s = s.split("\n")[0].strip()
    # Short + ends with colon -> heading
    if _HEADING_COLON.match(s):
        return True
    if _HEADING_NUMBERED.match(s):
        return True
    # ALL CAPS short line (allow optional colon)
    if len(s) < 60 and _HEADING_ALLCAPS.match(s) and len(s.split()) <= 6:
        return True
    return False
