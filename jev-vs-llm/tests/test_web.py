import json

import pytest

pytest.importorskip("fastapi")
from fastapi.testclient import TestClient
from jevllm.web import app as webapp

client = TestClient(webapp.app)


def events(resp):
    out, ev = [], None
    for line in resp.iter_lines():
        if line.startswith("event: "):
            ev = line[7:]
        elif line.startswith("data: "):
            out.append((ev, json.loads(line[6:])))
    return out


def T(n, text="I was charged twice for my invoice.", team="billing", page=None):
    return {"id": f"t{n}", "text": text, "team": team, "page": page}


def run(lanes, tickets, **extra):
    return events(client.post("/api/batch", json={"lanes": lanes, "tickets": tickets, **extra}))


def items(ev):
    return [d for e, d in ev if e == "item"]


MOCK = {"kind": "mock"}
LITE = {"kind": "litellm", "model": "gpt-6-luna", "url": "http://127.0.0.1:4999", "key": "sk-lite-secret"}


def test_landing_and_lab_pages_are_served():
    from jevllm.questions import SEVERITY_Q, TEAM_Q, URGENT_Q

    landing = client.get("/")
    assert landing.status_code == 200 and "Who should route your tickets" in landing.text
    for q in (TEAM_Q, URGENT_Q, SEVERITY_Q):  # the hero quotes the real questions: fail if they drift
        assert q in landing.text
    lab = client.get("/lab")
    assert lab.status_code == 200 and 'id="run"' in lab.text and "Connect your contenders" in lab.text
    assert client.get("/static/landing.js").status_code == 200


def test_cases_are_well_formed():
    from jevllm.schema import TEAMS

    cases = client.get("/api/cases").json()["cases"]
    ids = [t["id"] for c in cases for t in c["tickets"]]
    assert len(cases) >= 3 and len(ids) == len(set(ids))  # ticket ids are deep-link keys: unique
    assert len({c["id"] for c in cases}) == len(cases)
    for c in cases:
        for t in c["tickets"]:
            assert 0 < len(t["text"]) <= webapp.MAX_TEXT and t["trap"]
            assert t["team"] is None or t["team"] in TEAMS
            assert t["page"] in (True, False, None)


def test_models_are_listed_from_the_users_own_account(monkeypatch):
    calls = []

    def fake_fetch(url, key=None, **kw):
        calls.append((url, key))
        return {"data": [{"id": "gpt-6-luna"}, {"id": "bad id!"}, {"id": "gpt-4o"}]}

    monkeypatch.setattr(webapp.net, "fetch_json", fake_fetch)
    r = client.post("/api/models", json={"provider": "openai", "key": "sk-mine"})
    assert r.json() == {"models": ["gpt-4o", "gpt-6-luna"]}  # invalid ids are dropped, the rest sorted
    assert calls[-1] == ("https://api.openai.com/v1/models", "sk-mine")
    r = client.post("/api/models", json={"provider": "litellm", "key": "sk-l", "url": "http://127.0.0.1:4999"})
    assert r.status_code == 200 and calls[-1] == ("http://127.0.0.1:4999/v1/models", "sk-l")
    r = client.post("/api/models", json={"provider": "litellm", "key": "sk-l", "url": "http://127.0.0.1:4999/v1"})
    assert calls[-1][0] == "http://127.0.0.1:4999/v1/models"

    def failing(url, key=None, **kw):
        raise RuntimeError(f"HTTP 401 for {key}")

    monkeypatch.setattr(webapp.net, "fetch_json", failing)
    r = client.post("/api/models", json={"provider": "openai", "key": "sk-mine"})
    assert r.status_code == 502 and "sk-mine" not in r.text
    r = client.post("/api/models", json={"provider": "litellm", "key": "sk-l", "url": "http://169.254.169.254"})
    assert r.status_code == 502 and "not allowed" in r.text
    assert client.post("/api/models", json={"provider": "openai", "key": "has space"}).status_code == 422


def test_price_endpoint_answers_from_the_public_list_only(monkeypatch):
    monkeypatch.setattr(webapp.prices, "lookup", lambda kind, model: (0.1, 0.5) if model == "gpt-6-luna" else None)
    assert client.get("/api/price", params={"kind": "openai", "model": "gpt-6-luna"}).json()["price_in"] == 0.1
    assert client.get("/api/price", params={"kind": "openai", "model": "nope"}).json() == {}
    assert client.get("/api/price", params={"kind": "openai", "model": "bad id!"}).status_code == 422
    assert client.get("/api/price", params={"kind": "mock", "model": "x"}).status_code == 422


def test_showcase_serves_the_static_recording_file(monkeypatch, tmp_path):
    monkeypatch.setattr(webapp, "SHOWCASE", tmp_path / "none.json")
    assert client.get("/api/showcase").json() == {"recorded": False}
    rec = {"recorded_at": "2026-10-04T10:00:00+00:00", "threshold": 0.6, "backends": [], "results": {}}
    (tmp_path / "s.json").write_text(json.dumps(rec))
    monkeypatch.setattr(webapp, "SHOWCASE", tmp_path / "s.json")
    assert client.get("/api/showcase").json() == {"recorded": True, **rec}


