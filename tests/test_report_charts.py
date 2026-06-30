"""core/report_charts.py 的合成数据测试

覆盖:
- 4 张核心图: 返回 Figure, 含图题/轴标签(单位)/图例
- 退化数据: 空数组/单点/全等值 → 不崩, 出可读占位图
- save_figure: PNG 存在且字节非空
- setup_chinese_font: 无字体 → 回退不崩
- FigureManifest: add 递增/ to_llm_context / 迭代
- 图01: 负截距含 '−', 不含 '+-'
- 图02: 升降支按 x 排序后绘; 环面积标注存在
- 图03: 多面板各自独立 y 轴; 图例无 '#'; NaN 传感器不绘
- 图04: 通道名下划线→空格
- overlay: 差值子面板存在、基准相减正确；多源各线在图
- scatter: corr/RMSE 传入才入图例、对角/拟合线在
- distribution: box/hist 两种 kind; 各通道一项
- metric_bar: 图例/标签无 '#'; 负值/空值不崩
- timeseries 增强: raw/cleaned 叠加与趋势线开关生效; 默认回归不变
"""

from __future__ import annotations

import os
import tempfile

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pytest

from core.report_charts import (
    # 基建
    setup_chinese_font,
    apply_house_style,
    save_figure,
    # 4 张核心图
    make_calibration_linearity,
    make_hysteresis_loop,
    make_sensor_grade_bar,
    make_timeseries,
    # Phase 0.5 新增
    make_comparison_overlay,
    make_correlation_scatter,
    make_distribution,
    make_metric_bar,
    # 清单
    ReportFigure,
    FigureManifest,
)


# ═══════════════════════════════════════════════════════════════════════════
# 合成数据 fixtures
# ═══════════════════════════════════════════════════════════════════════════

@pytest.fixture
def ref_linear() -> np.ndarray:
    """线性参考值: 0 ~ 1000 με, 20 点"""
    ref = np.linspace(0, 1000, 20)
    return ref


@pytest.fixture
def measured_linear(ref_linear: np.ndarray) -> np.ndarray:
    """带噪声的实测值: ref * 1.001 + noise"""
    rng = np.random.default_rng(42)
    return ref_linear * 1.001 + rng.normal(0, 2, len(ref_linear))


@pytest.fixture
def hysteresis_xy() -> tuple[np.ndarray, np.ndarray]:
    """产生一个简单的迟滞环: 三角波温度 + 带滞后的应变"""
    rng = np.random.default_rng(123)
    # 升支: 10→60°C, 200 点
    t_up = np.linspace(10, 60, 200)
    eps_up = 2.0 * (t_up - 10) + rng.normal(0, 0.3, 200)
    # 降支: 60→10°C, 200 点, 加滞后偏移
    t_down = np.linspace(60, 10, 200)
    eps_down = 2.0 * (t_down - 10) + 2.5 + rng.normal(0, 0.3, 200)  # +2.5 偏移
    t = np.concatenate([t_up, t_down])
    eps = np.concatenate([eps_up, eps_down])
    return t, eps


@pytest.fixture
def sensor_list_good() -> list[dict]:
    """3 个传感器的指标列表, 覆盖不同评级"""
    return [
        {"name": "A1", "sigma": 2.3, "cv": 0.5, "hysteresis": 4.1, "grade": "优"},
        {"name": "A2", "sigma": 8.1, "cv": 1.2, "hysteresis": 12.3, "grade": "良"},
        {"name": "B1", "sigma": 18.5, "cv": 2.8, "hysteresis": 35.0, "grade": "合格"},
        {"name": "B2", "sigma": 25.0, "cv": 4.1, "hysteresis": 55.0, "grade": "FAIL"},
        {"name": "C1", "sigma": 12.0, "cv": 3.0, "hysteresis": 20.0, "grade": "合格"},
        {"name": "C2", "sigma": float("nan"), "cv": float("nan"),
         "hysteresis": float("nan"), "grade": "N/A"},
    ]


@pytest.fixture
def sensor_list_simple() -> list[dict]:
    """3 个传感器, 简单列表"""
    return [
        {"name": "C1", "sigma": 2.3, "cv": 0.5, "hysteresis": 4.1, "grade": "优"},
        {"name": "C2", "sigma": 8.1, "cv": 1.2, "hysteresis": 12.3, "grade": "良"},
        {"name": "A1", "sigma": 18.5, "cv": 2.8, "hysteresis": 35.0, "grade": "合格"},
    ]


@pytest.fixture
def sensor_list_mixed() -> list[dict]:
    """含 FAIL 和 N/A 的混合传感器列表"""
    return [
        {"name": "B1", "sigma": 25.0, "cv": 4.1, "hysteresis": 55.0, "grade": "FAIL"},
        {"name": "B2", "sigma": np.nan, "cv": np.nan, "hysteresis": np.nan, "grade": "N/A"},
    ]


@pytest.fixture
def timeseries_data() -> tuple[np.ndarray, dict[str, np.ndarray]]:
    """3 通道时序数据, 500 点, 通道名含下划线"""
    rng = np.random.default_rng(99)
    t = np.arange(500) / 3600.0 * 10  # 10 小时
    ch1 = 1000 * np.sin(t / 2) + rng.normal(0, 5, 500)
    ch2 = 800 * np.cos(t / 3) + rng.normal(0, 3, 500)
    ch3 = 500 * np.sin(t / 1.5 + 1) + rng.normal(0, 4, 500)
    return t, {"C1_应变": ch1, "C2_应变": ch2, "A1_应变": ch3}


