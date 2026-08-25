"""TEMPORARY BATCH 3.6.6 ACCEPTANCE CAPTURE — DELETE AFTER SCENARIO A/B STABLE.

Captures real GUI planning snapshots at three checkpoints:
  1. DESIGN_CONFIRMED — after ConfirmDesign succeeds, before GenerateSlides
  2. SLIDES_READY   — after GenerateSlides succeeds
  3. PLAN_CONFIRMED — after ConfirmPlan succeeds

Gated behind DPP_BATCH_366_CAPTURE_PLAN=1 environment variable.
Default OFF — zero production behavior change.

Capture never modifies phase, snapshot, confirmation state, or fingerprint.
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
    from dp_engine.ppt_master_host.planning import PlanningPhase, PlanningSnapshot
    from dp_engine.ppt_master_host.workflow import PptMasterPlanningWorkflow


_ENV_FLAG = "DPP_BATCH_366_CAPTURE_PLAN"
_DEFAULT_ARTIFACT_ROOT = (
    Path(__file__).resolve().parents[2] / "tests" / ".artifacts" / "batch-3.6.6"
)

_RUN_ID: str | None = None


# ── Public API ──────────────────────────────────────────────────────


def _is_enabled() -> bool:
    return os.environ.get(_ENV_FLAG) == "1"


def startup_signal() -> str:
    """Print acceptance capture arming status at application startup.

    Called once early in main.py so the operator can verify capture is ARMED
    before beginning Scenario A.  Returns the signal keyword for test inspection.
    """
    armed = _is_enabled()
    signal = (
        "BATCH_366_ACCEPTANCE_CAPTURE=ARMED"
        if armed
        else "BATCH_366_ACCEPTANCE_CAPTURE=NOT_ARMED"
    )
    print(
        f"[ACCEPTANCE_CAPTURE]\narmed = {str(armed).lower()}\nflag = {_ENV_FLAG}",
        file=sys.stderr,
        flush=True,
    )
    if armed:
        try:
            rel = _DEFAULT_ARTIFACT_ROOT.relative_to(
                Path(__file__).resolve().parents[2]
            )
        except ValueError:
            rel = _DEFAULT_ARTIFACT_ROOT
        print(f"artifact_root = {rel}", file=sys.stderr, flush=True)
    return signal


def _get_run_id() -> str:
    """Return the stable run-id for the current process lifetime."""
    global _RUN_ID
    if _RUN_ID is None:
        _RUN_ID = datetime.now(timezone.utc).strftime("run_%Y%m%d_%H%M%S")
    return _RUN_ID


def _reset_run_id() -> None:
    """Reset the run id (test-only)."""
    global _RUN_ID
    _RUN_ID = None


def capture_at_phase(
    snapshot: "PlanningSnapshot",
    phase: "PlanningPhase",
    *,
    artifact_root: Path | None = None,
) -> Path | None:
    """Capture a planning snapshot at the given phase checkpoint.

    Only active when DPP_BATCH_366_CAPTURE_PLAN=1.
    Does NOT modify the snapshot, state machine, or phase.

    Args:
        snapshot: The PlanningSnapshot to capture.
        phase: Expected PlanningPhase (DESIGN_CONFIRMED, SLIDES_READY,
               or PLAN_CONFIRMED).
        artifact_root: Override artifact root directory.

    Returns:
        Path to the written snapshot file, or None if capture is disabled.
    """
    if not _is_enabled():
        return None

    if snapshot is None:
        _log("ACCEPTANCE CHECKPOINT WRITE FAILED: snapshot is None")
        return None

    # ── late import to avoid circular dependency ──
    from dp_engine.ppt_master_host.planning import PlanningPhase

    _PHASE_FILENAME: dict[PlanningPhase, str] = {
        PlanningPhase.DESIGN_CONFIRMED: "design_confirmed.snapshot.json",
        PlanningPhase.SLIDES_READY: "slides_ready.snapshot.json",
        PlanningPhase.PLAN_CONFIRMED: "plan_confirmed.snapshot.json",
    }

    filename = _PHASE_FILENAME.get(phase)
    if filename is None:
        _log(
            "ACCEPTANCE CHECKPOINT WRITE FAILED: "
            f"unsupported capture phase {phase.value}"
        )
        return None

    if snapshot.phase != phase:
        _log(
            "ACCEPTANCE CHECKPOINT WRITE FAILED: "
            f"snapshot phase={snapshot.phase.value} != requested capture phase={phase.value}"
        )
        return None

    # ── Serialize ──
    try:
        serialized = snapshot.model_dump_json(exclude_none=True)
    except Exception as exc:
        _log(f"ACCEPTANCE CHECKPOINT WRITE FAILED: serialize error: {exc}")
        raise

    # ── Write snapshot artifact ──
    run_id = _get_run_id()
    target_dir = _artifact_dir(run_id, artifact_root)
    snapshot_path = target_dir / filename

    try:
        snapshot_path.write_text(serialized, encoding="utf-8")
    except OSError:
        _log(f"ACCEPTANCE CHECKPOINT WRITE FAILED: cannot write {snapshot_path}")
        raise

    # ── Build metadata (no secrets, no tokens, no env) ──
    sha256_hex = _snapshot_sha256(serialized)
    captured_at = datetime.now(timezone.utc).isoformat()

    slides_count = len(snapshot.slides.slides) if snapshot.slides else 0
    required_assets = (
        sum(len(s.asset_ids) for s in (snapshot.slides.slides or ()))
        if snapshot.slides
        else 0
    )

    template_mode = (
        snapshot.request.template_mode.value if snapshot.request else "unknown"
    )

    # diagnosis_record_id from source_context (sanitized)
    diagnosis_id = ""
    try:
        if snapshot.request and snapshot.request.source_context:
            ctx = json.loads(snapshot.request.source_context)
            if isinstance(ctx, dict):
                diagnosis_id = str(ctx.get("diagnosis_record_id", ""))[:64]
    except (json.JSONDecodeError, TypeError):
        pass

    # Content fingerprints
    outline_sha256 = ""
    design_sha256 = ""
    slide_plan_sha256 = ""
    planning_request_sha256 = ""
    try:
        from dp_engine.ppt_master_host.planning import _model_fingerprint

        if snapshot.outline:
            outline_sha256 = _model_fingerprint(snapshot.outline)
        if snapshot.design:
            design_sha256 = _model_fingerprint(snapshot.design)
        if snapshot.slides:
            slide_plan_sha256 = _model_fingerprint(snapshot.slides)
        if snapshot.request:
            planning_request_sha256 = _model_fingerprint(snapshot.request)
    except Exception:
        pass

    scope_sha256 = _compute_scope_sha256()

    # Per-phase metadata (no secrets — see test_capture_metadata_no_secrets)
    phase_meta: dict = {
        "run_id": run_id,
        "captured_at": captured_at,
        "phase": phase.value,
        "snapshot_sha256": sha256_hex,
        "planning_request_sha256": planning_request_sha256,
        "outline_sha256": outline_sha256,
        "design_sha256": design_sha256,
        "slide_plan_sha256": slide_plan_sha256,
        "requested_slide_count": slides_count,
        "required_asset_count": required_assets,
        "template_mode": template_mode,
        "diagnosis_record_id": diagnosis_id,
        "scope_sha256": scope_sha256,
        "branch": _git_branch(),
        "head_sha": _git_head_sha(),
        "source": "real_gui_acceptance",
    }

    meta_path = target_dir / f"{phase.value}_metadata.json"
    try:
        meta_path.write_text(
            json.dumps(phase_meta, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    except OSError:
        _log(
            "ACCEPTANCE CHECKPOINT WRITE FAILED: cannot write metadata "
            f"{meta_path}"
        )
        raise

    _log(
        f"ACCEPTANCE CHECKPOINT CAPTURED [{phase.value}]\n"
        f"  run_id: {run_id}\n"
        f"  artifact: {snapshot_path}\n"
        f"  sha256: {sha256_hex[:16]}\n"
        f"  slides: {slides_count}\n"
        f"  assets: {required_assets}"
    )
    return snapshot_path


# ── Legacy wrapper (kept for backward compat) ──


def capture_if_enabled(
    workflow: "PptMasterPlanningWorkflow",
    *,
    artifact_dir: Path | None = None,
) -> Path | None:
    """Legacy wrapper: capture PLAN_CONFIRMED from a workflow object.

    Prefer ``capture_at_phase(snapshot, phase, ...)`` for new call sites.
    """
    from dp_engine.ppt_master_host.planning import PlanningPhase

    snapshot = workflow.snapshot
    if snapshot is None:
        _log("ACCEPTANCE CHECKPOINT WRITE FAILED: workflow has no snapshot")
        return None

    if snapshot.phase != PlanningPhase.PLAN_CONFIRMED:
        _log(
            "ACCEPTANCE CHECKPOINT SKIPPED: "
            f"phase={snapshot.phase.value}, requires plan_confirmed"
        )
        return None

    return capture_at_phase(
        snapshot, snapshot.phase, artifact_root=artifact_dir
    )


# ── Internal helpers ────────────────────────────────────────────────


def _snapshot_sha256(serialized: str) -> str:
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


def _artifact_dir(run_id: str, base: Path | None = None) -> Path:
    target = (base or _DEFAULT_ARTIFACT_ROOT) / run_id
    target.mkdir(parents=True, exist_ok=True)
    return target


_LOG_PATH: Path | None = None


def _log(message: str) -> None:
    print(message, file=sys.stderr, flush=True)
    # TEMPORARY: fallback log file — PyQt may swallow stderr on Windows
    global _LOG_PATH
    try:
        if _LOG_PATH is None:
            _LOG_PATH = _DEFAULT_ARTIFACT_ROOT / "capture.log"
            _LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        with open(_LOG_PATH, "a", encoding="utf-8") as f:
            f.write(message + "\n")
            f.flush()
    except OSError:
        pass  # must not break if even log file is unwritable


def _compute_scope_sha256() -> str:
    """Compute a scope identity from batch-3.6.6-e2e-scope.md if available."""
    scope_path = (
        Path(__file__).resolve().parents[2]
        / "docs"
        / "agents"
        / "batch-3.6.6-e2e-scope.md"
    )
    if scope_path.is_file():
        return hashlib.sha256(scope_path.read_bytes()).hexdigest()
    return ""


def _git_branch() -> str:
    import subprocess

    try:
        result = subprocess.run(
            ["git", "branch", "--show-current"],
            capture_output=True,
            text=True,
            timeout=5,
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
            capture_output=True,
            text=True,
            timeout=5,
            cwd=Path(__file__).resolve().parents[2],
        )
        return result.stdout.strip()
    except Exception:
        return ""
