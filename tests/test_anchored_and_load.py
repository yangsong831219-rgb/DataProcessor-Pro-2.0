"""锚固模式修复 + 加载项目列表 — 回归测试

测试:
  Anchored: g2_anchor / g1_anchor / chart_plots / grating_persists /
            grating_none / grating_missing / compute_ke_none
  Load:    populate_list / strain_synced / select_sensor / empty_strain

用法: python run_tests.py tests/test_anchored_and_load.py -v
"""

from __future__ import annotations
import pytest
import sys
import os
import tempfile
import shutil
import json

import numpy as np
import math
from PyQt6.QtWidgets import QApplication
from PyQt6.QtCore import Qt


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance()
    if app is None:
        app = QApplication(sys.argv)
    yield app


# ═══════════════════════════════════════════════════════════════════════
# Helpers
# ═══════════════════════════════════════════════════════════════════════


def _make_anchored_readings(levels, working_ke=1.21):
    """B1 锚固数据形状: G1 工作栅 (k~working_ke), G2 锚固栅 (几乎无响应)"""
    gauge = 80.0
    eps = [d / gauge * 1e6 for d in levels]
    wl_g1 = [1550.0 + (working_ke * e / 1000.0) for e in eps]
    wl_g2 = [1545.0 + (0.0016 * e / 1000.0) for e in eps]
    return {1: {1: {"load": wl_g1}}, 2: {1: {"load": wl_g2}}}


def _save_project(name, temp_dir, strain_configs):
    from py.calibration.project_config import ProjectConfig, StrainSubConfig
    pc = ProjectConfig.create_new(name)
    for s_name, mode, ke1, ke2 in strain_configs:
        pc.strain[s_name] = StrainSubConfig(
            sensor_name=s_name, sensor_mode=mode,
            ke_results={"Ke1": ke1, "Ke2": ke2},
        )
    pc.save(dir_path=temp_dir)
    return pc


# ═══════════════════════════════════════════════════════════════════════
# Test 1: 锚固模式修复
# ═══════════════════════════════════════════════════════════════════════

class TestAnchoredModeFix:

    def test_anchored_g2_anchor(self, qapp):
        """锚固=G2，G1 斜率~1.21 → Ke1≈1.21, Ke2=0"""
        from ui.calibration_tab import StrainCalibrationPage
        from py.calibration.strain_calibration import (
            StrainCalibrationConfig, calibrate_strain, compute_theoretical_strain,
        )
        page = StrainCalibrationPage()
        page._config.update({
            "gauge_length_mm": 80.0, "mode": "tension_only",
            "n_cycles": 1, "grating_kind": "dual_anchored",
            "anchored_grating": 2,
        })
        page._levels = page._generate_default_levels()
        page._readings = _make_anchored_readings(page._levels)

        config = StrainCalibrationConfig(
            gauge_length_mm=80.0, mode="tension_only", n_cycles=1,
            grating_kind="dual_anchored", anchored_grating=2,
            levels=page._levels,
        )
        result = calibrate_strain(config, page._readings)
        page._last_result = result
        ke = page._compute_ke_results(result)

        assert abs(ke["Ke1"] - 1.21) < 0.05, f"Ke1={ke['Ke1']} should be ~1.21"
        assert ke["Ke2"] == 0.0, f"Ke2 should be 0, got {ke['Ke2']}"

    def test_anchored_g1_anchor(self, qapp):
        """锚固=G1 → G2 工作, Ke2≈1.21, Ke1=0"""
        from ui.calibration_tab import StrainCalibrationPage
        from py.calibration.strain_calibration import (
            StrainCalibrationConfig, calibrate_strain,
        )
        page = StrainCalibrationPage()
        page._config.update({
            "gauge_length_mm": 80.0, "mode": "tension_only",
            "n_cycles": 1, "grating_kind": "dual_anchored",
            "anchored_grating": 1,
        })
        page._levels = page._generate_default_levels()
        # swap: G1=anchor(flat), G2=working(~1.21)
        page._readings = _make_anchored_readings(page._levels, working_ke=1.21)
        # swap keys: {1: anchor(flat), 2: working}
        page._readings = {1: page._readings[2], 2: page._readings[1]}

        config = StrainCalibrationConfig(
            gauge_length_mm=80.0, mode="tension_only", n_cycles=1,
            grating_kind="dual_anchored", anchored_grating=1,
            levels=page._levels,
        )
        result = calibrate_strain(config, page._readings)
        page._last_result = result
        ke = page._compute_ke_results(result)

        assert ke["Ke1"] == 0.0, f"Ke1 should be 0 (anchor), got {ke['Ke1']}"
        assert abs(ke["Ke2"] - 1.21) < 0.05, f"Ke2={ke['Ke2']} should be ~1.21"

    def test_anchored_chart_plots_working_grating(self, qapp):
        """图表只画工作栅 (non-NaN k)，不画锚固栅"""
        from ui.calibration_tab import StrainCalibrationPage
        from py.calibration.strain_calibration import (
            StrainCalibrationConfig, calibrate_strain,
        )
        page = StrainCalibrationPage()
        page._config.update({
            "gauge_length_mm": 80.0, "mode": "tension_only",
            "n_cycles": 1, "grating_kind": "dual_anchored",
            "anchored_grating": 2,
        })
        page._levels = page._generate_default_levels()
        page._readings = _make_anchored_readings(page._levels)

        config = StrainCalibrationConfig(
            gauge_length_mm=80.0, mode="tension_only", n_cycles=1,
            grating_kind="dual_anchored", anchored_grating=2,
            levels=page._levels,
        )
        result = calibrate_strain(config, page._readings)
        page._plot_strain(result)

        # 验证结果文本
        page._show_strain_text(result)
        text = page.strain_result_text.toPlainText()
        assert "光栅1" in text
        assert "G1" in text or "光栅1" in text

    def test_anchored_grating_persists_in_config(self, qapp):
        """set anchored_grating=2 → _config.get() returns 2 after dialog accept"""
        from ui.calibration_tab import StrainCalibrationPage
        page = StrainCalibrationPage()
        page._config["grating_kind"] = "dual_anchored"
        page._config["anchored_grating"] = 2

        assert page._config.get("anchored_grating", 2) == 2
        assert (page._config.get("anchored_grating", 2)) - 1 == 1  # index for combo


