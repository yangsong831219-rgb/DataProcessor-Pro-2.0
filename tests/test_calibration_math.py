"""阶段 2 回归测试 — 标定计算引擎（纯函数，无 UI）

引擎已实现，本阶段移除 xfail 标记，
使所有可立即验证的测试全绿。
"""

from __future__ import annotations

import math
import os
import pytest
import numpy as np
import pandas as pd
import pandas as pd

from .golden.golden_data import (
    STRAIN_GOLDEN,
    TEMP_GOLDEN_S_EFF,
    TEMP_GOLDEN_T_BASE,
    TEMP_GOLDEN_R2,
    TEMP_REGRESSION_ATOL,
    TEMP_TBASE_ATOL,
    TEMP_R2_ATOL,
    STRAIN_K_TOL,
    STRAIN_R2_TOL,
    STRAIN_NL_TOL,
    STRAIN_RP_TOL,
    STRAIN_HY_TOL,
)

# FBG.py 原始温度循环数据文件路径
import os as _os
_TEMP_DATA_FILE = _os.path.join(
    _os.path.dirname(_os.path.abspath(__file__)),
    "golden", "温度循环数据.txt",
)
_TEMP_DATA_FALLBACK = "D:/桌面文件/222/4次温度循环温度系数修订/温度循环数据.txt"
from py.calibration.step_extractor import detect_plateaus
from py.calibration.temperature_calibration import (
    load_continuous,
    assign_setpoints,
    regress_sensitivity,
    decouple,
    compare_given_vs_measured,
)
from py.calibration.strain_calibration import (
    compute_theoretical_strain,
    StrainCalibrationConfig,
    StrainCalibrationResult,
    calibrate_strain,
)

ATOL = 1e-6
RTOL = 1e-4


# ═══════════════════════════════════════════════════════════════════════
# 解耦矩阵 — 引擎已实现，全绿
# ═══════════════════════════════════════════════════════════════════════

class TestDecoupleMath:
    """2×2 解耦矩阵数学正确性"""

    def test_decouple_zero_strain_zero_temp(self):
        """零输入 → 零输出"""
        eps, dT = decouple(0.0, 0.0, 1.2, 26.2, 1.2, 29.15)
        assert eps == pytest.approx(0.0, abs=ATOL)
        assert dT == pytest.approx(0.0, abs=ATOL)

    def test_decouple_pure_strain(self):
        """只有应变信号 → 温度输出≈0"""
        Ke1, KT1 = 1.2, 26.2
        Ke2, KT2 = 1.2, 29.15
        dl1 = Ke1 * 100.0
        dl2 = Ke2 * 100.0
        eps, dT = decouple(dl1, dl2, Ke1, KT1, Ke2, KT2)
        assert eps == pytest.approx(100.0, abs=1e-3)
        assert dT == pytest.approx(0.0, abs=1e-3)

    def test_decouple_pure_temperature(self):
        """只有温度信号 → 应变输出≈0"""
        Ke1, KT1 = 1.2, 26.2
        Ke2, KT2 = 1.2, 29.15
        dl1 = KT1 * 10.0
        dl2 = KT2 * 10.0
        eps, dT = decouple(dl1, dl2, Ke1, KT1, Ke2, KT2)
        assert eps == pytest.approx(0.0, abs=1e-3)
        assert dT == pytest.approx(10.0, abs=1e-3)

    def test_decouple_singular_matrix(self):
        """行列式接近零时不崩溃（返回 NaN）"""
        Ke1, KT1 = 1.0, 2.0
        Ke2, KT2 = 2.0, 4.0
        eps, dT = decouple(100.0, 200.0, Ke1, KT1, Ke2, KT2)
        assert eps is not None
        assert dT is not None

    def test_decouple_consistency_with_models_py(self):
        """与 core/models.py _evaluate_decoupling 公式一致"""
        Ke1, KT1 = 1.2, 26.2
        Ke2, KT2 = 1.2, 29.15
        dl1, dl2 = 500.0, 600.0
        D = Ke1 * KT2 - Ke2 * KT1
        eps_expected = (dl1 * KT2 - dl2 * KT1) / D
        dT_expected = (dl2 * Ke1 - dl1 * Ke2) / D
        eps, dT = decouple(dl1, dl2, Ke1, KT1, Ke2, KT2)
        assert eps == pytest.approx(eps_expected, abs=ATOL)
        assert dT == pytest.approx(dT_expected, abs=ATOL)

    def test_decouple_vectorized(self):
        """向量化解耦 — np.ndarray 输入"""
        Ke1, KT1 = 1.2, 26.2
        Ke2, KT2 = 1.2, 29.15
        dl1 = np.array([0.0, Ke1 * 100.0, KT1 * 10.0])
        dl2 = np.array([0.0, Ke2 * 100.0, KT2 * 10.0])
        eps, dT = decouple(dl1, dl2, Ke1, KT1, Ke2, KT2)
        assert eps == pytest.approx([0.0, 100.0, 0.0], abs=1e-3)
        assert dT == pytest.approx([0.0, 0.0, 10.0], abs=1e-3)


