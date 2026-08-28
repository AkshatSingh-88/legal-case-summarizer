"""Adaptive evidence-aware chunking — paragraph-first, linear."""

import re
import uuid
from dataclasses import dataclass, field

from backend.app.chunking.sections import is_heading
from backend.app.chunking.tokenizer import count_tokens
from backend.app.config import get_settings
from backend.app.ingestion.models import IngestedPage
from backend.app.nlp.evidence import Evidence
from backend.app.nlp.extract import split_sentences


@dataclass
class Chunk:
    chunk_id: str
    document_id: str
    filename: str
    chunk_index: int
    page_start: int
    page_end: int
    pages: list[int]
    text: str
    token_count: int
    evidence_ids: list[str]
    evidence_score: float
    evidence_count: int
    section: str | None
    meta: dict = field(default_factory=dict)


_PARAGRAPH_SPLIT = re.compile(r"\n\s*\n")


def _split_paragraphs(text: str) -> list[str]:
    if not text or not text.strip():
        return []
    # Split on 2+ newlines; if no such separator, treat whole text as one paragraph
    # Also handle \r\n
    normalized = text.replace("\r\n", "\n")
    parts = _PARAGRAPH_SPLIT.split(normalized)
    result: list[str] = []
    for p in parts:
        s = p.strip()
        if s:
            # Collapse single newlines inside paragraph to space, preserve sentence boundaries
            s = re.sub(r"\s*\n\s*", " ", s)
            s = re.sub(r" +", " ", s).strip()
            if s:
                result.append(s)
    return result


def _char_fallback(sentence: str, max_tokens: int) -> list[str]:
    """Split single oversized sentence by chars with token estimate."""
    # Estimate chars per token ~4
    max_chars = max_tokens * 4
    if len(sentence) <= max_chars:
        return [sentence]
    parts: list[str] = []
    start = 0
    while start < len(sentence):
        end = min(start + max_chars, len(sentence))
        # Try to break at last space within window to avoid mid-word
        if end < len(sentence):
            space = sentence.rfind(" ", start, end)
            if space > start + max_chars // 2:
                end = space + 1
        chunk = sentence[start:end].strip()
        if chunk:
            parts.append(chunk)
        start = end
    return parts if parts else [sentence]


