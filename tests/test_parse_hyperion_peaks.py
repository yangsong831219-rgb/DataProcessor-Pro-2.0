"""Hyperion Peaks 解析修复黄金测试 — 全合成文本夹具，不依赖真实文件。

覆盖:
  1. 列结构: Timestamp + w1..w8，无 # CH 列
  2. 时间戳归位、波长 dtype 为 float
  3. 波长值正确落位
  4. 变峰数 → 缺峰槽位为 NaN，不串列
  5. 原始文件 annotation 为空
  6. 计数列已消费
  7. 回归隔离: Sensors/Legacy 不误判为 Peaks
"""

from __future__ import annotations

import io
import os
import tempfile

import pandas as pd
import pytest

from utils.file_parser import (
    _N_CH,
    _find_peaks_header,
    _is_peaks_data_row,
    parse_enlight_file,
)


# ═══════════════════════════════════════════════════════════════════════
# 合成文件构建器
# ═══════════════════════════════════════════════════════════════════════

def _make_peaks_file(
    *,
    data_rows: list[str] | None = None,
    num_gratings: int = 8,
) -> str:
    """构建最小 Peaks 文本文件 (temp .txt) 并返回路径。

    布局: 元数据块 + Timestamp\t# CH 1\t...\t# CH 16 + 数据行
    """
    lines: list[str] = []
    # 元数据块
    lines.append("ENLIGHT Version")
    lines.append("Module Type: Hyperion")
    lines.append("Culture: Default")
    lines.append("Date: 2026/05/12")
    # 通道配置 (16 CH)
    for i in range(1, 17):
        lines.append(
            f"CH {i} Configuration:\tThreshold=2.0\tMinWavelength=1525.0\t"
            f"MaxWavelength=1555.0"
        )

    # 表头
    ch_header = "\t".join([f"# CH {i}" for i in range(1, _N_CH + 1)])
    lines.append(f"Timestamp\t{ch_header}")

    if data_rows is not None:
        lines.extend(data_rows)
    else:
        # 默认: 4 行，每行 8 峰
        for t in range(4):
            ts = f"{t + 1:02d}:00.0"
            counts_str = "\t".join([
                "1" if c < num_gratings else "0"
                for c in range(_N_CH)
            ])
            wls_str = "\t".join([
                f"{1525.0 + c * 3 + t * 0.01:.5f}"
                for c in range(num_gratings)
            ])
            lines.append(f"{ts}\t{counts_str}\t{wls_str}")

    fd, path = tempfile.mkstemp(suffix=".txt", prefix="test_peaks_")
    os.close(fd)
    with open(path, "w", encoding="utf-8", newline="") as f:
        f.write("\n".join(lines))
    return path


# ═══════════════════════════════════════════════════════════════════════
# 常量
# ═══════════════════════════════════════════════════════════════════════

_N_CH = 16  # 与 file_parser.py 一致的通道数


# ═══════════════════════════════════════════════════════════════════════
# 测试
# ═══════════════════════════════════════════════════════════════════════

class TestPeaksStructure:
    """列结构与数据类型"""

    def test_columns_are_timestamp_and_wavelengths(self):
        """列 = Timestamp + w1..w8，无 # CH 列"""
        path = _make_peaks_file(num_gratings=8)
        try:
            df, _annotation, meta = parse_enlight_file(path)
            assert meta["format"] == "hyperion_peaks_count"
            cols = list(df.columns)
            assert cols[0] == "Timestamp"
            assert len(cols) == 9, f"expected 9 cols (Timestamp + 8 wl), got {len(cols)}: {cols}"
            for i in range(1, 9):
                assert cols[i] == f"w{i}", f"col[{i}] expected w{i}, got {cols[i]}"
            # 断言无 # CH 列
            for c in cols:
                assert "# CH" not in str(c), f"unexpected # CH column: {c}"
        finally:
            os.unlink(path)

    def test_timestamp_not_contaminated(self):
        """Timestamp 列包含的是时间格式字符串，不是纯数值"""
        path = _make_peaks_file(num_gratings=8)
        try:
            df, _annotation, _meta = parse_enlight_file(path)
            ts0 = str(df["Timestamp"].iloc[0])
            # 包含冒号是时间格式的特征 (如 "01:00.0")
            assert ":" in ts0, f"Timestamp should contain colon (time format), got: {ts0}"
            # Timestamp 列 dtype 应该是 object (字符串)，不是 float/int
            # Timestamp 列应为非数值类型 (object 或 str)，绝不是 float/int
            assert df["Timestamp"].dtype not in ("float64", "int64"), \
                f"Timestamp dtype should not be numeric, got {df['Timestamp'].dtype}"
        finally:
            os.unlink(path)

    def test_wavelength_dtype_is_float(self):
        """波长列 dtype 为 float64"""
        path = _make_peaks_file(num_gratings=8)
        try:
            df, _annotation, _meta = parse_enlight_file(path)
            for col in df.columns:
                if col == "Timestamp":
                    continue
                assert df[col].dtype == "float64", \
                    f"{col} dtype is {df[col].dtype}, expected float64"
        finally:
            os.unlink(path)


