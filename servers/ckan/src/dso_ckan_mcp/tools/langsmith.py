"""
LangSmith tools — traces, prompts, datasets, and experiments.

Registered only when LANGSMITH_API_KEY is set (see server.py).
Tool names are prefixed with ``langsmith_`` to avoid collisions with
CKAN tools.

Tools registered here (7)
--------------------------
  langsmith_list_projects   — list tracing projects with metrics
  langsmith_fetch_runs      — query runs with optional filters
  langsmith_list_prompts    — browse the prompt hub
  langsmith_get_prompt      — fetch a prompt by name
  langsmith_list_datasets   — list evaluation datasets
  langsmith_list_examples   — list examples from a dataset
  langsmith_list_experiments — list evaluation experiment runs
"""

from __future__ import annotations

from typing import Any

from ..langsmith_client import LangSmithClient


def register(mcp: Any, client: LangSmithClient) -> None:

    @mcp.tool()
    def langsmith_list_projects(limit: int = 20) -> list[dict[str, Any]]:
        """List LangSmith tracing projects with summary metrics.

        Parameters
        ----------
        limit:
            Maximum number of projects to return (clamped to 1..500).

        Returns
        -------
        list
            Each dict includes ``id``, ``name``, ``run_count``,
            ``latency_p50``, ``error_rate``, ``total_tokens``, ``total_cost``.
        """
        return client.list_projects(limit=limit)

    @mcp.tool()
    def langsmith_fetch_runs(
        project_name: str | None = None,
        project_id: str | None = None,
        run_type: str | None = None,
        error: bool | None = None,
        filter: str | None = None,
        limit: int = 20,
    ) -> list[dict[str, Any]]:
        """Fetch runs (traces) from a LangSmith project.

        Parameters
        ----------
        project_name:
            Project name (use ``langsmith_list_projects`` to find names).
        project_id:
            Project UUID — alternative to ``project_name``.
        run_type:
            Filter by type: ``"chain"``, ``"llm"``, ``"tool"``, ``"retriever"``.
        error:
            True = only errored runs; False = only successful runs.
        filter:
            FQL filter string (e.g. ``"and(gt(latency, 5), eq(feedback_key, 'score'))"``).
        limit:
            Maximum number of runs to return (clamped to 1..500).
        """
        return client.list_runs(
            project_name=project_name,
            project_id=project_id,
            run_type=run_type,
            error=error,
            filter=filter,
            limit=limit,
        )

    @mcp.tool()
    def langsmith_list_prompts(
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
            True = public only; False = private only; None = both.
        query:
            Text search query.
        """
        return client.list_prompts(limit=limit, is_public=is_public, query=query)

    @mcp.tool()
    def langsmith_get_prompt(prompt_identifier: str) -> dict[str, Any]:
        """Fetch a specific prompt from the LangSmith prompt hub.

        Parameters
        ----------
        prompt_identifier:
            Prompt name (e.g. ``'my-prompt'``) or owner-scoped
            (e.g. ``'owner/my-prompt'``).
        """
        return client.get_prompt(prompt_identifier)

    @mcp.tool()
    def langsmith_list_datasets(
        dataset_type: str | None = None,
        limit: int = 20,
    ) -> list[dict[str, Any]]:
        """List LangSmith evaluation datasets.

        Parameters
        ----------
        dataset_type:
            Optional type filter: ``"kv"``, ``"llm"``, ``"chat"``, ``"sp"``.
        limit:
            Maximum number of datasets to return (clamped to 1..500).
        """
        return client.list_datasets(dataset_type=dataset_type, limit=limit)

    @mcp.tool()
    def langsmith_list_examples(
        dataset_id: str | None = None,
        dataset_name: str | None = None,
        limit: int = 20,
    ) -> list[dict[str, Any]]:
        """List examples from a LangSmith dataset.

        Parameters
        ----------
        dataset_id:
            UUID of the dataset.
        dataset_name:
            Name of the dataset — alternative to ``dataset_id``.
        limit:
            Maximum number of examples to return (clamped to 1..500).
        """
        if not dataset_id and not dataset_name:
            return [{"error": "Provide dataset_id or dataset_name"}]
        return client.list_examples(
            dataset_id=dataset_id,
            dataset_name=dataset_name,
            limit=limit,
        )

    @mcp.tool()
    def langsmith_list_experiments(limit: int = 20) -> list[dict[str, Any]]:
        """List LangSmith evaluation experiments with aggregate metrics.

        Experiments are tracing projects linked to a reference dataset.

        Parameters
        ----------
        limit:
            Maximum number of experiments to return (clamped to 1..500).
        """
        return client.list_experiments(limit=limit)
