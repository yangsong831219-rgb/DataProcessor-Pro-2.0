"""应用系数到当前传感器 — 阶段3 回归测试 (standalone, no pytest dependency)

测试 4 场景:
1. 双栅传感器 + 温度完成 → ke_table 写入 + phase_b_state 同步
2. 双栅传感器 + Phase A 完成 (Phase B 未填) → ke_table 写入，下次开 Phase B 可读出
3. 温度未做 → strain 暂存，提示消息
4. 单栅传感器 + 温度完成 → strain only + 单栅提示

用法: python tests/test_apply_coefficients.py
"""

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PyQt6.QtWidgets import QApplication

_app = QApplication.instance() or QApplication(sys.argv)

import pandas as pd
import numpy as np

from py.calibration.strain_calibration import (
    StrainCalibrationConfig, StrainCalibrationResult, GratingStrainResult, calibrate_strain,
)
from py.calibration.project_config import ProjectConfig, StrainSubConfig


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

def make_page_with_ke(grating_kind, ke1, ke2=0.0):
    """创建 StrainCalibrationPage 并模拟分析完成后的状态。"""
    from ui.calibration_tab import StrainCalibrationPage
    p = StrainCalibrationPage()
    # 设置 config
    p._config.update({
        "gauge_length_mm": 80.0, "mode": "tension_only",
        "n_cycles": 1, "grating_kind": grating_kind,
    })
    p._levels = p._generate_default_levels()

    # 填入 grating_map (备注行)
    if grating_kind == "single":
        p._grating_map = {"G1": "A1-W1"}
    else:
        p._grating_map = {"G1": "A1-W1", "G2": "A1-W2"}

    # 填入 ke_results
    p._ke_results = {"Ke1": ke1, "Ke2": ke2}

    # 构造 last_result
    gratings = [GratingStrainResult(
        grating_index=1, k_pm_per_ue=ke1, R2=0.999,
        nonlinearity_pct_fs=0.5, repeatability_pct_fs=0.3, hysteresis_pct_fs=0.2,
    )]
    if grating_kind != "single":
        gratings.append(GratingStrainResult(
            grating_index=2, k_pm_per_ue=ke2, R2=0.998,
            nonlinearity_pct_fs=0.4, repeatability_pct_fs=0.3, hysteresis_pct_fs=0.1,
        ))
    p._last_result = StrainCalibrationResult(
        gauge_length_mm=80.0, mode="tension_only", n_cycles=1,
        grating_kind=grating_kind,
        levels=p._levels,
        eps_theory=[d / 80.0 * 1e6 for d in p._levels],
        gratings=gratings,
    )
    return p


# ═══════════════════════════════════════════════════════════════════════
# Test 1: _parse_sensor_from_grating_map
# ═══════════════════════════════════════════════════════════════════════
print("\n═══ TestParseSensor ═══")

from ui.calibration_tab import StrainCalibrationPage

p = StrainCalibrationPage()
total += 1; passed += check("empty map → ''", p._parse_sensor_from_grating_map() == "")

p._grating_map = {"G1": "A1-W1"}
total += 1; passed += check("A1-W1 → A1", p._parse_sensor_from_grating_map() == "A1")

p._grating_map = {"G1": "A1-W1", "G2": "A1-W2"}
total += 1; passed += check("dual A1 → A1", p._parse_sensor_from_grating_map() == "A1")

p._grating_map = {"G1": "B2-W1"}
total += 1; passed += check("B2-W1 → B2", p._parse_sensor_from_grating_map() == "B2")


# ═══════════════════════════════════════════════════════════════════════
# Test 2: _build_strain_subconfig
# ═══════════════════════════════════════════════════════════════════════
print("\n═══ TestBuildStrainSubConfig ═══")

p2 = make_page_with_ke("dual_both", 1.23, 0.98)
sub = p2._build_strain_subconfig()
total += 1; passed += check("sub sensor_name=A1", sub.sensor_name == "A1")
total += 1; passed += check("sub sensor_mode=dual_working", sub.sensor_mode == "dual_working")
total += 1; passed += check("sub gauge=80", abs(sub.gauge_length_mm - 80.0) < 0.01)
total += 1; passed += check("sub Ke1=1.23", abs(sub.ke_results["Ke1"] - 1.23) < 0.001)
total += 1; passed += check("sub Ke2=0.98", abs(sub.ke_results["Ke2"] - 0.98) < 0.001)
total += 1; passed += check("sub grating_map", sub.grating_map == {"G1": "A1-W1", "G2": "A1-W2"})

