"""
Tests for the tools modules — all HTTP mocked via ``responses``.

Tools are registered via each module's ``register(mcp)`` using a fake MCP that
captures the decorated functions by name (FastMCP exposes no public registry,
and the tools are closures created inside ``register``). We then call the
captured functions directly.

Verifies:
- Transform tools without a token return an error and make NO HTTP calls.
- Param validation (CRS, output_name, clip geometry) rejects before any HTTP.
- Metadata tools resolve via CKAN, submit to Abaco, and return an execution id.
- get_execution_status returns the parsed actor JSON from the mocked logs.
"""

from __future__ import annotations

import json

import responses as resp_lib

CKAN_URL = "http://localhost:5001"
TAPIS_BASE = "https://portals.tapis.io"
ACTOR_ID = "testactor123"
EXEC_ID = "exec-abc-123"
RESOURCE_ID = "resource-uuid-abc"
PACKAGE_ID = "test-dataset"
DOWNLOAD_URL = f"{CKAN_URL}/dataset/{PACKAGE_ID}/resource/{RESOURCE_ID}/download/test.tif"
TOKEN = "eyJfaketoken.fake.sig"


class _FakeMCP:
    """Captures functions registered via @mcp.tool() or @mcp.tool."""

    def __init__(self):
        self.tools = {}

    def tool(self, *args, **kwargs):
        if args and callable(args[0]) and not kwargs:  # used as @mcp.tool
            fn = args[0]
            self.tools[fn.__name__] = fn
            return fn

        def deco(fn):  # used as @mcp.tool(...)
            self.tools[fn.__name__] = fn
            return fn

        return deco


def _tools_of(module):
    m = _FakeMCP()
    module.register(m)
    return m.tools


# Mock payloads
_RESOURCE_SHOW_OK = {
    "success": True,
    "result": {"id": RESOURCE_ID, "package_id": PACKAGE_ID, "url": DOWNLOAD_URL, "name": "test.tif"},
}
_SUBMIT_OK = {"result": {"execution_id": EXEC_ID}, "status": "success"}
_EXEC_RUNNING = {"result": {"status": "RUNNING", "id": EXEC_ID}}
_EXEC_COMPLETE = {"result": {"status": "COMPLETE", "id": EXEC_ID}}
_EXEC_FAILED = {"result": {"status": "FAILED", "id": EXEC_ID}}
_ACTOR_JSON_SUCCESS = json.dumps({
    "status": "ok", "operation": "gdalinfo", "gdal_version": "GDAL 3.8.0",
    "metrics": {"duration_ms": 1234}, "metadata": {"driverShortName": "GTiff"},
})
_ACTOR_JSON_WITH_REGISTERED = json.dumps({
    "status": "ok", "operation": "reproject", "gdal_version": "GDAL 3.8.0",
    "output_path": "/data/out/result.tif", "metrics": {"duration_ms": 5678},
    "registered": {"status": "ok", "resource": {"id": "new-resource-uuid", "name": "result.tif"}},
})
_LOGS_OK = {"result": {"logs": _ACTOR_JSON_SUCCESS}}
_LOGS_TRANSFORM_OK = {"result": {"logs": _ACTOR_JSON_WITH_REGISTERED}}
_LOGS_FAILED = {"result": {"logs": '{"status": "error", "message": "GDAL failed"}'}}


# ---------------------------------------------------------------------------
# Transform tools: no token -> error, no HTTP
# ---------------------------------------------------------------------------

