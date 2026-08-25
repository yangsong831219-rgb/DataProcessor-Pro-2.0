"""Skill install service — orchestration between sources and installer.

SkillInstallService coordinates:
- GitHub archive download
- Local directory/ZIP sourcing
- SkillInstaller invocation
- Registry interaction
- Install logging

It does NOT:
- Execute skill code
- Install dependencies
- Run health checks
"""

from __future__ import annotations

import hashlib
import logging
import re
from pathlib import Path
from typing import Callable

from dp_engine.skills.errors import (
    SkillInstallError,
    SkillSourceError,
)
from dp_engine.skills.install_events import (
    InstallLogEntry,
    InstallLogger,
    sanitize_location,
)
from dp_engine.skills.installer import SkillInstaller
from dp_engine.skills.package_models import (
    SkillInstallRequest,
    SkillInstallResult,
    SkillPackageLimits,
    SkillPackageSource,
    SkillPackageSourceType,
    SkillPaths,
    get_default_limits,
)
from dp_engine.skills.registry import SkillRegistry

logger = logging.getLogger(__name__)

# ── GitHub URL validation ──

_GITHUB_ARCHIVE_RE = re.compile(
    r"^https://github\.com/([a-zA-Z0-9]([a-zA-Z0-9._-]*[a-zA-Z0-9])?)"
    r"/([a-zA-Z0-9]([a-zA-Z0-9._-]*[a-zA-Z0-9])?)"
    r"/archive/([^/]+)\.zip$"
)

_ALLOWED_DOMAINS = frozenset({
    "github.com",
    "codeload.github.com",
    "objects.githubusercontent.com",
})

_MAX_REDIRECTS = 5


def validate_github_archive_url(url: str) -> tuple[str, str, str]:
    """Validate a GitHub archive URL and extract owner, repo, revision.

    Only accepts URLs of the form:
    https://github.com/<owner>/<repo>/archive/<revision>.zip

    Args:
        url: The URL to validate.

    Returns:
        Tuple of (owner, repo, revision).

    Raises:
        SkillSourceError: If the URL is invalid.
    """
    if not url.startswith("https://"):
        raise SkillSourceError(
            f"Only HTTPS URLs are allowed. Got: {url}"
        )

    # Validate URL format
    match = _GITHUB_ARCHIVE_RE.match(url)
    if not match:
        raise SkillSourceError(
            f"Invalid GitHub archive URL: {url}. "
            f"Expected format: https://github.com/<owner>/<repo>/archive/<ref>.zip"
        )

    owner = match.group(1)
    repo = match.group(3)
    revision = match.group(5)

    # Validate owner and repo are not too long
    if len(owner) > 100 or len(repo) > 100:
        raise SkillSourceError(
            f"Owner or repo name too long: {owner}/{repo}"
        )

    # Validate revision looks reasonable
    if not revision or len(revision) > 200:
        raise SkillSourceError(
            f"Invalid revision: {revision}"
        )

    # Check for suspicious characters in revision
    if re.search(r"[<>\"'|*\x00-\x1f]", revision):
        raise SkillSourceError(
            f"Revision contains invalid characters: {revision!r}"
        )

    return owner, repo, revision


def is_github_archive_url(url: str) -> bool:
    """Check if a URL is a valid GitHub archive URL."""
    try:
        validate_github_archive_url(url)
        return True
    except SkillSourceError:
        return False


# ── Download size limits ──


def check_content_length(
    content_length: int | None,
    max_bytes: int,
) -> None:
    """Check Content-Length header against max_bytes.

    Raises SkillSourceError if Content-Length exceeds the limit.
    If content_length is None, no check is performed (caller must
    track streaming bytes).
    """
    if content_length is not None and content_length > max_bytes:
        raise SkillSourceError(
            f"Content-Length {content_length} exceeds maximum {max_bytes} bytes",
            detail={
                "content_length": content_length,
                "max_bytes": max_bytes,
            },
        )


# ── Service ──


