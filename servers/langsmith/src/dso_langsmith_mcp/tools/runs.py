"""
Run and project tools — query LangSmith traces.

Tools registered here (2)
--------------------------
  list_projects  — list tracing projects with summary metrics
  fetch_runs     — query runs from a project with optional filters
"""

from __future__ import annotations

from typing import Any

from ..langsmith_client import LangSmithClient


def register(mcp: Any, client: LangSmithClient) -> None:

    @mcp.tool()
    def list_projects(limit: int = 20) -> list[dict[str, Any]]:
        """List LangSmith tracing projects with summary metrics.

        Parameters
        ----------
        limit:
            Maximum number of projects to return (clamped to 1..500).

        Returns
        -------
        list
            Each dict includes ``id``, ``name``, ``run_count``,
            ``latency_p50``, ``error_rate``, ``total_tokens``, ``total_cost``,
            ``start_time``, ``end_time``.
        """
        return client.list_projects(limit=limit)

    @mcp.tool()
    def fetch_runs(
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
            Name of the project to query (use ``list_projects`` to find names).
        project_id:
            Project UUID — alternative to ``project_name``.
        run_type:
            Filter by run type: ``"chain"``, ``"llm"``, ``"tool"``, ``"retriever"``.
        error:
            When True, return only errored runs; when False, only successful ones.
        filter:
            FQL filter string (e.g. ``"and(gt(latency, 5), eq(feedback_key, 'score'))"``).
        limit:
            Maximum number of runs to return (clamped to 1..500).

        Returns
        -------
        list
            Each dict includes ``id``, ``name``, ``run_type``, ``status``,
            ``error``, ``start_time``, ``end_time``, ``total_tokens``,
            ``prompt_tokens``, ``completion_tokens``, ``total_cost``, ``tags``.
        """
        return client.list_runs(
            project_name=project_name,
            project_id=project_id,
            run_type=run_type,
            error=error,
            filter=filter,
            limit=limit,
        )
