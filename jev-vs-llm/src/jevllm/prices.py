"""Model prices (USD per 1M tokens) from a public list, so nobody has to maintain a price file.

Source: LiteLLM's open price list, which cites each vendor's own pricing page for every entry (OpenAI's pricing
page, docs.typesafe.ai for JEV...). It is fetched on first use and refreshed once a day. If it cannot be reached,
or a model is not in it, the price is unknown and the page says so: the user can type a price for that model.
"""

from __future__ import annotations

import threading
import time

from .net import fetch_json

URL = "https://raw.githubusercontent.com/BerriAI/litellm/main/model_prices_and_context_window.json"
TTL, RETRY = 24 * 3600, 300  # refresh after a day; after a failed fetch try again in five minutes

_lock = threading.Lock()
_table: dict[str, tuple[float, float]] = {}
_until = 0.0


def _load() -> dict[str, tuple[float, float]]:
    raw = fetch_json(URL, timeout=20, max_bytes=20_000_000)
    return {
        k: (float(v["input_cost_per_token"]) * 1e6, float(v.get("output_cost_per_token") or 0) * 1e6)
        for k, v in raw.items()
        if isinstance(v, dict) and isinstance(v.get("input_cost_per_token"), int | float)
    }


def table() -> dict[str, tuple[float, float]]:
    global _table, _until
    with _lock:  # one download at a time, however many lanes ask at once
        if time.time() >= _until:
            try:
                _table, _until = _load(), time.time() + TTL
            except Exception:
                _until = time.time() + RETRY
    return _table


def lookup(kind: str, model: str) -> tuple[float, float] | None:
    """(input, output) USD per 1M tokens, or None when the public list has no entry for this model."""
    names = [f"typesafe/{model}", model] if kind == "jev" else [model, f"openai/{model}", model.rsplit("/", 1)[-1]]
    t = table()
    return next((t[n] for n in names if n in t), None)
