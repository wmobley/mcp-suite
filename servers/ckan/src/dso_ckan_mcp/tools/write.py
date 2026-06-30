"""
Write tools — token-gated, dry-run-first CKAN writes.

All three tools default to ``dry_run=True``.  In dry-run mode NO HTTP POST
is made to CKAN; the tool performs a client-side completeness check (via
``validators.check_completeness``) and returns a preview dict.

Only after the user gives an explicit go-ahead ("write it", "submit", or a
clear variant) should the MCP client call the same tool with
``dry_run=False``.  That call runs the write-gate check, then POST to CKAN.

AUTHENTICATION MODEL
--------------------
Write tools authenticate via the portal's ``ckanext-oauth2`` plugin, which
accepts a Tapis OAuth2 JWT in the ``X-Tapis-Token`` header.  The token is
supplied PER-CALL by the caller via the ``tapis_token`` argument.  If the
caller omits it, the server falls back to the ``CKAN_API_TOKEN`` environment
variable (also a Tapis JWT, sent as ``X-Tapis-Token``).  If neither is
present on a live write, the write is refused.

The ``tapis_token`` argument:
  - Is NEVER stored, logged, or included in returned dicts.
  - Is NOT included in the ``call_args`` dict passed to audit.
  - Is ignored on dry-run (no token needed for dry-run validation).
  - Is short-lived (Tapis JWTs expire in ~hours); provide a fresh token per
    session.

WRITE GATE
----------
Before any live POST the shared ``_write_gate`` helper enforces:

  a. An effective token must be present (tapis_token arg OR CKAN_API_TOKEN
     env fallback) — writes without a token are rejected with a clear error
     dict (no network call).
  b. If the target CKAN_URL is classified as production (not localhost /
     127.0.0.1 / *.dev / *.test) AND ``MCP_ALLOW_PROD_WRITES`` is not
     ``True`` — writes are refused with a prominent error dict (no POST).

DRY-RUN VALIDATION HONESTY
---------------------------
The dry-run check is a CLIENT-SIDE COMPLETENESS CHECK ONLY.  It verifies
required fields are present and non-empty (using the scheming schema) and
flags basic date-format issues.  It CANNOT catch:

- Unique package name/slug collision (only CKAN knows existing names).
- Owner org membership (only CKAN/token knows permissions).
- Token permission (checked server-side on write).
- Scheming cross-field validators (run only by CKAN's validator chain).
- Resource field constraints that depend on CKAN state.

A ``dry_run=True`` result with ``valid=True`` does NOT guarantee the live
write will succeed.  Users must be prepared for live validation errors.

NO DELETE TOOLS
---------------
v1 deliberately omits delete tools.  Deletion requires a manual CKAN admin
action or a separate approval process.

Tools registered here (3)
--------------------------
  schema_create_package   — create a dataset with schema validation
  schema_update_package   — patch an existing dataset
  schema_create_resource  — create a resource (with optional file upload)
"""

from __future__ import annotations

import logging
from typing import Any

from .. import audit
from ..ckan_client import CKANAPIError, CKANClient
from ..config import Settings
from ..schema_loader import SchemaLoader
from ..upload import resolve_upload_path
from ..validators import check_completeness

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Write-gate helper
# ---------------------------------------------------------------------------


