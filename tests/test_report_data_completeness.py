"""检测报告数据完整性检查 — 阶段6 回归测试

确认以下字段在 ProjectConfig roundtrip 后仍完整存在:
- StrainSubConfig.charts_meta (为检测报告预留)
- StrainSubConfig.ke_results (Ke 系数)
- temperature.phase_a S_eff (slope, r2, T_base)
- temperature.ke_table (Phase B Ke)
- temperature.decoupling_results (解耦评级)

用法: python tests/test_report_data_completeness.py
"""

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import tempfile
import shutil
import json
import pytest

from dp_engine.calibration.project_config import (
    ProjectConfig, StrainSubConfig, ProjectConfigManager,
    TEMPERATURE_FIELDS, STRAIN_SUB_CONFIG_FIELDS,
)


def check(label, cond):
    if cond:
        print(f"  PASS: {label}")
        return 1
    else:
        print(f"  FAIL: {label}")
        return 0


# How many fields must survive roundtrip for the detection report
REQUIRED_STRAIN_FIELDS = ["ke_results", "charts_meta", "grating_map",
                           "gauge_length_mm", "sensor_mode", "n_cycles",
                           "readings"]
REQUIRED_TEMP_FIELDS = ["s_eff_results", "ke_table", "decoupling_results",
                         "annotation", "temp_min", "temp_max", "temp_step"]

# Shared temperature fixture used by multiple tests
TEMP_FIXTURE = {
    "file_path": "/data/test.txt",
    "file_format": "hyperion_peaks",
    "annotation": {"c1": "A1-W1", "c2": "A1-W2"},
    "temp_min": 10.0, "temp_max": 70.0, "temp_step": 10.0,
    "detection_params": {"rolling_window": 25},
    "s_eff_results": {
        "A1-W1": {"slope": 27.83, "r2": 0.997, "T_base": 14.11},
        "A1-W2": {"slope": 26.23, "r2": 0.993, "T_base": 11.61},
    },
    "ke_table": {"A1": {"Ke1": 0.7, "Ke2": 1.1}},
    "decoupling_results": {
        "A1": {"e_mean": 3.0, "e_std": 43.5, "e_range": 100.0, "rating": "良"},
    },
    "compensation": {
        "A1": {
            "grade": {"sensor": "A1", "grade": "良", "passed": True,
                      "reasons": ["test"], "is_single_grating": False},
            "metrics": {"sensor": "A1", "residual_sigma": 5.0, "fs": 1000.0,
                        "residual_sigma_pct_fs": 0.5, "low_confidence": False,
                        "repeatability": 2.0, "repeatability_pct_fs": 0.2,
                        "hysteresis_max": 15.0, "hysteresis_max_pct_fs": 1.5,
                        "noise_floor": 1.0, "noise_floor_pct_fs": 0.1,
                        "temp_sensitivity_max": 2.5,
                        "worst_case_single": 7.5, "worst_case_single_pct_fs": 0.75},
            "lut": {"sensor": "A1", "T_base": 25.0,
                    "T_grid": [10.0, 20.0, 30.0], "eps_app": [0.0, 1.0, 2.0],
                    "T_min": 10.0, "T_max": 30.0, "n_cycles": 3, "source": "test"},
        },
    },
}


# ═══════════════════════════════════════════════════════════════════════
# Test 1: StrainSubConfig — charts_meta + ke_results 持久化
# ═══════════════════════════════════════════════════════════════════════

def test_strain_report_fields_roundtrip():
    """StrainSubConfig fields survive to_dict → to_file → load roundtrip."""
    sc = StrainSubConfig(
        sensor_name="A1",
        sensor_mode="dual_working",
        gauge_length_mm=80.0,
        n_cycles=3,
        grating_map={"G1": "A1-W1", "G2": "A1-W2"},
        ke_results={"Ke1": 1.23, "Ke2": 0.98},
        charts_meta={
            "r2_g1": 0.9995, "r2_g2": 0.9993,
            "slope_g1": 1.234, "slope_g2": 0.981,
            "residual_max_g1": 0.5, "residual_max_g2": 0.3,
            "repeatability_pct": 0.15, "hysteresis_pct": 0.08,
            "nonlinearity_pct_g1": 0.4, "nonlinearity_pct_g2": 0.3,
        },
    )

    d = sc.to_dict()
    for f in REQUIRED_STRAIN_FIELDS:
        assert f in d, f"strain field '{f}' missing from to_dict"

    # roundtrip
    tmp_dir = tempfile.mkdtemp(prefix="rdc_")
    try:
        pc = ProjectConfig(
            name="报告数据测试", created_at="2026-01-01", last_modified="2026-01-01",
            temperature=None,
            strain={"A1": sc},
        )
        path = pc.save(dir_path=tmp_dir)

        with open(path, "r", encoding="utf-8") as f:
            raw = json.load(f)

        # 磁盘上 charts_meta 完整
        cm_disk = raw["strain"]["A1"]["charts_meta"]
        for key in ["r2_g1", "r2_g2", "slope_g1", "slope_g2", "residual_max_g1",
                    "residual_max_g2", "repeatability_pct", "hysteresis_pct"]:
            assert key in cm_disk, f"charts_meta.{key} missing on disk"
        assert abs(cm_disk["nonlinearity_pct_g1"] - 0.4) < 0.01

        # 重新加载 → charts_meta 完整
        pc2 = ProjectConfig.load("报告数据测试", dir_path=tmp_dir)
        sc2 = pc2.strain["A1"]
        assert abs(sc2.ke_results["Ke1"] - 1.23) < 0.001
        assert abs(sc2.ke_results["Ke2"] - 0.98) < 0.001
        assert abs(sc2.charts_meta.get("r2_g1", 0) - 0.9995) < 0.001
        assert abs(sc2.charts_meta.get("nonlinearity_pct_g1", 0) - 0.4) < 0.01
        assert sc2.grating_map == {"G1": "A1-W1", "G2": "A1-W2"}
        assert abs(sc2.gauge_length_mm - 80.0) < 0.01
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


