"""Tests for the HTTP transport's shared-secret middleware (spec 2026-06-29).

The MCP HTTP endpoint must be unauthenticated only when no secret is configured; when
``MCP_HTTP_SHARED_SECRET`` is set, every request must carry a matching bearer token.
These tests exercise the ASGI middleware directly via Starlette's TestClient — they do not
require a running CKAN portal.
"""

from __future__ import annotations

import pytest
from starlette.testclient import TestClient

from dso_ckan_mcp import server


@pytest.fixture()
def secret(monkeypatch):
    monkeypatch.setattr(server.settings, "mcp_http_shared_secret", "s3cret", raising=False)
    return "s3cret"


def test_missing_bearer_is_rejected(secret):
    app = server._build_http_app()
    client = TestClient(app)
    resp = client.post("/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "ping"})
    assert resp.status_code == 401
    assert resp.json()["error"] == "unauthorized"


def test_wrong_bearer_is_rejected(secret):
    app = server._build_http_app()
    client = TestClient(app)
    resp = client.post(
        "/mcp",
        headers={"Authorization": "Bearer wrong"},
        json={"jsonrpc": "2.0", "id": 1, "method": "ping"},
    )
    assert resp.status_code == 401


def test_correct_bearer_passes_middleware(secret):
    """A correct secret is NOT rejected by the middleware (status is not 401)."""
    app = server._build_http_app()
    # Context-manager form runs the FastMCP app lifespan (initializes its task group).
    with TestClient(app) as client:
        resp = client.post(
            "/mcp",
            headers={
                "Authorization": "Bearer s3cret",
                "Accept": "application/json, text/event-stream",
            },
            json={"jsonrpc": "2.0", "id": 1, "method": "initialize"},
        )
    assert resp.status_code != 401


def test_no_secret_means_no_auth(monkeypatch):
    monkeypatch.setattr(server.settings, "mcp_http_shared_secret", None, raising=False)
    app = server._build_http_app()
    with TestClient(app) as client:
        resp = client.post(
            "/mcp",
            headers={"Accept": "application/json, text/event-stream"},
            json={"jsonrpc": "2.0", "id": 1, "method": "initialize"},
        )
    assert resp.status_code != 401  # middleware lets it through when no secret configured
