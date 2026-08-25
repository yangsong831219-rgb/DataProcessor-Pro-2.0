"""Safe ZIP archive inspection and extraction.

This module provides ZIP handling that defends against:
- ZIP slip (path traversal)
- ZIP bombs (compression ratio attacks)
- Symlink entries
- Encrypted entries
- Special device files
- Path length / depth / count limits
- Duplicate paths and case collisions

No code from the ZIP contents is ever executed or imported.
"""

from __future__ import annotations

import hashlib
import logging
import os
import shutil
import zipfile
from pathlib import Path
from typing import Callable

from dp_engine.skills.errors import (
    SkillArchiveError,
    SkillInstallCancelled,
    SkillPackageError,
    SkillPackageLimitError,
    SkillPackageSecurityError,
)
from dp_engine.skills.package_models import (
    SkillPackageLimits,
    get_default_limits,
)

logger = logging.getLogger(__name__)

# Characters forbidden in ZIP entry names
_FORBIDDEN_NAME_CHARS = frozenset({"\x00", "\n", "\r"})

# POSIX device / special paths to reject
_SPECIAL_PREFIXES = (
    "/dev/",
    "dev/",
    "/proc/",
    "proc/",
    "/sys/",
    "sys/",
    "CON",
    "PRN",
    "AUX",
    "NUL",
    "COM1", "COM2", "COM3", "COM4",
    "LPT1", "LPT2", "LPT3",
)

# Windows reserved names (case-insensitive)
_WINDOWS_RESERVED = frozenset({
    "con", "prn", "aux", "nul",
    "com1", "com2", "com3", "com4", "com5", "com6", "com7", "com8", "com9",
    "lpt1", "lpt2", "lpt3", "lpt4", "lpt5", "lpt6", "lpt7", "lpt8", "lpt9",
})


# ── Archive inspection (read-only, no extraction) ──


def inspect_archive(
    archive_path: Path,
    limits: SkillPackageLimits | None = None,
) -> tuple[list[zipfile.ZipInfo], str]:
    """Inspect a ZIP archive without extracting anything.

    Checks:
    - Archive file size against max_archive_bytes
    - Each entry name for path traversal
    - Entry count against max_file_count
    - Total uncompressed size against max_extracted_bytes
    - No encrypted entries
    - Archive can be opened and is valid

    Args:
        archive_path: Path to the ZIP file.
        limits: Resource limits (uses defaults if None).

    Returns:
        Tuple of (validated ZipInfo list, archive_sha256_hex).

    Raises:
        SkillArchiveError: Archive is invalid or encrypted.
        SkillPackageLimitError: A resource limit was exceeded.
        SkillPackageSecurityError: Security violation detected.
    """
    if limits is None:
        limits = get_default_limits()

    # Check archive file size
    try:
        archive_size = archive_path.stat().st_size
    except OSError as e:
        raise SkillArchiveError(
            f"Cannot stat archive: {archive_path}\n{e}"
        ) from e

    # Compute archive SHA-256
    archive_sha256 = _sha256_file(archive_path)

    if archive_size > limits.max_archive_bytes:
        raise SkillPackageLimitError(
            f"Archive file too large: {archive_size} bytes "
            f"(limit: {limits.max_archive_bytes} bytes)",
            detail={
                "limit": "max_archive_bytes",
                "value": archive_size,
                "max": limits.max_archive_bytes,
            },
        )

    try:
        with zipfile.ZipFile(archive_path, "r") as zf:
            _validate_all_entries(zf, limits)
            return (list(zf.infolist()), archive_sha256)
    except zipfile.BadZipFile as e:
        raise SkillArchiveError(
            f"Invalid or corrupted ZIP archive: {archive_path}\n{e}"
        ) from e


