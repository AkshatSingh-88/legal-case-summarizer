"""Phase 9 Test Suite — Cross-File & Case-Level Legal Analysis."""

import re
import uuid
from unittest.mock import patch, MagicMock

import pytest

from backend.app.case import CaseAnalysis, CaseRelationship, CaseTimelineEvent, analyze_case
from backend.app.config import get_settings
from backend.app.file.models import AnalysisItem, FileAnalysis


def make_file_analysis(
    document_id: str | None = None,
    filename: str = "doc.pdf",
    document_type: str = "petition",
    chunk_count: int = 5,
    facts: list[str] | None = None,
    issues: list[str] | None = None,
    arguments: list[str] | None = None,
    evidence: list[str] | None = None,
    court_reasoning: list[str] | None = None,
    findings: list[str] | None = None,
    decisions: list[str] | None = None,
    important_dates: list[str] | None = None,
    status: str = "complete",
    coverage: float = 1.0,
    failed: bool = False,
    uncertainty: str | None = None,
) -> FileAnalysis:
    doc_id = document_id or str(uuid.uuid4())
    chunk_ids = [str(uuid.uuid4()) for _ in range(chunk_count)]

    if failed:
        return FileAnalysis(
            document_id=doc_id,
            filename=filename,
            chunk_ids=chunk_ids,
            chunk_count=chunk_count,
            pages=[1, 2],
            page_start=1,
            page_end=2,
            analyzed_chunk_ids=[],
            failed_chunk_ids=chunk_ids,
            coverage=0.0,
            status="failed",
            document_type=document_type,
            uncertainty=uncertainty or "File processing failed",
            meta={},
            model="fake-json",
            provider="fake",
        )

    def to_items(strings: list[str] | None, prefix: str = "SRC-001") -> list[AnalysisItem] | None:
        if strings is None:
            return None
        return [AnalysisItem(text=s, source_refs=[prefix]) for s in strings]

    return FileAnalysis(
        document_id=doc_id,
        filename=filename,
        chunk_ids=chunk_ids,
        chunk_count=chunk_count,
        pages=[1, 2],
        page_start=1,
        page_end=2,
        analyzed_chunk_ids=chunk_ids,
        failed_chunk_ids=[],
        coverage=coverage,
        status=status,
        document_type=document_type,
        facts=to_items(facts or ["The petitioner filed a claim."]),
        issues=to_items(issues),
        arguments=to_items(arguments),
        evidence=to_items(evidence),
        court_reasoning=to_items(court_reasoning),
        findings=to_items(findings),
        decisions=to_items(decisions),
        important_dates=to_items(important_dates or ["15 January 2021 — Notice issued"]),
        uncertainty=uncertainty,
        meta={},
        model="fake-json",
        provider="fake",
    )


def test_one_document_case():
    fa = make_file_analysis(document_id="doc-1", filename="petition.pdf", document_type="petition")
    call_count = {"n": 0}

    def fake_provider(prompts):
        call_count["n"] += 1
        return [{
            "case_summary": "Single document summary.",
            "parties": ["Petitioner: Alice"],
            "overall_facts": [{"text": "Fact from doc", "source_refs": ["DOC-001:SRC-001"]}],
            "final_disposition": "Pending",
        }]

    with patch("backend.app.case.analyze.get_llm_provider", return_value=fake_provider):
        result = analyze_case("case-001", [fa])

    assert call_count["n"] == 1
    assert result.case_id == "case-001"
    assert result.document_count == 1
    assert result.status == "complete"
    assert result.case_coverage == 1.0
    assert result.confidence == 1.0
    assert result.overall_facts is not None
    assert len(result.overall_facts) == 1
    assert result.overall_facts[0].source_refs == ["DOC-001:SRC-001"]


