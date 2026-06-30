"""
MCP-layer parameter validation — mirrors the gdal-actor/validators.py contract.

Every public function either returns the validated value or raises ValueError
with a message that contains NO token or secret data.

This is the FIRST line of defense: bad inputs are rejected before a message
is ever built or submitted to Tapis.  The actor also validates (defense in
depth), but the MCP layer must catch injection attempts early.

Contract (Decision 7 in the design spec):
  - operation       : must be in ALLOWED_OPERATIONS (actor name-space)
  - target_crs      : int in [1, 999999]; returns ["-t_srs", "EPSG:<n>"]
  - output_name     : matches ^[A-Za-z0-9_\\-.]+$, ends with .tif
  - compression     : one of {deflate, lzw, zstd, none}
  - overview_levels : list of int, each 2–512, at most 10 elements
  - clip_geometry   : GeoJSON dict, type Polygon or MultiPolygon,
                      coords within WGS84 bounds, at most 1000 vertices
  - execution_id    : non-empty string, no path traversal chars
"""

from __future__ import annotations

import re
from typing import Any

# ---------------------------------------------------------------------------
# Constants (keep in sync with gdal-actor/validators.py)
# ---------------------------------------------------------------------------

ALLOWED_OPERATIONS: frozenset[str] = frozenset(
    ["gdalinfo", "reproject", "cog", "clip", "overviews"]
)

_COMPRESSION_ENUM: frozenset[str] = frozenset(["deflate", "lzw", "zstd", "none"])

_OUTPUT_NAME_RE = re.compile(r"^[A-Za-z0-9_\-.]+$")

# WGS84 coordinate bounds
_WGS84_LON_MIN, _WGS84_LON_MAX = -180.0, 180.0
_WGS84_LAT_MIN, _WGS84_LAT_MAX = -90.0, 90.0

_MAX_OVERVIEW_LEVELS = 10
_MAX_CLIP_VERTICES = 1000

# Execution ID: Tapis Abaco IDs are alphanumeric + hyphens/underscores
# Reject anything with path separators (prevent URL path injection)
_EXECUTION_ID_RE = re.compile(r"^[A-Za-z0-9_\-]+$")


# ---------------------------------------------------------------------------
# Operation allowlist
# ---------------------------------------------------------------------------


def validate_operation(op: Any) -> str:
    """Return *op* if it is in the allowed-operations set, else raise ValueError.

    >>> validate_operation("gdalinfo")
    'gdalinfo'
    >>> validate_operation("rm")  # doctest: +ELLIPSIS
    Traceback (most recent call last):
        ...
    ValueError: ...
    """
    if not isinstance(op, str):
        raise ValueError(f"operation must be a string, got {type(op).__name__}")
    if op not in ALLOWED_OPERATIONS:
        raise ValueError(
            f"operation {op!r} is not allowed; permitted: {sorted(ALLOWED_OPERATIONS)}"
        )
    return op


# ---------------------------------------------------------------------------
# target_crs — EPSG integer guard
# ---------------------------------------------------------------------------


def validate_target_crs(crs: Any) -> list[str]:
    """Validate *crs* is an integer in [1, 999999] and return the gdalwarp flag list.

    Returns ``["-t_srs", "EPSG:<n>"]``.  Never forwards a raw string to the
    actor message.

    >>> validate_target_crs(4326)
    ['-t_srs', 'EPSG:4326']
    >>> validate_target_crs(0)
    Traceback (most recent call last):
        ...
    ValueError: target_crs must be an integer in [1, 999999]
    """
    if not isinstance(crs, int) or isinstance(crs, bool):
        raise ValueError("target_crs must be an integer in [1, 999999]")
    if not (1 <= crs <= 999999):
        raise ValueError("target_crs must be an integer in [1, 999999]")
    return ["-t_srs", f"EPSG:{crs}"]


# ---------------------------------------------------------------------------
# output_name — path traversal / metachar guard
# ---------------------------------------------------------------------------


def validate_output_name(name: Any) -> str:
    """Validate *name* as a safe bare filename (no path separators or metacharacters).

    Rules:
    - Must be a non-empty string.
    - Must match ``^[A-Za-z0-9_\\-.]+$``.
    - Must end with ``.tif`` (case-sensitive).
    - Must not contain ``/``, ``\\``, or ``..``.

    >>> validate_output_name("output.tif")
    'output.tif'
    >>> validate_output_name("../etc/passwd")
    Traceback (most recent call last):
        ...
    ValueError: ...
    """
    if not isinstance(name, str) or not name:
        raise ValueError("output_name must be a non-empty string")
    if "/" in name or "\\" in name:
        raise ValueError("output_name must not contain path separators")
    if ".." in name:
        raise ValueError("output_name must not contain '..'")
    if not _OUTPUT_NAME_RE.match(name):
        raise ValueError(
            "output_name must match ^[A-Za-z0-9_\\-.]+$ "
            "(letters, digits, underscore, dash, dot only)"
        )
    if not name.endswith(".tif"):
        raise ValueError("output_name must end with '.tif'")
    return name


# ---------------------------------------------------------------------------
# compression — enum guard
# ---------------------------------------------------------------------------


def validate_compression(comp: Any) -> str:
    """Validate *comp* is one of {deflate, lzw, zstd, none}.

    >>> validate_compression("deflate")
    'deflate'
    >>> validate_compression("gzip")
    Traceback (most recent call last):
        ...
    ValueError: ...
    """
    if not isinstance(comp, str):
        raise ValueError(f"compression must be a string, got {type(comp).__name__}")
    if comp not in _COMPRESSION_ENUM:
        raise ValueError(
            f"compression {comp!r} is not allowed; "
            f"permitted: {sorted(_COMPRESSION_ENUM)}"
        )
    return comp