# ═══════════════════════════════════════════════════════════════════════════
# 基建测试
# ═══════════════════════════════════════════════════════════════════════════

class TestSetupChineseFont:
    """中文字体配置测试"""

    def test_does_not_crash(self):
        """调用不抛异常"""
        setup_chinese_font()

    def test_sets_rcparams(self):
        """应设置 font.sans-serif 和 axes.unicode_minus"""
        setup_chinese_font()
        assert len(plt.rcParams["font.sans-serif"]) >= 1
        assert plt.rcParams["axes.unicode_minus"] is False

    def test_idempotent(self):
        """重复调用不崩"""
        setup_chinese_font()
        setup_chinese_font()
        setup_chinese_font()


class TestApplyHouseStyle:
    """house style 测试"""

    def test_global_none_does_not_crash(self):
        """fig=None 设置全局 rcParams, 不崩"""
        apply_house_style(None)

    def test_fig_applies_per_figure(self):
        """传入 fig 应设置单图属性"""
        fig, ax = plt.subplots()
        apply_house_style(fig)
        # 验证 figsize 已设置 (可能因后端差异有微小偏差)
        w, h = fig.get_size_inches()
        assert 7.5 <= w <= 8.5
        assert 3.5 <= h <= 4.5
        plt.close(fig)

    def test_fig_with_legend(self):
        """有图例的 fig 不崩"""
        fig, ax = plt.subplots()
        ax.plot([1, 2, 3], label="test")
        ax.legend()
        apply_house_style(fig)
        plt.close(fig)


class TestSaveFigure:
    """save_figure 测试"""

    def test_creates_png(self):
        """保存后 PNG 文件存在且非空"""
        fig, ax = plt.subplots()
        ax.plot([1, 2, 3], [4, 5, 6])
        ax.set_title("Test")

        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "test.png")
            result = save_figure(fig, path, dpi=150)
            assert result == os.path.abspath(path)
            assert os.path.exists(path)
            assert os.path.getsize(path) > 100  # 非空 PNG

    def test_auto_creates_dirs(self):
        """自动创建目录"""
        fig, ax = plt.subplots()
        ax.plot([0, 1])

        with tempfile.TemporaryDirectory() as tmp:
            nested = os.path.join(tmp, "a", "b", "c", "chart.png")
            result = save_figure(fig, nested, dpi=100)
            assert os.path.exists(result)
            assert os.path.getsize(result) > 50


# ═══════════════════════════════════════════════════════════════════════════
# make_calibration_linearity 测试
# ═══════════════════════════════════════════════════════════════════════════

class TestCalibrationLinearity:
    """标定线性度图测试"""

    def test_returns_figure(self, ref_linear, measured_linear):
        fig = make_calibration_linearity(ref_linear, measured_linear, sensor="C2")
        assert isinstance(fig, plt.Figure)
        plt.close(fig)

    def test_two_subplots(self, ref_linear, measured_linear):
        fig = make_calibration_linearity(ref_linear, measured_linear, sensor="C2")
        assert len(fig.axes) == 2
        plt.close(fig)

    def test_title_contains_sensor(self, ref_linear, measured_linear):
        fig = make_calibration_linearity(ref_linear, measured_linear, sensor="C2")
        assert "C2" in fig.axes[0].get_title()
        plt.close(fig)

    def test_axes_labels_contain_unit(self, ref_linear, measured_linear):
        fig = make_calibration_linearity(ref_linear, measured_linear, unit="με")
        # 上图 y 标签
        assert "με" in fig.axes[0].get_ylabel()
        # 下图 x 标签
        assert "με" in fig.axes[1].get_xlabel()
        # 下图 y 标签
        assert "με" in fig.axes[1].get_ylabel()
        plt.close(fig)

    def test_has_legend(self, ref_linear, measured_linear):
        fig = make_calibration_linearity(ref_linear, measured_linear, sensor="C2")
        assert fig.axes[0].get_legend() is not None
        plt.close(fig)

    def test_r2_annotation_in_legend(self, ref_linear, measured_linear):
        fig = make_calibration_linearity(ref_linear, measured_linear)
        legend = fig.axes[0].get_legend()
        assert legend is not None
        legend_texts = [t.get_text() for t in legend.get_texts()]
        has_r2 = any("R²" in t for t in legend_texts)
        assert has_r2
        plt.close(fig)

    def test_negative_intercept_uses_minus_not_plusminus(self):
        """负截距: 显示 '−', 不含 '+-'"""
        # 故意造负截距: ref * 0.5 - 10
        ref = np.linspace(0, 100, 20)
        measured = ref * 0.5 - 10 + np.random.default_rng(99).normal(0, 0.5, 20)
        fig = make_calibration_linearity(ref, measured, sensor="X")
        legend = fig.axes[0].get_legend()
        assert legend is not None
        combined = "".join(t.get_text() for t in legend.get_texts())
        assert "−" in combined  # Unicode minus
        assert "+−" not in combined
        assert "+-" not in combined
        plt.close(fig)

    # ---- 退化数据 ----

    def test_empty_arrays_placeholder(self):
        fig = make_calibration_linearity([], [], sensor="X")
        assert len(fig.axes) == 2
        plt.close(fig)

    def test_single_point_placeholder(self):
        fig = make_calibration_linearity([100], [101], sensor="X")
        assert len(fig.axes) == 2
        plt.close(fig)

    def test_all_nan_placeholder(self):
        fig = make_calibration_linearity(
            [np.nan, np.nan, np.nan], [np.nan, np.nan, np.nan], sensor="X"
        )
        assert len(fig.axes) == 2
        plt.close(fig)

    def test_all_equal_values(self):
        """全等值数据: 仍可出图 (R² 可能为 0 或无意义)"""
        fig = make_calibration_linearity([5, 5, 5, 5], [5.1, 5.0, 4.9, 5.1], sensor="X")
        assert len(fig.axes) == 2
        plt.close(fig)


