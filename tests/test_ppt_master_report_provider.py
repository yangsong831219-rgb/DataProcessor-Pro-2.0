"""Batch 3.6.4 PPT Master Report Provider Adapter tests."""

from __future__ import annotations

import hashlib
import json
import shutil
from collections.abc import Callable
from dataclasses import replace
from pathlib import Path
from typing import TypeVar
from xml.etree import ElementTree

import pytest
from pydantic import BaseModel

from dp_engine.ppt_master_host import (
    ColorPalette,
    CommunicationContract,
    ConfirmDesign,
    ConfirmOutline,
    ConfirmPlan,
    ControlledRunStatus,
    ControlledToolRequest,
    ControlledToolResult,
    DesignContract,
    FinalizeSvgArguments,
    GenerateDesign,
    GenerateOutline,
    GenerateSlides,
    HostAuthoringError,
    HostPlanningStateMachine,
    OutlinePlan,
    OutlineSection,
    OutputLogAttestation,
    PlanningAsset,
    PlanningAssetKind,
    PlanningRequest,
    PlanningSnapshot,
    QualityReceipt,
    ProjectInitArguments,
    PptxStructure,
    QualityStage,
    SlideIntent,
    SlideIntentPlan,
    SlideCheckpointStore,
    SlideLayout,
    SvgQualityArguments,
    SvgToPptxArguments,
    TemplateMode,
    ToolArtifactAttestation,
    ToolchainRunAttestation,
    TypographySpec,
)
from dp_engine.report_provider import (
    PPT_MASTER_PROVIDER_ID,
    PptMasterAuthoredSlide,
    PptMasterAuthoringContext,
    PptMasterProjectSpec,
    PptMasterReportProviderError,
    PptMasterReportRenderProvider,
    PptMasterSlideAuthoringRequest,
    ReportRenderAsset,
    ReportRenderOrchestrator,
    ReportRenderProvider,
    ReportRenderRequest,
)
from dp_engine.report_provider.ppt_master import (
    _normalize_svg_contract,
    _quality_repair_is_monotonic,
    _sanitize_svg_for_ppt_master,
)


_EMPTY_SHA256 = hashlib.sha256(b"").hexdigest()
_PPTX_BYTES = b"PK\x03\x04fixture-native-pptx"
ModelT = TypeVar("ModelT", bound=BaseModel)


class _ScriptedPlanningModel:
    identity = "fixture-planning-model"

    def __init__(self, outputs: list[BaseModel]) -> None:
        self._outputs = outputs

    def generate_structured(
        self,
        *,
        prompt: str,
        schema: type[ModelT],
        system_prompt: str,
        max_tokens: int,
        max_schema_retries: int = 1,
    ) -> ModelT:
        del prompt, system_prompt, max_tokens, max_schema_retries
        output = self._outputs.pop(0)
        return schema.model_validate(output)


class _FakeAuthorer:
    def __init__(
        self,
        events: list[str],
        *,
        invalid_slide: int | None = None,
        wrong_assets_slide: int | None = None,
    ) -> None:
        self.events = events
        self.invalid_slide = invalid_slide
        self.wrong_assets_slide = wrong_assets_slide
        self.contexts: list[PptMasterAuthoringContext] = []

    def create_project_spec(
        self,
        context: PptMasterAuthoringContext,
    ) -> PptMasterProjectSpec:
        self.events.append("author:spec")
        self.contexts.append(context)
        return PptMasterProjectSpec(
            design_spec_markdown="# Design Spec\n\n## IX. Page Roster\n",
            spec_lock_markdown="mode: custom\npptx_structure: flat\n",
            structure=PptxStructure.FLAT,
        )

    def author_slide(
        self,
        context: PptMasterAuthoringContext,
        request: PptMasterSlideAuthoringRequest,
    ) -> PptMasterAuthoredSlide:
        sequence = request.intent.sequence
        self.events.append(f"author:slide:{sequence}")
        asset_map = {asset.asset_id: asset for asset in context.assets}
        images = "".join(
            (
                '<image x="80" y="120" width="480" height="320" '
                f'href="../images/{asset_map[asset_id].project_filename}" />'
            )
            for asset_id in request.intent.asset_ids
        )
        if self.invalid_slide == sequence:
            images += '<image href="https://invalid.example/chart.png" />'
        svg = (
            '<svg xmlns="http://www.w3.org/2000/svg" '
            'viewBox="0 0 1280 720">'
            '<rect x="0" y="0" width="1280" height="720" fill="#ffffff" />'
            f'<text x="80" y="80">{request.intent.title}</text>'
            f"{images}</svg>"
        )
        used_assets = request.intent.asset_ids
        if self.wrong_assets_slide == sequence:
            used_assets = ()
        return PptMasterAuthoredSlide(
            slide_id=request.intent.slide_id,
            sequence=sequence,
            svg_text=svg,
            speaker_notes_markdown=f"Notes for slide {sequence}",
            used_asset_ids=used_assets,
        )