# ---------------------------------------------------------------------------
# overview_levels — list of bounded ints
# ---------------------------------------------------------------------------


def validate_overview_levels(levels: Any) -> list[int]:
    """Validate *levels* is a list of ints each in [2, 512] with at most 10 elements.

    Returns the validated list on success.

    >>> validate_overview_levels([2, 4, 8])
    [2, 4, 8]
    >>> validate_overview_levels([1])
    Traceback (most recent call last):
        ...
    ValueError: ...
    """
    if not isinstance(levels, list):
        raise ValueError("overview_levels must be a list")
    if len(levels) == 0:
        raise ValueError("overview_levels must not be empty")
    if len(levels) > _MAX_OVERVIEW_LEVELS:
        raise ValueError(
            f"overview_levels must have at most {_MAX_OVERVIEW_LEVELS} elements "
            f"(got {len(levels)})"
        )
    for i, lvl in enumerate(levels):
        if not isinstance(lvl, int) or isinstance(lvl, bool):
            raise ValueError(
                f"overview_levels[{i}] must be an integer (got {type(lvl).__name__})"
            )
        if not (2 <= lvl <= 512):
            raise ValueError(
                f"overview_levels[{i}]={lvl} is out of range; each value must be in [2, 512]"
            )
    return list(levels)


# ---------------------------------------------------------------------------
# clip_geometry — GeoJSON Polygon / MultiPolygon guard
# ---------------------------------------------------------------------------


def _count_vertices(geometry: dict[str, Any]) -> int:
    """Count total coordinate pairs in a GeoJSON Polygon or MultiPolygon."""
    geo_type = geometry.get("type", "")
    coords = geometry.get("coordinates", [])
    if geo_type == "Polygon":
        return sum(len(ring) for ring in coords)
    elif geo_type == "MultiPolygon":
        return sum(len(ring) for polygon in coords for ring in polygon)
    return 0


def _check_wgs84_bounds(geometry: dict[str, Any]) -> None:
    """Raise ValueError if any coordinate is outside WGS84 bounds."""
    geo_type = geometry.get("type", "")
    coords = geometry.get("coordinates", [])

    def check_ring(ring: list) -> None:
        for pt in ring:
            if not (isinstance(pt, (list, tuple)) and len(pt) >= 2):
                raise ValueError("clip_geometry coordinate must be [lon, lat] pair")
            lon, lat = pt[0], pt[1]
            if not (_WGS84_LON_MIN <= lon <= _WGS84_LON_MAX):
                raise ValueError(
                    f"clip_geometry longitude {lon} is outside WGS84 bounds "
                    f"[{_WGS84_LON_MIN}, {_WGS84_LON_MAX}]"
                )
            if not (_WGS84_LAT_MIN <= lat <= _WGS84_LAT_MAX):
                raise ValueError(
                    f"clip_geometry latitude {lat} is outside WGS84 bounds "
                    f"[{_WGS84_LAT_MIN}, {_WGS84_LAT_MAX}]"
                )

    if geo_type == "Polygon":
        for ring in coords:
            check_ring(ring)
    elif geo_type == "MultiPolygon":
        for polygon in coords:
            for ring in polygon:
                check_ring(ring)


def validate_clip_geometry(geometry: Any) -> dict[str, Any]:
    """Validate *geometry* as a safe GeoJSON Polygon or MultiPolygon dict.

    Rules:
    - Must be a Python dict (not a JSON string).
    - ``type`` must be ``"Polygon"`` or ``"MultiPolygon"``.
    - All coordinates must be within WGS84 bounds (lon ±180, lat ±90).
    - Total vertex count must not exceed 1000.

    Returns the validated dict on success.
    """
    if not isinstance(geometry, dict):
        raise ValueError(
            "clip_geometry must be a GeoJSON dict (not a string or other type)"
        )
    geo_type = geometry.get("type")
    if geo_type not in ("Polygon", "MultiPolygon"):
        raise ValueError(
            f"clip_geometry type must be 'Polygon' or 'MultiPolygon' "
            f"(got {geo_type!r})"
        )
    if "coordinates" not in geometry:
        raise ValueError("clip_geometry must have a 'coordinates' field")
    if not isinstance(geometry["coordinates"], list):
        raise ValueError("clip_geometry.coordinates must be a list")

    _check_wgs84_bounds(geometry)

    n_vertices = _count_vertices(geometry)
    if n_vertices > _MAX_CLIP_VERTICES:
        raise ValueError(
            f"clip_geometry has {n_vertices} vertices; "
            f"maximum allowed is {_MAX_CLIP_VERTICES}"
        )
    return geometry


# ---------------------------------------------------------------------------
# execution_id — Tapis ID format guard (prevent URL path injection)
# ---------------------------------------------------------------------------


def validate_execution_id(eid: Any) -> str:
    """Validate *eid* as a safe Tapis execution ID before URL interpolation.

    Must be a non-empty string matching ``^[A-Za-z0-9_\\-]+$``.  This
    prevents path traversal attacks (e.g. ``../../admin``) in the
    ``get_execution_status`` URL construction.

    >>> validate_execution_id("abc123-def_456")
    'abc123-def_456'
    >>> validate_execution_id("../etc/passwd")
    Traceback (most recent call last):
        ...
    ValueError: ...
    """
    if not isinstance(eid, str) or not eid:
        raise ValueError("execution_id must be a non-empty string")
    if not _EXECUTION_ID_RE.match(eid):
        raise ValueError(
            "execution_id must match ^[A-Za-z0-9_\\-]+$ "
            "(no path separators, dots, or metacharacters)"
        )
    return eid
