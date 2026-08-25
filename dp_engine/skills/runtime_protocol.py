"""Protocol validation and atomic serialization for SkillRuntime.

Validates requests before they reach the subprocess and responses
after the subprocess returns.  All I/O is atomic (tmp + fsync + replace).
"""

from __future__ import annotations

import json
import logging
import os
import re
import tempfile
from pathlib import Path

from dp_engine.skills.models import parse_entrypoint_ref
from dp_engine.skills.runtime_errors import RuntimeProtocolError
from dp_engine.skills.runtime_models import (
    ALLOWED_OPERATIONS,
    ARTIFACT_SCHEMA_VERSION,
    MAX_DECLARE_MANIFEST_BYTES,
    MAX_REQUEST_BYTES,
    MAX_RESULT_JSON_BYTES,
    MAX_JSON_NESTING_DEPTH,
    MAX_JSON_CONTAINER_ITEMS,
    MAX_JSON_STRING_BYTES,
    RUNTIME_PROTOCOL_VERSION,
    VALID_ARTIFACT_KINDS,
    VALID_HEALTHCHECK_STATUSES,
    VALID_RUN_STATUSES,
    GENERATE_REPORT_OPERATION,
    ArtifactDeclaration,
    RuntimeArtifact,
    SkillRuntimeRequest,
    SkillRuntimeResponse,
)

logger = logging.getLogger(__name__)

# task_id must be 1-64 hex characters
_TASK_ID_RE = re.compile(r"^[a-fA-F0-9]{1,64}$")

# skill_id from models (same pattern): lowercase letters, digits, dots, hyphens, underscores
_SKILL_ID_RE = re.compile(r"^[a-z0-9]([a-z0-9._-]*[a-z0-9])?$")

# semantic version
_VERSION_RE = re.compile(r"^\d+\.\d+\.\d+$")

# Sensitive param key patterns (case-insensitive) — Batch 3.1.1A
_SENSITIVE_KEY_PATTERNS = (
    "password", "token", "api_key", "secret", "credential",
)


# ── Params validation (Batch 3.1.1A) ──


def _check_json_compatible(
    value: object,
    *,
    depth: int = 0,
    _path: str = "params",
) -> None:
    """Recursively check that a value is JSON-compatible.

    Raises RuntimeProtocolError if the value or any nested value
    violates JSON compatibility, depth limits, or size limits.
    """
    if depth > MAX_JSON_NESTING_DEPTH:
        raise RuntimeProtocolError(
            f"JSON nesting depth exceeded: {depth} > {MAX_JSON_NESTING_DEPTH} "
            f"at '{_path}'"
        )

    if value is None:
        return
    if isinstance(value, bool):
        return
    if isinstance(value, int):
        return
    if isinstance(value, float):
        if value != value or value == float("inf") or value == float("-inf"):
            raise RuntimeProtocolError(
                f"NaN/Infinity not allowed at '{_path}': {value}"
            )
        return
    if isinstance(value, str):
        str_bytes = len(value.encode("utf-8"))
        if str_bytes > MAX_JSON_STRING_BYTES:
            raise RuntimeProtocolError(
                f"String exceeds {MAX_JSON_STRING_BYTES} bytes at '{_path}': "
                f"{str_bytes} bytes"
            )
        return
    if isinstance(value, (list, tuple)):
        if len(value) > MAX_JSON_CONTAINER_ITEMS:
            raise RuntimeProtocolError(
                f"Container exceeds {MAX_JSON_CONTAINER_ITEMS} items "
                f"at '{_path}': {len(value)}"
            )
        for i, item in enumerate(value):
            _check_json_compatible(item, depth=depth + 1, _path=f"{_path}[{i}]")
        return
    if isinstance(value, dict):
        if len(value) > MAX_JSON_CONTAINER_ITEMS:
            raise RuntimeProtocolError(
                f"Container exceeds {MAX_JSON_CONTAINER_ITEMS} items "
                f"at '{_path}': {len(value)}"
            )
        for k, v in value.items():
            if not isinstance(k, str):
                raise RuntimeProtocolError(
                    f"Dict key must be string at '{_path}': {type(k).__name__}"
                )
            _check_json_compatible(v, depth=depth + 1, _path=f"{_path}.{k}")
        return
    raise RuntimeProtocolError(
        f"Non-JSON-compatible type at '{_path}': {type(value).__name__}"
    )


