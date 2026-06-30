"""项目配置共享保存/加载 — 阶段5 回归测试 (standalone, no pytest dependency)

测试:
1. capture: 从温度页 + 应变页构造 ProjectConfig
2. restore: 从 ProjectConfig 回填温度页 + 应变页
3. roundtrip: capture → save → load → restore 完整链路
4. strain page save: 从应变页调用保存
5. load restores both modules: 温度 + 应变都回填
6. empty project: 温度无数据场景

用法: python tests/test_project_save_load.py
"""

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import json
import tempfile
import shutil

from PyQt6.QtWidgets import QApplication, QTableWidget
_app = QApplication.instance() or QApplication(sys.argv)

import pandas as pd
import numpy as np

from dp_engine.calibration.project_config import (
    ProjectConfig, StrainSubConfig, ProjectConfigManager, PROFILES_DIR,
)
from ui.calibration_tab import CalibrationTabWidget, StrainCalibrationPage
from dp_engine.calibration.strain_calibration import (
    StrainCalibrationResult, GratingStrainResult,
)


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

def make_temp_df():
    """构造含暗号的温度测试数据"""
    return pd.DataFrame({
        "Timestamp": range(100),
        "Unnamed: 17": [1550.0 + i * 0.5 for i in range(100)],
        "Unnamed: 18": [1545.0 + i * 0.3 for i in range(100)],
    })


def setup_temp_page(tp, df):
    """设置温度页状态: 加载文件 + 暗号标注 + Phase A 完成 + Phase B 完成"""
    tp._loaded_df = df.copy()
    tp._annotation_dict = {"Unnamed: 17": "A1-W1", "Unnamed: 18": "A1-W2"}
    tp._annotation_meta = {"format": "hyperion_peaks"}
    tp._time_col_idx = 0
    tp.file_path_edit.setText("/fake/test.txt")

    tp._detection_params = {"rolling_window": 25, "std_percentile": 45.0}
    tp._annotation_groups = {
        "A1": [
            {"name": "A1-W1", "col_name": "Unnamed: 17"},
            {"name": "A1-W2", "col_name": "Unnamed: 18"},
        ],
    }

    tp._phase_a_done = True
    tp._last_result = {
        "S_eff": {
            "Unnamed: 17": {"slope": 27.8, "r2": 0.995, "T_base": 25.0},
            "Unnamed: 18": {"slope": 26.2, "r2": 0.993, "T_base": 25.5},
        },
        "wavelength_cols": ["Unnamed: 17", "Unnamed: 18"],
        "plateaus": pd.DataFrame(),
        "df": tp._loaded_df,
    }

    tp._phase_a_state = {
        "annotation": dict(tp._annotation_dict),
        "groups": dict(tp._annotation_groups),
        "tmin": 10.0, "tmax": 70.0, "tstep": 10.0,
        "params": dict(tp._detection_params),
        "seff_result": tp._last_result,
    }
    tp._phase_b_state = {
        "ke_table": {"A1": {"Ke1": 1.2, "Ke2": 1.1}},
        "decoupling_results": {"A1": {"e_mean": 3.0, "e_std": 43.5, "e_range": 100.0, "rating": "良"}},
    }
    tp._phase_b_result = {
        "sensors": {"A1": {"eps_corr": [3.0] * 10, "eps_orig": [], "dT_corr": [],
                            "T_abs": [], "S1": 27.8, "S2": 26.2, "T_base": 25.25,
                            "single_grating": False}},
        "time_h": np.array([]),
        "df": tp._loaded_df,
    }


def setup_strain_page(sp):
    """设置应变页状态: 标定完成 + Ke results"""
    sp._config.update({
        "gauge_length_mm": 80.0, "mode": "tension_only",
        "n_cycles": 3, "grating_kind": "dual_both",
    })
    sp._levels = sp._generate_default_levels()
    sp._grating_map = {"G1": "A1-W1", "G2": "A1-W2"}
    sp._ke_results = {"Ke1": 1.23, "Ke2": 0.98}
    sp._last_result = StrainCalibrationResult(
        gauge_length_mm=80.0, mode="tension_only", n_cycles=3,
        grating_kind="dual_both", levels=sp._levels,
        eps_theory=[d / 80.0 * 1e6 for d in sp._levels],
        gratings=[
            GratingStrainResult(grating_index=1, k_pm_per_ue=1.23, R2=0.999),
            GratingStrainResult(grating_index=2, k_pm_per_ue=0.98, R2=0.998),
        ],
    )


