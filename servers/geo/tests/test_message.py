"""
Tests for message.py — actor message builder.

Verifies:
- Correct message shape for each operation.
- ckan block present for transforms, absent for gdalinfo.
- token fields are redacted by scrub_message_for_audit.
- read_token included when provided, absent when not.
"""

from __future__ import annotations

import pytest

from dso_geo_mcp.message import (
    build_clip_message,
    build_cog_message,
    build_gdalinfo_message,
    build_overviews_message,
    build_reproject_message,
    scrub_message_for_audit,
)

INPUT_URL = "https://localhost/dataset/pkg/resource/abc/download/test.tif"
TOKEN = "eyJfaketoken.fake.sig"
CKAN_URL = "http://localhost:5001"
PACKAGE_ID = "my-dataset"
OUTPUT_NAME = "result.tif"


# ---------------------------------------------------------------------------
# gdalinfo
# ---------------------------------------------------------------------------

class TestBuildGdalinfoMessage:
    def test_operation_field(self):
        msg = build_gdalinfo_message(INPUT_URL)
        assert msg["operation"] == "gdalinfo"

    def test_input_url_field(self):
        msg = build_gdalinfo_message(INPUT_URL)
        assert msg["input_url"] == INPUT_URL

    def test_include_stats_default_true(self):
        msg = build_gdalinfo_message(INPUT_URL)
        assert msg["include_stats"] is True

    def test_include_stats_false(self):
        msg = build_gdalinfo_message(INPUT_URL, include_stats=False)
        assert msg["include_stats"] is False

    def test_no_ckan_block(self):
        msg = build_gdalinfo_message(INPUT_URL)
        assert "ckan" not in msg

    def test_no_output_name(self):
        msg = build_gdalinfo_message(INPUT_URL)
        assert "output_name" not in msg

    def test_read_token_included_when_provided(self):
        msg = build_gdalinfo_message(INPUT_URL, read_token=TOKEN)
        assert msg["read_token"] == TOKEN

    def test_read_token_absent_when_none(self):
        msg = build_gdalinfo_message(INPUT_URL, read_token=None)
        assert "read_token" not in msg


# ---------------------------------------------------------------------------
# reproject
# ---------------------------------------------------------------------------

class TestBuildReprojectMessage:
    def test_operation_field(self):
        msg = build_reproject_message(INPUT_URL, OUTPUT_NAME, 4326, CKAN_URL, TOKEN, PACKAGE_ID)
        assert msg["operation"] == "reproject"

    def test_target_crs_in_params(self):
        msg = build_reproject_message(INPUT_URL, OUTPUT_NAME, 32614, CKAN_URL, TOKEN, PACKAGE_ID)
        assert msg["params"]["target_crs"] == 32614

    def test_output_name_field(self):
        msg = build_reproject_message(INPUT_URL, OUTPUT_NAME, 4326, CKAN_URL, TOKEN, PACKAGE_ID)
        assert msg["output_name"] == OUTPUT_NAME

    def test_ckan_block_present(self):
        msg = build_reproject_message(INPUT_URL, OUTPUT_NAME, 4326, CKAN_URL, TOKEN, PACKAGE_ID)
        assert "ckan" in msg
        assert msg["ckan"]["url"] == CKAN_URL
        assert msg["ckan"]["token"] == TOKEN
        assert msg["ckan"]["package_id"] == PACKAGE_ID

    def test_read_token_when_provided(self):
        msg = build_reproject_message(INPUT_URL, OUTPUT_NAME, 4326, CKAN_URL, TOKEN, PACKAGE_ID, read_token=TOKEN)
        assert msg["read_token"] == TOKEN

    def test_read_token_absent_when_none(self):
        msg = build_reproject_message(INPUT_URL, OUTPUT_NAME, 4326, CKAN_URL, TOKEN, PACKAGE_ID, read_token=None)
        assert "read_token" not in msg


# ---------------------------------------------------------------------------
# cog
# ---------------------------------------------------------------------------

