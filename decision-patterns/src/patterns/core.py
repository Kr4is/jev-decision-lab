"""Tiny typed-decision toolkit shared by the patterns.

`Decider.decide(state, questions)` has the same contract as the System One wire format, so the same
pattern code runs against TypeSafe's Jev, any open server that copies `/v1/systemone` (Kev, Laya's
`laya-serve`, ...), or a scripted offline stand-in used by the tests.
"""

from __future__ import annotations

import json
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class Q:
    kind: str  # "noul" | "choice" | "score"
    instructions: str
    criteria: dict[str, str] | list[str] | None = None

    def wire(self) -> dict:
        d = {"type": self.kind, "instructions": self.instructions}
        if self.criteria is not None:
            d["criteria"] = self.criteria
        return d


@dataclass
class A:
    kind: str
    value: float | str  # noul: P(true) | choice: label | score: expected level
    probs: dict[str, float] | None
    confidence: float  # 0..1, higher = surer


def noul_confidence(p: float) -> float:
    """Distance from 0.5, rescaled: 0.5 means 'can't tell', not 'medium'."""
    return abs(2 * p - 1)


def choice_confidence(probs: dict[str, float]) -> float:
    """(N*pmax - 1)/(N - 1): how concentrated the distribution is. Independent tests found Jev's own
    Choice confidence follows this formula of the top probability, so it adds no information on top of it."""
    n = len(probs)
    return (n * max(probs.values()) - 1) / (n - 1) if n > 1 else 1.0


def noul(p: float) -> A:
    return A("noul", p, None, noul_confidence(p))


def choice(probs: dict[str, float]) -> A:
    return A("choice", max(probs, key=probs.get), dict(probs), choice_confidence(probs))


class Decider(Protocol):
    def decide(self, state: str, questions: dict[str, Q]) -> dict[str, A]: ...


class ScriptedDecider:
    """Offline stand-in: one rule (callable) per question id. Lets the tests run with no API."""

    def __init__(self, rules: dict[str, Callable[[str], A]]):
        self.rules = rules
        self.calls = 0

    def decide(self, state: str, questions: dict[str, Q]) -> dict[str, A]:
        self.calls += 1
        return {qid: self.rules[qid](state) for qid in questions}


class SystemOneDecider:
    """Real thing: POST {base_url}/v1/systemone. Stdlib only. Response parsing is defensive."""

    def __init__(
        self, base_url: str | None = None, api_key: str | None = None, model: str | None = None, timeout: float = 30.0
    ):
        self.base_url = (base_url or "https://api.typesafe.ai").rstrip("/")
        self.api_key = api_key  # never read from the environment: whoever calls this passes the key
        self.model = model or "jev-1.13.0"  # pin: aliases like jev-latest move
        self.timeout = timeout

    def decide(self, state: str, questions: dict[str, Q]) -> dict[str, A]:
        payload = {"state": state, "model": self.model, "questions": {k: q.wire() for k, q in questions.items()}}
        headers = {"content-type": "application/json"}
        if self.api_key:
            headers["authorization"] = f"Bearer {self.api_key}"
        req = urllib.request.Request(f"{self.base_url}/v1/systemone", json.dumps(payload).encode(), headers)
        with urllib.request.urlopen(req, timeout=self.timeout) as r:
            body = json.loads(r.read())
        return parse(body.get("answers", body), questions)


def parse(answers: dict, questions: dict[str, Q]) -> dict[str, A]:
    out: dict[str, A] = {}
    for qid, q in questions.items():
        a = answers[qid]
        if q.kind == "noul":
            out[qid] = noul(float(a["noul"]))
        elif q.kind == "choice":
            raw = a.get("probabilities")
            labels = list(q.criteria or [])
            if isinstance(raw, list):
                raw = dict(zip(labels, raw))
            probs = {str(k): float(v) for k, v in (raw or {a["choice"]: 1.0}).items()}
            out[qid] = A("choice", a["choice"], probs, float(a.get("confidence", choice_confidence(probs))))
        else:  # score
            out[qid] = A("score", float(a["score"]), a.get("probabilities"), float(a.get("confidence", 1.0)))
    return out
