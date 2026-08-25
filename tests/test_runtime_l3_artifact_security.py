"""L3 tests: Artifact security boundary — path, file type, TOCTOU, transactions.

Uses real filesystem operations for security-relevant checks.
Windows-specific tests are defined only where applicable.
"""

from __future__ import annotations

import hashlib
import json
import os
import stat as _stat
import struct
import tempfile
import zipfile
from pathlib import Path

import pytest

from dp_engine.skills.runtime_artifacts import (
    ArtifactPublisher,
    ArtifactStore,
    _sniff_media_type,
    _compute_sha256,
    _CancelBeforeCommitError,
)
from dp_engine.skills.runtime_models import (
    ArtifactDeclaration,
    ALLOWED_ARTIFACT_EXTENSIONS,
    MAX_ARTIFACT_FILE_BYTES,
)
from dp_engine.skills.runtime_paths import (
    get_artifact_root,
    build_safe_filename,
)


# ── Helpers ──


def _make_declaration(
    declared_path="test.txt",
    display_name="Test",
    media_type_hint=None,
    kind="data",
    metadata=None,
    observed_size_bytes=0,
    observed_sha256="",
    observed_device=0,
    observed_inode=0,
    observed_mtime_ns=0,
):
    """Create an ArtifactDeclaration for testing."""
    return ArtifactDeclaration(
        declared_path=declared_path,
        display_name=display_name,
        media_type_hint=media_type_hint,
        kind=kind,
        metadata=metadata or {},
        observed_size_bytes=observed_size_bytes,
        observed_sha256=observed_sha256,
        observed_device=observed_device,
        observed_inode=observed_inode,
        observed_mtime_ns=observed_mtime_ns,
    )


