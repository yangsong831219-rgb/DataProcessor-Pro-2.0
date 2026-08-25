"""Host-owned Controlled Tool Runner for the PPT Master toolchain.

The public Interface accepts typed operations, never raw command lines.  The
Implementation verifies the confirmed plan, installed bundle, Python runtime,
workspace, process limits, logs, and output provenance around one isolated
worker invocation.
"""

from __future__ import annotations

import ctypes
import hashlib
import importlib.metadata
import json
import os
import stat
import subprocess
import sys
import sysconfig
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import StrEnum
from pathlib import Path, PurePosixPath
from typing import Annotated, Any, Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from dp_engine.ppt_master_host.bundle_store import (
    PPT_MASTER_2_7_0_TOOLCHAIN_TREE_SHA256,
    PptMasterBundleStore,
    PptMasterBundleStoreError,
    PptMasterInstalledBundle,
)
from dp_engine.ppt_master_host.planning import PlanningPhase, PlanningSnapshot
from dp_engine.ppt_master_host.source_bundle import PPT_MASTER_2_7_0_CONTRACT


_ID_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$"
_PROJECT_NAME_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9_-]{0,47}$"
_OUTPUT_NAME_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9._-]{0,62}\.pptx$"
_BUFFER_CHUNK_BYTES = 64 * 1024
_HASH_CHUNK_BYTES = 1024 * 1024
_DEFAULT_REQUIRED_DISTRIBUTIONS = (
    "python-pptx",
    "Pillow",
    "lxml",
    "PyYAML",
    "XlsxWriter",
)


class _StrictModel(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        str_strip_whitespace=True,
    )


class ControlledToolCommand(StrEnum):
    PROJECT_INIT = "project_init"
    SVG_QUALITY_CHECK = "svg_quality_check"
    FINALIZE_SVG = "finalize_svg"
    SVG_TO_PPTX = "svg_to_pptx"


class QualityStage(StrEnum):
    FIRST_PAGE = "first-page"
    FINAL = "final"


class PptxStructure(StrEnum):
    FLAT = "flat"
    STRUCTURED = "structured"


class ControlledRunStatus(StrEnum):
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"
    TIMED_OUT = "timed_out"
    RESOURCE_REJECTED = "resource_rejected"


class ProcessTermination(StrEnum):
    COMPLETED = "completed"
    CANCELLED = "cancelled"
    TIMED_OUT = "timed_out"


def _validated_relative_path(value: str, *, field: str) -> str:
    normalized = value.replace("\\", "/")
    pure = PurePosixPath(normalized)
    if (
        not normalized
        or pure.is_absolute()
        or ":" in normalized
        or any(part in {"", ".", ".."} for part in pure.parts)
    ):
        raise ValueError(f"{field} must be a confined relative path")
    return pure.as_posix()


class ProjectInitArguments(_StrictModel):
    kind: Literal[ControlledToolCommand.PROJECT_INIT] = (
        ControlledToolCommand.PROJECT_INIT
    )
    project_name: str = Field(pattern=_PROJECT_NAME_PATTERN)
    canvas_format: Literal["ppt169"] = "ppt169"


class SvgQualityArguments(_StrictModel):
    kind: Literal[ControlledToolCommand.SVG_QUALITY_CHECK] = (
        ControlledToolCommand.SVG_QUALITY_CHECK
    )
    project_dir: str
    canvas_format: Literal["ppt169"] = "ppt169"
    stage: QualityStage = QualityStage.FINAL

    @field_validator("project_dir")
    @classmethod
    def _validate_project_dir(cls, value: str) -> str:
        return _validated_relative_path(value, field="project_dir")


class FinalizeSvgArguments(_StrictModel):
    kind: Literal[ControlledToolCommand.FINALIZE_SVG] = (
        ControlledToolCommand.FINALIZE_SVG
    )
    project_dir: str

    @field_validator("project_dir")
    @classmethod
    def _validate_project_dir(cls, value: str) -> str:
        return _validated_relative_path(value, field="project_dir")


class SvgToPptxArguments(_StrictModel):
    kind: Literal[ControlledToolCommand.SVG_TO_PPTX] = (
        ControlledToolCommand.SVG_TO_PPTX
    )
    project_dir: str
    output_name: str = Field(pattern=_OUTPUT_NAME_PATTERN)
    canvas_format: Literal["ppt169"] = "ppt169"
    structure: PptxStructure

    @field_validator("project_dir")
    @classmethod
    def _validate_project_dir(cls, value: str) -> str:
        return _validated_relative_path(value, field="project_dir")


ControlledToolArguments = Annotated[
    ProjectInitArguments
    | SvgQualityArguments
    | FinalizeSvgArguments
    | SvgToPptxArguments,
    Field(discriminator="kind"),
]


class ControlledToolLimits(_StrictModel):
    timeout_seconds: float = Field(default=120.0, ge=1.0, le=300.0)
    cpu_seconds: int = Field(default=90, ge=1, le=240)
    memory_bytes: int = Field(
        default=768 * 1024 * 1024,
        ge=128 * 1024 * 1024,
        le=1024 * 1024 * 1024,
    )
    max_stdout_bytes: int = Field(default=512 * 1024, ge=1024, le=2 * 1024 * 1024)
    max_stderr_bytes: int = Field(default=512 * 1024, ge=1024, le=2 * 1024 * 1024)
    max_artifact_count: int = Field(default=2_000, ge=1, le=5_000)
    max_artifact_bytes: int = Field(
        default=100 * 1024 * 1024,
        ge=1024,
        le=250 * 1024 * 1024,
    )
    max_total_artifact_bytes: int = Field(
        default=500 * 1024 * 1024,
        ge=1024,
        le=750 * 1024 * 1024,
    )


