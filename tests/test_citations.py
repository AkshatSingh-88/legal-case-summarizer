"""Phase 11 Test Suite — Citation & Evidence Linking."""

from unittest.mock import MagicMock, patch

import pytest

from backend.app.case.analyze import analyze_case
from backend.app.case.models import CaseAnalysis, CaseRelationship, CaseTimelineEvent
from backend.app.chunking.chunk import Chunk, build_chunks
from backend.app.file.analyze import analyze_file
from backend.app.file.models import AnalysisItem, FileAnalysis
from backend.app.ingestion.models import IngestedPage
from backend.app.llm.analyze import analyze_chunks
from backend.app.llm.models import ChunkAnalysis
from backend.app.nlp.corpus import build_reduced_corpus
from backend.app.nlp.evidence import build_evidence
from backend.app.presentation.builder import (
    build_detailed_analysis,
    build_presentation,
    build_quick_summary,
)
from backend.app.presentation.citations import (
    CitedAnalysisItem,
    CitedRelationship,
    CitedTimelineEvent,
    ResolvedCitation,
    cite_items,
    cite_relationships,
    cite_timeline,
    resolve_ref,
    resolve_refs,
)
from backend.app.presentation.models import (
    DetailedAnalysis,
    ProgressivePresentation,
    QuickSummary,
    SummarySection,
)


@pytest.fixture
def sample_doc_registry():
    return {
        "DOC-001": {
            "document_id": "doc-petition-123",
            "filename": "petition.pdf",
            "src_registry": {
                "SRC-001": {
                    "chunk_id": "chunk-1",
                    "document_id": "doc-petition-123",
                    "filename": "petition.pdf",
                    "page_start": 1,
                    "page_end": 2,
                    "pages": [1, 2],
                },
                "SRC-002": {
                    "chunk_id": "chunk-2",
                    "document_id": "doc-petition-123",
                    "filename": "petition.pdf",
                    "page_start": 3,
                    "page_end": 5,
                    "pages": [3, 4, 5],
                },
            },
        },
        "DOC-002": {
            "document_id": "doc-reply-456",
            "filename": "reply.pdf",
            "src_registry": {
                "SRC-001": {
                    "chunk_id": "chunk-3",
                    "document_id": "doc-reply-456",
                    "filename": "reply.pdf",
                    "page_start": 1,
                    "page_end": 1,
                    "pages": [1],
                }
            },
        },
    }


# ==========================================
# 1. RESOLUTION TESTS
# ==========================================


def test_resolve_valid_compound_ref(sample_doc_registry):
    citation = resolve_ref("DOC-001:SRC-002", sample_doc_registry)
    assert citation is not None
    assert isinstance(citation, ResolvedCitation)
    assert citation.source_ref == "DOC-001:SRC-002"
    assert citation.doc_label == "DOC-001"
    assert citation.document_id == "doc-petition-123"
    assert citation.filename == "petition.pdf"
    assert citation.page_start == 3
    assert citation.page_end == 5
    assert citation.pages == [3, 4, 5]


def test_resolve_invalid_doc_label(sample_doc_registry):
    assert resolve_ref("DOC-999:SRC-001", sample_doc_registry) is None


def test_resolve_invalid_src_id(sample_doc_registry):
    assert resolve_ref("DOC-001:SRC-999", sample_doc_registry) is None


def test_resolve_malformed_refs(sample_doc_registry):
    assert resolve_ref("SRC-001", sample_doc_registry) is None
    assert resolve_ref("", sample_doc_registry) is None
    assert resolve_ref("   ", sample_doc_registry) is None
    assert resolve_ref(None, sample_doc_registry) is None


def test_resolve_empty_or_none_registry():
    assert resolve_ref("DOC-001:SRC-001", {}) is None
    assert resolve_ref("DOC-001:SRC-001", None) is None


def test_resolve_refs_deduplicates_and_preserves_order(sample_doc_registry):
    refs = ["DOC-001:SRC-002", "DOC-002:SRC-001", "DOC-001:SRC-002", "DOC-999:INVALID"]
    citations = resolve_refs(refs, sample_doc_registry)
    assert len(citations) == 2
    assert citations[0].source_ref == "DOC-001:SRC-002"
    assert citations[0].filename == "petition.pdf"
    assert citations[1].source_ref == "DOC-002:SRC-001"
    assert citations[1].filename == "reply.pdf"