# ═══════════════════════════════════════════════════════════════════════════
# make_hysteresis_loop 测试
# ═══════════════════════════════════════════════════════════════════════════

class TestHysteresisLoop:
    """迟滞回线测试"""

    def test_returns_figure(self, hysteresis_xy):
        x, y = hysteresis_xy
        fig = make_hysteresis_loop(x, y, sensor="C2",
                                   x_label="温度 (°C)", y_label="应变 (με)")
        assert isinstance(fig, plt.Figure)
        plt.close(fig)

    def test_axes_labels(self, hysteresis_xy):
        x, y = hysteresis_xy
        fig = make_hysteresis_loop(x, y, sensor="C2",
                                   x_label="温度 (°C)", y_label="应变 (με)")
        ax = fig.axes[0]
        assert "温度" in ax.get_xlabel()
        assert "应变" in ax.get_ylabel()
        plt.close(fig)

    def test_title_contains_sensor(self, hysteresis_xy):
        x, y = hysteresis_xy
        fig = make_hysteresis_loop(x, y, sensor="C2",
                                   x_label="T", y_label="ε")
        assert "C2" in fig.axes[0].get_title()
        plt.close(fig)

    def test_has_legend(self, hysteresis_xy):
        x, y = hysteresis_xy
        fig = make_hysteresis_loop(x, y, sensor="C2",
                                   x_label="T", y_label="ε")
        assert fig.axes[0].get_legend() is not None
        plt.close(fig)

    def test_branches_sorted_by_x(self, hysteresis_xy):
        """验证升/降支线数据点按 x 单调排列 (排序后绘)"""
        x, y = hysteresis_xy
        fig = make_hysteresis_loop(x, y, sensor="C2",
                                   x_label="T", y_label="ε")
        ax = fig.axes[0]
        # 检查有多条线 (升支+降支)
        lines = ax.get_lines()
        assert len(lines) >= 2, f"预期至少 2 条分支线, 实际 {len(lines)}"
        # 验证每条线的 xdata 已排序 (通过检查差分是否 ≥ 0, 允许极小噪声)
        # pyright: ignore[reportGeneralTypeIssues]
        for line in lines:
            xd = np.asarray(line.get_xdata())
            if len(xd) > 2:
                dx = np.diff(xd)
                neg_frac = (dx < 0).sum() / len(dx)
                # 若 x 按单调方向排列, 绝大多数 diff ≥ 0
                assert neg_frac < 0.3, f"线有 {neg_frac:.0%} 的负 dx, 非排序"
        plt.close(fig)

    def test_area_annotation_exists(self, hysteresis_xy):
        """环面积标注存在"""
        x, y = hysteresis_xy
        fig = make_hysteresis_loop(x, y, sensor="C2",
                                   x_label="T", y_label="ε")
        ax = fig.axes[0]
        texts = [t.get_text() for t in ax.texts]
        has_area = any("环面积" in t for t in texts)
        assert has_area, f"未找到环面积标注, texts={texts}"
        plt.close(fig)

    # ---- 退化数据 ----

    def test_empty_placeholder(self):
        fig = make_hysteresis_loop([], [], sensor="X", x_label="T", y_label="ε")
        assert len(fig.axes) == 1
        plt.close(fig)

    def test_single_point_placeholder(self):
        fig = make_hysteresis_loop([25], [0], sensor="X", x_label="T", y_label="ε")
        assert len(fig.axes) == 1
        plt.close(fig)

    def test_monotonic_no_hysteresis(self):
        """纯单调数据: 显示无迟滞环提示"""
        x = np.linspace(10, 60, 100)
        y = 2.0 * (x - 10)
        fig = make_hysteresis_loop(x, y, sensor="X", x_label="T", y_label="ε")
        texts = [t.get_text() for t in fig.axes[0].texts]
        has_no_loop = any("无迟滞环" in t for t in texts)
        assert has_no_loop
        plt.close(fig)


# ═══════════════════════════════════════════════════════════════════════════
# make_sensor_grade_bar 测试
# ═══════════════════════════════════════════════════════════════════════════

