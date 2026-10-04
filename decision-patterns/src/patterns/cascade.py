"""Pattern 3 - VERIFIED CASCADE: draft cheap, verify with a decision model, escalate only on failure.

(The pattern OpenRouter's Jev guide describes.) Cost logic: the verifier is ~free next to a generation,
so the strong model is only paid for on the tail the verifier doesn't trust. Caveat from the evidence: a
verifier reading attacker-controlled text can be argued into approving, so keep the evidence grounded and
keep irreversible actions behind a human.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import TypedDict

from langgraph.graph import END, START, StateGraph

from .core import Decider, Q

QUESTIONS = {
    "supported": Q("noul", "Is every claim in the ANSWER supported by the EVIDENCE?"),
    "on_topic": Q("noul", "Does the ANSWER address the TASK?"),
}
Generate = Callable[[str, str], str]  # (task, evidence) -> answer


class CascadeState(TypedDict, total=False):
    task: str
    evidence: str
    draft: str
    p_supported: float
    p_on_topic: float
    answer: str
    escalated: bool
    cost_units: float


def accept(p_supported: float, p_on_topic: float, threshold: float) -> bool:
    return p_supported >= threshold and p_on_topic >= 0.5


def build_graph(
    decider: Decider,
    cheap: Generate,
    strong: Generate,
    threshold: float = 0.8,
    cheap_cost: float = 1.0,
    strong_cost: float = 30.0,
    verify_cost: float = 0.05,
):
    def draft(s: CascadeState) -> CascadeState:
        return {"draft": cheap(s["task"], s["evidence"]), "cost_units": cheap_cost}

    def verify(s: CascadeState) -> CascadeState:
        state = f"TASK: {s['task']}\nEVIDENCE: {s['evidence']}\nANSWER: {s['draft']}"
        a = decider.decide(state, QUESTIONS)
        return {
            "p_supported": float(a["supported"].value),
            "p_on_topic": float(a["on_topic"].value),
            "cost_units": s["cost_units"] + verify_cost,
        }

    def route(s: CascadeState) -> str:
        return "keep" if accept(s["p_supported"], s["p_on_topic"], threshold) else "escalate"

    def keep(s: CascadeState) -> CascadeState:
        return {"answer": s["draft"], "escalated": False}

    def escalate(s: CascadeState) -> CascadeState:
        return {
            "answer": strong(s["task"], s["evidence"]),
            "escalated": True,
            "cost_units": s["cost_units"] + strong_cost,
        }

    g = StateGraph(CascadeState)
    for n, f in (("draft", draft), ("verify", verify), ("keep", keep), ("escalate", escalate)):
        g.add_node(n, f)
    g.add_edge(START, "draft")
    g.add_edge("draft", "verify")
    g.add_conditional_edges("verify", route, {"keep": "keep", "escalate": "escalate"})
    g.add_edge("keep", END)
    g.add_edge("escalate", END)
    return g.compile()