# ═══════════════════════════════════════════════════════════════════════
# 线性回归 — 纯数学
# ═══════════════════════════════════════════════════════════════════════

class TestRegressionMath:
    """np.polyfit 线性回归正确性（引擎调用的底层函数）"""

    def test_perfect_linear(self):
        """完全线性数据 → R²=1.0"""
        x = np.array([10.0, 20.0, 30.0, 40.0, 50.0])
        y = 25.0 * x + 100.0
        slope, intercept = np.polyfit(x, y, 1)
        assert slope == pytest.approx(25.0, abs=ATOL)
        assert intercept == pytest.approx(100.0, abs=ATOL)
        y_pred = slope * x + intercept
        ss_res = np.sum((y - y_pred) ** 2)
        ss_tot = np.sum((y - y.mean()) ** 2)
        r2 = 1.0 - ss_res / ss_tot
        assert r2 == pytest.approx(1.0, abs=ATOL)

    def test_T_base_calculation(self):
        """T_base = -intercept / slope"""
        slope, intercept = 28.5, -285.0
        T_base = -intercept / slope
        assert T_base == pytest.approx(10.0, abs=ATOL)

    def test_noisy_linear(self):
        """带噪声线性数据 → R² > 0.95"""
        np.random.seed(42)
        x = np.linspace(10, 70, 100)
        y = 30.0 * x + 200.0 + np.random.normal(0, 10, 100)
        slope, intercept = np.polyfit(x, y, 1)
        y_pred = slope * x + intercept
        ss_res = np.sum((y - y_pred) ** 2)
        ss_tot = np.sum((y - y.mean()) ** 2)
        r2 = 1.0 - ss_res / ss_tot
        assert r2 > 0.95
        assert slope == pytest.approx(30.0, abs=1.0)

    def test_regress_sensitivity_engine(self):
        """regress_sensitivity 端到端测试"""
        P = pd.DataFrame({
            "T_set": [10, 20, 30, 40, 50, 60, 70],
            "test_d": [
                282.0, 565.0, 848.0, 1131.0, 1414.0, 1697.0, 1980.0,
            ],
        })
        result = regress_sensitivity(P, "test_d", "test")
        assert result["slope"] == pytest.approx(28.3, abs=0.5)
        assert result["r2"] > 0.9999


# ═══════════════════════════════════════════════════════════════════════
# 标定 vs 实测 对比
# ═══════════════════════════════════════════════════════════════════════

class TestCompareGivenVsMeasured:
    """compare_given_vs_measured 单元测试"""

    def test_basic(self):
        """基本对比计算"""
        comp = compare_given_vs_measured("A1-W1", 26.2, 28.5, 1.2)
        assert comp["diff_pm_per_C"] == pytest.approx(2.3)
        assert comp["apparent_strain_ppm_per_C"] == pytest.approx(2.3 / 1.2)

    def test_zero_Ke_graceful(self):
        """Ke=0 时不崩溃"""
        comp = compare_given_vs_measured("X", 10.0, 12.0, 0.0)
        assert np.isnan(comp["apparent_strain_ppm_per_C"])


# ═══════════════════════════════════════════════════════════════════════
# 应变标定 — 黄金值回归 (xfail 直到黄金值填入)
# ═══════════════════════════════════════════════════════════════════════

