"""
TTL-cached loader for CKAN scheming dataset schemas.

Wraps two CKAN scheming API actions:
  - scheming_dataset_schema_list   → list of dataset_type strings
  - scheming_dataset_schema_show   → expanded schema for one type

Security
--------
``validate_type()`` checks *dataset_type* against the (cached) allowlist
**before** any parameter interpolation or API call.  This prevents SSRF and
path-injection via a crafted dataset_type argument.

Cache
-----
Schemas are cached in-memory with a monotonic-clock TTL (default 3600 s).
Calling ``invalidate()`` forces a fresh fetch on the next request.
"""

from __future__ import annotations

import logging
import threading
import time
from typing import Any

from .ckan_client import CKANClient

logger = logging.getLogger(__name__)


class SchemaLoader:
    """Load and cache CKAN scheming schemas.

    Parameters
    ----------
    client:
        Configured :class:`~dso_ckan_mcp.ckan_client.CKANClient` instance.
    ttl:
        Cache time-to-live in seconds (monotonic clock).
    """

    def __init__(self, client: CKANClient, ttl: int = 3600) -> None:
        self._client = client
        self._ttl = ttl

        # Cache entries: (value, expiry_monotonic)
        self._types_cache: tuple[list[str], float] | None = None
        self._schema_cache: dict[str, tuple[dict[str, Any], float]] = {}

        # FastMCP runs sync tools in worker threads; guard cache mutations.
        # A worst-case race only causes a duplicate (idempotent) fetch, but
        # the lock keeps the cache dict mutations atomic.
        self._lock = threading.Lock()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def list_dataset_types(self) -> list[str]:
        """Return the list of dataset_type strings from the CKAN portal.

        Result is cached for *ttl* seconds.  Raises
        :class:`~dso_ckan_mcp.ckan_client.CKANAPIError` on network/API failure.
        """
        with self._lock:
            if self._types_cache is not None:
                value, expiry = self._types_cache
                if time.monotonic() < expiry:
                    logger.debug("schema_loader: returning cached dataset types")
                    return value

        logger.debug("schema_loader: fetching dataset types from portal")
        types: list[str] = self._client.get("scheming_dataset_schema_list")
        with self._lock:
            self._types_cache = (types, time.monotonic() + self._ttl)
        return types

    def get_schema(self, dataset_type: str) -> dict[str, Any]:
        """Return the expanded scheming schema for *dataset_type*.

        The type is validated against the allowlist via
        :meth:`validate_type` before any API call.

        Parameters
        ----------
        dataset_type:
            One of the types returned by :meth:`list_dataset_types`.

        Returns
        -------
        dict
            Full schema dict with ``dataset_fields`` and ``resource_fields``.

        Raises
        ------
        ValueError
            If *dataset_type* is not in the portal's allowlist.
        CKANAPIError
            On network/API failure.
        """
        self.validate_type(dataset_type)  # allowlist check BEFORE param use

        with self._lock:
            cached = self._schema_cache.get(dataset_type)
            if cached is not None:
                value, expiry = cached
                if time.monotonic() < expiry:
                    logger.debug("schema_loader: returning cached schema for %r", dataset_type)
                    return value

        logger.debug("schema_loader: fetching schema for %r from portal", dataset_type)
        schema: dict[str, Any] = self._client.get(
            "scheming_dataset_schema_show", params={"type": dataset_type}
        )
        with self._lock:
            self._schema_cache[dataset_type] = (schema, time.monotonic() + self._ttl)
        return schema

    def validate_type(self, dataset_type: str) -> None:
        """Raise :class:`ValueError` if *dataset_type* is not in the portal allowlist.

        This is called BEFORE any parameter interpolation to prevent SSRF
        and path/query injection.

        Parameters
        ----------
        dataset_type:
            The type string to validate.

        Raises
        ------
        ValueError
            With a helpful message listing the known types.
        """
        known = self.list_dataset_types()
        if dataset_type not in known:
            raise ValueError(
                f"Unknown dataset_type {dataset_type!r}. "
                f"Known types on this portal: {known}"
            )

    def invalidate(self) -> None:
        """Clear all cached data, forcing fresh fetches on the next call."""
        with self._lock:
            self._types_cache = None
            self._schema_cache.clear()
        logger.debug("schema_loader: cache invalidated")
