"""Skill package data models — immutable, pure data.

All models are frozen dataclasses. They carry data only — no QWidget,
QObject, thread, process, or callable references.

Models for:
- Package source identification
- Package file inventory with checksums
- Package validation results
- Install requests and results
- Skill directory paths (SSOT)
- Package resource limits
- Uninstall results
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from dp_engine.skills.models import SkillManifest


# ── Enums ──


class SkillPackageSourceType(Enum):
    """Where the skill package originates."""
    LOCAL_DIRECTORY = "local_directory"
    LOCAL_ZIP = "local_zip"
    GITHUB_ARCHIVE = "github_archive"


class SkillPackageFileType(Enum):
    """Type of a file entry in a skill package."""
    REGULAR = "regular"
    DIRECTORY = "directory"
    SYMLINK = "symlink"


class InstallStage(Enum):
    """Stages of the install process for progress reporting."""
    PREPARING = "preparing"
    DOWNLOADING = "downloading"
    COPYING_SOURCE = "copying_source"
    CHECKING_ARCHIVE = "checking_archive"
    EXTRACTING = "extracting"
    LOCATING_SKILL_ROOT = "locating_skill_root"
    VALIDATING_MANIFEST = "validating_manifest"
    HASHING_FILES = "hashing_files"
    COMMITTING_FILES = "committing_files"
    UPDATING_REGISTRY = "updating_registry"
    COMPLETED = "completed"
    ROLLING_BACK = "rolling_back"
    CANCELLED = "cancelled"
    FAILED = "failed"


# ── Package source ──


@dataclass(frozen=True)
class SkillPackageSource:
    """Identifies where a skill package comes from.

    Attributes:
        source_type: How the package is delivered.
        location: Path or URL to the package.
        revision: Git revision (for GitHub archives) or None.
        expected_checksum: If provided, the installer MUST verify
            the archive SHA-256 against this value before extraction.
        sub_path: Relative path within the archive/root to the skill
            directory (for nested repos where SKILL.md is not at root).
    """

    source_type: SkillPackageSourceType
    location: str
    revision: str | None = None
    expected_checksum: str | None = None
    sub_path: str = ""

    def __post_init__(self) -> None:
        if not self.location.strip():
            raise ValueError("location must not be empty")
        # Normalize sub_path
        sp = self.sub_path.strip().strip("/").replace("\\", "/")
        if sp in (".", ""):
            object.__setattr__(self, "sub_path", "")
        else:
            object.__setattr__(self, "sub_path", sp)


# ── Package limits ──


@dataclass(frozen=True)
class SkillPackageLimits:
    """Resource limits for skill package validation.

    All limits are checked during extraction and validation.
    Defaults are conservative; can be overridden via config.
    """

    max_archive_bytes: int = 200 * 1024 * 1024       # 200 MB
    max_extracted_bytes: int = 500 * 1024 * 1024      # 500 MB
    max_single_file_bytes: int = 100 * 1024 * 1024    # 100 MB
    max_file_count: int = 10_000
    max_directory_depth: int = 20
    max_path_length: int = 240
    max_compression_ratio: float = 100.0


def get_default_limits() -> SkillPackageLimits:
    """Return the default package limits. Override via config if needed."""
    return SkillPackageLimits()


# ── Package file ──


@dataclass(frozen=True)
class SkillPackageFile:
    """A single file in a skill package, with checksum.

    Attributes:
        relative_path: Normalized POSIX-style path relative to skill_root.
        size_bytes: File size in bytes.
        sha256: Hex-encoded SHA-256 digest.
        file_type: REGULAR, DIRECTORY, or SYMLINK.
    """

    relative_path: str
    size_bytes: int
    sha256: str
    file_type: str  # SkillPackageFileType value

    def __post_init__(self) -> None:
        valid_types = {"regular", "directory", "symlink"}
        if self.file_type not in valid_types:
            raise ValueError(
                f"Invalid file_type: '{self.file_type}'. Must be one of {valid_types}"
            )
        if self.file_type != "directory" and self.size_bytes < 0:
            raise ValueError(
                f"size_bytes must be non-negative, got {self.size_bytes}"
            )
        if len(self.sha256) != 64 and self.file_type != "directory":
            raise ValueError(
                f"sha256 must be 64 hex chars, got {len(self.sha256)}: '{self.sha256}'"
            )

    def to_dict(self) -> dict:
        return {
            "relative_path": self.relative_path,
            "size_bytes": self.size_bytes,
            "sha256": self.sha256,
            "file_type": self.file_type,
        }

    @classmethod
    def from_dict(cls, d: dict) -> SkillPackageFile:
        return cls(
            relative_path=str(d["relative_path"]),
            size_bytes=int(d["size_bytes"]),
            sha256=str(d.get("sha256", "")),
            file_type=str(d.get("file_type", "regular")),
        )


# ── Validation result ──


@dataclass(frozen=True)
class SkillPackageValidationResult:
    """Result of validating a skill package.

    Attributes:
        valid: True if the package passes all checks.
        manifest: Parsed SkillManifest (None if validation failed).
        skill_root: Normalized absolute path to the skill root directory
            within staging (None if validation failed).
        files: Sorted tuple of SkillPackageFile entries.
        total_files: Count of all files (regular + directories).
        total_size_bytes: Sum of all file sizes.
        package_checksum: Stable content-based SHA-256 (None if failed).
        archive_sha256: SHA-256 of the archive file (None for directory sources).
        warnings: Non-fatal warnings (e.g., unknown manifest fields).
        errors: Fatal errors that caused validation failure.
    """

    valid: bool
    manifest: object | None = None  # SkillManifest | None
    skill_root: str | None = None
    files: tuple[object, ...] = ()  # tuple[SkillPackageFile, ...]
    total_files: int = 0
    total_size_bytes: int = 0
    package_checksum: str | None = None
    archive_sha256: str | None = None
    warnings: tuple[str, ...] = ()
    errors: tuple[str, ...] = ()

    @property
    def manifest_obj(self) -> object | None:  # SkillManifest | None
        """Typed accessor for the manifest."""
        return self.manifest


# ── Install request ──


@dataclass(frozen=True)
class SkillInstallRequest:
    """A request to install a skill package.

    Attributes:
        source: Where the package comes from.
        activate_after_install: If True, this version becomes the active
            version after successful install.
        replace_same_version: If True, overwrite an existing same-version
            install. If False (default), same-version conflict is an error.
    """

    source: SkillPackageSource
    activate_after_install: bool = False
    replace_same_version: bool = False


# ── Install result ──


@dataclass(frozen=True)
class SkillInstallResult:
    """Result of installing a skill package.

    Never use a bare bool to express install outcome.
    Check `success` first, then inspect `message` and `warnings`.
    """

    success: bool
    skill_id: str | None = None
    version: str | None = None
    install_path: str | None = None
    package_checksum: str | None = None
    warnings: tuple[str, ...] = ()
    message: str = ""


# ── Uninstall result ──


@dataclass(frozen=True)
class SkillUninstallResult:
    """Result of uninstalling a skill version.

    Attributes:
        success: True if the uninstall completed.
        skill_id: The skill that was uninstalled.
        version: The version that was uninstalled.
        was_active: True if the uninstalled version was the active one.
        message: Human-readable result message.
    """

    success: bool
    skill_id: str | None = None
    version: str | None = None
    was_active: bool = False
    message: str = ""


# ── SkillPaths — SSOT for all skill-related filesystem paths ──


@dataclass(frozen=True)
class SkillPaths:
    """Single Source of Truth for all skill filesystem paths.

    All paths derive from a single root. No UI, installer, or registry
    code should construct these paths independently.
    """

    root: Path
    registry_file: Path
    installed_dir: Path
    staging_dir: Path
    downloads_dir: Path
    quarantine_dir: Path
    logs_dir: Path


def get_default_skill_paths(base_dir: Path | None = None) -> SkillPaths:
    """Create SkillPaths from a base directory.

    If base_dir is None, uses the platform-appropriate user application
    data directory (e.g., %LOCALAPPDATA%/DataProcessorPro/skills on Windows).

    The default path is NEVER inside the source repository, the current
    working directory, or a Python package directory.

    Args:
        base_dir: Optional explicit base directory. If None, uses
                  utils.app_paths.get_skills_root().

    Returns:
        SkillPaths with all subdirectories resolved.
    """
    if base_dir is None:
        from utils.app_paths import get_skills_paths as _get_paths
        cfg = _get_paths()
        return SkillPaths(
            root=cfg.root,
            registry_file=cfg.registry_file,
            installed_dir=cfg.installed_dir,
            staging_dir=cfg.staging_dir,
            downloads_dir=cfg.downloads_dir,
            quarantine_dir=cfg.quarantine_dir,
            logs_dir=cfg.logs_dir,
        )
    else:
        root = Path(base_dir).resolve()

    return SkillPaths(
        root=root,
        registry_file=root / "registry.json",
        installed_dir=root / "installed",
        staging_dir=root / "staging",
        downloads_dir=root / "downloads",
        quarantine_dir=root / "quarantine",
        logs_dir=root / "logs",
    )


def ensure_skill_dirs(paths: SkillPaths) -> None:
    """Create all skill directories if they don't exist.

    Safe to call multiple times — existing directories are not modified.
    """
    for d in [
        paths.root,
        paths.installed_dir,
        paths.staging_dir,
        paths.downloads_dir,
        paths.quarantine_dir,
        paths.logs_dir,
    ]:
        d.mkdir(parents=True, exist_ok=True)
