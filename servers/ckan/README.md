# DSO CKAN MCP Server

A [Model Context Protocol (MCP)](https://modelcontextprotocol.io/) server for the DSO CKAN data portal.  Enables MCP clients (Claude Code, Windsurf, Cursor, etc.) to search datasets, inspect schemas, validate metadata, and — with an API token — create and update datasets and resources.

**Track A**: read, schema, and validate tools (anonymous, no token required).  
**Track B**: gated write tools (token required; dry-run-first; no delete tools).

---

## Quick Start

### Prerequisites

- Python 3.10+
- [`uv`](https://docs.astral.sh/uv/) package manager (`curl -LsSf https://astral.sh/uv/install.sh | sh`)
- CKAN portal running at `http://localhost:5001` (or set `CKAN_URL`)

### Install and run

```bash
cd ckan-docker/mcp-server

# Install dependencies
uv sync

# Run the server (stdio transport for MCP clients)
uv run dso-ckan-mcp
```

#### HTTP transport (for a long-running consumer such as ckan-agent-api)

By default the server runs over **stdio**. To serve over **HTTP** so another service can connect
as an MCP client, set `MCP_TRANSPORT=http`:

```bash
MCP_TRANSPORT=http \
MCP_HTTP_HOST=127.0.0.1 \
MCP_HTTP_PORT=8100 \
MCP_HTTP_SHARED_SECRET="$(openssl rand -hex 32)" \
uv run dso-ckan-mcp
# serves at http://127.0.0.1:8100/mcp
```

**Security:** HTTP binds to `127.0.0.1` by default and is **unauthenticated unless
`MCP_HTTP_SHARED_SECRET` is set** (clients must then send `Authorization: Bearer <secret>`).
Because a configured `CKAN_API_TOKEN` grants ambient write access to any caller that can reach
the port, never bind to `0.0.0.0` / expose the endpoint publicly without a fronting proxy that
enforces auth.

### Environment variables

Copy `.env.example` to `.env` and edit as needed:

```bash
cp .env.example .env
```

| Variable | Default | Description |
|---|---|---|
| `CKAN_URL` | `http://localhost:5001` | CKAN portal base URL |
| `SCHEMA_CACHE_TTL` | `3600` | Schema cache TTL in seconds |
| `CKAN_API_TOKEN` | *(unset)* | Optional Tapis JWT fallback for writes (see below); normally pass `tapis_token=` per-call instead |
| `MCP_ALLOW_PROD_WRITES` | `false` | Must be `true` to allow live writes to a non-localhost portal |
| `MCP_UPLOAD_DIR` | *(unset)* | Allowed directory for file uploads; unset = file uploads disabled |
| `MCP_MAX_UPLOAD_MB` | `90` | Max upload size in MB (must be strictly < CKAN's 100 MB limit) |
| `MCP_TRANSPORT` | `stdio` | `stdio` for local MCP clients, or `http` to serve over HTTP |
| `MCP_HTTP_HOST` | `127.0.0.1` | HTTP bind host (keep loopback unless fronted by an auth proxy) |
| `MCP_HTTP_PORT` | `8100` | HTTP bind port |
| `MCP_HTTP_SHARED_SECRET` | *(unset)* | Required `Authorization: Bearer` secret for HTTP; unset = no auth (loopback dev only) |

> **`CKAN_URL` divergence:** the production/dev write gate (`MCP_ALLOW_PROD_WRITES`,
> `is_production`) is evaluated against **this server's** `CKAN_URL`, which is independent of
> any consumer's `CKAN_URL`. Set both consistently. Private-IP hosts (`10.x`, `192.168.x`,
> `172.16–31.x`) are classified as **production** — a VPN/staging portal needs
> `MCP_ALLOW_PROD_WRITES=true` to write. The startup banner logs the effective gate.

---

## Tool Reference

### Read tools (8 — anonymous, no token required)

| Tool | Description |
|---|---|
| `package_search(q, fq, rows, start, sort)` | Full-text dataset search with Solr filter queries and pagination |
| `package_show(id)` | Fetch full metadata and resource list for a dataset |
| `find_relevant_datasets(query_text, limit)` | Relevance-ranked dataset search |
| `resource_show(id)` | Fetch metadata for a single resource by UUID |
| `organization_list(all_fields, limit, offset)` | List all organisations |
| `organization_show(id, include_datasets)` | Fetch organisation metadata |
| `group_list(all_fields, sort, limit, offset)` | List all groups |
| `get_capabilities()` | CKAN version, site title, and active extensions |

Result counts are capped server-side at 1 000: `rows` (`package_search`,
`find_relevant_datasets`) is clamped to 1..1000; `limit` (`organization_list`,
`group_list`) is capped at 1 000 when supplied. `package_search` also accepts
`fq` as either a single string or a list of clauses (joined with a space).

### Schema tools (2 — anonymous)

| Tool | Description |
|---|---|
| `list_dataset_types()` | List dataset_type values on this portal (e.g. `dataset`, `mint_dataset`, `subside_dataset`) |
| `describe_dataset_schema(dataset_type)` | Return the full expanded schema (dataset_fields + resource_fields) for a type |

`dataset_type` is validated against the portal allowlist before any API call (SSRF/injection prevention).

### Validation tools (1 — anonymous)

| Tool | Description |
|---|---|
| `validate_metadata(dataset_type, metadata)` | Client-side completeness check: required fields present, unknown keys warned, basic date format |

**Important**: `valid=True` does NOT guarantee the live write will succeed.  The full CKAN validator chain (unique name, org membership, cross-field validators, token permission) runs only on a live write.

### Write tools (3 — token-gated, dry-run-first)

| Tool | Description |
|---|---|
| `schema_create_package(dataset_type, metadata, tapis_token=None, dry_run=True)` | Create a new dataset with schema validation |
| `schema_update_package(id, metadata_updates, tapis_token=None, dry_run=True)` | Patch an existing dataset |
| `schema_create_resource(package_id, resource_metadata, upload_file=None, tapis_token=None, dry_run=True)` | Create a resource, with optional CKAN file upload |

**No delete tools** — v1 deliberately omits deletion.  Deletion requires a manual CKAN admin action.

**Dry-run-first workflow:**
1. Call any write tool with `dry_run=True` (the default) — returns a preview and validation results; NO write is made.
2. Review the preview.
3. Only after explicit user approval ("write it", "submit", etc.) call the same tool with `dry_run=False`.
4. The tool runs the write gate, then POSTs to CKAN.  CKAN's full validator chain runs server-side.

**Dry-run caveat:** `valid=True` on a dry run does NOT guarantee the live write succeeds.  Unique name collisions, org membership, and cross-field validators run only on the live write.

**What `upload_file` does:** `schema_create_resource` accepts a local file path.  On a live call the bytes are sent as a multipart `resource_create` POST to CKAN (not via the Tapis Files API).  CKAN writes the bytes to `ckan.storage_path` (Corral-backed in production).

---

## Write tools (token-gated) — setup

### Authentication model

Write tools authenticate via the portal's `ckanext-oauth2` plugin, which accepts a **Tapis OAuth2 JWT** in the `X-Tapis-Token` header.  Raw CKAN API tokens are not used.

Tapis JWTs are **short-lived (~hours)**.  Obtain a fresh JWT before each write session:

```bash
JWT=$(./scripts/tapis-oauth/get-jwt.sh <username> <password>)
```

There are two ways to supply the token:

**Primary — per-call `tapis_token` argument (recommended):**

Pass the JWT as the `tapis_token=` argument to any write tool:

```python
schema_create_package(dataset_type="mint_dataset", metadata={...}, tapis_token="<jwt>", dry_run=False)
```

The token travels in the tool-call arguments (caller-supplied, never stored server-side).  No env var required.

**Fallback — `CKAN_API_TOKEN` env var:**

If `tapis_token` is omitted, the server falls back to `CKAN_API_TOKEN` (also a Tapis JWT, sent as `X-Tapis-Token`).  Useful for automated workflows.

### 1. Obtain a Tapis JWT

```bash
# Get a short-lived Tapis OAuth2 JWT
JWT=$(./scripts/tapis-oauth/get-jwt.sh <username> <password>)
```

The user MUST have `editor` role on the target organization.  Sysadmin access is **prohibited** for MCP write operations.

### 2. (Optional) Store the fallback token in a secrets file

```bash
# .env.dev.secrets  (gitignored — never commit)
CKAN_API_TOKEN=<tapis-jwt-here>
```

Ensure `*.secrets` is in `.gitignore`.

### 3. Configure upload directory (optional)

```bash
MCP_UPLOAD_DIR=/path/to/allowed/uploads
MCP_MAX_UPLOAD_MB=90   # must be < 100 (CKAN's built-in limit)
```

If `MCP_UPLOAD_DIR` is not set, file uploads are disabled.  Metadata-only resource creates still work.

### 4. Production guard

For non-localhost `CKAN_URL`, live writes are refused unless you explicitly set:

```bash
MCP_ALLOW_PROD_WRITES=true   # in your production secrets file only
```

Without this flag, write tools return a clear error when targeting a production portal.  The startup log emits a prominent `WARNING` when production + token + allow-prod-writes are all set.

### 5. Audit log

Every `dry_run=False` (live) write — success or failure — emits a structured log line to stderr:

```
AUDIT tool=schema_create_package ts=2026-06-29T12:34:56Z ckan_url=http://localhost:5001
      status=200 result_id=abc-123 args_keys=[dataset_type,metadata]
```

Token values and file handles never appear in the audit log.

---

## Prompts (4 — read-only templates)

Parameterised guidance prompts that steer the model to the read tools (they
fetch no data themselves). They surface in MCP clients as selectable prompts.

| Prompt | Description |
|---|---|
| `analyze_dataset(dataset_id)` | Summarise one dataset's metadata, coverage, and resources |
| `find_by_variable(variable)` | Locate datasets carrying a MINT standard variable |
| `recent_datasets(org="", limit=10)` | List most-recently-modified datasets, optionally scoped to an org |
| `describe_org_holdings(org)` | Summarise what an organisation holds |

## Resources (1 — read-only, on-demand)

| Resource URI | Description |
|---|---|
| `ckan://openapi` | The portal's OpenAPI 3.0 spec for the CKAN Action API (`application/json`), fetched live and cached. Informational reference — the server only acts through its tools. |

---

## Register with an MCP client

### `.mcp.json` snippet (Claude Code / Windsurf / Cursor)

```json
{
  "mcpServers": {
    "dso-ckan": {
      "command": "uv",
      "args": ["run", "--directory", "/path/to/ckan-docker/mcp-server", "dso-ckan-mcp"],
      "env": {
        "CKAN_URL": "http://localhost:5001",
        "CKAN_API_TOKEN": "your-token-here"
      }
    }
  }
}
```

Replace `/path/to/ckan-docker/mcp-server` with the absolute path on your system.  For production, move the token to a secrets file and reference it via your environment rather than hard-coding it in `.mcp.json`.

---

## Running tests

```bash
cd ckan-docker/mcp-server
uv run pytest -v
```

Integration tests (marked `integration`) are automatically skipped when the portal at `localhost:5001` is not reachable.  With the portal running they exercise live API calls.

```bash
# Run only unit tests (no portal needed)
uv run pytest -v -m "not integration"

# Run only integration tests
uv run pytest -v -m integration

# Live write integration tests (opt-in; requires token + portal + explicit flag)
MCP_LIVE_WRITE_TESTS=1 CKAN_API_TOKEN=<token> uv run pytest -v -m integration
```

---

## Architecture

```
server.py          — FastMCP app, tool registration, stdio entrypoint
config.py          — env loading, is_production(), startup banner (with prod warning)
ckan_client.py     — thin requests wrapper; X-Tapis-Token auth; scrubbing
schema_loader.py   — TTL-cached scheming API wrapper; allowlist guard
validators.py      — client-side completeness checker
audit.py           — structured audit logger (one line per live write)
upload.py          — upload path validator (realpath, allowed-dir, size)
tools/
  read.py          — 8 anonymous read tools
  schema.py        — 2 schema discovery tools
  validation.py    — 1 validation tool
  write.py         — 3 token-gated write tools (dry-run-first, per-call tapis_token)
```

---

## Security notes

- **Read path is fully anonymous** — no token required or attached; no `X-Tapis-Token` or `Authorization` header is ever sent on GET calls.
- **Write authentication** uses a Tapis OAuth2 JWT sent as `X-Tapis-Token` (required by `ckanext-oauth2`).  The JWT is supplied per-call via `tapis_token=` argument or falls back to `CKAN_API_TOKEN` env var.  Raw CKAN API tokens are not used.
- **Token scrubbing**: `X-Tapis-Token` and `Authorization` header values are redacted in all exceptions and logs via `_scrub()`.  `tapis_token` arg values are never put into `call_args` passed to audit, never appear in returned dicts.
- Startup logs `CKAN_API_TOKEN: [SET] / [NOT SET]` — never the token value.
- `dataset_type` is validated against the portal allowlist before any API call.
- Upload paths are resolved with `os.path.realpath` and validated against `MCP_UPLOAD_DIR`.  `..` traversal and symlink escapes are rejected.  Known-sensitive prefixes (`/etc`, `~/.ssh`, `~/.aws`) are rejected even if somehow inside the allowed dir.
- File size is checked via `os.path.getsize` **before** the file is opened.
- Every live write emits a token-scrubbed audit log line to stderr.
- Production writes require `MCP_ALLOW_PROD_WRITES=true`; the startup banner warns prominently when targeting production.
- No delete tools in v1.

---

## Limitations

- No delete tools (v1; deletion requires manual CKAN admin action).
- No SQL datastore search (deferred — SQL injection surface).
- No spatial bbox search (deferred — backend unverified).
- No MINT variable autocomplete (deferred — response shape unverified).
- stdio transport only (HTTP transport requires a separate security review).
- File uploads require `MCP_UPLOAD_DIR` to be set (security boundary); unset = uploads disabled.
