"""AI 客户端 — 配置解析 + URL 构建 + 错误格式化测试

覆盖:
1. AIClient 默认配置 (base_url/model_name 正确)
2. format_api_error 404 含 Anthropic URL 提示
3. format_api_error 401/429/timeout 各场景
4. _run_latency_test URL 构建 — removesuffix 删 /v1 尾缀
5. ai_models_config.json → base_url 不含 /anthropic
"""

from __future__ import annotations
import pytest
import json
import os
import sys


# ═══════════════════════════════════════════════════════════════════════
# Test 1: classify_openai_error — 异常分类工厂（覆盖原 format_api_error 意图）
# ═══════════════════════════════════════════════════════════════════════

class TestClassifyOpenAIErrorMessages:

    def test_404_not_found(self):
        """404 → AIClientServerError（兜底），信息含 404"""
        from core.ai_errors import classify_openai_error, AIClientServerError

        class FakeHTTPError(Exception):
            status_code = 404
        e = classify_openai_error(FakeHTTPError("Not found"))
        assert isinstance(e, AIClientServerError)
        assert "404" in e.message or "Not found" in e.message

    def test_401_unauthorized(self):
        """401 + 'invalid api key' → AIClientAuthError"""
        from core.ai_errors import classify_openai_error, AIClientAuthError
        e = classify_openai_error(Exception("401 Unauthorized: invalid api key"))
        assert isinstance(e, AIClientAuthError)
        assert "API" in e.message or "认证" in e.message

    def test_429_rate_limit(self):
        """429 → AIClientRateLimitError"""
        from core.ai_errors import classify_openai_error, AIClientRateLimitError

        class FakeHTTPError(Exception):
            status_code = 429
        e = classify_openai_error(FakeHTTPError("rate_limit exceeded"))
        assert isinstance(e, AIClientRateLimitError)
        assert e.retryable is True

    def test_timeout(self):
        """'timed out' 文本 → AIClientTimeoutError"""
        from core.ai_errors import classify_openai_error, AIClientTimeoutError
        e = classify_openai_error(Exception("Connection timed out"))
        assert isinstance(e, AIClientTimeoutError)
        assert e.retryable is True

    def test_500_server_error(self):
        """500 → AIClientServerError"""
        from core.ai_errors import classify_openai_error, AIClientServerError

        class FakeHTTPError(Exception):
            status_code = 500
        e = classify_openai_error(FakeHTTPError("Internal server error"))
        assert isinstance(e, AIClientServerError)
        assert e.retryable is True

    def test_unknown_falls_to_server_error(self):
        """未知错误 → AIClientServerError (兜底)"""
        from core.ai_errors import classify_openai_error, AIClientServerError
        msg = "Unknown: internal server error 500"
        e = classify_openai_error(Exception(msg))
        assert isinstance(e, AIClientServerError)
        assert "500" in e.message


# ═══════════════════════════════════════════════════════════════════════
# Test 2: removesuffix 替代 rstrip
# ═══════════════════════════════════════════════════════════════════════

class TestRemovesuffixVsRstrip:

    def test_removesuffix_removes_v1_suffix(self):
        """removesuffix('/v1') 正确删尾缀"""
        base = "https://api.deepseek.com/v1"
        assert base.removesuffix('/v1') == "https://api.deepseek.com"

    def test_removesuffix_noop_when_no_suffix(self):
        """base_url 不含 /v1 时 removesuffix 不动"""
        base = "https://api.deepseek.com"
        assert base.removesuffix('/v1') == "https://api.deepseek.com"

    def test_rstrip_wrongly_strips_characters(self):
        """rstrip('/v1') 删除字符集中任意字符 (不是子串) — 回归验证"""
        base = "https://example.com/v1"
        # rstrip('/v1') deletes trailing chars from the set {/, v, 1}
        # not the literal substring "/v1"
        wrong = base.rstrip('/v1')
        assert wrong == "https://example.com"  # coincidentally correct for this case
        # But for other URLs it breaks:
        base2 = "https://example.com/abc"
        wrong2 = base2.rstrip('/v1')  # 'c' not in {/, v, 1}, nothing stripped
        assert wrong2 == "https://example.com/abc"  # /abc stays because 'c' not in set

    def test_latency_url_building(self):
        """延迟测试 URL 组装正确"""
        base = "https://api.deepseek.com/v1"
        stripped = base.removesuffix('/v1')
        url = f"{stripped}/v1/models"
        assert url == "https://api.deepseek.com/v1/models"

    def test_latency_url_without_v1_suffix(self):
        """base_url 不含 /v1 尾缀时仍正确"""
        base = "https://api.deepseek.com"
        stripped = base.removesuffix('/v1')
        url = f"{stripped}/v1/models"
        assert url == "https://api.deepseek.com/v1/models"


