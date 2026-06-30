"""
Integration-ish tests for tools modules — all HTTP mocked via ``responses``.

Tests verify:
- Transform tools without token return error + make NO HTTP calls.
- Metadata tools build+submit correctly and return execution_id.
- get_execution_status returns parsed actor JSON from mocked logs.
- CKAN resolution failures surface as clear error dicts.
"""

from __future__ import annotations

import json

import pytest
import responses as resp_lib

# conftest.py patches settings; import after so the patched singleton is used.
CKAN_URL = "http://localhost:5001"
TAPIS_BASE = "https://portals.tapis.io"
ACTOR_ID = "testactor123"
EXEC_ID = "exec-abc-123"
RESOURCE_ID = "resource-uuid-abc"
PACKAGE_ID = "test-dataset"
DOWNLOAD_URL = f"{CKAN_URL}/dataset/{PACKAGE_ID}/resource/{RESOURCE_ID}/download/test.tif"
TOKEN = "eyJfaketoken.fake.sig"

_RESOURCE_SHOW_OK = {
    "success": True,
    "result": {
        "id": RESOURCE_ID,
        "package_id": PACKAGE_ID,
        "url": DOWNLOAD_URL,
        "name": "test.tif",
    },
}

_SUBMIT_OK = {
    "result": {"execution_id": EXEC_ID},
    "status": "success",
}

_EXEC_RUNNING = {"result": {"status": "RUNNING", "id": EXEC_ID}}
_EXEC_COMPLETE = {"result": {"status": "COMPLETE", "id": EXEC_ID}}
_EXEC_FAILED = {"result": {"status": "FAILED", "id": EXEC_ID}}

_ACTOR_JSON_SUCCESS = json.dumps({
    "status": "ok",
    "operation": "gdalinfo",
    "gdal_version": "GDAL 3.8.0",
    "metrics": {"duration_ms": 1234},
    "metadata": {"driverShortName": "GTiff"},
})

_ACTOR_JSON_WITH_REGISTERED = json.dumps({
    "status": "ok",
    "operation": "reproject",
    "gdal_version": "GDAL 3.8.0",
    "output_path": "/data/out/result.tif",
    "metrics": {"duration_ms": 5678},
    "registered": {
        "status": "ok",
        "resource": {"id": "new-resource-uuid", "name": "result.tif"},
    },
})

_LOGS_OK = {"result": {"logs": _ACTOR_JSON_SUCCESS}}
_LOGS_TRANSFORM_OK = {"result": {"logs": _ACTOR_JSON_WITH_REGISTERED}}
_LOGS_FAILED = {"result": {"logs": '{"status": "error", "message": "GDAL failed"}'}}


# ---------------------------------------------------------------------------
# Transform tools: no token → error, no HTTP
# ---------------------------------------------------------------------------

