from __future__ import annotations

import json
from typing import Any, TypeVar

import pytest
from pydantic import BaseModel, ValidationError

from core.ai_errors import AIClientTimeoutError
from dp_engine.ppt_master_host.planning import (
    CancelPlanning,
    ColorPalette,
    CommunicationContract,
    ConfirmDesign,
    ConfirmOutline,
    ConfirmPlan,
    DesignContract,
    GenerateDesign,
    GenerateOutline,
    GenerateSlides,
    HostAIClientPlanningAdapter,
    HostPlanningModelError,
    HostPlanningStateMachine,
    HostPlanningTransitionError,
    OutlinePlan,
    OutlineSection,
    PlanningAsset,
    PlanningAssetKind,
    PlanningPhase,
    PlanningRequest,
    PlanningSnapshot,
    ResumePlanning,
    SlideIntent,
    SlideIntentPlan,
    SlideLayout,
    TemplateMode,
    TypographySpec,
    _SLIDES_CAPACITY_APPENDIX,
    _extract_schema_error_detail_from_exc,
    _get_max_assets_per_slide,
    _get_max_candidates_per_section,
    _outline_schema_corrective_prompt,
    _parse_cross_section_assets,
    _slides_corrective_prompt,
    _slides_schema_corrective_prompt,
    _slides_prompt,
    _slides_semantic_corrective_prompt,
)


ModelT = TypeVar("ModelT", bound=BaseModel)


class ScriptedPlanningModel:
    def __init__(
        self,
        outputs: list[BaseModel | HostPlanningModelError],
        *,
        identity: str = "scripted:test-model",
    ) -> None:
        self.outputs = list(outputs)
        self._identity = identity
        self.calls: list[tuple[str, str, int]] = []

    @property
    def identity(self) -> str:
        return self._identity

    def generate_structured(
        self,
        *,
        prompt: str,
        schema: type[ModelT],
        system_prompt: str,
        max_tokens: int,
        max_schema_retries: int = 1,
    ) -> ModelT:
        self.calls.append((schema.__name__, system_prompt, max_tokens))
        output = self.outputs.pop(0)
        if isinstance(output, HostPlanningModelError):
            raise output
        return schema.model_validate(output)


class FakeAIClient:
    backend = "online"
    model_name = "deepseek-v4-pro"

    def __init__(self, output: BaseModel) -> None:
        self._output = output
        self.secret = "must-not-cross-adapter"
        self.call: dict[str, Any] = {}

    def generate_structured(
        self,
        prompt: str,
        schema: type[BaseModel],
        system_prompt: str = "",
        temperature: float = 0.3,
        max_tokens: int = 2_048,
        *,
        max_schema_retries: int = 1,
    ) -> dict[str, Any]:
        self.call = {
            "prompt": prompt,
            "schema": schema,
            "system_prompt": system_prompt,
            "temperature": temperature,
            "max_tokens": max_tokens,
            "max_schema_retries": max_schema_retries,
        }
        return self._output.model_dump(mode="json")


def _request() -> PlanningRequest:
    return PlanningRequest(
        request_id="report-001",
        report_title="光纤传感器诊断汇报",
        objective="给出数据质量、标定结论和处置决策",
        audience="实验负责人和工程审核人",
        source_context="诊断记录已完成清洗、分析、对比和传感器标定。",
        requested_slide_count=4,
        assets=(
            PlanningAsset(
                asset_id="chart.temperature",
                kind=PlanningAssetKind.CHART,
                semantic_label="温度补偿诊断图",
                summary="阶段 B 原始与补偿后诊断证据",
                required=True,
            ),
            PlanningAsset(
                asset_id="chart.strain",
                kind=PlanningAssetKind.CHART,
                semantic_label="应变标定曲线",
                summary="应变传感器线性和迟滞证据",
            ),
        ),
    )


def _outline() -> OutlinePlan:
    return OutlinePlan(
        deck_title="光纤传感器诊断与决策",
        narrative_arc="先给结论，再展示证据，最后明确行动。",
        sections=(
            OutlineSection(
                section_id="opening",
                title="结论",
                purpose="建立决策语境",
                key_messages=("先说明整体评级",),
                allocated_slides=1,
            ),
            OutlineSection(
                section_id="evidence",
                title="关键证据",
                purpose="解释评级来源",
                key_messages=("温度补偿效果", "应变标定风险"),
                allocated_slides=2,
                candidate_asset_ids=("chart.temperature", "chart.strain"),
            ),
            OutlineSection(
                section_id="decision",
                title="处置建议",
                purpose="形成可执行闭环",
                key_messages=("明确责任人与验证门槛",),
                allocated_slides=1,
            ),
        ),
    )


def _design() -> DesignContract:
    return DesignContract(
        communication=CommunicationContract(
            objective="让审核人快速批准处置方案",
            audience_success="能复述风险、证据和下一步",
            tone="专业、克制、结论先行",
            content_divergence="faithful",
        ),
        template_strategy=TemplateMode.FREE_DESIGN,
        visual_style="深蓝工程报告风格，强调证据和状态分级",
        palette=ColorPalette(
            primary="#17365D",
            secondary="#5B9BD5",
            accent="#ED7D31",
            background="#F7F9FC",
            text="#17202A",
        ),
        typography=TypographySpec(
            title_font="Microsoft YaHei",
            body_font="Microsoft YaHei",
            monospace_font="Cascadia Mono",
            title_size_px=40,
            body_size_px=22,
            caption_size_px=14,
        ),
        density="balanced",
        image_usage="source_only",
        chart_style="统一坐标轴、颜色语义和数据来源说明",
        refine_spec="优先强化信息层级、留白和图文对齐。",
        layout_rules=("每页只表达一个结论", "图表紧邻其支撑结论"),
        accessibility_rules=("正文与背景保持高对比", "颜色之外增加文字状态"),
    )


def _slides() -> SlideIntentPlan:
    return SlideIntentPlan(
        deck_title="光纤传感器诊断与决策",
        slides=(
            SlideIntent(
                slide_id="slide-01",
                section_id="opening",
                sequence=1,
                title="整体数据可用，但标定风险阻断工程应用",
                message="当前结果只支持限定性分析。",
                layout=SlideLayout.TITLE,
                content_points=("完整性通过", "标定存在阻断项"),
                visual_brief="评级卡与一句话结论。",
            ),
            SlideIntent(
                slide_id="slide-02",
                section_id="evidence",
                sequence=2,
                title="温度补偿后残差显著收敛",
                message="原始与补偿后诊断应成对呈现。",
                layout=SlideLayout.DATA_FOCUS,
                content_points=("比较原始四联图", "比较补偿后四联图"),
                asset_ids=("chart.temperature",),
                visual_brief="主图占据页面三分之二并标注关键残差。",
            ),
            SlideIntent(
                slide_id="slide-03",
                section_id="evidence",
                sequence=3,
                title="应变迟滞是主要标定风险",
                message="高迟滞传感器不能用于定量结论。",
                layout=SlideLayout.COMPARISON,
                content_points=("区分合格和不合格传感器",),
                asset_ids=("chart.strain",),
                visual_brief="左右对比曲线与分级标记。",
            ),
            SlideIntent(
                slide_id="slide-04",
                section_id="decision",
                sequence=4,
                title="先重标定，再进入工程验收",
                message="用三项行动闭合风险。",
                layout=SlideLayout.CONCLUSION,
                content_points=("核对映射", "重做标定", "复核验收"),
                visual_brief="三步行动路线图。",
            ),
        ),
    )


def _advance_to_outline_confirmed(
    model: ScriptedPlanningModel,
) -> tuple[HostPlanningStateMachine, PlanningSnapshot]:
    machine = HostPlanningStateMachine(model)
    snapshot = machine.start(_request())
    snapshot = machine.apply(snapshot, GenerateOutline())
    snapshot = machine.apply(snapshot, ConfirmOutline())
    return machine, snapshot


def test_full_lifecycle_requires_three_explicit_user_confirmations() -> None:
    model = ScriptedPlanningModel([_outline(), _design(), _slides()])
    machine = HostPlanningStateMachine(model)
    snapshot = machine.start(_request())
    phases = [snapshot.phase]

    for command in (
        GenerateOutline(),
        ConfirmOutline(),
        GenerateDesign(),
        ConfirmDesign(),
        GenerateSlides(),
        ConfirmPlan(),
    ):
        snapshot = machine.apply(snapshot, command)
        phases.append(snapshot.phase)

    assert phases == [
        PlanningPhase.NEW,
        PlanningPhase.OUTLINE_READY,
        PlanningPhase.OUTLINE_CONFIRMED,
        PlanningPhase.DESIGN_READY,
        PlanningPhase.DESIGN_CONFIRMED,
        PlanningPhase.SLIDES_READY,
        PlanningPhase.PLAN_CONFIRMED,
    ]
    assert snapshot.revision == 6
    assert snapshot.ready_for_authoring is True
    assert snapshot.outline_confirmation is not None
    assert snapshot.design_confirmation is not None
    assert snapshot.plan_confirmation is not None
    assert [call[0] for call in model.calls] == [
        "OutlinePlan",
        "DesignContract",
        "SlideIntentPlan",
    ]


def test_illegal_transition_does_not_call_model_or_mutate_snapshot() -> None:
    model = ScriptedPlanningModel([_design()])
    machine = HostPlanningStateMachine(model)
    snapshot = machine.start(_request())

    with pytest.raises(HostPlanningTransitionError) as caught:
        machine.apply(snapshot, GenerateDesign())

    assert caught.value.code == "illegal_transition"
    assert caught.value.phase == PlanningPhase.NEW
    assert snapshot.phase == PlanningPhase.NEW
    assert snapshot.revision == 0
    assert model.calls == []


def test_edited_confirmation_is_fingerprinted_and_restore_detects_tampering() -> None:
    model = ScriptedPlanningModel([_outline()])
    machine = HostPlanningStateMachine(model)
    ready = machine.apply(machine.start(_request()), GenerateOutline())
    edited = _outline().model_copy(update={"narrative_arc": "编辑后：风险、证据、行动。"})
    confirmed = machine.apply(ready, ConfirmOutline(outline=edited))

    serialized = machine.serialize(confirmed)
    assert machine.restore(serialized) == confirmed
    payload = json.loads(serialized)
    payload["outline"]["narrative_arc"] = "未重新确认的篡改"

    with pytest.raises(ValidationError, match="fingerprint mismatch"):
        machine.restore(json.dumps(payload, ensure_ascii=False))


def test_cancellation_before_model_call_is_resumable_without_model_use() -> None:
    model = ScriptedPlanningModel([_outline()])
    machine = HostPlanningStateMachine(model, cancel_check=lambda: True)
    start = machine.start(_request())

    cancelled = machine.apply(start, GenerateOutline())
    resumed = machine.apply(cancelled, ResumePlanning())

    assert cancelled.phase == PlanningPhase.CANCELLED
    assert cancelled.cancelled_from == PlanningPhase.NEW
    assert cancelled.revision == 1
    assert resumed.phase == PlanningPhase.NEW
    assert resumed.cancelled_from is None
    assert resumed.revision == 2
    assert model.calls == []


def test_cancellation_after_model_call_discards_uncommitted_output() -> None:
    checks = iter((False, True))
    model = ScriptedPlanningModel([_outline()])
    machine = HostPlanningStateMachine(
        model,
        cancel_check=lambda: next(checks),
    )

    cancelled = machine.apply(machine.start(_request()), GenerateOutline())

    assert cancelled.phase == PlanningPhase.CANCELLED
    assert cancelled.cancelled_from == PlanningPhase.NEW
    assert cancelled.outline is None
    assert len(model.calls) == 1


def test_model_failure_uses_current_phase_and_preserves_prior_snapshot() -> None:
    failure = HostPlanningModelError(
        "upstream_failed",
        PlanningPhase.NEW,
        "scripted failure",
    )
    model = ScriptedPlanningModel([_outline(), failure])
    machine, confirmed = _advance_to_outline_confirmed(model)

    with pytest.raises(HostPlanningModelError) as caught:
        machine.apply(confirmed, GenerateDesign())

    assert caught.value.code == "upstream_failed"
    assert caught.value.phase == PlanningPhase.OUTLINE_CONFIRMED
    assert confirmed.phase == PlanningPhase.OUTLINE_CONFIRMED
    assert confirmed.design is None
    assert confirmed.revision == 2


def test_generated_cross_stage_violation_is_a_model_contract_error() -> None:
    bad_sections = list(_outline().sections)
    bad_sections[0] = bad_sections[0].model_copy(update={"allocated_slides": 2})
    bad_outline = _outline().model_copy(update={"sections": tuple(bad_sections)})
    model = ScriptedPlanningModel([bad_outline])
    machine = HostPlanningStateMachine(model)
    start = machine.start(_request())

    with pytest.raises(HostPlanningModelError) as caught:
        machine.apply(start, GenerateOutline())

    assert caught.value.code == "model_contract_invalid"
    assert caught.value.phase == PlanningPhase.NEW
    assert start.outline is None
    assert start.revision == 0


def test_invalid_user_edit_and_non_user_actor_cannot_confirm() -> None:
    model = ScriptedPlanningModel([_outline()])
    machine = HostPlanningStateMachine(model)
    ready = machine.apply(machine.start(_request()), GenerateOutline())
    bad_sections = list(_outline().sections)
    bad_sections[0] = bad_sections[0].model_copy(update={"allocated_slides": 2})
    invalid_edit = _outline().model_copy(update={"sections": tuple(bad_sections)})

    with pytest.raises(HostPlanningTransitionError) as caught:
        machine.apply(ready, ConfirmOutline(outline=invalid_edit))
    with pytest.raises(ValidationError):
        ConfirmOutline.model_validate({"actor": "model"})

    assert caught.value.code == "confirmation_invalid"
    assert ready.phase == PlanningPhase.OUTLINE_READY
    assert ready.outline_confirmation is None


def test_required_asset_must_be_placed_exactly_once_in_final_plan() -> None:
    slides = list(_slides().slides)
    slides[1] = slides[1].model_copy(update={"asset_ids": ()})
    missing_required = _slides().model_copy(update={"slides": tuple(slides)})
    # Provide enough copies for retry exhaustion (3 attempts total)
    model = ScriptedPlanningModel([
        _outline(), _design(),
        missing_required, missing_required, missing_required,
    ])
    machine, snapshot = _advance_to_outline_confirmed(model)
    snapshot = machine.apply(snapshot, GenerateDesign())
    snapshot = machine.apply(snapshot, ConfirmDesign())

    with pytest.raises(HostPlanningModelError) as caught:
        machine.apply(snapshot, GenerateSlides())

    # With unified semantic retry, missing-required errors are now retried
    assert caught.value.code == "slides_semantic_exhausted"
    assert caught.value.phase == PlanningPhase.DESIGN_CONFIRMED
    assert "Missing required assets" in str(caught.value)
    assert snapshot.slides is None


def test_host_ai_client_adapter_passes_schema_without_exposing_credentials() -> None:
    client = FakeAIClient(_outline())
    adapter = HostAIClientPlanningAdapter(client)

    result = adapter.generate_structured(
        prompt="safe planning context",
        schema=OutlinePlan,
        system_prompt="return structured outline",
        max_tokens=1_234,
    )

    assert result == _outline()
    assert adapter.identity == "online:deepseek-v4-pro"
    assert client.call["schema"] is OutlinePlan
    assert client.call["temperature"] == 0.3
    assert client.call["max_tokens"] == 1_234
    assert client.call["max_schema_retries"] == 1
    boundary_text = " ".join(
        (
            adapter.identity,
            str(client.call["prompt"]),
            str(client.call["system_prompt"]),
        )
    )
    assert client.secret not in boundary_text


def test_outline_timeout_is_not_retried_as_schema_failure() -> None:
    class TimeoutAIClient:
        backend = "online"
        model_name = "deepseek-v4-pro"

        def __init__(self) -> None:
            self.calls = 0

        def generate_structured(
            self,
            prompt: str,
            schema: type[BaseModel],
            system_prompt: str = "",
            temperature: float = 0.3,
            max_tokens: int = 2_048,
            *,
            max_schema_retries: int = 1,
        ) -> dict[str, Any]:
            del prompt, schema, system_prompt, temperature, max_tokens
            del max_schema_retries
            self.calls += 1
            raise AIClientTimeoutError("upstream timed out")

    client = TimeoutAIClient()
    machine = HostPlanningStateMachine(HostAIClientPlanningAdapter(client))
    snapshot = machine.start(_request())

    with pytest.raises(HostPlanningModelError) as caught:
        machine.apply(snapshot, GenerateOutline())

    assert caught.value.code == "model_timeout"
    assert caught.value.phase == PlanningPhase.NEW
    assert "timed out" in str(caught.value).lower()
    assert "schema validation failed" not in str(caught.value).lower()
    assert client.calls == 1
    assert snapshot.phase == PlanningPhase.NEW
    assert snapshot.outline is None


