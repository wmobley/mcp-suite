"""
Configuration for the DSO CKAN MCP server.

Loads settings from environment variables.  Read-path settings are all that
are required for Track A.  Track B (write) env vars are read but never
required and are NOT validated here.

Startup banner logs:
  - resolved CKAN_URL
  - classified environment (dev / production)
  - CKAN_API_TOKEN: [SET] / [NOT SET]    ← never the value

Configuration sources, in precedence order (highest first):
  1. Real process environment variables (e.g. the MCP client's ``.mcp.json``
     ``env`` block, or an exported shell variable).
  2. A ``.env`` file at the mcp-server project root (gitignored — the home for
     a local ``CKAN_API_TOKEN``).
Real env vars always win: ``load_dotenv(override=False)`` never overwrites a
value already present in the environment.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

logger = logging.getLogger(__name__)

# Load mcp-server/.env (this file is src/dso_ckan_mcp/config.py → parents[2] is
# the mcp-server project root). Real environment variables take precedence.
_ENV_PATH = Path(__file__).resolve().parents[2] / ".env"
load_dotenv(_ENV_PATH, override=False)
# Also honour a .env in the current working directory, if present.
load_dotenv(override=False)

# Hostnames/suffixes that classify a CKAN_URL as "dev" (non-production).
# 0.0.0.0 (any-interface bind) and ::1 (IPv6 loopback) are treated as dev.
# RFC-1918 private ranges (192.168.x, 10.x, 172.16–31.x) are intentionally
# left as production — they are commonly used for staging and VPN-accessible
# portals where write guards should remain active.
_DEV_HOSTS = {"localhost", "127.0.0.1", "0.0.0.0", "::1"}
_DEV_SUFFIXES = (".test", ".dev", ".localhost")


def is_production(url: str) -> bool:
    """Return True if *url* points at a production (non-local) host.

    A URL is classified as dev when its hostname is 'localhost',
    '127.0.0.1', or ends with a known dev suffix (.test / .dev /
    .localhost).  Everything else is production.

    Examples
    --------
    >>> is_production("http://localhost:5001")
    False
    >>> is_production("http://127.0.0.1:5001")
    False
    >>> is_production("https://ckan.tacc.cloud")
    True
    >>> is_production("http://my-app.localhost")
    False
    """
    try:
        from urllib.parse import urlparse

        host = urlparse(url).hostname or ""
        host = host.lower()
    except Exception:
        # If we cannot parse, err on the side of caution.
        return True

    if host in _DEV_HOSTS:
        return False
    for suffix in _DEV_SUFFIXES:
        if host.endswith(suffix):
            return False
    return True


@dataclass
class Settings:
    """Runtime configuration loaded from environment variables."""

    # ── Track A (read path) ────────────────────────────────────────────────
    ckan_url: str = field(default_factory=lambda: os.environ.get("CKAN_URL", "http://localhost:5001"))
    schema_cache_ttl: int = field(
        default_factory=lambda: int(os.environ.get("SCHEMA_CACHE_TTL", "3600"))
    )

    # ── Track B (write path) — read but NOT required ───────────────────────
    # Optional Tapis OAuth2 JWT used as an env-level fallback when the caller
    # does not supply a per-call ``tapis_token`` argument to a write tool.
    # When present it is sent as ``X-Tapis-Token`` on POST (write) requests,
    # which is how the portal's ``ckanext-oauth2`` plugin authenticates writes.
    # Tapis JWTs are short-lived (~hours) — regenerate when expired.
    # Value is stored but NEVER logged (startup banner shows [SET]/[NOT SET]).
    ckan_api_token: str | None = field(
        default_factory=lambda: os.environ.get("CKAN_API_TOKEN") or None
    )
    mcp_allow_prod_writes: bool = field(
        default_factory=lambda: os.environ.get("MCP_ALLOW_PROD_WRITES", "").lower() == "true"
    )
    # ``None`` means uploads are disabled; resolve_upload_path will refuse any upload.
    mcp_upload_dir: str | None = field(
        default_factory=lambda: os.environ.get("MCP_UPLOAD_DIR") or None
    )
    # Default 90 MB — must remain strictly below CKAN's CKAN_MAX_UPLOAD_SIZE_MB=100.
    # 90 MB leaves headroom so the MCP client rejects oversized files before
    # CKAN's own limit silently truncates or rejects the multipart request.
    mcp_max_upload_mb: int = field(
        default_factory=lambda: int(os.environ.get("MCP_MAX_UPLOAD_MB", "90"))
    )

    # ── Transport ──────────────────────────────────────────────────────────
    # "stdio" (default) for local MCP clients; "http" to serve over HTTP so a
    # long-running consumer (e.g. ckan-agent-api) can connect as an MCP client.
    mcp_transport: str = field(
        default_factory=lambda: os.environ.get("MCP_TRANSPORT", "stdio").strip().lower()
    )
    # HTTP transport binds to loopback by default — the endpoint is unauthenticated
    # unless MCP_HTTP_SHARED_SECRET is set, and the CKAN_API_TOKEN env fallback grants
    # ambient write access to any caller that can reach the port. Do NOT bind to
    # 0.0.0.0 / expose publicly without a fronting proxy enforcing auth.
    mcp_http_host: str = field(
        default_factory=lambda: os.environ.get("MCP_HTTP_HOST", "127.0.0.1")
    )
    mcp_http_port: int = field(
        default_factory=lambda: int(os.environ.get("MCP_HTTP_PORT", "8100"))
    )
    # Shared secret required as `Authorization: Bearer <secret>` on every HTTP
    # request. Unset = no auth (only acceptable for a strictly loopback dev bind).
    mcp_http_shared_secret: str | None = field(
        default_factory=lambda: os.environ.get("MCP_HTTP_SHARED_SECRET") or None
    )

    @property
    def is_production(self) -> bool:
        return is_production(self.ckan_url)

    @property
    def env_label(self) -> str:
        return "production" if self.is_production else "dev"

    def log_startup_banner(self) -> None:
        """Emit a startup log showing resolved config.  Token value is NEVER logged.

        Logs:
        - Resolved CKAN_URL and dev/production classification.
        - Whether the API token is set (write tools enabled) or not (read-only mode).
        - A prominent WARNING when targeting production with writes enabled.
        """
        # TODO (prod): warn if configured token is sysadmin — sysadmin tokens
        # have broader permissions than needed; prefer an editor-role token.
        token_status = "[SET]" if self.ckan_api_token else "[NOT SET]"
        writes_enabled = bool(self.ckan_api_token)
        logger.info(
            "DSO CKAN MCP Server starting — CKAN_URL=%s  env=%s  CKAN_API_TOKEN=%s  "
            "write_tools=%s",
            self.ckan_url,
            self.env_label,
            token_status,
            "enabled" if writes_enabled else "disabled (no token)",
        )
        if self.is_production and writes_enabled:
            logger.warning(
                "TARGETING PRODUCTION CKAN (%s) — writes are LIVE. "
                "MCP_ALLOW_PROD_WRITES=%s",
                self.ckan_url,
                "true" if self.mcp_allow_prod_writes else "false (live writes will be refused)",
            )


# Module-level singleton — import and use `settings` everywhere.
settings = Settings()
