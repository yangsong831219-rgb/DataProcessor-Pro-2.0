"""Runtime permission enforcement via Python audit hooks.

IMPORTANT: This is process isolation + audit hooks, NOT a complete OS sandbox.
Python audit hooks are a defense-in-depth measure; they do NOT provide an
absolute security boundary against malicious native extensions.

The audit hook is installed in the WORKER subprocess ONLY, before any skill
code is imported.  The parent process never installs these hooks.

Capability model:
- read_skill_files:     read from the installed skill directory
- read_runtime_workspace: read from the runtime workspace
- write_runtime_workspace: write to workspace/output and workspace/temp

Denied by default:
- network, subprocess, shell, project_read, project_write,
  installed_write, environment_secrets, ctypes, dynamic_native_library

KNOWN BOUNDARY (honest disclosure):
This mechanism is NOT a complete OS sandbox.
File path, network, subprocess and some native-loading restrictions are
best-effort defenses.  Native extensions MAY bypass Python-layer auditing,
so high-risk or unapproved native skills MUST NOT be executed.
"""

from __future__ import annotations

import logging
import os
import sys
import sysconfig
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

logger = logging.getLogger(__name__)

# Environment variables REQUIRED on Windows
_WINDOWS_REQUIRED_ENV = frozenset({
    "SYSTEMROOT", "WINDIR", "TEMP", "TMP",
    "USERPROFILE", "COMPUTERNAME", "HOMEDRIVE", "HOMEPATH",
    "PATH", "PATHEXT", "COMSPEC",
})

# Environment variables ALWAYS allowed (safe)
_ALWAYS_ALLOWED_ENV = frozenset({
    "PYTHONUTF8",
    "PYTHONDONTWRITEBYTECODE",
    "PYTHONUNBUFFERED",
    "PYTHONIOENCODING",
    "LANG",
    "LC_ALL",
    "LC_CTYPE",
    "TZ",
})

# Environment variable name patterns to REJECT
_FORBIDDEN_ENV_PATTERNS = (
    "TOKEN", "SECRET", "PASSWORD", "API_KEY", "KEY",
)


# ── File access policy ──


@dataclass(frozen=True)
class RuntimeFilePolicy:
    """Immutable file access policy for the Worker sandbox."""

    allowed_read_roots: tuple[Path, ...]
    allowed_write_roots: tuple[Path, ...]

    @classmethod
    def build(
        cls,
        *,
        skill_dir: Path,
        workspace_path: Path,
        approved_dependency_paths: tuple[Path, ...] = (),
    ) -> RuntimeFilePolicy:
        """Build a file policy from skill and workspace paths.

        Read roots:
        - The installed skill directory (read-only)
        - The runtime workspace (read access)
        - Python standard library paths (via sysconfig — NOT all of sys.path)
        - Verified dependency paths (explicitly passed)

        Write roots:
        - workspace/output/
        - workspace/temp/

        IMPORTANT: sys.path is NOT used as an implicit allowlist.
        Only stdlib (stdlib + platstdlib from sysconfig) and explicitly
        approved dependency paths are added.  Project root, cwd, and
        unverified site-packages are NOT implicitly allowed.
        """
        read_roots: list[Path] = [
            skill_dir.resolve(),
            workspace_path.resolve(),
        ]

        # Add Python stdlib paths only (NOT all of sys.path)
        for key in ("stdlib", "platstdlib"):
            try:
                std_path = Path(sysconfig.get_paths().get(key, ""))
                if std_path.is_dir():
                    read_roots.append(std_path.resolve())
            except (KeyError, OSError):
                pass

        # Add explicitly approved dependency paths
        for dep_path in approved_dependency_paths:
            dp = Path(dep_path)
            if dp.is_dir():
                try:
                    read_roots.append(dp.resolve())
                except OSError:
                    pass

        wp = workspace_path.resolve()
        write_roots: list[Path] = [
            wp / "output",
            wp / "temp",
        ]

        return cls(
            allowed_read_roots=tuple(read_roots),
            allowed_write_roots=tuple(write_roots),
        )


