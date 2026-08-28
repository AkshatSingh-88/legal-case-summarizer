"""Local NLP evidence layer."""

from backend.app.nlp.evidence import Evidence, build_evidence
from backend.app.nlp.extract import split_sentences, textrank_scores, tfidf_scores
from backend.app.nlp.entities import extract_case_numbers, extract_dates, extract_provisions, extract_entities_spacy

__all__ = [
    "Evidence",
    "build_evidence",
    "split_sentences",
    "textrank_scores",
    "tfidf_scores",
    "extract_dates",
    "extract_case_numbers",
    "extract_provisions",
    "extract_entities_spacy",
]
