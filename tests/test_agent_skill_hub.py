"""Batch 3.4.1 contracts for the fixed built-in Agent tool catalog."""

from __future__ import annotations

import builtins
import importlib.metadata
import math
from pathlib import Path
from typing import Any

import pytest

from dp_engine import agent_skill_hub as hub


EXPECTED_CATALOG_NAMES = {
    "python_repl",
    "arxiv",
    "ddg_search",
    "wikipedia",
    "apply_butterworth_filter",
    "execute_custom_formula",
    "read_wiki_page",
    "write_wiki_page",
    "list_wiki_pages",
    "search_wiki_pages",
    "semantic_search_wiki",
}


def _local_tools() -> dict[str, Any]:
    return {tool.name: tool for tool in hub._get_local_tools()}


def _assert_error(result: object, code: str) -> None:
    assert isinstance(result, dict)
    assert result.get("ok") is False
    error = result.get("error")
    assert isinstance(error, dict)
    assert error.get("code") == code
    assert isinstance(error.get("message"), str)


def test_requirements_pin_agent_dependencies_exactly() -> None:
    requirements = (
        Path(__file__).resolve().parents[1] / "requirements.txt"
    ).read_text(encoding="utf-8").splitlines()
    expected = {
        "langchain-core==1.4.0",
        "langgraph==1.2.1",
        "langchain-experimental==0.4.2",
        "langchain-community==0.4.2",
    }
    assert expected.issubset(set(requirements))
    assert importlib.metadata.version("langchain-core") == "1.4.0"
    assert importlib.metadata.version("langgraph") == "1.2.1"
    assert importlib.metadata.version("langchain-experimental") == "0.4.2"
    assert importlib.metadata.version("langchain-community") == "0.4.2"


def test_catalog_covers_every_fixed_candidate_once() -> None:
    catalog = hub.get_tool_safety_catalog()
    names = [profile.name for profile in catalog]
    assert len(names) == len(set(names))
    assert set(names) == EXPECTED_CATALOG_NAMES


def test_default_whitelist_is_exact_and_mechanically_safe() -> None:
    assert hub.DEFAULT_AGENT_TOOL_NAMES == (
        "apply_butterworth_filter",
        "execute_custom_formula",
    )
    profiles = {profile.name: profile for profile in hub.get_tool_safety_catalog()}
    approved = {
        name for name, profile in profiles.items() if profile.default_allowed
    }
    assert approved == set(hub.DEFAULT_AGENT_TOOL_NAMES)
    for name in hub.DEFAULT_AGENT_TOOL_NAMES:
        profile = profiles[name]
        assert profile.network_access is False
        assert profile.file_read is False
        assert profile.file_write is False
        assert profile.code_execution is False
        assert profile.third_party is False


