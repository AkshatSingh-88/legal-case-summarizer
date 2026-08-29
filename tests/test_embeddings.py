import math
import uuid
import pytest
from unittest.mock import patch

from backend.app.chunking.chunk import Chunk
from backend.app.embeddings import embed_chunks, EmbeddedChunk
from backend.app.embeddings.model import get_provider


def make_chunk(doc_id="doc-1", filename="file.pdf", chunk_index=0, page_start=1, page_end=1, text="Hello world", evidence_ids=None):
    if evidence_ids is None:
        evidence_ids = []
    return Chunk(
        chunk_id=str(uuid.uuid4()),
        document_id=doc_id,
        filename=filename,
        chunk_index=chunk_index,
        page_start=page_start,
        page_end=page_end,
        pages=list(range(page_start, page_end + 1)),
        text=text,
        token_count=len(text.split()),
        evidence_ids=evidence_ids,
        evidence_score=0.5 if evidence_ids else 0.0,
        evidence_count=len(evidence_ids),
        section=None,
        meta={},
    )


def test_basic_chunk_to_embedding():
    chunk = make_chunk(text="The Supreme Court held that breach requires compensation.")
    embedded = embed_chunks([chunk])
    assert len(embedded) == 1
    assert isinstance(embedded[0], EmbeddedChunk)
    assert len(embedded[0].embedding) > 0
    assert embedded[0].chunk is chunk


def test_embedding_dimension_consistency():
    chunks = [make_chunk(text=f"Text {i} about legal reasoning and contract breach.") for i in range(5)]
    embedded = embed_chunks(chunks)
    dims = {e.dim for e in embedded}
    assert len(dims) == 1  # all same dim
    assert dims == {32}  # fake-32
    for e in embedded:
        assert len(e.embedding) == e.dim


def test_document_id_preservation():
    chunks = [
        make_chunk(doc_id="doc-A", text="Doc A content about Section 302 IPC."),
        make_chunk(doc_id="doc-B", text="Doc B content about Article 21."),
    ]
    embedded = embed_chunks(chunks)
    assert embedded[0].document_id == "doc-A"
    assert embedded[1].document_id == "doc-B"
    assert embedded[0].chunk.document_id == "doc-A"


def test_filename_preservation():
    chunk = make_chunk(filename="Judgment.pdf", text="Judgment text about court reasoning.")
    embedded = embed_chunks([chunk])[0]
    assert embedded.filename == "Judgment.pdf"
    assert embedded.chunk.filename == "Judgment.pdf"


def test_chunk_id_preservation():
    chunk = make_chunk(text="Some text")
    embedded = embed_chunks([chunk])[0]
    assert embedded.chunk_id == chunk.chunk_id
    assert embedded.chunk.chunk_id == chunk.chunk_id


def test_page_start_end_preservation():
    chunk = make_chunk(page_start=3, page_end=5, text="Multi-page chunk text about legal provisions.")
    embedded = embed_chunks([chunk])[0]
    assert embedded.page_start == 3
    assert embedded.page_end == 5
    assert embedded.chunk.page_start == 3


def test_pages_preservation():
    chunk = make_chunk(page_start=2, page_end=4, text="Pages 2-4 content.")
    embedded = embed_chunks([chunk])[0]
    assert embedded.pages == [2, 3, 4]
    assert embedded.chunk.pages == [2, 3, 4]


def test_evidence_ids_preservation():
    ev_ids = [str(uuid.uuid4()), str(uuid.uuid4())]
    chunk = make_chunk(text="Important sentence with evidence.", evidence_ids=ev_ids)
    embedded = embed_chunks([chunk])[0]
    assert embedded.evidence_ids == ev_ids
    assert embedded.chunk.evidence_ids == ev_ids


