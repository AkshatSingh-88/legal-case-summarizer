"""Prompt builder for legal chunk analysis."""

from backend.app.chunking.chunk import Chunk
from backend.app.nlp.evidence import Evidence

SYSTEM_PROMPT = """You are a legal assistant analyzing a single chunk of a court case document.
Analyze ONLY the supplied chunk text. Do not invent facts, dates, provisions, or entities not present in the chunk.
Extract meaningful legal information and distinguish:
- facts from arguments
- arguments from court observations and court reasoning
- legal provisions/laws cited
- decisions/orders
Preserve names, dates, case numbers and legal provisions accurately and verbatim where possible.
If context is insufficient for a category, omit that field rather than writing "N/A" or inventing content.
Set uncertainty when the chunk does not provide enough information.
Do NOT attempt to summarize the entire case from this single chunk.
Return structured JSON matching the requested schema.
"""

_EVIDENCE_LABEL = {
    "legal_provision": "legal_provision",
    "case_number": "case_number",
    "date": "date",
    "entity": "entity",
    "important_sentence": "important",
}


def build_chunk_prompt(chunk: Chunk, evidence: list[Evidence]) -> str:
    """Build prompt for a single chunk + top evidence snippets."""
    # Evidence snippets: filter to this chunk's pages, sort by score desc, top 5
    relevant = [e for e in evidence if e.document_id == chunk.document_id and e.page_number in chunk.pages]
    relevant.sort(key=lambda e: e.score, reverse=True)
    top = relevant[:5]

    evidence_block = ""
    if top:
        lines = []
        for e in top:
            label = _EVIDENCE_LABEL.get(e.type, e.type)
            snippet = e.text.strip().replace("\n", " ")
            lines.append(f"[{label}] {snippet} (p. {e.page_number})")
        evidence_block = "Evidence snippets (for context, do not duplicate verbatim if already in chunk):\n" + "\n".join(lines) + "\n\n"

    return (
        f"{SYSTEM_PROMPT}\n"
        f"Chunk source: {chunk.filename} p. {chunk.page_start}-{chunk.page_end} (chunk {chunk.chunk_index})\n"
        f"{evidence_block}"
        f"Chunk text:\n{chunk.text}\n"
    )
