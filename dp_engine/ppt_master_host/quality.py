"""Structured Host interpretation of PPT Master SVG quality output."""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .controlled_runner import (
    ControlledToolResult,
    QualityStage,
    ToolArtifactAttestation,
)


_PAGE_LINE = re.compile(
    r"^\[(?P<status>OK|WARN|ERROR)\]\s+"
    r"(?P<filename>P(?P<sequence>\d{2})_[A-Za-z0-9._-]+\.svg)\s+-\s+",
)
_ISSUE_LINE = re.compile(r"^\s+\[(?P<severity>WARN|ERROR)\]\s+(?P<message>.+)$")


class _StrictQualityModel(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        str_strip_whitespace=True,
    )


class _StrictAliasModel(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        str_strip_whitespace=True,
        populate_by_name=True,
    )


class QualityIssue(_StrictQualityModel):
    severity: Literal["warning", "error"]
    rule_id: str = Field(min_length=1, max_length=100)
    message: str = Field(min_length=1, max_length=4_000)


class PageQualityReceipt(_StrictQualityModel):
    page_id: str = Field(pattern=r"^P(?:0[1-9]|[1-3][0-9]|40)$")
    sequence: int = Field(ge=1, le=40)
    svg_filename: str = Field(pattern=r"^P(?:0[1-9]|[1-3][0-9]|40)_[A-Za-z0-9._-]+\.svg$")
    status: Literal["passed", "warning", "failed"]
    issues: tuple[QualityIssue, ...] = ()


class QualityReceipt(_StrictQualityModel):
    schema_version: Literal[1] = 1
    stage: QualityStage
    run_id: str = Field(min_length=1, max_length=96)
    pages: tuple[PageQualityReceipt, ...] = Field(min_length=1, max_length=40)

    @model_validator(mode="after")
    def _validate_pages(self) -> QualityReceipt:
        if len({page.page_id for page in self.pages}) != len(self.pages):
            raise ValueError("quality receipt page IDs must be unique")
        return self

    @property
    def passed(self) -> bool:
        return all(page.status != "failed" for page in self.pages)

    @property
    def failed_pages(self) -> tuple[PageQualityReceipt, ...]:
        return tuple(page for page in self.pages if page.status == "failed")


class MethodReceipt(_StrictQualityModel):
    """Portable first-page authoring method constraints."""

    schema_version: Literal[1] = 1
    first_page_id: str = Field(pattern=r"^P01$")
    accepted_with_warnings: bool
    warning_rule_ids: tuple[str, ...] = ()


class PptxPostflightReceipt(_StrictQualityModel):
    """Path-free Host receipt for one attested PPTX postflight report."""

    schema_version: Literal[1] = 1
    run_id: str = Field(min_length=1, max_length=96)
    status: Literal["passed", "passed-with-warnings"]
    slide_count: int = Field(ge=1, le=40)
    output_bytes: int = Field(ge=1)
    warning_codes: tuple[str, ...] = ()


class _PostflightOutput(_StrictQualityModel):
    path: str = Field(min_length=1)
    bytes: int = Field(ge=1)


class _PostflightSource(_StrictQualityModel):
    svg_slide_count: int = Field(ge=1, le=40)
    layout_definition_count: int = Field(ge=0)
    fingerprint: dict[str, object]


class _PostflightPackage(_StrictQualityModel):
    zip_integrity: Literal["passed"]
    corrupt_member: None
    slides: int = Field(ge=1, le=40)
    notes: int = Field(ge=0, le=40)
    masters: int = Field(ge=1)
    layouts: int = Field(ge=1)


class _PostflightChecks(_StrictQualityModel):
    zip_integrity: Literal["passed"]
    slide_count: Literal["passed"]
    internal_relationships: Literal["enforced-at-build"]
    structured_package: Literal["enforced-at-build", "not-applicable"]
    transitions: Literal["enforced-at-build"]
    animations: Literal["enforced-at-build"]
    quality_gate: Literal["passed"]
    quality_warnings: Literal["passed", "warning"]
    template_tokens: Literal["passed", "warning"]
    external_images: Literal["passed", "warning"]
    font_portability: Literal["passed", "warning"]


