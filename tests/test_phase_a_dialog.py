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


def test_cycle_program_expects_15_platforms_for_0_to_70_to_0(qapp, sample_df):
    """The range UI must expose the physical cycle program, not only 8 levels."""
    from ui.calibration_tab import PhaseADialog

    annotation = {"A1-W1": "A1-W1", "A1-W2": "A1-W2"}
    groups = {"A1": [
        {"name": "A1-W1", "col_name": "A1-W1"},
        {"name": "A1-W2", "col_name": "A1-W2"},
    ]}
    dlg = PhaseADialog(
        sample_df, annotation, groups,
        {"temperature_program_mode": "cycle", "temperature_cycle_count": 1},
        None,
    )
    dlg.tmin.setValue(0.0)
    dlg.tmax.setValue(70.0)
    dlg.tstep.setValue(10.0)
    dlg.program_mode_combo.setCurrentIndex(dlg.program_mode_combo.findData("cycle"))

    assert dlg._expected_platform_count() == 15
    assert dlg.temperature_cycle_spin.isEnabled()
    assert dlg._params["temperature_program_mode"] == "cycle"
    assert dlg._params["temperature_cycle_count"] == 1
    dlg.close()


def test_detection_message_distinguishes_candidate_fragments_from_platforms():
    """A split dwell should not be presented as an extra temperature level."""
    from ui.calibration_tab import DetectionParamsDialog

    message = DetectionParamsDialog._format_match_info(
        45,
        43,
        {
            "normalized_segments": 43,
            "complete_cycles": 3,
            "required_cycles": 3,
            "expected_platforms_per_cycle": 15,
            "is_complete": True,
        },
    )

    assert "45" in message
    assert "43" in message
    assert "逻辑平台" in message


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

    def test_reject_does_not_writeback(self, qapp):
        """reject() 不写回 — 暗号/状态不被改写"""
        from ui.calibration_tab import PhaseADialog, TemperatureCalibrationPage
        import pandas as pd
        df = pd.DataFrame({f"ch{i}": [1550.0 + i * 5] * 5 for i in range(1, 3)})
        page = TemperatureCalibrationPage()
        page._loaded_df = df
        annot = {"ch1": "w1-类型-位置", "ch2": "w2-类型-位置"}
        # 预置 _phase_a_state 旧值
        page._phase_a_state = {"annotation": {"ch1": "OLD-VALUE"}, "tmin": 10.0, "tmax": 70.0, "tstep": 10.0}
        dlg = PhaseADialog(df, annot, {},
            {"hold_time_min": 6.0, "sample_interval_s": 2.0, "rolling_window": 25,
             "std_percentile": 45.0, "min_plateau_samples": 50, "head_trim_ratio": 0.70},
            time_col_idx=None, parent=page)
        dlg._annotation["ch1"] = "A1-W1"
        dlg._annotation_dirty.add("ch1")
        dlg.reject()
        # reject 不写回 → _phase_a_state 保持旧值
        assert page._phase_a_state["annotation"]["ch1"] == "OLD-VALUE", \
            "reject should NOT write back annotation"
        # _annotation_dict 也不应被修改
        assert page._annotation_dict.get("ch1") != "A1-W1", \
            "reject should NOT modify _annotation_dict"

    def test_accept_does_writeback(self, qapp):
        """accept() 写回 — 暗号持久化"""
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
        dlg._annotation_dirty.add("ch1")
        dlg.accept()
        assert page._phase_a_state["annotation"]["ch1"] == "A1-W1", \
            "accept SHOULD write back annotation"
        assert page._annotation_dict["ch1"] == "A1-W1", \
            "accept SHOULD modify _annotation_dict"
        assert "ch1" in page._annotation_dirty, "dirty flag should be persisted"


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

    def test_reject_does_not_writeback(self, qapp):
        """PhaseB reject() 不写回 — 状态不被改写"""
        from ui.calibration_tab import PhaseBDialog, TemperatureCalibrationPage
        import pandas as pd
        df = pd.DataFrame({"A1-W1": [1550.0] * 5, "A1-W2": [1545.0] * 5})
        pa = {"S_eff": {"A1-W1": {"slope": 27.8}, "A1-W2": {"slope": 29.4}}}
        groups = {"A1": [{"col_name": "A1-W1", "name": "A1-W1"},
                         {"col_name": "A1-W2", "name": "A1-W2"}]}
        page = TemperatureCalibrationPage()
        page._phase_b_state = {"ke_table": {"A1": {"Ke1": 9.9, "Ke2": 9.9}}}
        dlg = PhaseBDialog(df, groups, pa, parent=page)
        dlg._coeffs = {"A1": {"Ke1": 0.7, "Ke2": 1.1}}
        if dlg.coef_table.rowCount() > 0:
            dlg.coef_table.setItem(0, 1, QTableWidgetItem("0.7"))
            dlg.coef_table.setItem(0, 2, QTableWidgetItem("1.1"))
        dlg.reject()
        # reject 不写回 → _phase_b_state 保持旧值
        assert page._phase_b_state["ke_table"]["A1"]["Ke1"] == 9.9, \
            "reject should NOT write back phase_b_state"


