"""AI 客户端 — Phase 1 本地后端 + 健康检查 + 流式推理测试

覆盖:
1. backend 路由: online / local 分别走对应 base_url/model/key
2. is_available: local 不依赖 api_key
3. health_check: 200 ok / 503→异常 / 连接失败→异常（mock HTTP）
4. generate_stream: 基本流式 yield / 空响应→异常
"""

from __future__ import annotations
import json
import os
import sys
import pytest


# ═══════════════════════════════════════════════════════════════════════
# 辅助：构造干净 AIClient 实例（绕过单例 + 强制走完 __init__）
# ═══════════════════════════════════════════════════════════════════════


def _patch_config(monkeypatch, overrides: dict):
    """Mock AIClient._load_config_json 返回指定配置."""
    from core.ai_client import AIClient
    monkeypatch.setattr(AIClient, '_load_config_json', staticmethod(lambda: overrides))


def _fresh_client(monkeypatch):
    """创建全新的 AIClient 实例（重置单例，清除 _initialized 防护）。"""
    from core.ai_client import AIClient
    AIClient._instance = None
    client = object.__new__(AIClient)
    # ★ 必须删除 _initialized，否则 __init__ 中 hasattr 返回 True 会提前 return
    try:
        del client._initialized  # type: ignore[attr-defined]
    except AttributeError:
        pass
    return client


def _init_client(client, monkeypatch, api_key=None, base_url=None, model_name=None):
    """调用 AIClient.__init__（应用 monkeypatch 后）。"""
    from core.ai_client import AIClient
    kwargs = {}
    if api_key is not None:
        kwargs['api_key'] = api_key
    if base_url is not None:
        kwargs['base_url'] = base_url
    if model_name is not None:
        kwargs['model_name'] = model_name
    AIClient.__init__(client, **kwargs)


# ═══════════════════════════════════════════════════════════════════════
# OpenAI SDK 流式模拟（注入 sys.modules 绕过 lazy import）
# ═══════════════════════════════════════════════════════════════════════


class _MockDelta:
    """模拟 openai.types.chat.chat_completion_chunk.ChoiceDelta."""
    def __init__(self, content: str | None = None):
        self.content = content


class _MockChoice:
    """模拟 chunk.choices[0]."""
    def __init__(self, content: str | None = None):
        self.delta = _MockDelta(content)


class _MockChunk:
    """模拟 openai.types.chat.chat_completion_chunk.ChatCompletionChunk."""
    def __init__(self, content: str | None = None):
        self.choices = [_MockChoice(content)]


def _make_openai_mock(response_tokens: list[str]):
    """构造一个 fake OpenAI 模块并注入 sys.modules['openai'].

    response_tokens: 每次迭代 yield 一个 chunk 的 token 列表。
    """
    tokens_iter = iter(response_tokens)

    class FakeCompletions:
        @staticmethod
        def create(*args, **kwargs):
            def gen():
                for t in tokens_iter:
                    yield _MockChunk(t)

            # 返回一个可迭代对象（模拟 stream=True 时的响应）
            result = gen()
            return result

    class FakeChat:
        completions = FakeCompletions()

    class FakeOpenAI:
        def __init__(self, api_key=None, base_url=None):
            self.chat = FakeChat()

    # ★ 注入 sys.modules 使 generate_stream 内的 from openai import OpenAI 可用
    import types
    fake_mod = types.ModuleType('openai')
    fake_mod.OpenAI = FakeOpenAI
    sys.modules['openai'] = fake_mod


def _remove_openai_mock():
    """清理 sys.modules 中的 fake openai."""
    sys.modules.pop('openai', None)


# ═══════════════════════════════════════════════════════════════════════
# Test 1: Backend 路由
# ═══════════════════════════════════════════════════════════════════════


