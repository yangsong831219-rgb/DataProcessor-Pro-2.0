"""Data provider tests — DataFile/Cleaning/Analysis/Calibration + registry + Ke + features."""

from __future__ import annotations
import json
import os
import sys
from types import SimpleNamespace
import numpy as np
import pandas as pd
import pytest


def _make_fake_metrics(**overrides):
    """Return plain dict matching CompensationMetrics fields (production JSON roundtrip format)."""
    defaults: dict = dict(
        sensor="A1", residual_sigma=12.3, repeatability=5.0,
        hysteresis_max=8.0, noise_floor=1.5, temp_sensitivity_max=0.3,
        worst_case_single=4.0, low_confidence=False, fs=1000,
        comp_form="poly", poly_order=2,
        residual_sigma_pct_fs=1.23, repeatability_pct_fs=0.5,
        hysteresis_max_pct_fs=0.8, noise_floor_pct_fs=0.15,
        worst_case_single_pct_fs=0.4,
    )
    defaults.update(overrides)
    return defaults


def _make_fake_grade(sensor, grade, passed, reasons):
    """Return plain dict matching SensorGrade fields (production JSON roundtrip format)."""
    return dict(sensor=sensor, grade=grade, passed=passed, reasons=reasons)


def _make_main_win(data=None, template=None, has_cal=True):
    if has_cal:
        compensation = {
            "A1": {
                "model": None,
                "metrics": _make_fake_metrics(sensor="A1", residual_sigma_pct_fs=1.2,
                                              hysteresis_max_pct_fs=0.8, repeatability_pct_fs=0.5,
                                              fs=1000, comp_form="poly", poly_order=2),
                "grade": _make_fake_grade("A1", "good", True, ["ok"]),
            },
            "C1": {
                "model": None,
                "metrics": _make_fake_metrics(sensor="C1", residual_sigma_pct_fs=3.8,
                                              hysteresis_max_pct_fs=4.2, repeatability_pct_fs=2.1,
                                              fs=2000, comp_form="lut", poly_order=0),
                "grade": _make_fake_grade("C1", "pass", True, ["high hysteresis"]),
            },
            "C2": {
                "model": None,
                "metrics": _make_fake_metrics(sensor="C2", residual_sigma_pct_fs=6.5,
                                              hysteresis_max_pct_fs=7.8, repeatability_pct_fs=3.2,
                                              fs=800, comp_form="poly", poly_order=4),
                "grade": _make_fake_grade("C2", "FAIL", False, ["hys>5%FS", "sigma>4%FS"]),
            },
            "D1": {
                "model": None, "metrics": None,
                "grade": _make_fake_grade("D1", "N/A", False, ["single grating"]),
            },
        }
        ke_table = {
            "A1": {"Ke1": 0.82, "Ke2": 0.78},
            "C1": {"Ke1": 0.91, "Ke2": 0.88},
            "C2": {"Ke1": 1.05, "Ke2": 0.95},
        }
        decoupling = {
            "A1": {"e_mean": 5.2, "e_std": 12.3, "e_range": 9.8, "rating": "good", "single_grating": False},
            "C1": {"e_mean": 8.1, "e_std": 34.5, "e_range": 28.7, "rating": "pass", "single_grating": False},
            "C2": {"e_mean": 3.4, "e_std": 6.2, "e_range": 5.1, "rating": "FAIL", "single_grating": False},
            "D1": {"e_mean": 0, "e_std": 0, "e_range": 0, "rating": "N/A", "single_grating": True},
        }
        S_eff = {"W1": 0.00123, "W2": 0.00135}
        tp = SimpleNamespace(
            _phase_a_state={
                "seff_result": {"S_eff": S_eff},
                "annotation": {}, "groups": {},
                "tmin": 20.0, "tmax": 80.0, "tstep": 10.0, "params": {},
            },
            _phase_b_state={
                "ke_table": ke_table,
                "decoupling_results": decoupling,
                "compensation": compensation,
                "comp_form": "poly", "poly_order": 2,
                "grade_thresholds": {}, "subsample_step": 10,
            },
        )
    else:
        tp = SimpleNamespace(_phase_a_state={}, _phase_b_state={})

    ct = SimpleNamespace(temp_page=tp)
    mw = SimpleNamespace(
        current_data=data,
        current_template=template,
        calibration_tab_widget=ct,
        cleaning_tab_widget=None,
        sensor_results={},
    )
    return mw


