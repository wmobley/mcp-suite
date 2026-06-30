"""
Read tools — anonymous CKAN Action API wrappers.

All tools in this module work without a CKAN API token.  They call
the public CKAN Action API and return plain dicts or lists that FastMCP
serialises to JSON for the MCP client.

Row / limit caps
----------------
Result-count parameters are capped server-side at 1000 to prevent runaway
requests: ``rows`` (package_search, find_relevant_datasets) is clamped to
1..1000, and ``limit`` (organization_list, group_list) is capped at 1000 when
supplied.  CKAN itself may return fewer results depending on its own
configuration.

Tools registered here (8)
--------------------------
  package_search          — full-text search with facets and pagination
  package_show            — fetch a single dataset by ID or name
  find_relevant_datasets  — relevance-ranked shortcut for package_search
  resource_show           — fetch a single resource by UUID
  organization_list       — list all organisations
  organization_show       — fetch a single organisation
  group_list              — list all groups
  get_capabilities        — portal version, title, and active extensions
"""

from __future__ import annotations

from typing import Any

from ..ckan_client import CKANClient

_MAX_ROWS = 1000


def register(mcp: Any, client: CKANClient) -> None:
    """Register all read tools onto *mcp*.

    Parameters
    ----------
    mcp:
        The FastMCP application instance.
    client:
        Shared :class:`~dso_ckan_mcp.ckan_client.CKANClient`.
    """

    @mcp.tool()
    def package_search(
        q: str = "*:*",
        fq: list[str] | str | None = None,
        rows: int = 10,
        start: int = 0,
        sort: str = "score desc",
        include_resources: bool = False,
    ) -> dict[str, Any]:
        """Search datasets on the CKAN portal.

        Performs a full-text search with optional filter queries.  Results
        include dataset metadata, facets, and the total match count.

        By default each hit's ``resources`` list is omitted (only the
        ``num_resources`` count is kept) — a single dataset can carry
        hundreds of resources, which would otherwise dominate the response.
        Use ``package_show(id)`` to get a dataset's full resource list, or
        set ``include_resources=True`` to keep them inline.

        Parameters
        ----------
        q:
            Solr query string (default ``*:*`` = all datasets).
        fq:
            Optional filter query, either a single string
            (``"owner_org:water-team dataset_type:mint_dataset"``) or a list
            of clauses (``["owner_org:water-team", "dataset_type:mint_dataset"]``)
            which are joined with a space.
        rows:
            Number of results to return (clamped to 1..1000).
        start:
            Pagination offset.
        sort:
            Solr sort expression (e.g. ``"score desc"`` or
            ``"metadata_modified desc"``).
        include_resources:
            When True, keep each hit's full ``resources`` list.  Default
            False returns a lean, list-friendly result.

        Returns
        -------
        dict
            ``{"count": int, "results": [...], "facets": {...}}``
        """
        rows = max(1, min(rows, _MAX_ROWS))
        params: dict[str, Any] = {"q": q, "rows": rows, "start": start, "sort": sort}
        if fq is not None:
            params["fq"] = " ".join(fq) if isinstance(fq, list) else fq
        result = client.get("package_search", params=params)
        if not include_resources and isinstance(result, dict):
            for hit in result.get("results", []):
                if isinstance(hit, dict):
                    hit.pop("resources", None)
        return result

    @mcp.tool()
    def package_show(id: str) -> dict[str, Any]:
        """Fetch full metadata for a single dataset.

        Parameters
        ----------
        id:
            Dataset ID (UUID) or name (slug).

        Returns
        -------
        dict
            Full CKAN package dict including the ``resources`` list.
        """
        return client.get("package_show", params={"id": id})

    @mcp.tool()
    def find_relevant_datasets(query_text: str, limit: int = 10) -> list[dict[str, Any]]:
        """Find datasets ranked by relevance to a natural-language query.

        A convenience wrapper around ``package_search`` that sorts by
        Solr relevance score.

        Parameters
        ----------
        query_text:
            Free-text query (e.g. ``"groundwater Texas rainfall"``).
        limit:
            Maximum number of results to return (clamped to 1..1000).

        Returns
        -------
        list
            List of dataset dicts, most relevant first.
        """
        limit = max(1, min(limit, _MAX_ROWS))
        result = client.get(
            "package_search",
            params={"q": query_text, "rows": limit, "sort": "score desc"},
        )
        return result.get("results", [])

    @mcp.tool()
    def resource_show(id: str) -> dict[str, Any]:
        """Fetch metadata for a single dataset resource.

        Parameters
        ----------
        id:
            Resource UUID.

        Returns
        -------
        dict
            CKAN resource dict (name, url, format, size, created, etc.).
        """
        return client.get("resource_show", params={"id": id})

    @mcp.tool()
    def organization_list(
        all_fields: bool = False,
        limit: int | None = None,
        offset: int | None = None,
    ) -> list[Any]:
        """List all organisations on the portal.

        Parameters
        ----------
        all_fields:
            When True, return full organisation dicts instead of name strings.
        limit:
            Maximum number of organisations to return.
        offset:
            Pagination offset.

        Returns
        -------
        list
            List of organisation names (strings) or full dicts.
        """
        params: dict[str, Any] = {"all_fields": all_fields}
        if limit is not None:
            params["limit"] = min(limit, _MAX_ROWS)
        if offset is not None:
            params["offset"] = offset
        return client.get("organization_list", params=params)

    @mcp.tool()
    def organization_show(id: str, include_datasets: bool = False) -> dict[str, Any]:
        """Fetch metadata for a single organisation.

        Parameters
        ----------
        id:
            Organisation ID or name.
        include_datasets:
            When True, include a list of the organisation's datasets.

        Returns
        -------
        dict
            Organisation dict (name, title, description, image_url, etc.).
        """
        return client.get(
            "organization_show",
            params={"id": id, "include_datasets": include_datasets},
        )

    @mcp.tool()
    def group_list(
        all_fields: bool = False,
        sort: str | None = None,
        limit: int | None = None,
        offset: int | None = None,
    ) -> list[Any]:
        """List all groups on the portal.

        Parameters
        ----------
        all_fields:
            When True, return full group dicts instead of name strings.
        sort:
            Sort field (e.g. ``"name asc"``).
        limit:
            Maximum number of groups to return.
        offset:
            Pagination offset.

        Returns
        -------
        list
            List of group names (strings) or full dicts.
        """
        params: dict[str, Any] = {"all_fields": all_fields}
        if sort is not None:
            params["sort"] = sort
        if limit is not None:
            params["limit"] = min(limit, _MAX_ROWS)
        if offset is not None:
            params["offset"] = offset
        return client.get("group_list", params=params)

    @mcp.tool()
    def get_capabilities() -> dict[str, Any]:
        """Return portal capabilities derived from the CKAN status endpoint.

        Queries ``status_show`` and returns the CKAN version, site title,
        and installed extension list.  Useful for understanding what
        dataset types and features are available on this portal.

        Returns
        -------
        dict
            ``{"ckan_version": str, "site_title": str, "extensions": [...], ...}``
        """
        status = client.get("status_show")
        # Return the full status dict; useful fields include ckan_version,
        # site_title, site_url, error_emails_to, extensions.
        return status
