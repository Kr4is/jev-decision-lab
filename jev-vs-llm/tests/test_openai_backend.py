"""OpenAI adapter against a stub client. Does NOT prove the live API shape: run one real comparison with your key."""

import json
import sys
import types
from types import SimpleNamespace as NS


def _install_fake_openai(monkeypatch, content, refusal=None):
    mod = types.ModuleType("openai")
    seen = {}

    class Completions:
        def create(self, **kw):
            seen.update(kw)
            msg = NS(content=content, refusal=refusal)
            return NS(choices=[NS(message=msg)], usage=NS(prompt_tokens=200, completion_tokens=30), model=kw["model"])

    class OpenAI:
        def __init__(self, **kw):
            seen["client"] = kw
            self.chat = NS(completions=Completions())

    mod.OpenAI = OpenAI
    monkeypatch.setitem(sys.modules, "openai", mod)
    return seen


def test_openai_backend_maps_structured_output(monkeypatch):
    seen = _install_fake_openai(
        monkeypatch, json.dumps({"team": "security", "urgent": True, "severity": 2, "confidence": 1.4})
    )
    from jevllm.backends.openai_backend import OpenAIBackend

    d = OpenAIBackend("m1", api_key="k", price=(1.0, 4.0)).decide("leaked key")
    assert d.team == "security" and d.confidence == 1.0  # clamped to [0,1]
    assert abs(sum(d.team_probs.values()) - 1) < 1e-9 and d.urgent_prob == 1.0 and d.severity == 2.0
    assert d.input_tokens == 200 and d.output_tokens == 30
    assert abs(d.cost_usd - (200 * 1.0 + 30 * 4.0) / 1e6) < 1e-12 and d.cost_known
    rf = seen["response_format"]["json_schema"]
    assert rf["strict"] is True and rf["schema"]["additionalProperties"] is False
    assert "<ticket>" in seen["messages"][1]["content"]


def test_unknown_price_is_unknown_not_free(monkeypatch):
    _install_fake_openai(monkeypatch, json.dumps({"team": "infra", "urgent": False, "severity": 0, "confidence": 0.5}))
    from jevllm.backends.openai_backend import OpenAIBackend

    d = OpenAIBackend("whatever", api_key="k").decide("x")
    assert d.cost_known is False and d.cost_usd == 0.0


def test_refusal_becomes_an_error(monkeypatch):
    _install_fake_openai(monkeypatch, None, refusal="I can't help with that")
    import pytest
    from jevllm.backends.openai_backend import OpenAIBackend

    with pytest.raises(ValueError, match="refused"):
        OpenAIBackend("m", api_key="k").decide("x")


def test_litellm_vendor_uses_its_base_url_and_the_price_it_is_given(monkeypatch):
    seen = _install_fake_openai(
        monkeypatch, json.dumps({"team": "infra", "urgent": False, "severity": 1, "confidence": 0.9})
    )
    from jevllm.backends.openai_backend import OpenAIBackend

    be = OpenAIBackend(
        "gpt-6-luna", api_key="sk-lite", base_url="http://litellm:4000", vendor="litellm", price=(2.0, 8.0)
    )
    d = be.decide("x")
    assert be.name == "litellm:gpt-6-luna" and seen["client"]["base_url"] == "http://litellm:4000"
    assert seen["client"]["api_key"] == "sk-lite"
    assert seen["client"]["http_client"].follow_redirects is False  # a checked URL must not bounce elsewhere
    # a proxy that caches would answer in milliseconds and ruin the latency comparison: ask it to skip the cache
    assert seen["extra_body"] == {"cache": {"no-cache": True, "no-store": True}}
    assert d.cost_known and abs(d.cost_usd - (200 * 2.0 + 30 * 8.0) / 1e6) < 1e-12


def test_openai_itself_never_gets_the_litellm_cache_controls(monkeypatch):
    seen = _install_fake_openai(
        monkeypatch, json.dumps({"team": "infra", "urgent": False, "severity": 1, "confidence": 0.9})
    )
    from jevllm.backends.openai_backend import OpenAIBackend

    OpenAIBackend("gpt-6-luna", api_key="k").decide("x")
    assert "extra_body" not in seen  # OpenAI rejects request fields it does not know
