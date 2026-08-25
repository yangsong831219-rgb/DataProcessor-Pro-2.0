"""core/chart_bundle.py ChartBundle.from_providers 合成数据测试

覆盖:
- AnalysisProvider: _current_data(非current_data) → series/time_h 非空
- sensor_results 取 main_win → metrics/dist 非空
- 应变标定 linearity → calib_ref/calib_measured 填值
- hyst/compare 无数据 → skip 不崩
- 多图场景: made 含 ts_cleaning/dist_box/metrics_bar/grade_bar/calib_lin
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import numpy as np
import pandas as pd
import pytest

from core.chart_bundle import ChartBundle


# ═══════════════════════════════════════════════════════════════════════════
# Mock 对象 — 模拟主窗口/各 Tab 的真实属性结构
# ═══════════════════════════════════════════════════════════════════════════


class MockAnalysisTabWidget:
    """模拟 AnalysisTabWidget — 只暴露真实存在的私有属性。"""

    def __init__(self, df: pd.DataFrame | None = None, sensor_results: dict | None = None):
        # ★ 关键: 真实属性名是 _current_data (私有), 不是 current_data
        self._current_data: pd.DataFrame | None = df
        self._sensor_results: dict = sensor_results or {}
        # 以下属性在真 AnalysisTabWidget 上存在但 from_providers 不读
        self._annotated_cols = None
        self._sensor_system = None
        self._annotation_mode = False
        self._current_plot_df = None
        # ★ 故意不放 current_data (public) — 验证不会因误读 None 而跳过
        # ★ 故意不放 sensor_results (public) — 验证从 main_win 去取


class MockCompareTabWidget:
    """模拟 CompareTabWidget."""

    def __init__(self, comparison: dict | None = None):
        self._last_comparison: dict | None = comparison


@dataclass
class MockStrainSubConfig:
    """模拟 strain calibration 子配置 — 最少字段即可触发 calib 提取。"""
    sensor_name: str = "A1"
    gauge_length_mm: float = 80.0
    readings: list[dict] = field(default_factory=list)
    ke_results: dict[str, float] = field(default_factory=dict)
    sensor_mode: str = "single"
    grating_map: dict[str, str] = field(default_factory=dict)
    charts_meta: dict = field(default_factory=dict)


class MockTempPage:
    """模拟温度标定子页 — 含 _phase_b_state。"""

    def __init__(
        self,
        phase_b_state: dict | None = None,
        phase_a_result: dict | None = None,
        phase_b_result: dict | None = None,
        annotation: dict | None = None,
    ):
        self._phase_b_state: dict = phase_b_state or {}
        self._phase_a_state: dict = (
            {"seff_result": phase_a_result} if phase_a_result is not None else {}
        )
        self._last_result = phase_a_result
        self._phase_b_result = phase_b_result
        self._annotation_dict = annotation or {}


class MockStrainPage:
    """模拟应变标定子页 — 含 _strain_configs。"""

    def __init__(self, strain_configs: dict | None = None):
        self._strain_configs: dict[str, Any] = strain_configs or {}


class MockCalibrationTabWidget:
    """模拟 CalibrationTabWidget — temp_page + strain_page。"""

    def __init__(self, temp_page=None, strain_page=None):
        self.temp_page = temp_page
        self.strain_page = strain_page
        self.project_config = None


class MockSensorSystem:
    """Phase 1-a: 模拟 SensorSystem — 仅含 reference_row。"""

    def __init__(self, reference_row: int = 0):
        self.reference_row = reference_row


class MockMainWindow:
    """模拟 DataProcessorWindow — 最小属性集供 from_providers 消费。"""

    def __init__(
        self,
        current_data: pd.DataFrame | None = None,
        sensor_results: dict | None = None,
        analysis_tab_widget=None,
        compare_tab_widget=None,
        calibration_tab_widget=None,
        sensor_system=None,  # Phase 1-a: 含 reference_row
    ):
        self.current_data = current_data  # ★ public attr on main_win
        self.sensor_results = sensor_results or {}  # ★ public attr (AnalysisProvider 同源)
        self.analysis_tab_widget = analysis_tab_widget
        self.compare_tab_widget = compare_tab_widget
        self.calibration_tab_widget = calibration_tab_widget
        self.sensor_system = sensor_system  # ★ Phase 1-a: SensorSystem 实例


# ═══════════════════════════════════════════════════════════════════════════
# fixtures
# ═══════════════════════════════════════════════════════════════════════════


@pytest.fixture
def sample_df() -> pd.DataFrame:
    """生成 500 行 × 5 列的数值 DataFrame (模拟分析数据)。"""
    rng = np.random.default_rng(42)
    n = 500
    df = pd.DataFrame({
        "时间": np.arange(n) / 10.0,
        "A1_应变(με)": 1000 * np.sin(np.arange(n) / 50) + rng.normal(0, 5, n),
        "A2_应变(με)": 800 * np.cos(np.arange(n) / 50) + rng.normal(0, 3, n),
        "B1_应变(με)": 600 * np.sin(np.arange(n) / 40 + 1) + rng.normal(0, 4, n),
        "温度(℃)": 25 + 2 * np.sin(np.arange(n) / 200) + rng.normal(0, 0.1, n),
    })
    return df


@pytest.fixture
def sample_df_with_W() -> pd.DataFrame:
    """Phase 1-a: 含 W-column (W1/W2) 的 DataFrame — 用于测试 series_delta 计算。"""
    rng = np.random.default_rng(42)
    n = 500
    w1_base = 1550.0 + rng.normal(0, 0.001, n)
    w2_base = 1552.0 + rng.normal(0, 0.001, n)
    return pd.DataFrame({
        "时间": np.arange(n) / 10.0,
        "W1": w1_base,
        "W2": w2_base,
        "温度(℃)": 25 + 2 * np.sin(np.arange(n) / 200),
    })


@pytest.fixture
def sample_sensor_results_phys() -> dict:
    """Phase 1-a: 模拟 sensor_results — 含应变+温度+自定义公式 key。"""
    rng = np.random.default_rng(99)
    n = 500
    return {
        "A1_应变": (1000 * np.sin(np.arange(n) / 50) + rng.normal(0, 5, n)).tolist(),
        "A1_温度": (25 + 0.1 * np.sin(np.arange(n) / 200) + rng.normal(0, 0.01, n)).tolist(),
        "A2_应变": (800 * np.cos(np.arange(n) / 50) + rng.normal(0, 3, n)).tolist(),
        "A2_温度": (25.5 + 0.1 * np.cos(np.arange(n) / 200) + rng.normal(0, 0.01, n)).tolist(),
        "B1": (600 * np.sin(np.arange(n) / 40 + 1) + rng.normal(0, 4, n)).tolist(),
    }


@pytest.fixture
def sample_sensor_results() -> dict:
    """模拟主窗口的 sensor_results (计算后的物理量) — 旧 fixture 保持向后兼容。"""
    rng = np.random.default_rng(99)
    return {
        "A1": 1000 * np.sin(np.arange(500) / 50) + rng.normal(0, 5, 500),
        "A2": 800 * np.cos(np.arange(500) / 50) + rng.normal(0, 3, 500),
        "B1": 600 * np.sin(np.arange(500) / 40 + 1) + rng.normal(0, 4, 500),
    }


@pytest.fixture
def sample_compensation() -> dict:
    """模拟 _phase_b_state.compensation (含评级 metrics)。"""
    return {
        "A1": {
            "metrics": type("Metrics", (), {
                "residual_sigma_pct_fs": 0.8,
                "hysteresis_max_pct_fs": 1.2,
                "fs": 1000,
            })(),
            "grade": type("Grade", (), {"grade": "优", "passed": True})(),
        },
        "A2": {
            "metrics": type("Metrics", (), {
                "residual_sigma_pct_fs": 1.5,
                "hysteresis_max_pct_fs": 2.8,
                "fs": 1000,
            })(),
            "grade": type("Grade", (), {"grade": "良", "passed": True})(),
        },
        "B1": {
            "metrics": type("Metrics", (), {
                "residual_sigma_pct_fs": 3.5,
                "hysteresis_max_pct_fs": 5.5,
                "fs": 1000,
            })(),
            "grade": type("Grade", (), {"grade": "FAIL", "passed": False,
                                          "reasons": ["迟滞超标(>5%FS)"]})(),
        },
    }


@pytest.fixture
def sample_strain_configs() -> dict:
    """模拟应变标定 _strain_configs — 含 readings + ke_results (单传感器 A1)。"""
    cfg = MockStrainSubConfig(
        sensor_name="A1",
        gauge_length_mm=80.0,
        readings=[
            {"disp_mm": 0.0, "G1_C1_load": 1550.0000, "G2_C1_load": 1552.0000},
            {"disp_mm": 0.01, "G1_C1_load": 1550.1547, "G2_C1_load": 1552.1520},
            {"disp_mm": 0.02, "G1_C1_load": 1550.3079, "G2_C1_load": 1552.3061},
            {"disp_mm": 0.05, "G1_C1_load": 1550.7718, "G2_C1_load": 1552.7630},
            {"disp_mm": 0.10, "G1_C1_load": 1551.5419, "G2_C1_load": 1553.5270},
        ],
        ke_results={"Ke1": 1.234, "Ke2": 1.221},
        sensor_mode="dual_working",
    )
    return {"A1": cfg}


@pytest.fixture
def sample_strain_configs_6() -> dict:
    """Phase 1a 新增: 模拟 _strain_configs 含 6 传感器 (A1-C2)。"""
    configs = {}
    for name in ["A1", "A2", "B1", "B2", "C1", "C2"]:
        configs[name] = MockStrainSubConfig(
            sensor_name=name,
            gauge_length_mm=80.0,
            readings=[
                {"disp_mm": 0.0, "G1_C1_load": 1550.0000, "G2_C1_load": 1552.0000},
                {"disp_mm": 0.02, "G1_C1_load": 1550.3079, "G2_C1_load": 1552.3061},
                {"disp_mm": 0.05, "G1_C1_load": 1550.7718, "G2_C1_load": 1552.7630},
                {"disp_mm": 0.10, "G1_C1_load": 1551.5419, "G2_C1_load": 1553.5270},
            ],
            ke_results={"Ke1": 1.234, "Ke2": 1.221},
            sensor_mode="dual_working",
        )
    return configs


@pytest.fixture
def sample_phase_b_sensors() -> dict:
    """模拟 _phase_b_state['sensors'] — 含 T_abs + eps_orig 迟滞回线数据。"""
    rng = np.random.default_rng(123)
    n = 200
    T_up = np.linspace(25, 85, n // 2)
    T_down = np.linspace(85, 25, n // 2)
    T_abs = np.concatenate([T_up, T_down]).tolist()
    eps_up = 800 * np.sin(np.linspace(0, np.pi, n // 2)) + rng.normal(0, 3, n // 2)
    eps_down = 800 * np.sin(np.linspace(np.pi, 0, n // 2)) + rng.normal(0, 3, n // 2)
    eps_orig = np.concatenate([eps_up, eps_down]).tolist()
    return {
        "A1": {"T_abs": T_abs, "eps_orig": eps_orig},
        "A2": {"T_abs": T_abs, "eps_orig": eps_orig},
    }


# ═══════════════════════════════════════════════════════════════════════════
# 核心测试
# ═══════════════════════════════════════════════════════════════════════════


class TestFromProvidersBasic:
    """基础: _current_data → series/time_h 非空"""

    def test_reads_private_current_data(self, sample_df):
        """验证读的是 atw._current_data(私有), 不是 atw.current_data(不存在)。"""
        atw = MockAnalysisTabWidget(df=sample_df)
        mw = MockMainWindow(current_data=sample_df, analysis_tab_widget=atw)

        bundle = ChartBundle.from_providers(mw)

        # ★ series 非空 — 证明读到了 _current_data
        assert len(bundle.time_h) > 0, "time_h 应为非空"
        assert len(bundle.series) > 0, "series 应为非空"
        assert bundle.time_downsample >= 1

    def test_no_atw_does_not_crash(self):
        """analysis_tab_widget 为 None 时不崩。"""
        mw = MockMainWindow(analysis_tab_widget=None)
        bundle = ChartBundle.from_providers(mw)
        assert bundle.time_h == []
        assert bundle.series == {}

    def test_empty_dataframe_does_not_crash(self):
        """空 DataFrame 不崩。"""
        atw = MockAnalysisTabWidget(df=pd.DataFrame())
        mw = MockMainWindow(analysis_tab_widget=atw)
        bundle = ChartBundle.from_providers(mw)
        assert bundle.time_h == []
        assert bundle.series == {}

    def test_time_axis_uses_real_timestamp_interval(self):
        """4 秒采样必须显示真实历时，不能按 1 秒/行缩短为四分之一。"""
        n = 100
        df = pd.DataFrame({
            "Timestamp": pd.date_range("2026-06-09 20:23:36", periods=n, freq="4s").astype(str),
            "W1": np.linspace(1550.0, 1550.1, n),
        })
        mw = MockMainWindow(current_data=df, analysis_tab_widget=MockAnalysisTabWidget(df=df))

        bundle = ChartBundle.from_providers(mw)

        assert bundle.time_h[-1] == pytest.approx((n - 1) * 4 / 3600.0)


class TestSensorResultsFromMainWin:
    """sensor_results 主源: main_win.sensor_results (已不再提取 metrics/dist → 表格化)"""

    def test_series_still_reads_ok(self, sample_df, sample_sensor_results):
        """验证 series/time_h 仍正常读取 (sensor_results 不再做 metrics/dist 提取)。"""
        atw = MockAnalysisTabWidget(df=sample_df)
        mw = MockMainWindow(
            current_data=sample_df,
            sensor_results=sample_sensor_results,
            analysis_tab_widget=atw,
        )

        bundle = ChartBundle.from_providers(mw)

        assert len(bundle.time_h) > 0, "time_h 应非空"
        assert len(bundle.series) > 0, "series 应非空"


class TestCalibLinearity:
    """标定线性度: calib_sensors (Phase 1a: 多传感器 list[dict]) 从 strain 填值"""

    def test_calib_sensors_from_strain_configs(self, sample_df, sample_sensor_results,
                                                sample_compensation, sample_strain_configs):
        """验证 calib_sensors 从 _strain_configs 提取 (Phase 1a: 单传感器向后兼容)。"""
        atw = MockAnalysisTabWidget(df=sample_df)
        tp = MockTempPage(phase_b_state={"compensation": sample_compensation})
        sp = MockStrainPage(strain_configs=sample_strain_configs)
        ct = MockCalibrationTabWidget(temp_page=tp, strain_page=sp)
        mw = MockMainWindow(
            current_data=sample_df,
            sensor_results=sample_sensor_results,
            analysis_tab_widget=atw,
            calibration_tab_widget=ct,
        )

        bundle = ChartBundle.from_providers(mw)

        # Phase 1a: calib_sensors 为 list[dict]
        assert len(bundle.calib_sensors) == 1, \
            f"calib_sensors 应有 1 条目, 实际 {len(bundle.calib_sensors)}"
        s = bundle.calib_sensors[0]
        assert s["sensor"] == "A1"
        assert len(s["ref"]) >= 3, f"ref 应 ≥3 点, 实际 {len(s['ref'])}"
        assert len(s["measured"]) >= 3, f"measured 应 ≥3 点, 实际 {len(s['measured'])}"
        assert s["unit"] == "pm"
        assert s["slope"] > 0, f"slope 应为正, 实际 {s['slope']}"
        assert "r2" in s
        measured = np.asarray(s["measured"], dtype=float)
        ref = np.asarray(s["ref"], dtype=float)
        fabricated = np.mean([1.234, 1.221]) * ref
        assert not np.allclose(measured, fabricated), "实测纵轴不得由 Ke×理论应变伪造"
        fit = np.polyfit(ref, measured, 1)
        pred = np.polyval(fit, ref)
        expected_r2 = 1.0 - np.sum((measured - pred) ** 2) / np.sum((measured - measured.mean()) ** 2)
        assert s["r2"] == pytest.approx(expected_r2)

    def test_calib_sensors_multiple(self, sample_df, sample_sensor_results,
                                     sample_compensation, sample_strain_configs_6):
        """Phase 1a 新增: _strain_configs 含 6 传感器 → calib_sensors 有 6 条。"""
        atw = MockAnalysisTabWidget(df=sample_df)
        tp = MockTempPage(phase_b_state={"compensation": sample_compensation})
        sp = MockStrainPage(strain_configs=sample_strain_configs_6)
        ct = MockCalibrationTabWidget(temp_page=tp, strain_page=sp)
        mw = MockMainWindow(
            current_data=sample_df,
            sensor_results=sample_sensor_results,
            analysis_tab_widget=atw,
            calibration_tab_widget=ct,
        )

        bundle = ChartBundle.from_providers(mw)

        assert len(bundle.calib_sensors) == 6, \
            f"calib_sensors 应有 6 条目, 实际 {len(bundle.calib_sensors)}"
        sensor_names = {s["sensor"] for s in bundle.calib_sensors}
        assert sensor_names == {"A1", "A2", "B1", "B2", "C1", "C2"}, \
            f"sensor names 应为全部 6 个, 实际 {sensor_names}"
        for s in bundle.calib_sensors:
            assert len(s["ref"]) >= 3, f"{s['sensor']} ref 应 ≥3 点, 实际 {len(s['ref'])}"
            assert len(s["ref"]) == len(s["measured"]), \
                f"{s['sensor']} ref/measured 长度不一致"
            assert s["slope"] > 0, f"{s['sensor']} slope 应为正, 实际 {s['slope']}"
            assert s["unit"] == "pm"

    def test_no_strain_configs_skip_gracefully(self, sample_df, sample_compensation):
        """无 strain_configs → calib_sensors 空列表, 不崩。"""
        atw = MockAnalysisTabWidget(df=sample_df)
        tp = MockTempPage(phase_b_state={"compensation": sample_compensation})
        sp = MockStrainPage(strain_configs={})
        ct = MockCalibrationTabWidget(temp_page=tp, strain_page=sp)
        mw = MockMainWindow(current_data=sample_df, analysis_tab_widget=atw,
                            calibration_tab_widget=ct)

        bundle = ChartBundle.from_providers(mw)
        assert bundle.calib_sensors == []

    def test_legacy_config_without_raw_readings_is_marked_ke_fitted(self, sample_df):
        cfg = MockStrainSubConfig(
            sensor_name="A1",
            gauge_length_mm=80.0,
            readings=[{"disp_mm": 0.0}, {"disp_mm": 0.04}, {"disp_mm": 0.08}],
            ke_results={"Ke1": 1.2},
        )
        ct = MockCalibrationTabWidget(
            temp_page=MockTempPage(),
            strain_page=MockStrainPage({"A1": cfg}),
        )
        mw = MockMainWindow(
            current_data=sample_df,
            analysis_tab_widget=MockAnalysisTabWidget(df=sample_df),
            calibration_tab_widget=ct,
        )

        bundle = ChartBundle.from_providers(mw)

        assert len(bundle.calib_sensors) == 1
        curve = bundle.calib_sensors[0]
        assert curve["source"] == "fitted_from_ke"
        assert curve["ref"] == [0.0, 500.0, 1000.0]
        assert curve["curves"]["G1"] == [0.0, 600.0, 1200.0]


class TestTemperatureCalibrationStandardCharts:
    def test_phase_a_and_phase_b_chart_data_extracted(self, sample_df):
        plateaus = pd.DataFrame({
            "T_set": [20.0, 30.0, 40.0],
            "W1_d": [0.0, 280.0, 560.0],
            "W2_d": [0.0, 300.0, 600.0],
        })
        phase_a_result = {
            "plateaus": plateaus,
            "S_eff": {
                "W1": {"slope": 28.0, "intercept": -560.0, "r2": 0.9999},
                "W2": {"slope": 30.0, "intercept": -600.0, "r2": 0.9998},
            },
            "wavelength_cols": ["W1", "W2"],
        }
        eps = np.array([0.0, 10.0, -5.0, 2.0])
        d_temperature = np.array([0.0, 5.0, 10.0, 15.0])
        phase_b_state = {
            "ke_table": {"A1": {"Ke1": 1.2, "Ke2": 1.1}},
            "sensors": {
                "A1": {
                    "single_grating": False,
                    "eps_corr": eps,
                    "dT_corr": d_temperature,
                    "T_abs": d_temperature + 20.0,
                    "S1": 28.0,
                    "S2": 30.0,
                },
            },
        }
        phase_b_result = {
            "time_h": [0.0, 0.1, 0.2, 0.3],
            "diagnostic_series": {
                "A1": {
                    "eps_raw": eps,
                    "eps_compensated": eps - 2.5,
                },
            },
        }
        tp = MockTempPage(
            phase_b_state=phase_b_state,
            phase_a_result=phase_a_result,
            phase_b_result=phase_b_result,
            annotation={"W1": "A1-W1", "W2": "A1-W2"},
        )
        ct = MockCalibrationTabWidget(
            temp_page=tp,
            strain_page=MockStrainPage({}),
        )
        mw = MockMainWindow(
            current_data=sample_df,
            analysis_tab_widget=MockAnalysisTabWidget(df=sample_df),
            calibration_tab_widget=ct,
        )

        bundle = ChartBundle.from_providers(mw)

        assert len(bundle.temp_regressions) == 2
        assert bundle.temp_regressions[0]["display"] == "A1-W1"
        assert len(bundle.phaseb_diagnostics) == 1
        diagnostic = bundle.phaseb_diagnostics[0]
        assert diagnostic["name"] == "A1"
        assert diagnostic["time_h"] == [0.0, 0.1, 0.2, 0.3]
        expected_dl1 = (1.2 * eps + 28.0 * d_temperature).tolist()
        assert diagnostic["dl1_pm"] == pytest.approx(expected_dl1)
        assert diagnostic["eps_raw"] == pytest.approx(eps)
        assert diagnostic["eps_compensated"] == pytest.approx(eps - 2.5)


class TestGradeTable:
    """分级表 (Batch A): grade_table_md 从 compensation 提取"""

    def test_grade_table_from_compensation(self, sample_df, sample_compensation):
        """验证 grade_table_md 填充 (表替换柱状)。"""
        atw = MockAnalysisTabWidget(df=sample_df)
        tp = MockTempPage(phase_b_state={"compensation": sample_compensation})
        ct = MockCalibrationTabWidget(temp_page=tp)
        mw = MockMainWindow(current_data=sample_df, analysis_tab_widget=atw,
                            calibration_tab_widget=ct)

        bundle = ChartBundle.from_providers(mw)

        assert bundle.grade_table_md, "分级表不应为空"
        assert "A1" in bundle.grade_table_md
        assert "A2" in bundle.grade_table_md
        assert "B1" in bundle.grade_table_md
        assert "优" in bundle.grade_table_md
        assert "FAIL" in bundle.grade_table_md

    def test_grade_table_reads_serialized_metric_and_grade_dicts(self, sample_df):
        compensation = {
            "A1": {
                "metrics": {
                    "residual_sigma_pct_fs": 0.8,
                    "hysteresis_max_pct_fs": 1.2,
                },
                "grade": {
                    "grade": "良",
                    "passed": True,
                    "reasons": ["满足出厂阈值"],
                },
            }
        }
        tp = MockTempPage(phase_b_state={"compensation": compensation})
        ct = MockCalibrationTabWidget(temp_page=tp)
        mw = MockMainWindow(
            current_data=sample_df,
            analysis_tab_widget=MockAnalysisTabWidget(df=sample_df),
            calibration_tab_widget=ct,
        )

        bundle = ChartBundle.from_providers(mw)

        assert "| A1 | 0.80 | 1.20 | 良 | ✓ | 满足出厂阈值 |" in bundle.grade_table_md


class TestPhysSeriesProvider:
    """Phase 1-a: phys_series + series_delta 数据层接入"""

    def test_phys_series_from_sensor_results(self, sample_df, sample_sensor_results_phys):
        """验证 sensor_results → phys_series 降采样填充。"""
        atw = MockAnalysisTabWidget(df=sample_df)
        mw = MockMainWindow(
            current_data=sample_df,
            sensor_results=sample_sensor_results_phys,
            analysis_tab_widget=atw,
        )

        bundle = ChartBundle.from_providers(mw)

        # phys_series 含全部 5 个 key
        assert len(bundle.phys_series) == 5, \
            f"phys_series 应有 5 个 key, 实际 {len(bundle.phys_series)}: {list(bundle.phys_series)}"
        for key in ["A1_应变", "A1_温度", "A2_应变", "A2_温度", "B1"]:
            assert key in bundle.phys_series, f"缺 key={key}"

        # ★ 点数对齐: phys_series 每个 key 与 time_h 等长
        for key, vals in bundle.phys_series.items():
            assert len(vals) == len(bundle.time_h), \
                f"{key}: phys_series len={len(vals)} != time_h len={len(bundle.time_h)}"

        # step 生效: 500行 → step=max(1, 500//200)=2 → 250点 (不是500)
        assert len(bundle.time_h) == 250, f"降采样后应为 250 点, 实际 {len(bundle.time_h)}"

    def test_phys_series_empty_when_no_sensor_results(self, sample_df):
        """sensor_results 缺 → phys_series 空 dict 不崩。"""
        atw = MockAnalysisTabWidget(df=sample_df)
        mw = MockMainWindow(
            current_data=sample_df,
            sensor_results={},
            analysis_tab_widget=atw,
        )

        bundle = ChartBundle.from_providers(mw)
        assert bundle.phys_series == {}

    def test_series_delta_from_W_columns(self, sample_df_with_W):
        """验证 _current_data W列 → Δλ = λ − λ0 (基准行)。"""
        atw = MockAnalysisTabWidget(df=sample_df_with_W)
        ss = MockSensorSystem(reference_row=0)
        mw = MockMainWindow(
            current_data=sample_df_with_W,
            analysis_tab_widget=atw,
            sensor_system=ss,
        )

        bundle = ChartBundle.from_providers(mw)

        # series_delta 含 W1/W2
        assert len(bundle.series_delta) == 2, \
            f"series_delta 应有 2 个 key, 实际 {len(bundle.series_delta)}: {list(bundle.series_delta)}"
        for key in ["W1", "W2"]:
            assert key in bundle.series_delta, f"缺 W-column: {key}"

        # W1[reference_row=0] 的 Δλ[0] 应为 0 (λ − λ0 at ref row)
        assert abs(bundle.series_delta["W1"][0]) < 1e-9, \
            f"W1 基准行 Δλ 应为 0, 实际 {bundle.series_delta['W1'][0]}"

        # ★ 点数对齐
        for key, vals in bundle.series_delta.items():
            assert len(vals) == len(bundle.time_h), \
                f"{key}: series_delta len={len(vals)} != time_h len={len(bundle.time_h)}"

        # 后续点 Δλ 非零 (制造了随机波动)
        assert abs(bundle.series_delta["W1"][-1]) > 0, \
            f"W1 末点 Δλ 不应全零"

    def test_series_delta_excludes_anomaly_derivative_columns(self):
        df = pd.DataFrame({
            "Timestamp": pd.date_range("2026-01-01", periods=4, freq="4s").astype(str),
            "W1": [1550.0, 1550.1, 1550.2, 1550.3],
            "W1_anomaly": [False, False, True, False],
            "W1_anomaly_anomaly": [False, False, False, True],
            "W2": [1552.0, 1552.1, 1552.2, 1552.3],
        })
        mw = MockMainWindow(
            current_data=df,
            analysis_tab_widget=MockAnalysisTabWidget(df=df),
            sensor_system=MockSensorSystem(reference_row=0),
        )

        bundle = ChartBundle.from_providers(mw)

        assert set(bundle.series_delta) == {"W1", "W2"}

    def test_series_delta_empty_when_no_sensor_system(self, sample_df_with_W):
        """无 sensor_system → series_delta 留空不崩。"""
        atw = MockAnalysisTabWidget(df=sample_df_with_W)
        mw = MockMainWindow(
            current_data=sample_df_with_W,
            analysis_tab_widget=atw,
            sensor_system=None,
        )

        bundle = ChartBundle.from_providers(mw)
        assert bundle.series_delta == {}

    def test_phys_series_to_dict_roundtrip(self, sample_df, sample_sensor_results_phys):
        """to_dict 后 chart_data 含 phys_series + series_delta。"""
        atw = MockAnalysisTabWidget(df=sample_df)
        ss = MockSensorSystem(reference_row=0)
        mw = MockMainWindow(
            current_data=sample_df,
            sensor_results=sample_sensor_results_phys,
            analysis_tab_widget=atw,
            sensor_system=ss,
        )

        bundle = ChartBundle.from_providers(mw)
        cd = bundle.to_dict()

        assert "phys_series" in cd
        assert "series_delta" in cd
        assert len(cd["phys_series"]) == 5
        # 每个值应是 list[float] (已序列化)
        for v in cd["phys_series"].values():
            assert isinstance(v, list)
            if v:
                assert isinstance(v[0], (int, float))


class TestHystCompareSkip:
    """滞回/多源对比无数据 → skip 不崩"""

    def test_no_hyst_data_does_not_crash(self, sample_df, sample_compensation):
        """_phase_b_state 无 sensors → hyst_sensors 为空。"""
        atw = MockAnalysisTabWidget(df=sample_df)
        tp = MockTempPage(phase_b_state={"compensation": sample_compensation})
        ct = MockCalibrationTabWidget(temp_page=tp)
        mw = MockMainWindow(current_data=sample_df, analysis_tab_widget=atw,
                            calibration_tab_widget=ct)

        bundle = ChartBundle.from_providers(mw)
        assert bundle.hyst_sensors == []

    def test_hyst_sensors_populated_when_sensors_data_present(
        self, sample_df, sample_compensation, sample_phase_b_sensors
    ):
        """_phase_b_state 含 sensors(T_abs+eps_orig) → hyst_sensors 非空、结构正确。"""
        atw = MockAnalysisTabWidget(df=sample_df)
        tp = MockTempPage(phase_b_state={
            "compensation": sample_compensation,
            "sensors": sample_phase_b_sensors,
        })
        ct = MockCalibrationTabWidget(temp_page=tp)
        mw = MockMainWindow(current_data=sample_df, analysis_tab_widget=atw,
                            calibration_tab_widget=ct)

        bundle = ChartBundle.from_providers(mw)
        assert len(bundle.hyst_sensors) == 2  # A1 + A2
        names = {hs['name'] for hs in bundle.hyst_sensors}
        assert names == {"A1", "A2"}
        for hs in bundle.hyst_sensors:
            assert isinstance(hs['T_abs'], list)
            assert isinstance(hs['eps'], list)
            assert len(hs['T_abs']) == 200
            assert len(hs['eps']) == 200
            # 值域合理性: T 应在 20-90℃, ε 应在合理范围
            assert 20 < max(hs['T_abs']) < 90
            assert abs(max(hs['eps'])) < 2000

    def test_hyst_prefers_finite_eps_corr_over_all_nan_eps_orig(
        self, sample_df, sample_compensation
    ):
        """修正解耦序列有效时，不能被仅用于对比的全 NaN eps_orig 遮蔽。"""
        sensors = {
            "A1": {
                "T_abs": [25.0, 45.0, 65.0, 85.0],
                "eps_orig": [float("nan")] * 4,
                "eps_corr": [0.0, 12.0, 24.0, 36.0],
            }
        }
        tp = MockTempPage(phase_b_state={
            "compensation": sample_compensation,
            "sensors": sensors,
        })
        ct = MockCalibrationTabWidget(temp_page=tp)
        mw = MockMainWindow(
            current_data=sample_df,
            analysis_tab_widget=MockAnalysisTabWidget(df=sample_df),
            calibration_tab_widget=ct,
        )

        bundle = ChartBundle.from_providers(mw)

        assert bundle.hyst_sensors == [{
            "name": "A1",
            "T_abs": [25.0, 45.0, 65.0, 85.0],
            "eps": [0.0, 12.0, 24.0, 36.0],
        }]

    def test_hyst_falls_back_to_finite_eps_orig_when_eps_corr_invalid(
        self, sample_df, sample_compensation
    ):
        """兼容旧状态：eps_corr 缺失/无有效值时，有限 eps_orig 仍可出图。"""
        sensors = {
            "A1": {
                "T_abs": [25.0, 45.0, 65.0],
                "eps_orig": [1.0, 2.0, 3.0],
                "eps_corr": [float("nan")] * 3,
            }
        }
        tp = MockTempPage(phase_b_state={
            "compensation": sample_compensation,
            "sensors": sensors,
        })
        ct = MockCalibrationTabWidget(temp_page=tp)
        mw = MockMainWindow(
            current_data=sample_df,
            analysis_tab_widget=MockAnalysisTabWidget(df=sample_df),
            calibration_tab_widget=ct,
        )

        bundle = ChartBundle.from_providers(mw)

        assert bundle.hyst_sensors[0]["eps"] == [1.0, 2.0, 3.0]

    def test_six_corrected_phase_b_series_produce_six_hysteresis_pngs(
        self, sample_df, sample_compensation, tmp_path
    ):
        """真实缺图模式的端到端门禁：全 NaN 原始值不得阻止六张修正迟滞图落盘。"""
        temperatures = [25.0, 45.0, 65.0, 85.0, 65.0, 45.0, 25.0]
        sensors = {
            name: {
                "T_abs": temperatures,
                "eps_orig": [float("nan")] * len(temperatures),
                "eps_corr": [
                    offset + 0.5 * temperature
                    for temperature in temperatures
                ],
            }
            for offset, name in enumerate(("A1", "A2", "B1", "B2", "C1", "C2"))
        }
        tp = MockTempPage(phase_b_state={
            "compensation": sample_compensation,
            "sensors": sensors,
        })
        ct = MockCalibrationTabWidget(temp_page=tp)
        mw = MockMainWindow(
            current_data=sample_df,
            analysis_tab_widget=MockAnalysisTabWidget(df=sample_df),
            calibration_tab_widget=ct,
        )

        bundle = ChartBundle.from_providers(mw)
        from core.chart_store import build_chart_store

        manifest = build_chart_store(bundle.to_dict(), str(tmp_path), [])
        produced = [
            entry for entry in manifest
            if entry.chart_id.startswith("phaseb_hyst_") and entry.produced
        ]

        assert len(produced) == 6
        assert all((tmp_path / entry.rel_path).is_file() for entry in produced)

    def test_no_compare_data_does_not_crash(self, sample_df):
        """无 _last_comparison → compare 字段全空。"""
        atw = MockAnalysisTabWidget(df=sample_df)
        cw = MockCompareTabWidget(comparison=None)  # 没跑过
        mw = MockMainWindow(current_data=sample_df, analysis_tab_widget=atw,
                            compare_tab_widget=cw)

        bundle = ChartBundle.from_providers(mw)
        assert bundle.compare_time_h == []
        assert bundle.compare_sources == {}
        assert bundle.compare_pairs == []

    def test_compare_plot_payload_produces_overlay_and_scatter(
        self, sample_df, tmp_path
    ):
        """UI 留存的对齐时程必须一路进入 ChartBundle 和图仓。"""
        comparison = {
            "time_h": (np.arange(500, dtype=float) / 3600.0).tolist(),
            "sources": {
                "应变-光纤1": np.sin(np.linspace(0, 8, 500)).tolist(),
                "应变-应变片1": np.sin(np.linspace(0, 8, 500) + 0.1).tolist(),
            },
            "pairs": [
                {
                    "device_a": "应变-光纤1",
                    "device_b": "应变-应变片1",
                    "corr": 0.98,
                    "mae": 0.05,
                    "rmse": 0.08,
                }
            ],
        }
        mw = MockMainWindow(
            current_data=sample_df,
            analysis_tab_widget=MockAnalysisTabWidget(df=sample_df),
            compare_tab_widget=MockCompareTabWidget(comparison=comparison),
        )

        bundle = ChartBundle.from_providers(mw)

        assert 2 <= len(bundle.compare_time_h) <= 201
        assert set(bundle.compare_sources) == {"应变-光纤1", "应变-应变片1"}
        assert all(
            len(values) == len(bundle.compare_time_h)
            for values in bundle.compare_sources.values()
        )

        from core.chart_store import build_chart_store

        manifest = build_chart_store(bundle.to_dict(), str(tmp_path), [])
        produced_ids = {entry.chart_id for entry in manifest if entry.produced}
        assert "compare_ol" in produced_ids
        assert "compare_corr_scatter_0" in produced_ids