class ControlledToolRequest(_StrictModel):
    run_id: str = Field(pattern=_ID_PATTERN)
    workspace_id: str = Field(pattern=_ID_PATTERN)
    arguments: ControlledToolArguments
    limits: ControlledToolLimits = Field(default_factory=ControlledToolLimits)


class RuntimeDependencyAttestation(_StrictModel):
    name: str = Field(min_length=1, max_length=100)
    version: str = Field(min_length=1, max_length=100)
    content_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    file_count: int = Field(ge=1)
    import_root: str = Field(min_length=1)
    read_roots: tuple[str, ...] = Field(min_length=1)


class PythonRuntimeAttestation(_StrictModel):
    executable: str = Field(min_length=1)
    executable_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    python_version: str = Field(min_length=1, max_length=100)
    dependencies: tuple[RuntimeDependencyAttestation, ...] = ()

    @model_validator(mode="after")
    def _validate_dependency_names(self) -> PythonRuntimeAttestation:
        names = [item.name.casefold() for item in self.dependencies]
        if len(names) != len(set(names)):
            raise ValueError("runtime dependency names must be unique")
        return self

    @classmethod
    def capture(
        cls,
        *,
        executable: str | Path | None = None,
        distributions: tuple[str, ...] = _DEFAULT_REQUIRED_DISTRIBUTIONS,
    ) -> PythonRuntimeAttestation:
        _exec: str | Path = executable if executable is not None else (
            getattr(sys, "_base_executable", None) or sys.executable
        )
        executable_path = Path(_exec).resolve(strict=True)
        dependencies = tuple(
            _capture_distribution(name) for name in sorted(distributions, key=str.casefold)
        )
        return cls(
            executable=str(executable_path),
            executable_sha256=_sha256_file(executable_path),
            python_version=sys.version.split()[0],
            dependencies=dependencies,
        )

    def verify(self) -> None:
        current = self.capture(
            executable=self.executable,
            distributions=tuple(item.name for item in self.dependencies),
        )
        if current != self:
            raise ControlledToolError(
                "runtime_attestation_mismatch",
                "Python executable or dependency content changed after attestation",
            )

    @property
    def sha256(self) -> str:
        return _model_sha256(self)

    @property
    def approved_read_roots(self) -> tuple[str, ...]:
        roots = {
            root
            for dependency in self.dependencies
            for root in dependency.read_roots
        }
        return tuple(sorted(roots, key=str.casefold))

    @property
    def dependency_sys_paths(self) -> tuple[str, ...]:
        return tuple(
            sorted(
                {item.import_root for item in self.dependencies},
                key=str.casefold,
            )
        )


class ToolchainRunAttestation(_StrictModel):
    version: str
    archive_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    tree_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    file_count: int = Field(ge=1)
    total_bytes: int = Field(ge=1)


class OutputLogAttestation(_StrictModel):
    relative_path: str
    observed_bytes: int = Field(ge=0)
    captured_bytes: int = Field(ge=0)
    stream_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    log_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    truncated: bool


class ToolArtifactAttestation(_StrictModel):
    relative_path: str
    size_bytes: int = Field(ge=0)
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


class ControlledToolResult(_StrictModel):
    schema_version: Literal[1] = 1
    run_id: str
    workspace_id: str
    command: ControlledToolCommand
    status: ControlledRunStatus
    planning_snapshot_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    toolchain: ToolchainRunAttestation
    runtime_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    started_at: str
    finished_at: str
    duration_ms: int = Field(ge=0)
    exit_code: int | None
    error_code: str = ""
    message: str = ""
    stdout: OutputLogAttestation
    stderr: OutputLogAttestation
    artifacts: tuple[ToolArtifactAttestation, ...] = ()
    audit_dir: str

    @property
    def succeeded(self) -> bool:
        return self.status == ControlledRunStatus.SUCCEEDED


class ControlledToolError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


class ControlledToolchainVerifier(Protocol):
    def verify(
        self,
        *,
        cancel_check: Callable[[], bool] | None,
    ) -> PptMasterInstalledBundle: ...


class PptMasterInstalledToolchainVerifier:
    """Adapter from the Runner Seam to the Managed Source Bundle Store."""

    def __init__(self, store: PptMasterBundleStore | None = None) -> None:
        self._store = store or PptMasterBundleStore()

    def verify(
        self,
        *,
        cancel_check: Callable[[], bool] | None,
    ) -> PptMasterInstalledBundle:
        try:
            return self._store.verify_installed(
                PPT_MASTER_2_7_0_CONTRACT.version,
                PPT_MASTER_2_7_0_CONTRACT.archive_sha256,
                cancel_check=cancel_check,
            )
        except PptMasterBundleStoreError as error:
            raise ControlledToolError(
                f"toolchain_{error.code}",
                f"Managed Source Bundle verification failed: {error.code}",
            ) from error


@dataclass(frozen=True, slots=True)
class ProcessLaunch:
    argv: tuple[str, ...]
    cwd: Path
    environment: dict[str, str]
    timeout_seconds: float
    cpu_seconds: int
    memory_bytes: int
    max_stdout_bytes: int
    max_stderr_bytes: int


@dataclass(frozen=True, slots=True)
class ProcessExecution:
    termination: ProcessTermination
    exit_code: int | None
    duration_ms: int
    stdout: bytes
    stderr: bytes
    stdout_observed_bytes: int
    stderr_observed_bytes: int
    stdout_sha256: str
    stderr_sha256: str
    stdout_truncated: bool
    stderr_truncated: bool


