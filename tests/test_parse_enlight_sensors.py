"""ENLIGHT Sensors（公式版）解析黄金测试 — 全合成文本夹具，不依赖真实文件。

覆盖:
  1. BOM 处理: 表头第一列 == "Timestamp" (无 \\ufeff)
  2. 暗号抽取: 键值无引号
  3. 数值纯净: 除 Timestamp 外全 float
  4. 稀疏标注: 未标注列不在 annotation
  5. 混合引号全剥
  6. 格式判别 Sensors↔Peaks 互不误判
  7. 暗号往返 smoke
  8. 回归隔离
"""

from __future__ import annotations

import os
import tempfile

import pandas as pd
import pytest

from utils.file_parser import parse_enlight_file


# ═══════════════════════════════════════════════════════════════════════
# 合成文件构建器
# ═══════════════════════════════════════════════════════════════════════

_QUOTE_CHARS_TEST = "'\"‘’“”"  # 直+弯


def _make_sensors_file(
    *,
    with_bom: bool = True,
    with_annotation_row: bool = True,
    use_mixed_quotes: bool = False,
    data_rows: int = 3,
) -> str:
    """构建最小 Sensors 文本文件 (temp .txt) 并返回路径。

    布局: [可选 BOM] + 表头 + [可选暗号行] + 数据行
    """
    lines: list[str] = []

    # 表头
    header = "Timestamp\tA1G1\tA1G2\tA2G1\tFBG_A1\tFBG_A2"
    lines.append(header)

    # 暗号行
    if with_annotation_row:
        if use_mixed_quotes:
            # 混合引号: 直引号 + 弯引号
            ts_anno = "'时间戳'"  # 直引号
            a1g1_anno = "‘应变-光纤1’"  # 弯引号左/右
            a1g2_anno = "“应变-光纤2”"  # 弯双引号左/右
            lines.append(f"{ts_anno}\t{a1g1_anno}\t{a1g2_anno}\t\t\t")
        else:
            # 标准直引号
            lines.append("'时间戳'\t'应变-光纤1'\t'应变-光纤2'\t\t\t")

    # 数据行
    for t in range(data_rows):
        ts = f"2026/5/19 09:38:5{t}.04646"
        vals = [
            f"{-0.637 + t * 0.001:.5f}",  # A1G1
            f"{0.123 + t * 0.001:.5f}",   # A1G2
            f"{1.234 + t * 0.001:.5f}",   # A2G1
            f"{1525.123 + t:.5f}",         # FBG_A1
            f"{1545.456 + t:.5f}",         # FBG_A2
        ]
        lines.append(f"{ts}\t" + "\t".join(vals))

    text = "\n".join(lines)

    fd, path = tempfile.mkstemp(suffix=".txt", prefix="test_sensors_")
    os.close(fd)
    if with_bom:
        with open(path, "wb") as f:
            f.write(b"\xef\xbb\xbf")  # UTF-8 BOM
            f.write(text.encode("utf-8"))
    else:
        with open(path, "w", encoding="utf-8") as f:
            f.write(text)
    return path


# ═══════════════════════════════════════════════════════════════════════
# 测试
# ═══════════════════════════════════════════════════════════════════════

class TestBOMHandling:
    """BOM 处理"""

    def test_header_no_bom_pollution(self):
        """表头第一列 == 'Timestamp' (不含 \\ufeff)"""
        path = _make_sensors_file(with_bom=True, with_annotation_row=False)
        try:
            df, _annotation, meta = parse_enlight_file(path)
            assert meta["format"] == "hyperion_sensors"
            first_col = str(df.columns[0])
            assert first_col == "Timestamp", \
                f"expected 'Timestamp', got {repr(first_col)}"
            assert "﻿" not in first_col, "BOM leaked into column name"
        finally:
            os.unlink(path)

    def test_bom_detected_in_meta(self):
        """meta 记录 BOM 状态"""
        path = _make_sensors_file(with_bom=True, with_annotation_row=False)
        try:
            _df, _annotation, meta = parse_enlight_file(path)
            assert meta.get("had_bom") is True, \
                f"expected had_bom=True, got {meta.get('had_bom')}"
        finally:
            os.unlink(path)