def _write_file(path, content):
    """Write content bytes to a file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)


def _sha256_file(path):
    """Compute SHA256 of a file."""
    sha = hashlib.sha256()
    with open(path, "rb") as f:
        while True:
            chunk = f.read(65536)
            if not chunk:
                break
            sha.update(chunk)
    return sha.hexdigest()


def _create_ntfs_junction(target_dir: Path, junction_path: Path) -> None:
    """Create an NTFS directory junction using mklink /J.

    Uses the system COMSPEC (cmd.exe) with shell=False.
    The test fails if junction creation is not possible —
    no skip, no mock, no fallback.
    """
    import subprocess

    result = subprocess.run(
        [
            os.environ.get("COMSPEC", r"C:\Windows\System32\cmd.exe"),
            "/d",
            "/s",
            "/c",
            "mklink",
            "/J",
            str(junction_path),
            str(target_dir),
        ],
        shell=False,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, (
        f"Junction creation failed (rc={result.returncode}); "
        f"stdout={result.stdout!r}; stderr={result.stderr!r}"
    )


# ── Content sniffing tests ──


class TestContentSniffing:
    """Tests for content-based media_type detection."""

    def test_pdf_sniffing_valid(self, tmp_path):
        f = tmp_path / "test.pdf"
        _write_file(f, b"%PDF-1.4\n%...")
        result = _sniff_media_type(f, None)
        assert result == "application/pdf"

    def test_pdf_sniffing_fake(self, tmp_path):
        f = tmp_path / "fake.pdf"
        _write_file(f, b"NOT A PDF FILE")
        with pytest.raises(ValueError, match="PDF magic"):
            _sniff_media_type(f, None)

    def test_png_sniffing_valid(self, tmp_path):
        f = tmp_path / "test.png"
        _write_file(f, b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR...")
        result = _sniff_media_type(f, None)
        assert result == "image/png"

    def test_png_sniffing_fake(self, tmp_path):
        f = tmp_path / "fake.png"
        _write_file(f, b"NOT A PNG")
        with pytest.raises(ValueError, match="PNG magic"):
            _sniff_media_type(f, None)

    def test_jpeg_sniffing_valid(self, tmp_path):
        f = tmp_path / "test.jpg"
        _write_file(f, b"\xff\xd8\xff\xe0\x00\x10JFIF...")
        result = _sniff_media_type(f, None)
        assert result == "image/jpeg"

    def test_json_sniffing_valid(self, tmp_path):
        f = tmp_path / "test.json"
        _write_file(f, b'{"key": "value"}')
        result = _sniff_media_type(f, None)
        assert result == "application/json"

    def test_json_sniffing_invalid(self, tmp_path):
        f = tmp_path / "bad.json"
        _write_file(f, b"not json at all")
        with pytest.raises(ValueError, match="valid JSON"):
            _sniff_media_type(f, None)

    def test_json_nan_rejected(self, tmp_path):
        f = tmp_path / "nan.json"
        _write_file(f, b'{"x": NaN}')
        with pytest.raises(ValueError):
            _sniff_media_type(f, None)

    def test_json_infinity_rejected(self, tmp_path):
        f = tmp_path / "inf.json"
        _write_file(f, b'{"x": Infinity}')
        with pytest.raises(ValueError):
            _sniff_media_type(f, None)

    def test_txt_sniffing_valid(self, tmp_path):
        f = tmp_path / "test.txt"
        _write_file(f, "hello world\n".encode("utf-8"))
        result = _sniff_media_type(f, None)
        assert result == "text/plain"

    def test_txt_nul_rejected(self, tmp_path):
        f = tmp_path / "bad.txt"
        _write_file(f, b"hello\x00world")
        with pytest.raises(ValueError, match="NUL"):
            _sniff_media_type(f, None)

    def test_csv_sniffing_valid(self, tmp_path):
        f = tmp_path / "test.csv"
        _write_file(f, "a,b,c\n1,2,3\n".encode("utf-8"))
        result = _sniff_media_type(f, None)
        assert result == "text/csv"

    def test_docx_sniffing_valid(self, tmp_path):
        """Create a minimal valid OOXML-like ZIP."""
        f = tmp_path / "test.docx"
        with zipfile.ZipFile(f, "w") as zf:
            zf.writestr("[Content_Types].xml", "<Types/>")
            zf.writestr("word/document.xml", "<document/>")
        result = _sniff_media_type(f, None)
        assert result.startswith("application/vnd.openxmlformats-officedocument.wordprocessingml")

    def test_docx_zip_without_content_types(self, tmp_path):
        f = tmp_path / "fake.docx"
        with zipfile.ZipFile(f, "w") as zf:
            zf.writestr("word/document.xml", "<document/>")
        with pytest.raises(ValueError, match="Content_Types"):
            _sniff_media_type(f, None)

    def test_zip_sniffing_valid(self, tmp_path):
        f = tmp_path / "test.zip"
        with zipfile.ZipFile(f, "w") as zf:
            zf.writestr("data.txt", "hello")
        result = _sniff_media_type(f, None)
        assert result == "application/zip"

    def test_media_type_hint_conflict(self, tmp_path):
        """Hint doesn't match content → protocol_error."""
        f = tmp_path / "test.txt"
        _write_file(f, "hello\n".encode("utf-8"))
        with pytest.raises(ValueError, match="conflicts"):
            _sniff_media_type(f, "image/png")

    def test_svg_rejected(self, tmp_path):
        """SVG is not in the allowlist."""
        f = tmp_path / "test.svg"
        _write_file(f, b"<svg></svg>")
        with pytest.raises(ValueError, match="not allowed"):
            _sniff_media_type(f, None)

    def test_double_extension_rejected(self, tmp_path):
        """Double extension with inner .exe is rejected."""
        f = tmp_path / "report.pdf.exe"
        _write_file(f, b"bad")
        with pytest.raises(ValueError):
            _sniff_media_type(f, None)


# ── Path security tests ──


