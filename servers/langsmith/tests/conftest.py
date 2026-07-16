"""
Pytest configuration and shared fixtures.

Integration tests are automatically skipped when LANGSMITH_API_KEY is not set.
"""

from __future__ import annotations

import os

import pytest


def _api_key_present() -> bool:
    return bool(os.environ.get("LANGSMITH_API_KEY"))


_API_KEY_UP = _api_key_present()


@pytest.fixture(scope="session")
def live_client():
    """A LangSmithClient pointed at the real API (integration tests only)."""
    if not _API_KEY_UP:
        pytest.skip("LANGSMITH_API_KEY not set — skipping integration test")
    from dso_langsmith_mcp.langsmith_client import LangSmithClient

    return LangSmithClient(api_key=os.environ["LANGSMITH_API_KEY"])


def pytest_runtest_setup(item: pytest.Item) -> None:
    """Skip integration-marked tests when the API key is absent."""
    if "integration" in item.keywords and not _API_KEY_UP:
        pytest.skip("LANGSMITH_API_KEY not set — skipping integration test")


class FakeMCP:
    """Captures functions registered via the @mcp.tool() decorator."""

    def __init__(self):
        self.tools: dict = {}

    def tool(self):
        def decorator(fn):
            self.tools[fn.__name__] = fn
            return fn
        return decorator
