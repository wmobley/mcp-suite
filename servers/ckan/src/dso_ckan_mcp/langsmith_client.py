"""
Thin wrapper around the LangSmith Python SDK.

Converts SDK iterator/object responses into plain dicts for JSON
serialisation by FastMCP.  Applies per-call result limits to prevent
runaway pagination.
"""

from __future__ import annotations

from typing import Any

_MAX_RESULTS = 500


def _safe_str(val: Any) -> str | None:
    """Convert a value to string, or None if falsy."""
    return str(val) if val else None


def _safe_iso(val: Any) -> str | None:
    """Convert a datetime-like to ISO string, or None."""
    if val is None:
        return None
    try:
        return val.isoformat()
    except AttributeError:
        return str(val)


class LangSmithClient:
    """Wraps ``langsmith.Client`` and normalises responses to plain dicts."""

    def __init__(
        self,
        api_key: str,
        endpoint: str = "https://api.smith.langchain.com",
        workspace_id: str | None = None,
    ) -> None:
        from langsmith import Client

        self._client = Client(api_key=api_key, api_url=endpoint)
        self._workspace_id = workspace_id

    # ── Projects ──────────────────────────────────────────────────────────────

    def list_projects(self, limit: int = 20) -> list[dict[str, Any]]:
        """Return up to *limit* tracing projects."""
        limit = max(1, min(limit, _MAX_RESULTS))
        result: list[dict[str, Any]] = []
        for p in self._client.list_projects():
            result.append({
                "id": _safe_str(p.id),
                "name": p.name,
                "start_time": _safe_iso(getattr(p, "start_time", None)),
                "end_time": _safe_iso(getattr(p, "end_time", None)),
                "run_count": getattr(p, "run_count", None),
                "latency_p50": getattr(p, "latency_p50", None),
                "latency_p99": getattr(p, "latency_p99", None),
                "error_rate": getattr(p, "error_rate", None),
                "total_tokens": getattr(p, "total_tokens", None),
                "total_cost": getattr(p, "total_cost", None),
            })
            if len(result) >= limit:
                break
        return result

    # ── Runs ──────────────────────────────────────────────────────────────────

    def list_runs(
        self,
        project_name: str | None = None,
        project_id: str | None = None,
        run_type: str | None = None,
        error: bool | None = None,
        filter: str | None = None,
        limit: int = 20,
    ) -> list[dict[str, Any]]:
        """Return up to *limit* runs, optionally filtered."""
        limit = max(1, min(limit, _MAX_RESULTS))
        kwargs: dict[str, Any] = {}
        if project_name:
            kwargs["project_name"] = project_name
        if project_id:
            kwargs["project_id"] = project_id
        if run_type:
            kwargs["run_type"] = run_type
        if error is not None:
            kwargs["error"] = error
        if filter:
            kwargs["filter"] = filter

        result: list[dict[str, Any]] = []
        for run in self._client.list_runs(**kwargs):
            result.append({
                "id": _safe_str(run.id),
                "name": run.name,
                "run_type": run.run_type,
                "start_time": _safe_iso(getattr(run, "start_time", None)),
                "end_time": _safe_iso(getattr(run, "end_time", None)),
                "status": getattr(run, "status", None),
                "error": getattr(run, "error", None),
                "total_tokens": getattr(run, "total_tokens", None),
                "prompt_tokens": getattr(run, "prompt_tokens", None),
                "completion_tokens": getattr(run, "completion_tokens", None),
                "total_cost": getattr(run, "total_cost", None),
                "tags": getattr(run, "tags", None),
                "parent_run_id": _safe_str(getattr(run, "parent_run_id", None)),
            })
            if len(result) >= limit:
                break
        return result

    # ── Prompts ───────────────────────────────────────────────────────────────

    def list_prompts(
        self,
        limit: int = 20,
        is_public: bool | None = None,
        query: str | None = None,
    ) -> list[dict[str, Any]]:
        """Return up to *limit* prompts from the prompt hub."""
        limit = max(1, min(limit, _MAX_RESULTS))
        kwargs: dict[str, Any] = {"limit": limit}
        if is_public is not None:
            kwargs["is_public"] = is_public
        if query:
            kwargs["query"] = query
        response = self._client.list_prompts(**kwargs)
        repos = getattr(response, "repos", []) or []
        result: list[dict[str, Any]] = []
        for repo in repos[:limit]:
            result.append({
                "id": _safe_str(getattr(repo, "id", None)),
                "repo_handle": getattr(repo, "repo_handle", None),
                "description": getattr(repo, "description", None),
                "is_public": getattr(repo, "is_public", None),
                "is_archived": getattr(repo, "is_archived", None),
                "tags": getattr(repo, "tags", None),
                "num_commits": getattr(repo, "num_commits", None),
                "last_commit_hash": getattr(repo, "last_commit_hash", None),
                "created_at": _safe_iso(getattr(repo, "created_at", None)),
                "updated_at": _safe_iso(getattr(repo, "updated_at", None)),
            })
        return result

    def get_prompt(self, prompt_identifier: str) -> dict[str, Any]:
        """Fetch a specific prompt by name (e.g. ``'my-prompt'`` or ``'owner/my-prompt'``)."""
        prompt = self._client.get_prompt(prompt_identifier)
        if prompt is None:
            return {"error": f"Prompt '{prompt_identifier}' not found"}
        return {
            "id": _safe_str(getattr(prompt, "id", None)),
            "repo_handle": getattr(prompt, "repo_handle", None),
            "description": getattr(prompt, "description", None),
            "is_public": getattr(prompt, "is_public", None),
            "tags": getattr(prompt, "tags", None),
            "last_commit_hash": getattr(prompt, "last_commit_hash", None),
            "created_at": _safe_iso(getattr(prompt, "created_at", None)),
            "updated_at": _safe_iso(getattr(prompt, "updated_at", None)),
        }

    # ── Datasets ──────────────────────────────────────────────────────────────

    def list_datasets(
        self,
        dataset_type: str | None = None,
        limit: int = 20,
    ) -> list[dict[str, Any]]:
        """Return up to *limit* datasets."""
        limit = max(1, min(limit, _MAX_RESULTS))
        kwargs: dict[str, Any] = {}
        if dataset_type:
            kwargs["data_type"] = dataset_type

        result: list[dict[str, Any]] = []
        for ds in self._client.list_datasets(**kwargs):
            result.append({
                "id": _safe_str(ds.id),
                "name": ds.name,
                "description": getattr(ds, "description", None),
                "data_type": getattr(ds, "data_type", None),
                "example_count": getattr(ds, "example_count", None),
                "created_at": _safe_iso(getattr(ds, "created_at", None)),
                "modified_at": _safe_iso(getattr(ds, "modified_at", None)),
            })
            if len(result) >= limit:
                break
        return result

    def list_examples(
        self,
        dataset_id: str | None = None,
        dataset_name: str | None = None,
        limit: int = 20,
    ) -> list[dict[str, Any]]:
        """Return up to *limit* examples from a dataset."""
        limit = max(1, min(limit, _MAX_RESULTS))
        kwargs: dict[str, Any] = {}
        if dataset_id:
            kwargs["dataset_id"] = dataset_id
        if dataset_name:
            kwargs["dataset_name"] = dataset_name

        result: list[dict[str, Any]] = []
        for ex in self._client.list_examples(**kwargs):
            result.append({
                "id": _safe_str(ex.id),
                "dataset_id": _safe_str(getattr(ex, "dataset_id", None)),
                "inputs": getattr(ex, "inputs", None),
                "outputs": getattr(ex, "outputs", None),
                "created_at": _safe_iso(getattr(ex, "created_at", None)),
                "modified_at": _safe_iso(getattr(ex, "modified_at", None)),
            })
            if len(result) >= limit:
                break
        return result

    # ── Experiments ───────────────────────────────────────────────────────────

    def list_experiments(self, limit: int = 20) -> list[dict[str, Any]]:
        """Return up to *limit* experiment projects (projects linked to a dataset)."""
        limit = max(1, min(limit, _MAX_RESULTS))
        result: list[dict[str, Any]] = []
        for p in self._client.list_projects():
            ref_dataset = getattr(p, "reference_dataset_id", None)
            if ref_dataset is None:
                continue
            result.append({
                "id": _safe_str(p.id),
                "name": p.name,
                "reference_dataset_id": _safe_str(ref_dataset),
                "start_time": _safe_iso(getattr(p, "start_time", None)),
                "end_time": _safe_iso(getattr(p, "end_time", None)),
                "run_count": getattr(p, "run_count", None),
                "error_rate": getattr(p, "error_rate", None),
                "latency_p50": getattr(p, "latency_p50", None),
                "feedback_stats": getattr(p, "feedback_stats", None),
                "total_cost": getattr(p, "total_cost", None),
            })
            if len(result) >= limit:
                break
        return result
