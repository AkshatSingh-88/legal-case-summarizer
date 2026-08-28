"""Embedding provider boundary — small callable interface."""

import hashlib
import math
from typing import Callable

Provider = Callable[[list[str]], list[list[float]]]


def _fake_vector(text: str, dim: int = 32) -> list[float]:
    # Deterministic: sha256 of text, stretched to dim
    digest = hashlib.sha256(text.encode("utf-8")).digest()  # 32 bytes
    # If dim >32, repeat with hashing of text+index
    vals: list[float] = []
    for i in range(dim):
        b = digest[i % len(digest)]
        # Also mix index to avoid repetition for dim>32
        # Use additional hash for variation when repeating
        if i >= len(digest):
            b = hashlib.sha256(f"{text}:{i}".encode()).digest()[0]
        # Map byte 0-255 to -1..1
        vals.append(b / 127.5 - 1.0)
    # Handle empty text: keep deterministic vector (hash of ""), not zero vector
    return vals


def _normalize(vec: list[float]) -> list[float]:
    norm = math.sqrt(sum(x * x for x in vec))
    if norm == 0:
        return vec
    return [x / norm for x in vec]


def _fake_provider(texts: list[str], dim: int = 32, normalize: bool = True) -> list[list[float]]:
    vectors: list[list[float]] = []
    for t in texts:
        v = _fake_vector(t, dim=dim)
        if normalize:
            v = _normalize(v)
        vectors.append(v)
    return vectors


def get_provider(name: str, model: str, normalize: bool, dim: int | None) -> Provider:
    """Return provider callable. Only 'fake' implemented in Phase 6."""
    if name == "fake":
        # Infer dim from model like "fake-32" or use dim param
        inferred = dim
        if inferred is None:
            # parse model e.g., fake-32
            try:
                inferred = int(model.split("-")[-1])
            except Exception:
                inferred = 32
        # Closure captures dim/normalize
        def provider(texts: list[str]) -> list[list[float]]:
            return _fake_provider(texts, dim=inferred, normalize=normalize)

        return provider
    # Future providers (bge-m3, gemini) will be added without changing embed.py
    raise ValueError(f"Unknown embedding provider: {name}")
