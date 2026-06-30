"""
DSO Geo MCP Server — entrypoint.

Creates the FastMCP application and registers all geo tools:

  Metadata tools (read-only, no CKAN write):
    gdalinfo_extract      — extract metadata from a single raster resource
    gdalinfo_summary      — extract metadata from all rasters in a dataset

  Transform tools (token-required, CKAN registration via actor ckan block):
    reproject_raster      — gdalwarp to target CRS
    convert_to_cog        — gdal_translate to Cloud-Optimized GeoTIFF
    clip_raster           — gdalwarp -cutline to GeoJSON geometry
    build_overviews       — gdaladdo on a copy (source never mutated)

  Status tool (shared):
    get_execution_status  — poll Tapis Abaco execution; parse actor logs

Usage
-----
Run via the installed script::

    dso-geo-mcp

Or directly::

    python -m dso_geo_mcp.server

Or via uv::

    uv run dso-geo-mcp
"""

from __future__ import annotations

import logging
import sys

import fastmcp

from .config import settings
from .tools import metadata, status, transform

# Configure logging to stderr so tool results (stdout) are clean.
logging.basicConfig(
    stream=sys.stderr,
    level=logging.INFO,
    format="%(asctime)s %(levelname)-8s %(name)s — %(message)s",
)

logger = logging.getLogger(__name__)

# ------------------------------------------------------------------
# FastMCP application
# ------------------------------------------------------------------

mcp = fastmcp.FastMCP(
    "DSO Geo MCP Server",
    instructions=(
        "You are connected to the DSO geospatial processing service. "
        "Use the available tools to extract metadata from rasters and run "
        "GDAL transformations on datasets stored on TACC Corral, dispatched "
        "to Tapis Abaco compute actors — no local download required. "
        "\n\n"
        "Typical workflow:\n"
        "1. Use dso-ckan tools to find a dataset and its resource IDs.\n"
        "2. Call a geo tool (e.g. gdalinfo_extract, reproject_raster) with the "
        "   resource_id and a Tapis JWT as tapis_token.\n"
        "3. The tool returns an execution_id immediately.\n"
        "4. Poll get_execution_status(execution_id) until status is COMPLETE "
        "   or FAILED.\n"
        "5. When COMPLETE, the result contains metadata or provenance, and "
        "   transform outputs are automatically registered as new CKAN resources.\n"
        "\n"
        "Token requirement:\n"
        "  Metadata tools accept an optional tapis_token (falls back to "
        "  GEO_TAPIS_TOKEN env var for anonymous CKAN reads).\n"
        "  Transform tools REQUIRE an explicit tapis_token — no env fallback.\n"
        "  Never log or return the token.\n"
        "\n"
        "SSRF guard: all CKAN resource URLs are validated to point at the "
        "configured CKAN host before being forwarded to the Abaco actor."
    ),
)

# ------------------------------------------------------------------
# Register tool modules
# ------------------------------------------------------------------

metadata.register(mcp)
transform.register(mcp)
status.register(mcp)


# ------------------------------------------------------------------
# Entrypoint
# ------------------------------------------------------------------


def main() -> None:
    """Start the MCP server over stdio (the only supported transport for v1)."""
    settings.log_startup_banner()
    mcp.run()


if __name__ == "__main__":
    main()
