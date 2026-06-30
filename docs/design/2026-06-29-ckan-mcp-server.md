# Custom CKAN MCP Server for DSO Portal

## Status

**Implemented** (2026-06-29) — Track A (read/schema/validate + 4 prompts + `ckan://openapi` resource) and Track B (3 token-gated write tools + security controls) are built, reviewed (architect/skeptic/security/qaqc), and fixed. Suite: 120 passed, 1 skipped (live-write integration guard). Security Controls section verified in code. **Live-write proof: DONE (2026-06-30)** — gated dry-run → live `schema_create_package` into `twdb-gam` via `X-Tapis-Token` (user `wmobley`) succeeded: created `mint_dataset` id `1a027bb5-b31a-436d-840e-e65c7068e6b4` (`mcp-test-throwaway-001`), audit line `status=200` with token scrubbed, verified via `package_show`. Confirmed the full path (Tapis JWT auth → scheming validation → create → audit → read-back). Also empirically confirmed the expiry behavior: an expired JWT yields a clean CKAN 401 that is audit-logged, not a crash. **Remaining: v2/prod prerequisites listed under Decisions** (sysadmin-token warning, prod-write discipline, safe MCP_UPLOAD_DIR, token-in-tool-args security re-look; HTTP transport; resource-field dry-run validation). The dev proof was run with `wmobley` as sysadmin (test portal) — production must use a scoped editor identity. Implementation plan approved by user 2026-06-29.

## Objective

Build a single, unified Model Context Protocol (MCP) server (Python + FastMCP) that enables MCP clients (Claude Code, Windsurf, Cursor, etc.) to interact with this CKAN data portal. The server cherry-picks the best read tools and patterns from three existing community servers, reimplements them in one codebase, adds schema-aware "nuanced" tools tailored to the DSO portal's custom scheming extension, and includes dry-run-first write tools gated by explicit user confirmation. (Per Decision 6, the MCP write path writes directly to the CKAN Action API rather than routing through the `ckan-publisher` agent; the gate is dry-run + explicit user instruction, backed by the security controls below.)

## User Need

**Developers and AI agents** need programmatic, conversational access to the DSO CKAN portal to:
- Search for datasets by relevance, spatial bounds, schema type, and custom fields (mint_standard_variables, temporal coverage, etc.)
- Discover portal structure (organizations, groups, available dataset types)
- Validate metadata against the portal's three dataset schemas before creation
- Create and update datasets with schema validation and dry-run preview
- Upload resource files through CKAN (bytes land on Corral-backed storage in production)
- Write only after explicit user confirmation, with a dry-run preview first

**Example workflows:**
- "Search for all MINT-type datasets in the Texas region created after 2023"
- "Create a new subside_dataset with validated metadata and upload a CSV file"
- "Show me the field schemas for mint_dataset and validate this JSON against it"
- "What MINT standard variables are available for this dataset?"

## Current Code/System Summary

### CKAN Portal Configuration

- **Runtime**: CKAN 2.9+ via Docker Compose (dev: `localhost:5001`, prod: configurable via `CKAN_SITE_URL`)
- **Auth**: Tapis OAuth2 for UI login; CKAN API token (generated via `ckan user token add`) for Action API access
- **Installed Plugins** (from `.env.dev.config`): datastore, datapusher, spatial_metadata, spatial_query, geo_view, scheming_datasets/groups/organizations, **dso_scheming** (custom), openapi, tacc_theme, showcase, tapisfilestore, potree, oauth2, envvars
- **API Documentation**: OpenAPI endpoint at `/api-specs/ckan-openapi.json`

### Custom Scheming Extension (`ckanext-dso_scheming`)

Three dataset schemas defined in YAML:

1. **`ckan_dataset`** (`dataset_type: dataset`): Default CKAN schema
   - Dataset fields: title, name (slug), notes, tag_string, license_id, owner_org, url, version, author, author_email, maintainer, maintainer_email, temporal_coverage_start/end (date preset), spatial (GeoJSON)
   - Resource fields: url (resource_url_upload), name, description, format, mint_standard_variables

2. **`mint_dataset`** (`dataset_type: mint_dataset`): For MINT modeling datasets
   - Same dataset fields as ckan_dataset, but temporal_coverage_start/end are required
   - Resource fields: same as ckan_dataset, plus mint_standard_variables (required)
   - Key differentiator: mint_standard_variables preset points to https://api.models.mint.tacc.utexas.edu/v1.8.0/standardvariables for autocomplete

3. **`subside_dataset`** (`dataset_type: subside_dataset`): For subsidence modeling data
   - Additional dataset field: mint_standard_variables (uses multiple_text preset, not autocomplete)
   - Resource fields: name, url (resource_url_upload), abstract (markdown), format, temporal_coverage_start/end, program_area, data_contact_email, caveats_usage, categories, primary_tags, secondary_tags, collection_method, quality_control_level, spatial (JSON object)

### Existing Presets and Autocomplete

- **Standard presets** (presets.json): title, dataset_slug, tag_string_autocomplete, dataset_organization, resource_url_upload, resource_format_autocomplete, select, multiple_checkbox, multiple_select, date, datetime, datetime_tz, json_object, multiple_text, markdown, radio
- **MINT variable preset** (mint_presets.json): mint_variable_string_autocomplete (calls MINT API directly)

### Existing Workflow Constraints

- **Write-gate policy (pre-existing)**: The repo's default rule is that CKAN writes go through the `ckan-publisher` agent with dry-run first and explicit user approval. **Per Decision 6, this is intentionally superseded for the MCP write path**: MCP write tools write directly to the CKAN Action API, gated by dry-run + explicit user confirmation and constrained by the Security Controls section (scoped non-sysadmin token, prod guard, audit log).
- **File handling / storage**: Data is ingested as a standard CKAN resource **file upload**. CKAN's `ResourceUpload` writes bytes to `ckan.storage_path` (`/var/lib/ckan`, `CKAN_STORAGE_PATH`). In **production**, `docker-compose.yml` mounts host `/data/ckan/storage` and `/data/ckan/resources` into the container, and `/data/ckan/` is the **Corral-backed** mount — so a CKAN upload lands on Corral, which is the desired destination. The `tapisfilestore` plugin is the **serving** side only: it implements `IResourceController` to validate and rewrite/proxy `tapis://` URLs for download (it does **not** implement `IUploader` and does not ingest bytes). v1 ingestion therefore uses the CKAN upload path, not the Tapis Files API.
- **Current Agents**: project-manager, requirements-interviewer, design-spec-writer, architect, skeptic, security-reviewer, implementer, tester, qaqc, docs-writer, github-pr, **ckan-publisher** (CKAN write operations), mint-runner

## Proposed Design

### Architecture Overview

```
┌─────────────────────────────────────────────────────────────┐
│  MCP Client (Claude Code, Windsurf, Cursor, etc.)           │
└────────────────────┬────────────────────────────────────────┘
                     │ MCP Protocol (stdio, HTTP)
                     ▼
┌──────────────────────────────────────────────────────────────┐
│  FastMCP Server (Python 3.10+)                              │
├──────────────────────────────────────────────────────────────┤
│                                                              │
│  Tools Layer:                                               │
│  ├─ Read tools (package_search, resource_show, etc.)       │
│  ├─ Portal config tools (list_dataset_types, etc.)         │
│  ├─ Nuanced search tools (spatial_search, etc.)            │
│  ├─ Validation tools (validate_metadata, etc.)             │
│  └─ Write tools (schema_create_package, etc.) [dry-run]    │
│                                                              │
│  Internal:                                                  │
│  ├─ CKAN API client (requests + error handling + token scrub)│
│  ├─ Schema loader (TTL cache over scheming_* API actions)  │
│  ├─ Dry-run executor (client-side completeness check)      │
│  └─ Prompt templates & result formatting                   │
│                                                              │
└────────────────────┬────────────────────────────────────────┘
                     │ CKAN Action API (HTTP + token auth)
                     ▼
┌──────────────────────────────────────────────────────────────┐
│  DSO CKAN Portal (http://localhost:5001 or env CKAN_URL)    │
├──────────────────────────────────────────────────────────────┤
│  Action API (/api/3/action/*)                              │
│  Scheming schemas (dso_scheming plugin)                     │
│  Datastore (PostgreSQL)                                     │
└──────────────────────────────────────────────────────────────┘
```

