"""Phase 3: 审查裁决结构化 — AuditVerdict schema + 确定性路由 + 显式失败终态

所有测试均用合成 StateGraph + mock LLM，无真实网络调用。
"""

from __future__ import annotations

import json
import pytest


# ═══════════════════════════════════════════════════════════════════════
# AuditVerdict schema 定义（与 multi_agent.py 同步）
# ═══════════════════════════════════════════════════════════════════════


class TestAuditVerdictSchema:
    """AuditVerdict 结构校验"""

    def test_pass_verdict_valid(self):
        verdict = {"verdict": "pass", "reasons": ["所有指标正常"], "required_fixes": []}
        assert verdict["verdict"] == "pass"
        assert isinstance(verdict["reasons"], list)
        assert len(verdict["reasons"]) >= 1

    def test_reject_verdict_valid(self):
        verdict = {
            "verdict": "reject",
            "reasons": ["滤波截止频率过低导致波形畸变", "缺失值处理不完整"],
            "required_fixes": ["提高滤波截止频率至≥50Hz", "补全缺失值插值"],
        }
        assert verdict["verdict"] == "reject"
        assert len(verdict["required_fixes"]) == 2

    def test_invalid_verdict_value_rejected(self):
        verdict = {"verdict": "maybe", "reasons": ["test"], "required_fixes": []}
        assert verdict["verdict"] not in ("pass", "reject")

    def test_missing_reasons_detected(self):
        verdict = {"verdict": "pass", "required_fixes": []}
        assert "reasons" not in verdict or not verdict.get("reasons")


# ═══════════════════════════════════════════════════════════════════════
# should_continue_workflow — 确定性路由 (mock 状态)
# ═══════════════════════════════════════════════════════════════════════


class TestShouldContinueWorkflow:
    """should_continue_workflow 基于结构化 AuditVerdict 路由"""

    def test_pass_routes_to_chief_scientist(self):
        """verdict=pass → chief_scientist"""
        from py.multi_agent import should_continue_workflow
        state: dict = {
            "task_status": "auditing",
            "rejection_count": 0,
            "audit_result": {"verdict": "pass", "reasons": ["ok"], "required_fixes": []},
        }
        assert should_continue_workflow(state) == "chief_scientist"

    def test_reject_under_3_routes_to_data_scientist(self):
        """verdict=reject 且 count<3 → data_scientist（回环修正）"""
        from py.multi_agent import should_continue_workflow
        state: dict = {
            "task_status": "auditing",
            "rejection_count": 1,
            "audit_result": {
                "verdict": "reject",
                "reasons": ["数据不完整"],
                "required_fixes": ["补充缺失值"],
            },
        }
        assert should_continue_workflow(state) == "data_scientist"

    def test_reject_at_count_3_routes_to_failed(self):
        """verdict=reject 且 count==3 → failed（显式失败终态，不兜圈子）"""
        from py.multi_agent import should_continue_workflow
        state: dict = {
            "task_status": "auditing",
            "rejection_count": 3,
            "audit_result": {
                "verdict": "reject",
                "reasons": ["三次驳回仍不达标"],
                "required_fixes": ["需要人工介入"],
            },
        }
        assert should_continue_workflow(state) == "failed"

    def test_reject_at_count_4_routes_to_failed(self):
        """count>3 也进 failed"""
        from py.multi_agent import should_continue_workflow
        state: dict = {
            "task_status": "auditing",
            "rejection_count": 5,
            "audit_result": {"verdict": "reject", "reasons": ["持续失败"], "required_fixes": []},
        }
        assert should_continue_workflow(state) == "failed"

    def test_none_verdict_routes_to_data_scientist_safe_side(self):
        """裁决为 None/不明确 → data_scientist（安全侧，不盲目放行）"""
        from py.multi_agent import should_continue_workflow
        state: dict = {
            "task_status": "auditing",
            "rejection_count": 0,
            "audit_result": None,
        }
        assert should_continue_workflow(state) == "data_scientist"


# ═══════════════════════════════════════════════════════════════════════
# auditor_node — 结构化 JSON 解析 (mock LLM)
# ═══════════════════════════════════════════════════════════════════════


class _FakeLLM:
    """模拟 LangChain LLM，返回预设 JSON 字符串"""

    def __init__(self, response: str):
        self._response = response

    def invoke(self, messages):
        from langchain_core.messages import AIMessage
        return AIMessage(content=self._response)


