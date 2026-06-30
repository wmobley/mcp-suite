#!/usr/bin/env bash
# Phase 0b local Docker test for the GDAL actor image.
#
# Tests (no Abaco / Tapis / CKAN required):
#   1. docker build
#   2. gdalinfo on a public COG URL via /vsicurl/
#      - asserts JSON output contains CRS and bands
#   3. cog conversion on the same URL
#      - asserts output .tif appears in the mounted host dir
#
# Skips gracefully if:
#   - Docker daemon is not available
#   - No network egress to pull the base image or read the test COG
#
# Override the test COG by setting TEST_COG_URL before running:
#   export TEST_COG_URL="https://your-host.example.com/path/to/file.tif"
#   ./tests/local_test.sh
#
# The default TEST_COG_URL is a publicly-accessible small GeoTIFF on a
# USGS/NASA-backed COG archive. Change it to any public GeoTIFF if the
# default is unreachable from your network.

set -euo pipefail

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ACTOR_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
IMAGE_TAG="dso-geo-gdal-actor:test-$$"
# Small public COG (Landsat 8 OLI, single-band, ~1 MB) from AWS public datasets
DEFAULT_COG_URL="https://landsat-pds.s3.amazonaws.com/c1/L8/139/045/LC08_L1TP_139045_20170304_20170316_01_T1/LC08_L1TP_139045_20170304_20170316_01_T1_B1.TIF"
TEST_COG_URL="${TEST_COG_URL:-${DEFAULT_COG_URL}}"

HOST_OUT_DIR="$(mktemp -d)"
CONTAINER_OUT_DIR="/data/out"

pass=0
fail=0
skip=0

ok()   { echo "  PASS: $1"; pass=$((pass+1)); }
no()   { echo "  FAIL: $1"; fail=$((fail+1)); }
skip() { echo "  SKIP: $1"; skip=$((skip+1)); }

cleanup() {
    echo
    echo "== Cleanup =="
    docker rmi -f "${IMAGE_TAG}" >/dev/null 2>&1 || true
    rm -rf "${HOST_OUT_DIR}"
}
trap cleanup EXIT

# ---------------------------------------------------------------------------
# Check: Docker available
# ---------------------------------------------------------------------------
echo "== Pre-flight: Docker daemon =="
if ! docker info >/dev/null 2>&1; then
    echo "  Docker daemon not available — skipping all Docker tests."
    echo "  (Install Docker Desktop or start the Docker daemon to run live tests.)"
    skip "Docker unavailable — all Docker tests skipped"
    echo
    echo "== Summary == PASS=${pass} FAIL=${fail} SKIP=${skip}"
    echo "Code is complete; Docker tests require a running Docker daemon."
    exit 0
fi
echo "  Docker daemon is available."

# ---------------------------------------------------------------------------
# Check: network egress for base image pull
# ---------------------------------------------------------------------------
echo "== Pre-flight: network egress for base image pull =="
if ! curl -fsS --max-time 10 -o /dev/null "https://ghcr.io/v2/" 2>/dev/null; then
    echo "  Cannot reach ghcr.io — base image pull will likely fail."
    echo "  Attempting build anyway (may fail if image is not cached locally)."
fi

# ---------------------------------------------------------------------------
# Test 1: docker build
# ---------------------------------------------------------------------------
echo
echo "== Test 1: docker build =="
if docker build -t "${IMAGE_TAG}" "${ACTOR_DIR}" 2>&1 | tail -5; then
    ok "docker build succeeded"
else
    no "docker build FAILED"
    echo
    echo "== Summary == PASS=${pass} FAIL=${fail} SKIP=${skip}"
    exit 1
fi

# ---------------------------------------------------------------------------
# Pre-flight: network egress for COG URL
# ---------------------------------------------------------------------------
echo
echo "== Pre-flight: network egress to COG URL =="
if ! curl -fsS --max-time 15 --range "0-512" -o /dev/null "${TEST_COG_URL}" 2>/dev/null; then
    echo "  Cannot reach TEST_COG_URL: ${TEST_COG_URL}"
    echo "  Set TEST_COG_URL to a reachable public GeoTIFF and re-run."
    skip "COG URL unreachable — /vsicurl/ tests skipped"
    echo
    echo "== Summary == PASS=${pass} FAIL=${fail} SKIP=${skip}"
    exit 0
fi
echo "  COG URL reachable: ${TEST_COG_URL}"

