"""SKILL.md manifest parser.

Parses a YAML Front Matter block from a SKILL.md file and validates it
through SkillManifest.  This module only does LOCAL file reading — it does
NOT download, install, or execute anything.

This module is the SINGLE SOURCE OF TRUTH for Front Matter parsing rules.
Both local parse_skill_manifest() and remote inspect_github_skill_source()
use parse_skill_front_matter() defined here.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

import yaml

from dp_engine.skills.errors import SkillManifestError
from dp_engine.skills.models import SkillManifest, _normalize_tuple

logger = logging.getLogger(__name__)

# ── Front Matter delimiters ──
_FRONT_MATTER_START = "---\n"
_FRONT_MATTER_END = "\n---"

# ── Required fields (must be present in Front Matter) ──
# ★ SINGLE SOURCE OF TRUTH — do not copy this list elsewhere.
_REQUIRED_FIELDS = frozenset({
    "schema_version",
    "skill_id",
    "name",
    "version",
    "skill_type",
    "capabilities",
    "entrypoints",
})

# ── Optional fields (parsed if present, no error if absent) ──
_OPTIONAL_FIELDS = frozenset({
    "description",
    "artifact_types",
    "dependencies",
    "source_url",
    "min_app_version",
})

# All recognized fields (required + optional)
_KNOWN_FIELDS = _REQUIRED_FIELDS | _OPTIONAL_FIELDS

# Known typed-extension keys remain in SkillManifest.extensions, but should
# not be reported as unknown when they belong to a report_backend manifest.
_REPORT_BACKEND_EXTENSION_FIELDS = frozenset({
    "report_backend_contract",
    "template_modes",
})


# ── Type-narrowing helpers for Front Matter fields ──
# These functions validate and narrow `object` values from the parsed
# YAML dict into concrete types.  Each function raises SkillManifestError
# with a descriptive message on type mismatch — no silent coercion.


def _require_string(
    metadata: Mapping[str, object],
    key: str,
) -> str:
    """Extract a required non-empty string from Front Matter metadata.

    Raises SkillManifestError if the key is missing, the value is not a
    string, or the value is whitespace-only.
    """
    value = metadata.get(key)
    if not isinstance(value, str) or not value.strip():
        raise SkillManifestError(
            f"Field '{key}' must be a non-empty string, "
            f"got {type(value).__name__}: {value!r}"
        )
    return value.strip()


def _require_optional_string(
    metadata: Mapping[str, object],
    key: str,
) -> str | None:
    """Extract an optional string from Front Matter metadata.

    Returns None if the key is absent or the value is None / empty.
    Raises SkillManifestError if the value is present but not a string.
    """
    value = metadata.get(key)
    if value is None:
        return None
    if not isinstance(value, str):
        raise SkillManifestError(
            f"Field '{key}' must be a string if present, "
            f"got {type(value).__name__}: {value!r}"
        )
    stripped = value.strip()
    return stripped if stripped else None


def _require_string_list(
    metadata: Mapping[str, object],
    key: str,
) -> tuple[str, ...]:
    """Extract a list of non-empty strings from Front Matter metadata.

    Returns an empty tuple if the key is absent.  Raises SkillManifestError
    if the value is not a list, or any element is not a string.
    """
    value = metadata.get(key)
    if value is None:
        return ()
    if not isinstance(value, (list, tuple)):
        raise SkillManifestError(
            f"Field '{key}' must be a list of strings, "
            f"got {type(value).__name__}: {value!r}"
        )
    result: list[str] = []
    for i, item in enumerate(value):
        if not isinstance(item, str):
            raise SkillManifestError(
                f"Field '{key}[{i}]' must be a string, "
                f"got {type(item).__name__}: {item!r}"
            )
        stripped = item.strip()
        if stripped:
            result.append(stripped)
    return _normalize_tuple(result)


def _require_string_mapping(
    metadata: Mapping[str, object],
    key: str,
) -> dict[str, str]:
    """Extract a str→str mapping from Front Matter metadata.

    Returns an empty dict if the key is absent.  Raises SkillManifestError
    if the value is not a dict, or any key/value is not a string.
    """
    value = metadata.get(key)
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise SkillManifestError(
            f"Field '{key}' must be a mapping, "
            f"got {type(value).__name__}: {value!r}"
        )
    result: dict[str, str] = {}
    for k, v in value.items():
        if not isinstance(k, str):
            raise SkillManifestError(
                f"Field '{key}' keys must be strings, "
                f"got {type(k).__name__}: {k!r}"
            )
        if not isinstance(v, str):
            raise SkillManifestError(
                f"Field '{key}.{k}' must be a string, "
                f"got {type(v).__name__}: {v!r}"
            )
        result[str(k)] = str(v)
    return result


def _require_schema_version(
    metadata: Mapping[str, object],
) -> int:
    """Extract and validate schema_version from Front Matter metadata.

    schema_version must be a positive integer.  Booleans are explicitly
    rejected (bool is a subclass of int in Python).
    Raises SkillManifestError on type mismatch or invalid value.
    """
    value = metadata.get("schema_version")
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise SkillManifestError(
            f"schema_version must be a positive integer, "
            f"got {type(value).__name__}: {value!r}"
        )
    v = int(value)
    if v <= 0:
        raise SkillManifestError(
            f"schema_version must be a positive integer, got {v}"
        )
    return v


# ── Shared Front Matter result ──


@dataclass(frozen=True)
class ParsedSkillFrontMatter:
    """Result of parsing a SKILL.md Front Matter block.

    This is a PURE DATA object — no filesystem access, no entrypoint
    validation, no code execution.

    Fields:
        metadata: The parsed YAML dict.
        warnings: Non-fatal warnings (e.g., unknown fields).
    """

    metadata: dict[str, object]
    warnings: tuple[str, ...] = ()


# ── Shared Front Matter parser ──


def parse_skill_front_matter(
    text: str,
    *,
    source_label: str = "<string>",
) -> ParsedSkillFrontMatter:
    """Parse a SKILL.md Front Matter block from raw text.

    This is the SINGLE canonical Front Matter parser.  Both local
    parse_skill_manifest() and remote inspect_github_skill_source()
    MUST use this function — do not duplicate the parsing logic.

    Checks performed:
    1. Text starts with '---\\n' (Front Matter delimiter).
    2. Closing '\\n---' delimiter exists.
    3. YAML block is valid YAML.
    4. Root node is a mapping (dict).
    5. All required fields are present.
    6. Field types are valid (schema_version is int, capabilities is list[str],
       entrypoints is str→str mapping, etc.) via centralized type-narrowing
       helpers.

    This function does NOT:
    - Access the filesystem.
    - Validate entrypoint paths.
    - Check that entrypoint files exist.
    - Execute any code.

    Args:
        text: Raw file content as string.
        source_label: Human-readable label for error messages
                      (e.g., file path or URL).

    Returns:
        ParsedSkillFrontMatter with metadata dict and warnings tuple.

    Raises:
        SkillManifestError: If Front Matter is missing, invalid YAML,
                            not a mapping, missing required fields,
                            or any field has an invalid type.
    """
    warnings: list[str] = []

    # Check 1: starts with Front Matter delimiter
    if not text.startswith(_FRONT_MATTER_START):
        raise SkillManifestError(
            f"SKILL.md missing YAML Front Matter: {source_label}. "
            f"File must start with '---\\n' followed by YAML metadata."
        )

    # Check 2: find closing delimiter
    end_idx = text.find(_FRONT_MATTER_END, len(_FRONT_MATTER_START))
    if end_idx == -1:
        raise SkillManifestError(
            f"SKILL.md has unclosed Front Matter: {source_label}. "
            f"Expected closing '---' after YAML block."
        )

    yaml_block = text[len(_FRONT_MATTER_START):end_idx]

    # Check 3: valid YAML
    try:
        parsed = yaml.safe_load(yaml_block)
    except yaml.YAMLError as e:
        raise SkillManifestError(
            f"SKILL.md Front Matter is not valid YAML: {source_label}\n{e}"
        ) from e

    # Check 4: root must be a mapping
    if not isinstance(parsed, dict):
        raise SkillManifestError(
            f"SKILL.md Front Matter must be a YAML mapping, "
            f"got {type(parsed).__name__}: {source_label}"
        )

    # Check 5: required fields present
    missing = [f for f in _REQUIRED_FIELDS if f not in parsed]
    if missing:
        raise SkillManifestError(
            f"SKILL.md missing required fields: {', '.join(missing)}. "
            f"Source: {source_label}"
        )

    # Check 6: validate field types via centralized type-narrowing helpers.
    # This catches bool-as-int, list-vs-string, dict-vs-string, etc.
    # at the SSOT parser level so both local and remote callers get
    # consistent type validation.
    _require_schema_version(parsed)
    _require_string(parsed, "skill_id")
    _require_string(parsed, "name")
    _require_string(parsed, "version")
    _require_string(parsed, "skill_type")
    _require_string_list(parsed, "capabilities")
    _require_string_mapping(parsed, "entrypoints")
    # Optional fields — validate if present
    if "description" in parsed:
        _require_optional_string(parsed, "description")
    if "artifact_types" in parsed:
        _require_string_list(parsed, "artifact_types")
    if "dependencies" in parsed:
        _require_string_list(parsed, "dependencies")
    if "source_url" in parsed:
        _require_optional_string(parsed, "source_url")
    if "min_app_version" in parsed:
        _require_optional_string(parsed, "min_app_version")

    # Collect unknown fields as warnings
    known_extensions = (
        _REPORT_BACKEND_EXTENSION_FIELDS
        if parsed.get("skill_type") == "report_backend"
        else frozenset()
    )
    unknown = [
        k for k in parsed
        if k not in _KNOWN_FIELDS and k not in known_extensions
    ]
    if unknown:
        warnings.append(
            f"Unknown fields in Front Matter: {', '.join(sorted(unknown))}"
        )

    return ParsedSkillFrontMatter(
        metadata=dict(parsed),
        warnings=tuple(warnings),
    )


# ── Internal helpers (delegate to shared parser) ──


def _extract_front_matter(content: str, source_path: Path) -> dict:
    """Extract YAML Front Matter from SKILL.md content.

    Delegates to parse_skill_front_matter() — the SSOT parser.

    Args:
        content: Raw file content as string.
        source_path: Path to SKILL.md (for error messages).

    Returns:
        Parsed Front Matter dict.

    Raises:
        SkillManifestError: If Front Matter is missing or invalid YAML.
    """
    result = parse_skill_front_matter(content, source_label=str(source_path))
    if result.warnings:
        for w in result.warnings:
            logger.warning("SKILL.md at %s: %s", source_path, w)
    return result.metadata


def _check_missing_fields(front_matter: dict, source_path: Path) -> list[str]:
    """Check for missing required fields. Returns list of missing field names."""
    missing = [f for f in _REQUIRED_FIELDS if f not in front_matter]
    return missing


def _collect_unknown_fields(front_matter: dict) -> dict:
    """Collect unknown fields for forward-compat extension storage."""
    return {k: v for k, v in front_matter.items() if k not in _KNOWN_FIELDS}


# ── Public: parse skill manifest from local directory ──


def parse_skill_manifest(skill_root: Path) -> SkillManifest:
    """Parse a SKILL.md file from a skill directory and return a validated SkillManifest.

    This function:
    1. Reads SKILL.md from skill_root
    2. Extracts YAML Front Matter via parse_skill_front_matter() (SSOT)
    3. Validates required fields
    4. Validates entrypoint path security
    5. Constructs and returns a SkillManifest

    No code from the SKILL.md body or entrypoints is executed.

    Args:
        skill_root: Path to the skill directory containing SKILL.md.

    Returns:
        A validated, immutable SkillManifest.

    Raises:
        SkillManifestError: If SKILL.md is missing, Front Matter is invalid,
                            required fields are absent, or entrypoints fail
                            security validation.
        FileNotFoundError: If skill_root does not exist.
    """
    skill_root = skill_root.resolve()
    skill_md_path = skill_root / "SKILL.md"

    if not skill_md_path.is_file():
        raise SkillManifestError(
            f"SKILL.md not found in skill directory: {skill_root}. "
            f"Expected file at: {skill_md_path}"
        )

    # Read file
    try:
        content = skill_md_path.read_text(encoding="utf-8")
    except UnicodeDecodeError as e:
        raise SkillManifestError(
            f"SKILL.md is not valid UTF-8: {skill_md_path}"
        ) from e

    # Parse Front Matter via SSOT parser
    parsed = parse_skill_front_matter(content, source_label=str(skill_md_path))
    front_matter = parsed.metadata

    if parsed.warnings:
        for w in parsed.warnings:
            logger.warning("SKILL.md at %s: %s", skill_md_path, w)

    # Collect unknown fields for forward-compat
    extensions = _collect_unknown_fields(front_matter)

    # ── Build SkillManifest ──
    # All value extraction goes through type-narrowing helpers that
    # validate and narrow `object` → concrete types.  This eliminates
    # the Pyright reportArgumentType warnings on lines 260-272 (prior).
    #
    # Validation order matters: schema_version first (reject bool-as-int),
    # then string fields, then list fields, then mappings.
    try:
        manifest = SkillManifest(
            schema_version=_require_schema_version(front_matter),
            skill_id=_require_string(front_matter, "skill_id"),
            name=_require_string(front_matter, "name"),
            version=_require_string(front_matter, "version"),
            description=_require_optional_string(front_matter, "description") or "",
            skill_type=_require_string(front_matter, "skill_type"),
            artifact_types=_require_string_list(front_matter, "artifact_types"),
            capabilities=_require_string_list(front_matter, "capabilities"),
            dependencies=_require_string_list(front_matter, "dependencies"),
            entrypoints=_require_string_mapping(front_matter, "entrypoints"),
            source_url=_require_optional_string(front_matter, "source_url"),
            min_app_version=_require_optional_string(
                front_matter, "min_app_version"
            ),
            extensions=extensions,
        )
    except (ValueError, TypeError) as e:
        raise SkillManifestError(
            f"SKILL.md Front Matter validation failed: {skill_md_path}\n{e}"
        ) from e

    # ── Validate entrypoint paths are within skill_root ──
    # skill_root is already resolved (line 127).  All comparisons use the
    # resolved root to catch symlink escapes.
    #
    # Entrypoint values may be either:
    #   - "path/to/module.py"  (simple file path)
    #   - "path/to/module.py:function_name"  (module:function format)
    # For filesystem checks, only the module path portion is used.
    for key, rel_path in manifest.entrypoints.items():
        # Extract the module file path (before any colon for function name)
        if ":" in rel_path:
            from dp_engine.skills.models import parse_entrypoint_ref
            module_path_str, _func_name = parse_entrypoint_ref(rel_path)
        else:
            module_path_str = rel_path

        # Build the unresolved candidate path
        candidate = skill_root / module_path_str

        # Resolve the full path (follows symlinks, normalizes separators,
        # eliminates .. components).  This is the ground-truth filesystem
        # location.
        try:
            resolved = candidate.resolve()
        except OSError as e:
            raise SkillManifestError(
                f"Entrypoint '{key}' cannot be resolved: '{module_path_str}'. "
                f"OS error: {e}"
            ) from e

        # Check 1: resolved path must be within the resolved skill_root.
        try:
            resolved.relative_to(skill_root)
        except ValueError:
            # Provide detailed diagnostics for security audit
            raise SkillManifestError(
                f"Entrypoint '{key}' resolves outside skill_root: "
                f"'{module_path_str}' → '{resolved}'. "
                f"skill_root (resolved): '{skill_root}'. "
                f"All entrypoints must be within the skill directory. "
                f"This may indicate a symlink pointing outside the root, "
                f"or a path-traversal attack."
            )

        # Check 2: resolved path must be a regular file (not a directory,
        # not a device, not a broken symlink).
        if resolved.is_dir():
            raise SkillManifestError(
                f"Entrypoint '{key}' is a directory, not a file: "
                f"'{module_path_str}' → '{resolved}'."
            )
        if not resolved.is_file():
            raise SkillManifestError(
                f"Entrypoint '{key}' file not found or not a regular file: "
                f"'{module_path_str}'. Expected at: {resolved}"
            )

        # Check 3: if the path before resolution differs from after
        # resolution, it may be a symlink.  Log a warning but allow it
        # (as long as check 1 passed — it's still within root).
        if candidate != resolved and resolved.is_file():
            logger.info(
                "Entrypoint '%s' is a symlink: '%s' → '%s' (within root, allowed).",
                key, candidate, resolved,
            )

    return manifest