class TestWavelengthPlacement:
    """波长值落位正确性"""

    def test_first_row_wavelengths_correct(self):
        """首行波长落在正确列中"""
        path = _make_peaks_file(num_gratings=8)
        try:
            df, _annotation, _meta = parse_enlight_file(path)
            # 默认数据: 1525.0 + c*3 + 0*0.01
            expected = [1525.0, 1528.0, 1531.0, 1534.0, 1537.0, 1540.0, 1543.0, 1546.0]
            for i, exp in enumerate(expected):
                col = f"w{i + 1}"
                val = df[col].iloc[0]
                assert abs(val - exp) < 0.1, \
                    f"{col}: expected ~{exp}, got {val}"
        finally:
            os.unlink(path)

    def test_count_columns_consumed(self):
        """输出中不存在 # CH n 计数列"""
        path = _make_peaks_file(num_gratings=8)
        try:
            df, _annotation, _meta = parse_enlight_file(path)
            # 确认没有计数整数出现在数据中（除了时间戳列外的所有列都是波长）
            non_ts = [c for c in df.columns if c != "Timestamp"]
            for col in non_ts:
                vals = df[col].dropna()
                # 波长应该在 1500-1600 nm 范围
                if len(vals) > 0:
                    assert vals.min() > 1000, \
                        f"{col} contains small values (likely count integers): min={vals.min()}"
        finally:
            os.unlink(path)


class TestVariablePeakCount:
    """变峰数稳健性"""

    def test_missing_peak_produces_nan(self):
        """某行缺一个峰 → 该槽位为 NaN，列集合不变、不串列"""
        # 手动构造数据行: 第 2 行缺 CH1 的峰
        rows = []
        # Row 0: 正常 8 峰
        ts0 = "01:00.0"
        counts0 = "\t".join(["2", "1", "1", "1", "1", "1", "1", "0"] + ["0"] * 8)
        wls0 = "\t".join([f"{1525.0 + i:.5f}" for i in range(8)])
        rows.append(f"{ts0}\t{counts0}\t{wls0}")

        # Row 1: CH1 只有 1 个峰 (max=2, 缺第 2 个)
        ts1 = "02:00.0"
        counts1 = "\t".join(["1", "1", "1", "1", "1", "1", "1", "0"] + ["0"] * 8)
        wls1 = "\t".join([f"{1530.0 + i:.5f}" for i in range(7)])
        rows.append(f"{ts1}\t{counts1}\t{wls1}")

        path = _make_peaks_file(data_rows=rows)
        try:
            df, _annotation, _meta = parse_enlight_file(path)
            # max_counts: CH1=2, CH2-7=1 → 共 8 列
            assert df.shape[1] == 9, f"expected 9 cols (Timestamp + 8 wl), got {df.shape[1]}"
            # Row 0, w2 (CH1 第 2 峰) 应该有值
            assert pd.notna(df["w2"].iloc[0]), "Row 0 CH1 peak 2 should exist"
            # Row 1, w2 (CH1 第 2 峰) 应该是 NaN
            assert pd.isna(df["w2"].iloc[1]), \
                f"Row 1 CH1 peak 2 should be NaN (missing), got {df['w2'].iloc[1]}"
            # Row 1 的 w1 应该有值 (CH1 第 1 峰)
            assert pd.notna(df["w1"].iloc[1]), "Row 1 CH1 peak 1 should exist"
        finally:
            os.unlink(path)

    def test_column_set_stable_across_variable_peaks(self):
        """变峰数文件列集合不变"""
        rows = [
            "01:00.0\t2\t1\t1\t1\t1\t1\t1\t0\t0\t0\t0\t0\t0\t0\t0\t0\t"
            "1525.0\t1525.5\t1528.0\t1531.0\t1534.0\t1537.0\t1540.0\t1543.0",
            "02:00.0\t1\t1\t1\t1\t1\t1\t1\t0\t0\t0\t0\t0\t0\t0\t0\t0\t"
            "1530.0\t1533.0\t1536.0\t1539.0\t1542.0\t1545.0\t1548.0",
        ]
        path = _make_peaks_file(data_rows=rows)
        try:
            df1, _, _ = parse_enlight_file(path)
            cols1 = list(df1.columns)
            assert cols1 == ["Timestamp", "w1", "w2", "w3", "w4", "w5", "w6", "w7", "w8"], \
                f"columns should be stable: {cols1}"
        finally:
            os.unlink(path)


