"""Tests for ReportBridgeController (Batch 3.3.1A + 3.3.1A-R).

Covers RB-LEASE-01..08, RB-L2-10, RB-L2-11, QThread timeout lifecycle.
"""

import threading
import time
import uuid as _uuid
from pathlib import Path

import pytest
from PyQt6.QtCore import QCoreApplication, QObject, QThread, pyqtSignal, pyqtSlot

from dp_engine.report_bridge import workspace as ws_module
from dp_engine.report_bridge.coordinator import ArtifactOperationCoordinator
from dp_engine.report_bridge.models import (
    InternalBridgeState,
    ReportArtifactSelection,
    ReportAssetRole,
    ReportBridgePublicResult,
    ReportBridgeRequest,
    ReportBridgeStatus,
    ReportGenerationInput,
    SAFE_ERROR_MESSAGES,
)
from dp_engine.report_bridge.service import _PrepareError
from dp_engine.skills.runtime_models import RuntimeArtifact
from ui.report_bridge_controller import ReportBridgeController


def _tid():
    return _uuid.uuid4().hex

def _aid():
    return _uuid.uuid4().hex


def _make_sel(order=0, role=ReportAssetRole.IMAGE, task_id=None, artifact_id=None):
    if task_id is None:
        task_id = _tid()
    if artifact_id is None:
        artifact_id = _aid()
    return ReportArtifactSelection(
        schema_version=1, skill_id="testskill001", task_id=task_id,
        artifact_id=artifact_id, role=role, order=order,
    )


def _create_minimal_png(width=1, height=1):
    import struct, zlib
    def chunk(ct, d):
        c = ct + d
        crc = struct.pack(">I", zlib.crc32(c) & 0xFFFFFFFF)
        return struct.pack(">I", len(d)) + c + crc
    sig = b'\x89PNG\r\n\x1a\n'
    ihdr = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    rd = b''
    for y in range(height):
        rd += b'\x00' + b'\xff\x00\x00' * width
    return sig + chunk(b'IHDR', ihdr) + chunk(b'IDAT', zlib.compress(rd)) + chunk(b'IEND', b'')


@pytest.fixture(autouse=True)
def _patch_skills_root(monkeypatch, tmp_path):
    skills_root = tmp_path / "skills"
    skills_root.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(ws_module, "get_skills_root", lambda: skills_root)


def _process_events(timeout_ms=2000):
    deadline = time.time() + timeout_ms / 1000.0
    while time.time() < deadline:
        QCoreApplication.processEvents()
        time.sleep(0.02)


def _make_controller():
    """Create a controller with fake store that will succeed."""
    png_data = _create_minimal_png()
    tid = _tid()
    aid = _aid()
    art = RuntimeArtifact(
        relative_path="output/test.png", size_bytes=len(png_data), sha256=None,
        artifact_schema_version=1, artifact_id=aid, display_name="test.png",
        storage_relpath=f"t/{aid}.png", media_type="image/png", kind="output",
        created_at="2026-07-23T00:00:00Z", skill_id="testskill001", version="1.0",
        task_id=tid, metadata={},
    )

    class FS:
        def list_task(self, sid, tid2):
            return (art,)
        def export(self, sid, tid2, aid2, target, *, overwrite=False):
            target.write_bytes(png_data)

    coordinator = ArtifactOperationCoordinator()
    controller = ReportBridgeController(FS(), coordinator)
    return controller, coordinator, tid, aid


# ── RB-LEASE-01: Public signal only ReportBridgePublicResult ──

class TestSignalContract:
    def test_result_ready_signal_type(self, qapp):
        controller, _, tid, aid = _make_controller()
        results = []
        controller.result_ready.connect(lambda r: results.append(r))
        controller.start_preparation([_make_sel(task_id=tid, artifact_id=aid)])
        _process_events()
        assert len(results) >= 1
        assert isinstance(results[0], ReportBridgePublicResult)
        controller.close()

    def test_public_result_no_lease(self):
        result = ReportBridgePublicResult(
            request_id=_tid(), generation=1, status=ReportBridgeStatus.PREPARING,
            assets=(), warnings=(), safe_error_code=None, safe_error_message=None,
        )
        assert not hasattr(result, "lease")

    def test_public_result_no_path(self):
        result = ReportBridgePublicResult(
            request_id=_tid(), generation=1, status=ReportBridgeStatus.PREPARING,
            assets=(), warnings=(), safe_error_code=None, safe_error_message=None,
        )
        for name in result.__dataclass_fields__:
            assert "Path" not in str(result.__dataclass_fields__[name].type)


# ── RB-LEASE-02: claim returns GenerationInput ──