def validate_params(params: dict[str, object] | None) -> None:
    """Validate user-supplied params for strict JSON compatibility.

    Raises RuntimeProtocolError on any violation.
    """
    if params is None:
        return
    if not isinstance(params, dict):
        raise RuntimeProtocolError(
            f"params must be a dict, got {type(params).__name__}"
        )
    # Check all keys are strings
    for k in params:
        if not isinstance(k, str):
            raise RuntimeProtocolError(
                f"params key must be string, got {type(k).__name__}: {k!r}"
            )
    _check_json_compatible(params, depth=0)


# ── Redaction helpers (Batch 3.1.1A) ──

_REDACTED = "***REDACTED***"


def _is_sensitive_key(key: str) -> bool:
    """Check if a key name matches a sensitive pattern (case-insensitive)."""
    key_lower = key.lower()
    return any(pattern in key_lower for pattern in _SENSITIVE_KEY_PATTERNS)


def collect_redaction_values(params: dict[str, object] | None) -> set[str]:
    """Recursively collect values for sensitive keys from params.

    Returns a set of string values that must be redacted from output.
    Empty strings and None are excluded.
    """
    values: set[str] = set()
    if params is None:
        return values
    _collect_redaction_values(params, values)
    return values


def _collect_redaction_values(obj: object, values: set[str]) -> None:
    """Recursively scan for sensitive-key values."""
    if isinstance(obj, dict):
        for k, v in obj.items():
            if isinstance(k, str) and _is_sensitive_key(k):
                if isinstance(v, str) and v:
                    values.add(v)
            else:
                _collect_redaction_values(v, values)
    elif isinstance(obj, list):
        for item in obj:
            _collect_redaction_values(item, values)


def redact_text(text: str, redaction_values: set[str]) -> str:
    """Replace all sensitive values in text with ***REDACTED***."""
    if not redaction_values:
        return text
    result = text
    for secret in redaction_values:
        if secret:
            result = result.replace(secret, _REDACTED)
    return result


def redact_dict(data: dict[str, object], redaction_values: set[str]) -> dict[str, object]:
    """Return a new dict with sensitive values redacted."""
    return _redact_value(data, redaction_values)  # type: ignore[return-value]


def _redact_value(value: object, redaction_values: set[str]) -> object:
    """Recursively redact sensitive values."""
    if isinstance(value, str):
        return redact_text(value, redaction_values)
    if isinstance(value, dict):
        return {
            str(k): (
                _REDACTED if (isinstance(k, str) and _is_sensitive_key(k))
                else _redact_value(v, redaction_values)
            )
            for k, v in value.items()
        }
    if isinstance(value, list):
        return [_redact_value(item, redaction_values) for item in value]
    return value


# ── Atomic I/O ──


def _atomic_write_json(path: Path, data: object) -> None:
    """Atomically write JSON data to a file.

    Uses tempfile + os.fsync + os.replace pattern (same as Registry).
    """
    json_text = json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True, default=str)

    parent = path.parent
    fd, tmp_str = -1, ""
    try:
        fd, tmp_str = tempfile.mkstemp(
            dir=str(parent),
            prefix=".runtime_",
            suffix=".tmp",
        )
        tmp_path = Path(tmp_str)
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as f:
            f.write(json_text)
            f.flush()
            os.fsync(f.fileno())
        fd = -1
    except OSError as e:
        if fd >= 0:
            try:
                os.close(fd)
            except OSError:
                pass
        if tmp_str:
            try:
                Path(tmp_str).unlink(missing_ok=True)
            except OSError:
                pass
        raise RuntimeProtocolError(
            f"Failed to write runtime file: {path}\n{e}"
        ) from e

    try:
        os.replace(tmp_str, path)
    except OSError as e:
        try:
            Path(tmp_str).unlink(missing_ok=True)
        except OSError:
            pass
        raise RuntimeProtocolError(
            f"Failed to atomically replace: {path}\n{e}"
        ) from e


