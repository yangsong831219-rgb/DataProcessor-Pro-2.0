"""Batch 3.5.2 tests for the isolated ``generate_report`` runtime.

The suite exercises the real Worker and ArtifactPublisher.  It deliberately
does not connect report generation UI or the existing Word/PPT builders.
"""

from __future__ import annotations

import json
import threading
import time
import zipfile
from pathlib import Path

import pytest

from dp_engine.skills.manifest_parser import parse_skill_manifest
from dp_engine.skills.models import InstalledSkill
from dp_engine.skills.registry import SkillRegistry
from dp_engine.skills.runtime_artifacts import ArtifactStore
from dp_engine.skills.runtime_models import (
    ArtifactDeclaration,
    RUNTIME_PROTOCOL_VERSION,
    SkillRuntimeRequest,
)
from dp_engine.skills.runtime_protocol import (
    validate_artifact_declarations,
    validate_request,
)
from dp_engine.skills.runtime_errors import RuntimeProtocolError, SkillRuntimeError
from dp_engine.skills.runtime_service import SkillRuntimeService


SAFE_CAPABILITIES = (
    "read_skill_files",
    "read_runtime_workspace",
    "write_runtime_workspace",
)


def _job(*, artifact_type: str = "pptx", **overrides):
    from dp_engine.report_backend.job import ReportBackendJobV1

    values = {
        "report_type": "ppt" if artifact_type == "pptx" else "word",
        "output_artifact_type": artifact_type,
        "report": {"title": "Sensor report", "sections": []},
        "required_figure_ids": (),
        "supplementary_figure_ids": (),
        "inclusion_summary": {"included": 0, "omitted": 0},
    }
    values.update(overrides)
    return ReportBackendJobV1(**values)


