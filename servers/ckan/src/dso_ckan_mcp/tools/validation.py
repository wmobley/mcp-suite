"""
Validation tool — client-side metadata completeness check.

Loads the scheming schema for a dataset type and runs
``validators.check_completeness`` against the proposed metadata.

The result explicitly disclaims that this is a client-side check only.
Full CKAN validation (cross-field, uniqueness, permission) runs on a
live write.

Tools registered here (1)
--------------------------
  validate_metadata — validate proposed metadata against a dataset schema
"""

from __future__ import annotations

from typing import Any

from ..schema_loader import SchemaLoader
from ..validators import check_completeness


def register(mcp: Any, loader: SchemaLoader) -> None:
    """Register all validation tools onto *mcp*.

    Parameters
    ----------
    mcp:
        The FastMCP application instance.
    loader:
        Shared :class:`~dso_ckan_mcp.schema_loader.SchemaLoader`.
    """

    @mcp.tool()
    def validate_metadata(
        dataset_type: str,
        metadata: dict[str, Any],
    ) -> dict[str, Any]:
        """Validate proposed dataset metadata against the scheming schema.

        Checks completeness client-side (required fields present and
        non-empty; unknown keys warned).  Does NOT call CKAN's write
        path — no side effects.

        What this check CAN catch
        -------------------------
        - Required fields that are missing or empty.
        - Unknown keys that are not in the schema (as warnings).
        - Basic date-format sanity for date-preset fields (YYYY-MM-DD).

        What this check CANNOT catch
        ----------------------------
        - Unique package name/slug collision.
        - Owner org membership.
        - API token permission.
        - Scheming cross-field validators.
        - Resource field constraints that depend on CKAN state.

        A result of ``valid=True`` does NOT guarantee the live write will
        succeed.  Always review the ``note`` field in the response.

        Parameters
        ----------
        dataset_type:
            A dataset type known to this portal (e.g. ``"mint_dataset"``).
            Validated against the portal allowlist before use.
        metadata:
            Proposed dataset metadata dict (field names → values).

        Returns
        -------
        dict
            ``{"valid": bool, "errors": [...], "warnings": [...], "note": str}``

        Raises
        ------
        ValueError
            If *dataset_type* is not in the portal's allowlist.
        """
        # validate_type is called inside get_schema — enforces allowlist
        # BEFORE any parameter interpolation.
        schema = loader.get_schema(dataset_type)
        return check_completeness(schema, metadata)
