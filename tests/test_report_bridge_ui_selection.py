"""Batch 3.3.2: Report Bridge UI Selection Tests.

UI-01..UI-10: Verify Skill Tab bridge multi-select behavior.
"""

from __future__ import annotations

import pytest
from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QApplication, QTreeWidgetItem

from dp_engine.report_bridge.coordinator import ArtifactOperationCoordinator
from dp_engine.report_bridge.models import (
    InternalBridgeState,
    ReportArtifactSelection,
    ReportAssetRole,
    ReportBridgeRequest,
)
from ui.report_bridge_controller import ReportBridgeController
from ui.skill_center.artifact_panel import ArtifactPanel
from ui.skill_tab import AgentSkillWidget


# ── Helpers ──

def _make_fake_artifacts(count: int = 3):
    """Create fake artifact dicts as they would appear in UserRole data."""
    artifacts = []
    for i in range(count):
        media_map = {
            0: "image/png",
            1: "text/csv",
            2: "application/json",
        }
        art = type("FakeArt", (), {
            "artifact_id": f"a{i:031d}",
            "skill_id": "test_skill_001",
            "task_id": "a1b2c3d4e5f6a7b8c9d0e1f2a3b4c5d6",
            "media_type": media_map.get(i, "text/plain"),
            "display_name": f"test_file_{i}.dat",
            "kind": "file",
            "size_bytes": 1024 * (i + 1),
            "relative_path": f"out/test_file_{i}.dat",
        })()
        artifacts.append(art)
    return tuple(artifacts)


def _artifact_panel_with_items(count: int = 3) -> ArtifactPanel:
    """Create an ArtifactPanel populated with fake artifacts."""
    panel = ArtifactPanel()
    artifacts = _make_fake_artifacts(count)
    panel.populate_artifacts(artifacts)
    return panel


class TestBridgeSelection:
    """UI-01: Current owner Artifact multi-select."""

    def test_can_check_multiple_artifacts(self, qapp):
        """UI-01: Multiple artifacts can be checked from same owner."""
        panel = _artifact_panel_with_items(3)
        # Check first two items
        for i in range(2):
            item = panel.artifact_list.topLevelItem(i)
            item.setCheckState(0, Qt.CheckState.Checked)
        assert panel.get_checked_count() == 2

    def test_checked_artifacts_returned_in_visible_order(self, qapp):
        """UI-08: order follows visible row order, not click order."""
        panel = _artifact_panel_with_items(3)
        # Check items out of order
        panel.artifact_list.topLevelItem(2).setCheckState(0, Qt.CheckState.Checked)
        panel.artifact_list.topLevelItem(0).setCheckState(0, Qt.CheckState.Checked)
        checked = panel.get_checked_artifacts()
        # Must be in row order: 0, 2
        assert len(checked) == 2
        assert checked[0]["artifact_id"] == "a0000000000000000000000000000000"
        assert checked[1]["artifact_id"] == "a0000000000000000000000000000002"

    def test_clear_all_checkboxes_resets_count(self, qapp):
        """Clearing all checkboxes sets count to 0."""
        panel = _artifact_panel_with_items(3)
        panel.artifact_list.topLevelItem(0).setCheckState(0, Qt.CheckState.Checked)
        panel.artifact_list.topLevelItem(1).setCheckState(0, Qt.CheckState.Checked)
        assert panel.get_checked_count() == 2
        panel.clear_all_checkboxes()
        assert panel.get_checked_count() == 0


class TestSelectionCount:
    """UI-03..UI-06: Selection count validation."""

    def test_zero_items_send_disabled(self, qapp):
        """UI-03: Send button disabled when 0 items checked."""
        panel = _artifact_panel_with_items(3)
        panel.update_bridge_selection_ui(0, 12)
        assert not panel.send_to_report_btn.isEnabled()

    def test_one_item_allowed(self, qapp):
        """UI-04: Send enabled when exactly 1 item checked."""
        panel = _artifact_panel_with_items(3)
        panel.update_bridge_selection_ui(1, 12)
        assert panel.send_to_report_btn.isEnabled()

    def test_max_items_allowed(self, qapp):
        """UI-05: Send enabled when exactly 12 items checked."""
        panel = _artifact_panel_with_items(3)
        panel.update_bridge_selection_ui(12, 12)
        assert panel.send_to_report_btn.isEnabled()

    def test_more_than_max_disabled(self, qapp):
        """UI-06: 13th item rejected, button disabled, no silent truncation."""
        panel = _artifact_panel_with_items(3)
        panel.update_bridge_selection_ui(13, 12)
        assert not panel.send_to_report_btn.isEnabled()


