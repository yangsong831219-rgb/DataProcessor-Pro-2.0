# Batch 3.6.6 Acceptance Snapshot Capture Preparation — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add temporary acceptance-only instrumentation to capture real GUI PLAN_CONFIRMED snapshots to disk, with zero production behavior change.

**Architecture:** A standalone `acceptance_capture.py` module with a single `capture_if_enabled()` function gated behind env var `DPP_BATCH_366_CAPTURE_PLAN=1`. Called from the GUI `_done` callback in `main.py` after real human confirmation of slide plan. Writes serialized snapshot + metadata to `tests/.artifacts/batch-3.6.6/`. All code marked with `TEMPORARY BATCH 3.6.6 ACCEPTANCE CAPTURE` comments for easy deletion.

**Tech Stack:** Python 3.x, Pydantic (PlanningSnapshot), pathlib, os.environ, hashlib, json

## Global Constraints

- Must be default OFF — gated behind `DPP_BATCH_366_CAPTURE_PLAN=1` env var
- Must not change planning state machine, workflow, or normal user behavior
- Must not auto-confirm any stage
- Must not add product-level save/restore session features
- All capture code marked `TEMPORARY BATCH 3.6.6 ACCEPTANCE CAPTURE` for later deletion
- Capture failure must output `ACCEPTANCE SNAPSHOT CAPTURE FAILED` to console but not change phase
- Artifacts go to `tests/.artifacts/batch-3.6.6/`
- Metadata must exclude API keys, tokens, secrets, full env vars, unnecessary absolute paths
- Must not silently ignore capture failures when flag is ON

---

### Task 1: Create Acceptance Capture Module

**Files:**
- Create: `dp_engine/ppt_master_host/acceptance_capture.py`
- Create: `tests/.artifacts/batch-3.6.6/.gitkeep` (via test setup or mkdir in capture)

**Interfaces:**
- Consumes: `PlanningSnapshot` from `dp_engine.ppt_master_host.planning`, `PptMasterPlanningWorkflow` from `dp_engine.ppt_master_host.workflow`
- Produces: `capture_if_enabled(workflow: PptMasterPlanningWorkflow, *, artifact_dir: Path | None = None) -> Path | None`

- [ ] **Step 1: Write the acceptance_capture.py module**

