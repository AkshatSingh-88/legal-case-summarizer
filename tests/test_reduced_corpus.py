"""Test suite for Phase 10 Reduced-Corpus Quick Mode."""

from unittest.mock import MagicMock, patch
import uuid
import pytest

from backend.app.chunking.chunk import build_chunks
from backend.app.chunking.tokenizer import count_tokens
from backend.app.config import get_settings
from backend.app.file.models import AnalysisItem
from backend.app.ingestion.models import IngestedPage
from backend.app.nlp.corpus import build_reduced_corpus
from backend.app.nlp.evidence import Evidence, build_evidence


def make_page(
    doc_id: str = "doc-1",
    filename: str = "doc1.pdf",
    page_number: int = 1,
    text: str = "Sample sentence one. Sample sentence two.",
) -> IngestedPage:
    return IngestedPage(
        document_id=doc_id,
        filename=filename,
        page_number=page_number,
        text=text,
        char_count=len(text),
        word_count=len(text.split()),
        is_empty=False,
        ocr_used=False,
        error=None,
        ocr_error=None,
    )


def test_token_budget_hard_cap():
    pages = [
        make_page(
            page_number=i + 1,
            text=f"Sentence {i*2 + 1} with substantive legal facts regarding the contract and dispute. Sentence {i*2 + 2} explaining additional background facts.",
        )
        for i in range(10)
    ]
    ev = build_evidence(pages)

    # Set hard cap of 150 tokens total
    reduced = build_reduced_corpus(pages, ev, max_tokens_total=150, max_tokens_per_doc=150)
    total_tokens = sum(count_tokens(p.text) for p in reduced)
    assert total_tokens <= 150
    assert len(reduced) > 0


def test_per_document_budget_cap():
    pages_doc1 = [make_page(doc_id="doc-1", page_number=i + 1, text=f"Doc 1 sentence {i} explaining facts.") for i in range(15)]
    pages_doc2 = [make_page(doc_id="doc-2", page_number=i + 1, text=f"Doc 2 sentence {i} explaining facts.") for i in range(15)]
    all_pages = pages_doc1 + pages_doc2
    ev = build_evidence(all_pages)

    reduced = build_reduced_corpus(all_pages, ev, max_tokens_per_doc=100, max_tokens_total=500)
    tokens_d1 = sum(count_tokens(p.text) for p in reduced if p.document_id == "doc-1")
    tokens_d2 = sum(count_tokens(p.text) for p in reduced if p.document_id == "doc-2")

    assert tokens_d1 <= 100
    assert tokens_d2 <= 100


def test_tier1_anchor_selection():
    text_p1 = "Writ Petition No 123/2024 was instituted before the Supreme Court. The appellant filed an application."
    text_p2 = "Under Section 302 IPC, the trial court convicted the accused on 12 March 2024. The judgment was pronounced."
    pages = [
        make_page(page_number=1, text=text_p1),
        make_page(page_number=2, text=text_p2),
    ]
    ev = build_evidence(pages)

    reduced = build_reduced_corpus(pages, ev, max_tokens_per_doc=500)
    combined_text = " ".join(p.text for p in reduced)

    # Anchor sentences containing petition number and statutory section should be selected
    assert "Writ Petition No 123/2024" in combined_text
    assert "Section 302 IPC" in combined_text


def test_tier1_budget_overflow():
    lines = [
        "Writ Petition No 101/2024 was filed.",
        "Writ Petition No 102/2024 was filed.",
        "Writ Petition No 103/2024 was filed.",
        "Section 302 IPC applies here.",
        "Section 304 IPC applies here.",
        "Order 39 Rule 1 applies here.",
        "The judgment was delivered on 12 March 2024.",
        "The hearing was held on 15 April 2024.",
    ]
    pages = [make_page(page_number=1, text=" ".join(lines))]
    ev = build_evidence(pages)

    # 30% of 200 is 60 tokens cap for Tier 1
    reduced = build_reduced_corpus(pages, ev, max_tokens_per_doc=200, max_tokens_total=200)
    assert len(reduced) > 0
    total_tokens = sum(count_tokens(p.text) for p in reduced)
    assert total_tokens <= 200


