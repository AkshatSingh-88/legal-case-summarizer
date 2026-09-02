"""Case-level analysis engine — synthesize FileAnalysis objects into CaseAnalysis."""

import logging
import uuid
from collections import defaultdict
from datetime import datetime
from typing import Callable

from backend.app.case.models import CaseAnalysis, CaseRelationship, CaseTimelineEvent
from backend.app.case.prompts import CASE_ANALYSIS_RESPONSE_SCHEMA, build_case_prompt
from backend.app.chunking.tokenizer import count_tokens
from backend.app.config import get_settings
from backend.app.file.models import AnalysisItem, FileAnalysis
from backend.app.llm.provider import get_llm_provider

logger = logging.getLogger(__name__)

DOC_TYPE_PRIORITY = {
    "petition": 0,
    "reply": 1,
    "written statement": 1,
    "written_statement": 1,
    "rejoinder": 2,
    "affidavit": 3,
    "evidence": 4,
    "annexure": 4,
    "order": 5,
    "judgment": 6,
    "unknown": 7,
}


def _doc_type_priority(doc_type: str | None) -> int:
    if not doc_type:
        return 7
    return DOC_TYPE_PRIORITY.get(doc_type.lower().strip(), 7)


def _is_failed_file(fa: FileAnalysis) -> bool:
    return fa.status == "failed" or (fa.coverage == 0.0 and bool(fa.failed_chunk_ids))


def _create_doc_map(
    file_analyses: list[FileAnalysis],
) -> tuple[dict[str, FileAnalysis], dict[str, str], set[str]]:
    """Deterministically map files to DOC-001, DOC-002, and build valid compound source refs."""
    sorted_files = sorted(
        file_analyses,
        key=lambda fa: (_doc_type_priority(fa.document_type), fa.filename, fa.document_id),
    )
    doc_map: dict[str, FileAnalysis] = {}
    doc_id_to_label: dict[str, str] = {}
    valid_compound_refs: set[str] = set()

    for idx, fa in enumerate(sorted_files, start=1):
        doc_label = f"DOC-{idx:03d}"
        doc_map[doc_label] = fa
        doc_id_to_label[fa.document_id] = doc_label

        for field in [
            "facts", "procedural_events", "issues", "arguments", "counterarguments",
            "evidence", "legal_provisions", "court_observations", "court_reasoning",
            "findings", "decisions", "important_dates",
        ]:
            items = getattr(fa, field, None)
            if items:
                for it in items:
                    refs = it.source_refs if isinstance(it, AnalysisItem) else (
                        it.get("source_refs", []) if isinstance(it, dict) else []
                    )
                    for r in refs:
                        valid_compound_refs.add(f"{doc_label}:{r}")
                        if not r.startswith("DOC-"):
                            valid_compound_refs.add(f"{doc_label}:{r}")
                        else:
                            valid_compound_refs.add(r)

    return doc_map, doc_id_to_label, valid_compound_refs


def _parse_date_safely(s: str) -> datetime | None:
    for fmt in ("%d %B %Y", "%d %b %Y", "%d/%m/%Y", "%d-%m-%Y", "%Y-%m-%d", "%d.%m.%Y", "%B %Y", "%b %Y", "%Y"):
        try:
            return datetime.strptime(s.strip(), fmt)
        except Exception:
            continue
    return None


