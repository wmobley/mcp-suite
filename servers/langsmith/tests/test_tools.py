"""
Unit tests for tool param-building — no network required.

Uses a stub client that records calls and returns canned responses.
Integration tests hit the live API and are auto-skipped when LANGSMITH_API_KEY
is not set (see conftest.py).
"""

from __future__ import annotations

from typing import Any

import pytest

from conftest import FakeMCP


class StubClient:
    """Records the last call to each method and returns canned data."""

    def __init__(self):
        self.last_call: dict[str, Any] = {}

    def list_projects(self, limit=20):
        self.last_call = {"method": "list_projects", "limit": limit}
        return [{"id": "proj-1", "name": "my-project", "run_count": 5}]

    def list_runs(self, **kwargs):
        self.last_call = {"method": "list_runs", **kwargs}
        return [{"id": "run-1", "name": "test-run", "run_type": "chain"}]

    def list_prompts(self, limit=20, is_public=None, query=None):
        self.last_call = {"method": "list_prompts", "limit": limit}
        return [{"repo_handle": "my-prompt"}]

    def get_prompt(self, identifier):
        self.last_call = {"method": "get_prompt", "identifier": identifier}
        return {"repo_handle": identifier}

    def list_datasets(self, dataset_type=None, limit=20):
        self.last_call = {"method": "list_datasets", "limit": limit}
        return [{"id": "ds-1", "name": "my-dataset"}]

    def list_examples(self, dataset_id=None, dataset_name=None, limit=20):
        self.last_call = {"method": "list_examples", "dataset_id": dataset_id}
        return [{"id": "ex-1"}]

    def list_experiments(self, limit=20):
        self.last_call = {"method": "list_experiments", "limit": limit}
        return [{"id": "exp-1", "name": "eval-run", "reference_dataset_id": "ds-1"}]


@pytest.fixture
def stub():
    return StubClient()


# ── runs ──────────────────────────────────────────────────────────────────────

def test_list_projects_registered(stub):
    from dso_langsmith_mcp.tools import runs
    mcp = FakeMCP()
    runs.register(mcp, stub)
    assert "list_projects" in mcp.tools
    assert "fetch_runs" in mcp.tools


def test_fetch_runs_passes_filters(stub):
    from dso_langsmith_mcp.tools import runs
    mcp = FakeMCP()
    runs.register(mcp, stub)
    mcp.tools["fetch_runs"](project_name="proj", run_type="llm", error=True, limit=5)
    assert stub.last_call["project_name"] == "proj"
    assert stub.last_call["run_type"] == "llm"
    assert stub.last_call["error"] is True
    assert stub.last_call["limit"] == 5


# ── prompts ───────────────────────────────────────────────────────────────────

def test_list_prompts_registered(stub):
    from dso_langsmith_mcp.tools import prompts
    mcp = FakeMCP()
    prompts.register(mcp, stub)
    assert "list_prompts" in mcp.tools
    assert "get_prompt" in mcp.tools


def test_get_prompt_passes_identifier(stub):
    from dso_langsmith_mcp.tools import prompts
    mcp = FakeMCP()
    prompts.register(mcp, stub)
    mcp.tools["get_prompt"]("owner/my-prompt")
    assert stub.last_call["identifier"] == "owner/my-prompt"


# ── datasets ──────────────────────────────────────────────────────────────────

def test_list_examples_requires_id_or_name(stub):
    from dso_langsmith_mcp.tools import datasets
    mcp = FakeMCP()
    datasets.register(mcp, stub)
    result = mcp.tools["list_examples"]()
    assert result[0].get("error")


def test_list_examples_passes_dataset_id(stub):
    from dso_langsmith_mcp.tools import datasets
    mcp = FakeMCP()
    datasets.register(mcp, stub)
    mcp.tools["list_examples"](dataset_id="ds-1")
    assert stub.last_call["dataset_id"] == "ds-1"


# ── experiments ───────────────────────────────────────────────────────────────

def test_list_experiments_registered(stub):
    from dso_langsmith_mcp.tools import experiments
    mcp = FakeMCP()
    experiments.register(mcp, stub)
    assert "list_experiments" in mcp.tools


# ── integration ───────────────────────────────────────────────────────────────

@pytest.mark.integration
def test_live_list_projects(live_client):
    projects = live_client.list_projects(limit=5)
    assert isinstance(projects, list)


@pytest.mark.integration
def test_live_list_datasets(live_client):
    datasets = live_client.list_datasets(limit=5)
    assert isinstance(datasets, list)