def test_two_document_adversarial_case():
    fa1 = make_file_analysis(document_id="doc-1", filename="petition.pdf", document_type="petition", facts=["Petitioner paid loan."])
    fa2 = make_file_analysis(document_id="doc-2", filename="reply.pdf", document_type="reply", facts=["Respondent denies receiving payment."])

    def fake_provider(prompts):
        prompt = prompts[0]
        assert "DOC-001" in prompt
        assert "DOC-002" in prompt
        return [{
            "case_summary": "Dispute over loan payment.",
            "parties": ["Petitioner: Alice", "Respondent: Bob"],
            "claims_and_defenses": [
                {
                    "relationship_id": "REL-001",
                    "relationship_type": "claim_defense",
                    "source_document_id": "doc-1",
                    "source_item": "Petitioner paid loan.",
                    "target_document_id": "doc-2",
                    "target_item": "Respondent denies receiving payment.",
                    "status": "disputed",
                    "source_refs": ["DOC-001:SRC-001", "DOC-002:SRC-001"],
                }
            ],
            "disputed_matters": [{"text": "Whether payment was made.", "source_refs": ["DOC-001:SRC-001"]}],
        }]

    with patch("backend.app.case.analyze.get_llm_provider", return_value=fake_provider):
        result = analyze_case("case-002", [fa1, fa2])

    assert result.status == "complete"
    assert result.claims_and_defenses is not None
    assert len(result.claims_and_defenses) == 1
    rel = result.claims_and_defenses[0]
    assert rel.relationship_type == "claim_defense"
    assert rel.status == "disputed"
    assert "DOC-001:SRC-001" in rel.source_refs


def test_full_case_lifecycle():
    fa_petition = make_file_analysis(document_id="d1", filename="1_petition.pdf", document_type="petition", facts=["Claim asserted"])
    fa_reply = make_file_analysis(document_id="d2", filename="2_reply.pdf", document_type="reply", facts=["Claim denied"])
    fa_evidence = make_file_analysis(document_id="d3", filename="3_bank_statement.pdf", document_type="evidence", evidence=["Bank statement showing transfer"])
    fa_judgment = make_file_analysis(
        document_id="d4",
        filename="4_judgment.pdf",
        document_type="judgment",
        court_reasoning=["Bank statement confirms transfer occurred."],
        findings=["Payment was duly made."],
        decisions=["Petition allowed with costs."],
    )

    def fake_provider(prompts):
        return [{
            "case_summary": "Case fully adjudicated in favor of petitioner.",
            "court_reasoning": [{"text": "Bank statement confirms transfer occurred.", "source_refs": ["DOC-004:SRC-001"]}],
            "findings": [{"text": "Payment was duly made.", "source_refs": ["DOC-004:SRC-001"]}],
            "decisions": [{"text": "Petition allowed with costs.", "source_refs": ["DOC-004:SRC-001"]}],
            "final_disposition": "Petition Allowed",
            "cross_file_relationships": [
                {
                    "relationship_id": "REL-002",
                    "relationship_type": "finding_decision",
                    "source_document_id": "d4",
                    "source_item": "Payment was duly made.",
                    "target_document_id": "d4",
                    "target_item": "Petition allowed.",
                    "status": "decided",
                    "source_refs": ["DOC-004:SRC-001"],
                }
            ],
        }]

    with patch("backend.app.case.analyze.get_llm_provider", return_value=fake_provider):
        result = analyze_case("case-full", [fa_petition, fa_reply, fa_evidence, fa_judgment])

    assert result.status == "complete"
    assert result.final_disposition == "Petition Allowed"
    assert result.findings is not None
    assert result.decisions is not None


def test_contradiction_detection():
    fa1 = make_file_analysis(document_id="d1", filename="affidavit_a.pdf", document_type="affidavit", facts=["Signatures occurred on 1 Jan 2022"])
    fa2 = make_file_analysis(document_id="d2", filename="affidavit_b.pdf", document_type="affidavit", facts=["No signature occurred on 1 Jan 2022"])

    def fake_provider(prompts):
        return [{
            "cross_file_relationships": [
                {
                    "relationship_id": "REL-CONTRADICTION",
                    "relationship_type": "contradiction",
                    "source_document_id": "d1",
                    "source_item": "Signatures occurred on 1 Jan 2022",
                    "target_document_id": "d2",
                    "target_item": "No signature occurred on 1 Jan 2022",
                    "status": "disputed",
                    "source_refs": ["DOC-001:SRC-001", "DOC-002:SRC-001"],
                }
            ]
        }]

    with patch("backend.app.case.analyze.get_llm_provider", return_value=fake_provider):
        result = analyze_case("case-contra", [fa1, fa2])

    assert result.cross_file_relationships is not None
    assert result.cross_file_relationships[0].relationship_type == "contradiction"
    assert result.cross_file_relationships[0].status == "disputed"