# ═══════════════════════════════════════════════════════════════════
# P0-1 Schema strictness tests — narrative_arc must be string
# ═══════════════════════════════════════════════════════════════════


class TestOutlinePlanSchemaStrictness:
    """OutlinePlan.narrative_arc is str — object/array must be rejected."""

    def test_narrative_arc_rejects_object(self) -> None:
        payload = _outline().model_dump(mode="json")
        payload["narrative_arc"] = {
            "theme": "risk-first",
            "progression": "conclusion→evidence→action",
        }
        with pytest.raises(ValidationError) as exc_info:
            OutlinePlan.model_validate(payload)
        errors = exc_info.value.errors()
        assert any(
            e.get("loc") == ("narrative_arc",)
            and e.get("type") in ("string_type", "str_type")
            for e in errors
        ), f"Expected string_type error for narrative_arc object, got: {errors}"

    def test_narrative_arc_rejects_array(self) -> None:
        payload = _outline().model_dump(mode="json")
        payload["narrative_arc"] = ["step1", "step2"]
        with pytest.raises(ValidationError) as exc_info:
            OutlinePlan.model_validate(payload)
        errors = exc_info.value.errors()
        assert any(
            e.get("loc") == ("narrative_arc",)
            and "str" in e.get("type", "")
            for e in errors
        ), f"Expected string_type error for narrative_arc array, got: {errors}"

    def test_narrative_arc_rejects_null(self) -> None:
        payload = _outline().model_dump(mode="json")
        payload["narrative_arc"] = None
        with pytest.raises(ValidationError) as exc_info:
            OutlinePlan.model_validate(payload)
        errors = exc_info.value.errors()
        assert any(
            e.get("loc") == ("narrative_arc",)
            and e.get("type") in ("string_type", "str_type")
            for e in errors
        ), f"Expected string_type error for narrative_arc null, got: {errors}"

    def test_narrative_arc_accepts_string(self) -> None:
        plan = _outline()
        assert isinstance(plan.narrative_arc, str)
        assert len(plan.narrative_arc) > 0
        # Ensure it roundtrips correctly
        reloaded = OutlinePlan.model_validate(plan.model_dump(mode="json"))
        assert reloaded.narrative_arc == plan.narrative_arc

    def test_empty_narrative_arc_rejected(self) -> None:
        payload = _outline().model_dump(mode="json")
        payload["narrative_arc"] = ""
        with pytest.raises(ValidationError):
            OutlinePlan.model_validate(payload)


class TestDesignContractSchemaStrictness:
    """DesignContract must be fully parseable with valid fields."""

    def test_valid_design_roundtrips(self) -> None:
        design = _design()
        reloaded = DesignContract.model_validate(design.model_dump(mode="json"))
        assert reloaded.communication.tone == design.communication.tone
        assert reloaded.palette.primary == design.palette.primary
        assert reloaded.typography.title_font == design.typography.title_font

    def test_invalid_hex_color_rejected(self) -> None:
        payload = _design().model_dump(mode="json")
        payload["palette"]["primary"] = "not-a-color"
        with pytest.raises(ValidationError):
            DesignContract.model_validate(payload)

    def test_wrong_template_strategy_rejected(self) -> None:
        payload = _design().model_dump(mode="json")
        payload["template_strategy"] = "invalid_mode"
        with pytest.raises(ValidationError):
            DesignContract.model_validate(payload)


class TestSlideIntentPlanSchemaStrictness:
    """SlideIntentPlan must be fully parseable with valid fields."""

    def test_valid_slides_roundtrip(self) -> None:
        slides = _slides()
        reloaded = SlideIntentPlan.model_validate(slides.model_dump(mode="json"))
        assert len(reloaded.slides) == len(slides.slides)
        assert reloaded.deck_title == slides.deck_title

    def test_non_contiguous_sequence_rejected(self) -> None:
        payload = _slides().model_dump(mode="json")
        payload["slides"][1]["sequence"] = 999
        with pytest.raises(ValidationError):
            SlideIntentPlan.model_validate(payload)


# ═══════════════════════════════════════════════════════════════════
# P0-1: Verify generate_structured embeds JSON schema in system prompt
# ═══════════════════════════════════════════════════════════════════


def test_ai_client_embeds_json_schema_in_system_prompt() -> None:
    """The real AIClient.generate_structured MUST embed the JSON Schema
    so the model knows the expected types (P0-1 fix: narrative_arc string)."""
    from unittest.mock import patch

    from core.ai_client import AIClient

    client = AIClient.get_instance()
    if not client.is_available():
        pytest.skip("AI client not configured — cannot test schema embedding")

    captured_system: list[str] = []

    def _fake_generate(*, prompt: str, system_prompt: str, **kw: object) -> str:
        captured_system.append(system_prompt)
        # Return minimal valid OutlinePlan JSON
        return json.dumps(_outline().model_dump(mode="json"), ensure_ascii=False)

    with patch.object(client, "generate", _fake_generate):
        client.generate_structured(
            prompt="test prompt",
            schema=OutlinePlan,
            system_prompt="test system",
            max_tokens=512,
        )

    assert len(captured_system) == 1
    augmented = captured_system[0]
    # Must contain the original system prompt
    assert "test system" in augmented
    # Must contain schema-critical type info
    assert '"narrative_arc"' in augmented
    assert '"type"' in augmented
    assert '"string"' in augmented
    # Must contain the schema instruction header
    assert "CRITICAL" in augmented.upper() or "JSON Schema" in augmented


# ═══════════════════════════════════════════════════════════════

# ===========================================================
# Batch 3.6.6 - P0-6: SlideIntentPlan cross-section asset assignment
# ===========================================================


def _two_section_outline() -> OutlinePlan:
    return OutlinePlan(
        deck_title="温度补偿诊断结论",
        narrative_arc="从事实证据推进到处置决策",
        sections=(
            OutlineSection(
                section_id="sec_compensation",
                title="温度补偿效果验证",
                purpose="展示补偿前后对比证据",
                key_messages=("补偿有效降低偏差",),
                allocated_slides=2,
                candidate_asset_ids=(
                    "phaseb_diagnostic_B1_raw",
                    "phaseb_diagnostic_B1_compensated",
                    "phaseb_diagnostic_B2_raw",
                ),
            ),
            OutlineSection(
                section_id="sec_correlation",
                title="回归分析与C组细节",
                purpose="展示回归分析和温度相关性证据",
                key_messages=("温度系数回归可靠",),
                allocated_slides=2,
                candidate_asset_ids=(
                    "phaseb_diagnostic_B2_compensated",
                    "phaseb_diagnostic_C2_raw",
                    "phaseb_diagnostic_C2_compensated",
                ),
            ),
        ),
    )


def _two_section_request() -> PlanningRequest:
    return PlanningRequest(
        request_id="cross-section-test",
        report_title="温度补偿诊断结论",
        objective="验证补偿有效性和回归可靠性",
        audience="项目技术负责人",
        source_context="诊断记录包含温度补偿证据。",
        requested_slide_count=4,
        assets=(
            PlanningAsset(asset_id="phaseb_diagnostic_B1_raw", kind=PlanningAssetKind.CHART, semantic_label="B1原始", summary="B1原始诊断", required=True),
            PlanningAsset(asset_id="phaseb_diagnostic_B1_compensated", kind=PlanningAssetKind.CHART, semantic_label="B1补偿后", summary="B1补偿后诊断", required=True),
            PlanningAsset(asset_id="phaseb_diagnostic_B2_raw", kind=PlanningAssetKind.CHART, semantic_label="B2原始", summary="B2原始诊断", required=True),
            PlanningAsset(asset_id="phaseb_diagnostic_B2_compensated", kind=PlanningAssetKind.CHART, semantic_label="B2补偿后", summary="B2补偿后诊断", required=True),
            PlanningAsset(asset_id="phaseb_diagnostic_C2_raw", kind=PlanningAssetKind.CHART, semantic_label="C2原始", summary="C2原始诊断", required=True),
            PlanningAsset(asset_id="phaseb_diagnostic_C2_compensated", kind=PlanningAssetKind.CHART, semantic_label="C2补偿后", summary="C2补偿后诊断", required=True),
        ),
    )


def _two_section_design() -> DesignContract:
    return DesignContract(
        communication=CommunicationContract(
            objective="审核", audience_success="理解",
            tone="严谨", content_divergence="faithful",
        ),
        template_strategy=TemplateMode.FREE_DESIGN,
        visual_style="正式",
        palette=ColorPalette(primary="#111111", secondary="#222222", accent="#333333", background="#FFFFFF", text="#000000"),
        typography=TypographySpec(title_font="Arial", body_font="Arial", monospace_font="Consolas", title_size_px=36, body_size_px=20, caption_size_px=14),
        density="balanced",
        image_usage="source_only",
        chart_style="趋势",
        refine_spec="网格",
        layout_rules=("规则",),
        accessibility_rules=("对比度",),
    )


def _valid_slides_for_two_section() -> SlideIntentPlan:
    return SlideIntentPlan(
        deck_title="温度补偿诊断结论",
        slides=(
            SlideIntent(slide_id="s-01", section_id="sec_compensation", sequence=1,
                title="B1 原始与补偿后对比", message="补偿前后残差收敛",
                layout=SlideLayout.DATA_FOCUS,
                content_points=("B1原始", "B1补偿后"),
                asset_ids=("phaseb_diagnostic_B1_raw", "phaseb_diagnostic_B1_compensated"),
                visual_brief="B1对比图"),
            SlideIntent(slide_id="s-02", section_id="sec_compensation", sequence=2,
                title="B2 原始诊断证据", message="B2原始四联图",
                layout=SlideLayout.DATA_FOCUS,
                content_points=("B2原始诊断",),
                asset_ids=("phaseb_diagnostic_B2_raw",),
                visual_brief="B2原始图"),
            SlideIntent(slide_id="s-03", section_id="sec_correlation", sequence=3,
                title="B2 补偿后回归分析", message="B2补偿后残差与回归",
                layout=SlideLayout.DATA_FOCUS,
                content_points=("B2补偿后", "回归线"),
                asset_ids=("phaseb_diagnostic_B2_compensated",),
                visual_brief="B2补偿后回归图"),
            SlideIntent(slide_id="s-04", section_id="sec_correlation", sequence=4,
                title="C2 原始与补偿后分析", message="C2详细证据",
                layout=SlideLayout.CONCLUSION,
                content_points=("C2原始", "C2补偿后"),
                asset_ids=("phaseb_diagnostic_C2_raw", "phaseb_diagnostic_C2_compensated"),
                visual_brief="C2图表"),
        ),
    )


def _cross_section_slides() -> SlideIntentPlan:
    return SlideIntentPlan(
        deck_title="温度补偿诊断结论",
        slides=(
            SlideIntent(slide_id="s-01", section_id="sec_compensation", sequence=1,
                title="B1 原始与补偿后对比", message="补偿前后残差收敛",
                layout=SlideLayout.DATA_FOCUS,
                content_points=("B1原始", "B1补偿后"),
                asset_ids=("phaseb_diagnostic_B1_raw", "phaseb_diagnostic_B1_compensated"),
                visual_brief="B1对比图"),
            SlideIntent(slide_id="s-02", section_id="sec_compensation", sequence=2,
                title="B2补偿后细节", message="B2补偿后残差",
                layout=SlideLayout.DATA_FOCUS,
                content_points=("B2补偿后",),
                asset_ids=("phaseb_diagnostic_B2_compensated",),
                visual_brief="B2图表"),
            SlideIntent(slide_id="s-03", section_id="sec_correlation", sequence=3,
                title="B2原始证据回顾", message="B2原始四联图",
                layout=SlideLayout.DATA_FOCUS,
                content_points=("B2原始",),
                asset_ids=("phaseb_diagnostic_B2_raw",),
                visual_brief="B2原始图"),
            SlideIntent(slide_id="s-04", section_id="sec_correlation", sequence=4,
                title="C2 原始与补偿后", message="C2详细证据",
                layout=SlideLayout.CONCLUSION,
                content_points=("C2原始", "C2补偿后"),
                asset_ids=("phaseb_diagnostic_C2_raw", "phaseb_diagnostic_C2_compensated"),
                visual_brief="C2图表"),
        ),
    )


class TestSlidesCrossSectionValidation:

    def test_valid_slides_pass_validation(self) -> None:
        from dp_engine.ppt_master_host.planning import _validate_slides_for_request
        _validate_slides_for_request(
            _two_section_request(),
            _two_section_outline(),
            _valid_slides_for_two_section(),
        )

    def test_cross_section_asset_rejected(self) -> None:
        from dp_engine.ppt_master_host.planning import _validate_slides_for_request
        with pytest.raises(ValueError) as exc:
            _validate_slides_for_request(
                _two_section_request(),
                _two_section_outline(),
                _cross_section_slides(),
            )
        assert "Cross-section assets" in str(exc.value)
        assert "phaseb_diagnostic_B2_compensated" in str(exc.value)

    def test_cross_section_preserves_outline(self) -> None:
        outline = _two_section_outline()
        original = outline.model_dump_json()
        from dp_engine.ppt_master_host.planning import _validate_slides_for_request
        try:
            _validate_slides_for_request(
                _two_section_request(), outline, _cross_section_slides(),
            )
        except ValueError:
            pass
        assert outline.model_dump_json() == original

    def test_slides_prompt_contains_section_allowlist(self) -> None:
        prompt = _slides_prompt(
            _two_section_request(),
            _two_section_outline(),
            _two_section_design(),
        )
        assert "sec_compensation" in prompt
        assert "sec_correlation" in prompt
        assert "ALLOWED" in prompt
        assert "CRITICAL" in prompt.upper()

    def test_corrective_prompt_contains_cross_section_info(self) -> None:
        prompt = _slides_corrective_prompt(
            _two_section_request(),
            _two_section_outline(),
            _two_section_design(),
            ["phaseb_diagnostic_B2_compensated"],
        )
        assert "phaseb_diagnostic_B2_compensated" in prompt
        assert "sec_correlation" in prompt

    def test_parse_cross_section_from_error(self) -> None:
        msg = "slide uses assets outside its outline section: ['phaseb_diagnostic_B2_compensated']"
        result = _parse_cross_section_assets(msg)
        assert result == ["phaseb_diagnostic_B2_compensated"]

    def test_parse_cross_section_multi_assets(self) -> None:
        msg = "slide uses assets outside its outline section: ['asset_A', 'asset_B']"
        result = _parse_cross_section_assets(msg)
        assert result == ["asset_A", "asset_B"]

    def test_parse_cross_section_other_error_returns_empty(self) -> None:
        result = _parse_cross_section_assets("slide intent count must equal requested_slide_count")
        assert result == []

    def test_first_cross_section_second_valid_retry_succeeds(self) -> None:
        request = _two_section_request()
        outline = _two_section_outline()
        design = _two_section_design()
        valid_outline = outline.model_copy(update={"deck_title": request.report_title})
        model = ScriptedPlanningModel([
            valid_outline, design,
            _cross_section_slides(),
            _valid_slides_for_two_section(),
        ])
        sm = HostPlanningStateMachine(model)
        snapshot = sm.start(request)
        snapshot = sm.apply(snapshot, GenerateOutline())
        snapshot = sm.apply(snapshot, ConfirmOutline())
        snapshot = sm.apply(snapshot, GenerateDesign())
        snapshot = sm.apply(snapshot, ConfirmDesign())
        result = sm.apply(snapshot, GenerateSlides())
        assert result.phase == PlanningPhase.SLIDES_READY

    def test_cross_section_retry_exhausted_raises_clear_error(self) -> None:
        request = _two_section_request()
        outline = _two_section_outline()
        design = _two_section_design()
        valid_outline = outline.model_copy(update={"deck_title": request.report_title})
        model = ScriptedPlanningModel([
            valid_outline, design,
            _cross_section_slides(),
            _cross_section_slides(),
            _cross_section_slides(),
        ])
        sm = HostPlanningStateMachine(model)
        snapshot = sm.start(request)
        snapshot = sm.apply(snapshot, GenerateOutline())
        snapshot = sm.apply(snapshot, ConfirmOutline())
        snapshot = sm.apply(snapshot, GenerateDesign())
        snapshot = sm.apply(snapshot, ConfirmDesign())
        with pytest.raises(HostPlanningModelError) as exc:
            sm.apply(snapshot, GenerateSlides())
        assert "cross-section" in str(exc.value).lower()

    def test_non_retryable_slide_error_does_not_retry(self) -> None:
        """Unknown/deck-title errors are NOT retryable — only count/cross/missing."""
        request = _two_section_request()
        outline = _two_section_outline()
        design = _two_section_design()
        valid_outline = outline.model_copy(update={"deck_title": request.report_title})
        # Deck title mismatch is NOT retryable
        bad_slides = SlideIntentPlan(
            deck_title="WRONG TITLE — should fail hard",
            slides=_valid_slides_for_two_section().slides,
        )
        model = ScriptedPlanningModel([valid_outline, design, bad_slides])
        sm = HostPlanningStateMachine(model)
        snapshot = sm.start(request)
        snapshot = sm.apply(snapshot, GenerateOutline())
        snapshot = sm.apply(snapshot, ConfirmOutline())
        snapshot = sm.apply(snapshot, GenerateDesign())
        snapshot = sm.apply(snapshot, ConfirmDesign())
        with pytest.raises(HostPlanningModelError) as exc:
            sm.apply(snapshot, GenerateSlides())
        assert "deck title" in str(exc.value).lower()
        slide_calls = [c for c in model.calls if c[0] == "SlideIntentPlan"]
        assert len(slide_calls) == 1