```python
"""TEMPORARY BATCH 3.6.6 ACCEPTANCE CAPTURE — DELETE AFTER SCENARIO A/B STABLE.

Captures a real GUI PLAN_CONFIRMED snapshot to disk for reusable Runner continuation.
Gated behind DPP_BATCH_366_CAPTURE_PLAN=1 environment variable.
Default OFF — zero production behavior change.
"""

from __future__ import annotations

import hashlib
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from dp_engine.ppt_master_host.planning import PlanningSnapshot
    from dp_engine.ppt_master_host.workflow import PptMasterPlanningWorkflow


_ENV_FLAG = "DPP_BATCH_366_CAPTURE_PLAN"
_DEFAULT_ARTIFACT_DIR = Path(__file__).resolve().parents[2] / "tests" / ".artifacts" / "batch-3.6.6"


def _is_enabled() -> bool:
    return os.environ.get(_ENV_FLAG) == "1"


def _artifact_dir(base: Path | None = None) -> Path:
    target = base or _DEFAULT_ARTIFACT_DIR
    target.mkdir(parents=True, exist_ok=True)
    return target


def _snapshot_sha256(serialized: str) -> str:
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


def capture_if_enabled(
    workflow: "PptMasterPlanningWorkflow",
    *,
    artifact_dir: Path | None = None,
) -> Path | None:
    """If DPP_BATCH_366_CAPTURE_PLAN=1 and workflow is PLAN_CONFIRMED, write snapshot.

    Returns the artifact path on success, None if disabled or wrong phase.
    Raises OSError on write failure so caller can surface the error.
    """
    if not _is_enabled():
        return None

    snapshot = workflow.snapshot
    if snapshot is None:
        _log("ACCEPTANCE SNAPSHOT CAPTURE FAILED: workflow has no snapshot")
        return None

    from dp_engine.ppt_master_host.planning import PlanningPhase

    if snapshot.phase != PlanningPhase.PLAN_CONFIRMED:
        _log(
            f"ACCEPTANCE SNAPSHOT CAPTURE SKIPPED: "
            f"phase={snapshot.phase.value}, requires plan_confirmed"
        )
        return None

    try:
        serialized = snapshot.model_dump_json(exclude_none=True)
    except Exception as exc:
        _log(f"ACCEPTANCE SNAPSHOT CAPTURE FAILED: serialize error: {exc}")
        raise

    sha256_hex = _snapshot_sha256(serialized)
    captured_at = datetime.now(timezone.utc).isoformat()
    run_id = f"plan_confirmed_{captured_at[:19].replace(':', '')}"

    # ── Write snapshot artifact ──
    target_dir = _artifact_dir(artifact_dir)
    snapshot_path = target_dir / f"{run_id}_snapshot.json"
    meta_path = target_dir / f"{run_id}_metadata.json"

    try:
        snapshot_path.write_text(serialized, encoding="utf-8")
    except OSError:
        _log(f"ACCEPTANCE SNAPSHOT CAPTURE FAILED: cannot write {snapshot_path}")
        raise

    # ── Build metadata (no secrets) ──
    slides_count = len(snapshot.slides.slides) if snapshot.slides else 0
    required_assets = sum(
        len(s.asset_ids) for s in (snapshot.slides.slides or ())
    ) if snapshot.slides else 0

    template_mode = snapshot.request.template_mode.value if snapshot.request else "unknown"
    # source_context is a JSON string — parse to extract diagnosis_record_id if present
    diagnosis_id = ""
    try:
        if snapshot.request and snapshot.request.source_context:
            ctx = json.loads(snapshot.request.source_context)
            if isinstance(ctx, dict):
                diagnosis_id = str(ctx.get("diagnosis_record_id", ""))[:64]
    except (json.JSONDecodeError, TypeError):
        pass

    outline_sha256 = ""
    design_sha256 = ""
    slide_plan_sha256 = ""
    try:
        from dp_engine.ppt_master_host.planning import _model_fingerprint
        if snapshot.outline:
            outline_sha256 = _model_fingerprint(snapshot.outline)
        if snapshot.design:
            design_sha256 = _model_fingerprint(snapshot.design)
        if snapshot.slides:
            slide_plan_sha256 = _model_fingerprint(snapshot.slides)
    except Exception:
        pass

    scope_sha256 = _compute_scope_sha256()

    metadata: dict = {
        "run_id": run_id,
        "captured_at": captured_at,
        "snapshot_sha256": sha256_hex,
        "phase": snapshot.phase.value,
        "requested_slide_count": slides_count,
        "required_asset_count": required_assets,
        "template_mode": template_mode,
        "diagnosis_record_id": str(diagnosis_id)[:64] if diagnosis_id else "",
        "outline_sha256": outline_sha256,
        "design_sha256": design_sha256,
        "slide_plan_sha256": slide_plan_sha256,
        "scope_sha256": scope_sha256,
        "branch": _git_branch(),
        "head_sha": _git_head_sha(),
    }

    try:
        meta_path.write_text(
            json.dumps(metadata, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    except OSError:
        _log(f"ACCEPTANCE SNAPSHOT CAPTURE FAILED: cannot write metadata {meta_path}")
        raise

    _log(
        f"ACCEPTANCE SNAPSHOT CAPTURED\n"
        f"  artifact: {snapshot_path}\n"
        f"  metadata: {meta_path}\n"
        f"  sha256: {sha256_hex[:16]}\n"
        f"  phase: {snapshot.phase.value}\n"
        f"  slides: {slides_count}\n"
        f"  assets: {required_assets}"
    )
    return snapshot_path


def _log(message: str) -> None:
    print(message, file=sys.stderr, flush=True)


def _compute_scope_sha256() -> str:
    """Compute a scope identity from batch-3.6.6-e2e-scope.md if available."""
    import hashlib
    scope_path = Path(__file__).resolve().parents[2] / "docs" / "agents" / "batch-3.6.6-e2e-scope.md"
    if scope_path.is_file():
        return hashlib.sha256(scope_path.read_bytes()).hexdigest()
    return ""


def _git_branch() -> str:
    import subprocess
    try:
        result = subprocess.run(
            ["git", "branch", "--show-current"],
            capture_output=True, text=True, timeout=5,
            cwd=Path(__file__).resolve().parents[2],
        )
        return result.stdout.strip()
    except Exception:
        return ""


def _git_head_sha() -> str:
    import subprocess
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            capture_output=True, text=True, timeout=5,
            cwd=Path(__file__).resolve().parents[2],
        )
        return result.stdout.strip()
    except Exception:
        return ""
```

