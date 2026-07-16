"""
DSO LangSmith MCP Server — entrypoint.

Creates the FastMCP application, instantiates the LangSmith client, and
registers all tools:

  runs        — list_projects, fetch_runs
  prompts     — list_prompts, get_prompt
  datasets    — list_datasets, list_examples
  experiments — list_experiments

Usage
-----
Run via the installed script::

    dso-langsmith-mcp

Or directly::

    python -m dso_langsmith_mcp.server

Or via uv::

    uv run dso-langsmith-mcp
"""

from __future__ import annotations

import logging
import sys

import fastmcp

from .config import settings
from .langsmith_client import LangSmithClient
from .tools import datasets, experiments, prompts, runs

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
    "DSO LangSmith MCP Server",
    instructions=(
        "You are connected to LangSmith, LangChain's observability and evaluation platform. "
        "Use the available tools to inspect tracing projects, fetch runs, browse the prompt hub, "
        "explore datasets, and review evaluation experiments. "
        "All tools are read-only — no writes are performed. "
        "Use list_projects to discover available projects, then fetch_runs to drill into traces. "
        "Use list_datasets and list_examples to explore evaluation datasets. "
        "Use list_experiments to review evaluation runs with aggregate metrics."
    ),
)

# ------------------------------------------------------------------
# Shared client
# ------------------------------------------------------------------

_client = LangSmithClient(
    api_key=settings.langsmith_api_key or "",
    endpoint=settings.langsmith_endpoint,
    workspace_id=settings.langsmith_workspace_id,
)

# ------------------------------------------------------------------
# Register tool modules
# ------------------------------------------------------------------

runs.register(mcp, _client)
prompts.register(mcp, _client)
datasets.register(mcp, _client)
experiments.register(mcp, _client)


# ------------------------------------------------------------------
# HTTP transport (mirrors CKAN / Geo pattern)
# ------------------------------------------------------------------

def _build_http_app():
    """Build the Starlette ASGI app for HTTP transport, guarded by a shared secret."""
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
            import hmac
            if not hmac.compare_digest(provided, expected):
                await JSONResponse({"error": "unauthorized"}, status_code=401)(scope, receive, send)
                return
            await self.app(scope, receive, send)

    return mcp.http_app(middleware=[Middleware(SharedSecretMiddleware)])


# ------------------------------------------------------------------
# Entrypoint
# ------------------------------------------------------------------

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
            "Serving MCP over HTTP at http://%s:%d/mcp",
            settings.mcp_http_host,
            settings.mcp_http_port,
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
