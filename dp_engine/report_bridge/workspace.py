"""Report Bridge workspace management (Batch 3.3.1A).

All bridge workspaces live under <app-data>/skills/report_bridge/.
Each request gets its own directory named by the 32-hex request_id.

Security: lstat verification at every level, symlink/reparse rejection,
fail-closed, no path traversal, no following links.
"""

from __future__ import annotations

import logging
import os
import re
import shutil
import stat as _stat
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path

from utils.app_paths import get_skills_root

logger = logging.getLogger(__name__)

_BRIDGE_DIR_NAME = "report_bridge"
_HEX32_RE = re.compile(r"^[0-9a-fA-F]{32}$")
_MAX_ORPHAN_CLEANUP = 100
_ORPHAN_MAX_AGE_HOURS = 24


# ── Injectable verification boundary (testable on any platform) ──

def _platform_is_symlink_or_reparse(path: Path) -> bool:
    """Check if a path is a symlink/reparse point using platform lstat.

    Injectable for testing — tests may monkey-patch this to simulate
    Windows reparse points on non-Windows platforms.
    """
    try:
        st = path.lstat()
    except OSError:
        return False
    if _stat.S_ISLNK(st.st_mode):
        return True
    # Windows: FILE_ATTRIBUTE_REPARSE_POINT
    if hasattr(st, "st_file_attributes"):
        FILE_ATTRIBUTE_REPARSE_POINT = 0x400
        if st.st_file_attributes & FILE_ATTRIBUTE_REPARSE_POINT:
            return True
    return False


def _verify_no_symlink(path: Path) -> None:
    """Raise RuntimeError if path is a symlink or reparse point."""
    if _platform_is_symlink_or_reparse(path):
        raise RuntimeError(
            f"Path is a symlink/reparse point — rejected: {path}"
        )


def _verify_within_root(path: Path, root: Path) -> None:
    """Verify a resolved path is within the given root directory.

    Also checks each parent component for symlinks.
    """
    resolved = path.resolve()
    root_resolved = root.resolve()
    try:
        resolved.relative_to(root_resolved)
    except ValueError:
        raise RuntimeError(
            f"Path escapes root: {path} is not within {root_resolved}"
        )
    # Walk parent components
    _verify_parent_chain(path, root_resolved)


def _verify_parent_chain(path: Path, root: Path) -> None:
    """Check each parent component from root to path for symlink/reparse."""
    resolved = path.resolve()
    try:
        rel = resolved.relative_to(root)
    except ValueError:
        return
    parts = rel.parts
    current = root
    for part in parts[:-1]:
        current = current / part
        if not current.exists():
            break
        if _platform_is_symlink_or_reparse(current):
            raise RuntimeError(
                f"Parent component is symlink/reparse: {current}"
            )


# ── Root management ──


def get_report_bridge_root() -> Path:
    """Return the verified report_bridge root directory.

    Creates root if it doesn't exist, verifying all parent components.
    """
    root = get_skills_root() / _BRIDGE_DIR_NAME
    if not root.exists():
        # Verify parent
        parent = get_skills_root()
        if not parent.exists():
            parent.mkdir(parents=True, exist_ok=True)
        _verify_no_symlink(parent)
        root.mkdir(exist_ok=False)
    _verify_no_symlink(root)
    return root


# ── Workspace creation ──


def create_report_bridge_workspace(request_id: str) -> Path:
    """Create a safe workspace directory for a bridge request.

    Args:
        request_id: Must be a 32-character hex string.

    Returns:
        The newly created workspace Path.

    Raises:
        ValueError: request_id is invalid.
        RuntimeError: Security violation (symlink/reparse detected).
        OSError: Filesystem error.
    """
    if not _HEX32_RE.match(request_id):
        raise ValueError(
            f"request_id must be a 32-character hex string, got: {request_id!r}"
        )

    root = get_report_bridge_root()

    # Step 1: Verify root safety
    _verify_no_symlink(root)
    _verify_within_root(root, get_skills_root())

    # Step 2: Verify root by lstat
    try:
        root.lstat()
    except OSError as e:
        raise RuntimeError(
            f"Cannot lstat bridge root: {e}"
        )

    # Step 3: The target path
    workspace = root / request_id

    # Step 4: Verify parent components from root to workspace
    # (root already verified; workspace's parent is root itself)

    # Step 5: Reject if already exists
    if workspace.exists():
        raise FileExistsError(
            f"Workspace already exists: {workspace}"
        )

    # Step 6: Create with exist_ok=False
    workspace.mkdir(exist_ok=False)

    # Step 7: Re-verify after creation
    if not workspace.is_dir():
        workspace.rmdir()
        raise RuntimeError("Created workspace is not a directory")
    _verify_no_symlink(workspace)
    if workspace.resolve() != root.resolve() / request_id:
        # Path may have been redirected
        try:
            workspace.rmdir()
        except OSError:
            pass
        raise RuntimeError("Workspace resolved to unexpected path")

    logger.info("Created bridge workspace: %s", workspace)
    return workspace


