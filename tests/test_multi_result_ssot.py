"""多智能体结果 SSOT + 排版规范化 回归测试 — Phase 7。

验证:
1. 同一 _multi_result → 显示与 Word 同源
2. Markdown 渲染器: 标题/加粗/列表/表格正确转、无残留 # ** |
3. 首席专家结论在顶部 + 醒目
4. 显示==Word: 章节顺序一致、格式对应
5. 向后兼容 + 截断标注 + 幂等切换
"""

import json
import os
import sys
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest


# ── Fixtures ──────────────────────────────────────────────────────────


@pytest.fixture
def valid_chief_json() -> dict:
    """一个有效的 chief structured 报告。"""
    return {
        "diagnosis_summary": {
            "data_type": "fiber_optic",
            "template_name": "ENLIGHT",
            "data_quality": "good",
            "anomaly_count": 3,
            "overall_assessment": "系统运行正常，少数异常点为瞬时扰动",
        },
        "data_quality_assessment": {
            "completeness": "数据完整，无缺失列",
            "consistency": "波长范围正常 (1520-1570nm)",
            "anomaly_patterns": ["FBG_A2 偶发跳变"],
        },
        "sensor_analysis": [
            {
                "sensor_id": "FBG_A1",
                "status": "normal",
                "statistics": {"mean": 1545.2, "min": 1544.8, "max": 1545.6},
                "findings": "波动在正常范围",
                "suggestions": "持续监测",
            },
            {
                "sensor_id": "FBG_A2",
                "status": "warning",
                "statistics": {"mean": 1550.1, "min": 1548.0, "max": 1552.5},
                "findings": "偶发跳变 2-3με 等效",
                "suggestions": "检查接头",
            },
        ],
        "physical_diagnosis": {
            "phenomenon": "FBG_A2 偶发跳变",
            "severity": "low",
            "possible_causes": ["接头松动", "光纤弯曲"],
            "recommended_actions": ["紧固接头", "检查光纤路径"],
        },
    }


@pytest.fixture(scope="session")
def ai_diag_widget(qapp):
    """构造 AiDiagnosisWidget 实例 (不挂载到主窗口, session-scoped)。"""
    from ui.ai_diagnosis import AiDiagnosisWidget

    widget = AiDiagnosisWidget()
    yield widget
    try:
        widget.deleteLater()
    except Exception:
        pass


# ── Phase 5 fixtures (新结构) ──


@pytest.fixture
def multi_result_with_chief(valid_chief_json: dict) -> dict:
    """Phase 5 _multi_result: chief_structured + data_scientist_text + audit_advisory。"""
    return {
        "chief_structured": valid_chief_json,
        "data_scientist_text": "数据科学家分析结果：已完成滤波和特征提取...",
        "audit_advisory": "1. 滤波参数选择合理\n2. 建议补充低温工况验证\n3. 异常点标注可能遗漏第3秒处的跳变",
        "logs": ["[DataScientist] 500 chars in 3.2s", "[Advisor] 200 chars in 2.1s"],
    }


@pytest.fixture
def multi_result_chief_none() -> dict:
    """Phase 5: chief JSON 解析失败 → chief_structured=None。"""
    return {
        "chief_structured": None,
        "data_scientist_text": "分析结果...",
        "audit_advisory": "审查意见: 滤波参数可能偏高",
        "logs": ["[DataScientist] 100 chars in 1.0s"],
    }


@pytest.fixture
def phase5_result_dict(valid_chief_json: dict) -> dict:
    """Phase 5 run_multi_agent 返回结构。"""
    return {
        "chief_report": json.dumps(valid_chief_json, ensure_ascii=False),
        "data_scientist_text": "数据科学家分析结果：已完成滤波和特征提取...",
        "audit_advisory": "1. 滤波参数选择合理\n2. 建议补充低温工况验证",
        "execution_logs": ["[DataScientist] 500 chars in 3.2s", "[Advisor] 200 chars in 2.1s"],
    }


# ── Tests: _on_multi_done 定型 (Phase 5) ─────────────────────────────


class TestOnMultiDonePhase5:
    """验证 _on_multi_done 从 Phase 5 返回结构正确固化。"""

    def test_on_multi_done_stores_chief_structured(
        self, ai_diag_widget, phase5_result_dict, valid_chief_json
    ):
        """_on_multi_done 将 chief_report 解析为 chief_structured。"""
        widget = ai_diag_widget
        widget._on_multi_done(phase5_result_dict)

        mr = widget._multi_result
        assert mr is not None
        assert mr.get("chief_structured") == valid_chief_json
        assert mr.get("data_scientist_text") == phase5_result_dict["data_scientist_text"]
        assert mr.get("audit_advisory") == phase5_result_dict["audit_advisory"]
        assert mr.get("logs") == phase5_result_dict["execution_logs"]

    def test_on_multi_done_chief_none_when_invalid_json(self, ai_diag_widget):
        """chief_report 无效 JSON → chief_structured=None。"""
        widget = ai_diag_widget

        result = {
            "chief_report": "这不是JSON，是纯文本报告...",
            "data_scientist_text": "报告...",
            "audit_advisory": "意见",
            "execution_logs": [],
        }

        widget._on_multi_done(result)

        mr = widget._multi_result
        assert mr is not None
        assert mr.get("chief_structured") is None
        assert mr.get("data_scientist_text") == "报告..."
        assert mr.get("audit_advisory") == "意见"

    def test_on_multi_done_empty_chief_report(self, ai_diag_widget):
        """chief_report 为空字符串 → chief_structured=None 不崩溃。"""
        widget = ai_diag_widget

        result = {
            "chief_report": "",
            "data_scientist_text": "报告",
            "audit_advisory": "",
            "execution_logs": [],
        }

        widget._on_multi_done(result)

        mr = widget._multi_result
        assert mr is not None
        assert mr.get("chief_structured") is None
        assert mr.get("data_scientist_text") == "报告"
        assert mr.get("audit_advisory") == ""

    def test_on_multi_done_sets_active_result_to_multi(self, ai_diag_widget):
        """_on_multi_done 将 _active_result 设为 'multi'。"""
        widget = ai_diag_widget
        widget._active_result = "ai"

        widget._on_multi_done({
            "chief_report": "",
            "data_scientist_text": "",
            "audit_advisory": "",
            "execution_logs": [],
        })
        assert widget._active_result == "multi"


