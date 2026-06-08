"""Phase A 对话框测试"""

from __future__ import annotations
import pytest
import numpy as np
import pandas as pd
from PyQt6.QtWidgets import QApplication, QTableWidgetItem


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    yield app


@pytest.fixture
def sample_df():
    np.random.seed(42)
    parts = []
    for level in range(1, 8):
        base = 1525.0 + level * 5.0
        parts.append(pd.DataFrame({
            "A1-W1": np.full(200, base) + np.random.normal(0, 0.05, 200),
            "A1-W2": np.full(200, base + 5.0) + np.random.normal(0, 0.05, 200),
        }))
    return pd.concat(parts, ignore_index=True)


def test_run_button_disabled_with_no_legal_annotations(qapp, sample_df):
    from ui.calibration_tab import PhaseADialog
    ann = {"A1-W1": "w1-类型-位置", "A1-W2": "w2-类型-位置"}
    dlg = PhaseADialog(sample_df, ann, {},
        {"hold_time_min": 6.0, "sample_interval_s": 2.0, "rolling_window": 25,
         "std_percentile": 45.0, "min_plateau_samples": 50, "head_trim_ratio": 0.70},
        None)
    assert not dlg.run_btn.isEnabled()
    assert "合法" in dlg.run_btn.toolTip()
    dlg.close()


def test_run_button_visual_gray_when_disabled(qapp, sample_df):
    from ui.calibration_tab import PhaseADialog, _BTN_STYLE_DISABLED
    ann = {"A1-W1": "w1-类型-位置"}
    dlg = PhaseADialog(sample_df, ann, {},
        {"hold_time_min": 6.0, "sample_interval_s": 2.0, "rolling_window": 25,
         "std_percentile": 45.0, "min_plateau_samples": 50, "head_trim_ratio": 0.70},
        None)
    assert not dlg.run_btn.isEnabled()
    ss = dlg.run_btn.styleSheet()
    assert "E0E0E0" in ss or "gray" in ss.lower()
    dlg.close()


def test_run_button_enabled_after_apply_annotations(qapp, sample_df):
    from ui.calibration_tab import PhaseADialog
    ann = {"A1-W1": "w1-类型-位置", "A1-W2": "w2-类型-位置"}
    dlg = PhaseADialog(sample_df, ann, {},
        {"hold_time_min": 6.0, "sample_interval_s": 2.0, "rolling_window": 25,
         "std_percentile": 45.0, "min_plateau_samples": 50, "head_trim_ratio": 0.70},
        None)
    assert not dlg.run_btn.isEnabled()
    # Simulate filling valid annotations via QTableWidgetItem (replaced setCellWidget)
    for i in range(dlg.fill_table.rowCount()):
        col_item = dlg.fill_table.item(i, 0)
        if not col_item:
            continue
        cname = col_item.text().strip()
        if "W1" in cname:
            dlg.fill_table.setItem(i, 2, QTableWidgetItem("A1-W1"))
        elif "W2" in cname:
            dlg.fill_table.setItem(i, 2, QTableWidgetItem("A1-W2"))
    dlg._on_apply()
    assert dlg.run_btn.isEnabled()
    dlg.close()


def test_run_emits_result(qapp, sample_df):
    from ui.calibration_tab import PhaseADialog
    ann = {"A1-W1": "A1-W1", "A1-W2": "A1-W2"}
    groups = {"A1": [{"col_idx": 0, "name": "A1-W1"}, {"col_idx": 1, "name": "A1-W2"}]}
    dlg = PhaseADialog(sample_df, ann, groups,
        {"hold_time_min": 6.0, "sample_interval_s": 2.0, "rolling_window": 25,
         "std_percentile": 45.0, "min_plateau_samples": 50, "head_trim_ratio": 0.70},
        None)
    assert dlg.run_btn.isEnabled()

    # 验证 PhaseAWorker 对合法暗号产出 S_eff
    from ui.calibration_tab import PhaseAWorker
    # 与 test_phase_a_worker.py 同参数 (已验证能检出 7 平台)
    setpoints = [10.0, 20.0, 30.0, 40.0, 50.0, 60.0, 70.0]
    worker = PhaseAWorker(
        sample_df, ["A1-W1", "A1-W2"], setpoints,
        {"rolling_window": 50, "std_percentile": 85.0, "min_plateau_samples": 50, "head_trim_ratio": 0.70},
    )
    worker.run()
    assert worker._last_result is not None, "Phase A worker 未产出结果"
    S_eff = worker._last_result.get("S_eff", {})
    assert "A1-W1" in S_eff, f"S_eff keys should not have _d: {list(S_eff.keys())}"
    assert len(S_eff) == 2

    # 验证对话框的 _on_result 正确渲染 (S_eff 表格 + 图表按钮启用)
    dlg._on_result(worker._last_result)
    assert dlg.seff_table.rowCount() == 2, f"seff_table 应有 2 行，实际 {dlg.seff_table.rowCount()}"
    assert dlg.charts_btn.isEnabled(), "图表按钮应在 _on_result 后启用"
    dlg.close()


