"""
Tests for ckan_client.py — envelope parsing and Authorization scrubbing.

Uses the ``responses`` library to mock HTTP calls without touching the
network.
"""

from __future__ import annotations

import json

import pytest
import responses as resp_lib
from responses import matchers


# ---------------------------------------------------------------------------
# _scrub() unit tests  (no HTTP)
# ---------------------------------------------------------------------------


def test_scrub_removes_authorization_value():
    from dso_ckan_mcp.ckan_client import _scrub

    original = {"Authorization": "Bearer secret-token", "Content-Type": "application/json"}
    scrubbed = _scrub(original)
    assert scrubbed["Authorization"] == "[REDACTED]"
    assert scrubbed["Content-Type"] == "application/json"
    # Original not mutated.
    assert original["Authorization"] == "Bearer secret-token"


def test_scrub_case_insensitive():
    """Header key matching is case-insensitive."""
    from dso_ckan_mcp.ckan_client import _scrub

    assert _scrub({"authorization": "secret"})["authorization"] == "[REDACTED]"
    assert _scrub({"AUTHORIZATION": "secret"})["AUTHORIZATION"] == "[REDACTED]"


def test_scrub_empty():
    from dso_ckan_mcp.ckan_client import _scrub

    assert _scrub({}) == {}


def test_scrub_no_auth_header():
    from dso_ckan_mcp.ckan_client import _scrub

    headers = {"X-Custom": "value", "Accept": "application/json"}
    assert _scrub(headers) == headers


# ---------------------------------------------------------------------------
# CKANClient — envelope parsing
# ---------------------------------------------------------------------------


@resp_lib.activate
def test_get_success():
    from dso_ckan_mcp.ckan_client import CKANClient

    resp_lib.add(
        resp_lib.GET,
        "http://localhost:5001/api/3/action/status_show",
        json={"success": True, "result": {"ckan_version": "2.9.9"}},
    )
    client = CKANClient("http://localhost:5001")
    result = client.get("status_show")
    assert result == {"ckan_version": "2.9.9"}


@resp_lib.activate
def test_get_success_false_raises():
    from dso_ckan_mcp.ckan_client import CKANAPIError, CKANClient

    resp_lib.add(
        resp_lib.GET,
        "http://localhost:5001/api/3/action/package_show",
        json={
            "success": False,
            "error": {"__type": "Not Found Error", "message": "Dataset not found"},
        },
    )
    client = CKANClient("http://localhost:5001")
    with pytest.raises(CKANAPIError) as exc_info:
        client.get("package_show", params={"id": "nonexistent"})
    assert "Dataset not found" in str(exc_info.value)


@resp_lib.activate
def test_get_http_error_raises():
    from dso_ckan_mcp.ckan_client import CKANAPIError, CKANClient

    resp_lib.add(
        resp_lib.GET,
        "http://localhost:5001/api/3/action/package_show",
        status=404,
        body="Not found",
    )
    client = CKANClient("http://localhost:5001")
    with pytest.raises(CKANAPIError) as exc_info:
        client.get("package_show", params={"id": "x"})
    assert exc_info.value.status_code == 404


@resp_lib.activate
def test_authorization_not_in_exception_message():
    """The CKANAPIError message must never contain an Authorization value."""
    from dso_ckan_mcp.ckan_client import CKANAPIError, CKANClient

    resp_lib.add(
        resp_lib.GET,
        "http://localhost:5001/api/3/action/package_create",
        json={"success": False, "error": {"message": "Not authorised"}},
    )
    client = CKANClient("http://localhost:5001", api_token="ultra-secret-bearer")
    try:
        client.get("package_create")
    except CKANAPIError as exc:
        assert "ultra-secret-bearer" not in str(exc)
    else:
        pytest.fail("Expected CKANAPIError")


@resp_lib.activate
def test_post_success():
    from dso_ckan_mcp.ckan_client import CKANClient

    resp_lib.add(
        resp_lib.POST,
        "http://localhost:5001/api/3/action/package_search",
        json={"success": True, "result": {"count": 0, "results": []}},
    )
    client = CKANClient("http://localhost:5001")
    result = client.post("package_search", data={"q": "test"})
    assert result["count"] == 0


# ---------------------------------------------------------------------------
# FIX 1 security tests: token isolation between read and write calls
# Authentication model: writes use X-Tapis-Token (ckanext-oauth2 / Tapis JWT).
# ---------------------------------------------------------------------------


