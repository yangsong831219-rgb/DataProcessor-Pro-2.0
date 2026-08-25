"""TEMPORARY BATCH 3.6.6 ACCEPTANCE CAPTURE — focused tests.

DELETE after Scenario A/B stable and final fresh E2E.

Covers all three checkpoint phases:
  DESIGN_CONFIRMED — after ConfirmDesign, before GenerateSlides
  SLIDES_READY   — after GenerateSlides succeeds
  PLAN_CONFIRMED — after ConfirmPlan succeeds

Plus: flag arming, write failures, serialize/restore roundtrip,
       DESIGN_CONFIRMED → GenerateSlides-only continuation,
       and normal-behavior-unchanged-with-flag-OFF.
"""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import TypeVar
from unittest import mock

import pytest
from pydantic import BaseModel

from dp_engine.ppt_master_host.acceptance_capture import (
    _ENV_FLAG,
    _reset_run_id,
    capture_at_phase,
    capture_if_enabled,
    startup_signal,
)
from dp_engine.ppt_master_host.planning import (
    ColorPalette,
    CommunicationContract,
    ConfirmDesign,
    ConfirmOutline,
    ConfirmPlan,
    DesignContract,
    GenerateDesign,
    GenerateOutline,
    GenerateSlides,
    HostPlanningStateMachine,
    OutlinePlan,
    OutlineSection,
    PlanningPhase,
    PlanningRequest,
    PlanningSnapshot,
    SlideIntent,
    SlideIntentPlan,
    SlideLayout,
    TemplateMode,
    TypographySpec,
    _model_fingerprint,
)


ModelT = TypeVar("ModelT", bound=BaseModel)


class ScriptedPlanningModel:
    """In-memory model that returns pre-baked responses for testing."""

    def __init__(self, outputs: list[BaseModel], identity: str = "test-model-v1") -> None:
        self._outputs = list(outputs)
        self._idx = 0
        self._identity = identity

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
        del prompt, system_prompt, max_tokens, max_schema_retries
        if self._idx >= len(self._outputs):
            raise RuntimeError("ScriptedPlanningModel exhausted")
        result = self._outputs[self._idx]
        self._idx += 1
        return schema.model_validate(result)


# ═══════════════════════════════════════════════════════════════════
# Shared fixtures
# ═══════════════════════════════════════════════════════════════════


def _minimal_outline() -> OutlinePlan:
    return OutlinePlan(
        deck_title="Test Report Deck",
        narrative_arc="Introduction to conclusion narrative",
        sections=(
            OutlineSection(
                section_id="s1",
                title="Introduction",
                purpose="Introduce the report topic",
                key_messages=("Overview of key findings",),
                allocated_slides=1,
            ),
            OutlineSection(
                section_id="s2",
                title="Data Analysis",
                purpose="Present analysis results",
                key_messages=("Key data insights",),
                allocated_slides=1,
            ),
            OutlineSection(
                section_id="s3",
                title="Conclusions",
                purpose="Summarize and recommend",
                key_messages=("Final recommendations",),
                allocated_slides=1,
            ),
        ),
    )


def _minimal_design() -> DesignContract:
    return DesignContract(
        communication=CommunicationContract(
            objective="Present findings clearly",
            audience_success="Audience understands key takeaways",
            tone="professional",
            content_divergence="faithful",
        ),
        template_strategy=TemplateMode.FREE_DESIGN,
        visual_style="Clean and modern with data-focused layouts",
        palette=ColorPalette(
            primary="#1A1A1A",
            secondary="#333333",
            accent="#0066CC",
            background="#FFFFFF",
            text="#1A1A1A",
        ),
        typography=TypographySpec(
            title_font="Arial",
            body_font="Arial",
            monospace_font="Consolas",
            title_size_px=36,
            body_size_px=20,
            caption_size_px=14,
        ),
        density="balanced",
        image_usage="none",
        chart_style="Clean matplotlib-style charts",
        refine_spec="Final polish for presentation quality",
        layout_rules=("Consistent margins", "Aligned elements"),
        accessibility_rules=("High contrast", "Readable font sizes"),
    )