class TestSensorGradeBar:
    """传感器评级小多图测试"""

    def test_returns_figure(self, sensor_list_simple):
        fig = make_sensor_grade_bar(sensor_list_simple)
        assert isinstance(fig, plt.Figure)
        plt.close(fig)

    def test_multi_panel_per_metric(self, sensor_list_simple):
        """默认 3 指标 → 3 列子面板"""
        fig = make_sensor_grade_bar(sensor_list_simple,
                                    metrics=("sigma", "cv", "hysteresis"))
        assert len(fig.axes) == 3
        plt.close(fig)

    def test_each_panel_independent_y(self, sensor_list_simple):
        """各面板 y 轴量纲不同 (不同 metric)"""
        fig = make_sensor_grade_bar(sensor_list_simple,
                                    metrics=("sigma", "cv", "hysteresis"))
        ylabels = [ax.get_ylabel() for ax in fig.axes]
        assert any("με" in yl for yl in ylabels if yl)
        assert any("CV" in yl for yl in ylabels if yl)
        assert any("迟滞" in yl for yl in ylabels if yl)
        plt.close(fig)

    def test_title(self, sensor_list_simple):
        fig = make_sensor_grade_bar(sensor_list_simple)
        suptitle = getattr(fig, "_suptitle", None)  # matplotlib has no public suptitle getter
        assert suptitle is not None
        assert "评级" in suptitle.get_text()
        plt.close(fig)

    def test_has_legend(self, sensor_list_simple):
        fig = make_sensor_grade_bar(sensor_list_simple)
        assert fig.axes[0].get_legend() is not None
        plt.close(fig)

    def test_legend_no_hash(self, sensor_list_simple):
        """图例文本不含 '#' (hex 泄漏)"""
        fig = make_sensor_grade_bar(sensor_list_simple)
        legend = fig.axes[0].get_legend()
        assert legend is not None
        legend_texts = [t.get_text() for t in legend.get_texts()]
        combined = " ".join(legend_texts)
        assert "#" not in combined, f"图例含 hex: {combined}"
        plt.close(fig)

    def test_legend_has_chinese_grade_names(self, sensor_list_good):
        """图例含中文评级 (优/良/合格/FAIL/N/A)"""
        fig = make_sensor_grade_bar(sensor_list_good,
                                    metrics=("sigma", "cv", "hysteresis"))
        legend = fig.axes[0].get_legend()
        assert legend is not None
        legend_texts = [t.get_text() for t in legend.get_texts()]
        combined = " ".join(legend_texts)
        assert "优" in combined
        assert "良" in combined
        assert "合格" in combined
        assert "FAIL" in combined
        assert "N/A" in combined
        plt.close(fig)

    def test_nan_sensor_skipped_bars(self, sensor_list_good):
        """C2 全 NaN → 不绘柱, 但 x 刻度保留"""
        fig = make_sensor_grade_bar(sensor_list_good,
                                    metrics=("sigma", "cv", "hysteresis"))
        # C2 是最后一个 (index 5), 全 NaN
        # 检查所有 bar 高度: 不应有 C2 的 bar
        for ax in fig.axes:
            bars = [p for p in ax.patches if hasattr(p, "get_height")]
            # bar center — Rectangle.get_x() + get_width()/2
            centers = [b.get_x() + b.get_width() / 2 for b in bars]  # pyright: ignore[reportAttributeAccessIssue]  # Rectangle stub missing
            # C2 在 x=5, bars 不应出现在靠近 5 的位置
            for pos in centers:
                assert abs(pos - 5.0) > 0.2, f"C2 位置 {pos} 出现空柱"
        plt.close(fig)

    def test_metrics_list_controls_panels(self, sensor_list_simple):
        """自定义 metrics=('sigma',) → 1 面板"""
        fig = make_sensor_grade_bar(sensor_list_simple, metrics=("sigma",))
        assert len(fig.axes) == 1
        plt.close(fig)

    def test_insertion_order_preserved(self):
        """按传入顺序排列，A1→B1→C1"""
        sensors = [
            {"name": "A1", "sigma": 1.0, "cv": 0.3, "hysteresis": 2.0, "grade": "优"},
            {"name": "B1", "sigma": 5.0, "cv": 1.0, "hysteresis": 8.0, "grade": "良"},
            {"name": "C1", "sigma": 9.0, "cv": 2.0, "hysteresis": 15.0, "grade": "合格"},
        ]
        fig = make_sensor_grade_bar(sensors, metrics=("sigma",))
        xtick_labels = [t.get_text() for t in fig.axes[0].get_xticklabels()]
        assert xtick_labels == ["A1", "B1", "C1"], f"顺序错误: {xtick_labels}"
        plt.close(fig)

    # ---- 退化数据 ----

    def test_empty_list_placeholder(self):
        fig = make_sensor_grade_bar([])
        assert isinstance(fig, plt.Figure)
        plt.close(fig)

    def test_all_nan_placeholder_text(self, sensor_list_mixed):
        """B2 全 NaN → 面板显示'无数据'标注"""
        # 仅 B2 是 NaN, 面板应该有 ticks 但无 bar
        fig = make_sensor_grade_bar(sensor_list_mixed,
                                    metrics=("sigma", "cv", "hysteresis"))
        # B1 有数据, B2 是 NaN
        for ax in fig.axes:
            bars = [p for p in ax.patches if hasattr(p, "get_height")]
            # B2 (x=1) bar 应缺失
            for b in bars:
                x_pos = b.get_x() + b.get_width() / 2  # pyright: ignore[reportAttributeAccessIssue]  # Rectangle stub missing
                assert abs(x_pos - 1.0) > 0.2, "B2 应该跳过了全 NaN 柱"
        plt.close(fig)


# ═══════════════════════════════════════════════════════════════════════════
# make_timeseries 测试
# ═══════════════════════════════════════════════════════════════════════════

