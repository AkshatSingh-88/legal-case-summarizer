"""Phase 10 Test Suite — Progressive Quick Summary + Detailed Dynamic Analysis."""

from unittest.mock import patch, MagicMock

import pytest

from backend.app.case.models import CaseAnalysis, CaseRelationship, CaseTimelineEvent
from backend.app.config import get_settings
from backend.app.file.models import AnalysisItem
from backend.app.presentation import (
    DetailedAnalysis,
    ProgressivePresentation,
    QuickSummary,
    SummarySection,
    build_detailed_analysis,
    build_presentation,
    build_quick_summary,
)


def make_case_analysis(
    case_id: str = "case-100",
    status: str = "complete",
    coverage: float = 1.0,
    confidence: float = 1.0,
    case_summary: str | None = "Executive summary of the legal dispute.",
    parties: list[str] | None = None,
    overall_facts: list[str] | None = None,
    procedural_history: list[str] | None = None,
    timeline: list[CaseTimelineEvent] | None = None,
    issues: list[str] | None = None,
    claims_and_defenses: list[CaseRelationship] | None = None,
    disputed_matters: list[str] | None = None,
    undisputed_facts: list[str] | None = None,
    evidence_summary: list[str] | None = None,
    legal_provisions: list[str] | None = None,
    court_reasoning: list[str] | None = None,
    findings: list[str] | None = None,
    decisions: list[str] | None = None,
    final_disposition: str | None = None,
    uncertainty: str | None = None,
    document_count: int = 2,
) -> CaseAnalysis:
    def to_items(strings: list[str] | None, prefix: str = "DOC-001:SRC-001") -> list[AnalysisItem] | None:
        if strings is None:
            return None
        return [AnalysisItem(text=s, source_refs=[prefix]) for s in strings]

    return CaseAnalysis(
        case_id=case_id,
        document_ids=[f"doc-{i}" for i in range(document_count)],
        document_count=document_count,
        documents=[
            {
                "doc_id": f"doc-{i}",
                "filename": f"file_{i}.pdf",
                "type": "petition" if i == 0 else "reply",
                "coverage": coverage,
                "status": status,
            }
            for i in range(document_count)
        ],
        analyzed_document_ids=[f"doc-{i}" for i in range(document_count)] if status != "failed" else [],
        failed_document_ids=[f"doc-{i}" for i in range(document_count)] if status == "failed" else [],
        case_coverage=coverage,
        status=status,
        case_summary=case_summary,
        parties=parties or ["Petitioner: Alice", "Respondent: Bob"],
        overall_facts=to_items(overall_facts or ["Fact 1: Agreement signed", "Fact 2: Payment made"]),
        procedural_history=to_items(procedural_history),
        timeline=timeline,
        issues=to_items(issues or ["Issue 1: Whether breach occurred"]),
        claims_and_defenses=claims_and_defenses,
        disputed_matters=to_items(disputed_matters),
        undisputed_facts=to_items(undisputed_facts),
        evidence_summary=to_items(evidence_summary),
        legal_provisions=to_items(legal_provisions),
        court_reasoning=to_items(court_reasoning),
        findings=to_items(findings),
        decisions=to_items(decisions),
        final_disposition=final_disposition,
        cross_file_relationships=None,
        confidence=confidence,
        uncertainty=uncertainty,
        meta={"document_count": document_count},
        model="fake-json",
        provider="fake",
    )


def test_quick_summary_generation():
    ca = make_case_analysis(
        case_summary="Concise summary of contract breach dispute.",
        parties=["Petitioner: Alpha Corp", "Respondent: Beta LLC"],
        overall_facts=["Contract signed in 2020", "Goods delivered in 2021", "Payment withheld"],
        issues=["Whether delivery complied with specifications"],
        final_disposition="Petition Allowed",
    )

    qs = build_quick_summary(ca)
    assert qs is not None
    assert isinstance(qs, QuickSummary)
    assert qs.case_id == "case-100"
    assert "contract breach dispute" in qs.case_overview
    assert qs.parties == ["Petitioner: Alpha Corp", "Respondent: Beta LLC"]
    assert qs.key_facts is not None
    assert len(qs.key_facts) == 3
    assert qs.core_issues is not None
    assert len(qs.core_issues) == 1
    assert "Adjudicated — Petition Allowed" in qs.current_status
    assert qs.decision_or_disposition == "Petition Allowed"
    assert "DOC-001:SRC-001" in qs.source_refs