class _PostflightQuality(_StrictAliasModel):
    status: Literal["loaded"]
    path: str = Field(min_length=1)
    schema_name: Literal["ppt-master.svg-quality-report.v1"] = Field(alias="schema")
    stage: Literal["final"]
    source_match: Literal["passed"]
    source_fingerprint: dict[str, object]
    summary: dict[str, object]
    categories: dict[str, object]


class _PptxPostflightReport(_StrictAliasModel):
    schema_name: Literal["ppt-master.pptx-postflight-report.v1"] = Field(
        alias="schema"
    )
    status: Literal["passed", "passed-with-warnings"]
    output: _PostflightOutput
    source: _PostflightSource
    package: _PostflightPackage
    checks: _PostflightChecks
    quality: _PostflightQuality
    resources: dict[str, object]
    backup_path: str | None
    conversion_trace_path: str | None


class QualityReceiptError(RuntimeError):
    pass


def parse_pptx_postflight_receipt(
    result: ControlledToolResult,
    *,
    project_relative: str,
    output_name: str,
    expected_slide_count: int,
) -> tuple[PptxPostflightReceipt, ToolArtifactAttestation]:
    """Validate the export report and return its path-free Host receipt."""
    output_relative = f"output/{output_name}"
    report_relative = (
        f"{project_relative}/validation/{Path(output_name).stem}.report.json"
    )
    artifacts = {artifact.relative_path: artifact for artifact in result.artifacts}
    if set(artifacts) != {output_relative, report_relative}:
        raise QualityReceiptError("postflight artifact roster is invalid")
    output_artifact = artifacts[output_relative]
    report_artifact = artifacts[report_relative]
    report_path = _attested_artifact_path(result, report_artifact)
    try:
        payload = json.loads(report_path.read_text(encoding="utf-8"))
        report = _PptxPostflightReport.model_validate(payload)
    except (OSError, UnicodeError, json.JSONDecodeError, ValueError) as error:
        raise QualityReceiptError("postflight report is invalid") from error

    workspace = Path(result.audit_dir).resolve(strict=True).parent.parent
    expected_output = (workspace / output_relative).resolve(strict=True)
    reported_output = Path(report.output.path).resolve(strict=False)
    if (
        reported_output != expected_output
        or report.output.bytes != output_artifact.size_bytes
        or report.source.svg_slide_count != expected_slide_count
        or report.package.slides != expected_slide_count
    ):
        raise QualityReceiptError("postflight report does not match the export")

    warning_codes = tuple(
        name
        for name, value in (
            ("quality_warnings", report.checks.quality_warnings),
            ("template_tokens", report.checks.template_tokens),
            ("external_images", report.checks.external_images),
            ("font_portability", report.checks.font_portability),
        )
        if value == "warning"
    )
    return (
        PptxPostflightReceipt(
            run_id=result.run_id,
            status=report.status,
            slide_count=report.package.slides,
            output_bytes=report.output.bytes,
            warning_codes=warning_codes,
        ),
        output_artifact,
    )


def parse_quality_receipt(
    result: ControlledToolResult,
    *,
    stage: QualityStage,
) -> QualityReceipt:
    """Parse one attested Controlled Run stdout into a strict receipt."""
    stdout_path = _attested_log_path(result)
    try:
        text = stdout_path.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as error:
        raise QualityReceiptError("quality stdout is unavailable") from error

    pages: list[PageQualityReceipt] = []
    current: dict[str, object] | None = None
    issues: list[QualityIssue] = []

    def flush() -> None:
        nonlocal current, issues
        if current is None:
            return
        pages.append(PageQualityReceipt.model_validate({
            **current,
            "issues": tuple(issues),
        }))
        current = None
        issues = []

    for line in text.splitlines():
        page_match = _PAGE_LINE.match(line)
        if page_match is not None:
            flush()
            status_token = page_match.group("status")
            current = {
                "page_id": f"P{page_match.group('sequence')}",
                "sequence": int(page_match.group("sequence")),
                "svg_filename": page_match.group("filename"),
                "status": {
                    "OK": "passed",
                    "WARN": "warning",
                    "ERROR": "failed",
                }[status_token],
            }
            continue
        issue_match = _ISSUE_LINE.match(line)
        if current is None or issue_match is None:
            continue
        message = issue_match.group("message").strip()
        issues.append(QualityIssue(
            severity=(
                "error"
                if issue_match.group("severity") == "ERROR"
                else "warning"
            ),
            rule_id=_quality_rule_id(message),
            message=message,
        ))
    flush()
    if not pages:
        raise QualityReceiptError("quality stdout contained no page results")
    return QualityReceipt(stage=stage, run_id=result.run_id, pages=tuple(pages))