def test_static_assets_served():
    assert client.get("/").status_code == 200
    for f in ("lab.js", "lab.css", "landing.js", "landing.css", "app.css", "logo.svg", "favicon.svg"):
        assert client.get(f"/static/{f}").status_code == 200


def test_batch_scores_every_ticket_against_the_users_ground_truth():
    tickets = [
        T(1, "I was charged twice for my invoice.", "billing", page=False),
        T(2, "I was charged twice for my invoice.", "infra", page=True),  # a deliberately wrong truth
        T(3, "I was charged twice for my invoice.", None),  # no single right answer: run, not scored
    ]
    ev = run([MOCK], tickets)
    assert ev[0][0] == "start" and ev[0][1]["total"] == 3 and ev[-1][0] == "done"
    got = items(ev)
    assert [i["ticket"] for i in got] == ["t1", "t2", "t3"] and all(i["ok"] and i["queue"] for i in got)
    assert [i["correct"] for i in got] == [True, False, None]
    assert [i["page_correct"] for i in got] == [True, False, None]  # page truth only where it was given
    last = got[-1]["summary"]
    assert (last["scored"], last["correct"], last["accuracy"], last["paging"]) == (2, 1, 0.5, [1, 2])
    assert {"team_probs", "urgent_prob", "severity", "latency_ms", "cost_usd", "input_tokens"} <= set(got[0])


def test_every_contender_sees_every_ticket():
    ev = run([MOCK, {"kind": "jev"}], [T(1), T(2)], keys={"typesafe": "k"})
    assert len(items(ev)) == 4 and {i["id"] for i in items(ev)} == {"mock:", "jev:"}


def test_ticket_and_lane_validation():
    ok = [T(1)]
    bad = [
        {"lanes": [MOCK], "tickets": []},
        {"lanes": [MOCK], "tickets": [T(n) for n in range(41)]},
        {"lanes": [MOCK], "tickets": [T(1, "x" * 4001)]},
        {"lanes": [MOCK], "tickets": [T(1, "")]},
        {"lanes": [MOCK], "tickets": [T(1, team="sales")]},
        {"lanes": [MOCK], "tickets": [T(1), T(1)]},  # ids are how results are matched: unique
        {"lanes": [MOCK], "tickets": [{**T(1), "id": "no spaces!"}]},
        {"lanes": [], "tickets": ok},
        {"lanes": [MOCK] * 7, "tickets": ok},
        {"lanes": [{"kind": "systemone", "url": "http://x"}], "tickets": ok},
        {"lanes": [{"kind": "openai", "model": "no spaces; rm -rf"}], "tickets": ok},
        {"lanes": [MOCK], "tickets": ok, "threshold": 2},
        {"lanes": [MOCK], "tickets": ok, "keys": {"openai": "has space"}},
        {"lanes": [MOCK], "tickets": ok, "keys": {"openai": "line\nbreak"}},
    ]
    for body in bad:
        assert client.post("/api/batch", json=body).status_code == 422, body


def test_team_labels_match_the_schema():
    from typing import get_args

    from jevllm.schema import TEAMS

    assert get_args(webapp.Team) == TEAMS


def test_dataset_endpoint_serves_the_labeled_sample():
    rows = client.get("/api/dataset").json()["tickets"]
    assert len(rows) == 40 and len({r["id"] for r in rows}) == 40
    assert all(r["team"] in ("billing", "infra", "account", "security") and r["text"] for r in rows)
    assert client.post("/api/batch", json={"lanes": [MOCK], "tickets": rows}).status_code == 200


def test_config_endpoint_is_gone_and_nothing_leaks_the_environment(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "server-key-must-not-matter")
    assert client.get("/api/config").status_code == 404 and client.post("/api/compare").status_code == 404
    res = items(run([{"kind": "openai", "model": "m"}], [T(1)]))[0]
    assert not res["ok"] and "OpenAI API key" in res["error"]  # the server's own env key is never used


def test_user_keys_reach_the_backends_and_are_redacted_from_errors(monkeypatch):
    import jevllm.backends.openai_backend as ob
    import jevllm.backends.systemone_http as sh

    seen = {}

    class FakeJev:
        def __init__(self, **kw):
            seen["jev"] = kw["api_key"]

        def decide(self, text):
            raise RuntimeError("401 bad key tsk-secret-1")

    class FakeOpenAI:
        def __init__(self, model, api_key=None, **kw):
            seen["openai"] = api_key

        def decide(self, text):
            raise RuntimeError("Incorrect API key provided: sk-secret-2")

    monkeypatch.setattr(sh, "SystemOneHTTPBackend", FakeJev)
    monkeypatch.setattr(ob, "OpenAIBackend", FakeOpenAI)
    ev = run(
        [{"kind": "jev"}, {"kind": "openai", "model": "m"}],
        [T(1)],
        keys={"typesafe": "tsk-secret-1", "openai": "sk-secret-2"},
    )
    assert seen == {"jev": "tsk-secret-1", "openai": "sk-secret-2"}
    errs = [i["error"] for i in items(ev)]
    assert len(errs) == 2 and all("***" in e for e in errs) and "secret" not in json.dumps(errs).replace("***", "")


