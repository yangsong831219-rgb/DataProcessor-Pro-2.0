"""Non-GUI Outline-only retest for Batch 3.6.6 Scenario A — Dual-Capacity P0.

Uses the real DeepSeek V4 Pro backend with the real Scenario A diagnosis record
to generate a corrected OutlinePlan. Validates all dual-capacity constraints
and produces a Section Capacity Ledger.

Usage:
    python tests/outline_dual_capacity_retest.py

Output: Section Capacity Ledger → PASS or FAIL for each constraint.
"""

from __future__ import annotations

import json
import sys
import uuid
from pathlib import Path

# Ensure the project root is on sys.path
_project_root = Path(__file__).resolve().parent.parent
if str(_project_root) not in sys.path:
    sys.path.insert(0, str(_project_root))


def _find_diagnosis_record() -> Path:
    """Find the Scenario A diagnosis record."""
    candidates = [
        _project_root / "项目资料库" / "三组标定" / "数据" / "诊断记录"
        / "诊断记录_20260720_172143.json",
    ]
    # Also check wiki_vault
    wiki = _project_root / "wiki_vault" / "diagnoses"
    if wiki.is_dir():
        for f in sorted(wiki.glob("*.json"), reverse=True):
            candidates.append(f)
    for path in candidates:
        if path.is_file():
            return path
    raise FileNotFoundError("No diagnosis record found")


def _load_diagnosis() -> dict:
    path = _find_diagnosis_record()
    print(f"[RETEST] Using diagnosis: {path}")
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def _build_planning_request(diagnosis: dict) -> "PlanningRequest":
    """Build PlanningRequest from real diagnosis record."""
    from dp_engine.ppt_master_host.planning import (
        PlanningAsset,
        PlanningAssetKind,
        PlanningRequest,
        TemplateMode,
    )

    manifest = diagnosis.get("chart_manifest", [])
    if not manifest:
        raise RuntimeError("Diagnosis record has no chart_manifest")

    # Build assets from chart_manifest
    assets = []
    for entry in manifest:
        if not isinstance(entry, dict):
            continue
        chart_id = entry.get("chart_id", "")
        if not chart_id:
            continue
        # Determine if this chart is required for the report
        produced = entry.get("produced", False)
        report_include = entry.get("report_include", False)
        is_required = produced and report_include

        assets.append(
            PlanningAsset(
                asset_id=chart_id,
                kind=PlanningAssetKind.CHART,
                semantic_label=str(entry.get("title", chart_id))[:200],
                summary=f"模块: {entry.get('module', '未知')}"[:1000],
                required=is_required,
            )
        )

    required_count = sum(1 for a in assets if a.required)
    print(f"[RETEST] Assets: {len(assets)} total, {required_count} required "
          f"(produced=True AND report_include=True)")

    # Build source context
    source_parts = [
        f"诊断记录 ID: {diagnosis.get('record_id', 'unknown')}",
        f"时间戳: {diagnosis.get('timestamp', 'unknown')}",
        f"Schema 版本: {diagnosis.get('schema_version', 'unknown')}",
        f"后端: {diagnosis.get('backend', 'unknown')}",
        f"模型: {diagnosis.get('model', 'unknown')}",
    ]

    # Data source snapshot — could be list of strings
    snapshot_data = diagnosis.get("data_source_snapshot", [])
    if snapshot_data:
        source_parts.append("\n数据源选择:")
        if isinstance(snapshot_data, dict):
            for source_name, entries in snapshot_data.items():
                if isinstance(entries, list):
                    source_parts.append(f"  {source_name}: {len(entries)} 项")
        elif isinstance(snapshot_data, list):
            for item in snapshot_data:
                source_parts.append(f"  - {item}")

    # KB hits summary
    kb_hits = diagnosis.get("kb_hits", [])
    if kb_hits:
        source_parts.append(f"\n知识库命中 ({len(kb_hits)} 条):")
        for hit in kb_hits[:10]:
            if isinstance(hit, dict):
                source_parts.append(
                    f"  - [{hit.get('rule_id', '?')}] {hit.get('finding', '')[:200]}"
                )

    source_context = "\n".join(source_parts)
    slide_count = 14  # Frozen Scenario A slide count
    report_title = "需求01 — 光纤光栅传感器专业诊断汇报"

    return PlanningRequest(
        request_id=f"ppt-outline-retest-{uuid.uuid4().hex[:24]}",
        report_title=report_title,
        objective="基于已加载的诊断事实形成可审核、可决策的专业技术汇报。",
        audience="项目技术负责人、试验人员与质量审核人员",
        source_context=source_context,
        requested_slide_count=slide_count,
        template_mode=TemplateMode.FREE_DESIGN,
        template_summary="",
        assets=tuple(assets),
    )


