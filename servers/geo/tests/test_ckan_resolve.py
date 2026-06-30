"""
Tests for ckan_resolve.py — CKAN resource/dataset URL resolution and SSRF guard.

Uses the ``responses`` library to mock CKAN API calls.
Verifies:
- resource_show resolution: returns (download_url, package_id).
- SSRF host allowlist rejects a resolved URL on a different host.
- package_show resolution: returns list of resource records.
- Private / loopback IPs are rejected.
- Non-http schemes are rejected.
"""

from __future__ import annotations

import json

import pytest
import responses as resp_lib

from dso_geo_mcp.ckan_resolve import (
    CKANResolveError,
    resolve_dataset_raster_urls,
    resolve_resource_url,
)

CKAN_URL = "http://localhost:5001"
RESOURCE_ID = "resource-uuid-abc"
PACKAGE_ID = "test-dataset"
DOWNLOAD_URL = f"{CKAN_URL}/dataset/{PACKAGE_ID}/resource/{RESOURCE_ID}/download/test.tif"

RESOURCE_SHOW_RESPONSE = {
    "success": True,
    "result": {
        "id": RESOURCE_ID,
        "package_id": PACKAGE_ID,
        "url": DOWNLOAD_URL,
        "name": "test.tif",
    },
}

PACKAGE_SHOW_RESPONSE = {
    "success": True,
    "result": {
        "id": PACKAGE_ID,
        "name": PACKAGE_ID,
        "resources": [
            {
                "id": "res-1",
                "url": f"{CKAN_URL}/dataset/{PACKAGE_ID}/resource/res-1/download/layer1.tif",
                "name": "layer1.tif",
            },
            {
                "id": "res-2",
                "url": f"{CKAN_URL}/dataset/{PACKAGE_ID}/resource/res-2/download/layer2.tif",
                "name": "layer2.tif",
            },
        ],
    },
}


# ---------------------------------------------------------------------------
# resolve_resource_url
# ---------------------------------------------------------------------------

class TestResolveResourceUrl:
    @resp_lib.activate
    def test_returns_download_url_and_package_id(self):
        resp_lib.add(
            resp_lib.GET,
            f"{CKAN_URL}/api/3/action/resource_show",
            json=RESOURCE_SHOW_RESPONSE,
            status=200,
        )
        url, pkg_id = resolve_resource_url(RESOURCE_ID)
        assert url == DOWNLOAD_URL
        assert pkg_id == PACKAGE_ID

    @resp_lib.activate
    def test_raises_on_http_error(self):
        resp_lib.add(
            resp_lib.GET,
            f"{CKAN_URL}/api/3/action/resource_show",
            json={"success": False, "error": {"message": "Not found"}},
            status=404,
        )
        with pytest.raises(CKANResolveError):
            resolve_resource_url(RESOURCE_ID)

    @resp_lib.activate
    def test_raises_on_ckan_success_false(self):
        resp_lib.add(
            resp_lib.GET,
            f"{CKAN_URL}/api/3/action/resource_show",
            json={"success": False, "error": {"message": "Resource not found"}},
            status=200,
        )
        with pytest.raises(CKANResolveError):
            resolve_resource_url(RESOURCE_ID)

    @resp_lib.activate
    def test_ssrf_rejects_different_host(self):
        """Resolved URL pointing to a different host must be rejected."""
        resp_lib.add(
            resp_lib.GET,
            f"{CKAN_URL}/api/3/action/resource_show",
            json={
                "success": True,
                "result": {
                    "id": RESOURCE_ID,
                    "package_id": PACKAGE_ID,
                    "url": "https://evil.attacker.com/data.tif",
                    "name": "data.tif",
                },
            },
            status=200,
        )
        with pytest.raises(ValueError, match="SSRF guard"):
            resolve_resource_url(RESOURCE_ID)

    @resp_lib.activate
    def test_ssrf_rejects_private_ip(self):
        """Resolved URL pointing to a private IP is rejected."""
        resp_lib.add(
            resp_lib.GET,
            f"{CKAN_URL}/api/3/action/resource_show",
            json={
                "success": True,
                "result": {
                    "id": RESOURCE_ID,
                    "package_id": PACKAGE_ID,
                    "url": "http://10.0.0.1/internal/data.tif",
                    "name": "data.tif",
                },
            },
            status=200,
        )
        with pytest.raises(ValueError):
            resolve_resource_url(RESOURCE_ID)

    @resp_lib.activate
    def test_ssrf_rejects_file_scheme(self):
        """file:// URLs must be rejected."""
        resp_lib.add(
            resp_lib.GET,
            f"{CKAN_URL}/api/3/action/resource_show",
            json={
                "success": True,
                "result": {
                    "id": RESOURCE_ID,
                    "package_id": PACKAGE_ID,
                    "url": "file:///etc/passwd",
                    "name": "passwd",
                },
            },
            status=200,
        )
        with pytest.raises(ValueError):
            resolve_resource_url(RESOURCE_ID)

    @resp_lib.activate
    def test_raises_when_url_is_empty(self):
        resp_lib.add(
            resp_lib.GET,
            f"{CKAN_URL}/api/3/action/resource_show",
            json={
                "success": True,
                "result": {
                    "id": RESOURCE_ID,
                    "package_id": PACKAGE_ID,
                    "url": "",
                    "name": "test.tif",
                },
            },
            status=200,
        )
        with pytest.raises(CKANResolveError):
            resolve_resource_url(RESOURCE_ID)


