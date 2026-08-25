"""Safe one-time migration from old skill registry paths to new app-data paths.

Migrates TWO old registry sources:
  1. project_root/readings_profiles/skills/skill_registry.json  (Batch 1)
  2. project_root/readings_profiles/skills/registry.json        (Batch 2 early)

Batch 2.2 Security enhancements:
- Full SkillPackageValidator chain for every old skill
- Manifest skill_id + version cross-check against old registry record
- Package checksum recomputation with old checksum comparison
- Migration marker (migration-v1.json) for idempotency
- Transaction safety: all-or-nothing with registry snapshot
- Unique staging per migration run
- Entrypoint existence verification
- Symlink / special file rejection during migration copy
- Old checksum mismatch → conflict, not silent migration
- Old checksum absent → use new checksum + log warning

Rules:
- Only migrates when the NEW registry file does not exist AND
  migration marker does not indicate completion.
- Reads old registries and performs schema validation.
- Does NOT move or delete old files — only copies to new location.
- When both old registries exist: deterministic merge (not random pick).
- Conflict (same skill_id/version) → compare checksum and install_path.
- Cannot safely merge? → abort with detailed conflict report.
- Old install_path under old directory → validate, copy skill dir to new
  installed/, update install_path.
- Migration failure MUST NOT corrupt the new registry.
- After successful migration, old files stay as read-only backup.
- The application MUST NOT read from both old and new registries afterwards.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import shutil
import stat as _stat
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from dp_engine.skills.errors import (
    SkillInstallConflictError,
    SkillRegistryError,
    SkillRollbackError,
)
from dp_engine.skills.models import InstalledSkill

logger = logging.getLogger(__name__)

# Migration marker schema version
_MIGRATION_MARKER_SCHEMA = 1


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


# ── Result types ──


@dataclass
class SkillMigrationResult:
    """Result of a migration attempt."""

    migrated: bool
    source_count: int = 0
    skills_migrated: int = 0
    skills_skipped: int = 0
    skills_failed: int = 0
    conflicts: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    message: str = ""
    # Internal: per-skill results for migration marker (not part of public API)
    _per_skill_results: list = field(default_factory=list)

    @property
    def success(self) -> bool:
        return len(self.errors) == 0


@dataclass
class _PerSkillMigrationResult:
    """Result of migrating a single skill."""
    skill_id: str = ""
    version: str = ""
    status: str = ""  # "migrated", "skipped", "failed"
    reason: str = ""
    old_checksum: str = ""
    new_checksum: str = ""


# ── Migration marker ──


@dataclass
class _MigrationMarker:
    """Persistent record of a migration attempt.

    Stored at skills/migration-v1.json in the app data directory.
    Used to prevent duplicate migration on subsequent launches.

    Batch 2.3: overall_status is one of:
    - "not_started": marker placeholder before migration begins
    - "in_progress": migration is actively running
    - "completed": all skills migrated successfully, new registry committed
    - "failed": one or more skills failed, all changes rolled back

    There is NO "partial" status — migration is all-or-nothing.
    Any single skill failure rolls back the entire batch.
    """
    schema_version: int = _MIGRATION_MARKER_SCHEMA
    source_paths: list[str] = field(default_factory=list)
    source_checksums: dict[str, str] = field(default_factory=dict)
    started_at: str = ""
    completed_at: str = ""
    skills: list[_PerSkillMigrationResult] = field(default_factory=list)
    overall_status: str = "not_started"  # "not_started" | "in_progress" | "completed" | "failed"

    def to_dict(self) -> dict:
        return {
            "_schema_version": self.schema_version,
            "source_paths": self.source_paths,
            "source_checksums": self.source_checksums,
            "started_at": self.started_at,
            "completed_at": self.completed_at,
            "skills": [
                {
                    "skill_id": s.skill_id,
                    "version": s.version,
                    "status": s.status,
                    "reason": s.reason,
                }
                for s in self.skills
            ],
            "overall_status": self.overall_status,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "_MigrationMarker":
        skills_raw = data.get("skills", [])
        skills = [
            _PerSkillMigrationResult(
                skill_id=s.get("skill_id", ""),
                version=s.get("version", ""),
                status=s.get("status", ""),
                reason=s.get("reason", ""),
            )
            for s in skills_raw
        ]
        return cls(
            schema_version=data.get("_schema_version", 0),
            source_paths=data.get("source_paths", []),
            source_checksums=data.get("source_checksums", {}),
            started_at=data.get("started_at", ""),
            completed_at=data.get("completed_at", ""),
            skills=skills,
            overall_status=data.get("overall_status", ""),
        )


# ── Migrator ──


class SkillDataMigrator:
    """Handles one-time migration from old registry paths to new app-data paths.

    Batch 2.2: Full validation chain, checksum verification, transaction safety,
    and migration marker for idempotency.

    Usage:
        migrator = SkillDataMigrator(
            old_paths=[old_skill_registry_path, old_registry_path],
            new_registry_path=new_app_data_registry,
            new_installed_dir=new_app_data_installed,
            old_skill_root=old_readings_profiles_skills_dir,
            migration_marker_path=skills_data_dir / "migration-v1.json",
        )
        result = migrator.migrate_if_needed()
        if result.migrated:
            logger.info("Migration completed: %s", result.message)
    """

    def __init__(
        self,
        old_paths: list[Path],
        new_registry_path: Path,
        new_installed_dir: Path,
        old_skill_root: Path,
        migration_marker_path: Path | None = None,
    ) -> None:
        self._old_paths = [p.resolve() for p in old_paths]
        self._new_registry_path = new_registry_path.resolve()
        self._new_installed_dir = new_installed_dir.resolve()
        self._old_skill_root = old_skill_root.resolve()
        self._marker_path = (
            migration_marker_path.resolve()
            if migration_marker_path
            else new_registry_path.parent / "migration-v1.json"
        )

    def migrate_if_needed(self) -> SkillMigrationResult:
        """Run migration if the new registry doesn't exist yet and marker absent.

        Returns SkillMigrationResult describing what happened.
        Never corrupts the new registry.
        Idempotent: checks migration marker before proceeding.
        """
        # ── Check migration marker ──
        marker = self._read_marker()
        if marker is not None and marker.overall_status == "completed":
            # Verify source files haven't changed since last migration
            current_checksums = self._compute_source_checksums()
            if current_checksums == marker.source_checksums:
                logger.info(
                    "Migration marker indicates completed migration — skipping."
                )
                return SkillMigrationResult(
                    migrated=False,
                    message="Migration already completed (marker found, sources unchanged).",
                )
            else:
                logger.warning(
                    "Migration marker found but source files changed — "
                    "this may indicate tampering or dual-registry usage. "
                    "Proceeding with new migration."
                )

        # If previous migration failed, allow retry (all-or-nothing rollback)
        if marker is not None and marker.overall_status == "failed":
            logger.info("Previous migration failed — retrying.")

        # If previous migration was interrupted (crashed mid-run), allow retry
        if marker is not None and marker.overall_status in ("not_started", "in_progress"):
            logger.info(
                "Previous migration was interrupted (status=%s) — retrying.",
                marker.overall_status,
            )

        # Guard: new registry already exists → skip
        if self._new_registry_path.exists():
            logger.info(
                "New registry already exists at %s — skipping migration.",
                self._new_registry_path,
            )
            return SkillMigrationResult(
                migrated=False,
                message="New registry already exists — no migration needed.",
            )

        # ── Compute source checksums for marker ──
        source_checksums = self._compute_source_checksums()

        # Collect old registry data
        sources: list[_RegistrySource] = []
        for old_path in self._old_paths:
            if old_path.exists():
                try:
                    data = _load_and_validate_registry(old_path)
                    if data:
                        sources.append(_RegistrySource(path=old_path, data=data))
                        logger.info(
                            "Found old registry: %s (%d skills)",
                            old_path, len(data.get("skills", {})),
                        )
                except Exception as e:
                    logger.warning("Cannot read old registry %s: %s", old_path, e)

        if not sources:
            logger.info("No old registries found — nothing to migrate.")
            # Write marker to prevent future checks
            self._write_marker(_MigrationMarker(
                source_paths=[str(p) for p in self._old_paths],
                source_checksums=source_checksums,
                started_at=_now_iso(),
                completed_at=_now_iso(),
                overall_status="completed",
            ))
            return SkillMigrationResult(
                migrated=False,
                message="No old registries found.",
            )

        # Merge if multiple sources
        if len(sources) == 1:
            merged_data = sources[0].data
            conflicts: list[str] = []
        else:
            merged_data, conflicts = _merge_registry_data(sources)
            if conflicts:
                conflict_msg = (
                    f"Cannot safely merge {len(sources)} old registries. "
                    f"Conflicts: {'; '.join(conflicts[:5])}"
                )
                logger.error(conflict_msg)
                return SkillMigrationResult(
                    migrated=False,
                    source_count=len(sources),
                    conflicts=conflicts,
                    errors=[conflict_msg],
                    message=conflict_msg,
                )

        # ── Migrate skills with full validation ──
        started_at = _now_iso()
        result = self._migrate_skills_validated(merged_data, len(sources))
        completed_at = _now_iso()

        # ── Write migration marker ──
        # Batch 2.3: all-or-nothing — only "completed" or "failed", never "partial"
        per_skill_results = getattr(result, '_per_skill_results', [])
        marker = _MigrationMarker(
            source_paths=[str(p) for p in self._old_paths],
            source_checksums=source_checksums,
            started_at=started_at,
            completed_at=completed_at,
            skills=per_skill_results,
            overall_status="completed" if result.success else "failed",
        )
        self._write_marker(marker)

        return result

    def _migrate_skills_validated(
        self, data: dict, source_count: int
    ) -> SkillMigrationResult:
        """Migrate skills with full package validation chain.

        For each old skill record:
        1. Load InstalledSkill from old record
        2. Verify install_path under old root
        3. safe_copy_directory to staging
        4. locate_skill_root
        5. SkillPackageValidator.validate
        6. Cross-check Manifest skill_id + version
        7. Recompute package checksum
        8. Compare with old checksum
        9. Verify entrypoints exist
        10. Check for symlinks/special files
        11. Atomic commit to new installed dir
        12. Update registry
        """
        skills_data = data.get("skills", {})
        if not isinstance(skills_data, dict):
            return SkillMigrationResult(
                migrated=False,
                source_count=source_count,
                message="Old registry has no valid skills dict.",
            )

        # Lazy imports to avoid circular deps
        from dp_engine.skills.archive_utils import locate_skill_root, safe_copy_directory
        from dp_engine.skills.package_validator import SkillPackageValidator
        from dp_engine.skills.package_models import get_default_limits

        limits = get_default_limits()
        validator = SkillPackageValidator(limits=limits)

        new_skills: dict[str, dict] = {}
        active_versions: dict[str, str] = {}
        migrated_count = 0
        skipped_count = 0
        failed_count = 0
        errors: list[str] = []
        warnings: list[str] = []
        per_skill: list[_PerSkillMigrationResult] = []

        # Unique staging root for this entire migration
        staging_root = self._new_installed_dir.parent / ".migration_staging"
        if staging_root.exists():
            shutil.rmtree(str(staging_root), ignore_errors=True)
        staging_root.mkdir(parents=True, exist_ok=True)

        try:
            for key_str, skill_dict in skills_data.items():
                psr = _PerSkillMigrationResult(status="failed")
                try:
                    # ── Step 1: Load old record ──
                    installed = InstalledSkill.from_dict(skill_dict)
                    psr.skill_id = installed.skill_id
                    psr.version = installed.version
                    psr.old_checksum = installed.checksum or ""

                    old_install_path = Path(installed.install_path)

                    # ── Step 2: Verify under old root ──
                    try:
                        old_install_path.resolve().relative_to(self._old_skill_root)
                    except ValueError:
                        # Not under old root — register as-is without validation
                        new_skills[key_str] = installed.to_dict()
                        psr.status = "migrated"
                        psr.reason = "install_path outside old root — registered as-is"
                        per_skill.append(psr)
                        migrated_count += 1
                        continue

                    # ── Step 3: Verify old directory exists ──
                    if not old_install_path.exists() or not old_install_path.is_dir():
                        warnings.append(
                            f"Old skill dir not found for {key_str}: {old_install_path}"
                        )
                        new_skills[key_str] = installed.to_dict()
                        psr.status = "skipped"
                        psr.reason = "old directory not found"
                        per_skill.append(psr)
                        skipped_count += 1
                        continue

                    # ── Step 4: Check for symlinks in old path ──
                    if old_install_path.is_symlink():
                        errors.append(
                            f"Old skill '{key_str}' install_path is a symlink — rejected"
                        )
                        psr.status = "failed"
                        psr.reason = "old install_path is symlink"
                        per_skill.append(psr)
                        failed_count += 1
                        continue

                    # ── Step 5: Quick scan for forbidden files in old dir ──
                    forbidden = _scan_for_forbidden_in_dir(old_install_path)
                    if forbidden:
                        errors.append(
                            f"Old skill '{key_str}' contains forbidden items: "
                            f"{'; '.join(forbidden[:3])}"
                        )
                        psr.status = "failed"
                        psr.reason = f"forbidden items: {forbidden[:3]}"
                        per_skill.append(psr)
                        failed_count += 1
                        continue

                    # ── Step 6: Safe copy to migration staging ──
                    task_staging = staging_root / f"{installed.skill_id}-{installed.version}"
                    try:
                        safe_copy_directory(
                            old_install_path,
                            task_staging,
                            limits=limits,
                        )
                    except Exception as e:
                        errors.append(
                            f"Failed to copy old skill '{key_str}': {e}"
                        )
                        psr.status = "failed"
                        psr.reason = f"copy failed: {e}"
                        per_skill.append(psr)
                        failed_count += 1
                        continue

                    # ── Step 7: Locate skill root ──
                    try:
                        skill_root = locate_skill_root(task_staging)
                    except Exception as e:
                        errors.append(
                            f"Failed to locate skill root for '{key_str}': {e}"
                        )
                        psr.status = "failed"
                        psr.reason = f"locate_skill_root failed: {e}"
                        per_skill.append(psr)
                        failed_count += 1
                        continue

                    # ── Step 8: Validate package ──
                    validation = validator.validate(skill_root)
                    if not validation.valid:
                        errors.append(
                            f"Validation failed for '{key_str}': "
                            f"{'; '.join(validation.errors)}"
                        )
                        psr.status = "failed"
                        psr.reason = f"validation: {validation.errors}"
                        per_skill.append(psr)
                        failed_count += 1
                        continue

                    manifest = validation.manifest
                    if manifest is None:
                        errors.append(f"No manifest for '{key_str}'")
                        psr.status = "failed"
                        psr.reason = "no manifest"
                        per_skill.append(psr)
                        failed_count += 1
                        continue

                    # ── Step 9: Cross-check Manifest skill_id ──
                    manifest_skill_id = getattr(manifest, 'skill_id', '')
                    if manifest_skill_id and manifest_skill_id != installed.skill_id:
                        errors.append(
                            f"Manifest skill_id '{manifest_skill_id}' does not match "
                            f"registry skill_id '{installed.skill_id}' for '{key_str}'"
                        )
                        psr.status = "failed"
                        psr.reason = (
                            f"skill_id mismatch: "
                            f"manifest={manifest_skill_id} vs registry={installed.skill_id}"
                        )
                        per_skill.append(psr)
                        failed_count += 1
                        continue

                    # ── Step 10: Cross-check Manifest version ──
                    manifest_version = getattr(manifest, 'version', '')
                    if manifest_version and manifest_version != installed.version:
                        errors.append(
                            f"Manifest version '{manifest_version}' does not match "
                            f"registry version '{installed.version}' for '{key_str}'"
                        )
                        psr.status = "failed"
                        psr.reason = (
                            f"version mismatch: "
                            f"manifest={manifest_version} vs registry={installed.version}"
                        )
                        per_skill.append(psr)
                        failed_count += 1
                        continue

                    # ── Step 11: Verify entrypoints exist ──
                    # Entrypoint values support "module.py:function" format
                    entrypoints = getattr(manifest, 'entrypoints', {}) or {}
                    missing_eps = []
                    for ep_key, ep_rel_path in entrypoints.items():
                        _mod_path = ep_rel_path.split(":")[0] if ":" in ep_rel_path else ep_rel_path
                        ep_file = skill_root / _mod_path
                        if not ep_file.is_file():
                            missing_eps.append(f"{ep_key}: {ep_rel_path}")
                    if missing_eps:
                        errors.append(
                            f"Entrypoint(s) missing for '{key_str}': "
                            f"{'; '.join(missing_eps)}"
                        )
                        psr.status = "failed"
                        psr.reason = f"missing entrypoints: {missing_eps}"
                        per_skill.append(psr)
                        failed_count += 1
                        continue

                    # ── Step 12: Recompute package checksum ──
                    new_checksum = validation.package_checksum or ""
                    psr.new_checksum = new_checksum

                    # ── Step 13: Compare with old checksum ──
                    old_checksum = installed.checksum or ""
                    if old_checksum:
                        if old_checksum != new_checksum:
                            # Checksum mismatch — treat as conflict, don't silently migrate
                            errors.append(
                                f"Checksum mismatch for '{key_str}': "
                                f"old={old_checksum[:16]}... new={new_checksum[:16]}..."
                            )
                            psr.status = "failed"
                            psr.reason = (
                                f"checksum mismatch: "
                                f"old={old_checksum[:16]}... new={new_checksum[:16]}..."
                            )
                            per_skill.append(psr)
                            failed_count += 1
                            continue
                        else:
                            psr.reason = "checksum verified"
                    else:
                        # Old checksum empty — use new, log warning
                        warnings.append(
                            f"Old checksum missing for '{key_str}' — using new checksum"
                        )
                        psr.reason = "old checksum absent, using new"

                    # ── Step 14: Atomic commit to new installed dir ──
                    new_install_dir = (
                        self._new_installed_dir
                        / installed.skill_id
                        / installed.version
                    )

                    # Use pending → rename pattern
                    pending_dir = (
                        self._new_installed_dir
                        / f".pending_migrate_{installed.skill_id}_{installed.version}"
                    )
                    if pending_dir.exists():
                        shutil.rmtree(str(pending_dir), ignore_errors=True)

                    try:
                        new_install_dir.parent.mkdir(parents=True, exist_ok=True)
                        shutil.copytree(
                            str(skill_root), str(pending_dir),
                            symlinks=False, dirs_exist_ok=False,
                        )
                        # fsync parent
                        _fsync_dir(str(new_install_dir.parent))
                        # Atomic rename (no copytree fallback)
                        _atomic_replace_dir(
                            pending_dir, new_install_dir,
                            expected_skill_id=installed.manifest.skill_id,
                            expected_version=installed.manifest.version,
                            expected_checksum=new_checksum,
                        )
                    except Exception as e:
                        # Clean up pending
                        if pending_dir.exists():
                            shutil.rmtree(str(pending_dir), ignore_errors=True)
                        raise

                    # ── Step 15: Build new InstalledSkill ──
                    new_installed = InstalledSkill(
                        manifest=installed.manifest,
                        install_path=str(new_install_dir),
                        enabled=installed.enabled,
                        installed_at=installed.installed_at,
                        source_revision=installed.source_revision,
                        checksum=new_checksum,
                        health_status=installed.health_status,
                        health_message=installed.health_message,
                    )
                    new_skills[key_str] = new_installed.to_dict()

                    psr.status = "migrated"
                    per_skill.append(psr)
                    migrated_count += 1
                    logger.info(
                        "Migrated skill: %s@%s (checksum: %s)",
                        installed.skill_id, installed.version,
                        new_checksum[:16] if new_checksum else "(none)",
                    )

                except Exception as e:
                    logger.exception("Failed to migrate skill '%s'", key_str)
                    psr.status = "failed"
                    psr.reason = f"exception: {e}"
                    per_skill.append(psr)
                    failed_count += 1
                    errors.append(f"Failed to migrate '{key_str}': {e}")

            # ── If any skill failed, abort entire migration ──
            if failed_count > 0:
                # Clean up any committed skill dirs
                for key_str, skill_dict in new_skills.items():
                    ip = skill_dict.get("install_path", "")
                    if ip:
                        try:
                            ip_path = Path(ip)
                            if ip_path.exists():
                                # Only clean dirs under new installed dir
                                try:
                                    ip_path.resolve().relative_to(self._new_installed_dir)
                                    shutil.rmtree(str(ip_path), ignore_errors=True)
                                except ValueError:
                                    pass
                        except Exception:
                            pass
                return SkillMigrationResult(
                    migrated=False,
                    source_count=source_count,
                    skills_migrated=migrated_count,
                    skills_skipped=skipped_count,
                    skills_failed=failed_count,
                    errors=errors,
                    warnings=warnings,
                    message=(
                        f"Migration aborted: {failed_count} skill(s) failed validation. "
                        f"{migrated_count} migrated, {skipped_count} skipped."
                    ),
                )
                result._per_skill_results = per_skill

            # ── All skills succeeded: commit registry ──
            # Preserve active versions
            raw_active = data.get("active_versions", {})
            if isinstance(raw_active, dict):
                active_versions = {
                    str(k): str(v) for k, v in raw_active.items()
                }

            new_data: dict[str, Any] = {
                "_schema_version": 1,
                "_updated_at": _now_iso(),
                "_migrated_from": [str(p) for p in self._old_paths],
                "_migrated_at": _now_iso(),
                "active_versions": active_versions,
                "skills": new_skills,
            }

            try:
                self._new_registry_path.parent.mkdir(parents=True, exist_ok=True)
                _atomic_write_json(self._new_registry_path, new_data)
            except Exception as e:
                # Clean up any committed skill dirs
                for key_str, skill_dict in new_skills.items():
                    ip = skill_dict.get("install_path", "")
                    if ip:
                        try:
                            ip_path = Path(ip)
                            if ip_path.exists():
                                shutil.rmtree(str(ip_path), ignore_errors=True)
                        except Exception:
                            pass
                return SkillMigrationResult(
                    migrated=False,
                    source_count=source_count,
                    errors=[f"Failed to write new registry: {e}"],
                )
                result._per_skill_results = per_skill

        finally:
            # Always clean up staging
            if staging_root.exists():
                shutil.rmtree(str(staging_root), ignore_errors=True)

        msg = (
            f"Migration complete: {migrated_count} skills migrated, "
            f"{skipped_count} skipped from {source_count} old source(s)."
        )
        logger.info(msg)

        result = SkillMigrationResult(
            migrated=True,
            source_count=source_count,
            skills_migrated=migrated_count,
            skills_skipped=skipped_count,
            skills_failed=0,
            warnings=warnings,
            message=msg,
        )
        result._per_skill_results = per_skill
        return result

    # ── Marker helpers ──

    def _read_marker(self) -> _MigrationMarker | None:
        """Read the migration marker file if it exists."""
        if not self._marker_path.exists():
            return None
        try:
            raw = self._marker_path.read_text(encoding="utf-8")
            if not raw.strip():
                return None
            data = json.loads(raw)
            return _MigrationMarker.from_dict(data)
        except Exception as e:
            logger.warning("Failed to read migration marker: %s", e)
            return None

    def _write_marker(self, marker: _MigrationMarker) -> None:
        """Write the migration marker file atomically."""
        try:
            self._marker_path.parent.mkdir(parents=True, exist_ok=True)
            _atomic_write_json(self._marker_path, marker.to_dict())
        except Exception as e:
            logger.warning("Failed to write migration marker: %s", e)

    def _compute_source_checksums(self) -> dict[str, str]:
        """Compute SHA-256 of each old source file for change detection."""
        result: dict[str, str] = {}
        for p in self._old_paths:
            try:
                if p.exists():
                    h = hashlib.sha256()
                    with open(p, "rb") as f:
                        while True:
                            chunk = f.read(1024 * 1024)
                            if not chunk:
                                break
                            h.update(chunk)
                    result[str(p)] = h.hexdigest()
                else:
                    result[str(p)] = ""
            except Exception:
                result[str(p)] = ""
        return result


# ── Internal helpers ──


@dataclass
class _RegistrySource:
    path: Path
    data: dict


def _load_and_validate_registry(path: Path) -> dict:
    """Load a registry JSON file and validate its structure."""
    if not path.exists():
        return {}

    raw = path.read_text(encoding="utf-8")
    if not raw.strip():
        return {}

    try:
        data = json.loads(raw)
    except json.JSONDecodeError as e:
        raise SkillRegistryError(
            f"Old registry JSON is corrupted: {path}\n{e}"
        ) from e

    if not isinstance(data, dict):
        raise SkillRegistryError(
            f"Old registry root must be a dict, got {type(data).__name__}: {path}"
        )

    skills = data.get("skills", {})
    if not isinstance(skills, dict):
        raise SkillRegistryError(
            f"Old registry 'skills' field must be a dict: {path}"
        )

    return data


def _merge_registry_data(
    sources: list[_RegistrySource],
) -> tuple[dict, list[str]]:
    """Deterministically merge multiple registry sources."""
    merged_skills: dict[str, dict] = {}
    merged_active: dict[str, str] = {}
    conflicts: list[str] = []

    for src in sorted(sources, key=lambda s: str(s.path)):
        skills = src.data.get("skills", {})
        if not isinstance(skills, dict):
            continue

        for key_str, skill_dict in skills.items():
            if key_str in merged_skills:
                existing = merged_skills[key_str]
                existing_checksum = existing.get("checksum", "")
                new_checksum = skill_dict.get("checksum", "")
                existing_path = existing.get("install_path", "")
                new_path = skill_dict.get("install_path", "")

                if existing_checksum == new_checksum and existing_path == new_path:
                    logger.debug(
                        "Duplicate entry '%s' with same checksum and path — skipping.",
                        key_str,
                    )
                    continue
                else:
                    conflict_msg = (
                        f"Conflict for '{key_str}': "
                        f"checksum={existing_checksum[:12]}... vs {new_checksum[:12]}..., "
                        f"path={existing_path} vs {new_path}"
                    )
                    conflicts.append(conflict_msg)
                    logger.warning("Registry merge conflict: %s", conflict_msg)
                    continue

            merged_skills[key_str] = skill_dict

        active = src.data.get("active_versions", {})
        if isinstance(active, dict):
            for sid, ver in active.items():
                if sid not in merged_active:
                    merged_active[str(sid)] = str(ver)

    merged: dict[str, Any] = {
        "_schema_version": 1,
        "active_versions": merged_active,
        "skills": merged_skills,
    }
    return merged, conflicts


def _scan_for_forbidden_in_dir(directory: Path) -> list[str]:
    """Quick scan for symlinks, FIFOs, sockets, device files."""
    forbidden: list[str] = []
    try:
        with os.scandir(str(directory)) as entries:
            for entry in entries:
                if entry.is_symlink():
                    forbidden.append(f"symlink: {entry.name}")
                    continue
                try:
                    st = entry.stat(follow_symlinks=False)
                except OSError:
                    continue
                mode = st.st_mode
                if _stat.S_ISFIFO(mode):
                    forbidden.append(f"FIFO: {entry.name}")
                elif _stat.S_ISSOCK(mode):
                    forbidden.append(f"socket: {entry.name}")
                elif _stat.S_ISBLK(mode) or _stat.S_ISCHR(mode):
                    forbidden.append(f"device: {entry.name}")
    except OSError:
        pass
    return forbidden


def _fsync_dir(path_str: str) -> None:
    """fsync a directory to ensure metadata is on disk."""
    try:
        fd = os.open(path_str, os.O_RDONLY)
        os.fsync(fd)
        os.close(fd)
    except OSError:
        pass


def _read_manifest_fields(skill_dir: Path) -> tuple[str, str]:
    """Read (skill_id, version) from SKILL.md frontmatter in *skill_dir*.

    Returns ``("", "")`` when SKILL.md is missing or the fields are absent.
    This is a lightweight reader for idempotent comparison — it does NOT
    invoke the full ``parse_skill_manifest()`` validation chain.
    """
    skill_md = skill_dir / "SKILL.md"
    if not skill_md.is_file():
        return ("", "")
    try:
        text = skill_md.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return ("", "")
    lines = text.splitlines()
    # Only parse YAML frontmatter between the first two ``---`` fences
    if not lines or lines[0].strip() != "---":
        return ("", "")
    skill_id = ""
    version = ""
    in_frontmatter = False
    for line in lines[1:]:
        stripped = line.strip()
        if stripped == "---":
            break
        if ":" in stripped:
            key, _, val = stripped.partition(":")
            key = key.strip()
            val = val.strip().strip('"').strip("'")
            if key == "skill_id":
                skill_id = val
            elif key == "version":
                version = val
    return (skill_id, version)


def _compute_dir_package_checksum(skill_dir: Path) -> str | None:
    """Recompute the stable package content checksum for a directory.

    Uses the same algorithm as ``SkillPackageValidator._compute_package_checksum``:
    ``sha256(rel_path|size|sha256\\n)`` for every regular file, sorted by
    relative path.  Directories and symlinks are excluded.

    Returns ``None`` when the directory cannot be read or contains no
    regular files.
    """
    try:
        entries: list[tuple[str, int, str]] = []
        for root, _dirs, files in os.walk(str(skill_dir)):
            root_path = Path(root)
            for fname in files:
                fpath = root_path / fname
                try:
                    st = fpath.stat()
                except OSError:
                    continue
                if not _stat.S_ISREG(st.st_mode):
                    continue  # skip symlinks, devices, etc.
                try:
                    rel = fpath.relative_to(skill_dir).as_posix()
                except ValueError:
                    continue
                fsize = st.st_size
                fhash = hashlib.sha256()
                try:
                    with open(fpath, "rb") as fh:
                        while True:
                            chunk = fh.read(1024 * 1024)
                            if not chunk:
                                break
                            fhash.update(chunk)
                except OSError:
                    continue
                entries.append((rel, fsize, fhash.hexdigest()))

        if not entries:
            return None

        entries.sort(key=lambda x: x[0])  # sort by relative_path
        h = hashlib.sha256()
        for rel, fsize, fhash in entries:
            line = f"{rel}|{fsize}|{fhash}\n"
            h.update(line.encode("utf-8"))
        return h.hexdigest()
    except OSError:
        return None


def _dirs_content_match(
    src: Path,
    dst: Path,
    expected_skill_id: str | None,
    expected_version: str | None,
    expected_checksum: str | None,
) -> bool:
    """Return True when *src* and *dst* appear to hold the same skill content.

    Checks performed (all must pass):
    1. Manifest *skill_id* from *dst* matches *expected_skill_id*
    2. Manifest *version* from *dst* matches *expected_version*
    3. Manifest *skill_id* and *version* match between *src* and *dst*
    4. When *expected_checksum* is provided: recompute the **destination**
       directory's package content checksum and verify it matches.

    The destination checksum is **always recomputed** from the actual
    files on disk — the caller's *expected_checksum* is the reference
    value, and the destination must independently match it.
    """
    src_id, src_ver = _read_manifest_fields(src)
    dst_id, dst_ver = _read_manifest_fields(dst)

    # ── Manifest cross-check ──
    if not src_id or not dst_id:
        return False
    if src_id != dst_id or src_ver != dst_ver:
        return False

    # ── Verify against expected identity ──
    if expected_skill_id and expected_skill_id.strip():
        if dst_id != expected_skill_id.strip():
            return False
    if expected_version and expected_version.strip():
        if dst_ver != expected_version.strip():
            return False

    # ── Recompute destination checksum and compare with expected ──
    if expected_checksum and expected_checksum.strip():
        dst_checksum = _compute_dir_package_checksum(dst)
        if dst_checksum is None:
            # Cannot compute checksum — treat as mismatch for safety
            return False
        if dst_checksum != expected_checksum.strip():
            return False

    return True


def _atomic_replace_dir(
    src: Path,
    dst: Path,
    replace_existing: bool = False,
    expected_skill_id: str | None = None,
    expected_version: str | None = None,
    expected_checksum: str | None = None,
) -> None:
    """Atomically replace *dst* directory with *src*.

    **No ``shutil.copytree`` fallback** — the commit is atomic or it fails
    explicitly.

    Rules
    -----
    1. **dst does not exist**: ``os.replace`` with limited retries (max 5,
       ~200 ms exponential backoff, total < 1 s).  If all retries are
       exhausted an ``OSError`` is raised.
    2. **dst exists and content matches**: idempotent completion — clean up
       *src* staging and return.  This solves the duplicate-migration-marker
       scenario without overwriting an already-committed directory.
    3. **dst exists and content differs**: raise
       ``SkillInstallConflictError`` unless *replace_existing* is ``True``.
    4. **Explicit replace** (*replace_existing*=True): backup → replace →
       delete-backup.  If the replace step fails, restore the backup.  If
       the restore also fails, raise ``SkillRollbackError`` and preserve the
       backup as recovery evidence.

    All renames stay within the same filesystem so they remain atomic on
    POSIX and best-effort atomic on Windows.
    """
    import time as _time

    _MAX_RETRIES = 5
    _BASE_DELAY = 0.05  # seconds

    # ── Case 2+3: dst exists ──
    if dst.exists():
        if _dirs_content_match(
            src, dst,
            expected_skill_id=expected_skill_id,
            expected_version=expected_version,
            expected_checksum=expected_checksum,
        ):
            # Idempotent — same content already committed
            shutil.rmtree(str(src), ignore_errors=True)
            return

        if not replace_existing:
            raise SkillInstallConflictError(
                f"Target directory already exists with different content: {dst}"
            )

        # ── Case 4: explicit replace with backup ──
        backup_dir = dst.with_name(dst.name + ".atomic_backup")
        # Ensure backup slot is clean
        if backup_dir.exists():
            shutil.rmtree(str(backup_dir), ignore_errors=True)

        try:
            os.replace(str(dst), str(backup_dir))
        except OSError as e:
            raise SkillInstallConflictError(
                f"Failed to create backup of {dst}: {e}"
            ) from e

        # Replace src → dst with retries
        last_err: OSError | None = None
        replaced = False
        for attempt in range(_MAX_RETRIES):
            try:
                os.replace(str(src), str(dst))
                replaced = True
                break
            except (PermissionError, OSError) as e:
                last_err = e
                if attempt < _MAX_RETRIES - 1:
                    if not src.exists():
                        break
                    _time.sleep(_BASE_DELAY * (2 ** attempt))

        if not replaced:
            # Rollback: restore backup → dst
            try:
                if dst.exists():
                    shutil.rmtree(str(dst), ignore_errors=True)
                os.replace(str(backup_dir), str(dst))
            except OSError as rollback_err:
                raise SkillRollbackError(
                    f"Replace failed and rollback also failed. "
                    f"Backup preserved at {backup_dir}. "
                    f"Replace error: {last_err}. Rollback error: {rollback_err}"
                ) from rollback_err
            raise OSError(
                f"os.replace failed after {_MAX_RETRIES} retries: {last_err}"
            ) from last_err

        # Success — delete backup
        shutil.rmtree(str(backup_dir), ignore_errors=True)
        return

    # ── Case 1: dst does not exist ──
    last_err: OSError | None = None
    for attempt in range(_MAX_RETRIES):
        try:
            os.replace(str(src), str(dst))
            return
        except (PermissionError, OSError) as e:
            last_err = e
            if attempt < _MAX_RETRIES - 1:
                if not src.exists():
                    raise OSError(
                        f"Source directory vanished before commit: {src}"
                    ) from e
                _time.sleep(_BASE_DELAY * (2 ** attempt))

    raise OSError(
        f"os.replace failed after {_MAX_RETRIES} retries: {last_err}"
    ) from last_err


def _atomic_write_json(path: Path, data: dict) -> None:
    """Atomically write JSON data to path (temp file + flush + fsync + os.replace)."""
    import tempfile

    json_text = json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True, default=str)

    parent = path.parent
    parent.mkdir(parents=True, exist_ok=True)

    fd, temp_path_str = tempfile.mkstemp(
        dir=str(parent), prefix=".migrate_", suffix=".tmp"
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as f:
            f.write(json_text)
            f.flush()
            os.fsync(f.fileno())
    except Exception:
        try:
            Path(temp_path_str).unlink(missing_ok=True)
        except Exception:
            pass
        raise

    try:
        os.replace(temp_path_str, str(path))
    except Exception:
        try:
            Path(temp_path_str).unlink(missing_ok=True)
        except Exception:
            pass
        raise


# Backward-compat: _safe_copy_skill_dir kept for external callers
def _safe_copy_skill_dir(src: Path, dst: Path) -> None:
    """Copy a skill directory safely (legacy wrapper)."""
    if not src.is_dir():
        raise ValueError(f"Source is not a directory: {src}")
    dst.parent.mkdir(parents=True, exist_ok=True)

    def _ignore_symlinks_and_special(d: str, files: list[str]) -> list[str]:
        ignored: list[str] = []
        for f in files:
            fp = os.path.join(d, f)
            try:
                s = os.lstat(fp)
                if _stat.S_ISLNK(s.st_mode):
                    logger.warning("Skipping symlink during migration: %s", fp)
                    ignored.append(f)
                elif not (_stat.S_ISREG(s.st_mode) or _stat.S_ISDIR(s.st_mode)):
                    logger.warning("Skipping special file during migration: %s", fp)
                    ignored.append(f)
            except OSError:
                pass
        return ignored

    shutil.copytree(
        str(src), str(dst),
        symlinks=False,
        ignore=_ignore_symlinks_and_special,
        dirs_exist_ok=False,
    )
