"""
Tests for upload.py — resolve_upload_path security checks.

All tests are pure unit tests (no HTTP calls, no network).  They use
``tmp_path`` (pytest built-in) for safe, isolated temporary directories.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from dso_ckan_mcp.upload import resolve_upload_path


# ---------------------------------------------------------------------------
# Helper: write a small real file
# ---------------------------------------------------------------------------


def _write_file(path: Path, size_bytes: int = 100) -> Path:
    """Write *size_bytes* of zero bytes to *path* and return it."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"\x00" * size_bytes)
    return path


# ---------------------------------------------------------------------------
# Upload disabled (allowed_dir is None)
# ---------------------------------------------------------------------------


def test_uploads_disabled_when_no_allowed_dir(tmp_path: Path) -> None:
    """Uploads are disabled when MCP_UPLOAD_DIR is not configured (None)."""
    f = _write_file(tmp_path / "data.csv")
    with pytest.raises(ValueError, match="MCP_UPLOAD_DIR"):
        resolve_upload_path(str(f), allowed_dir=None, max_mb=10)


def test_uploads_disabled_when_empty_string_allowed_dir(tmp_path: Path) -> None:
    """Empty string allowed_dir also disables uploads."""
    f = _write_file(tmp_path / "data.csv")
    with pytest.raises(ValueError, match="MCP_UPLOAD_DIR"):
        resolve_upload_path(str(f), allowed_dir="", max_mb=10)


# ---------------------------------------------------------------------------
# Valid path accepted
# ---------------------------------------------------------------------------


def test_valid_file_inside_allowed_dir_accepted(tmp_path: Path) -> None:
    """A file inside the allowed dir is accepted and the resolved Path returned."""
    allowed = tmp_path / "uploads"
    f = _write_file(allowed / "data.csv")
    result = resolve_upload_path(str(f), allowed_dir=str(allowed), max_mb=10)
    assert result == f.resolve()
    assert result.is_file()


def test_valid_file_in_subdirectory_accepted(tmp_path: Path) -> None:
    """A file in a subdirectory of allowed_dir is also accepted."""
    allowed = tmp_path / "uploads"
    f = _write_file(allowed / "subdir" / "data.csv")
    result = resolve_upload_path(str(f), allowed_dir=str(allowed), max_mb=10)
    assert result.is_file()


# ---------------------------------------------------------------------------
# Traversal rejected
# ---------------------------------------------------------------------------


def test_dotdot_traversal_rejected(tmp_path: Path) -> None:
    """Path traversal via '..' components is rejected."""
    allowed = tmp_path / "uploads"
    allowed.mkdir(parents=True, exist_ok=True)
    # Construct a traversal attempt: inside uploads then back out.
    victim = tmp_path / "secret.txt"
    victim.write_bytes(b"secret")
    traversal = str(allowed / ".." / "secret.txt")
    with pytest.raises(ValueError, match="outside"):
        resolve_upload_path(traversal, allowed_dir=str(allowed), max_mb=10)


def test_path_outside_allowed_dir_rejected(tmp_path: Path) -> None:
    """A path that simply points outside allowed_dir is rejected."""
    allowed = tmp_path / "uploads"
    allowed.mkdir(parents=True, exist_ok=True)
    outside = tmp_path / "other" / "data.csv"
    _write_file(outside)
    with pytest.raises(ValueError, match="outside"):
        resolve_upload_path(str(outside), allowed_dir=str(allowed), max_mb=10)


def test_symlink_escape_rejected(tmp_path: Path) -> None:
    """A symlink inside allowed_dir that points outside is rejected."""
    allowed = tmp_path / "uploads"
    allowed.mkdir(parents=True, exist_ok=True)
    target = tmp_path / "outside_target.txt"
    _write_file(target)
    link = allowed / "link.csv"
    link.symlink_to(target)
    with pytest.raises(ValueError, match="outside"):
        resolve_upload_path(str(link), allowed_dir=str(allowed), max_mb=10)


# ---------------------------------------------------------------------------
# Sensitive path rejected
# ---------------------------------------------------------------------------


