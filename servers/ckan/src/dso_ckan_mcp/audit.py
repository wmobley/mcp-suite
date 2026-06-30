"""
Structured audit logger for Track B write operations.

Every ``dry_run=False`` (live) write attempt — success or failure — emits one
structured log line to stderr via the stdlib logger.  This provides an
append-only audit trail of all writes performed through the MCP server.

Log format
----------
Each line is a key=value record (machine-parseable, human-readable):

    AUDIT tool=schema_create_package ts=2026-06-29T12:34:56Z
          ckan_url=http://localhost:5001 status=201 result_id=abc-123
          args_keys=dataset_type,metadata

Security requirements
---------------------
- Token values MUST NEVER appear in the log.
- File handles / io.IOBase objects MUST be replaced with ``<file>``.
- Any arg key matching ``token``, ``password``, ``secret``,
  ``authorization``, ``tapis_token``, ``x-tapis-token``, or
  ``x_tapis_token`` (case-insensitive) MUST be redacted.
- The ``tapis_token`` tool argument MUST NOT be included in ``call_args``
  passed to ``log_write`` — it should be stripped at the write tool layer
  before audit is called.
"""

from __future__ import annotations

import datetime
import io
import logging
from typing import Any

logger = logging.getLogger(__name__)

# Keys whose values must never appear in logs.
# Matched case-insensitively: exact match OR starts-with for prefix matches.
_SENSITIVE_KEYS = {"token", "password", "secret", "authorization", "api_key"}
# Exact-match keys that contain separators (hyphens/underscores) and would not
# be caught by the prefix-startswith logic above.
_SENSITIVE_EXACT = {"x-tapis-token", "x_tapis_token", "tapis_token"}


def _is_sensitive_key(key: str) -> bool:
    """Return True if *key* looks like a credential field."""
    lower = key.lower()
    if lower in _SENSITIVE_EXACT:
        return True
    return any(lower == s or lower.startswith(s) for s in _SENSITIVE_KEYS)


def _scrub_args(args: dict[str, Any]) -> dict[str, Any]:
    """Return a sanitised copy of *args* safe to include in log output.

    Rules applied:
    - File-handle values (``io.IOBase`` / has a ``read`` attribute) are
      replaced with the string ``"<file>"``.
    - Values for sensitive keys (token, password, secret, authorization)
      are replaced with ``"[REDACTED]"``.
    - All other values are kept as-is (dicts/lists are left structured for
      readability; they will be repr'd when the log line is formatted).

    Parameters
    ----------
    args:
        The raw tool-argument dict.

    Returns
    -------
    dict
        Sanitised copy — the original dict is never mutated.
    """
    result: dict[str, Any] = {}
    for key, value in args.items():
        if _is_sensitive_key(key):
            result[key] = "[REDACTED]"
        elif isinstance(value, io.IOBase) or (
            hasattr(value, "read") and callable(value.read)
        ):
            result[key] = "<file>"
        else:
            result[key] = value
    return result


def log_blocked(tool: str, ckan_url: str, reason: str) -> None:
    """Emit one structured audit line when a live (``dry_run=False``) write is blocked.

    Called by ``_write_gate`` when a write is refused (no token, or prod-guard).
    This is a security-relevant event: every ``dry_run=False`` call must be
    traceable whether it proceeded or was blocked.

    Parameters
    ----------
    tool:
        MCP tool name (e.g. ``"schema_create_package"``).
    ckan_url:
        The resolved CKAN portal base URL.
    reason:
        Short identifier for the block reason (e.g. ``"no_token"`` or
        ``"prod_guard"``).  Token values MUST NEVER appear here.
    """
    ts = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    logger.warning(
        "AUDIT tool=%s ts=%s ckan_url=%s status=blocked reason=%s",
        tool,
        ts,
        ckan_url,
        reason,
    )


def log_write(
    tool: str,
    args: dict[str, Any],
    ckan_url: str,
    status: int | str,
    result_id: str | None,
) -> None:
    """Emit one structured audit line for a live (``dry_run=False``) write.

    This function MUST be called for every live write attempt — both on
    success and on failure.  It logs to ``stderr`` via the stdlib logger
    ``dso_ckan_mcp.audit`` at ``INFO`` level.

    Parameters
    ----------
    tool:
        MCP tool name (e.g. ``"schema_create_package"``).
    args:
        The full argument dict passed to the tool.  Sensitive values and
        file handles are scrubbed before logging.
    ckan_url:
        The resolved CKAN portal base URL (never localhost aliasing).
    status:
        HTTP status code returned by CKAN (e.g. ``200``), or a short
        string like ``"error"`` when no HTTP response was received.
    result_id:
        Created or updated package/resource ID, or ``None`` if unavailable
        (e.g. on error).

    Notes
    -----
    - Token values are NEVER included in the log — ``_scrub_args`` strips
      them, and ``ckan_url`` is the portal URL (not a credential).
    - The log goes to ``stderr`` so it does not pollute the MCP protocol
      stream on ``stdout``.
    """
    ts = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    safe_args = _scrub_args(args)
    # Emit a compact key=value line; dicts are summarised by their top-level
    # keys to avoid log-line explosion on large metadata payloads.
    args_summary = ",".join(safe_args.keys())
    logger.info(
        "AUDIT tool=%s ts=%s ckan_url=%s status=%s result_id=%s args_keys=[%s]",
        tool,
        ts,
        ckan_url,
        status,
        result_id or "None",
        args_summary,
    )