def _write_gate(
    client: CKANClient,
    settings: Settings,
    tool: str,
    effective_token: str | None,
) -> dict[str, Any] | None:
    """Run pre-write security checks.

    Returns ``None`` if the write is allowed, or an error dict (that the
    calling tool should return immediately — no POST made) if not.
    When a write is blocked, emits an audit log line (security-relevant event).

    Checks (in order):
    a. An effective token must be present (per-call tapis_token arg OR
       CKAN_API_TOKEN env fallback).  The token value is never logged.
    b. Production CKAN_URL requires MCP_ALLOW_PROD_WRITES=true.

    Parameters
    ----------
    client:
        The shared CKAN client (used only for URL reference here).
    settings:
        Loaded settings (prod guard + ckan_url).
    tool:
        MCP tool name, for audit log attribution.
    effective_token:
        The resolved token (``tapis_token`` arg OR ``settings.ckan_api_token``
        env fallback, already computed by the caller).  Must NEVER be logged.
    """
    if not effective_token:
        audit.log_blocked(tool=tool, ckan_url=settings.ckan_url, reason="no_token")
        return {
            "error": "writes_require_token",
            "message": (
                "No Tapis token available for this write. "
                "Pass a Tapis OAuth2 JWT as the tapis_token argument, or set "
                "CKAN_API_TOKEN in the server environment as a fallback. "
                "Tapis JWTs are short-lived (~hours) — provide a fresh token."
            ),
        }
    if settings.is_production and not settings.mcp_allow_prod_writes:
        audit.log_blocked(tool=tool, ckan_url=settings.ckan_url, reason="prod_guard")
        return {
            "error": "prod_writes_not_allowed",
            "message": (
                f"Refusing live write to production CKAN ({settings.ckan_url}). "
                "Set MCP_ALLOW_PROD_WRITES=true to allow writes to a non-localhost portal. "
                "Ensure you understand the risks before enabling this."
            ),
        }
    return None


# ---------------------------------------------------------------------------
# Tool registration
# ---------------------------------------------------------------------------


