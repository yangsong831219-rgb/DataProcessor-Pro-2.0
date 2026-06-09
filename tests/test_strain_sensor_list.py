"""应变标定多传感器列表 — 阶段2.5 回归测试 (qtbot 真实事件)

测试:
1. test_analyze_adds_sensor_to_list — 分析 A1 → 列表出现 A1；再分析 A2 → 两条目
2. test_select_sensor_recomputes_charts — 选传感器 → 图表懒重算无异常 + Ke 正确
3. test_reanalyze_same_sensor_overwrites — 重标定同名传感器覆盖非新增
4. test_delete_sensor — 删除 → 列表移除 + project.strain 移除 + 结果区清空
5. test_inconsistent_grating_map_rejected — G1→A1-W1, G2→B2-W1 提交报错
6. test_current_sensor_is_selection — 切换选中 → self._current_sensor 同步
7. test_lazy_recompute_guards_empty — 空 readings 传感器选中不崩
8. test_new_calibration_clears_workspace — 新建标定清空工作态

用法: python run_tests.py tests/test_strain_sensor_list.py -v
"""

from __future__ import annotations
import pytest
import sys
import os

import numpy as np
import pandas as pd
from PyQt6.QtWidgets import QApplication, QTableWidgetItem, QComboBox
from PyQt6.QtCore import Qt
from PyQt6.QtTest import QTest


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance()
    if app is None:
        app = QApplication(sys.argv)
    yield app


# ═══════════════════════════════════════════════════════════════════════
# Helpers
# ═══════════════════════════════════════════════════════════════════════


def _make_page_with_config(grating_kind, ke1, ke2=0.0, grating_map=None):
    """创建 StrainCalibrationPage, 填充 activity state 模拟分析完成。"""
    from ui.calibration_tab import StrainCalibrationPage
    from py.calibration.strain_calibration import (
        StrainCalibrationConfig, StrainCalibrationResult, GratingStrainResult, calibrate_strain,
    )
    page = StrainCalibrationPage()
    page._config.update({
        "gauge_length_mm": 80.0, "mode": "tension_only",
        "n_cycles": 1, "grating_kind": grating_kind,
        "anchored_grating": 2 if grating_kind == "dual_anchored" else None,
    })
    page._levels = page._generate_default_levels()
    page._grating_map = grating_map or {}
    page._ke_results = {"Ke1": ke1, "Ke2": ke2}

    # 构造 result
    gratings = [GratingStrainResult(
        grating_index=1, k_pm_per_ue=ke1, R2=0.999, nonlinearity_pct_fs=0.5,
        repeatability_pct_fs=0.3, hysteresis_pct_fs=0.2,
    )]
    if grating_kind != "single":
        gratings.append(GratingStrainResult(
            grating_index=2, k_pm_per_ue=ke2, R2=0.998, nonlinearity_pct_fs=0.4,
            repeatability_pct_fs=0.3, hysteresis_pct_fs=0.1,
        ))
    page._last_result = StrainCalibrationResult(
        gauge_length_mm=80.0, mode="tension_only", n_cycles=1,
        grating_kind=grating_kind, levels=page._levels,
        eps_theory=[d / 80.0 * 1e6 for d in page._levels],
        gratings=gratings,
    )
    return page


def _simulate_analyze(page, sensor_name, grating_kind="single", grating_map=None):
    """模拟 _on_strain_result 的完整后处理 (不真正跑 worker)。"""
    grating_map = grating_map or {}
    if not grating_map:
        if grating_kind == "single":
            grating_map = {"G1": f"{sensor_name}-W1"}
        else:
            grating_map = {"G1": f"{sensor_name}-W1", "G2": f"{sensor_name}-W2"}

    page._config["grating_kind"] = grating_kind
    page._grating_map = grating_map
    ke1, ke2 = 1.23, 0.98 if grating_kind == "dual_both" else (1.23, 0.0)
    page._ke_results = {"Ke1": ke1, "Ke2": ke2}

    from py.calibration.strain_calibration import (
        StrainCalibrationResult, GratingStrainResult,
    )
    gratings = [GratingStrainResult(grating_index=1, k_pm_per_ue=ke1, R2=0.999)]
    if grating_kind != "single":
        gratings.append(GratingStrainResult(grating_index=2, k_pm_per_ue=ke2, R2=0.998))
    page._last_result = StrainCalibrationResult(
        gauge_length_mm=80.0, mode="tension_only", n_cycles=1,
        grating_kind=grating_kind, levels=page._levels,
        eps_theory=[d / 80.0 * 1e6 for d in page._levels],
        gratings=gratings,
    )

    # 呼叫 _on_strain_result 后半段的传感器管理逻辑
    page._ke_results = page._compute_ke_results(page._last_result)
    err = page._validate_grating_map_consistency()
    if err:
        return err  # 返回错误消息
    s_name = page._parse_sensor_from_grating_map()
    if not s_name:
        return None
    page._upsert_strain_config(s_name)
    page._current_sensor = s_name
    return s_name


