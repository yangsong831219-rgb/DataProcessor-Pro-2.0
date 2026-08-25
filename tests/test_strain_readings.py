"""应变标定 — 阶段2 回归测试

测试:
1. _generate_default_levels — 位移值正确，应变自动计算
2. _on_strain_cell_changed — 应变自动计算公式 (1e6), row>=1
3. _populate_table — 填充位移 + 理论应变 (数据起始 row 1)
4. _extract_table_data roundtrip (3-tuple: levels, readings, grating_map)
5. 列顺序 — 循环外层 → 光栅内层
6. _grating_col_indices — 备注行列索引映射
7. _compute_ke_results — Ke 计算 (single / dual_working / dual_anchored)
8. Bug 2 — 第二次打开不崩

用法: python tests/test_strain_readings.py
"""

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PyQt6.QtWidgets import QApplication, QTableWidget, QComboBox
from PyQt6.QtCore import Qt

import numpy as np

from ui.calibration_tab import StrainCalibrationPage, _StrainPasteTable
from typing import cast


def _get_qapp():
    return QApplication.instance() or QApplication(sys.argv)


# Ensure QApplication exists before any widget creation
_qapp = _get_qapp()


# ═══════════════════════════════════════════════════════════════════════
# Test 1: _generate_default_levels
# ═══════════════════════════════════════════════════════════════════════

class TestDefaultLevels:
    """_generate_default_levels displacement and auto-strain."""

    def test_default_levels_80mm(self):
        page = StrainCalibrationPage()
        levels = page._generate_default_levels()
        assert len(levels) == 11
        assert abs(levels[0]) < 0.0001
        assert abs(levels[5] - 0.040) < 0.0001
        assert abs(levels[10] - 0.080) < 0.0001

    def test_default_levels_50mm(self):
        page = StrainCalibrationPage()
        page._config["gauge_length_mm"] = 50.0
        levels50 = page._generate_default_levels()
        assert abs(levels50[0]) < 0.0001
        assert abs(levels50[10] - 0.050) < 0.0001

    def test_default_levels_100mm(self):
        page = StrainCalibrationPage()
        page._config["gauge_length_mm"] = 100.0
        levels100 = page._generate_default_levels()
        assert abs(levels100[0]) < 0.0001
        assert abs(levels100[10] - 0.100) < 0.0001

    def test_default_levels_custom_n(self):
        page = StrainCalibrationPage()
        page._config["gauge_length_mm"] = 100.0
        levels5 = page._generate_default_levels(n=5)
        assert len(levels5) == 5

    def test_init_uses_default_levels(self):
        page2 = StrainCalibrationPage()
        assert len(page2._levels) == 11


# ═══════════════════════════════════════════════════════════════════════
# Test 2: _on_strain_cell_changed formula (1e6), row >= 1
# ═══════════════════════════════════════════════════════════════════════

