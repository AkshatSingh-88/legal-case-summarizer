import time
import pytest
from unittest.mock import patch

from backend.app.chunking import build_chunks, count_tokens, is_heading
from backend.app.chunking.chunk import Chunk
from backend.app.ingestion.models import IngestedPage
from backend.app.ingestion.quality import analyze_quality
from backend.app.nlp.evidence import Evidence
import uuid


def make_page(doc_id="doc-1", filename="file.pdf", page_number=1, text="Hello world.", ocr_used=False):
    q = analyze_quality(text)
    return IngestedPage(
        document_id=doc_id,
        filename=filename,
        page_number=page_number,
        text=text,
        char_count=q.char_count,
        word_count=q.word_count,
        is_empty=q.is_empty,
        ocr_used=ocr_used,
        error=None,
        ocr_error=None,
    )


def make_evidence(doc_id, filename, page_number, text, ev_type="important_sentence", score=0.9):
    return Evidence(
        id=str(uuid.uuid4()),
        type=ev_type,
        text=text,
        score=score,
        document_id=doc_id,
        filename=filename,
        page_number=page_number,
        meta={},
    )


LONG_PARA = (
    "The Supreme Court examined breach of contract under Section 13 of SARFAESI Act and Article 21 "
    "with detailed reasoning on compensation and relief. " * 8
)
SHORT_PARA = "Short page content."
HEADING_TEXT = "Facts:"
NUMBERED_HEADING = "1. Introduction"


def test_normal_3_page_document():
    pages = [
        make_page(page_number=1, text="Facts of the case involve breach.\n\nThe petitioner alleged failure to deliver goods."),
        make_page(page_number=2, text="Issues framed include whether Section 302 IPC applies.\n\nThe court examined precedent."),
        make_page(page_number=3, text="Decision: Civil Appeal No. 123/2024 allowed with costs and final order pronounced."),
    ]
    chunks = build_chunks(pages, [])
    assert len(chunks) >= 1
    # All pages represented
    all_pages = set()
    for c in chunks:
        all_pages.update(c.pages)
    assert all_pages == {1, 2, 3}
    for c in chunks:
        assert c.document_id == "doc-1"
        assert c.page_start <= c.page_end
        assert c.pages == sorted(c.pages)


def test_empty_pages():
    pages = [
        make_page(page_number=1, text=""),
        make_page(page_number=2, text="   "),
        make_page(page_number=3, text=LONG_PARA),
    ]
    # Ensure empty pages have is_empty True
    assert pages[0].is_empty is True
    chunks = build_chunks(pages, [])
    # Empty pages should be skipped, only page 3 represented
    all_pages = set()
    for c in chunks:
        all_pages.update(c.pages)
    assert 1 not in all_pages
    assert 2 not in all_pages
    assert 3 in all_pages


def test_short_pages_merging():
    pages = [
        make_page(page_number=1, text=SHORT_PARA),
        make_page(page_number=2, text=SHORT_PARA),
        make_page(page_number=3, text=SHORT_PARA),
    ]
    chunks = build_chunks(pages, [])
    # Short pages should merge into 1 chunk (since each ~3 tokens, total <400 min? Actually 3 pages * ~3 tokens = 9 <400, so will be single chunk)
    # Our min_tokens handling merges tiny tail, so expect 1 chunk spanning 1-3
    assert len(chunks) == 1
    assert chunks[0].pages == [1, 2, 3]


def test_long_paragraph_splitting():
    # Paragraph >1500 tokens
    long_para = ("Sentence one about legal reasoning. " * 600)  # ~1800 tokens
    pages = [make_page(page_number=1, text=long_para)]
    chunks = build_chunks(pages, [])
    assert len(chunks) >= 2
    for c in chunks:
        assert c.token_count <= 1500 * 1.1  # allow slack? But no strong evidence, so <=1500
        assert c.token_count <= 1650
    # Text preserved via chunks concatenation contains original sentences
    combined = "\n\n".join(c.text for c in chunks)
    assert "Sentence one" in combined


def test_long_sentence_char_fallback():
    # Single sentence without punctuation splits? Use a long string without sentence boundaries
    long_sentence = "A" * 8000  # chars/4 =2000 tokens >1500, no sentence split
    pages = [make_page(page_number=1, text=long_sentence)]
    chunks = build_chunks(pages, [])
    assert len(chunks) >= 2
    for c in chunks:
        assert c.token_count <= 1650
        assert c.meta.get("was_truncated") is True or c.token_count > 0
    # All text preserved
    combined = "".join(c.text for c in chunks).replace("\n\n", "")
    assert len(combined.replace(" ", "")) >= 7000


