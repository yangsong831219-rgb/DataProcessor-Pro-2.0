"""Immutable domain models for the skill plugin system.

All models are frozen dataclasses — they carry data only.
No QWidget, QObject, thread, process, or callable references are allowed.

Models are designed for stable JSON round-trip serialization.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Literal
import re as _re_module

# ── Path security: forbidden patterns in entrypoint paths ──

# Characters forbidden in any entrypoint path segment
_FORBIDDEN_CHARS = frozenset({"\x00"})  # NUL byte

# Patterns that indicate an absolute Windows path (drive letter or UNC)
import string as _string
_WINDOWS_DRIVE_LETTERS = frozenset(_string.ascii_letters)
# UNC pattern: \\server\share\...
_UNC_PREFIXES = ("\\\\", "//")

# ── Enumerated types ──

# skill_type constrains what kind of skill this is.
# New types can be added here, but downstream code must handle unknown types
# gracefully (treat as opaque label, do not assume execution capability).
SkillType = Literal[
    "instruction",   # Pure markdown instruction skill (no code execution)
    "executable",    # Executable skill (Python script / entrypoint)
    "report_backend",  # Report generation backend (e.g., PPT Master)
]

# Health status for an installed skill record.
# - "unknown":             never checked
# - "unavailable":         essential files missing or cannot be read
# - "not_supported":       manifest has no healthcheck entrypoint
# - "checking":            healthcheck is currently in progress
# - "healthy":             all checks passed
# - "unhealthy":           checks failed (e.g., broken entrypoint, missing dependency)
# - "dependency_missing":  required dependencies not installed or version mismatch
# - "permission_denied":   requested capabilities not allowed
# - "timeout":             healthcheck exceeded time limit
# - "cancelled":           user cancelled the healthcheck
# - "crashed":             worker process crashed or non-zero exit
# - "protocol_error":      response validation failed (schema, paths, etc.)
HealthStatus = Literal[
    "unknown",
    "unavailable",
    "not_supported",
    "checking",
    "healthy",
    "unhealthy",
    "dependency_missing",
    "permission_denied",
    "timeout",
    "cancelled",
    "crashed",
    "protocol_error",
]

# Standard entrypoint key for healthcheck in Manifest.entrypoints dict.
HEALTHCHECK_ENTRYPOINT_KEY = "healthcheck"

# ── Validation helpers ──

_VERSION_RE = re.compile(r"^\d+\.\d+\.\d+$")  # semantic version: X.Y.Z
_SKILL_ID_RE = re.compile(r"^[a-z0-9]([a-z0-9._-]*[a-z0-9])?$")  # safe directory name


def _normalize_tuple(value: tuple[str, ...] | list[str] | set[str] | None) -> tuple[str, ...]:
    """Normalize and deduplicate a sequence of strings into a sorted tuple."""
    if value is None:
        return ()
    seen: set[str] = set()
    result: list[str] = []
    for item in value:
        item = item.strip()
        if item and item not in seen:
            seen.add(item)
            result.append(item)
    return tuple(sorted(result))


def _validate_version(version: str) -> str:
    """Validate semantic version format. Raises ValueError on failure."""
    version = version.strip()
    if not _VERSION_RE.match(version):
        raise ValueError(
            f"Invalid version format: '{version}'. Expected semantic version (e.g., 1.0.0)"
        )
    return version


def _validate_skill_id(skill_id: str) -> str:
    """Validate skill_id is a safe directory-name-compatible string."""
    skill_id = skill_id.strip()
    if not skill_id:
        raise ValueError("skill_id must not be empty")
    if not _SKILL_ID_RE.match(skill_id):
        raise ValueError(
            f"Invalid skill_id: '{skill_id}'. "
            f"Must match pattern: lowercase letters, digits, dots, hyphens, underscores"
        )
    if len(skill_id) > 128:
        raise ValueError(f"skill_id too long: {len(skill_id)} > 128 characters")
    return skill_id


def _validate_entrypoints(entrypoints: dict[str, str]) -> dict[str, str]:
    """Validate entrypoint paths for security.

    Rules (checked here, at the string level):
    - Non-empty
    - No NUL characters
    - No POSIX absolute paths (starts with /)
    - No Windows drive-letter paths (C:\\..., D:/...)
    - No UNC paths (\\\\server\\share\\...)
    - No parent-directory traversal (..)
    - Mixed-separator traversal caught by normalizing to / then checking ..

    Resolution-based checks (symlinks, real relative_to) are done in
    manifest_parser.parse_skill_manifest() after resolving against skill_root.
    """
    cleaned: dict[str, str] = {}
    for key, path in entrypoints.items():
        path = path.strip()
        if not path:
            raise ValueError(f"Entrypoint '{key}' has empty path")

        # Reject NUL characters (path traversal via null-byte truncation)
        if any(c in _FORBIDDEN_CHARS for c in path):
            raise ValueError(
                f"Entrypoint '{key}' contains forbidden characters"
            )

        # Reject POSIX absolute paths
        if path.startswith("/"):
            raise ValueError(
                f"Entrypoint '{key}' has absolute path: '{path}'"
            )

        # Reject Windows drive-letter paths (C:\\... or C:/...)
        if len(path) >= 2 and path[1] == ":" and path[0] in _WINDOWS_DRIVE_LETTERS:
            raise ValueError(
                f"Entrypoint '{key}' has Windows drive-letter path: '{path}'"
            )

        # Reject UNC paths (\\\\server\\share\\... or //server/share/...)
        if path.startswith(_UNC_PREFIXES):
            raise ValueError(
                f"Entrypoint '{key}' has UNC path: '{path}'"
            )

        # Normalize to forward-slash and check for parent-directory traversal.
        # This catches: ../outside, ..\\outside, foo/../../outside,
        # and mixed-separator escapes like workflows/..\\../outside
        normalized = path.replace("\\", "/")
        segments = normalized.split("/")
        if ".." in segments:
            raise ValueError(
                f"Entrypoint '{key}' contains '..' traversal: '{path}'"
            )
        # Also reject "." as a path (self-reference, ambiguous)
        if normalized == "." or segments == ["."]:
            raise ValueError(
                f"Entrypoint '{key}' must not be '.'"
            )

        cleaned[key] = path
    return cleaned


# Regular expression for validating Python identifier (function names).
_PY_IDENTIFIER_RE = _re_module.compile(r"^[a-zA-Z_][a-zA-Z0-9_]*$")


def parse_entrypoint_ref(ep_ref: str) -> tuple[str, str]:
    """Parse a 'module/path.py:function_name' entrypoint reference.

    Returns (module_path, function_name) if valid.
    The module_path is the relative file path before the last colon.
    The function_name is the Python identifier after the last colon.

    Raises ValueError if:
    - ref is empty
    - ref contains no colon
    - function_name is not a valid Python identifier
    - module_path is empty
    """
    ep_ref = ep_ref.strip()
    if not ep_ref:
        raise ValueError("Entrypoint reference is empty")

    # Split on LAST colon only (module paths don't contain colons on Windows,
    # and we restrict module_path to not use colons anyway).
    if ":" not in ep_ref:
        raise ValueError(
            f"Entrypoint reference missing colon separator: '{ep_ref}'. "
            f"Expected format: 'relative/path/to/module.py:function_name'"
        )

    idx = ep_ref.rindex(":")
    module_path = ep_ref[:idx].strip()
    function_name = ep_ref[idx + 1:].strip()

    if not module_path:
        raise ValueError(
            f"Entrypoint reference has empty module path: '{ep_ref}'"
        )

    if not function_name:
        raise ValueError(
            f"Entrypoint reference has empty function name: '{ep_ref}'"
        )

    if not _PY_IDENTIFIER_RE.match(function_name):
        raise ValueError(
            f"Entrypoint function name is not a valid Python identifier: "
            f"'{function_name}'"
        )

    return module_path, function_name


# ── Domain models ──

@dataclass(frozen=True)
class SkillManifest:
    """Immutable metadata for a single skill, parsed from SKILL.md Front Matter.

    This is the canonical description of what a skill IS and what it requires.
    It does NOT represent runtime state (see InstalledSkill for that).
    """

    schema_version: int
    skill_id: str
    name: str
    version: str
    description: str
    skill_type: str  # SkillType literal value
    artifact_types: tuple[str, ...] = field(default_factory=tuple)
    capabilities: tuple[str, ...] = field(default_factory=tuple)
    dependencies: tuple[str, ...] = field(default_factory=tuple)
    entrypoints: dict[str, str] = field(default_factory=dict)
    source_url: str | None = None
    min_app_version: str | None = None
    # Forward-compatible extension: unknown fields from Front Matter
    extensions: dict[str, object] = field(default_factory=dict, repr=False)

    def __post_init__(self) -> None:
        """Validate fields after construction."""
        _validate_skill_id(self.skill_id)
        _validate_version(self.version)
        if not self.name.strip():
            raise ValueError("name must not be empty")
        if not self.description.strip():
            raise ValueError("description must not be empty")
        if self.skill_type not in ("instruction", "executable", "report_backend"):
            # Allow unknown types for forward-compat but log a warning at parse time
            pass
        _validate_entrypoints(dict(self.entrypoints))

    def to_dict(self) -> dict:
        """Serialize to a JSON-compatible dictionary."""
        d: dict = {
            "schema_version": self.schema_version,
            "skill_id": self.skill_id,
            "name": self.name,
            "version": self.version,
            "description": self.description,
            "skill_type": self.skill_type,
            "artifact_types": list(self.artifact_types),
            "capabilities": list(self.capabilities),
            "dependencies": list(self.dependencies),
            "entrypoints": dict(self.entrypoints),
            "source_url": self.source_url,
            "min_app_version": self.min_app_version,
        }
        if self.extensions:
            d["extensions"] = dict(self.extensions)
        return d

    @classmethod
    def from_dict(cls, d: dict) -> SkillManifest:
        """Deserialize from a JSON-compatible dictionary."""
        return cls(
            schema_version=int(d["schema_version"]),
            skill_id=str(d["skill_id"]),
            name=str(d["name"]),
            version=str(d["version"]),
            description=str(d["description"]),
            skill_type=str(d["skill_type"]),
            artifact_types=_normalize_tuple(d.get("artifact_types", [])),
            capabilities=_normalize_tuple(d.get("capabilities", [])),
            dependencies=_normalize_tuple(d.get("dependencies", [])),
            entrypoints=dict(d.get("entrypoints", {})),
            source_url=d.get("source_url"),
            min_app_version=d.get("min_app_version"),
            extensions=dict(d.get("extensions", {})),
        )


@dataclass(frozen=True)
class InstalledSkill:
    """Installation record for a skill in the local registry.

    An InstalledSkill pairs a SkillManifest with local installation metadata.
    It does NOT provide execution capability — that belongs to SkillRuntime (future).
    """

    manifest: SkillManifest
    install_path: str
    enabled: bool = True
    installed_at: str = ""  # ISO 8601 UTC
    source_revision: str | None = None
    checksum: str | None = None
    health_status: str = "unknown"  # HealthStatus literal value
    health_message: str | None = None

    # -- Health status values for InstalledSkill validation --
    # Keep in sync with HealthStatus Literal above.
    _VALID_HEALTH_STATUSES: frozenset[str] = frozenset({
        "unknown", "unavailable", "not_supported", "checking",
        "healthy", "unhealthy", "dependency_missing",
        "permission_denied", "timeout", "cancelled", "crashed",
        "protocol_error",
    })

    def __post_init__(self) -> None:
        if self.health_status not in self._VALID_HEALTH_STATUSES:
            raise ValueError(
                f"Invalid health_status: '{self.health_status}'. "
                f"Must be one of: {sorted(self._VALID_HEALTH_STATUSES)}"
            )

    @property
    def skill_id(self) -> str:
        """Convenience accessor for manifest.skill_id."""
        return self.manifest.skill_id

    @property
    def version(self) -> str:
        """Convenience accessor for manifest.version."""
        return self.manifest.version

    def to_dict(self) -> dict:
        """Serialize to a JSON-compatible dictionary."""
        return {
            "manifest": self.manifest.to_dict(),
            "install_path": self.install_path,
            "enabled": self.enabled,
            "installed_at": self.installed_at,
            "source_revision": self.source_revision,
            "checksum": self.checksum,
            "health_status": self.health_status,
            "health_message": self.health_message,
        }

    @classmethod
    def from_dict(cls, d: dict) -> InstalledSkill:
        """Deserialize from a JSON-compatible dictionary."""
        return cls(
            manifest=SkillManifest.from_dict(d["manifest"]),
            install_path=str(d.get("install_path", "")),
            enabled=bool(d.get("enabled", True)),
            installed_at=str(d.get("installed_at", "")),
            source_revision=d.get("source_revision"),
            checksum=d.get("checksum"),
            health_status=str(d.get("health_status", "unknown")),
            health_message=d.get("health_message"),
        )


# ── Source inspection model ──


@dataclass(frozen=True)
class SkillSourceInspectionResult:
    """Result of inspecting a remote skill source (e.g., GitHub repository).

    This model uses layered boolean fields instead of a single vague 'success'
    flag.  Each field captures a specific inspection stage.  The UI MUST check
    individual fields — never assume that one True implies any other.

    Inspection stages (in order):
      1. source_reachable   — network connection succeeded, HTTP response received
      2. skill_md_found     — the URL returned HTTP 200 (not 404)
      3. content_nonempty   — response body is non-empty after stripping whitespace
      4. front_matter_detected — content starts with YAML Front Matter delimiter (---)
      5. metadata_parseable — Front Matter is valid YAML and all required fields present

    A result is only meaningful for a LOCAL skill directory when ALL five fields
    are True AND the caller has also run parse_skill_manifest() successfully.
    """

    source_reachable: bool
    skill_md_found: bool
    content_nonempty: bool
    front_matter_detected: bool
    metadata_parseable: bool
    source_url: str
    repository_name: str | None = None
    message: str = ""
    warnings: tuple[str, ...] = ()
    error_code: str | None = None
    # Raw metadata extracted from Front Matter (only populated when
    # metadata_parseable=True).  Keys are the YAML top-level keys.
    raw_metadata_keys: tuple[str, ...] = ()

    @property
    def is_viable_skill_source(self) -> bool:
        """Convenience: all five stages passed.

        Note: this does NOT mean the skill is installed, loaded, or runnable.
        It only means a valid-looking SKILL.md was found at the source URL.
        """
        return (
            self.source_reachable
            and self.skill_md_found
            and self.content_nonempty
            and self.front_matter_detected
            and self.metadata_parseable
        )