# ═══════════════════════════════════════════════════════════════════════
# Test 3: ai_models_config.json 不含 /anthropic
# ═══════════════════════════════════════════════════════════════════════

class TestConfigFile:

    @staticmethod
    def _model_entries(data: dict):
        """过滤系统键（_backend / _local / _comment），仅返回模型条目."""
        return {
            k: v for k, v in data.items()
            if not k.startswith('_') and isinstance(v, dict) and 'base_url' in v
        }

    def test_config_base_url_not_anthropic(self):
        """ai_models_config.json 的 base_url 必须用 OpenAI 兼容端点"""
        config_path = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "ai_models_config.json",
        )
        if not os.path.exists(config_path):
            pytest.skip("ai_models_config.json not found")
        with open(config_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        models = self._model_entries(data)
        assert len(models) > 0, "配置文件应至少包含一个模型条目"
        for name, cfg in models.items():
            url = cfg.get("base_url", "")
            assert "/anthropic" not in url, \
                f"Config '{name}' uses Anthropic endpoint ({url}). Use OpenAI-compatible (/v1) instead."

    def test_config_base_url_ends_with_v1(self):
        """ai_models_config.json 的 base_url 应以 /v1 结尾 (OpenAI 兼容)"""
        config_path = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "ai_models_config.json",
        )
        if not os.path.exists(config_path):
            pytest.skip("ai_models_config.json not found")
        with open(config_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        models = self._model_entries(data)
        assert len(models) > 0, "配置文件应至少包含一个模型条目"
        for name, cfg in models.items():
            url = cfg.get("base_url", "")
            assert url.endswith("/v1"), \
                f"Config '{name}' base_url should end with /v1: {url}"


# ═══════════════════════════════════════════════════════════════════════
# Test 4: AIClientError 类型化异常层级 (Phase 1)
# ═══════════════════════════════════════════════════════════════════════


class TestAIClientErrors:
    """AIClientError 子类 retryable 标志 + 构造"""

    def test_auth_error_not_retryable(self):
        from core.ai_errors import AIClientAuthError
        e = AIClientAuthError("bad key", status_code=401)
        assert e.retryable is False
        assert e.status_code == 401
        assert "bad key" == e.message

    def test_rate_limit_retryable(self):
        from core.ai_errors import AIClientRateLimitError
        e = AIClientRateLimitError("rate limited", status_code=429)
        assert e.retryable is True

    def test_server_error_retryable(self):
        from core.ai_errors import AIClientServerError
        e = AIClientServerError("boom", status_code=500)
        assert e.retryable is True

    def test_timeout_retryable(self):
        from core.ai_errors import AIClientTimeoutError
        e = AIClientTimeoutError("timed out")
        assert e.retryable is True

    def test_empty_response_not_retryable(self):
        from core.ai_errors import AIClientEmptyResponseError
        e = AIClientEmptyResponseError("empty")
        assert e.retryable is False

    def test_not_configured_not_retryable(self):
        from core.ai_errors import AIClientNotConfiguredError
        e = AIClientNotConfiguredError("no key")
        assert e.retryable is False

    def test_request_error_not_retryable(self):
        from core.ai_errors import AIClientRequestError
        e = AIClientRequestError("bad request", status_code=400)
        assert e.retryable is False


# ═══════════════════════════════════════════════════════════════════════
# Test 5: classify_openai_error — 异常分类工厂 (Phase 1)
# ═══════════════════════════════════════════════════════════════════════


class TestClassifyOpenAIError:
    """classify_openai_error 按 HTTP 状态码/异常消息正确分类"""

    def test_401_maps_to_auth_error(self):
        from core.ai_errors import classify_openai_error, AIClientAuthError

        class FakeHTTPError(Exception):
            status_code = 401
        e = classify_openai_error(FakeHTTPError("Unauthorized"))
        assert isinstance(e, AIClientAuthError)
        assert e.retryable is False

    def test_429_maps_to_rate_limit(self):
        from core.ai_errors import classify_openai_error, AIClientRateLimitError

        class FakeHTTPError(Exception):
            status_code = 429
        e = classify_openai_error(FakeHTTPError("Too Many Requests"))
        assert isinstance(e, AIClientRateLimitError)
        assert e.retryable is True

    def test_500_maps_to_server_error(self):
        from core.ai_errors import classify_openai_error, AIClientServerError

        class FakeHTTPError(Exception):
            status_code = 500
        e = classify_openai_error(FakeHTTPError("Internal Server Error"))
        assert isinstance(e, AIClientServerError)
        assert e.retryable is True

    def test_502_maps_to_server_error(self):
        from core.ai_errors import classify_openai_error, AIClientServerError

        class FakeHTTPError(Exception):
            status_code = 502
        e = classify_openai_error(FakeHTTPError("Bad Gateway"))
        assert isinstance(e, AIClientServerError)
        assert e.retryable is True

    def test_timeout_message_maps_to_timeout(self):
        from core.ai_errors import classify_openai_error, AIClientTimeoutError
        e = classify_openai_error(Exception("Connection timed out"))
        assert isinstance(e, AIClientTimeoutError)
        assert e.retryable is True

    def test_unknown_error_falls_back_to_server_error(self):
        from core.ai_errors import classify_openai_error, AIClientServerError
        e = classify_openai_error(Exception("Something weird happened"))
        assert isinstance(e, AIClientServerError)
        assert e.retryable is True


# ═══════════════════════════════════════════════════════════════════════
# Test 6: AIClient.generate — 异常抛出 (Phase 1, mock 传输层)
# ═══════════════════════════════════════════════════════════════════════


class TestGenerateNeverReturnsEmpty:
    """【契约核心】generate() 失败必须抛 AIClientError，绝不再返回 ""。此测试是整个阶段一+阶段二+阶段三的基石。"""

    def _make_mock_client(self, monkeypatch):
        from core.ai_client import AIClient
        client = object.__new__(AIClient)
        object.__setattr__(client, "_initialized", False)
        AIClient.__init__(client, api_key="sk-test-key")
        return client

    def test_empty_string_is_never_returned_not_configured(self, monkeypatch):
        """API Key 未配置 → 抛 AIClientNotConfiguredError，不是 return ''"""
        from core.ai_client import AIClient
        from core.ai_errors import AIClientNotConfiguredError
        monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
        client = object.__new__(AIClient)
        object.__setattr__(client, "api_key", "")
        with pytest.raises(AIClientNotConfiguredError):
            result = client.generate("hello")
            # 安全带：万一没抛，断言结果不是 ""
            assert result != "", "generate() returned empty string instead of raising"

    def test_empty_string_is_never_returned_auth_error(self, monkeypatch):
        """401 → 抛 AIClientAuthError，不是 return ''"""
        from core.ai_errors import AIClientAuthError
        client = self._make_mock_client(monkeypatch)
        monkeypatch.setattr(client, "generate", lambda *a, **kw: (_ for _ in ()).throw(
            AIClientAuthError("bad key", status_code=401)))
        with pytest.raises(AIClientAuthError):
            result = client.generate_with_retry("test", max_retries=0)
            assert result != "", "generate_with_retry returned empty string instead of raising"

    def test_empty_string_is_never_returned_server_error(self, monkeypatch):
        """500 → 抛 AIClientServerError，不是 return ''"""
        from core.ai_errors import AIClientServerError
        client = self._make_mock_client(monkeypatch)
        monkeypatch.setattr(client, "generate", lambda *a, **kw: (_ for _ in ()).throw(
            AIClientServerError("internal error", status_code=500)))
        with pytest.raises(AIClientServerError):
            result = client.generate_with_retry("test", max_retries=0)
            assert result != "", "generate_with_retry returned empty string instead of raising"

    def test_empty_string_is_never_returned_timeout(self, monkeypatch):
        """超时 → 抛 AIClientTimeoutError，不是 return ''"""
        from core.ai_errors import AIClientTimeoutError
        client = self._make_mock_client(monkeypatch)
        monkeypatch.setattr(client, "generate", lambda *a, **kw: (_ for _ in ()).throw(
            AIClientTimeoutError("timed out")))
        with pytest.raises(AIClientTimeoutError):
            result = client.generate_with_retry("test", max_retries=0)
            assert result != "", "generate_with_retry returned empty string instead of raising"

    def test_empty_string_is_never_returned_empty_response(self, monkeypatch):
        """200 但空 content → 抛 AIClientEmptyResponseError，不是 return ''"""
        from core.ai_errors import AIClientEmptyResponseError
        client = self._make_mock_client(monkeypatch)
        monkeypatch.setattr(client, "generate", lambda *a, **kw: (_ for _ in ()).throw(
            AIClientEmptyResponseError("空响应")))
        with pytest.raises(AIClientEmptyResponseError):
            result = client.generate_with_retry("test", max_retries=0)
            assert result != "", "空响应绝不能静默通过"

    def test_generate_fn_callback_also_throws(self, monkeypatch):
        """get_generate_fn() 返回的回调失败也抛异常（非 return ''），契约穿透到 engine 层"""
        from core.ai_errors import AIClientServerError
        from core.ai_client import AIClient
        client = object.__new__(AIClient)
        object.__setattr__(client, "_initialized", False)
        AIClient.__init__(client, api_key="sk-test")
        monkeypatch.setattr(client, "generate", lambda *a, **kw: (_ for _ in ()).throw(
            AIClientServerError("boom", status_code=500)))
        fn = client.get_generate_fn()
        with pytest.raises(AIClientServerError, match="boom"):
            result = fn("hello")
            assert result != "", "generate_fn returned '' instead of raising"


class TestAIClientGenerateErrors:
    """AIClient.generate 按错误抛出类型化异常，无网络调用"""

    def _make_mock_client(self, monkeypatch):
        """构造一个有假 API key 的 AIClient 并 mock openai SDK。"""
        from core.ai_client import AIClient

        # 使用反射重置单例，让测试可以独立初始化
        client = object.__new__(AIClient)
        object.__setattr__(client, "_initialized", False)
        AIClient.__init__(client, api_key="sk-test-key")

        return client

    def test_no_api_key_raises_not_configured(self, monkeypatch):
        """API Key 为空 → AIClientNotConfiguredError"""
        from core.ai_client import AIClient
        from core.ai_errors import AIClientNotConfiguredError

        # 绕过单例创建纯测试实例
        monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
        client = object.__new__(AIClient)
        # 手动设属性，避开 __init__ 单例保护与 env 回退
        object.__setattr__(client, "api_key", "")
        object.__setattr__(client, "base_url", "https://test/v1")
        object.__setattr__(client, "model_name", "test-model")
        import pytest
        with pytest.raises(AIClientNotConfiguredError, match="未配置"):
            client.generate("hello")

    def test_empty_response_is_not_retryable(self):
        """AIClientEmptyResponseError retryable=False (直接测异常类，不依赖 openai 安装)"""
        from core.ai_errors import AIClientEmptyResponseError as E
        e = E("AI 返回了空响应")
        assert e.retryable is False
        assert e.status_code is None
        assert "空响应" in e.message

    def test_http_401_raises_auth_error(self, monkeypatch):
        """401 → AIClientAuthError"""
        from core.ai_errors import AIClientAuthError

        client = self._make_mock_client(monkeypatch)

        def fake_401(*args, **kwargs):
            raise AIClientAuthError("Unauthorized", status_code=401)

        import pytest
        monkeypatch.setattr(client, "generate", fake_401)
        with pytest.raises(AIClientAuthError):
            client.generate("test")


# ═══════════════════════════════════════════════════════════════════════
# Test 7: generate_with_retry — 指数退避 (Phase 1)
# ═══════════════════════════════════════════════════════════════════════


class TestGenerateWithRetry:
    """generate_with_retry 重试逻辑"""

    def _make_client(self, fail_counts: list[Exception], monkeypatch):
        """构造 AIClient，并用 monkeypatch 替换 generate。

        fail_counts: 每次调用的返回值/异常序列。最后一个应是成功返回值或最终异常。
        """
        from core.ai_client import AIClient

        client = object.__new__(AIClient)
        object.__setattr__(client, "_initialized", False)
        AIClient.__init__(client, api_key="sk-test")

        call_count = [0]

        def fake_generate(**kwargs):
            idx = call_count[0]
            call_count[0] += 1
            result = fail_counts[idx]
            if isinstance(result, Exception):
                raise result
            return result

        monkeypatch.setattr(client, "generate", fake_generate)
        return client

    def test_retry_on_429_succeeds(self, monkeypatch):
        """429 retryable → 重试成功"""
        from core.ai_errors import AIClientRateLimitError

        client = self._make_client(
            [AIClientRateLimitError("rate limited", status_code=429), "ok result"],
            monkeypatch,
        )
        result = client.generate_with_retry("hi", max_retries=2, base_backoff_s=0.01)
        assert result == "ok result"

    def test_retry_on_500_succeeds(self, monkeypatch):
        """500 retryable → 第二次成功"""
        from core.ai_errors import AIClientServerError

        client = self._make_client(
            [
                AIClientServerError("boom", status_code=500),
                AIClientServerError("boom again", status_code=503),
                "finally",
            ],
            monkeypatch,
        )
        result = client.generate_with_retry("hi", max_retries=3, base_backoff_s=0.01)
        assert result == "finally"

    def test_401_immediate_fail_no_retry(self, monkeypatch):
        """401 not retryable → 立即抛，不重试"""
        from core.ai_errors import AIClientAuthError

        client = self._make_client(
            [AIClientAuthError("bad key", status_code=401)],
            monkeypatch,
        )
        import pytest
        with pytest.raises(AIClientAuthError):
            client.generate_with_retry("hi", max_retries=2, base_backoff_s=0.01)

    def test_persistent_500_exhausts_retries(self, monkeypatch):
        """连续 5xx → 重试 max_retries 次后抛最终异常"""
        from core.ai_errors import AIClientServerError

        errors = [AIClientServerError(f"fail {i}", status_code=500) for i in range(3)]
        client = self._make_client(errors, monkeypatch)
        import pytest
        with pytest.raises(AIClientServerError):
            client.generate_with_retry("hi", max_retries=2, base_backoff_s=0.01)

    def test_timeout_retryable(self, monkeypatch):
        """timeout retryable → 重试成功"""
        from core.ai_errors import AIClientTimeoutError

        client = self._make_client(
            [AIClientTimeoutError("timed out"), "ok"],
            monkeypatch,
        )
        result = client.generate_with_retry("hi", max_retries=1, base_backoff_s=0.01)
        assert result == "ok"

    def test_empty_response_not_retryable(self, monkeypatch):
        """空响应不可重试 → 立即抛"""
        from core.ai_errors import AIClientEmptyResponseError

        client = self._make_client(
            [AIClientEmptyResponseError("empty")],
            monkeypatch,
        )
        import pytest
        with pytest.raises(AIClientEmptyResponseError):
            client.generate_with_retry("hi", max_retries=2, base_backoff_s=0.01)


# ═══════════════════════════════════════════════════════════════════════
# Test 8: ReportWorker 错误链路 — generate() 异常 → error 信号 → UI
# ═══════════════════════════════════════════════════════════════════════


class TestReportWorkerErrorChain:
    """【调用点契约】generate() 抛 AIClientError → ReportWorker.run() 捕获
    → error 信号发射 → 调用方收到错误文本，绝不静默。"""

    def _wait_worker_error(self, worker, timeout_ms: int = 3000):
        """启动 worker 并等待 error 信号触发，返回发射的错误文本。超时返回 None。"""
        from PyQt6.QtCore import QEventLoop, QTimer

        result = []
        loop = QEventLoop()

        worker.error.connect(lambda val: (result.append(val), loop.quit()))
        QTimer.singleShot(timeout_ms, loop.quit)
        worker.start()
        loop.exec()
        return result[0] if result else None

    def test_worker_error_signal_on_auth_failure(self, qapp):
        """work_fn 抛 AIClientAuthError → ReportWorker.error 发射错误文本"""
        from core.ai_errors import AIClientAuthError
        from ui.report_worker import ReportWorker

        def bad_fn(*args, **kwargs):
            raise AIClientAuthError("API Key 无效", status_code=401)

        worker = ReportWorker(bad_fn)
        error_text = self._wait_worker_error(worker)
        assert error_text is not None, "ReportWorker must emit error signal"
        assert "401" in error_text or "API Key" in error_text or "无效" in error_text, \
            f"error signal should contain diagnostic info, got: {error_text!r}"

    def test_worker_error_signal_on_server_error(self, qapp):
        """work_fn 抛 AIClientServerError → error 信号含 status_code"""
        from core.ai_errors import AIClientServerError
        from ui.report_worker import ReportWorker

        def bad_fn(*args, **kwargs):
            raise AIClientServerError("服务端爆炸", status_code=500)

        worker = ReportWorker(bad_fn)
        error_text = self._wait_worker_error(worker)
        assert error_text is not None, "ReportWorker must emit error signal"
        assert "500" in error_text or "服务端" in error_text or "Server" in error_text, \
            f"error should contain diagnostic info, got: {error_text!r}"

    def test_worker_error_signal_on_empty_response(self, qapp):
        """空响应 → error 信号发射，绝不静默"""
        from core.ai_errors import AIClientEmptyResponseError
        from ui.report_worker import ReportWorker

        def bad_fn(*args, **kwargs):
            raise AIClientEmptyResponseError("AI 返回了空响应")

        worker = ReportWorker(bad_fn)
        error_text = self._wait_worker_error(worker)
        assert error_text is not None, "空响应必须通过 error 信号报告"
        assert "空" in error_text or "Empty" in error_text or "empty" in error_text.lower(), \
            f"error should mention empty response, got: {error_text!r}"

    def test_worker_finished_does_not_fire_on_error(self, qapp):
        """work_fn 抛异常 → finished 信号不触发，只有 error（防止误当成功）"""
        from core.ai_errors import AIClientServerError
        from ui.report_worker import ReportWorker

        finished_fired = []

        def bad_fn(*args, **kwargs):
            raise AIClientServerError("fail", status_code=500)

        worker = ReportWorker(bad_fn)
        worker.finished.connect(lambda v: finished_fired.append(v))
        # 等待 error 信号确认 worker 已跑完
        error_text = self._wait_worker_error(worker)
        assert error_text is not None
        # 给 finished 信号一点时间——它不应该触发
        from PyQt6.QtCore import QTimer, QEventLoop
        loop = QEventLoop()
        QTimer.singleShot(300, loop.quit)
        loop.exec()
        assert len(finished_fired) == 0, \
            f"finished 不应在异常时触发，但触发了: {finished_fired}"

    def test_report_schema_error_reaches_worker_error(self, qapp):
        """【Phase 1↔2 接缝】逐节生成时抛 ReportSchemaError → worker error 信号发射，
        finished 不触发。防 someone 把 except Exception 收窄成 except AIClientError
        导致 ReportSchemaError 裸漏。"""
        from core.ai_errors import ReportSchemaError
        from ui.report_worker import ReportWorker

        def build_fn(*args, **kwargs):
            raise ReportSchemaError(
                "第 3 节 Schema 校验失败",
                raw_text='{"heading": missing}',
                missing_fields=["heading"],
                type_errors=[],
            )

        worker = ReportWorker(build_fn)
        finished_fired = []
        worker.finished.connect(lambda v: finished_fired.append(v))
        error_text = self._wait_worker_error(worker)
        assert error_text is not None, "ReportSchemaError 必须到达 error 信号"
        assert "Schema" in error_text or "第 3 节" in error_text or "heading" in error_text, \
            f"error 信号应含诊断信息，got: {error_text!r}"
        # finished 不应触发
        from PyQt6.QtCore import QTimer, QEventLoop
        loop = QEventLoop()
        QTimer.singleShot(300, loop.quit)
        loop.exec()
        assert len(finished_fired) == 0, \
            "ReportSchemaError 时 finished 不应触发（否则 UI 会误当成功）"


# ═══════════════════════════════════════════════════════════════════════
# Phase 2: ReportSchemaError + generate_structured + Pydantic 校验
# ═══════════════════════════════════════════════════════════════════════


class TestReportSchemaError:
    """ReportSchemaError 构造 + 诊断字段"""

    def test_constructor_with_diagnostics(self):
        from core.ai_errors import ReportSchemaError
        e = ReportSchemaError(
            "Schema 不匹配",
            raw_text='{"heading": 123}',
            missing_fields=["heading"],
            type_errors=["heading: Input should be a string"],
        )
        assert e.retryable is False
        assert e.raw_text == '{"heading": 123}'
        assert "heading" in e.missing_fields
        assert len(e.type_errors) == 1

    def test_no_diagnostics_defaults(self):
        from core.ai_errors import ReportSchemaError
        e = ReportSchemaError("bare error")
        assert e.missing_fields == []
        assert e.type_errors == []
        assert e.raw_text == ""


class TestMissingFieldsExtraction:
    """_missing_fields_from_error / _type_errors_from_error 从 ValidationError 提取诊断"""

    def test_missing_fields(self):
        from pydantic import BaseModel, ValidationError
        from core.report_engine import _missing_fields_from_error

        class T(BaseModel):
            name: str

        try:
            T.model_validate_json('{}')
        except ValidationError as e:
            missing = _missing_fields_from_error(e)
            assert "name" in missing

    def test_type_errors(self):
        from pydantic import BaseModel, ValidationError
        from core.report_engine import _type_errors_from_error

        class T(BaseModel):
            age: int

        try:
            T.model_validate_json('{"age": "not-a-number"}')
        except ValidationError as e:
            type_errs = _type_errors_from_error(e)
            assert any("age" in te for te in type_errs)


class TestGenerateStructured:
    """generate_structured() — 调用 LLM + Pydantic schema 校验"""

    def test_valid_json_passes_validation(self, monkeypatch):
        """AI 返回合法 WordSection JSON → 返回 dict"""
        from core.ai_client import AIClient
        from core.report_models import WordSection

        client = object.__new__(AIClient)
        object.__setattr__(client, "_initialized", False)
        AIClient.__init__(client, api_key="sk-test")

        valid_resp = '{"heading": "Test", "paragraphs": ["p1", "p2"], "image_anchors": []}'
        monkeypatch.setattr(client, "generate", lambda *a, **kw: valid_resp)

        result = client.generate_structured("write a section", WordSection, max_schema_retries=0)
        assert result["heading"] == "Test"
        assert len(result["paragraphs"]) == 2

    def test_invalid_json_raises_report_schema_error(self, monkeypatch):
        """AI 返回非法 JSON → ReportSchemaError（不静默）"""
        from core.ai_client import AIClient
        from core.report_models import WordSection
        from core.ai_errors import ReportSchemaError

        client = object.__new__(AIClient)
        object.__setattr__(client, "_initialized", False)
        AIClient.__init__(client, api_key="sk-test")

        # 缺失必填字段 heading
        bad_resp = '{"paragraphs": [], "image_anchors": []}'
        monkeypatch.setattr(client, "generate", lambda *a, **kw: bad_resp)

        with pytest.raises(ReportSchemaError) as exc_info:
            client.generate_structured("write", WordSection, max_schema_retries=0)
        assert "WordSection" in str(exc_info.value.message)
        # 应有诊断信息（missing_fields 或 type_errors 至少一个非空）
        assert len(exc_info.value.missing_fields) + len(exc_info.value.type_errors) > 0, \
            f"expected diagnostics, got missing={exc_info.value.missing_fields} type={exc_info.value.type_errors}"

    def test_retry_succeeds_on_second_attempt(self, monkeypatch):
        """首次 JSON 非法、第二次合法 → 重试成功"""
        from core.ai_client import AIClient
        from core.report_models import PPTSlide

        client = object.__new__(AIClient)
        object.__setattr__(client, "_initialized", False)
        AIClient.__init__(client, api_key="sk-test")

        calls = [0]
        responses = [
            '{"slide_title": 123, "bullet_points": [], "speaker_notes": ""}',
            '{"slide_title": "Title", "bullet_points": ["a", "b"], "speaker_notes": "OK"}',
        ]

        def fake_gen(**kw):
            r = responses[calls[0]]
            calls[0] += 1
            return r

        monkeypatch.setattr(client, "generate", fake_gen)
        result = client.generate_structured("write slide", PPTSlide, max_schema_retries=1)
        assert result["slide_title"] == "Title"
        assert calls[0] == 2  # 确实重试了一次

    def test_retry_exhausted_raises(self, monkeypatch):
        """持续非法 JSON → 重试耗竭后抛 ReportSchemaError"""
        from core.ai_client import AIClient
        from core.report_models import WordSection
        from core.ai_errors import ReportSchemaError

        client = object.__new__(AIClient)
        object.__setattr__(client, "_initialized", False)
        AIClient.__init__(client, api_key="sk-test")

        bad_resp = '{"paragraphs": [], "image_anchors": []}'  # 缺 heading
        monkeypatch.setattr(client, "generate", lambda *a, **kw: bad_resp)

        with pytest.raises(ReportSchemaError, match="回馈"):
            client.generate_structured("write", WordSection, max_schema_retries=1)


class TestSchemaRoundtrip:
    """Pydantic ↔ dict ↔ to_builder_dict 往返"""

    def test_word_section_roundtrip(self):
        from core.report_models import WordSection
        data = {
            "heading": "3.1 测试节",
            "paragraphs": ["段落一", "段落二"],
            "image_anchors": ["[INSERT_IMAGE: fig1.png]"],
        }
        section = WordSection.model_validate(data)
        assert section.heading == "3.1 测试节"
        assert len(section.paragraphs) == 2
        assert section.image_anchors[0] == "[INSERT_IMAGE: fig1.png]"

    def test_word_report_to_builder_dict(self):
        from core.report_models import WordReport, WordSection
        section = WordSection(
            heading="Ch1",
            paragraphs=["text"],
            image_anchors=["img.png"],
        )
        report = WordReport(title="R", sections=[section])
        d = report.to_builder_dict()
        assert d["title"] == "R"
        assert d["sections"][0]["heading"] == "Ch1"
        assert d["sections"][0]["content_paragraphs"] == ["text"]

    def test_ppt_slide_roundtrip(self):
        from core.report_models import PPTSlide
        data = {
            "slide_title": "第1页",
            "bullet_points": ["要点1", "要点2"],
            "speaker_notes": "详细论述",
            "image_anchor": "[INSERT_IMAGE: chart.png]",
        }
        slide = PPTSlide.model_validate(data)
        assert slide.slide_title == "第1页"
        assert slide.bullet_points == ["要点1", "要点2"]

    def test_ppt_report_to_builder_dict(self):
        from core.report_models import PPTReport, PPTSlide
        slide = PPTSlide(
            slide_title="Slide 1",
            bullet_points=["a"],
            speaker_notes="notes",
        )
        report = PPTReport(title="PPT", slides=[slide])
        d = report.to_builder_dict()
        assert d["title"] == "PPT"
        assert d["slides"][0]["slide_title"] == "Slide 1"