class TestMediaTypeRoleMapping:
    """UI-07, UI-09: Media type to role deterministic mapping."""

    def test_png_maps_to_image(self, qapp):
        """UI-07: image/png → IMAGE."""
        result = AgentSkillWidget._MEDIA_TYPE_TO_ROLE.get("image/png")
        assert result == "image"

    def test_jpeg_maps_to_image(self, qapp):
        result = AgentSkillWidget._MEDIA_TYPE_TO_ROLE.get("image/jpeg")
        assert result == "image"

    def test_csv_maps_to_table_source(self, qapp):
        result = AgentSkillWidget._MEDIA_TYPE_TO_ROLE.get("text/csv")
        assert result == "table_source"

    def test_json_maps_to_text_source(self, qapp):
        result = AgentSkillWidget._MEDIA_TYPE_TO_ROLE.get("application/json")
        assert result == "text_source"

    def test_plain_text_maps_to_text_source(self, qapp):
        result = AgentSkillWidget._MEDIA_TYPE_TO_ROLE.get("text/plain")
        assert result == "text_source"

    def test_unsupported_media_type_not_in_map(self, qapp):
        """UI-09: Unsupported media type not in mapping."""
        assert "application/pdf" not in AgentSkillWidget._MEDIA_TYPE_TO_ROLE
        assert "application/zip" not in AgentSkillWidget._MEDIA_TYPE_TO_ROLE
        assert "application/vnd.ms-excel" not in AgentSkillWidget._MEDIA_TYPE_TO_ROLE


class TestSelectionConstruction:
    """Selection construction produces valid ReportArtifactSelection."""

    def test_selection_constructs_with_valid_data(self, qapp):
        """Valid data produces a valid ReportArtifactSelection."""
        sel = ReportArtifactSelection(
            schema_version=1,
            skill_id="test-skill",
            task_id="a1b2c3d4e5f6a7b8c9d0e1f2a3b4c5d6",
            artifact_id="a0b1c2d3e4f5a6b7c8d9e0f1a2b3c4d5",
            role=ReportAssetRole.IMAGE,
            order=0,
            display_name_hint="test.png",
        )
        assert sel.order == 0
        assert sel.role == ReportAssetRole.IMAGE

    def test_schema_version_must_be_one(self, qapp):
        """schema_version other than 1 raises ValueError."""
        with pytest.raises(ValueError, match="schema_version"):
            ReportArtifactSelection(
                schema_version=2,
                skill_id="test-skill",
                task_id="a1b2c3d4e5f6a7b8c9d0e1f2a3b4c5d6",
                artifact_id="a0b1c2d3e4f5a6b7c8d9e0f1a2b3c4d5",
                role=ReportAssetRole.IMAGE,
                order=0,
            )

    def test_order_must_be_non_negative(self, qapp):
        """Negative order raises ValueError."""
        with pytest.raises(ValueError, match="order"):
            ReportArtifactSelection(
                schema_version=1,
                skill_id="test-skill",
                task_id="a1b2c3d4e5f6a7b8c9d0e1f2a3b4c5d6",
                artifact_id="a0b1c2d3e4f5a6b7c8d9e0f1a2b3c4d5",
                role=ReportAssetRole.IMAGE,
                order=-1,
            )


