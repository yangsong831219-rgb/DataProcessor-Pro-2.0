"""Typed exception hierarchy for the skill management system.

All skill-related errors inherit from SkillError.
UI-facing messages should be derived from the exception message,
but the original exception must never be swallowed silently.
"""

from __future__ import annotations


class SkillError(Exception):
    """Base exception for all skill-related errors."""

    def __init__(self, message: str, *, detail: dict | None = None) -> None:
        super().__init__(message)
        self.detail = detail or {}


class SkillManifestError(SkillError):
    """Invalid or missing SKILL.md manifest.

    Raised when:
    - SKILL.md is missing from a skill directory
    - Required Front Matter fields are absent
    - Field values fail validation (e.g., bad version format)
    - Entrypoint paths violate security constraints
    """


class SkillRegistryError(SkillError):
    """Registry persistence or integrity error.

    Raised when:
    - Registry JSON is corrupted and cannot be loaded
    - Atomic save fails (e.g., disk full, permission denied)
    - A duplicate registration is attempted without explicit replace
    """


class SkillSourceError(SkillError):
    """Error accessing a skill source (e.g., GitHub repository).

    Raised when:
    - GitHub API returns an error (404, 403, 5xx)
    - Network timeout or connection failure
    - The source does not contain a recognizable skill
    """


class SkillValidationError(SkillError):
    """Runtime validation error for skill data that passes manifest parsing
    but fails higher-level business rules.

    Raised when:
    - A skill_id does not match the expected naming convention
    - Cross-field consistency checks fail (e.g., skill_type vs capabilities)
    """


# ── Batch 2: Package, Install, Uninstall errors ──


class SkillPackageError(SkillError):
    """Base for skill package validation errors."""


class SkillArchiveError(SkillPackageError):
    """ZIP archive is malformed, encrypted, or contains path escapes.

    Raised when:
    - ZIP cannot be opened
    - ZIP contains path traversal entries
    - ZIP is encrypted
    - ZIP contains symlinks or special files (when policy rejects them)
    """


class SkillPackageLimitError(SkillPackageError):
    """A package resource limit was exceeded.

    Raised when:
    - Archive file too large
    - Extracted size exceeds limit
    - Too many files
    - Directory depth exceeds limit
    - Compression ratio exceeds limit

    The detail dict contains the specific limit that was violated.
    """


class SkillPackageSecurityError(SkillPackageError):
    """A security violation was detected in the package.

    Raised when:
    - ZIP slip path traversal detected
    - Symlink points outside package root
    - Suspicious file types found (device files, FIFOs)
    - Path collision or case-conflict detected
    """


class SkillInstallError(SkillError):
    """Base for install-related errors."""


class SkillInstallConflictError(SkillInstallError):
    """Target version already installed and replace_same_version=False.

    Raised when attempting to install a version that already exists.
    """


class SkillInstallCancelled(SkillInstallError):
    """Install was cancelled by user before completion."""


class SkillRollbackError(SkillError):
    """Rollback after a failed install also failed.

    This is a high-severity error — the system may be in an inconsistent
    state.  The detail dict contains the original error and the rollback
    error.
    """


class SkillUninstallError(SkillError):
    """Base for uninstall-related errors.

    Raised when:
    - Target version not found
    - install_path is outside the managed directory
    - Registry save fails during uninstall
    - Directory removal fails
    """