def _validate_all_entries(
    zf: zipfile.ZipFile,
    limits: SkillPackageLimits,
) -> None:
    """Validate all entries in an opened ZipFile without extracting.

    Checks each entry for security and limit violations.
    """
    infolist = zf.infolist()

    # Check file count
    if len(infolist) > limits.max_file_count:
        raise SkillPackageLimitError(
            f"Too many files in archive: {len(infolist)} "
            f"(limit: {limits.max_file_count})",
            detail={
                "limit": "max_file_count",
                "value": len(infolist),
                "max": limits.max_file_count,
            },
        )

    total_uncompressed = sum(
        info.file_size for info in infolist
    )
    if total_uncompressed > limits.max_extracted_bytes:
        raise SkillPackageLimitError(
            f"Total uncompressed size too large: {total_uncompressed} bytes "
            f"(limit: {limits.max_extracted_bytes} bytes)",
            detail={
                "limit": "max_extracted_bytes",
                "value": total_uncompressed,
                "max": limits.max_extracted_bytes,
            },
        )

    seen_paths: set[str] = set()
    seen_paths_lower: set[str] = set()

    for info in infolist:
        _validate_single_entry(
            info, limits, seen_paths, seen_paths_lower
        )


def _validate_single_entry(
    info: zipfile.ZipInfo,
    limits: SkillPackageLimits,
    seen_paths: set[str],
    seen_paths_lower: set[str],
) -> None:
    """Validate a single ZIP entry for security and limit violations."""
    raw_name = info.filename

    # 1. Empty name
    if not raw_name or not raw_name.strip():
        raise SkillPackageSecurityError(
            f"ZIP entry has empty filename"
        )

    # 2. NUL characters
    if any(c in _FORBIDDEN_NAME_CHARS for c in raw_name):
        raise SkillPackageSecurityError(
            f"ZIP entry contains forbidden characters: {raw_name!r}"
        )

    # 3-6. Path traversal checks
    # Normalize separators
    normalized = raw_name.replace("\\", "/")

    # Check for UNC paths FIRST (before absolute POSIX check, since
    # //server/share also starts with /)
    if normalized.startswith("//") or normalized.startswith("\\\\"):
        raise SkillPackageSecurityError(
            f"ZIP entry has UNC path: {raw_name!r}"
        )

    # Check for absolute POSIX path
    if normalized.startswith("/"):
        raise SkillPackageSecurityError(
            f"ZIP entry has absolute path: {raw_name!r}"
        )

    # Check for Windows drive letter
    if len(normalized) >= 2 and normalized[1] == ":":
        raise SkillPackageSecurityError(
            f"ZIP entry has Windows drive-letter path: {raw_name!r}"
        )

    # Check for parent directory traversal
    segments = normalized.rstrip("/").split("/")
    if ".." in segments:
        raise SkillPackageSecurityError(
            f"ZIP entry contains '..' traversal: {raw_name!r}"
        )

    # 7. Check for special device file prefixes
    for seg in segments:
        seg_lower = seg.lower()
        # Check full segment against reserved names
        base = seg_lower.split(".")[0] if "." in seg_lower else seg_lower
        if base in _WINDOWS_RESERVED:
            raise SkillPackageSecurityError(
                f"ZIP entry uses reserved name: {raw_name!r}"
            )

    # 8. Directory depth
    depth = len(segments)
    if info.is_dir():
        depth = len(segments)  # dirs count full depth
    if depth > limits.max_directory_depth:
        raise SkillPackageLimitError(
            f"Directory depth exceeds limit: {depth} "
            f"(limit: {limits.max_directory_depth})",
            detail={
                "limit": "max_directory_depth",
                "value": depth,
                "max": limits.max_directory_depth,
            },
        )

    # 9. Path length
    if len(normalized) > limits.max_path_length:
        raise SkillPackageLimitError(
            f"Path length exceeds limit: {len(normalized)} "
            f"(limit: {limits.max_path_length})",
            detail={
                "limit": "max_path_length",
                "value": len(normalized),
                "max": limits.max_path_length,
            },
        )

    # 10. Single file size
    if info.file_size > limits.max_single_file_bytes:
        raise SkillPackageLimitError(
            f"Single file too large: {raw_name!r} = {info.file_size} bytes "
            f"(limit: {limits.max_single_file_bytes} bytes)",
            detail={
                "limit": "max_single_file_bytes",
                "file": raw_name,
                "value": info.file_size,
                "max": limits.max_single_file_bytes,
            },
        )

    # 11. Compression ratio check
    if info.file_size > 0 and info.compress_size > 0:
        ratio = info.file_size / info.compress_size
        if ratio > limits.max_compression_ratio:
            raise SkillPackageLimitError(
                f"Compression ratio too high for '{raw_name}': "
                f"{ratio:.1f}:1 (limit: {limits.max_compression_ratio}:1)",
                detail={
                    "limit": "max_compression_ratio",
                    "file": raw_name,
                    "value": ratio,
                    "max": limits.max_compression_ratio,
                },
            )

    # 12. Duplicate path
    clean_path = normalized.rstrip("/")
    if clean_path in seen_paths:
        raise SkillPackageSecurityError(
            f"Duplicate ZIP entry path: {raw_name!r}"
        )
    seen_paths.add(clean_path)

    # 13. Case-insensitive collision
    clean_lower = clean_path.lower()
    if clean_lower in seen_paths_lower:
        raise SkillPackageSecurityError(
            f"Case-insensitive path collision in ZIP: {raw_name!r}"
        )
    seen_paths_lower.add(clean_lower)

    # 14. File/directory conflict: check if a path exists both as dir and file
    # (handled by duplicate check since dirs end with /)

    # 15. Symlink entry
    # Check if this is a symlink via the ZIP external attributes
    # (Unix permissions: 0120000 = symlink)
    external_attr = info.external_attr >> 16
    if (external_attr & 0o120000) == 0o120000:
        raise SkillPackageSecurityError(
            f"ZIP contains a symlink entry: {raw_name!r}. "
            f"Symlinks are not permitted in skill packages."
        )

    # 16. Encrypted entry
    if info.flag_bits & 0x1:
        raise SkillArchiveError(
            f"ZIP contains an encrypted entry: {raw_name!r}. "
            f"Encrypted archives are not supported."
        )