def register(
    mcp: Any,
    client: CKANClient,
    loader: SchemaLoader,
    settings: Settings,
) -> None:
    """Register all write tools onto *mcp*.

    Parameters
    ----------
    mcp:
        The FastMCP application instance.
    client:
        Shared :class:`~dso_ckan_mcp.ckan_client.CKANClient` (token-configured).
    loader:
        Shared :class:`~dso_ckan_mcp.schema_loader.SchemaLoader`.
    settings:
        Loaded :class:`~dso_ckan_mcp.config.Settings` (for prod guard + upload dir).
    """

    @mcp.tool()
    def schema_create_package(
        dataset_type: str,
        metadata: dict[str, Any],
        tapis_token: str | None = None,
        dry_run: bool = True,
    ) -> dict[str, Any]:
        """Create a new dataset on the CKAN portal with schema validation.

        DRY-RUN (dry_run=True, the default)
        ------------------------------------
        Validates *metadata* against the scheming schema for *dataset_type*
        using a CLIENT-SIDE COMPLETENESS CHECK.  No HTTP POST is made.
        Returns a preview dict with validation results.

        LIVE WRITE (dry_run=False)
        --------------------------
        Requires an explicit user instruction ("write it", "submit", or a
        clear variant) before calling with dry_run=False.  Runs the write
        gate (token required; production guard), refuses if validation has
        errors, then POSTs to CKAN's ``package_create`` action.  CKAN's
        full validator chain (uniqueness, org membership, cross-field, token
        permission) runs server-side.

        DRY-RUN CAVEAT: ``valid=True`` does NOT guarantee the live write
        succeeds.  Unique name collisions, org membership, and cross-field
        validators run only on the live write.

        Parameters
        ----------
        dataset_type:
            One of the portal's dataset types (e.g. ``"mint_dataset"``).
            Must be in the allowlist returned by ``list_dataset_types``.
        metadata:
            Dataset field dict (title, name, notes, owner_org, etc.).
            Required fields depend on the schema; use ``describe_dataset_schema``
            to inspect them.
        tapis_token:
            Tapis OAuth2 JWT for the live write, sent as ``X-Tapis-Token``.
            Required for ``dry_run=False`` unless the server has a
            ``CKAN_API_TOKEN`` env-configured fallback token.  Not needed
            for dry-run.  Never stored or logged.
        dry_run:
            Default ``True``.  Set to ``False`` only after user explicitly
            approves the dry-run preview.
        """
        # Allowlist check + schema load.
        try:
            loader.validate_type(dataset_type)
            schema = loader.get_schema(dataset_type)
        except ValueError as exc:
            return {"error": "invalid_dataset_type", "message": str(exc)}
        except CKANAPIError as exc:
            return {"error": "schema_load_failed", "message": str(exc)}

        validation = check_completeness(schema, metadata)

        if dry_run:
            return {
                "dry_run": True,
                "valid": validation["valid"],
                "errors": validation["errors"],
                "warnings": validation["warnings"],
                "preview": {**metadata, "type": dataset_type},
                "note": (
                    validation["note"] + " "
                    "This is a completeness check only — explicit user go-ahead needed. "
                    "Set dry_run=False to perform the actual write."
                ),
            }

        # Live write path.
        # Compute effective token: per-call arg wins over env fallback.
        # The token is NEVER put into call_args, returned dicts, or audit lines.
        effective_token = tapis_token or settings.ckan_api_token
        gate = _write_gate(client, settings, tool="schema_create_package", effective_token=effective_token)
        if gate:
            return gate

        if not validation["valid"]:
            return {
                "dry_run": False,
                "success": False,
                "error": "validation_failed",
                "errors": validation["errors"],
                "warnings": validation["warnings"],
                "message": (
                    "Metadata has validation errors. Fix the errors above "
                    "and try again (or re-run with dry_run=True to re-check)."
                ),
            }

        payload = {**metadata, "type": dataset_type}
        # call_args must NOT include tapis_token — never log or return the token.
        call_args = {"dataset_type": dataset_type, "metadata": metadata}

        try:
            result = client.post("package_create", data=payload, token=effective_token)
            result_id = result.get("id") if isinstance(result, dict) else None
            audit.log_write(
                tool="schema_create_package",
                args=call_args,
                ckan_url=settings.ckan_url,
                status=200,
                result_id=result_id,
            )
            return {
                "dry_run": False,
                "success": True,
                "id": result_id,
                "result": result,
            }
        except CKANAPIError as exc:
            audit.log_write(
                tool="schema_create_package",
                args=call_args,
                ckan_url=settings.ckan_url,
                status=exc.status_code,
                result_id=None,
            )
            return {
                "dry_run": False,
                "success": False,
                "error": "ckan_error",
                "message": str(exc),
            }

    @mcp.tool()
    def schema_update_package(
        id: str,
        metadata_updates: dict[str, Any],
        tapis_token: str | None = None,
        dry_run: bool = True,
    ) -> dict[str, Any]:
        """Update an existing dataset on the CKAN portal.

        DRY-RUN (dry_run=True, the default)
        ------------------------------------
        Fetches the current dataset to determine its ``dataset_type``, then
        validates *metadata_updates* against the schema for that type using a
        CLIENT-SIDE COMPLETENESS CHECK.  Only the fields present in
        *metadata_updates* are validated (patch semantics); fields not included
        are left unchanged.  No HTTP POST is made.

        LIVE WRITE (dry_run=False)
        --------------------------
        Requires an explicit user instruction before calling with dry_run=False.
        POSTs to CKAN's ``package_patch`` action.  CKAN's full validator chain
        runs server-side.

        DRY-RUN CAVEAT: ``valid=True`` does NOT guarantee the live write
        succeeds.  See schema_create_package for the full list of what
        dry-run cannot catch.

        Parameters
        ----------
        id:
            Dataset ID or name-slug of the dataset to update.
        metadata_updates:
            Fields to update (patch semantics — only provided fields are
            changed; omitted fields are left as-is by CKAN's patch action).
        tapis_token:
            Tapis OAuth2 JWT for the live write, sent as ``X-Tapis-Token``.
            Required for ``dry_run=False`` unless the server has a
            ``CKAN_API_TOKEN`` env-configured fallback token.  Not needed
            for dry-run.  Never stored or logged.
        dry_run:
            Default ``True``.  Set to ``False`` only after user explicitly
            approves the preview.
        """
        # Guard: empty updates are a no-op and almost certainly a caller mistake.
        if not metadata_updates:
            return {
                "error": "no_updates",
                "message": "metadata_updates is empty; nothing to update.",
            }

        # Fetch existing dataset to learn its dataset_type for schema validation.
        try:
            existing = client.get("package_show", params={"id": id})
        except CKANAPIError as exc:
            if exc.status_code == 404:
                return {
                    "error": "dataset_not_found",
                    "message": f"Dataset {id!r} not found. Check the id or name.",
                }
            # status_code == 0 → network/connection error; fall through to ckan_error.
            return {"error": "ckan_error", "message": str(exc)}

        dataset_type = (existing or {}).get("type", "dataset")

        # Validate only the provided fields against the schema (patch semantics).
        # Build a filtered schema that contains only the fields being updated so
        # check_completeness does not report missing required fields that are
        # absent from metadata_updates but already set on the existing dataset.
        try:
            full_schema = loader.get_schema(dataset_type)
        except (ValueError, CKANAPIError):
            full_schema = {"dataset_fields": []}

        patch_schema = {
            **full_schema,
            "dataset_fields": [
                f for f in full_schema.get("dataset_fields", [])
                if f.get("field_name") in metadata_updates
            ],
        }
        validation = check_completeness(patch_schema, metadata_updates)

        if dry_run:
            return {
                "dry_run": True,
                "id": id,
                "dataset_type": dataset_type,
                "valid": validation["valid"],
                "errors": validation["errors"],
                "warnings": validation["warnings"],
                "preview": {"id": id, **metadata_updates},
                "note": (
                    "Patch semantics: only the provided fields will be updated. "
                    + validation["note"] + " "
                    "Explicit user go-ahead needed; set dry_run=False to write."
                ),
            }

        # Live write path.
        # Compute effective token: per-call arg wins over env fallback.
        # The token is NEVER put into call_args, returned dicts, or audit lines.
        effective_token = tapis_token or settings.ckan_api_token
        gate = _write_gate(client, settings, tool="schema_update_package", effective_token=effective_token)
        if gate:
            return gate

        if not validation["valid"]:
            return {
                "dry_run": False,
                "success": False,
                "error": "validation_failed",
                "errors": validation["errors"],
                "warnings": validation["warnings"],
            }

        payload = {"id": id, **metadata_updates}
        # call_args must NOT include tapis_token — never log or return the token.
        call_args = {"id": id, "metadata_updates": metadata_updates}

        try:
            result = client.post("package_patch", data=payload, token=effective_token)
            result_id = (result or {}).get("id") if isinstance(result, dict) else None
            audit.log_write(
                tool="schema_update_package",
                args=call_args,
                ckan_url=settings.ckan_url,
                status=200,
                result_id=result_id,
            )
            return {
                "dry_run": False,
                "success": True,
                "id": result_id,
                "result": result,
            }
        except CKANAPIError as exc:
            audit.log_write(
                tool="schema_update_package",
                args=call_args,
                ckan_url=settings.ckan_url,
                status=exc.status_code,
                result_id=None,
            )
            return {
                "dry_run": False,
                "success": False,
                "error": "ckan_error",
                "message": str(exc),
            }

    @mcp.tool()
    def schema_create_resource(
        package_id: str,
        resource_metadata: dict[str, Any],
        upload_file: str | None = None,
        tapis_token: str | None = None,
        dry_run: bool = True,
    ) -> dict[str, Any]:
        """Create a resource on an existing CKAN dataset, with optional file upload.

        DRY-RUN (dry_run=True, the default)
        ------------------------------------
        Validates the UPLOAD PATH only (existence, size, and allowed directory
        via ``resolve_upload_path``) when *upload_file* is provided — NO bytes
        are sent to CKAN.  Resource-field validation (name, format, etc.) runs
        server-side on the live write; full field validation is deferred to v2
        because it requires a ``package_show`` fetch to determine dataset_type.
        Returns a preview of what would be uploaded.

        LIVE WRITE (dry_run=False)
        --------------------------
        Requires an explicit user instruction before calling with dry_run=False.
        POSTs to CKAN's ``resource_create`` action.  If *upload_file* is
        provided, the file bytes are sent as a multipart upload; CKAN writes
        them to its storage path (Corral-backed in production).  The file
        handle is closed after the upload.

        UPLOAD SAFETY:
        - The file path is resolved with ``os.path.realpath`` and must reside
          within ``MCP_UPLOAD_DIR`` (prevents ``..`` traversal and symlink
          escape).
        - Known-sensitive paths (``/etc``, ``~/.ssh``, ``~/.aws``, etc.) are
          rejected even if they somehow pass the directory check.
        - File size is checked via ``os.path.getsize`` BEFORE opening the file.
        - If no *upload_file* is provided, a metadata-only resource is created
          (still a live write; still requires the token).

        NOTE: This is a CKAN file upload (multipart ``resource_create``), NOT a
        Tapis Files API upload.  Bytes land on CKAN's ``ckan.storage_path``
        (Corral-backed in production).

        DRY-RUN CAVEAT: ``valid=True`` does NOT guarantee the live write
        succeeds.  See schema_create_package for the full list.

        Parameters
        ----------
        package_id:
            ID or name of the dataset to add this resource to.
        resource_metadata:
            Resource field dict (name, description, format, etc.).
        upload_file:
            Optional local file path.  Must reside within ``MCP_UPLOAD_DIR``.
            Omit for a metadata-only (external URL) resource.
        tapis_token:
            Tapis OAuth2 JWT for the live write, sent as ``X-Tapis-Token``.
            Required for ``dry_run=False`` unless the server has a
            ``CKAN_API_TOKEN`` env-configured fallback token.  Not needed
            for dry-run.  Never stored or logged.
        dry_run:
            Default ``True``.  Set to ``False`` only after user explicitly
            approves the preview.
        """
        # Validate upload path on dry-run if provided.
        upload_info: dict[str, Any] = {}
        if upload_file:
            try:
                resolved = resolve_upload_path(
                    path=upload_file,
                    allowed_dir=settings.mcp_upload_dir,
                    max_mb=settings.mcp_max_upload_mb,
                )
                upload_info = {
                    "upload_file": str(resolved),
                    "size_bytes": resolved.stat().st_size,
                    "size_mb": round(resolved.stat().st_size / (1024 * 1024), 2),
                }
            except ValueError as exc:
                return {
                    "error": "upload_path_invalid",
                    "message": str(exc),
                }

        if dry_run:
            return {
                "dry_run": True,
                "package_id": package_id,
                "resource_metadata": resource_metadata,
                "upload": upload_info if upload_file else None,
                "note": (
                    "Client-side completeness check only. "
                    "Full CKAN validation runs on the live write. "
                    "Explicit user go-ahead needed; set dry_run=False to write. "
                    + (
                        f"File validated: {upload_info.get('upload_file')} "
                        f"({upload_info.get('size_mb')} MB)."
                        if upload_info
                        else "No upload_file provided — metadata-only resource."
                    )
                ),
            }

        # Live write path.
        # Compute effective token: per-call arg wins over env fallback.
        # The token is NEVER put into call_args, returned dicts, or audit lines.
        effective_token = tapis_token or settings.ckan_api_token
        gate = _write_gate(client, settings, tool="schema_create_resource", effective_token=effective_token)
        if gate:
            return gate

        # Re-validate upload path (safety: validate again at write time,
        # even if dry-run validated it earlier, in case the file changed).
        upload_path = None
        if upload_file:
            try:
                upload_path = resolve_upload_path(
                    path=upload_file,
                    allowed_dir=settings.mcp_upload_dir,
                    max_mb=settings.mcp_max_upload_mb,
                )
            except ValueError as exc:
                return {
                    "error": "upload_path_invalid",
                    "message": str(exc),
                }

        payload = {"package_id": package_id, **resource_metadata}
        # call_args must NOT include tapis_token — never log or return the token.
        call_args = {
            "package_id": package_id,
            "resource_metadata": resource_metadata,
            "upload_file": upload_file,
        }

        fh = None
        try:
            files = None
            if upload_path:
                fh = open(upload_path, "rb")
                files = {"upload": fh}

            result = client.post("resource_create", data=payload, files=files, token=effective_token)
            result_id = (result or {}).get("id") if isinstance(result, dict) else None
            audit.log_write(
                tool="schema_create_resource",
                args=call_args,
                ckan_url=settings.ckan_url,
                status=200,
                result_id=result_id,
            )
            return {
                "dry_run": False,
                "success": True,
                "id": result_id,
                "result": result,
            }
        except CKANAPIError as exc:
            audit.log_write(
                tool="schema_create_resource",
                args=call_args,
                ckan_url=settings.ckan_url,
                status=exc.status_code,
                result_id=None,
            )
            return {
                "dry_run": False,
                "success": False,
                "error": "ckan_error",
                "message": str(exc),
            }
        finally:
            if fh is not None:
                fh.close()
