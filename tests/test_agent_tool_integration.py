"""Batch 3.4.2：受控工具执行图合同测试（全部离线）。"""

from __future__ import annotations

import json
import time
from types import SimpleNamespace

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, ToolMessage
from langchain_core.tools import tool
import pytest

from dp_engine.agent_tool_adapter import (
    AgentToolAdapter,
    MAX_TOOL_OUTPUT_CHARS,
)
from core.ai_client import AIToolCall


@pytest.fixture(autouse=True)
def _isolate_ai_client_singleton():
    """新增测试不得把进程级 AIClient 配置泄漏给后续既有测试。"""
    from core.ai_client import AIClient

    previous = AIClient._instance
    AIClient._instance = None
    try:
        yield
    finally:
        AIClient._instance = previous


def _formula_call(
    *,
    call_id: str = "call-1",
    factor: float = 2.0,
) -> AIToolCall:
    return {
        "id": call_id,
        "name": "execute_custom_formula",
        "arguments": json.dumps({
            "columns": {"x": [1.0, 2.0, 3.0]},
            "formula_str": "x * factor",
            "params": {"factor": factor},
        }),
    }


def _payload(message: ToolMessage) -> dict:
    return json.loads(str(message.content))


class _FakeAI:
    def __init__(self, steps: list[dict]) -> None:
        self.steps = list(steps)
        self.requests: list[dict] = []

    def generate_tool_step(self, **kwargs):
        self.requests.append(kwargs)
        return self.steps.pop(0)


def _final_step(content: str = "数据科学家最终报告") -> dict:
    return {
        "content": content,
        "finish_reason": "stop",
        "tool_calls": [],
        "tool_calling_unavailable": False,
    }


def _tool_step(call: AIToolCall) -> dict:
    return {
        "content": "",
        "finish_reason": "tool_calls",
        "tool_calls": [call],
        "tool_calling_unavailable": False,
    }


def test_default_adapter_exposes_only_frozen_safe_tools():
    adapter = AgentToolAdapter()
    assert adapter.tool_names == (
        "apply_butterworth_filter",
        "execute_custom_formula",
    )
    schemas = adapter.openai_tool_schemas()
    functions = [item.get("function") for item in schemas]
    assert all(isinstance(function, dict) for function in functions)
    assert [function.get("name") for function in functions if isinstance(function, dict)] == list(adapter.tool_names)
    assert all("file_path" not in json.dumps(item) for item in schemas)


def test_adapter_executes_valid_formula_and_aligns_tool_call_id():
    result = AgentToolAdapter().execute(_formula_call(call_id="aligned-id"))
    payload = _payload(result.message)
    assert result.message.tool_call_id == "aligned-id"
    assert result.error_code is None
    assert payload["ok"] is True
    assert payload["tool"] == "execute_custom_formula"
    assert payload["data"]["values"] == [2.0, 4.0, 6.0]


@pytest.mark.parametrize(
    "arguments",
    [
        "{broken",
        "[]",
        json.dumps({
            "columns": {"x": [1.0]},
            "formula_str": "x",
            "unexpected": True,
        }),
        json.dumps({"columns": {"x": [1.0]}, "formula_str": 123}),
        json.dumps({
            "columns": {"x": [1.0] * 2049},
            "formula_str": "x",
        }),
    ],
)
def test_adapter_rejects_invalid_json_object_schema_and_bounds(arguments: str):
    result = AgentToolAdapter().execute({
        "id": "invalid-id",
        "name": "execute_custom_formula",
        "arguments": arguments,
    })
    payload = _payload(result.message)
    assert payload["ok"] is False
    assert payload["error"]["code"] == "invalid_arguments"


def test_adapter_rejects_unknown_tool_without_execution():
    result = AgentToolAdapter().execute({
        "id": "unknown-id",
        "name": "python_repl",
        "arguments": "{}",
    })
    assert _payload(result.message)["error"]["code"] == "unknown_tool"


