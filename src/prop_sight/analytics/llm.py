"""Shared Groq client.

Every LLM feature in this app is strictly optional: without GROQ_API_KEY the
app must still ingest files and render every chart. So `chat()` never raises —
it returns None on a missing key, a network error, a rate limit, or malformed
output, and each caller supplies its own non-LLM fallback.
"""

from __future__ import annotations

import json
import logging
import os
import re

import requests

GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"
DEFAULT_MODEL = "llama-3.3-70b-versatile"
# Groq applies rate limits per model per API key, not per account — so a 429 on
# the primary model still leaves the fallbacks' quotas untouched. Tried in order.
FALLBACK_MODELS = ["llama-3.1-8b-instant", "gemma2-9b-it"]

_JSON_BLOCK = re.compile(r"(\{.*\}|\[.*\])", re.DOTALL)


def is_enabled() -> bool:
    return bool(os.environ.get("GROQ_API_KEY", "").strip())


def _call_model(api_key: str, model: str, prompt: str, max_tokens: int, temperature: float):
    """One request to one model. Returns (content, error_kind); error_kind "rate_limit" is retryable."""
    try:
        resp = requests.post(
            GROQ_URL,
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            json={
                "model": model,
                "messages": [{"role": "user", "content": prompt}],
                "temperature": temperature,
                "max_tokens": max_tokens,
            },
            timeout=60,
        )
        if resp.status_code == 429:
            return None, "rate_limit"
        resp.raise_for_status()
        return resp.json()["choices"][0]["message"]["content"], None
    except Exception:
        logging.getLogger(__name__).warning("Groq call failed (model=%s)", model, exc_info=True)
        return None, "error"


def chat_status(prompt: str, *, max_tokens: int = 2048, temperature: float = 0.3) -> tuple[str | None, str | None]:
    """Single-turn completion. Returns (content, error_kind) instead of raising.

    error_kind is None on success, else one of "no_key", "rate_limit", "error" —
    callers that need to tell a transient rate limit apart from a hard failure
    (to word the message differently, or avoid caching it) should use this
    directly; chat() below is the simple never-raises wrapper for callers that
    don't care why it failed.

    Groq rate-limits each model on a key separately, so a 429 on the primary
    model is retried against FALLBACK_MODELS (each with its own quota) before
    giving up — "rate_limit" is only returned once every model has been tried.
    """
    api_key = os.environ.get("GROQ_API_KEY", "").strip()
    if not api_key:
        return None, "no_key"

    models = [os.environ.get("GROQ_MODEL", DEFAULT_MODEL), *FALLBACK_MODELS]
    last_err = "rate_limit"
    for model in models:
        content, err = _call_model(api_key, model, prompt, max_tokens, temperature)
        if err is None:
            return content, None
        last_err = err
        if err == "rate_limit":
            logging.getLogger(__name__).warning("Groq rate limited on %s, trying next model", model)
            continue
        return None, err  # a hard error isn't model-specific quota — no point retrying other models
    return None, last_err


def chat(prompt: str, *, max_tokens: int = 2048, temperature: float = 0.3) -> str | None:
    """Single-turn completion. Returns None instead of raising, on any failure."""
    content, _ = chat_status(prompt, max_tokens=max_tokens, temperature=temperature)
    return content


def _extract_json(content: str | None):
    if not content:
        return None
    try:
        return json.loads(content)
    except Exception:
        pass
    match = _JSON_BLOCK.search(content)
    if not match:
        return None
    try:
        return json.loads(match.group(1))
    except Exception:
        return None


def chat_json(prompt: str, **kwargs):
    """chat() + tolerant JSON extraction (models like to wrap output in prose).

    Returns the parsed object, or None if nothing parseable came back.
    """
    return _extract_json(chat(prompt, **kwargs))


def chat_json_status(prompt: str, **kwargs) -> tuple[object | None, str | None]:
    """chat_status() + tolerant JSON extraction. See chat_status for error_kind."""
    content, err = chat_status(prompt, **kwargs)
    if err:
        return None, err
    parsed = _extract_json(content)
    return parsed, (None if parsed is not None else "unparseable")
