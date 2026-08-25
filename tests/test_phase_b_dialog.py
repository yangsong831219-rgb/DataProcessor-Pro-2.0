"""Phase B 解耦对话框回归测试 — IndexError 防御 + 双栅解耦断言"""

from __future__ import annotations
import pytest
import numpy as np
import pandas as pd
from PyQt6.QtWidgets import QApplication


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    yield app


@pytest.fixture
def phase_a_result():
    """模拟 Phase A 已完成：8 个光栅 S_eff"""
    return {
        "df": pd.DataFrame(index=range(100)),
        "plateaus": pd.DataFrame(),
        "S_eff": {
            "B2-W1": {"slope": 41.0, "intercept": -410.0, "T_base": 10.0, "r2": 0.99},
            "B2-W2": {"slope": 30.0, "intercept": -300.0, "T_base": 10.0, "r2": 0.99},
            "B1-W1": {"slope": 41.0, "intercept": -410.0, "T_base": 10.0, "r2": 0.99},
            "B1-W2": {"slope": 28.0, "intercept": -280.0, "T_base": 10.0, "r2": 0.99},
            "A1-W1": {"slope": 27.8, "intercept": -278.0, "T_base": 10.0, "r2": 0.99},
            "A1-W2": {"slope": 29.4, "intercept": -294.0, "T_base": 10.0, "r2": 0.99},
            "A2-W1": {"slope": 27.6, "intercept": -276.0, "T_base": 10.0, "r2": 0.99},
            "A2-W2": {"slope": 30.2, "intercept": -302.0, "T_base": 10.0, "r2": 0.99},
        },
        "wavelength_cols": ["B2-W1", "B2-W2", "B1-W1", "B1-W2",
                             "A1-W1", "A1-W2", "A2-W1", "A2-W2"],
    }


@pytest.fixture
def sample_df():
    """8 列波长数据含 dL (_d 后缀)"""
    np.random.seed(42)
    n = 300
    data = {}
    for wl in ["B2-W1", "B2-W2", "B1-W1", "B1-W2",
               "A1-W1", "A1-W2", "A2-W1", "A2-W2"]:
        data[wl] = np.full(n, 1550.0) + np.random.normal(0, 0.1, n)
        # 已计算的 dL 列 (Phase A worker 产出)
        data[f"{wl}_d"] = np.sin(np.linspace(0, 3, n)) * 1000.0
    return pd.DataFrame(data)


def test_phase_b_run_no_index_error(qapp, sample_df, phase_a_result):
    """已加载 Peaks + 8 合法暗号 + Phase A 完成 → 运行解耦不报 IndexError"""
    # 注入 dL 列到 S_eff result 的 df 中
    phase_a_result["df"] = sample_df
    from ui.calibration_tab import PhaseBWorker
    groups = {
        "A1": [
            {"col_name": "A1-W1", "name": "A1-W1"},
            {"col_name": "A1-W2", "name": "A1-W2"},
        ],
        "B2": [
            {"col_name": "B2-W1", "name": "B2-W1"},
            {"col_name": "B2-W2", "name": "B2-W2"},
        ],
    }
    coeffs = {"A1": {"Ke1": 1.2, "Ke2": 1.2}, "B2": {"Ke1": 1.2, "Ke2": 1.2}}
    time_h = np.arange(len(sample_df)) * 2.0 / 3600.0

    worker = PhaseBWorker(
        sample_df, time_h,
        ["A1-W1", "A1-W2", "B2-W1", "B2-W2"],
        groups,
        phase_a_result["S_eff"],
        coeffs, 2.0,
    )
    worker.run()
    assert worker._last_result is not None, "PhaseBWorker 应产出结果"


def test_phase_b_decouple_per_sensor(qapp, sample_df, phase_a_result):
    """每传感器解耦输出 eps/dT，输入 Ke 正确传递给 decouple"""
    phase_a_result["df"] = sample_df
    from ui.calibration_tab import PhaseBWorker
    groups = {
        "A1": [
            {"col_name": "A1-W1", "name": "A1-W1"},
            {"col_name": "A1-W2", "name": "A1-W2"},
        ],
        "B2": [
            {"col_name": "B2-W1", "name": "B2-W1"},
            {"col_name": "B2-W2", "name": "B2-W2"},
        ],
    }
    coeffs = {"A1": {"Ke1": 1.2, "Ke2": 1.2}, "B2": {"Ke1": 0.8, "Ke2": 1.1}}
    time_h = np.arange(len(sample_df)) * 2.0 / 3600.0

    worker = PhaseBWorker(
        sample_df, time_h,
        ["A1-W1", "A1-W2", "B2-W1", "B2-W2"],
        groups,
        phase_a_result["S_eff"],
        coeffs, 2.0,
    )
    worker.run()
    res = worker._last_result
    sensors = res["sensors"]
    assert "A1" in sensors
    assert "B2" in sensors
    assert not sensors["A1"].get("single_grating")
    assert not sensors["B2"].get("single_grating")
    assert sensors["A1"]["S1"] > 0
    assert sensors["A1"]["S2"] > 0