class TestAnnotationExtraction:
    """暗号抽取正确性"""

    def test_annotation_keys_values_no_quotes(self):
        """暗号键和值不含任何引号字符"""
        path = _make_sensors_file(with_bom=True, with_annotation_row=True)
        try:
            _df, annotation, _meta = parse_enlight_file(path)
            assert len(annotation) > 0, "should have extracted annotations"
            for key, val in annotation.items():
                for q in _QUOTE_CHARS_TEST:
                    assert q not in key, \
                        f"annotation key {repr(key)} contains quote char {repr(q)}"
                    assert q not in val, \
                        f"annotation value {repr(val)} contains quote char {repr(q)}"
        finally:
            os.unlink(path)

    def test_annotation_content_correct(self):
        """暗号内容: Timestamp→时间戳, A1G1→应变-光纤1, A1G2→应变-光纤2"""
        path = _make_sensors_file(with_bom=True, with_annotation_row=True)
        try:
            _df, annotation, _meta = parse_enlight_file(path)
            assert annotation.get("Timestamp") == "时间戳", \
                f"got {annotation.get('Timestamp')}"
            assert annotation.get("A1G1") == "应变-光纤1", \
                f"got {annotation.get('A1G1')}"
            assert annotation.get("A1G2") == "应变-光纤2", \
                f"got {annotation.get('A1G2')}"
        finally:
            os.unlink(path)

    def test_sparse_annotation(self):
        """未标注列不在 annotation 字典中"""
        path = _make_sensors_file(with_bom=True, with_annotation_row=True)
        try:
            _df, annotation, _meta = parse_enlight_file(path)
            # A2G1, FBG_A1, FBG_A2 应该不在 annotation 中
            assert "A2G1" not in annotation, \
                f"unannotated col A2G1 should not be in annotation"
            assert "FBG_A1" not in annotation, \
                f"unannotated col FBG_A1 should not be in annotation"
            assert "FBG_A2" not in annotation, \
                f"unannotated col FBG_A2 should not be in annotation"
        finally:
            os.unlink(path)

    def test_no_annotation_row_when_none_present(self):
        """无暗号行的文件 → annotation = {}"""
        path = _make_sensors_file(with_bom=True, with_annotation_row=False)
        try:
            _df, annotation, _meta = parse_enlight_file(path)
            assert annotation == {}, \
                f"expected empty annotation, got {annotation}"
        finally:
            os.unlink(path)


class TestNumericPurity:
    """数值纯净性"""

    def test_non_timestamp_cols_are_float(self):
        """除 Timestamp 外所有列 dtype 为 float"""
        path = _make_sensors_file(with_bom=True, with_annotation_row=True)
        try:
            df, _annotation, _meta = parse_enlight_file(path)
            for col in df.columns:
                if col == "Timestamp":
                    continue
                assert df[col].dtype == "float64", \
                    f"{col} dtype is {df[col].dtype}, expected float64"
        finally:
            os.unlink(path)

    def test_annotation_strings_not_in_data(self):
        """暗号字符串不出现在数据行中"""
        path = _make_sensors_file(with_bom=True, with_annotation_row=True)
        try:
            df, _annotation, _meta = parse_enlight_file(path)
            for col in df.columns:
                vals = df[col].astype(str).values
                for v in vals:
                    assert "应变" not in v, \
                        f"annotation text '应变' leaked into data at {col}"
                    assert "时间戳" not in v, \
                        f"annotation text '时间戳' leaked into data at {col}"
        finally:
            os.unlink(path)

    def test_first_data_value_correct(self):
        """首行数据值正确 (不被暗号行污染)"""
        path = _make_sensors_file(with_bom=True, with_annotation_row=True)
        try:
            df, _annotation, _meta = parse_enlight_file(path)
            # 第一行数据: A1G1 = -0.637
            assert abs(df["A1G1"].iloc[0] - (-0.637)) < 0.01, \
                f"expected ~-0.637, got {df['A1G1'].iloc[0]}"
        finally:
            os.unlink(path)

    def test_data_row_count_preserved(self):
        """暗号行不影响数据行数"""
        path = _make_sensors_file(
            with_bom=True, with_annotation_row=True, data_rows=5,
        )
        try:
            df, _annotation, _meta = parse_enlight_file(path)
            assert len(df) == 5, \
                f"expected 5 data rows, got {len(df)}"
        finally:
            os.unlink(path)


