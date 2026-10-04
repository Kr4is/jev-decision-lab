import io
import json

from patterns import cascade, router, tool_gate
from patterns.core import (
    Q,
    ScriptedDecider,
    SystemOneDecider,
    choice,
    choice_confidence,
    noul,
    noul_confidence,
    parse,
)


def test_confidence_formulas():
    assert noul_confidence(0.5) == 0 and noul_confidence(1.0) == 1 and abs(noul_confidence(0.8) - 0.6) < 1e-9
    assert choice_confidence({"a": 0.25, "b": 0.25, "c": 0.25, "d": 0.25}) == 0
    assert choice_confidence({"a": 1.0, "b": 0.0}) == 1


def test_router_policy_escalates_one_tier_when_unsure():
    assert router.pick_tier("easy", 0.9, 0.5) == ("easy", False)
    assert router.pick_tier("easy", 0.2, 0.5) == ("medium", True)
    assert router.pick_tier("hard", 0.1, 0.5) == ("hard", False)  # nowhere higher to go


def test_router_graph_end_to_end():
    d = ScriptedDecider({"complexity": lambda s: choice({"easy": 0.4, "medium": 0.35, "hard": 0.25})})
    out = router.build_graph(d).invoke({"request": "x"})
    assert out["tier"] == "medium" and out["escalated"] and out["model"] == "mid-tier"


def _gate(risky, injected, call, **kw):
    pol = tool_gate.Policy(allowed_tools={"shell", "refund", "search"}, irreversible={"refund"}, **kw)
    d = ScriptedDecider({"risky": lambda s: noul(risky), "injected": lambda s: noul(injected)})
    return tool_gate.build_graph(d, pol).invoke({"call": call}), d


def test_gate_hard_checks_run_before_the_model():
    out, d = _gate(0.0, 0.0, tool_gate.ToolCall("wire", {}))
    assert out["verdict"] == "block" and d.calls == 0  # model never consulted
    out, d = _gate(0.0, 0.0, tool_gate.ToolCall("refund", {"amount_eur": 500}))
    assert out["verdict"] == "block" and "exceeds" in out["reasons"][0] and d.calls == 0


def test_gate_policy_thresholds():
    c = tool_gate.ToolCall("shell", {})
    assert _gate(0.05, 0.0, c)[0]["verdict"] == "allow"
    assert _gate(0.95, 0.0, c)[0]["verdict"] == "block"
    assert _gate(0.5, 0.0, c)[0]["verdict"] == "human"
    assert _gate(0.05, 0.9, c)[0]["verdict"] == "human"  # injection can only make it stricter
    assert _gate(0.05, 0.0, tool_gate.ToolCall("refund", {"amount_eur": 5}))[0]["verdict"] == "human"  # irreversible


def test_cascade_keeps_good_drafts_and_escalates_bad_ones():
    def rules(ps):
        return ScriptedDecider({"supported": lambda s: noul(ps), "on_topic": lambda s: noul(0.9)})

    kw = {"cheap": lambda t, e: "draft", "strong": lambda t, e: "strong"}
    ok = cascade.build_graph(rules(0.95), **kw).invoke({"task": "t", "evidence": "e"})
    bad = cascade.build_graph(rules(0.3), **kw).invoke({"task": "t", "evidence": "e"})
    assert ok["answer"] == "draft" and not ok["escalated"] and abs(ok["cost_units"] - 1.05) < 1e-9
    assert bad["answer"] == "strong" and bad["escalated"] and abs(bad["cost_units"] - 31.05) < 1e-9


def test_parse_and_http_decider(monkeypatch):
    qs = {"c": Q("choice", "q", {"x": "", "y": ""}), "n": Q("noul", "q"), "s": Q("score", "q", ["a", "b"])}
    body = {
        "answers": {
            "c": {"choice": "y", "probabilities": {"x": 0.2, "y": 0.8}},
            "n": {"noul": 0.7},
            "s": {"score": 0.4},
        }
    }
    out = parse(body["answers"], qs)
    assert out["c"].value == "y" and abs(out["c"].confidence - choice_confidence({"x": 0.2, "y": 0.8})) < 1e-9
    assert out["n"].value == 0.7 and out["s"].value == 0.4

    class Resp(io.BytesIO):
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    seen = {}

    def fake(req, timeout=None):
        seen["url"], seen["body"] = req.full_url, json.loads(req.data)
        return Resp(json.dumps(body).encode())

    monkeypatch.setattr("urllib.request.urlopen", fake)
    res = SystemOneDecider(base_url="http://localhost:8009", model="kev-latest").decide("state", qs)
    assert seen["url"].endswith("/v1/systemone") and seen["body"]["questions"]["n"] == {
        "type": "noul",
        "instructions": "q",
    }
    assert res["c"].value == "y"
