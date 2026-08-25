"""AI 客户端单例 — OpenAI 兼容接口，默认 DeepSeek-V4 Pro.

与 report_engine.py 配合：engine 通过 generate_fn 回调调用此客户端，
引擎不直接依赖 AIClient 类，保持完全解耦。

阶段一 (P0) 升级：generate() 不再吞异常返回空串，改为抛出 AIClientError
类型化异常。调用方通过 generate_with_retry() 获得指数退避重试。

阶段 4 (本地后端)：backend ∈ {online, local}；local 走独立 llama.cpp server
或 Ollama HTTP (均 OpenAI 兼容)。app 进程内不加载模型权重。
"""

from __future__ import annotations

import json
import os
import time as _time
from typing import Any, Callable, Dict, Generator, Optional, Tuple, TypedDict
from urllib.parse import urlparse

import requests as _requests  # 仅用于 health check（轻量 GET，不依赖 openai SDK）
from pydantic import BaseModel

from core.ai_errors import (
    AIClientError,
    AIClientEmptyResponseError,
    AIClientNotConfiguredError,
    AIClientServerError,
    AIClientTruncationError,
    classify_openai_error,
)


# ═══════════════════════════════════════════════════════════════════
# Schema 工具 — 将 Pydantic 模型转换为 LLM 提示词
# ═══════════════════════════════════════════════════════════════════


def _sanitize_json_text(json_str: str) -> str:
    """修复 LLM 输出 JSON 中的常见问题（FBG 领域专有 + 通用）。

    - 非法转义（LaTeX: \\Delta、\\lambda、\\varepsilon 等）→ 双重反斜杠
    - 尾逗号（,} 或 ,]）→ 删除
    - 首尾空白
    """
    _VALID_JSON_ESCAPES = frozenset('"\\/bfnrtu')
    result: list[str] = []
    i = 0
    while i < len(json_str):
        ch = json_str[i]
        if ch == '\\' and i + 1 < len(json_str):
            next_ch = json_str[i + 1]
            if next_ch not in _VALID_JSON_ESCAPES:
                # 非法转义 → 双重反斜杠（如 \D → \\D）
                result.append('\\\\')
            else:
                result.append('\\')
        else:
            result.append(ch)
        i += 1
    cleaned = ''.join(result)

    # 去尾逗号
    import re as _re2
    cleaned = _re2.sub(r',\s*([}\]])', r'\1', cleaned)

    return cleaned.strip()


def build_schema_prompt(models: list[type[BaseModel]]) -> str:
    """将 Pydantic 模型的 JSON Schema 注入为 System Prompt 段落.

    Args:
        models: Pydantic 模型类列表（如 [WordReport, WordSection]）

    Returns:
        格式化的 Schema 说明文本，可直接插入 System Prompt
    """
    parts: list[str] = []
    for model in models:
        schema = model.model_json_schema()
        name = schema.get('title', model.__name__)
        desc = schema.get('description', '')
        parts.append(f'### {name}')
        if desc:
            parts.append(desc)
        parts.append(f'```json\n{json.dumps(schema, ensure_ascii=False, indent=2)}\n```')
    return '\n\n'.join(parts)


class AIToolCall(TypedDict):
    """模型返回的单个原始工具调用。"""

    id: str
    name: str
    arguments: str


class AIToolStep(TypedDict):
    """一次模型步骤的结构化结果；不在 AIClient 内执行工具。"""

    content: str
    finish_reason: str
    tool_calls: list[AIToolCall]
    tool_calling_unavailable: bool


# ═══════════════════════════════════════════════════════════════════
# AIClient — 单例模式
# ═══════════════════════════════════════════════════════════════════


