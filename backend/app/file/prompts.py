"""File-level consolidation prompts — uses ChunkAnalysis summaries, not raw PDFs."""

from backend.app.file.models import FileAnalysis


FILE_SYSTEM_PROMPT = """You are a legal assistant consolidating chunk-level analyses of a single legal document.
You will receive multiple ChunkAnalysis summaries, each with a source ID (SRC-001, SRC-002, ...).
Your task is to consolidate information belonging to this single document into a coherent file-level summary.

Rules:
- Analyze ONLY the supplied ChunkAnalysis information for this single document.
- Preserve distinctions: facts vs allegations vs arguments vs court findings vs evidence vs conclusions.
- Do NOT merge legally distinct statements merely because they are similar.
- Preserve conflicting statements rather than arbitrarily resolving them; note conflicts in uncertainty if needed.
- Do NOT invent facts, dates, provisions, or entities not present in the chunk analyses.
- Preserve important legal terminology, names, dates and provisions verbatim where possible.
- Identify uncertainty when context is insufficient for a category — omit the field rather than writing "N/A".
- Never invent document IDs, chunk IDs, page numbers, or source IDs. Use only supplied SRC-* IDs.
- For each extracted statement, provide source_refs as a subset of the supplied SRC IDs that support that statement.
- If no valid source remains for a statement, exclude the statement and note the provenance problem in uncertainty.
- Identify document type from content (choose one: petition, reply, affidavit, evidence, order, judgment, annexure, unknown). Filename is hint only; mixed/unclear → unknown.
- Preserve chronological/page order where given; do not invent dates.
- Return structured JSON matching the requested schema (FileAnalysis). Do not include chunk-level provenance fields beyond source_refs.
"""

# FileAnalysis JSON schema for structured output (used by Gemini)
FILE_ANALYSIS_RESPONSE_SCHEMA = {
    "type": "object",
    "properties": {
        "document_type": {"type": "string", "enum": ["petition", "reply", "affidavit", "evidence", "order", "judgment", "annexure", "unknown"]},
        "facts": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "text": {"type": "string"},
                    "source_refs": {"type": "array", "items": {"type": "string"}},
                },
                "required": ["text", "source_refs"],
            },
        },
        "procedural_events": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "text": {"type": "string"},
                    "source_refs": {"type": "array", "items": {"type": "string"}},
                },
                "required": ["text", "source_refs"],
            },
        },
        "issues": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "text": {"type": "string"},
                    "source_refs": {"type": "array", "items": {"type": "string"}},
                },
                "required": ["text", "source_refs"],
            },
        },
        "arguments": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "text": {"type": "string"},
                    "source_refs": {"type": "array", "items": {"type": "string"}},
                },
                "required": ["text", "source_refs"],
            },
        },
        "counterarguments": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "text": {"type": "string"},
                    "source_refs": {"type": "array", "items": {"type": "string"}},
                },
                "required": ["text", "source_refs"],
            },
        },
        "evidence": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "text": {"type": "string"},
                    "source_refs": {"type": "array", "items": {"type": "string"}},
                },
                "required": ["text", "source_refs"],
            },
        },
        "legal_provisions": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "text": {"type": "string"},
                    "source_refs": {"type": "array", "items": {"type": "string"}},
                },
                "required": ["text", "source_refs"],
            },
        },
        "court_observations": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "text": {"type": "string"},
                    "source_refs": {"type": "array", "items": {"type": "string"}},
                },
                "required": ["text", "source_refs"],
            },
        },
        "court_reasoning": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "text": {"type": "string"},
                    "source_refs": {"type": "array", "items": {"type": "string"}},
                },
                "required": ["text", "source_refs"],
            },
        },
        "findings": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "text": {"type": "string"},
                    "source_refs": {"type": "array", "items": {"type": "string"}},
                },
                "required": ["text", "source_refs"],
            },
        },
        "decisions": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "text": {"type": "string"},
                    "source_refs": {"type": "array", "items": {"type": "string"}},
                },
                "required": ["text", "source_refs"],
            },
        },
        "important_dates": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "text": {"type": "string"},
                    "source_refs": {"type": "array", "items": {"type": "string"}},
                },
                "required": ["text", "source_refs"],
            },
        },
        "uncertainty": {"type": "string"},
    },
}


def _compact_chunk_analysis(ca, src_id: str) -> str:
    # Compact JSON snippet per ChunkAnalysis for prompt
    parts = [f"{src_id} (chunk {ca.chunk_id[:8]} p.{ca.page_start}-{ca.page_end}):"]
    # Include only non-None semantic fields, truncated to 300 chars each
    for field in [
        "facts",
        "procedural_events",
        "issues",
        "arguments",
        "counterarguments",
        "evidence_mentioned",
        "legal_provisions",
        "court_observations",
        "court_reasoning",
        "decisions",
        "important_dates",
        "entities",
    ]:
        val = getattr(ca, field, None)
        if val:
            # Join list, truncate
            text = "; ".join(str(x)[:200] for x in val[:3])
            parts.append(f"  {field}: {text[:300]}")
    if ca.uncertainty:
        parts.append(f"  uncertainty: {ca.uncertainty[:200]}")
    return "\n".join(parts)


def build_file_prompt(
    document_id: str,
    filename: str,
    chunk_analyses: list,
    src_map: dict[str, any],
    failed_ids: list[str],
    coverage: float,
) -> str:
    lines = [FILE_SYSTEM_PROMPT]
    lines.append(f"Document: {filename} (document_id={document_id})")
    lines.append(f"Coverage: {coverage:.0%} ({len(chunk_analyses)}/{len(chunk_analyses)+len(failed_ids)} chunks successful)")
    if failed_ids:
        lines.append(f"Failed chunks (excluded): {', '.join(failed_ids[:5])}")
    lines.append("\nSources (use only these SRC IDs):")
    for src_id, ca in src_map.items():
        lines.append(f"{src_id} → chunk {ca.chunk_id[:8]} p.{ca.page_start}-{ca.page_end} document_id={ca.document_id}")

    lines.append("\nChunkAnalyses to consolidate:")
    for src_id, ca in src_map.items():
        lines.append(_compact_chunk_analysis(ca, src_id))
        lines.append("")

    lines.append("Instruction: Consolidate the above ChunkAnalyses into a single FileAnalysis JSON. For each fact/issue/decision, provide source_refs as subset of the SRC IDs above. Do not invent new SRC IDs.")
    return "\n".join(lines)
