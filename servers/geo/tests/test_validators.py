"""
Unit tests for validators.py — the MCP-layer injection-safe parameter contract.

Mirrors the gdal-actor/tests/test_validators.py test cases, adapted for the
dso_geo_mcp.validators module.

No external services, no Docker.
"""

from __future__ import annotations

import pytest

from dso_geo_mcp.validators import (
    ALLOWED_OPERATIONS,
    validate_clip_geometry,
    validate_compression,
    validate_execution_id,
    validate_operation,
    validate_output_name,
    validate_overview_levels,
    validate_target_crs,
)


# ===========================================================================
# validate_operation
# ===========================================================================

class TestValidateOperation:
    def test_all_allowed(self):
        for op in ["gdalinfo", "reproject", "cog", "clip", "overviews"]:
            assert validate_operation(op) == op

    def test_rejects_shell_injection(self):
        with pytest.raises(ValueError):
            validate_operation("gdalinfo; rm -rf /")

    def test_rejects_arbitrary_command(self):
        with pytest.raises(ValueError):
            validate_operation("rm")

    def test_rejects_empty(self):
        with pytest.raises(ValueError):
            validate_operation("")

    def test_rejects_non_string(self):
        with pytest.raises(ValueError):
            validate_operation(42)

    def test_rejects_gdalwarp_direct(self):
        with pytest.raises(ValueError):
            validate_operation("gdalwarp")

    def test_rejects_gdal_translate_direct(self):
        with pytest.raises(ValueError):
            validate_operation("gdal_translate")


# ===========================================================================
# validate_target_crs
# ===========================================================================

class TestValidateTargetCrs:
    def test_valid_epsg4326(self):
        assert validate_target_crs(4326) == ["-t_srs", "EPSG:4326"]

    def test_valid_epsg32614(self):
        assert validate_target_crs(32614) == ["-t_srs", "EPSG:32614"]

    def test_valid_minimum(self):
        assert validate_target_crs(1) == ["-t_srs", "EPSG:1"]

    def test_valid_maximum(self):
        assert validate_target_crs(999999) == ["-t_srs", "EPSG:999999"]

    def test_rejects_zero(self):
        with pytest.raises(ValueError):
            validate_target_crs(0)

    def test_rejects_negative(self):
        with pytest.raises(ValueError):
            validate_target_crs(-1)

    def test_rejects_too_large(self):
        with pytest.raises(ValueError):
            validate_target_crs(1_000_000)

    def test_rejects_string(self):
        with pytest.raises(ValueError):
            validate_target_crs("4326")

    def test_rejects_float(self):
        with pytest.raises(ValueError):
            validate_target_crs(4326.0)

    def test_rejects_bool(self):
        with pytest.raises(ValueError):
            validate_target_crs(True)

    def test_rejects_injection_string(self):
        with pytest.raises(ValueError):
            validate_target_crs("4326; rm -rf /")

    def test_rejects_none(self):
        with pytest.raises(ValueError):
            validate_target_crs(None)


# ===========================================================================
# validate_output_name
# ===========================================================================

