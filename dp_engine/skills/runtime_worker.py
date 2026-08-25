"""SkillRuntime worker — isolated subprocess entry point.

This script is launched as a SEPARATE Python process via:
    python -I dp_engine/skills/runtime_worker.py --request <request.json>

It loads a skill's healthcheck entrypoint, executes it inside audit hooks,
and atomically writes result.json.

The parent process NEVER imports or executes any skill code.
Only this Worker — in an isolated subprocess — imports skill modules.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import logging
import os
import sys
import time
import traceback
from datetime import datetime, timezone
from pathlib import Path

from dp_engine.skills.runtime_models import (
    ALLOWED_ARTIFACT_EXTENSIONS,
    MAX_ARTIFACTS_PER_RUN,
    MAX_ARTIFACT_FILE_BYTES,
    MAX_ARTIFACTS_TOTAL_BYTES,
    MAX_RESULT_JSON_BYTES,
    MAX_STDOUT_BYTES,
    GENERATE_REPORT_OPERATION,
    MAX_STDERR_BYTES,
)
from dp_engine.skills.runtime_protocol import (
    collect_redaction_values,
    redact_dict,
    redact_text,
)

# ── Batch 3.2.1B: import ArtifactRunContext BEFORE audit hooks ──
from dp_engine.skills.runtime_artifacts import ArtifactRunContext

# Sentinel strings for identifying audit-hook rejections in RuntimeError messages
_AUDIT_HOOK_BLOCKED_SENTINELS = (
    "blocked by skill runtime audit hook",
    "permission_denied",
)

def _is_audit_hook_rejection(exc: Exception) -> bool:
    """Check if an exception was raised by the runtime audit hooks."""
    if isinstance(exc, PermissionError):
        return True
    msg = str(exc)
    return any(s in msg for s in _AUDIT_HOOK_BLOCKED_SENTINELS)

# ── We are in a subprocess; configure basic logging early ──

logging.basicConfig(
    level=logging.DEBUG,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    stream=sys.stderr,
)
logger = logging.getLogger("runtime_worker")


def _now_iso() -> str:
    """Return current UTC time as ISO 8601 string."""
    return datetime.now(timezone.utc).isoformat()


def _atomic_write_json(path: Path, data: object) -> None:
    """Atomically write JSON to a file (tmp + fsync + replace)."""
    import tempfile as _tf

    json_text = json.dumps(
        data, ensure_ascii=False, indent=2, sort_keys=True, default=str
    )
    parent = path.parent
    fd, tmp_str = -1, ""
    try:
        fd, tmp_str = _tf.mkstemp(
            dir=str(parent), prefix=".result_", suffix=".tmp"
        )
        tmp_path = Path(tmp_str)
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as f:
            f.write(json_text)
            f.flush()
            os.fsync(f.fileno())
        fd = -1
        os.replace(tmp_str, path)
    finally:
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


# ── Healthcheck result validation ──


def _validate_healthcheck_return(value: object) -> dict:
    """Validate the return value of a healthcheck function.

    Returns a dict suitable for the 'health' field of the response.
    Raises ValueError on invalid return.
    """
    if value is None:
        raise ValueError(
            "Healthcheck function returned None. "
            "Expected a dict with 'healthy' key."
        )

    if not isinstance(value, dict):
        raise ValueError(
            f"Healthcheck function returned {type(value).__name__}, "
            f"expected a dict with 'healthy' key."
        )

    if "healthy" not in value:
        raise ValueError(
            "Healthcheck return dict missing required 'healthy' key."
        )

    healthy = value["healthy"]
    if not isinstance(healthy, bool):
        raise ValueError(
            f"Healthcheck 'healthy' must be bool, got {type(healthy).__name__}"
        )

    message = str(value.get("message", "")) if isinstance(value.get("message"), str) else ""
    details = value.get("details", {})
    if not isinstance(details, dict):
        details = {}

    # Size limit on message
    if len(message) > 1000:
        message = message[:1000] + "...[truncated]"

    # Basic size check on details
    try:
        details_json = json.dumps(details, default=str)
        if len(details_json) > 100 * 1024:
            details = {"_truncated": True, "_original_size": len(details_json)}
    except Exception:
        details = {"_serialization_error": True}

    return {
        "healthy": healthy,
        "message": message,
        "details": details,
    }


# ── Entrypoint loading ──


def _load_entrypoint_function(
    installed_path: Path,
    entrypoint: str,
) -> object:
    """Load a healthcheck function from a skill's installed directory.

    Args:
        installed_path: Root of the installed skill directory.
        entrypoint: "relative/path/to/module.py:function_name"

    Returns:
        The callable function object.

    Raises:
        ValueError: If entrypoint format is invalid.
        ImportError: If module cannot be imported.
        AttributeError: If function not found in module.
        TypeError: If the target is not callable.
    """
    from dp_engine.skills.models import parse_entrypoint_ref

    # Parse entrypoint reference
    module_rel_path, func_name = parse_entrypoint_ref(entrypoint)

    # Security: resolve and verify the module path is within installed_path
    module_path = (installed_path / module_rel_path).resolve()
    try:
        module_path.relative_to(installed_path.resolve())
    except ValueError:
        raise ValueError(
            f"Entrypoint module resolves outside installed directory: "
            f"'{module_rel_path}' → '{module_path}'"
        )

    if not module_path.is_file():
        raise ImportError(
            f"Entrypoint module not found: {module_path}"
        )

    # Build a unique module name to avoid collisions
    module_name = (
        f"_skill_healthcheck_"
        + module_rel_path.replace("/", "_").replace("\\", "_").replace(".py", "")
    )

    # Load the module
    spec = importlib.util.spec_from_file_location(
        module_name, str(module_path)
    )
    if spec is None or spec.loader is None:
        raise ImportError(
            f"Cannot create module spec for: {module_path}"
        )

    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)

    # Get the function
    func = getattr(module, func_name, None)
    if func is None:
        raise AttributeError(
            f"Function '{func_name}' not found in module '{module_rel_path}'"
        )

    if not callable(func):
        raise TypeError(
            f"Entrypoint '{func_name}' in '{module_rel_path}' is not callable"
        )

    return func


# ── Main worker ──


def run_worker(request_path: str) -> None:
    """Main worker entry point.

    1. Load and validate request.json
    2. Install audit hooks
    3. Sanitize environment
    4. Load and execute healthcheck entrypoint
    5. Write result.json
    """
    started_at = _now_iso()
    t_start = time.monotonic()

    rp = Path(request_path).resolve()
    workspace_path = rp.parent
    result_path = workspace_path / "result.json"

    # ── 1. Load request ──
    logger.info("Worker starting, request: %s", rp)
    try:
        if not rp.is_file():
            _write_error_result(
                result_path, started_at,
                status="crashed",
                message=f"Request file not found: {rp}",
                error_type="RuntimeProtocolError",
            )
            return

        raw = rp.read_text(encoding="utf-8")
        request_data = json.loads(raw)
    except Exception as e:
        _write_error_result(
            result_path, started_at,
            status="crashed",
            message=f"Failed to read request: {e}",
            error_type=type(e).__name__,
        )
        return

    task_id = str(request_data.get("task_id", "unknown"))
    operation = str(request_data.get("operation", "unknown"))
    installed_path = Path(str(request_data.get("installed_path", "")))
    entrypoint = str(request_data.get("entrypoint", ""))
    caps = tuple(str(c) for c in request_data.get("capabilities", []))

    # Redirect stdout/stderr to workspace logs
    stdout_log = workspace_path / "stdout.log"
    stderr_log = workspace_path / "stderr.log"
    try:
        _redirect_stdio(stdout_log, stderr_log)
    except OSError:
        pass  # Non-fatal

    # ── 2. Install audit hooks ──
    try:
        from dp_engine.skills.runtime_permissions import (
            build_allowed_read_roots,
            build_allowed_write_roots,
            install_audit_hooks,
        )

        read_roots = build_allowed_read_roots(installed_path, workspace_path)
        write_roots = build_allowed_write_roots(workspace_path)
        install_audit_hooks(
            allowed_read_roots=read_roots,
            allowed_write_roots=write_roots,
            capabilities=caps,
        )
        logger.info("Audit hooks installed")
    except Exception as e:
        _write_error_result(
            result_path, started_at, task_id, operation,
            status="crashed",
            message=f"Failed to install audit hooks: {e}",
            error_type=type(e).__name__,
        )
        return

    # ── 3. Sanitize environment ──
    try:
        from dp_engine.skills.runtime_permissions import build_sanitized_env

        sanitized = build_sanitized_env()
        # Apply to current process
        for key in list(os.environ.keys()):
            if key not in sanitized:
                del os.environ[key]
        for key, value in sanitized.items():
            os.environ[key] = value
        logger.info("Environment sanitized")
    except Exception as e:
        logger.warning("Environment sanitization error (non-fatal): %s", e)

    # ── 4. Load and execute based on operation ──
    # Collect per-operation redaction values for safe stdio cleanup
    redaction_values: set[str] = set()
    if operation in {"run", GENERATE_REPORT_OPERATION}:
        params = request_data.get("params", {})
        if isinstance(params, dict):
            redaction_values = collect_redaction_values(dict(params))

    if operation == "healthcheck":
        _execute_healthcheck(
            result_path, started_at, t_start,
            task_id, operation, installed_path, entrypoint,
            caps, request_data, workspace_path,
        )
    elif operation in {"run", GENERATE_REPORT_OPERATION}:
        _execute_run(
            result_path, started_at, t_start,
            task_id, operation, installed_path, entrypoint,
            caps, request_data, workspace_path,
            redaction_values,
        )
    else:
        _write_error_result(
            result_path, started_at, task_id, operation,
            status="crashed",
            message=f"Unknown operation: '{operation}'",
            error_type="RuntimeProtocolError",
        )
        # NOTE: no redaction_values for unknown-operation path

    # ── Batch 3.1.1B-S: flush and close redirected stdio ──
    # The authoritative redaction of stdout.log / stderr.log happens in
    # SkillRuntimeService.run_skill() (parent process), where file access
    # is not subject to Windows handle-close races.
    for handle_attr in ("stdout", "stderr"):
        try:
            getattr(sys, handle_attr).close()
        except Exception:
            pass
        setattr(sys, handle_attr, getattr(sys, f"__{handle_attr}__"))


def _execute_healthcheck(
    result_path: Path,
    started_at: str,
    t_start: float,
    task_id: str,
    operation: str,
    installed_path: Path,
    entrypoint: str,
    caps: tuple[str, ...],
    request_data: dict,
    workspace_path: Path,
) -> None:
    """Execute healthcheck entrypoint and write result.json."""
    try:
        func = _load_entrypoint_function(installed_path, entrypoint)
    except Exception as e:
        _write_error_result(
            result_path, started_at, task_id, operation,
            status="unhealthy",
            message=f"Failed to load healthcheck entrypoint: {e}",
            error_type=type(e).__name__,
            error_message=str(e),
            error_traceback=traceback.format_exc(),
        )
        return

    # Build context
    healthcheck_context = {
        "skill_id": str(request_data.get("skill_id", "")),
        "version": str(request_data.get("version", "")),
        "installed_path": str(installed_path),
        "workspace_path": str(workspace_path),
        "capabilities": list(caps),
    }

    # Execute
    try:
        raw_result = func(healthcheck_context)  # type: ignore[reportCallIssue]
        health_dict = _validate_healthcheck_return(raw_result)
    except Exception as e:
        _write_error_result(
            result_path, started_at, task_id, operation,
            status="unhealthy",
            message=f"Healthcheck execution failed: {e}",
            error_type=type(e).__name__,
            error_message=str(e),
            error_traceback=traceback.format_exc(),
        )
        return

    t_end = time.monotonic()
    # Floor completed operations at 1 ms — on Windows, time.monotonic()
    # granularity can round sub-tick elapsed to 0 ms even though the
    # real wall-clock span (visible in started_at/finished_at ISO) is
    # several milliseconds.  The 1-ms floor preserves the "this
    # operation ran" signal without falsifying the actual time.
    duration_ms = max(1, int((t_end - t_start) * 1000))

    is_healthy = bool(health_dict.get("healthy", False))
    status = "healthy" if is_healthy else "unhealthy"
    message = str(health_dict.get("message", ""))

    # Collect output artifacts
    artifacts: list[dict] = []
    output_dir = workspace_path / "output"
    if output_dir.is_dir():
        try:
            for entry in sorted(
                output_dir.iterdir(),
                key=lambda e: e.name,
            ):
                if entry.is_file():
                    st = entry.stat()
                    artifacts.append({
                        "relative_path": entry.name,
                        "size_bytes": st.st_size,
                        "sha256": None,
                    })
        except OSError:
            pass

    response = {
        "protocol_version": 1,
        "task_id": task_id,
        "operation": operation,
        "success": True,
        "status": status,
        "message": message,
        "started_at": started_at,
        "finished_at": _now_iso(),
        "duration_ms": duration_ms,
        "health": health_dict,
        "artifacts": artifacts,
        "warnings": [],
    }

    _atomic_write_json(result_path, response)
    logger.info(
        "Worker finished: status=%s duration_ms=%d", status, duration_ms
    )


def _execute_run(
    result_path: Path,
    started_at: str,
    t_start: float,
    task_id: str,
    operation: str,
    installed_path: Path,
    entrypoint: str,
    caps: tuple[str, ...],
    request_data: dict,
    workspace_path: Path,
    redaction_values: set[str],
) -> None:
    """Execute run entrypoint and write result.json (Batch 3.1.1A).

    Calls run(context) and wraps the return value as the 'result' field.
    The skill's return value (even {"ok": false}) is treated as a
    successful business payload → status="succeeded".

    Batch 3.2.1B: Injects ArtifactRunContext, performs real filesystem
    observation of declared artifacts, and writes artifact_declarations
    wire field.
    """
    import hashlib
    import stat as _worker_stat

    params = request_data.get("params", {})
    if not isinstance(params, dict):
        params = {}

    # Load entrypoint function
    try:
        func = _load_entrypoint_function(installed_path, entrypoint)
    except Exception as e:
        if _is_audit_hook_rejection(e):
            _write_error_result(
                result_path, started_at, task_id, operation,
                status="permission_denied",
                message=f"Run permission denied during module load: {e}",
                error_type=type(e).__name__,
                error_message=redact_text(str(e), redaction_values),
                error_traceback=traceback.format_exc(),
                redaction_values=redaction_values,
            )
        else:
            _write_error_result(
                result_path, started_at, task_id, operation,
                status="failed",
                message=f"Failed to load run entrypoint: {e}",
                error_type=type(e).__name__,
                error_message=redact_text(str(e), redaction_values),
                error_traceback=traceback.format_exc(),
                redaction_values=redaction_values,
            )
        return

    # ── Batch 3.2.1B: Build ArtifactRunContext (dict subclass) ──
    context = ArtifactRunContext({
        "skill_id": str(request_data.get("skill_id", "")),
        "version": str(request_data.get("version", "")),
        "installed_path": str(installed_path),
        "workspace_path": str(workspace_path),
        "capabilities": list(caps),
        "params": dict(params),
    })

    # Execute run function
    try:
        raw_result = func(context)  # type: ignore[reportCallIssue]
    except Exception as e:
        is_report_operation = operation == GENERATE_REPORT_OPERATION
        safe_error_message = (
            "Report backend execution failed"
            if is_report_operation else str(e)
        )
        safe_traceback = None if is_report_operation else traceback.format_exc()
        if _is_audit_hook_rejection(e):
            _write_error_result(
                result_path, started_at, task_id, operation,
                status="permission_denied",
                message=(
                    "Report backend permission denied"
                    if is_report_operation else f"Run permission denied: {e}"
                ),
                error_type=type(e).__name__,
                error_message=redact_text(safe_error_message, redaction_values),
                error_traceback=safe_traceback,
                redaction_values=redaction_values,
            )
        else:
            _write_error_result(
                result_path, started_at, task_id, operation,
                status="failed",
                message=(
                    "Report backend execution failed"
                    if is_report_operation else f"Run execution failed: {e}"
                ),
                error_type=type(e).__name__,
                error_message=redact_text(safe_error_message, redaction_values),
                error_traceback=safe_traceback,
                redaction_values=redaction_values,
            )
        return

    # ── Batch 3.2.1B: Check fatal declaration errors ──
    if context.has_fatal_declaration_error:
        _write_error_result(
            result_path, started_at, task_id, operation,
            status="protocol_error",
            message=(
                f"Fatal artifact declaration error: "
                f"{context.fatal_declaration_message or 'unknown'}"
            ),
            error_type="RuntimeProtocolError",
            error_message=redact_text(
                context.fatal_declaration_message or "fatal declaration error",
                redaction_values,
            ),
            redaction_values=redaction_values,
        )
        return

    # ── Batch 3.2.1B: Real file observation for pending declarations ──
    output_dir = workspace_path / "output"
    observed_declarations: list[dict[str, object]] = []
    pending = context._get_pending_declarations()

    if pending:
        output_resolved = output_dir.resolve()
        cumulative_size = 0

        for decl in pending:
            try:
                src_path = (output_dir / decl.declared_path).resolve()

                # Verify path is within output dir
                try:
                    src_path.relative_to(output_resolved)
                except ValueError:
                    raise ValueError(
                        f"declared path escapes output dir: {decl.declared_path}"
                    )

                # Verify each parent component (not just final file)
                _verify_parent_components_worker(src_path, output_resolved)

                # lstat the source file
                st = src_path.lstat()

                # Reject symlinks
                if _worker_stat.S_ISLNK(st.st_mode):
                    raise ValueError(
                        f"declared file is a symlink: {decl.declared_path}"
                    )

                # Reject reparse points (Windows)
                if hasattr(st, "st_file_attributes"):
                    FILE_ATTRIBUTE_REPARSE_POINT = 0x400
                    if st.st_file_attributes & FILE_ATTRIBUTE_REPARSE_POINT:
                        raise ValueError(
                            f"declared file is a reparse point: {decl.declared_path}"
                        )

                # Reject directories
                if _worker_stat.S_ISDIR(st.st_mode):
                    raise ValueError(
                        f"declared path is a directory: {decl.declared_path}"
                    )

                # Reject special files
                if not _worker_stat.S_ISREG(st.st_mode):
                    raise ValueError(
                        f"declared path is not a regular file: {decl.declared_path}"
                    )

                # Reject hardlinks (nlink > 1)
                if st.st_nlink > 1:
                    raise ValueError(
                        f"declared file has hardlinks (nlink={st.st_nlink}): "
                        f"{decl.declared_path}"
                    )

                # Check size
                if st.st_size > MAX_ARTIFACT_FILE_BYTES:
                    raise ValueError(
                        f"declared file exceeds max size {MAX_ARTIFACT_FILE_BYTES}: "
                        f"{st.st_size} bytes"
                    )
                cumulative_size += st.st_size
                if cumulative_size > MAX_ARTIFACTS_TOTAL_BYTES:
                    raise ValueError(
                        f"cumulative artifact size exceeds {MAX_ARTIFACTS_TOTAL_BYTES} bytes"
                    )

                # Check extension allowlist
                ext = Path(decl.declared_path).suffix.lower()
                if ext not in ALLOWED_ARTIFACT_EXTENSIONS:
                    raise ValueError(
                        f"file extension not allowed: {ext!r}"
                    )

                # Compute SHA256
                sha256_hash = hashlib.sha256()
                with open(src_path, "rb") as f:
                    while True:
                        chunk = f.read(65536)
                        if not chunk:
                            break
                        sha256_hash.update(chunk)
                observed_sha256 = sha256_hash.hexdigest()

                observed_declarations.append({
                    "declared_path": decl.declared_path,
                    "display_name": decl.display_name,
                    "media_type_hint": decl.media_type_hint,
                    "kind": decl.kind,
                    "metadata": dict(decl.metadata),
                    "observed_size_bytes": st.st_size,
                    "observed_sha256": observed_sha256,
                    "observed_device": st.st_dev,
                    "observed_inode": st.st_ino,
                    "observed_mtime_ns": st.st_mtime_ns,
                })
            except ValueError:
                # Fatal: declaration failed real file observation
                _write_error_result(
                    result_path, started_at, task_id, operation,
                    status="protocol_error",
                    message=(
                        f"Artifact declaration failed file observation: "
                        f"{traceback.format_exc().split(chr(10))[-2]}"
                    ),
                    error_type="RuntimeProtocolError",
                    error_message=redact_text(
                        traceback.format_exc().split("\n")[-2]
                        if traceback.format_exc() else "observation error",
                        redaction_values,
                    ),
                    redaction_values=redaction_values,
                )
                return

    # Validate run return value
    try:
        result_dict = _validate_run_return(raw_result)
    except ValueError as e:
        _write_error_result(
            result_path, started_at, task_id, operation,
            status="protocol_error",
            message=str(e),
            error_type="RuntimeProtocolError",
            error_message=redact_text(str(e), redaction_values),
            redaction_values=redaction_values,
        )
        return

    # Redact sensitive values from result before serialization (Batch 3.1.1B)
    result_dict = redact_dict(result_dict, redaction_values)

    t_end = time.monotonic()
    # Same 1-ms floor as _execute_healthcheck — see comment there.
    duration_ms = max(1, int((t_end - t_start) * 1000))

    response: dict[str, object] = {
        "protocol_version": 1,
        "task_id": task_id,
        "operation": operation,
        "success": True,
        "status": "succeeded",
        "message": "Run completed successfully",
        "started_at": started_at,
        "finished_at": _now_iso(),
        "duration_ms": duration_ms,
        "result": result_dict,
        "artifacts": [],
        "warnings": [],
    }

    # ── Batch 3.2.1B: Write artifact_declarations wire field ──
    if observed_declarations:
        response["artifact_declarations"] = observed_declarations

    _atomic_write_json(result_path, response)
    logger.info(
        "Worker finished: operation=%s status=succeeded duration_ms=%d "
        "artifact_declarations=%d",
        operation, duration_ms, len(observed_declarations),
    )


def _validate_run_return(value: object) -> dict:
    """Validate the return value of a run function.

    Returns the dict if valid.  Raises ValueError on protocol violations.
    """
    if not isinstance(value, dict):
        raise ValueError(
            f"Run function returned {type(value).__name__}, "
            f"expected a dict"
        )

    # Check JSON-compatible (no NaN, Infinity, non-serializable types)
    import json as _json
    try:
        _json.dumps(value, allow_nan=False)
    except (ValueError, TypeError) as e:
        raise ValueError(
            f"Run function returned non-JSON-compatible value: {e}"
        ) from e

    # Check size
    result_json = _json.dumps(value, ensure_ascii=False, default=str)
    if len(result_json.encode("utf-8")) > MAX_RESULT_JSON_BYTES:
        raise ValueError(
            f"Run result exceeds {MAX_RESULT_JSON_BYTES} bytes"
        )

    return value


def _write_error_result(
    result_path: Path,
    started_at: str,
    task_id: str = "unknown",
    operation: str = "healthcheck",
    *,
    status: str = "crashed",
    message: str = "",
    error_type: str = "",
    error_message: str = "",
    error_traceback: str | None = None,
    redaction_values: set[str] | None = None,
) -> None:
    """Write an error result.json when the worker cannot proceed.

    Batch 3.1.1B: redaction_values, when provided, are applied to the
    error message before it is written to result.json.
    """
    rv: set[str] = redaction_values or set()
    final_error_message = error_message or message
    if rv:
        final_error_message = redact_text(final_error_message, rv)
        message = redact_text(message, rv)
        if error_traceback:
            error_traceback = redact_text(error_traceback, rv)

    response = {
        "protocol_version": 1,
        "task_id": task_id,
        "operation": operation,
        "success": False,
        "status": status,
        "message": message,
        "started_at": started_at,
        "finished_at": _now_iso(),
        "duration_ms": 0,
        "health": None,
        "result": None,
        "artifacts": [],
        "warnings": [],
        "error": {
            "error_type": error_type,
            "message": final_error_message,
            "traceback": error_traceback,
            "detail": {},
        },
    }
    try:
        _atomic_write_json(result_path, response)
    except Exception:
        logger.exception("Failed to write error result.json")
    logger.error("Worker error: status=%s message=%s", status, message)


def _redact_and_truncate_io(
    log_path: Path,
    max_bytes: int,
    redaction_values: set[str],
) -> None:
    """Apply redaction-before-truncation to a log file.

    Contract: final_output = truncate(redact(raw_output))

    Uses text-based I/O (read_text / write_text) to avoid the
    handle-close-then-reopen dance that can fail on Windows when
    the original stream handle hasn't been flushed to disk yet.

    If the log file does not exist or is empty, this is a no-op.
    """
    if not redaction_values:
        return
    if not log_path.is_file():
        return
    try:
        raw_text = log_path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return
    if not raw_text:
        return
    # Step 1: redact
    redacted = redact_text(raw_text, redaction_values)
    # Step 2: truncate to max_bytes on a UTF-8 boundary
    final_bytes = redacted.encode("utf-8")
    if len(final_bytes) > max_bytes:
        truncated = final_bytes[:max_bytes]
        for cut in range(len(truncated) - 1, max(0, len(truncated) - 4) - 1, -1):
            try:
                truncated[:cut].decode("utf-8")
                final_bytes = truncated[:cut]
                break
            except UnicodeDecodeError:
                continue
    # Step 3: persist the (possibly truncated) redacted content
    try:
        log_path.write_bytes(final_bytes)
    except OSError:
        pass


def _flush_and_redact_stdio(
    workspace_path: Path,
    redaction_values: set[str],
) -> None:
    """Flush, redact, and truncate stdout.log / stderr.log.

    Implements the contract: final_output = truncate(redact(raw_output)).
    Called after skill execution completes, before writing result.json.
    Closes the redirected handles so all buffered data reaches disk before
    we read the files back for processing.
    """
    # Close the redirected stdout/stderr handles to flush all buffers
    for handle_attr, log_name in (
        ("stdout", "stdout.log"),
        ("stderr", "stderr.log"),
    ):
        try:
            handle = getattr(sys, handle_attr)
            handle.flush()
        except (OSError, ValueError, AttributeError):
            pass
        try:
            handle = getattr(sys, handle_attr)
            handle.close()
        except (OSError, ValueError, AttributeError):
            pass
        # Restore a safe fallback so subsequent writes don't crash
        if handle_attr == "stdout":
            sys.stdout = sys.__stdout__
        else:
            sys.stderr = sys.__stderr__

    # Now read, redact, and truncate each log file
    _redact_and_truncate_io(
        workspace_path / "stdout.log", MAX_STDOUT_BYTES, redaction_values,
    )
    _redact_and_truncate_io(
        workspace_path / "stderr.log", MAX_STDERR_BYTES, redaction_values,
    )


def _verify_parent_components_worker(path: Path, root: Path) -> None:
    """Verify parent components from root to path are not symlink/reparse.

    Worker-side implementation — walks from output root to file's parent,
    checking each component via lstat.
    """
    import stat as _ws

    try:
        rel = path.relative_to(root)
    except ValueError:
        raise ValueError(f"Path not within root: {path}")
    parts = rel.parts
    current = root
    for part in parts[:-1]:  # skip the final filename
        current = current / part
        if not current.exists():
            break
        try:
            st = current.lstat()
            if _ws.S_ISLNK(st.st_mode):
                raise ValueError(
                    f"Parent component is a symlink: {current}"
                )
            if hasattr(st, "st_file_attributes"):
                FILE_ATTRIBUTE_REPARSE_POINT = 0x400
                if st.st_file_attributes & FILE_ATTRIBUTE_REPARSE_POINT:
                    raise ValueError(
                        f"Parent component is a reparse point: {current}"
                    )
        except (FileNotFoundError, NotADirectoryError):
            raise ValueError(f"Parent component invalid: {current}")


def _redirect_stdio(stdout_path: Path, stderr_path: Path) -> None:
    """Redirect stdout and stderr to workspace log files."""
    # Truncate mode: always start fresh
    sys.stdout = open(str(stdout_path), "w", encoding="utf-8", buffering=1)
    sys.stderr = open(str(stderr_path), "w", encoding="utf-8", buffering=1)


# ── CLI entry point ──


def main() -> None:
    """CLI entry point: python -I runtime_worker.py --request <path>"""
    parser = argparse.ArgumentParser(
        description="SkillRuntime worker — execute healthcheck in isolation"
    )
    parser.add_argument(
        "--request",
        required=True,
        help="Path to request.json file",
    )
    args = parser.parse_args()
    run_worker(args.request)


if __name__ == "__main__":
    main()
