"""标定图表工具 — 供报告引擎调用的渲染函数

所有函数输出 PNG 字节流（BytesIO），统一处理中文字体与配色，
复用 analysis_tab 的风格。

用于 Word 报告的 [INSERT_IMAGE: ...] 锚点。
"""

from __future__ import annotations

import io
from typing import Optional

import numpy as np
import pandas as pd
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

# 中文字体 — 与 main.py 全局设置一致
plt.rcParams["font.sans-serif"] = ["SimHei", "Microsoft YaHei", "Arial Unicode MS"]
plt.rcParams["axes.unicode_minus"] = False

_COLORS = ["#2980b9", "#e67e22", "#27ae60", "#c0392b",
           "#8e44ad", "#16a085", "#d35400", "#2c3e50"]


def render_temp_regression(plateaus: pd.DataFrame, S_eff: dict,
                           title: str = "温度灵敏度回归") -> bytes:
    """渲染温度回归图 (dL-T 散点 + 回归线) → PNG bytes"""
    fig, ax = plt.subplots(figsize=(8, 4))
    xs = np.linspace(plateaus["T_set"].min(), plateaus["T_set"].max(), 100)

    dL_cols = [c for c in plateaus.columns if c.endswith("_d")]
    for i, dc in enumerate(dL_cols):
        if dc not in S_eff:
            continue
        s = S_eff[dc]
        c = _COLORS[i % len(_COLORS)]
        lb = dc.replace("_d", "")
        ax.scatter(plateaus["T_set"], plateaus[dc], s=30, alpha=0.6,
                   color=c, zorder=3, label=lb)
        ax.plot(xs, s["slope"] * xs + s["intercept"], "-", lw=2, color=c,
                label=f'{lb}: {s["slope"]:.2f} pm/°C, R²={s["r2"]:.5f}')

    ax.set_xlabel("设定温度 (°C)")
    ax.set_ylabel("波长漂移 (pm)")
    ax.set_title(title)
    ax.legend(fontsize=7)
    ax.grid(alpha=0.3)
    fig.tight_layout()

    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=120)
    plt.close(fig)
    buf.seek(0)
    return buf.read()


def render_temp_diagnosis(result: dict, sensor_name: str = "") -> bytes:
    """渲染温度诊断四联图 → PNG bytes"""
    sensors = result.get("sensors", {})
    df = result.get("df")
    time_h = result.get("time_h")
    if not sensors or df is None:
        return b""

    n = len(sensors)
    fig, axes = plt.subplots(n, 4, figsize=(16, 4 * n), squeeze=False)

    for si, (s_name, r) in enumerate(sensors.items()):
        dl_cols = [c for c in df.columns if c.endswith("_d") and s_name in c]
        dl1 = df[dl_cols[0]].values if len(dl_cols) > 0 else np.zeros(len(df))
        dl2 = df[dl_cols[1]].values if len(dl_cols) > 1 else np.zeros(len(df))

        ax = axes[si, 0]
        ax.plot(time_h, dl1, lw=0.4, label=f"W1 (S={r.get('S1',0):.1f})")
        ax.plot(time_h, dl2, lw=0.4, label=f"W2 (S={r.get('S2',0):.1f})")
        ax.set_title(f"(a) {s_name} 原始波长漂移")
        ax.set_ylabel("pm"); ax.legend(fontsize=7); ax.grid(alpha=0.3)

        ax = axes[si, 1]
        valid = ~(np.isnan(dl1) | np.isnan(dl2))
        if valid.sum() > 10:
            d1, d2 = dl1[valid], dl2[valid]
            slope = np.polyfit(d2, d1, 1)[0]
            xs = np.linspace(d2.min(), d2.max(), 100)
            ax.plot(d2, d1, ".", ms=1, alpha=0.2, color="#3a7")
            ax.plot(xs, slope * xs, "b-", lw=2, label=f"slope={slope:.2f}")
        ax.set_title("(b) 斜率对比"); ax.legend(fontsize=7); ax.grid(alpha=0.3)

        ax = axes[si, 2]; ax2 = ax.twinx()
        eps_o = r.get("eps_orig", np.zeros(len(df)))
        dT_o = r.get("dT_orig", np.zeros(len(df)))
        ax.plot(time_h, eps_o, lw=0.4, color="#c0392b")
        ax2.plot(time_h, dT_o, lw=0.4, color="#888")
        ax.set_title(f"(c) 标定: e std={np.nanstd(eps_o):.0f} με")
        ax.set_ylabel("应变 (με)", color="#c0392b"); ax2.set_ylabel("dT (°C)", color="#888")

        ax = axes[si, 3]; ax3 = ax.twinx()
        eps_c = r.get("eps_corr", np.zeros(len(df)))
        T_abs = r.get("T_abs", np.zeros(len(df)))
        ax.plot(time_h, eps_c, lw=0.5, color="#27ae60")
        ax3.plot(time_h, T_abs, lw=0.6, color="#2980b9")
        ax.axhline(0, color="k", lw=0.6)
        ax.set_title(f"(d) 修正: e std={np.nanstd(eps_c):.1f} με")
        ax.set_ylabel("应变 (με)", color="#27ae60"); ax3.set_ylabel("温度 (°C)", color="#2980b9")
        ax.set_xlabel("时间 (h)")

    fig.suptitle("解耦诊断" if not sensor_name else f"{sensor_name} — 解耦诊断",
                 fontweight="bold")
    fig.tight_layout()

    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=120)
    plt.close(fig)
    buf.seek(0)
    return buf.read()