class TestMixedQuotes:
    """混合引号剥离"""

    def test_all_quote_types_stripped(self):
        """直引号 + 弯单引号 + 弯双引号 全被剥离"""
        path = _make_sensors_file(
            with_bom=True, with_annotation_row=True, use_mixed_quotes=True,
        )
        try:
            _df, annotation, _meta = parse_enlight_file(path)
            # 直引号: '时间戳' → 时间戳
            assert annotation.get("Timestamp") == "时间戳", \
                f"straight quote: {repr(annotation.get('Timestamp'))}"
            # 弯单引号: '应变-光纤1' → 应变-光纤1
            assert annotation.get("A1G1") == "应变-光纤1", \
                f"curly single quote: {repr(annotation.get('A1G1'))}"
            # 弯双引号: "应变-光纤2" → 应变-光纤2
            assert annotation.get("A1G2") == "应变-光纤2", \
                f"curly double quote: {repr(annotation.get('A1G2'))}"
        finally:
            os.unlink(path)


class TestFormatDiscrimination:
    """格式判别正确性"""

    def test_sensors_format_in_meta(self):
        """Sensors 文件 meta.format == 'hyperion_sensors'"""
        path = _make_sensors_file(with_bom=True, with_annotation_row=False)
        try:
            _df, _annotation, meta = parse_enlight_file(path)
            assert meta["format"] == "hyperion_sensors", \
                f"expected hyperion_sensors, got {meta['format']}"
        finally:
            os.unlink(path)

    def test_sensors_not_misidentified_as_peaks(self):
        """Sensors 文件不被误判为 Peaks"""
        path = _make_sensors_file(with_bom=True, with_annotation_row=False)
        try:
            _df, _annotation, meta = parse_enlight_file(path)
            assert meta["format"] != "hyperion_peaks", \
                "Sensors should NOT be identified as peaks"
        finally:
            os.unlink(path)

    def test_no_bom_timestamp_goes_to_sensors(self):
        """无 BOM 但有 Timestamp\t 表头的文件 → Sensors (不再误入 Legacy)"""
        path = _make_sensors_file(with_bom=False, with_annotation_row=False)
        try:
            _df, _annotation, meta = parse_enlight_file(path)
            # ★ 修复后: 只要全文件内有 Timestamp\t 表头就进 Sensors
            assert meta["format"] == "hyperion_sensors", \
                f"expected hyperion_sensors, got {meta['format']}"
        finally:
            os.unlink(path)


class TestAnnotationRoundtrip:
    """暗号往返 smoke 测试"""

    def test_roundtrip_annotation_keys_match_columns(self):
        """暗号键与 DataFrame 列名一致"""
        path = _make_sensors_file(with_bom=True, with_annotation_row=True)
        try:
            df, annotation, _meta = parse_enlight_file(path)
            for key in annotation:
                assert key in df.columns, \
                    f"annotation key {repr(key)} not in DataFrame columns {list(df.columns)}"
        finally:
            os.unlink(path)


class TestRegression:
    """回归隔离"""

    def test_no_annotation_row_still_gets_correct_data_count(self):
        """无暗号行 → 不丢数据行"""
        path = _make_sensors_file(
            with_bom=True, with_annotation_row=False, data_rows=3,
        )
        try:
            df, annotation, _meta = parse_enlight_file(path)
            assert annotation == {}, "should be no annotation"
            assert len(df) == 3, f"expected 3 data rows, got {len(df)}"
        finally:
            os.unlink(path)

    def test_non_annotation_first_row_not_deleted(self):
        """首行是数据(非暗号)时不被删除"""
        path = _make_sensors_file(
            with_bom=True, with_annotation_row=False, data_rows=1,
        )
        try:
            df, _annotation, _meta = parse_enlight_file(path)
            assert len(df) == 1, \
                f"single data row should not be deleted, got {len(df)}"
            assert pd.notna(df["A1G1"].iloc[0]), \
                "data value should not be NaN"
        finally:
            os.unlink(path)


# ═══════════════════════════════════════════════════════════════════════
# 变体A: 原始仪器导出（元数据块/无BOM/无暗号行）
# ═══════════════════════════════════════════════════════════════════════


