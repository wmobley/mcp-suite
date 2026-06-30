"""
Tests for tools/write.py — write gate, dry-run, happy paths, and audit.

All HTTP is mocked with the ``responses`` library.  No live CKAN calls are
made.  Integration tests that perform real writes are separately gated
(@pytest.mark.integration + MCP_LIVE_WRITE_TESTS=1 + CKAN_API_TOKEN set).
"""

from __future__ import annotations

import logging
import os
import time
from pathlib import Path
from typing import Any

import pytest
import responses as resp_lib

from dso_ckan_mcp.ckan_client import CKANClient
from dso_ckan_mcp.config import Settings
from dso_ckan_mcp.schema_loader import SchemaLoader
from dso_ckan_mcp.tools import write as write_module

# ---------------------------------------------------------------------------
# Shared test infrastructure (mirrors _FakeMCP from test_read_tools.py)
# ---------------------------------------------------------------------------

BASE_URL = "http://localhost:5001"

MINT_SCHEMA = {
    "dataset_type": "mint_dataset",
    "dataset_fields": [
        {"field_name": "title", "label": "Title", "required": True},
        {
            "field_name": "name",
            "validators": "not_empty unicode_safe name_validator package_name_validator",
        },
        {"field_name": "owner_org", "label": "Organization", "required": False},
        {
            "field_name": "temporal_coverage_start",
            "validators": "scheming_required isodate convert_to_json_if_date",
            "preset": "date",
        },
        {
            "field_name": "temporal_coverage_end",
            "validators": "scheming_required isodate convert_to_json_if_date",
            "preset": "date",
        },
    ],
    "resource_fields": [],
}

VALID_METADATA = {
    "title": "Rain 2024",
    "name": "rain-2024",
    "temporal_coverage_start": "2024-01-01",
    "temporal_coverage_end": "2024-12-31",
}


class _FakeMCP:
    """Captures functions registered via the @mcp.tool() decorator by name."""

    def __init__(self) -> None:
        self.tools: dict[str, Any] = {}

    def tool(self) -> Any:
        def decorator(fn: Any) -> Any:
            self.tools[fn.__name__] = fn
            return fn

        return decorator


def _make_settings(
    *,
    token: str | None = "test-token",
    prod_writes: bool = False,
    ckan_url: str = BASE_URL,
    upload_dir: str | None = None,
    max_upload_mb: int = 90,
) -> Settings:
    s = Settings.__new__(Settings)
    s.ckan_url = ckan_url
    s.ckan_api_token = token
    s.mcp_allow_prod_writes = prod_writes
    s.mcp_upload_dir = upload_dir
    s.mcp_max_upload_mb = max_upload_mb
    s.schema_cache_ttl = 3600
    return s


def _make_client(token: str | None = "test-token") -> CKANClient:
    return CKANClient(BASE_URL, api_token=token)


def _make_loader_with_schema(schema: dict = MINT_SCHEMA) -> SchemaLoader:
    """Return a SchemaLoader whose cache is pre-loaded with the given schema."""
    client = _make_client()
    loader = SchemaLoader(client=client, ttl=3600)
    dataset_type = schema["dataset_type"]
    loader._types_cache = ([dataset_type], time.monotonic() + 3600)
    loader._schema_cache[dataset_type] = (schema, time.monotonic() + 3600)
    return loader


def _setup(
    *,
    token: str | None = "test-token",
    prod_writes: bool = False,
    ckan_url: str = BASE_URL,
    upload_dir: str | None = None,
    schema: dict = MINT_SCHEMA,
) -> tuple[dict[str, Any], CKANClient, Settings]:
    """Build a _FakeMCP, register write tools, return (tools_dict, client, settings)."""
    settings = _make_settings(
        token=token,
        prod_writes=prod_writes,
        ckan_url=ckan_url,
        upload_dir=upload_dir,
    )
    client = CKANClient(ckan_url, api_token=token)
    loader = _make_loader_with_schema(schema)
    mcp = _FakeMCP()
    write_module.register(mcp, client, loader, settings)
    return mcp.tools, client, settings


# ---------------------------------------------------------------------------
# Dry-run: NO HTTP posted, returns preview
# ---------------------------------------------------------------------------


@resp_lib.activate
def test_dry_run_create_no_http_posted() -> None:
    """dry_run=True must return a preview dict and make NO POST request."""
    tools, _, _ = _setup()
    result = tools["schema_create_package"](
        dataset_type="mint_dataset",
        metadata=VALID_METADATA,
        dry_run=True,
    )
    assert result["dry_run"] is True
    assert "preview" in result
    assert "errors" in result
    assert "warnings" in result
    # No HTTP calls should have been made.
    assert len(resp_lib.calls) == 0


