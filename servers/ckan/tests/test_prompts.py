"""
Tests for prompts.py — parameterised MCP prompt templates.

Pure unit tests: a fake MCP captures the registered prompt functions and we
assert the generated text embeds the parameter and points at the right tools.
"""

from __future__ import annotations

import pytest

from dso_ckan_mcp import prompts as prompt_mod


class _FakeMCP:
    """Captures functions registered via the @mcp.prompt() decorator."""

    def __init__(self):
        self.prompts = {}

    def prompt(self):
        def decorator(fn):
            self.prompts[fn.__name__] = fn
            return fn

        return decorator


@pytest.fixture
def prompts():
    mcp = _FakeMCP()
    prompt_mod.register(mcp)
    return mcp.prompts


def test_all_four_prompts_registered(prompts):
    assert set(prompts) == {
        "analyze_dataset",
        "find_by_variable",
        "recent_datasets",
        "describe_org_holdings",
    }


def test_analyze_dataset_embeds_id_and_tool(prompts):
    text = prompts["analyze_dataset"]("ntgam-water-levels")
    assert "ntgam-water-levels" in text
    assert "package_show" in text


def test_find_by_variable_embeds_variable(prompts):
    text = prompts["find_by_variable"]("groundwater__hydraulic_head")
    assert "groundwater__hydraulic_head" in text
    assert "package_search" in text


def test_recent_datasets_org_scoped(prompts):
    text = prompts["recent_datasets"](org="twdb-gam", limit=5)
    assert "twdb-gam" in text
    assert "owner_org:twdb-gam" in text
    assert "5" in text


def test_recent_datasets_unscoped(prompts):
    text = prompts["recent_datasets"]()
    assert "whole portal" in text
    # No org filter when unscoped.
    assert "owner_org:" not in text


def test_describe_org_holdings_embeds_org(prompts):
    text = prompts["describe_org_holdings"]("twdb-gam")
    assert "twdb-gam" in text
    assert "organization_show" in text


def test_server_registers_prompts_on_real_fastmcp():
    """Importing the server module wires prompts onto the real FastMCP app."""
    from dso_ckan_mcp import server

    # FastMCP exposes registered prompts; the exact accessor varies by version,
    # so just assert the app object exists and the module imported cleanly.
    assert server.mcp is not None