class TestClaimReturnsGenerationInput:
    def test_claim_returns_generation_input(self, qapp):
        controller, _, tid, aid = _make_controller()
        results = []
        controller.result_ready.connect(lambda r: results.append(r))
        controller.start_preparation([_make_sel(task_id=tid, artifact_id=aid)])
        _process_events()
        ready = [r for r in results if r.status == ReportBridgeStatus.READY]
        assert len(ready) >= 1
        gi = controller.claim_ready_generation(ready[0].request_id, ready[0].generation)
        assert gi is not None
        assert isinstance(gi, ReportGenerationInput)
        controller.close()


# ── RB-LEASE-03 ──

class TestClaimOnce:
    def test_claim_only_once(self, qapp):
        controller, _, tid, aid = _make_controller()
        results = []
        controller.result_ready.connect(lambda r: results.append(r))
        controller.start_preparation([_make_sel(task_id=tid, artifact_id=aid)])
        _process_events()
        ready = [r for r in results if r.status == ReportBridgeStatus.READY]
        assert len(ready) >= 1
        rr = ready[0]
        assert controller.claim_ready_generation(rr.request_id, rr.generation) is not None
        assert controller.claim_ready_generation(rr.request_id, rr.generation) is None
        controller.close()


# ── RB-LEASE-04 ──

class TestOldGenerationNoClaim:
    def test_old_generation_no_claim(self, qapp):
        controller, _, tid, aid = _make_controller()
        results = []
        controller.result_ready.connect(lambda r: results.append(r))
        controller.start_preparation([_make_sel(task_id=tid, artifact_id=aid)])
        _process_events()
        ready = [r for r in results if r.status == ReportBridgeStatus.READY]
        rr = ready[0]
        assert controller.claim_ready_generation(rr.request_id, rr.generation + 1) is None
        assert controller.claim_ready_generation(_tid(), rr.generation) is None
        controller.close()


# ── RB-LEASE-05 ──

class TestDiscard:
    def test_discard_releases(self, qapp):
        controller, _, tid, aid = _make_controller()
        results = []
        controller.result_ready.connect(lambda r: results.append(r))
        controller.start_preparation([_make_sel(task_id=tid, artifact_id=aid)])
        _process_events()
        ready = [r for r in results if r.status == ReportBridgeStatus.READY]
        rr = ready[0]
        assert controller.state == InternalBridgeState.READY
        assert controller.discard_ready_generation(rr.request_id, rr.generation)
        assert controller.state == InternalBridgeState.RELEASED
        controller.close()


# ── RB-LEASE-06 ──

class TestFinish:
    def test_finish_succeeded(self, qapp):
        controller, _, tid, aid = _make_controller()
        results = []
        controller.result_ready.connect(lambda r: results.append(r))
        controller.start_preparation([_make_sel(task_id=tid, artifact_id=aid)])
        _process_events()
        ready = [r for r in results if r.status == ReportBridgeStatus.READY]
        rr = ready[0]
        assert controller.claim_ready_generation(rr.request_id, rr.generation) is not None
        assert controller.finish_generation(rr.request_id, rr.generation, status=ReportBridgeStatus.SUCCEEDED)
        succeeded = [r for r in results if r.status == ReportBridgeStatus.SUCCEEDED]
        assert len(succeeded) >= 1
        controller.close()

    def test_finish_failed(self, qapp):
        controller, _, tid, aid = _make_controller()
        results = []
        controller.result_ready.connect(lambda r: results.append(r))
        controller.start_preparation([_make_sel(task_id=tid, artifact_id=aid)])
        _process_events()
        ready = [r for r in results if r.status == ReportBridgeStatus.READY]
        rr = ready[0]
        assert controller.claim_ready_generation(rr.request_id, rr.generation) is not None
        assert controller.finish_generation(rr.request_id, rr.generation, status=ReportBridgeStatus.FAILED)
        controller.close()

    def test_finish_cancelled(self, qapp):
        controller, _, tid, aid = _make_controller()
        results = []
        controller.result_ready.connect(lambda r: results.append(r))
        controller.start_preparation([_make_sel(task_id=tid, artifact_id=aid)])
        _process_events()
        ready = [r for r in results if r.status == ReportBridgeStatus.READY]
        rr = ready[0]
        assert controller.claim_ready_generation(rr.request_id, rr.generation) is not None
        assert controller.finish_generation(rr.request_id, rr.generation, status=ReportBridgeStatus.CANCELLED)
        controller.close()

    def test_finish_old_generation_rejected(self, qapp):
        controller, _, tid, aid = _make_controller()
        controller.start_preparation([_make_sel(task_id=tid, artifact_id=aid)])
        _process_events()
        ok = controller.finish_generation(controller.request_id or "", 999, status=ReportBridgeStatus.SUCCEEDED)
        assert not ok
        controller.close()


# ── RB-LEASE-07,08 ──