@resp_lib.activate
def test_dry_run_update_no_http_posted() -> None:
    """dry_run=True for update fetches existing (GET) but makes no POST."""
    # Mock the package_show GET call.
    resp_lib.add(
        resp_lib.GET,
        f"{BASE_URL}/api/3/action/package_show",
        json={"success": True, "result": {"id": "abc-123", "type": "mint_dataset", "name": "existing"}},
    )

    tools, _, _ = _setup()
    result = tools["schema_update_package"](
        id="abc-123",
        metadata_updates={"title": "New Title"},
        dry_run=True,
    )
    assert result["dry_run"] is True
    assert "preview" in result
    # Only the GET was called; no POST.
    post_calls = [c for c in resp_lib.calls if c.request.method == "POST"]
    assert post_calls == []


# ---------------------------------------------------------------------------
# Write gate: no token → error, no POST
# ---------------------------------------------------------------------------


@resp_lib.activate
def test_live_create_no_token_returns_error() -> None:
    """dry_run=False without a token returns token-error dict and makes no POST."""
    tools, _, _ = _setup(token=None)
    result = tools["schema_create_package"](
        dataset_type="mint_dataset",
        metadata=VALID_METADATA,
        dry_run=False,
    )
    assert result["error"] == "writes_require_token"
    assert "CKAN_API_TOKEN" in result["message"]
    assert len(resp_lib.calls) == 0


# ---------------------------------------------------------------------------
# Write gate: production URL + MCP_ALLOW_PROD_WRITES unset → error, no POST
# ---------------------------------------------------------------------------


@resp_lib.activate
def test_live_create_prod_url_no_flag_returns_error() -> None:
    """dry_run=False on a production URL without MCP_ALLOW_PROD_WRITES=true is refused."""
    prod_url = "https://ckan.tacc.cloud"
    settings = _make_settings(token="mytoken", prod_writes=False, ckan_url=prod_url)
    loader = _make_loader_with_schema()
    client = CKANClient(prod_url, api_token="mytoken")
    mcp = _FakeMCP()
    write_module.register(mcp, client, loader, settings)
    tools = mcp.tools

    result = tools["schema_create_package"](
        dataset_type="mint_dataset",
        metadata=VALID_METADATA,
        dry_run=False,
    )
    assert result["error"] == "prod_writes_not_allowed"
    assert prod_url in result["message"]
    assert "MCP_ALLOW_PROD_WRITES" in result["message"]
    assert len(resp_lib.calls) == 0


# ---------------------------------------------------------------------------
# Write gate: production URL + MCP_ALLOW_PROD_WRITES=true → proceeds (mocked)
# ---------------------------------------------------------------------------


@resp_lib.activate
def test_live_create_prod_url_with_flag_proceeds() -> None:
    """With MCP_ALLOW_PROD_WRITES=true, a production-URL write is attempted."""
    prod_url = "https://ckan.tacc.cloud"
    settings = _make_settings(token="mytoken", prod_writes=True, ckan_url=prod_url)
    loader = _make_loader_with_schema()
    client = CKANClient(prod_url, api_token="mytoken")
    mcp = _FakeMCP()
    write_module.register(mcp, client, loader, settings)
    tools = mcp.tools

    resp_lib.add(
        resp_lib.POST,
        f"{prod_url}/api/3/action/package_create",
        json={"success": True, "result": {"id": "new-id", "type": "mint_dataset"}},
    )

    result = tools["schema_create_package"](
        dataset_type="mint_dataset",
        metadata=VALID_METADATA,
        dry_run=False,
    )
    assert result["dry_run"] is False
    assert result["success"] is True
    assert result["id"] == "new-id"


# ---------------------------------------------------------------------------
# Happy path: schema_create_package (mocked)
# ---------------------------------------------------------------------------


@resp_lib.activate
def test_live_create_package_success() -> None:
    """Live create returns success dict with id on a 200 response."""
    tools, _, _ = _setup()

    resp_lib.add(
        resp_lib.POST,
        f"{BASE_URL}/api/3/action/package_create",
        json={"success": True, "result": {"id": "abc-123", "name": "rain-2024"}},
    )

    result = tools["schema_create_package"](
        dataset_type="mint_dataset",
        metadata=VALID_METADATA,
        dry_run=False,
    )
    assert result["dry_run"] is False
    assert result["success"] is True
    assert result["id"] == "abc-123"


# ---------------------------------------------------------------------------
# Validation errors block live create
# ---------------------------------------------------------------------------


@resp_lib.activate
def test_live_create_validation_errors_block_post() -> None:
    """If metadata has errors, live create is refused and no POST is made."""
    tools, _, _ = _setup()

    result = tools["schema_create_package"](
        dataset_type="mint_dataset",
        metadata={},  # Missing all required fields.
        dry_run=False,
    )
    assert result["success"] is False
    assert result["error"] == "validation_failed"
    assert len(result["errors"]) > 0
    assert len(resp_lib.calls) == 0


# ---------------------------------------------------------------------------
# Happy path: schema_update_package (mocked)
# ---------------------------------------------------------------------------


