"""Analysis engine — provider-agnostic, bounded concurrency, provenance."""

import asyncio
from typing import List

from backend.app.chunking.chunk import Chunk
from backend.app.config import get_settings
from backend.app.llm.models import ChunkAnalysis
from backend.app.llm.prompts import build_chunk_prompt
from backend.app.llm.provider import get_llm_provider, _normalize_response
from backend.app.nlp.evidence import Evidence


def _normalize_and_validate(raw: dict, chunk: Chunk, provider: str, model: str) -> ChunkAnalysis:
    # Provenance from Chunk, never LLM
    # Semantic fields from raw, missing -> None, extra -> ignored via ConfigDict
    # First normalize provider variations (Mistral dict/string handling)
    data = _normalize_response(dict(raw))  # copy + normalize
    # Ensure provenance overwritten (LLM must not invent)
    data["chunk_id"] = chunk.chunk_id
    data["document_id"] = chunk.document_id
    data["filename"] = chunk.filename
    data["page_start"] = chunk.page_start
    data["page_end"] = chunk.page_end
    data["pages"] = list(chunk.pages)
    data["provider"] = provider
    data["model"] = model
    # Validate
    return ChunkAnalysis.model_validate(data)


async def _analyze_batch(
    chunks: list[Chunk],
    evidence: list[Evidence],
    provider_name: str,
    model: str,
    semaphore: asyncio.Semaphore,
) -> list[ChunkAnalysis]:
    # Build prompts
    prompts = [build_chunk_prompt(c, evidence) for c in chunks]
    provider = get_llm_provider(provider_name, model)

    # Provider is sync callable; run in thread to not block event loop
    # For fake, it's instant
    async with semaphore:
        # Call provider in executor to avoid blocking
        loop = asyncio.get_running_loop()
        try:
            raw_list = await loop.run_in_executor(None, lambda: provider(prompts))
        except Exception as e:
            # Batch failed — mark each chunk as failed but continue
            # We will handle per-chunk failure below: if provider raised for whole batch,
            # create failure analyses for each chunk
            return [
                ChunkAnalysis(
                    chunk_id=c.chunk_id,
                    document_id=c.document_id,
                    filename=c.filename,
                    page_start=c.page_start,
                    page_end=c.page_end,
                    pages=list(c.pages),
                    uncertainty=f"provider failed: {e}",
                    confidence=0.0,
                    provider=provider_name,
                    model=model,
                )
                for c in chunks
            ]

    results: list[ChunkAnalysis] = []
    for chunk, raw in zip(chunks, raw_list):
        try:
            if not isinstance(raw, dict):
                raise ValueError(f"Provider returned non-dict: {type(raw)}")
            ca = _normalize_and_validate(raw, chunk, provider_name, model)
            results.append(ca)
        except Exception as e:
            # Validation failure → failure ChunkAnalysis
            results.append(
                ChunkAnalysis(
                    chunk_id=chunk.chunk_id,
                    document_id=chunk.document_id,
                    filename=chunk.filename,
                    page_start=chunk.page_start,
                    page_end=chunk.page_end,
                    pages=list(chunk.pages),
                    uncertainty=f"validation failed: {e}",
                    confidence=0.0,
                    provider=provider_name,
                    model=model,
                )
            )
    return results


def analyze_chunks(
    chunks: list[Chunk],
    evidence: list[Evidence] | None = None,
) -> list[ChunkAnalysis]:
    """Public entry: list[Chunk] -> list[ChunkAnalysis] preserving order and provenance."""
    if not chunks:
        return []
    evidence = evidence or []
    settings = get_settings()
    provider_name = settings.llm_provider
    model = settings.llm_model
    max_conc = settings.llm_max_concurrency

    # Group chunks into batches for bounded concurrency
    # We process in batches of max_conc to respect provider RPM as well (provider has internal throttle)
    # Use asyncio for bounded concurrency, but keep sync entry point for tests

    async def _run() -> list[ChunkAnalysis]:
        sem = asyncio.Semaphore(max_conc)
        # Split into batch groups to avoid huge gather
        tasks = []
        for i in range(0, len(chunks), max_conc):
            batch = chunks[i : i + max_conc]
            tasks.append(_analyze_batch(batch, evidence, provider_name, model, sem))
        # Gather batches sequentially or concurrently? Bounded: gather all with semaphore
        batch_results = await asyncio.gather(*tasks)
        # Flatten preserving original order (batches already in order)
        flat: list[ChunkAnalysis] = []
        for br in batch_results:
            flat.extend(br)
        return flat

    # If already in event loop (e.g., tests), run via asyncio.run or get loop
    try:
        loop = asyncio.get_running_loop()
        # If loop running, create new task and run until complete via run_until_complete not possible
        # Fallback to synchronous provider call without async
        # Simplify: for sync context, just call provider directly without async
        import concurrent.futures

        # Synchronous fallback: chunk-by-chunk with provider
        provider = get_llm_provider(provider_name, model)
        prompts = [build_chunk_prompt(c, evidence) for c in chunks]
        # Batch via provider directly (provider handles internal batching/retries)
        try:
            raw_list = provider(prompts)
        except Exception as e:
            # Whole batch failed
            return [
                ChunkAnalysis(
                    chunk_id=c.chunk_id,
                    document_id=c.document_id,
                    filename=c.filename,
                    page_start=c.page_start,
                    page_end=c.page_end,
                    pages=list(c.pages),
                    uncertainty=f"provider failed: {e}",
                    confidence=0.0,
                    provider=provider_name,
                    model=model,
                )
                for c in chunks
            ]
        results: list[ChunkAnalysis] = []
        for chunk, raw in zip(chunks, raw_list):
            try:
                results.append(_normalize_and_validate(raw, chunk, provider_name, model))
            except Exception as e:
                results.append(
                    ChunkAnalysis(
                        chunk_id=chunk.chunk_id,
                        document_id=chunk.document_id,
                        filename=chunk.filename,
                        page_start=chunk.page_start,
                        page_end=chunk.page_end,
                        pages=list(chunk.pages),
                        uncertainty=f"validation failed: {e}",
                        confidence=0.0,
                        provider=provider_name,
                        model=model,
                    )
                )
        return results
    except RuntimeError:
        # No running loop
        return asyncio.run(_run())