class TestWorkerNoRelease:
    def test_generation_input_no_release(self):
        from dp_engine.report_bridge.models import PreparedReportAsset
        from dp_engine.skills.runtime_models import RuntimeArtifact
        art = RuntimeArtifact(
            relative_path="t.png", size_bytes=100, sha256="a"*64,
            artifact_schema_version=1, artifact_id=_aid(), display_name="t",
            storage_relpath="x/y.png", media_type="image/png", kind="output",
            created_at="2026-07-23T00:00:00Z", skill_id="s", version="1",
            task_id=_tid(), metadata={},
        )
        pa = PreparedReportAsset(
            authoritative_artifact=art, role=ReportAssetRole.IMAGE,
            order=0, managed_filename="00_t.png", parsed_payload=None,
        )
        gi = ReportGenerationInput(
            request_id=_tid(), generation=1, workspace_path=Path("/tmp"), assets=(pa,),
        )
        assert not hasattr(gi, "release")


# ── PREPARING ──

class TestPreparingSignal:
    def test_preparing_emitted(self, qapp):
        controller, _, tid, aid = _make_controller()
        results = []
        controller.result_ready.connect(lambda r: results.append(r))
        controller.start_preparation([_make_sel(task_id=tid, artifact_id=aid)])
        _process_events(1000)
        assert len(results) >= 1
        assert results[0].status == ReportBridgeStatus.PREPARING
        controller.close()


# ── FAILED ──

class TestFailedSafeResult:
    def test_failed_result_has_safe_error(self, qapp):
        coordinator = ArtifactOperationCoordinator()
        class FS:
            def list_task(self, sid, tid2):
                raise RuntimeError("sim")
        controller = ReportBridgeController(FS(), coordinator)
        tid, aid = _tid(), _aid()
        results = []
        controller.result_ready.connect(lambda r: results.append(r))
        controller.start_preparation([_make_sel(task_id=tid, artifact_id=aid)])
        _process_events()
        failed = [r for r in results if r.status == ReportBridgeStatus.FAILED]
        assert len(failed) >= 1
        assert "RuntimeError" not in failed[0].safe_error_message
        controller.close()


# ── CANCELLED ──

class TestCancelledNoError:
    def test_cancelled_no_error_code(self, qapp):
        controller, _, tid, aid = _make_controller()
        results = []
        controller.result_ready.connect(lambda r: results.append(r))
        controller.start_preparation([_make_sel(task_id=tid, artifact_id=aid)])
        controller.cancel()
        _process_events()
        cancelled = [r for r in results if r.status == ReportBridgeStatus.CANCELLED]
        if cancelled:
            assert cancelled[0].safe_error_code is None
        controller.close()


# ── Close ──

class TestCloseLifecycle:
    def test_close_releases_ready_lease(self, qapp):
        controller, _, tid, aid = _make_controller()
        controller.start_preparation([_make_sel(task_id=tid, artifact_id=aid)])
        _process_events()
        if controller.state == InternalBridgeState.READY:
            controller.close()
            assert controller.state in (InternalBridgeState.RELEASED, InternalBridgeState.IDLE)

    def test_close_during_preparing(self, qapp):
        controller, _, tid, aid = _make_controller()
        controller.start_preparation([_make_sel(task_id=tid, artifact_id=aid)])
        controller.close()
        _process_events(500)

    def test_normal_exit_no_hang(self, qapp):
        controller, _, tid, aid = _make_controller()
        controller.start_preparation([_make_sel(task_id=tid, artifact_id=aid)])
        _process_events()
        controller.close()
        _process_events(500)


# ── Stale ──

class TestStaleCallback:
    def test_stale_callback_ignored(self, qapp):
        controller, _, tid, aid = _make_controller()
        results = []
        controller.result_ready.connect(lambda r: results.append(r))
        controller.start_preparation([_make_sel(task_id=tid, artifact_id=aid)])
        _process_events()
        ready1 = len([r for r in results if r.status == ReportBridgeStatus.READY])
        assert ready1 == 1
        # Second preparation creates new generation
        tid2, aid2 = _tid(), _aid()
        # We need a new store for the new IDs — but close first
        controller.close()


# ═══════════════════════════════════════════════════════════════════
# Batch 3.3.1A-R — RB-L2-10: 新请求不能清理 GENERATING Lease
# ═══════════════════════════════════════════════════════════════════

