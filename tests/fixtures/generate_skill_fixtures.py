"""Generate test fixtures for skill package testing.

Run this script to create ZIP fixtures for security tests.
Fixtures are small and deterministic — safe to commit.
"""

from __future__ import annotations

import hashlib
import os
import struct
import zipfile
from pathlib import Path

FIXTURES_DIR = Path(__file__).parent / "skill_packages"


def _ensure_dir() -> None:
    FIXTURES_DIR.mkdir(parents=True, exist_ok=True)


def _make_skill_md(
    skill_id: str = "test-skill",
    name: str = "Test Skill",
    version: str = "1.0.0",
    entrypoints: str = "  run: run.py",
    capabilities: str = "  - read",
    extra: str = "",
) -> str:
    return f"""---
schema_version: 1
skill_id: {skill_id}
name: {name}
version: {version}
description: A test skill for unit testing
skill_type: instruction
capabilities:
{capabilities}
entrypoints:
{entrypoints}{extra}
---

# Test Skill

This is a test skill for unit testing.
"""


def _make_run_py() -> str:
    return "# Test entrypoint — never executed by installer\nprint('hello')\n"


# ── Valid fixtures ──


def generate_valid_directory_skill() -> None:
    """Create a valid skill directory fixture."""
    _ensure_dir()
    root = FIXTURES_DIR / "valid_directory_skill"
    if root.exists():
        import shutil
        shutil.rmtree(str(root))
    root.mkdir(parents=True)
    (root / "SKILL.md").write_text(_make_skill_md(), encoding="utf-8")
    (root / "run.py").write_text(_make_run_py(), encoding="utf-8")
    (root / "scripts").mkdir()
    (root / "scripts" / "helper.py").write_text("# helper\n", encoding="utf-8")
    print(f"Created: {root}")


def generate_valid_nested_github_archive() -> None:
    """Create a ZIP with repo-branch/skill_dir/SKILL.md (GitHub archive layout)."""
    _ensure_dir()
    import tempfile, shutil
    with tempfile.TemporaryDirectory() as td:
        base = Path(td) / "test-repo-main"
        skill_dir = base / "skills" / "my-skill"
        skill_dir.mkdir(parents=True)
        (skill_dir / "SKILL.md").write_text(
            _make_skill_md(skill_id="my-skill", name="My Skill"),
            encoding="utf-8",
        )
        (skill_dir / "run.py").write_text(_make_run_py(), encoding="utf-8")
        # Also add a README at repo root
        (base / "README.md").write_text("# Test Repo\n", encoding="utf-8")

        zip_path = FIXTURES_DIR / "valid_nested_github_archive.zip"
        _make_zip(zip_path, base, td)
        print(f"Created: {zip_path}")


def generate_valid_flat_zip() -> None:
    """Create a ZIP with SKILL.md at root."""
    _ensure_dir()
    import tempfile
    with tempfile.TemporaryDirectory() as td:
        root = Path(td) / "skill_root"
        root.mkdir()
        (root / "SKILL.md").write_text(_make_skill_md(), encoding="utf-8")
        (root / "run.py").write_text(_make_run_py(), encoding="utf-8")

        zip_path = FIXTURES_DIR / "valid_flat_skill.zip"
        _make_zip(zip_path, root, td)
        print(f"Created: {zip_path}")


# ── Invalid / security fixtures ──


def generate_missing_skill_md_zip() -> None:
    """ZIP without SKILL.md."""
    _ensure_dir()
    import tempfile
    with tempfile.TemporaryDirectory() as td:
        root = Path(td) / "no_skill_md"
        root.mkdir()
        (root / "run.py").write_text(_make_run_py(), encoding="utf-8")

        zip_path = FIXTURES_DIR / "missing_skill_md.zip"
        _make_zip(zip_path, root, td)
        print(f"Created: {zip_path}")


def generate_multiple_skill_md_zip() -> None:
    """ZIP with multiple SKILL.md in different subdirs."""
    _ensure_dir()
    import tempfile
    with tempfile.TemporaryDirectory() as td:
        root = Path(td) / "multi"
        root.mkdir()
        (root / "README.md").write_text("# Multi\n", encoding="utf-8")
        a = root / "skill-a"
        a.mkdir()
        (a / "SKILL.md").write_text(
            _make_skill_md(skill_id="skill-a", name="Skill A"),
            encoding="utf-8",
        )
        b = root / "skill-b"
        b.mkdir()
        (b / "SKILL.md").write_text(
            _make_skill_md(skill_id="skill-b", name="Skill B"),
            encoding="utf-8",
        )

        zip_path = FIXTURES_DIR / "multiple_skill_md.zip"
        _make_zip(zip_path, root, td)
        print(f"Created: {zip_path}")


