"""parse_validation.py — 解析后校验层 黄金测试

覆盖:
  1. 空表/0 行 → raise
  2. 列名含元数据特征 → raise
  3. 波长列首值异常 (0 / -0.5) → raise
  4. 暗号/元数据污染 object 列 → raise
  5. 正常 Sensors/Peaks 解析 → 放行
  6. 非 ENLIGHT 通用文件 → lenient 档放行
"""

from __future__ import annotations

import tempfile
import os

import pandas as pd
import numpy as np
import pytest

from utils.parse_validation import (
    validate_parsed_data,
    ParseValidationError,
    _is_wave_value,
    _WAVE_LO,
    _WAVE_HI,
)


# ═══════════════════════════════════════════════════════════════════════
# 夹具
# ═══════════════════════════════════════════════════════════════════════


def _make_valid_sensors_df(n_wave: int = 3, n_rows: int = 5) -> pd.DataFrame:
    """构造合法的 Sensors DataFrame。"""
    cols = ["Timestamp"]
    cols += [f"F{i+1}" for i in range(3)]  # 公式列
    cols += [f"FBG_{c}" for c in "ABC"[:n_wave]]  # 波长列
    rows = []
    for t in range(n_rows):
        ts = f"2026/5/12 {t+1:02d}:00:{t:02d}.0"
        f_vals = [f"{-0.5 + t*0.01+i*0.001:.5f}" for i in range(3)]
        w_vals = [f"{1525.0 + i*2.5 + t*0.01:.5f}" for i in range(n_wave)]
        rows.append([ts] + f_vals + w_vals)
    df = pd.DataFrame(rows, columns=cols)
    for c in cols[1:]:
        df[c] = pd.to_numeric(df[c], errors="coerce")
    return df


def _make_valid_meta(fmt: str = "hyperion_sensors", fbg_cols: list[str] | None = None) -> dict[str, object]:
    d: dict[str, object] = {"format": fmt}
    if fbg_cols is not None:
        d["fbg_cols"] = fbg_cols
    return d


# ═══════════════════════════════════════════════════════════════════════
# _is_wave_value 单元测试
# ═══════════════════════════════════════════════════════════════════════


class TestIsWaveValue:
    def test_1525_is_wave(self):
        assert _is_wave_value("1525.0") is True
        assert _is_wave_value(1525.0) is True

    def test_0_is_not_wave(self):
        assert _is_wave_value("0") is False
        assert _is_wave_value(0) is False

    def test_minus_0_5_is_not_wave(self):
        assert _is_wave_value("-0.5") is False
        assert _is_wave_value(-0.5) is False

    def test_non_numeric_is_not_wave(self):
        assert _is_wave_value("时间戳") is False
        assert _is_wave_value("FBG_G1 (FBG): Range(...)") is False

    def test_boundary_lo(self):
        assert _is_wave_value(f"{_WAVE_LO}") is True

    def test_boundary_hi(self):
        assert _is_wave_value(f"{_WAVE_HI}") is True

    def test_boundary_above(self):
        assert _is_wave_value("1701.0") is False


# ═══════════════════════════════════════════════════════════════════════
# 空表 / 行数不足
# ═══════════════════════════════════════════════════════════════════════


class TestEmptyAndRowCount:
    def test_none_df_raises(self):
        with pytest.raises(ParseValidationError, match="空表"):
            validate_parsed_data(None, _make_valid_meta(), source_path="/fake.txt")

    def test_empty_df_raises(self):
        df = pd.DataFrame()
        with pytest.raises(ParseValidationError, match="空表"):
            validate_parsed_data(df, _make_valid_meta(), source_path="/fake.txt")

    def test_zero_rows_raises(self):
        df = pd.DataFrame(columns=["Timestamp", "FBG_A"])
        with pytest.raises(ParseValidationError):
            validate_parsed_data(df, _make_valid_meta(), source_path="/fake.txt")

    def test_one_row_passes(self):
        df = _make_valid_sensors_df(n_rows=1)
        validate_parsed_data(df, _make_valid_meta(fbg_cols=["FBG_A"]),
                             source_path="/fake.txt")  # 不应 raise


# ═══════════════════════════════════════════════════════════════════════
# 元数据泄漏检测
# ═══════════════════════════════════════════════════════════════════════


