"""Presentation layer models — QuickSummary, SummarySection, DetailedAnalysis, ProgressivePresentation."""

from pydantic import BaseModel, ConfigDict, Field

from backend.app.case.models import CaseRelationship, CaseTimelineEvent
from backend.app.file.models import AnalysisItem
from backend.app.presentation.citations import (
    CitedAnalysisItem,
    CitedRelationship,
    CitedTimelineEvent,
    ResolvedCitation,
)



# =============================================================================
# QUICK SUMMARY DISABLED — QuickSummary model commented out.
# To re-enable: uncomment this class.
# =============================================================================
# class QuickSummary(BaseModel):
#     model_config = ConfigDict(extra="ignore")
#
#     case_id: str
#     case_overview: str
#     parties: list[str] | None = None
#
#     key_facts: list[CitedAnalysisItem] | None = None
#     core_issues: list[CitedAnalysisItem] | None = None
#     key_arguments: list[CitedAnalysisItem] | None = None
#
#     current_status: str
#     decision_or_disposition: str | None = None
#
#     confidence: float
#     uncertainty: str | None = None
#     source_refs: list[str]
#
#     analysis_mode: str = "quick"
#     is_preliminary: bool = True
#     disclaimer: str = (
#         "Preliminary summary generated from NLP-selected relevant passages. "
#         "Some contextual details or peripheral facts may be omitted because "
#         "the complete document corpus was not supplied to the analysis model."
#     )



class SummarySection(BaseModel):
    model_config = ConfigDict(extra="ignore")

    section_id: str
    title: str
    section_type: str  # text | items | relationships | timeline
    order: int

    text: str | None = None
    items: list[CitedAnalysisItem] | None = None
    relationships: list[CitedRelationship] | None = None
    timeline_events: list[CitedTimelineEvent] | None = None

    source_refs: list[str]



class DetailedAnalysis(BaseModel):
    model_config = ConfigDict(extra="ignore")

    case_id: str
    section_count: int
    sections: list[SummarySection]

    case_coverage: float
    status: str  # complete | partial | failed
    confidence: float
    uncertainty: str | None = None
    meta: dict = Field(default_factory=dict)

    analysis_mode: str = "detailed"
    is_preliminary: bool = False


class ProgressivePresentation(BaseModel):
    model_config = ConfigDict(extra="ignore")

    case_id: str
    status: str  # complete | partial | failed
    quick_summary_status: str  # ready | pending | failed
    detailed_analysis_status: str  # ready | pending | failed

    quick_summary: None = None  # QUICK SUMMARY DISABLED (was: QuickSummary | None = None)
    detailed_analysis: DetailedAnalysis | None = None


    case_coverage: float
    confidence: float
    uncertainty: str | None = None
