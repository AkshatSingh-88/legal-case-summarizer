"""Phase 10 & 11 — Progressive Quick Summary + Detailed Dynamic Analysis + Citation Layer."""

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
    # QuickSummary,  # QUICK SUMMARY DISABLED — commented out
    SummarySection,
)
from backend.app.presentation.builder import (
    build_detailed_analysis,
    build_presentation,
    # build_quick_summary,  # QUICK SUMMARY DISABLED — commented out
)

__all__ = [
    # "QuickSummary",  # QUICK SUMMARY DISABLED — commented out
    "SummarySection",
    "DetailedAnalysis",
    "ProgressivePresentation",
    "ResolvedCitation",
    "CitedAnalysisItem",
    "CitedTimelineEvent",
    "CitedRelationship",
    "resolve_ref",
    "resolve_refs",
    "cite_items",
    "cite_timeline",
    "cite_relationships",
    # "build_quick_summary",  # QUICK SUMMARY DISABLED — commented out
    "build_detailed_analysis",
    "build_presentation",
]
