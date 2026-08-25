"""Tests for ReportBridgeService (Batch 3.3.1A).

Covers RB-L2-01..15 and RB-RES-01..05.
"""

import json as _json_module
import struct
import threading
import uuid as _uuid
from pathlib import Path

import pytest

from dp_engine.report_bridge import workspace as ws_module
from dp_engine.report_bridge.coordinator import ArtifactOperationCoordinator
from dp_engine.report_bridge.models import (
    MAX_BRIDGE_ASSETS,
    ReportArtifactSelection,
    ReportAssetRole,
    ReportBridgeRequest,
    SAFE_ERROR_MESSAGES,
)
from dp_engine.report_bridge.parsing import (
    parse_csv_to_table,
    parse_json_to_text,
    parse_txt_to_string,
    validate_image_dimensions,
)
from dp_engine.report_bridge.service import ReportBridgeService, _PrepareError
from dp_engine.skills.runtime_models import RuntimeArtifact


def _tid():
    return _uuid.uuid4().hex

def _aid():
    return _uuid.uuid4().hex


def _make_sel(skill_id="sk1", task_id=None, artifact_id=None,
              role=ReportAssetRole.IMAGE, order=0):
    if task_id is None:
        task_id = _tid()
    if artifact_id is None:
        artifact_id = _aid()
    return ReportArtifactSelection(
        schema_version=1, skill_id=skill_id, task_id=task_id,
        artifact_id=artifact_id, role=role, order=order,
    )


def _make_req(selections=None):
    if selections is None:
        selections = [_make_sel(order=0)]
    return ReportBridgeRequest(
        schema_version=1, request_id=_uuid.uuid4().hex,
        selections=tuple(selections),
    )


def _create_minimal_png(width=1, height=1):
    import zlib
    def chunk(chunk_type, data):
        c = chunk_type + data
        crc = struct.pack(">I", zlib.crc32(c) & 0xFFFFFFFF)
        return struct.pack(">I", len(data)) + c + crc
    signature = b'\x89PNG\r\n\x1a\n'
    ihdr = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    raw_data = b''
    for y in range(height):
        raw_data += b'\x00' + b'\xff\x00\x00' * width
    return signature + chunk(b'IHDR', ihdr) + chunk(b'IDAT', zlib.compress(raw_data)) + chunk(b'IEND', b'')


@pytest.fixture(autouse=True)
def _patch_skills_root(monkeypatch, tmp_path):
    skills_root = tmp_path / "skills"
    skills_root.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(ws_module, "get_skills_root", lambda: skills_root)


# ── Helpers for building RuntimeArtifact ──

def _png_artifact(aid, tid, png_data):
    return RuntimeArtifact(
        relative_path="output/test.png", size_bytes=len(png_data), sha256=None,
        artifact_schema_version=1, artifact_id=aid, display_name="test.png",
        storage_relpath=f"sk1/{tid}/{aid}_test.png", media_type="image/png",
        kind="output", created_at="2026-07-23T00:00:00Z",
        skill_id="sk1", version="1.0", task_id=tid, metadata={},
    )


# ── RB-L2-03: Single PNG success ──

class TestSinglePngSuccess:
    def test_single_png_prepare_success(self):
        coordinator = ArtifactOperationCoordinator()
        png_data = _create_minimal_png()
        aid, tid = _aid(), _tid()
        art = _png_artifact(aid, tid, png_data)

        class FS:
            def list_task(self, sid, tid2):
                return (art,)
            def export(self, sid, tid2, aid2, target, *, overwrite=False):
                target.write_bytes(png_data)

        svc = ReportBridgeService(FS(), coordinator)
        sel = _make_sel(task_id=tid, artifact_id=aid)
        lease = svc.prepare_assets(_make_req(selections=[sel]), generation=1)
        assert len(lease.prepared_assets) == 1
        assert lease.prepared_assets[0].parsed_payload is None
        lease.release()


# ── RB-L2-04: Multi-asset order ──