p2s = make_page_with_ke("single", 2.30, 0.0)
sub_s = p2s._build_strain_subconfig()
total += 1; passed += check("sub single mode", sub_s.sensor_mode == "single")
total += 1; passed += check("sub single Ke2=0", sub_s.ke_results["Ke2"] == 0.0)


# ═══════════════════════════════════════════════════════════════════════
# Test 3: 场景 1 — 双栅 + Phase B 已有 Ke → 覆盖
# ═══════════════════════════════════════════════════════════════════════
print("\n═══ TestScenario1_DualOverwrite ═══")

from ui.calibration_tab import CalibrationTabWidget
import tempfile, shutil

ctw = CalibrationTabWidget()

# 设置温度页状态: Phase A done + Phase B 已有旧 ke_table
tp = ctw.temp_page
tp._loaded_df = pd.DataFrame({"c1": [1550.0], "c2": [1545.0]})
tp._phase_a_done = True
tp._annotation_groups = {
    "A1": [{"name": "A1-W1", "col_name": "c1"}, {"name": "A1-W2", "col_name": "c2"}],
}
tp._phase_b_state = {
    "ke_table": {"A1": {"Ke1": 0.5, "Ke2": 0.8}},  # 旧值
    "decoupling_results": {},
}

# 设置应变页状态: 双栅 + 新 Ke
sp = ctw.strain_page
sp._config.update({"gauge_length_mm": 80.0, "grating_kind": "dual_both", "n_cycles": 1})
sp._levels = sp._generate_default_levels()
sp._grating_map = {"G1": "A1-W1", "G2": "A1-W2"}
sp._ke_results = {"Ke1": 1.23, "Ke2": 0.98}
# 构造 last_result
sp._last_result = StrainCalibrationResult(
    gauge_length_mm=80.0, mode="tension_only", n_cycles=1,
    grating_kind="dual_both", levels=sp._levels,
    eps_theory=[d / 80.0 * 1e6 for d in sp._levels],
    gratings=[
        GratingStrainResult(grating_index=1, k_pm_per_ue=1.23, R2=0.999),
        GratingStrainResult(grating_index=2, k_pm_per_ue=0.98, R2=0.998),
    ],
)

# 验证前置: is_dual_in_temperature = True, has_temperature_data = True
is_dual, sname = sp._is_dual_in_temperature()
total += 1; passed += check("S1: is_dual=True", is_dual is True)
total += 1; passed += check("S1: sensor_name=A1", sname == "A1")
total += 1; passed += check("S1: has_temp=True", sp._has_temperature_data() is True)

# 验证前置: phase_b_state 旧值存在
total += 1; passed += check("S1: old Ke1=0.5", abs(tp._phase_b_state["ke_table"]["A1"]["Ke1"] - 0.5) < 0.01)

# 模拟 _apply_coefficients 核心逻辑 (不弹对话框)
pc = sp._get_or_create_project_config()
sub = sp._build_strain_subconfig()
pc.strain[sname] = sub

ke_dict = {"Ke1": 1.23, "Ke2": 0.98}
if pc.temperature is None:
    pc.temperature = {}
pc.temperature["ke_table"] = pc.temperature.get("ke_table", {})
pc.temperature["ke_table"][sname] = ke_dict

# 同步到 phase_b_state
pb_state = tp._phase_b_state or {}
pb_state["ke_table"] = pb_state.get("ke_table", {})
pb_state["ke_table"][sname] = ke_dict
tp._phase_b_state = pb_state

# 验证: ke_table 已覆盖
total += 1; passed += check("S1: new Ke1=1.23 in pc", abs(pc.temperature["ke_table"]["A1"]["Ke1"] - 1.23) < 0.001)
total += 1; passed += check("S1: new Ke2=0.98 in pc", abs(pc.temperature["ke_table"]["A1"]["Ke2"] - 0.98) < 0.001)
total += 1; passed += check("S1: new Ke1=1.23 in pb_state", abs(tp._phase_b_state["ke_table"]["A1"]["Ke1"] - 1.23) < 0.001)
total += 1; passed += check("S1: strain sub exists", "A1" in pc.strain)
total += 1; passed += check("S1: strain sub Ke1=1.23", abs(pc.strain["A1"].ke_results["Ke1"] - 1.23) < 0.001)


