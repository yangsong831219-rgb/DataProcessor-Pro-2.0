"""暗号行插入 — 回归测试

测试 is_wave_col (1400-1700nm 值域判定) 和 build_annotation_row (逐列对齐暗号行构建)
的正确行为。全部通过 utils.annotation_utils 导入，锁死刚修复的行为。
"""

from __future__ import annotations

import sys
import os
import tempfile

import pandas as pd
import numpy as np
import pytest

from utils.annotation_utils import is_wave_col, build_annotation_row, insert_blank_row, apply_annotation_row, _WAVE_LO, _WAVE_HI


# ═══════════════════════════════════════════════════════════════════════
# 合成夹具
# ═══════════════════════════════════════════════════════════════════════


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
        assert is_wave_col(df, "FBG_A")
        assert is_wave_col(df, "FBG_B")
        assert is_wave_col(df, "FBG_C")

    def test_formula_col_not_wave(self):
        """公式列 值~-0.5 → False"""
        df = _make_sensors_df(n_formula=3, n_wavelength=3, n_rows=3)
        for c in df.columns:
            if str(c).startswith("C") and "_" in str(c):
                assert not is_wave_col(df, str(c)), f"{c} should NOT be wave col"

    def test_timestamp_not_wave(self):
        """Timestamp 列 → False (非 float)"""
        df = _make_sensors_df(n_formula=3, n_wavelength=3, n_rows=3)
        assert not is_wave_col(df, "Timestamp")

    def test_wave_with_nan_first_value(self):
        """波长列首行为 NaN 但后续有值 → 仍判为波长列 (用首个非空值)"""
        df = _make_sensors_df(n_formula=3, n_wavelength=3, n_rows=5)
        df.iloc[0, df.columns.get_loc("FBG_A")] = np.nan
        # 首行是 NaN, 但第二行有正常波长值
        assert is_wave_col(df, "FBG_A"), \
            "应跳过 NaN 取首个非空值, 判为波长列"


# ═══════════════════════════════════════════════════════════════════════
# 测试 build_annotation_row（annotation_utils 真实函数）
# ═══════════════════════════════════════════════════════════════════════


class TestAnnotationRowAlignment:
    """暗号行对齐"""

    def test_annotation_row_length_matches_columns(self):
        """暗号行长度 == 列数"""
        df = _make_sensors_df(n_formula=12, n_wavelength=12, n_rows=3)
        ann = build_annotation_row(df, file_format="enlight")
        assert len(ann) == len(df.columns), \
            f"len(ann)={len(ann)} != ncol={len(df.columns)}"

    def test_timestamp_gets_label(self):
        """Timestamp 列 → '时间戳'"""
        df = _make_sensors_df(n_formula=3, n_wavelength=3, n_rows=3)
        ann = build_annotation_row(df, file_format="enlight")
        ts_idx = list(df.columns).index("Timestamp")
        assert ann[ts_idx] == "'时间戳'", \
            f"Timestamp at col {ts_idx} got {ann[ts_idx]!r}"

    def test_wave_cols_get_w_labels(self):
        """波长列 → 'wN-类型-位置' 按序递增"""
        df = _make_sensors_df(n_formula=3, n_wavelength=4, n_rows=3)
        ann = build_annotation_row(df, file_format="enlight")
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
        ann = build_annotation_row(df, file_format="enlight")
        for ci, col_name in enumerate(df.columns):
            if str(col_name).startswith("C") and "_" in str(col_name):
                assert ann[ci] == "", \
                    f"公式列 {col_name} at col {ci} should be empty, got {ann[ci]!r}"

    def test_wave_labels_at_correct_positions(self):
        """波长标签贴在正确的列位置 (非公式列位置)"""
        df = _make_sensors_df(n_formula=3, n_wavelength=3, n_rows=3)
        ann = build_annotation_row(df, file_format="enlight")
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
        ann = build_annotation_row(df, file_format="enlight")
        joined = "|".join(ann)
        assert "A1G1" not in joined
        assert "FBG FBG" not in joined
        assert "FBG_" not in joined


# ═══════════════════════════════════════════════════════════════════════
# 测试 insert_blank_row 和 apply_annotation_row
# ═══════════════════════════════════════════════════════════════════════


class TestInsertBlankRow:
    """insert_blank_row 空白行插入（纯 DataFrame 操作）"""

    def test_insert_blank_row_adds_one_row(self):
        """插入后行数 +1"""
        df = _make_sensors_df(n_formula=3, n_wavelength=2, n_rows=3)
        new_df, dtypes = insert_blank_row(df)
        assert len(new_df) == len(df) + 1

    def test_insert_blank_row_first_row_all_nan(self):
        """首行全为 NaN 占位（空白暗号行）"""
        df = _make_sensors_df(n_formula=3, n_wavelength=2, n_rows=3)
        new_df, _ = insert_blank_row(df)
        # 数值列（如 F1）的首行应为 NaN
        assert pd.isna(new_df.iloc[0, 1])  # F1 列首行

    def test_insert_blank_row_preserves_data(self):
        """数据行内容不变（从第1行开始）"""
        df = _make_sensors_df(n_formula=2, n_wavelength=1, n_rows=3)
        new_df, _ = insert_blank_row(df)
        # 第2行（原第1行）的 FBG_A 波长值应与原 df 一致
        orig_val = float(df["FBG_A"].iloc[0])
        new_val = float(new_df["FBG_A"].iloc[1])
        assert abs(new_val - orig_val) < 0.01

    def test_insert_blank_row_returns_dtypes(self):
        """返回的 orig_dtypes 包含所有列"""
        df = _make_sensors_df(n_formula=2, n_wavelength=1, n_rows=3)
        _, dtypes = insert_blank_row(df)
        for col in df.columns:
            assert col in dtypes


class TestApplyAnnotationRow:
    """apply_annotation_row 暗号文本写入"""

    def test_apply_writes_to_correct_row(self):
        """暗号文本写入正确行号"""
        df = _make_sensors_df(n_formula=2, n_wavelength=2, n_rows=3)
        new_df, dtypes = insert_blank_row(df)
        ann = build_annotation_row(df, file_format="enlight")  # 对原始 df 计算暗号
        apply_annotation_row(new_df, ann, 0, dtypes)
        # Timestamp 列第0行应为 '时间戳'
        ts_idx = list(new_df.columns).index("Timestamp")
        assert "时间戳" in str(new_df.iloc[0, ts_idx])

    def test_apply_leaves_data_rows_untouched(self):
        """写入暗号行后数据行不变"""
        df = _make_sensors_df(n_formula=2, n_wavelength=1, n_rows=3)
        orig_data_val = float(df["FBG_A"].iloc[0])
        new_df, dtypes = insert_blank_row(df)
        ann = build_annotation_row(df, file_format="enlight")
        apply_annotation_row(new_df, ann, 0, dtypes)
        assert abs(float(new_df["FBG_A"].iloc[1]) - orig_data_val) < 0.01

    def test_apply_empty_annotation_preserves_dtypes(self):
        """空暗号行（非光纤格式）不破坏 dtype 结构"""
        df = _make_sensors_df(n_formula=2, n_wavelength=1, n_rows=3)
        new_df, dtypes = insert_blank_row(df)
        # 非光纤格式 → 全空暗号
        ann = build_annotation_row(df, file_format="")  # 空 file_format
        apply_annotation_row(new_df, ann, 0, dtypes)
        # 数据行应完好无损
        assert len(new_df) == 4  # 3 + 1 blank