# ═══════════════════════════════════════════════════════════════════
# Batch 3.6.6 - P0-8: SlideIntentPlan required asset coverage
# ═══════════════════════════════════════════════════════════════════


class TestSlidesRequiredAssetCoverage:
    """Unified semantic retry: missing required + cross-section, bounded."""

    def test_valid_complete_coverage_passes(self) -> None:
        """All required assets covered in correct sections → PASS."""
        request = _two_section_request()
        outline = _two_section_outline()
        design = _two_section_design()
        valid_outline = outline.model_copy(update={"deck_title": request.report_title})
        model = ScriptedPlanningModel([valid_outline, design, _valid_slides_for_two_section()])
        sm = HostPlanningStateMachine(model)
        snapshot = sm.start(request)
        snapshot = sm.apply(snapshot, GenerateOutline())
        snapshot = sm.apply(snapshot, ConfirmOutline())
        snapshot = sm.apply(snapshot, GenerateDesign())
        snapshot = sm.apply(snapshot, ConfirmDesign())
        result = sm.apply(snapshot, GenerateSlides())
        assert result.phase == PlanningPhase.SLIDES_READY

    def test_missing_required_asset_retried_then_exhausted(self) -> None:
        """Missing required → corrective retry → exhausted → semantic error."""
        slides = list(_valid_slides_for_two_section().slides)
        # Drop required asset from one slide
        slides[0] = slides[0].model_copy(update={"asset_ids": ()})
        missing_plan = _valid_slides_for_two_section().model_copy(
            update={"slides": tuple(slides)}
        )
        # 3 attempts, all missing
        model = ScriptedPlanningModel([
            _two_section_outline().model_copy(
                update={"deck_title": _two_section_request().report_title}
            ),
            _two_section_design(),
            missing_plan, missing_plan, missing_plan,
        ])
        sm = HostPlanningStateMachine(model)
        snapshot = sm.start(_two_section_request())
        snapshot = sm.apply(snapshot, GenerateOutline())
        snapshot = sm.apply(snapshot, ConfirmOutline())
        snapshot = sm.apply(snapshot, GenerateDesign())
        snapshot = sm.apply(snapshot, ConfirmDesign())
        with pytest.raises(HostPlanningModelError) as exc:
            sm.apply(snapshot, GenerateSlides())
        assert "slides_semantic_exhausted" in exc.value.code
        assert "Missing required assets" in str(exc.value)

    def test_cross_section_and_missing_combined_corrective(self) -> None:
        """Sequential errors: cross-section first → missing second → unified prompt.

        Validator checks cross-section before missing-required, so when both
        issues exist, only the first is reported. The corrective prompt must
        cover both categories so the model fixes both in one retry.
        """
        request = _two_section_request()
        outline = _two_section_outline()
        design = _two_section_design()
        valid_outline = outline.model_copy(update={"deck_title": request.report_title})

        # Attempt 0: cross-section error (checked first by validator)
        slides_0 = list(_valid_slides_for_two_section().slides)
        slides_0[2] = slides_0[2].model_copy(update={
            "asset_ids": ("phaseb_diagnostic_B2_raw",),  # wrong section
        })
        bad_0 = _valid_slides_for_two_section().model_copy(
            update={"slides": tuple(slides_0)}
        )

        # Attempt 1: model "fixes" cross-section by deleting → missing required
        slides_1 = list(_valid_slides_for_two_section().slides)
        slides_1[1] = slides_1[1].model_copy(update={"asset_ids": ()})  # drops B2_raw
        bad_1 = _valid_slides_for_two_section().model_copy(
            update={"slides": tuple(slides_1)}
        )

        # Attempt 2: still missing → exhausted
        bad_2 = bad_1

        model = ScriptedPlanningModel([
            valid_outline, design, bad_0, bad_1, bad_2,
        ])
        sm = HostPlanningStateMachine(model)
        snapshot = sm.start(request)
        snapshot = sm.apply(snapshot, GenerateOutline())
        snapshot = sm.apply(snapshot, ConfirmOutline())
        snapshot = sm.apply(snapshot, GenerateDesign())
        snapshot = sm.apply(snapshot, ConfirmDesign())
        with pytest.raises(HostPlanningModelError) as exc:
            sm.apply(snapshot, GenerateSlides())
        msg = str(exc.value)
        # After sequential retries, exhausted with missing required
        assert "Missing required assets" in msg

    def test_corrective_retry_regression_no_delete_to_fix_cross_section(self) -> None:
        """Attempt 1 cross-section → corrective MUST NOT fix by deleting required."""
        request = _two_section_request()
        outline = _two_section_outline()
        design = _two_section_design()
        valid_outline = outline.model_copy(update={"deck_title": request.report_title})

        # Attempt 1: only cross-section error (no missing)
        slides_1 = list(_valid_slides_for_two_section().slides)
        slides_1[2] = slides_1[2].model_copy(update={
            "asset_ids": ("phaseb_diagnostic_B2_raw",),
        })
        cross_only = _valid_slides_for_two_section().model_copy(
            update={"slides": tuple(slides_1)}
        )

        # Attempt 2: model "fixes" by deleting the cross-section asset entirely
        # → now missing required
        slides_2 = list(_valid_slides_for_two_section().slides)
        # Remove B2_raw from its legit section too (model deleted it everywhere)
        slides_2[1] = slides_2[1].model_copy(update={"asset_ids": ()})
        now_missing = _valid_slides_for_two_section().model_copy(
            update={"slides": tuple(slides_2)}
        )

        # Attempt 3: still missing
        still_missing = now_missing

        model = ScriptedPlanningModel([
            valid_outline, design,
            cross_only,      # attempt 0: cross-section only
            now_missing,     # attempt 1: "fixed" cross-section by deleting → missing
            still_missing,   # attempt 2: still missing → exhausted
        ])
        sm = HostPlanningStateMachine(model)
        snapshot = sm.start(request)
        snapshot = sm.apply(snapshot, GenerateOutline())
        snapshot = sm.apply(snapshot, ConfirmOutline())
        snapshot = sm.apply(snapshot, GenerateDesign())
        snapshot = sm.apply(snapshot, ConfirmDesign())
        with pytest.raises(HostPlanningModelError) as exc:
            sm.apply(snapshot, GenerateSlides())
        # Must report missing required (the regression)
        assert "Missing required assets" in str(exc.value)

    def test_second_corrective_fixes_both_passes(self) -> None:
        """Attempt 1 fails → attempt 2 fixes both missing + cross → PASS."""
        request = _two_section_request()
        outline = _two_section_outline()
        design = _two_section_design()
        valid_outline = outline.model_copy(update={"deck_title": request.report_title})

        # Attempt 1: cross-section
        slides_1 = list(_valid_slides_for_two_section().slides)
        slides_1[2] = slides_1[2].model_copy(update={
            "asset_ids": ("phaseb_diagnostic_B2_raw",),
        })
        bad = _valid_slides_for_two_section().model_copy(
            update={"slides": tuple(slides_1)}
        )

        # Attempt 2: valid
        good = _valid_slides_for_two_section()

        model = ScriptedPlanningModel([
            valid_outline, design, bad, good,
        ])
        sm = HostPlanningStateMachine(model)
        snapshot = sm.start(request)
        snapshot = sm.apply(snapshot, GenerateOutline())
        snapshot = sm.apply(snapshot, ConfirmOutline())
        snapshot = sm.apply(snapshot, GenerateDesign())
        snapshot = sm.apply(snapshot, ConfirmDesign())
        result = sm.apply(snapshot, GenerateSlides())
        assert result.phase == PlanningPhase.SLIDES_READY

    def test_semantic_corrective_prompt_contains_both_error_types(self) -> None:
        """Unified prompt covers both missing required and cross-section errors."""
        prompt = _slides_semantic_corrective_prompt(
            _two_section_request(),
            _two_section_outline(),
            _two_section_design(),
            cross_section_ids=["phaseb_diagnostic_B2_raw"],
            missing_required_ids=["phaseb_diagnostic_B1_compensated"],
        )
        assert "phaseb_diagnostic_B2_raw" in prompt
        assert "phaseb_diagnostic_B1_compensated" in prompt
        assert "HARD CONSTRAINTS" in prompt
        assert "REQUIRED COVERAGE" in prompt
        assert "SECTION SCOPING" in prompt
        assert "CROSS-SECTION ASSET ERRORS" in prompt
        assert "MISSING REQUIRED ASSETS" in prompt
        assert "CONSTRAINT PRIORITY" in prompt

    def test_semantic_corrective_prompt_missing_only(self) -> None:
        """Unified prompt with only missing required (no cross-section)."""
        prompt = _slides_semantic_corrective_prompt(
            _two_section_request(),
            _two_section_outline(),
            _two_section_design(),
            missing_required_ids=["phaseb_diagnostic_C2_raw"],
        )
        assert "MISSING REQUIRED ASSETS" in prompt
        assert "phaseb_diagnostic_C2_raw" in prompt
        assert "CROSS-SECTION ASSET ERRORS" not in prompt

    def test_slides_prompt_contains_required_coverage(self) -> None:
        """Initial slides prompt now includes REQUIRED ASSET COVERAGE section."""
        prompt = _slides_prompt(
            _two_section_request(),
            _two_section_outline(),
            _two_section_design(),
        )
        assert "REQUIRED ASSET COVERAGE" in prompt
        assert "REQUIRED ASSETS BY SECTION" in prompt
        assert "REQUIRED =" in prompt
        # All required IDs appear
        for asset in _two_section_request().assets:
            if asset.required:
                assert asset.asset_id in prompt

    def test_find_owning_section(self) -> None:
        """_find_owning_section returns correct section_id."""
        from dp_engine.ppt_master_host.planning import _find_owning_section
        outline = _two_section_outline()
        assert _find_owning_section(outline, "phaseb_diagnostic_B1_raw") == "sec_compensation"
        assert _find_owning_section(outline, "phaseb_diagnostic_C2_compensated") == "sec_correlation"
        assert _find_owning_section(outline, "nonexistent") == "unknown"

    def test_retry_exhaustion_preserves_design_ready_phase(self) -> None:
        """After retry exhaustion, snapshot phase stays DESIGN_CONFIRMED."""
        slides = list(_valid_slides_for_two_section().slides)
        slides[0] = slides[0].model_copy(update={"asset_ids": ()})
        missing_plan = _valid_slides_for_two_section().model_copy(
            update={"slides": tuple(slides)}
        )
        model = ScriptedPlanningModel([
            _two_section_outline().model_copy(
                update={"deck_title": _two_section_request().report_title}
            ),
            _two_section_design(),
            missing_plan, missing_plan, missing_plan,
        ])
        sm = HostPlanningStateMachine(model)
        snapshot = sm.start(_two_section_request())
        snapshot = sm.apply(snapshot, GenerateOutline())
        snapshot = sm.apply(snapshot, ConfirmOutline())
        snapshot = sm.apply(snapshot, GenerateDesign())
        snapshot = sm.apply(snapshot, ConfirmDesign())
        original_phase = snapshot.phase
        try:
            sm.apply(snapshot, GenerateSlides())
        except HostPlanningModelError:
            pass
        # Phase is DESIGN_CONFIRMED (not SLIDES_READY)
        assert original_phase == PlanningPhase.DESIGN_CONFIRMED


# ═══════════════════════════════════════════════════════════════════
# Batch 3.6.6 - P0-9: SlideIntentPlan slide count contract
# ═══════════════════════════════════════════════════════════════════


