"""OpenAI chat models behind the same Backend contract, using strict structured outputs.

Works with any model id your account can call (set them in OPENAI_MODELS or type one in the UI). Any
OpenAI-compatible endpoint works too: pass `base_url` and `vendor` (LiteLLM proxy -> vendor="litellm").
Confidence is the model's *verbalized* confidence, not a logit probability: that asymmetry with
Jev's probabilities is part of what the comparison shows.
"""

from __future__ import annotations

import json
import time

import httpx

from ..questions import llm_system_prompt
from ..schema import TEAMS, Decision

SCHEMA = {
    "type": "object",
    "properties": {
        "team": {"type": "string", "enum": list(TEAMS)},
        "urgent": {"type": "boolean"},
        "severity": {"type": "integer", "enum": [0, 1, 2]},
        "confidence": {"type": "number"},
    },
    "required": ["team", "urgent", "severity", "confidence"],
    "additionalProperties": False,
}


class OpenAIBackend:
    def __init__(
        self,
        model: str,
        api_key: str,
        base_url: str | None = None,
        vendor: str = "openai",
        price: tuple[float, float] | None = None,
    ):
        try:
            from openai import OpenAI
        except ImportError as e:  # pragma: no cover
            raise RuntimeError("Install the extra: pip install -e '.[openai]'") from e
        self.model = model
        self.name = f"{vendor}:{model}"
        # redirects are never followed: a user-typed base_url was checked once, and must not bounce elsewhere
        self.client = OpenAI(
            api_key=api_key, base_url=base_url, http_client=httpx.Client(follow_redirects=False, timeout=60)
        )
        self.vendor = vendor
        self.price_known = price is not None  # unknown is not free: the page shows "no price set"
        self.price_in, self.price_out = price or (0.0, 0.0)

    def decide(self, text: str) -> Decision:
        messages = [
            {"role": "system", "content": llm_system_prompt()},
            {"role": "user", "content": f"<ticket>\n{text}\n</ticket>"},
        ]
        t0 = time.perf_counter()
        # A LiteLLM proxy may cache identical requests: a cache hit answers in milliseconds, which would make a
        # latency comparison meaningless. Ask it (documented per-request controls) to skip the cache and not store.
        extra = {"extra_body": {"cache": {"no-cache": True, "no-store": True}}} if self.vendor == "litellm" else {}
        resp = self.client.chat.completions.create(
            model=self.model,
            messages=messages,
            **extra,
            response_format={
                "type": "json_schema",
                "json_schema": {"name": "triage", "strict": True, "schema": SCHEMA},
            },
        )
        dt = time.perf_counter() - t0
        msg = resp.choices[0].message
        if getattr(msg, "refusal", None):
            raise ValueError(f"model refused: {msg.refusal[:120]}")
        data = json.loads(msg.content)
        conf = min(max(float(data["confidence"]), 0.0), 1.0)
        rest = (1 - conf) / (len(TEAMS) - 1)
        probs = {t: (conf if t == data["team"] else rest) for t in TEAMS}
        usage = getattr(resp, "usage", None)
        tin = int(getattr(usage, "prompt_tokens", 0) or 0)
        tout = int(getattr(usage, "completion_tokens", 0) or 0)
        return Decision(
            team=data["team"],
            team_probs=probs,
            urgent_prob=1.0 if data["urgent"] else 0.0,
            severity=float(data["severity"]),
            latency_s=dt,
            input_tokens=tin,
            output_tokens=tout,
            cost_usd=tin * self.price_in / 1e6 + tout * self.price_out / 1e6,
            cost_known=self.price_known,
            raw={"model": getattr(resp, "model", self.model)},
        )