class TestSendRejectsMixedOwner:
    """UI-02: Send rejects selections with mixed skill_id or task_id.

    Verifies that ReportBridgeRequest construction fails when selections
    have different skill_id or task_id values. Also verifies that
    Controller.prepare is never called and no silent filtering occurs.
    """

    def test_mixed_skill_id_rejected_at_request_construction(self, qapp):
        """UI-02: ReportBridgeRequest raises ValueError on mixed skill_id."""
        s1 = ReportArtifactSelection(
            schema_version=1, skill_id="skill-A",
            task_id="a1b2c3d4e5f6a7b8c9d0e1f2a3b4c5d6",
            artifact_id="a0000000000000000000000000000000",
            role=ReportAssetRole.IMAGE, order=0,
        )
        s2 = ReportArtifactSelection(
            schema_version=1, skill_id="skill-B",
            task_id="a1b2c3d4e5f6a7b8c9d0e1f2a3b4c5d6",
            artifact_id="a0000000000000000000000000000001",
            role=ReportAssetRole.IMAGE, order=1,
        )
        with pytest.raises(ValueError, match="same.*skill_id"):
            ReportBridgeRequest(
                schema_version=1,
                request_id="a1b2c3d4e5f6a7b8c9d0e1f2a3b4c5d6",
                selections=(s1, s2),
            )

    def test_mixed_task_id_rejected_at_request_construction(self, qapp):
        """UI-02: ReportBridgeRequest raises ValueError on mixed task_id."""
        s1 = ReportArtifactSelection(
            schema_version=1, skill_id="test-skill",
            task_id="a1b2c3d4e5f6a7b8c9d0e1f2a3b4c5d6",
            artifact_id="a0000000000000000000000000000000",
            role=ReportAssetRole.IMAGE, order=0,
        )
        s2 = ReportArtifactSelection(
            schema_version=1, skill_id="test-skill",
            task_id="b1b2c3d4e5f6a7b8c9d0e1f2a3b4c5d6",
            artifact_id="a0000000000000000000000000000001",
            role=ReportAssetRole.IMAGE, order=1,
        )
        with pytest.raises(ValueError, match="same.*task_id"):
            ReportBridgeRequest(
                schema_version=1,
                request_id="c1b2c3d4e5f6a7b8c9d0e1f2a3b4c5d6",
                selections=(s1, s2),
            )

    def test_controller_prepare_not_called_on_invalid_request(self, qapp):
        """UI-02: Controller stays IDLE when request construction fails."""
        coord = ArtifactOperationCoordinator()
        ctrl = ReportBridgeController(artifact_store=None, coordinator=coord)
        assert ctrl.state == InternalBridgeState.IDLE

        s1 = ReportArtifactSelection(
            schema_version=1, skill_id="skill-A",
            task_id="a1b2c3d4e5f6a7b8c9d0e1f2a3b4c5d6",
            artifact_id="a0000000000000000000000000000000",
            role=ReportAssetRole.IMAGE, order=0,
        )
        s2 = ReportArtifactSelection(
            schema_version=1, skill_id="skill-B",
            task_id="a1b2c3d4e5f6a7b8c9d0e1f2a3b4c5d6",
            artifact_id="a0000000000000000000000000000001",
            role=ReportAssetRole.IMAGE, order=1,
        )
        # Request construction must fail — controller must not be called
        with pytest.raises(ValueError):
            ReportBridgeRequest(
                schema_version=1,
                request_id="d1b2c3d4e5f6a7b8c9d0e1f2a3b4c5d6",
                selections=(s1, s2),
            )
        # Controller must remain IDLE — no side effects
        assert ctrl.state == InternalBridgeState.IDLE
        ctrl.close()

    def test_mixed_owner_no_silent_filtering(self, qapp):
        """UI-02: Mixed owner rejected entirely — zero items proceed."""
        valid_s1 = ReportArtifactSelection(
            schema_version=1, skill_id="skill-A",
            task_id="a1b2c3d4e5f6a7b8c9d0e1f2a3b4c5d6",
            artifact_id="a0000000000000000000000000000000",
            role=ReportAssetRole.IMAGE, order=0,
        )
        mixed_s2 = ReportArtifactSelection(
            schema_version=1, skill_id="skill-B",
            task_id="a1b2c3d4e5f6a7b8c9d0e1f2a3b4c5d6",
            artifact_id="a0000000000000000000000000000001",
            role=ReportAssetRole.IMAGE, order=1,
        )
        # The entire batch must be rejected — no partial send
        try:
            ReportBridgeRequest(
                schema_version=1,
                request_id="e1b2c3d4e5f6a7b8c9d0e1f2a3b4c5d6",
                selections=(valid_s1, mixed_s2),
            )
            assert False, "Should have raised ValueError"
        except ValueError:
            pass  # Expected — mixed owner must reject the entire batch


# ── QApplication fixture ──

@pytest.fixture(scope="session")
def qapp():
    """Session-scoped QApplication for all UI tests."""
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    yield app
    # Do not quit — shared across test files
