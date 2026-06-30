"""
MCP prompt templates for the DSO CKAN portal.

These are read-only, parameterised guidance prompts.  Each returns a focused
instruction that steers the model to use the existing read/schema tools — they
do NOT fetch data themselves (keeping them network-free and side-effect-free).

Registered prompts (4)
----------------------
  analyze_dataset(dataset_id)        — summarise one dataset
  find_by_variable(variable)         — locate datasets carrying a MINT variable
  recent_datasets(org, limit)        — list most-recently-modified datasets
  describe_org_holdings(org)         — summarise what an organisation holds
"""

from __future__ import annotations

from typing import Any


def register(mcp: Any) -> None:
    """Register all prompt templates onto *mcp*.

    Parameters
    ----------
    mcp:
        The FastMCP application instance.
    """

    @mcp.prompt()
    def analyze_dataset(dataset_id: str) -> str:
        """Summarise a single dataset's metadata, coverage, and resources."""
        return (
            f"Analyze the CKAN dataset {dataset_id!r}.\n\n"
            "1. Call `package_show(id=" f"{dataset_id!r})` to fetch its full metadata.\n"
            "2. Summarise for the user: title, dataset type, organisation, "
            "license, temporal coverage (start/end), spatial coverage if present, "
            "and any MINT standard variables.\n"
            "3. Describe the resources: how many, which formats, and what they "
            "represent. If there are many similar resources, group them rather "
            "than listing each one.\n"
            "4. Note anything notable or missing (e.g. no temporal coverage set)."
        )

    @mcp.prompt()
    def find_by_variable(variable: str) -> str:
        """Find datasets/resources that carry a given MINT standard variable."""
        return (
            f"Find datasets on the portal related to the MINT standard variable "
            f"{variable!r}.\n\n"
            "1. Call `package_search` with q=" f"{variable!r}" " (and try the "
            "`mint_standard_variables` field as a filter if a plain query is too "
            "broad).\n"
            "2. Return a concise list of matching datasets: title, organisation, "
            "and resource count. Do not request resources inline (use the default "
            "summary mode); point the user to `package_show` for detail.\n"
            "3. If nothing matches, suggest close variable names the portal may use."
        )

    @mcp.prompt()
    def recent_datasets(org: str = "", limit: int = 10) -> str:
        """List the most recently modified datasets, optionally scoped to an org."""
        scope = (
            f" within the organisation {org!r}" if org else " across the whole portal"
        )
        fq_hint = f' and fq="owner_org:{org}"' if org else ""
        return (
            f"List the {limit} most recently modified datasets{scope}.\n\n"
            "1. Call `package_search` with sort=\"metadata_modified desc\", rows="
            f"{limit}{fq_hint}.\n"
            "2. Present a readable list: title, organisation, and last-modified "
            "date. Use the default summary mode (no inline resources).\n"
            "3. Keep it brief — one line per dataset."
        )

    @mcp.prompt()
    def describe_org_holdings(org: str) -> str:
        """Summarise what an organisation holds on the portal."""
        return (
            f"Summarise what the organisation {org!r} holds on the portal.\n\n"
            "1. Call `organization_show(id=" f"{org!r}, include_datasets=True)` to "
            "get the org and its datasets (or `package_search` with "
            f'fq="owner_org:{org}" if you need more than the org view returns).\n'
            "2. Summarise: how many datasets, their dataset types, the range of "
            "topics/titles, and the dominant resource formats.\n"
            "3. Highlight the largest or most data-rich datasets by resource count."
        )
