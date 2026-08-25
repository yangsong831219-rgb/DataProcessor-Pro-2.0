"""Transactional Managed Source Bundle Store for PPT Master.

The store qualifies a pinned Source Bundle, selectively extracts the reviewed
toolchain subset, attests every file, and commits it atomically.  It never
imports or executes bundle content and never touches the Skill Registry.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import stat
import threading
import time
import uuid
import zipfile
from collections.abc import Callable, Mapping
from dataclasses import dataclass, replace
from pathlib import Path, PurePosixPath
from typing import Any

from dp_engine.ppt_master_host.source_bundle import (
    PPT_MASTER_2_7_0_CONTRACT,
    PptMasterSourceBundle,
    PptMasterSourceContract,
    PptMasterSourceQualificationError,
    qualify_ppt_master_source_archive,
)
from utils.app_paths import get_app_data_root


_ATTESTATION_FILENAME = ".source-bundle.json"
_ATTESTATION_SCHEMA_VERSION = 1
_ATTESTATION_MAX_BYTES = 4 * 1024 * 1024
_COPY_CHUNK_BYTES = 1024 * 1024
_MAX_SELECTED_FILES = 12_500
_MAX_SELECTED_BYTES = 25 * 1024 * 1024
_SELECTION_POLICY = "ppt-master-host-toolchain-v1"
_INCLUDED_FILES = frozenset(
    {
        ".claude-plugin/marketplace.json",
        "LICENSE",
        "skills/.claude-plugin/plugin.json",
    }
)
_INCLUDED_PREFIX = "skills/ppt-master/"
_EXCLUDED_PREFIXES = (
    "skills/ppt-master/references/ai-image-comparison/",
)
_STORE_LOCK = threading.RLock()

PPT_MASTER_2_7_0_TOOLCHAIN_TREE_SHA256 = (
    "a9e3b28cd98571a1d03e348d882dc9ffc8807e90a2cdb130dd4c79faf1167850"
)


class PptMasterBundleStoreError(RuntimeError):
    """A named Managed Source Bundle Store operation failed."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


class PptMasterBundleInstallCancelled(PptMasterBundleStoreError):
    """The current install transaction was cancelled and rolled back."""

    def __init__(self, message: str = "PPT Master installation was cancelled") -> None:
        super().__init__("cancelled", message)


@dataclass(frozen=True, slots=True)
class PptMasterBundleStorePaths:
    """Single root for an isolated Managed Source Bundle Store."""

    root: Path

    def __post_init__(self) -> None:
        root = Path(self.root).expanduser().resolve(strict=False)
        if root == Path(root.anchor):
            raise ValueError("PPT Master store root cannot be a filesystem root")
        object.__setattr__(self, "root", root)

    @property
    def installed_dir(self) -> Path:
        return self.root / "installed"

    @property
    def staging_dir(self) -> Path:
        return self.root / "staging"


@dataclass(frozen=True, slots=True)
class PptMasterInstalledBundle:
    """Attested installation returned by the store Interface."""

    install_path: Path
    version: str
    archive_sha256: str
    tree_sha256: str
    file_count: int
    total_bytes: int
    created: bool


@dataclass(frozen=True, slots=True)
class _FileRecord:
    relative_path: str
    size_bytes: int
    sha256: str

    def to_dict(self) -> dict[str, object]:
        return {
            "path": self.relative_path,
            "size_bytes": self.size_bytes,
            "sha256": self.sha256,
        }


def get_default_ppt_master_store_paths(
    root: str | Path | None = None,
) -> PptMasterBundleStorePaths:
    """Return paths outside the Skill Runtime and source repository."""

    selected_root = (
        Path(root)
        if root is not None
        else get_app_data_root() / "toolchains" / "ppt-master"
    )
    return PptMasterBundleStorePaths(selected_root)


