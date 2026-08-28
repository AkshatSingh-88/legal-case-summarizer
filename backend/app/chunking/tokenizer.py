"""Provider-agnostic token count approximation.

Heuristic: max(words * 1.3, chars / 4) — cheap, deterministic, no tokenizer.
Isolated so it can be replaced with tiktoken/sentencepiece when provider chosen.
"""


def count_tokens(text: str) -> int:
    """Approximate token count for chunk sizing. Integer, never invents provenance."""
    if not text or not text.strip():
        return 0
    words = len(text.split())
    chars = len(text)
    est = max(words * 1.3, chars / 4)
    return int(est) if est >= 1 else 1
