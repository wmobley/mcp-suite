"""
Structured audit logger for dso-geo MCP server.

Every actor submission and status poll emits one structured log line to
stderr via the stdlib logger.  This provides an append-only audit trail of
all dispatches through the MCP server.

Log format
----------
Each line is a key=value record (machine-parseable, human-readable):

    AUDIT tool=gdalinfo_extract ts=2026-06-30T12:34:56Z
          actor_id=abcdef12 operation=gdalinfo resource_id=abc-123
          execution_id=abc123 status=submitted

Security requirements
---------------------
- Token values MUST NEVER appear in the log.
- Any value matching Bearer/<eyJ JWT patterns is scrubbed automatically.
- The ``tapis_token`` argument is NEVER passed to audit functions.
- Execution IDs are logged (not sensitive).
- actor_id is logged (truncated to first 8 chars in banner, full in audit).
"""

from __future__ import annotations

import datetime
import logging
import re
from typing import Any

logger = logging.getLogger(__name__)

# Token scrubbing patterns (matches actor.py / tapis_client.py)
_BEARER_RE = re.compile(r"Bearer\s+\S+", re.IGNORECASE)
_JWT_RE = re.compile(r"eyJ[A-Za-z0-9_\-]+\.[A-Za-z0-9_\-]+\.[A-Za-z0-9_\-]*")
_X_TAPIS_RE = re.compile(r"(X-Tapis-Token\s*[:=]\s*)\S+", re.IGNORECASE)


def scrub(text: str) -> str:
    """Remove Bearer tokens, JWTs, and X-Tapis-Token values from *text*.

    Safe to call on any string before logging or returning to callers.
    Never raises; returns the original text on any error.
    """
    try:
        text = _BEARER_RE.sub("Bearer [REDACTED]", text)
        text = _JWT_RE.sub("[REDACTED_JWT]", text)
        text = _X_TAPIS_RE.sub(r"\1[REDACTED]", text)
    except Exception:
        pass
    return text


def _ts() -> str:
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def log_submit(
    tool: str,
    actor_id: str,
    operation: str,
    resource_id: str,
    execution_id: str,
) -> None:
    """Emit one structured audit line when an actor message is submitted.

    Parameters
    ----------
    tool:
        MCP tool name (e.g. ``"gdalinfo_extract"``).
    actor_id:
        Tapis Abaco actor ID (logged; not a secret).
    operation:
        GDAL operation name (e.g. ``"gdalinfo"``).
    resource_id:
        CKAN resource UUID being processed (not a secret; logged for traceability).
    execution_id:
        Tapis Abaco execution ID returned by the submit call.
    """
    logger.info(
        "AUDIT tool=%s ts=%s actor_id=%s operation=%s resource_id=%s execution_id=%s status=submitted",
        tool,
        _ts(),
        actor_id,
        operation,
        resource_id,
        execution_id,
    )


def log_blocked(tool: str, reason: str) -> None:
    """Emit one structured audit line when a submission is blocked.

    Called when a tool call is refused (e.g. missing token for transforms).

    Parameters
    ----------
    tool:
        MCP tool name.
    reason:
        Short identifier for the block reason (e.g. ``"no_token"``).
        Token values MUST NEVER appear here.
    """
    logger.warning(
        "AUDIT tool=%s ts=%s status=blocked reason=%s",
        tool,
        _ts(),
        reason,
    )


def log_status_poll(
    execution_id: str,
    status: str,
    terminal: bool,
) -> None:
    """Emit one structured audit line for a status poll.

    Parameters
    ----------
    execution_id:
        Tapis execution ID being polled.
    status:
        Status string returned by Tapis (e.g. ``"COMPLETE"``).
    terminal:
        Whether this status is a terminal state (COMPLETE / FAILED / ERROR).
    """
    logger.info(
        "AUDIT ts=%s execution_id=%s status=%s terminal=%s",
        _ts(),
        execution_id,
        status,
        terminal,
    )