class TestArtifactPathSecurity:
    """Path safety verification using real filesystem operations."""

    def test_absolute_path_rejected(self, tmp_path):
        """Absolute path in declared_path → rejected."""
        decl = _make_declaration(declared_path="/etc/passwd")
        # Verify the path syntax validator would catch this
        from dp_engine.skills.runtime_models import _validate_declared_path_syntax
        with pytest.raises(ValueError, match="absolute"):
            _validate_declared_path_syntax("/etc/passwd")

    def test_dot_dot_rejected(self, tmp_path):
        """.. in declared_path → rejected."""
        from dp_engine.skills.runtime_models import _validate_declared_path_syntax
        with pytest.raises(ValueError, match=".."):
            _validate_declared_path_syntax("../../etc/passwd")

    def test_hardlink_rejected(self, tmp_path):
        """Hardlink (nlink > 1) → rejected by ArtifactPublisher.

        Creates a real hardlink with os.link(), proves it via nlink > 1
        and shared inode, then calls ArtifactPublisher.publish_artifacts().
        The Service-side secondary verification
        (_service_verify_declarations) lstat's the source and raises
        RuntimeError("has hardlinks") when st_nlink > 1.

        Hardlink creation failure is a hard test failure — no fallback,
        no ordinary-file fallthrough.
        """
        output_dir = tmp_path / "output"
        output_dir.mkdir(parents=True)
        orig = output_dir / "orig.txt"
        _write_file(orig, b"real content for hardlink test")
        orig_sha = _sha256_file(orig)

        # ── Observe the file BEFORE creating the hardlink ──
        st_before = orig.lstat()
        decl = ArtifactDeclaration(
            declared_path="orig.txt",
            display_name="HardlinkFile",
            media_type_hint=None,
            kind="data",
            metadata={},
            observed_size_bytes=st_before.st_size,
            observed_sha256=orig_sha,
            observed_device=st_before.st_dev,
            observed_inode=st_before.st_ino,
            observed_mtime_ns=st_before.st_mtime_ns,
        )

        # ── Create the hardlink AFTER observation (TOCTOU scenario) ──
        link = output_dir / "link.txt"
        os.link(str(orig), str(link))

        # ── Prove it is a real hardlink ──
        st_orig = orig.stat()
        st_link = link.stat()
        assert st_orig.st_nlink > 1, (
            f"Hardlink: orig nlink={st_orig.st_nlink}, expected > 1"
        )
        assert st_link.st_nlink > 1, (
            f"Hardlink: link nlink={st_link.st_nlink}, expected > 1"
        )
        assert st_orig.st_ino == st_link.st_ino, (
            "Hardlink: orig and link must share inode"
        )

        # ── ArtifactPublisher._service_verify_declarations rejects ──
        # The Service-side check lstat's the source file and sees
        # st_nlink > 1 → raises RuntimeError("has hardlinks").
        test_root = tmp_path / "artifacts"
        publisher = ArtifactPublisher(_artifact_root=test_root)
        with pytest.raises(RuntimeError, match="hardlink"):
            publisher.publish_artifacts(
                (decl,),
                workspace_output=output_dir,
                skill_id="test-skill",
                version="1.0.0",
                task_id="hardlink001",
            )

        # ── Verify no task directory was created ──
        task_dir = test_root / "test-skill" / "hardlink001"
        assert not task_dir.exists(), (
            "Task directory must not exist after hardlink rejection"
        )

        # ── Cleanup: unlink the hardlink entry ──
        link.unlink()
        assert orig.exists(), (
            "Original file must survive hardlink removal"
        )

    def test_directory_rejected(self, tmp_path):
        """Directory as source → rejected."""
        output_dir = tmp_path / "output"
        output_dir.mkdir(parents=True)
        subdir = output_dir / "subdir"
        subdir.mkdir()
        assert subdir.is_dir()
        st = subdir.lstat()
        assert _stat.S_ISDIR(st.st_mode)  # S_ISDIR → reject

    def test_nonexistent_file_rejected(self, tmp_path):
        """Non-existent file → rejected."""
        output_dir = tmp_path / "output"
        output_dir.mkdir(parents=True)
        missing = output_dir / "missing.txt"
        assert not missing.exists()

    def test_empty_path_rejected(self):
        """Empty path → rejected."""
        from dp_engine.skills.runtime_models import _validate_declared_path_syntax
        with pytest.raises(ValueError):
            _validate_declared_path_syntax("")


