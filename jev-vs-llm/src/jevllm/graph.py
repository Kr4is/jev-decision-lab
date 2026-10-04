"""One LangGraph, any decision backend. Code owns the flow; the backend only makes the narrow judgement."""

from __future__ import annotations

from typing import TypedDict

from langgraph.graph import END, START, StateGraph

from .backends.base import Backend
from .schema import TEAMS, Decision

QUEUES = (*TEAMS, "human_review", "page_oncall")


class TriageState(TypedDict, total=False):
    ticket: str
    decision: Decision
    queue: str


def pick_queue(d: Decision, threshold: float = 0.6) -> str:
    """Pure routing policy, easy to unit-test and identical for every backend."""
    if d.confidence < threshold:
        return "human_review"  # not sure -> a person looks
    if d.urgent_prob >= 0.5 and d.severity >= 1.5:
        return "page_oncall"
    return d.team


def build_graph(backend: Backend, threshold: float = 0.6):
    def decide(state: TriageState) -> TriageState:
        return {"decision": backend.decide(state["ticket"])}

    def router(state: TriageState) -> str:
        return f"queue_{pick_queue(state['decision'], threshold)}"

    g = StateGraph(TriageState)
    g.add_node("decide", decide)
    for q in QUEUES:
        g.add_node(f"queue_{q}", lambda s, q=q: {"queue": q})
        g.add_edge(f"queue_{q}", END)
    g.add_edge(START, "decide")
    g.add_conditional_edges("decide", router, {f"queue_{q}": f"queue_{q}" for q in QUEUES})
    return g.compile()