def _merge_case_timeline(
    file_analyses: list[FileAnalysis],
    doc_id_to_label: dict[str, str],
) -> list[CaseTimelineEvent]:
    """Aggregate structured important_dates directly from FileAnalyses with conflict retention."""
    raw_events: list[dict] = []

    for fa in file_analyses:
        doc_label = doc_id_to_label.get(fa.document_id, "DOC-001")
        if not fa.important_dates:
            continue

        for it in fa.important_dates:
            text = it.text if isinstance(it, AnalysisItem) else (
                it.get("text", "") if isinstance(it, dict) else str(it)
            )
            refs = it.source_refs if isinstance(it, AnalysisItem) else (
                it.get("source_refs", []) if isinstance(it, dict) else []
            )
            compound_refs = [
                f"{doc_label}:{r}" if not r.startswith("DOC-") and not r.startswith("CLUSTER-") else r
                for r in refs
            ]

            parts = text.split("—", 1) if "—" in text else (text.split(" - ", 1) if " - " in text else text.split(":", 1))
            if len(parts) == 2:
                d_str = parts[0].strip()
                desc = parts[1].strip()
            else:
                d_str = text.strip()
                desc = text.strip()

            dt = _parse_date_safely(d_str)
            iso_date = dt.strftime("%Y-%m-%d") if dt else None

            raw_events.append({
                "date_raw": d_str,
                "date_normalized": iso_date,
                "parsed_dt": dt,
                "event": desc,
                "doc_id": fa.document_id,
                "doc_label": doc_label,
                "source_refs": compound_refs,
            })

    if not raw_events:
        return []

    parseable = [e for e in raw_events if e["parsed_dt"] is not None]
    unparseable = [e for e in raw_events if e["parsed_dt"] is None]
    parseable.sort(key=lambda x: x["parsed_dt"])

    all_sorted = parseable + unparseable

    timeline: list[CaseTimelineEvent] = []
    seen_events: dict[str, CaseTimelineEvent] = {}

    for idx, e in enumerate(all_sorted, start=1):
        event_key = e["event"].strip().lower()

        if event_key in seen_events:
            existing = seen_events[event_key]
            if existing.date_raw != e["date_raw"]:
                existing.is_disputed = True
                existing.conflict_details = (
                    f"Conflicting dates asserted: '{existing.date_raw}' vs '{e['date_raw']}'"
                )
                if e["doc_id"] not in existing.document_ids:
                    existing.document_ids.append(e["doc_id"])
                for r in e["source_refs"]:
                    if r not in existing.source_refs:
                        existing.source_refs.append(r)
                continue
            else:
                if e["doc_id"] not in existing.document_ids:
                    existing.document_ids.append(e["doc_id"])
                for r in e["source_refs"]:
                    if r not in existing.source_refs:
                        existing.source_refs.append(r)
                continue

        evt = CaseTimelineEvent(
            event_id=f"EVT-{idx:03d}",
            date_raw=e["date_raw"],
            date_normalized=e["date_normalized"],
            event=e["event"],
            document_ids=[e["doc_id"]],
            source_refs=e["source_refs"],
            is_disputed=False,
            conflict_details=None,
        )
        timeline.append(evt)
        seen_events[event_key] = evt

    return timeline


def _validate_case_source_refs(
    items,
    valid_refs: set[str],
    meta_invalid: list[str],
    meta_empty: list[str],
    uncertainty_parts: list[str],
    cluster_ref_map: dict[str, list[str]] | None = None,
) -> list[dict] | None:
    if items is None:
        return None
    if not isinstance(items, list):
        meta_invalid.append(f"malformed_{type(items).__name__}")
        uncertainty_parts.append(f"Malformed field type {type(items).__name__} — expected list")
        return None

    valid_items: list[dict] = []
    seen_texts: set[str] = set()

    for it in items:
        if isinstance(it, str):
            text = it
            meta_empty.append(text[:50])
            uncertainty_parts.append(f"Empty source_refs for '{text[:30]}' (string without refs)")
            key = text.strip().lower()
            if key in seen_texts:
                continue
            seen_texts.add(key)
            valid_items.append({"text": text, "source_refs": []})
            continue

        if not isinstance(it, dict):
            continue

        text = it.get("text", "")
        raw_refs = it.get("source_refs", [])
        if not isinstance(raw_refs, list):
            raw_refs = [raw_refs] if isinstance(raw_refs, str) else []
        raw_refs = [str(r) for r in raw_refs if r is not None]

        key = text.strip().lower()
        if key in seen_texts:
            continue
        seen_texts.add(key)

        if not raw_refs:
            meta_empty.append(text[:50])
            uncertainty_parts.append(f"Empty source_refs for '{text[:30]}'")
            valid_items.append({"text": text, "source_refs": []})
            continue

        resolved_valid: list[str] = []
        invalid_refs: list[str] = []

        for r in raw_refs:
            if cluster_ref_map and r in cluster_ref_map:
                resolved_valid.extend(cluster_ref_map[r])
            elif r in valid_refs:
                resolved_valid.append(r)
            elif ":" in r:
                unprefixed = ":".join(r.split(":")[1:])
                if unprefixed in valid_refs:
                    resolved_valid.append(unprefixed)
                elif cluster_ref_map and unprefixed in cluster_ref_map:
                    resolved_valid.extend(cluster_ref_map[unprefixed])
                else:
                    invalid_refs.append(r)
            else:
                invalid_refs.append(r)


        if invalid_refs:
            meta_invalid.extend(invalid_refs)
            uncertainty_parts.append(f"Invalid source_refs {invalid_refs} for '{text[:30]}' — excluded invalid")
            if not resolved_valid:
                continue

        unique_refs = list(dict.fromkeys(resolved_valid))
        valid_items.append({"text": text, "source_refs": unique_refs})

    return valid_items if valid_items else None


