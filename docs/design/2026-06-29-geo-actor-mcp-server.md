# Geospatial GDAL MCP Server (dso-geo)

## Status

**Approved — Phase-0a PASSED (2026-06-30, GO).** Proof-of-life on the live `portals.tapis.io` tenant: the public GHCR image `ghcr.io/wmobley/mcp-suite/gdal-actor` was **registered → READY → executed → polled to COMPLETE, and its stdout (our `actor.py` structured JSON) retrieved via the executions/logs endpoint** (PASS=5/5). Full compute path validated: Abaco runs our image, the `MSG` env contract works, structured JSON output works, log retrieval works.
- **Pipeline-shape RESOLVED → single-actor mode** (supersedes the 2-stage HTTP-triggered pipeline): the actor does GDAL **and** the CKAN register in ONE execution (message carries a `ckan` block) — no HTTP-triggered chaining needed; already supported by the built image.
- **dso-geo runtime model**: a PRE-REGISTERED persistent actor (the GHCR image registered once, referenced by env `GEO_ACTOR_ID`), not register-per-call. dso-geo submits a message → polls the execution → reads logs for the actor's JSON result.
- **Remaining live confirmation (NOT blocking the server build)**: checks 5–6 — real `gdalinfo`/transform over `/vsicurl/` against a TACC-routable CKAN URL + actor egress (`/vsicurl` already verified offline).