def test_resolve_refs_empty_or_none(sample_doc_registry):
    assert resolve_refs([], sample_doc_registry) == []
    assert resolve_refs(None, sample_doc_registry) == []


# ==========================================
# 2. PRESENTATION HELPER TESTS
# ==========================================


def test_cite_items(sample_doc_registry):
    raw_items = [
        AnalysisItem(text="Fact 1", source_refs=["DOC-001:SRC-001"]),
        AnalysisItem(text="Fact 2 (no refs)", source_refs=[]),
        {"text": "Fact 3 (dict)", "source_refs": ["DOC-002:SRC-001"]},
        "Fact 4 (string)",
    ]
    cited = cite_items(raw_items, sample_doc_registry)
    assert cited is not None
    assert len(cited) == 4
    assert isinstance(cited[0], CitedAnalysisItem)
    assert cited[0].text == "Fact 1"
    assert cited[0].source_refs == ["DOC-001:SRC-001"]
    assert len(cited[0].citations) == 1
    assert cited[0].citations[0].filename == "petition.pdf"
    assert cited[0].citations[0].pages == [1, 2]

    assert cited[1].text == "Fact 2 (no refs)"
    assert cited[1].citations == []

    assert cited[2].text == "Fact 3 (dict)"
    assert len(cited[2].citations) == 1
    assert cited[2].citations[0].filename == "reply.pdf"

    assert cited[3].text == "Fact 4 (string)"
    assert cited[3].citations == []


def test_cite_items_none():
    assert cite_items(None, {}) is None
    assert cite_items([], {}) == []


def test_cite_timeline(sample_doc_registry):
    raw_events = [
        CaseTimelineEvent(
            event_id="EVT-001",
            date_raw="12 March 2021",
            event="Petition filed in Court",
            document_ids=["doc-petition-123"],
            source_refs=["DOC-001:SRC-001"],
            is_disputed=False,
        ),
        CaseTimelineEvent(
            event_id="EVT-002",
            date_raw="15 April 2021",
            event="Reply filed by respondent",
            document_ids=["doc-reply-456"],
            source_refs=["DOC-002:SRC-001"],
            is_disputed=True,
            conflict_details="Date disputed",
        ),
    ]
    cited_events = cite_timeline(raw_events, sample_doc_registry)
    assert cited_events is not None
    assert len(cited_events) == 2
    assert isinstance(cited_events[0], CitedTimelineEvent)
    assert cited_events[0].event_id == "EVT-001"
    assert cited_events[0].event == "Petition filed in Court"
    assert cited_events[0].source_refs == ["DOC-001:SRC-001"]
    assert len(cited_events[0].citations) == 1
    assert cited_events[0].citations[0].filename == "petition.pdf"

    assert cited_events[1].event_id == "EVT-002"
    assert cited_events[1].is_disputed is True
    assert cited_events[1].conflict_details == "Date disputed"
    assert len(cited_events[1].citations) == 1
    assert cited_events[1].citations[0].filename == "reply.pdf"


def test_cite_relationships(sample_doc_registry):
    raw_rels = [
        CaseRelationship(
            relationship_id="REL-001",
            relationship_type="claim_defense",
            source_document_id="doc-petition-123",
            source_item="Claim of Rs 50L breach",
            target_document_id="doc-reply-456",
            target_item="Denial of liability",
            status="disputed",
            source_refs=["DOC-001:SRC-002", "DOC-002:SRC-001"],
            notes="Active dispute",
        )
    ]
    cited_rels = cite_relationships(raw_rels, sample_doc_registry)
    assert cited_rels is not None
    assert len(cited_rels) == 1
    assert isinstance(cited_rels[0], CitedRelationship)
    assert cited_rels[0].relationship_id == "REL-001"
    assert cited_rels[0].source_refs == ["DOC-001:SRC-002", "DOC-002:SRC-001"]
    assert len(cited_rels[0].citations) == 2
    assert cited_rels[0].citations[0].filename == "petition.pdf"
    assert cited_rels[0].citations[1].filename == "reply.pdf"