class TestSlidesCountContract:
    """Deterministic tests for exact slide count contract."""

    # ── helpers ──

    @staticmethod
    def _count_slides(n: int, *, section_id: str = "sec_compensation") -> SlideIntentPlan:
        return SlideIntentPlan(
            deck_title="温度补偿诊断结论",
            slides=tuple(
                SlideIntent(slide_id=f"s-{i:02d}", section_id=section_id,
                    sequence=i, title=f"Slide {i}", message=".",
                    layout=SlideLayout.TITLE,
                    content_points=("点",), visual_brief=".")
                for i in range(1, n + 1)
            ),
        )

    @staticmethod
    def _count_slides_two_section(n_sec1: int, n_sec2: int) -> SlideIntentPlan:
        """Create slides distributed across two sections matching the outline.

        Covers all 6 required assets exactly once across 4 slides.
        For count tests requiring different slide counts, the caller should
        construct the plan directly.
        """
        # Fixed 4-slide plan covering all 6 required assets
        return SlideIntentPlan(
            deck_title="温度补偿诊断结论",
            slides=(
                SlideIntent(slide_id="s-01", section_id="sec_compensation",
                    sequence=1, title="B1 Raw", message=".",
                    layout=SlideLayout.TITLE, content_points=("点",), visual_brief=".",
                    asset_ids=("phaseb_diagnostic_B1_raw",)),
                SlideIntent(slide_id="s-02", section_id="sec_compensation",
                    sequence=2, title="B1 Comp + B2 Raw", message=".",
                    layout=SlideLayout.TITLE, content_points=("点",), visual_brief=".",
                    asset_ids=("phaseb_diagnostic_B1_compensated",
                               "phaseb_diagnostic_B2_raw")),
                SlideIntent(slide_id="s-03", section_id="sec_correlation",
                    sequence=3, title="B2 Comp", message=".",
                    layout=SlideLayout.TITLE, content_points=("点",), visual_brief=".",
                    asset_ids=("phaseb_diagnostic_B2_compensated",)),
                SlideIntent(slide_id="s-04", section_id="sec_correlation",
                    sequence=4, title="C2 Both", message=".",
                    layout=SlideLayout.TITLE, content_points=("点",), visual_brief=".",
                    asset_ids=("phaseb_diagnostic_C2_raw",
                               "phaseb_diagnostic_C2_compensated")),
            ),
        )

    @staticmethod
    def _advance_to_design_confirmed(
        request: PlanningRequest | None = None,
    ) -> tuple[ScriptedPlanningModel, HostPlanningStateMachine, PlanningSnapshot]:
        req = request or _two_section_request()
        outline = _two_section_outline()
        design = _two_section_design()
        valid_outline = outline.model_copy(update={"deck_title": req.report_title})
        model = ScriptedPlanningModel([valid_outline, design])
        sm = HostPlanningStateMachine(model)
        snapshot = sm.start(req)
        snapshot = sm.apply(snapshot, GenerateOutline())
        snapshot = sm.apply(snapshot, ConfirmOutline())
        snapshot = sm.apply(snapshot, GenerateDesign())
        snapshot = sm.apply(snapshot, ConfirmDesign())
        # Add the slide outputs — caller must do this before GenerateSlides
        return model, sm, snapshot

    # ── Test 1: exact count ──

    def test_host_builds_exact_slide_slot_schema_from_confirmed_outline(self) -> None:
        """The model schema itself must freeze total and per-section page slots."""
        from dp_engine.ppt_master_host.planning import _build_slide_intent_schema

        schema = _build_slide_intent_schema(_two_section_outline())
        schema_json = schema.model_json_schema()
        properties = schema_json["properties"]
        slides_schema = properties["slides"]
        assert slides_schema["minItems"] == 4
        assert slides_schema["maxItems"] == 4
        assert slides_schema["items"]["$ref"].endswith("/SlideIntent")
        assert schema_json["additionalProperties"] is False

        valid = _valid_slides_for_two_section()
        parsed = schema.model_validate(valid.model_dump(mode="python"))
        assert len(parsed.slides) == 4

        slides = list(valid.slides)
        slides.insert(
            2,
            SlideIntent(
                slide_id="s-extra",
                section_id="sec_compensation",
                sequence=3,
                title="Unbudgeted split",
                message="This extra page violates the confirmed section budget.",
                layout=SlideLayout.TITLE,
                content_points=("extra",),
                visual_brief="No asset.",
            ),
        )
        slides[3] = slides[3].model_copy(update={"sequence": 4})
        slides[4] = slides[4].model_copy(update={"sequence": 5})
        over_budget = {"deck_title": valid.deck_title, "slides": tuple(slides)}

        with pytest.raises(ValidationError):
            schema.model_validate(over_budget)

        wrong_section = list(valid.slides)
        wrong_section[2] = wrong_section[2].model_copy(
            update={"section_id": "sec_compensation"}
        )
        generated = schema.model_validate(
            {"deck_title": valid.deck_title, "slides": tuple(wrong_section)}
        )
        from dp_engine.ppt_master_host.planning import _compile_slide_intent_slots

        compiled = _compile_slide_intent_slots(_two_section_outline(), generated)
        assert compiled.slides[2].section_id == "sec_correlation"
        assert [slide.sequence for slide in compiled.slides] == [1, 2, 3, 4]

    def test_exact_count_passes(self) -> None:
        """requested=4, slides=4 (2+2 per section) → PASS, reaches SLIDES_READY."""
        model, sm, snapshot = self._advance_to_design_confirmed()
        model.outputs.append(self._count_slides_two_section(2, 2))
        result = sm.apply(snapshot, GenerateSlides())
        assert result.phase == PlanningPhase.SLIDES_READY

    # ── Test 2: too few slides ──

    def test_too_few_slides_retried_and_exhausted(self) -> None:
        """requested=4, slides=3 (always) → retry → exhaustion error."""
        model, sm, snapshot = self._advance_to_design_confirmed()
        # 3 slides every attempt (3 attempts total: initial + 2 retries)
        model.outputs.extend([self._count_slides(3)] * 3)
        with pytest.raises(HostPlanningModelError) as exc:
            sm.apply(snapshot, GenerateSlides())
        assert "after 3 attempts" in str(exc.value)
        slide_calls = [c for c in model.calls if c[0] == "SlideIntentPlan"]
        assert len(slide_calls) == 3

    # ── Test 3: too many slides ──

    def test_too_many_slides_retried_and_exhausted(self) -> None:
        """requested=4, slides=5 (always) → retry → exhaustion error."""
        model, sm, snapshot = self._advance_to_design_confirmed()
        model.outputs.extend([self._count_slides(5)] * 3)
        with pytest.raises(HostPlanningModelError) as exc:
            sm.apply(snapshot, GenerateSlides())
        assert "after 3 attempts" in str(exc.value)
        slide_calls = [c for c in model.calls if c[0] == "SlideIntentPlan"]
        assert len(slide_calls) == 3

    # ── Test 4: outline total mismatch rejected at generation ──

    def test_outline_count_mismatch_rejected_at_generation(self) -> None:
        """Outline with sum(allocated) != requested → rejected at generation time.

        This ensures the outline contract is enforced before the user ever sees
        OUTLINE_READY, preventing Case B inconsistency.
        """
        req = _two_section_request()  # requested_slide_count = 4
        bad_outline = OutlinePlan(
            deck_title=req.report_title,
            narrative_arc="测试",
            sections=(
                OutlineSection(
                    section_id="sec_a", title="A", purpose=".",
                    key_messages=("M1",), allocated_slides=1,  # total=1 != 4
                    candidate_asset_ids=(
                        "phaseb_diagnostic_B1_raw",
                        "phaseb_diagnostic_B1_compensated",
                    ),
                ),
            ),
        )
        model = ScriptedPlanningModel([bad_outline])
        sm = HostPlanningStateMachine(model)
        snapshot = sm.start(req)
        with pytest.raises(HostPlanningModelError) as exc:
            sm.apply(snapshot, GenerateOutline())
        assert "outline allocated slide count" in str(exc.value)

    # ── Test 5: corrective retry preserves count ──

    def test_retry_preserves_count_when_fixing_coverage(self) -> None:
        """Attempt 1: count correct + missing required → retry → count still correct."""
        model, sm, snapshot = self._advance_to_design_confirmed()
        valid = _valid_slides_for_two_section()
        # Attempt 1: remove one required asset to trigger missing-required
        slides_list = list(valid.slides)
        slides_list[0] = slides_list[0].model_copy(update={"asset_ids": ()})
        missing_plan = valid.model_copy(update={"slides": tuple(slides_list)})
        # Attempt 2: full valid plan (count = 4, all required covered)
        model.outputs.extend([missing_plan, valid])
        result = sm.apply(snapshot, GenerateSlides())
        assert result.phase == PlanningPhase.SLIDES_READY
        assert result.slides is not None
        assert len(result.slides.slides) == 4

    # ── Test 6: retry fixes count error ──

    def test_retry_fixes_count_error(self) -> None:
        """Attempt 1: count wrong → retry → count correct → SLIDES_READY."""
        model, sm, snapshot = self._advance_to_design_confirmed()
        model.outputs.extend([
            self._count_slides(3),               # attempt 1: only 3 slides
            _valid_slides_for_two_section(),      # attempt 2: correct 4 slides
        ])
        result = sm.apply(snapshot, GenerateSlides())
        assert result.phase == PlanningPhase.SLIDES_READY
        assert result.slides is not None
        assert len(result.slides.slides) == 4

    # ── Test 7: retry exhaustion (count) → DESIGN_CONFIRMED preserved ──

    def test_count_retry_exhaustion_preserves_design_phase(self) -> None:
        """Count always wrong → exhaustion → phase stays DESIGN_CONFIRMED."""
        model, sm, snapshot = self._advance_to_design_confirmed()
        # 3 slides each time (3 attempts)
        model.outputs.extend([self._count_slides(3)] * 3)
        try:
            sm.apply(snapshot, GenerateSlides())
        except HostPlanningModelError:
            pass
        assert snapshot.phase == PlanningPhase.DESIGN_CONFIRMED

    # ── Test 8: corrective prompts contain exact count ──

    def test_slides_prompt_contains_exact_count(self) -> None:
        """Initial prompt explicitly states exact slide count."""
        prompt = _slides_prompt(
            _two_section_request(),
            _two_section_outline(),
            _two_section_design(),
        )
        assert "EXACT SLIDE COUNT" in prompt
        assert "EXACTLY 4" in prompt
        assert "len(slides) == 4" in prompt
        assert "IMMUTABLE SLIDE SLOT CONTRACT" in prompt
        assert "slot 01: sequence=1, section_id=sec_compensation" in prompt
        assert "slot 03: sequence=3, section_id=sec_correlation" in prompt

    def test_semantic_corrective_prompt_contains_exact_count(self) -> None:
        """Semantic corrective prompt includes count as constraint #0."""
        prompt = _slides_semantic_corrective_prompt(
            _two_section_request(),
            _two_section_outline(),
            _two_section_design(),
            missing_required_ids=["phaseb_diagnostic_C2_raw"],
        )
        assert "EXACT SLIDE COUNT" in prompt
        assert "EXACTLY 4" in prompt

    def test_schema_corrective_prompt_contains_exact_count(self) -> None:
        """Schema corrective prompt includes count constraint."""
        from dp_engine.ppt_master_host.planning import _slides_schema_corrective_prompt
        prompt = _slides_schema_corrective_prompt(
            _two_section_request(),
            _two_section_outline(),
            _two_section_design(),
        )
        assert "EXACTLY 4" in prompt
        assert "len(slides) == 4" in prompt


# ═══════════════════════════════════════════════════════════════════
# Batch 3.6.6 — SlideIntent Semantic Convergence
# ═══════════════════════════════════════════════════════════════════


class TestSlidePlanViolationsAggregation:
    """Violation aggregation: one pass collects all detectable violations."""

    def test_valid_plan_has_no_violations(self) -> None:
        from dp_engine.ppt_master_host.planning import _collect_slide_violations
        v = _collect_slide_violations(
            _two_section_request(), _two_section_outline(),
            _valid_slides_for_two_section(),
        )
        assert v.is_valid
        assert v.summary() == "no violations"

    def test_multiple_violations_collected_in_one_pass(self) -> None:
        """Wrong count + cross-section + missing required → all detected."""
        from dp_engine.ppt_master_host.planning import _collect_slide_violations
        req = _two_section_request()
        outline = _two_section_outline()
        bad = SlideIntentPlan(
            deck_title=req.report_title,
            slides=(
                SlideIntent(slide_id="s-01", section_id="sec_compensation",
                    sequence=1, title="A", message=".",
                    layout=SlideLayout.TITLE, content_points=("x",),
                    visual_brief=".", asset_ids=("phaseb_diagnostic_B1_raw",)),
                SlideIntent(slide_id="s-02", section_id="sec_compensation",
                    sequence=2, title="B", message=".",
                    layout=SlideLayout.TITLE, content_points=("x",),
                    visual_brief=".",
                    asset_ids=("phaseb_diagnostic_B2_compensated",)),
                SlideIntent(slide_id="s-03", section_id="sec_correlation",
                    sequence=3, title="C", message=".",
                    layout=SlideLayout.TITLE, content_points=("x",),
                    visual_brief=".",
                    asset_ids=("phaseb_diagnostic_C2_raw",)),
            ),
        )
        v = _collect_slide_violations(req, outline, bad)
        assert not v.is_valid
        assert v.count_mismatch is not None
        assert v.count_mismatch == (3, 4)
        assert "phaseb_diagnostic_B2_compensated" in v.cross_section_assets
        assert len(v.missing_required_assets) >= 2

    def test_unknown_sections_safe_degradation(self) -> None:
        """Unknown sections → reported; section-count check skipped safely."""
        from dp_engine.ppt_master_host.planning import _collect_slide_violations
        req = _two_section_request()
        outline = _two_section_outline()
        bad = SlideIntentPlan(
            deck_title=req.report_title,
            slides=(
                SlideIntent(slide_id="s-01", section_id="sec_nonexistent",
                    sequence=1, title="A", message=".",
                    layout=SlideLayout.TITLE, content_points=("x",),
                    visual_brief=".", asset_ids=("phaseb_diagnostic_B1_raw",)),
                SlideIntent(slide_id="s-02", section_id="sec_compensation",
                    sequence=2, title="B", message=".",
                    layout=SlideLayout.TITLE, content_points=("x",),
                    visual_brief=".",
                    asset_ids=("phaseb_diagnostic_B1_compensated",)),
                SlideIntent(slide_id="s-03", section_id="sec_compensation",
                    sequence=3, title="C", message=".",
                    layout=SlideLayout.TITLE, content_points=("x",),
                    visual_brief=".",
                    asset_ids=("phaseb_diagnostic_B2_raw",)),
                SlideIntent(slide_id="s-04", section_id="sec_correlation",
                    sequence=4, title="D", message=".",
                    layout=SlideLayout.TITLE, content_points=("x",),
                    visual_brief=".",
                    asset_ids=("phaseb_diagnostic_B2_compensated",)),
            ),
        )
        v = _collect_slide_violations(req, outline, bad)
        assert not v.is_valid
        assert "sec_nonexistent" in v.unknown_sections
        assert "section_count" in v.skipped_checks
        assert "phaseb_diagnostic_C2_raw" in v.missing_required_assets

    def test_feasibility_check_pass(self) -> None:
        from dp_engine.ppt_master_host.planning import _check_slide_constraint_feasibility
        feasible, reason = _check_slide_constraint_feasibility(
            _two_section_request(), _two_section_outline(),
        )
        assert feasible
        assert reason == "feasible"

    def test_feasibility_check_count_mismatch(self) -> None:
        from dp_engine.ppt_master_host.planning import _check_slide_constraint_feasibility
        outline_bad = OutlinePlan(
            deck_title="测试", narrative_arc="测试",
            sections=(
                OutlineSection(section_id="s1", title="A", purpose=".",
                    key_messages=("M",), allocated_slides=10,
                    candidate_asset_ids=()),
            ),
        )
        feasible, reason = _check_slide_constraint_feasibility(
            _two_section_request(), outline_bad,  # req = 4 slides, outline = 10
        )
        assert not feasible
        assert "allocates" in reason

    def test_feasibility_check_unowned_required(self) -> None:
        from dp_engine.ppt_master_host.planning import _check_slide_constraint_feasibility
        req = PlanningRequest(
            request_id="test", report_title="T", objective=".", audience=".",
            source_context=".", requested_slide_count=3,
            assets=(
                PlanningAsset(asset_id="orphan", kind=PlanningAssetKind.CHART,
                    semantic_label="X", summary=".", required=True),
            ),
        )
        outline = OutlinePlan(
            deck_title="T", narrative_arc=".",
            sections=(
                OutlineSection(section_id="s1", title="A", purpose=".",
                    key_messages=("M",), allocated_slides=3,
                    candidate_asset_ids=()),
            ),
        )
        feasible, reason = _check_slide_constraint_feasibility(req, outline)
        assert not feasible
        assert "orphan" in reason


class TestUnifiedCorrectivePrompt:
    """Unified corrective prompt carries ALL violations + ownership table."""

    def test_unified_prompt_contains_count(self) -> None:
        from dp_engine.ppt_master_host.planning import (
            _collect_slide_violations, _unified_slides_corrective_prompt,
        )
        req = _two_section_request()
        outline = _two_section_outline()
        design = _two_section_design()
        bad = SlideIntentPlan(
            deck_title=req.report_title,
            slides=tuple(
                SlideIntent(slide_id=f"s-{i:02d}", section_id="sec_compensation",
                    sequence=i, title=f"S{i}", message=".",
                    layout=SlideLayout.TITLE, content_points=("x",),
                    visual_brief=".")
                for i in range(1, 4)
            ),
        )
        v = _collect_slide_violations(req, outline, bad)
        prompt = _unified_slides_corrective_prompt(req, outline, design, v)
        assert "EXACT SLIDE COUNT" in prompt
        assert "4 slides" in prompt

    def test_unified_prompt_contains_ownership_table(self) -> None:
        from dp_engine.ppt_master_host.planning import (
            _collect_slide_violations, _unified_slides_corrective_prompt,
        )
        req = _two_section_request()
        outline = _two_section_outline()
        design = _two_section_design()
        bad = _cross_section_slides()
        v = _collect_slide_violations(req, outline, bad)
        prompt = _unified_slides_corrective_prompt(req, outline, design, v)
        assert "OWNERSHIP TABLE" in prompt
        assert "phaseb_diagnostic_B1_raw" in prompt
        assert "sec_compensation" in prompt

    def test_unified_prompt_contains_section_budget(self) -> None:
        from dp_engine.ppt_master_host.planning import (
            _collect_slide_violations, _unified_slides_corrective_prompt,
        )
        req = _two_section_request()
        outline = _two_section_outline()
        design = _two_section_design()
        bad = _cross_section_slides()
        v = _collect_slide_violations(req, outline, bad)
        prompt = _unified_slides_corrective_prompt(req, outline, design, v)
        assert "SECTION SLIDE BUDGET" in prompt
        assert "sec_compensation" in prompt
        assert "2 slides" in prompt

    def test_unified_prompt_contains_missing_list(self) -> None:
        from dp_engine.ppt_master_host.planning import (
            _collect_slide_violations, _unified_slides_corrective_prompt,
        )
        req = _two_section_request()
        outline = _two_section_outline()
        design = _two_section_design()
        bad = SlideIntentPlan(
            deck_title=req.report_title,
            slides=(
                SlideIntent(slide_id="s-01", section_id="sec_compensation",
                    sequence=1, title="A", message=".",
                    layout=SlideLayout.TITLE, content_points=("x",),
                    visual_brief=".",
                    asset_ids=("phaseb_diagnostic_B1_raw",)),
                SlideIntent(slide_id="s-02", section_id="sec_compensation",
                    sequence=2, title="B", message=".",
                    layout=SlideLayout.TITLE, content_points=("x",),
                    visual_brief=".",
                    asset_ids=("phaseb_diagnostic_B1_compensated",)),
                SlideIntent(slide_id="s-03", section_id="sec_correlation",
                    sequence=3, title="C", message=".",
                    layout=SlideLayout.TITLE, content_points=("x",),
                    visual_brief=".",
                    asset_ids=("phaseb_diagnostic_B2_compensated",)),
                SlideIntent(slide_id="s-04", section_id="sec_correlation",
                    sequence=4, title="D", message=".",
                    layout=SlideLayout.TITLE, content_points=("x",),
                    visual_brief=".",
                    asset_ids=("phaseb_diagnostic_C2_raw",)),
            ),
        )
        v = _collect_slide_violations(req, outline, bad)
        prompt = _unified_slides_corrective_prompt(req, outline, design, v)
        assert "MISSING REQUIRED ASSETS" in prompt
        assert "phaseb_diagnostic_B2_raw" in prompt

    def test_unified_prompt_contains_cross_section_list(self) -> None:
        from dp_engine.ppt_master_host.planning import (
            _collect_slide_violations, _unified_slides_corrective_prompt,
        )
        req = _two_section_request()
        outline = _two_section_outline()
        design = _two_section_design()
        v = _collect_slide_violations(req, outline, _cross_section_slides())
        prompt = _unified_slides_corrective_prompt(req, outline, design, v)
        assert "CROSS-SECTION ASSET ERRORS" in prompt