class TestStrainAutoCalc:
    """Strain auto-calculation on cell change."""

    def _mk_item(self, text):
        from PyQt6.QtWidgets import QTableWidgetItem
        return QTableWidgetItem(text)

    def test_row0_skipped(self):
        _get_qapp()
        page = StrainCalibrationPage()
        page._config["gauge_length_mm"] = 80.0
        table = _StrainPasteTable(2, 2)
        assert isinstance(table, _StrainPasteTable)
        page._dialog_table = cast(_StrainPasteTable, table)
        table.setItem(0, 0, self._mk_item("0.008"))
        page._on_strain_cell_changed(0, 0)
        si_row0 = table.item(0, 1)
        assert si_row0 is None or si_row0.text() == ""

    def test_strain_row1_values(self):
        _get_qapp()
        page = StrainCalibrationPage()
        page._config["gauge_length_mm"] = 80.0
        table = _StrainPasteTable(2, 2)
        assert isinstance(table, _StrainPasteTable)
        page._dialog_table = cast(_StrainPasteTable, table)

        # disp=0.008, gauge=80 → strain=100
        table.setItem(1, 0, self._mk_item("0.008"))
        page._on_strain_cell_changed(1, 0)
        si = table.item(1, 1)
        assert si is not None and abs(float(si.text()) - 100.0) < 1.0

        # disp=0.040, gauge=80 → strain=500
        table.setItem(1, 0, self._mk_item("0.040"))
        page._on_strain_cell_changed(1, 0)
        si = table.item(1, 1)
        assert si is not None and abs(float(si.text()) - 500.0) < 1.0

        # disp=0.080, gauge=80 → strain=1000
        table.setItem(1, 0, self._mk_item("0.080"))
        page._on_strain_cell_changed(1, 0)
        si = table.item(1, 1)
        assert si is not None and abs(float(si.text()) - 1000.0) < 1.0

    def test_strain_50mm_gauge(self):
        _get_qapp()
        page = StrainCalibrationPage()
        page._config["gauge_length_mm"] = 50.0
        table = _StrainPasteTable(2, 2)
        assert isinstance(table, _StrainPasteTable)
        page._dialog_table = cast(_StrainPasteTable, table)
        table.setItem(1, 0, self._mk_item("0.005"))
        page._on_strain_cell_changed(1, 0)
        si = table.item(1, 1)
        assert si is not None and abs(float(si.text()) - 100.0) < 1.0

    def test_mid_row_edit(self):
        _get_qapp()
        page = StrainCalibrationPage()
        page._config["gauge_length_mm"] = 80.0
        table = _StrainPasteTable(2, 2)
        assert isinstance(table, _StrainPasteTable)
        page._dialog_table = cast(_StrainPasteTable, table)
        table.setItem(1, 0, self._mk_item("0.050"))
        page._on_strain_cell_changed(1, 0)
        si = table.item(1, 1)
        assert si is not None and abs(float(si.text()) - 625.0) < 1.0


# ═══════════════════════════════════════════════════════════════════════
# Test 3: _populate_table (数据从 row 1 开始)
# ═══════════════════════════════════════════════════════════════════════

class TestPopulateTable:
    """_populate_table fills data from row 1."""

    def test_populate_table(self):
        page = StrainCalibrationPage()
        page._config["gauge_length_mm"] = 80.0
        page._levels = page._generate_default_levels()
        cols = page._build_table_columns()
        table = QTableWidget(len(page._levels) + 1, len(cols))
        table.setHorizontalHeaderLabels(cols)
        page._populate_table(table)

        assert table.item(0, 0) is None
        _i = table.item(1, 0); assert _i is not None; assert abs(float(_i.text())) < 0.0001
        _i = table.item(1, 1); assert _i is not None; assert abs(float(_i.text())) < 1.0
        _i = table.item(6, 0); assert _i is not None; assert abs(float(_i.text()) - 0.040) < 0.0001
        _i = table.item(6, 1); assert _i is not None; assert abs(float(_i.text()) - 500.0) < 1.0
        _i = table.item(11, 0); assert _i is not None; assert abs(float(_i.text()) - 0.080) < 0.0001
        _i = table.item(11, 1); assert _i is not None; assert abs(float(_i.text()) - 1000.0) < 1.0


# ═══════════════════════════════════════════════════════════════════════
# Test 4: _extract_table_data roundtrip (3-tuple)
# ═══════════════════════════════════════════════════════════════════════

class TestExtractRoundtrip:
    """_extract_table_data roundtrip."""

    def test_extract_roundtrip(self):
        page = StrainCalibrationPage()
        page._config["gauge_length_mm"] = 80.0
        page._levels = page._generate_default_levels()
        cols = page._build_table_columns()
        table = QTableWidget(len(page._levels) + 1, len(cols))
        table.setHorizontalHeaderLabels(cols)
        page._populate_table(table)

        ext_levels, ext_readings, ext_map = page._extract_table_data(table)
        assert len(ext_levels) == len(page._levels)
        for i in range(len(page._levels)):
            assert abs(ext_levels[i] - page._levels[i]) < 0.001
        assert ext_map == {}


# ═══════════════════════════════════════════════════════════════════════
# Test 5: 列顺序验证
# ═══════════════════════════════════════════════════════════════════════