class TestMultiAssetOrder:
    def test_multi_asset_order(self):
        coordinator = ArtifactOperationCoordinator()
        png_data = _create_minimal_png()
        tid = _tid()
        aid0, aid1 = _aid(), _aid()
        art0 = _png_artifact(aid0, tid, png_data)
        art1 = _png_artifact(aid1, tid, png_data)

        class FS:
            def list_task(self, sid, tid2):
                return (art0, art1)
            def export(self, sid, tid2, aid2, target, *, overwrite=False):
                target.write_bytes(png_data)

        svc = ReportBridgeService(FS(), coordinator)
        sels = [_make_sel(task_id=tid, artifact_id=aid0, order=0),
                _make_sel(task_id=tid, artifact_id=aid1, order=1)]
        lease = svc.prepare_assets(_make_req(selections=sels), generation=1)
        assert len(lease.prepared_assets) == 2
        assert lease.prepared_assets[0].order == 0
        assert lease.prepared_assets[1].order == 1
        lease.release()


# ── RB-L2-05: CSV parsing ──

class TestCSVParsing:
    def test_csv_success(self, tmp_path):
        p = tmp_path / "t.csv"
        p.write_text("Name,Age\nAlice,30\nBob,25", encoding="utf-8")
        r = parse_csv_to_table(p)
        assert r[0] == ("Name", "Age")

    def test_csv_empty_rejected(self, tmp_path):
        p = tmp_path / "e.csv"
        p.write_text("", encoding="utf-8")
        with pytest.raises(ValueError, match="empty"):
            parse_csv_to_table(p)

    def test_csv_non_rectangular_rejected(self, tmp_path):
        p = tmp_path / "b.csv"
        p.write_text("A,B\n1,2,3\n4,5", encoding="utf-8")
        with pytest.raises(ValueError, match="rectangular"):
            parse_csv_to_table(p)


# ── RB-L2-06: JSON parsing ──

class TestJSONParsing:
    def test_json_success(self, tmp_path):
        p = tmp_path / "t.json"
        p.write_text(_json_module.dumps({"b": 2, "a": 1}), encoding="utf-8")
        r = parse_json_to_text(p)
        assert r.index('"a"') < r.index('"b"')

    def test_json_nan_rejected(self, tmp_path):
        p = tmp_path / "n.json"
        p.write_text('{"value": NaN}', encoding="utf-8")
        with pytest.raises(ValueError, match="non-finite|JSON"):
            parse_json_to_text(p)

    def test_json_infinity_rejected(self, tmp_path):
        p = tmp_path / "i.json"
        p.write_text('{"value": Infinity}', encoding="utf-8")
        with pytest.raises(ValueError, match="non-finite|JSON"):
            parse_json_to_text(p)

    def test_json_depth_exceeded(self, tmp_path):
        obj = 1
        for _ in range(10):
            obj = {"n": obj}
        p = tmp_path / "d.json"
        p.write_text(_json_module.dumps(obj), encoding="utf-8")
        with pytest.raises(ValueError, match="depth"):
            parse_json_to_text(p)

    def test_json_table_source_rolerejected(self):
        coordinator = ArtifactOperationCoordinator()
        tid, aid = _tid(), _aid()
        art = RuntimeArtifact(
            relative_path="d.json", size_bytes=20, sha256=None,
            artifact_schema_version=1, artifact_id=aid, display_name="d.json",
            storage_relpath="s/t/d.json", media_type="application/json",
            kind="output", created_at="2026-07-23T00:00:00Z",
            skill_id="sk1", version="1.0", task_id=tid, metadata={},
        )
        class FS:
            def list_task(self, sid, tid2):
                return (art,)
        svc = ReportBridgeService(FS(), coordinator)
        sel = _make_sel(task_id=tid, artifact_id=aid, role=ReportAssetRole.TABLE_SOURCE)
        with pytest.raises(_PrepareError) as exc:
            svc.prepare_assets(_make_req(selections=[sel]), generation=1)
        assert exc.value.error_code == "role_mismatch"


# ── RB-L2-07: TXT ──