# ==========================================
# 3. REGISTRY INTEGRATION TESTS (FILE & CASE)
# ==========================================


def test_file_analysis_persists_src_registry_direct():
    chunks = [
        Chunk(
            chunk_id="c-1",
            document_id="doc-1",
            filename="sample.pdf",
            chunk_index=0,
            page_start=1,
            page_end=2,
            pages=[1, 2],
            text="Chunk 1 text on breach of contract.",
            token_count=10,
            evidence_ids=[],
            evidence_score=0.0,
            evidence_count=0,
            section=None,
        ),
        Chunk(
            chunk_id="c-2",
            document_id="doc-1",
            filename="sample.pdf",
            chunk_index=1,
            page_start=3,
            page_end=4,
            pages=[3, 4],
            text="Chunk 2 text on damages claim.",
            token_count=10,
            evidence_ids=[],
            evidence_score=0.0,
            evidence_count=0,
            section=None,
        ),
    ]
    analyses = [
        ChunkAnalysis(
            chunk_id="c-1",
            document_id="doc-1",
            filename="sample.pdf",
            page_start=1,
            page_end=2,
            pages=[1, 2],
            facts=["Fact from chunk 1"],
            model="fake",
            provider="fake",
        ),
        ChunkAnalysis(
            chunk_id="c-2",
            document_id="doc-1",
            filename="sample.pdf",
            page_start=3,
            page_end=4,
            pages=[3, 4],
            facts=["Fact from chunk 2"],
            model="fake",
            provider="fake",
        ),
    ]

    with patch(
        "backend.app.file.analyze.get_llm_provider",
        return_value=lambda prompts: [{"facts": [{"text": "Fact from chunk 1", "source_refs": ["SRC-001"]}]}],
    ):
        fa = analyze_file("doc-1", chunks, analyses)

    assert fa.status == "complete"
    assert "src_registry" in fa.meta
    reg = fa.meta["src_registry"]
    assert "SRC-001" in reg
    assert "SRC-002" in reg
    assert reg["SRC-001"]["filename"] == "sample.pdf"
    assert reg["SRC-001"]["page_start"] == 1
    assert reg["SRC-001"]["page_end"] == 2
    assert reg["SRC-001"]["pages"] == [1, 2]
    assert reg["SRC-002"]["page_start"] == 3
    assert reg["SRC-002"]["pages"] == [3, 4]


def test_case_analysis_persists_doc_registry():
    fa1 = FileAnalysis(
        document_id="doc-pet-1",
        filename="petition.pdf",
        chunk_ids=["c-1"],
        chunk_count=1,
        pages=[1, 2],
        page_start=1,
        page_end=2,
        analyzed_chunk_ids=["c-1"],
        failed_chunk_ids=[],
        coverage=1.0,
        status="complete",
        document_type="petition",
        facts=[AnalysisItem(text="Petitioner claims breach", source_refs=["SRC-001"])],
        meta={
            "src_registry": {
                "SRC-001": {
                    "chunk_id": "c-1",
                    "document_id": "doc-pet-1",
                    "filename": "petition.pdf",
                    "page_start": 1,
                    "page_end": 2,
                    "pages": [1, 2],
                }
            }
        },
        model="fake",
        provider="fake",
    )
    fa2 = FileAnalysis(
        document_id="doc-rep-2",
        filename="reply.pdf",
        chunk_ids=["c-2"],
        chunk_count=1,
        pages=[1],
        page_start=1,
        page_end=1,
        analyzed_chunk_ids=["c-2"],
        failed_chunk_ids=[],
        coverage=1.0,
        status="complete",
        document_type="reply",
        facts=[AnalysisItem(text="Respondent denies liability", source_refs=["SRC-001"])],
        meta={
            "src_registry": {
                "SRC-001": {
                    "chunk_id": "c-2",
                    "document_id": "doc-rep-2",
                    "filename": "reply.pdf",
                    "page_start": 1,
                    "page_end": 1,
                    "pages": [1],
                }
            }
        },
        model="fake",
        provider="fake",
    )

    with patch(
        "backend.app.case.analyze.get_llm_provider",
        return_value=lambda prompts: [
            {
                "case_summary": "Dispute between parties",
                "overall_facts": [{"text": "Petitioner claims breach", "source_refs": ["DOC-001:SRC-001"]}],
            }
        ],
    ):
        ca = analyze_case("case-100", [fa1, fa2])

    assert ca.status == "complete"
    assert "doc_registry" in ca.meta
    doc_reg = ca.meta["doc_registry"]
    assert "DOC-001" in doc_reg
    assert "DOC-002" in doc_reg
    assert doc_reg["DOC-001"]["filename"] == "petition.pdf"
    assert doc_reg["DOC-002"]["filename"] == "reply.pdf"
    assert "SRC-001" in doc_reg["DOC-001"]["src_registry"]