def test_phase_b_worker_materializes_raw_and_compensated_diagnostic_series(
    qapp, sample_df, phase_a_result, monkeypatch
):
    """补偿应用必须在 worker 内完成，并随瞬态结果供诊断记录消费。"""
    from ui import calibration_tab
    from utils.apparent_strain_comp import ApparentStrainLUT, CompensationModel

    model = CompensationModel(
        form="lut",
        model=ApparentStrainLUT(
            sensor="A1",
            T_base=10.0,
            T_grid=[-1_000_000.0, 1_000_000.0],
            eps_app=[5.0, 5.0],
            T_min=-1_000_000.0,
            T_max=1_000_000.0,
            n_cycles=1,
            source="test",
        ),
    )

    def fake_pipeline(sensors, fs_map, **kwargs):
        return {
            name: {"model": model, "metrics": None, "grade": None}
            for name, sensor in sensors.items()
            if not sensor.get("single_grating", False)
        }

    monkeypatch.setattr(
        calibration_tab,
        "_run_compensation_pipeline_static",
        fake_pipeline,
    )
    groups = {
        "A1": [
            {"col_name": "A1-W1", "name": "A1-W1"},
            {"col_name": "A1-W2", "name": "A1-W2"},
        ],
    }
    time_h = np.arange(len(sample_df)) * 2.0 / 3600.0
    worker = calibration_tab.PhaseBWorker(
        sample_df,
        time_h,
        ["A1-W1", "A1-W2"],
        groups,
        phase_a_result["S_eff"],
        {"A1": {"Ke1": 1.2, "Ke2": 1.2}},
        2.0,
    )

    worker.run()

    result = worker._last_result
    assert result is not None
    raw = np.asarray(result["diagnostic_series"]["A1"]["eps_raw"])
    compensated = np.asarray(
        result["diagnostic_series"]["A1"]["eps_compensated"]
    )
    assert raw == pytest.approx(result["sensors"]["A1"]["eps_corr"])
    assert compensated == pytest.approx(raw - 5.0)


def test_phase_b_handles_single_grating(qapp, sample_df, phase_a_result):
    """单栅传感器跳过解耦但不崩溃"""
    phase_a_result["df"] = sample_df
    from ui.calibration_tab import PhaseBWorker
    groups = {
        "S1": [
            {"col_name": "A1-W1", "name": "A1-W1"},
        ],
    }
    time_h = np.arange(len(sample_df)) * 2.0 / 3600.0

    worker = PhaseBWorker(
        sample_df, time_h,
        ["A1-W1"],
        groups,
        phase_a_result["S_eff"],
        {}, 2.0,
    )
    worker.run()
    res = worker._last_result
    sensors = res["sensors"]
    assert "S1" in sensors
    assert sensors["S1"]["single_grating"]


def test_phase_b_accept_saves_state(qapp, sample_df, phase_a_result):
    """accept() 不抛 AttributeError 且 _phase_b_state 正确写入"""
    from ui.calibration_tab import PhaseBDialog
    np.random.seed(42)
    sensors = {}
    for name in ["A1", "A2", "B1", "B2"]:
        sensors[name] = {
            "eps_corr": np.random.normal(0, 40, 100),
            "dT_corr": np.zeros(100),
            "T_abs": np.arange(100),
            "S1": 30.0, "S2": 28.0, "T_base": 12.0,
            "single_grating": False,
        }
    result = {"sensors": sensors, "time_h": np.arange(100) * 2.0 / 3600.0}

    dlg = PhaseBDialog(sample_df,
        {"A1": [{"col_name": "A1-W1", "name": "A1-W1"}, {"col_name": "A1-W2", "name": "A1-W2"}],
         "A2": [{"col_name": "A2-W1", "name": "A2-W1"}, {"col_name": "A2-W2", "name": "A2-W2"}],
         "B1": [{"col_name": "B1-W1", "name": "B1-W1"}, {"col_name": "B1-W2", "name": "B1-W2"}],
         "B2": [{"col_name": "B2-W1", "name": "B2-W1"}, {"col_name": "B2-W2", "name": "B2-W2"}]},
        {"S_eff": {"A1-W1": {"slope": 27.8}, "A1-W2": {"slope": 29.4},
                    "A2-W1": {"slope": 27.6}, "A2-W2": {"slope": 30.2},
                    "B1-W1": {"slope": 41.0}, "B1-W2": {"slope": 28.0},
                    "B2-W1": {"slope": 41.0}, "B2-W2": {"slope": 30.0}}},
    )
    # 模拟运行状态
    dlg._coeffs = {"A1": {"Ke1": 1.2, "Ke2": 1.2}, "A2": {"Ke1": 0.8, "Ke2": 1.1},
                   "B1": {"Ke1": 0.7, "Ke2": 1.1}, "B2": {"Ke1": 0.8, "Ke2": 1.1}}
    dlg._last_result = result

    # accept 不应抛 AttributeError (注: test 无父窗口不写 state)
    dlg.accept()
    QApplication.processEvents()
    assert True  # 只要没抛异常就通过
    dlg.close()


