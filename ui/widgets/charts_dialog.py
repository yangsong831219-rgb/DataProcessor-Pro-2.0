"""独立图表对话框 — PhaseA/B 共用基类

BaseChartsDialog(QDialog): 大窗口 + 单个 ChartPanel + 底部全屏 + 关闭按钮
PhaseAChartsDialog: 8 子图 4×2 回归网格
PhaseBChartsDialog: 诊断四联图 + 传感器下拉 + 散点/直方图切换
"""

from __future__ import annotations
from typing import Optional

import numpy as np
import pandas as pd
from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QComboBox, QRadioButton, QGroupBox,
)
from PyQt6.QtCore import QSettings
from PyQt6.QtCore import Qt, QObject

from ui.widgets.chart_panel import ChartPanel
from ui.components import create_button


class _EscFilter(QObject):
    def eventFilter(self, obj, event):
        if event.type() == event.Type.KeyPress and event.key() == Qt.Key.Key_Escape:
            if obj.isFullScreen():
                obj.showNormal(); return True
            obj.accept(); return True
        return False


# ═══════════════════════════════════════════════════════════════════════
# 基类 — 底部全屏为唯一全屏入口，ChartPanel 不显示自带全屏按钮
# ═══════════════════════════════════════════════════════════════════════

class BaseChartsDialog(QDialog):
    """独立图表窗口 — ChartPanel 满铺 + 底部全屏"""

    def __init__(self, title="图表分析", figsize=(12, 8), parent=None):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setSizeGripEnabled(True)
        self.resize(1300, 900)

        esc = _EscFilter(self); self.installEventFilter(esc)

        layout = QVBoxLayout(self)

        # ChartPanel — 隐藏自带全屏按钮，由对话框底部统一提供
        self._chart = ChartPanel(figsize=figsize, dpi=100, show_fs_btn=False, parent=self)
        layout.addWidget(self._chart, stretch=1)

        bottom = QHBoxLayout()
        bottom.addWidget(create_button("⛶ 全屏", self._toggle_fs, "secondary"))
        bottom.addStretch()
        bottom.addWidget(create_button("确定", self.accept, "primary"))
        layout.addLayout(bottom)

    def chart(self) -> ChartPanel:
        return self._chart

    def _toggle_fs(self):
        if self.isFullScreen(): self.showNormal()
        else: self.showFullScreen()


# ═══════════════════════════════════════════════════════════════════════
# Phase A 回归图
# ═══════════════════════════════════════════════════════════════════════

