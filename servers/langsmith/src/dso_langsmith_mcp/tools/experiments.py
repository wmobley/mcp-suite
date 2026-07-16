"""
Experiment tools — list LangSmith evaluation runs.

Experiments in LangSmith are tracing projects that are linked to a reference
dataset (i.e. projects with a ``reference_dataset_id``).

Tools registered here (1)
--------------------------
  list_experiments  — list evaluation experiment projects with metrics
"""

from __future__ import annotations

from typing import Any

from ..langsmith_client import LangSmithClient


def register(mcp: Any, client: LangSmithClient) -> None:

    @mcp.tool()
    def list_experiments(limit: int = 20) -> list[dict[str, Any]]:
        """List LangSmith evaluation experiments with aggregate metrics.

        Experiments are tracing projects linked to a reference dataset.
        Use ``list_datasets`` to find dataset IDs referenced here.

        Parameters
        ----------
        limit:
            Maximum number of experiments to return (clamped to 1..500).

        Returns
        -------
        list
            Each dict includes ``id``, ``name``, ``reference_dataset_id``,
            ``run_count``, ``error_rate``, ``latency_p50``, ``feedback_stats``,
            ``total_cost``, ``start_time``, ``end_time``.
        """
        return client.list_experiments(limit=limit)
