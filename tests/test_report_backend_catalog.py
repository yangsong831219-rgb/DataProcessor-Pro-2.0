"""Batch 3.5.1 tests for report_backend manifest semantics and catalog.

This batch is discovery-only: it classifies installed report backends but
does not execute them or connect them to report generation.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from dp_engine.skills.models import InstalledSkill, SkillManifest
from dp_engine.skills.registry import SkillRegistry


SAFE_CAPABILITIES = (
    "read_skill_files",
    "read_runtime_workspace",
    "write_runtime_workspace",
)


def _manifest(
    *,
    skill_id: str = "ppt-master",
    version: str = "1.0.0",
    skill_type: str = "report_backend",
    artifact_types: tuple[str, ...] = ("pptx",),
    capabilities: tuple[str, ...] = SAFE_CAPABILITIES,
    entrypoints: dict[str, str] | None = None,
    extensions: dict[str, object] | None = None,
) -> SkillManifest:
    return SkillManifest(
        schema_version=1,
        skill_id=skill_id,
        name=skill_id,
        version=version,
        description="test report backend",
        skill_type=skill_type,
        artifact_types=artifact_types,
        capabilities=capabilities,
        entrypoints=(
            {"generate": "workflows/generate.py:generate"}
            if entrypoints is None
            else entrypoints
        ),
        extensions=(
            {
                "report_backend_contract": 1,
                "template_modes": ["none", "normalized"],
            }
            if extensions is None
            else extensions
        ),
    )


def _issue_codes(manifest: SkillManifest) -> set[str]:
    from dp_engine.report_backend.models import validate_report_backend_manifest

    return {issue.code for issue in validate_report_backend_manifest(manifest)}


class TestReportBackendManifestSemantics:
    def test_valid_ppt_backend_has_no_issues(self):
        assert _issue_codes(_manifest()) == set()

    def test_valid_multi_format_backend_has_no_issues(self):
        manifest = _manifest(artifact_types=("docx", "pptx"))
        assert _issue_codes(manifest) == set()

    @pytest.mark.parametrize(
        ("extensions", "expected_code"),
        [
            ({"template_modes": ["none"]}, "CONTRACT_MISSING"),
            (
                {"report_backend_contract": True, "template_modes": ["none"]},
                "CONTRACT_INVALID",
            ),
            (
                {"report_backend_contract": 2, "template_modes": ["none"]},
                "CONTRACT_UNSUPPORTED",
            ),
            ({"report_backend_contract": 1}, "TEMPLATE_MODES_MISSING"),
            (
                {"report_backend_contract": 1, "template_modes": "none"},
                "TEMPLATE_MODES_INVALID",
            ),
            (
                {"report_backend_contract": 1, "template_modes": ["legacy"]},
                "TEMPLATE_MODES_UNSUPPORTED",
            ),
        ],
    )
    def test_contract_and_template_mode_errors_are_typed(
        self,
        extensions: dict[str, object],
        expected_code: str,
    ):
        assert expected_code in _issue_codes(_manifest(extensions=extensions))

    def test_generate_entrypoint_is_required(self):
        assert "GENERATE_ENTRYPOINT_MISSING" in _issue_codes(
            _manifest(entrypoints={"run": "run.py:run"})
        )

    def test_generate_entrypoint_must_be_callable_reference(self):
        assert "GENERATE_ENTRYPOINT_INVALID" in _issue_codes(
            _manifest(entrypoints={"generate": "workflows/generate.py"})
        )

    def test_artifact_type_is_required(self):
        assert "ARTIFACT_TYPES_MISSING" in _issue_codes(
            _manifest(artifact_types=())
        )

    def test_unknown_artifact_type_is_rejected(self):
        assert "ARTIFACT_TYPES_UNSUPPORTED" in _issue_codes(
            _manifest(artifact_types=("pdf", "pptx"))
        )

    def test_privileged_capability_is_rejected(self):
        assert "CAPABILITIES_UNSUPPORTED" in _issue_codes(
            _manifest(capabilities=SAFE_CAPABILITIES + ("subprocess",))
        )

    def test_non_report_backend_is_rejected_by_semantic_validator(self):
        assert "NOT_REPORT_BACKEND" in _issue_codes(
            _manifest(skill_type="instruction")
        )


def _write_backend_package(
    root: Path,
    *,
    contract: str = "report_backend_contract: 1",
    template_modes: str = "template_modes:\n  - none\n  - normalized",
    generate: str = "workflows/generate.py:generate",
    artifact_types: str = "  - pptx",
    capabilities: str = (
        "  - read_skill_files\n"
        "  - read_runtime_workspace\n"
        "  - write_runtime_workspace"
    ),
) -> None:
    workflows = root / "workflows"
    workflows.mkdir(parents=True)
    (workflows / "generate.py").write_text(
        "def generate(context):\n    return {'status': 'succeeded'}\n",
        encoding="utf-8",
    )
    (root / "SKILL.md").write_text(
        f"""---
schema_version: 1
skill_id: test-report-backend
name: Test Report Backend
version: 1.0.0
description: Test package
skill_type: report_backend
artifact_types:
{artifact_types}
capabilities:
{capabilities}
entrypoints:
  generate: {generate}
{contract}
{template_modes}
---

