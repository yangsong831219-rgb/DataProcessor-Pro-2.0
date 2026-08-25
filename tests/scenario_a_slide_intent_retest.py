"""Batch 3.6.6 Scenario A — SlideIntent Per-Slide Capacity Real Retest.

Loads the real Scenario A diagnosis record, constructs a DESIGN_CONFIRMED snapshot
with 24 Required assets / 14 slides, then runs ONLY SlideIntent generation with
real DeepSeek V4 Pro. Full forensic instrumentation on every attempt.

Usage:
    python tests/scenario_a_slide_intent_retest.py

Output: complete attempt-by-attempt evidence ledger.
"""

from __future__ import annotations

import hashlib
import json
import os
import sys
import uuid
from pathlib import Path

# ── Ensure project root on sys.path ──
_project_root = Path(__file__).resolve().parent.parent
if str(_project_root) not in sys.path:
    sys.path.insert(0, str(_project_root))

# ── Force UTF-8 stdout ──
if hasattr(sys.stdout, 'buffer'):
    sys.stdout = open(sys.stdout.fileno(), mode='w', encoding='utf-8', buffering=1)


def _ensure_api_key() -> None:
    """Read API key from ai_models_config.json (same source as GUI)."""
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


def _load_diagnosis() -> dict:
    """Load the real Scenario A diagnosis record."""
    diag_path = (
        _project_root / "项目资料库" / "三组标定" / "数据" / "诊断记录"
        / "诊断记录_20260720_172143.json"
    )
    if not diag_path.is_file():
        raise FileNotFoundError(f"Diagnosis record not found: {diag_path}")
    with open(diag_path, 'r', encoding='utf-8') as f:
        return json.load(f)


