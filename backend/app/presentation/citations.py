"""Presentation citation models and deterministic resolution layer."""

from pydantic import BaseModel, ConfigDict, Field


class ResolvedCitation(BaseModel):
    model_config = ConfigDict(extra="ignore")

    source_ref: str
    doc_label: str
    document_id: str
    filename: str
    page_start: int
    page_end: int
    pages: list[int] = Field(default_factory=list)


class CitedAnalysisItem(BaseModel):
    model_config = ConfigDict(extra="ignore")

    text: str
    source_refs: list[str] = Field(default_factory=list)
    citations: list[ResolvedCitation] = Field(default_factory=list)


class CitedTimelineEvent(BaseModel):
    model_config = ConfigDict(extra="ignore")

    event_id: str
    date_raw: str
    date_normalized: str | None = None
    event: str
    document_ids: list[str] = Field(default_factory=list)
    source_refs: list[str] = Field(default_factory=list)
    is_disputed: bool = False
    conflict_details: str | None = None
    citations: list[ResolvedCitation] = Field(default_factory=list)


class CitedRelationship(BaseModel):
    model_config = ConfigDict(extra="ignore")

    relationship_id: str
    relationship_type: str
    source_document_id: str
    source_item: str
    target_document_id: str | None = None
    target_item: str | None = None
    status: str
    source_refs: list[str] = Field(default_factory=list)
    notes: str | None = None
    citations: list[ResolvedCitation] = Field(default_factory=list)


def resolve_ref(compound_ref: str, doc_registry: dict) -> ResolvedCitation | None:
    """Deterministically resolve a compound reference ('DOC-001:SRC-001') to a ResolvedCitation."""
    if not compound_ref or not isinstance(compound_ref, str) or ":" not in compound_ref:
        return None

    doc_label, src_id = compound_ref.split(":", 1)
    doc_label = doc_label.strip()
    src_id = src_id.strip()

    if not doc_registry or not isinstance(doc_registry, dict):
        return None

    doc_info = doc_registry.get(doc_label)
    if not doc_info or not isinstance(doc_info, dict):
        return None

    src_registry = doc_info.get("src_registry")
    if not src_registry or not isinstance(src_registry, dict):
        return None

    src_info = src_registry.get(src_id)
    if not src_info or not isinstance(src_info, dict):
        return None

    page_start = src_info.get("page_start", 0)
    page_end = src_info.get("page_end", 0)
    pages = src_info.get("pages")
    if pages is None:
        pages = [page_start] if page_start > 0 else []

    return ResolvedCitation(
        source_ref=compound_ref,
        doc_label=doc_label,
        document_id=src_info.get("document_id", doc_info.get("document_id", "")),
        filename=src_info.get("filename", doc_info.get("filename", "")),
        page_start=page_start,
        page_end=page_end,
        pages=pages,
    )


def resolve_refs(source_refs: list[str] | None, doc_registry: dict) -> list[ResolvedCitation]:
    """Resolve a list of source references into deduplicated, order-preserving ResolvedCitations."""
    if not source_refs:
        return []

    citations: list[ResolvedCitation] = []
    seen: set[str] = set()

    for ref in source_refs:
        if not ref or ref in seen:
            continue
        seen.add(ref)
        citation = resolve_ref(ref, doc_registry)
        if citation is not None:
            citations.append(citation)

    return citations


def cite_items(items: list | None, doc_registry: dict) -> list[CitedAnalysisItem] | None:
    """Transform a list of AnalysisItems into CitedAnalysisItems with resolved citations."""
    if items is None:
        return None
    if not items:
        return []

    result: list[CitedAnalysisItem] = []
    for it in items:
        if isinstance(it, CitedAnalysisItem):
            if not it.citations and it.source_refs and doc_registry:
                it = CitedAnalysisItem(
                    text=it.text,
                    source_refs=it.source_refs,
                    citations=resolve_refs(it.source_refs, doc_registry),
                )
            result.append(it)
        elif hasattr(it, "text") and hasattr(it, "source_refs"):
            refs = getattr(it, "source_refs", []) or []
            result.append(
                CitedAnalysisItem(
                    text=getattr(it, "text", ""),
                    source_refs=refs,
                    citations=resolve_refs(refs, doc_registry),
                )
            )
        elif isinstance(it, dict):
            refs = it.get("source_refs", []) or []
            result.append(
                CitedAnalysisItem(
                    text=it.get("text", ""),
                    source_refs=refs,
                    citations=resolve_refs(refs, doc_registry),
                )
            )
        elif isinstance(it, str):
            result.append(
                CitedAnalysisItem(
                    text=it,
                    source_refs=[],
                    citations=[],
                )
            )

    return result


