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

    def test_grade_enum_ground_truth_is_injected_verbatim(self):
        from core.report_engine import _build_diagnosis_summary

        rec = dict(DIAG_RECORD)
        rec["chart_data"] = {
            "grade_table_md": (
                "| 传感器 | 评级 |\n|---|---|\n"
                "| A1 | 良 |\n| A2 | FAIL |\n| C2 | 优 |"
            )
        }

        s = _build_diagnosis_summary(rec)

        assert "| A2 | FAIL |" in s
        assert "评级/数值一律照摘要原文，不得改写" in s


class TestBuildContextWithDiagnosis:

    def test_context_includes_diagnosis(self):
        from core.report_engine import _build_context
        config = {"_diagnosis_record": DIAG_RECORD_V1_0}
        ctx, warnings = _build_context(config)
        assert "C1" in ctx
        assert "hys_fail" in ctx
        assert "不要被低σ误导" in ctx

    def test_context_with_1_1_schema(self):
        """schema 1.1 (ai_diagnosis 段) 也被正确处理。"""
        from core.report_engine import _build_context
        config = {"_diagnosis_record": DIAG_RECORD}
        ctx, warnings = _build_context(config)
        assert "C1" in ctx
        assert "hys_fail" in ctx

    def test_context_without_diagnosis_still_works(self):
        from core.report_engine import _build_context
        config = {"req_file": ""}
        ctx, warnings = _build_context(config)
        # 空诊断时 _build_context 会注入显式告知 (不是静默兜底套话)
        assert "未加载诊断数据" in ctx

    def test_context_with_both_req_and_diag(self):
        from core.report_engine import _build_context
        config = {"_diagnosis_record": DIAG_RECORD_V1_0, "req_file": "/nonexistent.txt"}
        ctx, warnings = _build_context(config)
        assert "C1" in ctx
        assert "未提供具体需求文件" not in ctx  # diag provides context
        # 不存在文件进警告
        assert any("不存在" in w for w in warnings)

    def test_context_no_dump_of_raw_text(self):
        from core.report_engine import _build_context
        config = {"_diagnosis_record": DIAG_RECORD}
        ctx, _ = _build_context(config)
        # Should NOT contain the full raw diagnosis JSON
        assert len(ctx) < 5000  # compact, not 11k

    def test_failed_file_yields_warning(self):
        """读入失败文件 → 进 warnings 列表，不静默。"""
        from core.report_engine import _build_context
        import tempfile, os as _os
        with tempfile.TemporaryDirectory() as td:
            bad = _os.path.join(td, "not_a_real_file.docx")
            config = {"project_files": [bad]}
            ctx, warnings = _build_context(config)
            assert any("不存在" in w for w in warnings)

    def test_unsupported_format_yields_warning(self):
        """不支持的扩展名 → 可见警告。"""
        from core.report_engine import _build_context
        import tempfile, os as _os
        with tempfile.TemporaryDirectory() as td:
            weird = _os.path.join(td, "data.xlsx")
            with open(weird, "w") as f:
                f.write("test")
            config = {"project_files": [weird]}
            _, warnings = _build_context(config)
            assert any("不支持的格式" in w for w in warnings)


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


# ═══════════════════════════════════════════════════════════════════════════
# Test: sensor count shared helper (0-vs-2 fix)
# ═══════════════════════════════════════════════════════════════════════════