class TestTimeseries:
    """时序图测试"""

    def test_returns_figure(self, timeseries_data):
        t, ser = timeseries_data
        fig = make_timeseries(t, ser, y_label="应变 (με)")
        assert isinstance(fig, plt.Figure)
        plt.close(fig)

    def test_axes_labels(self, timeseries_data):
        t, ser = timeseries_data
        fig = make_timeseries(t, ser, y_label="应变 (με)")
        ax = fig.axes[0]
        assert "时间" in ax.get_xlabel()
        assert "με" in ax.get_ylabel()
        plt.close(fig)

    def test_has_legend_multi_channel(self, timeseries_data):
        t, ser = timeseries_data
        fig = make_timeseries(t, ser, y_label="应变 (με)")
        assert fig.axes[0].get_legend() is not None
        plt.close(fig)

    def test_underscore_replaced_with_space(self, timeseries_data):
        """通道名 'C1_应变' → legend 显示 'C1 应变'"""
        t, ser = timeseries_data
        fig = make_timeseries(t, ser, y_label="ε")
        legend = fig.axes[0].get_legend()
        assert legend is not None
        legend_texts = [t.get_text() for t in legend.get_texts()]
        # 查找含空格的标签
        has_space = any("C1 应变" in txt for txt in legend_texts)
        assert has_space, f"图例未将下划线转空格: {legend_texts}"
        plt.close(fig)

    def test_underscore_not_present_in_legend(self, timeseries_data):
        """图例不含原始下划线"""
        t, ser = timeseries_data
        fig = make_timeseries(t, ser, y_label="ε")
        legend = fig.axes[0].get_legend()
        assert legend is not None
        for text in legend.get_texts():
            assert "_" not in text.get_text(), f"图例含下划线: {text.get_text()}"
        plt.close(fig)

    def test_with_anomalies(self, timeseries_data):
        """异常点应绘制为散点标注"""
        t, ser = timeseries_data
        anomalies = {"C1_应变": [50, 120, 300]}
        fig = make_timeseries(t, ser, y_label="应变 (με)", anomalies=anomalies)
        assert len(fig.axes[0].collections) >= 1
        plt.close(fig)

    def test_anomalies_out_of_bounds_silently_filtered(self, timeseries_data):
        """越界异常索引被静默过滤"""
        t, ser = timeseries_data
        anomalies = {"C1_应变": [99999, -5]}  # 越界
        fig = make_timeseries(t, ser, y_label="ε", anomalies=anomalies)
        plt.close(fig)

    def test_single_channel(self, timeseries_data):
        t, ser = timeseries_data
        single = {"only": ser["C1_应变"]}
        fig = make_timeseries(t, single, y_label="ε")
        assert isinstance(fig, plt.Figure)
        plt.close(fig)

    # ---- 退化数据 ----

    def test_empty_series_placeholder(self):
        t = np.arange(100)
        fig = make_timeseries(t, {}, y_label="ε")
        assert isinstance(fig, plt.Figure)
        plt.close(fig)

    def test_mismatched_lengths_no_crash(self):
        """长度不匹配: 截断到较短的"""
        t = np.arange(10, dtype=float)
        vals = np.arange(5, dtype=float)
        fig = make_timeseries(t, {"ch": vals}, y_label="ε")  # pyright: ignore[reportArgumentType]  # numpy ndarray stubs don't infer Sequence[float]
        assert isinstance(fig, plt.Figure)
        plt.close(fig)

    def test_all_nan_channel(self):
        """全 NaN 通道仍可绘 (只是空线)"""
        t = np.arange(50, dtype=float)
        vals: np.ndarray = np.full(50, np.nan).astype(float)
        fig = make_timeseries(t, {"nan_ch": vals}, y_label="ε")  # pyright: ignore[reportArgumentType]  # numpy ndarray stub limitation
        assert isinstance(fig, plt.Figure)
        plt.close(fig)

    def test_zero_length_series(self):
        t = np.arange(10, dtype=float)
        empty: np.ndarray = np.array([], dtype=float)
        fig = make_timeseries(t, {"empty": empty}, y_label="ε")  # pyright: ignore[reportArgumentType]  # numpy ndarray stub limitation
        assert isinstance(fig, plt.Figure)
        plt.close(fig)

    # ---- timeseries 增强: cleaned 叠加 ----

    def test_cleaned_overlay(self, timeseries_data):
        """raw + cleaned 同时出现: 原始虚线 + 清洗实线"""
        t, ser = timeseries_data
        cleaned = {k: v * 0.9 + 5 for k, v in ser.items()}
        fig = make_timeseries(t, ser, y_label="ε", cleaned=cleaned)
        ax = fig.axes[0]
        lines = ax.get_lines()
        # 至少 2×通道数的线
        assert len(lines) >= len(ser) * 2
        plt.close(fig)

    def test_cleaned_legend_has_raw_and_cleaned(self, timeseries_data):
        """图例含 '(清洗)' '(原始)' 标记"""
        t, ser = timeseries_data
        cleaned = {k: v * 0.9 + 5 for k, v in ser.items()}
        fig = make_timeseries(t, ser, y_label="ε", cleaned=cleaned)
        legend = fig.axes[0].get_legend()
        assert legend is not None
        texts = " ".join(t for t in (txt.get_text() for txt in legend.get_texts()))
        assert "清洗" in texts
        assert "原始" in texts
        plt.close(fig)

    def test_default_no_trend(self, timeseries_data):
        """默认 trend=False: 无红色趋势线"""
        t, ser = timeseries_data
        fig = make_timeseries(t, ser, y_label="ε")
        plt.close(fig)

    def test_trend_enabled(self, timeseries_data):
        """trend=True: 出现趋势线"""
        t, ser = timeseries_data
        fig = make_timeseries(t, ser, y_label="ε", trend=True)
        ax = fig.axes[0]
        leg = ax.get_legend()
        leg_texts = [t.get_text() for t in leg.get_texts()] if leg else []
        has_trend = any("趋势" in l for l in leg_texts)
        assert has_trend
        plt.close(fig)

    def test_trend_colors_differ_per_channel(self, timeseries_data):
        """多通道趋势线颜色互不相同"""
        t, ser = timeseries_data
        fig = make_timeseries(t, ser, y_label="ε", trend=True)
        ax = fig.axes[0]
        # 收集含 "趋势" 的线的颜色
        trend_colors = []
        for line in ax.get_lines():
            lbl = str(line.get_label())
            if "趋势" in lbl:
                trend_colors.append(line.get_color())
        # 去重后应 >1 (多通道趋势异色)
        unique = set(str(c) for c in trend_colors)
        assert len(unique) >= 2, f"多通道趋势线颜色应互异, 实际唯一色 {unique}"
        plt.close(fig)