class TestBackendRouting:
    """AIClient 按 backend 选择 base_url / model / api_key."""

    def test_backend_defaults_to_online(self, monkeypatch):
        """无 _backend 键 → 默认 online."""
        _patch_config(monkeypatch, {
            "deepseek V4 Pro": {
                "base_url": "https://api.deepseek.com/v1",
                "api_key": "sk-test",
                "model_name": "deepseek-v4-pro",
            }
        })
        from core.ai_client import AIClient
        assert AIClient.get_backend() == "online"

    def test_backend_local(self, monkeypatch):
        """_backend = 'local' → local."""
        _patch_config(monkeypatch, {
            "_backend": "local",
            "_local": {
                "base_url": "http://127.0.0.1:8080/v1",
                "api_key": "",
                "model_name": "qwen3.5-9b",
            },
        })
        from core.ai_client import AIClient
        assert AIClient.get_backend() == "local"

    def test_online_init_uses_online_defaults(self, monkeypatch):
        """backend=online → base_url 指向 DeepSeek."""
        monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
        _patch_config(monkeypatch, {
            "_backend": "online",
            "deepseek V4 Pro": {
                "base_url": "https://api.deepseek.com/v1",
                "api_key": "sk-test",
                "model_name": "deepseek-v4-pro",
            }
        })
        client = _fresh_client(monkeypatch)
        _init_client(client, monkeypatch, api_key="sk-online-key")
        assert client.backend == "online"
        assert "deepseek" in client.base_url or "api" in client.base_url

    def test_local_init_uses_local_defaults(self, monkeypatch):
        """backend=local → base_url 指向本地."""
        _patch_config(monkeypatch, {
            "_backend": "local",
            "_local": {
                "base_url": "http://127.0.0.1:8080/v1",
                "api_key": "",
                "model_name": "qwen3.5-9b",
            },
        })
        client = _fresh_client(monkeypatch)
        _init_client(client, monkeypatch)
        assert client.backend == "local"
        assert "8080" in client.base_url
        assert client.model_name == "qwen3.5-9b"

    def test_is_available_local_no_key(self, monkeypatch):
        """local 后端 api_key 缺省时 is_available 仍 True，自动填 'not-needed'."""
        _patch_config(monkeypatch, {
            "_backend": "local",
            "_local": {
                "base_url": "http://127.0.0.1:8080/v1",
                "model_name": "qwen3.5-9b",
                # api_key 键不出现 → 默认填 "not-needed"
            },
        })
        client = _fresh_client(monkeypatch)
        _init_client(client, monkeypatch)
        assert client.is_available() is True
        assert client.api_key == "not-needed"

    def test_is_available_local_no_base_url(self, monkeypatch):
        """local 后端 base_url 为空时 is_available False."""
        _patch_config(monkeypatch, {
            "_backend": "local",
            "_local": {
                "base_url": "",
                "api_key": "",
                "model_name": "qwen3.5-9b",
            },
        })
        client = _fresh_client(monkeypatch)
        _init_client(client, monkeypatch)
        assert client.is_available() is False

    def test_is_available_online_no_key(self, monkeypatch):
        """online 后端 api_key 为空时 is_available False."""
        monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
        _patch_config(monkeypatch, {"_backend": "online"})
        client = _fresh_client(monkeypatch)
        _init_client(client, monkeypatch)
        assert client.is_available() is False


# ═══════════════════════════════════════════════════════════════════════
# Test 2: 健康检查
# ═══════════════════════════════════════════════════════════════════════


