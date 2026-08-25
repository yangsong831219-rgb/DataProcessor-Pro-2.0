"""Atomic skill installer with staging, rollback, and uninstall.

SkillInstaller provides:
- Atomic install with staging directory
- Same-version replacement with backup/restore
- Uninstall via quarantine-then-delete
- Single-process install lock (threading.RLock)
- Registry coordination (installer talks to SkillRegistry)

Thread-safety: protected by threading.RLock.  Cross-process locking
is not implemented — concurrent installs from separate processes may
race on the registry file.
"""

from __future__ import annotations

import hashlib
import logging
import os
import shutil
import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from dp_engine.skills.archive_utils import (
    locate_skill_root,
    safe_copy_directory,
    safe_extract_zip,
)
from dp_engine.skills.errors import (
    SkillInstallCancelled,
    SkillInstallConflictError,
    SkillInstallError,
    SkillRollbackError,
    SkillUninstallError,
)
from dp_engine.skills.models import InstalledSkill
from dp_engine.skills.package_models import (
    SkillInstallRequest,
    SkillInstallResult,
    SkillPackageLimits,
    SkillPackageSource,
    SkillPackageSourceType,
    SkillPaths,
    SkillUninstallResult,
    get_default_limits,
)
from dp_engine.skills.package_validator import SkillPackageValidator
from dp_engine.skills.registry import SkillRegistry

logger = logging.getLogger(__name__)


def _now_iso() -> str:
    """Return current UTC time as ISO 8601 string."""
    return datetime.now(timezone.utc).isoformat()


def _task_id() -> str:
    """Generate a unique task ID for this install operation."""
    return uuid.uuid4().hex[:12]


