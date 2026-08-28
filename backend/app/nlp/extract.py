"""TF-IDF + bounded per-document TextRank."""

import re
import math
from collections import defaultdict

_SENTENCE_SPLIT = re.compile(r"(?<=[.!?])\s+")

# Bounding constants are configurable via Settings but defaults are sane for tests
# We read Settings at call time to avoid import-time coupling.


def split_sentences(text: str) -> list[str]:
    """Lightweight regex splitter — no spaCy, no NLTK."""
    if not text or not text.strip():
        return []
    # Normalize newlines
    text = text.replace("\r", " ").replace("\n", " ")
    # Collapse multiple spaces
    text = re.sub(r"\s+", " ", text).strip()
    if not text:
        return []
    parts = _SENTENCE_SPLIT.split(text)
    # Filter very short fragments that are not sentences
    result: list[str] = []
    for p in parts:
        s = p.strip()
        if len(s) < 5:
            continue
        # Ensure sentence ends with punctuation for consistency, but keep as-is if not
        result.append(s)
    return result


def tfidf_scores(sentences: list[str]) -> list[float]:
    """Return per-sentence TF-IDF importance in [0,1].

    Score = mean of top-3 TF-IDF term weights in sentence.
    Returns zeros if insufficient data.
    """
    if not sentences:
        return []
    if len(sentences) == 1:
        return [1.0]
    # Filter sentences that are too short for vectorizer
    # If all sentences are very short, vectorizer may fail on stop_words
    try:
        from sklearn.feature_extraction.text import TfidfVectorizer
    except ImportError:
        return [0.5] * len(sentences)

    try:
        vectorizer = TfidfVectorizer(stop_words="english", max_features=5000)
        matrix = vectorizer.fit_transform(sentences)  # sparse
    except ValueError:
        # e.g., empty vocabulary
        return [0.0] * len(sentences)

    import numpy as np

    scores: list[float] = []
    for i in range(matrix.shape[0]):
        row = matrix.getrow(i).toarray()[0]
        # Get non-zero values
        vals = row[row > 0]
        if len(vals) == 0:
            scores.append(0.0)
        else:
            # Mean of top-3
            vals_sorted = sorted(vals, reverse=True)
            top = vals_sorted[:3]
            scores.append(float(sum(top) / len(top)))
    # Normalize to [0,1] per document
    max_s = max(scores) if scores else 1.0
    if max_s > 0:
        scores = [s / max_s for s in scores]
    return scores


def textrank_scores(
    sentences: list[str],
    top_k: int = 5,
    threshold: float = 0.1,
    max_sentences: int = 800,
    damping: float = 0.85,
    iterations: int = 30,
) -> list[float]:
    """Bounded per-document TextRank scores in [0,1].

    `max_sentences` (default 800) is a configurable performance cap.
    For documents above the cap, TF-IDF pre-filtering is a bounded
    performance heuristic — it does NOT mean sentences outside the cap
    are unimportant. Remaining sentences still receive the existing
    fallback `0.3 * tfidf` score. The cap is tunable via
    `Settings.evidence_max_sentences_for_textrank` based on real
    workloads. Implementation remains sparse/bounded (top_k edges per
    node, threshold pruning) and does not use a dense O(n²) matrix or
    networkx. top_k/threshold/max_sentences are configurable via Settings.
    """
    n = len(sentences)
    if n == 0:
        return []
    if n == 1:
        return [1.0]

    # 800 is a configurable cap, not an importance cutoff — fallback keeps TF-IDF signal
    # Pre-filter if too many sentences
    indices = list(range(n))
    if n > max_sentences:
        # Use TF-IDF scores to keep most distinctive sentences for ranking
        pre_scores = tfidf_scores(sentences)
        # Get top max_sentences indices by pre_scores
        ranked = sorted(indices, key=lambda i: pre_scores[i], reverse=True)
        keep = set(ranked[:max_sentences])
        # We will compute TextRank only for kept sentences; others get 0
        # For simplicity, filter sentences list
        kept_sentences = [sentences[i] for i in sorted(keep)]
        kept_scores = textrank_scores(
            kept_sentences, top_k=top_k, threshold=threshold, max_sentences=max_sentences
        )
        # Map back
        result = [0.0] * n
        for idx, score in zip(sorted(keep), kept_scores):
            result[idx] = score
        # For non-kept, give low score based on tfidf
        for i in indices:
            if i not in keep:
                result[i] = pre_scores[i] * 0.3  # downgraded
        # Normalize
        max_r = max(result) if result else 1.0
        if max_r > 0:
            result = [r / max_r for r in result]
        return result

    try:
        from sklearn.feature_extraction.text import TfidfVectorizer
    except ImportError:
        return [0.5] * n

    try:
        vectorizer = TfidfVectorizer(stop_words="english", max_features=5000)
        X = vectorizer.fit_transform(sentences)  # L2-normalized sparse
    except ValueError:
        return [0.0] * n

    # Sparse cosine similarity via dot product (TF-IDF is L2 normalized)
    # S = X * X.T is sparse n x n
    S = (X * X.T).tocsr()

    # Build sparse adjacency: for each i, keep top_k neighbors above threshold
    # We store adjacency as list of (j, weight)
    neighbors: list[list[tuple[int, float]]] = [[] for _ in range(n)]
    for i in range(n):
        row = S.getrow(i)
        # row is sparse; row.indices / row.data give non-zero entries
        # Includes self-similarity 1.0 at j==i, we skip self
        candidates: list[tuple[int, float]] = []
        for j, w in zip(row.indices, row.data):
            if j == i:
                continue
            if w >= threshold:
                candidates.append((j, float(w)))
        # Keep top_k by weight
        candidates.sort(key=lambda x: x[1], reverse=True)
        neighbors[i] = candidates[:top_k]

    # PageRank iteration on sparse graph
    # Initialize uniform
    scores = [1.0 / n] * n
    # Precompute out-degree weights sum per node for normalization
    # For PageRank, we need to handle dangling nodes (no outgoing edges)
    for _ in range(iterations):
        new_scores = [(1 - damping) / n] * n
        for i in range(n):
            # Distribute score from i to its neighbors
            outs = neighbors[i]
            if not outs:
                # Dangling: distribute uniformly to all
                contrib = damping * scores[i] / n
                for j in range(n):
                    new_scores[j] += contrib
            else:
                total_w = sum(w for _, w in outs)
                if total_w == 0:
                    continue
                for j, w in outs:
                    new_scores[j] += damping * scores[i] * (w / total_w)
        scores = new_scores

    # Normalize to [0,1]
    max_s = max(scores) if scores else 1.0
    if max_s > 0:
        scores = [s / max_s for s in scores]
    return scores
