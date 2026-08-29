"""ChunkAnalysis canonical schema — Pydantic validation."""

from pydantic import BaseModel, ConfigDict


class ChunkAnalysis(BaseModel):
    model_config = ConfigDict(extra="ignore")

    # Provenance — set from Chunk, never from LLM
    chunk_id: str
    document_id: str
    filename: str
    page_start: int
    page_end: int
    pages: list[int]

    # Semantic — optional, no "N/A"
    facts: list[str] | None = None
    procedural_events: list[str] | None = None
    issues: list[str] | None = None
    arguments: list[str] | None = None
    counterarguments: list[str] | None = None
    evidence_mentioned: list[str] | None = None
    legal_provisions: list[str] | None = None
    court_observations: list[str] | None = None
    court_reasoning: list[str] | None = None
    decisions: list[str] | None = None
    important_dates: list[str] | None = None
    entities: list[str] | None = None
    uncertainty: str | None = None
    confidence: float | None = None

    # Provider provenance
    model: str
    provider: str