class TestTransformNoToken:
    @resp_lib.activate
    def test_reproject_requires_token(self):
        from dso_geo_mcp.tools import transform
        fn = _tools_of(transform)["reproject_raster"]
        result = fn(resource_id=RESOURCE_ID, target_crs=4326, output_name="result.tif", tapis_token=None)
        assert "error" in result
        assert len(resp_lib.calls) == 0

    @resp_lib.activate
    def test_convert_to_cog_requires_token(self):
        from dso_geo_mcp.tools import transform
        fn = _tools_of(transform)["convert_to_cog"]
        result = fn(resource_id=RESOURCE_ID, output_name="result.tif", tapis_token=None)
        assert "error" in result
        assert len(resp_lib.calls) == 0

    @resp_lib.activate
    def test_clip_raster_requires_token(self):
        from dso_geo_mcp.tools import transform
        fn = _tools_of(transform)["clip_raster"]
        result = fn(
            resource_id=RESOURCE_ID,
            clip_geometry={"type": "Polygon", "coordinates": [
                [[-97.75, 30.25], [-97.70, 30.25], [-97.70, 30.30], [-97.75, 30.30], [-97.75, 30.25]]
            ]},
            output_name="result.tif", tapis_token=None,
        )
        assert "error" in result
        assert len(resp_lib.calls) == 0

    @resp_lib.activate
    def test_build_overviews_requires_token(self):
        from dso_geo_mcp.tools import transform
        fn = _tools_of(transform)["build_overviews"]
        result = fn(resource_id=RESOURCE_ID, output_name="result.tif", tapis_token=None)
        assert "error" in result
        assert len(resp_lib.calls) == 0


# ---------------------------------------------------------------------------
# Metadata tools: build + submit
# ---------------------------------------------------------------------------

AGENT_API_HOST = "dsoagentapi.pods.portals.tapis.io"
AGENT_FILE_URL = f"https://{AGENT_API_HOST}/v1/uploads/abc123/odm_orthophoto.tif"


class TestGdalinfoFromUrl:
    @resp_lib.activate
    def test_returns_execution_id_for_valid_url(self, monkeypatch):
        import os
        monkeypatch.setenv("GEO_ALLOWED_AGENT_HOST", AGENT_API_HOST)
        # reload settings singleton so the new env var is picked up
        import importlib
        from dso_geo_mcp import config as cfg_mod
        importlib.reload(cfg_mod)
        from dso_geo_mcp.tools import metadata
        importlib.reload(metadata)

        resp_lib.add(resp_lib.POST, f"{TAPIS_BASE}/v3/actors/{ACTOR_ID}/messages", json=_SUBMIT_OK, status=200)
        fn = _tools_of(metadata)["gdalinfo_from_url"]
        result = fn(url=AGENT_FILE_URL, tapis_token=TOKEN)
        assert result.get("execution_id") == EXEC_ID
        assert result.get("status") == "SUBMITTED"

    def test_rejects_private_ip_url(self, monkeypatch):
        import importlib
        import os
        monkeypatch.setenv("GEO_ALLOWED_AGENT_HOST", AGENT_API_HOST)
        from dso_geo_mcp import config as cfg_mod
        importlib.reload(cfg_mod)
        from dso_geo_mcp.tools import metadata
        importlib.reload(metadata)

        fn = _tools_of(metadata)["gdalinfo_from_url"]
        result = fn(url="https://10.0.0.1/secret.tif", tapis_token=TOKEN)
        assert "error" in result

    def test_rejects_wrong_host(self, monkeypatch):
        import importlib
        import os
        monkeypatch.setenv("GEO_ALLOWED_AGENT_HOST", AGENT_API_HOST)
        from dso_geo_mcp import config as cfg_mod
        importlib.reload(cfg_mod)
        from dso_geo_mcp.tools import metadata
        importlib.reload(metadata)

        fn = _tools_of(metadata)["gdalinfo_from_url"]
        result = fn(url="https://evil.example.com/file.tif", tapis_token=TOKEN)
        assert "error" in result

    def test_allows_public_url_when_host_not_configured(self, monkeypatch):
        import importlib
        monkeypatch.delenv("GEO_ALLOWED_AGENT_HOST", raising=False)
        from dso_geo_mcp import config as cfg_mod
        importlib.reload(cfg_mod)
        from dso_geo_mcp.tools import metadata
        importlib.reload(metadata)

        # Without GEO_ALLOWED_AGENT_HOST, any public HTTPS URL is accepted (private IPs still blocked).
        # The tool proceeds to the token check before the Abaco submission.
        fn = _tools_of(metadata)["gdalinfo_from_url"]
        result = fn(url=AGENT_FILE_URL, tapis_token=None)
        # Fails on missing token, not on SSRF — proves the URL passed the guard
        assert result.get("error", "").startswith("tapis_token is required")