def _read_json_safe(path: Path, max_bytes: int) -> dict:
    """Read and parse a JSON file with size limit check.

    Returns parsed dict.  Raises RuntimeProtocolError on failure.
    """
    if not path.is_file():
        raise RuntimeProtocolError(
            f"Response file not found: {path}"
        )

    try:
        file_size = path.stat().st_size
    except OSError as e:
        raise RuntimeProtocolError(
            f"Cannot stat response file: {path}\n{e}"
        ) from e

    if file_size == 0:
        raise RuntimeProtocolError(
            f"Response file is empty: {path}"
        )

    if file_size > max_bytes:
        raise RuntimeProtocolError(
            f"Response file exceeds size limit: {file_size} > {max_bytes} bytes"
        )

    try:
        raw = path.read_text(encoding="utf-8")
    except OSError as e:
        raise RuntimeProtocolError(
            f"Cannot read response file: {path}\n{e}"
        ) from e

    try:
        data = json.loads(raw)
    except json.JSONDecodeError as e:
        raise RuntimeProtocolError(
            f"Response JSON is invalid: {path}\n{e}"
        ) from e

    if not isinstance(data, dict):
        raise RuntimeProtocolError(
            f"Response JSON root must be a dict, got {type(data).__name__}"
        )

    return data


# ── Request validation ──


def validate_request(request: SkillRuntimeRequest) -> None:
    """Validate a SkillRuntimeRequest before sending to the worker.

    Raises RuntimeProtocolError if any field fails validation.
    All checks are pure logic — no filesystem access here.
    (The caller must separately verify filesystem state.)
    """
    # 1. Protocol version
    if request.protocol_version != RUNTIME_PROTOCOL_VERSION:
        raise RuntimeProtocolError(
            f"Unsupported protocol version: {request.protocol_version}. "
            f"Expected: {RUNTIME_PROTOCOL_VERSION}"
        )

    # 2. Operation
    if request.operation not in ALLOWED_OPERATIONS:
        raise RuntimeProtocolError(
            f"Operation not allowed: '{request.operation}'. "
            f"Allowed: {sorted(ALLOWED_OPERATIONS)}"
        )

    # 3. Task ID format
    if not _TASK_ID_RE.match(request.task_id):
        raise RuntimeProtocolError(
            f"Invalid task_id format: '{request.task_id}'"
        )

    # 4. Skill ID format
    if not _SKILL_ID_RE.match(request.skill_id):
        raise RuntimeProtocolError(
            f"Invalid skill_id format: '{request.skill_id}'"
        )

    # 5. Version format
    if not _VERSION_RE.match(request.version):
        raise RuntimeProtocolError(
            f"Invalid version format: '{request.version}'"
        )

    # 6. Installed path must be absolute
    installed = Path(request.installed_path)
    if not installed.is_absolute():
        raise RuntimeProtocolError(
            f"installed_path must be absolute: '{request.installed_path}'"
        )

    # 7. Workspace path must be absolute
    ws = Path(request.workspace_path)
    if not ws.is_absolute():
        raise RuntimeProtocolError(
            f"workspace_path must be absolute: '{request.workspace_path}'"
        )

    # 8. Entrypoint — parse and validate
    try:
        module_path, func_name = parse_entrypoint_ref(request.entrypoint)
    except ValueError as e:
        raise RuntimeProtocolError(
            f"Invalid entrypoint reference: {e}"
        ) from e

    # module_path must be relative and not have '..'
    module_p = Path(module_path)
    if module_p.is_absolute():
        raise RuntimeProtocolError(
            f"Entrypoint module path must be relative: '{module_path}'"
        )
    if ".." in module_p.parts:
        raise RuntimeProtocolError(
            f"Entrypoint module path contains '..': '{module_path}'"
        )

    # 9. Timeout must be positive
    if request.timeout_seconds <= 0:
        raise RuntimeProtocolError(
            f"timeout_seconds must be > 0: {request.timeout_seconds}"
        )

    # 10. Capabilities — every requested capability must be a non-empty string
    for cap in request.capabilities:
        if not cap or not isinstance(cap, str):
            raise RuntimeProtocolError(
                f"Invalid capability: {cap!r}"
            )

    # 11. Environment — keys must be valid env var names
    for key in request.environment:
        if not key or not key.isupper() or not re.match(r'^[A-Z][A-Z0-9_]*$', key):
            raise RuntimeProtocolError(
                f"Invalid environment variable name: '{key}'"
            )

    # 12. Params validation (Batch 3.1.1A)
    if request.operation in {"run", GENERATE_REPORT_OPERATION}:
        if request.params is None:
            raise RuntimeProtocolError(
                f"params must be provided for operation='{request.operation}'"
            )
        validate_params(request.params)
        if request.operation == GENERATE_REPORT_OPERATION:
            _validate_generate_report_params(request.params)
    else:
        # healthcheck: params must be None
        if request.params is not None:
            raise RuntimeProtocolError(
                "params must be None for operation='healthcheck'"
            )