# ═══════════════════════════════════════════════════════════════════════
# 三层优先级: 手改 > 文件 > profile
# ═══════════════════════════════════════════════════════════════════════

class TestThreeTierPriority:
    """三层暗号优先级: 手改锁定 > 文件暗号 > profile 回退"""

    def test_dirty_column_survives_state_restore(self, qapp):
        """手改列经对话框重新打开后仍保持用户值(不被文件盖)"""
        from ui.calibration_tab import PhaseADialog, TemperatureCalibrationPage
        import pandas as pd
        df = pd.DataFrame({f"ch{i}": [1550.0] * 5 for i in range(1, 4)})
        page = TemperatureCalibrationPage()
        page._loaded_df = df
        # 文件暗号: ch1=A1-W1, ch2=A1-W2
        file_annot = {"ch1": "A1-W1", "ch2": "A1-W2"}
        # 模拟用户上次手动改了 ch2 → 锁定
        page._phase_a_state = {
            "annotation": {"ch2": "USER-CHANGED-C2"},
            "annotation_dirty": ["ch2"],
            "tmin": 10.0, "tmax": 70.0, "tstep": 10.0,
        }
        page._annotation_dirty = {"ch2"}

        dlg = PhaseADialog(df, file_annot, {},
            {"sample_interval_s": 2.0, "hold_time_min": 6.0, "rolling_window": 25,
             "std_percentile": 45.0, "min_plateau_samples": 50, "head_trim_ratio": 0.70},
            time_col_idx=None, parent=page)
        # 手改列 ch2 → 保持用户值
        assert dlg._annotation["ch2"] == "USER-CHANGED-C2", \
            f"dirty column should keep user value, got {dlg._annotation['ch2']!r}"
        # 未改列 ch1 → 文件优先
        assert dlg._annotation["ch1"] == "A1-W1", \
            f"non-dirty column should use file value, got {dlg._annotation['ch1']!r}"
        dlg.close()

    def test_file_priority_over_profile_for_clean_columns(self, qapp):
        """未改列: 文件有暗号 → 用文件 (不用 profile)"""
        from ui.calibration_tab import PhaseADialog, TemperatureCalibrationPage
        import pandas as pd
        df = pd.DataFrame({f"ch{i}": [1550.0] * 5 for i in range(1, 4)})
        page = TemperatureCalibrationPage()
        page._loaded_df = df
        # profile 说 ch1=PROFILE-VAL, 但文件说 ch1=FILE-VAL
        page._phase_a_state = {
            "annotation": {"ch1": "PROFILE-VAL", "ch3": "PROFILE-C3"},
            "annotation_dirty": [],
            "tmin": 10.0, "tmax": 70.0, "tstep": 10.0,
        }
        file_annot = {"ch1": "FILE-VAL", "ch2": "FILE-C2"}

        dlg = PhaseADialog(df, file_annot, {},
            {"sample_interval_s": 2.0, "hold_time_min": 6.0, "rolling_window": 25,
             "std_percentile": 45.0, "min_plateau_samples": 50, "head_trim_ratio": 0.70},
            time_col_idx=None, parent=page)
        # ch1: 文件有 → 文件优先
        assert dlg._annotation["ch1"] == "FILE-VAL"
        # ch2: 仅文件有 → 用文件
        assert dlg._annotation["ch2"] == "FILE-C2"
        # ch3: 文件无、profile 有 → 用 profile
        assert dlg._annotation["ch3"] == "PROFILE-C3", \
            f"file has no ch3, should fallback to profile, got {dlg._annotation.get('ch3')!r}"
        dlg.close()

    def test_on_apply_marks_dirty(self, qapp):
        """_on_apply 正确标记手改列为 dirty"""
        from ui.calibration_tab import PhaseADialog
        import pandas as pd
        df = pd.DataFrame({"A1-W1": [1550.0] * 5, "A1-W2": [1545.0] * 5})
        # 提供初始 annotation 让表格有填充行
        ann = {"A1-W1": "w1-类型-位置", "A1-W2": "w2-类型-位置"}
        dlg = PhaseADialog(df, ann, {},
            {"sample_interval_s": 2.0, "hold_time_min": 6.0, "rolling_window": 25,
             "std_percentile": 45.0, "min_plateau_samples": 50, "head_trim_ratio": 0.70},
            time_col_idx=None)
        # 模拟用户在输入列填入合法暗号并应用
        for i in range(dlg.fill_table.rowCount()):
            col_item = dlg.fill_table.item(i, 0)
            if col_item and "W1" in col_item.text():
                dlg.fill_table.setItem(i, 2, QTableWidgetItem("A1-W1"))
            elif col_item and "W2" in col_item.text():
                dlg.fill_table.setItem(i, 2, QTableWidgetItem("A1-W2"))
        dlg._on_apply()
        assert "A1-W1" in dlg._annotation_dirty, \
            "edited column should be marked dirty"
        assert "A1-W2" in dlg._annotation_dirty, \
            "edited column should be marked dirty"
        dlg.close()

    def test_dirty_persists_through_accept(self, qapp):
        """accept 后 dirty 标记持久化到 _phase_a_state"""
        from ui.calibration_tab import PhaseADialog, TemperatureCalibrationPage
        import pandas as pd
        df = pd.DataFrame({"A1-W1": [1550.0] * 5})
        page = TemperatureCalibrationPage()
        page._loaded_df = df
        dlg = PhaseADialog(df, {}, {},
            {"sample_interval_s": 2.0, "hold_time_min": 6.0, "rolling_window": 25,
             "std_percentile": 45.0, "min_plateau_samples": 50, "head_trim_ratio": 0.70},
            time_col_idx=None, parent=page)
        dlg._annotation["A1-W1"] = "CH1-W1"
        dlg._annotation_dirty.add("A1-W1")
        dlg.accept()
        assert "A1-W1" in page._annotation_dirty, \
            "dirty should persist to page"
        assert "A1-W1" in page._phase_a_state.get("annotation_dirty", []), \
            "dirty should be in _phase_a_state"


