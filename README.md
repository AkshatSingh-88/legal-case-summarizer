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

## Notes

- No OCR/embedding/LLM/Supabase dependencies installed in Phase 1.
- `application startup` (main.py) is separate from `API routing` (api/).
- Configuration is isolated in config.py and injected via get_settings().