# ==========================================
# 4. PRESENTATION LAYER CITATION TESTS
# ==========================================


def test_quick_summary_citations(sample_doc_registry):
    ca = CaseAnalysis(
        case_id="case-100",
        document_ids=["doc-petition-123", "doc-reply-456"],
        document_count=2,
        documents=[],
        analyzed_document_ids=["doc-petition-123", "doc-reply-456"],
        failed_document_ids=[],
        case_coverage=1.0,
        status="complete",
        case_summary="Contract dispute overview",
        parties=["Petitioner: Alpha", "Respondent: Beta"],
        overall_facts=[
            AnalysisItem(text="Contract was executed on 12 Jan 2020", source_refs=["DOC-001:SRC-001"]),
            AnalysisItem(text="Goods delivered in partial condition", source_refs=["DOC-001:SRC-002"]),
        ],
        issues=[AnalysisItem(text="Whether breach was material", source_refs=["DOC-002:SRC-001"])],
        claims_and_defenses=[
            CaseRelationship(
                relationship_id="REL-001",
                relationship_type="claim_defense",
                source_document_id="doc-petition-123",
                source_item="Claim of damages",
                target_document_id="doc-reply-456",
                target_item="Counterclaim",
                status="disputed",
                source_refs=["DOC-001:SRC-002"],
            )
        ],
        final_disposition="Petition allowed in part",
        confidence=1.0,
        uncertainty=None,
        meta={"doc_registry": sample_doc_registry},
        model="fake",
        provider="fake",
    )

    qs = build_quick_summary(ca)
    assert qs is not None
    assert isinstance(qs, QuickSummary)

    # Key facts are CitedAnalysisItem with resolved citations
    assert qs.key_facts is not None
    assert len(qs.key_facts) == 2
    assert isinstance(qs.key_facts[0], CitedAnalysisItem)
    assert qs.key_facts[0].source_refs == ["DOC-001:SRC-001"]
    assert len(qs.key_facts[0].citations) == 1
    assert qs.key_facts[0].citations[0].filename == "petition.pdf"
    assert qs.key_facts[0].citations[0].pages == [1, 2]

    assert qs.key_facts[1].citations[0].pages == [3, 4, 5]

    # Core issues
    assert qs.core_issues is not None
    assert len(qs.core_issues[0].citations) == 1
    assert qs.core_issues[0].citations[0].filename == "reply.pdf"

    # Key arguments
    assert qs.key_arguments is not None
    assert len(qs.key_arguments[0].citations) == 1
    assert qs.key_arguments[0].citations[0].filename == "petition.pdf"

    # Overview and parties are not mechanically cited
    assert qs.case_overview == "Contract dispute overview"
    assert qs.parties == ["Petitioner: Alpha", "Respondent: Beta"]