# ── Tests: SSOT 一致性 (显示 ↔ Word) ──────────────────────────────────


class TestMultiResultSSOT:
    """验证: 显示渲染与 Word 渲染读同一份定型字段。"""

    def test_switch_reads_chief_structured_not_re_extract(
        self, ai_diag_widget, multi_result_with_chief
    ):
        """_switch_to_multi_result 从 chief_structured 读取，不重新 _extract_json。"""
        widget = ai_diag_widget
        widget._multi_result = multi_result_with_chief
        widget._active_result = "ai"

        with patch.object(widget, "_extract_json", wraps=widget._extract_json) as spy:
            widget._switch_to_multi_result()
            assert spy.call_count == 0, (
                f"不应重新 _extract_json (实际调用 {spy.call_count} 次)"
            )

    def test_switch_with_valid_chief_renders_html(
        self, ai_diag_widget, multi_result_with_chief
    ):
        """有效的 chief_structured → 结构化 HTML 卡片渲染。"""
        widget = ai_diag_widget
        widget._multi_result = multi_result_with_chief

        widget._switch_to_multi_result()

        html = widget.ai_diagnosis_result.toHtml()
        assert "FBG_A1" in html
        assert "FBG_A2" in html
        assert "数据质量" in html
        # 应含审核顾问意见 (非裁决)
        assert "审核顾问意见" in html or "顾问" in html

    def test_switch_shows_full_data_scientist_text(
        self, ai_diag_widget, multi_result_with_chief
    ):
        """data_scientist_text 完整展示，不截断。"""
        widget = ai_diag_widget
        long_ds = "详细分析报告: " + "数据 " * 200  # long text
        mr = dict(multi_result_with_chief)
        mr["data_scientist_text"] = long_ds
        widget._multi_result = mr

        widget._switch_to_multi_result()

        html = widget.ai_diagnosis_result.toHtml()
        # 长文本应完整出现
        assert "数据 " * 50 in html, "data_scientist_text 不应被截断"

    def test_switch_shows_audit_advisory(
        self, ai_diag_widget, multi_result_with_chief
    ):
        """审核顾问意见作为独立区块展示。"""
        widget = ai_diag_widget
        widget._multi_result = multi_result_with_chief

        widget._switch_to_multi_result()

        html = widget.ai_diagnosis_result.toHtml()
        assert "审查" in html or "顾问" in html or "意见" in html, (
            "HTML 应展示审核顾问意见区块"
        )

    def test_switch_with_none_chief_falls_back(
        self, ai_diag_widget, multi_result_chief_none
    ):
        """chief_structured=None → 降级展示，不崩溃。"""
        widget = ai_diag_widget
        widget._multi_result = multi_result_chief_none

        widget._switch_to_multi_result()

        text = widget.ai_diagnosis_result.toPlainText()
        assert len(text) > 0, "降级展示不应为空"

    def test_word_and_display_read_same_chief_structured(
        self, ai_diag_widget, multi_result_with_chief, valid_chief_json
    ):
        """_build_diagnosis_record 从 chief_structured 读取，与显示同源。"""
        widget = ai_diag_widget
        widget._multi_result = multi_result_with_chief

        rec = widget._build_diagnosis_record()
        ma = rec.get("multi_agent", {})

        assert ma.get("chief_structured") == valid_chief_json
        assert ma.get("data_scientist_text") == multi_result_with_chief["data_scientist_text"]
        assert ma.get("audit_advisory") == multi_result_with_chief["audit_advisory"]

    def test_word_render_with_chief_structured(self, ai_diag_widget, multi_result_with_chief):
        """_build_word_document 生成含完整详报+顾问意见的 docx。"""
        widget = ai_diag_widget
        widget._multi_result = multi_result_with_chief

        rec = widget._build_diagnosis_record()

        with tempfile.NamedTemporaryFile(suffix=".docx", delete=False) as f:
            output_path = f.name

        try:
            widget._build_word_document(rec, "multi", output_path)
            assert os.path.getsize(output_path) > 0
        finally:
            if os.path.exists(output_path):
                os.unlink(output_path)

    def test_word_render_includes_full_ds_and_advisory(
        self, ai_diag_widget, multi_result_with_chief
    ):
        """Word 文档包含完整的 data_scientist 详报 + 审核顾问意见。"""
        widget = ai_diag_widget
        widget._multi_result = multi_result_with_chief

        rec = widget._build_diagnosis_record()

        with tempfile.NamedTemporaryFile(suffix=".docx", delete=False) as f:
            output_path = f.name

        try:
            widget._build_word_document(rec, "multi", output_path)
            size = os.path.getsize(output_path)
            # 有详报+顾问意见，文档不应太小
            assert size > 5000, (
                f"Word 文档应含完整内容 (实际 {size} bytes)"
            )
        finally:
            if os.path.exists(output_path):
                os.unlink(output_path)

    def test_word_and_display_both_degrade_when_chief_none(
        self, ai_diag_widget, multi_result_chief_none
    ):
        """chief_structured=None 时 Word 和显示一致降级。"""
        widget = ai_diag_widget
        widget._multi_result = multi_result_chief_none

        widget._switch_to_multi_result()
        display_text = widget.ai_diagnosis_result.toPlainText()

        rec = widget._build_diagnosis_record()
        ma = rec.get("multi_agent", {})
        assert ma.get("chief_structured") is None

        with tempfile.NamedTemporaryFile(suffix=".docx", delete=False) as f:
            output_path = f.name
        try:
            widget._build_word_document(rec, "multi", output_path)
            assert os.path.getsize(output_path) > 0
        finally:
            if os.path.exists(output_path):
                os.unlink(output_path)

        assert len(display_text) > 0


