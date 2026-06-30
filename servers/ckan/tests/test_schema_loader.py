"""
Tests for schema_loader.py — cache TTL and allowlist rejection.

Uses mock CKANClient to avoid network calls.
"""

from __future__ import annotations

import time
from unittest.mock import MagicMock, call

import pytest

from dso_ckan_mcp.schema_loader import SchemaLoader


def _make_loader(types=None, schema=None, ttl=3600):
    """Return a SchemaLoader backed by a mock client."""
    client = MagicMock()
    if types is None:
        types = ["dataset", "mint_dataset", "subside_dataset"]
    if schema is None:
        schema = {
            "dataset_type": "mint_dataset",
            "dataset_fields": [
                {"field_name": "title", "label": "Title", "required": True},
                {"field_name": "notes", "label": "Notes", "required": False},
            ],
            "resource_fields": [],
        }
    client.get.side_effect = lambda action, params=None: (
        types if action == "scheming_dataset_schema_list" else schema
    )
    return SchemaLoader(client=client, ttl=ttl), client


# ---------------------------------------------------------------------------
# list_dataset_types
# ---------------------------------------------------------------------------


def test_list_dataset_types_returns_list():
    loader, _ = _make_loader()
    result = loader.list_dataset_types()
    assert result == ["dataset", "mint_dataset", "subside_dataset"]


def test_list_dataset_types_cached():
    """Second call within TTL should NOT make a second API call."""
    loader, client = _make_loader(ttl=3600)
    loader.list_dataset_types()
    loader.list_dataset_types()
    # The mock records calls; scheming_dataset_schema_list should be called once.
    calls = [c for c in client.get.call_args_list if c[0][0] == "scheming_dataset_schema_list"]
    assert len(calls) == 1


def test_list_dataset_types_refreshes_after_ttl(monkeypatch):
    """After TTL expires, the next call fetches fresh data."""
    loader, client = _make_loader(ttl=1)
    loader.list_dataset_types()

    # Advance time past TTL by monkey-patching time.monotonic.
    original = time.monotonic()
    monkeypatch.setattr(time, "monotonic", lambda: original + 2)

    loader.list_dataset_types()
    calls = [c for c in client.get.call_args_list if c[0][0] == "scheming_dataset_schema_list"]
    assert len(calls) == 2


# ---------------------------------------------------------------------------
# validate_type
# ---------------------------------------------------------------------------


def test_validate_type_accepts_known():
    loader, _ = _make_loader()
    # Should not raise.
    loader.validate_type("mint_dataset")


def test_validate_type_rejects_unknown():
    loader, _ = _make_loader()
    with pytest.raises(ValueError, match="Unknown dataset_type"):
        loader.validate_type("evil_type")


def test_validate_type_rejects_injection_attempt():
    """Crafted type strings must be rejected before any API call."""
    loader, client = _make_loader()
    with pytest.raises(ValueError):
        loader.validate_type("../../etc/passwd")
    # Must not have called the schema show endpoint.
    calls_show = [c for c in client.get.call_args_list if c[0][0] == "scheming_dataset_schema_show"]
    assert calls_show == []


# ---------------------------------------------------------------------------
# get_schema
# ---------------------------------------------------------------------------


def test_get_schema_calls_show():
    loader, client = _make_loader()
    schema = loader.get_schema("mint_dataset")
    assert "dataset_fields" in schema


def test_get_schema_cached():
    loader, client = _make_loader(ttl=3600)
    loader.get_schema("mint_dataset")
    loader.get_schema("mint_dataset")
    show_calls = [c for c in client.get.call_args_list if c[0][0] == "scheming_dataset_schema_show"]
    assert len(show_calls) == 1


def test_get_schema_invalid_type_raises():
    loader, _ = _make_loader()
    with pytest.raises(ValueError):
        loader.get_schema("nonexistent_type")


# ---------------------------------------------------------------------------
# invalidate
# ---------------------------------------------------------------------------


def test_invalidate_clears_cache():
    loader, client = _make_loader(ttl=3600)
    loader.list_dataset_types()
    loader.invalidate()
    loader.list_dataset_types()
    calls = [c for c in client.get.call_args_list if c[0][0] == "scheming_dataset_schema_list"]
    assert len(calls) == 2