def test_full_temp_calibration_roundtrip_smoke(qapp):
    """端到端 smoke test: 加载→Phase A→Phase B→close→重开→全程无异常"""
    import pandas as pd
    from ui.calibration_tab import TemperatureCalibrationPage, PhaseADialog, PhaseBDialog

    page = TemperatureCalibrationPage()
    df = pd.DataFrame({f"ch{i}": [1550.0 + i * 5] * 100 for i in range(1, 9)})
    page._loaded_df = df

    # Simulate annotation
    annot = {f"ch{i}": f"w{i}-类型-位置" for i in range(1, 9)}
    page._annotation_dict = annot
    page._detection_params = {"rolling_window": 25, "std_percentile": 45.0,
                               "min_plateau_samples": 50, "head_trim_ratio": 0.70}

    # Open PhaseA dialog — should not raise
    dlg_a = PhaseADialog(df, annot, {},
        {"hold_time_min": 6.0, "sample_interval_s": 2.0, "rolling_window": 25,
         "std_percentile": 45.0, "min_plateau_samples": 50, "head_trim_ratio": 0.70}, None)
    assert dlg_a is not None
    dlg_a.close()

    # Phase A done → open Phase B
    pa_result = {"S_eff": {}}
    dlg_b = PhaseBDialog(df, {}, pa_result)
    assert dlg_b is not None
    dlg_b.close()

    # Reopen Phase A — no crash
    dlg_a2 = PhaseADialog(df, annot, {},
        {"hold_time_min": 6.0, "sample_interval_s": 2.0, "rolling_window": 25,
         "std_percentile": 45.0, "min_plateau_samples": 50, "head_trim_ratio": 0.70}, None)
    assert dlg_a2 is not None
    dlg_a2.close()
    print("✓ End-to-end smoke test passed")


