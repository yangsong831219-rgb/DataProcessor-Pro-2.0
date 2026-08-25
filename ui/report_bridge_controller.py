"""ReportBridgeController — Qt state machine owner for Report Bridge.

Batch 3.3.1A: Manages the full lifecycle of bridge asset preparation.
Uses QThread + Worker pattern. Public signals only emit
ReportBridgePublicResult — never Lease, GenerationInput, Path, or exceptions.

State machine (internal):
  IDLE → PREPARING → READY → GENERATING → SUCCEEDED/FAILED/CANCELLED → RELEASED
  PREPARING → FAILED/CANCELLED
  READY → RELEASED (discard)
"""

from __future__ import annotations

import logging
import threading
import uuid as _uuid
from pathlib import Path
from typing import Any

from PyQt6.QtCore import QObject, QThread, pyqtSignal, pyqtSlot

from dp_engine.report_bridge.coordinator import ArtifactOperationCoordinator
from dp_engine.report_bridge.models import (
    InternalBridgeState,
    ReportArtifactSelection,
    ReportAssetRole,
    ReportAssetSummary,
    ReportBridgeLease,
    ReportBridgePublicResult,
    ReportBridgeRequest,
    ReportBridgeStatus,
    ReportGenerationInput,
    SAFE_ERROR_MESSAGES,
    PreparedReportAsset,
)
from dp_engine.report_bridge.service import ReportBridgeService

logger = logging.getLogger(__name__)

# Freeze: 15 seconds timeout for worker to finish after cancel
_WORKER_JOIN_TIMEOUT_SECONDS = 15.0