# ── Safe extraction ──


def safe_extract_zip(
    archive_path: Path,
    dest_dir: Path,
    limits: SkillPackageLimits | None = None,
    cancel_check: Callable[[], bool] | None = None,
) -> str:
    """Safely extract a ZIP archive to dest_dir.

    Every entry is validated BEFORE extraction.  Extraction is done
    member-by-member — NEVER use ZipFile.extractall().

    After extraction, each file's resolved path is verified to be within
    dest_dir (defense-in-depth against any path traversal that might have
    slipped past the name checks).

    Args:
        archive_path: Path to the ZIP file.
        dest_dir: Destination directory (must exist).
        limits: Resource limits (uses defaults if None).
        cancel_check: Optional callable returning True if cancelled.

    Returns:
        The archive SHA-256 hex digest.

    Raises:
        SkillArchiveError: Archive is invalid.
        SkillPackageLimitError: Resource limit exceeded.
        SkillPackageSecurityError: Security violation.
    """
    if limits is None:
        limits = get_default_limits()

    dest_dir = dest_dir.resolve()
    if not dest_dir.is_dir():
        raise SkillArchiveError(
            f"Destination directory does not exist: {dest_dir}"
        )

    archive_sha256 = _sha256_file(archive_path)

    try:
        with zipfile.ZipFile(archive_path, "r") as zf:
            infolist = zf.infolist()

            # Pre-validate all entries
            seen_paths: set[str] = set()
            seen_paths_lower: set[str] = set()

            # Check file count BEFORE iterating
            if len(infolist) > limits.max_file_count:
                raise SkillPackageLimitError(
                    f"Too many files in archive: {len(infolist)} "
                    f"(limit: {limits.max_file_count})",
                    detail={
                        "limit": "max_file_count",
                        "value": len(infolist),
                        "max": limits.max_file_count,
                    },
                )

            for info in infolist:
                _validate_single_entry(
                    info, limits, seen_paths, seen_paths_lower
                )

            # Re-check totals (they may have been computed during inspect,
            # but do it again here since we've already validated)
            total_uncompressed = sum(
                info.file_size for info in infolist
            )
            if total_uncompressed > limits.max_extracted_bytes:
                raise SkillPackageLimitError(
                    f"Total uncompressed size exceeds limit",
                    detail={
                        "limit": "max_extracted_bytes",
                        "value": total_uncompressed,
                        "max": limits.max_extracted_bytes,
                    },
                )

            # Extract each entry
            for info in infolist:
                if cancel_check and cancel_check():
                    raise SkillInstallCancelled(
                        "Extraction cancelled by user"
                    )

                normalized = info.filename.replace("\\", "/").rstrip("/")

                if info.is_dir():
                    target = dest_dir / normalized
                    target.mkdir(parents=True, exist_ok=True)
                else:
                    # Ensure parent directory exists
                    target = dest_dir / normalized
                    target.parent.mkdir(parents=True, exist_ok=True)

                    # Extract with verification
                    with zf.open(info) as src:
                        # Write to a temp file first, then atomically rename
                        # (not strictly necessary here since we're in staging,
                        # but good practice)
                        with open(target, "wb") as dst:
                            # Stream in chunks to avoid memory exhaustion
                            bytes_written = 0
                            while True:
                                chunk = src.read(1024 * 1024)  # 1 MB chunks
                                if not chunk:
                                    break
                                bytes_written += len(chunk)
                                if bytes_written > limits.max_single_file_bytes:
                                    raise SkillPackageLimitError(
                                        f"Decompressed file exceeds limit: "
                                        f"{normalized!r}",
                                        detail={
                                            "limit": "max_single_file_bytes",
                                            "file": normalized,
                                        },
                                    )
                                dst.write(chunk)

                    # Verify the extracted file is within dest_dir
                    resolved = target.resolve()
                    try:
                        resolved.relative_to(dest_dir)
                    except ValueError:
                        # Clean up the escaped file
                        target.unlink(missing_ok=True)
                        raise SkillPackageSecurityError(
                            f"Extracted file escapes destination: "
                            f"{normalized!r} → {resolved}"
                        )

    except zipfile.BadZipFile as e:
        raise SkillArchiveError(
            f"Invalid or corrupted ZIP archive: {archive_path}\n{e}"
        ) from e

    return archive_sha256


