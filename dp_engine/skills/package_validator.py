"""Skill package validator — integrity, manifest, and security checks.

SkillPackageValidator.validate() performs all checks on a skill root
directory (after extraction or copy to staging).  It does NOT:
- Execute any code from the skill
- Import any Python modules from the skill
- Run healthcheck scripts
- Install dependencies
- Make network calls
"""

from __future__ import annotations

import hashlib
import logging
from pathlib import Path
from typing import TYPE_CHECKING, Callable

if TYPE_CHECKING:
    from dp_engine.skills.package_models import SkillPackageLimits

from dp_engine.skills.errors import (
    SkillManifestError,
    SkillPackageError,
    SkillPackageLimitError,
    SkillPackageSecurityError,
)
from dp_engine.skills.manifest_parser import parse_skill_manifest
from dp_engine.skills.models import SkillManifest, parse_entrypoint_ref
from dp_engine.skills.package_models import (
    SkillPackageFile,
    SkillPackageLimits,
    SkillPackageValidationResult,
    get_default_limits,
)

logger = logging.getLogger(__name__)


class SkillPackageValidator:
    """Validates a skill package directory for integrity and security.

    Usage:
        validator = SkillPackageValidator()
        result = validator.validate(skill_root)
        if result.valid:
            print(f"Package OK: {result.package_checksum}")
    """

    def __init__(self, limits: SkillPackageLimits | None = None) -> None:
        self._limits = limits or get_default_limits()

    def validate(
        self,
        skill_root: Path,
        archive_sha256: str | None = None,
        cancel_check: Callable[[], bool] | None = None,
    ) -> SkillPackageValidationResult:
        """Validate a skill package directory.

        Steps:
        1. Verify skill_root exists and is a directory
        2. Parse SKILL.md manifest
        3. Verify all entrypoints exist within skill_root
        4. Inventory all files with SHA-256
        5. Compute stable package checksum
        6. Check resource limits

        Args:
            skill_root: Path to the skill root directory.
            archive_sha256: SHA-256 of the source archive (if applicable).
            cancel_check: Optional cancel callback.

        Returns:
            SkillPackageValidationResult with all fields populated.
        """
        errors: list[str] = []
        warnings: list[str] = []

        skill_root = skill_root.resolve()

        # Step 1: Verify root
        if not skill_root.exists():
            errors.append(f"Skill root does not exist: {skill_root}")
            return SkillPackageValidationResult(
                valid=False,
                errors=tuple(errors),
            )
        if not skill_root.is_dir():
            errors.append(f"Skill root is not a directory: {skill_root}")
            return SkillPackageValidationResult(
                valid=False,
                errors=tuple(errors),
            )

        # Step 2: Parse manifest
        try:
            manifest = parse_skill_manifest(skill_root)
        except (SkillManifestError, FileNotFoundError, ValueError) as e:
            errors.append(f"Manifest parsing failed: {e}")
            return SkillPackageValidationResult(
                valid=False,
                errors=tuple(errors),
            )

        if cancel_check and cancel_check():
            return SkillPackageValidationResult(
                valid=False,
                errors=("Validation cancelled by user",),
            )

        # Step 2b: report_backend has a strict install-time semantic contract.
        # Generic instruction/executable manifests remain forward-compatible.
        if manifest.skill_type == "report_backend":
            from dp_engine.report_backend.models import (
                validate_report_backend_manifest,
            )

            for issue in validate_report_backend_manifest(manifest):
                errors.append(
                    f"Report backend manifest {issue.code}: {issue.message}"
                )

        # Step 3: Verify entrypoints exist
        for key, rel_path in manifest.entrypoints.items():
            module_path = rel_path
            if ":" in rel_path:
                try:
                    module_path, _function_name = parse_entrypoint_ref(rel_path)
                except ValueError as exc:
                    errors.append(
                        f"Entrypoint '{key}' is not callable: {exc}"
                    )
                    continue
            entry_file = skill_root / module_path
            if not entry_file.is_file():
                errors.append(
                    f"Entrypoint '{key}' file not found: {module_path}"
                )

        if cancel_check and cancel_check():
            return SkillPackageValidationResult(
                valid=False,
                errors=("Validation cancelled by user",),
            )

        # Step 4: Inventory files
        try:
            files = self._inventory_files(skill_root, cancel_check)
        except (SkillPackageLimitError, SkillPackageSecurityError) as e:
            errors.append(str(e))
            return SkillPackageValidationResult(
                valid=False,
                manifest=manifest,
                skill_root=str(skill_root),
                errors=tuple(errors),
            )

        if cancel_check and cancel_check():
            return SkillPackageValidationResult(
                valid=False,
                errors=("Validation cancelled by user",),
            )

        # Step 5: Compute package checksum
        package_checksum = self._compute_package_checksum(files)

        # Step 6: Collect stats
        total_files = len(files)
        total_size = sum(f.size_bytes for f in files if f.file_type != "directory")

        # Collect warnings from manifest extensions
        extension_keys = set(manifest.extensions)
        if manifest.skill_type == "report_backend":
            from dp_engine.report_backend.models import (
                REPORT_BACKEND_EXTENSION_KEYS,
            )

            extension_keys -= REPORT_BACKEND_EXTENSION_KEYS
        if extension_keys:
            ext_keys = ", ".join(sorted(extension_keys))
            warnings.append(f"Unknown manifest fields (preserved): {ext_keys}")

        # Collect warnings for dependencies
        if manifest.dependencies:
            warnings.append(
                f"Package declares dependencies: {', '.join(manifest.dependencies)}. "
                f"Dependencies are NOT installed by this version."
            )

        valid = len(errors) == 0

        return SkillPackageValidationResult(
            valid=valid,
            manifest=manifest,
            skill_root=str(skill_root),
            files=tuple(files),
            total_files=total_files,
            total_size_bytes=total_size,
            package_checksum=package_checksum if valid else None,
            archive_sha256=archive_sha256,
            warnings=tuple(warnings),
            errors=tuple(errors),
        )

    # ── Internal ──

    def _inventory_files(
        self,
        skill_root: Path,
        cancel_check: Callable[[], bool] | None = None,
    ) -> list[SkillPackageFile]:
        """Walk the skill directory and inventory all files.

        Uses os.scandir() with follow_symlinks=False for safe traversal.
        Symlinks are REJECTED as hard security errors — never warnings.
        FIFO, socket, block device, char device also rejected.

        Files are sorted by normalized relative path.

        Raises:
            SkillPackageLimitError: If file count or size limits exceeded.
            SkillPackageSecurityError: If symlinks or special files found.
        """
        result: list[SkillPackageFile] = []
        total_bytes = 0
        seen_inodes: dict[int, str] = {}
        errors: list[str] = []

        _inventory_recursive(
            skill_root=skill_root,
            base_dir=skill_root,
            result=result,
            total_bytes=total_bytes,
            seen_inodes=seen_inodes,
            limits=self._limits,
            cancel_check=cancel_check,
            errors=errors,
            _depth=0,
        )

        if errors:
            raise SkillPackageSecurityError(
                f"Package contains forbidden items: {'; '.join(errors[:5])}"
            )

        # Update total_bytes (was mutated in closure via list wrapping)
        # Actually, we use a different approach: recompute from result
        actual_total = sum(f.size_bytes for f in result if f.file_type != "directory")

        # Check limits
        if len(result) > self._limits.max_file_count:
            raise SkillPackageLimitError(
                f"File count exceeds limit",
                detail={
                    "limit": "max_file_count",
                    "value": len(result),
                },
            )

        if actual_total > self._limits.max_extracted_bytes:
            raise SkillPackageLimitError(
                f"Total extracted size exceeds limit",
                detail={
                    "limit": "max_extracted_bytes",
                    "value": actual_total,
                },
            )

        # Sort by relative path for deterministic output
        result.sort(key=lambda f: f.relative_path)
        return result

    def _compute_package_checksum(
        self,
        files: list[SkillPackageFile],
    ) -> str:
        """Compute a stable package content checksum.

        Based on (relative_path, file_size, file_sha256) for all regular
        files, sorted by relative_path.  Does NOT depend on:
        - Absolute paths
        - Filesystem traversal order
        - File timestamps
        - ZIP compression parameters

        This means two installs from different source formats (directory
        vs ZIP) of the same content produce the same checksum.
        """
        h = hashlib.sha256()
        for f in sorted(files, key=lambda x: x.relative_path):
            if f.file_type == "directory":
                continue  # directories don't contribute to checksum
            if f.file_type == "symlink":
                # Skip symlinks — their content hash is unstable
                continue
            line = f"{f.relative_path}|{f.size_bytes}|{f.sha256}\n"
            h.update(line.encode("utf-8"))
        return h.hexdigest()


