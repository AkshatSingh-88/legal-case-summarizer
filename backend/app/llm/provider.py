"""LLM provider boundary — fake/gemini/mistral, claude stub. Simple callable, no hierarchy."""

import asyncio
import hashlib
import json
import logging
import os
import time
from typing import Callable

from tenacity import retry, stop_after_attempt, wait_exponential_jitter, retry_if_exception_type

logger = logging.getLogger(__name__)

LlmProvider = Callable[[list[str]], list[dict]]


def _normalize_response(data: dict) -> dict:
    """Normalize provider JSON to ChunkAnalysis-compatible dict.

    Handles Mistral variations:
    - list[str] stays, single string -> [string], list[dict] -> list[str], dict -> list[str]
    - uncertainty dict -> string
    Keeps provider-agnostic normalization in one place.
    """
    SEMANTIC_LIST_FIELDS = {
        "facts",
        "procedural_events",
        "issues",
        "arguments",
        "counterarguments",
        "evidence_mentioned",
        "legal_provisions",
        "court_observations",
        "court_reasoning",
        "decisions",
        "important_dates",
        "entities",
    }

    normalized: dict = {}
    for k, v in data.items():
        if k in SEMANTIC_LIST_FIELDS:
            if v is None:
                normalized[k] = None
            elif isinstance(v, str):
                # Single string -> single-item list
                normalized[k] = [v]
            elif isinstance(v, dict):
                # Dict -> list of meaningful string values
                vals: list[str] = []
                for vv in v.values():
                    if isinstance(vv, str) and vv.strip():
                        vals.append(vv.strip())
                    elif isinstance(vv, list):
                        for item in vv:
                            if isinstance(item, str):
                                vals.append(item)
                            elif isinstance(item, dict):
                                val = item.get("description") or item.get("text") or item.get("content") or item.get("value") or item.get("name") or item.get("statement")
                                if val is None:
                                    for x in item.values():
                                        if isinstance(x, str):
                                            val = x
                                            break
                                if val is not None:
                                    vals.append(str(val))
                    elif vv is not None:
                        vals.append(str(vv))
                # Fallback: if no string values extracted, try top-level keys
                if not vals:
                    for vv in v.values():
                        if isinstance(vv, str):
                            vals.append(vv)
                normalized[k] = vals if vals else None
            elif isinstance(v, list):
                norm_list: list[str] = []
                for item in v:
                    if isinstance(item, dict):
                        val = item.get("description") or item.get("text") or item.get("content") or item.get("value") or item.get("name") or item.get("statement")
                        if val is None:
                            for vv in item.values():
                                if isinstance(vv, str):
                                    val = vv
                                    break
                        norm_list.append(str(val) if val is not None else str(item))
                    elif isinstance(item, str):
                        norm_list.append(item)
                    else:
                        norm_list.append(str(item))
                normalized[k] = norm_list
            else:
                # Unexpected type, coerce to string list
                normalized[k] = [str(v)]
        elif k == "uncertainty":
            if v is None:
                normalized[k] = None
            elif isinstance(v, str):
                normalized[k] = v
            elif isinstance(v, dict):
                # Extract meaningful text: prefer reason/description/text
                val = v.get("reason") or v.get("description") or v.get("text") or v.get("message")
                if val is None:
                    # Combine all string values
                    parts = [str(x) for x in v.values() if isinstance(x, str) and x.strip()]
                    val = " ".join(parts) if parts else json.dumps(v)
                normalized[k] = str(val)
            elif isinstance(v, list):
                # List of strings/dicts -> join
                parts = []
                for item in v:
                    if isinstance(item, str):
                        parts.append(item)
                    elif isinstance(item, dict):
                        val = item.get("description") or item.get("text")
                        parts.append(str(val) if val else str(item))
                    else:
                        parts.append(str(item))
                normalized[k] = " ".join(parts)
            else:
                normalized[k] = str(v)
        elif k == "confidence":
            if v is None:
                normalized[k] = None
            elif isinstance(v, (int, float)):
                normalized[k] = float(v)
            elif isinstance(v, dict):
                # Try to extract numeric
                val = v.get("confidence") or v.get("value")
                try:
                    normalized[k] = float(val) if val is not None else None
                except Exception:
                    normalized[k] = None
            elif isinstance(v, str):
                try:
                    normalized[k] = float(v)
                except Exception:
                    normalized[k] = None
            else:
                normalized[k] = None
        else:
            # Provenance or unknown: keep as is (Pydantic extra="ignore" will drop)
            normalized[k] = v
    return normalized