# ── Safe release ──


def safe_release_report_bridge_workspace(
    request_id: str,
    workspace_path: Path,
) -> None:
    """Safely delete a bridge workspace directory.

    Only deletes if:
    - workspace_path is a direct child of report_bridge_root
    - The directory name equals request_id
    - No symlink/reparse at workspace_path

    Does NOT raise on cleanup failure — logs a sanitized warning.
    """
    root = get_report_bridge_root()

    try:
        # Verify root
        if not root.is_dir():
            logger.warning("Bridge root does not exist, cannot clean workspace")
            return

        _verify_no_symlink(root)

        # Verify workspace_path is a direct child of root
        if workspace_path.parent != root:
            logger.warning(
                "Workspace path is not a direct child of bridge root; skipping"
            )
            return

        # Verify directory name matches request_id
        if workspace_path.name != request_id:
            logger.warning(
                "Workspace directory name does not match request_id; skipping"
            )
            return

        # Verify no symlink on workspace
        if workspace_path.exists():
            _verify_no_symlink(workspace_path)
            shutil.rmtree(str(workspace_path), ignore_errors=False)
            logger.info("Released bridge workspace for request %s", request_id)
    except OSError:
        logger.warning(
            "Failed to clean bridge workspace (error_code=workspace_io_failed)"
        )
    except RuntimeError:
        logger.warning(
            "Bridge workspace security check failed during cleanup"
        )


# ── Orphan cleanup ──


def cleanup_orphan_report_bridge_workspaces() -> int:
    """Clean up orphaned bridge workspaces older than 24 hours.

    Only scans direct children of report_bridge_root.
    Only processes 32-hex directory names.
    Max 100 directories per call.
    Suspicious entries are left in place (fail-closed).

    Returns:
        Number of directories cleaned.
    """
    root = get_report_bridge_root()
    if not root.is_dir():
        return 0

    try:
        _verify_no_symlink(root)
    except RuntimeError:
        logger.warning("Bridge root symlink detected during orphan cleanup; aborting")
        return 0

    cutoff = datetime.now(timezone.utc) - timedelta(hours=_ORPHAN_MAX_AGE_HOURS)
    cleaned = 0

    try:
        with os.scandir(str(root)) as entries:
            for entry in entries:
                if cleaned >= _MAX_ORPHAN_CLEANUP:
                    break

                # Only direct children
                if not entry.is_dir(follow_symlinks=False):
                    continue

                # Only 32-hex directory names
                if not _HEX32_RE.match(entry.name):
                    logger.debug("Skipping non-hex32 directory in bridge root: %s", entry.name)
                    continue

                entry_path = Path(entry.path)

                # Reject symlink/reparse
                if _platform_is_symlink_or_reparse(entry_path):
                    logger.warning(
                        "Skipping symlink/reparse orphan: %s", entry.name
                    )
                    continue

                # Check age
                try:
                    st = entry.stat()
                    mtime = datetime.fromtimestamp(st.st_mtime, tz=timezone.utc)
                    if mtime >= cutoff:
                        continue
                except OSError:
                    continue

                # Delete
                try:
                    shutil.rmtree(str(entry_path), ignore_errors=False)
                    cleaned += 1
                    logger.info("Cleaned orphan bridge workspace: %s", entry.name)
                except OSError:
                    logger.warning(
                        "Failed to clean orphan bridge workspace (error_code=workspace_io_failed)"
                    )
    except OSError:
        logger.warning("Failed to scan bridge root for orphans")

    return cleaned
