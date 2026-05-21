from typing import Literal
from dataclasses import dataclass
import pandas as pd
import numpy as np

@dataclass
class CleaningRule:
    name: str
    rule_type: Literal['range', 'negative', 'zero', 'format']
    enabled: bool = True
    min_value: float = None
    max_value: float = None
    fill_method: Literal['linear', 'mean', 'forward', 'custom'] = 'linear'
    fill_value: float = None

def detect_anomalies(df: pd.DataFrame, rules: list[CleaningRule]) -> pd.DataFrame:
    """检测异常值"""
    result = df.copy()
    for col in result.columns:
        if pd.api.types.is_numeric_dtype(result[col]):
            for rule in rules:
                if not rule.enabled:
                    continue
                if rule.rule_type == 'range':
                    mask = (result[col] < rule.min_value) | (result[col] > rule.max_value)
                    result.loc[mask, f'{col}_anomaly'] = True
                elif rule.rule_type == 'negative':
                    result.loc[result[col] < 0, f'{col}_anomaly'] = True
                elif rule.rule_type == 'zero':
                    result.loc[result[col] == 0, f'{col}_anomaly'] = True
    return result

def fill_missing(df: pd.DataFrame, rules: list[CleaningRule]) -> pd.DataFrame:
    """填充缺失值"""
    result = df.copy()
    for col in result.columns:
        if pd.api.types.is_numeric_dtype(result[col]):
            for rule in rules:
                if not rule.enabled or rule.rule_type != 'range':
                    continue
                if rule.fill_method == 'linear':
                    result[col] = result[col].interpolate()
                elif rule.fill_method == 'mean':
                    result[col] = result[col].fillna(result[col].mean())
                elif rule.fill_method == 'forward':
                    result[col] = result[col].ffill()
                elif rule.fill_method == 'custom':
                    result[col] = result[col].fillna(rule.fill_value)
    return result

def clean_data(df: pd.DataFrame, rules: list[CleaningRule]) -> pd.DataFrame:
    """完整清洗流程"""
    df = detect_anomalies(df, rules)
    df = fill_missing(df, rules)
    return df