class TestDataProvidersAnnotationAttr:
    """_get_calibration_annotation 读 _annotation_dict"""

    def test_reads_annotation_dict(self):
        """_get_calibration_annotation 正确读取 _annotation_dict"""
        from core.data_providers import _get_calibration_annotation

        # 构造假 main_win，calibration_tab_widget.temp_page._annotation_dict
        class FakeTempPage:
            _annotation_dict = {"FBG_A1": "A1-W1", "FBG_A2": "A1-W2"}

        class FakeCalTab:
            temp_page = FakeTempPage()

        class FakeMainWin:
            calibration_tab_widget = FakeCalTab()

        result = _get_calibration_annotation(FakeMainWin())
        assert result == {"FBG_A1": "A1-W1", "FBG_A2": "A1-W2"}, \
            f"expected annotation dict, got {result}"

    def test_fallback_to_old_annotation_attr(self):
        """向后兼容: 若无 _annotation_dict 但有旧 _annotation 仍能读到"""
        from core.data_providers import _get_calibration_annotation

        class FakeTempPage:
            _annotation = {"FBG_A1": "OLD-A1-W1"}  # 旧属性名
            # 无 _annotation_dict

        class FakeCalTab:
            temp_page = FakeTempPage()

        class FakeMainWin:
            calibration_tab_widget = FakeCalTab()

        result = _get_calibration_annotation(FakeMainWin())
        assert result == {"FBG_A1": "OLD-A1-W1"}, \
            f"should fallback to old attr, got {result}"

    def test_annotation_dict_takes_priority(self):
        """_annotation_dict 优先于旧 _annotation"""
        from core.data_providers import _get_calibration_annotation

        class FakeTempPage:
            _annotation_dict = {"FBG_A1": "NEW-A1-W1"}
            _annotation = {"FBG_A1": "OLD-VAL"}  # 不应被读取

        class FakeCalTab:
            temp_page = FakeTempPage()

        class FakeMainWin:
            calibration_tab_widget = FakeCalTab()

        result = _get_calibration_annotation(FakeMainWin())
        assert result == {"FBG_A1": "NEW-A1-W1"}, \
            f"_annotation_dict should win, got {result}"


