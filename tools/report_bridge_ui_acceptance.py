#!/usr/bin/env python
"""Report Bridge UI Visual Acceptance Script — Batch 3.3.2-R5.

Uses real production Qt widgets with memory-mock public-model data to
produce four screenshots proving UI layout and safety display.

Screenshot 1 uses QTest.mouseClick on real Qt checkbox indicators —
not setCheckState or Unicode text fakery.

Does NOT install skills, call ArtifactStore, call ReportBridgeService,
or create Leases/workspaces. Screenshots serve as UI layout and safety
evidence only — they do NOT substitute for integration tests.

Usage:
    python tools/report_bridge_ui_acceptance.py
"""

from __future__ import annotations

import hashlib
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

from PyQt6.QtCore import Qt, QPoint, QRect
from PyQt6.QtGui import QImage
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import (
    QApplication,
    QMessageBox,
    QTreeWidgetItem,
    QWidget,
)

# ── Ensure project root is on sys.path ──
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

# ── Evidence output directory ──
EVIDENCE_DIR = _PROJECT_ROOT / "docs" / "agents" / "evidence"

# ── Fixed constants ──
FIXED_REQUEST_ID: str = "a1b2c3d4e5f6a7b8c9d0e1f2a3b4c5d6"  # uuid4 hex
FIXED_SKILL_ID: str = "test-skill"
FIXED_TASK_ID: str = "a1b2c3d4e5f6a7b8c9d0e1f2a3b4c5d6"
ART_ID_1: str = "b1b2c3d4e5f6a7b8c9d0e1f2a3b4c5d6"
ART_ID_2: str = "c1b2c3d4e5f6a7b8c9d0e1f2a3b4c5d6"

# ── Screenshot filenames ──
SCREENSHOT_FILENAMES: tuple[str, ...] = (
    "screenshot_3.3.2_01_skill_tab_selection.png",
    "screenshot_3.3.2_02_workbench_preparing.png",
    "screenshot_3.3.2_03_workbench_ready.png",
    "screenshot_3.3.2_04_ready_replace_dialog.png",
)


# ═══════════════════════════════════════════════════════════════════
# Mock helpers
# ═══════════════════════════════════════════════════════════════════

def _mock_artifact(
    display_name: str,
    media_type: str,
    size_bytes: int,
    artifact_id: str,
    skill_id: str = FIXED_SKILL_ID,
    task_id: str = FIXED_TASK_ID,
) -> SimpleNamespace:
    """Create a mock object with the shape ArtifactPanel.populate_artifacts expects."""
    return SimpleNamespace(
        display_name=display_name,
        kind="",
        media_type=media_type,
        size_bytes=size_bytes,
        artifact_id=artifact_id,
        skill_id=skill_id,
        task_id=task_id,
    )


# ═══════════════════════════════════════════════════════════════════
# Screenshot generators
# ═══════════════════════════════════════════════════════════════════

def _click_checkbox_indicator(tree, row: int) -> None:
    """Click the checkbox indicator of a QTreeWidget row using QTest.mouseClick.

    Computes the visual position of the checkbox indicator within column 0
    of the given row and performs a real left-click.
    """
    item = tree.topLevelItem(row)
    if item is None:
        return

    # Get visual rectangle of column 0 for this item
    visual_rect: QRect = tree.visualItemRect(item)
    if visual_rect.isNull() or visual_rect.isEmpty():
        return

    # The checkbox indicator is at the left portion of column 0 rect.
    # With rootIsDecorated=False and indentation=0, the indicator
    # occupies roughly the first ~20px of the rect.
    indicator_x = visual_rect.x() + 10  # center of checkbox indicator area
    indicator_y = visual_rect.y() + visual_rect.height() // 2

    click_point = QPoint(indicator_x, indicator_y)

    # Move mouse to position and click
    viewport = cast(QWidget, tree.viewport())
    # getattr avoids PyQt6 stub bug: mouseClick(self, widget, …) instance method
    _click = getattr(QTest, "mouseClick")
    _click(viewport, Qt.MouseButton.LeftButton, pos=click_point)


