"""Install event types and logging.

Provides structured install event logging to skills/logs/.
Never logs tokens, authorization headers, or file content.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from dp_engine.skills.package_models import InstallStage

logger = logging.getLogger(__name__)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class InstallLogEntry:
    """A single install operation log entry.

    Safe for serialization — never contains tokens or file content.
    """

    task_id: str
    source_type: str  # SkillPackageSourceType value
    source_location_safe: str  # Sanitized location (no tokens)
    skill_id: str = ""
    version: str = ""
    archive_checksum: str | None = None
    package_checksum: str | None = None
    file_count: int = 0
    total_size_bytes: int = 0
    warnings: list[str] = field(default_factory=list)
    started_at: str = ""
    ended_at: str = ""
    result: str = ""  # "success", "failed", "cancelled", "conflict"
    rollback_result: str = ""  # "none", "success", "failed"
    message: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "task_id": self.task_id,
            "source_type": self.source_type,
            "source_location_safe": self.source_location_safe,
            "skill_id": self.skill_id,
            "version": self.version,
            "archive_checksum": self.archive_checksum,
            "package_checksum": self.package_checksum,
            "file_count": self.file_count,
            "total_size_bytes": self.total_size_bytes,
            "warnings": self.warnings,
            "started_at": self.started_at,
            "ended_at": self.ended_at,
            "result": self.result,
            "rollback_result": self.rollback_result,
            "message": self.message,
        }


class InstallLogger:
    """Writes structured install logs to skills/logs/.

    Usage:
        log_writer = InstallLogger(paths.logs_dir)
        entry = InstallLogEntry(...)
        log_writer.write(entry)
    """

    def __init__(self, logs_dir: Path) -> None:
        self._logs_dir = Path(logs_dir)
        self._logs_dir.mkdir(parents=True, exist_ok=True)

    def write(self, entry: InstallLogEntry) -> Path:
        """Write a log entry to a JSON file.

        Filename: install-{task_id}-{timestamp}.json
        """
        timestamp = _now_iso().replace(":", "-")
        filename = f"install-{entry.task_id}-{timestamp}.json"
        filepath = self._logs_dir / filename

        try:
            data = entry.to_dict()
            filepath.write_text(
                json.dumps(data, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            return filepath
        except OSError as e:
            logger.warning("Failed to write install log: %s", e)
            return filepath


def sanitize_location(source_type: str, location: str) -> str:
    """Sanitize a source location for logging.

    Strips tokens, passwords, and other secrets from URLs.
    For local paths, shows only the basename.
    For GitHub URLs, strips query parameters and fragments.
    """
    if source_type in ("local_directory", "local_zip"):
        try:
            return Path(location).name
        except Exception:
            return "<local>"
    elif source_type == "github_archive":
        # Strip query params and fragments from URL
        if "?" in location:
            location = location.split("?")[0]
        if "#" in location:
            location = location.split("#")[0]
        # Show only repo/ref parts
        return location
    return "<unknown>"