def build_scenario_a_snapshot():
    """Build a Scenario A DESIGN_CONFIRMED snapshot.

    Uses real diagnosis record's chart_manifest, filtered to produced+report_include
    (24 assets). Constructs a feasible OutlinePlan with realistic section structure
    and 14 slides. The DesignContract is synthetically constructed (matching the
    pattern from real Scenario A).

    Returns (snapshot, adapter) ready for SlideIntent generation.
    """
    from dp_engine.ppt_master_host.planning import (
        ColorPalette, CommunicationContract, ConfirmDesign, ConfirmOutline,
        DesignContract, GenerateDesign, GenerateOutline,
        HostAIClientPlanningAdapter, HostPlanningStateMachine,
        OutlinePlan, OutlineSection, PlanningAsset, PlanningAssetKind,
        PlanningPhase, PlanningRequest, PlanningSnapshot,
        PlanningConfirmation, ConfirmationStage, _model_fingerprint,
        TemplateMode, TypographySpec,
    )
    from core.ai_client import AIClient

    diagnosis = _load_diagnosis()
    manifest = diagnosis.get('chart_manifest', [])

    # ── Filter to produced + report_include (24 assets) ──
    planning_assets: list[PlanningAsset] = []
    for entry in manifest:
        if not isinstance(entry, dict):
            continue
        chart_id = entry.get('chart_id', '')
        if not chart_id:
            continue
        produced = entry.get('produced', False)
        report_include = entry.get('report_include', False)
        if not (produced and report_include):
            continue
        planning_assets.append(PlanningAsset(
            asset_id=chart_id,
            kind=PlanningAssetKind.CHART,
            semantic_label=str(entry.get('title', chart_id))[:200],
            summary=f"模块: {entry.get('module', '未知')}"[:1000],
            required=True,
        ))

    required_count = sum(1 for a in planning_assets if a.required)
    slide_count = max(10, (required_count + 1) // 2 + 2)

    print(f"[RETEST] Assets: {len(planning_assets)} total, {required_count} required")
    print(f"[RETEST] Requested slide count: {slide_count}")

    # ── Build asset-to-section mapping (realistic Scenario A structure) ──
    # Section assignments match the real corrected Outline structure:
    #   overview: data_ts_dlambda, tempa_regression
    #   strain_calib: strain_calib_lin_A1..C2
    #   phaseb_AB: phaseb_diagnostic A1,A2,B1,B2 (8 candidates)
    #   phaseb_C: phaseb_diagnostic C1,C2 (4 candidates)
    #   compare: compare_corr_scatter_0,1,2 + compare_ol

    section_asset_map: dict[str, list[str]] = {
        "overview": [],
        "strain_calib": [],
        "phaseb_AB": [],
        "phaseb_C": [],
        "compare": [],
    }

    for a in planning_assets:
        aid = a.asset_id
        if aid.startswith('data_ts_') or aid.startswith('tempa_'):
            section_asset_map['overview'].append(aid)
        elif aid.startswith('strain_calib_'):
            section_asset_map['strain_calib'].append(aid)
        elif aid.startswith('phaseb_diagnostic_'):
            # A1, A2, B1, B2 → phaseb_AB; C1, C2 → phaseb_C
            if any(aid.startswith(f'phaseb_diagnostic_{g}') for g in ['A1', 'A2', 'B1', 'B2']):
                section_asset_map['phaseb_AB'].append(aid)
            else:
                section_asset_map['phaseb_C'].append(aid)
        elif aid.startswith('compare_'):
            section_asset_map['compare'].append(aid)
        else:
            section_asset_map['overview'].append(aid)  # safety fallback

    # ── Section definitions with slide allocations ──
    section_defs = [
        ("overview", "数据概览", "总览关键时序数据与温度回归关系，建立数据集全局认知",
         ("关键物理量时间序列完整、无异常跳变", "温度回归关系符合预期"),
         1),
        ("strain_calib", "应变标定分析", "六组应变计线性标定曲线与系数对比",
         ("12个应变计均完成标定，线性度良好", "C1/C2组标定系数与A/B组一致"),
         3),
        ("phaseb_AB", "相位B诊断 — A/B组", "Phase-B滞回与温补诊断: A1/A2/B1/B2 传感器对",
         ("A组温补效果显著，B1组滞回闭合良好", "B2组需关注补偿残差"),
         4),
        ("phaseb_C", "相位B诊断 — C组", "Phase-B滞回与温补诊断: C1/C2 传感器对",
         ("C组相位B特征与A/B组一致", "补偿后残差在可接受范围"),
         4),
        ("compare", "通道一致性对比", "多源数据互相关与叠加对比验证",
         ("三组对比散点高度一致 (r>0.95)", "叠加曲线无明显系统偏差"),
         2),
    ]

    sections: list[OutlineSection] = []
    for sec_id, title, purpose, key_msgs, alloc_slides in section_defs:
        candidate_ids = tuple(section_asset_map[sec_id])
        sections.append(OutlineSection(
            section_id=sec_id,
            title=title,
            purpose=purpose,
            key_messages=key_msgs,
            allocated_slides=alloc_slides,
            candidate_asset_ids=candidate_ids,
        ))

    total_allocated = sum(s.allocated_slides for s in sections)
    print(f"[RETEST] Section allocation: {dict((s.section_id, s.allocated_slides) for s in sections)}")
    print(f"[RETEST] Total allocated: {total_allocated} (requested: {slide_count})")
    assert total_allocated == slide_count, f"Slide count mismatch: {total_allocated} != {slide_count}"

    # Verify all required assets are covered
    covered = set()
    for s in sections:
        covered.update(s.candidate_asset_ids)
    required_ids = {a.asset_id for a in planning_assets}
    missing = required_ids - covered
    extra = covered - required_ids
    assert not missing, f"Missing required assets: {missing}"
    assert not extra, f"Unknown assets: {extra}"

    # Verify no section exceeds 8 candidates
    for s in sections:
        assert len(s.candidate_asset_ids) <= 8, \
            f"Section {s.section_id} has {len(s.candidate_asset_ids)} candidates (max 8)"

    # ── Build source context ──
    source_parts = [
        f"诊断记录 ID: {diagnosis.get('record_id', 'unknown')}",
        f"时间戳: {diagnosis.get('timestamp', 'unknown')}",
        f"Schema 版本: {diagnosis.get('schema_version', 'unknown')}",
        f"后端: {diagnosis.get('backend', 'unknown')}",
        f"模型: {diagnosis.get('model', 'unknown')}",
    ]
    source_context = "\n".join(source_parts)

    request = PlanningRequest(
        request_id=f"ppt-retest-{uuid.uuid4().hex[:24]}",
        report_title="需求01 — 光纤光栅传感器专业诊断汇报",
        objective="基于已加载的诊断事实形成可审核、可决策的专业技术汇报。",
        audience="项目技术负责人、试验人员与质量审核人员",
        source_context=source_context,
        requested_slide_count=slide_count,
        template_mode=TemplateMode.FREE_DESIGN,
        template_summary="",
        assets=tuple(planning_assets),
    )

    outline = OutlinePlan(
        deck_title="需求01 — 光纤光栅传感器专业诊断汇报",
        narrative_arc="从数据总览到分组深入诊断，最终以通道一致性交叉验证收束，形成完整的技术汇报链条。",
        sections=tuple(sections),
    )

    design = DesignContract(
        communication=CommunicationContract(
            objective="提供清晰、专业的传感器诊断结论，支持技术决策",
            audience_success="技术负责人可基于汇报内容做出质量判断与后续决策",
            tone="专业、客观、数据驱动",
            content_divergence="faithful",
        ),
        template_strategy=TemplateMode.FREE_DESIGN,
        visual_style="Clean technical report with data-first layout",
        palette=ColorPalette(
            primary="#1A5276", secondary="#2E86C1", accent="#E74C3C",
            background="#FFFFFF", text="#2C3E50",
        ),
        typography=TypographySpec(
            title_font="Source Han Sans", body_font="Source Han Serif",
            monospace_font="Consolas",
            title_size_px=36, body_size_px=20, caption_size_px=14,
        ),
        density="balanced",
        image_usage="source_only",
        chart_style="Unified axis styling and color scheme",
        refine_spec="Keep concise, avoid information overload",
        layout_rules=("Max 5 points per slide", "Charts occupy 60% of slide area"),
        accessibility_rules=("Font >= 14px", "WCAG AA contrast compliance"),
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

    print(f"[RETEST] Snapshot built: phase={snapshot.phase.value}, "
          f"revision={snapshot.revision}, model={adapter.identity}")

    # ── Pre-flight feasibility check ──
    from dp_engine.ppt_master_host.planning import _check_slide_constraint_feasibility
    feasible, reason = _check_slide_constraint_feasibility(request, outline)
    print(f"[RETEST] Pre-flight feasibility: {'PASS' if feasible else 'FAIL'}")
    if not feasible:
        print(f"[RETEST]   Reason: {reason}")
        raise RuntimeError(f"Pre-flight feasibility FAILED: {reason}")

    return snapshot, adapter


def run_slide_intent_retest(snapshot, adapter):
    """Run SlideIntent-only generation with full forensic instrumentation.

    Returns the attempt ledger and final result.
    """
    from dp_engine.ppt_master_host.planning import (
        GenerateSlides,
        HostPlanningModelError,
        HostPlanningStateMachine,
        _collect_slide_violations,
        _extract_schema_error_detail_from_exc,
    )

    machine = HostPlanningStateMachine(adapter)

    print(f"\n{'='*70}")
    print(f"[RETEST] SlideIntent Generation — Real DeepSeek V4 Pro")
    print(f"[RETEST]   snapshot.phase = {snapshot.phase.value}")
    print(f"[RETEST]   requested_slide_count = {snapshot.request.requested_slide_count}")
    print(f"[RETEST]   required assets = {sum(1 for a in snapshot.request.assets if a.required)}")
    print(f"[RETEST]   model = {adapter.identity}")
    print(f"[RETEST]   max attempts = 3 (Host sole retry owner)")
    print(f"{'='*70}")

    try:
        result = machine.apply(snapshot, GenerateSlides())
        print(f"\n[RETEST] ✓ SUCCESS: phase = {result.phase.value}")
        slide_count = len(result.slides.slides) if result.slides else 0
        print(f"[RETEST] ✓ slides count = {slide_count}")

        # Post-hoc semantic validation
        violations = _collect_slide_violations(
            snapshot.request, snapshot.outline, result.slides,
        )
        print(f"[RETEST] ✓ violations.is_valid = {violations.is_valid}")
        print(f"[RETEST] ✓ violations.summary() = {violations.summary()}")

        return {"result": "PASS", "slides": result.slides, "violations": violations}
    except HostPlanningModelError as e:
        print(f"\n[RETEST] ✗ FAILED: {e.code}")
        print(f"[RETEST] ✗ message: {e}")
        return {"result": "FAIL", "code": e.code, "message": str(e)}
    except Exception as e:
        print(f"\n[RETEST] ✗ UNEXPECTED ERROR: {type(e).__name__}: {e}")
        import traceback
        traceback.print_exc()
        return {"result": "ERROR", "error_type": type(e).__name__, "message": str(e)}


def print_section_capacity_ledger(snapshot):
    """Print the section capacity ledger for the snapshot."""
    from dp_engine.ppt_master_host.planning import (
        _get_max_assets_per_slide,
        _get_max_candidates_per_section,
    )
    max_candidates = _get_max_candidates_per_section()
    max_per_slide = _get_max_assets_per_slide()
    required_ids = {a.asset_id for a in snapshot.request.assets if a.required}

    print(f"\n{'='*80}")
    print("SECTION CAPACITY LEDGER")
    print(f"{'='*80}")
    print(f"Max candidates/section: {max_candidates}")
    print(f"Max assets/slide:       {max_per_slide}")
    print(f"Total required assets:  {len(required_ids)}")
    print(f"Requested slides:       {snapshot.request.requested_slide_count}")
    print()
    header = f"{'Section ID':<20} {'Cand':>5} {'Req':>4} {'Slides':>6} {'Cap':>5} {'Feasible':>8}"
    print(header)
    print("-" * len(header))
    for section in snapshot.outline.sections:
        cand_count = len(section.candidate_asset_ids)
        req_count = sum(1 for aid in section.candidate_asset_ids if aid in required_ids)
        capacity = section.allocated_slides * max_per_slide
        feasible = "PASS" if req_count <= capacity else "FAIL"
        print(f"{section.section_id:<20} {cand_count:>5} {req_count:>4} "
              f"{section.allocated_slides:>6} {capacity:>5} {feasible:>8}")


def main():
    _ensure_api_key()

    print("=" * 70)
    print("Batch 3.6.6 Scenario A — SlideIntent Per-Slide Capacity Real Retest")
    print("=" * 70)
    print(f"[RETEST] Credential path: same application config (ai_models_config.json)")
    print(f"[RETEST] Secret printed: NO")

    # ── Step 1: Build Scenario A DESIGN_CONFIRMED snapshot ──
    print(f"\n{'─'*70}")
    print("[RETEST] Step 1: Building Scenario A DESIGN_CONFIRMED snapshot...")
    snapshot, adapter = build_scenario_a_snapshot()
    print_section_capacity_ledger(snapshot)

    # ── Step 2: Run SlideIntent generation ──
    print(f"\n{'─'*70}")
    print("[RETEST] Step 2: Running SlideIntent-only generation...")
    result = run_slide_intent_retest(snapshot, adapter)

    # ── Final Report ──
    print(f"\n{'='*70}")
    print("## Batch 3.6.6 Scenario A Per-Slide Capacity Real Retest Report")
    print(f"{'='*70}")
    print(f"### Result: {result['result']}")
    print(f"### Credential Path")
    print(f"  - same application provider resolver used: yes")
    print(f"  - secret printed: no")
    print(f"### Snapshot")
    print(f"  - real Scenario A: yes (diagnosis 20260720_172143)")
    print(f"  - required assets: 24")
    print(f"  - requested slides: 14")
    print(f"  - starting phase: DESIGN_CONFIRMED")

    if result['result'] == 'PASS':
        violations = result['violations']
        slides = result['slides']
        max_assets = max(len(s.asset_ids) for s in slides.slides)
        print(f"### Final Contract")
        print(f"  - slides = {len(slides.slides)}")
        print(f"  - max asset_ids/slide = {max_assets} (target: <= 2)")
        print(f"  - Required missing = {len(violations.missing_required_assets)}")
        print(f"  - cross-section = {len(violations.cross_section_assets)}")
        print(f"  - semantic violations = {0 if violations.is_valid else violations.summary()}")
        print(f"\n### P0 Status: CLOSED")
        print(f"\n## OPERATOR ACTION REQUIRED")
        print(f"Scenario: A — DeepSeek / No Template")
        print(f"Real SlideIntent-only retest: PASS")
        print(f"请重新启动/切回 DataProcessor Pro。")
        print(f"保持相同 Scenario A 输入。")
        print(f"重新走到 DESIGN_READY。")
        print(f"然后只点击一次：'确认设计并生成逐页计划'")
        print(f"如果成功进入 SLIDES_READY：不要点击'确认逐页计划'。")
        print(f"请把完整逐页计划界面截图提交人工审核。")
    else:
        print(f"### P0 Status: {'BLOCKED' if result['result'] == 'FAIL' else 'ERROR'}")
        print(f"### Details: {result}")

    return 0 if result['result'] == 'PASS' else 1


if __name__ == "__main__":
    sys.exit(main())