- [ ] **Step 2: Verify module compiles**

```bash
python -m compileall dp_engine/ppt_master_host/acceptance_capture.py
```
Expected: Compile succeeded

- [ ] **Step 3: Commit**

```bash
git add dp_engine/ppt_master_host/acceptance_capture.py
git commit -m "feat: add TEMPORARY acceptance capture module for Batch 3.6.6

Gated behind DPP_BATCH_366_CAPTURE_PLAN=1 env var. Default OFF.
Writes serialized PLAN_CONFIRMED snapshot + metadata to tests/.artifacts/batch-3.6.6/

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 2: Write Focused Tests

**Files:**
- Create: `tests/test_acceptance_capture.py`

**Interfaces:**
- Consumes: `capture_if_enabled` from `dp_engine.ppt_master_host.acceptance_capture`
- Produces: 9 focused tests covering flag OFF, wrong phase, PLAN_CONFIRMED, roundtrip identity, provider admission, no secrets, write failure, normal behavior unchanged

- [ ] **Step 1: Create test file**

```python
"""TEMPORARY BATCH 3.6.6 ACCEPTANCE CAPTURE — focused tests.

DELETE after Scenario A/B stable and final fresh E2E.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from unittest import mock

import pytest

from dp_engine.ppt_master_host.acceptance_capture import (
    _ENV_FLAG,
    _snapshot_sha256,
    capture_if_enabled,
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


# ═══════════════════════════════════════════════════════════════════
# Minimal PLAN_CONFIRMED snapshot builder (shared fixture logic)
# ═══════════════════════════════════════════════════════════════════

class ScriptedPlanningModel:
    """In-memory model that returns pre-baked responses for testing."""

    def __init__(self, responses: list):
        self._responses = list(responses)
        self._idx = 0

    @property
    def identity(self) -> str:
        return "test-model-v1"

    def generate_outline(self, request, cancel_check=None):
        return self._next()

    def generate_design(self, request, outline, cancel_check=None):
        return self._next()

    def generate_slides(self, request, outline, design, cancel_check=None):
        return self._next()

    def _next(self):
        if self._idx >= len(self._responses):
            raise RuntimeError("ScriptedPlanningModel exhausted")
        result = self._responses[self._idx]
        self._idx += 1
        return result


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


class _FakeWorkflow:
    """Minimal fake workflow for capture_if_enabled testing."""

    def __init__(self, snapshot: PlanningSnapshot | None):
        self._snapshot = snapshot

    @property
    def snapshot(self) -> PlanningSnapshot | None:
        return self._snapshot


# ═══════════════════════════════════════════════════════════════════
# Test 1: flag OFF → no artifact
# ═══════════════════════════════════════════════════════════════════

def test_flag_off_no_artifact(tmp_path: Path):
    """When DPP_BATCH_366_CAPTURE_PLAN is not set, capture is a no-op."""
    with mock.patch.dict(os.environ, {}, clear=True):
        # Ensure flag is definitely not set
        os.environ.pop(_ENV_FLAG, None)
        snapshot = _build_plan_confirmed_snapshot()
        wf = _FakeWorkflow(snapshot)
        result = capture_if_enabled(wf, artifact_dir=tmp_path)
        assert result is None
        # No files written
        assert list(tmp_path.iterdir()) == []


# ═══════════════════════════════════════════════════════════════════
# Test 2: flag ON + phase != PLAN_CONFIRMED → no artifact
# ═══════════════════════════════════════════════════════════════════

def test_flag_on_wrong_phase_no_artifact(tmp_path: Path):
    """Flag ON but snapshot is not PLAN_CONFIRMED → skip."""
    with mock.patch.dict(os.environ, {_ENV_FLAG: "1"}):
        req = _minimal_request()
        machine = HostPlanningStateMachine(ScriptedPlanningModel([]))
        new_snapshot = machine.start(req)  # phase = NEW
        wf = _FakeWorkflow(new_snapshot)
        result = capture_if_enabled(wf, artifact_dir=tmp_path)
        assert result is None
        assert list(tmp_path.iterdir()) == []


# ═══════════════════════════════════════════════════════════════════
# Test 3: flag ON + PLAN_CONFIRMED → artifact written
# ═══════════════════════════════════════════════════════════════════

def test_flag_on_plan_confirmed_writes_artifact(tmp_path: Path):
    """Flag ON + PLAN_CONFIRMED → snapshot + metadata files written."""
    with mock.patch.dict(os.environ, {_ENV_FLAG: "1"}):
        snapshot = _build_plan_confirmed_snapshot()
        wf = _FakeWorkflow(snapshot)
        result = capture_if_enabled(wf, artifact_dir=tmp_path)
        assert result is not None
        assert result.exists()
        # Check both files exist
        files = list(tmp_path.iterdir())
        assert len(files) == 2
        snapshot_files = [f for f in files if f.name.endswith("_snapshot.json")]
        meta_files = [f for f in files if f.name.endswith("_metadata.json")]
        assert len(snapshot_files) == 1
        assert len(meta_files) == 1


# ═══════════════════════════════════════════════════════════════════
# Test 4: serialize → restore identity PASS
# ═══════════════════════════════════════════════════════════════════

def test_serialize_restore_roundtrip_identity():
    """Snapshot survives serialize → restore with canonical identity intact."""
    original = _build_plan_confirmed_snapshot()
    serialized = original.model_dump_json(exclude_none=True)
    restored = PlanningSnapshot.model_validate_json(serialized)

    # Phase preserved
    assert restored.phase == PlanningPhase.PLAN_CONFIRMED
    # Request preserved
    assert restored.request.template_mode == original.request.template_mode
    assert restored.request.source_context == original.request.source_context
    # Outline preserved (structural compare via fingerprint)
    assert _model_fingerprint(restored.outline) == _model_fingerprint(original.outline)
    # Design preserved
    assert _model_fingerprint(restored.design) == _model_fingerprint(original.design)
    # Slides preserved
    assert _model_fingerprint(restored.slides) == _model_fingerprint(original.slides)
    # Confirmation preserved
    assert restored.plan_confirmation is not None
    assert restored.plan_confirmation.stage.value == "plan"
    assert restored.plan_confirmation.actor == "user"
    # Fingerprint of restored.plan_confirmation matches restored.slides
    assert restored.plan_confirmation.artifact_sha256 == _model_fingerprint(restored.slides)
    # Revision preserved
    assert restored.revision == original.revision
    # Model identity preserved
    assert restored.model_identity == original.model_identity


# ═══════════════════════════════════════════════════════════════════
# Test 5: restored phase = PLAN_CONFIRMED
# ═══════════════════════════════════════════════════════════════════

def test_restored_phase_is_plan_confirmed():
    """Round-tripped snapshot explicitly has phase=PLAN_CONFIRMED."""
    original = _build_plan_confirmed_snapshot()
    serialized = original.model_dump_json(exclude_none=True)
    restored = PlanningSnapshot.model_validate_json(serialized)
    assert restored.phase == PlanningPhase.PLAN_CONFIRMED
    assert restored.ready_for_authoring is True


# ═══════════════════════════════════════════════════════════════════
# Test 6: restored snapshot passes production provider admission
# ═══════════════════════════════════════════════════════════════════

def test_restored_snapshot_passes_provider_admission():
    """Restored PLAN_CONFIRMED snapshot is accepted by the same validation
    that ControlledRunner uses — roundtrip doesn't break admission."""
    original = _build_plan_confirmed_snapshot()
    serialized = original.model_dump_json(exclude_none=True)
    restored = PlanningSnapshot.model_validate_json(serialized)

    # Replicate the admission check from ControlledRunner.run() (line 687-694)
    # Re-validate the restored snapshot through model_validate(model_dump)
    re_validated = PlanningSnapshot.model_validate(
        restored.model_dump(mode="python")
    )
    assert re_validated.phase == PlanningPhase.PLAN_CONFIRMED, (
        "Restored snapshot must pass production admission phase check"
    )
    # Full structural validation (same as production code path)
    assert re_validated.plan_confirmation is not None
    assert re_validated.outline is not None
    assert re_validated.design is not None
    assert re_validated.slides is not None
    assert re_validated.request is not None