class SkillInstaller:
    """Atomic skill package installer.

    Manages the full install lifecycle:
    source → download/copy → staging → validate → commit → registry.

    All destructive operations are protected by a threading.RLock.
    """

    def __init__(
        self,
        paths: SkillPaths,
        registry: SkillRegistry,
        limits: SkillPackageLimits | None = None,
    ) -> None:
        self._paths = paths
        self._registry = registry
        self._limits = limits or get_default_limits()
        self._validator = SkillPackageValidator(limits=self._limits)
        self._lock = threading.RLock()

    # ── Public API ──

    @property
    def paths(self) -> SkillPaths:
        return self._paths

    @property
    def lock(self) -> threading.RLock:
        return self._lock

    def install(
        self,
        request: SkillInstallRequest,
        cancel_check: Callable[[], bool] | None = None,
        stage_callback: Callable[[str], None] | None = None,
    ) -> SkillInstallResult:
        """Install a skill package.

        Full pipeline:
        1. Acquire install lock
        2. Check for existing same-version
        3. Create staging directory
        4. Prepare package to staging (copy dir or extract zip)
        5. Locate skill root
        6. Validate package
        7. Commit to installed directory
        8. Update registry
        9. Clean up staging

        Args:
            request: Install request with source and options.
            cancel_check: Optional callable returning True if cancelled.
            stage_callback: Optional callback receiving stage name strings.

        Returns:
            SkillInstallResult with success/failure details.
        """
        task = _task_id()

        try:
            with self._lock:
                return self._install_locked(
                    request, task, cancel_check, stage_callback
                )
        except SkillInstallCancelled:
            if stage_callback:
                stage_callback("cancelled")
            return SkillInstallResult(
                success=False,
                message="Install cancelled by user",
            )
        except Exception as e:
            logger.exception("Install failed for task %s: %s", task, e)
            if stage_callback:
                stage_callback("failed")
            return SkillInstallResult(
                success=False,
                message=f"Install failed: {e}",
            )

    def uninstall(
        self,
        skill_id: str,
        version: str,
        cancel_check: Callable[[], bool] | None = None,
    ) -> SkillUninstallResult:
        """Uninstall a specific skill version.

        Process:
        1. Verify target exists in registry
        2. Verify install_path is within managed directory
        3. Move install directory to quarantine
        4. Update registry
        5. Delete quarantine

        Args:
            skill_id: Skill to uninstall.
            version: Version to uninstall.
            cancel_check: Optional cancel callback.

        Returns:
            SkillUninstallResult with success/failure details.
        """
        with self._lock:
            return self._uninstall_locked(skill_id, version, cancel_check)

    # ── Install internals ──

    def _install_locked(
        self,
        request: SkillInstallRequest,
        task: str,
        cancel_check: Callable[[], bool] | None,
        stage_callback: Callable[[str], None] | None,
    ) -> SkillInstallResult:
        """Install under the lock. See install() for pipeline steps."""
        source = request.source
        warnings: list[str] = []

        # Stage callback helper
        def _stage(s: str) -> None:
            if stage_callback:
                stage_callback(s)

        _stage("preparing")

        # Ensure directories exist
        self._ensure_dirs()

        # Step 2: Check existing version
        existing = self._registry.get(
            self._registry._skills.get(("", ""))
        ) if False else None  # No-op, see below

        # Check if this (skill_id, version) is already registered
        # We don't know skill_id yet (need to parse manifest first),
        # so this check happens after validation.
        # But we know the source — if it's a local path, we can
        # do some early checks.

        if cancel_check and cancel_check():
            raise SkillInstallCancelled("Install cancelled before staging")

        # Step 3: Create unique staging directory
        staging_root = self._paths.staging_dir / task
        if staging_root.exists():
            shutil.rmtree(str(staging_root), ignore_errors=True)
        staging_root.mkdir(parents=True, exist_ok=True)

        archive_sha256: str | None = None

        try:
            # Step 4: Prepare package to staging
            if source.source_type == SkillPackageSourceType.LOCAL_DIRECTORY:
                _stage("copying_source")
                source_path = Path(source.location).resolve()
                safe_copy_directory(
                    source_path,
                    staging_root / "source",
                    limits=self._limits,
                    cancel_check=cancel_check,
                )
                extracted_dir = staging_root / "source"

            elif source.source_type == SkillPackageSourceType.LOCAL_ZIP:
                _stage("checking_archive")
                archive_path = Path(source.location).resolve()
                if not archive_path.is_file():
                    raise SkillInstallError(
                        f"ZIP file not found: {archive_path}"
                    )

                _stage("extracting")
                extract_dir = staging_root / "extracted"
                extract_dir.mkdir()
                archive_sha256 = safe_extract_zip(
                    archive_path,
                    extract_dir,
                    limits=self._limits,
                    cancel_check=cancel_check,
                )
                extracted_dir = extract_dir

            elif source.source_type == SkillPackageSourceType.GITHUB_ARCHIVE:
                _stage("checking_archive")
                archive_path = self._paths.downloads_dir / f"{task}.zip"
                if not archive_path.is_file():
                    raise SkillInstallError(
                        f"Downloaded archive not found: {archive_path}"
                    )

                _stage("extracting")
                extract_dir = staging_root / "extracted"
                extract_dir.mkdir()
                archive_sha256 = safe_extract_zip(
                    archive_path,
                    extract_dir,
                    limits=self._limits,
                    cancel_check=cancel_check,
                )
                extracted_dir = extract_dir

            else:
                raise SkillInstallError(
                    f"Unsupported source type: {source.source_type.value}"
                )

            if cancel_check and cancel_check():
                raise SkillInstallCancelled("Install cancelled after extraction")

            # Step 5: Locate skill root
            _stage("locating_skill_root")
            skill_root = locate_skill_root(extracted_dir, source.sub_path)

            # Step 6: Validate package
            _stage("validating_manifest")
            validation = self._validator.validate(
                skill_root,
                archive_sha256=archive_sha256,
                cancel_check=cancel_check,
            )

            if not validation.valid:
                return SkillInstallResult(
                    success=False,
                    message=f"Package validation failed: {'; '.join(validation.errors)}",
                    warnings=validation.warnings,
                )

            if cancel_check and cancel_check():
                raise SkillInstallCancelled("Install cancelled after validation")

            # Now we know skill_id and version
            manifest = validation.manifest
            if manifest is None:
                return SkillInstallResult(
                    success=False,
                    message="Package validation returned no manifest",
                )
            # manifest is SkillManifest
            skill_id = manifest.skill_id  # type: ignore[union-attr]
            version = manifest.version  # type: ignore[union-attr]

            # Check for existing same-version
            existing = self._registry.get(skill_id, version)
            if existing is not None:
                if not request.replace_same_version:
                    raise SkillInstallConflictError(
                        f"Skill '{skill_id}@{version}' is already installed. "
                        f"Use replace_same_version=True to overwrite.",
                        detail={
                            "skill_id": skill_id,
                            "version": version,
                            "existing_path": existing.install_path,
                        },
                    )

            # Step 7: Compute package checksum
            _stage("hashing_files")
            package_checksum = validation.package_checksum
            warnings.extend(validation.warnings)

            # Step 8: Commit to installed directory
            _stage("committing_files")
            install_dir = self._paths.installed_dir / skill_id
            version_dir = install_dir / version

            # If replacing, back up existing
            backup_dir: Path | None = None
            if existing is not None:
                backup_dir = (
                    self._paths.staging_dir / f"backup-{task}-{skill_id}-{version}"
                )
                if version_dir.exists():
                    _atomic_rename(version_dir, backup_dir)

            try:
                # Create pending directory
                pending_dir = install_dir / f".pending-{task}"
                if pending_dir.exists():
                    shutil.rmtree(str(pending_dir), ignore_errors=True)
                install_dir.mkdir(parents=True, exist_ok=True)

                # Copy validated skill to pending
                _copy_tree(str(skill_root), str(pending_dir))

                # fsync parent directory
                _fsync_dir(str(install_dir))

                # Atomic rename pending → version
                if version_dir.exists():
                    shutil.rmtree(str(version_dir), ignore_errors=True)
                _atomic_rename(pending_dir, version_dir)

            except Exception:
                # Restore backup if replace failed
                if backup_dir is not None and backup_dir.exists():
                    if version_dir.exists():
                        shutil.rmtree(str(version_dir), ignore_errors=True)
                    _atomic_rename(backup_dir, version_dir)
                raise

            # Build InstalledSkill
            installed = InstalledSkill(
                manifest=manifest,  # type: ignore[arg-type]
                install_path=str(version_dir),
                enabled=False,  # Will be set by registry
                installed_at=_now_iso(),
                source_revision=source.revision,
                checksum=package_checksum,
                health_status="unavailable",
                health_message="SkillRuntime not implemented",
            )

            # Step 9: Update registry (with snapshot for rollback)
            _stage("updating_registry")

            # Snapshot registry state BEFORE mutation
            reg_snapshot = self._registry.snapshot()

            if existing is not None:
                # Replace existing record
                self._registry.replace(installed)
            else:
                # New registration
                self._registry.register(installed)

            # Handle activate_after_install
            if request.activate_after_install:
                self._registry.set_active_version(skill_id, version)

            # Persist registry — rollback on failure
            try:
                self._registry.save()
            except Exception as save_err:
                # Restore registry memory snapshot
                self._registry.restore(reg_snapshot)
                # Rollback filesystem
                _stage("rolling_back")
                _rollback_install(version_dir, backup_dir)
                raise SkillRollbackError(
                    f"Registry save failed — memory and filesystem rolled back",
                    detail={
                        "skill_id": skill_id,
                        "version": version,
                        "install_dir": str(version_dir),
                        "save_error": str(save_err),
                    },
                ) from save_err

            # Step 10: Clean up
            # Delete backup if replacement succeeded
            if backup_dir is not None and backup_dir.exists():
                shutil.rmtree(str(backup_dir), ignore_errors=True)

            _stage("completed")

            return SkillInstallResult(
                success=True,
                skill_id=skill_id,
                version=version,
                install_path=str(version_dir),
                package_checksum=package_checksum,
                warnings=tuple(warnings),
                message=(
                    f"Skill '{skill_id}@{version}' installed successfully. "
                    f"技能包已安装并注册，但当前尚不能执行。"
                ),
            )

        finally:
            # Always clean up staging for this task
            if staging_root.exists():
                shutil.rmtree(str(staging_root), ignore_errors=True)

    def _uninstall_locked(
        self,
        skill_id: str,
        version: str,
        cancel_check: Callable[[], bool] | None,
    ) -> SkillUninstallResult:
        """Uninstall under the lock."""
        # Verify target exists
        installed = self._registry.get(skill_id, version)
        if installed is None:
            return SkillUninstallResult(
                success=False,
                skill_id=skill_id,
                version=version,
                message=f"Skill '{skill_id}@{version}' is not installed",
            )

        install_path = Path(installed.install_path)

        # Verify install_path is within managed directory
        try:
            install_path.resolve().relative_to(
                self._paths.installed_dir.resolve()
            )
        except ValueError:
            raise SkillUninstallError(
                f"Install path is outside managed directory: {install_path}. "
                f"Refusing to delete for safety.",
                detail={
                    "install_path": str(install_path),
                    "managed_dir": str(self._paths.installed_dir),
                },
            )

        if cancel_check and cancel_check():
            raise SkillInstallCancelled("Uninstall cancelled by user")

        # Check if this was the active version
        active = self._registry.get_active(skill_id)
        was_active = (
            active is not None
            and active.skill_id == skill_id
            and active.version == version
        )

        # Move to quarantine
        quarantine_dir = (
            self._paths.quarantine_dir / f"{skill_id}-{version}-{_task_id()}"
        )
        self._paths.quarantine_dir.mkdir(parents=True, exist_ok=True)

        quarantine_success = False
        if install_path.exists():
            try:
                _atomic_rename(install_path, quarantine_dir)
                quarantine_success = True
            except OSError as e:
                return SkillUninstallResult(
                    success=False,
                    skill_id=skill_id,
                    version=version,
                    was_active=was_active,
                    message=f"Failed to move install to quarantine: {e}",
                )

        # Snapshot registry before mutation
        reg_snapshot = self._registry.snapshot()

        # Update registry
        self._registry.unregister(skill_id, version)

        try:
            self._registry.save()
        except Exception as e:
            # Registry save failed — restore memory + filesystem
            self._registry.restore(reg_snapshot)
            if quarantine_success and quarantine_dir.exists():
                _atomic_rename(quarantine_dir, install_path)
            raise SkillUninstallError(
                f"Registry save failed during uninstall — memory and filesystem restored",
                detail={"error": str(e)},
            ) from e

        # Delete quarantine
        if quarantine_success and quarantine_dir.exists():
            shutil.rmtree(str(quarantine_dir), ignore_errors=True)

        return SkillUninstallResult(
            success=True,
            skill_id=skill_id,
            version=version,
            was_active=was_active,
            message=f"Skill '{skill_id}@{version}' uninstalled successfully",
        )

    def _ensure_dirs(self) -> None:
        """Create all required directories."""
        self._paths.root.mkdir(parents=True, exist_ok=True)
        self._paths.installed_dir.mkdir(parents=True, exist_ok=True)
        self._paths.staging_dir.mkdir(parents=True, exist_ok=True)
        self._paths.downloads_dir.mkdir(parents=True, exist_ok=True)
        self._paths.quarantine_dir.mkdir(parents=True, exist_ok=True)
        self._paths.logs_dir.mkdir(parents=True, exist_ok=True)


