# Legal Case Summarizer

Public web application for summarizing Indian court case PDFs. Phase 1 is the minimal backend foundation only — no document processing pipeline yet.

## What this project is

Upload 1..N PDFs per case → page-level extraction with conditional OCR → local evidence layer → adaptive chunking → embeddings (BGE-M3) → parallel LLM chunk analysis → file-level → cross-file relationship analysis → case-level quick + detailed summaries with source refs `[file, p. N]` and downloadable PDF. Future RAG reuses same chunks/embeddings. Provider-agnostic LLM interface (Gemini primary).

Phase 1 contains only: FastAPI app factory, config, health endpoint, logging, project skeleton for future modules.

## Requirements

- Python 3.10+
- pip (or uv/pipx)
- Tesseract 5 executable (for OCR) — see OCR section below

## Setup

```bash
# 1. Create environment
python -m venv .venv
# Windows
.venv\Scripts\activate
# macOS/Linux
# source .venv/bin/activate

# 2. Install dependencies
pip install -e ".[dev]"

# Alternative with uv
# uv venv .venv && uv pip install -e ".[dev]"
```

## Configure

```bash
copy .env.example .env   # Windows
# cp .env.example .env   # macOS/Linux

# Edit .env if needed — defaults work for local dev without keys.
```

## Run

```bash
uvicorn backend.app.main:app --reload --port 8000
```

Open http://127.0.0.1:8000/ and http://127.0.0.1:8000/api/health

## Test

```bash
pytest -v
```

## OCR — Tesseract 5 (Phase 3)

Page-level conditional OCR: native text extracted first via PyMuPDF; OCR attempted only if text is empty/error or `char_count < OCR_CHAR_THRESHOLD (100)` or `word_count < OCR_WORD_THRESHOLD (15)`. Good native pages skip rendering/OCR for performance.

- **Install Tesseract 5:** Windows: `https://github.com/UB-Mannheim/tesseract/wiki` (add to PATH); macOS: `brew install tesseract`; Linux: `apt install tesseract-ocr`
- **Verify:** `tesseract --version` and `python -c "import pytesseract; print(pytesseract.get_tesseract_version())"`
- **Executable path:** If not on PATH, set `TESSERACT_CMD` in `.env` to full binary path.
- **Language:** `OCR_LANGUAGE` in `.env` (default `eng`); for Indic packs install language data and use e.g. `eng+hin`.
- **Unit tests** mock OCR and do not require Tesseract installed; integration test is separately marked.

## Project structure

```
backend/app/
  main.py          # app factory (create_app)
  config.py        # Settings (pydantic-settings, .env)
  logging_config.py
  api/             # routing only
    health.py
    router.py
  ingestion/       # page-level PDF + conditional OCR (Tesseract 5)
    models.py      # IngestedPage
    quality.py     # analyze_quality, thresholds
    ocr.py         # ocr_image boundary (replaceable)
    pdf.py         # ingest_pdf with OCR gate + rendering
  nlp/             # future: TF-IDF, TextRank, NER, legal extraction
  chunking/        # future: adaptive evidence-aware chunking
  embeddings/      # future: BGE-M3 (configurable)
  llm/             # future: provider interface (Gemini primary)
  pipeline/        # future: orchestration + quick/detailed synthesis
  storage/         # future: Supabase/pgvector
  workers/         # future: BackgroundTasks -> upgradeable
  pdfgen/          # future: detailed summary -> PDF
tests/
  test_health.py
  test_ingestion.py
  test_ocr.py
```

## LLM — Chunk Analysis (Phase 7)

Real API providers are available but **fake remains the default** for tests/CI.

- **Providers:** `fake` (deterministic, no SDK), `gemini` (PRIMARY, default model `gemini-3.5-flash-lite` — high-throughput lightweight Flash for chunk analysis, substantially higher free-tier RPD than 3.6 Flash, Free Tier via Google AI Studio, configurable RPM via `llm_gemini_rpm`), `mistral` (SPEED, `mistral-small-latest`, Experiment 1B/month free then paid, 2 RPM), `claude` (architecture-ready, no $0 free API — requires paid `ANTHROPIC_API_KEY`).
- **Env vars:** `GEMINI_API_KEY`, `MISTRAL_API_KEY` (never committed), `llm_provider`, `llm_model` (configurable, no code change to switch), `llm_max_concurrency=5` plus `llm_gemini_rpm=10`/`llm_mistral_rpm=2` provider throttling.
- **Free-tier safety:** `fake` default → no external send; real provider without key → `ConfigurationError` (no silent fallback to fake); Mistral `mistral_free_mode_only=True` blocks paid transition after 1B exhausted.
- **Manual real-model test (not in pytest):**
  ```bash
  pip install -e ".[dev]"  # installs google-genai, mistralai, tenacity
  GEMINI_API_KEY=... python -m backend.app.llm.manual --provider gemini --model gemini-3.5-flash-lite --limit 3
  MISTRAL_API_KEY=... python -m backend.app.llm.manual --provider mistral --model mistral-small-latest --limit 3
  ```
  Prints `ChunkAnalysis` JSON for manual inspection. Chunk text is sent to external API — legal docs only when you explicitly select real provider.
- **Config:** `backend/app/config.py` `llm_*` settings; `fake` works offline.

## Notes

- `application startup` (main.py) is separate from `API routing` (api/).
- Configuration is isolated in config.py and injected via get_settings().