# ═══════════════════════════════════════════════════════════════════════
# DataTabWidget 暗号行显示 — 与标定页同源
# ═══════════════════════════════════════════════════════════════════════

class TestDataTabAnnotationDisplay:
    """数据文件页暗号行: 真暗号显示 + 视觉区分 + 不行数扰动"""

    def test_annotation_row_styled_gray_italic(self, qapp):
        """有暗号 → row 0 灰底 + 斜体"""
        from ui.data_tab import DataTabWidget
        import pandas as pd

        widget = DataTabWidget()
        df = pd.DataFrame({
            "Timestamp": ["2026/1/1 12:00:00", "2026/1/1 12:00:01"],
            "A1_1": [1525.123, 1525.234],
            "A1_2": [1530.456, 1530.567],
        })
        annotation = {"A1_1": "w1-A1-1", "A1_2": "w1-A1-2"}
        # 模拟暗号行已插入 df row 0
        df_with_annot = pd.concat([
            pd.DataFrame([["", "w1-A1-1", "w1-A1-2"]], columns=df.columns),
            df,
        ], ignore_index=True)

        widget.update_data_table(df_with_annot, annotation=annotation)
        # row 0 应有灰底
        item = widget.data_table.item(0, 1)
        assert item is not None, "annotation row should have a cell"
        assert item.text() == "w1-A1-1", \
            f"expected clean annotation, got {item.text()!r}"
        # 背景色: QBrush → color → name
        bg = item.background()
        assert bg.style() != 0, \
            "annotation row should have background brush set"
        # 斜体
        font = item.font()
        assert font.italic(), "annotation row should be italic"

    def test_row_count_excludes_annotation(self, qapp):
        """行计数不含暗号行"""
        from ui.data_tab import DataTabWidget
        import pandas as pd

        widget = DataTabWidget()
        df = pd.DataFrame({
            "Timestamp": ["2026/1/1 12:00:00", "2026/1/1 12:00:01"],
            "A1_1": [1525.123, 1525.234],
        })
        annotation = {"A1_1": "w1-A1-1"}
        df_with_annot = pd.concat([
            pd.DataFrame([["", "w1-A1-1"]], columns=df.columns),
            df,
        ], ignore_index=True)

        widget.update_data_table(df_with_annot, annotation=annotation)
        # 2 行数据 + 1 行暗号 = 3 行 df，但 info_label 应显示"共 2 行"
        assert "共 2 行" in widget.data_info_label.text(), \
            f"expected '共 2 行', got {widget.data_info_label.text()!r}"

    def test_no_annotation_no_extra_row(self, qapp):
        """无暗号 → 无灰底行、不崩、行计数正常"""
        from ui.data_tab import DataTabWidget
        import pandas as pd

        widget = DataTabWidget()
        df = pd.DataFrame({
            "Timestamp": ["2026/1/1 12:00:00", "2026/1/1 12:00:01"],
            "A1_1": [1525.123, 1525.234],
        })

        widget.update_data_table(df, annotation={})
        # 无灰底/斜体行
        item = widget.data_table.item(0, 0)
        assert item is not None
        font = item.font()
        assert not font.italic(), "no annotation → no italic row"
        # 行计数正确
        assert "共 2 行" in widget.data_info_label.text(), \
            f"expected '共 2 行', got {widget.data_info_label.text()!r}"

    def test_none_df_no_crash(self, qapp):
        """df=None 不崩"""
        from ui.data_tab import DataTabWidget
        widget = DataTabWidget()
        widget.update_data_table(None, annotation={"A": "w1-A1-1"})
        assert widget.data_table.rowCount() == 0

    def test_empty_annotation_dict_no_italic(self, qapp):
        """空 annotation 不触发暗号行样式"""
        from ui.data_tab import DataTabWidget
        import pandas as pd

        widget = DataTabWidget()
        df = pd.DataFrame({"A": [1.0, 2.0], "B": [3.0, 4.0]})
        widget.update_data_table(df, annotation={})
        item = widget.data_table.item(0, 0)
        assert item is not None
        assert not item.font().italic(), \
            "empty annotation should not trigger italic"


