"""Phase B 单栅传感器排除 — 阶段4 回归测试 (standalone, no pytest dependency)

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

# No QApplication needed for PhaseBWorker (pure computation)


def check(label, cond):
    if cond:
        print(f"  PASS: {label}")
        return 1
    else:
        print(f"  FAIL: {label}")
        return 0


passed = 0
total = 0


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


# ═══════════════════════════════════════════════════════════════════════
# Test 1: PhaseBWorker — 单栅标记 single_grating=True
# ═══════════════════════════════════════════════════════════════════════
print("\n═══ TestSingleGratingFlag ═══")

from ui.calibration_tab import PhaseBWorker

cols = ["c1"]
df = make_df(cols, n_rows=100)
groups = make_groups({"B1": ["c1"]})
seff = make_S_eff(groups, slope=28.5)
time_h = np.arange(100) * 2.0 / 3600.0

worker = PhaseBWorker(df, time_h, cols, groups, seff, {}, 2.0)
worker.run()
result = worker._last_result

sensors = result["sensors"]
total += 1; passed += check("B1 in sensors", "B1" in sensors)
total += 1; passed += check("B1 single_grating=True", sensors["B1"]["single_grating"] is True)
total += 1; passed += check("B1 has S1", abs(sensors["B1"]["S1"] - 28.5) < 0.01)
total += 1; passed += check("B1 has T_base", abs(sensors["B1"]["T_base"] - 25.0) < 0.01)
total += 1; passed += check("B1 no eps_corr", "eps_corr" not in sensors["B1"])


# ═══════════════════════════════════════════════════════════════════════
# Test 2: PhaseBWorker — 双栅正确解耦
# ═══════════════════════════════════════════════════════════════════════
print("\n═══ TestDualGratingDecouple ═══")

cols2 = ["c1", "c2"]
df2 = make_df(cols2, n_rows=100)
groups2 = make_groups({"A1": ["c1", "c2"]})
seff2 = make_S_eff(groups2, slope=30.0)
# 提供 Ke
coeffs2 = {"A1": {"Ke1": 1.2, "Ke2": 1.1}}

worker2 = PhaseBWorker(df2, time_h, cols2, groups2, seff2, coeffs2, 2.0)
worker2.run()
result2 = worker2._last_result

sensors2 = result2["sensors"]
total += 1; passed += check("A1 in sensors", "A1" in sensors2)
total += 1; passed += check("A1 single_grating=False", sensors2["A1"]["single_grating"] is False)
total += 1; passed += check("A1 has eps_corr", "eps_corr" in sensors2["A1"])
total += 1; passed += check("A1 has dT_corr", "dT_corr" in sensors2["A1"])
total += 1; passed += check("A1 has T_abs", "T_abs" in sensors2["A1"])
total += 1; passed += check("A1 eps_corr not all NaN", not np.all(np.isnan(sensors2["A1"]["eps_corr"])))
# S1/S2 正确传递
total += 1; passed += check("A1 S1=30", abs(sensors2["A1"]["S1"] - 30.0) < 0.01)
total += 1; passed += check("A1 S2=30", abs(sensors2["A1"]["S2"] - 30.0) < 0.01)


# ═══════════════════════════════════════════════════════════════════════
# Test 3: 混合场景 — 1 单栅 + 3 双栅
# ═══════════════════════════════════════════════════════════════════════
print("\n═══ TestMixed1Single3Dual ═══")

cols3 = ["c1", "c2", "c3", "c4", "c5", "c6", "c7"]
df3 = make_df(cols3, n_rows=100)
groups3 = make_groups({
    "A1": ["c1", "c2"],   # 双栅
    "A2": ["c3", "c4"],   # 双栅
    "B1": ["c5"],         # 单栅
    "B2": ["c6", "c7"],   # 双栅
})
seff3 = make_S_eff(groups3, slope=30.0)
coeffs3 = {
    "A1": {"Ke1": 1.2, "Ke2": 1.1},
    "A2": {"Ke1": 0.9, "Ke2": 1.0},
    "B2": {"Ke1": 1.3, "Ke2": 1.2},
}

worker3 = PhaseBWorker(df3, time_h, cols3, groups3, seff3, coeffs3, 2.0)
worker3.run()
result3 = worker3._last_result
sensors3 = result3["sensors"]

# 4 sensors total
total += 1; passed += check("4 sensors total", len(sensors3) == 4)

# 单栅 B1
total += 1; passed += check("B1 single_grating=True", sensors3["B1"]["single_grating"] is True)
total += 1; passed += check("B1 no eps_corr", "eps_corr" not in sensors3["B1"])

# 3 双栅
dual = {k: v for k, v in sensors3.items() if not v.get("single_grating", False)}
total += 1; passed += check("3 dual sensors", len(dual) == 3)
total += 1; passed += check("A1 dual ok", "eps_corr" in dual["A1"])
total += 1; passed += check("A2 dual ok", "eps_corr" in dual["A2"])
total += 1; passed += check("B2 dual ok", "eps_corr" in dual["B2"])

# 单栅 S_eff 仍在 (不会丢失)
total += 1; passed += check("B1 S1=30 (S_eff preserved)", abs(sensors3["B1"]["S1"] - 30.0) < 0.01)
total += 1; passed += check("B1 T_base=25", abs(sensors3["B1"]["T_base"] - 25.0) < 0.01)


# ═══════════════════════════════════════════════════════════════════════
# Test 4: _render_result_table 过滤
# ═══════════════════════════════════════════════════════════════════════
print("\n═══ TestRenderResultTableFilter ═══")

from PyQt6.QtWidgets import QApplication, QDialog, QVBoxLayout
_app = QApplication.instance() or QApplication(sys.argv)

from ui.calibration_tab import PhaseBDialog

# 构造 PhaseBDialog (不 exec)
dlg = PhaseBDialog(df3, groups3, {"S_eff": seff3}, parent=None)
# 手动设置 last_result (模拟 _on_done)
result3["sensors"] = sensors3  # already set
dlg._last_result = result3
dlg._coeffs = coeffs3

# 调用 _render_result_table
dlg._render_result_table(sensors3, seff3)

# 验证: result_table 只有 3 行 (3 双栅)
total += 1; passed += check("result_table rows=3", dlg.result_table.rowCount() == 3)

# 验证: 3 行都是双栅传感器
sensor_names_in_table = []
for r in range(dlg.result_table.rowCount()):
    sensor_names_in_table.append(dlg.result_table.item(r, 0).text())
total += 1; passed += check("B1 not in table", "B1" not in sensor_names_in_table)
total += 1; passed += check("A1 in table", "A1" in sensor_names_in_table)
total += 1; passed += check("A2 in table", "A2" in sensor_names_in_table)
total += 1; passed += check("B2 in table", "B2" in sensor_names_in_table)

# 验证: 单栅排除提示 label 存在
total += 1; passed += check("excluded label exists", dlg._single_excluded_label is not None)
total += 1; passed += check("excluded label text contains B1", "B1" in (dlg._single_excluded_label.text() or ""))

dlg.close()
del dlg


# ═══════════════════════════════════════════════════════════════════════
# Test 5: _write_state_to_main_page 过滤
# ═══════════════════════════════════════════════════════════════════════
print("\n═══ TestWriteStateFilter ═══")

from ui.calibration_tab import TemperatureCalibrationPage

tp = TemperatureCalibrationPage()
tp._loaded_df = df3.copy()
# 需要 annotation_dict 让 _parse_annotation_row 解析
tp._annotation_groups = groups3

dlg2 = PhaseBDialog(df3, groups3, {"S_eff": seff3}, parent=tp)
dlg2._last_result = result3
dlg2._coeffs = coeffs3
# 需要 coef_table 有行 (模拟 Ke 表)
dlg2.coef_table.setRowCount(3)
from PyQt6.QtWidgets import QTableWidgetItem as _QI
for i, (sn, ke) in enumerate([("A1", {"Ke1": 1.2, "Ke2": 1.1}),
                                 ("A2", {"Ke1": 0.9, "Ke2": 1.0}),
                                 ("B2", {"Ke1": 1.3, "Ke2": 1.2})]):
    dlg2.coef_table.setItem(i, 0, _QI(sn))
    dlg2.coef_table.setItem(i, 1, _QI(str(ke["Ke1"])))
    dlg2.coef_table.setItem(i, 2, _QI(str(ke["Ke2"])))

dlg2._write_state_to_main_page()

# 验证: decoupling_results 只有 3 个双栅
dec = tp._phase_b_state.get("decoupling_results", {})
total += 1; passed += check("decoupling has 3 entries", len(dec) == 3)
total += 1; passed += check("B1 not in decoupling", "B1" not in dec)
total += 1; passed += check("A1 in decoupling", "A1" in dec)
total += 1; passed += check("A2 in decoupling", "A2" in dec)
total += 1; passed += check("B2 in decoupling", "B2" in dec)

# 验证: ke_table 只有 3 个双栅
ke = tp._phase_b_state.get("ke_table", {})
total += 1; passed += check("ke_table has 3 entries", len(ke) == 3)
total += 1; passed += check("B1 not in ke_table", "B1" not in ke)

dlg2.close()
del dlg2


# ═══════════════════════════════════════════════════════════════════════
# Test 6: 全单栅边界 — 只有单栅传感器
# ═══════════════════════════════════════════════════════════════════════
print("\n═══ TestAllSingle ═══")

cols4 = ["c1", "c2"]
df4 = make_df(cols4, n_rows=100)
groups4 = make_groups({"B1": ["c1"], "B2": ["c2"]})
seff4 = make_S_eff(groups4, slope=28.0)

worker4 = PhaseBWorker(df4, time_h, cols4, groups4, seff4, {}, 2.0)
worker4.run()
result4 = worker4._last_result
sensors4 = result4["sensors"]

total += 1; passed += check("all single: 2 sensors", len(sensors4) == 2)
total += 1; passed += check("B1 single=True", sensors4["B1"]["single_grating"] is True)
total += 1; passed += check("B2 single=True", sensors4["B2"]["single_grating"] is True)

# render: result_table 应为 0 行
dlg4 = PhaseBDialog(df4, groups4, {"S_eff": seff4}, parent=None)
dlg4._last_result = result4
dlg4._render_result_table(sensors4, seff4)
total += 1; passed += check("all single: table rows=0", dlg4.result_table.rowCount() == 0)
total += 1; passed += check("all single: excluded label exists", dlg4._single_excluded_label is not None)
label_text = dlg4._single_excluded_label.text() or ""
total += 1; passed += check("all single: label mentions B1,B2", "B1" in label_text and "B2" in label_text)

dlg4.close()
del dlg4


# ═══════════════════════════════════════════════════════════════════════
# Test 7: Phase A S_eff 不受影响 (单栅温度系数保留)
# ═══════════════════════════════════════════════════════════════════════
print("\n═══ TestPhaseASEffPreserved ═══")

# 保存原始 S_eff
original_seff = make_S_eff(groups3, slope=30.0)
original_b1_slope = original_seff["c5"]["slope"]

# 运行 Phase B worker
worker7 = PhaseBWorker(df3, time_h, cols3, groups3, original_seff, coeffs3, 2.0)
worker7.run()

# 验证: S_eff 未被修改 (PhaseBWorker 只读)
total += 1; passed += check("S_eff B1 slope unchanged", abs(original_seff["c5"]["slope"] - original_b1_slope) < 0.001)
total += 1; passed += check("S_eff B1 r2 unchanged", abs(original_seff["c5"]["r2"] - 0.995) < 0.001)
total += 1; passed += check("S_eff B1 T_base unchanged", abs(original_seff["c5"]["T_base"] - 25.0) < 0.001)


# ═══════════════════════════════════════════════════════════════════════
# Summary
# ═══════════════════════════════════════════════════════════════════════
print(f"\n{'='*50}")
print(f"RESULTS: {passed}/{total} passed")
if passed == total:
    print("ALL TESTS PASSED")
    sys.exit(0)
else:
    print(f"FAILURES: {total - passed}")
    sys.exit(1)
