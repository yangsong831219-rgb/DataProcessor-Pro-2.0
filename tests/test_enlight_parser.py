"""ENLIGHT/Hyperion 解析器黄金测试

样本文件 (待用户放入 tests/golden/):
  - Peaks_20260512144535_sampled_10pct.txt
  - Sensors_20260519093854_sampled_10pct.txt

断言:
  Peaks:      header_idx==104, df shape (44049, 25), 8 波长列均值[1525,1551]
  Sensors:    df shape (15657, 17), 8 波长列(均值[1526,1551])+8 已解码列(|mean|<20)
"""

from __future__ import annotations

import os
import math
import pytest
import numpy as np
import pandas as pd

from utils.file_parser import parse_enlight_file, detect_numeric_wavelength_columns


_GOLDEN_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "golden")


def _get_sample_path(filename: str) -> str:
    return os.path.join(_GOLDEN_DIR, filename)


# ═══════════════════════════════════════════════════════════════════════
# Hyperion Peaks 格式
# ═══════════════════════════════════════════════════════════════════════

class TestHyperionPeaks:
    """Peaks_20260512144535_sampled_10pct.txt"""

    @pytest.fixture
    def path(self):
        p = _get_sample_path("Peaks_20260512144535_sampled_10pct.txt")
        if not os.path.isfile(p):
            pytest.skip(f"样本文件不存在: {p}")
        return p

    def test_format_detection(self, path):
        """检测为 hyperion_peaks 格式"""
        _df, _annotation, meta = parse_enlight_file(path)
        assert meta["format"] == "hyperion_peaks_count", f"expected hyperion_peaks_count, got {meta['format']}"

    def test_header_idx(self, path):
        """跳过 104 行元数据"""
        _df, _annotation, meta = parse_enlight_file(path)
        assert meta["header_idx"] == 104, f"expected 104, got {meta['header_idx']}"

    def test_df_shape(self, path):
        """清洗后形状 (44049, 9) — Timestamp + 8 波长列 (w1..w8)，计数列已消费"""
        df, _annotation, _meta = parse_enlight_file(path)
        assert df.shape == (44049, 9), f"expected (44049, 9), got {df.shape}"

    def test_wavelength_columns(self, path):
        """8 个波长列均值在 [1525, 1551] 范围内"""
        df, _annotation, _meta = parse_enlight_file(path)
        num_cols, wave_cols = detect_numeric_wavelength_columns(df)
        assert len(wave_cols) == 8, f"expected 8 wavelength cols, got {len(wave_cols)}: {wave_cols}"

        # 每个波长列均值在 1525–1551 nm
        for wc in wave_cols:
            mean_val = float(pd.to_numeric(df[wc], errors="coerce").dropna().mean())
            assert 1525.0 <= mean_val <= 1551.0, f"{wc}: mean={mean_val:.1f} outside [1525, 1551]"

    def test_all_numeric_cols_count(self, path):
        """数值列总计 (含 wavelength + 其它数值)"""
        df, _annotation, _meta = parse_enlight_file(path)
        num_cols, wave_cols = detect_numeric_wavelength_columns(df)
        # Peaks 文件应有足够多数值列
        assert len(num_cols) >= 8, f"expected >=8 numeric cols, got {len(num_cols)}"

    def test_annotation_extracted(self, path):
        """暗号行已抽出，DataFrame 首行不含引号包裹值"""
        df, annotation, _meta = parse_enlight_file(path)
        # 暗号行不应出现在数据中
        first_row = df.iloc[0]
        for val in first_row:
            s = str(val).strip()
            # 允许空字符串或纯数字，但不允许引号包裹的暗号
            quote_chars = "'\"" + chr(0x2018) + chr(0x2019) + chr(0x201C) + chr(0x201D)
            if s and s[0] in quote_chars:
                pytest.fail(f"暗号行未被移除: first row cell = {s[:50]}")


# ═══════════════════════════════════════════════════════════════════════
# Hyperion Sensors 格式
# ═══════════════════════════════════════════════════════════════════════

class TestHyperionSensors:
    """Sensors_20260519093854_sampled_10pct.txt"""

    @pytest.fixture
    def path(self):
        p = _get_sample_path("Sensors_20260519093854_sampled_10pct.txt")
        if not os.path.isfile(p):
            pytest.skip(f"样本文件不存在: {p}")
        return p

    def test_format_detection(self, path):
        """检测为 hyperion_sensors 格式"""
        _df, _annotation, meta = parse_enlight_file(path)
        assert meta["format"] == "hyperion_sensors", f"expected hyperion_sensors, got {meta['format']}"

    def test_df_shape(self, path):
        """清洗后形状 (15656, 17) — 暗号行已剔除，仅保留数值数据行"""
        df, _annotation, _meta = parse_enlight_file(path)
        assert df.shape == (15656, 17), f"expected (15656, 17), got {df.shape}"

    def test_wavelength_columns(self, path):
        """8 个波长列均值在 [1526, 1551] 范围内"""
        df, _annotation, _meta = parse_enlight_file(path)
        num_cols, wave_cols = detect_numeric_wavelength_columns(df)
        assert len(wave_cols) == 8, f"expected 8 wavelength cols, got {len(wave_cols)}: {wave_cols}"

        for wc in wave_cols:
            mean_val = float(pd.to_numeric(df[wc], errors="coerce").dropna().mean())
            assert 1526.0 <= mean_val <= 1551.0, f"{wc}: mean={mean_val:.1f} outside [1526, 1551]"

    def test_decoded_columns(self, path):
        """8 个已解码物理量列 (|均值| < 20)"""
        df, _annotation, _meta = parse_enlight_file(path)
        num_cols, wave_cols = detect_numeric_wavelength_columns(df)

        # 已解码列 = 数值列 - 波长列 (排除时间列)
        decoded_cols = [c for c in num_cols if c not in wave_cols and "时间" not in str(c).lower() and "time" not in str(c).lower()]
        assert len(decoded_cols) >= 8, f"expected >=8 decoded cols, got {len(decoded_cols)}"

        # 每个已解码列 |均值| < 20 (应变/温度量级)
        for dc in decoded_cols[:8]:
            mean_val = float(pd.to_numeric(df[dc], errors="coerce").dropna().mean())
            assert abs(mean_val) < 20.0, f"{dc}: mean={mean_val:.1f} outside [-20, 20]"


# ═══════════════════════════════════════════════════════════════════════
# Legacy ENLIGHT 回退 (使用已有的温度循环数据)
# ═══════════════════════════════════════════════════════════════════════

class TestLegacyEnlightFallback:
    """非 Hyperion 的普通 tab-separated 文件走 legacy 路径"""

    def test_plain_tabs_file(self):
        """普通 TSV (如温度循环数据) 应该回退 legacy 并正常解析"""
        path = "D:/桌面文件/222/4次温度循环温度系数修订/温度循环数据.txt"
        if not os.path.isfile(path):
            pytest.skip(f"文件不存在: {path}")

        df, annotation, meta = parse_enlight_file(path)
        assert meta["format"] == "legacy_enlight"
        assert len(df) > 100, f"expected >100 rows, got {len(df)}"
        assert df.shape[1] >= 4, f"expected >=4 columns"