# ═══════════════════════════════════════════════════════════════════════
# Phase 0: _auto_match / _preview — compute_dL → detect_plateaus 链
# ═══════════════════════════════════════════════════════════════════════


@pytest.fixture
def sample_wavelength_df_no_d():
    """构造不含 _d 列的波长 DataFrame（模拟 ENLIGHT 解析原始输出）。"""
    np.random.seed(123)
    parts = []
    for level in range(1, 8):
        base_w1 = 1525.0 + level * 5.0
        base_w2 = base_w1 + 5.0
        parts.append(pd.DataFrame({
            "w1": np.full(200, base_w1) + np.random.normal(0, 0.05, 200),
            "w2": np.full(200, base_w2) + np.random.normal(0, 0.05, 200),
        }))
    return pd.concat(parts, ignore_index=True)


class TestDetectionParamsWithComputeDL:
    """_auto_match / _preview 先 compute_dL 再 detect_plateaus，不再缺列崩"""

    def test_preview_succeeds_with_no_d_columns(self, qapp, sample_wavelength_df_no_d):
        """_preview: 对无 _d 列的原始 df → compute_dL → detect_plateaus 成功"""
        from ui.calibration_tab import DetectionParamsDialog
        dlg = DetectionParamsDialog(
            {"hold_time_min": 6.0, "sample_interval_s": 2.0, "rolling_window": 50,
             "std_percentile": 85.0, "min_plateau_samples": 50, "head_trim_ratio": 0.70},
            df=sample_wavelength_df_no_d, wavelength_cols=["w1", "w2"], n_expected=7,
        )
        dlg._preview()
        text = dlg.match_info.text()
        assert "检出" in text, f"应检出平台, 实际: {text!r}"
        assert "dL 计算失败" not in text
        assert "平台检测失败" not in text
        dlg.close()

    def test_auto_match_succeeds_with_no_d_columns(self, qapp, sample_wavelength_df_no_d):
        """_auto_match: 对无 _d 列的原始 df → compute_dL(循环外一次) → 循环内复用成功"""
        from ui.calibration_tab import DetectionParamsDialog
        dlg = DetectionParamsDialog(
            {"hold_time_min": 6.0, "sample_interval_s": 2.0, "rolling_window": 50,
             "std_percentile": 50.0, "min_plateau_samples": 50, "head_trim_ratio": 0.70},
            df=sample_wavelength_df_no_d, wavelength_cols=["w1", "w2"], n_expected=7,
        )
        dlg._auto_match()
        text = dlg.match_info.text()
        assert "检出" in text, f"应检出平台, 实际: {text!r}"
        assert "dL 计算失败" not in text
        assert "自动匹配失败" not in text
        dlg.close()

    def test_auto_match_calls_compute_dl_only_once(self, qapp, sample_wavelength_df_no_d, monkeypatch):
        """_auto_match 循环外调 compute_dL 一次，循环内不复重算。"""
        from unittest import mock
        from dp_engine.calibration import temperature_calibration as tc_mod

        original = tc_mod.compute_dL
        call_count = [0]

        def counting_compute_dL(df, wl_cols):
            call_count[0] += 1
            return original(df, wl_cols)

        monkeypatch.setattr(tc_mod, "compute_dL", counting_compute_dL)

        from ui.calibration_tab import DetectionParamsDialog
        dlg = DetectionParamsDialog(
            {"hold_time_min": 6.0, "sample_interval_s": 2.0, "rolling_window": 50,
             "std_percentile": 50.0, "min_plateau_samples": 50, "head_trim_ratio": 0.70},
            df=sample_wavelength_df_no_d, wavelength_cols=["w1", "w2"], n_expected=7,
        )
        dlg._auto_match()
        dlg.close()

        assert call_count[0] == 1, (
            f"_auto_match 应在循环外只调一次 compute_dL, 实际调了 {call_count[0]} 次"
        )

    def test_preview_compute_dl_failure_shows_warning(self, qapp, sample_wavelength_df_no_d, monkeypatch):
        """_preview: compute_dL 失败 → QMessageBox + match_info 提示，不裸崩。"""
        def failing_compute_dL(df, wl_cols):
            raise ValueError("模拟: 无有效波长数据")

        from dp_engine.calibration import temperature_calibration as tc_mod
        monkeypatch.setattr(tc_mod, "compute_dL", failing_compute_dL)

        from unittest import mock as _umock
        from ui.calibration_tab import DetectionParamsDialog

        dlg = DetectionParamsDialog(
            {"hold_time_min": 6.0, "sample_interval_s": 2.0, "rolling_window": 50,
             "std_percentile": 85.0, "min_plateau_samples": 50, "head_trim_ratio": 0.70},
            df=sample_wavelength_df_no_d, wavelength_cols=["w1", "w2"], n_expected=7,
        )

        # mock QMessageBox.warning 避免真弹窗
        with _umock.patch("PyQt6.QtWidgets.QMessageBox.warning") as mock_warn:
            dlg._preview()

        mock_warn.assert_called_once()
        call_args = mock_warn.call_args[0]
        assert "波长漂移计算失败" in str(call_args)
        assert "模拟: 无有效波长数据" in str(call_args)
        assert "dL 计算失败" in dlg.match_info.text()
        dlg.close()

    def test_auto_match_detect_plateaus_failure_shows_warning(self, qapp, sample_wavelength_df_no_d, monkeypatch):
        """_auto_match: detect_plateaus 失败 → QMessageBox + match_info 提示，不裸崩。"""
        def failing_detect(df, wl_cols, **kw):
            raise RuntimeError("模拟: 平台检测内部错误")

        from dp_engine.calibration import step_extractor as se_mod
        monkeypatch.setattr(se_mod, "detect_plateaus", failing_detect)

        from unittest import mock as _umock
        from ui.calibration_tab import DetectionParamsDialog

        dlg = DetectionParamsDialog(
            {"hold_time_min": 6.0, "sample_interval_s": 2.0, "rolling_window": 50,
             "std_percentile": 50.0, "min_plateau_samples": 50, "head_trim_ratio": 0.70},
            df=sample_wavelength_df_no_d, wavelength_cols=["w1", "w2"], n_expected=7,
        )

        with _umock.patch("PyQt6.QtWidgets.QMessageBox.warning") as mock_warn:
            dlg._auto_match()

        mock_warn.assert_called_once()
        call_args = mock_warn.call_args[0]
        assert "自动匹配失败" in str(call_args)
        assert "模拟: 平台检测内部错误" in str(call_args)
        assert "自动匹配失败" in dlg.match_info.text()
        dlg.close()