# ═══════════════════════════════════════════════════════════════════════
# Test 1: capture — 从双模块构造 ProjectConfig
# ═══════════════════════════════════════════════════════════════════════
print("\n═══ TestCapture ═══")

ctw = CalibrationTabWidget()
tp = ctw.temp_page
sp = ctw.strain_page
setup_temp_page(tp, make_temp_df())
setup_strain_page(sp)

# 先让 apply_coefficients 的逻辑把 strain 写入 project_config
sp._apply_coefficients.__code__  # no-op: just verify method exists

# 手动写入 strain 到 pc
from dp_engine.calibration.project_config import ProjectConfig
ctw.project_config = ProjectConfig.create_new("测试项目")
ctw.project_config.strain["A1"] = sp._build_strain_subconfig()

pc = ProjectConfigManager.capture(tp, sp)

total += 1; passed += check("pc name non-empty", pc.name != "")
total += 1; passed += check("pc has temperature", pc.temperature is not None)
total += 1; passed += check("pc temp annotation A1-W1", pc.temperature["annotation"].get("Unnamed: 17") == "A1-W1")
total += 1; passed += check("pc temp s_eff has key", len(pc.temperature["s_eff_results"]) == 2)
total += 1; passed += check("pc temp ke_table A1", "A1" in pc.temperature["ke_table"])
total += 1; passed += check("pc temp decoupling A1", "A1" in pc.temperature["decoupling_results"])
total += 1; passed += check("pc strain A1 exists", "A1" in pc.strain)
total += 1; passed += check("pc strain A1 Ke1", abs(pc.strain["A1"].ke_results["Ke1"] - 1.23) < 0.01)
total += 1; passed += check("pc validate passes", pc.validate() == [])


# ═══════════════════════════════════════════════════════════════════════
# Test 2: restore — 回填双模块活状态
# ═══════════════════════════════════════════════════════════════════════
print("\n═══ TestRestore ═══")

ctw2 = CalibrationTabWidget()
tp2 = ctw2.temp_page
sp2 = ctw2.strain_page

# 从 pc restore
ProjectConfigManager.restore(pc, tp2, sp2)

# 温度页验证
total += 1; passed += check("R: tp annotation", tp2._annotation_dict.get("Unnamed: 17") == "A1-W1")
total += 1; passed += check("R: tp phase_a_done", tp2._phase_a_done is True)
total += 1; passed += check("R: tp last_result S_eff", tp2._last_result is not None)
total += 1; passed += check("R: tp phase_a_state annotation", tp2._phase_a_state["annotation"].get("Unnamed: 17") == "A1-W1")
total += 1; passed += check("R: tp phase_b_state ke_table A1", "A1" in tp2._phase_b_state["ke_table"])
total += 1; passed += check("R: tp ke_table Ke1", abs(tp2._phase_b_state["ke_table"]["A1"]["Ke1"] - 1.2) < 0.01)

# 应变页验证
total += 1; passed += check("R: sp grating_map", sp2._grating_map == {"G1": "A1-W1", "G2": "A1-W2"})
total += 1; passed += check("R: sp ke_results", abs(sp2._ke_results["Ke1"] - 1.23) < 0.01)


# ═══════════════════════════════════════════════════════════════════════
# Test 3: save → load → restore 完整链路
# ═══════════════════════════════════════════════════════════════════════
print("\n═══ TestSaveLoadRoundtrip ═══")

