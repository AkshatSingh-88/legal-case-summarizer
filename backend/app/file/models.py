"""File-level analysis models — AnalysisItem + FileAnalysis."""

from pydantic import BaseModel, ConfigDict, Field


class AnalysisItem(BaseModel):
    model_config = ConfigDict(extra="ignore")

    text: str
    source_refs: list[str]


class FileAnalysis(BaseModel):
    model_config = ConfigDict(extra="ignore")

    document_id: str
    filename: str

    chunk_ids: list[str]
    chunk_count: int
    pages: list[int]
    page_start: int
    page_end: int

    analyzed_chunk_ids: list[str]
    failed_chunk_ids: list[str]
    coverage: float
    status: str  # complete | partial | failed

    document_type: str | None = None  # petition|reply|affidavit|evidence|order|judgment|annexure|unknown

    facts: list[AnalysisItem] | None = None
    procedural_events: list[AnalysisItem] | None = None
    issues: list[AnalysisItem] | None = None
    arguments: list[AnalysisItem] | None = None
    counterarguments: list[AnalysisItem] | None = None
    evidence: list[AnalysisItem] | None = None
    legal_provisions: list[AnalysisItem] | None = None
    court_observations: list[AnalysisItem] | None = None
    court_reasoning: list[AnalysisItem] | None = None
    findings: list[AnalysisItem] | None = None
    decisions: list[AnalysisItem] | None = None
    important_dates: list[AnalysisItem] | None = None

    uncertainty: str | None = None
    meta: dict = Field(default_factory=dict)

    model: str
    provider: str
