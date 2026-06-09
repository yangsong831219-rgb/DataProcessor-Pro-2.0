"""应变标定读数表 — Ctrl+V 粘贴测试 (qtbot 真实剪贴板 + QTest.keyClick)

测试:
1. test_paste_displacement_block — 剪贴板 3 行位移 → Ctrl+V → 位移填入 + 应变自动算 + 备注行不脏
2. test_paste_readings_block — 剪贴板波长读数 → Ctrl+V → 填正确行
3. test_paste_does_not_touch_annotation_row — 粘贴后 row 0 暗号下拉不变
4. test_single_cell_paste — 单格粘贴仍工作
5. test_paste_tab_separated — Tab 分隔多列粘贴 (Excel 复制场景)
"""

from __future__ import annotations

import pytest
import sys
import os

import numpy as np
from PyQt6.QtWidgets import QApplication, QTableWidgetItem
from PyQt6.QtCore import Qt
from PyQt6.QtTest import QTest


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance()
    if app is None:
        app = QApplication(sys.argv)
    yield app


# ═══════════════════════════════════════════════════════════════════════
# Helpers
# ═══════════════════════════════════════════════════════════════════════

def _build_test_table(page, n_levels=3):
    """构造模拟的 _StrainPasteTable + 备注行，返回 (table, combos_by_grating)。"""
    from ui.calibration_tab import _StrainPasteTable, _ComboPasteRedirect
    from PyQt6.QtWidgets import QComboBox
    from PyQt6.QtGui import QColor

    page._config.update({
        "gauge_length_mm": 80.0, "mode": "tension_only",
        "n_cycles": 1, "grating_kind": "single",
    })
    page._levels = page._generate_default_levels(n=n_levels)
    cols = page._build_table_columns()

    table = _StrainPasteTable(len(page._levels) + 1, len(cols))
    table._paste_gauge_mm = page._config["gauge_length_mm"]
    table.setHorizontalHeaderLabels(cols)
    table.cellChanged.connect(page._on_strain_cell_changed)
    page._dialog_table = table
    page._populate_table(table)

    # 备注行 (row 0): QComboBox
    page._annotation_combos = {}
    grating_cols = page._grating_col_indices()
    combos = {}
    for gi, col_indices in grating_cols.items():
        if not col_indices:
            continue
        combo = QComboBox()
        combo.setEditable(True)
        combo.addItems(["A1-W1", "A1-W2", "B1-W1"])
        combo.setCurrentText(f"A1-W{gi}")
        table.setCellWidget(0, col_indices[0], combo)
        combo.installEventFilter(_ComboPasteRedirect(table, combo))
        page._annotation_combos[gi] = combo
        combos[gi] = combo
        for other_col in col_indices[1:]:
            gray_item = QTableWidgetItem("—")
            gray_item.setFlags(Qt.ItemFlag.NoItemFlags)
            gray_item.setBackground(QColor(230, 230, 230))
            table.setItem(0, other_col, gray_item)

    return table, combos, cols


# ═══════════════════════════════════════════════════════════════════════
# Test 1: 位移列整块粘贴
# ═══════════════════════════════════════════════════════════════════════