# ---------------------------------------------------------------------------
# resolve_dataset_raster_urls
# ---------------------------------------------------------------------------

class TestResolveDatasetRasterUrls:
    @resp_lib.activate
    def test_returns_resource_list(self):
        resp_lib.add(
            resp_lib.GET,
            f"{CKAN_URL}/api/3/action/package_show",
            json=PACKAGE_SHOW_RESPONSE,
            status=200,
        )
        resources = resolve_dataset_raster_urls(PACKAGE_ID)
        assert len(resources) == 2
        assert resources[0]["resource_id"] == "res-1"
        assert resources[1]["resource_id"] == "res-2"

    @resp_lib.activate
    def test_caps_at_max_resources(self):
        many_resources = [
            {
                "id": f"res-{i}",
                "url": f"{CKAN_URL}/dataset/{PACKAGE_ID}/resource/res-{i}/download/layer{i}.tif",
                "name": f"layer{i}.tif",
            }
            for i in range(15)
        ]
        resp_lib.add(
            resp_lib.GET,
            f"{CKAN_URL}/api/3/action/package_show",
            json={
                "success": True,
                "result": {"id": PACKAGE_ID, "resources": many_resources},
            },
            status=200,
        )
        resources = resolve_dataset_raster_urls(PACKAGE_ID, max_resources=10)
        assert len(resources) == 10

    @resp_lib.activate
    def test_skips_resources_with_ssrf_violation(self):
        """Resources with disallowed URLs are silently skipped (with a warning)."""
        resources = [
            {
                "id": "good-res",
                "url": f"{CKAN_URL}/dataset/{PACKAGE_ID}/resource/good/download/ok.tif",
                "name": "ok.tif",
            },
            {
                "id": "evil-res",
                "url": "https://evil.attacker.com/steal.tif",
                "name": "steal.tif",
            },
        ]
        resp_lib.add(
            resp_lib.GET,
            f"{CKAN_URL}/api/3/action/package_show",
            json={"success": True, "result": {"id": PACKAGE_ID, "resources": resources}},
            status=200,
        )
        result = resolve_dataset_raster_urls(PACKAGE_ID)
        assert len(result) == 1
        assert result[0]["resource_id"] == "good-res"

    @resp_lib.activate
    def test_raises_on_dataset_not_found(self):
        resp_lib.add(
            resp_lib.GET,
            f"{CKAN_URL}/api/3/action/package_show",
            json={"success": False, "error": {"message": "Not found"}},
            status=200,
        )
        with pytest.raises(CKANResolveError):
            resolve_dataset_raster_urls(PACKAGE_ID)

    @resp_lib.activate
    def test_skips_resources_with_empty_url(self):
        resources = [
            {"id": "res-no-url", "url": "", "name": "empty.tif"},
            {
                "id": "res-ok",
                "url": f"{CKAN_URL}/dataset/{PACKAGE_ID}/resource/res-ok/download/ok.tif",
                "name": "ok.tif",
            },
        ]
        resp_lib.add(
            resp_lib.GET,
            f"{CKAN_URL}/api/3/action/package_show",
            json={"success": True, "result": {"id": PACKAGE_ID, "resources": resources}},
            status=200,
        )
        result = resolve_dataset_raster_urls(PACKAGE_ID)
        assert len(result) == 1
        assert result[0]["resource_id"] == "res-ok"