class TestStrainCalibrationGolden:
    """应变标定引擎黄金值 — 运行标定自补偿2组.xlsx 并断言"""

    _EXCEL = "D:/桌面文件/项目资料/自补偿应变计研发项目/标定自补偿2组.xlsx"
    _LEVELS = [0.0, 0.008, 0.016, 0.024, 0.032, 0.040, 0.048, 0.056, 0.064, 0.073, 0.080]
    _SECTIONS = {"A1": (1, 79), "A2": (79, 156), "B1": (156, 233), "B2": (233, 307)}

    @staticmethod
    def _parse_sensor(df, start, end):
        readings = {1: {}, 2: {}}
        r = start
        while r < end:
            label = str(df.iloc[r, 0]).strip()
            zl = '张拉' in label; th = '退回' in label
            if zl or th:
                cyc = 1 if '1' in label else (2 if '2' in label else 3)
                phase = 'load' if zl else 'unload'
                wl1s, wl2s = [], []
                curr = r
                while curr < end:
                    cl = str(df.iloc[curr, 0]).strip()
                    czl = '张拉' in cl; cth = '退回' in cl
                    if curr != r and cl != 'nan' and (czl or cth):
                        break
                    w1 = df.iloc[curr, 2]; w2 = df.iloc[curr, 3]
                    if not pd.isna(w1): wl1s.append(float(w1))
                    if not pd.isna(w2): wl2s.append(float(w2))
                    curr += 1
                if wl1s: readings.setdefault(1, {}).setdefault(cyc, {})[phase] = wl1s
                if wl2s: readings.setdefault(2, {}).setdefault(cyc, {})[phase] = wl2s
                r = curr
            else:
                r += 1
        return readings

    @pytest.fixture(scope="class")
    def strain_results(self):
        if not os.path.isfile(self._EXCEL):
            pytest.skip(f"Excel文件未找到: {self._EXCEL}")
        df = pd.read_excel(self._EXCEL, sheet_name=0, header=None)
        results = {}
        for name, (s, e) in self._SECTIONS.items():
            rd = self._parse_sensor(df, s, e)
            config = StrainCalibrationConfig(
                gauge_length_mm=80.0, mode="tension_return",
                n_cycles=3, grating_kind="dual_both", levels=self._LEVELS,
            )
            results[name] = calibrate_strain(config, rd)
        return results

    @pytest.mark.parametrize("sensor,grating,key", [
        ("A1", 1, "A1_g1"), ("A2", 1, "A2_g1"),
        ("B1", 1, "B1_g1"), ("B1", 2, "B1_g2"),
        ("B2", 1, "B2_g1"), ("B2", 2, "B2_g2"),
    ])
    def test_golden(self, strain_results, sensor, grating, key):
        golden = STRAIN_GOLDEN[key]
        g = strain_results[sensor].gratings[grating - 1]
        assert g.k_pm_per_ue == pytest.approx(golden["k_pm_per_ue"], abs=STRAIN_K_TOL), (
            f"{key} k: {g.k_pm_per_ue:.4f} vs {golden['k_pm_per_ue']}"
        )
        assert g.R2 == pytest.approx(golden["R2"], abs=STRAIN_R2_TOL), (
            f"{key} R²: {g.R2:.4f} vs {golden['R2']}"
        )
        assert g.nonlinearity_pct_fs == pytest.approx(golden["nonlinearity_pct_fs"], abs=STRAIN_NL_TOL), (
            f"{key} NL: {g.nonlinearity_pct_fs:.2f} vs {golden['nonlinearity_pct_fs']}"
        )
        assert g.repeatability_pct_fs == pytest.approx(golden["repeatability_pct_fs"], abs=STRAIN_RP_TOL), (
            f"{key} RP: {g.repeatability_pct_fs:.2f} vs {golden['repeatability_pct_fs']}"
        )
        assert g.hysteresis_pct_fs == pytest.approx(golden["hysteresis_pct_fs"], abs=STRAIN_HY_TOL), (
            f"{key} HY: {g.hysteresis_pct_fs:.2f} vs {golden['hysteresis_pct_fs']}"
        )


# ═══════════════════════════════════════════════════════════════════════
# 温度标定 — 黄金值回归 (xfail 直到黄金值填入)
# ═══════════════════════════════════════════════════════════════════════

