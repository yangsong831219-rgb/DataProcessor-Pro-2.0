"""Skill management package — domain models, manifest parser, and registry.

This package provides the data layer for the skill plugin system.
It does NOT include runtime execution, installation, or process management.

Modules:
    models.py              — Immutable domain models
    errors.py              — Typed exception hierarchy
    manifest_parser.py     — SKILL.md Front Matter parser
    registry.py            — Local skill registry with JSON persistence
    package_models.py      — Package source, limits, paths, install models (Batch 2)
    archive_utils.py       — Safe ZIP extraction and directory copy (Batch 2)
    package_validator.py   — Skill package integrity validation (Batch 2)
    installer.py           — Atomic install/uninstall with staging (Batch 2)
    install_service.py     — Orchestration between sources and installer (Batch 2)
    install_events.py      — Install event logging (Batch 2)
"""

# Re-export key symbols for convenience
from dp_engine.skills.models import (   # noqa: F401
    SkillManifest,
    InstalledSkill,
    SkillSourceInspectionResult,
    SkillType,
    HealthStatus,
    HEALTHCHECK_ENTRYPOINT_KEY,
    parse_entrypoint_ref,
)
from dp_engine.skills.errors import (   # noqa: F401
    SkillError,
    SkillManifestError,
    SkillRegistryError,
    SkillSourceError,
    SkillValidationError,
    SkillPackageError,
    SkillArchiveError,
    SkillPackageLimitError,
    SkillPackageSecurityError,
    SkillInstallError,
    SkillInstallConflictError,
    SkillInstallCancelled,
    SkillRollbackError,
    SkillUninstallError,
)
from dp_engine.skills.manifest_parser import (  # noqa: F401
    parse_skill_manifest,
    parse_skill_front_matter,
    ParsedSkillFrontMatter,
)
from dp_engine.skills.registry import SkillRegistry  # noqa: F401
