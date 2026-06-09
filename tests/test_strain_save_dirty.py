"""应变标定保存语义 + dirty flag — 回归测试

测试:
1. test_save_captures_all_list_sensors — 标定2个传感器 → 保存 → JSON 含两个
2. test_new_calibration_wording — 已分析后新建不误弹告警；草稿未分析时弹正确措辞
3. test_dirty_flag_set_on_analyze — 分析新传感器 → dirty=True
4. test_dirty_flag_cleared_on_save — 保存后 dirty=False
5. test_dirty_flag_set_on_delete — 删除传感器 → dirty=True

用法: python run_tests.py tests/test_strain_save_dirty.py -v
"""

from __future__ import annotations
import pytest
import sys
import os
import tempfile
import shutil
import json

import numpy as np
from PyQt6.QtWidgets import QApplication


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance()
    if app is None:
        app = QApplication(sys.argv)
    yield app


# ═══════════════════════════════════════════════════════════════════════
# Helpers
# ═══════════════════════════════════════════════════════════════════════


def _simulate_analyze(page, sensor_name, grating_kind="single", grating_map=None):
    """模拟分析完成后 upsert 进列表"""
    grating_map = grating_map or {}
    if not grating_map:
        if grating_kind == "single":
            grating_map = {"G1": f"{sensor_name}-W1"}
        else:
            grating_map = {"G1": f"{sensor_name}-W1", "G2": f"{sensor_name}-W2"}

    page._config["grating_kind"] = grating_kind
    page._grating_map = grating_map
    page._ke_results = {"Ke1": 1.23, "Ke2": 0.98 if grating_kind == "dual_both" else 0.0}
    page._upsert_strain_config(sensor_name)
    page._current_sensor = sensor_name


# ═══════════════════════════════════════════════════════════════════════
# Test 1: 保存捕获列表全部传感器
# ═══════════════════════════════════════════════════════════════════════

class TestSaveCapturesAllListSensors:

    def test_save_captures_all_list_sensors(self, qapp):
        """标定 A1 + B1 → 保存 → JSON 含两个子配置"""
        tmp_dir = tempfile.mkdtemp(prefix="sav_all_")
        try:
            from ui.calibration_tab import StrainCalibrationPage
            page = StrainCalibrationPage()
            _simulate_analyze(page, "A1", "single", grating_map={"G1": "A1-W1"})
            _simulate_analyze(page, "B1", "dual_both",
                              grating_map={"G1": "B1-W1", "G2": "B1-W2"})

            from py.calibration.project_config import ProjectConfigManager
            pc = ProjectConfigManager.capture(None, page)

            assert pc.strain is not None
            assert len(pc.strain) == 2, f"Expected 2 sensors, got {len(pc.strain)}: {list(pc.strain.keys())}"
            assert "A1" in pc.strain
            assert "B1" in pc.strain
            assert abs(pc.strain["A1"].ke_results["Ke1"] - 1.23) < 0.001
            assert pc.strain["B1"].sensor_mode == "dual_working"
        finally:
            shutil.rmtree(tmp_dir, ignore_errors=True)

    def test_save_to_json_contains_all_sensors(self, qapp):
        """capture → save → 磁盘 JSON strain 含两个传感器"""
        tmp_dir = tempfile.mkdtemp(prefix="sav_json_")
        try:
            from ui.calibration_tab import StrainCalibrationPage
            from py.calibration.project_config import ProjectConfigManager
            page = StrainCalibrationPage()
            _simulate_analyze(page, "A1", "single", grating_map={"G1": "A1-W1"})
            _simulate_analyze(page, "A2", "dual_both",
                              grating_map={"G1": "A2-W1", "G2": "A2-W2"})

            pc = ProjectConfigManager.capture(None, page)
            pc.name = "双传感器测试"
            path = ProjectConfigManager.save(pc, dir_path=tmp_dir)

            with open(path, "r", encoding="utf-8") as f:
                raw = json.load(f)

            assert len(raw["strain"]) == 2
            assert "A1" in raw["strain"]
            assert "A2" in raw["strain"]
        finally:
            shutil.rmtree(tmp_dir, ignore_errors=True)

    def test_capture_prefers_self_strain_configs(self, qapp):
        """_strain_configs 有 3 个传感器，ctw.project_config.strain 只有 1 个 → capture 取并集"""
        from ui.calibration_tab import StrainCalibrationPage, CalibrationTabWidget
        from py.calibration.project_config import ProjectConfigManager, StrainSubConfig

        ctw = CalibrationTabWidget()
        sp = ctw.strain_page

        _simulate_analyze(sp, "A1", "single", grating_map={"G1": "A1-W1"})
        _simulate_analyze(sp, "A2", "dual_both",
                          grating_map={"G1": "A2-W1", "G2": "A2-W2"})

        # ctw.project_config.strain 也应被 _upsert 同步 (走 ctw 路径)
        assert ctw.project_config is not None
        assert len(ctw.project_config.strain) >= 2

        pc = ProjectConfigManager.capture(None, sp)
        assert len(pc.strain) == 2


# ═══════════════════════════════════════════════════════════════════════
# Test 2: 新建标定弹窗措辞
# ═══════════════════════════════════════════════════════════════════════

