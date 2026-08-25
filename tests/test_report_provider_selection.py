"""Batch 3.5.4 report backend selection and professional Adapter tests."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from unittest.mock import MagicMock, patch
import logging

import pytest
from PyQt6.QtTest import QSignalSpy
from PyQt6.QtWidgets import QListWidgetItem

from dp_engine.report_provider import (
    BUILTIN_PROVIDER_ID,
    BUILTIN_PROVIDER_VERSION,
    ReportProviderExecutionError,
    ReportProviderOption,
    ReportProviderProvenance,
    ReportProviderController,
    ReportProviderSelectionError,
    ReportRenderAsset,
    ReportRenderOrchestrator,
    ReportRenderRequest,
    ReportRenderResult,
    SkillReportRenderProvider,
)
from dp_engine.skills.runtime_models import (
    RUNTIME_PROTOCOL_VERSION,
    RuntimeArtifact,
    RuntimeErrorInfo,
    SkillRuntimeResponse,
)
from dp_engine.report_backend.job import ReportBackendJobV1
from dp_engine.skills.models import InstalledSkill, SkillManifest
from dp_engine.skills.manifest_parser import parse_skill_manifest
from dp_engine.skills.registry import SkillRegistry
from ui.report_workbench import ReportWorkbenchWidget


def _option(
    provider_id: str = "ppt-master",
    version: str = "2.1.0",
) -> ReportProviderOption:
    return ReportProviderOption(
        provider_id=provider_id,
        provider_version=version,
        display_name="PPT Master",
        is_builtin=False,
    )


def _builtin_option() -> ReportProviderOption:
    return ReportProviderOption(
        provider_id=BUILTIN_PROVIDER_ID,
        provider_version=BUILTIN_PROVIDER_VERSION,
        display_name="内置标准生成器",
        is_builtin=True,
    )


def _runtime_response(
    *,
    success: bool,
    status: str,
    artifact: RuntimeArtifact | None = None,
    stage: str = "backend_execute",
) -> SkillRuntimeResponse:
    return SkillRuntimeResponse(
        protocol_version=RUNTIME_PROTOCOL_VERSION,
        task_id="task12345678",
        operation="generate_report",
        success=success,
        status=status,
        message="safe runtime result",
        started_at="2026-08-06T00:00:00+00:00",
        finished_at="2026-08-06T00:00:01+00:00",
        duration_ms=1000,
        artifacts=((artifact,) if artifact is not None else ()),
        warnings=("backend warning",) if success else (),
        error=(
            None
            if success
            else RuntimeErrorInfo(
                error_type="BackendFailure",
                message="safe backend failure",
                detail={"stage": stage},
            )
        ),
    )


class _RuntimeStub:
    def __init__(self, response: SkillRuntimeResponse) -> None:
        self.response = response
        self.calls: list[tuple[str, str, ReportBackendJobV1]] = []
        self.cancelled = False

    def run_report_backend(
        self,
        skill_id: str,
        version: str,
        job: ReportBackendJobV1,
        **_kwargs: object,
    ) -> SkillRuntimeResponse:
        self.calls.append((skill_id, version, job))
        return self.response

    def cancel(self) -> None:
        self.cancelled = True


class _ArtifactStoreStub:
    def __init__(self) -> None:
        self.exports: list[tuple[str, str, str, Path, bool]] = []

    def export(
        self,
        skill_id: str,
        task_id: str,
        artifact_id: str,
        target: Path,
        *,
        overwrite: bool = False,
    ) -> None:
        self.exports.append(
            (skill_id, task_id, artifact_id, target, overwrite)
        )
        target.write_bytes(b"professional-ooxml")


def _request(tmp_path: Path) -> ReportRenderRequest:
    chart = tmp_path / "phaseb_raw.png"
    chart.write_bytes(b"\x89PNG\r\n\x1a\n")
    return ReportRenderRequest(
        report_type="ppt",
        structured_report={"title": "诊断报告", "slides": []},
        template_path="",
        output_path=str(tmp_path / "output.pptx"),
        assets=(
            ReportRenderAsset(
                host_id="phaseb_raw",
                source_path=chart,
                media_type="image/png",
                semantic_label="阶段B原始诊断四联图",
                target="温度标定诊断",
            ),
        ),
        required_figure_ids=("phaseb_raw",),
        inclusion_summary={"diagnosis_loaded": True, "figure_count": 1},
    )


def _artifact() -> RuntimeArtifact:
    return RuntimeArtifact(
        relative_path="report.pptx",
        size_bytes=128,
        sha256="a" * 64,
        artifact_schema_version=1,
        artifact_id="b" * 32,
        display_name="Professional report",
        storage_relpath="ppt-master/task12345678/report.pptx",
        media_type=(
            "application/vnd.openxmlformats-officedocument."
            "presentationml.presentation"
        ),
        kind="document",
        created_at="2026-08-06T00:00:01+00:00",
        skill_id="ppt-master",
        version="2.1.0",
        task_id="task12345678",
    )


def _manifest(
    skill_id: str,
    *,
    artifact_types: tuple[str, ...] = ("pptx",),
    version: str = "1.0.0",
) -> SkillManifest:
    return SkillManifest(
        schema_version=1,
        skill_id=skill_id,
        name=skill_id,
        version=version,
        description="Batch 3.5.4 selection fixture",
        skill_type="report_backend",
        artifact_types=artifact_types,
        capabilities=(
            "read_skill_files",
            "read_runtime_workspace",
            "write_runtime_workspace",
        ),
        entrypoints={"generate": "workflows/generate.py:generate"},
        extensions={
            "report_backend_contract": 1,
            "template_modes": ["none", "normalized"],
        },
    )


def _register(
    registry: SkillRegistry,
    manifest: SkillManifest,
    *,
    enabled: bool = True,
    health: str = "healthy",
) -> None:
    registry.register(InstalledSkill(
        manifest=manifest,
        install_path=f"C:/managed/{manifest.skill_id}/{manifest.version}",
        enabled=enabled,
        installed_at="2026-08-06T00:00:00+00:00",
        health_status=health,
    ))
    if not enabled:
        registry.set_enabled(
            manifest.skill_id,
            False,
            manifest.version,
        )


class TestReportProviderController:
    def test_options_filter_disabled_unhealthy_non_active_and_wrong_format(
        self,
        tmp_path: Path,
    ) -> None:
        registry_path = tmp_path / "registry.json"
        registry = SkillRegistry(registry_path)
        registry.load()
        _register(registry, _manifest("healthy-ppt"))
        _register(registry, _manifest("disabled-ppt"), enabled=False)
        _register(registry, _manifest("unhealthy-ppt"), health="unhealthy")
        _register(
            registry,
            _manifest("word-only", artifact_types=("docx",)),
        )
        _register(registry, _manifest("versioned", version="1.0.0"))
        _register(registry, _manifest("versioned", version="2.0.0"))
        registry.set_active_version("versioned", "2.0.0")
        registry.save()

        options = ReportProviderController(
            registry_path,
            tmp_path / "installed",
        ).list_options(report_type="ppt", template_mode="none")

        assert [(item.provider_id, item.provider_version) for item in options] == [
            (BUILTIN_PROVIDER_ID, BUILTIN_PROVIDER_VERSION),
            ("healthy-ppt", "1.0.0"),
            ("versioned", "2.0.0"),
        ]

    @pytest.mark.parametrize(
        ("skill_id", "enabled", "health", "artifact_types", "issue"),
        [
            ("disabled", False, "healthy", ("pptx",), "DISABLED"),
            ("unhealthy", True, "unhealthy", ("pptx",), "NOT_HEALTHY"),
            ("word-only", True, "healthy", ("docx",), "FORMAT_UNSUPPORTED"),
        ],
    )
    def test_explicit_incompatible_selection_fails_without_builtin_fallback(
        self,
        tmp_path: Path,
        skill_id: str,
        enabled: bool,
        health: str,
        artifact_types: tuple[str, ...],
        issue: str,
    ) -> None:
        registry_path = tmp_path / f"{skill_id}.json"
        registry = SkillRegistry(registry_path)
        registry.load()
        _register(
            registry,
            _manifest(skill_id, artifact_types=artifact_types),
            enabled=enabled,
            health=health,
        )
        registry.save()
        controller = ReportProviderController(
            registry_path,
            tmp_path / "installed",
        )

        with pytest.raises(ReportProviderSelectionError, match=issue):
            controller.create_provider(
                provider_id=skill_id,
                provider_version="1.0.0",
                report_type="ppt",
                template_mode="none",
            )


class TestSkillReportRenderProvider:
    def test_success_builds_semantic_job_exports_and_attests_provider(
        self,
        tmp_path: Path,
    ) -> None:
        runtime = _RuntimeStub(
            _runtime_response(
                success=True,
                status="succeeded",
                artifact=_artifact(),
            )
        )
        store = _ArtifactStoreStub()
        provider = SkillReportRenderProvider(
            provenance=ReportProviderProvenance("ppt-master", "2.1.0"),
            runtime=runtime,
            artifact_store=store,
        )

        result = provider.render(_request(tmp_path))

        assert len(runtime.calls) == 1
        skill_id, version, job = runtime.calls[0]
        assert (skill_id, version) == ("ppt-master", "2.1.0")
        assert job.required_figure_ids == ("phaseb_raw",)
        assert job.assets[0].semantic_label == "阶段B原始诊断四联图"
        assert job.inclusion_summary["diagnosis_loaded"] is True
        assert store.exports == [
            (
                "ppt-master",
                "task12345678",
                "b" * 32,
                tmp_path / "output.pptx",
                True,
            )
        ]
        assert result.provenance == provider.provenance
        assert result.requires_host_postprocessing is False
        assert result.warnings == ("backend warning",)

    def test_failure_reports_selected_identity_and_stage_without_export(
        self,
        tmp_path: Path,
    ) -> None:
        runtime = _RuntimeStub(
            _runtime_response(
                success=False,
                status="failed",
                stage="backend_execute",
            )
        )
        store = _ArtifactStoreStub()
        provider = SkillReportRenderProvider(
            provenance=ReportProviderProvenance("ppt-master", "2.1.0"),
            runtime=runtime,
            artifact_store=store,
        )

        with pytest.raises(ReportProviderExecutionError) as captured:
            provider.render(_request(tmp_path))

        message = str(captured.value)
        assert "ppt-master@2.1.0" in message
        assert "backend_execute" in message
        assert "status=failed" in message
        assert store.exports == []
        assert not (tmp_path / "output.pptx").exists()

    def test_cancelled_runtime_is_typed_and_provider_cancel_reaches_runtime(
        self,
        tmp_path: Path,
    ) -> None:
        runtime = _RuntimeStub(
            _runtime_response(
                success=False,
                status="cancelled",
                stage="execute",
            )
        )
        provider = SkillReportRenderProvider(
            provenance=ReportProviderProvenance("ppt-master", "2.1.0"),
            runtime=runtime,
            artifact_store=_ArtifactStoreStub(),
        )

        provider.cancel()
        assert runtime.cancelled is True
        with pytest.raises(
            ReportProviderExecutionError,
            match=r"ppt-master@2\.1\.0.*status=cancelled.*stage=execute",
        ):
            provider.render(_request(tmp_path))


class TestReportWorkbenchProviderSelection:
    def test_default_config_uses_builtin_provider(self, qapp) -> None:
        widget = ReportWorkbenchWidget()

        selection = widget.get_config()["report_provider"]

        assert selection == {
            "provider_id": BUILTIN_PROVIDER_ID,
            "provider_version": BUILTIN_PROVIDER_VERSION,
        }
        assert "未检测到兼容的专业后端" in widget.report_provider_hint.text()

    def test_compatible_provider_can_be_selected_and_written_to_config(
        self,
        qapp,
    ) -> None:
        widget = ReportWorkbenchWidget()
        widget.set_report_provider_options((_builtin_option(), _option()))
        widget.report_provider_combo.setCurrentIndex(1)

        assert widget.is_report_provider_selection_available() is True
        assert widget.get_config()["report_provider"] == {
            "provider_id": "ppt-master",
            "provider_version": "2.1.0",
        }
        assert "1 个兼容专业后端" in widget.report_provider_hint.text()

    def test_stale_explicit_selection_is_retained_and_blocks_generation(
        self,
        qapp,
    ) -> None:
        widget = ReportWorkbenchWidget()
        widget.set_report_provider_options((_builtin_option(), _option()))
        widget.report_provider_combo.setCurrentIndex(1)
        widget.set_report_provider_options((_builtin_option(),))
        widget._proj_file_list.addItem(QListWidgetItem("C:/project/data.csv"))
        widget._diagnosis_record = {"schema_version": "1.2"}
        widget.full_report_requested = MagicMock()

        with patch("PyQt6.QtWidgets.QMessageBox.warning") as warning:
            widget._on_full_report_requested()

        assert widget.get_config()["report_provider"]["provider_id"] == "ppt-master"
        assert widget.is_report_provider_selection_available() is False
        assert "当前不可用" in widget.report_provider_combo.currentText()
        widget.full_report_requested.emit.assert_not_called()
        warning.assert_called_once()

    def test_format_and_template_mode_changes_request_a_refilter(
        self,
        qapp,
    ) -> None:
        widget = ReportWorkbenchWidget()
        spy = QSignalSpy(widget.report_provider_filter_changed)

        widget.ppt_radio.setChecked(True)
        assert list(spy[-1]) == ["pptx", "none"]

        widget._template_file_path = "C:/normalized/template.pptx"
        widget.notify_report_template_changed()
        assert list(spy[-1]) == ["pptx", "normalized"]

    def test_provider_selector_is_locked_while_generation_runs(
        self,
        qapp,
    ) -> None:
        widget = ReportWorkbenchWidget()

        widget.set_generation_running(True)
        assert widget.report_provider_combo.isEnabled() is False
        widget.set_generation_running(False)
        assert widget.report_provider_combo.isEnabled() is True


class TestReportProviderMainIntegration:
    def test_controller_adapter_runtime_and_artifact_store_form_real_vertical_slice(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        from zipfile import ZipFile
        from dp_engine.skills import runtime_paths

        skills_root = tmp_path / "skills"
        installed_dir = skills_root / "installed"
        package = installed_dir / "ppt-master" / "2.1.0"
        workflows = package / "workflows"
        workflows.mkdir(parents=True)
        (workflows / "generate.py").write_text(
            '''import json
import zipfile
from pathlib import Path

def generate(context):
    workspace = Path(context["workspace_path"])
    job = json.loads(
        (workspace / context["params"]["job_path"]).read_text(encoding="utf-8")
    )
    output = workspace / "output" / "report.pptx"
    with zipfile.ZipFile(output, "w") as archive:
        archive.writestr("[Content_Types].xml", "<Types/>")
        archive.writestr("ppt/presentation.xml", "<presentation/>")
    context.declare_artifact(
        "report.pptx",
        display_name="PPT Master report",
        media_type="application/vnd.openxmlformats-officedocument.presentationml.presentation",
        kind="document",
    )
    return {
        "provider_id": context["skill_id"],
        "provider_version": context["version"],
        "contract_version": 1,
        "placed_figure_ids": list(job["required_figure_ids"]),
        "supplementary_figure_ids": list(job["supplementary_figure_ids"]),
        "warnings": [],
    }
''',
            encoding="utf-8",
        )
        (package / "SKILL.md").write_text(
            """---