tmp_dir = tempfile.mkdtemp(prefix="projsave_")
try:
    pc.name = "完整链路测试"

    # Save
    path = ProjectConfigManager.save(pc, dir_path=tmp_dir)
    total += 1; passed += check("SL: file exists", os.path.isfile(path))

    # List
    projects = ProjectConfigManager.list_all(dir_path=tmp_dir)
    total += 1; passed += check("SL: list has 1", len(projects) == 1)
    total += 1; passed += check("SL: _key correct", projects[0]["_key"] == "完整链路测试")

    # Load
    pc2 = ProjectConfigManager.load("完整链路测试", dir_path=tmp_dir)
    total += 1; passed += check("SL: name match", pc2.name == "完整链路测试")
    total += 1; passed += check("SL: temperature exists", pc2.temperature is not None)
    total += 1; passed += check("SL: strain A1 exists", "A1" in pc2.strain)
    total += 1; passed += check("SL: strain Ke1 match", abs(pc2.strain["A1"].ke_results["Ke1"] - 1.23) < 0.01)
    total += 1; passed += check("SL: temp ke_table A1", "A1" in pc2.temperature["ke_table"])

    # Restore to fresh modules
    ctw3 = CalibrationTabWidget()
    tp3 = ctw3.temp_page
    sp3 = ctw3.strain_page
    ProjectConfigManager.restore(pc2, tp3, sp3)

    total += 1; passed += check("SL: R tp annotation", tp3._annotation_dict.get("Unnamed: 17") == "A1-W1")
    total += 1; passed += check("SL: R sp ke_results", abs(sp3._ke_results.get("Ke1", 0) - 1.23) < 0.01)
    total += 1; passed += check("SL: R sp grating_map", sp3._grating_map.get("G1") == "A1-W1")

    # Delete
    ProjectConfigManager.delete("完整链路测试", dir_path=tmp_dir)
    total += 1; passed += check("SL: deleted", ProjectConfigManager.list_all(dir_path=tmp_dir) == [])

finally:
    shutil.rmtree(tmp_dir, ignore_errors=True)


# ═══════════════════════════════════════════════════════════════════════
# Test 4: 应变页 save → 温度页 load
# ═══════════════════════════════════════════════════════════════════════
print("\n═══ TestStrainPageSaveTempPageLoad ═══")

tmp_dir2 = tempfile.mkdtemp(prefix="s2t_")
try:
    ctw4 = CalibrationTabWidget()
    sp4 = ctw4.strain_page
    tp4 = ctw4.temp_page

    # 温度页已完成标定
    setup_temp_page(tp4, make_temp_df())

    # 应变页也完成标定
    setup_strain_page(sp4)
    ctw4.project_config = ProjectConfig.create_new("应变存温度取")
    ctw4.project_config.strain["A1"] = sp4._build_strain_subconfig()

    # 从应变页视角保存 (模拟用户点应变页的"保存项目")
    pc4 = ProjectConfigManager.capture(tp4, sp4)
    pc4.name = "应变存温度取"
    ProjectConfigManager.save(pc4, dir_path=tmp_dir2)

    # 从温度页视角加载 (模拟用户切换到温度页，点"加载项目")
    ctw4b = CalibrationTabWidget()
    tp4b = ctw4b.temp_page
    sp4b = ctw4b.strain_page

    pc4b = ProjectConfigManager.load("应变存温度取", dir_path=tmp_dir2)
    ProjectConfigManager.restore(pc4b, tp4b, sp4b)

    total += 1; passed += check("S2T: tp annotation", tp4b._annotation_dict.get("Unnamed: 17") == "A1-W1")
    total += 1; passed += check("S2T: sp ke_results", abs(sp4b._ke_results.get("Ke1", 0) - 1.23) < 0.01)
    total += 1; passed += check("S2T: sp grating_map", sp4b._grating_map.get("G1") == "A1-W1")
    total += 1; passed += check("S2T: tp ke_table A1", "A1" in tp4b._phase_b_state["ke_table"])
    total += 1; passed += check("S2T: tp phase_a_done", tp4b._phase_a_done is True)

finally:
    shutil.rmtree(tmp_dir2, ignore_errors=True)


# ═══════════════════════════════════════════════════════════════════════
# Test 5: 空温度项目 save/load
# ═══════════════════════════════════════════════════════════════════════
print("\n═══ TestEmptyTemperature ═══")