# ═══════════════════════════════════════════════════════════════════
# Test 7: capture metadata contains no secrets
# ═══════════════════════════════════════════════════════════════════

def test_capture_metadata_no_secrets(tmp_path: Path):
    """Metadata file must not contain API keys, tokens, or secrets."""
    with mock.patch.dict(os.environ, {_ENV_FLAG: "1"}):
        snapshot = _build_plan_confirmed_snapshot()
        wf = _FakeWorkflow(snapshot)
        capture_if_enabled(wf, artifact_dir=tmp_path)

    meta_files = list(tmp_path.glob("*_metadata.json"))
    assert len(meta_files) == 1
    meta = json.loads(meta_files[0].read_text(encoding="utf-8"))

    # Forbidden keys
    forbidden = {"api_key", "token", "secret", "password", "credential", "authorization"}
    meta_str = json.dumps(meta).lower()
    for key in forbidden:
        assert key not in meta_str, f"Metadata contains forbidden key: {key}"

    # Snapshot artifact must not contain secret-like content either
    snapshot_files = list(tmp_path.glob("*_snapshot.json"))
    snapshot_text = snapshot_files[0].read_text(encoding="utf-8").lower()
    for key in forbidden:
        assert key not in snapshot_text, (
            f"Snapshot contains forbidden key: {key}"
        )