def test_detailed_analysis_citations(sample_doc_registry):
    ca = CaseAnalysis(
        case_id="case-100",
        document_ids=["doc-petition-123", "doc-reply-456"],
        document_count=2,
        documents=[],
        analyzed_document_ids=["doc-petition-123", "doc-reply-456"],
        failed_document_ids=[],
        case_coverage=1.0,
        status="complete",
        case_summary="Detailed dispute analysis",
        parties=["Petitioner: Alpha", "Respondent: Beta"],
        procedural_history=[AnalysisItem(text="Notice issued 10 Feb 2021", source_refs=["DOC-001:SRC-001"])],
        timeline=[
            CaseTimelineEvent(
                event_id="EVT-001",
                date_raw="10 Feb 2021",
                event="Notice issued",
                document_ids=["doc-petition-123"],
                source_refs=["DOC-001:SRC-001"],
            )
        ],
        overall_facts=[AnalysisItem(text="Contract signed", source_refs=["DOC-001:SRC-001"])],
        undisputed_facts=[AnalysisItem(text="Agreement existence admitted", source_refs=["DOC-002:SRC-001"])],
        issues=[AnalysisItem(text="Whether breach occurred", source_refs=["DOC-001:SRC-002"])],
        claims_and_defenses=[
            CaseRelationship(
                relationship_id="REL-001",
                relationship_type="claim_defense",
                source_document_id="doc-petition-123",
                source_item="Claim of breach",
                target_document_id="doc-reply-456",
                target_item="Denial",
                status="disputed",
                source_refs=["DOC-001:SRC-002"],
            )
        ],
        disputed_matters=[AnalysisItem(text="Quantum of loss", source_refs=["DOC-001:SRC-002"])],
        evidence_summary=[AnalysisItem(text="Bank statements Exhibit P-1", source_refs=["DOC-001:SRC-001"])],
        legal_provisions=[AnalysisItem(text="Section 73 Contract Act", source_refs=["DOC-001:SRC-001"])],
        court_reasoning=[AnalysisItem(text="Loss is proven by records", source_refs=["DOC-001:SRC-002"])],
        findings=[AnalysisItem(text="Breach established", source_refs=["DOC-001:SRC-002"])],
        decisions=[AnalysisItem(text="Suit decreed for Rs 50L", source_refs=["DOC-001:SRC-002"])],
        final_disposition="Decreed with Costs",
        confidence=1.0,
        uncertainty=None,
        meta={"doc_registry": sample_doc_registry},
        model="fake",
        provider="fake",
    )

    da = build_detailed_analysis(ca)
    assert da is not None
    assert isinstance(da, DetailedAnalysis)

    sec_map = {s.section_id: s for s in da.sections}

    # sec_overview: no items, no citations
    assert sec_map["sec_overview"].items is None
    assert sec_map["sec_overview"].source_refs == []

    # sec_procedural
    assert sec_map["sec_procedural"].items is not None
    assert isinstance(sec_map["sec_procedural"].items[0], CitedAnalysisItem)
    assert len(sec_map["sec_procedural"].items[0].citations) == 1
    assert sec_map["sec_procedural"].items[0].citations[0].filename == "petition.pdf"

    # sec_timeline
    assert sec_map["sec_timeline"].timeline_events is not None
    assert isinstance(sec_map["sec_timeline"].timeline_events[0], CitedTimelineEvent)
    assert len(sec_map["sec_timeline"].timeline_events[0].citations) == 1

    # sec_facts
    assert sec_map["sec_facts"].items is not None
    assert len(sec_map["sec_facts"].items[0].citations) == 1

    # sec_undisputed
    assert sec_map["sec_undisputed"].items is not None
    assert len(sec_map["sec_undisputed"].items[0].citations) == 1
    assert sec_map["sec_undisputed"].items[0].citations[0].filename == "reply.pdf"

    # sec_claims
    assert sec_map["sec_claims"].relationships is not None
    assert isinstance(sec_map["sec_claims"].relationships[0], CitedRelationship)
    assert len(sec_map["sec_claims"].relationships[0].citations) == 1

    # sec_decisions (includes final disposition item with no citation + decision item with citation)
    dec_items = sec_map["sec_decisions"].items
    assert dec_items is not None
    assert len(dec_items) == 2
    assert "Final Disposition:" in dec_items[0].text
    assert dec_items[0].citations == []  # Synthesized disposition has no citations
    assert dec_items[1].text == "Suit decreed for Rs 50L"
    assert len(dec_items[1].citations) == 1


# ==========================================
# 5. HIERARCHICAL CASE CITATION RESOLUTION
# ==========================================