class TestHealthCheck:
    """health_check() — HTTP mock 验证状态码→正确行为."""

    def _make_local_client(self, monkeypatch, base_url: str = "http://127.0.0.1:8080/v1"):
        _patch_config(monkeypatch, {
            "_backend": "local",
            "_local": {"base_url": base_url, "api_key": "", "model_name": "test"},
        })
        client = _fresh_client(monkeypatch)
        _init_client(client, monkeypatch)
        return client

    def _make_online_client(self, monkeypatch):
        _patch_config(monkeypatch, {"_backend": "online"})
        client = _fresh_client(monkeypatch)
        _init_client(client, monkeypatch, api_key="sk-test",
                     base_url="https://api.deepseek.com/v1",
                     model_name="deepseek-v4-pro")
        return client

    def test_local_health_200_ok(self, monkeypatch):
        """GET /health 返回 200 → True."""
        client = self._make_local_client(monkeypatch)

        class MockResp:
            status_code = 200
            text = '{"status":"ok"}'
        monkeypatch.setattr(
            'core.ai_client._requests.get',
            lambda url, timeout=5: MockResp(),
        )
        assert client.health_check() is True

    def test_local_health_503_loading_raises(self, monkeypatch):
        """GET /health 返回 503 → AIClientServerError."""
        from core.ai_errors import AIClientServerError
        client = self._make_local_client(monkeypatch)

        class MockResp503:
            status_code = 503
            text = '{"status":"loading model"}'
        monkeypatch.setattr(
            'core.ai_client._requests.get',
            lambda url, timeout=5: MockResp503(),
        )
        with pytest.raises(AIClientServerError) as exc_info:
            client.health_check()
        assert exc_info.value.status_code == 503
        assert "加载" in exc_info.value.message or "503" in exc_info.value.message

    def test_local_health_connection_refused_raises(self, monkeypatch):
        """连接失败 → AIClientServerError 含启动指引."""
        from core.ai_errors import AIClientServerError
        import requests as _r
        client = self._make_local_client(monkeypatch)
        monkeypatch.setattr(
            'core.ai_client._requests.get',
            lambda url, timeout=5: (_ for _ in ()).throw(_r.ConnectionError("refused")),
        )
        with pytest.raises(AIClientServerError) as exc_info:
            client.health_check()
        assert "未启动" in exc_info.value.message or "8080" in exc_info.value.message or "11434" in exc_info.value.message

    def test_local_health_timeout_raises(self, monkeypatch):
        """超时 → AIClientServerError status_code=503."""
        from core.ai_errors import AIClientServerError
        import requests as _r
        client = self._make_local_client(monkeypatch)
        monkeypatch.setattr(
            'core.ai_client._requests.get',
            lambda url, timeout=5: (_ for _ in ()).throw(_r.Timeout("timed out")),
        )
        with pytest.raises(AIClientServerError) as exc_info:
            client.health_check()
        assert exc_info.value.status_code == 503

    def test_ollama_fallback_api_tags(self, monkeypatch):
        """GET /health 404 但 /api/tags 200 → True (Ollama)."""
        client = self._make_local_client(monkeypatch)

        call_urls = []

        class FakeGet:
            def __init__(self, url, timeout=5):
                call_urls.append(url)
            @property
            def status_code(self):
                return 404 if '/health' in call_urls[-1] else 200
            text = '{}'

        monkeypatch.setattr(
            'core.ai_client._requests.get',
            lambda url, timeout=5: FakeGet(url, timeout),
        )
        assert client.health_check() is True
        assert len(call_urls) == 2
        assert '/health' in call_urls[0]
        assert '/api/tags' in call_urls[1]

    def test_online_health_200_ok(self, monkeypatch):
        """在线 /v1/models 200 → True."""
        client = self._make_online_client(monkeypatch)

        class MockResp:
            status_code = 200
            text = '{}'
        monkeypatch.setattr(
            'core.ai_client._requests.get',
            lambda url, timeout=5: MockResp(),
        )
        assert client.health_check() is True

    def test_online_health_401_still_ok(self, monkeypatch):
        """在线 /v1/models 401 → 仍 True（服务可达）."""
        client = self._make_online_client(monkeypatch)

        class MockResp:
            status_code = 401
            text = '{}'
        monkeypatch.setattr(
            'core.ai_client._requests.get',
            lambda url, timeout=5: MockResp(),
        )
        assert client.health_check() is True

    def test_online_health_connection_refused_raises(self, monkeypatch):
        """在线连接失败 → AIClientServerError."""
        from core.ai_errors import AIClientServerError
        import requests as _r
        client = self._make_online_client(monkeypatch)
        monkeypatch.setattr(
            'core.ai_client._requests.get',
            lambda url, timeout=5: (_ for _ in ()).throw(_r.ConnectionError("refused")),
        )
        with pytest.raises(AIClientServerError) as exc_info:
            client.health_check()
        assert "连接" in exc_info.value.message or "无法" in exc_info.value.message


# ═══════════════════════════════════════════════════════════════════════
# Test 3: generate_stream
# ═══════════════════════════════════════════════════════════════════════


class TestGenerateStream:
    """generate_stream() — 流式 token 生成."""

    def _make_client(self, monkeypatch, backend: str = "online", base_url: str = "https://test/v1"):
        _patch_config(monkeypatch, {
            "_backend": backend,
            "_local": {"base_url": "http://127.0.0.1:8080/v1", "api_key": "", "model_name": "test"}
            if backend == "local" else {},
        })
        client = _fresh_client(monkeypatch)
        if backend == "online":
            _init_client(client, monkeypatch, api_key="sk-test", base_url=base_url, model_name="test-model")
        else:
            _init_client(client, monkeypatch)
        return client

    def test_stream_yields_tokens(self, monkeypatch):
        """基本流式: 3 chunk → 3 yield."""
        client = self._make_client(monkeypatch)
        _make_openai_mock(["Hello", " world", "!"])

        try:
            result = list(client.generate_stream("Hi"))
            assert result == ["Hello", " world", "!"]
        finally:
            _remove_openai_mock()

    def test_stream_empty_raises(self, monkeypatch):
        """0 token → AIClientEmptyResponseError."""
        from core.ai_errors import AIClientEmptyResponseError
        client = self._make_client(monkeypatch)
        _make_openai_mock([])  # 空迭代

        try:
            with pytest.raises(AIClientEmptyResponseError):
                list(client.generate_stream("Hi"))
        finally:
            _remove_openai_mock()

    def test_stream_not_available_raises(self, monkeypatch):
        """is_available=False → AIClientNotConfiguredError."""
        from core.ai_errors import AIClientNotConfiguredError
        client = self._make_client(monkeypatch, backend="online")
        client.api_key = ""  # 清空 key
        with pytest.raises(AIClientNotConfiguredError):
            list(client.generate_stream("Hi"))

    def test_stream_backend_local(self, monkeypatch):
        """local 后端 generate_stream 也走 OpenAI SDK mock."""
        client = self._make_client(monkeypatch, backend="local")
        _make_openai_mock(["ok"])

        try:
            result = list(client.generate_stream("Hi"))
            assert result == ["ok"]
            assert client.backend == "local"
        finally:
            _remove_openai_mock()