def test_adapter_converts_tool_exception_to_generic_error():
    @tool
    def boom_tool(value: int) -> dict:
        """Always fail for adapter testing."""
        raise RuntimeError(f"D:/secret/path/{value}")

    result = AgentToolAdapter([boom_tool]).execute({
        "id": "boom-id",
        "name": "boom_tool",
        "arguments": '{"value":1}',
    })
    content = str(result.message.content)
    assert _payload(result.message)["error"]["code"] == "tool_exception"
    assert "secret" not in content
    assert "RuntimeError" not in content


def test_adapter_enforces_per_tool_timeout():
    @tool
    def slow_tool(value: int) -> dict:
        """Sleep briefly for timeout testing."""
        time.sleep(0.05)
        return {"value": value}

    result = AgentToolAdapter([slow_tool]).execute(
        {"id": "slow-id", "name": "slow_tool", "arguments": '{"value":1}'},
        timeout_s=0.001,
    )
    assert _payload(result.message)["error"]["code"] == "tool_timeout"


def test_adapter_redacts_secrets_environment_values_and_absolute_paths(monkeypatch):
    monkeypatch.setenv("BATCH342_SECRET", "environment-secret-value")

    @tool
    def secret_tool() -> dict:
        """Return unsafe-looking output for sanitization testing."""
        return {
            "api_key": "sk-abcdefghijklmnop",
            "note": "Bearer abcdefghijklmnop",
            "env": "environment-secret-value",
            "path": r"C:\Users\Administrator\private\data.csv",
        }

    result = AgentToolAdapter([secret_tool]).execute({
        "id": "secret-id",
        "name": "secret_tool",
        "arguments": "{}",
    })
    content = str(result.message.content)
    assert "environment-secret-value" not in content
    assert "abcdefghijklmnop" not in content
    assert "Administrator" not in content
    assert "[REDACTED" in content


def test_adapter_truncates_as_valid_json_without_cutting_payload():
    @tool
    def large_tool() -> dict:
        """Return a deliberately oversized JSON-compatible value."""
        return {"blob": "x" * 20_000}

    result = AgentToolAdapter([large_tool]).execute({
        "id": "large-id",
        "name": "large_tool",
        "arguments": "{}",
    })
    content = str(result.message.content)
    payload = json.loads(content)
    assert len(content) <= MAX_TOOL_OUTPUT_CHARS
    assert payload["truncated"] is True
    assert payload["original_chars"] > MAX_TOOL_OUTPUT_CHARS


def test_adapter_rejects_consecutive_duplicate_signature():
    adapter = AgentToolAdapter()
    first = adapter.execute(_formula_call())
    second = adapter.execute(_formula_call(call_id="call-2"), previous_signature=first.signature)
    assert _payload(second.message)["error"]["code"] == "repeated_tool_call"


def test_tool_node_rejects_more_than_one_call_and_aligns_every_id():
    from dp_engine.multi_agent import _make_tool_node

    node = _make_tool_node(AgentToolAdapter())
    result = node({
        "pending_tool_calls": [
            _formula_call(call_id="first"),
            _formula_call(call_id="second", factor=3.0),
        ],
        "execution_logs": [],
    })
    messages = result.get("messages", [])
    assert all(isinstance(message, ToolMessage) for message in messages)
    tool_messages = [message for message in messages if isinstance(message, ToolMessage)]
    assert [message.tool_call_id for message in tool_messages] == ["first", "second"]
    assert result.get("force_tool_free") is True
    assert all(
        _payload(message)["error"]["code"] == "invalid_arguments"
        for message in tool_messages
    )


def test_tool_node_enforces_four_round_limit():
    from dp_engine.multi_agent import MAX_TOOL_ROUNDS, _make_tool_node

    node = _make_tool_node(AgentToolAdapter())
    result = node({
        "pending_tool_calls": [_formula_call(call_id="limit-id")],
        "tool_rounds": MAX_TOOL_ROUNDS,
        "execution_logs": [],
    })
    messages = result.get("messages", [])
    assert messages and isinstance(messages[0], ToolMessage)
    assert _payload(messages[0])["error"]["code"] == "tool_round_limit"
    assert result.get("force_tool_free") is True