@resp_lib.activate
def test_live_update_package_success() -> None:
    """Live update returns success dict with id on a 200 response."""
    tools, _, _ = _setup()

    resp_lib.add(
        resp_lib.GET,
        f"{BASE_URL}/api/3/action/package_show",
        json={"success": True, "result": {"id": "abc-123", "type": "mint_dataset", "name": "existing"}},
    )
    resp_lib.add(
        resp_lib.POST,
        f"{BASE_URL}/api/3/action/package_patch",
        json={"success": True, "result": {"id": "abc-123", "title": "Updated Title"}},
    )

    result = tools["schema_update_package"](
        id="abc-123",
        metadata_updates={"title": "Updated Title"},
        dry_run=False,
    )
    assert result["dry_run"] is False
    assert result["success"] is True


# ---------------------------------------------------------------------------
# Happy path: schema_create_resource (mocked, no upload)
# ---------------------------------------------------------------------------


@resp_lib.activate
def test_live_create_resource_no_upload_success() -> None:
    """Metadata-only resource create succeeds without upload_file."""
    tools, _, _ = _setup()

    resp_lib.add(
        resp_lib.POST,
        f"{BASE_URL}/api/3/action/resource_create",
        json={"success": True, "result": {"id": "res-456", "name": "My CSV"}},
    )

    result = tools["schema_create_resource"](
        package_id="abc-123",
        resource_metadata={"name": "My CSV", "format": "CSV"},
        upload_file=None,
        dry_run=False,
    )
    assert result["dry_run"] is False
    assert result["success"] is True
    assert result["id"] == "res-456"


# ---------------------------------------------------------------------------
# Happy path: schema_create_resource with file upload (mocked)
# ---------------------------------------------------------------------------


@resp_lib.activate
def test_live_create_resource_with_upload_success(tmp_path: Path) -> None:
    """Resource create with a valid upload_file sends the file and returns id."""
    allowed = tmp_path / "uploads"
    allowed.mkdir()
    f = allowed / "data.csv"
    f.write_bytes(b"col1,col2\n1,2\n")

    tools, _, _ = _setup(upload_dir=str(allowed))

    resp_lib.add(
        resp_lib.POST,
        f"{BASE_URL}/api/3/action/resource_create",
        json={"success": True, "result": {"id": "res-789", "name": "data.csv"}},
    )

    result = tools["schema_create_resource"](
        package_id="abc-123",
        resource_metadata={"name": "data.csv", "format": "CSV"},
        upload_file=str(f),
        dry_run=False,
    )
    assert result["dry_run"] is False
    assert result["success"] is True
    assert result["id"] == "res-789"


# ---------------------------------------------------------------------------
# Dry-run resource with upload: validates file but does NOT post
# ---------------------------------------------------------------------------


@resp_lib.activate
def test_dry_run_resource_with_upload_validates_but_no_post(tmp_path: Path) -> None:
    """Dry-run with upload_file validates the path/size but makes no POST."""
    allowed = tmp_path / "uploads"
    allowed.mkdir()
    f = allowed / "data.csv"
    f.write_bytes(b"col1\n1\n")

    tools, _, _ = _setup(upload_dir=str(allowed))

    result = tools["schema_create_resource"](
        package_id="abc-123",
        resource_metadata={"name": "data.csv"},
        upload_file=str(f),
        dry_run=True,
    )
    assert result["dry_run"] is True
    assert "upload" in result
    assert result["upload"] is not None
    assert len(resp_lib.calls) == 0


# ---------------------------------------------------------------------------
# Dry-run resource: invalid upload path returns error immediately
# ---------------------------------------------------------------------------


@resp_lib.activate
def test_dry_run_resource_invalid_upload_path_returns_error() -> None:
    """If upload_file path is invalid on dry-run, returns an error dict."""
    tools, _, _ = _setup(upload_dir=None)  # uploads disabled

    result = tools["schema_create_resource"](
        package_id="abc-123",
        resource_metadata={"name": "x"},
        upload_file="/some/file.csv",
        dry_run=True,
    )
    assert result["error"] == "upload_path_invalid"
    assert len(resp_lib.calls) == 0


# ---------------------------------------------------------------------------
# Audit: structured log line emitted on live write; token NOT in log
# ---------------------------------------------------------------------------


