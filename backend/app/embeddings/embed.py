"""Embedding foundation — Chunk → EmbeddedChunk via batching."""

import uuid
from dataclasses import dataclass, field

from backend.app.chunking.chunk import Chunk
from backend.app.config import get_settings
from backend.app.embeddings.model import get_provider


@dataclass
class EmbeddedChunk:
    chunk: Chunk
    embedding: list[float]
    model: str
    provider: str
    dim: int
    normalized: bool
    # Flattened provenance for easy pgvector mapping (mirrors Chunk)
    chunk_id: str = field(init=False)
    document_id: str = field(init=False)
    filename: str = field(init=False)
    chunk_index: int = field(init=False)
    page_start: int = field(init=False)
    page_end: int = field(init=False)
    pages: list[int] = field(init=False)
    evidence_ids: list[str] = field(init=False)

    def __post_init__(self):
        self.chunk_id = self.chunk.chunk_id
        self.document_id = self.chunk.document_id
        self.filename = self.chunk.filename
        self.chunk_index = self.chunk.chunk_index
        self.page_start = self.chunk.page_start
        self.page_end = self.chunk.page_end
        self.pages = list(self.chunk.pages)
        self.evidence_ids = list(self.chunk.evidence_ids)


def embed_chunks(chunks: list[Chunk]) -> list[EmbeddedChunk]:
    """Embed chunks preserving provenance. Batch-aware, order-preserving."""
    if not chunks:
        return []

    settings = get_settings()
    provider_name = settings.embedding_provider
    model = settings.embedding_model
    batch_size = settings.embedding_batch_size
    normalize = settings.embedding_normalize
    dim_cfg = settings.embedding_dimension

    provider = get_provider(provider_name, model, normalize, dim_cfg)

    # Batch texts preserving order
    texts = [c.text for c in chunks]
    embeddings: list[list[float]] = []

    for i in range(0, len(texts), batch_size):
        batch_texts = texts[i : i + batch_size]
        batch_chunks = chunks[i : i + batch_size]
        try:
            batch_vectors = provider(batch_texts)
        except Exception as e:
            # Identify affected chunk ids in this batch
            ids = [c.chunk_id for c in batch_chunks]
            raise RuntimeError(f"Embedding provider failed for chunks {ids}: {e}") from e

        if len(batch_vectors) != len(batch_texts):
            raise RuntimeError(
                f"Provider returned {len(batch_vectors)} vectors for {len(batch_texts)} texts"
            )
        embeddings.extend(batch_vectors)

    # Determine dim from first vector
    dim = len(embeddings[0]) if embeddings else (dim_cfg or 0)

    result: list[EmbeddedChunk] = []
    for chunk, vec in zip(chunks, embeddings):
        # Ensure vec is list[float]
        result.append(
            EmbeddedChunk(
                chunk=chunk,
                embedding=list(vec),
                model=model,
                provider=provider_name,
                dim=len(vec),
                normalized=normalize,
            )
        )

    return result
