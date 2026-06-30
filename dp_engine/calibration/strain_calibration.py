"""应变标定引擎 — 手填表驱动 (重写版)

理论应变: eps_theory = disp_mm / gauge_length_mm × 10⁶ (με)

指标定义:
  - 灵敏度 k: 全部 load 点 np.polyfit(eps, dλ_pm, 1) 斜率 (pm/με)
  - R²: 拟合决定系数
  - 非线性: max|dλ - fit| / (k × 1000) × 100 (%FS, 1000με 量程)
  - 重复性: 同方向同级别跨循环最大极差 / FS (仅 load 方向)
  - 迟滞: 同循环内 load-unload 最大差值 / FS (从应变域插值)
  - 循环漂移: 每循环单独斜率/截距
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Optional

import numpy as np


# ═══════════════════════════════════════════════════════════════════════
# 数据结构
# ═══════════════════════════════════════════════════════════════════════

@dataclass
class StrainCalibrationConfig:
    gauge_length_mm: float
    mode: str                # "tension_only" | "tension_return"
    n_cycles: int
    grating_kind: str        # "single" | "dual_anchored" | "dual_both"
    anchored_grating: Optional[int] = None
    levels: list[float] = field(default_factory=list)


@dataclass
class GratingStrainResult:
    grating_index: int
    k_pm_per_ue: float = float("nan")
    R2: float = float("nan")
    nonlinearity_pct_fs: float = float("nan")
    repeatability_pct_fs: float = float("nan")
    hysteresis_pct_fs: float = float("nan")
    cycle_drift_slopes: list[float] = field(default_factory=list)
    cycle_drift_intercepts: list[float] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "grating_index": self.grating_index,
            "k_pm_per_ue": self.k_pm_per_ue,
            "R2": self.R2,
            "nonlinearity_pct_fs": self.nonlinearity_pct_fs,
            "repeatability_pct_fs": self.repeatability_pct_fs,
            "hysteresis_pct_fs": self.hysteresis_pct_fs,
            "cycle_drift_slopes": self.cycle_drift_slopes,
            "cycle_drift_intercepts": self.cycle_drift_intercepts,
        }


@dataclass
class StrainCalibrationResult:
    gauge_length_mm: float
    mode: str
    n_cycles: int
    grating_kind: str
    levels: list[float] = field(default_factory=list)
    eps_theory: list[float] = field(default_factory=list)
    gratings: list[GratingStrainResult] = field(default_factory=list)
    dual_avg_strain: Optional[list[float]] = None
    dual_diff_strain: Optional[list[float]] = None

    def to_dict(self) -> dict:
        d = {
            "gauge_length_mm": self.gauge_length_mm,
            "mode": self.mode, "n_cycles": self.n_cycles,
            "grating_kind": self.grating_kind,
            "levels": self.levels, "eps_theory": self.eps_theory,
            "gratings": [g.to_dict() for g in self.gratings],
        }
        if self.dual_avg_strain is not None:
            d["dual_avg_strain"] = self.dual_avg_strain
        if self.dual_diff_strain is not None:
            d["dual_diff_strain"] = self.dual_diff_strain
        return d


# ═══════════════════════════════════════════════════════════════════════
# 理论应变
# ═══════════════════════════════════════════════════════════════════════

def compute_theoretical_strain(levels: list[float], gauge_length_mm: float) -> list[float]:
    if gauge_length_mm <= 0:
        raise ValueError(f"标距必须 > 0")
    return [d / gauge_length_mm * 1e6 for d in levels]


# ═══════════════════════════════════════════════════════════════════════
# 主入口
# ═══════════════════════════════════════════════════════════════════════

def calibrate_strain(
    config: StrainCalibrationConfig,
    readings: dict,
) -> StrainCalibrationResult:
    eps_theory = compute_theoretical_strain(config.levels, config.gauge_length_mm)

    grating_indices = [1, 2] if config.grating_kind in ("dual_anchored", "dual_both") else [1]

    result = StrainCalibrationResult(
        gauge_length_mm=config.gauge_length_mm,
        mode=config.mode, n_cycles=config.n_cycles,
        grating_kind=config.grating_kind,
        levels=list(config.levels), eps_theory=eps_theory,
    )

    for gi in grating_indices:
        if config.grating_kind == "dual_anchored" and gi == config.anchored_grating:
            result.gratings.append(GratingStrainResult(grating_index=gi))
            continue
        gr = _calibrate_one(gi, config, readings.get(gi, {}), eps_theory)
        result.gratings.append(gr)

    # dual_both 额外指标
    if config.grating_kind == "dual_both" and len(result.gratings) == 2:
        g1, g2 = result.gratings
        if not math.isnan(g1.k_pm_per_ue) and not math.isnan(g2.k_pm_per_ue):
            k1, k2 = g1.k_pm_per_ue, g2.k_pm_per_ue
            eps1, eps2 = _dual_indicated_strains(config, readings, k1, k2)
            result.dual_avg_strain = [(a + b) / 2.0 for a, b in zip(eps1, eps2)]
            result.dual_diff_strain = [b - a for a, b in zip(eps1, eps2)]

    return result


# ═══════════════════════════════════════════════════════════════════════
# 单光栅标定
# ═══════════════════════════════════════════════════════════════════════

def _calibrate_one(
    grating_index: int,
    config: StrainCalibrationConfig,
    readings_by_cycle: dict,
    eps_theory: list[float],
) -> GratingStrainResult:
    n = len(eps_theory)
    eps_arr = np.array(eps_theory, dtype=np.float64)

    # ── 提取数据：每个循环的 load_dl(pm) 与 unload_dl(pm) ──
    first_zero = None
    cycle_loads: dict[int, np.ndarray] = {}      # eps_theory 顺序
    cycle_unloads: dict[int, np.ndarray] = {}    # 高→低 eps 顺序（采集方向）

    for cyc in range(1, config.n_cycles + 1):
        rd = readings_by_cycle.get(cyc, {})
        load_wl = rd.get("load", [])
        unload_wl = rd.get("unload") if config.mode == "tension_return" else None

        if not load_wl or len(load_wl) != n:
            continue

        if cyc == 1:
            first_zero = load_wl[0]
        z = first_zero if first_zero is not None else load_wl[0]

        cycle_loads[cyc] = np.array([(wl - z) * 1000.0 for wl in load_wl], dtype=np.float64)

        if unload_wl and len(unload_wl) == n:
            # 采集方向：高→低 eps，保留此顺序便于迟滞计算
            cycle_unloads[cyc] = np.array([(wl - z) * 1000.0 for wl in unload_wl], dtype=np.float64)

    if not cycle_loads:
        return GratingStrainResult(grating_index=grating_index)

    # ── 灵敏度 k：仅用全部 load 点 ──
    all_load_eps = np.tile(eps_arr, len(cycle_loads))
    all_load_dl = np.concatenate(list(cycle_loads.values()))

    slope, intercept = np.polyfit(all_load_eps, all_load_dl, 1)
    k = float(slope)
    dl_pred = k * all_load_eps + intercept
    ss_res = np.sum((all_load_dl - dl_pred) ** 2)
    ss_tot = np.sum((all_load_dl - all_load_dl.mean()) ** 2)
    r2 = float(1.0 - ss_res / ss_tot) if ss_tot > 1e-10 else float("nan")

    # FS = k × 1000 (1000με 量程对应的 pm 值)
    fs = abs(k) * 1000.0

    # ── 非线性：load 点最大残差 / FS ──
    nl = float(np.max(np.abs(all_load_dl - dl_pred)) / fs * 100.0) if fs > 1e-10 else float("nan")

    # ── 重复性：load 方向，同级跨循环最大极差 / FS ──
    rp = float("nan")
    if len(cycle_loads) >= 2:
        max_rp = 0.0
        for li in range(n):
            vals = [ld[li] for ld in cycle_loads.values() if li < len(ld)]
            if len(vals) >= 2:
                rng = max(vals) - min(vals)
                if rng > max_rp:
                    max_rp = rng
        rp = float(max_rp / fs * 100.0) if fs > 1e-10 else float("nan")

    # ── 迟滞：同循环 load-unload 同一应变下最大插值差 / FS ──
    hy = float("nan")
    if config.mode == "tension_return" and cycle_unloads:
        max_hy = 0.0
        for cyc in sorted(cycle_loads.keys()):
            ul = cycle_unloads.get(cyc)
            if ul is None:
                continue
            ld = cycle_loads[cyc]
            # unload 采集顺序: 高ε→低ε。先反转到 asc 顺序 (低ε→高ε)
            ul_dl_asc = ul[::-1]                     # asc dL
            ul_eps_asc = np.array(eps_theory[::-1][::-1], dtype=np.float64)  # = eps_theory, asc
            # 插值 unload 到 load ε 点
            ul_interp = np.interp(eps_arr, ul_eps_asc, ul_dl_asc)
            diff = np.max(np.abs(ul_interp - ld))
            if diff > max_hy:
                max_hy = diff
        hy = float(max_hy / fs * 100.0) if fs > 1e-10 else float("nan")

    # ── 循环漂移 ──
    drift_s, drift_i = [], []
    for cyc in sorted(cycle_loads.keys()):
        s, i = np.polyfit(eps_arr, cycle_loads[cyc], 1)
        drift_s.append(float(s))
        drift_i.append(float(i))

    return GratingStrainResult(
        grating_index=grating_index,
        k_pm_per_ue=k, R2=r2,
        nonlinearity_pct_fs=nl,
        repeatability_pct_fs=rp,
        hysteresis_pct_fs=hy,
        cycle_drift_slopes=drift_s,
        cycle_drift_intercepts=drift_i,
    )


# ═══════════════════════════════════════════════════════════════════════
# 双栅指示应变
# ═══════════════════════════════════════════════════════════════════════

def _dual_indicated_strains(config, readings: dict, k1: float, k2: float):
    eps1, eps2 = [], []
    for gi, rd, k in [(1, readings.get(1, {}), k1), (2, readings.get(2, {}), k2)]:
        if not rd or 1 not in rd or not rd[1].get("load"):
            continue
        zero = rd[1]["load"][0]
        all_eps = []
        for cyc in range(1, config.n_cycles + 1):
            ld = rd.get(cyc, {}).get("load", [])
            for wl in ld:
                dl = (wl - zero) * 1000.0
                all_eps.append(dl / k if abs(k) > 1e-10 else float("nan"))
        if gi == 1:
            eps1 = all_eps
        else:
            eps2 = all_eps
    return eps1, eps2