@resp_lib.activate
def test_get_does_not_send_auth_headers() -> None:
    """GET (read) must carry NO X-Tapis-Token or Authorization header when a token is configured."""
    from dso_ckan_mcp.ckan_client import CKANClient

    resp_lib.add(
        resp_lib.GET,
        "http://localhost:5001/api/3/action/status_show",
        json={"success": True, "result": {"ckan_version": "2.9.9"}},
    )
    client = CKANClient("http://localhost:5001", api_token="should-not-leak")
    client.get("status_show")

    assert len(resp_lib.calls) == 1
    sent_headers = resp_lib.calls[0].request.headers
    assert "Authorization" not in sent_headers, (
        f"Authorization header must not be sent on GET; got: {sent_headers}"
    )
    assert "X-Tapis-Token" not in sent_headers, (
        f"X-Tapis-Token header must not be sent on GET; got: {sent_headers}"
    )


@resp_lib.activate
def test_post_sends_x_tapis_token_header_when_token_configured() -> None:
    """POST (write) must carry X-Tapis-Token (not Authorization) when an env-fallback token is set."""
    from dso_ckan_mcp.ckan_client import CKANClient

    resp_lib.add(
        resp_lib.POST,
        "http://localhost:5001/api/3/action/package_create",
        json={"success": True, "result": {"id": "new-pkg"}},
    )
    client = CKANClient("http://localhost:5001", api_token="my-write-token")
    client.post("package_create", data={"name": "test-pkg", "type": "dataset"})

    assert len(resp_lib.calls) == 1
    sent_headers = resp_lib.calls[0].request.headers
    assert sent_headers.get("X-Tapis-Token") == "my-write-token", (
        f"X-Tapis-Token must be 'my-write-token' on POST; got: {sent_headers.get('X-Tapis-Token')}"
    )
    # Authorization must NOT be set — the portal uses X-Tapis-Token.
    assert "Authorization" not in sent_headers, (
        f"Authorization must not be set; got: {sent_headers.get('Authorization')}"
    )


@resp_lib.activate
def test_post_sends_x_tapis_token_from_per_call_arg() -> None:
    """POST must send X-Tapis-Token from the per-call token arg, ignoring the env fallback."""
    from dso_ckan_mcp.ckan_client import CKANClient

    resp_lib.add(
        resp_lib.POST,
        "http://localhost:5001/api/3/action/package_create",
        json={"success": True, "result": {"id": "new-pkg"}},
    )
    # env fallback has one token; per-call arg supplies a different one
    client = CKANClient("http://localhost:5001", api_token="env-fallback-token")
    client.post("package_create", data={"name": "test-pkg", "type": "dataset"}, token="per-call-token")

    assert len(resp_lib.calls) == 1
    sent_headers = resp_lib.calls[0].request.headers
    assert sent_headers.get("X-Tapis-Token") == "per-call-token", (
        f"X-Tapis-Token must be the per-call token; got: {sent_headers.get('X-Tapis-Token')}"
    )


@resp_lib.activate
def test_post_no_auth_headers_when_no_token() -> None:
    """POST without a configured token must send NO X-Tapis-Token or Authorization header."""
    from dso_ckan_mcp.ckan_client import CKANClient

    resp_lib.add(
        resp_lib.POST,
        "http://localhost:5001/api/3/action/package_search",
        json={"success": True, "result": {"count": 0, "results": []}},
    )
    client = CKANClient("http://localhost:5001", api_token=None)
    client.post("package_search", data={"q": "test"})

    assert len(resp_lib.calls) == 1
    sent_headers = resp_lib.calls[0].request.headers
    assert "Authorization" not in sent_headers
    assert "X-Tapis-Token" not in sent_headers


# ---------------------------------------------------------------------------
# _scrub() — X-Tapis-Token redaction
# ---------------------------------------------------------------------------


def test_scrub_removes_x_tapis_token():
    """_scrub must redact X-Tapis-Token in addition to Authorization."""
    from dso_ckan_mcp.ckan_client import _scrub

    original = {"X-Tapis-Token": "jwt-secret", "Content-Type": "application/json"}
    scrubbed = _scrub(original)
    assert scrubbed["X-Tapis-Token"] == "[REDACTED]"
    assert scrubbed["Content-Type"] == "application/json"
    # Original not mutated.
    assert original["X-Tapis-Token"] == "jwt-secret"


def test_scrub_removes_x_tapis_token_case_insensitive():
    """_scrub must be case-insensitive for x-tapis-token."""
    from dso_ckan_mcp.ckan_client import _scrub

    assert _scrub({"x-tapis-token": "secret"})["x-tapis-token"] == "[REDACTED]"
    assert _scrub({"X-TAPIS-TOKEN": "secret"})["X-TAPIS-TOKEN"] == "[REDACTED]"