# ═══════════════════════════════════════════════════════════════════════
# Test 4: 配置 SSOT 互不污染
# ═══════════════════════════════════════════════════════════════════════


class TestConfigSSOT:
    """_load_config_json / get_backend / get_local_config 行为."""

    def test_get_backend_missing_key_defaults_online(self, monkeypatch):
        from core.ai_client import AIClient
        monkeypatch.setattr(AIClient, '_load_config_json', staticmethod(lambda: {}))
        assert AIClient.get_backend() == "online"

    def test_get_backend_empty_string_defaults_online(self, monkeypatch):
        from core.ai_client import AIClient
        monkeypatch.setattr(AIClient, '_load_config_json',
                            staticmethod(lambda: {"_backend": ""}))
        assert AIClient.get_backend() == "online"

    def test_get_backend_whitespace_defaults_online(self, monkeypatch):
        from core.ai_client import AIClient
        monkeypatch.setattr(AIClient, '_load_config_json',
                            staticmethod(lambda: {"_backend": "  "}))
        assert AIClient.get_backend() == "online"

    def test_get_local_config_missing_key_fills_defaults(self, monkeypatch):
        from core.ai_client import AIClient
        monkeypatch.setattr(AIClient, '_load_config_json', staticmethod(lambda: {}))
        cfg = AIClient.get_local_config()
        assert "8080" in cfg["base_url"]
        assert cfg["model_name"] == "qwen3.5-9b"
        assert cfg["api_key"] == "not-needed"

    def test_get_local_config_empty_values_fill_defaults(self, monkeypatch):
        from core.ai_client import AIClient
        monkeypatch.setattr(AIClient, '_load_config_json',
                            staticmethod(lambda: {"_local": {}}))
        cfg = AIClient.get_local_config()
        assert "8080" in cfg["base_url"]
        assert cfg["model_name"] == "qwen3.5-9b"


# ═══════════════════════════════════════════════════════════════════════
# Test 5: 异常信息明确指示
# ═══════════════════════════════════════════════════════════════════════


class TestErrorMessageClarity:

    def test_local_not_configured_message_mentions_base_url(self, monkeypatch):
        """generate_stream local 不可用时提示本地信息."""
        from core.ai_errors import AIClientNotConfiguredError
        _patch_config(monkeypatch, {
            "_backend": "local",
            "_local": {"base_url": "", "api_key": "", "model_name": ""},
        })
        client = _fresh_client(monkeypatch)
        _init_client(client, monkeypatch)
        with pytest.raises(AIClientNotConfiguredError) as exc_info:
            list(client.generate_stream("test"))
        assert "base_url" in exc_info.value.message.lower() or "本地" in exc_info.value.message

    def test_health_check_503_message_mentions_llama_server(self, monkeypatch):
        """503 时消息提及加载/llama."""
        from core.ai_errors import AIClientServerError
        _patch_config(monkeypatch, {
            "_backend": "local",
            "_local": {"base_url": "http://127.0.0.1:8080/v1", "api_key": "", "model_name": "test"},
        })
        client = _fresh_client(monkeypatch)
        _init_client(client, monkeypatch)

        class Mock503:
            status_code = 503
            text = '{"status":"loading model"}'
        monkeypatch.setattr(
            'core.ai_client._requests.get',
            lambda url, timeout=5: Mock503(),
        )
        with pytest.raises(AIClientServerError) as exc_info:
            client.health_check()
        assert "8080" in exc_info.value.message or "llama" in exc_info.value.message.lower() or "加载" in exc_info.value.message

    def test_health_check_not_running_message_mentions_start_command(self, monkeypatch):
        """连接失败时消息包含启动命令示例."""
        from core.ai_errors import AIClientServerError
        import requests as _r
        _patch_config(monkeypatch, {
            "_backend": "local",
            "_local": {"base_url": "http://127.0.0.1:8080/v1", "api_key": "", "model_name": "test"},
        })
        client = _fresh_client(monkeypatch)
        _init_client(client, monkeypatch)
        monkeypatch.setattr(
            'core.ai_client._requests.get',
            lambda url, timeout=5: (_ for _ in ()).throw(_r.ConnectionError("refused")),
        )
        with pytest.raises(AIClientServerError) as exc_info:
            client.health_check()
        assert "llama-server" in exc_info.value.message or "8080" in exc_info.value.message or "11434" in exc_info.value.message