class ControlledProcessAdapter(Protocol):
    def execute(
        self,
        launch: ProcessLaunch,
        *,
        cancel_check: Callable[[], bool] | None,
    ) -> ProcessExecution: ...


class _StreamCapture:
    def __init__(self, limit: int) -> None:
        self._limit = limit
        self.buffer = bytearray()
        self.observed = 0
        self.digest = hashlib.sha256()
        self.truncated = False

    def drain(self, stream: Any) -> None:
        while True:
            chunk = stream.read(_BUFFER_CHUNK_BYTES)
            if not chunk:
                return
            self.observed += len(chunk)
            self.digest.update(chunk)
            remaining = self._limit - len(self.buffer)
            if remaining > 0:
                self.buffer.extend(chunk[:remaining])
            if len(chunk) > remaining:
                self.truncated = True


class SubprocessControlledProcessAdapter:
    """Windows process Adapter with a one-process, kill-on-close Job."""

    def execute(
        self,
        launch: ProcessLaunch,
        *,
        cancel_check: Callable[[], bool] | None,
    ) -> ProcessExecution:
        if sys.platform != "win32":
            raise ControlledToolError(
                "platform_unsupported",
                "Controlled PPT Master execution currently requires Windows Job Objects",
            )
        if cancel_check is not None and cancel_check():
            return _empty_process_execution(ProcessTermination.CANCELLED)

        started = time.monotonic()
        creation_flags = (
            int(getattr(subprocess, "CREATE_NO_WINDOW", 0))
            | int(getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0))
            | 0x00000004  # CREATE_SUSPENDED
        )
        try:
            process = subprocess.Popen(
                list(launch.argv),
                cwd=str(launch.cwd),
                env=launch.environment,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                shell=False,
                creationflags=creation_flags,
            )
        except OSError as error:
            raise ControlledToolError(
                "process_launch_failed",
                f"Controlled worker launch failed: {type(error).__name__}",
            ) from error

        job: _WindowsJob | None = None
        try:
            job = _WindowsJob(
                process,
                cpu_seconds=launch.cpu_seconds,
                memory_bytes=launch.memory_bytes,
            )
            job.resume(process)
        except Exception as error:
            process.kill()
            process.wait()
            if job is not None:
                job.close()
            raise ControlledToolError(
                "process_isolation_failed",
                f"Windows Job isolation failed: {type(error).__name__}",
            ) from error

        stdout_capture = _StreamCapture(launch.max_stdout_bytes)
        stderr_capture = _StreamCapture(launch.max_stderr_bytes)
        stdout_thread = threading.Thread(
            target=stdout_capture.drain,
            args=(process.stdout,),
            daemon=True,
        )
        stderr_thread = threading.Thread(
            target=stderr_capture.drain,
            args=(process.stderr,),
            daemon=True,
        )
        stdout_thread.start()
        stderr_thread.start()

        termination = ProcessTermination.COMPLETED
        deadline = started + launch.timeout_seconds
        while process.poll() is None:
            if cancel_check is not None and cancel_check():
                termination = ProcessTermination.CANCELLED
                job.terminate()
                break
            if time.monotonic() >= deadline:
                termination = ProcessTermination.TIMED_OUT
                job.terminate()
                break
            time.sleep(0.02)

        try:
            process.wait(timeout=5.0)
        except subprocess.TimeoutExpired:
            job.terminate()
            process.wait(timeout=5.0)
        stdout_thread.join(timeout=5.0)
        stderr_thread.join(timeout=5.0)
        job.close()
        finished = time.monotonic()
        return ProcessExecution(
            termination=termination,
            exit_code=process.returncode,
            duration_ms=max(0, round((finished - started) * 1000)),
            stdout=bytes(stdout_capture.buffer),
            stderr=bytes(stderr_capture.buffer),
            stdout_observed_bytes=stdout_capture.observed,
            stderr_observed_bytes=stderr_capture.observed,
            stdout_sha256=stdout_capture.digest.hexdigest(),
            stderr_sha256=stderr_capture.digest.hexdigest(),
            stdout_truncated=stdout_capture.truncated,
            stderr_truncated=stderr_capture.truncated,
        )


class _IoCounters(ctypes.Structure):
    _fields_ = [
        ("ReadOperationCount", ctypes.c_ulonglong),
        ("WriteOperationCount", ctypes.c_ulonglong),
        ("OtherOperationCount", ctypes.c_ulonglong),
        ("ReadTransferCount", ctypes.c_ulonglong),
        ("WriteTransferCount", ctypes.c_ulonglong),
        ("OtherTransferCount", ctypes.c_ulonglong),
    ]


class _BasicLimitInformation(ctypes.Structure):
    _fields_ = [
        ("PerProcessUserTimeLimit", ctypes.c_longlong),
        ("PerJobUserTimeLimit", ctypes.c_longlong),
        ("LimitFlags", ctypes.c_uint32),
        ("MinimumWorkingSetSize", ctypes.c_size_t),
        ("MaximumWorkingSetSize", ctypes.c_size_t),
        ("ActiveProcessLimit", ctypes.c_uint32),
        ("Affinity", ctypes.c_size_t),
        ("PriorityClass", ctypes.c_uint32),
        ("SchedulingClass", ctypes.c_uint32),
    ]


