from jevllm.backends.mock_backend import MockBackend
from jevllm.dataset import load_dataset
from jevllm.graph import build_graph, pick_queue
from jevllm.schema import Decision


def _d(team="infra", conf=0.9, urgent=0.0, sev=0.0):
    rest = (1 - conf) / 3
    probs = {t: (conf if t == team else rest) for t in ("billing", "infra", "account", "security")}
    return Decision(team=team, team_probs=probs, urgent_prob=urgent, severity=sev)


def test_low_confidence_goes_to_human():
    assert pick_queue(_d(conf=0.4)) == "human_review"


def test_urgent_and_severe_pages_oncall():
    assert pick_queue(_d(urgent=0.9, sev=2.0)) == "page_oncall"


def test_normal_routes_to_team():
    assert pick_queue(_d(team="billing")) == "billing"


def test_graph_runs_end_to_end():
    out = build_graph(MockBackend()).invoke({"ticket": "I was charged twice for my invoice, please refund."})
    assert out["queue"] in {"billing", "human_review", "page_oncall"}
    assert out["decision"].team == "billing"


def test_dataset_is_valid():
    rows = load_dataset()
    assert len(rows) == 40
    assert {r["team"] for r in rows} == {"billing", "infra", "account", "security"}
    assert all(r["severity"] in (0, 1, 2) for r in rows)
    assert len({r["id"] for r in rows}) == len(rows)