class TestSensorCountHelper:
    """get_sensor_analysis / count_sensors 兼容 v1.0/v1.1."""

    def test_v1_1_schema_ai_diagnosis(self):
        """v1.1: ai_diagnosis.diagnosis_json.sensor_analysis"""
        from core.report_engine import get_sensor_analysis, count_sensors
        rec = {
            "schema_version": "1.1",
            "ai_diagnosis": {
                "diagnosis_json": {
                    "sensor_analysis": [
                        {"sensor_id": "C1", "status": "ok"},
                        {"sensor_id": "C2", "status": "ok"},
                    ]
                }
            },
        }
        assert count_sensors(rec) == 2
        sa = get_sensor_analysis(rec)
        assert len(sa) == 2
        assert sa[0]["sensor_id"] == "C1"

    def test_v1_0_schema_direct(self):
        """v1.0: diagnosis_json.sensor_analysis (顶层)"""
        from core.report_engine import get_sensor_analysis, count_sensors
        rec = {
            "schema_version": "1.0",
            "diagnosis_json": {
                "sensor_analysis": [
                    {"sensor_id": "A1"}, {"sensor_id": "B1"}, {"sensor_id": "C1"},
                    {"sensor_id": "D1"},
                ]
            },
        }
        assert count_sensors(rec) == 4
        assert len(get_sensor_analysis(rec)) == 4

    def test_multi_agent_chief_structured_takes_precedence(self):
        """实网多智能体记录必须显示 chief_structured 中的真实传感器数。"""
        from core.report_engine import count_sensors

        rec = {
            "ai_diagnosis": {"diagnosis_json": {"sensor_analysis": []}},
            "multi_agent": {
                "chief_structured": {
                    "sensor_analysis": [
                        {"sensor_id": sensor_id}
                        for sensor_id in ("A1", "A2", "B1", "B2", "C1", "C2")
                    ]
                }
            },
        }

        assert count_sensors(rec) == 6

    def test_empty_record_returns_zero(self):
        from core.report_engine import count_sensors, get_sensor_analysis
        assert count_sensors({}) == 0
        assert get_sensor_analysis({}) == []

    def test_none_diag_returns_zero(self):
        from core.report_engine import count_sensors, get_sensor_analysis
        rec = {"schema_version": "1.1", "ai_diagnosis": {}}
        assert count_sensors(rec) == 0
        assert get_sensor_analysis(rec) == []


# ═══════════════════════════════════════════════════════════════════════════
# Test: _extract_json balanced brace extraction
# ═══════════════════════════════════════════════════════════════════════════

class TestExtractJson:
    """_extract_json: 平衡括号匹配不跨块过捕获."""

    def test_single_object(self):
        from core.report_engine import _extract_json
        text = 'some text {"key": "value"} extra'
        assert _extract_json(text) == '{"key": "value"}'

    def test_code_fence_priority(self):
        from core.report_engine import _extract_json
        text = 'prefix\n```json\n{"a": 1}\n```\nsuffix'
        assert _extract_json(text) == '{"a": 1}'

    def test_multi_objects_no_cross_capture(self):
        """贪婪正则会跨块捕获第一个 { 到最后一个 }, 平衡括号只取第一个。"""
        from core.report_engine import _extract_json
        text = '{"first": 1} some text {"second": 2}'
        result = _extract_json(text)
        assert result == '{"first": 1}'

    def test_nested_braces(self):
        from core.report_engine import _extract_json
        text = '{"outer": {"inner": [1,2,3]}, "key": "val"} trailing'
        result = _extract_json(text)
        assert '"outer"' in result
        assert '"inner"' in result
        assert '"key"' in result

    def test_no_braces_returns_stripped(self):
        from core.report_engine import _extract_json
        assert _extract_json("just text") == "just text"
        assert _extract_json("") == ""


# ═══════════════════════════════════════════════════════════════════════════
# Test: section degradation (bad JSON → retry → degrade + warning)
# ═══════════════════════════════════════════════════════════════════════════

