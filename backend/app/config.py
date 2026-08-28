from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_name: str = "Legal Case Summarizer"
    env: str = "development"
    log_level: str = "INFO"
    api_prefix: str = "/api"

    # Placeholders for future phases — not used in Phase 1
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
