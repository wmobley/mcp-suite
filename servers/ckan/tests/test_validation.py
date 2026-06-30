"""
Tests for validators.py — client-side completeness check.

These are pure unit tests (no HTTP calls).
"""

from __future__ import annotations

import pytest

from dso_ckan_mcp.validators import _is_required, check_completeness

# ---------------------------------------------------------------------------
# Minimal schema fixture mirroring mint_dataset structure
# ---------------------------------------------------------------------------

MINT_SCHEMA = {
    "dataset_type": "mint_dataset",
    "dataset_fields": [
        {"field_name": "title", "label": "Title", "required": True, "preset": "title"},
        {"field_name": "name", "label": "URL", "required": False, "preset": "dataset_slug"},
        {"field_name": "notes", "label": "Description", "required": False},
        {"field_name": "owner_org", "label": "Organization", "required": False},
        {
            "field_name": "temporal_coverage_start",
            "label": "Start date",
            # Portal uses "scheming_required" in validators (not required: true in YAML)
            "validators": "scheming_required isodate convert_to_json_if_date",
            "preset": "date",
        },
        {
            "field_name": "temporal_coverage_end",
            "label": "End date",
            "validators": "scheming_required isodate convert_to_json_if_date",
            "preset": "date",
        },
    ],
    "resource_fields": [],
}


# ---------------------------------------------------------------------------
# _is_required() helper
# ---------------------------------------------------------------------------


def test_is_required_explicit_bool():
    from dso_ckan_mcp.validators import _is_required

    assert _is_required({"field_name": "x", "required": True}) is True


def test_is_required_scheming_required_validator():
    """Fields with 'scheming_required' in validators string are required."""
    from dso_ckan_mcp.validators import _is_required

    assert _is_required({"field_name": "x", "validators": "scheming_required isodate"}) is True


def test_is_required_not_empty_validator():
    """Core CKAN fields use 'not_empty' (e.g. name) — treated as required."""
    from dso_ckan_mcp.validators import _is_required

    name_field = {
        "field_name": "name",
        "validators": "not_empty unicode_safe name_validator package_name_validator",
    }
    assert _is_required(name_field) is True


def test_is_required_not_required():
    from dso_ckan_mcp.validators import _is_required

    assert _is_required({"field_name": "x"}) is False
    assert _is_required({"field_name": "x", "validators": "ignore_missing unicode_safe"}) is False


def test_empty_name_with_not_empty_validator_fails():
    """A 'name' field with not_empty validator must be flagged when empty."""
    schema = {
        "dataset_fields": [
            {"field_name": "title", "required": True, "preset": "title"},
            {
                "field_name": "name",
                "validators": "not_empty unicode_safe name_validator",
            },
        ]
    }
    result = check_completeness(schema, {"title": "T"})  # name missing
    assert result["valid"] is False
    assert any("name" in e for e in result["errors"])


# ---------------------------------------------------------------------------
# Required field checks
# ---------------------------------------------------------------------------


def test_valid_metadata_passes():
    metadata = {
        "title": "Rainfall 2024",
        "temporal_coverage_start": "2024-01-01",
        "temporal_coverage_end": "2024-12-31",
    }
    result = check_completeness(MINT_SCHEMA, metadata)
    assert result["valid"] is True
    assert result["errors"] == []


def test_missing_required_field_fails():
    metadata = {
        "title": "Rainfall 2024",
        # temporal_coverage_start missing
        "temporal_coverage_end": "2024-12-31",
    }
    result = check_completeness(MINT_SCHEMA, metadata)
    assert result["valid"] is False
    assert any("temporal_coverage_start" in e for e in result["errors"])


def test_empty_required_field_fails():
    metadata = {
        "title": "",  # empty string
        "temporal_coverage_start": "2024-01-01",
        "temporal_coverage_end": "2024-12-31",
    }
    result = check_completeness(MINT_SCHEMA, metadata)
    assert result["valid"] is False
    assert any("title" in e for e in result["errors"])


def test_all_required_missing():
    result = check_completeness(MINT_SCHEMA, {})
    assert result["valid"] is False
    # Should have an error for each required field.
    assert len(result["errors"]) >= 3  # title, start, end


# ---------------------------------------------------------------------------
# Date format sanity
# ---------------------------------------------------------------------------


def test_bad_date_format_errors():
    metadata = {
        "title": "Test",
        "temporal_coverage_start": "01/01/2024",  # wrong format
        "temporal_coverage_end": "2024-12-31",
    }
    result = check_completeness(MINT_SCHEMA, metadata)
    assert result["valid"] is False
    assert any("temporal_coverage_start" in e for e in result["errors"])


def test_good_date_format_passes():
    metadata = {
        "title": "Test",
        "temporal_coverage_start": "2020-06-15",
        "temporal_coverage_end": "2023-12-31",
    }
    result = check_completeness(MINT_SCHEMA, metadata)
    assert result["valid"] is True


# ---------------------------------------------------------------------------
# Unknown key warnings
# ---------------------------------------------------------------------------


def test_unknown_keys_produce_warnings():
    metadata = {
        "title": "Test",
        "temporal_coverage_start": "2024-01-01",
        "temporal_coverage_end": "2024-12-31",
        "totally_unknown_field": "value",
    }
    result = check_completeness(MINT_SCHEMA, metadata)
    assert result["valid"] is True  # warnings don't fail validation
    assert any("totally_unknown_field" in w for w in result["warnings"])


def test_no_unknown_keys_no_warnings():
    metadata = {
        "title": "Test",
        "notes": "Some notes",
        "temporal_coverage_start": "2024-01-01",
        "temporal_coverage_end": "2024-12-31",
    }
    result = check_completeness(MINT_SCHEMA, metadata)
    assert result["valid"] is True
    assert result["warnings"] == []


# ---------------------------------------------------------------------------
# note / disclaimer
# ---------------------------------------------------------------------------


def test_result_always_contains_note():
    result = check_completeness(MINT_SCHEMA, {"title": "X", "temporal_coverage_start": "2024-01-01", "temporal_coverage_end": "2024-12-31"})
    assert "note" in result
    assert len(result["note"]) > 10  # non-trivial disclaimer


def test_note_mentions_client_side():
    result = check_completeness(MINT_SCHEMA, {})
    assert "client-side" in result["note"].lower() or "client" in result["note"].lower()


# ---------------------------------------------------------------------------
# Empty schema edge case
# ---------------------------------------------------------------------------


def test_empty_schema_no_errors():
    """An empty schema produces no errors (nothing required)."""
    result = check_completeness({"dataset_fields": []}, {"foo": "bar"})
    # Unknown keys become warnings, not errors.
    assert result["valid"] is True
    assert result["errors"] == []
