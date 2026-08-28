"""Embeddings foundation — provider boundary + batching."""

from backend.app.embeddings.embed import EmbeddedChunk, embed_chunks
from backend.app.embeddings.model import get_provider

__all__ = ["EmbeddedChunk", "embed_chunks", "get_provider"]