def test_tool_node_honours_cancellation_before_execution():
    from dp_engine.multi_agent import _make_tool_node

    node = _make_tool_node(AgentToolAdapter(), cancel_check=lambda: True)
    result = node({
        "pending_tool_calls": [_formula_call()],
        "execution_logs": [],
    })
    assert result.get("cancelled") is True
    assert "messages" not in result


def test_tool_node_honours_cancellation_after_inflight_execution():
    from dp_engine.multi_agent import _make_tool_node

    checks = iter([False, True])
    node = _make_tool_node(AgentToolAdapter(), cancel_check=lambda: next(checks))
    result = node({
        "pending_tool_calls": [_formula_call()],
        "execution_logs": [],
        "tool_rounds": 0,
        "tool_elapsed_s": 0.0,
    })
    assert result.get("cancelled") is True
    assert isinstance(result.get("messages", [None])[0], ToolMessage)


def test_data_scientist_can_return_directly_without_tool(monkeypatch):
    import dp_engine.multi_agent as ma
    from core.ai_client import AIClient

    fake = _FakeAI([_final_step()])
    monkeypatch.setattr(AIClient, "get_instance", staticmethod(lambda: fake))
    ds_node, _, _ = ma._make_nodes(tool_adapter=AgentToolAdapter())
    result = ds_node({"messages": [HumanMessage(content="分析")], "execution_logs": []})
    assert result.get("data_scientist_report") == "数据科学家最终报告"
    assert fake.requests[0]["tools"]


def test_data_scientist_unsupported_tools_falls_back_once(monkeypatch):
    import dp_engine.multi_agent as ma
    from core.ai_client import AIClient

    fake = _FakeAI([
        {
            "content": "",
            "finish_reason": "tool_calling_unavailable",
            "tool_calls": [],
            "tool_calling_unavailable": True,
        },
        _final_step("纯文本降级报告"),
    ])
    monkeypatch.setattr(AIClient, "get_instance", staticmethod(lambda: fake))
    ds_node, _, _ = ma._make_nodes(tool_adapter=AgentToolAdapter())
    result = ds_node({"messages": [HumanMessage(content="分析")], "execution_logs": []})
    assert result.get("data_scientist_report") == "纯文本降级报告"
    assert result.get("tool_calling_unavailable") is True
    assert len(fake.requests) == 2
    assert fake.requests[0]["tools"]
    assert fake.requests[1]["tools"] == []


def test_advisor_and_chief_never_receive_tools(monkeypatch):
    import dp_engine.multi_agent as ma

    observed: list[str] = []

    def fake_invoke(system_prompt: str, user_prompt: str, max_tokens: int = 0) -> str:
        observed.append(system_prompt)
        return "{}"

    monkeypatch.setattr(ma, "_invoke_llm", fake_invoke)
    _, advisor, chief = ma._make_nodes(tool_adapter=AgentToolAdapter())
    state_messages: list[BaseMessage] = [AIMessage(content="DS")]
    state = ma.MultiAgentState(
        messages=state_messages,
        execution_logs=[],
        data_scientist_report="DS",
        audit_advisory="建议",
    )
    advisor(state)
    chief(state)
    assert len(observed) == 2
    assert all("tools" not in prompt.lower() for prompt in observed)


def test_full_graph_completes_one_tool_round_trip(monkeypatch):
    import dp_engine.multi_agent as ma
    from core.ai_client import AIClient

    fake = _FakeAI([_tool_step(_formula_call()), _final_step("工具后报告")])
    monkeypatch.setattr(AIClient, "get_instance", staticmethod(lambda: fake))
    monkeypatch.setattr(ma, "_invoke_llm", lambda *args, **kwargs: "{}")

    result = ma.run_multi_agent("分析数据")
    assert result["data_scientist_text"] == "工具后报告"
    assert result["cancelled"] is False
    assert len(fake.requests) == 2
    assert any(message["role"] == "tool" for message in fake.requests[1]["messages"])
    assistant = next(
        message for message in fake.requests[1]["messages"]
        if message["role"] == "assistant" and message.get("tool_calls")
    )
    assert assistant["tool_calls"][0]["id"] == "call-1"