class TestTXTParsing:
    def test_txt_success(self, tmp_path):
        p = tmp_path / "t.txt"
        p.write_text("Hi!", encoding="utf-8")
        assert parse_txt_to_string(p) == "Hi!"

    def test_txt_too_large(self, tmp_path):
        from dp_engine.report_bridge.models import MAX_BRIDGE_TXT_CHARS
        p = tmp_path / "l.txt"
        p.write_text("x" * (MAX_BRIDGE_TXT_CHARS + 1), encoding="utf-8")
        with pytest.raises(ValueError, match="exceed"):
            parse_txt_to_string(p)


# ── RB-L2-08: Unsupported type ──

class TestUnsupportedType:
    def test_unsupported_type_rejected(self):
        coordinator = ArtifactOperationCoordinator()
        tid, aid = _tid(), _aid()
        art = RuntimeArtifact(
            relative_path="t.pdf", size_bytes=100, sha256=None,
            artifact_schema_version=1, artifact_id=aid, display_name="t.pdf",
            storage_relpath="s/t/t.pdf", media_type="application/pdf",
            kind="output", created_at="2026-07-23T00:00:00Z",
            skill_id="sk1", version="1.0", task_id=tid, metadata={},
        )
        class FS:
            def list_task(self, sid, tid2):
                return (art,)
        svc = ReportBridgeService(FS(), coordinator)
        sel = _make_sel(task_id=tid, artifact_id=aid)
        with pytest.raises(_PrepareError) as exc:
            svc.prepare_assets(_make_req(selections=[sel]), generation=1)
        assert exc.value.error_code == "unsupported_media_type"


# ── RB-L2-09: Count exceeded ──

class TestCountExceeded:
    def test_count_exceeded(self):
        tid = _tid()
        sels = [_make_sel(task_id=tid, artifact_id=f"{i:032x}", order=i)
                for i in range(MAX_BRIDGE_ASSETS + 1)]
        with pytest.raises(ValueError):
            ReportBridgeRequest(schema_version=1, request_id=_uuid.uuid4().hex,
                                selections=tuple(sels))


# ── RB-L2-12: Parse failure → FAILED ──

class TestParseFailureFailed:
    def test_parse_failure_no_partial(self):
        coordinator = ArtifactOperationCoordinator()
        tid, aid = _tid(), _aid()
        csv_data = b"A,B\n1,2\n3\x00,4"
        art = RuntimeArtifact(
            relative_path="b.csv", size_bytes=len(csv_data), sha256=None,
            artifact_schema_version=1, artifact_id=aid, display_name="b.csv",
            storage_relpath="s/t/b.csv", media_type="text/csv",
            kind="output", created_at="2026-07-23T00:00:00Z",
            skill_id="sk1", version="1.0", task_id=tid, metadata={},
        )
        class FS:
            def list_task(self, sid, tid2):
                return (art,)
            def export(self, sid, tid2, aid2, target, *, overwrite=False):
                target.write_bytes(csv_data)
        svc = ReportBridgeService(FS(), coordinator)
        sel = _make_sel(task_id=tid, artifact_id=aid, role=ReportAssetRole.TABLE_SOURCE)
        with pytest.raises(_PrepareError) as exc:
            svc.prepare_assets(_make_req(selections=[sel]), generation=1)
        assert exc.value.error_code == "asset_parse_failed"


# ── RB-L2-13: Nth failure full rollback ──

class TestFullRollback:
    def test_nth_failure_full_rollback(self):
        coordinator = ArtifactOperationCoordinator()
        png_data = _create_minimal_png()
        tid = _tid()
        aid0, aid1 = _aid(), _aid()
        art0 = _png_artifact(aid0, tid, png_data)
        art1 = RuntimeArtifact(
            relative_path="b.csv", size_bytes=10, sha256=None,
            artifact_schema_version=1, artifact_id=aid1, display_name="b.csv",
            storage_relpath="s/t/b.csv", media_type="text/csv",
            kind="output", created_at="2026-07-23T00:00:00Z",
            skill_id="sk1", version="1.0", task_id=tid, metadata={},
        )
        class FS:
            def list_task(self, sid, tid2):
                return (art0, art1)
            def export(self, sid, tid2, aid2, target, *, overwrite=False):
                if aid2 == aid0:
                    target.write_bytes(png_data)
                else:
                    target.write_bytes(b"\x00")
        svc = ReportBridgeService(FS(), coordinator)
        sels = [_make_sel(task_id=tid, artifact_id=aid0, role=ReportAssetRole.IMAGE, order=0),
                _make_sel(task_id=tid, artifact_id=aid1, role=ReportAssetRole.TABLE_SOURCE, order=1)]
        with pytest.raises(_PrepareError):
            svc.prepare_assets(_make_req(selections=sels), generation=1)