class TestValidateOutputName:
    def test_valid_simple(self):
        assert validate_output_name("output.tif") == "output.tif"

    def test_valid_with_underscores_dashes(self):
        assert validate_output_name("rainfall_epsg4326-v2.tif") == "rainfall_epsg4326-v2.tif"

    def test_valid_dots_in_name(self):
        assert validate_output_name("file.v1.0.tif") == "file.v1.0.tif"

    def test_rejects_dotdot(self):
        with pytest.raises(ValueError):
            validate_output_name("../etc/passwd")

    def test_rejects_dotdot_embedded(self):
        with pytest.raises(ValueError):
            validate_output_name("out/../secret.tif")

    def test_rejects_absolute_path(self):
        with pytest.raises(ValueError):
            validate_output_name("/etc/passwd.tif")

    def test_rejects_forward_slash(self):
        with pytest.raises(ValueError):
            validate_output_name("subdir/output.tif")

    def test_rejects_backslash(self):
        with pytest.raises(ValueError):
            validate_output_name("sub\\output.tif")

    def test_rejects_semicolon(self):
        with pytest.raises(ValueError):
            validate_output_name("output;rm.tif")

    def test_rejects_dollar_sign(self):
        with pytest.raises(ValueError):
            validate_output_name("$(whoami).tif")

    def test_rejects_backtick(self):
        with pytest.raises(ValueError):
            validate_output_name("`id`.tif")

    def test_rejects_space(self):
        with pytest.raises(ValueError):
            validate_output_name("my output.tif")

    def test_rejects_null_byte(self):
        with pytest.raises(ValueError):
            validate_output_name("output\x00.tif")

    def test_rejects_wrong_extension(self):
        with pytest.raises(ValueError):
            validate_output_name("output.nc")

    def test_rejects_no_extension(self):
        with pytest.raises(ValueError):
            validate_output_name("output")

    def test_rejects_empty(self):
        with pytest.raises(ValueError):
            validate_output_name("")

    def test_rejects_non_string(self):
        with pytest.raises(ValueError):
            validate_output_name(42)


# ===========================================================================
# validate_compression
# ===========================================================================

class TestValidateCompression:
    @pytest.mark.parametrize("comp", ["deflate", "lzw", "zstd", "none"])
    def test_valid_enum(self, comp):
        assert validate_compression(comp) == comp

    def test_rejects_gzip(self):
        with pytest.raises(ValueError):
            validate_compression("gzip")

    def test_rejects_empty(self):
        with pytest.raises(ValueError):
            validate_compression("")

    def test_rejects_uppercase(self):
        with pytest.raises(ValueError):
            validate_compression("DEFLATE")

    def test_rejects_injection(self):
        with pytest.raises(ValueError):
            validate_compression("deflate; rm -rf /")

    def test_rejects_non_string(self):
        with pytest.raises(ValueError):
            validate_compression(1)


# ===========================================================================
# validate_overview_levels
# ===========================================================================

class TestValidateOverviewLevels:
    def test_valid_standard(self):
        assert validate_overview_levels([2, 4, 8]) == [2, 4, 8]

    def test_valid_single(self):
        assert validate_overview_levels([2]) == [2]

    def test_valid_max_elements(self):
        levels = [2, 4, 8, 16, 32, 64, 128, 256, 512, 2]
        assert validate_overview_levels(levels) == levels

    def test_valid_boundary_min(self):
        assert validate_overview_levels([2]) == [2]

    def test_valid_boundary_max(self):
        assert validate_overview_levels([512]) == [512]

    def test_rejects_below_min(self):
        with pytest.raises(ValueError):
            validate_overview_levels([1])

    def test_rejects_above_max(self):
        with pytest.raises(ValueError):
            validate_overview_levels([513])

    def test_rejects_zero(self):
        with pytest.raises(ValueError):
            validate_overview_levels([0])

    def test_rejects_too_many(self):
        with pytest.raises(ValueError):
            validate_overview_levels([2, 4, 8, 16, 32, 64, 128, 256, 512, 2, 4])  # 11 items

    def test_rejects_empty(self):
        with pytest.raises(ValueError):
            validate_overview_levels([])

    def test_rejects_non_list(self):
        with pytest.raises(ValueError):
            validate_overview_levels("2 4 8")

    def test_rejects_float_in_list(self):
        with pytest.raises(ValueError):
            validate_overview_levels([2.0, 4, 8])

    def test_rejects_bool_in_list(self):
        with pytest.raises(ValueError):
            validate_overview_levels([True, 4, 8])

    def test_rejects_string_in_list(self):
        with pytest.raises(ValueError):
            validate_overview_levels(["2", 4, 8])


# ===========================================================================
# validate_clip_geometry
# ===========================================================================

_VALID_POLYGON = {
    "type": "Polygon",
    "coordinates": [
        [
            [-97.75, 30.25],
            [-97.70, 30.25],
            [-97.70, 30.30],
            [-97.75, 30.30],
            [-97.75, 30.25],
        ]
    ],
}

