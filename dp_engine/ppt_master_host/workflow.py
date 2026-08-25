"""Host planning workflow used by the report-workbench Controller.

This stateful Module wraps immutable Planning Snapshots with an input
fingerprint, explicit user confirmation actions, a path-free preview, and a
deterministic render payload.  It performs no UI work and no tool execution.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from .bundle_store import (
    PPT_MASTER_2_7_0_TOOLCHAIN_TREE_SHA256,
    get_default_ppt_master_store_paths,
)
from .planning import (
    ConfirmDesign,
    ConfirmOutline,
    ConfirmPlan,
    GenerateDesign,
    GenerateOutline,
    GenerateSlides,
    HostPlanningStateMachine,
    PlanningPhase,
    PlanningRequest,
    PlanningSnapshot,
    StructuredPlanningModel,
)
from .source_bundle import PPT_MASTER_2_7_0_CONTRACT
from .template_workspace import PptMasterTemplateWorkspace


PptMasterWorkflowAction = Literal[
    "confirm_outline",
    "confirm_design",
    "confirm_plan",
]


class PptMasterWorkflowError(RuntimeError):
    """A named Host workflow operation failed."""

    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(message)


class PptMasterWorkflowView(BaseModel):
    """Path-free view model displayed by the dumb workbench component."""

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        str_strip_whitespace=True,
    )

    phase: PlanningPhase
    status_text: str = Field(min_length=1, max_length=300)
    preview_markdown: str = Field(min_length=1, max_length=100_000)
    next_action: PptMasterWorkflowAction | None = None
    next_action_label: str = ""
    ready_for_authoring: bool = False
    valid_for_current_inputs: bool = True


class PptMasterPlanningWorkflow:
    """Explicit confirmation workflow around the Planning State Machine."""

    def __init__(
        self,
        model: StructuredPlanningModel,
        *,
        input_fingerprint: str,
        template_workspace: PptMasterTemplateWorkspace | None = None,
    ) -> None:
        if not _is_digest(input_fingerprint):
            raise ValueError("workflow input fingerprint must be SHA-256")
        self._machine = HostPlanningStateMachine(model)
        self._input_fingerprint = input_fingerprint
        self._template_workspace = template_workspace
        self._snapshot: PlanningSnapshot | None = None
        self._invalidated_reason = ""

    @property
    def snapshot(self) -> PlanningSnapshot | None:
        return self._snapshot

    @property
    def input_fingerprint(self) -> str:
        return self._input_fingerprint

    @property
    def template_workspace(self) -> PptMasterTemplateWorkspace | None:
        return self._template_workspace

    @property
    def ready_for_authoring(self) -> bool:
        return bool(
            not self._invalidated_reason
            and self._snapshot is not None
            and self._snapshot.ready_for_authoring
        )

    def generate_outline(self, request: PlanningRequest) -> PlanningSnapshot:
        if self._snapshot is not None:
            raise PptMasterWorkflowError(
                "workflow_already_started",
                "PPT Master 规划已开始；输入改变后请重新建立工作流。",
            )
        snapshot = self._machine.start(request)
        self._snapshot = self._machine.apply(snapshot, GenerateOutline())
        return self._snapshot

    def advance(
        self,
        action: PptMasterWorkflowAction,
        *,
        current_input_fingerprint: str,
    ) -> PlanningSnapshot:
        self.require_current_inputs(current_input_fingerprint)
        snapshot = self._snapshot
        if snapshot is None:
            raise PptMasterWorkflowError(
                "workflow_not_started",
                "请先生成 PPT Master 规划大纲。",
            )
        if action == "confirm_outline":
            snapshot = self._machine.apply(snapshot, ConfirmOutline())
            snapshot = self._machine.apply(snapshot, GenerateDesign())
        elif action == "confirm_design":
            snapshot = self._machine.apply(snapshot, ConfirmDesign())
            # ── TEMPORARY BATCH 3.6.6: capture DESIGN_CONFIRMED before GenerateSlides ──
            _capture_design_confirmed(snapshot)
            snapshot = self._machine.apply(snapshot, GenerateSlides())
            # ── TEMPORARY BATCH 3.6.6: capture SLIDES_READY after GenerateSlides ──
            _capture_slides_ready(snapshot)
        elif action == "confirm_plan":
            snapshot = self._machine.apply(snapshot, ConfirmPlan())
            # ── TEMPORARY BATCH 3.6.6: capture PLAN_CONFIRMED after ConfirmPlan ──
            _capture_plan_confirmed(snapshot)
        else:
            raise PptMasterWorkflowError(
                "action_unsupported",
                f"不支持的 PPT Master 规划动作：{action}",
            )
        self._snapshot = snapshot
        return snapshot

    def invalidate(self, reason: str) -> None:
        self._invalidated_reason = reason.strip() or "报告输入已改变"

    def require_current_inputs(self, current_input_fingerprint: str) -> None:
        if self._invalidated_reason:
            raise PptMasterWorkflowError(
                "workflow_invalidated",
                f"PPT Master 规划已失效：{self._invalidated_reason}。请重新生成方案。",
            )
        if current_input_fingerprint != self._input_fingerprint:
            self.invalidate("需求、资料、诊断、模板、后端或技能素材发生变化")
            raise PptMasterWorkflowError(
                "input_fingerprint_changed",
                "报告输入已改变，原确认指纹不可继续使用；请重新生成方案。",
            )

    def view(self) -> PptMasterWorkflowView:
        snapshot = self._snapshot
        if snapshot is None:
            raise PptMasterWorkflowError(
                "workflow_not_started",
                "PPT Master 规划尚未开始。",
            )
        if self._invalidated_reason:
            return PptMasterWorkflowView(
                phase=snapshot.phase,
                status_text=f"规划已失效：{self._invalidated_reason}",
                preview_markdown=_preview(snapshot),
                ready_for_authoring=False,
                valid_for_current_inputs=False,
            )
        action_by_phase: dict[
            PlanningPhase,
            tuple[PptMasterWorkflowAction, str],
        ] = {
            PlanningPhase.OUTLINE_READY: (
                "confirm_outline",
                "确认大纲并生成设计方案",
            ),
            PlanningPhase.DESIGN_READY: (
                "confirm_design",
                "确认设计并生成逐页计划",
            ),
            PlanningPhase.SLIDES_READY: (
                "confirm_plan",
                "确认最终逐页计划",
            ),
        }
        next_step = action_by_phase.get(snapshot.phase)
        status = {
            PlanningPhase.OUTLINE_READY: "待确认：叙事大纲",
            PlanningPhase.DESIGN_READY: "待确认：视觉与沟通设计",
            PlanningPhase.SLIDES_READY: "待确认：逐页内容与图片位置",
            PlanningPhase.PLAN_CONFIRMED: "规划已确认，可生成 PPT Master 报告",
        }.get(snapshot.phase, f"当前阶段：{snapshot.phase.value}")
        return PptMasterWorkflowView(
            phase=snapshot.phase,
            status_text=status,
            preview_markdown=_preview(snapshot),
            next_action=next_step[0] if next_step else None,
            next_action_label=next_step[1] if next_step else "",
            ready_for_authoring=snapshot.ready_for_authoring,
        )

    def structured_report(self) -> dict[str, object]:
        if not self.ready_for_authoring or self._snapshot is None:
            raise PptMasterWorkflowError(
                "plan_not_confirmed",
                "PPT Master 逐页计划尚未确认。",
            )
        slides = self._snapshot.slides
        if slides is None:
            raise PptMasterWorkflowError(
                "slide_plan_missing",
                "已确认规划缺少逐页计划。",
            )
        return {
            "title": slides.deck_title,
            "slides": [
                {
                    "description": slide.message,
                    "slide_title": slide.title,
                    "bullet_points": list(slide.content_points),
                    "speaker_notes": slide.speaker_notes_intent,
                    "image_anchor": None,
                }
                for slide in slides.slides
            ],
        }


def fingerprint_ppt_master_inputs(
    config: dict[str, Any],
    *,
    bridge_claim_info: dict[str, Any] | None = None,
) -> str:
    """Bind all user-controlled planning inputs without reading their files."""

    diagnosis = config.get("_diagnosis_record")
    diagnosis_identity: dict[str, object] = {}
    if isinstance(diagnosis, dict):
        diagnosis_identity = {
            "record_id": diagnosis.get("record_id"),
            "timestamp": diagnosis.get("timestamp"),
            "schema_version": diagnosis.get("schema_version"),
            "chart_manifest": diagnosis.get("chart_manifest"),
        }
    payload = {
        "schema_version": 1,
        "report_type": config.get("report_type"),
        "req_file": config.get("req_file"),
        "template_file": config.get("template_file"),
        "project_files": list(config.get("project_files") or []),
        "diagnosis": diagnosis_identity,
        "report_provider": config.get("report_provider") or {},
        "bridge": bridge_claim_info or {},
    }
    canonical = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def probe_expected_ppt_master_installation() -> bool:
    """Cheap UI catalog probe; the Controlled Runner does full verification."""

    paths = get_default_ppt_master_store_paths()
    install = (
        paths.installed_dir
        / PPT_MASTER_2_7_0_CONTRACT.version
        / PPT_MASTER_2_7_0_CONTRACT.archive_sha256
    )
    attestation = install / ".source-bundle.json"
    try:
        return (
            install.is_dir()
            and attestation.is_file()
            and 0 < attestation.stat().st_size <= 4 * 1024 * 1024
            and len(PPT_MASTER_2_7_0_TOOLCHAIN_TREE_SHA256) == 64
        )
    except OSError:
        return False


def _preview(snapshot: PlanningSnapshot) -> str:
    lines = [
        f"# {snapshot.request.report_title}",
        "",
        f"> PPT Master 规划阶段：`{snapshot.phase.value}`；当前内容仅为方案预览。",
        "",
    ]
    if snapshot.outline is not None:
        lines.extend([
            "## 叙事大纲",
            "",
            f"**主线：** {snapshot.outline.narrative_arc}",
            "",
        ])
        for section in snapshot.outline.sections:
            lines.append(
                f"### {section.title}（{section.allocated_slides} 页）"
            )
            lines.append(f"- 目的：{section.purpose}")
            lines.extend(f"- {message}" for message in section.key_messages)
            if section.candidate_asset_ids:
                lines.append(
                    "- 候选图片：" + "、".join(section.candidate_asset_ids)
                )
            lines.append("")
    if snapshot.design is not None:
        design = snapshot.design
        lines.extend([
            "## 视觉与沟通设计",
            "",
            f"- 风格：{design.visual_style}",
            f"- 语气：{design.communication.tone}",
            f"- 密度：{design.density}",
            f"- 图表策略：{design.chart_style}",
            "- 配色："
            f"背景 {design.palette.background} / 主色 {design.palette.primary} / "
            f"强调 {design.palette.accent} / 正文 {design.palette.text}",
            "- 字体："
            f"标题 {design.typography.title_font} {design.typography.title_size_px}px；"
            f"正文 {design.typography.body_font} {design.typography.body_size_px}px",
            "",
        ])
    if snapshot.slides is not None:
        lines.extend(["## 逐页计划", ""])
        for slide in snapshot.slides.slides:
            assets = (
                "；图片：" + "、".join(slide.asset_ids)
                if slide.asset_ids else ""
            )
            lines.extend([
                f"### 第 {slide.sequence} 页｜{slide.title}",
                f"- 核心信息：{slide.message}",
                f"- 版式：{slide.layout.value}{assets}",
                *[f"- {point}" for point in slide.content_points],
                "",
            ])
    return "\n".join(lines).strip()


def _is_digest(value: str) -> bool:
    return len(value) == 64 and all(char in "0123456789abcdef" for char in value)


# ── TEMPORARY BATCH 3.6.6 ACCEPTANCE CAPTURE HOOKS ──────────────────
# These are thin try/except wrappers so capture failures never affect
# the production workflow state machine.  DELETE after Scenario A/B stable.


def _capture_design_confirmed(snapshot: PlanningSnapshot) -> None:
    """Capture DESIGN_CONFIRMED snapshot before GenerateSlides is called.

    The snapshot at this point has outline + design confirmed by the user
    but no slide plan yet.  This is the most valuable checkpoint because
    it preserves the real human-confirmed design and can be restored for
    slide-intent-only retests.
    """
    try:
        from .acceptance_capture import capture_at_phase
        capture_at_phase(snapshot, PlanningPhase.DESIGN_CONFIRMED)
    except Exception:
        import sys
        print(
            "ACCEPTANCE CHECKPOINT WRITE FAILED: DESIGN_CONFIRMED capture error",
            file=sys.stderr,
            flush=True,
        )


def _capture_slides_ready(snapshot: PlanningSnapshot) -> None:
    """Capture SLIDES_READY snapshot after GenerateSlides succeeds."""
    try:
        from .acceptance_capture import capture_at_phase
        capture_at_phase(snapshot, PlanningPhase.SLIDES_READY)
    except Exception:
        import sys
        print(
            "ACCEPTANCE CHECKPOINT WRITE FAILED: SLIDES_READY capture error",
            file=sys.stderr,
            flush=True,
        )


def _capture_plan_confirmed(snapshot: PlanningSnapshot) -> None:
    """Capture PLAN_CONFIRMED snapshot after ConfirmPlan succeeds."""
    try:
        from .acceptance_capture import capture_at_phase
        capture_at_phase(snapshot, PlanningPhase.PLAN_CONFIRMED)
    except Exception:
        import sys
        print(
            "ACCEPTANCE CHECKPOINT WRITE FAILED: PLAN_CONFIRMED capture error",
            file=sys.stderr,
            flush=True,
        )