class TestColumnOrder:
    """Column order validation."""

    def _test_col_order(self, config_overrides, expected_headers):
        p = StrainCalibrationPage()
        p._config.update(config_overrides)
        cols = p._build_table_columns()
        errors = []
        for i, (actual, expected) in enumerate(zip(cols, expected_headers)):
            if actual != expected:
                errors.append(f"col {i}: expected '{expected}', got '{actual}'")
        if errors:
            raise AssertionError("; ".join(errors))

    def test_single_tension_only(self):
        cfg1 = {"grating_kind": "single", "n_cycles": 1, "mode": "tension_only"}
        expected1 = ["位移(mm)", "理论应变(με)", "G1_C1_张拉(nm)"]
        self._test_col_order(cfg1, expected1)

    def test_single_tension_return(self):
        cfg1r = {"grating_kind": "single", "n_cycles": 1, "mode": "tension_return"}
        expected1r = ["位移(mm)", "理论应变(με)",
                      "G1_C1_张拉(nm)", "G1_C1_退回(nm)"]
        self._test_col_order(cfg1r, expected1r)

    def test_dual_3cycle_return(self):
        cfg3 = {"grating_kind": "dual_both", "n_cycles": 3, "mode": "tension_return"}
        expected3 = ["位移(mm)", "理论应变(με)",
                     "G1_C1_张拉(nm)", "G2_C1_张拉(nm)", "G1_C1_退回(nm)", "G2_C1_退回(nm)",
                     "G1_C2_张拉(nm)", "G2_C2_张拉(nm)", "G1_C2_退回(nm)", "G2_C2_退回(nm)",
                     "G1_C3_张拉(nm)", "G2_C3_张拉(nm)", "G1_C3_退回(nm)", "G2_C3_退回(nm)"]
        self._test_col_order(cfg3, expected3)

    def test_dual_2cycle_tension(self):
        cfg2 = {"grating_kind": "dual_both", "n_cycles": 2, "mode": "tension_only"}
        expected2 = ["位移(mm)", "理论应变(με)",
                     "G1_C1_张拉(nm)", "G2_C1_张拉(nm)",
                     "G1_C2_张拉(nm)", "G2_C2_张拉(nm)"]
        self._test_col_order(cfg2, expected2)


# ═══════════════════════════════════════════════════════════════════════
# Test 6: 列顺序 roundtrip — 双栅+3循环+退回
# ═══════════════════════════════════════════════════════════════════════

class TestColumnOrderRoundtrip:
    """Column order roundtrip with dual grating + 3 cycles + return."""

    def test_column_order_roundtrip(self):
        p = StrainCalibrationPage()
        p._config.update({"grating_kind": "dual_both", "n_cycles": 3, "mode": "tension_return"})
        p._levels = p._generate_default_levels()
        p._readings = {
            1: {ci+1: {"load": [float(f"{ci+1}.1") * 10] * len(p._levels),
                       "unload": [float(f"{ci+1}.2") * 10] * len(p._levels)}
                for ci in range(3)},
            2: {ci+1: {"load": [float(f"{ci+1}.3") * 10] * len(p._levels),
                       "unload": [float(f"{ci+1}.4") * 10] * len(p._levels)}
                for ci in range(3)},
        }
        cols = p._build_table_columns()
        table = QTableWidget(len(p._levels) + 1, len(cols))
        table.setHorizontalHeaderLabels(cols)
        p._populate_table(table)

        expected_layout = [
            (2, 11.0), (3, 13.0), (4, 12.0), (5, 14.0),
            (6, 21.0), (7, 23.0), (8, 22.0), (9, 24.0),
            (10, 31.0), (11, 33.0), (12, 32.0), (13, 34.0),
        ]
        for col_idx, expected_val in expected_layout:
            item = table.item(1, col_idx)
            got = float(item.text()) if item and item.text().strip() else -999
            assert abs(got - expected_val) < 0.001, f"col{col_idx}={expected_val} failed, got {got}"

        ext_levels, ext_readings, ext_map = p._extract_table_data(table)
        assert abs(ext_readings[1][1]["load"][0] - 11.0) < 0.001
        assert abs(ext_readings[2][3]["unload"][0] - 34.0) < 0.001


# ═══════════════════════════════════════════════════════════════════════
# Test 7: _grating_col_indices
# ═══════════════════════════════════════════════════════════════════════