class _FailingAuthorer(_FakeAuthorer):
    def author_slide(
        self,
        context: PptMasterAuthoringContext,
        request: PptMasterSlideAuthoringRequest,
    ) -> PptMasterAuthoredSlide:
        del context, request
        raise HostAuthoringError("model_schema_invalid", "invalid authored SVG")


class _RegressingRepairAuthorer(_FakeAuthorer):
    def author_slide(
        self,
        context: PptMasterAuthoringContext,
        request: PptMasterSlideAuthoringRequest,
    ) -> PptMasterAuthoredSlide:
        authored = super().author_slide(context, request)
        if request.repair_receipt is None:
            return authored
        return authored.model_copy(update={
            "svg_text": authored.svg_text.replace(
                "</svg>",
                '<text x="80" y="680">regressed repair candidate</text></svg>',
            ),
        })


class _FakeRunner:
    def __init__(
        self,
        workspace_parent: Path,
        events: list[str],
        *,
        fail_key: str = "",
        fail_occurrences: int | None = None,
        quality_failed_sequences: tuple[int, ...] = (),
        quality_errors_by_occurrence: dict[int, tuple[str, ...]] | None = None,
        tamper_export_receipt: bool = False,
        drift_key: str = "",
    ) -> None:
        self.workspace_parent = workspace_parent
        self.events = events
        self.fail_key = fail_key
        self.fail_occurrences = fail_occurrences
        self.quality_failed_sequences = quality_failed_sequences
        self.quality_errors_by_occurrence = quality_errors_by_occurrence or {}
        self.tamper_export_receipt = tamper_export_receipt
        self.drift_key = drift_key
        self.requests: list[ControlledToolRequest] = []
        self._key_counts: dict[str, int] = {}

    def workspace_path(self, workspace_id: str) -> Path:
        return self.workspace_parent / workspace_id

    def run(
        self,
        planning_snapshot: PlanningSnapshot,
        request: ControlledToolRequest,
        *,
        cancel_check: Callable[[], bool] | None = None,
    ) -> ControlledToolResult:
        if cancel_check is not None:
            assert cancel_check() is False
        self.requests.append(request)
        workspace = self.workspace_path(request.workspace_id)
        for relative in ("audit", "output", "project", "temp"):
            (workspace / relative).mkdir(parents=True, exist_ok=True)
        key = _request_key(request)
        self._key_counts[key] = self._key_counts.get(key, 0) + 1
        self.events.append(f"run:{key}")
        failed = key == self.fail_key and (
            self.fail_occurrences is None
            or self._key_counts[key] <= self.fail_occurrences
        )
        artifacts: tuple[ToolArtifactAttestation, ...] = ()

        if not failed and isinstance(request.arguments, ProjectInitArguments):
            project = (
                workspace
                / "project"
                / f"{request.arguments.project_name}_ppt169_20260806"
            )
            for relative in ("svg_output", "svg_final", "images", "notes"):
                (project / relative).mkdir(parents=True, exist_ok=False)
            (project / "README.md").write_text("fixture", encoding="utf-8")
        elif not failed and isinstance(request.arguments, FinalizeSvgArguments):
            project = workspace / request.arguments.project_dir
            for svg in (project / "svg_output").glob("*.svg"):
                shutil.copyfile(svg, project / "svg_final" / svg.name)
        elif not failed and isinstance(request.arguments, SvgToPptxArguments):
            output = workspace / "output" / request.arguments.output_name
            output.write_bytes(_PPTX_BYTES)
            digest = hashlib.sha256(_PPTX_BYTES).hexdigest()
            if self.tamper_export_receipt:
                digest = "f" * 64
            project = workspace / request.arguments.project_dir
            report = (
                project
                / "validation"
                / f"{Path(request.arguments.output_name).stem}.report.json"
            )
            report.parent.mkdir(parents=True, exist_ok=True)
            report_payload = {
                "schema": "ppt-master.pptx-postflight-report.v1",
                "status": "passed",
                "output": {
                    "path": str(output.resolve()),
                    "bytes": len(_PPTX_BYTES),
                },
                "source": {
                    "svg_slide_count": 3,
                    "layout_definition_count": 0,
                    "fingerprint": {},
                },
                "package": {
                    "zip_integrity": "passed",
                    "corrupt_member": None,
                    "slides": 3,
                    "notes": 0,
                    "masters": 1,
                    "layouts": 1,
                },
                "checks": {
                    "zip_integrity": "passed",
                    "slide_count": "passed",
                    "internal_relationships": "enforced-at-build",
                    "structured_package": "not-applicable",
                    "transitions": "enforced-at-build",
                    "animations": "enforced-at-build",
                    "quality_gate": "passed",
                    "quality_warnings": "passed",
                    "template_tokens": "passed",
                    "external_images": "passed",
                    "font_portability": "passed",
                },
                "quality": {
                    "status": "loaded",
                    "path": "validation/svg_quality_report.json",
                    "schema": "ppt-master.svg-quality-report.v1",
                    "stage": "final",
                    "source_match": "passed",
                    "source_fingerprint": {},
                    "summary": {},
                    "categories": {},
                },
                "resources": {},
                "backup_path": None,
                "conversion_trace_path": None,
            }
            report_bytes = (
                json.dumps(report_payload, ensure_ascii=False).encode("utf-8")
            )
            report.write_bytes(report_bytes)
            artifacts = (
                ToolArtifactAttestation(
                    relative_path=f"output/{request.arguments.output_name}",
                    size_bytes=len(_PPTX_BYTES),
                    sha256=digest,
                ),
                ToolArtifactAttestation(
                    relative_path=report.relative_to(workspace).as_posix(),
                    size_bytes=len(report_bytes),
                    sha256=hashlib.sha256(report_bytes).hexdigest(),
                ),
            )

        audit = workspace / "audit" / request.run_id
        audit.mkdir(parents=True, exist_ok=False)
        stdout_bytes = b""
        if isinstance(request.arguments, SvgQualityArguments):
            project = workspace / request.arguments.project_dir
            svg_files = sorted((project / "svg_output").glob("*.svg"))
            status_token = "ERROR" if failed else "WARN"
            outcome = "Failed" if failed else "Passed (with warnings)"
            page_lines = []
            for svg_path in svg_files:
                sequence = int(svg_path.name[1:3])
                page_failed = failed and (
                    not self.quality_failed_sequences
                    or sequence in self.quality_failed_sequences
                )
                page_status = "ERROR" if page_failed else "WARN"
                page_outcome = "Failed" if page_failed else "Passed (with warnings)"
                page_lines.append(
                    f"[{page_status}] {svg_path.name} - {page_outcome}"
                )
                if page_failed:
                    messages = self.quality_errors_by_occurrence.get(
                        self._key_counts[key],
                        ("fixture quality contract violation",),
                    )
                    page_lines.extend(
                        f"   [ERROR] {message}" for message in messages
                    )
                else:
                    page_lines.append("   [WARN] fixture page warning")
            stdout_bytes = ("\n".join(page_lines) + "\n").encode("utf-8")
            machine_report = (
                project
                / "validation"
                / f"{request.run_id}.quality.json"
            )
            machine_report.parent.mkdir(parents=True, exist_ok=True)
            machine_report_bytes = (
                b'{"schema":"ppt-master.svg-quality-report.v1"}'
            )
            machine_report.write_bytes(machine_report_bytes)
            artifacts = (
                ToolArtifactAttestation(
                    relative_path=machine_report.relative_to(workspace).as_posix(),
                    size_bytes=len(machine_report_bytes),
                    sha256=hashlib.sha256(machine_report_bytes).hexdigest(),
                ),
            )
        (audit / "stdout.log").write_bytes(stdout_bytes)
        (audit / "stderr.log").write_bytes(b"")
        runtime_sha256 = "b" * 64
        if key == self.drift_key:
            runtime_sha256 = "c" * 64
        log = OutputLogAttestation(
            relative_path=f"audit/{request.run_id}/stdout.log",
            observed_bytes=len(stdout_bytes),
            captured_bytes=len(stdout_bytes),
            stream_sha256=hashlib.sha256(stdout_bytes).hexdigest(),
            log_sha256=hashlib.sha256(stdout_bytes).hexdigest(),
            truncated=False,
        )
        return ControlledToolResult(
            run_id=request.run_id,
            workspace_id=request.workspace_id,
            command=request.arguments.kind,
            status=(ControlledRunStatus.FAILED if failed else ControlledRunStatus.SUCCEEDED),
            planning_snapshot_sha256=_model_sha256(planning_snapshot),
            toolchain=ToolchainRunAttestation(
                version="2.7.0",
                archive_sha256="a" * 64,
                tree_sha256="d" * 64,
                file_count=1,
                total_bytes=1,
            ),
            runtime_sha256=runtime_sha256,
            started_at="2026-08-06T00:00:00+00:00",
            finished_at="2026-08-06T00:00:01+00:00",
            duration_ms=1,
            exit_code=(1 if failed else 0),
            error_code=("fixture_failed" if failed else ""),
            message=("fixture failure" if failed else ""),
            stdout=log,
            stderr=log.model_copy(
                update={
                    "relative_path": f"audit/{request.run_id}/stderr.log",
                    "observed_bytes": 0,
                    "captured_bytes": 0,
                    "stream_sha256": _EMPTY_SHA256,
                    "log_sha256": _EMPTY_SHA256,
                }
            ),
            artifacts=artifacts,
            audit_dir=str(audit),
        )