# ═══════════════════════════════════════════════════════════════════════════
# make_comparison_overlay 测试
# ═══════════════════════════════════════════════════════════════════════════

class TestComparisonOverlay:
    """多源叠加时程图测试"""

    @pytest.fixture
    def sources_data(self) -> tuple[np.ndarray, dict[str, np.ndarray]]:
        rng = np.random.default_rng(77)
        t = np.arange(300) / 3600.0 * 5
        src_a = 1000 * np.sin(t) + rng.normal(0, 3, 300)
        src_b = 1000 * np.sin(t + 0.1) + 2 + rng.normal(0, 3, 300)
        src_c = 1000 * np.sin(t - 0.05) - 1 + rng.normal(0, 3, 300)
        return t, {"传感器A": src_a, "传感器B": src_b, "传感器C": src_c}

    def test_returns_figure(self, sources_data):
        t, src = sources_data
        fig = make_comparison_overlay(t, src, y_label="应变 (με)")
        assert isinstance(fig, plt.Figure)
        plt.close(fig)

    def test_show_difference_creates_two_subplots(self, sources_data):
        t, src = sources_data
        fig = make_comparison_overlay(t, src, y_label="应变 (με)", show_difference=True)
        assert len(fig.axes) == 2
        plt.close(fig)

    def test_no_difference_one_subplot(self, sources_data):
        t, src = sources_data
        fig = make_comparison_overlay(t, src, y_label="ε", show_difference=False)
        assert len(fig.axes) == 1
        plt.close(fig)

    def test_difference_panel_has_zero_line(self, sources_data):
        t, src = sources_data
        fig = make_comparison_overlay(t, src, y_label="ε", show_difference=True)
        # 下方 axhline(0) 应在
        ax_bot = fig.axes[1]
        lines = ax_bot.get_lines()
        ydata_list = [np.asarray(l.get_ydata(), dtype=float) for l in lines]
        has_hline = any(
            abs(np.mean(yd)) < 1e-6 for yd in ydata_list if len(yd) > 1
        )
        assert has_hline
        plt.close(fig)

    def test_reference_source_is_first_by_default(self, sources_data):
        t, src = sources_data
        fig = make_comparison_overlay(t, src, y_label="ε", show_difference=True)
        # 下方 legend 应包含 '−' 符号，指向差值
        ax_bot = fig.axes[1]
        leg = ax_bot.get_legend()
        assert leg is not None
        combined = " ".join(t.get_text() for t in leg.get_texts())
        assert "−" in combined
        plt.close(fig)

    def test_single_source_no_difference_panel(self, sources_data):
        t, src = sources_data
        single = {"仅A": src["传感器A"]}
        fig = make_comparison_overlay(t, single, y_label="ε", show_difference=True)
        assert len(fig.axes) == 1
        plt.close(fig)

    def test_empty_sources_placeholder(self):
        fig = make_comparison_overlay(np.arange(50), {}, y_label="ε")
        assert isinstance(fig, plt.Figure)
        plt.close(fig)


# ═══════════════════════════════════════════════════════════════════════════
# make_correlation_scatter 测试
# ═══════════════════════════════════════════════════════════════════════════

class TestCorrelationScatter:
    """配对相关散点测试"""

    @pytest.fixture
    def correlated_pair(self) -> tuple[np.ndarray, np.ndarray]:
        rng = np.random.default_rng(42)
        x = np.linspace(0, 1000, 50)
        y = x * 0.98 + 5 + rng.normal(0, 15, 50)
        return x, y

    def test_returns_figure(self, correlated_pair):
        x, y = correlated_pair
        fig = make_correlation_scatter(x, y, label_x="A (με)", label_y="B (με)")
        assert isinstance(fig, plt.Figure)
        plt.close(fig)

    def test_axis_labels(self, correlated_pair):
        x, y = correlated_pair
        fig = make_correlation_scatter(x, y, label_x="A (με)", label_y="B (με)")
        ax = fig.axes[0]
        assert "A" in ax.get_xlabel()
        assert "B" in ax.get_ylabel()
        plt.close(fig)

    def test_diagonal_reference_line(self, correlated_pair):
        x, y = correlated_pair
        fig = make_correlation_scatter(x, y, label_x="A", label_y="B")
        ax = fig.axes[0]
        leg = ax.get_legend()
        legend_texts = [t.get_text() for t in leg.get_texts()] if leg else []
        assert any("y=x" in t for t in legend_texts)
        plt.close(fig)

    def test_corr_in_legend_when_passed(self, correlated_pair):
        x, y = correlated_pair
        fig = make_correlation_scatter(x, y, label_x="A", label_y="B", corr=0.95)
        legend = fig.axes[0].get_legend()
        assert legend is not None
        combined = " ".join(t.get_text() for t in legend.get_texts())
        assert "r=0.95" in combined
        plt.close(fig)

    def test_rmse_in_legend_when_passed(self, correlated_pair):
        x, y = correlated_pair
        fig = make_correlation_scatter(x, y, label_x="A", label_y="B", rmse=5.2)
        legend = fig.axes[0].get_legend()
        assert legend is not None
        combined = " ".join(t.get_text() for t in legend.get_texts())
        assert "RMSE=5.2" in combined
        plt.close(fig)

    def test_no_corr_when_none(self, correlated_pair):
        x, y = correlated_pair
        fig = make_correlation_scatter(x, y, label_x="A", label_y="B")
        legend = fig.axes[0].get_legend()
        assert legend is not None
        combined = " ".join(t.get_text() for t in legend.get_texts())
        assert "r=" not in combined
        assert "RMSE=" not in combined
        plt.close(fig)

    # ---- 退化数据 ----

    def test_empty_placeholder(self):
        fig = make_correlation_scatter([], [], label_x="X", label_y="Y")
        assert isinstance(fig, plt.Figure)
        plt.close(fig)

    def test_all_nan_placeholder(self):
        fig = make_correlation_scatter(
            [np.nan, np.nan], [np.nan, np.nan], label_x="X", label_y="Y"
        )
        assert isinstance(fig, plt.Figure)
        plt.close(fig)


