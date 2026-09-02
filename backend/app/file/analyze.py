"""File-level consolidation — ChunkAnalysis → FileAnalysis, hierarchical, source-aware."""

import asyncio
import logging
import uuid
from collections import defaultdict
from datetime import datetime
from typing import Callable

from backend.app.chunking.chunk import Chunk
from backend.app.chunking.tokenizer import count_tokens
from backend.app.config import get_settings
from backend.app.file.models import AnalysisItem, FileAnalysis
from backend.app.file.prompts import FILE_ANALYSIS_RESPONSE_SCHEMA, build_file_prompt
from backend.app.llm.models import ChunkAnalysis
from backend.app.llm.provider import get_llm_provider

logger = logging.getLogger(__name__)


def _is_failed(ca: ChunkAnalysis) -> bool:
    return ca.confidence == 0.0 and ca.uncertainty is not None and "failed" in ca.uncertainty.lower()


def _create_src_map(analyses: list[ChunkAnalysis], start_idx: int = 1) -> tuple[dict[str, ChunkAnalysis], dict[str, str]]:
    sorted_analyses = sorted(analyses, key=lambda ca: (ca.page_start, ca.chunk_id))
    src_map: dict[str, ChunkAnalysis] = {}
    chunk_to_src: dict[str, str] = {}
    for idx, ca in enumerate(sorted_analyses, start=start_idx):
        src_id = f"SRC-{idx:03d}"
        src_map[src_id] = ca
        chunk_to_src[ca.chunk_id] = src_id
    return src_map, chunk_to_src