def _minimal_slides() -> SlideIntentPlan:
    return SlideIntentPlan(
        deck_title="Test Report Deck",
        slides=(
            SlideIntent(
                slide_id="slide_1",
                section_id="s1",
                sequence=1,
                title="Title Slide",
                message="Welcome to the test report",
                layout=SlideLayout.TITLE,
                content_points=("Report overview",),
                visual_brief="Title page with branding",
            ),
            SlideIntent(
                slide_id="slide_2",
                section_id="s2",
                sequence=2,
                title="Data Analysis",
                message="Key data insights",
                layout=SlideLayout.DATA_FOCUS,
                content_points=("Chart analysis",),
                visual_brief="Data visualization focus",
            ),
            SlideIntent(
                slide_id="slide_3",
                section_id="s3",
                sequence=3,
                title="Conclusions",
                message="Summary of findings",
                layout=SlideLayout.CONCLUSION,
                content_points=("Key takeaways",),
                visual_brief="Summary with recommendations",
            ),
        ),
    )


def _minimal_request() -> PlanningRequest:
    return PlanningRequest(
        request_id="test-req-001",
        report_title="Test Acceptance Report",
        objective="Verify acceptance capture works correctly",
        audience="Test engineers",
        source_context='{"diagnosis_record_id": "test-001"}',
        requested_slide_count=3,
        template_mode=TemplateMode.FREE_DESIGN,
    )


def _build_plan_confirmed_snapshot(
    request: PlanningRequest | None = None,
    outline: OutlinePlan | None = None,
    design: DesignContract | None = None,
    slides: SlideIntentPlan | None = None,
) -> PlanningSnapshot:
    """Build a valid PLAN_CONFIRMED snapshot through the state machine."""
    req = request or _minimal_request()
    model = ScriptedPlanningModel([
        outline or _minimal_outline(),
        design or _minimal_design(),
        slides or _minimal_slides(),
    ])
    machine = HostPlanningStateMachine(model)
    snapshot = machine.start(req)
    snapshot = machine.apply(snapshot, GenerateOutline())
    snapshot = machine.apply(snapshot, ConfirmOutline())
    snapshot = machine.apply(snapshot, GenerateDesign())
    snapshot = machine.apply(snapshot, ConfirmDesign())
    snapshot = machine.apply(snapshot, GenerateSlides())
    snapshot = machine.apply(snapshot, ConfirmPlan())
    return snapshot


def _build_design_confirmed_snapshot(
    request: PlanningRequest | None = None,
    outline: OutlinePlan | None = None,
    design: DesignContract | None = None,
) -> PlanningSnapshot:
    """Build a valid DESIGN_CONFIRMED snapshot through the state machine."""
    req = request or _minimal_request()
    model = ScriptedPlanningModel([
        outline or _minimal_outline(),
        design or _minimal_design(),
    ])
    machine = HostPlanningStateMachine(model)
    snapshot = machine.start(req)
    snapshot = machine.apply(snapshot, GenerateOutline())
    snapshot = machine.apply(snapshot, ConfirmOutline())
    snapshot = machine.apply(snapshot, GenerateDesign())
    snapshot = machine.apply(snapshot, ConfirmDesign())
    return snapshot


def _build_slides_ready_snapshot(
    request: PlanningRequest | None = None,
    outline: OutlinePlan | None = None,
    design: DesignContract | None = None,
    slides: SlideIntentPlan | None = None,
) -> PlanningSnapshot:
    """Build a valid SLIDES_READY snapshot through the state machine."""
    req = request or _minimal_request()
    model = ScriptedPlanningModel([
        outline or _minimal_outline(),
        design or _minimal_design(),
        slides or _minimal_slides(),
    ])
    machine = HostPlanningStateMachine(model)
    snapshot = machine.start(req)
    snapshot = machine.apply(snapshot, GenerateOutline())
    snapshot = machine.apply(snapshot, ConfirmOutline())
    snapshot = machine.apply(snapshot, GenerateDesign())
    snapshot = machine.apply(snapshot, ConfirmDesign())
    snapshot = machine.apply(snapshot, GenerateSlides())
    return snapshot