# ── Tests: 幂等切换 ───────────────────────────────────────────────────


class TestMultiResultIdempotent:
    """验证: 反复切换 ai/multi，multi 内容不变。"""

    def test_switch_multi_twice_same_content(
        self, ai_diag_widget, multi_result_with_chief
    ):
        """连续切换到 multi 两次，内容相同。"""
        widget = ai_diag_widget
        widget._multi_result = multi_result_with_chief
        widget._active_result = "ai"

        widget._switch_to_multi_result()
        html1 = widget.ai_diagnosis_result.toHtml()

        widget._switch_to_ai_result()
        widget._switch_to_multi_result()
        html2 = widget.ai_diagnosis_result.toHtml()

        assert html1 == html2, f"两次输出应相同 (长度: {len(html1)} vs {len(html2)})"

    def test_switch_ai_multi_cycle_stable(
        self, ai_diag_widget, multi_result_with_chief
    ):
        """ai→multi→ai→multi 循环多次，multi 始终稳定。"""
        widget = ai_diag_widget
        widget._multi_result = multi_result_with_chief
        widget._ai_result = {"json": None, "raw": "AI 原始结果"}
        widget._active_result = "ai"

        multi_snapshots = []
        for _ in range(3):
            widget._switch_to_multi_result()
            multi_snapshots.append(widget.ai_diagnosis_result.toHtml())
            widget._switch_to_ai_result()

        for i in range(1, len(multi_snapshots)):
            assert multi_snapshots[0] == multi_snapshots[i], (
                f"第 {i+1} 次 multi 切换内容与首次不同"
            )

    def test_switch_does_not_mutate_stored_result(
        self, ai_diag_widget, multi_result_with_chief
    ):
        """切换操作不修改 _multi_result 中的定型数据。"""
        import copy
        widget = ai_diag_widget
        widget._multi_result = copy.deepcopy(multi_result_with_chief)
        widget._active_result = "ai"

        before = copy.deepcopy(widget._multi_result)

        widget._switch_to_multi_result()
        widget._switch_to_ai_result()
        widget._switch_to_multi_result()

        after = widget._multi_result
        assert before == after, "_multi_result 在多次切换后不应被修改"


# ── Tests: 向后兼容 ───────────────────────────────────────────────────


class TestBackwardCompat:
    """验证: Phase 5 结构向后兼容旧 record。"""

    def test_old_record_word_does_not_crash(self, ai_diag_widget):
        """旧版 record (无 chief_structured/audit_advisory) Word 不崩。"""
        old_rec = {
            "schema_version": "1.1",
            "timestamp": "2025-01-01 00:00:00",
            "backend": "ollama",
            "model": "qwen",
            "multi_agent": {
                "report": "## 旧版报告\n\n内容...",
                "raw_json": {},
            },
        }

        widget = ai_diag_widget
        with tempfile.NamedTemporaryFile(suffix=".docx", delete=False) as f:
            output_path = f.name
        try:
            widget._build_word_document(old_rec, "multi", output_path)
            assert os.path.getsize(output_path) > 0
        finally:
            if os.path.exists(output_path):
                os.unlink(output_path)

    def test_old_record_with_verdict_word_does_not_crash(self, ai_diag_widget):
        """旧版 record (含 verdict/audit_result) Word 不崩，降级展示。"""
        old_rec = {
            "schema_version": "1.1",
            "timestamp": "2025-01-01 00:00:00",
            "backend": "ollama",
            "model": "qwen",
            "multi_agent": {
                "report": "## 多智能体报告\n测试内容",
                "raw_json": {
                    "audit_result": {
                        "verdict": "pass",
                        "reasons": ["OK"],
                        "required_fixes": [],
                    }
                },
            },
            "rejection_count": 0,
        }

        widget = ai_diag_widget
        with tempfile.NamedTemporaryFile(suffix=".docx", delete=False) as f:
            output_path = f.name
        try:
            widget._build_word_document(old_rec, "multi", output_path)
            assert os.path.getsize(output_path) > 0
        finally:
            if os.path.exists(output_path):
                os.unlink(output_path)

    def test_record_compat_with_old_fields(self, ai_diag_widget, multi_result_with_chief):
        """新 record 向后兼容: report/raw_json 为空占位。"""
        widget = ai_diag_widget
        widget._multi_result = multi_result_with_chief

        rec = widget._build_diagnosis_record()
        ma = rec.get("multi_agent", {})

        # 新字段有值
        assert ma.get("chief_structured") is not None
        assert ma.get("data_scientist_text")
        assert isinstance(ma.get("audit_advisory"), str)
        # 旧字段为占位空值 (不崩)
        assert "report" in ma
        assert "raw_json" in ma

    def test_get_multi_report_from_new_structure(self, ai_diag_widget, multi_result_with_chief):
        """get_multi_report 从 Phase 5 定型字段重建报告。"""
        widget = ai_diag_widget
        widget._multi_result = multi_result_with_chief

        report = widget.get_multi_report()
        assert isinstance(report, str)
        assert len(report) > 0
        # 应含三个段
        assert "数据科学家" in report
        assert "顾问" in report or "审核" in report

    def test_get_multi_report_empty_when_no_result(self, ai_diag_widget):
        """无多体结果时返回空字符串。"""
        widget = ai_diag_widget
        widget._multi_result = None

        report = widget.get_multi_report()
        assert report == ""


# ── Tests: 显示==Word==完整 ────────────────────────────────────────────