class TestTransformNoToken:
    def test_reproject_no_token_returns_error(self):
        from dso_geo_mcp.tools.transform import reproject_raster
        # Using the function directly (not registered MCP; unit test)
        # We can't call the mcp.tool directly, so test via the module.
        # The register() function creates closures; re-import to get fresh.
        # Instead, we test the behavior through direct calls to the functions
        # as registered by the tools module.
        pass  # Covered by test_reproject_requires_token below

    @resp_lib.activate
    def test_reproject_requires_token(self):
        """reproject_raster without token returns error dict and makes no HTTP calls."""
        import fastmcp
        from dso_geo_mcp.tools import transform

        mcp = fastmcp.FastMCP("test")
        transform.register(mcp)

        # Find the registered tool function
        tool_fn = None
        for tool_name, tool_obj in mcp._tools.items():
            if tool_name == "reproject_raster":
                tool_fn = tool_obj.fn
                break
        assert tool_fn is not None, "reproject_raster not registered"

        result = tool_fn(
            resource_id=RESOURCE_ID,
            target_crs=4326,
            output_name="result.tif",
            tapis_token=None,  # No token!
        )
        assert "error" in result
        # No HTTP calls should have been made
        assert len(resp_lib.calls) == 0

    @resp_lib.activate
    def test_convert_to_cog_requires_token(self):
        """convert_to_cog without token returns error dict and makes no HTTP calls."""
        import fastmcp
        from dso_geo_mcp.tools import transform

        mcp = fastmcp.FastMCP("test")
        transform.register(mcp)

        tool_fn = mcp._tools["convert_to_cog"].fn
        result = tool_fn(
            resource_id=RESOURCE_ID,
            output_name="result.tif",
            tapis_token=None,
        )
        assert "error" in result
        assert len(resp_lib.calls) == 0

    @resp_lib.activate
    def test_clip_raster_requires_token(self):
        """clip_raster without token returns error dict and makes no HTTP calls."""
        import fastmcp
        from dso_geo_mcp.tools import transform

        mcp = fastmcp.FastMCP("test")
        transform.register(mcp)

        tool_fn = mcp._tools["clip_raster"].fn
        result = tool_fn(
            resource_id=RESOURCE_ID,
            clip_geometry={"type": "Polygon", "coordinates": [
                [[-97.75, 30.25], [-97.70, 30.25], [-97.70, 30.30], [-97.75, 30.30], [-97.75, 30.25]]
            ]},
            output_name="result.tif",
            tapis_token=None,
        )
        assert "error" in result
        assert len(resp_lib.calls) == 0

    @resp_lib.activate
    def test_build_overviews_requires_token(self):
        """build_overviews without token returns error dict and makes no HTTP calls."""
        import fastmcp
        from dso_geo_mcp.tools import transform

        mcp = fastmcp.FastMCP("test")
        transform.register(mcp)

        tool_fn = mcp._tools["build_overviews"].fn
        result = tool_fn(
            resource_id=RESOURCE_ID,
            output_name="result.tif",
            tapis_token=None,
        )
        assert "error" in result
        assert len(resp_lib.calls) == 0


# ---------------------------------------------------------------------------
# Metadata tools: build + submit
# ---------------------------------------------------------------------------

class TestMetadataTools:
    @resp_lib.activate
    def test_gdalinfo_extract_returns_execution_id(self):
        resp_lib.add(resp_lib.GET, f"{CKAN_URL}/api/3/action/resource_show",
                     json=_RESOURCE_SHOW_OK, status=200)
        resp_lib.add(resp_lib.POST, f"{TAPIS_BASE}/v3/actors/{ACTOR_ID}/messages",
                     json=_SUBMIT_OK, status=200)

        import fastmcp
        from dso_geo_mcp.tools import metadata

        mcp = fastmcp.FastMCP("test")
        metadata.register(mcp)

        tool_fn = mcp._tools["gdalinfo_extract"].fn
        result = tool_fn(resource_id=RESOURCE_ID, tapis_token=TOKEN)

        assert result["execution_id"] == EXEC_ID
        assert result["status"] == "SUBMITTED"

    @resp_lib.activate
    def test_gdalinfo_extract_ckan_failure_returns_error(self):
        resp_lib.add(resp_lib.GET, f"{CKAN_URL}/api/3/action/resource_show",
                     json={"success": False, "error": {"message": "Not found"}}, status=200)

        import fastmcp
        from dso_geo_mcp.tools import metadata

        mcp = fastmcp.FastMCP("test")
        metadata.register(mcp)

        tool_fn = mcp._tools["gdalinfo_extract"].fn
        result = tool_fn(resource_id="nonexistent-id", tapis_token=TOKEN)
        assert "error" in result

    @resp_lib.activate
    def test_gdalinfo_summary_returns_executions(self):
        resp_lib.add(
            resp_lib.GET, f"{CKAN_URL}/api/3/action/package_show",
            json={
                "success": True,
                "result": {
                    "id": PACKAGE_ID,
                    "resources": [
                        {"id": "res-1", "url": f"{CKAN_URL}/dataset/{PACKAGE_ID}/resource/res-1/download/l1.tif", "name": "l1.tif"},
                        {"id": "res-2", "url": f"{CKAN_URL}/dataset/{PACKAGE_ID}/resource/res-2/download/l2.tif", "name": "l2.tif"},
                    ],
                },
            },
            status=200,
        )
        resp_lib.add(resp_lib.POST, f"{TAPIS_BASE}/v3/actors/{ACTOR_ID}/messages",
                     json={"result": {"execution_id": "exec-1"}}, status=200)
        resp_lib.add(resp_lib.POST, f"{TAPIS_BASE}/v3/actors/{ACTOR_ID}/messages",
                     json={"result": {"execution_id": "exec-2"}}, status=200)

        import fastmcp
        from dso_geo_mcp.tools import metadata

        mcp = fastmcp.FastMCP("test")
        metadata.register(mcp)

        tool_fn = mcp._tools["gdalinfo_summary"].fn
        result = tool_fn(dataset_id=PACKAGE_ID, tapis_token=TOKEN)
        assert result["submitted"] == 2
        assert len(result["executions"]) == 2