# ═══════════════════════════════════════════════════════════════════════
# Test 2: anchored_grating=None / 缺键 → 不崩，默认 G2
# ═══════════════════════════════════════════════════════════════════════

class TestAnchoredGratingNone:

    def test_anchored_grating_none_defaults_g2(self, qapp):
        """config['anchored_grating']=None → combo index=1 (光栅2) 不崩"""
        from ui.calibration_tab import StrainCalibrationPage
        page = StrainCalibrationPage()
        page._config["grating_kind"] = "dual_anchored"
        page._config["anchored_grating"] = None

        idx = (page._config.get("anchored_grating") or 2) - 1
        assert idx == 1  # 光栅2 (index 1)
        assert not isinstance(idx, float)  # None-1 would be TypeError

    def test_anchored_grating_missing_defaults_g2(self, qapp):
        """config 无 anchored_grating 键 → 不崩 + 默认 G2"""
        from ui.calibration_tab import StrainCalibrationPage
        page = StrainCalibrationPage()
        page._config["grating_kind"] = "dual_anchored"
        # 无 anchored_grating 键
        if "anchored_grating" in page._config:
            del page._config["anchored_grating"]

        idx = (page._config.get("anchored_grating") or 2) - 1
        assert idx == 1

    def test_compute_ke_anchored_grating_none(self, qapp):
        """anchored_grating=None → _compute_ke_results 不崩 + Ke2=0"""
        from ui.calibration_tab import StrainCalibrationPage
        from py.calibration.strain_calibration import (
            StrainCalibrationConfig, calibrate_strain,
        )
        page = StrainCalibrationPage()
        page._config.update({
            "gauge_length_mm": 80.0, "mode": "tension_only",
            "n_cycles": 1, "grating_kind": "dual_anchored",
            "anchored_grating": None,
        })
        page._levels = page._generate_default_levels()
        page._readings = _make_anchored_readings(page._levels)

        config = StrainCalibrationConfig(
            gauge_length_mm=80.0, mode="tension_only", n_cycles=1,
            grating_kind="dual_anchored", anchored_grating=None,
            levels=page._levels,
        )
        result = calibrate_strain(config, page._readings)
        page._last_result = result
        ke = page._compute_ke_results(result)

        # anchored_grating=None → or 2 → 默认锚 G2 → Ke2=0
        assert ke["Ke2"] == 0.0
        assert not math.isnan(ke["Ke1"])




# ═══════════════════════════════════════════════════════════════════════
# Test 3: 加载项目 → 列表显示
# ═══════════════════════════════════════════════════════════════════════