def test_heading_boundary():
    pages = [
        make_page(page_number=1, text="Facts:\n\nThe facts involve breach of contract and delivery failure."),
        make_page(page_number=1, text="Issues:\n\nWhether Section 302 IPC applies and whether compensation is due."),
    ]
    # Actually two paragraphs on same page: first heading Facts:, second Issues:
    # Instead create single page with both headings as paragraphs
    pages = [make_page(page_number=1, text="Facts:\n\nThe facts involve breach.\n\nIssues:\n\nWhether Section 302 applies.")]
    chunks = build_chunks(pages, [])
    # Heading should flush, expect at least 2 chunks (Facts and Issues separate)
    assert len(chunks) >= 2
    assert any(c.section == "Facts:" for c in chunks)
    assert any(c.section == "Issues:" for c in chunks)
    # Direct helper
    assert is_heading("Facts:") is True
    assert is_heading(NUMBERED_HEADING) is True
    assert is_heading("Normal sentence about facts.") is False


def test_numbered_legal_provision_preserved():
    pages = [make_page(page_number=1, text="Section 13 of the SARFAESI Act is relevant to the case and was extensively argued.")]
    ev = [make_evidence("doc-1", "file.pdf", 1, "Section 13 of the SARFAESI Act", "legal_provision", 0.9)]
    chunks = build_chunks(pages, ev)
    assert len(chunks) == 1
    assert "Section 13" in chunks[0].text
    assert chunks[0].evidence_ids == [ev[0].id]


def test_token_count_limits():
    pages = [make_page(page_number=i+1, text=LONG_PARA) for i in range(5)]
    chunks = build_chunks(pages, [])
    for c in chunks:
        assert c.token_count <= 1650  # max 1500 *1.1 slack
        assert c.token_count > 0


def test_adaptive_varying_chunk_sizes():
    # Doc A: short paragraphs (100 tokens each), Doc B: long paragraphs (800 tokens)
    short = "Short paragraph content. " * 20  # ~ 80 tokens
    long = LONG_PARA  # ~ 400 tokens approx
    pages_short = [make_page(doc_id="short", filename="s.pdf", page_number=1, text="\n\n".join([short]*5))]
    pages_long = [make_page(doc_id="long", filename="l.pdf", page_number=1, text="\n\n".join([long]*5))]
    chunks_short = build_chunks(pages_short, [])
    chunks_long = build_chunks(pages_long, [])
    # Adaptive: short doc should have fewer tokens per chunk variance vs long doc?
    # At least both produce chunks and not fixed size
    assert len(chunks_short) >= 1
    assert len(chunks_long) >= 1
    # Token counts should vary (not all equal)
    # For short doc with 5*80=400 tokens, should be 1-2 chunks
    # For long doc 5*400=2000 tokens, should be 2+ chunks, avg tokens larger
    avg_short = sum(c.token_count for c in chunks_short) / len(chunks_short)
    avg_long = sum(c.token_count for c in chunks_long) / len(chunks_long)
    assert avg_long > avg_short


def test_evidence_attachment():
    pages = [
        make_page(page_number=1, text="The court heard Civil Appeal No. 123/2024 on 12 March 2024."),
        make_page(page_number=2, text="Section 302 IPC was discussed in detail with reasoning."),
    ]
    ev = [
        make_evidence("doc-1", "file.pdf", 1, "Civil Appeal No. 123/2024", "case_number", 0.95),
        make_evidence("doc-1", "file.pdf", 1, "12 March 2024", "date", 0.9),
        make_evidence("doc-1", "file.pdf", 2, "Section 302 IPC", "legal_provision", 0.9),
    ]
    chunks = build_chunks(pages, ev)
    # Find chunk containing page 1
    c1 = [c for c in chunks if 1 in c.pages][0]
    assert any(eid in c1.evidence_ids for eid in [ev[0].id, ev[1].id])
    c2 = [c for c in chunks if 2 in c.pages][0]
    assert ev[2].id in c2.evidence_ids


def test_evidence_score_mean_top3():
    pages = [make_page(page_number=1, text="Sentence one. Sentence two. Sentence three. Sentence four.")]
    ev = [
        make_evidence("doc-1", "file.pdf", 1, "Sentence one.", "important_sentence", 0.9),
        make_evidence("doc-1", "file.pdf", 1, "Sentence two.", "important_sentence", 0.7),
        make_evidence("doc-1", "file.pdf", 1, "Sentence three.", "important_sentence", 0.5),
        make_evidence("doc-1", "file.pdf", 1, "Sentence four.", "important_sentence", 0.3),
    ]
    # All sentences in one chunk (since short)
    chunks = build_chunks(pages, ev)
    assert len(chunks) == 1
    # Score should be mean of top 3: (0.9+0.7+0.5)/3 =0.7
    assert abs(chunks[0].evidence_score - 0.7) < 1e-6
    assert chunks[0].evidence_count == 4


def test_page_provenance():
    pages = [make_page(page_number=i, text=f"Content for page {i} with sufficient legal text to exceed minimal token threshold for testing provenance. " * 3) for i in [1, 3, 5]]
    chunks = build_chunks(pages, [])
    all_pages = set()
    for c in chunks:
        assert c.page_start == min(c.pages)
        assert c.page_end == max(c.pages)
        all_pages.update(c.pages)
    assert all_pages == {1, 3, 5}


