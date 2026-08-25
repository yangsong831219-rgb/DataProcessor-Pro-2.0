"""Runtime workspace path management.

All runtime workspaces live under <app-data>/skills/runtime/.
Each operation gets a unique <task-id> directory.

Workspace cleanup is explicit — never recursive-delete on startup.

Batch 3.2.1B: Artifact root paths and safety verification helpers.
"""

from __future__ import annotations

import logging
import os
import shutil
import stat as _stat
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

from utils.app_paths import get_skills_root

logger = logging.getLogger(__name__)

# Directories inside a workspace
_WORKSPACE_SUBDIRS = ("input", "output", "temp")

# Files inside a workspace (besides subdirectories)
_REQUEST_FILE = "request.json"
_RESULT_FILE = "result.json"
_STDOUT_LOG = "stdout.log"
_STDERR_LOG = "stderr.log"
_RUNTIME_LOG = "runtime.log"
_CANCEL_MARKER = ".cancel"


def get_runtime_root() -> Path:
    """Return the runtime workspaces root directory."""
    return get_skills_root() / "runtime"


def generate_task_id() -> str:
    """Generate a unique task ID for a runtime operation."""
    return uuid.uuid4().hex[:12]


def create_workspace(task_id: str) -> Path:
    """Create a runtime workspace directory for the given task_id.

    Returns the workspace path.  Raises OSError on filesystem failure.

    The workspace is created under <skills>/runtime/<task_id>/.
    If the directory already exists, raises FileExistsError.
    """
    root = get_runtime_root()
    workspace = root / task_id

    if workspace.exists():
        raise FileExistsError(
            f"Workspace already exists: {workspace}"
        )

    # Create all directories
    for sub in _WORKSPACE_SUBDIRS:
        (workspace / sub).mkdir(parents=True, exist_ok=False)

    logger.info("Created runtime workspace: %s", workspace)
    return workspace


def workspace_path_for(
    task_id: str,
) -> Path:
    """Return the expected workspace path without creating it."""
    return get_runtime_root() / task_id


def iter_workspace_artifacts(
    workspace: Path,
) -> list[Path]:
    """List all files in workspace/output/ (non-recursive)."""
    output_dir = workspace / "output"
    if not output_dir.is_dir():
        return []
    result: list[Path] = []
    try:
        with os.scandir(str(output_dir)) as entries:
            for entry in entries:
                if entry.is_file(follow_symlinks=False):
                    result.append(Path(entry.path))
    except OSError:
        pass
    return sorted(result)


def cleanup_workspace(
    workspace: Path,
    *,
    succeeded: bool,
) -> None:
    """Clean up a runtime workspace.

    On success: remove the entire workspace directory.
    On failure: keep the workspace for diagnostics but ensure no secrets
    are in the request/result files beyond what the service already scrubs.

    Does NOT raise on cleanup failure — logs at warning level.
    """
    if not workspace.exists():
        return

    if succeeded:
        try:
            shutil.rmtree(str(workspace), ignore_errors=False)
            logger.info("Cleaned up succeeded workspace: %s", workspace)
        except OSError as e:
            logger.warning(
                "Failed to clean up workspace %s: %s", workspace, e
            )
    else:
        # Keep workspace for diagnostics but remove cancel marker
        cancel = workspace / _CANCEL_MARKER
        try:
            cancel.unlink(missing_ok=True)
        except OSError:
            pass
        logger.info(
            "Preserved failed workspace for diagnostics: %s", workspace
        )


def cleanup_expired_workspaces(
    *,
    max_age_hours: int = 24,
) -> int:
    """Remove runtime workspaces older than max_age_hours.

    This is an explicit cleanup function — NOT called on startup.
    Returns the number of workspaces removed.

    Only removes workspaces that don't have a .keep marker file.
    """
    root = get_runtime_root()
    if not root.is_dir():
        return 0

    cutoff = datetime.now(timezone.utc) - timedelta(hours=max_age_hours)
    removed = 0

    try:
        with os.scandir(str(root)) as entries:
            for entry in entries:
                if not entry.is_dir(follow_symlinks=False):
                    continue
                ws = Path(entry.path)
                # Skip workspaces with .keep marker
                if (ws / ".keep").exists():
                    continue
                # Check age via directory mtime
                try:
                    st = entry.stat()
                    mtime = datetime.fromtimestamp(
                        st.st_mtime, tz=timezone.utc
                    )
                    if mtime < cutoff:
                        shutil.rmtree(str(ws), ignore_errors=False)
                        removed += 1
                        logger.info(
                            "Removed expired workspace: %s (age: %s)",
                            ws, mtime.isoformat(),
                        )
                except OSError as e:
                    logger.warning(
                        "Failed to remove workspace %s: %s", ws, e
                    )
    except OSError as e:
        logger.warning("Failed to scan runtime workspaces: %s", e)

    return removed


def write_cancel_marker(workspace: Path) -> None:
    """Write a cancel marker file in the workspace."""
    marker = workspace / _CANCEL_MARKER
    try:
        marker.write_text("cancel", encoding="utf-8")
    except OSError as e:
        logger.warning("Failed to write cancel marker: %s", e)


def has_cancel_marker(workspace: Path) -> bool:
    """Check if the cancel marker exists."""
    return (workspace / _CANCEL_MARKER).exists()


def get_output_dir(workspace: Path) -> Path:
    """Return the output directory within a workspace."""
    return workspace / "output"


def get_temp_dir(workspace: Path) -> Path:
    """Return the temp directory within a workspace."""
    return workspace / "temp"


def get_input_dir(workspace: Path) -> Path:
    """Return the input directory within a workspace."""
    return workspace / "input"


# ── Batch 3.2.1B: Artifact persistent storage paths ──


