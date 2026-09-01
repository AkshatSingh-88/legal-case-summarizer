"""Phase 10 — Progressive Quick Summary + Detailed Dynamic Analysis."""

from backend.app.presentation.models import (
    DetailedAnalysis,
    ProgressivePresentation,
    QuickSummary,
    SummarySection,
)
from backend.app.presentation.builder import (
    build_detailed_analysis,
    build_presentation,
    build_quick_summary,
)

__all__ = [
    "QuickSummary",
    "SummarySection",
    "DetailedAnalysis",
    "ProgressivePresentation",
    "build_quick_summary",
    "build_detailed_analysis",
    "build_presentation",
]