class _ExtendedLimitInformation(ctypes.Structure):
    _fields_ = [
        ("BasicLimitInformation", _BasicLimitInformation),
        ("IoInfo", _IoCounters),
        ("ProcessMemoryLimit", ctypes.c_size_t),
        ("JobMemoryLimit", ctypes.c_size_t),
        ("PeakProcessMemoryUsed", ctypes.c_size_t),
        ("PeakJobMemoryUsed", ctypes.c_size_t),
    ]


class _WindowsJob:
    _LIMIT_PROCESS_TIME = 0x00000002
    _LIMIT_ACTIVE_PROCESS = 0x00000008
    _LIMIT_PROCESS_MEMORY = 0x00000100
    _LIMIT_KILL_ON_JOB_CLOSE = 0x00002000
    _EXTENDED_LIMIT_INFORMATION = 9

    def __init__(
        self,
        process: subprocess.Popen[bytes],
        *,
        cpu_seconds: int,
        memory_bytes: int,
    ) -> None:
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)  # pyright: ignore[reportAttributeAccessIssue]
        self._kernel32 = kernel32
        kernel32.CreateJobObjectW.argtypes = [ctypes.c_void_p, ctypes.c_wchar_p]
        kernel32.CreateJobObjectW.restype = ctypes.c_void_p
        kernel32.SetInformationJobObject.argtypes = [
            ctypes.c_void_p,
            ctypes.c_int,
            ctypes.c_void_p,
            ctypes.c_uint32,
        ]
        kernel32.SetInformationJobObject.restype = ctypes.c_int
        kernel32.AssignProcessToJobObject.argtypes = [
            ctypes.c_void_p,
            ctypes.c_void_p,
        ]
        kernel32.AssignProcessToJobObject.restype = ctypes.c_int
        kernel32.TerminateJobObject.argtypes = [ctypes.c_void_p, ctypes.c_uint32]
        kernel32.TerminateJobObject.restype = ctypes.c_int
        kernel32.CloseHandle.argtypes = [ctypes.c_void_p]
        kernel32.CloseHandle.restype = ctypes.c_int
        self._handle = kernel32.CreateJobObjectW(None, None)
        if not self._handle:
            raise OSError(ctypes.get_last_error(), "CreateJobObjectW failed")

        info = _ExtendedLimitInformation()
        info.BasicLimitInformation.PerProcessUserTimeLimit = cpu_seconds * 10_000_000
        info.BasicLimitInformation.ActiveProcessLimit = 1
        info.BasicLimitInformation.LimitFlags = (
            self._LIMIT_PROCESS_TIME
            | self._LIMIT_ACTIVE_PROCESS
            | self._LIMIT_PROCESS_MEMORY
            | self._LIMIT_KILL_ON_JOB_CLOSE
        )
        info.ProcessMemoryLimit = memory_bytes
        configured = kernel32.SetInformationJobObject(
            self._handle,
            self._EXTENDED_LIMIT_INFORMATION,
            ctypes.byref(info),
            ctypes.sizeof(info),
        )
        if not configured:
            self.close()
            raise OSError(ctypes.get_last_error(), "SetInformationJobObject failed")

        process_handle = ctypes.c_void_p(int(getattr(process, "_handle")))
        if not kernel32.AssignProcessToJobObject(self._handle, process_handle):
            self.close()
            raise OSError(ctypes.get_last_error(), "AssignProcessToJobObject failed")

    def resume(self, process: subprocess.Popen[bytes]) -> None:
        ntdll = ctypes.WinDLL("ntdll")  # pyright: ignore[reportAttributeAccessIssue]
        ntdll.NtResumeProcess.argtypes = [ctypes.c_void_p]
        ntdll.NtResumeProcess.restype = ctypes.c_long
        status = ntdll.NtResumeProcess(
            ctypes.c_void_p(int(getattr(process, "_handle")))
        )
        if status != 0:
            raise OSError(status, "NtResumeProcess failed")

    def terminate(self) -> None:
        if self._handle:
            self._kernel32.TerminateJobObject(self._handle, 1)

    def close(self) -> None:
        if self._handle:
            self._kernel32.CloseHandle(self._handle)
            self._handle = None


@dataclass(frozen=True, slots=True)
class _CompiledCommand:
    command: ControlledToolCommand
    tool_relative_path: str
    tool_argv: tuple[str, ...]
    allowed_write_roots: tuple[Path, ...]
    artifact_roots: tuple[Path, ...]
    required_artifact: Path | None = None
    required_companion_artifact: Path | None = None
    suppressed_subprocess_argv: tuple[tuple[str, ...], ...] = ()


