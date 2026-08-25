"""Batch 3.3.2: Report Bridge Workbench UI Tests.

UI-11..UI-26: Verify ReportWorkbenchWidget bridge asset summary display.
"""

from __future__ import annotations

import pytest
from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QApplication

import re
from pathlib import Path

from dp_engine.report_bridge.models import (
    ReportAssetSummary,
    ReportAssetRole,
    ReportBridgePublicResult,
    ReportBridgeStatus,
)
from ui.report_workbench import ReportWorkbenchWidget


def _make_asset_summary(order: int = 0, role: str = "image",
                        display_name: str = "test.png",
                        size_bytes: int = 1024) -> ReportAssetSummary:
    """Create a ReportAssetSummary for testing."""
    return ReportAssetSummary(
        asset_key=f"asset_{order}",
        display_name=display_name,
        role=ReportAssetRole(role),
        size_bytes=size_bytes,
        order=order,
    )


class TestBridgeStatusDisplay:
    """UI-15, UI-16: Workbench displays only ReportAssetSummary, zero Path."""

    def test_preparing_status_shows_cancel_button(self, qapp):
        """UI-17: PREPARING status shows cancel button."""
        wb = ReportWorkbenchWidget()
        wb.show()  # Widget must be shown for visibility checks
        wb.set_bridge_status(status="preparing")
        assert wb._bridge_group.isVisible()
        assert wb._bridge_cancel_prepare_btn.isVisible()
        assert not wb._bridge_clear_btn.isVisible()
        wb.hide()

    def test_ready_status_shows_clear_button(self, qapp):
        """UI-15, UI-18: READY status shows asset summaries and clear button."""
        wb = ReportWorkbenchWidget()
        wb.show()
        assets = (_make_asset_summary(0, "image", "chart.png", 2048),)
        wb.set_bridge_status(status="ready", assets=assets)
        assert wb._bridge_group.isVisible()
        assert not wb._bridge_cancel_prepare_btn.isVisible()
        assert wb._bridge_clear_btn.isVisible()
        assert wb._bridge_asset_list.count() == 1
        wb.hide()

    def test_ready_assets_shown_with_role_display(self, qapp):
        """UI-15: Asset display shows role name, not internal code."""
        wb = ReportWorkbenchWidget()
        wb.show()
        assets = (
            _make_asset_summary(0, "image", "img.png", 1024),
            _make_asset_summary(1, "table_source", "data.csv", 2048),
        )
        wb.set_bridge_status(status="ready", assets=assets)
        texts = [
            wb._bridge_asset_list.item(i).text()
            for i in range(wb._bridge_asset_list.count())
        ]
        assert "图片" in texts[0]
        assert "表格" in texts[1]
        wb.hide()

    def test_failed_status_shows_error(self, qapp):
        """UI-15: FAILED status shows error message."""
        wb = ReportWorkbenchWidget()
        wb.show()
        wb.set_bridge_status(status="failed", safe_error="素材解析失败")
        assert wb._bridge_group.isVisible()
        assert "失败" in wb._bridge_status_label.text()
        assert "素材解析失败" in wb._bridge_info_label.text()
        wb.hide()

    def test_cancelled_status_hides_buttons(self, qapp):
        """UI-15: CANCELLED status hides both cancel and clear."""
        wb = ReportWorkbenchWidget()
        wb.show()
        wb.set_bridge_status(status="cancelled")
        assert wb._bridge_group.isVisible()
        assert not wb._bridge_cancel_prepare_btn.isVisible()
        assert not wb._bridge_clear_btn.isVisible()
        wb.hide()

    def test_succeeded_status_shows_completion(self, qapp):
        """UI-15: SUCCEEDED status shows completion info."""
        wb = ReportWorkbenchWidget()
        wb.show()
        assets = (_make_asset_summary(0, "text_source", "output.json", 512),)
        wb.set_bridge_status(status="succeeded", assets=assets)
        assert "已用于报告" in wb._bridge_status_label.text()
        assert not wb._bridge_clear_btn.isVisible()
        wb.hide()

    def test_released_status_hides_group(self, qapp):
        """UI-18: Released/empty status hides bridge group."""
        wb = ReportWorkbenchWidget()
        wb.show()
        wb.set_bridge_status(status="ready", assets=(_make_asset_summary(),))
        assert wb._bridge_group.isVisible()
        wb.set_bridge_status(status="released")
        assert not wb._bridge_group.isVisible()
        wb.hide()


