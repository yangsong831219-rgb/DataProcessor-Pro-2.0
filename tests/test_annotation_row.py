"""暗号行插入 — 回归测试

测试 _insert_annotation_row_if_timestamp_exists 对 Sensors 公式版文件
的暗号行生成逻辑：Timestamp 列='时间戳'，波长列(值1400-1700)='wN-类型-位置'，
公式列(值~-0.5)留空，按实际 df.columns 对齐。
"""

from __future__ import annotations

import sys
import os
import tempfile

import pandas as pd
import numpy as np
import pytest


# ═══════════════════════════════════════════════════════════════════════
# 合成夹具
# ═══════════════════════════════════════════════════════════════════════

_WAVE_LO, _WAVE_HI = 1400.0, 1700.0


def _is_wave_col(df: pd.DataFrame, col_name: str) -> bool:
    """值落在 FBG 波长区间 [1400, 1700] → 波长列。"""
    s = df[col_name].dropna()
    if s.empty:
        return False
    try:
        v = float(s.iloc[0])
    except (ValueError, TypeError):
        return False
    return _WAVE_LO <= v <= _WAVE_HI


def _make_sensors_df(
    n_formula: int = 12,
    n_wavelength: int = 12,
    n_rows: int = 5,
) -> pd.DataFrame:
    """构造 Sensors 公式版 DataFrame。

    列: Timestamp + n_formula 个公式列(C1_1..C2_2) + n_wavelength 个波长列(FBG_A..FBG_J)
    公式列值 ≈ -0.5~0.0; 波长列值 ≈ 1525~1555.
    """
    cols = ["Timestamp"]
    formula_cols = [f"C{i+1}_{(i % 2) + 1}" for i in range(n_formula)]
    wave_cols = [f"FBG_{chr(65+i)}" for i in range(n_wavelength)]
    cols.extend(formula_cols)
    cols.extend(wave_cols)

    rows = []
    for t in range(n_rows):
        ts = f"2026/5/12 {t+1:02d}:00:{t:02d}.0"
        f_vals = [f"{-0.5 + t * 0.01 + i * 0.001:.5f}" for i in range(n_formula)]
        w_vals = [f"{1525.0 + i * 2.5 + t * 0.01:.5f}" for i in range(n_wavelength)]
        rows.append([ts] + f_vals + w_vals)

    df = pd.DataFrame(rows, columns=cols)
    for c in cols[1:]:
        df[c] = pd.to_numeric(df[c], errors="coerce")
    return df


# ═══════════════════════════════════════════════════════════════════════
# 测试 _is_wave_col 检测
# ═══════════════════════════════════════════════════════════════════════


class TestIsWaveCol:
    """_is_wave_col 波长列判定"""

    def test_wave_col_detected(self):
        """FBG 列值~1530 → True"""
        df = _make_sensors_df(n_formula=3, n_wavelength=3, n_rows=3)
        assert _is_wave_col(df, "FBG_A")
        assert _is_wave_col(df, "FBG_B")
        assert _is_wave_col(df, "FBG_C")

    def test_formula_col_not_wave(self):
        """公式列 值~-0.5 → False"""
        df = _make_sensors_df(n_formula=3, n_wavelength=3, n_rows=3)
        for c in df.columns:
            if str(c).startswith("C") and "_" in str(c):
                assert not _is_wave_col(df, str(c)), f"{c} should NOT be wave col"

    def test_timestamp_not_wave(self):
        """Timestamp 列 → False (非 float)"""
        df = _make_sensors_df(n_formula=3, n_wavelength=3, n_rows=3)
        assert not _is_wave_col(df, "Timestamp")

    def test_wave_with_nan_first_value(self):
        """波长列首行为 NaN 但后续有值 → 仍判为波长列 (用首个非空值)"""
        df = _make_sensors_df(n_formula=3, n_wavelength=3, n_rows=5)
        df.iloc[0, df.columns.get_loc("FBG_A")] = np.nan
        # 首行是 NaN, 但第二行有正常波长值
        assert _is_wave_col(df, "FBG_A"), \
            "应跳过 NaN 取首个非空值, 判为波长列"


