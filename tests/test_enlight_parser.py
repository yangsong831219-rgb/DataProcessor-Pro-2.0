"""ENLIGHT/Hyperion 解析器黄金测试 — 使用小型确定性fixture

Fixtures (tests/golden/):
  - Peaks_20260512144535_sampled_10pct.txt — minimal Hyperion Peaks format
  - Sensors_20260519093854_sampled_10pct.txt — minimal Hyperion Sensors format
  - legacy_tabs.txt — minimal legacy tab-separated file

断言:
  Peaks:      format=hyperion_peaks_count, header_idx==104, df shape (120, 9), 8 波长列
  Sensors:    format=hyperion_sensors, df shape (200, 17), 8 波长列 + 8 已解码列
  Legacy:     format=legacy_enlight, >100 rows, >=4 columns
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
    """Peaks_20260512144535_sampled_10pct.txt — minimal deterministic fixture"""

    @pytest.fixture
    def path(self):
        p = _get_sample_path("Peaks_20260512144535_sampled_10pct.txt")
        if not os.path.isfile(p):
            pytest.fail(f"Fixture missing: {p}")
        return p

    def test_format_detection(self, path):
        """检测为 hyperion_peaks_count 格式"""
        _df, _annotation, meta = parse_enlight_file(path)
        assert meta["format"] == "hyperion_peaks_count", f"expected hyperion_peaks_count, got {meta['format']}"

    def test_header_idx(self, path):
        """跳过 104 行元数据"""
        _df, _annotation, meta = parse_enlight_file(path)
        assert meta["header_idx"] == 104, f"expected 104, got {meta['header_idx']}"

    def test_df_shape(self, path):
        """清洗后形状 (120, 9) — Timestamp + 8 波长列"""
        df, _annotation, _meta = parse_enlight_file(path)
        assert df.shape == (120, 9), f"expected (120, 9), got {df.shape}"

    def test_wavelength_columns(self, path):
        """8 个波长列均值在 [1525, 1551] 范围内"""
        df, _annotation, _meta = parse_enlight_file(path)
        num_cols, wave_cols = detect_numeric_wavelength_columns(df)
        assert len(wave_cols) == 8, f"expected 8 wavelength cols, got {len(wave_cols)}: {wave_cols}"

        for wc in wave_cols:
            mean_val = float(pd.to_numeric(df[wc], errors="coerce").dropna().mean())
            assert 1525.0 <= mean_val <= 1551.0, f"{wc}: mean={mean_val:.1f} outside [1525, 1551]"

    def test_all_numeric_cols_count(self, path):
        """数值列总计 (含 wavelength + 其它数值)"""
        df, _annotation, _meta = parse_enlight_file(path)
        num_cols, wave_cols = detect_numeric_wavelength_columns(df)
        assert len(num_cols) >= 8, f"expected >=8 numeric cols, got {len(num_cols)}"

    def test_annotation_extracted(self, path):
        """DataFrame 首行不含引号包裹值（暗号行已被正确处理）"""
        df, annotation, _meta = parse_enlight_file(path)
        # Data should be clean — no quoted values in first row
        first_row = df.iloc[0]
        for val in first_row:
            s = str(val).strip()
            quote_chars = "'\"" + chr(0x2018) + chr(0x2019) + chr(0x201C) + chr(0x201D)
            if s and s[0] in quote_chars:
                pytest.fail(f"暗号行未被移除: first row cell = {s[:50]}")


# ═══════════════════════════════════════════════════════════════════════
# Hyperion Sensors 格式
# ═══════════════════════════════════════════════════════════════════════

class TestHyperionSensors:
    """Sensors_20260519093854_sampled_10pct.txt — minimal deterministic fixture"""

    @pytest.fixture
    def path(self):
        p = _get_sample_path("Sensors_20260519093854_sampled_10pct.txt")
        if not os.path.isfile(p):
            pytest.fail(f"Fixture missing: {p}")
        return p

    def test_format_detection(self, path):
        """检测为 hyperion_sensors 格式"""
        _df, _annotation, meta = parse_enlight_file(path)
        assert meta["format"] == "hyperion_sensors", f"expected hyperion_sensors, got {meta['format']}"

    def test_df_shape(self, path):
        """清洗后形状 (200, 17) — 暗号行已剔除，仅保留数值数据行"""
        df, _annotation, _meta = parse_enlight_file(path)
        assert df.shape == (200, 17), f"expected (200, 17), got {df.shape}"

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

        decoded_cols = [c for c in num_cols if c not in wave_cols
                        and "时间" not in str(c).lower()
                        and "time" not in str(c).lower()]
        assert len(decoded_cols) >= 8, f"expected >=8 decoded cols, got {len(decoded_cols)}"

        for dc in decoded_cols[:8]:
            mean_val = float(pd.to_numeric(df[dc], errors="coerce").dropna().mean())
            assert abs(mean_val) < 20.0, f"{dc}: mean={mean_val:.1f} outside [-20, 20]"


# ═══════════════════════════════════════════════════════════════════════
# Legacy ENLIGHT 回退 — 使用仓库内小型确定性fixture
# ═══════════════════════════════════════════════════════════════════════

class TestLegacyEnlightFallback:
    """非 Hyperion 的普通 tab-separated 文件走 legacy 路径"""

    def test_plain_tabs_file(self, tmp_path):
        """普通 TSV 回退到 legacy 并正常解析（使用 tmp_path 生成最小 fixture）"""
        import os as _os
        # Generate a minimal legacy-format tab file
        path = tmp_path / "legacy_test.txt"
        lines = ["col1\tcol2\tcol3\tcol4\tcol5"]
        for r in range(150):
            lines.append(f"{r*2.0:.1f}\t{1525.0 + (r%30)*1.0:.4f}\t{1530.0:.4f}\t{1540.0:.4f}\t{1550.0:.4f}")
        path.write_text("\n".join(lines), encoding="utf-8")

        df, annotation, meta = parse_enlight_file(str(path))
        assert meta["format"] == "legacy_enlight"
        assert len(df) > 100, f"expected >100 rows, got {len(df)}"
        assert df.shape[1] >= 4, f"expected >=4 columns, got {df.shape[1]}"
