"""
DSO CKAN MCP Server — entrypoint.

Creates the FastMCP application, instantiates shared infrastructure
(CKANClient + SchemaLoader), and registers all tools:

  Track A — read / schema / validate (anonymous, no token required)
  Track B — write tools (token-gated, dry-run-first, audit-logged)

Usage
-----
Run via the installed script::

    dso-ckan-mcp

Or directly::

    python -m dso_ckan_mcp.server

Or via uv::

    uv run dso-ckan-mcp
"""

from __future__ import annotations

import logging
import sys

import fastmcp

from . import prompts, resources
from .ckan_client import CKANClient
from .config import settings
from .schema_loader import SchemaLoader
from .tools import read, schema, validation, write, langsmith as langsmith_tools

# Configure logging to stderr so tool results (stdout) are clean.
logging.basicConfig(
    stream=sys.stderr,
    level=logging.INFO,
    format="%(asctime)s %(levelname)-8s %(name)s — %(message)s",
)

logger = logging.getLogger(__name__)

# ------------------------------------------------------------------
# FastMCP application
# ------------------------------------------------------------------

mcp = fastmcp.FastMCP(
    "DSO CKAN MCP Server",
    instructions=(
        "You are connected to the DSO CKAN data portal. "
        "Use the available tools to search datasets, inspect schemas, "
        "validate metadata, and discover portal capabilities. "
        "All read operations are anonymous (no API token required). "
        "Write tools (schema_create_package, schema_update_package, "
        "schema_create_resource) are available but require explicit user "
        "confirmation before performing live writes. Always call write tools "
        "with dry_run=True first to show the user a preview, then only set "
        "dry_run=False after the user explicitly instructs you to write "
        "(e.g. 'write it', 'submit', 'publish'). "
        "Write tools require CKAN_API_TOKEN to be configured. "
        "No delete tools are available in v1."
    ),
)

# ------------------------------------------------------------------
# Shared infrastructure
# ------------------------------------------------------------------

_client = CKANClient(
    base_url=settings.ckan_url,
    api_token=settings.ckan_api_token,
)

_loader = SchemaLoader(client=_client, ttl=settings.schema_cache_ttl)

# ------------------------------------------------------------------
# Register tool modules
# ------------------------------------------------------------------

read.register(mcp, _client)
schema.register(mcp, _loader)
validation.register(mcp, _loader)
prompts.register(mcp)
resources.register(mcp, _client, ttl=settings.schema_cache_ttl)

# Track B — write tools (token-gated, dry-run-first).
write.register(mcp, _client, _loader, settings)

# LangSmith tools — registered only when LANGSMITH_API_KEY is configured.
if settings.langsmith_api_key:
    from .langsmith_client import LangSmithClient as _LSClient
    _ls_client = _LSClient(
        api_key=settings.langsmith_api_key,
        endpoint=settings.langsmith_endpoint,
    )
    langsmith_tools.register(mcp, _ls_client)
    logger.info("LangSmith tools registered (endpoint=%s)", settings.langsmith_endpoint)
else:
    logger.info("LANGSMITH_API_KEY not set — LangSmith tools not registered")


# ------------------------------------------------------------------
# Entrypoint
# ------------------------------------------------------------------


def _build_http_app():
    """Build the Starlette ASGI app for HTTP transport, guarded by a shared secret.

    When ``MCP_HTTP_SHARED_SECRET`` is set, every request must carry
    ``Authorization: Bearer <secret>``; otherwise it is refused with 401. When the
    secret is unset the endpoint is unauthenticated — only acceptable for a strictly
    loopback dev bind (see config warnings).
    """
    from starlette.middleware import Middleware
    from starlette.responses import JSONResponse
    from starlette.types import ASGIApp, Receive, Scope, Send

    secret = settings.mcp_http_shared_secret

    class SharedSecretMiddleware:
        def __init__(self, app: ASGIApp) -> None:
            self.app = app

        async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
            if scope["type"] != "http" or not secret:
                await self.app(scope, receive, send)
                return
            headers = {k.decode().lower(): v.decode() for k, v in scope.get("headers", [])}
            provided = headers.get("authorization", "")
            expected = f"Bearer {secret}"
            # constant-time-ish comparison
            import hmac

            if not hmac.compare_digest(provided, expected):
                await JSONResponse({"error": "unauthorized"}, status_code=401)(scope, receive, send)
                return
            await self.app(scope, receive, send)

    return mcp.http_app(middleware=[Middleware(SharedSecretMiddleware)])


def main() -> None:
    """Start the MCP server. Transport is stdio by default, or HTTP when
    ``MCP_TRANSPORT=http`` (see config: host/port/shared-secret)."""
    settings.log_startup_banner()
    if settings.mcp_transport == "http":
        import uvicorn

        if not settings.mcp_http_shared_secret:
            logger.warning(
                "MCP HTTP transport is running WITHOUT a shared secret — the endpoint is "
                "UNAUTHENTICATED. Set MCP_HTTP_SHARED_SECRET unless this is a loopback-only dev bind."
            )
        logger.info(
            "Serving MCP over HTTP at http://%s:%d/mcp  (auth=%s)",
            settings.mcp_http_host,
            settings.mcp_http_port,
            "shared-secret" if settings.mcp_http_shared_secret else "none",
        )
        uvicorn.run(
            _build_http_app(),
            host=settings.mcp_http_host,
            port=settings.mcp_http_port,
            log_level="info",
        )
    else:
        mcp.run()


if __name__ == "__main__":
    main()
