"""
Actor message builder for dso-geo.

Constructs the validated message dict that is POSTed to the Abaco actor.

Message schema (must match gdal-actor/actor.py exactly):
{
    "operation":    "gdalinfo" | "reproject" | "cog" | "clip" | "overviews",
    "input_url":    "https://...",
    "output_name":  "result.tif",        # omitted / ignored for gdalinfo
    "params": {
        "target_crs":      4326,         # reproject only
        "compression":     "deflate",    # cog only
        "overview_levels": [2, 4, 8],    # overviews only
        "clip_geometry":   {...}         # clip only
    },
    "include_stats": false,              # gdalinfo only
    "read_token":    "eyJ...",           # OPTIONAL: for private /vsicurl/ reads
    "ckan": {                            # OPTIONAL: include for transforms to auto-register
        "url":        "https://ckan.example.org",
        "token":      "eyJ...",
        "package_id": "my-dataset",
        "extra":      {}
    }
}

Security notes
--------------
- Parameters are validated by validators.py BEFORE build_message is called.
  build_message trusts its inputs (callers must validate first).
- The read_token / ckan.token fields carry the Tapis JWT.  They are present
  in the dict but are NEVER passed to audit functions — the caller strips
  them before auditing.
- The ``ckan`` block is included only for transform operations that have
  a register_to_dataset target (or when the source dataset is known).
"""

from __future__ import annotations

from typing import Any


def build_gdalinfo_message(
    input_url: str,
    include_stats: bool = True,
    read_token: str | None = None,
) -> dict[str, Any]:
    """Build a gdalinfo actor message.

    Parameters
    ----------
    input_url:
        Validated CKAN download URL (SSRF-checked by ckan_resolve).
    include_stats:
        Whether to compute band statistics (passed to actor).
    read_token:
        Optional Tapis JWT for private /vsicurl/ reads (never logged).

    Returns
    -------
    dict
        Actor message ready for JSON serialisation.
    """
    msg: dict[str, Any] = {
        "operation": "gdalinfo",
        "input_url": input_url,
        "include_stats": include_stats,
        "params": {},
    }
    if read_token:
        msg["read_token"] = read_token
    return msg


def build_reproject_message(
    input_url: str,
    output_name: str,
    target_crs: int,
    ckan_url: str,
    ckan_token: str,
    package_id: str,
    read_token: str | None = None,
) -> dict[str, Any]:
    """Build a reproject actor message with CKAN registration block.

    Parameters
    ----------
    input_url:
        Validated CKAN download URL.
    output_name:
        Validated bare filename (e.g. ``"result.tif"``).
    target_crs:
        Validated EPSG integer (e.g. ``4326``).
    ckan_url:
        CKAN portal URL for the registration block.
    ckan_token:
        Tapis JWT for the actor's CKAN registration call (never logged).
    package_id:
        CKAN dataset ID to register the output into.
    read_token:
        Optional Tapis JWT for private /vsicurl/ reads (never logged).

    Returns
    -------
    dict
        Actor message ready for JSON serialisation.
    """
    msg: dict[str, Any] = {
        "operation": "reproject",
        "input_url": input_url,
        "output_name": output_name,
        "params": {"target_crs": target_crs},
        "ckan": {
            "url": ckan_url,
            "token": ckan_token,
            "package_id": package_id,
            "extra": {},
        },
    }
    if read_token:
        msg["read_token"] = read_token
    return msg


def build_cog_message(
    input_url: str,
    output_name: str,
    compression: str,
    ckan_url: str,
    ckan_token: str,
    package_id: str,
    read_token: str | None = None,
) -> dict[str, Any]:
    """Build a COG conversion actor message with CKAN registration block."""
    msg: dict[str, Any] = {
        "operation": "cog",
        "input_url": input_url,
        "output_name": output_name,
        "params": {"compression": compression},
        "ckan": {
            "url": ckan_url,
            "token": ckan_token,
            "package_id": package_id,
            "extra": {},
        },
    }
    if read_token:
        msg["read_token"] = read_token
    return msg


def build_clip_message(
    input_url: str,
    output_name: str,
    clip_geometry: dict[str, Any],
    ckan_url: str,
    ckan_token: str,
    package_id: str,
    read_token: str | None = None,
) -> dict[str, Any]:
    """Build a clip actor message with CKAN registration block."""
    msg: dict[str, Any] = {
        "operation": "clip",
        "input_url": input_url,
        "output_name": output_name,
        "params": {"clip_geometry": clip_geometry},
        "ckan": {
            "url": ckan_url,
            "token": ckan_token,
            "package_id": package_id,
            "extra": {},
        },
    }
    if read_token:
        msg["read_token"] = read_token
    return msg


def build_overviews_message(
    input_url: str,
    output_name: str,
    overview_levels: list[int],
    ckan_url: str,
    ckan_token: str,
    package_id: str,
    read_token: str | None = None,
) -> dict[str, Any]:
    """Build an overviews actor message with CKAN registration block."""
    msg: dict[str, Any] = {
        "operation": "overviews",
        "input_url": input_url,
        "output_name": output_name,
        "params": {"overview_levels": overview_levels},
        "ckan": {
            "url": ckan_url,
            "token": ckan_token,
            "package_id": package_id,
            "extra": {},
        },
    }
    if read_token:
        msg["read_token"] = read_token
    return msg


def scrub_message_for_audit(msg: dict[str, Any]) -> dict[str, Any]:
    """Return a copy of *msg* with token fields redacted for safe audit logging.

    Strips ``read_token`` and ``ckan.token`` from the dict.  Does NOT modify
    the original.

    Parameters
    ----------
    msg:
        The raw actor message dict.

    Returns
    -------
    dict
        Safe copy with sensitive fields replaced by ``"[REDACTED]"``.
    """
    import copy
    safe = copy.deepcopy(msg)
    if "read_token" in safe:
        safe["read_token"] = "[REDACTED]"
    if "ckan" in safe and isinstance(safe["ckan"], dict):
        if "token" in safe["ckan"]:
            safe["ckan"]["token"] = "[REDACTED]"
    return safe