def _validate_relationships(
    items,
    valid_refs: set[str],
    meta_invalid: list[str],
    uncertainty_parts: list[str],
    cluster_ref_map: dict[str, list[str]] | None = None,
) -> list[CaseRelationship] | None:
    if not items or not isinstance(items, list):
        return None

    valid_rels: list[CaseRelationship] = []
    allowed_types = {
        "claim_defense",
        "claim_counterargument",
        "argument_evidence_support",
        "argument_evidence_contradiction",
        "evidence_court_consideration",
        "reasoning_finding",
        "finding_decision",
        "contradiction",
        "agreement",
    }
    allowed_statuses = {"disputed", "agreed", "supported", "contradicted", "undecided", "decided"}

    for idx, it in enumerate(items, start=1):
        if not isinstance(it, dict):
            continue

        rel_id = str(it.get("relationship_id") or f"REL-{idx:03d}")
        rel_type = str(it.get("relationship_type", "")).lower().strip()
        if rel_type not in allowed_types:
            rel_type = "contradiction" if "contradict" in rel_type else ("agreement" if "agree" in rel_type else "claim_defense")

        src_doc = str(it.get("source_document_id", "unknown"))
        src_item = str(it.get("source_item", ""))
        tgt_doc = it.get("target_document_id")
        tgt_item = it.get("target_item")
        status = str(it.get("status", "")).lower().strip()
        if status not in allowed_statuses:
            status = "disputed" if rel_type in ("contradiction", "claim_defense") else "decided"

        raw_refs = it.get("source_refs", [])
        if not isinstance(raw_refs, list):
            raw_refs = [raw_refs] if isinstance(raw_refs, str) else []
        raw_refs = [str(r) for r in raw_refs if r is not None]

        resolved_valid: list[str] = []
        invalid_refs: list[str] = []

        for r in raw_refs:
            if cluster_ref_map and r in cluster_ref_map:
                resolved_valid.extend(cluster_ref_map[r])
            elif r in valid_refs:
                resolved_valid.append(r)
            elif ":" in r:
                unprefixed = ":".join(r.split(":")[1:])
                if unprefixed in valid_refs:
                    resolved_valid.append(unprefixed)
                elif cluster_ref_map and unprefixed in cluster_ref_map:
                    resolved_valid.extend(cluster_ref_map[unprefixed])
                else:
                    invalid_refs.append(r)
            else:
                invalid_refs.append(r)


        if invalid_refs:
            meta_invalid.extend(invalid_refs)
            uncertainty_parts.append(f"Invalid relationship source_refs {invalid_refs}")

        unique_refs = list(dict.fromkeys(resolved_valid))

        valid_rels.append(
            CaseRelationship(
                relationship_id=rel_id,
                relationship_type=rel_type,
                source_document_id=src_doc,
                source_item=src_item,
                target_document_id=str(tgt_doc) if tgt_doc else None,
                target_item=str(tgt_item) if tgt_item else None,
                status=status,
                source_refs=unique_refs,
                notes=it.get("notes"),
            )
        )

    return valid_rels if valid_rels else None