def build_chunks(
    pages: list[IngestedPage],
    evidence: list[Evidence],
) -> list[Chunk]:
    """Build adaptive chunks from pages + evidence. Never re-reads PDFs."""
    if not pages:
        return []

    settings = get_settings()
    max_tokens = settings.chunk_max_tokens
    min_tokens = settings.chunk_min_tokens
    # overlap_tokens reserved for future RAG (0 default) — keep field but don't apply yet
    # chunk_overlap_tokens = settings.chunk_overlap_tokens

    # Group pages by document_id
    from collections import defaultdict

    by_doc: dict[str, list[IngestedPage]] = defaultdict(list)
    for p in pages:
        by_doc[p.document_id].append(p)

    # Index evidence by document_id and page
    evidence_by_doc: dict[str, list[Evidence]] = defaultdict(list)
    for e in evidence:
        evidence_by_doc[e.document_id].append(e)

    # For quick strong-evidence check per page
    # Strong = legal_provision or case_number with score >0.85
    strong_text_by_page: dict[tuple[str, int], list[str]] = defaultdict(list)
    for e in evidence:
        if e.type in ("legal_provision", "case_number") and e.score > 0.85:
            strong_text_by_page[(e.document_id, e.page_number)].append(e.text)

    all_chunks: list[Chunk] = []

    for doc_id, doc_pages in by_doc.items():
        doc_pages = sorted(doc_pages, key=lambda p: p.page_number)
        # Filename consistent per doc_id (take first)
        filename = doc_pages[0].filename if doc_pages else ""

        # Build paragraph list preserving order, page provenance, heading flag
        paragraphs: list[dict] = []  # {text, page_number, is_heading}
        for pg in doc_pages:
            if pg.is_empty or not pg.text or not pg.text.strip():
                continue
            paras = _split_paragraphs(pg.text)
            if not paras:
                paras = [pg.text.strip()]
            for para in paras:
                # Handle oversized paragraph: split into sentences before packing
                para_tokens = count_tokens(para)
                if para_tokens > max_tokens:
                    sents = split_sentences(para)
                    if not sents:
                        # No sentence boundaries found — will be handled as char fallback later
                        paragraphs.append(
                            {"text": para, "page_number": pg.page_number, "is_heading": is_heading(para)}
                        )
                    else:
                        # Check each sentence for oversized
                        for sent in sents:
                            if count_tokens(sent) > max_tokens:
                                # Char fallback pieces
                                for piece in _char_fallback(sent, max_tokens):
                                    paragraphs.append(
                                        {"text": piece, "page_number": pg.page_number, "is_heading": False, "was_truncated": True}
                                    )
                            else:
                                paragraphs.append(
                                    {"text": sent, "page_number": pg.page_number, "is_heading": is_heading(sent)}
                                )
                else:
                    paragraphs.append(
                        {"text": para, "page_number": pg.page_number, "is_heading": is_heading(para)}
                    )

        # Adaptive packing
        chunks_for_doc: list[Chunk] = []
        current_texts: list[str] = []
        current_pages: set[int] = set()
        current_section: str | None = None
        current_was_truncated = False
        # Track headings/provisions in current chunk for meta
        current_headings: list[str] = []
        current_provisions: list[str] = []

        def flush_current():
            nonlocal current_texts, current_pages, current_section, current_was_truncated, current_headings, current_provisions
            if not current_texts:
                return
            text = "\n\n".join(current_texts)
            pages_list = sorted(current_pages)
            token_count = count_tokens(text)
            chunk_index = len(chunks_for_doc)
            # Evidence attachment
            doc_evidence = evidence_by_doc.get(doc_id, [])
            attached: list[Evidence] = []
            lower_text = text.lower()
            for e in doc_evidence:
                if e.page_number not in pages_list:
                    continue
                # Evidence text contained in chunk text (case-insensitive for robustness)
                if e.text.lower() in lower_text or e.text.strip().lower() in lower_text:
                    attached.append(e)
                # For important_sentence, also consider if sentence was part of paragraph — already covered by substring

            # Fallback: if no evidence matched via substring but evidence page in chunk, attach by page (edge: long paraphrased)
            # We already did substring, so fine

            evidence_ids = [e.id for e in attached]
            evidence_count = len(attached)
            if attached:
                top_scores = sorted([e.score for e in attached], reverse=True)[:3]
                evidence_score = sum(top_scores) / len(top_scores)
            else:
                evidence_score = 0.0

            # Collect headings/provisions for meta
            headings_in_chunk = [t for t in current_headings]
            # Provisions: legal_provision evidence text in this chunk
            provisions_in_chunk = [e.text for e in attached if e.type == "legal_provision"]

            meta: dict = {
                "headings": headings_in_chunk,
                "provisions": provisions_in_chunk,
                "has_overlap": False,
                "evidence_types": list({e.type for e in attached}),
            }
            if current_was_truncated:
                meta["was_truncated"] = True

            chunk = Chunk(
                chunk_id=str(uuid.uuid4()),
                document_id=doc_id,
                filename=filename,
                chunk_index=chunk_index,
                page_start=min(pages_list) if pages_list else 0,
                page_end=max(pages_list) if pages_list else 0,
                pages=pages_list,
                text=text,
                token_count=token_count,
                evidence_ids=evidence_ids,
                evidence_score=evidence_score,
                evidence_count=evidence_count,
                section=current_section,
                meta=meta,
            )
            chunks_for_doc.append(chunk)
            # Reset
            current_texts = []
            current_pages = set()
            current_section = None
            current_was_truncated = False
            current_headings = []
            current_provisions = []

        for para in paragraphs:
            para_text: str = para["text"]
            para_page: int = para["page_number"]
            para_heading: bool = para["is_heading"]
            para_truncated: bool = para.get("was_truncated", False)

            # Heading boundary: flush before starting new section
            if para_heading and current_texts:
                flush_current()

            # Check strong evidence slack for this paragraph
            strong_list = strong_text_by_page.get((doc_id, para_page), [])
            has_strong = False
            if strong_list:
                lower_para = para_text.lower()
                for s in strong_list:
                    if s.lower() in lower_para:
                        has_strong = True
                        break

            effective_max = int(max_tokens * 1.10) if has_strong else max_tokens

            prospective_text = "\n\n".join(current_texts + [para_text]) if current_texts else para_text
            prospective_tokens = count_tokens(prospective_text)

            if prospective_tokens > effective_max and current_texts:
                flush_current()

            # Now add paragraph to current
            if not current_texts:
                # Starting new chunk — section is heading if this para is heading
                if para_heading:
                    current_section = para_text.strip()
                current_headings = []
                current_provisions = []

            current_texts.append(para_text)
            current_pages.add(para_page)
            if para_heading:
                current_headings.append(para_text.strip())
                if current_section is None:
                    current_section = para_text.strip()
            if para_truncated:
                current_was_truncated = True

        # Flush remaining
        flush_current()

        # Handle tiny tail chunks: if last chunk < min_tokens and we have at least 2 chunks, merge with previous if fits
        # Do not merge heading-separated chunks (preserve section boundaries)
        if len(chunks_for_doc) >= 2:
            last = chunks_for_doc[-1]
            if last.token_count < min_tokens:
                prev = chunks_for_doc[-2]
                if prev.section is not None and last.section is not None and prev.section != last.section:
                    pass  # preserve heading boundary
                else:
                    merged_text = prev.text + "\n\n" + last.text
                    merged_tokens = count_tokens(merged_text)
                    # Allow merge up to 1.10*max_tokens if either had strong evidence, else max_tokens
                    # For simplicity allow up to max_tokens*1.10 for tail merge
                    if merged_tokens <= int(max_tokens * 1.10):
                        # Merge
                        merged_pages = sorted(set(prev.pages + last.pages))
                        merged_evidence_ids = list(dict.fromkeys(prev.evidence_ids + last.evidence_ids))
                        # Recompute evidence_score as mean top3 of merged evidences
                        doc_ev = evidence_by_doc.get(doc_id, [])
                        merged_attached = [e for e in doc_ev if e.id in merged_evidence_ids]
                        if merged_attached:
                            top = sorted([e.score for e in merged_attached], reverse=True)[:3]
                            merged_score = sum(top) / len(top)
                        else:
                            merged_score = 0.0
                        merged_chunk = Chunk(
                            chunk_id=prev.chunk_id,  # keep first id for stability, or new? Use prev's
                            document_id=doc_id,
                            filename=filename,
                            chunk_index=prev.chunk_index,
                            page_start=min(merged_pages),
                            page_end=max(merged_pages),
                            pages=merged_pages,
                            text=merged_text,
                            token_count=merged_tokens,
                            evidence_ids=merged_evidence_ids,
                            evidence_score=merged_score,
                            evidence_count=len(merged_evidence_ids),
                            section=prev.section,
                            meta={
                                "headings": list(dict.fromkeys(prev.meta.get("headings", []) + last.meta.get("headings", []))),
                                "provisions": list(dict.fromkeys(prev.meta.get("provisions", []) + last.meta.get("provisions", []))),
                                "has_overlap": False,
                                "evidence_types": list(dict.fromkeys(prev.meta.get("evidence_types", []) + last.meta.get("evidence_types", []))),
                            },
                        )
                        if prev.meta.get("was_truncated") or last.meta.get("was_truncated"):
                            merged_chunk.meta["was_truncated"] = True
                        # Replace last two with merged
                        chunks_for_doc = chunks_for_doc[:-2] + [merged_chunk]
                        # Re-index following chunks (only last was merged, so no following)
                        # Fix chunk_index sequence
                        for idx, ch in enumerate(chunks_for_doc):
                            ch.chunk_index = idx

        # Ensure chunk_index sequential (already)
        all_chunks.extend(chunks_for_doc)

    return all_chunks