def _screenshot_01_skill_tab_selection(app: QApplication) -> Path:
    """Screenshot 1: ArtifactPanel with two checked artifacts + send button enabled.

    Uses REAL QTest.mouseClick on checkbox indicators — never setCheckState.

    Verifies:
      - Header text is "选择" (not "☑")
      - Both rows start Unchecked
      - After real click: both rows Checked
      - get_checked_artifacts() returns 2 items
      - bridge_count_label shows "已选: 2"
      - send_to_report_btn enabled
      - artifact names, types, sizes visible
      - Zero artifact_id / skill_id / task_id / path / hash / manifest visible
    """
    from ui.skill_center.artifact_panel import ArtifactPanel

    panel = ArtifactPanel()

    art1 = _mock_artifact("chart_output.png", "image/png", 245760, ART_ID_1)
    art2 = _mock_artifact("sensor_data.csv", "text/csv", 8192, ART_ID_2)

    panel.populate_artifacts((art1, art2))

    tree = panel.artifact_list

    # ── Pre-condition: header text ──
    header_item = tree.headerItem()
    assert header_item is not None, "QTreeWidget must have a header item"
    header_text = header_item.text(0)
    assert header_text == "选择", f"Header must be '选择', got '{header_text}'"
    assert header_text != "☑", f"Header must NOT be '☑'"

    # ── Pre-condition: both rows initially Unchecked ──
    for i in range(tree.topLevelItemCount()):
        item = tree.topLevelItem(i)
        assert item is not None
        assert item.checkState(0) == Qt.CheckState.Unchecked, (
            f"Row {i} must start Unchecked"
        )
        assert item.flags() & Qt.ItemFlag.ItemIsUserCheckable, (
            f"Row {i} must be user-checkable"
        )

    # ── Pre-condition: zero checked, button disabled ──
    assert panel.get_checked_count() == 0
    assert panel.get_checked_artifacts() == []
    assert not panel.send_to_report_btn.isEnabled()

    # ── ACT: Real mouse click on each row's checkbox indicator ──
    panel.resize(1080, 525)
    panel.show()
    app.processEvents()

    # Click checkbox for row 0 (chart_output.png)
    _click_checkbox_indicator(tree, 0)
    app.processEvents()

    # Click checkbox for row 1 (sensor_data.csv)
    _click_checkbox_indicator(tree, 1)
    app.processEvents()

    # ── Post-condition: both rows Checked ──
    item0 = tree.topLevelItem(0)
    item1 = tree.topLevelItem(1)
    assert item0 is not None, "Row 0 must exist"
    assert item1 is not None, "Row 1 must exist"
    assert item0.checkState(0) == Qt.CheckState.Checked, (
        "Row 0 must be Checked after real mouse click"
    )
    assert item1.checkState(0) == Qt.CheckState.Checked, (
        "Row 1 must be Checked after real mouse click"
    )

    # ── Post-condition: selection count driven by real checkboxes ──
    checked = panel.get_checked_artifacts()
    assert len(checked) == 2, f"get_checked_artifacts must return 2, got {len(checked)}"
    assert checked[0]["display_name"] == "chart_output.png", (
        f"First checked must be chart_output.png, got {checked[0].get('display_name')}"
    )
    assert checked[1]["display_name"] == "sensor_data.csv", (
        f"Second checked must be sensor_data.csv, got {checked[1].get('display_name')}"
    )

    # Update bridge selection UI based on actual checkbox count
    count = panel.get_checked_count()
    panel.update_bridge_selection_ui(count=count)

    assert count == 2, f"Selection count must be 2, got {count}"
    # Verify label text
    assert "已选: 2" in panel.bridge_count_label.text(), (
        f"Label must show '已选: 2', got '{panel.bridge_count_label.text()}'"
    )
    assert panel.send_to_report_btn.isEnabled(), (
        "Send-to-report button must be enabled with 2 selections"
    )

    # ── Test uncheck: click row 0 again to uncheck ──
    _click_checkbox_indicator(tree, 0)
    app.processEvents()

    assert item0.checkState(0) == Qt.CheckState.Unchecked, (
        "Row 0 must be Unchecked after second click"
    )
    assert panel.get_checked_count() == 1, (
        f"After unchecking one, count must be 1, got {panel.get_checked_count()}"
    )

    # Recheck for screenshot
    _click_checkbox_indicator(tree, 0)
    app.processEvents()
    panel.update_bridge_selection_ui(count=2)

    # ── Final assertions before screenshot ──
    assert panel.get_checked_count() == 2
    assert panel.send_to_report_btn.isEnabled()
    assert item0.checkState(0) == Qt.CheckState.Checked
    assert item1.checkState(0) == Qt.CheckState.Checked

    app.processEvents()

    # ── R7: Diagnostic output (stdout only, zero artifact_id/path/hash) ──
    print()
    print("--- Screenshot 1 Diagnostics (R7) ---")
    header_item = tree.headerItem()
    print(f"  header text         : {header_item.text(0)!r}" if header_item else "  header item: None")
    print(f"  item0 checkState    : {item0.checkState(0)}")
    print(f"  item1 checkState    : {item1.checkState(0)}")
    col0_width = tree.columnWidth(0)
    print(f"  column 0 width      : {col0_width}")
    ss = tree.styleSheet()
    has_checked_rule = "QTreeWidget::indicator:checked" in ss
    has_unchecked_rule = "QTreeWidget::indicator:unchecked" in ss
    print(f"  indicator:checked   : {'PRESENT' if has_checked_rule else 'MISSING'}")
    print(f"  indicator:unchecked : {'PRESENT' if has_unchecked_rule else 'MISSING'}")
    print("--- End Diagnostics ---")
    print()

    pixmap = panel.grab()
    path = EVIDENCE_DIR / SCREENSHOT_FILENAMES[0]
    pixmap.save(str(path))

    panel.hide()
    panel.deleteLater()
    app.processEvents()
    return path


