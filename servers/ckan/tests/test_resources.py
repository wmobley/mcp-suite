"""
Tests for resources.py (ckan://openapi) and CKANClient.get_json.
"""

from __future__ import annotations

import pytest
import responses as resp_lib


# ---------------------------------------------------------------------------
# CKANClient.get_json — raw (non-envelope) JSON fetch
# ---------------------------------------------------------------------------


@resp_lib.activate
def test_get_json_returns_raw_body():
    from dso_ckan_mcp.ckan_client import CKANClient

    resp_lib.add(
        resp_lib.GET,
        "http://localhost:5001/api-specs/ckan-openapi.json",
        json={"openapi": "3.0.3", "paths": {}},
    )
    client = CKANClient("http://localhost:5001")
    spec = client.get_json("/api-specs/ckan-openapi.json")
    assert spec["openapi"] == "3.0.3"


@resp_lib.activate
def test_get_json_http_error_raises():
    from dso_ckan_mcp.ckan_client import CKANAPIError, CKANClient

    resp_lib.add(
        resp_lib.GET,
        "http://localhost:5001/api-specs/missing.json",
        status=404,
        body="nope",
    )
    client = CKANClient("http://localhost:5001")
    with pytest.raises(CKANAPIError):
        client.get_json("/api-specs/missing.json")


# ---------------------------------------------------------------------------
# resources.register — ckan://openapi resource + caching
# ---------------------------------------------------------------------------


class _RecordingClient:
    def __init__(self, spec):
        self.calls = 0
        self._spec = spec

    def get_json(self, path):
        self.calls += 1
        return self._spec


class _FakeMCP:
    def __init__(self):
        self.resources = {}

    def resource(self, uri, **kwargs):
        def decorator(fn):
            self.resources[uri] = fn
            return fn

        return decorator


def test_openapi_resource_registered_and_cached():
    from dso_ckan_mcp import resources

    client = _RecordingClient({"openapi": "3.0.3", "info": {"title": "TACC CKAN"}})
    mcp = _FakeMCP()
    resources.register(mcp, client, ttl=3600)

    assert "ckan://openapi" in mcp.resources
    fn = mcp.resources["ckan://openapi"]

    first = fn()
    second = fn()
    assert first["info"]["title"] == "TACC CKAN"
    # Second call served from cache — client hit only once.
    assert client.calls == 1
    assert second is first


def test_server_registers_openapi_resource_on_real_fastmcp():
    from dso_ckan_mcp import server

    assert server.mcp is not None
