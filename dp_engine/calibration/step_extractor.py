"""平台检测 — 移植 + 通用化 FBG.py detect_plateaus()

从温度循环连续波长数据中，利用滚动标准差识别稳定平台段。

算法:
  1. 用温度栅均值作为代理信号
  2. 计算滚动标准差 -> 低百分位阈值判定稳定段
  3. 连续稳定段分组，过滤过短段
  4. ★ 掐头留尾: 只裁掉平台前段过渡区（head_trim_ratio），保留尾部最稳段

输入:
  - 连续 Δλ 序列（或多列 DataFrame）
  - 检测参数（rolling_window, std_percentile, min_plateau_samples, head_trim_ratio）
  - 代理信号列（默认取所选温度栅的均值）

输出: 平台表 DataFrame（idx_start, idx_end, mid, proxy, 各列均值）
"""

from __future__ import annotations

from typing import Optional

import numpy as np
import pandas as pd


def filter_plateaus_by_adjacent_jumps(
    df: pd.DataFrame,
    source_col: str,
    plateaus: pd.DataFrame,
    *,
    jump_threshold_nm: Optional[float],
    min_jump_count: int = 10,
    delta_suffix: str = "_d",
) -> tuple[pd.DataFrame, list[int], list[int]]:
    """Keep only plateaus that are locally free of repeated discontinuities.

    A temperature trace naturally changes rapidly between plateaus, so a
    whole-file adjacent-jump count is not a valid reason to discard a grating.
    This helper evaluates each *trimmed plateau* independently.  It returns
    ``(usable_plateaus, excluded_positions, jump_counts)``; the positions are
    relative to the supplied plateau table and are suitable for audit output.
    """
    if plateaus.empty or jump_threshold_nm is None or jump_threshold_nm <= 0.0:
        return plateaus.copy(), [], [0] * len(plateaus)

    value_col = source_col
    scale_to_nm = 1.0
    if value_col not in df.columns:
        value_col = f"{source_col}{delta_suffix}"
        scale_to_nm = 1.0 / 1000.0
    if value_col not in df.columns:
        return plateaus.copy(), [], [0] * len(plateaus)

    source_values = pd.Series(df[value_col])
    values = np.asarray(
        pd.to_numeric(source_values, errors="coerce"), dtype=np.float64,
    )
    jumps = np.abs(np.diff(values) * scale_to_nm) > float(jump_threshold_nm)
    counts: list[int] = []
    excluded: list[int] = []
    required_count = max(1, int(min_jump_count))
    for position, row in enumerate(plateaus.itertuples(index=False)):
        start = int(getattr(row, "idx_start"))
        end = int(getattr(row, "idx_end"))
        count = int(np.count_nonzero(jumps[start:max(start, end - 1)]))
        counts.append(count)
        if count >= required_count:
            excluded.append(position)

    excluded_set = set(excluded)
    usable = plateaus.iloc[[
        position for position in range(len(plateaus)) if position not in excluded_set
    ]].copy()
    return usable, excluded, counts