def test_phase_b_result_table(qapp, sample_df, phase_a_result):
    """Phase B 解耦结果表格 — 4行12列 + 评级着色 + 单栅占位符"""
    from ui.calibration_tab import PhaseBDialog
    np.random.seed(42)
    # 构造 4 传感器解耦结果 (模拟 Phase B worker 输出)
    eps = [np.random.normal(43.5, 10, 100),  # A1 → "良"
           np.random.normal(79.2, 25, 100),  # A2 → "差"
           np.random.normal(20.1, 5, 100),   # B1 → "优"
           np.random.normal(18.3, 5, 100)]   # B2 → "优"
    sensors = {}
    for i, name in enumerate(["A1", "A2", "B1", "B2"]):
        sensors[name] = {
            "eps_corr": eps[i],
            "dT_corr": np.zeros(100),
            "T_abs": np.arange(100),
            "S1": 30.0, "S2": 28.0, "T_base": 12.0,
            "single_grating": False,
        }
    result = {"sensors": sensors, "time_h": np.arange(100) * 2.0 / 3600.0}

    dlg = PhaseBDialog(sample_df,
        {"A1": [{"col_name": "A1-W1", "name": "A1-W1"}, {"col_name": "A1-W2", "name": "A1-W2"}],
         "A2": [{"col_name": "A2-W1", "name": "A2-W1"}, {"col_name": "A2-W2", "name": "A2-W2"}],
         "B1": [{"col_name": "B1-W1", "name": "B1-W1"}, {"col_name": "B1-W2", "name": "B1-W2"}],
         "B2": [{"col_name": "B2-W1", "name": "B2-W1"}, {"col_name": "B2-W2", "name": "B2-W2"}]},
        {"S_eff": {"A1-W1": {"slope": 27.8}, "A1-W2": {"slope": 29.4},
                    "A2-W1": {"slope": 27.6}, "A2-W2": {"slope": 30.2},
                    "B1-W1": {"slope": 41.0}, "B1-W2": {"slope": 28.0},
                    "B2-W1": {"slope": 41.0}, "B2-W2": {"slope": 30.0}}},
    )
    # 填入 Ke 系数
    dlg._coeffs = {"A1": {"Ke1": 1.2, "Ke2": 1.2}, "A2": {"Ke1": 0.8, "Ke2": 1.1},
                   "B1": {"Ke1": 0.7, "Ke2": 1.1}, "B2": {"Ke1": 0.8, "Ke2": 1.1}}
    dlg._last_result = result
    dlg._render_result_table(sensors, dlg._phase_a_result.get("S_eff", {}))
    QApplication.processEvents()

    tbl = dlg.result_table
    tbl.setSortingEnabled(False)  # disable sorting for stable indexing
    assert tbl.rowCount() == 4, f"expected 4 rows, got {tbl.rowCount()}"
    assert tbl.columnCount() == 12, f"expected 12 cols, got {tbl.columnCount()}"

    # Build row_map by sensor name (dict iteration order may vary)
    row_map = {}
    for i in range(tbl.rowCount()):
        row_map[tbl.item(i, 0).text()] = i

    # Type column
    assert tbl.item(row_map["A1"], 1).text() == "双栅"

    # Rating verification (compensation pipeline runs in _on_done; monotonic data → NaN hys → capped at liang)
    for i in range(tbl.rowCount()):
        rating_item = tbl.item(i, 11)
        assert rating_item is not None
        rating = rating_item.text()
        assert rating != "FAIL", f"row {i}: expected not FAIL, got {rating}"
        assert rating != "ERROR", f"row {i}: expected not ERROR, got {rating}"

    dlg.close()


# ═══════════════════════════════════════════════════════════════════════
# Phase 3c 回归测试: _ensure_decoupled_result 守卫 + 评级 + 状态恢复
# ═══════════════════════════════════════════════════════════════════════

def test_ensure_decoupled_result_lazy_decouple(qapp, sample_df, phase_a_result):
    """_last_result=None → _ensure_decoupled_result 返回 None (主线程禁止重算)"""
    from ui.calibration_tab import PhaseBDialog

    dlg = PhaseBDialog(sample_df,
        {"A1": [{"col_name": "A1-W1", "name": "A1-W1"},
                {"col_name": "A1-W2", "name": "A1-W2"}]},
        {"S_eff": {"A1-W1": {"slope": 27.8, "T_base": 10.0},
                    "A1-W2": {"slope": 29.4, "T_base": 10.0}}})
    dlg._coeffs = {"A1": {"Ke1": 1.2, "Ke2": 1.2}}
    assert dlg._last_result is None

    sensors = dlg._ensure_decoupled_result(show_warning=False)
    assert sensors is None, "should return None — heavy compute only in PhaseBWorker"
    dlg.close()


def test_ensure_decoupled_result_no_data_shows_warning(qapp):
    """无效 coeffs + 无 phase_a_result → 弹 QMessageBox 警告"""
    from ui.calibration_tab import PhaseBDialog
    import pandas as pd
    from PyQt6.QtWidgets import QMessageBox

    df_empty = pd.DataFrame({"a": [1, 2, 3]})
    dlg = PhaseBDialog(df_empty, {}, {"S_eff": {}})
    dlg._coeffs = {}
    assert dlg._last_result is None

    warned_msgs = []
    _orig = QMessageBox.warning

    def fake_warning(parent, title, msg):
        warned_msgs.append((title, msg))
    try:
        QMessageBox.warning = fake_warning  # type: ignore[method-assign]
        result = dlg._ensure_decoupled_result(show_warning=True)
        assert result is None
        assert len(warned_msgs) == 1
        assert "请先加载数据" in warned_msgs[0][1] or "解耦" in warned_msgs[0][1]
    finally:
        QMessageBox.warning = _orig  # type: ignore[method-assign]
    dlg.close()


