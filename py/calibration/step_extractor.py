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
        return pd.DataFrame(columns=[
            "idx_start", "idx_end", "mid", "proxy",
        ] + [f"{c}{delta_suffix}" for c in wavelength_cols])
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
        d = {"idx_start": a, "idx_end": b, "mid": (a + b) // 2}
        d["proxy"] = float(np.mean(proxy[a:b]))
        for dc in dL_cols:
            if dc in df.columns:
                d[dc] = float(np.nanmean(df[dc].values[a:b].astype(np.float64)))
            else:
                d[dc] = float("nan")
        rows.append(d)

    if not rows:
        return pd.DataFrame(
            columns=["idx_start", "idx_end", "mid", "proxy"] + dL_cols
        )

    P = pd.DataFrame(rows)
    print(f"  检测到稳定平台段: {len(P)} 个")
    return P