def test_detection_params_dialog_instantiates(qapp, sample_df):
    """DetectionParamsDialog 构造不抛异常，控件都存在"""
    from ui.calibration_tab import DetectionParamsDialog
    dlg = DetectionParamsDialog(
        {"hold_time_min": 6.0, "sample_interval_s": 2.0, "rolling_window": 25,
         "std_percentile": 45.0, "min_plateau_samples": 180, "head_trim_ratio": 0.70},
        df=sample_df, wavelength_cols=["A1-W1"], n_expected=7,
    )
    assert hasattr(dlg, 'hold_time_spin')
    assert hasattr(dlg, 'sample_interval_spin')
    assert hasattr(dlg, 'shortest_display')
    dlg.close()


def test_detection_params_hold_time_signal(qapp, sample_df):
    """修改每级恒温时长后 shortest_display 联动更新"""
    from ui.calibration_tab import DetectionParamsDialog
    dlg = DetectionParamsDialog(
        {"hold_time_min": 6.0, "sample_interval_s": 2.0, "rolling_window": 25,
         "std_percentile": 45.0, "min_plateau_samples": 180, "head_trim_ratio": 0.70},
        df=sample_df, wavelength_cols=["A1-W1"], n_expected=7,
    )
    old_text = dlg.shortest_display.text()
    dlg.hold_time_spin.setValue(12.0)
    new_text = dlg.shortest_display.text()
    assert old_text != new_text, f"shortest_display 未联动: {old_text!r} → {new_text!r}"
    dlg.close()


def test_phase_b_gated_by_phase_a(qapp):
    from ui.calibration_tab import TemperatureCalibrationPage
    page = TemperatureCalibrationPage()
    assert not page.btn_phase_b.isEnabled()
    assert page.btn_phase_b.toolTip()  # 非空 tooltip


# ═══════════════════════════════════════════════════════════════════════
# 关闭路径回归: accept / reject Esc / reject X — 必须全写回
# ═══════════════════════════════════════════════════════════════════════

class TestPhaseAWriteback:
    """PhaseADialog accept/reject 写回 — 测试 _write_state_to_main_page()"""

    def test_writeback_sets_phase_a_state(self, qapp):
        """_write_state_to_main_page → tp._phase_a_state 正确填充"""
        from ui.calibration_tab import PhaseADialog, TemperatureCalibrationPage
        import pandas as pd
        df = pd.DataFrame({f"ch{i}": [1550.0 + i * 5] * 5 for i in range(1, 3)})
        page = TemperatureCalibrationPage()
        page._loaded_df = df
        annot = {"ch1": "w1-类型-位置", "ch2": "w2-类型-位置"}
        # parent=page → _get_temp_page() 能找到
        dlg = PhaseADialog(df, annot, {},
            {"hold_time_min": 6.0, "sample_interval_s": 2.0, "rolling_window": 25,
             "std_percentile": 45.0, "min_plateau_samples": 50, "head_trim_ratio": 0.70},
            time_col_idx=None, parent=page)
        dlg._annotation["ch1"] = "A1-W1"
        dlg._annotation["ch2"] = "A1-W2"
        dlg._write_state_to_main_page()
        assert page._phase_a_state["annotation"]["ch1"] == "A1-W1"

    def test_accept_triggers_writeback(self, qapp):
        """accept() 内部调用 _write_state_to_main_page"""
        from ui.calibration_tab import PhaseADialog, TemperatureCalibrationPage
        import pandas as pd
        df = pd.DataFrame({f"ch{i}": [1550.0 + i * 5] * 5 for i in range(1, 3)})
        page = TemperatureCalibrationPage()
        page._loaded_df = df
        annot = {"ch1": "w1-类型-位置", "ch2": "w2-类型-位置"}
        dlg = PhaseADialog(df, annot, {},
            {"hold_time_min": 6.0, "sample_interval_s": 2.0, "rolling_window": 25,
             "std_percentile": 45.0, "min_plateau_samples": 50, "head_trim_ratio": 0.70},
            time_col_idx=None, parent=page)
        dlg._annotation["ch1"] = "A1-W1"
        dlg.accept()
        assert page._phase_a_state["annotation"]["ch1"] == "A1-W1"

    def test_reject_triggers_writeback(self, qapp):
        """reject() 内部调用 _write_state_to_main_page"""
        from ui.calibration_tab import PhaseADialog, TemperatureCalibrationPage
        import pandas as pd
        df = pd.DataFrame({f"ch{i}": [1550.0 + i * 5] * 5 for i in range(1, 3)})
        page = TemperatureCalibrationPage()
        page._loaded_df = df
        annot = {"ch1": "w1-类型-位置", "ch2": "w2-类型-位置"}
        dlg = PhaseADialog(df, annot, {},
            {"hold_time_min": 6.0, "sample_interval_s": 2.0, "rolling_window": 25,
             "std_percentile": 45.0, "min_plateau_samples": 50, "head_trim_ratio": 0.70},
            time_col_idx=None, parent=page)
        dlg._annotation["ch1"] = "A1-W1"
        dlg.reject()
        assert page._phase_a_state["annotation"]["ch1"] == "A1-W1"


