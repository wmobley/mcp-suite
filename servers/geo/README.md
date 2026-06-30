# dso-geo MCP Server

A FastMCP stdio server that dispatches GDAL operations to a pre-registered
Tapis Abaco actor, enabling AI models and MCP clients to run geospatial
metadata extraction and raster transformations on data stored on TACC Corral
— without downloading files locally.

## Quick start

```bash
cd servers/geo
uv sync --extra dev
uv run dso-geo-mcp
```

Copy `.env.example` to `.env` and fill in `GEO_ACTOR_ID` (required) and
`CKAN_URL`.

## Prerequisites

- A pre-registered Tapis Abaco actor running the GHCR image
  `ghcr.io/wmobley/mcp-suite/gdal-actor`.  Register the actor once; paste
  the actor ID into `GEO_ACTOR_ID`.  **dso-geo never registers actors at
  runtime.**
- Tapis JWT (obtained via `scripts/tapis-oauth/get-jwt.sh`).  Pass as
  `tapis_token` per-call argument or set `GEO_TAPIS_TOKEN` env fallback
  (metadata tools only).

## Environment variables

| Variable | Required | Default | Description |
|---|---|---|---|
| `GEO_ACTOR_ID` | YES | — | Pre-registered Abaco actor ID |
| `TAPIS_BASE` | no | `https://portals.tapis.io` | Tapis tenant base URL |
| `CKAN_URL` | no | `http://localhost:5001` | CKAN portal base URL |
| `GEO_ALLOWED_CKAN_HOST` | no | CKAN_URL hostname | SSRF guard hostname |
| `GEO_TAPIS_TOKEN` | no | — | Env fallback JWT (metadata only; warns on production) |
| `GEO_POLL_TIMEOUT_S` | no | `10` | HTTP timeout per poll call (seconds) |
| `GEO_POLL_RETRIES` | no | `1` | Retries per poll call |

## Tools

### Metadata (read-only)

**`gdalinfo_extract(resource_id, include_stats=True, tapis_token=None)`**
Extract GDAL metadata from a raster resource. Returns `execution_id` immediately;
poll with `get_execution_status`.

**`gdalinfo_summary(dataset_id, tapis_token=None)`**
Extract metadata from all rasters in a CKAN dataset (max 10). Returns a list
of `execution_id`s.

### Transformations (token required, auto-register output to CKAN)

All transform tools require an explicit `tapis_token`.  They include a `ckan`
block in the actor message so the SAME execution registers the output as a
new CKAN resource automatically (single-actor mode).

**`reproject_raster(resource_id, target_crs, output_name, register_to_dataset=None, tapis_token=None)`**
Reproject a raster to an EPSG CRS via gdalwarp.

**`convert_to_cog(resource_id, output_name, compression="deflate", register_to_dataset=None, tapis_token=None)`**
Convert to Cloud-Optimized GeoTIFF via gdal_translate.

**`clip_raster(resource_id, clip_geometry, output_name, register_to_dataset=None, tapis_token=None)`**
Clip to a GeoJSON Polygon/MultiPolygon via gdalwarp -cutline.

**`build_overviews(resource_id, output_name, overview_levels=[2,4,8], register_to_dataset=None, tapis_token=None)`**
Build overviews on a COPY of a raster via gdaladdo. Source never mutated.

### Status polling

**`get_execution_status(execution_id, tapis_token=None)`**
Poll once; the MCP client/model drives the retry loop. When terminal
(COMPLETE/FAILED/ERROR), fetches actor logs and parses structured JSON.
Returns `result` (actor JSON) on COMPLETE; `error` on FAILED/ERROR.

## Typical workflow

```
1. Use dso-ckan tools to find a dataset and resource_id.
2. Call gdalinfo_extract(resource_id, tapis_token="eyJ...")
   → {"execution_id": "abc123", "status": "SUBMITTED"}
3. Poll get_execution_status("abc123", tapis_token="eyJ...")
   → {"status": "RUNNING", ...}  (poll again)
   → {"status": "COMPLETE", "result": {"metadata": {...}}}
```

For transforms, the result also includes:
```json
{
  "status": "COMPLETE",
  "result": {"operation": "reproject", "output_path": "...", ...},
  "registered": {"status": "ok", "resource": {"id": "new-ckan-resource-uuid"}}
}
```

## Composing with dso-ckan

dso-ckan finds datasets and resource IDs; dso-geo operates on them.  The
model uses both servers together:

1. `dso-ckan`: `package_search("twdb-ntgam")` → dataset + resource list
2. `dso-geo`: `gdalinfo_extract(resource_id, tapis_token=...)` → metadata
3. `dso-geo`: `reproject_raster(resource_id, 4326, "out.tif", tapis_token=...)` → new CKAN resource

dso-geo calls CKAN directly for URL resolution and actor registration; it
does NOT call dso-ckan via MCP.

## Security

- **Token handling**: Per-call `tapis_token` args are never stored, logged,
  or returned.  Bearer/JWT patterns are scrubbed from all Tapis error
  responses before they surface to the caller.
- **SSRF guard**: Resolved CKAN download URLs are validated to point at
  `GEO_ALLOWED_CKAN_HOST` (defaults to CKAN_URL hostname) before being
  forwarded to the Abaco actor.
- **Parameter validation**: All params are validated at the MCP layer (and
  again inside the actor) before any actor message is built or submitted.
- **Transform token gate**: Transform tools explicitly require `tapis_token`
  and do NOT fall back to the `GEO_TAPIS_TOKEN` env var, reducing ambient
  write exposure.

## Tests

```bash
cd servers/geo
PATH="$HOME/.local/bin:$PATH" uv run pytest -q
```

All tests are mocked — no live Tapis or CKAN required.