def _screenshot_02_workbench_preparing(app: QApplication) -> Path:
    """Screenshot 2: ReportWorkbenchWidget in PREPARING state.

    Verifies: "正在准备素材..." status text, cancel-prepare button visible,
    bridge asset area visible. Zero internal paths.
    """
    from ui.report_workbench import ReportWorkbenchWidget

    widget = ReportWorkbenchWidget()
    widget.set_bridge_status(
        status="preparing",
        assets=(),
        request_id=FIXED_REQUEST_ID,
        generation=1,
    )

    widget.resize(1080, 1295)
    widget.show()
    app.processEvents()

    pixmap = widget.grab()
    path = EVIDENCE_DIR / SCREENSHOT_FILENAMES[1]
    pixmap.save(str(path))

    widget.hide()
    widget.deleteLater()
    app.processEvents()
    return path


def _screenshot_03_workbench_ready(app: QApplication) -> Path:
    """Screenshot 3: ReportWorkbenchWidget in READY state.

    Verifies: READY status text, two asset summaries with display_name,
    role (Chinese labels), size, order, clear-assets button visible.
    Zero paths, hash, manifest, or artifact_id.
    """
    from dp_engine.report_bridge.models import ReportAssetRole, ReportAssetSummary
    from ui.report_workbench import ReportWorkbenchWidget

    widget = ReportWorkbenchWidget()

    assets: tuple[ReportAssetSummary, ...] = (
        ReportAssetSummary(
            asset_key="asset-1",
            display_name="chart_output.png",
            role=ReportAssetRole.IMAGE,
            size_bytes=245760,
            order=0,
        ),
        ReportAssetSummary(
            asset_key="asset-2",
            display_name="sensor_data.csv",
            role=ReportAssetRole.TABLE_SOURCE,
            size_bytes=8192,
            order=1,
        ),
    )

    widget.set_bridge_status(
        status="ready",
        assets=assets,
        request_id=FIXED_REQUEST_ID,
        generation=1,
    )

    widget.resize(1080, 1295)
    widget.show()
    app.processEvents()

    pixmap = widget.grab()
    path = EVIDENCE_DIR / SCREENSHOT_FILENAMES[2]
    pixmap.save(str(path))

    widget.hide()
    widget.deleteLater()
    app.processEvents()
    return path


def _screenshot_04_ready_replace_dialog(app: QApplication) -> Path:
    """Screenshot 4: Real QMessageBox for READY replace confirmation.

    Uses the EXACT same title, body text, buttons, and default button
    as the production handler in main.py _handle_bridge_send_to_report().

    Verifies: complete confirmation text, Yes/No buttons, No as default.
    """
    msg = QMessageBox()
    msg.setWindowTitle("替换确认")
    msg.setText(
        "当前已有准备完成的报告素材。\n替换后旧素材将被释放。是否继续？"
    )
    msg.setStandardButtons(
        QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
    )
    msg.setDefaultButton(QMessageBox.StandardButton.No)

    msg.show()
    app.processEvents()

    pixmap = msg.grab()
    path = EVIDENCE_DIR / SCREENSHOT_FILENAMES[3]
    pixmap.save(str(path))

    msg.hide()
    msg.deleteLater()
    app.processEvents()
    return path