def test_full_graph_supports_multiple_nonrepeating_tool_rounds(monkeypatch):
    import dp_engine.multi_agent as ma
    from core.ai_client import AIClient

    fake = _FakeAI([
        _tool_step(_formula_call(call_id="call-1", factor=2.0)),
        _tool_step(_formula_call(call_id="call-2", factor=3.0)),
        _final_step("多轮完成"),
    ])
    monkeypatch.setattr(AIClient, "get_instance", staticmethod(lambda: fake))
    monkeypatch.setattr(ma, "_invoke_llm", lambda *args, **kwargs: "{}")

    result = ma.run_multi_agent("分析数据")
    assert result["data_scientist_text"] == "多轮完成"
    assert len(fake.requests) == 3
    assert sum(message["role"] == "tool" for message in fake.requests[2]["messages"]) == 2


def test_ai_client_tool_step_preserves_raw_call(monkeypatch):
    import openai
    from core.ai_client import AIClient

    raw_arguments = '{"nonce":"abc"}'
    call = SimpleNamespace(
        id="provider-id",
        function=SimpleNamespace(name="capability_echo", arguments=raw_arguments),
    )
    response = SimpleNamespace(choices=[SimpleNamespace(
        finish_reason="tool_calls",
        message=SimpleNamespace(content="", tool_calls=[call]),
    )])
    completions = SimpleNamespace(create=lambda **kwargs: response)
    monkeypatch.setattr(
        openai,
        "OpenAI",
        lambda **kwargs: SimpleNamespace(chat=SimpleNamespace(completions=completions)),
    )
    client = object.__new__(AIClient)
    client._backend = "online"
    client.api_key = "configured"
    client.base_url = "https://example.invalid/v1"
    client.model_name = "test-model"
    client.max_tokens = 128
    client.temperature = 0.3
    monkeypatch.setattr(client, "_make_openai_kwargs", lambda: {})
    monkeypatch.setattr(client, "_thinking_extra_body", lambda enabled: None)

    result = client.generate_tool_step(
        [{"role": "user", "content": "call"}],
        [{"type": "function", "function": {"name": "capability_echo", "parameters": {"type": "object"}}}],
    )
    assert result["tool_calls"] == [{
        "id": "provider-id",
        "name": "capability_echo",
        "arguments": raw_arguments,
    }]


@pytest.mark.parametrize("status_code", [400, 422])
def test_ai_client_tool_step_marks_provider_rejection_unavailable(monkeypatch, status_code: int):
    import openai
    from core.ai_client import AIClient

    class ProviderRejected(Exception):
        def __init__(self, status: int) -> None:
            super().__init__("provider rejected tools")
            self.status_code = status

    def reject(**kwargs):
        raise ProviderRejected(status_code)

    completions = SimpleNamespace(create=reject)
    monkeypatch.setattr(
        openai,
        "OpenAI",
        lambda **kwargs: SimpleNamespace(chat=SimpleNamespace(completions=completions)),
    )
    client = object.__new__(AIClient)
    client._backend = "online"
    client.api_key = "configured"
    client.base_url = "https://example.invalid/v1"
    client.model_name = "test-model"
    client.max_tokens = 128
    client.temperature = 0.3
    monkeypatch.setattr(client, "_make_openai_kwargs", lambda: {})
    monkeypatch.setattr(client, "_thinking_extra_body", lambda enabled: None)

    result = client.generate_tool_step(
        [{"role": "user", "content": "call"}],
        [{"type": "function", "function": {"name": "capability_echo", "parameters": {"type": "object"}}}],
    )
    assert result["tool_calling_unavailable"] is True
    assert result["tool_calls"] == []