class TestMetadataLeak:
    def test_fbg_meta_in_column_name_raises(self):
        """列名含 '(FBG):' → raise"""
        df = pd.DataFrame({
            "Timestamp": ["01:00.0"],
            "FBG_G1 (FBG): #Avg=1, Range(1525,1555)": [1525.0],
        })
        # 第二列需是 float 否则也会被数值纯度检测拦
        df.iloc[:, 1] = pd.to_numeric(df.iloc[:, 1])
        with pytest.raises(ParseValidationError, match="元数据"):
            validate_parsed_data(df, _make_valid_meta(), source_path="/fake.txt")

    def test_configuration_in_column_name_raises(self):
        """列名含 'Configuration' → raise"""
        df = pd.DataFrame({
            "Timestamp": ["01:00.0"],
            "CH 1 Configuration": [1525.0],
        })
        df.iloc[:, 1] = pd.to_numeric(df.iloc[:, 1])
        with pytest.raises(ParseValidationError, match="元数据"):
            validate_parsed_data(df, _make_valid_meta(), source_path="/fake.txt")

    def test_range_in_column_name_raises(self):
        """列名含 'Range (' → raise"""
        df = pd.DataFrame({
            "Timestamp": ["01:00.0"],
            "FBG_A1 Range (1525, 1555)": [1525.0],
        })
        df.iloc[:, 1] = pd.to_numeric(df.iloc[:, 1])
        with pytest.raises(ParseValidationError, match="元数据"):
            validate_parsed_data(df, _make_valid_meta(), source_path="/fake.txt")


# ═══════════════════════════════════════════════════════════════════════
# 波长列校验
# ═══════════════════════════════════════════════════════════════════════


class TestWaveColumnValidation:
    def test_wave_first_value_zero_raises(self):
        """FBG 列首值 = 0 → raise (列错位/时间戳=0)"""
        df = _make_valid_sensors_df(n_wave=3, n_rows=3)
        df["FBG_A"] = 0.0  # 覆盖为 0
        # 还需要一个正常的 FBG 列让校验知道这是波长列
        with pytest.raises(ParseValidationError, match="波长列"):
            validate_parsed_data(df, _make_valid_meta(fbg_cols=["FBG_A"]),
                                 source_path="/fake.txt")

    def test_wave_first_value_minus_point_five_raises(self):
        """FBG 列首值 = -0.5 (公式列错位) → raise"""
        df = _make_valid_sensors_df(n_wave=3, n_rows=3)
        df["FBG_A"] = -0.5  # 覆盖为公式列值
        with pytest.raises(ParseValidationError, match="波长列"):
            validate_parsed_data(df, _make_valid_meta(fbg_cols=["FBG_A"]),
                                 source_path="/fake.txt")

    def test_wave_first_value_normal_passes(self):
        """FBG 列首值正常 1525 → 不 raise"""
        df = _make_valid_sensors_df(n_wave=3, n_rows=3)
        validate_parsed_data(df, _make_valid_meta(fbg_cols=["FBG_A"]),
                             source_path="/fake.txt")  # 不应 raise

    def test_wave_col_all_nan_skips(self):
        """FBG 列全 NaN → 跳过检测，不 raise"""
        df = _make_valid_sensors_df(n_wave=3, n_rows=3)
        df["FBG_A"] = np.nan
        validate_parsed_data(df, _make_valid_meta(fbg_cols=["FBG_B", "FBG_C"]),
                             source_path="/fake.txt")  # 不应 raise


# ═══════════════════════════════════════════════════════════════════════
# 数值纯度 / 暗号污染
# ═══════════════════════════════════════════════════════════════════════