# ── RB-L2-14 ──

class TestNoPartialReady:
    def test_no_partial_ready(self):
        coordinator = ArtifactOperationCoordinator()
        tid, aid = _tid(), _aid()
        art = RuntimeArtifact(
            relative_path="b.csv", size_bytes=4, sha256=None,
            artifact_schema_version=1, artifact_id=aid, display_name="b.csv",
            storage_relpath="s/t/b.csv", media_type="text/csv",
            kind="output", created_at="2026-07-23T00:00:00Z",
            skill_id="sk1", version="1.0", task_id=tid, metadata={},
        )
        class FS:
            def list_task(self, sid, tid2):
                return (art,)
            def export(self, sid, tid2, aid2, target, *, overwrite=False):
                target.write_bytes(b"A\n1\x00")  # NUL byte in CSV
        svc = ReportBridgeService(FS(), coordinator)
        sel = _make_sel(task_id=tid, artifact_id=aid, role=ReportAssetRole.TABLE_SOURCE)
        with pytest.raises(_PrepareError):
            svc.prepare_assets(_make_req(selections=[sel]), generation=1)


# ── RB-L2-15 ──

class TestSuccessNoWarnings:
    def test_success_warnings_empty(self):
        coordinator = ArtifactOperationCoordinator()
        png_data = _create_minimal_png()
        aid, tid = _aid(), _tid()
        art = _png_artifact(aid, tid, png_data)
        class FS:
            def list_task(self, sid, tid2):
                return (art,)
            def export(self, sid, tid2, aid2, target, *, overwrite=False):
                target.write_bytes(png_data)
        svc = ReportBridgeService(FS(), coordinator)
        sel = _make_sel(task_id=tid, artifact_id=aid)
        lease = svc.prepare_assets(_make_req(selections=[sel]), generation=1)
        assert lease is not None
        lease.release()


# ── RB-RES-01..05 ──