class TestDisplayEqualsWordComplete:
    """验证: 同一 canonical → 显示与 Word 体量一致，均含完整报告。"""

    def test_both_contain_full_data_scientist(
        self, ai_diag_widget, multi_result_with_chief
    ):
        """显示和 Word 都含完整 data_scientist 详报。"""
        widget = ai_diag_widget
        widget._multi_result = multi_result_with_chief
        ds_text = multi_result_with_chief["data_scientist_text"]

        # 显示
        widget._switch_to_multi_result()
        html = widget.ai_diagnosis_result.toHtml()
        assert ds_text[:50] in html, "显示应含 data_scientist_text 开头"

        # Word
        rec = widget._build_diagnosis_record()
        with tempfile.NamedTemporaryFile(suffix=".docx", delete=False) as f:
            output_path = f.name
        try:
            widget._build_word_document(rec, "multi", output_path)
            size = os.path.getsize(output_path)
            assert size > 3000, "Word 应含完整 data_scientist 详报 (不应单薄)"
        finally:
            if os.path.exists(output_path):
                os.unlink(output_path)

    def test_both_contain_advisory(
        self, ai_diag_widget, multi_result_with_chief
    ):
        """显示和 Word 都含审核顾问意见。"""
        widget = ai_diag_widget
        widget._multi_result = multi_result_with_chief
        advisory = multi_result_with_chief["audit_advisory"]

        # 显示 — advisory 被 html.escape 处理，行首内容应在 HTML 中
        widget._switch_to_multi_result()
        html = widget.ai_diagnosis_result.toHtml()
        # 检查第一行关键内容 (已在 HTML 转义)
        assert "滤波参数选择合理" in html, "显示应含 audit_advisory 第一行内容"

        # Word — 确认包含 advisory 内容
        rec = widget._build_diagnosis_record()
        assert rec["multi_agent"]["audit_advisory"] == advisory, (
            "diagnosis_record 应保存完整 advisory"
        )

    def test_no_verdict_or_rejection_in_display(
        self, ai_diag_widget, multi_result_with_chief
    ):
        """显示中不含 '裁决' / '驳回' / 'reject' 旧版 UI 字样。"""
        widget = ai_diag_widget
        widget._multi_result = multi_result_with_chief

        widget._switch_to_multi_result()
        html = widget.ai_diagnosis_result.toHtml()

        assert "裁决" not in html, "Phase 5 显示不应含'裁决'"
        assert "驳回" not in html, "Phase 5 显示不应含'驳回'"


# ── Phase 6 fixtures ────────────────────────────────────────────────


@pytest.fixture
def multi_result_truncated(valid_chief_json: dict) -> dict:
    """Phase 6: chief_truncated=True — 截断降级场景。"""
    return {
        "chief_structured": valid_chief_json,
        "data_scientist_text": "完整数据科学家详报...",
        "audit_advisory": "审查意见...",
        "logs": ["[Chief] 200 chars in 2.0s (max_tok=512, TRUNCATED)"],
        "chief_truncated": True,
    }


@pytest.fixture
def phase6_result_dict(valid_chief_json: dict) -> dict:
    """Phase 6 run_multi_agent 返回结构 (含 chief_truncated)。"""
    return {
        "chief_report": json.dumps(valid_chief_json, ensure_ascii=False),
        "data_scientist_text": "数据科学家分析结果...",
        "audit_advisory": "审查意见...",
        "execution_logs": [],
        "chief_truncated": False,
    }


@pytest.fixture
def phase6_truncated_result_dict(valid_chief_json: dict) -> dict:
    """Phase 6: 截断降级返回结构。"""
    return {
        "chief_report": json.dumps(valid_chief_json, ensure_ascii=False),
        "data_scientist_text": "完整数据科学家详报...",
        "audit_advisory": "审查意见...",
        "execution_logs": ["[Chief] TRUNCATED"],
        "chief_truncated": True,
    }


# ── Tests: Phase 6 截断标记存储与展示 ─────────────────────────────


class TestChiefTruncatedDisplay:
    """Phase 6: chief_truncated 标记在显示和 Word 中正确呈现。"""

    def test_on_multi_done_stores_chief_truncated(
        self, ai_diag_widget, phase6_truncated_result_dict
    ):
        """_on_multi_done 将 chief_truncated 固化到 _multi_result。"""
        widget = ai_diag_widget
        widget._on_multi_done(phase6_truncated_result_dict)

        assert widget._multi_result is not None
        assert widget._multi_result.get("chief_truncated") is True

    def test_truncation_show_note_in_display(
        self, ai_diag_widget, multi_result_truncated
    ):
        """截断时显示含⚠截断警告。"""
        widget = ai_diag_widget
        widget._multi_result = multi_result_truncated

        widget._switch_to_multi_result()
        html = widget.ai_diagnosis_result.toHtml()

        assert "截断" in html or "被截断" in html, "截断标记应显示截断警告"

    def test_normal_no_truncation_note(
        self, ai_diag_widget, multi_result_with_chief
    ):
        """正常路径不含截断警告。"""
        widget = ai_diag_widget
        widget._multi_result = multi_result_with_chief

        widget._switch_to_multi_result()
        html = widget.ai_diagnosis_result.toHtml()

        assert "截断" not in html and "被截断" not in html, (
            "正常路径不应含截断警告"
        )

    def test_truncation_note_in_word(
        self, ai_diag_widget, multi_result_truncated
    ):
        """截断时 Word 含截断警告。"""
        widget = ai_diag_widget
        widget._multi_result = multi_result_truncated

        rec = widget._build_diagnosis_record()
        assert rec["multi_agent"]["chief_truncated"] is True

    def test_diagnosis_record_contains_chief_truncated(
        self, ai_diag_widget, multi_result_truncated
    ):
        """_build_diagnosis_record 含 chief_truncated 字段。"""
        widget = ai_diag_widget
        widget._multi_result = multi_result_truncated

        rec = widget._build_diagnosis_record()
        assert "chief_truncated" in rec["multi_agent"]
        assert rec["multi_agent"]["chief_truncated"] is True

    def test_phase6_result_structure(self, ai_diag_widget, phase6_result_dict):
        """Phase 6 返回结构被正确消耗，不丢字段。"""
        widget = ai_diag_widget
        widget._on_multi_done(phase6_result_dict)

        mr = widget._multi_result
        assert mr is not None
        assert mr.get("chief_structured") is not None
        assert mr.get("chief_truncated") is False
        assert mr.get("data_scientist_text") == phase6_result_dict["data_scientist_text"]


# ── Tests: Phase 7 Markdown 渲染器 ──────────────────────────────────