def generate_zip_slip_parent() -> None:
    """ZIP with ../ path traversal."""
    _ensure_dir()
    zip_path = FIXTURES_DIR / "zip_slip_parent.zip"
    with zipfile.ZipFile(zip_path, "w") as zf:
        zf.writestr("../outside/evil.py", "# malicious\n")
        zf.writestr("skill/SKILL.md", _make_skill_md())
    print(f"Created: {zip_path}")


def generate_zip_slip_absolute() -> None:
    """ZIP with absolute POSIX path."""
    _ensure_dir()
    zip_path = FIXTURES_DIR / "zip_slip_absolute.zip"
    with zipfile.ZipFile(zip_path, "w") as zf:
        zf.writestr("/etc/passwd", "root:x:0:0:...\n")
        zf.writestr("skill/SKILL.md", _make_skill_md())
    print(f"Created: {zip_path}")


def generate_windows_drive_path_zip() -> None:
    """ZIP with Windows drive-letter path."""
    _ensure_dir()
    zip_path = FIXTURES_DIR / "windows_drive_path.zip"
    with zipfile.ZipFile(zip_path, "w") as zf:
        zf.writestr("C:/Windows/evil.exe", "# not good\n")
        zf.writestr("skill/SKILL.md", _make_skill_md())
    print(f"Created: {zip_path}")


def generate_unc_path_zip() -> None:
    """ZIP with UNC path."""
    _ensure_dir()
    zip_path = FIXTURES_DIR / "unc_path.zip"
    with zipfile.ZipFile(zip_path, "w") as zf:
        zf.writestr("\\\\server\\share\\evil.py", "# bad\n")
        zf.writestr("skill/SKILL.md", _make_skill_md())
    print(f"Created: {zip_path}")


def generate_duplicate_path_zip() -> None:
    """ZIP with duplicate entry paths."""
    _ensure_dir()
    zip_path = FIXTURES_DIR / "duplicate_path.zip"
    with zipfile.ZipFile(zip_path, "w") as zf:
        zf.writestr("skill/run.py", "# first\n")
        zf.writestr("skill/run.py", "# second\n")  # Duplicate!
    print(f"Created: {zip_path}")


def generate_case_collision_zip() -> None:
    """ZIP with case-insensitive path collision."""
    _ensure_dir()
    zip_path = FIXTURES_DIR / "case_collision.zip"
    with zipfile.ZipFile(zip_path, "w") as zf:
        zf.writestr("skill/Run.py", "# upper\n")
        zf.writestr("skill/run.py", "# lower\n")  # Case collision!
    print(f"Created: {zip_path}")


def generate_symlink_entry_zip() -> None:
    """ZIP with a Unix symlink entry."""
    _ensure_dir()
    zip_path = FIXTURES_DIR / "symlink_entry.zip"
    with zipfile.ZipFile(zip_path, "w") as zf:
        # Create a symlink-looking entry using external_attr
        info = zipfile.ZipInfo("skill/link.py")
        # Unix symlink mode: 0o120777
        info.external_attr = (0o120777 << 16)
        zf.writestr(info, "target")
        zf.writestr("skill/SKILL.md", _make_skill_md())
    print(f"Created: {zip_path}")


def generate_encrypted_entry_zip() -> None:
    """ZIP with encrypted entry (flag bit 0 set)."""
    _ensure_dir()
    zip_path = FIXTURES_DIR / "encrypted_entry.zip"
    with zipfile.ZipFile(zip_path, "w") as zf:
        info = zipfile.ZipInfo("skill/secret.py")
        info.flag_bits |= 0x1  # Encrypted
        # Need to set a fake CRC for encrypted entries
        info.CRC = 0
        info.compress_size = 0
        info.file_size = 0
        info.compress_type = zipfile.ZIP_STORED
        zf.writestr(info, b"")
        zf.writestr("skill/SKILL.md", _make_skill_md())
    print(f"Created: {zip_path}")


def generate_too_many_files_zip() -> None:
    """ZIP with many files (but actually small — just test limit)."""
    _ensure_dir()
    zip_path = FIXTURES_DIR / "too_many_files.zip"
    with zipfile.ZipFile(zip_path, "w") as zf:
        for i in range(100):
            zf.writestr(f"skill/file_{i:04d}.py", f"# file {i}\n")
        zf.writestr("skill/SKILL.md", _make_skill_md())
    print(f"Created: {zip_path}")