class TestMetadataTools:
    @resp_lib.activate
    def test_gdalinfo_extract_returns_execution_id(self):
        resp_lib.add(resp_lib.GET, f"{CKAN_URL}/api/3/action/resource_show", json=_RESOURCE_SHOW_OK, status=200)
        resp_lib.add(resp_lib.POST, f"{TAPIS_BASE}/v3/actors/{ACTOR_ID}/messages", json=_SUBMIT_OK, status=200)
        from dso_geo_mcp.tools import metadata
        fn = _tools_of(metadata)["gdalinfo_extract"]
        result = fn(resource_id=RESOURCE_ID, tapis_token=TOKEN)
        assert result["execution_id"] == EXEC_ID
        assert result["status"] == "SUBMITTED"

    @resp_lib.activate
    def test_gdalinfo_extract_ckan_failure_returns_error(self):
        resp_lib.add(resp_lib.GET, f"{CKAN_URL}/api/3/action/resource_show",
                     json={"success": False, "error": {"message": "Not found"}}, status=200)
        from dso_geo_mcp.tools import metadata
        fn = _tools_of(metadata)["gdalinfo_extract"]
        result = fn(resource_id="nonexistent-id", tapis_token=TOKEN)
        assert "error" in result

    @resp_lib.activate
    def test_gdalinfo_summary_returns_executions(self):
        resp_lib.add(
            resp_lib.GET, f"{CKAN_URL}/api/3/action/package_show",
            json={"success": True, "result": {"id": PACKAGE_ID, "resources": [
                {"id": "res-1", "url": f"{CKAN_URL}/dataset/{PACKAGE_ID}/resource/res-1/download/l1.tif", "name": "l1.tif"},
                {"id": "res-2", "url": f"{CKAN_URL}/dataset/{PACKAGE_ID}/resource/res-2/download/l2.tif", "name": "l2.tif"},
            ]}}, status=200,
        )
        resp_lib.add(resp_lib.POST, f"{TAPIS_BASE}/v3/actors/{ACTOR_ID}/messages",
                     json={"result": {"execution_id": "exec-1"}}, status=200)
        resp_lib.add(resp_lib.POST, f"{TAPIS_BASE}/v3/actors/{ACTOR_ID}/messages",
                     json={"result": {"execution_id": "exec-2"}}, status=200)
        from dso_geo_mcp.tools import metadata
        fn = _tools_of(metadata)["gdalinfo_summary"]
        result = fn(dataset_id=PACKAGE_ID, tapis_token=TOKEN)
        assert result["submitted"] == 2
        assert len(result["executions"]) == 2


# ---------------------------------------------------------------------------
# Transform tools: full path + validation
# ---------------------------------------------------------------------------

class TestTransformTools:
    @resp_lib.activate
    def test_reproject_raster_returns_execution_id(self):
        resp_lib.add(resp_lib.GET, f"{CKAN_URL}/api/3/action/resource_show", json=_RESOURCE_SHOW_OK, status=200)
        resp_lib.add(resp_lib.POST, f"{TAPIS_BASE}/v3/actors/{ACTOR_ID}/messages", json=_SUBMIT_OK, status=200)
        from dso_geo_mcp.tools import transform
        fn = _tools_of(transform)["reproject_raster"]
        result = fn(resource_id=RESOURCE_ID, target_crs=4326, output_name="reprojected.tif", tapis_token=TOKEN)
        assert result["execution_id"] == EXEC_ID
        assert result["status"] == "SUBMITTED"

    @resp_lib.activate
    def test_reproject_invalid_crs_returns_error_no_http(self):
        from dso_geo_mcp.tools import transform
        fn = _tools_of(transform)["reproject_raster"]
        result = fn(resource_id=RESOURCE_ID, target_crs=0, output_name="result.tif", tapis_token=TOKEN)
        assert "error" in result and "target_crs" in result["error"]
        assert len(resp_lib.calls) == 0

    @resp_lib.activate
    def test_reproject_invalid_output_name_returns_error_no_http(self):
        from dso_geo_mcp.tools import transform
        fn = _tools_of(transform)["reproject_raster"]
        result = fn(resource_id=RESOURCE_ID, target_crs=4326, output_name="../etc/passwd", tapis_token=TOKEN)
        assert "error" in result and "output_name" in result["error"]
        assert len(resp_lib.calls) == 0

    @resp_lib.activate
    def test_clip_raster_invalid_geometry_returns_error_no_http(self):
        from dso_geo_mcp.tools import transform
        fn = _tools_of(transform)["clip_raster"]
        result = fn(resource_id=RESOURCE_ID, clip_geometry={"type": "Point", "coordinates": [-97, 30]},
                    output_name="result.tif", tapis_token=TOKEN)
        assert "error" in result and "clip_geometry" in result["error"]
        assert len(resp_lib.calls) == 0


