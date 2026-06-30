"""Tests for the geo HTTP transport's shared-secret middleware (spec 2026-06-30).

The geo HTTP endpoint must reject requests without a matching bearer token. Unlike the CKAN
server, the secret is mandatory in HTTP mode (the GEO_TAPIS_TOKEN env fallback grants ambient
Abaco compute), so `main()` refuses to start without it. These tests exercise the ASGI
middleware via Starlette's TestClient — no live Tapis/CKAN required.
"""

from __future__ import annotations

import pytest
from starlette.testclient import TestClient

from dso_geo_mcp import server


@pytest.fixture()
def secret(monkeypatch):
    monkeypatch.setattr(server.settings, "mcp_http_shared_secret", "g30s3cret", raising=False)
    return "g30s3cret"


def test_missing_bearer_is_rejected(secret):
    client = TestClient(server._build_http_app())
    resp = client.post("/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "ping"})
    assert resp.status_code == 401
    assert resp.json()["error"] == "unauthorized"


def test_wrong_bearer_is_rejected(secret):
    client = TestClient(server._build_http_app())
    resp = client.post(
        "/mcp",
        headers={"Authorization": "Bearer nope"},
        json={"jsonrpc": "2.0", "id": 1, "method": "ping"},
    )
    assert resp.status_code == 401


def test_correct_bearer_passes_middleware(secret):
    with TestClient(server._build_http_app()) as client:
        resp = client.post(
            "/mcp",
            headers={
                "Authorization": "Bearer g30s3cret",
                "Accept": "application/json, text/event-stream",
            },
            json={"jsonrpc": "2.0", "id": 1, "method": "initialize"},
        )
    assert resp.status_code != 401


def test_http_mode_requires_secret(monkeypatch):
    """main() must refuse to start in http mode without a shared secret."""
    monkeypatch.setattr(server.settings, "mcp_transport", "http", raising=False)
    monkeypatch.setattr(server.settings, "mcp_http_shared_secret", None, raising=False)
    with pytest.raises(SystemExit):
        server.main()


def test_http_mode_refuses_nonloopback_with_env_token(monkeypatch):
    """Non-loopback bind + GEO_TAPIS_TOKEN env fallback must refuse to start."""
    monkeypatch.setattr(server.settings, "mcp_transport", "http", raising=False)
    monkeypatch.setattr(server.settings, "mcp_http_shared_secret", "s", raising=False)
    monkeypatch.setattr(server.settings, "mcp_http_host", "0.0.0.0", raising=False)
    monkeypatch.setattr(server.settings, "geo_tapis_token", "tok", raising=False)
    with pytest.raises(SystemExit):
        server.main()
