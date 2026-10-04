import http.server
import threading

import pytest
from jevllm import net, prices


def test_check_url_rules(monkeypatch):
    monkeypatch.delenv("BLOCK_PRIVATE_URLS", raising=False)
    assert net.check_url("http://127.0.0.1:4000/") == "http://127.0.0.1:4000"  # a homelab proxy is fine
    assert net.check_url(" http://192.168.1.20:4000 ") == "http://192.168.1.20:4000"
    for bad in (
        "ftp://x",
        "file:///etc/passwd",
        "http://",
        "",
        "http://u:p@127.0.0.1",
        "http://169.254.169.254",
        "http://0.0.0.0:80",
        "http://224.0.0.1",
        "http://[fe80::1]",
        "http://[::ffff:169.254.169.254]",
    ):
        with pytest.raises(net.UnsafeURL):
            net.check_url(bad)
    monkeypatch.setenv("BLOCK_PRIVATE_URLS", "1")
    for private in ("http://127.0.0.1:4000", "http://192.168.1.20", "http://10.0.0.5", "http://[::1]"):
        with pytest.raises(net.UnsafeURL):
            net.check_url(private)


def test_redirects_are_never_followed():
    class H(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(302)
            self.send_header("location", "http://169.254.169.254/")
            self.end_headers()

        def log_message(self, *a):
            pass

    srv = http.server.HTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        with pytest.raises(RuntimeError, match="HTTP 302"):
            net.fetch_json(f"http://127.0.0.1:{srv.server_port}/models")
    finally:
        srv.shutdown()


def test_public_price_list_lookup_and_failure(monkeypatch):
    raw = {
        "gpt-6-luna": {"input_cost_per_token": 1e-7, "output_cost_per_token": 5e-7},
        "typesafe/jev-1.13.0": {"input_cost_per_token": 4.2e-8, "output_cost_per_token": 0.0},
        "openai/only-prefixed": {"input_cost_per_token": 2e-6, "output_cost_per_token": 4e-6},
        "sample_spec": {"note": "not a model"},
    }
    monkeypatch.undo()  # the autouse fixture stubbed _load; use the real one against a fake download
    monkeypatch.setattr(prices, "fetch_json", lambda *a, **k: raw)
    monkeypatch.setattr(prices, "_until", 0.0)
    monkeypatch.setattr(prices, "_table", {})
    assert prices.lookup("openai", "gpt-6-luna") == pytest.approx((0.1, 0.5))  # USD per 1M tokens
    assert prices.lookup("jev", "jev-1.13.0") == pytest.approx((0.042, 0.0))
    assert prices.lookup("litellm", "only-prefixed") == pytest.approx((2.0, 4.0))
    assert prices.lookup("openai", "sample_spec") is None and prices.lookup("openai", "unknown") is None

    def down(*a, **k):
        raise RuntimeError("Could not connect")

    monkeypatch.setattr(prices, "fetch_json", down)
    monkeypatch.setattr(prices, "_until", 0.0)
    monkeypatch.setattr(prices, "_table", {})
    assert prices.lookup("openai", "gpt-6-luna") is None  # unreachable list: price unknown, never an error
