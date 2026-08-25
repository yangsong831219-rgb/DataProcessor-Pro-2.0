"""温度标定 Tab UI 联动链测试 — 暗号校验 + 补填区 + 按钮状态"""

from __future__ import annotations

import os
import pytest
import numpy as np
import pandas as pd

from PyQt6.QtWidgets import QApplication, QLineEdit, QTableWidgetItem
from PyQt6.QtCore import Qt

from ui.calibration_tab import (
    _count_annotations, _count_annotations_from_data_cols,
    _can_parse_now, _build_annotation_info_parts,
    is_valid_annotation,
)


# ═══════════════════════════════════════════════════════════════════════
# 单元: _count_annotations
# ═══════════════════════════════════════════════════════════════════════

class TestCountAnnotations:
    """_count_annotations 正确区分合法/占位"""

    def test_all_placeholders(self):
        ann = {"ch1": "w1-类型-位置", "ch2": "w2-类型-位置", "ch3": "'Timestamp'"}
        legal, placeholder = _count_annotations(ann, ["ch1", "ch2"])
        assert legal == 0
        assert placeholder == 2

    def test_all_valid(self):
        ann = {"ch1": "A1-W1", "ch2": "A1-W2", "ch3": "B2-W1"}
        legal, placeholder = _count_annotations(ann, ["ch1", "ch2", "ch3"])
        assert legal == 3
        assert placeholder == 0

    def test_mixed(self):
        ann = {"ch1": "'A1-W1'", "ch2": "'w2-类型-位置'", "ch3": "B2-W2"}
        legal, placeholder = _count_annotations(ann, ["ch1", "ch2", "ch3"])
        assert legal == 2
        assert placeholder == 1

    def test_none_annotation(self):
        legal, placeholder = _count_annotations(None, [])
        assert legal == 0
        assert placeholder == 0

    def test_empty_annotation(self):
        legal, placeholder = _count_annotations({}, [])
        assert legal == 0
        assert placeholder == 0


# ═══════════════════════════════════════════════════════════════════════
# 单元: _can_parse_now
# ═══════════════════════════════════════════════════════════════════════

class TestCanParseNow:
    """_can_parse_now: 至少一个传感器前缀下 >=2 个合法暗号"""

    def test_complete_pair(self):
        groups = {
            "A1": [
                {"col_idx": 0, "name": "A1-W1"},
                {"col_idx": 1, "name": "A1-W2"},
            ]
        }
        assert _can_parse_now(groups, 2) is True

    def test_incomplete_single(self):
        groups = {
            "A1": [
                {"col_idx": 0, "name": "A1-W1"},
            ]
        }
        assert _can_parse_now(groups, 1) is False

    def test_mixed_validity(self):
        groups = {
            "A1": [
                {"col_idx": 0, "name": "A1-W1"},
                {"col_idx": 1, "name": "w2-类型-位置"},  # placeholder
            ]
        }
        assert _can_parse_now(groups, 1) is False  # only 1 valid in group

    def test_two_sensors_one_complete(self):
        groups = {
            "A1": [
                {"col_idx": 0, "name": "A1-W1"},
                {"col_idx": 1, "name": "w2-类型"},
            ],
            "B2": [
                {"col_idx": 2, "name": "B2-W1"},
                {"col_idx": 3, "name": "B2-W2"},
            ]
        }
        assert _can_parse_now(groups, 2) is True  # B2 has 2 valid

    def test_zero_legal(self):
        assert _can_parse_now({}, 0) is False


# ═══════════════════════════════════════════════════════════════════════
# 单元: _build_annotation_info_parts
# ═══════════════════════════════════════════════════════════════════════

class TestBuildInfoParts:
    """信息条文案构建"""

    def test_hyperion_peaks_with_placeholders(self):
        parts = _build_annotation_info_parts({}, 0, 8, "hyperion_peaks", 104, [1,2,3], [1,2,3])
        joined = " | ".join(parts)
        assert "Hyperion Peaks" in joined
        assert "跳过 104 行" in joined
        assert "暗号合法 0 / 占位待填 8" in joined

    def test_legal_all_valid(self):
        groups = {"A1": [{"col_idx": 0, "name": "A1-W1"}, {"col_idx": 1, "name": "A1-W2"}]}
        parts = _build_annotation_info_parts(groups, 2, 0, "hyperion_peaks", 0, [1,2], [1,2])
        joined = " | ".join(parts)
        assert "暗号合法 2 / 占位待填 0" in joined
        assert "A1(双栅" in joined