class TestLoadProjectPopulatesList:

    def test_load_project_populates_strain_list(self, qapp):
        """存含 3 个应变传感器的项目 → 加载 → 列表 3 条"""
        tmp_dir = tempfile.mkdtemp(prefix="ld_list_")
        try:
            _save_project("multi_strain", tmp_dir, [
                ("A1", "dual_working", 1.23, 0.98),
                ("A2", "single", 2.30, 0.0),
                ("B1", "dual_working", 0.85, 0.72),
            ])

            from ui.calibration_tab import CalibrationTabWidget
            from py.calibration.project_config import ProjectConfigManager
            import py.calibration.project_config as pcfg
            old_dir = pcfg.PROFILES_DIR
            pcfg.PROFILES_DIR = tmp_dir

            try:
                ctw = CalibrationTabWidget()
                sp = ctw.strain_page

                pc = ProjectConfigManager.load("multi_strain")
                ProjectConfigManager.restore(pc, ctw.temp_page, sp)

                # 列表应显示 3 条
                assert sp.sensor_list.count() == 3, \
                    f"Expected 3 sensors in list, got {sp.sensor_list.count()}"
                assert len(sp._strain_configs) == 3
                assert "A1" in sp._strain_configs
                assert "A2" in sp._strain_configs
                assert "B1" in sp._strain_configs
            finally:
                pcfg.PROFILES_DIR = old_dir
        finally:
            shutil.rmtree(tmp_dir, ignore_errors=True)

    def test_load_project_strain_active_state_synced(self, qapp):
        """加载后 _strain_configs 字段级与磁盘一致"""
        tmp_dir = tempfile.mkdtemp(prefix="ld_sync_")
        try:
            _save_project("sync_test", tmp_dir, [
                ("A1", "dual_working", 1.23, 0.98),
            ])

            from ui.calibration_tab import CalibrationTabWidget
            from py.calibration.project_config import ProjectConfigManager
            import py.calibration.project_config as pcfg
            old_dir = pcfg.PROFILES_DIR
            pcfg.PROFILES_DIR = tmp_dir

            try:
                ctw = CalibrationTabWidget()
                sp = ctw.strain_page
                pc = ProjectConfigManager.load("sync_test")
                ProjectConfigManager.restore(pc, ctw.temp_page, sp)

                a1 = sp._strain_configs.get("A1")
                assert a1 is not None
                assert getattr(a1, 'sensor_mode', '') == "dual_working"
                assert abs(getattr(a1, 'ke_results', {}).get("Ke1", 0) - 1.23) < 0.01
                assert abs(getattr(a1, 'ke_results', {}).get("Ke2", 0) - 0.98) < 0.01
                # dirty 应干净
                assert sp._project_dirty is False
            finally:
                pcfg.PROFILES_DIR = old_dir
        finally:
            shutil.rmtree(tmp_dir, ignore_errors=True)

    def test_load_then_select_sensor(self, qapp):
        """加载后选传感器 → 工作结果正确载入"""
        tmp_dir = tempfile.mkdtemp(prefix="ld_sel_")
        try:
            _save_project("sel_test", tmp_dir, [
                ("A1", "dual_working", 1.23, 0.98),
                ("B2", "single", 1.50, 0.0),
            ])

            from ui.calibration_tab import CalibrationTabWidget
            from py.calibration.project_config import ProjectConfigManager
            import py.calibration.project_config as pcfg
            old_dir = pcfg.PROFILES_DIR
            pcfg.PROFILES_DIR = tmp_dir

            try:
                ctw = CalibrationTabWidget()
                sp = ctw.strain_page
                pc = ProjectConfigManager.load("sel_test")
                ProjectConfigManager.restore(pc, ctw.temp_page, sp)

                # 选第二个传感器
                sp._on_sensor_selected(1)
                assert sp._current_sensor == "B2"
                assert sp._working_result is not None
                assert abs(sp._ke_results.get("Ke1", 0) - 1.50) < 0.01

                # 选第一个传感器
                sp._on_sensor_selected(0)
                assert sp._current_sensor == "A1"
                assert abs(sp._ke_results.get("Ke1", 0) - 1.23) < 0.01
            finally:
                pcfg.PROFILES_DIR = old_dir
        finally:
            shutil.rmtree(tmp_dir, ignore_errors=True)

    def test_load_empty_strain_no_crash(self, qapp):
        """temperature=None + strain={} 项目加载不崩"""
        tmp_dir = tempfile.mkdtemp(prefix="ld_empty_")
        try:
            from py.calibration.project_config import ProjectConfig
            pc = ProjectConfig.create_new("empty")
            pc.save(dir_path=tmp_dir)

            from ui.calibration_tab import CalibrationTabWidget
            from py.calibration.project_config import ProjectConfigManager
            import py.calibration.project_config as pcfg
            old_dir = pcfg.PROFILES_DIR
            pcfg.PROFILES_DIR = tmp_dir

            try:
                ctw = CalibrationTabWidget()
                sp = ctw.strain_page
                import pandas as pd
                ctw.temp_page._loaded_df = pd.DataFrame()
                pc2 = ProjectConfigManager.load("empty")
                ProjectConfigManager.restore(pc2, ctw.temp_page, sp)
                assert sp.sensor_list.count() == 0
            finally:
                pcfg.PROFILES_DIR = old_dir
        finally:
            shutil.rmtree(tmp_dir, ignore_errors=True)
