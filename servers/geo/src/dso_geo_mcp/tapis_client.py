"""
Tapis Abaco HTTP client for dso-geo.

Provides three thin wrappers around the Abaco REST API:
  - submit_message(actor_id, message_dict, token) → execution_id
  - get_execution(execution_id, token) → {status, ...}
  - get_logs(execution_id, token) → logs string

All functions use a fresh requests.Session per call (stateless stdio server).
Tokens are passed as ``X-Tapis-Token`` headers; scrubbed from all error
messages before they are raised or returned to callers.

Abaco API shape (portals.tapis.io):
  POST /v3/actors/{actor_id}/messages
    body:  {"message": "<json-string>"}
    returns: {"result": {"execution_id": "..."}, ...}

  GET /v3/actors/{actor_id}/executions/{execution_id}
    returns: {"result": {"status": "...", ...}, ...}

  GET /v3/actors/{actor_id}/executions/{execution_id}/logs
    returns: {"result": {"logs": "..."}, ...}
"""

from __future__ import annotations

import json
import logging
from typing import Any

import requests

from .audit import scrub

logger = logging.getLogger(__name__)

# Terminal execution statuses (Abaco uses these names)
TERMINAL_STATUSES = frozenset(["COMPLETE", "FAILED", "ERROR"])


class TapisError(Exception):
    """Raised when the Tapis API returns an error or unexpected response.

    The message is always scrubbed of tokens before being raised.
    """


def _headers(token: str) -> dict[str, str]:
    return {"X-Tapis-Token": token, "Content-Type": "application/json"}


def _scrub_error(text: str) -> str:
    """Scrub tokens from an error message before raising or logging."""
    return scrub(text)


def submit_message(
    actor_id: str,
    message_dict: dict[str, Any],
    token: str,
    tapis_base: str,
) -> str:
    """POST a message to the Abaco actor and return the execution_id.

    Parameters
    ----------
    actor_id:
        Tapis Abaco actor ID (e.g. ``"abcdef1234567890"``).
    message_dict:
        The validated message payload dict.  It will be JSON-serialised and
        wrapped in ``{"message": "<json string>"}`` per the Abaco contract.
    token:
        Tapis JWT (``X-Tapis-Token`` header).  Never logged.
    tapis_base:
        Base URL of the Tapis tenant (e.g. ``"https://portals.tapis.io"``).

    Returns
    -------
    str
        The Abaco execution ID (e.g. ``"abc123def456"``).

    Raises
    ------
    TapisError
        On HTTP error or unexpected response shape.  Tokens are scrubbed.
    """
    url = f"{tapis_base}/v3/actors/{actor_id}/messages"
    body = {"message": json.dumps(message_dict)}

    session = requests.Session()
    try:
        resp = session.post(
            url,
            json=body,
            headers=_headers(token),
            timeout=30,
        )
    except requests.RequestException as exc:
        raise TapisError(_scrub_error(f"Tapis submit request failed: {exc}")) from exc
    finally:
        session.close()

    if not resp.ok:
        raise TapisError(
            _scrub_error(
                f"Tapis submit returned HTTP {resp.status_code}: {resp.text[:300]}"
            )
        )

    try:
        data = resp.json()
        execution_id = data["result"]["execution_id"]
    except (KeyError, ValueError) as exc:
        raise TapisError(
            _scrub_error(f"Unexpected Tapis submit response shape: {resp.text[:300]}")
        ) from exc

    return execution_id


def get_execution(
    actor_id: str,
    execution_id: str,
    token: str,
    tapis_base: str,
) -> dict[str, Any]:
    """GET the execution status dict from Abaco.

    Parameters
    ----------
    actor_id:
        Tapis Abaco actor ID.
    execution_id:
        Tapis execution ID returned by submit_message.
    token:
        Tapis JWT.  Never logged.
    tapis_base:
        Base URL of the Tapis tenant.

    Returns
    -------
    dict
        The ``result`` block from the Tapis response
        (e.g. ``{"status": "COMPLETE", "id": "...", ...}``).

    Raises
    ------
    TapisError
        On HTTP error or unexpected response shape.  Tokens are scrubbed.
    """
    url = f"{tapis_base}/v3/actors/{actor_id}/executions/{execution_id}"

    session = requests.Session()
    try:
        resp = session.get(
            url,
            headers=_headers(token),
            timeout=30,
        )
    except requests.RequestException as exc:
        raise TapisError(_scrub_error(f"Tapis get_execution request failed: {exc}")) from exc
    finally:
        session.close()

    if not resp.ok:
        raise TapisError(
            _scrub_error(
                f"Tapis get_execution returned HTTP {resp.status_code}: {resp.text[:300]}"
            )
        )

    try:
        data = resp.json()
        return data["result"]
    except (KeyError, ValueError) as exc:
        raise TapisError(
            _scrub_error(f"Unexpected Tapis execution response shape: {resp.text[:300]}")
        ) from exc


def get_logs(
    actor_id: str,
    execution_id: str,
    token: str,
    tapis_base: str,
) -> str:
    """GET the actor logs for a terminal execution.

    The logs contain the actor's structured JSON output (written to stdout
    by actor.py).  Tokens are scrubbed from the returned string.

    Parameters
    ----------
    actor_id:
        Tapis Abaco actor ID.
    execution_id:
        Tapis execution ID.
    token:
        Tapis JWT.  Never logged.
    tapis_base:
        Base URL of the Tapis tenant.

    Returns
    -------
    str
        The raw logs text from ``result.logs`` (may contain JSON).
        Tokens are scrubbed before returning.

    Raises
    ------
    TapisError
        On HTTP error or unexpected response shape.  Tokens are scrubbed.
    """
    url = f"{tapis_base}/v3/actors/{actor_id}/executions/{execution_id}/logs"

    session = requests.Session()
    try:
        resp = session.get(
            url,
            headers=_headers(token),
            timeout=30,
        )
    except requests.RequestException as exc:
        raise TapisError(_scrub_error(f"Tapis get_logs request failed: {exc}")) from exc
    finally:
        session.close()

    if not resp.ok:
        raise TapisError(
            _scrub_error(
                f"Tapis get_logs returned HTTP {resp.status_code}: {resp.text[:300]}"
            )
        )

    try:
        data = resp.json()
        raw_logs = data["result"]["logs"]
    except (KeyError, ValueError) as exc:
        raise TapisError(
            _scrub_error(f"Unexpected Tapis logs response shape: {resp.text[:300]}")
        ) from exc

    # Scrub tokens from log content before returning (actor may echo partial errors)
    return scrub(raw_logs)
