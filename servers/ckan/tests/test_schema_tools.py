"""
Integration tests for schema tools — hit the live dev portal at localhost:5001.

All tests are marked ``integration`` and auto-skipped when the portal is
not reachable.
"""

from __future__ import annotations

import pytest

EXPECTED_TYPES = {"dataset", "mint_dataset", "subside_dataset"}


@pytest.mark.integration
def test_schema_list_returns_three_types(live_loader):
    types = live_loader.list_dataset_types()
    assert set(types) == EXPECTED_TYPES


@pytest.mark.integration
def test_mint_dataset_schema_field_counts(live_loader):
    schema = live_loader.get_schema("mint_dataset")
    assert len(schema["dataset_fields"]) == 14, (
        f"Expected 14 dataset_fields, got {len(schema['dataset_fields'])}"
    )
    assert len(schema["resource_fields"]) == 5, (
        f"Expected 5 resource_fields, got {len(schema['resource_fields'])}"
    )


@pytest.mark.integration
def test_mint_dataset_temporal_fields_exist(live_loader):
    """temporal_coverage_start/end exist in dataset_fields."""
    schema = live_loader.get_schema("mint_dataset")
    field_names = {f["field_name"] for f in schema["dataset_fields"]}
    assert "temporal_coverage_start" in field_names
    assert "temporal_coverage_end" in field_names


@pytest.mark.integration
def test_mint_dataset_temporal_fields_have_scheming_required_validator(live_loader):
    """The live portal marks temporal fields via 'scheming_required' in validators string."""
    schema = live_loader.get_schema("mint_dataset")
    temporal_fields = {
        f["field_name"]: f
        for f in schema["dataset_fields"]
        if f["field_name"] in ("temporal_coverage_start", "temporal_coverage_end")
    }
    for fname, field in temporal_fields.items():
        validators_str = field.get("validators", "")
        assert "scheming_required" in validators_str, (
            f"Expected 'scheming_required' in validators for {fname!r}, "
            f"got: {validators_str!r}"
        )


@pytest.mark.integration
def test_describe_schema_invalid_type_raises(live_loader):
    with pytest.raises(ValueError, match="Unknown dataset_type"):
        live_loader.get_schema("nonexistent_type_xyz")


@pytest.mark.integration
def test_subside_dataset_schema_loads(live_loader):
    schema = live_loader.get_schema("subside_dataset")
    assert "dataset_fields" in schema
    assert "resource_fields" in schema
    assert len(schema["dataset_fields"]) > 0


@pytest.mark.integration
def test_dataset_schema_loads(live_loader):
    schema = live_loader.get_schema("dataset")
    assert "dataset_fields" in schema
    assert "resource_fields" in schema