class TestAnnotationEmpty:
    """原始 Peaks 文件无暗号"""

    def test_annotation_empty_for_raw_peaks(self):
        """不含暗号行的原始文件 → annotation = {}"""
        path = _make_peaks_file(num_gratings=8)
        try:
            _df, annotation, _meta = parse_enlight_file(path)
            assert annotation == {}, \
                f"raw Peaks should have empty annotation, got {annotation}"
        finally:
            os.unlink(path)


class TestRegression:
    """回归隔离: Peaks ↔ Sensors ↔ Legacy 互不误判"""

    def test_sensors_not_misidentified_as_peaks(self):
        """Sensors 文件不被误判为 Peaks"""
        # 写一个带 BOM 的 Sensors 文件
        sensors_text = (
            "﻿Timestamp\tA1G1\tA1G2\tFBG_A1\tFBG_A2\n"
            "2026/5/19 09:38:55.04646\t-0.637\t0.123\t1525.123\t1545.456\n"
            "2026/5/19 09:38:56.04646\t-0.640\t0.125\t1525.130\t1545.460\n"
        )
        fd, path = tempfile.mkstemp(suffix=".txt", prefix="test_sensors_")
        os.close(fd)
        with open(path, "wb") as f:
            f.write(sensors_text.encode("utf-8"))
        try:
            _df, _annotation, meta = parse_enlight_file(path)
            assert meta["format"] == "hyperion_sensors", \
                f"expected hyperion_sensors, got {meta['format']}"
        finally:
            os.unlink(path)

    def test_legacy_not_misidentified_as_peaks(self):
        """普通 TSV 文件不走 Peaks 分支"""
        legacy_text = (
            "Timestamp\tValue1\tValue2\n"
            "0.0\t1.0\t2.0\n"
            "1.0\t1.1\t2.1\n"
        )
        fd, path = tempfile.mkstemp(suffix=".txt", prefix="test_legacy_")
        os.close(fd)
        with open(path, "w", encoding="utf-8") as f:
            f.write(legacy_text)
        try:
            _df, _annotation, meta = parse_enlight_file(path)
            assert meta["format"] != "hyperion_peaks", \
                f"plain TSV should not be identified as peaks"
        finally:
            os.unlink(path)

    def test_peaks_not_used_without_ch_header(self):
        """有 Hyperion 标志但无 # CH 1 表头 → 不走 Peaks"""
        text = (
            "ENLIGHT Version: 2.0\n"
            "Module Type: Hyperion\n"
            "Timestamp\tCol1\tCol2\n"
            "0.0\t1.0\t2.0\n"
        )
        fd, path = tempfile.mkstemp(suffix=".txt", prefix="test_nopeaks_")
        os.close(fd)
        with open(path, "w", encoding="utf-8") as f:
            f.write(text)
        try:
            _df, _annotation, meta = parse_enlight_file(path)
            # 有 Hyperion 元数据但无 # CH 1 表头 → 不应走 Peaks
            assert meta["format"] != "hyperion_peaks", \
                f"should not be peaks without # CH header, got {meta['format']}"
        finally:
            os.unlink(path)


# ═══════════════════════════════════════════════════════════════════════
# 回归测试: "Timestamp Format: Full" 元数据不误锚 + 非法行不崩溃
# ═══════════════════════════════════════════════════════════════════════