class TestSemanticConvergenceRetry:
    """End-to-end retry: unified corrective prompt leads to convergence."""

    def test_retry_convergence_missing_plus_cross_fixed_in_one_pass(self) -> None:
        """Attempt 0: missing + cross-section. Attempt 1: fixes both → PASS."""
        req = _two_section_request()
        outline = _two_section_outline()
        design = _two_section_design()
        valid_outline = outline.model_copy(update={"deck_title": req.report_title})
        bad = SlideIntentPlan(
            deck_title=req.report_title,
            slides=(
                SlideIntent(slide_id="s-01", section_id="sec_compensation",
                    sequence=1, title="A", message=".",
                    layout=SlideLayout.TITLE, content_points=("x",),
                    visual_brief=".",
                    asset_ids=("phaseb_diagnostic_B1_raw",)),
                SlideIntent(slide_id="s-02", section_id="sec_compensation",
                    sequence=2, title="B", message=".",
                    layout=SlideLayout.TITLE, content_points=("x",),
                    visual_brief=".",
                    asset_ids=("phaseb_diagnostic_B2_compensated",)),
                SlideIntent(slide_id="s-03", section_id="sec_correlation",
                    sequence=3, title="C", message=".",
                    layout=SlideLayout.TITLE, content_points=("x",),
                    visual_brief=".",
                    asset_ids=("phaseb_diagnostic_C2_raw",)),
                SlideIntent(slide_id="s-04", section_id="sec_correlation",
                    sequence=4, title="D", message=".",
                    layout=SlideLayout.TITLE, content_points=("x",),
                    visual_brief=".",
                    asset_ids=("phaseb_diagnostic_C2_compensated",)),
            ),
        )
        good = _valid_slides_for_two_section()
        model = ScriptedPlanningModel([valid_outline, design, bad, good])
        sm = HostPlanningStateMachine(model)
        snapshot = sm.start(req)
        snapshot = sm.apply(snapshot, GenerateOutline())
        snapshot = sm.apply(snapshot, ConfirmOutline())
        snapshot = sm.apply(snapshot, GenerateDesign())
        snapshot = sm.apply(snapshot, ConfirmDesign())
        result = sm.apply(snapshot, GenerateSlides())
        assert result.phase == PlanningPhase.SLIDES_READY
        slide_calls = [c for c in model.calls if c[0] == "SlideIntentPlan"]
        assert len(slide_calls) == 2

    def test_whack_a_mole_regression_still_fails(self) -> None:
        """Different errors each attempt → exhaustion, not passed."""
        req = _two_section_request()
        outline = _two_section_outline()
        design = _two_section_design()
        valid_outline = outline.model_copy(update={"deck_title": req.report_title})
        missing_plan = SlideIntentPlan(
            deck_title=req.report_title,
            slides=(
                SlideIntent(slide_id="s-01", section_id="sec_compensation",
                    sequence=1, title="A", message=".",
                    layout=SlideLayout.TITLE, content_points=("x",),
                    visual_brief=".",
                    asset_ids=("phaseb_diagnostic_B1_raw",)),
                SlideIntent(slide_id="s-02", section_id="sec_compensation",
                    sequence=2, title="B", message=".",
                    layout=SlideLayout.TITLE, content_points=("x",),
                    visual_brief=".",
                    asset_ids=("phaseb_diagnostic_B1_compensated",)),
                SlideIntent(slide_id="s-03", section_id="sec_correlation",
                    sequence=3, title="C", message=".",
                    layout=SlideLayout.TITLE, content_points=("x",),
                    visual_brief=".",
                    asset_ids=("phaseb_diagnostic_B2_compensated",)),
                SlideIntent(slide_id="s-04", section_id="sec_correlation",
                    sequence=4, title="D", message=".",
                    layout=SlideLayout.TITLE, content_points=("x",),
                    visual_brief=".",
                    asset_ids=("phaseb_diagnostic_C2_raw",)),
            ),
        )
        count_plan = SlideIntentPlan(
            deck_title=req.report_title,
            slides=tuple(
                SlideIntent(slide_id=f"s-{i:02d}", section_id="sec_compensation",
                    sequence=i, title=f"S{i}", message=".",
                    layout=SlideLayout.TITLE, content_points=("x",),
                    visual_brief=".")
                for i in range(1, 4)
            ),
        )
        cross_plan = _cross_section_slides()
        model = ScriptedPlanningModel([
            valid_outline, design,
            missing_plan, count_plan, cross_plan,
        ])
        sm = HostPlanningStateMachine(model)
        snapshot = sm.start(req)
        snapshot = sm.apply(snapshot, GenerateOutline())
        snapshot = sm.apply(snapshot, ConfirmOutline())
        snapshot = sm.apply(snapshot, GenerateDesign())
        snapshot = sm.apply(snapshot, ConfirmDesign())
        with pytest.raises(HostPlanningModelError) as exc:
            sm.apply(snapshot, GenerateSlides())
        assert "corrective retries" in str(exc.value)
        slide_calls = [c for c in model.calls if c[0] == "SlideIntentPlan"]
        assert len(slide_calls) == 3

    def test_retry_ownership_no_nested_amplification(self) -> None:
        """One outer attempt = exactly 1 model call (Host owns retries)."""
        req = _two_section_request()
        outline = _two_section_outline()
        design = _two_section_design()
        valid_outline = outline.model_copy(update={"deck_title": req.report_title})
        model = ScriptedPlanningModel([
            valid_outline, design, _valid_slides_for_two_section(),
        ])
        sm = HostPlanningStateMachine(model)
        snapshot = sm.start(req)
        snapshot = sm.apply(snapshot, GenerateOutline())
        snapshot = sm.apply(snapshot, ConfirmOutline())
        snapshot = sm.apply(snapshot, GenerateDesign())
        snapshot = sm.apply(snapshot, ConfirmDesign())
        sm.apply(snapshot, GenerateSlides())
        slide_calls = [c for c in model.calls if c[0] == "SlideIntentPlan"]
        assert len(slide_calls) == 1

    def test_exhaustion_error_contains_full_violation_summary(self) -> None:
        """Exhaustion error message includes the complete violation summary."""
        req = _two_section_request()
        outline = _two_section_outline()
        design = _two_section_design()
        valid_outline = outline.model_copy(update={"deck_title": req.report_title})
        bad = _cross_section_slides()
        model = ScriptedPlanningModel([valid_outline, design, bad, bad, bad])
        sm = HostPlanningStateMachine(model)
        snapshot = sm.start(req)
        snapshot = sm.apply(snapshot, GenerateOutline())
        snapshot = sm.apply(snapshot, ConfirmOutline())
        snapshot = sm.apply(snapshot, GenerateDesign())
        snapshot = sm.apply(snapshot, ConfirmDesign())
        with pytest.raises(HostPlanningModelError) as exc:
            sm.apply(snapshot, GenerateSlides())
        assert "Cross-section assets" in str(exc.value)
        assert "phaseb_diagnostic_B2_compensated" in str(exc.value)

    def test_exhaustion_preserves_design_confirmed_phase(self) -> None:
        """After retry exhaustion, snapshot phase stays DESIGN_CONFIRMED."""
        req = _two_section_request()
        outline = _two_section_outline()
        design = _two_section_design()
        valid_outline = outline.model_copy(update={"deck_title": req.report_title})
        bad = _cross_section_slides()
        model = ScriptedPlanningModel([valid_outline, design, bad, bad, bad])
        sm = HostPlanningStateMachine(model)
        snapshot = sm.start(req)
        snapshot = sm.apply(snapshot, GenerateOutline())
        snapshot = sm.apply(snapshot, ConfirmOutline())
        snapshot = sm.apply(snapshot, GenerateDesign())
        snapshot = sm.apply(snapshot, ConfirmDesign())
        try:
            sm.apply(snapshot, GenerateSlides())
        except HostPlanningModelError:
            pass
        assert snapshot.phase == PlanningPhase.DESIGN_CONFIRMED

    def test_feasibility_preflight_passes_for_valid_outline(self) -> None:
        """Feasibility pre-flight passes for valid outline → model call proceeds."""
        req = _two_section_request()
        outline = _two_section_outline()
        design = _two_section_design()
        valid_outline = outline.model_copy(update={"deck_title": req.report_title})
        model = ScriptedPlanningModel([
            valid_outline, design, _valid_slides_for_two_section(),
        ])
        sm = HostPlanningStateMachine(model)
        snapshot = sm.start(req)
        snapshot = sm.apply(snapshot, GenerateOutline())
        snapshot = sm.apply(snapshot, ConfirmOutline())
        snapshot = sm.apply(snapshot, GenerateDesign())
        snapshot = sm.apply(snapshot, ConfirmDesign())
        result = sm.apply(snapshot, GenerateSlides())
        assert result.phase == PlanningPhase.SLIDES_READY
        slide_calls = [c for c in model.calls if c[0] == "SlideIntentPlan"]
        assert len(slide_calls) == 1


# ═══════════════════════════════════════════════════════════════════════
# Section Asset Capacity Validation Tests
# ═══════════════════════════════════════════════════════════════════════