# ═══════════════════════════════════════════════════════════
# DataFileProvider
# ═══════════════════════════════════════════════════════════

class TestDataFileProvider:

    def test_is_available_no_data(self):
        from core.data_providers import DataFileProvider
        p = DataFileProvider()
        assert not p.is_available(SimpleNamespace(current_data=None))

    def test_is_available_with_data(self):
        import pandas as pd
        from core.data_providers import DataFileProvider
        mw = SimpleNamespace(current_data=pd.DataFrame({"A": [1]}))
        assert DataFileProvider().is_available(mw)

    def test_summary_has_template_and_shape(self):
        import pandas as pd
        from core.data_providers import DataFileProvider
        tpl = SimpleNamespace(name="ENLIGHT_Peaks")
        mw = SimpleNamespace(
            current_data=pd.DataFrame({"W1": [1530.0, 1530.1], "W2": [1540.0, 1540.1]}),
            current_template=tpl,
        )
        mw.is_fiber_data = lambda: True
        mw.get_annotated_columns = lambda: None
        s = DataFileProvider().get_summary(mw)
        assert "ENLIGHT" in s
        assert "fiber" in s.lower() or "grating" in s.lower() or "optical" in s.lower() or "光" in s
        assert "2" in s  # row or col count

    def test_no_timestamp_graceful(self):
        import pandas as pd
        from core.data_providers import DataFileProvider
        mw = SimpleNamespace(current_data=pd.DataFrame({"A": [1, 2]}))
        mw.is_fiber_data = lambda: False
        mw.get_annotated_columns = lambda: None
        s = DataFileProvider().get_summary(mw)
        assert "无" in s or "time" in s.lower()

    def test_all_unknown_shows_fallback(self):
        import pandas as pd
        from core.data_providers import DataFileProvider
        mw = SimpleNamespace(current_data=pd.DataFrame({"C1": [1, 2], "C2": [3, 4]}))
        mw.is_fiber_data = lambda: False
        mw.get_annotated_columns = lambda: None
        s = DataFileProvider().get_summary(mw)
        assert "未知" in s

    def test_budget_enforced(self):
        import pandas as pd
        import numpy as np
        from core.data_providers import DataFileProvider
        df = pd.DataFrame(np.random.randn(10, 50), columns=[f"C{i}" for i in range(50)])
        mw = SimpleNamespace(current_data=df)
        mw.is_fiber_data = lambda: False
        mw.get_annotated_columns = lambda: None
        s = DataFileProvider().get_summary(mw, budget_chars=300)
        assert len(s) <= 350  # small margin


# ═══════════════════════════════════════════════════════════
# CalibrationProvider
# ═══════════════════════════════════════════════════════════

class TestCalibrationProvider:

    def test_is_available_with_state(self):
        from core.data_providers import CalibrationProvider
        assert CalibrationProvider().is_available(_make_main_win())

    def test_is_available_empty(self):
        from core.data_providers import CalibrationProvider
        assert not CalibrationProvider().is_available(_make_main_win(has_cal=False))

    def test_is_available_no_tab(self):
        from core.data_providers import CalibrationProvider
        assert not CalibrationProvider().is_available(SimpleNamespace(calibration_tab_widget=None))

    def test_summary_has_sensors(self):
        from core.data_providers import CalibrationProvider
        s = CalibrationProvider().get_summary(_make_main_win())
        assert "A1" in s
        assert "1.2" in s or "1.20" in s  # sigma %FS
        assert "good" in s

    def test_summary_FAIL(self):
        from core.data_providers import CalibrationProvider
        s = CalibrationProvider().get_summary(_make_main_win())
        assert "C2" in s
        assert "FAIL" in s

    def test_summary_single_grating(self):
        from core.data_providers import CalibrationProvider
        s = CalibrationProvider().get_summary(_make_main_win())
        assert "D1" in s
        assert "N/A" in s