# ---------------------------------------------------------------------------
# Transform tools: full path with mocked CKAN + Tapis
# ---------------------------------------------------------------------------

class TestTransformTools:
    @resp_lib.activate
    def test_reproject_raster_returns_execution_id(self):
        resp_lib.add(resp_lib.GET, f"{CKAN_URL}/api/3/action/resource_show",
                     json=_RESOURCE_SHOW_OK, status=200)
        resp_lib.add(resp_lib.POST, f"{TAPIS_BASE}/v3/actors/{ACTOR_ID}/messages",
                     json=_SUBMIT_OK, status=200)

        import fastmcp
        from dso_geo_mcp.tools import transform

        mcp = fastmcp.FastMCP("test")
        transform.register(mcp)

        tool_fn = mcp._tools["reproject_raster"].fn
        result = tool_fn(
            resource_id=RESOURCE_ID,
            target_crs=4326,
            output_name="reprojected.tif",
            tapis_token=TOKEN,
        )
        assert result["execution_id"] == EXEC_ID
        assert result["status"] == "SUBMITTED"

    @resp_lib.activate
    def test_reproject_invalid_crs_returns_error_no_http(self):
        import fastmcp
        from dso_geo_mcp.tools import transform

        mcp = fastmcp.FastMCP("test")
        transform.register(mcp)

        tool_fn = mcp._tools["reproject_raster"].fn
        result = tool_fn(
            resource_id=RESOURCE_ID,
            target_crs=0,  # invalid
            output_name="result.tif",
            tapis_token=TOKEN,
        )
        assert "error" in result
        assert "target_crs" in result["error"]
        assert len(resp_lib.calls) == 0

    @resp_lib.activate
    def test_reproject_invalid_output_name_returns_error_no_http(self):
        import fastmcp
        from dso_geo_mcp.tools import transform

        mcp = fastmcp.FastMCP("test")
        transform.register(mcp)

        tool_fn = mcp._tools["reproject_raster"].fn
        result = tool_fn(
            resource_id=RESOURCE_ID,
            target_crs=4326,
            output_name="../etc/passwd",  # invalid
            tapis_token=TOKEN,
        )
        assert "error" in result
        assert "output_name" in result["error"]
        assert len(resp_lib.calls) == 0

    @resp_lib.activate
    def test_clip_raster_invalid_geometry_returns_error_no_http(self):
        import fastmcp
        from dso_geo_mcp.tools import transform

        mcp = fastmcp.FastMCP("test")
        transform.register(mcp)

        tool_fn = mcp._tools["clip_raster"].fn
        result = tool_fn(
            resource_id=RESOURCE_ID,
            clip_geometry={"type": "Point", "coordinates": [-97, 30]},  # invalid
            output_name="result.tif",
            tapis_token=TOKEN,
        )
        assert "error" in result
        assert "clip_geometry" in result["error"]
        assert len(resp_lib.calls) == 0


# ---------------------------------------------------------------------------
# get_execution_status
# ---------------------------------------------------------------------------