def test_detailed_analysis_generation():
    ca = make_case_analysis(
        overall_facts=["Fact A", "Fact B"],
        issues=["Issue A"],
        legal_provisions=["Section 73 Contract Act"],
        court_reasoning=["Defendant failed to prove delivery defect."],
        findings=["Breach is established."],
        decisions=["Damages awarded of Rs 50 Lakhs."],
        final_disposition="Suit Decreed with Costs",
    )

    da = build_detailed_analysis(ca)
    assert da is not None
    assert isinstance(da, DetailedAnalysis)
    assert da.case_id == "case-100"
    assert da.section_count > 0
    assert len(da.sections) == da.section_count
    assert da.status == "complete"


def test_dynamic_section_inclusion():
    # Only facts and issues provided -> only sec_overview, sec_facts, sec_issues should exist
    ca = make_case_analysis(
        overall_facts=["Fact 1"],
        issues=["Issue 1"],
        procedural_history=None,
        timeline=None,
        court_reasoning=None,
        findings=None,
        decisions=None,
        final_disposition=None,
    )

    da = build_detailed_analysis(ca)
    assert da is not None
    section_ids = [s.section_id for s in da.sections]
    assert "sec_overview" in section_ids
    assert "sec_facts" in section_ids
    assert "sec_issues" in section_ids
    assert "sec_procedural" not in section_ids
    assert "sec_timeline" not in section_ids
    assert "sec_reasoning" not in section_ids
    assert "sec_findings" not in section_ids
    assert "sec_decisions" not in section_ids


def test_empty_sections_omitted():
    ca = make_case_analysis(
        procedural_history=None,
        timeline=None,
        undisputed_facts=None,
        disputed_matters=None,
        evidence_summary=None,
        legal_provisions=None,
        court_reasoning=None,
        findings=None,
        decisions=None,
        final_disposition=None,
    )

    da = build_detailed_analysis(ca)
    assert da is not None
    for sec in da.sections:
        assert sec.section_id not in (
            "sec_procedural",
            "sec_timeline",
            "sec_undisputed",
            "sec_disputed",
            "sec_evidence",
            "sec_laws",
            "sec_reasoning",
            "sec_findings",
            "sec_decisions",
        )


def test_zero_na_sections():
    ca = make_case_analysis(
        overall_facts=["Fact 1"],
        issues=["Issue 1"],
        final_disposition="Dismissed",
    )

    da = build_detailed_analysis(ca)
    assert da is not None
    for sec in da.sections:
        if sec.text:
            assert "N/A" not in sec.text
            assert "No information available" not in sec.text
        if sec.items:
            for it in sec.items:
                assert it.text != "N/A"
                assert it.text != "None"


def test_deterministic_section_ordering():
    ca = make_case_analysis(
        case_summary="Overview",
        procedural_history=["Filed in HC", "Transferred to Commercial Court"],
        timeline=[
            CaseTimelineEvent(
                event_id="EVT-001",
                date_raw="10 May 2020",
                event="Agreement signed",
                document_ids=["doc-0"],
                source_refs=["DOC-001:SRC-001"],
            )
        ],
        overall_facts=["Fact 1"],
        undisputed_facts=["Undisputed fact"],
        issues=["Issue 1"],
        claims_and_defenses=[
            CaseRelationship(
                relationship_id="REL-001",
                relationship_type="claim_defense",
                source_document_id="doc-0",
                source_item="Claim",
                target_document_id="doc-1",
                target_item="Defense",
                status="disputed",
                source_refs=["DOC-001:SRC-001", "DOC-002:SRC-001"],
            )
        ],
        disputed_matters=["Disputed matter"],
        evidence_summary=["Exhibit A: Statement of Account"],
        legal_provisions=["Section 10 CPC"],
        court_reasoning=["Reasoning"],
        findings=["Finding 1"],
        decisions=["Decision 1"],
        final_disposition="Allowed",
    )

    da = build_detailed_analysis(ca)
    assert da is not None
    orders = [s.order for s in da.sections]
    assert orders == sorted(orders)
    assert orders == list(range(1, 14))


def test_quick_summary_provenance_aggregation():
    ca = make_case_analysis(
        overall_facts=["Fact 1"],
        issues=["Issue 1"],
    )
    ca.overall_facts = [AnalysisItem(text="Fact 1", source_refs=["DOC-001:SRC-001", "DOC-001:SRC-002"])]
    ca.issues = [AnalysisItem(text="Issue 1", source_refs=["DOC-002:SRC-005"])]

    qs = build_quick_summary(ca)
    assert qs is not None
    assert set(qs.source_refs) == {"DOC-001:SRC-001", "DOC-001:SRC-002", "DOC-002:SRC-005"}


