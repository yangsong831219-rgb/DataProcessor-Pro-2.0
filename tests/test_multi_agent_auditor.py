"""Phase 6: 线性顾问式 + 输入预算安全 — 无驳回/无重跑/无裁决/截断不崩。

所有测试用合成 StateGraph + mock LLM，无真实网络调用。
"""

from __future__ import annotations

import json
import pytest

from dp_engine.multi_agent import MultiAgentState


# ═══════════════════════════════════════════════════════════════════════
# 测试辅助
# ═══════════════════════════════════════════════════════════════════════


def _make_multi_agent_state(
    data_scientist_report: str = "DS 分析结果...",
    audit_advisory: str = "审查意见...",
    chief_scientist_report: str | None = None,
    chief_truncated: bool = False,
    current_csv_path: str = "",
) -> MultiAgentState:
    """构造符合 MultiAgentState 合同的测试状态。

    所有字段明确提供，避免 dict→TypedDict 类型不匹配。
    """
    return MultiAgentState(
        messages=[],
        current_csv_path=current_csv_path,
        execution_logs=[],
        data_scientist_report=data_scientist_report,
        audit_advisory=audit_advisory,
        chief_scientist_report=chief_scientist_report,
        chief_truncated=chief_truncated,
    )


# ═══════════════════════════════════════════════════════════════════════
# Token 预算工具
# ═══════════════════════════════════════════════════════════════════════


class TestEstimateTokens:
    """_estimate_tokens 字符→token 估算。"""

    def test_empty_string(self):
        from dp_engine.multi_agent import _estimate_tokens
        assert _estimate_tokens("") == 0
        assert _estimate_tokens("   ") > 0  # 空格也算字符

    def test_english_text(self):
        from dp_engine.multi_agent import _estimate_tokens
        text = "Hello world, this is a test sentence with about fifty characters."
        tokens = _estimate_tokens(text)
        # 英文 ~3.5 char/token → ~14 tokens
        assert 10 <= tokens <= 30, f"英文估算应在合理范围, got {tokens}"

    def test_chinese_text(self):
        from dp_engine.multi_agent import _estimate_tokens
        text = "这是一段中文测试文本大约有三十个汉字左右用于估算token数量"
        tokens = _estimate_tokens(text)
        # 中文 ~1.5 char/token → ~20 tokens
        assert 15 <= tokens <= 40, f"中文估算应在合理范围, got {tokens}"

    def test_mixed_text(self):
        from dp_engine.multi_agent import _estimate_tokens
        text = "传感器 FBG_A1 的波长均值是 1545.2 nm"
        tokens = _estimate_tokens(text)
        assert tokens > 0

    def test_long_text_grows(self):
        from dp_engine.multi_agent import _estimate_tokens
        short = _estimate_tokens("短")
        long = _estimate_tokens("长文本 " * 100)
        assert long > short * 10, "长文本 token 数应明显更大"


class TestTrimReportForBudget:
    """_trim_report_for_budget 输入裁剪。"""

    def test_short_report_untouched(self):
        from dp_engine.multi_agent import _trim_report_for_budget
        short = "简短报告"
        result = _trim_report_for_budget(short, 1000)
        assert result == short, "短报告不应被裁剪"

    def test_long_report_trimmed(self):
        from dp_engine.multi_agent import _trim_report_for_budget
        long = "数据 " * 500  # ~500 CJK chars
        result = _trim_report_for_budget(long, 50)  # 50 token budget
        assert len(result) < len(long), "长报告应被裁剪"
        assert "中间段已裁剪" in result, "应含裁剪标记"

    def test_empty_report(self):
        from dp_engine.multi_agent import _trim_report_for_budget
        assert _trim_report_for_budget("", 100) == ""

    def test_trim_preserves_start(self):
        from dp_engine.multi_agent import _trim_report_for_budget
        report = "AAAA" + "X" * 2000 + "ZZZZ"
        result = _trim_report_for_budget(report, 50)
        # 开头保留
        assert result.startswith("AAAA"), "开头应保留"