class _FakeWorkflow:
    """Minimal fake workflow for capture_if_enabled testing."""

    def __init__(self, snapshot: PlanningSnapshot | None):
        self._snapshot = snapshot

    @property
    def snapshot(self) -> PlanningSnapshot | None:
        return self._snapshot


# ═══════════════════════════════════════════════════════════════════
# Test 1: flag OFF → no artifact (PLAN_CONFIRMED — existing)
# ═══════════════════════════════════════════════════════════════════

def test_flag_off_no_artifact(tmp_path: Path):
    """When DPP_BATCH_366_CAPTURE_PLAN is not set, capture is a no-op."""
    _reset_run_id()
    with mock.patch.dict(os.environ, {}, clear=True):
        os.environ.pop(_ENV_FLAG, None)
        snapshot = _build_plan_confirmed_snapshot()
        wf = _FakeWorkflow(snapshot)
        result = capture_if_enabled(wf, artifact_dir=tmp_path)
        assert result is None
        assert list(tmp_path.iterdir()) == []


# ═══════════════════════════════════════════════════════════════════
# Test 2: flag ON + phase != PLAN_CONFIRMED → no artifact (existing)
# ═══════════════════════════════════════════════════════════════════

def test_flag_on_wrong_phase_no_artifact(tmp_path: Path):
    """Flag ON but snapshot is not PLAN_CONFIRMED → skip."""
    _reset_run_id()
    with mock.patch.dict(os.environ, {_ENV_FLAG: "1"}):
        req = _minimal_request()
        machine = HostPlanningStateMachine(ScriptedPlanningModel([]))
        new_snapshot = machine.start(req)  # phase = NEW
        wf = _FakeWorkflow(new_snapshot)
        result = capture_if_enabled(wf, artifact_dir=tmp_path)
        assert result is None
        assert list(tmp_path.iterdir()) == []


# ═══════════════════════════════════════════════════════════════════
# Test 3: flag ON + PLAN_CONFIRMED → artifact written (existing)
# ═══════════════════════════════════════════════════════════════════

def test_flag_on_plan_confirmed_writes_artifact(tmp_path: Path):
    """Flag ON + PLAN_CONFIRMED → snapshot + metadata files written."""
    _reset_run_id()
    with mock.patch.dict(os.environ, {_ENV_FLAG: "1"}):
        snapshot = _build_plan_confirmed_snapshot()
        wf = _FakeWorkflow(snapshot)
        result = capture_if_enabled(wf, artifact_dir=tmp_path)
        assert result is not None
        assert result.exists()
        files = list(tmp_path.iterdir())
        # run_id/ subdirectory with files inside
        run_dirs = [d for d in files if d.is_dir()]
        assert len(run_dirs) == 1
        run_files = list(run_dirs[0].iterdir())
        assert len(run_files) == 2  # snapshot + metadata
        snapshot_files = [f for f in run_files if f.name.endswith(".snapshot.json")]
        meta_files = [f for f in run_files if f.name.endswith("_metadata.json")]
        assert len(snapshot_files) == 1
        assert len(meta_files) == 1


# ═══════════════════════════════════════════════════════════════════
# Test 4: serialize → restore identity PASS (PLAN_CONFIRMED — existing)
# ═══════════════════════════════════════════════════════════════════

def test_serialize_restore_roundtrip_identity():
    """Snapshot survives serialize → restore with canonical identity intact."""
    original = _build_plan_confirmed_snapshot()
    serialized = original.model_dump_json(exclude_none=True)
    restored = PlanningSnapshot.model_validate_json(serialized)

    assert restored.phase == PlanningPhase.PLAN_CONFIRMED
    assert restored.request.template_mode == original.request.template_mode
    assert restored.request.source_context == original.request.source_context
    assert _model_fingerprint(restored.outline) == _model_fingerprint(original.outline)
    assert _model_fingerprint(restored.design) == _model_fingerprint(original.design)
    assert _model_fingerprint(restored.slides) == _model_fingerprint(original.slides)
    assert restored.plan_confirmation is not None
    assert restored.plan_confirmation.stage.value == "plan"
    assert restored.plan_confirmation.actor == "user"
    assert restored.plan_confirmation.artifact_sha256 == _model_fingerprint(restored.slides)
    assert restored.revision == original.revision
    assert restored.model_identity == original.model_identity