class TestBridgeClaimInfo:
    """UI-19..UI-21: Bridge claim info for report generation."""

    def test_no_ready_assets_returns_empty_claim(self, qapp):
        """UI-19: No READY assets → get_bridge_claim_info returns empty."""
        wb = ReportWorkbenchWidget()
        info = wb.get_bridge_claim_info()
        assert info == {}

    def test_ready_assets_return_request_id_and_generation(self, qapp):
        """READY assets → get_bridge_claim_info returns request_id + generation."""
        wb = ReportWorkbenchWidget()
        assets = (_make_asset_summary(),)
        wb.set_bridge_status(
            status="ready", assets=assets,
            request_id="abc123def456", generation=5,
        )
        info = wb.get_bridge_claim_info()
        assert info["request_id"] == "abc123def456"
        assert info["generation"] == 5

    def test_has_ready_bridge_assets_detects_ready(self, qapp):
        """has_ready_bridge_assets returns True only when READY."""
        wb = ReportWorkbenchWidget()
        assert not wb.has_ready_bridge_assets()
        wb.set_bridge_status(status="ready", assets=(_make_asset_summary(),))
        assert wb.has_ready_bridge_assets()

    def test_no_bridge_material_when_no_ready(self, qapp):
        """UI-19: get_bridge_assets_for_report returns empty when not READY."""
        wb = ReportWorkbenchWidget()
        assert wb.get_bridge_assets_for_report() == ()


class TestBridgeSignals:
    """UI-17, UI-18: Bridge signals are emitted."""

    def test_clear_requested_signal_connected(self, qapp):
        """UI-18: bridge_clear_requested signal is connected and emit-able."""
        wb = ReportWorkbenchWidget()
        wb.show()
        assets = (_make_asset_summary(),)
        wb.set_bridge_status(status="ready", assets=assets)

        received = []
        wb.bridge_clear_requested.connect(lambda: received.append(True))
        wb._bridge_clear_btn.click()
        qapp.processEvents()
        assert len(received) == 1
        wb.hide()

    def test_cancel_prepare_signal_connected(self, qapp):
        """UI-17: bridge_cancel_prepare_requested signal is connected."""
        wb = ReportWorkbenchWidget()
        wb.show()
        wb.set_bridge_status(status="preparing")

        received = []
        wb.bridge_cancel_prepare_requested.connect(lambda: received.append(True))
        wb._bridge_cancel_prepare_btn.click()
        qapp.processEvents()
        assert len(received) == 1
        wb.hide()