class ControlledToolRunner:
    """Deep Module implementing one audited Controlled Run."""

    def __init__(
        self,
        workspace_parent: str | Path,
        *,
        runtime: PythonRuntimeAttestation | None = None,
        toolchain_verifier: ControlledToolchainVerifier | None = None,
        process_adapter: ControlledProcessAdapter | None = None,
        expected_tree_sha256: str = PPT_MASTER_2_7_0_TOOLCHAIN_TREE_SHA256,
    ) -> None:
        parent = Path(workspace_parent).expanduser().resolve(strict=False)
        if parent == Path(parent.anchor) or _is_reparse(parent):
            raise ValueError("Controlled Tool Runner workspace parent is unsafe")
        self._workspace_parent = parent
        self._runtime = runtime or PythonRuntimeAttestation.capture()
        self._toolchain_verifier = (
            toolchain_verifier or PptMasterInstalledToolchainVerifier()
        )
        self._process_adapter = process_adapter or SubprocessControlledProcessAdapter()
        self._expected_tree_sha256 = _normalize_digest(expected_tree_sha256)
        self._worker_path = Path(__file__).with_name("controlled_worker.py").resolve(
            strict=True
        )

    def run(
        self,
        planning_snapshot: PlanningSnapshot,
        request: ControlledToolRequest,
        *,
        cancel_check: Callable[[], bool] | None = None,
    ) -> ControlledToolResult:
        snapshot = PlanningSnapshot.model_validate(
            planning_snapshot.model_dump(mode="python")
        )
        if snapshot.phase != PlanningPhase.PLAN_CONFIRMED:
            raise ControlledToolError(
                "planning_not_confirmed",
                "Controlled Tool Runner requires a PLAN_CONFIRMED snapshot",
            )
        if cancel_check is not None and cancel_check():
            raise ControlledToolError("cancelled", "Controlled run cancelled before preflight")

        workspace = self._workspace_for(request.workspace_id)
        self._prepare_workspace(workspace)
        audit_dir = workspace / "audit" / request.run_id
        try:
            audit_dir.mkdir(parents=False, exist_ok=False)
        except FileExistsError as error:
            raise ControlledToolError(
                "duplicate_run_id",
                f"Controlled run ID already exists: {request.run_id}",
            ) from error

        installed = self._toolchain_verifier.verify(cancel_check=cancel_check)
        self._verify_toolchain_identity(installed)
        self._runtime.verify()
        _verify_no_reparse_tree(workspace)
        compiled = self._compile(
            request.arguments,
            workspace,
            run_id=request.run_id,
        )
        tool_path = _managed_child(installed.install_path, compiled.tool_relative_path)
        if not tool_path.is_file() or _is_reparse(tool_path):
            raise ControlledToolError(
                "tool_missing",
                f"Attested tool path is unavailable: {compiled.tool_relative_path}",
            )
        self._verify_clean_outputs(compiled)

        planning_sha256 = _model_sha256(snapshot)
        toolchain = ToolchainRunAttestation(
            version=installed.version,
            archive_sha256=installed.archive_sha256,
            tree_sha256=installed.tree_sha256,
            file_count=installed.file_count,
            total_bytes=installed.total_bytes,
        )
        run_temp = workspace / "temp" / request.run_id
        run_temp.mkdir(parents=False, exist_ok=False)
        write_roots = (*compiled.allowed_write_roots, run_temp)
        read_roots = self._read_roots(
            workspace=workspace,
            install_path=installed.install_path,
            audit_dir=audit_dir,
        )

        worker_request_path = audit_dir / "worker-request.json"
        worker_request = {
            "tool_path": str(tool_path),
            "tool_argv": list(compiled.tool_argv),
            "cwd": str(workspace),
            "allowed_read_roots": [str(path) for path in read_roots],
            "allowed_write_roots": [str(path) for path in write_roots],
            "restricted_read_roots": list(self._runtime.dependency_sys_paths),
            "approved_dependency_roots": list(
                self._runtime.approved_read_roots
            ),
            "dependency_sys_paths": list(self._runtime.dependency_sys_paths),
            "suppressed_subprocess_argv": [
                list(argv) for argv in compiled.suppressed_subprocess_argv
            ],
        }
        _write_json_exclusive(worker_request_path, worker_request)
        launch_record = {
            "schema_version": 1,
            "run_id": request.run_id,
            "workspace_id": request.workspace_id,
            "planning_snapshot_sha256": planning_sha256,
            "request": request.model_dump(mode="json"),
            "toolchain": toolchain.model_dump(mode="json"),
            "runtime_sha256": self._runtime.sha256,
            "tool_relative_path": compiled.tool_relative_path,
            "tool_argv": list(compiled.tool_argv),
        }
        _write_json_exclusive(audit_dir / "launch.json", launch_record)

        environment = _controlled_environment(run_temp)
        launch = ProcessLaunch(
            argv=(
                self._runtime.executable,
                "-I",
                "-S",
                "-B",
                str(self._worker_path),
                str(worker_request_path),
            ),
            cwd=workspace,
            environment=environment,
            timeout_seconds=request.limits.timeout_seconds,
            cpu_seconds=request.limits.cpu_seconds,
            memory_bytes=request.limits.memory_bytes,
            max_stdout_bytes=request.limits.max_stdout_bytes,
            max_stderr_bytes=request.limits.max_stderr_bytes,
        )
        started_at = datetime.now(timezone.utc)
        execution = self._process_adapter.execute(
            launch,
            cancel_check=cancel_check,
        )
        finished_at = datetime.now(timezone.utc)

        stdout_path = audit_dir / "stdout.log"
        stderr_path = audit_dir / "stderr.log"
        stdout_path.write_bytes(execution.stdout)
        stderr_path.write_bytes(execution.stderr)
        stdout_attestation = _log_attestation(
            stdout_path,
            workspace,
            observed=execution.stdout_observed_bytes,
            stream_sha256=execution.stdout_sha256,
            truncated=execution.stdout_truncated,
        )
        stderr_attestation = _log_attestation(
            stderr_path,
            workspace,
            observed=execution.stderr_observed_bytes,
            stream_sha256=execution.stderr_sha256,
            truncated=execution.stderr_truncated,
        )

        status, error_code, message = _execution_status(execution)
        artifacts: tuple[ToolArtifactAttestation, ...] = ()
        try:
            _verify_no_reparse_tree(workspace)
            artifacts = _attest_artifacts(
                compiled.artifact_roots,
                workspace=workspace,
                limits=request.limits,
                cancel_check=cancel_check,
            )
            if execution.stdout_truncated or execution.stderr_truncated:
                status = ControlledRunStatus.RESOURCE_REJECTED
                error_code = "log_limit_exceeded"
                message = "Controlled tool output exceeded the configured log limit"
            if status == ControlledRunStatus.SUCCEEDED:
                _validate_required_artifact(compiled, artifacts, workspace)
        except ControlledToolError as error:
            status = ControlledRunStatus.RESOURCE_REJECTED
            error_code = error.code
            message = str(error)

        result = ControlledToolResult(
            run_id=request.run_id,
            workspace_id=request.workspace_id,
            command=compiled.command,
            status=status,
            planning_snapshot_sha256=planning_sha256,
            toolchain=toolchain,
            runtime_sha256=self._runtime.sha256,
            started_at=started_at.isoformat(),
            finished_at=finished_at.isoformat(),
            duration_ms=execution.duration_ms,
            exit_code=execution.exit_code,
            error_code=error_code,
            message=message,
            stdout=stdout_attestation,
            stderr=stderr_attestation,
            artifacts=artifacts,
            audit_dir=str(audit_dir),
        )
        _write_json_exclusive(
            audit_dir / "result.json",
            result.model_dump(mode="json"),
        )
        return result

    def _workspace_for(self, workspace_id: str) -> Path:
        return _managed_child(self._workspace_parent, workspace_id)

    def workspace_path(self, workspace_id: str) -> Path:
        """Return the confined Host workspace for a validated workspace ID.

        This Interface grants the Host Provider access to author project inputs;
        it does not create the workspace or grant third-party write authority.
        """
        return self._workspace_for(workspace_id)

    def _prepare_workspace(self, workspace: Path) -> None:
        self._workspace_parent.mkdir(parents=True, exist_ok=True)
        _verify_path_chain_no_reparse(self._workspace_parent)
        workspace.mkdir(parents=True, exist_ok=True)
        for relative in ("audit", "output", "project", "temp"):
            (workspace / relative).mkdir(exist_ok=True)
        _verify_no_reparse_tree(workspace)

    def _verify_toolchain_identity(self, installed: PptMasterInstalledBundle) -> None:
        if (
            installed.version != PPT_MASTER_2_7_0_CONTRACT.version
            or installed.archive_sha256 != PPT_MASTER_2_7_0_CONTRACT.archive_sha256
            or installed.tree_sha256 != self._expected_tree_sha256
        ):
            raise ControlledToolError(
                "toolchain_identity_mismatch",
                "Verified toolchain identity does not match the Runner contract",
            )

    def _compile(
        self,
        arguments: ControlledToolArguments,
        workspace: Path,
        *,
        run_id: str,
    ) -> _CompiledCommand:
        scripts = "skills/ppt-master/scripts"
        if isinstance(arguments, ProjectInitArguments):
            project_parent = workspace / "project"
            return _CompiledCommand(
                command=arguments.kind,
                tool_relative_path=f"{scripts}/project_manager.py",
                tool_argv=(
                    "init",
                    arguments.project_name,
                    "--format",
                    arguments.canvas_format,
                    "--dir",
                    str(project_parent),
                ),
                allowed_write_roots=(project_parent,),
                artifact_roots=(project_parent,),
            )

        project = _managed_child(workspace, arguments.project_dir)
        if not project.is_dir() or _is_reparse(project):
            raise ControlledToolError(
                "project_missing",
                f"Controlled project directory does not exist: {arguments.project_dir}",
            )
        if isinstance(arguments, SvgQualityArguments):
            quality_report = (
                project
                / "validation"
                / f"{run_id}.quality.json"
            )
            return _CompiledCommand(
                command=arguments.kind,
                tool_relative_path=f"{scripts}/svg_quality_checker.py",
                tool_argv=(
                    str(project),
                    "--format",
                    arguments.canvas_format,
                    "--stage",
                    arguments.stage.value,
                    "--json-output",
                    str(quality_report),
                ),
                allowed_write_roots=(quality_report,),
                artifact_roots=(quality_report,),
                required_artifact=quality_report,
            )
        if isinstance(arguments, FinalizeSvgArguments):
            svg_final = project / "svg_final"
            return _CompiledCommand(
                command=arguments.kind,
                tool_relative_path=f"{scripts}/finalize_svg.py",
                tool_argv=(str(project), "--quiet"),
                allowed_write_roots=(svg_final,),
                artifact_roots=(svg_final,),
            )
        if isinstance(arguments, SvgToPptxArguments):
            output = workspace / "output" / arguments.output_name
            postflight = (
                project
                / "validation"
                / f"{Path(arguments.output_name).stem}.report.json"
            )
            return _CompiledCommand(
                command=arguments.kind,
                tool_relative_path=f"{scripts}/svg_to_pptx.py",
                tool_argv=(
                    str(project),
                    "-o",
                    str(output),
                    "-f",
                    arguments.canvas_format,
                    "-q",
                    "--pptx-structure",
                    arguments.structure.value,
                ),
                allowed_write_roots=(workspace / "output", postflight),
                artifact_roots=(workspace / "output", postflight),
                required_artifact=output,
                required_companion_artifact=postflight,
                suppressed_subprocess_argv=((
                    "icacls",
                    str(output),
                    "/grant",
                    "*S-1-5-32-545:R",
                ),),
            )
        raise ControlledToolError(
            "command_not_allowlisted",
            f"Unsupported controlled operation: {type(arguments).__name__}",
        )

    @staticmethod
    def _verify_clean_outputs(compiled: _CompiledCommand) -> None:
        if compiled.command == ControlledToolCommand.PROJECT_INIT:
            if any(compiled.artifact_roots[0].iterdir()):
                raise ControlledToolError(
                    "dirty_output",
                    "Project initialization requires an empty project root",
                )
            return
        if compiled.command == ControlledToolCommand.FINALIZE_SVG:
            target = compiled.artifact_roots[0]
            if not target.is_dir() or any(target.iterdir()):
                raise ControlledToolError(
                    "dirty_output",
                    "SVG finalization requires an existing empty svg_final directory",
                )
            return
        if compiled.command == ControlledToolCommand.SVG_TO_PPTX:
            target = compiled.artifact_roots[0]
            if any(target.iterdir()):
                raise ControlledToolError(
                    "dirty_output",
                    "PPTX export requires an empty controlled output directory",
                )

    def _read_roots(
        self,
        *,
        workspace: Path,
        install_path: Path,
        audit_dir: Path,
    ) -> tuple[Path, ...]:
        roots = {
            workspace.resolve(),
            install_path.resolve(),
            audit_dir.resolve(),
            self._worker_path.parent.resolve(),
            Path(self._runtime.executable).resolve(),
            *(
                Path(path).resolve()
                for path in self._runtime.approved_read_roots
            ),
        }
        for key in ("stdlib", "platstdlib"):
            value = sysconfig.get_paths().get(key)
            if value:
                roots.add(Path(value).resolve())
        executable_parent = Path(self._runtime.executable).resolve().parent
        dlls = executable_parent / "DLLs"
        if dlls.is_dir():
            roots.add(dlls.resolve())
        windows_root = os.environ.get("WINDIR") or os.environ.get("SYSTEMROOT")
        if windows_root:
            fonts = Path(windows_root) / "Fonts"
            if fonts.is_dir():
                roots.add(fonts.resolve())
        return tuple(sorted(roots, key=lambda path: str(path).casefold()))