# ═══════════════════════════════════════════════════════════════════
# Test 5: restored phase = PLAN_CONFIRMED (existing)
# ═══════════════════════════════════════════════════════════════════

def test_restored_phase_is_plan_confirmed():
    """Round-tripped snapshot explicitly has phase=PLAN_CONFIRMED."""
    original = _build_plan_confirmed_snapshot()
    serialized = original.model_dump_json(exclude_none=True)
    restored = PlanningSnapshot.model_validate_json(serialized)
    assert restored.phase == PlanningPhase.PLAN_CONFIRMED
    assert restored.ready_for_authoring is True


# ═══════════════════════════════════════════════════════════════════
# Test 6: restored snapshot passes production provider admission (existing)
# ═══════════════════════════════════════════════════════════════════

def test_restored_snapshot_passes_provider_admission():
    """Restored PLAN_CONFIRMED snapshot is accepted by the same validation
    that ControlledRunner uses — roundtrip doesn't break admission."""
    original = _build_plan_confirmed_snapshot()
    serialized = original.model_dump_json(exclude_none=True)
    restored = PlanningSnapshot.model_validate_json(serialized)

    re_validated = PlanningSnapshot.model_validate(
        restored.model_dump(mode="python")
    )
    assert re_validated.phase == PlanningPhase.PLAN_CONFIRMED
    assert re_validated.plan_confirmation is not None
    assert re_validated.outline is not None
    assert re_validated.design is not None
    assert re_validated.slides is not None
    assert re_validated.request is not None


# ═══════════════════════════════════════════════════════════════════
# Test 7: capture metadata contains no secrets (existing)
# ═══════════════════════════════════════════════════════════════════

def test_capture_metadata_no_secrets(tmp_path: Path):
    """Metadata file must not contain API keys, tokens, or secrets."""
    _reset_run_id()
    with mock.patch.dict(os.environ, {_ENV_FLAG: "1"}):
        snapshot = _build_plan_confirmed_snapshot()
        wf = _FakeWorkflow(snapshot)
        capture_if_enabled(wf, artifact_dir=tmp_path)

    run_dirs = [d for d in tmp_path.iterdir() if d.is_dir()]
    assert len(run_dirs) == 1
    meta_files = list(run_dirs[0].glob("*_metadata.json"))
    assert len(meta_files) == 1
    meta = json.loads(meta_files[0].read_text(encoding="utf-8"))

    forbidden = {"api_key", "token", "secret", "password", "credential", "authorization"}
    meta_str = json.dumps(meta).lower()
    for key in forbidden:
        assert key not in meta_str, f"Metadata contains forbidden key: {key}"

    snapshot_files = list(run_dirs[0].glob("*.snapshot.json"))
    snapshot_text = snapshot_files[0].read_text(encoding="utf-8").lower()
    for key in forbidden:
        assert key not in snapshot_text, f"Snapshot contains forbidden key: {key}"


# ═══════════════════════════════════════════════════════════════════
# Test 8: capture write failure explicit (existing)
# ═══════════════════════════════════════════════════════════════════

def test_capture_write_failure_raises(tmp_path: Path):
    """When artifact file write fails, capture raises OSError."""
    _reset_run_id()
    with mock.patch.dict(os.environ, {_ENV_FLAG: "1"}):
        snapshot = _build_plan_confirmed_snapshot()
        wf = _FakeWorkflow(snapshot)

        original_write_text = Path.write_text

        def _failing_write_text(self_path, *args, **kwargs):
            if str(self_path).startswith(str(tmp_path)):
                raise OSError("Simulated disk full")
            return original_write_text(self_path, *args, **kwargs)

        with mock.patch.object(Path, "write_text", _failing_write_text):
            with pytest.raises(OSError, match="Simulated disk full"):
                capture_if_enabled(wf, artifact_dir=tmp_path)


