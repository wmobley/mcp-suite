"""
Gated transformation tools — token-required; include CKAN registration block.

All transforms:
  1. Validate parameters (raises clear error if invalid — no submission made).
  2. Require tapis_token (no env fallback accepted — explicit security gate).
  3. Resolve resource_id → CKAN download URL (SSRF-validated).
  4. Build actor message WITH ``ckan`` block so the SAME execution also
     registers the output to CKAN automatically (single-actor mode).
  5. Submit to Abaco and return execution_id immediately.
  6. Audit every submission (tool, actor_id, operation, resource_id,
     execution_id — NEVER the token).

Tools:
  reproject_raster(resource_id, target_crs, output_name, register_to_dataset, tapis_token)
  convert_to_cog(resource_id, output_name, compression, register_to_dataset, tapis_token)
  clip_raster(resource_id, clip_geometry, output_name, register_to_dataset, tapis_token)
  build_overviews(resource_id, output_name, overview_levels, register_to_dataset, tapis_token)
"""

from __future__ import annotations

import logging
from typing import Any

import fastmcp

from ..audit import log_blocked, log_submit
from ..ckan_resolve import CKANResolveError, resolve_resource_url
from ..config import settings
from ..message import (
    build_clip_message,
    build_cog_message,
    build_overviews_message,
    build_reproject_message,
)
from ..tapis_client import TapisError, submit_message
from ..validators import (
    validate_clip_geometry,
    validate_compression,
    validate_output_name,
    validate_overview_levels,
    validate_target_crs,
)

logger = logging.getLogger(__name__)


def _require_token(tool: str, tapis_token: str | None) -> str | None:
    """Return the token or None.  Transforms REQUIRE an explicit token.

    Unlike metadata tools, transform tools do NOT fall back to the
    GEO_TAPIS_TOKEN env var.  The env var is a broad-scope fallback that is
    acceptable for read-only metadata but too risky for writes (registration).
    The caller must supply a token explicitly.

    Returns None (with audit log) if no token is present.
    """
    token = (tapis_token or "").strip()
    if not token:
        log_blocked(tool=tool, reason="no_token")
        return None
    return token


def _submit_transform(
    tool: str,
    operation: str,
    resource_id: str,
    message_dict: dict[str, Any],
    token: str,
) -> dict[str, Any]:
    """Submit a transform message and return the standard response dict."""
    actor_id = settings.geo_actor_id
    if not actor_id:
        return {"error": "GEO_ACTOR_ID is not configured"}

    try:
        execution_id = submit_message(
            actor_id=actor_id,
            message_dict=message_dict,
            token=token,
            tapis_base=settings.tapis_base,
        )
    except TapisError as exc:
        return {"error": f"Abaco submission failed: {exc}"}

    log_submit(
        tool=tool,
        actor_id=actor_id,
        operation=operation,
        resource_id=resource_id,
        execution_id=execution_id,
    )

    return {
        "execution_id": execution_id,
        "status": "SUBMITTED",
        "note": "Poll with get_execution_status(execution_id) for progress and the registered CKAN resource.",
    }