class PptMasterBundleStore:
    """Deep Module for qualification, extraction, attestation, and commit."""

    def __init__(
        self,
        paths: PptMasterBundleStorePaths | None = None,
        *,
        source_contract: PptMasterSourceContract = PPT_MASTER_2_7_0_CONTRACT,
        expected_tree_sha256: str | None = (
            PPT_MASTER_2_7_0_TOOLCHAIN_TREE_SHA256
        ),
    ) -> None:
        self._paths = paths or get_default_ppt_master_store_paths()
        self._source_contract = source_contract
        self._expected_tree_sha256 = _normalize_optional_digest(
            expected_tree_sha256
        )

    @property
    def paths(self) -> PptMasterBundleStorePaths:
        return self._paths

    def install_archive(
        self,
        archive_path: str | Path,
        *,
        cancel_check: Callable[[], bool] | None = None,
    ) -> PptMasterInstalledBundle:
        """Qualify and transactionally install one reviewed Source Bundle."""

        with _STORE_LOCK:
            return self._install_locked(
                archive_path,
                cancel_check=cancel_check,
            )

    def verify_installed(
        self,
        version: str,
        archive_sha256: str,
        *,
        cancel_check: Callable[[], bool] | None = None,
    ) -> PptMasterInstalledBundle:
        """Verify a committed bundle without executing any installed file."""

        safe_version = _safe_segment(version, "version")
        digest = _normalize_digest(archive_sha256)
        install_path = self._install_path(safe_version, digest)
        if not install_path.is_dir():
            raise PptMasterBundleStoreError(
                "not_installed",
                f"PPT Master bundle is not installed: {safe_version}@{digest}",
            )
        return self._verify_installation(
            install_path,
            version=safe_version,
            archive_sha256=digest,
            cancel_check=cancel_check,
        )

    def _install_locked(
        self,
        archive_path: str | Path,
        *,
        cancel_check: Callable[[], bool] | None,
    ) -> PptMasterInstalledBundle:
        _raise_if_cancelled(cancel_check)
        try:
            bundle = qualify_ppt_master_source_archive(
                archive_path,
                contract=self._source_contract,
            )
        except PptMasterSourceQualificationError as error:
            raise PptMasterBundleStoreError(
                "qualification_failed",
                f"PPT Master Source Bundle qualification failed: {error}",
            ) from error
        _raise_if_cancelled(cancel_check)

        version = _safe_segment(bundle.version, "version")
        digest = _normalize_digest(bundle.archive_sha256)
        self._ensure_store_dirs()
        install_path = self._install_path(version, digest)

        if install_path.exists():
            try:
                verified = self._verify_installation(
                    install_path,
                    version=version,
                    archive_sha256=digest,
                    cancel_check=cancel_check,
                )
            except PptMasterBundleStoreError as error:
                raise PptMasterBundleStoreError(
                    "install_conflict",
                    "Existing PPT Master installation failed integrity checks; "
                    "refusing to overwrite it",
                ) from error
            return replace(verified, created=False)

        transaction_dir = self._paths.staging_dir / (
            f"install-{version}-{uuid.uuid4().hex}"
        )
        payload_dir = transaction_dir / "payload"
        transaction_dir.mkdir(parents=False, exist_ok=False)
        payload_dir.mkdir(parents=False, exist_ok=False)

        try:
            records = self._extract_selected_files(
                bundle,
                payload_dir,
                cancel_check=cancel_check,
            )
            actual_archive_sha256 = _sha256_file(
                bundle.archive_path,
                cancel_check=cancel_check,
            )
            if actual_archive_sha256 != digest:
                raise PptMasterBundleStoreError(
                    "source_changed",
                    "PPT Master source archive changed during installation",
                )

            tree_sha256 = _tree_sha256(records)
            if (
                self._expected_tree_sha256 is not None
                and tree_sha256 != self._expected_tree_sha256
            ):
                raise PptMasterBundleStoreError(
                    "tree_digest_mismatch",
                    "Selected PPT Master toolchain content does not match the "
                    "reviewed tree digest",
                )

            _write_attestation(
                payload_dir,
                bundle=bundle,
                records=records,
                tree_sha256=tree_sha256,
            )
            _verify_payload_inventory(payload_dir, records)
            _raise_if_cancelled(cancel_check)

            install_path.parent.mkdir(parents=True, exist_ok=True)
            _verify_not_reparse(install_path.parent)
            try:
                _atomic_replace_dir(str(payload_dir), str(install_path))
            except OSError as error:
                if install_path.is_dir():
                    verified = self._verify_installation(
                        install_path,
                        version=version,
                        archive_sha256=digest,
                        cancel_check=cancel_check,
                    )
                    return replace(verified, created=False)
                raise PptMasterBundleStoreError(
                    "commit_failed",
                    f"PPT Master atomic install commit failed: {error}",
                ) from error
            _fsync_directory(install_path.parent)

            return PptMasterInstalledBundle(
                install_path=install_path,
                version=version,
                archive_sha256=digest,
                tree_sha256=tree_sha256,
                file_count=len(records),
                total_bytes=sum(record.size_bytes for record in records),
                created=True,
            )
        except PptMasterBundleInstallCancelled:
            raise
        except PptMasterBundleStoreError:
            raise
        except (OSError, zipfile.BadZipFile, RuntimeError) as error:
            raise PptMasterBundleStoreError(
                "install_failed",
                f"PPT Master installation failed: {error}",
            ) from error
        finally:
            _remove_transaction_dir(transaction_dir, self._paths.staging_dir)

    def _extract_selected_files(
        self,
        bundle: PptMasterSourceBundle,
        payload_dir: Path,
        *,
        cancel_check: Callable[[], bool] | None,
    ) -> tuple[_FileRecord, ...]:
        with zipfile.ZipFile(bundle.archive_path, "r") as archive:
            selected = _select_file_infos(archive, bundle.root_prefix)
            selected_bytes = sum(info.file_size for _, info in selected)
            if len(selected) > _MAX_SELECTED_FILES:
                raise PptMasterBundleStoreError(
                    "selection_limit",
                    f"Selected toolchain has too many files: {len(selected)}",
                )
            if selected_bytes > _MAX_SELECTED_BYTES:
                raise PptMasterBundleStoreError(
                    "selection_limit",
                    "Selected toolchain exceeds the managed-store byte limit",
                )

            selected_names = {relative_path for relative_path, _ in selected}
            required = {
                _strip_root_prefix(name, bundle.root_prefix)
                for name in bundle.critical_files
            }
            if required - selected_names:
                raise PptMasterBundleStoreError(
                    "selection_invalid",
                    "Selection policy omits required PPT Master files",
                )

            records: list[_FileRecord] = []
            for relative_path, info in selected:
                _raise_if_cancelled(cancel_check)
                target = _target_for_relative_path(payload_dir, relative_path)
                target.parent.mkdir(parents=True, exist_ok=True)
                digest = hashlib.sha256()
                written = 0
                with archive.open(info, "r") as source, target.open("xb") as output:
                    while True:
                        _raise_if_cancelled(cancel_check)
                        chunk = source.read(_COPY_CHUNK_BYTES)
                        if not chunk:
                            break
                        output.write(chunk)
                        digest.update(chunk)
                        written += len(chunk)
                if written != info.file_size:
                    raise PptMasterBundleStoreError(
                        "extraction_size_mismatch",
                        f"Extracted size mismatch for {relative_path!r}",
                    )
                records.append(
                    _FileRecord(
                        relative_path=relative_path,
                        size_bytes=written,
                        sha256=digest.hexdigest(),
                    )
                )
        return tuple(sorted(records, key=lambda record: record.relative_path))

    def _verify_installation(
        self,
        install_path: Path,
        *,
        version: str,
        archive_sha256: str,
        cancel_check: Callable[[], bool] | None,
    ) -> PptMasterInstalledBundle:
        _verify_managed_child(install_path, self._paths.installed_dir)
        _verify_not_reparse(install_path)
        attestation = _read_attestation(install_path / _ATTESTATION_FILENAME)
        bundle_data = _require_mapping(attestation.get("bundle"), "bundle")
        selection = _require_mapping(attestation.get("selection"), "selection")

        _require_equal(attestation.get("schema_version"), 1, "schema_version")
        _require_equal(bundle_data.get("name"), "ppt-master", "bundle.name")
        _require_equal(bundle_data.get("version"), version, "bundle.version")
        _require_equal(
            bundle_data.get("archive_sha256"),
            archive_sha256,
            "bundle.archive_sha256",
        )
        _require_equal(
            bundle_data.get("source_url"),
            self._source_contract.source_url,
            "bundle.source_url",
        )
        _require_equal(
            bundle_data.get("license"),
            self._source_contract.license_name,
            "bundle.license",
        )
        _require_equal(
            selection.get("policy"),
            _SELECTION_POLICY,
            "selection.policy",
        )

        records = _parse_file_records(attestation.get("files"))
        recorded_count = _require_int(selection.get("file_count"), "file_count")
        recorded_bytes = _require_int(selection.get("total_bytes"), "total_bytes")
        recorded_tree = _require_digest(
            selection.get("tree_sha256"),
            "selection.tree_sha256",
        )
        if recorded_count != len(records):
            raise PptMasterBundleStoreError(
                "attestation_invalid",
                "PPT Master attestation file_count is inconsistent",
            )
        total_bytes = sum(record.size_bytes for record in records)
        if recorded_bytes != total_bytes:
            raise PptMasterBundleStoreError(
                "attestation_invalid",
                "PPT Master attestation total_bytes is inconsistent",
            )
        if _tree_sha256(records) != recorded_tree:
            raise PptMasterBundleStoreError(
                "attestation_invalid",
                "PPT Master attestation tree digest is inconsistent",
            )
        if (
            self._expected_tree_sha256 is not None
            and recorded_tree != self._expected_tree_sha256
        ):
            raise PptMasterBundleStoreError(
                "integrity_mismatch",
                "Installed PPT Master tree digest is not the reviewed digest",
            )

        expected_paths = {record.relative_path for record in records}
        actual_paths = _scan_regular_files(install_path)
        expected_with_attestation = expected_paths | {_ATTESTATION_FILENAME}
        if actual_paths != expected_with_attestation:
            raise PptMasterBundleStoreError(
                "integrity_mismatch",
                "Installed PPT Master file inventory does not match attestation",
            )

        for record in records:
            _raise_if_cancelled(cancel_check)
            target = _target_for_relative_path(install_path, record.relative_path)
            if target.stat().st_size != record.size_bytes:
                raise PptMasterBundleStoreError(
                    "integrity_mismatch",
                    f"Installed PPT Master file size changed: {record.relative_path}",
                )
            if _sha256_file(target, cancel_check=cancel_check) != record.sha256:
                raise PptMasterBundleStoreError(
                    "integrity_mismatch",
                    f"Installed PPT Master file changed: {record.relative_path}",
                )

        return PptMasterInstalledBundle(
            install_path=install_path,
            version=version,
            archive_sha256=archive_sha256,
            tree_sha256=recorded_tree,
            file_count=len(records),
            total_bytes=total_bytes,
            created=False,
        )

    def _ensure_store_dirs(self) -> None:
        self._paths.root.mkdir(parents=True, exist_ok=True)
        _verify_not_reparse(self._paths.root)
        for directory in (
            self._paths.installed_dir,
            self._paths.staging_dir,
        ):
            directory.mkdir(parents=True, exist_ok=True)
            _verify_not_reparse(directory)

    def _install_path(self, version: str, archive_sha256: str) -> Path:
        result = self._paths.installed_dir / version / archive_sha256
        _verify_managed_child(result, self._paths.installed_dir)
        return result


