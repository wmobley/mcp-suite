#!/usr/bin/env bash
#
# Send ONE message to an EXISTING (persistent) Abaco actor, poll the execution,
# and print the actor's logs (its structured JSON output). This mirrors exactly
# what the dso-geo MCP server does at runtime, so it's the quickest way to
# confirm phase-0a checks 5-6 (container egress + /vsicurl reads) on the tenant.
#
# External call (executes the actor). Run with your own fresh Tapis token.
#
# Usage:
#   export TAPIS_TOKEN=...                 # fresh Tapis JWT
#   export ACTOR_ID=0mR3vZ4P1y07r          # the persistent dso-geo actor
#   # a GDAL op message; input_url must be reachable FROM TACC (not localhost):
#   export ACTOR_MESSAGE='{"operation":"gdalinfo","input_url":"https://host/path/file.tif","include_stats":false}'
#   ./exec-actor.sh

set -euo pipefail
BASE="${TAPIS_BASE:-https://portals.tapis.io}"
: "${TAPIS_TOKEN:?Set TAPIS_TOKEN to a fresh Tapis JWT}"
: "${ACTOR_ID:?Set ACTOR_ID to the persistent actor id (from register-actor.sh)}"
: "${ACTOR_MESSAGE:?Set ACTOR_MESSAGE to a GDAL op JSON (see header)}"
hdr=(-H "X-Tapis-Token: ${TAPIS_TOKEN}" -H "Content-Type: application/json")

echo "== Submit message to actor ${ACTOR_ID} =="
body=$(python3 -c "import json,sys;print(json.dumps({'message':sys.argv[1]}))" "${ACTOR_MESSAGE}")
ex=$(curl -s -m 30 "${hdr[@]}" -X POST "${BASE}/v3/actors/${ACTOR_ID}/messages" -d "${body}")
EXEC_ID=$(echo "${ex}" | python3 -c "import sys,json;r=json.load(sys.stdin).get('result',{});print(r.get('execution_id') or '')" 2>/dev/null || echo "")
[ -n "${EXEC_ID}" ] && echo "  execution_id: ${EXEC_ID}" || { echo "  FAIL: no execution_id"; echo "${ex}" | head -c 400; exit 1; }

echo "== Poll =="
for _ in $(seq 1 40); do
  st=$(curl -s -m 15 "${hdr[@]}" "${BASE}/v3/actors/${ACTOR_ID}/executions/${EXEC_ID}" | python3 -c "import sys,json;print(json.load(sys.stdin).get('result',{}).get('status',''))" 2>/dev/null || echo "")
  echo "  status=${st}"
  case "${st}" in COMPLETE|FAILED|ERROR) break;; esac
  sleep 5
done

echo "== Actor logs (the actor's JSON result) =="
curl -s -m 15 "${hdr[@]}" "${BASE}/v3/actors/${ACTOR_ID}/executions/${EXEC_ID}/logs" \
  | python3 -c "import sys,json;print(json.load(sys.stdin).get('result',{}).get('logs','(no logs)'))" 2>/dev/null | head -60