### Tool Set (Organized by Category)

#### 1. Read Tools: Core CKAN Actions

Cherry-picked from ondics, ondata, and mjanez implementations; reimplemented for consistency and portal customization.

**Package/Dataset Tools:**
- `package_search(q, fq, rows, start, sort)` — Full-text search with facets and pagination. Supports `fq=` filter queries (e.g., `owner_org:myorg`, `dataset_type:mint_dataset`). Returns result count, datasets, facets.
  - *Source*: ondics ckan-mcp-server
  - *Enhancement*: Include facets for custom scheming fields (dataset_type, temporal coverage range)

- `package_show(id)` — Fetch detailed dataset metadata and resource list.
  - *Source*: ondics
  - *Inputs*: dataset ID or name
  - *Output*: Full package dict (schema-aware; show which fields belong to which schema)

- `find_relevant_datasets(query_text, limit)` — Relevance-ranked search using CKAN's full-text scorer.
  - *Source*: ondata/@aborruso
  - *Use case*: "Find datasets about groundwater" (returns sorted by relevance, not just keyword match)

**Resource Tools:**
- `resource_show(id)` — Fetch single resource metadata (name, url, format, size, created, etc.).
  - *Source*: ondics
  - *Inputs*: resource UUID
  - *Output*: Resource dict with computed properties (e.g., is_remote, mime_type)

**Organization & Group Tools:**
- `organization_show(id, include_datasets)` — Fetch org metadata (name, title, description, display_name, image_url).
  - *Source*: ondics
  - *Inputs*: org ID or name; optional include_datasets (boolean)

- `organization_list(all_fields, include_datasets, limit, offset)` — List all organizations.
  - *Source*: ondics

- `group_list(all_fields, sort, limit, offset)` — List all groups.
  - *Source*: ondics

**Portal Metadata:**
- `get_capabilities()` — Auto-detect active CKAN plugins, version, and endpoint status.
  - *Source*: mjanez
  - *Output*: {ckan_version, site_title, active_plugins, api_endpoint, ...}

#### 2. Nuanced Tools: Schema-Aware Search & Configuration

These are **differentiated** from the existing community servers and enable the portal's custom schemas to shine.

**Dataset Type & Schema Tools:**
- `list_dataset_types()` — List available dataset_type values on this portal (e.g., dataset, mint_dataset, subside_dataset).
  - *Inputs*: None
  - *Output*: List of types with their human-friendly descriptions
  - *Enables*: Knowing which type to create before validating

- `describe_dataset_schema(dataset_type)` — Load and return the full scheming schema for a given dataset_type (fields, presets, required/optional, validators, help text).
  - *Inputs*: dataset_type (e.g., 'mint_dataset')
  - *Output*: JSON-ified schema with dataset_fields and resource_fields annotated, including all preset expansions
  - *Enables*: "Show me the schema for mint_dataset and validate this JSON against it"
  - *Implementation*: Call CKAN scheming Action API `scheming_dataset_schema_show(type_name)` (side-effect-free, verified to exist). Validate `dataset_type` against the list returned by `scheming_dataset_schema_list` first (allowlist; prevents SSRF/path injection). This API returns the live, expanded schema (all presets resolved), not raw YAML.

**Advanced Search Tools:**
- `spatial_search(bbox, dataset_type, limit)` — Search for datasets with spatial coverage overlapping a bounding box. Uses the spatial_query plugin.
  - *Inputs*: bbox (geojson or [min_lon, min_lat, max_lon, max_lat]), optional dataset_type filter, limit
  - *Output*: Matching datasets with spatial metadata
  - *Enables*: "Find all MINT datasets covering the Texas region"

- `search_by_schema_fields(dataset_type, filters, limit)` — Faceted search using custom scheming fields.
  - *Inputs*: dataset_type, dict of {field_name: value_or_range} (e.g., {temporal_coverage_start: "2020-01-01", temporal_coverage_end: "2023-12-31"}), limit
  - *Output*: Matching datasets
  - *Enables*: "Find MINT datasets from 2020 to 2023"
  - *Note*: Builds filter queries from field names; backend validation via CKAN API facets

- `datastore_search_sql(sql, limit)` — Execute a SQL query against the datastore (if enabled).
  - *Source*: ondata
  - *Inputs*: SQL WHERE clause (SELECT fields are pre-defined to prevent injection)
  - *Output*: Rows matching query
  - *Enables*: "Query the datastore for all records where temp > 25°C"

**Autocomplete & Suggest Tools:**
- `autocomplete_mint_standard_variables(incomplete)` — Fetch MINT standard variable completions from the MINT API.
  - *Inputs*: incomplete string (e.g., 'air__')
  - *Output*: List of matching variable names with descriptions
  - *Source*: mint_presets.json preset config
  - *Enables*: "What mint_standard_variables start with 'air__'?"

- `autocomplete_tags(incomplete)` — Tag autocomplete (CKAN built-in).
  - *Inputs*: incomplete string
  - *Output*: Matching tags

- `autocomplete_resource_formats(incomplete)` — Format autocomplete.
  - *Inputs*: incomplete string
  - *Output*: Matching formats

#### 3. Validation & Configuration Tools

- `validate_metadata(dataset_type, metadata_dict, dry_run)` — Validate proposed dataset metadata against the scheming schema WITHOUT creating/updating the dataset.
  - *Inputs*: dataset_type, dict with proposed fields, dry_run (bool; ignored for validation, passed through for future writes)
  - *Output*: {valid: bool, errors: [...], warnings: [...], normalized_metadata: {...}}
  - *Enables*: "Is this metadata valid for mint_dataset?"
  - *Implementation*: Call package_patch or package_create with `__action: validate` (if supported); fallback to client-side validation using scheming field definitions

- `get_organization_users(org_id)` — List users who can upload to an organization (for understanding permissions).
  - *Inputs*: org_id
  - *Output*: List of users with roles

#### 4. Write Tools: Gated, Dry-Run-First

These tools create or update datasets and resources. **Critical**: They respect the `ckan-publisher` gate and default to dry-run validation.

**Design Principle**: All write tools follow this pattern:
1. The tool is called with `dry_run=true` (default) — validates and returns a preview, **no side effects**.
2. The preview is shown to the user.
3. **The live write happens only after the user gives an explicit go-ahead in conversation** — e.g. "write it", "submit", "publish", or a clear variant.
4. Only then does the client call the same tool with `dry_run=false`, and the tool performs the write via the CKAN Action API.
5. Tool returns the result (or a detailed error).

**The gate is the user's explicit instruction**, per the user's decision (2026-06-29). The conversational approval ("write it" / "submit" / variant) is a UX/behavioral gate, not a technical security boundary — the CKAN API token (scoped, non-sysadmin, write-only; see Security Controls section) remains the real security boundary.

> **Relationship to the `ckan-publisher` gate**: this supersedes the earlier "Option A" proposal (route every write through the `ckan-publisher` agent). Per the user's decision, MCP write tools perform the write **directly** once the user says so, with dry-run-first + explicit confirmation as the gate. This relaxation of the repo's "all CKAN writes go through ckan-publisher" rule is intentional for the MCP path and **must be confirmed by the security review** (token scope, audit/logging of writes, dry-run enforcement, no-delete).