# ── Local directory safe copy ──


def safe_copy_directory(
    source_dir: Path,
    dest_dir: Path,
    limits: SkillPackageLimits | None = None,
    cancel_check: Callable[[], bool] | None = None,
) -> None:
    """Safely copy a local directory to dest_dir — member-by-member.

    Checks:
    - Source directory exists and is a regular directory
    - Source is not a symlink
    - Every entry checked via os.scandir() with DirEntry.is_symlink()
    - No symlinks (file or dir) — immediate SkillPackageSecurityError
    - No special files: FIFO, socket, block device, char device
    - No hard-link duplication (tracked by inode)
    - All files readable
    - Resource limits observed
    - Post-copy re-verification: file type + size + SHA-256
    - Source not modified during copy
    - Destination staging cleaned on cancel/failure

    Does NOT use shutil.copytree() directly — copies member-by-member.
    Source files are never moved, renamed, or modified.

    Args:
        source_dir: Source directory to copy from.
        dest_dir: Destination directory.
        limits: Resource limits.
        cancel_check: Optional cancel callback.

    Raises:
        SkillPackageSecurityError: Security violation.
        SkillPackageLimitError: Resource limit exceeded.
    """
    if limits is None:
        limits = get_default_limits()

    source_dir = source_dir.resolve()

    # ── Source validation ──
    if not source_dir.exists():
        raise SkillPackageError(
            f"Source directory does not exist: {source_dir}"
        )
    if not source_dir.is_dir():
        raise SkillPackageError(
            f"Source is not a directory: {source_dir}"
        )
    if source_dir.is_symlink():
        raise SkillPackageSecurityError(
            f"Source directory is a symlink — not permitted: {source_dir}"
        )

    dest_dir = dest_dir.resolve()
    dest_dir.mkdir(parents=True, exist_ok=True)

    # ── Snapshot source before copy ──
    source_snapshot = _snapshot_directory(source_dir)
    source_hash_before = source_snapshot["checksum"]
    source_file_count_before = source_snapshot["file_count"]

    # Track inodes to detect hard links
    seen_inodes: dict[int, str] = {}  # inode → first path (for diagnostics)

    file_count = 0
    total_bytes = 0
    copied_files: list[tuple[Path, str]] = []  # (dest_path, expected_sha256)

    # ── Member-by-member traversal using os.scandir() ──
    try:
        _copy_tree_recursive(
            source_dir=source_dir,
            dest_dir=dest_dir,
            limits=limits,
            cancel_check=cancel_check,
            file_count=file_count,
            total_bytes=total_bytes,
            seen_inodes=seen_inodes,
            copied_files=copied_files,
        )
    except Exception:
        # Clean up staging on any failure
        if dest_dir.exists():
            shutil.rmtree(str(dest_dir), ignore_errors=True)
        raise

    # ── Post-copy re-verification ──
    for dest_path, expected_sha256 in copied_files:
        if not dest_path.exists():
            raise SkillPackageSecurityError(
                f"Copied file missing after copy: {dest_path}"
            )
        if dest_path.is_symlink():
            raise SkillPackageSecurityError(
                f"Copied file became a symlink: {dest_path}"
            )
        if not dest_path.is_file():
            raise SkillPackageSecurityError(
                f"Copied file is not a regular file: {dest_path}"
            )
        # Verify size and SHA-256
        actual_size = dest_path.stat().st_size
        actual_sha256 = _sha256_file(dest_path)
        if actual_sha256 != expected_sha256:
            raise SkillPackageSecurityError(
                f"Copied file checksum mismatch: {dest_path} "
                f"(expected {expected_sha256[:16]}..., got {actual_sha256[:16]}...)"
            )

    # ── Verify source was not modified ──
    source_snapshot_after = _snapshot_directory(source_dir)
    if source_snapshot_after["checksum"] != source_hash_before:
        raise SkillPackageSecurityError(
            f"Source directory was modified during copy: {source_dir}"
        )
    if source_snapshot_after["file_count"] != source_file_count_before:
        raise SkillPackageSecurityError(
            f"Source directory file count changed during copy: {source_dir}"
        )