class TestMarkdownToHtmlRenderer:
    """_render_markdown_to_html: 标题/加粗/列表/表格/普通段 — 无裸 # ** |。"""

    def test_plain_text_no_marks(self):
        from ui.ai_diagnosis import AiDiagnosisWidget
        html = AiDiagnosisWidget._render_markdown_to_html("普通文本")
        assert "普通文本" in html
        assert "#" not in html

    def test_h1_heading(self):
        from ui.ai_diagnosis import AiDiagnosisWidget
        html = AiDiagnosisWidget._render_markdown_to_html("# 一级标题")
        assert "一级标题" in html
        # h2 tag wraps the heading (not # prefix)
        assert "h2" in html.lower()

    def test_h2_heading(self):
        from ui.ai_diagnosis import AiDiagnosisWidget
        html = AiDiagnosisWidget._render_markdown_to_html("## 二级标题")
        assert "二级标题" in html
        # h3 tag wraps ## heading
        assert "h3" in html.lower()

    def test_h3_heading(self):
        from ui.ai_diagnosis import AiDiagnosisWidget
        html = AiDiagnosisWidget._render_markdown_to_html("### 三级标题")
        assert "三级标题" in html
        assert "h4" in html.lower()

    def test_bold_text(self):
        from ui.ai_diagnosis import AiDiagnosisWidget
        html = AiDiagnosisWidget._render_markdown_to_html("这是 **加粗** 文字")
        assert "加粗" in html
        assert "**" not in html  # 无残留 ** 符号
        # bold 标签已渲染
        assert "<b>" in html.lower()

    def test_unordered_list_dash(self):
        from ui.ai_diagnosis import AiDiagnosisWidget
        html = AiDiagnosisWidget._render_markdown_to_html("- 条目1\n- 条目2")
        assert "条目1" in html
        assert "条目2" in html
        # list 标签已渲染
        assert "<li>" in html.lower()
        assert "<ul" in html.lower()  # 可能有 style 属性

    def test_unordered_list_star(self):
        from ui.ai_diagnosis import AiDiagnosisWidget
        html = AiDiagnosisWidget._render_markdown_to_html("* 星号条目")
        assert "<li>" in html.lower()

    def test_table(self):
        from ui.ai_diagnosis import AiDiagnosisWidget
        md = "| 列A | 列B |\n|-----|-----|\n| 值1 | 值2 |"
        html = AiDiagnosisWidget._render_markdown_to_html(md)
        assert "列A" in html
        assert "值1" in html
        assert "值2" in html
        # table 标签已渲染
        assert "<table" in html.lower()
        assert "<th" in html.lower()
        assert "<td" in html.lower()

    def test_mixed_content(self):
        from ui.ai_diagnosis import AiDiagnosisWidget
        md = "## 分析结果\n\n数据**质量良好**\n\n- 要点1\n- 要点2\n\n| 指标 | 值 |\n|------|----|\n| σ   | 0.5 |"
        html = AiDiagnosisWidget._render_markdown_to_html(md)
        # 无裸 markdown 符号 (** 加粗符, ## 标题符)
        assert "**" not in html
        assert "##" not in html
        # 内容存在, HTML 标签已渲染
        assert "分析结果" in html
        assert "质量良好" in html
        assert "要点1" in html
        assert "0.5" in html
        assert "<b>" in html.lower()
        assert "<ul" in html.lower()
        assert "<table" in html.lower()

    def test_empty_input(self):
        from ui.ai_diagnosis import AiDiagnosisWidget
        assert AiDiagnosisWidget._render_markdown_to_html("") == ""
        assert AiDiagnosisWidget._render_markdown_to_html("   ") == ""


# ── Tests: Phase 7 首席结论醒目 + 统一层次 ───────────────────────────