class TestSafeMaxTokens:
    """_safe_max_tokens 安全计算。"""

    def test_returns_min_512(self):
        from dp_engine.multi_agent import _safe_max_tokens
        # 中等长度 prompt → available 在 512-N 之间
        medium = "x" * 5000
        result = _safe_max_tokens(medium, medium, 4096)
        # 应有 positive 但 ≤ requested
        assert 512 <= result <= 4096, f"应在 [512, 4096], got {result}"

    def test_huge_prompt_returns_512(self):
        from dp_engine.multi_agent import _safe_max_tokens
        # 超长 prompt 填满 ctx → 退回最小值 512
        huge = "x" * 20000
        result = _safe_max_tokens(huge, huge, 4096, ctx_tokens=4096)
        assert result == 512, "超长 prompt 应退回最小值 512"

    def test_caps_at_requested(self):
        from dp_engine.multi_agent import _safe_max_tokens
        result = _safe_max_tokens("短", "短", 2048, ctx_tokens=8192)
        assert result == 2048, "prompt 很短时不变"

    def test_respects_context_limit(self):
        from dp_engine.multi_agent import _safe_max_tokens
        # prompt 估算 ≈ 2000 tokens, ctx=3000, margin=600 → available=400
        # 请求 2048 → 应返回 400
        result = _safe_max_tokens("x" * 3000, "y" * 4000, 2048, ctx_tokens=3000)
        assert result == 512, "ctx 不够时应退回安全值"


# ═══════════════════════════════════════════════════════════════════════
# 图结构验证 — 线性, 无回环 (Phase 5 回归)
# ═══════════════════════════════════════════════════════════════════════


class TestLinearGraph:
    """验证图结构为线性: START → DS → Advisor → Chief → END。"""

    def test_graph_is_linear_no_conditional_edges(self):
        from dp_engine.multi_agent import create_multi_agent_graph

        graph = create_multi_agent_graph()
        assert graph is not None

        import dp_engine.multi_agent as ma
        assert not hasattr(ma, '_MAX_REJECTIONS')
        assert not hasattr(ma, 'should_continue_workflow')

    def test_no_rejection_count_in_state(self):
        from dp_engine.multi_agent import MultiAgentState
        ann = MultiAgentState.__annotations__
        assert "rejection_count" not in ann
        assert "audit_result" not in ann
        assert "task_status" not in ann

    def test_run_multi_agent_return_keys(self):
        """Phase 6 返回含 chief_truncated。"""
        from dp_engine.multi_agent import run_multi_agent
        import inspect
        src = inspect.getsource(run_multi_agent)
        # 新返回键
        assert "chief_report" in src
        assert "data_scientist_text" in src
        assert "audit_advisory" in src
        assert "chief_truncated" in src
        # 旧字段不应出现
        assert "audit_result" not in src.split("return {")[1].split("}")[0] if "return {" in src else True


# ═══════════════════════════════════════════════════════════════════════
# Advisor 顾问式 (Phase 5 回归)
# ═══════════════════════════════════════════════════════════════════════


class TestAdvisoryAdvisor:
    """Advisor 为顾问式 (不裁决, 自由文本)。"""

    def test_advisor_prompt_is_advisory(self):
        import dp_engine.multi_agent as ma
        prompt = ma.DATA_ADVISOR_PROMPT_TPL
        prompt_body = prompt.replace("{data_context}", "")
        assert "{" not in prompt_body
        assert "verdict" not in prompt.lower()
        assert "顾问" in prompt or "建议" in prompt or "意见" in prompt

    def test_extract_advisory_text_passthrough(self):
        from dp_engine.multi_agent import _extract_advisory_text
        assert _extract_advisory_text("审查意见") == "审查意见"
        assert _extract_advisory_text("") == ""
        assert _extract_advisory_text("  ") == ""


# ═══════════════════════════════════════════════════════════════════════
# Phase 6: 截断降级
# ═══════════════════════════════════════════════════════════════════════