# ---------------------------------------------------------------------------
# get_execution_status
# ---------------------------------------------------------------------------

class TestGetExecutionStatus:
    @resp_lib.activate
    def test_returns_running_status(self):
        resp_lib.add(resp_lib.GET, f"{TAPIS_BASE}/v3/actors/{ACTOR_ID}/executions/{EXEC_ID}", json=_EXEC_RUNNING, status=200)
        from dso_geo_mcp.tools import status as status_mod
        fn = _tools_of(status_mod)["get_execution_status"]
        result = fn(execution_id=EXEC_ID, tapis_token=TOKEN)
        assert result["status"] == "RUNNING"
        assert "result" not in result

    @resp_lib.activate
    def test_returns_parsed_actor_json_on_complete(self):
        resp_lib.add(resp_lib.GET, f"{TAPIS_BASE}/v3/actors/{ACTOR_ID}/executions/{EXEC_ID}", json=_EXEC_COMPLETE, status=200)
        resp_lib.add(resp_lib.GET, f"{TAPIS_BASE}/v3/actors/{ACTOR_ID}/executions/{EXEC_ID}/logs", json=_LOGS_OK, status=200)
        from dso_geo_mcp.tools import status as status_mod
        fn = _tools_of(status_mod)["get_execution_status"]
        result = fn(execution_id=EXEC_ID, tapis_token=TOKEN)
        assert result["status"] == "COMPLETE"
        assert result["result"]["operation"] == "gdalinfo"
        assert result["result"]["metadata"]["driverShortName"] == "GTiff"

    @resp_lib.activate
    def test_returns_registered_block_for_transform(self):
        resp_lib.add(resp_lib.GET, f"{TAPIS_BASE}/v3/actors/{ACTOR_ID}/executions/{EXEC_ID}", json=_EXEC_COMPLETE, status=200)
        resp_lib.add(resp_lib.GET, f"{TAPIS_BASE}/v3/actors/{ACTOR_ID}/executions/{EXEC_ID}/logs", json=_LOGS_TRANSFORM_OK, status=200)
        from dso_geo_mcp.tools import status as status_mod
        fn = _tools_of(status_mod)["get_execution_status"]
        result = fn(execution_id=EXEC_ID, tapis_token=TOKEN)
        assert result["status"] == "COMPLETE"
        assert result["result"]["registered"]["status"] == "ok"

    @resp_lib.activate
    def test_failed_status_surfaces(self):
        resp_lib.add(resp_lib.GET, f"{TAPIS_BASE}/v3/actors/{ACTOR_ID}/executions/{EXEC_ID}", json=_EXEC_FAILED, status=200)
        resp_lib.add(resp_lib.GET, f"{TAPIS_BASE}/v3/actors/{ACTOR_ID}/executions/{EXEC_ID}/logs", json=_LOGS_FAILED, status=200)
        from dso_geo_mcp.tools import status as status_mod
        fn = _tools_of(status_mod)["get_execution_status"]
        result = fn(execution_id=EXEC_ID, tapis_token=TOKEN)
        assert result["status"] == "FAILED"

    def test_invalid_execution_id_returns_error(self):
        from dso_geo_mcp.tools import status as status_mod
        fn = _tools_of(status_mod)["get_execution_status"]
        result = fn(execution_id="../../admin", tapis_token=TOKEN)
        assert "error" in result

    def test_no_token_returns_error(self):
        from dso_geo_mcp.tools import status as status_mod
        fn = _tools_of(status_mod)["get_execution_status"]
        result = fn(execution_id=EXEC_ID, tapis_token=None)
        assert "error" in result