class TestWorkbenchSummaryZeroPath:
    """UI-16: Workbench summary contains only public safe fields.

    ReportBridgePublicResult and workbench display must never expose:
    Path, workspace_path, managed_filename, artifact_id, task_id,
    skill_id, sha256, manifest, storage_relpath, exception objects,
    or raw exception messages.
    """

    SENSITIVE_FIELDS: tuple[str, ...] = (
        "workspace_path", "managed_filename", "storage_relpath",
        "artifact_id", "task_id", "skill_id",
        "sha256", "manifest", "bridge_dir", "material_path",
        "artifact_root",
    )

    def test_public_result_model_has_zero_path_fields(self, qapp):
        """UI-16: ReportBridgePublicResult has no Path or sensitive fields."""
        from dataclasses import fields as dc_fields
        result = ReportBridgePublicResult(
            request_id="req-001", generation=1,
            status=ReportBridgeStatus.READY,
            assets=(_make_asset_summary(0, "image", "img.png", 1024),),
            warnings=(), safe_error_code=None, safe_error_message=None,
        )
        # Inspect field names — must not contain sensitive names
        field_names = {f.name for f in dc_fields(result)}
        for sensitive in self.SENSITIVE_FIELDS:
            assert sensitive not in field_names, (
                f"ReportBridgePublicResult exposes forbidden field: {sensitive}"
            )
        # Inspect field values — must not be Path objects
        for f in dc_fields(result):
            val = getattr(result, f.name)
            assert not isinstance(val, Path), (
                f"ReportBridgePublicResult.{f.name} is a Path — forbidden"
            )

    def test_asset_summary_has_zero_path_fields(self, qapp):
        """UI-16: ReportAssetSummary has only safe fields."""
        from dataclasses import fields as dc_fields
        summary = _make_asset_summary(0, "image", "img.png", 1024)
        field_names = {f.name for f in dc_fields(summary)}
        assert field_names == {"asset_key", "display_name", "role", "size_bytes", "order"}, (
            f"ReportAssetSummary has unexpected fields: {field_names}"
        )
        for sensitive in self.SENSITIVE_FIELDS:
            assert sensitive not in field_names, (
                f"ReportAssetSummary exposes forbidden field: {sensitive}"
            )

    def test_workbench_widget_text_zero_path(self, qapp):
        """UI-16: Workbench display text contains zero absolute paths."""
        wb = ReportWorkbenchWidget()
        wb.show()
        assets = (
            _make_asset_summary(0, "image", "chart.png", 2048),
            _make_asset_summary(1, "table_source", "data.csv", 4096),
        )
        wb.set_bridge_status(status="ready", assets=assets)

        # Collect all visible text from bridge group widgets
        all_text: list[str] = []
        for child in wb._bridge_group.findChildren(type(wb._bridge_status_label)):
            if hasattr(child, 'text'):
                t = child.text()
                if t:
                    all_text.append(t)

        # Also check the list widget items
        for i in range(wb._bridge_asset_list.count()):
            item_text = wb._bridge_asset_list.item(i).text()
            all_text.append(item_text)

        combined = " ".join(all_text)

        # Must not contain anything that looks like a Windows or Unix path
        assert "C:\\" not in combined, "Workbench text contains Windows path"
        assert "/home/" not in combined, "Workbench text contains Unix path"
        assert "\\\\" not in combined, "Workbench text contains UNC path"
        # Must not contain a full uuid4 hex (32 hex chars) which could be artifact_id
        uuid4_hex_pattern = re.compile(r'\b[a-f0-9]{32}\b')
        assert not uuid4_hex_pattern.search(combined), (
            "Workbench text contains 32-char hex (potential artifact_id/task_id)"
        )

        wb.hide()

    def test_workbench_internal_model_zero_path(self, qapp):
        """UI-16: Workbench internal _bridge_request_id stores only string id."""
        wb = ReportWorkbenchWidget()
        wb.show()
        assets = (_make_asset_summary(0, "image", "img.png", 1024),)
        wb.set_bridge_status(
            status="ready", assets=assets,
            request_id="abc123def456", generation=5,
        )
        # _bridge_request_id should be a plain string, not a Path
        assert isinstance(wb._bridge_request_id, str)
        assert "\\" not in wb._bridge_request_id
        assert "/" not in wb._bridge_request_id

        # _bridge_assets_info should be a tuple of summaries, not contain Path
        info = wb.get_bridge_claim_info()
        assert "workspace_path" not in info
        assert "managed_filename" not in info
        wb.hide()


# ── Fixtures ──

@pytest.fixture(scope="session")
def qapp():
    """Session-scoped QApplication."""
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    yield app
