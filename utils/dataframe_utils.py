"""dataframe_utils.py — DataFrame 行定位、范围查询等纯函数

零 Qt 依赖，零全局状态。
"""

from __future__ import annotations

import pandas as pd


def find_first_timestamp_row(df: pd.DataFrame) -> int:
    """找到第一个包含有效时间戳的数据行索引。

    扫描 df 列名中包含 '时间'/'time'/'timestamp' 的列，
    返回该列第一个非空有效值的行号。无匹配返回 0。

    Args:
        df: 数据 DataFrame

    Returns:
        首个有效时间戳的 0-based 行索引（未找到返回 0）
    """
    time_col = None
    for col in df.columns:
        col_lower = str(col).lower()
        if "时间" in col_lower or "time" in col_lower or "timestamp" in col_lower:
            time_col = col
            break

    if time_col is None:
        return 0  # 没有时间列，从第一行开始

    for idx in range(len(df)):
        val = df.iloc[idx][time_col]
        if pd.notna(val) and str(val).strip() != "":
            try:
                pd.to_numeric(val)
                return idx
            except (ValueError, TypeError):
                # 可能是时间字符串，也算有效
                return idx
    return 0