class AIClient:
    """OpenAI 兼容接口客户端.

    默认配置指向 DeepSeek-V4 Pro，可通过环境变量或参数覆盖。
    支持 backend ∈ {online, local} 切换；local 走本地 HTTP 服务 (llama.cpp / Ollama)。

    Usage:
        client = AIClient()
        resp = client.generate("写一段总结", "你是一个写作专家")
    """

    # 默认 DeepSeek 在线配置
    DEFAULT_BASE_URL = 'https://api.deepseek.com/v1'
    DEFAULT_MODEL = 'deepseek-v4-pro'

    # 默认本地 llama.cpp server 配置
    DEFAULT_LOCAL_BASE_URL = 'http://127.0.0.1:8080/v1'
    DEFAULT_LOCAL_MODEL = 'qwen3.5-9b'

    # 类级单例
    _instance: Optional['AIClient'] = None

    # ═══════════════════════════════════════════════════════════════
    # 后端配置 SSOT（从 ai_models_config.json 读取）
    # ═══════════════════════════════════════════════════════════════

    @staticmethod
    def _load_config_json() -> dict[str, Any]:
        """加载 ai_models_config.json 全文（无缓存，每次读盘）。"""
        cfg_path = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            'ai_models_config.json',
        )
        if os.path.exists(cfg_path):
            with open(cfg_path, 'r', encoding='utf-8') as f:
                return json.load(f)
        return {}

    @staticmethod
    def get_backend() -> str:
        """读取当前后端开关：'online' | 'local'（默认 online）。"""
        cfg = AIClient._load_config_json()
        return str(cfg.get('_backend', 'online')).strip().lower() or 'online'

    @staticmethod
    def get_local_config() -> dict[str, str]:
        """读取本地后端配置（base_url / model_name / api_key / system_prompt）。

        None 或缺键 → 填默认值；显式空字符串 "" 保留（允许用户主动清空以禁用）。
        """
        cfg = AIClient._load_config_json()
        local = cfg.get('_local') or {}

        def _str_or(key: str, default: str) -> str:
            val = local.get(key)
            if val is None:
                return default
            return str(val)

        return {
            'base_url': _str_or('base_url', AIClient.DEFAULT_LOCAL_BASE_URL),
            'model_name': _str_or('model_name', AIClient.DEFAULT_LOCAL_MODEL),
            'api_key': _str_or('api_key', 'not-needed'),
            'system_prompt': _str_or('system_prompt', ''),
        }

    # ═══════════════════════════════════════════════════════════════
    # 构造
    # ═══════════════════════════════════════════════════════════════

    def __new__(cls, *args, **kwargs):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def __init__(
        self,
        api_key: Optional[str] = None,
        base_url: Optional[str] = None,
        model_name: Optional[str] = None,
    ) -> None:
        # 单例保护：只初始化一次
        if hasattr(self, '_initialized'):
            return
        self._initialized = True
        self._stream_response: Any | None = None  # 当前活跃的流式响应，供 cancel 关闭

        # ── 按 backend 选择默认值来源 ──
        _backend = self.get_backend()
        self._backend: str = _backend
        _cfg = self._load_config_json()

        if _backend == 'local':
            _lc = self.get_local_config()
            _default_key = str(_lc['api_key'])
            _default_url = str(_lc['base_url'])
            _default_model = str(_lc['model_name'])
        else:
            _default_key = os.environ.get('DEEPSEEK_API_KEY', '')
            _default_url = os.environ.get('DEEPSEEK_BASE_URL', self.DEFAULT_BASE_URL)
            _default_model = os.environ.get('DEEPSEEK_MODEL', self.DEFAULT_MODEL)

        # ★ max_tokens 从配置读取（不再硬编 2048），默认为 4096
        _cfg_mt = _cfg.get('max_tokens') if _backend != 'local' else (_cfg.get('_local') or {}).get('max_tokens')
        self.max_tokens: int = int(_cfg_mt) if _cfg_mt and int(_cfg_mt) > 0 else 4096
        self.temperature: float = 0.3

        self.api_key = api_key or _default_key
        self._api_key_backend = _backend
        self.base_url = base_url or _default_url
        self.model_name = model_name or _default_model
        self._client = None

    # ── backend 属性（只读） ──

    @property
    def backend(self) -> str:
        """当前后端：'online' | 'local'."""
        return self._backend

    # ── 公共 API ──

    def configure_online(
        self, api_key: str, base_url: str = "", model_name: str = "",
        max_tokens: int = 0, temperature: float = 0.0,
    ) -> None:
        """就地更新单例的 online 配置 (不 reset _instance)。

        此方法不抛异常 — 调用方应在连接成功后调用。
        Args:
            api_key: API 密钥 (非空)
            base_url: API 端点 (空则保留现有值)
            model_name: 模型名 (空则保留现有值)
            max_tokens: max_tokens (0 则保留现有值)
            temperature: temperature (≤0 则保留现有值)
        """
        if not api_key:
            return  # 静默退 — 调用方已验证非空
        self._backend = 'online'
        self.api_key = api_key
        self._api_key_backend = 'online'
        if base_url:
            self.base_url = base_url
        if model_name:
            self.model_name = model_name
        if max_tokens > 0:
            self.max_tokens = max_tokens
        if temperature > 0:
            self.temperature = temperature

    def is_available(self) -> bool:
        """检查当前后端是否可用.

        - online: 需要 api_key 非空
        - local:  不需要 api_key (llama.cpp server 无认证)，仅检查 base_url 非空
        """
        _be = getattr(self, '_backend', 'online')  # 兼容旧测试绕 __init__ 的场景
        if _be == 'local':
            return bool(self.base_url)
        key_backend = getattr(self, '_api_key_backend', _be)
        return bool(self.api_key) and key_backend == 'online'

    def _is_local_qwen(self) -> bool:
        """本地方 Qwen3.5 模型（需要非思考模式注入）。"""
        _be = getattr(self, '_backend', 'online')
        if _be != 'local':
            return False
        mn = (self.model_name or '').lower()
        return 'qwen' in mn or 'qwq' in mn

    def _is_official_deepseek(self) -> bool:
        """Return whether the active endpoint is DeepSeek's official API."""
        if getattr(self, '_backend', 'online') != 'online':
            return False
        hostname = (urlparse(self.base_url or '').hostname or '').lower()
        return hostname == 'api.deepseek.com'

    def _thinking_extra_body(self, enable_thinking: bool) -> dict[str, Any] | None:
        """Build the vendor-specific thinking-mode switch for one request."""
        if self._is_local_qwen():
            return {
                'chat_template_kwargs': {'enable_thinking': enable_thinking},
            }
        if self._is_official_deepseek():
            return {
                'thinking': {
                    'type': 'enabled' if enable_thinking else 'disabled',
                },
            }
        return None

    # ═══════════════════════════════════════════════════════════════
    # 健康检查
    # ═══════════════════════════════════════════════════════════════

    def health_check(self, timeout: float = 5.0) -> bool:
        """检查后端服务是否可达且健康.

        本地后端：
          - 先尝试 GET {host}/health (llama.cpp server 标准)
          - 若 404，回退 GET {host}/api/tags (Ollama 兼容)
        在线后端：
          - GET {host}/v1/models (轻量探针)

        Returns:
            True 若健康

        Raises:
            AIClientServerError: 服务不可达/503 加载中/连接失败，
                                信息明确指向“本地服务未启动”或相应原因
        """
        host = self.base_url.removesuffix('/v1').removesuffix('/')
        _be = getattr(self, '_backend', 'online')
        is_local = _be == 'local'

        if is_local:
            # ── 本地：先试 llama.cpp /health ──
            endpoints = [f'{host}/health', f'{host}/api/tags']
            last_status: int | None = None
            last_body: str = ''
            for ep in endpoints:
                try:
                    resp = _requests.get(ep, timeout=timeout)
                    last_status = resp.status_code
                    if resp.status_code == 200:
                        # llama.cpp /health 返回 {"status":"ok"} 或类似
                        # Ollama /api/tags 返回 {"models":[...]}
                        return True
                    if resp.status_code == 503:
                        last_body = resp.text[:200]
                except _requests.ConnectionError:
                    continue  # 试下一个 endpoint
                except _requests.Timeout:
                    continue

            # ── 所有尝试均失败 → 构造明确错误 ──
            if last_status == 503:
                raise AIClientServerError(
                    f"本地 llama.cpp server 正在加载模型 (HTTP 503)，请稍后重试"
                    + (f"\n响应: {last_body}" if last_body else ""),
                    status_code=503,
                )
            raise AIClientServerError(
                "本地服务未启动，请先启动 llama-server (端口 8080) 或 Ollama (端口 11434)\n"
                "启动示例: llama-server -m qwen3.5-9b-q4_k_m.gguf -ngl 99 -c 8192 --host 127.0.0.1 --port 8080",
                status_code=503,
            )
        else:
            # ── 在线后端：轻量探测 ──
            try:
                resp = _requests.get(f'{host}/v1/models', timeout=timeout)
                if resp.status_code == 200:
                    return True
                # 即使 401/403 也说明服务可达（认证问题另有 generate() 处理）
                if resp.status_code in (401, 403):
                    return True
                raise AIClientServerError(
                    f"在线 API 服务异常 (HTTP {resp.status_code})",
                    status_code=resp.status_code,
                )
            except _requests.ConnectionError:
                raise AIClientServerError(
                    f"无法连接到在线 API 服务: {host}",
                    status_code=503,
                )
            except _requests.Timeout:
                raise AIClientServerError(
                    f"连接在线 API 超时 ({timeout}s): {host}",
                    status_code=503,
                )

    # ── 上下文窗口大小（token） ──
    LOCAL_CTX_TOKENS: int = 8192     # llama.cpp 本地模型默认上下文
    ONLINE_CTX_TOKENS: int = 131072  # DeepSeek-V4 Pro 上下文

    def _get_context_size(self) -> int:
        """返回当前后端的上下文窗口大小（token）。"""
        _be = getattr(self, '_backend', 'online')
        if _be == 'local':
            # 尝试从配置读取 ctx_size，否则用默认 8192
            cfg = self._load_config_json()
            local_cfg = cfg.get('_local') or {}
            return int(local_cfg.get('ctx_size', self.LOCAL_CTX_TOKENS))
        return self.ONLINE_CTX_TOKENS

    def _get_httpx_timeout(self) -> Any:
        """返回当前后端对应的 httpx.Timeout，按 backend 分流。

        本地 llama.cpp: read 超时从配置文件 _local.read_timeout_s 读取
        (默认 LOCAL_READ_TIMEOUT_S=900s)，覆盖最坏单轮生成 ≈ 661s。
        在线 deepseek: 维持原 ONLINE_READ_TIMEOUT_S=120s。
        """
        try:
            import httpx as _httpx
        except Exception:
            return None
        _be = getattr(self, '_backend', 'online')
        if _be == 'local':
            cfg = self._load_config_json()
            local_cfg = cfg.get('_local') or {}
            read_s = int(local_cfg.get('read_timeout_s', self.LOCAL_READ_TIMEOUT_S))
            return _httpx.Timeout(read_s, connect=self.CONNECT_TIMEOUT_S)
        return _httpx.Timeout(self.ONLINE_READ_TIMEOUT_S, connect=self.CONNECT_TIMEOUT_S)

    def _make_openai_kwargs(self, api_key_override: str = '') -> dict[str, Any]:
        """构建 OpenAI 客户端构造参数 — 超时按后端分流 + 本地代理豁免。

        本地后端: 传入显式 httpx.Client(trust_env=False)，避免系统 HTTP_PROXY
        拦截 127.0.0.1 请求。
        在线后端: 仅传入 timeout，不传 http_client（由 SDK 默认创建）。
        """
        import httpx as _httpx
        _timeout = self._get_httpx_timeout()
        _be = getattr(self, '_backend', 'online')
        kwargs: dict[str, Any] = dict(
            api_key=api_key_override if api_key_override else self.api_key,
            base_url=self.base_url,
        )
        if _timeout is not None:
            if _be == 'local':
                # 本地: 显式 httpx.Client 禁代理 + 长超时
                kwargs['http_client'] = _httpx.Client(
                    timeout=_timeout,
                    trust_env=False,
                )
            else:
                # 在线: 仅传 timeout，SDK 默认创建
                kwargs['timeout'] = _timeout
        return kwargs

    @staticmethod
    def _estimate_tokens(text: str) -> int:
        """粗略估算 token 数（保守: chars/2 + 安全余量）。

        中文每字 ~1-2 token，英文 ~0.25 token/char，取保守的 chars/2。
        实际 tokenizer 差异大，此估算偏保守，确保不超。
        """
        if not text:
            return 0
        # 保守估计：每个字符 ≈ 0.5 token + 20% 安全余量
        return int(len(text) / 2 * 1.2)

    def _safe_max_tokens(self, system_prompt: str, user_prompt: str,
                         requested: int) -> int:
        """安全计算 max_tokens: 确保 prompt + max_tokens + margin ≤ ctx。

        Args:
            system_prompt: 系统提示词
            user_prompt: 用户提示词
            requested: 请求的 max_tokens (0 或调用方传入值)

        Returns:
            安全上限内的 max_tokens，最小 512。
        """
        ctx = self._get_context_size()
        margin = 512  # 安全边距
        prompt_est = self._estimate_tokens(system_prompt) + self._estimate_tokens(user_prompt)
        available = ctx - prompt_est - margin

        if requested <= 0:
            requested = self.max_tokens

        safe = max(512, min(requested, available))

        # 本地后端：硬墙 8192，不允许超额
        _be = getattr(self, '_backend', 'online')
        if _be == 'local' and safe > ctx - margin:
            safe = max(512, ctx - prompt_est - margin)

        return safe

    # ── Qwen 官方推荐采样参数 (非思考模式) ──
    # ── 超时常量 (按后端分流) ──
    LOCAL_READ_TIMEOUT_S: int = 900      # 本地慢模型最坏单轮 ~661s，留余量
    ONLINE_READ_TIMEOUT_S: int = 120     # 在线 API 快，维持原值
    CONNECT_TIMEOUT_S: int = 10

    QWEN_TEMPERATURE: float = 0.7
    QWEN_TOP_P: float = 0.8
    QWEN_TOP_K: int = 20
    QWEN_PRESENCE_PENALTY: float = 1.5

    def generate(
        self,
        prompt: str,
        system_prompt: str = '',
        temperature: float = 0.0,  # 0 = 用单例 self.temperature
        max_tokens: int = 0,        # 0 = 用单例 self.max_tokens
        *,
        enable_thinking: bool = False,
    ) -> str:
        """同步调用 LLM，返回文本响应。

        Args:
            prompt: 用户提示词
            system_prompt: 系统提示词（可选）
            temperature: 温度参数，结构化输出建议 0.3
            max_tokens: 最大生成长度
            enable_thinking: True=允许思考 (诊断/聊天)，False=纯答案 (报告/结构化)。
                             对本地 Qwen3.5 经 extra_body→chat_template_kwargs 下发。

        Returns:
            LLM 响应文本

        Raises:
            AIClientNotConfiguredError: API Key 未配置
            AIClientAuthError: 认证失败 (401/403)
            AIClientRequestError: 请求参数错误 (400/422)
            AIClientRateLimitError: 速率限制 (429), 可重试
            AIClientServerError: 服务端错误 (5xx), 可重试
            AIClientTimeoutError: 超时/连接失败, 可重试
            AIClientEmptyResponseError: 200 但 content 为空
        """
        if not self.is_available():
            raise AIClientNotConfiguredError("AI API Key 未配置，请在设置中配置 AI 模型")

        # 用单例字段兜底（调用方未显式传值时）
        _max_tokens = max_tokens if max_tokens > 0 else self.max_tokens
        # ★ 动态安全钳制: max_tokens 不超过 ctx − prompt − margin
        _max_tokens = self._safe_max_tokens(system_prompt, prompt, _max_tokens)
        _temperature = temperature if temperature > 0 else self.temperature

        from openai import OpenAI  # pyright: ignore[reportImplicitRelativeImport]

        try:
            for attempt in range(2):  # 0=首次, 1=截断重试（放大 max_tokens）
                kwargs_opts = self._make_openai_kwargs()
                try:
                    client = OpenAI(**kwargs_opts)  # pyright: ignore[reportArgumentType]
                except TypeError:
                    kwargs_opts.pop('http_client', None)
                    kwargs_opts.pop('timeout', None)
                    client = OpenAI(**kwargs_opts)  # pyright: ignore[reportArgumentType]

                messages: list[Dict[str, str]] = []
                if system_prompt:
                    messages.append({'role': 'system', 'content': system_prompt})
                messages.append({'role': 'user', 'content': prompt})

                create_kwargs: dict = {
                    'model': self.model_name,
                    'messages': messages,
                    'temperature': _temperature,
                    'max_tokens': _max_tokens,
                }

                extra_body = self._thinking_extra_body(enable_thinking)
                if extra_body is not None:
                    create_kwargs['extra_body'] = extra_body
                    if self._is_local_qwen() and not enable_thinking:
                        create_kwargs.setdefault('temperature', self.QWEN_TEMPERATURE)
                        create_kwargs['top_p'] = self.QWEN_TOP_P
                        extra_body.update({
                            'top_k': self.QWEN_TOP_K,
                            'presence_penalty': self.QWEN_PRESENCE_PENALTY,
                        })
                response = client.chat.completions.create(**create_kwargs)
                content = (response.choices[0].message.content or '').strip()
                finish = getattr(response.choices[0], 'finish_reason', 'stop')
                reasoning_content = getattr(response.choices[0].message, 'reasoning_content', '') or ''

                if finish == 'length':
                    _ctx = self._get_context_size()
                    if attempt == 0 and _max_tokens < _ctx:
                        # 截断 → 放大 max_tokens 重试 1 次（不超过上下文窗口）
                        _max_tokens = min(_max_tokens * 2, _ctx)
                        continue  # 回到 for attempt 循环开头, 用更大的 max_tokens 重试

                    if content:
                        raise AIClientTruncationError(
                            f"AI 输出在 {_max_tokens} token 处被截断"
                            f"（finish_reason=length），内容不完整。"
                            "请增大 max_tokens 重试。",
                            partial_content=content,
                        )
                    raise AIClientTruncationError(
                        f"AI 输出在 {_max_tokens} token 处被截断且 content 为空"
                        f"（已自动重试 1 次）。"
                        "请增大 max_tokens 后重试。",
                    )

                if not content:
                    # content 为空但未截断 → 其他故障
                    reasoning_len = len(
                        getattr(response.choices[0].message, 'reasoning_content', '') or ''
                    )
                    raise AIClientEmptyResponseError(
                        f"AI 返回空 content（finish_reason={finish}"
                        + (f", reasoning_len={reasoning_len}" if reasoning_len else "")
                        + f", max_tokens={_max_tokens}）"
                        + (
                            "。已请求非思考模式，但服务仍未返回最终答案，请重试"
                            if not enable_thinking else ""
                        )
                    )
                return content

        except AIClientError:
            raise
        except Exception as e:
            raise classify_openai_error(e) from e
        return ""  # unreachable — 所有路径均 raise/return

    # ── 流式推理 ──

    def cancel_current_stream(self) -> None:
        """关闭当前活跃的流式 HTTP 连接，让 llama-server 停止生成、释放 slot。

        由 UI 线程调用（AIClientStreamThread.cancel() → 此项）。
        """
        if self._stream_response is not None:
            try:
                self._stream_response.close()
            except Exception:
                pass
            self._stream_response = None

    def generate_stream(
        self,
        prompt: str,
        system_prompt: str = '',
        temperature: float = 0.7,
        max_tokens: int = 0,
        *,
        enable_thinking: bool = False,
    ) -> Generator[str, None, None]:
        """流式调用 LLM，逐 token yield（纯 Python 生成器，无 Qt 依赖）.

        与 generate() 共享同一后端路由（online / local），均走 OpenAI SDK 流式 API。
        TTFB / 取消 / 信号发射由调用方（QThread wrapper）负责。

        enable_thinking: True=允许思考流式展示 (诊断/聊天)，
                         False=纯答案 (报告/结构化，经 extra_body 下发)。

        截断重试: finish_reason=length → 自动翻倍重试 1 次（上限不超过上下文窗口）。
        本地后端: max_tokens 安全上限 = min(配置, 上下文 − prompt占用 − 512边距)。

        Yields:
            每次 yield 一个 token 字符串

        Raises:
            AIClientNotConfiguredError: 未配置
            AIClientEmptyResponseError: 流结束但无有效内容
            AIClientTruncationError: 重试后仍截断
            AIClientError: 其他 HTTP/网络错误（经 classify_openai_error）
        """
        if not self.is_available():
            raise AIClientNotConfiguredError(
                "AI 不可用：请检查后端配置"
                + (" (本地 base_url)" if getattr(self, '_backend', 'online') == 'local' else " (在线 API Key)")
            )

        from openai import OpenAI  # pyright: ignore[reportImplicitRelativeImport]

        # ── 安全上限：min(请求, 上下文−prompt−margin) ──
        _ctx_size = self._get_context_size()
        _max_tokens = self._safe_max_tokens(system_prompt, prompt, max_tokens)

        try:
            kwargs_opts = self._make_openai_kwargs()
            try:
                client = OpenAI(**kwargs_opts)  # pyright: ignore[reportArgumentType]
            except TypeError:
                # mock / test fallback — FakeOpenAI doesn't accept timeout / http_client
                kwargs_opts.pop('http_client', None)
                kwargs_opts.pop('timeout', None)
                client = OpenAI(**kwargs_opts)  # pyright: ignore[reportArgumentType]

            messages: list[Dict[str, str]] = []
            if system_prompt:
                messages.append({'role': 'system', 'content': system_prompt})
            messages.append({'role': 'user', 'content': prompt})

            # ── 截断重试循环（最多 2 次）──
            for attempt in range(2):
                create_kwargs: dict = {
                    'model': self.model_name,
                    'messages': messages,
                    'stream': True,
                    'temperature': temperature,
                    'max_tokens': _max_tokens,
                }

                # ── 本地 Qwen3.5：chat_template_kwargs 控制思考开关 ──
                _eb = self._thinking_extra_body(enable_thinking)
                if _eb is not None:
                    create_kwargs['extra_body'] = _eb
                    if self._is_local_qwen() and not enable_thinking:
                        create_kwargs.setdefault('temperature', self.QWEN_TEMPERATURE)
                        create_kwargs['top_p'] = self.QWEN_TOP_P
                        _eb.update({
                            'top_k': self.QWEN_TOP_K,
                            'presence_penalty': self.QWEN_PRESENCE_PENALTY,
                        })
                self._stream_response = None
                yielded_any = False
                last_finish = 'stop'
                # 第一次尝试（可能截断）: 缓冲 tokens；第二次（终试）: 直接 yield
                _buf: list[str] = []
                _is_final_attempt = (attempt == 1)
                try:
                    response = client.chat.completions.create(**create_kwargs)
                    self._stream_response = response  # 存引用，供 cancel 关闭
                    for chunk in response:
                        delta = chunk.choices[0].delta if chunk.choices else None
                        if delta:
                            token = delta.content or ''
                            if not token and enable_thinking:
                                # 思考开启时：reasoning_content 作为流式后备
                                reasoning = getattr(delta, 'reasoning_content', None)
                                if reasoning:
                                    token = str(reasoning)
                            if token:
                                yielded_any = True
                                if _is_final_attempt:
                                    yield token
                                else:
                                    _buf.append(token)
                        # 最后一个 chunk 带 finish_reason
                        if chunk.choices and hasattr(chunk.choices[0], 'finish_reason') and chunk.choices[0].finish_reason:
                            last_finish = chunk.choices[0].finish_reason

                    # ── 截断判定 ──
                    if last_finish == 'length':
                        if not _is_final_attempt and _max_tokens < _ctx_size:
                            # 翻倍重试（但不超过安全上限：ctx − prompt − margin）
                            _new_mt = min(_max_tokens * 2, _ctx_size)
                            _safe = self._safe_max_tokens(system_prompt, prompt, _new_mt)
                            _max_tokens = _safe
                            continue  # 回到 for attempt 循环开头，用更大的 max_tokens
                        # 终试也截断 → 报错
                        if yielded_any:
                            raise AIClientTruncationError(
                                f"AI 流式输出在 {_max_tokens} token 处被截断"
                                f"（finish_reason=length，上下文窗口={_ctx_size}），内容不完整。"
                                "请减少 prompt 长度或增大上下文窗口。",
                            )
                        raise AIClientTruncationError(
                            f"AI 流式输出在 {_max_tokens} token 处被截断且 content 为空"
                            f"（已自动重试 1 次，上下文窗口={_ctx_size}）。",
                        )

                    # ── 成功完成 ──
                    if not yielded_any:
                        raise AIClientEmptyResponseError(
                            "AI 流式返回了空响应，可能模型不支持当前请求"
                            + (" (非思考模式下 content 全空)" if not enable_thinking else "")
                        )

                    # 第一次尝试成功 → yield 缓冲的 tokens
                    if not _is_final_attempt and _buf:
                        for token in _buf:
                            yield token
                    return  # 正常结束

                finally:
                    self._stream_response = None  # 清理引用

        except AIClientError:
            raise
        except Exception as e:
            raise classify_openai_error(e) from e

    # ── 单步结构化工具请求（工具执行由上层安全适配器负责） ──

    def generate_tool_step(
        self,
        messages: list[Dict[str, Any]],
        tools: list[Dict[str, Any]],
        temperature: float = 0.0,
        max_tokens: int = 0,
        *,
        enable_thinking: bool = False,
    ) -> AIToolStep:
        """执行一次 OpenAI-compatible chat step，但不执行任何工具。

        当请求携带工具且端点以 HTTP 400/422 拒绝时，返回显式
        ``tool_calling_unavailable``，供多智能体图只降级一次纯文本调用。
        """
        if not self.is_available():
            raise AIClientNotConfiguredError("AI API Key 未配置，请在设置中配置 AI 模型")

        requested = max_tokens if max_tokens > 0 else self.max_tokens
        prompt_text = json.dumps(messages, ensure_ascii=False, default=str)
        safe_max_tokens = self._safe_max_tokens("", prompt_text, requested)
        request_temperature = temperature if temperature > 0 else self.temperature

        from openai import OpenAI  # pyright: ignore[reportImplicitRelativeImport]

        try:
            kwargs_openai = self._make_openai_kwargs()
            try:
                client = OpenAI(**kwargs_openai)  # pyright: ignore[reportArgumentType]
            except TypeError:
                kwargs_openai.pop('http_client', None)
                kwargs_openai.pop('timeout', None)
                client = OpenAI(**kwargs_openai)  # pyright: ignore[reportArgumentType]

            create_kwargs: dict[str, Any] = {
                'model': self.model_name,
                'messages': messages,
                'temperature': request_temperature,
                'max_tokens': safe_max_tokens,
            }
            if tools:
                create_kwargs['tools'] = tools

            extra_body = self._thinking_extra_body(enable_thinking)
            if extra_body is not None:
                create_kwargs['extra_body'] = extra_body
                if self._is_local_qwen() and not enable_thinking:
                    create_kwargs.setdefault('temperature', self.QWEN_TEMPERATURE)
                    create_kwargs['top_p'] = self.QWEN_TOP_P
                    extra_body.update({
                        'top_k': self.QWEN_TOP_K,
                        'presence_penalty': self.QWEN_PRESENCE_PENALTY,
                    })

            response = client.chat.completions.create(**create_kwargs)
            choice = response.choices[0]
            message = choice.message
            finish_reason = str(choice.finish_reason or '')

            if finish_reason == 'length':
                partial = (message.content or '').strip()
                raise AIClientTruncationError(
                    f"AI 工具步骤在 {safe_max_tokens} token 处被截断",
                    partial_content=partial,
                )

            tool_calls: list[AIToolCall] = []
            for call in message.tool_calls or []:
                tool_calls.append({
                    'id': str(call.id or ''),
                    'name': str(call.function.name or ''),
                    'arguments': str(call.function.arguments or ''),
                })

            if finish_reason == 'tool_calls' and tool_calls:
                return {
                    'content': (message.content or '').strip(),
                    'finish_reason': finish_reason,
                    'tool_calls': tool_calls,
                    'tool_calling_unavailable': False,
                }

            content = (message.content or '').strip()
            if not content:
                raise AIClientEmptyResponseError(
                    f"AI 工具步骤返回空响应（finish_reason={finish_reason}）"
                )
            return {
                'content': content,
                'finish_reason': finish_reason,
                'tool_calls': [],
                'tool_calling_unavailable': False,
            }
        except AIClientError:
            raise
        except Exception as e:
            status_code = getattr(e, 'status_code', None)
            if tools and status_code in (400, 422):
                return {
                    'content': '',
                    'finish_reason': 'tool_calling_unavailable',
                    'tool_calls': [],
                    'tool_calling_unavailable': True,
                }
            raise classify_openai_error(e) from e

    # ── 多轮工具调用 (Function Calling / Agent Loop) ──

    def generate_with_tools(
        self,
        messages: list[Dict[str, Any]],
        tools: list[Dict[str, Any]],
        tool_executable_map: Dict[str, Any],
        temperature: float = 0.0,
        max_tokens: int = 0,
        *,
        enable_thinking: bool = False,
    ) -> str:
        """支持多轮 Function Calling 的 Agent Loop.

        经典闭环：
          发起请求 → finish_reason=="tool_calls" → 解析参数 → 执行本地函数
          → 结果注入 history → 再次请求 → finish_reason=="stop" → 返回最终文本

        max_tokens=0 → 使用 self.max_tokens (由 configure_online 同步)。
        finish_reason=length → 自动翻倍重试 1 次 (上限 16384)。

        Args:
            messages: 对话历史 [{'role':'system',...}, {'role':'user',...}]
            tools: OpenAI 工具定义列表（含 name / description / parameters）
            tool_executable_map: {函数名: 可执行函数} 映射
            temperature: 温度参数
            max_tokens: 每次请求最大 token 数
            enable_thinking: True=允许思考

        Returns:
            最终模型回复文本，失败或超轮次返回空字符串
        """
        if not self.is_available():
            raise AIClientNotConfiguredError("AI API Key 未配置，请在设置中配置 AI 模型")

        # 用单例字段兜底（与 generate() 同源）
        _max_tokens = max_tokens if max_tokens > 0 else self.max_tokens
        _temperature = temperature if temperature > 0 else self.temperature

        from openai import OpenAI  # pyright: ignore[reportImplicitRelativeImport]

        def _build_kwargs(msgs: list[Dict[str, Any]], mt: int) -> dict:
            kw: dict = {
                'model': self.model_name,
                'messages': msgs,
                'tools': tools,
                'temperature': _temperature,
                'max_tokens': mt,
            }
            _eb = self._thinking_extra_body(enable_thinking)
            if _eb is not None:
                kw['extra_body'] = _eb
                if self._is_local_qwen() and not enable_thinking:
                    kw.setdefault('temperature', self.QWEN_TEMPERATURE)
                    kw['top_p'] = self.QWEN_TOP_P
                    _eb.update({
                        'top_k': self.QWEN_TOP_K,
                        'presence_penalty': self.QWEN_PRESENCE_PENALTY,
                    })
            return kw

        try:
            kw_openai = self._make_openai_kwargs()
            try:
                client = OpenAI(**kw_openai)  # pyright: ignore[reportArgumentType]
            except TypeError:
                kw_openai.pop('http_client', None)
                kw_openai.pop('timeout', None)
                client = OpenAI(**kw_openai)  # pyright: ignore[reportArgumentType]

            MAX_TOOL_TURNS = 10
            _ctx_size = self._get_context_size()

            for attempt in range(2):  # 0=首次, 1=截断翻倍重试
                _current_mt = _max_tokens if attempt == 0 else min(_max_tokens * 2, _ctx_size)
                truncation_retry = False

                for turn in range(MAX_TOOL_TURNS):
                    response = client.chat.completions.create(
                        **_build_kwargs(messages, _current_mt),
                    )

                    choice = response.choices[0]
                    msg = choice.message

                    # ── 情况 A：模型请求调用工具 ──
                    if choice.finish_reason == 'tool_calls' and msg.tool_calls:
                        assistant_msg: Dict[str, Any] = {
                            'role': 'assistant',
                            'content': msg.content or '',
                        }
                        assistant_msg['tool_calls'] = [
                            {
                                'id': tc.id,
                                'type': 'function',
                                'function': {
                                    'name': tc.function.name,
                                    'arguments': tc.function.arguments,
                                },
                            }
                            for tc in msg.tool_calls
                        ]
                        messages.append(assistant_msg)

                        for tc in msg.tool_calls:
                            fn_name = tc.function.name
                            try:
                                fn_args = json.loads(tc.function.arguments)
                            except json.JSONDecodeError:
                                fn_args = {}

                            executable = tool_executable_map.get(fn_name)
                            if executable:
                                try:
                                    result = executable(**fn_args)
                                    result_str = (
                                        str(result)
                                        if result is not None
                                        else '(空结果)'
                                    )
                                except Exception as e:
                                    result_str = f'[工具执行错误: {e}]'
                            else:
                                result_str = f'[未知工具: {fn_name}]'

                            messages.append({
                                'role': 'tool',
                                'tool_call_id': tc.id,
                                'content': result_str,
                            })

                        continue  # 进入下一轮 Agent Loop

                    # ── 情况 B：模型正常输出 ──
                    content = (msg.content or '').strip()
                    finish = getattr(choice, 'finish_reason', 'stop')
                    if finish == 'length':
                        if attempt == 0 and _max_tokens < _ctx_size:
                            truncation_retry = True
                            break  # 退出 turn 循环, 翻倍重试
                        if content:
                            raise AIClientTruncationError(
                                f"AI 工具调用输出在 {_current_mt} token 处被截断"
                                + (f"（content={len(content)} chars）" if content else ""),
                                partial_content=content,
                            )
                        raise AIClientTruncationError(
                            f"AI 工具调用输出在 {_current_mt} token 处被截断且 content 为空"
                            f"（已自动翻倍重试 1 次）。",
                        )
                    if not content:
                        raise AIClientEmptyResponseError("AI 工具调用返回了空响应")
                    return content

                # turn 循环结束: truncation_retry 或 超轮次
                if truncation_retry:
                    continue  # next attempt with larger _current_mt

                # 超轮次保护
                raise AIClientServerError(
                    f"工具调用超过最大轮次 ({MAX_TOOL_TURNS})，强制终止"
                )

        except AIClientError:
            raise
        except Exception as e:
            raise classify_openai_error(e) from e
        return ""  # pyright: ignore[reportReturnType]  # unreachable — all paths return/raise



    def reset(self, api_key: str = '', base_url: str = '', model_name: str = '') -> None:
        """重置配置（允许运行时切换模型）."""
        if api_key:
            self.api_key = api_key
            self._api_key_backend = self._backend
        if base_url:
            self.base_url = base_url
        if model_name:
            self.model_name = model_name
        self._client = None

    @classmethod
    def get_instance(cls) -> 'AIClient':
        """获取全局单例."""
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    def generate_with_retry(
        self,
        prompt: str,
        system_prompt: str = '',
        temperature: float = 0.3,
        max_tokens: int = 2048,
        *,
        max_retries: int = 2,
        base_backoff_s: float = 2.0,
        enable_thinking: bool = False,
    ) -> str:
        """同步调用 LLM，带指数退避重试。

        仅对 retryable=True 的异常重试；不可重试的异常（如 401/403/400/422）立即抛出。

        Args:
            prompt: 用户提示词
            system_prompt: 系统提示词（可选）
            temperature: 温度参数
            max_tokens: 最大生成长度
            max_retries: 最大重试次数（默认 2 次，共 3 次尝试）
            base_backoff_s: 基础退避秒数（指数递增）
            enable_thinking: True=允许思考

        Returns:
            LLM 响应文本

        Raises:
            AIClientError 及其子类：重试耗尽或不可重试时抛出最后一个异常
        """
        last_exc: AIClientError | None = None

        for attempt in range(max_retries + 1):
            try:
                return self.generate(
                    prompt=prompt,
                    system_prompt=system_prompt,
                    temperature=temperature,
                    max_tokens=max_tokens,
                    enable_thinking=enable_thinking,
                )
            except AIClientError as e:
                last_exc = e
                if not e.retryable:
                    raise
                if attempt < max_retries:
                    wait = base_backoff_s * (2 ** attempt)
                    print(
                        f"[AIClient] 可重试错误 {type(e).__name__}，"
                        f"第 {attempt + 1}/{max_retries} 次重试，等待 {wait:.1f}s"
                    )
                    _time.sleep(wait)

        # 重试耗尽
        assert last_exc is not None  # pyright 推断
        raise last_exc

    def generate_structured(
        self,
        prompt: str,
        schema: type[BaseModel],
        system_prompt: str = "",
        temperature: float = 0.3,
        max_tokens: int = 2048,
        *,
        max_schema_retries: int = 1,
    ) -> Dict[str, Any]:
        """调用 LLM 并返回通过指定 Pydantic Schema 校验的 dict。

        对支持 json_schema 的端点使用 OpenAI 原生结构化输出；
        不支持时回退为 json_object + 后端校验 + 最多 1 次回喂修复重试。

        Args:
            prompt: 用户提示词
            schema: Pydantic BaseModel 子类（如 WordSection）
            system_prompt: 系统提示词
            temperature: 温度参数
            max_tokens: 最大生成长度
            max_schema_retries: schema 校验失败时的回喂重试次数（0=不重试）

        Returns:
            Pydantic model 的 dict 表示

        Raises:
            AIClientError: API 调用失败
            ReportSchemaError: 校验失败（含缺失字段/类型错误详情）
        """
        from core.ai_errors import ReportSchemaError

        schema_json = schema.model_json_schema()
        schema_name = schema_json.get("title", schema.__name__)

        # ── 将 JSON Schema 嵌入 system prompt，确保模型知晓期望的字段类型 ──
        # ★ 不用 markdown fence 包装 schema — 否则模型会模仿 fence 输出。
        _schema_text = json.dumps(schema_json, ensure_ascii=False, indent=2)
        _schema_instruction = (
            "\n\n---\n"
            "CRITICAL OUTPUT CONTRACT:\n"
            "1. Respond with RAW JSON only — NO markdown fences (```), "
            "NO code blocks, NO prose before or after.\n"
            "2. Your entire response MUST start with {{ and end with }}.\n"
            "3. Every required field in the schema below MUST be present.\n"
            "4. Pay close attention to field TYPES — e.g. 'string' means "
            "a text value, not an object or array.\n"
            "\n"
            "EXPECTED JSON SCHEMA (read carefully, output raw JSON matching this):\n"
            f"{_schema_text}"
        )
        _augmented_system = system_prompt + _schema_instruction

        for attempt in range(max_schema_retries + 1):
            raw = self.generate(
                prompt=prompt,
                system_prompt=_augmented_system,
                temperature=temperature,
                max_tokens=max_tokens,
                enable_thinking=False,  # 报告 JSON 必须纯答案，不能混入思维链
            )

            # ── 确定性 JSON 提取 ──
            import re as _re
            json_str = raw.strip()

            # Step 1: 去除完整 markdown fence（如有）
            m_fence = _re.match(
                r'```(?:json)?\s*\n?(.*?)\n?\s*```\s*$', json_str, _re.DOTALL
            )
            if m_fence:
                json_str = m_fence.group(1).strip()
            else:
                # Step 2: 去除开头不完整的 opening fence（```json 或 ```）
                # 模型可能输出了 opening fence 但忘记 closing fence
                _opening = _re.match(r'```(?:json)?\s*\n?', json_str)
                if _opening:
                    json_str = json_str[_opening.end():].strip()

            # Step 3: 如果清理后不以 {{ 或 [ 开头，拒绝猜测
            if not json_str.startswith(("{", "[")):
                # 不做启发式 JSON 片段搜寻 — 直接让本轮失败进入 corrective retry
                pass  # json_str 保持原样，json.loads / model_validate_json 会失败

            try:
                # ── JSON 转义清洗（修复 LLM 输出的 LaTeX 反斜杠等非法转义）──
                json_str = _sanitize_json_text(json_str)
                validated = schema.model_validate_json(json_str)
                return validated.model_dump()
            except Exception as e:
                # Collect structured error info for ReportSchemaError
                _json_parse_ok = False
                _json_error_detail = ""
                try:
                    _parsed = json.loads(json_str)
                    _json_parse_ok = True
                except json.JSONDecodeError as _jde:
                    _json_error_detail = f"JSONDecodeError at line {_jde.lineno}, col {_jde.colno}, pos {_jde.pos}: {str(_jde)[:300]}"
                except Exception as _je:
                    _json_error_detail = f"JSON parse error: {type(_je).__name__}: {str(_je)[:300]}"

                # If Pydantic ValidationError, get structured detail
                if hasattr(e, 'errors'):
                    try:
                        _errs = e.errors()
                    except Exception:
                        pass

                if attempt < max_schema_retries:
                    fix_hint = (
                        f"\n\n[系统提示：上次输出 JSON 格式校验失败，错误为: {e}。"
                        f"请严格按 JSON Schema 修正输出。]"
                    )
                    prompt = prompt + fix_hint
                else:
                    missing: list[str] = []
                    type_errs: list[str] = []
                    validation_errors: list[dict[str, object]] = []
                    if hasattr(e, "errors"):
                        for err in e.errors():
                            err_type = err.get("type", "")
                            loc = err.get("loc", ("?",))
                            # ── Structured validation error (no raw input_value) ──
                            _ve: dict[str, object] = {
                                "loc": ".".join(str(x) for x in loc),
                                "type": err_type,
                                "msg": str(err.get("msg", ""))[:200],
                            }
                            # Parse ctx for bounds info (e.g. max_length, actual_length)
                            ctx = err.get("ctx")
                            if isinstance(ctx, dict):
                                _ve["ctx"] = {
                                    k: v for k, v in ctx.items()
                                    if k in ("max_length", "min_length", "actual_length", "gt", "ge", "lt", "le")
                                }
                            validation_errors.append(_ve)
                            # Legacy fields for backward compat
                            if err_type == "missing":
                                missing.append(str(loc[0]))
                            else:
                                loc_str = ".".join(str(x) for x in loc)
                                type_errs.append(f"{loc_str}: {err.get('msg', '')}")
                    else:
                        type_errs = [str(e)]
                    raise ReportSchemaError(
                        f"AI 输出与 Schema '{schema_name}' 不匹配"
                        + (f"（已回馈修复 {max_schema_retries} 次）" if max_schema_retries > 0 else ""),
                        raw_text=raw,
                        missing_fields=missing,
                        type_errors=type_errs,
                        validation_errors=validation_errors,
                    ) from e

        # 理论上不会到达这里（所有路径均 return 或 raise）
        raise ReportSchemaError(
            f"generate_structured 意外退出：Schema '{schema_name}' 校验失败"
        )

    def get_generate_fn(self, enable_thinking: bool = False) -> Callable[..., str]:
        """返回兼容 report_engine 的 generate_fn 回调。

        返回的闭包签名: (prompt: str) -> str，失败时抛 AIClientError。
        默认 enable_thinking=False：报告/结构化路径关闭思考。

        调用方负责捕获异常并呈现给用户。
        """
        # 闭包捕获 self 和 enable_thinking
        _self = self
        _et = enable_thinking
        def _generate(prompt: str, **kwargs) -> str:
            return _self.generate(prompt=prompt, enable_thinking=_et, **kwargs)
        return _generate
