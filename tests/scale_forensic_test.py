# -*- coding: utf-8 -*-
"""Scale test for DeepSeek SlideIntent generation.

Tests increasing complexity levels to find the threshold where
JSON generation starts failing.

Usage: python tests/scale_forensic_test.py [--level 1|2|3|4]
  Level 1:  5 assets,  6 slides (baseline - should pass)
  Level 2: 10 assets, 10 slides (medium)
  Level 3: 15 assets, 13 slides (significant)
  Level 4: 24 assets, 16 slides (near real Scenario A scale)
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

if hasattr(sys.stdout, 'buffer'):
    sys.stdout = open(sys.stdout.fileno(), mode='w', encoding='utf-8', buffering=1)

_project_root = Path(__file__).resolve().parent.parent
if str(_project_root) not in sys.path:
    sys.path.insert(0, str(_project_root))


def _ensure_api_key() -> None:
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


def build_scale_snapshot(level: int):
    """Build a snapshot at the given complexity level."""
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

    if level == 1:
        n_assets, n_slides, n_sections = 5, 6, 3
    elif level == 2:
        n_assets, n_slides, n_sections = 10, 10, 4
    elif level == 3:
        n_assets, n_slides, n_sections = 15, 13, 5
    else:
        n_assets, n_slides, n_sections = 24, 16, 5

    # Build assets
    kinds = ['strain_calib_lin', 'phaseb_hyst', 'phaseb_diagnostic',
             'corr_scatter', 'strain_compare']
    sections_labels = ['A1', 'B1', 'C1', 'A2', 'B2', 'C2']
    assets = []
    for i in range(n_assets):
        kind = kinds[i % len(kinds)]
        label = sections_labels[i % len(sections_labels)]
        asset_id = f'{kind}_{label}_{i}'
        assets.append(PlanningAsset(
            asset_id=asset_id,
            kind=PlanningAssetKind.CHART,
            semantic_label=f'{kind.replace("_", " ").title()} {label} #{i}',
            summary=f'Chart {asset_id} showing diagnostic data for sensor {label}',
            required=True,
        ))

    # Build sections with asset distribution
    sections = []
    assets_per_section = n_assets // n_sections
    remainder = n_assets % n_sections
    idx = 0
    for s in range(n_sections):
        n_sec_assets = assets_per_section + (1 if s < remainder else 0)
        sec_assets = tuple(a.asset_id for a in assets[idx:idx + n_sec_assets])
        idx += n_sec_assets
        slides_alloc = max(1, (n_slides * n_sec_assets) // n_assets) if n_sec_assets > 0 else 1
        sections.append(OutlineSection(
            section_id=f'sec_{s:02d}',
            title=f'Section {s+1}',
            purpose=f'Analysis of sensor group {s+1}',
            key_messages=(f'Key finding for group {s+1}', f'Secondary insight for group {s+1}'),
            allocated_slides=slides_alloc,
            candidate_asset_ids=sec_assets,
        ))

    # Normalize slide allocations to match requested
    total_alloc = sum(s.allocated_slides for s in sections)
    if total_alloc != n_slides:
        diff = n_slides - total_alloc
        # Add/subtract from last sections
        for i in range(abs(diff)):
            si = -(1 + i % n_sections)
            new_alloc = sections[si].allocated_slides + (1 if diff > 0 else -1)
            if new_alloc >= 1:
                sections[si] = OutlineSection(
                    section_id=sections[si].section_id,
                    title=sections[si].title,
                    purpose=sections[si].purpose,
                    key_messages=sections[si].key_messages,
                    allocated_slides=new_alloc,
                    candidate_asset_ids=sections[si].candidate_asset_ids,
                )

    request = PlanningRequest(
        request_id=f'ppt-forensic-scale-{level}',
        report_title=f'Scale Test Level {level} - Sensor Diagnostic Report',
        objective='Comprehensive sensor diagnostic analysis with full asset coverage',
        audience='Technical team lead and quality reviewers',
        source_context=(
            f'Scale test with {n_assets} chart assets across {n_sections} sections. '
            f'All {n_assets} assets are required and must be placed on exactly '
            f'{n_slides} slides. Each asset belongs to exactly one section.'
        ),
        requested_slide_count=n_slides,
        template_mode=TemplateMode.FREE_DESIGN,
        template_summary='',
        assets=tuple(assets),
    )

    outline = OutlinePlan(
        deck_title=f'Scale Test Level {level} - Sensor Diagnostic Report',
        narrative_arc=f'Comprehensive {n_sections}-section analysis covering {n_assets} chart assets',
        sections=tuple(sections),
    )

    design = DesignContract(
        communication=CommunicationContract(
            objective='Deliver clear sensor diagnostic conclusions',
            audience_success='Audience can make informed decisions',
            tone='Professional, technical',
            content_divergence='faithful',
        ),
        template_strategy=TemplateMode.FREE_DESIGN,
        visual_style='Clean technical report with data-first layout',
        palette=ColorPalette(
            primary='#1A5276', secondary='#2E86C1', accent='#E74C3C',
            background='#FFFFFF', text='#2C3E50',
        ),
        typography=TypographySpec(
            title_font='Source Han Sans', body_font='Source Han Serif',
            monospace_font='Consolas',
            title_size_px=36, body_size_px=20, caption_size_px=14,
        ),
        density='balanced',
        image_usage='source_only',
        chart_style='Unified axis styling and color scheme',
        refine_spec='Keep concise, avoid information overload',
        layout_rules=('Max 5 points per slide', 'Charts occupy 60% of slide area'),
        accessibility_rules=('Font >= 14px', 'WCAG AA contrast compliance'),
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


def main():
    import argparse
    _ensure_api_key()

    parser = argparse.ArgumentParser()
    parser.add_argument('--level', type=int, default=1, choices=[1, 2, 3, 4])
    parser.add_argument('--all', action='store_true', help='Run all levels')
    args = parser.parse_args()

    from dp_engine.ppt_master_host.planning import (
        GenerateSlides, HostPlanningModelError, HostPlanningStateMachine,
    )

    levels = [1, 2, 3, 4] if args.all else [args.level]
    results = {}

    for level in levels:
        print(f"\n{'#'*70}")
        print(f"# SCALE TEST LEVEL {level}")
        print(f"{'#'*70}")

        snapshot, adapter = build_scale_snapshot(level)
        n_assets = len(snapshot.request.assets)
        n_slides = snapshot.request.requested_slide_count
        n_sections = len(snapshot.outline.sections)
        print(f"[SCALE] assets={n_assets}, slides={n_slides}, sections={n_sections}")

        machine = HostPlanningStateMachine(adapter)
        try:
            result = machine.apply(snapshot, GenerateSlides())
            print(f"\n[SCALE] Level {level}: PASS - {result.phase.value}")
            results[level] = 'PASS'
        except HostPlanningModelError as e:
            print(f"\n[SCALE] Level {level}: FAIL - {e.code}")
            results[level] = f'FAIL: {e.code}'
        except Exception as e:
            print(f"\n[SCALE] Level {level}: ERROR - {type(e).__name__}: {e}")
            results[level] = f'ERROR: {type(e).__name__}'

    print(f"\n{'='*70}")
    print("SCALE TEST SUMMARY")
    print(f"{'='*70}")
    for level, result in results.items():
        print(f"  Level {level}: {result}")

    all_pass = all(r == 'PASS' for r in results.values())
    sys.exit(0 if all_pass else 1)


if __name__ == '__main__':
    main()