class TestPhaseBWriteback:
    """PhaseBDialog accept/reject 写回 — 测试 _write_state_to_main_page()"""

    def test_writeback_sets_phase_b_state(self, qapp):
        """_write_state_to_main_page → tp._phase_b_state 正确填充"""
        from ui.calibration_tab import PhaseBDialog, TemperatureCalibrationPage
        import pandas as pd
        df = pd.DataFrame({"A1-W1": [1550.0] * 5, "A1-W2": [1545.0] * 5})
        pa = {"S_eff": {"A1-W1": {"slope": 27.8}, "A1-W2": {"slope": 29.4}}}
        groups = {"A1": [{"col_name": "A1-W1", "name": "A1-W1"},
                         {"col_name": "A1-W2", "name": "A1-W2"}]}
        page = TemperatureCalibrationPage()
        dlg = PhaseBDialog(df, groups, pa, parent=page)
        dlg._coeffs = {"A1": {"Ke1": 0.7, "Ke2": 1.1}}
        # coef_table 默认填了 1.2, 这里模拟用户修改为 0.7/1.1
        if dlg.coef_table.rowCount() > 0:
            dlg.coef_table.setItem(0, 1, QTableWidgetItem("0.7"))
            dlg.coef_table.setItem(0, 2, QTableWidgetItem("1.1"))
        dlg._write_state_to_main_page()
        assert "ke_table" in page._phase_b_state
        assert page._phase_b_state["ke_table"]["A1"]["Ke1"] == 0.7

    def test_accept_triggers_writeback(self, qapp):
        """PhaseB accept() → _write_state_to_main_page"""
        from ui.calibration_tab import PhaseBDialog, TemperatureCalibrationPage
        import pandas as pd
        df = pd.DataFrame({"A1-W1": [1550.0] * 5, "A1-W2": [1545.0] * 5})
        pa = {"S_eff": {"A1-W1": {"slope": 27.8}, "A1-W2": {"slope": 29.4}}}
        groups = {"A1": [{"col_name": "A1-W1", "name": "A1-W1"},
                         {"col_name": "A1-W2", "name": "A1-W2"}]}
        page = TemperatureCalibrationPage()
        dlg = PhaseBDialog(df, groups, pa, parent=page)
        dlg._coeffs = {"A1": {"Ke1": 0.7, "Ke2": 1.1}}
        if dlg.coef_table.rowCount() > 0:
            dlg.coef_table.setItem(0, 1, QTableWidgetItem("0.7"))
            dlg.coef_table.setItem(0, 2, QTableWidgetItem("1.1"))
        dlg.accept()
        assert page._phase_b_state["ke_table"]["A1"]["Ke1"] == 0.7

    def test_reject_triggers_writeback(self, qapp):
        """PhaseB reject() → _write_state_to_main_page"""
        from ui.calibration_tab import PhaseBDialog, TemperatureCalibrationPage
        import pandas as pd
        df = pd.DataFrame({"A1-W1": [1550.0] * 5, "A1-W2": [1545.0] * 5})
        pa = {"S_eff": {"A1-W1": {"slope": 27.8}, "A1-W2": {"slope": 29.4}}}
        groups = {"A1": [{"col_name": "A1-W1", "name": "A1-W1"},
                         {"col_name": "A1-W2", "name": "A1-W2"}]}
        page = TemperatureCalibrationPage()
        dlg = PhaseBDialog(df, groups, pa, parent=page)
        dlg._coeffs = {"A1": {"Ke1": 0.7, "Ke2": 1.1}}
        if dlg.coef_table.rowCount() > 0:
            dlg.coef_table.setItem(0, 1, QTableWidgetItem("0.7"))
            dlg.coef_table.setItem(0, 2, QTableWidgetItem("1.1"))
        dlg.reject()
        assert page._phase_b_state["ke_table"]["A1"]["Ke1"] == 0.7