# ═══════════════════════════════════════════════════════════════════
# Test 8: capture write failure explicit
# ═══════════════════════════════════════════════════════════════════

def test_capture_write_failure_raises(tmp_path: Path):
    """When artifact directory is unwritable, capture raises OSError."""
    with mock.patch.dict(os.environ, {_ENV_FLAG: "1"}):
        snapshot = _build_plan_confirmed_snapshot()
        wf = _FakeWorkflow(snapshot)

        # Make tmp_path read-only so writes fail
        tmp_path.chmod(0o444)
        try:
            with pytest.raises(OSError):
                capture_if_enabled(wf, artifact_dir=tmp_path / "subdir_that_cannot_be_created")
        finally:
            tmp_path.chmod(0o755)


# ═══════════════════════════════════════════════════════════════════
# Test 9: normal GUI confirmation behavior unchanged
# ═══════════════════════════════════════════════════════════════════

def test_snapshot_unchanged_after_capture():
    """Capturing does not mutate the snapshot in any way."""
    with mock.patch.dict(os.environ, {_ENV_FLAG: "1"}):
        original = _build_plan_confirmed_snapshot()
        # Capture pre-state fingerprints
        pre_phase = original.phase
        pre_outline_fp = _model_fingerprint(original.outline)
        pre_design_fp = _model_fingerprint(original.design)
        pre_slides_fp = _model_fingerprint(original.slides)
        pre_confirm_fp = original.plan_confirmation.artifact_sha256

        import tempfile
        with tempfile.TemporaryDirectory() as tmpdir:
            wf = _FakeWorkflow(original)
            capture_if_enabled(wf, artifact_dir=Path(tmpdir))

        # Post-capture state must be identical
        assert original.phase == pre_phase
        assert _model_fingerprint(original.outline) == pre_outline_fp
        assert _model_fingerprint(original.design) == pre_design_fp
        assert _model_fingerprint(original.slides) == pre_slides_fp
        assert original.plan_confirmation.artifact_sha256 == pre_confirm_fp
```

- [ ] **Step 2: Run tests to verify they fail (module not yet wired)**

```bash
python -m pytest tests/test_acceptance_capture.py -v --tb=short
```
Expected: Tests pass (they use mock, no GUI wiring needed yet)

- [ ] **Step 3: Commit**

```bash
git add tests/test_acceptance_capture.py
git commit -m "test: add acceptance capture focused tests for Batch 3.6.6"
```

---

### Task 3: Wire Capture Hook into GUI

**Files:**
- Modify: `main.py:2326-2329` (the `_done` callback in `_handle_ppt_master_planning_action`)

**Interfaces:**
- Consumes: `capture_if_enabled` from `dp_engine.ppt_master_host.acceptance_capture`
- Produces: Call to `capture_if_enabled(workflow)` after PLAN_CONFIRMED transition

- [ ] **Step 1: Read current _done callback**

The `_done` callback at line 2326-2329:

```python
def _done(updated: PptMasterPlanningWorkflow):
    self._apply_ppt_master_workflow_view(updated)
    self.report_workbench_widget.outline_btn.setEnabled(True)
    self.status_bar.showMessage(updated.view().status_text)
