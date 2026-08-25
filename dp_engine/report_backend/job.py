"""Frozen Batch 3.5 report-backend job and output contracts.

Host filesystem paths exist only on the Python-side input models.  ``stage``
copies approved inputs into the managed Runtime workspace and serializes only
safe relative paths for the untrusted backend.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import stat
import zipfile
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath, PureWindowsPath

from dp_engine.report_backend.models import REPORT_BACKEND_CONTRACT_VERSION
from dp_engine.skills.runtime_models import (
    MAX_ARTIFACT_FILE_BYTES,
    MAX_ARTIFACTS_TOTAL_BYTES,
    ArtifactDeclaration,
    RuntimeArtifact,
)


REPORT_JOB_SCHEMA_VERSION = 1
REPORT_JOB_RELATIVE_PATH = "input/report_job.json"
MAX_REPORT_JOB_JSON_BYTES = 10 * 1024 * 1024

_REPORT_TYPE_BY_ARTIFACT = {"pptx": "ppt", "docx": "word"}
_MEDIA_TYPE_BY_ARTIFACT = {
    "pptx": (
        "application/vnd.openxmlformats-officedocument.presentationml.presentation"
    ),
    "docx": (
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    ),
}
_ASSET_SUFFIXES_BY_MEDIA = {
    "image/png": frozenset({".png"}),
    "image/jpeg": frozenset({".jpg", ".jpeg"}),
}
_SAFE_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
_FORBIDDEN_JOB_KEYS = frozenset({
    "api_key",
    "apikey",
    "token",
    "secret",
    "password",
    "credential",
    "credentials",
    "command",
    "cmd",
    "environment",
    "env",
})


@dataclass(frozen=True)
class ReportBackendAssetInput:
    """One host-approved image copied into ``workspace/input/assets``."""

    host_id: str
    source_path: Path
    media_type: str
    semantic_label: str
    target: str

    def __post_init__(self) -> None:
        if not _SAFE_ID_RE.fullmatch(self.host_id):
            raise ValueError("asset host_id must be a safe non-empty identifier")
        if self.media_type not in _ASSET_SUFFIXES_BY_MEDIA:
            raise ValueError("asset media_type must be image/png or image/jpeg")
        suffix = self.source_path.suffix.lower()
        if suffix not in _ASSET_SUFFIXES_BY_MEDIA[self.media_type]:
            raise ValueError("asset extension does not match media_type")
        if not self.semantic_label.strip():
            raise ValueError("asset semantic_label must be non-empty")
        if not self.target.strip():
            raise ValueError("asset target must be non-empty")
        if not self.source_path.is_absolute():
            raise ValueError("asset source_path must be absolute")
        _reject_forbidden_wire_values(self.semantic_label)
        _reject_forbidden_wire_values(self.target)


@dataclass(frozen=True)
class ReportBackendJobV1:
    """Host-side immutable snapshot of one professional report render job."""

    report_type: str
    output_artifact_type: str
    report: dict[str, object]
    normalized_template_path: Path | None = None
    assets: tuple[ReportBackendAssetInput, ...] = ()
    required_figure_ids: tuple[str, ...] = ()
    supplementary_figure_ids: tuple[str, ...] = ()
    inclusion_summary: dict[str, object] = field(default_factory=dict)
    _report_json: str = field(init=False, repr=False, compare=False)
    _inclusion_json: str = field(init=False, repr=False, compare=False)

    def __post_init__(self) -> None:
        expected_report_type = _REPORT_TYPE_BY_ARTIFACT.get(
            self.output_artifact_type
        )
        if expected_report_type is None:
            raise ValueError("output_artifact_type must be 'pptx' or 'docx'")
        if self.report_type != expected_report_type:
            raise ValueError(
                "report_type does not match output_artifact_type"
            )

        if self.normalized_template_path is not None:
            if not self.normalized_template_path.is_absolute():
                raise ValueError("normalized template path must be absolute")
            expected_suffix = f".{self.output_artifact_type}"
            if self.normalized_template_path.suffix.lower() != expected_suffix:
                raise ValueError(
                    "normalized template extension does not match output format"
                )

        asset_ids = tuple(asset.host_id for asset in self.assets)
        _validate_unique_ids(asset_ids, "asset")
        _validate_unique_ids(self.required_figure_ids, "required figure")
        _validate_unique_ids(
            self.supplementary_figure_ids, "supplementary figure"
        )
        overlap = set(self.required_figure_ids) & set(
            self.supplementary_figure_ids
        )
        if overlap:
            raise ValueError("figure IDs cannot be both required and supplementary")
        unknown = (
            set(self.required_figure_ids)
            | set(self.supplementary_figure_ids)
        ) - set(asset_ids)
        if unknown:
            raise ValueError("figure IDs must reference staged assets")

        _reject_forbidden_wire_values(self.report)
        _reject_forbidden_wire_values(self.inclusion_summary)
        report_json = _canonical_json(self.report, "report")
        inclusion_json = _canonical_json(
            self.inclusion_summary, "inclusion_summary"
        )
        object.__setattr__(self, "_report_json", report_json)
        object.__setattr__(self, "_inclusion_json", inclusion_json)

    @property
    def template_mode(self) -> str:
        return "normalized" if self.normalized_template_path is not None else "none"

    @property
    def expected_output(self) -> str:
        return f"output/report.{self.output_artifact_type}"

    @property
    def expected_declared_path(self) -> str:
        return f"report.{self.output_artifact_type}"

    @property
    def expected_media_type(self) -> str:
        return _MEDIA_TYPE_BY_ARTIFACT[self.output_artifact_type]

    def stage(self, workspace: Path) -> dict[str, object]:
        """Copy approved inputs and atomically write ``report_job.json``."""
        workspace_resolved = workspace.resolve()
        input_dir = (workspace / "input").resolve()
        try:
            input_dir.relative_to(workspace_resolved)
        except ValueError as exc:
            raise ValueError("runtime input directory escapes workspace") from exc
        if not input_dir.is_dir():
            raise ValueError("runtime input directory is missing")

        staged_template: dict[str, object] | None = None
        total_bytes = 0
        if self.normalized_template_path is not None:
            destination = input_dir / f"template.{self.output_artifact_type}"
            digest, size = _copy_input_file(
                self.normalized_template_path, destination
            )
            _validate_normalized_template(
                destination, self.output_artifact_type
            )
            total_bytes += size
            if total_bytes > MAX_ARTIFACTS_TOTAL_BYTES:
                raise ValueError("staged report inputs exceed total size limit")
            staged_template = {
                "relative_path": (
                    f"input/template.{self.output_artifact_type}"
                ),
                "sha256": digest,
            }

        staged_assets: list[dict[str, object]] = []
        if self.assets:
            assets_dir = input_dir / "assets"
            assets_dir.mkdir(parents=False, exist_ok=False)
            for asset in self.assets:
                suffix = asset.source_path.suffix.lower()
                destination = assets_dir / f"{asset.host_id}{suffix}"
                digest, size = _copy_input_file(asset.source_path, destination)
                _validate_staged_asset(destination, asset.media_type)
                total_bytes += size
                if total_bytes > MAX_ARTIFACTS_TOTAL_BYTES:
                    raise ValueError("staged report inputs exceed total size limit")
                staged_assets.append({
                    "host_id": asset.host_id,
                    "relative_path": (
                        f"input/assets/{asset.host_id}{suffix}"
                    ),
                    "sha256": digest,
                    "media_type": asset.media_type,
                    "semantic_label": asset.semantic_label,
                    "target": asset.target,
                })

        wire: dict[str, object] = {
            "schema_version": REPORT_JOB_SCHEMA_VERSION,
            "report_type": self.report_type,
            "output_artifact_type": self.output_artifact_type,
            "report": json.loads(self._report_json),
            "template": staged_template,
            "assets": staged_assets,
            "required_figure_ids": list(self.required_figure_ids),
            "supplementary_figure_ids": list(self.supplementary_figure_ids),
            "inclusion_summary": json.loads(self._inclusion_json),
            "expected_output": self.expected_output,
        }
        _write_json_atomic(input_dir / "report_job.json", wire)
        return {
            "job_path": REPORT_JOB_RELATIVE_PATH,
            "output_artifact_type": self.output_artifact_type,
            "expected_output": self.expected_output,
            "contract_version": REPORT_BACKEND_CONTRACT_VERSION,
        }


def validate_report_backend_worker_output(
    job: ReportBackendJobV1,
    *,
    provider_id: str,
    provider_version: str,
    result: dict[str, object] | None,
    declarations: tuple[ArtifactDeclaration, ...],
) -> tuple[str, ...]:
    """Validate provider provenance, figure coverage and one-document output."""
    if result is None:
        raise ValueError("report backend result is missing")
    expected_result_keys = {
        "provider_id",
        "provider_version",
        "contract_version",
        "placed_figure_ids",
        "supplementary_figure_ids",
        "warnings",
    }
    if set(result) != expected_result_keys:
        raise ValueError("report backend result contains unknown or missing fields")
    if result.get("provider_id") != provider_id:
        raise ValueError("report backend provider_id mismatch")
    if result.get("provider_version") != provider_version:
        raise ValueError("report backend provider_version mismatch")
    contract_version = result.get("contract_version")
    if (
        isinstance(contract_version, bool)
        or contract_version != REPORT_BACKEND_CONTRACT_VERSION
    ):
        raise ValueError("report backend contract_version mismatch")

    placed = _string_list(result.get("placed_figure_ids"), "placed_figure_ids")
    supplementary = _string_list(
        result.get("supplementary_figure_ids"),
        "supplementary_figure_ids",
    )
    _validate_unique_ids(placed, "placed figure")
    _validate_unique_ids(supplementary, "supplementary figure")
    if set(placed) != set(job.required_figure_ids):
        raise ValueError("placed_figure_ids do not cover required figures exactly")
    if not set(supplementary).issubset(set(job.supplementary_figure_ids)):
        raise ValueError("supplementary_figure_ids contain unknown figures")
    if set(placed) & set(supplementary):
        raise ValueError("placed and supplementary figure IDs overlap")

    warnings = _string_list(result.get("warnings"), "warnings")
    if len(declarations) != 1:
        raise ValueError("report backend must declare exactly one document")
    declaration = declarations[0]
    if declaration.declared_path != job.expected_declared_path:
        raise ValueError("report backend declared path does not match expected output")
    if declaration.kind != "document":
        raise ValueError("report backend artifact kind must be document")
    if declaration.media_type_hint != job.expected_media_type:
        raise ValueError("report backend artifact media_type does not match format")
    return warnings


def validate_report_backend_workspace_output(
    job: ReportBackendJobV1,
    workspace: Path,
) -> None:
    """Require one top-level output file and no hidden auxiliary outputs."""
    output_dir = workspace / "output"
    try:
        entries = tuple(output_dir.iterdir())
    except OSError as exc:
        raise ValueError("report backend output directory is unavailable") from exc
    if len(entries) != 1 or entries[0].name != job.expected_declared_path:
        raise ValueError("report backend workspace must contain only expected output")


def validate_published_report_artifact(
    job: ReportBackendJobV1,
    artifact: RuntimeArtifact,
    *,
    provider_id: str,
    provider_version: str,
    task_id: str,
) -> None:
    """Defence-in-depth validation after ArtifactPublisher content sniffing."""
    if artifact.relative_path != job.expected_declared_path:
        raise ValueError("published report path mismatch")
    if artifact.media_type != job.expected_media_type:
        raise ValueError("published report OOXML type mismatch")
    if artifact.kind != "document":
        raise ValueError("published report kind mismatch")
    if (
        artifact.skill_id != provider_id
        or artifact.version != provider_version
        or artifact.task_id != task_id
    ):
        raise ValueError("published report owner mismatch")
    if not artifact.sha256 or artifact.size_bytes <= 0:
        raise ValueError("published report integrity metadata is incomplete")


def _validate_unique_ids(values: tuple[str, ...], label: str) -> None:
    for value in values:
        if not isinstance(value, str) or not _SAFE_ID_RE.fullmatch(value):
            raise ValueError(f"{label} ID is invalid")
    if len(values) != len(set(values)):
        raise ValueError(f"{label} IDs must be unique")


def _string_list(value: object, field_name: str) -> tuple[str, ...]:
    if not isinstance(value, list) or any(
        not isinstance(item, str) for item in value
    ):
        raise ValueError(f"{field_name} must be a string list")
    return tuple(value)


def _canonical_json(value: object, field_name: str) -> str:
    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field_name} must be JSON-compatible") from exc


def _reject_forbidden_wire_values(value: object) -> None:
    if isinstance(value, dict):
        for key, item in value.items():
            if not isinstance(key, str):
                raise ValueError("report job keys must be strings")
            normalized = str(key).strip().lower().replace("-", "_")
            if normalized in _FORBIDDEN_JOB_KEYS:
                raise ValueError("report job contains a forbidden field")
            _reject_forbidden_wire_values(item)
    elif isinstance(value, (list, tuple)):
        for item in value:
            _reject_forbidden_wire_values(item)
    elif isinstance(value, str) and _looks_like_absolute_path(value):
        raise ValueError("report job contains an absolute path")


def _looks_like_absolute_path(value: str) -> bool:
    stripped = value.strip()
    if not stripped:
        return False
    return (
        PureWindowsPath(stripped).is_absolute()
        or PurePosixPath(stripped).is_absolute()
        or stripped.startswith("\\\\")
    )


def _copy_input_file(source: Path, destination: Path) -> tuple[str, int]:
    """Copy one regular, non-linked input and return its SHA-256 and size."""
    try:
        source_stat = source.lstat()
    except OSError as exc:
        raise ValueError("report input source is unavailable") from exc
    file_attributes = getattr(source_stat, "st_file_attributes", 0)
    if stat.S_ISLNK(source_stat.st_mode) or file_attributes & 0x400:
        raise ValueError("report input source must not be a symlink or reparse point")
    if not stat.S_ISREG(source_stat.st_mode):
        raise ValueError("report input source must be a regular file")
    if source_stat.st_nlink > 1:
        raise ValueError("report input source must not be hard-linked")
    if source_stat.st_size > MAX_ARTIFACT_FILE_BYTES:
        raise ValueError("report input source exceeds single-file size limit")

    digest = hashlib.sha256()
    copied = 0
    try:
        with source.open("rb") as src, destination.open("xb") as dest:
            opened_stat = os.fstat(src.fileno())
            if (
                opened_stat.st_dev != source_stat.st_dev
                or opened_stat.st_ino != source_stat.st_ino
                or opened_stat.st_size != source_stat.st_size
            ):
                raise ValueError("report input source changed before staging")
            while True:
                chunk = src.read(1024 * 1024)
                if not chunk:
                    break
                copied += len(chunk)
                if copied > MAX_ARTIFACT_FILE_BYTES:
                    raise ValueError("report input source exceeds size limit")
                digest.update(chunk)
                dest.write(chunk)
            dest.flush()
            os.fsync(dest.fileno())
            final_stat = os.fstat(src.fileno())
            if (
                final_stat.st_size != opened_stat.st_size
                or final_stat.st_mtime_ns != opened_stat.st_mtime_ns
            ):
                raise ValueError("report input source changed during staging")
    except Exception:
        destination.unlink(missing_ok=True)
        raise
    return digest.hexdigest(), copied


def _write_json_atomic(path: Path, value: dict[str, object]) -> None:
    payload = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        indent=2,
        allow_nan=False,
    )
    if len(payload.encode("utf-8")) > MAX_REPORT_JOB_JSON_BYTES:
        raise ValueError("report_job.json exceeds the size limit")
    temp_path = path.with_name(f".{path.name}.tmp")
    try:
        with temp_path.open("x", encoding="utf-8", newline="\n") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_path, path)
    except Exception:
        temp_path.unlink(missing_ok=True)
        raise


def _validate_staged_asset(path: Path, media_type: str) -> None:
    with path.open("rb") as handle:
        header = handle.read(8)
    valid = (
        media_type == "image/png" and header == b"\x89PNG\r\n\x1a\n"
    ) or (
        media_type == "image/jpeg" and header.startswith(b"\xff\xd8\xff")
    )
    if not valid:
        path.unlink(missing_ok=True)
        raise ValueError("staged report asset content does not match media_type")


def _validate_normalized_template(path: Path, artifact_type: str) -> None:
    required_prefix = "ppt/" if artifact_type == "pptx" else "word/"
    try:
        with zipfile.ZipFile(path, "r") as archive:
            names = archive.namelist()
            valid = (
                "[Content_Types].xml" in names
                and any(name.startswith(required_prefix) for name in names)
            )
    except zipfile.BadZipFile as exc:
        path.unlink(missing_ok=True)
        raise ValueError("normalized template is not valid OOXML") from exc
    if not valid:
        path.unlink(missing_ok=True)
        raise ValueError("normalized template OOXML structure is invalid")