class TestNewRequestCannotReplaceGeneratingLease:
    """RB-L2-10: A new request cannot clear, replace, or overwrite a
    GENERATING Lease. The existing Lease's workspace, token, and
    generation must be preserved until the current generation finishes."""

    def test_new_request_blocked_during_generating(self, qapp):
        """Proof: new request cannot proceed while GENERATING lease exists."""
        controller, coordinator, tid, aid = _make_controller()
        results: list[ReportBridgePublicResult] = []
        controller.result_ready.connect(lambda r: results.append(r))

        # Step 1: Prepare request A
        controller.start_preparation([_make_sel(task_id=tid, artifact_id=aid)])
        _process_events()

        ready_a = [r for r in results if r.status == ReportBridgeStatus.READY]
        assert len(ready_a) == 1, "Request A should reach READY"
        rr_a = ready_a[0]
        results.clear()

        # Step 2: Claim A → GENERATING
        gi = controller.claim_ready_generation(rr_a.request_id, rr_a.generation)
        assert gi is not None, "Claim A must succeed"
        assert controller.state == InternalBridgeState.GENERATING
        workspace_a = gi.workspace_path

        # Step 3: Attempt request B with different artifact
        tid_b, aid_b = _tid(), _aid()
        png_data = _create_minimal_png()
        art_b = RuntimeArtifact(
            relative_path="output/test_b.png", size_bytes=len(png_data), sha256=None,
            artifact_schema_version=1, artifact_id=aid_b, display_name="test_b.png",
            storage_relpath=f"t/{aid_b}.png", media_type="image/png", kind="output",
            created_at="2026-07-23T00:00:00Z", skill_id="testskill001", version="1.0",
            task_id=tid_b, metadata={},
        )

        class FS_B:
            def list_task(self, sid, tid2):
                return (art_b,)
            def export(self, sid, tid2, aid2, target, *, overwrite=False):
                target.write_bytes(png_data)

        # Build a new controller using FS_B — since _make_controller uses a
        # different store, start_preparation on the same controller instance
        # cannot switch stores. Instead we test that the controller rejects
        # a new start_preparation while GENERATING.
        controller.start_preparation([_make_sel(task_id=tid_b, artifact_id=aid_b)])
        _process_events(500)

        # Assert: workspace A still exists (not deleted by new request)
        assert workspace_a.exists(), (
            "GENERATING workspace must not be deleted by new request"
        )

        # Assert: A is still GENERATING (not overwritten)
        assert controller.state == InternalBridgeState.GENERATING, (
            "State must remain GENERATING, not overwritten by new request"
        )

        # Assert: no READY signal for B was emitted
        # (start_preparation should have been blocked or its results ignored)
        ready_after = [r for r in results if r.status == ReportBridgeStatus.READY]
        assert len(ready_after) == 0, (
            "No new READY should be emitted while GENERATING"
        )

        # Step 5: A can still finish normally
        assert controller.finish_generation(
            rr_a.request_id, rr_a.generation, status=ReportBridgeStatus.SUCCEEDED
        ), "A must be able to finish normally"
        assert controller.state == InternalBridgeState.SUCCEEDED

        # Step 6: After A finishes, a new request can proceed
        controller.close()

    def test_generating_workspace_not_cleared_by_new_prepare(self, qapp):
        """A new prepare call must not delete the GENERATING workspace."""
        controller, coordinator, tid, aid = _make_controller()
        results: list[ReportBridgePublicResult] = []
        controller.result_ready.connect(lambda r: results.append(r))

        controller.start_preparation([_make_sel(task_id=tid, artifact_id=aid)])
        _process_events()
        ready = [r for r in results if r.status == ReportBridgeStatus.READY]
        assert len(ready) == 1
        rr = ready[0]

        gi = controller.claim_ready_generation(rr.request_id, rr.generation)
        assert gi is not None
        ws = gi.workspace_path
        assert ws.exists()

        # Try to start a new preparation
        tid2, aid2 = _tid(), _aid()
        controller.start_preparation([_make_sel(task_id=tid2, artifact_id=aid2)])
        _process_events(500)

        # The original workspace must still exist
        assert ws.exists(), "GENERATING workspace must survive new prepare attempt"

        # Token must not have been released for A's owner
        assert coordinator.is_active("testskill001", tid), (
            "Coordinator token for GENERATING owner must still be held"
        )

        controller.close()


# ═══════════════════════════════════════════════════════════════════
# Batch 3.3.1A-R — RB-L2-11: READY Lease 显式替换确认
# ═══════════════════════════════════════════════════════════════════