**Dataset Write Tools:**
- `schema_create_package(dataset_type, metadata, dry_run=True)` — Create a new dataset with schema validation.
  - *Inputs*:
    - dataset_type (e.g., 'mint_dataset')
    - metadata (dict with title, name, notes, owner_org, etc.)
    - dry_run (bool, default true)
  - *Output* (dry_run=true): {valid: bool, errors: [...], preview: {...}, dry_run: true}
  - *Output* (dry_run=false): {success: bool, result: {...}, id: "...", message: "Dataset created"}
  - *Error handling*: Validation errors list missing/invalid fields; suggest corrections
  - *Workflow integration*: The client presents the dry-run preview to the user (via Claude Code or a UI); the user approves explicitly ("write it" / "submit" / variant); only then does the model call with `dry_run=false`. This aligns with the approval-gate pattern (dry-run-first, explicit human go-ahead, then write).

- `schema_update_package(id, metadata_updates, dry_run=True)` — Update an existing dataset with schema validation.
  - *Inputs*: dataset ID, dict of fields to update, dry_run
  - *Output*: Same as schema_create_package

- `schema_create_resource(package_id, resource_metadata, upload_file, dry_run=True)` — Create a resource by **uploading a local file through CKAN** (the bytes land on Corral in production).
  - *Inputs*:
    - package_id (dataset to add resource to)
    - resource_metadata (dict with name, description, format, mint_standard_variables, etc.)
    - upload_file (str, **local file path**; required for v1's upload path)
    - dry_run (bool, default true)
  - *Implementation*: On a confirmed call (`dry_run=false`), multipart POST to `resource_create` with `data=resource_metadata` and `files={"upload": open(upload_file, "rb")}` plus the API token. CKAN's `ResourceUpload` writes the bytes to `ckan.storage_path`, which is Corral-backed in production. **No Tapis Files API is involved** — this is the standard CKAN upload path.
  - *Dry-run semantics*: byte writes cannot be previewed, so `dry_run=true` validates resource metadata against the schema, confirms `upload_file` exists and is readable, checks it is within `MCP_UPLOAD_DIR`, and reports its size/format. The actual upload happens only on the `dry_run=false` call.
  - *Output*: Same pattern as schema_create_package (dry-run preview vs. live result with resource id + stored URL)
  - *v1 scope*: **CKAN file upload → Corral only.** `tapis://`-URL-referenced resources and plain external-URL resources are explicitly **out of scope for v1** (possible v2 additions).
  - *Size handling*: enforce a configurable max upload size (env, e.g. `MCP_MAX_UPLOAD_MB`); reject uploads larger than the CKAN limit (`CKAN_MAX_UPLOAD_SIZE_MB=100`). Check file size BEFORE open(). The MCP client passes a file **path**, never the bytes through the model context.

**Write Tool Constraints:**
- NO delete tools in v1 (too destructive; adds complexity)
- Writes never bypass the CKAN Action API (all validation is done by CKAN's scheming validators)
- Dry-run uses client-side validation (required fields, field types) plus CKAN schema endpoint; the FULL CKAN validator chain (cross-field validators, unique checks, etc.) runs only on live write. See DRY-RUN VALIDATION HONESTY section below.
- The conversational approval ("write it" / "submit" / variant) is a **UX gate, not a security boundary**. Real auth is via CKAN API token (env var CKAN_API_TOKEN; see Security Controls section)
- If write fails, return full error detail; suggest which fields caused issues

#### 5. Resource Utilities (Internal)

- **Prompt templates** (from ondata):
  - "Analyze this dataset: [summary of fields, temporal/spatial coverage, license, etc.]"
  - "Recent datasets in [org]" (formatted for humans)
  - "Dataset [ID] has [N] resources" (summary card)

- **Resource scheme**: `ckan://datasets/ID`, `ckan://organizations/ID`, `ckan://groups/ID`
  - *Enables*: MCP clients to reference portal objects; not required for v1 but recommended for extensibility

### V1 Scope Cut (Approved 2026-06-29)

**V1 Ships**:

- **READ** (anonymous, no token): `package_search`, `package_show`, `find_relevant_datasets`, `resource_show`, `organization_list`, `organization_show`, `group_list`, `get_capabilities`
- **SCHEMA**: `list_dataset_types`, `describe_dataset_schema`
- **VALIDATION**: `validate_metadata` (client-side completeness check; see Dry-Run Validation Honesty section)
- **WRITE** (token required, all controls in Security Controls section): `schema_create_package`, `schema_update_package`, `schema_create_resource` (CKAN multipart upload → Corral)

**Deferred to V2** (with rationale; revisit when unverified):
- `datastore_search_sql` — SQL-injection surface (limited to SELECT only; no UNION/subqueries/semicolons). In v2, replace with structured `datastore_search` or a WHERE-only allowlist parser, relying on CKAN's read-only datastore user.
- `spatial_search` — bbox/Solr fq syntax unverified against solr-bbox backend. v2 will test against spatial_query plugin.
- `search_by_schema_fields` with date-range filters — depends on Solr indexing custom extras as date-typed fields (not confirmed). v2 may need post-fetch Python filtering with pagination caveat.
- `autocomplete_mint_standard_variables` — verify the MINT API response shape first; preset URL is a browser-autocomplete contract. v2 will add with confidence.
- `autocomplete_tags` / `autocomplete_resource_formats` — keep in v1 only if backed by standard CKAN actions; otherwise defer.
- `tapis://`-referenced resources — file already on Corral, no ingestion needed, but requires Tapis integration (out of scope v1).
- Plain external-URL resources — no upload needed, but may require URL validation / availability check (out of scope v1).
- `get_organization_users` — Returns PII risk; defer to v2 with role-only output and audit controls.

### Files Likely Affected

**New files to create:**

1. `ckan-docker/mcp-server/` (new directory; co-located with the CKAN portal it serves; not at repo root)
   - `__init__.py` — Package initialization
   - `server.py` — FastMCP server entrypoint and tool registration
   - `tools/` — Tool implementations (one module per category)
     - `read.py` — Core read tools (package_search, resource_show, etc.)
     - `nuanced.py` — Schema-aware search and config tools (note: may split into search.py / schema.py / autocomplete.py / config.py as tool count grows)
     - `validation.py` — Validation and autocomplete tools
     - `write.py` — Write tools with dry-run logic
   - `ckan_client.py` — CKAN API wrapper (requests + error handling; scrubs Authorization headers from logs/errors)
   - `schema_loader.py` — Thin TTL-cached wrapper around CKAN scheming API endpoints (scheming_dataset_schema_list, scheming_dataset_schema_show)
   - `validators.py` — Client-side schema validation helpers
   - `prompts.py` — Prompt templates and result formatting
   - `config.py` — Configuration (env vars, defaults)

2. `ckan-docker/mcp-server/requirements.txt` — Python dependencies (fastmcp, requests, pydantic, etc.)

3. `.env.dev.secrets` and `.env.prod.secrets` (create if not present): Add `CKAN_API_TOKEN` (write-only token; non-sysadmin editor role; optional at runtime). Add `MCP_ALLOW_PROD_WRITES` (false for dev, explicit true for prod). Add `MCP_UPLOAD_DIR` (configurable allowed upload dir; default: `/tmp/mcp-uploads`). Add `MCP_MAX_UPLOAD_MB` (strictly LOWER than CKAN's `CKAN_MAX_UPLOAD_SIZE_MB=100`; default: 50). Ensure `.gitignore` includes `*.secrets` files.

4. `docs/design/2026-06-29-ckan-mcp-server.md` — This design spec

**Existing files modified (minimal):**

- None for v1 (no changes to docker-compose, CKAN core, or plugins). The MCP server runs standalone on the developer's host via stdio transport.

**Testing:**
- `ckan-docker/mcp-server/tests/` — Unit tests for tools, schema loader, validators
- Integration tests (dry-run tools against real dev portal)

**Note on container service**: Development container service is `ckan-dev` (so token/admin CLI commands use `docker compose -f docker-compose.dev.yml exec ckan-dev ckan ...`). v1 does not containerize the MCP server; it runs locally via stdio.

### API/Schema Changes

**New MCP Tool Signatures** (Python/FastMCP):

```python
# Read tools
@mcp.tool()
def package_search(q: str, fq: list[str] | None = None, rows: int = 10, start: int = 0, sort: str = "score desc") -> dict:
    """Search datasets with full-text and filters."""
    pass

@mcp.tool()
def package_show(id: str) -> dict:
    """Get dataset metadata and resources."""
    pass

@mcp.tool()
def find_relevant_datasets(query_text: str, limit: int = 10) -> list[dict]:
    """Search datasets ranked by relevance."""
    pass

# ... (resource_show, organization_show, group_list, get_capabilities, etc.)

# Nuanced tools
@mcp.tool()
def list_dataset_types() -> list[dict]:
    """List available dataset_type values and descriptions."""
    pass

@mcp.tool()
def describe_dataset_schema(dataset_type: str) -> dict:
    """Return full scheming YAML for a dataset_type."""
    pass

@mcp.tool()
def spatial_search(bbox: list[float] | dict, dataset_type: str | None = None, limit: int = 10) -> list[dict]:
    """Search datasets by spatial coverage."""
    pass

@mcp.tool()
def search_by_schema_fields(dataset_type: str, filters: dict, limit: int = 10) -> list[dict]:
    """Search datasets using custom field filters."""
    pass

# ... (datastore_search_sql, autocomplete_*, validate_metadata, etc.)

# Write tools
@mcp.tool()
def schema_create_package(
    dataset_type: str,
    metadata: dict,
    dry_run: bool = True
) -> dict:
    """Create dataset with schema validation."""
    pass

@mcp.tool()
def schema_update_package(
    id: str,
    metadata_updates: dict,
    dry_run: bool = True
) -> dict:
    """Update dataset with schema validation."""
    pass

@mcp.tool()
def schema_create_resource(
    package_id: str,
    resource_metadata: dict,
    upload_file: str | None = None,
    dry_run: bool = True
) -> dict:
    """Create resource with optional file upload."""
    pass
```

**No changes to CKAN core API.** The MCP server calls the existing CKAN Action API (`/api/3/action/*`). The server is read-only from CKAN's perspective until write tools are fully specified.

**No changes to dso_scheming plugin.** The MCP server reads existing schemas and validates against them.

### Dry-Run Validation Honesty

There is **no reliable server-side validate-only action in CKAN 2.11**. Dry-run validation in write tools is therefore defined as:

1. **Client-side completeness & type check**: Using the field list and validators from `scheming_dataset_schema_show`, the tool validates that all required fields are present, that field types match (e.g., date fields are ISO8601), and that no extra fields are included.
2. **Live validator chain on write**: The FULL CKAN validator chain (cross-field validators, unique package name/slug collision, owner_org membership, token permission, scheming cross-field logic) runs **only on the live write** (`dry_run=false`). CKAN's error response is surfaced as-is to the user.

**Dry-run explicitly CANNOT catch**:
- Unique package name/slug collision (only CKAN knows existing names)
- Owner org membership (only CKAN/token knows permissions)
- API token permission (tried at write time)
- Scheming cross-field validators (run only by CKAN)
- Resource field constraints that depend on CKAN state (e.g., format validation against known mime types)

**Consequence**: A dry-run `valid: true` **does NOT guarantee** the live write succeeds. Users must be prepared for a live write to fail with validation errors; the tool will surface the error with field details and suggest corrections.

### Data Flow

**Scenario: "Create a new MINT dataset with metadata validation"**

1. User (via Claude Code):
   "Create a mint_dataset with title='Rainfall Model 2024', owner_org='water-team'"

2. Claude Code calls MCP server:
   schema_create_package(
     dataset_type='mint_dataset',
     metadata={title: '...', owner_org: '...', ...},
     dry_run=True
   )

3. MCP server:
   a) Loads mint_dataset schema via `scheming_dataset_schema_show('mint_dataset')`
   b) Validates metadata: required fields present, types match, no extra fields
   c) Returns dry-run preview (no CKAN write):
      {
        valid: true,
        preview: {title: '...', owner_org: '...', ...},
        errors: [],
        warnings: [missing required field 'temporal_coverage_start'],
        dry_run: true
      }

