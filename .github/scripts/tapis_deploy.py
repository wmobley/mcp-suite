"""Deploy or restart a Tapis pod after a successful image build.

Environment variables set by the workflow:
  TAPIS_USERNAME, TAPIS_PASSWORD  — Tapis credentials (from secrets)
  POD_ID                          — Tapis pod identifier
  IMAGE                           — GHCR image name (without tag)
  CKAN_BASE_URL                   — forwarded to pod env
  MCP_HTTP_SHARED_SECRET          — forwarded to pod env
"""
import os
import sys

from tapipy.tapis import Tapis

base_url   = "https://portals.tapis.io"
username   = os.environ["TAPIS_USERNAME"]
password   = os.environ["TAPIS_PASSWORD"]
pod_id     = os.environ["POD_ID"]
image      = os.environ["IMAGE"].lower() + ":latest"
ckan_url   = os.environ.get("CKAN_BASE_URL", "")
mcp_secret = os.environ.get("MCP_HTTP_SHARED_SECRET", "")

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
    print(f"Pod {pod_id} exists — restarting to pick up new image")
    t.pods.restart_pod(pod_id=pod_id)
    print("Restart requested.")
else:
    print(f"Pod {pod_id} not found — creating")
    t.pods.create_pod(
        pod_id=pod_id,
        image=image,
        description="DSO CKAN MCP server (HTTP transport)",
        environment_variables={
            "MCP_TRANSPORT":          "http",
            "MCP_HTTP_HOST":          "0.0.0.0",
            "MCP_HTTP_PORT":          "8100",
            "CKAN_BASE_URL":          ckan_url,
            "MCP_HTTP_SHARED_SECRET": mcp_secret,
        },
        networking={"default": {"protocol": "http", "port": 8100}},
    )
    print(f"Pod {pod_id} created.")