def _request_key(request: ControlledToolRequest) -> str:
    arguments = request.arguments
    if isinstance(arguments, SvgQualityArguments):
        return f"svg_quality_check:{arguments.stage.value}"
    return arguments.kind.value


def _planning_artifacts() -> tuple[OutlinePlan, DesignContract, SlideIntentPlan]:
    outline = OutlinePlan(
        deck_title="PPT Master Fixture Report",
        narrative_arc="Evidence to decision",
        sections=(
            OutlineSection(
                section_id="main",
                title="Main",
                purpose="Explain the verified result",
                key_messages=("Evidence", "Decision"),
                allocated_slides=3,
                candidate_asset_ids=("required-chart",),
            ),
        ),
    )
    design = DesignContract(
        communication=CommunicationContract(
            objective="Explain the diagnosis",
            audience_success="Approve the remediation",
            tone="professional",
            content_divergence="faithful",
        ),
        template_strategy=TemplateMode.FREE_DESIGN,
        visual_style="technical executive briefing",
        palette=ColorPalette(
            primary="#17324D",
            secondary="#4A6B8A",
            accent="#E8792E",
            background="#FFFFFF",
            text="#17212B",
        ),
        typography=TypographySpec(
            title_font="Microsoft YaHei",
            body_font="Microsoft YaHei",
            monospace_font="Consolas",
            title_size_px=40,
            body_size_px=24,
            caption_size_px=16,
        ),
        density="balanced",
        image_usage="source_only",
        chart_style="clean technical charts",
        structure_mode="flat",
        refine_spec="Keep conclusions evidence-led",
        layout_rules=("Use a clear visual hierarchy",),
        accessibility_rules=("Maintain readable contrast",),
    )
    slides = SlideIntentPlan(
        deck_title=outline.deck_title,
        slides=(
            SlideIntent(
                slide_id="cover",
                section_id="main",
                sequence=1,
                title="Finding",
                message="The evidence is complete",
                layout=SlideLayout.TITLE,
                content_points=("Scope",),
                visual_brief="Minimal technical cover",
                speaker_notes_intent="Introduce the assessment",
            ),
            SlideIntent(
                slide_id="evidence",
                section_id="main",
                sequence=2,
                title="Evidence",
                message="The chart supports the finding",
                layout=SlideLayout.DATA_FOCUS,
                content_points=("Observed trend",),
                asset_ids=("required-chart",),
                visual_brief="Large chart with one takeaway",
                speaker_notes_intent="Explain the chart",
            ),
            SlideIntent(
                slide_id="decision",
                section_id="main",
                sequence=3,
                title="Decision",
                message="Proceed with remediation",
                layout=SlideLayout.CONCLUSION,
                content_points=("Action",),
                visual_brief="Decision card",
                speaker_notes_intent="Close with next steps",
            ),
        ),
    )
    return outline, design, slides