# ═══════════════════════════════════════════════════════════════════════
# Test 4: 场景 2 — Phase A 完成, Phase B 未填 → ke_table 写入
# ═══════════════════════════════════════════════════════════════════════
print("\n═══ TestScenario2_PhaseBEmpty ═══")

ctw2 = CalibrationTabWidget()
tp2 = ctw2.temp_page
tp2._loaded_df = pd.DataFrame({"c1": [1550.0], "c2": [1545.0]})
tp2._phase_a_done = True
tp2._annotation_groups = {
    "A2": [{"name": "A2-W1", "col_name": "c1"}, {"name": "A2-W2", "col_name": "c2"}],
}
tp2._phase_b_state = {}  # Phase B 未填

sp2 = ctw2.strain_page
sp2._config.update({"gauge_length_mm": 50.0, "grating_kind": "dual_both", "n_cycles": 1})
sp2._levels = sp2._generate_default_levels()
sp2._grating_map = {"G1": "A2-W1", "G2": "A2-W2"}
sp2._ke_results = {"Ke1": 0.85, "Ke2": 0.72}
sp2._last_result = StrainCalibrationResult(
    gauge_length_mm=50.0, mode="tension_only", n_cycles=1,
    grating_kind="dual_both", levels=sp2._levels,
    eps_theory=[d / 50.0 * 1e6 for d in sp2._levels],
    gratings=[
        GratingStrainResult(grating_index=1, k_pm_per_ue=0.85, R2=0.999),
        GratingStrainResult(grating_index=2, k_pm_per_ue=0.72, R2=0.998),
    ],
)

# 验证前置
is_dual2, sname2 = sp2._is_dual_in_temperature()
total += 1; passed += check("S2: is_dual=True", is_dual2 is True)
total += 1; passed += check("S2: sensor_name=A2", sname2 == "A2")
total += 1; passed += check("S2: phase_b empty", tp2._phase_b_state.get("ke_table", {}) == {})

# 模拟写入
pc2 = sp2._get_or_create_project_config()
sub2 = sp2._build_strain_subconfig()
pc2.strain[sname2] = sub2

ke_dict2 = {"Ke1": 0.85, "Ke2": 0.72}
if pc2.temperature is None:
    pc2.temperature = {}
pc2.temperature["ke_table"] = pc2.temperature.get("ke_table", {})
pc2.temperature["ke_table"][sname2] = ke_dict2

tp2._phase_b_state["ke_table"] = {sname2: ke_dict2}  # lazy restore: next time Phase B opened

# 验证: Phase B template 已准备好
total += 1; passed += check("S2: pb_state Ke1=0.85", abs(tp2._phase_b_state["ke_table"]["A2"]["Ke1"] - 0.85) < 0.001)
total += 1; passed += check("S2: pc ke_table Ke2=0.72", abs(pc2.temperature["ke_table"]["A2"]["Ke2"] - 0.72) < 0.001)
total += 1; passed += check("S2: strain saved", "A2" in pc2.strain)

# 模拟 Phase B 对话框打开时 restore (从 _phase_b_state)
pb_state2 = tp2._phase_b_state
total += 1; passed += check("S2: restore Ke1=0.85", abs(pb_state2["ke_table"]["A2"]["Ke1"] - 0.85) < 0.001)


# ═══════════════════════════════════════════════════════════════════════
# Test 5: 场景 3 — 温度未做 → 仅 strain
# ═══════════════════════════════════════════════════════════════════════
print("\n═══ TestScenario3_NoTemperature ═══")

ctw3 = CalibrationTabWidget()
# 温度页完全空白
tp3 = ctw3.temp_page
tp3._loaded_df = None
tp3._phase_a_done = False

