"""图表对话框冒烟测试 — PhaseA/B + 基类"""

from __future__ import annotations
import pytest, warnings
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
    np.random.seed(42)
    n = 300
    wcols = ["B2-W1", "B2-W2", "B1-W1", "B1-W2", "A1-W1", "A1-W2", "A2-W1", "A2-W2"]
    P = pd.DataFrame({
        "T_set": [10, 20, 30, 40, 50, 60, 70] * 8,
    })
    S = {}
    for wc in wcols:
        dc = f"{wc}_d"
        P[dc] = np.linspace(-50, 30000, len(P)) + np.random.normal(0, 5, len(P))
        S[wc] = {"slope": 500.0, "intercept": -5000.0, "T_base": 10.0, "r2": 0.9999,
                 "display": wc}
    return {"S_eff": S, "plateaus": P, "wavelength_cols": wcols,
            "df": pd.DataFrame({wc: [1550.0] * n for wc in wcols})}


@pytest.fixture
def phase_b_result():
    n = 100
    sensors = {}
    np.random.seed(42)
    for name in ["A1", "A2", "B1", "B2"]:
        eps_c = np.sin(np.linspace(0, 3, n)) * 20.0 + np.random.normal(0, 1, n)
        sensors[name] = {
            "eps_corr": eps_c,
            "eps_orig": eps_c * 1.5,
            "dT_orig": np.cos(np.linspace(0, 3, n)) * 5.0,
            "dT_corr": np.cos(np.linspace(0, 3, n)) * 3.0 + np.random.normal(0, 0.5, n),
            "T_abs": 30.0 + np.cos(np.linspace(0, 3, n)) * 5.0,
            "S1": 30.0, "S2": 28.0, "T_base": 12.0,
            "single_grating": False,
        }
    return {"sensors": sensors, "time_h": np.arange(n) * 2.0 / 3600.0}


@pytest.fixture
def phase_b_df_groups():
    df = pd.DataFrame({
        "A1-W1": [1550.0] * 100,
        "A1-W2": [1545.0] * 100,
        "B2-W1": [1552.0] * 100,
        "B2-W2": [1547.0] * 100,
    })
    groups = {
        "A1": [{"col_name": "A1-W1", "name": "A1-W1"},
               {"col_name": "A1-W2", "name": "A1-W2"}],
        "B2": [{"col_name": "B2-W1", "name": "B2-W1"},
               {"col_name": "B2-W2", "name": "B2-W2"}],
    }
    return df, groups


def test_phase_a_charts_dialog_opens(qapp, phase_a_result):
    """Phase A 图表对话框 — 打开后 8 个子图正常"""
    from ui.widgets.charts_dialog import PhaseAChartsDialog
    from ui.calibration_tab import _group_annotations_by_prefix
    annotation = {wc: wc for wc in phase_a_result["wavelength_cols"]}
    dlg = PhaseAChartsDialog(phase_a_result, annotation)
    dlg.show()
    QApplication.processEvents()
    axes = dlg.chart().get_figure().axes
    assert len(axes) == 8, f"expected 8 subplots, got {len(axes)}"
    dlg.close()


def test_phase_b_charts_dialog_opens(qapp, phase_b_result, phase_b_df_groups):
    """Phase B 图表对话框 — 打开后 4 子图正常"""
    from PyQt6.QtCore import QSettings
    QSettings("DataProcessor", "Calibration").setValue("phaseB/panelD_mode", "histogram")
    from ui.widgets.charts_dialog import PhaseBChartsDialog
    df, groups = phase_b_df_groups
    dlg = PhaseBChartsDialog(phase_b_result, df=df, annotation_groups=groups)
    dlg.show()
    QApplication.processEvents()
    axes = dlg.chart().get_figure().axes
    assert len(axes) == 4, f"expected 4 subplots, got {len(axes)}"
    dlg.close()


def test_phase_b_render_no_typeerror(qapp, phase_b_result, phase_b_df_groups):
    """PhaseBChartsDialog._render 不抛 TypeError"""
    from PyQt6.QtCore import QSettings
    QSettings("DataProcessor", "Calibration").setValue("phaseB/panelD_mode", "histogram")
    from ui.widgets.charts_dialog import PhaseBChartsDialog
    df, groups = phase_b_df_groups
    dlg = PhaseBChartsDialog(phase_b_result, df=df, annotation_groups=groups)
    assert dlg._selector.count() == 4  # A1,A2,B1,B2 (all dual grating)
    assert len(dlg.chart().get_figure().axes) == 4
    dlg.close()


def test_phase_a_dialog_no_embedded_chart(qapp, phase_a_result):
    """PhaseADialog 自身不含 ChartPanel (已外移)"""
    from ui.calibration_tab import PhaseADialog
    annotation = {wc: wc for wc in phase_a_result["wavelength_cols"]}
    dlg = PhaseADialog(
        pd.DataFrame({wc: [1550.0] for wc in phase_a_result["wavelength_cols"]}),
        annotation, {}, {"hold_time_min": 6.0, "sample_interval_s": 2.0,
                          "rolling_window": 25, "std_percentile": 45.0,
                          "min_plateau_samples": 50, "head_trim_ratio": 0.70}, None)
    assert hasattr(dlg, 'charts_btn'), "PhaseADialog 应有 charts_btn"
    assert not dlg.charts_btn.isEnabled(), "图表按钮初始应禁用"
    assert hasattr(dlg, 'seff_table'), "PhaseADialog 应有 seff_table (替换旧 result_summary)"
    dlg.close()