def _snapshot(*, confirmed: bool = True) -> PlanningSnapshot:
    outline, design, slides = _planning_artifacts()
    model = _ScriptedPlanningModel([outline, design, slides])
    machine = HostPlanningStateMachine(model)
    snapshot = machine.start(PlanningRequest(
        request_id="fixture-report",
        report_title="PPT Master Fixture Report",
        objective="Verify the provider orchestration",
        audience="Engineering reviewer",
        source_context="Closed fixture facts",
        requested_slide_count=3,
        assets=(
            PlanningAsset(
                asset_id="required-chart",
                kind=PlanningAssetKind.CHART,
                semantic_label="Required diagnosis chart",
                summary="Primary evidence",
                required=True,
            ),
            PlanningAsset(
                asset_id="optional-chart",
                kind=PlanningAssetKind.CHART,
                semantic_label="Optional context chart",
                summary="Supplementary context",
                required=False,
            ),
        ),
    ))
    if not confirmed:
        return snapshot
    snapshot = machine.apply(snapshot, GenerateOutline())
    snapshot = machine.apply(snapshot, ConfirmOutline())
    snapshot = machine.apply(snapshot, GenerateDesign())
    snapshot = machine.apply(snapshot, ConfirmDesign())
    snapshot = machine.apply(snapshot, GenerateSlides())
    return machine.apply(snapshot, ConfirmPlan())


