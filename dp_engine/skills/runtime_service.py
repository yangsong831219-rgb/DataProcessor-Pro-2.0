"""SkillRuntimeService — core orchestration for isolated skill execution.

Manages the full lifecycle:
1. Pre-flight gating (8 checks before subprocess launch)
2. Subprocess creation and management
3. Timeout and cancellation
4. Response validation
5. Registry health status update

The service uses subprocess.Popen (shell=False, parameter array).
UI must wrap this in a QThread Worker — never call from UI thread.
"""

from __future__ import annotations

import logging
import os
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING

from dp_engine.skills.registry import ConsistencyCheckMode, SkillRegistry
from dp_engine.skills.runtime_dependencies import SkillDependencyChecker
from dp_engine.skills.runtime_errors import (
    RuntimeCancelledError,
    RuntimeProtocolError,
    RuntimeTimeoutError,
    RuntimeWorkerCrashedError,
    SkillRuntimeError,
)
from dp_engine.skills.runtime_models import (
    DEFAULT_HEALTHCHECK_CAPABILITIES,
    DEFAULT_HEALTHCHECK_TIMEOUT_SECONDS,
    FORBIDDEN_HEALTHCHECK_CAPABILITIES,
    MAX_STDERR_BYTES,
    MAX_STDOUT_BYTES,
    RUN_ENTRYPOINT_KEY,
    GENERATE_REPORT_OPERATION,
    RUNTIME_PROTOCOL_VERSION,
    VALID_RUN_STATUSES,
    WORKER_GRACE_PERIOD_SECONDS,
    WORKER_KILL_WAIT_SECONDS,
    WORKER_TERMINATE_WAIT_SECONDS,
    HealthcheckContext,
    HealthcheckResult,
    RuntimeArtifact,
    RuntimeErrorInfo,
    SkillRuntimeRequest,
    SkillRuntimeResponse,
)
from dp_engine.skills.runtime_paths import (
    create_workspace,
    generate_task_id,
    get_runtime_root,
    has_cancel_marker,
    write_cancel_marker,
    workspace_path_for,
    cleanup_workspace,
)
from dp_engine.skills.runtime_protocol import (
    collect_redaction_values,
    read_response_atomic,
    read_worker_response_atomic,
    redact_text,
    validate_request,
    write_request_atomic,
)
from dp_engine.skills.models import HEALTHCHECK_ENTRYPOINT_KEY

if TYPE_CHECKING:
    from dp_engine.report_backend.job import ReportBackendJobV1