class TestSectionDegradation:
    """段 JSON 校验失败 → 1 次纠正重试 → 仍失败 → 降级占位 + 警告."""

    def _mock_generate_fn(self, prompts_return: list[str]):
        """返回一个 generate_fn，依次返回 prompts_return 内容。"""
        idx = [0]

        def fn(prompt: str) -> str:
            i = idx[0]
            idx[0] = min(i + 1, len(prompts_return) - 1)
            return prompts_return[i]

        return fn

    def test_section_markdown_degradation(self):
        """markdown 模式: 空响应 → 重试 1 次 → 降级 (不整篇 raise)。"""
        from core.report_engine import _generate_word_markdown_report

        sections = [{"heading": "测试章节", "key_points": []}]
        # 两次都返回空/仅空白
        bad_outputs = ["", "   \n  "]
        generate_fn = self._mock_generate_fn(bad_outputs)

        result = _generate_word_markdown_report(
            title="测试报告", sections=sections,
            project_context="", generate_fn=generate_fn,
        )
        assert "sections" in result
        assert len(result["sections"]) == 1
        sec = result["sections"][0]
        assert sec["heading"] == "测试章节"
        assert any("降级" in p for p in sec["content_paragraphs"])
        warnings_in = result.get("_report_warnings", [])
        assert any("降级" in w or "空内容" in w for w in warnings_in)

    def test_section_markdown_ok(self):
        """markdown 模式: 正常输出 → 段落正确。"""
        from core.report_engine import _generate_word_markdown_report

        sections = [{"heading": "可恢复节", "key_points": []}]
        outputs = ["## 概述\n\n正常正文段落。"]
        generate_fn = self._mock_generate_fn(outputs)

        result = _generate_word_markdown_report(
            title="测试", sections=sections,
            project_context="", generate_fn=generate_fn,
        )
        assert len(result["sections"]) == 1
        sec = result["sections"][0]
        assert "## 概述" in sec["content_paragraphs"][0]
        dw = result.get("_report_warnings", [])
        assert not any("降级" in w for w in dw)

    def test_word_prompt_contains_enum_passthrough_rule(self):
        from core.report_engine import _generate_word_markdown_report

        prompts: list[str] = []

        def generate(prompt: str) -> str:
            prompts.append(prompt)
            return "正文"

        _generate_word_markdown_report(
            title="测试",
            sections=[{"heading": "结论", "key_points": []}],
            project_context="| A2 | FAIL |",
            generate_fn=generate,
        )

        assert "评级/数值一律照摘要原文，不得改写" in prompts[0]

    def test_schema_1_1_both_sections(self):
        """schema 1.1: multi_agent 仅含废弃 report 字段 (无 chief_structured) → 降级 ai_diagnosis。"""
        rec = {
            "schema_version": "1.1",
            "ai_diagnosis": {
                "diagnosis_json": {
                    "sensor_analysis": [
                        {"sensor_id": "C1", "findings": "迟滞超标", "suggestions": "整改"},
                    ],
                },
            },
            "multi_agent": {"report": "# 旧格式 report 字段已废弃"},  # no chief_structured
            "kb_hits": [],
        }
        from core.report_engine import _build_diagnosis_summary
        s = _build_diagnosis_summary(rec)
        assert "C1" in s
        assert "多智能体结果暂缺" in s  # 降级标注

    def test_schema_1_0_still_works(self):
        """schema 1.0 (旧格式) 仍被兼容 — diagnosis_json 在顶层。"""
        from core.report_engine import _build_diagnosis_summary
        s = _build_diagnosis_summary(DIAG_RECORD_V1_0)
        assert "C1" in s
        assert "多智能体结果暂缺" in s  # 无 multi_agent key → 降级标注


# ═══════════════════════════════════════════════════════════════════════
# Phase 2 (Part B): 报告优先读 multi_agent.chief_structured
# ═══════════════════════════════════════════════════════════════════════

