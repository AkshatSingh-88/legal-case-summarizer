"""Case-level prompts and response schema for legal case synthesis."""

from backend.app.file.models import AnalysisItem, FileAnalysis

CASE_SYSTEM_PROMPT = """You are an expert legal case analysis system.
Your task is to synthesize multiple structured FileAnalysis documents belonging to a legal case into a single, unified CaseAnalysis.

CRITICAL INSTRUCTIONS:
1. Cross-File Interconnection: Connect claims, defenses, counterarguments, supporting/contradicting evidence, legal provisions, court reasoning, findings, and decisions across documents.
2. Neutrality: Different documents may make opposing or conflicting factual assertions (e.g., Petition asserts X, Reply asserts not-X). Retain neutrality. Mark contested matters as status 'disputed' or relationship 'contradiction'. Do not decide which party is correct unless an order or judgment explicitly makes a judicial finding or decision on that point.
3. Distinction: Carefully distinguish party assertions (claims, arguments) from judicial determinations (court observations, reasoning, findings, decisions, final orders).
4. Do Not Fabricate: Do not invent facts, parties, dates, or citations not grounded in the supplied FileAnalyses. Missing sections should remain null.
5. Strict Provenance: For each synthesized statement or relationship, provide source_refs as a subset of the valid compound source identifiers supplied below (e.g. DOC-001:SRC-001). Do NOT invent new source references.
6. JSON Schema: You must respond with valid JSON matching the exact schema provided."""

CASE_ANALYSIS_RESPONSE_SCHEMA = {
    "type": "object",
    "properties": {
        "case_summary": {"type": ["string", "null"]},
        "parties": {
            "type": ["array", "null"],
            "items": {"type": "string"},
        },
        "overall_facts": {
            "type": ["array", "null"],
            "items": {
                "type": "object",
                "properties": {
                    "text": {"type": "string"},
                    "source_refs": {"type": "array", "items": {"type": "string"}},
                },
                "required": ["text", "source_refs"],
            },
        },
        "procedural_history": {
            "type": ["array", "null"],
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
            "type": ["array", "null"],
            "items": {
                "type": "object",
                "properties": {
                    "text": {"type": "string"},
                    "source_refs": {"type": "array", "items": {"type": "string"}},
                },
                "required": ["text", "source_refs"],
            },
        },
        "claims_and_defenses": {
            "type": ["array", "null"],
            "items": {
                "type": "object",
                "properties": {
                    "relationship_id": {"type": "string"},
                    "relationship_type": {"type": "string"},
                    "source_document_id": {"type": "string"},
                    "source_item": {"type": "string"},
                    "target_document_id": {"type": ["string", "null"]},
                    "target_item": {"type": ["string", "null"]},
                    "status": {"type": "string"},
                    "source_refs": {"type": "array", "items": {"type": "string"}},
                    "notes": {"type": ["string", "null"]},
                },
                "required": ["relationship_id", "relationship_type", "source_document_id", "source_item", "status", "source_refs"],
            },
        },
        "disputed_matters": {
            "type": ["array", "null"],
            "items": {
                "type": "object",
                "properties": {
                    "text": {"type": "string"},
                    "source_refs": {"type": "array", "items": {"type": "string"}},
                },
                "required": ["text", "source_refs"],
            },
        },
        "undisputed_facts": {
            "type": ["array", "null"],
            "items": {
                "type": "object",
                "properties": {
                    "text": {"type": "string"},
                    "source_refs": {"type": "array", "items": {"type": "string"}},
                },
                "required": ["text", "source_refs"],
            },
        },
        "evidence_summary": {
            "type": ["array", "null"],
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
            "type": ["array", "null"],
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
            "type": ["array", "null"],
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
            "type": ["array", "null"],
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
            "type": ["array", "null"],
            "items": {
                "type": "object",
                "properties": {
                    "text": {"type": "string"},
                    "source_refs": {"type": "array", "items": {"type": "string"}},
                },
                "required": ["text", "source_refs"],
            },
        },
        "final_disposition": {"type": ["string", "null"]},
        "cross_file_relationships": {
            "type": ["array", "null"],
            "items": {
                "type": "object",
                "properties": {
                    "relationship_id": {"type": "string"},
                    "relationship_type": {"type": "string"},
                    "source_document_id": {"type": "string"},
                    "source_item": {"type": "string"},
                    "target_document_id": {"type": ["string", "null"]},
                    "target_item": {"type": ["string", "null"]},
                    "status": {"type": "string"},
                    "source_refs": {"type": "array", "items": {"type": "string"}},
                    "notes": {"type": ["string", "null"]},
                },
                "required": ["relationship_id", "relationship_type", "source_document_id", "source_item", "status", "source_refs"],
            },
        },
        "uncertainty": {"type": ["string", "null"]},
    },
}