def _capture_distribution(name: str) -> RuntimeDependencyAttestation:
    try:
        distribution = importlib.metadata.distribution(name)
    except importlib.metadata.PackageNotFoundError as error:
        raise ControlledToolError(
            "runtime_dependency_missing",
            f"Required Python distribution is not installed: {name}",
        ) from error
    base = Path(str(distribution.locate_file(""))).resolve(strict=True)
    digest = hashlib.sha256()
    file_count = 0
    read_roots: set[str] = set()
    for entry in sorted(distribution.files or (), key=lambda item: str(item).casefold()):
        path = Path(str(distribution.locate_file(str(entry)))).resolve(strict=False)
        if not path.is_file():
            continue
        if _is_reparse(path):
            raise ControlledToolError(
                "runtime_dependency_unsafe",
                f"Runtime dependency contains a reparse file: {name}",
            )
        try:
            relative = path.relative_to(base)
        except ValueError:
            # Wheel RECORD files may include console scripts outside the
            # import root (for example ``../../Scripts/vba_extract.py``).
            # They are not importable package content, so exclude them from
            # both the digest and the worker's approved read roots.
            continue
        file_sha256 = _sha256_file(path)
        size = path.stat().st_size
        digest.update(f"{relative.as_posix()}\0{size}\0{file_sha256}\n".encode())
        file_count += 1
        first = relative.parts[0]
        read_roots.add(str((base / first).resolve()))
    if file_count == 0:
        raise ControlledToolError(
            "runtime_dependency_invalid",
            f"Runtime dependency has no attested files: {name}",
        )
    return RuntimeDependencyAttestation(
        name=name,
        version=distribution.version,
        content_sha256=digest.hexdigest(),
        file_count=file_count,
        import_root=str(base),
        read_roots=tuple(sorted(read_roots, key=str.casefold)),
    )


