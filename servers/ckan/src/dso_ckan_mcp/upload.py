"""
Upload path validation for MCP write tools.

``resolve_upload_path`` is the single entry point.  It enforces all
security checks before any file handle is opened:

1. Upload directory must be configured (``MCP_UPLOAD_DIR`` must be set).
2. The resolved (symlink-expanded) path must live inside the configured
   allowed directory.
3. The path must not match any known-sensitive system prefix even if it
   somehow passed the directory check.
4. The target must be a regular file that exists.
5. The file size must be within the configured limit (checked via
   ``os.path.getsize`` BEFORE opening).

Usage
-----
    from dso_ckan_mcp.upload import resolve_upload_path

    path = resolve_upload_path(
        path="/tmp/mcp-uploads/data.csv",
        allowed_dir="/tmp/mcp-uploads",
        max_mb=90,
    )
    with open(path, "rb") as fh:
        ...

All checks are pure filesystem operations — no network calls, no side
effects.  The function raises ``ValueError`` for every security violation.
"""

from __future__ import annotations

import os
from pathlib import Path

# System path prefixes that are never safe to upload, even if they somehow
# resolve inside the allowed directory (e.g. via misconfigured MCP_UPLOAD_DIR).
_SENSITIVE_PREFIXES: list[str] = [
    "/etc",
    "/root",
    "/proc",
    "/sys",
    "/dev",
    "/run",
    # Expand ~ for common credential dirs.
    str(Path("~/.ssh").expanduser()),
    str(Path("~/.aws").expanduser()),
    str(Path("~/.gnupg").expanduser()),
]


def resolve_upload_path(
    path: str,
    allowed_dir: str | None,
    max_mb: int,
) -> Path:
    """Validate and resolve an upload file path.

    Parameters
    ----------
    path:
        The upload file path as provided by the caller (may contain ``..``
        components or symlinks — both are resolved before comparison).
    allowed_dir:
        The configured allowed upload directory (``MCP_UPLOAD_DIR``).
        If ``None`` or empty, uploads are disabled and a ``ValueError``
        is raised immediately.
    max_mb:
        Maximum allowed file size in megabytes.  The size is checked via
        ``os.path.getsize`` BEFORE the file is opened.  Must be < CKAN's
        100 MB limit; the default is 90 MB (see ``MCP_MAX_UPLOAD_MB``).

    Returns
    -------
    Path
        The validated, fully resolved (symlink-free) ``pathlib.Path``.

    Raises
    ------
    ValueError
        For any security or feasibility violation:
        - uploads disabled (``allowed_dir`` is None/empty)
        - path escapes the allowed directory (traversal / symlink escape)
        - path matches a known-sensitive prefix
        - file does not exist or is not a regular file
        - file size exceeds ``max_mb``
    """
    # ── 1. Upload directory must be configured ──────────────────────────────
    if not allowed_dir:
        raise ValueError(
            "Uploads are disabled: MCP_UPLOAD_DIR is not configured. "
            "Set MCP_UPLOAD_DIR to an allowed directory path to enable uploads."
        )

    # Resolve both paths to defeat symlinks and ``..`` components.
    real_path = Path(os.path.realpath(path))
    real_allowed = Path(os.path.realpath(allowed_dir))

    # ── 2. Path must be within allowed_dir ──────────────────────────────────
    try:
        real_path.relative_to(real_allowed)
    except ValueError:
        raise ValueError(
            f"Upload path {path!r} resolves to {real_path} which is outside "
            f"the allowed upload directory {real_allowed}. "
            "Set MCP_UPLOAD_DIR to include this path."
        )

    # ── 3. Reject known-sensitive system prefixes ────────────────────────────
    real_path_str = str(real_path)
    for prefix in _SENSITIVE_PREFIXES:
        if real_path_str == prefix or real_path_str.startswith(prefix + "/"):
            raise ValueError(
                f"Upload path {path!r} resolves to a sensitive system path "
                f"({real_path_str!r}). Uploads from this location are prohibited."
            )

    # ── 4. File must exist and be a regular file ─────────────────────────────
    if not real_path.exists():
        raise ValueError(
            f"Upload path {path!r} does not exist (resolved: {real_path})."
        )
    if not real_path.is_file():
        raise ValueError(
            f"Upload path {path!r} is not a regular file (resolved: {real_path})."
        )

    # ── 5. Size check BEFORE open() ──────────────────────────────────────────
    max_bytes = max_mb * 1024 * 1024
    file_size = os.path.getsize(real_path)
    if file_size > max_bytes:
        file_mb = file_size / (1024 * 1024)
        raise ValueError(
            f"Upload file {path!r} is {file_mb:.1f} MB, which exceeds the "
            f"configured limit of {max_mb} MB (MCP_MAX_UPLOAD_MB). "
            "Reduce the file size or increase MCP_MAX_UPLOAD_MB (must remain "
            "strictly below CKAN's 100 MB limit)."
        )

    return real_path
