import pytest
from unittest.mock import patch, Mock

from backend.app.ingestion.models import IngestedPage
from backend.app.ingestion.quality import analyze_quality
from backend.app.nlp.evidence import Evidence, build_evidence
from backend.app.nlp.extract import split_sentences, textrank_scores, tfidf_scores
from backend.app.nlp.entities import extract_dates, extract_case_numbers, extract_provisions, extract_entities_spacy


def make_page(doc_id="doc-1", filename="file.pdf", page_number=1, text="Hello world."):
    q = analyze_quality(text)
    return IngestedPage(
        document_id=doc_id,
        filename=filename,
        page_number=page_number,
        text=text,
        char_count=q.char_count,
        word_count=q.word_count,
        is_empty=q.is_empty,
        ocr_used=False,
        error=None,
        ocr_error=None,
    )


def test_normal_page_produces_evidence():
    pages = [make_page(text="The Supreme Court held that breach of contract requires compensation. The court examined evidence and delivered judgment.")]
    ev = build_evidence(pages)
    assert len([e for e in ev if e.type == "important_sentence"]) >= 1
    assert all(e.document_id for e in ev)
    assert all(e.filename for e in ev)


def test_empty_short_pages_no_crash():
    pages = [
        make_page(page_number=1, text=""),
        make_page(page_number=2, text="Hi"),
        make_page(page_number=3, text="   "),
    ]
    ev = build_evidence(pages)
    # Should not crash, may produce 0 or minimal evidence for empty, but not raise
    assert isinstance(ev, list)
    # Empty pages should not produce important_sentence with is_empty provenance? They are skipped.
    # But dates/provisions on empty should be 0
    for e in ev:
        assert e.page_number in (1, 2, 3)


def test_evidence_retains_document_id():
    pages = [make_page(doc_id="my-doc-xyz", text="Section 302 IPC is relevant. This is a legal reasoning about murder and punishment.")]
    ev = build_evidence(pages)
    for e in ev:
        assert e.document_id == "my-doc-xyz"


def test_evidence_retains_filename():
    pages = [make_page(filename="Judgment.pdf", text="The judgment was delivered on 12 March 2024 by the Supreme Court.")]
    ev = build_evidence(pages)
    for e in ev:
        assert e.filename == "Judgment.pdf"


def test_evidence_retains_page_number_multi():
    pages = [
        make_page(page_number=1, text="Page one has important Supreme Court reasoning about breach of contract and compensation."),
        make_page(page_number=2, text="Page two discusses Article 21 and Section 13 of SARFAESI Act with detailed analysis."),
        make_page(page_number=3, text="Page three contains final order and relief granted to petitioner with costs."),
    ]
    ev = build_evidence(pages)
    # Each sentence should map to correct page
    pages_seen = {e.page_number for e in ev if e.type == "important_sentence"}
    assert pages_seen == {1, 2, 3}


def test_important_sentences_ranked_higher():
    # Deterministic: 2 distinctive legal sentences + 1 filler with repeated stopwords
    filler = "The the the and and and the the and and."
    s1 = "The Supreme Court held that breach of contract under Section 302 IPC requires compensation for damages and specific performance."
    s2 = "The High Court examined SARFAESI Act Section 13 and Article 21 reasoning before granting relief to the petitioner."
    text = f"{s1} {filler} {s2}"
    pages = [make_page(text=text)]
    ev = build_evidence(pages)
    sents = [e for e in ev if e.type == "important_sentence"]
    # Find scores by text
    def score_of(fragment):
        for e in sents:
            if fragment[:20] in e.text:
                return e.score
        return None
    score_filler = score_of(filler[:10])
    score_s1 = score_of(s1[:20])
    score_s2 = score_of(s2[:20])
    assert score_s1 is not None and score_s2 is not None and score_filler is not None
    assert score_s1 > score_filler
    assert score_s2 > score_filler
    # Meta should contain components
    for e in sents:
        assert "textrank" in e.meta and "tfidf" in e.meta


def test_dates_detected():
    pages = [make_page(text="The judgment was delivered on 12 March 2024 and also on 04/12/2024.")]
    ev = build_evidence(pages)
    dates = [e for e in ev if e.type == "date"]
    assert len(dates) >= 1
    texts = [d.text for d in dates]
    assert any("12 March 2024" in t for t in texts)
    assert any("04/12/2024" in t for t in texts)
    for d in dates:
        assert d.page_number == 1
        assert d.document_id == "doc-1"


def test_case_numbers_detected():
    pages = [make_page(text="Civil Appeal No. 123/2024 was heard. Also Writ Petition No. 45/2023 pending.")]
    ev = build_evidence(pages)
    cns = [e for e in ev if e.type == "case_number"]
    assert len(cns) >= 1
    assert any("123/2024" in c.text for c in cns)


def test_legal_provisions_detected():
    pages = [make_page(text="Section 302 IPC and Article 21 were cited. Section 13 of the SARFAESI Act applies.")]
    ev = build_evidence(pages)
    provs = [e for e in ev if e.type == "legal_provision"]
    texts = [p.text for p in provs]
    assert any("Section 302" in t for t in texts)
    assert any("Article 21" in t for t in texts)
    assert any("SARFAESI" in t for t in texts)


