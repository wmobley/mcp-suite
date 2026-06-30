"""
register_to_ckan.py — downstream pipeline task: register an output file to CKAN.

Calls CKAN's ``resource_create`` action via multipart POST, using the same
``X-Tapis-Token`` auth pattern as dso-ckan's CKANClient.  Tokens are NEVER
included in log messages or raised exceptions.

Usage (standalone script, called by actor after writing output):
    python3 register_to_ckan.py \
        --output-path /data/out/reprojected.tif \
        --ckan-url https://ckan.example.org \
        --token <tapis-jwt> \
        --package-id my-dataset \
        --name "reprojected.tif" \
        --extra '{"provenance": {...}}'

Or imported and called programmatically:
    from register_to_ckan import register
    result = register(
        output_path="/data/out/reprojected.tif",
        ckan_url="https://ckan.example.org",
        token="<tapis-jwt>",
        package_id="my-dataset",
        name="reprojected.tif",
        extra_metadata={"provenance": {...}},
    )
"""

from __future__ import annotations

import json
import logging
import re
import sys
import urllib.error
import urllib.request
import uuid
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


def _encode_multipart(
    fields: dict[str, Any], file_field: str, filename: str, file_bytes: bytes,
    content_type: str = "image/tiff",
) -> tuple[bytes, str]:
    """Build a multipart/form-data body (stdlib only). Returns (body, boundary)."""
    boundary = "----dso-geo-" + uuid.uuid4().hex
    crlf = b"\r\n"
    b = bytearray()
    for k, v in fields.items():
        b += b"--" + boundary.encode() + crlf
        b += f'Content-Disposition: form-data; name="{k}"'.encode() + crlf + crlf
        b += str(v).encode() + crlf
    b += b"--" + boundary.encode() + crlf
    b += (
        f'Content-Disposition: form-data; name="{file_field}"; filename="{filename}"'
    ).encode() + crlf
    b += f"Content-Type: {content_type}".encode() + crlf + crlf
    b += file_bytes + crlf
    b += b"--" + boundary.encode() + b"--" + crlf
    return bytes(b), boundary

# Regex patterns to scrub from error messages (tokens, JWTs)
_TOKEN_RE = re.compile(r"Bearer\s+\S+", re.IGNORECASE)
_JWT_RE = re.compile(r"eyJ[A-Za-z0-9_\-]+\.[A-Za-z0-9_\-]+\.[A-Za-z0-9_\-]*")

_DEFAULT_TIMEOUT = 60  # seconds


def _scrub(text: str) -> str:
    """Remove Bearer tokens and JWT patterns from *text*."""
    text = _TOKEN_RE.sub("Bearer [REDACTED]", text)
    text = _JWT_RE.sub("[REDACTED_JWT]", text)
    return text


def register(
    output_path: str,
    ckan_url: str,
    token: str,
    package_id: str,
    name: str,
    extra_metadata: dict[str, Any] | None = None,
    timeout: int = _DEFAULT_TIMEOUT,
) -> dict[str, Any]:
    """Upload *output_path* to CKAN as a new resource in *package_id*.

    Parameters
    ----------
    output_path:
        Absolute path to the local output file to upload.
    ckan_url:
        Root CKAN URL (e.g. ``https://ckan.example.org``).
    token:
        Tapis JWT sent as ``X-Tapis-Token`` header.  Never logged.
    package_id:
        CKAN dataset (package) ID or name to attach the resource to.
    name:
        Display name for the new CKAN resource.
    extra_metadata:
        Optional dict merged into the resource ``create`` payload (e.g.
        provenance fields, format, description).
    timeout:
        HTTP request timeout in seconds.

    Returns
    -------
    dict
        The ``result`` dict from CKAN's ``resource_create`` response.

    Raises
    ------
    RuntimeError
        On HTTP error or non-JSON response; token is scrubbed from the message.
    FileNotFoundError
        If *output_path* does not exist.
    """
    path = Path(output_path)
    if not path.exists():
        raise FileNotFoundError(f"output_path does not exist: {output_path}")

    base = ckan_url.rstrip("/")
    url = f"{base}/api/3/action/resource_create"

    data: dict[str, Any] = {
        "package_id": package_id,
        "name": name,
        "format": "GeoTIFF",
    }
    if extra_metadata:
        # Flatten extra_metadata into the data payload; CKAN stores extras.
        data.update(extra_metadata)

    # Provenance note: token is NOT included in data or logs.
    logger.info(
        "Registering resource to CKAN: package_id=%s name=%s path=%s",
        package_id,
        name,
        output_path,
    )

    file_bytes = path.read_bytes()
    payload, boundary = _encode_multipart(data, "upload", path.name, file_bytes)
    req = urllib.request.Request(url, data=payload, method="POST")
    req.add_header("Content-Type", f"multipart/form-data; boundary={boundary}")
    req.add_header("X-Tapis-Token", token)

    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            text = resp.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as exc:
        scrubbed_body = _scrub(exc.read().decode("utf-8", "replace")[:500])
        raise RuntimeError(
            f"CKAN resource_create returned HTTP {exc.code}: {scrubbed_body}"
        ) from exc
    except urllib.error.URLError as exc:
        # Scrub any token-like text that might appear in the exception.
        raise RuntimeError(
            f"CKAN resource_create request failed: {_scrub(str(exc))}"
        ) from exc

    try:
        body = json.loads(text)
    except Exception as exc:
        raise RuntimeError(
            f"CKAN resource_create returned non-JSON: {_scrub(text[:200])}"
        ) from exc

    if not body.get("success"):
        error = body.get("error", {})
        msg = _scrub(error.get("message", str(error)))
        raise RuntimeError(f"CKAN resource_create failed: {msg}")

    result = body.get("result", {})
    logger.info(
        "Resource registered: id=%s url=%s",
        result.get("id"),
        result.get("url"),
    )
    return result


# ---------------------------------------------------------------------------
# CLI entry point (called by actor as a downstream task)
# ---------------------------------------------------------------------------


def _main() -> None:
    import argparse

    parser = argparse.ArgumentParser(
        description="Register a GDAL output file to CKAN as a new resource."
    )
    parser.add_argument("--output-path", required=True)
    parser.add_argument("--ckan-url", required=True)
    parser.add_argument("--token", required=True)
    parser.add_argument("--package-id", required=True)
    parser.add_argument("--name", required=True)
    parser.add_argument(
        "--extra",
        default="{}",
        help="JSON string of extra metadata fields (e.g. provenance).",
    )
    args = parser.parse_args()

    try:
        extra = json.loads(args.extra)
    except json.JSONDecodeError as exc:
        print(json.dumps({"status": "error", "message": f"--extra is not valid JSON: {exc}"}))
        sys.exit(1)

    try:
        result = register(
            output_path=args.output_path,
            ckan_url=args.ckan_url,
            token=args.token,
            package_id=args.package_id,
            name=args.name,
            extra_metadata=extra,
        )
        print(json.dumps({"status": "ok", "resource": result}))
    except Exception as exc:
        print(json.dumps({"status": "error", "message": _scrub(str(exc))}))
        sys.exit(1)


if __name__ == "__main__":
    logging.basicConfig(stream=sys.stderr, level=logging.INFO)
    _main()