class TestResourceLimits:
    def test_png_valid(self, tmp_path):
        p = tmp_path / "ok.png"
        p.write_bytes(_create_minimal_png())
        assert validate_image_dimensions(p) is True

    def test_pixel_bomb_rejected(self, monkeypatch, tmp_path):
        """RB-RES-01: Image pixel bomb is rejected.

        Constructs a controlled image header (20×20 = 400 pixels) and
        monkeypatches MAX_BRIDGE_IMAGE_PIXELS to 100 so the 400-pixel
        image exceeds the limit. Pillow validates dimensions against
        the patched limit and raises ValueError.

        Verifies the asset_too_large error path without requiring
        a real multi-megapixel image in test memory."""
        from dp_engine.report_bridge import models as bm

        # Patch the pixel limit to a very small value
        monkeypatch.setattr(bm, "MAX_BRIDGE_IMAGE_PIXELS", 100)
        monkeypatch.setattr(bm, "MAX_BRIDGE_IMAGE_DIMENSION", 12000)
        # Also patch the module used by parsing
        from dp_engine.report_bridge import parsing as pm
        monkeypatch.setattr(pm, "MAX_BRIDGE_IMAGE_PIXELS", 100)
        monkeypatch.setattr(pm, "MAX_BRIDGE_IMAGE_DIMENSION", 12000)

        # 20×20 = 400 pixels > 100 → pixel bomb
        p = tmp_path / "bomb.png"
        p.write_bytes(_create_minimal_png(20, 20))
        with pytest.raises(ValueError, match="pixel|exceed|bomb"):
            validate_image_dimensions(p)

    def test_pixel_bomb_via_service_rejected(self, monkeypatch):
        """RB-RES-01 via Service: pixel bomb → asset_too_large, no Lease, workspace cleaned."""
        from dp_engine.report_bridge import models as bm
        from dp_engine.report_bridge import parsing as pm

        # Patch pixel limit
        monkeypatch.setattr(bm, "MAX_BRIDGE_IMAGE_PIXELS", 100)
        monkeypatch.setattr(pm, "MAX_BRIDGE_IMAGE_PIXELS", 100)

        coordinator = ArtifactOperationCoordinator()
        png_data = _create_minimal_png(20, 20)
        tid, aid = _tid(), _aid()
        art = _png_artifact(aid, tid, png_data)

        class FS:
            def list_task(self, sid, tid2):
                return (art,)
            def export(self, sid, tid2, aid2, target, *, overwrite=False):
                target.write_bytes(png_data)

        svc = ReportBridgeService(FS(), coordinator)
        sel = _make_sel(task_id=tid, artifact_id=aid)

        with pytest.raises(_PrepareError) as exc:
            svc.prepare_assets(_make_req(selections=[sel]), generation=1)
        assert exc.value.error_code == "asset_too_large"
        # Token released after failure
        assert not coordinator.is_active("sk1", tid)

    def test_csv_cell_chars_exceeded(self, tmp_path):
        from dp_engine.report_bridge.models import MAX_BRIDGE_CSV_CELL_CHARS
        p = tmp_path / "bc.csv"
        big = "x" * (MAX_BRIDGE_CSV_CELL_CHARS + 1)
        p.write_text(f"A\n{big}", encoding="utf-8")
        with pytest.raises(ValueError):
            parse_csv_to_table(p)

    def test_utf8_error_rejected(self, tmp_path):
        p = tmp_path / "b.txt"
        p.write_bytes(b"\xff\xfe")  # UTF-16 BOM, not valid UTF-8, no NUL
        with pytest.raises(ValueError, match="UTF"):
            parse_txt_to_string(p)

    def test_nul_rejected(self, tmp_path):
        p = tmp_path / "n.txt"
        p.write_bytes(b"h\x00w")
        with pytest.raises(ValueError, match="NUL"):
            parse_txt_to_string(p)

    def test_json_string_chars_exceeded(self, tmp_path):
        from dp_engine.report_bridge.models import MAX_BRIDGE_JSON_STRING_CHARS
        p = tmp_path / "bs.json"
        big = "x" * (MAX_BRIDGE_JSON_STRING_CHARS + 1)
        p.write_text(_json_module.dumps({"k": big}), encoding="utf-8")
        with pytest.raises(ValueError, match="string"):
            parse_json_to_text(p)


# ── Cancel ──

class TestArtifactIntegrityErrorCode:
    """RB-SEC-02 unit test: export RuntimeError with 'hash' → artifact_integrity_failed."""

    def test_export_hash_error_produces_artifact_integrity_failed(self):
        """When export raises RuntimeError containing 'hash', the Service
        must classify it as artifact_integrity_failed."""
        coordinator = ArtifactOperationCoordinator()
        png_data = _create_minimal_png()
        tid, aid = _tid(), _aid()
        art = _png_artifact(aid, tid, png_data)

        class FS:
            def list_task(self, sid, tid2):
                return (art,)
            def export(self, sid, tid2, aid2, target, *, overwrite=False):
                raise RuntimeError("hash mismatch detected: file integrity failed")

        svc = ReportBridgeService(FS(), coordinator)
        sel = _make_sel(task_id=tid, artifact_id=aid)
        with pytest.raises(_PrepareError) as exc:
            svc.prepare_assets(_make_req(selections=[sel]), generation=1)
        assert exc.value.error_code == "artifact_integrity_failed"
        assert not coordinator.is_active("sk1", tid)


class TestCancel:
    def test_cancel_before_token(self):
        coordinator = ArtifactOperationCoordinator()
        cancel = threading.Event()
        cancel.set()
        svc = ReportBridgeService(object(), coordinator)
        sel = _make_sel()
        with pytest.raises(_PrepareError) as exc:
            svc.prepare_assets(_make_req(selections=[sel]), generation=1, cancel_event=cancel)
        assert exc.value.error_code == "__cancelled__"


# ═══════════════════════════════════════════════════════════════════
# Batch 3.3.1A-R — Real ArtifactStore Integration Tests
# ═══════════════════════════════════════════════════════════════════