def _render_request(tmp_path: Path) -> ReportRenderRequest:
    required = tmp_path / "required.png"
    optional = tmp_path / "optional.jpg"
    required.write_bytes(b"required-chart-bytes")
    optional.write_bytes(b"optional-chart-bytes")
    output = tmp_path / "host-temp.pptx"
    output.write_bytes(b"")
    return ReportRenderRequest(
        report_type="ppt",
        structured_report={
            "title": "PPT Master Fixture Report",
            "slides": [{"slide_title": f"Slide {index}"} for index in range(1, 4)],
        },
        template_path="",
        output_path=str(output.resolve()),
        assets=(
            ReportRenderAsset(
                host_id="required-chart",
                source_path=required.resolve(),
                media_type="image/png",
                semantic_label="Required diagnosis chart",
                target="Evidence",
            ),
            ReportRenderAsset(
                host_id="optional-chart",
                source_path=optional.resolve(),
                media_type="image/jpeg",
                semantic_label="Optional context chart",
                target="Appendix",
            ),
        ),
        required_figure_ids=("required-chart",),
        supplementary_figure_ids=("optional-chart",),
        inclusion_summary={"figure_count": 2},
    )


def _provider(
    tmp_path: Path,
    *,
    snapshot: PlanningSnapshot | None = None,
    authorer: _FakeAuthorer | None = None,
    runner: _FakeRunner | None = None,
    events: list[str] | None = None,
) -> tuple[PptMasterReportRenderProvider, _FakeRunner, _FakeAuthorer, list[str]]:
    event_log = events if events is not None else []
    selected_runner = runner or _FakeRunner(tmp_path / "workspaces", event_log)
    selected_authorer = authorer or _FakeAuthorer(event_log)
    return (
        PptMasterReportRenderProvider(
            planning_snapshot=snapshot or _snapshot(),
            runner=selected_runner,
            authoring_adapter=selected_authorer,
            checkpoint_store=SlideCheckpointStore(tmp_path / "slide-checkpoints"),
            id_factory=lambda: "fixturetoken",
        ),
        selected_runner,
        selected_authorer,
        event_log,
    )


def test_provider_enforces_serial_cadence_and_returns_attested_host_temp(
    tmp_path: Path,
) -> None:
    request = _render_request(tmp_path)
    provider, runner, authorer, events = _provider(tmp_path)
    assert isinstance(provider, ReportRenderProvider)
    orchestrator = ReportRenderOrchestrator(
        providers=(provider,),
        default_provider_id=PPT_MASTER_PROVIDER_ID,
    )

    result = orchestrator.render(request)

    assert events == [
        "run:project_init",
        "author:spec",
        "author:slide:1",
        "run:svg_quality_check:first-page",
        "author:slide:2",
        "author:slide:3",
        "run:svg_quality_check:final",
        "run:finalize_svg",
        "run:svg_to_pptx",
    ]
    assert Path(request.output_path).read_bytes() == _PPTX_BYTES
    assert result.provenance.provider_id == PPT_MASTER_PROVIDER_ID
    assert result.requires_host_postprocessing is False
    assert result.warnings == (
        "PPT Master plan omitted supplementary figure: optional-chart",
    )
    context_json = authorer.contexts[0].model_dump_json()
    assert str(tmp_path) not in context_json
    workspace = runner.workspace_path(runner.requests[0].workspace_id)
    project = next((workspace / "project").iterdir())
    authorization = (project / "host_authorization.json").read_text(encoding="utf-8")
    assert str(tmp_path) not in authorization
    assert (project / "images" / authorer.contexts[0].assets[0].project_filename).is_file()


@pytest.mark.parametrize(
    ("mutation", "expected_code"),
    [
        ("unconfirmed", "planning_not_confirmed"),
        ("word", "report_type_unsupported"),
        ("template", "unexpected_template_workspace"),
        ("asset_roster", "asset_roster_mismatch"),
    ],
)
def test_preflight_mismatches_fail_before_runner_or_authorer(
    tmp_path: Path,
    mutation: str,
    expected_code: str,
) -> None:
    request = _render_request(tmp_path)
    snapshot = _snapshot(confirmed=mutation != "unconfirmed")
    if mutation == "word":
        request = replace(request, report_type="word")
    elif mutation == "template":
        request = replace(
            request,
            template_path=str((tmp_path / "template.pptx").resolve()),
        )
    elif mutation == "asset_roster":
        request = replace(
            request,
            assets=request.assets[:1],
            supplementary_figure_ids=(),
        )
    provider, runner, authorer, _events = _provider(
        tmp_path,
        snapshot=snapshot,
    )

    with pytest.raises(PptMasterReportProviderError) as captured:
        provider.render(request)

    assert captured.value.code == expected_code
    assert runner.requests == []
    assert authorer.contexts == []
    assert Path(request.output_path).read_bytes() == b""