def test_agreement_extraction():
    fa1 = make_file_analysis(document_id="d1", filename="petition.pdf", document_type="petition", facts=["Tenancy agreement exists"])
    fa2 = make_file_analysis(document_id="d2", filename="reply.pdf", document_type="reply", facts=["Tenancy agreement admitted"])

    def fake_provider(prompts):
        return [{
            "undisputed_facts": [{"text": "Tenancy agreement existence is admitted by both parties.", "source_refs": ["DOC-001:SRC-001", "DOC-002:SRC-001"]}]
        }]

    with patch("backend.app.case.analyze.get_llm_provider", return_value=fake_provider):
        result = analyze_case("case-agree", [fa1, fa2])

    assert result.undisputed_facts is not None
    assert len(result.undisputed_facts) == 1
    assert "DOC-001:SRC-001" in result.undisputed_facts[0].source_refs


def test_timeline_merging():
    fa1 = make_file_analysis(document_id="d1", filename="p.pdf", important_dates=["10 May 2020 — Agreement Signed", "15 June 2021 — Notice Sent"])
    fa2 = make_file_analysis(document_id="d2", filename="r.pdf", important_dates=["01 January 2020 — Initial Meeting", "20 July 2021 — Reply to Notice"])

    def fake_provider(prompts):
        return [{"case_summary": "Timeline test"}]

    with patch("backend.app.case.analyze.get_llm_provider", return_value=fake_provider):
        result = analyze_case("case-timeline", [fa1, fa2])

    assert result.timeline is not None
    assert len(result.timeline) == 4
    # Check chronological ordering: 01 Jan 2020 -> 10 May 2020 -> 15 June 2021 -> 20 July 2021
    assert "Initial Meeting" in result.timeline[0].event
    assert "Agreement Signed" in result.timeline[1].event
    assert "Notice Sent" in result.timeline[2].event
    assert "Reply to Notice" in result.timeline[3].event


def test_timeline_conflict_preservation():
    # Same event with conflicting dates asserted by two different documents
    fa1 = make_file_analysis(document_id="d1", filename="p.pdf", important_dates=["10 May 2020 — Agreement Signed"])
    fa2 = make_file_analysis(document_id="d2", filename="r.pdf", important_dates=["15 June 2020 — Agreement Signed"])

    def fake_provider(prompts):
        return [{"case_summary": "Conflict test"}]

    with patch("backend.app.case.analyze.get_llm_provider", return_value=fake_provider):
        result = analyze_case("case-conflict-dates", [fa1, fa2])

    assert result.timeline is not None
    assert len(result.timeline) == 1
    event = result.timeline[0]
    assert event.is_disputed is True
    assert event.conflict_details is not None
    assert "10 May 2020" in event.conflict_details
    assert "15 June 2020" in event.conflict_details
    assert "d1" in event.document_ids
    assert "d2" in event.document_ids


def test_provenance_preservation():
    fa1 = make_file_analysis(document_id="doc-123", filename="petition.pdf", document_type="petition", facts=["Fact 1"])

    def fake_provider(prompts):
        return [{
            "overall_facts": [{"text": "Synthesized Fact 1", "source_refs": ["DOC-001:SRC-001"]}]
        }]

    with patch("backend.app.case.analyze.get_llm_provider", return_value=fake_provider):
        result = analyze_case("case-prov", [fa1])

    assert result.overall_facts is not None
    assert result.overall_facts[0].source_refs == ["DOC-001:SRC-001"]


def test_invalid_source_refs_rejected():
    fa1 = make_file_analysis(document_id="d1", filename="petition.pdf", facts=["Fact 1"])

    def fake_provider(prompts):
        return [{
            "overall_facts": [
                {"text": "Fact Valid", "source_refs": ["DOC-001:SRC-001", "DOC-999:SRC-999"]},
                {"text": "Fact Totally Invalid", "source_refs": ["DOC-999:SRC-999"]},
            ]
        }]

    with patch("backend.app.case.analyze.get_llm_provider", return_value=fake_provider):
        result = analyze_case("case-invalid-refs", [fa1])

    assert result.overall_facts is not None
    assert len(result.overall_facts) == 1
    assert result.overall_facts[0].text == "Fact Valid"
    assert result.overall_facts[0].source_refs == ["DOC-001:SRC-001"]
    assert "DOC-999:SRC-999" in result.meta.get("invalid_source_refs", [])
    assert "DOC-999:SRC-999" in (result.uncertainty or "")