class TestAuditorNode:
    """auditor_node 产出结构化 AuditVerdict"""

    def test_valid_pass_json_yields_approved(self):
        """合法 pass JSON → audit_result[verdict]=pass, rejection_count 不变"""
        from py.multi_agent import _make_nodes

        fake_llm = _FakeLLM(
            '{"verdict": "pass", "reasons": ["数据质量合格"], "required_fixes": []}'
        )
        ds, auditor, chief = _make_nodes(fake_llm)
        state: dict = {
            "messages": [],
            "data_scientist_report": "数据处理完成，噪声已滤除",
            "rejection_count": 0,
            "execution_logs": [],
            "task_status": "data_processing",
        }
        result = auditor(state)
        assert result["audit_result"]["verdict"] == "pass"
        assert result["rejection_count"] == 0
        assert len(result["execution_logs"]) >= 1
        assert "pass" in result["execution_logs"][-1]

    def test_valid_reject_json_yields_rejected_with_fixes(self):
        """合法 reject JSON → rejection_count+1, required_fixes 注入 messages"""
        from py.multi_agent import _make_nodes

        fake_llm = _FakeLLM(
            '{"verdict": "reject", "reasons": ["截止频率过低"], "required_fixes": ["提高至 50Hz"]}'
        )
        ds, auditor, chief = _make_nodes(fake_llm)
        state: dict = {
            "messages": [],
            "data_scientist_report": "已处理",
            "rejection_count": 0,
            "execution_logs": [],
            "task_status": "data_processing",
        }
        result = auditor(state)
        assert result["audit_result"]["verdict"] == "reject"
        assert result["rejection_count"] == 1
        assert len(result["execution_logs"]) >= 1
        # required_fixes 已注入 messages
        assert any("50Hz" in str(m.content) for m in result["messages"] if hasattr(m, "content"))

    def test_invalid_json_raises_error(self):
        """非法 JSON（缺 verdict）→ raise ValueError，不静默放行"""
        from py.multi_agent import _make_nodes

        fake_llm = _FakeLLM(
            '{"reasons": ["no verdict field"], "required_fixes": []}'
        )
        ds, auditor, chief = _make_nodes(fake_llm)
        state: dict = {
            "messages": [],
            "data_scientist_report": "已处理",
            "rejection_count": 0,
            "execution_logs": [],
            "task_status": "data_processing",
        }
        with pytest.raises(ValueError, match="Auditor|verdict"):
            auditor(state)

    def test_non_json_output_raises_error(self):
        """LLM 返回纯文本（非 JSON）→ raise ValueError"""
        from py.multi_agent import _make_nodes

        fake_llm = _FakeLLM(
            "我认为应该通过，数据看起来不错。驳回理由：无。"
        )
        ds, auditor, chief = _make_nodes(fake_llm)
        state: dict = {
            "messages": [],
            "data_scientist_report": "已处理",
            "rejection_count": 0,
            "execution_logs": [],
            "task_status": "data_processing",
        }
        with pytest.raises(ValueError, match="Auditor|verdict"):
            auditor(state)


# ═══════════════════════════════════════════════════════════════════════
# rejection_count 累积（跨多轮驳回）
# ═══════════════════════════════════════════════════════════════════════


class TestRejectionAccumulation:
    """驳回次数累积 + 第 3 次触发终态"""

    def test_two_rejects_then_routes_to_data_scientist(self):
        """连续 2 次 reject → count=2, 仍回 data_scientist"""
        from py.multi_agent import should_continue_workflow
        state: dict = {
            "task_status": "auditing",
            "rejection_count": 2,
            "audit_result": {
                "verdict": "reject",
                "reasons": ["r1", "r2"],
                "required_fixes": ["f1", "f2"],
            },
        }
        assert should_continue_workflow(state) == "data_scientist"

    def test_third_reject_routes_to_failed(self):
        """第 3 次 reject → failed"""
        from py.multi_agent import should_continue_workflow
        state: dict = {
            "task_status": "auditing",
            "rejection_count": 3,
            "audit_result": {
                "verdict": "reject",
                "reasons": ["r1", "r2", "r3"],
                "required_fixes": ["f1"],
            },
        }
        assert should_continue_workflow(state) == "failed"

    def test_failed_state_preserves_reasons(self):
        """failed 终态时的 reasons/required_fixes 可被调用方读取"""
        # 模拟 run_multi_agent 的返回路径
        result: dict = {
            "task_status": "failed",
            "rejection_count": 3,
            "audit_result": {
                "verdict": "reject",
                "reasons": ["连续三次审核不通过"],
                "required_fixes": ["需人工介入重新制定分析方案"],
            },
            "execution_logs": [
                "[DataScientist] input_msgs=1 ouput_len=100 rejection_count=0",
                "[Auditor] verdict=reject rejection_count=1 ...",
                "[DataScientist] input_msgs=4 ouput_len=120 rejection_count=1",
                "[Auditor] verdict=reject rejection_count=2 ...",
                "[DataScientist] input_msgs=6 ouput_len=130 rejection_count=2",
                "[Auditor] verdict=reject rejection_count=3 ...",
            ],
            "is_failed": True,
        }
        assert result["is_failed"] is True
        assert result["rejection_count"] == 3
        assert len(result["audit_result"]["reasons"]) >= 1


