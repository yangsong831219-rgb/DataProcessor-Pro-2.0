"""core/chart_bundle.py Batch A 合成数据测试 — 表格替换空白/崩坏图

覆盖:
- G1 分级表: grade_table_md 含传感器/残余σ/迟滞/评级/通过/原因列
- A1 异常表: anomaly_table_md 含通道/异常点数/总索引数/占比列
- S2 Ke表: ke_table_md 含传感器/模式/Ke1/Ke2/R²/备注列
- T5 解耦表: decoupling_table_md 含传感器/ε_mean/ε_std/ε_range/评级列
- 多表整合: gen_markdown_tables_from_bundle 拼出所有非空表
- 表格注入 docx: markdown 表有 pipe 格式 + 表头分隔行
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

import numpy as np
import pandas as pd
import pytest

from core.chart_bundle import (
    ChartBundle, gen_markdown_tables_from_bundle,
    extract_four_tables, TableData, _parse_single_md_table,
    _safe_float, _rows_to_md_table,
)


# ═══════════════════════════════════════════════════════════════════════════
# _safe_float / _rows_to_md_table 单元测试
# ═══════════════════════════════════════════════════════════════════════════

class TestSafeFloat:
    def test_numeric(self):
        assert _safe_float(3.14) == 3.14
        assert _safe_float(0) == 0.0
        assert _safe_float(-5) == -5.0

    def test_none(self):
        assert _safe_float(None) is None

    def test_non_numeric(self):
        assert _safe_float("abc") is None
        assert _safe_float({}) is None


class TestRowsToMdTable:
    def test_simple(self):
        md = _rows_to_md_table([
            ["A", "B"],
            ["1", "2"],
            ["3", "4"],
        ])
        lines = md.split("\n")
        assert len(lines) == 4  # header + sep + 2 data
        assert lines[0] == "| A | B |"
        assert "---" in lines[1]

    def test_empty(self):
        assert _rows_to_md_table([]) == ""

    def test_single_header_only(self):
        md = _rows_to_md_table([["X"]])
        assert "| X |" in md
        assert "---" in md


# ═══════════════════════════════════════════════════════════════════════════
# Mock objects
# ═══════════════════════════════════════════════════════════════════════════

class MockAnalysisTabWidget:
    def __init__(self, df=None):
        self._current_data = df


class MockCleaningTabWidget:
    def __init__(self, anomaly_info=None, has_run: bool | None = None):
        self._anomaly_info = anomaly_info or {}
        self._cleaning_has_run = bool(anomaly_info) if has_run is None else has_run

    def get_config(self):
        return {"fill_method": "linear"}


class MockTempPage:
    def __init__(self, phase_b_state=None):
        self._phase_b_state = phase_b_state or {}


@dataclass
class MockStrainSubConfig:
    sensor_name: str = "A1"
    sensor_mode: str = "dual_working"
    gauge_length_mm: float = 80.0
    readings: list[dict] = field(default_factory=list)
    ke_results: dict[str, float] = field(default_factory=dict)


class MockStrainPage:
    def __init__(self, strain_configs=None):
        self._strain_configs = strain_configs or {}


class MockCalibrationTabWidget:
    def __init__(self, temp_page=None, strain_page=None):
        self.temp_page = temp_page
        self.strain_page = strain_page
        self.project_config = None


class MockMainWindow:
    def __init__(self, current_data=None, analysis_tab_widget=None,
                 cleaning_tab_widget=None, calibration_tab_widget=None,
                 compare_tab_widget=None):
        self.current_data = current_data
        self.sensor_results = {}
        self.analysis_tab_widget = analysis_tab_widget
        self.cleaning_tab_widget = cleaning_tab_widget
        self.calibration_tab_widget = calibration_tab_widget
        self.compare_tab_widget = compare_tab_widget


# ═══════════════════════════════════════════════════════════════════════════
# fixtures
# ═══════════════════════════════════════════════════════════════════════════

@pytest.fixture
def sample_df():
    rng = np.random.default_rng(42)
    return pd.DataFrame({
        "时间": np.arange(100) / 10.0,
        "A1_应变(με)": rng.normal(100, 5, 100),
    })


@pytest.fixture
def sample_anomaly_info():
    return {
        "A1_应变(με)": {"count": 5, "indices": [10, 20, 30, 40, 50], "total_indices": 100},
        "A2_应变(με)": {"count": 3, "indices": [15, 25, 35], "total_indices": 100},
        "温度(℃)": {"count": 0, "indices": [], "total_indices": 100},
    }


@pytest.fixture
def sample_compensation():
    return {
        "A1": {
            "metrics": type("M", (), {
                "residual_sigma_pct_fs": 0.85,
                "hysteresis_max_pct_fs": 1.2,
                "fs": 1000,
            })(),
            "grade": type("G", (), {
                "grade": "优", "passed": True, "reasons": [],
            })(),
        },
        "A2": {
            "metrics": type("M", (), {
                "residual_sigma_pct_fs": 1.5,
                "hysteresis_max_pct_fs": 2.8,
                "fs": 1000,
            })(),
            "grade": type("G", (), {
                "grade": "良", "passed": True, "reasons": [],
            })(),
        },
        "B1": {
            "metrics": type("M", (), {
                "residual_sigma_pct_fs": 5.5,
                "hysteresis_max_pct_fs": 6.2,
                "fs": 1000,
            })(),
            "grade": type("G", (), {
                "grade": "FAIL", "passed": False,
                "reasons": ["迟滞超标(>5%FS)"],
            })(),
        },
    }


@pytest.fixture
def sample_decoupling():
    return {
        "A1": {"e_mean": 1.5, "e_std": 8.2, "e_range": 35.0, "rating": "优"},
        "A2": {"e_mean": -2.3, "e_std": 15.5, "e_range": 68.0, "rating": "良"},
    }


@pytest.fixture
def sample_strain_configs():
    return {
        "A1": MockStrainSubConfig(
            sensor_name="A1", sensor_mode="dual_working",
            gauge_length_mm=80.0,
            readings=[
                {"disp_mm": 0.0}, {"disp_mm": 0.01}, {"disp_mm": 0.02},
                {"disp_mm": 0.05}, {"disp_mm": 0.10},
            ],
            ke_results={"Ke1": 1.234, "Ke2": 1.221},
        ),
        "A2": MockStrainSubConfig(
            sensor_name="A2", sensor_mode="single",
            gauge_length_mm=100.0,
            readings=[
                {"disp_mm": 0.0}, {"disp_mm": 0.02}, {"disp_mm": 0.05},
            ],
            ke_results={"Ke1": 0.987},
        ),
    }


# ═══════════════════════════════════════════════════════════════════════════
# G1 分级表
# ═══════════════════════════════════════════════════════════════════════════

class TestGradeTable:
    def test_grade_table_content(self, sample_df, sample_compensation):
        tp = MockTempPage({"compensation": sample_compensation})
        ct = MockCalibrationTabWidget(temp_page=tp)
        atw = MockAnalysisTabWidget(df=sample_df)
        mw = MockMainWindow(current_data=sample_df, analysis_tab_widget=atw,
                            calibration_tab_widget=ct)

        bundle = ChartBundle.from_providers(mw)

        assert bundle.grade_table_md, "分级表不应为空"
        md = bundle.grade_table_md
        # 表头
        assert "传感器" in md
        assert "残余σ" in md
        assert "迟滞" in md
        assert "评级" in md
        assert "通过" in md
        # 数据
        assert "A1" in md
        assert "A2" in md
        assert "B1" in md
        assert "0.85" in md  # sigma
        assert "6.20" in md  # hyst
        assert "优" in md
        assert "FAIL" in md
        # 通过列
        assert "✓" in md
        assert "✗" in md
        # pipe 格式
        lines = md.strip().split("\n")
        assert lines[0].startswith("|")
        assert "---" in lines[1]

    def test_no_compensation_grade_table_empty(self, sample_df):
        tp = MockTempPage({})  # no compensation
        ct = MockCalibrationTabWidget(temp_page=tp)
        atw = MockAnalysisTabWidget(df=sample_df)
        mw = MockMainWindow(current_data=sample_df, analysis_tab_widget=atw,
                            calibration_tab_widget=ct)

        bundle = ChartBundle.from_providers(mw)
        assert bundle.grade_table_md == ""


# ═══════════════════════════════════════════════════════════════════════════
# A1 异常统计表
# ═══════════════════════════════════════════════════════════════════════════

class TestAnomalyTable:
    def test_anomaly_table_content(self, sample_df, sample_anomaly_info):
        atw = MockAnalysisTabWidget(df=sample_df)
        clw = MockCleaningTabWidget(anomaly_info=sample_anomaly_info)
        mw = MockMainWindow(current_data=sample_df, analysis_tab_widget=atw,
                            cleaning_tab_widget=clw)

        bundle = ChartBundle.from_providers(mw)

        assert bundle.anomaly_table_md, "异常表不应为空"
        md = bundle.anomaly_table_md
        assert "通道" in md
        assert "异常点数" in md
        assert "总索引数" in md
        assert "占比" in md
        assert "5" in md  # count for A1
        assert "3" in md  # count for A2
        # A1: 5/100 = 5.0%
        assert "5.0" in md

    def test_no_anomaly_data_empty(self, sample_df):
        atw = MockAnalysisTabWidget(df=sample_df)
        clw = MockCleaningTabWidget(anomaly_info={})
        mw = MockMainWindow(current_data=sample_df, analysis_tab_widget=atw,
                            cleaning_tab_widget=clw)

        bundle = ChartBundle.from_providers(mw)
        assert bundle.anomaly_table_md == ""

    def test_no_cleaning_tab_empty(self, sample_df):
        atw = MockAnalysisTabWidget(df=sample_df)
        mw = MockMainWindow(current_data=sample_df, analysis_tab_widget=atw)

        bundle = ChartBundle.from_providers(mw)
        assert bundle.anomaly_table_md == ""

    def test_cleaning_statistics_table_exists_when_zero_anomalies(self, sample_df):
        atw = MockAnalysisTabWidget(df=sample_df)
        clw = MockCleaningTabWidget(anomaly_info={}, has_run=True)
        mw = MockMainWindow(
            current_data=sample_df,
            analysis_tab_widget=atw,
            cleaning_tab_widget=clw,
        )

        bundle = ChartBundle.from_providers(mw)

        assert bundle.anomaly_table_md == ""
        assert bundle.cleaning_table_md
        assert "有效数" in bundle.cleaning_table_md
        assert "缺失数" in bundle.cleaning_table_md
        assert "异常点数" in bundle.cleaning_table_md
        assert "填充方式" in bundle.cleaning_table_md

    def test_cleaning_statistics_accepts_object_columns_with_annotation_row(
        self, sample_df
    ):
        """主表含暗号行时 dtype 会整体变 object，统计表仍必须识别数值通道。"""
        annotated = sample_df.astype(object)
        annotation = {
            column: ("时间" if column == "Timestamp" else "波长")
            for column in annotated.columns
        }
        annotated = pd.concat(
            [pd.DataFrame([annotation]), annotated],
            ignore_index=True,
        )
        atw = MockAnalysisTabWidget(df=sample_df)
        clw = MockCleaningTabWidget(anomaly_info={}, has_run=True)
        mw = MockMainWindow(
            current_data=annotated,
            analysis_tab_widget=atw,
            cleaning_tab_widget=clw,
        )

        bundle = ChartBundle.from_providers(mw)

        assert bundle.cleaning_table_md
        assert "A1_应变(με)" in bundle.cleaning_table_md


# ═══════════════════════════════════════════════════════════════════════════
# S2 应变 Ke 汇总表
# ═══════════════════════════════════════════════════════════════════════════

class TestKeTable:
    def test_ke_table_content(self, sample_df, sample_strain_configs):
        atw = MockAnalysisTabWidget(df=sample_df)
        sp = MockStrainPage(strain_configs=sample_strain_configs)
        ct = MockCalibrationTabWidget(strain_page=sp)
        mw = MockMainWindow(current_data=sample_df, analysis_tab_widget=atw,
                            calibration_tab_widget=ct)

        bundle = ChartBundle.from_providers(mw)

        assert bundle.ke_table_md, "Ke表不应为空"
        md = bundle.ke_table_md
        assert "传感器" in md
        assert "Ke1" in md
        assert "Ke2" in md
        assert "A1" in md
        assert "A2" in md
        assert "1.234" in md
        assert "0.987" in md
        assert "dual_working" in md
        assert "single" in md
        # pipe 格式
        lines = md.strip().split("\n")
        assert lines[0].startswith("|")

    def test_no_strain_configs_empty(self, sample_df):
        atw = MockAnalysisTabWidget(df=sample_df)
        ct = MockCalibrationTabWidget()
        mw = MockMainWindow(current_data=sample_df, analysis_tab_widget=atw,
                            calibration_tab_widget=ct)

        bundle = ChartBundle.from_providers(mw)
        assert bundle.ke_table_md == ""


# ═══════════════════════════════════════════════════════════════════════════
# T5 温度B 解耦标量表
# ═══════════════════════════════════════════════════════════════════════════

class TestDecouplingTable:
    def test_decoupling_table_content(self, sample_df, sample_decoupling):
        tp = MockTempPage({"decoupling_results": sample_decoupling})
        ct = MockCalibrationTabWidget(temp_page=tp)
        atw = MockAnalysisTabWidget(df=sample_df)
        mw = MockMainWindow(current_data=sample_df, analysis_tab_widget=atw,
                            calibration_tab_widget=ct)

        bundle = ChartBundle.from_providers(mw)

        assert bundle.decoupling_table_md, "解耦表不应为空"
        md = bundle.decoupling_table_md
        assert "ε_mean" in md
        assert "ε_std" in md
        assert "ε_range" in md
        assert "评级" in md
        assert "1.50" in md or "1.5" in md
        assert "8.2" in md or "8.20" in md
        assert "35.00" in md or "35.0" in md

    def test_no_decoupling_empty(self, sample_df):
        tp = MockTempPage({})
        ct = MockCalibrationTabWidget(temp_page=tp)
        atw = MockAnalysisTabWidget(df=sample_df)
        mw = MockMainWindow(current_data=sample_df, analysis_tab_widget=atw,
                            calibration_tab_widget=ct)

        bundle = ChartBundle.from_providers(mw)
        assert bundle.decoupling_table_md == ""


# ═══════════════════════════════════════════════════════════════════════════
# gen_markdown_tables_from_bundle 整合
# ═══════════════════════════════════════════════════════════════════════════

class TestGenMarkdownTables:
    def test_all_tables_included(self):
        cd = {
            "grade_table_md": "| A | B |\n|---|---|\n| 1 | 2 |",
            "decoupling_table_md": "| X | Y |\n|---|---|\n| a | b |",
            "ke_table_md": "",
            "anomaly_table_md": "| Ch | N |\n|---|---|\n| C1 | 5 |",
        }
        result = gen_markdown_tables_from_bundle(cd)
        assert "### 传感器分级汇总" in result
        assert "### 温度标定" in result
        assert "### 应变标定" not in result  # ke_table_md is empty → skipped
        assert "### 异常统计" in result

    def test_empty_all(self):
        cd = {}
        result = gen_markdown_tables_from_bundle(cd)
        assert result == ""

    def test_pipe_format(self):
        cd = {
            "grade_table_md": "| X |\n|---|\n| 1 |",
        }
        result = gen_markdown_tables_from_bundle(cd)
        # 每表前后有空行
        assert "\n\n" in result
        # 含 heading
        assert "###" in result


# ═══════════════════════════════════════════════════════════════════════════
# _parse_single_md_table 单元
# ═══════════════════════════════════════════════════════════════════════════

class TestParseSingleMdTable:
    def test_normal_table(self):
        md = "| A | B |\n|---|---|\n| 1 | 2 |"
        result = _parse_single_md_table(md)
        assert result is not None
        headers, rows = result
        assert headers == ['A', 'B']
        assert rows == [['1', '2']]

    def test_with_alignment_separator(self):
        md = "| X | Y | Z |\n|:---|:---:|---:|\n| a | b | c |\n| d | e | f |"
        result = _parse_single_md_table(md)
        assert result is not None
        headers, rows = result
        assert headers == ['X', 'Y', 'Z']
        assert len(rows) == 2
        assert rows[1] == ['d', 'e', 'f']

    def test_no_separator_returns_none(self):
        md = "| A | B |\n| 1 | 2 |"  # 缺分隔行
        assert _parse_single_md_table(md) is None

    def test_header_only_no_data_returns_none(self):
        md = "| A | B |\n|---|---|"
        assert _parse_single_md_table(md) is None

    def test_empty_string(self):
        assert _parse_single_md_table("") is None

    def test_extra_spaces_and_padding(self):
        md = "|  col1  |  col2  |\n|--------|--------|\n|  v1  |  v2  |"
        result = _parse_single_md_table(md)
        assert result is not None
        headers, rows = result
        assert headers == ['col1', 'col2']
        assert rows == [['v1', 'v2']]


# ═══════════════════════════════════════════════════════════════════════════
# extract_four_tables 集成 — 四表齐 / 部分空 / 全空
# ═══════════════════════════════════════════════════════════════════════════

_GRADE_MD = "| 传感器 | σ(%FS) | 评级 |\n|---|---|---|\n| S1 | 0.05 | 优 |"
_DEC_MD = "| 传感器 | ε_mean | ε_std |\n|---|---|---|\n| T1 | 1.23 | 0.45 |"
_KE_MD = "| 传感器 | Ke1 | Ke2 |\n|---|---|---|\n| K1 | 0.8 | 0.9 |"
_ANOM_MD = "| 通道 | 异常点数 | 占比 |\n|---|---|---|\n| C1 | 5 | 1.2% |"


class TestExtractFourTables:
    def test_all_four_tables_present(self):
        cd = {
            "grade_table_md": _GRADE_MD,
            "decoupling_table_md": _DEC_MD,
            "ke_table_md": _KE_MD,
            "anomaly_table_md": _ANOM_MD,
        }
        result = extract_four_tables(cd)
        assert len(result) == 4
        headings = [t.heading for t in result]
        assert "传感器分级汇总" in headings
        assert "温度标定 — 解耦诊断标量" in headings
        assert "应变标定 — Ke 系数汇总" in headings
        assert "异常统计" in headings

    def test_partial_empty_some_tables(self):
        cd = {
            "grade_table_md": _GRADE_MD,
            "decoupling_table_md": "",  # 空
            "ke_table_md": _KE_MD,
            "anomaly_table_md": "",  # 空
        }
        result = extract_four_tables(cd)
        assert len(result) == 2
        headings = [t.heading for t in result]
        assert "传感器分级汇总" in headings
        assert "应变标定 — Ke 系数汇总" in headings

    def test_all_empty_returns_empty_list(self):
        cd = {}
        result = extract_four_tables(cd)
        assert result == []

    def test_none_values_treated_as_empty(self):
        cd = {"grade_table_md": None, "anomaly_table_md": None}
        result = extract_four_tables(cd)
        assert result == []

    def test_malformed_md_gracefully_omitted(self):
        cd = {
            "grade_table_md": "| A | B |\n| 1 | 2 |",  # 缺分隔行 → None
        }
        result = extract_four_tables(cd)
        assert result == []

    def test_header_only_table_omitted(self):
        cd = {
            "anomaly_table_md": "| Ch | N |\n|---|---|",  # 仅表头, 无数据
        }
        result = extract_four_tables(cd)
        assert result == []

    def test_output_structure_has_required_fields(self):
        cd = {"grade_table_md": _GRADE_MD}
        result = extract_four_tables(cd)
        assert len(result) == 1
        tbl = result[0]
        assert isinstance(tbl.heading, str)
        assert isinstance(tbl.headers, list)
        assert isinstance(tbl.rows, list)
        assert all(isinstance(r, list) for r in tbl.rows)
        assert len(tbl.headers) == 3
        assert len(tbl.rows) == 1

    def test_heading_has_no_markdown_prefix(self):
        """extract_four_tables 的 heading 是纯文本, 不含 '###' — 由 caller 决定格式."""
        cd = {"grade_table_md": _GRADE_MD}
        result = extract_four_tables(cd)
        assert "###" not in result[0].heading

    def test_equivalence_to_gen_markdown_tables(self):
        """验证 extract_four_tables + _rows_to_md_table 重生成 = 原 markdown 内容等价."""
        cd = {
            "grade_table_md": _GRADE_MD,
            "decoupling_table_md": _DEC_MD,
        }
        # 旧路径
        old_md = gen_markdown_tables_from_bundle(cd)
        # 新路径: extract → 重建 markdown
        four = extract_four_tables(cd)
        parts = []
        for tbl in four:
            md = _rows_to_md_table([tbl.headers] + tbl.rows)
            parts.append(f"\n### {tbl.heading}\n\n{md}\n")
        new_md = "\n".join(parts)
        # 两者应包含相同的 heading 和表格内容
        for heading_text in ["传感器分级汇总", "解耦诊断标量"]:
            assert heading_text in old_md
            assert heading_text in new_md
        # 两者应包含相同的单元格内容
        assert "S1" in new_md
        assert "T1" in new_md


# ═══════════════════════════════════════════════════════════════════════════
# 全场景: 四表全在 (synthetic 模拟真数据)
# ═══════════════════════════════════════════════════════════════════════════

class TestFullScenario:
    def test_all_four_tables_populated(self, sample_df, sample_anomaly_info,
                                        sample_compensation, sample_decoupling,
                                        sample_strain_configs):
        tp = MockTempPage({
            "compensation": sample_compensation,
            "decoupling_results": sample_decoupling,
        })
        sp = MockStrainPage(strain_configs=sample_strain_configs)
        ct = MockCalibrationTabWidget(temp_page=tp, strain_page=sp)
        atw = MockAnalysisTabWidget(df=sample_df)
        clw = MockCleaningTabWidget(anomaly_info=sample_anomaly_info)
        mw = MockMainWindow(current_data=sample_df, analysis_tab_widget=atw,
                            cleaning_tab_widget=clw,
                            calibration_tab_widget=ct)

        bundle = ChartBundle.from_providers(mw)

        # 所有四张表都非空
        assert bundle.grade_table_md, "G1 分级表为空"
        assert bundle.anomaly_table_md, "A1 异常表为空"
        assert bundle.ke_table_md, "S2 Ke表为空"
        assert bundle.decoupling_table_md, "T5 解耦表为空"

        # 整合 markdown
        cd = bundle.to_dict()
        tables_md = gen_markdown_tables_from_bundle(cd)
        assert "传感器分级汇总" in tables_md
        assert "解耦诊断标量" in tables_md
        assert "Ke 系数汇总" in tables_md
        assert "异常统计" in tables_md

        # 所有表都是 pipe 格式
        for heading_key in ["grade_table_md", "anomaly_table_md",
                            "ke_table_md", "decoupling_table_md"]:
            md = cd.get(heading_key, "")
            if md:
                lines = md.strip().split("\n")
                assert lines[0].startswith("|"), f"{heading_key} 首行不是 pipe"
                assert "---" in lines[1], f"{heading_key} 缺表头分隔行"
                # 无裸 LaTeX
                assert "$$" not in md, f"{heading_key} 含 $$"
                assert "\\frac" not in md, f"{heading_key} 含 \\frac"


# ═══════════════════════════════════════════════════════════════════════
# 表格注入 log: 从 four_tables 构造诊断 print，不依赖 bundle
# (regression: 修复 main.py _build_and_save 内 UnboundLocalError)
# ═══════════════════════════════════════════════════════════════════════


class TestAppendixInjectionLog:
    """four_tables → 诊断 log 字符串 (不含 bundle 引用)"""

    def test_log_from_four_tables_no_bundle_needed(self):
        """extract_four_tables(cd) 产出含 .heading 的 TableData，
           可直接构造 log 字符串，不依赖 bundle 变量。"""
        from core.chart_bundle import extract_four_tables, TableData

        cd = {
            "grade_table_md": _GRADE_MD,
            "decoupling_table_md": _DEC_MD,
            "ke_table_md": _KE_MD,
            "anomaly_table_md": "",  # 空 — 省略
        }
        four_tables = extract_four_tables(cd)
        assert len(four_tables) == 3  # grade + dec + ke

        # 模拟修复后的 log 构造 (不再引用 bundle)
        log = (f"[报告] 已注入数据汇总附表 (表数={len(four_tables)}, "
               f"标题={[t.heading for t in four_tables]})")
        assert "表数=3" in log
        assert "传感器分级汇总" in log
        assert "Ke 系数汇总" in log
        assert "bundle" not in log  # ← 关键: 零 bundle 引用

    def test_empty_four_tables_log(self):
        """cd 全空 → four_tables=[] → 打印跳过而非崩"""
        from core.chart_bundle import extract_four_tables
        cd = {}
        four_tables = extract_four_tables(cd)
        assert four_tables == []

    def test_table_data_has_heading_attr(self):
        """TableData.heading 字段存在且为 str"""
        from core.chart_bundle import extract_four_tables, TableData
        cd = {"grade_table_md": _GRADE_MD}
        tables = extract_four_tables(cd)
        assert len(tables) == 1
        assert isinstance(tables[0].heading, str)
        assert len(tables[0].heading) > 0