class TestChiefTruncationDegrade:
    """Chief 触发 AIClientTruncationError → 降级保底, 不崩。"""

    def test_chief_node_catches_truncation(self):
        """mock _invoke_llm 抛 AIClientTruncationError → chief 不抛, 设 chief_truncated=True。"""
        from dp_engine.multi_agent import _make_nodes, AIClientTruncationError

        ds_node, adv_node, chief_node = _make_nodes()

        # 构造 state (DS 已运行, advisor 已运行)
        state = _make_multi_agent_state()

        # mock _invoke_llm 抛 AIClientTruncationError (有 partial_content)
        import dp_engine.multi_agent as ma
        original = ma._invoke_llm

        def _mock_invoke(sp, up, max_tokens=4096):
            raise AIClientTruncationError(
                "截断", partial_content='{"diagnosis_summary":{"data_quality":"good"}}'
            )

        ma._invoke_llm = _mock_invoke
        try:
            result = chief_node(state)
            # 不应抛异常
            truncated = result.get("chief_truncated")
            assert truncated is True
            # 应有部分内容
            report = result.get("chief_scientist_report")
            assert isinstance(report, str)
            assert len(report) > 0
            assert "diagnosis_summary" in report
        finally:
            ma._invoke_llm = original

    def test_chief_node_truncation_empty_content(self):
        """AIClientTruncationError 无 partial_content → chief_report 为空字符串, 不崩。"""
        from dp_engine.multi_agent import _make_nodes, AIClientTruncationError

        _, _, chief_node = _make_nodes()

        state = _make_multi_agent_state()

        import dp_engine.multi_agent as ma
        original = ma._invoke_llm

        def _mock_invoke(sp, up, max_tokens=4096):
            raise AIClientTruncationError("截断, 无 content")

        ma._invoke_llm = _mock_invoke
        try:
            result = chief_node(state)
            truncated = result.get("chief_truncated")
            assert truncated is True
            report = result.get("chief_scientist_report")
            assert isinstance(report, str)
            assert report == ""
        finally:
            ma._invoke_llm = original

    def test_chief_node_non_truncation_still_raises(self):
        """非截断异常 (如 AuthError) 仍向上抛。"""
        from dp_engine.multi_agent import _make_nodes

        _, _, chief_node = _make_nodes()

        state = _make_multi_agent_state()

        import dp_engine.multi_agent as ma
        original = ma._invoke_llm

        def _mock_invoke(sp, up, max_tokens=4096):
            raise RuntimeError("模拟其他错误")

        ma._invoke_llm = _mock_invoke
        try:
            with pytest.raises(RuntimeError, match="模拟其他错误"):
                chief_node(state)
        finally:
            ma._invoke_llm = original


# ═══════════════════════════════════════════════════════════════════════
# Phase 6: 输入预算 — chief 不灌 DS 全文
# ═══════════════════════════════════════════════════════════════════════