def install_ppt_master_2_7_0_archive(
    archive_path: str | Path,
    *,
    store_root: str | Path | None = None,
    cancel_check: Callable[[], bool] | None = None,
) -> PptMasterInstalledBundle:
    """Install the reviewed 2.7.0 archive into the dedicated Host store."""

    store = PptMasterBundleStore(
        get_default_ppt_master_store_paths(store_root),
    )
    return store.install_archive(archive_path, cancel_check=cancel_check)


def _select_file_infos(
    archive: zipfile.ZipFile,
    root_prefix: str,
) -> tuple[tuple[str, zipfile.ZipInfo], ...]:
    selected: list[tuple[str, zipfile.ZipInfo]] = []
    for info in archive.infolist():
        normalized = info.filename.replace("\\", "/")
        if info.is_dir() or not normalized.startswith(root_prefix):
            continue
        relative_path = normalized[len(root_prefix):]
        if _is_selected(relative_path):
            selected.append((relative_path, info))
    return tuple(sorted(selected, key=lambda item: item[0]))


def _is_selected(relative_path: str) -> bool:
    if any(relative_path.startswith(prefix) for prefix in _EXCLUDED_PREFIXES):
        return False
    return (
        relative_path in _INCLUDED_FILES
        or relative_path.startswith(_INCLUDED_PREFIX)
    )