def test_first_page_failure_blocks_remaining_authoring_and_rolls_back(
    tmp_path: Path,
) -> None:
    request = _render_request(tmp_path)
    events: list[str] = []
    runner = _FakeRunner(
        tmp_path / "workspaces",
        events,
        fail_key="svg_quality_check:first-page",
    )
    provider, _runner, _authorer, _events = _provider(
        tmp_path,
        runner=runner,
        events=events,
    )

    with pytest.raises(PptMasterReportProviderError) as captured:
        provider.render(request)

    assert captured.value.stage == "quality_first_page_repair"
    assert events.count("author:slide:1") == 2
    assert events.count("run:svg_quality_check:first-page") == 2
    assert "author:slide:2" not in events
    assert "run:svg_quality_check:final" not in events
    assert Path(request.output_path).read_bytes() == b""


def test_svg_sanitizer_inlines_class_css_before_removing_ppt_master_forbidden_markup(
) -> None:
    raw_svg = """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1280 720">
  <defs>
    <clipPath id="safeArea"><rect x="64" y="48" width="1152" height="624"/></clipPath>
    <style>
      .title { font-family: 'Microsoft YaHei'; font-size: 36px; fill: #E2E8F0; font-weight: 700; }
      .accent { fill: #0EA5E9; font-size: 18px; }
    </style>
  </defs>
  <g clip-path="url(#safeArea)">
    <text x="80" y="80" class="title">结论<tspan class="accent" fill="#F97316">重要</tspan></text>
  </g>
</svg>"""

    sanitized = _sanitize_svg_for_ppt_master(raw_svg)

    assert "<style" not in sanitized
    assert "class=" not in sanitized
    assert "<clipPath" not in sanitized
    assert "clip-path=" not in sanitized
    root = ElementTree.fromstring(sanitized)
    text = next(element for element in root.iter() if element.tag.endswith("}text"))
    tspan = next(element for element in root.iter() if element.tag.endswith("}tspan"))
    assert text.attrib["font-family"] == "'Microsoft YaHei'"
    assert text.attrib["font-size"] == "36"
    assert text.attrib["fill"] == "#E2E8F0"
    assert text.attrib["font-weight"] == "700"
    assert tspan.attrib["font-size"] == "18"
    assert tspan.attrib["fill"] == "#F97316"


@pytest.mark.parametrize(
    "style_rule",
    (
        ".title > tspan { fill: #E2E8F0; }",
        ".title { filter: blur(2px); }",
    ),
)
def test_svg_sanitizer_rejects_css_it_cannot_inline_without_visual_loss(
    style_rule: str,
) -> None:
    raw_svg = (
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1280 720">'
        f"<style>{style_rule}</style>"
        '<text class="title" x="80" y="80">结论</text></svg>'
    )

    with pytest.raises(ValueError, match="unsupported|simple SVG class"):
        _sanitize_svg_for_ppt_master(raw_svg)


def test_svg_contract_normalizer_repairs_captured_p07_structural_failures() -> None:
    raw_svg = """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1280 720">
  <rect x="0" y="0" width="1280" height="720" fill="#0F1722" data-pptx-role="background"/>
  <rect x="0" y="0" width="1280" height="6" fill="#E5484D" data-pptx-role="decoration"/>
  <g id="section-title"><text x="64" y="80" font-size="36">Title</text></g>
  <g id="section-title"><text x="64" y="120" font-size="18">Subtitle</text></g>
  <g id="page-footer" data-pptx-bounds="0 688 1280 32">
    <text x="64" y="696" fill="#7A8A9E" font-size="14">B1 phase B diagnostic provenance</text>
  </g>
</svg>"""

    normalized = _normalize_svg_contract(raw_svg, page_role="content")
    assert _normalize_svg_contract(normalized, page_role="content") == normalized
    root = ElementTree.fromstring(normalized)
    children = list(root)

    assert root.attrib["data-pptx-page-role"] == "content"
    assert children[0].attrib["id"] == "host-background"
    assert children[1].attrib["id"] == "host-decoration"
    assert children[2].attrib["id"] == "section-title"
    assert children[3].attrib["id"] == "section-title-2"
    footer = children[4]
    assert footer.attrib["data-pptx-bounds"] == "0 684.1 1280 35.9"


def test_quality_repair_monotonicity_rejects_new_error_rules() -> None:
    before = QualityReceipt.model_validate({
        "stage": "final",
        "run_id": "quality-final",
        "pages": [{
            "page_id": "P07",
            "sequence": 7,
            "svg_filename": "P07_slide-07.svg",
            "status": "failed",
            "issues": [{
                "severity": "error",
                "rule_id": "top_level_group_id",
                "message": "duplicate group id",
            }],
        }],
    })
    after = QualityReceipt.model_validate({
        "stage": "final",
        "run_id": "quality-final-repair",
        "pages": [{
            "page_id": "P07",
            "sequence": 7,
            "svg_filename": "P07_slide-07.svg",
            "status": "failed",
            "issues": [{
                "severity": "error",
                "rule_id": "root_group_bounds",
                "message": "footer text exceeds bounds",
            }],
        }],
    })

    assert _quality_repair_is_monotonic(before, after) is False