def test_hierarchical_case_analysis_canonical_resolution():
    # Setup 4 documents that force hierarchical batching when max_files=2
    fas = []
    for idx in range(1, 5):
        doc_id = f"doc-{idx}"
        filename = f"file_{idx}.pdf"
        src_reg = {
            "SRC-001": {
                "chunk_id": f"chunk-{idx}-1",
                "document_id": doc_id,
                "filename": filename,
                "page_start": idx,
                "page_end": idx + 1,
                "pages": [idx, idx + 1],
            }
        }
        fa = FileAnalysis(
            document_id=doc_id,
            filename=filename,
            chunk_ids=[f"chunk-{idx}-1"],
            chunk_count=1,
            pages=[idx, idx + 1],
            page_start=idx,
            page_end=idx + 1,
            analyzed_chunk_ids=[f"chunk-{idx}-1"],
            failed_chunk_ids=[],
            coverage=1.0,
            status="complete",
            document_type="petition" if idx == 1 else "evidence",
            facts=[AnalysisItem(text=f"Fact from doc {idx}", source_refs=["SRC-001"])],
            meta={"src_registry": src_reg},
            model="fake",
            provider="fake",
        )
        fas.append(fa)

    # Patch settings to force hierarchical partitioning with max_files=2
    with patch("backend.app.case.analyze.get_settings") as mock_settings:
        s = MagicMock()
        s.llm_model = "fake"
        s.llm_provider = "fake"
        s.case_max_files_per_prompt = 2
        s.case_max_tokens = 50000
        mock_settings.return_value = s

        call_count = {"n": 0}

        def fake_provider(prompts):
            call_count["n"] += 1
            # Return canonical reference DOC-001:SRC-001 or DOC-003:SRC-001
            return [
                {
                    "case_summary": f"Hierarchical synthesis call {call_count['n']}",
                    "overall_facts": [
                        {"text": "Fact from doc 1", "source_refs": ["DOC-001:SRC-001"]},
                        {"text": "Fact from doc 3", "source_refs": ["DOC-003:SRC-001"]},
                    ],
                }
            ]

        with patch("backend.app.case.analyze.get_llm_provider", return_value=fake_provider):
            ca = analyze_case("case-hierarchical-100", fas)

    assert ca.status == "complete"
    assert "doc_registry" in ca.meta
    doc_reg = ca.meta["doc_registry"]
    assert "DOC-001" in doc_reg
    assert "DOC-003" in doc_reg

    # Build presentation and verify citations resolve canonically
    da = build_detailed_analysis(ca)
    assert da is not None
    sec_facts = next(s for s in da.sections if s.section_id == "sec_facts")
    assert sec_facts.items is not None
    assert len(sec_facts.items) == 2

    # Doc 1 citation
    assert len(sec_facts.items[0].citations) == 1
    assert sec_facts.items[0].citations[0].filename == "file_1.pdf"
    assert sec_facts.items[0].citations[0].pages == [1, 2]

    # Doc 3 citation
    assert len(sec_facts.items[1].citations) == 1
    assert sec_facts.items[1].citations[0].filename == "file_3.pdf"
    assert sec_facts.items[1].citations[0].pages == [3, 4]


# ==========================================
# 6. QUICK MODE VS DETAILED MODE PROVENANCE
# ==========================================