class _BridgeWorker(QObject):
    """QObject worker that runs ReportBridgeService.prepare_assets on a QThread."""

    finished = pyqtSignal(object)  # ReportBridgeLease on success
    error = pyqtSignal(str, str)    # error_code, safe_message

    def __init__(
        self,
        request: ReportBridgeRequest,
        generation: int,
        service: ReportBridgeService,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self._request = request
        self._generation = generation
        self._service = service
        self._cancel_event = threading.Event()

    def cancel(self) -> None:
        """Signal the worker to cancel. Non-blocking."""
        self._cancel_event.set()

    @pyqtSlot()
    def run(self) -> None:
        """Execute prepare_assets in worker thread."""
        try:
            lease = self._service.prepare_assets(
                self._request,
                self._generation,
                cancel_event=self._cancel_event,
            )
            self.finished.emit(lease)
        except Exception as e:
            error_code = getattr(e, "error_code", None)
            if error_code == "__cancelled__":
                # Cancelled is handled by controller via the cancel path
                self.error.emit("__cancelled__", "Operation cancelled")
            elif error_code and error_code in SAFE_ERROR_MESSAGES:
                self.error.emit(error_code, SAFE_ERROR_MESSAGES[error_code])
            else:
                logger.exception("Unexpected error in BridgeWorker")
                self.error.emit(
                    "internal_failure",
                    SAFE_ERROR_MESSAGES["internal_failure"],
                )


class ReportBridgeController(QObject):
    """Singleton state machine owner for Report Bridge lifecycle.

    Public Qt signals only send ReportBridgePublicResult.
    Never sends Lease, GenerationInput, Path, workspace_path,
    or internal exceptions through signals.
    """

    # ── Public signals (only ReportBridgePublicResult) ──

    result_ready = pyqtSignal(ReportBridgePublicResult)

    def __init__(
        self,
        artifact_store: Any,
        coordinator: ArtifactOperationCoordinator,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self._artifact_store = artifact_store
        self._coordinator = coordinator

        # Internal state
        self._state = InternalBridgeState.IDLE
        self._generation = 0
        self._request_id: str | None = None
        self._lease: ReportBridgeLease | None = None
        self._worker: _BridgeWorker | None = None
        self._thread: QThread | None = None
        self._cancel_event: threading.Event | None = None
        self._latest_generation = 0
        self._latest_request_id: str | None = None
        self._lock = threading.Lock()

        # ── Close / lifecycle flags ──
        self._closing = False
        self._deferred_cleanup_pending = False

    # ── Properties ──

    @property
    def state(self) -> InternalBridgeState:
        with self._lock:
            return self._state

    @property
    def generation(self) -> int:
        with self._lock:
            return self._generation

    @property
    def request_id(self) -> str | None:
        with self._lock:
            return self._request_id

    # ── Start preparation ──

    def start_preparation(
        self,
        selections: list[ReportArtifactSelection],
    ) -> None:
        """Initiate asset preparation.

        Generates request_id, increments generation, constructs
        ReportBridgeRequest, emits PREPARING, and spawns Worker thread.

        Args:
            selections: List of validated artifact selections.
        """
        from dp_engine.report_bridge.models import ReportBridgeRequest

        with self._lock:
            # Validate current state
            if self._state not in (InternalBridgeState.IDLE, InternalBridgeState.READY,
                                   InternalBridgeState.FAILED, InternalBridgeState.CANCELLED,
                                   InternalBridgeState.SUCCEEDED, InternalBridgeState.RELEASED):
                logger.warning(
                    "Cannot start preparation in state %s", self._state.value
                )
                return

            # Generate request_id and increment generation
            request_id = _uuid.uuid4().hex
            self._generation += 1
            generation = self._generation
            self._request_id = request_id
            self._state = InternalBridgeState.PREPARING
            self._latest_generation = generation
            self._latest_request_id = request_id

            # Build request
            request = ReportBridgeRequest(
                schema_version=1,
                request_id=request_id,
                selections=tuple(selections),
            )

        # Emit PREPARING (outside lock to avoid deadlock)
        preparing_result = ReportBridgePublicResult(
            request_id=request_id,
            generation=generation,
            status=ReportBridgeStatus.PREPARING,
            assets=(),
            warnings=(),
            safe_error_code=None,
            safe_error_message=None,
        )
        self.result_ready.emit(preparing_result)

        # Build service
        service = ReportBridgeService(self._artifact_store, self._coordinator)

        # Create worker and thread
        self._cancel_event = threading.Event()
        self._worker = _BridgeWorker(request, generation, service)
        self._thread = QThread()

        # Move worker to thread
        self._worker.moveToThread(self._thread)
        self._thread.started.connect(self._worker.run)

        # Connect worker signals
        self._worker.finished.connect(self._on_worker_success)
        self._worker.error.connect(self._on_worker_error)

        # Cleanup thread when done
        self._thread.finished.connect(self._thread.deleteLater)
        self._worker.finished.connect(self._thread.quit)
        self._worker.error.connect(self._thread.quit)

        # Start
        self._thread.start()

    # ── Worker callbacks ──

    @pyqtSlot(object)
    def _on_worker_success(self, lease: ReportBridgeLease) -> None:
        """Worker successfully prepared all assets."""
        with self._lock:
            # Ignore old generations
            if (lease.request_id != self._latest_request_id or
                    lease.generation != self._latest_generation):
                logger.debug(
                    "Ignoring stale worker success: gen=%d (current gen=%d)",
                    lease.generation, self._latest_generation,
                )
                # Clean up stale lease
                self._safe_release_lease(lease)
                return

            # Transition PREPARING → READY
            if self._state != InternalBridgeState.PREPARING:
                logger.warning(
                    "Unexpected state %s for worker success", self._state.value
                )
                self._safe_release_lease(lease)
                return

            self._lease = lease
            self._state = InternalBridgeState.READY

            # Generate asset summaries
            assets = self._build_asset_summaries(lease)
            generation = lease.generation
            request_id = lease.request_id

        # Suppress public state update if controller is closing
        if self._closing:
            logger.debug("Suppressing READY emission — controller is closing")
            return

        # Emit READY
        ready_result = ReportBridgePublicResult(
            request_id=request_id,
            generation=generation,
            status=ReportBridgeStatus.READY,
            assets=assets,
            warnings=(),
            safe_error_code=None,
            safe_error_message=None,
        )
        self.result_ready.emit(ready_result)

    @pyqtSlot(str, str)
    def _on_worker_error(self, error_code: str, safe_message: str) -> None:
        """Worker encountered an error or cancellation."""
        with self._lock:
            # Check if this is for current generation
            if self._state != InternalBridgeState.PREPARING:
                logger.debug(
                    "Ignoring stale worker error in state %s: %s",
                    self._state.value, error_code,
                )
                return

            if error_code == "__cancelled__":
                self._state = InternalBridgeState.CANCELLED
                generation = self._generation
                request_id = self._request_id or ""

                # Suppress public state update if controller is closing
                if self._closing:
                    logger.debug(
                        "Suppressing CANCELLED emission — controller is closing"
                    )
                    return

                # Emit CANCELLED
                cancelled_result = ReportBridgePublicResult(
                    request_id=request_id,
                    generation=generation,
                    status=ReportBridgeStatus.CANCELLED,
                    assets=(),
                    warnings=(),
                    safe_error_code=None,
                    safe_error_message=None,
                )
                self.result_ready.emit(cancelled_result)
            else:
                self._state = InternalBridgeState.FAILED
                generation = self._generation
                request_id = self._request_id or ""

                # Suppress public state update if controller is closing
                if self._closing:
                    logger.debug(
                        "Suppressing FAILED emission — controller is closing"
                    )
                    return

                # Emit FAILED
                failed_result = ReportBridgePublicResult(
                    request_id=request_id,
                    generation=generation,
                    status=ReportBridgeStatus.FAILED,
                    assets=(),
                    warnings=(),
                    safe_error_code=error_code,
                    safe_error_message=safe_message,
                )
                self.result_ready.emit(failed_result)

    # ── Private claim/discard/finish ──

    def claim_ready_generation(
        self,
        request_id: str,
        generation: int,
    ) -> ReportGenerationInput | None:
        """Atomically claim a READY generation for report generation.

        Returns GenerationInput on success, None if not READY or mismatch.
        Controller retains Lease ownership.
        """
        with self._lock:
            if self._state != InternalBridgeState.READY:
                return None
            if self._request_id != request_id:
                return None
            if self._generation != generation:
                return None
            if self._lease is None:
                return None

            # Transition READY → GENERATING
            self._state = InternalBridgeState.GENERATING
            lease = self._lease

            return ReportGenerationInput(
                request_id=lease.request_id,
                generation=lease.generation,
                workspace_path=lease.workspace_path,
                assets=lease.prepared_assets,
            )

    def discard_ready_generation(
        self,
        request_id: str,
        generation: int,
    ) -> bool:
        """Discard a READY generation — releases the lease."""
        with self._lock:
            if self._state != InternalBridgeState.READY:
                return False
            if self._request_id != request_id:
                return False
            if self._generation != generation:
                return False

            lease = self._lease
            self._lease = None
            self._state = InternalBridgeState.RELEASED

        if lease is not None:
            self._safe_release_lease(lease)
        return True

    def finish_generation(
        self,
        request_id: str,
        generation: int,
        *,
        status: ReportBridgeStatus,
    ) -> bool:
        """Finish a GENERATING generation with a terminal status."""
        if status not in (
            ReportBridgeStatus.SUCCEEDED,
            ReportBridgeStatus.FAILED,
            ReportBridgeStatus.CANCELLED,
        ):
            return False

        with self._lock:
            if self._state != InternalBridgeState.GENERATING:
                return False
            if self._request_id != request_id:
                return False
            if self._generation != generation:
                return False

            lease = self._lease
            # Build public result before releasing
            if status == ReportBridgeStatus.SUCCEEDED:
                assets = self._build_asset_summaries(lease) if lease else ()
                result = ReportBridgePublicResult(
                    request_id=request_id,
                    generation=generation,
                    status=ReportBridgeStatus.SUCCEEDED,
                    assets=assets,
                    warnings=(),
                    safe_error_code=None,
                    safe_error_message=None,
                )
            elif status == ReportBridgeStatus.FAILED:
                result = ReportBridgePublicResult(
                    request_id=request_id,
                    generation=generation,
                    status=ReportBridgeStatus.FAILED,
                    assets=(),
                    warnings=(),
                    safe_error_code="internal_failure",
                    safe_error_message=SAFE_ERROR_MESSAGES["internal_failure"],
                )
            else:  # CANCELLED
                result = ReportBridgePublicResult(
                    request_id=request_id,
                    generation=generation,
                    status=ReportBridgeStatus.CANCELLED,
                    assets=(),
                    warnings=(),
                    safe_error_code=None,
                    safe_error_message=None,
                )

            # Update internal state
            new_internal = {
                ReportBridgeStatus.SUCCEEDED: InternalBridgeState.SUCCEEDED,
                ReportBridgeStatus.FAILED: InternalBridgeState.FAILED,
                ReportBridgeStatus.CANCELLED: InternalBridgeState.CANCELLED,
            }[status]
            self._state = new_internal
            self._lease = None

        # Release lease
        if lease is not None:
            self._safe_release_lease(lease)

        # Emit public result (outside lock)
        self.result_ready.emit(result)
        return True

    # ── Cancel ──

    def cancel(self) -> None:
        """Request cancellation of the active preparation."""
        if self._cancel_event is not None:
            self._cancel_event.set()
        if self._worker is not None:
            self._worker.cancel()

    # ── Close / shutdown ──

    def close(self) -> None:
        """Graceful shutdown with frozen QThread lifecycle contract.

        Success path (wait returns True):
          1. Cancel event set
          2. thread.quit() + wait(CLOSE_TIMEOUT)
          3. Worker stopped — safe to release Lease
          4. Disconnect signals, clear references

        Timeout path (wait returns False):
          1. Retain thread and worker references (do NOT set to None)
          2. Do NOT release in-use Lease (Worker may still use workspace)
          3. Do NOT delete workspace
          4. Do NOT release bridge session token
          5. Set _closing flag to suppress public state updates
          6. Connect late finished to deferred cleanup handlers
          7. Return without destroying thread

        Late finished callback (deferred cleanup):
          1. Worker no longer uses workspace
          2. One-time idempotent Lease.release()
          3. Release Coordinator token
          4. Clean thread and worker references
          5. Do NOT publish READY/FAILED/CANCELLED to closed UI
          6. Do NOT access destroyed QObject
        """
        # Mark controller as closing — suppress public state updates
        self._closing = True

        # Cancel any active preparation
        self.cancel()

        # Wait for worker thread to finish
        finished_cleanly = False
        try:
            if self._thread is not None and self._thread.isRunning():
                self._thread.quit()
                finished_cleanly = self._thread.wait(
                    int(_WORKER_JOIN_TIMEOUT_SECONDS * 1000)
                )
                if not finished_cleanly:
                    logger.warning(
                        "Bridge worker thread did not finish within timeout"
                    )
        except RuntimeError:
            # QThread C++ object already deleted — nothing to wait for
            finished_cleanly = True  # Thread is gone, treat as clean finish

        if finished_cleanly:
            # ── Success path: Worker stopped, safe to release ──
            self._cleanup_after_worker_stopped()
        else:
            # ── Timeout path: Worker may still be running ──
            # Retain references — do NOT release Lease or clear thread/worker
            self._deferred_cleanup_pending = True

            # Reconnect worker signals to deferred cleanup handlers
            # that suppress public state updates
            if self._worker is not None:
                try:
                    self._worker.finished.disconnect()
                    self._worker.error.disconnect()
                except (TypeError, RuntimeError):
                    pass
                self._worker.finished.connect(self._on_deferred_cleanup_success)
                self._worker.error.connect(self._on_deferred_cleanup_error)

    def _cleanup_after_worker_stopped(self) -> None:
        """Release resources after Worker thread has confirmed stopped."""
        # Release lease if held
        with self._lock:
            lease = self._lease
            self._lease = None
            if self._state in (
                InternalBridgeState.READY,
                InternalBridgeState.GENERATING,
                InternalBridgeState.PREPARING,
            ):
                self._state = InternalBridgeState.RELEASED

        if lease is not None:
            self._safe_release_lease(lease)

        # Disconnect signals
        self._disconnect_worker_signals()

    def _disconnect_worker_signals(self) -> None:
        """Disconnect all worker signals and clear references."""
        if self._worker is not None:
            try:
                self._worker.finished.disconnect()
                self._worker.error.disconnect()
            except (TypeError, RuntimeError):
                pass
            self._worker = None

        if self._thread is not None:
            try:
                self._thread.finished.disconnect()
            except (TypeError, RuntimeError):
                pass
            self._thread = None

    # ── Deferred cleanup handlers (timeout path) ──

    @pyqtSlot(object)
    def _on_deferred_cleanup_success(self, lease: ReportBridgeLease) -> None:
        """Late finished callback after close timeout — deferred cleanup only.

        Does NOT publish READY/SUCCEEDED to closed UI.
        Performs one-time idempotent cleanup:
          1. Release Lease (workspace + token)
          2. Clear references
        """
        if not self._deferred_cleanup_pending:
            return

        logger.debug("Deferred cleanup: worker succeeded after close timeout")
        self._safe_release_lease(lease)
        self._deferred_cleanup_pending = False
        self._disconnect_worker_signals()

    @pyqtSlot(str, str)
    def _on_deferred_cleanup_error(self, error_code: str, safe_message: str) -> None:
        """Late error callback after close timeout — deferred cleanup only.

        Does NOT publish FAILED/CANCELLED to closed UI.
        Worker's finally block in service.py already handled workspace + token.
        We just clean up references.
        """
        if not self._deferred_cleanup_pending:
            return

        logger.debug(
            "Deferred cleanup: worker error after close timeout: %s", error_code
        )
        self._deferred_cleanup_pending = False
        self._disconnect_worker_signals()

    # ── Internal helpers ──

    def _build_asset_summaries(
        self,
        lease: ReportBridgeLease | None,
    ) -> tuple[ReportAssetSummary, ...]:
        """Build safe ReportAssetSummary from lease assets."""
        if lease is None:
            return ()
        summaries: list[ReportAssetSummary] = []
        for i, pa in enumerate(lease.prepared_assets):
            art = pa.authoritative_artifact
            # asset_key: opaque, no artifact_id, no path, no hash
            asset_key = f"asset_{i}"
            summary = ReportAssetSummary(
                asset_key=asset_key,
                display_name=art.display_name or art.relative_path or f"Asset {i + 1}",
                role=pa.role,
                size_bytes=art.size_bytes,
                order=pa.order,
            )
            summaries.append(summary)
        return tuple(summaries)

    def _safe_release_lease(self, lease: ReportBridgeLease) -> None:
        """Release a lease with workspace cleanup and token release."""
        from dp_engine.report_bridge.workspace import (
            safe_release_report_bridge_workspace,
        )

        def _cleanup_workspace(ws_path: Path) -> None:
            safe_release_report_bridge_workspace(lease.request_id, ws_path)

        # Access the coordinator token stored in the lease for release
        token = getattr(lease, "_coordinator_token", None)

        lease.release(
            workspace_cleanup=_cleanup_workspace,
            token_release=(lambda t=token: t.release()) if token is not None else None,
        )