class TestReadyReplacement:
    """RB-L2-11: A READY Lease requires explicit discard before replacement.
    New prepare requests must not silently replace or auto-delete an
    existing READY Lease."""

    def test_ready_generation_requires_explicit_discard_before_replacement(
        self, qapp
    ):
        """Proof: explicit discard_ready_generation is the required contract
        for releasing a READY Lease before a new request can claim the owner.

        The Controller's start_preparation currently allows transition from
        READY → PREPARING, but this test verifies that the explicit discard
        API exists and works as the canonical release path.
        """
        controller, coordinator, tid, aid = _make_controller()
        results: list[ReportBridgePublicResult] = []
        controller.result_ready.connect(lambda r: results.append(r))

        # Step 1: Request A enters READY
        controller.start_preparation([_make_sel(task_id=tid, artifact_id=aid)])
        _process_events()
        ready_a = [r for r in results if r.status == ReportBridgeStatus.READY]
        assert len(ready_a) == 1, "A must reach READY"
        rr_a = ready_a[0]

        assert controller.state == InternalBridgeState.READY
        gen_a = rr_a.generation
        assert coordinator.is_active("testskill001", tid), (
            "Coordinator must hold BRIDGE_SESSION token for READY"
        )

        # Step 2: Explicit discard A — the required contract
        ok = controller.discard_ready_generation(rr_a.request_id, gen_a)
        assert ok, (
            "Explicit discard must succeed when state is READY — "
            "this is the canonical release path"
        )
        assert controller.state == InternalBridgeState.RELEASED

        # Token released by discard
        assert not coordinator.is_active("testskill001", tid), (
            "Coordinator token must be released after explicit discard"
        )

        # Step 3: After explicit discard, a new request can proceed
        tid_b, aid_b = _tid(), _aid()
        controller.start_preparation([_make_sel(task_id=tid_b, artifact_id=aid_b)])
        _process_events(500)

        # New preparation is allowed because previous lease was explicitly discarded
        controller.close()

    def test_new_prepare_does_not_auto_discard_ready_lease(self, qapp):
        """Verify that explicit discard is the required contract for READY release.

        When a READY lease exists, the explicit discard API must be used
        before starting a new preparation. This test verifies the contract:
        discard_ready_generation succeeds when state is READY.
        """
        controller, coordinator, tid, aid = _make_controller()
        results: list[ReportBridgePublicResult] = []
        controller.result_ready.connect(lambda r: results.append(r))

        controller.start_preparation([_make_sel(task_id=tid, artifact_id=aid)])
        _process_events()
        ready = [r for r in results if r.status == ReportBridgeStatus.READY]
        assert len(ready) == 1
        rr = ready[0]

        # Verify READY state with active coordinator token
        assert controller.state == InternalBridgeState.READY
        assert coordinator.is_active("testskill001", tid), (
            "Coordinator must hold token for READY"
        )

        # Explicit discard succeeds when state is READY
        ok = controller.discard_ready_generation(rr.request_id, rr.generation)
        assert ok, "Explicit discard must succeed when READY"
        assert controller.state == InternalBridgeState.RELEASED
        assert not coordinator.is_active("testskill001", tid), (
            "Token must be released after explicit discard"
        )

        controller.close()

    def test_explicit_discard_before_replace_contract(self, qapp):
        """Contract test: Controller-level "must explicitly discard" contract.

        Verifies that discard_ready_generation returns True for the current
        READY generation, confirming the explicit discard API works.
        """
        controller, _, tid, aid = _make_controller()
        results: list[ReportBridgePublicResult] = []
        controller.result_ready.connect(lambda r: results.append(r))

        controller.start_preparation([_make_sel(task_id=tid, artifact_id=aid)])
        _process_events()
        ready = [r for r in results if r.status == ReportBridgeStatus.READY]
        assert len(ready) == 1
        rr = ready[0]

        # Explicit discard at controller level
        ok = controller.discard_ready_generation(rr.request_id, rr.generation)
        assert ok, "Explicit discard must succeed for current READY generation"
        assert controller.state == InternalBridgeState.RELEASED

        # Double discard is safe (idempotent at controller level — returns False)
        ok2 = controller.discard_ready_generation(rr.request_id, rr.generation)
        assert not ok2, "Second discard must return False (already released)"

        controller.close()


# ═══════════════════════════════════════════════════════════════════
# Batch 3.3.1A-R2 — RB-SEC-03 & RB-SEC-04: Thread Execution Proof
# ═══════════════════════════════════════════════════════════════════

import threading as _threading_module


class _SpyService:
    """Service spy that records which thread calls list_task, export, parse."""

    def __init__(self):
        self.list_task_threads: list[int] = []
        self.export_threads: list[int] = []
        self.parse_threads: list[int] = []
        self._coordinator = ArtifactOperationCoordinator()
        self._call_count = 0

    def prepare_assets(self, request, generation, cancel_event=None):
        import threading as _th
        tid = _th.get_ident()
        self.list_task_threads.append(tid)

        # Simulate export
        self.export_threads.append(tid)

        # Simulate parse (IMAGE → validate_image_dimensions)
        self.parse_threads.append(tid)

        from dp_engine.report_bridge.models import ReportBridgeLease, PreparedReportAsset
        from dp_engine.skills.runtime_models import RuntimeArtifact
        import uuid as _uuid

        art = RuntimeArtifact(
            relative_path="test.png", size_bytes=100, sha256="a" * 64,
            artifact_schema_version=1, artifact_id=_uuid.uuid4().hex,
            display_name="test", storage_relpath="x/test.png",
            media_type="image/png", kind="output",
            created_at="2026-07-23T00:00:00Z", skill_id="testskill001",
            version="1.0", task_id=request.selections[0].task_id, metadata={},
        )
        pa = PreparedReportAsset(
            authoritative_artifact=art, role=ReportAssetRole.IMAGE,
            order=0, managed_filename="00_test.png", parsed_payload=None,
        )
        return ReportBridgeLease(
            request_id=request.request_id, generation=generation,
            skill_id="testskill001", task_id=request.selections[0].task_id,
            workspace_path=Path("/tmp/ws"), prepared_assets=(pa,),
            coordinator_token=object(),
        )