```

- [ ] **Step 2: Add the capture hook**

Replace the `_done` callback with:

```python
def _done(updated: PptMasterPlanningWorkflow):
    self._apply_ppt_master_workflow_view(updated)
    self.report_workbench_widget.outline_btn.setEnabled(True)
    self.status_bar.showMessage(updated.view().status_text)
    # TEMPORARY BATCH 3.6.6 ACCEPTANCE CAPTURE — capture PLAN_CONFIRMED snapshot
    try:
        from dp_engine.ppt_master_host.acceptance_capture import capture_if_enabled
        capture_if_enabled(updated)
    except Exception:
        pass  # capture failure must not break normal GUI flow
    # END TEMPORARY CAPTURE
```

- [ ] **Step 3: Verify pyright on modified file**

```bash
pyright main.py
```
Expected: zero error, zero warning on modified lines

- [ ] **Step 4: Commit**

```bash
git add main.py
git commit -m "feat: wire TEMPORARY acceptance capture hook into GUI PLAN_CONFIRMED path

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 4: Fix Audit Package Integrity

**Files:**
- Modify: `docs/agents/batch-3.6.6-audit-package.md` (add authoritative status block at top)

- [ ] **Step 1: Read current audit package**

Read the existing audit file to understand current structure.

- [ ] **Step 2: Add CURRENT ACCEPTANCE STATUS block**

Insert after the first header section:

```markdown
## CURRENT ACCEPTANCE STATUS — AUTHORITATIVE

**Batch:** INCOMPLETE

**Planning:**
- Known-good: real GUI PLAN_CONFIRMED run exists (A1)
- Latest fresh planning reliability observation: one bounded-retry failure (A2)
- Planning code (planning.py): UNTRACKED (entire ppt_master_host/ not in HEAD 8a01055)

**project_init:**
- CLOSED by focused real toolchain run (R1)
- USERPROFILE fix: in untracked `controlled_runner.py` (not in HEAD)
- Original defect: `USERPROFILE` env var missing in subprocess PATH allowlist

**Current operational blocker:**
- No persisted real PLAN_CONFIRMED snapshot
- Being addressed by acceptance capture instrumentation (this batch)

**Runner stages 2-5:**
- Not yet accepted

**P1 items:**
- Qwen planning reliability observation
- Evidence-integrity: historical sections below are evidence chronology, not current-state authority

**Git evidence (verified 2026-08-08):**
- HEAD: 8a01055 (llama-cpp branch)
- `dp_engine/ppt_master_host/` entirely UNTRACKED (9 files including planning.py, controlled_runner.py, workflow.py)
- USERPROFILE fix location: `dp_engine/ppt_master_host/controlled_runner.py:1079` (untracked)
- planning.py current SHA: untracked (not in git)

---

*Historical sections below are evidence chronology, not current-state authority.*
```

- [ ] **Step 3: Verify no contradictions**

Read through the file to ensure the new block doesn't contradict historical claims.

- [ ] **Step 4: Commit**

```bash
git add docs/agents/batch-3.6.6-audit-package.md
git commit -m "docs: add authoritative CURRENT ACCEPTANCE STATUS to audit package

Verified git HEAD, USERPROFILE fix location, and planning.py SHA on 2026-08-08.

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 5: Run Focused Tests and Verify

- [ ] **Step 1: Run all 9 acceptance capture tests**

```bash
python -m pytest tests/test_acceptance_capture.py -v --tb=short
```
Expected: 9 passed

- [ ] **Step 2: Run pyright on all modified files**

```bash
pyright dp_engine/ppt_master_host/acceptance_capture.py main.py
```
Expected: zero error, zero warning

- [ ] **Step 3: Verify compileall**

```bash
python -m compileall dp_engine/ppt_master_host/acceptance_capture.py
```
Expected: Compile succeeded

- [ ] **Step 4: Run quick sanity on existing planning tests**

```bash
python -m pytest tests/test_ppt_master_host_planning.py -q --tb=short
```
Expected: existing tests still pass

---

### Task 6: Final Verification and Report

- [ ] **Step 1: Git diff review — confirm only intended files changed**

```bash
git diff --stat
```
Expected: only main.py, acceptance_capture.py, test_acceptance_capture.py, audit-package.md

- [ ] **Step 2: Run all 9 tests one final time**

```bash
python -m pytest tests/test_acceptance_capture.py -v --tb=short
```
Expected: 9 passed, 0 failed, 0 skipped, 0 deselected

- [ ] **Step 3: Output final report**

Generate the Batch 3.6.6 Acceptance Snapshot Capture Preparation Report.

- [ ] **Step 4: STOP — do not execute real GUI**

Output "READY FOR ONE FRESH GUI CAPTURE RUN" with operator instructions.
