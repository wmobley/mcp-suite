#!/usr/bin/env bash
#
# Phase 0a proof-of-life: Abaco register -> execute -> poll -> cleanup.
#
# This performs EXTERNAL WRITES (registers and runs a Tapis Abaco actor on
# portals.tapis.io). Run it yourself with your own fresh Tapis JWT.
#
# Usage:
#   export TAPIS_TOKEN=$(../../scripts/tapis-oauth/get-jwt.sh <user> <pass>)
#   export ACTOR_IMAGE=<image the tenant can pull>   # e.g. a hello-world, then a GDAL image
#   ./check-abaco.sh
#
# Checks 1-4 (reachability, register, execute, poll) are automated here.
# Checks 5-7 (egress, /vsicurl, downstream task) need a GDAL actor image and
# are documented in README.md — they are the Phase 0b-bound part of the gate.

set -euo pipefail

BASE="${TAPIS_BASE:-https://portals.tapis.io}"
: "${TAPIS_TOKEN:?Set TAPIS_TOKEN to a fresh Tapis JWT (see README)}"
ACTOR_IMAGE="${ACTOR_IMAGE:-}"

hdr=(-H "X-Tapis-Token: ${TAPIS_TOKEN}" -H "Content-Type: application/json")
pass=0 fail=0
ok()   { echo "  PASS: $1"; pass=$((pass+1)); }
no()   { echo "  FAIL: $1"; fail=$((fail+1)); }

echo "== Check 1: Abaco reachable + token valid (GET /v3/actors) =="
code=$(curl -s -m 15 -o /tmp/p0a_actors.json -w '%{http_code}' "${hdr[@]}" "${BASE}/v3/actors")
echo "  HTTP ${code}"
if [ "${code}" = "200" ]; then ok "Abaco reachable and token accepted"
else no "GET /v3/actors returned ${code} (token expired? Abaco auth differs? see body)"; head -c 300 /tmp/p0a_actors.json; echo; fi

if [ -z "${ACTOR_IMAGE}" ]; then
  echo
  echo "ACTOR_IMAGE not set — stopping after the reachability check."
  echo "Set ACTOR_IMAGE to a pullable image to run register/execute/poll (checks 2-4)."
  echo
  echo "== Summary == PASS=${pass} FAIL=${fail} (reachability only)"
  exit 0
fi

echo "== Check 2: Register actor (POST /v3/actors, image=${ACTOR_IMAGE}) =="
# NOTE: adjust this JSON to the live Abaco 26Q2 schema if it 4xxs.
reg=$(curl -s -m 30 "${hdr[@]}" -X POST "${BASE}/v3/actors" \
  -d "{\"image\":\"${ACTOR_IMAGE}\",\"name\":\"dso-geo-phase0a\",\"description\":\"phase-0a proof of life\",\"stateless\":true}")
echo "${reg}" | head -c 400; echo
ACTOR_ID=$(echo "${reg}" | python3 -c "import sys,json;print(json.load(sys.stdin).get('result',{}).get('id',''))" 2>/dev/null || echo "")
if [ -n "${ACTOR_ID}" ]; then ok "actor registered: ${ACTOR_ID}"; else no "no actor id returned (adjust payload to live schema)"; echo "Summary PASS=${pass} FAIL=${fail}"; exit 1; fi

cleanup() { echo "== Cleanup: DELETE actor ${ACTOR_ID} =="; curl -s -m 20 "${hdr[@]}" -X DELETE "${BASE}/v3/actors/${ACTOR_ID}" -o /dev/null -w '  HTTP %{http_code}\n' || true; }
trap cleanup EXIT

echo "== wait for actor READY =="
for i in $(seq 1 20); do
  st=$(curl -s -m 15 "${hdr[@]}" "${BASE}/v3/actors/${ACTOR_ID}" | python3 -c "import sys,json;print(json.load(sys.stdin).get('result',{}).get('status',''))" 2>/dev/null || echo "")
  echo "  status=${st}"; [ "${st}" = "READY" ] && break; sleep 6
done
[ "${st:-}" = "READY" ] && ok "actor READY" || no "actor did not reach READY"

echo "== Check 3: Execute (POST /v3/actors/${ACTOR_ID}/messages) =="
ex=$(curl -s -m 30 "${hdr[@]}" -X POST "${BASE}/v3/actors/${ACTOR_ID}/messages" -d '{"message":"phase-0a hello"}')
echo "${ex}" | head -c 400; echo
EXEC_ID=$(echo "${ex}" | python3 -c "import sys,json;print(json.load(sys.stdin).get('result',{}).get('executionId',''))" 2>/dev/null || echo "")
if [ -n "${EXEC_ID}" ]; then ok "execution submitted: ${EXEC_ID}"; else no "no executionId returned"; fi

if [ -n "${EXEC_ID}" ]; then
  echo "== Check 4: Poll execution (GET .../executions/${EXEC_ID}) =="
  for i in $(seq 1 20); do
    est=$(curl -s -m 15 "${hdr[@]}" "${BASE}/v3/actors/${ACTOR_ID}/executions/${EXEC_ID}" | python3 -c "import sys,json;print(json.load(sys.stdin).get('result',{}).get('status',''))" 2>/dev/null || echo "")
    echo "  exec status=${est}"; [ "${est}" = "COMPLETE" ] && break; sleep 6
  done
  [ "${est:-}" = "COMPLETE" ] && ok "execution COMPLETE (poll works)" || no "execution did not COMPLETE"
fi

echo
echo "== Summary == PASS=${pass} FAIL=${fail}"
echo "Checks 5-7 (container egress to CKAN, gdalinfo /vsicurl, HTTP-triggered downstream task)"
echo "require a GDAL actor image — see README.md. Those settle GO vs PARTIAL vs NO-GO."