schema_version: 1
skill_id: ppt-master
name: PPT Master
version: 2.1.0
description: Batch 3.5.4 vertical-slice backend
skill_type: report_backend
artifact_types:
  - pptx
capabilities:
  - read_skill_files
  - read_runtime_workspace
  - write_runtime_workspace
entrypoints:
  generate: workflows/generate.py:generate
report_backend_contract: 1
template_modes:
  - none
  - normalized
---

# PPT Master fixture
""",
            encoding="utf-8",
        )
        registry_path = skills_root / "registry.json"
        registry = SkillRegistry(registry_path)
        registry.load()
        registry.register(InstalledSkill(
            manifest=parse_skill_manifest(package),
            install_path=str(package),
            enabled=True,
            installed_at="2026-08-06T00:00:00+00:00",
            health_status="healthy",
        ))
        registry.save()
        monkeypatch.setattr(
            runtime_paths,
            "get_skills_root",
            lambda: skills_root,
        )

        provider = ReportProviderController(
            registry_path,
            installed_dir,
        ).create_provider(
            provider_id="ppt-master",
            provider_version="2.1.0",
            report_type="ppt",
            template_mode="none",
        )
        output = tmp_path / "selected.pptx"
        output.touch()

        result = provider.render(ReportRenderRequest(
            report_type="ppt",
            structured_report={"title": "Vertical slice", "slides": []},
            template_path="",
            output_path=str(output),
            inclusion_summary={"diagnosis_loaded": True},
        ))

        assert result.provenance == ReportProviderProvenance(
            "ppt-master",
            "2.1.0",
        )
        assert result.requires_host_postprocessing is False
        with ZipFile(output) as archive:
            assert "ppt/presentation.xml" in archive.namelist()

    def test_external_provider_runs_in_real_transaction_without_host_reinjection(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        import main
        from core.ai_client import AIClient
        from docx import Document

        class _AvailableAI:
            def is_available(self) -> bool:
                return True

        class _ProfessionalProvider:
            provenance = ReportProviderProvenance("ppt-master", "2.1.0")

            def __init__(self) -> None:
                self.requests: list[ReportRenderRequest] = []

            def render(self, request: ReportRenderRequest) -> ReportRenderResult:
                self.requests.append(request)
                document = Document()
                document.add_heading("Professional report", level=1)
                document.save(request.output_path)
                return ReportRenderResult(
                    output_path=request.output_path,
                    provenance=self.provenance,
                    requires_host_postprocessing=False,
                )

        monkeypatch.setattr(AIClient, "_instance", _AvailableAI())
        monkeypatch.setattr(
            main,
            "generate_structured_report",
            lambda *_args, **_kwargs: {
                "title": "Batch 3.5.4",
                "sections": [],
                "_report_warnings": [],
                "_diagnosis_loaded": True,
            },
        )
        monkeypatch.setattr(
            "core.chart_store.build_chart_store",
            lambda *_args, **_kwargs: [],
        )
        footer = MagicMock(side_effect=AssertionError("host footer called"))
        monkeypatch.setattr(
            main.DataProcessorWindow,
            "_append_inclusion_footer",
            footer,
        )

        provider = _ProfessionalProvider()
        orchestrator = ReportRenderOrchestrator(
            providers=(provider,),
            default_provider_id="ppt-master",
        )
        report_dir = tmp_path / "报告"
        report_dir.mkdir()
        final = report_dir / "professional.docx"

        result = main._execute_report_build_transaction(
            config={
                "report_type": "word",
                "project_files": [str(tmp_path / "source.txt")],
                "_diagnosis_record": {
                    "record_id": "batch354",
                    "chart_data": {},
                },
            },
            outline="# Report",
            report_type="word",
            generate_fn=lambda *_args, **_kwargs: "unused",
            template_path="",
            final_output_path=str(final),
            report_dir=str(report_dir),
            project_dir=str(tmp_path),
            candidate=str(tmp_path),
            render_orchestrator=orchestrator,
        )

        assert final.is_file()
        assert result["provider_provenance"] == {
            "provider_id": "ppt-master",
            "provider_version": "2.1.0",
        }
        assert len(provider.requests) == 1
        assert provider.requests[0].inclusion_summary == {
            "diagnosis_loaded": True,
            "figure_count": 0,
            "project_source_count": 1,
            "bridge_asset_count": 0,
        }
        footer.assert_not_called()

    def test_real_main_window_loads_compatible_backend_from_registry(
        self,
        qapp,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        import utils.app_paths as app_paths
        from main import DataProcessorWindow

        skills_root = tmp_path / "skills"
        paths = app_paths.get_skills_paths(skills_root)
        registry = SkillRegistry(paths.registry_file)
        registry.load()
        _register(registry, _manifest("ppt-master"))
        registry.save()
        monkeypatch.setattr(app_paths, "get_skills_root", lambda: skills_root)

        window = DataProcessorWindow()
        try:
            window.report_workbench_widget.ppt_radio.setChecked(True)
            qapp.processEvents()
            options = [
                window.report_workbench_widget.report_provider_combo.itemData(index)
                for index in range(
                    window.report_workbench_widget.report_provider_combo.count()
                )
            ]
            assert any(
                option.get("provider_id") == "ppt-master"
                for option in options
                if isinstance(option, dict)
            )
        finally:
            window.hide()
            window.deleteLater()
            qapp.processEvents()

    def test_success_dialog_status_and_log_show_actual_provider(
        self,
        qapp,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        import main
        import utils.app_paths as app_paths

        class _Signal:
            def __init__(self) -> None:
                self._slots: list[Callable[..., None]] = []

            def connect(self, slot: Callable[..., None]) -> None:
                self._slots.append(slot)

            def emit(self, value=None) -> None:
                for slot in self._slots:
                    if value is None:
                        slot()
                    else:
                        slot(value)

        class _ImmediateWorker:
            def __init__(self, work_fn) -> None:
                self._work_fn = work_fn
                self._cancelled = False
                self.finished = _Signal()
                self.error = _Signal()
                self.progress = _Signal()

            def start(self) -> None:
                self.finished.emit(self._work_fn(self))

            def cancel(self) -> None:
                self._cancelled = True

        class _SelectedProvider:
            provenance = ReportProviderProvenance("ppt-master", "2.1.0")

            def render(self, request: ReportRenderRequest) -> ReportRenderResult:
                raise AssertionError("transaction is stubbed")

            def cancel(self) -> None:
                pass

        skills_root = tmp_path / "skills"
        monkeypatch.setattr(app_paths, "get_skills_root", lambda: skills_root)
        monkeypatch.setattr(main, "ReportWorker", _ImmediateWorker)
        final = tmp_path / "project" / "报告" / "final.pptx"
        final.parent.mkdir(parents=True)
        captured: dict[str, object] = {}

        def _transaction_stub(**kwargs):
            captured.update(kwargs)
            return {
                "path": str(final),
                "warnings": [],
                "diagnosis_loaded": True,
                "provider_provenance": {
                    "provider_id": "ppt-master",
                    "provider_version": "2.1.0",
                },
            }

        monkeypatch.setattr(
            main,
            "_execute_report_build_transaction",
            _transaction_stub,
        )
        monkeypatch.setattr(
            main,
            "_resolve_project_root_from_files",
            lambda *_args, **_kwargs: str(tmp_path / "project"),
        )
        monkeypatch.setattr(main.os, "startfile", lambda *_args: None)

        window = main.DataProcessorWindow()
        window._report_provider_controller.create_provider = MagicMock(
            return_value=_SelectedProvider()
        )
        window._get_generate_fn = MagicMock(return_value=lambda *_args: "ok")
        window._claim_bridge_for_report = MagicMock(return_value=None)
        config = {
            "report_type": "ppt",
            "project_files": [str(tmp_path / "project" / "source.txt")],
            "req_file": "",
            "template_file": "",
            "report_provider": {
                "provider_id": "ppt-master",
                "provider_version": "2.1.0",
            },
        }
        try:
            with patch("PyQt6.QtWidgets.QMessageBox.information") as info:
                with caplog.at_level(logging.INFO, logger="main"):
                    window._handle_full_report_generation(config, "# outline")

            window._report_provider_controller.create_provider.assert_called_once_with(
                provider_id="ppt-master",
                provider_version="2.1.0",
                report_type="ppt",
                template_mode="none",
            )
            assert isinstance(
                captured.get("render_orchestrator"),
                ReportRenderOrchestrator,
            )
            assert "ppt-master@2.1.0" in info.call_args.args[2]
            assert "ppt-master@2.1.0" in window.status_bar.currentMessage()
            assert any(
                "provider=ppt-master@2.1.0" in record.message
                for record in caplog.records
            )
            assert window._report_cancel_handler is None
            assert window._active_report_provider is None
        finally:
            window.hide()
            window.deleteLater()
            qapp.processEvents()

    def test_main_cancel_reaches_selected_external_provider_and_worker(
        self,
        qapp,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        import main
        import utils.app_paths as app_paths

        class _Signal:
            def __init__(self) -> None:
                self._slots: list[Callable[..., None]] = []

            def connect(self, slot: Callable[..., None]) -> None:
                self._slots.append(slot)

        class _PendingWorker:
            def __init__(self, work_fn) -> None:
                self.work_fn = work_fn
                self._cancelled = False
                self.finished = _Signal()
                self.error = _Signal()
                self.progress = _Signal()

            def start(self) -> None:
                pass

            def cancel(self) -> None:
                self._cancelled = True

        selected = MagicMock()
        selected.provenance = ReportProviderProvenance(
            "ppt-master",
            "2.1.0",
        )
        skills_root = tmp_path / "skills"
        monkeypatch.setattr(app_paths, "get_skills_root", lambda: skills_root)
        monkeypatch.setattr(main, "ReportWorker", _PendingWorker)
        monkeypatch.setattr(
            main,
            "_resolve_project_root_from_files",
            lambda *_args, **_kwargs: str(tmp_path / "project"),
        )

        window = main.DataProcessorWindow()
        window._report_provider_controller.create_provider = MagicMock(
            return_value=selected
        )
        window._get_generate_fn = MagicMock(return_value=lambda *_args: "ok")
        window._claim_bridge_for_report = MagicMock(return_value=None)
        config = {
            "report_type": "ppt",
            "project_files": [str(tmp_path / "project" / "source.txt")],
            "req_file": "",
            "template_file": "",
            "report_provider": {
                "provider_id": "ppt-master",
                "provider_version": "2.1.0",
            },
        }
        try:
            window._handle_full_report_generation(config, "# outline")
            window.report_workbench_widget.cancel_requested.emit()

            selected.cancel.assert_called_once_with()
            assert window._report_worker._cancelled is True
        finally:
            window.hide()
            window.deleteLater()
            qapp.processEvents()