# ═══════════════════════════════════════════════════════════════════
# Test 9: normal GUI confirmation behavior unchanged (existing)
# ═══════════════════════════════════════════════════════════════════

def test_snapshot_unchanged_after_capture():
    """Capturing does not mutate the snapshot in any way."""
    _reset_run_id()
    with mock.patch.dict(os.environ, {_ENV_FLAG: "1"}):
        original = _build_plan_confirmed_snapshot()
        assert original.outline is not None
        assert original.design is not None
        assert original.slides is not None
        assert original.plan_confirmation is not None
        pre_phase = original.phase
        pre_outline_fp = _model_fingerprint(original.outline)
        pre_design_fp = _model_fingerprint(original.design)
        pre_slides_fp = _model_fingerprint(original.slides)
        pre_confirm_fp = original.plan_confirmation.artifact_sha256

        with tempfile.TemporaryDirectory() as tmpdir:
            wf = _FakeWorkflow(original)
            capture_if_enabled(wf, artifact_dir=Path(tmpdir))

        assert original.phase == pre_phase
        assert _model_fingerprint(original.outline) == pre_outline_fp
        assert _model_fingerprint(original.design) == pre_design_fp
        assert _model_fingerprint(original.slides) == pre_slides_fp
        assert original.plan_confirmation.artifact_sha256 == pre_confirm_fp


# ═══════════════════════════════════════════════════════════════════
# NEW TESTS — Batch 3.6.6 checkpoint expansion
# ═══════════════════════════════════════════════════════════════════

# ── Test 10: flag OFF → no DESIGN_CONFIRMED checkpoint ─────────────

def test_flag_off_no_design_confirmed_artifact(tmp_path: Path):
    """Flag OFF: capture_at_phase returns None regardless of phase."""
    _reset_run_id()
    with mock.patch.dict(os.environ, {}, clear=True):
        os.environ.pop(_ENV_FLAG, None)
        snapshot = _build_design_confirmed_snapshot()
        result = capture_at_phase(
            snapshot, PlanningPhase.DESIGN_CONFIRMED, artifact_root=tmp_path,
        )
        assert result is None
        assert list(tmp_path.iterdir()) == []


# ── Test 11: flag ON + DESIGN_CONFIRMED → artifact written ─────────

def test_flag_on_design_confirmed_writes_artifact(tmp_path: Path):
    """Flag ON: capture DESIGN_CONFIRMED snapshot to disk."""
    _reset_run_id()
    with mock.patch.dict(os.environ, {_ENV_FLAG: "1"}):
        snapshot = _build_design_confirmed_snapshot()
        assert snapshot.phase == PlanningPhase.DESIGN_CONFIRMED
        result = capture_at_phase(
            snapshot, PlanningPhase.DESIGN_CONFIRMED, artifact_root=tmp_path,
        )
        assert result is not None
        assert result.exists()

        run_dirs = [d for d in tmp_path.iterdir() if d.is_dir()]
        assert len(run_dirs) == 1
        run_files = list(run_dirs[0].iterdir())
        assert len(run_files) == 2  # snapshot + metadata
        snapshot_files = [
            f for f in run_files
            if f.name == "design_confirmed.snapshot.json"
        ]
        assert len(snapshot_files) == 1


# ── Test 12: flag ON + SLIDES_READY → artifact written ─────────────

def test_flag_on_slides_ready_writes_artifact(tmp_path: Path):
    """Flag ON: capture SLIDES_READY snapshot to disk."""
    _reset_run_id()
    with mock.patch.dict(os.environ, {_ENV_FLAG: "1"}):
        snapshot = _build_slides_ready_snapshot()
        assert snapshot.phase == PlanningPhase.SLIDES_READY
        result = capture_at_phase(
            snapshot, PlanningPhase.SLIDES_READY, artifact_root=tmp_path,
        )
        assert result is not None
        assert result.exists()

        run_dirs = [d for d in tmp_path.iterdir() if d.is_dir()]
        assert len(run_dirs) == 1
        snapshot_files = [
            f for f in run_dirs[0].iterdir()
            if f.name == "slides_ready.snapshot.json"
        ]
        assert len(snapshot_files) == 1