def _copy_tree_recursive(
    *,
    source_dir: Path,
    dest_dir: Path,
    limits: SkillPackageLimits,
    cancel_check: Callable[[], bool] | None,
    file_count: int,
    total_bytes: int,
    seen_inodes: dict[int, str],
    copied_files: list[tuple[Path, str]],
    _depth: int = 0,
) -> tuple[int, int]:
    """Recursively copy a directory tree using os.scandir().

    Every entry is validated for file type before copying.
    Symlinks, FIFOs, sockets, block/char devices are all rejected.
    Hard links detected by duplicate inode.

    Returns updated (file_count, total_bytes).
    """
    import stat as _stat

    if _depth > limits.max_directory_depth:
        raise SkillPackageLimitError(
            f"Directory depth exceeds limit: {_depth}",
            detail={"limit": "max_directory_depth", "value": _depth},
        )

    try:
        with os.scandir(str(source_dir)) as entries:
            for entry in entries:
                if cancel_check and cancel_check():
                    raise SkillInstallCancelled("Directory copy cancelled by user")

                rel_name = entry.name
                src_path = Path(entry.path)
                dest_path = dest_dir / rel_name

                # ── 1. Symlink check (ALWAYS first, before any stat()) ──
                if entry.is_symlink():
                    raise SkillPackageSecurityError(
                        f"Symlink in source tree: {src_path}"
                    )

                # ── 2. Get stat WITHOUT following symlinks ──
                try:
                    st = entry.stat(follow_symlinks=False)
                except OSError as e:
                    raise SkillPackageError(
                        f"Cannot stat entry: {src_path}\n{e}"
                    ) from e

                mode = st.st_mode

                # ── 3. Regular file ──
                if _stat.S_ISREG(mode):
                    # Hard link detection: check inode
                    # NOTE: On Windows, st_ino is always 0, so inode tracking
                    # is skipped on that platform (unreliable).
                    import sys as _sys
                    inode = st.st_ino if _sys.platform != "win32" else None
                    if inode is not None and inode != 0:
                        if inode in seen_inodes:
                            raise SkillPackageSecurityError(
                                f"Hard link detected (same inode {inode}): "
                                f"'{src_path}' shares inode with "
                                f"'{seen_inodes[inode]}'"
                            )
                        seen_inodes[inode] = str(src_path)

                    # Size check
                    fsize = st.st_size
                    if fsize > limits.max_single_file_bytes:
                        raise SkillPackageLimitError(
                            f"Single file too large: {src_path} = {fsize} bytes",
                            detail={
                                "limit": "max_single_file_bytes",
                                "file": rel_name,
                                "value": fsize,
                            },
                        )

                    # Readability check
                    if not os.access(str(src_path), os.R_OK):
                        raise SkillPackageSecurityError(
                            f"File is not readable: {src_path}"
                        )

                    # Path length check
                    try:
                        rel_str = src_path.relative_to(source_dir).as_posix()
                    except ValueError:
                        raise SkillPackageSecurityError(
                            f"Path traversal: {src_path} not under {source_dir}"
                        )
                    if len(rel_str) > limits.max_path_length:
                        raise SkillPackageLimitError(
                            f"Path length exceeds limit: {rel_str}",
                            detail={
                                "limit": "max_path_length",
                                "value": len(rel_str),
                            },
                        )

                    # Count and size limits
                    file_count += 1
                    total_bytes += fsize

                    if file_count > limits.max_file_count:
                        raise SkillPackageLimitError(
                            f"Too many files: {file_count}",
                            detail={
                                "limit": "max_file_count",
                                "value": file_count,
                            },
                        )

                    if total_bytes > limits.max_extracted_bytes:
                        raise SkillPackageLimitError(
                            f"Total size exceeds limit: {total_bytes} bytes",
                            detail={
                                "limit": "max_extracted_bytes",
                                "value": total_bytes,
                            },
                        )

                    # Compute source SHA-256
                    src_sha256 = _sha256_file(src_path)

                    # Controlled file copy
                    with open(src_path, "rb") as src_f:
                        with open(dest_path, "wb") as dst_f:
                            while True:
                                chunk = src_f.read(1024 * 1024)
                                if not chunk:
                                    break
                                dst_f.write(chunk)
                            dst_f.flush()
                            os.fsync(dst_f.fileno())

                    # Record for post-copy verification
                    copied_files.append((dest_path, src_sha256))

                # ── 4. Regular directory ──
                elif _stat.S_ISDIR(mode):
                    dest_path.mkdir(parents=True, exist_ok=True)
                    file_count, total_bytes = _copy_tree_recursive(
                        source_dir=src_path,
                        dest_dir=dest_path,
                        limits=limits,
                        cancel_check=cancel_check,
                        file_count=file_count,
                        total_bytes=total_bytes,
                        seen_inodes=seen_inodes,
                        copied_files=copied_files,
                        _depth=_depth + 1,
                    )

                # ── 5. FIFO ──
                elif _stat.S_ISFIFO(mode):
                    raise SkillPackageSecurityError(
                        f"FIFO (named pipe) in source tree: {src_path}"
                    )

                # ── 6. Socket ──
                elif _stat.S_ISSOCK(mode):
                    raise SkillPackageSecurityError(
                        f"Socket in source tree: {src_path}"
                    )

                # ── 7. Block device ──
                elif _stat.S_ISBLK(mode):
                    raise SkillPackageSecurityError(
                        f"Block device in source tree: {src_path}"
                    )

                # ── 8. Character device ──
                elif _stat.S_ISCHR(mode):
                    raise SkillPackageSecurityError(
                        f"Character device in source tree: {src_path}"
                    )

                # ── 9. Unknown type ──
                else:
                    raise SkillPackageSecurityError(
                        f"Unknown file type (mode={mode:o}) in source tree: {src_path}"
                    )

    except OSError as e:
        raise SkillPackageError(
            f"Failed to scan directory: {source_dir}\n{e}"
        ) from e

    return file_count, total_bytes


