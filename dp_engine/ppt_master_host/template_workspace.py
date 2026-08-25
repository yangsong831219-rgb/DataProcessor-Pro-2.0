"""Validated PPT Master template-style workspaces.

The Module accepts only an already prepared PowerPoint template, extracts a
bounded 16:9 style contract, copies the artifact into a private digest-addressed
workspace, and commits an attestation atomically.  It does not execute upstream
code and does not promise object-for-object template mirroring.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import stat
import tempfile
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from utils.app_paths import get_app_data_root
from utils.report_template_preparation import load_prepared_template_profile


_MAX_TEMPLATE_BYTES = 100 * 1024 * 1024
_MAX_MANIFEST_BYTES = 256 * 1024
_COPY_CHUNK_BYTES = 1024 * 1024


class PptMasterTemplateWorkspaceError(RuntimeError):
    """A named template-workspace admission failure."""

    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(message)


class PptMasterTemplateWorkspace(BaseModel):
    """Attestation passed from Host workflow to the report Provider."""

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        str_strip_whitespace=True,
    )

    schema_version: Literal[1] = 1
    workspace_id: str = Field(pattern=r"^tmpl-[0-9a-f]{24}$")
    workspace_path: Path
    template_path: Path
    template_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    template_size_bytes: int = Field(ge=1, le=_MAX_TEMPLATE_BYTES)
    profile_json: str = Field(min_length=2, max_length=64 * 1024)
    style_summary: str = Field(min_length=1, max_length=4_000)

    @field_validator("workspace_path", "template_path")
    @classmethod
    def _absolute_path(cls, value: Path) -> Path:
        selected = Path(value).expanduser().resolve(strict=False)
        if not selected.is_absolute():
            raise ValueError("template workspace paths must be absolute")
        return selected


def get_default_template_workspace_root() -> Path:
    return get_app_data_root() / "toolchains" / "ppt-master" / "template-workspaces"


def prepare_ppt_master_template_workspace(
    prepared_template_path: str | Path,
    *,
    workspace_root: str | Path | None = None,
) -> PptMasterTemplateWorkspace:
    """Admit one prepared 16:9 PPTX as a style-guidance workspace."""

    source = Path(prepared_template_path).expanduser().resolve(strict=False)
    if source.suffix.casefold() != ".pptx" or not source.is_file():
        raise PptMasterTemplateWorkspaceError(
            "template_file_invalid",
            "PPT Master 模板工作区仅接受已标准化的 .pptx 文件。",
        )
    if _is_reparse(source):
        raise PptMasterTemplateWorkspaceError(
            "template_path_unsafe",
            "模板路径是链接或重解析点，无法建立可信工作区。",
        )
    size = source.stat().st_size
    if size <= 0 or size > _MAX_TEMPLATE_BYTES:
        raise PptMasterTemplateWorkspaceError(
            "template_size_invalid",
            "模板为空或超过 100 MB 工作区上限。",
        )

    profile = load_prepared_template_profile(str(source))
    if not isinstance(profile, dict) or profile.get("report_type") != "ppt":
        raise PptMasterTemplateWorkspaceError(
            "prepared_profile_missing",
            "模板尚未通过软件的 PPT 检测与标准化，请重新载入模板。",
        )
    _validate_ppt169(source)

    profile_json = _canonical_json(profile)
    digest = _sha256_file(source)
    workspace_id = f"tmpl-{digest[:24]}"
    root = Path(workspace_root or get_default_template_workspace_root()).expanduser()
    root = root.resolve(strict=False)
    if root == Path(root.anchor) or _is_reparse(root):
        raise PptMasterTemplateWorkspaceError(
            "workspace_root_unsafe",
            "模板工作区根目录不安全。",
        )
    final = root / workspace_id
    style_summary = _build_style_summary(profile)
    cached = _load_workspace(
        final,
        expected_digest=digest,
        expected_size=size,
        expected_profile_json=profile_json,
        expected_summary=style_summary,
    )
    if cached is not None:
        return cached

    root.mkdir(parents=True, exist_ok=True)
    staging_parent = root / ".staging"
    staging_parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=f"{workspace_id}-", dir=staging_parent))
    try:
        template_dir = staging / "templates"
        template_dir.mkdir(parents=False, exist_ok=False)
        staged_template = template_dir / "style-template.pptx"
        copied_size, copied_digest = _copy_attested(source, staged_template)
        if copied_size != size or copied_digest != digest:
            raise PptMasterTemplateWorkspaceError(
                "template_changed",
                "模板在工作区建立期间发生变化，请重新检测。",
            )
        manifest = {
            "schema_version": 1,
            "workspace_id": workspace_id,
            "template_file": "templates/style-template.pptx",
            "template_sha256": digest,
            "template_size_bytes": size,
            "profile": json.loads(profile_json),
            "style_summary": style_summary,
            "reuse_scope": "style",
            "canvas": "ppt169",
        }
        (staging / "template-workspace.json").write_text(
            _canonical_json(manifest),
            encoding="utf-8",
            newline="\n",
        )
        if final.exists():
            cached = _load_workspace(
                final,
                expected_digest=digest,
                expected_size=size,
                expected_profile_json=profile_json,
                expected_summary=style_summary,
            )
            if cached is None:
                raise PptMasterTemplateWorkspaceError(
                    "workspace_conflict",
                    "同指纹模板工作区已存在但未通过完整性检查。",
                )
            return cached
        os.replace(staging, final)
        return _load_workspace_required(final)
    finally:
        if staging.exists():
            shutil.rmtree(staging, ignore_errors=True)


def _load_workspace(
    workspace: Path,
    *,
    expected_digest: str,
    expected_size: int,
    expected_profile_json: str,
    expected_summary: str,
) -> PptMasterTemplateWorkspace | None:
    try:
        result = _load_workspace_required(workspace)
    except PptMasterTemplateWorkspaceError:
        return None
    if (
        result.template_sha256 != expected_digest
        or result.template_size_bytes != expected_size
        or result.profile_json != expected_profile_json
        or result.style_summary != expected_summary
    ):
        return None
    return result


def _load_workspace_required(workspace: Path) -> PptMasterTemplateWorkspace:
    manifest_path = workspace / "template-workspace.json"
    template_path = workspace / "templates" / "style-template.pptx"
    if (
        not workspace.is_dir()
        or _is_reparse(workspace)
        or not manifest_path.is_file()
        or _is_reparse(manifest_path)
        or not template_path.is_file()
        or _is_reparse(template_path)
    ):
        raise PptMasterTemplateWorkspaceError(
            "workspace_invalid",
            "模板工作区结构不完整。",
        )
    if manifest_path.stat().st_size > _MAX_MANIFEST_BYTES:
        raise PptMasterTemplateWorkspaceError(
            "workspace_manifest_limit",
            "模板工作区清单超过大小限制。",
        )
    try:
        payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError) as error:
        raise PptMasterTemplateWorkspaceError(
            "workspace_manifest_invalid",
            "模板工作区清单无法读取。",
        ) from error
    if (
        payload.get("schema_version") != 1
        or payload.get("template_file") != "templates/style-template.pptx"
        or payload.get("reuse_scope") != "style"
        or payload.get("canvas") != "ppt169"
    ):
        raise PptMasterTemplateWorkspaceError(
            "workspace_contract_invalid",
            "模板工作区契约不兼容。",
        )
    size = template_path.stat().st_size
    digest = _sha256_file(template_path)
    if size != payload.get("template_size_bytes") or digest != payload.get(
        "template_sha256"
    ):
        raise PptMasterTemplateWorkspaceError(
            "workspace_integrity_mismatch",
            "模板工作区文件与清单摘要不一致。",
        )
    profile = payload.get("profile")
    if not isinstance(profile, dict):
        raise PptMasterTemplateWorkspaceError(
            "workspace_profile_invalid",
            "模板工作区样式契约缺失。",
        )
    return PptMasterTemplateWorkspace(
        workspace_id=str(payload.get("workspace_id") or ""),
        workspace_path=workspace,
        template_path=template_path,
        template_sha256=digest,
        template_size_bytes=size,
        profile_json=_canonical_json(profile),
        style_summary=str(payload.get("style_summary") or ""),
    )


def _validate_ppt169(source: Path) -> None:
    try:
        from pptx import Presentation

        presentation = Presentation(str(source))
        width = int(presentation.slide_width or 0)
        height = int(presentation.slide_height or 0)
    except Exception as error:
        raise PptMasterTemplateWorkspaceError(
            "template_open_failed",
            "标准化模板无法重新打开。",
        ) from error
    ratio = width / height if height else 0.0
    if width <= 0 or height <= 0 or abs(ratio - (16 / 9)) > 0.02:
        raise PptMasterTemplateWorkspaceError(
            "canvas_unsupported",
            "PPT Master 当前仅支持 16:9 模板；请先转换页面尺寸。",
        )


def _build_style_summary(profile: dict[str, Any]) -> str:
    title = profile.get("title_style") if isinstance(profile, dict) else {}
    body = profile.get("body_style") if isinstance(profile, dict) else {}
    geometry = profile.get("safe_geometry") if isinstance(profile, dict) else {}
    summary = {
        "reuse_scope": "style",
        "canvas": "16:9",
        "content_contract": profile.get("content_contract"),
        "cover_contract": profile.get("cover_contract"),
        "title_style": title if isinstance(title, dict) else {},
        "body_style": body if isinstance(body, dict) else {},
        "safe_geometry": geometry if isinstance(geometry, dict) else {},
    }
    return "PPT 模板样式工作区（不复制样例对象）：" + _canonical_json(summary)


def _copy_attested(source: Path, target: Path) -> tuple[int, str]:
    digest = hashlib.sha256()
    total = 0
    with source.open("rb") as input_file, target.open("xb") as output_file:
        while chunk := input_file.read(_COPY_CHUNK_BYTES):
            output_file.write(chunk)
            digest.update(chunk)
            total += len(chunk)
        output_file.flush()
        os.fsync(output_file.fileno())
    return total, digest.hexdigest()


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        while chunk := source.read(_COPY_CHUNK_BYTES):
            digest.update(chunk)
    return digest.hexdigest()


def _canonical_json(value: object) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def _is_reparse(path: Path) -> bool:
    try:
        info = path.lstat()
    except OSError:
        return False
    if stat.S_ISLNK(info.st_mode):
        return True
    return bool(getattr(info, "st_file_attributes", 0) & 0x400)