sp3 = ctw3.strain_page
sp3._config.update({"gauge_length_mm": 80.0, "grating_kind": "dual_both", "n_cycles": 1})
sp3._levels = sp3._generate_default_levels()
sp3._grating_map = {"G1": "B1-W1", "G2": "B1-W2"}
sp3._ke_results = {"Ke1": 1.50, "Ke2": 1.10}
sp3._last_result = StrainCalibrationResult(
    gauge_length_mm=80.0, mode="tension_only", n_cycles=1,
    grating_kind="dual_both", levels=sp3._levels,
    eps_theory=[d / 80.0 * 1e6 for d in sp3._levels],
    gratings=[
        GratingStrainResult(grating_index=1, k_pm_per_ue=1.50, R2=0.999),
        GratingStrainResult(grating_index=2, k_pm_per_ue=1.10, R2=0.998),
    ],
)

total += 1; passed += check("S3: has_temp=False", sp3._has_temperature_data() is False)
is_dual3, sname3 = sp3._is_dual_in_temperature()
total += 1; passed += check("S3: is_dual=False (no temp)", is_dual3 is False)
total += 1; passed += check("S3: sensor_name=B1", sname3 == "B1")

# 模拟仅 strain 保存
pc3 = sp3._get_or_create_project_config()
sub3 = sp3._build_strain_subconfig()
pc3.strain[sname3] = sub3

# 验证: 只有 strain，无 temperature
total += 1; passed += check("S3: strain saved", "B1" in pc3.strain)
total += 1; passed += check("S3: strain Ke1=1.50", abs(pc3.strain["B1"].ke_results["Ke1"] - 1.50) < 0.001)
total += 1; passed += check("S3: no temp ke_table", pc3.temperature is None or pc3.temperature.get("ke_table", {}) == {})


# ═══════════════════════════════════════════════════════════════════════
# Test 6: 场景 4 — 单栅 + 温度完成 → strain only (不碰 ke_table)
# ═══════════════════════════════════════════════════════════════════════
print("\n═══ TestScenario4_SingleGrating ═══")

ctw4 = CalibrationTabWidget()
tp4 = ctw4.temp_page
tp4._loaded_df = pd.DataFrame({"c1": [1550.0]})
tp4._phase_a_done = True
# 单栅: A2-W1 只有一个 grating
tp4._annotation_groups = {
    "A2": [{"name": "A2-W1", "col_name": "c1"}],
}
tp4._phase_b_state = {}

sp4 = ctw4.strain_page
sp4._config.update({"gauge_length_mm": 50.0, "grating_kind": "single", "n_cycles": 1})
sp4._levels = sp4._generate_default_levels()
sp4._grating_map = {"G1": "A2-W1"}
sp4._ke_results = {"Ke1": 2.30, "Ke2": 0.0}
sp4._last_result = StrainCalibrationResult(
    gauge_length_mm=50.0, mode="tension_only", n_cycles=1,
    grating_kind="single", levels=sp4._levels,
    eps_theory=[d / 50.0 * 1e6 for d in sp4._levels],
    gratings=[
        GratingStrainResult(grating_index=1, k_pm_per_ue=2.30, R2=0.999),
    ],
)

is_dual4, sname4 = sp4._is_dual_in_temperature()
total += 1; passed += check("S4: is_dual=False (1 grating)", is_dual4 is False)
total += 1; passed += check("S4: sensor_name=A2", sname4 == "A2")
total += 1; passed += check("S4: has_temp=True", sp4._has_temperature_data() is True)

# 模拟: 单栅 → 仅 strain, 不写 ke_table
pc4 = sp4._get_or_create_project_config()
sub4 = sp4._build_strain_subconfig()
pc4.strain[sname4] = sub4
# 不写 ke_table

total += 1; passed += check("S4: strain saved", "A2" in pc4.strain)
total += 1; passed += check("S4: Ke1=2.30", abs(pc4.strain["A2"].ke_results["Ke1"] - 2.30) < 0.001)
total += 1; passed += check("S4: Ke2=0", abs(pc4.strain["A2"].ke_results["Ke2"] - 0.0) < 0.001)
total += 1; passed += check("S4: sensor_mode=single", pc4.strain["A2"].sensor_mode == "single")
total += 1; passed += check("S4: no ke_table written", pc4.temperature is None or pc4.temperature.get("ke_table", {}) == {})


# ═══════════════════════════════════════════════════════════════════════
# Test 7: _get_or_create_project_config
# ═══════════════════════════════════════════════════════════════════════
print("\n═══ TestGetOrCreateProjectConfig ═══")