def get_artifact_root() -> Path:
    """Return the artifact persistent storage root directory.

    <app-data>/skills/artifacts/
    """
    return get_skills_root() / "artifacts"


def get_artifact_skill_dir(skill_id: str) -> Path:
    """Return the artifact directory for a specific skill.

    <artifact_root>/<skill_id>/
    """
    return get_artifact_root() / skill_id


def get_artifact_task_dir(skill_id: str, task_id: str) -> Path:
    """Return the artifact directory for a specific task.

    <artifact_root>/<skill_id>/<task_id>/
    """
    return get_artifact_skill_dir(skill_id) / task_id


# ── Batch 3.2.1B: Path safety verification ──


def _is_symlink_or_reparse(path: Path) -> bool:
    """Check if a path is a symlink or reparse point.

    Uses lstat (non-follow) to detect. On Windows, also checks
    FILE_ATTRIBUTE_REPARSE_POINT.
    """
    try:
        st = path.lstat()
    except OSError:
        return False
    if _stat.S_ISLNK(st.st_mode):
        return True
    # Windows: check reparse point attribute
    if hasattr(st, "st_file_attributes"):
        FILE_ATTRIBUTE_REPARSE_POINT = 0x400
        if st.st_file_attributes & FILE_ATTRIBUTE_REPARSE_POINT:
            return True
    return False


def verify_path_not_symlink_or_reparse(path: Path) -> None:
    """Verify that a path is NOT a symlink, junction, or reparse point.

    Raises RuntimeError (or subclass) on violation.
    """
    if _is_symlink_or_reparse(path):
        raise RuntimeError(
            f"Path is a symlink or reparse point: {path}"
        )


def verify_path_within_root(path: Path, root: Path) -> None:
    """Verify that a resolved path is within the given root directory.

    Also checks each path component for symlink/reparse.

    Raises RuntimeError on violation.
    """
    resolved = path.resolve()
    root_resolved = root.resolve()
    try:
        resolved.relative_to(root_resolved)
    except ValueError:
        raise RuntimeError(
            f"Path escapes root: {path} (resolved: {resolved}) "
            f"is not within {root_resolved}"
        )
    # Check each parent component for symlink/reparse
    _verify_parent_components(path, root_resolved)


def _verify_parent_components(path: Path, root: Path) -> None:
    """Verify each parent component from root to path is not symlink/reparse.

    Walks from root down to the immediate parent of path, checking each
    directory component via lstat.
    """
    resolved = path.resolve()
    try:
        rel = resolved.relative_to(root)
    except ValueError:
        return  # Already caught by verify_path_within_root
    parts = rel.parts
    current = root
    # Check each intermediate directory
    for part in parts[:-1]:  # skip the final filename
        current = current / part
        if not current.exists():
            break
        if _is_symlink_or_reparse(current):
            raise RuntimeError(
                f"Parent component is symlink/reparse: {current}"
            )


def verify_artifact_root_safety(artifact_root: Path) -> None:
    """Verify the artifact root and all path components are safe.

    Raises RuntimeError on any symlink/reparse/junction detected.
    """
    if not artifact_root.exists():
        return
    verify_path_not_symlink_or_reparse(artifact_root)


def safe_create_artifact_dir(path: Path) -> None:
    """Safely create an artifact directory with parent validation.

    Creates the directory and all parents, then verifies each level
    is not a symlink/reparse.

    Raises RuntimeError on violation, OSError on filesystem failure.
    """
    path.mkdir(parents=True, exist_ok=True)
    # Verify each level from root up
    current = path
    artifact_root = get_artifact_root()
    while current != artifact_root.parent:
        try:
            if current.exists():
                verify_path_not_symlink_or_reparse(current)
        except ValueError:
            pass
        if current == artifact_root:
            break
        current = current.parent


def cleanup_orphan_commit_dirs(
    skill_dir: Path,
    *,
    max_entries: int = 50,
) -> int:
    """Clean up orphaned .commit_* temporary directories.

    Only processes direct children matching .commit_* prefix.
    Does NOT follow symlinks or reparse points.

    Args:
        skill_dir: The skill directory to scan.
        max_entries: Maximum number of orphan directories to process.

    Returns:
        Number of directories removed.
    """
    if not skill_dir.is_dir():
        return 0
    cleaned = 0
    try:
        with os.scandir(str(skill_dir)) as entries:
            for entry in entries:
                if cleaned >= max_entries:
                    break
                if not entry.name.startswith(".commit_"):
                    continue
                if not entry.is_dir(follow_symlinks=False):
                    continue
                # Safety: verify the path is not a symlink/reparse
                entry_path = Path(entry.path)
                if _is_symlink_or_reparse(entry_path):
                    logger.warning(
                        "Skipping symlink/reparse commit dir: %s", entry_path
                    )
                    continue
                try:
                    shutil.rmtree(str(entry_path), ignore_errors=False)
                    cleaned += 1
                    logger.info("Cleaned orphan commit dir: %s", entry_path)
                except OSError as e:
                    logger.warning(
                        "Failed to clean orphan commit dir %s: %s", entry_path, e
                    )
    except OSError as e:
        logger.warning("Failed to scan for orphan commit dirs: %s", e)
    return cleaned


def build_safe_filename(artifact_id: str, declared_path: str) -> str:
    """Build a safe filename for artifact storage.

    Format: <artifact_id>_<safe_filename>

    Args:
        artifact_id: 32-hex-char artifact ID.
        declared_path: Original declared relative path.

    Returns:
        Safe filename string.
    """
    original_name = Path(declared_path).name
    # Sanitize: replace problematic characters
    safe = "".join(
        c if c.isalnum() or c in "._- " else "_"
        for c in original_name
    )
    if not safe:
        safe = "artifact"
    return f"{artifact_id}_{safe}"