class TestBuildCogMessage:
    def test_operation_field(self):
        msg = build_cog_message(INPUT_URL, OUTPUT_NAME, "deflate", CKAN_URL, TOKEN, PACKAGE_ID)
        assert msg["operation"] == "cog"

    def test_compression_in_params(self):
        msg = build_cog_message(INPUT_URL, OUTPUT_NAME, "lzw", CKAN_URL, TOKEN, PACKAGE_ID)
        assert msg["params"]["compression"] == "lzw"

    def test_ckan_block_present(self):
        msg = build_cog_message(INPUT_URL, OUTPUT_NAME, "deflate", CKAN_URL, TOKEN, PACKAGE_ID)
        assert "ckan" in msg


# ---------------------------------------------------------------------------
# clip
# ---------------------------------------------------------------------------

_POLYGON = {
    "type": "Polygon",
    "coordinates": [
        [[-97.75, 30.25], [-97.70, 30.25], [-97.70, 30.30], [-97.75, 30.30], [-97.75, 30.25]]
    ],
}


class TestBuildClipMessage:
    def test_operation_field(self):
        msg = build_clip_message(INPUT_URL, OUTPUT_NAME, _POLYGON, CKAN_URL, TOKEN, PACKAGE_ID)
        assert msg["operation"] == "clip"

    def test_clip_geometry_in_params(self):
        msg = build_clip_message(INPUT_URL, OUTPUT_NAME, _POLYGON, CKAN_URL, TOKEN, PACKAGE_ID)
        assert msg["params"]["clip_geometry"] == _POLYGON

    def test_ckan_block_present(self):
        msg = build_clip_message(INPUT_URL, OUTPUT_NAME, _POLYGON, CKAN_URL, TOKEN, PACKAGE_ID)
        assert "ckan" in msg


# ---------------------------------------------------------------------------
# overviews
# ---------------------------------------------------------------------------

class TestBuildOverviewsMessage:
    def test_operation_field(self):
        msg = build_overviews_message(INPUT_URL, OUTPUT_NAME, [2, 4, 8], CKAN_URL, TOKEN, PACKAGE_ID)
        assert msg["operation"] == "overviews"

    def test_overview_levels_in_params(self):
        msg = build_overviews_message(INPUT_URL, OUTPUT_NAME, [2, 4, 8, 16], CKAN_URL, TOKEN, PACKAGE_ID)
        assert msg["params"]["overview_levels"] == [2, 4, 8, 16]

    def test_ckan_block_present(self):
        msg = build_overviews_message(INPUT_URL, OUTPUT_NAME, [2, 4, 8], CKAN_URL, TOKEN, PACKAGE_ID)
        assert "ckan" in msg


# ---------------------------------------------------------------------------
# scrub_message_for_audit
# ---------------------------------------------------------------------------

class TestScrubMessageForAudit:
    def test_read_token_redacted(self):
        msg = build_gdalinfo_message(INPUT_URL, read_token=TOKEN)
        safe = scrub_message_for_audit(msg)
        assert safe["read_token"] == "[REDACTED]"
        # Original not mutated
        assert msg["read_token"] == TOKEN

    def test_ckan_token_redacted(self):
        msg = build_reproject_message(INPUT_URL, OUTPUT_NAME, 4326, CKAN_URL, TOKEN, PACKAGE_ID)
        safe = scrub_message_for_audit(msg)
        assert safe["ckan"]["token"] == "[REDACTED]"
        # Original not mutated
        assert msg["ckan"]["token"] == TOKEN

    def test_no_read_token_unaffected(self):
        msg = build_gdalinfo_message(INPUT_URL, read_token=None)
        safe = scrub_message_for_audit(msg)
        assert "read_token" not in safe

    def test_other_fields_preserved(self):
        msg = build_gdalinfo_message(INPUT_URL)
        safe = scrub_message_for_audit(msg)
        assert safe["operation"] == "gdalinfo"
        assert safe["input_url"] == INPUT_URL