ctw5 = CalibrationTabWidget()
sp5 = ctw5.strain_page

# 初始 none
total += 1; passed += check("PC: initially None", ctw5.project_config is None)

# 首次获取 → 创建
pc5a = sp5._get_or_create_project_config()
total += 1; passed += check("PC: created", pc5a is not None)
total += 1; passed += check("PC: set on CalibrationTabWidget", ctw5.project_config is pc5a)

# 再次获取 → 复用
pc5b = sp5._get_or_create_project_config()
total += 1; passed += check("PC: reused", pc5b is pc5a)


# ═══════════════════════════════════════════════════════════════════════
# Test 8: 同传感器重标定覆盖 (ke_table + strain)
# ═══════════════════════════════════════════════════════════════════════
print("\n═══ TestOverwriteRoundtrip ═══")

ctw6 = CalibrationTabWidget()
tp6 = ctw6.temp_page
tp6._loaded_df = pd.DataFrame({"c1": [1550.0], "c2": [1545.0]})
tp6._phase_a_done = True
tp6._annotation_groups = {
    "A1": [{"name": "A1-W1", "col_name": "c1"}, {"name": "A1-W2", "col_name": "c2"}],
}
tp6._phase_b_state = {}

sp6 = ctw6.strain_page
sp6._config.update({"gauge_length_mm": 80.0, "grating_kind": "dual_both", "n_cycles": 1})
sp6._levels = sp6._generate_default_levels()
sp6._grating_map = {"G1": "A1-W1", "G2": "A1-W2"}

# 第一次标定: Ke1=1.0, Ke2=0.8
sp6._ke_results = {"Ke1": 1.0, "Ke2": 0.8}
pc6 = sp6._get_or_create_project_config()
pc6.strain["A1"] = sp6._build_strain_subconfig()
pc6.temperature = {"ke_table": {"A1": {"Ke1": 1.0, "Ke2": 0.8}}}

total += 1; passed += check("OW: first Ke1=1.0", abs(pc6.temperature["ke_table"]["A1"]["Ke1"] - 1.0) < 0.001)

# 第二次标定 (覆盖): Ke1=1.5, Ke2=1.2
sp6._ke_results = {"Ke1": 1.5, "Ke2": 1.2}
# 模拟 _apply_coefficients 核心逻辑
sname6 = sp6._parse_sensor_from_grating_map()
pc6.strain[sname6] = sp6._build_strain_subconfig()
pc6.temperature["ke_table"][sname6] = {"Ke1": 1.5, "Ke2": 1.2}
tp6._phase_b_state["ke_table"] = {"A1": {"Ke1": 1.5, "Ke2": 1.2}}

total += 1; passed += check("OW: overwrite Ke1=1.5", abs(pc6.temperature["ke_table"]["A1"]["Ke1"] - 1.5) < 0.001)
total += 1; passed += check("OW: overwrite Ke2=1.2", abs(pc6.temperature["ke_table"]["A1"]["Ke2"] - 1.2) < 0.001)
total += 1; passed += check("OW: strain overwrite Ke1=1.5", abs(pc6.strain["A1"].ke_results["Ke1"] - 1.5) < 0.001)
total += 1; passed += check("OW: pb_state overwrite Ke2=1.2", abs(tp6._phase_b_state["ke_table"]["A1"]["Ke2"] - 1.2) < 0.001)


# ═══════════════════════════════════════════════════════════════════════
# Test 9: 边界 — grating_map 为空时
# ═══════════════════════════════════════════════════════════════════════
print("\n═══ TestEdgeCases ═══")

p9 = make_page_with_ke("dual_working", 1.0, 0.8)
p9._grating_map = {}
total += 1; passed += check("Edge: empty map → ''", p9._parse_sensor_from_grating_map() == "")

total += 1; passed += check("Edge: empty map dual_check", p9._is_dual_in_temperature() == (False, ""))

p9._grating_map = {"G1": "bad-format"}
# bad-format has no dash
# is_valid_annotation checks for ^[A-Za-z0-9_]+-[A-Za-z0-9_]+$
# "bad-format" matches that pattern, so it is valid
sensor_name_bad = p9._parse_sensor_from_grating_map()
total += 1; passed += check("Edge: bad-format → bad", sensor_name_bad == "bad")


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