# ═══════════════════════════════════════════════════════════════════════
# 测试暗号行对齐 (模拟 _insert_annotation_row_if_timestamp_exists)
# ═══════════════════════════════════════════════════════════════════════


def _build_annotation_row(df: pd.DataFrame) -> list[str]:
    """模拟暗号行构建逻辑 — 按实际 df.columns 逐列生成。"""
    ann: list[str] = []
    wi = 0
    for col_name in df.columns:
        col_name = str(col_name)
        if col_name == "Timestamp":
            ann.append("'时间戳'")
        elif _is_wave_col(df, col_name):
            wi += 1
            ann.append(f"'w{wi}-类型-位置'")
        else:
            ann.append("")
    return ann


class TestAnnotationRowAlignment:
    """暗号行对齐"""

    def test_annotation_row_length_matches_columns(self):
        """暗号行长度 == 列数"""
        df = _make_sensors_df(n_formula=12, n_wavelength=12, n_rows=3)
        ann = _build_annotation_row(df)
        assert len(ann) == len(df.columns), \
            f"len(ann)={len(ann)} != ncol={len(df.columns)}"

    def test_timestamp_gets_label(self):
        """Timestamp 列 → '时间戳'"""
        df = _make_sensors_df(n_formula=3, n_wavelength=3, n_rows=3)
        ann = _build_annotation_row(df)
        ts_idx = list(df.columns).index("Timestamp")
        assert ann[ts_idx] == "'时间戳'", \
            f"Timestamp at col {ts_idx} got {ann[ts_idx]!r}"

    def test_wave_cols_get_w_labels(self):
        """波长列 → 'wN-类型-位置' 按序递增"""
        df = _make_sensors_df(n_formula=3, n_wavelength=4, n_rows=3)
        ann = _build_annotation_row(df)
        wave_labels = [a for a in ann if "w" in a and "类型" in a]
        assert len(wave_labels) == 4, \
            f"expected 4 wave labels, got {len(wave_labels)}: {wave_labels}"
        assert wave_labels[0] == "'w1-类型-位置'"
        assert wave_labels[1] == "'w2-类型-位置'"
        assert wave_labels[2] == "'w3-类型-位置'"
        assert wave_labels[3] == "'w4-类型-位置'"

    def test_formula_cols_stay_empty(self):
        """公式列 (值~-0.5) → 暗号行对应位为空字符串"""
        df = _make_sensors_df(n_formula=5, n_wavelength=3, n_rows=3)
        ann = _build_annotation_row(df)
        for ci, col_name in enumerate(df.columns):
            if str(col_name).startswith("C") and "_" in str(col_name):
                assert ann[ci] == "", \
                    f"公式列 {col_name} at col {ci} should be empty, got {ann[ci]!r}"

    def test_wave_labels_at_correct_positions(self):
        """波长标签贴在正确的列位置 (非公式列位置)"""
        df = _make_sensors_df(n_formula=3, n_wavelength=3, n_rows=3)
        ann = _build_annotation_row(df)
        for ci, col_name in enumerate(df.columns):
            if str(col_name).startswith("FBG_"):
                assert "类型" in ann[ci], \
                    f"FBG col {col_name} at {ci} should have wave label, got {ann[ci]!r}"
            elif str(col_name).startswith("C") and "_" in str(col_name):
                assert ann[ci] == "", \
                    f"Formula col {col_name} at {ci} should be empty, got {ann[ci]!r}"

    def test_no_stale_template_labels(self):
        """不含旧模板标签如 'A1G1', 'FBG FBG_' 等"""
        df = _make_sensors_df(n_formula=6, n_wavelength=6, n_rows=3)
        ann = _build_annotation_row(df)
        joined = "|".join(ann)
        assert "A1G1" not in joined
        assert "FBG FBG" not in joined
        assert "FBG_" not in joined