def _validate_generate_report_params(params: dict[str, object]) -> None:
    """Require the small host-created Batch 3.5.2 request envelope."""
    expected_keys = {
        "job_path",
        "output_artifact_type",
        "expected_output",
        "contract_version",
    }
    if set(params) != expected_keys:
        raise RuntimeProtocolError(
            "generate_report params must use the fixed report job envelope"
        )
    artifact_type = params.get("output_artifact_type")
    if artifact_type not in {"pptx", "docx"}:
        raise RuntimeProtocolError(
            "generate_report output_artifact_type must be pptx or docx"
        )
    if params.get("job_path") != "input/report_job.json":
        raise RuntimeProtocolError(
            "generate_report job_path must be input/report_job.json"
        )
    if params.get("expected_output") != f"output/report.{artifact_type}":
        raise RuntimeProtocolError(
            "generate_report expected_output does not match output format"
        )
    contract_version = params.get("contract_version")
    if isinstance(contract_version, bool) or contract_version != 1:
        raise RuntimeProtocolError(
            "generate_report contract_version must be 1"
        )


# ── Artifact declarations validation (Batch 3.2.1A) ──


def validate_artifact_declarations(
    raw: object,
    operation: str,
) -> tuple[ArtifactDeclaration, ...]:
    """Validate and parse the artifact_declarations wire field.

    Args:
        raw: The raw value from result.json artifact_declarations key.
        operation: The operation type ("healthcheck" or "run").

    Returns:
        Tuple of validated ArtifactDeclaration (may be empty).

    Raises:
        RuntimeProtocolError: If declarations are invalid for the operation.
    """
    if raw is None:
        return ()

    if not isinstance(raw, list):
        raise RuntimeProtocolError(
            f"artifact_declarations must be a list, got {type(raw).__name__}"
        )

    if operation == "healthcheck":
        if len(raw) > 0:
            raise RuntimeProtocolError(
                "artifact_declarations must be empty for operation='healthcheck'"
            )
        return ()

    if operation not in {"run", GENERATE_REPORT_OPERATION}:
        raise RuntimeProtocolError(
            f"artifact_declarations not allowed for operation='{operation}'"
        )

    if len(raw) == 0:
        return ()

    # Parse each declaration strictly
    declarations: list[ArtifactDeclaration] = []
    for i, item in enumerate(raw):
        if not isinstance(item, dict):
            raise RuntimeProtocolError(
                f"artifact_declarations[{i}] must be a dict, "
                f"got {type(item).__name__}"
            )
        try:
            decl = ArtifactDeclaration.from_wire_dict(item)
        except ValueError as e:
            raise RuntimeProtocolError(
                f"artifact_declarations[{i}] invalid: {e}"
            ) from e
        declarations.append(decl)

    # ── Batch 3.2.1A-R: canonical wire size check ──
    if declarations:
        wire_list = [d.to_wire_dict() for d in declarations]
        try:
            encoded = json.dumps(
                wire_list,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
                allow_nan=False,
            ).encode("utf-8")
        except (ValueError, TypeError) as e:
            raise RuntimeProtocolError(
                f"artifact_declarations cannot be serialized: {e}"
            ) from e

        if len(encoded) > MAX_DECLARE_MANIFEST_BYTES:
            raise RuntimeProtocolError(
                "Artifact declaration manifest exceeds the 32768-byte limit."
            )

    return tuple(declarations)


