"""受控 Agent 工具执行适配器。

该模块是多智能体图唯一允许执行 ``BaseTool`` 的位置。模型节点只产生
结构化调用请求；本适配器负责白名单、参数、超时、结果净化与 ToolMessage。
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeoutError
from dataclasses import dataclass
import json
import math
import os
import re
import time
from collections.abc import Mapping

from langchain_core.messages import ToolMessage
from langchain_core.tools import BaseTool
from pydantic import ValidationError

from dp_engine.agent_skill_hub import get_default_agent_tools


MAX_ARGUMENT_BYTES = 16 * 1024
MAX_TOOL_OUTPUT_CHARS = 8_192
DEFAULT_TOOL_TIMEOUT_S = 5.0
MAX_TOTAL_TOOL_BUDGET_S = 20.0

ALLOWED_ERROR_CODES = frozenset({
    "unknown_tool",
    "invalid_arguments",
    "tool_timeout",
    "tool_exception",
    "output_rejected",
    "repeated_tool_call",
    "tool_round_limit",
})

_SENSITIVE_KEY_PATTERN = re.compile(
    r"(?:api[_-]?key|authorization|cookie|password|secret|token)",
    re.IGNORECASE,
)
_WINDOWS_PATH_PATTERN = re.compile(
    r"(?<![\w])(?:[A-Za-z]:[\\/])[^\s\"'<>|]+",
)
_USER_PATH_PATTERN = re.compile(
    r"(?<![\w])/(?:home|Users)/[^\s\"'<>|]+",
)
_BEARER_PATTERN = re.compile(r"\bBearer\s+[A-Za-z0-9._~+/=-]+", re.IGNORECASE)
_COMMON_SECRET_PATTERN = re.compile(
    r"\b(?:sk|pk|api)[-_][A-Za-z0-9_-]{12,}\b",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class ToolExecutionResult:
    """一次受控执行的稳定返回合同。"""

    message: ToolMessage
    signature: str | None
    elapsed_s: float
    error_code: str | None


def _canonical_json(value: object) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def _redact_text(value: str) -> str:
    redacted = _BEARER_PATTERN.sub("[REDACTED]", value)
    redacted = _COMMON_SECRET_PATTERN.sub("[REDACTED]", redacted)
    redacted = _WINDOWS_PATH_PATTERN.sub("[REDACTED_PATH]", redacted)
    redacted = _USER_PATH_PATTERN.sub("[REDACTED_PATH]", redacted)
    for env_value in os.environ.values():
        if len(env_value) >= 8 and env_value in redacted:
            redacted = redacted.replace(env_value, "[REDACTED]")
    return redacted


def _sanitize_json_value(value: object) -> object:
    if value is None or isinstance(value, (bool, int)):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError("non-finite number")
        return value
    if isinstance(value, str):
        return _redact_text(value)
    if isinstance(value, list):
        return [_sanitize_json_value(item) for item in value]
    if isinstance(value, tuple):
        return [_sanitize_json_value(item) for item in value]
    if isinstance(value, Mapping):
        result: dict[str, object] = {}
        for raw_key, raw_value in value.items():
            if not isinstance(raw_key, str):
                raise ValueError("non-string mapping key")
            key = _redact_text(raw_key)
            if _SENSITIVE_KEY_PATTERN.search(key):
                result[key] = "[REDACTED]"
            else:
                result[key] = _sanitize_json_value(raw_value)
        return result
    raise ValueError("non JSON-compatible output")


def _error_content(tool_name: str, code: str, message: str) -> str:
    safe_code = code if code in ALLOWED_ERROR_CODES else "tool_exception"
    payload = {
        "ok": False,
        "tool": tool_name,
        "error": {
            "code": safe_code,
            "message": _redact_text(message)[:512],
        },
    }
    return _canonical_json(payload)


def _bounded_success_content(tool_name: str, data: object) -> str:
    safe_data = _sanitize_json_value(data)
    payload: dict[str, object] = {
        "ok": True,
        "tool": tool_name,
        "data": safe_data,
        "truncated": False,
    }
    encoded = _canonical_json(payload)
    if len(encoded) <= MAX_TOOL_OUTPUT_CHARS:
        return encoded

    data_text = _canonical_json(safe_data)
    preview_chars = min(7_000, len(data_text))
    while preview_chars >= 0:
        truncated_payload: dict[str, object] = {
            "ok": True,
            "tool": tool_name,
            "data": {"preview": data_text[:preview_chars]},
            "truncated": True,
            "original_chars": len(encoded),
        }
        encoded = _canonical_json(truncated_payload)
        if len(encoded) <= MAX_TOOL_OUTPUT_CHARS:
            return encoded
        preview_chars -= 256
    raise ValueError("unable to bound tool output")


class AgentToolAdapter:
    """严格白名单的同步纯内存工具执行器。"""

    def __init__(self, tools: list[BaseTool] | None = None) -> None:
        selected = list(tools) if tools is not None else get_default_agent_tools()
        self._tools = {tool.name: tool for tool in selected}
        if len(self._tools) != len(selected):
            raise ValueError("工具名称必须唯一")

    @property
    def tool_names(self) -> tuple[str, ...]:
        return tuple(self._tools)

    def openai_tool_schemas(self) -> list[dict[str, object]]:
        schemas: list[dict[str, object]] = []
        for tool in self._tools.values():
            schema = tool.tool_call_schema
            parameters = (
                dict(schema)
                if isinstance(schema, dict)
                else schema.model_json_schema()
            )
            parameters.pop("title", None)
            parameters.pop("description", None)
            schemas.append({
                "type": "function",
                "function": {
                    "name": tool.name,
                    "description": tool.description or tool.name,
                    "parameters": parameters,
                },
            })
        return schemas

    def error_result(
        self,
        *,
        call_id: str,
        tool_name: str,
        code: str,
        message: str,
        signature: str | None = None,
    ) -> ToolExecutionResult:
        safe_id = call_id.strip() or "invalid-tool-call-id"
        safe_name = tool_name.strip() or "unknown"
        return ToolExecutionResult(
            message=ToolMessage(
                content=_error_content(safe_name, code, message),
                tool_call_id=safe_id,
            ),
            signature=signature,
            elapsed_s=0.0,
            error_code=code if code in ALLOWED_ERROR_CODES else "tool_exception",
        )

    def execute(
        self,
        call: Mapping[str, object],
        *,
        previous_signature: str | None = None,
        timeout_s: float = DEFAULT_TOOL_TIMEOUT_S,
        remaining_budget_s: float = MAX_TOTAL_TOOL_BUDGET_S,
    ) -> ToolExecutionResult:
        call_id = str(call.get("id") or "")
        tool_name = str(call.get("name") or "")
        raw_arguments = call.get("arguments")

        if not call_id.strip():
            return self.error_result(
                call_id=call_id,
                tool_name=tool_name,
                code="invalid_arguments",
                message="tool call id 不能为空",
            )
        if not tool_name.strip():
            return self.error_result(
                call_id=call_id,
                tool_name=tool_name,
                code="invalid_arguments",
                message="tool name 不能为空",
            )
        if not isinstance(raw_arguments, str):
            return self.error_result(
                call_id=call_id,
                tool_name=tool_name,
                code="invalid_arguments",
                message="arguments 必须是 JSON 字符串",
            )
        if len(raw_arguments.encode("utf-8")) > MAX_ARGUMENT_BYTES:
            return self.error_result(
                call_id=call_id,
                tool_name=tool_name,
                code="invalid_arguments",
                message="arguments 超过 16 KiB",
            )
        try:
            arguments = json.loads(raw_arguments)
        except (json.JSONDecodeError, UnicodeError):
            return self.error_result(
                call_id=call_id,
                tool_name=tool_name,
                code="invalid_arguments",
                message="arguments 不是合法 JSON",
            )
        if not isinstance(arguments, dict):
            return self.error_result(
                call_id=call_id,
                tool_name=tool_name,
                code="invalid_arguments",
                message="arguments 必须是 JSON object",
            )

        signature = f"{tool_name}:{_canonical_json(arguments)}"
        if signature == previous_signature:
            return self.error_result(
                call_id=call_id,
                tool_name=tool_name,
                code="repeated_tool_call",
                message="禁止连续重复调用相同工具和参数",
                signature=signature,
            )

        tool = self._tools.get(tool_name)
        if tool is None:
            return self.error_result(
                call_id=call_id,
                tool_name=tool_name,
                code="unknown_tool",
                message="工具不在允许白名单中",
                signature=signature,
            )

        schema = tool.tool_call_schema
        if isinstance(schema, dict):
            raw_properties = schema.get("properties", {})
            allowed_keys = set(raw_properties) if isinstance(raw_properties, dict) else set()
        else:
            allowed_keys = set(schema.model_fields)
        unknown_keys = sorted(set(arguments) - allowed_keys)
        if unknown_keys:
            return self.error_result(
                call_id=call_id,
                tool_name=tool_name,
                code="invalid_arguments",
                message="arguments 包含未知字段",
                signature=signature,
            )
        try:
            validated = (
                dict(arguments)
                if isinstance(schema, dict)
                else schema.model_validate(arguments).model_dump()
            )
        except ValidationError:
            return self.error_result(
                call_id=call_id,
                tool_name=tool_name,
                code="invalid_arguments",
                message="arguments 未通过工具 schema 校验",
                signature=signature,
            )

        effective_timeout = min(timeout_s, remaining_budget_s)
        if effective_timeout <= 0:
            return self.error_result(
                call_id=call_id,
                tool_name=tool_name,
                code="tool_timeout",
                message="工具总时间预算已耗尽",
                signature=signature,
            )

        started = time.perf_counter()
        executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="agent-safe-tool")
        future = executor.submit(tool.invoke, validated)
        try:
            raw_result = future.result(timeout=effective_timeout)
        except FutureTimeoutError:
            future.cancel()
            elapsed = time.perf_counter() - started
            return ToolExecutionResult(
                message=ToolMessage(
                    content=_error_content(tool_name, "tool_timeout", "工具调用超时"),
                    tool_call_id=call_id,
                ),
                signature=signature,
                elapsed_s=elapsed,
                error_code="tool_timeout",
            )
        except Exception:
            elapsed = time.perf_counter() - started
            return ToolExecutionResult(
                message=ToolMessage(
                    content=_error_content(tool_name, "tool_exception", "工具执行失败"),
                    tool_call_id=call_id,
                ),
                signature=signature,
                elapsed_s=elapsed,
                error_code="tool_exception",
            )
        finally:
            executor.shutdown(wait=False, cancel_futures=True)

        elapsed = time.perf_counter() - started
        try:
            normalized = _sanitize_json_value(raw_result)
            if isinstance(normalized, dict) and normalized.get("ok") is False:
                raw_error = normalized.get("error")
                raw_code = "tool_exception"
                raw_message = "工具返回失败"
                if isinstance(raw_error, dict):
                    raw_code = str(raw_error.get("code") or raw_code)
                    raw_message = str(raw_error.get("message") or raw_message)
                safe_code = raw_code if raw_code in ALLOWED_ERROR_CODES else "tool_exception"
                return ToolExecutionResult(
                    message=ToolMessage(
                        content=_error_content(tool_name, safe_code, raw_message),
                        tool_call_id=call_id,
                    ),
                    signature=signature,
                    elapsed_s=elapsed,
                    error_code=safe_code,
                )
            data = normalized.get("data") if isinstance(normalized, dict) and normalized.get("ok") is True else normalized
            content = _bounded_success_content(tool_name, data)
        except (TypeError, ValueError):
            return ToolExecutionResult(
                message=ToolMessage(
                    content=_error_content(tool_name, "output_rejected", "工具输出不是安全 JSON 数据"),
                    tool_call_id=call_id,
                ),
                signature=signature,
                elapsed_s=elapsed,
                error_code="output_rejected",
            )

        return ToolExecutionResult(
            message=ToolMessage(content=content, tool_call_id=call_id),
            signature=signature,
            elapsed_s=elapsed,
            error_code=None,
        )
