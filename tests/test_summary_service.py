"""Unit and integration tests for SummaryService."""

from datetime import datetime, timezone
from unittest.mock import MagicMock, patch
from uuid import UUID, uuid4

import pytest
from fastapi import HTTPException

from backend.app.api.schemas.summary import (
    SummaryResponse,
    SummaryStatus,
    SummaryType,
)
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
from backend.app.services.case_service import CaseService
from backend.app.services.models import CallerContext
from backend.app.services.summary_service import SummaryService, get_summary_service


def _create_query_mock(data_list):
    """Creates a mock query builder supporting chained .eq(), .select(), .update(), etc."""
    builder = MagicMock()
    builder.eq.return_value = builder
    builder.select.return_value = builder
    builder.update.return_value = builder
    builder.insert.return_value = builder
    builder.execute.return_value = MagicMock(data=data_list)
    return builder


@pytest.fixture
def caller_user():
    return CallerContext(user_id="00000000-0000-0000-0000-000000000001", role="authenticated")


@pytest.fixture
def mock_case_service():
    return MagicMock(spec=CaseService)


def _build_sample_detailed_analysis(case_id_str: str) -> DetailedAnalysis:
    citation1 = ResolvedCitation(
        source_ref="DOC-001:SRC-001",
        doc_label="DOC-001",
        document_id="9b1deb4d-3b7d-4bad-9bdd-2b0d7b3dcb6d",
        filename="petition.pdf",
        page_start=1,
        page_end=3,
        pages=[1, 2, 3],
    )
    citation2 = ResolvedCitation(
        source_ref="DOC-002:SRC-001",
        doc_label="DOC-002",
        document_id="8a0deb4d-3b7d-4bad-9bdd-2b0d7b3dcb5c",
        filename="reply.pdf",
        page_start=5,
        page_end=5,
        pages=[5],
    )

    item1 = CitedAnalysisItem(
        text="Petitioner claims breach of contract dated 12-Jan-2022.",
        source_refs=["DOC-001:SRC-001"],
        citations=[citation1],
    )
    item2 = CitedAnalysisItem(
        text="Respondent contends force majeure due to supply disruptions.",
        source_refs=["DOC-002:SRC-001"],
        citations=[citation2],
    )

    sec_overview = SummarySection(
        section_id="sec_overview",
        title="Executive Overview",
        section_type="text",
        order=1,
        text="Commercial dispute over supply delivery delays.",
        items=None,
        relationships=None,
        timeline_events=None,
        source_refs=["DOC-001:SRC-001", "DOC-002:SRC-001"],
    )

    sec_facts = SummarySection(
        section_id="sec_facts",
        title="Key Facts & Contentions",
        section_type="items",
        order=2,
        text=None,
        items=[item1, item2],
        relationships=None,
        timeline_events=None,
        source_refs=["DOC-001:SRC-001", "DOC-002:SRC-001"],
    )

    sec_timeline = SummarySection(
        section_id="sec_timeline",
        title="Chronology of Events",
        section_type="timeline",
        order=3,
        text=None,
        items=None,
        relationships=None,
        timeline_events=[
            CitedTimelineEvent(
                event_id="evt_001",
                date_raw="2022-01-12",
                date_normalized="2022-01-12",
                event="Supply Agreement executed.",
                document_ids=["9b1deb4d-3b7d-4bad-9bdd-2b0d7b3dcb6d"],
                source_refs=["DOC-001:SRC-001"],
                is_disputed=False,
                citations=[citation1],
            )
        ],
        source_refs=["DOC-001:SRC-001"],
    )

    sec_rel = SummarySection(
        section_id="sec_relationships",
        title="Party Relationships",
        section_type="relationships",
        order=4,
        text=None,
        items=None,
        relationships=[
            CitedRelationship(
                relationship_id="rel_001",
                relationship_type="Buyer-Supplier",
                source_document_id="9b1deb4d-3b7d-4bad-9bdd-2b0d7b3dcb6d",
                source_item="Petitioner",
                target_document_id="8a0deb4d-3b7d-4bad-9bdd-2b0d7b3dcb5c",
                target_item="Respondent",
                status="active",
                source_refs=["DOC-001:SRC-001"],
                notes="Long term supply arrangement.",
                citations=[citation1],
            )
        ],
        timeline_events=None,
        source_refs=["DOC-001:SRC-001"],
    )

    return DetailedAnalysis(
        case_id=case_id_str,
        section_count=4,
        sections=[sec_overview, sec_facts, sec_timeline, sec_rel],
        case_coverage=1.0,
        status="complete",
        confidence=0.96,
        uncertainty=None,
        meta={"document_count": 2},
        analysis_mode="detailed",
        is_preliminary=False,
    )