def test_quick_mode_citation_integrity_mocked():
    pages = [
        IngestedPage(
            document_id="doc-quick-1",
            filename="quick_petition.pdf",
            page_number=1,
            text="Writ Petition No 123/2024 filed before Supreme Court. Petitioner claims breach of contract.",
            char_count=90,
            word_count=13,
            is_empty=False,
            ocr_used=False,
        ),
        IngestedPage(
            document_id="doc-quick-1",
            filename="quick_petition.pdf",
            page_number=2,
            text="Section 73 Contract Act applies. Damages of Rs 50 Lakhs claimed.",
            char_count=65,
            word_count=10,
            is_empty=False,
            ocr_used=False,
        ),
    ]
    ev = build_evidence(pages)

    # 1. Quick reduced corpus
    reduced_pages = build_reduced_corpus(pages, ev)
    assert len(reduced_pages) >= 1

    # 2. Existing build_chunks on reduced corpus
    chunks = build_chunks(reduced_pages, ev)
    assert len(chunks) >= 1

    # 3. Analyze chunks
    with patch(
        "backend.app.llm.analyze.get_llm_provider",
        return_value=lambda prompts: [{"facts": ["Contract breach claimed"], "issues": ["Whether breach occurred"]}],
    ):
        chunk_analyses = analyze_chunks(chunks, ev)

    # 4. Analyze file (persists src_registry)
    with patch(
        "backend.app.file.analyze.get_llm_provider",
        return_value=lambda prompts: [{"facts": [{"text": "Contract breach claimed", "source_refs": ["SRC-001"]}]}],
    ):
        file_analysis = analyze_file("doc-quick-1", chunks, chunk_analyses)
        assert "src_registry" in file_analysis.meta
        assert "SRC-001" in file_analysis.meta["src_registry"]

    # 5. Analyze case (persists doc_registry)
    with patch(
        "backend.app.case.analyze.get_llm_provider",
        return_value=lambda prompts: [
            {
                "case_summary": "Dispute on contract breach",
                "overall_facts": [{"text": "Contract breach claimed", "source_refs": ["DOC-001:SRC-001"]}],
            }
        ],
    ):
        case_analysis = analyze_case("case-quick-1", [file_analysis])
        assert "doc_registry" in case_analysis.meta

    # 6. Quick summary presentation with citations
    quick_summary = build_quick_summary(case_analysis)
    assert quick_summary is not None
    assert quick_summary.analysis_mode == "quick"
    assert quick_summary.is_preliminary is True
    assert quick_summary.key_facts is not None
    assert len(quick_summary.key_facts[0].citations) == 1
    assert quick_summary.key_facts[0].citations[0].filename == "quick_petition.pdf"
    assert quick_summary.key_facts[0].citations[0].page_start in [1, 2]


# ==========================================
# 7. MULTI-LEVEL HIERARCHICAL REGRESSION TEST
# ==========================================