@resp_lib.activate
def test_audit_log_emitted_on_live_write(caplog: pytest.LogCaptureFixture) -> None:
    """A live write emits a structured AUDIT log line via dso_ckan_mcp.audit."""
    settings = _make_settings(token="super-secret-token")
    loader = _make_loader_with_schema()
    client = CKANClient(BASE_URL, api_token="super-secret-token")
    mcp = _FakeMCP()
    write_module.register(mcp, client, loader, settings)
    tools = mcp.tools

    resp_lib.add(
        resp_lib.POST,
        f"{BASE_URL}/api/3/action/package_create",
        json={"success": True, "result": {"id": "audit-test-id"}},
    )

    with caplog.at_level(logging.INFO, logger="dso_ckan_mcp.audit"):
        tools["schema_create_package"](
            dataset_type="mint_dataset",
            metadata=VALID_METADATA,
            dry_run=False,
        )

    audit_lines = [r.message for r in caplog.records if "AUDIT" in r.message]
    assert len(audit_lines) >= 1, f"No AUDIT line found. Records: {caplog.records}"

    line = audit_lines[0]
    assert "schema_create_package" in line
    assert "audit-test-id" in line
    # Token must NOT appear in the audit line.
    assert "super-secret-token" not in line


@resp_lib.activate
def test_audit_log_does_not_contain_token(caplog: pytest.LogCaptureFixture) -> None:
    """Audit log line from a failed write also must not contain the token."""
    settings = _make_settings(token="my-private-token")
    loader = _make_loader_with_schema()
    client = CKANClient(BASE_URL, api_token="my-private-token")
    mcp = _FakeMCP()
    write_module.register(mcp, client, loader, settings)
    tools = mcp.tools

    resp_lib.add(
        resp_lib.POST,
        f"{BASE_URL}/api/3/action/package_create",
        json={"success": False, "error": {"message": "Validation Error"}},
        status=409,
    )

    with caplog.at_level(logging.INFO, logger="dso_ckan_mcp.audit"):
        tools["schema_create_package"](
            dataset_type="mint_dataset",
            metadata=VALID_METADATA,
            dry_run=False,
        )

    for record in caplog.records:
        assert "my-private-token" not in record.message


# ---------------------------------------------------------------------------
# Invalid dataset_type returns error (no HTTP)
# ---------------------------------------------------------------------------


@resp_lib.activate
def test_invalid_dataset_type_returns_error() -> None:
    """An unknown dataset_type returns an error dict without any HTTP call."""
    client = _make_client()
    loader = SchemaLoader(client=client, ttl=3600)
    # Pre-load the type list — does NOT include "bad_type".
    loader._types_cache = (["mint_dataset", "dataset"], time.monotonic() + 3600)
    settings = _make_settings()
    mcp = _FakeMCP()
    write_module.register(mcp, client, loader, settings)
    tools = mcp.tools

    result = tools["schema_create_package"](
        dataset_type="bad_type",
        metadata={},
        dry_run=True,
    )
    assert result["error"] == "invalid_dataset_type"
    assert len(resp_lib.calls) == 0


# ---------------------------------------------------------------------------
# audit._scrub_args helper
# ---------------------------------------------------------------------------


def test_scrub_args_removes_token_keys() -> None:
    """_scrub_args must redact any key containing 'token'."""
    from dso_ckan_mcp.audit import _scrub_args

    raw = {"dataset_type": "mint_dataset", "token": "secret-value", "metadata": {"title": "T"}}
    scrubbed = _scrub_args(raw)
    assert scrubbed["token"] == "[REDACTED]"
    assert scrubbed["dataset_type"] == "mint_dataset"


def test_scrub_args_removes_file_handles(tmp_path: Path) -> None:
    """_scrub_args must replace file handles with '<file>'."""
    from dso_ckan_mcp.audit import _scrub_args
    import io

    fh = io.BytesIO(b"data")
    scrubbed = _scrub_args({"upload": fh, "name": "data.csv"})
    assert scrubbed["upload"] == "<file>"
    assert scrubbed["name"] == "data.csv"


def test_scrub_args_leaves_safe_keys() -> None:
    """_scrub_args must not alter non-sensitive, non-file values."""
    from dso_ckan_mcp.audit import _scrub_args

    raw = {"dataset_type": "mint_dataset", "package_id": "abc"}
    scrubbed = _scrub_args(raw)
    assert scrubbed == raw


# ---------------------------------------------------------------------------
# Integration test guard (skipped by default; requires MCP_LIVE_WRITE_TESTS=1
# AND CKAN_API_TOKEN set AND portal reachable)
# ---------------------------------------------------------------------------


@pytest.mark.integration
def test_live_write_integration_guard() -> None:
    """Placeholder: real live-write integration tests are opt-in only.

    This test is skipped unless ALL of the following are true:
    - @pytest.mark.integration and the portal is reachable (handled by conftest)
    - MCP_LIVE_WRITE_TESTS=1 environment variable is set
    - CKAN_API_TOKEN environment variable is set

    These checks prevent accidental real writes during CI or normal test runs.
    """
    if os.environ.get("MCP_LIVE_WRITE_TESTS") != "1":
        pytest.skip("MCP_LIVE_WRITE_TESTS=1 not set — skipping live write integration test")
    if not os.environ.get("CKAN_API_TOKEN"):
        pytest.skip("CKAN_API_TOKEN not set — skipping live write integration test")
    # If we reach here, the integration gate is open.
    # Real write tests would go here.
    pytest.skip("No live write test body implemented yet — add tests when ready")