# ── Test 13: flag ON but phase mismatch → no artifact ──────────────

def test_flag_on_phase_mismatch_no_artifact(tmp_path: Path):
    """Flag ON but snapshot phase != requested capture phase → no artifact."""
    _reset_run_id()
    with mock.patch.dict(os.environ, {_ENV_FLAG: "1"}):
        # Build DESIGN_CONFIRMED but request SLIDES_READY capture
        snapshot = _build_design_confirmed_snapshot()
        result = capture_at_phase(
            snapshot, PlanningPhase.SLIDES_READY, artifact_root=tmp_path,
        )
        assert result is None
        assert list(tmp_path.iterdir()) == []


# ── Test 14: flag ON + DESIGN_READY → no DESIGN_CONFIRMED artifact ─

def test_flag_on_design_ready_no_checkpoint(tmp_path: Path):
    """Flag ON but phase is DESIGN_READY (not yet confirmed) → no capture."""
    _reset_run_id()
    with mock.patch.dict(os.environ, {_ENV_FLAG: "1"}):
        req = _minimal_request()
        model = ScriptedPlanningModel([
            _minimal_outline(),
            _minimal_design(),
        ])
        machine = HostPlanningStateMachine(model)
        snapshot = machine.start(req)
        snapshot = machine.apply(snapshot, GenerateOutline())
        snapshot = machine.apply(snapshot, ConfirmOutline())
        snapshot = machine.apply(snapshot, GenerateDesign())
        # Phase should be DESIGN_READY (design generated, not yet confirmed)
        assert snapshot.phase == PlanningPhase.DESIGN_READY

        # Try to capture at DESIGN_CONFIRMED — should fail (wrong phase)
        result = capture_at_phase(
            snapshot, PlanningPhase.DESIGN_CONFIRMED, artifact_root=tmp_path,
        )
        assert result is None


# ── Test 15: serialize/restore DESIGN_CONFIRMED identity ────────────

def test_serialize_restore_design_confirmed_identity():
    """DESIGN_CONFIRMED snapshot survives serialize→restore with identity intact."""
    original = _build_design_confirmed_snapshot()
    assert original.phase == PlanningPhase.DESIGN_CONFIRMED
    assert original.slides is None  # no slides yet
    assert original.plan_confirmation is None  # not yet plan-confirmed
    assert original.design_confirmation is not None

    serialized = original.model_dump_json(exclude_none=True)
    restored = PlanningSnapshot.model_validate_json(serialized)

    assert restored.phase == PlanningPhase.DESIGN_CONFIRMED
    assert restored.slides is None
    assert restored.plan_confirmation is None
    assert restored.design_confirmation is not None
    assert restored.design_confirmation.stage.value == "design"
    assert restored.design_confirmation.actor == "user"
    assert restored.request.template_mode == original.request.template_mode
    assert _model_fingerprint(restored.outline) == _model_fingerprint(original.outline)
    assert _model_fingerprint(restored.design) == _model_fingerprint(original.design)
    assert restored.revision == original.revision
    assert restored.model_identity == original.model_identity


# ── Test 16: serialize/restore SLIDES_READY identity ────────────────

def test_serialize_restore_slides_ready_identity():
    """SLIDES_READY snapshot survives serialize→restore with identity intact."""
    original = _build_slides_ready_snapshot()
    assert original.phase == PlanningPhase.SLIDES_READY
    assert original.slides is not None
    assert original.plan_confirmation is None
    assert original.design_confirmation is not None

    serialized = original.model_dump_json(exclude_none=True)
    restored = PlanningSnapshot.model_validate_json(serialized)

    assert restored.phase == PlanningPhase.SLIDES_READY
    assert restored.slides is not None
    assert restored.plan_confirmation is None
    assert _model_fingerprint(restored.outline) == _model_fingerprint(original.outline)
    assert _model_fingerprint(restored.design) == _model_fingerprint(original.design)
    assert _model_fingerprint(restored.slides) == _model_fingerprint(original.slides)
    assert restored.revision == original.revision
    assert restored.model_identity == original.model_identity