class SkillInstallService:
    """Orchestration layer between sources and SkillInstaller."""

    def __init__(
        self,
        paths: SkillPaths,
        registry: SkillRegistry,
        installer: SkillInstaller | None = None,
        limits: SkillPackageLimits | None = None,
    ) -> None:
        self._paths = paths
        self._registry = registry
        self._limits = limits or get_default_limits()
        self._installer = installer or SkillInstaller(
            paths=paths, registry=registry, limits=self._limits
        )
        self._log_writer = InstallLogger(paths.logs_dir)

    @property
    def paths(self) -> SkillPaths:
        return self._paths

    @property
    def installer(self) -> SkillInstaller:
        return self._installer

    def install_from_directory(
        self,
        directory: str,
        activate_after_install: bool = False,
        cancel_check: Callable[[], bool] | None = None,
        stage_callback: Callable[[str], None] | None = None,
    ) -> SkillInstallResult:
        """Install a skill from a local directory.

        Args:
            directory: Path to the skill directory.
            activate_after_install: Whether to make this the active version.
            cancel_check: Optional cancel callback.
            stage_callback: Optional stage callback.

        Returns:
            SkillInstallResult.
        """
        source = SkillPackageSource(
            source_type=SkillPackageSourceType.LOCAL_DIRECTORY,
            location=directory,
        )
        request = SkillInstallRequest(
            source=source,
            activate_after_install=activate_after_install,
        )
        return self._install_and_log(
            request, cancel_check, stage_callback
        )

    def install_from_zip(
        self,
        zip_path: str,
        sub_path: str = "",
        activate_after_install: bool = False,
        cancel_check: Callable[[], bool] | None = None,
        stage_callback: Callable[[str], None] | None = None,
    ) -> SkillInstallResult:
        """Install a skill from a local ZIP file.

        Args:
            zip_path: Path to the ZIP file.
            sub_path: Subdirectory within the ZIP containing SKILL.md.
            activate_after_install: Whether to make this the active version.
            cancel_check: Optional cancel callback.
            stage_callback: Optional stage callback.

        Returns:
            SkillInstallResult.
        """
        source = SkillPackageSource(
            source_type=SkillPackageSourceType.LOCAL_ZIP,
            location=zip_path,
            sub_path=sub_path,
        )
        request = SkillInstallRequest(
            source=source,
            activate_after_install=activate_after_install,
        )
        return self._install_and_log(
            request, cancel_check, stage_callback
        )

    def install_from_github(
        self,
        archive_path: str,
        revision: str = "",
        sub_path: str = "",
        activate_after_install: bool = False,
        cancel_check: Callable[[], bool] | None = None,
        stage_callback: Callable[[str], None] | None = None,
    ) -> SkillInstallResult:
        """Install a skill from a downloaded GitHub archive.

        The archive must already be downloaded to archive_path.
        This method handles the extraction and installation.

        Args:
            archive_path: Path to the downloaded archive ZIP.
            revision: Git revision (for record-keeping).
            sub_path: Subdirectory within the archive containing SKILL.md.
            activate_after_install: Whether to make this the active version.
            cancel_check: Optional cancel callback.
            stage_callback: Optional stage callback.

        Returns:
            SkillInstallResult.
        """
        source = SkillPackageSource(
            source_type=SkillPackageSourceType.GITHUB_ARCHIVE,
            location=archive_path,
            revision=revision,
            sub_path=sub_path,
        )
        request = SkillInstallRequest(
            source=source,
            activate_after_install=activate_after_install,
        )
        return self._install_and_log(
            request, cancel_check, stage_callback
        )

    def uninstall(
        self,
        skill_id: str,
        version: str,
        cancel_check: Callable[[], bool] | None = None,
    ) -> object:  # SkillUninstallResult
        """Uninstall a skill version."""
        return self._installer.uninstall(skill_id, version, cancel_check)

    # ── Internal ──

    def _install_and_log(
        self,
        request: SkillInstallRequest,
        cancel_check: Callable[[], bool] | None,
        stage_callback: Callable[[str], None] | None,
    ) -> SkillInstallResult:
        """Run install and log the result."""
        from dp_engine.skills.install_events import (
            InstallLogEntry,
            sanitize_location,
            _now_iso,
        )
        import uuid

        task_id = uuid.uuid4().hex[:12]
        entry = InstallLogEntry(
            task_id=task_id,
            source_type=request.source.source_type.value,
            source_location_safe=sanitize_location(
                request.source.source_type.value,
                request.source.location,
            ),
            started_at=_now_iso(),
        )

        result = self._installer.install(
            request, cancel_check, stage_callback
        )

        # Populate log entry
        entry.skill_id = result.skill_id or ""
        entry.version = result.version or ""
        entry.package_checksum = result.package_checksum
        entry.warnings = list(result.warnings)
        entry.ended_at = _now_iso()
        entry.result = "success" if result.success else "failed"
        entry.message = result.message

        self._log_writer.write(entry)

        return result
