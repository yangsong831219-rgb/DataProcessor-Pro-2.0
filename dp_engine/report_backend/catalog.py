"""Deterministic, read-only catalog of installed report backends."""

from __future__ import annotations

from dp_engine.report_backend.models import (
    SUPPORTED_REPORT_ARTIFACT_TYPES,
    SUPPORTED_TEMPLATE_MODES,
    ReportBackendDescriptor,
    ReportBackendIssue,
    extract_report_backend_template_modes,
    validate_report_backend_manifest,
)
from dp_engine.skills.registry import SkillRegistry


class ReportBackendCatalog:
    """Classify installed report_backend records without executing them."""

    def __init__(self, registry: SkillRegistry) -> None:
        self._registry = registry

    def list_backends(
        self,
        *,
        artifact_type: str | None = None,
        template_mode: str | None = None,
    ) -> tuple[ReportBackendDescriptor, ...]:
        """Return every report backend with deterministic compatibility issues."""
        _validate_query(artifact_type, template_mode)

        descriptors: list[ReportBackendDescriptor] = []
        # One immutable registry snapshot prevents active/enabled state from
        # changing between per-version queries while the catalog is built.
        snapshot = self._registry.snapshot()
        for (_skill_id, _version), installed in sorted(snapshot.skills.items()):
            manifest = installed.manifest
            if manifest.skill_type != "report_backend":
                continue

            issues = list(validate_report_backend_manifest(manifest))
            is_active = (
                snapshot.active_versions.get(installed.skill_id)
                == installed.version
            )

            if not installed.enabled:
                issues.append(ReportBackendIssue(
                    "DISABLED",
                    "报告后端已禁用。",
                ))
            if not is_active:
                issues.append(ReportBackendIssue(
                    "NOT_ACTIVE",
                    "报告后端不是当前 active 版本。",
                ))
            if installed.health_status != "healthy":
                issues.append(ReportBackendIssue(
                    "NOT_HEALTHY",
                    f"报告后端健康状态为 {installed.health_status}。",
                ))

            if (
                artifact_type is not None
                and artifact_type not in manifest.artifact_types
            ):
                issues.append(ReportBackendIssue(
                    "FORMAT_UNSUPPORTED",
                    f"报告后端不支持 {artifact_type}。",
                ))

            template_modes = extract_report_backend_template_modes(manifest)
            if template_mode is not None and template_mode not in template_modes:
                issues.append(ReportBackendIssue(
                    "TEMPLATE_MODE_UNSUPPORTED",
                    f"报告后端不支持模板模式 {template_mode}。",
                ))

            descriptors.append(ReportBackendDescriptor(
                skill_id=installed.skill_id,
                name=manifest.name,
                version=installed.version,
                artifact_types=manifest.artifact_types,
                template_modes=template_modes,
                generate_entrypoint=manifest.entrypoints.get("generate", ""),
                capabilities=manifest.capabilities,
                enabled=installed.enabled,
                is_active=is_active,
                health_status=installed.health_status,
                issues=tuple(issues),
            ))

        return tuple(descriptors)

    def compatible_backends(
        self,
        artifact_type: str,
        template_mode: str,
    ) -> tuple[ReportBackendDescriptor, ...]:
        """Return only backends usable for the requested format and template mode."""
        return tuple(
            descriptor
            for descriptor in self.list_backends(
                artifact_type=artifact_type,
                template_mode=template_mode,
            )
            if descriptor.compatible
        )


def _validate_query(
    artifact_type: str | None,
    template_mode: str | None,
) -> None:
    if (
        artifact_type is not None
        and artifact_type not in SUPPORTED_REPORT_ARTIFACT_TYPES
    ):
        raise ValueError(
            f"artifact_type must be one of "
            f"{sorted(SUPPORTED_REPORT_ARTIFACT_TYPES)}, got {artifact_type!r}"
        )
    if template_mode is not None and template_mode not in SUPPORTED_TEMPLATE_MODES:
        raise ValueError(
            f"template_mode must be one of "
            f"{sorted(SUPPORTED_TEMPLATE_MODES)}, got {template_mode!r}"
        )
