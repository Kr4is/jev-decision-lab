from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

TEAMS = ("billing", "infra", "account", "security")


@dataclass
class Decision:
    """Backend-agnostic result of triaging one ticket."""

    team: str
    team_probs: dict[str, float]
    urgent_prob: float  # P(needs attention now). LLM backend can only give 0.0 / 1.0.
    severity: float  # 0..2 (Jev: probability-weighted position, so fractional)
    latency_s: float = 0.0
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float = 0.0
    tokens_estimated: bool = False
    cost_known: bool = True  # False when no price is configured (cost_usd is then 0, not free)
    raw: Any = field(default=None, repr=False)

    @property
    def confidence(self) -> float:
        return max(self.team_probs.values())