class PhaseAChartsDialog(BaseChartsDialog):
    """N 个光栅的 dL vs T_set 回归子图网格"""

    def __init__(self, phase_a_result: dict, annotation: dict, parent=None):
        super().__init__("阶段 A — 回归图", (14, 9), parent)
        self._result = phase_a_result
        self._annotation = annotation
        P = phase_a_result.get("plateaus", pd.DataFrame())
        print(f"[A2] PhaseAChartsDialog init, plateaus shape={P.shape if P is not None else 'None'}, "
              f"S_eff keys={list(phase_a_result.get('S_eff',{}).keys())[:3]}")
        self._render()

    def _render(self):
        result = self._result
        S_eff = result.get("S_eff", {})
        P = result.get("plateaus", pd.DataFrame())
        wcols = result.get("wavelength_cols", list(S_eff.keys()))
        df = result.get("df")
        print(f"[A3] _render start: P shape={P.shape if P is not None else 'None'}, "
              f"df is None? {df is None}, wcols count={len(wcols)}")

        # ── Lazy 重算: profile 加载后 plateaus 为空 → 自动重跑平台检测 ──
        if (P is None or P.empty) and df is not None and not df.empty and wcols:
            print("[A4] lazy recompute: plateaus empty, computing...")
            try:
                from py.calibration.step_extractor import detect_plateaus
                from py.calibration.temperature_calibration import assign_setpoints, compute_dL
                # ★ 先补 _d 列 (detect_plateaus 需要 _d 后缀列)
                df_dL, _ = compute_dL(df.copy(), wcols)
                params = getattr(self.parent(), '_detection_params', {}) if self.parent() else {}
                tp = self.parent()
                if hasattr(tp, 'temperature_page'):
                    tp = tp.temperature_page
                temp_page = tp if hasattr(tp, '_detection_params') else None
                if temp_page:
                    params = temp_page._detection_params
                # 重跑平台检测 (使用缓存的检测参数, df_dL 已含 _d 列)
                P = detect_plateaus(
                    df_dL, wcols,
                    rolling_window=params.get("rolling_window", 25),
                    std_percentile=params.get("std_percentile", 45.0),
                    min_plateau_samples=params.get("min_plateau_samples", 180),
                    head_trim_ratio=params.get("head_trim_ratio", 0.70),
                )
                # 从 S_eff key 反推设定温度 (用 T_base)
                if not P.empty and S_eff:
                    first_wcol = wcols[0]
                    first_S = S_eff.get(first_wcol, {})
                    T_base = first_S.get("T_base", 10.0)
                    slope = first_S.get("slope", 28.0)
                    if slope > 0:
                        n_levels = len(P)
                        setpoints = [round(T_base + (P.iloc[i]["proxy"] - P.iloc[0]["proxy"]) / slope, 0)
                                    for i in range(n_levels)]
                        setpoints = sorted(set(setpoints))
                        if len(setpoints) == 1:
                            setpoints = [10, 20, 30, 40, 50, 60, 70][:n_levels]
                        if len(setpoints) >= 2:
                            P = assign_setpoints(P, setpoints)
                result["plateaus"] = P
                # ★ 补全 intercept (OLS: intercept = mean(λ) - slope * mean(T))
                if not P.empty and "T_set" in P.columns:
                    for wcol in wcols:
                        s = S_eff.get(wcol, {})
                        if "intercept" not in s and "slope" in s:
                            dc = f"{wcol}_d"
                            if dc in P.columns:
                                T_mean = P["T_set"].mean()
                                lam_mean = P[dc].mean()
                                s["intercept"] = float(lam_mean - s["slope"] * T_mean)
                                S_eff[wcol] = s
                print(f"[A4] lazy recompute done: plateaus shape={P.shape}, filled intercepts")
            except Exception as e:
                print(f"[A4] lazy recompute failed: {e}")
                import traceback; traceback.print_exc()
                P = pd.DataFrame()

        fig = self._chart.get_figure()
        fig.clear()
        n = len(wcols)
        if n == 0:
            print("[A3] render: wcols=0, nothing to draw")
            self._chart.draw()
            return

        ncols = min(2, n)
        nrows = (n + 1) // 2
        colors = ["#2980b9", "#e67e22", "#27ae60", "#c0392b",
                   "#8e44ad", "#16a085", "#d35400", "#2c3e50"]
        for i, wcol in enumerate(wcols):
            ax = fig.add_subplot(nrows, ncols, i + 1)
            dc = f"{wcol}_d"
            if dc not in P.columns:
                continue
            s = S_eff.get(wcol, {})
            display = self._annotation.get(wcol, wcol)
            if not P.empty and "T_set" in P.columns and dc in P.columns:
                ax.scatter(P["T_set"], P[dc], s=25, alpha=0.6,
                           color=colors[i % len(colors)], zorder=3)
                if not np.isnan(s.get("slope", float("nan"))):
                    xs = np.linspace(P["T_set"].min(), P["T_set"].max(), 100)
                    intercept = s.get("intercept")
                    if intercept is None and not P.empty and dc in P.columns and "T_set" in P.columns:
                        # defensive fallback: OLS intercept = mean(λ) - slope * mean(T)
                        intercept = float(P[dc].mean() - s["slope"] * P["T_set"].mean())
                    if intercept is not None:
                        ax.plot(xs, s["slope"] * xs + intercept, "-", lw=2,
                                color="red", label=f'灵敏度={s["slope"]:.2f} pm/°C')
            ax.set_title(f"{display}: 灵敏度={s.get('slope',0):.2f} pm/°C  "
                         f"R²={s.get('r2',0):.5f}", fontsize=9)
            ax.set_xlabel("设定温度 (°C)", fontsize=8)
            ax.set_ylabel("波长漂移 (pm)", fontsize=8)
            ax.legend(fontsize=7)
            ax.grid(alpha=0.3)
        self._chart.draw_with_layout()


# ═══════════════════════════════════════════════════════════════════════
# Phase B 诊断四联图
# ═══════════════════════════════════════════════════════════════════════