class TestNumericPurity:
    def test_object_col_half_non_numeric_raises(self):
        """object 列过半不可转数值 → raise (用公式列测, 避免波長检查先拦截)"""
        df = _make_valid_sensors_df(n_wave=3, n_rows=4)
        # 公式列 F1 改为 object 且填充非数值
        df["F1"] = ["时间戳", "应变", "暗号", "w1-类型-位置"]
        df["F1"] = df["F1"].astype(object)
        with pytest.raises(ParseValidationError, match="非数值占比过半"):
            validate_parsed_data(df, _make_valid_meta(fbg_cols=["FBG_A"]),
                                 source_path="/fake.txt")

    def test_mixed_numeric_object_below_half_passes(self):
        """object 列 80% 可转数值 → 不 raise (主要是数值, 少量 str)"""
        df = _make_valid_sensors_df(n_wave=3, n_rows=5)
        # 公式列 F1: 5行中仅1行非数值 → <50%
        df["F1"] = df["F1"].astype(object)
        df.iloc[0, df.columns.get_loc("F1")] = "bad"
        validate_parsed_data(df, _make_valid_meta(fbg_cols=["FBG_A"]),
                             source_path="/fake.txt")  # 不应 raise


# ═══════════════════════════════════════════════════════════════════════
# 正常解析放行
# ═══════════════════════════════════════════════════════════════════════


class TestValidPassThrough:
    def test_valid_sensors_passes(self):
        df = _make_valid_sensors_df(n_wave=3, n_rows=5)
        meta = _make_valid_meta(fmt="hyperion_sensors", fbg_cols=["FBG_A", "FBG_B", "FBG_C"])
        validate_parsed_data(df, meta, source_path="/fake.txt")  # 不应 raise

    def test_valid_sensors_detected_through_parse_enlight_file(self):
        """通过 parse_enlight_file 加载合法 Sensors 文件 — 不应 raise"""
        from utils.file_parser import parse_enlight_file
        # 构造一个最小合法 Sensors 文件
        lines = ["Timestamp\tF1\tFBG_A", "01:00.0\t-0.5\t1525.0"]
        fd, path = tempfile.mkstemp(suffix=".txt", prefix="test_valid_")
        os.close(fd)
        with open(path, "w", encoding="utf-8", newline="") as f:
            f.write("\n".join(lines))
        try:
            df, _annotation, meta = parse_enlight_file(path)
            assert len(df) == 1
        finally:
            os.unlink(path)


# ═══════════════════════════════════════════════════════════════════════
# 宽松模式 (非 ENLIGHT)
# ═══════════════════════════════════════════════════════════════════════


class TestLenientMode:
    def test_generic_tiny_file_passes_lenient(self):
        """3 行通用 TSV → lenient 档放行"""
        df = pd.DataFrame({"A": [1, 2, 3], "B": [4, 5, 6]})
        validate_parsed_data(
            df, {"format": "csv"}, source_path="/fake.csv", strict=False,
        )  # 不应 raise

    def test_generic_empty_raises_even_lenient(self):
        """空表 → lenient 档也 raise"""
        with pytest.raises(ParseValidationError, match="空表"):
            validate_parsed_data(
                None, {"format": "csv"}, source_path="/fake.csv", strict=False,
            )

    def test_generic_metadata_leak_raises_even_lenient(self):
        """元数据泄漏 → lenient 档也 raise"""
        df = pd.DataFrame({
            "A": [1],
            "FBG_G1 (FBG):#Avg=1": [2],
        })
        df.iloc[:, 1] = pd.to_numeric(df.iloc[:, 1])
        with pytest.raises(ParseValidationError, match="元数据"):
            validate_parsed_data(
                df, {"format": "csv"}, source_path="/fake.csv", strict=False,
            )

    def test_generic_skips_wave_and_purity_checks(self):
        """lenient 档跳过 FBG 波长 + 数值纯度校验（即使 FBG_ 首值=0 也不报）"""
        df = pd.DataFrame({"Timestamp": ["t1"], "FBG_X": [0.0]})
        df["FBG_X"] = pd.to_numeric(df["FBG_X"])
        validate_parsed_data(
            df, {"format": "csv"}, source_path="/fake.csv", strict=False,
        )  # 不应 raise


# ═══════════════════════════════════════════════════════════════════════
# 缺失 Timestamp 列
# ═══════════════════════════════════════════════════════════════════════


class TestMissingTimestamp:
    def test_missing_timestamp_raises_strict(self):
        df = pd.DataFrame({"Col1": [1.0], "FBG_A": [1525.0]})
        df["FBG_A"] = pd.to_numeric(df["FBG_A"])
        with pytest.raises(ParseValidationError, match="缺少 Timestamp"):
            validate_parsed_data(df, _make_valid_meta(), source_path="/fake.txt")
