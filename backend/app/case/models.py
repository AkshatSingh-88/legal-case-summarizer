"""Case-level analysis models — CaseRelationship, CaseTimelineEvent, and CaseAnalysis."""

from pydantic import BaseModel, ConfigDict, Field

from backend.app.file.models import AnalysisItem


class CaseRelationship(BaseModel):
    model_config = ConfigDict(extra="ignore")

    relationship_id: str
    relationship_type: str  # claim_defense | claim_counterargument | argument_evidence_support | argument_evidence_contradiction | evidence_court_consideration | reasoning_finding | finding_decision | contradiction | agreement

    source_document_id: str
    source_item: str

    target_document_id: str | None = None
    target_item: str | None = None

    status: str  # disputed | agreed | supported | contradicted | undecided | decided

    source_refs: list[str]

    notes: str | None = None


class CaseTimelineEvent(BaseModel):
    model_config = ConfigDict(extra="ignore")

    event_id: str
    date_raw: str
    date_normalized: str | None = None

    event: str

    document_ids: list[str]
    source_refs: list[str]

    is_disputed: bool = False
    conflict_details: str | None = None


class CaseAnalysis(BaseModel):
    model_config = ConfigDict(extra="ignore")

    case_id: str

    document_ids: list[str]
    document_count: int

    documents: list[dict]

    analyzed_document_ids: list[str]
    failed_document_ids: list[str]

    case_coverage: float
    status: str  # complete | partial | failed

    case_summary: str | None = None
    parties: list[str] | None = None

    overall_facts: list[AnalysisItem] | None = None
    procedural_history: list[AnalysisItem] | None = None
    timeline: list[CaseTimelineEvent] | None = None

    issues: list[AnalysisItem] | None = None

    claims_and_defenses: list[CaseRelationship] | None = None

    disputed_matters: list[AnalysisItem] | None = None
    undisputed_facts: list[AnalysisItem] | None = None

    evidence_summary: list[AnalysisItem] | None = None
    legal_provisions: list[AnalysisItem] | None = None

    court_reasoning: list[AnalysisItem] | None = None
    findings: list[AnalysisItem] | None = None
    decisions: list[AnalysisItem] | None = None

    final_disposition: str | None = None

    cross_file_relationships: list[CaseRelationship] | None = None

    confidence: float
    uncertainty: str | None = None

    meta: dict = Field(default_factory=dict)

    model: str
    provider: str
