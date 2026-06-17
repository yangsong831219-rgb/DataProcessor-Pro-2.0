"""AI 客户端单例 — OpenAI 兼容接口，默认 DeepSeek-V4 Pro.

与 report_engine.py 配合：engine 通过 generate_fn 回调调用此客户端，
引擎不直接依赖 AIClient 类，保持完全解耦。

阶段一 (P0) 升级：generate() 不再吞异常返回空串，改为抛出 AIClientError
类型化异常。调用方通过 generate_with_retry() 获得指数退避重试。
"""

from __future__ import annotations

import json
import os
import time as _time
from typing import Any, Callable, Dict, Optional

from pydantic import BaseModel

from core.ai_errors import (
    AIClientError,
    AIClientEmptyResponseError,
    AIClientNotConfiguredError,
    AIClientServerError,
    classify_openai_error,
)


# ═══════════════════════════════════════════════════════════════════
# Schema 工具 — 将 Pydantic 模型转换为 LLM 提示词
# ═══════════════════════════════════════════════════════════════════


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


# ═══════════════════════════════════════════════════════════════════
# AIClient — 单例模式
# ═══════════════════════════════════════════════════════════════════


class AIClient:
    """OpenAI 兼容接口客户端.

    默认配置指向 DeepSeek-V4 Pro，可通过环境变量或参数覆盖。

    Usage:
        client = AIClient()
        resp = client.generate("写一段总结", "你是一个写作专家")
    """

    # 默认 DeepSeek 配置
    DEFAULT_BASE_URL = 'https://api.deepseek.com/v1'
    DEFAULT_MODEL = 'deepseek-v4-pro'

    # 类级单例
    _instance: Optional['AIClient'] = None

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

        self.api_key = api_key or os.environ.get(
            'DEEPSEEK_API_KEY',
            '',
        )
        self.base_url = base_url or os.environ.get(
            'DEEPSEEK_BASE_URL',
            self.DEFAULT_BASE_URL,
        )
        self.model_name = model_name or os.environ.get(
            'DEEPSEEK_MODEL',
            self.DEFAULT_MODEL,
        )
        self._client = None

    # ── 公共 API ──

    def is_available(self) -> bool:
        """检查 API Key 是否已配置."""
        return bool(self.api_key)

    def generate(
        self,
        prompt: str,
        system_prompt: str = '',
        temperature: float = 0.3,
        max_tokens: int = 2048,
    ) -> str:
        """同步调用 LLM，返回文本响应。

        Args:
            prompt: 用户提示词
            system_prompt: 系统提示词（可选）
            temperature: 温度参数，结构化输出建议 0.3
            max_tokens: 最大生成长度

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

        from openai import OpenAI  # pyright: ignore[reportImplicitRelativeImport]

        try:
            client = OpenAI(
                api_key=self.api_key,
                base_url=self.base_url,
            )

            messages: list[Dict[str, str]] = []
            if system_prompt:
                messages.append({'role': 'system', 'content': system_prompt})
            messages.append({'role': 'user', 'content': prompt})

            response = client.chat.completions.create(
                model=self.model_name,
                messages=messages,
                temperature=temperature,
                max_tokens=max_tokens,
            )

            content = (response.choices[0].message.content or '').strip()
            if not content:
                raise AIClientEmptyResponseError(
                    "AI 返回了空响应，可能模型不支持当前请求或输入格式有误"
                )
            return content

        except AIClientError:
            raise
        except Exception as e:
            raise classify_openai_error(e) from e

    # ── 多轮工具调用 (Function Calling / Agent Loop) ──

    def generate_with_tools(
        self,
        messages: list[Dict[str, Any]],
        tools: list[Dict[str, Any]],
        tool_executable_map: Dict[str, Any],
        temperature: float = 0.3,
        max_tokens: int = 4096,
    ) -> str:
        """支持多轮 Function Calling 的 Agent Loop.

        经典闭环：
          发起请求 → finish_reason=="tool_calls" → 解析参数 → 执行本地函数
          → 结果注入 history → 再次请求 → finish_reason=="stop" → 返回最终文本

        Args:
            messages: 对话历史 [{'role':'system',...}, {'role':'user',...}]
            tools: OpenAI 工具定义列表（含 name / description / parameters）
            tool_executable_map: {函数名: 可执行函数} 映射
            temperature: 温度参数
            max_tokens: 每次请求最大 token 数

        Returns:
            最终模型回复文本，失败或超轮次返回空字符串
        """
        if not self.is_available():
            raise AIClientNotConfiguredError("AI API Key 未配置，请在设置中配置 AI 模型")

        from openai import OpenAI  # pyright: ignore[reportImplicitRelativeImport]

        try:
            client = OpenAI(
                api_key=self.api_key,
                base_url=self.base_url,
            )

            MAX_TOOL_TURNS = 10

            for turn in range(MAX_TOOL_TURNS):
                response = client.chat.completions.create(
                    model=self.model_name,
                    messages=messages,
                    tools=tools,
                    temperature=temperature,
                    max_tokens=max_tokens,
                )

                choice = response.choices[0]
                msg = choice.message

                # ── 情况 A：模型请求调用工具 ──
                if choice.finish_reason == 'tool_calls' and msg.tool_calls:
                    # 将 assistant 的 tool_calls 消息追加到历史
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

                    # 逐一执行工具，将结果注入历史
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
                if not content:
                    raise AIClientEmptyResponseError("AI 工具调用返回了空响应")
                return content

            # 超轮次保护
            raise AIClientServerError(
                f"工具调用超过最大轮次 ({MAX_TOOL_TURNS})，强制终止"
            )

        except AIClientError:
            raise
        except Exception as e:
            raise classify_openai_error(e) from e

    def reset(self, api_key: str = '', base_url: str = '', model_name: str = '') -> None:
        """重置配置（允许运行时切换模型）."""
        if api_key:
            self.api_key = api_key
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

        for attempt in range(max_schema_retries + 1):
            raw = self.generate(
                prompt=prompt,
                system_prompt=system_prompt,
                temperature=temperature,
                max_tokens=max_tokens,
            )

            # 提取 JSON 块
            import re as _re
            m = _re.search(r'```(?:json)?\s*([\s\S]*?)\s*```', raw)
            json_str = m.group(1).strip() if m else raw.strip()
            # 若首尾不是 { 或 [, 尝试提取
            if not json_str.startswith(("{", "[")):
                m2 = _re.search(r'(\{[\s\S]*\}|\[[\s\S]*\])', json_str)
                if m2:
                    json_str = m2.group(1)

            try:
                validated = schema.model_validate_json(json_str)
                return validated.model_dump()
            except Exception as e:
                if attempt < max_schema_retries:
                    fix_hint = (
                        f"\n\n[系统提示：上次输出 JSON 格式校验失败，错误为: {e}。"
                        f"请严格按 JSON Schema 修正输出。]"
                    )
                    prompt = prompt + fix_hint
                else:
                    missing: list[str] = []
                    type_errs: list[str] = []
                    if hasattr(e, "errors"):
                        for err in e.errors():
                            err_type = err.get("type", "")
                            loc = err.get("loc", ("?",))
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
                    ) from e

        # 理论上不会到达这里（所有路径均 return 或 raise）
        raise ReportSchemaError(
            f"generate_structured 意外退出：Schema '{schema_name}' 校验失败"
        )

    def get_generate_fn(self) -> Callable[..., str]:
        """返回兼容 report_engine 的 generate_fn 回调。

        返回的闭包签名: (prompt: str) -> str，失败时抛 AIClientError。
        调用方负责捕获异常并呈现给用户。
        """
        return self.generate