class TestPasteDisplacementBlock:

    def test_paste_displacement_block(self, qapp):
        """剪贴板 3 行位移值 → 点 row 1 col 0 → Ctrl+V → 位移填入 + 应变自动计算"""
        from ui.calibration_tab import StrainCalibrationPage

        page = StrainCalibrationPage()
        table, combos, cols = _build_test_table(page, n_levels=5)

        # 设剪贴板
        qapp.clipboard().setText("0.008\n0.016\n0.024")
        # 点击 row 1 (第一个数据行) col 0 (位移列)
        table.setCurrentCell(1, 0)
        table.setFocus()

        # Ctrl+V
        QTest.keyClick(table, Qt.Key.Key_V, Qt.KeyboardModifier.ControlModifier)

        # 断言: 位移正确填入
        assert abs(float(table.item(1, 0).text()) - 0.008) < 0.001
        assert abs(float(table.item(2, 0).text()) - 0.016) < 0.001
        assert abs(float(table.item(3, 0).text()) - 0.024) < 0.001

        # 断言: 应变自动计算 (disp / 80mm × 1e6)
        assert abs(float(table.item(1, 1).text()) - 100.0) < 2.0   # 0.008/80*1e6=100
        assert abs(float(table.item(2, 1).text()) - 200.0) < 2.0   # 0.016/80*1e6=200
        assert abs(float(table.item(3, 1).text()) - 300.0) < 2.0   # 0.024/80*1e6=300

        # 断言: 备注行未被污染
        combo_text = combos[1].currentText()
        assert combo_text == "A1-W1", f"Combo text changed: {combo_text}"

        # 断言: row 4, 5 未被覆盖 (只有 3 行剪贴板)
        disp4 = table.item(4, 0)
        assert disp4 is not None  # 原有数据行应该还在

    def test_paste_displacement_from_row0_skips(self, qapp):
        """即使用户点了备注行 row 0，粘贴也应从 row 1 开始"""
        from ui.calibration_tab import StrainCalibrationPage

        page = StrainCalibrationPage()
        table, combos, cols = _build_test_table(page, n_levels=5)

        qapp.clipboard().setText("0.040")
        # 点 row 0 (备注行)
        table.setCurrentCell(0, 0)
        table.setFocus()

        QTest.keyClick(table, Qt.Key.Key_V, Qt.KeyboardModifier.ControlModifier)

        # 断言: row 0 没被写入 (备注行保护)
        combo_text = combos[1].currentText()
        assert combo_text == "A1-W1", f"Row 0 combo corrupted: {combo_text}"

        # 断言: 数据写入了 row 1 (跳过 row 0)
        item = table.item(1, 0)
        assert item is not None
        assert abs(float(item.text()) - 0.040) < 0.001

    def test_paste_displacement_calc_uses_correct_gauge(self, qapp):
        """粘贴位移后的应变计算使用正确标距"""
        from ui.calibration_tab import StrainCalibrationPage

        page = StrainCalibrationPage()
        table, combos, cols = _build_test_table(page, n_levels=5)
        # 改标距为 50mm
        page._config["gauge_length_mm"] = 50.0
        table._paste_gauge_mm = 50.0

        qapp.clipboard().setText("0.005")
        table.setCurrentCell(1, 0)
        table.setFocus()
        QTest.keyClick(table, Qt.Key.Key_V, Qt.KeyboardModifier.ControlModifier)

        # 0.005 / 50.0 * 1e6 = 100 με
        assert abs(float(table.item(1, 0).text()) - 0.005) < 0.001
        assert abs(float(table.item(1, 1).text()) - 100.0) < 2.0


# ═══════════════════════════════════════════════════════════════════════
# Test 2: 读数列整块粘贴
# ═══════════════════════════════════════════════════════════════════════

class TestPasteReadingsBlock:

    def test_paste_readings_block(self, qapp):
        """剪贴板波长读数 → 点 G1_C1 数据格 → Ctrl+V → 整块填入"""
        from ui.calibration_tab import StrainCalibrationPage

        page = StrainCalibrationPage()
        table, combos, cols = _build_test_table(page, n_levels=5)

        qapp.clipboard().setText("1550.123\n1550.246\n1550.369")
        # col 2 = G1_C1_张拉
        table.setCurrentCell(1, 2)
        table.setFocus()

        QTest.keyClick(table, Qt.Key.Key_V, Qt.KeyboardModifier.ControlModifier)

        assert abs(float(table.item(1, 2).text()) - 1550.123) < 0.01
        assert abs(float(table.item(2, 2).text()) - 1550.246) < 0.01
        assert abs(float(table.item(3, 2).text()) - 1550.369) < 0.01

        # 备注行未被污染
        assert combos[1].currentText() == "A1-W1"


# ═══════════════════════════════════════════════════════════════════════
# Test 3: 备注行保护
# ═══════════════════════════════════════════════════════════════════════

