"""Supabase client factory and configuration for Legal Case Summarizer."""

from functools import lru_cache
import logging
from typing import Any

from backend.app.config import Settings, get_settings

logger = logging.getLogger(__name__)

_supabase_client: Any = None


def get_supabase_client(settings: Settings | None = None) -> Any:
    """Returns a singleton Supabase client configured with the service-role key.

    Returns None if SUPABASE_URL or SUPABASE_SERVICE_ROLE_KEY are not configured.
    """
    global _supabase_client
    if _supabase_client is not None:
        return _supabase_client

    if settings is None:
        settings = get_settings()

    url = settings.supabase_url
    key = settings.supabase_service_role_key

    if not url or not key:
        logger.debug("Supabase URL or service-role key not set; client unavailable.")
        return None

    try:
        from supabase import Client, create_client

        _supabase_client = create_client(url, key)
        logger.info("Supabase service-role client initialized successfully.")
        return _supabase_client
    except Exception as e:
        logger.error("Failed to initialize Supabase client: %s", e)
        return None


def reset_supabase_client() -> None:
    """Reset the singleton instance (primarily for tests)."""
    global _supabase_client
    _supabase_client = None
