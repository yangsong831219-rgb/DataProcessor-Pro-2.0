"""应变标定 — 阶段2 回归测试 (standalone, no pytest dependency)

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

_app = QApplication.instance() or QApplication(sys.argv)

from ui.calibration_tab import StrainCalibrationPage


def check(label, cond):
    if cond:
        print(f"  PASS: {label}")
        return 1
    else:
        print(f"  FAIL: {label}")
        return 0


passed = 0
total = 0


# ═══ Test 1: _generate_default_levels ═══
print("\n═══ TestDefaultLevels ═══")

page = StrainCalibrationPage()

# 80mm → 11 rows (含零点), disp values correct
levels = page._generate_default_levels()
total += 1; passed += check("n_rows=11", len(levels) == 11)
total += 1; passed += check("80mm row0=0.000 (zero)", abs(levels[0]) < 0.0001)
total += 1; passed += check("80mm row5=0.040", abs(levels[5] - 0.040) < 0.0001)
total += 1; passed += check("80mm row10=0.080", abs(levels[10] - 0.080) < 0.0001)

# 50mm
page._config["gauge_length_mm"] = 50.0
levels50 = page._generate_default_levels()
total += 1; passed += check("50mm row0=0.000", abs(levels50[0]) < 0.0001)
total += 1; passed += check("50mm row10=0.050", abs(levels50[10] - 0.050) < 0.0001)

# 100mm
page._config["gauge_length_mm"] = 100.0
levels100 = page._generate_default_levels()
total += 1; passed += check("100mm row0=0.000", abs(levels100[0]) < 0.0001)
total += 1; passed += check("100mm row10=0.100", abs(levels100[10] - 0.100) < 0.0001)

# Custom n=5
levels5 = page._generate_default_levels(n=5)
total += 1; passed += check("n=5 (custom)", len(levels5) == 5)

# __init__ uses _generate_default_levels (11 rows)
page2 = StrainCalibrationPage()
total += 1; passed += check("__init__ uses _generate_default_levels", len(page2._levels) == 11)


# ═══ Test 2: _on_strain_cell_changed formula (1e6), row >= 1 ═══
print("\n═══ TestStrainAutoCalc ═══")

page._config["gauge_length_mm"] = 80.0

def _mk_item(text):
    from PyQt6.QtWidgets import QTableWidgetItem
    return QTableWidgetItem(text)

# Row 0 (备注行) → 不触发自动计算
page._dialog_table = QTableWidget(2, 2)  # row 0 = annotation, row 1 = data
page._dialog_table.setItem(0, 0, _mk_item("0.008"))
page._on_strain_cell_changed(0, 0)
si_row0 = page._dialog_table.item(0, 1)
total += 1; passed += check("row0 skipped (no auto calc)", si_row0 is None or si_row0.text() == "")

# Row 1: disp=0.008, gauge=80 → strain=100
page._dialog_table.setItem(1, 0, _mk_item("0.008"))
page._on_strain_cell_changed(1, 0)
si = page._dialog_table.item(1, 1)
total += 1; passed += check("strain row1=100", si is not None and abs(float(si.text()) - 100.0) < 1.0)

# Row 1: disp=0.040, gauge=80 → strain=500
page._dialog_table.setItem(1, 0, _mk_item("0.040"))
page._on_strain_cell_changed(1, 0)
si = page._dialog_table.item(1, 1)
total += 1; passed += check("strain row5=500", si is not None and abs(float(si.text()) - 500.0) < 1.0)

# Row 1: disp=0.080, gauge=80 → strain=1000
page._dialog_table.setItem(1, 0, _mk_item("0.080"))
page._on_strain_cell_changed(1, 0)
si = page._dialog_table.item(1, 1)
total += 1; passed += check("strain row10=1000", si is not None and abs(float(si.text()) - 1000.0) < 1.0)

# 50mm gauge: disp=0.005 → strain=100
page._config["gauge_length_mm"] = 50.0
page._dialog_table.setItem(1, 0, _mk_item("0.005"))
page._on_strain_cell_changed(1, 0)
si = page._dialog_table.item(1, 1)
total += 1; passed += check("strain 50mm row1=100", si is not None and abs(float(si.text()) - 100.0) < 1.0)

# Mid-row edit: change row 1 disp to 0.050, gauge=80 → strain=625
page._config["gauge_length_mm"] = 80.0
page._dialog_table.setItem(1, 0, _mk_item("0.050"))
page._on_strain_cell_changed(1, 0)
si = page._dialog_table.item(1, 1)
total += 1; passed += check("strain disp=0.050→625", si is not None and abs(float(si.text()) - 625.0) < 1.0)


# ═══ Test 3: _populate_table (数据从 row 1 开始) ═══
print("\n═══ TestPopulateTable ═══")

page._config["gauge_length_mm"] = 80.0
page._levels = page._generate_default_levels()
cols = page._build_table_columns()
# Table = 1 备注行 + 10 数据行
table = QTableWidget(len(page._levels) + 1, len(cols))
table.setHorizontalHeaderLabels(cols)
page._populate_table(table)

# Row 0 (备注行) 应为空 (未放 ComboBox, _populate_table 不写 row 0)
total += 1; passed += check("populate row0 col0 empty", table.item(0, 0) is None)
# Row 1 = first data row
total += 1; passed += check("populate disp row1=0.000 (zero)", abs(float(table.item(1, 0).text())) < 0.0001)
total += 1; passed += check("populate strain row1=0", abs(float(table.item(1, 1).text())) < 1.0)
total += 1; passed += check("populate disp row6=0.040", abs(float(table.item(6, 0).text()) - 0.040) < 0.0001)
total += 1; passed += check("populate strain row6=500", abs(float(table.item(6, 1).text()) - 500.0) < 1.0)
total += 1; passed += check("populate disp row11=0.080", abs(float(table.item(11, 0).text()) - 0.080) < 0.0001)
total += 1; passed += check("populate strain row11=1000", abs(float(table.item(11, 1).text()) - 1000.0) < 1.0)


# ═══ Test 4: _extract_table_data roundtrip (3-tuple) ═══
print("\n═══ TestExtractRoundtrip ═══")

ext_levels, ext_readings, ext_map = page._extract_table_data(table)
total += 1; passed += check("roundtrip len matches", len(ext_levels) == len(page._levels))
for i in range(len(page._levels)):
    ok = abs(ext_levels[i] - page._levels[i]) < 0.001
    total += 1; passed += check(f"roundtrip row{i}", ok)

# grating_map 应为空 (未放 ComboBox)
total += 1; passed += check("roundtrip grating_map empty", ext_map == {})


# ═══ Test 5: 列顺序验证 ═══
print("\n═══ TestColumnOrder ═══")

def test_col_order(config_overrides, expected_headers):
    """创建临时 page，验证 _build_table_columns 输出"""
    p = StrainCalibrationPage()
    p._config.update(config_overrides)
    cols = p._build_table_columns()
    t = 0
    ok_count = 0
    for i, (actual, expected) in enumerate(zip(cols, expected_headers)):
        t += 1
        ok = actual == expected
        if ok:
            ok_count += 1
        else:
            print(f"    col {i}: expected '{expected}', got '{actual}'")
    return t, ok_count

# 单栅 + 1循环: G1_C1_张拉
cfg1 = {"grating_kind": "single", "n_cycles": 1, "mode": "tension_only"}
expected1 = ["位移(mm)", "理论应变(με)", "G1_C1_张拉(nm)"]
t, ok = test_col_order(cfg1, expected1)
total += t; passed += ok

# 单栅 + 1循环 + 退回: G1_C1_张, G1_C1_退
cfg1r = {"grating_kind": "single", "n_cycles": 1, "mode": "tension_return"}
expected1r = ["位移(mm)", "理论应变(με)",
              "G1_C1_张拉(nm)", "G1_C1_退回(nm)"]
t, ok = test_col_order(cfg1r, expected1r)
total += t; passed += ok

# 双栅 + 3循环 + 退回 (最复杂场景)
cfg3 = {"grating_kind": "dual_both", "n_cycles": 3, "mode": "tension_return"}
expected3 = ["位移(mm)", "理论应变(με)",
             "G1_C1_张拉(nm)", "G2_C1_张拉(nm)", "G1_C1_退回(nm)", "G2_C1_退回(nm)",
             "G1_C2_张拉(nm)", "G2_C2_张拉(nm)", "G1_C2_退回(nm)", "G2_C2_退回(nm)",
             "G1_C3_张拉(nm)", "G2_C3_张拉(nm)", "G1_C3_退回(nm)", "G2_C3_退回(nm)"]
t, ok = test_col_order(cfg3, expected3)
total += t; passed += ok

# 双栅 + 2循环 + 只张拉
cfg2 = {"grating_kind": "dual_both", "n_cycles": 2, "mode": "tension_only"}
expected2 = ["位移(mm)", "理论应变(με)",
             "G1_C1_张拉(nm)", "G2_C1_张拉(nm)",
             "G1_C2_张拉(nm)", "G2_C2_张拉(nm)"]
t, ok = test_col_order(cfg2, expected2)
total += t; passed += ok


# ═══ Test 6: 列顺序 roundtrip — 双栅+3循环+退回 ═══
print("\n═══ TestColumnOrderRoundtrip ═══")

p = StrainCalibrationPage()
p._config.update({"grating_kind": "dual_both", "n_cycles": 3, "mode": "tension_return"})
p._levels = p._generate_default_levels()
# 填入模拟数据: 每个 grating/cycle/direction 不同值以便区分
p._readings = {
    1: {ci+1: {"load": [float(f"{ci+1}.1") * 10] * len(p._levels),
               "unload": [float(f"{ci+1}.2") * 10] * len(p._levels)}
        for ci in range(3)},
    2: {ci+1: {"load": [float(f"{ci+1}.3") * 10] * len(p._levels),
               "unload": [float(f"{ci+1}.4") * 10] * len(p._levels)}
        for ci in range(3)},
}
cols = p._build_table_columns()
# Table = 1 备注行 + N 数据行
table = QTableWidget(len(p._levels) + 1, len(cols))
table.setHorizontalHeaderLabels(cols)
p._populate_table(table)

# 验证列顺序: col 2=G1_C1_张 (11.0), col 3=G2_C1_张 (13.0),
#              col 4=G1_C1_退 (12.0), col 5=G2_C1_退 (14.0), ...
expected_layout = [
    (2, 11.0),   # G1_C1_张拉 (1.1*10)
    (3, 13.0),   # G2_C1_张拉 (1.3*10)
    (4, 12.0),   # G1_C1_退回 (1.2*10)
    (5, 14.0),   # G2_C1_退回 (1.4*10)
    (6, 21.0),   # G1_C2_张拉 (2.1*10)
    (7, 23.0),   # G2_C2_张拉 (2.3*10)
    (8, 22.0),   # G1_C2_退回 (2.2*10)
    (9, 24.0),   # G2_C2_退回 (2.4*10)
    (10, 31.0),  # G1_C3_张拉 (3.1*10)
    (11, 33.0),  # G2_C3_张拉 (3.3*10)
    (12, 32.0),  # G1_C3_退回 (3.2*10)
    (13, 34.0),  # G2_C3_退回 (3.4*10)
]
for col_idx, expected_val in expected_layout:
    item = table.item(1, col_idx)  # row 1 = first data row
    got = float(item.text()) if item and item.text().strip() else -999
    total += 1; passed += check(f"col{col_idx}={expected_val}", abs(got - expected_val) < 0.001)

# extract roundtrip (3-tuple)
ext_levels, ext_readings, ext_map = p._extract_table_data(table)
total += 1; passed += check("roundtrip G1_C1 load", abs(ext_readings[1][1]["load"][0] - 11.0) < 0.001)
total += 1; passed += check("roundtrip G2_C3 unload", abs(ext_readings[2][3]["unload"][0] - 34.0) < 0.001)


# ═══ Test 7: _grating_col_indices ═══
print("\n═══ TestGratingColIndices ═══")

# 单栅 + 1循环 + 只张拉: G1 只有 col 2
p_single = StrainCalibrationPage()
p_single._config.update({"grating_kind": "single", "n_cycles": 1, "mode": "tension_only"})
gci = p_single._grating_col_indices()
total += 1; passed += check("single: G1 cols", gci[1] == [2])
total += 1; passed += check("single: no G2", 2 not in gci)

# 双栅 + 2循环 + 只张拉: G1=[2,4], G2=[3,5]
p_dual = StrainCalibrationPage()
p_dual._config.update({"grating_kind": "dual_both", "n_cycles": 2, "mode": "tension_only"})
gci2 = p_dual._grating_col_indices()
total += 1; passed += check("dual_both: G1 cols", gci2[1] == [2, 4])
total += 1; passed += check("dual_both: G2 cols", gci2[2] == [3, 5])

# 双栅 + 1循环 + 退回: G1=[2,4], G2=[3,5]
p_ret = StrainCalibrationPage()
p_ret._config.update({"grating_kind": "dual_both", "n_cycles": 1, "mode": "tension_return"})
gci3 = p_ret._grating_col_indices()
total += 1; passed += check("return: G1 cols", gci3[1] == [2, 4])
total += 1; passed += check("return: G2 cols", gci3[2] == [3, 5])


# ═══ Test 8: _compute_ke_results ═══
print("\n═══ TestComputeKeResults ═══")

from py.calibration.strain_calibration import (
    StrainCalibrationConfig, StrainCalibrationResult, GratingStrainResult, calibrate_strain,
)

def _make_readings(grating_data, n_levels=10):
    """构造 readings dict: {grating_index: {cycle: {"load": [wl_values]}}}"""
    read = {}
    for gi, wl_values in grating_data.items():
        read[gi] = {1: {"load": wl_values}}
    return read

# 8a: 单栅 → Ke1 computed, Ke2=0
print("  [single grating]")
p_single._config.update({"grating_kind": "single", "n_cycles": 1, "mode": "tension_only",
                          "gauge_length_mm": 80.0})
p_single._levels = p_single._generate_default_levels()
eps_theory = [d / 80.0 * 1e6 for d in p_single._levels]
# 构造波长: 每个 level 的 dλ(pm) = Ke * ε, Ke=1.5
Ke_true = 1.5
wl_base = 1550.0
wl_values = [wl_base + Ke_true * eps / 1000.0 for eps in eps_theory]  # nm
readings_s = _make_readings({1: wl_values})
config_s = StrainCalibrationConfig(
    gauge_length_mm=80.0, mode="tension_only", n_cycles=1,
    grating_kind="single", anchored_grating=None,
    levels=p_single._levels,
)
result_s = calibrate_strain(config_s, readings_s)
ke_s = p_single._compute_ke_results(result_s)
total += 1; passed += check("single: Ke1≈1.5", abs(ke_s["Ke1"] - Ke_true) < 0.01)
total += 1; passed += check("single: Ke2=0", ke_s["Ke2"] == 0.0)

# 8b: dual_working → Ke1 + Ke2 computed
print("  [dual working]")
p_dual._config.update({"grating_kind": "dual_both", "n_cycles": 1, "mode": "tension_only",
                        "gauge_length_mm": 80.0})
p_dual._levels = p_dual._generate_default_levels()
Ke1_true, Ke2_true = 1.23, 0.98
wl_g1 = [wl_base + Ke1_true * eps / 1000.0 for eps in eps_theory]
wl_g2 = [wl_base + Ke2_true * eps / 1000.0 for eps in eps_theory]
readings_d = _make_readings({1: wl_g1, 2: wl_g2})
config_d = StrainCalibrationConfig(
    gauge_length_mm=80.0, mode="tension_only", n_cycles=1,
    grating_kind="dual_both", levels=p_dual._levels,
)
result_d = calibrate_strain(config_d, readings_d)
ke_d = p_dual._compute_ke_results(result_d)
total += 1; passed += check("dual: Ke1≈1.23", abs(ke_d["Ke1"] - Ke1_true) < 0.01)
total += 1; passed += check("dual: Ke2≈0.98", abs(ke_d["Ke2"] - Ke2_true) < 0.01)

# 8c: dual_anchored → only Ke1 computed, Ke2=0
print("  [dual anchored]")
p_anc = StrainCalibrationPage()
p_anc._config.update({"grating_kind": "dual_anchored", "n_cycles": 1, "mode": "tension_only",
                       "gauge_length_mm": 80.0, "anchored_grating": 2})
p_anc._levels = p_anc._generate_default_levels()
Ke_anc = 2.10
wl_g1a = [wl_base + Ke_anc * eps / 1000.0 for eps in eps_theory]
readings_a = _make_readings({1: wl_g1a, 2: [wl_base] * len(eps_theory)})
config_a = StrainCalibrationConfig(
    gauge_length_mm=80.0, mode="tension_only", n_cycles=1,
    grating_kind="dual_anchored", anchored_grating=2,
    levels=p_anc._levels,
)
result_a = calibrate_strain(config_a, readings_a)
ke_a = p_anc._compute_ke_results(result_a)
# G1 (grating_index=1) should have Ke computed
total += 1; passed += check("anchored: Ke1≈2.10", abs(ke_a["Ke1"] - Ke_anc) < 0.01)
total += 1; passed += check("anchored: Ke2=0", ke_a["Ke2"] == 0.0)


# ═══ Test 9: Bug 2 — 第二次调用不崩 ═══
print("\n═══ TestBug2Smoke ═══")

page3 = StrainCalibrationPage()
total += 1; passed += check("_dialog_table is None initially", page3._dialog_table is None)
cols1 = page3._build_table_columns()
cols2 = page3._build_table_columns()
total += 1; passed += check("_build_table_columns idempotent", cols1 == cols2)
t1 = QTableWidget(len(page3._levels) + 1, len(cols1))
t2 = QTableWidget(len(page3._levels) + 1, len(cols1))
page3._populate_table(t1)
page3._populate_table(t2)
total += 1; passed += check("_populate_table on 2 tables OK", True)

# 3-tuple extract
l, r, m = page3._extract_table_data(t1)
total += 1; passed += check("extract 3-tuple works", len(l) > 0 and isinstance(m, dict))


# ═══ Test 10: 备注行 ComboBox roundtrip ═══
print("\n═══ TestAnnotationComboRoundtrip ═══")

p_ann = StrainCalibrationPage()
p_ann._config.update({"grating_kind": "dual_both", "n_cycles": 1, "mode": "tension_only"})
p_ann._levels = p_ann._generate_default_levels()
cols_ann = p_ann._build_table_columns()
t_ann = QTableWidget(len(p_ann._levels) + 1, len(cols_ann))
t_ann.setHorizontalHeaderLabels(cols_ann)

# 模拟 _open_readings 中的备注行 ComboBox 放置
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

# 灰显 G1/G2 其余列
for gi in [1, 2]:
    for col in grating_cols[gi][1:]:
        from PyQt6.QtWidgets import QTableWidgetItem as _QI
        from PyQt6.QtGui import QColor as _QC
        gi_item = _QI("—")
        gi_item.setFlags(Qt.ItemFlag.NoItemFlags)
        gi_item.setBackground(_QC(230, 230, 230))
        t_ann.setItem(0, col, gi_item)

# extract → 应读到 grating_map
ext_l, ext_r, ext_m = p_ann._extract_table_data(t_ann)
total += 1; passed += check("combo: G1=A1-W1", ext_m.get("G1") == "A1-W1")
total += 1; passed += check("combo: G2=A1-W2", ext_m.get("G2") == "A1-W2")


# ═══ Summary ═══
print(f"\n{'='*50}")
print(f"RESULTS: {passed}/{total} passed")
if passed == total:
    print("ALL TESTS PASSED")
    sys.exit(0)
else:
    print(f"FAILURES: {total - passed}")
    sys.exit(1)