def test_default_tools_do_not_load_third_party(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def forbidden() -> dict[str, object]:
        raise AssertionError("default path must not load third-party tools")

    monkeypatch.setattr(hub, "get_third_party_tools", forbidden)
    assert [tool.name for tool in hub.get_default_agent_tools()] == list(
        hub.DEFAULT_AGENT_TOOL_NAMES
    )


def test_default_category_matches_default_interface() -> None:
    assert [tool.name for tool in hub.get_tools_by_category("default")] == list(
        hub.DEFAULT_AGENT_TOOL_NAMES
    )
    assert hub.get_tools_by_category("unknown") == []


def test_optional_third_party_imports_fail_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    real_import = builtins.__import__

    def reject_optional(
        name: str,
        globals: dict[str, object] | None = None,
        locals: dict[str, object] | None = None,
        fromlist: tuple[str, ...] = (),
        level: int = 0,
    ) -> object:
        if name.startswith(("langchain_experimental", "langchain_community")):
            raise ImportError("optional dependency unavailable")
        return real_import(name, globals, locals, fromlist, level)

    monkeypatch.setattr(builtins, "__import__", reject_optional)
    assert hub._import_third_party_tools(silent=True) == {}


def test_safe_tool_schemas_accept_no_file_paths() -> None:
    tools = _local_tools()
    filter_schema = tools["apply_butterworth_filter"].tool_call_schema
    formula_schema = tools["execute_custom_formula"].tool_call_schema
    filter_json = filter_schema.model_json_schema()
    formula_json = formula_schema.model_json_schema()
    assert "file_path" not in filter_json.get("properties", {})
    assert "file_path" not in formula_json.get("properties", {})
    assert {"values", "cutoff_hz", "sampling_rate_hz"}.issubset(
        filter_json["properties"]
    )
    assert {"columns", "formula_str", "params"}.issubset(
        formula_json["properties"]
    )


def test_filter_runs_in_memory_and_returns_structured_data() -> None:
    tool = _local_tools()["apply_butterworth_filter"]
    values = [
        math.sin(2 * math.pi * 2 * index / 100)
        + 0.2 * math.sin(2 * math.pi * 30 * index / 100)
        for index in range(64)
    ]
    result = tool.invoke({
        "values": values,
        "cutoff_hz": 10.0,
        "sampling_rate_hz": 100.0,
        "order": 4,
        "filter_type": "lowpass",
    })
    assert isinstance(result, dict)
    assert result.get("ok") is True
    data = result.get("data")
    assert isinstance(data, dict)
    assert data.get("total_count") == 64
    assert data.get("returned_count") == 64
    assert data.get("truncated") is False
    assert len(data.get("values", [])) == 64


@pytest.mark.parametrize(
    ("overrides", "code"),
    [
        ({"values": [1.0] * 15}, "invalid_arguments"),
        ({"cutoff_hz": 50.0}, "invalid_arguments"),
        ({"order": 3}, "invalid_arguments"),
        ({"filter_type": "bandpass"}, "invalid_arguments"),
    ],
)
def test_filter_rejects_out_of_contract_arguments(
    overrides: dict[str, object],
    code: str,
) -> None:
    args: dict[str, object] = {
        "values": [float(index) for index in range(32)],
        "cutoff_hz": 5.0,
        "sampling_rate_hz": 100.0,
        "order": 4,
        "filter_type": "lowpass",
    }
    args.update(overrides)
    result = _local_tools()["apply_butterworth_filter"].invoke(args)
    _assert_error(result, code)


def test_filter_output_is_bounded_without_skipping_computation() -> None:
    result = _local_tools()["apply_butterworth_filter"].invoke({
        "values": [math.sin(index / 10) for index in range(300)],
        "cutoff_hz": 5.0,
        "sampling_rate_hz": 100.0,
    })
    assert isinstance(result, dict)
    data = result.get("data")
    assert isinstance(data, dict)
    assert data == {
        "values": data["values"],
        "total_count": 300,
        "returned_count": hub.MAX_TOOL_OUTPUT_VALUES,
        "truncated": True,
    }
    assert len(data["values"]) == hub.MAX_TOOL_OUTPUT_VALUES


def test_formula_runs_in_memory_and_returns_structured_data() -> None:
    result = _local_tools()["execute_custom_formula"].invoke({
        "columns": {"W1": [1.0, 2.0], "W2": [0.5, 1.0]},
        "formula_str": "W1 - W2 + k",
        "params": {"k": 1.0},
    })
    assert isinstance(result, dict)
    assert result.get("ok") is True
    data = result.get("data")
    assert isinstance(data, dict)
    assert data.get("values") == pytest.approx([1.5, 2.0])
    assert data.get("total_count") == 2
    assert data.get("truncated") is False


@pytest.mark.parametrize(
    "args",
    [
        {"columns": {}, "formula_str": "W1"},
        {
            "columns": {"W1": [1.0, 2.0], "W2": [1.0]},
            "formula_str": "W1 + W2",
        },
        {"columns": {"W1": [1.0]}, "formula_str": "__import__('os')"},
        {
            "columns": {"W1": [1.0]},
            "formula_str": "W1",
            "params": {"bad-param": 1.0},
        },
    ],
)
def test_formula_rejects_out_of_contract_arguments(
    args: dict[str, object],
) -> None:
    result = _local_tools()["execute_custom_formula"].invoke(args)
    _assert_error(result, "invalid_arguments")


def test_formula_output_is_bounded() -> None:
    result = _local_tools()["execute_custom_formula"].invoke({
        "columns": {"W1": [float(index) for index in range(300)]},
        "formula_str": "W1 * 2",
        "params": {},
    })
    assert isinstance(result, dict)
    data = result.get("data")
    assert isinstance(data, dict)
    assert data.get("total_count") == 300
    assert data.get("returned_count") == hub.MAX_TOOL_OUTPUT_VALUES
    assert data.get("truncated") is True


def test_safe_tools_do_not_need_file_io(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def reject_open(*args: object, **kwargs: object) -> object:
        raise AssertionError("safe default tools must not open files")

    monkeypatch.setattr(builtins, "open", reject_open)
    tools = _local_tools()
    filter_result = tools["apply_butterworth_filter"].invoke({
        "values": [math.sin(index / 10) for index in range(32)],
        "cutoff_hz": 5.0,
        "sampling_rate_hz": 100.0,
    })
    formula_result = tools["execute_custom_formula"].invoke({
        "columns": {"W1": [1.0, 2.0]},
        "formula_str": "W1 + 1",
        "params": {},
    })
    assert isinstance(filter_result, dict) and filter_result.get("ok") is True
    assert isinstance(formula_result, dict) and formula_result.get("ok") is True


def test_internal_failures_return_generic_structured_errors(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import dp_engine.analyzer
    import dp_engine.formula

    def fail(*args: object, **kwargs: object) -> object:
        raise RuntimeError(r"SECRET_TOKEN at C:\Users\private\data.csv")

    monkeypatch.setattr(dp_engine.analyzer, "apply_filter", fail)
    monkeypatch.setattr(dp_engine.formula, "calculate", fail)
    tools = _local_tools()
    filter_result = tools["apply_butterworth_filter"].invoke({
        "values": [float(index) for index in range(32)],
        "cutoff_hz": 5.0,
        "sampling_rate_hz": 100.0,
    })
    formula_result = tools["execute_custom_formula"].invoke({
        "columns": {"W1": [1.0, 2.0]},
        "formula_str": "W1 + 1",
        "params": {},
    })
    _assert_error(filter_result, "tool_exception")
    _assert_error(formula_result, "tool_exception")
    assert "SECRET_TOKEN" not in repr((filter_result, formula_result))
    assert "Users" not in repr((filter_result, formula_result))