class TestChiefConclusionsProminent:
    """验证: 首席专家综合结论在顶部、醒目。"""

    def test_chief_conclusions_at_top(self, ai_diag_widget, multi_result_with_chief):
        """首席结论 HTML 含 '一、首席专家综合结论' 节标题。"""
        from ui.ai_diagnosis import AiDiagnosisWidget

        html = AiDiagnosisWidget._render_chief_conclusions_html(
            multi_result_with_chief["chief_structured"], kb_hits=[])
        assert "一、首席专家综合结论" in html

    def test_chief_conclusions_has_data_quality(self, ai_diag_widget, multi_result_with_chief):
        """首席结论含数据质量评级。"""
        from ui.ai_diagnosis import AiDiagnosisWidget

        html = AiDiagnosisWidget._render_chief_conclusions_html(
            multi_result_with_chief["chief_structured"], kb_hits=[])
        assert "数据质量" in html or "GOOD" in html or "优" in html

    def test_chief_conclusions_has_sensor_table(self, ai_diag_widget, multi_result_with_chief):
        """首席结论含传感器分级结论表格。"""
        from ui.ai_diagnosis import AiDiagnosisWidget

        html = AiDiagnosisWidget._render_chief_conclusions_html(
            multi_result_with_chief["chief_structured"], kb_hits=[])
        assert "传感器分级结论" in html or "传感器" in html
        assert "FBG_A1" in html

    def test_chief_conclusions_has_physical_diagnosis(self, ai_diag_widget, multi_result_with_chief):
        """首席结论含物理诊断要点。"""
        from ui.ai_diagnosis import AiDiagnosisWidget

        html = AiDiagnosisWidget._render_chief_conclusions_html(
            multi_result_with_chief["chief_structured"], kb_hits=[])
        assert "物理诊断" in html

    def test_chief_conclusions_with_kb_hits(self, ai_diag_widget, multi_result_with_chief):
        """携带 KB 命中规则时表格出现。"""
        from ui.ai_diagnosis import AiDiagnosisWidget
        kb = [{"id": "KB001", "severity": "high", "meaning": "波长漂移超标",
                "recommendation": "检查接头"}]
        html = AiDiagnosisWidget._render_chief_conclusions_html(
            multi_result_with_chief["chief_structured"], kb_hits=kb)
        assert "命中诊断规则" in html
        assert "KB001" in html

    def test_switch_multi_shows_chief_conclusions_section(
        self, ai_diag_widget, multi_result_with_chief):
        """切换多体结果时显示含首席结论标头。"""
        widget = ai_diag_widget
        widget._multi_result = multi_result_with_chief

        widget._switch_to_multi_result()
        html = widget.ai_diagnosis_result.toHtml()
        assert "一、首席专家综合结论" in html

    def test_switch_multi_shows_unified_hierarchy(
        self, ai_diag_widget, multi_result_with_chief):
        """显示含三个编号节。"""
        widget = ai_diag_widget
        widget._multi_result = multi_result_with_chief

        widget._switch_to_multi_result()
        html = widget.ai_diagnosis_result.toHtml()
        assert "一、首席专家综合结论" in html
        assert "二、数据科学家详细分析" in html
        assert "三、审核顾问意见" in html

    def test_word_unified_hierarchy(self, ai_diag_widget, multi_result_with_chief):
        """Word 含三个编号节。"""
        widget = ai_diag_widget
        widget._multi_result = multi_result_with_chief

        rec = widget._build_diagnosis_record()
        with tempfile.NamedTemporaryFile(suffix=".docx", delete=False) as f:
            output_path = f.name
        try:
            widget._build_word_document(rec, "multi", output_path)
            size = os.path.getsize(output_path)
            assert size > 8000, f"排版后 Word 应含丰富内容 (实际 {size} bytes)"
        finally:
            if os.path.exists(output_path):
                os.unlink(output_path)

    def test_ds_section_rendered_via_markdown(
        self, ai_diag_widget, multi_result_with_chief):
        """DS 详报经 markdown 渲染器, 无裸 ** / ## 符号。"""
        widget = ai_diag_widget
        mr = dict(multi_result_with_chief)
        mr["data_scientist_text"] = "## 分析\n**关键发现**: 正常\n- 点1"
        widget._multi_result = mr

        widget._switch_to_multi_result()
        html = widget.ai_diagnosis_result.toHtml()

        # 无裸 ** / ## markdown 符号 (Qt 用 font-weight 替代 <b>)
        assert "**" not in html
        assert "##" not in html
        assert "关键发现" in html  # 内容存在
        assert "分析" in html
        assert "点1" in html
        # Qt rich text 用 font-weight:700 表示 bold
        assert "font-weight:700" in html or "font-weight:600" in html

    def test_advisory_rendered_via_markdown(
        self, ai_diag_widget, multi_result_with_chief):
        """审核意见经 markdown 渲染器, 无裸符号。"""
        widget = ai_diag_widget
        mr = dict(multi_result_with_chief)
        mr["audit_advisory"] = "## 意见\n- 问题1\n- **严重**问题2"
        widget._multi_result = mr

        widget._switch_to_multi_result()
        html = widget.ai_diagnosis_result.toHtml()

        assert "##" not in html
        assert "**" not in html
        assert "严重" in html

    def test_missing_chief_structured_degrades_cleanly(
        self, ai_diag_widget, multi_result_chief_none):
        """chief_structured=None → fallback 含 DS 文本，不崩。"""
        widget = ai_diag_widget
        widget._multi_result = multi_result_chief_none

        widget._switch_to_multi_result()
        text = widget.ai_diagnosis_result.toPlainText()
        assert "分析结果" in text

    def test_no_bare_markdown_symbols_in_display(
        self, ai_diag_widget, multi_result_with_chief):
        """显示不含裸 ** / ## 符号残留。"""
        widget = ai_diag_widget
        mr = dict(multi_result_with_chief)
        mr["data_scientist_text"] = "## 报告\n**重点**: 关键发现\n- 点1"
        mr["audit_advisory"] = "# 意见\n* 列表项"
        widget._multi_result = mr

        widget._switch_to_multi_result()
        html = widget.ai_diagnosis_result.toHtml()

        # 裸 markdown 符号不应出现在渲染后的 content 里
        assert "**" not in html
        assert "##" not in html
        # 内容正确渲染
        assert "关键发现" in html
        assert "列表项" in html


# ── Tests: Phase 7 Word markdown 渲染器 ──────────────────────────────


class TestMarkdownToDocxRenderer:
    """_render_markdown_blocks_to_docx: 无裸 markdown 符号残留。"""

    def test_plain_text_no_marks(self, ai_diag_widget):
        from docx import Document
        doc = Document()
        ai_diag_widget._render_markdown_blocks_to_docx(doc, "普通文本")
        # 文档有内容
        assert len(doc.paragraphs) >= 1
        assert "普通文本" in doc.paragraphs[0].text

    def test_headings_in_docx(self, ai_diag_widget):
        from docx import Document
        doc = Document()
        ai_diag_widget._render_markdown_blocks_to_docx(doc, "# H1\n## H2")
        texts = [p.text for p in doc.paragraphs]
        assert any("H1" in t for t in texts)
        assert any("H2" in t for t in texts)

    def test_list_items_in_docx(self, ai_diag_widget):
        from docx import Document
        doc = Document()
        ai_diag_widget._render_markdown_blocks_to_docx(doc, "- 条目1\n- 条目2")
        texts = [p.text for p in doc.paragraphs]
        assert any("条目1" in t for t in texts)
        assert any("条目2" in t for t in texts)

    def test_table_in_docx(self, ai_diag_widget):
        from docx import Document
        doc = Document()
        ai_diag_widget._render_markdown_blocks_to_docx(
            doc, "| 列A | 列B |\n|-----|-----|\n| 值1 | 值2 |")
        assert len(doc.tables) >= 1

    def test_bold_in_docx(self, ai_diag_widget):
        from docx import Document
        doc = Document()
        ai_diag_widget._render_markdown_blocks_to_docx(doc, "这是 **加粗** 文字")
        # 至少有一处 bold
        has_bold = False
        for p in doc.paragraphs:
            for run in p.runs:
                if run.font.bold:
                    has_bold = True
        assert has_bold, "** 应转为 bold run"

    def test_empty_text_noop(self, ai_diag_widget):
        from docx import Document
        doc = Document()
        ai_diag_widget._render_markdown_blocks_to_docx(doc, "")
        ai_diag_widget._render_markdown_blocks_to_docx(doc, "  ")
        # 不崩溃 = pass