def _make_sensors_raw_export(
    *,
    num_meta_lines: int = 30,
    data_rows: int = 20,
) -> str:
    """构建带元数据块的 ENLIGHT Sensors 原始导出文件。

    布局：
      - 元数据块 (含 "ENLIGHT Version"、"Module Type: Hyperion"、
        "Timestamp Format: Full" (空格!)、CH 配置、FBG_ 定义行等)
      - Timestamp\tA1_1\t...\tC2_2\tFBG_A1\t...\tFBG_J2 表头 (有 \\t)
      - 直接数据行 (无暗号行，无计数列)
    """
    lines: list[str] = []
    # ── 元数据块 ──
    lines.append("ENLIGHT Version: 3.0")
    lines.append("Module Type: Hyperion")
    lines.append("Culture: Default")
    lines.append("Date: 2026/05/12")
    lines.append("Name: \t\t\t")
    lines.append("")
    lines.append("Probe Type: Temperature")
    # ★ 关键陷阱行: "Timestamp Format:" 用空格分隔, 不能匹配 "Timestamp\t"
    lines.append("Timestamp Format: Full")
    lines.append("")
    for i in range(1, 5):
        lines.append(
            f"CH {i} Configuration:\tThreshold=2.0\t"
            f"MinWavelength=1525.0\tMaxWavelength=1555.0"
        )
    # FBG_ 定义行 (元数据, 不是列)
    lines.append("FBG_A1: Wavelength=1525.0")
    lines.append("FBG_A2: Wavelength=1530.0")
    lines.append("FBG_B1: Wavelength=1535.0")
    # 填充到 meta_lines
    while len(lines) < num_meta_lines:
        lines.append(f"Extra meta line {len(lines)}")
    # 确保最后一行不是空行
    if not lines[-1]:
        lines[-1] = "End of metadata"

    # ── 表头 (有 \\t) ──
    data_cols = ["A1_1", "A1_2", "A2_1", "A2_2", "B1_1", "B1_2",
                 "B2_1", "B2_2", "C1_1", "C1_2", "C2_1", "C2_2"]
    fbg_cols = [f"FBG_{c}" for c in ["A1", "A2", "B1", "B2", "C1", "C2",
                                       "D1", "D2", "E1", "E2", "F1", "F2",
                                       "G1", "G2", "H1", "H2", "I1", "I2",
                                       "J1", "J2"]]
    all_cols = data_cols + fbg_cols
    header_line = "Timestamp\t" + "\t".join(all_cols)
    lines.append(header_line)

    # ── 数据行 (时间戳 + 数值) ──
    base_wl = 1525.0
    for t in range(data_rows):
        ts = f"2026/5/12 {t + 1:02d}:00:{t:02d}.00000"
        vals = []
        for ci in range(len(data_cols)):
            vals.append(f"{-0.5 + t * 0.01 + ci * 0.001:.5f}")
        for fi in range(len(fbg_cols)):
            vals.append(f"{base_wl + fi * 3 + t * 0.01:.5f}")
        lines.append(f"{ts}\t" + "\t".join(vals))

    text = "\n".join(lines)
    fd, path = tempfile.mkstemp(suffix=".txt", prefix="test_sensors_raw_")
    os.close(fd)
    with open(path, "w", encoding="utf-8", newline="") as f:
        f.write(text)
    return path