# --- Exceptions ---

class ConfigurationError(Exception):
    pass


class AuthenticationError(Exception):
    pass


class RateLimitError(Exception):
    pass


class ServerError(Exception):
    pass


# --- Simple rate limiter per provider (token-bucket via sleep) ---

_last_call: dict[str, float] = {}
_min_interval: dict[str, float] = {}


def _throttle(provider_name: str, rpm: int):
    if rpm <= 0:
        return
    interval = 60.0 / rpm
    now = time.monotonic()
    last = _last_call.get(provider_name, 0.0)
    wait = (last + interval) - now
    if wait > 0:
        time.sleep(wait)
    _last_call[provider_name] = time.monotonic()


async def _athrottle(provider_name: str, rpm: int):
    if rpm <= 0:
        return
    interval = 60.0 / rpm
    now = time.monotonic()
    last = _last_call.get(provider_name, 0.0)
    wait = (last + interval) - now
    if wait > 0:
        await asyncio.sleep(wait)
    _last_call[provider_name] = time.monotonic()


# --- Fake provider (deterministic, no SDK) ---

def _fake_response_for_prompt(prompt: str) -> dict:
    # Deterministic hash of prompt -> stable fake extraction
    h = hashlib.sha256(prompt.encode()).hexdigest()
    # Use hash to vary whether facts present
    has_facts = int(h[:2], 16) % 2 == 0
    base = {
        "facts": [f"Fake fact {h[:6]} from chunk"] if has_facts else None,
        "issues": None,
        "arguments": None,
        "legal_provisions": None,
        "decisions": None,
        "uncertainty": None,
        "confidence": 0.8,
    }
    # Occasionally add provision to test normalization
    if int(h[2:4], 16) % 3 == 0:
        base["legal_provisions"] = [f"Section {int(h[4:6], 16) % 500} IPC"]
    return base


def _fake_provider(prompts: list[str]) -> list[dict]:
    return [_fake_response_for_prompt(p) for p in prompts]


# --- Gemini provider ---

# JSON schema for structured output — semantic fields only, provenance is set by us
_GEMINI_RESPONSE_SCHEMA = {
    "type": "object",
    "properties": {
        "facts": {"type": "array", "items": {"type": "string"}},
        "procedural_events": {"type": "array", "items": {"type": "string"}},
        "issues": {"type": "array", "items": {"type": "string"}},
        "arguments": {"type": "array", "items": {"type": "string"}},
        "counterarguments": {"type": "array", "items": {"type": "string"}},
        "evidence_mentioned": {"type": "array", "items": {"type": "string"}},
        "legal_provisions": {"type": "array", "items": {"type": "string"}},
        "court_observations": {"type": "array", "items": {"type": "string"}},
        "court_reasoning": {"type": "array", "items": {"type": "string"}},
        "decisions": {"type": "array", "items": {"type": "string"}},
        "important_dates": {"type": "array", "items": {"type": "string"}},
        "entities": {"type": "array", "items": {"type": "string"}},
        "uncertainty": {"type": "string"},
        "confidence": {"type": "number"},
    },
}