def _backend_code(
    *,
    artifact_type: str = "pptx",
    declaration_path: str | None = None,
    kind: str = "document",
    fake_ooxml: bool = False,
    result_overrides: str = "",
    extra_declaration: bool = False,
    prelude: str = "",
    declare: bool = True,
) -> str:
    declared = declaration_path or f"report.{artifact_type}"
    package_dir = "ppt" if artifact_type == "pptx" else "word"
    media_type = (
        "application/vnd.openxmlformats-officedocument.presentationml.presentation"
        if artifact_type == "pptx"
        else "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    )
    write_output = (
        "report_path.write_bytes(b'not-an-ooxml-package')"
        if fake_ooxml
        else f'''with zipfile.ZipFile(report_path, "w") as archive:
        archive.writestr("[Content_Types].xml", "<Types/>")
        archive.writestr("{package_dir}/document.xml", "<document/>")'''
    )
    second = """
    extra = output / "extra.pptx"
    with zipfile.ZipFile(extra, "w") as archive:
        archive.writestr("[Content_Types].xml", "<Types/>")
        archive.writestr("ppt/presentation.xml", "<presentation/>")
    context.declare_artifact(
        "extra.pptx", display_name="Extra", media_type=
        "application/vnd.openxmlformats-officedocument.presentationml.presentation",
        kind="document",
    )
""" if extra_declaration else ""
    overrides = f"\n    result.update({result_overrides})" if result_overrides else ""
    declaration = f'''context.declare_artifact(
        {declared!r}, display_name="Professional report",
        media_type={media_type!r}, kind={kind!r},
    )''' if declare else ""
    return f'''"""Generated Batch 3.5.2 test backend."""
import json
import time
import zipfile
from pathlib import Path

def generate(context):
    workspace = Path(context["workspace_path"])
    params = context["params"]
    job_path = Path(params["job_path"])
    assert not job_path.is_absolute()
    job = json.loads((workspace / job_path).read_text(encoding="utf-8"))
    {prelude}
    output = workspace / "output"
    report_path = output / {declared!r}
    {write_output}
    {declaration}
    {second}
    result = {{
        "provider_id": context["skill_id"],
        "provider_version": context["version"],
        "contract_version": 1,
        "placed_figure_ids": list(job["required_figure_ids"]),
        "supplementary_figure_ids": list(job["supplementary_figure_ids"]),
        "warnings": [],
    }}{overrides}
    return result
'''


def _write_backend(
    installed_dir: Path,
    skill_id: str,
    code: str,
    *,
    artifact_types: tuple[str, ...] = ("pptx",),
    template_modes: tuple[str, ...] = ("none", "normalized"),
) -> Path:
    skill_dir = installed_dir / skill_id / "1.0.0"
    workflows = skill_dir / "workflows"
    workflows.mkdir(parents=True)
    (workflows / "generate.py").write_text(code, encoding="utf-8")
    artifact_yaml = "\n".join(f"  - {item}" for item in artifact_types)
    mode_yaml = "\n".join(f"  - {item}" for item in template_modes)
    capability_yaml = "\n".join(f"  - {item}" for item in SAFE_CAPABILITIES)
    (skill_dir / "SKILL.md").write_text(
        f"""---
schema_version: 1
skill_id: {skill_id}
name: {skill_id}
version: 1.0.0
description: Batch 3.5.2 test backend
skill_type: report_backend
artifact_types:
{artifact_yaml}
capabilities:
{capability_yaml}
entrypoints:
  generate: workflows/generate.py:generate
report_backend_contract: 1
template_modes:
{mode_yaml}
---

# Test backend
""",
        encoding="utf-8",
    )
    return skill_dir


def _service_for_backend(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    skill_id: str,
    code: str,
    *,
    artifact_types: tuple[str, ...] = ("pptx",),
    template_modes: tuple[str, ...] = ("none", "normalized"),
    health_status: str = "healthy",
) -> tuple[SkillRuntimeService, SkillRegistry, Path]:
    from dp_engine.skills import runtime_paths

    skills_root = tmp_path / "skills-root"
    monkeypatch.setattr(runtime_paths, "get_skills_root", lambda: skills_root)
    installed_dir = tmp_path / "installed"
    skill_dir = _write_backend(
        installed_dir,
        skill_id,
        code,
        artifact_types=artifact_types,
        template_modes=template_modes,
    )
    registry = SkillRegistry(tmp_path / "registry.json")
    registry.load()
    registry.register(InstalledSkill(
        manifest=parse_skill_manifest(skill_dir),
        install_path=str(skill_dir),
        enabled=True,
        installed_at="2026-08-05T00:00:00+00:00",
        health_status=health_status,
    ))
    registry.set_active_version(skill_id, "1.0.0")
    registry.save()
    service = SkillRuntimeService(
        registry,
        installed_dir,
        cancel_grace_ms=100,
        terminate_grace_ms=100,
        kill_grace_ms=100,
        poll_interval_ms=20,
    )
    return service, registry, skills_root


class TestReportBackendJob:
    def test_stages_only_relative_paths_hashes_and_fixed_output(self, tmp_path):
        from dp_engine.report_backend.job import ReportBackendAssetInput

        template = tmp_path / "normalized.pptx"
        with zipfile.ZipFile(template, "w") as archive:
            archive.writestr("[Content_Types].xml", "<Types/>")
            archive.writestr("ppt/presentation.xml", "<presentation/>")
        chart = tmp_path / "chart.png"
        chart.write_bytes(b"\x89PNG\r\n\x1a\nchart")
        workspace = tmp_path / "workspace"
        for name in ("input", "output", "temp"):
            (workspace / name).mkdir(parents=True, exist_ok=True)

        job = _job(
            normalized_template_path=template,
            assets=(ReportBackendAssetInput(
                host_id="figure-1",
                source_path=chart,
                media_type="image/png",
                semantic_label="temperature regression",
                target="calibration/temperature",
            ),),
            required_figure_ids=("figure-1",),
        )
        params = job.stage(workspace)
        wire = json.loads((workspace / "input" / "report_job.json").read_text("utf-8"))

        assert params == {
            "job_path": "input/report_job.json",
            "output_artifact_type": "pptx",
            "expected_output": "output/report.pptx",
            "contract_version": 1,
        }
        assert wire["schema_version"] == 1
        assert wire["expected_output"] == "output/report.pptx"
        assert wire["template"]["relative_path"] == "input/template.pptx"
        assert len(wire["template"]["sha256"]) == 64
        assert wire["assets"][0]["relative_path"] == "input/assets/figure-1.png"
        assert len(wire["assets"][0]["sha256"]) == 64
        serialized = json.dumps(wire, ensure_ascii=False)
        assert str(tmp_path) not in serialized

    @pytest.mark.parametrize(
        "overrides,match",
        [
            ({"report_type": "word"}, "report_type"),
            ({"report": {"api_key": "secret"}}, "forbidden"),
            ({"report": {"source": "D:/private/data.json"}}, "absolute path"),
            ({"required_figure_ids": ("missing",)}, "figure"),
        ],
    )
    def test_invalid_or_sensitive_job_fails_closed(self, overrides, match):
        with pytest.raises(ValueError, match=match):
            _job(**overrides)


class TestGenerateReportProtocol:
    def test_request_and_declaration_accept_dedicated_operation(self, tmp_path):
        request = SkillRuntimeRequest(
            protocol_version=RUNTIME_PROTOCOL_VERSION,
            task_id="abc123def456",
            operation="generate_report",
            skill_id="ppt-master",
            version="1.0.0",
            installed_path=str(tmp_path.resolve()),
            entrypoint="workflows/generate.py:generate",
            workspace_path=str((tmp_path / "workspace").resolve()),
            timeout_seconds=5.0,
            capabilities=SAFE_CAPABILITIES,
            environment={},
            params={
                "job_path": "input/report_job.json",
                "output_artifact_type": "pptx",
                "expected_output": "output/report.pptx",
                "contract_version": 1,
            },
        )
        validate_request(request)
        declaration = ArtifactDeclaration(
            declared_path="report.pptx",
            display_name="Report",
            media_type_hint=(
                "application/vnd.openxmlformats-officedocument.presentationml.presentation"
            ),
            kind="document",
            observed_size_bytes=1,
            observed_sha256="a" * 64,
        )
        assert validate_artifact_declarations(
            [declaration.to_wire_dict()], "generate_report"
        ) == (declaration,)

    def test_request_rejects_freeform_or_absolute_report_params(self, tmp_path):
        request = SkillRuntimeRequest(
            protocol_version=RUNTIME_PROTOCOL_VERSION,
            task_id="abc123def456",
            operation="generate_report",
            skill_id="ppt-master",
            version="1.0.0",
            installed_path=str(tmp_path.resolve()),
            entrypoint="workflows/generate.py:generate",
            workspace_path=str((tmp_path / "workspace").resolve()),
            timeout_seconds=5.0,
            capabilities=SAFE_CAPABILITIES,
            environment={},
            params={
                "job_path": "D:/private/report_job.json",
                "output_artifact_type": "pptx",
                "expected_output": "output/report.pptx",
                "contract_version": 1,
                "command": "render",
            },
        )
        with pytest.raises(RuntimeProtocolError, match="fixed report job envelope"):
            validate_request(request)


class TestGenerateReportRuntime:
    @pytest.mark.parametrize("artifact_type", ["pptx", "docx"])
    def test_real_worker_publishes_one_verified_document(
        self, tmp_path, monkeypatch, artifact_type,
    ):
        skill_id = f"professional-{artifact_type}"
        service, registry, skills_root = _service_for_backend(
            tmp_path,
            monkeypatch,
            skill_id,
            _backend_code(artifact_type=artifact_type),
            artifact_types=(artifact_type,),
        )
        registry_before = (tmp_path / "registry.json").read_bytes()

        response = service.run_report_backend(
            skill_id, "1.0.0", _job(artifact_type=artifact_type),
            timeout_seconds=5.0,
        )

        assert response.success is True
        assert response.status == "succeeded"
        assert response.operation == "generate_report"
        assert len(response.artifacts) == 1
        artifact = response.artifacts[0]
        assert artifact.relative_path == f"report.{artifact_type}"
        assert artifact.kind == "document"
        assert artifact.skill_id == skill_id
        assert artifact.version == "1.0.0"
        assert artifact.task_id == response.task_id
        assert ArtifactStore().list_task(skill_id, response.task_id) == (artifact,)
        assert (tmp_path / "registry.json").read_bytes() == registry_before
        assert not (skills_root / "runtime" / response.task_id).exists()
        manifest = json.loads(
            (skills_root / "artifacts" / skill_id / response.task_id / "manifest.json")
            .read_text("utf-8")
        )
        assert manifest["operation"] == "generate_report"

    @pytest.mark.parametrize(
        "code,expected_status",
        [
            (_backend_code(fake_ooxml=True), "protocol_error"),
            (_backend_code(kind="data"), "protocol_error"),
            (_backend_code(declaration_path="other.pptx"), "protocol_error"),
            (_backend_code(extra_declaration=True), "protocol_error"),
            (_backend_code(declare=False), "protocol_error"),
            (
                _backend_code(result_overrides="{'placed_figure_ids': []}"),
                "protocol_error",
            ),
        ],
    )
    def test_invalid_output_fails_closed(
        self, tmp_path, monkeypatch, code, expected_status,
    ):
        service, _, skills_root = _service_for_backend(
            tmp_path, monkeypatch, "invalid-output", code,
        )
        job = _job(
            assets=(),
            required_figure_ids=(),
        )
        if "result.update({'placed_figure_ids': []})" in code:
            # The backend must not be able to hide a required figure.
            from dp_engine.report_backend.job import ReportBackendAssetInput

            chart = tmp_path / "required.png"
            chart.write_bytes(b"\x89PNG\r\n\x1a\nchart")
            job = _job(
                assets=(ReportBackendAssetInput(
                    host_id="required",
                    source_path=chart,
                    media_type="image/png",
                    semantic_label="required",
                    target="slide-1",
                ),),
                required_figure_ids=("required",),
            )

        response = service.run_report_backend(
            "invalid-output", "1.0.0", job, timeout_seconds=5.0,
        )

        assert response.success is False
        assert response.status == expected_status
        assert response.artifacts == ()
        assert not (
            skills_root / "artifacts" / "invalid-output" / response.task_id
        ).exists()

    @pytest.mark.parametrize(
        "state,match",
        [
            ("disabled", "enabled"),
            ("unhealthy", "healthy"),
            ("format", "pptx"),
            ("template", "normalized"),
        ],
    )
    def test_catalog_state_and_capability_are_runtime_gates(
        self, tmp_path, monkeypatch, state, match,
    ):
        artifact_types = ("docx",) if state == "format" else ("pptx",)
        template_modes = ("none",) if state == "template" else ("none", "normalized")
        health = "unhealthy" if state == "unhealthy" else "healthy"
        service, registry, _ = _service_for_backend(
            tmp_path,
            monkeypatch,
            "gated-backend",
            _backend_code(),
            artifact_types=artifact_types,
            template_modes=template_modes,
            health_status=health,
        )
        if state == "disabled":
            registry.set_enabled("gated-backend", False, "1.0.0")
        template = None
        if state == "template":
            template = tmp_path / "normalized.pptx"
            template.write_bytes(b"template")

        with pytest.raises(SkillRuntimeError, match=match):
            service.run_report_backend(
                "gated-backend",
                "1.0.0",
                _job(normalized_template_path=template),
                timeout_seconds=5.0,
            )

    def test_timeout_and_cancel_fail_closed(self, tmp_path, monkeypatch):
        hanging = _backend_code(prelude='''
    (workspace / "temp" / "started").write_text("1", encoding="utf-8")
    time.sleep(30)
''')
        service, _, skills_root = _service_for_backend(
            tmp_path, monkeypatch, "slow-backend", hanging,
        )
        timeout_response = service.run_report_backend(
            "slow-backend", "1.0.0", _job(), timeout_seconds=0.2,
        )
        assert timeout_response.status == "timeout"
        assert timeout_response.artifacts == ()
        assert timeout_response.error is not None
        assert timeout_response.error.detail["stage"] == "execute"

        holder = []
        thread = threading.Thread(target=lambda: holder.append(
            service.run_report_backend(
                "slow-backend", "1.0.0", _job(), timeout_seconds=10.0,
            )
        ))
        thread.start()
        deadline = time.monotonic() + 5.0
        while time.monotonic() < deadline:
            if list((skills_root / "runtime").glob("*/temp/started")):
                break
            time.sleep(0.02)
        service.cancel()
        thread.join(timeout=5.0)

        assert not thread.is_alive()
        assert len(holder) == 1
        assert holder[0].status == "cancelled"
        assert holder[0].artifacts == ()

    @pytest.mark.parametrize(
        "skill_id,code,expected_status,expected_stage",
        [
            (
                "crashing-backend",
                '''import json
import os
import sys
from pathlib import Path
def generate(context):
    workspace = Path(context["workspace_path"])
    job = json.loads((workspace / "input" / "report_job.json").read_text("utf-8"))
    print(job["report"]["title"], file=sys.stderr, flush=True)
    os._exit(7)
''',
                "crashed",
                "execute",
            ),
            (
                "privileged-backend",
                '''from pathlib import Path
def generate(context):
    workspace = Path(context["workspace_path"])
    (workspace.parent / "forbidden.txt").read_text(encoding="utf-8")
    return {}
''',
                "permission_denied",
                "backend_execute",
            ),
        ],
    )
    def test_crash_and_privilege_violation_fail_closed(
        self, tmp_path, monkeypatch, skill_id, code, expected_status,
        expected_stage,
    ):
        service, _, skills_root = _service_for_backend(
            tmp_path, monkeypatch, skill_id, code,
        )
        if expected_status == "permission_denied":
            forbidden = skills_root / "runtime" / "forbidden.txt"
            forbidden.parent.mkdir(parents=True, exist_ok=True)
            forbidden.write_text("private", encoding="utf-8")

        response = service.run_report_backend(
            skill_id, "1.0.0", _job(), timeout_seconds=5.0,
        )

        assert response.success is False
        assert response.status == expected_status
        assert response.artifacts == ()
        assert response.error is not None
        assert response.error.detail["stage"] == expected_stage
        serialized_response = json.dumps(response.to_dict(), ensure_ascii=False)
        assert "Sensor report" not in serialized_response
        stderr_log = skills_root / "runtime" / response.task_id / "stderr.log"
        if stderr_log.exists():
            assert "Sensor report" not in stderr_log.read_text("utf-8")
        assert not (
            skills_root / "artifacts" / skill_id / response.task_id
        ).exists()