class TestChiefInputBudget:
    """chief 输入裁剪到预算内，不灌 DS 全文。"""

    def test_chief_prompt_trimmed_for_long_ds(self):
        """超长 DS 报告 → chief prompt 被裁剪。"""
        from dp_engine.multi_agent import _make_nodes

        ds_node, adv_node, chief_node = _make_nodes()

        long_ds = "传感器数据分析报告\n" + "详细数据 " * 3000  # ~15000 chars

        state = _make_multi_agent_state(
            data_scientist_report=long_ds,
            audit_advisory="意见",
        )

        # 不实际调 LLM — 只验证 prompt 构造逻辑 (通过 mock _invoke_llm 检查参数)
        import dp_engine.multi_agent as ma
        original = ma._invoke_llm
        captured_user_prompt = []

        def _capture(sp, up, max_tokens=4096):
            captured_user_prompt.append(up)
            return '{"diagnosis_summary":{"data_quality":"good"}}'

        ma._invoke_llm = _capture
        try:
            result = chief_node(state)
            assert len(captured_user_prompt) == 1
            prompt = captured_user_prompt[0]
            # 完整 DS 不应在 prompt 中 (被裁剪了)
            assert long_ds not in prompt, "完整 DS 不应进入 chief prompt"
            # 裁剪标记应在 prompt 中
            assert "中间段已裁剪" in prompt, "长 DS 应触发裁剪标记"
            # 开头保留
            assert "传感器数据分析报告" in prompt
        finally:
            ma._invoke_llm = original

    def test_short_ds_not_trimmed(self):
        """短 DS 报告 → chief prompt 不触发裁剪。"""
        from dp_engine.multi_agent import _make_nodes

        _, _, chief_node = _make_nodes()

        short_ds = "短报告: 一切正常"

        state = _make_multi_agent_state(
            data_scientist_report=short_ds,
            audit_advisory="无",
        )

        import dp_engine.multi_agent as ma
        original = ma._invoke_llm
        captured = []

        def _capture(sp, up, max_tokens=4096):
            captured.append(up)
            return '{"diagnosis_summary":{"data_quality":"good"}}'

        ma._invoke_llm = _capture
        try:
            chief_node(state)
            prompt = captured[0]
            assert "中间段已裁剪" not in prompt, "短 DS 不应触发裁剪"
            assert short_ds in prompt, "短 DS 应完整保留"
        finally:
            ma._invoke_llm = original

    def test_max_tokens_not_8192(self):
        """chief max_tokens 不再是硬编码 8192。"""
        import dp_engine.multi_agent as ma
        src = open(ma.__file__, encoding='utf-8').read()
        # 不应有 8192 硬编码在 chief 相关逻辑中
        # (safe_max_tokens 上限 4096 或 3072, 取决于节点)
        import re
        chief_func = re.search(r'def chief_scientist_node.*?(?=\n    def |\n    return ds)', src, re.DOTALL)
        if chief_func:
            # _safe_max_tokens 调用第三个参数应 < 8192
            assert '8192' not in chief_func.group(), "chief 节点不应再硬编码 8192"


# ═══════════════════════════════════════════════════════════════════════
# 源码审计 — 无旧字样残留
# ═══════════════════════════════════════════════════════════════════════


class TestSourceAudit:
    """反查活跃路径不含旧版惊扰/回环字样。"""

    def test_no_old_alarm_words(self):
        from pathlib import Path as _Path
        import re
        ma_path = _Path(__file__).parent.parent / "dp_engine" / "multi_agent.py"
        src = ma_path.read_text(encoding="utf-8")
        code_only = re.sub(r'""".*?"""', '', src, flags=re.DOTALL)
        code_only = re.sub(r'#.*$', '', code_only, flags=re.MULTILINE)

        forbidden = [
            "_MAX_REJECTIONS", "_invoke_structured", "_parse_audit_json",
            "_AuditVerdictModel", "AUDIT_JSON_SCHEMA", "_AUDIT_SCHEMA_STR",
            "should_continue_workflow", "rejection_count", "is_failed", "audit_result",
        ]
        for word in forbidden:
            assert word not in code_only, f"活跃代码不应含 '{word}'"

    def test_linear_progress_messages(self):
        from pathlib import Path as _Path
        ma_path = _Path(__file__).parent.parent / "dp_engine" / "multi_agent.py"
        src = ma_path.read_text(encoding="utf-8")
        assert "数据科学家" in src
        assert "审核员" in src
        assert "首席" in src

    def test_phase6_features_present(self):
        """源码含 Phase 6 新特性。"""
        from pathlib import Path as _Path
        ma_path = _Path(__file__).parent.parent / "dp_engine" / "multi_agent.py"
        src = ma_path.read_text(encoding="utf-8")
        assert "_estimate_tokens" in src
        assert "_trim_report_for_budget" in src
        assert "_safe_max_tokens" in src
        assert "AIClientTruncationError" in src
        assert "chief_truncated" in src