def _gemini_provider(model: str, temperature: float, max_output_tokens: int, timeout: int, rpm: int, mistral_free_only: bool = False) -> LlmProvider:
    # Validate config before creating closure
    api_key = os.getenv("GEMINI_API_KEY") or _get_settings_key("gemini_api_key")
    if not api_key:
        # Return a provider that fails clearly on call, not at factory time for import safety
        def _missing(prompts: list[str]) -> list[dict]:
            raise ConfigurationError("GEMINI_API_KEY is not configured for gemini provider")
        return _missing

    def _call(prompts: list[str]) -> list[dict]:
        import google.genai as genai  # type: ignore
        from google.genai import types  # type: ignore

        client = genai.Client(api_key=api_key)
        results: list[dict] = []

        for prompt in prompts:
            _throttle("gemini", rpm)

            @retry(
                stop=stop_after_attempt(3),
                wait=wait_exponential_jitter(initial=1, max=10),
                retry=retry_if_exception_type((RateLimitError, ServerError)),
                reraise=True,
            )
            def _one(p=prompt):
                try:
                    # Disable AFC (warning) and request structured JSON via schema
                    resp = client.models.generate_content(
                        model=model,
                        contents=p,
                        config=types.GenerateContentConfig(
                            temperature=temperature,
                            max_output_tokens=max_output_tokens,
                            response_mime_type="application/json",
                            response_schema=_GEMINI_RESPONSE_SCHEMA,
                            automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True),
                        ),
                    )
                    # Prefer parsed structured output if available
                    if hasattr(resp, "parsed") and resp.parsed is not None:
                        data = resp.parsed
                        if isinstance(data, dict):
                            return _normalize_response(data)
                        # Pydantic may return object with model_dump
                        if hasattr(data, "model_dump"):
                            return _normalize_response(data.model_dump())
                        if hasattr(data, "dict"):
                            return _normalize_response(data.dict())
                    text = resp.text or ""
                    if not text.strip():
                        raise ServerError("Gemini returned empty response")
                    # Try parse JSON; if wrapped, extract
                    try:
                        data = json.loads(text)
                        if isinstance(data, dict):
                            return _normalize_response(data)
                        return _normalize_response({"facts": [str(data)]})
                    except json.JSONDecodeError:
                        # Fallback: try to extract JSON block
                        start = text.find("{")
                        end = text.rfind("}")
                        if start != -1 and end != -1:
                            return _normalize_response(json.loads(text[start : end + 1]))
                        raise ServerError(f"Gemini returned non-JSON: {text[:500]}")
                except Exception as e:
                    msg = str(e).lower()
                    if "429" in msg or "rate" in msg and "limit" in msg:
                        raise RateLimitError(str(e)) from e
                    if "401" in msg or "api key" in msg or "authentication" in msg or "unauthenticated" in msg:
                        raise AuthenticationError(str(e)) from e
                    if "500" in msg or "503" in msg or "internal" in msg:
                        raise ServerError(str(e)) from e
                    # For timeout/network, treat as ServerError for retry
                    if "timeout" in msg or "timed out" in msg:
                        raise ServerError(str(e)) from e
                    raise

            results.append(_one())
        return results

    return _call


# --- Mistral provider ---