def test_partial_document_failure():
    fa_ok = make_file_analysis(document_id="d1", filename="good.pdf", chunk_count=10, coverage=1.0)
    fa_failed = make_file_analysis(document_id="d2", filename="bad.pdf", chunk_count=10, failed=True, uncertainty="OCR failed")

    def fake_provider(prompts):
        return [{"case_summary": "Partial analysis"}]

    with patch("backend.app.case.analyze.get_llm_provider", return_value=fake_provider):
        result = analyze_case("case-partial", [fa_ok, fa_failed])

    assert result.status == "partial"
    assert result.case_coverage == 0.5  # 10 / 20 chunks
    assert "d2" in result.failed_document_ids
    assert "d1" in result.analyzed_document_ids
    assert "OCR failed" in (result.uncertainty or "")


def test_all_documents_failed():
    fa1 = make_file_analysis(document_id="d1", failed=True)
    fa2 = make_file_analysis(document_id="d2", failed=True)
    call_count = {"n": 0}

    def fake_provider(prompts):
        call_count["n"] += 1
        return [{}]

    with patch("backend.app.case.analyze.get_llm_provider", return_value=fake_provider):
        result = analyze_case("case-all-failed", [fa1, fa2])

    assert call_count["n"] == 0
    assert result.status == "failed"
    assert result.case_coverage == 0.0
    assert result.confidence == 0.0
    assert len(result.failed_document_ids) == 2


def test_malformed_provider_output():
    fa1 = make_file_analysis(document_id="d1", filename="p.pdf")

    def fake_provider(prompts):
        return [{
            "overall_facts": "This should have been a list, not a string",
            "case_summary": "Malformed test",
        }]

    with patch("backend.app.case.analyze.get_llm_provider", return_value=fake_provider):
        result = analyze_case("case-malformed", [fa1])

    assert result.status == "partial"
    assert "Malformed" in (result.uncertainty or "")
    assert result.overall_facts is None


def test_provider_failure_retry():
    fa1 = make_file_analysis(document_id="d1", filename="p.pdf")

    def failing_provider(prompts):
        raise RuntimeError("API Timeout / 500 error")

    with patch("backend.app.case.analyze.get_llm_provider", return_value=failing_provider):
        result = analyze_case("case-fail-provider", [fa1])

    assert result.status == "partial"
    assert result.confidence == 0.0
    assert "API Timeout" in (result.uncertainty or "")


def test_10_files_one_call():
    files = [make_file_analysis(document_id=f"doc-{i}", filename=f"file_{i}.pdf", chunk_count=2) for i in range(10)]
    call_count = {"n": 0}

    def counting_provider(prompts):
        call_count["n"] += 1
        return [{"case_summary": "10 files consolidated"}]

    with patch("backend.app.case.analyze.get_llm_provider", return_value=counting_provider):
        result = analyze_case("case-10", files)

    assert call_count["n"] == 1
    assert result.document_count == 10
    assert result.status == "complete"


def test_25_files_four_calls():
    files = [make_file_analysis(document_id=f"doc-{i}", filename=f"file_{i:02d}.pdf", chunk_count=2) for i in range(25)]
    call_count = {"n": 0}

    def counting_provider(prompts):
        call_count["n"] += 1
        return [{"case_summary": f"Consolidated call {call_count['n']}", "overall_facts": [{"text": "Fact", "source_refs": ["DOC-001:SRC-001"]}]}]

    with patch("backend.app.case.analyze.get_llm_provider", return_value=counting_provider):
        result = analyze_case("case-25", files)

    assert call_count["n"] == 4
    assert result.document_count == 25
    assert result.status == "complete"