def test_unconfigured_supabase_raises_500():
    with patch("backend.app.services.summary_service.get_supabase_client", return_value=None):
        svc = SummaryService(client=None)
        caller = CallerContext(user_id="u1", role="authenticated")
        with pytest.raises(HTTPException) as exc:
            svc.get_summary(str(uuid4()), caller)
        assert exc.value.status_code == 500
        assert exc.value.detail["error"]["code"] == "DATABASE_UNAVAILABLE"


def test_summary_serialization_and_deserialization_fidelity(caller_user, mock_case_service):
    case_id = uuid4()
    case_id_str = str(case_id)
    analysis = _build_sample_detailed_analysis(case_id_str)

    mock_client = MagicMock()
    # Check existing returns empty list -> insert is called
    mock_client.table().select.return_value = _create_query_mock([])

    summary_id = str(uuid4())
    now = datetime.now(timezone.utc)
    inserted_row = {
        "id": summary_id,
        "case_id": case_id_str,
        "summary_type": "detailed",
        "status": "ready",
        "content": analysis.model_dump(mode="json"),
        "created_at": now.isoformat(),
        "updated_at": now.isoformat(),
    }
    mock_client.table().insert.return_value = _create_query_mock([inserted_row])

    svc = SummaryService(client=mock_client, case_service=mock_case_service)
    upsert_resp = svc.upsert_summary(case_id, analysis)

    assert upsert_resp.id == UUID(summary_id)
    assert upsert_resp.case_id == case_id
    assert upsert_resp.status == SummaryStatus.READY
    assert upsert_resp.content is not None
    assert upsert_resp.content.case_id == case_id_str
    assert upsert_resp.content.section_count == 4

    # Now verify get_summary deserializes with 100% citation and provenance fidelity
    mock_client.table().select.return_value = _create_query_mock([inserted_row])
    mock_case_service.get_case.return_value = MagicMock()

    get_resp = svc.get_summary(case_id, caller_user)

    assert get_resp.status == SummaryStatus.READY
    retrieved_content = get_resp.content
    assert retrieved_content is not None
    assert len(retrieved_content.sections) == 4

    # Inspect citations in facts section
    facts_sec = retrieved_content.sections[1]
    assert facts_sec.section_id == "sec_facts"
    assert facts_sec.items is not None
    assert len(facts_sec.items) == 2

    # Check citation 1 fidelity
    c1 = facts_sec.items[0].citations[0]
    assert c1.source_ref == "DOC-001:SRC-001"
    assert c1.doc_label == "DOC-001"
    assert c1.document_id == "9b1deb4d-3b7d-4bad-9bdd-2b0d7b3dcb6d"
    assert c1.filename == "petition.pdf"
    assert c1.page_start == 1
    assert c1.page_end == 3
    assert c1.pages == [1, 2, 3]

    # Check timeline citation fidelity
    tl_sec = retrieved_content.sections[2]
    assert tl_sec.timeline_events is not None
    assert tl_sec.timeline_events[0].date_raw == "2022-01-12"
    assert tl_sec.timeline_events[0].citations[0].doc_label == "DOC-001"

    # Check relationships citation fidelity
    rel_sec = retrieved_content.sections[3]
    assert rel_sec.relationships is not None
    assert rel_sec.relationships[0].source_item == "Petitioner"
    assert rel_sec.relationships[0].relationship_type == "Buyer-Supplier"


def test_upsert_summary_updates_existing_row(mock_case_service):
    case_id = uuid4()
    case_id_str = str(case_id)
    analysis = _build_sample_detailed_analysis(case_id_str)

    summary_id = str(uuid4())
    existing_row = {
        "id": summary_id,
        "case_id": case_id_str,
        "summary_type": "detailed",
        "status": "generating",
        "content": None,
    }

    mock_client = MagicMock()
    mock_client.table().select.return_value = _create_query_mock([existing_row])
    mock_client.table().update.return_value = _create_query_mock([existing_row])

    svc = SummaryService(client=mock_client, case_service=mock_case_service)
    resp = svc.upsert_summary(case_id, analysis)

    assert resp.id == UUID(summary_id)
    assert resp.status == SummaryStatus.READY


def test_get_summary_not_found(caller_user, mock_case_service):
    case_id = uuid4()
    mock_client = MagicMock()
    mock_client.table().select.return_value = _create_query_mock([])
    mock_case_service.get_case.return_value = MagicMock()

    svc = SummaryService(client=mock_client, case_service=mock_case_service)

    with pytest.raises(HTTPException) as exc:
        svc.get_summary(case_id, caller_user)

    assert exc.value.status_code == 404
    assert exc.value.detail["error"]["code"] == "SUMMARY_NOT_FOUND"