def test_quality_repair_monotonicity_requires_fewer_hard_errors() -> None:
    before = QualityReceipt.model_validate({
        "stage": "final",
        "run_id": "quality-final",
        "pages": [{
            "page_id": "P07",
            "sequence": 7,
            "svg_filename": "P07_slide-07.svg",
            "status": "failed",
            "issues": [{
                "severity": "error",
                "rule_id": "root_group_bounds",
                "message": "footer text exceeds bounds",
            }],
        }],
    })
    unchanged = before.model_copy(update={"run_id": "quality-final-repair"})

    assert _quality_repair_is_monotonic(before, unchanged) is False


@pytest.mark.parametrize(
    "fail_key",
    ["svg_quality_check:final", "finalize_svg", "svg_to_pptx"],
)
def test_failed_controlled_stage_stops_later_runs_and_rolls_back(
    tmp_path: Path,
    fail_key: str,
) -> None:
    request = _render_request(tmp_path)
    events: list[str] = []
    runner = _FakeRunner(tmp_path / "workspaces", events, fail_key=fail_key)
    provider, _runner, _authorer, _events = _provider(
        tmp_path,
        runner=runner,
        events=events,
    )

    with pytest.raises(PptMasterReportProviderError):
        provider.render(request)

    failed_index = events.index(f"run:{fail_key}")
    if fail_key == "svg_quality_check:final":
        assert events.count("run:svg_quality_check:final") == 2
        assert events.count("author:slide:1") == 2
        assert events.count("author:slide:2") == 2
        assert events.count("author:slide:3") == 2
        second_failure = len(events) - 1 - events[::-1].index(
            "run:svg_quality_check:final"
        )
        assert all(
            not event.startswith("run:") for event in events[second_failure + 1 :]
        )
    else:
        assert all(
            not event.startswith("run:") for event in events[failed_index + 1 :]
        )
    assert Path(request.output_path).read_bytes() == b""


def test_final_quality_repairs_only_failed_pages_once(tmp_path: Path) -> None:
    request = _render_request(tmp_path)
    events: list[str] = []
    runner = _FakeRunner(
        tmp_path / "workspaces",
        events,
        fail_key="svg_quality_check:final",
        fail_occurrences=1,
        quality_failed_sequences=(2,),
    )
    provider, _runner, _authorer, _events = _provider(
        tmp_path,
        runner=runner,
        events=events,
    )

    provider.render(request)

    assert events.count("author:slide:1") == 1
    assert events.count("author:slide:2") == 2
    assert events.count("author:slide:3") == 1
    assert events.count("run:svg_quality_check:final") == 2
    assert "run:svg_to_pptx" in events


def test_unresolved_quality_error_exposes_failed_page_and_rule(tmp_path: Path) -> None:
    request = _render_request(tmp_path)
    events: list[str] = []
    provider, _runner, _authorer, _events = _provider(
        tmp_path,
        runner=_FakeRunner(
            tmp_path / "workspaces",
            events,
            fail_key="svg_quality_check:final",
            quality_failed_sequences=(2,),
        ),
        events=events,
    )

    with pytest.raises(PptMasterReportProviderError) as captured:
        provider.render(request)

    assert captured.value.quality_receipt is not None
    assert captured.value.quality_receipt.failed_pages[0].page_id == "P02"
    assert "P02:quality_contract" in str(captured.value)


def test_final_quality_rejects_repair_that_introduces_new_error_rule(
    tmp_path: Path,
) -> None:
    request = _render_request(tmp_path)
    events: list[str] = []
    runner = _FakeRunner(
        tmp_path / "workspaces",
        events,
        fail_key="svg_quality_check:final",
        quality_failed_sequences=(2,),
        quality_errors_by_occurrence={
            1: ("Duplicate top-level group id 'section-title'",),
            2: (
                '<text> exceeds <g id="page-footer"> data-pptx-bounds',
            ),
        },
    )
    provider, _runner, _authorer, _events = _provider(
        tmp_path,
        runner=runner,
        authorer=_RegressingRepairAuthorer(events),
        events=events,
    )

    with pytest.raises(PptMasterReportProviderError) as captured:
        provider.render(request)

    assert captured.value.code == "quality_repair_regressed"
    assert events.count("run:svg_quality_check:final") == 2
    assert "run:svg_to_pptx" not in events
    workspace = next((tmp_path / "workspaces").iterdir())
    project = next((workspace / "project").iterdir())
    restored_path = next((project / "svg_output").glob("P02_*.svg"))
    restored = restored_path.read_text(encoding="utf-8")
    assert "regressed repair candidate" not in restored
    manifest = json.loads(
        (project / "host_authoring_manifest.json").read_text(encoding="utf-8")
    )
    page = next(item for item in manifest["slides"] if item["sequence"] == 2)
    assert page["svg_sha256"] == hashlib.sha256(restored.encode("utf-8")).hexdigest()