class TestRawExportWithMetadata:
    """变体A: 原始仪器导出 (元数据块/无BOM/无暗号行)"""

    def test_raw_export_header_idx_points_to_real_header(self):
        """header_idx 指向 Timestamp\t 行，不是第0行或 "Timestamp Format" 行"""
        path = _make_sensors_raw_export(data_rows=3)
        try:
            _df, _annotation, meta = parse_enlight_file(path)
            assert meta["format"] == "hyperion_sensors"
            header_idx = meta.get("header_idx", 0)
            assert header_idx > 0, \
                f"header_idx should not be 0 (metadata), got {header_idx}"
            assert header_idx >= 30, \
                f"header_idx should be >= 30 (after metadata), got {header_idx}"
        finally:
            os.unlink(path)

    def test_raw_export_has_data_rows(self):
        """行数 > 0, Timestamp 为真实时间戳"""
        path = _make_sensors_raw_export(data_rows=5)
        try:
            df, _annotation, meta = parse_enlight_file(path)
            assert len(df) == 5, f"expected 5 data rows, got {len(df)}"
            ts0 = str(df["Timestamp"].iloc[0])
            assert ":" in ts0, f"Timestamp should be real, got {ts0!r}"
            assert "/" in ts0, f"Timestamp should contain date, got {ts0!r}"
            assert "Format" not in ts0, \
                f"Timestamp must not be metadata text: {ts0!r}"
        finally:
            os.unlink(path)

    def test_raw_export_data_cols_are_float(self):
        """数据列 (非 Timestamp) dtype 为 float64"""
        path = _make_sensors_raw_export(data_rows=3)
        try:
            df, _annotation, _meta = parse_enlight_file(path)
            for col in df.columns:
                if col == "Timestamp":
                    continue
                assert df[col].dtype == "float64", \
                    f"{col} dtype is {df[col].dtype}, expected float64"
                assert pd.notna(df[col].iloc[0]), \
                    f"{col}: first value is NaN"
        finally:
            os.unlink(path)

    def test_raw_export_fbg_cols_identified(self):
        """meta['fbg_cols'] 列出所有 FBG_ 前缀列 (20个)"""
        path = _make_sensors_raw_export(data_rows=3)
        try:
            _df, _annotation, meta = parse_enlight_file(path)
            fbg_cols = meta.get("fbg_cols", [])
            assert len(fbg_cols) == 20, \
                f"expected 20 FBG_ cols, got {len(fbg_cols)}: {fbg_cols}"
            for c in fbg_cols:
                assert c.startswith("FBG_"), \
                    f"non-FBG col in fbg_cols: {c!r}"
        finally:
            os.unlink(path)

    def test_raw_export_annotation_empty(self):
        """无暗号行 → annotation = {}"""
        path = _make_sensors_raw_export(data_rows=3)
        try:
            _df, annotation, _meta = parse_enlight_file(path)
            assert annotation == {}, \
                f"raw export with no annotation row should have empty dict, got {annotation}"
        finally:
            os.unlink(path)

    def test_raw_export_detects_as_sensors_not_peaks(self):
        """原始导出应判为 hyperion_sensors，不是 Peaks 或 Legacy"""
        path = _make_sensors_raw_export(data_rows=3)
        try:
            _df, _annotation, meta = parse_enlight_file(path)
            assert meta["format"] == "hyperion_sensors"
            assert meta["format"] != "hyperion_peaks"
            assert meta["format"] != "legacy_enlight"
        finally:
            os.unlink(path)


class TestSavedSampleWithBomAndAnnotation:
    """变体B: 旧保存样本 (有BOM/元数据块/有暗号行) — 不回归"""

    def test_bom_sample_still_works(self):
        """BOM + 暗号行 → 仍正常解析, annotation 正确"""
        path = _make_sensors_file(
            with_bom=True, with_annotation_row=True, data_rows=3,
        )
        try:
            df, annotation, meta = parse_enlight_file(path)
            assert meta["format"] == "hyperion_sensors"
            assert len(df) == 3
            assert annotation.get("Timestamp") == "时间戳"
            assert annotation.get("A1G1") == "应变-光纤1"
        finally:
            os.unlink(path)

    def test_bom_sample_without_annotation_still_works(self):
        """BOM + 无暗号行 → 仍正常"""
        path = _make_sensors_file(
            with_bom=True, with_annotation_row=False, data_rows=5,
        )
        try:
            df, annotation, meta = parse_enlight_file(path)
            assert meta["format"] == "hyperion_sensors"
            assert len(df) == 5
            assert annotation == {}
        finally:
            os.unlink(path)


