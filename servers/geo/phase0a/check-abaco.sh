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

# By default the throwaway actor is DELETED on exit (that's why it won't appear
# in the Tapis UI after a run). Set KEEP_ACTOR=1 to keep it for inspection.
cleanup() {
  if [ "${KEEP_ACTOR:-0}" = "1" ]; then
    echo "== KEEP_ACTOR=1 — leaving actor ${ACTOR_ID} in place (delete it yourself when done) =="
    return 0
  fi
  echo "== Cleanup: DELETE actor ${ACTOR_ID} =="
  curl -s -m 20 "${hdr[@]}" -X DELETE "${BASE}/v3/actors/${ACTOR_ID}" -o /dev/null -w '  HTTP %{http_code}\n' || true
}
trap cleanup EXIT

echo "== wait for actor READY =="
for i in $(seq 1 20); do
  st=$(curl -s -m 15 "${hdr[@]}" "${BASE}/v3/actors/${ACTOR_ID}" | python3 -c "import sys,json;print(json.load(sys.stdin).get('result',{}).get('status',''))" 2>/dev/null || echo "")
  echo "  status=${st}"; [ "${st}" = "READY" ] && break; sleep 6
done
[ "${st:-}" = "READY" ] && ok "actor READY" || no "actor did not reach READY"

echo "== Check 3: Execute (POST /v3/actors/${ACTOR_ID}/messages) =="
# ACTOR_MESSAGE: the Abaco message body, delivered to the actor as the MSG env var.
# Default is a plain hello (proves execute+poll). For checks 5-6, set it to a real
# GDAL operation, e.g.:
#   export ACTOR_MESSAGE='{"operation":"gdalinfo","input_url":"'"$CKAN_TEST_URL"'","include_stats":false}'
ACTOR_MESSAGE="${ACTOR_MESSAGE:-phase-0a hello}"
ex=$(curl -s -m 30 "${hdr[@]}" -X POST "${BASE}/v3/actors/${ACTOR_ID}/messages" \
       -d "$(python3 -c "import json,os;print(json.dumps({'message':os.environ['ACTOR_MESSAGE']}))")")
echo "${ex}" | head -c 400; echo
# Abaco returns snake_case execution_id (accept camelCase too, just in case).
EXEC_ID=$(echo "${ex}" | python3 -c "import sys,json;r=json.load(sys.stdin).get('result',{});print(r.get('execution_id') or r.get('executionId') or '')" 2>/dev/null || echo "")
if [ -n "${EXEC_ID}" ]; then ok "execution submitted: ${EXEC_ID}"; else no "no execution_id returned"; fi

if [ -n "${EXEC_ID}" ]; then
  echo "== Check 4: Poll execution (GET .../executions/${EXEC_ID}) =="
  for i in $(seq 1 30); do
    est=$(curl -s -m 15 "${hdr[@]}" "${BASE}/v3/actors/${ACTOR_ID}/executions/${EXEC_ID}" | python3 -c "import sys,json;print(json.load(sys.stdin).get('result',{}).get('status',''))" 2>/dev/null || echo "")
    echo "  exec status=${est}"
    case "${est}" in COMPLETE|FAILED|ERROR) break;; esac
    sleep 6
  done
  [ "${est:-}" = "COMPLETE" ] && ok "execution COMPLETE (poll works)" || no "execution terminal status=${est:-none} (poll works, but actor exited non-COMPLETE)"

  echo "== Actor logs (the actor's stdout — for checks 5-6 this is the JSON result) =="
  curl -s -m 15 "${hdr[@]}" "${BASE}/v3/actors/${ACTOR_ID}/executions/${EXEC_ID}/logs" \
    | python3 -c "import sys,json;d=json.load(sys.stdin);print(d.get('result',{}).get('logs','(no logs)'))" 2>/dev/null | head -40 || echo "  (could not fetch logs)"
fi

echo
echo "== Summary == PASS=${pass} FAIL=${fail}"
echo "Checks 1-4 prove Abaco register/execute/poll with the public GHCR image."
echo "For checks 5-6 (gdalinfo over /vsicurl): set ACTOR_MESSAGE to a real GDAL op"
echo "(see above) with CKAN_TEST_URL reachable from the actor, then inspect the logs."
echo "Check 7 (HTTP-triggered Stage-2 register task) settles single-actor vs pipeline."