def register(mcp: fastmcp.FastMCP) -> None:
    """Register transform tools with the FastMCP app."""

    @mcp.tool()
    def reproject_raster(
        resource_id: str,
        target_crs: int,
        output_name: str,
        register_to_dataset: str | None = None,
        tapis_token: str | None = None,
    ) -> dict[str, Any]:
        """Reproject a raster via gdalwarp (Tapis Abaco actor, single-actor mode).

        Resolves the CKAN resource ID to a download URL, validates parameters,
        builds an actor message with a CKAN registration block, and submits to
        the pre-registered Abaco actor. The actor runs gdalwarp AND registers
        the output to CKAN in a single execution.

        Args:
            resource_id: CKAN resource UUID; dso-geo resolves to download URL
                (SSRF-validated).
            target_crs: EPSG code as integer (e.g. 4326); server validates range
                [1, 999999] and builds ["-t_srs", "EPSG:4326"] — never raw string.
            output_name: Bare filename (regex ^[A-Za-z0-9_\\-.]+$, must end with
                .tif); no slashes or metacharacters.
            register_to_dataset: CKAN dataset ID to register the output resource
                (optional; defaults to source resource's dataset).
            tapis_token: Tapis JWT — REQUIRED for transforms (no env fallback).

        Returns:
            {"execution_id": "<id>", "status": "SUBMITTED", "note": "..."}
            Poll with get_execution_status(execution_id) for the registered resource.
        """
        token = _require_token("reproject_raster", tapis_token)
        if token is None:
            return {
                "error": "tapis_token is required for reproject_raster (transforms are writes)",
                "hint": "Provide a Tapis JWT as tapis_token argument.",
            }

        # Validate params before touching CKAN or Abaco
        try:
            validate_target_crs(target_crs)
        except ValueError as exc:
            return {"error": f"Invalid target_crs: {exc}"}
        try:
            validate_output_name(output_name)
        except ValueError as exc:
            return {"error": f"Invalid output_name: {exc}"}

        try:
            download_url, package_id = resolve_resource_url(resource_id)
        except (CKANResolveError, ValueError) as exc:
            return {"error": f"CKAN resolution failed: {exc}"}

        effective_dataset = register_to_dataset or package_id

        msg = build_reproject_message(
            input_url=download_url,
            output_name=output_name,
            target_crs=target_crs,
            ckan_url=settings.ckan_url,
            ckan_token=token,
            package_id=effective_dataset,
            read_token=token,
        )

        return _submit_transform(
            tool="reproject_raster",
            operation="reproject",
            resource_id=resource_id,
            message_dict=msg,
            token=token,
        )

    @mcp.tool()
    def convert_to_cog(
        resource_id: str,
        output_name: str,
        compression: str = "deflate",
        register_to_dataset: str | None = None,
        tapis_token: str | None = None,
    ) -> dict[str, Any]:
        """Convert raster to Cloud-Optimized GeoTIFF via gdal_translate (Tapis Abaco).

        Args:
            resource_id: CKAN resource UUID; resolved to download URL (SSRF-validated).
            output_name: Bare filename (must end with .tif).
            compression: Compression algorithm — one of: deflate, lzw, zstd, none.
                Default: deflate.
            register_to_dataset: CKAN dataset ID for output (optional; defaults to
                source resource's dataset).
            tapis_token: Tapis JWT — REQUIRED for transforms.

        Returns:
            {"execution_id": "<id>", "status": "SUBMITTED", "note": "..."}
        """
        token = _require_token("convert_to_cog", tapis_token)
        if token is None:
            return {
                "error": "tapis_token is required for convert_to_cog (transforms are writes)",
                "hint": "Provide a Tapis JWT as tapis_token argument.",
            }

        try:
            validate_output_name(output_name)
        except ValueError as exc:
            return {"error": f"Invalid output_name: {exc}"}
        try:
            validate_compression(compression)
        except ValueError as exc:
            return {"error": f"Invalid compression: {exc}"}

        try:
            download_url, package_id = resolve_resource_url(resource_id)
        except (CKANResolveError, ValueError) as exc:
            return {"error": f"CKAN resolution failed: {exc}"}

        effective_dataset = register_to_dataset or package_id

        msg = build_cog_message(
            input_url=download_url,
            output_name=output_name,
            compression=compression,
            ckan_url=settings.ckan_url,
            ckan_token=token,
            package_id=effective_dataset,
            read_token=token,
        )

        return _submit_transform(
            tool="convert_to_cog",
            operation="cog",
            resource_id=resource_id,
            message_dict=msg,
            token=token,
        )

    @mcp.tool()
    def clip_raster(
        resource_id: str,
        clip_geometry: dict,
        output_name: str,
        register_to_dataset: str | None = None,
        tapis_token: str | None = None,
    ) -> dict[str, Any]:
        """Clip raster to geometry via gdalwarp -cutline (Tapis Abaco actor).

        Args:
            resource_id: CKAN resource UUID; resolved to download URL (SSRF-validated).
            clip_geometry: GeoJSON dict ONLY (not a string); type must be Polygon or
                MultiPolygon; coordinates within WGS84 bounds (lon ±180, lat ±90);
                max ~1000 vertices. The actor serialises this to a temp GeoJSON file
                for gdalwarp.
            output_name: Bare filename (must end with .tif).
            register_to_dataset: CKAN dataset ID for output (optional).
            tapis_token: Tapis JWT — REQUIRED for transforms.

        Returns:
            {"execution_id": "<id>", "status": "SUBMITTED", "note": "..."}
        """
        token = _require_token("clip_raster", tapis_token)
        if token is None:
            return {
                "error": "tapis_token is required for clip_raster (transforms are writes)",
                "hint": "Provide a Tapis JWT as tapis_token argument.",
            }

        try:
            validate_clip_geometry(clip_geometry)
        except ValueError as exc:
            return {"error": f"Invalid clip_geometry: {exc}"}
        try:
            validate_output_name(output_name)
        except ValueError as exc:
            return {"error": f"Invalid output_name: {exc}"}

        try:
            download_url, package_id = resolve_resource_url(resource_id)
        except (CKANResolveError, ValueError) as exc:
            return {"error": f"CKAN resolution failed: {exc}"}

        effective_dataset = register_to_dataset or package_id

        msg = build_clip_message(
            input_url=download_url,
            output_name=output_name,
            clip_geometry=clip_geometry,
            ckan_url=settings.ckan_url,
            ckan_token=token,
            package_id=effective_dataset,
            read_token=token,
        )

        return _submit_transform(
            tool="clip_raster",
            operation="clip",
            resource_id=resource_id,
            message_dict=msg,
            token=token,
        )

    @mcp.tool()
    def build_overviews(
        resource_id: str,
        output_name: str,
        overview_levels: list[int] | None = None,
        register_to_dataset: str | None = None,
        tapis_token: str | None = None,
    ) -> dict[str, Any]:
        """Build overviews on a COPY of a raster via gdaladdo (Tapis Abaco actor).

        The source raster is NEVER mutated. The actor downloads the source via
        /vsicurl/, COPIES it to a working location, runs gdaladdo on the copy,
        and registers the copy+overviews as a new CKAN resource.

        Args:
            resource_id: CKAN resource UUID; resolved to download URL. Source
                never mutated (read-only guarantee).
            output_name: Bare filename for the output copy (e.g.
                'rainfall_with_overviews.tif').
            overview_levels: List of integers, each 2–512, max 10 elements
                (e.g. [2, 4, 8, 16]). Default: [2, 4, 8].
            register_to_dataset: CKAN dataset ID for output (optional).
            tapis_token: Tapis JWT — REQUIRED for transforms.

        Returns:
            {"execution_id": "<id>", "status": "SUBMITTED", "note": "..."}
        """
        token = _require_token("build_overviews", tapis_token)
        if token is None:
            return {
                "error": "tapis_token is required for build_overviews (transforms are writes)",
                "hint": "Provide a Tapis JWT as tapis_token argument.",
            }

        levels = overview_levels if overview_levels is not None else [2, 4, 8]
        try:
            validate_overview_levels(levels)
        except ValueError as exc:
            return {"error": f"Invalid overview_levels: {exc}"}
        try:
            validate_output_name(output_name)
        except ValueError as exc:
            return {"error": f"Invalid output_name: {exc}"}

        try:
            download_url, package_id = resolve_resource_url(resource_id)
        except (CKANResolveError, ValueError) as exc:
            return {"error": f"CKAN resolution failed: {exc}"}

        effective_dataset = register_to_dataset or package_id

        msg = build_overviews_message(
            input_url=download_url,
            output_name=output_name,
            overview_levels=levels,
            ckan_url=settings.ckan_url,
            ckan_token=token,
            package_id=effective_dataset,
            read_token=token,
        )

        return _submit_transform(
            tool="build_overviews",
            operation="overviews",
            resource_id=resource_id,
            message_dict=msg,
            token=token,
        )
