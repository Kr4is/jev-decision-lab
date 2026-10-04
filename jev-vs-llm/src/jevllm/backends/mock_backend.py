from __future__ import annotations

import math
import re
import time

from ..schema import TEAMS, Decision

KEYWORDS = {
    "billing": [
        "charge",
        "charged",
        "invoice",
        "refund",
        "payment",
        "card",
        "subscription",
        "coupon",
        "plan",
        "vat",
        "billing",
        "receipt",
        "seat",
    ],
    "infra": [
        "deploy",
        "500",
        "latency",
        "down",
        "outage",
        "timeout",
        "times out",
        "backup",
        "webhook",
        "cron",
        "cdn",
        "dashboard",
        "memory",
        "region",
        "slow",
        "platform",
    ],
    "account": [
        "log in",
        "login",
        "password",
        "profile",
        "admin",
        "two-factor",
        "locked",
        "username",
        "sso",
        "email address",
        "account",
        "merge",
    ],
    "security": [
        "phishing",
        "api key",
        "leaked",
        "unauthorized",
        "scraping",
        "breach",
        "vulnerab",
        "soc 2",
        "penetration",
        "former employee",
        "rotate",
        "device",
        "never visited",
    ],
}
URGENT = [
    "now",
    "today",
    "right now",
    "minutes",
    "hours",
    "everyone",
    "entire",
    "twice",
    "public",
    "former employee",
    "never visited",
    "didn't sign in",
]
SEVERE = ["entire", "everyone", "public", "down", "500", "never visited", "former employee", "didn't sign in"]


class MockBackend:
    """Deterministic keyword scorer. Exists ONLY so the project runs with no API keys.
    Its numbers say nothing about Jev or any LLM."""

    name = "mock"

    def decide(self, text: str) -> Decision:
        t0 = time.perf_counter()
        low = text.lower()
        scores = {t: sum(low.count(k) for k in KEYWORDS[t]) + 0.1 for t in TEAMS}
        z = sum(math.exp(s) for s in scores.values())
        probs = {t: math.exp(s) / z for t, s in scores.items()}
        urgent = sum(bool(re.search(rf"\b{re.escape(k)}\b", low)) for k in URGENT)
        sev = min(2.0, float(sum(k in low for k in SEVERE)))
        return Decision(
            team=max(probs, key=probs.get),
            team_probs=probs,
            urgent_prob=min(1.0, 0.2 + 0.3 * urgent),
            severity=sev,
            latency_s=time.perf_counter() - t0,
        )