# ── Test 17: restored DESIGN_CONFIRMED → GenerateSlides legal ──────

def test_restored_design_confirmed_generate_slides_legal():
    """Restored DESIGN_CONFIRMED snapshot can legally transition to SLIDES_READY
    via GenerateSlides — no re-generation of Outline or Design."""
    original = _build_design_confirmed_snapshot()
    serialized = original.model_dump_json(exclude_none=True)
    restored = PlanningSnapshot.model_validate_json(serialized)

    # Verify this IS a DESIGN_CONFIRMED snapshot with human-confirmed design
    assert restored.phase == PlanningPhase.DESIGN_CONFIRMED
    assert restored.design_confirmation is not None
    assert restored.design_confirmation.actor == "user"

    # Create a NEW state machine with the same model identity
    slides_output = _minimal_slides()
    new_model = ScriptedPlanningModel(
        [slides_output],
        identity=original.model_identity,
    )
    new_machine = HostPlanningStateMachine(new_model)

    # Apply GenerateSlides — must NOT require GenerateOutline or GenerateDesign
    new_snapshot = new_machine.apply(restored, GenerateSlides())
    assert new_snapshot.phase == PlanningPhase.SLIDES_READY
    assert new_snapshot.slides is not None
    assert new_snapshot.slides.deck_title == slides_output.deck_title


# ── Test 18: restored DESIGN_CONFIRMED cannot skip to ConfirmPlan ──

def test_restored_design_confirmed_cannot_skip_to_confirm_plan():
    """Restored DESIGN_CONFIRMED → ConfirmPlan should fail (must go through
    GenerateSlides first)."""
    original = _build_design_confirmed_snapshot()
    serialized = original.model_dump_json(exclude_none=True)
    restored = PlanningSnapshot.model_validate_json(serialized)

    from dp_engine.ppt_master_host.planning import HostPlanningTransitionError

    new_model = ScriptedPlanningModel([], identity=original.model_identity)
    new_machine = HostPlanningStateMachine(new_model)

    with pytest.raises(HostPlanningTransitionError, match="slides_ready"):
        new_machine.apply(restored, ConfirmPlan())


# ── Test 19: startup signal ARMED ──────────────────────────────────

def test_startup_signal_armed():
    """When DPP_BATCH_366_CAPTURE_PLAN=1, startup_signal returns ARMED."""
    with mock.patch.dict(os.environ, {_ENV_FLAG: "1"}):
        signal = startup_signal()
        assert signal == "BATCH_366_ACCEPTANCE_CAPTURE=ARMED"


# ── Test 20: startup signal NOT ARMED ──────────────────────────────

def test_startup_signal_not_armed():
    """When flag is unset, startup_signal returns NOT_ARMED."""
    with mock.patch.dict(os.environ, {}, clear=True):
        os.environ.pop(_ENV_FLAG, None)
        signal = startup_signal()
        assert signal == "BATCH_366_ACCEPTANCE_CAPTURE=NOT_ARMED"


# ── Test 21: capture write failure explicit (DESIGN_CONFIRMED) ─────

def test_capture_write_failure_design_confirmed(tmp_path: Path):
    """OSError during DESIGN_CONFIRMED capture → raises OSError."""
    _reset_run_id()
    with mock.patch.dict(os.environ, {_ENV_FLAG: "1"}):
        snapshot = _build_design_confirmed_snapshot()

        original_write_text = Path.write_text

        def _failing_write_text(self_path, *args, **kwargs):
            if str(self_path).startswith(str(tmp_path)):
                raise OSError("Simulated write failure")
            return original_write_text(self_path, *args, **kwargs)

        with mock.patch.object(Path, "write_text", _failing_write_text):
            with pytest.raises(OSError, match="Simulated write failure"):
                capture_at_phase(
                    snapshot, PlanningPhase.DESIGN_CONFIRMED,
                    artifact_root=tmp_path,
                )


# ── Test 22: multiple captures in same run share run_id ────────────

