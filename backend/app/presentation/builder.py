"""Presentation layer builder — deterministic QuickSummary, DetailedAnalysis, and ProgressivePresentation assembly."""

from backend.app.case.models import CaseAnalysis, CaseRelationship, CaseTimelineEvent
from backend.app.config import get_settings
from backend.app.file.models import AnalysisItem
from backend.app.presentation.citations import (
    CitedAnalysisItem,
    CitedRelationship,
    CitedTimelineEvent,
    cite_items,
    cite_relationships,
    cite_timeline,
)
from backend.app.presentation.models import (
    DetailedAnalysis,
    ProgressivePresentation,
    # QuickSummary,  # QUICK SUMMARY DISABLED — commented out
    SummarySection,
)


def _collect_source_refs_from_items(items: list | None) -> list[str]:
    if not items:
        return []
    refs: list[str] = []
    for it in items:
        if hasattr(it, "source_refs") and it.source_refs:
            refs.extend(it.source_refs)
        elif isinstance(it, dict) and it.get("source_refs"):
            refs.extend(it["source_refs"])
    return list(dict.fromkeys(refs))


def _collect_source_refs_from_relationships(rels: list | None) -> list[str]:
    if not rels:
        return []
    refs: list[str] = []
    for r in rels:
        if hasattr(r, "source_refs") and r.source_refs:
            refs.extend(r.source_refs)
        elif isinstance(r, dict) and r.get("source_refs"):
            refs.extend(r["source_refs"])
    return list(dict.fromkeys(refs))


def _collect_source_refs_from_timeline(events: list | None) -> list[str]:
    if not events:
        return []
    refs: list[str] = []
    for ev in events:
        if hasattr(ev, "source_refs") and ev.source_refs:
            refs.extend(ev.source_refs)
        elif isinstance(ev, dict) and ev.get("source_refs"):
            refs.extend(ev["source_refs"])
    return list(dict.fromkeys(refs))



# =============================================================================
# QUICK SUMMARY DISABLED — build_quick_summary commented out.
# To re-enable: uncomment this function and the QuickSummary import above,
# and uncomment the quick summary block in build_presentation() below.
# =============================================================================
# def build_quick_summary(case_analysis: CaseAnalysis) -> QuickSummary | None:
#     """Deterministically assemble a concise QuickSummary from CaseAnalysis without LLM calls."""
#     if case_analysis.status == "failed" and case_analysis.case_coverage == 0.0:
#         return None
#
#     settings = get_settings()
#     max_facts = settings.presentation_max_quick_facts
#     max_issues = settings.presentation_max_quick_issues
#     max_args = settings.presentation_max_quick_arguments
#
#     doc_registry = case_analysis.meta.get("doc_registry", {}) if case_analysis.meta else {}
#
#     # 1. Overview
#     overview = case_analysis.case_summary
#     if not overview or not overview.strip():
#         if case_analysis.parties:
#             overview = f"Legal case concerning dispute between {', '.join(case_analysis.parties)}."
#         else:
#             overview = f"Legal case {case_analysis.case_id} comprising {case_analysis.document_count} documents."
#
#     # 2. Key Facts (capped and cited)
#     key_facts: list[CitedAnalysisItem] | None = None
#     if case_analysis.overall_facts:
#         key_facts = cite_items(case_analysis.overall_facts[:max_facts], doc_registry)
#
#     # 3. Core Issues (capped and cited)
#     core_issues: list[CitedAnalysisItem] | None = None
#     if case_analysis.issues:
#         core_issues = cite_items(case_analysis.issues[:max_issues], doc_registry)
#
#     # 4. Key Arguments (from claims_and_defenses or disputed_matters, capped and cited)
#     key_arguments: list[CitedAnalysisItem] | None = None
#     if case_analysis.claims_and_defenses:
#         arg_items = []
#         for rel in case_analysis.claims_and_defenses[:max_args]:
#             text = f"{rel.source_item}"
#             if rel.target_item:
#                 text = f"{text} (Counter: {rel.target_item})"
#             arg_items.append(AnalysisItem(text=text, source_refs=rel.source_refs))
#         key_arguments = cite_items(arg_items, doc_registry)
#     elif case_analysis.disputed_matters:
#         key_arguments = cite_items(case_analysis.disputed_matters[:max_args], doc_registry)
#
#     # 5. Status & Decision
#     if case_analysis.final_disposition:
#         current_status = f"Adjudicated — {case_analysis.final_disposition}"
#         decision = case_analysis.final_disposition
#     elif case_analysis.decisions:
#         current_status = "Adjudicated — Orders Rendered"
#         decision = "; ".join(d.text for d in case_analysis.decisions[:2])
#     else:
#         current_status = "Proceedings Pending / Unadjudicated"
#         decision = None
#
#     # 6. Distinct Source Refs
#     all_refs: list[str] = []
#     if key_facts:
#         all_refs.extend(_collect_source_refs_from_items(key_facts))
#     if core_issues:
#         all_refs.extend(_collect_source_refs_from_items(core_issues))
#     if key_arguments:
#         all_refs.extend(_collect_source_refs_from_items(key_arguments))
#
#     unique_refs = list(dict.fromkeys(all_refs))
#
#     return QuickSummary(
#         case_id=case_analysis.case_id,
#         case_overview=overview,
#         parties=case_analysis.parties,
#         key_facts=key_facts,
#         core_issues=core_issues,
#         key_arguments=key_arguments,
#         current_status=current_status,
#         decision_or_disposition=decision,
#         confidence=case_analysis.confidence,
#         uncertainty=case_analysis.uncertainty,
#         source_refs=unique_refs,
#     )