def _make_peaks_file_with_crash_metadata() -> str:
    """构建含陷阱元数据的 Peaks 文件:
      - 元数据块含 "Timestamp Format: Full" (空格, 不是 \\t)
      - 至少一行空字段元数据行 (如 "Name: \t\t...")
      - CH 配置块
      - 真数据表头 "Timestamp\t# CH 1\t...\t# CH 16"
      - 几行真实数据
    """
    lines: list[str] = []
    # ── 陷阱元数据 ──
    lines.append("ENLIGHT Version: 3.0")
    lines.append("Module Type: Hyperion")
    lines.append("Timestamp Format: Full")           # ← 关键陷阱行
    lines.append("Date: 2026/05/12")
    lines.append("Name: \t\t\t\t")                    # ← 空字段元数据行
    lines.append("Culture: Default")

    # ── CH 配置 (16 行) ──
    for i in range(1, _N_CH + 1):
        lines.append(
            f"CH {i} Configuration:\tThreshold=2.0\t"
            f"MinWavelength=1525.0\tMaxWavelength=1555.0"
        )

    # ── 真数据表头 ──
    ch_header = "\t".join([f"# CH {i}" for i in range(1, _N_CH + 1)])
    lines.append(f"Timestamp\t{ch_header}")

    # ── 数据行: 8 峰 ──
    for t in range(3):
        ts = f"{t + 1:02d}:00.0"
        counts_str = "\t".join([
            "1" if c < 8 else "0" for c in range(_N_CH)
        ])
        wls_str = "\t".join([
            f"{1525.0 + c * 3 + t * 0.01:.5f}" for c in range(8)
        ])
        lines.append(f"{ts}\t{counts_str}\t{wls_str}")

    fd, path = tempfile.mkstemp(suffix=".txt", prefix="test_crash_peaks_")
    os.close(fd)
    with open(path, "w", encoding="utf-8", newline="") as f:
        f.write("\n".join(lines))
    return path


def _make_peaks_file_with_illegal_row() -> str:
    """构建含一行非法计数行（空串代替整数）的 Peaks 文件。"""
    lines: list[str] = []
    lines.append("ENLIGHT Version: 3.0")
    lines.append("Module Type: Hyperion")
    lines.append("Timestamp Format: Full")           # 陷阱 1
    for i in range(1, _N_CH + 1):
        lines.append(f"CH {i} Configuration:\tThreshold=2.0")
    ch_header = "\t".join([f"# CH {i}" for i in range(1, _N_CH + 1)])
    lines.append(f"Timestamp\t{ch_header}")

    # 正常数据行
    normal_counts = "\t".join(["1" if c < 8 else "0" for c in range(_N_CH)])
    normal_wls = "\t".join([f"{1525.0 + c * 3:.5f}" for c in range(8)])
    lines.append(f"01:00.0\t{normal_counts}\t{normal_wls}")

    # 非法行: 计数列全为 "" (模拟残留元数据行)
    empty_counts = "\t".join(["" for _ in range(_N_CH)])
    lines.append(f"Name:\t{empty_counts}")

    # 正常行
    lines.append(f"02:00.0\t{normal_counts}\t{normal_wls}")

    fd, path = tempfile.mkstemp(suffix=".txt", prefix="test_illegal_peaks_")
    os.close(fd)
    with open(path, "w", encoding="utf-8", newline="") as f:
        f.write("\n".join(lines))
    return path


# ═══════════════════════════════════════════════════════════════════════
# 测试
# ═══════════════════════════════════════════════════════════════════════

