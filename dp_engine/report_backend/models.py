"""Immutable report_backend discovery models and manifest semantics.

This module performs pure validation only.  It never reads the filesystem,
loads a plugin, mutates the registry, or generates a report.
"""

from __future__ import annotations

from dataclasses import dataclass

from dp_engine.skills.models import SkillManifest, parse_entrypoint_ref


REPORT_BACKEND_CONTRACT_VERSION = 1
REPORT_BACKEND_GENERATE_ENTRYPOINT = "generate"

SUPPORTED_REPORT_ARTIFACT_TYPES: frozenset[str] = frozenset({"pptx", "docx"})
SUPPORTED_TEMPLATE_MODES: frozenset[str] = frozenset({"none", "normalized"})

# Batch 3.5.1 keeps the same least-privilege boundary as SkillRuntime.  A
# backend that asks for shell/network/subprocess is not a compatible candidate.
ALLOWED_REPORT_BACKEND_CAPABILITIES: frozenset[str] = frozenset({
    "read_skill_files",
    "read_runtime_workspace",
    "write_runtime_workspace",
})

REPORT_BACKEND_EXTENSION_KEYS: frozenset[str] = frozenset({
    "report_backend_contract",
    "template_modes",
})


@dataclass(frozen=True, order=True)
class ReportBackendIssue:
    """Stable machine-readable incompatibility with a safe user message."""

    code: str
    message: str


@dataclass(frozen=True)
class ReportBackendDescriptor:
    """Read-only catalog view of one installed report backend version."""

    skill_id: str
    name: str
    version: str
    artifact_types: tuple[str, ...]
    template_modes: tuple[str, ...]
    generate_entrypoint: str
    capabilities: tuple[str, ...]
    enabled: bool
    is_active: bool
    health_status: str
    issues: tuple[ReportBackendIssue, ...] = ()

    @property
    def compatible(self) -> bool:
        """True only when manifest, registry state and query all pass."""
        return not self.issues

    @property
    def issue_codes(self) -> tuple[str, ...]:
        """Stable issue codes for UI/controller consumption."""
        return tuple(issue.code for issue in self.issues)


def extract_report_backend_template_modes(
    manifest: SkillManifest,
) -> tuple[str, ...]:
    """Return normalized, supported template modes from manifest extensions."""
    raw = manifest.extensions.get("template_modes")
    if not isinstance(raw, (list, tuple)):
        return ()
    modes = {
        item.strip()
        for item in raw
        if isinstance(item, str)
        and item.strip() in SUPPORTED_TEMPLATE_MODES
    }
    return tuple(sorted(modes))


def validate_report_backend_manifest(
    manifest: SkillManifest,
) -> tuple[ReportBackendIssue, ...]:
    """Validate Batch 3.5 report_backend manifest semantics.

    Generic SkillManifest parsing deliberately remains forward-compatible.
    This stricter validator is used by package installation and catalog
    discovery so malformed report backends fail closed without affecting
    instruction/executable skills.
    """
    issues: list[ReportBackendIssue] = []

    if manifest.skill_type != "report_backend":
        return (
            ReportBackendIssue(
                "NOT_REPORT_BACKEND",
                "skill_type 必须为 report_backend。",
            ),
        )

    contract = manifest.extensions.get("report_backend_contract")
    if contract is None:
        issues.append(ReportBackendIssue(
            "CONTRACT_MISSING",
            "缺少 report_backend_contract。",
        ))
    elif isinstance(contract, bool) or not isinstance(contract, int):
        issues.append(ReportBackendIssue(
            "CONTRACT_INVALID",
            "report_backend_contract 必须是整数。",
        ))
    elif contract != REPORT_BACKEND_CONTRACT_VERSION:
        issues.append(ReportBackendIssue(
            "CONTRACT_UNSUPPORTED",
            "不支持的 report_backend_contract 版本。",
        ))

    generate_entrypoint = manifest.entrypoints.get(
        REPORT_BACKEND_GENERATE_ENTRYPOINT,
        "",
    ).strip()
    if not generate_entrypoint:
        issues.append(ReportBackendIssue(
            "GENERATE_ENTRYPOINT_MISSING",
            "缺少 entrypoints.generate。",
        ))
    else:
        try:
            parse_entrypoint_ref(generate_entrypoint)
        except ValueError:
            issues.append(ReportBackendIssue(
                "GENERATE_ENTRYPOINT_INVALID",
                "entrypoints.generate 必须使用 relative.py:function 格式。",
            ))

    if not manifest.artifact_types:
        issues.append(ReportBackendIssue(
            "ARTIFACT_TYPES_MISSING",
            "report_backend 至少声明 pptx 或 docx。",
        ))
    else:
        unsupported_artifacts = sorted(
            set(manifest.artifact_types) - SUPPORTED_REPORT_ARTIFACT_TYPES
        )
        if unsupported_artifacts:
            issues.append(ReportBackendIssue(
                "ARTIFACT_TYPES_UNSUPPORTED",
                "不支持的报告产物类型: " + ", ".join(unsupported_artifacts),
            ))

    raw_template_modes = manifest.extensions.get("template_modes")
    if raw_template_modes is None or raw_template_modes == [] or raw_template_modes == ():
        issues.append(ReportBackendIssue(
            "TEMPLATE_MODES_MISSING",
            "template_modes 必须声明 none 和/或 normalized。",
        ))
    elif not isinstance(raw_template_modes, (list, tuple)):
        issues.append(ReportBackendIssue(
            "TEMPLATE_MODES_INVALID",
            "template_modes 必须是字符串列表。",
        ))
    else:
        invalid_items = [
            item
            for item in raw_template_modes
            if not isinstance(item, str) or not item.strip()
        ]
        if invalid_items:
            issues.append(ReportBackendIssue(
                "TEMPLATE_MODES_INVALID",
                "template_modes 只能包含非空字符串。",
            ))
        unsupported_modes = sorted({
            item.strip()
            for item in raw_template_modes
            if isinstance(item, str)
            and item.strip()
            and item.strip() not in SUPPORTED_TEMPLATE_MODES
        })
        if unsupported_modes:
            issues.append(ReportBackendIssue(
                "TEMPLATE_MODES_UNSUPPORTED",
                "不支持的模板模式: " + ", ".join(unsupported_modes),
            ))

    unsupported_capabilities = sorted(
        set(manifest.capabilities) - ALLOWED_REPORT_BACKEND_CAPABILITIES
    )
    if unsupported_capabilities:
        issues.append(ReportBackendIssue(
            "CAPABILITIES_UNSUPPORTED",
            "报告后端申请了不允许的能力: "
            + ", ".join(unsupported_capabilities),
        ))

    return tuple(issues)
