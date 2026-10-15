"""Network-free paid SDK fixtures; real Docker is mandatory for boundary tests."""

import httpx
import pytest

from agent_workbench.store import Store


@pytest.fixture
def store(tmp_path):
    return Store(tmp_path / "state")


@pytest.fixture(autouse=True)
def forbid_paid_network(monkeypatch):
    def denied(*args, **kwargs):
        raise AssertionError("Live HTTP transport forbidden; use explicit httpx.MockTransport")

    monkeypatch.setattr(httpx.HTTPTransport, "handle_request", denied)
