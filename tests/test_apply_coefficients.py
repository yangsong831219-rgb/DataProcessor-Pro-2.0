"""应用系数到当前传感器 — 阶段3 回归测试

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
import pandas as pd
import numpy as np

from dp_engine.calibration.strain_calibration import (
    StrainCalibrationConfig, StrainCalibrationResult, GratingStrainResult, calibrate_strain,
)
from dp_engine.calibration.project_config import ProjectConfig, StrainSubConfig


def _get_qapp():
    return QApplication.instance() or QApplication(sys.argv)


# ═══════════════════════════════════════════════════════════════════════
# Helpers
# ═══════════════════════════════════════════════════════════════════════

def make_page_with_ke(grating_kind, ke1, ke2=0.0):
    """创建 StrainCalibrationPage 并模拟分析完成后的状态。"""
    _get_qapp()
    from ui.calibration_tab import StrainCalibrationPage
    p = StrainCalibrationPage()
    p._config.update({
        "gauge_length_mm": 80.0, "mode": "tension_only",
        "n_cycles": 1, "grating_kind": grating_kind,
    })
    p._levels = p._generate_default_levels()

    if grating_kind == "single":
        p._grating_map = {"G1": "A1-W1"}
    else:
        p._grating_map = {"G1": "A1-W1", "G2": "A1-W2"}

    p._ke_results = {"Ke1": ke1, "Ke2": ke2}

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

def test_parse_sensor_from_grating_map():
    """_parse_sensor_from_grating_map extracts sensor name from grating map."""
    _get_qapp()
    from ui.calibration_tab import StrainCalibrationPage

    p = StrainCalibrationPage()
    assert p._parse_sensor_from_grating_map() == ""

    p._grating_map = {"G1": "A1-W1"}
    assert p._parse_sensor_from_grating_map() == "A1"

    p._grating_map = {"G1": "A1-W1", "G2": "A1-W2"}
    assert p._parse_sensor_from_grating_map() == "A1"

    p._grating_map = {"G1": "B2-W1"}
    assert p._parse_sensor_from_grating_map() == "B2"


# ═══════════════════════════════════════════════════════════════════════
# Test 2: _build_strain_subconfig
# ═══════════════════════════════════════════════════════════════════════

def test_build_strain_subconfig():
    """_build_strain_subconfig captures correct StrainSubConfig fields."""
    p2 = make_page_with_ke("dual_both", 1.23, 0.98)
    sub = p2._build_strain_subconfig()
    assert sub.sensor_name == "A1"
    assert sub.sensor_mode == "dual_working"
    assert abs(sub.gauge_length_mm - 80.0) < 0.01
    assert abs(sub.ke_results["Ke1"] - 1.23) < 0.001
    assert abs(sub.ke_results["Ke2"] - 0.98) < 0.001
    assert sub.grating_map == {"G1": "A1-W1", "G2": "A1-W2"}

    p2s = make_page_with_ke("single", 2.30, 0.0)
    sub_s = p2s._build_strain_subconfig()
    assert sub_s.sensor_mode == "single"
    assert sub_s.ke_results["Ke2"] == 0.0


# ═══════════════════════════════════════════════════════════════════════
# Test 3: 场景 1 — 双栅 + Phase B 已有 Ke → 覆盖
# ═══════════════════════════════════════════════════════════════════════

def test_scenario1_dual_overwrite():
    """Dual grating + Phase B done → ke_table overwritten."""
    _get_qapp()
    from ui.calibration_tab import CalibrationTabWidget

    ctw = CalibrationTabWidget()

    tp = ctw.temp_page
    tp._loaded_df = pd.DataFrame({"c1": [1550.0], "c2": [1545.0]})
    tp._phase_a_done = True
    tp._annotation_groups = {
        "A1": [{"name": "A1-W1", "col_name": "c1"}, {"name": "A1-W2", "col_name": "c2"}],
    }
    tp._phase_b_state = {
        "ke_table": {"A1": {"Ke1": 0.5, "Ke2": 0.8}},
        "decoupling_results": {},
    }

    sp = ctw.strain_page
    sp._config.update({"gauge_length_mm": 80.0, "grating_kind": "dual_both", "n_cycles": 1})
    sp._levels = sp._generate_default_levels()
    sp._grating_map = {"G1": "A1-W1", "G2": "A1-W2"}
    sp._ke_results = {"Ke1": 1.23, "Ke2": 0.98}
    sp._last_result = StrainCalibrationResult(
        gauge_length_mm=80.0, mode="tension_only", n_cycles=1,
        grating_kind="dual_both", levels=sp._levels,
        eps_theory=[d / 80.0 * 1e6 for d in sp._levels],
        gratings=[
            GratingStrainResult(grating_index=1, k_pm_per_ue=1.23, R2=0.999),
            GratingStrainResult(grating_index=2, k_pm_per_ue=0.98, R2=0.998),
        ],
    )

    is_dual, sname = sp._is_dual_in_temperature()
    assert is_dual is True
    assert sname == "A1"
    assert sp._has_temperature_data() is True
    assert abs(tp._phase_b_state["ke_table"]["A1"]["Ke1"] - 0.5) < 0.01

    pc = sp._get_or_create_project_config()
    sub = sp._build_strain_subconfig()
    pc.strain[sname] = sub

    ke_dict = {"Ke1": 1.23, "Ke2": 0.98}
    if pc.temperature is None:
        pc.temperature = {}
    pc.temperature["ke_table"] = pc.temperature.get("ke_table", {})
    pc.temperature["ke_table"][sname] = ke_dict

    pb_state = tp._phase_b_state or {}
    pb_state["ke_table"] = pb_state.get("ke_table", {})
    pb_state["ke_table"][sname] = ke_dict
    tp._phase_b_state = pb_state

    assert abs(pc.temperature["ke_table"]["A1"]["Ke1"] - 1.23) < 0.001
    assert abs(pc.temperature["ke_table"]["A1"]["Ke2"] - 0.98) < 0.001
    assert abs(tp._phase_b_state["ke_table"]["A1"]["Ke1"] - 1.23) < 0.001
    assert "A1" in pc.strain
    assert abs(pc.strain["A1"].ke_results["Ke1"] - 1.23) < 0.001


# ═══════════════════════════════════════════════════════════════════════
# Test 4: 场景 2 — Phase A 完成, Phase B 未填 → ke_table 写入
# ═══════════════════════════════════════════════════════════════════════

def test_scenario2_phase_b_empty():
    """Phase A done but Phase B empty → ke_table written for future use."""
    _get_qapp()
    from ui.calibration_tab import CalibrationTabWidget

    ctw2 = CalibrationTabWidget()
    tp2 = ctw2.temp_page
    tp2._loaded_df = pd.DataFrame({"c1": [1550.0], "c2": [1545.0]})
    tp2._phase_a_done = True
    tp2._annotation_groups = {
        "A2": [{"name": "A2-W1", "col_name": "c1"}, {"name": "A2-W2", "col_name": "c2"}],
    }
    tp2._phase_b_state = {}

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

    is_dual2, sname2 = sp2._is_dual_in_temperature()
    assert is_dual2 is True
    assert sname2 == "A2"
    assert tp2._phase_b_state.get("ke_table", {}) == {}

    pc2 = sp2._get_or_create_project_config()
    sub2 = sp2._build_strain_subconfig()
    pc2.strain[sname2] = sub2

    ke_dict2 = {"Ke1": 0.85, "Ke2": 0.72}
    if pc2.temperature is None:
        pc2.temperature = {}
    pc2.temperature["ke_table"] = pc2.temperature.get("ke_table", {})
    pc2.temperature["ke_table"][sname2] = ke_dict2

    tp2._phase_b_state["ke_table"] = {sname2: ke_dict2}

    assert abs(tp2._phase_b_state["ke_table"]["A2"]["Ke1"] - 0.85) < 0.001
    assert abs(pc2.temperature["ke_table"]["A2"]["Ke2"] - 0.72) < 0.001
    assert "A2" in pc2.strain

    pb_state2 = tp2._phase_b_state
    assert abs(pb_state2["ke_table"]["A2"]["Ke1"] - 0.85) < 0.001


# ═══════════════════════════════════════════════════════════════════════
# Test 5: 场景 3 — 温度未做 → 仅 strain
# ═══════════════════════════════════════════════════════════════════════

def test_scenario3_no_temperature():
    """No temperature data → only strain saved, no ke_table."""
    _get_qapp()
    from ui.calibration_tab import CalibrationTabWidget

    ctw3 = CalibrationTabWidget()
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

    assert sp3._has_temperature_data() is False
    is_dual3, sname3 = sp3._is_dual_in_temperature()
    assert is_dual3 is False
    assert sname3 == "B1"

    pc3 = sp3._get_or_create_project_config()
    sub3 = sp3._build_strain_subconfig()
    pc3.strain[sname3] = sub3

    assert "B1" in pc3.strain
    assert abs(pc3.strain["B1"].ke_results["Ke1"] - 1.50) < 0.001
    assert pc3.temperature is None or pc3.temperature.get("ke_table", {}) == {}


# ═══════════════════════════════════════════════════════════════════════
# Test 6: 场景 4 — 单栅 + 温度完成 → strain only (不碰 ke_table)
# ═══════════════════════════════════════════════════════════════════════

def test_scenario4_single_grating():
    """Single grating with temp → strain only, no ke_table write."""
    _get_qapp()
    from ui.calibration_tab import CalibrationTabWidget

    ctw4 = CalibrationTabWidget()
    tp4 = ctw4.temp_page
    tp4._loaded_df = pd.DataFrame({"c1": [1550.0]})
    tp4._phase_a_done = True
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
    assert is_dual4 is False
    assert sname4 == "A2"
    assert sp4._has_temperature_data() is True

    pc4 = sp4._get_or_create_project_config()
    sub4 = sp4._build_strain_subconfig()
    pc4.strain[sname4] = sub4

    assert "A2" in pc4.strain
    assert abs(pc4.strain["A2"].ke_results["Ke1"] - 2.30) < 0.001
    assert abs(pc4.strain["A2"].ke_results["Ke2"] - 0.0) < 0.001
    assert pc4.strain["A2"].sensor_mode == "single"
    assert pc4.temperature is None or pc4.temperature.get("ke_table", {}) == {}


# ═══════════════════════════════════════════════════════════════════════
# Test 7: _get_or_create_project_config
# ═══════════════════════════════════════════════════════════════════════

def test_get_or_create_project_config():
    """_get_or_create_project_config creates on first call, reuses on subsequent."""
    _get_qapp()
    from ui.calibration_tab import CalibrationTabWidget

    ctw5 = CalibrationTabWidget()
    sp5 = ctw5.strain_page

    assert ctw5.project_config is None

    pc5a = sp5._get_or_create_project_config()
    assert pc5a is not None
    assert ctw5.project_config is pc5a

    pc5b = sp5._get_or_create_project_config()
    assert pc5b is pc5a


# ═══════════════════════════════════════════════════════════════════════
# Test 8: 同传感器重标定覆盖 (ke_table + strain)
# ═══════════════════════════════════════════════════════════════════════

def test_overwrite_roundtrip():
    """Recalibration of same sensor overwrites both ke_table and strain."""
    _get_qapp()
    from ui.calibration_tab import CalibrationTabWidget

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

    sp6._ke_results = {"Ke1": 1.0, "Ke2": 0.8}
    pc6 = sp6._get_or_create_project_config()
    pc6.strain["A1"] = sp6._build_strain_subconfig()
    pc6.temperature = {"ke_table": {"A1": {"Ke1": 1.0, "Ke2": 0.8}}}

    assert abs(pc6.temperature["ke_table"]["A1"]["Ke1"] - 1.0) < 0.001

    sp6._ke_results = {"Ke1": 1.5, "Ke2": 1.2}
    sname6 = sp6._parse_sensor_from_grating_map()
    pc6.strain[sname6] = sp6._build_strain_subconfig()
    pc6.temperature["ke_table"][sname6] = {"Ke1": 1.5, "Ke2": 1.2}
    tp6._phase_b_state["ke_table"] = {"A1": {"Ke1": 1.5, "Ke2": 1.2}}

    assert abs(pc6.temperature["ke_table"]["A1"]["Ke1"] - 1.5) < 0.001
    assert abs(pc6.temperature["ke_table"]["A1"]["Ke2"] - 1.2) < 0.001
    assert abs(pc6.strain["A1"].ke_results["Ke1"] - 1.5) < 0.001
    assert abs(tp6._phase_b_state["ke_table"]["A1"]["Ke2"] - 1.2) < 0.001


# ═══════════════════════════════════════════════════════════════════════
# Test 9: 边界 — grating_map 为空时
# ═══════════════════════════════════════════════════════════════════════

def test_edge_cases():
    """Edge cases: empty grating map, bad format."""
    p9 = make_page_with_ke("dual_working", 1.0, 0.8)
    p9._grating_map = {}
    assert p9._parse_sensor_from_grating_map() == ""
    assert p9._is_dual_in_temperature() == (False, "")

    p9._grating_map = {"G1": "bad-format"}
    sensor_name_bad = p9._parse_sensor_from_grating_map()
    assert sensor_name_bad == "bad"


# ═══════════════════════════════════════════════════════════════════════
# Standalone entry point
# ═══════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    tests = [
        ("TestParseSensor", test_parse_sensor_from_grating_map),
        ("TestBuildStrainSubConfig", test_build_strain_subconfig),
        ("TestScenario1_DualOverwrite", test_scenario1_dual_overwrite),
        ("TestScenario2_PhaseBEmpty", test_scenario2_phase_b_empty),
        ("TestScenario3_NoTemperature", test_scenario3_no_temperature),
        ("TestScenario4_SingleGrating", test_scenario4_single_grating),
        ("TestGetOrCreateProjectConfig", test_get_or_create_project_config),
        ("TestOverwriteRoundtrip", test_overwrite_roundtrip),
        ("TestEdgeCases", test_edge_cases),
    ]

    passed = 0
    total = len(tests)
    for name, fn in tests:
        print(f"\n═══ {name} ═══")
        try:
            fn()
            print(f"  ALL PASSED: {name}")
            passed += 1
        except AssertionError as e:
            print(f"  FAIL: {e}")
        except Exception as e:
            print(f"  ERROR: {type(e).__name__}: {e}")

    print(f"\n{'='*50}")
    print(f"RESULTS: {passed}/{total} passed")
    if passed == total:
        print("ALL TESTS PASSED")
        sys.exit(0)
    else:
        print(f"FAILURES: {total - passed}")
        sys.exit(1)