# ---------------------------------------------------------------------------
# FIX 2: network error (status_code==0) must NOT produce dataset_not_found
# ---------------------------------------------------------------------------


@resp_lib.activate
def test_update_package_network_error_returns_ckan_error_not_not_found() -> None:
    """A connection error (status 0) during package_show dry-run returns ckan_error."""
    import requests as _requests

    tools, _, _ = _setup()

    # Use requests.exceptions.ConnectionError so it is caught by RequestException
    # inside ckan_client.get(), producing CKANAPIError(status_code=0).
    resp_lib.add(
        resp_lib.GET,
        f"{BASE_URL}/api/3/action/package_show",
        body=_requests.exceptions.ConnectionError("connection refused"),
    )

    result = tools["schema_update_package"](
        id="some-dataset",
        metadata_updates={"title": "New Title"},
        dry_run=True,
    )
    assert result.get("error") == "ckan_error", (
        f"Expected ckan_error for network failure, got: {result}"
    )
    assert result.get("error") != "dataset_not_found"


# ---------------------------------------------------------------------------
# FIX 4: empty metadata_updates returns error before any POST
# ---------------------------------------------------------------------------


@resp_lib.activate
def test_update_package_empty_metadata_updates_returns_error() -> None:
    """Empty metadata_updates returns a no_updates error dict without any HTTP call."""
    tools, _, _ = _setup()

    result = tools["schema_update_package"](
        id="some-dataset",
        metadata_updates={},
        dry_run=False,
    )
    assert result.get("error") == "no_updates"
    assert "empty" in result.get("message", "").lower()
    # No network call should have been made.
    assert len(resp_lib.calls) == 0


@resp_lib.activate
def test_update_package_empty_metadata_updates_dry_run_also_returns_error() -> None:
    """Empty metadata_updates returns no_updates on dry-run path too."""
    tools, _, _ = _setup()

    result = tools["schema_update_package"](
        id="some-dataset",
        metadata_updates={},
        dry_run=True,
    )
    assert result.get("error") == "no_updates"
    assert len(resp_lib.calls) == 0


# ---------------------------------------------------------------------------
# FIX 5: blocked writes emit an audit log line (no token in it)
# ---------------------------------------------------------------------------


@resp_lib.activate
def test_blocked_write_emits_audit_log_no_token(caplog: pytest.LogCaptureFixture) -> None:
    """When a live write is blocked (no token), an audit WARNING line is emitted."""
    tools, _, _ = _setup(token=None)

    with caplog.at_level(logging.WARNING, logger="dso_ckan_mcp.audit"):
        result = tools["schema_create_package"](
            dataset_type="mint_dataset",
            metadata=VALID_METADATA,
            dry_run=False,
        )

    assert result["error"] == "writes_require_token"
    audit_lines = [r.message for r in caplog.records if "AUDIT" in r.message]
    assert len(audit_lines) >= 1, f"Expected at least one AUDIT line; got: {caplog.records}"
    line = audit_lines[0]
    assert "blocked" in line
    assert "no_token" in line
    # No token should appear in the log (there is none here, but verify the pattern).
    assert "secret" not in line.lower()


@resp_lib.activate
def test_blocked_write_prod_guard_emits_audit_log(caplog: pytest.LogCaptureFixture) -> None:
    """When a live write is blocked (prod guard), an audit WARNING line is emitted."""
    prod_url = "https://ckan.tacc.cloud"
    settings = _make_settings(token="prod-token", prod_writes=False, ckan_url=prod_url)
    loader = _make_loader_with_schema()
    client = _make_client(token="prod-token")
    mcp = _FakeMCP()
    write_module.register(mcp, client, loader, settings)
    tools = mcp.tools

    with caplog.at_level(logging.WARNING, logger="dso_ckan_mcp.audit"):
        result = tools["schema_create_package"](
            dataset_type="mint_dataset",
            metadata=VALID_METADATA,
            dry_run=False,
        )

    assert result["error"] == "prod_writes_not_allowed"
    audit_lines = [r.message for r in caplog.records if "AUDIT" in r.message]
    assert len(audit_lines) >= 1, f"Expected at least one AUDIT line; got: {caplog.records}"
    line = audit_lines[0]
    assert "blocked" in line
    assert "prod_guard" in line
    # Token must NOT appear in the log.
    assert "prod-token" not in line


# ---------------------------------------------------------------------------
# FIX 7: write-gate + error/audit coverage for schema_update_package
# ---------------------------------------------------------------------------