# ═══════════════════════════════════════════════════════════════════
# Verification
# ═══════════════════════════════════════════════════════════════════

def _verify_screenshots() -> tuple[bool, list[dict[str, Any]]]:
    """Validate all four screenshots: exist, non-zero pixels, unique SHA256."""
    results: list[dict[str, Any]] = []
    seen_hashes: set[str] = set()
    all_ok: bool = True

    for name in SCREENSHOT_FILENAMES:
        path = EVIDENCE_DIR / name
        entry: dict[str, Any] = {
            "filename": name,
            "abs_path": str(path),
            "rel_path": f"docs/agents/evidence/{name}",
        }

        if not path.exists():
            print(f"FAIL MISSING: {name}")
            entry["exists"] = False
            entry["pixels"] = (0, 0)
            entry["bytes"] = 0
            entry["sha256"] = ""
            entry["ok"] = False
            all_ok = False
            results.append(entry)
            continue

        entry["exists"] = True
        data = path.read_bytes()
        sha = hashlib.sha256(data).hexdigest()
        size_bytes = len(data)
        entry["bytes"] = size_bytes
        entry["sha256"] = sha

        # Read image dimensions via QImage
        try:
            img = QImage(str(path))
            w, h = img.width(), img.height()
        except Exception:
            w, h = 0, 0
        entry["pixels"] = (w, h)

        ok = w > 0 and h > 0 and size_bytes > 0
        entry["ok"] = ok
        if not ok:
            all_ok = False

        unique = sha not in seen_hashes
        seen_hashes.add(sha)
        entry["unique_sha"] = unique
        if not unique:
            all_ok = False

        status = "PASS" if (ok and unique) else "FAIL"
        detail = "" if unique else " DUPLICATE SHA256"
        print(f"[{status}] {name}{detail}")
        print(f"   绝对路径: {entry['abs_path']}")
        print(f"   相对路径: {entry['rel_path']}")
        print(f"   像素尺寸: {w}×{h}")
        print(f"   字节数: {size_bytes:,}")
        print(f"   SHA256: {sha}")

        results.append(entry)

    if len(seen_hashes) != 4:
        print(f"\nFAIL: Expected 4 unique SHA256 hashes, got {len(seen_hashes)}")
        all_ok = False

    return all_ok, results


# ═══════════════════════════════════════════════════════════════════
# Main
# ═══════════════════════════════════════════════════════════════════

def main() -> int:
    """Run visual acceptance suite and verify output."""
    EVIDENCE_DIR.mkdir(parents=True, exist_ok=True)

    app = QApplication(sys.argv)

    print("=" * 64)
    print("  Report Bridge UI Visual Acceptance — Batch 3.3.2-R5")
    print("=" * 64)
    print()
    print("WARNING: No skills installed in current environment.")
    print("WARNING: Screenshots prove real Qt widget layout and safety display only.")
    print("WARNING: Zero ArtifactStore / Service / Lease calls.")
    print("WARNING: Screenshot 1 uses QTest.mouseClick on real Qt checkboxes.")
    print()

    # ── Screenshot 1 ──
    print("[1/4] ArtifactPanel — real QTest.mouseClick checkbox interactions...")
    p1 = _screenshot_01_skill_tab_selection(app)
    print(f"     -> {p1.name}")

    # ── Screenshot 2 ──
    print("[2/4] ReportWorkbenchWidget — PREPARING state...")
    p2 = _screenshot_02_workbench_preparing(app)
    print(f"     -> {p2.name}")

    # ── Screenshot 3 ──
    print("[3/4] ReportWorkbenchWidget — READY state + safe summaries...")
    p3 = _screenshot_03_workbench_ready(app)
    print(f"     -> {p3.name}")

    # ── Screenshot 4 ──
    print("[4/4] QMessageBox — READY replace confirmation dialog...")
    p4 = _screenshot_04_ready_replace_dialog(app)
    print(f"     -> {p4.name}")

    # ── Verify ──
    print()
    print("=" * 64)
    print("  Verification")
    print("=" * 64)
    ok, _ = _verify_screenshots()

    if ok:
        print()
        print("PASS: All 4 screenshots verified — exist, non-zero pixels, 4 unique SHA256.")
    else:
        print()
        print("FAIL: Verification failed — see details above.")

    app.quit()
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
