"""
Configuration for the DSO LangSmith MCP server.

Loads settings from environment variables.

Startup banner logs:
  - resolved LANGSMITH_ENDPOINT
  - LANGSMITH_API_KEY: [SET] / [NOT SET]   ← value never logged
  - LANGSMITH_WORKSPACE_ID: [SET] / [not set]

Configuration sources, in precedence order (highest first):
  1. Real process environment variables (e.g. the MCP client's ``.mcp.json``
     ``env`` block, or an exported shell variable).
  2. A ``.env`` file at the server project root (gitignored).
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

_ENV_PATH = Path(__file__).resolve().parents[2] / ".env"
load_dotenv(_ENV_PATH, override=False)
load_dotenv(override=False)


@dataclass
class Settings:
    """Runtime configuration loaded from environment variables."""

    # ── LangSmith ─────────────────────────────────────────────────────────────
    langsmith_api_key: str | None = field(
        default_factory=lambda: os.environ.get("LANGSMITH_API_KEY") or None
    )
    langsmith_endpoint: str = field(
        default_factory=lambda: os.environ.get(
            "LANGSMITH_ENDPOINT", "https://api.smith.langchain.com"
        ).rstrip("/")
    )
    # Optional — required only when using workspace-scoped API keys.
    langsmith_workspace_id: str | None = field(
        default_factory=lambda: os.environ.get("LANGSMITH_WORKSPACE_ID") or None
    )

    # ── Transport ──────────────────────────────────────────────────────────────
    mcp_transport: str = field(
        default_factory=lambda: os.environ.get("MCP_TRANSPORT", "stdio").strip().lower()
    )
    mcp_http_host: str = field(
        default_factory=lambda: os.environ.get("MCP_HTTP_HOST", "127.0.0.1")
    )
    mcp_http_port: int = field(
        default_factory=lambda: int(os.environ.get("MCP_HTTP_PORT", "8300"))
    )
    mcp_http_shared_secret: str | None = field(
        default_factory=lambda: os.environ.get("MCP_HTTP_SHARED_SECRET") or None
    )

    def log_startup_banner(self) -> None:
        """Emit a startup log showing resolved config. API key value is NEVER logged."""
        key_status = "[SET]" if self.langsmith_api_key else "[NOT SET — all tools will fail]"
        ws_status = self.langsmith_workspace_id or "not set (uses default workspace)"
        logger.info(
            "DSO LangSmith MCP Server starting — endpoint=%s  api_key=%s  workspace=%s",
            self.langsmith_endpoint,
            key_status,
            ws_status,
        )
        if not self.langsmith_api_key:
            logger.warning(
                "LANGSMITH_API_KEY is not set — all tool calls will fail. "
                "Set it in .env or as an environment variable."
            )


settings = Settings()