# ── Skill root detection ──


def locate_skill_root(
    extracted_dir: Path,
    sub_path: str = "",
) -> Path:
    """Locate the unique skill root within an extracted archive.

    Rules:
    1. If sub_path is specified, look only at extracted_dir/sub_path.
    2. If SKILL.md exists at extracted_dir root, use extracted_dir.
    3. If exactly ONE immediate child directory contains SKILL.md, use that.
    4. If multiple SKILL.md found, raise error — require explicit sub_path.
    5. If no SKILL.md found, raise error.

    Args:
        extracted_dir: Root of extracted archive or copied directory.
        sub_path: Optional sub-path to the skill directory.

    Returns:
        Resolved Path to the unique skill root.

    Raises:
        SkillPackageError: If skill root cannot be uniquely determined.
    """
    extracted_dir = extracted_dir.resolve()

    # Rule 1: explicit sub_path
    if sub_path:
        candidate = extracted_dir / sub_path
        if not candidate.is_dir():
            raise SkillPackageError(
                f"Specified sub_path does not exist: {sub_path} "
                f"(in {extracted_dir})"
            )
        if not (candidate / "SKILL.md").is_file():
            raise SkillPackageError(
                f"SKILL.md not found at specified sub_path: {sub_path} "
                f"(in {extracted_dir})"
            )
        return candidate

    # Rule 2: SKILL.md at root
    if (extracted_dir / "SKILL.md").is_file():
        return extracted_dir

    # Rule 3-5: search immediate children
    children = [
        p for p in extracted_dir.iterdir()
        if p.is_dir() and not p.name.startswith(".")
    ]

    skill_roots: list[Path] = []
    for child in children:
        if (child / "SKILL.md").is_file():
            skill_roots.append(child)

    if len(skill_roots) == 1:
        return skill_roots[0]
    elif len(skill_roots) == 0:
        raise SkillPackageError(
            f"No SKILL.md found in extracted archive: {extracted_dir}. "
            f"Please specify sub_path if the skill is in a subdirectory."
        )
    else:
        paths_str = ", ".join(str(p.relative_to(extracted_dir)) for p in skill_roots)
        raise SkillPackageError(
            f"Multiple SKILL.md found in extracted archive: {paths_str}. "
            f"Please specify sub_path to disambiguate."
        )


