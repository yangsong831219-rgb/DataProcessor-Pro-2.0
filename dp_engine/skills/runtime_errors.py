"""Typed exception hierarchy for SkillRuntime.

All runtime errors inherit from SkillRuntimeError so callers can
catch a single base type.  Each subclass carries structured context
(detail dict) for UI rendering and diagnostics.
"""

from __future__ import annotations

from dp_engine.skills.errors import SkillError


class SkillRuntimeError(SkillError):
    """Base exception for all SkillRuntime-related errors."""


class RuntimeProtocolError(SkillRuntimeError):
    """Request or response failed protocol validation.

    Raised when:
    - protocol_version mismatch
    - task_id mismatch between request and response
    - operation mismatch
    - artifact path escape attempt
    - response schema validation fails
    - result.json missing, empty, or oversized
    """


class RuntimeTimeoutError(SkillRuntimeError):
    """Healthcheck exceeded the configured timeout.

    The detail dict includes 'timeout_seconds' and 'duration_ms'.
    """


class RuntimeCancelledError(SkillRuntimeError):
    """Healthcheck was cancelled by user before completion."""


class RuntimeWorkerCrashedError(SkillRuntimeError):
    """Worker process exited with non-zero code or was killed.

    The detail dict includes 'exit_code' (or -1 if killed by signal)
    and optional 'stderr_tail'.
    """


class RuntimePermissionError(SkillRuntimeError):
    """Requested capability was denied or worker attempted blocked action.

    Raised when:
    - Skill requests a capability that is not in the allowed set
    - Worker audit hook blocked a forbidden operation
    """


class RuntimeDependencyError(SkillRuntimeError):
    """Required dependencies are missing or version mismatch.

    The detail dict includes the SkillDependencyReport as 'dependency_report'.
    """


class RuntimeEntrypointError(SkillRuntimeError):
    """Healthcheck entrypoint cannot be loaded or executed.

    Raised when:
    - entrypoint function not found
    - entrypoint function not callable
    - entrypoint function raises an exception
    - entrypoint function returns invalid result
    """


class RuntimeWorkspaceError(SkillRuntimeError):
    """Workspace creation or cleanup failed.

    Raised when:
    - workspace directory cannot be created
    - disk is full
    - permission denied
    """