def check_runtime_file_access(
    path: str | os.PathLike[str],
    *,
    mode: str,
    policy: RuntimeFilePolicy,
    workspace_path: Path | None = None,
) -> None:
    """Check whether a file access is permitted under the runtime policy.

    Rules:
    1. Resolve the path first (handles symlinks, .., relative)
    2. Relative paths are resolved against workspace_path (or cwd if None)
    3. Read modes check allowed_read_roots
    4. Write/append/update modes check allowed_write_roots
    5. Raises PermissionError with structured message on denial

    This is a PURE function — no side effects, no sys.addaudithook.
    It can be tested without subprocess or audit infrastructure.

    Args:
        path: The file path to check (may be relative or absolute).
        mode: The open mode string (e.g. 'r', 'w', 'rb', 'a+').
        policy: The RuntimeFilePolicy with allowed roots.
        workspace_path: If provided, relative paths are resolved against this.
                        If None, relative paths use os.getcwd().

    Raises:
        PermissionError: If the access is denied.
    """
    path_str = os.fsdecode(path) if isinstance(path, (bytes, os.PathLike)) else str(path)

    # Determine if this is a write operation
    mode_lower = mode.lower()
    is_write = any(c in mode_lower for c in ("w", "a", "+", "x"))

    # Resolve the path
    try:
        p = Path(path_str)
        if not p.is_absolute():
            if workspace_path is not None:
                p = (workspace_path / p).resolve()
            else:
                p = p.resolve()
        else:
            p = p.resolve()
    except OSError:
        # If we can't resolve, let the OS handle it — it will likely fail too
        return

    # Windows: normalize case for comparison
    resolved_str = str(p)
    if sys.platform == "win32":
        resolved_str = resolved_str.lower()

    if is_write:
        for root in policy.allowed_write_roots:
            root_str = str(root)
            if sys.platform == "win32":
                root_str = root_str.lower()
            if resolved_str == root_str or resolved_str.startswith(root_str + os.sep):
                return
        # Denied — build structured message
        raise PermissionError(
            f"permission_denied: write to '{path_str}' "
            f"(resolved: '{p}') is outside allowed write roots. "
            f"Allowed: {[str(r) for r in policy.allowed_write_roots]}"
        )
    else:
        # Read operation — check against allowed_read_roots
        for root in policy.allowed_read_roots:
            root_str = str(root)
            if sys.platform == "win32":
                root_str = root_str.lower()
            if resolved_str == root_str or resolved_str.startswith(root_str + os.sep):
                return
        # Denied — raise structured error
        raise PermissionError(
            f"permission_denied: read from '{path_str}' "
            f"(resolved: '{p}') is outside allowed read roots."
        )


# ── Legacy helpers (kept for backward compatibility) ──


def is_path_within_roots(path: Path, roots: tuple[Path, ...]) -> bool:
    """Check if a resolved path is within any of the allowed root directories.

    Args:
        path: The resolved (absolute, normalized) path to check.
        roots: Tuple of resolved root directories.

    Returns:
        True if path is equal to or under any root.
    """
    for root in roots:
        try:
            path.relative_to(root)
            return True
        except ValueError:
            continue
    return False


def build_allowed_read_roots(
    skill_dir: Path,
    workspace_path: Path,
    *,
    approved_dependency_paths: tuple[Path, ...] = (),
) -> tuple[Path, ...]:
    """Build the set of directories where file reads are allowed.

    Includes:
    - The installed skill directory (read-only access)
    - The runtime workspace (read access)
    - Python standard library paths (from sysconfig — NOT all of sys.path)
    - Verified dependency paths (explicitly passed, via approval gate)

    IMPORTANT: sys.path is NOT used as an implicit allowlist.  Only stdlib
    paths from sysconfig and explicitly approved dependency paths are added.
    Project root, cwd, unverified site-packages, and user home are NOT
    implicitly allowed.
    """
    roots: list[Path] = [
        skill_dir.resolve(),
        workspace_path.resolve(),
    ]

    # Add Python stdlib paths only (NOT all of sys.path)
    for key in ("stdlib", "platstdlib"):
        try:
            std_path = Path(sysconfig.get_paths().get(key, ""))
            if std_path.is_dir():
                roots.append(std_path.resolve())
        except (KeyError, OSError):
            pass

    # Add explicitly approved dependency paths
    for dep_path in approved_dependency_paths:
        dp = Path(dep_path)
        if dp.is_dir():
            try:
                roots.append(dp.resolve())
            except OSError:
                pass

    return tuple(roots)


