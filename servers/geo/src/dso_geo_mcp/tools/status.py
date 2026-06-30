"""
Status polling tool — registered ONCE; shared by all geo tools.

``get_execution_status`` polls the Tapis Abaco actor execution API once
(a single HTTP call per MCP tool invocation — the MCP client/model drives
the retry loop).  When the execution is in a terminal state (COMPLETE,
FAILED, or ERROR), it also fetches the actor logs and parses the actor's
structured JSON output.

Execution ID format
-------------------
The Abaco execution ID is stored and returned as-is (plain string, e.g.
``"abc123def456"``).  The actor_id is taken from ``settings.geo_actor_id``
— all executions in this server use the same pre-registered actor.

Token handling
--------------
Per-call ``tapis_token`` argument takes precedence; env fallback
``settings.geo_tapis_token`` is used if the arg is None/empty.  Token is
NEVER stored, logged, or returned to callers.
"""

from __future__ import annotations

import json
import logging
from typing import Any

import fastmcp

from ..audit import log_status_poll, scrub
from ..config import settings
from ..tapis_client import TERMINAL_STATUSES, TapisError, get_execution, get_logs
from ..validators import validate_execution_id

logger = logging.getLogger(__name__)


def register(mcp: fastmcp.FastMCP) -> None:
    """Register the get_execution_status tool with the FastMCP app."""

    @mcp.tool()
    def get_execution_status(
        execution_id: str,
        tapis_token: str | None = None,
    ) -> dict[str, Any]:
        """Poll status of a Tapis Abaco actor execution.

        Makes ONE HTTP call to Tapis — the MCP client/model is responsible
        for calling again if the execution is still running.

        When the execution reaches a terminal state (COMPLETE / FAILED /
        ERROR), also fetches the actor logs and parses the actor's structured
        JSON result.

        Args:
            execution_id: Tapis Abaco execution ID returned by a geo tool
                (e.g. from gdalinfo_extract or reproject_raster).
            tapis_token: Tapis JWT (X-Tapis-Token). Required. Falls back to
                GEO_TAPIS_TOKEN env var if not provided.

        Returns:
            {
                "execution_id": "<id>",
                "status": "SUBMITTED|RUNNING|COMPLETE|FAILED|ERROR",
                "result": {  # only when status is terminal and COMPLETE
                    "status": "ok",
                    "operation": "...",
                    ...  # actor's full JSON output
                },
                "error": "..."  # only when FAILED or ERROR (log content scrubbed)
            }
        """
        # ── Validate execution_id format before URL interpolation ─────────────
        try:
            eid = validate_execution_id(execution_id)
        except ValueError as exc:
            return {"error": str(exc), "execution_id": execution_id}

        # ── Resolve token ─────────────────────────────────────────────────────
        token = (tapis_token or "").strip() or (settings.geo_tapis_token or "")
        if not token:
            return {
                "error": "tapis_token is required for get_execution_status",
                "execution_id": eid,
            }

        actor_id = settings.geo_actor_id
        if not actor_id:
            return {
                "error": "GEO_ACTOR_ID is not configured",
                "execution_id": eid,
            }

        # ── Poll execution status ─────────────────────────────────────────────
        try:
            exec_data = get_execution(
                actor_id=actor_id,
                execution_id=eid,
                token=token,
                tapis_base=settings.tapis_base,
            )
        except TapisError as exc:
            return {
                "error": scrub(str(exc)),
                "execution_id": eid,
            }

        status: str = exec_data.get("status", "UNKNOWN")
        is_terminal = status in TERMINAL_STATUSES

        log_status_poll(
            execution_id=eid,
            status=status,
            terminal=is_terminal,
        )

        result: dict[str, Any] = {
            "execution_id": eid,
            "status": status,
        }

        if not is_terminal:
            return result

        # ── Terminal: fetch and parse actor logs ──────────────────────────────
        try:
            logs_text = get_logs(
                actor_id=actor_id,
                execution_id=eid,
                token=token,
                tapis_base=settings.tapis_base,
            )
        except TapisError as exc:
            result["error"] = scrub(str(exc))
            return result

        # Actor writes a single JSON object to stdout; it appears in logs.
        # Try to parse it; fall back to raw text.
        actor_json: dict[str, Any] | None = None
        for line in logs_text.splitlines():
            line = line.strip()
            if line.startswith("{"):
                try:
                    actor_json = json.loads(line)
                    break
                except json.JSONDecodeError:
                    continue

        if actor_json is None:
            # Try the full text
            try:
                actor_json = json.loads(logs_text.strip())
            except json.JSONDecodeError:
                pass

        if status == "COMPLETE":
            if actor_json is not None:
                result["result"] = actor_json
                # Surface registered resource info at the top level for convenience
                if "registered" in actor_json:
                    result["registered"] = actor_json["registered"]
            else:
                result["result"] = {"raw_logs": logs_text[:2000]}
        else:
            # FAILED or ERROR
            if actor_json is not None:
                result["error"] = actor_json.get("message", logs_text[:500])
            else:
                result["error"] = logs_text[:500]

        return result