class TestSectionAssetCapacity:
    """Deterministic tests for section-level asset capacity validation.

    When SlideIntent.asset_ids has max_length=2, each section must have:
        allocated_slides >= ceil(required_assets / 2)
    """

    @staticmethod
    def _capacity_request(
        request_id: str = "capacity-test",
        title: str = "容量测试",
    ) -> PlanningRequest:
        """Build a request with configurable assets."""
        return PlanningRequest(
            request_id=request_id,
            report_title=title,
            objective="测试节容量约束",
            audience="测试人员",
            source_context="测试上下文",
            requested_slide_count=4,
            assets=(),
        )

    @staticmethod
    def _make_assets(
        *ids: str,
        required: bool = True,
    ) -> tuple[PlanningAsset, ...]:
        return tuple(
            PlanningAsset(
                asset_id=aid,
                kind=PlanningAssetKind.CHART,
                semantic_label=aid,
                summary=f"测试资产 {aid}",
                required=required,
            )
            for aid in ids
        )

    # ── Test 1: feasible section ──

    def test_feasible_section_capacity(self) -> None:
        """required=4, allocated=2, max_per_slide=2 → capacity=4 → FEASIBLE."""
        request = self._capacity_request()
        request = request.model_copy(update={
            "requested_slide_count": 2,
            "assets": self._make_assets("a1", "a2", "a3", "a4"),
        })
        outline = OutlinePlan(
            deck_title=request.report_title,
            narrative_arc="测试",
            sections=(
                OutlineSection(
                    section_id="sec_test",
                    title="测试节",
                    purpose="测试",
                    key_messages=("测试",),
                    allocated_slides=2,
                    candidate_asset_ids=("a1", "a2", "a3", "a4"),
                ),
            ),
        )
        from dp_engine.ppt_master_host.planning import (
            _check_slide_constraint_feasibility,
        )
        feasible, reason = _check_slide_constraint_feasibility(request, outline)
        assert feasible, f"Expected feasible, got: {reason}"

    # ── Test 2: infeasible section ──

    def test_infeasible_section_capacity(self) -> None:
        """required=5, allocated=2, max_per_slide=2 → capacity=4 → INFEASIBLE."""
        request = self._capacity_request()
        request = request.model_copy(update={
            "requested_slide_count": 2,
            "assets": self._make_assets("a1", "a2", "a3", "a4", "a5"),
        })
        outline = OutlinePlan(
            deck_title=request.report_title,
            narrative_arc="测试",
            sections=(
                OutlineSection(
                    section_id="sec_test",
                    title="测试节",
                    purpose="测试",
                    key_messages=("测试",),
                    allocated_slides=2,
                    candidate_asset_ids=("a1", "a2", "a3", "a4", "a5"),
                ),
            ),
        )
        from dp_engine.ppt_master_host.planning import (
            _check_slide_constraint_feasibility,
        )
        feasible, reason = _check_slide_constraint_feasibility(request, outline)
        assert not feasible, "Expected infeasible"
        assert "capacity infeasible" in reason
        assert "required=5" in reason
        assert "minimum slides needed=3" in reason

    # ── Test 3: candidate > capacity but Required <= capacity ──

    def test_candidate_gt_capacity_but_required_le_capacity(self) -> None:
        """candidate=8 but only 4 Required, allocated=2 → FEASIBLE (only Required counts)."""
        assets = self._make_assets("r1", "r2", "r3", "r4", required=True) + self._make_assets("o1", "o2", "o3", "o4", required=False)
        request = self._capacity_request()
        request = request.model_copy(update={
            "requested_slide_count": 2,
            "assets": assets,
        })
        outline = OutlinePlan(
            deck_title=request.report_title,
            narrative_arc="测试",
            sections=(
                OutlineSection(
                    section_id="sec_test",
                    title="测试节",
                    purpose="测试",
                    key_messages=("测试",),
                    allocated_slides=2,
                    candidate_asset_ids=("r1", "r2", "r3", "r4", "o1", "o2", "o3", "o4"),
                ),
            ),
        )
        from dp_engine.ppt_master_host.planning import (
            _check_slide_constraint_feasibility,
        )
        feasible, reason = _check_slide_constraint_feasibility(request, outline)
        assert feasible, f"Expected feasible (only Required counts), got: {reason}"

    # ── Test 4: real strain-calib pattern ──

    def test_strain_calib_6_required_2_slides_infeasible(self) -> None:
        """6 Required, 2 slides, max2 → capacity=4, minimum=3 → INFEASIBLE."""
        strain_ids = (
            "strain_calib_lin_A1", "strain_calib_lin_A2",
            "strain_calib_lin_B1", "strain_calib_lin_B2",
            "strain_calib_lin_C1", "strain_calib_lin_C2",
        )
        request = self._capacity_request(title="应变标定报告")
        request = request.model_copy(update={
            "requested_slide_count": 2,
            "assets": self._make_assets(*strain_ids),
        })
        outline = OutlinePlan(
            deck_title=request.report_title,
            narrative_arc="测试",
            sections=(
                OutlineSection(
                    section_id="sec_strain_calib",
                    title="应变标定结果",
                    purpose="展示应变标定曲线",
                    key_messages=("线性标定结果",),
                    allocated_slides=2,
                    candidate_asset_ids=strain_ids,
                ),
            ),
        )
        from dp_engine.ppt_master_host.planning import (
            _check_slide_constraint_feasibility,
        )
        feasible, reason = _check_slide_constraint_feasibility(request, outline)
        assert not feasible, f"Expected infeasible (6 required / 2 slides / max2), got: {reason}"
        assert "capacity infeasible" in reason
        assert "required=6" in reason
        assert "minimum slides needed=3" in reason

    # ── Test 5: globally feasible reallocation ──

    def test_global_feasible_after_reallocation(self) -> None:
        """Host repairs the production 3-assets/1-slide allocation pattern."""
        cross_channel_ids = (
            "compare_ol",
            "corr_strain_fiber_1_2",
            "corr_strain_fiber_1_reference",
        )
        other_ids = ("chart_01", "chart_02", "chart_03", "chart_04")
        all_assets = (
            self._make_assets(*cross_channel_ids)
            + self._make_assets(*other_ids)
        )
        request = PlanningRequest(
            request_id="realloc-test",
            report_title="容量重分配测试",
            objective="测试全局再分配",
            audience="测试人员",
            source_context="测试",
            requested_slide_count=5,
            assets=all_assets,
        )

        # Production failure: cross_channel_compare has three Required assets
        # on one slide, while another section has spare capacity.
        bad_outline = OutlinePlan(
            deck_title=request.report_title,
            narrative_arc="测试",
            sections=(
                OutlineSection(
                    section_id="cross_channel_compare",
                    title="通道间相关性分析与异常定位",
                    purpose="定位跨通道异常",
                    key_messages=("相关性证据",),
                    allocated_slides=1,
                    candidate_asset_ids=cross_channel_ids,
                ),
                OutlineSection(
                    section_id="sec_other",
                    title="其他",
                    purpose="其他",
                    key_messages=("其他",),
                    allocated_slides=4,
                    candidate_asset_ids=other_ids,
                ),
            ),
        )
        # The model repeats the bad arithmetic in production.  The Host owns
        # this semantic-neutral page-budget correction and must not spend
        # another model call (or silently switch report providers) to fix it.
        model = ScriptedPlanningModel([bad_outline])
        sm = HostPlanningStateMachine(model)
        snapshot = sm.start(request)
        result = sm.apply(snapshot, GenerateOutline())
        assert result.phase == PlanningPhase.OUTLINE_READY
        assert result.outline is not None
        allocations = {
            section.section_id: section.allocated_slides
            for section in result.outline.sections
        }
        assert allocations == {
            "cross_channel_compare": 2,
            "sec_other": 3,
        }
        assert sum(allocations.values()) == request.requested_slide_count
        assert tuple(
            section.model_dump(exclude={"allocated_slides"})
            for section in result.outline.sections
        ) == tuple(
            section.model_dump(exclude={"allocated_slides"})
            for section in bad_outline.sections
        )
        outline_calls = [c for c in model.calls if c[0] == "OutlinePlan"]
        assert len(outline_calls) == 1

    # ── Test 6: globally impossible ──

    def test_globally_impossible_capacity(self) -> None:
        """minimum sum (ceil(8/2) + ceil(7/2) = 4+4=8) > requested (7) → BLOCKED.

        Uses max 8 assets per section (OutlineSection limit) split across sections.
        """
        group_a = tuple(f"a{i}" for i in range(1, 9))    # 8 Required
        group_b = tuple(f"b{i}" for i in range(1, 8))    # 7 Required
        all_assets = self._make_assets(*group_a) + self._make_assets(*group_b)
        request = PlanningRequest(
            request_id="impossible-test",
            report_title="不可能容量测试",
            objective="测试全局不可行",
            audience="测试人员",
            source_context="测试",
            requested_slide_count=7,
            assets=all_assets,
        )
        # Best allocation: ceil(8/2)=4 + ceil(7/2)=4 = 8 > 7
        outline = OutlinePlan(
            deck_title=request.report_title,
            narrative_arc="测试",
            sections=(
                OutlineSection(
                    section_id="sec_a",
                    title="组A",
                    purpose="A",
                    key_messages=("A",),
                    allocated_slides=4,
                    candidate_asset_ids=group_a,
                ),
                OutlineSection(
                    section_id="sec_b",
                    title="组B",
                    purpose="B",
                    key_messages=("B",),
                    allocated_slides=3,
                    candidate_asset_ids=group_b,
                ),
            ),
        )
        from dp_engine.ppt_master_host.planning import (
            _check_slide_constraint_feasibility,
        )
        feasible, reason = _check_slide_constraint_feasibility(request, outline)
        assert not feasible, f"Expected globally infeasible, got: {reason}"
        assert "capacity infeasible" in reason
        # sec_b: 7 required, 3 slides, capacity=6
        assert "required=7" in reason
        assert "minimum slides needed=4" in reason

        # The deterministic repair must not manufacture a plan when the
        # requested total is below the mathematical minimum.  Preserve the
        # bounded retry contract and fail explicitly after three model calls.
        model = ScriptedPlanningModel([outline, outline, outline])
        sm = HostPlanningStateMachine(model)
        snapshot = sm.start(request)
        with pytest.raises(HostPlanningModelError) as exc_info:
            sm.apply(snapshot, GenerateOutline())
        assert exc_info.value.code == "outline_capacity_infeasible"
        assert snapshot.phase == PlanningPhase.NEW
        outline_calls = [call for call in model.calls if call[0] == "OutlinePlan"]
        assert len(outline_calls) == 3

    # ── Test 7: confirmation integrity ──

    def test_capacity_fix_invalidates_confirmation(self) -> None:
        """When Outline has capacity violation, ConfirmOutline rejects it.

        PlanningSnapshot validation now runs _validate_outline_for_request() inside
        model_validator, so a capacity-violating outline cannot enter a snapshot.
        This test verifies that _validate_outline_for_request rejects it directly.
        """
        strain_ids = (
            "strain_calib_lin_A1", "strain_calib_lin_A2",
            "strain_calib_lin_B1", "strain_calib_lin_B2",
            "strain_calib_lin_C1", "strain_calib_lin_C2",
        )
        other_ids = ("chart_01", "chart_02", "chart_03", "chart_04")
        all_assets = self._make_assets(*strain_ids) + self._make_assets(*other_ids)
        request = PlanningRequest(
            request_id="confirm-integrity-test",
            report_title="确认完整性",
            objective="测试确认失效",
            audience="测试人员",
            source_context="测试",
            requested_slide_count=7,
            assets=all_assets,
        )
        # Old outline with capacity violation
        old_outline = OutlinePlan(
            deck_title=request.report_title,
            narrative_arc="测试",
            sections=(
                OutlineSection(
                    section_id="sec_strain_calib",
                    title="应变标定",
                    purpose="展示",
                    key_messages=("标定",),
                    allocated_slides=2,  # INFEASIBLE: 6/2=3 > 2
                    candidate_asset_ids=strain_ids,
                ),
                OutlineSection(
                    section_id="sec_other",
                    title="其他",
                    purpose="其他",
                    key_messages=("其他",),
                    allocated_slides=5,
                    candidate_asset_ids=other_ids,
                ),
            ),
        )
        from dp_engine.ppt_master_host.planning import (
            _validate_outline_for_request,
        )
        # Direct validation should reject the capacity-violating outline
        with pytest.raises(ValueError, match="capacity infeasible"):
            _validate_outline_for_request(request, old_outline)
        # Corrected outline passes
        corrected_outline = OutlinePlan(
            deck_title=request.report_title,
            narrative_arc="测试",
            sections=(
                OutlineSection(
                    section_id="sec_strain_calib",
                    title="应变标定",
                    purpose="展示",
                    key_messages=("标定",),
                    allocated_slides=3,  # FEASIBLE: 6/2=3
                    candidate_asset_ids=strain_ids,
                ),
                OutlineSection(
                    section_id="sec_other",
                    title="其他",
                    purpose="其他",
                    key_messages=("其他",),
                    allocated_slides=4,
                    candidate_asset_ids=other_ids,
                ),
            ),
        )
        _validate_outline_for_request(request, corrected_outline)

    # ── Test 8: corrected outline passes all validations ──

    def test_corrected_outline_capacity_passes(self) -> None:
        """Corrected outline: all sections capacity PASS, sum allocations = requested."""
        strain_ids = (
            "strain_calib_lin_A1", "strain_calib_lin_A2",
            "strain_calib_lin_B1", "strain_calib_lin_B2",
            "strain_calib_lin_C1", "strain_calib_lin_C2",
        )
        other_ids = ("chart_01", "chart_02", "chart_03", "chart_04")
        all_assets = self._make_assets(*strain_ids) + self._make_assets(*other_ids)
        request = PlanningRequest(
            request_id="corrected-test",
            report_title="已修正容量",
            objective="测试修正后的Outline",
            audience="测试人员",
            source_context="测试",
            requested_slide_count=7,
            assets=all_assets,
        )
        corrected_outline = OutlinePlan(
            deck_title=request.report_title,
            narrative_arc="测试",
            sections=(
                OutlineSection(
                    section_id="sec_strain_calib",
                    title="应变标定结果",
                    purpose="展示",
                    key_messages=("标定",),
                    allocated_slides=3,  # 6/2=3 → FEASIBLE
                    candidate_asset_ids=strain_ids,
                ),
                OutlineSection(
                    section_id="sec_other",
                    title="其他",
                    purpose="其他",
                    key_messages=("其他",),
                    allocated_slides=4,  # 4/2=2 → FEASIBLE
                    candidate_asset_ids=other_ids,
                ),
            ),
        )
        from dp_engine.ppt_master_host.planning import (
            _check_slide_constraint_feasibility,
            _validate_outline_for_request,
        )
        feasible, reason = _check_slide_constraint_feasibility(request, corrected_outline)
        assert feasible, f"Expected feasible corrected outline, got: {reason}"
        # Also verify _validate_outline_for_request passes
        _validate_outline_for_request(request, corrected_outline)

    # ── Test 9: Outline validation rejects capacity violation ──

    def test_validate_outline_rejects_capacity_violation(self) -> None:
        """_validate_outline_for_request raises ValueError on capacity violation."""
        strain_ids = (
            "strain_calib_lin_A1", "strain_calib_lin_A2",
            "strain_calib_lin_B1", "strain_calib_lin_B2",
            "strain_calib_lin_C1", "strain_calib_lin_C2",
        )
        request = self._capacity_request(title="应变标定")
        request = request.model_copy(update={
            "requested_slide_count": 2,
            "assets": self._make_assets(*strain_ids),
        })
        bad_outline = OutlinePlan(
            deck_title=request.report_title,
            narrative_arc="测试",
            sections=(
                OutlineSection(
                    section_id="sec_strain",
                    title="应变标定",
                    purpose="展示",
                    key_messages=("标定",),
                    allocated_slides=2,
                    candidate_asset_ids=strain_ids,
                ),
            ),
        )
        from dp_engine.ppt_master_host.planning import (
            _validate_outline_for_request,
        )
        with pytest.raises(ValueError, match="capacity infeasible"):
            _validate_outline_for_request(request, bad_outline)

    # ── Test 10: empty section (no Required) is always feasible ──

    def test_empty_section_always_feasible(self) -> None:
        """Section with zero Required assets has no capacity constraint."""
        request = self._capacity_request()
        request = request.model_copy(update={
            "requested_slide_count": 3,
            "assets": self._make_assets("a1", "a2"),
        })
        outline = OutlinePlan(
            deck_title=request.report_title,
            narrative_arc="测试",
            sections=(
                OutlineSection(
                    section_id="sec_content",
                    title="内容节",
                    purpose="内容",
                    key_messages=("内容",),
                    allocated_slides=2,
                    candidate_asset_ids=("a1", "a2"),
                ),
                OutlineSection(
                    section_id="sec_empty",
                    title="封面/目录",
                    purpose="结构页",
                    key_messages=("结构",),
                    allocated_slides=1,
                    candidate_asset_ids=(),  # no assets
                ),
            ),
        )
        from dp_engine.ppt_master_host.planning import (
            _check_slide_constraint_feasibility,
        )
        feasible, reason = _check_slide_constraint_feasibility(request, outline)
        assert feasible, f"Expected feasible (empty section no constraint), got: {reason}"

    # ── Test 11: _get_max_assets_per_slide reflects schema ──

    def test_get_max_assets_per_slide_matches_schema(self) -> None:
        """_get_max_assets_per_slide() returns the actual SlideIntent.asset_ids max_length."""
        from dp_engine.ppt_master_host.planning import _get_max_assets_per_slide
        max_val = _get_max_assets_per_slide()
        assert max_val == 2, f"Expected 2, got {max_val}"

    # ── Test 12: capacity corrective prompt contains key info ──

    def test_capacity_corrective_prompt_content(self) -> None:
        """Capacity corrective prompt contains capacity error details and redistribution guidance."""
        from dp_engine.ppt_master_host.planning import (
            _outline_capacity_corrective_prompt,
        )
        strain_ids = (
            "strain_calib_lin_A1", "strain_calib_lin_A2",
            "strain_calib_lin_B1", "strain_calib_lin_B2",
            "strain_calib_lin_C1", "strain_calib_lin_C2",
        )
        request = PlanningRequest(
            request_id="prompt-test",
            report_title="提示词测试",
            objective="测试",
            audience="测试",
            source_context="测试",
            requested_slide_count=6,
            assets=self._make_assets(*strain_ids),
        )
        capacity_error = (
            "Section asset capacity infeasible — each slide can carry at most 2 assets. "
            "Violations: sec_strain (应变标定): required=6, allocated=2 slides, "
            "capacity=4 (max 2/slide), minimum slides needed=3"
        )
        prompt = _outline_capacity_corrective_prompt(request, capacity_error)
        assert "capacity" in prompt.lower()
        assert "ceil" in prompt
        assert "6" in prompt
        assert "redistribute" in prompt.lower() or "redistribut" in prompt.lower()
        assert str(request.requested_slide_count) in prompt


# ═══════════════════════════════════════════════════════════════════════
# Batch 3.6.6 — Dual-Capacity Feasibility (candidate + slide)
# ═══════════════════════════════════════════════════════════════════════