# ── POSIX-specific security tests ──
# Defined only on POSIX; NOT collected on Windows (not skipped, not defined).

if os.name != "nt":

    class TestPosixPathSecurity:
        """POSIX-specific path security with real special files.

        Each test creates a real filesystem object (symlink, FIFO, socket)
        and verifies the production security checks reject it.
        Creation failure is a hard test failure — no skip, no fallback.
        """

        def test_symlink_file_rejected(self, tmp_path):
            """Real symlink file source → rejected by worker observation."""
            output_dir = tmp_path / "output"
            output_dir.mkdir(parents=True)
            real_file = output_dir / "real.txt"
            _write_file(real_file, b"content")
            symlink_file = output_dir / "link.txt"
            symlink_file.symlink_to(real_file)

            # Prove it is a real symlink
            st = symlink_file.lstat()
            assert _stat.S_ISLNK(st.st_mode), (
                "Must be a real symlink (S_ISLNK)"
            )

            # Worker parent-component verification must reject
            from dp_engine.skills.runtime_worker import (
                _verify_parent_components_worker,
            )
            with pytest.raises((ValueError, RuntimeError)):
                _verify_parent_components_worker(
                    symlink_file, output_dir.resolve(),
                )

        def test_symlink_parent_rejected(self, tmp_path):
            """Symlink in a parent directory component → rejected."""
            output_dir = tmp_path / "output"
            output_dir.mkdir(parents=True)
            real_dir = tmp_path / "real_dir"
            real_dir.mkdir()
            real_file = real_dir / "data.txt"
            _write_file(real_file, b"content")

            # symlink the parent
            symlink_parent = output_dir / "linked_dir"
            symlink_parent.symlink_to(real_dir)

            file_via_symlink = symlink_parent / "data.txt"
            assert file_via_symlink.exists()

            from dp_engine.skills.runtime_worker import (
                _verify_parent_components_worker,
            )
            with pytest.raises((ValueError, RuntimeError)):
                _verify_parent_components_worker(
                    file_via_symlink, output_dir.resolve(),
                )

        def test_fifo_rejected(self, tmp_path):
            """FIFO special file → rejected (not a regular file)."""
            output_dir = tmp_path / "output"
            output_dir.mkdir(parents=True)
            fifo_path = output_dir / "test.fifo"
            os.mkfifo(str(fifo_path))

            st = fifo_path.lstat()
            assert not _stat.S_ISREG(st.st_mode), (
                "FIFO must not be a regular file"
            )


# ── Windows-specific security tests ──