# ═══════════════════════════════════════════════════════════════════════
# Test 6: 截断检测 — finish_reason="length" 不静默交付半截结果
# ═══════════════════════════════════════════════════════════════════════


class TestTruncationDetection:
    """AIClientTruncationError：finish_reason=length 必须 loud fail."""

    def _make_truncated_response(self, content: str, finish_reason: str):
        """构造一个 mock OpenAI 响应对象."""
        from dataclasses import dataclass

        @dataclass
        class FakeMessage:
            content: str
            def __getattr__(self, name):
                return None  # reasoning_content etc.

        @dataclass
        class FakeChoice:
            message: FakeMessage
            finish_reason: str

        return type('Response', (), {
            'choices': [FakeChoice(
                message=FakeMessage(content=content),
                finish_reason=finish_reason,
            )],
        })()

    def _make_client(self, monkeypatch, backend: str = "online"):
        _patch_config(monkeypatch, {
            "_backend": backend,
            "_local": {"base_url": "http://127.0.0.1:8080/v1", "api_key": "", "model_name": "test"}
            if backend == "local" else {},
        })
        client = _fresh_client(monkeypatch)
        if backend == "online":
            _init_client(client, monkeypatch, api_key="sk-test", base_url="https://test/v1", model_name="test-model")
        else:
            _init_client(client, monkeypatch)
        return client

    def _mock_openai_response(self, monkeypatch, resp):
        """注入 sys.modules['openai'] 使 generate() 内的 lazy import 拿到 mock."""
        import types
        fake_mod = types.ModuleType('openai')

        class MockClient:
            def __init__(self, **kw):
                pass

            class chat:
                class completions:
                    @staticmethod
                    def create(**kw):
                        return resp

        fake_mod.OpenAI = MockClient
        monkeypatch.setitem(sys.modules, 'openai', fake_mod)

    def test_length_with_content_raises_truncation(self, monkeypatch):
        """finish_reason=length + content 非空 → AIClientTruncationError 含 partial_content."""
        from core.ai_errors import AIClientTruncationError
        client = self._make_client(monkeypatch)
        resp = self._make_truncated_response(
            content="Half report about FBG_A1 strain analysis...",
            finish_reason="length",
        )
        self._mock_openai_response(monkeypatch, resp)

        with pytest.raises(AIClientTruncationError) as exc_info:
            client.generate("test")
        assert "截断" in exc_info.value.message or "不完整" in exc_info.value.message
        assert "Half report" in exc_info.value.partial_content

    def test_length_with_empty_content_raises_truncation_not_empty(self, monkeypatch):
        """finish_reason=length + content 为空 → AIClientTruncationError（非 EmptyResponseError）."""
        from core.ai_errors import AIClientTruncationError
        client = self._make_client(monkeypatch)
        resp = self._make_truncated_response(content="", finish_reason="length")
        self._mock_openai_response(monkeypatch, resp)

        with pytest.raises(AIClientTruncationError) as exc_info:
            client.generate("test")
        assert "截断" in exc_info.value.message or "length" in exc_info.value.message

    def test_stop_with_content_passes_normally(self, monkeypatch):
        """finish_reason=stop + content → 正常返回."""
        client = self._make_client(monkeypatch)
        resp = self._make_truncated_response(content="OK", finish_reason="stop")
        self._mock_openai_response(monkeypatch, resp)
        result = client.generate("test")
        assert result == "OK"

    def test_truncation_not_retryable(self):
        """AIClientTruncationError retryable=False（需人工加大 max_tokens）."""
        from core.ai_errors import AIClientTruncationError
        e = AIClientTruncationError("truncated", partial_content="x")
        assert e.retryable is False
        assert e.partial_content == "x"

    def test_truncation_error_default_message(self):
        """无 message 时自动填默认信息."""
        from core.ai_errors import AIClientTruncationError
        e = AIClientTruncationError()
        assert "截断" in e.message or "不完整" in e.message
        assert e.partial_content == ""

    def test_stream_truncation_raises_at_end(self, monkeypatch):
        """流式：最后一个 chunk finish_reason=length → raise AIClientTruncationError."""
        from core.ai_errors import AIClientTruncationError
        client = self._make_client(monkeypatch, backend="online")

        import sys
        # Mock OpenAI module for streaming
        import types
        fake_mod = types.ModuleType('openai')

        class FakeDelta:
            def __init__(self, content: str | None = None):
                self.content = content

        class FakeChoice:
            def __init__(self, content: str | None = None, finish: str | None = None):
                self.delta = FakeDelta(content)
                self.finish_reason = finish

        class FakeChunk:
            def __init__(self, content: str | None = None, finish: str | None = None):
                self.choices = [FakeChoice(content, finish)]

        class FakeCompletions:
            @staticmethod
            def create(**kw):
                tokens = [("Hello", None), (" world", None), ("", "length")]
                for t, f in tokens:
                    yield FakeChunk(t, f)

        class FakeChat:
            completions = FakeCompletions()

        class FakeOpenAI:
            def __init__(self, **kw):
                self.chat = FakeChat()

        fake_mod.OpenAI = FakeOpenAI
        sys.modules['openai'] = fake_mod

        try:
            tokens = []
            with pytest.raises(AIClientTruncationError):
                for t in client.generate_stream("test"):
                    tokens.append(t)
            # Should have yielded tokens before raising
            assert len(tokens) == 2
            assert "Hello" in tokens[0]
        finally:
            sys.modules.pop('openai', None)