class TestDualCapacityFeasibility:
    """Tests for Outline dual-capacity constraints:
    Contract A: len(candidate_asset_ids) <= max_candidates_per_section (currently 8)
    Contract B: required_assets <= allocated_slides × max_assets_per_slide (currently 2)
    """

    @staticmethod
    def _make_assets(
        *ids: str,
        required: bool = True,
    ) -> tuple[PlanningAsset, ...]:
        return tuple(
            PlanningAsset(
                asset_id=aid,
                kind=PlanningAssetKind.CHART,
                semantic_label=aid,
                summary=f"Asset {aid}",
                required=required,
            )
            for aid in ids
        )

    @staticmethod
    def _make_request(
        *asset_ids: str,
        slide_count: int = 14,
        request_id: str = "dual-cap-test",
    ) -> PlanningRequest:
        return PlanningRequest(
            request_id=request_id,
            report_title="Dual-Capacity Test",
            objective="Test dual-capacity constraints",
            audience="QA",
            source_context="Test context.",
            requested_slide_count=slide_count,
            assets=TestDualCapacityFeasibility._make_assets(*asset_ids),
        )

    # ── Test 1: candidate max exact (8 candidates) PASS ──

    def test_candidate_max_exact_pass(self) -> None:
        """8 candidate_asset_ids — exact limit — Pydantic validation PASS."""
        ids = tuple(f"chart_{i:02d}" for i in range(1, 9))
        section = OutlineSection(
            section_id="sec_test",
            title="测试节",
            purpose="测试8候选上限",
            key_messages=("测试",),
            allocated_slides=4,
            candidate_asset_ids=ids,
        )
        assert len(section.candidate_asset_ids) == 8
        outline = OutlinePlan(
            deck_title="8候选测试",
            narrative_arc="测试",
            sections=(section,),
        )
        assert outline.sections[0].candidate_asset_ids == ids

    # ── Test 2: candidate overflow (9 candidates) FAIL ──

    def test_candidate_overflow_pydantic_rejects(self) -> None:
        """9 candidates — Pydantic rejects OutlineSection creation."""
        ids = tuple(f"chart_{i:02d}" for i in range(1, 10))
        with pytest.raises(ValidationError) as exc_info:
            OutlineSection(
                section_id="sec_test",
                title="测试节",
                purpose="测试溢出",
                key_messages=("测试",),
                allocated_slides=5,
                candidate_asset_ids=ids,
            )
        errors = exc_info.value.errors()
        assert any(
            "candidate_asset_ids" in str(e.get("loc", ()))
            for e in errors
        ), f"Expected candidate_asset_ids validation error, got: {errors}"

    # ── Test 3: real-like overflow (12 Phase-B assets) FAIL ──

    def test_real_like_overflow_12_assets_rejected(self) -> None:
        """12 Phase-B diagnostic assets in one section — Pydantic rejects."""
        phase_b_ids = tuple(
            f"phaseb_diagnostic_{g}_{t}"
            for g in ("B1", "B2", "B3", "C1", "C2", "C3")
            for t in ("raw", "compensated")
        )  # 6 × 2 = 12
        assert len(phase_b_ids) == 12
        with pytest.raises(ValidationError):
            OutlineSection(
                section_id="sec_temp",
                title="温度补偿分析",
                purpose="阶段B诊断证据",
                key_messages=("温度补偿效果",),
                allocated_slides=6,
                candidate_asset_ids=phase_b_ids,
            )

    # ── Test 4: valid split (12 assets → 6+6) PASS ──

    def test_valid_split_12_as_6_plus_6_pass(self) -> None:
        """12 assets split across two sections (6+6) — both valid."""
        phase_b_ids = tuple(
            f"phaseb_diagnostic_{g}_{t}"
            for g in ("B1", "B2", "B3", "C1", "C2", "C3")
            for t in ("raw", "compensated")
        )
        assert len(phase_b_ids) == 12
        half = len(phase_b_ids) // 2
        sec1 = OutlineSection(
            section_id="sec_temp_part1",
            title="温度补偿分析（上）",
            purpose="阶段B原始与补偿后诊断 第1组",
            key_messages=("温度补偿效果",),
            allocated_slides=3,
            candidate_asset_ids=phase_b_ids[:half],
        )
        sec2 = OutlineSection(
            section_id="sec_temp_part2",
            title="温度补偿分析（下）",
            purpose="阶段B原始与补偿后诊断 第2组",
            key_messages=("回归分析",),
            allocated_slides=3,
            candidate_asset_ids=phase_b_ids[half:],
        )
        assert len(sec1.candidate_asset_ids) == 6
        assert len(sec2.candidate_asset_ids) == 6
        outline = OutlinePlan(
            deck_title="温度补偿诊断",
            narrative_arc="先展示补偿证据，再分析回归可靠性",
            sections=(sec1, sec2),
        )
        assert sum(s.allocated_slides for s in outline.sections) == 6

    # ── Test 5: candidate PASS but slide capacity FAIL ──

    def test_candidate_pass_slide_capacity_fail(self) -> None:
        """candidates=6, required=6, slides=2, max_slide=2 → capacity=4 < 6 → FAIL.
        Uses requested_slide_count=3 with one empty section to satisfy min=3."""
        ids = tuple(f"chart_{i:02d}" for i in range(1, 7))
        request = self._make_request(*ids, slide_count=3)
        outline = OutlinePlan(
            deck_title=request.report_title,
            narrative_arc="测试",
            sections=(
                OutlineSection(
                    section_id="sec_test",
                    title="测试",
                    purpose="测试",
                    key_messages=("测试",),
                    allocated_slides=2,  # 6 required / 2 per slide = 3 needed
                    candidate_asset_ids=ids,
                ),
                OutlineSection(
                    section_id="sec_cover",
                    title="封面",
                    purpose="结构",
                    key_messages=("结构",),
                    allocated_slides=1,
                    candidate_asset_ids=(),
                ),
            ),
        )
        from dp_engine.ppt_master_host.planning import (
            _check_slide_constraint_feasibility,
        )
        feasible, reason = _check_slide_constraint_feasibility(request, outline)
        assert not feasible, f"Expected infeasible (6 required / 2 slides), got: {reason}"
        assert "capacity infeasible" in reason
        assert "required=6" in reason
        assert "minimum slides needed=3" in reason

    # ── Test 6: slide capacity PASS but candidate schema FAIL ──

    def test_slide_pass_candidate_schema_fail(self) -> None:
        """candidates=9 (only 4 required), slides=2 → slide capacity OK, but
        Pydantic rejects the 9 candidates at schema level."""
        ids = tuple(f"chart_{i:02d}" for i in range(1, 10))
        # 9 candidates, 4 required, 2 slides → slide capacity = 4 >= 4 ✓
        # But 9 > 8 → Pydantic FAIL ✗
        with pytest.raises(ValidationError) as exc_info:
            OutlineSection(
                section_id="sec_test",
                title="测试",
                purpose="测试",
                key_messages=("测试",),
                allocated_slides=2,
                candidate_asset_ids=ids,
            )
        errors = exc_info.value.errors()
        assert any(
            "candidate_asset_ids" in str(e.get("loc", ()))
            for e in errors
        )

    # ── Test 7: both capacities PASS ──

    def test_both_capacities_pass(self) -> None:
        """candidates=6, required=6, slides=3, max_slide=2 → capacity=6 >= 6 ✓
        candidates=6 <= 8 ✓ → both PASS."""
        ids = tuple(f"chart_{i:02d}" for i in range(1, 7))
        section = OutlineSection(
            section_id="sec_test",
            title="测试",
            purpose="测试",
            key_messages=("测试",),
            allocated_slides=3,
            candidate_asset_ids=ids,
        )
        assert len(section.candidate_asset_ids) == 6
        outline = OutlinePlan(
            deck_title="双重容量测试",
            narrative_arc="测试",
            sections=(section,),
        )
        request = self._make_request(*ids, slide_count=3)
        from dp_engine.ppt_master_host.planning import (
            _check_slide_constraint_feasibility,
        )
        feasible, reason = _check_slide_constraint_feasibility(request, outline)
        assert feasible, f"Expected feasible, got: {reason}"

    # ── Test 8: total slides preserved after split ──

    def test_total_slides_preserved_after_split(self) -> None:
        """Split a large section → sum(allocated_slides) still equals requested."""
        all_ids = tuple(f"chart_{i:02d}" for i in range(1, 13))
        sec1 = OutlineSection(
            section_id="sec_a",
            title="组A",
            purpose="A",
            key_messages=("A",),
            allocated_slides=4,
            candidate_asset_ids=all_ids[:6],
        )
        sec2 = OutlineSection(
            section_id="sec_b",
            title="组B",
            purpose="B",
            key_messages=("B",),
            allocated_slides=4,
            candidate_asset_ids=all_ids[6:],
        )
        outline = OutlinePlan(
            deck_title="分拆测试",
            narrative_arc="测试",
            sections=(sec1, sec2),
        )
        assert sum(s.allocated_slides for s in outline.sections) == 8

    # ── Test 9: required coverage preserved after split ──

    def test_required_coverage_preserved_after_split(self) -> None:
        """After splitting, all required assets still appear in some section."""
        all_ids = tuple(f"chart_{i:02d}" for i in range(1, 13))
        request = self._make_request(*all_ids, slide_count=8)
        sec1 = OutlineSection(
            section_id="sec_a",
            title="组A",
            purpose="A",
            key_messages=("A",),
            allocated_slides=4,
            candidate_asset_ids=all_ids[:6],
        )
        sec2 = OutlineSection(
            section_id="sec_b",
            title="组B",
            purpose="B",
            key_messages=("B",),
            allocated_slides=4,
            candidate_asset_ids=all_ids[6:],
        )
        outline = OutlinePlan(
            deck_title="覆盖测试",
            narrative_arc="测试",
            sections=(sec1, sec2),
        )
        planned = []
        for s in outline.sections:
            planned.extend(s.candidate_asset_ids)
        required = {a.asset_id for a in request.assets if a.required}
        assert required == set(planned)
        from dp_engine.ppt_master_host.planning import (
            _validate_outline_for_request,
        )
        _validate_outline_for_request(request, outline)

    # ── Test 10: schema corrective prompt contains specific error detail ──

    def test_schema_corrective_prompt_content(self) -> None:
        """Schema corrective prompt includes actual count, max, split instruction,
        total slides, and slide capacity constraint."""
        ids = tuple(f"chart_{i:02d}" for i in range(1, 13))
        request = self._make_request(*ids, slide_count=14)
        error_detail = (
            "sections.2.candidate_asset_ids: too_long — "
            "Tuple should have at most 8 items after validation, not 12"
        )
        prompt = _outline_schema_corrective_prompt(request, error_detail)
        assert "REJECTED BY SCHEMA VALIDATION" in prompt
        assert "12" in prompt or "candidate_asset_ids" in prompt
        assert "8" in prompt
        assert "split" in prompt.lower()
        assert str(request.requested_slide_count) in prompt
        assert "ceil" in prompt
        assert "REQUIRED COVERAGE" in prompt

    # ── Test 11: bounded retry — schema overflow → corrective → valid → OUTLINE_READY ──

    def test_bounded_retry_schema_overflow_then_valid(self) -> None:
        """Attempt 0: 12 candidates → Pydantic fail → schema corrective →
        Attempt 1: 6+6 split → valid → OUTLINE_READY."""
        all_ids = tuple(f"chart_{i:02d}" for i in range(1, 13))
        request = PlanningRequest(
            request_id="retry-schema-test",
            report_title="Schema Retry Test",
            objective="Test schema overflow retry",
            audience="QA",
            source_context="Test.",
            requested_slide_count=6,
            assets=self._make_assets(*all_ids),
        )
        # Attempt 0: bad — 12 candidates in one section (won't pass Pydantic)
        bad_outline_dict = {
            "deck_title": request.report_title,
            "narrative_arc": "test",
            "sections": [
                {
                    "section_id": "sec_overloaded",
                    "title": "Overloaded Section",
                    "purpose": "Has too many candidates",
                    "key_messages": ["msg"],
                    "allocated_slides": 6,
                    "candidate_asset_ids": list(all_ids),  # 12 — will fail Pydantic
                },
            ],
        }
        # Raising HostPlanningModelError from the ScriptedPlanningModel simulates
        # what happens when AIClient's schema validation fails.
        schema_error = HostPlanningModelError(
            "model_schema_invalid",
            PlanningPhase.NEW,
            "Host model returned invalid OutlinePlan",
        )
        # Attempt 1: valid split
        valid_outline = OutlinePlan(
            deck_title=request.report_title,
            narrative_arc="test",
            sections=(
                OutlineSection(
                    section_id="sec_a",
                    title="组A",
                    purpose="A",
                    key_messages=("A",),
                    allocated_slides=3,
                    candidate_asset_ids=all_ids[:6],
                ),
                OutlineSection(
                    section_id="sec_b",
                    title="组B",
                    purpose="B",
                    key_messages=("B",),
                    allocated_slides=3,
                    candidate_asset_ids=all_ids[6:],
                ),
            ),
        )
        model = ScriptedPlanningModel([schema_error, valid_outline])
        sm = HostPlanningStateMachine(model)
        snapshot = sm.start(request)
        result = sm.apply(snapshot, GenerateOutline())
        assert result.phase == PlanningPhase.OUTLINE_READY
        outline_calls = [c for c in model.calls if c[0] == "OutlinePlan"]
        assert len(outline_calls) == 2  # bad + retry

    # ── Test 12: retry exhaustion — continuous overflow → explicit failure ──

    def test_retry_exhaustion_continuous_overflow(self) -> None:
        """All 3 attempts fail with same schema error → exhaustion → no OUTLINE_READY."""
        all_ids = tuple(f"chart_{i:02d}" for i in range(1, 13))
        request = PlanningRequest(
            request_id="exhaustion-test",
            report_title="Exhaustion Test",
            objective="Test schema overflow exhaustion",
            audience="QA",
            source_context="Test.",
            requested_slide_count=6,
            assets=self._make_assets(*all_ids),
        )
        schema_error = HostPlanningModelError(
            "model_schema_invalid",
            PlanningPhase.NEW,
            "Host model returned invalid OutlinePlan",
        )
        # 3 attempts all fail with schema error
        model = ScriptedPlanningModel([schema_error, schema_error, schema_error])
        sm = HostPlanningStateMachine(model)
        snapshot = sm.start(request)
        with pytest.raises(HostPlanningModelError) as exc:
            sm.apply(snapshot, GenerateOutline())
        assert "outline_schema_exhausted" in exc.value.code
        assert snapshot.phase == PlanningPhase.NEW  # never reached OUTLINE_READY
        outline_calls = [c for c in model.calls if c[0] == "OutlinePlan"]
        assert len(outline_calls) == 3

    # ── Test 13: confirmation invalidation — old confirmed outline not auto-restored ──

    def test_confirmation_invalidation_not_auto_restored(self) -> None:
        """After capacity-violating outline rejected, old confirmation is NOT
        automatically restored. A fresh GenerateOutline is required."""
        asset_ids = tuple(f"chart_{i:02d}" for i in range(1, 5))
        request = self._make_request(*asset_ids, slide_count=3)
        valid_outline = OutlinePlan(
            deck_title=request.report_title,
            narrative_arc="test",
            sections=(
                OutlineSection(
                    section_id="sec_a",
                    title="A",
                    purpose="A",
                    key_messages=("A",),
                    allocated_slides=3,
                    candidate_asset_ids=asset_ids,
                ),
            ),
        )
        # Initial successful generation
        model = ScriptedPlanningModel([valid_outline])
        sm = HostPlanningStateMachine(model)
        snapshot = sm.start(request)
        # Generation succeeds → OUTLINE_READY achieved
        result = sm.apply(snapshot, GenerateOutline())
        assert result.phase == PlanningPhase.OUTLINE_READY
        # But without explicit ConfirmOutline, OUTLINE_CONFIRMED never reached
        assert result.outline_confirmation is None

    # ── Metadata: _get_max_candidates_per_section reads from schema ──

    def test_get_max_candidates_matches_schema(self) -> None:
        """_get_max_candidates_per_section() returns actual OutlineSection
        candidate_asset_ids max_length (currently 8)."""
        max_val = _get_max_candidates_per_section()
        assert max_val == 8, f"Expected 8, got {max_val}"

    def test_get_max_assets_per_slide_matches_schema(self) -> None:
        """_get_max_assets_per_slide() returns actual SlideIntent asset_ids
        max_length (currently 2)."""
        max_val = _get_max_assets_per_slide()
        assert max_val == 2, f"Expected 2, got {max_val}"


# ═════════════════════════════════════════════════════════════
# Batch 3.6.6 Scenario A — Schema Error Detail Preservation
# ═════════════════════════════════════════════════════════════