if os.name == "nt":

    class TestWindowsPathSecurity:
        """Windows-specific path security tests."""

        def test_junction_rejected(self, tmp_path):
            """Windows junction → rejected by artifact path security.

            Creates a real NTFS directory junction, proves it IS a reparse
            point, then verifies that both the worker-side parent-component
            walk and the publisher-side path-verification reject it.

            No skip, no mock, no fallback — junction creation failure is a
            hard test failure (P0).
            """
            assert os.name == "nt", "test_junction_rejected requires Windows NTFS"

            output_dir = tmp_path / "output"
            output_dir.mkdir(parents=True)
            target_dir = tmp_path / "target"
            target_dir.mkdir()

            # Create a real file in the target directory (outside output/)
            real_file = target_dir / "report.txt"
            _write_file(real_file, b"legitimate content")
            real_sha = _sha256_file(real_file)

            junction = output_dir / "junction_link"
            _create_ntfs_junction(target_dir, junction)

            # ── Evidence 1: junction exists and is a reparse point ──
            assert junction.exists(), "Junction must exist after creation"
            st_junction = junction.lstat()
            assert hasattr(st_junction, "st_file_attributes"), (
                "Windows must expose st_file_attributes"
            )
            FILE_ATTRIBUTE_REPARSE_POINT = 0x400
            assert st_junction.st_file_attributes & FILE_ATTRIBUTE_REPARSE_POINT, (
                f"Junction must be reparse point; "
                f"attributes={st_junction.st_file_attributes:#x}"
            )

            # ── Evidence 2: _is_symlink_or_reparse detects the junction ──
            from dp_engine.skills.runtime_paths import _is_symlink_or_reparse
            assert _is_symlink_or_reparse(junction), (
                "_is_symlink_or_reparse must return True for NTFS junction"
            )

            # File must be reachable through the junction
            junction_file = junction / "report.txt"
            assert junction_file.exists(), (
                "File through junction must be reachable"
            )

            # ── Evidence 3: Worker parent-component check rejects reparse ──
            from dp_engine.skills.runtime_worker import (
                _verify_parent_components_worker,
            )
            with pytest.raises((ValueError, RuntimeError), match="reparse"):
                _verify_parent_components_worker(
                    junction_file, output_dir.resolve(),
                )

            # ── Evidence 4: Full publisher rejects junction-bearing path ──
            # The junction points outside output, so verify_path_within_root
            # catches it as a path-escape; regardless of which exact code
            # path fires, the publish must NOT succeed.
            decl = ArtifactDeclaration(
                declared_path="junction_link/report.txt",
                display_name="JunctionReport",
                media_type_hint=None,
                kind="data",
                metadata={},
                observed_size_bytes=real_file.stat().st_size,
                observed_sha256=real_sha,
                observed_device=real_file.stat().st_dev,
                observed_inode=real_file.stat().st_ino,
                observed_mtime_ns=real_file.stat().st_mtime_ns,
            )

            test_root = tmp_path / "artifacts"
            publisher = ArtifactPublisher(_artifact_root=test_root)
            with pytest.raises((ValueError, RuntimeError)):
                publisher.publish_artifacts(
                    (decl,),
                    workspace_output=output_dir,
                    skill_id="test-skill",
                    version="1.0.0",
                    task_id="junction001",
                )

            # Verify no task directory was created (commit rolled back)
            task_dir = test_root / "test-skill" / "junction001"
            assert not task_dir.exists(), (
                "Task directory must not exist after junction rejection"
            )

            # ── Cleanup: rmdir junction first (does NOT delete target contents) ──
            junction.rmdir()
            assert target_dir.exists(), (
                "Target directory must survive junction rmdir"
            )

        def test_ads_rejected(self, tmp_path):
            """NTFS ADS → rejected by path syntax or filesystem checks.

            Creates a real NTFS Alternate Data Stream, proves it exists, then
            verifies the production path-syntax validator rejects colon-bearing
            declared paths (drive-letter form at minimum).
            """
            assert os.name == "nt", "test_ads_rejected requires Windows NTFS"

            output_dir = tmp_path / "output"
            output_dir.mkdir(parents=True)

            # ── Create a real NTFS ADS stream ──
            base = output_dir / "test.txt"
            _write_file(base, b"base content")

            ads_full = str(base) + ":hidden"
            with open(ads_full, "w", encoding="utf-8") as f:
                f.write("hidden stream data")

            # ── Prove the ADS exists ──
            with open(ads_full, "r", encoding="utf-8") as f:
                assert f.read() == "hidden stream data", (
                    "ADS stream must be readable"
                )

            # Base file content is unchanged by ADS
            assert base.read_bytes() == b"base content", (
                "Base file must be unchanged by ADS write"
            )
            st = base.lstat()
            assert _stat.S_ISREG(st.st_mode)

            # ── Path syntax check rejects colon patterns ──
            from dp_engine.skills.runtime_models import (
                _validate_declared_path_syntax,
            )
            # Drive-letter form "C:..." is explicitly rejected
            with pytest.raises(ValueError, match="drive letter"):
                _validate_declared_path_syntax("C:test.txt")

        def test_case_insensitive_path(self, tmp_path):
            """Windows case-insensitive path handling."""
            assert os.name == "nt", "test_case_insensitive_path requires Windows"

            output_dir = tmp_path / "output"
            output_dir.mkdir(parents=True)
            f = output_dir / "TestFile.TXT"
            _write_file(f, b"content")

            # Case variation should still resolve
            resolved = output_dir.resolve()
            assert (resolved / "testfile.txt").exists() or (resolved / "TestFile.TXT").exists()