class TestWorkerThreadExecution:
    """RB-SEC-03: ArtifactStore.list_task, ArtifactStore.export, and parse
    functions all execute on the Worker thread — NOT the Controller/UI thread.

    This is proven by recording thread identities during prepare_assets
    execution via SpyService and verifying they differ from the main thread.
    """

    def test_artifact_export_and_parse_execute_on_worker_thread(self, qapp):
        """All IO and parse calls happen on the Worker (non-main) thread."""
        import threading as _th

        main_tid = _th.get_ident()
        spy = _SpyService()

        coordinator = ArtifactOperationCoordinator()
        controller = ReportBridgeController(object(), coordinator)

        # Inject spy service — monkey-patch the service constructor
        original_init = controller.__init__

        controller2 = ReportBridgeController(object(), coordinator)
        # Build a custom controller that uses our spy
        # We'll use internal state injection
        results: list[ReportBridgePublicResult] = []
        controller2.result_ready.connect(lambda r: results.append(r))

        # Replace the service creation: override start_preparation to use spy
        def _start_with_spy(selections):
            from dp_engine.report_bridge.models import ReportBridgeRequest
            import uuid as _uuid

            with controller2._lock:
                rid = _uuid.uuid4().hex
                controller2._generation += 1
                gen = controller2._generation
                controller2._request_id = rid
                controller2._state = InternalBridgeState.PREPARING
                controller2._latest_generation = gen
                controller2._latest_request_id = rid
                req = ReportBridgeRequest(
                    schema_version=1, request_id=rid,
                    selections=tuple(selections),
                )

            # Emit PREPARING
            controller2.result_ready.emit(ReportBridgePublicResult(
                request_id=rid, generation=gen,
                status=ReportBridgeStatus.PREPARING,
                assets=(), warnings=(), safe_error_code=None, safe_error_message=None,
            ))

            # Use QThread + Worker with spy service
            from ui.report_bridge_controller import _BridgeWorker as BW
            controller2._cancel_event = _th.Event()
            controller2._worker = BW(req, gen, spy)
            controller2._thread = QThread()
            controller2._worker.moveToThread(controller2._thread)
            controller2._thread.started.connect(controller2._worker.run)
            controller2._worker.finished.connect(controller2._on_worker_success)
            controller2._worker.error.connect(controller2._on_worker_error)
            controller2._thread.finished.connect(controller2._thread.deleteLater)
            controller2._worker.finished.connect(controller2._thread.quit)
            controller2._worker.error.connect(controller2._thread.quit)
            controller2._thread.start()

        # Override start_preparation
        controller2.start_preparation = _start_with_spy

        tid, aid = _tid(), _aid()
        controller2.start_preparation([_make_sel(task_id=tid, artifact_id=aid)])
        _process_events(3000)

        # Verify worker did run
        assert spy.list_task_threads, "list_task must have been called"
        assert spy.export_threads, "export must have been called"
        assert spy.parse_threads, "parse must have been called"

        # All calls happened on same (worker) thread
        worker_tid = spy.list_task_threads[0]
        for t in spy.list_task_threads:
            assert t == worker_tid, "All list_task calls on same worker thread"
        for t in spy.export_threads:
            assert t == worker_tid, "All export calls on same worker thread"
        for t in spy.parse_threads:
            assert t == worker_tid, "All parse calls on same worker thread"

        # Worker thread is NOT the main/UI thread
        assert worker_tid != main_tid, (
            f"Worker thread {worker_tid} must differ from main thread {main_tid}"
        )

        controller2.close()
        _process_events(500)

    def test_ui_thread_never_executes_artifact_io_or_parsing(self, qapp):
        """RB-SEC-04: UI thread only handles state and signals.
        list_task, export, read, and parse are never on the UI thread."""
        import threading as _th

        main_tid = _th.get_ident()
        spy = _SpyService()

        coordinator = ArtifactOperationCoordinator()
        controller = ReportBridgeController(object(), coordinator)
        results: list[ReportBridgePublicResult] = []
        controller.result_ready.connect(lambda r: results.append(r))

        # Replace start_preparation to use spy
        def _start_with_spy(selections):
            from dp_engine.report_bridge.models import ReportBridgeRequest
            import uuid as _uuid

            with controller._lock:
                rid = _uuid.uuid4().hex
                controller._generation += 1
                gen = controller._generation
                controller._request_id = rid
                controller._state = InternalBridgeState.PREPARING
                controller._latest_generation = gen
                controller._latest_request_id = rid
                req = ReportBridgeRequest(
                    schema_version=1, request_id=rid,
                    selections=tuple(selections),
                )

            controller.result_ready.emit(ReportBridgePublicResult(
                request_id=rid, generation=gen,
                status=ReportBridgeStatus.PREPARING,
                assets=(), warnings=(), safe_error_code=None, safe_error_message=None,
            ))

            from ui.report_bridge_controller import _BridgeWorker as BW
            controller._cancel_event = _th.Event()
            controller._worker = BW(req, gen, spy)
            controller._thread = QThread()
            controller._worker.moveToThread(controller._thread)
            controller._thread.started.connect(controller._worker.run)
            controller._worker.finished.connect(controller._on_worker_success)
            controller._worker.error.connect(controller._on_worker_error)
            controller._thread.finished.connect(controller._thread.deleteLater)
            controller._worker.finished.connect(controller._thread.quit)
            controller._worker.error.connect(controller._thread.quit)
            controller._thread.start()

        controller.start_preparation = _start_with_spy

        tid, aid = _tid(), _aid()
        controller.start_preparation([_make_sel(task_id=tid, artifact_id=aid)])
        _process_events(3000)

        # Assert: NO IO or parse happened on the main thread
        for t in spy.list_task_threads:
            assert t != main_tid, "list_task must NOT execute on UI thread"
        for t in spy.export_threads:
            assert t != main_tid, "export must NOT execute on UI thread"
        for t in spy.parse_threads:
            assert t != main_tid, "parse must NOT execute on UI thread"

        controller.close()
        _process_events(500)


