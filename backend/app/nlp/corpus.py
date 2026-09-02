"""Reduced-corpus builder for Quick mode — token-budgeted, metadata-anchored, provenance-preserving."""

from collections import defaultdict
from typing import Dict, List, Set, Tuple

from backend.app.chunking.tokenizer import count_tokens
from backend.app.config import get_settings
from backend.app.ingestion.models import IngestedPage
from backend.app.nlp.evidence import Evidence
from backend.app.nlp.extract import split_sentences

METADATA_PRIORITY = {
    "case_number": 4,
    "legal_provision": 3,
    "date": 2,
    "entity": 1,
}


def _calc_doc_tokens(selected_indices: set[int], s_text: dict[int, str], s_page: dict[int, int]) -> int:
    """Calculate the exact token count if selected_indices are assembled into pages."""
    by_page: dict[int, list[int]] = defaultdict(list)
    for idx in selected_indices:
        by_page[s_page[idx]].append(idx)
    return sum(count_tokens("\n\n".join(s_text[i] for i in sorted(by_page[p]))) for p in by_page)


def build_reduced_corpus(
    pages: list[IngestedPage],
    evidence: list[Evidence],
    max_tokens_per_doc: int | None = None,
    max_tokens_total: int | None = None,
) -> list[IngestedPage]:
    """Construct a token-budgeted reduced list of IngestedPage objects for Quick mode analysis."""
    if not pages:
        return []

    settings = get_settings()
    budget_total = max_tokens_total if max_tokens_total is not None else settings.quick_corpus_max_tokens_total
    budget_per_doc_cap = max_tokens_per_doc if max_tokens_per_doc is not None else settings.quick_corpus_max_tokens_per_doc
    min_sentences_per_doc = settings.quick_corpus_min_sentences_per_doc
    tier1_ratio = settings.quick_corpus_tier1_budget_ratio

    # 1. Group pages and evidence by document_id
    pages_by_doc: dict[str, list[IngestedPage]] = defaultdict(list)
    for p in pages:
        if not p.is_empty and p.text and p.text.strip():
            pages_by_doc[p.document_id].append(p)

    evidence_by_doc: dict[str, list[Evidence]] = defaultdict(list)
    for e in evidence:
        evidence_by_doc[e.document_id].append(e)

    sorted_doc_ids = sorted(pages_by_doc.keys())
    num_docs = len(sorted_doc_ids)
    if num_docs == 0:
        return []

    # 2. Multi-document budget allocation
    base_doc_budget = min(budget_per_doc_cap, max(1, budget_total // num_docs))
    doc_budgets: dict[str, int] = {doc_id: base_doc_budget for doc_id in sorted_doc_ids}

    doc_sentence_data: dict[str, dict] = {}
    for doc_id in sorted_doc_ids:
        doc_pages = sorted(pages_by_doc[doc_id], key=lambda pg: pg.page_number)
        filename = doc_pages[0].filename if doc_pages else ""

        s_text: dict[int, str] = {}
        s_page: dict[int, int] = {}
        s_score: dict[int, float] = {}
        page_to_s_indices: dict[int, list[int]] = defaultdict(list)

        doc_ev = evidence_by_doc.get(doc_id, [])
        important_sents = [e for e in doc_ev if e.type == "important_sentence" and "sentence_index" in e.meta]

        if important_sents:
            for e in sorted(important_sents, key=lambda x: x.meta["sentence_index"]):
                idx = e.meta["sentence_index"]
                s_text[idx] = e.text
                s_page[idx] = e.page_number
                s_score[idx] = e.score
                page_to_s_indices[e.page_number].append(idx)
        else:
            global_idx = 0
            for pg in doc_pages:
                p_sents = split_sentences(pg.text)
                for sent_str in p_sents:
                    s_text[global_idx] = sent_str
                    s_page[global_idx] = pg.page_number
                    s_score[global_idx] = 0.5
                    page_to_s_indices[pg.page_number].append(global_idx)
                    global_idx += 1

        total_doc_tokens = sum(count_tokens(t) for t in s_text.values())
        doc_sentence_data[doc_id] = {
            "filename": filename,
            "doc_pages": doc_pages,
            "s_text": s_text,
            "s_page": s_page,
            "s_score": s_score,
            "page_to_s_indices": page_to_s_indices,
            "total_tokens": total_doc_tokens,
            "all_indices": sorted(s_text.keys()),
        }

    # Surplus redistribution
    surplus = 0
    needy_docs = []
    for doc_id in sorted_doc_ids:
        needed = doc_sentence_data[doc_id]["total_tokens"]
        allocated = doc_budgets[doc_id]
        if needed < allocated:
            surplus += (allocated - needed)
            doc_budgets[doc_id] = needed
        elif needed > allocated:
            needy_docs.append(doc_id)

    if surplus > 0 and needy_docs:
        extra_per_needy = surplus // len(needy_docs)
        for doc_id in needy_docs:
            additional = min(extra_per_needy, budget_per_doc_cap - doc_budgets[doc_id])
            doc_budgets[doc_id] += additional

    # 3. Selection per document
    selected_pages_all: list[IngestedPage] = []
    accumulated_global_tokens = 0

    for doc_id in sorted_doc_ids:
        ddata = doc_sentence_data[doc_id]
        filename = ddata["filename"]
        s_text = ddata["s_text"]
        s_page = ddata["s_page"]
        s_score = ddata["s_score"]
        page_to_s_indices = ddata["page_to_s_indices"]
        all_indices = ddata["all_indices"]

        if not all_indices:
            continue

        doc_budget = doc_budgets[doc_id]
        tier1_cap = int(doc_budget * tier1_ratio)

        # A. Map metadata anchors (Tier 1 candidates)
        tier1_candidates: dict[int, int] = {}
        doc_ev = evidence_by_doc.get(doc_id, [])
        discrete_evs = [e for e in doc_ev if e.type in METADATA_PRIORITY]

        for m_ev in discrete_evs:
            p_num = m_ev.page_number
            m_text_lower = m_ev.text.strip().lower()
            if not m_text_lower:
                continue
            m_prio = METADATA_PRIORITY.get(m_ev.type, 1)

            page_candidates = page_to_s_indices.get(p_num, [])
            matching_s_indices = [idx for idx in page_candidates if m_text_lower in s_text[idx].lower()]

            if matching_s_indices:
                best_match = max(matching_s_indices, key=lambda idx: (s_score[idx], -idx))
                current_prio = tier1_candidates.get(best_match, 0)
                if m_prio > current_prio:
                    tier1_candidates[best_match] = m_prio

        # B. Anti-Starvation / Coverage Candidates (Tier 3)
        anti_starvation_indices: set[int] = set()
        pages_with_sentences = sorted(page_to_s_indices.keys())
        if len(all_indices) <= min_sentences_per_doc:
            anti_starvation_indices.update(all_indices)
        elif pages_with_sentences:
            p_len = len(pages_with_sentences)
            q1_pages = pages_with_sentences[: max(1, p_len // 3)]
            q2_pages = pages_with_sentences[max(1, p_len // 3) : max(2, (2 * p_len) // 3)]
            q3_pages = pages_with_sentences[max(2, (2 * p_len) // 3) :]

            for q_pgs in [q1_pages, q2_pages, q3_pages]:
                q_s_indices = [idx for pg in q_pgs for idx in page_to_s_indices.get(pg, [])]
                if q_s_indices:
                    best_q_s = max(q_s_indices, key=lambda idx: (s_score[idx], -idx))
                    anti_starvation_indices.add(best_q_s)

        # C. Rank candidate lists
        tier1_sorted = sorted(
            tier1_candidates.keys(),
            key=lambda idx: (tier1_candidates[idx], s_score[idx], -idx),
            reverse=True,
        )

        tier2_sorted = sorted(
            all_indices,
            key=lambda idx: (s_score[idx], -idx),
            reverse=True,
        )

        # D. Execute Budgeted Greedy Selection with exact page token tracking
        selected_indices: set[int] = set()
        selected_tier1_indices: set[int] = set()

        def _can_add(idx: int, is_tier1: bool = False) -> bool:
            tentative = selected_indices | {idx}
            tentative_doc_tokens = _calc_doc_tokens(tentative, s_text, s_page)
            if tentative_doc_tokens > doc_budget:
                return False
            if (accumulated_global_tokens + tentative_doc_tokens) > budget_total:
                return False
            if is_tier1:
                tentative_tier1 = selected_tier1_indices | {idx}
                tentative_tier1_tokens = _calc_doc_tokens(tentative_tier1, s_text, s_page)
                if tentative_tier1_tokens > tier1_cap:
                    return False
            return True

        # Step 1: Add Anti-starvation sentences
        for idx in sorted(anti_starvation_indices, key=lambda i: (s_score[i], -i), reverse=True):
            if _can_add(idx):
                selected_indices.add(idx)
                if idx in tier1_candidates:
                    selected_tier1_indices.add(idx)

        # Step 2: Add Tier 1 Anchor sentences up to tier1_cap
        for idx in tier1_sorted:
            if idx in selected_indices:
                continue
            if _can_add(idx, is_tier1=True):
                selected_indices.add(idx)
                selected_tier1_indices.add(idx)

        # Step 3: Add Tier 2 Substantive sentences until doc_budget
        for idx in tier2_sorted:
            if idx in selected_indices:
                continue
            if _can_add(idx):
                selected_indices.add(idx)

        # Step 4: Context Expansion for high-scoring sentences (score >= 0.75)
        for idx in sorted(list(selected_indices)):
            if s_score.get(idx, 0.0) >= 0.75:
                prev_idx = idx - 1
                if prev_idx in s_text and prev_idx not in selected_indices:
                    if s_page.get(prev_idx) == s_page.get(idx):
                        if _can_add(prev_idx):
                            selected_indices.add(prev_idx)

        # E. Construct reduced IngestedPage objects for this document
        selected_by_page: dict[int, list[int]] = defaultdict(list)
        for idx in selected_indices:
            selected_by_page[s_page[idx]].append(idx)

        doc_pages_out: list[IngestedPage] = []
        for p_num in sorted(selected_by_page.keys()):
            page_s_indices = sorted(selected_by_page[p_num])
            page_text = "\n\n".join(s_text[idx] for idx in page_s_indices)

            reduced_page = IngestedPage(
                document_id=doc_id,
                filename=filename,
                page_number=p_num,
                text=page_text,
                char_count=len(page_text),
                word_count=len(page_text.split()),
                is_empty=False,
                ocr_used=False,
                error=None,
                ocr_error=None,
            )
            doc_pages_out.append(reduced_page)

        selected_pages_all.extend(doc_pages_out)
        doc_final_tokens = sum(count_tokens(p.text) for p in doc_pages_out)
        accumulated_global_tokens += doc_final_tokens

    selected_pages_all.sort(key=lambda pg: (pg.document_id, pg.page_number))
    return selected_pages_all
