"""Pattern 1 - MODEL ROUTER: pick the cheapest model that can handle the request.

Jev's own LangChain middleware does this (ModelRouterMiddleware). Here it is as a plain graph so the
policy is visible: the decision model only labels complexity; CODE decides what to do when it is unsure
(pay for the stronger tier). Prices are illustrative placeholders: set your real ones.
"""

from __future__ import annotations

from typing import TypedDict

from langgraph.graph import END, START, StateGraph

from .core import Decider, Q

TIERS = ("easy", "medium", "hard")
MODELS = {"easy": "small-fast", "medium": "mid-tier", "hard": "frontier"}
PRICE_PER_CALL_USD = {"small-fast": 0.0002, "mid-tier": 0.003, "frontier": 0.03}  # PLACEHOLDERS

QUESTIONS = {
    "complexity": Q(
        "choice",
        "How complex is the user's request?",
        {
            "easy": "Lookups, formatting, short factual answers, simple edits with explicit targets.",
            "medium": "Multi-step but well-specified work: summarising, drafting, modest refactors.",
            "hard": "Architecture, novel root-cause analysis, ambiguous or high-stakes decisions.",
        },
    )
}


class RouterState(TypedDict, total=False):
    request: str
    tier: str
    confidence: float
    escalated: bool
    model: str
    est_cost_usd: float


def pick_tier(label: str, confidence: float, threshold: float) -> tuple[str, bool]:
    """Policy in code: unsure -> go one tier up."""
    if confidence >= threshold or label == TIERS[-1]:
        return label, False
    return TIERS[TIERS.index(label) + 1], True


def build_graph(decider: Decider, threshold: float = 0.5):
    def classify(s: RouterState) -> RouterState:
        a = decider.decide(s["request"], QUESTIONS)["complexity"]
        tier, esc = pick_tier(str(a.value), a.confidence, threshold)
        return {"tier": tier, "confidence": a.confidence, "escalated": esc}

    def make(tier: str):
        def node(_: RouterState) -> RouterState:
            m = MODELS[tier]
            return {"model": m, "est_cost_usd": PRICE_PER_CALL_USD[m]}

        return node

    g = StateGraph(RouterState)
    g.add_node("classify", classify)
    for t in TIERS:
        g.add_node(f"run_{t}", make(t))
        g.add_edge(f"run_{t}", END)
    g.add_edge(START, "classify")
    g.add_conditional_edges("classify", lambda s: f"run_{s['tier']}", {f"run_{t}": f"run_{t}" for t in TIERS})
    return g.compile()