def test_multiple_documents_isolated():
    chunks = [
        make_chunk(doc_id="doc-A", filename="a.pdf", chunk_index=0, text="Doc A text about breach."),
        make_chunk(doc_id="doc-A", filename="a.pdf", chunk_index=1, text="Doc A second chunk."),
        make_chunk(doc_id="doc-B", filename="b.pdf", chunk_index=0, text="Doc B text about SARFAESI."),
    ]
    embedded = embed_chunks(chunks)
    assert len(embedded) == 3
    assert embedded[0].document_id == "doc-A"
    assert embedded[2].document_id == "doc-B"
    assert embedded[0].chunk_index == 0
    assert embedded[2].chunk_index == 0  # resets per doc, but embed preserves as is


def test_empty_input():
    assert embed_chunks([]) == []


def test_empty_short_text():
    chunks = [
        make_chunk(text=""),
        make_chunk(text="Hi"),
        make_chunk(text="   "),
    ]
    embedded = embed_chunks(chunks)
    assert len(embedded) == 3
    for e in embedded:
        assert len(e.embedding) == 32
        # Normalized
        norm = math.sqrt(sum(x * x for x in e.embedding))
        assert abs(norm - 1.0) < 1e-6


def test_deterministic_fake_provider():
    text = "Deterministic text about Supreme Court judgment and compensation."
    chunk1 = make_chunk(text=text)
    chunk2 = make_chunk(text=text)
    e1 = embed_chunks([chunk1])[0]
    e2 = embed_chunks([chunk2])[0]
    assert e1.embedding == e2.embedding
    # Different text -> different vector
    chunk3 = make_chunk(text="Different text completely.")
    e3 = embed_chunks([chunk3])[0]
    assert e3.embedding != e1.embedding


def test_batching_and_order_preservation():
    texts = [f"Chunk text number {i} with legal content about contract breach and court reasoning." for i in range(10)]
    chunks = [make_chunk(text=t, chunk_index=i) for i, t in enumerate(texts)]
    # Use small batch size to test batching
    from backend.app.config import get_settings
    settings = get_settings()
    orig_batch = settings.embedding_batch_size
    settings.embedding_batch_size = 3
    try:
        embedded = embed_chunks(chunks)
        assert len(embedded) == 10
        # Order preserved
        for i, e in enumerate(embedded):
            assert e.chunk.text == texts[i]
            assert e.chunk.chunk_index == i
    finally:
        settings.embedding_batch_size = orig_batch


def test_provider_failure_handling():
    chunks = [make_chunk(text="Text one"), make_chunk(text="Text two")]
    def failing_provider(texts):
        raise RuntimeError("API down")
    with patch("backend.app.embeddings.embed.get_provider", return_value=failing_provider):
        with pytest.raises(RuntimeError, match="Embedding provider failed"):
            embed_chunks(chunks)


def test_no_pdf_reopening():
    chunks = [make_chunk(text="Some chunk text")]
    with patch("pymupdf.open") as mock_open:
        embed_chunks(chunks)
        mock_open.assert_not_called()


def test_large_synthetic_800_chunks_performance():
    # ~800 chunks => 500 pages equivalent
    chunks = [make_chunk(text=f"Chunk {i} with legal text about Section 302 IPC and Article 21 and SARFAESI Act reasoning for performance testing. " * 2, chunk_index=i) for i in range(800)]
    import time
    start = time.time()
    embedded = embed_chunks(chunks)
    elapsed = time.time() - start
    assert len(embedded) == 800
    assert elapsed < 3.0
    for e in embedded:
        assert e.dim == 32


def test_normalized_vector():
    chunk = make_chunk(text="Normalize this legal chunk text for cosine similarity.")
    embedded = embed_chunks([chunk])[0]
    assert embedded.normalized is True
    norm = math.sqrt(sum(x * x for x in embedded.embedding))
    assert abs(norm - 1.0) < 1e-6
    assert embedded.model == "fake-32"
    assert embedded.provider == "fake"


def test_provider_boundary_fake_only():
    # Unknown provider should raise
    from backend.app.embeddings.model import get_provider
    with pytest.raises(ValueError, match="Unknown embedding provider"):
        get_provider("bge-m3", "fake-32", True, None)