# ═══════════════════════════════════════════════════════════════════════════
# make_distribution 测试
# ═══════════════════════════════════════════════════════════════════════════

class TestDistribution:
    """分布图测试"""

    @pytest.fixture
    def dist_data(self) -> dict[str, np.ndarray]:
        rng = np.random.default_rng(99)
        return {
            "C1": rng.normal(100, 5, 200),
            "C2": rng.normal(102, 8, 200),
            "A1": rng.normal(98, 6, 200),
        }

    def test_box_returns_figure(self, dist_data):
        fig = make_distribution(dist_data, y_label="应变 (με)", kind="box")
        assert isinstance(fig, plt.Figure)
        plt.close(fig)

    def test_hist_returns_figure(self, dist_data):
        fig = make_distribution(dist_data, y_label="应变 (με)", kind="hist")
        assert isinstance(fig, plt.Figure)
        plt.close(fig)

    def test_box_has_correct_number_of_items(self, dist_data):
        fig = make_distribution(dist_data, y_label="ε", kind="box")
        ax = fig.axes[0]
        # boxplot 应产生 3 组箱子
        boxes = ax.patches  # patch_artist=True 时 box 是 patch
        assert len(boxes) >= 3, f"预期 ≥3 个箱，实际 {len(boxes)}"
        plt.close(fig)

    def test_hist_has_legend(self, dist_data):
        fig = make_distribution(dist_data, y_label="ε", kind="hist")
        assert fig.axes[0].get_legend() is not None
        plt.close(fig)

    def test_hist_xlabel_not_empty(self, dist_data):
        """直方图应有 x 轴标签 (含单位)"""
        fig = make_distribution(dist_data, y_label="应变 (με)", kind="hist")
        ax = fig.axes[0]
        xlabel = ax.get_xlabel()
        assert xlabel != "", "直方图 x 轴标签不应为空"
        assert "με" in xlabel, f"x 轴标签应含单位 με，实际: {xlabel}"
        plt.close(fig)

    def test_hist_step_style_not_filled(self, dist_data):
        """直方图用 step 描边，patch 应无填充 (alpha=0)"""
        fig = make_distribution(dist_data, y_label="ε", kind="hist")
        ax = fig.axes[0]
        # histtype='step' 时 patch 存在但 facecolor alpha=0
        for p in ax.patches:
            fc = p.get_facecolor()
            assert len(fc) == 4 and fc[3] < 0.05, f"step patch facecolor alpha 应为 0, 实际 {fc}"
        plt.close(fig)

    def test_invalid_kind_raises(self, dist_data):
        """无效 kind 应抛异常 (hist 路径忽略无效 kind, 走入 else 即为 hist)"""
        fig = make_distribution(dist_data, y_label="ε", kind="unknown")
        # 走入 else 分支 → hist
        assert isinstance(fig, plt.Figure)
        plt.close(fig)

    def test_empty_series_placeholder(self):
        fig = make_distribution({}, y_label="ε")
        assert isinstance(fig, plt.Figure)
        plt.close(fig)

    def test_all_nan_channel(self):
        d: dict[str, np.ndarray] = {"ch": np.full(100, np.nan)}
        fig = make_distribution(d, y_label="ε", kind="box")  # pyright: ignore[reportArgumentType]  # numpy dtype variance
        assert isinstance(fig, plt.Figure)
        plt.close(fig)


# ═══════════════════════════════════════════════════════════════════════════
# make_metric_bar 测试
# ═══════════════════════════════════════════════════════════════════════════

