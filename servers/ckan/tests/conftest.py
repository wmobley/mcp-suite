"""
Pytest configuration and shared fixtures.

Integration tests are automatically skipped when the CKAN portal at
http://localhost:5001 is not reachable.  No manual skip markers are
needed in test files — just mark tests with ``@pytest.mark.integration``
and they will be gated here.
"""

from __future__ import annotations

import socket

import pytest

PORTAL_URL = "http://localhost:5001"
PORTAL_HOST = "localhost"
PORTAL_PORT = 5001


def _portal_reachable() -> bool:
    """Return True if the CKAN dev portal is reachable via TCP."""
    try:
        with socket.create_connection((PORTAL_HOST, PORTAL_PORT), timeout=2):
            return True
    except OSError:
        return False


# Evaluate once at collection time.
_PORTAL_UP = _portal_reachable()


@pytest.fixture(scope="session")
def portal_url() -> str:
    """Base URL of the dev portal (used by integration tests)."""
    return PORTAL_URL


@pytest.fixture(scope="session")
def live_client():
    """A CKANClient pointed at the live dev portal (integration tests only)."""
    if not _PORTAL_UP:
        pytest.skip("CKAN portal not reachable at localhost:5001")
    from dso_ckan_mcp.ckan_client import CKANClient

    return CKANClient(base_url=PORTAL_URL)


@pytest.fixture(scope="session")
def live_loader(live_client):
    """A SchemaLoader backed by the live dev portal (integration tests only)."""
    from dso_ckan_mcp.schema_loader import SchemaLoader

    return SchemaLoader(client=live_client, ttl=300)


def pytest_runtest_setup(item: pytest.Item) -> None:
    """Skip integration-marked tests when the portal is unreachable."""
    if "integration" in item.keywords and not _PORTAL_UP:
        pytest.skip("CKAN portal not reachable at localhost:5001 — skipping integration test")
