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
    """Normalize provider JSON to ChunkAnalysis/FileAnalysis-compatible dict.

    Handles Mistral variations and Phase 8 nested AnalysisItem:
    - list[str] stays, single string -> [string], list[dict] -> list[str], dict -> list[str]
    - For FileAnalysis (Phase 8): list[dict{text, source_refs}] stays, single dict -> [dict], etc.
    - uncertainty dict -> string
    Keeps provider-agnostic normalization in one place, backward compatible with Phase 7.
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
        "findings",
        "evidence",
    }

    def _is_analysis_item_dict(d: dict) -> bool:
        return isinstance(d, dict) and "text" in d and "source_refs" in d

    normalized: dict = {}
    for k, v in data.items():
        if k in SEMANTIC_LIST_FIELDS:
            if v is None:
                normalized[k] = None
            elif isinstance(v, str):
                # Single string -> single-item list (Phase 7) or single AnalysisItem for Phase 8?
                # For Phase 8, string without source_refs is wrapped as AnalysisItem with empty refs
                # Detect Phase 8 context: if caller expects AnalysisItem, string -> [{"text": str, "source_refs": []}]
                # Heuristic: if k is one of FileAnalysis fields, wrap; for ChunkAnalysis keep as [str]
                # We keep as [str] for backward compat; FileAnalysis will handle string->AnalysisItem in its own validation if needed
                normalized[k] = [v]
            elif isinstance(v, dict):
                # Check if dict is single AnalysisItem (has text/source_refs)
                if _is_analysis_item_dict(v):
                    normalized[k] = [v]
                else:
                    # Dict -> list of meaningful string values (Phase 7) or list of AnalysisItems?
                    # Try to extract values; if values are dicts with text/source_refs, keep them
                    vals: list = []
                    # If dict values are AnalysisItem-like, collect them
                    is_nested = any(isinstance(x, dict) and "text" in x for x in v.values())
                    if is_nested:
                        for vv in v.values():
                            if isinstance(vv, dict) and "text" in vv:
                                vals.append(vv)
                            elif isinstance(vv, list):
                                vals.extend(vv)
                            elif isinstance(vv, str) and vv.strip():
                                vals.append({"text": vv.strip(), "source_refs": []})
                    else:
                        for vv in v.values():
                            if isinstance(vv, str) and vv.strip():
                                vals.append(vv.strip())
                            elif isinstance(vv, list):
                                for item in vv:
                                    if isinstance(item, str):
                                        vals.append(item)
                                    elif isinstance(item, dict):
                                        if _is_analysis_item_dict(item):
                                            vals.append(item)
                                        else:
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
                    if not vals:
                        for vv in v.values():
                            if isinstance(vv, str):
                                vals.append(vv)
                    normalized[k] = vals if vals else None
            elif isinstance(v, list):
                # Detect Phase 8 nested AnalysisItem list
                if v and all(isinstance(item, dict) and "text" in item for item in v):
                    # Already list[AnalysisItem] shape — keep, but normalize source_refs
                    norm_list: list[dict] = []
                    for item in v:
                        # Normalize source_refs to list[str]
                        refs = item.get("source_refs", [])
                        if isinstance(refs, str):
                            refs = [refs]
                        elif isinstance(refs, dict):
                            refs = list(refs.values())
                        elif not isinstance(refs, list):
                            refs = []
                        # Ensure refs are strings
                        refs = [str(r) for r in refs if isinstance(r, (str, int)) or r is not None]
                        norm_list.append({"text": str(item.get("text", "")), "source_refs": refs})
                    normalized[k] = norm_list
                else:
                    norm_list: list[str] = []
                    for item in v:
                        if isinstance(item, dict):
                            # Check if it's AnalysisItem dict
                            if _is_analysis_item_dict(item):
                                norm_list.append(item)  # type: ignore — will be handled as list[dict] for Phase 8
                                # Actually for Phase 7, dict with description should be flattened
                                # Distinguish: if dict has source_refs, it's Phase 8; if has description, it's Phase 7
                                if "source_refs" in item:
                                    # Keep as AnalysisItem dict, not flattened
                                    # Remove from norm_list and add as dict to separate handling
                                    norm_list.pop()
                                    # Re-add as dict to preserve Phase 8
                                    if not isinstance(normalized.get(k), list) or (normalized.get(k) and isinstance(normalized[k][0], dict)):
                                        # Already handling as Phase 8, keep dict
                                        if k not in normalized or not isinstance(normalized[k], list):
                                            normalized[k] = []
                                        # This path shouldn't happen due to above check, but keep
                                        pass
                                    # For Phase 7 compatibility, flatten description
                                    val = item.get("description") or item.get("text") or item.get("content") or item.get("value") or item.get("name") or item.get("statement")
                                    if val is None:
                                        for vv in item.values():
                                            if isinstance(vv, str):
                                                val = vv
                                                break
                                    norm_list.append(str(val) if val is not None else str(item))
                                else:
                                    val = item.get("description") or item.get("text") or item.get("content") or item.get("value") or item.get("name") or item.get("statement")
                                    if val is None:
                                        for vv in item.values():
                                            if isinstance(vv, str):
                                                val = vv
                                                break
                                    norm_list.append(str(val) if val is not None else str(item))
                            else:
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
                    # If we detected Phase 8 list[dict] earlier, we already returned; otherwise use norm_list
                    if v and all(isinstance(item, dict) and "text" in item and "source_refs" in item for item in v):
                        # Keep as list[AnalysisItem] dicts, not flattened
                        normalized[k] = v  # type: ignore
                    else:
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
# Supports both ChunkAnalysis (flat list[str]) and FileAnalysis (nested AnalysisItem with source_refs)
_ANALYSIS_ITEM_SCHEMA = {
    "type": "object",
    "properties": {
        "text": {"type": "string"},
        "source_refs": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["text", "source_refs"],
}

_SEMANTIC_FIELD_SCHEMA = {
    "anyOf": [
        {"type": "array", "items": {"type": "string"}},
        {"type": "array", "items": _ANALYSIS_ITEM_SCHEMA},
    ]
}

_GEMINI_RESPONSE_SCHEMA = {
    "type": "object",
    "properties": {
        "facts": _SEMANTIC_FIELD_SCHEMA,
        "procedural_events": _SEMANTIC_FIELD_SCHEMA,
        "issues": _SEMANTIC_FIELD_SCHEMA,
        "arguments": _SEMANTIC_FIELD_SCHEMA,
        "counterarguments": _SEMANTIC_FIELD_SCHEMA,
        "evidence_mentioned": _SEMANTIC_FIELD_SCHEMA,
        "evidence": _SEMANTIC_FIELD_SCHEMA,
        "legal_provisions": _SEMANTIC_FIELD_SCHEMA,
        "court_observations": _SEMANTIC_FIELD_SCHEMA,
        "court_reasoning": _SEMANTIC_FIELD_SCHEMA,
        "findings": _SEMANTIC_FIELD_SCHEMA,
        "decisions": _SEMANTIC_FIELD_SCHEMA,
        "important_dates": _SEMANTIC_FIELD_SCHEMA,
        "entities": _SEMANTIC_FIELD_SCHEMA,
        "document_type": {"type": "string", "enum": ["petition", "reply", "affidavit", "evidence", "order", "judgment", "annexure", "unknown"]},
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