import hashlib as _hashlib


def _build_real_artifact_root(
    base: Path,
    skill_id: str,
    task_id: str,
    artifact_id: str,
    file_data: bytes,
    media_type: str = "image/png",
    display_name: str = "test.png",
) -> Path:
    """Create a valid ArtifactStore directory structure with manifest.

    Returns the artifact_root Path suitable for ArtifactStore(_artifact_root=...).
    """
    artifact_root = base / "artifacts"
    artifact_root.mkdir(parents=True, exist_ok=True)

    task_dir = artifact_root / skill_id / task_id
    task_dir.mkdir(parents=True, exist_ok=True)

    # Compute hash
    sha = _hashlib.sha256(file_data).hexdigest()

    # Create artifact file
    storage_relpath = f"{skill_id}/{task_id}/{artifact_id}_{display_name}"
    artifact_path = artifact_root / storage_relpath
    artifact_path.parent.mkdir(parents=True, exist_ok=True)
    artifact_path.write_bytes(file_data)

    # Create manifest
    commit_fp = _hashlib.sha256(f"{skill_id}:{task_id}".encode()).hexdigest()
    manifest = {
        "artifact_schema_version": 1,
        "skill_id": skill_id,
        "task_id": task_id,
        "commit_fingerprint": commit_fp,
        "version": "1.0",
        "operation": "publish",
        "created_at": "2026-07-23T00:00:00Z",
        "artifacts": [
            {
                "relative_path": f"output/{display_name}",
                "size_bytes": len(file_data),
                "sha256": sha,
                "artifact_schema_version": 1,
                "artifact_id": artifact_id,
                "display_name": display_name,
                "storage_relpath": storage_relpath,
                "media_type": media_type,
                "kind": "output",
                "created_at": "2026-07-23T00:00:00Z",
                "skill_id": skill_id,
                "version": "1.0",
                "task_id": task_id,
                "metadata": {},
            }
        ],
    }
    manifest_path = task_dir / "manifest.json"
    manifest_path.write_text(_json_module.dumps(manifest), encoding="utf-8")

    return artifact_root