# ═══════════════════════════════════════════════════════════════════════
# Test 6b: configure_online — 就地更新单例 (不 reset _instance)
# ═══════════════════════════════════════════════════════════════════════


class TestConfigureOnline:
    """AIClient.configure_online() 统一配置源测试."""

    def test_configure_makes_available(self):
        """configure_online 后 is_available()→True。"""
        from core.ai_client import AIClient
        AIClient._instance = None
        # 从无配置开始 (env 可能也有值, 先设空)
        old_val = os.environ.pop('DEEPSEEK_API_KEY', None)
        try:
            client = AIClient()
            if client.backend == 'local':
                # 切到 online
                client._backend = 'online'
            assert not client.is_available()
            client.configure_online("sk-test-123", "https://api.test.com/v1", "test-model")
            assert client.is_available()
            assert client.api_key == "sk-test-123"
            assert client.base_url == "https://api.test.com/v1"
            assert client.model_name == "test-model"
            assert client.backend == 'online'
        finally:
            if old_val is not None:
                os.environ['DEEPSEEK_API_KEY'] = old_val

    def test_configure_switches_backend_to_online(self):
        """configure_online 必须把 _backend 设为 'online'(承重: is_available 分支)。"""
        from core.ai_client import AIClient
        AIClient._instance = None
        client = AIClient()
        client._backend = 'local'  # 模拟本地模式
        client.base_url = 'http://127.0.0.1:8080/v1'
        # 此时 is_available 走 local 分支 → bool(base_url) → True
        assert client.is_available()
        # configure_online 必须切 backend
        client.configure_online("sk-test", "https://api.deepseek.com/v1")
        assert client.backend == 'online'
        assert client.is_available()  # 走 online 分支 → bool(api_key) → True

    def test_configure_online_preserves_existing_fields(self):
        """传入空的 base_url/model_name 不覆盖已有值。"""
        from core.ai_client import AIClient
        AIClient._instance = None
        client = AIClient()
        client.model_name = "existing-model"
        client.base_url = "https://existing.url/v1"
        client.configure_online("sk-test")
        assert client.model_name == "existing-model"
        assert client.base_url == "https://existing.url/v1"

    def test_configure_empty_key_is_noop(self):
        """空 api_key 不写单例 (调用方已应验证非空)。"""
        from core.ai_client import AIClient
        AIClient._instance = None
        client = AIClient()
        client._backend = 'online'
        client.api_key = ''
        client.configure_online("")
        assert client.backend == 'online'
        assert client.api_key == ''  # 未被覆盖

    def test_configure_syncs_max_tokens_and_temperature(self):
        """configure_online 同步 max_tokens + temperature 到单例。"""
        from core.ai_client import AIClient
        AIClient._instance = None
        client = AIClient()
        client.max_tokens = 2048
        client.temperature = 0.3
        client.configure_online(
            "sk-test", "https://a.b/v1", "m",
            max_tokens=8192, temperature=0.5,
        )
        assert client.max_tokens == 8192
        assert client.temperature == 0.5
        # 0 值不覆盖
        client.configure_online(
            "sk-test", max_tokens=0, temperature=0.0,
        )
        assert client.max_tokens == 8192  # unchanged
        assert client.temperature == 0.5   # unchanged

    def test_generate_uses_singleton_max_tokens(self):
        """generate() 未传 max_tokens 时用单例 self.max_tokens。"""
        from core.ai_client import AIClient
        AIClient._instance = None
        client = AIClient()
        client.max_tokens = 8888
        # is_available 先配好
        client._backend = 'online'
        client.api_key = 'sk-test'
        client.model_name = 'test-model'
        # 直接看 generate 的默认解析 (没法真调 API, 只看 _max_tokens 推断逻辑:
        #   generate(prompt) → max_tokens=0 → _max_tokens = self.max_tokens = 8888)
        # 通过查看方法签名确认 — 默认参数从 2048 变成了 0
        import inspect
        sig = inspect.signature(client.generate)
        mt_default = sig.parameters['max_tokens'].default
        assert mt_default == 0, f"generate max_tokens default should be 0, got {mt_default}"