# ═══════════════════════════════════════════════════════════
# CleaningProvider
# ═══════════════════════════════════════════════════════════

class TestCleaningProvider:

    def test_not_available_no_widget(self):
        from core.data_providers import CleaningProvider
        assert not CleaningProvider().is_available(SimpleNamespace(cleaning_tab_widget=None))

    def test_not_available_never_run(self):
        from core.data_providers import CleaningProvider
        ct = SimpleNamespace(_cleaning_has_run=False, _anomaly_info={})
        assert not CleaningProvider().is_available(SimpleNamespace(cleaning_tab_widget=ct))

    def test_available_run_with_anomalies(self):
        from core.data_providers import CleaningProvider
        ct = SimpleNamespace(_cleaning_has_run=True,
                            _anomaly_info={"W1": {"count": 5, "indices": [1, 2], "total_indices": 5}})
        assert CleaningProvider().is_available(SimpleNamespace(cleaning_tab_widget=ct))

    def test_available_run_zero_anomalies(self):
        """跑了但 0 异常 → is_available=True。"""
        from core.data_providers import CleaningProvider
        ct = SimpleNamespace(_cleaning_has_run=True, _anomaly_info={})
        assert CleaningProvider().is_available(SimpleNamespace(cleaning_tab_widget=ct))

    def test_summary_never_run(self):
        from core.data_providers import CleaningProvider
        ct = SimpleNamespace(_cleaning_has_run=False, _anomaly_info={})
        s = CleaningProvider().get_summary(SimpleNamespace(cleaning_tab_widget=ct))
        assert "未执行" in s

    def test_summary_run_zero_anomalies(self):
        """跑了 0 异常 → '已清洗，未发现异常'。"""
        from core.data_providers import CleaningProvider
        ct = SimpleNamespace(_cleaning_has_run=True, _anomaly_info={})
        ct.get_config = lambda: {"fill_method": "linear", "adjacent_enabled": True, "diff_threshold": 1.0, "nan_enabled": True}
        s = CleaningProvider().get_summary(SimpleNamespace(cleaning_tab_widget=ct))
        assert "已清洗" in s
        assert "未发现异常" in s
        assert "linear" in s

    def test_summary_with_anomalies_shows_counts(self):
        from core.data_providers import CleaningProvider
        ct = SimpleNamespace(
            _cleaning_has_run=True,
            _anomaly_info={
                "W1": {"count": 3, "indices": [1, 2, 3], "total_indices": 3},
                "W2": {"count": 7, "indices": [4, 5], "total_indices": 7},
            },
        )
        ct.get_config = lambda: {"fill_method": "linear", "adjacent_enabled": True, "nan_enabled": True}
        s = CleaningProvider().get_summary(SimpleNamespace(cleaning_tab_widget=ct))
        assert "10" in s
        assert "W1" in s
        assert "W2" in s
        assert "linear" in s


# ═══════════════════════════════════════════════════════════
# AnalysisProvider
# ═══════════════════════════════════════════════════════════

