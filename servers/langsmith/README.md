# DSO LangSmith MCP Server

FastMCP server exposing LangSmith tracing, prompt hub, datasets, and evaluation experiments as MCP tools.

## Quick start

```bash
cp .env.example .env   # fill LANGSMITH_API_KEY
uv run dso-langsmith-mcp
```

## HTTP mode

```bash
MCP_TRANSPORT=http MCP_HTTP_PORT=8300 MCP_HTTP_SHARED_SECRET=<secret> uv run dso-langsmith-mcp
```

## Tools

| Tool | Description |
|---|---|
| `list_projects` | List tracing projects with metrics |
| `fetch_runs` | Query runs; filter by type, error, FQL |
| `list_prompts` | Browse the prompt hub |
| `get_prompt` | Fetch a prompt by name |
| `list_datasets` | List evaluation datasets |
| `list_examples` | List examples from a dataset |
| `list_experiments` | List evaluation runs with aggregate metrics |

## Environment variables

| Variable | Required | Default |
|---|---|---|
| `LANGSMITH_API_KEY` | Yes | — |
| `LANGSMITH_ENDPOINT` | No | `https://api.smith.langchain.com` |
| `LANGSMITH_WORKSPACE_ID` | No | — |
| `MCP_TRANSPORT` | No | `stdio` |
| `MCP_HTTP_HOST` | No | `127.0.0.1` |
| `MCP_HTTP_PORT` | No | `8300` |
| `MCP_HTTP_SHARED_SECRET` | No | — |