def _compact_file_analysis(fa: FileAnalysis, doc_id_label: str) -> str:
    """Format a FileAnalysis into a compact text snippet with DOC-xxx:SRC-yyy provenance."""
    header = (
        f"=== {doc_id_label}: {fa.filename} (doc_id={fa.document_id}, "
        f"type={fa.document_type or 'unknown'}, pages={fa.page_start}-{fa.page_end}, "
        f"coverage={fa.coverage:.0%}, status={fa.status}) ==="
    )
    lines = [header]

    semantic_fields = [
        ("facts", "Facts"),
        ("procedural_events", "Procedural Events"),
        ("issues", "Issues"),
        ("arguments", "Arguments"),
        ("counterarguments", "Counterarguments"),
        ("evidence", "Evidence"),
        ("legal_provisions", "Legal Provisions"),
        ("court_observations", "Court Observations"),
        ("court_reasoning", "Court Reasoning"),
        ("findings", "Findings"),
        ("decisions", "Decisions"),
        ("important_dates", "Important Dates"),
    ]

    for field_name, display_name in semantic_fields:
        items = getattr(fa, field_name, None)
        if items:
            item_lines = []
            for it in items:
                if isinstance(it, AnalysisItem):
                    text = it.text
                    refs = it.source_refs
                elif isinstance(it, dict):
                    text = it.get("text", "")
                    refs = it.get("source_refs", [])
                else:
                    text = str(it)
                    refs = []
                
                # Format compound refs for this document
                compound_refs = [
                    f"{doc_id_label}:{r}" if not r.startswith("DOC-") and not r.startswith("CLUSTER-") else r
                    for r in refs
                ]
                ref_str = f" [refs: {', '.join(compound_refs)}]" if compound_refs else ""
                item_lines.append(f"    - {text}{ref_str}")

            if item_lines:
                lines.append(f"  {display_name}:")
                lines.extend(item_lines)

    if fa.uncertainty:
        lines.append(f"  Uncertainty: {fa.uncertainty}")

    return "\n".join(lines)


def build_case_prompt(
    case_id: str,
    file_analyses: list[FileAnalysis],
    doc_map: dict[str, FileAnalysis],
    failed_docs: list[str],
    coverage: float,
) -> str:
    """Build the case synthesis prompt with structured FileAnalysis data and valid source tables."""
    lines = [CASE_SYSTEM_PROMPT]
    lines.append(f"\nCase ID: {case_id}")
    lines.append(
        f"Case Coverage: {coverage:.0%} ({len(file_analyses)}/{len(file_analyses) + len(failed_docs)} files successful)"
    )
    if failed_docs:
        lines.append(f"Failed documents (excluded): {', '.join(failed_docs)}")

    lines.append("\nAvailable Documents (Source Labels):")
    for doc_label, fa in doc_map.items():
        lines.append(
            f"  {doc_label} → {fa.filename} (doc_id={fa.document_id}, type={fa.document_type or 'unknown'}, "
            f"pages={fa.page_start}-{fa.page_end})"
        )

    lines.append("\nStructured Document Analyses:")
    for doc_label, fa in doc_map.items():
        lines.append(_compact_file_analysis(fa, doc_label))
        lines.append("")

    lines.append(
        "Instruction: Synthesize the above document analyses into a unified CaseAnalysis JSON matching the schema. "
        "Explicitly connect cross-file claims, defenses, counterarguments, and evidence. "
        "For each item and relationship, provide source_refs using only the valid DOC-xxx:SRC-xxx identifiers listed in the document analyses above."
    )
    return "\n".join(lines)