def _controlled_environment(run_temp: Path) -> dict[str, str]:
    environment: dict[str, str] = {
        "PYTHONUTF8": "1",
        "PYTHONDONTWRITEBYTECODE": "1",
        "PYTHONUNBUFFERED": "1",
        "PYTHONIOENCODING": "utf-8:replace",
        "TEMP": str(run_temp),
        "TMP": str(run_temp),
    }
    for name in ("SYSTEMROOT", "WINDIR", "COMSPEC", "PATHEXT", "USERPROFILE"):
        value = os.environ.get(name)
        if value:
            environment[name] = value
    return environment


def _execution_status(
    execution: ProcessExecution,
) -> tuple[ControlledRunStatus, str, str]:
    if execution.termination == ProcessTermination.CANCELLED:
        return ControlledRunStatus.CANCELLED, "cancelled", "Controlled run cancelled"
    if execution.termination == ProcessTermination.TIMED_OUT:
        return ControlledRunStatus.TIMED_OUT, "timeout", "Controlled run timed out"
    if execution.exit_code != 0:
        return (
            ControlledRunStatus.FAILED,
            "tool_failed",
            f"Allowlisted tool exited with status {execution.exit_code}",
        )
    return ControlledRunStatus.SUCCEEDED, "", ""


def _attest_artifacts(
    roots: tuple[Path, ...],
    *,
    workspace: Path,
    limits: ControlledToolLimits,
    cancel_check: Callable[[], bool] | None,
) -> tuple[ToolArtifactAttestation, ...]:
    artifacts: list[ToolArtifactAttestation] = []
    total_bytes = 0
    pending = list(roots)
    while pending:
        root = pending.pop()
        if not root.exists():
            continue
        if _is_reparse(root):
            raise ControlledToolError("artifact_unsafe", "Artifact root is a reparse point")
        if root.is_file():
            entries = (root,)
        else:
            entries = tuple(Path(entry.path) for entry in os.scandir(root))
        for path in entries:
            if cancel_check is not None and cancel_check():
                raise ControlledToolError("cancelled", "Cancelled while attesting output")
            if _is_reparse(path):
                raise ControlledToolError(
                    "artifact_unsafe",
                    f"Artifact is a symlink or reparse point: {path.name}",
                )
            if path.is_dir():
                pending.append(path)
                continue
            if not path.is_file():
                raise ControlledToolError(
                    "artifact_unsafe",
                    f"Artifact is not a regular file: {path.name}",
                )
            size = path.stat().st_size
            if size > limits.max_artifact_bytes:
                raise ControlledToolError(
                    "artifact_file_limit",
                    f"Artifact exceeds per-file byte limit: {path.name}",
                )
            total_bytes += size
            if total_bytes > limits.max_total_artifact_bytes:
                raise ControlledToolError(
                    "artifact_total_limit",
                    "Artifacts exceed total byte limit",
                )
            artifacts.append(
                ToolArtifactAttestation(
                    relative_path=path.relative_to(workspace).as_posix(),
                    size_bytes=size,
                    sha256=_sha256_file(path),
                )
            )
            if len(artifacts) > limits.max_artifact_count:
                raise ControlledToolError(
                    "artifact_count_limit",
                    "Artifacts exceed file-count limit",
                )
    return tuple(sorted(artifacts, key=lambda item: item.relative_path))


