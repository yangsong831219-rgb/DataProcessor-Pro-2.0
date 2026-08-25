"""Forensic non-GUI test for Scenario A SlideIntent structured generation.

Reads the real diagnosis record, builds a PlanningRequest, generates outline+design
once (cached), then repeatedly tests ONLY SlideIntent generation with real DeepSeek.

Usage:
    python tests/forensic_slide_intent_test.py [--force-regenerate] [--skip-semantic]

Output: detailed [FORENSIC] attempt-by-attempt ledger to stdout.
"""

from __future__ import annotations

import json
import os
import sys
import uuid
from pathlib import Path

# Ensure the project root is on sys.path
_project_root = Path(__file__).resolve().parent.parent
if str(_project_root) not in sys.path:
    sys.path.insert(0, str(_project_root))


def _load_diagnosis_record() -> dict:
    """Find and load the real diagnosis record."""
    diagnosis_dir = _project_root / "wiki_vault" / "diagnoses"
    if diagnosis_dir.is_dir():
        for f in sorted(diagnosis_dir.glob("*.json"), reverse=True):
            try:
                with open(f, "r", encoding="utf-8") as fh:
                    return json.load(fh)
            except Exception:
                continue
    # Fallback: check config
    config_path = _project_root / "ai_models_config.json"
    if config_path.exists():
        with open(config_path, "r", encoding="utf-8") as fh:
            cfg = json.load(fh)
        diag = cfg.get("_diagnosis_record")
        if isinstance(diag, dict):
            return diag
    raise RuntimeError("No diagnosis record found")


def _build_source_context(diagnosis: dict) -> str:
    """Build source_context similar to main.py build_report_source_context."""
    parts: list[str] = []
    parts.append(f"诊断记录 ID: {diagnosis.get('record_id', 'unknown')}")
    parts.append(f"时间戳: {diagnosis.get('timestamp', 'unknown')}")
    parts.append(f"Schema 版本: {diagnosis.get('schema_version', 'unknown')}")

    # Chart manifest
    manifest = diagnosis.get("chart_manifest", [])
    if manifest:
        parts.append(f"\n图表清单 ({len(manifest)} 项):")
        for entry in manifest:
            if isinstance(entry, dict):
                parts.append(
                    f"  - {entry.get('chart_id', '?')}: "
                    f"{entry.get('semantic_label', '?')} [{entry.get('target', '?')}]"
                )
    # Try loading req files
    req_dir = _project_root
    for req_name in ["需求01.txt", "实验方案.txt"]:
        req_path = req_dir / req_name
        if req_path.is_file():
            try:
                content = req_path.read_text(encoding="utf-8")
                parts.append(f"\n{'='*40}\n{req_name}:\n{content[:5000]}")
            except Exception:
                pass
    return "\n".join(parts)


