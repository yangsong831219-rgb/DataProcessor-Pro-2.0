"""Unified application data directory helpers.

Provides a single source of truth for all application data paths.
Supports Windows, macOS, and Linux.  Works in headless test environments
(no Qt dependency for core path resolution).

Usage:
    from utils.app_paths import get_app_data_root, get_skills_root

    app_data = get_app_data_root()        # %LOCALAPPDATA%/DataProcessorPro (Windows)
    skills_root = get_skills_root()       # <app_data>/skills/
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

_APP_NAME = "DataProcessorPro"


def get_app_data_root() -> Path:
    """Return the platform-appropriate application data root directory.

    Windows:  %LOCALAPPDATA%/DataProcessorPro
              Falls back to %APPDATA%/DataProcessorPro, then ~/AppData/Local/DataProcessorPro
    macOS:    ~/Library/Application Support/DataProcessorPro
    Linux:    $XDG_DATA_HOME/DataProcessorPro, else ~/.local/share/DataProcessorPro

    This directory is NOT inside the source repo, NOT the current working
    directory, and NOT a Python package directory.
    """
    if sys.platform == "win32":
        base = _windows_app_data()
    elif sys.platform == "darwin":
        base = Path.home() / "Library" / "Application Support"
    else:
        # Linux / other Unix
        xdg = os.environ.get("XDG_DATA_HOME")
        if xdg:
            base = Path(xdg)
        else:
            base = Path.home() / ".local" / "share"

    return base / _APP_NAME


def _windows_app_data() -> Path:
    """Resolve Windows app data directory with fallback chain."""
    # Primary: %LOCALAPPDATA%
    local_app_data = os.environ.get("LOCALAPPDATA")
    if local_app_data:
        return Path(local_app_data)

    # Fallback 1: %APPDATA%
    app_data = os.environ.get("APPDATA")
    if app_data:
        return Path(app_data)

    # Fallback 2: construct from home
    home = Path.home()
    return home / "AppData" / "Local"


def get_skills_root() -> Path:
    """Return the skills data root directory under app data.

    <app_data_root>/skills/
    """
    return get_app_data_root() / "skills"


def get_skills_paths(skills_root: Path | None = None) -> "SkillPathsConfig":
    """Build SkillPathsConfig from a skills root directory.

    If skills_root is None, uses the default get_skills_root().

    All paths are normalized absolute paths under skills_root.
    """
    root = Path(skills_root).resolve() if skills_root is not None else get_skills_root().resolve()
    return SkillPathsConfig(
        root=root,
        registry_file=root / "registry.json",
        installed_dir=root / "installed",
        staging_dir=root / "staging",
        downloads_dir=root / "downloads",
        quarantine_dir=root / "quarantine",
        logs_dir=root / "logs",
    )


class SkillPathsConfig:
    """Immutable, validated skill directory paths.

    All fields are absolute, resolved paths under `root`.
    Use get_skills_paths() to create instances.
    """

    __slots__ = (
        "_root", "_registry_file", "_installed_dir", "_staging_dir",
        "_downloads_dir", "_quarantine_dir", "_logs_dir",
    )

    def __init__(
        self,
        *,
        root: Path,
        registry_file: Path,
        installed_dir: Path,
        staging_dir: Path,
        downloads_dir: Path,
        quarantine_dir: Path,
        logs_dir: Path,
    ) -> None:
        # Validate root
        root = root.resolve()
        if _is_empty_or_root(root):
            raise ValueError(f"Skills root must not be empty or filesystem root: {root}")

        # All paths must be absolute
        for name, p in [
            ("root", root),
            ("registry_file", registry_file),
            ("installed_dir", installed_dir),
            ("staging_dir", staging_dir),
            ("downloads_dir", downloads_dir),
            ("quarantine_dir", quarantine_dir),
            ("logs_dir", logs_dir),
        ]:
            if not p.is_absolute():
                raise ValueError(f"{name} must be absolute: {p}")

        # All sub-paths must be under root
        for name, p in [
            ("registry_file", registry_file),
            ("installed_dir", installed_dir),
            ("staging_dir", staging_dir),
            ("downloads_dir", downloads_dir),
            ("quarantine_dir", quarantine_dir),
            ("logs_dir", logs_dir),
        ]:
            try:
                p.resolve().relative_to(root)
            except ValueError:
                raise ValueError(f"{name} ({p}) is not under root ({root})")

        self._root = root
        self._registry_file = registry_file.resolve()
        self._installed_dir = installed_dir.resolve()
        self._staging_dir = staging_dir.resolve()
        self._downloads_dir = downloads_dir.resolve()
        self._quarantine_dir = quarantine_dir.resolve()
        self._logs_dir = logs_dir.resolve()

    # -- read-only properties --

    @property
    def root(self) -> Path:
        return self._root

    @property
    def registry_file(self) -> Path:
        return self._registry_file

    @property
    def installed_dir(self) -> Path:
        return self._installed_dir

    @property
    def staging_dir(self) -> Path:
        return self._staging_dir

    @property
    def downloads_dir(self) -> Path:
        return self._downloads_dir

    @property
    def quarantine_dir(self) -> Path:
        return self._quarantine_dir

    @property
    def logs_dir(self) -> Path:
        return self._logs_dir

    def to_skill_paths(self):
        """Convert to dp_engine.skills.package_models.SkillPaths for backward compat."""
        from dp_engine.skills.package_models import SkillPaths  # noqa: F811
        return SkillPaths(
            root=self._root,
            registry_file=self._registry_file,
            installed_dir=self._installed_dir,
            staging_dir=self._staging_dir,
            downloads_dir=self._downloads_dir,
            quarantine_dir=self._quarantine_dir,
            logs_dir=self._logs_dir,
        )

    def ensure_dirs(self) -> None:
        """Create all skill directories if they don't exist."""
        for d in [
            self._root,
            self._installed_dir,
            self._staging_dir,
            self._downloads_dir,
            self._quarantine_dir,
            self._logs_dir,
        ]:
            d.mkdir(parents=True, exist_ok=True)


def _is_empty_or_root(p: Path) -> bool:
    """Check if path is effectively empty or filesystem root."""
    parts = p.parts
    if not parts:
        return True
    # On Windows: C:\\ has parts like ('C:\\',)
    # On Unix: '/' has parts like ('/',)
    if len(parts) == 1 and (parts[0] == '/' or parts[0].endswith(':\\') or parts[0].endswith(':')):
        return True
    return False