def _build_case_analysis_from_llm(
    case_id: str,
    all_file_analyses: list[FileAnalysis],
    doc_map: dict[str, FileAnalysis],
    failed_doc_ids: list[str],
    case_coverage: float,
    status: str,
    llm_result: dict,
    model: str,
    provider: str,
    valid_refs: set[str],
    cluster_ref_map: dict[str, list[str]] | None = None,
    doc_id_to_label: dict[str, str] | None = None,
) -> CaseAnalysis:
    meta_invalid: list[str] = []
    meta_empty: list[str] = []
    uncertainty_parts: list[str] = []

    for fa in all_file_analyses:
        if fa.uncertainty:
            uncertainty_parts.append(f"[{fa.filename}]: {fa.uncertainty}")

    if failed_doc_ids:
        uncertainty_parts.append(f"Excluded failed documents: {', '.join(failed_doc_ids)}")

    semantic_fields = [
        "overall_facts", "procedural_history", "issues", "disputed_matters",
        "undisputed_facts", "evidence_summary", "legal_provisions",
        "court_reasoning", "findings", "decisions",
    ]

    validated_data: dict = {}
    has_malformed = False

    for field in semantic_fields:
        original = llm_result.get(field)
        if original is not None and not isinstance(original, list):
            has_malformed = True
            meta_invalid.append(f"malformed_{field}")
            uncertainty_parts.append(f"Malformed field {field} type {type(original).__name__} — expected list")

        validated = _validate_case_source_refs(
            original,
            valid_refs,
            meta_invalid,
            meta_empty,
            uncertainty_parts,
            cluster_ref_map=cluster_ref_map,
        )
        validated_data[field] = validated

    claims_and_defenses = _validate_relationships(
        llm_result.get("claims_and_defenses"),
        valid_refs,
        meta_invalid,
        uncertainty_parts,
        cluster_ref_map=cluster_ref_map,
    )
    cross_file_relationships = _validate_relationships(
        llm_result.get("cross_file_relationships"),
        valid_refs,
        meta_invalid,
        uncertainty_parts,
        cluster_ref_map=cluster_ref_map,
    )

    successful_files = [fa for fa in all_file_analyses if not _is_failed_file(fa)]
    merged_timeline = _merge_case_timeline(successful_files, doc_id_to_label or {})

    parties_raw = llm_result.get("parties")
    parties: list[str] | None = None
    if isinstance(parties_raw, list):
        parties = [str(p) for p in parties_raw if p]
    elif isinstance(parties_raw, str):
        parties = [parties_raw]

    llm_uncertainty = llm_result.get("uncertainty")
    if has_malformed:
        malformed_msg = "Malformed provider output detected"
        llm_uncertainty = f"{llm_uncertainty} | {malformed_msg}" if llm_uncertainty else malformed_msg

    if uncertainty_parts:
        prov_warning = "; ".join(uncertainty_parts[:5])
        llm_uncertainty = f"{llm_uncertainty} | Issues: {prov_warning}" if llm_uncertainty else f"Issues: {prov_warning}"

    final_status = status
    if has_malformed and status == "complete":
        final_status = "partial"

    doc_summaries = [
        {
            "doc_id": fa.document_id,
            "filename": fa.filename,
            "type": fa.document_type or "unknown",
            "coverage": fa.coverage,
            "status": fa.status,
        }
        for fa in all_file_analyses
    ]

    all_doc_ids = [fa.document_id for fa in all_file_analyses]

    return CaseAnalysis(
        case_id=case_id,
        document_ids=all_doc_ids,
        document_count=len(all_doc_ids),
        documents=doc_summaries,
        analyzed_document_ids=[fa.document_id for fa in successful_files],
        failed_document_ids=failed_doc_ids,
        case_coverage=case_coverage,
        status=final_status,
        case_summary=llm_result.get("case_summary"),
        parties=parties,
        overall_facts=validated_data.get("overall_facts"),
        procedural_history=validated_data.get("procedural_history"),
        timeline=merged_timeline if merged_timeline else None,
        issues=validated_data.get("issues"),
        claims_and_defenses=claims_and_defenses,
        disputed_matters=validated_data.get("disputed_matters"),
        undisputed_facts=validated_data.get("undisputed_facts"),
        evidence_summary=validated_data.get("evidence_summary"),
        legal_provisions=validated_data.get("legal_provisions"),
        court_reasoning=validated_data.get("court_reasoning"),
        findings=validated_data.get("findings"),
        decisions=validated_data.get("decisions"),
        final_disposition=llm_result.get("final_disposition"),
        cross_file_relationships=cross_file_relationships,
        confidence=case_coverage if final_status != "failed" else 0.0,
        uncertainty=llm_uncertainty,
        meta={
            "invalid_source_refs": list(set(meta_invalid))[:10],
            "empty_source_refs": meta_empty[:5],
            "document_count": len(all_doc_ids),
        },
        model=model,
        provider=provider,
    )