class TestTemperatureCalibrationGolden:
    """温度标定引擎复现 FBG.py 黄金输出

    使用 FBG.py 的同一份原始数据文件，用相同参数运行标定流程，
    断言 S_eff / T_base / R² 与 FBG.py 原始输出一致。

    数据文件查找顺序:
      1. tests/golden/温度循环数据.txt
      2. D:/桌面文件/222/4次温度循环温度系数修订/温度循环数据.txt
    """

    @pytest.fixture(scope="class")
    def temp_data(self):
        """加载温度循环数据，整个类共享一次"""
        data_path = _TEMP_DATA_FILE if _os.path.isfile(_TEMP_DATA_FILE) else _TEMP_DATA_FALLBACK
        if not _os.path.isfile(data_path):
            pytest.skip(f"温度循环数据文件未找到: {_TEMP_DATA_FILE} (或 {_TEMP_DATA_FALLBACK})")
        df, base = load_continuous(
            data_path,
            wavelength_cols=["A1-W1", "A1-W2", "A2-W1", "A2-W2"],
            encoding="gb18030",
            separator="\t",
        )
        return df

    @pytest.fixture(scope="class")
    def temp_plateaus(self, temp_data):
        """检测平台"""
        P = detect_plateaus(
            temp_data,
            wavelength_cols=["A1-W1", "A1-W2", "A2-W1", "A2-W2"],
            rolling_window=25,
            std_percentile=45.0,
            min_plateau_samples=180,
            head_trim_ratio=0.70,
        )
        return P

    @pytest.fixture(scope="class")
    def temp_labeled(self, temp_plateaus):
        """平台映射到设定温度"""
        return assign_setpoints(temp_plateaus, [10, 20, 30, 40, 50, 60, 70])

    @pytest.fixture(scope="class")
    def temp_S_eff(self, temp_labeled):
        """对全部 4 个光栅做回归"""
        S_eff = {}
        for dc in ["A1-W1_d", "A1-W2_d", "A2-W1_d", "A2-W2_d"]:
            if dc in temp_labeled.columns:
                S_eff[dc] = regress_sensitivity(temp_labeled, dc, dc)
        return S_eff

    def test_S_eff_golden(self, temp_S_eff):
        """各光栅实测有效温度灵敏度复现 FBG.py 输出"""
        key_map = {
            "A1-W1_d": "A1-W1", "A1-W2_d": "A1-W2",
            "A2-W1_d": "A2-W1", "A2-W2_d": "A2-W2",
        }
        for dc, grating in key_map.items():
            if dc not in temp_S_eff:
                pytest.skip(f"S_eff 未计算: {dc}")
            actual = temp_S_eff[dc]["slope"]
            expected = TEMP_GOLDEN_S_EFF[grating]
            assert actual == pytest.approx(expected, abs=TEMP_REGRESSION_ATOL), (
                f"{grating}: S_eff 不符 — 期望 {expected:.2f}, 实际 {actual:.2f} pm/°C"
            )

    def test_T_base_golden(self, temp_S_eff):
        """推断基准温度复现 FBG.py 输出"""
        key_map = {
            "A1-W1_d": "A1-W1", "A1-W2_d": "A1-W2",
            "A2-W1_d": "A2-W1", "A2-W2_d": "A2-W2",
        }
        for dc, grating in key_map.items():
            if dc not in temp_S_eff:
                pytest.skip(f"T_base 未计算: {dc}")
            actual = temp_S_eff[dc]["T_base"]
            expected = TEMP_GOLDEN_T_BASE[grating]
            assert actual == pytest.approx(expected, abs=TEMP_TBASE_ATOL), (
                f"{grating}: T_base 不符 — 期望 {expected:.1f}, 实际 {actual:.1f} °C"
            )

    def test_R2_golden(self, temp_S_eff):
        """决定系数 R² 复现 FBG.py 输出"""
        key_map = {
            "A1-W1_d": "A1-W1", "A1-W2_d": "A1-W2",
            "A2-W1_d": "A2-W1", "A2-W2_d": "A2-W2",
        }
        for dc, grating in key_map.items():
            if dc not in temp_S_eff:
                pytest.skip(f"R² 未计算: {dc}")
            actual = temp_S_eff[dc]["r2"]
            expected = TEMP_GOLDEN_R2[grating]
            assert actual == pytest.approx(expected, abs=TEMP_R2_ATOL), (
                f"{grating}: R² 不符 — 期望 {expected:.6f}, 实际 {actual:.6f}"
            )


# ═══════════════════════════════════════════════════════════════════════
# 平台检测 — DataFrame 输入
# ═══════════════════════════════════════════════════════════════════════