def _build_planning_request(diagnosis: dict) -> "PlanningRequest":
    """Build a PlanningRequest from the real diagnosis record."""
    from dp_engine.ppt_master_host.planning import (
        PlanningAsset,
        PlanningAssetKind,
        PlanningRequest,
        TemplateMode,
    )

    manifest = diagnosis.get("chart_manifest", [])
    asset_entries = []
    for entry in manifest:
        if not isinstance(entry, dict):
            continue
        chart_id = entry.get("chart_id", "")
        if not chart_id:
            continue
        asset_entries.append({
            "chart_id": chart_id,
            "kind": entry.get("kind", "chart"),
            "semantic_label": entry.get("semantic_label", chart_id),
            "summary": f"目标章节: {entry.get('target', '通用')}",
        })

    # Determine required assets (marked as required in manifest, or all if none marked)
    required_ids = {
        e["chart_id"] for e in asset_entries
        if e.get("kind") == "chart"  # all charts are required for diagnosis
    }

    planning_assets = tuple(
        PlanningAsset(
            asset_id=e["chart_id"],
            kind=PlanningAssetKind.CHART,
            semantic_label=e["semantic_label"][:200],
            summary=e["summary"][:1000],
            required=e["chart_id"] in required_ids,
        )
        for e in asset_entries
    )

    source_context = _build_source_context(diagnosis)
    slide_count = max(10, (len(planning_assets) + 1) // 2 + 2)
    report_title = "需求01 — 光纤光栅传感器专业诊断汇报"

    return PlanningRequest(
        request_id=f"ppt-forensic-{uuid.uuid4().hex[:24]}",
        report_title=report_title,
        objective="基于已加载的诊断事实形成可审核、可决策的专业技术汇报。",
        audience="项目技术负责人、试验人员与质量审核人员",
        source_context=source_context,
        requested_slide_count=slide_count,
        template_mode=TemplateMode.FREE_DESIGN,
        template_summary="",
        assets=planning_assets,
    )


def _get_or_create_snapshot_at_design_confirmed(
    request: "PlanningRequest",
    cache_path: Path,
    *,
    force_regenerate: bool = False,
) -> "PlanningSnapshot":
    """Load a cached DESIGN_CONFIRMED snapshot, or generate outline+design with real DeepSeek."""
    from dp_engine.ppt_master_host.planning import (
        ConfirmDesign,
        ConfirmOutline,
        GenerateDesign,
        GenerateOutline,
        HostAIClientPlanningAdapter,
        HostPlanningStateMachine,
        PlanningSnapshot,
    )

    if cache_path.exists() and not force_regenerate:
        print(f"[FORENSIC] Loading cached snapshot from {cache_path}")
        serialized = cache_path.read_text(encoding="utf-8")
        snapshot = PlanningSnapshot.model_validate_json(serialized)
        print(f"[FORENSIC]   phase = {snapshot.phase.value}")
        print(f"[FORENSIC]   outline sections = {len(snapshot.outline.sections) if snapshot.outline else 0}")
        print(f"[FORENSIC]   design = {'present' if snapshot.design else 'missing'}")
        return snapshot

    from core.ai_client import AIClient

    print("[FORENSIC] Generating Outline with real DeepSeek...")
    ai = AIClient.get_instance()
    adapter = HostAIClientPlanningAdapter(ai)
    machine = HostPlanningStateMachine(adapter)
    snapshot = machine.start(request)

    # Generate outline
    snapshot = machine.apply(snapshot, GenerateOutline())
    print(f"[FORENSIC]   Outline generated: {len(snapshot.outline.sections)} sections, "
          f"phase={snapshot.phase.value}")

    # Confirm outline → generate design
    snapshot = machine.apply(snapshot, ConfirmOutline())
    snapshot = machine.apply(snapshot, GenerateDesign())
    print(f"[FORENSIC]   Design generated: phase={snapshot.phase.value}")

    # Confirm design → ready for slides
    snapshot = machine.apply(snapshot, ConfirmDesign())
    print(f"[FORENSIC]   Snapshot at DESIGN_CONFIRMED, ready for SlideIntent")

    # Save for reuse
    serialized = snapshot.model_dump_json(exclude_none=True)
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    cache_path.write_text(serialized, encoding="utf-8")
    print(f"[FORENSIC]   Saved snapshot to {cache_path} ({len(serialized)} chars)")

    return snapshot


def run_slide_intent_forensic(
    snapshot: "PlanningSnapshot",
) -> int:
    """Run SlideIntent generation with forensic instrumentation.

    Returns:
        0 if SLIDES_READY reached, 1 if all attempts failed.
    """
    from dp_engine.ppt_master_host.planning import (
        GenerateSlides,
        HostAIClientPlanningAdapter,
        HostPlanningModelError,
        HostPlanningStateMachine,
    )
    from core.ai_client import AIClient

    ai = AIClient.get_instance()
    adapter = HostAIClientPlanningAdapter(ai)
    machine = HostPlanningStateMachine(adapter)

    print(f"\n{'='*70}")
    print(f"[FORENSIC] Starting SlideIntent generation (real DeepSeek)")
    print(f"[FORENSIC]   snapshot.phase = {snapshot.phase.value}")
    print(f"[FORENSIC]   snapshot.revision = {snapshot.revision}")
    print(f"[FORENSIC]   model = {adapter.identity}")
    print(f"{'='*70}")

    try:
        result = machine.apply(snapshot, GenerateSlides())
        print(f"\n[FORENSIC] 🠶 SUCCESS: phase = {result.phase.value}")
        slide_count = len(result.slides.slides) if result.slides else 0
        print(f"[FORENSIC] 🠶 slides count = {slide_count}")
        return 0
    except HostPlanningModelError as e:
        print(f"\n[FORENSIC] 🠶 FAILED: {e.code}")
        print(f"[FORENSIC] 🠶 message: {e}")
        return 1
    except Exception as e:
        print(f"\n[FORENSIC] 🠶 UNEXPECTED ERROR: {type(e).__name__}: {e}")
        import traceback
        traceback.print_exc()
        return 2


def _ensure_api_key() -> None:
    """Set DEEPSEEK_API_KEY from ai_models_config.json if not already in env."""
    import os as _os
    if _os.environ.get('DEEPSEEK_API_KEY'):
        return
    cfg_path = _project_root / 'ai_models_config.json'
    if cfg_path.exists():
        with open(cfg_path, encoding='utf-8') as _f:
            _cfg = json.load(_f)
        for _key in _cfg:
            if isinstance(_cfg[_key], dict) and 'api_key' in _cfg[_key]:
                _ak = _cfg[_key]['api_key']
                if _ak and _ak not in ('not-needed', ''):
                    _os.environ['DEEPSEEK_API_KEY'] = _ak
                    return


def main() -> None:
    import argparse

    _ensure_api_key()

    parser = argparse.ArgumentParser(description="SlideIntent forensic test")
    parser.add_argument(
        "--force-regenerate", action="store_true",
        help="Force regeneration of outline+design (real API calls)"
    )
    parser.add_argument(
        "--cache-dir", type=str, default=None,
        help="Directory for cached snapshots"
    )
    args = parser.parse_args()

    cache_dir = Path(args.cache_dir) if args.cache_dir else (_project_root / "tests" / ".forensic_cache")
    cache_path = cache_dir / "design_confirmed_snapshot.json"

    print("=" * 70)
    print("SlideIntent Structured Generation — Forensic Test")
    print("=" * 70)

    # Step 1: Load diagnosis record
    print("\n[FORENSIC] Step 1: Loading diagnosis record...")
    diagnosis = _load_diagnosis_record()
    print(f"[FORENSIC]   record_id = {diagnosis.get('record_id', '?')}")
    print(f"[FORENSIC]   chart_manifest entries = {len(diagnosis.get('chart_manifest', []))}")

    # Step 2: Build PlanningRequest
    print("\n[FORENSIC] Step 2: Building PlanningRequest...")
    request = _build_planning_request(diagnosis)
    print(f"[FORENSIC]   request_id = {request.request_id}")
    print(f"[FORENSIC]   requested_slide_count = {request.requested_slide_count}")
    print(f"[FORENSIC]   assets count = {len(request.assets)}")
    required_count = sum(1 for a in request.assets if a.required)
    print(f"[FORENSIC]   required assets = {required_count}")

    # Step 3: Get or create snapshot at DESIGN_CONFIRMED
    print("\n[FORENSIC] Step 3: Getting DESIGN_CONFIRMED snapshot...")
    snapshot = _get_or_create_snapshot_at_design_confirmed(
        request, cache_path, force_regenerate=args.force_regenerate,
    )

    # Step 4: Run SlideIntent generation
    print("\n[FORENSIC] Step 4: Running SlideIntent generation...")
    exit_code = run_slide_intent_forensic(snapshot)

    print(f"\n{'='*70}")
    print(f"[FORENSIC] Exit code: {exit_code}")
    print(f"{'='*70}")
    sys.exit(exit_code)


if __name__ == "__main__":
    main()