tmp_dir3 = tempfile.mkdtemp(prefix="empty_")
try:
    # 只有应变数据、温度完全未做
    ctw5 = CalibrationTabWidget()
    sp5 = ctw5.strain_page
    setup_strain_page(sp5)
    sp5._grating_map = {"G1": "B1-W1"}
    sp5._ke_results = {"Ke1": 1.50, "Ke2": 0.0}

    ctw5.project_config = ProjectConfig.create_new("空温度项目")
    ctw5.project_config.strain["B1"] = sp5._build_strain_subconfig()

    pc5 = ProjectConfigManager.capture(ctw5.temp_page, sp5)
    pc5.name = "空温度项目"
    ProjectConfigManager.save(pc5, dir_path=tmp_dir3)

    # 加载 → 温度段为 None，应变段恢复
    ctw5b = CalibrationTabWidget()
    tp5b = ctw5b.temp_page
    sp5b = ctw5b.strain_page

    pc5b = ProjectConfigManager.load("空温度项目", dir_path=tmp_dir3)
    total += 1; passed += check("Empty: temperature is None", pc5b.temperature is None)
    total += 1; passed += check("Empty: strain B1 exists", "B1" in pc5b.strain)
    total += 1; passed += check("Empty: strain B1 Ke1", abs(pc5b.strain["B1"].ke_results["Ke1"] - 1.50) < 0.01)

    # restore (temperature=None 不应崩溃)
    ProjectConfigManager.restore(pc5b, tp5b, sp5b)
    total += 1; passed += check("Empty: R sp ke_results", abs(sp5b._ke_results.get("Ke1", 0) - 1.50) < 0.01)

finally:
    shutil.rmtree(tmp_dir3, ignore_errors=True)


# ═══════════════════════════════════════════════════════════════════════
# Test 6: 多传感器应变 save/load
# ═══════════════════════════════════════════════════════════════════════
print("\n═══ TestMultiSensorStrain ═══")

tmp_dir4 = tempfile.mkdtemp(prefix="multi_")
try:
    pc6 = ProjectConfig.create_new("多传感器项目")

    # 添加 3 个传感器
    pc6.strain["A1"] = StrainSubConfig(
        sensor_name="A1", sensor_mode="dual_working",
        gauge_length_mm=80.0, n_cycles=3,
        grating_map={"G1": "A1-W1", "G2": "A1-W2"},
        ke_results={"Ke1": 1.23, "Ke2": 0.98},
    )
    pc6.strain["A2"] = StrainSubConfig(
        sensor_name="A2", sensor_mode="single",
        gauge_length_mm=50.0, n_cycles=1,
        grating_map={"G1": "A2-W1"},
        ke_results={"Ke1": 2.30, "Ke2": 0.0},
    )
    pc6.strain["B1"] = StrainSubConfig(
        sensor_name="B1", sensor_mode="dual_working",
        gauge_length_mm=100.0, n_cycles=2,
        grating_map={"G1": "B1-W1", "G2": "B1-W2"},
        ke_results={"Ke1": 0.85, "Ke2": 0.72},
    )

    ProjectConfigManager.save(pc6, dir_path=tmp_dir4)
    pc6b = ProjectConfigManager.load("多传感器项目", dir_path=tmp_dir4)

    total += 1; passed += check("Multi: 3 sensors", len(pc6b.strain) == 3)
    total += 1; passed += check("Multi: A1 dual_working", pc6b.strain["A1"].sensor_mode == "dual_working")
    total += 1; passed += check("Multi: A2 single", pc6b.strain["A2"].sensor_mode == "single")
    total += 1; passed += check("Multi: B1 Ke2=0.72", abs(pc6b.strain["B1"].ke_results["Ke2"] - 0.72) < 0.001)

    # Restore to empty page: 应变页应该拿到第一个传感器
    ctw6 = CalibrationTabWidget()
    sp6 = ctw6.strain_page
    ProjectConfigManager.restore(pc6b, ctw6.temp_page, sp6)
    # 第一个传感器回填到页面
    total += 1; passed += check("Multi: R sp grating_map", len(sp6._grating_map) > 0)

finally:
    shutil.rmtree(tmp_dir4, ignore_errors=True)


# ═══════════════════════════════════════════════════════════════════════
# Test 7: _get_cal_tab_widget on temperature page
# ═══════════════════════════════════════════════════════════════════════
print("\n═══ TestGetCalTabWidget ═══")

ctw7 = CalibrationTabWidget()
tp7 = ctw7.temp_page
result7 = tp7._get_cal_tab_widget()
total += 1; passed += check("GCW: tp finds ctw", result7 is ctw7)
total += 1; passed += check("GCW: has project_config", hasattr(result7, 'project_config'))


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
