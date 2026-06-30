"""报告图表绘制层 — 确定性图表生成 (Phase 0)

纯函数图表层，不依赖 LLM。所有函数返回 matplotlib Figure，
由调用方决定 savefig、嵌入 Word/PPT 或喂 FigureManifest。

设计原则：
- 图由代码从真实数据生成，绝不补点/臆造
- LLM 只引用图号不画图
- 轴标签必带单位、有图题、适用时有图例
- 退化数据不崩，出可读占位图
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from typing import Optional, Sequence

import matplotlib
matplotlib.use("Agg")  # 非交互后端，headless 安全
import matplotlib.pyplot as plt
import numpy as np

logger = logging.getLogger(__name__)

# ═══════════════════════════════════════════════════════════════════════════
# 配色与样式常量
# ═══════════════════════════════════════════════════════════════════════════

_HOUSE_COLORS = [
    "#2980b9", "#e67e22", "#27ae60", "#c0392b",
    "#8e44ad", "#16a085", "#d35400", "#2c3e50",
]

_GRADE_COLOR_MAP = {
    "优": "#52c41a",    # 绿色
    "良": "#faad14",    # 黄色
    "合格": "#fa8c16",  # 橙色
    "FAIL": "#ff4d4f",  # 红色
    "N/A": "#8c8c8c",   # 灰色
    "ERROR": "#ff4d4f", # 红色
}

_FIGSIZE_DEFAULT = (8, 4)
_DPI_DEFAULT = 150


# ═══════════════════════════════════════════════════════════════════════════
# 基建
# ═══════════════════════════════════════════════════════════════════════════

def setup_chinese_font() -> None:
    """配置 matplotlib 中文字体，找不到则回退 + 告警，不崩。

    检测顺序: Microsoft YaHei → SimHei → sans-serif 回退。
    同时设置 axes.unicode_minus=False 避免负号豆腐块。
    """
    from matplotlib.font_manager import FontManager, findfont

    candidates = ["Microsoft YaHei", "SimHei"]
    found: list[str] = []
    try:
        fm = FontManager()
        # FontManager 在 Agg 后端下可能只有基本字体，用 findfont 实测
        for name in candidates:
            try:
                path = findfont(name, fallback_to_default=False)
                if path and os.path.exists(path):
                    found.append(name)
            except Exception:
                continue
    except Exception:
        pass

    if found:
        font_list = found + ["sans-serif"]
        logger.debug("中文字体已就绪: %s", found[0])
    else:
        font_list = candidates + ["sans-serif"]
        logger.warning("未检测到中文字体 (%s)，图表中文可能显示为方块",
                       ", ".join(candidates))

    plt.rcParams["font.sans-serif"] = font_list
    plt.rcParams["axes.unicode_minus"] = False


def apply_house_style(fig: Optional[plt.Figure] = None) -> None:
    """应用统一图表排版样式 (house style)。

    若 fig 为 None，设置全局 rcParams 默认值。
    若传入 fig，对单图调整尺寸/字号/网格等。

    Args:
        fig: 目标 Figure，None 则设全局默认。
    """
    if fig is None:
        plt.rcParams.update({
            "figure.figsize": _FIGSIZE_DEFAULT,
            "figure.dpi": _DPI_DEFAULT,
            "font.size": 10,
            "axes.titlesize": 12,
            "axes.labelsize": 10,
            "xtick.labelsize": 9,
            "ytick.labelsize": 9,
            "legend.fontsize": 8,
            "axes.prop_cycle": plt.cycler(color=_HOUSE_COLORS),
            "grid.alpha": 0.3,
        })
    else:
        fig.set_size_inches(*_FIGSIZE_DEFAULT)
        fig.set_dpi(_DPI_DEFAULT)
        for ax in fig.axes:
            ax.grid(alpha=0.3)
            # 设置字号
            ax.title.set_fontsize(12)
            ax.xaxis.label.set_fontsize(10)  # pyright: ignore[reportAttributeAccessIssue]  # pyplot stub lacks Text.set_fontsize
            ax.yaxis.label.set_fontsize(10)  # pyright: ignore[reportAttributeAccessIssue]
            for tick_label in ax.get_xticklabels() + ax.get_yticklabels():
                tick_label.set_fontsize(9)
            legend = ax.get_legend()
            if legend is not None:
                for text in legend.get_texts():
                    text.set_fontsize(8)  # pyright: ignore[reportAttributeAccessIssue]  # matplotlib stub gap


def save_figure(fig: plt.Figure, path: str, dpi: int = 200) -> str:
    """保存 Figure 为 PNG，自动创建目录，关闭 Figure。

    Args:
        fig: matplotlib Figure
        path: 输出 PNG 路径
        dpi: 输出分辨率 (默认 200)

    Returns:
        保存后的绝对路径

    Raises:
        OSError: 目录创建失败或写入失败
    """
    path = os.path.abspath(path)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    try:
        fig.savefig(path, dpi=dpi, bbox_inches="tight")
    finally:
        plt.close(fig)
    return path


# ═══════════════════════════════════════════════════════════════════════════
# 4 张核心图
# ═══════════════════════════════════════════════════════════════════════════

def make_calibration_linearity(
    ref: np.ndarray | Sequence[float],
    measured: np.ndarray | Sequence[float],
    *,
    sensor: str = "",
    unit: str = "με",
) -> plt.Figure:
    """标定线性度图 — 实测 vs 参考散点 + 拟合线 + R² + 残差子图。

    上图: 实测 vs 参考 (散点 + 线性拟合)
    下图: 残差 vs 参考

    Args:
        ref: 参考值序列
        measured: 实测值序列 (与 ref 等长)
        sensor: 传感器名称 (用于图题)
        unit: 物理量单位 (默认 με)

    Returns:
        matplotlib Figure (2 子图)
    """
    setup_chinese_font()
    ref_arr = np.asarray(ref, dtype=float)
    meas_arr = np.asarray(measured, dtype=float)

    fig, (ax_top, ax_bot) = plt.subplots(2, 1, figsize=(8, 6),
                                         gridspec_kw={"height_ratios": [3, 1]})

    # 退化: 数据不足
    if len(ref_arr) < 2:
        _placeholder(ax_top, f"数据点不足 (n={len(ref_arr)})，无法绘图")
        _placeholder(ax_bot, "—")
        title = f"{sensor} 标定线性度" if sensor else "标定线性度"
        ax_top.set_title(title)
        apply_house_style(fig)
        fig.tight_layout()
        return fig

    # 线性拟合
    valid = ~(np.isnan(ref_arr) | np.isnan(meas_arr))
    if valid.sum() < 2:
        _placeholder(ax_top, "有效数据点不足")
        _placeholder(ax_bot, "—")
        title = f"{sensor} 标定线性度" if sensor else "标定线性度"
        ax_top.set_title(title)
        apply_house_style(fig)
        fig.tight_layout()
        return fig

    rv, mv = ref_arr[valid], meas_arr[valid]
    slope, intercept = np.polyfit(rv, mv, 1)
    fitted = slope * rv + intercept
    residuals = mv - fitted

    # R²
    ss_res = np.sum(residuals ** 2)
    ss_tot = np.sum((mv - np.mean(mv)) ** 2)
    r2 = 1 - ss_res / ss_tot if ss_tot > 0 else 0.0

    # 拟合公式渲染 — 负截距用 " − "，避免 "+−"
    sign = "+" if intercept >= 0 else "−"
    abs_int = abs(intercept)
    eq_label = f"拟合: y={slope:.4f}x {sign} {abs_int:.2f}, R²={r2:.5f}"
    # pyright: ignore[reportArgumentType]  # Unicode minus in f-string

    # 上图: 实测 vs 参考
    color = _HOUSE_COLORS[0]
    ax_top.scatter(rv, mv, s=20, alpha=0.5, color=color, zorder=3, label="实测")
    xs_line = np.linspace(rv.min(), rv.max(), 200)
    ax_top.plot(xs_line, slope * xs_line + intercept, "-", lw=2,
                color=_HOUSE_COLORS[2], label=eq_label)
    ax_top.set_ylabel(f"实测 ({unit})")
    ax_top.legend(fontsize=7)
    ax_top.grid(alpha=0.3)

    # 下图: 残差
    ax_bot.axhline(0, color="k", lw=0.6, ls="--")
    ax_bot.scatter(rv, residuals, s=12, alpha=0.6, color=_HOUSE_COLORS[3], zorder=3)
    ax_bot.set_xlabel(f"参考 ({unit})")
    ax_bot.set_ylabel(f"残差 ({unit})")
    ax_bot.grid(alpha=0.3)

    title = f"{sensor} 标定线性度" if sensor else "标定线性度"
    ax_top.set_title(title)

    apply_house_style(fig)
    fig.tight_layout()
    return fig


def make_hysteresis_loop(
    x: np.ndarray | Sequence[float],
    y: np.ndarray | Sequence[float],
    *,
    sensor: str = "",
    x_label: str = "温度 (°C)",
    y_label: str = "应变 (με)",
) -> plt.Figure:
    """迟滞回线图 — 加载-卸载回线闭合曲线 + 标注环面积。

    升支/降支各自按驱动量 (x) 排序后绘曲线。
    若噪声大 (CV>10%) 则对 y 做滑动窗口中位数平滑，使迟滞带可读。

    Args:
        x: x 轴数据 (如温度)
        y: y 轴数据 (如应变)
        sensor: 传感器名称
        x_label: x 轴标签 (含单位)
        y_label: y 轴标签 (含单位)

    Returns:
        matplotlib Figure
    """
    setup_chinese_font()
    x_arr = np.asarray(x, dtype=float)
    y_arr = np.asarray(y, dtype=float)

    fig, ax = plt.subplots(figsize=(8, 5))

    valid = ~(np.isnan(x_arr) | np.isnan(y_arr))
    if valid.sum() < 2:
        _placeholder(ax, f"有效数据点不足 (n={valid.sum()})，无法绘迟滞回线")
        title = f"{sensor} 迟滞回线" if sensor else "迟滞回线"
        ax.set_title(title)
        apply_house_style(fig)
        fig.tight_layout()
        return fig

    xv, yv = x_arr[valid], y_arr[valid]
    n_total = len(xv)

    # ── 分割升/降支：以原始 x 方向判定 ──
    # 基于每对相邻点的 x 方向, 不用平滑 (避免边缘效应误判单调数据)
    dx_raw = np.diff(xv)
    up_mask = np.concatenate([[True], dx_raw >= 0])
    down_mask = np.concatenate([[True], dx_raw <= 0])

    xu, yu = xv[up_mask], yv[up_mask]
    xd, yd = xv[down_mask], yv[down_mask]

    has_up = len(xu) > 1
    has_down = len(xd) > 1
    has_loop = has_up and has_down

    # ── 判断是否需要平滑 (y 变异系数 > 10%)，仅对渲染的 y 值平滑 ──
    cv = np.std(yv) / (np.abs(np.mean(yv)) + 1e-9)
    smooth_win = 0
    if cv > 0.10:
        smooth_win = max(3, min(21, n_total // 20))
        try:
            import pandas as pd
            _y_smooth_pd = pd.Series  # 仅用于 type reference
            if has_up and len(xu) > smooth_win:
                order_u = np.argsort(xu)
                yu_sm = pd.Series(yu[order_u]).rolling(
                    window=smooth_win, center=True, min_periods=1).median().to_numpy()
                yu_sm_full = np.empty_like(yu)
                for i, oi in enumerate(order_u):
                    yu_sm_full[oi] = yu_sm[i]
                yu = yu_sm_full
            if has_down and len(xd) > smooth_win:
                order_d = np.argsort(xd)
                yd_sm = pd.Series(yd[order_d]).rolling(
                    window=smooth_win, center=True, min_periods=1).median().to_numpy()
                yd_sm_full = np.empty_like(yd)
                for i, oi in enumerate(order_d):
                    yd_sm_full[oi] = yd_sm[i]
                yd = yd_sm_full
        except ImportError:
            # 降级：numpy 卷积平滑
            kernel = np.ones(smooth_win) / smooth_win
            if has_up and len(xu) > smooth_win:
                order_u = np.argsort(xu)
                yu_sm = np.convolve(yu[order_u], kernel, mode="same")
                yu_sm_full = np.empty_like(yu)
                for i, oi in enumerate(order_u):
                    yu_sm_full[oi] = yu_sm[i]
                yu = yu_sm_full
            if has_down and len(xd) > smooth_win:
                order_d = np.argsort(xd)
                yd_sm = np.convolve(yd[order_d], kernel, mode="same")
                yd_sm_full = np.empty_like(yd)
                for i, oi in enumerate(order_d):
                    yd_sm_full[oi] = yd_sm[i]
                yd = yd_sm_full

    if has_loop:
        # 按 x 排序各自分支，使曲线可读
        if has_up:
            order_u = np.argsort(xu)
            ax.plot(xu[order_u], yu[order_u], "-", lw=1.5,
                    color=_HOUSE_COLORS[0], label="升支 (加载)", alpha=0.85)
        if has_down:
            order_d = np.argsort(xd)
            ax.plot(xd[order_d], yd[order_d], "-", lw=1.5,
                    color=_HOUSE_COLORS[3], label="降支 (卸载)", alpha=0.85)

        # 计算环面积 (Shoelace 公式近似)
        try:
            if has_up and has_down:
                up_pts = np.column_stack([xu[order_u], yu[order_u]])
                # 降支逆序以闭合回路
                down_pts_rev = np.column_stack([xd[order_d][::-1], yd[order_d][::-1]])
                loop_pts = np.vstack([up_pts, down_pts_rev])
                if len(loop_pts) > 2:
                    area = _polygon_area(loop_pts[:, 0], loop_pts[:, 1])
                    cx = float(np.mean(loop_pts[:, 0]))
                    cy = float(np.mean(loop_pts[:, 1]))
                    sm_note = ""
                    if smooth_win > 0:
                        sm_note = f" (平滑 win={smooth_win})"
                    ax.annotate(f"环面积 ≈ {abs(area):.1f}{sm_note}",
                                xy=(cx, cy), fontsize=8, color="#555",
                                bbox=dict(boxstyle="round,pad=0.3",
                                          facecolor="white", alpha=0.7))
        except Exception:
            pass
    else:
        order_all = np.argsort(xv)
        ax.plot(xv[order_all], yv[order_all], "-", lw=1.5, color=_HOUSE_COLORS[0],
                label="数据 (单调)")
        ax.annotate("无迟滞环 (数据单调)", xy=(0.5, 0.5),
                    xycoords="axes fraction", ha="center", va="center",
                    fontsize=10, color="#888")

    ax.set_xlabel(x_label)
    ax.set_ylabel(y_label)
    title = f"{sensor} 迟滞回线" if sensor else "迟滞回线"
    ax.set_title(title)
    ax.legend(fontsize=8)
    ax.grid(alpha=0.3)

    apply_house_style(fig)
    fig.tight_layout()
    return fig


def make_sensor_grade_bar(
    sensors: list[dict],
    *,
    metrics: tuple[str, ...] = ("sigma", "cv", "hysteresis"),
) -> plt.Figure:
    """传感器评级小多图 — 每指标一个子面板，传感器并排便于横比。

    评级编码到柱色 (优/良/合格/FAIL/N/A)。
    图例仅显示色块+中文，严禁出现 #hex。
    无数据 (NaN) 传感器不绘空柱，但保留 x 轴标签 (空白)。

    Args:
        sensors: 传感器指标列表 (按调用方传入顺序)，每项:
            {"name": str, "sigma": float, "cv": float, "hysteresis": float, "grade": str}
        metrics: 要绘制的指标键名 (默认 sigma, cv, hysteresis)

    Returns:
        matplotlib Figure (每指标一列子图)
    """
    setup_chinese_font()
    metric_labels: dict[str, str] = {
        "sigma": "σ (με)",
        "cv": "CV (%)",
        "hysteresis": "迟滞 (με)",
        "repeatability": "重复性 (με)",
        "noise_floor": "噪声底 (με)",
    }

    # 预过滤：确定哪些 sensor 有任一指标可用
    n_sensors = len(sensors)
    names = [s.get("name", f"S{i}") for i, s in enumerate(sensors)]

    if not sensors:
        fig, ax = plt.subplots(figsize=(8, 3))
        _placeholder(ax, "无传感器数据")
        ax.set_title("传感器评级")
        apply_house_style(fig)
        fig.tight_layout()
        return fig

    n_metrics = len(metrics)
    fig, axes = plt.subplots(1, n_metrics, figsize=(3.5 * n_metrics + 2, 4.5),
                             squeeze=False)
    axes = axes[0]  # 1D array

    x = np.arange(n_sensors)
    bar_w = 0.55  # 柱宽

    # 收集出现的评级 (保持顺序)
    seen_grades: list[str] = []
    for s in sensors:
        g = s.get("grade", "N/A")
        if g not in seen_grades:
            seen_grades.append(g)

    for mi, mkey in enumerate(metrics):
        ax = axes[mi]
        label = metric_labels.get(mkey, mkey)

        vals = []
        colors = []
        for s in sensors:
            v = s.get(mkey, np.nan)
            # NaN/None → 跳过该柱，但仍占位保持 x 刻度对齐
            vals.append(v if (v is not None and not np.isnan(v)) else np.nan)
            grade = s.get("grade", "N/A")
            colors.append(_GRADE_COLOR_MAP.get(grade, "#8c8c8c"))

        # 只绘非 NaN 的柱
        bars = []
        for i in range(n_sensors):
            if not np.isnan(vals[i]):
                b = ax.bar(x[i], vals[i], bar_w, color=colors[i], alpha=0.85, zorder=3)
                bars.append(b)

        ax.set_xticks(x)
        ax.set_xticklabels(names, fontsize=7)
        ax.set_ylabel(label)
        ax.grid(alpha=0.3, axis="y")

        # 若无可用数据，在子面板标注
        all_nan = all(np.isnan(v) for v in vals)
        if all_nan:
            ax.text(0.5, 0.5, "无数据", transform=ax.transAxes,
                    ha="center", va="center", fontsize=10, color="#999")
            ax.set_yticks([])

    # ── 统一评级图例 (仅色块 + 中文，无 #hex) ──
    from matplotlib.patches import Patch
    legend_patches = [
        Patch(facecolor=_GRADE_COLOR_MAP.get(g, "#8c8c8c"), label=g)
        for g in seen_grades
    ]
    # 放在第一面板
    axes[0].legend(handles=legend_patches, loc="upper right",
                   fontsize=7, title="评级", title_fontsize=8)

    fig.suptitle("传感器核心指标评级", fontsize=12, fontweight="bold")

    apply_house_style(fig)
    fig.tight_layout(rect=(0, 0, 1, 0.93))  # 给 suptitle 留空间
    return fig


def make_timeseries(
    time: np.ndarray | Sequence[float],
    series: dict[str, Sequence[float]],
    *,
    y_label: str = "值",
    anomalies: dict[str, list[int]] | None = None,
    cleaned: dict[str, Sequence[float]] | None = None,
    trend: bool = False,
) -> plt.Figure:
    """多通道时序折线图 + 可选异常点标注。

    通道名中的下划线自动替换为空格显示。
    若传入 cleaned，同通道原始曲线用虚线、清洗后曲线用实线叠加。
    若 trend=True，对每条线叠加红色线性趋势线。

    Args:
        time: 时间轴 (与各序列等长)
        series: {通道名: 值序列} 映射
        y_label: y 轴标签 (含单位)
        anomalies: {通道名: [异常点索引]} 映射，可选
        cleaned: {通道名: 清洗后值序列} 映射，可选；键应与 series 对齐
        trend: 是否叠加趋势线 (默认 False)

    Returns:
        matplotlib Figure
    """
    setup_chinese_font()
    fig, ax = plt.subplots(figsize=(10, 4))

    t_arr = np.asarray(time, dtype=float)

    if not series:
        _placeholder(ax, "无时序数据")
        ax.set_title("时序曲线")
        apply_house_style(fig)
        fig.tight_layout()
        return fig

    anomalies = anomalies or {}
    cleaned = cleaned or {}
    has_any_data = False

    for ci, (channel, vals) in enumerate(series.items()):
        v_arr = np.asarray(vals, dtype=float)
        if len(v_arr) == 0:
            continue
        # 确保长度匹配
        n_common = min(len(t_arr), len(v_arr))
        if n_common < 1:
            continue
        has_any_data = True
        t_use, v_use = t_arr[:n_common], v_arr[:n_common]

        # 通道名下划线 → 空格
        ch_label = channel.replace("_", " ")

        color = _HOUSE_COLORS[ci % len(_HOUSE_COLORS)]

        # 清洗后曲线 (实线) — 若提供
        if channel in cleaned:
            c_arr = np.asarray(cleaned[channel], dtype=float)
            nc = min(len(t_arr), len(c_arr))
            if nc > 0:
                ax.plot(t_arr[:nc], c_arr[:nc], "-", lw=0.8, color=color,
                        label=f"{ch_label} (清洗)", alpha=0.9)
            # 原始曲线 → 虚线
            ax.plot(t_use, v_use, "--", lw=0.5, color=color,
                    label=f"{ch_label} (原始)", alpha=0.6)
        else:
            ax.plot(t_use, v_use, "-", lw=0.6, color=color, label=ch_label, alpha=0.85)

        # 趋势线 — 按通道色派生深色调，多通道可区分
        if trend:
            valid_fit = ~np.isnan(v_use)
            if valid_fit.sum() >= 2:
                tv, vv = t_use[valid_fit], v_use[valid_fit]
                slope, intercept = np.polyfit(tv, vv, 1)
                trend_line = slope * tv + intercept
                trend_color = _darken(color, 0.55)  # 深色调
                ax.plot(tv, trend_line, "-", lw=1.0, color=trend_color,
                        alpha=0.8, label=f"{ch_label} 趋势")

        # 异常点标注
        anom_idx = anomalies.get(channel, [])
        if anom_idx:
            anom_idx_valid = [i for i in anom_idx if 0 <= i < n_common]
            if anom_idx_valid:
                ax.scatter(t_arr[anom_idx_valid], v_arr[anom_idx_valid],
                           s=30, marker="x", color="#ff4d4f", zorder=5,
                           label=f"{ch_label} 异常" if ci == 0 else "")

    if not has_any_data:
        _placeholder(ax, "所有通道均为空")

    ax.set_xlabel("时间 (h)")
    ax.set_ylabel(y_label)
    ax.set_title("时序曲线")

    handles, labels = ax.get_legend_handles_labels()
    if handles:
        ax.legend(fontsize=7)

    ax.grid(alpha=0.3)

    apply_house_style(fig)
    fig.tight_layout()
    return fig


def make_comparison_overlay(
    time: np.ndarray | Sequence[float],
    sources: dict[str, Sequence[float]],
    *,
    y_label: str = "值",
    show_difference: bool = True,
    reference_source: str | None = None,
) -> plt.Figure:
    """多源叠加时程图 — 多来源数据在同一时间轴叠加。

    上方: 各来源折线叠加 (各自颜色，图例标来源名)
    下方 (show_difference=True): 差值子面板，以 reference_source
    (默认第一个源) 为基准，其他源与其相减。

    Args:
        time: 时间轴
        sources: {来源名: 值序列} 映射
        y_label: y 轴标签 (含单位)
        show_difference: 是否显示下方差值面板 (默认 True)
        reference_source: 差值基准来源名 (默认 sources 第一个 key)

    Returns:
        matplotlib Figure (1 或 2 子图)
    """
    setup_chinese_font()
    t_arr = np.asarray(time, dtype=float)

    if not sources:
        fig, ax = plt.subplots(figsize=(8, 4))
        _placeholder(ax, "无来源数据")
        ax.set_title("多源叠加时程")
        apply_house_style(fig)
        fig.tight_layout()
        return fig

    source_keys = list(sources.keys())
    if reference_source is None:
        reference_source = source_keys[0]

    n_sources = len(sources)
    has_ref = reference_source in sources

    if show_difference and has_ref and n_sources >= 2:
        fig, (ax_top, ax_bot) = plt.subplots(2, 1, figsize=(10, 6),
                                              gridspec_kw={"height_ratios": [2, 1]})
    else:
        fig, ax_top = plt.subplots(figsize=(10, 4))
        ax_bot = None

    # 上方: 多源叠加
    ref_vals = None
    common_len = len(t_arr)
    for ci, (sname, vals) in enumerate(sources.items()):
        v_arr = np.asarray(vals, dtype=float)
        common_len = min(common_len, len(v_arr))
        color = _HOUSE_COLORS[ci % len(_HOUSE_COLORS)]
        ax_top.plot(t_arr[:len(v_arr)], v_arr, "-", lw=0.7, color=color,
                    label=sname, alpha=0.85)
        if sname == reference_source:
            ref_vals = v_arr

    if common_len < 1:
        _placeholder(ax_top, "数据长度不足")
        ax_top.set_title("多源叠加时程")
        apply_house_style(fig)
        fig.tight_layout()
        return fig

    ax_top.set_ylabel(y_label)
    ax_top.set_title("多源叠加时程")
    ax_top.legend(fontsize=7)
    ax_top.grid(alpha=0.3)

    # 下方: 差值面板
    if ax_bot is not None and ref_vals is not None:
        n_ref = len(ref_vals)
        for ci, (sname, vals) in enumerate(sources.items()):
            if sname == reference_source:
                continue
            v_arr = np.asarray(vals, dtype=float)
            nc = min(n_ref, len(v_arr), len(t_arr))
            if nc < 1:
                continue
            diff = v_arr[:nc] - ref_vals[:nc]
            color = _HOUSE_COLORS[ci % len(_HOUSE_COLORS)]
            ax_bot.plot(t_arr[:nc], diff, "-", lw=0.6, color=color,
                        alpha=0.85, label=f"{sname} − {reference_source}")
        ax_bot.axhline(0, color="k", lw=0.6, ls="--")
        ax_bot.set_xlabel("时间 (h)")
        ax_bot.set_ylabel(f"差值 ({_extract_unit(y_label)})")
        ax_bot.set_title("差值 (基准相减)")
        ax_bot.legend(fontsize=7)
        ax_bot.grid(alpha=0.3)

    apply_house_style(fig)
    fig.tight_layout()
    return fig


def make_correlation_scatter(
    x: np.ndarray | Sequence[float],
    y: np.ndarray | Sequence[float],
    *,
    label_x: str = "X",
    label_y: str = "Y",
    corr: float | None = None,
    rmse: float | None = None,
) -> plt.Figure:
    """配对相关散点图 — x vs y 散点 + 对角参考线 + 线性拟合线。

    图例可标注 Pearson 相关系数和 RMSE (仅传入非 None 值才显示)。

    Args:
        x: x 轴数据
        y: y 轴数据 (与 x 等长)
        label_x: x 轴标签 (含单位)
        label_y: y 轴标签 (含单位)
        corr: Pearson 相关系数 (传入则入图例)
        rmse: 均方根误差 (传入则入图例)

    Returns:
        matplotlib Figure
    """
    setup_chinese_font()
    x_arr = np.asarray(x, dtype=float)
    y_arr = np.asarray(y, dtype=float)

    fig, ax = plt.subplots(figsize=(6, 5.5))

    valid = ~(np.isnan(x_arr) | np.isnan(y_arr))
    if valid.sum() < 2:
        _placeholder(ax, f"有效数据点不足 (n={valid.sum()})，无法绘相关散点")
        apply_house_style(fig)
        fig.tight_layout()
        return fig

    xv, yv = x_arr[valid], y_arr[valid]

    # 散点
    ax.scatter(xv, yv, s=12, alpha=0.4, color=_HOUSE_COLORS[0], zorder=3)

    # 对角参考线
    all_vals = np.concatenate([xv, yv])
    diag_min, diag_max = all_vals.min(), all_vals.max()
    pad = (diag_max - diag_min) * 0.05
    ax.plot([diag_min - pad, diag_max + pad],
            [diag_min - pad, diag_max + pad],
            "--", lw=1, color="#888", alpha=0.6, label="对角参考 (y=x)")

    # 线性拟合线
    slope, intercept = np.polyfit(xv, yv, 1)
    xs_line = np.linspace(xv.min(), xv.max(), 100)
    sign = "+" if intercept >= 0 else "−"
    abs_int = abs(intercept)
    fit_label = f"y={slope:.4f}x {sign} {abs_int:.2f}"

    # 构建图例文本
    legend_parts = [fit_label]
    if corr is not None:
        legend_parts.append(f"r={corr:.4f}")
    if rmse is not None:
        legend_parts.append(f"RMSE={rmse:.2f}")
    label = "; ".join(legend_parts)
    ax.plot(xs_line, slope * xs_line + intercept, "-", lw=2,
            color=_HOUSE_COLORS[2], label=label)

    ax.set_xlabel(label_x)
    ax.set_ylabel(label_y)
    ax.set_title("相关散点")
    ax.legend(fontsize=7)
    ax.grid(alpha=0.3)

    apply_house_style(fig)
    fig.tight_layout()
    return fig


def make_distribution(
    series: dict[str, Sequence[float]],
    *,
    y_label: str = "值",
    kind: str = "box",
) -> plt.Figure:
    """分布图 — 各通道箱线图或直方图。

    Args:
        series: {通道名: 值序列} 映射
        y_label: y 轴标签 (含单位)
        kind: "box" (箱线图) 或 "hist" (直方图)

    Returns:
        matplotlib Figure
    """
    setup_chinese_font()

    if not series:
        fig, ax = plt.subplots(figsize=(8, 4))
        _placeholder(ax, "无数据")
        ax.set_title("分布图")
        apply_house_style(fig)
        fig.tight_layout()
        return fig

    channels = list(series.keys())

    if kind == "box":
        fig, ax = plt.subplots(figsize=(max(5, len(channels) * 1.2 + 2), 4.5))
        data_list = []
        labels = []
        colors = []
        for ci, (ch, vals) in enumerate(series.items()):
            v_arr = np.asarray(vals, dtype=float)
            v_clean = v_arr[~np.isnan(v_arr)]
            if len(v_clean) == 0:
                continue
            data_list.append(v_clean)
            labels.append(ch)
            colors.append(_HOUSE_COLORS[ci % len(_HOUSE_COLORS)])

        if not data_list:
            _placeholder(ax, "所有通道均无数据")
            ax.set_title("分布图 (箱线)")
            apply_house_style(fig)
            fig.tight_layout()
            return fig

        bp = ax.boxplot(data_list, patch_artist=True, widths=0.5)
        ax.set_xticklabels(labels)
        for ci, patch in enumerate(bp["boxes"]):
            patch.set_facecolor(colors[ci % len(colors)])
            patch.set_alpha(0.6)
        for median in bp["medians"]:
            median.set_color("#333")

        ax.set_ylabel(y_label)
        ax.set_title("分布图 (箱线)")
        ax.grid(alpha=0.3, axis="y")
    else:  # hist
        fig, ax = plt.subplots(figsize=(8, 4))
        for ci, (ch, vals) in enumerate(series.items()):
            v_arr = np.asarray(vals, dtype=float)
            v_clean = v_arr[~np.isnan(v_arr)]
            if len(v_clean) == 0:
                continue
            color = _HOUSE_COLORS[ci % len(_HOUSE_COLORS)]
            # 阶梯描边 (histtype='step'), 多通道不填充叠糊
            ax.hist(v_clean, bins=30, histtype="step", lw=1.2,
                    color=color, label=ch, density=True)
        # x 轴标签 + 单位
        unit = _extract_unit(y_label)
        xlabel = f"值 ({unit})" if unit else "值"
        ax.set_xlabel(xlabel)
        ax.set_ylabel("密度")
        ax.set_title("分布图 (直方)")
        ax.legend(fontsize=7)
        ax.grid(alpha=0.3, axis="y")

    apply_house_style(fig)
    fig.tight_layout()
    return fig


def make_metric_bar(
    categories: Sequence[str],
    values: Sequence[float],
    *,
    y_label: str = "值",
    unit: str = "",
    colors: Sequence[str] | None = None,
) -> plt.Figure:
    """通用单指标柱状图 — 各分类一项指标。

    默认所有柱同色 (house 色#0)；仅传 colors 时才按其上色。
    图例不含 hex 色码。

    Args:
        categories: 分类名称列表
        values: 各分类的值 (与 categories 等长)
        y_label: y 轴标签
        unit: 单位 (附加到 y 轴标签)
        colors: 可选柱色列表 (缺则所有柱同色)

    Returns:
        matplotlib Figure
    """
    setup_chinese_font()
    cats = list(categories)
    vals = np.asarray(values, dtype=float)

    fig, ax = plt.subplots(figsize=(max(5, len(cats) * 1.0 + 2), 4.5))

    if len(cats) == 0:
        _placeholder(ax, "无数据")
        ax.set_title(y_label if y_label else "指标")
        apply_house_style(fig)
        fig.tight_layout()
        return fig

    x = np.arange(len(cats))
    # 默认单一颜色；仅传 colors 时才按其上色
    if colors is not None:
        use_colors: Sequence[str] = list(colors)
    else:
        use_colors = [_HOUSE_COLORS[0]] * len(cats)

    bars = ax.bar(x, vals, 0.55, color=use_colors, alpha=0.85, zorder=3)

    ax.set_xticks(x)
    ax.set_xticklabels(cats)

    full_ylabel = f"{y_label} ({unit})" if unit else y_label
    ax.set_ylabel(full_ylabel)
    ax.set_title(y_label if y_label else "指标")
    ax.grid(alpha=0.3, axis="y")

    apply_house_style(fig)
    fig.tight_layout()
    return fig


# ═══════════════════════════════════════════════════════════════════════════
# 图表清单 (FigureManifest)
# ═══════════════════════════════════════════════════════════════════════════

@dataclass
class ReportFigure:
    """报告图表条目 — Word/PPT 装配 + LLM 引用的唯一标识。

    Attributes:
        fig_id: 内部唯一 ID (如 "calib_linearity_C2")
        fig_no: 报告中图号 (1, 2, 3, ...)，由 FigureManifest 自动递增
        section: 所属报告节 (如 "标定结果")
        title: 图题 (如 "C2 标定线性度")
        key_stat: 关键统计量简述 (如 "R²=0.9998; σ=2.3με")
        png_path: 已保存 PNG 的绝对路径
        caption: 自动生成的题注 ("图1 C2 标定线性度")
    """
    fig_id: str
    fig_no: int
    section: str
    title: str
    key_stat: str
    png_path: str
    caption: str = ""


class FigureManifest:
    """图表清单 — 记录所有报告图表，供 Word/PPT 装配 + LLM 按号引用。

    用法:
        manifest = FigureManifest()
        manifest.add("calib_C2", "标定结果", "C2 线性度",
                      "R²=0.9998", "/tmp/c2.png")
        ctx = manifest.to_llm_context()
        # "图1 C2 线性度(R²=0.9998); 图2 ..."
    """

    def __init__(self) -> None:
        self._figures: list[ReportFigure] = []
        self._next_no: int = 1

    def add(self, fig_id: str, section: str, title: str,
            key_stat: str, png_path: str) -> ReportFigure:
        """添加图表条目，自动递增图号并生成题注。

        Args:
            fig_id: 内部唯一 ID
            section: 所属报告节
            title: 图题
            key_stat: 关键统计量简述
            png_path: PNG 文件路径

        Returns:
            创建的 ReportFigure 条目
        """
        rf = ReportFigure(
            fig_id=fig_id,
            fig_no=self._next_no,
            section=section,
            title=title,
            key_stat=key_stat,
            png_path=png_path,
            caption=f"图{self._next_no} {title}",
        )
        self._figures.append(rf)
        self._next_no += 1
        return rf

    def to_llm_context(self, max_chars: int = 600) -> str:
        """生成供 LLM 引用的图表摘要行。

        格式: "图1 标题(关键数); 图2 标题(关键数); ..."
        LLM 只能按号引用，禁止虚构图号。
        超 max_chars 截断 + "…(已截断)"。

        Returns:
            LLM 上下文字符串
        """
        if not self._figures:
            return "(无图表)"
        parts = []
        for rf in self._figures:
            entry = f"图{rf.fig_no}: {rf.title}"
            if rf.key_stat:
                entry += f" ({rf.key_stat})"
            parts.append(entry)
        text = "; ".join(parts)
        if len(text) > max_chars:
            text = text[:max_chars - 10] + "…(已截断)"
        return text

    def __iter__(self):
        """按插入顺序遍历图表条目。"""
        return iter(self._figures)

    def __len__(self) -> int:
        return len(self._figures)

    def __getitem__(self, index: int) -> ReportFigure:
        return self._figures[index]

    @property
    def count(self) -> int:
        """已注册图表总数。"""
        return len(self._figures)


# ═══════════════════════════════════════════════════════════════════════════
# 内部辅助
# ═══════════════════════════════════════════════════════════════════════════

def _placeholder(ax: plt.Axes, message: str) -> None:
    """在 Axes 中央绘制占位文本。"""
    ax.text(0.5, 0.5, message, transform=ax.transAxes,
            ha="center", va="center", fontsize=11, color="#999")
    ax.set_xticks([])
    ax.set_yticks([])


def _polygon_area(x: np.ndarray, y: np.ndarray) -> float:
    """Shoelace 公式计算多边形面积 (有符号)。"""
    return 0.5 * abs(np.dot(x, np.roll(y, 1)) - np.dot(y, np.roll(x, 1)))


def _extract_unit(label: str) -> str:
    """从标签中提取单位，如 '应变 (με)' → 'με'。"""
    import re
    m = re.search(r'\((.+?)\)', label)
    return m.group(1) if m else ""


def _darken(hex_color: str, factor: float = 0.6) -> str:
    """将 hex 颜色加深 (factor 越小越深, 1.0=不变)。"""
    from matplotlib.colors import to_rgb, to_hex
    r, g, b = to_rgb(hex_color)
    return to_hex((r * factor, g * factor, b * factor))