# ═══════════════════════════════════════════════════════════════════════
# Test 2: temperature S_eff + ke_table + decoupling 持久化
# ═══════════════════════════════════════════════════════════════════════

def test_temperature_report_fields_roundtrip():
    """Temperature report fields survive roundtrip."""
    pc = ProjectConfig(
        name="温度报告测试", created_at="2026-01-01", last_modified="2026-01-01",
        temperature=dict(TEMP_FIXTURE),
    )

    tmp_dir2 = tempfile.mkdtemp(prefix="rdc2_")
    try:
        path = pc.save(dir_path=tmp_dir2)
        pc2 = ProjectConfig.load("温度报告测试", dir_path=tmp_dir2)
        t2 = pc2.temperature

        for f in REQUIRED_TEMP_FIELDS:
            assert t2 is not None and f in t2, f"temp field '{f}' missing"

        # S_eff detail
        assert abs(t2["s_eff_results"]["A1-W1"]["slope"] - 27.83) < 0.01
        assert abs(t2["s_eff_results"]["A1-W1"]["r2"] - 0.997) < 0.001
        assert abs(t2["s_eff_results"]["A1-W1"]["T_base"] - 14.11) < 0.01

        # ke_table detail
        assert abs(t2["ke_table"]["A1"]["Ke1"] - 0.7) < 0.01
        assert abs(t2["ke_table"]["A1"]["Ke2"] - 1.1) < 0.01

        # decoupling detail
        assert t2["decoupling_results"]["A1"]["rating"] == "良"
        assert abs(t2["decoupling_results"]["A1"]["e_std"] - 43.5) < 0.01
    finally:
        shutil.rmtree(tmp_dir2, ignore_errors=True)


# ═══════════════════════════════════════════════════════════════════════
# Test 3: 完整项目 (温度+应变) validate
# ═══════════════════════════════════════════════════════════════════════

def test_full_project_validate():
    """Full project (temperature + strain) validates cleanly."""
    sc_full = StrainSubConfig(
        sensor_name="A1",
        sensor_mode="dual_working",
        gauge_length_mm=80.0,
        n_cycles=3,
        grating_map={"G1": "A1-W1", "G2": "A1-W2"},
        ke_results={"Ke1": 1.23, "Ke2": 0.98},
        charts_meta={"r2_g1": 0.999, "r2_g2": 0.998},
        readings=[{"disp_mm": 0.008, "eps_theory": 100.0}],
    )

    pc_full = ProjectConfig(
        name="完整项目", created_at="2026-01-01", last_modified="2026-01-01",
        temperature=dict(TEMP_FIXTURE),
        strain={"A1": sc_full},
    )

    issues = pc_full.validate()
    assert issues == [], f"project validate failed: {issues}"

    # to_dict 包含所有顶层字段
    d_full = pc_full.to_dict()
    for f in ["name", "created_at", "last_modified", "temperature", "strain"]:
        assert f in d_full, f"top-level '{f}' missing from to_dict"


# ═══════════════════════════════════════════════════════════════════════
# Standalone entry point
# ═══════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    passed = 0
    total = 0

    print("\n═══ TestStrainReportFields ═══")
    try:
        test_strain_report_fields_roundtrip()
        print("  PASS: strain report fields roundtrip")
        passed += 1
    except AssertionError as e:
        print(f"  FAIL: {e}")
    total += 1

    print("\n═══ TestTemperatureReportFields ═══")
    try:
        test_temperature_report_fields_roundtrip()
        print("  PASS: temperature report fields roundtrip")
        passed += 1
    except AssertionError as e:
        print(f"  FAIL: {e}")
    total += 1

    print("\n═══ TestFullProjectValidate ═══")
    try:
        test_full_project_validate()
        print("  PASS: full project validate")
        passed += 1
    except AssertionError as e:
        print(f"  FAIL: {e}")
    total += 1

    print(f"\n{'='*50}")
    print(f"RESULTS: {passed}/{total} passed")
    if passed == total:
        print("ALL TESTS PASSED")
        print("\n报告所需数据字段确认:")
        print(f"  Strain: {REQUIRED_STRAIN_FIELDS}")
        print(f"  Temperature: {REQUIRED_TEMP_FIELDS}")
        print("  以上字段全部通过 roundtrip 持久化验证。")
        sys.exit(0)
    else:
        print(f"FAILURES: {total - passed}")
        sys.exit(1)