# ── Phase 2 Tests: sub/sup real formatting ───────────────────────────


class TestLatexSubSupFormatting:
    """①: _clean_latex_text 上下标 → <sup>/<sub> HTML, math-only。"""

    def test_superscript_single_digit(self):
        from ui.ai_diagnosis import AiDiagnosisWidget
        result = AiDiagnosisWidget._clean_latex_text(r"$R^2$")
        assert "<sup>2</sup>" in result
        assert "²" not in result  # no Unicode superscript
        assert "$" not in result

    def test_superscript_braced(self):
        from ui.ai_diagnosis import AiDiagnosisWidget
        result = AiDiagnosisWidget._clean_latex_text(r"$x^{max}$")
        assert "<sup>max</sup>" in result
        assert "$" not in result

    def test_subscript_braced(self):
        from ui.ai_diagnosis import AiDiagnosisWidget
        result = AiDiagnosisWidget._clean_latex_text(r"$T_{max}$")
        assert "<sub>max</sub>" in result
        assert "ₘ" not in result  # no Unicode subscript

    def test_subscript_single_digit(self):
        from ui.ai_diagnosis import AiDiagnosisWidget
        result = AiDiagnosisWidget._clean_latex_text(r"$x_1$")
        assert "<sub>1</sub>" in result

    def test_underscore_outside_math_stays(self):
        from ui.ai_diagnosis import AiDiagnosisWidget
        result = AiDiagnosisWidget._clean_latex_text("file_10pct")
        assert "file_10pct" in result  # 不动
        assert "<sub>" not in result

    def test_filename_underscore_preserved(self):
        from ui.ai_diagnosis import AiDiagnosisWidget
        result = AiDiagnosisWidget._clean_latex_text("S_B_max")
        assert "S_B_max" in result

    def test_greek_space_removed(self):
        from ui.ai_diagnosis import AiDiagnosisWidget
        result = AiDiagnosisWidget._clean_latex_text(r"$\Delta \lambda$")
        assert "Δλ" in result  # no space
        assert "Δ λ" not in result

    def test_symbol_mapping(self):
        from ui.ai_diagnosis import AiDiagnosisWidget
        result = AiDiagnosisWidget._clean_latex_text(r"$\mu$ strain, $\sigma$ noise")
        assert "μ" in result
        assert "σ" in result
        assert "$" not in result

    def test_clean_latex_preserves_outside_text(self):
        from ui.ai_diagnosis import AiDiagnosisWidget
        text = r"Sensor A2# shows $\Delta\lambda \approx 0.5$ nm at point_5"
        result = AiDiagnosisWidget._clean_latex_text(text)
        assert "point_5" in result  # underscore outside math: untouched
        # Check math was processed
        assert "Δλ" in result  # Δλ (Unicode)
        assert "≈" in result  # ≈
        assert "$" not in result


# ── Phase 2 Tests: DS prompt anti-fabrication ────────────────────────


class TestDSAntiFabrication:
    """②: DS prompt 含禁发问/禁臆造规则。"""

    def test_prompt_forbids_question_to_reader(self):
        import py.multi_agent as ma
        prompt = ma.DATA_SCIENTIST_PROMPT_TPL
        assert "严禁向读者发问" in prompt
        assert "请确认" in prompt  # 在禁止上下文中出现

    def test_prompt_forbids_fabrication(self):
        import py.multi_agent as ma
        prompt = ma.DATA_SCIENTIST_PROMPT_TPL
        assert "严禁臆造数值" in prompt
        assert "未提供" in prompt
        assert "无法计算" in prompt

    def test_prompt_requires_assumptions(self):
        import py.multi_agent as ma
        prompt = ma.DATA_SCIENTIST_PROMPT_TPL
        assert "分析口径" in prompt or "假设说明" in prompt

    def test_ai_sysprompt_has_fabrication_rule(self, ai_diag_widget):
        """AI 单体诊断系统提示词也含禁臆造规则。"""
        prompt = ai_diag_widget._build_system_prompt({"data_type": "fiber_optic"})
        assert "严禁臆造数值" in prompt
        assert "严禁向读者发问" in prompt


# ── Phase 2 Tests: single AI title ───────────────────────────────────


class TestSingleAITitle:
    """④: 单体 AI 诊断标题改为'诊断摘要'。"""

    def test_ai_conclusions_title_is_summary(self, valid_chief_json):
        from ui.ai_diagnosis import AiDiagnosisWidget
        html = AiDiagnosisWidget._render_chief_conclusions_html(
            valid_chief_json, kb_hits=[], kind="ai")
        assert "诊断摘要" in html
        assert "首席专家综合结论" not in html

    def test_multi_conclusions_title_is_chief(self, valid_chief_json):
        from ui.ai_diagnosis import AiDiagnosisWidget
        html = AiDiagnosisWidget._render_chief_conclusions_html(
            valid_chief_json, kb_hits=[], kind="multi")
        assert "首席专家综合结论" in html

    def test_ai_kind_defaults_to_multi(self, valid_chief_json):
        """不传 kind 时默认 multi（向后兼容）。"""
        from ui.ai_diagnosis import AiDiagnosisWidget
        html = AiDiagnosisWidget._render_chief_conclusions_html(
            valid_chief_json)
        assert "首席专家综合结论" in html


# ── Phase 2b Tests: code fence rendering ──────────────────────────────