@resp_lib.activate
def test_live_update_no_token_returns_error() -> None:
    """schema_update_package live write with no token returns token error, no POST."""
    tools, _, _ = _setup(token=None)

    # package_show is NOT called because the empty-updates guard fires first?
    # Use non-empty updates so we reach the write gate.
    resp_lib.add(
        resp_lib.GET,
        f"{BASE_URL}/api/3/action/package_show",
        json={"success": True, "result": {"id": "abc-123", "type": "mint_dataset"}},
    )

    result = tools["schema_update_package"](
        id="abc-123",
        metadata_updates={"title": "New Title"},
        dry_run=False,
    )
    assert result["error"] == "writes_require_token"
    post_calls = [c for c in resp_lib.calls if c.request.method == "POST"]
    assert post_calls == []


@resp_lib.activate
def test_live_update_prod_url_no_flag_returns_error() -> None:
    """schema_update_package live write on prod URL without flag → prod-guard error, no POST."""
    prod_url = "https://ckan.tacc.cloud"
    settings = _make_settings(token="mytoken", prod_writes=False, ckan_url=prod_url)
    loader = _make_loader_with_schema()
    # Client must use prod_url so package_show GET goes to the right host.
    client = CKANClient(prod_url, api_token="mytoken")
    mcp = _FakeMCP()
    write_module.register(mcp, client, loader, settings)
    tools = mcp.tools

    resp_lib.add(
        resp_lib.GET,
        f"{prod_url}/api/3/action/package_show",
        json={"success": True, "result": {"id": "abc-123", "type": "mint_dataset"}},
    )

    result = tools["schema_update_package"](
        id="abc-123",
        metadata_updates={"title": "New Title"},
        dry_run=False,
    )
    assert result["error"] == "prod_writes_not_allowed"
    post_calls = [c for c in resp_lib.calls if c.request.method == "POST"]
    assert post_calls == []


