"""
MCP resources for the DSO CKAN portal.

Resources are read-only content addressed by URI that an MCP client loads on
demand (they are not auto-injected into context like tool results).

Registered resources (1)
------------------------
  ckan://openapi   — the portal's OpenAPI 3.0 spec for the CKAN Action API,
                     fetched live from ``/api-specs/ckan-openapi.json`` and
                     cached in-memory with a TTL.
"""

from __future__ import annotations

import threading
import time
from typing import Any

# Portal path that serves the generated OpenAPI document.
_OPENAPI_PATH = "/api-specs/ckan-openapi.json"


def register(mcp: Any, client: Any, ttl: int = 3600) -> None:
    """Register portal resources onto *mcp*.

    Parameters
    ----------
    mcp:
        The FastMCP application instance.
    client:
        Shared :class:`~dso_ckan_mcp.ckan_client.CKANClient`.
    ttl:
        Cache time-to-live in seconds for the fetched spec.
    """
    cache: dict[str, tuple[Any, float]] = {}
    lock = threading.Lock()

    @mcp.resource("ckan://openapi", mime_type="application/json")
    def openapi_spec() -> dict[str, Any]:
        """The portal's OpenAPI 3.0 spec for the CKAN Action API.

        Use this for the **API shape**: which actions exist, their HTTP
        request/response envelopes, generic Package/Resource fields, and the
        multipart upload request format (ResourceCreateMultipartRequest).

        Two caveats — read before relying on it:

        - It documents the **full** CKAN Action API, including write actions
          (package_create/update/patch, resource_create) that this MCP server
          does NOT wrap as tools. The server only acts through its own tools;
          the spec is informational for those.
        - It does NOT contain this portal's custom scheming fields. Fields like
          ``mint_standard_variables`` and ``temporal_coverage_start/end`` are
          absent here. For the authoritative, per-dataset-type field list (with
          required flags, presets, and help text), call
          ``describe_dataset_schema(dataset_type)`` — that is the source of
          truth for getting a dataset's fields right.
        """
        with lock:
            cached = cache.get("openapi")
            if cached is not None and time.monotonic() < cached[1]:
                return cached[0]
        spec = client.get_json(_OPENAPI_PATH)
        with lock:
            cache["openapi"] = (spec, time.monotonic() + ttl)
        return spec
