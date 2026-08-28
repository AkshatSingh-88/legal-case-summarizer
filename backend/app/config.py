from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_name: str = "Legal Case Summarizer"
    env: str = "development"
    log_level: str = "INFO"
    api_prefix: str = "/api"

    # Phase 3 — OCR (Tesseract 5)
    ocr_language: str = "eng"
    ocr_char_threshold: int = 100
    ocr_word_threshold: int = 15
    ocr_dpi: int = 300
    tesseract_cmd: str | None = None

    # Phase 4 — Evidence layer scoring
    evidence_textrank_weight: float = 0.5
    evidence_tfidf_weight: float = 0.3
    evidence_entity_weight: float = 0.2
    evidence_textrank_top_k: int = 5
    evidence_textrank_threshold: float = 0.1
    evidence_max_sentences_for_textrank: int = 800  # tunable cap; see extract.py docstring

    # Phase 5 — Adaptive chunking (evidence-aware, provider-agnostic)
    chunk_max_tokens: int = 1500
    chunk_overlap_tokens: int = 0
    chunk_min_tokens: int = 400

    # Phase 6 — Embeddings (foundation, fake provider)
    embedding_provider: str = "fake"
    embedding_model: str = "fake-32"
    embedding_batch_size: int = 32
    embedding_normalize: bool = True
    embedding_dimension: int | None = None

    # Placeholders for future phases — not used yet
    # gemini_api_key: str | None = None
    # embedding_model: str = "BAAI/bge-m3"

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )


_settings: Settings | None = None


def get_settings() -> Settings:
    global _settings
    if _settings is None:
        _settings = Settings()
    return _settings