# Test
""",
        encoding="utf-8",
    )


class TestReportBackendPackageValidation:
    def test_callable_generate_entrypoint_validates(self, tmp_path):
        from dp_engine.skills.package_validator import SkillPackageValidator

        root = tmp_path / "valid"
        _write_backend_package(root)
        result = SkillPackageValidator().validate(root)

        assert result.valid is True, result.errors
        assert not any(
            "report_backend_contract" in warning
            or "template_modes" in warning
            for warning in result.warnings
        )

    def test_missing_contract_rejected_at_package_validation(self, tmp_path):
        from dp_engine.skills.package_validator import SkillPackageValidator

        root = tmp_path / "invalid"
        _write_backend_package(root, contract="")
        result = SkillPackageValidator().validate(root)

        assert result.valid is False
        assert any("CONTRACT_MISSING" in error for error in result.errors)

    def test_legacy_path_only_generate_rejected_at_package_validation(self, tmp_path):
        from dp_engine.skills.package_validator import SkillPackageValidator

        root = tmp_path / "legacy"
        _write_backend_package(root, generate="workflows/generate.py")
        result = SkillPackageValidator().validate(root)

        assert result.valid is False
        assert any("GENERATE_ENTRYPOINT_INVALID" in error for error in result.errors)


def _register(
    registry: SkillRegistry,
    manifest: SkillManifest,
    *,
    enabled: bool = True,
    health_status: str = "healthy",
) -> InstalledSkill:
    installed = InstalledSkill(
        manifest=manifest,
        install_path=f"C:/managed/{manifest.skill_id}/{manifest.version}",
        enabled=enabled,
        installed_at="2026-08-05T00:00:00+00:00",
        health_status=health_status,
    )
    registry.register(installed)
    return installed


class TestReportBackendCatalog:
    @pytest.fixture
    def registry(self, tmp_path) -> SkillRegistry:
        registry = SkillRegistry(tmp_path / "registry.json")
        registry.load()
        return registry

    def test_catalog_ignores_non_report_skills(self, registry):
        from dp_engine.report_backend.catalog import ReportBackendCatalog

        _register(registry, _manifest(skill_id="instruction", skill_type="instruction"))
        assert ReportBackendCatalog(registry).list_backends() == ()

    def test_active_healthy_backend_is_compatible(self, registry):
        from dp_engine.report_backend.catalog import ReportBackendCatalog

        _register(registry, _manifest())
        registry.set_active_version("ppt-master", "1.0.0")

        descriptors = ReportBackendCatalog(registry).list_backends(
            artifact_type="pptx",
            template_mode="normalized",
        )

        assert len(descriptors) == 1
        descriptor = descriptors[0]
        assert descriptor.compatible is True
        assert descriptor.issue_codes == ()
        assert descriptor.generate_entrypoint == "workflows/generate.py:generate"
        assert descriptor.template_modes == ("none", "normalized")

    @pytest.mark.parametrize(
        ("enabled", "health_status", "activate", "expected_code"),
        [
            (False, "healthy", False, "DISABLED"),
            (True, "healthy", False, "NOT_ACTIVE"),
            (True, "unknown", True, "NOT_HEALTHY"),
            (True, "unhealthy", True, "NOT_HEALTHY"),
        ],
    )
    def test_runtime_state_is_classified(
        self,
        registry,
        enabled: bool,
        health_status: str,
        activate: bool,
        expected_code: str,
    ):
        from dp_engine.report_backend.catalog import ReportBackendCatalog

        _register(
            registry,
            _manifest(),
            enabled=enabled,
            health_status=health_status,
        )
        if activate:
            registry.set_active_version("ppt-master", "1.0.0")
        else:
            # Registry makes the first installed version active.  Switch to a
            # second version so 1.0.0 is deterministically non-active.
            _register(
                registry,
                _manifest(version="2.0.0"),
                health_status="healthy",
            )
            registry.set_active_version("ppt-master", "2.0.0")
        if not enabled:
            registry.set_enabled("ppt-master", False, "1.0.0")

        descriptor = next(
            item
            for item in ReportBackendCatalog(registry).list_backends()
            if item.version == "1.0.0"
        )
        assert descriptor.compatible is False
        assert expected_code in descriptor.issue_codes

    def test_requested_format_and_template_mode_are_classified(self, registry):
        from dp_engine.report_backend.catalog import ReportBackendCatalog

        _register(
            registry,
            _manifest(
                artifact_types=("pptx",),
                extensions={
                    "report_backend_contract": 1,
                    "template_modes": ["none"],
                },
            ),
        )
        registry.set_active_version("ppt-master", "1.0.0")

        descriptor = ReportBackendCatalog(registry).list_backends(
            artifact_type="docx",
            template_mode="normalized",
        )[0]

        assert descriptor.compatible is False
        assert "FORMAT_UNSUPPORTED" in descriptor.issue_codes
        assert "TEMPLATE_MODE_UNSUPPORTED" in descriptor.issue_codes

    def test_manifest_semantic_failure_is_fail_closed(self, registry):
        from dp_engine.report_backend.catalog import ReportBackendCatalog

        _register(registry, _manifest(extensions={}))
        registry.set_active_version("ppt-master", "1.0.0")

        descriptor = ReportBackendCatalog(registry).list_backends()[0]
        assert descriptor.compatible is False
        assert "CONTRACT_MISSING" in descriptor.issue_codes

    def test_compatible_backends_filters_and_order_is_stable(self, registry):
        from dp_engine.report_backend.catalog import ReportBackendCatalog

        for skill_id in ("z-backend", "a-backend"):
            _register(registry, _manifest(skill_id=skill_id))
            registry.set_active_version(skill_id, "1.0.0")

        catalog = ReportBackendCatalog(registry)
        assert [d.skill_id for d in catalog.compatible_backends("pptx", "none")] == [
            "a-backend",
            "z-backend",
        ]

    def test_unknown_query_values_raise(self, registry):
        from dp_engine.report_backend.catalog import ReportBackendCatalog

        catalog = ReportBackendCatalog(registry)
        with pytest.raises(ValueError, match="artifact_type"):
            catalog.list_backends(artifact_type="pdf")
        with pytest.raises(ValueError, match="template_mode"):
            catalog.list_backends(template_mode="legacy")