DIAG_RECORD_WITH_MULTI = {
    "schema_version": "1.2",
    "timestamp": "2026-06-28 12:00:00",
    "backend": "local", "model": "qwen3.5-9b",
    "kb_hits": [
        {
            "id": "hys_fail", "severity": "high",
            "objects": ["B1", "C1"],
            "meaning": "升降温不重合，超阈值",
            "recommendation": "复查封装工艺",
        },
    ],
    "ai_diagnosis": {
        "diagnosis_json": {
            "sensor_analysis": [
                {"sensor_id": "A1", "status": "normal",
                 "findings": "AI诊断认为A1正常", "suggestions": "继续使用"},
            ],
            "physical_diagnosis": {
                "phenomenon": "AI诊断: 胶层正常",
                "possible_causes": ["无"],
            },
        },
    },
    "multi_agent": {
        "chief_structured": {
            "diagnosis_summary": {
                "data_type": "fiber_optic",
                "template_name": "ENLIGHT",
                "data_quality": "fair",
                "anomaly_count": 0,
                "overall_assessment": "数据完整性良好但物理一致性存疑，存在假应变风险",
            },
            "data_quality_assessment": {
                "completeness": "数据完整，98450行×39列",
                "consistency": "单通道CV<1%，多源分歧显著",
                "anomaly_patterns": [
                    "w9与其余通道严重解耦(r=-0.25)",
                    "部分通道呈负相关",
                ],
                "recommendations": ["执行双栅解耦", "以应变片为基准校准"],
            },
            "sensor_analysis": [
                {"sensor_id": "C1-1", "status": "critical",
                 "findings": "与系统完全脱节，偏差490pm，极可能安装失效",
                 "suggestions": "优先排查NOA81胶水固化及光纤线路"},
                {"sensor_id": "A1-1", "status": "warning",
                 "findings": "单通道统计稳定但与B/C组一致性差，可能存在界面滑移",
                 "suggestions": "确认是否位于同一受力构件"},
            ],
            "physical_diagnosis": {
                "phenomenon": "光纤光栅波长漂移趋势与宏观物理场不匹配",
                "severity": "high",
                "possible_causes": [
                    "NOA81胶水与PI光纤界面微滑移",
                    "传感器安装位置存在应力集中",
                    "温度补偿未完全解耦",
                ],
                "recommended_actions": [
                    "立即复核C1-1传感器胶层固化情况",
                    "执行双栅解耦算法剔除假应变",
                    "采用应变片2与应变5(r=0.9642)为可靠基准",
                ],
            },
        },
        "data_scientist_text": (
            "所有12个FBG通道CV<1%，均值漂移量级10^-4 pm/千样本。"
            "核心异常：w9(C1-1)与其他通道严重解耦。"
            "可信基准：应变片2↔应变5 (r=0.9642)。"
        ),
        "audit_advisory": "审核结论：诊断逻辑链完整，三专家结论一致。",
    },
    "chart_data": {},
}

DIAG_RECORD_WITH_MULTI_EMPTY_CHIEF = {
    "schema_version": "1.2",
    "kb_hits": [],
    "ai_diagnosis": {
        "diagnosis_json": {
            "sensor_analysis": [
                {"sensor_id": "B1", "status": "critical",
                 "findings": "单专家判定迟滞超标", "suggestions": "报废"},
            ],
            "physical_diagnosis": {
                "phenomenon": "迟滞超标",
                "possible_causes": ["封装失效"],
            },
        },
    },
    "multi_agent": {
        "chief_structured": {},  # 空 dict
    },
}


