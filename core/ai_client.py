"""AI 客户端单例 — OpenAI 兼容接口，默认 DeepSeek-V4 Pro.

与 report_engine.py 配合：engine 通过 generate_fn 回调调用此客户端，
引擎不直接依赖 AIClient 类，保持完全解耦。
"""

from __future__ import annotations

import json
import os
from typing import Any, Callable, Dict, Optional

from pydantic import BaseModel


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
        """同步调用 LLM，返回文本响应.

        Args:
            prompt: 用户提示词
            system_prompt: 系统提示词（可选）
            temperature: 温度参数，结构化输出建议 0.3
            max_tokens: 最大生成长度

        Returns:
            LLM 响应文本，失败返回空字符串
        """
        if not self.is_available():
            print('[AIClient] API Key 未配置')
            return ''

        try:
            from openai import OpenAI

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

            content = response.choices[0].message.content or ''
            return content.strip()

        except Exception as e:
            print(f'[AIClient] 调用失败: {e}')
            return ''

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
            print('[AIClient] API Key 未配置')
            return ''

        try:
            from openai import OpenAI

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
                return (msg.content or '').strip()

            # 超轮次保护
            print(
                '[AIClient] 工具调用超过最大轮次，强制终止'
            )
            return ''

        except Exception as e:
            print(f'[AIClient] 工具调用失败: {e}')
            return ''

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

    def get_generate_fn(self) -> Callable[..., str]:
        """返回兼容 report_engine 的 generate_fn 回调.

        返回的闭包签名: (prompt: str) -> str
        """
        return self.generate