# ---------------------------------------------------------------------------
# Test 2: gdalinfo via /vsicurl/ — assert CRS and bands in output
# ---------------------------------------------------------------------------
echo
echo "== Test 2: gdalinfo /vsicurl/ =="

MSG_GDALINFO=$(python3 -c "import json,sys; print(json.dumps({
    'operation': 'gdalinfo',
    'input_url': '${TEST_COG_URL}',
    'output_name': '',
    'params': {},
    'include_stats': False
}))")

GDALINFO_OUTPUT=$(docker run --rm \
    -e MSG="${MSG_GDALINFO}" \
    -v "${HOST_OUT_DIR}:${CONTAINER_OUT_DIR}" \
    "${IMAGE_TAG}" 2>/dev/null)

echo "  Raw output (first 300 chars): ${GDALINFO_OUTPUT:0:300}"

# Check status = ok
STATUS=$(echo "${GDALINFO_OUTPUT}" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('status',''))" 2>/dev/null || echo "")
if [ "${STATUS}" = "ok" ]; then
    ok "gdalinfo returned status=ok"
else
    no "gdalinfo status was not 'ok' (got: ${STATUS})"
fi

# Check metadata contains coordinate system
HAS_CRS=$(echo "${GDALINFO_OUTPUT}" | python3 -c "
import sys, json
d = json.load(sys.stdin)
meta = d.get('metadata', {})
crs_present = bool(meta.get('coordinateSystem') or meta.get('stac', {}).get('proj:epsg'))
print('yes' if crs_present else 'no')
" 2>/dev/null || echo "no")
if [ "${HAS_CRS}" = "yes" ]; then
    ok "gdalinfo metadata contains coordinateSystem (CRS)"
else
    no "gdalinfo metadata missing coordinateSystem"
fi

# Check metadata contains bands
HAS_BANDS=$(echo "${GDALINFO_OUTPUT}" | python3 -c "
import sys, json
d = json.load(sys.stdin)
meta = d.get('metadata', {})
bands = meta.get('bands', [])
print('yes' if bands else 'no')
" 2>/dev/null || echo "no")
if [ "${HAS_BANDS}" = "yes" ]; then
    ok "gdalinfo metadata contains bands"
else
    no "gdalinfo metadata missing bands"
fi

# ---------------------------------------------------------------------------
# Test 3: cog conversion — assert output .tif produced in mounted dir
# ---------------------------------------------------------------------------
echo
echo "== Test 3: cog conversion (gdal_translate -of COG) =="

COG_OUTPUT_NAME="test_cog_output.tif"
MSG_COG=$(python3 -c "import json; print(json.dumps({
    'operation': 'cog',
    'input_url': '${TEST_COG_URL}',
    'output_name': '${COG_OUTPUT_NAME}',
    'params': {'compression': 'deflate'},
    'include_stats': False
}))")

COG_OUTPUT=$(docker run --rm \
    -e MSG="${MSG_COG}" \
    -e OUTPUT_DIR="${CONTAINER_OUT_DIR}" \
    -v "${HOST_OUT_DIR}:${CONTAINER_OUT_DIR}" \
    "${IMAGE_TAG}" 2>/dev/null)

echo "  Raw output (first 300 chars): ${COG_OUTPUT:0:300}"

COG_STATUS=$(echo "${COG_OUTPUT}" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('status',''))" 2>/dev/null || echo "")
if [ "${COG_STATUS}" = "ok" ]; then
    ok "cog returned status=ok"
else
    no "cog status was not 'ok' (got: ${COG_STATUS})"
fi

# Check the output file actually exists in the mounted host directory
if [ -f "${HOST_OUT_DIR}/${COG_OUTPUT_NAME}" ]; then
    FILE_SIZE=$(wc -c < "${HOST_OUT_DIR}/${COG_OUTPUT_NAME}" | tr -d ' ')
    ok "output file exists: ${HOST_OUT_DIR}/${COG_OUTPUT_NAME} (${FILE_SIZE} bytes)"
else
    no "output file NOT found at ${HOST_OUT_DIR}/${COG_OUTPUT_NAME}"
    echo "  Contents of host out dir: $(ls -la "${HOST_OUT_DIR}" 2>/dev/null || echo '<empty>')"
fi

# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------
echo
echo "== Summary == PASS=${pass} FAIL=${fail} SKIP=${skip}"
if [ "${fail}" -gt 0 ]; then
    echo "One or more tests FAILED."
    exit 1
fi
echo "All tests passed."
exit 0
