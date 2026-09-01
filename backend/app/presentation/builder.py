"""Presentation layer builder — deterministic QuickSummary, DetailedAnalysis, and ProgressivePresentation assembly."""

from backend.app.case.models import CaseAnalysis, CaseRelationship, CaseTimelineEvent
from backend.app.config import get_settings
from backend.app.file.models import AnalysisItem
from backend.app.presentation.models import (
    DetailedAnalysis,
    ProgressivePresentation,
    QuickSummary,
    SummarySection,
)


def _collect_source_refs_from_items(items: list[AnalysisItem] | None) -> list[str]:
    if not items:
        return []
    refs: list[str] = []
    for it in items:
        if isinstance(it, AnalysisItem) and it.source_refs:
            refs.extend(it.source_refs)
    return list(dict.fromkeys(refs))


def _collect_source_refs_from_relationships(rels: list[CaseRelationship] | None) -> list[str]:
    if not rels:
        return []
    refs: list[str] = []
    for r in rels:
        if isinstance(r, CaseRelationship) and r.source_refs:
            refs.extend(r.source_refs)
    return list(dict.fromkeys(refs))


def _collect_source_refs_from_timeline(events: list[CaseTimelineEvent] | None) -> list[str]:
    if not events:
        return []
    refs: list[str] = []
    for ev in events:
        if isinstance(ev, CaseTimelineEvent) and ev.source_refs:
            refs.extend(ev.source_refs)
    return list(dict.fromkeys(refs))


def build_quick_summary(case_analysis: CaseAnalysis) -> QuickSummary | None:
    """Deterministically assemble a concise QuickSummary from CaseAnalysis without LLM calls."""
    if case_analysis.status == "failed" and case_analysis.case_coverage == 0.0:
        return None

    settings = get_settings()
    max_facts = settings.presentation_max_quick_facts
    max_issues = settings.presentation_max_quick_issues
    max_args = settings.presentation_max_quick_arguments

    # 1. Overview
    overview = case_analysis.case_summary
    if not overview or not overview.strip():
        if case_analysis.parties:
            overview = f"Legal case concerning dispute between {', '.join(case_analysis.parties)}."
        else:
            overview = f"Legal case {case_analysis.case_id} comprising {case_analysis.document_count} documents."

    # 2. Key Facts (capped)
    key_facts: list[AnalysisItem] | None = None
    if case_analysis.overall_facts:
        key_facts = case_analysis.overall_facts[:max_facts]

    # 3. Core Issues (capped)
    core_issues: list[AnalysisItem] | None = None
    if case_analysis.issues:
        core_issues = case_analysis.issues[:max_issues]

    # 4. Key Arguments (from claims_and_defenses or disputed_matters)
    key_arguments: list[AnalysisItem] | None = None
    if case_analysis.claims_and_defenses:
        arg_items = []
        for rel in case_analysis.claims_and_defenses[:max_args]:
            text = f"{rel.source_item}"
            if rel.target_item:
                text = f"{text} (Counter: {rel.target_item})"
            arg_items.append(AnalysisItem(text=text, source_refs=rel.source_refs))
        key_arguments = arg_items
    elif case_analysis.disputed_matters:
        key_arguments = case_analysis.disputed_matters[:max_args]

    # 5. Status & Decision
    if case_analysis.final_disposition:
        current_status = f"Adjudicated — {case_analysis.final_disposition}"
        decision = case_analysis.final_disposition
    elif case_analysis.decisions:
        current_status = "Adjudicated — Orders Rendered"
        decision = "; ".join(d.text for d in case_analysis.decisions[:2])
    else:
        current_status = "Proceedings Pending / Unadjudicated"
        decision = None

    # 6. Distinct Source Refs
    all_refs: list[str] = []
    if key_facts:
        all_refs.extend(_collect_source_refs_from_items(key_facts))
    if core_issues:
        all_refs.extend(_collect_source_refs_from_items(core_issues))
    if key_arguments:
        all_refs.extend(_collect_source_refs_from_items(key_arguments))

    unique_refs = list(dict.fromkeys(all_refs))

    return QuickSummary(
        case_id=case_analysis.case_id,
        case_overview=overview,
        parties=case_analysis.parties,
        key_facts=key_facts,
        core_issues=core_issues,
        key_arguments=key_arguments,
        current_status=current_status,
        decision_or_disposition=decision,
        confidence=case_analysis.confidence,
        uncertainty=case_analysis.uncertainty,
        source_refs=unique_refs,
    )


