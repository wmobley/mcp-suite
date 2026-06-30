# mcp-suite

A suite of [Model Context Protocol (MCP)](https://modelcontextprotocol.io/)
servers for the DSO / MODFLOW data platform. The servers are independent stdio
processes that an MCP client (Claude Code, Cursor, Windsurf, …) launches and
**composes** — e.g. the model finds a dataset with the CKAN server, then runs a
GDAL transform on it with the geo server.

## Servers

| Server | Path | What it does | Auth |
|--------|------|--------------|------|
| **ckan** (`dso-ckan`) | [`servers/ckan/`](servers/ckan/) | Read/search CKAN datasets, inspect scheming schemas, validate metadata, and (token-gated) create/update datasets + upload resources. | Reads anonymous; writes use a Tapis JWT via `X-Tapis-Token`. |
| **geo** (`dso-geo`) | [`servers/geo/`](servers/geo/) | Run GDAL metadata + transforms (reproject, COG, clip, overviews) on CKAN-hosted rasters via **Tapis Abaco actors**, reading inputs over `/vsicurl/` and registering outputs back to CKAN. | Tapis JWT (per call). |

The CKAN server is implemented and verified (133 tests, live-verified). The geo
server is at the design + Phase-0 stage: the **GDAL actor image** is built and
verified offline ([`servers/geo/gdal-actor/`](servers/geo/gdal-actor/)); the
Tapis-side proof-of-life is the next gate
([`servers/geo/phase0a/`](servers/geo/phase0a/)).

## Design docs

- [CKAN MCP server spec](docs/design/2026-06-29-ckan-mcp-server.md) — Implemented
- [Geo (GDAL/Abaco) MCP server spec](docs/design/2026-06-29-geo-actor-mcp-server.md) — Approved

## Quick start (CKAN server)

```bash
cd servers/ckan
uv sync
uv run dso-ckan-mcp        # stdio MCP server
```

Register it with your MCP client (absolute path):

```jsonc
{
  "mcpServers": {
    "dso-ckan": {
      "command": "uv",
      "args": ["run", "--directory", "/abs/path/to/mcp-suite/servers/ckan", "dso-ckan-mcp"],
      "env": { "CKAN_URL": "http://localhost:5001" }
    }
  }
}
```

Write tools additionally need a Tapis JWT — see
[`servers/ckan/README.md`](servers/ckan/README.md).

## GDAL actor image (geo server)

The geo server dispatches GDAL work to a Tapis Abaco actor running the image in
[`servers/geo/gdal-actor/`](servers/geo/gdal-actor/). CI builds and pushes it to
GHCR on changes:

```
ghcr.io/<owner>/mcp-suite/gdal-actor:<tag>
```

See [`.github/workflows/build-gdal-actor.yml`](.github/workflows/build-gdal-actor.yml).
To run the Tapis-side proof-of-life with the published image, set `ACTOR_IMAGE`
to the pushed tag and follow [`servers/geo/phase0a/README.md`](servers/geo/phase0a/README.md).

## CI

- **build-gdal-actor** — builds + pushes the GDAL actor image to GHCR (on changes
  under `servers/geo/gdal-actor/`, on `v*` tags, or manual dispatch).
- **test** — runs the CKAN server test suite and the actor's validator tests.

## Layout

```
mcp-suite/
├── servers/
│   ├── ckan/      # dso-ckan MCP server (implemented)
│   └── geo/       # dso-geo: gdal-actor image + phase-0a proof-of-life kit
├── docs/design/   # design specs (source of truth for decisions)
└── .github/workflows/
```
