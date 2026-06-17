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
_QUOTED_RE = re.compile(r"^[\'\"''""](.*?)[\'\"''""]$")


# ==========================================================================
# 波长列判定
# ==========================================================================


def is_wave_col(df: pd.DataFrame, col_name: str) -> bool:
    """值落在 FBG 波长区间 [1400, 1700] -> 波长列。

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


# ==========================================================================
# 暗号行构建
# ==========================================================================


def build_annotation_row(df: pd.DataFrame, *, file_format: str = "") -> list[str]:
    """按 df.columns 逐列构建暗号行文本列表（已带引号）。

    规则:
      - Timestamp 列 -> '时间戳'
      - 波长列（is_wave_col 判定）-> 'wN-类型-位置'（N 仅在波长列上递增）
      - 其它列（公式列等）-> 空字符串

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


# ==========================================================================
# 暗号行扫描与列角色解析
# ==========================================================================


def extract_annotation_info(
    df: pd.DataFrame,
) -> tuple[int | None, dict[int, str], int | None]:
    """扫描 DataFrame 寻找暗号行，识别列角色。

    Returns:
        (time_col_idx, data_cols, signal_row_idx)
        - time_col_idx:   时间戳列的整数索引，None 表示未找到
        - data_cols:      {列索引: 自定义列名} 字典
        - signal_row_idx: 暗号行在 DataFrame 中的行号，None 表示未找到
    """
    if df is None or df.empty:
        return None, {}, None

    # -- 1. 定位暗号行 --
    signal_row_idx: int | None = None
    for idx in range(len(df)):
        for val in df.iloc[idx]:
            s = str(val).strip().strip("'\"''\"\"")
            if "时间戳" in s:
                signal_row_idx = idx
                break
        if signal_row_idx is not None:
            break

    if signal_row_idx is None:
        return None, {}, None

    # -- 2. 解析暗号行的列含义 --
    signal_row = df.iloc[signal_row_idx]
    time_col_idx: int | None = None
    data_cols: dict[int, str] = {}

    for j in range(len(df.columns)):
        col_name = df.columns[j]
        val = str(signal_row[col_name]).strip()
        cleaned = val.strip("'\"''\"\"").strip()

        if "时间戳" in cleaned:
            time_col_idx = j
        elif _QUOTED_RE.match(val):
            m = _QUOTED_RE.match(val)
            if m:
                custom_name = m.group(1).strip()
                data_cols[j] = custom_name

    return time_col_idx, data_cols, signal_row_idx


# ==========================================================================
# 分析数据提取（跳过暗号行 + 数值恢复 + 列名清洗）
# ==========================================================================


def get_analysis_data(
    df: pd.DataFrame,
) -> tuple[pd.DataFrame, str, list[str]]:
    """从含暗号行的 DataFrame 中提取清洗后的分析用数据。

    Args:
        df: 当前数据 DataFrame（可能含暗号行）

    Returns:
        (cleaned_df, time_col_name, annotated_cols)
    """
    from utils.column_utils import strip_bracket_units

    time_col_idx, data_cols, signal_row_idx = extract_annotation_info(df)

    if signal_row_idx is None or df is None:
        if df is not None:
            df = df.copy()
            df.columns = [strip_bracket_units(str(c)) for c in df.columns]
        return df, "时间", []

    # 跳过暗号行及之前的所有行
    data_df = df.iloc[signal_row_idx + 1 :].copy()
    data_df.reset_index(drop=True, inplace=True)

    # 恢复数值列 dtype（暗号行可能已将列污染为 object）
    for col in data_df.columns:
        if not pd.api.types.is_numeric_dtype(data_df[col]):
            converted = pd.to_numeric(data_df[col], errors="coerce")
            before = data_df[col].notna().sum()
            after = converted.notna().sum()
            if before > 0 and after / before >= 0.8:
                data_df[col] = converted

    # 确定时间列名
    if time_col_idx is not None:
        time_col_name = str(df.columns[time_col_idx])
    else:
        time_col_name = "时间"
        if time_col_name not in data_df.columns and len(data_df.columns) > 0:
            time_col_name = str(data_df.columns[0])

    # 重命名数据列
    rename_map: dict[str, str] = {}
    if data_cols:
        for col_idx, custom_name in data_cols.items():
            if col_idx < len(df.columns):
                rename_map[str(df.columns[col_idx])] = custom_name
    if time_col_idx is not None:
        orig_time_name = str(df.columns[time_col_idx])
        if orig_time_name not in rename_map:
            rename_map[orig_time_name] = "时间"
            time_col_name = "时间"
        else:
            time_col_name = rename_map[orig_time_name]
    if rename_map:
        data_df = data_df.rename(columns=rename_map)

    annotated_cols = list(data_cols.values()) if data_cols else []

    # 卸妆
    data_df.columns = [strip_bracket_units(str(c)) for c in data_df.columns]
    time_col_name = strip_bracket_units(time_col_name)
    annotated_cols = [strip_bracket_units(c) for c in annotated_cols]

    return data_df, time_col_name, annotated_cols


def build_sensor_results_dict(
    analysis_df: pd.DataFrame, annotated_cols: list[str],
) -> dict[str, list]:
    """将标注数据列转为 sensor_results 格式 {列名: [值列表]}。

    纯函数 -- 不写 UI，只返回 dict。
    调用方自行将 dict 传给 analysis_tab_widget.set_sensor_results()。
    """
    sensor_results: dict[str, list] = {}
    for col in annotated_cols:
        if col in analysis_df.columns and pd.api.types.is_numeric_dtype(analysis_df[col]):
            sensor_results[col] = analysis_df[col].tolist()
    return sensor_results


# ==========================================================================
# 空白行插入（纯 DataFrame 操作，不依赖任何外部状态）
# ==========================================================================


def insert_blank_row(df: pd.DataFrame) -> tuple[pd.DataFrame, dict[str, Any]]:
    """在 DataFrame 首行插入一行全 NaN 的空白暗号行。

    使用 pd.concat 安全写法（符合 CLAUDE.md 规则 1：Pandas 2.0+ 类型安全）。

    Returns:
        (new_df, orig_dtypes):
          - new_df: 首行插入空行的新 DataFrame
          - orig_dtypes: 原始列的 dtype 映射，供 apply_annotation_row 恢复类型用
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
    """
    for col_idx, text in enumerate(ann):
        if not text:
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