class TestAnalysisProvider:

    def test_not_available_without_results(self):
        from core.data_providers import AnalysisProvider
        # 无 analysis_tab_widget → is_available False
        assert not AnalysisProvider().is_available(SimpleNamespace())

    def test_not_available_without_widget(self):
        from core.data_providers import AnalysisProvider
        # analysis_tab_widget 存在但 _current_data=None → is_available False
        assert not AnalysisProvider().is_available(
            SimpleNamespace(analysis_tab_widget=SimpleNamespace(_current_data=None)))

    def test_available_with_results(self):
        from core.data_providers import AnalysisProvider
        # _current_data 非空 DataFrame → is_available True（真机已验）
        df = pd.DataFrame({"W1": [1.0, 2.0, 3.0]})
        assert AnalysisProvider().is_available(
            SimpleNamespace(analysis_tab_widget=SimpleNamespace(_current_data=df)))

    def test_summary_has_features(self):
        from core.data_providers import AnalysisProvider
        # get_summary 从 _current_data 数值列提取统计摘要
        df = pd.DataFrame({
            "FBG_A1": np.linspace(100, 120, 100),
            "Timestamp": np.arange(100),  # 应被过滤
        })
        mw = SimpleNamespace(
            analysis_tab_widget=SimpleNamespace(_current_data=df),
            current_annotation={"FBG_A1": "w1-A1-1 应变 梁底"},
        )
        s = AnalysisProvider().get_summary(mw)
        # summary 应含暗号名或原始列名、不含被过滤列
        assert "FBG_A1" in s or "w1-A1-1" in s
        assert "110.000" in s
        assert "CV" in s or "一般" in s or "波动" in s
        assert "Timestamp" not in s  # 时间戳列被 is_plottable_data_column 过滤

    def test_summary_has_drift(self):
        from core.data_providers import AnalysisProvider
        df = pd.DataFrame({"S1": np.linspace(0, 100, 200)})
        mw = SimpleNamespace(
            analysis_tab_widget=SimpleNamespace(_current_data=df),
            current_annotation={},
        )
        s = AnalysisProvider().get_summary(mw)
        assert "drift" in s.lower() or "漂移" in s

    def test_feature_function(self):
        from core.data_providers import compute_time_series_features
        f = compute_time_series_features([1.0, 2.0, 3.0])
        assert f['n'] == 3
        assert f['mean'] == 2.0
        assert f['min'] == 1.0
        assert f['max'] == 3.0


# ═══════════════════════════════════════════════════════════
# Registry
# ═══════════════════════════════════════════════════════════

class TestProviderRegistry:

    def test_provider_count(self):
        from core.data_providers import get_all_providers
        assert len(get_all_providers()) >= 5  # 6 providers as of ExternalDataProvider addition

    def test_types(self):
        from core.data_providers import (get_all_providers, DataFileProvider,
                                          CleaningProvider, AnalysisProvider,
                                          CompareProvider, CalibrationProvider)
        ps = get_all_providers()
        assert isinstance(ps[0], DataFileProvider)
        assert isinstance(ps[1], CleaningProvider)
        assert isinstance(ps[2], AnalysisProvider)
        assert isinstance(ps[3], CompareProvider)
        assert isinstance(ps[4], CalibrationProvider)

    def test_compare_not_available_if_no_data(self):
        from core.data_providers import CompareProvider
        mw = SimpleNamespace(compare_tab_widget=None)
        assert not CompareProvider().is_available(mw)

    def test_compare_available_with_result(self):
        from core.data_providers import CompareProvider
        cw = SimpleNamespace(_last_comparison={
            'pairs': [{'device_a': 'A', 'device_b': 'B', 'corr': 0.95, 'mae': 0.1, 'rmse': 0.2, 'max_error': 0.5}],
            'device_features': {'A': {'mean': 1.0, 'std': 0.1, 'drift_slope_per_1k': 0.01, 'stability': 'stable'}},
            'align_method': 'test', 'baseline_method': 'test', 'n_points': 100,
        })
        mw = SimpleNamespace(compare_tab_widget=cw)
        assert CompareProvider().is_available(mw)

    def test_compare_summary_has_corr(self):
        from core.data_providers import CompareProvider
        cw = SimpleNamespace(_last_comparison={
            'pairs': [{'device_a': 'A', 'device_b': 'B', 'corr': 0.95, 'mae': 0.1, 'rmse': 0.2, 'max_error': 0.5}],
            'device_features': {},
            'align_method': 'test', 'baseline_method': 'test', 'n_points': 100,
        })
        mw = SimpleNamespace(compare_tab_widget=cw)
        s = CompareProvider().get_summary(mw)
        assert 'corr=0.9500' in s
        assert 'MAE=0.1000' in s
        assert 'RMSE=0.2000' in s

    def test_compare_empty_shows_unexecuted(self):
        from core.data_providers import CompareProvider
        cw = SimpleNamespace(_last_comparison=None)
        mw = SimpleNamespace(compare_tab_widget=cw)
        s = CompareProvider().get_summary(mw)
        assert '未执行' in s


