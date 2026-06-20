"""报告引擎消费已存诊断 JSON — _build_diagnosis_summary + 注入 _build_context."""

from __future__ import annotations
import json
import pytest


DIAG_RECORD_V1_0 = {
    "schema_version": "1.0",
    "timestamp": "2026-01-01 00:00:00",
    "backend": "local", "model": "qwen3.5-9b",
    "selected_sources": ["data_file", "calibration"],
    "data_source_snapshot": ["【数据文件】..."],
    "kb_hits": [
        {
            "id": "hys_fail", "severity": "high",
            "objects": ["B1", "B2", "C1"],
            "meaning": "升降温读数不重合，超废品阈值",
            "mechanism": "封装/胶层不可逆能量耗散",
            "recommendation": "复查封装与粘贴工艺；必要时报废重做",
        },
        {
            "id": "sigma_good_but_hys_fail", "severity": "high",
            "objects": ["C1"],
            "meaning": "补偿拟合精度很好但迟滞废品——拟合准不等于器件可用",
            "mechanism": "σ衡量贴合程度，迟滞衡量不可逆性；二者正交",
            "recommendation": "不要被低σ误导，按迟滞结论处理",
        },
        {
            "id": "pass_excellent", "severity": "info",
            "objects": ["A1", "C2"],
            "meaning": "补偿精度与迟滞均在良好范围，传感器可用",
            "mechanism": "封装/解耦/补偿各环节正常",
            "recommendation": "可投入使用，按常规周期复标",
        },
    ],
    "diagnosis_json": {
        "sensor_analysis": [
            {"sensor_id": "C1", "status": "critical",
             "findings": "迟滞超标(5.333%FS)，但补偿拟合精度很好(σ=0.784%FS)",
             "suggestions": "不要被低σ误导，按迟滞结论处理"},
            {"sensor_id": "B1", "status": "critical",
             "findings": "迟滞严重超标(14.777%FS)，补偿残余也大",
             "suggestions": "排查封装工艺一致性"},
            {"sensor_id": "A1", "status": "normal",
             "findings": "补偿精度与迟滞均在良好范围",
             "suggestions": "可投入使用"},
        ],
        "physical_diagnosis": {
            "phenomenon": "胶层蠕变导致不可逆耗散",
            "severity": "high",
            "possible_causes": ["低温段胶层模量突变", "封装工艺一致性差"],
            "recommended_actions": ["隔离不合格传感器"],
        },
    },
}
DIAG_RECORD = {
    "schema_version": "1.1",
    "timestamp": "2026-01-01 00:00:00",
    "backend": "local", "model": "qwen3.5-9b",
    "selected_sources": ["data_file", "calibration"],
    "data_source_snapshot": ["【数据文件】..."],
    "ai_diagnosis": {
        "diagnosis_json": DIAG_RECORD_V1_0["diagnosis_json"],
        "diagnosis_raw": "raw JSON text here (should NOT appear in summary)",
    },
    "multi_agent": {},
    "kb_hits": DIAG_RECORD_V1_0["kb_hits"],
}


class TestDiagnosisSummary:

    def test_compact_summary_has_sensors(self):
        from core.report_engine import _build_diagnosis_summary
        s = _build_diagnosis_summary(DIAG_RECORD_V1_0)
        assert "C1" in s
        assert "迟滞超标" in s
        assert "0.784%FS" in s
        assert "B1" in s
        assert "A1" in s

    def test_compact_summary_has_kb_rules(self):
        from core.report_engine import _build_diagnosis_summary
        s = _build_diagnosis_summary(DIAG_RECORD_V1_0)
        assert "hys_fail" in s
        assert "sigma_good_but_hys_fail" in s
        assert "pass_excellent" in s
        assert "诊断规则命中" in s

    def test_no_raw_text_leaked(self):
        from core.report_engine import _build_diagnosis_summary
        s = _build_diagnosis_summary(DIAG_RECORD)
        assert "diagnosis_raw" not in s  # should not dump raw field name
        assert "raw JSON text here" not in s.lower()

    def test_within_budget(self):
        from core.report_engine import _build_diagnosis_summary
        s = _build_diagnosis_summary(DIAG_RECORD_V1_0, max_chars=2500)
        assert len(s) <= 2600  # small margin for truncated marker

    def test_empty_record(self):
        from core.report_engine import _build_diagnosis_summary
        s = _build_diagnosis_summary({})
        assert len(s) > 0
        assert "诊断数据" in s

    def test_null_fields_handled(self):
        from core.report_engine import _build_diagnosis_summary
        rec = {"schema_version": "1.0", "diagnosis_json": {}}
        s = _build_diagnosis_summary(rec)
        assert len(s) > 0
        assert "诊断数据" in s