class TestMultiAgentPriority:
    """报告优先用 multi_agent.chief_structured，无则降级 ai_diagnosis"""

    def test_uses_multi_agent_when_available(self):
        """有有效 chief_structured → 摘要含多智能体内容"""
        from core.report_engine import _build_diagnosis_summary
        s = _build_diagnosis_summary(DIAG_RECORD_WITH_MULTI)
        assert "多智能体专家组诊断" in s
        assert "C1-1" in s
        assert "A1-1" in s
        assert "与系统完全脱节" in s
        assert "NOA81胶水" in s
        assert "假应变风险" in s
        assert "数据质量概览" in s
        assert "双栅解耦" in s
        assert "数据科学家详细分析" in s
        assert "审核顾问意见" in s
        assert "三专家结论一致" in s

    def test_not_leak_ai_diagnosis_when_multi_present(self):
        """多智能体有效时，ai_diagnosis 的单专家结论不出现"""
        from core.report_engine import _build_diagnosis_summary
        s = _build_diagnosis_summary(DIAG_RECORD_WITH_MULTI)
        assert "AI诊断认为A1正常" not in s
        assert "AI诊断: 胶层正常" not in s

    def test_fallback_when_chief_structured_empty(self):
        """chief_structured 为空 dict → 降级 ai_diagnosis + 可见标注"""
        from core.report_engine import _build_diagnosis_summary
        s = _build_diagnosis_summary(DIAG_RECORD_WITH_MULTI_EMPTY_CHIEF)
        assert "单专家 AI 诊断" in s
        assert "多智能体结果暂缺" in s
        assert "B1" in s
        assert "迟滞超标" in s
        assert "封装失效" in s

    def test_fallback_when_no_multi_agent_key(self):
        """record 无 multi_agent key → 降级 ai_diagnosis"""
        from core.report_engine import _build_diagnosis_summary
        s = _build_diagnosis_summary(DIAG_RECORD_V1_0)
        assert "单专家 AI 诊断" in s
        assert "多智能体结果暂缺" in s
        assert "C1" in s

    def test_schema_1_1_with_empty_multi_still_works(self):
        """DIAG_RECORD (v1.1, multi_agent={}) → 降级"""
        from core.report_engine import _build_diagnosis_summary
        s = _build_diagnosis_summary(DIAG_RECORD)
        assert "单专家 AI 诊断" in s
        assert "多智能体结果暂缺" in s

    def test_kb_hits_still_included_in_both_paths(self):
        """KB 命中规则在两种路径都出现"""
        from core.report_engine import _build_diagnosis_summary
        s_multi = _build_diagnosis_summary(DIAG_RECORD_WITH_MULTI)
        s_single = _build_diagnosis_summary(DIAG_RECORD_V1_0)
        assert "诊断规则命中" in s_multi
        assert "诊断规则命中" in s_single


# ═══════════════════════════════════════════════════════════════════════
# Phase 3: summarize_diagnosis_record — 加载弹窗 + 报告共用 SSOT
# ═══════════════════════════════════════════════════════════════════════

DIAG_RECORD_MULTI_ONLY = {
    "schema_version": "1.2",
    "kb_hits": [
        {"id": "hys_fail", "severity": "high",
         "meaning": "迟滞超标", "recommendation": "复查"},
    ],
    "ai_diagnosis": {
        "diagnosis_json": None,  # ← 仅多智能体，单专家未跑
        "diagnosis_raw": "",
    },
    "multi_agent": {
        "chief_structured": {
            "sensor_analysis": [
                {"sensor_id": "B1", "status": "critical",
                 "findings": "迟滞严重超标", "suggestions": "报废"},
                {"sensor_id": "A1", "status": "warning",
                 "findings": "界面可能滑移", "suggestions": "交叉验证"},
                {"sensor_id": "C2", "status": "normal",
                 "findings": "各项指标优", "suggestions": "可投入使用"},
            ],
        },
        "data_scientist_text": "详报内容...",
        "audit_advisory": "审核意见...",
        "report": "",  # ← 废弃字段
    },
}

DIAG_RECORD_BOTH = {
    "schema_version": "1.2",
    "kb_hits": [
        {"id": "both_test", "severity": "info",
         "meaning": "测试", "recommendation": "无"},
    ],
    "ai_diagnosis": {
        "diagnosis_json": {
            "sensor_analysis": [
                {"sensor_id": "AI_ONLY_A1", "status": "normal",
                 "findings": "单专家意见", "suggestions": "忽略"},
            ],
        },
    },
    "multi_agent": {
        "chief_structured": {
            "sensor_analysis": [
                {"sensor_id": "B1", "status": "critical",
                 "findings": "多智能体意见覆盖", "suggestions": "报废"},
                {"sensor_id": "A1", "status": "warning",
                 "findings": "多智能体意见覆盖", "suggestions": "交叉验证"},
            ],
        },
        "data_scientist_text": "详报",
        "report": "",  # ← 废弃字段
    },
}