def validate_runtime_artifact(
    artifact: RuntimeArtifact,
    *,
    operation: str,
    published: bool = False,
) -> None:
    """Validate a RuntimeArtifact against operation-specific rules.

    Two modes:
      - healthcheck (tolerant): Old 3-field OK, sha256=None OK,
        new fields optional.
      - published run (strict): artifact_schema_version==1, artifact_id=32 hex,
        sha256=64 hex, storage_relpath present, owner fields present,
        kind in allowlist.

    Raises RuntimeProtocolError on violation.
    """
    # ── Common checks (always) ──
    if not artifact.relative_path:
        raise RuntimeProtocolError("RuntimeArtifact has empty relative_path")
    if artifact.size_bytes < 0:
        raise RuntimeProtocolError(
            f"RuntimeArtifact size_bytes must be >= 0, got {artifact.size_bytes}"
        )

    if published:
        # ── Strict published-run mode ──
        if artifact.artifact_schema_version != ARTIFACT_SCHEMA_VERSION:
            raise RuntimeProtocolError(
                f"RuntimeArtifact artifact_schema_version must be "
                f"{ARTIFACT_SCHEMA_VERSION}, got {artifact.artifact_schema_version}"
            )
        # artifact_id must be 32 lowercase hex
        if not _ARTIFACT_ID_RE.match(artifact.artifact_id):
            raise RuntimeProtocolError(
                f"RuntimeArtifact artifact_id must be 32 lowercase hex chars, "
                f"got: {artifact.artifact_id!r}"
            )
        # storage_relpath must be non-empty and not absolute
        if not artifact.storage_relpath:
            raise RuntimeProtocolError(
                "RuntimeArtifact storage_relpath must be non-empty for published artifact"
            )
        sp = Path(artifact.storage_relpath)
        if sp.is_absolute() or ".." in sp.parts:
            raise RuntimeProtocolError(
                f"RuntimeArtifact storage_relpath must be safe relative path, "
                f"got: {artifact.storage_relpath!r}"
            )
        # sha256 must be 64 lowercase hex
        if not artifact.sha256 or not _SHA256_RE.match(artifact.sha256):
            raise RuntimeProtocolError(
                f"RuntimeArtifact sha256 must be 64 lowercase hex chars, "
                f"got: {artifact.sha256!r}"
            )
        # kind must be valid
        if artifact.kind not in VALID_ARTIFACT_KINDS:
            raise RuntimeProtocolError(
                f"RuntimeArtifact kind must be one of "
                f"{sorted(VALID_ARTIFACT_KINDS)}, got: {artifact.kind!r}"
            )
        # display_name must be non-empty
        if not artifact.display_name or not artifact.display_name.strip():
            raise RuntimeProtocolError(
                "RuntimeArtifact display_name must be non-empty for published artifact"
            )
        # media_type must be non-empty
        if not artifact.media_type:
            raise RuntimeProtocolError(
                "RuntimeArtifact media_type must be non-empty for published artifact"
            )
        # owner fields must be present
        if not artifact.skill_id or not artifact.version or not artifact.task_id:
            raise RuntimeProtocolError(
                "RuntimeArtifact skill_id/version/task_id must be present "
                "for published artifact"
            )
        # created_at must be present
        if not artifact.created_at:
            raise RuntimeProtocolError(
                "RuntimeArtifact created_at must be present for published artifact"
            )
    else:
        # ── Tolerant healthcheck mode ──
        # No additional checks beyond the common ones
        pass


# Compile regex once
import re as _re_mod
_ARTIFACT_ID_RE = _re_mod.compile(r"^[a-f0-9]{32}$")
_SHA256_RE = _re_mod.compile(r"^[a-f0-9]{64}$")


# ── Response validation ──