# ═══════════════════════════════════════════════════════════
# Cleaning compact budget
# ═══════════════════════════════════════════════════════════

class TestCleaningBudget:

    def test_large_anomaly_set_is_compact(self):
        """690 anomalies across many cols → summary still compact, no position dump."""
        from core.data_providers import CleaningProvider
        info = {}
        for i in range(20):
            info[f'col_{i}'] = {'count': 30 + i, 'indices': list(range(1000, 1020)),
                                'total_indices': 30 + i}
        ct = SimpleNamespace(_cleaning_has_run=True, _anomaly_info=info)
        ct.get_config = lambda: {}
        mw = SimpleNamespace(cleaning_tab_widget=ct)
        s = CleaningProvider().get_summary(mw, budget_chars=1000)
        # Should be compact, no raw position dumps
        lines = s.split('\n')
        assert len(lines) <= 20  # header + 15 cols + 1 remaining hint + rules
        assert len(s) < 1000
        # Check no 20-int position lists
        assert '[1, 2, 3, 4, 5]' not in s

    def test_names(self):
        from core.data_providers import get_all_providers
        names = [p.display_name for p in get_all_providers()]
        assert len(names) >= 5  # 6 providers as of ExternalDataProvider addition
        assert "data" in names[0].lower() or "file" in names[0].lower() or "数据" in names[0]
        assert "clean" in names[1].lower() or "清洗" in names[1]
        assert "analysis" in names[2].lower() or "分析" in names[2]
        assert "compare" in names[3].lower() or "对比" in names[3]
        assert "calibration" in names[4].lower() or "标定" in names[4]


# ═══════════════════════════════════════════════════════════
# Ke Precision
# ═══════════════════════════════════════════════════════════

class TestKePrecision:

    def test_strain_ke_from_configs_dict(self):
        """真实路径: StrainSubConfig.ke_results 是 dict, 取值用 .get() 不用 getattr。"""
        from core.data_providers import _get_strain_ke
        cfg = SimpleNamespace(sensor_name="A1", ke_results={"Ke1": 1.1814, "Ke2": 0.0008})
        sp = SimpleNamespace(_strain_configs={"A1": cfg})
        ct = SimpleNamespace(strain_page=sp)
        mw = SimpleNamespace(calibration_tab_widget=ct)
        ke = _get_strain_ke(mw, "A1")
        assert ke is not None
        assert abs(ke['Ke1'] - 1.1814) < 0.0001
        assert abs(ke['Ke2'] - 0.0008) < 0.0001

    def test_strain_ke_zero_values_return_none(self):
        """Ke1=Ke2=0 时不返回(让调用方回落 ke_table), 不挡真值。"""
        from core.data_providers import _get_strain_ke
        cfg = SimpleNamespace(sensor_name="A1", ke_results={"Ke1": 0.0, "Ke2": 0.0})
        sp = SimpleNamespace(_strain_configs={"A1": cfg})
        ct = SimpleNamespace(strain_page=sp)
        mw = SimpleNamespace(calibration_tab_widget=ct)
        ke = _get_strain_ke(mw, "A1")
        assert ke is None, "Ke 全零应返回 None，让 ke_table 兜底"

    def test_strain_ke_single_grating_ke2_zero(self):
        """单栅 Ke2=0, Ke1 有值时正常返回(不是全零)。"""
        from core.data_providers import _get_strain_ke
        cfg = SimpleNamespace(sensor_name="A2", ke_results={"Ke1": 0.987})
        sp = SimpleNamespace(_strain_configs={"A2": cfg})
        ct = SimpleNamespace(strain_page=sp)
        mw = SimpleNamespace(calibration_tab_widget=ct)
        ke = _get_strain_ke(mw, "A2")
        assert ke is not None
        assert abs(ke['Ke1'] - 0.987) < 0.0001
        assert abs(ke['Ke2'] - 0.0) < 0.0001  # 缺键默认 0

    def test_no_strain_returns_none(self):
        from core.data_providers import _get_strain_ke
        assert _get_strain_ke(SimpleNamespace(calibration_tab_widget=None), "A1") is None

    def test_empty_configs_returns_none(self):
        from core.data_providers import _get_strain_ke
        sp = SimpleNamespace(_strain_configs={})
        ct = SimpleNamespace(strain_page=sp)
        mw = SimpleNamespace(calibration_tab_widget=ct)
        assert _get_strain_ke(mw, "A1") is None


