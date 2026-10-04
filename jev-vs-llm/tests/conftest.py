import pytest


@pytest.fixture(autouse=True)
def no_network_price_list(monkeypatch):
    """Tests never download the public price list; the ones that need prices set them explicitly."""
    monkeypatch.setattr("jevllm.prices._load", lambda: {})
    monkeypatch.setattr("jevllm.prices._until", 0.0)
    monkeypatch.setattr("jevllm.prices._table", {})
