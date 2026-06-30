# Phase 0a — Abaco Proof-of-Life (Go/No-Go Gate)

**Purpose:** Before any `dso-geo` code is written, prove that the geo-server's
core assumption works on the **`portals.tapis.io`** tenant:

> An Abaco actor (or actor pipeline) can be registered and executed, can read a
> CKAN resource over **`/vsicurl/`** (HTTP), and a downstream **HTTP-triggered
> task** can register an output back into CKAN.

If this fails, the design must be reconsidered (see the design spec:
`../../docs/design/2026-06-29-geo-actor-mcp-server.md`). **No `dso-geo`
implementation should start until this gate passes.**

This kit performs **external writes** (registering/executing a Tapis Abaco
actor). Run it yourself with your own Tapis token — it is intentionally not run
automatically.

## What we already know (2026-06-30)

- `GET https://portals.tapis.io/v3/actors` is **live** (returned HTTP 400
  "No JWT or nonce provided", Abaco version `26Q2.0`) → the Abaco Actors API is
  **deployed** on the tenant. This kit confirms it end-to-end with auth.
- App-registration rights: **confirmed** by the portal owner.
- `/v3/jobs` also present (HTTP 401) — Tapis Jobs is the documented fallback if
  Abaco proves unsuitable.

## Prerequisites

1. A **fresh** Tapis JWT (they expire in ~hours):
   ```bash
   export TAPIS_TOKEN=$(../../scripts/tapis-oauth/get-jwt.sh <tacc_user> <tacc_pass>)
   ```
2. A CKAN resource **download URL** to test `/vsicurl/` against. Any public
   GeoTIFF resource works — e.g. one of the TWDB NTGAM rasters:
   `http://localhost:5001/.../resource/<id>/download/<file>.tif`
   (For a remote actor, this must be a URL the actor's container can reach —
   see the egress check below.)
3. An actor container image the tenant can pull. For the bare proof-of-life,
   any image that reads the Abaco `MSG` env var works; for the `/vsicurl/`
   step you need a GDAL image (e.g. a pinned `osgeo/gdal`).

## Go/No-Go checklist

| # | Check | Pass criteria |
|---|-------|---------------|
| 1 | **Abaco reachable + token valid** | `GET /v3/actors` → 200 with your token |
| 2 | **Register an actor** | `POST /v3/actors` → 200/201, returns an actor id; status reaches `READY` |
| 3 | **Execute it** | `POST /v3/actors/{id}/messages` → execution id; status reaches `COMPLETE` |
| 4 | **Poll execution** | `GET /v3/actors/{id}/executions/{execId}` returns status + logs |
| 5 | **Container egress to CKAN** | actor can HTTP-GET the CKAN URL (read) |
| 6 | **`/vsicurl/` read** | a GDAL actor runs `gdalinfo /vsicurl/<ckan_url>` and returns metadata |
| 7 | **HTTP-triggered downstream task** | one actor can trigger another (pipeline) OR a single actor can POST to CKAN `resource_create` |

**Gate decision:**
- **GO** if 1–7 all pass → proceed to Phase 0b (build the real GDAL actor +
  the two-stage pipeline).
- **PARTIAL** (1–6 pass, 7 fails) → the *two-stage pipeline* assumption is
  wrong; fall back to a **single actor** that does GDAL **and** the CKAN
  `resource_create` internally. Update the spec's pipeline-shape decision.
- **NO-GO** (Abaco can't register/execute, or no container egress) → revisit
  the architecture: Tapis Jobs instead of Actors, or a different compute path.

## Running

```bash
export TAPIS_TOKEN=...        # fresh JWT (see above)
export ACTOR_IMAGE=...        # an image the tenant can pull (hello-world, then a GDAL image)
export CKAN_TEST_URL=...      # a CKAN resource download URL for the /vsicurl test
./check-abaco.sh
```

`check-abaco.sh` walks checks 1–4 (register → execute → poll → cleanup) and
prints a Go/No-Go summary. Checks 5–7 (egress, `/vsicurl/`, downstream task)
require a GDAL image and a small actor entrypoint — the script documents the
exact API calls to run once that image exists; do not treat them as automated.

> Tapis v3 Abaco API payload shapes can vary by version (`26Q2.0` here). If a
> call returns 4xx with a schema error, check the live API docs for that
> version and adjust the JSON — the *shape* of the experiment (register →
> message → poll) is what matters for the gate.