def render_strain_curve(result, title: str = "Δλ — ε 标定曲线") -> bytes:
    """渲染应变标定曲线 → PNG bytes"""
    fig, ax = plt.subplots(figsize=(8, 4))
    eps = np.array(result.eps_theory)
    if len(eps) == 0:
        buf = io.BytesIO(); fig.savefig(buf, format="png"); plt.close(fig); return buf.getvalue()

    for g in result.gratings:
        if np.isnan(g.k_pm_per_ue):
            continue
        fit = g.k_pm_per_ue * eps
        ax.plot(eps, fit, "-", lw=2,
                label=f"G{g.grating_index}: k={g.k_pm_per_ue:.4f} pm/με, R²={g.R2:.5f}")

    ax.set_xlabel("理论应变 (με)"); ax.set_ylabel("波长漂移 (pm)")
    ax.set_title(title); ax.legend(fontsize=8); ax.grid(alpha=0.3)
    fig.tight_layout()

    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=120)
    plt.close(fig)
    buf.seek(0)
    return buf.read()


def render_strain_metrics_bar(result, title: str = "核心指标") -> bytes:
    """渲染应变指标柱状图 → PNG bytes"""
    fig, ax = plt.subplots(figsize=(8, 4))

    labels, k_vals, r2_vals, nl_vals, rp_vals, hy_vals = [], [], [], [], [], []
    for g in result.gratings:
        if np.isnan(g.k_pm_per_ue):
            continue
        labels.append(f"G{g.grating_index}")
        k_vals.append(g.k_pm_per_ue)
        r2_vals.append(g.R2 * 10 if not np.isnan(g.R2) else 0)
        nl_vals.append(g.nonlinearity_pct_fs if not np.isnan(g.nonlinearity_pct_fs) else 0)
        rp_vals.append(g.repeatability_pct_fs if not np.isnan(g.repeatability_pct_fs) else 0)
        hy_vals.append(g.hysteresis_pct_fs if not np.isnan(g.hysteresis_pct_fs) else 0)

    if not labels:
        buf = io.BytesIO(); fig.savefig(buf, format="png"); plt.close(fig); return buf.getvalue()

    x = np.arange(len(labels)); w = 0.15
    ax.bar(x-2*w, k_vals, w, label="k (pm/με)")
    ax.bar(x-w, r2_vals, w, label="R² ×10")
    ax.bar(x, nl_vals, w, label="非线性(%FS)")
    ax.bar(x+w, rp_vals, w, label="重复性(%FS)")
    ax.bar(x+2*w, hy_vals, w, label="迟滞(%FS)")

    ax.set_xticks(x); ax.set_xticklabels(labels)
    ax.set_title(title); ax.legend(fontsize=7); ax.grid(alpha=0.3, axis="y")
    fig.tight_layout()

    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=120)
    plt.close(fig)
    buf.seek(0)
    return buf.read()