def test_a_lane_without_its_key_fails_in_its_lane_and_the_others_keep_going():
    ev = run([MOCK, {"kind": "jev"}, {"kind": "litellm", "model": "m", "url": "http://127.0.0.1:4999"}], [T(1)])
    by = {i["id"]: i for i in items(ev)}
    assert by["mock:"]["ok"]
    assert "TypeSafe API key" in by["jev:"]["error"] and "key for your LiteLLM proxy" in by["litellm:m"]["error"]


def test_batch_builds_each_backend_once_with_the_users_key(monkeypatch):
    import jevllm.backends.openai_backend as ob

    built = []

    class FakeOpenAI:
        def __init__(self, model, api_key=None, **kw):
            built.append(api_key)

        def decide(self, text):
            raise RuntimeError("nope")

    monkeypatch.setattr(ob, "OpenAIBackend", FakeOpenAI)
    ev = run([{"kind": "openai", "model": "m"}], [T(1), T(2), T(3)], keys={"openai": "sk-mine"})
    assert built == ["sk-mine"] and len(items(ev)) == 3


def _fake_openai_backend(monkeypatch, fail_with=None):
    import jevllm.backends.openai_backend as ob

    seen = {}

    class FakeOpenAI:
        def __init__(self, model, api_key=None, base_url=None, vendor="openai", price=None):
            seen.update(model=model, api_key=api_key, base_url=base_url, vendor=vendor, price=price)

        def decide(self, text):
            raise RuntimeError(fail_with or "boom")

    monkeypatch.setattr(ob, "OpenAIBackend", FakeOpenAI)
    return seen


def _one(lane):
    return items(run([lane], [T(1)]))[0]


def test_litellm_lane_uses_the_users_url_and_key_and_redacts_the_key(monkeypatch):
    seen = _fake_openai_backend(monkeypatch, fail_with="401 for sk-lite-secret")
    monkeypatch.setattr(webapp.prices, "lookup", lambda kind, model: (0.1, 0.5))
    res = _one(LITE)
    assert seen == {
        "model": "gpt-6-luna",
        "api_key": "sk-lite-secret",
        "base_url": "http://127.0.0.1:4999",
        "vendor": "litellm",
        "price": (0.1, 0.5),
    }
    assert "sk-lite-secret" not in res["error"] and "***" in res["error"] and res["id"] == "litellm:gpt-6-luna"


def test_typed_price_beats_the_public_list_and_unknown_stays_unknown(monkeypatch):
    seen = _fake_openai_backend(monkeypatch)
    monkeypatch.setattr(webapp.prices, "lookup", lambda kind, model: (9.0, 9.0))
    _one({**LITE, "price_in": 1.5, "price_out": 6})
    assert seen["price"] == (1.5, 6)
    monkeypatch.setattr(webapp.prices, "lookup", lambda kind, model: None)
    _one(LITE)
    assert seen["price"] is None


def test_urls_typed_by_users_are_checked_before_the_server_calls_them(monkeypatch):
    seen = _fake_openai_backend(monkeypatch)
    bad = {
        "file:///etc/passwd": "http:// or https://",
        "http://169.254.169.254/latest/meta-data": "not allowed",  # cloud metadata
        "http://user:pw@127.0.0.1:4999": "user name or password",
        "http://[::ffff:169.254.169.254]/": "not allowed",
        "": "http:// or https://",
    }
    for url, why in bad.items():
        res = _one({**LITE, "url": url})
        assert not res["ok"] and why in res["error"], url
    assert not seen  # nothing was ever built, let alone called
    monkeypatch.setenv("BLOCK_PRIVATE_URLS", "1")
    res = _one(LITE)
    assert not res["ok"] and "private address" in res["error"]


def test_the_committed_recording_matches_the_use_cases():
    """If a showcase.json is committed, every case ticket must have an answer from every recorded contender."""
    from jevllm.cases import SHOWCASE, load_cases

    if not SHOWCASE.is_file():
        pytest.skip("no recording committed")
    rec = json.loads(SHOWCASE.read_text())
    ids = {b["id"] for b in rec["backends"]}
    assert ids and 0 <= rec["threshold"] <= 1
    for case in load_cases():
        for t in case["tickets"]:
            assert ids <= set(rec["results"][case["id"]][t["id"]]), (case["id"], t["id"])
    assert set(rec["dataset"]["summary"]) <= ids
    assert not any(s in SHOWCASE.read_text() for s in ("sk-", "Bearer", "api_key"))  # decisions only, never keys
