"""Generic client for any server that speaks the `POST /v1/systemone` contract.

Works against TypeSafe's hosted Jev AND against open implementations that copy the wire format
(Kev, Laya's `laya-serve`, jevos, ...). Stdlib only: no extra dependency.

Request : {"state": str, "model": str, "questions": {id: {"type": "noul|choice|score", "instructions": str, "criteria": ...}}}
Response: {"answers": {id: {...}}} with `choice`/`probabilities`/`confidence`, `noul`, `score`.
The response parser is defensive because open servers differ in small details.
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request

from ..net import OPENER
from ..questions import SEVERITY_LEVELS, SEVERITY_Q, TEAM_CRITERIA, TEAM_Q, URGENT_Q
from ..schema import TEAMS, Decision

DEFAULT_MODEL = "jev-1.13.0"  # pinned: aliases like jev-latest move


def build_questions() -> dict:
    return {
        "urgent": {"type": "noul", "instructions": URGENT_Q},
        "team": {"type": "choice", "instructions": TEAM_Q, "criteria": dict(TEAM_CRITERIA)},
        "severity": {"type": "score", "instructions": SEVERITY_Q, "criteria": list(SEVERITY_LEVELS)},
    }


def parse_answers(body: dict) -> tuple[str, dict[str, float], float, float]:
    ans = body.get("answers", body)
    team = ans["team"]
    label = team["choice"]
    raw = team.get("probabilities")
    if isinstance(raw, dict) and raw:
        probs = {str(k): float(v) for k, v in raw.items()}
    elif isinstance(raw, list) and len(raw) == len(TEAMS):  # aligned with criteria order
        probs = {t: float(p) for t, p in zip(TEAMS, raw)}
    else:  # server gave no distribution: spread what's left of `confidence`
        c = float(team.get("confidence", 1.0))
        probs = {t: (c if t == label else (1 - c) / (len(TEAMS) - 1)) for t in TEAMS}
    for t in TEAMS:
        probs.setdefault(t, 0.0)
    return label, probs, float(ans["urgent"]["noul"]), float(ans["severity"]["score"])


class SystemOneHTTPBackend:
    def __init__(
        self,
        name: str = "systemone",
        base_url: str | None = None,
        api_key: str | None = None,
        model: str | None = None,
        timeout: float = 30.0,
        price_in: float | None = None,
    ):
        self.name = name
        self.base_url = (base_url or "https://api.typesafe.ai").rstrip("/")
        self.api_key = api_key  # only ever what the caller passes: the web lab forwards the key the user typed
        self.model = model or DEFAULT_MODEL
        self.timeout = timeout
        self.price_known = price_in is not None  # USD per 1M input tokens; unknown is not free
        self.price_in = price_in or 0.0
        self._questions = build_questions()

    def _post(self, payload: dict) -> dict:
        headers = {"content-type": "application/json"}
        if self.api_key:
            headers["authorization"] = f"Bearer {self.api_key}"
        req = urllib.request.Request(f"{self.base_url}/v1/systemone", json.dumps(payload).encode(), headers)
        try:
            with OPENER.open(req, timeout=self.timeout) as r:  # never follows redirects
                return json.loads(r.read())
        except urllib.error.HTTPError as e:  # status only: the body of a user-typed server is not ours to echo
            raise RuntimeError(f"HTTP {e.code}") from e

    def decide(self, text: str) -> Decision:
        payload = {"state": text, "model": self.model, "questions": self._questions}
        t0 = time.perf_counter()
        body = self._post(payload)
        dt = time.perf_counter() - t0
        label, probs, urgent, severity = parse_answers(body)
        usage = body.get("usage") or {}
        tok = usage.get("input_tokens") if isinstance(usage, dict) else None
        est = not isinstance(tok, int)
        if est:
            tok = max(1, len(json.dumps(payload)) // 4)
        return Decision(
            team=label,
            team_probs=probs,
            urgent_prob=urgent,
            severity=severity,
            latency_s=dt,
            input_tokens=tok,
            cost_usd=tok * self.price_in / 1e6,
            cost_known=self.price_known,
            tokens_estimated=est,
            raw={"server": self.base_url, "model": self.model},
        )
