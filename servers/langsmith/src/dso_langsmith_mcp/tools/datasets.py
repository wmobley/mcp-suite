"""
Dataset tools — browse LangSmith datasets and examples.

Tools registered here (2)
--------------------------
  list_datasets  — list datasets with metadata
  list_examples  — list examples from a dataset
"""

from __future__ import annotations

from typing import Any

from ..langsmith_client import LangSmithClient


def register(mcp: Any, client: LangSmithClient) -> None:

    @mcp.tool()
    def list_datasets(
        dataset_type: str | None = None,
        limit: int = 20,
    ) -> list[dict[str, Any]]:
        """List LangSmith datasets.

        Parameters
        ----------
        dataset_type:
            Optional type filter: ``"kv"`` (key-value), ``"llm"`` (chat),
            ``"chat"``, or ``"sp"`` (single-prompt).
        limit:
            Maximum number of datasets to return (clamped to 1..500).

        Returns
        -------
        list
            Each dict includes ``id``, ``name``, ``description``,
            ``data_type``, ``example_count``, ``created_at``, ``modified_at``.
        """
        return client.list_datasets(dataset_type=dataset_type, limit=limit)

    @mcp.tool()
    def list_examples(
        dataset_id: str | None = None,
        dataset_name: str | None = None,
        limit: int = 20,
    ) -> list[dict[str, Any]]:
        """List examples from a LangSmith dataset.

        Provide either ``dataset_id`` or ``dataset_name`` — at least one is
        required.

        Parameters
        ----------
        dataset_id:
            UUID of the dataset (use ``list_datasets`` to find IDs).
        dataset_name:
            Name of the dataset — alternative to ``dataset_id``.
        limit:
            Maximum number of examples to return (clamped to 1..500).

        Returns
        -------
        list
            Each dict includes ``id``, ``dataset_id``, ``inputs``,
            ``outputs``, ``created_at``, ``modified_at``.
        """
        if not dataset_id and not dataset_name:
            return [{"error": "Provide dataset_id or dataset_name"}]
        return client.list_examples(
            dataset_id=dataset_id,
            dataset_name=dataset_name,
            limit=limit,
        )
