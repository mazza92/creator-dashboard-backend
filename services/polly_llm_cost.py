"""Price Polly LLM turns from provider usage, not Google's invoice.

The Gemini API key has no billing-read endpoint. generateContent already
returns usageMetadata (token counts). We convert those with current
standard paid-tier rates.
"""

from __future__ import annotations

from typing import Any, Dict, Optional

# USD per 1M tokens — Standard paid tier, text. Thinking billed as output.
# https://ai.google.dev/gemini-api/docs/pricing
_GEMINI_RATES = {
    "gemini-2.5-flash": (0.30, 2.50),
    "gemini-2.5-flash-lite": (0.10, 0.40),
    "gemini-3.5-flash": (1.50, 9.00),
    "gemini-3.5-flash-lite": (0.30, 2.50),
    "gemini-3.6-flash": (0.75, 3.75),
}

# https://www.anthropic.com/pricing
_ANTHROPIC_RATES = {
    "claude-haiku-4-5": (1.00, 5.00),
    "claude-haiku-4.5": (1.00, 5.00),
    "claude-sonnet-4-5": (3.00, 15.00),
    "claude-sonnet-4.5": (3.00, 15.00),
}

_DEFAULT_GEMINI = (0.30, 2.50)
_DEFAULT_ANTHROPIC = (1.00, 5.00)


def _rates(provider: str, model: Optional[str]) -> tuple:
    name = (model or "").strip().lower()
    if (provider or "").lower() == "anthropic":
        for key, pair in _ANTHROPIC_RATES.items():
            if key in name:
                return pair
        return _DEFAULT_ANTHROPIC
    for key, pair in _GEMINI_RATES.items():
        if name == key or name.startswith(key):
            return pair
    return _DEFAULT_GEMINI


def estimate_usd(
    provider: str,
    model: Optional[str],
    input_tokens: int = 0,
    output_tokens: int = 0,
    cached_tokens: int = 0,
) -> float:
    inp_rate, out_rate = _rates(provider, model)
    # Cached Gemini tokens are ~10% of input when used; Polly does not cache yet.
    cache_rate = inp_rate * 0.1
    usd = (
        max(0, int(input_tokens or 0)) * inp_rate
        + max(0, int(cached_tokens or 0)) * cache_rate
        + max(0, int(output_tokens or 0)) * out_rate
    ) / 1_000_000
    return round(usd, 8)


def _int(val: Any) -> int:
    try:
        return max(0, int(val or 0))
    except (TypeError, ValueError):
        return 0


def usage_from_gemini(data: Optional[Dict], model: str) -> Dict[str, Any]:
    meta = (data or {}).get("usageMetadata") or {}
    prompt = _int(meta.get("promptTokenCount"))
    cached = _int(meta.get("cachedContentTokenCount"))
    thoughts = _int(meta.get("thoughtsTokenCount"))
    candidates = _int(meta.get("candidatesTokenCount"))
    input_tokens = max(0, prompt - cached)
    output_tokens = candidates + thoughts
    return {
        "provider": "gemini",
        "model": model,
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "cached_tokens": cached,
        "thought_tokens": thoughts,
        "usd": estimate_usd("gemini", model, input_tokens, output_tokens, cached),
    }


def usage_from_anthropic(data: Optional[Dict], model: str) -> Dict[str, Any]:
    meta = (data or {}).get("usage") or {}
    input_tokens = _int(meta.get("input_tokens"))
    output_tokens = _int(meta.get("output_tokens"))
    cached = _int(meta.get("cache_read_input_tokens"))
    return {
        "provider": "anthropic",
        "model": model,
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "cached_tokens": cached,
        "thought_tokens": 0,
        "usd": estimate_usd("anthropic", model, input_tokens, output_tokens, cached),
    }


def rollup_usage(rows: Optional[list]) -> Dict[str, Any]:
    usd = 0.0
    inp = out = cached = calls = 0
    model = None
    provider = None
    for row in rows or []:
        if not isinstance(row, dict):
            continue
        usd += float(row.get("usd") or 0)
        inp += _int(row.get("input_tokens"))
        out += _int(row.get("output_tokens"))
        cached += _int(row.get("cached_tokens"))
        calls += 1
        provider = row.get("provider") or provider
        model = row.get("model") or model
    return {
        "usd": round(usd, 6),
        "input_tokens": inp,
        "output_tokens": out,
        "cached_tokens": cached,
        "calls": calls,
        "provider": provider,
        "model": model,
        "source": "response_usage",
    }