def test_detailed_section_provenance_aggregation():
    ca = make_case_analysis(
        overall_facts=["Fact 1", "Fact 2"],
    )
    ca.overall_facts = [
        AnalysisItem(text="Fact 1", source_refs=["DOC-001:SRC-001"]),
        AnalysisItem(text="Fact 2", source_refs=["DOC-001:SRC-003", "DOC-002:SRC-001"]),
    ]

    da = build_detailed_analysis(ca)
    assert da is not None
    facts_sec = next(s for s in da.sections if s.section_id == "sec_facts")
    assert set(facts_sec.source_refs) == {"DOC-001:SRC-001", "DOC-001:SRC-003", "DOC-002:SRC-001"}


def test_partial_case_handling():
    ca = make_case_analysis(
        status="partial",
        coverage=0.5,
        confidence=0.5,
        uncertainty="Document 2 processing failed",
        overall_facts=["Fact 1 from Document 1"],
    )

    pres = build_presentation(ca)
    assert pres.status == "partial"
    assert pres.quick_summary_status == "ready"
    assert pres.detailed_analysis_status == "ready"
    assert pres.quick_summary is not None
    assert pres.quick_summary.confidence == 0.5
    assert "Document 2 processing failed" in (pres.uncertainty or "")


def test_failed_case_handling():
    ca = make_case_analysis(
        status="failed",
        coverage=0.0,
        confidence=0.0,
        uncertainty="All documents failed",
    )

    pres = build_presentation(ca)
    assert pres.status == "failed"
    assert pres.quick_summary_status == "failed"
    assert pres.detailed_analysis_status == "failed"
    assert pres.quick_summary is None
    assert pres.detailed_analysis is None
    assert pres.confidence == 0.0


def test_empty_case_handling():
    ca = CaseAnalysis(
        case_id="case-empty",
        document_ids=[],
        document_count=0,
        documents=[],
        analyzed_document_ids=[],
        failed_document_ids=[],
        case_coverage=0.0,
        status="failed",
        confidence=0.0,
        uncertainty="No document analyses available",
        meta={},
        model="fake-json",
        provider="fake",
    )

    pres = build_presentation(ca)
    assert pres.status == "failed"
    assert pres.quick_summary is None
    assert pres.detailed_analysis is None


def test_case_without_judgment():
    ca = make_case_analysis(
        court_reasoning=None,
        findings=None,
        decisions=None,
        final_disposition=None,
    )

    da = build_detailed_analysis(ca)
    assert da is not None
    sec_ids = [s.section_id for s in da.sections]
    assert "sec_reasoning" not in sec_ids
    assert "sec_findings" not in sec_ids
    assert "sec_decisions" not in sec_ids

    qs = build_quick_summary(ca)
    assert qs is not None
    assert "Proceedings Pending" in qs.current_status
    assert qs.decision_or_disposition is None


def test_case_with_judgment():
    ca = make_case_analysis(
        court_reasoning=["Reason 1"],
        findings=["Finding 1"],
        decisions=["Order 1"],
        final_disposition="Appeal Dismissed",
    )

    da = build_detailed_analysis(ca)
    assert da is not None
    sec_ids = [s.section_id for s in da.sections]
    assert "sec_reasoning" in sec_ids
    assert "sec_findings" in sec_ids
    assert "sec_decisions" in sec_ids

    qs = build_quick_summary(ca)
    assert qs is not None
    assert "Adjudicated — Appeal Dismissed" in qs.current_status
    assert qs.decision_or_disposition == "Appeal Dismissed"


def test_procedural_history_inclusion():
    ca = make_case_analysis(procedural_history=["FIR registered", "Charge sheet filed"])
    da = build_detailed_analysis(ca)
    assert da is not None
    proc_sec = next(s for s in da.sections if s.section_id == "sec_procedural")
    assert proc_sec.items is not None
    assert len(proc_sec.items) == 2


def test_procedural_history_omission():
    ca = make_case_analysis(procedural_history=None)
    da = build_detailed_analysis(ca)
    assert da is not None
    assert not any(s.section_id == "sec_procedural" for s in da.sections)


def test_timeline_section_structured():
    events = [
        CaseTimelineEvent(
            event_id="EVT-001",
            date_raw="01-01-2020",
            event="Contract signed",
            document_ids=["doc-0"],
            source_refs=["DOC-001:SRC-001"],
            is_disputed=True,
            conflict_details="Conflicting date asserted",
        )
    ]
    ca = make_case_analysis(timeline=events)
    da = build_detailed_analysis(ca)
    assert da is not None
    tl_sec = next(s for s in da.sections if s.section_id == "sec_timeline")
    assert tl_sec.section_type == "timeline"
    assert tl_sec.timeline_events is not None
    assert tl_sec.timeline_events[0].is_disputed is True
    assert tl_sec.timeline_events[0].conflict_details == "Conflicting date asserted"


