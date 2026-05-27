"""数据清洗 — 异常值检测、缺失值填充、完整清洗流程

从 main.py 抽离的纯函数，无 UI 依赖。
"""

from __future__ import annotations

from typing import List

import pandas as pd


def detect_anomalies(df: pd.DataFrame, rules: list) -> pd.DataFrame:
    """检测异常值，为每列添加 _anomaly 标记列"""
    result = df.copy()

    for col in result.columns:
        result[f'{col}_anomaly'] = False

    for col in result.columns:
        if pd.api.types.is_numeric_dtype(result[col]):
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


def fill_missing(df: pd.DataFrame, rules: list) -> pd.DataFrame:
    """填充缺失值（基于规则）"""
    result = df.copy()
    for col in result.columns:
        if pd.api.types.is_numeric_dtype(result[col]):
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


def clean_data(df: pd.DataFrame, rules: list) -> pd.DataFrame:
    """完整清洗流程：异常检测 → 缺失值填充"""
    df = detect_anomalies(df, rules)
    df = fill_missing(df, rules)
    return df