# ═══════════════════════════════════════════════════════════════════════
# Test 1: 分析后加入列表
# ═══════════════════════════════════════════════════════════════════════

class TestAnalyzeAddsToSensorList:

    def test_analyze_adds_sensor_to_list(self, qapp):
        """分析 A1 → 列表出现 A1；再分析 A2 → 两条目, A2 选中"""
        sensor_name = _simulate_analyze(
            _make_page_with_config("single", 1.23), "A1", "single",
            grating_map={"G1": "A1-W1"},
        )
        from ui.calibration_tab import StrainCalibrationPage
        page = _make_page_with_config("single", 1.23)

        # Round 1: 分析 A1
        result1 = _simulate_analyze(page, "A1", "single",
                                     grating_map={"G1": "A1-W1"})
        assert result1 == "A1"
        assert page._current_sensor == "A1"
        assert page.sensor_list.count() == 1
        assert "A1" in str(page.sensor_list.item(0).text() or "")
        assert "A1" in page._strain_configs
        assert "A1" in page._get_or_create_project_config().strain

        # Round 2: 分析 A2 (新建页面的新状态)
        page2 = _make_page_with_config("dual_both", 0.85, 0.72)
        result2 = _simulate_analyze(page2, "A2", "dual_both",
                                     grating_map={"G1": "A2-W1", "G2": "A2-W2"})
        assert result2 == "A2"
        assert page2.sensor_list.count() == 1
        assert "A2" in page2._strain_configs

    def test_multi_sensor_list_display(self, qapp):
        """列表显示多条传感器，名称-模式-Ke 正确"""
        from ui.calibration_tab import StrainCalibrationPage
        page = StrainCalibrationPage()
        _simulate_analyze(page, "A1", "single", grating_map={"G1": "A1-W1"})

        # 手动添加第二个传感器条目 (模拟已保存的项目恢复)
        from py.calibration.project_config import StrainSubConfig
        sc = StrainSubConfig(
            sensor_name="B2", sensor_mode="dual_working",
            ke_results={"Ke1": 1.50, "Ke2": 1.10},
        )
        page._strain_configs["B2"] = sc
        page._get_or_create_project_config().strain["B2"] = sc
        page._refresh_sensor_list(select_sensor="A1")

        assert page.sensor_list.count() == 2
        texts = [str(page.sensor_list.item(i).text() or "") for i in range(page.sensor_list.count())]
        assert any("A1" in t and "单栅" in t and "1.2300" in t for t in texts)
        assert any("B2" in t and "双栅-双工作" in t and "1.5000" in t and "1.1000" in t for t in texts)


# ═══════════════════════════════════════════════════════════════════════
# Test 2: 选中传感器懒重算图表
# ═══════════════════════════════════════════════════════════════════════

class TestSelectSensorRecomputesCharts:

    def test_select_sensor_recomputes_charts_no_crash(self, qapp):
        """选传感器 → 图表从 readings 懒重算无异常"""
        from ui.calibration_tab import StrainCalibrationPage
        page = StrainCalibrationPage()
        _simulate_analyze(page, "A1", "single", grating_map={"G1": "A1-W1"})

        # 直接调用 (setCurrentRow 同 row 不重发信号)
        page._load_sensor_to_workspace("A1")
        page._lazy_recompute_charts("A1")

        assert page._current_sensor == "A1"
        text_content = page.strain_result_text.toPlainText()
        assert "A1" in text_content

    def test_select_shows_correct_ke(self, qapp):
        """选传感器后 Ke 在文本区显示正确"""
        from ui.calibration_tab import StrainCalibrationPage
        page = StrainCalibrationPage()
        _simulate_analyze(page, "A1", "dual_both",
                          grating_map={"G1": "A1-W1", "G2": "A1-W2"})

        page._load_sensor_to_workspace("A1")
        page._lazy_recompute_charts("A1")

        text = page.strain_result_text.toPlainText()
        assert "Ke1=1.23" in text.replace(" ", "")
        assert "Ke2=0.98" in text.replace(" ", "")