def generate_missing_entrypoint_zip() -> None:
    """ZIP with valid manifest but missing declared entrypoint."""
    _ensure_dir()
    import tempfile
    with tempfile.TemporaryDirectory() as td:
        root = Path(td) / "no_entry"
        root.mkdir()
        (root / "SKILL.md").write_text(
            _make_skill_md(entrypoints="  run: missing.py"),
            encoding="utf-8",
        )
        # No missing.py!

        zip_path = FIXTURES_DIR / "missing_entrypoint.zip"
        _make_zip(zip_path, root, td)
        print(f"Created: {zip_path}")


def generate_invalid_manifest_zip() -> None:
    """ZIP with invalid YAML in SKILL.md."""
    _ensure_dir()
    import tempfile
    with tempfile.TemporaryDirectory() as td:
        root = Path(td) / "bad_manifest"
        root.mkdir()
        (root / "SKILL.md").write_text(
            "---\nskill_id: [this is broken YAML\n---\n",
            encoding="utf-8",
        )

        zip_path = FIXTURES_DIR / "invalid_manifest.zip"
        _make_zip(zip_path, root, td)
        print(f"Created: {zip_path}")


def generate_high_compression_ratio_zip() -> None:
    """ZIP with a high compression ratio entry."""
    _ensure_dir()
    zip_path = FIXTURES_DIR / "high_compression_ratio.zip"
    with zipfile.ZipFile(zip_path, "w") as zf:
        # Create a large, highly compressible entry (all zeros)
        info = zipfile.ZipInfo("skill/large_zeros.bin")
        info.compress_type = zipfile.ZIP_DEFLATED
        # 10 MB of zeros — compresses to very little
        zf.writestr(info, b"\x00" * (10 * 1024 * 1024))
        zf.writestr("skill/SKILL.md", _make_skill_md())
    print(f"Created: {zip_path}")


def generate_oversized_single_file_zip() -> None:
    """ZIP with a single file that exceeds the size limit (simulated)."""
    _ensure_dir()
    zip_path = FIXTURES_DIR / "oversized_file.zip"
    with zipfile.ZipFile(zip_path, "w") as zf:
        info = zipfile.ZipInfo("skill/huge.py")
        info.file_size = 200 * 1024 * 1024  # 200 MB declared
        info.compress_type = zipfile.ZIP_STORED
        zf.writestr(info, b"# massive file\n" * 100)  # Small actual content
        zf.writestr("skill/SKILL.md", _make_skill_md())
    print(f"Created: {zip_path}")


# ── Replacement test fixtures ──


def generate_replacement_v1() -> None:
    """Skill package v1.0.0 for replacement testing."""
    _ensure_dir()
    import tempfile
    with tempfile.TemporaryDirectory() as td:
        root = Path(td) / "replace_v1"
        root.mkdir()
        (root / "SKILL.md").write_text(
            _make_skill_md(skill_id="replace-test", version="1.0.0", name="Replace Test v1"),
            encoding="utf-8",
        )
        (root / "run.py").write_text("# v1\n", encoding="utf-8")

        zip_path = FIXTURES_DIR / "replacement_skill_v1.zip"
        _make_zip(zip_path, root, td)
        print(f"Created: {zip_path}")


# ── Helper ──


def _make_zip(zip_path: Path, source_dir: Path, temp_dir: str) -> None:
    """Create a ZIP from a directory, with normalized paths."""
    import shutil
    if zip_path.exists():
        zip_path.unlink()
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for file in sorted(source_dir.rglob("*")):
            if file.is_file():
                arcname = file.relative_to(source_dir).as_posix()
                zf.write(file, arcname)


# ── Main ──


def main() -> None:
    print("Generating skill package test fixtures...")
    _ensure_dir()

    # Valid
    generate_valid_directory_skill()
    generate_valid_nested_github_archive()
    generate_valid_flat_zip()

    # Invalid / missing
    generate_missing_skill_md_zip()
    generate_multiple_skill_md_zip()

    # Security attacks
    generate_zip_slip_parent()
    generate_zip_slip_absolute()
    generate_windows_drive_path_zip()
    generate_unc_path_zip()
    generate_duplicate_path_zip()
    generate_case_collision_zip()
    generate_symlink_entry_zip()
    generate_encrypted_entry_zip()

    # Limits
    generate_too_many_files_zip()
    generate_high_compression_ratio_zip()
    generate_oversized_single_file_zip()

    # Manifest issues
    generate_missing_entrypoint_zip()
    generate_invalid_manifest_zip()

    # Replacement
    generate_replacement_v1()

    print("\nDone! All fixtures generated.")


if __name__ == "__main__":
    main()
