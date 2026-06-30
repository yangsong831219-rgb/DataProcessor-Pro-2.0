"""温度标定引擎 — 连续波长文件 + 自动平台检测 + 灵敏度回归 + 解耦诊断

移植 + 通用化 FBG.py 核心算法，所有硬编码参数改为函数参数。
"""

from __future__ import annotations

from typing import Optional, cast

import numpy as np
import pandas as pd
from sklearn.cluster import KMeans

from .step_extractor import detect_plateaus


# ═══════════════════════════════════════════════════════════════════════
# 1. 数据加载
# ═══════════════════════════════════════════════════════════════════════

def compute_dL(
    df: pd.DataFrame,
    wavelength_cols: list[str],
) -> tuple[pd.DataFrame, pd.Series]:
    """计算波长漂移 dL (pm) — 纯函数，零 I/O。

    对已解析的 DataFrame 计算: dL = (λ - λ_base) × 1000 (pm)

    Args:
        df: 已解析的数据 DataFrame，列名含 wavelength_cols
        wavelength_cols: 波长列名列表

    Returns:
        (df_out, base_row): df_out 新增 {col}_d 列
    """
    # 转换波长为数值
    for col in wavelength_cols:
        if col not in df.columns:
            raise KeyError(f"波长列不存在: {col}。可用列: {list(df.columns)[:10]}")
        if not pd.api.types.is_numeric_dtype(df[col]):
            df = df.astype(object)
            df[col] = pd.to_numeric(df[col], errors="coerce")

    # 丢弃波长为空的行
    valid = df[wavelength_cols].notna().all(axis=1)
    df = cast(pd.DataFrame, df[valid].reset_index(drop=True))

    if len(df) == 0:
        raise ValueError("有效波长数据为空")

    base = df.iloc[0]

    for col in wavelength_cols:
        dcol = f"{col}_d"
        df[dcol] = (df[col].astype(float) - float(base[col])) * 1000.0

    print(f"  有效数据: {len(df)} 行")
    for col in wavelength_cols:
        dcol = f"{col}_d"
        values = pd.to_numeric(df[dcol], errors="coerce")
        print(
            f"  {col}: 基准={base[col]:.5f} nm, "
            f"漂移=[{values.min():.0f}, {values.max():.0f}] pm"
        )

    return df, base


def load_continuous(
    file_path: str,
    wavelength_cols: list[str],
    encoding: str = "utf-8",
    separator: str = "\t",
) -> tuple[pd.DataFrame, pd.Series]:
    """读入连续波长文件，计算相对波长漂移 dL (pm)。[保留向后兼容]"""
    df = pd.read_csv(file_path, sep=separator, encoding=encoding)
    return compute_dL(df, wavelength_cols)


# ═══════════════════════════════════════════════════════════════════════
# 2. 平台 → 设定温度映射 (KMeans)
# ═══════════════════════════════════════════════════════════════════════

def assign_setpoints(
    plateau_df: pd.DataFrame,
    setpoints: list[float],
    *,
    proxy_column: str = "proxy",
    n_init: int = 10,
    random_state: int = 42,
) -> pd.DataFrame:
    """KMeans 聚类将平台映射到已知设定温度。

    按代理信号聚类中心升序 → 对应设定温度升序。

    Args:
        plateau_df: detect_plateaus 输出的平台表
        setpoints: 设定温度序列（升序），如 [10, 20, 30, 40, 50, 60, 70]
        proxy_column: 代理信号列名
        n_init: KMeans 初始次数
        random_state: 随机种子 (固定保证可重复)

    Returns:
        plateau_df 副本，新增 'T_set' 列
    """
    if plateau_df.empty:
        P = plateau_df.copy()
        P["T_set"] = float("nan")
        return P

    proxy_vals = plateau_df[[proxy_column]].values
    n_levels = len(setpoints)

    km = KMeans(
        n_clusters=n_levels,
        n_init=n_init,
        random_state=random_state,
    ).fit(proxy_vals)

    # 聚类中心排序：升序中心 → 升序温度
    order = np.argsort(km.cluster_centers_.ravel())
    label_to_temp = {order[k]: setpoints[k] for k in range(n_levels)}

    P = plateau_df.copy()
    P["T_set"] = [label_to_temp[l] for l in km.labels_]

    # 打印统计
    for t in sorted(P["T_set"].unique()):
        sub = P[P["T_set"] == t]
        print(
            f"    {t:3.0f} °C: {len(sub):2d} 个平台, "
            f"代理 dL 均值={sub[proxy_column].mean():7.0f} pm"
        )

    return P


# ═══════════════════════════════════════════════════════════════════════
# 3. 灵敏度线性回归
# ═══════════════════════════════════════════════════════════════════════