class TestSummarizeDiagnosisRecord:
    """summarize_diagnosis_record 四种 record 形态 + 废弃 report 字段回归"""

    def test_multi_only_record(self):
        """真机形态: ai_diagnosis=null, multi_agent.chief_structured.sensor_analysis=3"""
        from core.report_engine import summarize_diagnosis_record
        s = summarize_diagnosis_record(DIAG_RECORD_MULTI_ONLY)
        assert s['sensor_count'] == 3
        assert s['source_label'] == '多智能体专家组诊断'
        assert s['has_multi_agent'] is True
        assert s['kb_count'] == 1

    def test_single_expert_only(self):
        """仅单专家 AI 诊断，无 multi_agent"""
        from core.report_engine import summarize_diagnosis_record
        s = summarize_diagnosis_record(DIAG_RECORD_V1_0)
        assert s['sensor_count'] == 3  # C1, B1, A1
        assert s['source_label'] == '单专家 AI 诊断'
        assert s['has_multi_agent'] is False
        assert s['kb_count'] == 3

    def test_both_present_multi_wins(self):
        """两者皆非空 → multi_agent.chief_structured 优先"""
        from core.report_engine import summarize_diagnosis_record
        s = summarize_diagnosis_record(DIAG_RECORD_BOTH)
        assert s['sensor_count'] == 2  # 取 chief_structured 的2个，不是 ai_diagnosis 的1个
        assert s['source_label'] == '多智能体专家组诊断'
        assert s['has_multi_agent'] is True

    def test_both_empty(self):
        """两者皆空 → 诊断结果暂缺"""
        from core.report_engine import summarize_diagnosis_record
        s = summarize_diagnosis_record({})
        assert s['sensor_count'] == 0
        assert s['source_label'] == '诊断结果暂缺'
        assert s['has_multi_agent'] is False
        assert s['kb_count'] == 0

    def test_report_field_empty_but_chief_valid(self):
        """废弃 report 字段为空不影响 has_multi_agent (证明不再依赖 report)"""
        from core.report_engine import summarize_diagnosis_record
        rec = {
            "kb_hits": [],
            "ai_diagnosis": {"diagnosis_json": None},
            "multi_agent": {
                "chief_structured": {
                    "sensor_analysis": [
                        {"sensor_id": "X1", "findings": "ok"},
                    ],
                },
                "report": "",  # ← 废弃空串
            },
        }
        s = summarize_diagnosis_record(rec)
        assert s['has_multi_agent'] is True  # chief_structured 有数据 → True
        assert s['sensor_count'] == 1

    def test_aligned_with_build_diagnosis_summary(self):
        """summarize_diagnosis_record 与 _build_diagnosis_summary 同源一致"""
        from core.report_engine import summarize_diagnosis_record, _build_diagnosis_summary

        for rec in [DIAG_RECORD_MULTI_ONLY, DIAG_RECORD_V1_0,
                     DIAG_RECORD_BOTH, DIAG_RECORD_WITH_MULTI]:
            s = summarize_diagnosis_record(rec)
            text = _build_diagnosis_summary(rec)

            # source_label 在摘要文本中必然出现
            assert s['source_label'] in text or \
                   s['source_label'].replace('专家组诊断', '专家组诊断') in text, \
                   f"source_label={s['source_label']!r} not in summary"

            # 传感器数 ≥ 0
            assert s['sensor_count'] >= 0

    def test_no_underscore_keys_in_return_dict(self):
        """summarize_diagnosis_record 返回值不含 _ 前缀键（旧契约名已清理）"""
        from core.report_engine import summarize_diagnosis_record

        for rec in [DIAG_RECORD_MULTI_ONLY, DIAG_RECORD_V1_0,
                     DIAG_RECORD_BOTH, DIAG_RECORD_WITH_MULTI, {}]:
            s = summarize_diagnosis_record(rec)
            underscore_keys = [k for k in s.keys() if k.startswith('_')]
            assert underscore_keys == [], \
                f"不应含 _ 前缀键: {underscore_keys}, keys={list(s.keys())}"
            # 新键名存在
            assert 'chief_structured' in s
            assert 'multi_agent' in s
            assert 'sensor_analysis' in s