# ── Filesystem helpers ──


def _atomic_rename(src: Path, dst: Path) -> None:
    """Atomically rename src to dst using os.replace().

    On Windows, os.replace() fails with PermissionError if dst is an
    existing directory. We handle this by removing dst first.
    On Windows, file-system operations may be blocked momentarily by
    antivirus or other processes; we retry with backoff.

    Only for use within the managed skills directories.
    NEVER use on user source files.
    """
    import sys
    import time

    if dst.exists():
        # os.replace on Windows cannot replace a directory — remove first
        shutil.rmtree(str(dst), ignore_errors=True)

    # Retry on Windows for transient file locking (antivirus, etc.)
    max_retries = 3 if sys.platform == "win32" else 1
    last_error = None
    for attempt in range(max_retries):
        try:
            os.replace(str(src), str(dst))
            return
        except OSError as e:
            last_error = e
            if attempt < max_retries - 1:
                time.sleep(0.1 * (attempt + 1))  # 100ms, 200ms backoff
                continue
    raise last_error  # type: ignore[misc]


def _copy_tree(src: str, dst: str) -> None:
    """Copy a directory tree. Thin wrapper over shutil.copytree."""
    shutil.copytree(src, dst, symlinks=False, dirs_exist_ok=True)


def _rollback_install(version_dir: Path, backup_dir: Path | None) -> None:
    """Rollback: delete new install, restore backup."""
    if version_dir.exists():
        shutil.rmtree(str(version_dir), ignore_errors=True)
    if backup_dir is not None and backup_dir.exists():
        try:
            _atomic_rename(backup_dir, version_dir)
        except OSError:
            logger.exception(
                "Rollback: failed to restore backup %s → %s",
                backup_dir, version_dir,
            )


def _fsync_dir(path_str: str) -> None:
    """fsync a directory to ensure metadata is on disk."""
    try:
        fd = os.open(path_str, os.O_RDONLY)
        os.fsync(fd)
        os.close(fd)
    except OSError:
        pass  # Best-effort