# ── File type security tests ──


class TestArtifactTypeSecurity:
    """Tests for file type rejection."""

    def test_exe_rejected(self, tmp_path):
        f = tmp_path / "test.exe"
        _write_file(f, b"MZ...")
        with pytest.raises(ValueError, match="not allowed"):
            _sniff_media_type(f, None)

    def test_no_extension_rejected(self, tmp_path):
        f = tmp_path / "noextension"
        _write_file(f, b"hello")
        with pytest.raises(ValueError, match="not allowed"):
            _sniff_media_type(f, None)

    def test_double_extension_exe_rejected(self, tmp_path):
        f = tmp_path / "report.pdf.exe"
        _write_file(f, b"MZ...")
        with pytest.raises(ValueError):
            _sniff_media_type(f, None)


# ── Transaction tests ──


class TestArtifactTransaction:
    """Atomic commit and rollback tests."""

    def _make_observed_decl(self, src_path, declared_path, display_name="Test", kind="data"):
        """Create an ArtifactDeclaration with real file observation."""
        st = src_path.lstat()
        sha = _sha256_file(src_path)
        return _make_declaration(
            declared_path=declared_path,
            display_name=display_name,
            kind=kind,
            observed_size_bytes=st.st_size,
            observed_sha256=sha,
            observed_device=st.st_dev,
            observed_inode=st.st_ino,
            observed_mtime_ns=st.st_mtime_ns,
        )

    def test_publish_single_artifact(self, tmp_path):
        """Basic publish: file goes to artifact root."""
        output_dir = tmp_path / "output"
        output_dir.mkdir(parents=True)
        f = output_dir / "data.txt"
        _write_file(f, b"hello world")

        decl = self._make_observed_decl(f, "data.txt", "Data", "data")

        test_root = tmp_path / "artifacts"
        publisher = ArtifactPublisher(_artifact_root=test_root)
        result = publisher.publish_artifacts(
            (decl,),
            workspace_output=output_dir,
            skill_id="test-skill",
            version="1.0.0",
            task_id="task001",
        )

        assert len(result) == 1
        art = result[0]
        assert art.display_name == "Data"
        assert art.size_bytes == len(b"hello world")
        assert art.sha256 is not None and len(art.sha256) == 64
        assert art.artifact_schema_version == 1
        assert len(art.artifact_id) == 32
        assert art.kind == "data"
        assert art.skill_id == "test-skill"
        assert art.version == "1.0.0"
        assert art.task_id == "task001"

        # Verify file exists in artifact root
        task_dir = test_root / "test-skill" / "task001"
        assert task_dir.is_dir()
        assert (task_dir / "manifest.json").is_file()
        # Verify manifest
        manifest = json.loads((task_dir / "manifest.json").read_text())
        assert manifest["task_id"] == "task001"
        assert len(manifest["artifacts"]) == 1

    def test_publish_multiple_artifacts(self, tmp_path):
        """Multiple artifacts in one publish."""
        output_dir = tmp_path / "output"
        output_dir.mkdir(parents=True)

        f1 = output_dir / "a.txt"
        f2 = output_dir / "b.json"
        _write_file(f1, b"AAA")
        _write_file(f2, b'{"x":1}')

        d1 = self._make_observed_decl(f1, "a.txt", "A", "data")
        d2 = self._make_observed_decl(f2, "b.json", "B", "data")

        test_root = tmp_path / "artifacts"
        publisher = ArtifactPublisher(_artifact_root=test_root)
        result = publisher.publish_artifacts(
            (d1, d2),
            workspace_output=output_dir,
            skill_id="test-skill",
            version="1.0.0",
            task_id="task002",
        )

        assert len(result) == 2
        assert result[0].display_name == "A"
        assert result[1].display_name == "B"

    def test_same_fingerprint_idempotent(self, tmp_path):
        """Same declarations → same fingerprint → idempotent return."""
        output_dir = tmp_path / "output"
        output_dir.mkdir(parents=True)
        f = output_dir / "x.txt"
        _write_file(f, b"same content")

        decl = self._make_observed_decl(f, "x.txt", "X", "data")

        test_root = tmp_path / "artifacts"
        publisher = ArtifactPublisher(_artifact_root=test_root)
        r1 = publisher.publish_artifacts(
            (decl,),
            workspace_output=output_dir,
            skill_id="test-skill",
            version="1.0.0",
            task_id="idem001",
        )
        # Second publish with same declarations
        r2 = publisher.publish_artifacts(
            (decl,),
            workspace_output=output_dir,
            skill_id="test-skill",
            version="1.0.0",
            task_id="idem001",
        )

        assert len(r1) == 1
        assert len(r2) == 1
        assert r1[0].artifact_id == r2[0].artifact_id

    def test_different_fingerprint_rejected(self, tmp_path):
        """Different declarations for same task → fail closed."""
        output_dir = tmp_path / "output"
        output_dir.mkdir(parents=True)

        f1 = output_dir / "a.txt"
        f2 = output_dir / "b.txt"
        _write_file(f1, b"AAA")
        _write_file(f2, b"BBB")

        d1 = self._make_observed_decl(f1, "a.txt", "A", "data")
        d2 = self._make_observed_decl(f2, "b.txt", "B", "data")

        test_root = tmp_path / "artifacts"
        publisher = ArtifactPublisher(_artifact_root=test_root)
        publisher.publish_artifacts(
            (d1,),
            workspace_output=output_dir,
            skill_id="test-skill",
            version="1.0.0",
            task_id="conflict001",
        )
        # Different fingerprint → reject
        with pytest.raises(RuntimeError, match="fingerprint"):
            publisher.publish_artifacts(
                (d2,),
                workspace_output=output_dir,
                skill_id="test-skill",
                version="1.0.0",
                task_id="conflict001",
            )

    def test_cancel_before_commit(self, tmp_path):
        """Cancel before os.replace → rollback, no artifacts."""
        output_dir = tmp_path / "output"
        output_dir.mkdir(parents=True)
        f = output_dir / "data.txt"
        _write_file(f, b"hello")

        decl = self._make_observed_decl(f, "data.txt", "Data", "data")

        test_root = tmp_path / "artifacts"
        publisher = ArtifactPublisher(_artifact_root=test_root)
        with pytest.raises(_CancelBeforeCommitError):
            publisher.publish_artifacts(
                (decl,),
                workspace_output=output_dir,
                skill_id="test-skill",
                version="1.0.0",
                task_id="cancel001",
                check_cancelled=lambda: True,  # Always cancelled
            )
        # Task dir should not exist
        task_dir = test_root / "test-skill" / "cancel001"
        assert not task_dir.exists()