class TestGetExecutionStatus:
    @resp_lib.activate
    def test_returns_running_status(self):
        resp_lib.add(
            resp_lib.GET,
            f"{TAPIS_BASE}/v3/actors/{ACTOR_ID}/executions/{EXEC_ID}",
            json=_EXEC_RUNNING,
            status=200,
        )

        import fastmcp
        from dso_geo_mcp.tools import status as status_mod

        mcp = fastmcp.FastMCP("test")
        status_mod.register(mcp)

        tool_fn = mcp._tools["get_execution_status"].fn
        result = tool_fn(execution_id=EXEC_ID, tapis_token=TOKEN)
        assert result["status"] == "RUNNING"
        assert "result" not in result  # not terminal yet

    @resp_lib.activate
    def test_returns_parsed_actor_json_on_complete(self):
        resp_lib.add(
            resp_lib.GET,
            f"{TAPIS_BASE}/v3/actors/{ACTOR_ID}/executions/{EXEC_ID}",
            json=_EXEC_COMPLETE,
            status=200,
        )
        resp_lib.add(
            resp_lib.GET,
            f"{TAPIS_BASE}/v3/actors/{ACTOR_ID}/executions/{EXEC_ID}/logs",
            json=_LOGS_OK,
            status=200,
        )

        import fastmcp
        from dso_geo_mcp.tools import status as status_mod

        mcp = fastmcp.FastMCP("test")
        status_mod.register(mcp)

        tool_fn = mcp._tools["get_execution_status"].fn
        result = tool_fn(execution_id=EXEC_ID, tapis_token=TOKEN)
        assert result["status"] == "COMPLETE"
        assert "result" in result
        assert result["result"]["operation"] == "gdalinfo"
        assert result["result"]["metadata"]["driverShortName"] == "GTiff"

    @resp_lib.activate
    def test_returns_registered_block_for_transform(self):
        """When actor JSON has 'registered' field, it is surfaced at top level."""
        resp_lib.add(
            resp_lib.GET,
            f"{TAPIS_BASE}/v3/actors/{ACTOR_ID}/executions/{EXEC_ID}",
            json=_EXEC_COMPLETE,
            status=200,
        )
        resp_lib.add(
            resp_lib.GET,
            f"{TAPIS_BASE}/v3/actors/{ACTOR_ID}/executions/{EXEC_ID}/logs",
            json=_LOGS_TRANSFORM_OK,
            status=200,
        )

        import fastmcp
        from dso_geo_mcp.tools import status as status_mod

        mcp = fastmcp.FastMCP("test")
        status_mod.register(mcp)

        tool_fn = mcp._tools["get_execution_status"].fn
        result = tool_fn(execution_id=EXEC_ID, tapis_token=TOKEN)
        assert result["status"] == "COMPLETE"
        assert "registered" in result
        assert result["registered"]["status"] == "ok"

    @resp_lib.activate
    def test_failed_status_returns_error_field(self):
        resp_lib.add(
            resp_lib.GET,
            f"{TAPIS_BASE}/v3/actors/{ACTOR_ID}/executions/{EXEC_ID}",
            json=_EXEC_FAILED,
            status=200,
        )
        resp_lib.add(
            resp_lib.GET,
            f"{TAPIS_BASE}/v3/actors/{ACTOR_ID}/executions/{EXEC_ID}/logs",
            json=_LOGS_FAILED,
            status=200,
        )

        import fastmcp
        from dso_geo_mcp.tools import status as status_mod

        mcp = fastmcp.FastMCP("test")
        status_mod.register(mcp)

        tool_fn = mcp._tools["get_execution_status"].fn
        result = tool_fn(execution_id=EXEC_ID, tapis_token=TOKEN)
        assert result["status"] == "FAILED"
        assert "error" in result

    def test_invalid_execution_id_returns_error(self):
        import fastmcp
        from dso_geo_mcp.tools import status as status_mod

        mcp = fastmcp.FastMCP("test")
        status_mod.register(mcp)

        tool_fn = mcp._tools["get_execution_status"].fn
        result = tool_fn(execution_id="../../admin", tapis_token=TOKEN)
        assert "error" in result

    def test_no_token_returns_error(self):
        import fastmcp
        from dso_geo_mcp.tools import status as status_mod

        mcp = fastmcp.FastMCP("test")
        status_mod.register(mcp)

        tool_fn = mcp._tools["get_execution_status"].fn
        result = tool_fn(execution_id=EXEC_ID, tapis_token=None)
        assert "error" in result
