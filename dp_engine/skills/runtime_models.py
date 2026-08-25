"""Immutable domain models for SkillRuntime.

All models are frozen dataclasses — they carry data only.
No QWidget, QObject, thread, process, or callable references are allowed.

Limit constants are centralized here — no magic numbers in service code.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


# ── Current protocol version ──

RUNTIME_PROTOCOL_VERSION = 1
RUN_ENTRYPOINT_KEY = "run"  # Batch 3.1.1A
GENERATE_REPORT_OPERATION = "generate_report"  # Batch 3.5.2

# ── Allowed operations (Batch 3.1.1A: healthcheck + run) ──

ALLOWED_OPERATIONS: frozenset[str] = frozenset({
    "healthcheck",
    "run",
    GENERATE_REPORT_OPERATION,
})

# ── Default capabilities for healthcheck ──

DEFAULT_HEALTHCHECK_CAPABILITIES: tuple[str, ...] = (
    "read_skill_files",
    "read_runtime_workspace",
    "write_runtime_workspace",
)

# All capabilities that are NEVER allowed for healthcheck
FORBIDDEN_HEALTHCHECK_CAPABILITIES: frozenset[str] = frozenset({
    "network",
    "subprocess",
    "shell",
    "project_read",
    "project_write",
    "installed_write",
    "environment_secrets",
    "ctypes",
    "dynamic_native_library",
})

# ── Size limits ──

MAX_STDOUT_BYTES = 1 * 1024 * 1024        # 1 MB
MAX_STDERR_BYTES = 1 * 1024 * 1024        # 1 MB
MAX_RESULT_JSON_BYTES = 1 * 1024 * 1024   # 1 MB
MAX_REQUEST_BYTES = 1 * 1024 * 1024       # 1 MB — Batch 3.1.1A
MAX_OUTPUT_TOTAL_BYTES = 10 * 1024 * 1024  # 10 MB
MAX_SINGLE_FILE_BYTES = 5 * 1024 * 1024   # 5 MB
MAX_FILE_COUNT = 100                       # max files in output

# ── JSON input limits (Batch 3.1.1A) ──

MAX_JSON_NESTING_DEPTH = 32
MAX_JSON_CONTAINER_ITEMS = 10_000
MAX_JSON_STRING_BYTES = 1 * 1024 * 1024   # 1 MB
MAX_MEDIA_TYPE_CHARS = 128                 # accommodates standard OOXML MIME types

# ── Timeouts (production defaults — tests may override) ──

DEFAULT_HEALTHCHECK_TIMEOUT_SECONDS = 10.0

# Cancel / cleanup grace periods (seconds)
# These are conservative production defaults; tests SHOULD inject
# shorter values via SkillRuntimeService(cancel_grace_ms=..., ...).
WORKER_GRACE_PERIOD_SECONDS = 2.0          # after cancel marker
WORKER_TERMINATE_WAIT_SECONDS = 3.0         # after terminate
WORKER_KILL_WAIT_SECONDS = 2.0              # after kill

# Fast test defaults (used when SkillRuntimeService is created with
# cancel_grace_ms / terminate_grace_ms / kill_grace_ms)
_TEST_CANCEL_GRACE_MS = 200
_TEST_TERMINATE_GRACE_MS = 200
_TEST_KILL_GRACE_MS = 200

# ── Healthcheck result limits ──

MAX_HEALTHCHECK_MESSAGE_LENGTH = 1000
MAX_HEALTHCHECK_DETAILS_DEPTH = 4
MAX_HEALTHCHECK_DETAILS_KEYS = 50
MAX_HEALTHCHECK_DETAILS_TOTAL_BYTES = 100 * 1024  # 100 KB

# ── Valid healthcheck status values for responses ──

VALID_HEALTHCHECK_STATUSES: frozenset[str] = frozenset({
    "healthy",
    "unhealthy",
    "not_supported",
    "dependency_missing",
    "permission_denied",
    "timeout",
    "cancelled",
    "crashed",
    "protocol_error",
})

# ── Valid run status values for responses (Batch 3.1.1A) ──

VALID_RUN_STATUSES: frozenset[str] = frozenset({
    "succeeded",
    "failed",
    "permission_denied",
    "timeout",
    "cancelled",
    "crashed",
    "protocol_error",
})


# ── Runtime request ──


@dataclass(frozen=True)
class SkillRuntimeRequest:
    """Request to execute a skill operation in an isolated subprocess.

    Supports 'healthcheck' and 'run' operations (Batch 3.1.1A).
    """

    protocol_version: int
    task_id: str
    operation: str
    skill_id: str
    version: str
    installed_path: str
    entrypoint: str          # "module/path.py:function_name"
    workspace_path: str
    timeout_seconds: float
    capabilities: tuple[str, ...]
    environment: dict[str, str]
    params: dict[str, object] | None = None  # Batch 3.1.1A

    def to_dict(self) -> dict[str, object]:
        d: dict[str, object] = {
            "protocol_version": self.protocol_version,
            "task_id": self.task_id,
            "operation": self.operation,
            "skill_id": self.skill_id,
            "version": self.version,
            "installed_path": self.installed_path,
            "entrypoint": self.entrypoint,
            "workspace_path": self.workspace_path,
            "timeout_seconds": self.timeout_seconds,
            "capabilities": list(self.capabilities),
            "environment": dict(self.environment),
        }
        if self.params is not None:
            d["params"] = self.params
        return d

    @classmethod
    def from_dict(cls, d: dict[str, object]) -> SkillRuntimeRequest:
        raw_caps = d.get("capabilities", [])
        if isinstance(raw_caps, list):
            caps = tuple(str(c) for c in raw_caps)
        else:
            caps = ()
        raw_env = d.get("environment", {})
        if isinstance(raw_env, dict):
            env = {str(k): str(v) for k, v in raw_env.items()}
        else:
            env = {}
        raw_params = d.get("params")
        params: dict[str, object] | None = None
        if isinstance(raw_params, dict):
            params = {str(k): v for k, v in raw_params.items()}
        return cls(
            protocol_version=int(str(d["protocol_version"])),
            task_id=str(d["task_id"]),
            operation=str(d["operation"]),
            skill_id=str(d["skill_id"]),
            version=str(d["version"]),
            installed_path=str(d["installed_path"]),
            entrypoint=str(d["entrypoint"]),
            workspace_path=str(d["workspace_path"]),
            timeout_seconds=float(str(d["timeout_seconds"])),
            capabilities=caps,
            environment=env,
            params=params,
        )


# ── Artifact constants (Batch 3.2.1A) ──

MAX_ARTIFACTS_PER_RUN = 20
MAX_ARTIFACT_FILE_BYTES = 50 * 1024 * 1024     # 50 MB
MAX_ARTIFACTS_TOTAL_BYTES = 200 * 1024 * 1024  # 200 MB
MAX_DECLARE_MANIFEST_BYTES = 32768             # 32 KB — Batch 3.2.1A-R
MAX_DISPLAY_NAME_CHARS = 128
MAX_DECLARED_PATH_CHARS = 255
MAX_METADATA_JSON_BYTES = 4096                  # 4 KB per artifact
MAX_METADATA_AGGREGATE_BYTES = 24 * 1024        # 24 KB total — Batch 3.2.1A-R
ARTIFACT_SCHEMA_VERSION = 1

VALID_ARTIFACT_KINDS: frozenset[str] = frozenset({
    "chart", "table", "document", "data", "other",
})

ALLOWED_ARTIFACT_EXTENSIONS: frozenset[str] = frozenset({
    ".pdf", ".docx", ".pptx", ".xlsx",
    ".csv", ".json", ".txt",
    ".png", ".jpg", ".jpeg",
    ".zip",
})
# NOTE: .svg intentionally excluded (P2 — security risk without sanitizer)


# ── Runtime artifact ──


@dataclass(frozen=True)
class RuntimeArtifact:
    """A file produced by the skill operation in the output directory.

    Batch 3.2.1A extends this from 3 fields to 14 fields.
    Old 3-field format is still valid (backward compat via from_dict defaults).
    """

    relative_path: str       # relative to workspace/output/ (kept for backward compat)
    size_bytes: int
    sha256: str | None = None

    # ── Batch 3.2.1A extended fields ──
    artifact_schema_version: int = 0
    artifact_id: str = ""
    display_name: str = ""
    storage_relpath: str = ""       # host-generated, relative to artifact_root
    media_type: str = ""
    kind: str = ""
    created_at: str = ""
    skill_id: str = ""
    version: str = ""
    task_id: str = ""
    metadata: dict[str, object] = field(default_factory=dict)

    def to_extended_dict(self) -> dict[str, object]:
        """Full 14-field serialization (Batch 3.2.1A)."""
        d: dict[str, object] = {
            "artifact_schema_version": self.artifact_schema_version,
            "artifact_id": self.artifact_id,
            "display_name": self.display_name,
            "relative_path": self.relative_path,
            "storage_relpath": self.storage_relpath,
            "media_type": self.media_type,
            "kind": self.kind,
            "size_bytes": self.size_bytes,
            "sha256": self.sha256,
            "created_at": self.created_at,
            "skill_id": self.skill_id,
            "version": self.version,
            "task_id": self.task_id,
            "metadata": dict(self.metadata),
        }
        return d

    @classmethod
    def from_extended_dict(cls, d: dict[str, object]) -> RuntimeArtifact:
        """Parse from full 14-field dict with safe defaults for missing fields."""
        meta_raw = d.get("metadata", {})
        metadata: dict[str, object] = (
            dict(meta_raw) if isinstance(meta_raw, dict) else {}
        )
        return cls(
            relative_path=str(d.get("relative_path", "")),
            size_bytes=int(str(d.get("size_bytes", 0))),
            sha256=(
                str(d["sha256"]) if d.get("sha256") is not None else None
            ),
            artifact_schema_version=int(str(d.get("artifact_schema_version", 0))),
            artifact_id=str(d.get("artifact_id", "")),
            display_name=str(d.get("display_name", "")),
            storage_relpath=str(d.get("storage_relpath", "")),
            media_type=str(d.get("media_type", "")),
            kind=str(d.get("kind", "")),
            created_at=str(d.get("created_at", "")),
            skill_id=str(d.get("skill_id", "")),
            version=str(d.get("version", "")),
            task_id=str(d.get("task_id", "")),
            metadata=metadata,
        )


# ── Artifact declaration (Worker internal wire type, Batch 3.2.1A) ──


@dataclass(frozen=True)
class ArtifactDeclaration:
    """Worker internal declaration record — NOT a final RuntimeArtifact.

    This is the intermediate wire format written by the Worker in result.json
    under the "artifact_declarations" key.  It must NOT contain host-generated
    fields (artifact_id, storage_relpath, task_id, skill_id, version, created_at).
    """

    declared_path: str
    display_name: str
    media_type_hint: str | None
    kind: str
    metadata: dict[str, object] = field(default_factory=dict)
    observed_size_bytes: int = 0
    observed_sha256: str = ""
    observed_device: int = 0
    observed_inode: int = 0
    observed_mtime_ns: int = 0

    def to_wire_dict(self) -> dict[str, object]:
        """Serialize to wire format for result.json."""
        return {
            "declared_path": self.declared_path,
            "display_name": self.display_name,
            "media_type_hint": self.media_type_hint,
            "kind": self.kind,
            "metadata": dict(self.metadata),
            "observed_size_bytes": self.observed_size_bytes,
            "observed_sha256": self.observed_sha256,
            "observed_device": self.observed_device,
            "observed_inode": self.observed_inode,
            "observed_mtime_ns": self.observed_mtime_ns,
        }

    @classmethod
    def from_wire_dict(cls, d: dict[str, object]) -> ArtifactDeclaration:
        """Parse from wire format with strict validation.

        Raises ValueError on missing fields, wrong types, or forbidden fields.
        """
        # ── Forbidden host fields ──
        _FORBIDDEN = frozenset({
            "artifact_id", "storage_relpath", "published_path",
            "skill_id", "version", "task_id", "created_at",
        })
        for key in _FORBIDDEN:
            if key in d:
                raise ValueError(
                    f"ArtifactDeclaration must not contain host field: '{key}'"
                )

        # ── Required fields ──
        declared_path = d.get("declared_path")
        if not isinstance(declared_path, str) or not declared_path:
            raise ValueError("declared_path must be a non-empty string")
        if len(declared_path) > MAX_DECLARED_PATH_CHARS:
            raise ValueError(
                f"declared_path exceeds {MAX_DECLARED_PATH_CHARS} chars: "
                f"{len(declared_path)}"
            )

        # Path syntax validation
        _validate_declared_path_syntax(declared_path)

        display_name = d.get("display_name")
        if not isinstance(display_name, str) or not display_name.strip():
            raise ValueError("display_name must be a non-empty string")

        media_type_hint = d.get("media_type_hint")
        if media_type_hint is not None:
            if not isinstance(media_type_hint, str) or not media_type_hint:
                raise ValueError(
                    "media_type_hint must be a non-empty string or None"
                )
            if len(media_type_hint) > MAX_MEDIA_TYPE_CHARS:
                raise ValueError(
                    f"media_type_hint exceeds {MAX_MEDIA_TYPE_CHARS} chars"
                )

        kind = d.get("kind")
        if not isinstance(kind, str) or kind not in VALID_ARTIFACT_KINDS:
            raise ValueError(
                f"kind must be one of {sorted(VALID_ARTIFACT_KINDS)}, got {kind!r}"
            )

        meta_raw = d.get("metadata", {})
        if not isinstance(meta_raw, dict):
            raise ValueError("metadata must be a dict")
        metadata: dict[str, object] = dict(meta_raw)

        observed_size_bytes = d.get("observed_size_bytes")
        if not isinstance(observed_size_bytes, (int, float)):
            raise ValueError("observed_size_bytes must be a number")
        size_val = int(observed_size_bytes)
        if size_val < 0:
            raise ValueError(f"observed_size_bytes must be >= 0, got {size_val}")

        observed_sha256 = d.get("observed_sha256")
        if not isinstance(observed_sha256, str):
            raise ValueError("observed_sha256 must be a string")
        if not _SHA256_RE.match(observed_sha256):
            raise ValueError(
                f"observed_sha256 must be 64 lowercase hex chars, got: "
                f"{observed_sha256[:20]}..."
            )

        observed_device_raw = d.get("observed_device", 0)
        observed_device: int = (
            int(observed_device_raw)
            if isinstance(observed_device_raw, (int, float)) else 0
        )
        observed_inode_raw = d.get("observed_inode", 0)
        observed_inode: int = (
            int(observed_inode_raw)
            if isinstance(observed_inode_raw, (int, float)) else 0
        )
        observed_mtime_ns_raw = d.get("observed_mtime_ns", 0)
        observed_mtime_ns: int = (
            int(observed_mtime_ns_raw)
            if isinstance(observed_mtime_ns_raw, (int, float)) else 0
        )

        return cls(
            declared_path=declared_path,
            display_name=display_name,
            media_type_hint=media_type_hint,
            kind=kind,
            metadata=metadata,
            observed_size_bytes=size_val,
            observed_sha256=observed_sha256,
            observed_device=observed_device,
            observed_inode=observed_inode,
            observed_mtime_ns=observed_mtime_ns,
        )


# ── Reusable validation helpers ──

_SHA256_RE = __import__("re").compile(r"^[a-f0-9]{64}$")
_PATH_COMPONENT_RE = __import__("re").compile(r"^[a-zA-Z0-9._-]+$")


def _validate_declared_path_syntax(path: str) -> None:
    """Validate that a declared_path is a safe POSIX relative path.

    Raises ValueError on any violation.
    """
    if not path or not path.strip():
        raise ValueError("declared_path must not be empty")
    if path.startswith("/") or path.startswith("\\"):
        raise ValueError(f"declared_path must not be absolute: {path!r}")
    if "\\" in path:
        raise ValueError(
            f"declared_path must use POSIX separators, not backslashes: {path!r}"
        )
    # Check for drive letter pattern (C:)
    if len(path) >= 2 and path[1] == ":":
        raise ValueError(f"declared_path must not contain drive letter: {path!r}")
    # Check for UNC pattern
    if path.startswith("//") or path.startswith("\\\\"):
        raise ValueError(f"declared_path must not be UNC path: {path!r}")

    parts = path.split("/")
    for part in parts:
        if part == "..":
            raise ValueError(f"declared_path must not contain '..': {path!r}")
        if part in ("", "."):
            continue
        if not _PATH_COMPONENT_RE.match(part):
            # Allow common safe characters for filenames
            if any(c in part for c in '\x00\x01\x02\x03\x04\x05\x06\x07\x08\x09\x0a\x0b\x0c\x0d\x0e\x0f'):
                raise ValueError(f"declared_path contains control characters: {path!r}")


# ── Runtime error info ──


@dataclass(frozen=True)
class RuntimeErrorInfo:
    """Structured error information for a failed operation."""

    error_type: str          # exception class name or error code
    message: str
    traceback: str | None = None
    detail: dict[str, object] = field(default_factory=dict)


# ── Runtime response ──


@dataclass(frozen=True)
class SkillRuntimeResponse:
    """Response from a skill operation executed in an isolated subprocess.

    Supports 'healthcheck' and 'run' operations (Batch 3.1.1A).
    """

    protocol_version: int
    task_id: str
    operation: str
    success: bool
    status: str              # operation-specific status value
    message: str
    started_at: str          # ISO 8601 UTC
    finished_at: str         # ISO 8601 UTC
    duration_ms: int
    health: dict[str, object] | None = None
    result: dict[str, object] | None = None       # Batch 3.1.1A (run only)
    artifacts: tuple[RuntimeArtifact, ...] = ()
    warnings: tuple[str, ...] = ()
    error: RuntimeErrorInfo | None = None

    def to_dict(self) -> dict[str, object]:
        d: dict[str, object] = {
            "protocol_version": self.protocol_version,
            "task_id": self.task_id,
            "operation": self.operation,
            "success": self.success,
            "status": self.status,
            "message": self.message,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "duration_ms": self.duration_ms,
        }
        if self.health is not None:
            d["health"] = self.health
        if self.result is not None:
            d["result"] = self.result
        if self.artifacts:
            d["artifacts"] = [a.to_extended_dict() for a in self.artifacts]
        if self.warnings:
            d["warnings"] = list(self.warnings)
        if self.error is not None:
            d["error"] = {
                "error_type": self.error.error_type,
                "message": self.error.message,
                "traceback": self.error.traceback,
                "detail": self.error.detail,
            }
        return d

    @classmethod
    def from_dict(cls, d: dict[str, object]) -> SkillRuntimeResponse:
        health_raw = d.get("health")
        health = (
            dict(health_raw) if isinstance(health_raw, dict)
            else None
        )
        result_raw = d.get("result")
        result: dict[str, object] | None = None
        if isinstance(result_raw, dict):
            result = {str(k): v for k, v in result_raw.items()}
        artifacts_raw = d.get("artifacts", [])
        artifacts: tuple[RuntimeArtifact, ...] = ()
        if isinstance(artifacts_raw, list):
            arts: list[RuntimeArtifact] = []
            for a in artifacts_raw:
                if isinstance(a, dict):
                    arts.append(RuntimeArtifact.from_extended_dict(a))
            artifacts = tuple(arts)
        warnings_raw = d.get("warnings", [])
        warnings: tuple[str, ...] = (
            tuple(str(w) for w in warnings_raw)
            if isinstance(warnings_raw, list) else ()
        )
        error_raw = d.get("error")
        error: RuntimeErrorInfo | None = None
        if isinstance(error_raw, dict):
            error = RuntimeErrorInfo(
                error_type=str(error_raw.get("error_type", "")),
                message=str(error_raw.get("message", "")),
                traceback=(
                    str(error_raw["traceback"])
                    if error_raw.get("traceback") is not None
                    else None
                ),
                detail=(
                    dict(error_raw["detail"])
                    if isinstance(error_raw.get("detail"), dict)
                    else {}
                ),
            )
        return cls(
            protocol_version=int(str(d["protocol_version"])),
            task_id=str(d["task_id"]),
            operation=str(d["operation"]),
            success=bool(d["success"]),
            status=str(d["status"]),
            message=str(d["message"]),
            started_at=str(d["started_at"]),
            finished_at=str(d["finished_at"]),
            duration_ms=int(str(d["duration_ms"])),
            health=health,
            result=result,
            artifacts=artifacts,
            warnings=warnings,
            error=error,
        )


# ── Healthcheck context (passed to skill healthcheck function) ──


@dataclass(frozen=True)
class HealthcheckContext:
    """Context provided to a skill's healthcheck function.

    This is a frozen, JSON-serializable object.  The skill sees only
    this context — no filesystem handles, no QObjects, no registry refs.
    """

    skill_id: str
    version: str
    installed_path: str
    workspace_path: str
    capabilities: tuple[str, ...]

    def to_dict(self) -> dict[str, object]:
        return {
            "skill_id": self.skill_id,
            "version": self.version,
            "installed_path": self.installed_path,
            "workspace_path": self.workspace_path,
            "capabilities": list(self.capabilities),
        }


# ── Healthcheck result (returned by skill healthcheck function) ──


@dataclass(frozen=True)
class HealthcheckResult:
    """Result returned by a skill's healthcheck function.

    The skill must return a dict that validates to this structure.
    """

    healthy: bool
    message: str = ""
    details: dict[str, object] = field(default_factory=dict)

    def to_dict(self) -> dict[str, object]:
        return {
            "healthy": self.healthy,
            "message": self.message,
            "details": self.details,
        }


# ── Dependency check models ──


@dataclass(frozen=True)
class DependencyCheckItem:
    """Result of checking a single dependency."""

    name: str
    required_specifier: str
    installed_version: str | None
    satisfied: bool
    reason: str = ""


@dataclass(frozen=True)
class SkillDependencyReport:
    """Aggregate result of checking all declared dependencies."""

    skill_id: str
    version: str
    items: tuple[DependencyCheckItem, ...]
    all_satisfied: bool

    @property
    def missing(self) -> tuple[DependencyCheckItem, ...]:
        return tuple(item for item in self.items if not item.satisfied)

    @property
    def summary(self) -> str:
        if self.all_satisfied:
            return "All dependencies satisfied"
        missing = self.missing
        return (
            f"{len(missing)}/{len(self.items)} dependencies not satisfied: "
            + ", ".join(
                f"{m.name} ({m.reason})" for m in missing
            )
        )