def test_unmatched_metadata_never_synthesizes_text():
    pages = [make_page(page_number=1, text="The appellant filed a regular petition before the High Court.")]
    unmatched_ev = Evidence(
        id=str(uuid.uuid4()),
        type="legal_provision",
        text="Section 999 Nonexistent Act",
        score=0.95,
        document_id="doc-1",
        filename="doc1.pdf",
        page_number=1,
        meta={"entity_label": "LEGAL_PROVISION"},
    )
    ev = build_evidence(pages) + [unmatched_ev]

    reduced = build_reduced_corpus(pages, ev)
    combined_text = " ".join(p.text for p in reduced)

    # Must NOT synthesize or hallucinate the unmatched provision
    assert "Section 999 Nonexistent Act" not in combined_text
    assert "The appellant filed a regular petition" in combined_text


def test_duplicate_metadata_no_duplicate_sentences():
    text = "Section 302 IPC was cited by petitioner. In reply respondent also discussed Section 302 IPC."
    pages = [make_page(page_number=1, text=text)]
    ev = build_evidence(pages)

    reduced = build_reduced_corpus(pages, ev)
    assert len(reduced) == 1
    sents = reduced[0].text.split("\n\n")
    assert len(sents) == len(set(sents))


def test_sentence_ordering_within_page():
    s1 = "First sentence of the legal filing on facts."
    s2 = "Second sentence detailing dispute chronology."
    s3 = "Third sentence outlining the relief sought."
    pages = [make_page(page_number=1, text=f"{s1} {s2} {s3}")]
    ev = build_evidence(pages)

    reduced = build_reduced_corpus(pages, ev)
    assert len(reduced) == 1
    page_text = reduced[0].text
    if s1 in page_text and s3 in page_text:
        pos1 = page_text.find(s1)
        pos3 = page_text.find(s3)
        assert pos1 < pos3


def test_page_number_provenance():
    pages = [
        make_page(page_number=3, text="Page three sentence containing Section 10 CPC reasoning."),
        make_page(page_number=7, text="Page seven sentence containing final decree order."),
    ]
    ev = build_evidence(pages)

    reduced = build_reduced_corpus(pages, ev)
    page_numbers = [p.page_number for p in reduced]
    assert page_numbers == [3, 7]


def test_multi_document_anti_starvation():
    large_doc = [make_page(doc_id="large-doc", filename="large.pdf", page_number=i + 1, text=f"Large doc fact sentence {i}.") for i in range(30)]
    small_doc = [make_page(doc_id="small-doc", filename="small.pdf", page_number=1, text="Small doc critical final order decree.")]
    all_pages = large_doc + small_doc
    ev = build_evidence(all_pages)

    reduced = build_reduced_corpus(all_pages, ev, max_tokens_total=500)
    doc_ids = {p.document_id for p in reduced}

    assert "small-doc" in doc_ids
    assert "large-doc" in doc_ids


def test_sparse_document_fewer_than_minimum_sentences():
    pages = [make_page(doc_id="sparse-doc", page_number=1, text="Only single sentence here.")]
    ev = build_evidence(pages)

    reduced = build_reduced_corpus(pages, ev)
    assert len(reduced) == 1
    assert "Only single sentence here." in reduced[0].text


def test_deterministic_selection():
    pages = [
        make_page(page_number=1, text="Sentence A with Section 302 IPC. Sentence B with date 12 March 2024."),
        make_page(page_number=2, text="Sentence C with Writ Petition No 10/2024. Sentence D with observations."),
    ]
    ev = build_evidence(pages)

    run1 = build_reduced_corpus(pages, ev, max_tokens_total=300)
    run2 = build_reduced_corpus(pages, ev, max_tokens_total=300)

    assert len(run1) == len(run2)
    for p1, p2 in zip(run1, run2):
        assert p1.document_id == p2.document_id
        assert p1.page_number == p2.page_number
        assert p1.text == p2.text


def test_context_expansion():
    s0 = "Previous background sentence giving context."
    s1 = "High-scoring substantive finding under Section 302 IPC and Article 21."
    pages = [make_page(page_number=1, text=f"{s0} {s1}")]
    ev = build_evidence(pages)

    reduced = build_reduced_corpus(pages, ev, max_tokens_per_doc=500)
    assert len(reduced) == 1
    assert s1 in reduced[0].text


