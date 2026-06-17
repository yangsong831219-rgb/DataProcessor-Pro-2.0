"""annotation_utils.py — 暗号行构建、波长列判定、分析数据提取

纯函数模块，零 Qt 依赖。所有函数以显式参数传入 DataFrame/配置，
不读全局状态。供 main.py 暗号插入链和 analysis_tab 调用。
"""

from __future__ import annotations

import re
from typing import Any

import pandas as pd
import numpy as np

_WAVE_LO, _WAVE_HI = 1400.0, 1700.0


# ═══════════════════════════════════════════════════════════════════════
# 波长列判定
# ═══════════════════════════════════════════════════════════════════════


def is_wave_col(df: pd.DataFrame, col_name: str) -> bool:
    """值落在 FBG 波长区间 [1400, 1700] → 波长列。

    用该列首个非空 float 值判决；空列或非数值列返回 False。
    """
    s = df[col_name].dropna()
    if s.empty:
        return False
    try:
        v = float(s.iloc[0])  # pyright: ignore[reportArgumentType]  # deliberate: runtime guard against non-str
    except (ValueError, TypeError):
        return False
    return _WAVE_LO <= v <= _WAVE_HI


# ═══════════════════════════════════════════════════════════════════════
# 暗号行构建
# ═══════════════════════════════════════════════════════════════════════


def build_annotation_row(df: pd.DataFrame, *, file_format: str = "") -> list[str]:
    """按 df.columns 逐列构建暗号行文本列表（已带引号）。

    规则：
      - Timestamp 列 → '时间戳'
      - 波长列（is_wave_col 判定）→ 'wN-类型-位置'（N 仅在波长列上递增）
      - 其它列（公式列等）→ 空字符串

    Args:
        df: 数据 DataFrame（应已在暗号行插入之前，从 data_row 行开始）
        file_format: 模板文件格式；非 enlight/fiber_custom 时返回全空列表

    Returns:
        与 df.columns 等长的字符串 list，已带单引号包裹
    """
    if file_format not in ("enlight", "fiber_custom"):
        return [""] * len(df.columns)

    ann: list[str] = []
    wi = 0
    for col_name in df.columns:
        col_name = str(col_name)
        if col_name == "Timestamp":
            ann.append("'时间戳'")
        elif is_wave_col(df, col_name):
            wi += 1
            ann.append(f"'w{wi}-类型-位置'")
        else:
            ann.append("")
    return ann


# ═══════════════════════════════════════════════════════════════════════
# 空白行插入（纯 DataFrame 操作，不依赖任何外部状态）
# ═══════════════════════════════════════════════════════════════════════


def insert_blank_row(df: pd.DataFrame) -> tuple[pd.DataFrame, dict[str, Any]]:
    """在 DataFrame 首行插入一行全 NaN 的空白暗号行。

    使用 pd.concat 安全写法（符合 CLAUDE.md 规则 1：Pandas 2.0+ 类型安全）。

    Args:
        df: 原始数据 DataFrame

    Returns:
        (new_df, orig_dtypes):
          - new_df: 首行插入空行的新 DataFrame（列全部已提升为 object 以容纳暗号文本）
          - orig_dtypes: 原始列的 dtype 映射 {col_name: dtype}，供 apply_annotation_row 恢复类型用
    """
    orig_dtypes = df.dtypes.to_dict()
    blank_vals = [np.nan] * len(df.columns)
    blank_row = pd.DataFrame([blank_vals], columns=df.columns)
    new_df = pd.concat([blank_row, df], ignore_index=True)
    for col, dtype in orig_dtypes.items():
        try:
            new_df[col] = new_df[col].astype(dtype)
        except (ValueError, TypeError):
            if "int" in str(dtype):
                new_df[col] = new_df[col].astype("float64")
    return new_df, orig_dtypes


def apply_annotation_row(
    df: pd.DataFrame,
    ann: list[str],
    row_idx: int,
    orig_dtypes: dict[str, Any] | None = None,
) -> pd.DataFrame:
    """将暗号文本列表写入 DataFrame 的指定行。

    对每列先行 astype(object) 再写值，确保暗号文字不被 numeric dtype 吞掉。
    若提供 orig_dtypes，写完后尝试恢复各列 dtype。

    Args:
        df:         已插入空白行的 DataFrame
        ann:        暗号文本列表（与 df.columns 等长），不含引号的值可为空串
        row_idx:    暗号行在 DataFrame 中的行号（通常为 0）
        orig_dtypes: 原始 dtype 映射，用于写后恢复

    Returns:
        修改后的 df（in-place 修改 + 返回同一引用方便链式调用）
    """
    for col_idx, text in enumerate(ann):
        if not text:
            # 空串 → 确保该列兼容 object 即可，不写值
            col_name = str(df.columns[col_idx])
            if pd.api.types.is_numeric_dtype(df[col_name]):
                df[col_name] = df[col_name].astype(object)
            continue
        col_name = str(df.columns[col_idx])
        df[col_name] = df[col_name].astype(object)
        df.iloc[row_idx, col_idx] = text

    if orig_dtypes:
        for col, dtype in orig_dtypes.items():
            try:
                df[col] = df[col].astype(dtype)
            except (ValueError, TypeError):
                if "int" in str(dtype):
                    df[col] = df[col].astype("float64")
    return df