class TestTimestampFormatMetadataCrash:
    """Peaks 文件含 "Timestamp Format: Full" 元数据行 → 不崩溃，正确解析"""

    def test_find_peaks_header_returns_true_header(self):
        """_find_peaks_header 返回真表头行号，不是 'Timestamp Format' 行"""
        path = _make_peaks_file_with_crash_metadata()
        try:
            with open(path, "r", encoding="utf-8") as f:
                lines = [ln.rstrip("\r") for ln in f.read().split("\n")]
            idx = _find_peaks_header(lines)
            assert idx is not None, "should find the real header"
            header_line = lines[idx]
            assert header_line.startswith("Timestamp\t# CH 1"), \
                f"found wrong header: {repr(header_line)}"
            assert "Format" not in header_line, \
                f"anchored to 'Timestamp Format' metadata row: {repr(header_line)}"
        finally:
            os.unlink(path)

    def test_load_does_not_crash(self):
        """含陷阱元数据的文件正常解析，不抛异常"""
        path = _make_peaks_file_with_crash_metadata()
        try:
            df, _annotation, meta = parse_enlight_file(path)
            assert meta["format"] == "hyperion_peaks_count"
            assert len(df) == 3, f"expected 3 data rows, got {len(df)}"
        finally:
            os.unlink(path)

    def test_timestamp_column_has_real_values(self):
        """Timestamp 列首值为真实时间戳，不是元数据文本"""
        path = _make_peaks_file_with_crash_metadata()
        try:
            df, _annotation, _meta = parse_enlight_file(path)
            ts0 = str(df["Timestamp"].iloc[0])
            assert ":" in ts0, f"expected time-like timestamp, got {repr(ts0)}"
            assert "Format" not in ts0, \
                f"Timestamp column contains metadata: {repr(ts0)}"
            assert "ENLIGHT" not in ts0, \
                f"Timestamp column contains metadata: {repr(ts0)}"
        finally:
            os.unlink(path)

    def test_wavelength_columns_are_float(self):
        """波长列为 float，数据正确"""
        path = _make_peaks_file_with_crash_metadata()
        try:
            df, _annotation, _meta = parse_enlight_file(path)
            for col in df.columns:
                if col == "Timestamp":
                    continue
                assert df[col].dtype == "float64", \
                    f"{col} dtype is {df[col].dtype}"
                assert pd.notna(df[col].iloc[0]), \
                    f"{col} first value should not be NaN"
        finally:
            os.unlink(path)


class TestIsPeaksDataRowDefense:
    """_is_peaks_data_row 防御式校验 — 非法行被跳过不崩溃"""

    def test_illegal_row_skipped_by_defense(self):
        """含空串计数列的行被 _is_peaks_data_row 拒绝"""
        path = _make_peaks_file_with_illegal_row()
        try:
            df, _annotation, _meta = parse_enlight_file(path)
            # 应解析出 2 行合法数据，非法行被跳过
            assert len(df) == 2, \
                f"expected 2 data rows (1 illegal row skipped), got {len(df)}"
            # 首行时间戳正确
            assert df["Timestamp"].iloc[0] == "01:00.0"
            assert df["Timestamp"].iloc[1] == "02:00.0"
        finally:
            os.unlink(path)

    def test_is_peaks_data_row_rejects_empty_counts(self):
        """_is_peaks_data_row: 空字符串计数列 → 返回 False"""
        f = [""] * 20
        assert not _is_peaks_data_row(f), "all-empty fields should be rejected"

    def test_is_peaks_data_row_rejects_non_numeric_counts(self):
        """_is_peaks_data_row: 非数字计数列 → 返回 False"""
        f = ["01:00.0"]   # timestamp
        f += ["abc" for _ in range(_N_CH)]  # non-numeric counts
        f += ["1525.0"]   # fake wavelength
        assert not _is_peaks_data_row(f), "non-numeric counts should be rejected"

    def test_is_peaks_data_row_accepts_valid_row(self):
        """_is_peaks_data_row: 合法行 → 返回 True"""
        f = ["01:00.0"]   # timestamp
        f += ["1" if c < 8 else "0" for c in range(_N_CH)]  # valid counts
        f += ["1525.0" for _ in range(8)]  # wavelengths
        assert _is_peaks_data_row(f), "valid row should be accepted"


# ═══════════════════════════════════════════════════════════════════════
# 宽数据行回归: 12 波长 / 29 字段 — sum(counts)=12 不被误判为畸形
# ═══════════════════════════════════════════════════════════════════════

def _make_peaks_file_wide(num_data_rows: int = 3) -> str:
    """构建宽 Peaks 文件: 12 波长列，每行 29 字段 (1+16+12)。"""
    lines: list[str] = []
    lines.append("ENLIGHT Version: 3.0")
    lines.append("Module Type: Hyperion")
    lines.append("Timestamp Format: Full")           # 陷阱元数据
    for i in range(1, _N_CH + 1):
        lines.append(f"CH {i} Configuration:\tThreshold=2.0")
    ch_header = "\t".join([f"# CH {i}" for i in range(1, _N_CH + 1)])
    lines.append(f"Timestamp\t{ch_header}")

    # 12 波长列: CH0..CH11 每通道 1 峰，其余 0
    for t in range(num_data_rows):
        ts = f"{t + 1:02d}:00.0"
        counts = "\t".join(["1" if c < 12 else "0" for c in range(_N_CH)])
        wls = "\t".join([f"{1525.0 + c * 2.5 + t * 0.01:.5f}" for c in range(12)])
        lines.append(f"{ts}\t{counts}\t{wls}")

    fd, path = tempfile.mkstemp(suffix=".txt", prefix="test_wide_peaks_")
    os.close(fd)
    with open(path, "w", encoding="utf-8", newline="") as f:
        f.write("\n".join(lines))
    return path


