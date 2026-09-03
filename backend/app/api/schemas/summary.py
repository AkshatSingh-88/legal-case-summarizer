"""Pydantic schemas for Summary resources.

Reuses canonical presentation models: DetailedAnalysis, SummarySection,
CitedAnalysisItem, and ResolvedCitation directly from backend.app.presentation.
"""

from datetime import datetime
from enum import Enum
from uuid import UUID
from pydantic import BaseModel, ConfigDict

from backend.app.presentation.citations import (
    CitedAnalysisItem,
    CitedRelationship,
    CitedTimelineEvent,
    ResolvedCitation,
)
from backend.app.presentation.models import (
    DetailedAnalysis,
    SummarySection,
)


class SummaryType(str, Enum):
    DETAILED = "detailed"


class SummaryStatus(str, Enum):
    GENERATING = "generating"
    READY = "ready"
    FAILED = "failed"
    STALE = "stale"


class SummaryResponse(BaseModel):
    model_config = ConfigDict(extra="ignore")

    id: UUID
    case_id: UUID
    summary_type: SummaryType = SummaryType.DETAILED
    status: SummaryStatus
    content: DetailedAnalysis | None = None
    created_at: datetime
    updated_at: datetime