def _print_section_capacity_ledger(request, outline) -> None:
    """Print dual-capacity ledger for each section."""
    from dp_engine.ppt_master_host.planning import (
        _get_max_assets_per_slide,
        _get_max_candidates_per_section,
    )

    max_candidates = _get_max_candidates_per_section()
    max_per_slide = _get_max_assets_per_slide()
    required_ids = {a.asset_id for a in request.assets if a.required}

    print(f"\n{'='*80}")
    print("SECTION CAPACITY LEDGER")
    print(f"{'='*80}")
    print(f"Max candidates/section: {max_candidates}")
    print(f"Max assets/slide:       {max_per_slide}")
    print(f"Total required assets:  {len(required_ids)}")
    print(f"Requested slides:       {request.requested_slide_count}")
    print()

    header = (
        f"{'Section ID':<24} {'Cand':>5} {'Req':>4} "
        f"{'Slides':>6} {'CandOK':>6} {'SlideOK':>7}"
    )
    print(header)
    print("-" * len(header))

    all_ok = True
    for section in outline.sections:
        cand_count = len(section.candidate_asset_ids)
        req_count = sum(1 for aid in section.candidate_asset_ids if aid in required_ids)
        cand_ok = "PASS" if cand_count <= max_candidates else "FAIL"
        slide_capacity = section.allocated_slides * max_per_slide
        slide_ok = "PASS" if req_count <= slide_capacity else "FAIL"

        if cand_ok == "FAIL" or slide_ok == "FAIL":
            all_ok = False

        print(
            f"{section.section_id:<24} {cand_count:>5} {req_count:>4} "
            f"{section.allocated_slides:>6} {cand_ok:>6} {slide_ok:>7}"
        )

    total_allocated = sum(s.allocated_slides for s in outline.sections)
    print("-" * len(header))
    print(f"{'TOTAL':<24} {'':>5} {len(required_ids):>4} {total_allocated:>6}")

    total_cands = sum(len(s.candidate_asset_ids) for s in outline.sections)
    planned_required = sum(
        1 for s in outline.sections
        for aid in s.candidate_asset_ids if aid in required_ids
    )
    print(f"\nTotal candidates across all sections: {total_cands}")
    print(f"Total required assets in outline:     {planned_required}")
    print(f"All required covered: {planned_required == len(required_ids)}")
    print(f"Total slides match:   {total_allocated == request.requested_slide_count}")
    print(f"\nAll sections feasible: {all_ok}")


def main() -> int:
    """Run the non-GUI outline retest."""
    from dp_engine.ppt_master_host.planning import (
        GenerateOutline,
        HostAIClientPlanningAdapter,
        HostPlanningModelError,
        HostPlanningStateMachine,
    )
    from core.ai_client import AIClient

    print("=" * 80)
    print("Batch 3.6.6 Scenario A — Outline Dual-Capacity Non-GUI Retest")
    print("=" * 80)

    # 1. Load diagnosis
    diagnosis = _load_diagnosis()
    print(f"[RETEST] Record ID: {diagnosis.get('record_id', 'unknown')}")
    print(f"[RETEST] Schema version: {diagnosis.get('schema_version', 'unknown')}")

    # 2. Build PlanningRequest
    request = _build_planning_request(diagnosis)
    print(f"[RETEST] PlanningRequest built:")
    print(f"  request_id: {request.request_id}")
    print(f"  assets: {len(request.assets)} total, "
          f"{sum(1 for a in request.assets if a.required)} required")
    print(f"  requested_slide_count: {request.requested_slide_count}")

    # 3. Check AI client
    ai = AIClient.get_instance()
    if not ai.is_available():
        print("[RETEST] ERROR: AI client not configured")
        return 2
    print(f"[RETEST] Model: {ai.backend}:{ai.model_name}")

    # 4. Generate Outline
    adapter = HostAIClientPlanningAdapter(ai)
    machine = HostPlanningStateMachine(adapter)
    snapshot = machine.start(request)

    print(f"\n[RETEST] Generating Outline with {adapter.identity}...")
    print(f"[RETEST] This may take 30-60 seconds...\n")

    try:
        snapshot = machine.apply(snapshot, GenerateOutline())
    except HostPlanningModelError as e:
        print(f"\n[RETEST] OUTLINE GENERATION FAILED")
        print(f"[RETEST] Error code: {e.code}")
        print(f"[RETEST] Error: {e}")
        return 1

    outline = snapshot.outline
    if outline is None:
        print("[RETEST] ERROR: No outline produced")
        return 1

    print(f"\n[RETEST] Outline generated successfully!")
    print(f"[RETEST] Phase: {snapshot.phase.value}")
    print(f"[RETEST] Deck title: {outline.deck_title}")
    print(f"[RETEST] Sections: {len(outline.sections)}")

    # 5. Print section capacity ledger
    _print_section_capacity_ledger(request, outline)

    # 6. Validate
    from dp_engine.ppt_master_host.planning import _validate_outline_for_request
    try:
        _validate_outline_for_request(request, outline)
        print("\n[RETEST] _validate_outline_for_request: PASS")
    except ValueError as e:
        print(f"\n[RETEST] _validate_outline_for_request: FAIL — {e}")
        return 1

    # 7. Per-section detail
    print(f"\n{'='*80}")
    print("PER-SECTION DETAIL")
    print(f"{'='*80}")
    for i, section in enumerate(outline.sections):
        print(f"\n  Section {i}: {section.section_id}")
        print(f"    Title: {section.title}")
        print(f"    Purpose: {section.purpose}")
        print(f"    Key messages: {section.key_messages}")
        print(f"    Allocated slides: {section.allocated_slides}")
        print(f"    Candidate assets: {len(section.candidate_asset_ids)}")
        for aid in section.candidate_asset_ids[:5]:
            print(f"      - {aid}")
        if len(section.candidate_asset_ids) > 5:
            print(f"      ... and {len(section.candidate_asset_ids) - 5} more")

    print(f"\n{'='*80}")
    print("RETEST RESULT: PASS — OUTLINE_READY")
    print(f"{'='*80}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