def quality_report_artifact_path(
    result: ControlledToolResult,
) -> Path:
    """Return the sole attested machine-readable report for a quality run."""
    if len(result.artifacts) != 1:
        raise QualityReceiptError("quality report artifact roster is invalid")
    artifact = result.artifacts[0]
    expected_suffix = f"/validation/{result.run_id}.quality.json"
    if not artifact.relative_path.endswith(expected_suffix):
        raise QualityReceiptError("quality report artifact name is invalid")
    return _attested_artifact_path(result, artifact)


def method_receipt_from_first_page(receipt: QualityReceipt) -> MethodReceipt:
    if receipt.stage != QualityStage.FIRST_PAGE or len(receipt.pages) != 1:
        raise ValueError("Method Receipt requires one first-page Quality Receipt")
    page = receipt.pages[0]
    if page.page_id != "P01" or page.status == "failed":
        raise ValueError("Method Receipt requires an accepted P01")
    return MethodReceipt(
        first_page_id="P01",
        accepted_with_warnings=page.status == "warning",
        warning_rule_ids=tuple(
            sorted({issue.rule_id for issue in page.issues if issue.severity == "warning"})
        ),
    )


def _attested_log_path(result: ControlledToolResult) -> Path:
    audit_dir = Path(result.audit_dir).resolve(strict=True)
    stdout = (audit_dir.parent.parent / result.stdout.relative_path).resolve(strict=True)
    try:
        stdout.relative_to(audit_dir.parent.parent)
    except ValueError as error:
        raise QualityReceiptError("quality stdout attestation escapes workspace") from error
    if not stdout.is_file():
        raise QualityReceiptError("quality stdout attestation is not a file")
    content = stdout.read_bytes()
    if (
        len(content) != result.stdout.captured_bytes
        or hashlib.sha256(content).hexdigest() != result.stdout.log_sha256
    ):
        raise QualityReceiptError("quality stdout does not match its attestation")
    return stdout


def _attested_artifact_path(
    result: ControlledToolResult,
    artifact: ToolArtifactAttestation,
) -> Path:
    workspace = Path(result.audit_dir).resolve(strict=True).parent.parent
    path = (workspace / artifact.relative_path).resolve(strict=True)
    try:
        path.relative_to(workspace)
    except ValueError as error:
        raise QualityReceiptError("artifact attestation escapes workspace") from error
    if not path.is_file():
        raise QualityReceiptError("attested artifact is not a file")
    content = path.read_bytes()
    if (
        len(content) != artifact.size_bytes
        or hashlib.sha256(content).hexdigest() != artifact.sha256
    ):
        raise QualityReceiptError("artifact does not match its attestation")
    return path


def _quality_rule_id(message: str) -> str:
    lowered = message.casefold()
    rules = (
        ("typography_size_recurrence", "typography-size recurrence"),
        ("font_export_safety", "non-ppt-safe typeface"),
        ("top_level_group_id", "duplicate top-level group id"),
        ("semantic_stable_id", "requires a stable id"),
        ("root_group_bounds", "data-pptx-bounds"),
        ("top_level_group_structure", "top-level visible <g>"),
        ("top_level_grouping", "ungrouped top-level"),
        ("page_role", "data-pptx-page-role"),
        ("group_opacity", "group opacity"),
    )
    for rule_id, marker in rules:
        if marker in lowered:
            return rule_id
    return "quality_contract"