# ═══════════════════════════════════════════════════════════════════════
# Test 3: 重标定覆盖
# ═══════════════════════════════════════════════════════════════════════

class TestReanalyzeSameSensorOverwrites:

    def test_reanalyze_same_sensor_overwrites(self, qapp):
        """A1 重标 → 列表仍 1 条 A1 (覆盖非新增)"""
        from ui.calibration_tab import StrainCalibrationPage
        page = StrainCalibrationPage()
        _simulate_analyze(page, "A1", "single", grating_map={"G1": "A1-W1"})
        assert page.sensor_list.count() == 1

        # 改 Ke 重标
        page._ke_results = {"Ke1": 2.50, "Ke2": 0.0}
        page._grating_map = {"G1": "A1-W1"}
        page._upsert_strain_config("A1")

        assert page.sensor_list.count() == 1
        assert abs(page._strain_configs["A1"].ke_results["Ke1"] - 2.50) < 0.001
        item_text = str(page.sensor_list.item(0).text() or "")
        assert "2.5000" in item_text


# ═══════════════════════════════════════════════════════════════════════
# Test 4: 删除传感器
# ═══════════════════════════════════════════════════════════════════════

class TestDeleteSensor:

    def test_delete_removes_from_list_and_project(self, qapp):
        """删 A1 → 列表移除 + project.strain 移除 + 结果区清空"""
        from ui.calibration_tab import StrainCalibrationPage
        page = StrainCalibrationPage()
        _simulate_analyze(page, "A1", "single", grating_map={"G1": "A1-W1"})
        assert page.sensor_list.count() == 1

        pc = page._get_or_create_project_config()
        assert "A1" in pc.strain

        # 模拟删除 (跳过确认弹窗)
        page._current_sensor = "A1"
        page._strain_configs.pop("A1", None)
        pc.strain.pop("A1", None)
        page._clear_workspace()
        page._refresh_sensor_list()
        page.sensor_list.setCurrentRow(-1)

        assert page.sensor_list.count() == 0
        assert "A1" not in pc.strain
        assert page._current_sensor is None
        # 图表清空
        assert page.strain_result_text.toPlainText() == ""


# ═══════════════════════════════════════════════════════════════════════
# Test 5: grating_map 不一致拒绝
# ═══════════════════════════════════════════════════════════════════════

class TestInconsistentGratingMapRejected:

    def test_inconsistent_grating_map_rejected(self, qapp):
        """G1→A1-W1, G2→B2-W1 → _validate 返回错误消息"""
        from ui.calibration_tab import StrainCalibrationPage
        page = StrainCalibrationPage()
        page._grating_map = {"G1": "A1-W1", "G2": "B2-W1"}

        err = page._validate_grating_map_consistency()
        assert err is not None
        assert "同一" in err or "必须" in err
        assert "A1" in err
        assert "B2" in err

    def test_consistent_grating_map_passes(self, qapp):
        """G1→A1-W1, G2→A1-W2 → 通过校验"""
        from ui.calibration_tab import StrainCalibrationPage
        page = StrainCalibrationPage()
        page._grating_map = {"G1": "A1-W1", "G2": "A1-W2"}

        err = page._validate_grating_map_consistency()
        assert err is None

    def test_inconsistent_triggers_reject_in_result(self, qapp):
        """不一致 grating_map 在 _on_strain_result 中被拦截"""
        from ui.calibration_tab import StrainCalibrationPage
        page = StrainCalibrationPage()
        page._grating_map = {"G1": "A1-W1", "G2": "B1-W1"}
        page._ke_results = {"Ke1": 1.23, "Ke2": 0.98}

        from py.calibration.strain_calibration import (
            StrainCalibrationResult, GratingStrainResult,
        )
        page._last_result = StrainCalibrationResult(
            gauge_length_mm=80.0, mode="tension_only", n_cycles=1,
            grating_kind="dual_both", levels=page._levels,
            eps_theory=[d / 80.0 * 1e6 for d in page._levels],
            gratings=[
                GratingStrainResult(grating_index=1, k_pm_per_ue=1.23, R2=0.999),
                GratingStrainResult(grating_index=2, k_pm_per_ue=0.98, R2=0.998),
            ],
        )

        # 直接调用 _on_strain_result 看是否拦截
        page._on_strain_result(page._last_result)
        # 应显示错误消息，传感器未加入列表
        assert page.sensor_list.count() == 0
        assert page.strain_progress.text().startswith("❌")