# ═══════════════════════════════════════════════════════════════════════
# Test 7: Mock 降级边界 — "配置了但连不上" 抛错，"未配置" 才 mock
# ═══════════════════════════════════════════════════════════════════════


class TestMockFallbackBoundary:
    """_get_generate_fn 的 mock 只兜底"真未配置"，不为"配置了但调用失败"兜底。

    这是防止静默降级的关键：server 挂 → loud error，不伪装成正常内容。
    """

    def _get_generate_fn_standalone(self, ai):
        """复制 main.py _get_generate_fn 的核心逻辑（独立测试）。"""
        if ai.is_available():
            return ai.get_generate_fn(enable_thinking=False)

        print("[主窗口] AI 未配置，大纲/报告将使用模拟降级")
        def _fallback(prompt: str) -> str:
            return '# Mock 大纲\n\n## 数据概述\n- 模拟内容\n'
        return _fallback

    def test_unconfigured_local_returns_mock(self, monkeypatch):
        """local base_url 为空 → is_available=False → mock 降级（不抛错）。"""
        _patch_config(monkeypatch, {
            "_backend": "local",
            "_local": {"base_url": "", "api_key": "", "model_name": ""},
        })
        client = _fresh_client(monkeypatch)
        _init_client(client, monkeypatch)

        fn = self._get_generate_fn_standalone(client)
        result = fn("test")
        assert "Mock 大纲" in result
        assert len(result) > 0

    def test_unconfigured_online_returns_mock(self, monkeypatch):
        """online api_key 为空 → is_available=False → mock 降级（不抛错）。"""
        monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
        _patch_config(monkeypatch, {"_backend": "online"})
        client = _fresh_client(monkeypatch)
        _init_client(client, monkeypatch)
        # Ensure api_key is empty
        client.api_key = ""

        fn = self._get_generate_fn_standalone(client)
        result = fn("test")
        assert "Mock 大纲" in result

    def test_configured_local_but_server_down_throws_not_mock(self, monkeypatch):
        """local base_url 已配、但服务器连不上 → generate() 抛错，不掉进 mock。"""
        from core.ai_errors import AIClientError
        import requests as _r

        _patch_config(monkeypatch, {
            "_backend": "local",
            "_local": {"base_url": "http://127.0.0.1:8080/v1", "api_key": "", "model_name": "test"},
        })
        client = _fresh_client(monkeypatch)
        _init_client(client, monkeypatch)

        # local 已配置 → is_available() True → 拿到 generate_fn（不是 mock）
        fn = self._get_generate_fn_standalone(client)
        assert fn is not None

        # Mock 网络层：连接拒绝
        monkeypatch.setattr(
            'core.ai_client._requests.get',
            lambda url, timeout=5: (_ for _ in ()).throw(_r.ConnectionError("refused")),
        )

        # 调用 generate_fn → 内部调 generate() → 网络失败 → 抛 AIClientError
        # ★ 关键断言：抛错，不是返回 mock 内容
        with pytest.raises(AIClientError):
            result = fn("test")
            # 安全网：万一没抛，断言结果不是 mock
            assert "Mock 大纲" not in result, (
                "不应返回 mock 内容——配置了但挂了必须抛错"
            )

    def test_configured_online_but_auth_fails_throws_not_mock(self, monkeypatch):
        """online api_key 已配、但调用返回 401 → 抛 AIClientAuthError，不掉进 mock。"""
        from core.ai_errors import AIClientAuthError
        import sys, types

        _patch_config(monkeypatch, {"_backend": "online"})
        client = _fresh_client(monkeypatch)
        _init_client(client, monkeypatch, api_key="sk-test")
        client.base_url = "https://test/v1"

        fn = self._get_generate_fn_standalone(client)
        assert fn is not None

        # Mock openai to raise 401
        fake_mod = types.ModuleType('openai')

        class AuthFailClient:
            def __init__(self, **kw):
                raise AIClientAuthError("bad key", status_code=401)

        fake_mod.OpenAI = AuthFailClient
        monkeypatch.setitem(sys.modules, 'openai', fake_mod)

        with pytest.raises(AIClientAuthError):
            result = fn("test")
            assert "Mock 大纲" not in str(result)


# ═══════════════════════════════════════════════════════════════════════
# Per-backend timeout split (local vs online)
# ═══════════════════════════════════════════════════════════════════════