def test_multilevel_hierarchical_case_all_fields_resolve():
    """Verify that every source_ref across all fields in multi-level hierarchical case analysis resolves against doc_registry."""
    num_docs = 6
    fas = []
    for idx in range(1, num_docs + 1):
        doc_id = f"doc-multi-{idx}"
        filename = f"multi_file_{idx}.pdf"
        src_reg = {
            "SRC-001": {
                "chunk_id": f"chunk-{idx}-1",
                "document_id": doc_id,
                "filename": filename,
                "page_start": idx * 2 - 1,
                "page_end": idx * 2,
                "pages": [idx * 2 - 1, idx * 2],
            }
        }
        fa = FileAnalysis(
            document_id=doc_id,
            filename=filename,
            chunk_ids=[f"chunk-{idx}-1"],
            chunk_count=1,
            pages=[idx * 2 - 1, idx * 2],
            page_start=idx * 2 - 1,
            page_end=idx * 2,
            analyzed_chunk_ids=[f"chunk-{idx}-1"],
            failed_chunk_ids=[],
            coverage=1.0,
            status="complete",
            document_type="petition" if idx == 1 else "evidence",
            facts=[AnalysisItem(text=f"Fact from doc {idx}", source_refs=["SRC-001"])],
            important_dates=[AnalysisItem(text=f"202{idx}-01-01: Event {idx}", source_refs=["SRC-001"])],
            meta={"src_registry": src_reg},
            model="fake",
            provider="fake",
        )
        fas.append(fa)

    with patch("backend.app.case.analyze.get_settings") as mock_settings:
        s = MagicMock()
        s.llm_model = "fake"
        s.llm_provider = "fake"
        s.case_max_files_per_prompt = 2
        s.case_max_tokens = 50000
        mock_settings.return_value = s

        call_count = {"n": 0}

        def fake_provider(prompts):
            call_count["n"] += 1
            # Return payload containing references from across the documents
            return [
                {
                    "case_summary": f"Consolidated summary call {call_count['n']}",
                    "overall_facts": [
                        {"text": "Fact 1", "source_refs": ["DOC-001:SRC-001"]},
                        {"text": "Fact 2", "source_refs": ["DOC-002:SRC-001"]},
                    ],
                    "procedural_history": [
                        {"text": "Procedural step", "source_refs": ["DOC-003:SRC-001"]}
                    ],
                    "issues": [
                        {"text": "Issue 1", "source_refs": ["DOC-004:SRC-001"]}
                    ],
                    "claims_and_defenses": [
                        {
                            "relationship_id": "REL-001",
                            "relationship_type": "claim_defense",
                            "source_document_id": "doc-multi-1",
                            "source_item": "Claim",
                            "target_document_id": "doc-multi-2",
                            "target_item": "Defense",
                            "status": "disputed",
                            "source_refs": ["DOC-001:SRC-001", "DOC-002:SRC-001"],
                        }
                    ],
                    "disputed_matters": [
                        {"text": "Dispute on breach", "source_refs": ["DOC-005:SRC-001"]}
                    ],
                    "undisputed_facts": [
                        {"text": "Undisputed fact", "source_refs": ["DOC-006:SRC-001"]}
                    ],
                    "evidence_summary": [
                        {"text": "Evidence summary", "source_refs": ["DOC-001:SRC-001"]}
                    ],
                    "legal_provisions": [
                        {"text": "Section 73", "source_refs": ["DOC-002:SRC-001"]}
                    ],
                    "court_reasoning": [
                        {"text": "Court reasoning", "source_refs": ["DOC-003:SRC-001"]}
                    ],
                    "findings": [
                        {"text": "Finding 1", "source_refs": ["DOC-004:SRC-001"]}
                    ],
                    "decisions": [
                        {"text": "Decision 1", "source_refs": ["DOC-005:SRC-001"]}
                    ],
                }
            ]

        with patch("backend.app.case.analyze.get_llm_provider", return_value=fake_provider):
            final_case = analyze_case("case-multilevel-100", fas)

    assert final_case.status == "complete"
    assert "doc_registry" in final_case.meta
    doc_reg = final_case.meta["doc_registry"]

    # All 6 documents must be present in doc_registry
    for idx in range(1, num_docs + 1):
        doc_label = f"DOC-{idx:03d}"
        assert doc_label in doc_reg
        assert doc_reg[doc_label]["filename"] == f"multi_file_{idx}.pdf"
        assert "SRC-001" in doc_reg[doc_label]["src_registry"]

    # Collect EVERY source_ref across EVERY field in final_case
    all_final_refs = []

    def collect_from_items(items):
        if items:
            for it in items:
                all_final_refs.extend(it.source_refs)

    collect_from_items(final_case.overall_facts)
    collect_from_items(final_case.procedural_history)
    collect_from_items(final_case.issues)
    collect_from_items(final_case.disputed_matters)
    collect_from_items(final_case.undisputed_facts)
    collect_from_items(final_case.evidence_summary)
    collect_from_items(final_case.legal_provisions)
    collect_from_items(final_case.court_reasoning)
    collect_from_items(final_case.findings)
    collect_from_items(final_case.decisions)

    if final_case.timeline:
        for ev in final_case.timeline:
            all_final_refs.extend(ev.source_refs)

    if final_case.claims_and_defenses:
        for rel in final_case.claims_and_defenses:
            all_final_refs.extend(rel.source_refs)

    assert len(all_final_refs) > 0

    # Invariant: EVERY source_ref in final_case MUST resolve to a valid ResolvedCitation
    for ref in all_final_refs:
        citation = resolve_ref(ref, doc_reg)
        assert citation is not None, f"Reference '{ref}' failed to resolve against doc_registry"
        assert citation.source_ref == ref
        doc_idx = int(ref.split(":")[0].replace("DOC-", ""))
        assert citation.filename == f"multi_file_{doc_idx}.pdf"
        assert citation.page_start == doc_idx * 2 - 1
        assert citation.page_end == doc_idx * 2
        assert citation.pages == [doc_idx * 2 - 1, doc_idx * 2]

    # Verify presentation builders resolve all citations
    da = build_detailed_analysis(final_case)
    assert da is not None
    for sec in da.sections:
        if sec.items:
            for it in sec.items:
                if it.source_refs:
                    assert len(it.citations) > 0
                    for cit in it.citations:
                        assert cit.filename.startswith("multi_file_")
        if sec.timeline_events:
            for ev in sec.timeline_events:
                if ev.source_refs:
                    assert len(ev.citations) > 0
        if sec.relationships:
            for rel in sec.relationships:
                if rel.source_refs:
                    assert len(rel.citations) > 0