class TestStepExtractor:
    """平台检测算法"""

    def test_synthetic_steps(self):
        """人工合成 7 级阶梯信号 → 正确检测平台数"""
        rng = np.random.default_rng(42)
        n_levels = 7
        plateau_len = 300
        noise_std = 0.1  # 极低噪声模拟真实 FBG 温度平台
        level_height = 1000.0
        all_data = []
        for level in range(1, n_levels + 1):
            all_data.append(
                np.full(plateau_len, level * level_height)
                + rng.normal(0, noise_std, plateau_len)
            )
        signal = np.concatenate(all_data)

        df = pd.DataFrame({"A1-W1_d": signal})
        P = detect_plateaus(
            df,
            wavelength_cols=["A1-W1"],
            rolling_window=50,
            std_percentile=90.0,
            min_plateau_samples=100,
            head_trim_ratio=0.10,
        )
        assert len(P) == n_levels, f"期望 {n_levels} 平台，实际 {len(P)}"
        # 每个平台应有合理的索引范围
        for _, row in P.iterrows():
            assert row["idx_end"] > row["idx_start"]
            assert row["mid"] >= row["idx_start"]
            assert row["mid"] <= row["idx_end"]

    def test_min_plateau_filter(self):
        """过短稳定段 (10 点) 被过滤，仅保留 2 个长段"""
        rng = np.random.default_rng(42)
        noise = 0.1
        signal = np.concatenate([
            np.full(200, 1000.0) + rng.normal(0, noise, 200),
            np.full(10, 1500.0) + rng.normal(0, noise, 10),
            np.full(200, 2000.0) + rng.normal(0, noise, 200),
        ])
        df = pd.DataFrame({"A1-W1_d": signal})
        P = detect_plateaus(
            df,
            wavelength_cols=["A1-W1"],
            rolling_window=50,
            std_percentile=90.0,
            min_plateau_samples=50,
            head_trim_ratio=0.10,
        )
        assert len(P) == 2, f"期望 2 平台，实际 {len(P)}"

    def test_multi_column(self):
        """多列波长数据平台检测 — 单平台应正确检出"""
        rng = np.random.default_rng(42)
        n = 500
        signal = np.full(n, 5000.0) + rng.normal(0, 0.1, n)
        df = pd.DataFrame({
            "A1-W1_d": signal,
            "A1-W2_d": signal * 0.8,
        })
        P = detect_plateaus(
            df,
            wavelength_cols=["A1-W1", "A1-W2"],
            min_plateau_samples=100,
        )
        assert len(P) == 1, f"期望 1 平台，实际 {len(P)}"
        # 两列均值应都在平台结果中
        assert "A1-W1_d" in P.columns
        assert "A1-W2_d" in P.columns


# ═══════════════════════════════════════════════════════════════════════
# 理论应变 — 使用引擎函数
# ═══════════════════════════════════════════════════════════════════════

class TestTheoreticalStrain:
    """理论应变计算公式"""

    def test_formula(self):
        """80mm + 0.008mm → 100 με"""
        result = compute_theoretical_strain([0.008], 80.0)
        assert result == pytest.approx([100.0], abs=ATOL)

    def test_scale_with_gauge_length(self):
        """100mm + 0.008mm → 80 με"""
        result = compute_theoretical_strain([0.008], 100.0)
        assert result == pytest.approx([80.0], abs=ATOL)

    def test_multi_levels(self):
        """多级位移"""
        result = compute_theoretical_strain([0.0, 0.01, 0.02, 0.03], 80.0)
        expected = [0.0, 125.0, 250.0, 375.0]
        assert result == pytest.approx(expected, abs=ATOL)

    def test_invalid_gauge_length(self):
        """标距 ≤ 0 抛出异常"""
        with pytest.raises(ValueError):
            compute_theoretical_strain([0.01], 0.0)
        with pytest.raises(ValueError):
            compute_theoretical_strain([0.01], -10.0)


# ═══════════════════════════════════════════════════════════════════════
# 应变标定 — 集成测试
# ═══════════════════════════════════════════════════════════════════════