def build_allowed_write_roots(
    workspace_path: Path,
) -> tuple[Path, ...]:
    """Build the set of directories where file writes are allowed.

    Only allows:
    - workspace/output/
    - workspace/temp/
    """
    wp = workspace_path.resolve()
    return (
        (wp / "output"),
        (wp / "temp"),
    )


def build_sanitized_env(
    *,
    extra_allowed: tuple[str, ...] = (),
) -> dict[str, str]:
    """Build a minimal sanitized environment for the worker subprocess.

    Rules:
    1. Always allow Windows-required vars (SYSTEMROOT, WINDIR, etc.)
    2. Always allow safe vars (PYTHONUTF8, etc.)
    3. Extra allowed vars provided by caller
    4. Explicitly delete vars matching TOKEN/SECRET/PASSWORD/API_KEY/KEY
    5. Delete PYTHONPATH, PYTHONHOME
    6. Delete HTTP_PROXY, HTTPS_PROXY, NO_PROXY
    7. Delete common API key env vars
    """
    # Start empty
    sanitized: dict[str, str] = {}

    # Build allowlist
    allowed = set(_ALWAYS_ALLOWED_ENV) | set(extra_allowed)
    if sys.platform == "win32":
        allowed |= _WINDOWS_REQUIRED_ENV

    # Explicitly denied keys
    denied_exact = {
        "PYTHONPATH", "PYTHONHOME",
        "HTTP_PROXY", "HTTPS_PROXY", "NO_PROXY",
        "OPENAI_API_KEY", "ANTHROPIC_API_KEY", "DEEPSEEK_API_KEY",
        "GITHUB_TOKEN", "AZURE_API_KEY",
        "AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY",
    }

    for key, value in os.environ.items():
        # Check exact deny list
        if key.upper() in denied_exact:
            continue

        # Check forbidden patterns
        upper = key.upper()
        if any(pattern in upper for pattern in _FORBIDDEN_ENV_PATTERNS):
            continue

        # Check allowlist
        if key.upper() in allowed or key in allowed:
            sanitized[key] = value

    # Always set safe Python flags
    sanitized["PYTHONUTF8"] = "1"
    sanitized["PYTHONDONTWRITEBYTECODE"] = "1"

    return sanitized


# ── Audit hook ──