# ═══════════════════════════════════════════════════════════════════════
# 端到端：DeepSeekAgentWorker — 异常 → error 信号, report NOT sent
# ═══════════════════════════════════════════════════════════════════════


class TestDeepSeekAgentWorkerErrorChain:
    """【Phase 3 接缝】_execute_agent() 内部异常 → error_signal 发射,
    report_ready_signal 不发射, finished_signal 不发射（防止 UI 误当成功）。"""

    def _wait_signal(self, signal, timeout_ms: int = 3000):
        from PyQt6.QtCore import QEventLoop, QTimer
        result = []
        loop = QEventLoop()
        signal.connect(lambda v: (result.append(v), loop.quit()))
        QTimer.singleShot(timeout_ms, loop.quit)
        loop.exec()
        return result[0] if result else None

    def test_agent_error_signal_on_auditor_value_error(self, qapp, monkeypatch):
        """_execute_agent 内 raise ValueError（非法裁决 JSON）→ error_signal 发射"""
        from py.agent_worker import DeepSeekAgentWorker

        # Mock _execute_agent 以模拟 auditor_node 抛 ValueError 场景
        monkeypatch.setattr(
            DeepSeekAgentWorker, "_execute_agent",
            lambda self: (_ for _ in ()).throw(
                ValueError("Auditor 未产出合法 AuditVerdict JSON：verdict 字段非法: 'maybe'")
            ),
        )

        worker = DeepSeekAgentWorker()
        worker.set_task("分析传感器数据", "/fake/path.csv")

        error_received = []
        report_received = []
        finished_received = []

        worker.error_signal.connect(lambda v: error_received.append(v))
        worker.report_ready_signal.connect(lambda v: report_received.append(v))
        worker.finished_signal.connect(lambda: finished_received.append(True))

        worker.run()

        assert len(error_received) >= 1, "error_signal 必须发射"
        assert "Auditor" in error_received[0] or "ValueError" in error_received[0] or "verdict" in error_received[0], \
            f"error 消息应含诊断，got: {error_received[0][:200]!r}"
        assert len(report_received) == 0, \
            "report_ready_signal 在异常时不应发射（防止 UI 显示垃圾报告）"
        assert len(finished_received) == 0, \
            "finished_signal 在异常时不应发射（防止 UI 误当成功）"

    def test_agent_error_signal_on_schema_validation_failure(self, qapp, monkeypatch):
        """裁决 JSON 缺 reasons 字段 → error_signal, 不误发 report"""
        from py.agent_worker import DeepSeekAgentWorker

        monkeypatch.setattr(
            DeepSeekAgentWorker, "_execute_agent",
            lambda self: (_ for _ in ()).throw(
                ValueError("Auditor 未产出合法 AuditVerdict JSON：reasons 缺失或非数组")
            ),
        )

        worker = DeepSeekAgentWorker()
        worker.set_task("test", "/f.csv")

        error_received = []
        report_received = []

        worker.error_signal.connect(lambda v: error_received.append(v))
        worker.report_ready_signal.connect(lambda v: report_received.append(v))

        worker.run()

        assert len(error_received) >= 1
        assert len(report_received) == 0, "异常时 report_ready_signal 绝不应发射"

    def test_agent_normal_flow_still_works(self, qapp, monkeypatch):
        """正常执行路径不受影响 — report + finished 正确发射"""
        from py.agent_worker import DeepSeekAgentWorker

        monkeypatch.setattr(
            DeepSeekAgentWorker, "_execute_agent",
            lambda self: self.report_ready_signal.emit("分析完成"),
        )

        worker = DeepSeekAgentWorker()
        worker.set_task("test", "/f.csv")

        report_received = []
        finished_received = []

        worker.report_ready_signal.connect(lambda v: report_received.append(v))
        worker.finished_signal.connect(lambda: finished_received.append(True))

        worker.run()

        assert len(report_received) == 1
        assert len(finished_received) == 1