def cite_timeline(events: list | None, doc_registry: dict) -> list[CitedTimelineEvent] | None:
    """Transform a list of CaseTimelineEvents into CitedTimelineEvents with resolved citations."""
    if events is None:
        return None
    if not events:
        return []

    result: list[CitedTimelineEvent] = []
    for ev in events:
        if isinstance(ev, CitedTimelineEvent):
            if not ev.citations and ev.source_refs and doc_registry:
                ev = CitedTimelineEvent(
                    event_id=ev.event_id,
                    date_raw=ev.date_raw,
                    date_normalized=ev.date_normalized,
                    event=ev.event,
                    document_ids=ev.document_ids,
                    source_refs=ev.source_refs,
                    is_disputed=ev.is_disputed,
                    conflict_details=ev.conflict_details,
                    citations=resolve_refs(ev.source_refs, doc_registry),
                )
            result.append(ev)
        elif hasattr(ev, "event_id") and hasattr(ev, "source_refs"):
            refs = getattr(ev, "source_refs", []) or []
            result.append(
                CitedTimelineEvent(
                    event_id=getattr(ev, "event_id", ""),
                    date_raw=getattr(ev, "date_raw", ""),
                    date_normalized=getattr(ev, "date_normalized", None),
                    event=getattr(ev, "event", ""),
                    document_ids=getattr(ev, "document_ids", []) or [],
                    source_refs=refs,
                    is_disputed=getattr(ev, "is_disputed", False),
                    conflict_details=getattr(ev, "conflict_details", None),
                    citations=resolve_refs(refs, doc_registry),
                )
            )
        elif isinstance(ev, dict):
            refs = ev.get("source_refs", []) or []
            result.append(
                CitedTimelineEvent(
                    event_id=ev.get("event_id", ""),
                    date_raw=ev.get("date_raw", ""),
                    date_normalized=ev.get("date_normalized"),
                    event=ev.get("event", ""),
                    document_ids=ev.get("document_ids", []) or [],
                    source_refs=refs,
                    is_disputed=ev.get("is_disputed", False),
                    conflict_details=ev.get("conflict_details"),
                    citations=resolve_refs(refs, doc_registry),
                )
            )

    return result


def cite_relationships(rels: list | None, doc_registry: dict) -> list[CitedRelationship] | None:
    """Transform a list of CaseRelationships into CitedRelationships with resolved citations."""
    if rels is None:
        return None
    if not rels:
        return []

    result: list[CitedRelationship] = []
    for r in rels:
        if isinstance(r, CitedRelationship):
            if not r.citations and r.source_refs and doc_registry:
                r = CitedRelationship(
                    relationship_id=r.relationship_id,
                    relationship_type=r.relationship_type,
                    source_document_id=r.source_document_id,
                    source_item=r.source_item,
                    target_document_id=r.target_document_id,
                    target_item=r.target_item,
                    status=r.status,
                    source_refs=r.source_refs,
                    notes=r.notes,
                    citations=resolve_refs(r.source_refs, doc_registry),
                )
            result.append(r)
        elif hasattr(r, "relationship_id") and hasattr(r, "source_refs"):
            refs = getattr(r, "source_refs", []) or []
            result.append(
                CitedRelationship(
                    relationship_id=getattr(r, "relationship_id", ""),
                    relationship_type=getattr(r, "relationship_type", ""),
                    source_document_id=getattr(r, "source_document_id", ""),
                    source_item=getattr(r, "source_item", ""),
                    target_document_id=getattr(r, "target_document_id", None),
                    target_item=getattr(r, "target_item", None),
                    status=getattr(r, "status", "disputed"),
                    source_refs=refs,
                    notes=getattr(r, "notes", None),
                    citations=resolve_refs(refs, doc_registry),
                )
            )
        elif isinstance(r, dict):
            refs = r.get("source_refs", []) or []
            result.append(
                CitedRelationship(
                    relationship_id=r.get("relationship_id", ""),
                    relationship_type=r.get("relationship_type", ""),
                    source_document_id=r.get("source_document_id", ""),
                    source_item=r.get("source_item", ""),
                    target_document_id=r.get("target_document_id"),
                    target_item=r.get("target_item"),
                    status=r.get("status", "disputed"),
                    source_refs=refs,
                    notes=r.get("notes"),
                    citations=resolve_refs(refs, doc_registry),
                )
            )

    return result