class TestWideDataRow12Wavelengths:
    """12 波长 / 29 字段宽数据行 → 不被误判为畸形，正确解析"""

    def test_wide_row_not_skipped(self):
        """宽行 (len(f)==29) 不被 _is_peaks_data_row 拒绝"""
        path = _make_peaks_file_wide()
        try:
            df, _annotation, meta = parse_enlight_file(path)
            assert meta["format"] == "hyperion_peaks_count"
            assert len(df) == 3, f"expected 3 data rows, got {len(df)}"
        finally:
            os.unlink(path)

    def test_wide_row_has_12_wavelength_columns(self):
        """宽文件输出 12 个波长列 + Timestamp = 13 列"""
        path = _make_peaks_file_wide()
        try:
            df, _annotation, _meta = parse_enlight_file(path)
            n_wl = df.shape[1] - 1  # minus Timestamp
            assert n_wl == 12, f"expected 12 wavelength cols, got {n_wl}"
            cols = list(df.columns)
            assert cols[0] == "Timestamp"
            for i in range(1, 13):
                assert cols[i] == f"w{i}"
        finally:
            os.unlink(path)

    def test_wide_row_wavelengths_are_float(self):
        """宽文件所有波长列为 float64"""
        path = _make_peaks_file_wide()
        try:
            df, _annotation, _meta = parse_enlight_file(path)
            for col in df.columns:
                if col == "Timestamp":
                    continue
                assert df[col].dtype == "float64", \
                    f"{col} dtype is {df[col].dtype}"
                assert pd.notna(df[col].iloc[0]), \
                    f"{col}: first value NaN"
        finally:
            os.unlink(path)

    def test_is_peaks_data_row_accepts_29_field_row(self):
        """_is_peaks_data_row: 29 字段合法行 → True"""
        f = ["01:00.0"]
        f += ["1" if c < 12 else "0" for c in range(_N_CH)]
        f += [f"{1525.0 + c * 2.5:.5f}" for c in range(12)]
        assert _is_peaks_data_row(f), \
            f"29-field row should be accepted, len(f)={len(f)}"


# ═══════════════════════════════════════════════════════════════════════
# 矩形 Peaks 格式回归 — 软件保存的矩形文件 (无计数列)
# ═══════════════════════════════════════════════════════════════════════


def _make_peaks_rect_file(
    *,
    num_wl: int = 12,
    num_rows: int = 3,
    with_annotation: bool = True,
    use_zero_timestamp: bool = False,
) -> str:
    """构建矩形 Peaks 文件 (软件保存格式，无计数列)。

    布局: 元数据块 + Timestamp\t# CH 1..N 表头 [+ 暗号行] + 数据行
    数据行: 时间戳 + N个波长 (矩形，无计数列)。
    """
    lines: list[str] = []
    # 元数据块
    lines.append("ENLIGHT Version: 3.0")
    lines.append("Module Type: Hyperion")
    lines.append("Culture: Default")
    lines.append("Date: 2026/05/12")
    # 通道配置
    for i in range(1, num_wl + 1):
        lines.append(
            f"CH {i} Configuration:\tThreshold=2.0\t"
            f"MinWavelength=1525.0\tMaxWavelength=1555.0"
        )

    # 表头: Timestamp\t# CH 1\t...\t# CH N
    ch_header = "\t".join([f"# CH {i}" for i in range(1, num_wl + 1)])
    lines.append(f"Timestamp\t{ch_header}")

    # 暗号行 (可选)
    if with_annotation:
        ann_parts = ["'时间戳'"]  # Timestamp 列的暗号
        for i in range(1, num_wl + 1):
            ann_parts.append(f"'w{i}-类型-位置'")
        lines.append("\t".join(ann_parts))

    # 数据行
    for t in range(num_rows):
        if use_zero_timestamp:
            ts = "0"
        else:
            ts = f"2026/5/12 {t + 1:02d}:00:{t:02d}.0"
        wls = "\t".join(
            [f"{1525.0 + c * 3 + t * 0.01:.5f}" for c in range(num_wl)]
        )
        lines.append(f"{ts}\t{wls}")

    fd, path = tempfile.mkstemp(suffix=".txt", prefix="test_peaks_rect_")
    os.close(fd)
    with open(path, "w", encoding="utf-8", newline="") as f:
        f.write("\n".join(lines))
    return path


