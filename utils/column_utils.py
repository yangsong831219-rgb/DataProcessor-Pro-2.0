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