def install_audit_hooks(
    *,
    allowed_read_roots: tuple[Path, ...],
    allowed_write_roots: tuple[Path, ...],
    capabilities: tuple[str, ...],
) -> None:
    """Install sys.addaudithook to enforce capability restrictions.

    Must be called in the WORKER subprocess BEFORE importing any skill module.

    This is NOT a complete security sandbox.  It provides defense-in-depth:
    - Blocks subprocess/shell creation (unless capability 'subprocess' granted)
    - Blocks socket creation/connect (unless capability 'network' granted)
    - Blocks ctypes native library loading
    - Blocks file writes outside allowed_write_roots
    - Blocks file reads outside allowed_read_roots

    Args:
        allowed_read_roots: Resolved paths where reads are permitted.
        allowed_write_roots: Resolved paths where writes are permitted.
        capabilities: Set of granted capability names.
    """
    blocked_events: set[str] = set()
    _blocked_event_count: dict[str, int] = {}

    # Build policy for pure-function checks
    policy = RuntimeFilePolicy(
        allowed_read_roots=allowed_read_roots,
        allowed_write_roots=allowed_write_roots,
    )

    # Normalize roots for Windows comparison
    _norm_read_roots: list[str] = []
    _norm_write_roots: list[str] = []
    for r in allowed_read_roots:
        s = str(r)
        if sys.platform == "win32":
            s = s.lower()
        _norm_read_roots.append(s)
    for r in allowed_write_roots:
        s = str(r)
        if sys.platform == "win32":
            s = s.lower()
        _norm_write_roots.append(s)

    def _path_in_roots(resolved: Path, norm_roots: list[str]) -> bool:
        """Check if resolved path is within any normalized root."""
        resolved_str = str(resolved)
        if sys.platform == "win32":
            resolved_str = resolved_str.lower()
        for root_str in norm_roots:
            if resolved_str == root_str or resolved_str.startswith(root_str + os.sep):
                return True
        return False

    def _audit_handler(event: str, args: tuple) -> None:
        # ── subprocess / shell blocking ──
        if event == "subprocess.Popen":
            if "subprocess" not in capabilities and "shell" not in capabilities:
                blocked_events.add(event)
                _blocked_event_count[event] = _blocked_event_count.get(event, 0) + 1
                raise RuntimeError(
                    "subprocess.Popen is blocked by skill runtime audit hook. "
                    "Skill does not have 'subprocess' or 'shell' capability."
                )
            return

        if event == "os.system":
            if "shell" not in capabilities:
                blocked_events.add(event)
                _blocked_event_count[event] = _blocked_event_count.get(event, 0) + 1
                raise RuntimeError(
                    "os.system is blocked by skill runtime audit hook."
                )

        # ── Network blocking ──
        if event in ("socket.__new__", "socket.bind", "socket.connect"):
            if "network" not in capabilities:
                blocked_events.add(event)
                _blocked_event_count[event] = _blocked_event_count.get(event, 0) + 1
                raise RuntimeError(
                    f"{event} is blocked by skill runtime audit hook. "
                    f"Skill does not have 'network' capability."
                )

        # ── ctypes / native library blocking ──
        if event == "ctypes.dlopen":
            if "ctypes" not in capabilities and "dynamic_native_library" not in capabilities:
                blocked_events.add(event)
                _blocked_event_count[event] = _blocked_event_count.get(event, 0) + 1
                raise RuntimeError(
                    "ctypes.dlopen is blocked by skill runtime audit hook."
                )

        # ── File access restriction (read AND write) ──
        if event == "open" and args:
            mode = args[1] if len(args) > 1 else "r"
            path_str = args[0] if len(args) > 0 else ""

            if not path_str or not isinstance(path_str, (str, bytes)):
                return

            mode_str = str(mode) if isinstance(mode, str) else "r"
            is_write = any(c in mode_str for c in ("w", "a", "+", "x"))

            try:
                p = Path(str(path_str))
                if not p.is_absolute():
                    # Resolve relative paths against cwd (worker cwd = workspace)
                    p = p.resolve()
                else:
                    p = p.resolve()
            except OSError:
                return  # Let the OS handle it

            if is_write:
                # ── Write check ──
                if not _path_in_roots(p, _norm_write_roots):
                    blocked_events.add("file_write")
                    _blocked_event_count["file_write"] = (
                        _blocked_event_count.get("file_write", 0) + 1
                    )
                    raise PermissionError(
                        f"permission_denied: write to '{path_str}' "
                        f"(resolved: '{p}') is outside allowed write roots."
                    )
            else:
                # ── Read check (NEW in Batch 3.0.3) ──
                if not _path_in_roots(p, _norm_read_roots):
                    blocked_events.add("file_read")
                    _blocked_event_count["file_read"] = (
                        _blocked_event_count.get("file_read", 0) + 1
                    )
                    raise PermissionError(
                        f"permission_denied: read from '{path_str}' "
                        f"(resolved: '{p}') is outside allowed read roots."
                    )

    # Install the hook
    sys.addaudithook(_audit_handler)
    logger.info(
        "Audit hook installed. Capabilities: %s",
        list(capabilities),
    )