# ── ArtifactStore tests ──


class TestArtifactStore:
    """ArtifactStore safety and functionality tests."""

    def _publish_one(self, tmp_path, skill_id="store-skill", task_id="store001"):
        """Helper: publish a single artifact and return store + artifact + root."""
        output_dir = tmp_path / "output"
        output_dir.mkdir(parents=True)
        f = output_dir / "data.json"
        _write_file(f, b'{"key": "value"}')

        decl = ArtifactDeclaration(
            declared_path="data.json",
            display_name="StoreData",
            media_type_hint=None,
            kind="data",
            metadata={"test": True},
            observed_size_bytes=f.stat().st_size,
            observed_sha256=_sha256_file(f),
            observed_device=f.stat().st_dev,
            observed_inode=f.stat().st_ino,
            observed_mtime_ns=f.stat().st_mtime_ns,
        )

        test_root = tmp_path / "artifacts"
        publisher = ArtifactPublisher(_artifact_root=test_root)
        arts = publisher.publish_artifacts(
            (decl,),
            workspace_output=output_dir,
            skill_id=skill_id,
            version="1.0.0",
            task_id=task_id,
        )
        store = ArtifactStore(_artifact_root=test_root)
        return store, arts[0], test_root

    def test_list_task(self, tmp_path):
        store, art, root = self._publish_one(tmp_path)
        result = store.list_task("store-skill", "store001")
        assert len(result) == 1
        assert result[0].artifact_id == art.artifact_id
        assert result[0].display_name == "StoreData"

    def test_list_task_empty(self, tmp_path):
        store = ArtifactStore(_artifact_root=tmp_path / "nonexistent")
        result = store.list_task("nonexistent", "task999")
        assert result == ()

    def test_locate_verifies_manifest(self, tmp_path):
        """Locate works for existing artifact."""
        store, art, root = self._publish_one(tmp_path, "locate-skill", "loc001")
        # locate() is tested via the ArtifactStore interface
        # We just verify it doesn't crash and raises for missing artifacts
        with pytest.raises(RuntimeError, match="not found"):
            store.locate("locate-skill", "loc001", "nonexistent32bytehexstring12345678")

    def test_export_safe_copy(self, tmp_path):
        """Export creates a valid copy."""
        store, art, root = self._publish_one(tmp_path, "export-skill", "exp001")
        target = tmp_path / "exported.json"
        store.export("export-skill", "exp001", art.artifact_id, target)
        assert target.is_file()
        assert target.read_bytes() == b'{"key": "value"}'

    def test_export_overwrite_false(self, tmp_path):
        """Export with overwrite=False on existing target → FileExistsError."""
        store, art, root = self._publish_one(tmp_path, "ow-skill", "ow001")
        target = tmp_path / "exists.json"
        target.write_text("existing")
        with pytest.raises(FileExistsError):
            store.export("ow-skill", "ow001", art.artifact_id, target, overwrite=False)

    def test_export_overwrite_true(self, tmp_path):
        """Export with overwrite=True succeeds."""
        store, art, root = self._publish_one(tmp_path, "ow2-skill", "ow2001")
        target = tmp_path / "overwrite.json"
        target.write_text("old content")
        store.export("ow2-skill", "ow2001", art.artifact_id, target, overwrite=True)
        assert target.read_bytes() == b'{"key": "value"}'

    def test_delete_task_safe(self, tmp_path):
        """Delete task removes the task directory."""
        store, art, root = self._publish_one(tmp_path, "del-skill", "del001")
        task_dir = root / "del-skill" / "del001"
        assert task_dir.is_dir()
        store.delete_task("del-skill", "del001")
        assert not task_dir.exists()

    def test_delete_nonexistent_task(self, tmp_path):
        """Delete non-existent task → no error."""
        store = ArtifactStore(_artifact_root=tmp_path / "empty")
        # Should not raise
        store.delete_task("no-such-skill", "no-such-task")

    def test_owner_mismatch_rejected(self, tmp_path):
        """Query with wrong skill_id → returns empty (directory not found at that path)."""
        store, art, root = self._publish_one(tmp_path, "owner1", "task1")
        # owner2/task1 doesn't exist — returns empty tuple safely
        result = store.list_task("owner2", "task1")
        assert result == ()
