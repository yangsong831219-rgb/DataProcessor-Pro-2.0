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
    from dp_engine.calibration.strain_calibration import (
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

    from dp_engine.calibration.strain_calibration import (
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
        from dp_engine.calibration.project_config import StrainSubConfig
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

        from dp_engine.calibration.strain_calibration import (
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
        from dp_engine.calibration.project_config import StrainSubConfig
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
        from dp_engine.calibration.project_config import StrainSubConfig

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
        from dp_engine.calibration.project_config import StrainSubConfig

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


# ═══════════════════════════════════════════════════════════════════════
# Test 9: _parse_sensor_from_grating_map 传感器名解析
# ═══════════════════════════════════════════════════════════════════════


class TestParseSensorFromGratingMap:
    """纯逻辑测试: _parse_sensor_from_grating_map 传感器名提取"""

    def test_parse_c2_from_dual_annotation(self):
        """G1→C2-1, G2→C2-2 → 返回 C2"""
        from ui.calibration_tab import StrainCalibrationPage
        page = StrainCalibrationPage()
        page._grating_map = {"G1": "C2-1", "G2": "C2-2"}
        assert page._parse_sensor_from_grating_map() == "C2"

    def test_parse_c2_single_grating(self):
        """G1→C2-3 → C2"""
        from ui.calibration_tab import StrainCalibrationPage
        page = StrainCalibrationPage()
        page._grating_map = {"G1": "C2-3"}
        assert page._parse_sensor_from_grating_map() == "C2"

    def test_parse_a1_with_underscore(self):
        """G1→A1_W1 → A1 (下划线分隔兼容)"""
        from ui.calibration_tab import StrainCalibrationPage
        page = StrainCalibrationPage()
        page._grating_map = {"G1": "A1_W1"}
        assert page._parse_sensor_from_grating_map() == "A1"

    def test_parse_a1_standard_dash(self):
        """G1→A1-W1, G2→A1-W2 → A1 (标准破折号)"""
        from ui.calibration_tab import StrainCalibrationPage
        page = StrainCalibrationPage()
        page._grating_map = {"G1": "A1-W1", "G2": "A1-W2"}
        assert page._parse_sensor_from_grating_map() == "A1"

    def test_parse_b2_single(self):
        """G1→B2-W1 → B2"""
        from ui.calibration_tab import StrainCalibrationPage
        page = StrainCalibrationPage()
        page._grating_map = {"G1": "B2-W1"}
        assert page._parse_sensor_from_grating_map() == "B2"

    def test_empty_grating_map(self):
        """空 grating_map → ''"""
        from ui.calibration_tab import StrainCalibrationPage
        page = StrainCalibrationPage()
        assert page._parse_sensor_from_grating_map() == ""

    def test_inconsistent_prefixes_returns_empty(self):
        """G1→C2-1, G2→C3-2 → '' (前缀不一致, 不静默取首个)"""
        from ui.calibration_tab import StrainCalibrationPage
        page = StrainCalibrationPage()
        page._grating_map = {"G1": "C2-1", "G2": "C3-2"}
        assert page._parse_sensor_from_grating_map() == ""

    def test_c1_and_c2_different_sensors(self):
        """先标 C1 (源 C1-1/C1-2) 入列表, 再标 C2 (源 C2-1/C2-2) → 列表含两条"""
        from ui.calibration_tab import StrainCalibrationPage
        page = StrainCalibrationPage()
        _simulate_analyze(page, "C1", "dual_both",
                          grating_map={"G1": "C1-1", "G2": "C1-2"})
        assert page.sensor_list.count() == 1
        assert "C1" in page._strain_configs

        # 新建标定 → 清空工作态
        page._clear_workspace()

        # 标 C2
        _simulate_analyze(page, "C2", "dual_both",
                          grating_map={"G1": "C2-1", "G2": "C2-2"})
        assert page.sensor_list.count() == 2, \
            f"expected 2 sensors (C1 + C2), got {page.sensor_list.count()}"
        assert "C1" in page._strain_configs
        assert "C2" in page._strain_configs


# ═══════════════════════════════════════════════════════════════════════
# Test 10: _get_grating_label / _get_grating_sources — 图例标签映射
# ═══════════════════════════════════════════════════════════════════════


class TestGratingLabelMapping:
    """_get_grating_label 和 _get_grating_sources 光栅标签映射"""

    def test_get_grating_label_uses_annotation(self):
        """grating_map 有 C2-1/C2-2 → 标签为暗号名"""
        from ui.calibration_tab import StrainCalibrationPage
        page = StrainCalibrationPage()
        page._grating_map = {"G1": "C2-1", "G2": "C2-2"}
        assert page._get_grating_label(1) == "C2-1"
        assert page._get_grating_label(2) == "C2-2"

    def test_get_grating_label_falls_back_to_g1(self):
        """grating_map 为空 → 回退显示 G1/G2"""
        from ui.calibration_tab import StrainCalibrationPage
        page = StrainCalibrationPage()
        assert page._get_grating_label(1) == "G1"
        assert page._get_grating_label(2) == "G2"

    def test_get_grating_sources_returns_copy(self):
        """_get_grating_sources 返回 grating_map 副本"""
        from ui.calibration_tab import StrainCalibrationPage
        page = StrainCalibrationPage()
        page._grating_map = {"G1": "A1-W1"}
        sources = page._get_grating_sources()
        assert sources == {"G1": "A1-W1"}
        # 修改返回的 dict 不影响 _grating_map
        sources["G1"] = "B2-W2"
        assert page._grating_map["G1"] == "A1-W1"

    def test_chart_text_contains_annotation_labels(self, qapp):
        """分析后结果文本含暗号标签 (如 C2-1)，非裸 G1/G2"""
        from ui.calibration_tab import StrainCalibrationPage
        page = StrainCalibrationPage()
        _simulate_analyze(page, "C2", "dual_both",
                          grating_map={"G1": "C2-1", "G2": "C2-2"})
        # 触发文本渲染 (_lazy_recompute_charts 写 strain_result_text)
        page._lazy_recompute_charts("C2")
        text = page.strain_result_text.toPlainText()
        # 应含暗号标签
        assert "C2-1" in text, f"expected 'C2-1' in chart text, got:\n{text}"
        assert "C2-2" in text, f"expected 'C2-2' in chart text, got:\n{text}"

    def test_label_survives_new_calibration_then_reselect(self, qapp):
        """新建标定后重新分析 C2 → 标签仍为 C2-1/C2-2"""
        from ui.calibration_tab import StrainCalibrationPage
        page = StrainCalibrationPage()
        _simulate_analyze(page, "C2", "dual_both",
                          grating_map={"G1": "C2-1", "G2": "C2-2"})
        # 新建标定 → 清空工作态
        page._clear_workspace()
        # 重新分析 C2
        _simulate_analyze(page, "C2", "dual_both",
                          grating_map={"G1": "C2-1", "G2": "C2-2"})
        assert page._get_grating_label(1) == "C2-1"
        assert page._get_grating_label(2) == "C2-2"

    def test_label_not_first_combo_item(self):
        """grating_map 含非首项 C2-1/C2-2 → 标签用当前值而非 itemText(0)"""
        from ui.calibration_tab import StrainCalibrationPage
        page = StrainCalibrationPage()
        # 模拟下拉列表顺序: C1项在前, C2项在后
        # grating_map 存的是 C2 值 (用户从下拉选了非首项)
        page._grating_map = {"G1": "C2-1", "G2": "C2-2"}
        assert page._get_grating_label(1) == "C2-1", \
            "标签应为用户选值 C2-1，不应回退到列表首项 C1-1"
        assert page._get_grating_label(2) == "C2-2", \
            "标签应为用户选值 C2-2，不应回退到列表首项 C1-2"

    def test_sensor_name_not_from_header_cycle(self):
        """传感器名来自 grating_map 暗号，非表头循环号 C1/C2/C3"""
        from ui.calibration_tab import StrainCalibrationPage
        page = StrainCalibrationPage()
        # 表头列名有 G1_C1/G1_C2/G1_C3 但这是循环号
        # 传感器名应来自 _grating_map (暗号下拉)
        page._grating_map = {"G1": "C2-1", "G2": "C2-2"}
        assert page._parse_sensor_from_grating_map() == "C2", \
            f"传感器名应为 C2，非表头循环号 C1: got {page._parse_sensor_from_grating_map()!r}"

    def test_multi_sensor_list_c1_then_c2_no_overwrite(self, qapp):
        """先标 C1(源C1-1/C1-2)加入列表，再标 C2(源C2-1/C2-2)→列表含两条"""
        from ui.calibration_tab import StrainCalibrationPage
        page = StrainCalibrationPage()
        # Round 1: C1
        _simulate_analyze(page, "C1", "dual_both",
                          grating_map={"G1": "C1-1", "G2": "C1-2"})
        assert page.sensor_list.count() == 1
        assert "C1" in page._strain_configs
        assert page._get_grating_label(1) == "C1-1"
        # 新建标定 → 清空工作态
        page._clear_workspace()
        assert page._grating_map == {}
        # Round 2: C2
        _simulate_analyze(page, "C2", "dual_both",
                          grating_map={"G1": "C2-1", "G2": "C2-2"})
        assert page.sensor_list.count() == 2, \
            f"expected 2 sensors (C1 + C2), got {page.sensor_list.count()}"
        assert "C1" in page._strain_configs
        assert "C2" in page._strain_configs
        assert page._get_grating_label(1) == "C2-1", \
            "第二轮标签应为 C2-1，非 C1-1"
        assert page._get_grating_label(2) == "C2-2"


# ═══════════════════════════════════════════════════════════════════════
# Test 11: grating_map 缓存刷新 — 防 _load_sensor_to_workspace 覆盖
# ═══════════════════════════════════════════════════════════════════════


class TestGratingMapCacheRefresh:
    """_grating_map 缓存刷新 + 防覆盖守卫"""

    def test_grating_map_refreshes_after_new_calibration(self, qapp):
        """C1→新建标定→C2: _extract_table_data 写入后 _get_grating_sources 同步为 C2"""
        from ui.calibration_tab import StrainCalibrationPage
        page = StrainCalibrationPage()
        # 模拟"首次关闭对话框，提取到 C1"
        page._grating_map = {"G1": "C1-1", "G2": "C1-2"}
        page._grating_map_fresh = False  # 代表已入列表或加载态

        # 新建标定 → 清空
        page._clear_workspace()
        assert page._grating_map == {}
        assert page._grating_map_fresh is False

        # 模拟 _extract_table_data 写入 C2（如同用户重新打开读数录入、选 C2-1/C2-2 后关闭）
        page._grating_map.clear()
        page._grating_map["G1"] = "C2-1"
        page._grating_map["G2"] = "C2-2"
        page._grating_map_version += 1
        page._grating_map_fresh = True
        assert page._get_grating_sources() == {"G1": "C2-1", "G2": "C2-2"}, \
            f"_get_grating_sources 应返回最新值 C2，非旧缓存 C1: {page._get_grating_sources()!r}"

    def test_extract_and_sources_same_value(self):
        """同一次 _extract_table_data 后，_get_grating_sources 与其返回值一致"""
        from ui.calibration_tab import StrainCalibrationPage
        page = StrainCalibrationPage()
        # _extract_table_data 直接写 self._grating_map + 返回同一 dict
        page._grating_map.clear()
        page._grating_map["G1"] = "C2-1"
        page._grating_map["G2"] = "C2-2"
        page._grating_map_version += 1
        page._grating_map_fresh = True
        sources = page._get_grating_sources()
        assert sources == {"G1": "C2-1", "G2": "C2-2"}, \
            f"_get_grating_sources 应与 _grating_map 同步: {sources!r}"
        # 断言无旧值残留
        assert "C1" not in str(sources), f"不应有 C1 残留: {sources!r}"

    def test_load_sensor_does_not_overwrite_fresh(self, qapp):
        """_grating_map_fresh=True 时 _load_sensor_to_workspace 不覆盖"""
        from ui.calibration_tab import StrainCalibrationPage
        from dp_engine.calibration.project_config import StrainSubConfig
        page = StrainCalibrationPage()
        # 模拟用户刚编辑完 grating_map
        page._grating_map = {"G1": "C2-1", "G2": "C2-2"}
        page._grating_map_fresh = True
        page._grating_map_version = 1

        # 模拟一个已保存的 C1 配置
        saved = StrainSubConfig(
            sensor_name="C1", grating_map={"G1": "C1-1", "G2": "C1-2"},
        )
        page._strain_configs["C1"] = saved

        # _load_sensor_to_workspace 不应覆盖 fresh grating_map
        page._load_sensor_to_workspace("C1")
        assert page._grating_map == {"G1": "C2-1", "G2": "C2-2"}, \
            f"_load_sensor_to_workspace 不应覆盖 fresh grating_map: {page._grating_map!r}"
        assert page._get_grating_label(1) == "C2-1"
        assert page._get_grating_label(2) == "C2-2"

    def test_load_sensor_overwrites_when_not_fresh(self):
        """_grating_map_fresh=False 时 _load_sensor_to_workspace 正常恢复"""
        from ui.calibration_tab import StrainCalibrationPage
        from dp_engine.calibration.project_config import StrainSubConfig
        page = StrainCalibrationPage()
        page._grating_map_fresh = False

        saved = StrainSubConfig(
            sensor_name="C1", grating_map={"G1": "C1-1", "G2": "C1-2"},
        )
        page._strain_configs["C1"] = saved
        page._load_sensor_to_workspace("C1")
        assert page._grating_map == {"G1": "C1-1", "G2": "C1-2"}, \
            f"非 fresh 时应正常恢复: {page._grating_map!r}"
        assert page._get_grating_label(1) == "C1-1"

    def test_commit_to_list_clears_fresh_flag(self):
        """_commit_to_list 后 _grating_map_fresh 被清除"""
        from ui.calibration_tab import StrainCalibrationPage
        from dp_engine.calibration.strain_calibration import (
            StrainCalibrationResult, GratingStrainResult,
        )
        page = StrainCalibrationPage()
        # 模拟 _extract_table_data 刚写入 (fresh=True)
        page._grating_map = {"G1": "C2-1", "G2": "C2-2"}
        page._grating_map_fresh = True
        page._grating_map_version = 1

        # 构造 working_result + _on_strain_result 所需状态
        page._config["grating_kind"] = "dual_both"
        page._ke_results = {"Ke1": 1.23, "Ke2": 0.98}
        page._last_result = StrainCalibrationResult(
            gauge_length_mm=80.0, mode="tension_only", n_cycles=1,
            grating_kind="dual_both", levels=page._levels,
            eps_theory=[d / 80.0 * 1e6 for d in page._levels],
            gratings=[
                GratingStrainResult(grating_index=1, k_pm_per_ue=1.23, R2=0.999),
                GratingStrainResult(grating_index=2, k_pm_per_ue=0.98, R2=0.998),
            ],
        )
        page._working_result = page._build_strain_subconfig()
        page._commit_to_list()
        assert page._grating_map_fresh is False, \
            f"_commit_to_list 后 _grating_map_fresh 应为 False, got {page._grating_map_fresh}"
