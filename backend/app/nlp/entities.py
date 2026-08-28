"""Deterministic regex extraction for legal metadata + optional spaCy NER."""

import re

# --- Regex patterns ---

_DATE_PATTERNS = [
    # 12 March 2024, 12th March 2024
    re.compile(r"\b\d{1,2}(?:st|nd|rd|th)?\s+(?:January|February|March|April|May|June|July|August|September|October|November|December)\s+\d{4}\b", re.IGNORECASE),
    # 04/12/2024, 04-12-2024, 04.12.2024
    re.compile(r"\b\d{1,2}[-/.]\d{1,2}[-/.]\d{2,4}\b"),
    # ISO 2024-03-12
    re.compile(r"\b\d{4}[-/]\d{1,2}[-/]\d{1,2}\b"),
]

_CASE_NUMBER_PATTERNS = [
    re.compile(r"\b(?:Civil|Criminal|Writ|Special Leave|SLP|Appeal|C\.?A\.?|CRL\.?|WP|W\.P\.?)\s*(?:Appeal|Petition|No\.)?\s*No\.?\s*\d+/\d{4}\b", re.IGNORECASE),
    re.compile(r"\bCase No\.\s*\d+/\d{4}\b", re.IGNORECASE),
    re.compile(r"\bNo\.\s*\d+/\d{4}\b"),
]

_PROVISION_PATTERNS = [
    re.compile(r"\bSection\s+\d+[A-Z]?(?:\s*\(\s*\d+\s*\))?\s*(?:of\s+the\s+)?[A-Z][A-Za-z\s]*Act\b", re.IGNORECASE),
    re.compile(r"\bSection\s+\d+[A-Z]?(?:\s*\(\s*\d+\s*\))?\s+IPC\b", re.IGNORECASE),
    re.compile(r"\bSection\s+\d+[A-Z]?\s+CrPC\b", re.IGNORECASE),
    re.compile(r"\bArticle\s+\d+[A-Z]?\b", re.IGNORECASE),
    re.compile(r"\bOrder\s+\d+\s+Rule\s+\d+\b", re.IGNORECASE),
    re.compile(r"\bIPC\s+Section\s+\d+\b", re.IGNORECASE),
]


def _find_all(patterns: list[re.Pattern], text: str) -> list[str]:
    results: list[str] = []
    for pat in patterns:
        for m in pat.finditer(text):
            # Strip but preserve original matched text
            val = m.group(0).strip()
            if val and val not in results:
                results.append(val)
    return results


def extract_dates(text: str) -> list[str]:
    return _find_all(_DATE_PATTERNS, text)


def extract_case_numbers(text: str) -> list[str]:
    return _find_all(_CASE_NUMBER_PATTERNS, text)


def extract_provisions(text: str) -> list[str]:
    return _find_all(_PROVISION_PATTERNS, text)


def extract_entities_spacy(text: str) -> list[tuple[str, str, str]]:
    """Optional spaCy NER. Returns list of (text, label, explain) or [] if unavailable.

    Gracefully falls back: if spaCy or model missing, returns [] without raising.
    """
    try:
        import spacy

        # Try to get already-loaded model cached on function attribute
        nlp = getattr(extract_entities_spacy, "_nlp", None)
        if nlp is None:
            try:
                nlp = spacy.load("en_core_web_sm")
            except OSError:
                return []
            extract_entities_spacy._nlp = nlp  # type: ignore[attr-defined]

        doc = nlp(text)
        allowed = {"PERSON", "ORG", "GPE", "LOC", "FAC", "COURT" if "COURT" in nlp.pipe_labels.get("ner", []) else "ORG"}
        results: list[tuple[str, str, str]] = []
        for ent in doc.ents:
            if ent.label_ in allowed or ent.label_ in ("PERSON", "ORG", "GPE", "LOC"):
                results.append((ent.text, ent.label_, ent.label_))
        return results
    except ImportError:
        return []
    except Exception:
        return []
