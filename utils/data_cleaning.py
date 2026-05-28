"""数据清洗 — 异常值检测、缺失值填充、完整清洗流程

从 main.py 抽离的纯函数，无 UI 依赖。
"""

from __future__ import annotations

import re
from typing import List, Optional

import pandas as pd


def _filter_columns(df: pd.DataFrame, pattern: Optional[str]) -> list:
    """返回需要清洗的列名列表。若 pattern 非空，仅返回匹配正则的列。"""
    numeric_cols = [c for c in df.columns if pd.api.types.is_numeric_dtype(df[c])]
    if not pattern:
        return numeric_cols
    compiled = re.compile(pattern)
    return [c for c in numeric_cols if compiled.search(str(c))]


def detect_anomalies(df: pd.DataFrame, rules: list,
                     column_pattern: Optional[str] = None) -> pd.DataFrame:
    """检测异常值，为每列添加 _anomaly 标记列。

    Args:
        column_pattern: 可选正则，仅对列名匹配的数值列执行检测。
    """
    result = df.copy()
    target_cols = _filter_columns(result, column_pattern)

    for col in result.columns:
        result[f'{col}_anomaly'] = False

    for col in target_cols:
        for rule in rules:
            if not rule.enabled:
                continue

            if rule.rule_type == 'adjacent_diff' and rule.threshold is not None:
                diff = result[col].diff().abs()
                mask = diff > rule.threshold
                result.loc[mask, f'{col}_anomaly'] = True

            elif rule.rule_type == 'nan':
                mask = result[col].isna()
                result.loc[mask, f'{col}_anomaly'] = True

            elif rule.rule_type == 'range':
                if rule.min_value is not None and rule.max_value is not None:
                    mask = (result[col] < rule.min_value) | (result[col] > rule.max_value)
                    result.loc[mask, f'{col}_anomaly'] = True

            elif rule.rule_type == 'negative':
                mask = result[col] < 0
                result.loc[mask, f'{col}_anomaly'] = True

            elif rule.rule_type == 'zero':
                mask = result[col] == 0
                result.loc[mask, f'{col}_anomaly'] = True

    return result


def fill_missing(df: pd.DataFrame, rules: list,
                 column_pattern: Optional[str] = None) -> pd.DataFrame:
    """填充缺失值（基于规则）。

    Args:
        column_pattern: 可选正则，仅对列名匹配的数值列执行填充。
    """
    result = df.copy()
    target_cols = _filter_columns(result, column_pattern)

    for col in target_cols:
        fill_method = 'linear'
        fill_value = None

        for rule in rules:
            if rule.enabled and rule.rule_type == 'adjacent_diff':
                fill_method = rule.fill_method
                fill_value = rule.fill_value
                break

        if fill_method == 'linear':
            result[col] = result[col].interpolate()
        elif fill_method == 'mean':
            result[col] = result[col].fillna(result[col].mean())
        elif fill_method == 'forward':
            result[col] = result[col].ffill()
        elif fill_method == 'backward':
            result[col] = result[col].bfill()
        elif fill_method == 'custom' and fill_value is not None:
            result[col] = result[col].fillna(fill_value)

    return result


def clean_data(df: pd.DataFrame, rules: list,
               column_pattern: Optional[str] = None) -> pd.DataFrame:
    """完整清洗流程：异常检测 → 缺失值填充。

    Args:
        column_pattern: 可选正则，仅对列名匹配的数值列执行清洗。
    """
    df = detect_anomalies(df, rules, column_pattern)
    df = fill_missing(df, rules, column_pattern)
    return df