def regress_sensitivity(
    plateau_df: pd.DataFrame,
    dL_column: str,
    label: str = "",
) -> dict:
    """对一个光栅做 dL vs T_set 线性回归。

    dL = S_eff × T + intercept
    S_eff = 真实有效温度灵敏度 (pm/°C)
    T_base = -intercept / S_eff (推断的数据起始基准温度)

    Args:
        plateau_df: 带 T_set 列的平台表
        dL_column: 该光栅的 dL 列名，如 'A1-W1_d'
        label: 显示标签 (优先用暗号名)

    Returns:
        {"slope": S_eff, "intercept": intercept, "T_base": T_base, "r2": R², "display": label}
    """
    if plateau_df.empty:
        return {"slope": float("nan"), "intercept": float("nan"),
                "T_base": float("nan"), "r2": float("nan"), "display": label or dL_column}

    x = plateau_df["T_set"].values.astype(np.float64)
    y = plateau_df[dL_column].values.astype(np.float64)

    valid = ~np.isnan(y)
    x, y = x[valid], y[valid]

    if len(x) < 2:
        return {"slope": float("nan"), "intercept": float("nan"),
                "T_base": float("nan"), "r2": float("nan"), "display": label or dL_column}

    slope, intercept = np.polyfit(x, y, 1)
    T_base = -intercept / slope if abs(slope) > 1e-10 else float("nan")

    y_pred = slope * x + intercept
    ss_res = np.sum((y - y_pred) ** 2)
    ss_tot = np.sum((y - y.mean()) ** 2)
    r2 = 1.0 - ss_res / ss_tot if ss_tot > 1e-10 else float("nan")

    display = label or dL_column
    if label:
        print(
            f"    {display:8s}: 灵敏度 = {slope:6.2f} pm/°C, "
            f"基准温度 = {T_base:5.1f} °C, 线性度 = {r2:.6f}"
        )

    return {
        "slope": float(slope),
        "intercept": float(intercept),
        "T_base": float(T_base),
        "r2": float(r2),
        "display": display,
    }


# ═══════════════════════════════════════════════════════════════════════
# 4. 双波长矩阵解耦
# ═══════════════════════════════════════════════════════════════════════

def decouple(
    dl1: float | np.ndarray,
    dl2: float | np.ndarray,
    Ke1: float,
    KT1: float,
    Ke2: float,
    KT2: float,
) -> tuple[float | np.ndarray, float | np.ndarray]:
    """双波长矩阵解耦。

    | dL1 |   | Ke1  KT1 | | ε  |
    | dL2 | = | Ke2  KT2 | | dT |

    求逆解出 ε 和 dT。

    Args:
        dl1, dl2: 两光栅的波长漂移 (pm)
        Ke1, Ke2: 应变灵敏度 (pm/με)
        KT1, KT2: 温度灵敏度 (pm/°C)

    Returns:
        (eps, dT) — 应变 (με) 和温度变化 (°C)
    """
    det = Ke1 * KT2 - KT1 * Ke2

    if abs(det) < 1e-9:
        # 奇异矩阵 → 返回 NaN
        scalar_out = isinstance(dl1, (int, float))
        eps = np.full_like(np.asarray(dl1, dtype=np.float64), np.nan)
        dT = np.full_like(np.asarray(dl2, dtype=np.float64), np.nan)
        if scalar_out:
            return float(np.nan), float(np.nan)
        return eps, dT

    eps = (KT2 * dl1 - KT1 * dl2) / det
    dT = (Ke1 * dl2 - Ke2 * dl1) / det
    return eps, dT


# ═══════════════════════════════════════════════════════════════════════
# 5. 标定 vs 实测对比
# ═══════════════════════════════════════════════════════════════════════

def compare_given_vs_measured(
    grating_label: str,
    given_KT: float,
    measured_S_eff: float,
    Ke: float,
) -> dict:
    """对比标定 KT 与实测 S_eff，计算 apparent strain。

    Returns:
        {
            "grating": grating_label,
            "given_KT": given_KT,
            "measured_S_eff": measured_S_eff,
            "diff_pm_per_C": measured_S_eff - given_KT,
            "apparent_strain_ppm_per_C": (measured_S_eff - given_KT) / Ke,
        }
    """
    diff = measured_S_eff - given_KT
    return {
        "grating": grating_label,
        "given_KT": given_KT,
        "measured_S_eff": measured_S_eff,
        "diff_pm_per_C": diff,
        "apparent_strain_ppm_per_C": diff / Ke if abs(Ke) > 1e-10 else float("nan"),
    }


# ═══════════════════════════════════════════════════════════════════════
# 6. 全流程 orchestrator
# ═══════════════════════════════════════════════════════════════════════

