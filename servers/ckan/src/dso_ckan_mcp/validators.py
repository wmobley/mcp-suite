"""
Client-side metadata completeness checker.

This is a LIGHTWEIGHT client-side check only.  It verifies:
  - Required fields (per the scheming schema) are present and non-empty.
  - No unexpected (unknown) fields are in the submitted metadata.
  - Basic date-format sanity for date-preset fields (ISO 8601 YYYY-MM-DD).

What this check CANNOT catch
-----------------------------
- Unique package name/slug collision (only CKAN knows existing names).
- Owner org membership (only CKAN/token knows permissions).
- API token permission (checked at write time by CKAN).
- Scheming cross-field validators (run only by CKAN's validator chain).
- Resource field constraints that depend on CKAN state.

A dry_run result of ``valid=True`` does NOT guarantee the live write will
succeed.  The full CKAN validator chain runs only on a live write call.
"""

from __future__ import annotations

import re
from typing import Any

# Regex for basic ISO 8601 date (YYYY-MM-DD).
_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")

# Presets that should contain an ISO date string.
_DATE_PRESETS = {"date", "datetime", "datetime_tz"}

# The disclaimer note always included in the result dict.
_DISCLAIMER = (
    "Client-side completeness check only. "
    "The full CKAN validator chain (unique name, owner_org membership, "
    "cross-field validators, token permission) runs only on a live write "
    "and may reject metadata that passes this check."
)


def _is_required(field: dict[str, Any]) -> bool:
    """Return True if a scheming field definition marks the field as required.

    CKAN scheming marks a field required in three observable ways:

    1. ``required: true`` in the field YAML (explicit boolean).
    2. ``scheming_required`` appearing in the field's ``validators`` string
       (the portal's mint_dataset uses this pattern for temporal fields).
    3. ``not_empty`` appearing in the field's ``validators`` string — the
       core CKAN pattern for implicitly-required fields such as ``name``
       (``"not_empty unicode_safe name_validator package_name_validator"``).

    Note: CKAN core ``not_empty`` is only used on genuinely required fields,
    so detecting it does not introduce false positives.  This still does not
    cover every server-side validator (see the module disclaimer).

    Parameters
    ----------
    field:
        A field dict from ``dataset_fields`` or ``resource_fields`` as
        returned by ``scheming_dataset_schema_show``.
    """
    if field.get("required"):
        return True
    validators_str: str = field.get("validators") or ""
    return "scheming_required" in validators_str or "not_empty" in validators_str


def check_completeness(
    schema: dict[str, Any],
    metadata: dict[str, Any],
) -> dict[str, Any]:
    """Validate *metadata* for completeness against *schema*.

    Parameters
    ----------
    schema:
        A scheming dataset schema dict as returned by
        ``scheming_dataset_schema_show`` — must contain a ``dataset_fields``
        list where each field dict has at least ``field_name``.
    metadata:
        The proposed dataset metadata dict to validate.

    Returns
    -------
    dict
        ``{"valid": bool, "errors": [...], "warnings": [...], "note": str}``

        - ``errors`` — fields that must be fixed before a write can succeed
          (required fields missing/empty).
        - ``warnings`` — advisory notices (unknown keys in metadata that are
          not in the schema).
        - ``note`` — the disclaimer that this is client-side only.
    """
    errors: list[str] = []
    warnings: list[str] = []

    dataset_fields: list[dict[str, Any]] = schema.get("dataset_fields", [])

    # Build lookup: field_name → field definition.
    field_by_name: dict[str, dict[str, Any]] = {
        f["field_name"]: f for f in dataset_fields if "field_name" in f
    }
    known_names = set(field_by_name)

    # 1. Required fields — must be present and non-empty.
    #    A field is required if it has required=true OR "scheming_required"
    #    in its validators string (the pattern used by this portal's schemas).
    for field in dataset_fields:
        fname = field.get("field_name")
        if not fname:
            continue
        if _is_required(field):
            value = metadata.get(fname)
            if value is None or (isinstance(value, str) and not value.strip()):
                errors.append(f"Required field {fname!r} is missing or empty.")
            else:
                # Basic date sanity for date-preset fields.
                preset = field.get("preset", "")
                if preset in _DATE_PRESETS and isinstance(value, str):
                    if not _DATE_RE.match(value):
                        errors.append(
                            f"Field {fname!r} expects an ISO date (YYYY-MM-DD); "
                            f"got {value!r}."
                        )

    # 2. Unknown keys — keys in metadata that are not schema fields.
    # Some standard CKAN fields (name, owner_org, etc.) may not appear in
    # dataset_fields for every schema; we warn rather than error.
    for key in metadata:
        if key not in known_names:
            warnings.append(
                f"Key {key!r} is not in the schema for this dataset type "
                f"(may be a standard CKAN field or a typo)."
            )

    return {
        "valid": len(errors) == 0,
        "errors": errors,
        "warnings": warnings,
        "note": _DISCLAIMER,
    }