def detect_plateaus(
    df: pd.DataFrame,
    wavelength_cols: list[str],
    *,
    rolling_window: int = 25,
    std_percentile: float = 45.0,
    min_plateau_samples: int = 180,
    head_trim_ratio: float = 0.70,
    proxy_cols: Optional[list[str]] = None,
    delta_suffix: str = "_d",
    quality_jump_threshold_nm: Optional[float] = None,
    min_serial_jump_count: int = 10,
) -> pd.DataFrame:
    """从连续波长数据中检测温度阶梯平台段。

    算法 (照搬 FBG.py):
      1. 用温度栅均值作为代理信号
      2. 计算代理信号的滚动标准差
      3. 取 std 的低百分位数作为"稳定"阈值
      4. 连续稳定段分组，过滤过短段
      5. ★ 掐头留尾: 只裁掉平台前段过渡区，保留尾部最稳段求均值
      6. 每段输出各列均值与中点索引

    Args:
        df: 含波长列与/或 dL 列的 DataFrame
        wavelength_cols: 光栅波长列名列表，如 ['A1-W1', 'A1-W2']
        rolling_window: 滚动标准差窗口 (样本数)
        std_percentile: 稳定阈值百分位数 (越小越严格)
        min_plateau_samples: 最短平台长度 (样本数)
        head_trim_ratio: 掐头比例 (0~1)，裁掉前段过渡区，保留尾部
            默认 0.70 = 裁掉前 70%，只对最稳的后 30% 求均值
        proxy_cols: 代理信号列名，默认取需检测列的均值
        delta_suffix: dL 列后缀，如 '_d' → 'A1-W1_d'

    Returns:
        DataFrame，每行为一个平台，含:
          - idx_start, idx_end: 起止索引
          - mid: 中点索引
          - proxy: 代理信号均值
          - {col}{delta_suffix}: 各光栅在平台内的均值 dL
    """
    proxy_cols = proxy_cols or wavelength_cols
    proxy_names = [f"{c}{delta_suffix}" for c in proxy_cols]

    # ── 代理信号 ──
    proxy_series = None
    for name in proxy_names:
        if name in df.columns:
            col_data = df[name].values.astype(np.float64)
            if proxy_series is None:
                proxy_series = col_data.copy()
            else:
                proxy_series += col_data
    if proxy_series is None:
        raise ValueError(
            f"代理信号列不存在: {proxy_names}。"
            f"可用列: {list(df.columns)}"
        )
    proxy = proxy_series / len(proxy_names)

    # dL is stored in pm whereas the user-facing serial-data threshold is nm.
    # A rapid but monotonic temperature transition can exceed the threshold;
    # it must not invalidate a whole grating.  Only repeated back-and-forth
    # jumps are global serial evidence.  Individual plateau quality is checked
    # later by ``filter_plateaus_by_adjacent_jumps`` before its regression.
    proxy_columns_used = [
        source_col for source_col, name in zip(proxy_cols, proxy_names)
        if name in df.columns
    ]
    proxy_columns_rejected: dict[str, int] = {}
    adjacent_jump_counts: dict[str, int] = {}
    serial_reversal_counts: dict[str, int] = {}
    if quality_jump_threshold_nm is not None and quality_jump_threshold_nm > 0.0:
        jump_limit_pm = float(quality_jump_threshold_nm) * 1000.0
        retained: list[np.ndarray] = []
        retained_names: list[str] = []
        for source_col, name in zip(proxy_cols, proxy_names):
            if name not in df.columns:
                continue
            values = df[name].values.astype(np.float64)
            diff = np.diff(values)
            jumps = np.isfinite(diff) & (np.abs(diff) > jump_limit_pm)
            adjacent_jump_counts[source_col] = int(np.count_nonzero(jumps))
            reversals = jumps[:-1] & jumps[1:] & (diff[:-1] * diff[1:] < 0.0)
            reversal_count = int(np.count_nonzero(reversals))
            serial_reversal_counts[source_col] = reversal_count
            # Alternating back-and-forth jumps are not a normal thermal
            # trajectory.  Keep monotonic transitions in the median proxy.
            if reversal_count >= max(1, int(min_serial_jump_count)):
                proxy_columns_rejected[source_col] = reversal_count
                continue
            retained.append(values)
            retained_names.append(source_col)
        if retained:
            proxy = np.nanmedian(np.vstack(retained), axis=0)
            proxy_columns_used = retained_names
        else:
            proxy_columns_used = []

    def _set_proxy_metadata(result: pd.DataFrame) -> pd.DataFrame:
        result.attrs["proxy_columns_used"] = list(proxy_columns_used)
        result.attrs["proxy_columns_rejected"] = dict(proxy_columns_rejected)
        result.attrs["quality_jump_threshold_nm"] = quality_jump_threshold_nm
        result.attrs["source_adjacent_jump_counts"] = dict(adjacent_jump_counts)
        result.attrs["source_serial_reversal_counts"] = dict(serial_reversal_counts)
        return result

    if not proxy_columns_used:
        return _set_proxy_metadata(pd.DataFrame(columns=[
            "idx_start", "idx_end", "mid", "proxy",
        ] + [f"{c}{delta_suffix}" for c in wavelength_cols]))

    # ── 滚动标准差 ──
    rstd = (
        pd.Series(proxy)
        .rolling(rolling_window, center=True)
        .std()
        .bfill()
        .ffill()
        .values
    )

    # ── 稳定阈值 ──
    valid_std = rstd[~np.isnan(rstd)]
    if len(valid_std) == 0:
        return _set_proxy_metadata(pd.DataFrame(columns=[
            "idx_start", "idx_end", "mid", "proxy",
        ] + [f"{c}{delta_suffix}" for c in wavelength_cols]))
    thr = np.percentile(valid_std, std_percentile)
    stable = rstd < thr

    # ── 分组 ──
    segments: list[tuple[int, int]] = []
    i, n = 0, len(stable)
    while i < n:
        if stable[i]:
            j = i
            while j < n and stable[j]:
                j += 1
            if j - i >= min_plateau_samples:
                head = int((j - i) * head_trim_ratio)
                segments.append((i + head, j))
            i = j
        else:
            i += 1

    # ── 构建平台表 ──
    dL_cols = [f"{c}{delta_suffix}" for c in wavelength_cols]
    rows = []
    for a, b in segments:
        d: dict[str, float | int] = {
            "idx_start": a, "idx_end": b, "mid": (a + b) // 2,
        }
        d["proxy"] = float(np.mean(proxy[a:b]))
        for dc in dL_cols:
            if dc in df.columns:
                d[dc] = float(np.nanmean(df[dc].values[a:b].astype(np.float64)))
            else:
                d[dc] = float("nan")
        rows.append(d)

    if not rows:
        return _set_proxy_metadata(pd.DataFrame(
            columns=["idx_start", "idx_end", "mid", "proxy"] + dL_cols
        ))

    P = _set_proxy_metadata(pd.DataFrame(rows))
    print(f"  检测到稳定平台段: {len(P)} 个")
    return P