# ── Helper ──


def _sha256_file(path: Path) -> str:
    """Compute SHA-256 hex digest of a file."""
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while True:
            chunk = f.read(1024 * 1024)
            if not chunk:
                break
            h.update(chunk)
    return h.hexdigest()


def _inventory_recursive(
    *,
    skill_root: Path,
    base_dir: Path,
    result: list,  # list[SkillPackageFile]
    total_bytes: int,
    seen_inodes: dict[int, str],
    limits: "SkillPackageLimits",
    cancel_check: Callable[[], bool] | None,
    errors: list[str],
    _depth: int = 0,
) -> int:
    """Recursively inventory files in a directory using os.scandir().

    Rejects symlinks, FIFOs, sockets, block/char devices as hard errors.
    Detects hard links via inode tracking.

    Returns updated total_bytes.
    """
    import os as _os
    import stat as _stat

    if _depth > limits.max_directory_depth:
        errors.append(f"Directory depth exceeds limit: {_depth}")
        return total_bytes

    try:
        with _os.scandir(str(skill_root)) as entries:
            for entry in sorted(entries, key=lambda e: e.name):
                if cancel_check and cancel_check():
                    break

                rel_str = Path(entry.path).relative_to(base_dir).as_posix()

                # 1. Symlink → reject
                if entry.is_symlink():
                    errors.append(f"Symlink: {rel_str}")
                    continue

                # 2. Get stat without following symlinks
                try:
                    st = entry.stat(follow_symlinks=False)
                except OSError as e:
                    errors.append(f"Cannot stat: {rel_str} ({e})")
                    continue

                mode = st.st_mode

                # 3. Regular file
                if _stat.S_ISREG(mode):
                    # Hard link detection
                    # NOTE: On Windows, st_ino is always 0, so skip.
                    import sys as _sys
                    inode = st.st_ino if _sys.platform != "win32" else None
                    if inode is not None and inode != 0:
                        if inode in seen_inodes:
                            errors.append(
                                f"Hard link (inode {inode}): {rel_str} "
                                f"shares inode with {seen_inodes[inode]}"
                            )
                            continue
                        seen_inodes[inode] = rel_str

                    fsize = st.st_size
                    if fsize > limits.max_single_file_bytes:
                        errors.append(
                            f"File too large: {rel_str} = {fsize} bytes"
                        )
                        continue

                    sha256 = _sha256_file(Path(entry.path))

                    from dp_engine.skills.package_models import SkillPackageFile
                    result.append(SkillPackageFile(
                        relative_path=rel_str,
                        size_bytes=fsize,
                        sha256=sha256,
                        file_type="regular",
                    ))
                    total_bytes += fsize

                # 4. Directory → recurse
                elif _stat.S_ISDIR(mode):
                    from dp_engine.skills.package_models import SkillPackageFile
                    result.append(SkillPackageFile(
                        relative_path=rel_str,
                        size_bytes=0,
                        sha256="",
                        file_type="directory",
                    ))
                    total_bytes = _inventory_recursive(
                        skill_root=Path(entry.path),
                        base_dir=base_dir,
                        result=result,
                        total_bytes=total_bytes,
                        seen_inodes=seen_inodes,
                        limits=limits,
                        cancel_check=cancel_check,
                        errors=errors,
                        _depth=_depth + 1,
                    )

                # 5. FIFO → reject
                elif _stat.S_ISFIFO(mode):
                    errors.append(f"FIFO (named pipe): {rel_str}")

                # 6. Socket → reject
                elif _stat.S_ISSOCK(mode):
                    errors.append(f"Socket: {rel_str}")

                # 7. Block device → reject
                elif _stat.S_ISBLK(mode):
                    errors.append(f"Block device: {rel_str}")

                # 8. Character device → reject
                elif _stat.S_ISCHR(mode):
                    errors.append(f"Character device: {rel_str}")

                # 9. Unknown → reject
                else:
                    errors.append(f"Unknown file type (mode={mode:o}): {rel_str}")

    except OSError as e:
        errors.append(f"Failed to scan directory: {skill_root} ({e})")

    return total_bytes