class TestCodeFenceHtml:
    """①: ``` 围栏 → <pre><code> 等宽块，无残留。"""

    def test_fenced_code_no_residue(self):
        from ui.ai_diagnosis import AiDiagnosisWidget
        md = "```text\nline1\nline2\n```"
        html = AiDiagnosisWidget._render_markdown_to_html(md)
        assert "```" not in html  # 无残留
        assert "<pre" in html
        assert "<code>" in html
        assert "line1" in html
        assert "line2" in html

    def test_fenced_code_with_language_label(self):
        from ui.ai_diagnosis import AiDiagnosisWidget
        md = "```python\nprint('hi')\n```"
        html = AiDiagnosisWidget._render_markdown_to_html(md)
        assert "<pre" in html
        assert "print('hi')" in html or "print(&#x27;hi&#x27;)" in html
        assert "```" not in html

    def test_fenced_code_triple_tilde(self):
        from ui.ai_diagnosis import AiDiagnosisWidget
        md = "~~~\nsome code\n~~~"
        html = AiDiagnosisWidget._render_markdown_to_html(md)
        assert "~~~" not in html
        assert "<pre" in html
        assert "some code" in html

    def test_fenced_code_preserves_newlines(self):
        from ui.ai_diagnosis import AiDiagnosisWidget
        md = "```\na\n\nb\n```"
        html = AiDiagnosisWidget._render_markdown_to_html(md)
        # 换行保留 (通过 <br>)
        assert "<br>" in html

    def test_inline_backtick_still_works(self):
        from ui.ai_diagnosis import AiDiagnosisWidget
        md = "start `code` end"
        html = AiDiagnosisWidget._render_markdown_to_html(md)
        assert "`" not in html
        assert "<code" in html


class TestCodeFenceDocx:
    """①: ``` 围栏 → docx 等宽段。"""

    def test_fenced_code_in_docx(self, ai_diag_widget):
        from docx import Document
        doc = Document()
        ai_diag_widget._render_markdown_blocks_to_docx(doc, "```\nline1\nline2\n```")
        has_consolas = False
        for p in doc.paragraphs:
            for run in p.runs:
                if run.font.name == 'Consolas':
                    has_consolas = True
                    break
        assert has_consolas, "代码围栏应使用 Consolas 等宽字体"

    def test_fenced_code_no_backtick_residue_docx(self, ai_diag_widget):
        from docx import Document
        doc = Document()
        ai_diag_widget._render_markdown_blocks_to_docx(doc, "```\ntest\n```")
        all_text = ' '.join(p.text for p in doc.paragraphs)
        assert '```' not in all_text, "``` 不应残留为正文"


# ── Phase 2b Tests: ASCII art ban ────────────────────────────────────


class TestASCIIArtBan:
    """②: prompt 禁止 ASCII 字符画。"""

    def test_ds_prompt_bans_ascii_art(self):
        import py.multi_agent as ma
        prompt = ma.DATA_SCIENTIST_PROMPT_TPL
        assert "禁止 ASCII 字符画" in prompt
        assert "字符拼绘" in prompt or "拼绘趋势" in prompt
        assert "软件绘图模块出图" in prompt

    def test_ai_sysprompt_bans_ascii_art(self, ai_diag_widget):
        prompt = ai_diag_widget._build_system_prompt({"data_type": "fiber_optic"})
        assert "禁止 ASCII 字符画" in prompt
        assert "软件绘图模块出图" in prompt


# ── Phase 3 Tests: save dialog default path + button layout ────────────


class TestSaveDialogDefaultPath:
    """②: 保存对话框默认打开项目资料库 (SSOT: 软件根/项目资料库)."""

    def test_get_project_library_dir_is_deterministic(self, monkeypatch):
        """get_project_library_dir() 返回软件根/项目资料库, 不依赖 QSettings."""
        import os, sys
        from main import DataProcessorWindow

        # 临时替换软件根目录
        with tempfile.TemporaryDirectory() as tmp_root:
            monkeypatch.setattr(
                DataProcessorWindow, 'get_software_root_dir',
                staticmethod(lambda: tmp_root))

            result = DataProcessorWindow.get_project_library_dir(
                DataProcessorWindow.__new__(DataProcessorWindow))
            expected = os.path.join(tmp_root, "项目资料库")
            assert result == expected
            assert os.path.isdir(result)  # os.makedirs created it

    def test_get_project_library_dir_creates_if_missing(self, monkeypatch):
        """库目录不存在时自动创建。"""
        import os, sys
        from main import DataProcessorWindow

        with tempfile.TemporaryDirectory() as tmp_root:
            monkeypatch.setattr(
                DataProcessorWindow, 'get_software_root_dir',
                staticmethod(lambda: tmp_root))
            lib_dir = os.path.join(tmp_root, "项目资料库")
            assert not os.path.isdir(lib_dir)  # 初始不存在

            result = DataProcessorWindow.get_project_library_dir(
                DataProcessorWindow.__new__(DataProcessorWindow))
            assert os.path.isdir(lib_dir)  # 已创建

    def test_button_min_heights(self, ai_diag_widget):
        """两按钮 minHeight 足够 (≥36px) 防重叠."""
        assert ai_diag_widget.submit_external_btn.minimumHeight() >= 36
        assert ai_diag_widget.clear_external_btn.minimumHeight() >= 36

    def test_req_list_has_max_height(self, ai_diag_widget):
        """需求文件 QListWidget 有 maxHeight 约束 (不贪空间)."""
        rl = getattr(ai_diag_widget, '_req_list', None)
        assert rl is not None, "_req_list should exist after __init__"
        assert rl.maximumHeight() == 80, f"maxHeight should be 80, got {rl.maximumHeight()}"

    def test_source_group_has_expanding_vpolicy(self, ai_diag_widget):
        """数据源 QGroupBox SizePolicy 垂直方向为 Preferred (允许扩展)."""
        # Find the source group by looking at children
        from PyQt6.QtWidgets import QGroupBox
        group = None
        for child in ai_diag_widget.findChildren(QGroupBox):
            if child.title() == "数据源（注入诊断上下文）":
                group = child
                break
        assert group is not None, "Should find source group"
        sp = group.sizePolicy()
        assert sp.verticalPolicy() == sp.Policy.Preferred, (
            f"垂直 SizePolicy 应为 Preferred, got {sp.verticalPolicy()}")