def _case_analysis_to_file_analysis(ca: CaseAnalysis, doc_id: str, filename: str) -> FileAnalysis:
    """Convert an intermediate cluster CaseAnalysis into a FileAnalysis for next-level consolidation."""
    return FileAnalysis(
        document_id=doc_id,
        filename=filename,
        chunk_ids=ca.document_ids,
        chunk_count=ca.document_count,
        pages=[1],
        page_start=1,
        page_end=1,
        analyzed_chunk_ids=ca.analyzed_document_ids,
        failed_chunk_ids=ca.failed_document_ids,
        coverage=ca.case_coverage,
        status=ca.status,
        document_type="case_cluster",
        facts=ca.overall_facts,
        procedural_events=ca.procedural_history,
        issues=ca.issues,
        arguments=None,
        counterarguments=None,
        evidence=ca.evidence_summary,
        legal_provisions=ca.legal_provisions,
        court_observations=None,
        court_reasoning=ca.court_reasoning,
        findings=ca.findings,
        decisions=ca.decisions,
        important_dates=None,
        uncertainty=ca.uncertainty,
        meta=ca.meta,
        model=ca.model,
        provider=ca.provider,
    )


def _partition_files(
    items: list[FileAnalysis],
    max_per_prompt: int,
    max_tokens: int,
    build_prompt_fn: Callable[[list[FileAnalysis]], str],
) -> list[list[FileAnalysis]]:
    """Partition FileAnalysis items so each batch has <= max_per_prompt items and prompt <= max_tokens."""
    initial_batches = [items[i : i + max_per_prompt] for i in range(0, len(items), max_per_prompt)]
    final_batches: list[list[FileAnalysis]] = []

    for batch in initial_batches:
        if len(batch) <= 1:
            final_batches.append(batch)
            continue
        prompt = build_prompt_fn(batch)
        if count_tokens(prompt) <= max_tokens:
            final_batches.append(batch)
        else:
            mid = len(batch) // 2
            left = _partition_files(batch[:mid], max_per_prompt, max_tokens, build_prompt_fn)
            right = _partition_files(batch[mid:], max_per_prompt, max_tokens, build_prompt_fn)
            final_batches.extend(left)
            final_batches.extend(right)

    return final_batches