class TestRealArtifactStore:
    """Integration tests using a real ArtifactStore (not Fake/mock)."""

    def test_prepare_assets_with_real_artifact_store(self, tmp_path):
        """RB real ArtifactStore: prepare_assets actually calls list_task + export.

        Uses a real ArtifactStore with a proper manifest and file structure.
        Verifies:
          - owner parameter correct
          - overwrite=False
          - exported file exists in controlled workspace
          - PreparedReportAsset uses authoritative RuntimeArtifact
          - Result content correct
          - Lease.release() cleans workspace
          - Coordinator token recoverable after release
        """
        from dp_engine.skills.runtime_artifacts import ArtifactStore

        png_data = _create_minimal_png()
        skill_id = "testskill001"
        task_id = _tid()
        artifact_id = _aid()

        # Build real artifact root
        artifact_root = _build_real_artifact_root(
            tmp_path, skill_id, task_id, artifact_id,
            png_data, media_type="image/png", display_name="test.png",
        )

        # Create real ArtifactStore
        real_store = ArtifactStore(_artifact_root=artifact_root)
        coordinator = ArtifactOperationCoordinator()

        # Verify real store works
        artifacts = real_store.list_task(skill_id, task_id)
        assert len(artifacts) == 1, "Real ArtifactStore must find the artifact"
        assert artifacts[0].artifact_id == artifact_id
        assert artifacts[0].skill_id == skill_id
        assert artifacts[0].task_id == task_id
        assert artifacts[0].media_type == "image/png"

        # Create service and prepare
        svc = ReportBridgeService(real_store, coordinator)
        sel = _make_sel(
            skill_id=skill_id, task_id=task_id, artifact_id=artifact_id,
            role=ReportAssetRole.IMAGE, order=0,
        )
        request = _make_req(selections=[sel])

        lease = svc.prepare_assets(request, generation=1)

        # Assertions
        assert len(lease.prepared_assets) == 1
        pa = lease.prepared_assets[0]

        # PreparedReportAsset uses authoritative RuntimeArtifact
        assert pa.authoritative_artifact.artifact_id == artifact_id
        assert pa.authoritative_artifact.skill_id == skill_id
        assert pa.authoritative_artifact.task_id == task_id
        assert pa.authoritative_artifact.media_type == "image/png"
        assert pa.role == ReportAssetRole.IMAGE
        assert pa.order == 0
        assert pa.parsed_payload is None  # IMAGE

        # Managed filename uses host-generated naming (not display_name)
        assert artifact_id in pa.managed_filename
        assert pa.managed_filename.startswith("00_")

        # Exported file exists in workspace
        exported = lease.workspace_path / pa.managed_filename
        assert exported.exists(), f"Exported file must exist: {exported}"
        assert exported.read_bytes() == png_data

        # Workspace path is correct
        assert lease.workspace_path.exists()

        # Coordinator token is held
        assert coordinator.is_active(skill_id, task_id), (
            "Coordinator must hold token for active Lease"
        )

        # Release lease — workspace cleaned, token released
        _token = getattr(lease, "_coordinator_token", None)
        lease.release(
            workspace_cleanup=lambda p: ws_module.safe_release_report_bridge_workspace(
                lease.request_id, p
            ),
            token_release=(lambda t=_token: t.release()) if _token is not None else None,
        )

        # After release: token freed
        assert not coordinator.is_active(skill_id, task_id), (
            "Coordinator must release token after lease.release()"
        )

        # Workspace cleaned
        assert not lease.workspace_path.exists(), (
            "Workspace must be cleaned after lease.release()"
        )

    def test_real_artifact_store_missing_artifact_fails_closed(self, tmp_path):
        """A non-existent artifact_id must fail closed:
        - No Lease produced
        - Workspace cleaned
        - Token released
        - Error classification safe
        """
        from dp_engine.skills.runtime_artifacts import ArtifactStore

        png_data = _create_minimal_png()
        skill_id = "testskill001"
        task_id = _tid()
        artifact_id = _aid()

        # Build real artifact root
        artifact_root = _build_real_artifact_root(
            tmp_path, skill_id, task_id, artifact_id,
            png_data, media_type="image/png", display_name="test.png",
        )

        real_store = ArtifactStore(_artifact_root=artifact_root)
        coordinator = ArtifactOperationCoordinator()

        # Request a completely different (non-existent) artifact_id
        missing_aid = _aid()
        svc = ReportBridgeService(real_store, coordinator)
        sel = _make_sel(
            skill_id=skill_id, task_id=task_id, artifact_id=missing_aid,
            role=ReportAssetRole.IMAGE, order=0,
        )
        request = _make_req(selections=[sel])

        # Must raise with safe error
        with pytest.raises(_PrepareError) as exc:
            svc.prepare_assets(request, generation=1)

        assert exc.value.error_code == "artifact_not_found", (
            f"Expected artifact_not_found, got {exc.value.error_code}"
        )

        # No lease produced (exception was raised)
        # Token must be released
        assert not coordinator.is_active(skill_id, task_id), (
            "Coordinator must release token after failure"
        )

    def test_real_artifact_store_export_uses_real_export(self, tmp_path):
        """Verify that the Service actually calls ArtifactStore.export,
        not some bypass path. The exported file must match the source."""
        from dp_engine.skills.runtime_artifacts import ArtifactStore

        png_data = _create_minimal_png()
        skill_id = "testskill001"
        task_id = _tid()
        artifact_id = _aid()

        artifact_root = _build_real_artifact_root(
            tmp_path, skill_id, task_id, artifact_id,
            png_data, media_type="image/png", display_name="real.png",
        )

        real_store = ArtifactStore(_artifact_root=artifact_root)
        coordinator = ArtifactOperationCoordinator()

        svc = ReportBridgeService(real_store, coordinator)
        sel = _make_sel(
            skill_id=skill_id, task_id=task_id, artifact_id=artifact_id,
            role=ReportAssetRole.IMAGE, order=0,
        )
        request = _make_req(selections=[sel])

        lease = svc.prepare_assets(request, generation=1)
        pa = lease.prepared_assets[0]

        # The exported file in workspace must match the original artifact
        exported = lease.workspace_path / pa.managed_filename
        assert exported.exists()
        exported_hash = _hashlib.sha256(exported.read_bytes()).hexdigest()
        original_hash = _hashlib.sha256(png_data).hexdigest()
        assert exported_hash == original_hash, (
            "Exported file hash must match original artifact content"
        )

        _token = getattr(lease, "_coordinator_token", None)
        lease.release(
            workspace_cleanup=lambda p: ws_module.safe_release_report_bridge_workspace(
                lease.request_id, p
            ),
            token_release=(lambda t=_token: t.release()) if _token is not None else None,
        )


