"""PPT Master Adapter behind the stable report-render Provider Interface.

The Module keeps Host authoring, controlled third-party tools, and report
publication separated.  It accepts only a confirmed Planning Snapshot, gives a
Host authorer path-free immutable context, stages bounded project inputs, and
delivers one attested PPTX to the outer report transaction's temporary path.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import secrets
import stat
import tempfile
from collections.abc import Callable, Mapping
from pathlib import Path, PurePosixPath
from typing import Any, Protocol
from xml.etree import ElementTree

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from dp_engine.ppt_master_host import (
    ControlledRunStatus,
    ControlledToolCommand,
    ControlledToolError,
    ControlledToolRequest,
    ControlledToolResult,
    FinalizeSvgArguments,
    HostAuthoringError,
    MethodReceipt,
    PlanningPhase,
    PlanningSnapshot,
    PptxPostflightReceipt,
    ProjectInitArguments,
    PptxStructure,
    QualityStage,
    QualityReceipt,
    QualityReceiptError,
    SlideCheckpointKey,
    SlideCheckpointStore,
    SlideIntent,
    SvgQualityArguments,
    SvgToPptxArguments,
    TemplateMode,
    ToolArtifactAttestation,
    method_receipt_from_first_page,
    parse_pptx_postflight_receipt,
    parse_quality_receipt,
    quality_report_artifact_path,
    get_default_slide_checkpoint_root,
)
from dp_engine.ppt_master_host.template_workspace import (
    PptMasterTemplateWorkspace,
)

from .models import (
    ReportProviderProvenance,
    ReportRenderAsset,
    ReportRenderRequest,
    ReportRenderResult,
)


PPT_MASTER_PROVIDER_ID = "host.ppt_master"
PPT_MASTER_PROVIDER_VERSION = "2.7.0-host.4"

_ID_PATTERN = r"^[^/\\\s\x00-\x1f]{1,96}$"
_PROJECT_FILE_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9._-]{0,95}\.(?:png|jpg)$"
_SVG_FILE_PATTERN = r"^P(?:0[1-9]|[1-3][0-9]|40)_[A-Za-z0-9._-]{1,72}\.svg$"
_SVG_NAMESPACE = "http://www.w3.org/2000/svg"
_XLINK_NAMESPACE = "http://www.w3.org/1999/xlink"
_MAX_PROJECT_DOCUMENT_BYTES = 512 * 1024
_MAX_SVG_BYTES = 4 * 1024 * 1024
_MAX_NOTES_BYTES = 64 * 1024
_MAX_ASSET_BYTES = 50 * 1024 * 1024
_MAX_TOTAL_ASSET_BYTES = 250 * 1024 * 1024
_MAX_TEMPLATE_BYTES = 100 * 1024 * 1024
_COPY_CHUNK_BYTES = 1024 * 1024
_CSS_PRESENTATION_ATTRIBUTES = frozenset({
    "fill",
    "fill-opacity",
    "font-family",
    "font-size",
    "font-style",
    "font-weight",
    "opacity",
    "stroke",
    "stroke-opacity",
    "stroke-width",
    "text-anchor",
})
_SIMPLE_CSS_CLASS_SELECTOR = re.compile(r"^\.([A-Za-z_][A-Za-z0-9_-]*)$")
_CSS_RULE = re.compile(r"([^{}]+)\{([^{}]*)\}")
_CSS_COMMENT = re.compile(r"/\*.*?\*/", re.DOTALL)
_SEMANTIC_ROLE_ID_BASE = {
    "background": "host-background",
    "chrome": "host-chrome",
    "decoration": "host-decoration",
    "footer": "host-footer",
    "header": "host-header",
    "logo": "host-logo",
    "page-number": "host-page-number",
    "watermark": "host-watermark",
}
_LAYOUT_TO_PAGE_ROLE = {
    "title": "cover",
    "section": "section",
    "conclusion": "ending",
}


class _StrictProviderModel(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        str_strip_whitespace=True,
    )


class PptMasterAuthoringAsset(_StrictProviderModel):
    """One approved asset described without exposing its Host source path."""

    asset_id: str = Field(pattern=_ID_PATTERN)
    project_filename: str = Field(pattern=_PROJECT_FILE_PATTERN)
    media_type: str = Field(pattern=r"^image/(?:png|jpeg)$")
    semantic_label: str = Field(min_length=1, max_length=200)
    target: str = Field(min_length=1, max_length=500)
    required: bool
    size_bytes: int = Field(ge=1, le=_MAX_ASSET_BYTES)
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


class PptMasterAuthoringTemplate(_StrictProviderModel):
    """Path-free template-style attestation exposed to Host authoring."""

    workspace_id: str = Field(pattern=r"^tmpl-[0-9a-f]{24}$")
    project_filename: str = "style-template.pptx"
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    size_bytes: int = Field(ge=1, le=_MAX_TEMPLATE_BYTES)
    profile_json: str = Field(min_length=2, max_length=64 * 1024)
    style_summary: str = Field(min_length=1, max_length=4_000)


class PptMasterAuthoringContext(_StrictProviderModel):
    """Immutable, path-free input made available to the Host authorer."""

    planning_snapshot: PlanningSnapshot
    render_request_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    structured_report_json: str = Field(min_length=2, max_length=1_000_000)
    slide_filenames: tuple[str, ...] = Field(min_length=3, max_length=40)
    assets: tuple[PptMasterAuthoringAsset, ...] = Field(
        default_factory=tuple,
        max_length=80,
    )
    template_style: PptMasterAuthoringTemplate | None = None

    @model_validator(mode="after")
    def _validate_roster(self) -> PptMasterAuthoringContext:
        slides = self.planning_snapshot.slides
        if slides is None or len(self.slide_filenames) != len(slides.slides):
            raise ValueError("authoring roster must match the confirmed slide plan")
        if len(self.slide_filenames) != len(set(self.slide_filenames)):
            raise ValueError("authoring SVG filenames must be unique")
        return self


class PptMasterProjectSpec(_StrictProviderModel):
    """Host-authored PPT Master project contracts staged before P01."""

    design_spec_markdown: str = Field(min_length=1)
    spec_lock_markdown: str = Field(min_length=1)
    structure: PptxStructure

    @field_validator("design_spec_markdown", "spec_lock_markdown")
    @classmethod
    def _validate_project_document(cls, value: str) -> str:
        if "\x00" in value:
            raise ValueError("project contracts cannot contain NUL bytes")
        if len(value.encode("utf-8")) > _MAX_PROJECT_DOCUMENT_BYTES:
            raise ValueError("project contract exceeds the Host byte limit")
        return value


class PptMasterSlideAuthoringRequest(_StrictProviderModel):
    """One serial page-authoring request."""

    intent: SlideIntent
    svg_filename: str = Field(pattern=_SVG_FILE_PATTERN)
    method_receipt: MethodReceipt | None = None
    repair_receipt: QualityReceipt | None = None
    previous_svg_text: str = ""

    @model_validator(mode="after")
    def _validate_repair_request(self) -> PptMasterSlideAuthoringRequest:
        if bool(self.repair_receipt) != bool(self.previous_svg_text):
            raise ValueError(
                "repair receipt and previous SVG must be supplied together"
            )
        return self


class PptMasterAuthoredSlide(_StrictProviderModel):
    """One complete SVG page returned in memory by the Host authorer."""

    slide_id: str = Field(pattern=_ID_PATTERN)
    sequence: int = Field(ge=1, le=40)
    svg_text: str = Field(min_length=1)
    speaker_notes_markdown: str = ""
    used_asset_ids: tuple[str, ...] = Field(default_factory=tuple, max_length=2)

    @field_validator("svg_text")
    @classmethod
    def _validate_svg_size(cls, value: str) -> str:
        if "\x00" in value:
            raise ValueError("authored SVG cannot contain NUL bytes")
        if len(value.encode("utf-8")) > _MAX_SVG_BYTES:
            raise ValueError("authored SVG exceeds the Host byte limit")
        return value

    @field_validator("speaker_notes_markdown")
    @classmethod
    def _validate_notes_size(cls, value: str) -> str:
        if "\x00" in value:
            raise ValueError("speaker notes cannot contain NUL bytes")
        if len(value.encode("utf-8")) > _MAX_NOTES_BYTES:
            raise ValueError("speaker notes exceed the Host byte limit")
        return value

    @field_validator("used_asset_ids")
    @classmethod
    def _validate_used_assets(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if len(value) != len(set(value)):
            raise ValueError("authored slide asset IDs must be unique")
        return value


class PptMasterAuthoringAdapter(Protocol):
    """Host authoring Seam; implementations never receive filesystem paths."""

    def create_project_spec(
        self,
        context: PptMasterAuthoringContext,
    ) -> PptMasterProjectSpec: ...

    def author_slide(
        self,
        context: PptMasterAuthoringContext,
        request: PptMasterSlideAuthoringRequest,
    ) -> PptMasterAuthoredSlide: ...

class PptMasterControlledRunner(Protocol):
    """Narrow Controlled Tool Runner Interface consumed by this Adapter."""

    def workspace_path(self, workspace_id: str) -> Path: ...

    def run(
        self,
        planning_snapshot: PlanningSnapshot,
        request: ControlledToolRequest,
        *,
        cancel_check: Callable[[], bool] | None = None,
    ) -> ControlledToolResult: ...


class PptMasterReportProviderError(RuntimeError):
    """Stage-labelled, path-free failure from the PPT Master Provider."""

    def __init__(
        self,
        *,
        stage: str,
        code: str,
        workspace_id: str = "",
        run_id: str = "",
        exit_code: int | None = None,
        quality_receipt: QualityReceipt | None = None,
    ) -> None:
        self.stage = stage
        self.code = code
        self.workspace_id = workspace_id
        self.run_id = run_id
        self.exit_code = exit_code
        self.quality_receipt = quality_receipt
        detail = ""
        if quality_receipt is not None and quality_receipt.failed_pages:
            page_rules = []
            for page in quality_receipt.failed_pages:
                rules = sorted({
                    issue.rule_id
                    for issue in page.issues
                    if issue.severity == "error"
                })
                page_rules.append(
                    f"{page.page_id}:{','.join(rules) or 'quality_contract'}"
                )
            detail = f", failures={';'.join(page_rules)}"
        audit_reference = ""
        if workspace_id:
            audit_reference = f", workspace={workspace_id}"
        if run_id:
            audit_reference += f", run={run_id}"
        if exit_code is not None:
            audit_reference += f", exit={exit_code}"
        super().__init__(
            "PPT Master report provider failed: "
            f"stage={stage}, code={code}{audit_reference}{detail}"
        )


class _TargetIdentity(_StrictProviderModel):
    device: int
    inode: int


class _PreparedRequest(_StrictProviderModel):
    context: PptMasterAuthoringContext
    source_paths: Mapping[str, Path]
    target: Path
    target_identity: _TargetIdentity
    template_source: Path | None = None


class PptMasterReportRenderProvider:
    """Deep Provider Module coordinating Host authoring and Controlled Runs."""

    def __init__(
        self,
        *,
        planning_snapshot: PlanningSnapshot,
        runner: PptMasterControlledRunner,
        authoring_adapter: PptMasterAuthoringAdapter,
        template_workspace: PptMasterTemplateWorkspace | None = None,
        checkpoint_store: SlideCheckpointStore | None = None,
        id_factory: Callable[[], str] | None = None,
    ) -> None:
        self._planning_snapshot = PlanningSnapshot.model_validate(
            planning_snapshot.model_dump(mode="python")
        )
        self._runner = runner
        self._authoring_adapter = authoring_adapter
        self._template_workspace = template_workspace
        self._checkpoint_store = checkpoint_store or SlideCheckpointStore(
            get_default_slide_checkpoint_root()
        )
        self._id_factory = id_factory or (lambda: secrets.token_hex(12))

    @property
    def provenance(self) -> ReportProviderProvenance:
        return ReportProviderProvenance(
            provider_id=PPT_MASTER_PROVIDER_ID,
            provider_version=PPT_MASTER_PROVIDER_VERSION,
        )

    def render(self, request: ReportRenderRequest) -> ReportRenderResult:
        snapshot = PlanningSnapshot.model_validate(
            self._planning_snapshot.model_dump(mode="python")
        )
        prepared = self._prepare_request(snapshot, request)
        workspace_id = self._new_workspace_id()
        project_name = f"report_{prepared.context.render_request_sha256[:16]}"
        output_name = f"report-{prepared.context.render_request_sha256[:16]}.pptx"
        baseline: ControlledToolResult | None = None

        init_result = self._run_stage(
            stage="project_init",
            ordinal=1,
            workspace_id=workspace_id,
            snapshot=snapshot,
            arguments=ProjectInitArguments(project_name=project_name),
            request=request,
            baseline=baseline,
        )
        baseline = init_result
        toolchain_sha256 = _model_sha256(init_result.toolchain)

        workspace = self._runner.workspace_path(workspace_id).resolve(strict=True)
        if workspace.name != workspace_id or _is_reparse(workspace):
            raise PptMasterReportProviderError(
                stage="project_init",
                code="workspace_identity_mismatch",
                workspace_id=workspace_id,
            )
        project = _locate_initialized_project(workspace, project_name)

        project_spec = self._create_project_spec(
            prepared.context,
            snapshot,
            workspace_id,
        )
        deck_contract_sha256 = hashlib.sha256(
            (
                project_spec.design_spec_markdown
                + "\x00"
                + project_spec.spec_lock_markdown
                + "\x00"
                + project_spec.structure.value
            ).encode("utf-8")
        ).hexdigest()
        self._stage_project_contracts(project, project_spec, prepared.context)
        self._stage_template_workspace(
            project,
            prepared,
            request,
            workspace_id,
        )
        self._stage_assets(
            project,
            prepared,
            request,
            workspace_id,
        )

        authored_receipts: list[dict[str, object]] = []
        checkpoint_keys: dict[str, SlideCheckpointKey] = {}
        checkpoint_slides: dict[str, PptMasterAuthoredSlide] = {}
        slides = snapshot.slides
        if slides is None:
            raise PptMasterReportProviderError(
                stage="request_preflight",
                code="slide_plan_missing",
                workspace_id=workspace_id,
            )
        first_intent = slides.slides[0]
        first_filename = prepared.context.slide_filenames[0]
        first_request = PptMasterSlideAuthoringRequest(
            intent=first_intent,
            svg_filename=first_filename,
        )
        first_authored, first_checkpoint_key = self._load_or_author_slide(
            prepared.context,
            first_request,
            workspace_id,
            deck_contract_sha256=deck_contract_sha256,
            toolchain_sha256=toolchain_sha256,
        )
        checkpoint_keys[first_filename] = first_checkpoint_key
        checkpoint_slides[first_filename] = first_authored
        authored_receipts.append(self._stage_authored_slide(
            project,
            first_intent,
            first_filename,
            first_authored,
            prepared.context,
            workspace_id,
        ))
        baseline, first_quality = self._run_quality_stage(
            stage="quality_first_page",
            ordinal=2,
            workspace_id=workspace_id,
            snapshot=snapshot,
            project=project,
            workspace=workspace,
            quality_stage=QualityStage.FIRST_PAGE,
            request=request,
            baseline=baseline,
        )
        if not first_quality.passed:
            failed_quality = first_quality
            repair_backup = _capture_failed_page_sources(project, failed_quality)
            authored_receipts, repaired = self._repair_failed_pages(
                project=project,
                context=prepared.context,
                quality=first_quality,
                method_receipt=None,
                authored_receipts=authored_receipts,
                workspace_id=workspace_id,
            )
            baseline, first_quality = self._run_quality_stage(
                stage="quality_first_page_repair",
                ordinal=3,
                workspace_id=workspace_id,
                snapshot=snapshot,
                project=project,
                workspace=workspace,
                quality_stage=QualityStage.FIRST_PAGE,
                request=request,
                baseline=baseline,
            )
            if not first_quality.passed:
                if not _quality_repair_is_monotonic(
                    failed_quality,
                    first_quality,
                ):
                    _restore_failed_page_sources(project, repair_backup)
                    raise PptMasterReportProviderError(
                        stage="quality_first_page_repair",
                        code="quality_repair_regressed",
                        workspace_id=workspace_id,
                        quality_receipt=first_quality,
                    )
                raise PptMasterReportProviderError(
                    stage="quality_first_page_repair",
                    code="quality_contract_unresolved",
                    workspace_id=workspace_id,
                    quality_receipt=first_quality,
                )
            first_authored = repaired[first_filename]
            checkpoint_slides[first_filename] = first_authored
        method_receipt = method_receipt_from_first_page(first_quality)

        for intent, filename in zip(
            slides.slides[1:],
            prepared.context.slide_filenames[1:],
            strict=True,
        ):
            _invoke_cancel_check(request.cancel_check)
            slide_request = PptMasterSlideAuthoringRequest(
                intent=intent,
                svg_filename=filename,
                method_receipt=method_receipt,
            )
            authored, checkpoint_key = self._load_or_author_slide(
                prepared.context,
                slide_request,
                workspace_id,
                deck_contract_sha256=deck_contract_sha256,
                toolchain_sha256=toolchain_sha256,
            )
            checkpoint_keys[filename] = checkpoint_key
            checkpoint_slides[filename] = authored
            receipt = self._stage_authored_slide(
                project,
                intent,
                filename,
                authored,
                prepared.context,
                workspace_id,
            )
            authored_receipts.append(receipt)

        self._write_authoring_manifest(
            project,
            prepared.context,
            authored_receipts,
        )
        baseline, final_quality = self._run_quality_stage(
            stage="quality_final",
            ordinal=4,
            workspace_id=workspace_id,
            snapshot=snapshot,
            project=project,
            workspace=workspace,
            quality_stage=QualityStage.FINAL,
            request=request,
            baseline=baseline,
        )
        next_ordinal = 5
        if not final_quality.passed:
            failed_quality = final_quality
            repair_backup = _capture_failed_page_sources(project, failed_quality)
            pre_repair_receipts = list(authored_receipts)
            authored_receipts, repaired = self._repair_failed_pages(
                project=project,
                context=prepared.context,
                quality=final_quality,
                method_receipt=method_receipt,
                authored_receipts=authored_receipts,
                workspace_id=workspace_id,
            )
            self._write_authoring_manifest(
                project,
                prepared.context,
                authored_receipts,
                replace_existing=True,
            )
            baseline, final_quality = self._run_quality_stage(
                stage="quality_final_repair",
                ordinal=5,
                workspace_id=workspace_id,
                snapshot=snapshot,
                project=project,
                workspace=workspace,
                quality_stage=QualityStage.FINAL,
                request=request,
                baseline=baseline,
            )
            next_ordinal = 6
            if not final_quality.passed:
                if not _quality_repair_is_monotonic(
                    failed_quality,
                    final_quality,
                ):
                    _restore_failed_page_sources(project, repair_backup)
                    self._write_authoring_manifest(
                        project,
                        prepared.context,
                        pre_repair_receipts,
                        replace_existing=True,
                    )
                    raise PptMasterReportProviderError(
                        stage="quality_final_repair",
                        code="quality_repair_regressed",
                        workspace_id=workspace_id,
                        quality_receipt=final_quality,
                    )
                raise PptMasterReportProviderError(
                    stage="quality_final_repair",
                    code="quality_contract_unresolved",
                    workspace_id=workspace_id,
                    quality_receipt=final_quality,
                )
            checkpoint_slides.update(repaired)
        for filename, authored in checkpoint_slides.items():
            self._publish_slide_checkpoint(checkpoint_keys[filename], authored)
        baseline = self._run_stage(
            stage="finalize_svg",
            ordinal=next_ordinal,
            workspace_id=workspace_id,
            snapshot=snapshot,
            arguments=FinalizeSvgArguments(
                project_dir=_project_relative(project, workspace),
            ),
            request=request,
            baseline=baseline,
        )
        export_result = self._run_stage(
            stage="export_pptx",
            ordinal=next_ordinal + 1,
            workspace_id=workspace_id,
            snapshot=snapshot,
            arguments=SvgToPptxArguments(
                project_dir=_project_relative(project, workspace),
                output_name=output_name,
                structure=project_spec.structure,
            ),
            request=request,
            baseline=baseline,
        )

        exported, output_artifact, postflight = self._verify_export_artifact(
            workspace,
            project,
            output_name,
            export_result,
            workspace_id,
            expected_slide_count=len(slides.slides),
        )
        _invoke_cancel_check(request.cancel_check)
        self._deliver_to_host_temp(
            exported,
            output_artifact.sha256,
            prepared.target,
            prepared.target_identity,
            workspace_id,
        )

        placed_assets = {
            asset_id
            for slide in slides.slides
            for asset_id in slide.asset_ids
        }
        omitted_supplementary = tuple(
            asset_id
            for asset_id in request.supplementary_figure_ids
            if asset_id not in placed_assets
        )
        warnings = tuple(
            f"PPT Master plan omitted supplementary figure: {asset_id}"
            for asset_id in omitted_supplementary
        ) + tuple(
            f"PPT Master postflight warning: {code}"
            for code in postflight.warning_codes
        )
        return ReportRenderResult(
            output_path=request.output_path,
            provenance=self.provenance,
            warnings=warnings,
            requires_host_postprocessing=False,
        )

    def _prepare_request(
        self,
        snapshot: PlanningSnapshot,
        request: ReportRenderRequest,
    ) -> _PreparedRequest:
        if snapshot.phase != PlanningPhase.PLAN_CONFIRMED:
            raise PptMasterReportProviderError(
                stage="request_preflight",
                code="planning_not_confirmed",
            )
        if request.report_type != "ppt":
            raise PptMasterReportProviderError(
                stage="request_preflight",
                code="report_type_unsupported",
            )
        template_style: PptMasterAuthoringTemplate | None = None
        template_source: Path | None = None
        if snapshot.request.template_mode == TemplateMode.FREE_DESIGN:
            if request.template_path or self._template_workspace is not None:
                raise PptMasterReportProviderError(
                    stage="request_preflight",
                    code="unexpected_template_workspace",
                )
        else:
            workspace = self._template_workspace
            if workspace is None or not request.template_path:
                raise PptMasterReportProviderError(
                    stage="request_preflight",
                    code="template_workspace_missing",
                )
            if snapshot.request.template_summary != workspace.style_summary:
                raise PptMasterReportProviderError(
                    stage="request_preflight",
                    code="template_summary_mismatch",
                )
            requested_template = Path(request.template_path).resolve(strict=False)
            requested_size, requested_digest = _attest_template_source(
                requested_template
            )
            workspace_template = workspace.template_path.resolve(strict=False)
            workspace_size, workspace_digest = _attest_template_source(
                workspace_template
            )
            if (
                requested_size != workspace.template_size_bytes
                or requested_digest != workspace.template_sha256
                or workspace_size != workspace.template_size_bytes
                or workspace_digest != workspace.template_sha256
            ):
                raise PptMasterReportProviderError(
                    stage="request_preflight",
                    code="template_attestation_mismatch",
                )
            template_style = PptMasterAuthoringTemplate(
                workspace_id=workspace.workspace_id,
                sha256=workspace.template_sha256,
                size_bytes=workspace.template_size_bytes,
                profile_json=workspace.profile_json,
                style_summary=workspace.style_summary,
            )
            template_source = workspace_template

        target = Path(request.output_path)
        if not target.is_absolute() or target.suffix.casefold() != ".pptx":
            raise PptMasterReportProviderError(
                stage="request_preflight",
                code="host_temp_path_invalid",
            )
        try:
            target_stat = target.stat()
        except OSError as error:
            raise PptMasterReportProviderError(
                stage="request_preflight",
                code="host_temp_missing",
            ) from error
        if (
            not target.is_file()
            or _is_reparse(target)
            or target_stat.st_size != 0
        ):
            raise PptMasterReportProviderError(
                stage="request_preflight",
                code="host_temp_not_empty",
            )

        slides = snapshot.slides
        if slides is None:
            raise PptMasterReportProviderError(
                stage="request_preflight",
                code="slide_plan_missing",
            )
        title = request.structured_report.get("title")
        accepted_titles = {snapshot.request.report_title, slides.deck_title}
        if not isinstance(title, str) or title.strip() not in accepted_titles:
            raise PptMasterReportProviderError(
                stage="request_preflight",
                code="report_title_mismatch",
            )
        report_slides = request.structured_report.get("slides")
        if not isinstance(report_slides, (list, tuple)) or len(report_slides) != len(
            slides.slides
        ):
            raise PptMasterReportProviderError(
                stage="request_preflight",
                code="report_slide_count_mismatch",
            )

        report_assets = {asset.host_id: asset for asset in request.assets}
        planning_assets = {asset.asset_id: asset for asset in snapshot.request.assets}
        if set(report_assets) != set(planning_assets):
            raise PptMasterReportProviderError(
                stage="request_preflight",
                code="asset_roster_mismatch",
            )
        required_ids = set(request.required_figure_ids)
        if required_ids != {
            asset.asset_id for asset in snapshot.request.assets if asset.required
        }:
            raise PptMasterReportProviderError(
                stage="request_preflight",
                code="required_asset_mismatch",
            )

        authoring_assets: list[PptMasterAuthoringAsset] = []
        source_paths: dict[str, Path] = {}
        total_asset_bytes = 0
        for index, planning_asset in enumerate(snapshot.request.assets, start=1):
            report_asset = report_assets[planning_asset.asset_id]
            if report_asset.semantic_label != planning_asset.semantic_label:
                raise PptMasterReportProviderError(
                    stage="request_preflight",
                    code="asset_semantics_mismatch",
                )
            source = report_asset.source_path.resolve(strict=False)
            size, digest = _attest_source_asset(source)
            total_asset_bytes += size
            if total_asset_bytes > _MAX_TOTAL_ASSET_BYTES:
                raise PptMasterReportProviderError(
                    stage="request_preflight",
                    code="asset_total_limit",
                )
            suffix = ".png" if report_asset.media_type == "image/png" else ".jpg"
            project_filename = (
                f"asset_{index:02d}_{_safe_token(planning_asset.asset_id, 48)}{suffix}"
            )
            authoring_assets.append(PptMasterAuthoringAsset(
                asset_id=planning_asset.asset_id,
                project_filename=project_filename,
                media_type=report_asset.media_type,
                semantic_label=planning_asset.semantic_label,
                target=report_asset.target,
                required=planning_asset.required,
                size_bytes=size,
                sha256=digest,
            ))
            source_paths[planning_asset.asset_id] = source

        try:
            structured_report_json = _canonical_json(request.structured_report)
            inclusion_summary = json.loads(_canonical_json(request.inclusion_summary))
        except (TypeError, ValueError, json.JSONDecodeError) as error:
            raise PptMasterReportProviderError(
                stage="request_preflight",
                code="report_not_json_compatible",
            ) from error

        slide_filenames = tuple(
            f"P{intent.sequence:02d}_{_safe_token(intent.slide_id, 60)}.svg"
            for intent in slides.slides
        )
        fingerprint_payload = {
            "schema_version": 1,
            "report_type": request.report_type,
            "structured_report": json.loads(structured_report_json),
            "assets": [asset.model_dump(mode="json") for asset in authoring_assets],
            "required_figure_ids": list(request.required_figure_ids),
            "supplementary_figure_ids": list(request.supplementary_figure_ids),
            "inclusion_summary": inclusion_summary,
            "planning_snapshot_sha256": _model_sha256(snapshot),
            "template_style": (
                template_style.model_dump(mode="json")
                if template_style is not None else None
            ),
        }
        render_request_sha256 = hashlib.sha256(
            _canonical_json(fingerprint_payload).encode("utf-8")
        ).hexdigest()
        context = PptMasterAuthoringContext(
            planning_snapshot=snapshot,
            render_request_sha256=render_request_sha256,
            structured_report_json=structured_report_json,
            slide_filenames=slide_filenames,
            assets=tuple(authoring_assets),
            template_style=template_style,
        )
        return _PreparedRequest(
            context=context,
            source_paths=source_paths,
            target=target.resolve(strict=True),
            target_identity=_TargetIdentity(
                device=target_stat.st_dev,
                inode=target_stat.st_ino,
            ),
            template_source=template_source,
        )

    def _new_workspace_id(self) -> str:
        token = self._id_factory()
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{7,31}", token):
            raise PptMasterReportProviderError(
                stage="request_preflight",
                code="workspace_id_invalid",
            )
        return f"ppt-{token}"

    def _run_stage(
        self,
        *,
        stage: str,
        ordinal: int,
        workspace_id: str,
        snapshot: PlanningSnapshot,
        arguments: Any,
        request: ReportRenderRequest,
        baseline: ControlledToolResult | None,
        allow_failed: bool = False,
    ) -> ControlledToolResult:
        run_id = f"{ordinal:02d}-{stage}"
        controlled_request = ControlledToolRequest(
            run_id=run_id,
            workspace_id=workspace_id,
            arguments=arguments,
        )
        try:
            result = self._runner.run(
                snapshot,
                controlled_request,
                cancel_check=_runner_cancel_probe(request.cancel_check),
            )
        except ControlledToolError as error:
            raise PptMasterReportProviderError(
                stage=stage,
                code=error.code,
                workspace_id=workspace_id,
                run_id=run_id,
            ) from error
        expected_command = controlled_request.arguments.kind
        if (
            result.run_id != run_id
            or result.workspace_id != workspace_id
            or result.command != expected_command
            or result.planning_snapshot_sha256 != _model_sha256(snapshot)
        ):
            raise PptMasterReportProviderError(
                stage=stage,
                code="controlled_receipt_mismatch",
                workspace_id=workspace_id,
            )
        if baseline is not None and (
            result.toolchain != baseline.toolchain
            or result.runtime_sha256 != baseline.runtime_sha256
        ):
            raise PptMasterReportProviderError(
                stage=stage,
                code="controlled_identity_drift",
                workspace_id=workspace_id,
            )
        if result.status != ControlledRunStatus.SUCCEEDED and not allow_failed:
            raise PptMasterReportProviderError(
                stage=stage,
                code=result.error_code or result.status.value,
                workspace_id=workspace_id,
                run_id=run_id,
                exit_code=result.exit_code,
            )
        return result

    def _run_quality_stage(
        self,
        *,
        stage: str,
        ordinal: int,
        workspace_id: str,
        snapshot: PlanningSnapshot,
        project: Path,
        workspace: Path,
        quality_stage: QualityStage,
        request: ReportRenderRequest,
        baseline: ControlledToolResult | None,
    ) -> tuple[ControlledToolResult, QualityReceipt]:
        result = self._run_stage(
            stage=stage,
            ordinal=ordinal,
            workspace_id=workspace_id,
            snapshot=snapshot,
            arguments=SvgQualityArguments(
                project_dir=_project_relative(project, workspace),
                stage=quality_stage,
            ),
            request=request,
            baseline=baseline,
            allow_failed=True,
        )
        if result.status not in {
            ControlledRunStatus.SUCCEEDED,
            ControlledRunStatus.FAILED,
        }:
            raise PptMasterReportProviderError(
                stage=stage,
                code=result.error_code or result.status.value,
                workspace_id=workspace_id,
            )
        try:
            receipt = parse_quality_receipt(result, stage=quality_stage)
        except QualityReceiptError as error:
            code = (
                result.error_code or result.status.value
                if result.status != ControlledRunStatus.SUCCEEDED
                else "quality_receipt_invalid"
            )
            raise PptMasterReportProviderError(
                stage=stage,
                code=code,
                workspace_id=workspace_id,
            ) from error
        if result.succeeded != receipt.passed:
            raise PptMasterReportProviderError(
                stage=stage,
                code="quality_receipt_status_mismatch",
                workspace_id=workspace_id,
            )
        try:
            machine_report = quality_report_artifact_path(result)
        except QualityReceiptError as error:
            raise PptMasterReportProviderError(
                stage=stage,
                code="quality_machine_report_invalid",
                workspace_id=workspace_id,
                run_id=result.run_id,
                exit_code=result.exit_code,
            ) from error
        if receipt.passed:
            canonical_name = (
                "svg_quality_report.json"
                if quality_stage == QualityStage.FINAL
                else "svg_quality_first_page_report.json"
            )
            canonical_report = project / "validation" / canonical_name
            if canonical_report.exists():
                canonical_report.unlink()
            _copy_file_exclusive(
                machine_report,
                canonical_report,
                cancel_check=request.cancel_check,
            )
        _write_json_exclusive(
            project / f"host_{stage}_quality_receipt.json",
            receipt.model_dump(mode="json"),
        )
        return result, receipt

    def _repair_failed_pages(
        self,
        *,
        project: Path,
        context: PptMasterAuthoringContext,
        quality: QualityReceipt,
        method_receipt: MethodReceipt | None,
        authored_receipts: list[dict[str, object]],
        workspace_id: str,
    ) -> tuple[list[dict[str, object]], dict[str, PptMasterAuthoredSlide]]:
        slides = context.planning_snapshot.slides
        if slides is None:
            raise PptMasterReportProviderError(
                stage="quality_repair",
                code="slide_plan_missing",
                workspace_id=workspace_id,
            )
        by_filename = {
            filename: intent
            for intent, filename in zip(
                slides.slides,
                context.slide_filenames,
                strict=True,
            )
        }
        receipt_by_sequence: dict[int, int] = {}
        for index, receipt in enumerate(authored_receipts):
            sequence = receipt.get("sequence")
            if not isinstance(sequence, int):
                raise PptMasterReportProviderError(
                    stage="quality_repair",
                    code="authoring_receipt_invalid",
                    workspace_id=workspace_id,
                )
            receipt_by_sequence[sequence] = index
        updated = list(authored_receipts)
        repaired: dict[str, PptMasterAuthoredSlide] = {}
        for failed in quality.failed_pages:
            intent = by_filename.get(failed.svg_filename)
            if intent is None:
                raise PptMasterReportProviderError(
                    stage="quality_repair",
                    code="quality_page_identity_mismatch",
                    workspace_id=workspace_id,
                )
            svg_path = project / "svg_output" / failed.svg_filename
            try:
                previous_svg = svg_path.read_text(encoding="utf-8")
            except (OSError, UnicodeError) as error:
                raise PptMasterReportProviderError(
                    stage=f"repair_slide_{failed.sequence:02d}",
                    code="repair_source_unavailable",
                    workspace_id=workspace_id,
                ) from error
            page_receipt = QualityReceipt(
                stage=quality.stage,
                run_id=quality.run_id,
                pages=(failed,),
            )
            authored = self._author_slide(
                context,
                PptMasterSlideAuthoringRequest(
                    intent=intent,
                    svg_filename=failed.svg_filename,
                    method_receipt=method_receipt,
                    repair_receipt=page_receipt,
                    previous_svg_text=previous_svg,
                ),
                workspace_id,
            )
            replacement = self._stage_authored_slide(
                project,
                intent,
                failed.svg_filename,
                authored,
                context,
                workspace_id,
                replace_existing=True,
            )
            updated[receipt_by_sequence[failed.sequence]] = replacement
            repaired[failed.svg_filename] = authored
        return updated, repaired

    def _create_project_spec(
        self,
        context: PptMasterAuthoringContext,
        snapshot: PlanningSnapshot,
        workspace_id: str,
    ) -> PptMasterProjectSpec:
        try:
            raw_spec = self._authoring_adapter.create_project_spec(context)
            spec = PptMasterProjectSpec.model_validate(raw_spec)
        except HostAuthoringError as error:
            raise PptMasterReportProviderError(
                stage="author_project_spec",
                code=error.code,
                workspace_id=workspace_id,
            ) from error
        except Exception as error:
            raise PptMasterReportProviderError(
                stage="author_project_spec",
                code=type(error).__name__,
                workspace_id=workspace_id,
            ) from error
        design = snapshot.design
        if design is None or spec.structure.value != design.structure_mode:
            raise PptMasterReportProviderError(
                stage="author_project_spec",
                code="structure_mode_mismatch",
                workspace_id=workspace_id,
            )
        return spec

    def _stage_project_contracts(
        self,
        project: Path,
        spec: PptMasterProjectSpec,
        context: PptMasterAuthoringContext,
    ) -> None:
        _write_text_exclusive(project / "design_spec.md", spec.design_spec_markdown)
        _write_text_exclusive(project / "spec_lock.md", spec.spec_lock_markdown)
        _write_json_exclusive(
            project / "host_authorization.json",
            {
                "schema_version": 1,
                "planning_snapshot_sha256": _model_sha256(
                    context.planning_snapshot
                ),
                "render_request_sha256": context.render_request_sha256,
                "asset_attestations": [
                    asset.model_dump(mode="json") for asset in context.assets
                ],
                "slide_filenames": list(context.slide_filenames),
                "template_style": (
                    context.template_style.model_dump(mode="json")
                    if context.template_style is not None else None
                ),
            },
        )

    def _stage_template_workspace(
        self,
        project: Path,
        prepared: _PreparedRequest,
        request: ReportRenderRequest,
        workspace_id: str,
    ) -> None:
        style = prepared.context.template_style
        if style is None:
            return
        source = prepared.template_source
        if source is None:
            raise PptMasterReportProviderError(
                stage="template_stage",
                code="template_source_missing",
                workspace_id=workspace_id,
            )
        target = project / "templates" / style.project_filename
        try:
            digest, size = _copy_file_exclusive(
                source,
                target,
                cancel_check=request.cancel_check,
            )
        except Exception as error:
            target.unlink(missing_ok=True)
            raise PptMasterReportProviderError(
                stage="template_stage",
                code=type(error).__name__,
                workspace_id=workspace_id,
            ) from error
        if digest != style.sha256 or size != style.size_bytes:
            target.unlink(missing_ok=True)
            raise PptMasterReportProviderError(
                stage="template_stage",
                code="template_changed_after_attestation",
                workspace_id=workspace_id,
            )
        _write_json_exclusive(
            project / "templates" / "host-template-style.json",
            style.model_dump(mode="json"),
        )

    def _stage_assets(
        self,
        project: Path,
        prepared: _PreparedRequest,
        request: ReportRenderRequest,
        workspace_id: str,
    ) -> None:
        images = project / "images"
        for asset in prepared.context.assets:
            _invoke_cancel_check(request.cancel_check)
            source = prepared.source_paths[asset.asset_id]
            target = images / asset.project_filename
            try:
                digest, size = _copy_file_exclusive(
                    source,
                    target,
                    cancel_check=request.cancel_check,
                )
            except Exception as error:
                target.unlink(missing_ok=True)
                raise PptMasterReportProviderError(
                    stage="asset_stage",
                    code=type(error).__name__,
                    workspace_id=workspace_id,
                ) from error
            if digest != asset.sha256 or size != asset.size_bytes:
                target.unlink(missing_ok=True)
                raise PptMasterReportProviderError(
                    stage="asset_stage",
                    code="asset_changed_after_attestation",
                    workspace_id=workspace_id,
                )

    def _author_slide(
        self,
        context: PptMasterAuthoringContext,
        request: PptMasterSlideAuthoringRequest,
        workspace_id: str,
    ) -> PptMasterAuthoredSlide:
        try:
            raw_slide = self._authoring_adapter.author_slide(context, request)
            return PptMasterAuthoredSlide.model_validate(raw_slide)
        except HostAuthoringError as error:
            raise PptMasterReportProviderError(
                stage=f"author_slide_{request.intent.sequence:02d}",
                code=error.code,
                workspace_id=workspace_id,
            ) from error
        except Exception as error:
            raise PptMasterReportProviderError(
                stage=f"author_slide_{request.intent.sequence:02d}",
                code=type(error).__name__,
                workspace_id=workspace_id,
            ) from error

    def _load_or_author_slide(
        self,
        context: PptMasterAuthoringContext,
        request: PptMasterSlideAuthoringRequest,
        workspace_id: str,
        *,
        deck_contract_sha256: str,
        toolchain_sha256: str,
    ) -> tuple[PptMasterAuthoredSlide, SlideCheckpointKey]:
        key = self._slide_checkpoint_key(
            context,
            request,
            deck_contract_sha256=deck_contract_sha256,
            toolchain_sha256=toolchain_sha256,
        )
        if self._checkpoint_store is not None:
            cached = self._checkpoint_store.load(key)
            if cached is not None:
                return cached, key
        return self._author_slide(context, request, workspace_id), key

    def _publish_slide_checkpoint(
        self,
        key: SlideCheckpointKey,
        authored: PptMasterAuthoredSlide,
    ) -> None:
        if self._checkpoint_store is not None:
            self._checkpoint_store.publish(key, authored)

    def _slide_checkpoint_key(
        self,
        context: PptMasterAuthoringContext,
        request: PptMasterSlideAuthoringRequest,
        *,
        deck_contract_sha256: str,
        toolchain_sha256: str,
    ) -> SlideCheckpointKey:
        try:
            report = json.loads(context.structured_report_json)
        except json.JSONDecodeError as error:  # validated during request preparation
            raise PptMasterReportProviderError(
                stage="checkpoint_key",
                code="structured_report_invalid",
            ) from error
        report_slides = report.get("slides", []) if isinstance(report, dict) else []
        source_slide = (
            report_slides[request.intent.sequence - 1]
            if isinstance(report_slides, list)
            and len(report_slides) >= request.intent.sequence
            else {}
        )
        asset_by_id = {asset.asset_id: asset for asset in context.assets}
        slide_payload = {
            "intent": request.intent.model_dump(mode="json"),
            "svg_filename": request.svg_filename,
            "source_slide": source_slide,
            "assets": [
                asset_by_id[asset_id].model_dump(mode="json")
                for asset_id in request.intent.asset_ids
            ],
            "template_style": (
                context.template_style.model_dump(mode="json")
                if context.template_style is not None else None
            ),
        }
        identity = getattr(self._authoring_adapter, "model_identity", "")
        if not identity:
            identity = (
                f"{type(self._authoring_adapter).__module__}."
                f"{type(self._authoring_adapter).__qualname__}"
            )
        return SlideCheckpointKey(
            planning_context_sha256=hashlib.sha256(
                _canonical_json({
                    "request": context.planning_snapshot.request.model_dump(
                        mode="json",
                        exclude={"assets"},
                    ),
                    "design": (
                        context.planning_snapshot.design.model_dump(mode="json")
                        if context.planning_snapshot.design is not None else None
                    ),
                }).encode("utf-8")
            ).hexdigest(),
            deck_contract_sha256=deck_contract_sha256,
            toolchain_sha256=toolchain_sha256,
            authoring_model_identity=str(identity),
            slide_input_sha256=hashlib.sha256(
                _canonical_json(slide_payload).encode("utf-8")
            ).hexdigest(),
            method_receipt_sha256=(
                _model_sha256(request.method_receipt)
                if request.method_receipt is not None
                else hashlib.sha256(b"").hexdigest()
            ),
        )

    def _stage_authored_slide(
        self,
        project: Path,
        intent: SlideIntent,
        filename: str,
        authored: PptMasterAuthoredSlide,
        context: PptMasterAuthoringContext,
        workspace_id: str,
        *,
        replace_existing: bool = False,
    ) -> dict[str, object]:
        stage = f"author_slide_{intent.sequence:02d}"
        if authored.slide_id != intent.slide_id or authored.sequence != intent.sequence:
            raise PptMasterReportProviderError(
                stage=stage,
                code="slide_identity_mismatch",
                workspace_id=workspace_id,
            )
        if authored.used_asset_ids != intent.asset_ids:
            raise PptMasterReportProviderError(
                stage=stage,
                code="slide_asset_plan_mismatch",
                workspace_id=workspace_id,
            )
        asset_map = {asset.asset_id: asset for asset in context.assets}
        expected_filenames = {
            asset_map[asset_id].project_filename for asset_id in intent.asset_ids
        }
        try:
            svg_text = _normalize_svg_contract(
                _sanitize_svg_for_ppt_master(authored.svg_text),
                page_role=_page_role_for_intent(intent),
            )
            referenced_filenames = _validate_svg(svg_text)
        except ValueError as error:
            raise PptMasterReportProviderError(
                stage=stage,
                code="svg_contract_invalid",
                workspace_id=workspace_id,
            ) from error
        if referenced_filenames != expected_filenames:
            raise PptMasterReportProviderError(
                stage=stage,
                code="svg_asset_reference_mismatch",
                workspace_id=workspace_id,
            )
        if intent.speaker_notes_intent and not authored.speaker_notes_markdown:
            raise PptMasterReportProviderError(
                stage=stage,
                code="speaker_notes_missing",
                workspace_id=workspace_id,
            )
        svg_path = project / "svg_output" / filename
        notes_path = project / "notes" / f"{Path(filename).stem}.md"
        writer = _write_text_atomic if replace_existing else _write_text_exclusive
        writer(svg_path, svg_text)
        if authored.speaker_notes_markdown:
            writer(notes_path, authored.speaker_notes_markdown)
        return {
            "slide_id": intent.slide_id,
            "sequence": intent.sequence,
            "svg_filename": filename,
            "svg_sha256": _sha256_file(svg_path),
            "used_asset_ids": list(authored.used_asset_ids),
            "notes_sha256": (
                _sha256_file(notes_path) if notes_path.is_file() else None
            ),
        }

    def _write_authoring_manifest(
        self,
        project: Path,
        context: PptMasterAuthoringContext,
        slide_receipts: list[dict[str, object]],
        *,
        replace_existing: bool = False,
    ) -> None:
        payload = {
                "schema_version": 1,
                "planning_snapshot_sha256": _model_sha256(
                    context.planning_snapshot
                ),
                "render_request_sha256": context.render_request_sha256,
                "slides": slide_receipts,
            }
        path = project / "host_authoring_manifest.json"
        if replace_existing:
            _write_text_atomic(path, _canonical_json(payload))
        else:
            _write_json_exclusive(path, payload)

    def _verify_export_artifact(
        self,
        workspace: Path,
        project: Path,
        output_name: str,
        result: ControlledToolResult,
        workspace_id: str,
        *,
        expected_slide_count: int,
    ) -> tuple[Path, ToolArtifactAttestation, PptxPostflightReceipt]:
        expected_relative = f"output/{output_name}"
        project_relative = _project_relative(project, workspace)
        try:
            postflight, artifact = parse_pptx_postflight_receipt(
                result,
                project_relative=project_relative,
                output_name=output_name,
                expected_slide_count=expected_slide_count,
            )
        except QualityReceiptError as error:
            raise PptMasterReportProviderError(
                stage="export_pptx",
                code="postflight_receipt_invalid",
                workspace_id=workspace_id,
                run_id=result.run_id,
                exit_code=result.exit_code,
            ) from error
        exported = (workspace / "output" / output_name).resolve(strict=True)
        try:
            exported.relative_to(workspace)
        except ValueError as error:
            raise PptMasterReportProviderError(
                stage="export_pptx",
                code="export_path_escape",
                workspace_id=workspace_id,
            ) from error
        if (
            not exported.is_file()
            or _is_reparse(exported)
            or exported.stat().st_size != artifact.size_bytes
            or _sha256_file(exported) != artifact.sha256
        ):
            raise PptMasterReportProviderError(
                stage="export_pptx",
                code="export_artifact_attestation_mismatch",
                workspace_id=workspace_id,
            )
        if artifact.relative_path != expected_relative:
            raise PptMasterReportProviderError(
                stage="export_pptx",
                code="export_artifact_contract_mismatch",
                workspace_id=workspace_id,
                run_id=result.run_id,
                exit_code=result.exit_code,
            )
        return exported, artifact, postflight

    def _deliver_to_host_temp(
        self,
        exported: Path,
        expected_sha256: str,
        target: Path,
        target_identity: _TargetIdentity,
        workspace_id: str,
    ) -> None:
        stage_fd, stage_name = tempfile.mkstemp(
            dir=target.parent,
            prefix=".dp-ppt-master-",
            suffix=".pptx",
        )
        os.close(stage_fd)
        stage = Path(stage_name)
        try:
            digest = hashlib.sha256()
            with exported.open("rb") as source, stage.open("wb") as output:
                while chunk := source.read(_COPY_CHUNK_BYTES):
                    output.write(chunk)
                    digest.update(chunk)
                output.flush()
                os.fsync(output.fileno())
            if digest.hexdigest() != expected_sha256:
                raise PptMasterReportProviderError(
                    stage="host_temp_delivery",
                    code="delivery_digest_mismatch",
                    workspace_id=workspace_id,
                )
            current = target.stat()
            if (
                current.st_dev != target_identity.device
                or current.st_ino != target_identity.inode
                or current.st_size != 0
                or _is_reparse(target)
            ):
                raise PptMasterReportProviderError(
                    stage="host_temp_delivery",
                    code="host_temp_changed",
                    workspace_id=workspace_id,
                )
            os.replace(stage, target)
        finally:
            stage.unlink(missing_ok=True)


def _locate_initialized_project(workspace: Path, project_name: str) -> Path:
    project_root = workspace / "project"
    candidates = tuple(path for path in project_root.iterdir() if path.is_dir())
    expected = re.compile(rf"^{re.escape(project_name)}_ppt169_\d{{8}}$")
    if (
        len(candidates) != 1
        or not expected.fullmatch(candidates[0].name)
        or _is_reparse(candidates[0])
    ):
        raise PptMasterReportProviderError(
            stage="project_init",
            code="initialized_project_contract_mismatch",
            workspace_id=workspace.name,
        )
    return candidates[0].resolve(strict=True)


def _project_relative(project: Path, workspace: Path) -> str:
    try:
        return project.relative_to(workspace).as_posix()
    except ValueError as error:
        raise PptMasterReportProviderError(
            stage="project_init",
            code="project_path_escape",
            workspace_id=workspace.name,
        ) from error


def _attest_source_asset(source: Path) -> tuple[int, str]:
    if not source.is_file() or _is_reparse(source):
        raise PptMasterReportProviderError(
            stage="request_preflight",
            code="asset_source_unsafe",
        )
    size = source.stat().st_size
    if size <= 0 or size > _MAX_ASSET_BYTES:
        raise PptMasterReportProviderError(
            stage="request_preflight",
            code="asset_file_limit",
        )
    return size, _sha256_file(source)


def _attest_template_source(source: Path) -> tuple[int, str]:
    if (
        source.suffix.casefold() != ".pptx"
        or not source.is_file()
        or _is_reparse(source)
    ):
        raise PptMasterReportProviderError(
            stage="request_preflight",
            code="template_source_unsafe",
        )
    size = source.stat().st_size
    if size <= 0 or size > _MAX_TEMPLATE_BYTES:
        raise PptMasterReportProviderError(
            stage="request_preflight",
            code="template_file_limit",
        )
    return size, _sha256_file(source)


def _copy_file_exclusive(
    source: Path,
    target: Path,
    *,
    cancel_check: Callable[[], None] | None,
) -> tuple[str, int]:
    digest = hashlib.sha256()
    total = 0
    with source.open("rb") as input_file, target.open("xb") as output_file:
        while chunk := input_file.read(_COPY_CHUNK_BYTES):
            _invoke_cancel_check(cancel_check)
            output_file.write(chunk)
            digest.update(chunk)
            total += len(chunk)
        output_file.flush()
        os.fsync(output_file.fileno())
    return digest.hexdigest(), total


def _validate_svg(svg_text: str) -> set[str]:
    upper = svg_text.upper()
    if "<!DOCTYPE" in upper or "<!ENTITY" in upper:
        raise ValueError("SVG declarations are not allowed")
    try:
        root = ElementTree.fromstring(svg_text)
    except ElementTree.ParseError as error:
        raise ValueError("SVG is not well-formed XML") from error
    if root.tag != f"{{{_SVG_NAMESPACE}}}svg":
        raise ValueError("SVG root namespace is invalid")
    view_box = tuple((root.attrib.get("viewBox") or "").split())
    if view_box != ("0", "0", "1280", "720"):
        raise ValueError("SVG viewBox must be 0 0 1280 720")

    referenced: set[str] = set()
    href_names = {"href", f"{{{_XLINK_NAMESPACE}}}href"}
    for element in root.iter():
        local_name = element.tag.rsplit("}", 1)[-1]
        if local_name in {"script", "foreignObject", "iframe", "object"}:
            raise ValueError(f"unsupported SVG element: {local_name}")
        for name, value in element.attrib.items():
            normalized = value.strip()
            if name in href_names or name.rsplit("}", 1)[-1] == "href":
                if normalized.startswith("data:image/"):
                    continue
                pure = PurePosixPath(normalized.replace("\\", "/"))
                if (
                    len(pure.parts) != 3
                    or pure.parts[:2] != ("..", "images")
                    or not re.fullmatch(_PROJECT_FILE_PATTERN, pure.name)
                ):
                    raise ValueError("SVG image reference escapes the project pool")
                referenced.add(pure.name)
            if "url(" in normalized.casefold():
                for match in re.findall(r"url\(([^)]+)\)", normalized, re.I):
                    token = match.strip(" \t\r\n\"'")
                    if not token.startswith("#"):
                        raise ValueError("external SVG URL references are not allowed")
    return referenced


def _page_role_for_intent(intent: SlideIntent) -> str:
    return _LAYOUT_TO_PAGE_ROLE.get(intent.layout.value, "content")


def _normalize_svg_contract(svg_text: str, *, page_role: str) -> str:
    """Deterministically supply Host-owned structural SVG metadata.

    Model authoring owns visible composition. Stable semantic IDs, the root page
    role, and direct-root module subcanvases are compiler metadata and therefore
    remain Host-owned. Widening a declared module to contain its own simple
    geometry cannot change rendering, but prevents a repair from trading one
    structural error for another.
    """
    try:
        root = ElementTree.fromstring(svg_text)
    except ElementTree.ParseError:
        return svg_text

    changed = root.attrib.get("data-pptx-page-role") != page_role
    root.attrib["data-pptx-page-role"] = page_role
    reserved_ids = {
        element_id
        for element in root.iter()
        if (element_id := (element.attrib.get("id") or "").strip())
    }
    used_ids: set[str] = set()
    id_occurrences: dict[str, int] = {}
    role_occurrences: dict[str, int] = {}
    direct_root_group_ids = {
        id(element): index
        for index, element in enumerate(
            (
                child
                for child in list(root)
                if child.tag.rsplit("}", 1)[-1] == "g"
            ),
            start=1,
        )
    }
    for element in root.iter():
        element_id = (element.attrib.get("id") or "").strip()
        if element_id:
            occurrence = id_occurrences.get(element_id, 0) + 1
            id_occurrences[element_id] = occurrence
            if element_id in used_ids:
                element_id = _allocate_svg_id(
                    element_id,
                    reserved_ids | used_ids,
                    start=occurrence,
                )
                element.attrib["id"] = element_id
                reserved_ids.add(element_id)
                changed = True
            used_ids.add(element_id)
            continue

        role = (element.attrib.get("data-pptx-role") or "").strip().casefold()
        root_group_index = direct_root_group_ids.get(id(element))
        if not role and root_group_index is None:
            continue
        base = (
            _SEMANTIC_ROLE_ID_BASE.get(role, f"host-{_stable_id_token(role)}")
            if role
            else f"host-module-{root_group_index:02d}"
        )
        occurrence = role_occurrences.get(base, 0) + 1
        role_occurrences[base] = occurrence
        candidate = _allocate_svg_id(
            base,
            reserved_ids | used_ids,
            start=occurrence,
        )
        element.attrib["id"] = candidate
        reserved_ids.add(candidate)
        used_ids.add(candidate)
        changed = True

    for child in list(root):
        if child.tag.rsplit("}", 1)[-1] != "g":
            continue
        current = _parse_svg_bounds(child.attrib.get("data-pptx-bounds"))
        content = _simple_group_content_bounds(child)
        if content is None:
            continue
        merged = _merge_svg_bounds(current, content)
        normalized = _format_svg_bounds(merged)
        if child.attrib.get("data-pptx-bounds") != normalized:
            child.attrib["data-pptx-bounds"] = normalized
            changed = True

    if not changed:
        return svg_text
    return ElementTree.tostring(root, encoding="unicode")


def _stable_id_token(value: str) -> str:
    token = re.sub(r"[^a-z0-9-]+", "-", value.casefold()).strip("-")
    return token or "element"


def _allocate_svg_id(base: str, unavailable: set[str], *, start: int = 1) -> str:
    suffix = max(start, 1)
    candidate = base if suffix == 1 else f"{base}-{suffix}"
    while candidate in unavailable:
        suffix += 1
        candidate = f"{base}-{suffix}"
    return candidate


def _parse_svg_number(value: str | None) -> float | None:
    if value is None:
        return None
    match = re.fullmatch(
        r"\s*([+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?)"
        r"(?:px)?\s*",
        value,
    )
    if match is None:
        return None
    number = float(match.group(1))
    return number if number == number and abs(number) != float("inf") else None


def _parse_svg_bounds(
    value: str | None,
) -> tuple[float, float, float, float] | None:
    if value is None:
        return None
    raw = [item for item in re.split(r"[\s,]+", value.strip()) if item]
    if len(raw) != 4:
        return None
    try:
        x, y, width, height = (float(item) for item in raw)
    except ValueError:
        return None
    values = (x, y, width, height)
    if (
        any(number != number or abs(number) == float("inf") for number in values)
        or width <= 0
        or height <= 0
    ):
        return None
    return x, y, x + width, y + height


def _simple_group_content_bounds(
    group: ElementTree.Element,
) -> tuple[float, float, float, float] | None:
    """Estimate untransformed direct-root module bounds for safe metadata repair."""
    if any(element.attrib.get("transform") for element in group.iter()):
        return None
    bounds: list[tuple[float, float, float, float]] = []
    for element in group.iter():
        if element is group:
            continue
        local_name = element.tag.rsplit("}", 1)[-1]
        if local_name in {"rect", "image"}:
            x = _parse_svg_number(element.attrib.get("x"))
            y = _parse_svg_number(element.attrib.get("y"))
            width = _parse_svg_number(element.attrib.get("width"))
            height = _parse_svg_number(element.attrib.get("height"))
            if None not in (x, y, width, height) and width and height:
                assert x is not None and y is not None
                bounds.append((x, y, x + width, y + height))
        elif local_name == "line":
            coordinates = tuple(
                _parse_svg_number(element.attrib.get(name))
                for name in ("x1", "y1", "x2", "y2")
            )
            if None not in coordinates:
                x1, y1, x2, y2 = coordinates
                assert x1 is not None and y1 is not None
                assert x2 is not None and y2 is not None
                bounds.append((min(x1, x2), min(y1, y2), max(x1, x2), max(y1, y2)))
        elif local_name == "text":
            text = "".join(element.itertext()).strip()
            x = _parse_svg_number(element.attrib.get("x"))
            y = _parse_svg_number(element.attrib.get("y"))
            font_size = _parse_svg_number(element.attrib.get("font-size"))
            if text and x is not None and y is not None and font_size is not None:
                width = len(text) * font_size
                anchor = (element.attrib.get("text-anchor") or "start").strip()
                left = x - width if anchor == "end" else x - width / 2 if anchor == "middle" else x
                bounds.append((left, y - font_size * 0.85, left + width, y + font_size * 0.35))
    if not bounds:
        return None
    return (
        min(item[0] for item in bounds),
        min(item[1] for item in bounds),
        max(item[2] for item in bounds),
        max(item[3] for item in bounds),
    )


def _merge_svg_bounds(
    declared: tuple[float, float, float, float] | None,
    content: tuple[float, float, float, float],
) -> tuple[float, float, float, float]:
    if declared is None:
        merged = content
    else:
        merged = (
            min(declared[0], content[0]),
            min(declared[1], content[1]),
            max(declared[2], content[2]),
            max(declared[3], content[3]),
        )
    return (
        max(0.0, merged[0]),
        max(0.0, merged[1]),
        min(1280.0, merged[2]),
        min(720.0, merged[3]),
    )


def _format_svg_bounds(bounds: tuple[float, float, float, float]) -> str:
    x1, y1, x2, y2 = bounds
    return " ".join(_format_svg_number(value) for value in (x1, y1, x2 - x1, y2 - y1))


def _format_svg_number(value: float) -> str:
    rounded = round(value, 1)
    return str(int(rounded)) if rounded.is_integer() else f"{rounded:.1f}"


def _quality_repair_is_monotonic(
    before: QualityReceipt,
    after: QualityReceipt,
) -> bool:
    """Require every remaining hard-error rule to have existed before repair."""
    before_rules = {
        page.page_id: {
            issue.rule_id for issue in page.issues if issue.severity == "error"
        }
        for page in before.failed_pages
    }
    before_error_count = sum(
        issue.severity == "error"
        for page in before.failed_pages
        for issue in page.issues
    )
    after_error_count = 0
    for page in after.failed_pages:
        current = {
            issue.rule_id for issue in page.issues if issue.severity == "error"
        }
        after_error_count += sum(
            issue.severity == "error" for issue in page.issues
        )
        if not current.issubset(before_rules.get(page.page_id, set())):
            return False
    return after_error_count < before_error_count


def _capture_failed_page_sources(
    project: Path,
    quality: QualityReceipt,
) -> dict[str, tuple[str, str | None]]:
    captured: dict[str, tuple[str, str | None]] = {}
    for page in quality.failed_pages:
        svg_path = project / "svg_output" / page.svg_filename
        notes_path = project / "notes" / f"{Path(page.svg_filename).stem}.md"
        captured[page.svg_filename] = (
            svg_path.read_text(encoding="utf-8"),
            notes_path.read_text(encoding="utf-8") if notes_path.is_file() else None,
        )
    return captured


def _restore_failed_page_sources(
    project: Path,
    captured: Mapping[str, tuple[str, str | None]],
) -> None:
    for filename, (svg_text, notes_text) in captured.items():
        _write_text_atomic(project / "svg_output" / filename, svg_text)
        notes_path = project / "notes" / f"{Path(filename).stem}.md"
        if notes_text is not None:
            _write_text_atomic(notes_path, notes_text)


def _sanitize_svg_for_ppt_master(svg_text: str) -> str:
    """Inline simple Host CSS and remove markup forbidden by PPT Master 2.7.0.

    This is a defense-in-depth safety net; the authoring system prompt is the
    primary enforcement.  Residual class CSS is converted to presentation
    attributes before forbidden markup is removed.  Unsupported CSS fails
    closed instead of silently discarding visual styling.
    """
    try:
        root = ElementTree.fromstring(svg_text)
    except ElementTree.ParseError:
        return svg_text

    class_rules: list[tuple[str, dict[str, str]]] = []
    style_elements: list[tuple[ElementTree.Element, ElementTree.Element]] = []
    for parent in root.iter():
        for child in parent:
            if child.tag.rsplit("}", 1)[-1] != "style":
                continue
            css_text = "".join(child.itertext())
            class_rules.extend(_parse_simple_class_css(css_text))
            style_elements.append((parent, child))

    changed = False
    for element in root.iter():
        local_name = element.tag.rsplit("}", 1)[-1]
        is_image = local_name == "image"

        class_names = frozenset((element.attrib.get("class") or "").split())
        resolved: dict[str, str] = {}
        for class_name, declarations in class_rules:
            if class_name in class_names:
                resolved.update(declarations)
        for name, value in resolved.items():
            element.attrib.setdefault(name, value)

        inline_style = element.attrib.get("style")
        if inline_style:
            element.attrib.update(_parse_css_declarations(inline_style))

        for forbidden_attribute in ("class", "style"):
            if forbidden_attribute in element.attrib:
                del element.attrib[forbidden_attribute]
                changed = True

        if not is_image and "clip-path" in element.attrib:
            del element.attrib["clip-path"]
            changed = True

    for parent, style_element in style_elements:
        parent.remove(style_element)
        changed = True

    for parent in root.iter():
        clip_children = [
            child for child in parent
            if child.tag.rsplit("}", 1)[-1] == "clipPath"
        ]
        for clip_elem in clip_children:
            parent.remove(clip_elem)
            changed = True

    if not changed:
        return svg_text

    # Serialize back with XML declaration omitted for PPT Master compatibility
    result = ElementTree.tostring(root, encoding="unicode")
    return result


def _parse_simple_class_css(css_text: str) -> list[tuple[str, dict[str, str]]]:
    css_text = _CSS_COMMENT.sub("", css_text)
    rules: list[tuple[str, dict[str, str]]] = []
    cursor = 0
    for match in _CSS_RULE.finditer(css_text):
        if css_text[cursor:match.start()].strip():
            raise ValueError("unsupported SVG CSS syntax")
        selectors_text, declarations_text = match.groups()
        declarations = _parse_css_declarations(declarations_text)
        for selector_text in selectors_text.split(","):
            selector = selector_text.strip()
            selector_match = _SIMPLE_CSS_CLASS_SELECTOR.fullmatch(selector)
            if selector_match is None:
                raise ValueError("only simple SVG class selectors can be inlined")
            rules.append((selector_match.group(1), declarations))
        cursor = match.end()
    if css_text[cursor:].strip():
        raise ValueError("unsupported SVG CSS syntax")
    return rules


def _parse_css_declarations(value: str) -> dict[str, str]:
    declarations: dict[str, str] = {}
    for raw_declaration in value.split(";"):
        declaration = raw_declaration.strip()
        if not declaration:
            continue
        if ":" not in declaration:
            raise ValueError("invalid SVG CSS declaration")
        raw_name, raw_value = declaration.split(":", 1)
        name = raw_name.strip().lower()
        css_value = raw_value.strip()
        if name not in _CSS_PRESENTATION_ATTRIBUTES or not css_value:
            raise ValueError(f"unsupported SVG CSS property: {name or '<empty>'}")
        if "!important" in css_value.casefold():
            raise ValueError("SVG CSS !important is not supported")
        if name in {"font-size", "stroke-width"}:
            numeric = re.fullmatch(r"([0-9]+(?:\.[0-9]+)?)px", css_value, re.I)
            if numeric is not None:
                css_value = numeric.group(1)
        declarations[name] = css_value
    return declarations


def _runner_cancel_probe(
    callback: Callable[[], None] | None,
) -> Callable[[], bool] | None:
    if callback is None:
        return None

    def probe() -> bool:
        return bool(callback())

    return probe


def _invoke_cancel_check(callback: Callable[[], None] | None) -> None:
    if callback is not None:
        callback()


def _write_text_exclusive(path: Path, value: str) -> None:
    with path.open("x", encoding="utf-8", newline="\n") as output:
        output.write(value)
        output.flush()
        os.fsync(output.fileno())


def _write_text_atomic(path: Path, value: str) -> None:
    stage_fd, stage_name = tempfile.mkstemp(
        dir=path.parent,
        prefix=f".{path.name}.",
        suffix=".tmp",
    )
    os.close(stage_fd)
    stage = Path(stage_name)
    try:
        with stage.open("w", encoding="utf-8", newline="\n") as output:
            output.write(value)
            output.flush()
            os.fsync(output.fileno())
        os.replace(stage, path)
    finally:
        stage.unlink(missing_ok=True)


def _write_json_exclusive(path: Path, value: object) -> None:
    encoded = _canonical_json(value)
    _write_text_exclusive(path, encoded)


def _canonical_json(value: object) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def _model_sha256(model: BaseModel) -> str:
    return hashlib.sha256(
        _canonical_json(model.model_dump(mode="json", exclude_none=True)).encode(
            "utf-8"
        )
    ).hexdigest()


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        while chunk := source.read(_COPY_CHUNK_BYTES):
            digest.update(chunk)
    return digest.hexdigest()


def _safe_token(value: str, limit: int) -> str:
    normalized = re.sub(r"[^A-Za-z0-9._-]+", "_", value).strip("._-")
    return (normalized or "item")[:limit]


def _is_reparse(path: Path) -> bool:
    try:
        info = path.lstat()
    except OSError:
        return False
    if stat.S_ISLNK(info.st_mode):
        return True
    return bool(getattr(info, "st_file_attributes", 0) & 0x400)
