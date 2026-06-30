"""
Integration tests for read tools — hit the live dev portal at localhost:5001.

All tests are marked ``integration`` and auto-skipped when the portal is
not reachable (see conftest.py).
"""

from __future__ import annotations

import pytest

from dso_ckan_mcp.tools import read as read_tools


# ---------------------------------------------------------------------------
# Unit tests for tool param-building (no HTTP) — capture what is sent to CKAN.
# ---------------------------------------------------------------------------


class _RecordingClient:
    """Stand-in CKANClient that records the last get() call and returns a stub."""

    def __init__(self, result=None):
        self.last_action = None
        self.last_params = None
        self._result = result if result is not None else {"count": 0, "results": []}

    def get(self, action, params=None):
        self.last_action = action
        self.last_params = params or {}
        return self._result


class _FakeMCP:
    """Captures functions registered via the @mcp.tool() decorator by name."""

    def __init__(self):
        self.tools = {}

    def tool(self):
        def decorator(fn):
            self.tools[fn.__name__] = fn
            return fn

        return decorator


@pytest.fixture
def registered():
    """Return (tools_by_name, recording_client) with read tools registered."""
    client = _RecordingClient()
    mcp = _FakeMCP()
    read_tools.register(mcp, client)
    return mcp.tools, client


def test_package_search_joins_fq_list(registered):
    tools, client = registered
    tools["package_search"](q="*:*", fq=["owner_org:water", "dataset_type:mint_dataset"])
    assert client.last_params["fq"] == "owner_org:water dataset_type:mint_dataset"


def test_package_search_accepts_fq_string(registered):
    tools, client = registered
    tools["package_search"](fq="owner_org:water")
    assert client.last_params["fq"] == "owner_org:water"


def test_package_search_strips_resources_by_default():
    client = _RecordingClient(
        {"count": 1, "results": [{"name": "d1", "num_resources": 170, "resources": [{"id": "r1"}]}]}
    )
    mcp = _FakeMCP()
    read_tools.register(mcp, client)
    out = mcp.tools["package_search"](q="x")
    assert "resources" not in out["results"][0]
    assert out["results"][0]["num_resources"] == 170


def test_package_search_keeps_resources_when_requested():
    client = _RecordingClient(
        {"count": 1, "results": [{"name": "d1", "resources": [{"id": "r1"}]}]}
    )
    mcp = _FakeMCP()
    read_tools.register(mcp, client)
    out = mcp.tools["package_search"](q="x", include_resources=True)
    assert out["results"][0]["resources"] == [{"id": "r1"}]


def test_package_search_rows_floor_and_cap(registered):
    tools, client = registered
    tools["package_search"](rows=-1)
    assert client.last_params["rows"] == 1
    tools["package_search"](rows=999999)
    assert client.last_params["rows"] == 1000


def test_find_relevant_datasets_limit_floor(registered):
    tools, client = registered
    tools["find_relevant_datasets"]("water", limit=0)
    assert client.last_params["rows"] == 1


def test_organization_list_caps_limit(registered):
    tools, client = registered
    tools["organization_list"](limit=50000)
    assert client.last_params["limit"] == 1000


def test_group_list_caps_limit(registered):
    tools, client = registered
    tools["group_list"](limit=50000)
    assert client.last_params["limit"] == 1000


# ---------------------------------------------------------------------------
# Integration tests — hit the live dev portal (auto-skip if unreachable).
# ---------------------------------------------------------------------------


@pytest.mark.integration
def test_status_show(live_client):
    result = live_client.get("status_show")
    assert "ckan_version" in result
    assert "site_title" in result


@pytest.mark.integration
def test_package_search_smoke(live_client):
    result = live_client.get("package_search", params={"q": "*:*", "rows": 5})
    assert "count" in result
    assert "results" in result
    assert isinstance(result["results"], list)


@pytest.mark.integration
def test_organization_list_smoke(live_client):
    result = live_client.get("organization_list", params={"all_fields": False})
    assert isinstance(result, list)


@pytest.mark.integration
def test_group_list_smoke(live_client):
    result = live_client.get("group_list", params={"all_fields": False})
    assert isinstance(result, list)