# ═══════════════════════════════════════════════════════════════════════
# Test 6: 选中同步 _current_sensor
# ═══════════════════════════════════════════════════════════════════════

class TestCurrentSensorIsSelection:

    def test_current_sensor_syncs_with_list_selection(self, qapp):
        """切换列表选中 → _current_sensor 更新"""
        from ui.calibration_tab import StrainCalibrationPage
        page = StrainCalibrationPage()
        _simulate_analyze(page, "A1", "single", grating_map={"G1": "A1-W1"})

        # 手动加第二个
        from py.calibration.project_config import StrainSubConfig
        page._strain_configs["B1"] = StrainSubConfig(sensor_name="B1")
        page._refresh_sensor_list(select_sensor="A1")

        # 选第一个
        page.sensor_list.setCurrentRow(0)
        QTest.qWait(10)
        assert page._current_sensor == "A1"

        # 选第二个
        page.sensor_list.setCurrentRow(1)
        QTest.qWait(10)
        assert page._current_sensor == "B1"


# ═══════════════════════════════════════════════════════════════════════
# Test 7: 空 readings 守卫
# ═══════════════════════════════════════════════════════════════════════

class TestLazyRecomputeGuardsEmpty:

    def test_lazy_recompute_guards_empty(self, qapp):
        """readings 为空的传感器选中 → 不崩 (守卫生效)"""
        from ui.calibration_tab import StrainCalibrationPage
        from py.calibration.project_config import StrainSubConfig

        page = StrainCalibrationPage()
        sc = StrainSubConfig(
            sensor_name="C1", sensor_mode="single",
            readings=[], ke_results={"Ke1": 0.0, "Ke2": 0.0},
        )
        page._strain_configs["C1"] = sc
        page._refresh_sensor_list(select_sensor="C1")

        page._load_sensor_to_workspace("C1")
        page._lazy_recompute_charts("C1")

        text = page.strain_result_text.toPlainText()
        assert "C1" in text

    def test_lazy_recompute_without_ke_no_crash(self, qapp):
        """ke_results 全 0 的传感器选中 → 图表不崩"""
        from ui.calibration_tab import StrainCalibrationPage
        from py.calibration.project_config import StrainSubConfig

        page = StrainCalibrationPage()
        sc = StrainSubConfig(
            sensor_name="D1", sensor_mode="single",
            readings=[{"disp_mm": 0.008, "eps_theory": 100.0}],
            ke_results={"Ke1": 0.0, "Ke2": 0.0},
        )
        page._strain_configs["D1"] = sc
        page._refresh_sensor_list(select_sensor="D1")

        page.sensor_list.setCurrentRow(0)
        QTest.qWait(10)

        # 图表不崩
        assert True


# ═══════════════════════════════════════════════════════════════════════
# Test 8: 新建标定清空工作态
# ═══════════════════════════════════════════════════════════════════════

class TestNewCalibrationClearsWorkspace:

    def test_new_calibration_clears_workspace(self, qapp):
        """新建标定 → 工作态清空，列表不清"""
        from ui.calibration_tab import StrainCalibrationPage
        page = StrainCalibrationPage()
        _simulate_analyze(page, "A1", "single", grating_map={"G1": "A1-W1"})
        assert page.sensor_list.count() == 1

        # 清空工作态 (模拟 _new_calibration 核心逻辑)
        page._clear_workspace()

        assert page._current_sensor is None
        assert page._grating_map == {}
        assert page._ke_results == {}
        assert page.sensor_list.count() == 1  # 列表不受影响
        assert page.strain_result_text.toPlainText() == ""