class TestPeaksMisroutedToSensors:
    """误投: Peaks 文件喂给 Sensors → 应该抛提示"""

    def test_peaks_file_rejected_by_sensors(self):
        """Peaks 格式 (Timestamp\t# CH 1...) 被 parse_enlight_sensors 直接调用时抛 ValueError"""
        # 构造 Peaks 表头 (带 # CH)
        lines: list[str] = []
        ch_header = "\t".join([f"# CH {i}" for i in range(1, 4)])
        lines.append(f"Timestamp\t{ch_header}")
        lines.append("01:00.0\t1\t0\t0\t1525.0")

        fd, path = tempfile.mkstemp(suffix=".txt", prefix="test_misroute2_")
        os.close(fd)
        with open(path, "w", encoding="utf-8", newline="") as f:
            f.write("\n".join(lines))
        try:
            from utils.file_parser import parse_enlight_sensors
            with pytest.raises(ValueError, match="Peaks|模板"):
                parse_enlight_sensors(path)
        finally:
            os.unlink(path)

    def test_peaks_file_dispatched_to_peaks_not_sensors(self):
        """有 Hyperion 元数据 + Peaks 表头 → dispatch 走 Peaks 分支"""
        lines: list[str] = []
        lines.append("ENLIGHT Version: 3.0")
        lines.append("Module Type: Hyperion")
        for i in range(1, 4):
            lines.append(f"CH {i} Configuration:\tThreshold=2.0")
        ch_header = "\t".join([f"# CH {i}" for i in range(1, 4)])
        lines.append(f"Timestamp\t{ch_header}")
        lines.append("01:00.0\t1\t0\t0\t1525.0")

        fd, path = tempfile.mkstemp(suffix=".txt", prefix="test_misroute3_")
        os.close(fd)
        with open(path, "w", encoding="utf-8", newline="") as f:
            f.write("\n".join(lines))
        try:
            # dispatch 层: is_hyperion=True, _find_peaks_header 非 None → Peaks
            _df, _annotation, meta = parse_enlight_file(path)
            # 此文件列数 < 1+_N_CH, 走矩形 Peaks 分支 → 所有 Peaks 子格式均可接受
            assert meta["format"] in ("hyperion_peaks", "hyperion_peaks_count", "hyperion_peaks_rect"), \
                f"should be a peaks variant, got {meta['format']}"
        finally:
            os.unlink(path)

    def test_minimal_peaks_file_rect_variant(self):
        """文件有 # CH 表头但列数不足 1+_N_CH → 走 _parse_peaks_rectangular 安全解析"""
        lines: list[str] = []
        lines.append("ENLIGHT Version: 3.0")
        lines.append("Module Type: Hyperion")
        for i in range(1, 3):
            lines.append(f"CH {i} Configuration:\tThreshold=2.0")
        ch_header = "\t".join([f"# CH {i}" for i in range(1, 3)])
        lines.append(f"Timestamp\t{ch_header}")
        lines.append("01:00.0\t1525.0\t1530.0")

        fd, path = tempfile.mkstemp(suffix=".txt", prefix="test_minipeaks_")
        os.close(fd)
        with open(path, "w", encoding="utf-8", newline="") as f:
            f.write("\n".join(lines))
        try:
            df, _annotation, meta = parse_enlight_file(path)
            assert meta["format"] == "hyperion_peaks_rect"
            assert len(df) == 1
            assert "Timestamp" in df.columns
        finally:
            os.unlink(path)


# ═══════════════════════════════════════════════════════════════════════
# 硬断言: 元数据行当表头 → 抛出 ValueError
# ═══════════════════════════════════════════════════════════════════════


class TestHardAssertions:
    """硬断言 — 杜绝静默失败"""

    def test_bad_col_metadata_as_header_raises(self):
        """列名含 '(FBG):' → raise ValueError (元数据行被当了表头)"""
        # 构造一个在"表头"行(唯一 Timestamp\t 行)放 FBG 定义元数据的畸形文件
        lines: list[str] = []
        for i in range(250):
            lines.append(f"Extra metadata {i}")  # 大量元数据行 → 触发 len(lines) > 200 守卫
        # 假表头: 含 (FBG): 的元数据
        lines.append("Timestamp\tFBG_A1: (FBG): Wavelength=1525.0\tFBG_A2")
        lines.append("01:00.0\t1525.0\t1530.0")

        fd, path = tempfile.mkstemp(suffix=".txt", prefix="test_badcol_")
        os.close(fd)
        with open(path, "w", encoding="utf-8", newline="") as f:
            f.write("\n".join(lines))
        try:
            with pytest.raises(ValueError, match="表头定位错误|元数据"):
                parse_enlight_file(path)
        finally:
            os.unlink(path)

    def test_few_rows_large_file_raises(self):
        """大文件(>200行) 但仅解析出极少行 → raise ValueError"""
        lines: list[str] = []
        for i in range(250):
            lines.append(f"Extra metadata {i}")
        # 真表头在很后面
        lines.append("Timestamp\tCol1\tCol2")
        lines.append("01:00.0\t1.0\t2.0")  # 仅 1 行数据
        lines.append("Metadata footer 1")
        lines.append("Metadata footer 2")

        fd, path = tempfile.mkstemp(suffix=".txt", prefix="test_fewrows_")
        os.close(fd)
        with open(path, "w", encoding="utf-8", newline="") as f:
            f.write("\n".join(lines))
        try:
            with pytest.raises(ValueError, match="仅解析出"):
                parse_enlight_file(path)
        finally:
            os.unlink(path)