def _validate_required_artifact(
    compiled: _CompiledCommand,
    artifacts: tuple[ToolArtifactAttestation, ...],
    workspace: Path,
) -> None:
    if compiled.required_artifact is None:
        return
    required = compiled.required_artifact.relative_to(workspace).as_posix()
    required_paths = {required}
    if compiled.required_companion_artifact is not None:
        required_paths.add(
            compiled.required_companion_artifact.relative_to(workspace).as_posix()
        )
    actual_paths = {item.relative_path for item in artifacts}
    if actual_paths != required_paths or len(artifacts) != len(required_paths):
        raise ControlledToolError(
            "artifact_contract_invalid",
            "PPTX export must produce exactly the requested output and "
            "postflight artifacts",
        )


def _log_attestation(
    path: Path,
    workspace: Path,
    *,
    observed: int,
    stream_sha256: str,
    truncated: bool,
) -> OutputLogAttestation:
    return OutputLogAttestation(
        relative_path=path.relative_to(workspace).as_posix(),
        observed_bytes=observed,
        captured_bytes=path.stat().st_size,
        stream_sha256=stream_sha256,
        log_sha256=_sha256_file(path),
        truncated=truncated,
    )


def _empty_process_execution(termination: ProcessTermination) -> ProcessExecution:
    empty_sha = hashlib.sha256(b"").hexdigest()
    return ProcessExecution(
        termination=termination,
        exit_code=None,
        duration_ms=0,
        stdout=b"",
        stderr=b"",
        stdout_observed_bytes=0,
        stderr_observed_bytes=0,
        stdout_sha256=empty_sha,
        stderr_sha256=empty_sha,
        stdout_truncated=False,
        stderr_truncated=False,
    )


def _model_sha256(model: BaseModel) -> str:
    encoded = json.dumps(
        model.model_dump(mode="json", exclude_none=True),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        while chunk := source.read(_HASH_CHUNK_BYTES):
            digest.update(chunk)
    return digest.hexdigest()


def _write_json_exclusive(path: Path, value: object) -> None:
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    with path.open("xb") as output:
        output.write(encoded)
        output.flush()
        os.fsync(output.fileno())


def _managed_child(root: Path, relative: str) -> Path:
    normalized = _validated_relative_path(relative, field="managed path")
    target = root.joinpath(*PurePosixPath(normalized).parts).resolve(strict=False)
    try:
        target.relative_to(root.resolve(strict=False))
    except ValueError as error:
        raise ControlledToolError(
            "path_escape",
            f"Controlled path escapes its managed root: {relative}",
        ) from error
    return target


def _verify_path_chain_no_reparse(path: Path) -> None:
    current = path
    while True:
        if current.exists() and _is_reparse(current):
            raise ControlledToolError(
                "workspace_unsafe",
                f"Controlled workspace path is a reparse point: {current}",
            )
        if current == current.parent:
            return
        current = current.parent


def _verify_no_reparse_tree(root: Path) -> None:
    _verify_path_chain_no_reparse(root)
    pending = [root]
    while pending:
        directory = pending.pop()
        with os.scandir(directory) as entries:
            for entry in entries:
                path = Path(entry.path)
                if _is_reparse(path):
                    raise ControlledToolError(
                        "workspace_unsafe",
                        f"Controlled workspace contains a reparse point: {path}",
                    )
                if entry.is_dir(follow_symlinks=False):
                    pending.append(path)
                elif not entry.is_file(follow_symlinks=False):
                    raise ControlledToolError(
                        "workspace_unsafe",
                        f"Controlled workspace contains a special file: {path}",
                    )


def _is_reparse(path: Path) -> bool:
    try:
        info = path.lstat()
    except OSError:
        return False
    if stat.S_ISLNK(info.st_mode):
        return True
    return bool(getattr(info, "st_file_attributes", 0) & 0x400)


def _normalize_digest(value: str) -> str:
    normalized = value.lower()
    if len(normalized) != 64 or any(
        char not in "0123456789abcdef" for char in normalized
    ):
        raise ValueError("expected_tree_sha256 must be a SHA-256 digest")
    return normalized