def test_get_summary_generating_state(caller_user, mock_case_service):
    case_id = uuid4()
    now = datetime.now(timezone.utc)
    generating_row = {
        "id": str(uuid4()),
        "case_id": str(case_id),
        "summary_type": "detailed",
        "status": "generating",
        "content": None,
        "created_at": now.isoformat(),
        "updated_at": now.isoformat(),
    }

    mock_client = MagicMock()
    mock_client.table().select.return_value = _create_query_mock([generating_row])
    mock_case_service.get_case.return_value = MagicMock()

    svc = SummaryService(client=mock_client, case_service=mock_case_service)
    resp = svc.get_summary(case_id, caller_user)

    assert resp.status == SummaryStatus.GENERATING
    assert resp.content is None


def test_get_summary_failed_state(caller_user, mock_case_service):
    case_id = uuid4()
    now = datetime.now(timezone.utc)
    failed_row = {
        "id": str(uuid4()),
        "case_id": str(case_id),
        "summary_type": "detailed",
        "status": "failed",
        "content": None,
        "created_at": now.isoformat(),
        "updated_at": now.isoformat(),
    }

    mock_client = MagicMock()
    mock_client.table().select.return_value = _create_query_mock([failed_row])
    mock_case_service.get_case.return_value = MagicMock()

    svc = SummaryService(client=mock_client, case_service=mock_case_service)
    resp = svc.get_summary(case_id, caller_user)

    assert resp.status == SummaryStatus.FAILED
    assert resp.content is None


def test_get_summary_ownership_enforcement(caller_user, mock_case_service):
    case_id = uuid4()
    mock_client = MagicMock()

    # CaseService rejects unauthorized caller
    mock_case_service.get_case.side_effect = HTTPException(status_code=404, detail="Case not found")

    svc = SummaryService(client=mock_client, case_service=mock_case_service)

    with pytest.raises(HTTPException) as exc:
        svc.get_summary(case_id, caller_user)

    assert exc.value.status_code == 404


def test_mark_generating_clears_previous_content(mock_case_service):
    case_id = uuid4()
    case_id_str = str(case_id)
    summary_id = str(uuid4())

    existing_row = {"id": summary_id, "case_id": case_id_str, "status": "ready"}
    mock_client = MagicMock()
    mock_client.table().select.return_value = _create_query_mock([existing_row])
    mock_client.table().update.return_value = _create_query_mock([])

    svc = SummaryService(client=mock_client, case_service=mock_case_service)
    svc.mark_generating(case_id)

    # Verify update was called with content = None and status = generating
    mock_client.table().update.assert_called_once()
    update_arg = mock_client.table().update.call_args[0][0]
    assert update_arg["status"] == "generating"
    assert update_arg["content"] is None


def test_mark_failed_sets_status(mock_case_service):
    case_id = uuid4()
    case_id_str = str(case_id)
    summary_id = str(uuid4())

    existing_row = {"id": summary_id, "case_id": case_id_str, "status": "generating"}
    mock_client = MagicMock()
    mock_client.table().select.return_value = _create_query_mock([existing_row])
    mock_client.table().update.return_value = _create_query_mock([])

    svc = SummaryService(client=mock_client, case_service=mock_case_service)
    svc.mark_failed(case_id)

    mock_client.table().update.assert_called_once()
    update_arg = mock_client.table().update.call_args[0][0]
    assert update_arg["status"] == "failed"
    assert update_arg["content"] is None


def test_deserialization_error_handling(caller_user, mock_case_service):
    case_id = uuid4()
    now = datetime.now(timezone.utc)
    # Corrupt content that violates DetailedAnalysis schema
    corrupt_row = {
        "id": str(uuid4()),
        "case_id": str(case_id),
        "summary_type": "detailed",
        "status": "ready",
        "content": {"corrupt_key": "not a detailed analysis"},
        "created_at": now.isoformat(),
        "updated_at": now.isoformat(),
    }

    mock_client = MagicMock()
    mock_client.table().select.return_value = _create_query_mock([corrupt_row])
    mock_case_service.get_case.return_value = MagicMock()

    svc = SummaryService(client=mock_client, case_service=mock_case_service)

    with pytest.raises(HTTPException) as exc:
        svc.get_summary(case_id, caller_user)

    assert exc.value.status_code == 500
    assert exc.value.detail["error"]["code"] == "SUMMARY_DESERIALIZATION_ERROR"


def test_summary_factory():
    mock_client = MagicMock()
    svc = get_summary_service(client=mock_client)
    assert isinstance(svc, SummaryService)
    assert svc.client is mock_client