class TestGratingColIndices:
    """_grating_col_indices mapping."""

    def test_single_grating(self):
        p_single = StrainCalibrationPage()
        p_single._config.update({"grating_kind": "single", "n_cycles": 1, "mode": "tension_only"})
        gci = p_single._grating_col_indices()
        assert gci[1] == [2]
        assert 2 not in gci

    def test_dual_2cycle_tension(self):
        p_dual = StrainCalibrationPage()
        p_dual._config.update({"grating_kind": "dual_both", "n_cycles": 2, "mode": "tension_only"})
        gci2 = p_dual._grating_col_indices()
        assert gci2[1] == [2, 4]
        assert gci2[2] == [3, 5]

    def test_dual_1cycle_return(self):
        p_ret = StrainCalibrationPage()
        p_ret._config.update({"grating_kind": "dual_both", "n_cycles": 1, "mode": "tension_return"})
        gci3 = p_ret._grating_col_indices()
        assert gci3[1] == [2, 4]
        assert gci3[2] == [3, 5]


# ═══════════════════════════════════════════════════════════════════════
# Test 8: _compute_ke_results
# ═══════════════════════════════════════════════════════════════════════

class TestComputeKeResults:
    """_compute_ke_results for single / dual_working / dual_anchored."""

    def _make_readings(self, grating_data, n_levels=10):
        read = {}
        for gi, wl_values in grating_data.items():
            read[gi] = {1: {"load": wl_values}}
        return read

    def test_single_grating(self):
        from dp_engine.calibration.strain_calibration import StrainCalibrationConfig, calibrate_strain

        p_single = StrainCalibrationPage()
        p_single._config.update({"grating_kind": "single", "n_cycles": 1, "mode": "tension_only",
                                  "gauge_length_mm": 80.0})
        p_single._levels = p_single._generate_default_levels()
        eps_theory = [d / 80.0 * 1e6 for d in p_single._levels]
        Ke_true = 1.5
        wl_base = 1550.0
        wl_values = [wl_base + Ke_true * eps / 1000.0 for eps in eps_theory]
        readings_s = self._make_readings({1: wl_values})
        config_s = StrainCalibrationConfig(
            gauge_length_mm=80.0, mode="tension_only", n_cycles=1,
            grating_kind="single", anchored_grating=None,
            levels=p_single._levels,
        )
        result_s = calibrate_strain(config_s, readings_s)
        ke_s = p_single._compute_ke_results(result_s)
        assert abs(ke_s["Ke1"] - Ke_true) < 0.01
        assert ke_s["Ke2"] == 0.0

    def test_dual_working(self):
        from dp_engine.calibration.strain_calibration import StrainCalibrationConfig, calibrate_strain

        p_dual = StrainCalibrationPage()
        p_dual._config.update({"grating_kind": "dual_both", "n_cycles": 1, "mode": "tension_only",
                                "gauge_length_mm": 80.0})
        p_dual._levels = p_dual._generate_default_levels()
        eps_theory = [d / 80.0 * 1e6 for d in p_dual._levels]
        Ke1_true, Ke2_true = 1.23, 0.98
        wl_base = 1550.0
        wl_g1 = [wl_base + Ke1_true * eps / 1000.0 for eps in eps_theory]
        wl_g2 = [wl_base + Ke2_true * eps / 1000.0 for eps in eps_theory]
        readings_d = self._make_readings({1: wl_g1, 2: wl_g2})
        config_d = StrainCalibrationConfig(
            gauge_length_mm=80.0, mode="tension_only", n_cycles=1,
            grating_kind="dual_both", levels=p_dual._levels,
        )
        result_d = calibrate_strain(config_d, readings_d)
        ke_d = p_dual._compute_ke_results(result_d)
        assert abs(ke_d["Ke1"] - Ke1_true) < 0.01
        assert abs(ke_d["Ke2"] - Ke2_true) < 0.01

    def test_dual_anchored(self):
        from dp_engine.calibration.strain_calibration import StrainCalibrationConfig, calibrate_strain

        p_anc = StrainCalibrationPage()
        p_anc._config.update({"grating_kind": "dual_anchored", "n_cycles": 1, "mode": "tension_only",
                               "gauge_length_mm": 80.0, "anchored_grating": 2})
        p_anc._levels = p_anc._generate_default_levels()
        eps_theory = [d / 80.0 * 1e6 for d in p_anc._levels]
        Ke_anc = 2.10
        wl_base = 1550.0
        wl_g1a = [wl_base + Ke_anc * eps / 1000.0 for eps in eps_theory]
        readings_a = self._make_readings({1: wl_g1a, 2: [wl_base] * len(eps_theory)})
        config_a = StrainCalibrationConfig(
            gauge_length_mm=80.0, mode="tension_only", n_cycles=1,
            grating_kind="dual_anchored", anchored_grating=2,
            levels=p_anc._levels,
        )
        result_a = calibrate_strain(config_a, readings_a)
        ke_a = p_anc._compute_ke_results(result_a)
        assert abs(ke_a["Ke1"] - Ke_anc) < 0.01
        assert ke_a["Ke2"] == 0.0