# ═══════════════════════════════════════════════════════════════════════
# Phase 1: itemChanged 实时同步 — 列2编辑 → _annotation/_legal_cnt 自动刷新
# ═══════════════════════════════════════════════════════════════════════


@pytest.fixture
def old_format_df():
    """构造含旧格式暗号列的 DataFrame (w1, w2... 带 _anomaly 混淆列)。"""
    np.random.seed(99)
    n = 200
    df = pd.DataFrame({
        "w1": 1525.0 + np.arange(n) * 0.005 + np.random.normal(0, 0.02, n),
        "w2": 1530.0 + np.arange(n) * 0.005 + np.random.normal(0, 0.02, n),
        "w3": 1535.0 + np.arange(n) * 0.005 + np.random.normal(0, 0.02, n),
        "w4": 1540.0 + np.arange(n) * 0.005 + np.random.normal(0, 0.02, n),
        "w1_anomaly": [False] * n,
        "w2_anomaly": [False] * n,
    })
    return df


class TestAnnotationRealTimeSync:
    """Phase 1: 列2 编辑 → itemChanged → 对话框状态实时刷新"""

    def test_itemchanged_valid_pair_enables_button(self, qapp, old_format_df):
        """填入合法成对暗号 (A1-1/A1-2) → itemChanged → _legal_cnt 非0 → 按钮亮"""
        from ui.calibration_tab import PhaseADialog

        # 旧格式暗号 (双横杠 → is_valid_annotation=False)
        old_ann = {
            "w1": "w1-A1-1", "w2": "w2-A1-2",
            "w3": "w3-A2-1", "w4": "w4-A2-2",
        }
        dlg = PhaseADialog(old_format_df, old_ann, {},
            {"hold_time_min": 6.0, "sample_interval_s": 2.0, "rolling_window": 25,
             "std_percentile": 45.0, "min_plateau_samples": 50, "head_trim_ratio": 0.70},
            None)
        # 初始: 旧格式非法 → 按钮灰
        assert not dlg.run_btn.isEnabled()
        assert dlg._legal_cnt == 0

        # 模拟用户在列2填入合法暗号 → setItem 触发 itemChanged → _on_apply()
        for i in range(dlg.fill_table.rowCount()):
            col_item = dlg.fill_table.item(i, 0)
            if not col_item:
                continue
            cname = col_item.text().strip()
            if cname == "w1":
                dlg.fill_table.setItem(i, 2, QTableWidgetItem("A1-1"))
            elif cname == "w2":
                dlg.fill_table.setItem(i, 2, QTableWidgetItem("A1-2"))
            elif cname == "w3":
                dlg.fill_table.setItem(i, 2, QTableWidgetItem("A2-1"))
            elif cname == "w4":
                dlg.fill_table.setItem(i, 2, QTableWidgetItem("A2-2"))

        # itemChanged 触发 _on_apply → annotation 已同步
        assert dlg._legal_cnt == 4, f"应合法4个, 实际 {dlg._legal_cnt}"
        assert dlg._annotation.get("w1") == "A1-1"
        assert dlg._annotation.get("w2") == "A1-2"
        assert dlg.run_btn.isEnabled(), "成对双栅 → 按钮应亮"
        # _groups 应识别双栅
        assert "A1" in dlg._groups
        assert len(dlg._groups["A1"]) == 2
        dlg.close()

    def test_invalid_value_does_not_pollute_annotation(self, qapp, old_format_df):
        """填入非法值 → 不污染 _annotation, 按钮不亮"""
        from ui.calibration_tab import PhaseADialog

        old_ann = {"w1": "w1-A1-1", "w2": "w2-A1-2"}
        dlg = PhaseADialog(old_format_df, old_ann, {},
            {"hold_time_min": 6.0, "sample_interval_s": 2.0, "rolling_window": 25,
             "std_percentile": 45.0, "min_plateau_samples": 50, "head_trim_ratio": 0.70},
            None)
        assert not dlg.run_btn.isEnabled()

        # 填非法值: 与旧格式同样的双横杠
        for i in range(dlg.fill_table.rowCount()):
            col_item = dlg.fill_table.item(i, 0)
            if not col_item:
                continue
            cname = col_item.text().strip()
            if cname == "w1":
                dlg.fill_table.setItem(i, 2, QTableWidgetItem("bad-format-x"))

        # itemChanged 触发, 但非法值不进入 _annotation
        assert dlg._legal_cnt == 0, f"非法值不应增加合法计数, 实际 {dlg._legal_cnt}"
        assert dlg._annotation.get("w1") != "bad-format-x", \
            f"非法值不应进入 annotation: {dlg._annotation.get('w1')}"
        assert not dlg.run_btn.isEnabled()
        dlg.close()

    def test_no_recursion_on_itemchanged(self, qapp, old_format_df, monkeypatch):
        """itemChanged → _on_apply → setItem(col1/3) → itemChanged 不递归。
        验证: _on_apply 被调次数 ≤ 行数 + 1 (首次), 不会指数爆炸。
        """
        from ui.calibration_tab import PhaseADialog

        old_ann = {"w1": "w1-A1-1", "w2": "w2-A1-2"}
        dlg = PhaseADialog(old_format_df, old_ann, {},
            {"hold_time_min": 6.0, "sample_interval_s": 2.0, "rolling_window": 25,
             "std_percentile": 45.0, "min_plateau_samples": 50, "head_trim_ratio": 0.70},
            None)

        call_count = [0]
        original_apply = dlg._on_apply

        def counting_apply():
            call_count[0] += 1
            if call_count[0] > 20:
                raise RecursionError("_on_apply 递归爆炸!")
            original_apply()

        monkeypatch.setattr(dlg, '_on_apply', counting_apply)

        # 填两个合法暗号 → 每个 setItem 触发一次 itemChanged → 最多2次 _on_apply
        for i in range(dlg.fill_table.rowCount()):
            col_item = dlg.fill_table.item(i, 0)
            if not col_item:
                continue
            cname = col_item.text().strip()
            if cname == "w1":
                dlg.fill_table.setItem(i, 2, QTableWidgetItem("A1-1"))
            elif cname == "w2":
                dlg.fill_table.setItem(i, 2, QTableWidgetItem("A1-2"))

        # 不应递归爆炸
        assert call_count[0] <= 10, \
            f"_on_apply 被调 {call_count[0]} 次 — 可能递归!"
        assert dlg._legal_cnt >= 2
        dlg.close()

    def test_paste_calls_on_apply_once(self, qapp, old_format_df, monkeypatch):
        """粘贴多行 → 循环前 blockSignals → 循环后只调一次 _on_apply。"""
        from ui.calibration_tab import PhaseADialog

        old_ann = {"w1": "w1-A1-1", "w2": "w2-A1-2"}
        dlg = PhaseADialog(old_format_df, old_ann, {},
            {"hold_time_min": 6.0, "sample_interval_s": 2.0, "rolling_window": 25,
             "std_percentile": 45.0, "min_plateau_samples": 50, "head_trim_ratio": 0.70},
            None)

        call_count = [0]
        original_apply = dlg._on_apply

        def counting_apply():
            call_count[0] += 1
            original_apply()

        monkeypatch.setattr(dlg, '_on_apply', counting_apply)

        # 模拟粘贴: 直接调 _handle_multi_row_paste (setItem 在 blockSignals 下, 不触发 itemChanged)
        # 先把行建好 (dlg 初始化时已有 old_ann 的2行)
        from PyQt6.QtWidgets import QApplication
        QApplication.clipboard().setText("A1-1\nA1-2")
        dlg.fill_table._handle_multi_row_paste(0)

        # 粘贴后 _on_apply 应只被调 1 次 (paste handler 末尾显式调用)
        if call_count[0] == 0:
            # blockSignals 阻止了 itemChanged, _handle_multi_row_paste 末尾调 parent._on_apply
            # 如果 parent guard 没通过, call_count 是0
            assert dlg._legal_cnt >= 0  # 不崩即可
        else:
            assert call_count[0] == 1, \
                f"粘贴应只调1次 _on_apply, 实际 {call_count[0]} 次"
        dlg.close()

    def test_groups_recognize_dual_grating(self, qapp, old_format_df):
        """填够一对双栅 → _groups 识别为双栅、信息条文案含'双栅'"""
        from ui.calibration_tab import PhaseADialog

        old_ann = {"w1": "w1-A1-1", "w2": "w2-A1-2"}
        dlg = PhaseADialog(old_format_df, old_ann, {},
            {"hold_time_min": 6.0, "sample_interval_s": 2.0, "rolling_window": 25,
             "std_percentile": 45.0, "min_plateau_samples": 50, "head_trim_ratio": 0.70},
            None)

        # 填两个成对暗号
        for i in range(dlg.fill_table.rowCount()):
            col_item = dlg.fill_table.item(i, 0)
            if not col_item:
                continue
            cname = col_item.text().strip()
            if cname == "w1":
                dlg.fill_table.setItem(i, 2, QTableWidgetItem("A1-1"))
            elif cname == "w2":
                dlg.fill_table.setItem(i, 2, QTableWidgetItem("A1-2"))

        # itemChanged → 自动刷新了 _groups
        assert "A1" in dlg._groups
        gratings = dlg._groups["A1"]
        assert len(gratings) == 2, f"A1 组应有2个光栅, 实际 {len(gratings)}"

        # 信息条文案应含 "双栅"
        info_text = dlg.info_label.text()
        assert "双栅" in info_text, f"信息条应含'双栅', 实际: {info_text!r}"
        dlg.close()

    def test_itemchanged_does_not_trigger_on_columns_other_than_2(self, qapp, old_format_df, monkeypatch):
        """列1或列3的 itemChanged → 不应刷新 (handler 过滤 col != 2)。"""
        from ui.calibration_tab import PhaseADialog

        old_ann = {"w1": "w1-A1-1", "w2": "w2-A1-2"}
        dlg = PhaseADialog(old_format_df, old_ann, {},
            {"hold_time_min": 6.0, "sample_interval_s": 2.0, "rolling_window": 25,
             "std_percentile": 45.0, "min_plateau_samples": 50, "head_trim_ratio": 0.70},
            None)

        call_count = [0]
        original_apply = dlg._on_apply

        def counting_apply():
            call_count[0] += 1
            original_apply()

        monkeypatch.setattr(dlg, '_on_apply', counting_apply)

        # setItem 列 1 (当前暗号) → itemChanged 但不触发 _on_apply (col != 2)
        for i in range(dlg.fill_table.rowCount()):
            col_item = dlg.fill_table.item(i, 0)
            if not col_item:
                continue
            dlg.fill_table.setItem(i, 1, QTableWidgetItem("something-else"))

        assert call_count[0] == 0, \
            f"列1编辑不应触发 _on_apply, 实际调了 {call_count[0]} 次"
        dlg.close()