class TestBackendTimeoutSplit:
    """_get_httpx_timeout 和 _make_openai_kwargs 按 backend 分流"""

    def _make_client(self, backend: str, monkeypatch, **local_cfg):
        """构造 AIClient 实例，注入 backend 和假配置。"""
        import core.ai_client as _mod
        cfg = {"_backend": backend, "_local": dict(local_cfg)}

        # 直接覆写类属性 — _load_config_json 是 @staticmethod, 赋值覆盖
        monkeypatch.setattr(_mod.AIClient, '_load_config_json', staticmethod(lambda: cfg))

        client = _fresh_client(monkeypatch)
        # init 时需要 _load_config_json 已就绪 ↔ 已在 monkeypatch 上完成
        _init_client(client, monkeypatch)
        return client

    def test_local_timeout_is_long(self, monkeypatch):
        """本地后端 read 超时 ≥ 600s (覆盖 ~661s 最坏单轮)。"""
        client = self._make_client('local', monkeypatch)
        to = client._get_httpx_timeout()
        assert to is not None
        assert to.read >= 600.0, f"本地 read 超时过短: {to.read}s"

    def test_online_timeout_unchanged(self, monkeypatch):
        """在线后端 read 超时维持 120s。"""
        client = self._make_client('online', monkeypatch)
        to = client._get_httpx_timeout()
        assert to is not None
        assert to.read == 120.0, f"在线 read 超时应为 120s, 实际 {to.read}s"

    def test_local_read_timeout_from_config(self, monkeypatch):
        """本地超时从配置文件 _local.read_timeout_s 读取。"""
        client = self._make_client('local', monkeypatch, read_timeout_s=300)
        to = client._get_httpx_timeout()
        assert to.read == 300.0, f"配置的 read_timeout_s=300 未生效, 实际 {to.read}"

    def test_connect_timeout_same_for_both(self, monkeypatch):
        """连接超时 10s 对 local/online 均不变。"""
        for be in ('local', 'online'):
            client = self._make_client(be, monkeypatch)
            to = client._get_httpx_timeout()
            assert to.connect == 10.0, f"{be}: connect 超时应为 10s"

    def test_local_make_openai_kwargs_has_http_client(self, monkeypatch):
        """本地后端 _make_openai_kwargs 返回 http_client (非 timeout 裸值)。"""
        client = self._make_client('local', monkeypatch)
        kw = client._make_openai_kwargs()
        assert 'http_client' in kw, f"本地应返回 http_client, 实际 keys: {list(kw.keys())}"
        assert 'timeout' not in kw, "本地不应返回裸 timeout"

    def test_online_make_openai_kwargs_has_timeout(self, monkeypatch):
        """在线后端 _make_openai_kwargs 返回 timeout (非 http_client)。"""
        client = self._make_client('online', monkeypatch)
        kw = client._make_openai_kwargs()
        assert 'timeout' in kw, f"在线应返回 timeout, 实际 keys: {list(kw.keys())}"
        assert 'http_client' not in kw, "在线不应返回 http_client"

    def test_local_http_client_trust_env_false(self, monkeypatch):
        """本地后端的 http_client 拒绝环境代理。"""
        client = self._make_client('local', monkeypatch)
        kw = client._make_openai_kwargs()
        hc = kw['http_client']
        assert hc.trust_env is False, f"本地应 trust_env=False"


# ═══════════════════════════════════════════════════════════════════════
# Human-readable timeout error in _invoke_llm
# ═══════════════════════════════════════════════════════════════════════

class TestInvokeLlmTimeoutMessage:
    """_invoke_llm 超时时输出人类可读消息（不暴露 raw traceback）。"""

    def test_timeout_message_is_chinese_readable(self, monkeypatch):
        """超时时不再是一串 httpx traceback。"""
        from core.ai_errors import AIClientTimeoutError
        from core.ai_client import AIClient as _AIC

        # Mock AIClient.get_instance() 返回一个会 raise Timeout 的假实例
        class MockClient:
            backend = 'local'
            model_name = 'test-model'

            def _get_context_size(self):
                return 8192

            def _estimate_tokens(self, text):
                return len(text) // 2

            def _safe_max_tokens(self, sp, up, req):
                return min(req, 4000) if req > 0 else 4000

            def generate(self, **kw):
                raise AIClientTimeoutError("httpx.ReadTimeout")

        monkeypatch.setattr(_AIC, 'get_instance', lambda: MockClient())

        from dp_engine.multi_agent import _invoke_llm
        try:
            _invoke_llm("system", "user", max_tokens=4000)
        except AIClientTimeoutError as e:
            msg = str(e)
            # 必须含中文提示
            assert "超时" in msg, f"消息缺'超时': {msg}"
            assert "max_tokens" in msg, f"消息缺 max_tokens: {msg}"
            # 不得含 raw exception 链
            assert "ReadTimeout" not in msg, f"消息不应含 raw 异常名: {msg}"
            assert "traceback" not in msg.lower(), f"消息不应含 traceback"
        else:
            pytest.fail("应抛出 AIClientTimeoutError")
