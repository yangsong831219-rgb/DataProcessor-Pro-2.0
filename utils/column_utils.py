"""column_utils.py — 列名清洗、行分类、FBG 列检测

纯函数模块，零 Qt 依赖。所有函数以显式参数传入数据，
不读全局状态。
"""

from __future__ import annotations

import re
from typing import Any

import pandas as pd


# ═══════════════════════════════════════════════════════════════════════
# 卸妆 — 剥离列名/键中的括号与单位后缀
# ═══════════════════════════════════════════════════════════════════════

_BRACKET_RE = re.compile(r"[\(（].*?[\)）]")


def strip_bracket_units(key: str) -> str:
    """剥离字符串中的中英文括号及括号内单位。

    例: "A1_应变(με)" → "A1_应变"
        "波长(nm)"     → "波长"
    """
    return _BRACKET_RE.sub("", str(key)).strip()


def clean_dict_keys(results: dict[str, Any]) -> dict[str, Any]:
    """对字典所有键执行 strip_bracket_units。

    原始 _clean_sensor_keys 的等价纯函数版本。
    """
    return {strip_bracket_units(str(k)): v for k, v in results.items()}


# ═══════════════════════════════════════════════════════════════════════
# 行分类 — 判断是数据行还是表头行
# ═══════════════════════════════════════════════════════════════════════


def is_data_row(parts: list[str]) -> bool:
    """判断一行 split 后的字段列表是数据行还是表头行。

    若 >=50% 的字段可转为 float、日期格式、或字母/中文关键字开头，
    则判为数据行。用于通用文件格式的自动检测。

    Args:
        parts: 一行按分隔符 split 后的字符串列表

    Returns:
        True = 数据行, False = 表头行
    """
    import re as _re2

    data_count = 0
    header_count = 0
    for p in parts:
        p = p.strip()
        if not p:
            continue
        try:
            float(p)
            data_count += 1
            continue
        except ValueError:
            pass
        if _re2.match(r"^\d{2,4}[/-]\d{1,2}[/-]\d{1,2}", p):
            data_count += 1
            continue
        if _re2.match(r"^[A-Za-z_]", p) or any(
            kw in p for kw in ["波长", "时间", "温度", "应变"]
        ):
            header_count += 1
            continue
        data_count += 1

    total = data_count + header_count
    if total > 0:
        return data_count / total >= 0.5
    return True


# ═══════════════════════════════════════════════════════════════════════
# FBG 列检测 — 从 DataFrame 列名自动识别 FBG 波长列
# ═══════════════════════════════════════════════════════════════════════

_W_PATTERN = re.compile(r"^[wW](\d+)(?:-.*)?$")


def detect_fbg_columns(df: pd.DataFrame) -> list[str]:
    """从 DataFrame 列名自动识别 FBG 波长列。

    优先级：
      1. 匹配 wN / WN（暗号标注后的列名）
      2. 列名含 '波长' / 'wavelength'
      3. 列名以 FBG_ 开头
      4. 列名以 W 开头 + 后续为数字

    Args:
        df: 数据 DataFrame（通常为加载后、暗号行插入前）

    Returns:
        FBG 波长列的列名 list（按 w 序号排序），无匹配时返回 []
    """
    if df is None or df.empty:
        return []

    # 1) wN 模式
    w_matches = sorted(
        [
            (int(m.group(1)), c)
            for c in df.columns
            for m in [_W_PATTERN.match(str(c))]
            if m
        ],
        key=lambda x: x[0],
    )
    fbg_cols = [c[1] for c in w_matches]

    # 2) 文字 '波长'
    if not fbg_cols:
        fbg_cols = [
            str(c)
            for c in df.columns
            if "波长" in str(c) or "wavelength" in str(c).lower()
        ]

    # 3) FBG_ 前缀
    if not fbg_cols:
        fbg_cols = [
            str(c) for c in df.columns if str(c).upper().startswith("FBG_")
        ]

    # 4) W + 数字
    if not fbg_cols:
        w_digit_cols = [
            str(c)
            for c in df.columns
            if str(c).upper().startswith("W") and str(c)[1:].isdigit()
        ]
        if len(w_digit_cols) >= 2:
            fbg_cols = w_digit_cols

    return fbg_cols


# ═══════════════════════════════════════════════════════════════════════
# 数据列判定 — 哪些列是"可画的数值数据列"
# ═══════════════════════════════════════════════════════════════════════

def is_plottable_data_column(col_name: str, series: pd.Series | None = None) -> bool:
    """判定一列是否是"可画的数值数据列"（排除标记/暗号/异常/非数值列）。

    排除规则（任一命中即排除）：
      - 列名以 _anomaly 结尾
      - 列名 == 'Timestamp' 或含 '时间戳' / '时间'
      - 提供了 series 且 dtype 为非数值 (bool/object/category/string)
      - 提供了 series 且全为 False/NaN (纯标记列)

    Args:
        col_name: 列名 (str)
        series:   可选，该列的 pandas Series（用于 dtype 和内容检测）

    Returns:
        True = 该列是可画的数值数据列
    """
    name = str(col_name)

    # 硬排除：_anomaly 标记列
    if name.endswith('_anomaly'):
        return False

    # 硬排除：时间戳/时间列
    if name == 'Timestamp' or '时间戳' in name or name == '时间':
        return False

    if series is not None:
        # dtype 排除：布尔/非数值类型
        if pd.api.types.is_bool_dtype(series):
            return False
        if not pd.api.types.is_numeric_dtype(series):
            return False

        # 内容排除：纯零值/全NaN/全False 标记列
        try:
            unique_vals = series.dropna().unique()
            if len(unique_vals) <= 1:
                # 唯一值只有 0/False/NaN → 标记列
                only_val = unique_vals[0] if len(unique_vals) == 1 else None
                if only_val is None or only_val == 0 or only_val == 0.0 or only_val is False:
                    return False
        except Exception:
            pass  # dtype 不兼容 unique() → 信任上面 is_numeric_dtype

    return True


def filter_plottable_columns(df: pd.DataFrame) -> list[str]:
    """从 DataFrame 中筛选出所有可画的数据列。

    Args:
        df: 数据 DataFrame

    Returns:
        可画的数值数据列名列表
    """
    if df is None or df.empty:
        return []
    return [
        str(c) for c in df.columns
        if is_plottable_data_column(str(c), df[c])
    ]
