"""Estimated token pricing for the Usage Inspector.

Prices are USD per 1M tokens and are estimates for cost transparency only —
the source of truth is always the provider invoice. Users can override or
extend rates via ``model_pricing`` in config.yaml:

    model_pricing:
      my-model-prefix:
        input_per_m: 1.0
        cached_input_per_m: 0.1
        output_per_m: 5.0
"""

from __future__ import annotations

from typing import Any

# (prefix, input, cached_input, output) USD per 1M tokens.
_BUILTIN_RATES: list[tuple[str, float, float, float]] = [
    ("deepseek-reasoner", 0.55, 0.14, 2.19),
    ("deepseek-chat", 0.27, 0.07, 1.10),
    ("deepseek", 0.27, 0.07, 1.10),
    ("claude-opus-4", 15.0, 1.5, 75.0),
    ("claude-sonnet-4-5", 3.0, 0.3, 15.0),
    ("claude-sonnet-4", 3.0, 0.3, 15.0),
    ("claude-3-7-sonnet", 3.0, 0.3, 15.0),
    ("claude-3-5-sonnet", 3.0, 0.3, 15.0),
    ("claude-3-5-haiku", 0.8, 0.08, 4.0),
    ("claude-3-haiku", 0.25, 0.03, 1.25),
    ("gpt-5-mini", 0.25, 0.025, 2.0),
    ("gpt-5", 1.25, 0.125, 10.0),
    ("gpt-4.1-mini", 0.4, 0.1, 1.6),
    ("gpt-4.1", 2.0, 0.5, 8.0),
    ("gpt-4o-mini", 0.15, 0.075, 0.6),
    ("gpt-4o", 2.5, 1.25, 10.0),
    ("o4-mini", 1.1, 0.275, 4.4),
    ("o3", 2.0, 0.5, 8.0),
    ("glm-4.6", 0.6, 0.06, 2.2),
    ("glm-4.5", 0.6, 0.06, 2.2),
    ("qwen-max", 1.6, 0.16, 6.4),
    ("qwen-plus", 0.4, 0.04, 1.2),
    ("kimi-k2", 0.6, 0.06, 2.5),
    ("gemini-2.5-pro", 1.25, 0.31, 10.0),
    ("gemini-2.5-flash", 0.30, 0.075, 2.5),
]

_extra_rates: dict[str, tuple[float, float, float]] = {}


def configure_rates(overrides: dict[str, Any] | None) -> None:
    """Merge user-supplied ``model_pricing`` overrides from config.yaml."""
    _extra_rates.clear()
    if not isinstance(overrides, dict):
        return
    for prefix, rates in overrides.items():
        if not isinstance(rates, dict):
            continue
        try:
            entry = (
                float(rates["input_per_m"]),
                float(rates.get("cached_input_per_m", rates["input_per_m"])),
                float(rates["output_per_m"]),
            )
        except (KeyError, TypeError, ValueError):
            continue
        _extra_rates[str(prefix).strip().lower()] = entry


def lookup_rates(model: str | None) -> tuple[float, float, float] | None:
    name = (model or "").strip().lower()
    if not name:
        return None
    best: tuple[str, tuple[float, float, float]] | None = None
    for prefix, rates in _extra_rates.items():
        if name.startswith(prefix) and (best is None or len(prefix) > len(best[0])):
            best = (prefix, rates)
    for prefix, input_per_m, cached_per_m, output_per_m in _BUILTIN_RATES:
        if name.startswith(prefix) and (best is None or len(prefix) > len(best[0])):
            best = (prefix, (input_per_m, cached_per_m, output_per_m))
    return best[1] if best else None


def estimate_cost(
    model: str | None,
    *,
    input_tokens: int | float | None,
    output_tokens: int | float | None,
    cached_tokens: int | float | None = 0,
    cache_creation_tokens: int | float | None = 0,
) -> float | None:
    """Return an estimated USD cost, or None when the model is not priced."""
    rates = lookup_rates(model)
    if rates is None:
        return None
    input_per_m, cached_per_m, output_per_m = rates
    try:
        inp = max(0.0, float(input_tokens or 0))
        out = max(0.0, float(output_tokens or 0))
        cached = max(0.0, float(cached_tokens or 0))
        cache_create = max(0.0, float(cache_creation_tokens or 0))
    except (TypeError, ValueError):
        return None
    cached = min(cached, inp)
    fresh_input = inp - cached
    cost = (
        fresh_input * input_per_m / 1_000_000
        + cached * cached_per_m / 1_000_000
        + cache_create * input_per_m / 1_000_000
        + out * output_per_m / 1_000_000
    )
    return round(cost, 6)


def cost_available(model: str | None) -> bool:
    return lookup_rates(model) is not None
