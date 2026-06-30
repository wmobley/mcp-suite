"""
actor.py — GDAL Abaco actor entrypoint.

Reads an operation + params from one of three sources (in priority order):
  1. MSG environment variable (Abaco convention for deployed actors)
  2. --message '<json>' CLI argument (local testing)
  3. stdin (pipe-friendly local testing)

Message schema
--------------
{
    "operation":    "gdalinfo" | "reproject" | "cog" | "clip" | "overviews",
    "input_url":    "https://...",        # must be http(s)
    "output_name":  "result.tif",         # validated bare filename; ignored for gdalinfo
    "params": {
        "target_crs":      4326,          # reproject only; int 1–999999
        "compression":     "deflate",     # cog only; enum {deflate,lzw,zstd,none}
        "overview_levels": [2, 4, 8],     # overviews only; list[int 2–512], max 10
        "clip_geometry":   {...}          # clip only; GeoJSON dict Polygon/MultiPolygon
    },
    "include_stats": false,               # gdalinfo only; compute band statistics
    "read_token":    "eyJ...",            # OPTIONAL: Tapis JWT for private /vsicurl/ reads
    "ckan": {                             # OPTIONAL: if present, register output to CKAN
        "url":        "https://ckan.example.org",
        "token":      "eyJ...",
        "package_id": "my-dataset",
        "extra":      {}
    }
}

Private /vsicurl/ reads
-----------------------
If ``read_token`` is supplied, it is set as the ``X-Tapis-Token`` HTTP header
for /vsicurl/ requests by configuring ``GDAL_HTTP_HEADER_FILE`` (written to a
temp file) for the GDAL subprocess.  The token is NEVER written to logs or
included in error messages.

Output
------
Emits a single JSON object to stdout:

  On success:
  {
    "status": "ok",
    "operation": "<op>",
    "output_path": "<path>",          # absent for gdalinfo
    "gdal_version": "<ver>",
    "metrics": {"duration_ms": <n>},
    "metadata": {...},                 # gdalinfo only
    "registered": {...}                # if ckan block present and op produced a file
  }

  On error:
  {"status": "error", "message": "<scrubbed message>"}
  exits non-zero.

Security
--------
- All params are validated by validators.py BEFORE any subprocess is launched.
- subprocess.run() is always called with shell=False and a list[str] args.
- GDAL_HTTP_HEADER_FILE is used instead of GDAL_HTTP_HEADERS to avoid token
  exposure in process environment listings.
- Tokens are scrubbed from all error messages and logs.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import re
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

from validators import (
    ALLOWED_OPERATIONS,
    validate_clip_geometry,
    validate_compression,
    validate_input_url,
    validate_operation,
    validate_output_name,
    validate_overview_levels,
    validate_target_crs,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Token scrubbing
# ---------------------------------------------------------------------------

_BEARER_RE = re.compile(r"Bearer\s+\S+", re.IGNORECASE)
_JWT_RE = re.compile(r"eyJ[A-Za-z0-9_\-]+\.[A-Za-z0-9_\-]+\.[A-Za-z0-9_\-]*")


def _scrub(text: str) -> str:
    """Remove Bearer tokens and JWTs from *text* before logging/returning."""
    text = _BEARER_RE.sub("Bearer [REDACTED]", text)
    text = _JWT_RE.sub("[REDACTED_JWT]", text)
    return text


# ---------------------------------------------------------------------------
# GDAL version detection
# ---------------------------------------------------------------------------


def _gdal_version() -> str:
    try:
        result = subprocess.run(
            ["gdalinfo", "--version"],
            capture_output=True,
            text=True,
            timeout=10,
            shell=False,
        )
        return result.stdout.strip().split(",")[0]
    except Exception:
        return "unknown"


# ---------------------------------------------------------------------------
# Output directory
# ---------------------------------------------------------------------------


def _output_dir() -> Path:
    out = os.environ.get("OUTPUT_DIR", "/data/out")
    p = Path(out)
    p.mkdir(parents=True, exist_ok=True)
    return p


# ---------------------------------------------------------------------------
# /vsicurl/ path builder
# ---------------------------------------------------------------------------


def _vsicurl(url: str) -> str:
    """Prefix *url* with /vsicurl/ for GDAL HTTP range reads."""
    return f"/vsicurl/{url}"


# ---------------------------------------------------------------------------
# Private read auth: write a GDAL_HTTP_HEADER_FILE to a temp file
# ---------------------------------------------------------------------------


def _make_header_file(token: str) -> str:
    """Write a GDAL_HTTP_HEADER_FILE for X-Tapis-Token and return the path.

    Uses a temp file so the token does not appear in the process environment
    listing.  The caller must delete the file when done.
    """
    fd, path = tempfile.mkstemp(suffix=".hdr", prefix="gdal_auth_")
    with os.fdopen(fd, "w") as fh:
        fh.write(f"X-Tapis-Token: {token}\n")
    return path


# ---------------------------------------------------------------------------
# Operation runners (each returns (stdout_text_or_path, extra_env))
# ---------------------------------------------------------------------------


def _run_gdalinfo(
    vsicurl_path: str,
    include_stats: bool,
    extra_env: dict[str, str],
) -> dict[str, Any]:
    """Run gdalinfo -json and return parsed metadata dict."""
    args: list[str] = ["gdalinfo", "-json"]
    if include_stats:
        args.append("-stats")
    args.append(vsicurl_path)

    env = {**os.environ, **extra_env}
    result = subprocess.run(
        args,
        capture_output=True,
        text=True,
        timeout=int(os.environ.get("GDALINFO_TIMEOUT", "60")),
        shell=False,
        env=env,
    )
    if result.returncode != 0:
        raise RuntimeError(_scrub(result.stderr[:500]))
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise RuntimeError(
            f"gdalinfo returned non-JSON output: {result.stdout[:200]}"
        ) from exc


def _run_reproject(
    vsicurl_path: str,
    output_path: Path,
    target_crs: int,
    extra_env: dict[str, str],
) -> None:
    """Run gdalwarp to reproject to *target_crs* (EPSG integer)."""
    crs_flags = validate_target_crs(target_crs)  # ["-t_srs", "EPSG:<n>"]
    args: list[str] = ["gdalwarp"] + crs_flags + [vsicurl_path, str(output_path)]
    env = {**os.environ, **extra_env}
    result = subprocess.run(
        args,
        capture_output=True,
        text=True,
        timeout=int(os.environ.get("REPROJECT_TIMEOUT", "600")),
        shell=False,
        env=env,
    )
    if result.returncode != 0:
        raise RuntimeError(_scrub(result.stderr[:500]))


def _run_cog(
    vsicurl_path: str,
    output_path: Path,
    compression: str,
    extra_env: dict[str, str],
) -> None:
    """Run gdal_translate to produce a Cloud-Optimized GeoTIFF."""
    comp = validate_compression(compression)
    args: list[str] = [
        "gdal_translate",
        "-of", "COG",
        "-co", f"COMPRESS={comp.upper()}",
        vsicurl_path,
        str(output_path),
    ]
    env = {**os.environ, **extra_env}
    result = subprocess.run(
        args,
        capture_output=True,
        text=True,
        timeout=int(os.environ.get("COG_TIMEOUT", "180")),
        shell=False,
        env=env,
    )
    if result.returncode != 0:
        raise RuntimeError(_scrub(result.stderr[:500]))


def _run_clip(
    vsicurl_path: str,
    output_path: Path,
    clip_geometry: dict[str, Any],
    extra_env: dict[str, str],
) -> None:
    """Run gdalwarp -cutline with a temp GeoJSON file for the clip geometry."""
    validated_geom = validate_clip_geometry(clip_geometry)
    # Wrap in a FeatureCollection so gdalwarp can parse it as OGR datasource
    feature_collection = {
        "type": "FeatureCollection",
        "features": [
            {
                "type": "Feature",
                "geometry": validated_geom,
                "properties": {},
            }
        ],
    }
    # Write to a temp file (server controls the write; never user-supplied path)
    fd, cutline_path = tempfile.mkstemp(suffix=".geojson", prefix="gdal_clip_")
    try:
        with os.fdopen(fd, "w") as fh:
            json.dump(feature_collection, fh)
        args: list[str] = [
            "gdalwarp",
            "-cutline", cutline_path,
            "-crop_to_cutline",
            vsicurl_path,
            str(output_path),
        ]
        env = {**os.environ, **extra_env}
        result = subprocess.run(
            args,
            capture_output=True,
            text=True,
            timeout=int(os.environ.get("CLIP_TIMEOUT", "300")),
            shell=False,
            env=env,
        )
    finally:
        try:
            os.unlink(cutline_path)
        except OSError:
            pass

    if result.returncode != 0:
        raise RuntimeError(_scrub(result.stderr[:500]))


def _run_overviews(
    vsicurl_path: str,
    output_path: Path,
    overview_levels: list[int],
    extra_env: dict[str, str],
) -> None:
    """Copy source via gdal_translate, then build overviews on the copy.

    The source is NEVER mutated (Decision 8 / copy-not-mutate contract).
    """
    levels = validate_overview_levels(overview_levels)
    env = {**os.environ, **extra_env}

    # Step 1: copy source to output_path
    copy_args: list[str] = ["gdal_translate", vsicurl_path, str(output_path)]
    copy_result = subprocess.run(
        copy_args,
        capture_output=True,
        text=True,
        timeout=int(os.environ.get("OVERVIEWS_COPY_TIMEOUT", "180")),
        shell=False,
        env=env,
    )
    if copy_result.returncode != 0:
        raise RuntimeError(
            f"gdal_translate (copy) failed: {_scrub(copy_result.stderr[:500])}"
        )

    # Step 2: build overviews on the copy (never on vsicurl_path)
    levels_str = [str(lvl) for lvl in levels]
    addo_args: list[str] = ["gdaladdo", str(output_path)] + levels_str
    addo_result = subprocess.run(
        addo_args,
        capture_output=True,
        text=True,
        timeout=int(os.environ.get("OVERVIEWS_ADDO_TIMEOUT", "300")),
        shell=False,
        env=env,
    )
    if addo_result.returncode != 0:
        raise RuntimeError(
            f"gdaladdo failed: {_scrub(addo_result.stderr[:500])}"
        )


# ---------------------------------------------------------------------------
# Message reader
# ---------------------------------------------------------------------------


def _read_message() -> dict[str, Any]:
    """Read the JSON message from MSG env var, --message arg, or stdin."""
    # Priority 1: Abaco MSG env var
    msg_env = os.environ.get("MSG", "")
    if msg_env:
        try:
            return json.loads(msg_env)
        except json.JSONDecodeError as exc:
            raise ValueError(f"MSG env var is not valid JSON: {exc}") from exc

    # Priority 2: --message CLI arg
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--message", "-m", default=None)
    args, _ = parser.parse_known_args()
    if args.message:
        try:
            return json.loads(args.message)
        except json.JSONDecodeError as exc:
            raise ValueError(f"--message is not valid JSON: {exc}") from exc

    # Priority 3: stdin
    raw = sys.stdin.read().strip()
    if not raw:
        raise ValueError("No message provided via MSG env, --message, or stdin")
    try:
        return json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError(f"stdin is not valid JSON: {exc}") from exc


# ---------------------------------------------------------------------------
# Main entrypoint
# ---------------------------------------------------------------------------


def main() -> None:
    logging.basicConfig(
        stream=sys.stderr,
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )

    t0 = time.monotonic()

    # --- Parse message ---
    try:
        msg = _read_message()
    except ValueError as exc:
        print(json.dumps({"status": "error", "message": str(exc)}))
        sys.exit(1)

    # --- Validate operation first (before any params) ---
    try:
        op = validate_operation(msg.get("operation", ""))
    except ValueError as exc:
        print(json.dumps({"status": "error", "message": str(exc)}))
        sys.exit(1)

    # --- Validate input_url ---
    try:
        input_url = validate_input_url(msg.get("input_url", ""))
    except ValueError as exc:
        print(json.dumps({"status": "error", "message": str(exc)}))
        sys.exit(1)

    # --- output_name (not required for gdalinfo) ---
    output_name_raw = msg.get("output_name", "")
    params = msg.get("params") or {}
    include_stats = bool(msg.get("include_stats", False))

    # --- read_token for private /vsicurl/ reads ---
    read_token: str = msg.get("read_token") or ""

    # --- ckan registration block (optional) ---
    ckan_block: dict[str, Any] | None = msg.get("ckan")

    gdal_ver = _gdal_version()
    vsicurl_path = _vsicurl(input_url)

    # --- Build extra subprocess env for private reads ---
    extra_env: dict[str, str] = {}
    header_file_path: str = ""
    if read_token:
        header_file_path = _make_header_file(read_token)
        extra_env["GDAL_HTTP_HEADER_FILE"] = header_file_path

    try:
        # -----------------------------------------------------------------
        # Execute the validated operation
        # -----------------------------------------------------------------
        metadata_result: dict[str, Any] | None = None
        output_path: Path | None = None

        if op == "gdalinfo":
            metadata_result = _run_gdalinfo(vsicurl_path, include_stats, extra_env)

        else:
            # All other ops produce an output file — validate output_name
            try:
                out_name = validate_output_name(output_name_raw)
            except ValueError as exc:
                print(json.dumps({"status": "error", "message": str(exc)}))
                sys.exit(1)
            output_path = _output_dir() / out_name

            if op == "reproject":
                target_crs = params.get("target_crs")
                if target_crs is None:
                    print(json.dumps({"status": "error", "message": "params.target_crs is required for reproject"}))
                    sys.exit(1)
                try:
                    _run_reproject(vsicurl_path, output_path, target_crs, extra_env)
                except ValueError as exc:
                    print(json.dumps({"status": "error", "message": str(exc)}))
                    sys.exit(1)

            elif op == "cog":
                compression = params.get("compression", "deflate")
                try:
                    _run_cog(vsicurl_path, output_path, compression, extra_env)
                except ValueError as exc:
                    print(json.dumps({"status": "error", "message": str(exc)}))
                    sys.exit(1)

            elif op == "clip":
                clip_geom = params.get("clip_geometry")
                if clip_geom is None:
                    print(json.dumps({"status": "error", "message": "params.clip_geometry is required for clip"}))
                    sys.exit(1)
                try:
                    _run_clip(vsicurl_path, output_path, clip_geom, extra_env)
                except ValueError as exc:
                    print(json.dumps({"status": "error", "message": str(exc)}))
                    sys.exit(1)

            elif op == "overviews":
                levels = params.get("overview_levels", [2, 4, 8])
                try:
                    _run_overviews(vsicurl_path, output_path, levels, extra_env)
                except ValueError as exc:
                    print(json.dumps({"status": "error", "message": str(exc)}))
                    sys.exit(1)

    except RuntimeError as exc:
        print(json.dumps({"status": "error", "message": _scrub(str(exc))}))
        sys.exit(1)
    except subprocess.TimeoutExpired:
        print(json.dumps({"status": "error", "message": f"GDAL operation timed out: {op}"}))
        sys.exit(1)
    finally:
        # Always clean up the auth header file
        if header_file_path:
            try:
                os.unlink(header_file_path)
            except OSError:
                pass

    duration_ms = int((time.monotonic() - t0) * 1000)

    # -----------------------------------------------------------------
    # Compose success response
    # -----------------------------------------------------------------
    response: dict[str, Any] = {
        "status": "ok",
        "operation": op,
        "gdal_version": gdal_ver,
        "metrics": {"duration_ms": duration_ms},
    }
    if output_path is not None:
        response["output_path"] = str(output_path)
    if metadata_result is not None:
        response["metadata"] = metadata_result

    # -----------------------------------------------------------------
    # Optional CKAN registration (gdal+register mode)
    # -----------------------------------------------------------------
    if ckan_block and output_path is not None and output_path.exists():
        from register_to_ckan import register as ckan_register, _scrub as _ckan_scrub

        ckan_url = ckan_block.get("url", "")
        ckan_token = ckan_block.get("token", "")
        package_id = ckan_block.get("package_id", "")
        extra = ckan_block.get("extra") or {}

        if not ckan_url or not ckan_token or not package_id:
            response["registered"] = {
                "status": "error",
                "message": "ckan block missing url, token, or package_id",
            }
        else:
            try:
                reg_result = ckan_register(
                    output_path=str(output_path),
                    ckan_url=ckan_url,
                    token=ckan_token,
                    package_id=package_id,
                    name=output_path.name,
                    extra_metadata=extra,
                )
                response["registered"] = {"status": "ok", "resource": reg_result}
            except Exception as exc:
                response["registered"] = {
                    "status": "error",
                    "message": _scrub(str(exc)),
                }

    print(json.dumps(response))


if __name__ == "__main__":
    main()
