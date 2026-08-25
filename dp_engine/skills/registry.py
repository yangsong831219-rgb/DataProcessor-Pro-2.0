"""Local skill registry with JSON persistence.

The SkillRegistry is the Single Source of Truth for installed skill metadata.
It does NOT execute skills, manage processes, or handle downloads.

Active version rules:
- Each skill_id can have multiple installed versions.
- At most ONE version per skill_id is the "active" version at any time.
- The first version registered for a skill_id automatically becomes active.
- Registering additional versions does NOT change the active version.
- set_active_version() requires the target version to be installed.
- Switching active version auto-disables the old active and auto-enables
  the new active.
- Only the active version may be enabled (set_enabled(True) on a
  non-active version raises SkillRegistryError).
- Deleting the active version clears the active slot (does not auto-pick).
- get_active() returns None when no active version is set.

Thread-safety: single-process, single-writer.  Protected by threading.RLock.
Atomic saves use tempfile.mkstemp + os.fsync + os.replace.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from enum import Enum
import logging
import os
import tempfile
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from dp_engine.skills.errors import SkillRegistryError
from dp_engine.skills.models import InstalledSkill


# ── Consistency check mode (Batch 2.3) ──


class ConsistencyCheckMode(str, Enum):
    """Controls the depth of installation consistency validation.

    FULL (default):
        - Manifest parsing, entrypoint check, symlink/special file check
        - Package checksum recomputation and Registry checksum comparison
        - active/enabled rules
        Future Runtime MUST use FULL mode — only skills with zero blocking
        issues under FULL check may be executed.

    FAST:
        - Skips full file content SHA-256 recomputation
        - Still checks: install_path boundary, directory existence, path
          structure, SKILL.md, Manifest ID/version, entrypoint existence,
          symlink/special files, active/enabled rules
        - Always emits CHECKSUM_NOT_VERIFIED warning
        - MUST NOT be used before Runtime startup
        - MUST NOT be interpreted as "skill is fully trusted"
    """
    FAST = "fast"
    FULL = "full"

logger = logging.getLogger(__name__)

# Current registry file schema version.
# Increment when the on-disk format changes in a breaking way.
# v1: initial format with skills dict only
# (active_versions key was added backward-compatibly — missing key → empty dict)
REGISTRY_SCHEMA_VERSION = 1


def _now_iso() -> str:
    """Return current UTC time as ISO 8601 string."""
    return datetime.now(timezone.utc).isoformat()


def _load_json_safe(path: Path) -> dict:
    """Load and parse a JSON file.  Returns empty dict if file does not exist.

    Raises SkillRegistryError on parse failure — never returns empty dict
    for a corrupted file (to prevent silent data loss).
    """
    if not path.exists():
        return {}

    try:
        raw = path.read_text(encoding="utf-8")
    except OSError as e:
        raise SkillRegistryError(
            f"Cannot read registry file: {path}\n{e}"
        ) from e

    if not raw.strip():
        return {}

    try:
        data = json.loads(raw)
    except json.JSONDecodeError as e:
        raise SkillRegistryError(
            f"Registry JSON is corrupted: {path}\n{e}\n"
            f"The damaged file has been preserved at '{path}' for investigation. "
            f"To recover, delete or repair this file manually."
        ) from e

    if not isinstance(data, dict):
        raise SkillRegistryError(
            f"Registry JSON root must be a dict, got {type(data).__name__}: {path}"
        )

    return data


def _atomic_save(path: Path, data: dict) -> None:
    """Atomically write JSON data to path.

    1. Serialize data to JSON in memory (fail-early: don't touch disk on
       serialization error).
    2. Write to a UNIQUE temp file in the same directory (tempfile.mkstemp).
    3. flush() + os.fsync() to ensure the temp file is on disk.
    4. os.replace() to atomically swap.
    5. On any failure, clean up the temp file and raise.

    The original file is NOT touched until os.replace() succeeds.
    """
    # Step 1: serialize in memory first — if this fails, nothing is on disk
    try:
        json_text = json.dumps(
            data,
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
            default=str,
        )
    except (TypeError, ValueError) as e:
        raise SkillRegistryError(
            f"Failed to serialize registry data to JSON: {e}"
        ) from e

    # Step 2: ensure parent directory exists
    parent = path.parent
    try:
        parent.mkdir(parents=True, exist_ok=True)
    except OSError as e:
        raise SkillRegistryError(
            f"Failed to create registry directory: {parent}\n{e}"
        ) from e

    # Step 3: write to a unique temp file in the same directory
    fd, temp_path_str = -1, ""
    try:
        fd, temp_path_str = tempfile.mkstemp(
            dir=str(parent),
            prefix=".registry_",
            suffix=".tmp",
        )
        temp_path = Path(temp_path_str)

        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as f:
            f.write(json_text)
            f.flush()
            os.fsync(f.fileno())
        # fd is now closed by the with-block; reset to -1 to avoid double-close
        fd = -1

    except OSError as e:
        # Clean up temp file on write failure
        if fd >= 0:
            try:
                os.close(fd)
            except OSError:
                pass
        if temp_path_str:
            try:
                Path(temp_path_str).unlink(missing_ok=True)
            except OSError:
                pass
        raise SkillRegistryError(
            f"Failed to write registry temp file: {e}"
        ) from e

    # Step 4: atomic replace
    try:
        os.replace(temp_path_str, path)
    except OSError as e:
        # Clean up temp file
        try:
            Path(temp_path_str).unlink(missing_ok=True)
        except OSError:
            pass
        raise SkillRegistryError(
            f"Failed to atomically replace registry file: {path}\n{e}"
        ) from e

    # Success — temp file is now the real file (os.replace is atomic).
    # No cleanup needed.


class SkillRegistry:
    """Local registry of installed skills, backed by a JSON file.

    Usage:
        registry = SkillRegistry(Path("/path/to/skills.json"))
        registry.load()
        registry.register(installed_skill)
        registry.set_active_version("ppt-master", "1.2.0")
        registry.save()

    All mutations (register, unregister, set_enabled, set_active_version) are
    applied in-memory immediately; call save() to persist.  This allows batch
    operations without repeated I/O.

    Thread-safety: all public methods that mutate or read shared state are
    protected by a single threading.RLock.  This is sufficient for the
    single-process, single-writer usage pattern.  Cross-process locking is
    not implemented.
    """

    def __init__(self, registry_path: Path) -> None:
        self._registry_path = Path(registry_path)
        # Internal storage: {(skill_id, version): InstalledSkill}
        self._skills: dict[tuple[str, str], InstalledSkill] = {}
        # Active version for each skill_id: {skill_id: version}
        self._active_versions: dict[str, str] = {}
        self._loaded = False
        self._lock = threading.RLock()

    # ── Persistence ──

    def load(self) -> None:
        """Load the registry from disk.

        Raises:
            SkillRegistryError: If the JSON is corrupted.
        """
        with self._lock:
            data = _load_json_safe(self._registry_path)

            if not data:
                self._skills = {}
                self._active_versions = {}
                self._loaded = True
                return

            # Validate schema version
            file_version = data.get("_schema_version", 0)
            if file_version > REGISTRY_SCHEMA_VERSION:
                raise SkillRegistryError(
                    f"Registry schema version {file_version} is newer than "
                    f"supported version {REGISTRY_SCHEMA_VERSION}. "
                    f"Please upgrade the application."
                )

            # Load active versions (backward-compat: missing key → empty dict)
            raw_active = data.get("active_versions", {})
            if not isinstance(raw_active, dict):
                logger.warning(
                    "Registry 'active_versions' is not a dict (got %s), ignoring.",
                    type(raw_active).__name__,
                )
                raw_active = {}
            self._active_versions = {
                str(k): str(v) for k, v in raw_active.items()
            }

            # Load skills
            skills_data = data.get("skills", {})
            if not isinstance(skills_data, dict):
                raise SkillRegistryError(
                    f"Registry 'skills' field must be a dict, got {type(skills_data).__name__}"
                )

            self._skills = {}
            for key_str, skill_dict in skills_data.items():
                try:
                    installed = InstalledSkill.from_dict(skill_dict)
                except Exception as e:
                    logger.warning(
                        "Skipping corrupted skill entry '%s' in registry: %s",
                        key_str, e,
                    )
                    continue
                self._skills[(installed.skill_id, installed.version)] = installed

            # Validate active_versions consistency: each active version must
            # reference an actually-installed skill.  If not, warn and clear.
            stale_active: list[str] = []
            for sid, ver in self._active_versions.items():
                if (sid, ver) not in self._skills:
                    logger.warning(
                        "Active version '%s@%s' references a missing skill — clearing.",
                        sid, ver,
                    )
                    stale_active.append(sid)
            for sid in stale_active:
                del self._active_versions[sid]

            self._loaded = True
            logger.info(
                "Loaded %d skills from registry: %s",
                len(self._skills), self._registry_path,
            )

    def save(self) -> None:
        """Persist the current in-memory state to disk (atomic write)."""
        with self._lock:
            data: dict[str, Any] = {
                "_schema_version": REGISTRY_SCHEMA_VERSION,
                "_updated_at": _now_iso(),
                "active_versions": dict(sorted(self._active_versions.items())),
                "skills": {
                    f"{skill_id}@{version}": installed.to_dict()
                    for (skill_id, version), installed in sorted(self._skills.items())
                },
            }
            _atomic_save(self._registry_path, data)
            logger.info(
                "Saved %d skills to registry: %s",
                len(self._skills), self._registry_path,
            )

    # ── Query ──

    def list_skills(self) -> list[InstalledSkill]:
        """Return all registered skills in stable order (sorted by skill_id, then version)."""
        with self._lock:
            return [
                installed
                for (skill_id, version), installed in sorted(self._skills.items())
            ]

    def get(self, skill_id: str, version: str | None = None) -> InstalledSkill | None:
        """Get an installed skill by skill_id.

        If version is None, returns the highest-version match (simple string sort).
        Otherwise returns the exact (skill_id, version) match.
        """
        with self._lock:
            if version is not None:
                return self._skills.get((skill_id, version))

            # Find highest version for this skill_id
            candidates = [
                (v, installed)
                for (sid, v), installed in self._skills.items()
                if sid == skill_id
            ]
            if not candidates:
                return None

            candidates.sort(key=lambda x: x[0], reverse=True)
            return candidates[0][1]

    # ── Active version management ──

    def get_active(self, skill_id: str) -> InstalledSkill | None:
        """Return the currently active version for skill_id, or None.

        The active version is the one that SkillRuntime will use when
        resolving a skill by skill_id alone.
        """
        with self._lock:
            active_ver = self._active_versions.get(skill_id)
            if active_ver is None:
                return None
            return self._skills.get((skill_id, active_ver))

    def set_active_version(self, skill_id: str, version: str) -> None:
        """Set the active version for a skill_id.

        Rules:
        - The target (skill_id, version) MUST exist in the registry.
        - The old active version (if any) is auto-disabled (enabled=False).
        - The new active version is auto-enabled (enabled=True).
        - Switching to the same version is a no-op.

        Raises:
            SkillRegistryError: If the target version is not installed.
        """
        with self._lock:
            key = (skill_id, version)
            if key not in self._skills:
                raise SkillRegistryError(
                    f"Cannot set active version: '{skill_id}@{version}' "
                    f"is not installed. Register the skill first."
                )

            old_active_ver = self._active_versions.get(skill_id)

            # No-op if already active
            if old_active_ver == version:
                return

            # Disable old active version
            if old_active_ver is not None:
                old_key = (skill_id, old_active_ver)
                if old_key in self._skills:
                    old_skill = self._skills[old_key]
                    self._skills[old_key] = InstalledSkill(
                        manifest=old_skill.manifest,
                        install_path=old_skill.install_path,
                        enabled=False,
                        installed_at=old_skill.installed_at,
                        source_revision=old_skill.source_revision,
                        checksum=old_skill.checksum,
                        health_status=old_skill.health_status,
                        health_message=old_skill.health_message,
                    )

            # Enable new active version
            new_skill = self._skills[key]
            self._skills[key] = InstalledSkill(
                manifest=new_skill.manifest,
                install_path=new_skill.install_path,
                enabled=True,
                installed_at=new_skill.installed_at,
                source_revision=new_skill.source_revision,
                checksum=new_skill.checksum,
                health_status=new_skill.health_status,
                health_message=new_skill.health_message,
            )

            self._active_versions[skill_id] = version
            logger.info(
                "Active version for '%s' changed: %s → %s",
                skill_id, old_active_ver or "(none)", version,
            )

    # ── Mutation ──

    def register(self, skill: InstalledSkill) -> None:
        """Register an installed skill.

        - If this is the first version for this skill_id, it automatically
          becomes the active version.
        - If other versions already exist, the active version is unchanged.
        - If this exact (skill_id, version) is already registered, raises
          SkillRegistryError.

        Raises:
            SkillRegistryError: If the same (skill_id, version) already exists.
        """
        with self._lock:
            key = (skill.skill_id, skill.version)
            if key in self._skills:
                existing = self._skills[key]
                raise SkillRegistryError(
                    f"Skill '{skill.skill_id}@{skill.version}' is already registered. "
                    f"Use replace() to update the existing record, or unregister() first. "
                    f"Existing install_path: {existing.install_path}"
                )

            # Auto-enable if this will be the active version (first version for
            # this skill_id, or explicitly being set as active).
            is_first_version = not any(
                k[0] == skill.skill_id for k in self._skills
            )

            if is_first_version:
                # First version: auto-enable and auto-set as active
                skill_to_store = InstalledSkill(
                    manifest=skill.manifest,
                    install_path=skill.install_path,
                    enabled=True,
                    installed_at=skill.installed_at,
                    source_revision=skill.source_revision,
                    checksum=skill.checksum,
                    health_status=skill.health_status,
                    health_message=skill.health_message,
                )
                self._skills[key] = skill_to_store
                self._active_versions[skill.skill_id] = skill.version
                logger.info(
                    "Registered first version of '%s' @ %s (auto-active).",
                    skill.skill_id, skill.version,
                )
            else:
                # Additional version: store as-is (disabled by default if not active)
                self._skills[key] = skill
                logger.info(
                    "Registered additional version of '%s' @ %s.",
                    skill.skill_id, skill.version,
                )

    def replace(self, skill: InstalledSkill) -> None:
        """Register or replace a skill record for the same (skill_id, version).

        Unlike register(), this silently replaces an existing record.
        Does NOT change the active version.
        """
        with self._lock:
            key = (skill.skill_id, skill.version)
            self._skills[key] = skill

    def unregister(self, skill_id: str, version: str | None = None) -> None:
        """Remove a skill from the registry.

        If version is None, removes ALL versions of this skill_id and clears
        the active version for it.

        If version is specified and it was the active version, the active
        slot is cleared (no auto-selection of another version).
        """
        with self._lock:
            if version is not None:
                key = (skill_id, version)
                if key in self._skills:
                    del self._skills[key]
                # Clear active if we just deleted the active version
                if self._active_versions.get(skill_id) == version:
                    del self._active_versions[skill_id]
                    logger.info(
                        "Unregistered active version '%s@%s' — active slot cleared.",
                        skill_id, version,
                    )
                return

            # Remove all versions
            keys_to_remove = [
                k for k in self._skills if k[0] == skill_id
            ]
            for k in keys_to_remove:
                del self._skills[k]
            # Clear active
            self._active_versions.pop(skill_id, None)

    def set_enabled(
        self, skill_id: str, enabled: bool, version: str | None = None
    ) -> None:
        """Enable or disable a registered skill.

        Rules:
        - If version is None, uses the active version for this skill_id.
          If there is no active version, uses the highest version (legacy
          fallback).
        - Enabling (enabled=True) a non-active version raises
          SkillRegistryError.  Only the active version may be enabled.
        - Disabling (enabled=False) ANY version is always allowed.

        Raises:
            KeyError: If no matching skill is found.
            SkillRegistryError: If attempting to enable a non-active version.
        """
        with self._lock:
            # Resolve target version
            if version is None:
                active = self._active_versions.get(skill_id)
                if active is not None:
                    version = active
                # else: fall through to get() which picks highest version

            target = self.get(skill_id, version)
            if target is None:
                ver_info = f"@{version}" if version else ""
                raise KeyError(
                    f"Skill '{skill_id}{ver_info}' not found in registry"
                )

            # Rule: non-active versions cannot be enabled
            if enabled:
                active_ver = self._active_versions.get(skill_id)
                if active_ver is None or target.version != active_ver:
                    raise SkillRegistryError(
                        f"Cannot enable '{skill_id}@{target.version}': "
                        f"it is not the active version. "
                        f"Active version: {active_ver or '(none)'}. "
                        f"Use set_active_version() to change the active version first."
                    )

            # Create a new InstalledSkill with toggled enabled (frozen dataclass)
            updated = InstalledSkill(
                manifest=target.manifest,
                install_path=target.install_path,
                enabled=enabled,
                installed_at=target.installed_at,
                source_revision=target.source_revision,
                checksum=target.checksum,
                health_status=target.health_status,
                health_message=target.health_message,
            )
            key = (target.skill_id, target.version)
            self._skills[key] = updated

    def update_health_status(
        self,
        skill_id: str,
        version: str,
        status: str,
        message: str | None = None,
    ) -> None:
        """Update the health status of a registered skill.

        This is a targeted mutation — it only changes health_status and
        health_message.  enabled, active_version, and other fields are
        NOT affected.

        Uses snapshot/restore for transaction safety: if the update
        succeeds in memory but save() fails, the caller should restore
        the snapshot (but we don't auto-restore here — the caller
        controls persistence).

        Rules:
        - status must be a valid HealthStatus value.
        - Does NOT auto-enable or auto-activate the skill.
        - Does NOT change the active version.

        Raises:
            KeyError: If the (skill_id, version) is not registered.
            ValueError: If status is not a valid health status.
        """
        with self._lock:
            key = (skill_id, version)
            if key not in self._skills:
                raise KeyError(
                    f"Skill '{skill_id}@{version}' not found in registry"
                )

            target = self._skills[key]

            # Validate status (delegates to InstalledSkill.__post_init__)
            # We construct a temporary to trigger validation before mutating
            updated = InstalledSkill(
                manifest=target.manifest,
                install_path=target.install_path,
                enabled=target.enabled,
                installed_at=target.installed_at,
                source_revision=target.source_revision,
                checksum=target.checksum,
                health_status=status,
                health_message=message,
            )
            self._skills[key] = updated
            logger.info(
                "Health status updated for '%s@%s': %s → %s",
                skill_id, version, target.health_status, status,
            )

    # ── Transaction support: snapshot / restore ──

    def snapshot(self) -> SkillRegistrySnapshot:
        """Create a defensive snapshot of the current in-memory state.

        The snapshot is a deep copy — modifying the original registry
        does not affect a stored snapshot.

        Returns:
            SkillRegistrySnapshot frozen dataclass.
        """
        with self._lock:
            skills_copy: dict[tuple[str, str], InstalledSkill] = {}
            for key, skill in self._skills.items():
                skills_copy[key] = InstalledSkill(
                    manifest=skill.manifest,
                    install_path=skill.install_path,
                    enabled=skill.enabled,
                    installed_at=skill.installed_at,
                    source_revision=skill.source_revision,
                    checksum=skill.checksum,
                    health_status=skill.health_status,
                    health_message=skill.health_message,
                )
            active_copy = dict(self._active_versions)
            return SkillRegistrySnapshot(
                skills=skills_copy,
                active_versions=active_copy,
            )

    def restore(self, snapshot: SkillRegistrySnapshot) -> None:
        """Restore the registry to a previously captured snapshot.

        Replaces all in-memory state with the snapshot contents.
        Does NOT automatically save — the caller must call save()
        if persistence is desired.

        Args:
            snapshot: A SkillRegistrySnapshot previously returned by snapshot().
        """
        with self._lock:
            self._skills = dict(snapshot.skills)
            self._active_versions = dict(snapshot.active_versions)
            logger.info(
                "Registry restored from snapshot: %d skills",
                len(self._skills),
            )

    def validate_installed_paths(
        self,
        installed_dir: Path,
    ) -> tuple[SkillRegistryConsistencyIssue, ...]:
        """Validate that all registered install_paths are consistent.

        Checks:
        - install_path is absolute
        - install_path is within installed_dir
        - Directory exists
        - Directory name matches skill_id/version
        - No symlinks or special files in the installed directory
        - (Checksum verification is deferred — expensive)

        Returns a tuple of consistency issues found. Empty tuple = all OK.
        Only reports — never silently deletes or modifies.
        """
        import os as _os
        import stat as _stat

        issues: list[SkillRegistryConsistencyIssue] = []
        installed_dir = installed_dir.resolve()

        with self._lock:
            for (sid, ver), skill in self._skills.items():
                ip_str = skill.install_path
                ip = Path(ip_str)

                # Check absolute
                if not ip.is_absolute():
                    issues.append(SkillRegistryConsistencyIssue(
                        severity="error",
                        code="NON_ABSOLUTE_PATH",
                        skill_id=sid,
                        version=ver,
                        path=ip_str,
                        message=f"install_path is not absolute: {ip_str}",
                    ))
                    continue

                # Check within installed_dir
                try:
                    ip.resolve().relative_to(installed_dir)
                except ValueError:
                    issues.append(SkillRegistryConsistencyIssue(
                        severity="error",
                        code="PATH_OUTSIDE_MANAGED",
                        skill_id=sid,
                        version=ver,
                        path=ip_str,
                        message=f"install_path outside managed dir: {ip_str}",
                    ))
                    continue

                # Check directory exists
                if not ip.exists():
                    issues.append(SkillRegistryConsistencyIssue(
                        severity="blocking",
                        code="DIRECTORY_MISSING",
                        skill_id=sid,
                        version=ver,
                        path=str(ip),
                        message=f"install directory does not exist: {ip}",
                    ))
                    continue

                if not ip.is_dir():
                    issues.append(SkillRegistryConsistencyIssue(
                        severity="blocking",
                        code="NOT_A_DIRECTORY",
                        skill_id=sid,
                        version=ver,
                        path=str(ip),
                        message=f"install_path is not a directory: {ip}",
                    ))
                    continue

                # Check directory name
                expected_name = ver
                if ip.name != expected_name:
                    issues.append(SkillRegistryConsistencyIssue(
                        severity="error",
                        code="DIRECTORY_NAME_MISMATCH",
                        skill_id=sid,
                        version=ver,
                        path=str(ip),
                        message=f"Expected dir name '{expected_name}', got '{ip.name}'",
                    ))

                # Check parent dir name
                expected_parent = sid
                if ip.parent.name != expected_parent:
                    issues.append(SkillRegistryConsistencyIssue(
                        severity="error",
                        code="PARENT_NAME_MISMATCH",
                        skill_id=sid,
                        version=ver,
                        path=str(ip),
                        message=f"Expected parent '{expected_parent}', got '{ip.parent.name}'",
                    ))

                # Quick scan for symlinks / special files
                try:
                    _scan_for_forbidden_entries(ip, sid, ver, issues)
                except OSError:
                    issues.append(SkillRegistryConsistencyIssue(
                        severity="warning",
                        code="SCAN_ERROR",
                        skill_id=sid,
                        version=ver,
                        path=str(ip),
                        message=f"Failed to scan directory: {ip}",
                    ))

        return tuple(issues)

    def validate_installation_consistency(
        self,
        installed_dir: Path,
        *,
        mode: ConsistencyCheckMode = ConsistencyCheckMode.FULL,
        verify_checksums: bool | None = None,
    ) -> tuple[SkillRegistryConsistencyIssue, ...]:
        """Comprehensive installation consistency check (Batch 2.3).

        Checks ALL 18 items:
        1. install_path is absolute
        2. install_path is within installed_dir
        3. Directory exists
        4. Directory path structure: <installed_dir>/<skill_id>/<version>
        5. SKILL.md exists
        6. Manifest can be parsed
        7. Manifest skill_id matches Registry
        8. Manifest version matches Registry
        9. All entrypoints exist and are within package
        10. No symlinks in package
        11. No FIFO/socket/device files
        12. Package checksum matches Registry checksum (FULL mode only)
        13. Registry checksum is non-empty (FULL mode only)
        14. active_versions point to existing records
        15. Each skill_id has at most one active version
        16. Only active version is enabled=True
        17. Non-active versions with enabled=True → error
        18. Active version directory damaged → blocking

        Args:
            installed_dir: The managed installed/ directory root.
            mode: ConsistencyCheckMode.FULL (default) or .FAST.
                FAST skips file content SHA-256 recomputation and always
                emits CHECKSUM_NOT_VERIFIED warning per skill.
                Future Runtime MUST only accept FULL with no blocking issues.
            verify_checksums: Deprecated. Use mode instead.
                If set, overrides the mode-based checksum behavior.

        Returns tuple of issues. Empty = all OK.
        Only reports — never silently deletes or modifies.
        """
        # Resolve checksum verification policy
        if verify_checksums is not None:
            _do_checksums = verify_checksums
        else:
            _do_checksums = (mode == ConsistencyCheckMode.FULL)

        issues: list[SkillRegistryConsistencyIssue] = []

        # ── Run basic path validation first ──
        path_issues = self.validate_installed_paths(installed_dir)
        issues.extend(path_issues)

        installed_dir = installed_dir.resolve()

        # ── Lazy imports ──
        from dp_engine.skills.manifest_parser import parse_skill_manifest

        with self._lock:
            # ── Per-skill detailed checks ──
            for (sid, ver), skill in self._skills.items():
                ip = Path(skill.install_path)

                # Skip if path checks already flagged blocking issues
                path_blocked = any(
                    iss.severity == "blocking" and iss.skill_id == sid and iss.version == ver
                    for iss in issues
                )
                if path_blocked:
                    continue

                # 5. SKILL.md exists
                skill_md = ip / "SKILL.md"
                if not skill_md.is_file():
                    issues.append(SkillRegistryConsistencyIssue(
                        severity="blocking",
                        code="SKILL_MD_MISSING",
                        skill_id=sid,
                        version=ver,
                        path=str(ip),
                        message="SKILL.md not found in installed directory",
                    ))
                    continue

                # 6. Manifest can be parsed
                try:
                    manifest = parse_skill_manifest(ip)
                except Exception as e:
                    error_msg = str(e)
                    # parse_skill_manifest validates entrypoint existence
                    # (filesystem check).  If it fails because an entrypoint
                    # file is missing, emit a structured ENTRYPOINT_MISSING
                    # issue so callers can programmatically detect it.
                    if "Entrypoint" in error_msg and "not found" in error_msg:
                        issues.append(SkillRegistryConsistencyIssue(
                            severity="blocking",
                            code="ENTRYPOINT_MISSING",
                            skill_id=sid,
                            version=ver,
                            path=str(ip),
                            message=error_msg,
                        ))
                    issues.append(SkillRegistryConsistencyIssue(
                        severity="blocking",
                        code="MANIFEST_PARSE_FAILED",
                        skill_id=sid,
                        version=ver,
                        path=str(ip),
                        message=f"Cannot parse SKILL.md manifest: {e}",
                    ))
                    continue

                # 7. Manifest skill_id matches Registry
                m_skill_id = getattr(manifest, 'skill_id', '')
                if m_skill_id and m_skill_id != sid:
                    issues.append(SkillRegistryConsistencyIssue(
                        severity="blocking",
                        code="MANIFEST_ID_MISMATCH",
                        skill_id=sid,
                        version=ver,
                        path=str(ip),
                        message=(
                            f"Manifest skill_id '{m_skill_id}' does not match "
                            f"Registry skill_id '{sid}'"
                        ),
                    ))

                # 8. Manifest version matches Registry
                m_version = getattr(manifest, 'version', '')
                if m_version and m_version != ver:
                    issues.append(SkillRegistryConsistencyIssue(
                        severity="blocking",
                        code="MANIFEST_VERSION_MISMATCH",
                        skill_id=sid,
                        version=ver,
                        path=str(ip),
                        message=(
                            f"Manifest version '{m_version}' does not match "
                            f"Registry version '{ver}'"
                        ),
                    ))

                # 9. All entrypoints exist and are within package
                # Entrypoint values support "module.py:function" format —
                # extract only the module path for filesystem checks.
                entrypoints = getattr(manifest, 'entrypoints', {}) or {}
                for ep_key, ep_rel_path in entrypoints.items():
                    _module_path = _extract_module_path(ep_rel_path)
                    ep_file = ip / _module_path
                    if not ep_file.is_file():
                        issues.append(SkillRegistryConsistencyIssue(
                            severity="blocking",
                            code="ENTRYPOINT_MISSING",
                            skill_id=sid,
                            version=ver,
                            path=str(ep_file),
                            message=f"Entrypoint '{ep_key}' not found: {ep_rel_path}",
                        ))
                    else:
                        # Verify entrypoint is within package
                        try:
                            ep_file.resolve().relative_to(ip.resolve())
                        except ValueError:
                            issues.append(SkillRegistryConsistencyIssue(
                                severity="blocking",
                                code="ENTRYPOINT_OUTSIDE_PACKAGE",
                                skill_id=sid,
                                version=ver,
                                path=str(ep_file),
                                message=(
                                    f"Entrypoint '{ep_key}' resolves outside package: "
                                    f"{ep_rel_path}"
                                ),
                            ))

                # 12-13. Checksum verification (FULL mode only)
                if _do_checksums:
                    reg_checksum = skill.checksum or ""

                    # 13. Registry checksum non-empty
                    if not reg_checksum:
                        issues.append(SkillRegistryConsistencyIssue(
                            severity="error",
                            code="CHECKSUM_EMPTY",
                            skill_id=sid,
                            version=ver,
                            path=str(ip),
                            message="Registry checksum is empty",
                        ))
                    else:
                        # 12. Recompute and compare
                        try:
                            from dp_engine.skills.package_validator import SkillPackageValidator
                            validator = SkillPackageValidator()
                            validation = validator.validate(ip)
                            pkg_checksum = validation.package_checksum or ""
                            if pkg_checksum and pkg_checksum != reg_checksum:
                                issues.append(SkillRegistryConsistencyIssue(
                                    severity="blocking",
                                    code="CHECKSUM_MISMATCH",
                                    skill_id=sid,
                                    version=ver,
                                    path=str(ip),
                                    message=(
                                        f"Package checksum mismatch: "
                                        f"registry={reg_checksum[:16]}... "
                                        f"actual={pkg_checksum[:16]}..."
                                    ),
                                ))
                        except Exception as e:
                            issues.append(SkillRegistryConsistencyIssue(
                                severity="warning",
                                code="CHECKSUM_VERIFY_FAILED",
                                skill_id=sid,
                                version=ver,
                                path=str(ip),
                                message=f"Failed to verify checksum: {e}",
                            ))
                else:
                    # FAST mode: skip checksum recomputation, emit warning
                    issues.append(SkillRegistryConsistencyIssue(
                        severity="warning",
                        code="CHECKSUM_NOT_VERIFIED",
                        skill_id=sid,
                        version=ver,
                        path=str(ip),
                        message=(
                            "FAST mode: package checksum not verified. "
                            "Skill is NOT fully trusted. "
                            "Runtime must use FULL consistency check."
                        ),
                    ))

            # ── Active version checks (14-18) ──
            # Collect all skill_ids
            all_skill_ids: set[str] = {k[0] for k in self._skills}

            # 14. active_versions point to existing records
            for act_sid, act_ver in self._active_versions.items():
                if (act_sid, act_ver) not in self._skills:
                    issues.append(SkillRegistryConsistencyIssue(
                        severity="blocking",
                        code="ACTIVE_VERSION_NOT_INSTALLED",
                        skill_id=act_sid,
                        version=act_ver,
                        message=(
                            f"Active version '{act_sid}@{act_ver}' references "
                            f"a non-existent skill"
                        ),
                    ))

            # 15. Each skill_id has at most one active version
            # (already enforced by dict structure, but verify)
            skill_active_count: dict[str, int] = {}
            for act_sid in self._active_versions:
                skill_active_count[act_sid] = skill_active_count.get(act_sid, 0) + 1
            for act_sid, count in skill_active_count.items():
                if count > 1:
                    issues.append(SkillRegistryConsistencyIssue(
                        severity="blocking",
                        code="MULTIPLE_ACTIVE_VERSIONS",
                        skill_id=act_sid,
                        message=f"Skill has {count} active versions (expected 1)",
                    ))

            # 16-17. Only active version may be enabled
            for (sid, ver), skill in self._skills.items():
                active_ver = self._active_versions.get(sid)
                is_active = (active_ver == ver)

                if skill.enabled and not is_active:
                    issues.append(SkillRegistryConsistencyIssue(
                        severity="error",
                        code="NON_ACTIVE_ENABLED",
                        skill_id=sid,
                        version=ver,
                        path=skill.install_path,
                        message=(
                            f"Non-active version is enabled=True. "
                            f"Active version: {active_ver or '(none)'}"
                        ),
                    ))

            # 18. Active version directory damaged → blocking
            for act_sid, act_ver in self._active_versions.items():
                skill = self._skills.get((act_sid, act_ver))
                if skill is None:
                    continue
                ip = Path(skill.install_path)
                if not ip.exists() or not ip.is_dir():
                    issues.append(SkillRegistryConsistencyIssue(
                        severity="blocking",
                        code="ACTIVE_VERSION_DIR_DAMAGED",
                        skill_id=act_sid,
                        version=act_ver,
                        path=str(ip),
                        message="Active version directory is missing or damaged",
                    ))

        # ── Batch 2.3: FAST mode summary warning ──
        if mode == ConsistencyCheckMode.FAST:
            issues.append(SkillRegistryConsistencyIssue(
                severity="warning",
                code="FAST_MODE_SUMMARY",
                message=(
                    "Consistency check ran in FAST mode — checksums were NOT verified. "
                    "Runtime MUST use FULL consistency check with no blocking issues "
                    "before executing any skill."
                ),
            ))

        return tuple(issues)

    # ── Convenience ──

    def __len__(self) -> int:
        with self._lock:
            return len(self._skills)

    def __contains__(self, skill_id: str) -> bool:
        with self._lock:
            return any(k[0] == skill_id for k in self._skills)


# ── Snapshot data class ──


@dataclass(frozen=True)
class SkillRegistrySnapshot:
    """Immutable snapshot of SkillRegistry state.

    Contains defensive copies of skills dict and active_versions dict.
    Used for transaction rollback in case of save failure.
    """
    skills: dict[tuple[str, str], InstalledSkill]
    active_versions: dict[str, str]


# ── Consistency issue data class ──


@dataclass(frozen=True)
class SkillRegistryConsistencyIssue:
    """A single consistency issue found during registry validation.

    Batch 2.2: Extended with severity and code for structured reporting.

    Severity levels:
    - warning:  minor issue, skill still usable
    - error:    significant issue, skill should be treated with caution
    - blocking: skill must not be executed, UI must show "inconsistent, execution forbidden"

    Only reports — never silently deletes or modifies.
    """

    severity: str  # "warning", "error", "blocking"
    code: str       # stable machine-readable code e.g. "MANIFEST_ID_MISMATCH"
    skill_id: str | None = None
    version: str | None = None
    path: str | None = None
    message: str = ""

    # Backward-compat aliases for code that used the old fields
    @property
    def issue_type(self) -> str:
        return self.code

    @property
    def detail(self) -> str:
        return self.message


def _extract_module_path(ep_value: str) -> str:
    """Extract the module file path from an entrypoint value.

    Handles both formats:
    - "path/to/module.py" → "path/to/module.py"
    - "path/to/module.py:function_name" → "path/to/module.py"
    """
    if ":" in ep_value:
        idx = ep_value.rindex(":")
        return ep_value[:idx]
    return ep_value


def _scan_for_forbidden_entries(
    directory: Path,
    skill_id: str,
    version: str,
    issues: list[SkillRegistryConsistencyIssue],
) -> None:
    """Quick scan of an installed skill directory for symlinks/special files."""
    import os as _os
    import stat as _stat

    try:
        with _os.scandir(str(directory)) as entries:
            for entry in entries:
                if entry.is_symlink():
                    issues.append(SkillRegistryConsistencyIssue(
                        severity="blocking",
                        code="SYMLINK_FOUND",
                        skill_id=skill_id,
                        version=version,
                        path=entry.path,
                        message=f"Symlink in installed skill: {entry.path}",
                    ))
                    continue

                try:
                    st = entry.stat(follow_symlinks=False)
                except OSError:
                    continue

                mode = st.st_mode
                if _stat.S_ISFIFO(mode):
                    issues.append(SkillRegistryConsistencyIssue(
                        severity="blocking",
                        code="FIFO_FOUND",
                        skill_id=skill_id, version=version,
                        path=entry.path,
                        message=f"FIFO in installed skill: {entry.path}",
                    ))
                elif _stat.S_ISSOCK(mode):
                    issues.append(SkillRegistryConsistencyIssue(
                        severity="blocking",
                        code="SOCKET_FOUND",
                        skill_id=skill_id, version=version,
                        path=entry.path,
                        message=f"Socket in installed skill: {entry.path}",
                    ))
                elif _stat.S_ISBLK(mode) or _stat.S_ISCHR(mode):
                    issues.append(SkillRegistryConsistencyIssue(
                        severity="blocking",
                        code="DEVICE_FOUND",
                        skill_id=skill_id, version=version,
                        path=entry.path,
                        message=f"Device file in installed skill: {entry.path}",
                    ))
    except OSError:
        pass
