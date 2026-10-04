import io
import json

from jevllm.backends.systemone_http import SystemOneHTTPBackend, parse_answers

BODY = {
    "answers": {
        "team": {
            "choice": "billing",
            "confidence": 0.8,
            "probabilities": {"billing": 0.7, "infra": 0.1, "account": 0.1, "security": 0.1},
        },
        "urgent": {"noul": 0.2},
        "severity": {"score": 0.6},
    },
    "usage": {"input_tokens": 321},
}


def test_parse_answers_dict_list_and_missing_probs():
    label, probs, u, s = parse_answers(BODY)
    assert label == "billing" and probs["billing"] == 0.7 and u == 0.2 and s == 0.6
    b2 = json.loads(json.dumps(BODY))
    b2["answers"]["team"]["probabilities"] = [0.1, 0.6, 0.2, 0.1]
    assert parse_answers(b2)[1]["infra"] == 0.6
    b3 = json.loads(json.dumps(BODY))
    del b3["answers"]["team"]["probabilities"]
    assert abs(sum(parse_answers(b3)[1].values()) - 1) < 1e-9


def test_http_backend_uses_wire_contract(monkeypatch):
    seen = {}

    class Resp(io.BytesIO):
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    def fake_urlopen(req, timeout=None):
        seen["url"] = req.full_url
        seen["body"] = json.loads(req.data)
        seen["auth"] = req.get_header("Authorization")
        return Resp(json.dumps(BODY).encode())

    monkeypatch.setattr("jevllm.net.OPENER.open", fake_urlopen)
    be = SystemOneHTTPBackend(name="kev", base_url="http://localhost:8009/", api_key="k", model="kev-latest")
    d = be.decide("charged twice")
    assert seen["url"] == "http://localhost:8009/v1/systemone" and seen["auth"] == "Bearer k"
    assert set(seen["body"]["questions"]) == {"urgent", "team", "severity"} and seen["body"]["model"] == "kev-latest"
    assert d.team == "billing" and d.input_tokens == 321 and not d.tokens_estimated and d.cost_usd == 0.0


def test_price_is_only_what_the_caller_knows(monkeypatch):
    assert SystemOneHTTPBackend(base_url="https://api.typesafe.ai").price_known is False  # unknown is not free
    be = SystemOneHTTPBackend(base_url="https://api.typesafe.ai", price_in=0.042)
    assert be.price_known and be.price_in == 0.042


def test_http_errors_report_the_status_not_the_body(monkeypatch):
    import io as _io
    import urllib.error

    import pytest

    def boom(req, timeout=None):
        raise urllib.error.HTTPError(req.full_url, 500, "x", {}, _io.BytesIO(b"internal secret page"))

    monkeypatch.setattr("jevllm.net.OPENER.open", boom)
    with pytest.raises(RuntimeError) as e:
        SystemOneHTTPBackend(base_url="http://localhost:8009").decide("x")
    assert str(e.value) == "HTTP 500"