def test_multiple_captures_same_run_id(tmp_path: Path):
    """DESIGN_CONFIRMED then SLIDES_READY in same process → same run_id dir."""
    _reset_run_id()
    with mock.patch.dict(os.environ, {_ENV_FLAG: "1"}):
        # First capture
        dc_snapshot = _build_design_confirmed_snapshot()
        result1 = capture_at_phase(
            dc_snapshot, PlanningPhase.DESIGN_CONFIRMED, artifact_root=tmp_path,
        )
        assert result1 is not None

        # Second capture (same process lifetime → same run_id)
        sr_snapshot = _build_slides_ready_snapshot()
        result2 = capture_at_phase(
            sr_snapshot, PlanningPhase.SLIDES_READY, artifact_root=tmp_path,
        )
        assert result2 is not None

        # Both should be in the same run_id directory
        run_dirs = [d for d in tmp_path.iterdir() if d.is_dir()]
        assert len(run_dirs) == 1
        run_files = list(run_dirs[0].iterdir())
        assert len(run_files) == 4  # 2 snapshots + 2 metadata


# ── Test 23: capture_at_phase with None snapshot → safe ────────────

def test_capture_at_phase_none_snapshot(tmp_path: Path):
    """Calling capture_at_phase with None snapshot returns None safely."""
    _reset_run_id()
    with mock.patch.dict(os.environ, {_ENV_FLAG: "1"}):
        result = capture_at_phase(
            None, PlanningPhase.DESIGN_CONFIRMED, artifact_root=tmp_path,  # type: ignore[arg-type]
        )
        assert result is None


# ── Test 24: capture metadata has source=real_gui_acceptance ───────

def test_capture_metadata_has_real_source_marker(tmp_path: Path):
    """Every captured metadata marks source=real_gui_acceptance."""
    _reset_run_id()
    with mock.patch.dict(os.environ, {_ENV_FLAG: "1"}):
        snapshot = _build_design_confirmed_snapshot()
        capture_at_phase(
            snapshot, PlanningPhase.DESIGN_CONFIRMED, artifact_root=tmp_path,
        )

        run_dirs = [d for d in tmp_path.iterdir() if d.is_dir()]
        meta_files = list(run_dirs[0].glob("*_metadata.json"))
        meta = json.loads(meta_files[0].read_text(encoding="utf-8"))
        assert meta["source"] == "real_gui_acceptance"


# ── Test 25: PLAN_CONFIRMED capture_at_phase works ─────────────────

def test_capture_at_phase_plan_confirmed(tmp_path: Path):
    """capture_at_phase with PLAN_CONFIRMED works the same as legacy
    capture_if_enabled."""
    _reset_run_id()
    with mock.patch.dict(os.environ, {_ENV_FLAG: "1"}):
        snapshot = _build_plan_confirmed_snapshot()
        result = capture_at_phase(
            snapshot, PlanningPhase.PLAN_CONFIRMED, artifact_root=tmp_path,
        )
        assert result is not None
        run_dirs = [d for d in tmp_path.iterdir() if d.is_dir()]
        snapshot_files = [
            f for f in run_dirs[0].iterdir()
            if f.name == "plan_confirmed.snapshot.json"
        ]
        assert len(snapshot_files) == 1


# ── Test 26: metadata contains planning_request_sha256 ─────────────

def test_metadata_contains_planning_request_sha256(tmp_path: Path):
    """Metadata includes planning_request_sha256 fingerprint."""
    _reset_run_id()
    with mock.patch.dict(os.environ, {_ENV_FLAG: "1"}):
        snapshot = _build_design_confirmed_snapshot()
        capture_at_phase(
            snapshot, PlanningPhase.DESIGN_CONFIRMED, artifact_root=tmp_path,
        )

        run_dirs = [d for d in tmp_path.iterdir() if d.is_dir()]
        meta_files = list(run_dirs[0].glob("*_metadata.json"))
        meta = json.loads(meta_files[0].read_text(encoding="utf-8"))
        assert "planning_request_sha256" in meta
        assert len(meta["planning_request_sha256"]) == 64