class PhaseBChartsDialog(BaseChartsDialog):
    """诊断四联图 + 传感器下拉选择器 + 散点/直方图切换

    面板: (a)Δλ时程 (b)解耦温度 (c)解耦应变 (d)直方图或ε-ΔT散点
    """

    def __init__(self, phase_b_result: dict, df=None, annotation_groups=None, parent=None):
        super().__init__("阶段 B — 诊断四联图", (12, 8), parent)
        self._result = phase_b_result
        self._df = df
        self._groups = annotation_groups or {}
        self._sensors = phase_b_result.get("sensors", {})

        # 散点/直方图切换 — 持久化到 QSettings
        settings = QSettings("DataProcessor", "Calibration")
        self._use_histogram = settings.value("phaseB/panelD_mode", "histogram") == "histogram"

        # 传感器选择器
        selector_layout = QHBoxLayout()
        selector_layout.addWidget(QLabel("查看传感器:"))
        self._selector = QComboBox()
        dual = {k: v for k, v in self._sensors.items() if not v.get("single_grating")}
        self._selector.addItems(list(dual.keys()))
        self._selector.setEnabled(len(dual) > 0)
        self._selector.currentIndexChanged.connect(self._render)
        selector_layout.addWidget(self._selector)

        # 面板(d)模式切换
        gb = QGroupBox("面板(d) 模式")
        gb_layout = QHBoxLayout(gb)
        self._hist_radio = QRadioButton("直方图")
        self._scat_radio = QRadioButton("ε-ΔT 散点")
        if self._use_histogram:
            self._hist_radio.setChecked(True)
        else:
            self._scat_radio.setChecked(True)
        self._hist_radio.toggled.connect(self._on_panel_d_mode_changed)
        gb_layout.addWidget(self._hist_radio)
        gb_layout.addWidget(self._scat_radio)
        selector_layout.addWidget(gb)
        selector_layout.addStretch()
        self.layout().insertLayout(1, selector_layout)

        # 预计算 dL
        if self._df is not None and self._groups:
            from py.calibration.temperature_calibration import compute_dL
            cols = []
            for pfx, gratings in self._groups.items():
                for g in gratings:
                    c = g.get("col_name", g["name"])
                    if c in self._df.columns:
                        cols.append(c)
            self._df_dL, _ = compute_dL(self._df.copy(), cols) if cols else (self._df, None)
        else:
            self._df_dL = self._df

        if dual:
            self._render()

    def _on_panel_d_mode_changed(self):
        settings = QSettings("DataProcessor", "Calibration")
        self._use_histogram = self._hist_radio.isChecked()
        settings.setValue("phaseB/panelD_mode", "histogram" if self._use_histogram else "scatter")
        # 只重画面板 (d)，不重建 figure
        self._render_panel_d()

    def _render(self):
        s_name = self._selector.currentText()
        if not s_name:
            return
        r = self._sensors.get(s_name)
        if not r or r.get("single_grating"):
            return

        eps_c = np.asarray(r.get("eps_corr", []), dtype=np.float64)
        T_abs = np.asarray(r.get("T_abs", []), dtype=np.float64)
        dT_corr = np.asarray(r.get("dT_corr", []), dtype=np.float64)
        n = len(eps_c)
        if n == 0:
            return
        time_h = self._result.get("time_h", np.arange(n) * 2.0 / 3600.0)

        # 取两栅波长漂移: 以第一行为基准, dL = (λ - λ₀) × 1000 (pm)
        gratings = self._groups.get(s_name, [])
        dl_data = {}
        for gi, g in enumerate(gratings[:2]):
            cname = g.get("col_name", g["name"])
            gn = g.get("name", cname)
            dc = f"{cname}_d"
            # 优先取 _df_dL 的预计算 dL 列
            if self._df_dL is not None and dc in self._df_dL.columns:
                dl_data[gi] = (gn, self._df_dL[dc].values[:n])
            elif cname in self._df.columns:
                # fallback: 原地算 dL
                import pandas as pd_local
                raw = pd_local.to_numeric(self._df[cname], errors="coerce").values[:n]
                base = raw[0] if len(raw) > 0 and not np.isnan(raw[0]) else 0.0
                dl_data[gi] = (gn, (raw - base) * 1000.0)
            else:
                dl_data[gi] = (gn, np.zeros(n))

        fig = self._chart.get_figure()
        fig.clear()
        axes = fig.subplots(2, 2)

        # (a) Δλ 时程 — 双线标注 Δλ1/Δλ2
        ax = axes[0, 0]
        for gi in sorted(dl_data.keys()):
            gname, dl_vals = dl_data[gi]
            ax.plot(time_h[:n], dl_vals, lw=0.5, label=f"Δλ{gi+1} ({gname})")
        ax.set_title(f"(a) {s_name} Δλ 时程曲线", fontsize=10)
        ax.set_ylabel("Δλ (pm)")
        ax.set_xlabel("时间 (h)")
        ax.legend(fontsize=7)
        ax.grid(alpha=0.3)

        # (b) 解耦温度
        ax = axes[0, 1]
        ax.plot(time_h[:n], dT_corr if len(dT_corr) == n else T_abs, lw=0.6, color="#2980b9")
        ax.set_title(f"(b) {s_name} 解耦温度变化", fontsize=10)
        ax.set_ylabel("°C", color="#2980b9")
        ax.set_xlabel("时间 (h)")
        ax.grid(alpha=0.3)

        # (c) 解耦应变 ε(t) + 0 线 + e_std
        ax = axes[1, 0]
        if np.all(np.isnan(eps_c)):
            ax.text(0.5, 0.5, "解耦失败: 检查 Ke/KT", transform=ax.transAxes,
                    ha="center", va="center", fontsize=12, color="red")
        else:
            ax.plot(time_h[:n], eps_c, lw=0.5, color="#27ae60")
            ax.axhline(0, color="k", lw=0.5)
            ylim = max(abs(np.nanmin(eps_c)), abs(np.nanmax(eps_c))) * 1.2 if n > 0 else 200
            ax.set_ylim(-max(ylim, 50), max(ylim, 50))
        e_std = np.nanstd(eps_c)
        ax.set_title(f"(c) {s_name} 解耦应变: e_std={e_std:.1f} με", fontsize=10)
        ax.set_ylabel("με", color="#27ae60")
        ax.set_xlabel("时间 (h)")
        ax.grid(alpha=0.3)

        # (d) 直方图 (默认) 或 ε-ΔT 散点
        ax = axes[1, 1]
        self._render_panel_d_axis(ax, eps_c, dT_corr if len(dT_corr) == n else T_abs, n, s_name)

        fig.suptitle(f"{s_name} — 解耦诊断", fontweight="bold", fontsize=12)
        self._chart.draw_with_layout()

    def _render_panel_d(self):
        """仅重画面板(d)，不重建整图"""
        s_name = self._selector.currentText()
        if not s_name:
            return
        r = self._sensors.get(s_name)
        if not r or r.get("single_grating"):
            return
        eps_c = np.asarray(r.get("eps_corr", []), dtype=np.float64)
        if len(eps_c) == 0:
            return
        dT_corr = np.asarray(r.get("dT_corr", []), dtype=np.float64)
        n = len(eps_c)

        fig = self._chart.get_figure()
        axes = fig.axes
        if len(axes) < 4:
            return
        ax = axes[3]
        ax.clear()
        self._render_panel_d_axis(ax, eps_c, dT_corr if len(dT_corr) == n else np.zeros(n), n, s_name)
        self._chart._canvas.draw_idle()

    def _render_panel_d_axis(self, ax, eps_c, dT_vals, n, s_name):
        """面板(d) 渲染: 直方图 或 散点"""
        if self._use_histogram:
            # 直方图 + μ/σ 标注
            valid = eps_c[~np.isnan(eps_c)]
            e_mean = float(np.nanmean(eps_c))
            e_std = float(np.nanstd(eps_c))
            ax.hist(valid, bins=50, color="#8e44ad", edgecolor="#333",
                    alpha=0.8, linewidth=0.3)
            ax.axvline(e_mean, ls="--", color="red", lw=1.5,
                       label=f'μ={e_mean:.1f} με')
            ax.axvline(e_mean - e_std, ls=":", color="gray", lw=1)
            ax.axvline(e_mean + e_std, ls=":", color="gray", lw=1,
                       label=f'±σ={e_std:.1f} με')
            ax.set_title(f"(d) {s_name} 解耦应变分布: μ={e_mean:.1f}, σ={e_std:.1f} με",
                         fontsize=10)
            ax.set_xlabel("ε (με)")
            ax.set_ylabel("频次")
            ax.legend(fontsize=7, loc="upper right")
        else:
            # ε-ΔT 散点
            valid = ~(np.isnan(eps_c) | np.isnan(dT_vals))
            if valid.sum() > 10:
                ax.scatter(dT_vals[valid], eps_c[valid], s=2, alpha=0.3, color="#8e44ad")
                ax.axhline(0, color="k", lw=0.5)
            ax.set_title(f"(d) {s_name} 应变-温度相关", fontsize=10)
            ax.set_xlabel("ΔT (°C)")
            ax.set_ylabel("με", color="#8e44ad")
        ax.grid(alpha=0.3)
