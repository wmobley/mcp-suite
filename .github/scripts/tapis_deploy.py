"""Deploy or restart a Tapis pod after a successful image build.

Environment variables set by the workflow:
  TAPIS_USERNAME, TAPIS_PASSWORD  — Tapis credentials (from secrets)
  POD_ID                          — Tapis pod identifier
  IMAGE                           — GHCR image name (without tag)
  CKAN_BASE_URL                   — forwarded to pod env
  CKAN_API_TOKEN                  — forwarded to pod env (Tapis JWT for CKAN write auth)
  MCP_HTTP_SHARED_SECRET          — forwarded to pod env
  MCP_ALLOW_PROD_WRITES           — forwarded to pod env (set "true" for prod)
"""
import os
import sys

from tapipy.tapis import Tapis

base_url           = "https://portals.tapis.io"
username           = os.environ["TAPIS_USERNAME"]
password           = os.environ["TAPIS_PASSWORD"]
pod_id             = os.environ["POD_ID"]
image              = os.environ["IMAGE"].lower() + ":latest"
ckan_url           = os.environ.get("CKAN_BASE_URL", "")
ckan_api_token     = os.environ.get("CKAN_API_TOKEN", "")
mcp_secret         = os.environ.get("MCP_HTTP_SHARED_SECRET", "")
ls_api_key         = os.environ.get("LANGSMITH_API_KEY", "")
allow_prod_writes  = os.environ.get("MCP_ALLOW_PROD_WRITES", "false")

POD_ENV = {
    "MCP_TRANSPORT":          "http",
    "MCP_HTTP_HOST":          "0.0.0.0",
    "MCP_HTTP_PORT":          "8100",
    "CKAN_BASE_URL":          ckan_url,
    "CKAN_API_TOKEN":         ckan_api_token,
    "MCP_HTTP_SHARED_SECRET": mcp_secret,
    "LANGSMITH_API_KEY":      ls_api_key,
    "MCP_ALLOW_PROD_WRITES":  allow_prod_writes,
}

print(f"Authenticating to {base_url} as {username}")
t = Tapis(base_url=base_url, username=username, password=password)
t.get_tokens()
print("Token obtained.")

# ── Check whether the pod already exists ──────────────────────────
pod_exists = False
try:
    t.pods.get_pod(pod_id=pod_id)
    pod_exists = True
except Exception as e:
    status = getattr(getattr(e, "response", None), "status_code", None)
    if status == 404:
        pod_exists = False
    else:
        print(f"Error checking pod: {e}", file=sys.stderr)
        sys.exit(1)

if pod_exists:
    print(f"Pod {pod_id} exists — updating env vars and restarting")
    t.pods.update_pod(pod_id=pod_id, environment_variables=POD_ENV)
    t.pods.restart_pod(pod_id=pod_id)
    print("Update + restart requested.")
else:
    print(f"Pod {pod_id} not found — creating")
    t.pods.create_pod(
        pod_id=pod_id,
        image=image,
        description="DSO CKAN MCP server (HTTP transport)",
        environment_variables=POD_ENV,
        networking={"default": {"protocol": "http", "port": 8100}},
    )
    print(f"Pod {pod_id} created.")