FINAL design (after the panel review AND the user's 2026-06-30 directives — see Decisions 15–18 / Superseded S1–S5, which take precedence over the earlier Decisions 1–14 where they conflict):
- **CKAN-linked**: dso-geo HAS a CKAN client; tools take CKAN resource/dataset IDs; the Abaco pipeline registers outputs back to CKAN automatically (supersedes the earlier "no CKAN client / hand-back-to-model").
- **Data access via `/vsicurl/`**: GDAL reads the source over the CKAN download URL (HTTP range reads); no Corral POSIX mount. Output uploaded+registered to CKAN by a pipeline task.
- **Compute = Abaco Actors only** (Tapis Jobs rejected for v1). Transforms run as an Abaco actor pipeline (GDAL stage → register-to-CKAN task).
- **No dry-run** — transforms execute on call (non-destructive: new CKAN resource, source untouched; still validated + audited).
- **Carried-over controls remain**: GDAL injection-validation contract, token scrubbing middleware, cost/concurrency caps via Abaco query, audit, and SSRF guard (input URL built from the CKAN resource record, never user-supplied).
- **Tenant signal (2026-06-30)**: `GET /v3/actors` on `portals.tapis.io` is live (HTTP 400 "No JWT…", Abaco version 26Q2.0) — Abaco appears deployed; authenticated register→execute→poll is the Phase-0a gate. App-registration rights confirmed by user.
- **Default decisions locked for the deferrable items** (revisable after Phase-0a) — see refreshed Pending/Defaults below.

## Objective

Build a standalone Python + FastMCP (stdio) MCP server — call it `dso-geo` — that enables MCP clients (Claude Code, Windsurf, Cursor, etc.) to run GDAL metadata extraction and gated transformations on geospatial datasets stored on TACC Corral, by dispatching work to Tapis Abaco compute (Actors and actor pipelines) rather than processing bytes locally. **v1 includes both metadata extraction AND gated transformations**; all transformations follow gated submission + async polling + audit trail.

The server executes GDAL operations asynchronously (submit → immediate return of execution ID → separate polling tool to check status) and composes tightly with the CKAN server: derived outputs are **uploaded and registered back to CKAN by a PIPELINE TASK within the Abaco actor pipeline** (a downstream stage after GDAL execution), not by the model directly. The dso-geo server has a CKAN client to resolve CKAN resource IDs to download URLs; tools accept resource IDs and dataset IDs (for `gdalinfo_summary`), not Corral paths.

**Prerequisites**: 
- A **Phase-0 proof-of-life experiment** must verify that Tapis Abaco actor execution is viable on the target `portals.tapis.io` tenant BEFORE any dso-geo Python code is written. This is a hard Go/No-Go gate.
- A GDAL-capable Tapis Abaco actor image must be built and registered before any geo tools can run (Phase 0, critical path).

## User Need

**Data scientists, modelers, and AI agents** need programmatic access to GDAL operations on large geospatial files (30–50 MB+ rasters) without downloading and processing locally. Specific workflows:

- "Extract metadata from this GeoTIFF: CRS, bands, extent, nodata values"
- "Reproject this raster to EPSG:4326 and register the result back in CKAN"
- "Convert this raster to Cloud-Optimized GeoTIFF (COG) and derive a Zarr mosaic"
- "Clip this dataset to the Texas boundary and store on Corral"

**Why remote compute**: Data lives on Corral (TACC's parallel filesystem); running GDAL locally means downloading 30–50 MB+ files over the network. Running in a Tapis Actor/Job next to the data on Corral is orders of magnitude faster and eliminates bandwidth bottlenecks.

## Current Code/System Summary

### Existing Geospatial Infrastructure

- **CKAN Portal**: `ckanext-tapisfilestore` only **serves** (`IResourceController`) `tapis://` URLs; it does **not** ingest bytes or run compute. Resources have `download_url` fields accessible via CKAN API.
- **GDAL/Rasterio**: Not currently used in the repo (greenfield).
- **Tapis Compute**: Tapis Abaco (Actors) is the definitive compute service.
  - **Tapis Actors (Abaco)**: Event-driven, sub-10-sec startup, good for all operations including transforms; can be chained into actor pipelines with downstream HTTP-triggered tasks for registration
  - Both are accessed via REST API; execution IDs returned asynchronously
- **Data Storage**: Portal datasets are on Corral (`url_type: upload`, mounted at `/data/ckan/` in production CKAN container). Corral is also mounted on TACC compute nodes. GDAL can read raster sources via HTTP range reads using GDAL's `/vsicurl/` driver (no POSIX mount or full-file copy needed).
- **Tapis Auth**: Portal uses Tapis OAuth2 JWTs (`X-Tapis-Token` header). The `dso-geo` server will authenticate to CKAN and Tapis APIs with a Tapis JWT, passed per-call as a tool argument (same pattern as the CKAN write server).

### Existing Tapis Integration Points

- `scripts/tapis-oauth/get-jwt.sh` — obtain Tapis OAuth2 JWT (password grant)
- `ckanext-tapisfilestore` — serves `tapis://` URLs; no compute
- `ckanext-oauth2` — validates JWTs via RS256; `X-Tapis-Token` header support
- `src/ckanext-tapisfilestore/ckanext/tapisfilestore/plugin.py` — shows token retrieval patterns (oauth2_get_stored_token, toolkit.g.usertoken, request headers)

### MINT/TWDB Integration

- TWDB/NTGAM datasets are multi-band GeoTIFF rasters on Corral
- Stored in CKAN as resources (url_type: upload, CKAN storage path → Corral)
- Typical workflow: scientists want to reproject, clip, or convert to COG for web visualization

### MCP Architecture Pattern (from CKAN Server)

The existing CKAN server (just completed) provides the template:
- FastMCP + stdio transport
- Shared infrastructure: API client, config/settings, logging
- Tool modules registered with the mcp app
- Read tools (anonymous) vs. write tools (token-gated, dry-run-first)
- Audit logging to stderr
- Token scrubbing in logs/errors

## Proposed Design

### Architecture Overview

```
┌──────────────────────────────────────────────────────────┐
│  MCP Client (Claude Code, Windsurf, Cursor, etc.)        │
└─────────────────────┬──────────────────────────────────┘
                      │ MCP Protocol (stdio)
                      ▼
┌──────────────────────────────────────────────────────────┐
│  FastMCP Server (Python 3.10+, dso-geo)                 │
├──────────────────────────────────────────────────────────┤
│                                                          │
│  Tools Layer:                                           │
│  ├─ Metadata extraction tools (gdalinfo_*)             │
│  ├─ Transformation tools (reproject, convert, etc.)    │
│  │                                                      │
│  Internal:                                              │
│  ├─ CKAN client (resolve resource/dataset IDs → URLs)  │
│  ├─ Tapis Abaco dispatcher (actor execution API)       │
│  ├─ Actor pipeline poller (async execution tracking)   │
│  ├─ Result fetcher (retrieve outputs from CKAN)        │
│  └─ Config (Tapis auth, Actor ID, CKAN client, etc.)  │
│                                                          │
└────────────┬──────────────────────────────────────────┘
             │ Tapis API + CKAN API (HTTP + JWT auth)
             ▼
┌──────────────────────────────────────────────────────────┐
│  TACC Tapis Services                                     │
├──────────────────────────────────────────────────────────┤
│  Actor Service (Abaco) — lightweight event-driven         │
│  (hosts GDAL actor + downstream register-to-CKAN task)  │
└──────────────────────────────────────────────────────────┘
             │                    ▲
             │ (/vsicurl/...)     │ (CKAN resource_create)
             ▼                    │
┌──────────────────────────────────────────────────────────┐
│  TACC Corral (Geospatial data storage)                   │
│  /data/ckan/* (CKAN-managed source datasets)             │
│  /data/ckan/storage/* (Output files → CKAN resources)    │
└──────────────────────────────────────────────────────────┘
             ▲                    │
             └────────────────────┘
             CKAN portal (serves resources from Corral)
```

### Tool Set (V1 Full Scope: Metadata + Gated Transforms)

All v1 tools follow the same execution model: submit to Tapis Abaco actor pipeline, return execution ID immediately, poll asynchronously.

#### Metadata Extraction Tools

These tools extract metadata from rasters via Tapis Abaco; they do NOT modify data. **NOTE**: Geo tools accept **CKAN resource IDs** (not Corral paths). dso-geo internally resolves resource ID → CKAN resource record (with download URL) via `resource_show()`, then passes the HTTP URL to the Abaco actor for reading via GDAL's `/vsicurl/` driver.

**GDAL Metadata Tools:**

- `gdalinfo_extract(resource_id: str, include_stats: bool = True, tapis_token: str | None = None)` — Extract metadata from a raster via `gdalinfo`.
  - *Inputs*:
    - `resource_id`: CKAN resource UUID (e.g., `abc123def456`). dso-geo resolves this to the CKAN resource record, extracts the download URL, and validates it points at the configured CKAN host (SSRF control).
    - `include_stats` (bool, default true): Whether to compute band statistics (slower for large rasters; optional)
    - `tapis_token` (str): Tapis JWT for CKAN API + Abaco API access (optional env fallback)
  - *Output*: Execution ID; call `get_execution_status(execution_id)` to poll for structured metadata dict (CRS, bands, extent, nodata, overviews, stats, execution audit)
  - *Rationale*: Fast, read-only, idempotent. Execution happens on Tapis Abaco actor (quick metadata extraction). Source is never mutated.
  - *Validation*: Server validates resource_id + resolved URL at MCP layer AND inside Abaco container entrypoint (defense in depth); SSRF guard enforced on resolved URL.

- `gdalinfo_summary(dataset_id: str, tapis_token: str | None = None)` — Extract metadata from all rasters in a CKAN dataset.
  - *Inputs*: CKAN dataset ID (e.g., `twdb-ntgam`), tapis_token. Internally calls `package_show(dataset_id)` to list resources; max 10 resources per call (concurrency cap).
  - *Output*: Execution ID for an Abaco actor execution that processes all resources in parallel (or sequentially, depending on actor design); poll to retrieve list of metadata dicts (one per resource). Returns per-resource success/failure list for partial-failure handling.
  - *Enables*: "Show me metadata for all rasters in the TWDB NTGAM dataset" (dso-geo resolves dataset → resources → URLs internally)
  - *Rationale*: Single tool call with dataset ID is simpler than MODEL resolving resources first. dso-geo handles all CKAN lookups.

#### Gated Transformation Tools (V1, Executed on Submission)

These tools submit GDAL operations to an Abaco actor pipeline and are executed immediately on submission. No dry-run preview (removed per user decision, 2026-06-30). All transformations are audited and create new CKAN resources via a downstream pipeline task.

**Transformation Tools** (execute on submission; no dry-run):

- `reproject_raster(resource_id: str, target_crs: int, output_name: str, register_to_dataset: str | None = None, tapis_token: str | None = None)` — Reproject a raster to a target CRS via `gdalwarp`.
  - *Inputs*: 
    - `resource_id`: CKAN resource UUID; dso-geo resolves to download URL (SSRF-validated)
    - `target_crs`: EPSG code as integer (e.g., `4326`). Server validates range (1–999999) and builds flag as `["-t_srs", f"EPSG:{target_crs}"]` from integer only
    - `output_name`: Bare filename only (e.g., `rainfall_epsg4326.tif`), regex `^[A-Za-z0-9_\-.]+$`; no slashes, `..`, metacharacters. Server prepends output prefix; caller cannot escape.
    - `register_to_dataset`: CKAN dataset ID to register the output resource (optional; defaults to source resource's dataset)
    - `tapis_token`: Tapis JWT for CKAN + Abaco APIs (optional env fallback)
  - *Output* (immediate): Execution ID (e.g., `actor-xyz123`); poll `get_execution_status()` for pipeline progress and final CKAN resource ID
  - *Pipeline stages*: 
    1. Abaco GDAL actor: resolves source URL → reads via `/vsicurl/` → executes gdalwarp → writes output to Corral-backed storage
    2. Downstream task: calls CKAN `resource_create` to register the output as a new resource in the dataset, with provenance metadata
  - *Provenance fields*: source_resource_id, source_ckan_url, gdal_command, gdal_version (from actor image label), execution_id, dso_geo_version, submitted_by (Tapis user from JWT claims), timestamp
  - *Risk note*: No preview/dry-run; transform is a heavier action (reads + computes + creates CKAN resource). Acceptable because non-destructive (new resource), authenticated, parameter-validated, and audited.

- `convert_to_cog(resource_id: str, output_name: str, compression: str = "deflate", register_to_dataset: str | None = None, tapis_token: str | None = None)` — Convert raster to Cloud-Optimized GeoTIFF via `gdal_translate`.
  - *Inputs*: resource_id, output_name (same validation), compression (enum: deflate, lzw, zstd, none), register_to_dataset, token
  - *Output* (immediate): Execution ID; poll for pipeline progress and final resource ID + provenance

- `clip_raster(resource_id: str, clip_geometry: dict, output_name: str, register_to_dataset: str | None = None, tapis_token: str | None = None)` — Clip a raster to a geometry via `gdalwarp -cutline`.
  - *Inputs*: 
    - `resource_id`: CKAN resource UUID (resolved to download URL, SSRF-validated)
    - `clip_geometry`: GeoJSON dict ONLY (NO string form); geometry type Polygon or MultiPolygon only; coords within WGS84 bounds; max ~1000 vertices. Abaco actor serializes to temp file for gdalwarp.
    - `output_name`: Bare filename (same validation)
    - `register_to_dataset`, `tapis_token`
  - *Output* (immediate): Execution ID; poll for pipeline progress and final resource ID + provenance

- `build_overviews(resource_id: str, output_name: str, overview_levels: list[int] = [2, 4, 8], register_to_dataset: str | None = None, tapis_token: str | None = None)` — Build overviews on a COPY of a raster via `gdaladdo`.
  - *Inputs*: 
    - `resource_id`: CKAN resource UUID (resolved to download URL, never mutated)
    - `output_name`: Bare filename for output (e.g., `rainfall_with_overviews.tif`)
    - `overview_levels`: List of integers, each 2–512, max 10 elements (e.g., `[2, 4, 8, 16]`)
    - `register_to_dataset`, `tapis_token`
  - *Implementation*: Abaco actor downloads source via `/vsicurl/` to a temp location, COPIES to a working location, then runs `gdaladdo` on the copy. Source is never mutated (read-only guarantee). Output is uploaded to Corral-backed storage + registered as a new CKAN resource via pipeline task.
  - *Rationale*: gdaladdo's default behavior is in-place modification; we must prevent that. Output is always a new CKAN resource.
  - *Output* (immediate): Execution ID; poll for pipeline progress and final resource ID + provenance

#### Status Polling Tool (Shared, Registered Once)

- `get_execution_status(execution_id, tapis_token=...)` — Poll status of a Tapis Actor/Job execution.
  - *Inputs*: Execution ID from any tool (metadata or transform). Execution ID is type-tagged (e.g., `actor:<id>` or `job:<id>`); tool parses prefix to hit correct Tapis endpoint.
  - *Output*: Status ("SUBMITTED", "RUNNING", "COMPLETED", "FAILED"), progress, and (if complete) result dict with output path, provenance, metrics
  - *Module*: Implemented in `tools/status.py` (NOT duplicated in metadata.py / transform.py). Registered once with FastMCP.
  - *Polling bounds*: Max attempts + interval configurable per operation (documented in config); interrupted-polling → orphaned execution risk acknowledged; mitigated by deterministic output naming (see Rollout section).
  - *Model flow*: Claude Code polls implicitly in a loop until COMPLETED or FAILED

### Async Execution Model (DECISION: Submit + Separate Polling)

**Design**: All tools immediately return execution ID (non-blocking); a separate `get_execution_status(execution_id)` tool polls progress and returns results/output paths when complete.

*Rationale*: Aligns with Tapis async semantics; supports long-running ops; mirrors existing `mint-runner` pattern; non-blocking UX; Claude Code can poll implicitly in a loop.

*Workflow*:
1. User calls transform tool with `dry_run=False` → returns execution ID immediately
2. Claude Code repeatedly calls `get_execution_status(execution_id)` until COMPLETED or FAILED
3. Once done, status includes result dict (output path, provenance, metrics)
4. MODEL orchestrates CKAN registration: calls `dso_ckan_mcp.schema_create_resource(corral_path, dataset_id, ..., provenance={...})`

### Compute Strategy: Tapis Abaco Actors Only

**Tapis Actors (Abaco)**: Lightweight, event-driven, ~1–10 sec startup. Definitive choice for all operations (metadata extraction and transforms).

**v1 Strategy**:
- All operations (metadata extraction, transforms) → Tapis Abaco actors
- Transforms are chained as actor pipelines: GDAL actor stage → HTTP-triggered downstream registration task (CKAN resource_create)
- No Tapis Jobs (rejected per user decision, 2026-06-30; simpler architecture, no dual execution-id types)

**Architecture**:
- Single GDAL Abaco actor image (Python entrypoint, GDAL ops whitelist, param/path validators)
- Actor pipeline for transforms: Stage 1 (GDAL via `/vsicurl/`) → Stage 2 (HTTP POST to CKAN resource_create with provenance)
- Metadata operations dispatch directly to the GDAL actor (lightweight, read-only)

### GDAL Image: Phase 0 (Critical Path, NOT Deferred)

**Decision**: MUST BUILD a GDAL-capable Tapis Actor/Job image BEFORE any geo tools can run.

**Phase 0 Deliverables** (dependency for all other phases):
1. Dockerfile with `osgeo/gdal` base (or equivalent GDAL-capable image)
2. Python 3.10+ and JSON output support
3. Fixed entrypoint with whitelisted GDAL operations + validated parameter passing (NO arbitrary shell)
4. Tapis app definition (Actor or Job) to register the image as a reusable compute resource
5. Configuration: image URI, Tapis Actor/Job ID, cost/timeout caps, Corral access method (mounted path, Tapis Files staging, etc.)

**Unknown (to be decided during Phase 0 implementation)**:
- Tapis Actor (Abaco) vs Tapis Job app for the image?
- Base image: `osgeo/gdal`, `ubuntu + gdal`, other?
- Corral access mechanism: direct mounted path, Tapis Files API staging, container-level binding?
- Command/param validation: whitelist specific GDAL commands, restrict options, escape shell args?

### Composition with CKAN Server (DECISION: Tight Integration via Pipeline)

**Design**: dso-geo HAS a CKAN client (similar to dso-ckan's model, shared or embedded). Tools accept CKAN resource IDs and dataset IDs. Transformation tools submit Abaco actor PIPELINES that automatically register outputs back to CKAN as new resources; no separate MODEL orchestration needed for registration.

*Rationale*: Tighter integration reduces friction; pipeline automation ensures provenance is always captured; outputs are automatically discoverable in CKAN; MODEL doesn't need to handle the register step.

*Data flow*:
1. MODEL: "Reproject resource X (in dataset Y) and save as COG-X"
2. dso-geo: 
   - Resolves resource ID X → CKAN resource record + download URL
   - Validates URL (SSRF control)
   - Submits Abaco actor pipeline: [GDAL stage (read X via /vsicurl/, reproject, write) → Register stage (CKAN resource_create with provenance)]
   - Returns execution ID immediately
3. MODEL: polls `get_execution_status(execution_id)` until complete → receives pipeline result with new resource ID + CKAN URL
4. Pipeline handles registration automatically (dso-geo does not call dso-ckan MCP server; both call CKAN Action API directly)

*Implication*: dso-geo includes CKAN client (recommend extracting a shared module reused by both servers, e.g., `ckan-docker/mcp-common/ckan_client.py`, or dso-geo embeds a thin CKAN client mirroring dso-ckan's CKANClient — decision pending). Outputs are registered by the pipeline task, not by the MODEL.

## Files Likely Affected

### Phase 0 Deliverables (GDAL Image + Proof-of-Life)

**Phase-0a: Proof-of-Life Experiment (HARD GO/NO-GO GATE)**
   - Register a minimal hello-world container (NOT the full GDAL image yet) as a Tapis Abaco Actor on the actual `portals.tapis.io` tenant
   - Execute it and verify:
     1. Execution status is queryable and pollable via Tapis Actor API
     2. Container can READ a file from a CKAN resource download URL using GDAL's `/vsicurl/` driver
     3. Container can WRITE to an output location (Corral-backed storage)
   - Confirm:
     - (a) The allocation/team has permission to register a Tapis Abaco app (check with TACC)
     - (b) Abaco/Actors is available on this tenant (Abaco is unverified and deprecated; repo uses OAuth2 endpoints — Abaco-specific endpoints unknown)
     - (c) HTTP-triggered actor chaining works (for pipeline downstream tasks)
   - **Decision point**: If proof-of-life FAILS, the entire architecture must be reconsidered (may require switching to a different compute service).
   - **Estimated duration**: 1 week (including feedback loop with TACC)
   - **Deliverable**: Written confirmation of viability OR escalation to architect for redesign

**Phase-0b: GDAL Actor Image Build + Pipeline Registration**

1. `ckan-docker/gdal-image/` (new directory, separate artifact)
   - `Dockerfile` — GDAL + Python 3.10+ + validated entrypoint (NO shell passthrough, NO /bin/sh -c)
   - `entrypoint.py` — Python-based (not shell) command dispatcher; whitelisted GDAL ops only (gdalinfo, gdalwarp, gdal_translate, gdaladdo); builds subprocess args as list[str], subprocess.run(args, shell=False); rejects GDAL subcommands outside the whitelist
   - `url_validator.py` — Validate CKAN resource download URLs (SSRF control: must point at configured CKAN host); auth header preparation for private resources (X-Tapis-Token via GDAL_HTTP_HEADER)
   - `param_validator.py` — Validate all GDAL parameters (see GDAL Injection section below)
   - `register_to_ckan.py` — Downstream task script: HTTP POST to CKAN resource_create with provenance; handles auth and error recovery
   - `README.md` — Build instructions, Abaco registration, Phase-0a proof-of-life requirement, /vsicurl/ auth notes
   - `.dockerignore`

2. `ckan-docker/gdal-image/tapis-actor-definition.json` or `.yaml` — Register image as Tapis Abaco Actor (AFTER proof-of-life succeeds)
   - Specifies docker image URI, actor name, default environment (CKAN_URL, GDAL ops whitelist, etc.)
   - No mounted filesystems (all I/O via HTTP URLs)
   - Service-account identity: read-only access to CKAN, write access to register resources
   - Referenced in Phase 1+ tool implementations

3. `ckan-docker/gdal-image/pipeline-definition.json` — Register the actor pipeline (GDAL stage → Register stage)
   - Stage 1: GDAL Abaco actor (reads source via `/vsicurl/`, executes transform, writes to Corral-backed output storage)
   - Stage 2: Downstream HTTP-triggered task (calls CKAN resource_create to register output)
   - Defines message passing between stages (output path, provenance metadata)

### Phase 1+: dso-geo MCP Server

1. `ckan-docker/mcp-geo/` (new directory; sibling to `mcp-server`)
   - `__init__.py`
   - `server.py` — FastMCP entrypoint
   - `tools/` — Tool implementations
     - `metadata.py` — `gdalinfo_extract`, `gdalinfo_summary` (NO get_execution_status)
     - `transform.py` — `reproject_raster`, `convert_to_cog`, `clip_raster`, `build_overviews` (NO get_execution_status)
     - `status.py` — `get_execution_status()` (registered ONCE; used by all tools; type-tagged execution ID dispatch)
   - `ckan_client.py` — CKAN API client (resource_show, package_show, resource_create); built-in or extracted from `mcp-server` (decision: shared module vs embedded thin client — recommend shared extraction at `ckan-docker/mcp-common/ckan_client.py`)
   - `tapis_client.py` — Tapis Abaco actor API client (submit execution, poll status)
   - `tapis_middleware.py` — Token middleware: scrubs Bearer tokens and eyJ... patterns from all Tapis API error responses + logs before propagation.
   - `config.py` — Configuration (Tapis base URL, CKAN API base URL, Actor ID, timeouts, cost caps, allowed ops)
   - `audit.py` — Audit logging (execution IDs, provenance, token scrubbing); records Tapis user identity from JWT claims (decoded WITHOUT logging raw token); env fallback warning at startup if GEO_TAPIS_TOKEN is set in production
   - `url_validator.py` — SSRF validation: ensure resolved CKAN resource URLs point at configured CKAN host

2. `ckan-docker/mcp-geo/requirements.txt` — FastMCP, Tapis SDK, requests, no GDAL dependency (all GDAL runs remote)

3. `ckan-docker/mcp-common/` (new directory, optional shared module)
   - `ckan_client.py` — Shared CKAN API client (used by both `mcp-server` and `mcp-geo`); encapsulates resource_show, package_show, resource_create, package_create, etc.
   - `__init__.py`
   - (Alternatively: `mcp-geo` embeds a thin CKAN client mirroring `mcp-server`'s implementation — **Decision pending**: shared module vs embedded?)

4. `.env.dev.secrets` and `.env.prod.secrets`: Add configuration variables
   - `GEO_TAPIS_TOKEN` (optional env fallback; per-call preferred)
   - `GEO_TAPIS_BASE_URL` (e.g., `https://tapis.tacc.utexas.edu`)
   - `GEO_GDAL_ACTOR_ID` (from Phase 0 registration, e.g., `dso-gdal-v1`)
   - `GEO_CKAN_URL` (e.g., `http://localhost:5001` or production URL)
   - `GEO_MAX_EXECUTION_TIME_SECONDS` (per-op timeout, e.g., 600)
   - `GEO_MAX_CONCURRENT_EXECUTIONS` (runaway control, e.g., 5)

5. `docs/design/2026-06-29-geo-actor-mcp-server.md` — This spec

**Existing files referenced**:

- `ckan-docker/mcp-server/src/dso_ckan_mcp/ckan_client.py` — Candidate for extraction as shared module; or dso-geo implements its own thin client
- Abaco actor pipeline definition — Defined in Phase 0b; referenced by dso-geo for execution dispatch

## API/Schema Changes

**New MCP Tool Signatures** (Python/FastMCP):

Metadata extraction (v1, resource-id-based):
```python
@mcp.tool()
def gdalinfo_extract(
    resource_id: str,
    include_stats: bool = True,
    tapis_token: str | None = None
) -> dict:
    """Extract GDAL metadata from a raster via gdalinfo (Tapis Abaco Actor).
    
    Args:
        resource_id: CKAN resource UUID (e.g., 'abc123def456').
                     dso-geo resolves to CKAN resource record, extracts download URL, validates SSRF.
        include_stats: Whether to compute band statistics (slower).
        tapis_token: Tapis JWT for CKAN + Abaco APIs (optional env fallback).
    
    Returns: {"execution_id": "actor:<id>", "status": "SUBMITTED"}
    Poll with get_execution_status(execution_id) to retrieve metadata dict.
    """

@mcp.tool()
def gdalinfo_summary(
    dataset_id: str,
    tapis_token: str | None = None
) -> dict:
    """Extract metadata from all rasters in a CKAN dataset.
    
    Args:
        dataset_id: CKAN dataset ID (e.g., 'twdb-ntgam').
                    dso-geo resolves to package, lists resources, max 10 (concurrency cap).
        tapis_token: Tapis JWT for CKAN + Abaco APIs.
    
    Returns: {"execution_id": "actor:<id>", "status": "SUBMITTED"}
    Poll with get_execution_status(execution_id) to retrieve list of metadata dicts (one per resource).
    Handles per-resource success/failure.
    """
```

Transformations (v1, resource-id-based, no dry-run, pipeline-based):
```python
@mcp.tool()
def reproject_raster(
    resource_id: str,
    target_crs: int,
    output_name: str,
    register_to_dataset: str | None = None,
    tapis_token: str | None = None
) -> dict:
    """Reproject a raster via gdalwarp (Tapis Abaco actor pipeline).
    
    Args:
        resource_id: CKAN resource UUID; dso-geo resolves to download URL (SSRF-validated).
        target_crs: EPSG code as integer (e.g., 4326); server validates range (1–999999) and builds ["-t_srs", "EPSG:4326"].
        output_name: Bare filename (regex ^[A-Za-z0-9_\\-.]+$); no slashes, .., metacharacters.
        register_to_dataset: CKAN dataset ID to register output (optional; defaults to source resource's dataset).
        tapis_token: Tapis JWT for CKAN + Abaco APIs.
    
    Returns: {"execution_id": "actor:<id>", "status": "SUBMITTED"}
    Pipeline: [GDAL stage (reads via /vsicurl/, reproject, write) → Register stage (CKAN resource_create with provenance)]
    Poll with get_execution_status(execution_id) for pipeline progress and final resource ID.
    """

@mcp.tool()
def convert_to_cog(
    resource_id: str,
    output_name: str,
    compression: str = "deflate",
    register_to_dataset: str | None = None,
    tapis_token: str | None = None
) -> dict:
    """Convert raster to Cloud-Optimized GeoTIFF via gdal_translate (Tapis Abaco actor pipeline).
    
    Args:
        resource_id: CKAN resource UUID; dso-geo resolves to download URL (SSRF-validated).
        output_name: Bare filename; server prepends output prefix.
        compression: Enum (deflate, lzw, zstd, none).
        register_to_dataset: CKAN dataset ID for output (optional).
        tapis_token: Tapis JWT.
    """

@mcp.tool()
def clip_raster(
    resource_id: str,
    clip_geometry: dict,
    output_name: str,
    register_to_dataset: str | None = None,
    tapis_token: str | None = None
) -> dict:
    """Clip raster to geometry via gdalwarp -cutline (Tapis Abaco actor pipeline).
    
    Args:
        resource_id: CKAN resource UUID; dso-geo resolves to download URL (SSRF-validated).
        clip_geometry: GeoJSON dict ONLY (NO string); geometry type Polygon/MultiPolygon only; WGS84 bounds; max ~1000 vertices.
                       Abaco actor serializes to temp file for gdalwarp.
        output_name: Bare filename.
        register_to_dataset: CKAN dataset ID for output (optional).
        tapis_token: Tapis JWT.
    """

@mcp.tool()
def build_overviews(
    resource_id: str,
    output_name: str,
    overview_levels: list[int] = [2, 4, 8],
    register_to_dataset: str | None = None,
    tapis_token: str | None = None
) -> dict:
    """Build overviews on a COPY of a raster via gdaladdo (Tapis Abaco actor pipeline).
    
    Args:
        resource_id: CKAN resource UUID; dso-geo resolves to download URL (never mutated).
        output_name: Bare filename for output (e.g., 'rainfall_with_overviews.tif').
        overview_levels: List of ints, each 2–512, max 10 elements (e.g., [2, 4, 8, 16]).
        register_to_dataset: CKAN dataset ID for output (optional).
        tapis_token: Tapis JWT.
    
    Implementation: Abaco actor downloads source via /vsicurl/, COPIES to working location, runs gdaladdo on copy.
    Source never mutated (read-only guarantee). Output uploaded + registered as new CKAN resource via pipeline.
    """
```

Status polling (v1):
```python
@mcp.tool()
def get_execution_status(
    execution_id: str,
    tapis_token: str | None = None
) -> dict:
    """Poll status of a Tapis Abaco actor execution or pipeline.
    
    Returns: {
        "execution_id": "actor:<id>",
        "status": "SUBMITTED|RUNNING|COMPLETED|FAILED",
        "progress": 0–100,
        "result": {  # Only if COMPLETED
            "resource_id": "new-resource-uuid",  # For transforms; none for metadata
            "ckan_url": "http://ckan/dataset/.../resource/...",
            "metadata": {...},  # For metadata tools
            "provenance": {
                "source_resource_id": "abc123def456",
                "source_ckan_url": "http://ckan/dataset/.../resource/abc123",
                "gdal_command": "gdalwarp -t_srs EPSG:4326 ...",
                "gdal_version": "3.8.0",
                "execution_id": "actor:xyz789",
                "dso_geo_version": "v1.0",
                "submitted_by": "wm7972",
                "timestamp": "2026-06-30T14:32:10Z",
                "duration_seconds": 45.2,
                "output_size_bytes": 25698304
            }
        },
        "error": "..."  # If FAILED
    }
    """
```

**CKAN Client Included**: dso-geo imports or embeds a CKAN client (similar to dso-ckan's CKANClient) to resolve resource/dataset IDs and call resource_show/package_show. **Decision pending**: Extract a shared module (`ckan-docker/mcp-common/ckan_client.py`) reused by both servers, or embed a thin client in dso-geo mirroring dso-ckan's implementation.

**Provenance Schema** (returned in `get_execution_status(...).result.provenance`, captured by Abaco pipeline and passed to CKAN resource_create):
- `source_resource_id` (str): CKAN resource UUID (not CKAN resource ID slug, the UUID)
- `source_ckan_url` (str): Full CKAN resource download URL (for auditability and reproducibility)
- `gdal_command` (str): Exact GDAL command executed (for reproducibility)
- `gdal_version` (str): GDAL version from actor image label (e.g., "3.8.0")
- `execution_id` (str): Tapis Abaco actor execution ID (e.g., "actor:abc123def456")
- `dso_geo_version` (str): dso-geo tool version (e.g., "v1.0")
- `submitted_by` (str): Tapis user identity from JWT claims (decoded WITHOUT logging raw token); for audit trail
- `timestamp` (ISO 8601 str): When execution started (UTC)
- (optional) `duration_seconds` (float): Execution duration (from Abaco metrics)
- (optional) `output_size_bytes` (int): Output file size (from pipeline result)

## Data Flow

**Scenario 1: Extract metadata from a GeoTIFF raster**

1. **User** → Claude Code: "Get metadata for the rainfall GeoTIFF in the TWDB NTGAM dataset"
2. **Claude Code** calls dso-geo: `gdalinfo_extract(resource_id="abc123def456", include_stats=True, tapis_token="eyJ0eXAi...")`
3. **dso-geo**:
   - Calls CKAN: `resource_show(id="abc123def456")` → receives resource record with `download_url`
   - Validates download URL (SSRF control: must point at configured CKAN host)
   - Submits Tapis Abaco actor: "read `/vsicurl/<download_url>` via gdalinfo --json"
   - Returns execution ID **immediately** (non-blocking)
4. **Claude Code** polls: `get_execution_status(execution_id="actor-xyz", tapis_token=...)`
   - Repeats until status = COMPLETED
5. **Returns**: metadata dict with CRS, bands, extent, nodata, overviews, stats, execution audit

**Scenario 2: Extract metadata from all rasters in a dataset**

1. **User** → Claude Code: "Show me metadata for all rasters in the TWDB NTGAM dataset"
2. **Claude Code** calls dso-geo: `gdalinfo_summary(dataset_id="twdb-ntgam", tapis_token=...)`
3. **dso-geo**:
   - Calls CKAN: `package_show(id="twdb-ntgam")` → receives package with resource list
   - Filters to raster resources (max 10; enforces concurrency cap)
   - For each resource, resolves download_url and validates SSRF
   - Submits Tapis Abaco actor: "read all `/vsicurl/<urls>` via gdalinfo --json (list)"
   - Returns execution ID **immediately**
4. **Claude Code** polls: `get_execution_status(execution_id="actor-xyz", tapis_token=...)`
   - Repeats until status = COMPLETED
5. **Returns**: list of metadata dicts (one per resource) + per-resource success/failure flags

**Scenario 3: Reproject a raster and auto-register in CKAN** (Async Submit+Poll, Pipeline-Driven)

1. **User** → Claude Code: "Reproject the TWDB rainfall raster to EPSG:4326"

2. **Direct submission** (NO dry-run):
   - Claude Code calls: `reproject_raster(resource_id="rainfall_uuid", target_crs=4326, output_name="rainfall_epsg4326.tif", tapis_token=...)`
   - dso-geo:
     - Calls CKAN: `resource_show(id="rainfall_uuid")` → receives resource record with `download_url` + dataset context
     - Validates resource_id, download_url (SSRF), target_crs (range 1–999999), output_name (regex)
     - Before submitting: queries Tapis to check in-flight Abaco executions of this actor; rejects if >= concurrency cap (default 5)
     - Submits Tapis Abaco **actor pipeline**:
       - **Stage 1 (GDAL actor)**: reads source via `/vsicurl/<download_url>` → executes gdalwarp → writes output to Corral-backed storage path (e.g., `/data/ckan/storage/output_<uuid>.tif`)
       - **Stage 2 (Register task)**: HTTP POST to CKAN resource_create API with: dataset_id (source dataset), resource name, url (storage path), provenance metadata (source_resource_id, gdal_command, submitted_by, etc.)
   - Returns execution ID immediately (non-blocking): `{"execution_id": "actor-xy123", "status": "SUBMITTED"}`

3. **Polling** (loop until complete):
   - Claude Code repeatedly calls: `get_execution_status(execution_id="actor-xy123", tapis_token=...)`
   - Returns status updates: "SUBMITTED", "RUNNING (stage 1: GDAL executing...)", "RUNNING (stage 2: registering to CKAN...)", then "COMPLETED"

4. **Once done** (status = COMPLETED):
   - `get_execution_status(execution_id="actor-xy123")` returns result dict:
   ```json
   {
     "execution_id": "actor-xy123",
     "status": "COMPLETED",
     "result": {
       "resource_id": "new-resource-uuid",
       "ckan_url": "http://ckan/dataset/twdb-ntgam/resource/new-resource-uuid",
       "provenance": {
         "source_resource_id": "rainfall_uuid",
         "source_ckan_url": "http://ckan/dataset/twdb-ntgam/resource/rainfall_uuid/download/rainfall.tif",
         "gdal_command": "gdalwarp -t_srs EPSG:4326 /vsicurl/http://ckan/... /data/ckan/storage/output_<uuid>.tif",
         "gdal_version": "3.8.0",
         "execution_id": "actor-xy123",
         "dso_geo_version": "v1.0",
         "submitted_by": "wm7972",
         "timestamp": "2026-06-30T14:32:10Z",
         "duration_seconds": 45.2,
         "output_size_bytes": 25698304
       }
     }
   }
   ```
   - **Pipeline automatically registered** the output as a new CKAN resource in the source dataset (twdb-ntgam)

5. **Outcome**:
   - No additional MODEL steps needed; output is discoverable in CKAN immediately
   - Provenance is embedded in the CKAN resource (custom fields)
   - Source resource never mutated; output is a new resource
   - All operations logged and audited

**Key Differences from Prior Design**:
- **No dry-run**: Transforms execute on submission (trade-off: single heavier action, but non-destructive)
- **No MODEL orchestration of CKAN registration**: Pipeline task handles it automatically
- **Resource ID-based, not corral_path-based**: dso-geo resolves IDs → URLs internally
- **SSRF control**: Resolved URLs must point at configured CKAN host
- **Auth for private resources**: `/vsicurl/` reads carry X-Tapis-Token header if needed (note in pipeline design)

## Risks and Tradeoffs

1. **Security: Tapis Token Scope & Exposure**
   - *Risk*: Tapis JWT passed per-call as tool argument; travels through MCP client context
   - *Mitigation*: Short-lived JWTs (~hours); token scrubbed from logs/errors; optional env fallback; same model as CKAN server
   - *Acceptance*: Tapis tokens inherently risky via HTTP; v1 is stdio/local; mitigated by no persistent storage of tokens

2. **GDAL Command Injection via Transformations** (SPECIFIED IN DETAIL, 2026-06-30 REVISION)
   - *Risk*: Malicious GDAL command args could escape validation and execute arbitrary code in container
   - *Mitigation*: 
     - Fixed entrypoint (Python, not shell; no `/bin/sh -c`; subprocess.run(args, shell=False))
     - Whitelist specific GDAL operations ONLY: `gdalinfo`, `gdalwarp`, `gdal_translate`, `gdaladdo`. Reject any GDAL subcommand outside this list.
     - Validation contract (MCP layer + Abaco actor entrypoint, defense in depth):
       - `target_crs`: Accept ONLY integer range 1–999999. Server builds flag as `["-t_srs", f"EPSG:{n}"]` from integer only; never forward raw string.
       - `output_name`: Bare filename only, regex `^[A-Za-z0-9_\-.]+$`, no slashes, `..`, null, metacharacters. Server prepends output prefix; caller cannot escape.
       - `compression`: Enum allowlist only {deflate, lzw, zstd, none}.
       - `overview_levels`: List of integers, each 2–512, max 10 elements.
       - `clip_geometry`: GeoJSON dict ONLY (no string form); geometry type Polygon/MultiPolygon only; coords within WGS84 bounds; max ~1000 vertices. Abaco actor serializes to temp file.
       - `resource_id`: Validate UUID format BEFORE passing to CKAN API
       - `execution_id` (in `get_execution_status()`): Validate against known Tapis Actor ID format regex BEFORE interpolating into any Tapis API URL (prevent path traversal).
     - Entrypoint: builds args as list[str], never string interpolation; subprocess.run(args, shell=False) ALWAYS.
     - Reject stdin, user-supplied scripts, environment variable expansion in parameters
   - *Acceptance*: Phase 0 image build must enforce this strictly; fuzz-test injection payloads (e.g., `; rm -rf /`, `$(whoami)`, `/etc/passwd`, etc.)

3. **Server-Side Request Forgery (SSRF) via CKAN URL Resolution** (NEW, 2026-06-30)
   - *Risk*: dso-geo resolves resource IDs to CKAN-provided URLs; a compromised CKAN could return attacker-controlled URLs, exposing internal networks via `/vsicurl/`
   - *Mitigation*: 
     - After resolving resource_id → CKAN resource record, validate that the `download_url` field points at the configured CKAN host (same host as GEO_CKAN_URL env var)
     - Reject URLs pointing to internal networks (RFC 1918, 169.254.x.x, 127.x.x.x, localhost, etc.)
     - Whitelist HTTPS + authenticated HTTP only; reject file://, ftp://, gopher://, etc.
     - Log all resolved URLs (scrubbed of tokens) for audit trail
   - *Acceptance*: Design decision; enforced at dso-geo MCP layer before passing URL to Abaco actor

4. **Auth for Private/Non-Public Resources via /vsicurl/** (NEW, 2026-06-30)
   - *Risk*: If a CKAN resource is non-public, its download URL may not be directly accessible; /vsicurl/ read would fail silently or with 403
   - *Mitigation*: 
     - For private resources, the Abaco actor must carry auth credentials (Tapis JWT) in the `/vsicurl/` request
     - Abaco actor entrypoint adds X-Tapis-Token header to GDAL_HTTP_HEADER environment variable for /vsicurl/ reads
     - dso-geo passes the JWT to the actor execution as part of the submission payload (environment variable, not in URL)
     - Ensure token is scrubbed from Abaco logs and responses
   - *Acceptance*: Implementation note; requires careful token handling in actor script

5. **No Dry-Run Preview for Transforms** (TRADE-OFF, 2026-06-30)
   - *Risk*: Removing dry-run means transforms are executed immediately on submission; no opportunity to review parameters before resources are created
   - *Mitigation*: 
     - Transforms are non-destructive (new resources, source never mutated)
     - Parameter validation is strict (regex, enum, range checks) before submission
     - All operations authenticated + audited
     - User can review parameters by examining the tool call before approval in Claude Code
   - *Acceptance*: Design trade-off per user decision; moves gate from "preview + approve" to "parameter validation + audit trail"

6. **Compute Cost & Runaway Jobs** (COST CONTROLS = PHASE 2, BEFORE TRANSFORM GA)
   - *Risk*: Submitted actor could run indefinitely or consume unbounded resources; runaway/cascading submissions could burn SUs/quota
   - *Mitigation*: 
     - **Per-operation timeout**: Enforced at Tapis actor app definition level. Default ~600 sec (configurable per tool type, e.g., metadata 30 sec, transforms 600 sec). Tapis cancels execution if exceeded.
     - **Concurrent execution cap**: dso-geo MUST query Tapis for in-flight executions of this actor BEFORE each submission and refuse if >= cap (default 5 concurrent). Enforced via Tapis API query, not local counter (dso-geo is stateless stdio process). `gdalinfo_summary()` caps concurrent submissions per call (≤ 10).
     - **Audit logging**: All submissions logged with execution ID, user identity, timestamp, resource/dataset sizes, cost estimate (future).
     - Monitor HPC quota; implement quota alerts (future).
   - *Acceptance*: Phase 2 (cost controls) deployment BLOCKED until Phase 2 is complete. Phase 3 (transform GA) requires documented timeout defaults + concurrency cap + audit trail.

7. **Async Model Requires Polling**
   - *Risk*: Client must manage state and poll for results; adds complexity
   - *Mitigation*: Claude Code can poll implicitly in a loop; `get_execution_status()` is idempotent
   - *Acceptance*: Necessary tradeoff for supporting operations without blocking MCP

8. **No Destructive Operations on Source**
   - *Risk*: Could overwrite or delete source data on Corral (especially `gdaladdo`, which defaults to in-place modification)
   - *Mitigation*: 
     - `build_overviews()` downloads source via /vsicurl/, creates a working copy, then builds overviews on the copy. Source NEVER modified in place.
     - All other transformations read source, compute, write to new resource. Source files remain untouched (read-only semantics).
     - Output is always a new CKAN resource (registered by pipeline task)
   - *Acceptance*: Design decision; enforced at Abaco actor + pipeline level

9. **Tapis Token Scope & Full-User Tokens** (2026-06-30)
   - *Risk*: Tapis password-grant JWT passed per-call is a FULL USER token (broad scope across Tapis Abaco/Files/Apps). Token travels through MCP client context and could be exposed in error messages / logs.
   - *Mitigation*: 
     - Short-lived JWTs (~hours); rotate frequently
     - Token middleware (tapis_middleware.py) scrubs `Bearer <...>` and `eyJ...` patterns from ALL Tapis API error responses + logs BEFORE propagation. NOT just local log formatting; applies to all response bodies.
     - Audit records the Tapis user identity from JWT claims (decoded WITHOUT logging the raw token), never the token itself
     - Env fallback `GEO_TAPIS_TOKEN`: emit prominent startup WARNING (and ideally REFUSE to start) if set when targeting production
     - Do NOT replicate get-jwt.sh's unsafe JSON-by-string-interpolation; use a real JSON serializer (e.g., `json` module)
   - *Acceptance*: v1 known accepted risk; explicit roadmap note to move to Tapis scoped service tokens (narrow OAuth2 scopes) for compute dispatch in v2+

10. **Pipeline Registration Failure: Orphaned Outputs** (2026-06-30)
   - *Risk*: If Abaco actor (GDAL stage) succeeds but downstream registration task (CKAN stage) fails, output file exists on Corral but is not registered as a CKAN resource
   - *Mitigation*: 
     - Use deterministic output naming (not timestamp-only) so a failed registration can be retried idempotently with the same output_name
     - Audit logs track execution ID, output file path, and registration attempt
     - (Future, stronger): Add a `list_outputs()` tool to find orphaned outputs on Corral for manual recovery/registration
   - *Acceptance*: Design decision; idempotent naming enables retry without duplicating outputs; orphaned recovery via audit logs is acceptable in v1

## Alternatives Considered

1. **Run GDAL locally in the MCP server process**
   - *Rejected*: 30–50 MB files in memory; blocks MCP on slow ops; no HPC scheduling; security isolation

2. **Use POSIX mount for Corral access (no /vsicurl/)**
   - *Rejected (2026-06-30)*: Requires container-level bind mounts; tight coupling to Corral; HTTP range reads via `/vsicurl/` are simpler, more portable, and enable auth

3. **Use Tapis Files API to push/pull bytes (HTTP data transfer)**
   - *Rejected*: Bandwidth bottleneck; defeats purpose of remote compute; defeats entire value proposition

4. **Bundle dso-geo into the CKAN MCP server (single process)**
   - *Rejected*: Different deps (Tapis SDK, GDAL); different auth scope; bloats CKAN server; harder to test/maintain; violates separation of concerns

5. **Use Tapis Jobs instead of Actors for all operations**
   - *Rejected (2026-06-30)*: Jobs have longer startup (~10 sec); Actors are sufficient for all v1 ops; simpler architecture with single execution type

6. **Synchronous submit-and-wait model**
   - *Rejected*: Blocks MCP on long-running ops; poor UX; timeout risk; cannot express "wait for X in background"

7. **dso-geo calls CKAN API indirectly via dso-ckan MCP server (composition via MCP)**
   - *Rejected*: MCP composition across tool servers is complex; direct API calls are simpler and match CKAN server's pattern

8. **dso-geo returns output paths; MODEL orchestrates CKAN registration (loose coupling)**
   - *Rejected (2026-06-30, reversed)*: Original design; replaced by tight pipeline integration where Abaco pipeline stage handles registration automatically. Simpler UX, automatic provenance capture.

9. **Dry-run preview for transforms (local validation)**
   - *Rejected (2026-06-30)*: User decision to remove dry-run; trades off "preview before commit" for simpler UX + guaranteed non-destructive operations + strict parameter validation

10. **Cloud storage (S3, Azure Blob) instead of Corral for outputs**
    - *Rejected for v1*: Corral is the existing infrastructure; adds cost; integration with CKAN is simpler via Corral mount

## Test Plan

**Unit Tests**:
- Mock CKAN API responses (resource_show, package_show)
- Mock Tapis Abaco API responses (submit, poll, get status)
- Test resource ID → CKAN URL resolution
- Test SSRF validation (URL host matching)
- Test input validation (resource_id UUID format, CRS range, output_name regex, clip_geometry GeoJSON)
- Test token scrubbing in error messages
- Test UUID format validation before API interpolation

**Integration Tests**:
- Test against real Tapis Abaco (if available)
- Test `gdalinfo_extract` with real raster resource on CKAN/Corral
- Test `gdalinfo_summary` with multiple resources
- Test `reproject_raster` pipeline (GDAL + register stages)
- Test `get_execution_status` polling with real actor execution
- Verify pipeline automatically registers CKAN resource with provenance
- Verify audit logs record execution IDs + user identity (not token)

**Security Tests**:
- Verify SSRF rejection (URLs pointing to internal networks)
- Verify resource_id UUID format validation
- Verify GDAL injection payloads rejected (`;`, `$()`, backticks, etc.)
- Verify token not logged at startup
- Verify Bearer + eyJ patterns scrubbed from Tapis error responses
- Verify /vsicurl/ auth header set for private resources
- Test CKAN resource URL validation against configured host

## Documentation Plan

**Code Comments**: Docstrings for each tool, Tapis client, config, audit

**README** (ckan-docker/mcp-geo/README.md):
- Quick start, tool reference, configuration (env vars), architecture, security, limitations

**User-Facing Docs** (docs/mcp/geo-mcp-server.md):
- What is geo MCP server, why remote compute, setup, example workflows, approval gate, polling explanation, FAQ

**Runbook**: Local deployment, token setup, Actor/Job registration, env config, error monitoring

## Rollout/Rollback Plan (REORDERED, 2026-06-30)

**Phase 0: Proof-of-Life Experiment + GDAL Image** (CRITICAL PATH, HARD GO/NO-GO — must complete before Phases 1+)

*Phase 0a: Proof-of-Life Gate (1 week)*
- Register a minimal hello-world Abaco actor (NOT the full GDAL image yet) on `portals.tapis.io`
- Execute it and verify:
  - Execution status is queryable/pollable via Tapis Abaco API (new submission, poll, get result)
  - Actor can READ from a CKAN resource URL using GDAL's `/vsicurl/` driver (HTTP range reads, no POSIX mount)
  - Actor can WRITE to output storage (Corral-backed location)
  - HTTP-triggered downstream task works (for pipeline registration stage)
- Confirm with TACC:
  - (a) Allocation/team has permission to register a Tapis Abaco app
  - (b) Abaco/Actors is available on this tenant (unverified; repo OAuth2 endpoints only; Abaco deprecated)
  - (c) Actor chaining / HTTP-triggered task invocation is supported
- **Decision point**: FAIL = architecture reconsidered (escalate to architect); PASS = proceed to Phase 0b
- Duration: ~1 week (includes TACC feedback loop, API endpoint discovery, auth token setup)
- Deliverable: Written GO/NO-GO confirmation; confirmed Abaco actor endpoint + permissions

*Phase 0b: GDAL Actor Image Build + Pipeline Definition (1–2 weeks)*
- Build Dockerfile with osgeo/gdal, Python 3.10+, Python-based (not shell) fixed entrypoint, param validator, URL validator (SSRF), /vsicurl/ auth handler
- Build `register_to_ckan.py` downstream task script: HTTP POST to CKAN resource_create with provenance metadata
- Test locally: gdalinfo, gdalwarp, gdal_translate, gdaladdo with validated args; fuzz-test injection payloads
- Register as Tapis Abaco actor app via actor definition (AFTER Phase 0a passes)
- Define actor pipeline (Stage 1: GDAL actor → Stage 2: HTTP-triggered register task) via pipeline definition
- Test pipeline end-to-end with mock CKAN or dev CKAN if available
- Verify /vsicurl/ auth header propagation for private resources
- Deliverable: Published GDAL Actor ID in `.env` config; pipeline definition; documented in Phase 0b README
- Duration: 1–2 weeks (image build + Tapis registration + pipeline definition + local/integration testing)
- **Blocker for Phases 1–3**: No other phase can proceed until Phase 0a PASSES and Phase 0b is COMPLETE

**Phase 1: Metadata Extraction (v1, Low-Risk)**
- Implement: `gdalinfo_extract(resource_id, include_stats, tapis_token)`, `gdalinfo_summary(dataset_id, tapis_token)`, `get_execution_status(execution_id, tapis_token)` in tools/metadata.py and tools/status.py
- Implement: CKAN client (resource_show, package_show) — either extract shared module from mcp-server or embed thin client
- Validation: resource_id UUID format, dataset_id validation, max resource list (10), SSRF on resolved URLs, partial-failure handling
- Tests: mock CKAN + Tapis Abaco; real Actor integration test with CKAN resources; token scrubbing verification; SSRF rejection; resource resolution
- Launch: dso-geo MCP server accessible; metadata tools available; pipeline status polling confirmed
- Duration: 1 week
- **Dependencies**: Phase 0a + Phase 0b complete
- **No blocking risk to Phases 2–3**: metadata tools are independent

**Phase 2: Cost Controls & Runaway Protection** (PREREQUISITE FOR PHASE 3, before GA)
- Implement: per-operation timeout at Tapis app level (config-driven); concurrency cap via Tapis in-flight query (before each submission)
- Config: default timeouts (metadata: 30 sec, transforms: 600 sec), cap value (default 5), query interval
- Tests: verify timeout enforcement (Tapis cancels execution); verify concurrency cap blocks submissions at cap
- Audit: all submissions logged with execution ID, user identity, timestamp, input/output sizes
- Launch: cost control config ready for Phase 3 deployment
- Duration: 1 week
- **Dependencies**: Phase 0 + Phase 1 complete
- **Blocker for Phase 3 GA**: Phase 2 tests must pass; cost controls must be documented

**Phase 3: Gated Transformations (v1, Higher-Risk)** (AFTER Phase 2 complete)
- Implement: `reproject_raster(resource_id, target_crs, output_name, register_to_dataset, tapis_token)`, `convert_to_cog(...)`, `clip_raster(...)`, `build_overviews(...)` in tools/transform.py
- Pipeline dispatch: concurrency cap check (Phase 2), resolve resource_id → CKAN URL (SSRF validated), submit Abaco actor pipeline (GDAL stage + Register stage)
- build_overviews: Actor downloads source via /vsicurl/, COPIES to working location, runs gdaladdo on copy (never in-place)
- GDAL injection validation: enforce exact contract (target_crs int 1–999999, output_name regex, compression enum, clip_geometry GeoJSON dict-only, etc.)
- Pipeline provenance: GDAL stage constructs provenance dict (source_resource_id, gdal_command, submitted_by from JWT, timestamp, etc.); Register stage embeds it in CKAN resource metadata
- Tests: mock CKAN + Tapis Abaco pipeline; real pipeline integration test end-to-end (GDAL + register); command injection fuzz tests (semicolon, $(), backtick, etc.); SSRF rejection tests; resource ID validation; /vsicurl/ auth header tests
- Launch: transformation tools available; all authenticated + audited; non-destructive (new resources only); source read-only
- Duration: 2–3 weeks
- **Dependencies**: Phase 0 + Phase 1 + Phase 2 complete and tested
- **Blocking gate**: Phase 2 cost controls MUST be live before Phase 3 tools go GA

**Phase 3+ Future Enhancements** (Post-v1.0, not blocking GA):
- Quota monitoring and alerts
- Cost estimation + user warnings before submission
- Deterministic output naming + optional list_outputs() reconciliation tool
- Tapis scoped service tokens (v2+ security improvement)

**Rollback**:
- dso-geo is stateless; no persistent data on server
- Source data on Corral remains untouched (transformations write to separate dir)
- Outputs written to `/data/geo-outputs/` are orphaned but recoverable
- Rollback procedure: Stop dso-geo container; outputs remain on Corral; audit logs preserved
- No database mutations; no cascading failures to CKAN

**Deployment**:
- Docker compose addition: new service `dso-geo` (sibling to `mcp-server`)
- Environment variables: inject Phase 0 image config (GDAL Actor/Job IDs) from `.env.secrets`
- No migrations; no downtime to existing services

## Open Questions (REVISED 2026-06-30 — MAJOR USER-DIRECTED CHANGES APPLIED)

**Phase 0a: Proof-of-Life Experiment** (BLOCKING GATE — MUST BE RESOLVED BEFORE CODE):

1. **Tapis Abaco Viability**: Can we register and execute an Abaco actor on `portals.tapis.io`?
   - Is Abaco/Actors available on this tenant? (Abaco is unverified and deprecated; repo has OAuth2 endpoints only)
   - Does the allocation/team have permission to register Tapis Abaco apps?
   - Can Abaco actors read from CKAN resource URLs via GDAL's `/vsicurl/` (HTTP range reads)?
   - Can HTTP-triggered downstream tasks be chained for pipelines?
   - *Outcome needed*: Written PASS/FAIL confirmation from Phase 0a experiment (1 week, TACC-dependent); if FAIL, architecture must be reconsidered (escalate to architect)

**Phase 0b: GDAL Abaco Actor Image & Pipeline** (DECIDED: Abaco Actors only; open details):

2. **Base Docker Image**: Which base image for the GDAL actor?
   - `osgeo/gdal:latest` (full-featured, 2+ GB, latest GDAL version)
   - `osgeo/gdal:slim` (minimal, ~500 MB, reduced features)
   - `ubuntu:22.04 + apt install gdal` (custom, control over version/size/deps)
   - *User choice needed*: balance between features, size, startup time, maintenance

3. **CKAN Client Strategy**: Shared module or embedded in dso-geo?
   - Extract `ckan-docker/mcp-common/ckan_client.py` (shared by mcp-server + mcp-geo)
   - Embed a thin CKAN client in dso-geo mirroring dso-ckan's implementation
   - *Recommendation*: Shared module extraction (reduces duplication, easier maintenance). **Decision pending**: User confirmation on module organization.

4. **Pipeline Definition & Downstream Task**: How to implement the register-to-CKAN stage?
   - Single Abaco actor with dual entry points (GDAL op + register task)?
   - Separate actors (GDAL actor → HTTP-triggered register app)?
   - Async callback webhook (GDAL actor → external webhook → CKAN register)?
   - *User choice needed*: complexity, auth/token handling, error recovery flow
   - *Implementation note*: Requires careful JWT handling in register task (passed as env var, scrubbed from logs)

**Phase 1–3 Implementation Specifics** (DESIGN DECISIONS MADE; OPERATIONAL DEFAULTS NEEDED):

5. **Timeout Defaults** (Phase 2 cost controls): Per-operation timeouts (seconds)?
   - Metadata extraction (gdalinfo): 30 sec?
   - COG conversion (gdal_translate): 120 sec?
   - Large reproject (gdalwarp): 600 sec (10 min)?
   - Clipping (gdalwarp -cutline): 300 sec (5 min)?
   - Overview build (gdaladdo on copy): 300 sec?
   - *User choice needed*: defaults per tool, or global override?

6. **Concurrency Execution Cap** (Phase 2 cost controls): Max concurrent actor executions?
   - Default cap: 5 concurrent?
   - Scoping: per-user, per-team/allocation, or global?
   - What happens when cap is reached (reject or queue)?
   - *User choice needed*: cap value + enforcement scope

7. **Output Naming Convention** (Phase 3 transforms, deterministic vs. user-specified):
   - Fully deterministic (server controls all naming, e.g., `rainfall_epsg4326_<timestamp>_<uuid>.tif`)?
   - User-specified basename only (server validates regex + collision detection)?
   - Hybrid (user provides basename, server enforces format + adds collision suffix)?
   - *User choice needed*: user experience, idempotency (critical for failed registration retry), audit traceability
   - *Recommendation*: Deterministic naming with user-readable prefix (e.g., user-supplied name + server suffix); enables idempotent retries in v1

**Phase 3+ Future Enhancements** (Not blocking v1.0 GA; noted for roadmap):

8. **Cost Estimation & Quota Warnings** (v1.1+): Should dso-geo estimate execution cost before submission?
   - Query Tapis for HPC cost rate (SUs per actor execution)?
   - Warn if estimated cost exceeds threshold?
   - Require user confirmation for high-cost operations?
   - *Recommendation*: defer to v1.1; v1.0 relies on documented timeouts + concurrency cap

9. **Orphan Output Recovery** (v1.1+): How to handle failed CKAN registrations?
   - Current v1.0: deterministic naming + audit logs for manual recovery
   - Future: Add a `list_outputs()` tool to reconcile Corral with CKAN registry?
   - *Recommendation*: implement deterministic naming in v1.0; defer list_outputs() to v1.1

10. **Tapis Scoped Service Tokens** (v2.0+ security improvement): Move from password-grant (broad scope) to narrowly-scoped OAuth2 tokens?
    - *Recommendation*: document as roadmap; v1 uses full-user token with token middleware scrubbing as mitigation

## Decisions

**Decision 1: GDAL Image = MUST BUILD (Phase 0, Critical Path)** (2026-06-29)
- **Decided by**: User
- **Rationale**: No existing TACC GDAL image; building one is a prerequisite for all v1 geo tools. Phase 0 is on the critical path.
- **Impact**: Phase 0 deliverables (Dockerfile, Tapis registration, config) must complete before Phases 1+.
- **Status**: APPROVED; open sub-questions on Actor vs Job, base image, Corral access, command validation moved to Open Questions.

**Decision 2: v1 Scope = Metadata + Gated Transforms (NOT Metadata-Only)** (2026-06-29)
- **Decided by**: User
- **Rationale**: Reframed from "metadata-only v1 / transforms v2" to "v1 includes both with gated approval + dry-run + audit from day 1". Transforms are risky but managed by gate, not deferred.
- **Impact**: v1 tool list: `gdalinfo_extract`, `gdalinfo_summary`, `reproject_raster`, `convert_to_cog`, `clip_raster`, `build_overviews`, `get_execution_status`. All Phase 1+2 together; metadata is not in isolation.
- **Status**: APPROVED; tiering moved from "v1 vs v2" to "risk tier behind gate".

**Decision 3: Async Model = Submit + Poll (NOT Submit-and-Wait)** (2026-06-29)
- **Decided by**: User
- **Rationale**: Non-blocking; supports long-running ops (5–10 min reprojections); aligns with Tapis async semantics and existing `mint-runner` pattern.
- **Impact**: All tools return execution ID immediately; `get_execution_status(execution_id)` tool required for all workflows. Claude Code polls implicitly in a loop.
- **Status**: APPROVED; definitive design; submit-and-wait moved to Alternatives (rejected).

**Decision 4: CKAN Composition = Loose, Zero-Coupling (dso-geo Returns Path + Provenance)** (2026-06-29)
- **Decided by**: User
- **Rationale**: dso-geo holds NO CKAN client and does NOT call CKAN API. Transformation tools return Corral output path + provenance dict. MODEL orchestrates CKAN registration via dso-ckan's `schema_create_resource` tool.
- **Impact**: 
  - NO `register_output_to_ckan` tool in dso-geo
  - NO CKANClient import / dependency in dso-geo
  - NO shared CKANClient module extraction needed (moot question)
  - dso-geo and dso-ckan remain independent; no version coupling
  - MODEL (Claude Code or external orchestrator) calls dso-ckan to register outputs
- **Status**: APPROVED; resolves composition open question entirely.

**Decision 5: Phase-0 Proof-of-Life = Hard Go/No-Go Gate** (2026-06-30)
- **Decided by**: Architect + Skeptic (review feedback); user confirmation needed
- **Rationale**: Before writing ANY dso-geo Python code, must verify that Tapis Actor/Job execution is viable on the actual `portals.tapis.io` tenant. Abaco is unverified/deprecated; only repo OAuth2 endpoints exist (no Abaco-specific code). Proof-of-life experiment (hello-world container, read from Corral, write to output dir) is a hard gate; if it fails, architecture must be reconsidered.
- **Impact**: Phase 0a (1 week proof-of-life) is BLOCKING; Phase 0b (image build) + Phases 1–3 cannot start until Phase 0a is confirmed PASS.
- **Status**: APPROVED (per architect/skeptic feedback); Phase 0a experiment duration estimate (1 week) is contingent on TACC responsiveness.

**Decision 6: Dry-Run = Local Validation Only (No Tapis Submission)** (2026-06-30)
- **Decided by**: Security reviewer
- **Rationale**: `dry_run=True` means validate params, construct GDAL command, estimate output size LOCALLY in dso-geo (no Tapis dispatch). Matches CKAN server's "dry-run = no remote action" guarantee. Avoids burning SUs/queue latency on preview steps. Only `dry_run=False` submits to Tapis.
- **Impact**: Tool signatures updated; data flow clarified; metadata tools inherently dispatch (no dry-run); transforms get local dry-run.
- **Status**: APPROVED; tool signatures updated in spec.

**Decision 7: GDAL Injection Validation = Exact Specified Contract** (2026-06-30)
- **Decided by**: Security reviewer
- **Rationale**: Precise validation rules (not "to be decided") prevent code injection. Enforced at MCP layer AND inside Tapis container (defense in depth).
  - `target_crs`: Integer only (1–999999); server builds `["-t_srs", f"EPSG:{n}"]`; never forward raw string.
  - `output_name`: Regex `^[A-Za-z0-9_\-.]+$`; server prepends GEO_OUTPUT_DIR; caller cannot escape.
  - `compression`: Enum allowlist {deflate, lzw, zstd, none}.
  - `overview_levels`: List of ints, each 2–512, max 10.
  - `clip_geometry`: GeoJSON dict ONLY (no string); Polygon/MultiPolygon; WGS84 bounds; max ~1000 vertices.
  - `execution_id`: Validate format BEFORE interpolating into Tapis API URLs.
  - Entrypoint: Python (not shell), subprocess.run(args, shell=False), list[str] args only.
- **Impact**: Spec now fully specifies validation contract; Phase 0b image implementation must enforce strictly; test with injection payloads.
- **Status**: APPROVED; details added to Risks section and Proposed Design.

**Decision 8: build_overviews = COPY Source, Never In-Place** (2026-06-30)
- **Decided by**: Architect (design review feedback)
- **Rationale**: `gdaladdo` defaults to in-place modification. We must COPY source to GEO_OUTPUT_DIR, then build overviews on the copy. Source file remains untouched (read-only guarantee preserved).
- **Impact**: Tool signature updated (added output_name param); Proposed Design clarified; Risks updated.
- **Status**: APPROVED; tool signature and implementation notes updated.

**Decision 9: Concurrency Cap = Tapis Query (Not Local Counter)** (2026-06-30)
- **Decided by**: Architect (async dispatch feedback)
- **Rationale**: dso-geo is stateless stdio process (no local persistent state). Concurrency cap must be enforced via Tapis in-flight query: before each submission, query Tapis for executions of this app and refuse if >= cap. `gdalinfo_summary()` caps concurrent submissions per call (≤10).
- **Impact**: Phase 2 implementation must include pre-submission Tapis query logic.
- **Status**: APPROVED; Phase 2 cost controls section updated.

**Decision 10: Cross-Server Hand-Back = dso-ckan MUST Validate Output Path** (2026-06-30)
- **Decided by**: Security reviewer (composition risk)
- **Rationale**: dso-geo outputs a path + provenance to MODEL. MODEL calls dso-ckan's `schema_create_resource()` to register. If dso-ckan naively registers ANY path (e.g., `/etc/passwd` due to bug or exploit), source data could be exposed. REQUIRED: dso-ckan must validate that the provided `corral_path` is within GEO_OUTPUT_DIR BEFORE registering.
- **Impact**: Design documents required follow-up change to dso-ckan. Noted in Data Flow (Scenario 2, step 7) and Risks section.
- **Status**: APPROVED; documented as required follow-up; orphan recovery via deterministic naming recommended.

**Decision 11: Token Middleware = Full Tapis Response Scrubbing** (2026-06-30)
- **Decided by**: Security reviewer
- **Rationale**: Tapis password-grant JWT is a full-user token (known v1 risk). Token middleware must scrub `Bearer <...>` and `eyJ...` patterns from ALL Tapis API error responses + logs (not just local formatting). Audit records user identity from JWT claims (decoded WITHOUT logging token). Env fallback `GEO_TAPIS_TOKEN` should warn or refuse if set in production.
- **Impact**: New tapis_middleware.py module required; audit.py refactored for token scrubbing + user identity logging.
- **Status**: APPROVED; documented in Files Likely Affected + Risks; roadmap note for scoped tokens in v2+.

**Decision 12: Async Plumbing = Type-Tagged Execution IDs + Shared status.py Module** (2026-06-30)
- **Decided by**: Architect (modularity feedback)
- **Rationale**: Submit tools return type-tagged execution IDs (e.g., `actor:<id>` / `job:<id>`). `get_execution_status()` parses prefix to hit correct Tapis endpoint. Implemented in tools/status.py (registered ONCE), not duplicated in metadata.py / transform.py.
- **Impact**: Single status.py module shared across all tools; execution ID format specified; polling bounds documented.
- **Status**: APPROVED; tool structure updated in Files Likely Affected.

**Decision 13: Provenance Schema = Extended for CKAN Registration** (2026-06-30)
- **Decided by**: Architect + Auditor feedback
- **Rationale**: Extend provenance to include source_corral_path (full path, not just UUID), gdal_version (from image label), dso_geo_version, submitted_by (user from JWT), timestamp, and optional metrics (duration, size). Embedded in CKAN metadata on registration (not stored in dso-geo).
- **Impact**: Tool signatures updated; provenance schema specified; Data Flow updated; CKAN registration includes full audit trail.
- **Status**: APPROVED; schema locked; Fields Likely Affected updated.

**Decision 14: Rollout Reorder = Phase 0 (proof-of-life + image) → Phase 1 (metadata) → Phase 2 (cost controls) → Phase 3 (transforms, gated)** (2026-06-30)
- **Decided by**: Architect + Cost control feedback
- **Rationale**: Transforms must NOT go live before cost/runaway controls. Previous order (transforms then cost) inverted. Phase 2 (timeouts + concurrency cap + audit) is a prerequisite for Phase 3 GA.
- **Impact**: Rollout plan reordered; Phase 2 duration estimate (1 week); Phase 3 now explicitly depends on Phase 2.
- **Status**: APPROVED; Rollout/Rollback section fully reordered.

---

**Default decisions locked (2026-06-30, revisable after Phase-0a)** — chosen so Phase-0b is not blocked; the user can override any:
- **CKAN client**: dso-geo embeds its own thin CKAN client for v1 (mirrors dso-ckan's CKANClient + X-Tapis-Token auth); extracting a shared `mcp-common` module is a later refactor, not a v1 blocker.
- **Pipeline shape**: two-stage Abaco actor pipeline — Stage 1 GDAL (reads `/vsicurl/`), Stage 2 HTTP-triggered `register_to_ckan` task (resource_create + provenance). Matches the user's "register/upload out through a pipeline task."
- **Base image**: official `osgeo/gdal` (ubuntu-small variant), pinned to a specific tag; custom layers only if a needed driver is missing.
- **Timeout defaults** (per op, env-overridable): gdalinfo 60s, convert_to_cog 180s, build_overviews 300s, clip_raster 300s, reproject_raster 600s.
- **Concurrency cap**: 5 in-flight executions (enforced via Abaco query before submit), env-overridable.
- **Output naming**: deterministic, server-controlled — `{source_name}__{op}__{short_param_hash}.tif` (user `output_name` kept as a sanitized label) so a failed registration can be re-run idempotently.

**Still genuinely open (Phase-0a outcome + TACC specifics — user/TACC input):**
- **Phase-0a Go/No-Go**: authenticated register→execute→poll of a hello-world Abaco actor + `/vsicurl/` read of a CKAN URL + the HTTP-triggered downstream task (pipeline chaining). Abaco endpoint is live; this confirms it end-to-end.
- Does Abaco support the **HTTP-triggered downstream task** the two-stage pipeline relies on (vs. a single actor that does GDAL+register internally)? — the one design fork that the proof-of-life must settle.
- The Tapis **service identity / nonce vs JWT** the actor runs under, and `/vsicurl/` auth for non-public resources.
- Container **egress**: can an Abaco actor make outbound HTTPS to the CKAN host (for `/vsicurl/` read and the register POST)?

## User Feedback / Decisions

**Status**: **In Review** (2026-06-30) — **MAJOR REVISION WITH USER-DIRECTED CHANGES APPLIED**. Five critical prior decisions SUPERSEDED by new user directives (2026-06-30). Spec rewritten for CKAN-linked architecture, /vsicurl/ data access, Abaco-only compute, pipeline-driven registration, and no dry-run. Status remains **In Review** for final user approval on new open questions + operational defaults.

### Superseded Decisions (Reversed/Replaced 2026-06-30)

**S1: CKAN Composition (Reversed: Loose Coupling → Tight Integration)**
- **Supersedes**: Decision 4 (2026-06-29) — "CKAN Composition = Loose, Zero-Coupling"
- **Prior decision**: dso-geo has NO CKAN client; returns output paths; MODEL orchestrates CKAN registration
- **New decision (2026-06-30)**: dso-geo HAS a CKAN client; tools accept CKAN resource/dataset IDs; Abaco actor **pipeline** automatically registers outputs to CKAN via downstream task. No separate MODEL orchestration needed.
- **Rationale**: Tighter integration reduces friction; ensures provenance capture; outputs automatically discoverable in CKAN; simpler UX
- **Impact**: Tool signatures changed (resource_id, dataset_id instead of corral_path); Files Likely Affected includes CKAN client (shared module recommended); Data Flow rewritten (scenarios 1–3 show resource-ID-based resolution + automatic pipeline registration); no cross-server "hand-back" orchestration by MODEL
- **Risk trade-off**: dso-geo now depends on CKAN API availability; mitigated by same auth/error handling as dso-ckan

**S2: Data Access (Reversed: POSIX Mount → HTTP /vsicurl/)**
- **Supersedes**: Implicit assumption in Phase 0 (Corral POSIX mount for all I/O)
- **Prior assumption**: Containers read from Corral via direct POSIX mount (`/data/ckan/*` bound into container)
- **New decision (2026-06-30)**: Source data is read via GDAL's `/vsicurl/` driver (HTTP range reads from CKAN download URLs); no POSIX mount needed. Output files are written to Corral-backed storage (via Abaco execution context or Tapis Files API).
- **Rationale**: More portable; SSRF validation on URLs; auth via HTTP headers (X-Tapis-Token); no container-level mount complexity
- **Impact**: Phase 0a proof-of-life now tests `/vsicurl/` with CKAN URLs (not POSIX paths); Phase 0b image doesn't require Corral mount definition; Files Likely Affected includes `url_validator.py` (SSRF checks) and /vsicurl/ auth header handling
- **Risk trade-off**: HTTP range reads may be slightly slower than POSIX mount for small files; mitigated by GDAL's /vsicurl/ efficiency and modern HTTP server support

**S3: Compute Service (Reversed: Actors + Jobs → Abaco Actors Only)**
- **Supersedes**: Decision and open questions on Actors vs Jobs
- **Prior design**: Support both Tapis Actors (lightweight metadata) and Tapis Jobs (heavier transforms); configurable per tool
- **New decision (2026-06-30)**: **Abaco Actors only** for all operations (metadata + transforms). No Tapis Jobs. Transforms run as Abaco actor **pipelines** (GDAL stage + downstream register task).
- **Rationale**: Simpler architecture (single execution type, no actor:id vs job:id duality); Actors are fast enough for all v1 ops; pipelines elegantly handle multi-stage workflows
- **Impact**: Tool signatures no longer include dry_run parameter (no local preview); execution_id format is always `actor:<id>` (no `job:<id>`); Phase 0b builds single GDAL actor (not separate Job def); Data Flow shows pipeline chaining (Stage 1: GDAL → Stage 2: HTTP register task); Alternatives updated to note Jobs are rejected
- **Risk trade-off**: If a future op exceeds Actor resource limits, may need to revisit Jobs; documented as post-v1.0 enhancement

**S4: Dry-Run (Removed Entirely: Local Preview → Direct Submission)**
- **Supersedes**: Decision 6 (2026-06-30) "Dry-Run = Local Validation Only" and user assumptions of dry-run gating
- **Prior decision**: Transform tools include `dry_run=True` parameter; local validation + preview (no Tapis dispatch) before `dry_run=False` submit
- **New decision (2026-06-30)**: **NO dry-run parameter**. Transforms execute immediately on submission. No preview step.
- **Rationale**: Simplifies API; removes the "approve after preview" gate; transforms are non-destructive (new CKAN resources only, source never mutated); parameter validation is strict; all operations authenticated + audited
- **Impact**: Tool signatures remove `dry_run` parameter from all transforms; Data Flow shows direct submission without preview loop; Tests updated to remove dry-run scenarios; Risks section explicitly documents trade-off (no preview, but non-destructive + audited + validated)
- **Risk trade-off**: Users cannot preview parameters before resource creation; mitigated by strict validation + easy resource deletion + audit trail + immutable source guarantee

**S5: Compute Execution Model (Expanded: Submit+Poll → Submit+Pipeline)**
- **Supersedes**: Implicit design on execution and registration workflow
- **Prior design**: dso-geo submits Actor/Job and returns execution ID; MODEL (or Claude Code) polls; after completion, MODEL calls dso-ckan to register output
- **New design (2026-06-30)**: dso-geo submits Abaco **actor pipeline** where Stage 1 executes GDAL and Stage 2 (HTTP-triggered task) automatically calls CKAN resource_create with provenance. No separate MODEL registration step.
- **Rationale**: Pipeline automation ensures outputs are always registered; provenance is captured at registration time (no separate call); simpler UX
- **Impact**: Phase 0b includes pipeline definition (not just actor image); `register_to_ckan.py` implements downstream task (HTTP POST to CKAN resource_create); Data Flow shows pipeline status progression; Risks includes pipeline failure handling (orphan outputs with deterministic naming for idempotent retry); File organization includes pipeline definition artifact

### New Decisions (2026-06-30, Superseding Prior Decisions)

**Decision 15: CKAN-Linked Architecture** (2026-06-30, SUPERSEDES Decision 4)
- **Decided by**: User
- **Rationale**: Tight integration via CKAN client + pipeline automation is simpler and ensures outputs are always registered with provenance
- **Implementation**: dso-geo embeds CKAN client (or shares extracted module); tool signatures accept resource_id + dataset_id; pipeline task handles CKAN registration
- **Status**: APPROVED as per user directive

**Decision 16: /vsicurl/ Data Access (No POSIX Mount)** (2026-06-30, SUPERSEDES Phase 0 assumption)
- **Decided by**: User
- **Rationale**: HTTP range reads are portable, auth-friendly, and avoid container-level mount complexity
- **Implementation**: Phase 0a tests /vsicurl/ with CKAN URLs; Phase 0b includes url_validator.py (SSRF) + /vsicurl/ auth header handling
- **Status**: APPROVED as per user directive

**Decision 17: Abaco Actors Only (No Tapis Jobs)** (2026-06-30, SUPERSEDES Actor vs Job question)
- **Decided by**: User
- **Rationale**: Simpler architecture; Actors sufficient for all v1 ops; pipelines handle multi-stage workflows elegantly
- **Implementation**: Single GDAL actor image; actor pipelines for transforms; no Job definitions
- **Status**: APPROVED as per user directive

**Decision 18: No Dry-Run (Remove Entirely)** (2026-06-30, SUPERSEDES Decision 6)
- **Decided by**: User
- **Rationale**: Simplifies UX; transforms are non-destructive; strict validation + audit trail mitigate risk
- **Implementation**: Remove `dry_run` param from all tool signatures; Data Flow shows direct submission; Risks documents trade-off
- **Status**: APPROVED as per user directive

**Decision 19: Pipeline-Driven CKAN Registration** (2026-06-30, SUPERSEDES loosecomposition)
- **Decided by**: User
- **Rationale**: Automatic registration ensures outputs are always discoverable + provisioned with provenance; no MODEL orchestration step
- **Implementation**: Abaco actor pipeline (Stage 1: GDAL exec, Stage 2: HTTP POST to CKAN resource_create); register_to_ckan.py task script; deterministic output naming for retry idempotency
- **Status**: APPROVED as per user directive

### Spec Revisions Applied (2026-06-30)

- **Objective**: Reframed to highlight CKAN-linked architecture + /vsicurl/ + Abaco pipelines
- **Current system**: Updated to note /vsicurl/ capability + Abaco availability (unverified)
- **Architecture diagram**: Redrawn showing CKAN client + Abaco pipeline + /vsicurl/ → outputs → CKAN registration loop
- **Tool Set**: Completely rewritten with resource_id/dataset_id inputs (not corral_path); no dry_run param; pipeline-based execution; register_to_dataset parameter for output targeting
- **Files Likely Affected**: Phase 0b now includes url_validator.py, register_to_ckan.py, pipeline definition; Phase 1 includes CKAN client (shared or embedded); no POSIX mount or Corral path validation needed at MCP layer
- **API/Schema**: Tool signatures updated; provenance schema emphasizes source_resource_id + source_ckan_url (not corral_path); execution_id is actor:<id> only
- **Data Flow**: Rewritten with 3 scenarios (metadata single/multiple, transform with pipeline); shows resource resolution → SSRF validation → pipeline submission → automatic CKAN registration
- **Risks**: Completely revised; new risks (SSRF, /vsicurl/ auth, no-dry-run trade-off, pipeline failure orphans); removed risks (POSIX-mount traversal, Corral-only, cross-server hand-back to dso-ckan validation)
- **Alternatives**: Updated to note POSIX mount + Jobs are now rejected; /vsicurl/ is chosen
- **Test Plan**: Updated for CKAN-based tests; SSRF rejection; /vsicurl/ auth; pipeline end-to-end
- **Rollout Phase 0a**: Simplified to test /vsicurl/ + HTTP-triggered pipeline (not POSIX mount); emphasizes Abaco + HTTP range reads
- **Rollout Phase 0b**: Revised to build GDAL actor (not Job), include url_validator + register_to_ckan.py, register pipeline definition
- **Rollout Phase 1**: Updated to include CKAN client implementation + dataset ID support
- **Rollout Phase 3**: Revised to describe pipeline submission + automatic registration (no MODEL hand-back step)
- **Open Questions**: Reduced to 3 blocking (Phase 0a outcome, base image, CKAN client strategy, pipeline impl), 3 operational defaults (timeouts, cap, naming), 2 future (cost est, orphan recovery)

### Next Steps

1. **User review**: Confirm acceptance of all 5 superseding decisions + 5 new decisions (2026-06-30)
2. **Resolve open questions**: Answer Phase 0b + Phase 1–3 details (3 blocking + 3 operational + 2 future)
3. **If approved**: Move Status to **Approved**; Phase 0a proof-of-life begins (1 week, TACC-dependent)
4. **If feedback**: Update spec + iterate

---

## Summary for Reviewer

**Geo MCP Server (`dso-geo`)**: A standalone FastMCP server that dispatches GDAL operations to Tapis compute (Actors/Jobs), enabling geospatial analysis on Corral-stored datasets without downloading bytes.

**User Decisions Applied (2026-06-29)**:
1. ✅ GDAL image: MUST BUILD as Phase 0 (critical path)
2. ✅ v1 scope: Metadata + Gated Transforms (approved together)
3. ✅ Async: Submit + Poll (definitive; submit-and-wait rejected)
4. ✅ CKAN: Loose coupling (dso-geo zero-coupled; MODEL orchestrates)

**V1 Architecture** (5 phases, reordered 2026-06-30):
- **Phase 0a** (BLOCKING GATE, 1 week): Proof-of-life experiment on `portals.tapis.io` (hello-world container, read/write from Corral, verify polling). GO/NO-GO decision. If FAIL, architecture reconsidered.
- **Phase 0b** (1–2 weeks, depends on 0a PASS): Build GDAL Tapis image (Python entrypoint, param + path validators, GDAL ops whitelist). Register as Actor or Job app.
- **Phase 1** (1 week): Metadata tools (`gdalinfo_extract`, `gdalinfo_summary`, `get_execution_status`); low-risk, read-only
- **Phase 2** (1 week, PREREQUISITE for Phase 3 GA): Cost controls + runaway protection (timeouts, concurrency cap via Tapis query, audit)
- **Phase 3** (2–3 weeks): Gated transformation tools (`reproject_raster`, `convert_to_cog`, `clip_raster`, `build_overviews`); dry-run (local, no Tapis) + live (gated); BLOCKED until Phase 2 complete

**V1 Tool Signatures** (corral_path-based):
- **Metadata**: `gdalinfo_extract(corral_path, include_stats, tapis_token)`, `gdalinfo_summary(corral_paths, tapis_token)` — no UUID resolution; MODEL resolves via dso-ckan first
- **Transforms**: `reproject_raster(corral_path, target_crs:int, output_name, dry_run=True, tapis_token)`, `convert_to_cog(...)`, `clip_raster(corral_path, clip_geometry:dict, ...)`, `build_overviews(corral_path, output_name, overview_levels, ...)`
- **Status**: `get_execution_status(execution_id, tapis_token)` — shared module (tools/status.py); type-tagged IDs (actor:id / job:id); all tools use it

**Key Design Decisions** (13 APPROVED, 2026-06-29 + 2026-06-30):
- ✅ GDAL image = MUST BUILD (Phase 0)
- ✅ v1 scope = Metadata + Gated Transforms
- ✅ Async = Submit + Poll (definitive)
- ✅ CKAN = Loose coupling (zero CKAN client in dso-geo)
- ✅ Phase-0 Proof-of-Life = Hard Go/No-Go gate (Abaco viability unknown)
- ✅ Dry-Run = Local validation only (no Tapis dispatch)
- ✅ GDAL Injection = Exact validation contract specified (target_crs int, output_name regex, compression enum, clip_geometry dict-only, Python entrypoint, shell=False)
- ✅ build_overviews = COPY source, never in-place
- ✅ Concurrency Cap = Tapis query (before submit, refuse if >= cap)
- ✅ Cross-Server = dso-ckan MUST validate corral_path in GEO_OUTPUT_DIR (required follow-up change)
- ✅ Token = Middleware scrubs Bearer/eyJ from ALL Tapis responses; audit records user identity (not token)
- ✅ Async Module = tools/status.py (shared, registered once); type-tagged execution IDs
- ✅ Provenance = Extended schema (source_corral_path, gdal_version, dso_geo_version, submitted_by, timestamp, metrics)
- ✅ Rollout = Reordered (Phase 2 cost controls are BLOCKING prerequisite for Phase 3 GA)

**Critical Constraints**:
- **Phase 0a is BLOCKING**: No code until proof-of-life succeeds (1 week, TACC dependent)
- **Phase 2 is BLOCKING Phase 3 GA**: Transforms cannot go live until cost controls complete
- **dso-ckan validation is BLOCKING**: Required follow-up change to CKAN server (path validation)

**Open Questions** (12 total, user input needed):
- Phase 0a proof-of-life outcome (GO/NO-GO)
- Phase 0b: Actor vs Job? Base image? Corral mount? Service account?
- Phase 1–3: Timeout defaults? Concurrency cap? Output naming? Dry-run size estimation?
- Phase 1.1+: Deterministic naming in v1 vs v1.1? List_outputs()? Scoped tokens for v2?

**Recommended Next Step**: User reviews spec, confirms Phase 0a acceptance, answers Phase 0b/1–3 questions. Once approved, move Status to **Approved**; implementation begins with Phase 0a proof-of-life (1 week, contingent on TACC). If Phase 0a fails, escalate to architect for redesign.
