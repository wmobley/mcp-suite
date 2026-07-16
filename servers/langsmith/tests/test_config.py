"""Unit tests for config loading — no network required."""

from __future__ import annotations

import os


def test_default_endpoint(monkeypatch):
    monkeypatch.delenv("LANGSMITH_ENDPOINT", raising=False)
    monkeypatch.delenv("LANGSMITH_API_KEY", raising=False)
    # Re-instantiate Settings to pick up cleared env
    from dso_langsmith_mcp.config import Settings
    s = Settings()
    assert s.langsmith_endpoint == "https://api.smith.langchain.com"


def test_custom_endpoint(monkeypatch):
    monkeypatch.setenv("LANGSMITH_ENDPOINT", "https://custom.langsmith.example.com/")
    from dso_langsmith_mcp.config import Settings
    s = Settings()
    assert s.langsmith_endpoint == "https://custom.langsmith.example.com"  # trailing slash stripped


def test_api_key_read(monkeypatch):
    monkeypatch.setenv("LANGSMITH_API_KEY", "lsv2_pt_test123")
    from dso_langsmith_mcp.config import Settings
    s = Settings()
    assert s.langsmith_api_key == "lsv2_pt_test123"


def test_missing_api_key(monkeypatch):
    monkeypatch.delenv("LANGSMITH_API_KEY", raising=False)
    from dso_langsmith_mcp.config import Settings
    s = Settings()
    assert s.langsmith_api_key is None


def test_default_port(monkeypatch):
    monkeypatch.delenv("MCP_HTTP_PORT", raising=False)
    from dso_langsmith_mcp.config import Settings
    s = Settings()
    assert s.mcp_http_port == 8300