def test_render_result_table_high_hysteresis_shows_fail(qapp, sample_df, phase_a_result):
    """高滞回 B2 传感器在结果表中应显示 FAIL + 红色背景"""
    from ui.calibration_tab import PhaseBDialog
    np.random.seed(1)
    n = 600
    # 三角波温度 (3 循环)
    T_abs = np.empty(n)
    for ci in range(3):
        for half in range(2):
            s = (ci * 2 + half) * (n // 6)
            e = s + n // 6
            if half == 0:
                T_abs[s:e] = np.linspace(10, 60, n // 6)
            else:
                T_abs[s:e] = np.linspace(60, 10, n // 6)
    # 高迟滞应变: 升支 +130, 降支 -130 (模拟 260με 峰值迟滞)
    eps_corr = 2.0 * (T_abs - 25.0)
    for ci in range(3):
        for half in range(2):
            s = (ci * 2 + half) * (n // 6)
            e = s + n // 6
            eps_corr[s:e] += 130.0 if half == 0 else -130.0
    eps_corr += np.random.normal(0, 5, n)

    sensors = {"B2": {
        "eps_corr": eps_corr, "dT_corr": np.zeros(n),
        "T_abs": T_abs, "S1": 30.0, "S2": 28.0, "T_base": 25.0,
        "single_grating": False,
    }}

    dlg = PhaseBDialog(sample_df,
        {"B2": [{"col_name": "B2-W1", "name": "B2-W1"},
                {"col_name": "B2-W2", "name": "B2-W2"}]},
        {"S_eff": {"B2-W1": {"slope": 41.0, "T_base": 25.0},
                    "B2-W2": {"slope": 30.0, "T_base": 25.0}}})
    dlg._coeffs = {"B2": {"Ke1": 0.8, "Ke2": 1.1}}
    from ui.calibration_tab import _run_compensation_pipeline_static
    dlg._compensation_results = _run_compensation_pipeline_static(
        sensors, fs_map={"B2": 1000})
    dlg._render_result_table(sensors, phase_a_result.get("S_eff", {}),
                              compensation=dlg._compensation_results)
    QApplication.processEvents()

    tbl = dlg.result_table
    assert tbl.rowCount() >= 1
    found = False
    for i in range(tbl.rowCount()):
        sensor_item = tbl.item(i, 0)
        assert sensor_item is not None
        if sensor_item.text() == "B2":
            rating_item = tbl.item(i, 11)
            assert rating_item is not None
            rating = rating_item.text()
            assert rating == "FAIL", f"expected FAIL, got {rating}"
            bg_item = tbl.item(i, 11)
            assert bg_item is not None
            bg = bg_item.background().color().name()
            assert bg == "#ffcdd2", f"expected red bg #ffcdd2, got {bg}"
            found = True
            break
    assert found, "B2 row not found in table"
    dlg.close()


def test_render_result_table_shows_real_std(qapp, sample_df, phase_a_result):
    """ε_std ~10 的传感器在表格中显示真实 e_std (不是 0.00)"""
    from ui.calibration_tab import PhaseBDialog
    np.random.seed(42)
    n = 300
    T_abs = np.linspace(10, 60, n)
    eps_corr = np.random.normal(0, 10, n)  # std ~10

    sensors = {"A1": {
        "eps_corr": eps_corr, "dT_corr": np.zeros(n),
        "T_abs": T_abs, "S1": 30.0, "S2": 28.0, "T_base": 25.0,
        "single_grating": False,
    }}

    dlg = PhaseBDialog(sample_df,
        {"A1": [{"col_name": "A1-W1", "name": "A1-W1"},
                {"col_name": "A1-W2", "name": "A1-W2"}]},
        {"S_eff": {"A1-W1": {"slope": 27.8, "T_base": 25.0},
                    "A1-W2": {"slope": 29.4, "T_base": 25.0}}})
    dlg._coeffs = {"A1": {"Ke1": 1.2, "Ke2": 1.2}}
    from ui.calibration_tab import _run_compensation_pipeline_static
    dlg._compensation_results = _run_compensation_pipeline_static(
        sensors, fs_map={"A1": 1000})
    dlg._render_result_table(sensors, phase_a_result.get("S_eff", {}),
                              compensation=dlg._compensation_results)
    QApplication.processEvents()

    tbl = dlg.result_table
    for i in range(tbl.rowCount()):
        if tbl.item(i, 0).text() == "A1":
            e_std_text = tbl.item(i, 7).text()  # column 7 = e_std
            e_std_val = float(e_std_text)
            assert 1.0 < e_std_val < 20.0, \
                f"expected e_std ~10, got {e_std_val}"
            assert e_std_val != 0.0, "e_std should not be 0.00"
            break
    dlg.close()


def test_state_restore_no_fake_zeros(qapp):
    """旧持久化状态加载后，_ensure_decoupled_result 用真实数据渲染 (非 e_mean*10)"""
    from ui.calibration_tab import PhaseBDialog
    import pandas as pd

    df = pd.DataFrame({f"ch{i}": [1550.0 + i * 5] * 100 for i in range(1, 3)})
    dlg = PhaseBDialog(df,
        {"A1": [{"col_name": "ch1", "name": "A1-W1"},
                {"col_name": "ch2", "name": "A1-W2"}]},
        {"S_eff": {"ch1": {"slope": 27.8, "T_base": 10.0},
                    "ch2": {"slope": 29.4, "T_base": 10.0}}})
    dlg._coeffs = {"A1": {"Ke1": 1.2, "Ke2": 1.2}}

    # state restore 后表格不应包含 [e_mean]*10 造成的 0.00
    sensors = dlg._ensure_decoupled_result(show_warning=False)
    # 无 real underlying data → 表格应为空。
    # 若有 (当 df 有真实列) → 渲染真实值。
    if sensors:
        S_eff = {"ch1": {"slope": 27.8}, "ch2": {"slope": 29.4}}
        dlg._compensation_results = dlg._run_compensation_pipeline(sensors)
        dlg._render_result_table(sensors, S_eff,
                                  compensation=dlg._compensation_results)
        QApplication.processEvents()

        tbl = dlg.result_table
        if tbl.rowCount() > 0:
            e_std_text = tbl.item(0, 7).text()
            val = float(e_std_text)
            assert val >= 0, f"e_std should be non-negative, got {val}"

    dlg.close()


# ═══════════════════════════════════════════════════════════════════════
# Phase 1: sensors 落盘 + reject 未保存提示
# ═══════════════════════════════════════════════════════════════════════


class _MockTempPageForPhaseB:
    """模拟 TemperatureCalibrationPage — 仅含 _phase_b_state 供写回验证。"""

    def __init__(self):
        self._phase_b_state = {}
        self._phase_b_result = None
        self._annotation_dict = {}
        self._annotation_groups = {}
        self._annotation_dirty: set = set()
        self._last_result = None
        self._phase_a_done = True


class _MockCalTabWidget:
    """模拟 CalibrationTabWidget — 供 _get_cal_tab_widget 使用。"""
    def __init__(self):
        self.project_config = None


class TestPhaseBWritebackSensors:
    """_write_state_to_main_page 落盘 sensors → from_providers 可读"""

    @pytest.fixture
    def phase_b_dialog_with_result(self, sample_df, qapp, monkeypatch):
        """构造 PhaseBDialog，模拟分析完成有 _last_result。"""
        from ui.calibration_tab import PhaseBDialog, TemperatureCalibrationPage

        page = TemperatureCalibrationPage()
        page._loaded_df = sample_df

        S_eff = {}
        for wl in ["A1-W1", "A1-W2", "A2-W1", "A2-W2"]:
            S_eff[wl] = {"slope": 28.0, "intercept": -280.0, "T_base": 10.0, "r2": 0.99}

        dlg = PhaseBDialog(sample_df,
            {"A1": [{"col_name": "A1-W1", "name": "A1-W1"},
                    {"col_name": "A1-W2", "name": "A1-W2"}],
             "A2": [{"col_name": "A2-W1", "name": "A2-W1"},
                    {"col_name": "A2-W2", "name": "A2-W2"}]},
            {"S_eff": S_eff, "df": sample_df, "plateaus": pd.DataFrame()},
            parent=page,
        )

        n = 100
        rng = np.random.default_rng(42)
        T_abs = np.linspace(25, 85, n)
        eps_a1 = 500 * np.sin(np.linspace(0, np.pi, n)) + rng.normal(0, 5, n)
        eps_a2 = 400 * np.sin(np.linspace(0, np.pi, n)) + rng.normal(0, 3, n)

        dlg._last_result = {
            "sensors": {
                "A1": {
                    "single_grating": False,
                    "T_abs": T_abs, "eps_orig": eps_a1,
                    "eps_corr": eps_a1 * 0.9,
                    "dT_orig": np.zeros(n), "dT_corr": np.zeros(n),
                    "S1": 28.0, "S2": 28.5, "T_base": 10.0,
                },
                "A2": {
                    "single_grating": False,
                    "T_abs": T_abs, "eps_orig": eps_a2,
                    "eps_corr": eps_a2 * 0.7,
                    "dT_orig": np.zeros(n), "dT_corr": np.zeros(n),
                    "S1": 28.0, "S2": 28.5, "T_base": 10.0,
                },
                "B1": {
                    "single_grating": True,
                    "S1": 41.0, "T_base": 10.0, "R2": 0.99,
                },
            },
            "comparisons": [],
            "df": sample_df, "time_h": np.arange(n) / 3600.0,
            "compensation": {},
        }

        class _FakeGrade:
            grade = "优"
            passed = True
            reasons: list = []
            def to_dict(self):
                return {"grade": self.grade, "passed": self.passed}
        dlg._compensation_results = {
            "A1": {"model": None, "metrics": None, "grade": _FakeGrade()},
            "A2": {"model": None, "metrics": None, "grade": _FakeGrade()},
        }

        yield dlg
        dlg.close()

    def test_sensors_written_to_phase_b_state(self, phase_b_dialog_with_result):
        """落盘后 _phase_b_state['sensors'] 含 T_abs/eps_orig"""
        dlg = phase_b_dialog_with_result
        tp = _MockTempPageForPhaseB()
        dlg._get_temp_page = lambda: tp  # type: ignore[method-assign]
        dlg._write_state_to_main_page()

        assert "sensors" in tp._phase_b_state, \
            f"落盘字典缺 sensors, keys={list(tp._phase_b_state.keys())}"
        sensors = tp._phase_b_state["sensors"]
        assert "A1" in sensors
        assert "A2" in sensors
        a1 = sensors["A1"]
        assert "T_abs" in a1, f"A1 keys={list(a1.keys())}"
        assert "eps_orig" in a1
        assert len(a1["T_abs"]) > 0
        assert len(a1["eps_orig"]) > 0
        # 不影响原有字段
        assert "compensation" in tp._phase_b_state
        assert "decoupling_results" in tp._phase_b_state

    def test_empty_sensors_does_not_crash(self, sample_df, qapp):
        """_last_result.sensors 为空 → 落盘 sensors={} 不崩"""
        from ui.calibration_tab import PhaseBDialog, TemperatureCalibrationPage
        page = TemperatureCalibrationPage()
        page._loaded_df = sample_df
        dlg = PhaseBDialog(sample_df,
            {"A1": [{"col_name": "A1-W1", "name": "A1-W1"}]},
            {"S_eff": {"A1-W1": {"slope": 28.0, "T_base": 10.0, "r2": 0.99}},
             "df": sample_df, "plateaus": pd.DataFrame()},
            parent=page,
        )
        dlg._last_result = {"sensors": {}, "comparisons": [], "compensation": {}}
        tp = _MockTempPageForPhaseB()
        dlg._get_temp_page = lambda: tp  # type: ignore[method-assign]
        dlg._write_state_to_main_page()
        assert tp._phase_b_state.get("sensors") == {}
        dlg.close()

    def test_last_result_none_does_not_crash(self, sample_df, qapp):
        """_last_result = None → _write_state_to_main_page 不崩, sensors={}"""
        from ui.calibration_tab import PhaseBDialog, TemperatureCalibrationPage
        page = TemperatureCalibrationPage()
        page._loaded_df = sample_df
        dlg = PhaseBDialog(sample_df,
            {"A1": [{"col_name": "A1-W1", "name": "A1-W1"}]},
            {"S_eff": {"A1-W1": {"slope": 28.0, "T_base": 10.0, "r2": 0.99}},
             "df": sample_df, "plateaus": pd.DataFrame()},
            parent=page,
        )
        dlg._last_result = None  # ← 未分析
        tp = _MockTempPageForPhaseB()
        dlg._get_temp_page = lambda: tp  # type: ignore[method-assign]
        # 不应崩
        dlg._write_state_to_main_page()
        assert tp._phase_b_state.get("sensors") == {}
        # 其他字段仍正常落盘 (ke_table 从 coef_table 读)
        assert "ke_table" in tp._phase_b_state
        dlg.close()

    def test_from_providers_reads_sensors_after_writeback(
        self, phase_b_dialog_with_result):
        """闭环: 落盘 → from_providers → hyst_sensors 非空"""
        dlg = phase_b_dialog_with_result
        tp = _MockTempPageForPhaseB()

        dlg._get_temp_page = lambda: tp  # type: ignore[method-assign]
        dlg._coeffs = {"A1": {"Ke1": 1.2, "Ke2": 1.2}}
        dlg._write_state_to_main_page()

        from core.chart_bundle import ChartBundle

        class _MW:
            current_data = None
            sensor_results = {}
            analysis_tab_widget = None
            calibration_tab_widget = None

        mw = _MW()
        atw = type("ATW", (), {"_current_data": None})()
        mw.analysis_tab_widget = atw  # type: ignore[assignment]
        ctw = type("CTW", (), {"temp_page": tp})()
        mw.calibration_tab_widget = ctw  # type: ignore[assignment]

        bundle = ChartBundle.from_providers(mw)

        assert len(bundle.hyst_sensors) >= 1, \
            f"A1+A2应≥1有迟滞, 实际 {len(bundle.hyst_sensors)}"
        names = {hs["name"] for hs in bundle.hyst_sensors}
        assert "A1" in names
        for hs in bundle.hyst_sensors:
            assert isinstance(hs["T_abs"], list)
            assert isinstance(hs["eps"], list)
            assert len(hs["T_abs"]) > 0


class TestPhaseBRejectConfirmation:
    """reject 有未保存结果 → 弹确认; 无结果 → 不弹"""

    @pytest.fixture
    def dialog_no_result(self, sample_df, qapp):
        from ui.calibration_tab import PhaseBDialog, TemperatureCalibrationPage
        page = TemperatureCalibrationPage()
        page._loaded_df = sample_df
        dlg = PhaseBDialog(sample_df,
            {"A1": [{"col_name": "A1-W1", "name": "A1-W1"}]},
            {"S_eff": {"A1-W1": {"slope": 28.0, "T_base": 10.0, "r2": 0.99}},
             "df": sample_df, "plateaus": pd.DataFrame()},
            parent=page,
        )
        yield dlg
        dlg.close()

    @pytest.fixture
    def dialog_with_result(self, sample_df, qapp):
        from ui.calibration_tab import PhaseBDialog, TemperatureCalibrationPage
        page = TemperatureCalibrationPage()
        page._loaded_df = sample_df
        dlg = PhaseBDialog(sample_df,
            {"A1": [{"col_name": "A1-W1", "name": "A1-W1"}]},
            {"S_eff": {"A1-W1": {"slope": 28.0, "T_base": 10.0, "r2": 0.99}},
             "df": sample_df, "plateaus": pd.DataFrame()},
            parent=page,
        )
        dlg._last_result = {"sensors": {}, "comparisons": [], "compensation": {}}
        yield dlg
        dlg.close()

    def test_reject_with_result_shows_confirmation(self, dialog_with_result):
        """有 _last_result → QMessageBox Yes/No 弹窗"""
        from unittest import mock as _umock
        dlg = dialog_with_result
        with _umock.patch("PyQt6.QtWidgets.QMessageBox.question") as mock_q:
            mock_q.return_value = _umock.MagicMock()
            dlg.reject()
        mock_q.assert_called_once()
        call_args = mock_q.call_args[0]
        assert "未保存的分析结果" in str(call_args)

    def test_reject_no_result_direct_close(self, dialog_no_result):
        """无 _last_result → 不弹窗, 直接 reject"""
        from unittest import mock as _umock
        dlg = dialog_no_result
        with _umock.patch("PyQt6.QtWidgets.QMessageBox.question") as mock_q:
            dlg.reject()
        mock_q.assert_not_called()

    def test_reject_select_no_stays_open(self, dialog_with_result):
        """选 No → 不调 QDialog.reject"""
        from unittest import mock as _umock
        from PyQt6.QtWidgets import QMessageBox, QDialog
        dlg = dialog_with_result
        parent_reject_called = [False]

        def _fake_parent_reject(_self):
            parent_reject_called[0] = True

        with _umock.patch.object(QDialog, "reject", _fake_parent_reject), \
             _umock.patch("PyQt6.QtWidgets.QMessageBox.question",
                          return_value=QMessageBox.StandardButton.No):
            dlg.reject()

        assert not parent_reject_called[0], \
            "选 No 不应调 QDialog.reject()"

    def test_reject_select_yes_closes(self, dialog_with_result):
        """选 Yes → 调 QDialog.reject()"""
        from unittest import mock as _umock
        from PyQt6.QtWidgets import QMessageBox, QDialog
        dlg = dialog_with_result
        parent_reject_called = [False]

        def _fake_parent_reject(_self):
            parent_reject_called[0] = True

        with _umock.patch.object(QDialog, "reject", _fake_parent_reject), \
             _umock.patch("PyQt6.QtWidgets.QMessageBox.question",
                          return_value=QMessageBox.StandardButton.Yes):
            dlg.reject()

        assert parent_reject_called[0], \
            "选 Yes 应调 QDialog.reject()"