def test_100_files_eleven_calls():
    files = [make_file_analysis(document_id=f"doc-{i}", filename=f"file_{i:03d}.pdf", chunk_count=1) for i in range(100)]
    call_count = {"n": 0}

    def counting_provider(prompts):
        call_count["n"] += 1
        return [{"case_summary": f"Consolidated call {call_count['n']}", "overall_facts": [{"text": "Fact", "source_refs": ["DOC-001:SRC-001"]}]}]

    with patch("backend.app.case.analyze.get_llm_provider", return_value=counting_provider):
        result = analyze_case("case-100", files)

    assert call_count["n"] == 11
    assert result.document_count == 100
    assert result.status == "complete"


def test_token_limit_protection_single_file():
    huge_facts = ["Detailed legal fact paragraph number " + str(i) + " " * 500 for i in range(200)]
    fa = make_file_analysis(document_id="doc-huge", filename="huge.pdf", facts=huge_facts)

    def counting_provider(prompts):
        return [{"case_summary": "Handled oversized"}]

    with patch("backend.app.case.analyze.get_llm_provider", return_value=counting_provider):
        result = analyze_case("case-token-limit", [fa])

    assert result.status in ("complete", "partial")


def test_token_limit_protection_multi_file():
    large_facts = ["Extensive factual description " * 200 for _ in range(30)]
    files = [make_file_analysis(document_id=f"d-{i}", filename=f"f_{i}.pdf", facts=large_facts) for i in range(5)]

    def counting_provider(prompts):
        return [{"case_summary": "Handled oversized multi-file"}]

    with patch("backend.app.case.analyze.get_llm_provider", return_value=counting_provider):
        result = analyze_case("case-multi-token-limit", files)

    assert result.status in ("complete", "partial")


def test_deterministic_batch_ordering():
    fa_judgment = make_file_analysis(document_id="d-judg", filename="z_judg.pdf", document_type="judgment")
    fa_reply = make_file_analysis(document_id="d-reply", filename="m_reply.pdf", document_type="reply")
    fa_petition = make_file_analysis(document_id="d-pet", filename="a_pet.pdf", document_type="petition")

    prompts_captured = []

    def capturing_provider(prompts):
        prompts_captured.extend(prompts)
        return [{"case_summary": "Order test"}]

    with patch("backend.app.case.analyze.get_llm_provider", return_value=capturing_provider):
        analyze_case("case-order", [fa_judgment, fa_reply, fa_petition])

    assert len(prompts_captured) == 1
    prompt = prompts_captured[0]
    assert "DOC-001 → a_pet.pdf" in prompt
    assert "DOC-002 → m_reply.pdf" in prompt
    assert "DOC-003 → z_judg.pdf" in prompt


def test_empty_case_input():
    call_count = {"n": 0}

    def fake_provider(prompts):
        call_count["n"] += 1
        return [{}]

    with patch("backend.app.case.analyze.get_llm_provider", return_value=fake_provider):
        result = analyze_case("case-empty", [])

    assert call_count["n"] == 0
    assert result.status == "failed"
    assert result.document_count == 0
    assert result.case_coverage == 0.0


def test_no_pdf_reopening():
    fa = make_file_analysis(document_id="d1", filename="file_does_not_exist_on_disk.pdf")
    with patch("builtins.open", side_effect=AssertionError("open() should not be called")):
        with patch("backend.app.case.analyze.get_llm_provider", return_value=lambda p: [{"case_summary": "OK"}]):
            result = analyze_case("case-no-pdf", [fa])
    assert result.status == "complete"


def test_no_chunk_rebuilding():
    fa = make_file_analysis(document_id="d1")
    with patch("backend.app.chunking.chunk.build_chunks") as mock_build:
        with patch("backend.app.case.analyze.get_llm_provider", return_value=lambda p: [{"case_summary": "OK"}]):
            result = analyze_case("case-no-chunk", [fa])
        mock_build.assert_not_called()
    assert result.status == "complete"


def test_no_evidence_recomputation():
    fa = make_file_analysis(document_id="d1")
    with patch("backend.app.nlp.evidence.build_evidence") as mock_ev:
        with patch("backend.app.case.analyze.get_llm_provider", return_value=lambda p: [{"case_summary": "OK"}]):
            result = analyze_case("case-no-evidence", [fa])
        mock_ev.assert_not_called()
    assert result.status == "complete"


