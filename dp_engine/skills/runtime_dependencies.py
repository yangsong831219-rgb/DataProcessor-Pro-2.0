"""Dependency verification for skills using importlib.metadata ONLY.

Does NOT:
- Import dependency packages
- Call pip, conda, uv, or poetry
- Install any packages
- Spawn subprocesses

Only reads installed package metadata via importlib.metadata.
Version constraint parsing uses 'packaging' when available.
"""

from __future__ import annotations

import logging
import re
from importlib.metadata import PackageNotFoundError, distribution, distributions

from dp_engine.skills.runtime_models import (
    DependencyCheckItem,
    SkillDependencyReport,
)

logger = logging.getLogger(__name__)

# Regex: package name possibly followed by version spec
# Examples: "requests", "numpy>=1.21", "python-pptx>=0.6,<1.0"
_SPEC_RE = re.compile(
    r"^(?P<name>[a-zA-Z0-9]([a-zA-Z0-9._-]*[a-zA-Z0-9])?)"
    r"\s*(?P<specifier>.*)$"
)


def _parse_dependency_spec(dep_spec: str) -> tuple[str, str]:
    """Parse a dependency specifier into (name, specifier_str).

    Examples:
        "requests>=2.28" → ("requests", ">=2.28")
        "numpy" → ("numpy", "")
    """
    m = _SPEC_RE.match(dep_spec.strip())
    if not m:
        return dep_spec.strip(), ""
    return m.group("name"), m.group("specifier").strip()


def _get_installed_version(package_name: str) -> str | None:
    """Get the installed version of a package via importlib.metadata.

    Returns version string or None if not installed.
    Does NOT import the package.
    """
    try:
        dist = distribution(package_name)
        meta = dist.metadata
        # PackageMetadata is a dict-like mapping
        return meta["Version"] if "Version" in meta else None  # type: ignore[reportUnknownVariableType]
    except (PackageNotFoundError, KeyError):
        return None
    except Exception as e:
        logger.debug(
            "Error checking package '%s': %s", package_name, e
        )
        return None


def _check_version_satisfies(
    installed_version: str,
    specifier_str: str,
) -> bool:
    """Check if an installed version satisfies a PEP 440 specifier string.

    Uses 'packaging' library if available; falls back to simple check.
    """
    if not specifier_str:
        return True

    try:
        from packaging.specifiers import SpecifierSet
        spec_set = SpecifierSet(specifier_str)
        return spec_set.contains(installed_version)
    except ImportError:
        # Fallback: basic exact match for simple specifiers
        logger.debug(
            "packaging library not available; using basic version check"
        )
        return _basic_version_check(installed_version, specifier_str)
    except Exception as e:
        logger.warning(
            "Version specifier check failed for '%s': %s", specifier_str, e
        )
        return False


def _basic_version_check(version: str, specifier_str: str) -> bool:
    """Simplified fallback version check (best-effort, NOT PEP 440 compliant)."""
    import operator as _op

    # Simple patterns: >=1.0, ==1.0, >1.0, <1.0, <=1.0
    spec_map = {
        ">=": _op.ge, ">": _op.gt,
        "<=": _op.le, "<": _op.lt,
        "==": _op.eq, "!=": _op.ne,
    }
    try:
        from packaging.version import Version
        v = Version(version)
    except ImportError:
        # If even packaging.version is not available, skip
        return True

    parts = specifier_str.strip().split(",")
    for part in parts:
        part = part.strip()
        for op_str, op_func in spec_map.items():
            if part.startswith(op_str):
                spec_ver = Version(part[len(op_str):].strip())
                if not op_func(v, spec_ver):
                    return False
                break
    return True


class SkillDependencyChecker:
    """Check declared dependencies against installed packages.

    Uses importlib.metadata — no subprocess, no pip.
    """

    def check(
        self,
        skill_id: str,
        version: str,
        dependencies: tuple[str, ...],
    ) -> SkillDependencyReport:
        """Check all declared dependencies.

        Args:
            skill_id: Skill identifier (for report).
            version: Skill version (for report).
            dependencies: Tuple of dependency specifier strings.

        Returns:
            SkillDependencyReport with individual item results.
        """
        items: list[DependencyCheckItem] = []

        for dep_spec in dependencies:
            name, specifier_str = _parse_dependency_spec(dep_spec)
            installed = _get_installed_version(name)

            if installed is None:
                item = DependencyCheckItem(
                    name=name,
                    required_specifier=dep_spec,
                    installed_version=None,
                    satisfied=False,
                    reason=f"Package '{name}' is not installed",
                )
            elif not specifier_str:
                item = DependencyCheckItem(
                    name=name,
                    required_specifier=dep_spec,
                    installed_version=installed,
                    satisfied=True,
                    reason="",
                )
            elif _check_version_satisfies(installed, specifier_str):
                item = DependencyCheckItem(
                    name=name,
                    required_specifier=dep_spec,
                    installed_version=installed,
                    satisfied=True,
                    reason="",
                )
            else:
                item = DependencyCheckItem(
                    name=name,
                    required_specifier=dep_spec,
                    installed_version=installed,
                    satisfied=False,
                    reason=(
                        f"Version mismatch: installed={installed}, "
                        f"required={specifier_str}"
                    ),
                )
            items.append(item)

        all_satisfied = all(item.satisfied for item in items)

        logger.info(
            "Dependency check for %s@%s: %d/%d satisfied",
            skill_id, version,
            sum(1 for i in items if i.satisfied),
            len(items),
        )

        return SkillDependencyReport(
            skill_id=skill_id,
            version=version,
            items=tuple(items),
            all_satisfied=all_satisfied,
        )
