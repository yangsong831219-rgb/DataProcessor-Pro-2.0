"""L3 tests: ArtifactStore safe consumption API.

Tests list_task, locate, export, delete_task with
security validation: manifest integrity, owner verification,
storage_relpath safety, symlink/reparse rejection.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

import pytest

from dp_engine.skills.runtime_artifacts import (
    ArtifactPublisher,
    ArtifactStore,
    _compute_sha256,
)
from dp_engine.skills.runtime_models import ArtifactDeclaration


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


def _sha256_file(path):
    sha = hashlib.sha256()
    with open(path, "rb") as f:
        while True:
            chunk = f.read(65536)
            if not chunk:
                break
            sha.update(chunk)
    return sha.hexdigest()


def _publish_fixture(tmp_path, skill_id="store-skill", task_id="store001"):
    """Create a published artifact and return (artifact, artifact_root)."""
    output_dir = tmp_path / "output"
    output_dir.mkdir(parents=True)
    src = output_dir / "data.json"
    src.write_text('{"result": 42}', encoding="utf-8")

    st = src.lstat()
    decl = _make_declaration(
        declared_path="data.json",
        display_name="TestData",
        kind="data",
        observed_size_bytes=st.st_size,
        observed_sha256=_sha256_file(src),
        observed_device=st.st_dev,
        observed_inode=st.st_ino,
        observed_mtime_ns=st.st_mtime_ns,
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
    return arts[0], test_root


class TestArtifactStoreCore:
    """ArtifactStore core functionality tests."""

    def test_list_task_returns_artifact(self, tmp_path):
        art, root = _publish_fixture(tmp_path, "l1", "t1")
        store = ArtifactStore(_artifact_root=root)
        result = store.list_task("l1", "t1")
        assert len(result) == 1
        r = result[0]
        assert r.artifact_id == art.artifact_id
        assert r.display_name == "TestData"
        assert r.size_bytes == 14
        assert r.sha256 is not None
        assert r.media_type == "application/json"

    def test_list_task_nonexistent(self, tmp_path):
        store = ArtifactStore(_artifact_root=tmp_path / "empty")
        result = store.list_task("no-skill", "no-task")
        assert result == ()

    def test_export_success(self, tmp_path):
        art, root = _publish_fixture(tmp_path, "exp1", "t1")
        target = tmp_path / "exported.json"
        store = ArtifactStore(_artifact_root=root)
        store.export("exp1", "t1", art.artifact_id, target)
        assert target.is_file()
        assert target.read_text() == '{"result": 42}'

    def test_export_overwrite_false_rejects(self, tmp_path):
        art, root = _publish_fixture(tmp_path, "ow1", "t1")
        target = tmp_path / "exists.txt"
        target.write_text("existing")
        store = ArtifactStore(_artifact_root=root)
        with pytest.raises(FileExistsError):
            store.export("ow1", "t1", art.artifact_id, target, overwrite=False)

    def test_delete_task_success(self, tmp_path):
        art, root = _publish_fixture(tmp_path, "del1", "t1")
        task_dir = root / "del1" / "t1"
        assert task_dir.is_dir()
        store = ArtifactStore(_artifact_root=root)
        store.delete_task("del1", "t1")
        assert not task_dir.exists()

    def test_delete_task_nonexistent_no_error(self, tmp_path):
        store = ArtifactStore(_artifact_root=tmp_path / "empty")
        store.delete_task("ghost", "ghost")  # Should not raise

    def test_manifest_unknown_field_rejected(self, tmp_path):
        """Manifest with unknown top-level field → rejected."""
        art, root = _publish_fixture(tmp_path, "mf1", "t1")
        manifest_path = root / "mf1" / "t1" / "manifest.json"

        # Corrupt manifest with unknown field
        data = json.loads(manifest_path.read_text())
        data["unknown_field"] = "should be rejected"
        manifest_path.write_text(json.dumps(data), encoding="utf-8")

        store = ArtifactStore(_artifact_root=root)
        with pytest.raises(RuntimeError, match="unknown"):
            store.list_task("mf1", "t1")

    def test_owner_mismatch_delete_noop(self, tmp_path):
        """Delete with wrong skill_id → no-op (directory not found at that path)."""
        art, root = _publish_fixture(tmp_path, "owner1", "t1")
        store = ArtifactStore(_artifact_root=root)
        # different-owner/t1 doesn't exist → safe no-op
        store.delete_task("different-owner", "t1")
        # Original task still exists
        assert (root / "owner1" / "t1").is_dir()

    def test_owner_mismatch_list_empty(self, tmp_path):
        """List with wrong skill_id → returns empty (directory not found)."""
        art, root = _publish_fixture(tmp_path, "owner2", "t2")
        store = ArtifactStore(_artifact_root=root)
        # other-skill/t2 doesn't exist → returns empty
        result = store.list_task("other-skill", "t2")
        assert result == ()

    def test_hash_mismatch_excluded(self, tmp_path):
        """Artifact with corrupt file content (wrong hash) → excluded from list (fail-closed)."""
        art, root = _publish_fixture(tmp_path, "hash1", "t1")

        # Corrupt the artifact file
        task_dir = root / "hash1" / "t1"
        artifact_files = list(task_dir.glob("*_data.json"))
        if artifact_files:
            artifact_files[0].write_bytes(b"corrupted content")

        store = ArtifactStore(_artifact_root=root)
        # Should return empty because artifact fails hash check
        result = store.list_task("hash1", "t1")
        assert len(result) == 0

    # ── Platform-conditional tests: symlink / reparse rejection ──

    def test_export_target_symlink_rejected(self, tmp_path):
        """Export to a symlink/reparse target → rejected.

        On Windows this creates a real NTFS junction as the export
        target; on POSIX it creates a real symlink.  No skip, no
        fallback — creation failure is a hard test failure.
        """
        art, root = _publish_fixture(tmp_path, "sym1", "t1")

        real_target = tmp_path / "real.txt"
        real_target.write_text("real")

        if os.name == "nt":
            # Windows: create a real NTFS junction as export target
            from tests.test_runtime_l3_artifact_security import (
                _create_ntfs_junction,
            )
            dummy_dir = tmp_path / "dummy_export_target"
            dummy_dir.mkdir()
            junction_target = tmp_path / "junction_target"
            _create_ntfs_junction(dummy_dir, junction_target)

            store = ArtifactStore(_artifact_root=root)
            with pytest.raises(RuntimeError, match="reparse"):
                store.export("sym1", "t1", art.artifact_id, junction_target, overwrite=True)
        else:
            # POSIX: create a real symlink as export target
            symlink_target = tmp_path / "symlink.txt"
            symlink_target.symlink_to(real_target)
            assert symlink_target.is_symlink(), "Must be a real symlink"

            store = ArtifactStore(_artifact_root=root)
            with pytest.raises(RuntimeError, match="symlink"):
                store.export("sym1", "t1", art.artifact_id, symlink_target, overwrite=True)

    def test_reparse_component_rejected(self, tmp_path):
        """ArtifactStore rejects a path chain containing a real reparse component.

        Publishes a legitimate artifact, then replaces a directory component
        in the artifact-root path chain with a real NTFS junction.  The
        ArtifactStore safety checks (verify_path_not_symlink_or_reparse on
        skill_dir + task_dir) must reject the request.

        On POSIX the same logic is exercised with a real symlink.
        """
        art, root = _publish_fixture(tmp_path, "reparse-skill", "task1")
        task_dir = root / "reparse-skill" / "task1"
        assert task_dir.is_dir(), "Published task dir must exist"

        store = ArtifactStore(_artifact_root=root)

        # ── Pre-check: normal access works ──
        result = store.list_task("reparse-skill", "task1")
        assert len(result) == 1

        if os.name == "nt":
            # ── Windows: replace skill dir with a real NTFS junction ──
            from tests.test_runtime_l3_artifact_security import (
                _create_ntfs_junction,
            )
            from dp_engine.skills.runtime_paths import (
                _is_symlink_or_reparse,
            )

            real_skill_dir = root / "reparse-skill"
            saved_dir = root / "reparse-skill_saved"
            os.rename(str(real_skill_dir), str(saved_dir))

            # Create a fake target mimicking the structure
            fake_target = tmp_path / "fake_reparse_target"
            fake_target.mkdir()
            (fake_target / "task1").mkdir()
            (fake_target / "task1" / "manifest.json").write_text(
                '{"skill_id":"reparse-skill","task_id":"task1","artifacts":[]}',
                encoding="utf-8",
            )

            junction_path = root / "reparse-skill"
            _create_ntfs_junction(fake_target, junction_path)

            try:
                # ── Prove it IS a reparse point ──
                assert _is_symlink_or_reparse(junction_path), (
                    "Junction must be detected as reparse"
                )

                # ── ArtifactStore must reject ──
                with pytest.raises(RuntimeError, match="reparse"):
                    store.list_task("reparse-skill", "task1")

                with pytest.raises(RuntimeError, match="reparse"):
                    store.export("reparse-skill", "task1", art.artifact_id,
                                 tmp_path / "out.json", overwrite=True)

                with pytest.raises(RuntimeError, match="reparse"):
                    store.delete_task("reparse-skill", "task1")

            finally:
                # ── Cleanup: rmdir junction (does NOT delete target) ──
                junction_path.rmdir()
                assert fake_target.exists(), (
                    "Junction target must survive rmdir"
                )
                # Restore the real directory
                os.rename(str(saved_dir), str(real_skill_dir))
        else:
            # ── POSIX: replace skill dir with a real symlink ──
            from dp_engine.skills.runtime_paths import (
                _is_symlink_or_reparse,
            )

            real_skill_dir = root / "reparse-skill"
            saved_dir = root / "reparse-skill_saved"
            os.rename(str(real_skill_dir), str(saved_dir))

            fake_target = tmp_path / "fake_symlink_target"
            fake_target.mkdir()
            (fake_target / "task1").mkdir()
            (fake_target / "task1" / "manifest.json").write_text(
                '{"skill_id":"reparse-skill","task_id":"task1","artifacts":[]}',
                encoding="utf-8",
            )

            symlink_path = root / "reparse-skill"
            symlink_path.symlink_to(fake_target)

            try:
                assert _is_symlink_or_reparse(symlink_path), (
                    "Symlink must be detected"
                )

                with pytest.raises(RuntimeError):
                    store.list_task("reparse-skill", "task1")

                with pytest.raises(RuntimeError):
                    store.export("reparse-skill", "task1", art.artifact_id,
                                 tmp_path / "out.json", overwrite=True)

                with pytest.raises(RuntimeError):
                    store.delete_task("reparse-skill", "task1")

            finally:
                symlink_path.unlink()
                assert fake_target.exists(), (
                    "Symlink target must survive unlink"
                )
                os.rename(str(saved_dir), str(real_skill_dir))

        # ── Post-check: restored path works again ──
        result2 = store.list_task("reparse-skill", "task1")
        assert len(result2) == 1

    def test_artifact_not_found(self, tmp_path):
        """Non-existent artifact_id → RuntimeError."""
        art, root = _publish_fixture(tmp_path, "nf1", "t1")
        store = ArtifactStore(_artifact_root=root)
        with pytest.raises(RuntimeError, match="not found"):
            store.locate("nf1", "t1", "nonexistent32bytehexstring12345678")
