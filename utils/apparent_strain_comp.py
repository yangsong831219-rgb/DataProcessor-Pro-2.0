"""表观应变-温度补偿模块 (Phase 3a: LUT + 多项式两种形式)

在解耦流程之后接入：对每支传感器构建 ε_apparent(T) 补偿模型，
在分析路径中应用补偿，实现温度交叉敏感度的离线修正。

支持两种补偿形式:
- "lut": ApparentStrainLUT — 分箱线性插值查表 (Phase 1)
- "poly": ApparentStrainPoly — 多项式拟合 (在 x=T-T_base 上 polyfit)

核心约定：
- 越界钳位到端点值，严禁线性外推 (多项式 4 阶在标定区外发散极快)
- oob_mask 标出所有 T ∉ [T_min, T_max] 的点，调用方弹告警
- 零点参考: 插值/多项式使 eps_app(T_base) = 0
- 统一应用接口: CompensationModel + apply_compensation_model()
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional, Union

import numpy as np


# ═══════════════════════════════════════════════════════════════════════
# 数据模型
# ═══════════════════════════════════════════════════════════════════════


@dataclass
class ApparentStrainLUT:
    """单支传感器的表观应变-温度补偿查找表。

    Attributes:
        sensor: 传感器名 (如 "C2")
        T_base: 参考温度 (°C)，eps_app(T_base) = 0
        T_grid: 温度网格点 (°C)，单调递增
        eps_app: 对应表观应变 (με)，长度与 T_grid 一致
        T_min: 有效温度下限 (°C)
        T_max: 有效温度上限 (°C)
        n_cycles: 建表使用的温度循环数
        source: 建表数据来源标签 (如 "dwell_tail" / "continuous_sweep")
    """
    sensor: str
    T_base: float
    T_grid: list[float]
    eps_app: list[float]
    T_min: float
    T_max: float
    n_cycles: int
    source: str = ""

    def __post_init__(self):
        if len(self.T_grid) != len(self.eps_app):
            raise ValueError(
                f"T_grid 与 eps_app 长度不一致: {len(self.T_grid)} vs {len(self.eps_app)}"
            )
        if len(self.T_grid) < 2:
            raise ValueError("LUT 至少需要 2 个点才能插值")

    def to_dict(self) -> dict:
        return {
            "sensor": self.sensor,
            "T_base": self.T_base,
            "T_grid": self.T_grid,
            "eps_app": self.eps_app,
            "T_min": self.T_min,
            "T_max": self.T_max,
            "n_cycles": self.n_cycles,
            "source": self.source,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "ApparentStrainLUT":
        return cls(
            sensor=str(d.get("sensor", "")),
            T_base=float(d.get("T_base", 25.0)),
            T_grid=[float(v) for v in d.get("T_grid", [])],
            eps_app=[float(v) for v in d.get("eps_app", [])],
            T_min=float(d.get("T_min", 0.0)),
            T_max=float(d.get("T_max", 100.0)),
            n_cycles=int(d.get("n_cycles", 1)),
            source=str(d.get("source", "")),
        )


# ═══════════════════════════════════════════════════════════════════════
# 建表
# ═══════════════════════════════════════════════════════════════════════


def build_apparent_strain_lut(
    T: np.ndarray,
    eps: np.ndarray,
    T_base: float,
    *,
    bin_width: float = 1.0,
    min_count: int = 10,
    dwell_mask: Optional[np.ndarray] = None,
    sensor: str = "",
    n_cycles: int = 1,
    source: str = "",
) -> ApparentStrainLUT:
    """从解耦输出的 (T, eps) 时序构建表观应变补偿表。

    两步流程:
    1. 选样: dwell_mask 给定 → 只用稳定停留尾段 (台阶实验);
             否则 → 按 T 分箱取均值 (连续扫)。
    2. 零点: 插值使 eps_app(T_base) = 0。

    Args:
        T: 温度序列 (°C), shape (N,)
        eps: 解耦应变序列 (με), shape (N,)
        T_base: 参考温度 (°C)，LUT 在此温度下输出 0
        bin_width: 温度分箱宽度 (°C)，仅连续扫模式使用
        min_count: 每箱最少样本数，不足则跳过该箱
        dwell_mask: 停留段掩码 (bool array)，True = 使用该点
        sensor: 传感器名
        n_cycles: 温度循环数
        source: 数据来源标签

    Returns:
        ApparentStrainLUT

    Raises:
        ValueError: 有效数据点不足以建表
    """
    T = np.asarray(T, dtype=np.float64)
    eps = np.asarray(eps, dtype=np.float64)

    if len(T) < min_count:
        raise ValueError(
            f"数据点不足: 共 {len(T)} 点，建表至少需要 {min_count} 点"
        )

    # ── 1. 选样 ──
    if dwell_mask is not None:
        dwell_mask = np.asarray(dwell_mask, dtype=bool)
        if not np.any(dwell_mask):
            raise ValueError("dwell_mask 全为 False，无有效停留段样本")
        T_use = T[dwell_mask]
        eps_use = eps[dwell_mask]
        src_label = source or "dwell_tail"
    else:
        T_use = T
        eps_use = eps
        src_label = source or "continuous_sweep"

    # ── 2. 按 T 分箱取均值 ──
    T_min_data = float(np.min(T_use))
    T_max_data = float(np.max(T_use))
    n_bins = max(2, int(np.ceil((T_max_data - T_min_data) / bin_width)))

    T_grid: list[float] = []
    eps_mean: list[float] = []

    for i in range(n_bins):
        lo = T_min_data + i * bin_width
        hi = lo + bin_width
        in_bin = (T_use >= lo) & (T_use < hi)
        # 最后一箱包含上界
        if i == n_bins - 1:
            in_bin = (T_use >= lo) & (T_use <= hi)
        n_in = int(np.sum(in_bin))
        if n_in < min_count:
            continue
        T_grid.append((lo + hi) / 2.0)
        eps_mean.append(float(np.mean(eps_use[in_bin])))

    if len(T_grid) < 2:
        raise ValueError(
            f"有效分箱不足: 仅 {len(T_grid)} 箱满足 min_count={min_count}，"
            f"无法建表。请减小 bin_width 或 min_count"
        )

    # ── 3. 零点修正: eps_app(T_base) = 0 ──
    T_grid_arr = np.array(T_grid, dtype=np.float64)
    eps_mean_arr = np.array(eps_mean, dtype=np.float64)
    eps_at_base = float(np.interp(T_base, T_grid_arr, eps_mean_arr))
    eps_app_arr = eps_mean_arr - eps_at_base

    return ApparentStrainLUT(
        sensor=sensor,
        T_base=T_base,
        T_grid=[float(v) for v in T_grid_arr],
        eps_app=[float(v) for v in eps_app_arr],
        T_min=float(np.min(T_grid_arr)),
        T_max=float(np.max(T_grid_arr)),
        n_cycles=n_cycles,
        source=src_label,
    )


# ═══════════════════════════════════════════════════════════════════════
# 查表补偿
# ═══════════════════════════════════════════════════════════════════════


def apply_apparent_strain_comp(
    eps_dec: np.ndarray,
    T: np.ndarray,
    lut: ApparentStrainLUT,
    *,
    t_lowpass_win: Optional[int] = None,
) -> tuple[np.ndarray, np.ndarray]:
    """查表补偿解耦应变的表观温度漂移。

    eps_corr[i] = eps_dec[i] - eps_app(T[i])
    其中 eps_app(T[i]) 从 LUT 线性插值得到。

    越界行为:
    - T < T_min → 钳位到 eps_app(T_min)
    - T > T_max → 钳位到 eps_app(T_max)
    - 严禁线性外推 (两端陡且非单调)
    - oob_mask[i] = True 标记越界点

    Args:
        eps_dec: 解耦应变序列 (με), shape (N,)
        T: 温度序列 (°C), shape (N,) — 用于查表
        lut: 表观应变补偿表
        t_lowpass_win: 对查表用 T 做移动平均的窗口宽度 (奇数)。
                       只滤 T，不滤应变。None = 不过滤。

    Returns:
        (eps_corr, oob_mask):
          - eps_corr: 补偿后应变 (με), shape (N,)
          - oob_mask: 越界标记 (bool), shape (N,) — True = T 越界

    Raises:
        ValueError: eps_dec 与 T 长度不一致
    """
    eps_dec = np.asarray(eps_dec, dtype=np.float64)
    T = np.asarray(T, dtype=np.float64)

    if eps_dec.shape != T.shape:
        raise ValueError(
            f"eps_dec 与 T 长度不一致: {eps_dec.shape} vs {T.shape}"
        )

    T_grid = np.array(lut.T_grid, dtype=np.float64)
    eps_app_grid = np.array(lut.eps_app, dtype=np.float64)

    # ── 可选: T 低通滤波 (移动平均，只滤 T) ──
    T_lookup = T.copy()
    if t_lowpass_win is not None and t_lowpass_win > 1:
        if t_lowpass_win % 2 == 0:
            t_lowpass_win += 1  # 强制奇数，保证对称
        kernel = np.ones(t_lowpass_win) / t_lowpass_win
        T_lookup = np.convolve(T, kernel, mode="same")

    # ── 越界检测 ──
    oob_mask = (T_lookup < lut.T_min) | (T_lookup > lut.T_max)

    # ── 钳位 T_lookup 到 LUT 范围 ──
    T_clamped = np.clip(T_lookup, lut.T_min, lut.T_max)

    # ── 插值: np.interp 默认 left/right = 端点值 → 钳位 ──
    eps_app = np.interp(T_clamped, T_grid, eps_app_grid)

    eps_corr = eps_dec - eps_app

    return eps_corr, oob_mask


# ═══════════════════════════════════════════════════════════════════════
# 多项式补偿模型
# ═══════════════════════════════════════════════════════════════════════


@dataclass
class ApparentStrainPoly:
    """单支传感器的表观应变-温度多项式补偿模型。

    在中心化温度 x = T - T_base 上拟合，改善高阶数值条件。
    eps_app(T) = poly(x) - poly(0)，使 eps_app(T_base) = 0。

    Attributes:
        sensor: 传感器名
        T_base: 参考温度 (degC)
        coeffs: 多项式系数 [c0, c1, c2, ...]，最高次在前 (np.polyfit 惯例)
        order: 多项式阶数
        T_min: 有效温度下限 (degC)
        T_max: 有效温度上限 (degC)
        n_cycles: 建表使用的温度循环数
        source: 数据来源标签
    """
    sensor: str
    T_base: float
    coeffs: list[float]
    order: int
    T_min: float
    T_max: float
    n_cycles: int
    source: str = ""

    def __post_init__(self):
        if len(self.coeffs) != self.order + 1:
            raise ValueError(
                f"coeffs length ({len(self.coeffs)}) != order+1 ({self.order + 1})"
            )
        if self.order < 1:
            raise ValueError(f"polynomial order must be >= 1, got {self.order}")

    def to_dict(self) -> dict:
        return {
            "sensor": self.sensor,
            "T_base": self.T_base,
            "coeffs": self.coeffs,
            "order": self.order,
            "T_min": self.T_min,
            "T_max": self.T_max,
            "n_cycles": self.n_cycles,
            "source": self.source,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "ApparentStrainPoly":
        return cls(
            sensor=str(d.get("sensor", "")),
            T_base=float(d.get("T_base", 25.0)),
            coeffs=[float(v) for v in d.get("coeffs", [])],
            order=int(d.get("order", 2)),
            T_min=float(d.get("T_min", 0.0)),
            T_max=float(d.get("T_max", 100.0)),
            n_cycles=int(d.get("n_cycles", 1)),
            source=str(d.get("source", "")),
        )


def fit_apparent_strain_poly(
    T: np.ndarray,
    eps: np.ndarray,
    T_base: float,
    order: int,
    *,
    sensor: str = "",
    n_cycles: int = 1,
    source: str = "",
) -> ApparentStrainPoly:
    """在中心化温度 x = T - T_base 上拟合多项式补偿模型。

    零参考: 拟合后减去 poly(0)，使 eps_app(T_base) = 0。

    Args:
        T: 温度序列 (degC), shape (N,)
        eps: 解耦应变序列 (ue), shape (N,)
        T_base: 参考温度 (degC)
        order: 多项式阶数 (建议 2-4)
        sensor: 传感器名
        n_cycles: 温度循环数
        source: 数据来源标签

    Returns:
        ApparentStrainPoly

    Raises:
        ValueError: 数据点不足或阶数不合法
    """
    T = np.asarray(T, dtype=np.float64)
    eps = np.asarray(eps, dtype=np.float64)

    if len(T) < order + 2:
        raise ValueError(
            f"data points insufficient: {len(T)} pts, "
            f"need at least {order + 2} for order {order}"
        )
    if order < 1:
        raise ValueError(f"polynomial order must be >= 1, got {order}")

    # center on T_base
    x = T - T_base

    # polyfit: highest power first
    coeffs = list(np.polyfit(x, eps, order))

    # zero reference: subtract poly(0) so eps_app(T_base) = 0
    poly_at_0 = float(np.polyval(coeffs, 0.0))
    coeffs[-1] = coeffs[-1] - poly_at_0

    T_min = float(np.min(T))
    T_max = float(np.max(T))

    return ApparentStrainPoly(
        sensor=sensor,
        T_base=T_base,
        coeffs=[float(c) for c in coeffs],
        order=order,
        T_min=T_min,
        T_max=T_max,
        n_cycles=n_cycles,
        source=source or f"polyfit_order{order}",
    )


# ═══════════════════════════════════════════════════════════════════════
# 统一应用接口
# ═══════════════════════════════════════════════════════════════════════


@dataclass
class CompensationModel:
    """统一补偿模型 -- LUT or polynomial.

    Attributes:
        form: "lut" | "poly"
        model: ApparentStrainLUT | ApparentStrainPoly
    """
    form: str
    model: Union[ApparentStrainLUT, ApparentStrainPoly]

    def __post_init__(self):
        if self.form not in ("lut", "poly"):
            raise ValueError(f"unsupported form: {self.form}, use 'lut' or 'poly'")
        if self.form == "lut" and not isinstance(self.model, ApparentStrainLUT):
            raise TypeError(f"form='lut' but model is {type(self.model).__name__}")
        if self.form == "poly" and not isinstance(self.model, ApparentStrainPoly):
            raise TypeError(f"form='poly' but model is {type(self.model).__name__}")

    @property
    def T_min(self) -> float:
        return self.model.T_min

    @property
    def T_max(self) -> float:
        return self.model.T_max

    def to_dict(self) -> dict:
        return {
            "form": self.form,
            "model": self.model.to_dict(),
        }

    @classmethod
    def from_dict(cls, d: dict) -> "CompensationModel":
        form = str(d.get("form", "lut"))
        model_d = d.get("model", {})
        if form == "poly":
            model: Union[ApparentStrainLUT, ApparentStrainPoly] = \
                ApparentStrainPoly.from_dict(model_d)
        else:
            model = ApparentStrainLUT.from_dict(model_d)
        return cls(form=form, model=model)


def apply_compensation_model(
    eps_dec: np.ndarray,
    T: np.ndarray,
    cm: CompensationModel,
    *,
    t_lowpass_win: Optional[int] = None,
) -> tuple[np.ndarray, np.ndarray]:
    """统一补偿应用: LUT or polynomial.

    eps_corr[i] = eps_dec[i] - eps_app(T[i])

    OOB behavior (both forms):
    - T < T_min -> clamp to endpoint value
    - T > T_max -> clamp to endpoint value
    - Polynomial: NO extrapolation (order-4 diverges fast outside calibration range)
    - oob_mask[i] = True marks OOB points

    Args:
        eps_dec: decoupled strain (ue), shape (N,)
        T: temperature (degC), shape (N,)
        cm: CompensationModel (LUT or poly)
        t_lowpass_win: optional T lowpass window

    Returns:
        (eps_corr, oob_mask)

    Raises:
        ValueError: length mismatch
    """
    eps_dec = np.asarray(eps_dec, dtype=np.float64)
    T = np.asarray(T, dtype=np.float64)

    if eps_dec.shape != T.shape:
        raise ValueError(
            f"eps_dec and T length mismatch: {eps_dec.shape} vs {T.shape}"
        )

    # optional T lowpass
    T_lookup = T.copy()
    if t_lowpass_win is not None and t_lowpass_win > 1:
        if t_lowpass_win % 2 == 0:
            t_lowpass_win += 1
        kernel = np.ones(t_lowpass_win) / t_lowpass_win
        T_lookup = np.convolve(T, kernel, mode="same")

    # OOB detection
    oob_mask = (T_lookup < cm.T_min) | (T_lookup > cm.T_max)

    # clamp
    T_clamped = np.clip(T_lookup, cm.T_min, cm.T_max)

    # evaluate by form
    if cm.form == "lut":
        lut: ApparentStrainLUT = cm.model  # type: ignore[assignment]
        T_grid = np.array(lut.T_grid, dtype=np.float64)
        eps_grid = np.array(lut.eps_app, dtype=np.float64)
        eps_app = np.interp(T_clamped, T_grid, eps_grid)
    else:
        poly: ApparentStrainPoly = cm.model  # type: ignore[assignment]
        x = T_clamped - poly.T_base
        eps_app = np.polyval(poly.coeffs, x)

    eps_corr = eps_dec - eps_app
    return eps_corr, oob_mask