def test_sensitive_etc_prefix_rejected(tmp_path: Path) -> None:
    """/etc paths are rejected even if inside allowed_dir (misconfigured dir)."""
    # Simulate the scenario: allowed_dir *is* /etc (worst-case misconfiguration).
    # We can't actually create files in /etc in tests, so mock realpath.
    import unittest.mock as mock

    with mock.patch("os.path.realpath") as m:
        # Resolve to /etc/passwd regardless of input.
        def fake_realpath(p: str) -> str:
            if "etc" in p:
                return "/etc/passwd"
            return "/etc"  # allowed_dir also becomes /etc

        m.side_effect = fake_realpath
        with pytest.raises(ValueError, match="sensitive"):
            resolve_upload_path("/etc/passwd", allowed_dir="/etc", max_mb=10)


def test_sensitive_ssh_prefix_rejected(tmp_path: Path) -> None:
    """~/.ssh paths are rejected."""
    ssh_dir = Path("~/.ssh").expanduser()
    import unittest.mock as mock

    ssh_path = str(ssh_dir / "id_rsa")
    allowed = tmp_path / "uploads"
    allowed.mkdir(parents=True, exist_ok=True)

    with mock.patch("os.path.realpath") as m:
        def fake_realpath(p: str) -> str:
            if "id_rsa" in p:
                return ssh_path
            return str(allowed)

        m.side_effect = fake_realpath
        # Also need to fake stat for size check — but sensitive check runs first.
        with pytest.raises(ValueError, match="sensitive"):
            resolve_upload_path(ssh_path, allowed_dir=str(allowed), max_mb=10)


# ---------------------------------------------------------------------------
# File does not exist
# ---------------------------------------------------------------------------


def test_missing_file_rejected(tmp_path: Path) -> None:
    """A path that does not exist is rejected."""
    allowed = tmp_path / "uploads"
    allowed.mkdir(parents=True, exist_ok=True)
    nonexistent = allowed / "nope.csv"
    with pytest.raises(ValueError, match="does not exist"):
        resolve_upload_path(str(nonexistent), allowed_dir=str(allowed), max_mb=10)


def test_directory_not_accepted_as_file(tmp_path: Path) -> None:
    """A directory path is rejected (must be a regular file)."""
    allowed = tmp_path / "uploads"
    allowed.mkdir(parents=True, exist_ok=True)
    subdir = allowed / "subdir"
    subdir.mkdir()
    with pytest.raises(ValueError, match="not a regular file"):
        resolve_upload_path(str(subdir), allowed_dir=str(allowed), max_mb=10)


# ---------------------------------------------------------------------------
# Oversized file rejected BEFORE open()
# ---------------------------------------------------------------------------


def test_oversized_file_rejected_before_open(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Files larger than max_mb are rejected via getsize — open() is NOT called."""
    allowed = tmp_path / "uploads"
    f = _write_file(allowed / "big.bin", size_bytes=100)

    open_calls: list[str] = []
    original_open = open

    def tracking_open(path: object, *args: object, **kwargs: object) -> object:
        open_calls.append(str(path))
        return original_open(path, *args, **kwargs)  # type: ignore[call-overload]

    monkeypatch.setitem(
        __builtins__ if isinstance(__builtins__, dict) else vars(__builtins__),  # type: ignore[arg-type]
        "open",
        tracking_open,
    )

    # 100 bytes; max = 0 MB (0 bytes) — should fail on size check.
    with pytest.raises(ValueError, match="exceeds the configured limit"):
        resolve_upload_path(str(f), allowed_dir=str(allowed), max_mb=0)

    # open() must NOT have been called.
    assert open_calls == [], f"open() was called unexpectedly: {open_calls}"


def test_oversized_file_rejected_getsize(tmp_path: Path) -> None:
    """A file that exceeds max_mb raises with a helpful message."""
    allowed = tmp_path / "uploads"
    # Write 2 bytes; restrict to 0 MB (0 bytes effectively).
    f = _write_file(allowed / "data.bin", size_bytes=2)
    with pytest.raises(ValueError, match="exceeds the configured limit"):
        resolve_upload_path(str(f), allowed_dir=str(allowed), max_mb=0)


def test_file_within_limit_accepted(tmp_path: Path) -> None:
    """A file within the size limit is accepted without error."""
    allowed = tmp_path / "uploads"
    f = _write_file(allowed / "small.csv", size_bytes=100)
    result = resolve_upload_path(str(f), allowed_dir=str(allowed), max_mb=1)
    assert result.is_file()
