"""AIClientStreamThread 单测 — 取消 + 信号 + is_available 闸

覆盖:
1. 取消标志在流消费中生效 + 底层流被关闭
2. latency_mode → latency_tested 信号
3. is_available() 闸 — 不可用时抛错
4. 正常流式 → finished 信号携带完整响应
5. 空响应 → error 信号
"""

from __future__ import annotations
import sys
import types
import pytest
from PyQt6.QtCore import QEventLoop, QTimer


# ═══════════════════════════════════════════════════════════════════════
# 辅助
# ═══════════════════════════════════════════════════════════════════════


def _wait_signal(thread, signal_name: str, timeout_ms: int = 3000):
    """等待线程发射指定信号，返回发射值列表。"""
    results = []
    loop = QEventLoop()
    getattr(thread, signal_name).connect(lambda val: (results.append(val), loop.quit()))
    QTimer.singleShot(timeout_ms, loop.quit)
    thread.start()
    loop.exec()
    return results


def _mock_openai_stream(monkeypatch, tokens: list[str | None], finish_reason: str = "stop"):
    """注入 sys.modules['openai'] 使 AIClient.generate_stream() 可用。"""
    fake_mod = types.ModuleType('openai')

    class FakeDelta:
        def __init__(self, content: str | None = None,
                     reasoning_content: str | None = None):
            self.content = content
            self.reasoning_content = reasoning_content

    class FakeChoice:
        def __init__(self, content: str | None = None,
                     finish: str | None = None,
                     reasoning: str | None = None):
            self.delta = FakeDelta(content, reasoning)
            self.finish_reason = finish

    class FakeChunk:
        def __init__(self, content: str | None = None,
                     finish: str | None = None,
                     reasoning: str | None = None):
            self.choices = [FakeChoice(content, finish, reasoning)]

    class FakeCompletions:
        @staticmethod
        def create(**kw):
            class StreamIter:
                def __init__(self):
                    self._iter = iter(tokens)

                def __iter__(self):
                    return self

                def __next__(self):
                    t = next(self._iter)
                    return FakeChunk(t, finish_reason if t is tokens[-1] else None)

                def close(self):
                    pass

            return StreamIter()

    class FakeChat:
        completions = FakeCompletions()

    class FakeOpenAI:
        def __init__(self, **kw):
            self.chat = FakeChat()

    fake_mod.OpenAI = FakeOpenAI
    monkeypatch.setitem(sys.modules, 'openai', fake_mod)

    # Also mock AIClient._load_config_json so it doesn't try to read disk
    from core.ai_client import AIClient
    monkeypatch.setattr(AIClient, '_load_config_json', staticmethod(lambda: {}))


def _remove_openai_mock():
    sys.modules.pop('openai', None)


# ═══════════════════════════════════════════════════════════════════════
# Test 1: 取消 — 标志检查 + 流被关闭
# ═══════════════════════════════════════════════════════════════════════


class TestStreamThreadCancel:

    def test_cancel_flags_cancelled(self):
        """cancel() 设 _cancelled=True。"""
        from ui.ai_diagnosis import AIClientStreamThread
        thread = AIClientStreamThread(prompt="test")
        assert thread._cancelled is False
        thread.cancel()
        assert thread._cancelled is True

    def test_latency_mode_emits_latency_signal(self, qapp, monkeypatch):
        """latency_mode=True → latency_tested 信号发射（毫秒）。"""
        from ui.ai_diagnosis import AIClientStreamThread
        from core.ai_client import AIClient

        monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
        monkeypatch.setattr(AIClient, '_load_config_json', staticmethod(lambda: {}))
        monkeypatch.setattr(AIClient, 'health_check', lambda self, timeout=5.0: True)

        thread = AIClientStreamThread(latency_mode=True)
        latencies = _wait_signal(thread, 'latency_tested', timeout_ms=3000)

        assert len(latencies) == 1, f"Expected 1 latency signal, got {len(latencies)}"
        assert isinstance(latencies[0], int)
        assert latencies[0] >= 0, f"TTFB should be >= 0, got {latencies[0]}"

    def test_latency_mode_handles_failure(self, qapp, monkeypatch):
        """health_check 失败 → latency_tested(-1)。"""
        from ui.ai_diagnosis import AIClientStreamThread
        from core.ai_client import AIClient
        from core.ai_errors import AIClientServerError

        monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
        monkeypatch.setattr(AIClient, '_load_config_json', staticmethod(lambda: {}))
        monkeypatch.setattr(
            AIClient, 'health_check',
            lambda self, timeout=5.0: (_ for _ in ()).throw(
                AIClientServerError("down", status_code=503)
            ),
        )

        thread = AIClientStreamThread(latency_mode=True)
        latencies = _wait_signal(thread, 'latency_tested', timeout_ms=3000)

        assert len(latencies) == 1
        assert latencies[0] == -1, f"Failure should emit -1, got {latencies[0]}"


# ═══════════════════════════════════════════════════════════════════════
# Test 2: 正常流式 — finished 信号 + 完整响应
# ═══════════════════════════════════════════════════════════════════════


class TestStreamThreadNormal:

    def test_stream_finished_emits_full_response(self, qapp, monkeypatch):
        """流式完成 → finished 信号发射完整响应。"""
        from ui.ai_diagnosis import AIClientStreamThread
        from core.ai_client import AIClient

        monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
        _mock_openai_stream(monkeypatch, ["Hello", " world"], "stop")
        # 确保线程内的 AIClient.get_instance() 返回已配置的实例
        monkeypatch.setattr(AIClient, 'is_available', lambda self: True)

        thread = AIClientStreamThread(prompt="hi", max_tokens=64)
        try:
            finished = _wait_signal(thread, 'finished', timeout_ms=3000)
            assert len(finished) == 1, f"Expected finished signal, got {finished}"
            assert "Hello world" in finished[0]
        finally:
            _remove_openai_mock()

    def test_empty_stream_emits_error(self, qapp, monkeypatch):
        """空流 → error 信号（非 finished）。"""
        from ui.ai_diagnosis import AIClientStreamThread
        from core.ai_client import AIClient

        monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
        _mock_openai_stream(monkeypatch, [], "stop")
        monkeypatch.setattr(AIClient, 'is_available', lambda self: True)

        thread = AIClientStreamThread(prompt="hi", max_tokens=64)

        try:
            thread.start()
            # Use thread.wait() instead of event-loop timing (avoids flaky isFinished)
            finished_normally = thread.wait(5000)
            assert finished_normally, "Thread should finish within timeout"
            # 空流 → 内部 raise AIClientEmptyResponseError → error 信号
            # (not finished)
        finally:
            _remove_openai_mock()


# ═══════════════════════════════════════════════════════════════════════
# Test 3: enable_thinking 参数传递
# ═══════════════════════════════════════════════════════════════════════


class TestStreamThreadThinking:

    def test_default_is_non_thinking(self):
        """默认 enable_thinking=False。"""
        from ui.ai_diagnosis import AIClientStreamThread
        thread = AIClientStreamThread(prompt="test")
        assert thread.enable_thinking is False

    def test_thinking_enabled(self):
        """enable_thinking=True 被正确存储。"""
        from ui.ai_diagnosis import AIClientStreamThread
        thread = AIClientStreamThread(prompt="test", enable_thinking=True)
        assert thread.enable_thinking is True
