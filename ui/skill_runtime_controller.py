"""Qt async controller for SkillRuntime operations.

Wraps SkillRuntimeService in a QThread Worker so the UI never blocks.
Follows the same pattern as SkillInstallController.

Architecture:
    AgentSkillWidget
    → SkillRuntimeController.start_healthcheck(skill_id, version)
    → SkillRuntimeController.start_run(skill_id, version, params)
    → RuntimeWorker (QThread) → SkillRuntimeService.run_healthcheck() / run_skill()
    → result_ready / error_occurred signals

Batch 3.1.2: Added start_run() for run entrypoint execution.
Batch 3.2.2: Added artifact locate/export/delete_task background operations
    via ArtifactStore with shared mutex and generation tracking.
"""

from __future__ import annotations

import logging
from pathlib import Path

from PyQt6.QtCore import QObject, QThread, pyqtSignal, pyqtSlot

# Batch 3.3.2: Import for type annotation
from dp_engine.report_bridge.coordinator import ArtifactOperationCoordinator

logger = logging.getLogger(__name__)


# ── Worker ──


class _RuntimeWorker(QObject):
    """QObject worker that runs SkillRuntimeService on a QThread."""

    finished = pyqtSignal(object)   # SkillRuntimeResponse
    error = pyqtSignal(str, str)     # error_type, message

    def __init__(
        self,
        registry_path: Path,
        installed_dir: Path,
        skill_id: str,
        version: str,
        *,
        timeout_seconds: float = 10.0,
        cancel_grace_ms: int | None = None,
        terminate_grace_ms: int | None = None,
        kill_grace_ms: int | None = None,
        poll_interval_ms: int | None = None,
        operation: str = "healthcheck",
        params: dict[str, object] | None = None,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self._registry_path = registry_path
        self._installed_dir = installed_dir
        self._skill_id = skill_id
        self._version = version
        self._timeout_seconds = timeout_seconds
        self._cancel_grace_ms = cancel_grace_ms
        self._terminate_grace_ms = terminate_grace_ms
        self._kill_grace_ms = kill_grace_ms
        self._poll_interval_ms = poll_interval_ms
        self._operation = operation
        self._params = params
        self._cancelled = False

    def cancel(self) -> None:
        """Signal the service to cancel the running operation.

        NON-BLOCKING: sets the cancelled flag on the service if available,
        writes the cancel marker, and returns immediately. The polling
        loop in _run_subprocess detects the flag and handles cleanup.
        """
        self._cancelled = True
        if hasattr(self, '_service') and self._service is not None:
            svc = self._service
            svc._cancelled = True  # type: ignore[attr-defined]
            ws = svc._current_workspace()  # type: ignore[attr-defined]
            if ws is not None:
                from dp_engine.skills.runtime_paths import write_cancel_marker
                write_cancel_marker(ws)

    @pyqtSlot()
    def run(self) -> None:
        """Execute the operation on the worker thread."""
        from dp_engine.skills.registry import SkillRegistry
        from dp_engine.skills.runtime_service import SkillRuntimeService
        from dp_engine.skills.runtime_errors import SkillRuntimeError

        try:
            # Load registry
            registry = SkillRegistry(self._registry_path)
            registry.load()

            # Create service with configurable timing
            svc_kwargs: dict = {}
            if self._cancel_grace_ms is not None:
                svc_kwargs["cancel_grace_ms"] = self._cancel_grace_ms
            if self._terminate_grace_ms is not None:
                svc_kwargs["terminate_grace_ms"] = self._terminate_grace_ms
            if self._kill_grace_ms is not None:
                svc_kwargs["kill_grace_ms"] = self._kill_grace_ms
            if self._poll_interval_ms is not None:
                svc_kwargs["poll_interval_ms"] = self._poll_interval_ms
            self._service = SkillRuntimeService(
                registry=registry,
                installed_dir=self._installed_dir,
                **svc_kwargs,
            )

            # If cancel was requested before service was created, apply it now
            if self._cancelled:
                self._service._cancelled = True  # type: ignore[attr-defined]

            # Dispatch based on operation
            if self._operation == "run":
                response = self._service.run_skill(
                    self._skill_id,
                    self._version,
                    params=self._params or {},
                    timeout_seconds=self._timeout_seconds,
                )
            else:
                response = self._service.run_healthcheck(
                    self._skill_id,
                    self._version,
                    timeout_seconds=self._timeout_seconds,
                )

            self.finished.emit(response)

        except SkillRuntimeError as e:
            logger.error("Runtime error: %s", e)
            self.error.emit(type(e).__name__, str(e))
        except Exception as e:
            logger.exception("Unexpected error in RuntimeWorker")
            self.error.emit(type(e).__name__, str(e))


# ── Artifact Worker (Batch 3.2.2) ──


class _ArtifactWorker(QObject):
    """QObject worker that runs ArtifactStore operations on a QThread."""

    finished = pyqtSignal(str, bool, str, int)  # operation, success, safe_message, generation
    error = pyqtSignal(str, str, int)           # error_type, message, generation

    def __init__(
        self,
        operation: str,
        skill_id: str,
        task_id: str,
        artifact_id: str,
        target: str,
        overwrite: bool,
        generation: int,
        store: object,  # ArtifactStore
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self._operation = operation
        self._skill_id = skill_id
        self._task_id = task_id
        self._artifact_id = artifact_id
        self._target = target
        self._overwrite = overwrite
        self._generation = generation
        self._store = store

    @pyqtSlot()
    def run(self) -> None:
        """Execute the artifact operation on the worker thread."""
        try:
            if self._operation == "locate":
                self._store.locate(  # type: ignore[union-attr]
                    self._skill_id, self._task_id, self._artifact_id,
                )
                self.finished.emit(
                    "locate", True,
                    "已在文件管理器中定位文件",
                    self._generation,
                )
            elif self._operation == "export":
                target_path = Path(self._target)
                self._store.export(  # type: ignore[union-attr]
                    self._skill_id, self._task_id, self._artifact_id,
                    target_path,
                    overwrite=self._overwrite,
                )
                self.finished.emit(
                    "export", True,
                    "文件已成功导出",
                    self._generation,
                )
            elif self._operation == "delete_task":
                self._store.delete_task(  # type: ignore[union-attr]
                    self._skill_id, self._task_id,
                )
                self.finished.emit(
                    "delete_task", True,
                    "已删除本次运行生成的全部文件",
                    self._generation,
                )
            else:
                self.error.emit(
                    "ValueError",
                    f"Unknown artifact operation: {self._operation!r}",
                    self._generation,
                )
        except FileExistsError as e:
            self.finished.emit(
                self._operation, False,
                "目标文件已存在，操作已取消",
                self._generation,
            )
        except FileNotFoundError as e:
            self.finished.emit(
                self._operation, False,
                "无法定位该文件。文件可能已被移动、删除或安全校验失败。",
                self._generation,
            )
        except PermissionError:
            self.finished.emit(
                self._operation, False,
                "权限不足，无法完成操作",
                self._generation,
            )
        except OSError as e:
            self.finished.emit(
                self._operation, False,
                "文件操作失败，请检查磁盘空间和目标路径",
                self._generation,
            )
        except RuntimeError as e:
            msg = str(e)
            # Map known error categories to safe messages
            if "hash" in msg.lower() or "size" in msg.lower():
                safe = "文件已损坏或校验不匹配，无法完成操作"
            elif "owner" in msg.lower() or "skill_id" in msg.lower() or "task_id" in msg.lower():
                safe = "文件归属验证失败，无法完成操作"
            elif "symlink" in msg.lower() or "reparse" in msg.lower() or "escape" in msg.lower():
                safe = "路径安全校验失败，无法完成操作"
            elif "not found" in msg.lower():
                safe = "无法定位该文件。文件可能已被移动、删除或安全校验失败。"
            else:
                safe = "操作无法完成，请稍后重试"
            self.finished.emit(
                self._operation, False, safe,
                self._generation,
            )
        except Exception:
            logger.exception("Unexpected error in ArtifactWorker")
            self.error.emit(
                "ArtifactOperationError",
                "操作无法完成，请稍后重试",
                self._generation,
            )


# ── Controller ──


class SkillRuntimeController(QObject):
    """Qt controller for SkillRuntime operations.

    Usage:
        controller = SkillRuntimeController(registry_path, installed_dir, parent=self)
        controller.result_ready.connect(self._on_result)
        controller.error_occurred.connect(self._on_error)
        controller.running_changed.connect(self._on_running_changed)
        controller.start_healthcheck("my-skill", "1.0.0")
    """

    result_ready = pyqtSignal(object)    # SkillRuntimeResponse
    error_occurred = pyqtSignal(str, str)  # error_type, message
    running_changed = pyqtSignal(bool)    # True when running
    stage_changed = pyqtSignal(str)       # Current stage description
    # Batch 3.2.2: artifact operation result signal
    artifact_result_ready = pyqtSignal(str, bool, str, int)  # operation, success, safe_message, generation

    def __init__(
        self,
        registry_path: Path,
        installed_dir: Path,
        *,
        parent: QObject | None = None,
        cancel_grace_ms: int | None = None,
        terminate_grace_ms: int | None = None,
        kill_grace_ms: int | None = None,
        poll_interval_ms: int | None = None,
        artifact_store: object | None = None,  # Batch 3.2.2: injectable ArtifactStore
    ) -> None:
        super().__init__(parent)
        self._registry_path = registry_path
        self._installed_dir = installed_dir
        self._cancel_grace_ms = cancel_grace_ms
        self._terminate_grace_ms = terminate_grace_ms
        self._kill_grace_ms = kill_grace_ms
        self._poll_interval_ms = poll_interval_ms
        self._thread: QThread | None = None
        self._worker: _RuntimeWorker | None = None
        self._running = False
        # Batch 3.2.2: artifact operation state
        self._artifact_store = artifact_store
        self._artifact_thread: QThread | None = None
        self._artifact_worker: _ArtifactWorker | None = None
        self._artifact_generation: int = 0
        # Batch 3.3.2: Coordinator for artifact operation mutex
        self._coordinator: ArtifactOperationCoordinator | None = None  # type: ignore[valid-type]
        self._current_artifact_token: object | None = None

    @property
    def is_running(self) -> bool:
        return self._running

    @property
    def is_artifact_running(self) -> bool:
        """True when an artifact operation (locate/export/delete) is in progress."""
        return self._artifact_thread is not None and self._artifact_thread.isRunning()

    # ── Coordinator integration (Batch 3.3.2) ──

    def set_coordinator(self, coordinator: ArtifactOperationCoordinator) -> None:
        """Set the application-level ArtifactOperationCoordinator."""
        self._coordinator = coordinator

    def _try_acquire_artifact_token(self, skill_id: str, task_id: str) -> object | None:
        """Try to acquire a USER_ARTIFACT_OPERATION token from coordinator.

        Returns None if the same owner has an active BRIDGE_SESSION
        or another USER_ARTIFACT_OPERATION.
        """
        if self._coordinator is None:
            return True  # No coordinator → allow (backward compat)
        from dp_engine.report_bridge.models import OperationKind
        token = self._coordinator.try_acquire(  # type: ignore[union-attr]
            skill_id, task_id,
            operation=OperationKind.USER_ARTIFACT_OPERATION,
        )
        return token

    def _release_artifact_token(self, token: object) -> None:
        """Release an artifact operation token if it's a real token."""
        if token is not None and token is not True and hasattr(token, 'release'):
            token.release()  # type: ignore[union-attr]

    # ── Runtime operations ──

    def start_healthcheck(
        self,
        skill_id: str,
        version: str,
        *,
        timeout_seconds: float = 10.0,
    ) -> None:
        """Start a healthcheck for the given skill version.

        The check runs asynchronously on a QThread.
        Results are delivered via result_ready or error_occurred signals.
        """
        if self._running:
            logger.warning("Healthcheck already running — ignoring")
            return

        self._cleanup_thread()

        self._worker = _RuntimeWorker(
            registry_path=self._registry_path,
            installed_dir=self._installed_dir,
            skill_id=skill_id,
            version=version,
            timeout_seconds=timeout_seconds,
            cancel_grace_ms=self._cancel_grace_ms,
            terminate_grace_ms=self._terminate_grace_ms,
            kill_grace_ms=self._kill_grace_ms,
            poll_interval_ms=self._poll_interval_ms,
            operation="healthcheck",
            parent=self,
        )

        self._thread = QThread(self)
        self._worker.moveToThread(self._thread)

        # Wire signals
        self._thread.started.connect(self._worker.run)
        self._worker.finished.connect(self._on_worker_finished)
        self._worker.error.connect(self._on_worker_error)
        self._worker.finished.connect(self._thread.quit)
        self._worker.error.connect(self._thread.quit)
        self._thread.finished.connect(self._on_thread_finished)

        self._thread.start()
        self._set_running(True, f"开始健康检查: {skill_id}@{version}")

    def start_run(
        self,
        skill_id: str,
        version: str,
        params: dict[str, object],
        *,
        timeout_seconds: float | None = None,
    ) -> bool:
        """Start a run for the given skill version (Batch 3.1.2).

        The run executes asynchronously on a QThread.
        Results are delivered via result_ready or error_occurred signals.
        Only one operation (healthcheck or run) may be active at a time.

        Returns True if the run was started, False if an operation is
        already running.
        """
        if self._running:
            logger.warning("Operation already running — ignoring start_run")
            return False

        self._cleanup_thread()

        effective_timeout = (
            timeout_seconds if timeout_seconds is not None else 30.0
        )

        self._worker = _RuntimeWorker(
            registry_path=self._registry_path,
            installed_dir=self._installed_dir,
            skill_id=skill_id,
            version=version,
            timeout_seconds=effective_timeout,
            cancel_grace_ms=self._cancel_grace_ms,
            terminate_grace_ms=self._terminate_grace_ms,
            kill_grace_ms=self._kill_grace_ms,
            poll_interval_ms=self._poll_interval_ms,
            operation="run",
            params=params,
            parent=self,
        )

        self._thread = QThread(self)
        self._worker.moveToThread(self._thread)

        # Wire signals
        self._thread.started.connect(self._worker.run)
        self._worker.finished.connect(self._on_worker_finished)
        self._worker.error.connect(self._on_worker_error)
        self._worker.finished.connect(self._thread.quit)
        self._worker.error.connect(self._thread.quit)
        self._thread.finished.connect(self._on_thread_finished)

        self._thread.start()
        self._set_running(True, f"开始运行: {skill_id}@{version}")
        return True

    # ── Artifact operations (Batch 3.2.2) ──

    def start_artifact_locate(
        self,
        skill_id: str,
        task_id: str,
        artifact_id: str,
    ) -> bool:
        """Start a background locate operation via ArtifactStore.

        Returns False if any operation (runtime or artifact) is already
        running, or if coordinator blocks (BRIDGE_SESSION active).
        """
        if self._running or self.is_artifact_running:
            logger.warning("Operation already running — ignoring start_artifact_locate")
            return False

        # Batch 3.3.2: Coordinator check
        token = self._try_acquire_artifact_token(skill_id, task_id)
        if token is None:
            self.artifact_result_ready.emit(
                "locate", False,
                "当前任务的产物正在被报告流程使用，请稍后再试。",
                0,
            )
            return False

        self._current_artifact_token = token
        self._artifact_generation += 1
        gen = self._artifact_generation

        store = self._get_artifact_store()
        self._artifact_worker = _ArtifactWorker(
            operation="locate",
            skill_id=skill_id,
            task_id=task_id,
            artifact_id=artifact_id,
            target="",
            overwrite=False,
            generation=gen,
            store=store,
            parent=self,
        )

        self._artifact_thread = QThread(self)
        self._artifact_worker.moveToThread(self._artifact_thread)

        self._artifact_thread.started.connect(self._artifact_worker.run)
        self._artifact_worker.finished.connect(self._on_artifact_finished)
        self._artifact_worker.error.connect(self._on_artifact_error)
        self._artifact_worker.finished.connect(self._artifact_thread.quit)
        self._artifact_worker.error.connect(self._artifact_thread.quit)
        self._artifact_thread.finished.connect(self._on_artifact_thread_finished)

        self._artifact_thread.start()
        self._set_running(True, "正在定位文件...")
        return True

    def start_artifact_export(
        self,
        skill_id: str,
        task_id: str,
        artifact_id: str,
        target: Path,
        *,
        overwrite: bool,
    ) -> bool:
        """Start a background export operation via ArtifactStore.

        Returns False if any operation is already running,
        or if coordinator blocks (BRIDGE_SESSION active).
        """
        if self._running or self.is_artifact_running:
            logger.warning("Operation already running — ignoring start_artifact_export")
            return False

        # Batch 3.3.2: Coordinator check
        token = self._try_acquire_artifact_token(skill_id, task_id)
        if token is None:
            self.artifact_result_ready.emit(
                "export", False,
                "当前任务的产物正在被报告流程使用，请稍后再试。",
                0,
            )
            return False

        self._current_artifact_token = token
        self._artifact_generation += 1
        gen = self._artifact_generation

        store = self._get_artifact_store()
        self._artifact_worker = _ArtifactWorker(
            operation="export",
            skill_id=skill_id,
            task_id=task_id,
            artifact_id=artifact_id,
            target=str(target),
            overwrite=overwrite,
            generation=gen,
            store=store,
            parent=self,
        )

        self._artifact_thread = QThread(self)
        self._artifact_worker.moveToThread(self._artifact_thread)

        self._artifact_thread.started.connect(self._artifact_worker.run)
        self._artifact_worker.finished.connect(self._on_artifact_finished)
        self._artifact_worker.error.connect(self._on_artifact_error)
        self._artifact_worker.finished.connect(self._artifact_thread.quit)
        self._artifact_worker.error.connect(self._artifact_thread.quit)
        self._artifact_thread.finished.connect(self._on_artifact_thread_finished)

        self._artifact_thread.start()
        self._set_running(True, "正在导出文件...")
        return True

    def start_artifact_delete_task(
        self,
        skill_id: str,
        task_id: str,
    ) -> bool:
        """Start a background delete_task operation via ArtifactStore.

        Deletes ALL artifacts for the given skill+task.
        Returns False if any operation is already running,
        or if coordinator blocks (BRIDGE_SESSION active).
        """
        if self._running or self.is_artifact_running:
            logger.warning("Operation already running — ignoring start_artifact_delete_task")
            return False

        # Batch 3.3.2: Coordinator check
        token = self._try_acquire_artifact_token(skill_id, task_id)
        if token is None:
            self.artifact_result_ready.emit(
                "delete_task", False,
                "当前任务的产物正在被报告流程使用，请稍后再试。",
                0,
            )
            return False

        self._current_artifact_token = token
        self._artifact_generation += 1
        gen = self._artifact_generation

        store = self._get_artifact_store()
        self._artifact_worker = _ArtifactWorker(
            operation="delete_task",
            skill_id=skill_id,
            task_id=task_id,
            artifact_id="",
            target="",
            overwrite=False,
            generation=gen,
            store=store,
            parent=self,
        )

        self._artifact_thread = QThread(self)
        self._artifact_worker.moveToThread(self._artifact_thread)

        self._artifact_thread.started.connect(self._artifact_worker.run)
        self._artifact_worker.finished.connect(self._on_artifact_finished)
        self._artifact_worker.error.connect(self._on_artifact_error)
        self._artifact_worker.finished.connect(self._artifact_thread.quit)
        self._artifact_worker.error.connect(self._artifact_thread.quit)
        self._artifact_thread.finished.connect(self._on_artifact_thread_finished)

        self._artifact_thread.start()
        self._set_running(True, "正在删除文件...")
        return True

    def _get_artifact_store(self) -> object:
        """Get the ArtifactStore instance, creating a default one if needed."""
        if self._artifact_store is not None:
            return self._artifact_store
        from dp_engine.skills.runtime_artifacts import ArtifactStore
        return ArtifactStore()

    # ── Shared operations ──

    def cancel(self) -> None:
        """Cancel the currently running healthcheck/run.

        Note: Artifact operations cannot be cancelled mid-flight.
        The cancel button is disabled during artifact operations.
        """
        if self._worker is not None:
            self._worker.cancel()
            self.stage_changed.emit("正在取消...")

    def _on_worker_finished(self, response: object) -> None:
        """Worker completed successfully."""
        self.result_ready.emit(response)

    def _on_worker_error(self, error_type: str, message: str) -> None:
        """Worker raised an error."""
        self.error_occurred.emit(error_type, message)

    def _on_thread_finished(self) -> None:
        """QThread has stopped (runtime operation)."""
        self._set_running(False, "就绪")

    def _on_artifact_finished(self, operation: str, success: bool, safe_message: str, generation: int) -> None:
        """Artifact worker completed."""
        self.artifact_result_ready.emit(operation, success, safe_message, generation)

    def _on_artifact_error(self, error_type: str, message: str, generation: int) -> None:
        """Artifact worker raised an error — forward as a failed result."""
        self.artifact_result_ready.emit("unknown", False, message, generation)

    def _on_artifact_thread_finished(self) -> None:
        """QThread for artifact operation has stopped."""
        # Batch 3.3.2: Release coordinator token
        token = self._current_artifact_token
        self._current_artifact_token = None
        self._release_artifact_token(token)

        self._cleanup_artifact_thread()
        self._set_running(False, "就绪")

    def _cleanup_artifact_thread(self) -> None:
        """Clean up artifact thread and worker references."""
        if self._artifact_thread is not None:
            if self._artifact_thread.isRunning():
                self._artifact_thread.quit()
                self._artifact_thread.wait(3000)
            self._artifact_thread.deleteLater()
            self._artifact_thread = None
        if self._artifact_worker is not None:
            self._artifact_worker.deleteLater()
            self._artifact_worker = None

    def _set_running(self, running: bool, stage: str) -> None:
        """Update running state and emit signals."""
        self._running = running
        self.running_changed.emit(running)
        self.stage_changed.emit(stage)

    def _cleanup_thread(self) -> None:
        """Clean up previous runtime thread and worker if any."""
        if self._thread is not None:
            if self._thread.isRunning():
                self._thread.quit()
                self._thread.wait(3000)
            self._thread.deleteLater()
            self._thread = None
        if self._worker is not None:
            self._worker.deleteLater()
            self._worker = None

    def close(self) -> None:
        """Cancel and clean up. Safe to call during widget close."""
        self.cancel()
        self._cleanup_thread()
        self._cleanup_artifact_thread()