def test_large_document_reduction_ratio():
    pages = [
        make_page(
            page_number=i + 1,
            text=f"The Supreme Court examined breach of contract under Section {i+1} of SARFAESI Act. Filler sentence {i} explaining facts.",
        )
        for i in range(500)
    ]
    ev = build_evidence(pages)

    full_tokens = sum(count_tokens(p.text) for p in pages)
    reduced = build_reduced_corpus(pages, ev, max_tokens_per_doc=3000, max_tokens_total=3000)
    reduced_tokens = sum(count_tokens(p.text) for p in reduced)

    assert full_tokens > 10000
    assert reduced_tokens <= 3000
    assert reduced_tokens < (full_tokens * 0.30)  # Substantial token reduction


# QUICK SUMMARY DISABLED — test_end_to_end_quick_pipeline_mocked commented out
# def test_end_to_end_quick_pipeline_mocked():
#     from backend.app.case.analyze import analyze_case
#     from backend.app.file.analyze import analyze_file
#     from backend.app.llm.analyze import analyze_chunks
#     from backend.app.presentation import build_quick_summary
#
#     pages = [
#         make_page(page_number=1, text="Writ Petition No 123/2024 filed before Supreme Court. Petitioner claims breach of contract."),
#         make_page(page_number=2, text="Section 73 Contract Act applies. Respondent denied liability."),
#     ]
#     ev = build_evidence(pages)
#
#     # 1. Quick reduced corpus
#     reduced_pages = build_reduced_corpus(pages, ev)
#     assert len(reduced_pages) >= 1
#
#     # 2. Existing build_chunks
#     chunks = build_chunks(reduced_pages, ev)
#     assert len(chunks) >= 1
#
#     # 3. Existing analyze_chunks with mock provider
#     with patch("backend.app.llm.analyze.get_llm_provider", return_value=lambda prompts: [{"facts": ["Contract breach claimed"], "issues": ["Whether breach occurred"]}]):
#         chunk_analyses = analyze_chunks(chunks, ev)
#         assert len(chunk_analyses) == len(chunks)
#
#     # 4. Existing analyze_file with mock provider
#     with patch("backend.app.file.analyze.get_llm_provider", return_value=lambda prompts: [{"facts": [{"text": "Contract breach claimed", "source_refs": ["SRC-001"]}]}]):
#         file_analysis = analyze_file("doc-1", chunks, chunk_analyses)
#         assert file_analysis.status == "complete"
#
#     # 5. Existing analyze_case with mock provider
#     with patch("backend.app.case.analyze.get_llm_provider", return_value=lambda prompts: [{"case_summary": "Dispute on contract breach"}]):
#         case_analysis = analyze_case("case-1", [file_analysis])
#         assert case_analysis.status == "complete"
#
#     # 6. Quick summary presentation
#     quick_summary = build_quick_summary(case_analysis)
#     assert quick_summary is not None
#     assert quick_summary.analysis_mode == "quick"
#     assert quick_summary.is_preliminary is True
#     assert "Preliminary summary" in quick_summary.disclaimer



def test_tight_budget_multi_doc_scaling():
    # 5 documents with 5 sentences each (25 sentences total, ~400 tokens)
    all_pages = []
    for d in range(5):
        doc_id = f"doc-{d}"
        for p in range(5):
            all_pages.append(
                make_page(
                    doc_id=doc_id,
                    filename=f"{doc_id}.pdf",
                    page_number=p + 1,
                    text=f"Legal dispute fact sentence for {doc_id} on page {p + 1}.",
                )
            )
    ev = build_evidence(all_pages)

    # Budget of 120 tokens total (cannot fit 3 sentences * 5 docs = 15 sentences (~240 tokens))
    # It must scale down deterministically and preserve at least 1 sentence per document
    reduced = build_reduced_corpus(all_pages, ev, max_tokens_total=120)
    total_tokens = sum(count_tokens(p.text) for p in reduced)
    assert total_tokens <= 120

    doc_ids_in_reduced = {p.document_id for p in reduced}
    assert len(doc_ids_in_reduced) == 5  # All 5 documents preserved
