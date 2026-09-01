"""Presentation layer models — QuickSummary, SummarySection, DetailedAnalysis, ProgressivePresentation."""

from pydantic import BaseModel, ConfigDict, Field

from backend.app.case.models import CaseRelationship, CaseTimelineEvent
from backend.app.file.models import AnalysisItem


class QuickSummary(BaseModel):
    model_config = ConfigDict(extra="ignore")

    case_id: str
    case_overview: str
    parties: list[str] | None = None

    key_facts: list[AnalysisItem] | None = None
    core_issues: list[AnalysisItem] | None = None
    key_arguments: list[AnalysisItem] | None = None

    current_status: str
    decision_or_disposition: str | None = None

    confidence: float
    uncertainty: str | None = None
    source_refs: list[str]


class SummarySection(BaseModel):
    model_config = ConfigDict(extra="ignore")

    section_id: str
    title: str
    section_type: str  # text | items | relationships | timeline
    order: int

    text: str | None = None
    items: list[AnalysisItem] | None = None
    relationships: list[CaseRelationship] | None = None
    timeline_events: list[CaseTimelineEvent] | None = None

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


class ProgressivePresentation(BaseModel):
    model_config = ConfigDict(extra="ignore")

    case_id: str
    status: str  # complete | partial | failed
    quick_summary_status: str  # ready | pending | failed
    detailed_analysis_status: str  # ready | pending | failed

    quick_summary: QuickSummary | None = None
    detailed_analysis: DetailedAnalysis | None = None

    case_coverage: float
    confidence: float
    uncertainty: str | None = None