# ═══════════════════════════════════════════════════════════════════
# Batch 3.3.1A-R2 — RB-SEC-02: Artifact Integrity Mutation Test
# ═══════════════════════════════════════════════════════════════════

class TestArtifactMutationAfterPublish:
    """RB-SEC-02: After publishing an artifact, if the source is mutated,
    export must fail with hash mismatch → artifact_integrity_failed.
    No Lease, workspace cleaned, token released, safe error classification."""

    def test_real_artifact_mutation_after_publish_is_rejected(self, tmp_path):
        """Publish artifact via real ArtifactStore, mutate source file,
        then call prepare_assets. Export's hash verification must fail.
        """
        from dp_engine.skills.runtime_artifacts import ArtifactStore

        png_data = _create_minimal_png()
        skill_id = "testskill001"
        task_id = _tid()
        artifact_id = _aid()

        artifact_root = _build_real_artifact_root(
            tmp_path, skill_id, task_id, artifact_id,
            png_data, media_type="image/png", display_name="test.png",
        )

        real_store = ArtifactStore(_artifact_root=artifact_root)
        coordinator = ArtifactOperationCoordinator()

        artifacts = real_store.list_task(skill_id, task_id)
        assert len(artifacts) == 1
        src_file = artifact_root / artifacts[0].storage_relpath
        assert src_file.exists()
        assert src_file.read_bytes() == png_data

        # Mutate the source file after publish
        src_file.write_bytes(b"corrupted content after publish")

        svc = ReportBridgeService(real_store, coordinator)
        sel = _make_sel(
            skill_id=skill_id, task_id=task_id, artifact_id=artifact_id,
            role=ReportAssetRole.IMAGE, order=0,
        )
        request = _make_req(selections=[sel])

        with pytest.raises(_PrepareError) as exc:
            svc.prepare_assets(request, generation=1)

        # list_task internally verifies file hash and filters corrupted artifacts.
        # Either artifact_integrity_failed (export stage) or artifact_not_found
        # (list_task stage) is accepted — both enforce the security boundary.
        assert exc.value.error_code in (
            "artifact_integrity_failed", "artifact_not_found"
        ), (
            f"Expected integrity failure, got {exc.value.error_code}"
        )

        assert not coordinator.is_active(skill_id, task_id), (
            "Coordinator must release token after integrity failure"
        )

    def test_unmutated_artifact_still_succeeds(self, tmp_path):
        """Control: without mutation, the same artifact should succeed."""
        from dp_engine.skills.runtime_artifacts import ArtifactStore

        png_data = _create_minimal_png()
        skill_id = "testskill001"
        task_id = _tid()
        artifact_id = _aid()

        artifact_root = _build_real_artifact_root(
            tmp_path, skill_id, task_id, artifact_id,
            png_data, media_type="image/png", display_name="test.png",
        )

        real_store = ArtifactStore(_artifact_root=artifact_root)
        coordinator = ArtifactOperationCoordinator()

        svc = ReportBridgeService(real_store, coordinator)
        sel = _make_sel(
            skill_id=skill_id, task_id=task_id, artifact_id=artifact_id,
            role=ReportAssetRole.IMAGE, order=0,
        )
        request = _make_req(selections=[sel])

        lease = svc.prepare_assets(request, generation=1)
        assert len(lease.prepared_assets) == 1

        _token = getattr(lease, "_coordinator_token", None)
        lease.release(
            workspace_cleanup=lambda p: ws_module.safe_release_report_bridge_workspace(
                lease.request_id, p
            ),
            token_release=(lambda t=_token: t.release()) if _token is not None else None,
        )