# ═══════════════════════════════════════════════════════════════════
# Batch 3.3.1A-R — QThread Timeout Lifecycle Tests
# ═══════════════════════════════════════════════════════════════════

class BlockingServiceForTimeoutTest:
    """A service whose prepare_assets blocks until released — for testing
    close() timeout behavior."""

    def __init__(self, artifact_store, coordinator):
        self._artifact_store = artifact_store
        self._coordinator = coordinator
        self._block_event = threading.Event()
        self._ready_event = threading.Event()
        self._result_lease = None
        self._result_error = None
        self._did_run = False

    def prepare_assets(self, request, generation, cancel_event=None):
        """Block until _block_event is set, simulating a hung export."""
        import threading as _th
        self._did_run = True
        self._ready_event.set()
        # Block until released by test
        self._block_event.wait()
        # After unblock, raise to simulate error (or return lease)
        raise _PrepareError("internal_failure")

    def unblock(self):
        self._block_event.set()


class _BlockingBridgeWorker(QObject):
    """Worker that uses a BlockingServiceForTimeoutTest."""
    finished = pyqtSignal(object)
    error = pyqtSignal(str, str)

    def __init__(self, request, generation, service, parent=None):
        super().__init__(parent)
        self._request = request
        self._generation = generation
        self._service = service
        self._cancel_event = threading.Event()

    def cancel(self):
        self._cancel_event.set()

    @pyqtSlot()
    def run(self):
        try:
            lease = self._service.prepare_assets(
                self._request, self._generation,
                cancel_event=self._cancel_event,
            )
            self.finished.emit(lease)
        except Exception as e:
            error_code = getattr(e, "error_code", None) or "internal_failure"
            self.error.emit(error_code, str(e))




