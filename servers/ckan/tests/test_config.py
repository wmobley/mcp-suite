"""Tests for config.py — env loading and is_production()."""

from __future__ import annotations

import os

import pytest


def test_is_production_localhost():
    from dso_ckan_mcp.config import is_production

    assert is_production("http://localhost:5001") is False


def test_is_production_127():
    from dso_ckan_mcp.config import is_production

    assert is_production("http://127.0.0.1:5001") is False


def test_is_production_dev_suffix():
    from dso_ckan_mcp.config import is_production

    assert is_production("http://myapp.dev") is False
    assert is_production("http://myapp.test") is False
    assert is_production("http://myapp.localhost") is False


def test_is_production_real_host():
    from dso_ckan_mcp.config import is_production

    assert is_production("https://ckan.tacc.cloud") is True
    assert is_production("https://portal.example.com") is True


def test_settings_defaults(monkeypatch):
    """Settings picks up defaults when env vars are absent.

    Hermetic: clears the relevant env vars and constructs Settings directly.
    We do NOT reload the config module — a reload would re-run load_dotenv()
    and re-populate values from a local .env, defeating the isolation. Each
    Settings() reads os.environ fresh, so a plain construction is sufficient.
    """
    monkeypatch.delenv("CKAN_URL", raising=False)
    monkeypatch.delenv("SCHEMA_CACHE_TTL", raising=False)
    monkeypatch.delenv("CKAN_API_TOKEN", raising=False)

    from dso_ckan_mcp.config import Settings

    s = Settings()
    assert s.ckan_url == "http://localhost:5001"
    assert s.schema_cache_ttl == 3600
    assert s.ckan_api_token is None
    assert s.ckan_api_token is None
    assert s.is_production is False
    assert s.env_label == "dev"


def test_settings_env_override(monkeypatch):
    monkeypatch.setenv("CKAN_URL", "https://prod.ckan.example.com")
    monkeypatch.setenv("SCHEMA_CACHE_TTL", "600")
    monkeypatch.setenv("CKAN_API_TOKEN", "tok123")

    from dso_ckan_mcp.config import Settings

    s = Settings()
    assert s.ckan_url == "https://prod.ckan.example.com"
    assert s.schema_cache_ttl == 600
    assert s.ckan_api_token == "tok123"
    assert s.is_production is True
    assert s.env_label == "production"


def test_startup_banner_does_not_log_token(monkeypatch, capfd):
    """The startup banner must never log the token value."""
    monkeypatch.setenv("CKAN_API_TOKEN", "super-secret-token-value")
    import logging

    import dso_ckan_mcp.config as cfg_module

    s = cfg_module.Settings()

    # Capture the log output via a handler on the module logger.
    records: list[logging.LogRecord] = []

    class Capture(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            records.append(record)

    handler = Capture()
    logger = logging.getLogger("dso_ckan_mcp.config")
    logger.addHandler(handler)
    logger.setLevel(logging.DEBUG)
    try:
        s.log_startup_banner()
    finally:
        logger.removeHandler(handler)

    joined = " ".join(r.getMessage() for r in records)
    assert "super-secret-token-value" not in joined, "Token value leaked into log!"
    assert "[SET]" in joined, "Expected [SET] marker in log"