class TestRectangularPeaks:
    """矩形 Peaks (软件保存，无计数列)"""

    def test_rectangular_13_fields_real_timestamp(self):
        """矩形 + 真实时间戳 + 13 字段 (Timestamp + 12 波长)"""
        path = _make_peaks_rect_file(num_wl=12, with_annotation=True, use_zero_timestamp=False)
        try:
            df, annotation, meta = parse_enlight_file(path)
            assert meta["format"] == "hyperion_peaks_rect"
            assert len(df) == 3, f"expected 3 data rows, got {len(df)}"
            cols = list(df.columns)
            assert cols[0] == "Timestamp"
            assert len(cols) == 13, f"expected 13 cols, got {len(cols)}: {cols}"
            for i in range(1, 13):
                assert cols[i] == f"w{i}", f"col[{i}] expected w{i}, got {cols[i]}"
            # Timestamp 应为含真实时间特征的字符串
            ts0 = str(df["Timestamp"].iloc[0])
            assert ":" in ts0, f"Timestamp should contain colon, got {ts0!r}"
            assert "/" in ts0, f"Timestamp should contain slash, got {ts0!r}"
            # w 列为 float
            for i in range(1, 13):
                col = f"w{i}"
                assert df[col].dtype == "float64", f"{col} dtype is {df[col].dtype}"
                assert pd.notna(df[col].iloc[0]), f"{col}: first value NaN"
            # annotation: 键含 w1..w12，值无引号
            assert "Timestamp" in annotation
            assert "时间戳" in annotation["Timestamp"], \
                f"annotation[Timestamp]={annotation.get('Timestamp')!r}"
            for i in range(1, 13):
                wkey = f"w{i}"
                assert wkey in annotation, f"annotation missing {wkey}"
                val = annotation[wkey]
                assert wkey in val, f"annotation[{wkey}]={val!r}, expected contains {wkey!r}"
                # 值无引号
                assert "'" not in val, f"annotation[{wkey}]={val!r} should have no quotes"
                assert '"' not in val, f"annotation[{wkey}]={val!r} should have no quotes"
        finally:
            os.unlink(path)

    def test_rectangular_17_fields_legacy_timestamp(self):
        """矩形 + Timestamp=0 + 17 字段 (旧存档)"""
        path = _make_peaks_rect_file(
            num_wl=16, with_annotation=False, use_zero_timestamp=True
        )
        try:
            df, _annotation, meta = parse_enlight_file(path)
            assert meta["format"] == "hyperion_peaks_rect"
            assert len(df) == 3, f"expected 3 data rows, got {len(df)}"
            cols = list(df.columns)
            assert cols[0] == "Timestamp"
            assert len(cols) == 17, f"expected 17 cols, got {len(cols)}: {cols}"
            # Timestamp 为 "0" (字符串)
            assert df["Timestamp"].iloc[0] == "0"
            # w 列为 float
            for i in range(1, 17):
                col = f"w{i}"
                assert df[col].dtype == "float64", f"{col} dtype is {df[col].dtype}"
                assert pd.notna(df[col].iloc[0]), f"{col}: first value NaN"
        finally:
            os.unlink(path)

    def test_count_format_still_works(self):
        """计数列格式 (原始仪器导出) 仍走 parse_hyperion_peaks，不回归"""
        path = _make_peaks_file(num_gratings=8)
        try:
            df, _annotation, meta = parse_enlight_file(path)
            assert meta["format"] == "hyperion_peaks_count"
            cols = list(df.columns)
            assert cols[0] == "Timestamp"
            assert len(cols) == 9, f"expected 9 cols, got {len(cols)}: {cols}"
            for i in range(1, 9):
                assert cols[i] == f"w{i}"
            # 无 # CH 列
            for c in cols:
                assert "# CH" not in str(c)
        finally:
            os.unlink(path)