def _mistral_provider(model: str, temperature: float, max_output_tokens: int, timeout: int, rpm: int, free_mode_only: bool) -> LlmProvider:
    api_key = os.getenv("MISTRAL_API_KEY") or _get_settings_key("mistral_api_key")
    if not api_key:
        def _missing(prompts: list[str]) -> list[dict]:
            raise ConfigurationError("MISTRAL_API_KEY is not configured for mistral provider")
        return _missing

    def _call(prompts: list[str]) -> list[dict]:
        try:
            from mistralai.client import Mistral  # type: ignore
        except ImportError:
            from mistralai import Mistral  # type: ignore  # fallback

        client = Mistral(api_key=api_key)
        results: list[dict] = []
        for prompt in prompts:
            _throttle("mistral", rpm)

            @retry(
                stop=stop_after_attempt(3),
                wait=wait_exponential_jitter(initial=1, max=10),
                retry=retry_if_exception_type((RateLimitError, ServerError)),
                reraise=True,
            )
            def _one(p=prompt):
                try:
                    resp = client.chat.complete(
                        model=model,
                        messages=[{"role": "user", "content": p}],
                        temperature=temperature,
                        max_tokens=max_output_tokens,
                        response_format={"type": "json_object"},
                    )
                    content = resp.choices[0].message.content or ""
                    # mistral may return string JSON
                    if isinstance(content, str):
                        try:
                            data = json.loads(content)
                        except json.JSONDecodeError:
                            start = content.find("{")
                            end = content.rfind("}")
                            if start != -1 and end != -1:
                                data = json.loads(content[start : end + 1])
                            else:
                                raise ServerError(f"Mistral returned non-JSON: {content[:200]}")
                    else:
                        data = content
                    if not isinstance(data, dict):
                        raise ServerError(f"Mistral returned non-dict JSON: {data}")
                    data = _normalize_response(data)
                    # Free-mode guard: detect free allowance exhausted message
                    if free_mode_only and isinstance(data, dict):
                        # Check response metadata if present; otherwise rely on 429 handling above
                        pass
                    return data
                except Exception as e:
                    msg = str(e).lower()
                    # Detect free allowance exhausted
                    if free_mode_only and ("free" in msg and ("exhausted" in msg or "quota" in msg or "experiment" in msg)):
                        raise ConfigurationError(f"Mistral free allowance (1B) exhausted — set mistral_free_mode_only=False to allow paid usage: {e}") from e
                    if "429" in msg or "rate" in msg:
                        raise RateLimitError(str(e)) from e
                    if "401" in msg or "api key" in msg or "authentication" in msg or "unauthorized" in msg:
                        raise AuthenticationError(str(e)) from e
                    if "500" in msg or "503" in msg:
                        raise ServerError(str(e)) from e
                    if "timeout" in msg:
                        raise ServerError(str(e)) from e
                    raise

            results.append(_one())
        return results

    return _call


def _get_settings_key(name: str) -> str | None:
    try:
        from backend.app.config import get_settings

        s = get_settings()
        return getattr(s, name, None)
    except Exception:
        return None


def get_llm_provider(name: str, model: str | None = None) -> LlmProvider:
    """Return provider callable. Engine must not branch on name."""
    from backend.app.config import get_settings

    settings = get_settings()
    # Use passed model or config fallback — with provider-specific defaults for real APIs
    # Default llm_model is "fake-json" for fake provider; real providers default to current $0 models
    if model is None:
        if name == "gemini":
            model = "gemini-3.5-flash-lite"
        elif name == "mistral":
            model = "mistral-small-latest"
        else:
            model = settings.llm_model
    elif model == "fake-json" and name in ("gemini", "mistral"):
        # Config still holds fake default but provider explicitly selected — use real default
        if name == "gemini":
            model = "gemini-3.5-flash-lite"
        elif name == "mistral":
            model = "mistral-small-latest"
    else:
        model = model or settings.llm_model
    temp = settings.llm_temperature
    max_tokens = settings.llm_max_output_tokens
    timeout = settings.llm_timeout
    # Per-provider RPM from config
    gemini_rpm = settings.llm_gemini_rpm
    mistral_rpm = settings.llm_mistral_rpm

    if name == "fake":
        return _fake_provider
    if name == "gemini":
        return _gemini_provider(model, temp, max_tokens, timeout, gemini_rpm, settings.mistral_free_mode_only)
    if name == "mistral":
        return _mistral_provider(model, temp, max_tokens, timeout, mistral_rpm, settings.mistral_free_mode_only)
    if name == "claude":
        def _claude_stub(prompts: list[str]) -> list[dict]:
            raise ConfigurationError(
                "Claude provider is architecture-ready but no qualifying $0 API is currently available — "
                "real Claude requires paid ANTHROPIC_API_KEY (trial credits expire). "
                "Add anthropic SDK and set llm_provider='claude' with paid key when ready."
            )
        return _claude_stub
    raise ValueError(f"Unknown LLM provider: {name}")
