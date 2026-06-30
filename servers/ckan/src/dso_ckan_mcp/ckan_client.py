"""
Thin wrapper around the CKAN Action API.

Security requirements
---------------------
- Token values (Tapis JWTs, Authorization values) are NEVER logged or included
  in exceptions.  The log calls here deliberately emit only the URL and params
  (not headers), so the token cannot leak.  ``_scrub(headers)`` may be applied
  by any future code that logs/repr's request headers.
- Read path is fully anonymous: the shared ``_session`` carries NO auth header,
  so GET calls (``get``, ``get_json``) never send the token.
- Writes use a fresh ``requests.Session`` per ``post()`` call.  The caller
  supplies an optional Tapis OAuth2 JWT via the ``token`` parameter; it is sent
  as the ``X-Tapis-Token`` header (required by the portal's ``ckanext-oauth2``
  plugin).  This keeps the token off read calls AND makes concurrent writes
  thread-safe (no shared mutable session).

Authentication model
--------------------
The portal authenticates write calls via the ``ckanext-oauth2`` plugin, which
accepts a Tapis OAuth2 JWT in the ``X-Tapis-Token`` header (also accepted in
``Authorization: Bearer <jwt>``).  Raw CKAN API tokens are NOT used for writes.
The token is supplied per-call by the MCP tool layer or falls back to the env
var ``CKAN_API_TOKEN`` (also treated as a Tapis JWT).  Reads are always
anonymous — no token is ever sent on GET calls.

Usage
-----
    client = CKANClient(base_url="http://localhost:5001")
    result = client.get("package_search", params={"q": "*:*", "rows": 5})
    result = client.post("package_create", data={...}, token="<tapis-jwt>")
"""

from __future__ import annotations

import logging
from typing import Any

import requests

logger = logging.getLogger(__name__)

# Default network timeout for all CKAN requests (seconds).
_DEFAULT_TIMEOUT = 15


class CKANAPIError(Exception):
    """Raised when the CKAN Action API returns success=false or an HTTP error.

    The string representation never includes the Authorization header value.
    """

    def __init__(self, action: str, status_code: int, message: str) -> None:
        self.action = action
        self.status_code = status_code
        self.message = message
        super().__init__(self.__str__())

    def __str__(self) -> str:
        return f"CKANAPIError(action={self.action!r}, status={self.status_code}, message={self.message!r})"


def _scrub(headers: dict[str, str]) -> dict[str, str]:
    """Return a copy of *headers* with credential values redacted.

    Only the value is replaced; the key is preserved so that callers can
    still see that an auth header was present.  Apply this helper whenever
    request headers are included in log output or exception messages.

    Redacted header names (case-insensitive):
    - ``Authorization``
    - ``X-Tapis-Token``

    >>> _scrub({"Authorization": "super-secret", "Content-Type": "application/json"})
    {'Authorization': '[REDACTED]', 'Content-Type': 'application/json'}
    >>> _scrub({"X-Tapis-Token": "jwt-value", "Accept": "application/json"})
    {'X-Tapis-Token': '[REDACTED]', 'Accept': 'application/json'}
    >>> _scrub({})
    {}
    """
    _REDACT_HEADERS = {"authorization", "x-tapis-token"}
    scrubbed = dict(headers)
    for key in list(scrubbed):
        if key.lower() in _REDACT_HEADERS:
            scrubbed[key] = "[REDACTED]"
    return scrubbed