def _strip_root_prefix(path: str, root_prefix: str) -> str:
    normalized = path.replace("\\", "/")
    if not normalized.startswith(root_prefix):
        raise PptMasterBundleStoreError(
            "selection_invalid",
            f"Critical file is outside Source Bundle root: {path}",
        )
    return normalized[len(root_prefix):]


def _target_for_relative_path(root: Path, relative_path: str) -> Path:
    pure_path = PurePosixPath(relative_path)
    if pure_path.is_absolute() or not pure_path.parts or ".." in pure_path.parts:
        raise PptMasterBundleStoreError(
            "path_invalid",
            f"Unsafe managed-store relative path: {relative_path!r}",
        )
    target = root.joinpath(*pure_path.parts)
    _verify_managed_child(target, root)
    return target


def _write_attestation(
    payload_dir: Path,
    *,
    bundle: PptMasterSourceBundle,
    records: tuple[_FileRecord, ...],
    tree_sha256: str,
) -> None:
    attestation = {
        "schema_version": _ATTESTATION_SCHEMA_VERSION,
        "bundle": {
            "name": "ppt-master",
            "version": bundle.version,
            "archive_sha256": bundle.archive_sha256,
            "source_url": bundle.source_url,
            "license": bundle.license_name,
        },
        "selection": {
            "policy": _SELECTION_POLICY,
            "file_count": len(records),
            "total_bytes": sum(record.size_bytes for record in records),
            "tree_sha256": tree_sha256,
            "excluded_prefixes": list(_EXCLUDED_PREFIXES),
        },
        "files": [record.to_dict() for record in records],
    }
    destination = payload_dir / _ATTESTATION_FILENAME
    temporary = payload_dir / f"{_ATTESTATION_FILENAME}.{uuid.uuid4().hex}.tmp"
    encoded = json.dumps(
        attestation,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    try:
        with temporary.open("xb") as output:
            output.write(encoded)
            output.flush()
            os.fsync(output.fileno())
        os.replace(str(temporary), str(destination))
    finally:
        temporary.unlink(missing_ok=True)


def _read_attestation(path: Path) -> Mapping[str, Any]:
    if not path.is_file() or _is_reparse(path):
        raise PptMasterBundleStoreError(
            "attestation_missing",
            f"PPT Master installation attestation is missing: {path}",
        )
    if path.stat().st_size > _ATTESTATION_MAX_BYTES:
        raise PptMasterBundleStoreError(
            "attestation_invalid",
            "PPT Master installation attestation is too large",
        )
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise PptMasterBundleStoreError(
            "attestation_invalid",
            "PPT Master installation attestation is not valid UTF-8 JSON",
        ) from error
    return _require_mapping(value, "attestation")


def _parse_file_records(value: object) -> tuple[_FileRecord, ...]:
    if not isinstance(value, list):
        raise PptMasterBundleStoreError(
            "attestation_invalid",
            "PPT Master attestation files must be a list",
        )
    if len(value) > _MAX_SELECTED_FILES:
        raise PptMasterBundleStoreError(
            "attestation_invalid",
            "PPT Master attestation contains too many files",
        )
    records: list[_FileRecord] = []
    seen: set[str] = set()
    seen_lower: set[str] = set()
    for item in value:
        data = _require_mapping(item, "files[]")
        relative_path = _require_string(data.get("path"), "files[].path")
        _target_for_relative_path(Path("C:/managed-root"), relative_path)
        if relative_path in seen or relative_path.lower() in seen_lower:
            raise PptMasterBundleStoreError(
                "attestation_invalid",
                f"Duplicate path in PPT Master attestation: {relative_path}",
            )
        seen.add(relative_path)
        seen_lower.add(relative_path.lower())
        records.append(
            _FileRecord(
                relative_path=relative_path,
                size_bytes=_require_int(
                    data.get("size_bytes"),
                    "files[].size_bytes",
                ),
                sha256=_require_digest(
                    data.get("sha256"),
                    "files[].sha256",
                ),
            )
        )
    return tuple(sorted(records, key=lambda record: record.relative_path))


def _verify_payload_inventory(
    payload_dir: Path,
    records: tuple[_FileRecord, ...],
) -> None:
    actual = _scan_regular_files(payload_dir)
    expected = {record.relative_path for record in records} | {
        _ATTESTATION_FILENAME
    }
    if actual != expected:
        raise PptMasterBundleStoreError(
            "staging_integrity",
            "PPT Master staging inventory changed before commit",
        )


def _scan_regular_files(root: Path) -> set[str]:
    result: set[str] = set()
    pending = [root]
    while pending:
        directory = pending.pop()
        _verify_not_reparse(directory)
        try:
            with os.scandir(directory) as entries:
                for entry in entries:
                    path = Path(entry.path)
                    if _entry_is_reparse(entry):
                        raise PptMasterBundleStoreError(
                            "integrity_mismatch",
                            f"Managed PPT Master path is a reparse point: {path}",
                        )
                    if entry.is_dir(follow_symlinks=False):
                        pending.append(path)
                    elif entry.is_file(follow_symlinks=False):
                        result.add(path.relative_to(root).as_posix())
                    else:
                        raise PptMasterBundleStoreError(
                            "integrity_mismatch",
                            f"Managed PPT Master path is not a regular file: {path}",
                        )
        except OSError as error:
            raise PptMasterBundleStoreError(
                "integrity_mismatch",
                f"Cannot scan managed PPT Master directory: {directory}",
            ) from error
    return result


def _tree_sha256(records: tuple[_FileRecord, ...]) -> str:
    digest = hashlib.sha256()
    for record in sorted(records, key=lambda item: item.relative_path):
        digest.update(
            (
                f"{record.relative_path}\0{record.size_bytes}\0"
                f"{record.sha256}\n"
            ).encode("utf-8")
        )
    return digest.hexdigest()


def _sha256_file(
    path: Path,
    *,
    cancel_check: Callable[[], bool] | None,
) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        while True:
            _raise_if_cancelled(cancel_check)
            chunk = source.read(_COPY_CHUNK_BYTES)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


def _remove_transaction_dir(transaction_dir: Path, staging_dir: Path) -> None:
    if not transaction_dir.exists():
        return
    _verify_managed_child(transaction_dir, staging_dir)
    shutil.rmtree(transaction_dir, ignore_errors=False)


def _verify_managed_child(path: Path, root: Path) -> None:
    resolved_path = path.resolve(strict=False)
    resolved_root = root.resolve(strict=False)
    try:
        resolved_path.relative_to(resolved_root)
    except ValueError as error:
        raise PptMasterBundleStoreError(
            "path_invalid",
            f"Managed PPT Master path escapes store root: {path}",
        ) from error


def _verify_not_reparse(path: Path) -> None:
    if _is_reparse(path):
        raise PptMasterBundleStoreError(
            "path_invalid",
            f"Managed PPT Master path is a symlink or reparse point: {path}",
        )


def _is_reparse(path: Path) -> bool:
    try:
        info = path.lstat()
    except OSError:
        return False
    if stat.S_ISLNK(info.st_mode):
        return True
    attributes = getattr(info, "st_file_attributes", 0)
    return bool(attributes & 0x400)


def _entry_is_reparse(entry: os.DirEntry[str]) -> bool:
    if entry.is_symlink():
        return True
    try:
        info = entry.stat(follow_symlinks=False)
    except OSError as error:
        raise PptMasterBundleStoreError(
            "integrity_mismatch",
            f"Cannot inspect managed PPT Master path: {entry.path}",
        ) from error
    attributes = getattr(info, "st_file_attributes", 0)
    return bool(attributes & 0x400)


def _safe_segment(value: str, field: str) -> str:
    if (
        not value
        or len(value) > 64
        or value in {".", ".."}
        or any(not (char.isalnum() or char in "._-") for char in value)
    ):
        raise PptMasterBundleStoreError(
            "path_invalid",
            f"Unsafe PPT Master {field}: {value!r}",
        )
    return value


def _normalize_digest(value: str) -> str:
    normalized = value.lower()
    if len(normalized) != 64 or any(
        char not in "0123456789abcdef" for char in normalized
    ):
        raise PptMasterBundleStoreError(
            "digest_invalid",
            "PPT Master digest must be 64 hexadecimal characters",
        )
    return normalized


def _normalize_optional_digest(value: str | None) -> str | None:
    return None if value is None else _normalize_digest(value)


def _require_mapping(value: object, field: str) -> Mapping[str, Any]:
    if not isinstance(value, dict):
        raise PptMasterBundleStoreError(
            "attestation_invalid",
            f"PPT Master attestation field must be an object: {field}",
        )
    return value


def _require_string(value: object, field: str) -> str:
    if not isinstance(value, str) or not value:
        raise PptMasterBundleStoreError(
            "attestation_invalid",
            f"PPT Master attestation field must be a string: {field}",
        )
    return value


def _require_int(value: object, field: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise PptMasterBundleStoreError(
            "attestation_invalid",
            f"PPT Master attestation field must be a non-negative int: {field}",
        )
    return value


def _require_digest(value: object, field: str) -> str:
    if not isinstance(value, str):
        raise PptMasterBundleStoreError(
            "attestation_invalid",
            f"PPT Master attestation field must be a digest: {field}",
        )
    try:
        return _normalize_digest(value)
    except PptMasterBundleStoreError as error:
        raise PptMasterBundleStoreError(
            "attestation_invalid",
            f"PPT Master attestation field must be a digest: {field}",
        ) from error


def _require_equal(actual: object, expected: object, field: str) -> None:
    if actual != expected:
        raise PptMasterBundleStoreError(
            "attestation_invalid",
            f"PPT Master attestation mismatch for {field}",
        )


def _raise_if_cancelled(
    cancel_check: Callable[[], bool] | None,
) -> None:
    if cancel_check is not None and cancel_check():
        raise PptMasterBundleInstallCancelled()


def _fsync_directory(path: Path) -> None:
    try:
        descriptor = os.open(str(path), os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(descriptor)
    except OSError:
        pass
    finally:
        os.close(descriptor)


def _atomic_replace_dir(source: str, destination: str) -> None:
    """os.replace with bounded retry for transient Windows file-handle locks.

    On Windows, directory handles from os.scandir() or ZipFile extraction
    may not be fully released by the time os.replace() is called, causing
    sporadic ERROR_ACCESS_DENIED.  This retry loop mitigates that without
    hiding permanent permission errors.
    """
    for attempt in range(5):
        try:
            os.replace(source, destination)
            return
        except PermissionError:
            if attempt >= 4:
                raise
            time.sleep(0.1 * (attempt + 1))
