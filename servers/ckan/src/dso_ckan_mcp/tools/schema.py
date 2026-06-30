"""
Schema tools — dataset type discovery and schema inspection.

These tools are backed by the CKAN scheming extension's Action API actions:
  - scheming_dataset_schema_list
  - scheming_dataset_schema_show

The dataset_type parameter is validated against the portal's allowlist
(via SchemaLoader.validate_type) BEFORE any API call, preventing SSRF and
path/query injection.

Tools registered here (2)
--------------------------
  list_dataset_types      — list dataset_type values available on this portal
  describe_dataset_schema — return the full expanded schema for a type
"""

from __future__ import annotations

from typing import Any

from ..schema_loader import SchemaLoader


def register(mcp: Any, loader: SchemaLoader) -> None:
    """Register all schema tools onto *mcp*.

    Parameters
    ----------
    mcp:
        The FastMCP application instance.
    loader:
        Shared :class:`~dso_ckan_mcp.schema_loader.SchemaLoader`.
    """

    @mcp.tool()
    def list_dataset_types() -> list[str]:
        """List the dataset types available on this CKAN portal.

        Calls the ``scheming_dataset_schema_list`` action.  Results are
        cached in-memory (TTL configured by ``SCHEMA_CACHE_TTL``).

        Returns
        -------
        list[str]
            Dataset type identifiers, e.g.
            ``["dataset", "mint_dataset", "subside_dataset"]``.
        """
        return loader.list_dataset_types()

    @mcp.tool()
    def describe_dataset_schema(dataset_type: str) -> dict[str, Any]:
        """Return the full scheming schema for a dataset type.

        The schema is fetched from the CKAN portal via
        ``scheming_dataset_schema_show`` and returned with all presets
        expanded (each field dict includes ``field_name``, ``label``,
        ``preset``, ``required``, ``help_text``, ``validators``, etc.).

        The *dataset_type* is validated against the portal allowlist before
        any API call is made (prevents SSRF/injection).

        Parameters
        ----------
        dataset_type:
            A dataset type known to this portal (from :func:`list_dataset_types`).
            Example: ``"mint_dataset"``.

        Returns
        -------
        dict
            Full schema dict with ``dataset_fields`` (list) and
            ``resource_fields`` (list).

        Raises
        ------
        ValueError
            If *dataset_type* is not in the portal's allowlist.
        """
        return loader.get_schema(dataset_type)