def run_temperature_calibration(
    df: pd.DataFrame,
    wavelength_cols: list[str],
    setpoints: list[float],
    sensor_defs: dict,
    *,
    rolling_window: int = 25,
    std_percentile: float = 45.0,
    min_plateau_samples: int = 180,
    head_trim_ratio: float = 0.70,
    sample_interval_s: float = 2.0,
) -> dict:
    """运行完整温度标定流程。

    Args:
        df: load_continuous 输出的 DataFrame (含 _d 列)
        wavelength_cols: 波长列名列表
        setpoints: 设定温度 (°C)
        sensor_defs: {
            "S1": {
                "grating_strain": "A1-W1",  # 应变栅
                "grating_temp": "A1-W2",     # 温度栅
                "Ke1": 1.2, "KT1": 26.2,     # 标定系数
                "Ke2": 1.2, "KT2": 29.15,
            },
            ...
        }
        rolling_window, std_percentile, min_plateau_samples, head_trim_ratio:
            平台检测参数
        sample_interval_s: 采样间隔 (秒)

    Returns:
        {
            "plateaus": plateau_df,
            "S_eff": {grating_col: {"slope": ..., "T_base": ..., "r2": ...}},
            "comparisons": [...],
            "sensors": {
                sensor_name: {
                    "eps_orig": np.array, "dT_orig": np.array,
                    "eps_corr": np.array, "dT_corr": np.array,
                    "T_abs": np.array,
                    "S1": float, "S2": float, "T_base": float,
                }
            },
            "time_h": np.array,
        }
    """
    # ── 平台检测 ──
    print("\n[1] 检测温度阶梯平台...")
    P = detect_plateaus(
        df,
        wavelength_cols,
        rolling_window=rolling_window,
        std_percentile=std_percentile,
        min_plateau_samples=min_plateau_samples,
        head_trim_ratio=head_trim_ratio,
    )

    # ── 温度映射 ──
    print(f"\n[2] 将 {len(P)} 个平台映射到设定温度...")
    P = assign_setpoints(P, setpoints)

    # ── 灵敏度回归 ──
    print("\n[3] 各光栅 dL vs T 线性回归 → 真实灵敏度:")
    dL_cols = [f"{c}_d" for c in wavelength_cols]
    S_eff = {}
    for dc in dL_cols:
        S_eff[dc] = regress_sensitivity(P, dc, dc)

    # ── 标定 vs 实测 对比 ──
    print("\n[4] 标定值 vs 实测值 对比:")
    comparisons = []
    for s_name, sdef in sensor_defs.items():
        for gkey, given_KT_key, Ke_key in [
            ("grating_strain", "KT1", "Ke1"),
            ("grating_temp", "KT2", "Ke2"),
        ]:
            gcol = f"{sdef[gkey]}_d"
            if gcol not in S_eff:
                continue
            comp = compare_given_vs_measured(
                sdef[gkey],
                sdef[given_KT_key],
                S_eff[gcol]["slope"],
                sdef[Ke_key],
            )
            comparisons.append(comp)
            print(
                f"    {comp['grating']}: 标定={comp['given_KT']:6.2f}  "
                f"实测={comp['measured_S_eff']:6.2f}  "
                f"差={comp['diff_pm_per_C']:+6.2f} pm/°C  "
                f"→ apparent strain={comp['apparent_strain_ppm_per_C']:+5.1f} ppm/°C"
            )

    # ── 解耦 ──
    print("\n[5] 解耦分析...")
    t_hour = np.arange(len(df)) * sample_interval_s / 3600.0
    sensor_results = {}

    for s_name, sdef in sensor_defs.items():
        gs = sdef["grating_strain"]
        gt = sdef["grating_temp"]
        dl1 = df[f"{gs}_d"].values
        dl2 = df[f"{gt}_d"].values

        S1 = S_eff[f"{gs}_d"]["slope"]
        S2 = S_eff[f"{gt}_d"]["slope"]
        T_base = (
            S_eff[f"{gs}_d"]["T_base"] + S_eff[f"{gt}_d"]["T_base"]
        ) / 2.0

        # 标定系数解耦
        eps_orig, dT_orig = decouple(
            dl1, dl2,
            sdef["Ke1"], sdef["KT1"],
            sdef["Ke2"], sdef["KT2"],
        )
        # 修正解耦 (实测 S_eff 替换标定 KT)
        eps_corr, dT_corr = decouple(
            dl1, dl2,
            sdef["Ke1"], S1,
            sdef["Ke2"], S2,
        )
        T_abs = dT_corr + T_base

        sensor_results[s_name] = {
            "eps_orig": eps_orig,
            "dT_orig": dT_orig,
            "eps_corr": eps_corr,
            "dT_corr": dT_corr,
            "T_abs": T_abs,
            "S1": S1,
            "S2": S2,
            "T_base": T_base,
        }

        print(
            f"    [{s_name}] S1={S1:.2f}, S2={S2:.2f}, T_base={T_base:.1f}°C, "
            f"原始 e_std={np.nanstd(eps_orig):.1f} με, "
            f"修正 e_std={np.nanstd(eps_corr):.1f} με"
        )

    return {
        "plateaus": P,
        "S_eff": S_eff,
        "comparisons": comparisons,
        "sensors": sensor_results,
        "time_h": t_hour,
    }
