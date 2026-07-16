"""
Prompt hub tools — browse and retrieve prompts from LangSmith.

Tools registered here (2)
--------------------------
  list_prompts  — list prompts from the prompt hub
  get_prompt    — fetch a single prompt by name
"""

from __future__ import annotations

from typing import Any

from ..langsmith_client import LangSmithClient


def register(mcp: Any, client: LangSmithClient) -> None:

    @mcp.tool()
    def list_prompts(
        limit: int = 20,
        is_public: bool | None = None,
        query: str | None = None,
    ) -> list[dict[str, Any]]:
        """List prompts from the LangSmith prompt hub.

        Parameters
        ----------
        limit:
            Maximum number of prompts to return (clamped to 1..500).
        is_public:
            When True, return only public prompts; when False, only private ones.
            When None (default), return both.
        query:
            Optional text search query to filter prompts by name/description.

        Returns
        -------
        list
            Each dict includes ``id``, ``repo_handle``, ``description``,
            ``is_public``, ``is_archived``, ``tags``, ``num_commits``,
            ``last_commit_hash``, ``created_at``, ``updated_at``.
        """
        return client.list_prompts(limit=limit, is_public=is_public, query=query)

    @mcp.tool()
    def get_prompt(prompt_identifier: str) -> dict[str, Any]:
        """Fetch a specific prompt from the LangSmith prompt hub.

        Parameters
        ----------
        prompt_identifier:
            Prompt name (e.g. ``'my-prompt'``) or owner-scoped name
            (e.g. ``'owner/my-prompt'``).

        Returns
        -------
        dict
            Prompt metadata including ``repo_handle``, ``description``,
            ``tags``, ``last_commit_hash``, ``is_public``, ``created_at``.
        """
        return client.get_prompt(prompt_identifier)
