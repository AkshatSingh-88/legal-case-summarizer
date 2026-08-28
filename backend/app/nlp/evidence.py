"""Evidence layer — builds ranked Evidence from normalized pages."""

import uuid
from dataclasses import dataclass, field

from backend.app.config import get_settings
from backend.app.ingestion.models import IngestedPage
from backend.app.nlp.entities import (
    extract_case_numbers,
    extract_dates,
    extract_entities_spacy,
    extract_provisions,
)
from backend.app.nlp.extract import split_sentences, textrank_scores, tfidf_scores


@dataclass
class Evidence:
    id: str
    type: str  # important_sentence, important_passage, entity, date, case_number, legal_provision
    text: str
    score: float
    document_id: str
    filename: str
    page_number: int
    meta: dict = field(default_factory=dict)


def _entity_density(sentence: str, entity_texts: list[str]) -> float:
    words = len(sentence.strip().split())
    if words == 0:
        return 0.0
    # Count how many entity mentions appear in this sentence (simple substring)
    count = 0
    lower_sent = sentence.lower()
    for ent in entity_texts:
        if ent.lower() in lower_sent:
            count += 1
    return min(count / max(words, 1) * 5, 1.0)  # scale, cap at 1.0


def build_evidence(pages: list[IngestedPage]) -> list[Evidence]:
    """Build evidence from normalized pages.

    Groups by document_id for per-document TextRank, retains per-page provenance,
    never invents page numbers, works even if some pages are empty.
    """
    if not pages:
        return []

    settings = get_settings()
    w_tr = settings.evidence_textrank_weight
    w_tfidf = settings.evidence_tfidf_weight
    w_ent = settings.evidence_entity_weight

    # Group pages by document_id
    from collections import defaultdict

    by_doc: dict[str, list[IngestedPage]] = defaultdict(list)
    for p in pages:
        by_doc[p.document_id].append(p)

    all_evidence: list[Evidence] = []

    for doc_id, doc_pages in by_doc.items():
        # Sort pages by page_number for deterministic ordering
        doc_pages = sorted(doc_pages, key=lambda pp: pp.page_number)
        # Collect sentences with provenance
        sentences: list[str] = []
        sentence_provenance: list[tuple[str, str, int]] = []  # (doc_id, filename, page_number)

        for pg in doc_pages:
            if pg.is_empty or not pg.text.strip():
                continue
            sents = split_sentences(pg.text)
            for s in sents:
                sentences.append(s)
                sentence_provenance.append((pg.document_id, pg.filename, pg.page_number))

        # Compute per-sentence scores
        if sentences:
            tr_scores = textrank_scores(
                sentences,
                top_k=settings.evidence_textrank_top_k,
                threshold=settings.evidence_textrank_threshold,
                max_sentences=settings.evidence_max_sentences_for_textrank,
            )
            tfidf = tfidf_scores(sentences)
        else:
            tr_scores = []
            tfidf = []

        # Collect all entity-like texts for density calculation (per doc)
        # We extract globally per document text to avoid per-sentence repeated NER
        doc_text = " ".join(p.text for p in doc_pages if p.text)
        all_case_numbers = extract_case_numbers(doc_text)
        all_dates = extract_dates(doc_text)
        all_provisions = extract_provisions(doc_text)
        all_spacy = extract_entities_spacy(doc_text)
        spacy_texts = [t for t, _, _ in all_spacy]
        density_entities = all_case_numbers + all_dates + all_provisions + spacy_texts

        # Important sentences
        for idx, sent in enumerate(sentences):
            doc_id_p, filename_p, page_num = sentence_provenance[idx]
            tr = tr_scores[idx] if idx < len(tr_scores) else 0.0
            tf = tfidf[idx] if idx < len(tfidf) else 0.0
            dens = _entity_density(sent, density_entities)
            combined = w_tr * tr + w_tfidf * tf + w_ent * dens
            # Clamp
            combined = max(0.0, min(1.0, combined))
            all_evidence.append(
                Evidence(
                    id=str(uuid.uuid4()),
                    type="important_sentence",
                    text=sent,
                    score=combined,
                    document_id=doc_id_p,
                    filename=filename_p,
                    page_number=page_num,
                    meta={
                        "textrank": tr,
                        "tfidf": tf,
                        "entity_density": dens,
                        "sentence_index": idx,
                    },
                )
            )

        # Dates, case numbers, provisions, entities as separate evidence items
        # We emit per occurrence with page provenance by scanning each page's text
        for pg in doc_pages:
            if pg.is_empty or not pg.text.strip():
                continue
            # Dates on this page
            for d in extract_dates(pg.text):
                all_evidence.append(
                    Evidence(
                        id=str(uuid.uuid4()),
                        type="date",
                        text=d,
                        score=0.9,
                        document_id=pg.document_id,
                        filename=pg.filename,
                        page_number=pg.page_number,
                        meta={"entity_label": "DATE"},
                    )
                )
            for cn in extract_case_numbers(pg.text):
                all_evidence.append(
                    Evidence(
                        id=str(uuid.uuid4()),
                        type="case_number",
                        text=cn,
                        score=0.95,
                        document_id=pg.document_id,
                        filename=pg.filename,
                        page_number=pg.page_number,
                        meta={"entity_label": "CASE_NUMBER"},
                    )
                )
            for prov in extract_provisions(pg.text):
                all_evidence.append(
                    Evidence(
                        id=str(uuid.uuid4()),
                        type="legal_provision",
                        text=prov,
                        score=0.9,
                        document_id=pg.document_id,
                        filename=pg.filename,
                        page_number=pg.page_number,
                        meta={"entity_label": "LEGAL_PROVISION"},
                    )
                )
            # Optional spaCy entities per page
            for ent_text, label, _ in extract_entities_spacy(pg.text):
                all_evidence.append(
                    Evidence(
                        id=str(uuid.uuid4()),
                        type="entity",
                        text=ent_text,
                        score=0.7,
                        document_id=pg.document_id,
                        filename=pg.filename,
                        page_number=pg.page_number,
                        meta={"entity_label": label},
                    )
                )

    # Sort by score descending for convenience, but provenance retained
    all_evidence.sort(key=lambda e: e.score, reverse=True)
    return all_evidence