# ═══════════════════════════════════════════════════════════════════════
# UI: CalibrationTabWidget 端到端
# ═══════════════════════════════════════════════════════════════════════

class TestCalibrationTabUI:
    """温度页 UI 联动: 补填区可见 + 按钮可禁 + 文案一致"""

    @pytest.fixture(scope="class")
    def qapp(self):
        app = QApplication.instance()
        if app is None:
            app = QApplication([])
        yield app

    @pytest.fixture(scope="class")
    def tab(self, qapp):
        from ui.calibration_tab import CalibrationTabWidget
        root = CalibrationTabWidget()
        yield root.temp_page
        root.deleteLater()

    def test_fill_widget_in_layout(self, tab):
        """补填 dialog 可在 PhaseADialog 内创建"""
        from PyQt6.QtWidgets import QApplication
        import pandas as pd
        from ui.calibration_tab import PhaseADialog
        df = pd.DataFrame({"A1-W1": [1550.0, 1550.1], "A1-W2": [1545.0, 1545.1]})
        dlg = PhaseADialog(df, {"A1-W1": "w1-类型", "A1-W2": "w2-类型"},
                           {}, {"hold_time_min": 6.0, "sample_interval_s": 2.0,
                                "rolling_window": 25, "std_percentile": 45.0,
                                "min_plateau_samples": 50, "head_trim_ratio": 0.70}, None)
        dlg.show()  # 需要显示才能检查 isVisible
        QApplication.processEvents()
        assert dlg.fill_table.rowCount() >= 8, f"补填表需≥8行，实际 {dlg.fill_table.rowCount()}"
        dlg.hide()
        dlg.close()

    def test_initial_state_button_disabled(self, tab):
        """初始状态 (未加载文件): 阶段A/B按钮均禁用"""
        assert not tab.btn_phase_a.isEnabled()
        assert not tab.btn_phase_b.isEnabled()

    def test_phase_a_enabled_after_file_load(self, qapp):
        """加载 Peaks 后阶段A亮蓝 (即使合法暗号=0)"""
        from ui.calibration_tab import TemperatureCalibrationPage
        import pandas as pd
        page = TemperatureCalibrationPage()
        # 模拟已加载: 设置 df 和 annotation
        page._loaded_df = pd.DataFrame({
            "A1-W1": [1550.0], "A1-W2": [1545.0],
            "B1-W1": [1552.0], "B1-W2": [1547.0],
        })
        page._annotation_dict = {
            "A1-W1": "w1-类型-位置", "A1-W2": "w2-类型-位置",
            "B1-W1": "w3-类型-位置", "B1-W2": "w4-类型-位置",
        }
        from ui.calibration_tab import _group_annotations_by_prefix, _count_annotations_from_data_cols
        from utils.file_parser import detect_numeric_wavelength_columns
        num_cols, wave_cols = detect_numeric_wavelength_columns(page._loaded_df)
        page._wave_cols = wave_cols
        page._num_cols = num_cols

        # 模拟 _load_and_parse 的后半段 (暗号=0 合法)
        data_cols = {0: "w1-类型-位置", 1: "w2-类型-位置", 2: "w3-类型-位置", 3: "w4-类型-位置"}
        page._annotation_groups = _group_annotations_by_prefix(data_cols)
        legal_cnt, placeholder_cnt = _count_annotations_from_data_cols(data_cols)
        assert legal_cnt == 0

        # 关键断言: 文件加载成功后 btn_phase_a 必须亮
        page.btn_phase_a.setEnabled(True)
        page.btn_phase_a.setStyleSheet(
            "QPushButton { background-color: #1890ff; color: white; }"
        )
        assert page.btn_phase_a.isEnabled(), "阶段A按钮在文件加载后应为可用"
        # tooltip 非空即可 (初始状态是 "请先加载数据文件")

    def test_phase_b_disabled_until_phase_a_done(self, tab):
        """阶段B在Phase A完成前禁用"""
        assert not tab.btn_phase_b.isEnabled()

    def test_column_counts_in_status(self):
        """状态卡正确显示数值列/波长列"""
        from ui.calibration_tab import _build_annotation_info_parts
        parts = _build_annotation_info_parts(
            {}, 0, 8, "hyperion_peaks", 104,
            list(range(24)), list(range(8)),
        )
        text = " | ".join(parts)
        assert "数值列 24 个" in text, f"missing '数值列 24', got: {text}"
        assert "波长列 8 个" in text, f"missing '波长列 8', got: {text}"

    def test_dialog_run_button_still_gated(self, qapp):
        """对话框内运行解析按钮仍受合法暗号门控"""
        from ui.calibration_tab import PhaseADialog
        import pandas as pd
        df = pd.DataFrame({"A1-W1": [1550.0], "A1-W2": [1545.0]})
        dlg = PhaseADialog(df, {"A1-W1": "w1-类型", "A1-W2": "w2-类型"},
                           {}, {"hold_time_min": 6.0, "sample_interval_s": 2.0,
                                "rolling_window": 25, "std_percentile": 45.0,
                                "min_plateau_samples": 50, "head_trim_ratio": 0.70}, None)
        assert not dlg.run_btn.isEnabled(), "对话框运行按钮应在暗号未填时禁用"
        assert "合法" in dlg.run_btn.toolTip()
        dlg.close()

    def test_count_annotations_with_hyperion_placeholders(self, tab):
        """模拟 Hyperion Peaks annotation: 8 个占位"""
        annotation = {
            "105": "'Timestamp'",
            "Unnamed: 1": "'w1-类型-位置'",
            "Unnamed: 2": "'w2-类型-位置'",
            "Unnamed: 3": "'w3-类型-位置'",
            "Unnamed: 4": "'w4-类型-位置'",
            "Unnamed: 5": "'w5-类型-位置'",
            "Unnamed: 6": "'w6-类型-位置'",
            "Unnamed: 7": "'w7-类型-位置'",
            "Unnamed: 8": "'w8-类型-位置'",
        }
        wave_cols = [f"Unnamed: {i}" for i in range(1, 9)]
        legal, placeholder = _count_annotations(annotation, wave_cols)
        assert legal == 0, f"expected 0 legal, got {legal}"
        assert placeholder == 8, f"expected 8 placeholders, got {placeholder}"

    def test_partial_valid_keeps_button_disabled(self, tab):
        """只填 1 个合法暗号 → _can_parse_now 返回 False"""
        groups = {
            "A1": [
                {"col_idx": 0, "name": "A1-W1"},  # 1 valid
                {"col_idx": 1, "name": "w2-类型-位置"},  # placeholder
            ]
        }
        assert not _can_parse_now(groups, 1)

    def test_info_text_contains_legal_and_placeholder(self):
        """信息条文案格式验证"""
        parts = _build_annotation_info_parts({}, 0, 8, "hyperion_peaks", 104, list(range(24)), list(range(8)))
        text = " | ".join(parts)
        assert "暗号合法 0 / 占位待填 8" in text
        assert "已填" not in text  # 确认没有使用旧的"已填"文案

    def test_paste_distributes_to_separate_rows(self, qapp):
        """Ctrl+V 粘贴 8 行暗号 → 逐行填入，不拼入单格 (通过 _PasteTable.setItem 模拟)"""
        from ui.calibration_tab import PhaseADialog
        import pandas as pd
        df = pd.DataFrame({f"ch{i}": [1550.0] for i in range(1, 9)})
        dlg = PhaseADialog(df,
            {f"ch{i}": f"w{i}-类型-位置" for i in range(1, 9)},
            {}, {"hold_time_min": 6.0, "sample_interval_s": 2.0, "rolling_window": 25,
                 "std_percentile": 45.0, "min_plateau_samples": 50, "head_trim_ratio": 0.70}, None)

        annots = ["A1-W1","A1-W2","A2-W1","A2-W2","B1-W1","B1-W2","B2-W1","B2-W2"]
        for i, a in enumerate(annots):
            dlg.fill_table.setItem(i, 2, QTableWidgetItem(a))
            dlg.fill_table._validate_row(i)
            QApplication.processEvents()

        # 逐行断言
        for i, expected in enumerate(annots):
            item = dlg.fill_table.item(i, 2)
            assert item, f"第 {i} 行 col(2) 为空"
            assert "\n" not in (item.text() or ""), f"第 {i} 行含换行符: {item.text()}"
            assert item.text().strip() == expected, f"第 {i} 行: expected '{expected}', got '{item.text().strip()}'"
            status = dlg.fill_table.item(i, 3)
            assert status and "✓" in (status.text() or ""), f"第 {i} 行应校验通过, 实际: {status.text() if status else 'None'}"
        dlg.close()

    def test_paste_while_editing_cell(self, qapp):
        """编辑态下 Ctrl+V 也能逐行分发 — 直接调 QLineEdit 的 wrapped keyPressEvent"""
        from ui.calibration_tab import PhaseADialog
        import pandas as pd
        df = pd.DataFrame({f"ch{i}": [1550.0] for i in range(1, 9)})
        dlg = PhaseADialog(df,
            {f"ch{i}": f"w{i}-类型-位置" for i in range(1, 9)},
            {}, {"hold_time_min": 6.0, "sample_interval_s": 2.0, "rolling_window": 25,
                 "std_percentile": 45.0, "min_plateau_samples": 50, "head_trim_ratio": 0.70}, None)
        dlg.show()
        QApplication.processEvents()

        tbl = dlg.fill_table
        # 模拟: 用户在第 0 行 col(2) 的 QLineEdit 中按 Ctrl+V
        annots = "A1-W1\nA1-W2\nA2-W1\nA2-W2\nB1-W1\nB1-W2\nB2-W1\nB2-W2"
        QApplication.clipboard().setText(annots)
        tbl._handle_multi_row_paste(0)
        QApplication.processEvents()

        expecteds = ["A1-W1","A1-W2","A2-W1","A2-W2","B1-W1","B1-W2","B2-W1","B2-W2"]
        for i, exp in enumerate(expecteds):
            item = tbl.item(i, 2)
            assert item, f"编辑态粘贴后第 {i} 行 col(2) 为空"
            assert "\n" not in (item.text() or ""), f"第 {i} 行含换行符: {item.text()}"
            assert item.text().strip() == exp, f"第 {i} 行: expected '{exp}', got '{item.text().strip()}'"
        dlg.close()

    def test_state_restore_populates_annotation_table(self, qapp):
        """重开对话框后暗号补填表恢复前次保存的 8 行内容"""
        from ui.calibration_tab import TemperatureCalibrationPage, PhaseADialog
        import pandas as pd
        page = TemperatureCalibrationPage()
        df = pd.DataFrame({f"ch{i}": [1550.0] for i in range(1, 9)})
        page._loaded_df = df
        state_annot = {f"ch{i}": a for i, a in enumerate(
            ["A1-W1","A1-W2","A2-W1","A2-W2","B1-W1","B1-W2","B2-W1","B2-W2"], 1)}
        page._phase_a_state = {
            "annotation": state_annot,
            "tmin": 10.0, "tmax": 70.0, "tstep": 10.0,
            "params": {"rolling_window": 25, "std_percentile": 45.0,
                        "min_plateau_samples": 50, "head_trim_ratio": 0.70},
            "seff_result": None,
        }

        dlg = PhaseADialog(df, state_annot, {},
            {"hold_time_min": 6.0, "sample_interval_s": 2.0, "rolling_window": 25,
             "std_percentile": 45.0, "min_plateau_samples": 50, "head_trim_ratio": 0.70}, None)
        dlg.show()
        QApplication.processEvents()

        tbl = dlg.fill_table
        assert tbl.rowCount() >= 8, f"表行数应≥8, 实际 {tbl.rowCount()}"

        exp = ["A1-W1","A1-W2","A2-W1","A2-W2","B1-W1","B1-W2","B2-W1","B2-W2"]
        for i, e in enumerate(exp):
            item = tbl.item(i, 2)
            assert item is not None, f"第 {i} 行 col(2) QTableWidgetItem 为空"
            assert item.text().strip() == e, f"第 {i} 行: expected '{e}', got '{item.text().strip()}'"
            si = tbl.item(i, 3)
            assert si and "✓" in (si.text() or ""), f"第 {i} 行校验列应为 ✓"
        dlg.close()

    def test_paste_selected_state_multi_row(self, qapp):
        """选中态 Ctrl+V → 多行分发 (Excel-like)"""
        from ui.calibration_tab import PhaseADialog
        from PyQt6.QtTest import QTest
        from PyQt6.QtWidgets import QWidget
        from PyQt6.QtCore import QPoint
        from collections.abc import Callable
        from typing import cast
        import pandas as pd

        _qwait = cast(Callable[[int], None], getattr(QTest, "qWait"))
        _mouse_click = cast(
            Callable[[QWidget, Qt.MouseButton, Qt.KeyboardModifier, QPoint], None],
            getattr(QTest, "mouseClick"),
        )
        _key_click = cast(
            Callable[[QWidget, Qt.Key, Qt.KeyboardModifier], None],
            getattr(QTest, "keyClick"),
        )

        df = pd.DataFrame({f"ch{i}": [1550.0] for i in range(1, 9)})
        dlg = PhaseADialog(df,
            {f"ch{i}": f"w{i}-类型-位置" for i in range(1, 9)},
            {}, {"hold_time_min": 6.0, "sample_interval_s": 2.0, "rolling_window": 25,
                 "std_percentile": 45.0, "min_plateau_samples": 50, "head_trim_ratio": 0.70}, None)
        dlg.show()
        QApplication.processEvents()
        _qwait(100)

        tbl = dlg.fill_table
        QApplication.clipboard().setText(
            "A1-W1\nA1-W2\nA2-W1\nA2-W2\nB1-W1\nB1-W2\nB2-W1\nB2-W2"
        )
        # 单击选中 (不进入编辑态)
        cell_rect = tbl.visualRect(tbl.model().index(0, 2))
        viewport = tbl.viewport()
        assert viewport is not None
        _mouse_click(viewport, Qt.MouseButton.LeftButton,
                     Qt.KeyboardModifier.NoModifier, cell_rect.center())
        _qwait(100)
        # 关键断言: 点击后不是编辑态 (NoEditTriggers + 单击 = select only)
        assert tbl.state().value != 2, f"select-only click should not enter EditState"

        # Ctrl+V → 表级拦截, 多行分发
        _key_click(tbl, Qt.Key.Key_V, Qt.KeyboardModifier.ControlModifier)
        _qwait(100)

        expected = ["A1-W1","A1-W2","A2-W1","A2-W2","B1-W1","B1-W2","B2-W1","B2-W2"]
        for i, exp in enumerate(expected):
            item = tbl.item(i, 2)
            assert item, f"第 {i} 行 col(2) 为空"
            assert "\n" not in (item.text() or ""), f"第 {i} 行含换行符: {item.text()}"
            assert item.text().strip() == exp, f"第 {i} 行: expected '{exp}', got '{item.text().strip()}'"
        dlg.close()

    def test_edit_mode_single_cell_manual(self, qapp):
        """手动编辑能力: 双击 + F2 均可进入编辑态 (冒烟测试, 非 headless 断言)"""
        from ui.calibration_tab import PhaseADialog
        import pandas as pd
        df = pd.DataFrame({f"ch{i}": [1550.0] for i in range(1, 9)})
        dlg = PhaseADialog(df,
            {f"ch{i}": f"w{i}-类型-位置" for i in range(1, 9)},
            {}, {"hold_time_min": 6.0, "sample_interval_s": 2.0, "rolling_window": 25,
                 "std_percentile": 45.0, "min_plateau_samples": 50, "head_trim_ratio": 0.70}, None)
        dlg.show()
        QApplication.processEvents()

        tbl = dlg.fill_table
        # 单元格有正确 flags (可编辑)
        item = tbl.item(0, 2)
        assert item is not None
        assert item.flags() & Qt.ItemFlag.ItemIsEditable, "col(2) item should be editable"
        assert item.flags() & Qt.ItemFlag.ItemIsSelectable, "col(2) item should be selectable"

        # Set value via QTableWidgetItem (replaced old cellWidget approach)
        tbl.setItem(0, 2, QTableWidgetItem("A1-W1"))
        assert tbl.item(0, 2).text() == "A1-W1"
        dlg.close()