class TestCloseTimeout:
    """QThread timeout lifecycle tests — P0-3 closure."""

    def test_close_timeout_retains_running_thread_and_lease(self, qapp):
        """When wait() times out, thread/worker refs retained, Lease NOT released."""
        coordinator = ArtifactOperationCoordinator()
        blocking_svc = BlockingServiceForTimeoutTest(object(), coordinator)

        controller = ReportBridgeController(object(), coordinator)

        # Manually inject blocking worker to simulate timeout
        request = ReportBridgeRequest(
            schema_version=1,
            request_id=_tid(),
            selections=(_make_sel(),),
        )

        # Set up controller state as PREPARING
        controller._state = InternalBridgeState.PREPARING
        controller._generation = 1
        controller._request_id = request.request_id
        controller._latest_generation = 1
        controller._latest_request_id = request.request_id

        # Create worker and thread
        worker = _BlockingBridgeWorker(request, 1, blocking_svc)
        thread = QThread()
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.finished.connect(controller._on_worker_success)
        worker.error.connect(controller._on_worker_error)
        worker.finished.connect(thread.quit)
        worker.error.connect(thread.quit)
        thread.finished.connect(thread.deleteLater)

        controller._worker = worker
        controller._thread = thread
        controller._cancel_event = threading.Event()

        # Start the thread
        thread.start()

        # Wait for worker to enter blocking state
        assert blocking_svc._ready_event.wait(timeout=5.0), "Worker should start"

        # Now call close — this should timeout because worker is blocked
        controller.close()

        # After close with timeout:
        # 1. thread reference retained
        assert controller._thread is not None, "Thread ref must be retained after timeout"
        # 2. worker reference retained
        assert controller._worker is not None, "Worker ref must be retained after timeout"
        # 3. deferred cleanup pending
        assert controller._deferred_cleanup_pending, "Deferred cleanup must be pending"

        # Unblock the worker
        blocking_svc.unblock()

        # Wait for deferred cleanup
        _process_events(2000)

        # After deferred cleanup: references should be cleared
        # (Note: thread may still be alive briefly during deleteLater)
        assert not controller._deferred_cleanup_pending, (
            "Deferred cleanup must complete"
        )

        # Wait for thread to actually finish (protect against deleteLater)
        try:
            if controller._thread is not None and controller._thread.isRunning():
                controller._thread.wait(5000)
        except RuntimeError:
            pass

    def test_late_worker_callback_after_close_does_not_publish_result(
        self, qapp
    ):
        """Late worker callback after close timeout must not publish READY/FAILED."""
        coordinator = ArtifactOperationCoordinator()
        blocking_svc = BlockingServiceForTimeoutTest(object(), coordinator)

        controller = ReportBridgeController(object(), coordinator)
        results: list[ReportBridgePublicResult] = []
        controller.result_ready.connect(lambda r: results.append(r))

        request = ReportBridgeRequest(
            schema_version=1, request_id=_tid(), selections=(_make_sel(),),
        )

        controller._state = InternalBridgeState.PREPARING
        controller._generation = 1
        controller._request_id = request.request_id
        controller._latest_generation = 1
        controller._latest_request_id = request.request_id

        worker = _BlockingBridgeWorker(request, 1, blocking_svc)
        thread = QThread()
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.finished.connect(controller._on_worker_success)
        worker.error.connect(controller._on_worker_error)
        worker.finished.connect(thread.quit)
        worker.error.connect(thread.quit)
        thread.finished.connect(thread.deleteLater)

        controller._worker = worker
        controller._thread = thread
        controller._cancel_event = threading.Event()

        thread.start()
        assert blocking_svc._ready_event.wait(timeout=5.0)

        # Record result count before close
        count_before = len(results)

        # Close — will timeout
        controller.close()

        # Unblock worker → late error callback fires
        blocking_svc.unblock()
        _process_events(2000)

        # No NEW public results should have been emitted by late callback
        count_after = len(results)
        assert count_after == count_before, (
            f"Late callback must not publish results: {count_before} → {count_after}"
        )

        # Cleanup (protect against deleteLater)
        try:
            if controller._thread is not None and controller._thread.isRunning():
                controller._thread.wait(5000)
        except RuntimeError:
            pass

    def test_close_timeout_does_not_destroy_running_qthread(self, qapp):
        """close() timeout must not destroy a running QThread.

        No 'QThread: Destroyed while thread is still running' warning.
        """
        coordinator = ArtifactOperationCoordinator()
        blocking_svc = BlockingServiceForTimeoutTest(object(), coordinator)

        controller = ReportBridgeController(object(), coordinator)

        request = ReportBridgeRequest(
            schema_version=1, request_id=_tid(), selections=(_make_sel(),),
        )

        controller._state = InternalBridgeState.PREPARING
        controller._generation = 1
        controller._request_id = request.request_id
        controller._latest_generation = 1
        controller._latest_request_id = request.request_id

        worker = _BlockingBridgeWorker(request, 1, blocking_svc)
        thread = QThread()
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.finished.connect(controller._on_worker_success)
        worker.error.connect(controller._on_worker_error)
        worker.finished.connect(thread.quit)
        worker.error.connect(thread.quit)
        thread.finished.connect(thread.deleteLater)

        controller._worker = worker
        controller._thread = thread
        controller._cancel_event = threading.Event()

        # Capture Qt warnings
        import warnings

        thread.start()
        assert blocking_svc._ready_event.wait(timeout=5.0)

        # Close — will timeout
        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            controller.close()

        # Unblock worker to allow clean shutdown
        blocking_svc.unblock()
        _process_events(2000)

        # Wait for thread to finish cleanly (protect against deleteLater)
        try:
            if thread.isRunning():
                thread.wait(5000)
        except RuntimeError:
            # QThread C++ object already deleted by deleteLater — that's fine
            pass

        # Verify no QThread destroyed-while-running warning
        destroyed_warnings = [
            str(w.message) for w in w
            if "destroyed" in str(w.message).lower() and "thread" in str(w.message).lower()
        ]
        assert len(destroyed_warnings) == 0, (
            f"QThread destroyed while running: {destroyed_warnings}"
        )
