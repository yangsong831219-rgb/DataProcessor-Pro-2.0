"""项目配置共享保存/加载 — 阶段5 回归测试

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
from pathlib import Path

import pytest

from PyQt6.QtWidgets import QApplication, QTableWidget
import pandas as pd
import numpy as np
from typing import Protocol, cast

from dp_engine.calibration.project_config import (
    ProjectConfig, StrainSubConfig, ProjectConfigManager, PROFILES_DIR,
)
from ui.calibration_tab import CalibrationTabWidget, StrainCalibrationPage
from dp_engine.calibration.strain_calibration import (
    StrainCalibrationResult, GratingStrainResult,
)


# ═══════════════════════════════════════════════════════════════════════
# Helpers
# ═══════════════════════════════════════════════════════════════════════

def _get_qapp():
    return QApplication.instance() or QApplication(sys.argv)


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
# Protocol: 描述 CalibrationTabWidget 真实存在的 project_config 动态接口
# CalibrationTabWidget.__init__ 设置 self.project_config = None，
# 但 Pyright 将其窄化为 None 类型，无法直接赋值为 ProjectConfig。
# 本 Protocol 准确描述该属性真实运行时类型，不做不存在的接口扩展。
# ═══════════════════════════════════════════════════════════════════════

class _HasProjectConfig(Protocol):
    project_config: ProjectConfig | None


# ═══════════════════════════════════════════════════════════════════════
# Test 1: capture — 从双模块构造 ProjectConfig
# ═══════════════════════════════════════════════════════════════════════

def test_capture_from_dual_modules():
    """capture constructs ProjectConfig from temp + strain pages."""
    _get_qapp()
    ctw = CalibrationTabWidget()
    tp = ctw.temp_page
    sp = ctw.strain_page
    setup_temp_page(tp, make_temp_df())
    setup_strain_page(sp)

    cfg = ProjectConfig.create_new("测试项目")
    cast(_HasProjectConfig, ctw).project_config = cfg
    assert hasattr(ctw, "project_config")
    assert ctw.project_config is cfg
    cfg.strain["A1"] = sp._build_strain_subconfig()

    pc = ProjectConfigManager.capture(tp, sp)

    assert pc.name != ""
    assert pc.temperature is not None
    assert pc.temperature["annotation"].get("Unnamed: 17") == "A1-W1"
    assert len(pc.temperature["s_eff_results"]) == 2
    assert "A1" in pc.temperature["ke_table"]
    assert "A1" in pc.temperature["decoupling_results"]
    assert "A1" in pc.strain
    assert abs(pc.strain["A1"].ke_results["Ke1"] - 1.23) < 0.01
    assert pc.validate() == []


# ═══════════════════════════════════════════════════════════════════════
# Test 2: restore — 回填双模块活状态
# ═══════════════════════════════════════════════════════════════════════

def test_restore_to_dual_modules():
    """restore fills temp + strain pages from ProjectConfig."""
    _get_qapp()
    ctw = CalibrationTabWidget()
    tp = ctw.temp_page
    sp = ctw.strain_page
    setup_temp_page(tp, make_temp_df())
    setup_strain_page(sp)
    cfg = ProjectConfig.create_new("测试项目")
    cast(_HasProjectConfig, ctw).project_config = cfg
    assert hasattr(ctw, "project_config")
    assert ctw.project_config is cfg
    cfg.strain["A1"] = sp._build_strain_subconfig()
    pc = ProjectConfigManager.capture(tp, sp)

    ctw2 = CalibrationTabWidget()
    tp2 = ctw2.temp_page
    sp2 = ctw2.strain_page

    ProjectConfigManager.restore(pc, tp2, sp2)

    assert tp2._annotation_dict.get("Unnamed: 17") == "A1-W1"
    assert tp2._phase_a_done is True
    assert tp2._last_result is not None
    assert tp2._phase_a_state["annotation"].get("Unnamed: 17") == "A1-W1"
    assert "A1" in tp2._phase_b_state["ke_table"]
    assert abs(tp2._phase_b_state["ke_table"]["A1"]["Ke1"] - 1.2) < 0.01
    assert sp2._grating_map == {"G1": "A1-W1", "G2": "A1-W2"}
    assert abs(sp2._ke_results["Ke1"] - 1.23) < 0.01
    assert sp2._current_sensor == "A1"
    assert sp2.curve_panel.get_figure().axes


def test_restore_keeps_temperature_data_when_acquisition_timestamp_restarts(qapp, tmp_path):
    """Project restore must use the calibration import policy for repeated timestamps."""
    _get_qapp()
    fixture = Path(__file__).parent / "golden" / "Peaks_20260512144535_sampled_10pct.txt"
    lines = fixture.read_text(encoding="utf-8").splitlines()
    header_index = next(i for i, line in enumerate(lines) if line.startswith("Timestamp\t"))
    repeated_row = lines[header_index + 1].split("\t")
    repeated_row[-1] = "1547.9999"
    restarted = tmp_path / "restarted_hyperion_peaks.txt"
    restarted.write_text("\n".join(lines + ["\t".join(repeated_row)]), encoding="utf-8")

    project = ProjectConfig.create_new("timestamp restart restore")
    project.temperature = {
        "file_path": str(restarted),
        "file_format": "hyperion_peaks",
        "annotation": {"w1": "A1-1", "w2": "A1-2"},
        "temp_min": 0.0,
        "temp_max": 70.0,
        "temp_step": 10.0,
        "detection_params": {},
        "s_eff_results": {"w1": {"slope": 25.0, "r2": 0.99, "T_base": 0.0}},
        "ke_table": {},
        "decoupling_results": {},
        "compensation": {},
    }
    project.strain["A1"] = StrainSubConfig(
        sensor_name="A1",
        sensor_mode="single",
        gauge_length_mm=80.0,
        grating_map={"G1": "A1-1"},
        ke_results={"Ke1": 1.2, "Ke2": 0.0},
        readings=[
            {"eps_theory": 0.0, "G1_C1_load": 1540.000},
            {"eps_theory": 100.0, "G1_C1_load": 1540.120},
            {"eps_theory": 200.0, "G1_C1_load": 1540.240},
        ],
    )

    restored = CalibrationTabWidget()
    ProjectConfigManager.restore(project, restored.temp_page, restored.strain_page)

    assert restored.temp_page._loaded_df is not None
    assert len(restored.temp_page._loaded_df) == 121
    assert restored.temp_page.btn_phase_a.isEnabled()
    assert restored.temp_page.btn_phase_b.isEnabled()
    assert restored.strain_page._current_sensor == "A1"
    assert restored.strain_page._grating_map == {"G1": "A1-1"}
    assert len(restored.strain_page.curve_panel.get_figure().axes[0].lines) == 1


# ═══════════════════════════════════════════════════════════════════════
# Test 3: save → load → restore 完整链路
# ═══════════════════════════════════════════════════════════════════════

def test_save_load_roundtrip():
    """Full save → load → restore cycle."""
    _get_qapp()
    ctw = CalibrationTabWidget()
    tp = ctw.temp_page
    sp = ctw.strain_page
    setup_temp_page(tp, make_temp_df())
    setup_strain_page(sp)
    cfg = ProjectConfig.create_new("完整链路测试")
    cast(_HasProjectConfig, ctw).project_config = cfg
    assert hasattr(ctw, "project_config")
    assert ctw.project_config is cfg
    cfg.strain["A1"] = sp._build_strain_subconfig()
    pc = ProjectConfigManager.capture(tp, sp)
    pc.name = "完整链路测试"

    tmp_dir = tempfile.mkdtemp(prefix="projsave_")
    try:
        path = ProjectConfigManager.save(pc, dir_path=tmp_dir)
        assert os.path.isfile(path)

        projects = ProjectConfigManager.list_all(dir_path=tmp_dir)
        assert len(projects) == 1
        assert projects[0]["_key"] == "完整链路测试"

        pc2 = ProjectConfigManager.load("完整链路测试", dir_path=tmp_dir)
        assert pc2.name == "完整链路测试"
        assert pc2.temperature is not None
        assert "A1" in pc2.strain
        assert abs(pc2.strain["A1"].ke_results["Ke1"] - 1.23) < 0.01
        assert "A1" in pc2.temperature["ke_table"]

        ctw3 = CalibrationTabWidget()
        tp3 = ctw3.temp_page
        sp3 = ctw3.strain_page
        ProjectConfigManager.restore(pc2, tp3, sp3)

        assert tp3._annotation_dict.get("Unnamed: 17") == "A1-W1"
        assert abs(sp3._ke_results.get("Ke1", 0) - 1.23) < 0.01
        assert sp3._grating_map.get("G1") == "A1-W1"

        ProjectConfigManager.delete("完整链路测试", dir_path=tmp_dir)
        assert ProjectConfigManager.list_all(dir_path=tmp_dir) == []
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


# ═══════════════════════════════════════════════════════════════════════
# Test 4: 应变页 save → 温度页 load
# ═══════════════════════════════════════════════════════════════════════

def test_strain_page_save_temp_page_load():
    """Save from strain tab perspective, load from temp tab perspective."""
    _get_qapp()
    tmp_dir2 = tempfile.mkdtemp(prefix="s2t_")
    try:
        ctw4 = CalibrationTabWidget()
        sp4 = ctw4.strain_page
        tp4 = ctw4.temp_page
        setup_temp_page(tp4, make_temp_df())
        setup_strain_page(sp4)
        cfg4 = ProjectConfig.create_new("应变存温度取")
        cast(_HasProjectConfig, ctw4).project_config = cfg4
        assert hasattr(ctw4, "project_config")
        assert ctw4.project_config is cfg4
        cfg4.strain["A1"] = sp4._build_strain_subconfig()

        pc4 = ProjectConfigManager.capture(tp4, sp4)
        pc4.name = "应变存温度取"
        ProjectConfigManager.save(pc4, dir_path=tmp_dir2)

        ctw4b = CalibrationTabWidget()
        tp4b = ctw4b.temp_page
        sp4b = ctw4b.strain_page
        pc4b = ProjectConfigManager.load("应变存温度取", dir_path=tmp_dir2)
        ProjectConfigManager.restore(pc4b, tp4b, sp4b)

        assert tp4b._annotation_dict.get("Unnamed: 17") == "A1-W1"
        assert abs(sp4b._ke_results.get("Ke1", 0) - 1.23) < 0.01
        assert sp4b._grating_map.get("G1") == "A1-W1"
        assert "A1" in tp4b._phase_b_state["ke_table"]
        assert tp4b._phase_a_done is True
    finally:
        shutil.rmtree(tmp_dir2, ignore_errors=True)


# ═══════════════════════════════════════════════════════════════════════
# Test 5: 空温度项目 save/load
# ═══════════════════════════════════════════════════════════════════════

def test_empty_temperature_project():
    """Project with only strain data (temperature=None) saves and loads correctly."""
    _get_qapp()
    tmp_dir3 = tempfile.mkdtemp(prefix="empty_")
    try:
        ctw5 = CalibrationTabWidget()
        sp5 = ctw5.strain_page
        setup_strain_page(sp5)
        sp5._grating_map = {"G1": "B1-W1"}
        sp5._ke_results = {"Ke1": 1.50, "Ke2": 0.0}

        cfg5 = ProjectConfig.create_new("空温度项目")
        cast(_HasProjectConfig, ctw5).project_config = cfg5
        assert hasattr(ctw5, "project_config")
        assert ctw5.project_config is cfg5
        cfg5.strain["B1"] = sp5._build_strain_subconfig()

        pc5 = ProjectConfigManager.capture(ctw5.temp_page, sp5)
        pc5.name = "空温度项目"
        ProjectConfigManager.save(pc5, dir_path=tmp_dir3)

        ctw5b = CalibrationTabWidget()
        tp5b = ctw5b.temp_page
        sp5b = ctw5b.strain_page

        pc5b = ProjectConfigManager.load("空温度项目", dir_path=tmp_dir3)
        assert pc5b.temperature is None
        assert "B1" in pc5b.strain
        assert abs(pc5b.strain["B1"].ke_results["Ke1"] - 1.50) < 0.01

        ProjectConfigManager.restore(pc5b, tp5b, sp5b)
        assert abs(sp5b._ke_results.get("Ke1", 0) - 1.50) < 0.01
    finally:
        shutil.rmtree(tmp_dir3, ignore_errors=True)


# ═══════════════════════════════════════════════════════════════════════
# Test 6: 多传感器应变 save/load
# ═══════════════════════════════════════════════════════════════════════

def test_multi_sensor_strain():
    """Multiple strain sensors survive roundtrip."""
    _get_qapp()
    tmp_dir4 = tempfile.mkdtemp(prefix="multi_")
    try:
        pc6 = ProjectConfig.create_new("多传感器项目")

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

        assert len(pc6b.strain) == 3
        assert pc6b.strain["A1"].sensor_mode == "dual_working"
        assert pc6b.strain["A2"].sensor_mode == "single"
        assert abs(pc6b.strain["B1"].ke_results["Ke2"] - 0.72) < 0.001

        ctw6 = CalibrationTabWidget()
        sp6 = ctw6.strain_page
        ProjectConfigManager.restore(pc6b, ctw6.temp_page, sp6)
        assert len(sp6._grating_map) > 0
    finally:
        shutil.rmtree(tmp_dir4, ignore_errors=True)


# ═══════════════════════════════════════════════════════════════════════
# Test 7: _get_cal_tab_widget on temperature page
# ═══════════════════════════════════════════════════════════════════════

def test_get_cal_tab_widget():
    """_get_cal_tab_widget returns CalibrationTabWidget owner."""
    _get_qapp()
    ctw7 = CalibrationTabWidget()
    tp7 = ctw7.temp_page
    result7 = tp7._get_cal_tab_widget()
    assert result7 is ctw7
    assert hasattr(result7, 'project_config')


# ═══════════════════════════════════════════════════════════════════════
# Standalone entry point
# ═══════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    tests = [
        ("TestCapture", test_capture_from_dual_modules),
        ("TestRestore", test_restore_to_dual_modules),
        ("TestSaveLoadRoundtrip", test_save_load_roundtrip),
        ("TestStrainPageSaveTempPageLoad", test_strain_page_save_temp_page_load),
        ("TestEmptyTemperature", test_empty_temperature_project),
        ("TestMultiSensorStrain", test_multi_sensor_strain),
        ("TestGetCalTabWidget", test_get_cal_tab_widget),
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
            print(f"  ERROR: {e}")

    print(f"\n{'='*50}")
    print(f"RESULTS: {passed}/{total} passed")
    if passed == total:
        print("ALL TESTS PASSED")
        sys.exit(0)
    else:
        print(f"FAILURES: {total - passed}")
        sys.exit(1)