@resp_lib.activate
def test_live_update_ckan_error_returns_ckan_error_and_audit(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """schema_update_package live write where CKAN returns 409 → ckan_error + audit line."""
    tools, _, _ = _setup()

    resp_lib.add(
        resp_lib.GET,
        f"{BASE_URL}/api/3/action/package_show",
        json={"success": True, "result": {"id": "abc-123", "type": "mint_dataset"}},
    )
    resp_lib.add(
        resp_lib.POST,
        f"{BASE_URL}/api/3/action/package_patch",
        json={"success": False, "error": {"message": "Conflict"}},
        status=409,
    )

    with caplog.at_level(logging.INFO, logger="dso_ckan_mcp.audit"):
        result = tools["schema_update_package"](
            id="abc-123",
            metadata_updates={"title": "Updated Title"},
            dry_run=False,
        )

    assert result["error"] == "ckan_error"
    audit_lines = [r.message for r in caplog.records if "AUDIT" in r.message]
    assert len(audit_lines) >= 1, f"Expected AUDIT line; got: {caplog.records}"
    # Token must not be in the audit line.
    assert "test-token" not in audit_lines[0]


# ---------------------------------------------------------------------------
# FIX 7: write-gate + error/audit coverage for schema_create_resource
# ---------------------------------------------------------------------------


@resp_lib.activate
def test_live_create_resource_no_token_returns_error() -> None:
    """schema_create_resource live write with no token → token error, no POST."""
    tools, _, _ = _setup(token=None)

    result = tools["schema_create_resource"](
        package_id="abc-123",
        resource_metadata={"name": "My CSV", "format": "CSV"},
        upload_file=None,
        dry_run=False,
    )
    assert result["error"] == "writes_require_token"
    assert len(resp_lib.calls) == 0


@resp_lib.activate
def test_live_create_resource_prod_url_no_flag_returns_error() -> None:
    """schema_create_resource live write on prod URL without flag → prod-guard error, no POST."""
    prod_url = "https://ckan.tacc.cloud"
    settings = _make_settings(token="mytoken", prod_writes=False, ckan_url=prod_url)
    loader = _make_loader_with_schema()
    client = _make_client(token="mytoken")
    mcp = _FakeMCP()
    write_module.register(mcp, client, loader, settings)
    tools = mcp.tools

    result = tools["schema_create_resource"](
        package_id="abc-123",
        resource_metadata={"name": "My CSV"},
        upload_file=None,
        dry_run=False,
    )
    assert result["error"] == "prod_writes_not_allowed"
    assert len(resp_lib.calls) == 0


@resp_lib.activate
def test_live_create_resource_ckan_error_returns_ckan_error_and_audit(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """schema_create_resource live write where CKAN returns error → ckan_error + audit."""
    tools, _, _ = _setup()

    resp_lib.add(
        resp_lib.POST,
        f"{BASE_URL}/api/3/action/resource_create",
        json={"success": False, "error": {"message": "Package not found"}},
        status=404,
    )

    with caplog.at_level(logging.INFO, logger="dso_ckan_mcp.audit"):
        result = tools["schema_create_resource"](
            package_id="nonexistent-pkg",
            resource_metadata={"name": "My CSV", "format": "CSV"},
            upload_file=None,
            dry_run=False,
        )

    assert result["error"] == "ckan_error"
    audit_lines = [r.message for r in caplog.records if "AUDIT" in r.message]
    assert len(audit_lines) >= 1, f"Expected AUDIT line; got: {caplog.records}"
    assert "test-token" not in audit_lines[0]


# ---------------------------------------------------------------------------
# FIX 3: is_production tests for new dev hosts
# ---------------------------------------------------------------------------


def test_is_production_ipv6_loopback() -> None:
    """::1 (IPv6 loopback) must not be classified as production."""
    from dso_ckan_mcp.config import is_production

    assert is_production("http://[::1]:5001") is False


def test_is_production_any_iface() -> None:
    """0.0.0.0 (any-interface bind) must not be classified as production."""
    from dso_ckan_mcp.config import is_production

    assert is_production("http://0.0.0.0:5001") is False


# ---------------------------------------------------------------------------
# Tapis token per-call: X-Tapis-Token authentication model
# ---------------------------------------------------------------------------


@resp_lib.activate
def test_live_create_tapis_token_arg_no_env_token_succeeds() -> None:
    """Live write with tapis_token arg but NO env token → POST carries X-Tapis-Token; gate passes."""
    # Settings with NO env token.
    settings = _make_settings(token=None)
    loader = _make_loader_with_schema()
    client = CKANClient(BASE_URL, api_token=None)
    mcp = _FakeMCP()
    write_module.register(mcp, client, loader, settings)
    tools = mcp.tools

    resp_lib.add(
        resp_lib.POST,
        f"{BASE_URL}/api/3/action/package_create",
        json={"success": True, "result": {"id": "tapis-test-id"}},
    )

    result = tools["schema_create_package"](
        dataset_type="mint_dataset",
        metadata=VALID_METADATA,
        tapis_token="my-tapis-jwt",
        dry_run=False,
    )

    assert result["dry_run"] is False
    assert result["success"] is True
    assert result["id"] == "tapis-test-id"

    # Verify the POST carried X-Tapis-Token (not Authorization).
    assert len(resp_lib.calls) == 1
    sent_headers = resp_lib.calls[0].request.headers
    assert sent_headers.get("X-Tapis-Token") == "my-tapis-jwt", (
        f"Expected X-Tapis-Token header; got: {dict(sent_headers)}"
    )
    assert "Authorization" not in sent_headers


@resp_lib.activate
def test_live_create_no_tapis_token_no_env_token_returns_error() -> None:
    """Live write with NEITHER tapis_token arg NOR env token → error dict, no POST."""
    settings = _make_settings(token=None)
    loader = _make_loader_with_schema()
    client = CKANClient(BASE_URL, api_token=None)
    mcp = _FakeMCP()
    write_module.register(mcp, client, loader, settings)
    tools = mcp.tools

    result = tools["schema_create_package"](
        dataset_type="mint_dataset",
        metadata=VALID_METADATA,
        tapis_token=None,
        dry_run=False,
    )

    assert result["error"] == "writes_require_token"
    assert len(resp_lib.calls) == 0


@resp_lib.activate
def test_live_create_no_tapis_token_no_env_token_emits_audit_blocked(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """When blocked by missing token, audit.log_blocked is emitted."""
    settings = _make_settings(token=None)
    loader = _make_loader_with_schema()
    client = CKANClient(BASE_URL, api_token=None)
    mcp = _FakeMCP()
    write_module.register(mcp, client, loader, settings)
    tools = mcp.tools

    with caplog.at_level(logging.WARNING, logger="dso_ckan_mcp.audit"):
        result = tools["schema_create_package"](
            dataset_type="mint_dataset",
            metadata=VALID_METADATA,
            tapis_token=None,
            dry_run=False,
        )

    assert result["error"] == "writes_require_token"
    audit_lines = [r.message for r in caplog.records if "AUDIT" in r.message]
    assert len(audit_lines) >= 1, f"Expected AUDIT blocked line; got: {caplog.records}"
    assert "no_token" in audit_lines[0]
    assert "blocked" in audit_lines[0]


@resp_lib.activate
def test_tapis_token_not_in_audit_log_for_successful_write(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """tapis_token value must NEVER appear in audit log lines for a successful live write."""
    settings = _make_settings(token=None)
    loader = _make_loader_with_schema()
    client = CKANClient(BASE_URL, api_token=None)
    mcp = _FakeMCP()
    write_module.register(mcp, client, loader, settings)
    tools = mcp.tools

    resp_lib.add(
        resp_lib.POST,
        f"{BASE_URL}/api/3/action/package_create",
        json={"success": True, "result": {"id": "secret-test-id"}},
    )

    with caplog.at_level(logging.INFO, logger="dso_ckan_mcp.audit"):
        result = tools["schema_create_package"](
            dataset_type="mint_dataset",
            metadata=VALID_METADATA,
            tapis_token="super-secret-tapis-jwt",
            dry_run=False,
        )

    assert result["success"] is True

    # Token must NOT appear in any log record.
    for record in caplog.records:
        assert "super-secret-tapis-jwt" not in record.message, (
            f"Token leaked into log: {record.message}"
        )

    # Token must NOT appear in the returned dict.
    import json as _json
    result_str = _json.dumps(result, default=str)
    assert "super-secret-tapis-jwt" not in result_str, (
        f"Token leaked into returned dict: {result_str}"
    )


@resp_lib.activate
def test_tapis_token_not_in_returned_dict() -> None:
    """tapis_token value must NOT appear in any key of the returned dict."""
    settings = _make_settings(token=None)
    loader = _make_loader_with_schema()
    client = CKANClient(BASE_URL, api_token=None)
    mcp = _FakeMCP()
    write_module.register(mcp, client, loader, settings)
    tools = mcp.tools

    resp_lib.add(
        resp_lib.POST,
        f"{BASE_URL}/api/3/action/package_create",
        json={"success": True, "result": {"id": "ret-dict-test"}},
    )

    result = tools["schema_create_package"](
        dataset_type="mint_dataset",
        metadata=VALID_METADATA,
        tapis_token="dont-leak-me",
        dry_run=False,
    )

    import json as _json
    result_str = _json.dumps(result, default=str)
    assert "dont-leak-me" not in result_str
    assert "tapis_token" not in result


@resp_lib.activate
def test_get_carries_no_x_tapis_token() -> None:
    """Read (GET) calls must carry no X-Tapis-Token or Authorization header."""
    tools, client, _ = _setup(token="env-token")

    resp_lib.add(
        resp_lib.GET,
        f"{BASE_URL}/api/3/action/package_show",
        json={"success": True, "result": {"id": "abc", "type": "mint_dataset", "name": "x"}},
    )

    # Trigger a GET via the update dry-run path.
    result = tools["schema_update_package"](
        id="abc",
        metadata_updates={"title": "T"},
        dry_run=True,
    )
    assert result["dry_run"] is True

    get_calls = [c for c in resp_lib.calls if c.request.method == "GET"]
    assert len(get_calls) == 1
    h = get_calls[0].request.headers
    assert "X-Tapis-Token" not in h, f"X-Tapis-Token must not appear on GET; got: {dict(h)}"
    assert "Authorization" not in h, f"Authorization must not appear on GET; got: {dict(h)}"


@resp_lib.activate
def test_env_fallback_token_used_when_no_per_call_token() -> None:
    """When tapis_token arg is omitted, the env-configured CKAN_API_TOKEN is used."""
    # Settings with an env token but no per-call tapis_token supplied.
    settings = _make_settings(token="env-fallback-jwt")
    loader = _make_loader_with_schema()
    client = CKANClient(BASE_URL, api_token="env-fallback-jwt")
    mcp = _FakeMCP()
    write_module.register(mcp, client, loader, settings)
    tools = mcp.tools

    resp_lib.add(
        resp_lib.POST,
        f"{BASE_URL}/api/3/action/package_create",
        json={"success": True, "result": {"id": "fallback-id"}},
    )

    result = tools["schema_create_package"](
        dataset_type="mint_dataset",
        metadata=VALID_METADATA,
        # tapis_token intentionally omitted — env fallback should be used.
        dry_run=False,
    )

    assert result["success"] is True
    sent_headers = resp_lib.calls[-1].request.headers
    assert sent_headers.get("X-Tapis-Token") == "env-fallback-jwt"


# ---------------------------------------------------------------------------
# audit._scrub_args: tapis_token / x-tapis-token keys are redacted
# ---------------------------------------------------------------------------


def test_scrub_args_removes_tapis_token_key() -> None:
    """_scrub_args must redact 'tapis_token' key."""
    from dso_ckan_mcp.audit import _scrub_args

    raw = {"dataset_type": "mint_dataset", "tapis_token": "secret-jwt"}
    scrubbed = _scrub_args(raw)
    assert scrubbed["tapis_token"] == "[REDACTED]"
    assert scrubbed["dataset_type"] == "mint_dataset"


def test_scrub_args_removes_x_tapis_token_key() -> None:
    """_scrub_args must redact 'x-tapis-token' key."""
    from dso_ckan_mcp.audit import _scrub_args

    raw = {"x-tapis-token": "secret-jwt", "name": "test"}
    scrubbed = _scrub_args(raw)
    assert scrubbed["x-tapis-token"] == "[REDACTED]"
    assert scrubbed["name"] == "test"


def test_scrub_args_removes_x_tapis_token_underscore_key() -> None:
    """_scrub_args must redact 'x_tapis_token' key."""
    from dso_ckan_mcp.audit import _scrub_args

    raw = {"x_tapis_token": "secret-jwt", "name": "test"}
    scrubbed = _scrub_args(raw)
    assert scrubbed["x_tapis_token"] == "[REDACTED]"