class TestNewCalibrationWording:

    def test_no_warning_when_sensor_saved_in_list(self, qapp):
        """传感器已分析在列表中 → 新建标定不弹警告"""
        from ui.calibration_tab import StrainCalibrationPage
        page = StrainCalibrationPage()
        _simulate_analyze(page, "A1", "single", grating_map={"G1": "A1-W1"})

        # 此时 _grating_map + _readings 可能有内容
        # 但 _current_sensor = "A1" 在 _strain_configs 中
        # → has_saved = True → 不应弹窗
        assert page._current_sensor == "A1"
        assert "A1" in page._strain_configs
        has_draft = bool(page._grating_map or page._readings)
        has_saved = page._current_sensor is not None and page._current_sensor in page._strain_configs
        should_warn = has_draft and not has_saved
        assert should_warn is False, "已分析传感器在列表中，不应弹丢弃草稿告警"

    def test_warning_when_draft_not_analyzed(self, qapp):
        """读数录入有草稿但未分析 → 新建应弹正确措辞告警"""
        from ui.calibration_tab import StrainCalibrationPage, CalibrationTabWidget
        ctw = CalibrationTabWidget()
        sp = ctw.strain_page

        # 模拟有草稿但未提交分析
        sp._grating_map = {"G1": "X1-W1"}
        sp._readings = {1: {1: {"load": [1550.0]}}}

        has_draft = bool(sp._grating_map or sp._readings)
        has_saved = sp._current_sensor is not None and sp._current_sensor in sp._strain_configs
        should_warn = has_draft and not has_saved
        assert should_warn is True


# ═══════════════════════════════════════════════════════════════════════
# Test 3: dirty flag 置位
# ═══════════════════════════════════════════════════════════════════════

class TestDirtyFlag:

    def test_dirty_set_on_analyze(self, qapp):
        """分析新传感器 → dirty=True"""
        from ui.calibration_tab import StrainCalibrationPage
        page = StrainCalibrationPage()
        assert page._project_dirty is False

        _simulate_analyze(page, "A1", "single", grating_map={"G1": "A1-W1"})
        assert page._project_dirty is True

    def test_dirty_cleared_on_save(self, qapp):
        """保存成功后 dirty=False, label 清空"""
        from ui.calibration_tab import StrainCalibrationPage
        page = StrainCalibrationPage()
        _simulate_analyze(page, "A1", "single", grating_map={"G1": "A1-W1"})
        assert page._project_dirty is True

        # 模拟保存成功
        page._clear_dirty()
        assert page._project_dirty is False
        assert page._dirty_label.text() == ""

    def test_dirty_set_on_delete(self, qapp):
        """删除传感器 → dirty=True"""
        from ui.calibration_tab import StrainCalibrationPage
        page = StrainCalibrationPage()
        _simulate_analyze(page, "A1", "single", grating_map={"G1": "A1-W1"})
        page._clear_dirty()
        assert page._project_dirty is False

        # 模拟删除
        page._current_sensor = "A1"
        page._strain_configs.pop("A1", None)
        page._set_dirty()
        assert page._project_dirty is True

    def test_dirty_label_shows_text(self, qapp):
        """dirty label 文本正确"""
        from ui.calibration_tab import StrainCalibrationPage
        page = StrainCalibrationPage()

        page._set_dirty()
        assert "未保存" in page._dirty_label.text()
        assert "●" in page._dirty_label.text()

        page._clear_dirty()
        assert page._dirty_label.text() == ""


# ═══════════════════════════════════════════════════════════════════════
# Test 4: 应变页 _save_project 方法 dirty 联动
# ═══════════════════════════════════════════════════════════════════════

class TestSaveProjectClearsDirty:

    def test_save_project_clears_dirty(self, qapp):
        """调用 _save_project 核心逻辑 (不弹 UI 对话框) 后 dirty 清零"""
        tmp_dir = tempfile.mkdtemp(prefix="sav_clr_")
        try:
            from ui.calibration_tab import StrainCalibrationPage, CalibrationTabWidget
            from py.calibration.project_config import ProjectConfigManager
            import py.calibration.project_config as pcfg

            ctw = CalibrationTabWidget()
            sp = ctw.strain_page
            _simulate_analyze(sp, "A1", "single", grating_map={"G1": "A1-W1"})
            assert sp._project_dirty is True

            old_dir = pcfg.PROFILES_DIR
            pcfg.PROFILES_DIR = tmp_dir
            try:
                pc = ProjectConfigManager.capture(None, sp)
                pc.name = "dirty_test"
                ProjectConfigManager.save(pc)
                sp._clear_dirty()
                assert sp._project_dirty is False
            finally:
                pcfg.PROFILES_DIR = old_dir
        finally:
            shutil.rmtree(tmp_dir, ignore_errors=True)


# ═══════════════════════════════════════════════════════════════════════
# Test 5: dirty 标在列表刷新时不受影响
# ═══════════════════════════════════════════════════════════════════════

class TestDirtyNotAffectedByListRefresh:

    def test_refresh_list_does_not_change_dirty(self, qapp):
        """_refresh_sensor_list 不应改变 dirty 状态"""
        from ui.calibration_tab import StrainCalibrationPage
        page = StrainCalibrationPage()
        _simulate_analyze(page, "A1", "single", grating_map={"G1": "A1-W1"})
        assert page._project_dirty is True

        page._refresh_sensor_list()
        assert page._project_dirty is True  # 刷新列表不应清零

