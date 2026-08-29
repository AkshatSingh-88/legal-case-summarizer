"""Dev utility for manual real-model testing — not used in pytest."""

import argparse
import json

from backend.app.chunking.chunk import Chunk
from backend.app.config import get_settings
from backend.app.llm.analyze import analyze_chunks
from backend.app.nlp.evidence import Evidence

# Simple synthetic chunk for demo if no case file
DEMO_TEXT = (
    "Facts: The petitioner alleged breach of contract under Section 13 of the SARFAESI Act. "
    "The High Court observed that the borrower failed to discharge liability and referred to Article 21. "
    "Issues: Whether Section 13 is applicable and whether compensation is due. "
    "Decision: Civil Appeal No. 123/2024 allowed with costs on 12 March 2024."
)


def main():
    parser = argparse.ArgumentParser(description="Manual LLM chunk analysis")
    parser.add_argument("--provider", default=None, help="gemini|mistral|fake|claude")
    parser.add_argument("--model", default=None, help="model name override")
    parser.add_argument("--limit", type=int, default=3, help="number of chunks to analyze")
    parser.add_argument("--chunk-index", type=int, default=0, help="starting chunk index (for demo)")
    args = parser.parse_args()

    settings = get_settings()
    provider = args.provider or settings.llm_provider
    model = args.model or settings.llm_model

    # Override settings for this run
    settings.llm_provider = provider
    if args.model:
        settings.llm_model = args.model

    print(f"Manual test: provider={provider} model={settings.llm_model} limit={args.limit}")
    # Use same Settings/configuration mechanism as the real provider (reads .env via pydantic-settings)
    if provider == "gemini" and not settings.gemini_api_key:
        print("Warning: GEMINI_API_KEY not configured — set via .env (GEMINI_API_KEY=...) or environment variable; will fail with ConfigurationError")
    elif provider == "mistral" and not settings.mistral_api_key:
        print("Warning: MISTRAL_API_KEY not configured — set via .env (MISTRAL_API_KEY=...) or environment variable; will fail with ConfigurationError")

    # Build demo chunks
    chunks: list[Chunk] = []
    evidence: list[Evidence] = []
    for i in range(args.limit):
        # Use uuid-like chunk_id
        import uuid

        chunk = Chunk(
            chunk_id=str(uuid.uuid4()),
            document_id="demo-doc",
            filename="demo.pdf",
            chunk_index=i,
            page_start=i + 1,
            page_end=i + 1,
            pages=[i + 1],
            text=DEMO_TEXT,
            token_count=len(DEMO_TEXT.split()),
            evidence_ids=[],
            evidence_score=0.0,
            evidence_count=0,
            section=None,
            meta={},
        )
        chunks.append(chunk)

    results = analyze_chunks(chunks, evidence)
    for r in results:
        print(json.dumps(r.model_dump(exclude_none=False), indent=2, ensure_ascii=False))
        print("---")


if __name__ == "__main__":
    main()