def test_multi_document_isolation():
    pages = [
        make_page(doc_id="doc-A", filename="a.pdf", page_number=1, text=LONG_PARA),
        make_page(doc_id="doc-A", filename="a.pdf", page_number=2, text=LONG_PARA),
        make_page(doc_id="doc-B", filename="b.pdf", page_number=1, text=LONG_PARA),
    ]
    chunks = build_chunks(pages, [])
    for c in chunks:
        assert c.document_id in ("doc-A", "doc-B")
        if c.document_id == "doc-A":
            assert c.filename == "a.pdf"
            assert all(p in [1, 2] for p in c.pages)
        else:
            assert c.filename == "b.pdf"
            assert c.pages == [1]


def test_chunk_index_resets_per_document():
    pages = [
        make_page(doc_id="doc-A", filename="a.pdf", page_number=1, text=LONG_PARA),
        make_page(doc_id="doc-A", filename="a.pdf", page_number=2, text=LONG_PARA),
        make_page(doc_id="doc-B", filename="b.pdf", page_number=1, text=LONG_PARA),
        make_page(doc_id="doc-B", filename="b.pdf", page_number=2, text=LONG_PARA),
        make_page(doc_id="doc-B", filename="b.pdf", page_number=3, text=LONG_PARA),
    ]
    chunks = build_chunks(pages, [])
    doc_a = [c for c in chunks if c.document_id == "doc-A"]
    doc_b = [c for c in chunks if c.document_id == "doc-B"]
    assert [c.chunk_index for c in doc_a] == list(range(len(doc_a)))
    assert [c.chunk_index for c in doc_b] == list(range(len(doc_b)))
    assert doc_a[0].chunk_index == 0
    assert doc_b[0].chunk_index == 0


def test_ocr_text_treated_normally():
    pages = [
        make_page(page_number=1, text=LONG_PARA, ocr_used=False),
        make_page(page_number=2, text=LONG_PARA, ocr_used=True),
    ]
    chunks = build_chunks(pages, [])
    # Both pages should be represented regardless of ocr_used
    all_pages = set()
    for c in chunks:
        all_pages.update(c.pages)
    assert all_pages == {1, 2}


def test_no_pdf_reopening():
    pages = [make_page(text=LONG_PARA)]
    with patch("pymupdf.open") as mock_open:
        build_chunks(pages, [])
        mock_open.assert_not_called()


def test_large_synthetic_performance():
    # 500 pages, each with 2 paragraphs ~ 400 tokens each => ~200k tokens total
    pages = []
    for i in range(500):
        text = f"Paragraph one for page {i} with legal content about Section 302 IPC and Article 21. " * 10 + "\n\n" + f"Paragraph two for page {i} with additional reasoning and precedent discussion. " * 10
        pages.append(make_page(page_number=i+1, text=text))
    start = time.time()
    chunks = build_chunks(pages, [])
    elapsed = time.time() - start
    assert len(chunks) >= 100
    assert elapsed < 5.0  # linear, no dense matrix
    # Every page represented
    all_pages = set()
    for c in chunks:
        all_pages.update(c.pages)
    assert len(all_pages) == 500
    for c in chunks:
        assert c.token_count <= 1650


def test_count_tokens_heuristic():
    assert count_tokens("") == 0
    assert count_tokens("   ") == 0
    assert count_tokens("hello world") == max(int(2*1.3), int(11/4))  # 2 words
    # Long chars
    text = "a" * 100
    assert count_tokens(text) == max(int(1*1.3), int(100/4))


def test_evidence_aware_slack():
    # Paragraph with strong provision should get 10% slack
    # Create a paragraph exactly at limit + prov, ensure it doesn't split unnecessarily
    # Use 1500 tokens max, create chunk near limit then add provision para that would exceed by <10%
    base = "Base content sentence. " * 300  # ~ 900 tokens
    provision_para = "Section 13 of the SARFAESI Act is central to this issue and must be preserved intact with detailed analysis of the provision and its applicability to the present facts and circumstances."
    # Make provision Para tokens ~ 30, base chunk ~ 900, combined ~930 <1500 so no slack needed — test still checks not split
    pages = [make_page(page_number=1, text=base + "\n\n" + provision_para)]
    ev = [make_evidence("doc-1", "file.pdf", 1, "Section 13 of the SARFAESI Act", "legal_provision", 0.9)]
    chunks = build_chunks(pages, ev)
    # Provision should be in same chunk as base if fits
    assert any("Section 13" in c.text for c in chunks)
    # If we craft a case where adding provision would exceed by <10%, it should stay together
    # Create near-limit chunk: 1450 tokens base
    near_limit = "Word " * 1100  # ~1430 tokens
    pages2 = [make_page(page_number=1, text=near_limit + "\n\n" + provision_para)]
    ev2 = [make_evidence("doc-1", "file.pdf", 1, "Section 13 of the SARFAESI Act", "legal_provision", 0.9)]
    chunks2 = build_chunks(pages2, ev2)
    # With 10% slack (1650), 1430+30=1460 should stay together in one chunk, not split
    assert len(chunks2) == 1