def test_claims_and_defenses_section_structured():
    rels = [
        CaseRelationship(
            relationship_id="REL-001",
            relationship_type="claim_defense",
            source_document_id="doc-0",
            source_item="Non-payment of dues",
            target_document_id="doc-1",
            target_item="Full satisfaction receipt produced",
            status="disputed",
            source_refs=["DOC-001:SRC-001", "DOC-002:SRC-002"],
            notes="Receipt authenticity contested",
        )
    ]
    ca = make_case_analysis(claims_and_defenses=rels)
    da = build_detailed_analysis(ca)
    assert da is not None
    claims_sec = next(s for s in da.sections if s.section_id == "sec_claims")
    assert claims_sec.section_type == "relationships"
    assert claims_sec.relationships is not None
    assert claims_sec.relationships[0].notes == "Receipt authenticity contested"


def test_large_case_presentation():
    # 100-file consolidated CaseAnalysis
    ca = make_case_analysis(
        document_count=100,
        overall_facts=[f"Fact {i}" for i in range(50)],
        issues=[f"Issue {i}" for i in range(10)],
    )

    pres = build_presentation(ca)
    assert pres.status == "complete"
    assert pres.quick_summary is not None
    assert pres.detailed_analysis is not None
    assert pres.detailed_analysis.meta["document_count"] == 100


def test_no_pdf_reopening():
    ca = make_case_analysis()
    with patch("builtins.open", side_effect=AssertionError("open() should not be called in Phase 10")):
        pres = build_presentation(ca)
    assert pres.status == "complete"


def test_no_chunk_rebuilding():
    ca = make_case_analysis()
    with patch("backend.app.chunking.chunk.build_chunks") as mock_build:
        pres = build_presentation(ca)
        mock_build.assert_not_called()
    assert pres.status == "complete"


def test_no_evidence_recomputation():
    ca = make_case_analysis()
    with patch("backend.app.nlp.evidence.build_evidence") as mock_ev:
        pres = build_presentation(ca)
        mock_ev.assert_not_called()
    assert pres.status == "complete"


def test_no_embedding_recomputation():
    ca = make_case_analysis()
    with patch("backend.app.embeddings.embed.embed_chunks") as mock_emb:
        pres = build_presentation(ca)
        mock_emb.assert_not_called()
    assert pres.status == "complete"


def test_zero_additional_llm_calls():
    ca = make_case_analysis()
    with patch("backend.app.llm.provider.get_llm_provider", side_effect=AssertionError("No LLM provider calls allowed in Phase 10")):
        pres = build_presentation(ca)
    assert pres.status == "complete"


def test_config_overrides():
    settings = get_settings()
    assert settings.presentation_max_quick_facts == 5
    assert settings.presentation_max_quick_issues == 3
    assert settings.presentation_max_quick_arguments == 3

    facts = [f"Fact {i}" for i in range(20)]
    issues = [f"Issue {i}" for i in range(10)]
    ca = make_case_analysis(overall_facts=facts, issues=issues)

    qs = build_quick_summary(ca)
    assert qs is not None
    assert len(qs.key_facts) == 5
    assert len(qs.core_issues) == 3


def test_progressive_presentation_container():
    ca = make_case_analysis()
    pres = build_presentation(ca)
    assert isinstance(pres, ProgressivePresentation)
    assert pres.quick_summary_status == "ready"
    assert pres.detailed_analysis_status == "ready"
    assert pres.quick_summary is not None
    assert pres.detailed_analysis is not None
    assert pres.case_coverage == 1.0
    assert pres.confidence == 1.0


def test_existing_phases_remain_green():
    # Verify Phase 9 CaseAnalysis works as input to Phase 10
    from backend.app.case.analyze import analyze_case
    from backend.app.file.models import FileAnalysis
    fa = FileAnalysis(
        document_id="d1",
        filename="p.pdf",
        chunk_ids=["c1"],
        chunk_count=1,
        pages=[1],
        page_start=1,
        page_end=1,
        analyzed_chunk_ids=["c1"],
        failed_chunk_ids=[],
        coverage=1.0,
        status="complete",
        document_type="petition",
        facts=[AnalysisItem(text="Petition filed", source_refs=["SRC-001"])],
        meta={},
        model="fake-json",
        provider="fake",
    )
    with patch("backend.app.case.analyze.get_llm_provider", return_value=lambda p: [{"case_summary": "Summary"}]):
        case_res = analyze_case("case-integration", [fa])

    pres = build_presentation(case_res)
    assert pres.status == "complete"
    assert pres.quick_summary is not None
    assert pres.quick_summary.case_overview == "Summary"
