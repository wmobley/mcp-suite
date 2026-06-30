#!/usr/bin/env bash
# Phase 0b local Docker test for the GDAL actor image — fully offline.
#
# Serves the bundled real orthophoto fixture over a range-capable HTTP server
# and runs the actor over /vsicurl/ (no Abaco / Tapis / CKAN / external egress).
#
# Tests:
#   1. docker build
#   2. gdalinfo over /vsicurl/        -> asserts status ok, CRS + bands
#   3. reproject EPSG:32615 -> 4326   -> asserts an output .tif in the mounted dir
#   4. cog conversion                 -> asserts output is LAYOUT=COG
#
# Skips gracefully if Docker is unavailable.
# Override the fixture with TEST_TIF (a path to any GeoTIFF) if desired.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ACTOR_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
IMAGE_TAG="dso-geo-gdal-actor:test-$$"
FIXTURE="${TEST_TIF:-${SCRIPT_DIR}/fixtures/odm_orthophoto.tif}"
PORT="${TEST_PORT:-8099}"
URL="http://host.docker.internal:${PORT}/$(basename "${FIXTURE}")"

pass=0; fail=0
ok(){ echo "  PASS: $1"; pass=$((pass+1)); }
no(){ echo "  FAIL: $1"; fail=$((fail+1)); }

echo "== Pre-flight =="
if ! docker version >/dev/null 2>&1; then echo "  Docker not available — SKIP"; exit 0; fi
if [ ! -f "${FIXTURE}" ]; then echo "  fixture not found: ${FIXTURE} — SKIP"; exit 0; fi
echo "  fixture: ${FIXTURE}"

WORK="$(mktemp -d)"; cp "${FIXTURE}" "${WORK}/"; OUT="$(mktemp -d)"
cleanup(){ kill "${SRV:-0}" 2>/dev/null || true; docker rmi -f "${IMAGE_TAG}" >/dev/null 2>&1 || true; rm -rf "${WORK}" "${OUT}"; }
trap cleanup EXIT

echo "== Test 1: docker build =="
if docker build -t "${IMAGE_TAG}" "${ACTOR_DIR}" >/tmp/p0b_build.log 2>&1; then ok "image built"; else no "build failed"; tail -5 /tmp/p0b_build.log; exit 1; fi

# Range-capable static server (stdlib http.server doesn't do Range; this does).
cat > "${WORK}/rangeserver.py" <<'PY'
import http.server, functools, os, sys
D=sys.argv[1]; P=int(sys.argv[2])
class H(http.server.SimpleHTTPRequestHandler):
    def do_GET(self):
        p=self.translate_path(self.path)
        if not os.path.isfile(p): self.send_error(404); return
        sz=os.path.getsize(p); rng=self.headers.get('Range')
        with open(p,'rb') as f:
            if rng and rng.startswith('bytes='):
                s,_,e=rng[6:].partition('-'); s=int(s or 0); e=int(e) if e else sz-1; e=min(e,sz-1)
                f.seek(s); data=f.read(e-s+1)
                self.send_response(206); self.send_header('Content-Range',f'bytes {s}-{e}/{sz}'); self.send_header('Content-Length',str(len(data)))
            else:
                data=f.read(); self.send_response(200); self.send_header('Content-Length',str(sz))
            self.send_header('Accept-Ranges','bytes'); self.send_header('Content-Type','image/tiff'); self.end_headers(); self.wfile.write(data)
    def do_HEAD(self):
        p=self.translate_path(self.path)
        if not os.path.isfile(p): self.send_error(404); return
        self.send_response(200); self.send_header('Content-Length',str(os.path.getsize(p))); self.send_header('Accept-Ranges','bytes'); self.end_headers()
    def log_message(self,*a): pass
http.server.HTTPServer(('0.0.0.0',P),functools.partial(H,directory=D)).serve_forever()
PY
python3 "${WORK}/rangeserver.py" "${WORK}" "${PORT}" & SRV=$!; sleep 1

run(){ docker run --rm -v "${OUT}:/data/out" "${IMAGE_TAG}" --message "$1" 2>/dev/null; }

echo "== Test 2: gdalinfo over /vsicurl =="
r=$(run "{\"operation\":\"gdalinfo\",\"input_url\":\"${URL}\",\"include_stats\":false}")
echo "${r}" | python3 -c "import sys,json;d=json.load(sys.stdin);m=d.get('metadata') or {};assert d.get('status')=='ok',d;assert len(m.get('bands',[]))>=1;assert m.get('coordinateSystem');print('  size:',m.get('size'),'bands:',len(m.get('bands',[])))" && ok "gdalinfo over /vsicurl" || no "gdalinfo failed: ${r:0:200}"

echo "== Test 3: reproject -> EPSG:4326 =="
run "{\"operation\":\"reproject\",\"input_url\":\"${URL}\",\"output_name\":\"out_4326.tif\",\"params\":{\"target_crs\":4326}}" >/dev/null
[ -f "${OUT}/out_4326.tif" ] && ok "reprojected output produced" || no "no reprojected output"

echo "== Test 4: cog conversion =="
run "{\"operation\":\"cog\",\"input_url\":\"${URL}\",\"output_name\":\"out_cog.tif\",\"params\":{\"compression\":\"deflate\"}}" >/dev/null
if [ -f "${OUT}/out_cog.tif" ] && docker run --rm --entrypoint gdalinfo -v "${OUT}:/d" "${IMAGE_TAG}" /d/out_cog.tif 2>/dev/null | grep -q "LAYOUT=COG"; then ok "valid COG produced"; else no "COG not produced/valid"; fi

echo
echo "== Summary == PASS=${pass} FAIL=${fail}"
[ "${fail}" -eq 0 ]