class TestStrainCalibrationIntegration:
    """应变标定端到端测试"""

    def test_single_grat_tension_only(self):
        """单栅、只张拉、单循环 → 正确计算灵敏度"""
        config = StrainCalibrationConfig(
            gauge_length_mm=80.0,
            mode="tension_only",
            n_cycles=1,
            grating_kind="single",
            levels=[0.0, 0.01, 0.02, 0.03],
        )
        eps_theory = compute_theoretical_strain(config.levels, config.gauge_length_mm)
        # 模拟波长: zero=1550nm, k=1.2 pm/με
        k = 1.2  # pm/με
        # 波长 = 基准 + k*eps/1000 (nm), 引擎内部转 pm (*1000)
        wavelengths = [1550.0 + k * e / 1000.0 for e in eps_theory]
        readings = {
            1: {1: {"load": wavelengths}},
        }
        result = calibrate_strain(config, readings)
        assert len(result.gratings) == 1
        g = result.gratings[0]
        assert g.k_pm_per_ue == pytest.approx(k, rel=0.01)
        assert g.R2 > 0.999

    def test_single_cycle_minimal(self):
        """单循环：不报重复性/迟滞"""
        config = StrainCalibrationConfig(
            gauge_length_mm=80.0,
            mode="tension_only",
            n_cycles=1,
            grating_kind="single",
            levels=[0.0, 0.01],
        )
        readings = {
            1: {1: {"load": [1550.0, 1550.012]}},
        }
        result = calibrate_strain(config, readings)
        g = result.gratings[0]
        assert not np.isnan(g.k_pm_per_ue)
        assert np.isnan(g.repeatability_pct_fs)  # 单循环无法计算
        assert np.isnan(g.hysteresis_pct_fs)

    def test_dual_both_extra_indicators(self):
        """dual_both → 双栅平均与应变差"""
        k1, k2 = 1.2, 0.8
        levels = [0.0, 0.01, 0.02]
        eps_theory = compute_theoretical_strain(levels, 80.0)
        wl1 = [1550.0 + k1 * e / 1000.0 for e in eps_theory]
        wl2 = [1552.0 + k2 * e / 1000.0 for e in eps_theory]
        config = StrainCalibrationConfig(
            gauge_length_mm=80.0,
            mode="tension_only",
            n_cycles=1,
            grating_kind="dual_both",
            levels=levels,
        )
        readings = {
            1: {1: {"load": wl1}},
            2: {1: {"load": wl2}},
        }
        result = calibrate_strain(config, readings)
        assert len(result.gratings) == 2
        assert result.dual_avg_strain is not None
        assert result.dual_diff_strain is not None
        assert len(result.dual_avg_strain) == len(eps_theory)
        assert len(result.dual_diff_strain) == len(eps_theory)

    def test_empty_readings_no_crash(self):
        """空读数输入不崩溃"""
        config = StrainCalibrationConfig(
            gauge_length_mm=80.0,
            mode="tension_only",
            n_cycles=1,
            grating_kind="single",
            levels=[],
        )
        readings = {}
        result = calibrate_strain(config, readings)
        assert len(result.gratings) == 1
        # 空数据 → 所有指标为 NaN
        g = result.gratings[0]
        assert np.isnan(g.k_pm_per_ue)

    def test_multi_cycle_repeatability(self):
        """多循环时计算重复性"""
        config = StrainCalibrationConfig(
            gauge_length_mm=80.0,
            mode="tension_only",
            n_cycles=3,
            grating_kind="single",
            levels=[0.0, 0.01, 0.02],
        )
        k = 1.2
        eps_theory = compute_theoretical_strain(config.levels, config.gauge_length_mm)
        readings = {1: {}}
        for cycle in range(1, 4):
            wl = [1550.0 + k * e / 1000.0 for e in eps_theory]
            readings[1][cycle] = {"load": wl}
        result = calibrate_strain(config, readings)
        g = result.gratings[0]
        assert g.k_pm_per_ue == pytest.approx(k, rel=0.01)
        # 重复性应计算且 ≥ 0
        assert not np.isnan(g.repeatability_pct_fs)
        assert g.repeatability_pct_fs >= 0.0

    def test_tension_return_hysteresis(self):
        """张拉+退回模式计算迟滞"""
        levels = [0.0, 0.01, 0.02]
        config = StrainCalibrationConfig(
            gauge_length_mm=80.0,
            mode="tension_return",
            n_cycles=1,
            grating_kind="single",
            levels=levels,
        )
        k = 1.2
        eps_theory = compute_theoretical_strain(levels, config.gauge_length_mm)
        load_wl = [1550.0 + k * e / 1000.0 for e in eps_theory]
        # 退回时略有偏移 (模拟迟滞)
        unload_wl = [1550.0 + k * e / 1000.0 + 0.002 for e in eps_theory]
        readings = {
            1: {1: {"load": load_wl, "unload": unload_wl}},
        }
        result = calibrate_strain(config, readings)
        g = result.gratings[0]
        assert not np.isnan(g.hysteresis_pct_fs)
        assert g.hysteresis_pct_fs >= 0.0
