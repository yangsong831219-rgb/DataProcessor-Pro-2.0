"""Structured quality feedback and repair-loop tests."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from dp_engine.ppt_master_host import (
    ControlledRunStatus,
    ControlledToolCommand,
    ControlledToolResult,
    OutputLogAttestation,
    QualityStage,
    ToolArtifactAttestation,
    ToolchainRunAttestation,
    method_receipt_from_first_page,
    parse_pptx_postflight_receipt,
    parse_quality_receipt,
)
from dp_engine.ppt_master_host.quality import QualityReceiptError
import pytest


def _result(tmp_path: Path, text: str, *, succeeded: bool) -> ControlledToolResult:
    workspace = tmp_path / "workspace"
    audit = workspace / "audit" / "02-quality"
    audit.mkdir(parents=True)
    data = text.encode("utf-8")
    (audit / "stdout.log").write_bytes(data)
    (audit / "stderr.log").write_bytes(b"")
    log = OutputLogAttestation(
        relative_path="audit/02-quality/stdout.log",
        observed_bytes=len(data),
        captured_bytes=len(data),
        stream_sha256=hashlib.sha256(data).hexdigest(),
        log_sha256=hashlib.sha256(data).hexdigest(),
        truncated=False,
    )
    empty = hashlib.sha256(b"").hexdigest()
    return ControlledToolResult(
        run_id="02-quality",
        workspace_id="ppt-fixturetoken",
        command=ControlledToolCommand.SVG_QUALITY_CHECK,
        status=(ControlledRunStatus.SUCCEEDED if succeeded else ControlledRunStatus.FAILED),
        planning_snapshot_sha256="a" * 64,
        toolchain=ToolchainRunAttestation(
            version="2.7.0",
            archive_sha256="b" * 64,
            tree_sha256="c" * 64,
            file_count=1,
            total_bytes=1,
        ),
        runtime_sha256="d" * 64,
        started_at="2026-08-12T00:00:00+00:00",
        finished_at="2026-08-12T00:00:01+00:00",
        duration_ms=1,
        exit_code=(0 if succeeded else 1),
        error_code=("" if succeeded else "tool_failed"),
        stdout=log,
        stderr=log.model_copy(update={
            "relative_path": "audit/02-quality/stderr.log",
            "observed_bytes": 0,
            "captured_bytes": 0,
            "stream_sha256": empty,
            "log_sha256": empty,
        }),
        audit_dir=str(audit),
    )


def test_parser_preserves_page_rule_and_severity(tmp_path: Path) -> None:
    result = _result(
        tmp_path,
        """[WARN] P01_cover.svg - Passed (with warnings)
   [WARN] page SVG is missing root data-pptx-page-role
[ERROR] P02_evidence.svg - Failed
   [ERROR] spec_lock typography-size recurrence: undeclared font-size 22
""",
        succeeded=False,
    )

    receipt = parse_quality_receipt(result, stage=QualityStage.FINAL)

    assert receipt.passed is False
    assert receipt.failed_pages[0].page_id == "P02"
    assert receipt.failed_pages[0].issues[0].rule_id == "typography_size_recurrence"


@pytest.mark.parametrize(
    ("message", "rule_id"),
    (
        (
            "Duplicate top-level group id 'section-title' at visible positions 7, 8",
            "top_level_group_id",
        ),
        (
            "<rect> with data-pptx-role requires a stable id",
            "semantic_stable_id",
        ),
    ),
)
def test_parser_distinguishes_structural_error_rules(
    tmp_path: Path,
    message: str,
    rule_id: str,
) -> None:
    result = _result(
        tmp_path,
        f"[ERROR] P07_evidence.svg - Failed\n   [ERROR] {message}\n",
        succeeded=False,
    )

    receipt = parse_quality_receipt(result, stage=QualityStage.FINAL)

    assert receipt.failed_pages[0].issues[0].rule_id == rule_id


def test_first_page_receipt_becomes_method_receipt(tmp_path: Path) -> None:
    result = _result(
        tmp_path,
        """[WARN] P01_cover.svg - Passed (with warnings)
   [WARN] page SVG is missing root data-pptx-page-role
""",
        succeeded=True,
    )

    quality = parse_quality_receipt(result, stage=QualityStage.FIRST_PAGE)
    method = method_receipt_from_first_page(quality)

    assert method.first_page_id == "P01"
    assert method.accepted_with_warnings is True
    assert method.warning_rule_ids == ("page_role",)


def test_parser_rejects_stdout_changed_after_attestation(tmp_path: Path) -> None:
    result = _result(
        tmp_path,
        "[WARN] P01_cover.svg - Passed (with warnings)\n",
        succeeded=True,
    )
    (Path(result.audit_dir) / "stdout.log").write_text(
        "[ERROR] P01_cover.svg - Failed\n",
        encoding="utf-8",
    )

    with pytest.raises(QualityReceiptError, match="attestation"):
        parse_quality_receipt(result, stage=QualityStage.FIRST_PAGE)


def test_postflight_receipt_rejects_unlinked_quality_gate(tmp_path: Path) -> None:
    result = _result(tmp_path, "", succeeded=True)
    workspace = Path(result.audit_dir).parent.parent
    output = workspace / "output" / "deck.pptx"
    report = workspace / "project" / "demo" / "validation" / "deck.report.json"
    output.parent.mkdir(parents=True)
    report.parent.mkdir(parents=True)
    output.write_bytes(b"pptx")
    payload = {
        "schema": "ppt-master.pptx-postflight-report.v1",
        "status": "passed-with-warnings",
        "output": {"path": str(output.resolve()), "bytes": 4},
        "source": {
            "svg_slide_count": 3,
            "layout_definition_count": 0,
            "fingerprint": {},
        },
        "package": {
            "zip_integrity": "passed",
            "corrupt_member": None,
            "slides": 3,
            "notes": 0,
            "masters": 1,
            "layouts": 1,
        },
        "checks": {
            "zip_integrity": "passed",
            "slide_count": "passed",
            "internal_relationships": "enforced-at-build",
            "structured_package": "not-applicable",
            "transitions": "enforced-at-build",
            "animations": "enforced-at-build",
            "quality_gate": "not-provided",
            "quality_warnings": "passed",
            "template_tokens": "passed",
            "external_images": "passed",
            "font_portability": "passed",
        },
        "quality": {"status": "not-provided", "path": "quality.json"},
        "resources": {},
        "backup_path": None,
        "conversion_trace_path": None,
    }
    report_bytes = json.dumps(payload).encode("utf-8")
    report.write_bytes(report_bytes)
    output_artifact = ToolArtifactAttestation(
        relative_path="output/deck.pptx",
        size_bytes=4,
        sha256=hashlib.sha256(b"pptx").hexdigest(),
    )
    report_artifact = ToolArtifactAttestation(
        relative_path="project/demo/validation/deck.report.json",
        size_bytes=len(report_bytes),
        sha256=hashlib.sha256(report_bytes).hexdigest(),
    )
    result = result.model_copy(
        update={"artifacts": (output_artifact, report_artifact)}
    )

    with pytest.raises(QualityReceiptError, match="postflight report is invalid"):
        parse_pptx_postflight_receipt(
            result,
            project_relative="project/demo",
            output_name="deck.pptx",
            expected_slide_count=3,
        )