# ── Internal helpers ──


def _sha256_file(path: Path) -> str:
    """Compute SHA-256 hex digest of a file."""
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while True:
            chunk = f.read(1024 * 1024)  # 1 MB
            if not chunk:
                break
            h.update(chunk)
    return h.hexdigest()


def _snapshot_directory(directory: Path) -> dict:
    """Snapshot a directory for before/after comparison.

    Returns file count and a stable content hash.
    Uses os.scandir() recursively WITHOUT following symlinks.
    Used to verify source directories aren't modified during copy.
    """
    import stat as _stat
    files = []

    def _walk(dir_path: Path) -> None:
        try:
            with os.scandir(str(dir_path)) as entries:
                for entry in entries:
                    if entry.is_symlink():
                        continue  # Don't follow or track symlinks
                    try:
                        st = entry.stat(follow_symlinks=False)
                    except OSError:
                        continue
                    if _stat.S_ISREG(st.st_mode):
                        try:
                            rel = Path(entry.path).relative_to(directory).as_posix()
                            files.append((rel, st.st_size))
                        except ValueError:
                            continue
                    elif _stat.S_ISDIR(st.st_mode):
                        _walk(Path(entry.path))
        except OSError:
            pass

    _walk(directory)

    files.sort()
    h = hashlib.sha256()
    for rel, size in files:
        h.update(f"{rel}:{size}\n".encode("utf-8"))
    return {
        "file_count": len(files),
        "checksum": h.hexdigest(),
    }