def analyze_case(
    case_id: str,
    file_analyses: list[FileAnalysis],
) -> CaseAnalysis:
    """Main entry point for Phase 9 case-level legal synthesis."""
    logger.debug(f"[analyze_case] start case={case_id} files={len(file_analyses)}")
    settings = get_settings()
    model = settings.llm_model
    provider = settings.llm_provider
    max_files = settings.case_max_files_per_prompt
    max_tokens = settings.case_max_tokens

    if not file_analyses:
        return CaseAnalysis(
            case_id=case_id,
            document_ids=[],
            document_count=0,
            documents=[],
            analyzed_document_ids=[],
            failed_document_ids=[],
            case_coverage=0.0,
            status="failed",
            confidence=0.0,
            uncertainty="No document analyses provided for case",
            meta={},
            model=model,
            provider=provider,
        )

    failed_files = [fa for fa in file_analyses if _is_failed_file(fa)]
    successful_files = [fa for fa in file_analyses if not _is_failed_file(fa)]
    failed_doc_ids = [fa.document_id for fa in failed_files]

    total_chunks = sum(fa.chunk_count for fa in file_analyses)
    analyzed_chunks = sum(len(fa.analyzed_chunk_ids) for fa in successful_files)
    case_coverage = analyzed_chunks / total_chunks if total_chunks > 0 else 0.0

    failed_uncertainties = [f"[{fa.filename}]: {fa.uncertainty}" for fa in failed_files if fa.uncertainty]

    if case_coverage == 0.0 or not successful_files:
        unc_msg = "All document analyses failed or zero coverage"
        if failed_uncertainties:
            unc_msg = f"{unc_msg} | {'; '.join(failed_uncertainties)}"
        return CaseAnalysis(
            case_id=case_id,
            document_ids=[fa.document_id for fa in file_analyses],
            document_count=len(file_analyses),
            documents=[
                {
                    "doc_id": fa.document_id,
                    "filename": fa.filename,
                    "type": fa.document_type or "unknown",
                    "coverage": fa.coverage,
                    "status": fa.status,
                }
                for fa in file_analyses
            ],
            analyzed_document_ids=[],
            failed_document_ids=failed_doc_ids,
            case_coverage=0.0,
            status="failed",
            confidence=0.0,
            uncertainty=unc_msg,
            meta={"failed_document_ids": failed_doc_ids},
            model=model,
            provider=provider,
        )

    status = "complete" if case_coverage == 1.0 and not failed_doc_ids else "partial"

    doc_map, doc_id_to_label, valid_compound_refs = _create_doc_map(successful_files)
    doc_registry = {
        doc_label: {
            "document_id": fa.document_id,
            "filename": fa.filename,
            "src_registry": fa.meta.get("src_registry", {}),
        }
        for doc_label, fa in doc_map.items()
    }

    # 1. Evaluate candidate prompt for direct single LLM call
    if len(successful_files) <= max_files:
        candidate_prompt = build_case_prompt(
            case_id, successful_files, doc_map, failed_doc_ids, case_coverage
        )
        if count_tokens(candidate_prompt) <= max_tokens:
            try:
                provider_fn = get_llm_provider(provider, model)
                raw_res = provider_fn([candidate_prompt])
                if isinstance(raw_res, dict):
                    llm_result = raw_res
                elif isinstance(raw_res, list) and raw_res:
                    llm_result = raw_res[0] if isinstance(raw_res[0], dict) else {}
                else:
                    llm_result = {}
            except Exception as e:
                logger.warning(f"Provider failed for direct case analysis: {e}")
                unc = f"Provider failed during case synthesis: {e}"
                if failed_uncertainties:
                    unc = f"{unc} | {'; '.join(failed_uncertainties)}"
                return CaseAnalysis(
                    case_id=case_id,
                    document_ids=[fa.document_id for fa in file_analyses],
                    document_count=len(file_analyses),
                    documents=[],
                    analyzed_document_ids=[fa.document_id for fa in successful_files],
                    failed_document_ids=failed_doc_ids,
                    case_coverage=case_coverage,
                    status="partial",
                    confidence=0.0,
                    uncertainty=unc,
                    meta={"error": str(e), "doc_registry": doc_registry},
                    model=model,
                    provider=provider,
                )

            try:
                ca_direct = _build_case_analysis_from_llm(
                    case_id,
                    file_analyses,
                    doc_map,
                    failed_doc_ids,
                    case_coverage,
                    status,
                    llm_result,
                    model,
                    provider,
                    valid_compound_refs,
                    doc_id_to_label=doc_id_to_label,
                )
                ca_direct.meta["doc_registry"] = doc_registry
                return ca_direct
            except Exception as e:
                logger.warning(f"Validation failed for direct case analysis: {e}")
                unc = f"Validation failed during case synthesis: {e}"
                if failed_uncertainties:
                    unc = f"{unc} | {'; '.join(failed_uncertainties)}"
                return CaseAnalysis(
                    case_id=case_id,
                    document_ids=[fa.document_id for fa in file_analyses],
                    document_count=len(file_analyses),
                    documents=[],
                    analyzed_document_ids=[fa.document_id for fa in successful_files],
                    failed_document_ids=failed_doc_ids,
                    case_coverage=case_coverage,
                    status="partial",
                    confidence=0.0,
                    uncertainty=unc,
                    meta={"error": str(e), "doc_registry": doc_registry},
                    model=model,
                    provider=provider,
                )

    # 2. Hierarchical processing for large cases
    sorted_successful = sorted(
        successful_files,
        key=lambda fa: (_doc_type_priority(fa.document_type), fa.filename, fa.document_id),
    )

    def _prompt_for_file_batch(batch_files: list[FileAnalysis]) -> str:
        b_map = {doc_id_to_label[fa.document_id]: fa for fa in batch_files}
        return build_case_prompt(case_id, batch_files, b_map, [], 1.0)

    leaf_batches = _partition_files(sorted_successful, max_files, max_tokens, _prompt_for_file_batch)
    intermediate_cases: list[CaseAnalysis] = []

    for cluster_idx, batch in enumerate(leaf_batches, start=1):
        b_map = {doc_id_to_label[fa.document_id]: fa for fa in batch}
        b_doc_to_label = {fa.document_id: doc_id_to_label[fa.document_id] for fa in batch}
        b_valid_refs = {r for r in valid_compound_refs if r.split(":")[0] in b_map}
        cluster_prompt = build_case_prompt(case_id, batch, b_map, [], 1.0)
        try:
            provider_fn = get_llm_provider(provider, model)
            raw_res = provider_fn([cluster_prompt])
            if isinstance(raw_res, dict):
                llm_result = raw_res
            elif isinstance(raw_res, list) and raw_res:
                llm_result = raw_res[0] if isinstance(raw_res[0], dict) else {}
            else:
                llm_result = {}

            ca_cluster = _build_case_analysis_from_llm(
                f"{case_id}-cluster-{cluster_idx}",
                batch,
                b_map,
                [],
                1.0,
                "complete",
                llm_result,
                model,
                provider,
                b_valid_refs,
                doc_id_to_label=b_doc_to_label,
            )
            ca_cluster.meta["cluster_orig_refs"] = list(b_valid_refs)
            intermediate_cases.append(ca_cluster)

        except Exception as e:
            logger.warning(f"Cluster synthesis call failed: {e}")
            ca_failed = CaseAnalysis(
                case_id=f"{case_id}-cluster-{cluster_idx}",
                document_ids=[fa.document_id for fa in batch],
                document_count=len(batch),
                documents=[],
                analyzed_document_ids=[],
                failed_document_ids=[fa.document_id for fa in batch],
                case_coverage=0.0,
                status="failed",
                confidence=0.0,
                uncertainty=f"Cluster provider failed: {e}",
                meta={"error": str(e), "cluster_orig_refs": list(b_valid_refs)},
                model=model,
                provider=provider,
            )
            intermediate_cases.append(ca_failed)

    # Hierarchical consolidation of clusters
    current_level = intermediate_cases
    level = 1

    while len(current_level) > 1:
        next_level: list[CaseAnalysis] = []
        cluster_fas = [
            _case_analysis_to_file_analysis(ca, f"cluster-{idx+1}", f"Cluster_{idx+1}.pdf")
            for idx, ca in enumerate(current_level)
        ]

        def _prompt_for_cluster_batch(batch_fas: list[FileAnalysis]) -> str:
            b_map, _, _ = _create_doc_map(batch_fas)
            return build_case_prompt(case_id, batch_fas, b_map, [], 1.0)

        intermediate_batches = _partition_files(cluster_fas, max_files, max_tokens, _prompt_for_cluster_batch)

        for batch_fas in intermediate_batches:
            b_map, b_doc_to_label, _ = _create_doc_map(batch_fas)
            cluster_ref_map: dict[str, list[str]] = {}
            valid_refs_set: set[str] = set()

            ca_by_cluster_id = {f"cluster-{idx+1}": ca for idx, ca in enumerate(current_level)}
            for idx, fa in enumerate(batch_fas):
                c_label = b_doc_to_label.get(fa.document_id, f"DOC-{idx+1:03d}")
                matched_ca = ca_by_cluster_id.get(fa.document_id)
                orig_refs = matched_ca.meta.get("cluster_orig_refs", []) if matched_ca else []
                cluster_ref_map[c_label] = orig_refs
                valid_refs_set.add(c_label)
                for r in orig_refs:
                    valid_refs_set.add(r)


            cluster_prompt = build_case_prompt(case_id, batch_fas, b_map, [], 1.0)

            try:
                provider_fn = get_llm_provider(provider, model)
                raw_res = provider_fn([cluster_prompt])
                if isinstance(raw_res, dict):
                    llm_result = raw_res
                elif isinstance(raw_res, list) and raw_res:
                    llm_result = raw_res[0] if isinstance(raw_res[0], dict) else {}
                else:
                    llm_result = {}

                ca_consolidated = _build_case_analysis_from_llm(
                    case_id,
                    batch_fas,
                    b_map,
                    [],
                    1.0,
                    status,
                    llm_result,
                    model,
                    provider,
                    valid_refs_set,
                    cluster_ref_map=cluster_ref_map,
                    doc_id_to_label=b_doc_to_label,
                )
                ca_consolidated.meta["hierarchical_level"] = level
                batch_all_orig = []
                for r_list in cluster_ref_map.values():
                    batch_all_orig.extend(r_list)
                ca_consolidated.meta["cluster_orig_refs"] = list(dict.fromkeys(batch_all_orig))
                next_level.append(ca_consolidated)
            except Exception as e:
                logger.warning(f"Hierarchical case consolidation failed: {e}")
                ca_err = CaseAnalysis(
                    case_id=case_id,
                    document_ids=[fa.document_id for fa in batch_fas],
                    document_count=len(batch_fas),
                    documents=[],
                    analyzed_document_ids=[],
                    failed_document_ids=[fa.document_id for fa in batch_fas],
                    case_coverage=0.0,
                    status="partial",
                    confidence=0.0,
                    uncertainty=f"Hierarchical provider failed: {e}",
                    meta={"error": str(e)},
                    model=model,
                    provider=provider,
                )
                next_level.append(ca_err)

        current_level = next_level
        level += 1

    final_case = current_level[0]
    final_case.case_id = case_id
    final_case.document_ids = [fa.document_id for fa in file_analyses]
    final_case.document_count = len(file_analyses)
    final_case.documents = [
        {
            "doc_id": fa.document_id,
            "filename": fa.filename,
            "type": fa.document_type or "unknown",
            "coverage": fa.coverage,
            "status": fa.status,
        }
        for fa in file_analyses
    ]
    final_case.analyzed_document_ids = [fa.document_id for fa in successful_files]
    final_case.failed_document_ids = failed_doc_ids
    final_case.case_coverage = case_coverage
    final_case.status = status
    final_case.confidence = case_coverage if status != "failed" else 0.0

    if not final_case.timeline:
        final_case.timeline = _merge_case_timeline(successful_files, doc_id_to_label)

    final_case.meta["doc_registry"] = doc_registry
    return final_case