4. User reviews preview in Claude Code, decides to add temporal_coverage_start

5. User approves explicitly in conversation ("write it" / "submit" / variant)

6. Claude Code calls again with dry_run=False:
   schema_create_package(
     dataset_type='mint_dataset',
     metadata={title: '...', owner_org: '...', temporal_coverage_start: '2024-01-01', ...},
     dry_run=False
   )

7. MCP server:
   a) Calls CKAN API: package_create(metadata + type='mint_dataset')
   b) CKAN validates using full scheming validator chain (includes cross-field, permission, uniqueness checks)
   c) If valid, creates dataset; returns result with dataset ID
   d) If invalid, returns error with field details

8. MCP server returns:
   {
     success: true,
     id: 'rainfall-model-2024',
     result: {<full dataset metadata>},
     message: "Dataset created successfully"
   }

**Scenario: "Search for datasets with spatial coverage in Texas, 2020-2023"**

1. User:
   "Find MINT datasets covering Texas from 2020 to 2023"

2. Claude Code calls MCP server:
   search_by_schema_fields(
     dataset_type='mint_dataset',
     filters={
       temporal_coverage_start: {gte: '2020-01-01'},
       temporal_coverage_end: {lte: '2023-12-31'}
     },
     limit=20
   )
   [or spatial_search(bbox=[..., ...], dataset_type='mint_dataset')]

3. MCP server:
   a) Builds CKAN filter queries from filters dict
   b) Calls package_search with fq=['dataset_type:mint_dataset', 'temporal_coverage_start:[2020-01-01 TO *]', ...]
   c) Returns results with spatial/temporal metadata highlighted

4. Results displayed in Claude Code as a list of datasets

### Security Controls (Required Before Write Tools Ship)

The following MUST-HAVEs ensure write tools are safe to ship (approved by security review):

**TOKEN MODEL**:
- **Read path is ANONYMOUS** (no token). CKAN serves public datasets, search results, organizations, and groups unauthenticated.
- **Token is WRITE-ONLY** (plus optional for private/draft reads and org user details; reads degrade gracefully to public-only if no token).
- **Token MUST be a dedicated NON-SYSADMIN CKAN user** with `editor` role. CKAN has no read-only/role-scoped API tokens; a sysadmin token = full-portal blast radius — **prohibited**.
- **Token lives ONLY in `*.secrets` files** (e.g. `.env.dev.secrets` or `.env.prod.secrets`), **NEVER in `.env.dev.config`**. Verify `.gitignore` includes `*.secrets`.
- Token construction is **OPTIONAL at runtime**: no token → read-only mode. Write tools hard-fail with a clear error message if no token is set. Create the token with an expiry; document rotation/revocation in runbook.

**TOKEN SCRUBBING**:
- `ckan_client` error handler must **strip Authorization headers** from all exceptions, logs, and tool outputs.
- Startup logs ONLY `CKAN_API_TOKEN: [SET]/[NOT SET]` (never the value).
- Token **never appears** in any tool return dict, error message, or log line.

