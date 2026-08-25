"""Read-only qualification for a hash-pinned PPT Master Source Bundle.

Qualification is deliberately separate from Skill installation.  It inspects
the archive and its small metadata files but never extracts, imports, or
executes archive content.
"""

from __future__ import annotations

import json
import stat
import zipfile
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from dp_engine.skills.archive_utils import inspect_archive
from dp_engine.skills.errors import SkillPackageError
from dp_engine.skills.package_models import SkillPackageLimits


_MIB = 1024 * 1024
_MAX_METADATA_BYTES = 128 * 1024


class PptMasterSourceQualificationError(RuntimeError):
    """A Source Bundle failed a named qualification rule."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True, slots=True)
class PptMasterSourceContract:
    """Expected identity and safety envelope for one upstream release."""

    version: str
    archive_sha256: str
    root_prefix: str
    source_url: str
    license_name: str
    limits: SkillPackageLimits
    critical_relative_paths: tuple[str, ...]

    def __post_init__(self) -> None:
        if not self.version.strip():
            raise ValueError("Source Bundle version must be non-empty")
        digest = self.archive_sha256.lower()
        if len(digest) != 64 or any(c not in "0123456789abcdef" for c in digest):
            raise ValueError("Source Bundle SHA-256 must be 64 hexadecimal chars")
        object.__setattr__(self, "archive_sha256", digest)
        prefix = self.root_prefix.replace("\\", "/").strip("/") + "/"
        object.__setattr__(self, "root_prefix", prefix)
        if not self.source_url.strip():
            raise ValueError("Source Bundle source_url must be non-empty")
        if not self.license_name.strip():
            raise ValueError("Source Bundle license_name must be non-empty")
        if not self.critical_relative_paths:
            raise ValueError("Source Bundle must declare critical files")


@dataclass(frozen=True, slots=True)
class PptMasterSourceBundle:
    """Qualified, immutable identity of an upstream PPT Master archive."""

    archive_path: Path
    version: str
    archive_sha256: str
    archive_bytes: int
    uncompressed_bytes: int
    entry_count: int
    root_prefix: str
    source_url: str
    license_name: str
    critical_files: tuple[str, ...]


PPT_MASTER_SOURCE_LIMITS = SkillPackageLimits(
    max_archive_bytes=625 * _MIB,
    max_extracted_bytes=700 * _MIB,
    max_single_file_bytes=40 * _MIB,
    max_file_count=14_000,
    max_directory_depth=12,
    max_path_length=160,
    max_compression_ratio=20.0,
)


PPT_MASTER_2_7_0_CONTRACT = PptMasterSourceContract(
    version="2.7.0",
    archive_sha256=(
        "ac2599b467fff4166ea2c34b62d877b12683391feb95de7a8ffcc7892effd7af"
    ),
    root_prefix="ppt-master-main/",
    source_url="https://github.com/hugohe3/ppt-master",
    license_name="MIT",
    limits=PPT_MASTER_SOURCE_LIMITS,
    critical_relative_paths=(
        ".claude-plugin/marketplace.json",
        "LICENSE",
        "skills/.claude-plugin/plugin.json",
        "skills/ppt-master/SKILL.md",
        "skills/ppt-master/workflows/routing.md",
        "skills/ppt-master/workflows/generate-pptx.md",
        "skills/ppt-master/scripts/project_manager.py",
        "skills/ppt-master/scripts/svg_quality_checker.py",
        "skills/ppt-master/scripts/finalize_svg.py",
        "skills/ppt-master/scripts/svg_to_pptx.py",
    ),
)


def qualify_ppt_master_2_7_0_archive(
    archive_path: str | Path,
) -> PptMasterSourceBundle:
    """Qualify the one reviewed PPT Master 2.7.0 Source Bundle."""

    return qualify_ppt_master_source_archive(
        archive_path,
        contract=PPT_MASTER_2_7_0_CONTRACT,
    )


def qualify_ppt_master_source_archive(
    archive_path: str | Path,
    *,
    contract: PptMasterSourceContract,
) -> PptMasterSourceBundle:
    """Inspect an archive against an explicit Source Bundle contract.

    This Interface is intentionally read-only.  Passing qualification does not
    install the bundle and does not authorize any bundled command to run.
    """

    path = Path(archive_path).expanduser().resolve(strict=False)
    if not path.is_file():
        raise PptMasterSourceQualificationError(
            "archive_missing",
            f"PPT Master archive does not exist or is not a file: {path}",
        )

    try:
        infos, archive_sha256 = inspect_archive(path, limits=contract.limits)
    except SkillPackageError as error:
        raise PptMasterSourceQualificationError(
            "archive_safety",
            f"PPT Master archive failed safety inspection: {error}",
        ) from error

    if archive_sha256.lower() != contract.archive_sha256:
        raise PptMasterSourceQualificationError(
            "checksum_mismatch",
            "PPT Master archive SHA-256 does not match the reviewed release",
        )

    _reject_special_zip_entries(infos)
    entry_names = frozenset(info.filename.replace("\\", "/") for info in infos)
    critical_files = tuple(
        contract.root_prefix + relative_path
        for relative_path in contract.critical_relative_paths
    )
    missing = tuple(name for name in critical_files if name not in entry_names)
    if missing:
        raise PptMasterSourceQualificationError(
            "critical_files_missing",
            "PPT Master archive is missing required files: " + ", ".join(missing),
        )

    marketplace_path = contract.root_prefix + ".claude-plugin/marketplace.json"
    plugin_path = contract.root_prefix + "skills/.claude-plugin/plugin.json"
    with zipfile.ZipFile(path, "r") as archive:
        marketplace = _read_json_object(archive, marketplace_path)
        plugin_manifest = _read_json_object(archive, plugin_path)

    plugin = _select_marketplace_plugin(marketplace)
    metadata = _require_object(marketplace.get("metadata"), "metadata")
    source = _require_object(plugin.get("source"), "plugins[].source")

    _require_equal(marketplace.get("name"), "ppt-master", "marketplace.name")
    _require_equal(metadata.get("version"), contract.version, "metadata.version")
    _require_equal(plugin.get("license"), contract.license_name, "plugin.license")
    _require_source_url(source.get("url"), contract.source_url, "plugin.source.url")
    _require_equal(plugin_manifest.get("name"), "ppt-master", "manifest.name")
    _require_equal(
        plugin_manifest.get("license"),
        contract.license_name,
        "manifest.license",
    )
    _require_source_url(
        plugin_manifest.get("repository"),
        contract.source_url,
        "manifest.repository",
    )
    _require_equal(plugin_manifest.get("skills"), "./", "manifest.skills")

    return PptMasterSourceBundle(
        archive_path=path,
        version=contract.version,
        archive_sha256=archive_sha256.lower(),
        archive_bytes=path.stat().st_size,
        uncompressed_bytes=sum(info.file_size for info in infos),
        entry_count=len(infos),
        root_prefix=contract.root_prefix,
        source_url=contract.source_url,
        license_name=contract.license_name,
        critical_files=critical_files,
    )


def _reject_special_zip_entries(infos: list[zipfile.ZipInfo]) -> None:
    for info in infos:
        if info.create_system != 3:
            continue
        mode = info.external_attr >> 16
        file_type = stat.S_IFMT(mode)
        if file_type not in {0, stat.S_IFREG, stat.S_IFDIR}:
            raise PptMasterSourceQualificationError(
                "archive_safety",
                f"PPT Master archive contains a special file: {info.filename!r}",
            )


def _read_json_object(
    archive: zipfile.ZipFile,
    name: str,
) -> Mapping[str, Any]:
    info = archive.getinfo(name)
    if info.file_size > _MAX_METADATA_BYTES:
        raise PptMasterSourceQualificationError(
            "metadata_too_large",
            f"PPT Master metadata file is too large: {name}",
        )
    try:
        value = json.loads(archive.read(info).decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise PptMasterSourceQualificationError(
            "metadata_invalid",
            f"PPT Master metadata is not valid UTF-8 JSON: {name}",
        ) from error
    return _require_object(value, name)


def _require_object(value: object, field: str) -> Mapping[str, Any]:
    if not isinstance(value, dict):
        raise PptMasterSourceQualificationError(
            "metadata_invalid",
            f"PPT Master metadata field must be an object: {field}",
        )
    return value


def _select_marketplace_plugin(
    marketplace: Mapping[str, Any],
) -> Mapping[str, Any]:
    plugins = marketplace.get("plugins")
    if not isinstance(plugins, list):
        raise PptMasterSourceQualificationError(
            "metadata_invalid",
            "PPT Master marketplace plugins must be a list",
        )
    matches = [
        item
        for item in plugins
        if isinstance(item, dict) and item.get("name") == "ppt-master"
    ]
    if len(matches) != 1:
        raise PptMasterSourceQualificationError(
            "metadata_invalid",
            "PPT Master marketplace must declare exactly one ppt-master plugin",
        )
    return matches[0]


def _require_equal(actual: object, expected: str, field: str) -> None:
    if actual != expected:
        raise PptMasterSourceQualificationError(
            "identity_mismatch",
            f"PPT Master {field} must be {expected!r}, got {actual!r}",
        )


def _require_source_url(actual: object, expected: str, field: str) -> None:
    if not isinstance(actual, str):
        _require_equal(actual, expected, field)
        return
    normalized_actual = actual.rstrip("/")
    if normalized_actual.endswith(".git"):
        normalized_actual = normalized_actual[:-4]
    normalized_expected = expected.rstrip("/")
    _require_equal(normalized_actual, normalized_expected, field)