_VALID_MULTIPOLYGON = {
    "type": "MultiPolygon",
    "coordinates": [
        [
            [
                [-97.75, 30.25],
                [-97.70, 30.25],
                [-97.70, 30.30],
                [-97.75, 30.30],
                [-97.75, 30.25],
            ]
        ]
    ],
}


class TestValidateClipGeometry:
    def test_valid_polygon(self):
        result = validate_clip_geometry(_VALID_POLYGON)
        assert result["type"] == "Polygon"

    def test_valid_multipolygon(self):
        result = validate_clip_geometry(_VALID_MULTIPOLYGON)
        assert result["type"] == "MultiPolygon"

    def test_rejects_string(self):
        with pytest.raises(ValueError):
            validate_clip_geometry('{"type": "Polygon", "coordinates": []}')

    def test_rejects_point(self):
        with pytest.raises(ValueError):
            validate_clip_geometry({"type": "Point", "coordinates": [-97.0, 30.0]})

    def test_rejects_linestring(self):
        with pytest.raises(ValueError):
            validate_clip_geometry(
                {
                    "type": "LineString",
                    "coordinates": [[-97.0, 30.0], [-96.0, 30.0]],
                }
            )

    def test_rejects_none_type(self):
        with pytest.raises(ValueError):
            validate_clip_geometry({"type": None, "coordinates": []})

    def test_rejects_missing_coordinates(self):
        with pytest.raises(ValueError):
            validate_clip_geometry({"type": "Polygon"})

    def test_rejects_longitude_out_of_bounds(self):
        geom = {
            "type": "Polygon",
            "coordinates": [
                [
                    [200.0, 30.25],
                    [201.0, 30.25],
                    [201.0, 30.30],
                    [200.0, 30.30],
                    [200.0, 30.25],
                ]
            ],
        }
        with pytest.raises(ValueError):
            validate_clip_geometry(geom)

    def test_rejects_latitude_out_of_bounds(self):
        geom = {
            "type": "Polygon",
            "coordinates": [
                [
                    [-97.75, 91.0],
                    [-97.70, 91.0],
                    [-97.70, 90.0],
                    [-97.75, 90.0],
                    [-97.75, 91.0],
                ]
            ],
        }
        with pytest.raises(ValueError):
            validate_clip_geometry(geom)

    def test_rejects_too_many_vertices(self):
        ring = [[-97.75 + i * 0.0001, 30.25] for i in range(1001)]
        ring.append(ring[0])
        geom = {"type": "Polygon", "coordinates": [ring]}
        with pytest.raises(ValueError):
            validate_clip_geometry(geom)

    def test_accepts_exactly_1000_vertices(self):
        ring = [[-97.75 + i * 0.0001, 30.25] for i in range(999)]
        ring.append(ring[0])
        geom = {"type": "Polygon", "coordinates": [ring]}
        result = validate_clip_geometry(geom)
        assert result["type"] == "Polygon"

    def test_rejects_non_dict(self):
        with pytest.raises(ValueError):
            validate_clip_geometry(42)

    def test_rejects_list(self):
        with pytest.raises(ValueError):
            validate_clip_geometry([[[-97.75, 30.25]]])


# ===========================================================================
# validate_execution_id
# ===========================================================================

class TestValidateExecutionId:
    def test_valid_alphanumeric(self):
        assert validate_execution_id("abc123") == "abc123"

    def test_valid_with_dash_underscore(self):
        assert validate_execution_id("exec-abc-123_def") == "exec-abc-123_def"

    def test_rejects_empty(self):
        with pytest.raises(ValueError):
            validate_execution_id("")

    def test_rejects_path_traversal(self):
        with pytest.raises(ValueError):
            validate_execution_id("../../admin")

    def test_rejects_forward_slash(self):
        with pytest.raises(ValueError):
            validate_execution_id("exec/abc")

    def test_rejects_dot(self):
        with pytest.raises(ValueError):
            validate_execution_id("exec.abc")

    def test_rejects_semicolon(self):
        with pytest.raises(ValueError):
            validate_execution_id("exec;rm")

    def test_rejects_non_string(self):
        with pytest.raises(ValueError):
            validate_execution_id(42)