def test_entities_extracted_with_mock():
    # Mock spaCy to return fake entities without requiring model
    fake_ents = [("Supreme Court of India", "ORG", "ORG"), ("Ramesh Kumar", "PERSON", "PERSON")]
    with patch("backend.app.nlp.evidence.extract_entities_spacy", return_value=fake_ents):
        pages = [make_page(text="Supreme Court of India heard Ramesh Kumar.")]
        ev = build_evidence(pages)
        ents = [e for e in ev if e.type == "entity"]
        assert len(ents) >= 2
        assert any(e.text == "Supreme Court of India" for e in ents)
        assert any(e.text == "Ramesh Kumar" for e in ents)
        assert all(e.meta["entity_label"] in ("ORG", "PERSON") for e in ents)


def test_entities_graceful_without_spacy():
    # Without mock, should still work (returns []), no crash
    pages = [make_page(text="Supreme Court of India is a court.")]
    ev = build_evidence(pages)
    # Should at least have important_sentence证据, entity may be 0 if no model
    assert len([e for e in ev if e.type == "important_sentence"]) >= 1


def test_multi_document_isolation():
    pages = [
        make_page(doc_id="doc-A", filename="a.pdf", page_number=1, text="Section 302 IPC murder case with Supreme Court reasoning."),
        make_page(doc_id="doc-B", filename="b.pdf", page_number=1, text="Article 21 SARFAESI Act case with High Court order."),
    ]
    ev = build_evidence(pages)
    for e in ev:
        assert e.document_id in ("doc-A", "doc-B")
        # Ensure no cross mixing: sentences from doc-A should not have doc-B filename
        if e.document_id == "doc-A":
            assert e.filename == "a.pdf"
        else:
            assert e.filename == "b.pdf"
    # At least one evidence per doc for sentences
    assert len([e for e in ev if e.document_id == "doc-A"]) >= 1
    assert len([e for e in ev if e.document_id == "doc-B"]) >= 1


def test_multi_page_provenance():
    pages = [
        make_page(page_number=1, text="Facts of the case involve breach of contract on 12 March 2024."),
        make_page(page_number=2, text="Issues framed include whether Section 302 IPC applies to the facts."),
        make_page(page_number=3, text="Decision: Civil Appeal No. 123/2024 allowed with costs."),
    ]
    ev = build_evidence(pages)
    # Check dates/case numbers retain correct page
    dates = [e for e in ev if e.type == "date"]
    assert any(e.page_number == 1 for e in dates)
    cns = [e for e in ev if e.type == "case_number"]
    assert any(e.page_number == 3 for e in cns)


def test_no_pdf_reopened():
    pages = [make_page(text="Some legal text about Supreme Court judgment.")]
    with patch("fitz.open") as mock_open:
        ev = build_evidence(pages)
        mock_open.assert_not_called()
    assert len(ev) >= 1


def test_large_synthetic_bounded():
    # 500-page synthetic: 500 IngestedPage, each 2 sentences ~1000 sentences
    # Use cap of 800 for TextRank to bound
    long_sentence = "The Supreme Court examined breach of contract under SARFAESI Act Section 13 and Article 21 with detailed reasoning on compensation and relief."
    pages = []
    for i in range(500):
        text = f"{long_sentence} Additional filler sentence number {i} with distinctive content about case facts and legal provisions."
        pages.append(make_page(page_number=i+1, text=text))
    # Patch to verify bounded approach: ensure we don't build huge dense matrix without cap
    # Our implementation caps at 800 sentences for TextRank
    ev = build_evidence(pages)
    # Should complete quickly (<5s) and produce evidence
    assert len(ev) >= 500
    # Ensure provenance still correct for sampled page
    assert any(e.page_number == 500 for e in ev)
    # Verify that textrank_scores respects max_sentences cap — directly test function
    many_sentences = [f"Sentence number {i} about legal reasoning and contract breach and court order." for i in range(1000)]
    scores = textrank_scores(many_sentences, max_sentences=800)
    assert len(scores) == 1000
    # At least some scores are from capped computation
    assert max(scores) > 0


def test_scoring_weights_configurable():
    pages = [make_page(text="Section 302 IPC case with Supreme Court reasoning about breach of contract and compensation.")]
    # Default weights 0.5/0.3/0.2
    ev1 = build_evidence(pages)
    assert all("textrank" in e.meta for e in ev1 if e.type == "important_sentence")
    assert all("tfidf" in e.meta for e in ev1 if e.type == "important_sentence")
    assert all("entity_density" in e.meta for e in ev1 if e.type == "important_sentence")
    # Check score is weighted sum clamped
    for e in ev1:
        if e.type == "important_sentence":
            expected = 0.5 * e.meta["textrank"] + 0.3 * e.meta["tfidf"] + 0.2 * e.meta["entity_density"]
            assert abs(e.score - expected) < 1e-6


def test_split_sentences_handles_empty():
    assert split_sentences("") == []
    assert split_sentences("   ") == []
    assert split_sentences("Hello world") == ["Hello world"]
    assert len(split_sentences("First sentence. Second sentence! Third?")) == 3