class TestBuildContextWithDiagnosis:

    def test_context_includes_diagnosis(self):
        from core.report_engine import _build_context
        config = {"_diagnosis_record": DIAG_RECORD_V1_0}
        ctx = _build_context(config)
        assert "C1" in ctx
        assert "hys_fail" in ctx
        assert "不要被低σ误导" in ctx

    def test_context_with_1_1_schema(self):
        """schema 1.1 (ai_diagnosis 段) 也被正确处理。"""
        from core.report_engine import _build_context
        config = {"_diagnosis_record": DIAG_RECORD}
        ctx = _build_context(config)
        assert "C1" in ctx
        assert "hys_fail" in ctx

    def test_context_without_diagnosis_still_works(self):
        from core.report_engine import _build_context
        config = {"req_file": ""}
        ctx = _build_context(config)
        assert "诊断" not in ctx

    def test_context_with_both_req_and_diag(self):
        from core.report_engine import _build_context
        config = {"_diagnosis_record": DIAG_RECORD_V1_0, "req_file": "/nonexistent.txt"}
        ctx = _build_context(config)
        assert "C1" in ctx
        assert "未提供具体需求文件" not in ctx  # diag provides context

    def test_context_no_dump_of_raw_text(self):
        from core.report_engine import _build_context
        config = {"_diagnosis_record": DIAG_RECORD}
        ctx = _build_context(config)
        # Should NOT contain the full raw diagnosis JSON
        assert len(ctx) < 5000  # compact, not 11k


class TestSchemaValidation:

    def test_unknown_schema_still_works(self):
        """非标准 schema 也不应崩（UI 层校验 schema_version）。"""
        rec = {"schema_version": "0.9", "ai_diagnosis": {"diagnosis_json": {}}}
        from core.report_engine import _build_diagnosis_summary
        s = _build_diagnosis_summary(rec)
        assert len(s) > 0

    def test_missing_schema_version(self):
        rec = {"ai_diagnosis": {"diagnosis_json": {}}}
        from core.report_engine import _build_diagnosis_summary
        s = _build_diagnosis_summary(rec)
        assert len(s) > 0  # survived

    def test_non_dict_diagnosis_json(self):
        rec = {"ai_diagnosis": {"diagnosis_json": "not-a-dict"}}
        from core.report_engine import _build_diagnosis_summary
        s = _build_diagnosis_summary(rec)
        assert len(s) > 0  # survived

    def test_schema_1_1_both_sections(self):
        """schema 1.1: 含 ai_diagnosis 和 multi_agent 两段，摘要应包含两段内容。"""
        rec = {
            "schema_version": "1.1",
            "ai_diagnosis": {
                "diagnosis_json": {
                    "sensor_analysis": [
                        {"sensor_id": "C1", "findings": "迟滞超标", "suggestions": "整改"},
                    ],
                },
            },
            "multi_agent": {"report": "# 多智能体审查报告\n\n裁决: pass\n\n理由: 合格"},
            "kb_hits": [],
        }
        from core.report_engine import _build_diagnosis_summary
        s = _build_diagnosis_summary(rec)
        assert "C1" in s
        assert "多智能体" in s
        assert "裁决: pass" in s

    def test_schema_1_0_still_works(self):
        """schema 1.0 (旧格式) 仍被兼容 — diagnosis_json 在顶层。"""
        from core.report_engine import _build_diagnosis_summary
        s = _build_diagnosis_summary(DIAG_RECORD_V1_0)
        assert "C1" in s
