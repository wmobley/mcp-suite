"""
Metadata extraction tools — read-only, no CKAN registration block.

Tools:
  gdalinfo_extract(resource_id, include_stats, tapis_token)
      Resolves resource_id → CKAN download URL → builds gdalinfo actor
      message → submits to Abaco → returns execution_id.

  gdalinfo_summary(dataset_id, tapis_token)
      Resolves dataset_id → list of raster resource URLs (max 10) →
      submits one gdalinfo message per resource → returns list of execution_ids.

Both tools return immediately (non-blocking).  Poll with
get_execution_status(execution_id) to retrieve results.

Token handling
--------------
Per-call ``tapis_token`` takes precedence; env ``GEO_TAPIS_TOKEN`` is
the fallback.  Token is forwarded to the actor as ``read_token`` (for
private /vsicurl/ reads) but is NEVER logged or included in audit records.
"""

from __future__ import annotations

import logging
from typing import Any

import fastmcp

from ..audit import log_submit
from ..ckan_resolve import CKANResolveError, resolve_dataset_raster_urls, resolve_resource_url
from ..config import settings
from ..message import build_gdalinfo_message
from ..tapis_client import TapisError, submit_message

logger = logging.getLogger(__name__)


def register(mcp: fastmcp.FastMCP) -> None:
    """Register metadata tools with the FastMCP app."""

    @mcp.tool()
    def gdalinfo_extract(
        resource_id: str,
        include_stats: bool = True,
        tapis_token: str | None = None,
    ) -> dict[str, Any]:
        """Extract GDAL metadata from a raster via gdalinfo (Tapis Abaco Actor).

        Resolves the CKAN resource ID to a download URL (SSRF-validated), builds
        a gdalinfo actor message, and submits it to the pre-registered Abaco actor.
        Returns immediately with the execution_id.

        Args:
            resource_id: CKAN resource UUID (e.g. 'abc123def456'). dso-geo
                resolves this to the CKAN resource record, extracts the download
                URL, and validates it points at the configured CKAN host (SSRF guard).
            include_stats: Whether to compute band statistics (slower for large
                rasters). Default True.
            tapis_token: Tapis JWT for CKAN API + Abaco API access. Falls back to
                GEO_TAPIS_TOKEN env var if not provided.

        Returns:
            {"execution_id": "<id>", "status": "SUBMITTED", "note": "..."}
            Poll with get_execution_status(execution_id) to retrieve metadata.
        """
        # ── Resolve token ─────────────────────────────────────────────────────
        token = (tapis_token or "").strip() or (settings.geo_tapis_token or "")
        # Metadata tools: token needed for Abaco submission; CKAN read is anonymous.
        # Allow proceeding even without token — actor may use its own identity.
        # (Transforms require token; metadata is read-only.)

        actor_id = settings.geo_actor_id
        if not actor_id:
            return {"error": "GEO_ACTOR_ID is not configured"}

        # ── Resolve CKAN resource → URL ───────────────────────────────────────
        try:
            download_url, _package_id = resolve_resource_url(resource_id)
        except (CKANResolveError, ValueError) as exc:
            return {"error": f"CKAN resolution failed: {exc}"}

        # ── Build actor message ───────────────────────────────────────────────
        msg = build_gdalinfo_message(
            input_url=download_url,
            include_stats=include_stats,
            read_token=token or None,
        )

        # ── Submit to Abaco ───────────────────────────────────────────────────
        if not token:
            return {
                "error": "tapis_token is required to submit to Abaco",
                "hint": "Provide tapis_token argument or set GEO_TAPIS_TOKEN env var",
            }

        try:
            execution_id = submit_message(
                actor_id=actor_id,
                message_dict=msg,
                token=token,
                tapis_base=settings.tapis_base,
            )
        except TapisError as exc:
            return {"error": f"Abaco submission failed: {exc}"}

        log_submit(
            tool="gdalinfo_extract",
            actor_id=actor_id,
            operation="gdalinfo",
            resource_id=resource_id,
            execution_id=execution_id,
        )

        return {
            "execution_id": execution_id,
            "status": "SUBMITTED",
            "note": "Poll with get_execution_status(execution_id) to retrieve metadata.",
        }

    @mcp.tool()
    def gdalinfo_summary(
        dataset_id: str,
        tapis_token: str | None = None,
    ) -> dict[str, Any]:
        """Extract metadata from all rasters in a CKAN dataset.

        Resolves the dataset ID to its resource list (capped at 10), submits
        one gdalinfo actor message per resource, and returns the list of
        execution_ids immediately.

        Args:
            dataset_id: CKAN dataset ID or slug (e.g. 'twdb-ntgam'). dso-geo
                calls package_show to list resources; max 10 resources per call.
            tapis_token: Tapis JWT for Abaco API access. Falls back to
                GEO_TAPIS_TOKEN env var if not provided.

        Returns:
            {
                "executions": [
                    {"resource_id": "...", "name": "...", "execution_id": "..."},
                    ...
                ],
                "submitted": <count>,
                "skipped": <count>,
                "note": "Poll each execution_id with get_execution_status()"
            }
        """
        token = (tapis_token or "").strip() or (settings.geo_tapis_token or "")
        if not token:
            return {
                "error": "tapis_token is required to submit to Abaco",
                "hint": "Provide tapis_token argument or set GEO_TAPIS_TOKEN env var",
            }

        actor_id = settings.geo_actor_id
        if not actor_id:
            return {"error": "GEO_ACTOR_ID is not configured"}

        # ── Resolve dataset → resource URLs ───────────────────────────────────
        try:
            resources = resolve_dataset_raster_urls(dataset_id, max_resources=10)
        except (CKANResolveError, ValueError) as exc:
            return {"error": f"CKAN dataset resolution failed: {exc}"}

        if not resources:
            return {
                "error": f"No accessible raster resources found in dataset {dataset_id!r}",
                "submitted": 0,
            }

        executions: list[dict[str, Any]] = []
        skipped = 0

        for res in resources:
            resource_id = res["resource_id"]
            url = res["url"]
            name = res["name"]

            msg = build_gdalinfo_message(
                input_url=url,
                include_stats=True,
                read_token=token,
            )

            try:
                execution_id = submit_message(
                    actor_id=actor_id,
                    message_dict=msg,
                    token=token,
                    tapis_base=settings.tapis_base,
                )
            except TapisError as exc:
                logger.warning(
                    "Failed to submit gdalinfo for resource %s: %s", resource_id, exc
                )
                skipped += 1
                continue

            log_submit(
                tool="gdalinfo_summary",
                actor_id=actor_id,
                operation="gdalinfo",
                resource_id=resource_id,
                execution_id=execution_id,
            )
            executions.append(
                {
                    "resource_id": resource_id,
                    "name": name,
                    "execution_id": execution_id,
                }
            )

        return {
            "executions": executions,
            "submitted": len(executions),
            "skipped": skipped,
            "note": "Poll each execution_id with get_execution_status() to retrieve metadata.",
        }