def build_detailed_analysis(case_analysis: CaseAnalysis) -> DetailedAnalysis | None:
    """Dynamically construct a DetailedAnalysis with deterministically ordered, non-empty sections."""
    if case_analysis.status == "failed" and case_analysis.case_coverage == 0.0:
        return None

    sections: list[SummarySection] = []
    doc_registry = case_analysis.meta.get("doc_registry", {}) if case_analysis.meta else {}

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
        cited_procedural = cite_items(case_analysis.procedural_history, doc_registry)
        refs = _collect_source_refs_from_items(cited_procedural)
        sections.append(
            SummarySection(
                section_id="sec_procedural",
                title="Procedural History",
                section_type="items",
                order=2,
                items=cited_procedural,
                source_refs=refs,
            )
        )

    # Section 3: Case Chronology & Timeline (Timeline payload)
    if case_analysis.timeline:
        cited_timeline = cite_timeline(case_analysis.timeline, doc_registry)
        refs = _collect_source_refs_from_timeline(cited_timeline)
        sections.append(
            SummarySection(
                section_id="sec_timeline",
                title="Case Chronology & Timeline",
                section_type="timeline",
                order=3,
                timeline_events=cited_timeline,
                source_refs=refs,
            )
        )

    # Section 4: Factual Background (Items payload)
    if case_analysis.overall_facts:
        cited_facts = cite_items(case_analysis.overall_facts, doc_registry)
        refs = _collect_source_refs_from_items(cited_facts)
        sections.append(
            SummarySection(
                section_id="sec_facts",
                title="Factual Background",
                section_type="items",
                order=4,
                items=cited_facts,
                source_refs=refs,
            )
        )

    # Section 5: Undisputed & Admitted Facts (Items payload)
    if case_analysis.undisputed_facts:
        cited_undisputed = cite_items(case_analysis.undisputed_facts, doc_registry)
        refs = _collect_source_refs_from_items(cited_undisputed)
        sections.append(
            SummarySection(
                section_id="sec_undisputed",
                title="Undisputed & Admitted Facts",
                section_type="items",
                order=5,
                items=cited_undisputed,
                source_refs=refs,
            )
        )

    # Section 6: Legal Issues & Points of Determination (Items payload)
    if case_analysis.issues:
        cited_issues = cite_items(case_analysis.issues, doc_registry)
        refs = _collect_source_refs_from_items(cited_issues)
        sections.append(
            SummarySection(
                section_id="sec_issues",
                title="Legal Issues & Points of Determination",
                section_type="items",
                order=6,
                items=cited_issues,
                source_refs=refs,
            )
        )

    # Section 7: Claims, Defenses & Counterarguments (Relationships payload)
    if case_analysis.claims_and_defenses:
        cited_claims = cite_relationships(case_analysis.claims_and_defenses, doc_registry)
        refs = _collect_source_refs_from_relationships(cited_claims)
        sections.append(
            SummarySection(
                section_id="sec_claims",
                title="Claims, Defenses & Counterarguments",
                section_type="relationships",
                order=7,
                relationships=cited_claims,
                source_refs=refs,
            )
        )

    # Section 8: Disputed Matters & Contradictions (Items payload)
    if case_analysis.disputed_matters:
        cited_disputed = cite_items(case_analysis.disputed_matters, doc_registry)
        refs = _collect_source_refs_from_items(cited_disputed)
        sections.append(
            SummarySection(
                section_id="sec_disputed",
                title="Disputed Matters & Contradictions",
                section_type="items",
                order=8,
                items=cited_disputed,
                source_refs=refs,
            )
        )

    # Section 9: Evidentiary Record & Exhibits (Items payload)
    if case_analysis.evidence_summary:
        cited_evidence = cite_items(case_analysis.evidence_summary, doc_registry)
        refs = _collect_source_refs_from_items(cited_evidence)
        sections.append(
            SummarySection(
                section_id="sec_evidence",
                title="Evidentiary Record & Exhibits",
                section_type="items",
                order=9,
                items=cited_evidence,
                source_refs=refs,
            )
        )

    # Section 10: Applicable Legal Provisions & Statutes (Items payload)
    if case_analysis.legal_provisions:
        cited_laws = cite_items(case_analysis.legal_provisions, doc_registry)
        refs = _collect_source_refs_from_items(cited_laws)
        sections.append(
            SummarySection(
                section_id="sec_laws",
                title="Applicable Legal Provisions & Statutes",
                section_type="items",
                order=10,
                items=cited_laws,
                source_refs=refs,
            )
        )

    # Section 11: Judicial Observations & Reasoning (Items payload)
    if case_analysis.court_reasoning:
        cited_reasoning = cite_items(case_analysis.court_reasoning, doc_registry)
        refs = _collect_source_refs_from_items(cited_reasoning)
        sections.append(
            SummarySection(
                section_id="sec_reasoning",
                title="Judicial Observations & Reasoning",
                section_type="items",
                order=11,
                items=cited_reasoning,
                source_refs=refs,
            )
        )

    # Section 12: Judicial Findings of Fact & Law (Items payload)
    if case_analysis.findings:
        cited_findings = cite_items(case_analysis.findings, doc_registry)
        refs = _collect_source_refs_from_items(cited_findings)
        sections.append(
            SummarySection(
                section_id="sec_findings",
                title="Judicial Findings of Fact & Law",
                section_type="items",
                order=12,
                items=cited_findings,
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

        cited_decisions = cite_items(dec_items, doc_registry)
        refs = _collect_source_refs_from_items(cited_decisions)
        sections.append(
            SummarySection(
                section_id="sec_decisions",
                title="Operative Orders & Final Disposition",
                section_type="items",
                order=13,
                items=cited_decisions,
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


def build_presentation(
    case_analysis: CaseAnalysis | None = None,
    *,
    quick_case: CaseAnalysis | None = None,
    detailed_case: CaseAnalysis | None = None,
    case_id: str | None = None,
) -> ProgressivePresentation:
    """Main presentation generator assembling progressive presentation container."""
    # Positional case_analysis backward compatibility
    # QUICK SUMMARY DISABLED: quick_case assignment is preserved but unused (commented blocks below).
    if case_analysis is not None:
        if quick_case is None and detailed_case is None:
            # quick_case = case_analysis  # QUICK SUMMARY DISABLED
            detailed_case = case_analysis
        elif quick_case is None:
            pass  # quick_case = case_analysis  # QUICK SUMMARY DISABLED
        elif detailed_case is None:
            detailed_case = case_analysis

    resolved_case_id = case_id
    if resolved_case_id is None:
        # if quick_case is not None:  # QUICK SUMMARY DISABLED
        #     resolved_case_id = quick_case.case_id
        if detailed_case is not None:
            resolved_case_id = detailed_case.case_id
        else:
            resolved_case_id = "unknown_case"

    # QUICK SUMMARY DISABLED — the block below is commented out.
    # To re-enable: uncomment the block and re-enable build_quick_summary above.
    # qs: QuickSummary | None = None
    # quick_status = "pending"
    # if quick_case is not None:
    #     if quick_case.status == "failed" and quick_case.case_coverage == 0.0:
    #         quick_status = "failed"
    #     else:
    #         qs = build_quick_summary(quick_case)
    #         quick_status = "ready" if qs is not None else "failed"
    qs = None
    quick_status = "disabled"

    da: DetailedAnalysis | None = None
    detailed_status = "pending"
    if detailed_case is not None:
        if detailed_case.status == "failed" and detailed_case.case_coverage == 0.0:
            detailed_status = "failed"
        else:
            da = build_detailed_analysis(detailed_case)
            detailed_status = "ready" if da is not None else "failed"

    ref_case = detailed_case  # or quick_case  # QUICK SUMMARY DISABLED
    if ref_case is not None:
        status = ref_case.status
        coverage = ref_case.case_coverage
        confidence = ref_case.confidence
        uncertainty = ref_case.uncertainty
    else:
        status = "failed"
        coverage = 0.0
        confidence = 0.0
        uncertainty = "No case analyses available"

    return ProgressivePresentation(
        case_id=resolved_case_id,
        status=status,
        quick_summary_status=quick_status,
        detailed_analysis_status=detailed_status,
        quick_summary=qs,
        detailed_analysis=da,
        case_coverage=coverage,
        confidence=confidence,
        uncertainty=uncertainty,
    )