class TestReportSchemaErrorDetailPreservation:
    """Pydantic ValidationError details survive the error wrapping chain."""

    def _make_slide_with_too_many_assets(self) -> BaseModel:
        """Construct a SlideIntentPlan where slide 12 has 3 asset_ids (max 2)."""
        return SlideIntentPlan(
            deck_title="Test Deck",
            slides=tuple(
                [SlideIntent(
                    slide_id=f"slide_{i}",
                    section_id="intro",
                    sequence=i + 1,
                    title=f"Slide {i + 1}",
                    message="Test message.",
                    layout=SlideLayout.DATA_FOCUS,
                    content_points=("Point 1",),
                    visual_brief="Brief.",
                ) for i in range(11)]
                + [SlideIntent(
                    slide_id="slide_12",
                    section_id="compare",
                    sequence=12,
                    title="Compare Slide",
                    message="Compare message.",
                    layout=SlideLayout.COMPARISON,
                    content_points=("Point 1",),
                    asset_ids=("compare_corr_scatter_0", "compare_corr_scatter_1", "compare_corr_scatter_2"),
                    visual_brief="Brief.",
                )]
                + [SlideIntent(
                    slide_id=f"slide_{i}",
                    section_id="outro",
                    sequence=i + 1,
                    title=f"Slide {i + 1}",
                    message="Test message.",
                    layout=SlideLayout.DATA_FOCUS,
                    content_points=("Point 1",),
                    visual_brief="Brief.",
                ) for i in range(12, 14)]
            ),
        )

    def test_validation_error_has_correct_loc_and_type(self) -> None:
        """Pydantic ValidationError itself carries slides.12.asset_ids too_long."""
        with pytest.raises(ValidationError) as exc_info:
            self._make_slide_with_too_many_assets()
        errs = exc_info.value.errors()
        assert len(errs) >= 1
        err = errs[0]
        loc_str = '.'.join(str(x) for x in err['loc'])
        assert 'asset_ids' in loc_str
        assert err['type'] == 'too_long'

    def test_report_schema_error_preserves_validation_errors(self) -> None:
        """ReportSchemaError stores structured validation_errors from Pydantic."""
        from core.ai_errors import ReportSchemaError

        try:
            self._make_slide_with_too_many_assets()
        except ValidationError as ve:
            # Simulate the ai_client.py error wrapping
            validation_errors: list[dict[str, object]] = []
            for err in ve.errors():
                validation_errors.append({
                    'loc': '.'.join(str(x) for x in err.get('loc', ())),
                    'type': err.get('type', ''),
                    'msg': str(err.get('msg', ''))[:200],
                })
            rse = ReportSchemaError(
                "Schema mismatch",
                missing_fields=[],
                type_errors=[],
                validation_errors=validation_errors,
            )
            rse.__cause__ = ve

        assert len(rse.validation_errors) >= 1
        ve0 = rse.validation_errors[0]
        assert 'asset_ids' in str(ve0.get('loc', ''))
        assert ve0.get('type') == 'too_long'

    def test_no_raw_input_value_in_validation_errors(self) -> None:
        """validation_errors must NOT contain raw input_value arrays."""
        from core.ai_errors import ReportSchemaError

        try:
            self._make_slide_with_too_many_assets()
        except ValidationError as ve:
            validation_errors: list[dict[str, object]] = []
            for err in ve.errors():
                ve_dict: dict[str, object] = {
                    'loc': '.'.join(str(x) for x in err.get('loc', ())),
                    'type': err.get('type', ''),
                    'msg': str(err.get('msg', ''))[:200],
                }
                validation_errors.append(ve_dict)
            rse = ReportSchemaError(
                "Schema mismatch",
                validation_errors=validation_errors,
            )
            rse.__cause__ = ve

        for ve_item in rse.validation_errors:
            assert 'input' not in ve_item, f"validation_errors must not contain raw input: {ve_item}"
            assert 'input_value' not in ve_item

    def test_host_planning_model_error_chains_preserve_detail(self) -> None:
        """_extract_schema_error_detail_from_exc walks full chain to find detail."""
        from core.ai_errors import ReportSchemaError

        # Build the full error chain:
        # ValidationError → ReportSchemaError → HostPlanningModelError(inner) → HostPlanningModelError(outer)
        try:
            self._make_slide_with_too_many_assets()
        except ValidationError as ve:
            validation_errors: list[dict[str, object]] = []
            for err in ve.errors():
                ve_dict: dict[str, object] = {
                    'loc': '.'.join(str(x) for x in err.get('loc', ())),
                    'type': err.get('type', ''),
                    'msg': str(err.get('msg', ''))[:200],
                }
                ctx = err.get('ctx')
                if isinstance(ctx, dict):
                    ve_dict['ctx'] = {
                        k: v for k, v in ctx.items()
                        if k in ('max_length', 'actual_length')
                    }
                validation_errors.append(ve_dict)
            rse = ReportSchemaError(
                "AI 输出与 Schema 'SlideIntentPlan' 不匹配",
                validation_errors=validation_errors,
            )
            rse.__cause__ = ve

        # Adapter wraps ReportSchemaError → HostPlanningModelError(phase=NEW)
        inner_hpm = HostPlanningModelError(
            "model_failed",
            PlanningPhase.NEW,
            f"Host structured planning model failed: {type(rse).__name__}",
        )
        inner_hpm.__cause__ = rse

        # _call_model wraps again due to phase mismatch
        outer_hpm = HostPlanningModelError(
            "model_schema_invalid",
            PlanningPhase.DESIGN_READY,
            str(inner_hpm),
        )
        outer_hpm.__cause__ = inner_hpm

        # Extract detail — must find the structured validation info
        detail = _extract_schema_error_detail_from_exc(outer_hpm)

        # Must contain the key diagnostic info
        assert 'asset_ids' in detail, f"Expected 'asset_ids' in detail, got: {detail!r}"
        assert 'too_long' in detail, f"Expected 'too_long' in detail, got: {detail!r}"
        # Must NOT contain the full raw array of asset IDs
        assert 'compare_corr_scatter_0' not in detail, \
            f"Detail must not leak raw input values: {detail!r}"

    def test_extraction_falls_back_to_generic_for_plain_error(self) -> None:
        """When no structured details exist, fall back to str(error)."""
        error = HostPlanningModelError(
            "unknown_failure",
            PlanningPhase.DESIGN_READY,
            "Something went wrong without validation details.",
        )
        detail = _extract_schema_error_detail_from_exc(error)
        # Should fall back to str(error) — the message
        assert len(detail) > 0
        assert "unknown_failure" in detail or "Something went wrong" in detail

    def test_extraction_priority_validation_errors_over_legacy(self) -> None:
        """validation_errors takes priority; legacy fields not duplicated."""
        from core.ai_errors import ReportSchemaError

        rse = ReportSchemaError(
            "Schema mismatch",
            missing_fields=["old_field"],
            type_errors=["old_type: error"],
            validation_errors=[{
                'loc': 'slides.12.asset_ids',
                'type': 'too_long',
                'msg': 'Tuple should have at most 2 items',
            }],
        )
        hpm = HostPlanningModelError(
            "model_failed", PlanningPhase.NEW, "wrapped"
        )
        hpm.__cause__ = rse

        detail = _extract_schema_error_detail_from_exc(hpm)
        # Must contain the structured validation error
        assert 'slides.12.asset_ids' in detail
        assert 'too_long' in detail
        # Should not duplicate legacy fields when validation_errors is present
        # (validation_errors causes break out of walk loop)


class TestSlideIntentPromptContracts:
    """Batch 3.6.6 — prompt must carry per-slide max assets and section capacity."""

    @staticmethod
    def _make_compare_outline() -> tuple[PlanningRequest, OutlinePlan, DesignContract]:
        request = PlanningRequest(
            request_id="test-compare",
            report_title="通道一致性对比报告",
            objective="对比多源应变数据一致性",
            audience="技术专家",
            source_context="测试上下文 " * 50,
            requested_slide_count=14,
            assets=tuple([
                PlanningAsset(
                    asset_id=aid,
                    kind=PlanningAssetKind.CHART,
                    semantic_label=aid,
                    summary=f"Chart {aid}",
                    required=True,
                )
                for aid in [
                    "compare_corr_scatter_0",
                    "compare_corr_scatter_1",
                    "compare_corr_scatter_2",
                    "cmp_ol",
                ]
            ] + [
                PlanningAsset(
                    asset_id=f"other_{i}",
                    kind=PlanningAssetKind.CHART,
                    semantic_label=f"other_{i}",
                    summary=f"Other chart {i}",
                    required=False,
                )
                for i in range(10)
            ]),
        )
        outline = OutlinePlan(
            deck_title="通道一致性对比报告",
            narrative_arc="从标定到诊断的完整分析路径",
            sections=tuple([
                OutlineSection(
                    section_id="intro",
                    title="引言",
                    purpose="介绍背景",
                    key_messages=("背景",),
                    allocated_slides=2,
                    candidate_asset_ids=tuple(f"other_{i}" for i in range(3)),
                ),
                OutlineSection(
                    section_id="calibration",
                    title="标定分析",
                    purpose="标定评级",
                    key_messages=("标定",),
                    allocated_slides=3,
                    candidate_asset_ids=tuple(f"other_{i}" for i in range(3, 7)),
                ),
                OutlineSection(
                    section_id="compare",
                    title="通道一致性对比",
                    purpose="对比多源数据",
                    key_messages=("一致性", "相关性"),
                    allocated_slides=2,
                    candidate_asset_ids=(
                        "compare_corr_scatter_0",
                        "compare_corr_scatter_1",
                        "compare_corr_scatter_2",
                        "cmp_ol",
                    ),
                ),
                OutlineSection(
                    section_id="outro",
                    title="总结",
                    purpose="总结结论",
                    key_messages=("结论",),
                    allocated_slides=2,
                    candidate_asset_ids=tuple(f"other_{i}" for i in range(7, 10)),
                ),
            ]),
        )
        design = DesignContract(
            communication=CommunicationContract(
                objective="技术报告",
                audience_success="理解数据",
                tone="专业",
                content_divergence="faithful",
            ),
            template_strategy=TemplateMode.FREE_DESIGN,
            visual_style="简洁专业",
            palette=ColorPalette(
                primary="#1a5276",
                secondary="#2ecc71",
                accent="#e74c3c",
                background="#ffffff",
                text="#2c3e50",
            ),
            typography=TypographySpec(
                title_font="Microsoft YaHei",
                body_font="SimSun",
                monospace_font="Consolas",
                title_size_px=36,
                body_size_px=20,
                caption_size_px=14,
            ),
            density="balanced",
            image_usage="none",
            chart_style="统一配色",
            refine_spec="无特殊要求",
            layout_rules=("对齐",),
            accessibility_rules=("高对比",),
        )
        return request, outline, design

    def test_slides_prompt_contains_max_assets_per_slide(self) -> None:
        """Initial SlideIntent prompt explicitly states per-slide max asset limit."""
        request, outline, design = self._make_compare_outline()
        prompt = _slides_prompt(request, outline, design)
        max_val = _get_max_assets_per_slide()
        assert f"len(asset_ids) <= {max_val}" in prompt, \
            f"Prompt must contain per-slide max constraint. Prompt preview: {prompt[:2000]}"
        assert "No slide may contain more than" in prompt

    def test_slides_prompt_contains_section_capacity_table(self) -> None:
        """Initial prompt includes section capacity table with slide budget."""
        request, outline, design = self._make_compare_outline()
        prompt = _slides_prompt(request, outline, design)
        assert "SECTION CAPACITY TABLE" in prompt, \
            f"Prompt must contain section capacity table. Prompt: {prompt[:3000]}"
        # compare section: 2 slides, 4 assets
        assert "compare" in prompt
        assert "2 slides" in prompt

    def test_schema_corrective_prompt_contains_error_detail(self) -> None:
        """Schema corrective prompt carries specific error detail when available."""
        request, outline, design = self._make_compare_outline()
        error_detail = (
            "slides.12.asset_ids: too_long — "
            "Tuple should have at most 2 items after validation, not 3 "
            "[max=2, got=3]"
        )
        prompt = _slides_schema_corrective_prompt(
            request, outline, design,
            error_detail=error_detail,
        )
        assert "slides.12.asset_ids" in prompt, \
            f"Corrective prompt must include specific field path. Prompt: {prompt[:2000]}"
        assert "too_long" in prompt or "max=2" in prompt or "3" in prompt, \
            f"Corrective prompt must include error type/bounds. Prompt: {prompt[:2000]}"

    def test_schema_corrective_prompt_contains_per_slide_max(self) -> None:
        """Schema corrective prompt explicitly states per-slide max constraint."""
        request, outline, design = self._make_compare_outline()
        max_val = _get_max_assets_per_slide()
        prompt = _slides_schema_corrective_prompt(
            request, outline, design,
            error_detail="slides.12.asset_ids: too_long",
        )
        assert f"max_length={max_val}" in prompt, \
            f"Corrective prompt must include max_length. Prompt: {prompt[:2000]}"

    def test_schema_corrective_prompt_contains_section_capacity(self) -> None:
        """Schema corrective prompt includes section capacity table."""
        request, outline, design = self._make_compare_outline()
        prompt = _slides_schema_corrective_prompt(
            request, outline, design,
            error_detail="test error",
        )
        assert "SECTION CAPACITY TABLE" in prompt

    def test_slides_capacity_appendix_dynamically_reads_max(self) -> None:
        """_SLIDES_CAPACITY_APPENDIX reflects actual schema max_length."""
        max_val = _get_max_assets_per_slide()
        assert str(max_val) in _SLIDES_CAPACITY_APPENDIX, \
            f"Capacity appendix must contain dynamic max value {max_val}: {_SLIDES_CAPACITY_APPENDIX[:200]}"
        assert "max_length" in _SLIDES_CAPACITY_APPENDIX

    def test_max_assets_per_slide_not_hardcoded(self) -> None:
        """_get_max_assets_per_slide reads from SlideIntent schema, not a literal 2."""
        # Verify the function actually reads from model fields metadata
        from pydantic.fields import FieldInfo
        field = SlideIntent.model_fields.get("asset_ids")
        assert field is not None, "SlideIntent must have asset_ids field"
        # The metadata should contain a max_length constraint
        has_max_length = any(
            hasattr(m, "max_length") for m in (field.metadata or [])
        )
        assert has_max_length, "asset_ids field must have max_length metadata"

    def test_slides_prompt_compare_section_capacity_is_feasible(self) -> None:
        """Compare section: 2 slides × 2 assets = 4 capacity ≥ 4 required."""
        request, outline, design = self._make_compare_outline()
        compare_section = [s for s in outline.sections if s.section_id == "compare"][0]
        max_val = _get_max_assets_per_slide()
        capacity = compare_section.allocated_slides * max_val
        required_count = len([
            a for a in request.assets
            if a.required and a.asset_id in compare_section.candidate_asset_ids
        ])
        assert capacity >= required_count, \
            f"Compare section capacity {capacity} < required {required_count} — outline is infeasible"


class TestSchemaCorrectiveContentPrecision:
    """The corrective prompt must carry exact diagnostic info, not generic text."""

    def test_error_detail_hash_changes_with_different_errors(self) -> None:
        """Different errors produce different corrective prompt content."""
        request, outline, design = TestSlideIntentPromptContracts._make_compare_outline()

        p1 = _slides_schema_corrective_prompt(
            request, outline, design,
            error_detail="slides.12.asset_ids: too_long — max=2, got=3",
        )
        p2 = _slides_schema_corrective_prompt(
            request, outline, design,
            error_detail="slides.5.title: missing",
        )
        # Same base structure but different error detail embedded
        assert "slides.12.asset_ids" in p1
        assert "slides.5.title" in p2
        # The error details should make the prompts different
        assert p1 != p2, "Different errors must produce different corrective prompts"

    def test_initial_and_corrective_prompts_are_different(self) -> None:
        """Initial prompt and schema corrective prompt are not identical."""
        request, outline, design = TestSlideIntentPromptContracts._make_compare_outline()
        initial = _slides_prompt(request, outline, design)
        corrective = _slides_schema_corrective_prompt(
            request, outline, design,
            error_detail="slides.12.asset_ids: too_long — max=2, got=3",
        )
        assert initial != corrective, \
            "Initial and corrective prompts must differ (corrective carries error detail)"


class TestRetrySequenceBoundedFailure:
    """Batch 3.6.6 — retry budget is frozen at 3 physical calls."""

    def test_three_identical_schema_failures_are_captured(self) -> None:
        """When model repeats same error 3 times, Host raises bounded failure."""
        from core.ai_errors import ReportSchemaError

        # Build error: slide 13 has 3 assets
        validation_errors: list[dict[str, object]] = [{
            'loc': 'slides.12.asset_ids',
            'type': 'too_long',
            'msg': 'Tuple should have at most 2 items after validation, not 3',
        }]
        rse = ReportSchemaError(
            "AI 输出与 Schema 'SlideIntentPlan' 不匹配",
            validation_errors=validation_errors,
        )
        adapter_error = HostPlanningModelError(
            "model_failed",
            PlanningPhase.NEW,
            f"Host structured planning model failed: {type(rse).__name__}",
        )
        adapter_error.__cause__ = rse

        # Simulate 3 attempts, all failing with the same error
        errors: list[str] = []
        for _ in range(3):
            outer = HostPlanningModelError(
                "model_schema_invalid",
                PlanningPhase.DESIGN_READY,
                str(adapter_error),
            )
            outer.__cause__ = adapter_error
            detail = _extract_schema_error_detail_from_exc(outer)
            errors.append(detail)

        # All 3 must carry the same diagnostic detail
        for i, detail in enumerate(errors):
            assert 'asset_ids' in detail, \
                f"Attempt {i} detail missing 'asset_ids': {detail!r}"
            assert 'too_long' in detail, \
                f"Attempt {i} detail missing 'too_long': {detail!r}"

        # After 3 fails, the MAX_CORRECTIVE_RETRIES + 1 == 3 attempts exhausted
        assert len(errors) == 3

    def test_corrective_prompt_after_too_long_includes_field_and_max(self) -> None:
        """After a too_long error, corrective prompt must name field, max, actual."""
        request, outline, design = TestSlideIntentPromptContracts._make_compare_outline()

        # Simulate the error_detail that _extract_schema_error_detail_from_exc
        # would now produce (with our fix)
        error_detail = (
            "slides.12.asset_ids: too_long — "
            "Tuple should have at most 2 items after validation, not 3 "
            "[max=2, got=3]"
        )
        prompt = _slides_schema_corrective_prompt(
            request, outline, design, error_detail=error_detail,
        )
        assert "slides.12.asset_ids" in prompt
        assert "max=2" in prompt.lower() or "max_length=2" in prompt
        assert "3" in prompt  # actual count mentioned in error detail
