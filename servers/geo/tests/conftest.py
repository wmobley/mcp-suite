"""
Pytest configuration and shared fixtures for dso-geo tests.

All tests are unit tests that use mocked HTTP (via ``responses`` library).
No live Tapis or CKAN calls are made.
"""

from __future__ import annotations

import pytest


# Shared CKAN / Tapis mock values used across test files
CKAN_URL = "http://localhost:5001"
TAPIS_BASE = "https://portals.tapis.io"
ACTOR_ID = "testactor123"
EXECUTION_ID = "exec-abc-123"
RESOURCE_ID = "resource-uuid-abc"
PACKAGE_ID = "test-dataset"
DOWNLOAD_URL = f"{CKAN_URL}/dataset/{PACKAGE_ID}/resource/{RESOURCE_ID}/download/test.tif"
TAPIS_TOKEN = "eyJfaketoken.fake.token"


@pytest.fixture(autouse=True)
def patch_settings(monkeypatch):
    """Patch settings to use test values for all tests."""
    monkeypatch.setenv("GEO_ACTOR_ID", ACTOR_ID)
    monkeypatch.setenv("TAPIS_BASE", TAPIS_BASE)
    monkeypatch.setenv("CKAN_URL", CKAN_URL)
    monkeypatch.setenv("GEO_ALLOWED_CKAN_HOST", "localhost")
    # Reload settings to pick up new env
    import dso_geo_mcp.config as cfg
    # Patch the module-level singleton in-place
    cfg.settings.geo_actor_id = ACTOR_ID
    cfg.settings.tapis_base = TAPIS_BASE
    cfg.settings.ckan_url = CKAN_URL
    cfg.settings.geo_allowed_ckan_host = "localhost"
    cfg.settings.geo_tapis_token = None
    yield
    # restore defaults (the fixture runs fresh for each test anyway)