**PRODUCTION GUARD**:
- Live writes against a non-localhost `CKAN_URL` (not `localhost` or `127.0.0.1`) require explicit env var `MCP_ALLOW_PROD_WRITES=true`, else write tool returns an error before touching CKAN.
- Every dry-run preview and write confirmation must **display the resolved target `CKAN_URL`** (not just `http://localhost:5001` but the actual value).
- Startup logs the **resolved URL and whether it's classified dev** (localhost/127.0.0.1) **or production**.

**UPLOAD PATH SAFETY**:
- `upload_file` path must be resolved with `realpath` and validated to live within a configurable allowed dir (`MCP_UPLOAD_DIR`; default: `/tmp/mcp-uploads`).
- Reject paths outside `MCP_UPLOAD_DIR` and known-sensitive paths (`/etc`, `~/.ssh`, `~/.aws`, etc.).
- Enforce `MCP_MAX_UPLOAD_MB` (strictly LOWER than CKAN's `CKAN_MAX_UPLOAD_SIZE_MB=100`; recommended: 50 MB) by **checking file size BEFORE open()**.
- v1 upload is **stdio/local only** (MCP server co-located with the file). Containerized upload co-location is a v2 item.
- **Test item**: confirm CKAN creates resource atomically; verify no dangling resource on rejected oversized upload.

**AUDIT LOG**:
- Every `dry_run=False` call emits a **structured log line**: ISO8601 timestamp, tool name, args (token-scrubbed), target CKAN_URL, response status, created/updated package/resource id.
- Log output goes to stderr (not stdout, which carries tool results to the MCP client).

**SQL INJECTION (for future v2)**:
- `datastore_search_sql` is deferred from v1 (see V1 Scope Cut section). When built in v2, use structured `datastore_search` API OR a WHERE-only allowlist parser (no UNION / subqueries / semicolons / information_schema), relying on CKAN's read-only datastore user.

**PII PROTECTION**:
- `get_organization_users` (if kept in v1 or later) returns roles only, no PII. Its output must not be logged.

**TRANSPORT**:
- v1 is **stdio/local only** (no HTTP). MCP server runs on the developer's host.
- HTTP transport is explicitly OUT OF SCOPE for v1 and requires a separate security review (network-reachable token, no per-session user identity).

### Risks and Tradeoffs

1. **Security: API Token Scope & Exposure**
   - *Risk*: CKAN API token (env var `CKAN_API_TOKEN`) is required for all writes; token leakage = portal compromise.
   - *Mitigation* (see Security Controls section): 
     - Token MUST be non-sysadmin editor role (not sysadmin).
     - Token lives ONLY in `*.secrets` files (gitignored).
     - Token is scrubbed from all logs/errors/outputs.
     - Dry-run tools do NOT require the token; only live writes do.
     - Startup logs `[SET]/[NOT SET]`, never the value.
   - *Acceptance*: User/deployment is responsible for token rotation/revocation and secure provisioning.

2. **Write Approval Gate**
   - *Risk*: MCP write tools could bypass approval gates if not properly gated.
   - *Mitigation* (approved by user 2026-06-29): 
     - Dry-run is DEFAULT (`dry_run=true`); user must explicitly set `dry_run=false`.
     - The conversational gate ("write it" / "submit" / variant) is a **UX gate, not a technical security boundary**. The real boundary is the scoped CKAN API token (see Security Controls).
     - Write tool output includes a dry-run preview; user must review before approving.
     - MCP writes directly (not through `ckan-publisher` agent), but the approval model mirrors it: dry-run, user review, explicit approval, write.
   - *Acceptance*: This is an intentional relaxation of the "all writes via ckan-publisher" rule for the MCP path; approved by security review.

3. **Schema Discovery & Parsing**
   - *Risk*: Schemas are defined in CKAN extension (dso_scheming plugin). MCP server must fetch them at runtime.
   - *Approach* (RESOLVED 2026-06-29):
     - **Use CKAN scheming Action API endpoints**: `scheming_dataset_schema_list` (list types) and `scheming_dataset_schema_show(type_name)` (get expanded schema for a type). These are side-effect-free, live, and return preset-expanded schemas (not raw YAML).
     - Validate `dataset_type` against the list from `scheming_dataset_schema_list` (allowlist) before calling `scheming_dataset_schema_show` to prevent SSRF/path injection.
   - *Mitigation*: Cache schemas in memory with TTL (e.g., 1 hour). If API call fails, return error with suggestion to check portal availability.
   - *Acceptance*: Schemas are always live and match the portal's current configuration.

4. **File Uploads & Path Safety**
   - *Clarification*: Ingestion uses the **standard CKAN resource upload** (multipart `resource_create` with an `upload` field), NOT the Tapis Files API. CKAN writes bytes to `ckan.storage_path`, which is Corral-backed in production. The `tapisfilestore` plugin only serves/proxies `tapis://` URLs on download.
   - *Risk*: Large files streamed through the MCP server; path traversal (e.g., `../../etc/passwd`); oversized uploads.
   - *Approach* (see Security Controls section): v1 supports real file upload via a **local file path**. Resolve paths with `realpath`, validate within `MCP_UPLOAD_DIR`, reject sensitive paths. Enforce strict max size (`MCP_MAX_UPLOAD_MB`, strictly lower than CKAN's limit). Check file size BEFORE open().
   - *Mitigation*: Dry-run validates metadata + checks file exists/readable/safe + reports size; bytes written only on confirmed call. Stream the file (don't load fully into memory). Surface CKAN's error on failure; resource created atomically by CKAN (no half-resource on failure). **Test**: confirm no dangling resource on rejected oversized upload.
   - *Out of scope (v1)*: `tapis://`-referenced resources and pushing bytes to Corral via Tapis Files API — possible v2.

5. **MINT API Dependency**
   - *Risk*: mint_variable_string_autocomplete depends on external MINT API (https://api.models.mint.tacc.utexas.edu/...). If MINT API is down, autocomplete fails.
   - *Mitigation*: Autocomplete tools catch HTTP errors and return empty/error message; not fatal to other tools
   - *Acceptance*: Autocomplete failures are graceful; other tools work offline

6. **Performance: Lazy Schema Loading**
   - *Risk*: Loading schemas on every request is slow
   - *Approach*: Cache schemas in memory with TTL (e.g., 1 hour); invalidate on config change signal
   - *Mitigation*: Tool explicitly states cache TTL; user can manually refresh if needed

7. **Compliance: No Destructive Deletes**
   - *Risk*: MCP client could delete datasets via write tools
   - *Mitigation*: v1 does NOT implement delete tools. Only create/update. Deletion requires manual CKAN admin action or a separate approval process.
   - *Acceptance*: Acknowledged and approved by user (see Decisions)

### Alternatives Considered

1. **Merge three existing codebases (ondata, ondics, mjanez)**
   - *Pros*: Faster; less to maintain
   - *Cons*: Three different styles (ondics uses Python, ondata uses Node.js), different dependency sets, conflicting tool naming. Hard to cherry-pick. More likely to introduce bugs merging unfamiliar code.
   - *Decision*: **Rejected**. Build one clean implementation instead.

2. **Fork one of the three servers and extend it**
   - *Pros*: Starting point exists
   - *Cons*: Still requires understanding and refactoring for portal-specific nuances (dso_scheming, Tapis). Forked code becomes a maintenance burden. Better to build clean.
   - *Decision*: **Rejected**. Reference the three servers for inspiration, but implement fresh.

3. **Use existing proxy/wrapper to expose CKAN API directly**
   - *Pros*: Minimal code; fast
   - *Cons*: No schema validation, no dry-run, no approval gate, no nuanced search tools. Doesn't meet user need.
   - *Decision*: **Rejected**.

4. **Implement write tools as "validation-only" (no side effects)**
   - *Pros*: Simpler; no approval gate needed
   - *Cons*: User can't create/update datasets via MCP; defeats the "read + write" goal. Still requires ckan-publisher agent for actual writes.
   - *Decision*: **Rejected in favor of dry-run-first approach** (see Proposed Design).

5. **Use SPARQL endpoint instead of CKAN API**
   - *Pros*: Semantic search, RDF metadata
   - *Cons*: CKAN doesn't expose SPARQL by default (would need ckanext-dcat or similar). Plus, it's slower for keyword search.
   - *Decision*: **Rejected**. Stick with CKAN Action API.

### Test Plan

**Unit Tests** (ckan-docker/mcp-server/tests/test_tools.py, test_schema_loader.py, test_validators.py):
- Mock CKAN API responses
- Test each tool's input validation, error handling, output formatting
- Test schema loader against `scheming_dataset_schema_list` and `scheming_dataset_schema_show` responses
- Test validator (client-side) against valid/invalid metadata
- Test dry-run vs. live-write flow (no side effects on dry-run)
- Test path traversal rejection in upload_file (e.g., `../../etc/passwd`)
- Test oversized upload rejection (before open())
- Test token scrubbing in error messages (no Authorization header in logs)

**Integration Tests** (against dev portal):
- Setup: Run `docker compose -f docker-compose.dev.yml up -d` with CKAN running on localhost:5001
- Test: Call each read tool; verify results match CKAN web UI
- Test: Call `list_dataset_types()` and `describe_dataset_schema(type)` for each schema; verify preset expansion
- Test: Dry-run `schema_create_package` with valid/invalid metadata; verify preview and error messages
- Test: Live write with valid metadata; verify dataset created (requires CKAN_API_TOKEN set)
- Test: Verify dry_run=false fails gracefully if CKAN_API_TOKEN not set
- Coverage: All three dataset_types (dataset, mint_dataset, subside_dataset)

**MCP Protocol Tests**:
- Verify server implements MCP specification (tools, resources, prompts)
- Test server initialization with FastMCP
- Test tool invocation via MCP client (e.g., Claude Code)

**Dry-Run & Approval Flow**:
- Verify dry_run=true returns preview without side effects
- Verify dry_run=false attempts actual write
- Verify missing CKAN_API_TOKEN on dry_run=false returns clear error

**Security Tests**:
- Verify read tools work without token (anonymous)
- Verify write tools fail cleanly without token
- Verify prod-guard: write fails if CKAN_URL is not localhost and MCP_ALLOW_PROD_WRITES is not set
- Verify token not logged at startup (only `[SET]/[NOT SET]`)
- Verify path-traversal rejection (test with `../../../etc/passwd`)
- Verify oversized-upload rejection (before opening file)

**Schema Validation Tests**:
- Load each of the three scheming YAML files
- Validate sample metadata against each schema
- Test error messages for missing/invalid fields
- Test warnings for optional fields

**Error Handling**:
- Test CKAN API connection failure → graceful error
- Test invalid dataset_type → error with hint
- Test malformed bounding box → error
- Test MINT API timeout in autocomplete → return empty list, not crash

### Documentation Plan

**Code Comments:**
- Each tool function has docstring with description, inputs, outputs, security notes, example
- Schema loader comments explain API endpoints and caching strategy
- Validators comments explain client-side vs. server-side validation boundary
- ckan_client comments explain token scrubbing and error handling

**README** (ckan-docker/mcp-server/README.md):
- Quick start: install, configure env vars, run server
- Tool reference: list of tools with signatures, inputs/outputs, examples
- Configuration: env vars (CKAN_URL, CKAN_API_TOKEN, MCP_UPLOAD_DIR, MCP_MAX_UPLOAD_MB, MCP_ALLOW_PROD_WRITES)
- Security: token model (non-sysadmin, write-only), secrets file location, prod guard
- Architecture: diagram and layer breakdown
- Troubleshooting: common errors (connection failure, token invalid, missing CKAN_API_TOKEN, etc.)
- Limitations: v1 scope (read + writes; no deletes; no SQL; no tapis:// or external URLs; stdio/local only)

**User-Facing Documentation** (docs/mcp/ckan-mcp-server.md):
- What is an MCP server? (brief explanation)
- Why use the CKAN MCP server? (benefits: conversational search, schema-aware validation, approval-gated writes)
- Setup: install MCP server, add to Claude Desktop / Windsurf / Cursor config, set environment variables
- Security setup: create CKAN_API_TOKEN (non-sysadmin), gitignore `*.secrets`
- Example workflows: search datasets, validate metadata, create dataset with upload
- Approval gate explanation: dry-run, user review, explicit approval ("write it"), then write
- Dry-run validation caveats: what dry-run can/cannot catch (see Dry-Run Validation Honesty section)
- FAQ: "Can I delete datasets?" (No, v1 only creates/updates), "What if MINT API is down?" (autocomplete fails gracefully), "Can I use this in production?" (Yes, with token setup and MCP_ALLOW_PROD_WRITES), etc.

**Runbook** (for deployment):
- How to run MCP server locally (stdio) on dev host
- How to provision CKAN_API_TOKEN (non-sysadmin editor role; with expiry)
- How to configure secrets files and .gitignore
- How to monitor for errors (stderr logs)
- How to rotate/revoke token
- How to deploy in production (containerize and co-locate with CKAN if needed; future v2)

### Rollout/Rollback Plan

**Phase 1: Development & Testing (Current)**
- Implement MCP server in mcp-server/ directory
- Run against dev portal (docker-compose.dev.yml)
- Write tests; achieve >80% coverage
- Internal feedback from team (architect, skeptic, security-reviewer)

**Phase 2: Closed Beta (Dev/Staging)**
- Deploy MCP server alongside staging CKAN instance
- Limited testers (project team) use it via Claude Code / Windsurf
- Collect feedback: tool usefulness, error cases, performance
- Refine based on feedback (no breaking changes)

**Phase 3: Production Release**
- Deploy to production CKAN instance
- Document setup and limitations
- Announce to DSO portal users
- Monitor for usage errors and API errors

**Rollback:**
- MCP server is stateless; no data loss on crash
- If server has bugs, redeploy previous version (git tag)
- If CKAN API behavior changes, update schema loader and validators
- No database migrations needed

### Open Questions

All critical open questions have been **RESOLVED** (2026-06-29) through consolidated architect/skeptic/security review feedback:

1. **Schema Discovery** — **RESOLVED**
   - *Decision*: Use CKAN scheming Action API endpoints `scheming_dataset_schema_list` and `scheming_dataset_schema_show(type_name)` as the PRIMARY and only mechanism. These are side-effect-free, verified to exist, live, and return preset-expanded schemas.
   - *Validation*: Validate `dataset_type` against list from `scheming_dataset_schema_list` (allowlist; prevents SSRF/path injection).
   - *Caching*: TTL-cache schemas in memory (1 hour default); timeout/failure returns error with suggestion to check portal.

2. **Caching Strategy** — **RESOLVED**
   - *Decision*: Fixed TTL (1 hour). User can provide `--no-cache` flag at startup for dev.

3. **Dry-Run Validation** — **RESOLVED**
   - *Decision*: Client-side completeness check only (required fields, types, no extra fields). Full validator chain runs only on live write. See Dry-Run Validation Honesty section.
   - *Consequence*: Dry-run `valid: true` does NOT guarantee live write succeeds. Users must be prepared for live validation errors.

4. **Write Approval Gate** — **RESOLVED**
   - *Decision*: The user's explicit instruction ("write it" / "submit" / variant) is the UX gate. MCP writes directly after dry-run approval, **not through `ckan-publisher` agent**. Scoped, non-sysadmin CKAN API token is the real security boundary. Dry-run is default; `dry_run=false` is only set after explicit user approval.
   - *Rationale*: Dry-run + explicit approval + token scope = safe approval pattern without extra agent routing.

5. **MINT API Graceful Degradation** — **RESOLVED**
   - *Decision*: Return empty list on MINT API timeout/failure. Log warning. Not fatal.

6. **Customization & DSO-Specific Tools** — **RESOLVED**
   - *Decision*: None at the moment. v1 ships generic schema-aware tools only. Keep server **schema-agnostic** (reads `CKAN___SCHEMING__DATASET_SCHEMAS` dynamically).

7. **No SPARQL in v1** — **RESOLVED**
   - *Decision*: Stick with CKAN Action API (simpler, more reliable). SPARQL is v2 if needed.

---

## Decisions

**Write-auth model change — Tapis JWT via `X-Tapis-Token` (2026-06-29)** — SUPERSEDES the spec's original "CKAN API token" assumption. The portal does NOT use raw CKAN API tokens; it authenticates API writes with a **Tapis OAuth2 JWT** via the custom `ckanext-oauth2` `request_loader` (`CKAN_OAUTH2_JWT_ENABLE=true`, RS256, public key configured), which accepts an `X-Tapis-Token` (or `Authorization: Bearer`) header. Changes:
- `ckan_client.post()` sends the token as **`X-Tapis-Token`** (reads remain anonymous). Raw-`Authorization` (CKAN-API-token) path removed.
- The token is supplied **per-call** as a `tapis_token` argument on each write tool (server stores nothing); `CKAN_API_TOKEN` env remains an optional fallback. Token scrubbed from audit/errors/returns (incl. `X-Tapis-Token`/`tapis_token` keys).
- **Proven against the live dev portal**: `X-Tapis-Token` authenticates the JWT's user (`api_token_list?user=<u>` → success; anonymous → Authorization Error). Suite: 133 passed, 1 skipped.
- **Security re-look needed (tracked)**: a per-call `tapis_token` argument travels through the MCP tool-call arguments (model context / client transcript), unlike a secrets-file token. Accepted for dev given short-lived JWTs + server-side scrubbing; revisit for prod (HTTP transport + request header would keep it out of model context). Tapis JWTs are short-lived (~hours) — no refresh logic in v1; caller supplies a fresh token.

**Track B implementation + review (2026-06-29)** — write tools built under `ckan-docker/mcp-server/` (`tools/write.py`, `upload.py`, `audit.py`, config guards). Security-reviewer + qaqc passed (core controls verified: write gate token-before-POST, prod guard, upload path-traversal/symlink/size guard, audit on success+failure, no-delete, dry-run-never-writes, dataset_type allowlist). Verdict: dev-portal-ready; prod requires the deferred items below. Review fixes applied (suite now **120/120**, 1 skipped live-write guard):
- **Token isolation (security-critical)**: token was attached at session level (rode on read GETs → reads not anonymous, could expose private data). Fixed: shared session is anonymous for reads; `post()` uses a fresh per-call authenticated session. This also resolves the shared-`requests.Session` write thread-safety concern. Verified: reads send no `Authorization`, writes do.
- `schema_update_package` network errors (`status_code==0`) no longer misreported as `dataset_not_found` (now `==404` only).
- `is_production` adds `0.0.0.0`/`::1` to dev hosts; RFC-1918 remains production-by-design (documented).
- Empty `metadata_updates` guarded (no no-op live PATCH).
- Blocked live-write attempts (gate denials) now audit-logged (`audit.log_blocked`).
- `schema_create_resource` dry-run docstring corrected (validates upload path, not schema fields).
- Added write-gate + CKAN-error/audit tests for `schema_update_package` and `schema_create_resource`.

**Deferred to v2 (tracked)**: resource-field schema validation in dry-run (needs a `package_show` fetch for dataset_type); automated sysadmin-token startup warning (TODO in config; **prod prerequisite**). Other prod prerequisites from the security review: confirm `MCP_ALLOW_PROD_WRITES` discipline + `MCP_UPLOAD_DIR` set to a safe path.

**Track A enhancements (2026-06-29, post-QA)**:
- `package_search` now drops each hit's `resources` list by default (keeping `num_resources`); added `include_resources: bool = False` to opt back in. Rationale: a single dataset (e.g. TWDB `ntgam-water-levels`, 170 resources) otherwise floods the response. Full detail remains available via `package_show`.
- Added 4 read-only MCP **prompts** (`prompts.py`, registered on the app): `analyze_dataset`, `find_by_variable`, `recent_datasets`, `describe_org_holdings`. These are network-free guidance templates that steer the model to the existing tools. This brings prompts into v1 (the spec had listed them optional/deferred).
- Added 1 MCP **resource** `ckan://openapi` (`resources.py`): serves the portal's OpenAPI 3.0 spec (`/api-specs/ckan-openapi.json`, ~26 KB), fetched live via a new `CKANClient.get_json()` raw-fetch helper and cached (TTL). On-demand resource so the spec only enters context when a client loads it; informational only (the server still acts solely through its tools). Verified live: 11 tools + 4 prompts + 1 resource register and `read_resource('ckan://openapi')` returns the spec. Suite: 72/72.

**Track A QA/QC pass + fixes (2026-06-29)** — qaqc reviewed Track A (verdict: minor fixes then ship, no blockers). All findings addressed in code; suite now **59/59** (added 8 tests):
- `validators._is_required` now also detects `not_empty` (core CKAN pattern for implicitly-required fields like `name`), so `validate_metadata` no longer passes empty `name`. Documented in the validator disclaimer.
- `package_search` `fq` now accepts a list-or-string (list joined with space) to match the spec contract; `rows`/`limit` clamped (floor 1, cap 1000) across package_search/find_relevant_datasets and capped on organization_list/group_list; README corrected.
- `SchemaLoader` cache mutations now guarded by a `threading.Lock` (FastMCP runs sync tools in worker threads) — clears the must-fix-before-Track-B concurrency item for the schema cache.
- Added a `ckan_client` note to re-evaluate the shared `requests.Session` for thread-safety before Track B write tools land; clarified why `_scrub` isn't called today (headers are not logged).

**Track A implementation deviations (2026-06-29)** — recorded after building read/schema/validate under `ckan-docker/mcp-server/` (51/51 tests pass against the live dev portal):

- **Required-field encoding**: The live portal's `scheming_dataset_schema_show` returns `required: None` on all fields; requiredness is expressed by `scheming_required` appearing in the field's `validators` string, NOT by `required: true`. `validators.py:_is_required()` detects both forms. **This applies to Track B too** — write-tool validation must read `scheming_required` from `validators`, not rely on a `required` boolean.
- **pyproject**: used uv's modern `[dependency-groups]` instead of the deprecated `[tool.uv] dev-dependencies`. No functional change.
- **uv**: was not on PATH in this environment; installed to `~/.local/bin/uv` (one-time, no repo change). Test/run commands need `~/.local/bin` on PATH.

**Finalized by Consolidated Review (2026-06-29)**:

1. **Runtime & Framework**: Python + FastMCP (approved 2026-06-29)

2. **Scope**: Read + gated writes (dry-run-first); no deletes in v1 (approved 2026-06-29). V1 ships: anonymous read, token-gated write, schema discovery, client-side validation. Deferred to v2: datastore_search_sql, spatial_search, date-range filters, autocomplete_mint_standard_variables (unverified), get_organization_users, tapis:// resources, external-URL resources, HTTP transport, containerized upload.

3. **Cherry-pick approach**: Reference three servers (ondata, ondics, mjanez); implement fresh codebase (approved 2026-06-29). Rationale: cleaner than merging, less maintenance than forking.

4. **Schema Discovery — RESOLVED**: Use CKAN scheming Action API (`scheming_dataset_schema_list`, `scheming_dataset_schema_show`). Rationale: live, side-effect-free, preset-expanded, verified to exist on CKAN 2.9+. Validate dataset_type via allowlist (prevents SSRF). TTL-cache 1 hour. (architect+skeptic review 2026-06-29)

5. **Dry-Run Validation Honesty — RESOLVED**: Client-side completeness check only; full CKAN validator chain runs on live write. Dry-run `valid: true` does NOT guarantee write succeeds. Users must be prepared for live validation errors. (security review 2026-06-29)

6. **Write Approval Gate — RESOLVED**: User's explicit instruction ("write it" / "submit" / variant) is the UX gate. MCP writes **directly** via CKAN Action API (not through `ckan-publisher` agent). Scoped, non-sysadmin CKAN API token is the real security boundary. Dry-run=true is default; `dry_run=false` only after explicit approval. Rationale: dry-run + approval + token scope = safe. Relaxation of "all writes via ckan-publisher" approved by security review pending implementation verification. (user + security review 2026-06-29)

7. **File Uploads**: v1 supports **CKAN file upload → Corral only** (multipart `resource_create`, local file path). Path validation (realpath, MCP_UPLOAD_DIR), size check before open(), oversized rejection. Deferred: tapis:// and external-URL resources. (user 2026-06-29)

8. **Confirm Token Removed**: `confirm_token` parameter dropped from all write tools. Gate is conversational approval, not a token. (user + security review 2026-06-29)

9. **Placement**: Server at `ckan-docker/mcp-server/` (co-located with portal it serves; not repo root). v1 runs stdio/local only. (user 2026-06-29)

10. **Token Model (Security Controls)**: Non-sysadmin editor role, write-only, optional at runtime (read-only if absent), lives in `*.secrets` file, scrubbed from logs, audit-logged on write. Prod guard: `MCP_ALLOW_PROD_WRITES=true` required for non-localhost writes. (security review 2026-06-29)

11. **No destructive deletes in v1** (approved 2026-06-29).

12. **Module Note**: `nuanced.py` may split into search.py / schema.py / autocomplete.py / config.py as tool count grows; fine for v1 to keep combined. (architect 2026-06-29)

13. **Schema-Agnostic Design**: Tools read dataset types dynamically; new dso_scheming schemas picked up without code changes. (architect 2026-06-29)

**Implementation Decisions (to be recorded as implementation proceeds):**
- [Placeholder for deviations from approved design and runtime decisions]

---

## User Feedback / Decisions

**APPROVED by user (2026-06-29)** — Status moved to Approved; implementation may begin. Approval is conditional on the Security Controls section being implemented and verified in code before any write tool merges.

**All Design Decisions Consolidated (2026-06-29)**:

This revised spec incorporates feedback from architect, skeptic, and security reviews, plus user decisions on:

- **Schema Discovery** — Changed from hardcoded YAML paths to CKAN scheming API (`scheming_dataset_schema_list`/`scheming_dataset_schema_show`). Rationale: live, verified, preset-expanded, future-proof.

- **Confirm Token** — Removed from all write tool signatures. The conversational gate ("write it" / "submit" / variant) is sufficient; the real boundary is the scoped CKAN API token. This is a UX/behavioral gate, not a technical security boundary. User and security review approved.

- **Dry-Run Validation** — Explicitly honest: client-side completeness only. Full validator chain runs on live write. Dry-run `valid: true` does NOT guarantee write succeeds. Users must be prepared for live validation errors.

- **Write Path** — MCP writes directly via CKAN Action API (not through `ckan-publisher` agent). Dry-run + user approval + token scope = safe gate. This is an intentional relaxation of the "all writes via ckan-publisher" rule for the MCP path, approved by security review.

- **Scope Cut (v1 vs. v2)** — Read + writes in v1. Deferred: datastore_search_sql (SQL injection risk), spatial_search (unverified backend), date-range filters (Solr indexing unconfirmed), autocomplete_mint_standard_variables (response shape unverified), get_organization_users (PII), tapis:// resources, external-URL resources, HTTP transport.

- **Security Controls** — Token must be non-sysadmin editor role, write-only, optional at runtime, lives in `*.secrets`, scrubbed from logs, audit-logged on write. Prod guard: `MCP_ALLOW_PROD_WRITES=true` required for non-localhost writes.

- **Placement** — Server at `ckan-docker/mcp-server/` (co-located with portal; not repo root). v1 runs stdio/local only; containerized deployment is v2.

**Status**: **In Review** (awaiting final user approval before implementation begins).

Next steps:
- User reviews this revised spec.
- Any remaining questions or objections?
- Once approved (Status → **Approved**), implementer can begin.

---

## Appendix: Reference Implementations

The following three community servers informed this design:

1. **ondics/ckan-mcp-server** (GitHub): Python FastMCP server with tools for package_search, resource_show, organization_list, etc. Clean 1:1 wrappers around CKAN Action API.
   - Reference for: Core read tools, tool naming, error handling

2. **ondata/@aborruso** (GitHub): JavaScript/Node.js MCP server with find_relevant_datasets, datastore_search_sql, prompt templates. Focuses on relevance ranking and data analysis.
   - Reference for: Relevance search, prompt templates, ondata-specific patterns

3. **mjanez/ckan-mcp-server** (GitHub): Python FastMCP server with get_capabilities, plugin detection, extensible tool architecture.
   - Reference for: Plugin introspection, capabilities endpoint, architecture patterns

---

## Summary for User Review (Revised 2026-06-29)

**Consolidated Design**: A single Python + FastMCP MCP server for the DSO CKAN portal (co-located at `ckan-docker/mcp-server/`) with:

**V1 Shipped Tools**:
- **READ** (anonymous, no token): `package_search`, `package_show`, `find_relevant_datasets`, `resource_show`, `organization_list`, `organization_show`, `group_list`, `get_capabilities`
- **SCHEMA**: `list_dataset_types`, `describe_dataset_schema` (via CKAN scheming API; live, preset-expanded, TTL-cached)
- **VALIDATION**: `validate_metadata` (client-side completeness; full CKAN validators on write)
- **WRITE** (token-gated, dry-run-first, audit-logged): `schema_create_package`, `schema_update_package`, `schema_create_resource` (CKAN multipart upload → Corral, with path/size validation)

**Security**:
- Token: non-sysadmin editor role, write-only, optional at runtime, lives in `*.secrets`, scrubbed from logs
- Dry-run gate: conversational approval ("write it" / "submit" / variant) is the UX gate; scoped CKAN API token is the real boundary
- Prod guard: `MCP_ALLOW_PROD_WRITES=true` required for non-localhost writes
- Audit: structured log on every `dry_run=False` call (timestamp, tool, args, URL, result)
- No delete tools in v1

**Deferred to V2**: datastore_search_sql (SQL injection risk), spatial_search (backend unverified), date-range filters (Solr indexing unconfirmed), autocomplete_mint_standard_variables (response unverified), get_organization_users (PII), tapis:// and external-URL resources, HTTP transport, containerized deployment.

**Key Revisions (2026-06-29)** from architect/skeptic/security review:
1. Schema discovery now uses CKAN scheming API (not hardcoded YAML paths) — live, verified, future-proof
2. `confirm_token` removed from write tools — conversational gate is sufficient; token is the real boundary
3. Dry-run validation explicitly honest — client-side only; full CKAN validators on write; `valid: true` does NOT guarantee success
4. All critical open questions resolved (see Decisions section)
5. Security Controls section added with MUST-HAVEs for write tools

**Status**: **In Review** (consolidated feedback incorporated; awaiting final user approval)

**Recommended Next Step**: User confirms the consolidated design and approves (Status → **Approved**), then implementation begins.