def build_detailed_analysis(case_analysis: CaseAnalysis) -> DetailedAnalysis | None:
    """Dynamically construct a DetailedAnalysis with deterministically ordered, non-empty sections."""
    if case_analysis.status == "failed" and case_analysis.case_coverage == 0.0:
        return None

    sections: list[SummarySection] = []

    # Section 1: Case Overview & Parties (Text payload)
    if case_analysis.case_summary or case_analysis.parties:
        overview_parts = []
        if case_analysis.parties:
            overview_parts.append(f"Parties: {', '.join(case_analysis.parties)}")
        if case_analysis.case_summary:
            overview_parts.append(case_analysis.case_summary)
        overview_text = "\n\n".join(overview_parts)
        sections.append(
            SummarySection(
                section_id="sec_overview",
                title="Case Overview & Parties",
                section_type="text",
                order=1,
                text=overview_text,
                source_refs=[],
            )
        )

    # Section 2: Procedural History (Items payload)
    if case_analysis.procedural_history:
        refs = _collect_source_refs_from_items(case_analysis.procedural_history)
        sections.append(
            SummarySection(
                section_id="sec_procedural",
                title="Procedural History",
                section_type="items",
                order=2,
                items=case_analysis.procedural_history,
                source_refs=refs,
            )
        )

    # Section 3: Case Chronology & Timeline (Timeline payload)
    if case_analysis.timeline:
        refs = _collect_source_refs_from_timeline(case_analysis.timeline)
        sections.append(
            SummarySection(
                section_id="sec_timeline",
                title="Case Chronology & Timeline",
                section_type="timeline",
                order=3,
                timeline_events=case_analysis.timeline,
                source_refs=refs,
            )
        )

    # Section 4: Factual Background (Items payload)
    if case_analysis.overall_facts:
        refs = _collect_source_refs_from_items(case_analysis.overall_facts)
        sections.append(
            SummarySection(
                section_id="sec_facts",
                title="Factual Background",
                section_type="items",
                order=4,
                items=case_analysis.overall_facts,
                source_refs=refs,
            )
        )

    # Section 5: Undisputed & Admitted Facts (Items payload)
    if case_analysis.undisputed_facts:
        refs = _collect_source_refs_from_items(case_analysis.undisputed_facts)
        sections.append(
            SummarySection(
                section_id="sec_undisputed",
                title="Undisputed & Admitted Facts",
                section_type="items",
                order=5,
                items=case_analysis.undisputed_facts,
                source_refs=refs,
            )
        )

    # Section 6: Legal Issues & Points of Determination (Items payload)
    if case_analysis.issues:
        refs = _collect_source_refs_from_items(case_analysis.issues)
        sections.append(
            SummarySection(
                section_id="sec_issues",
                title="Legal Issues & Points of Determination",
                section_type="items",
                order=6,
                items=case_analysis.issues,
                source_refs=refs,
            )
        )

    # Section 7: Claims, Defenses & Counterarguments (Relationships payload)
    if case_analysis.claims_and_defenses:
        refs = _collect_source_refs_from_relationships(case_analysis.claims_and_defenses)
        sections.append(
            SummarySection(
                section_id="sec_claims",
                title="Claims, Defenses & Counterarguments",
                section_type="relationships",
                order=7,
                relationships=case_analysis.claims_and_defenses,
                source_refs=refs,
            )
        )

    # Section 8: Disputed Matters & Contradictions (Items payload)
    if case_analysis.disputed_matters:
        refs = _collect_source_refs_from_items(case_analysis.disputed_matters)
        sections.append(
            SummarySection(
                section_id="sec_disputed",
                title="Disputed Matters & Contradictions",
                section_type="items",
                order=8,
                items=case_analysis.disputed_matters,
                source_refs=refs,
            )
        )

    # Section 9: Evidentiary Record & Exhibits (Items payload)
    if case_analysis.evidence_summary:
        refs = _collect_source_refs_from_items(case_analysis.evidence_summary)
        sections.append(
            SummarySection(
                section_id="sec_evidence",
                title="Evidentiary Record & Exhibits",
                section_type="items",
                order=9,
                items=case_analysis.evidence_summary,
                source_refs=refs,
            )
        )

    # Section 10: Applicable Legal Provisions & Statutes (Items payload)
    if case_analysis.legal_provisions:
        refs = _collect_source_refs_from_items(case_analysis.legal_provisions)
        sections.append(
            SummarySection(
                section_id="sec_laws",
                title="Applicable Legal Provisions & Statutes",
                section_type="items",
                order=10,
                items=case_analysis.legal_provisions,
                source_refs=refs,
            )
        )

    # Section 11: Judicial Observations & Reasoning (Items payload)
    if case_analysis.court_reasoning:
        refs = _collect_source_refs_from_items(case_analysis.court_reasoning)
        sections.append(
            SummarySection(
                section_id="sec_reasoning",
                title="Judicial Observations & Reasoning",
                section_type="items",
                order=11,
                items=case_analysis.court_reasoning,
                source_refs=refs,
            )
        )

    # Section 12: Judicial Findings of Fact & Law (Items payload)
    if case_analysis.findings:
        refs = _collect_source_refs_from_items(case_analysis.findings)
        sections.append(
            SummarySection(
                section_id="sec_findings",
                title="Judicial Findings of Fact & Law",
                section_type="items",
                order=12,
                items=case_analysis.findings,
                source_refs=refs,
            )
        )

    # Section 13: Operative Orders & Final Disposition (Items payload)
    if case_analysis.decisions or case_analysis.final_disposition:
        dec_items = list(case_analysis.decisions) if case_analysis.decisions else []
        if case_analysis.final_disposition:
            disp_item = AnalysisItem(
                text=f"Final Disposition: {case_analysis.final_disposition}",
                source_refs=[],
            )
            dec_items.insert(0, disp_item)

        refs = _collect_source_refs_from_items(dec_items)
        sections.append(
            SummarySection(
                section_id="sec_decisions",
                title="Operative Orders & Final Disposition",
                section_type="items",
                order=13,
                items=dec_items,
                source_refs=refs,
            )
        )

    sections.sort(key=lambda s: s.order)

    return DetailedAnalysis(
        case_id=case_analysis.case_id,
        section_count=len(sections),
        sections=sections,
        case_coverage=case_analysis.case_coverage,
        status=case_analysis.status,
        confidence=case_analysis.confidence,
        uncertainty=case_analysis.uncertainty,
        meta={
            "document_count": case_analysis.document_count,
            "section_ids": [s.section_id for s in sections],
        },
    )


def build_presentation(case_analysis: CaseAnalysis) -> ProgressivePresentation:
    """Main presentation generator assembling progressive presentation container."""
    if case_analysis.status == "failed" and case_analysis.case_coverage == 0.0:
        return ProgressivePresentation(
            case_id=case_analysis.case_id,
            status="failed",
            quick_summary_status="failed",
            detailed_analysis_status="failed",
            quick_summary=None,
            detailed_analysis=None,
            case_coverage=0.0,
            confidence=0.0,
            uncertainty=case_analysis.uncertainty or "Case analysis failed or zero coverage",
        )

    qs = build_quick_summary(case_analysis)
    da = build_detailed_analysis(case_analysis)

    quick_status = "ready" if qs is not None else "failed"
    detailed_status = "ready" if da is not None else "failed"

    return ProgressivePresentation(
        case_id=case_analysis.case_id,
        status=case_analysis.status,
        quick_summary_status=quick_status,
        detailed_analysis_status=detailed_status,
        quick_summary=qs,
        detailed_analysis=da,
        case_coverage=case_analysis.case_coverage,
        confidence=case_analysis.confidence,
        uncertainty=case_analysis.uncertainty,
    )