def _validate_source_refs(
    items,
    valid_srcs: set[str],
    meta_invalid: list[str],
    meta_empty: list[str],
    uncertainty_parts: list[str],
    shard_ref_map: dict[str, list[str]] | None = None,
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
            if shard_ref_map and r in shard_ref_map:
                resolved_valid.extend(shard_ref_map[r])
            elif r in valid_srcs:
                resolved_valid.append(r)
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


def _build_file_analysis_from_llm(
    document_id: str,
    filename: str,
    chunk_ids: list[str],
    pages: list[int],
    analyzed_ids: list[str],
    failed_ids: list[str],
    coverage: float,
    status: str,
    src_map: dict[str, ChunkAnalysis],
    llm_result: dict,
    model: str,
    provider: str,
    valid_srcs: set[str],
    shard_ref_map: dict[str, list[str]] | None = None,
) -> FileAnalysis:
    meta_invalid: list[str] = []
    meta_empty: list[str] = []
    uncertainty_parts: list[str] = []

    doc_type = llm_result.get("document_type")
    allowed_types = {"petition", "reply", "affidavit", "evidence", "order", "judgment", "annexure", "unknown"}
    if doc_type not in allowed_types:
        doc_type = "unknown"

    semantic_fields = [
        "facts", "procedural_events", "issues", "arguments", "counterarguments",
        "evidence", "legal_provisions", "court_observations", "court_reasoning",
        "findings", "decisions", "important_dates",
    ]

    validated_data: dict = {}
    has_malformed = False

    for field in semantic_fields:
        original = llm_result.get(field)
        if original is not None and not isinstance(original, list):
            has_malformed = True
            meta_invalid.append(f"malformed_{field}")
            uncertainty_parts.append(f"Malformed field {field} type {type(original).__name__} — expected list")

        validated = _validate_source_refs(
            original,
            valid_srcs,
            meta_invalid,
            meta_empty,
            uncertainty_parts,
            shard_ref_map=shard_ref_map,
        )
        validated_data[field] = validated
        if original is not None and not isinstance(original, list) and validated is None:
            has_malformed = True

    # Chronological ordering for important_dates
    if validated_data.get("important_dates"):
        try:
            def _parse_date(s: str):
                for fmt in ("%d %B %Y", "%d %b %Y", "%d/%m/%Y", "%d-%m-%Y", "%Y-%m-%d", "%d.%m.%Y"):
                    try:
                        return datetime.strptime(s.strip(), fmt)
                    except Exception:
                        continue
                return None

            dates = validated_data["important_dates"]
            parseable = []
            unparseable = []
            for item in dates:
                dt = _parse_date(item["text"])
                if dt:
                    parseable.append((dt, item))
                else:
                    unparseable.append(item)
            parseable.sort(key=lambda x: x[0])
            validated_data["important_dates"] = [it for _, it in parseable] + unparseable
        except Exception:
            pass

    llm_uncertainty = llm_result.get("uncertainty")
    if has_malformed:
        malformed_msg = "Malformed provider output detected"
        if llm_uncertainty:
            llm_uncertainty = f"{llm_uncertainty} | {malformed_msg}"
        else:
            llm_uncertainty = malformed_msg

    if uncertainty_parts:
        prov_warning = "; ".join(uncertainty_parts[:3])
        if llm_uncertainty:
            llm_uncertainty = f"{llm_uncertainty} | Provenance issues: {prov_warning}"
        else:
            llm_uncertainty = f"Provenance issues: {prov_warning}"

    final_status = status
    if has_malformed and status == "complete":
        final_status = "partial"

    return FileAnalysis(
        document_id=document_id,
        filename=filename,
        chunk_ids=chunk_ids,
        chunk_count=len(chunk_ids),
        pages=sorted(pages),
        page_start=min(pages) if pages else 0,
        page_end=max(pages) if pages else 0,
        analyzed_chunk_ids=analyzed_ids,
        failed_chunk_ids=failed_ids,
        coverage=coverage,
        status=final_status,
        document_type=doc_type,
        facts=validated_data.get("facts"),
        procedural_events=validated_data.get("procedural_events"),
        issues=validated_data.get("issues"),
        arguments=validated_data.get("arguments"),
        counterarguments=validated_data.get("counterarguments"),
        evidence=validated_data.get("evidence"),
        legal_provisions=validated_data.get("legal_provisions"),
        court_observations=validated_data.get("court_observations"),
        court_reasoning=validated_data.get("court_reasoning"),
        findings=validated_data.get("findings"),
        decisions=validated_data.get("decisions"),
        important_dates=validated_data.get("important_dates"),
        uncertainty=llm_uncertainty,
        meta={
            "invalid_source_refs": list(set(meta_invalid))[:10],
            "empty_source_refs": meta_empty[:5],
            "chunk_count": len(chunk_ids),
        },
        model=model,
        provider=provider,
    )


def _file_analysis_to_chunk_analysis(fa: FileAnalysis, src_id: str) -> ChunkAnalysis:
    text_parts = []
    for field in [
        "facts", "procedural_events", "issues", "arguments", "counterarguments",
        "evidence", "legal_provisions", "court_observations", "court_reasoning",
        "findings", "decisions", "important_dates",
    ]:
        items = getattr(fa, field, None)
        if items:
            field_texts = [
                it.text if isinstance(it, AnalysisItem) else (it.get("text", "") if isinstance(it, dict) else str(it))
                for it in items
            ]
            if field_texts:
                text_parts.append(f"{field}: {'; '.join(field_texts[:3])}")

    text = "\n".join(text_parts)[:1000] or "Shard summary"
    return ChunkAnalysis(
        chunk_id=fa.chunk_ids[0] if fa.chunk_ids else str(uuid.uuid4()),
        document_id=fa.document_id,
        filename=fa.filename,
        page_start=fa.page_start,
        page_end=fa.page_end,
        pages=fa.pages,
        facts=[text] if text else None,
        uncertainty=fa.uncertainty,
        confidence=1.0 if fa.status == "complete" else 0.5,
        model=fa.model,
        provider=fa.provider,
    )


def _partition_chunks(
    items: list[ChunkAnalysis],
    max_per_prompt: int,
    max_tokens: int,
    build_prompt_fn: Callable[[list[ChunkAnalysis]], str],
) -> list[list[ChunkAnalysis]]:
    initial_batches = [items[i : i + max_per_prompt] for i in range(0, len(items), max_per_prompt)]
    final_batches: list[list[ChunkAnalysis]] = []

    for batch in initial_batches:
        if len(batch) <= 1:
            final_batches.append(batch)
            continue
        prompt = build_prompt_fn(batch)
        if count_tokens(prompt) <= max_tokens:
            final_batches.append(batch)
        else:
            mid = len(batch) // 2
            left = _partition_chunks(batch[:mid], max_per_prompt, max_tokens, build_prompt_fn)
            right = _partition_chunks(batch[mid:], max_per_prompt, max_tokens, build_prompt_fn)
            final_batches.extend(left)
            final_batches.extend(right)

    return final_batches


def _partition_intermediate(
    items: list[FileAnalysis],
    max_per_prompt: int,
    max_tokens: int,
    build_prompt_fn: Callable[[list[FileAnalysis]], str],
) -> list[list[FileAnalysis]]:
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
            left = _partition_intermediate(batch[:mid], max_per_prompt, max_tokens, build_prompt_fn)
            right = _partition_intermediate(batch[mid:], max_per_prompt, max_tokens, build_prompt_fn)
            final_batches.extend(left)
            final_batches.extend(right)

    return final_batches


def analyze_file(
    document_id: str,
    chunks: list[Chunk],
    analyses: list[ChunkAnalysis],
) -> FileAnalysis:
    """Analyze a single document's chunks/analyses into FileAnalysis."""
    logger.debug(f"[analyze_file] start doc={document_id} chunks={len(chunks)} analyses={len(analyses)}")
    settings = get_settings()
    model = settings.llm_model
    provider = settings.llm_provider
    max_per_prompt = settings.file_max_chunks_per_prompt
    max_tokens = settings.file_max_tokens

    doc_chunks = [c for c in chunks if c.document_id == document_id]
    doc_analyses = [a for a in analyses if a.document_id == document_id]

    chunk_ids = [c.chunk_id for c in doc_chunks]
    pages = sorted({p for c in doc_chunks for p in c.pages})

    if not doc_chunks:
        return FileAnalysis(
            document_id=document_id,
            filename="",
            chunk_ids=[],
            chunk_count=0,
            pages=[],
            page_start=0,
            page_end=0,
            analyzed_chunk_ids=[],
            failed_chunk_ids=[],
            coverage=0.0,
            status="failed",
            document_type="unknown",
            uncertainty="No chunks for document",
            meta={},
            model=model,
            provider=provider,
        )

    failed = [a for a in doc_analyses if _is_failed(a)]
    successful = [a for a in doc_analyses if not _is_failed(a)]
    failed_ids = [a.chunk_id for a in failed]
    analyzed_ids = [a.chunk_id for a in successful]
    total = len(doc_analyses) if doc_analyses else len(doc_chunks)

    if len(doc_analyses) != len(doc_chunks):
        total = len(doc_chunks)
        analyzed_chunk_ids_set = {a.chunk_id for a in successful}
        for c in doc_chunks:
            if c.chunk_id not in analyzed_chunk_ids_set and c.chunk_id not in failed_ids:
                failed_ids.append(c.chunk_id)

    coverage = len(analyzed_ids) / total if total > 0 else 0.0
    if coverage == 0.0:
        status = "failed"
    elif coverage == 1.0:
        status = "complete"
    else:
        status = "partial"

    if not successful:
        filename = doc_chunks[0].filename if doc_chunks else ""
        return FileAnalysis(
            document_id=document_id,
            filename=filename,
            chunk_ids=chunk_ids,
            chunk_count=len(chunk_ids),
            pages=pages,
            page_start=min(pages) if pages else 0,
            page_end=max(pages) if pages else 0,
            analyzed_chunk_ids=analyzed_ids,
            failed_chunk_ids=failed_ids,
            coverage=coverage,
            status=status,
            document_type="unknown",
            uncertainty="No chunks successfully analyzed" if status == "failed" else f"Incomplete coverage {coverage:.0%}",
            meta={"failed_chunk_ids": failed_ids},
            model=model,
            provider=provider,
        )

    global_src_map, global_chunk_to_src = _create_src_map(successful, start_idx=1)
    src_registry = {
        src_id: {
            "chunk_id": ca.chunk_id,
            "document_id": ca.document_id,
            "filename": ca.filename,
            "page_start": ca.page_start,
            "page_end": ca.page_end,
            "pages": ca.pages,
        }
        for src_id, ca in global_src_map.items()
    }

    # Direct single call if <= max_per_prompt and prompt fits within max_tokens
    if len(successful) <= max_per_prompt:
        direct_prompt = build_file_prompt(
            document_id, doc_chunks[0].filename, successful, global_src_map, failed_ids, coverage
        )
        if count_tokens(direct_prompt) <= max_tokens:
            try:
                llm_result = _call_llm_for_file(
                    document_id, doc_chunks[0].filename, global_src_map, failed_ids, coverage, model, provider
                )
            except Exception as e:
                logger.warning(f"Provider failed for direct file analysis: {e}")
                fa_prov_err = FileAnalysis(
                    document_id=document_id,
                    filename=doc_chunks[0].filename,
                    chunk_ids=chunk_ids,
                    chunk_count=len(chunk_ids),
                    pages=pages,
                    page_start=min(pages) if pages else 0,
                    page_end=max(pages) if pages else 0,
                    analyzed_chunk_ids=analyzed_ids,
                    failed_chunk_ids=failed_ids,
                    coverage=coverage,
                    status="failed" if coverage == 0 else "partial",
                    document_type="unknown",
                    uncertainty=f"provider failed: {e}",
                    meta={"error": str(e), "src_registry": src_registry},
                    model=model,
                    provider=provider,
                )
                return fa_prov_err

            try:
                fa_res = _build_file_analysis_from_llm(
                    document_id,
                    doc_chunks[0].filename,
                    chunk_ids,
                    pages,
                    analyzed_ids,
                    failed_ids,
                    coverage,
                    status,
                    global_src_map,
                    llm_result,
                    model,
                    provider,
                    set(global_src_map.keys()),
                )
                fa_res.meta["src_registry"] = src_registry
                return fa_res
            except Exception as e:
                logger.warning(f"Validation failed for direct file analysis: {e}")
                fa_val_err = FileAnalysis(
                    document_id=document_id,
                    filename=doc_chunks[0].filename,
                    chunk_ids=chunk_ids,
                    chunk_count=len(chunk_ids),
                    pages=pages,
                    page_start=min(pages) if pages else 0,
                    page_end=max(pages) if pages else 0,
                    analyzed_chunk_ids=analyzed_ids,
                    failed_chunk_ids=failed_ids,
                    coverage=coverage,
                    status="partial",
                    document_type="unknown",
                    uncertainty=f"validation failed: {e}",
                    meta={"error": str(e), "src_registry": src_registry},
                    model=model,
                    provider=provider,
                )
                return fa_val_err


    # Hierarchical sharding
    sorted_successful = sorted(successful, key=lambda ca: (ca.page_start, ca.chunk_id))

    def _prompt_for_chunks(batch_chunks: list[ChunkAnalysis]) -> str:
        batch_src_map = {global_chunk_to_src[c.chunk_id]: c for c in batch_chunks}
        return build_file_prompt(document_id, doc_chunks[0].filename, batch_chunks, batch_src_map, [], 1.0)

    leaf_batches = _partition_chunks(sorted_successful, max_per_prompt, max_tokens, _prompt_for_chunks)
    intermediate_analyses: list[FileAnalysis] = []

    for shard in leaf_batches:
        shard_src_map = {global_chunk_to_src[c.chunk_id]: c for c in shard}
        shard_chunk_ids = [c.chunk_id for c in shard]
        shard_pages = sorted({p for c in shard for p in c.pages})
        shard_orig_srcs = list(shard_src_map.keys())

        try:
            llm_result = _call_llm_for_file(document_id, doc_chunks[0].filename, shard_src_map, [], 1.0, model, provider)
            fa_shard = _build_file_analysis_from_llm(
                document_id,
                doc_chunks[0].filename,
                shard_chunk_ids,
                shard_pages,
                shard_chunk_ids,
                [],
                1.0,
                "complete",
                shard_src_map,
                llm_result,
                model,
                provider,
                set(shard_src_map.keys()),
            )
            fa_shard.meta["orig_src_ids"] = shard_orig_srcs
            intermediate_analyses.append(fa_shard)
        except Exception as e:
            logger.warning(f"Shard analysis failed: {e}")
            intermediate_analyses.append(
                FileAnalysis(
                    document_id=document_id,
                    filename=doc_chunks[0].filename,
                    chunk_ids=shard_chunk_ids,
                    chunk_count=len(shard_chunk_ids),
                    pages=shard_pages,
                    page_start=min(shard_pages) if shard_pages else 0,
                    page_end=max(shard_pages) if shard_pages else 0,
                    analyzed_chunk_ids=[],
                    failed_chunk_ids=shard_chunk_ids,
                    coverage=0.0,
                    status="failed",
                    document_type="unknown",
                    uncertainty=f"shard provider failed: {e}",
                    meta={"error": str(e), "orig_src_ids": shard_orig_srcs},
                    model=model,
                    provider=provider,
                )
            )

    # Level 1+ Hierarchical consolidation
    current_level = intermediate_analyses
    level = 1

    while len(current_level) > 1:
        next_level: list[FileAnalysis] = []

        def _prompt_for_intermediate(batch_fas: list[FileAnalysis]) -> str:
            batch_src_map = {
                f"SHARD-SRC-{idx+1:03d}": _file_analysis_to_chunk_analysis(fa, f"SHARD-SRC-{idx+1:03d}")
                for idx, fa in enumerate(batch_fas)
            }
            return build_file_prompt(
                document_id, doc_chunks[0].filename, list(batch_src_map.values()), batch_src_map, [], 1.0
            )

        intermediate_batches = _partition_intermediate(
            current_level, max_per_prompt, max_tokens, _prompt_for_intermediate
        )

        for batch in intermediate_batches:
            batch_src_map: dict[str, ChunkAnalysis] = {}
            shard_ref_map: dict[str, list[str]] = {}
            valid_srcs_set: set[str] = set()

            for idx, fa in enumerate(batch):
                s_id = f"SHARD-SRC-{idx+1:03d}"
                batch_src_map[s_id] = _file_analysis_to_chunk_analysis(fa, s_id)
                valid_srcs_set.add(s_id)

                fa_refs = []
                for field in [
                    "facts", "procedural_events", "issues", "arguments", "counterarguments",
                    "evidence", "legal_provisions", "court_observations", "court_reasoning",
                    "findings", "decisions", "important_dates",
                ]:
                    items = getattr(fa, field, None)
                    if items:
                        for it in items:
                            refs = (
                                it.source_refs
                                if isinstance(it, AnalysisItem)
                                else (it.get("source_refs", []) if isinstance(it, dict) else [])
                            )
                            fa_refs.extend(refs)

                if not fa_refs and "orig_src_ids" in fa.meta:
                    fa_refs = fa.meta["orig_src_ids"]

                unique_fa_refs = list(dict.fromkeys(fa_refs))
                shard_ref_map[s_id] = unique_fa_refs
                for r in unique_fa_refs:
                    valid_srcs_set.add(r)
                if "orig_src_ids" in fa.meta:
                    for r in fa.meta["orig_src_ids"]:
                        valid_srcs_set.add(r)

            batch_chunk_ids = [cid for fa in batch for cid in fa.chunk_ids]
            batch_pages = sorted({p for fa in batch for p in fa.pages})
            batch_analyzed_ids = [cid for fa in batch for cid in fa.analyzed_chunk_ids]
            batch_failed_ids = [cid for fa in batch for cid in fa.failed_chunk_ids]
            batch_coverage = len(batch_analyzed_ids) / len(batch_chunk_ids) if batch_chunk_ids else 0.0

            try:
                llm_result = _call_llm_for_file(
                    document_id, doc_chunks[0].filename, batch_src_map, [], 1.0, model, provider
                )
                fa_consolidated = _build_file_analysis_from_llm(
                    document_id,
                    doc_chunks[0].filename,
                    batch_chunk_ids,
                    batch_pages,
                    batch_analyzed_ids,
                    batch_failed_ids,
                    batch_coverage,
                    status if len(batch) == len(current_level) else ("complete" if batch_coverage == 1.0 else "partial"),
                    batch_src_map,
                    llm_result,
                    model,
                    provider,
                    valid_srcs_set,
                    shard_ref_map=shard_ref_map,
                )
                fa_consolidated.meta["hierarchical_level"] = level
                batch_all_orig_srcs = []
                for s_id, r_list in shard_ref_map.items():
                    batch_all_orig_srcs.extend(r_list)
                fa_consolidated.meta["orig_src_ids"] = list(dict.fromkeys(batch_all_orig_srcs))
                next_level.append(fa_consolidated)
            except Exception as e:
                logger.warning(f"Hierarchical consolidation call failed: {e}")
                fa_err = FileAnalysis(
                    document_id=document_id,
                    filename=doc_chunks[0].filename,
                    chunk_ids=batch_chunk_ids,
                    chunk_count=len(batch_chunk_ids),
                    pages=batch_pages,
                    page_start=min(batch_pages) if batch_pages else 0,
                    page_end=max(batch_pages) if batch_pages else 0,
                    analyzed_chunk_ids=batch_analyzed_ids,
                    failed_chunk_ids=batch_failed_ids,
                    coverage=batch_coverage,
                    status="partial",
                    document_type="unknown",
                    uncertainty=f"hierarchical provider failed: {e}",
                    meta={"error": str(e)},
                    model=model,
                    provider=provider,
                )
                next_level.append(fa_err)

        current_level = next_level
        level += 1

    final = current_level[0]
    final.chunk_ids = chunk_ids
    final.chunk_count = len(chunk_ids)
    final.pages = pages
    final.page_start = min(pages) if pages else 0
    final.page_end = max(pages) if pages else 0
    final.analyzed_chunk_ids = analyzed_ids
    final.failed_chunk_ids = failed_ids
    final.coverage = coverage
    final.status = status
    final.meta["src_registry"] = src_registry
    return final



def analyze_files(
    chunks: list[Chunk],
    analyses: list[ChunkAnalysis],
) -> list[FileAnalysis]:
    """Group by document_id and analyze each file."""
    if not chunks and not analyses:
        return []
    if not chunks:
        doc_ids = {a.document_id for a in analyses}
    else:
        doc_ids = {c.document_id for c in chunks}

    chunks_by_doc: dict[str, list[Chunk]] = defaultdict(list)
    for c in chunks:
        chunks_by_doc[c.document_id].append(c)
    analyses_by_doc: dict[str, list[ChunkAnalysis]] = defaultdict(list)
    for a in analyses:
        analyses_by_doc[a.document_id].append(a)

    results: list[FileAnalysis] = []

    for doc_id in sorted(doc_ids):
        doc_chunks = chunks_by_doc.get(doc_id, [])
        doc_analyses = analyses_by_doc.get(doc_id, [])
        if not doc_chunks and doc_analyses:
            doc_chunks = [
                Chunk(
                    chunk_id=a.chunk_id,
                    document_id=a.document_id,
                    filename=a.filename,
                    chunk_index=0,
                    page_start=a.page_start,
                    page_end=a.page_end,
                    pages=a.pages,
                    text="",
                    token_count=0,
                    evidence_ids=[],
                    evidence_score=0.0,
                    evidence_count=0,
                    section=None,
                    meta={},
                )
                for a in doc_analyses
            ]
        fa = analyze_file(doc_id, doc_chunks, doc_analyses)
        results.append(fa)

    results.sort(key=lambda fa: fa.document_id)
    return results


def _call_llm_for_file(
    document_id: str,
    filename: str,
    src_map: dict[str, ChunkAnalysis],
    failed_ids: list[str],
    coverage: float,
    model: str,
    provider: str,
) -> dict:
    prompt = build_file_prompt(document_id, filename, list(src_map.values()), src_map, failed_ids, coverage)
    provider_fn = get_llm_provider(provider, model)
    raw_list = provider_fn([prompt])
    if not raw_list:
        raise ValueError("Provider returned empty list")
    return raw_list[0]