class TestPasteAnnotationRowProtected:

    def test_paste_does_not_touch_annotation_row(self, qapp):
        """任何时候粘贴都不污染 row 0 备注行的暗号下拉值"""
        from ui.calibration_tab import StrainCalibrationPage

        page = StrainCalibrationPage()
        table, combos, cols = _build_test_table(page, n_levels=5)

        # 先从 row 1 粘贴位移
        qapp.clipboard().setText("0.008\n0.016")
        table.setCurrentCell(1, 0)
        table.setFocus()
        QTest.keyClick(table, Qt.Key.Key_V, Qt.KeyboardModifier.ControlModifier)

        assert combos[1].currentText() == "A1-W1"

        # 再从 row 2 粘贴读数
        qapp.clipboard().setText("1550.5\n1550.6")
        table.setCurrentCell(2, 2)
        table.setFocus()
        QTest.keyClick(table, Qt.Key.Key_V, Qt.KeyboardModifier.ControlModifier)

        assert combos[1].currentText() == "A1-W1"

    def test_combo_focus_paste_via_table(self, qapp):
        """即使 ComboBox 有焦点，Ctrl+V 发送到表格时数据写入正确，备注行不脏"""
        from ui.calibration_tab import StrainCalibrationPage

        page = StrainCalibrationPage()
        table, combos, cols = _build_test_table(page, n_levels=5)

        # 让 ComboBox 获得焦点 (模拟用户点了下拉未回车)
        combos[1].setFocus()
        original_text = combos[1].currentText()

        # 用户发现焦点在下拉，点回表格 → 表格获得焦点 → Ctrl+V
        table.setCurrentCell(1, 0)
        table.setFocus()

        qapp.clipboard().setText("0.050")
        QTest.keyClick(table, Qt.Key.Key_V, Qt.KeyboardModifier.ControlModifier)

        # ComboBox 文本应不变
        assert combos[1].currentText() == original_text

        # 数据应写入 row 1
        item = table.item(1, 0)
        assert item is not None and abs(float(item.text()) - 0.050) < 0.001


# ═══════════════════════════════════════════════════════════════════════
# Test 4: 单格粘贴
# ═══════════════════════════════════════════════════════════════════════

class TestSingleCellPaste:

    def test_single_cell_paste_selected_state(self, qapp):
        """选中态单行粘贴：剪贴板 1 行 → 当前格填入 + 应变自动算"""
        from ui.calibration_tab import StrainCalibrationPage

        page = StrainCalibrationPage()
        table, combos, cols = _build_test_table(page, n_levels=5)

        # 选中 row 2 col 0（非编辑态），剪贴板 1 行
        table.setCurrentCell(2, 0)
        table.setFocus()

        qapp.clipboard().setText("0.032")
        QTest.keyClick(table, Qt.Key.Key_V, Qt.KeyboardModifier.ControlModifier)

        # 单行写入 row 2
        assert abs(float(table.item(2, 0).text()) - 0.032) < 0.001
        # 应变自动计算: 0.032/80*1e6=400 με
        assert abs(float(table.item(2, 1).text()) - 400.0) < 2.0


# ═══════════════════════════════════════════════════════════════════════
# Test 5: Tab 分隔多列粘贴 (Excel 复制场景)
# ═══════════════════════════════════════════════════════════════════════

class TestPasteTabSeparated:

    def test_paste_tab_separated(self, qapp):
        """从 Excel 复制的 Tab 分隔多列数据 → Ctrl+V → 多列同时填入"""
        from ui.calibration_tab import StrainCalibrationPage

        page = StrainCalibrationPage()
        table, combos, cols = _build_test_table(page, n_levels=5)

        # 模拟从 Excel 复制的数据: 位移 + 波长
        qapp.clipboard().setText("0.008\t1550.123\n0.016\t1550.246\n0.024\t1550.369")
        # 从 row 1 col 0 开始
        table.setCurrentCell(1, 0)
        table.setFocus()

        QTest.keyClick(table, Qt.Key.Key_V, Qt.KeyboardModifier.ControlModifier)

        # col 0: 位移 (应变自动算)
        assert abs(float(table.item(1, 0).text()) - 0.008) < 0.001
        assert abs(float(table.item(2, 0).text()) - 0.016) < 0.001
        assert abs(float(table.item(3, 0).text()) - 0.024) < 0.001
        # col 1: 应变
        assert abs(float(table.item(1, 1).text()) - 100.0) < 2.0
        # col 2: 波长
        assert abs(float(table.item(1, 2).text()) - 1550.123) < 0.01
        assert abs(float(table.item(2, 2).text()) - 1550.246) < 0.01
        assert abs(float(table.item(3, 2).text()) - 1550.369) < 0.01

        # 备注行未污染
        assert combos[1].currentText() == "A1-W1"