def test_r_squared_no_warning(qapp, phase_a_result):
    """R² 渲染不应产生 SUPERSCRIPT TWO 警告"""
    from ui.widgets.charts_dialog import PhaseAChartsDialog
    annotation = {wc: wc for wc in phase_a_result["wavelength_cols"]}
    with warnings.catch_warnings(record=True) as w:
        warnings.simplefilter("always")
        dlg = PhaseAChartsDialog(phase_a_result, annotation)
        dlg.show()
        QApplication.processEvents()
        dlg.close()
    superscript_warnings = [x for x in w if "SUPERSCRIPT TWO" in str(x.message)]
    assert len(superscript_warnings) == 0, \
        f"仍有 {len(superscript_warnings)} 个 SUPERSCRIPT TWO 警告"


def test_diag_four_panels_distinct(qapp, phase_b_result, phase_b_df_groups):
    """诊断四联图 4 个面板内容各异，(a)波长漂移 ≠ (c)应变 ≠ (d)直方图"""
    from PyQt6.QtCore import QSettings
    QSettings("DataProcessor", "Calibration").setValue("phaseB/panelD_mode", "histogram")
    from ui.widgets.charts_dialog import PhaseBChartsDialog
    df, groups = phase_b_df_groups
    dlg = PhaseBChartsDialog(phase_b_result, df=df, annotation_groups=groups)
    dlg.show()
    QApplication.processEvents()
    axes = dlg.chart().get_figure().axes
    assert len(axes) == 4

    assert len(axes[0].lines) >= 1, "(a) 应有波长漂移曲线"
    assert len(axes[1].lines) >= 1, "(b) 应有温度曲线"
    assert len(axes[2].lines) >= 1, "(c) 应有应变曲线"
    assert len(axes[3].patches) > 0, "(d) 默认直方图应有 patches"
    dlg.close()


def test_no_top_fullscreen_button(qapp):
    """BaseChartsDialog 顶栏不含'全屏'按钮"""
    from ui.widgets.charts_dialog import BaseChartsDialog
    dlg = BaseChartsDialog("test", (6, 4))
    # ChartPanel 的 fs_btn 应不存在 (show_fs_btn=False)
    assert not hasattr(dlg.chart(), 'fs_btn'), \
        "ChartPanel 在 BaseChartsDialog 中不应有 fs_btn"
    dlg.close()


def test_panel_a_labels_use_delta_lambda(qapp, phase_b_result, phase_b_df_groups):
    """面板(a) Y轴/标题/图例使用 Δλ 标注"""
    from PyQt6.QtCore import QSettings
    QSettings("DataProcessor", "Calibration").setValue("phaseB/panelD_mode", "histogram")
    from ui.widgets.charts_dialog import PhaseBChartsDialog
    df, groups = phase_b_df_groups
    dlg = PhaseBChartsDialog(phase_b_result, df=df, annotation_groups=groups)
    dlg.show()
    QApplication.processEvents()
    axes = dlg.chart().get_figure().axes

    # Y轴标签
    assert axes[0].get_ylabel() == "Δλ (pm)", \
        f"expected 'Δλ (pm)', got '{axes[0].get_ylabel()}'"
    # 标题含 Δλ
    assert "Δλ" in axes[0].get_title(), \
        f"title should contain Δλ, got '{axes[0].get_title()}'"
    # 图例含 Δλ1 和 Δλ2
    legend_texts = [t.get_text() for t in axes[0].get_legend().get_texts()]
    assert any("Δλ1" in t for t in legend_texts), f"legend missing Δλ1: {legend_texts}"
    assert any("Δλ2" in t for t in legend_texts), f"legend missing Δλ2: {legend_texts}"
    dlg.close()


def test_panel_d_histogram_default(qapp, phase_b_result, phase_b_df_groups):
    """面板(d) 默认直方图 — patches + axvline + μ/σ 标签"""
    from PyQt6.QtCore import QSettings
    QSettings("DataProcessor", "Calibration").setValue("phaseB/panelD_mode", "histogram")
    from ui.widgets.charts_dialog import PhaseBChartsDialog
    df, groups = phase_b_df_groups
    dlg = PhaseBChartsDialog(phase_b_result, df=df, annotation_groups=groups)
    dlg.show()
    QApplication.processEvents()
    axes = dlg.chart().get_figure().axes

    # patches 非空 (histogram bars)
    assert len(axes[3].patches) > 0, "(d) 直方图应有 bars"
    # X 轴含 με
    assert "με" in axes[3].get_xlabel(), f"xlabel should have με: {axes[3].get_xlabel()}"
    # 至少 2 条 axvline (μ + σ bounds)
    vlines = [l for l in axes[3].lines if l.get_linestyle() != "None"]
    assert len(vlines) >= 2, f"应有≥2条axvline (μ/σ), 实际{len(vlines)}"
    # 标题含 μ 和 σ
    title = axes[3].get_title()
    assert "μ" in title, f"title missing μ: {title}"
    assert "σ" in title, f"title missing σ: {title}"
    dlg.close()


def test_panel_d_toggle_to_scatter(qapp, phase_b_result, phase_b_df_groups):
    """切换到散点模式后 axes[3] 含 scatter collection"""
    from ui.widgets.charts_dialog import PhaseBChartsDialog
    df, groups = phase_b_df_groups
    dlg = PhaseBChartsDialog(phase_b_result, df=df, annotation_groups=groups)
    dlg.show()
    QApplication.processEvents()

    # 切换到散点
    dlg._scat_radio.setChecked(True)
    QApplication.processEvents()

    axes = dlg.chart().get_figure().axes
    assert axes[3].collections, "(d) 散点模式应有 PathCollection"
    dlg.close()