@pytest.mark.parametrize("failure", ["unsafe_svg", "wrong_assets"])
def test_invalid_host_authored_page_never_reaches_quality_or_export(
    tmp_path: Path,
    failure: str,
) -> None:
    request = _render_request(tmp_path)
    events: list[str] = []
    authorer = _FakeAuthorer(
        events,
        invalid_slide=(1 if failure == "unsafe_svg" else None),
        wrong_assets_slide=(2 if failure == "wrong_assets" else None),
    )
    provider, _runner, _authorer, _events = _provider(
        tmp_path,
        authorer=authorer,
        events=events,
    )

    with pytest.raises(PptMasterReportProviderError) as captured:
        provider.render(request)

    if failure == "unsafe_svg":
        assert captured.value.code == "svg_contract_invalid"
        assert "run:svg_quality_check:first-page" not in events
    else:
        assert captured.value.code == "slide_asset_plan_mismatch"
        assert "run:svg_quality_check:final" not in events
    assert "run:svg_to_pptx" not in events
    assert Path(request.output_path).read_bytes() == b""


def test_authoring_error_code_is_preserved_by_provider(tmp_path: Path) -> None:
    request = _render_request(tmp_path)
    events: list[str] = []
    provider, _runner, _authorer, _events = _provider(
        tmp_path,
        authorer=_FailingAuthorer(events),
        events=events,
    )

    with pytest.raises(PptMasterReportProviderError) as captured:
        provider.render(request)

    assert captured.value.stage == "author_slide_01"
    assert captured.value.code == "model_schema_invalid"
    assert "run:svg_quality_check:first-page" not in events
    assert Path(request.output_path).read_bytes() == b""


def test_controlled_stage_failure_exposes_path_free_audit_reference(
    tmp_path: Path,
) -> None:
    request = _render_request(tmp_path)
    events: list[str] = []
    runner = _FakeRunner(
        tmp_path / "workspaces",
        events,
        fail_key="svg_to_pptx",
    )
    provider, _runner, _authorer, _events = _provider(
        tmp_path,
        runner=runner,
        events=events,
    )

    with pytest.raises(PptMasterReportProviderError) as captured:
        provider.render(request)

    error = captured.value
    assert error.stage == "export_pptx"
    assert error.run_id.endswith("-export_pptx")
    assert error.exit_code == 1
    assert f"workspace={error.workspace_id}" in str(error)
    assert f"run={error.run_id}" in str(error)
    assert "exit=1" in str(error)
    assert str(tmp_path) not in str(error)


def test_export_receipt_or_runtime_identity_drift_cannot_publish(
    tmp_path: Path,
) -> None:
    for mode in ("receipt", "identity"):
        case_root = tmp_path / mode
        case_root.mkdir()
        request = _render_request(case_root)
        events: list[str] = []
        runner = _FakeRunner(
            case_root / "workspaces",
            events,
            tamper_export_receipt=mode == "receipt",
            drift_key=("svg_quality_check:final" if mode == "identity" else ""),
        )
        provider, _runner, _authorer, _events = _provider(
            case_root,
            runner=runner,
            events=events,
        )

        with pytest.raises(PptMasterReportProviderError) as captured:
            provider.render(request)

        expected = (
            "export_artifact_attestation_mismatch"
            if mode == "receipt"
            else "controlled_identity_drift"
        )
        assert captured.value.code == expected
        assert Path(request.output_path).read_bytes() == b""


def test_cancellation_after_first_page_gate_stops_before_page_two(
    tmp_path: Path,
) -> None:
    events: list[str] = []

    def cancel_check() -> None:
        if "run:svg_quality_check:first-page" in events:
            raise RuntimeError("fixture cancellation")

    base = _render_request(tmp_path)
    request = replace(base, cancel_check=cancel_check)
    provider, _runner, _authorer, _events = _provider(tmp_path, events=events)

    with pytest.raises(RuntimeError, match="fixture cancellation"):
        provider.render(request)

    assert "author:slide:2" not in events
    assert "run:svg_quality_check:final" not in events
    assert Path(request.output_path).read_bytes() == b""


def test_nonempty_host_temp_is_never_overwritten(tmp_path: Path) -> None:
    request = _render_request(tmp_path)
    Path(request.output_path).write_bytes(b"existing")
    provider, runner, authorer, _events = _provider(tmp_path)

    with pytest.raises(PptMasterReportProviderError) as captured:
        provider.render(request)

    assert captured.value.code == "host_temp_not_empty"
    assert Path(request.output_path).read_bytes() == b"existing"
    assert runner.requests == []
    assert authorer.contexts == []


def _model_sha256(model: BaseModel) -> str:
    encoded = json.dumps(
        model.model_dump(mode="json", exclude_none=True),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()