class CKANClient:
    """HTTP client for the CKAN Action API.

    Parameters
    ----------
    base_url:
        Root URL of the CKAN portal (e.g. ``http://localhost:5001``).
    api_token:
        Optional Tapis OAuth2 JWT used as an env-level fallback for write
        requests.  When provided it is used as ``X-Tapis-Token`` on POST
        (write) requests only, via a fresh per-call session.  GET requests
        (``get``, ``get_json``) are always anonymous — the token is never
        sent on read calls.  A per-call ``token`` argument to :meth:`post`
        takes precedence over this fallback.
    timeout:
        Per-request timeout in seconds.
    """

    def __init__(
        self,
        base_url: str,
        api_token: str | None = None,
        timeout: int = _DEFAULT_TIMEOUT,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._api_token = api_token
        self._timeout = timeout
        # Shared session for anonymous reads ONLY — no Authorization header.
        self._session = requests.Session()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def get(self, action: str, params: dict[str, Any] | None = None) -> Any:
        """Call a CKAN Action API action via GET.

        Parameters
        ----------
        action:
            CKAN action name (e.g. ``package_search``).
        params:
            Query parameters forwarded to CKAN.

        Returns
        -------
        Any
            The ``result`` field from the CKAN JSON envelope.

        Raises
        ------
        CKANAPIError
            When the response envelope has ``success=false`` or the HTTP
            status is 4xx/5xx.
        """
        url = f"{self._base_url}/api/3/action/{action}"
        # Log without Authorization value.
        logger.debug("GET %s params=%s", url, params)
        try:
            resp = self._session.get(url, params=params, timeout=self._timeout)
        except requests.RequestException as exc:
            raise CKANAPIError(action, 0, str(exc)) from exc

        return self._parse(action, resp)

    def post(
        self,
        action: str,
        data: dict[str, Any] | None = None,
        files: dict[str, Any] | None = None,
        token: str | None = None,
    ) -> Any:
        """Call a CKAN Action API action via POST.

        Authentication uses the Tapis OAuth2 ``ckanext-oauth2`` model: the
        effective token (``token`` arg, or the ``api_token`` env fallback) is
        sent as the ``X-Tapis-Token`` header.  If no effective token is
        available, the POST is made without any auth header (the caller is
        responsible for ensuring this is intentional).

        Parameters
        ----------
        action:
            CKAN action name (e.g. ``package_create``).
        data:
            Form or JSON data.
        files:
            File-like objects for multipart upload (e.g.
            ``{"upload": open(path, "rb")}``).
        token:
            Per-call Tapis OAuth2 JWT.  Takes precedence over the
            ``api_token`` env fallback stored on the client.  Never stored,
            never logged.

        Returns
        -------
        Any
            The ``result`` field from the CKAN JSON envelope.

        Raises
        ------
        CKANAPIError
            When the response envelope has ``success=false`` or the HTTP
            status is 4xx/5xx.
        """
        url = f"{self._base_url}/api/3/action/{action}"
        logger.debug("POST %s", url)
        # Compute effective token: per-call arg wins over env fallback.
        effective = token or self._api_token
        # Use a fresh per-call session so the token is NOT shared with the
        # anonymous read session and concurrent writes are thread-safe.
        try:
            with requests.Session() as s:
                if effective:
                    s.headers["X-Tapis-Token"] = effective
                if files:
                    resp = s.post(url, data=data, files=files, timeout=self._timeout)
                else:
                    resp = s.post(url, json=data, timeout=self._timeout)
        except requests.RequestException as exc:
            raise CKANAPIError(action, 0, str(exc)) from exc

        return self._parse(action, resp)

    def get_json(self, path: str) -> Any:
        """GET an arbitrary portal path and return parsed JSON (no envelope).

        Unlike :meth:`get`, this does not target ``/api/3/action`` and does
        not unwrap a CKAN ``success``/``result`` envelope.  Used for portal
        endpoints such as the OpenAPI spec at ``/api-specs/...``.

        Parameters
        ----------
        path:
            Path relative to the portal root (leading slash optional).

        Raises
        ------
        CKANAPIError
            On HTTP error, non-JSON response, or network failure.
        """
        url = f"{self._base_url}/{path.lstrip('/')}"
        logger.debug("GET %s", url)
        try:
            resp = self._session.get(url, timeout=self._timeout)
        except requests.RequestException as exc:
            raise CKANAPIError(path, 0, str(exc)) from exc
        if not resp.ok:
            raise CKANAPIError(path, resp.status_code, resp.text[:500])
        try:
            return resp.json()
        except Exception as exc:
            raise CKANAPIError(
                path, resp.status_code, f"Non-JSON response: {resp.text[:200]}"
            ) from exc

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _parse(self, action: str, resp: requests.Response) -> Any:
        """Parse the CKAN JSON envelope and raise CKANAPIError on failure.

        CKAN returns HTTP 200 for many logical errors; we always check
        the ``success`` field in the envelope.
        """
        # Raise on HTTP-level errors first.
        if not resp.ok:
            # Try to extract CKAN error message from body.
            try:
                body = resp.json()
                msg = body.get("error", {}).get("message", resp.text[:500])
            except Exception:
                msg = resp.text[:500]
            raise CKANAPIError(action, resp.status_code, msg)

        try:
            body = resp.json()
        except Exception as exc:
            raise CKANAPIError(action, resp.status_code, f"Non-JSON response: {resp.text[:200]}") from exc

        if not body.get("success"):
            error = body.get("error", {})
            msg = error.get("message", str(error))
            raise CKANAPIError(action, resp.status_code, msg)

        return body.get("result")