logger = logging.getLogger(__name__)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class SkillRuntimeService:
    """Core service for isolated skill healthcheck execution.

    Does NOT use Qt.  Must be called from a QThread Worker in the UI layer.

    Cancel/cleanup timing is configurable via constructor parameters.
    Production uses conservative defaults; tests inject fast values.
    """

    def __init__(
        self,
        registry: SkillRegistry,
        installed_dir: Path,
        *,
        runtime_worker_path: Path | None = None,
        cancel_grace_ms: int | None = None,
        terminate_grace_ms: int | None = None,
        kill_grace_ms: int | None = None,
        poll_interval_ms: int | None = None,
    ) -> None:
        self._registry = registry
        self._installed_dir = installed_dir.resolve()
        self._dep_checker = SkillDependencyChecker()

        # Configurable timing — production defaults from runtime_models
        self._cancel_grace = (
            cancel_grace_ms / 1000.0 if cancel_grace_ms is not None
            else WORKER_GRACE_PERIOD_SECONDS
        )
        self._terminate_grace = (
            terminate_grace_ms / 1000.0 if terminate_grace_ms is not None
            else WORKER_TERMINATE_WAIT_SECONDS
        )
        self._kill_grace = (
            kill_grace_ms / 1000.0 if kill_grace_ms is not None
            else WORKER_KILL_WAIT_SECONDS
        )
        self._poll_interval = (
            poll_interval_ms / 1000.0 if poll_interval_ms is not None
            else 0.1  # 100ms default
        )

        # Resolve worker path
        if runtime_worker_path is not None:
            self._worker_path = runtime_worker_path.resolve()
        else:
            # Default: relative to this module
            self._worker_path = (
                Path(__file__).parent / "runtime_worker.py"
            ).resolve()

        if not self._worker_path.is_file():
            raise FileNotFoundError(
                f"runtime_worker.py not found at: {self._worker_path}"
            )

        # Per-operation state
        self._process: subprocess.Popen | None = None
        self._cancelled = False
        self._task_id: str | None = None

    # ── Public API ──

    def run_healthcheck(
        self,
        skill_id: str,
        version: str,
        *,
        timeout_seconds: float = DEFAULT_HEALTHCHECK_TIMEOUT_SECONDS,
    ) -> SkillRuntimeResponse:
        """Run healthcheck for a skill in an isolated subprocess.

        Args:
            skill_id: Skill identifier.
            version: Skill version.
            timeout_seconds: Maximum time for the healthcheck.

        Returns:
            SkillRuntimeResponse with structured result.

        Raises:
            SkillRuntimeError: On pre-flight failure.
        """
        # Do NOT reset _cancelled here — it may have been set externally
        # before run_healthcheck() was entered (e.g. pre-cancel during
        # thread startup).  The finally block handles cleanup.
        task_id = generate_task_id()
        self._task_id = task_id
        started_at = _now_iso()

        try:
            # ── Pre-flight gate ──
            skill = self._run_preflight_gates(skill_id, version)

            # ── Build request ──
            workspace_path = create_workspace(task_id)
            request = self._build_request(
                skill_id, version, skill.install_path,
                str(workspace_path), task_id, timeout_seconds,
            )

            # Validate request
            validate_request(request)

            # Write request atomically
            request_path = workspace_path / "request.json"
            write_request_atomic(request, request_path)

            # Write checking status
            self._update_health_status(
                skill_id, version, "checking",
                f"Healthcheck started at {started_at}",
            )

            # ── Launch subprocess ──
            t_start = time.monotonic()
            try:
                self._run_subprocess(workspace_path, request_path, timeout_seconds)
            except RuntimeTimeoutError:
                response = self._build_timeout_response(
                    task_id, started_at, timeout_seconds, time.monotonic() - t_start
                )
                self._update_health_status(skill_id, version, "timeout", response.message)
                return response
            except RuntimeCancelledError:
                response = self._build_cancelled_response(
                    task_id, started_at, time.monotonic() - t_start
                )
                self._update_health_status(skill_id, version, "cancelled", response.message)
                return response

            duration_ms = int((time.monotonic() - t_start) * 1000)

            # ── Validate subprocess exit ──
            exit_code = self._process.returncode if self._process else -1
            if exit_code != 0:
                stderr_tail = self._read_stderr_tail(workspace_path)
                success = False
                response = SkillRuntimeResponse(
                    protocol_version=RUNTIME_PROTOCOL_VERSION,
                    task_id=task_id,
                    operation="healthcheck",
                    success=False,
                    status="crashed",
                    message=f"Worker exited with code {exit_code}",
                    started_at=started_at,
                    finished_at=_now_iso(),
                    duration_ms=duration_ms,
                    error=RuntimeErrorInfo(
                        error_type="RuntimeWorkerCrashedError",
                        message=f"Worker process exited with code {exit_code}",
                        detail={"exit_code": exit_code, "stderr_tail": stderr_tail},
                    ),
                )
                self._update_health_status(skill_id, version, "crashed", response.message)
                return response

            # ── Read and validate response ──
            result_path = workspace_path / "result.json"
            try:
                response = read_response_atomic(
                    result_path, task_id, "healthcheck", workspace_path,
                )
            except RuntimeProtocolError as e:
                response = SkillRuntimeResponse(
                    protocol_version=RUNTIME_PROTOCOL_VERSION,
                    task_id=task_id,
                    operation="healthcheck",
                    success=False,
                    status="protocol_error",
                    message=str(e),
                    started_at=started_at,
                    finished_at=_now_iso(),
                    duration_ms=duration_ms,
                    error=RuntimeErrorInfo(
                        error_type="RuntimeProtocolError",
                        message=str(e),
                    ),
                )
                self._update_health_status(skill_id, version, "protocol_error", str(e))
                return response

            # ── Update registry ──
            self._update_health_status(
                skill_id, version,
                response.status,
                response.message,
            )

            # Clean up on success
            cleanup_workspace(workspace_path, succeeded=True)

            return response

        except SkillRuntimeError:
            raise
        except Exception as e:
            logger.exception("Unexpected error in run_healthcheck")
            raise SkillRuntimeError(
                f"Healthcheck failed: {e}"
            ) from e
        finally:
            self._process = None
            self._cancelled = False
            self._task_id = None

    def run_skill(
        self,
        skill_id: str,
        version: str,
        params: dict[str, object],
        *,
        timeout_seconds: float | None = None,
    ) -> SkillRuntimeResponse:
        """Run the ordinary ``entrypoints.run`` contract unchanged."""
        return self._run_artifact_operation(
            skill_id,
            version,
            params,
            operation="run",
            entrypoint_key=RUN_ENTRYPOINT_KEY,
            timeout_seconds=timeout_seconds,
        )

    def run_report_backend(
        self,
        skill_id: str,
        version: str,
        job: ReportBackendJobV1,
        *,
        timeout_seconds: float | None = None,
    ) -> SkillRuntimeResponse:
        """Run one compatible report backend through the isolated Runtime."""
        from dp_engine.report_backend.job import ReportBackendJobV1

        if not isinstance(job, ReportBackendJobV1):
            raise TypeError("job must be ReportBackendJobV1")
        return self._run_artifact_operation(
            skill_id,
            version,
            {},
            operation=GENERATE_REPORT_OPERATION,
            entrypoint_key="generate",
            timeout_seconds=timeout_seconds,
            report_job=job,
        )

    def _run_artifact_operation(
        self,
        skill_id: str,
        version: str,
        params: dict[str, object],
        *,
        operation: str,
        entrypoint_key: str,
        timeout_seconds: float | None = None,
        report_job: ReportBackendJobV1 | None = None,
    ) -> SkillRuntimeResponse:
        """Run a skill's run entrypoint in an isolated subprocess (Batch 3.1.1A).

        Args:
            skill_id: Skill identifier.
            version: Skill version.
            params: User-supplied business parameters (JSON-compatible).
            timeout_seconds: Maximum time for execution. Defaults to
                             DEFAULT_HEALTHCHECK_TIMEOUT_SECONDS.

        Returns:
            SkillRuntimeResponse with structured result.

        Raises:
            SkillRuntimeError: On pre-flight failure (typed exception).
            RuntimeDependencyError: On dependency failure.
            RuntimePermissionError: On capability denial.
        """
        effective_timeout = (
            timeout_seconds if timeout_seconds is not None
            else DEFAULT_HEALTHCHECK_TIMEOUT_SECONDS
        )
        task_id = generate_task_id()
        self._task_id = task_id
        started_at = _now_iso()

        # Collect per-task redaction values from user params (Batch 3.1.1B)
        redaction_values: set[str] = collect_redaction_values(params)

        try:
            if operation == GENERATE_REPORT_OPERATION:
                if report_job is None:
                    raise SkillRuntimeError("generate_report requires a report job")
                skill = self._run_preflight_gates_for_report(
                    skill_id,
                    version,
                    report_job.output_artifact_type,
                    report_job.template_mode,
                )
            else:
                skill = self._run_preflight_gates_for_run(skill_id, version)

            # ── Build request ──
            workspace_path = create_workspace(task_id)
            if report_job is not None:
                params = report_job.stage(workspace_path)
                redaction_values = collect_redaction_values(params)
            request = self._build_artifact_operation_request(
                skill_id, version, skill.install_path,
                str(workspace_path), task_id, effective_timeout,
                params, operation, entrypoint_key,
            )

            # Validate request
            validate_request(request)

            # Write request atomically
            request_path = workspace_path / "request.json"
            write_request_atomic(request, request_path)

            # NOTE: No Registry write for run (zero-write contract — Batch 3.1.1A)

            # ── Launch subprocess ──
            t_start = time.monotonic()
            try:
                self._run_subprocess(workspace_path, request_path, effective_timeout)
            except RuntimeTimeoutError:
                label = "Report generation" if report_job is not None else "Run"
                msg = f"{label} timed out after {effective_timeout:.1f}s"
                response = self._build_operation_error_response(
                    task_id, started_at, "timeout",
                    redact_text(msg, redaction_values),
                    "RuntimeTimeoutError",
                    redact_text(f"Timeout after {effective_timeout:.1f}s", redaction_values),
                    time.monotonic() - t_start,
                    operation,
                    stage="execute" if report_job is not None else None,
                )
                return response
            except RuntimeCancelledError:
                msg = (
                    "Report generation cancelled by user"
                    if report_job is not None
                    else "Run cancelled by user"
                )
                response = self._build_operation_error_response(
                    task_id, started_at, "cancelled",
                    redact_text(msg, redaction_values),
                    "RuntimeCancelledError",
                    redact_text(msg, redaction_values),
                    time.monotonic() - t_start,
                    operation,
                    stage="execute" if report_job is not None else None,
                )
                return response

            duration_ms = int((time.monotonic() - t_start) * 1000)

            # ── Validate subprocess exit ──
            exit_code = self._process.returncode if self._process else -1
            if exit_code != 0:
                stderr_tail = (
                    "[report backend process output omitted]"
                    if report_job is not None
                    else self._read_stderr_tail(workspace_path, redaction_values)
                )
                response = SkillRuntimeResponse(
                    protocol_version=RUNTIME_PROTOCOL_VERSION,
                    task_id=task_id,
                    operation=operation,
                    success=False,
                    status="crashed",
                    message=f"Worker exited with code {exit_code}",
                    started_at=started_at,
                    finished_at=_now_iso(),
                    duration_ms=duration_ms,
                    error=RuntimeErrorInfo(
                        error_type="RuntimeWorkerCrashedError",
                        message=redact_text(
                            f"Worker process exited with code {exit_code}",
                            redaction_values,
                        ),
                        detail={
                            "exit_code": exit_code,
                            "stderr_tail": stderr_tail,
                            **(
                                {"stage": "execute"}
                                if report_job is not None else {}
                            ),
                        },
                    ),
                )
                return response

            # ── Read and validate response ──
            result_path = workspace_path / "result.json"
            try:
                response, declarations = read_worker_response_atomic(
                    result_path, task_id, operation, workspace_path,
                )
            except RuntimeProtocolError as e:
                response = SkillRuntimeResponse(
                    protocol_version=RUNTIME_PROTOCOL_VERSION,
                    task_id=task_id,
                    operation=operation,
                    success=False,
                    status="protocol_error",
                    message=redact_text(str(e), redaction_values),
                    started_at=started_at,
                    finished_at=_now_iso(),
                    duration_ms=duration_ms,
                    error=RuntimeErrorInfo(
                        error_type="RuntimeProtocolError",
                        message=redact_text(str(e), redaction_values),
                        detail=(
                            {"stage": "worker_protocol"}
                            if report_job is not None else {}
                        ),
                    ),
                )
                return response

            if (
                report_job is not None
                and response.status != "succeeded"
                and response.error is not None
            ):
                response = SkillRuntimeResponse(
                    protocol_version=response.protocol_version,
                    task_id=response.task_id,
                    operation=response.operation,
                    success=response.success,
                    status=response.status,
                    message=response.message,
                    started_at=response.started_at,
                    finished_at=response.finished_at,
                    duration_ms=response.duration_ms,
                    health=response.health,
                    result=response.result,
                    artifacts=response.artifacts,
                    warnings=response.warnings,
                    error=RuntimeErrorInfo(
                        error_type=response.error.error_type,
                        message=response.error.message,
                        traceback=response.error.traceback,
                        detail={
                            **response.error.detail,
                            "stage": "backend_execute",
                        },
                    ),
                )

            report_warnings: tuple[str, ...] = ()
            if report_job is not None and response.status == "succeeded":
                try:
                    from dp_engine.report_backend.job import (
                        validate_report_backend_workspace_output,
                        validate_report_backend_worker_output,
                    )

                    validate_report_backend_workspace_output(
                        report_job,
                        workspace_path,
                    )
                    report_warnings = validate_report_backend_worker_output(
                        report_job,
                        provider_id=skill_id,
                        provider_version=version,
                        result=response.result,
                        declarations=declarations,
                    )
                except ValueError as contract_error:
                    return self._build_operation_error_response(
                        task_id,
                        started_at,
                        "protocol_error",
                        redact_text(
                            f"Report backend output rejected: {contract_error}",
                            redaction_values,
                        ),
                        "RuntimeProtocolError",
                        redact_text(str(contract_error), redaction_values),
                        time.monotonic() - t_start,
                        operation,
                        stage="output_contract",
                    )

            # ── Batch 3.2.1B: Artifact publishing ──
            published_artifacts: tuple[RuntimeArtifact, ...] = ()

            if (
                response.operation == operation
                and response.status == "succeeded"
                and declarations
                and not self._cancelled
            ):
                try:
                    from dp_engine.skills.runtime_artifacts import ArtifactPublisher

                    publisher = ArtifactPublisher()
                    published_artifacts = publisher.publish_artifacts(
                        declarations,
                        workspace_output=workspace_path / "output",
                        skill_id=skill_id,
                        version=version,
                        task_id=task_id,
                        operation=operation,
                        check_cancelled=lambda: self._cancelled,
                    )
                except Exception as pub_err:
                    # Publishing failed — determine correct status
                    from dp_engine.skills.runtime_artifacts import (
                        _CancelBeforeCommitError,
                    )
                    if isinstance(pub_err, _CancelBeforeCommitError):
                        self._cancelled = True
                        response = self._build_operation_error_response(
                            task_id, started_at, "cancelled",
                            redact_text(
                                "Report generation cancelled by user"
                                if report_job is not None
                                else "Run cancelled by user",
                                redaction_values,
                            ),
                            "RuntimeCancelledError",
                            redact_text("Cancel before commit", redaction_values),
                            time.monotonic() - t_start,
                            operation,
                            stage=(
                                "artifact_publish"
                                if report_job is not None else None
                            ),
                        )
                        return response
                    else:
                        # Distinguish protocol_error from failed
                        err_msg = str(pub_err)
                        is_protocol = any(
                            keyword in err_msg.lower()
                            for keyword in (
                                "symlink", "reparse", "hardlink",
                                "escapes", "not within", "media_type",
                                "sniff", "content", "extension",
                                "sha256", "hash", "fingerprint",
                                "device", "inode", "size changed",
                                "nlink", "not a regular file",
                                "reparse point", "valid zip", "ooxml",
                            )
                        )
                        status = "protocol_error" if is_protocol else "failed"
                        response = SkillRuntimeResponse(
                            protocol_version=RUNTIME_PROTOCOL_VERSION,
                            task_id=task_id,
                            operation=operation,
                            success=False,
                            status=status,
                            message=redact_text(
                                f"Artifact publish failed: {err_msg}",
                                redaction_values,
                            ),
                            started_at=started_at,
                            finished_at=_now_iso(),
                            duration_ms=duration_ms,
                            error=RuntimeErrorInfo(
                                error_type=(
                                    "RuntimeProtocolError" if is_protocol
                                    else "RuntimeError"
                                ),
                                message=redact_text(err_msg, redaction_values),
                                detail=(
                                    {"stage": "artifact_publish"}
                                    if report_job is not None else {}
                                ),
                            ),
                            artifacts=(),
                        )
                        return response

            if report_job is not None and published_artifacts:
                try:
                    from dp_engine.report_backend.job import (
                        validate_published_report_artifact,
                    )

                    if len(published_artifacts) != 1:
                        raise ValueError(
                            "report backend published an invalid artifact count"
                        )
                    validate_published_report_artifact(
                        report_job,
                        published_artifacts[0],
                        provider_id=skill_id,
                        provider_version=version,
                        task_id=task_id,
                    )
                except ValueError as contract_error:
                    from dp_engine.skills.runtime_artifacts import ArtifactStore

                    try:
                        ArtifactStore().delete_task(skill_id, task_id)
                    except RuntimeError:
                        logger.exception(
                            "Failed to remove rejected report artifact task"
                        )
                    return self._build_operation_error_response(
                        task_id,
                        started_at,
                        "protocol_error",
                        redact_text(
                            f"Published report rejected: {contract_error}",
                            redaction_values,
                        ),
                        "RuntimeProtocolError",
                        redact_text(str(contract_error), redaction_values),
                        time.monotonic() - t_start,
                        operation,
                        stage="published_validation",
                    )

            # ── Build final response with published artifacts ──
            if published_artifacts:
                # Check for non-empty declarations on non-success status
                # (caught by Worker — but defence in depth)
                response = SkillRuntimeResponse(
                    protocol_version=response.protocol_version,
                    task_id=response.task_id,
                    operation=response.operation,
                    success=response.success,
                    status=response.status,
                    message=response.message,
                    started_at=response.started_at,
                    finished_at=response.finished_at,
                    duration_ms=response.duration_ms,
                    health=response.health,
                    result=response.result,
                    artifacts=published_artifacts,
                    warnings=(
                        report_warnings
                        if report_job is not None
                        else response.warnings
                    ),
                    error=response.error,
                )

            # ── Double-layer redaction on response for defence-in-depth (Batch 3.1.1B) ──
            if response.error is not None and redaction_values:
                response = SkillRuntimeResponse(
                    protocol_version=response.protocol_version,
                    task_id=response.task_id,
                    operation=response.operation,
                    success=response.success,
                    status=response.status,
                    message=redact_text(response.message, redaction_values),
                    started_at=response.started_at,
                    finished_at=response.finished_at,
                    duration_ms=response.duration_ms,
                    health=response.health,
                    result=response.result,
                    artifacts=response.artifacts,
                    warnings=response.warnings,
                    error=RuntimeErrorInfo(
                        error_type=response.error.error_type,
                        message=redact_text(response.error.message, redaction_values),
                        detail=response.error.detail,
                        traceback=(
                            redact_text(response.error.traceback or "", redaction_values)
                            if response.error.traceback else None
                        ),
                    ),
                )

            # ── Batch 3.1.1B-S: redact workspace log files ──
            from dp_engine.skills.runtime_worker import (
                _redact_and_truncate_io,
            )
            _redact_and_truncate_io(
                workspace_path / "stdout.log",
                MAX_STDOUT_BYTES,
                redaction_values,
            )
            _redact_and_truncate_io(
                workspace_path / "stderr.log",
                MAX_STDERR_BYTES,
                redaction_values,
            )

            # Clean up on success
            cleanup_workspace(workspace_path, succeeded=True)

            return response

        except SkillRuntimeError:
            raise
        except Exception as e:
            logger.exception("Unexpected error in artifact operation")
            raise SkillRuntimeError(
                f"{operation} failed: {e}"
            ) from e
        finally:
            if report_job is not None:
                self._sanitize_report_backend_logs(workspace_path_for(task_id))
            self._process = None
            self._cancelled = False
            self._task_id = None

    def cancel(self) -> None:
        """Cancel the currently running operation (healthcheck or run).

        Sequence: write cancel marker → grace → terminate → wait → kill
        Grace periods use the configurable instance values.
        """
        self._cancelled = True
        if self._process is None:
            return

        ws = self._current_workspace()
        if ws is not None:
            write_cancel_marker(ws)

        # Grace period (configurable)
        try:
            self._process.wait(timeout=self._cancel_grace)
            return  # Already exited
        except subprocess.TimeoutExpired:
            pass

        # Terminate (configurable)
        try:
            self._process.terminate()
            self._process.wait(timeout=self._terminate_grace)
            return
        except subprocess.TimeoutExpired:
            pass

        # Kill (configurable)
        try:
            self._process.kill()
            self._process.wait(timeout=self._kill_grace)
        except subprocess.TimeoutExpired:
            logger.error("Failed to kill worker process")

    # ── Pre-flight gates ──

    def _run_preflight_gates(
        self, skill_id: str, version: str
    ):
        """Run all 8 pre-flight checks. Returns the InstalledSkill on success.

        Raises SkillRuntimeError if any check fails.
        """
        from dp_engine.skills.runtime_errors import (
            RuntimeDependencyError,
            RuntimePermissionError,
        )
        from dp_engine.skills.manifest_parser import parse_skill_manifest

        # Gate 1: Skill exists in registry
        skill = self._registry.get(skill_id, version)
        if skill is None:
            raise SkillRuntimeError(
                f"Skill '{skill_id}@{version}' not found in registry"
            )

        # Gate 2: install_path within managed installed dir
        ip = Path(skill.install_path)
        try:
            ip.resolve().relative_to(self._installed_dir)
        except ValueError:
            raise SkillRuntimeError(
                f"install_path outside managed directory: {skill.install_path}"
            )

        # Gate 3: FULL consistency check, no blocking issues
        issues = self._registry.validate_installation_consistency(
            self._installed_dir,
            mode=ConsistencyCheckMode.FULL,
        )
        blocking = [i for i in issues if i.severity == "blocking"]
        if blocking:
            codes = [i.code for i in blocking]
            raise SkillRuntimeError(
                f"Registry consistency check found {len(blocking)} blocking issue(s): "
                f"{', '.join(codes)}"
            )

        # Gate 4: Manifest has healthcheck entrypoint
        manifest = parse_skill_manifest(ip)
        healthcheck_ep = manifest.entrypoints.get(HEALTHCHECK_ENTRYPOINT_KEY)
        if not healthcheck_ep:
            self._update_health_status(
                skill_id, version, "not_supported",
                "Manifest has no healthcheck entrypoint",
            )
            raise SkillRuntimeError(
                f"Skill '{skill_id}@{version}' has no healthcheck entrypoint"
            )

        # Gate 5: Dependencies satisfied
        deps = manifest.dependencies
        if deps:
            dep_report = self._dep_checker.check(skill_id, version, deps)
            if not dep_report.all_satisfied:
                self._update_health_status(
                    skill_id, version, "dependency_missing",
                    dep_report.summary,
                )
                raise RuntimeDependencyError(
                    f"Dependencies not satisfied for '{skill_id}@{version}': "
                    f"{dep_report.summary}",
                    detail={"dependency_report": dep_report},
                )

        # Gate 6: Capabilities in allowed set
        requested_caps = set(manifest.capabilities)
        # Only grant default healthcheck caps
        allowed_caps = set(DEFAULT_HEALTHCHECK_CAPABILITIES)
        forbidden = requested_caps & FORBIDDEN_HEALTHCHECK_CAPABILITIES
        if forbidden:
            raise RuntimePermissionError(
                f"Skill '{skill_id}@{version}' requests forbidden capabilities: "
                f"{sorted(forbidden)}"
            )

        # Extra caps beyond defaults are denied
        extra = requested_caps - allowed_caps - FORBIDDEN_HEALTHCHECK_CAPABILITIES
        if extra:
            raise RuntimePermissionError(
                f"Skill '{skill_id}@{version}' requests unrecognized capabilities: "
                f"{sorted(extra)}. Healthcheck only allows: {sorted(allowed_caps)}"
            )

        return skill

    def _run_preflight_gates_for_run(
        self, skill_id: str, version: str
    ):
        """Run the existing zero-write preflight for ``entrypoints.run``."""
        return self._run_preflight_gates_for_artifact_operation(
            skill_id,
            version,
            entrypoint_key=RUN_ENTRYPOINT_KEY,
            operation_label="Run",
        )

    def _run_preflight_gates_for_report(
        self,
        skill_id: str,
        version: str,
        artifact_type: str,
        template_mode: str,
    ):
        """Require an active, enabled, healthy and compatible report backend."""
        from dp_engine.report_backend.models import (
            extract_report_backend_template_modes,
            validate_report_backend_manifest,
        )

        skill = self._run_preflight_gates_for_artifact_operation(
            skill_id,
            version,
            entrypoint_key="generate",
            operation_label="Report generation",
        )
        manifest_issues = validate_report_backend_manifest(skill.manifest)
        if manifest_issues:
            codes = ", ".join(issue.code for issue in manifest_issues)
            raise SkillRuntimeError(
                f"Report backend manifest is incompatible: {codes}"
            )

        snapshot = self._registry.snapshot()
        if snapshot.active_versions.get(skill_id) != version:
            raise SkillRuntimeError(
                f"Report backend '{skill_id}@{version}' is not the active version"
            )
        if not skill.enabled:
            raise SkillRuntimeError(
                f"Report backend '{skill_id}@{version}' is not enabled"
            )
        if skill.health_status != "healthy":
            raise SkillRuntimeError(
                f"Report backend '{skill_id}@{version}' is not healthy"
            )
        if artifact_type not in skill.manifest.artifact_types:
            raise SkillRuntimeError(
                f"Report backend does not support {artifact_type}"
            )
        template_modes = extract_report_backend_template_modes(skill.manifest)
        if template_mode not in template_modes:
            raise SkillRuntimeError(
                f"Report backend does not support {template_mode} templates"
            )
        return skill

    def _run_preflight_gates_for_artifact_operation(
        self,
        skill_id: str,
        version: str,
        *,
        entrypoint_key: str,
        operation_label: str,
    ):
        """Shared zero-write gates for artifact-producing Runtime operations."""
        from dp_engine.skills.runtime_errors import (
            RuntimeDependencyError,
            RuntimePermissionError,
        )
        from dp_engine.skills.manifest_parser import parse_skill_manifest

        # Gate 1: Skill exists in registry
        skill = self._registry.get(skill_id, version)
        if skill is None:
            raise SkillRuntimeError(
                f"Skill '{skill_id}@{version}' not found in registry"
            )

        # Gate 2: install_path within managed installed dir
        ip = Path(skill.install_path)
        try:
            ip.resolve().relative_to(self._installed_dir)
        except ValueError:
            raise SkillRuntimeError(
                f"install_path outside managed directory: {skill.install_path}"
            )

        # Gate 3: FULL consistency check, no blocking issues
        issues = self._registry.validate_installation_consistency(
            self._installed_dir,
            mode=ConsistencyCheckMode.FULL,
        )
        blocking = [i for i in issues if i.severity == "blocking"]
        if blocking:
            codes = [i.code for i in blocking]
            raise SkillRuntimeError(
                f"Registry consistency check found {len(blocking)} blocking issue(s): "
                f"{', '.join(codes)}"
            )

        # Gate 4: Manifest has the requested callable entrypoint
        manifest = parse_skill_manifest(ip)
        entrypoint = manifest.entrypoints.get(entrypoint_key)
        if not entrypoint:
            raise SkillRuntimeError(
                f"Skill '{skill_id}@{version}' has no {entrypoint_key} entrypoint"
            )

        # Gate 5: Dependencies satisfied (check only, no install)
        deps = manifest.dependencies
        if deps:
            dep_report = self._dep_checker.check(skill_id, version, deps)
            if not dep_report.all_satisfied:
                raise RuntimeDependencyError(
                    f"Dependencies not satisfied for '{skill_id}@{version}': "
                    f"{dep_report.summary}",
                    detail={"dependency_report": dep_report},
                )

        # Gate 6: Capabilities — same set as healthcheck
        requested_caps = set(manifest.capabilities)
        allowed_caps = set(DEFAULT_HEALTHCHECK_CAPABILITIES)
        forbidden = requested_caps & FORBIDDEN_HEALTHCHECK_CAPABILITIES
        if forbidden:
            raise RuntimePermissionError(
                f"Skill '{skill_id}@{version}' requests forbidden capabilities: "
                f"{sorted(forbidden)}"
            )
        extra = requested_caps - allowed_caps - FORBIDDEN_HEALTHCHECK_CAPABILITIES
        if extra:
            raise RuntimePermissionError(
                f"Skill '{skill_id}@{version}' requests unrecognized capabilities: "
                f"{sorted(extra)}. {operation_label} only allows: "
                f"{sorted(allowed_caps)}"
            )

        return skill

    # ── Helpers ──

    def _build_request(
        self,
        skill_id: str,
        version: str,
        installed_path: str,
        workspace_path: str,
        task_id: str,
        timeout_seconds: float,
    ) -> SkillRuntimeRequest:
        """Build a validated SkillRuntimeRequest."""
        # Get manifest to extract healthcheck entrypoint
        from dp_engine.skills.manifest_parser import parse_skill_manifest
        manifest = parse_skill_manifest(Path(installed_path))
        healthcheck_ep = manifest.entrypoints.get(HEALTHCHECK_ENTRYPOINT_KEY, "")

        return SkillRuntimeRequest(
            protocol_version=RUNTIME_PROTOCOL_VERSION,
            task_id=task_id,
            operation="healthcheck",
            skill_id=skill_id,
            version=version,
            installed_path=installed_path,
            entrypoint=healthcheck_ep,
            workspace_path=workspace_path,
            timeout_seconds=timeout_seconds,
            capabilities=DEFAULT_HEALTHCHECK_CAPABILITIES,
            environment={},
        )

    def _build_run_request(
        self,
        skill_id: str,
        version: str,
        installed_path: str,
        workspace_path: str,
        task_id: str,
        timeout_seconds: float,
        params: dict[str, object],
    ) -> SkillRuntimeRequest:
        """Build a validated SkillRuntimeRequest for operation='run'."""
        return self._build_artifact_operation_request(
            skill_id,
            version,
            installed_path,
            workspace_path,
            task_id,
            timeout_seconds,
            params,
            "run",
            RUN_ENTRYPOINT_KEY,
        )

    def _build_artifact_operation_request(
        self,
        skill_id: str,
        version: str,
        installed_path: str,
        workspace_path: str,
        task_id: str,
        timeout_seconds: float,
        params: dict[str, object],
        operation: str,
        entrypoint_key: str,
    ) -> SkillRuntimeRequest:
        """Build a request for a validated artifact-producing entrypoint."""
        from dp_engine.skills.manifest_parser import parse_skill_manifest
        manifest = parse_skill_manifest(Path(installed_path))
        entrypoint = manifest.entrypoints.get(entrypoint_key, "")

        return SkillRuntimeRequest(
            protocol_version=RUNTIME_PROTOCOL_VERSION,
            task_id=task_id,
            operation=operation,
            skill_id=skill_id,
            version=version,
            installed_path=installed_path,
            entrypoint=entrypoint,
            workspace_path=workspace_path,
            timeout_seconds=timeout_seconds,
            capabilities=DEFAULT_HEALTHCHECK_CAPABILITIES,
            environment={},
            params=params,
        )

    def _build_run_error_response(
        self,
        task_id: str,
        started_at: str,
        status: str,
        message: str,
        error_type: str,
        error_message: str,
        elapsed: float,
    ) -> SkillRuntimeResponse:
        """Build a SkillRuntimeResponse for a run error (timeout/cancel/crash)."""
        return self._build_operation_error_response(
            task_id,
            started_at,
            status,
            message,
            error_type,
            error_message,
            elapsed,
            "run",
        )

    def _build_operation_error_response(
        self,
        task_id: str,
        started_at: str,
        status: str,
        message: str,
        error_type: str,
        error_message: str,
        elapsed: float,
        operation: str,
        *,
        stage: str | None = None,
    ) -> SkillRuntimeResponse:
        """Build a typed Runtime failure for an artifact operation."""
        return SkillRuntimeResponse(
            protocol_version=RUNTIME_PROTOCOL_VERSION,
            task_id=task_id,
            operation=operation,
            success=False,
            status=status,
            message=message,
            started_at=started_at,
            finished_at=_now_iso(),
            duration_ms=int(elapsed * 1000),
            error=RuntimeErrorInfo(
                error_type=error_type,
                message=error_message,
                detail={"stage": stage} if stage is not None else {},
            ),
        )

    def _run_subprocess(
        self,
        workspace_path: Path,
        request_path: Path,
        timeout_seconds: float,
    ) -> None:
        """Launch the worker subprocess and wait for completion.

        Raises RuntimeTimeoutError on timeout, RuntimeCancelledError on cancel.
        """
        from dp_engine.skills.runtime_permissions import build_sanitized_env

        env = build_sanitized_env()

        # Add project root to PYTHONPATH so worker can import dp_engine
        # (only the project root — user PYTHONPATH is already stripped by
        # build_sanitized_env)
        project_root = str(Path(__file__).resolve().parent.parent.parent)
        env["PYTHONPATH"] = project_root

        # Build command as a parameter array (NO shell=True)
        # Use -s to disable user site-packages, but NOT -I (isolated)
        # so the worker can import project modules via controlled PYTHONPATH.
        cmd = [
            sys.executable,
            "-s",
            str(self._worker_path),
            "--request",
            str(request_path),
        ]

        logger.info(
            "Launching worker: %s (timeout=%.1fs)", " ".join(cmd), timeout_seconds
        )

        try:
            self._process = subprocess.Popen(
                cmd,
                cwd=str(workspace_path),
                env=env,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                shell=False,
            )
        except OSError as e:
            raise SkillRuntimeError(
                f"Failed to launch worker process: {e}"
            ) from e

        # Poll with timeout and cancel check
        poll_interval = self._poll_interval
        elapsed = 0.0

        while elapsed < timeout_seconds:
            # Check cancel
            if self._cancelled:
                self._cleanup_process()
                raise RuntimeCancelledError(
                    f"Healthcheck cancelled by user (task_id={self._task_id})"
                )

            try:
                returncode = self._process.wait(timeout=poll_interval)
                # Process finished
                return
            except subprocess.TimeoutExpired:
                elapsed += poll_interval
                continue

        # Timeout
        self._cleanup_process()
        raise RuntimeTimeoutError(
            f"Healthcheck timed out after {timeout_seconds:.1f}s "
            f"(task_id={self._task_id})",
            detail={"timeout_seconds": timeout_seconds},
        )

    def _cleanup_process(self) -> None:
        """Terminate and clean up the worker process."""
        if self._process is None:
            return
        try:
            self._process.terminate()
            self._process.wait(timeout=self._terminate_grace)
        except (subprocess.TimeoutExpired, OSError):
            try:
                self._process.kill()
                self._process.wait(timeout=self._kill_grace)
            except (subprocess.TimeoutExpired, OSError):
                pass

    def _current_workspace(self) -> Path | None:
        """Get current workspace path, if any."""
        if self._task_id:
            return workspace_path_for(self._task_id)
        return None

    def _update_health_status(
        self,
        skill_id: str,
        version: str,
        status: str,
        message: str | None,
    ) -> None:
        """Update the health status in the registry (transaction-safe)."""
        try:
            self._registry.update_health_status(
                skill_id, version, status, message
            )
            self._registry.save()
        except Exception as e:
            logger.error(
                "Failed to update health status for %s@%s: %s",
                skill_id, version, e,
            )
            raise SkillRuntimeError(
                f"Failed to persist health status: {e}"
            ) from e

    def _read_stderr_tail(
        self, workspace_path: Path, redaction_values: set[str] | None = None
    ) -> str:
        """Read the tail of stderr.log for diagnostics.

        Redacts BEFORE truncation to uphold the contract:
        final_output = truncate(redact(raw_output)).

        WARNING: If this function is called without redaction_values, the
        raw stderr content MAY contain unredacted secrets.  All call-sites
        that expose stderr content externally MUST pass redaction_values.
        """
        stderr_path = workspace_path / "stderr.log"
        if not stderr_path.is_file():
            return ""
        rv: set[str] = redaction_values or set()
        try:
            raw_bytes = stderr_path.read_bytes()
        except OSError:
            return ""
        if not raw_bytes:
            return ""
        # Step 1: redact the full content
        raw_text = raw_bytes.decode("utf-8", errors="replace")
        redacted = raw_text
        if rv:
            redacted = redact_text(raw_text, rv)
        # Step 2: truncate to last 2000 bytes
        redacted_bytes = redacted.encode("utf-8")
        tail_size = min(len(redacted_bytes), 2000)
        if len(redacted_bytes) > tail_size:
            tail_bytes = redacted_bytes[-tail_size:]
        else:
            tail_bytes = redacted_bytes
        # Repair to valid UTF-8 boundary at the start
        for cut in range(min(4, len(tail_bytes))):
            try:
                return tail_bytes[cut:].decode("utf-8")
            except UnicodeDecodeError:
                continue
        return tail_bytes.decode("utf-8", errors="replace")

    @staticmethod
    def _sanitize_report_backend_logs(workspace_path: Path) -> None:
        """Remove plugin-authored stdout/stderr so job content cannot persist."""
        marker = "[report backend process output omitted]\n"
        for filename in ("stdout.log", "stderr.log"):
            path = workspace_path / filename
            if not path.exists():
                continue
            try:
                path.write_text(marker, encoding="utf-8")
            except OSError:
                logger.exception("Failed to sanitize report backend log")

    def _build_timeout_response(
        self,
        task_id: str,
        started_at: str,
        timeout_seconds: float,
        elapsed: float,
    ) -> SkillRuntimeResponse:
        return SkillRuntimeResponse(
            protocol_version=RUNTIME_PROTOCOL_VERSION,
            task_id=task_id,
            operation="healthcheck",
            success=False,
            status="timeout",
            message=f"Healthcheck timed out after {timeout_seconds:.1f}s",
            started_at=started_at,
            finished_at=_now_iso(),
            duration_ms=int(elapsed * 1000),
            error=RuntimeErrorInfo(
                error_type="RuntimeTimeoutError",
                message=f"Timeout after {timeout_seconds:.1f}s",
                detail={"timeout_seconds": timeout_seconds},
            ),
        )

    def _build_cancelled_response(
        self,
        task_id: str,
        started_at: str,
        elapsed: float,
    ) -> SkillRuntimeResponse:
        return SkillRuntimeResponse(
            protocol_version=RUNTIME_PROTOCOL_VERSION,
            task_id=task_id,
            operation="healthcheck",
            success=False,
            status="cancelled",
            message="Healthcheck cancelled by user",
            started_at=started_at,
            finished_at=_now_iso(),
            duration_ms=int(elapsed * 1000),
        )