def test_no_embedding_recomputation():
    fa = make_file_analysis(document_id="d1")
    with patch("backend.app.embeddings.embed.embed_chunks") as mock_emb:
        with patch("backend.app.case.analyze.get_llm_provider", return_value=lambda p: [{"case_summary": "OK"}]):
            result = analyze_case("case-no-embed", [fa])
        mock_emb.assert_not_called()
    assert result.status == "complete"


def test_claims_and_defenses_structured():
    fa1 = make_file_analysis(document_id="d1", filename="p.pdf")
    fa2 = make_file_analysis(document_id="d2", filename="r.pdf")

    def fake_provider(prompts):
        return [{
            "claims_and_defenses": [
                {
                    "relationship_id": "REL-001",
                    "relationship_type": "claim_counterargument",
                    "source_document_id": "d1",
                    "source_item": "Specific performance of contract",
                    "target_document_id": "d2",
                    "target_item": "Contract is void for uncertainty",
                    "status": "disputed",
                    "source_refs": ["DOC-001:SRC-001", "DOC-002:SRC-001"],
                    "notes": "Section 29 Contract Act argument",
                }
            ]
        }]

    with patch("backend.app.case.analyze.get_llm_provider", return_value=fake_provider):
        result = analyze_case("case-rel-struct", [fa1, fa2])

    assert result.claims_and_defenses is not None
    rel = result.claims_and_defenses[0]
    assert isinstance(rel, CaseRelationship)
    assert rel.relationship_type == "claim_counterargument"
    assert rel.notes == "Section 29 Contract Act argument"


def test_parties_extracted():
    fa1 = make_file_analysis(document_id="d1")

    def fake_provider(prompts):
        return [{
            "parties": ["Petitioner: ABC Ltd.", "Respondent: Union of India", "Intervenor: State of MH"]
        }]

    with patch("backend.app.case.analyze.get_llm_provider", return_value=fake_provider):
        result = analyze_case("case-parties", [fa1])

    assert result.parties is not None
    assert len(result.parties) == 3
    assert "ABC Ltd." in result.parties[0]


def test_disposition_extracted():
    fa1 = make_file_analysis(document_id="d1", document_type="judgment")

    def fake_provider(prompts):
        return [{
            "final_disposition": "Appeal Allowed, Impugned Order Set Aside"
        }]

    with patch("backend.app.case.analyze.get_llm_provider", return_value=fake_provider):
        result = analyze_case("case-disp", [fa1])

    assert result.final_disposition == "Appeal Allowed, Impugned Order Set Aside"


def test_court_reasoning_and_findings():
    fa1 = make_file_analysis(document_id="d1", document_type="judgment")

    def fake_provider(prompts):
        return [{
            "court_reasoning": [{"text": "Principles of natural justice were violated.", "source_refs": ["DOC-001:SRC-001"]}],
            "findings": [{"text": "Inquiry report is null and void.", "source_refs": ["DOC-001:SRC-001"]}],
        }]

    with patch("backend.app.case.analyze.get_llm_provider", return_value=fake_provider):
        result = analyze_case("case-reason-find", [fa1])

    assert result.court_reasoning is not None
    assert result.findings is not None
    assert result.court_reasoning[0].text == "Principles of natural justice were violated."


def test_hierarchical_provenance_survives():
    files = [make_file_analysis(document_id=f"doc-{i}", filename=f"file_{i}.pdf", facts=[f"Fact {i}"]) for i in range(25)]

    def fake_provider(prompts):
        p = prompts[0]
        doc_refs = re.findall(r"DOC-\d{3}:SRC-\d{3}", p)
        cluster_refs = re.findall(r"DOC-\d{3}", p)
        chosen_ref = doc_refs[0] if doc_refs else (cluster_refs[0] if cluster_refs else "DOC-001:SRC-001")
        return [{
            "case_summary": "Hierarchical consolidation",
            "overall_facts": [{"text": "Consolidated Fact", "source_refs": [chosen_ref]}],
        }]

    with patch("backend.app.case.analyze.get_llm_provider", return_value=fake_provider):
        result = analyze_case("case-hier-prov", files)

    assert result.status == "complete"
    assert result.overall_facts is not None
    assert len(result.overall_facts) > 0


def test_config_overrides():
    settings = get_settings()
    assert settings.case_max_files_per_prompt == 10
    assert settings.case_max_tokens == 16000
