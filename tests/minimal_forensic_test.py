# -*- coding: utf-8 -*-
"""Minimal real DeepSeek SlideIntent forensic test.

Uses a small hand-crafted PlanningRequest (5 assets, 3 sections, 6 slides)
to test each of the 3 retry attempts. Captures full forensic output.

Usage: python tests/minimal_forensic_test.py
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

# Force UTF-8 for forensic output on Windows
if hasattr(sys.stdout, 'buffer'):
    sys.stdout = open(sys.stdout.fileno(), mode='w', encoding='utf-8', buffering=1)

_project_root = Path(__file__).resolve().parent.parent
if str(_project_root) not in sys.path:
    sys.path.insert(0, str(_project_root))


def _ensure_api_key() -> None:
    """Set DEEPSEEK_API_KEY from ai_models_config.json if not already in env."""
    if os.environ.get('DEEPSEEK_API_KEY'):
        return
    cfg_path = _project_root / 'ai_models_config.json'
    if cfg_path.exists():
        with open(cfg_path, encoding='utf-8') as f:
            cfg = json.load(f)
        for key in cfg:
            if isinstance(cfg[key], dict) and 'api_key' in cfg[key]:
                ak = cfg[key]['api_key']
                if ak and ak not in ('not-needed', ''):
                    os.environ['DEEPSEEK_API_KEY'] = ak
                    return


def build_minimal_snapshot():
    """Build a minimal DESIGN_CONFIRMED snapshot with 5 assets, 3 sections, 6 slides."""
    from dp_engine.ppt_master_host.planning import (
        ColorPalette,
        CommunicationContract,
        ConfirmDesign,
        ConfirmOutline,
        DesignContract,
        GenerateDesign,
        GenerateOutline,
        HostAIClientPlanningAdapter,
        HostPlanningStateMachine,
        OutlinePlan,
        OutlineSection,
        PlanningAsset,
        PlanningAssetKind,
        PlanningPhase,
        PlanningRequest,
        PlanningSnapshot,
        PlanningConfirmation,
        ConfirmationStage,
        _model_fingerprint,
        TemplateMode,
        TypographySpec,
    )
    from core.ai_client import AIClient

    request = PlanningRequest(
        request_id='ppt-forensic-minimal',
        report_title='传感器标定验证报告',
        objective='验证传感器标定流程并评估测量精度',
        audience='试验工程师与质量审核人员',
        source_context=(
            '测试数据包含5组标定曲线：A1线性标定、B1迟滞环、C1线性标定、'
            'A2迟滞环、C2线性标定。每组包含原始数据和补偿后数据。'
        ),
        requested_slide_count=6,
        template_mode=TemplateMode.FREE_DESIGN,
        template_summary='',
        assets=tuple([
            PlanningAsset(
                asset_id='strain_calib_lin_A1',
                kind=PlanningAssetKind.CHART,
                semantic_label='A1 线性标定曲线',
                summary='传感器A1的线性标定结果',
                required=True,
            ),
            PlanningAsset(
                asset_id='phaseb_hyst_B1',
                kind=PlanningAssetKind.CHART,
                semantic_label='B1 迟滞环',
                summary='传感器B1的迟滞环特性',
                required=True,
            ),
            PlanningAsset(
                asset_id='strain_calib_lin_C1',
                kind=PlanningAssetKind.CHART,
                semantic_label='C1 线性标定曲线',
                summary='传感器C1的线性标定结果',
                required=True,
            ),
            PlanningAsset(
                asset_id='phaseb_hyst_A2',
                kind=PlanningAssetKind.CHART,
                semantic_label='A2 迟滞环',
                summary='传感器A2的迟滞环特性',
                required=True,
            ),
            PlanningAsset(
                asset_id='strain_calib_lin_C2',
                kind=PlanningAssetKind.CHART,
                semantic_label='C2 线性标定曲线',
                summary='传感器C2的线性标定结果',
                required=True,
            ),
        ]),
    )

    outline = OutlinePlan(
        deck_title='传感器标定验证报告',
        narrative_arc='从标定方法到精度评估，循序渐进验证传感器性能',
        sections=tuple([
            OutlineSection(
                section_id='sec_overview',
                title='标定概述',
                purpose='介绍标定目的和测试方法',
                key_messages=('标定是传感器精度保证的基础', '本报告涵盖3组传感器的标定验证'),
                allocated_slides=1,
                candidate_asset_ids=(),
            ),
            OutlineSection(
                section_id='sec_method',
                title='标定方法与数据',
                purpose='展示标定实验数据和初步分析',
                key_messages=('线性标定方法', '迟滞环测量方法'),
                allocated_slides=2,
                candidate_asset_ids=(
                    'strain_calib_lin_A1', 'strain_calib_lin_C1',
                    'phaseb_hyst_B1',
                ),
            ),
            OutlineSection(
                section_id='sec_result',
                title='精度评估',
                purpose='对标定结果进行精度评估和讨论',
                key_messages=('标定精度满足要求', '迟滞特性在可接受范围'),
                allocated_slides=3,
                candidate_asset_ids=(
                    'phaseb_hyst_A2', 'strain_calib_lin_C2',
                ),
            ),
        ]),
    )

    design = DesignContract(
        communication=CommunicationContract(
            objective='清晰传达标定验证结果',
            audience_success='工程师能据此判断传感器是否达标',
            tone='专业、客观',
            content_divergence='faithful',
        ),
        template_strategy=TemplateMode.FREE_DESIGN,
        visual_style='简洁专业的工程报告风格',
        palette=ColorPalette(
            primary='#1A5276',
            secondary='#2E86C1',
            accent='#E74C3C',
            background='#FFFFFF',
            text='#2C3E50',
        ),
        typography=TypographySpec(
            title_font='思源黑体',
            body_font='思源宋体',
            monospace_font='Consolas',
            title_size_px=36,
            body_size_px=20,
            caption_size_px=14,
        ),
        density='balanced',
        image_usage='source_only',
        chart_style='统一的坐标轴样式和配色方案',
        refine_spec='保持简洁，避免信息过载',
        layout_rules=('每页不超过5条要点', '图表占页面60%'),
        accessibility_rules=('字体不小于14px', '颜色对比度满足WCAG AA'),
    )

    ai = AIClient.get_instance()
    adapter = HostAIClientPlanningAdapter(ai)

    outline_confirm = PlanningConfirmation(
        stage=ConfirmationStage.OUTLINE,
        artifact_sha256=_model_fingerprint(outline),
    )
    design_confirm = PlanningConfirmation(
        stage=ConfirmationStage.DESIGN,
        artifact_sha256=_model_fingerprint(design),
    )

    snapshot = PlanningSnapshot(
        request=request,
        phase=PlanningPhase.DESIGN_CONFIRMED,
        revision=4,
        model_identity=adapter.identity,
        outline=outline,
        design=design,
        outline_confirmation=outline_confirm,
        design_confirmation=design_confirm,
    )

    return snapshot, adapter


def main() -> None:
    _ensure_api_key()

    from dp_engine.ppt_master_host.planning import (
        GenerateSlides,
        HostPlanningModelError,
        HostPlanningStateMachine,
    )
    from core.ai_client import AIClient

    print("=" * 70)
    print("Minimal SlideIntent Forensic Test - Real DeepSeek")
    print("=" * 70)

    snapshot, adapter = build_minimal_snapshot()
    print(f"\n[FORENSIC] Snapshot ready:")
    print(f"  phase = {snapshot.phase.value}")
    print(f"  assets = {len(snapshot.request.assets)}")
    print(f"  sections = {len(snapshot.outline.sections)}")
    print(f"  requested_slides = {snapshot.request.requested_slide_count}")

    machine = HostPlanningStateMachine(adapter)

    try:
        result = machine.apply(snapshot, GenerateSlides())
        print(f"\n*** SUCCESS: phase = {result.phase.value}")
        slide_count = len(result.slides.slides) if result.slides else 0
        print(f"  slides count = {slide_count}")
        sys.exit(0)
    except HostPlanningModelError as e:
        print(f"\n*** FAILED: {e.code}")
        print(f"  message: {e}")
        sys.exit(1)
    except Exception as e:
        print(f"\n*** UNEXPECTED: {type(e).__name__}: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(2)


if __name__ == '__main__':
    main()