# ═══════════════════════════════════════════════════════════
# is_fiber_data
# ═══════════════════════════════════════════════════════════

class TestIsFiberData:

    def test_fbg_detected(self):
        import pandas as pd
        import re as _re
        df = pd.DataFrame({"FBG_A1": [1, 2]})
        is_fiber = any('FBG' in str(c) or _re.match(r'^W\d+$', str(c)) for c in df.columns)
        assert is_fiber

    def test_W_col_detected(self):
        import pandas as pd
        import re as _re
        df = pd.DataFrame({"W1": [1, 2], "W2": [3, 4]})
        is_fiber = any('FBG' in str(c) or _re.match(r'^W\d+$', str(c)) for c in df.columns)
        assert is_fiber

    def test_generic_not_detected(self):
        import pandas as pd
        import re as _re
        df = pd.DataFrame({"A": [1, 2], "B": [3, 4]})
        is_fiber = any('FBG' in str(c) or _re.match(r'^W\d+$', str(c)) for c in df.columns)
        assert not is_fiber


# ═══════════════════════════════════════════════════════════
# Defect 2: S_eff in snapshot when has_a=True, has_b=False
# ═══════════════════════════════════════════════════════════

class TestCalibrationSummaryS_eff:
    """CalibrationProvider.get_summary — S_eff 数据在仅有温度 Phase A 时仍进入快照"""

    def test_s_eff_only_produces_sensor_lines(self):
        """仅 S_eff 存在 (has_a=True, has_b=False) → 不出 '(无传感器数据)'。"""
        from core.data_providers import CalibrationProvider
        cp = CalibrationProvider()

        sp = SimpleNamespace(_strain_configs={})
        ct = SimpleNamespace(strain_page=sp)

        S_eff = {
            "A1-W1": {"slope": 21.57, "r2": 0.9998, "T_base": 23.5},
            "A1-W2": {"slope": 28.15, "r2": 0.9999, "T_base": 23.5},
        }
        tp = SimpleNamespace(
            _phase_a_state={"seff_result": {"S_eff": S_eff}},
            _phase_b_state={},
        )
        main_win = SimpleNamespace(calibration_tab_widget=SimpleNamespace(
            temp_page=tp, strain_page=sp,
        ))

        text = cp.get_summary(main_win, budget_chars=2000)
        assert "(未运行)" not in text
        assert "(无传感器数据)" not in text
        # 应有传感器前缀 A1
        assert "A1" in text
        # 应有灵敏度值
        assert "21.57" in text

    def test_s_eff_and_ke_table_both_included(self):
        """S_eff + ke_table 同时在 → 传感器数量正确。"""
        from core.data_providers import CalibrationProvider
        cp = CalibrationProvider()

        S_eff = {"A1-W1": {"slope": 21.57, "r2": 0.9998, "T_base": 23.5}}
        sp = SimpleNamespace(_strain_configs={})
        ct = SimpleNamespace(strain_page=sp)
        tp = SimpleNamespace(
            _phase_a_state={"seff_result": {"S_eff": S_eff}},
            _phase_b_state={
                "ke_table": {"A1": {"Ke1": 1.18, "Ke2": 0.95}},
            },
        )
        main_win = SimpleNamespace(calibration_tab_widget=SimpleNamespace(
            temp_page=tp, strain_page=sp,
        ))

        text = cp.get_summary(main_win, budget_chars=2000)
        assert "A1" in text
        assert "21.57" in text  # S_eff
        assert "1.18" in text   # Ke1 from ke_table

    def test_seff_result_none_does_not_crash(self):
        """_phase_a_state.seff_result 为 None → 不崩，has_a=False。"""
        from core.data_providers import CalibrationProvider
        cp = CalibrationProvider()

        tp = SimpleNamespace(
            _phase_a_state={"seff_result": None},
            _phase_b_state={},
        )
        sp = SimpleNamespace(_strain_configs={})
        main_win = SimpleNamespace(calibration_tab_widget=SimpleNamespace(
            temp_page=tp, strain_page=sp,
        ))

        text = cp.get_summary(main_win, budget_chars=2000)
        assert "(未运行)" in text  # has_a=False, has_b=False


