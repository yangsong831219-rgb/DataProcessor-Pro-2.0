"""Data provider tests — DataFile/Cleaning/Analysis/Calibration + registry + Ke + features."""

from __future__ import annotations
import json
import os
import sys
from types import SimpleNamespace
import pytest


def _make_fake_metrics(**overrides):
    from utils.compensation_metrics import CompensationMetrics
    defaults = dict(
        sensor="A1", residual_sigma=12.3, repeatability=5.0,
        hysteresis_max=8.0, noise_floor=1.5, temp_sensitivity_max=0.3,
        worst_case_single=4.0, low_confidence=False, fs=1000,
        comp_form="poly", poly_order=2,
        residual_sigma_pct_fs=1.23, repeatability_pct_fs=0.5,
        hysteresis_max_pct_fs=0.8, noise_floor_pct_fs=0.15,
        worst_case_single_pct_fs=0.4,
    )
    defaults.update(overrides)
    return CompensationMetrics(**defaults)


def _make_fake_grade(sensor, grade, passed, reasons):
    from utils.compensation_metrics import SensorGrade
    return SensorGrade(sensor=sensor, grade=grade, passed=passed, reasons=reasons)


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

    ct = SimpleNamespace(temperature_page=tp)
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
        assert not AnalysisProvider().is_available(SimpleNamespace(sensor_results={}))

    def test_available_with_results(self):
        from core.data_providers import AnalysisProvider
        assert AnalysisProvider().is_available(SimpleNamespace(sensor_results={"A": [1, 2, 3]}))

    def test_summary_has_features(self):
        import numpy as np
        from core.data_providers import AnalysisProvider
        vals = np.linspace(100, 120, 100).tolist()
        s = AnalysisProvider().get_summary(SimpleNamespace(sensor_results={"FBG_A1": vals}))
        assert "FBG_A1" in s
        assert "110.000" in s
        assert "CV" in s or "一般" in s or "波动" in s

    def test_summary_has_drift(self):
        import numpy as np
        from core.data_providers import AnalysisProvider
        s = AnalysisProvider().get_summary(SimpleNamespace(sensor_results={"S1": np.linspace(0, 100, 200).tolist()}))
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

    def test_five_providers(self):
        from core.data_providers import get_all_providers
        assert len(get_all_providers()) == 5

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
        assert len(names) == 5
        assert "data" in names[0].lower() or "file" in names[0].lower() or "数据" in names[0]
        assert "clean" in names[1].lower() or "清洗" in names[1]
        assert "analysis" in names[2].lower() or "分析" in names[2]
        assert "compare" in names[3].lower() or "对比" in names[3]
        assert "calibration" in names[4].lower() or "标定" in names[4]


# ═══════════════════════════════════════════════════════════
# Ke Precision
# ═══════════════════════════════════════════════════════════

class TestKePrecision:

    def test_strain_ke_used(self):
        from core.data_providers import _get_strain_ke
        sp = SimpleNamespace(_phase_b_state={"A1": {"ke_results": {"Ke1": 1.1814, "Ke2": 0.0008}}})
        ct = SimpleNamespace(strain_page=sp)
        mw = SimpleNamespace(calibration_tab_widget=ct)
        ke = _get_strain_ke(mw, "A1")
        assert ke is not None
        assert abs(ke['Ke1'] - 1.1814) < 0.0001
        assert abs(ke['Ke2'] - 0.0008) < 0.0001

    def test_no_strain_returns_none(self):
        from core.data_providers import _get_strain_ke
        assert _get_strain_ke(SimpleNamespace(calibration_tab_widget=None), "A1") is None


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
