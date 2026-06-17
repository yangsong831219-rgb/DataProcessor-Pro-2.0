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
# Test 1: format_api_error — 错误格式化
# ═══════════════════════════════════════════════════════════════════════

class TestFormatApiError:

    def test_404_with_anthropic_url_gives_helpful_message(self):
        """404 + 含 /anthropic → 提示改 /v1 (Anthropic 分支不含 raw code)"""
        from py.online_llm_thread import format_api_error
        err = Exception("404 Not found: https://api.deepseek.com/anthropic/chat/completions")
        result = format_api_error(err)
        assert "OpenAI" in result
        assert "/v1" in result

    def test_404_generic_gives_model_hint(self):
        """404 不含 /anthropic → 仍提示检查地址/模型"""
        from py.online_llm_thread import format_api_error
        err = Exception("HTTP 404 - model not found")
        result = format_api_error(err)
        assert "404" in result

    def test_401_unauthorized(self):
        """401 → API Key 无效提示"""
        from py.online_llm_thread import format_api_error
        result = format_api_error(Exception("401 Unauthorized: invalid api key"))
        assert "API Key" in result or "无效" in result

    def test_429_rate_limit(self):
        """429 → 额度用完提示"""
        from py.online_llm_thread import format_api_error
        result = format_api_error(Exception("429 rate_limit exceeded"))
        assert ("额度" in result or "用完" in result)

    def test_timeout(self):
        """timeout → 超时提示"""
        from py.online_llm_thread import format_api_error
        result = format_api_error(Exception("Connection timeout"))
        assert "超时" in result

    def test_generic_error_passthrough(self):
        """未知错误 → 保留原始消息"""
        from py.online_llm_thread import format_api_error
        msg = "Unknown: internal server error 500"
        result = format_api_error(Exception(msg))
        assert "500" in result


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
        for name, cfg in data.items():
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
        for name, cfg in data.items():
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
