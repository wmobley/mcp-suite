"""
Configuration for the DSO Geo MCP server.

Loads settings from environment variables.  A pre-registered Tapis Abaco
actor (GEO_ACTOR_ID) is REQUIRED; the server never registers actors at
runtime.

Startup banner logs:
  - resolved CKAN_URL, TAPIS_BASE, actor_id (masked)
  - GEO_TAPIS_TOKEN: [SET — WARNING] / [NOT SET]  (value never logged)

Configuration sources, in precedence order (highest first):
  1. Real process environment variables (e.g. the MCP client's ``.mcp.json``
     ``env`` block, or an exported shell variable).
  2. A ``.env`` file at the mcp-server project root (gitignored).
Real env vars always win: ``load_dotenv(override=False)`` never overwrites a
value already present in the environment.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlparse

from dotenv import load_dotenv

logger = logging.getLogger(__name__)

# Load servers/geo/.env  (this file is src/dso_geo_mcp/config.py → parents[2]
# is the project root).  Real environment variables take precedence.
_ENV_PATH = Path(__file__).resolve().parents[2] / ".env"
load_dotenv(_ENV_PATH, override=False)
# Also honour a .env in the current working directory, if present.
load_dotenv(override=False)

# Hostnames/suffixes that classify a URL as "dev" (non-production).
_DEV_HOSTS = {"localhost", "127.0.0.1", "0.0.0.0", "::1"}
_DEV_SUFFIXES = (".test", ".dev", ".localhost")


def _hostname(url: str) -> str:
    """Extract the lowercase hostname from *url*, or '' on failure."""
    try:
        return (urlparse(url).hostname or "").lower()
    except Exception:
        return ""


def is_production(url: str) -> bool:
    """Return True if *url* points at a production (non-local) host."""
    host = _hostname(url)
    if host in _DEV_HOSTS:
        return False
    for suffix in _DEV_SUFFIXES:
        if host.endswith(suffix):
            return False
    return True


@dataclass
class Settings:
    """Runtime configuration loaded from environment variables."""

    # ── Tapis / Abaco ─────────────────────────────────────────────────────────
    # Pre-registered actor ID — REQUIRED for any tool submission.
    geo_actor_id: str = field(
        default_factory=lambda: os.environ.get("GEO_ACTOR_ID", "")
    )
    tapis_base: str = field(
        default_factory=lambda: os.environ.get("TAPIS_BASE", "https://portals.tapis.io").rstrip("/")
    )
    # Optional env-level JWT fallback.  Per-call tapis_token arg is preferred.
    # Value is stored but NEVER logged (startup banner shows [SET]/[NOT SET]).
    geo_tapis_token: str | None = field(
        default_factory=lambda: os.environ.get("GEO_TAPIS_TOKEN") or None
    )

    # ── CKAN ──────────────────────────────────────────────────────────────────
    ckan_url: str = field(
        default_factory=lambda: os.environ.get("CKAN_URL", "http://localhost:5001").rstrip("/")
    )
    # SSRF guard: resolved CKAN URLs must match this hostname.
    # Defaults to CKAN_URL hostname when unset.
    geo_allowed_ckan_host: str = field(
        default_factory=lambda: os.environ.get("GEO_ALLOWED_CKAN_HOST", "")
    )

    # ── Polling ───────────────────────────────────────────────────────────────
    geo_poll_timeout_s: int = field(
        default_factory=lambda: int(os.environ.get("GEO_POLL_TIMEOUT_S", "10"))
    )
    geo_poll_retries: int = field(
        default_factory=lambda: int(os.environ.get("GEO_POLL_RETRIES", "1"))
    )

    @property
    def allowed_ckan_host(self) -> str:
        """Return the effective SSRF-guard hostname (explicit override or CKAN_URL host)."""
        if self.geo_allowed_ckan_host:
            return self.geo_allowed_ckan_host.lower()
        return _hostname(self.ckan_url)

    @property
    def is_production(self) -> bool:
        return is_production(self.tapis_base)

    @property
    def env_label(self) -> str:
        return "production" if self.is_production else "dev"

    def log_startup_banner(self) -> None:
        """Emit a startup log showing resolved config.  Token value is NEVER logged."""
        token_status = "[SET — WARNING: env fallback active]" if self.geo_tapis_token else "[NOT SET]"
        actor_display = self.geo_actor_id[:8] + "..." if len(self.geo_actor_id) > 8 else (self.geo_actor_id or "[NOT SET]")
        logger.info(
            "DSO Geo MCP Server starting — TAPIS_BASE=%s  CKAN_URL=%s  env=%s  "
            "GEO_ACTOR_ID=%s  GEO_TAPIS_TOKEN=%s",
            self.tapis_base,
            self.ckan_url,
            self.env_label,
            actor_display,
            token_status,
        )
        if not self.geo_actor_id:
            logger.warning(
                "GEO_ACTOR_ID is not set — all tool submissions will fail. "
                "Register the gdal-actor image once and set GEO_ACTOR_ID."
            )
        if self.geo_tapis_token and self.is_production:
            logger.warning(
                "GEO_TAPIS_TOKEN env fallback is active against PRODUCTION Tapis (%s). "
                "This token is a broad-scope user JWT.  Prefer per-call tapis_token args "
                "and rotate this token frequently.",
                self.tapis_base,
            )


# Module-level singleton — import and use `settings` everywhere.
settings = Settings()