# ═══════════════════════════════════════════════════════════════════════
# Test 9: Bug 2 — 第二次调用不崩
# ═══════════════════════════════════════════════════════════════════════

class TestBug2Smoke:
    """Bug 2 smoke test — second call does not crash."""

    def test_bug2_smoke(self):
        page3 = StrainCalibrationPage()
        assert page3._dialog_table is None
        cols1 = page3._build_table_columns()
        cols2 = page3._build_table_columns()
        assert cols1 == cols2
        t1 = QTableWidget(len(page3._levels) + 1, len(cols1))
        t2 = QTableWidget(len(page3._levels) + 1, len(cols1))
        page3._populate_table(t1)
        page3._populate_table(t2)
        l, r, m = page3._extract_table_data(t1)
        assert len(l) > 0
        assert isinstance(m, dict)


# ═══════════════════════════════════════════════════════════════════════
# Test 10: 备注行 ComboBox roundtrip
# ═══════════════════════════════════════════════════════════════════════

class TestAnnotationComboRoundtrip:
    """Annotation row ComboBox → grating_map roundtrip."""

    def test_annotation_combo_roundtrip(self):
        _get_qapp()
        p_ann = StrainCalibrationPage()
        p_ann._config.update({"grating_kind": "dual_both", "n_cycles": 1, "mode": "tension_only"})
        p_ann._levels = p_ann._generate_default_levels()
        cols_ann = p_ann._build_table_columns()
        t_ann = QTableWidget(len(p_ann._levels) + 1, len(cols_ann))
        t_ann.setHorizontalHeaderLabels(cols_ann)

        grating_cols = p_ann._grating_col_indices()
        combo_g1 = QComboBox()
        combo_g1.setEditable(True)
        combo_g1.addItems(["A1-W1", "A1-W2", "B1-W1"])
        combo_g1.setCurrentText("A1-W1")
        t_ann.setCellWidget(0, grating_cols[1][0], combo_g1)

        combo_g2 = QComboBox()
        combo_g2.setEditable(True)
        combo_g2.addItems(["A1-W1", "A1-W2", "B1-W1"])
        combo_g2.setCurrentText("A1-W2")
        t_ann.setCellWidget(0, grating_cols[2][0], combo_g2)

        from PyQt6.QtWidgets import QTableWidgetItem as _QI
        from PyQt6.QtGui import QColor as _QC
        for gi in [1, 2]:
            for col in grating_cols[gi][1:]:
                gi_item = _QI("—")
                gi_item.setFlags(Qt.ItemFlag.NoItemFlags)
                gi_item.setBackground(_QC(230, 230, 230))
                t_ann.setItem(0, col, gi_item)

        ext_l, ext_r, ext_m = p_ann._extract_table_data(t_ann)
        assert ext_m.get("G1") == "A1-W1"
        assert ext_m.get("G2") == "A1-W2"


# ═══════════════════════════════════════════════════════════════════════
# Standalone entry point
# ═══════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    import pytest as _pytest
    print("Running strain readings regression tests via pytest...")
    sys.exit(_pytest.main([__file__, "-v"]))