def validate_response(
    response: SkillRuntimeResponse,
    expected_task_id: str,
    expected_operation: str,
    workspace_path: Path,
) -> None:
    """Validate a SkillRuntimeResponse from the worker.

    Raises RuntimeProtocolError if any field fails validation.

    Checks:
    - protocol_version match
    - task_id match
    - operation match
    - status is valid
    - health dict schema (if present)
    - artifact paths are within workspace/output
    - message length within limits
    """
    # 1. Protocol version
    if response.protocol_version != RUNTIME_PROTOCOL_VERSION:
        raise RuntimeProtocolError(
            f"Response protocol version mismatch: "
            f"{response.protocol_version} != {RUNTIME_PROTOCOL_VERSION}"
        )

    # 2. Task ID match
    if response.task_id != expected_task_id:
        raise RuntimeProtocolError(
            f"Response task_id mismatch: "
            f"'{response.task_id}' != '{expected_task_id}'"
        )

    # 3. Operation match
    if response.operation != expected_operation:
        raise RuntimeProtocolError(
            f"Response operation mismatch: "
            f"'{response.operation}' != '{expected_operation}'"
        )

    # 4. Valid status (operation-specific — Batch 3.1.1A)
    if response.operation == "healthcheck":
        if response.status not in VALID_HEALTHCHECK_STATUSES:
            raise RuntimeProtocolError(
                f"Invalid healthcheck response status: '{response.status}'"
            )
    elif response.operation in {"run", GENERATE_REPORT_OPERATION}:
        if response.status not in VALID_RUN_STATUSES:
            raise RuntimeProtocolError(
                f"Invalid run response status: '{response.status}'"
            )
    else:
        raise RuntimeProtocolError(
            f"Unknown operation for response validation: '{response.operation}'"
        )

    # 5. Duration must be non-negative
    if response.duration_ms < 0:
        raise RuntimeProtocolError(
            f"Negative duration_ms: {response.duration_ms}"
        )

    # 6. Health dict validation (if present)
    if response.health is not None:
        if not isinstance(response.health, dict):
            raise RuntimeProtocolError("health field must be a dict")
        # healthy key must be bool if present at top level
        if "healthy" in response.health:
            if not isinstance(response.health["healthy"], bool):
                raise RuntimeProtocolError(
                    "health.healthy must be a boolean"
                )

    # 7. Artifact paths must be relative and within output
    output_dir = workspace_path / "output"
    for artifact in response.artifacts:
        if not artifact.relative_path:
            raise RuntimeProtocolError("Artifact has empty relative_path")
        artifact_p = Path(artifact.relative_path)
        if artifact_p.is_absolute():
            raise RuntimeProtocolError(
                f"Artifact path must be relative: '{artifact.relative_path}'"
            )
        if ".." in artifact_p.parts:
            raise RuntimeProtocolError(
                f"Artifact path contains '..': '{artifact.relative_path}'"
            )
        # Resolve against output dir and verify it's within
        resolved = (output_dir / artifact.relative_path).resolve()
        try:
            resolved.relative_to(output_dir.resolve())
        except ValueError:
            raise RuntimeProtocolError(
                f"Artifact path escapes output dir: '{artifact.relative_path}'"
            )

    # 8. Message length check (non-blocking — log warning only)
    from dp_engine.skills.runtime_models import MAX_HEALTHCHECK_MESSAGE_LENGTH
    if len(response.message) > MAX_HEALTHCHECK_MESSAGE_LENGTH:
        logger.warning(
            "Response message exceeds %d chars (actual: %d)",
            MAX_HEALTHCHECK_MESSAGE_LENGTH, len(response.message),
        )


# ── Public atomic I/O ──


def write_request_atomic(request: SkillRuntimeRequest, path: Path) -> None:
    """Atomically write a validated request to request.json.

    Checks MAX_REQUEST_BYTES before writing (Batch 3.1.1A).
    """
    data = request.to_dict()
    json_text = json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True, default=str)
    json_bytes = len(json_text.encode("utf-8"))
    if json_bytes > MAX_REQUEST_BYTES:
        raise RuntimeProtocolError(
            f"Request serialized size exceeds {MAX_REQUEST_BYTES} bytes: "
            f"{json_bytes} bytes"
        )
    _atomic_write_json(path, data)


def read_response_atomic(
    path: Path,
    expected_task_id: str,
    expected_operation: str,
    workspace_path: Path,
) -> SkillRuntimeResponse:
    """Read, parse, and validate a result.json from the worker.

    Returns a validated SkillRuntimeResponse.
    Raises RuntimeProtocolError on any failure.

    artifact_declarations is stripped from the public response.
    For internal declarations, use read_worker_response_atomic().
    """
    response, _ = read_worker_response_atomic(
        path, expected_task_id, expected_operation, workspace_path,
    )
    return response


def read_worker_response_atomic(
    path: Path,
    expected_task_id: str,
    expected_operation: str,
    workspace_path: Path,
) -> tuple[SkillRuntimeResponse, tuple[ArtifactDeclaration, ...]]:
    """Read, parse, and validate a result.json from the worker.

    Returns both the public SkillRuntimeResponse AND the internal
    ArtifactDeclaration tuple.  This is the host-private entry point
    used by the Service to access declarations for artifact publishing.

    The public SkillRuntimeResponse does NOT expose artifact_declarations.

    Batch 3.2.1B: declarations are now consumed by the Service for
    artifact publishing.
    """
    data = _read_json_safe(path, MAX_RESULT_JSON_BYTES)

    # ── Extract and validate internal wire field ──
    raw_declarations = data.pop("artifact_declarations", None)
    validated_declarations = validate_artifact_declarations(
        raw_declarations, expected_operation,
    )

    # Parse from dict (without artifact_declarations)
    try:
        response = SkillRuntimeResponse.from_dict(data)
    except Exception as e:
        raise RuntimeProtocolError(
            f"Failed to parse response from {path}: {e}"
        ) from e

    # Validate
    validate_response(response, expected_task_id, expected_operation, workspace_path)

    return response, validated_declarations
