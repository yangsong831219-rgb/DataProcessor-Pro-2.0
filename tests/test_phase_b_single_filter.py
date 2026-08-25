"""Phase B 单栅传感器排除 — 阶段4 回归测试

测试:
1. PhaseBWorker: 单栅传感器标记 single_grating=True, 不参与 decouple
2. PhaseBWorker: 双栅传感器正确解耦
3. 混合场景: 1单栅+3双栅 → 只有3双栅输出解耦结果
4. _render_result_table 过滤: single_grating 传感器不渲染
5. _write_state_to_main_page 过滤: 单栅不写入 decoupling_results
6. 全单栅边界: Ke 表空, 提示无双栅传感器

用法: python tests/test_phase_b_single_filter.py
"""

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np
import pandas as pd


def check(label, cond):
    if cond:
        print(f"  PASS: {label}")
        return 1
    else:
        print(f"  FAIL: {label}")
        return 0


# ═══════════════════════════════════════════════════════════════════════
# Helpers
# ═══════════════════════════════════════════════════════════════════════

def make_df(wavelength_cols, n_rows=100):
    """构造含多个波长列的模拟 DataFrame。

    每列: 1550 + 偏移 + 小噪声 + 模拟温度漂移 (线性递增)
    """
    np.random.seed(42)
    data = {}
    for i, col in enumerate(wavelength_cols):
        base = 1550.0 + i * 5.0
        # 模拟温度循环: 每个平台 20 行
        drift = np.repeat(np.arange(n_rows // 20) * 0.5, 20)[:n_rows]
        noise = np.random.normal(0, 0.001, n_rows)
        data[col] = base + drift + noise
    return pd.DataFrame(data)


def make_groups(sensor_specs: dict):
    """构造 annotation_groups。

    sensor_specs: {"A1": ["c1", "c2"], "B1": ["c3"]}
      → {"A1": [{"name": "A1-W1", "col_name": "c1"}, {"name": "A1-W2", "col_name": "c2"}],
          "B1": [{"name": "B1-W1", "col_name": "c3"}]}
    """
    groups = {}
    for sensor, cols in sensor_specs.items():
        groups[sensor] = [
            {"name": f"{sensor}-W{i+1}", "col_name": col}
            for i, col in enumerate(cols)
        ]
    return groups


def make_S_eff(groups: dict, slope=30.0):
    """构造 S_eff dict，每个 grating 都给相同 slope。"""
    seff = {}
    for gratings in groups.values():
        for g in gratings:
            seff[g["col_name"]] = {"slope": slope, "r2": 0.995, "T_base": 25.0}
    return seff


_TIME_H = np.arange(100) * 2.0 / 3600.0


# ═══════════════════════════════════════════════════════════════════════
# Test 1: PhaseBWorker — 单栅标记 single_grating=True
# ═══════════════════════════════════════════════════════════════════════

def test_single_grating_flag():
    """Single grating sensor is flagged single_grating=True, no eps_corr."""
    from ui.calibration_tab import PhaseBWorker

    cols = ["c1"]
    df = make_df(cols, n_rows=100)
    groups = make_groups({"B1": ["c1"]})
    seff = make_S_eff(groups, slope=28.5)

    worker = PhaseBWorker(df, _TIME_H, cols, groups, seff, {}, 2.0)
    worker.run()
    result = worker._last_result
    assert result is not None

    sensors = result["sensors"]
    assert "B1" in sensors
    assert sensors["B1"]["single_grating"] is True
    assert abs(sensors["B1"]["S1"] - 28.5) < 0.01
    assert abs(sensors["B1"]["T_base"] - 25.0) < 0.01
    assert "eps_corr" not in sensors["B1"]


# ═══════════════════════════════════════════════════════════════════════
# Test 2: PhaseBWorker — 双栅正确解耦
# ═══════════════════════════════════════════════════════════════════════

def test_dual_grating_decouple():
    """Dual grating sensor is correctly decoupled."""
    from ui.calibration_tab import PhaseBWorker

    cols2 = ["c1", "c2"]
    df2 = make_df(cols2, n_rows=100)
    groups2 = make_groups({"A1": ["c1", "c2"]})
    seff2 = make_S_eff(groups2, slope=30.0)
    coeffs2 = {"A1": {"Ke1": 1.2, "Ke2": 1.1}}

    worker2 = PhaseBWorker(df2, _TIME_H, cols2, groups2, seff2, coeffs2, 2.0)
    worker2.run()
    result2 = worker2._last_result
    assert result2 is not None

    sensors2 = result2["sensors"]
    assert "A1" in sensors2
    assert sensors2["A1"]["single_grating"] is False
    assert "eps_corr" in sensors2["A1"]
    assert "dT_corr" in sensors2["A1"]
    assert "T_abs" in sensors2["A1"]
    assert not np.all(np.isnan(sensors2["A1"]["eps_corr"]))
    assert abs(sensors2["A1"]["S1"] - 30.0) < 0.01
    assert abs(sensors2["A1"]["S2"] - 30.0) < 0.01


# ═══════════════════════════════════════════════════════════════════════
# Test 3: 混合场景 — 1 单栅 + 3 双栅
# ═══════════════════════════════════════════════════════════════════════

def test_mixed_1_single_3_dual():
    """Mixed: 1 single + 3 dual gratings — single excluded from decouple."""
    from ui.calibration_tab import PhaseBWorker

    cols3 = ["c1", "c2", "c3", "c4", "c5", "c6", "c7"]
    df3 = make_df(cols3, n_rows=100)
    groups3 = make_groups({
        "A1": ["c1", "c2"],   # dual
        "A2": ["c3", "c4"],   # dual
        "B1": ["c5"],         # single
        "B2": ["c6", "c7"],   # dual
    })
    seff3 = make_S_eff(groups3, slope=30.0)
    coeffs3 = {
        "A1": {"Ke1": 1.2, "Ke2": 1.1},
        "A2": {"Ke1": 0.9, "Ke2": 1.0},
        "B2": {"Ke1": 1.3, "Ke2": 1.2},
    }

    worker3 = PhaseBWorker(df3, _TIME_H, cols3, groups3, seff3, coeffs3, 2.0)
    worker3.run()
    result3 = worker3._last_result
    assert result3 is not None
    sensors3 = result3["sensors"]

    # 4 sensors total
    assert len(sensors3) == 4

    # single B1
    assert sensors3["B1"]["single_grating"] is True
    assert "eps_corr" not in sensors3["B1"]

    # 3 dual
    dual = {k: v for k, v in sensors3.items() if not v.get("single_grating", False)}
    assert len(dual) == 3
    assert "eps_corr" in dual["A1"]
    assert "eps_corr" in dual["A2"]
    assert "eps_corr" in dual["B2"]

    # single S_eff preserved
    assert abs(sensors3["B1"]["S1"] - 30.0) < 0.01
    assert abs(sensors3["B1"]["T_base"] - 25.0) < 0.01


# ═══════════════════════════════════════════════════════════════════════
# Test 4: _render_result_table 过滤
# ═══════════════════════════════════════════════════════════════════════

def test_render_result_table_filter():
    """_render_result_table excludes single grating sensors."""
    from PyQt6.QtWidgets import QApplication

    _app = QApplication.instance() or QApplication(sys.argv)

    from ui.calibration_tab import PhaseBWorker, PhaseBDialog

    cols3 = ["c1", "c2", "c3", "c4", "c5", "c6", "c7"]
    df3 = make_df(cols3, n_rows=100)
    groups3 = make_groups({
        "A1": ["c1", "c2"], "A2": ["c3", "c4"],
        "B1": ["c5"], "B2": ["c6", "c7"],
    })
    seff3 = make_S_eff(groups3, slope=30.0)
    coeffs3 = {
        "A1": {"Ke1": 1.2, "Ke2": 1.1},
        "A2": {"Ke1": 0.9, "Ke2": 1.0},
        "B2": {"Ke1": 1.3, "Ke2": 1.2},
    }

    worker3 = PhaseBWorker(df3, _TIME_H, cols3, groups3, seff3, coeffs3, 2.0)
    worker3.run()
    result3 = worker3._last_result
    assert result3 is not None
    sensors3 = result3["sensors"]

    dlg = PhaseBDialog(df3, groups3, {"S_eff": seff3}, parent=None)
    dlg._last_result = result3
    dlg._coeffs = coeffs3

    dlg._render_result_table(sensors3, seff3)

    assert dlg.result_table.rowCount() == 3

    sensor_names_in_table = []
    for r in range(dlg.result_table.rowCount()):
        item = dlg.result_table.item(r, 0)
        assert item is not None
        sensor_names_in_table.append(item.text())
    assert "B1" not in sensor_names_in_table
    assert "A1" in sensor_names_in_table
    assert "A2" in sensor_names_in_table
    assert "B2" in sensor_names_in_table

    assert dlg._single_excluded_label is not None
    assert "B1" in (dlg._single_excluded_label.text() or "")

    dlg.close()
    del dlg


# ═══════════════════════════════════════════════════════════════════════
# Test 5: _write_state_to_main_page 过滤
# ═══════════════════════════════════════════════════════════════════════

def test_write_state_filter():
    """_write_state_to_main_page excludes single from decoupling_results + ke_table."""
    from PyQt6.QtWidgets import QApplication, QTableWidgetItem as _QI

    _app = QApplication.instance() or QApplication(sys.argv)

    from ui.calibration_tab import TemperatureCalibrationPage, PhaseBWorker, PhaseBDialog

    cols3 = ["c1", "c2", "c3", "c4", "c5", "c6", "c7"]
    df3 = make_df(cols3, n_rows=100)
    groups3 = make_groups({
        "A1": ["c1", "c2"], "A2": ["c3", "c4"],
        "B1": ["c5"], "B2": ["c6", "c7"],
    })
    seff3 = make_S_eff(groups3, slope=30.0)
    coeffs3 = {
        "A1": {"Ke1": 1.2, "Ke2": 1.1},
        "A2": {"Ke1": 0.9, "Ke2": 1.0},
        "B2": {"Ke1": 1.3, "Ke2": 1.2},
    }

    worker3 = PhaseBWorker(df3, _TIME_H, cols3, groups3, seff3, coeffs3, 2.0)
    worker3.run()
    result3 = worker3._last_result

    tp = TemperatureCalibrationPage()
    tp._loaded_df = df3.copy()
    tp._annotation_groups = groups3

    dlg2 = PhaseBDialog(df3, groups3, {"S_eff": seff3}, parent=tp)
    dlg2._last_result = result3
    dlg2._coeffs = coeffs3
    dlg2.coef_table.setRowCount(3)
    for i, (sn, ke) in enumerate([("A1", {"Ke1": 1.2, "Ke2": 1.1}),
                                     ("A2", {"Ke1": 0.9, "Ke2": 1.0}),
                                     ("B2", {"Ke1": 1.3, "Ke2": 1.2})]):
        dlg2.coef_table.setItem(i, 0, _QI(sn))
        dlg2.coef_table.setItem(i, 1, _QI(str(ke["Ke1"])))
        dlg2.coef_table.setItem(i, 2, _QI(str(ke["Ke2"])))

    dlg2._write_state_to_main_page()

    dec = tp._phase_b_state.get("decoupling_results", {})
    assert len(dec) == 3
    assert "B1" not in dec
    assert "A1" in dec
    assert "A2" in dec
    assert "B2" in dec

    ke = tp._phase_b_state.get("ke_table", {})
    assert len(ke) == 3
    assert "B1" not in ke

    dlg2.close()
    del dlg2


# ═══════════════════════════════════════════════════════════════════════
# Test 6: 全单栅边界 — 只有单栅传感器
# ═══════════════════════════════════════════════════════════════════════

def test_all_single_boundary():
    """All-single-grating: result_table has 0 rows, label mentions all sensors."""
    from PyQt6.QtWidgets import QApplication

    _app = QApplication.instance() or QApplication(sys.argv)

    from ui.calibration_tab import PhaseBWorker, PhaseBDialog

    cols4 = ["c1", "c2"]
    df4 = make_df(cols4, n_rows=100)
    groups4 = make_groups({"B1": ["c1"], "B2": ["c2"]})
    seff4 = make_S_eff(groups4, slope=28.0)

    worker4 = PhaseBWorker(df4, _TIME_H, cols4, groups4, seff4, {}, 2.0)
    worker4.run()
    result4 = worker4._last_result
    assert result4 is not None
    sensors4 = result4["sensors"]

    assert len(sensors4) == 2
    assert sensors4["B1"]["single_grating"] is True
    assert sensors4["B2"]["single_grating"] is True

    dlg4 = PhaseBDialog(df4, groups4, {"S_eff": seff4}, parent=None)
    dlg4._last_result = result4
    dlg4._render_result_table(sensors4, seff4)
    assert dlg4.result_table.rowCount() == 0
    assert dlg4._single_excluded_label is not None
    label_text = dlg4._single_excluded_label.text() or ""
    assert "B1" in label_text and "B2" in label_text

    dlg4.close()
    del dlg4


# ═══════════════════════════════════════════════════════════════════════
# Test 7: Phase A S_eff 不受影响 (单栅温度系数保留)
# ═══════════════════════════════════════════════════════════════════════

def test_phase_a_seff_preserved():
    """Phase A S_eff is not modified by PhaseBWorker (read-only)."""
    from ui.calibration_tab import PhaseBWorker

    cols3 = ["c1", "c2", "c3", "c4", "c5", "c6", "c7"]
    df3 = make_df(cols3, n_rows=100)
    groups3 = make_groups({
        "A1": ["c1", "c2"], "A2": ["c3", "c4"],
        "B1": ["c5"], "B2": ["c6", "c7"],
    })
    original_seff = make_S_eff(groups3, slope=30.0)
    original_b1_slope = original_seff["c5"]["slope"]
    coeffs3 = {
        "A1": {"Ke1": 1.2, "Ke2": 1.1},
        "A2": {"Ke1": 0.9, "Ke2": 1.0},
        "B2": {"Ke1": 1.3, "Ke2": 1.2},
    }

    worker7 = PhaseBWorker(df3, _TIME_H, cols3, groups3, original_seff, coeffs3, 2.0)
    worker7.run()

    assert abs(original_seff["c5"]["slope"] - original_b1_slope) < 0.001
    assert abs(original_seff["c5"]["r2"] - 0.995) < 0.001
    assert abs(original_seff["c5"]["T_base"] - 25.0) < 0.001


# ═══════════════════════════════════════════════════════════════════════
# Standalone entry point
# ═══════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    passed = 0
    total = 0

    tests = [
        ("TestSingleGratingFlag", test_single_grating_flag),
        ("TestDualGratingDecouple", test_dual_grating_decouple),
        ("TestMixed1Single3Dual", test_mixed_1_single_3_dual),
        ("TestRenderResultTableFilter", test_render_result_table_filter),
        ("TestWriteStateFilter", test_write_state_filter),
        ("TestAllSingle", test_all_single_boundary),
        ("TestPhaseASEffPreserved", test_phase_a_seff_preserved),
    ]

    for name, fn in tests:
        print(f"\n═══ {name} ═══")
        try:
            fn()
            print(f"  ALL PASSED: {name}")
            passed += 1
        except AssertionError as e:
            print(f"  FAIL: {e}")
        total += 1

    print(f"\n{'='*50}")
    print(f"RESULTS: {passed}/{total} passed")
    if passed == total:
        print("ALL TESTS PASSED")
        sys.exit(0)
    else:
        print(f"FAILURES: {total - passed}")
        sys.exit(1)