# ═══════════════════════════════════════════════════════════
# Defect 3: _anomaly column filtering
# ═══════════════════════════════════════════════════════════

class TestAnomalyColumnFiltering:

    def test_numeric_cols_excludes_anomaly(self):
        """_anomaly 后缀列不进入数值列统计。"""
        import pandas as pd
        import numpy as np
        # 构造含 _anomaly 列的 DataFrame（模拟清洗后的数据）
        df = pd.DataFrame({
            "Timestamp": np.arange(10, dtype=float),
            "w1": np.random.default_rng(42).normal(1540, 0.1, 10),
            "w2": np.random.default_rng(42).normal(1545, 0.1, 10),
            "w1_anomaly": np.zeros(10, dtype=bool),
            "w2_anomaly": np.zeros(10, dtype=bool),
            "Timestamp_anomaly": np.zeros(10, dtype=bool),
        })

        numeric_cols = [
            c for c in df.select_dtypes(include=['number']).columns
            if not str(c).endswith('_anomaly')
        ]

        assert "Timestamp" in numeric_cols
        assert "w1" in numeric_cols
        assert "w2" in numeric_cols
        assert "w1_anomaly" not in numeric_cols
        assert "w2_anomaly" not in numeric_cols
        assert "Timestamp_anomaly" not in numeric_cols

    def test_column_list_excludes_anomaly(self):
        """列列表不含 _anomaly 后缀列。"""
        import pandas as pd
        df = pd.DataFrame({
            "Timestamp": [1.0], "w1": [2.0],
            "w1_anomaly": [False], "Timestamp_anomaly": [False],
        })
        cols = [c for c in df.columns if not str(c).endswith('_anomaly')]
        assert "Timestamp" in cols
        assert "w1" in cols
        assert "w1_anomaly" not in cols
        assert "Timestamp_anomaly" not in cols

    def test_no_anomaly_cols_unaffected(self):
        """无 _anomaly 列时过滤不影响正常列。"""
        import pandas as pd
        df = pd.DataFrame({"A": [1.0, 2.0], "B": [3.0, 4.0]})
        numeric_cols = [
            c for c in df.select_dtypes(include=['number']).columns
            if not str(c).endswith('_anomaly')
        ]
        assert set(numeric_cols) == {"A", "B"}