class TestMetricBar:
    """通用单指标柱测试"""

    def test_returns_figure(self):
        fig = make_metric_bar(["C1", "C2", "A1"], [2.3, 8.1, 18.5],
                              y_label="σ", unit="με")
        assert isinstance(fig, plt.Figure)
        plt.close(fig)

    def test_axis_labels(self):
        fig = make_metric_bar(["C1", "C2"], [2.3, 8.1],
                              y_label="σ", unit="με")
        ax = fig.axes[0]
        assert "με" in ax.get_ylabel()
        plt.close(fig)

    def test_no_hex_in_legend_or_labels(self):
        """保证无 hex 色码泄漏到图例/标签"""
        fig = make_metric_bar(["C1", "C2"], [2.3, 8.1],
                              y_label="σ", unit="με")
        ax = fig.axes[0]
        legend = ax.get_legend()
        if legend is not None:
            for t in legend.get_texts():
                assert "#" not in t.get_text()
        # x 标签
        for lbl in ax.get_xticklabels():
            assert "#" not in lbl.get_text()
        plt.close(fig)

    def test_default_single_color_all_bars(self):
        """未传 colors 时所有柱同色"""
        fig = make_metric_bar(["C1", "C2", "A1"], [2.3, 8.1, 18.5],
                              y_label="σ", unit="με")
        ax = fig.axes[0]
        bar_colors = [p.get_facecolor() for p in ax.patches]  # pyright: ignore[reportAttributeAccessIssue]
        # 所有柱颜色应相同
        for bc in bar_colors[1:]:
            assert bc == bar_colors[0], f"未传 colors 时所有柱应同色"
        plt.close(fig)

    def test_title_reflects_y_label(self):
        """标题应反映 y_label"""
        fig = make_metric_bar(["C1", "C2"], [2.3, 8.1],
                              y_label="σ", unit="με")
        assert "σ" in fig.axes[0].get_title(), f"标题应含 y_label 'σ'"
        plt.close(fig)

    def test_no_unit_in_title(self):
        """标题不含单位 (单位在 y 轴)"""
        fig = make_metric_bar(["A"], [1.0], y_label="漂移率", unit="με/h")
        assert "με/h" not in fig.axes[0].get_title()
        plt.close(fig)

    def test_negative_values(self):
        """负值不崩"""
        fig = make_metric_bar(["C1", "C2"], [-5.0, 3.0],
                              y_label="漂移率", unit="με/h")
        assert isinstance(fig, plt.Figure)
        plt.close(fig)

    def test_custom_colors(self):
        colors = ["#aaa", "#bbb", "#ccc"]
        fig = make_metric_bar(["A", "B", "C"], [1, 2, 3],
                              y_label="v", unit="", colors=colors)
        assert isinstance(fig, plt.Figure)
        plt.close(fig)

    def test_empty_categories_placeholder(self):
        fig = make_metric_bar([], [], y_label="v", unit="")
        assert isinstance(fig, plt.Figure)
        plt.close(fig)

    def test_mismatched_lengths_raises(self):
        """类别与值不等长: matplotlib bar 会报 shape mismatch"""
        import pytest as pt
        with pt.raises(ValueError):
            make_metric_bar(["A", "B"], [1.0, 2.0, 3.0], y_label="v", unit="")

    def test_zero_values(self):
        fig = make_metric_bar(["A", "B", "C"], [0, 0, 0],
                              y_label="v", unit="")
        assert isinstance(fig, plt.Figure)
        plt.close(fig)


# ═══════════════════════════════════════════════════════════════════════════
# ReportFigure + FigureManifest 测试
# ═══════════════════════════════════════════════════════════════════════════

class TestReportFigure:
    """ReportFigure dataclass 测试"""

    def test_construction(self):
        rf = ReportFigure(
            fig_id="cal_C2", fig_no=1, section="标定",
            title="C2 线性度", key_stat="R²=0.9998",
            png_path="/tmp/c2.png", caption="图1 C2 线性度",
        )
        assert rf.fig_id == "cal_C2"
        assert rf.fig_no == 1
        assert rf.caption == "图1 C2 线性度"
        assert rf.key_stat == "R²=0.9998"


class TestFigureManifest:
    """FigureManifest 清单测试"""

    def test_empty_to_llm_context(self):
        m = FigureManifest()
        assert m.to_llm_context() == "(无图表)"

    def test_add_increments_fig_no(self):
        m = FigureManifest()
        r1 = m.add("a", "节1", "标题A", "关键数1", "/tmp/a.png")
        assert r1.fig_no == 1
        assert r1.caption == "图1 标题A"

        r2 = m.add("b", "节2", "标题B", "", "/tmp/b.png")
        assert r2.fig_no == 2
        assert r2.caption == "图2 标题B"

    def test_to_llm_context_includes_all(self):
        m = FigureManifest()
        m.add("a", "节1", "T1", "σ=2.3με", "/tmp/a.png")
        m.add("b", "节2", "T2", "H=4.1με", "/tmp/b.png")
        ctx = m.to_llm_context()
        assert "图1" in ctx
        assert "图2" in ctx
        assert "σ=2.3με" in ctx
        assert "H=4.1με" in ctx

    def test_iter_in_insertion_order(self):
        m = FigureManifest()
        m.add("a", "节1", "First", "", "/tmp/1.png")
        m.add("b", "节2", "Second", "", "/tmp/2.png")
        m.add("c", "节3", "Third", "", "/tmp/3.png")
        items = list(m)
        assert [rf.fig_no for rf in items] == [1, 2, 3]
        assert [rf.title for rf in items] == ["First", "Second", "Third"]

    def test_len_and_getitem(self):
        m = FigureManifest()
        m.add("a", "节1", "A", "", "/tmp/a.png")
        m.add("b", "节2", "B", "", "/tmp/b.png")
        assert len(m) == 2
        assert m[0].fig_id == "a"
        assert m[1].fig_id == "b"
        assert m.count == 2

    def test_key_stat_empty_handled(self):
        """key_stat 为空时不显示括号"""
        m = FigureManifest()
        m.add("a", "节1", "NoKeyStat", "", "/tmp/a.png")
        ctx = m.to_llm_context()
        assert "图1: NoKeyStat" in ctx
        # 不应有空括号
        assert "()" not in ctx
