"""Agent Skill Hub — LangChain 工具工厂 (运行时工具提供者).

⚠️ 职责边界:
  本模块是 LangChain BaseTool 的运行时工厂，为 AI Agent 提供可调用的工具函数。
  它 **不是** 技能注册表 (SSOT)。技能元数据的唯一真相源是 SkillRegistry
  (dp_engine.skills.registry)。本模块不管理技能安装、版本或持久化。

核心第三方技能（仅显式获取，绝不进入默认白名单）:
- PythonREPLTool: Agent 自主编写并运行 Python 代码（非沙箱，危险）
- ArxivQueryRun: 学术文献搜索
- DuckDuckGoSearchRun: 联网搜索

本地技能:
- Wiki 读写
- 巴特沃斯滤波
- 自定义公式计算

默认安全使用方式:
  from dp_engine.agent_skill_hub import get_default_agent_tools
  tools = get_default_agent_tools()  # → 精确白名单，只含纯内存工具

与 SkillRegistry 的关系:
  - SkillRegistry:    已安装技能的 **元数据** SSOT (JSON 持久化)
  - AgentSkillHub:    AI Agent 的 **运行时工具** 工厂 (LangChain BaseTool)
  - 两者服务于不同层，互不冲突。
  - 本模块不承诺 Registry 动态技能、SkillRuntime 结果或 Artifact 注入 Agent。
"""

from dataclasses import dataclass
from importlib import import_module
import math
import re
from types import MappingProxyType
from typing import List, Mapping, Optional, Sequence

import numpy as np
from langchain_core.tools import BaseTool


MAX_TOOL_INPUT_POINTS = 2_048
MAX_TOOL_OUTPUT_VALUES = 256
MAX_FORMULA_COLUMNS = 16
MAX_FORMULA_PARAMS = 32
MAX_FORMULA_LENGTH = 512
_SAFE_FORMULA_PATTERN = re.compile(r"^[A-Za-z0-9_+\-*/().,\s]+$")


@dataclass(frozen=True)
class ToolSafetyProfile:
    """固定内置工具的安全属性；default_allowed 是精确白名单依据。"""

    name: str
    category: str
    source: str
    third_party: bool
    network_access: bool
    file_read: bool
    file_write: bool
    code_execution: bool
    default_allowed: bool


TOOL_SAFETY_CATALOG: Mapping[str, ToolSafetyProfile] = MappingProxyType({
    "python_repl": ToolSafetyProfile(
        "python_repl", "code", "langchain_experimental", True,
        True, True, True, True, False,
    ),
    "arxiv": ToolSafetyProfile(
        "arxiv", "network_search", "langchain_community", True,
        True, False, False, False, False,
    ),
    "ddg_search": ToolSafetyProfile(
        "ddg_search", "network_search", "langchain_community", True,
        True, False, False, False, False,
    ),
    "wikipedia": ToolSafetyProfile(
        "wikipedia", "network_search", "langchain_community", True,
        True, False, False, False, False,
    ),
    "apply_butterworth_filter": ToolSafetyProfile(
        "apply_butterworth_filter", "local_compute", "dp_engine.analyzer", False,
        False, False, False, False, True,
    ),
    "execute_custom_formula": ToolSafetyProfile(
        "execute_custom_formula", "local_compute", "dp_engine.formula", False,
        False, False, False, False, True,
    ),
    "read_wiki_page": ToolSafetyProfile(
        "read_wiki_page", "local_wiki", "dp_engine.wiki_system", False,
        False, True, True, False, False,
    ),
    "write_wiki_page": ToolSafetyProfile(
        "write_wiki_page", "local_wiki", "dp_engine.wiki_system", False,
        False, True, True, False, False,
    ),
    "list_wiki_pages": ToolSafetyProfile(
        "list_wiki_pages", "local_wiki", "dp_engine.wiki_system", False,
        False, True, True, False, False,
    ),
    "search_wiki_pages": ToolSafetyProfile(
        "search_wiki_pages", "local_wiki", "dp_engine.wiki_system", False,
        False, True, True, False, False,
    ),
    "semantic_search_wiki": ToolSafetyProfile(
        "semantic_search_wiki", "local_semantic_search", "dp_engine.wiki_system", False,
        True, True, True, False, False,
    ),
})

DEFAULT_AGENT_TOOL_NAMES: tuple[str, ...] = (
    "apply_butterworth_filter",
    "execute_custom_formula",
)


class _SafeToolInputError(ValueError):
    """可安全返回模型的输入校验错误。"""


def _error_result(code: str, message: str) -> dict[str, object]:
    return {"ok": False, "error": {"code": code, "message": message}}


def _validate_numeric_sequence(
    values: Sequence[float],
    *,
    field_name: str,
    minimum: int = 1,
) -> list[float]:
    if isinstance(values, (str, bytes)):
        raise _SafeToolInputError(f"{field_name} 必须是数值数组")
    if not minimum <= len(values) <= MAX_TOOL_INPUT_POINTS:
        raise _SafeToolInputError(
            f"{field_name} 长度必须在 {minimum}..{MAX_TOOL_INPUT_POINTS} 之间"
        )

    normalized: list[float] = []
    for value in values:
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise _SafeToolInputError(f"{field_name} 只能包含有限实数")
        number = float(value)
        if not math.isfinite(number):
            raise _SafeToolInputError(f"{field_name} 只能包含有限实数")
        normalized.append(number)
    return normalized


def _bounded_numeric_payload(
    values: Sequence[float | None],
) -> dict[str, object]:
    normalized: list[float | None] = []
    for value in values[:MAX_TOOL_OUTPUT_VALUES]:
        if value is None:
            normalized.append(None)
            continue
        number = float(value)
        normalized.append(number if math.isfinite(number) else None)
    total = len(values)
    return {
        "values": normalized,
        "total_count": total,
        "returned_count": len(normalized),
        "truncated": total > len(normalized),
    }


def _validate_identifier(name: str, *, field_name: str) -> None:
    if not name or len(name) > 64 or not name.isidentifier():
        raise _SafeToolInputError(f"{field_name} 必须是长度不超过 64 的标识符")

# ============ 第三方开源技能导入 ============

# 第三方技能中文描述映射
THIRD_PARTY_TOOL_DESCRIPTIONS = {
    "Python_REPL": "Python 沙箱执行器 - Agent 自主编写并运行 Python 代码，适用于动态计算和数据处理",
    "arxiv": "学术文献搜索 - 搜索 arXiv 论文数据库，获取最新学术研究论文",
    "ddg_search": "联网搜索 - 使用 DuckDuckGo 进行实时网络信息搜索",
    "wikipedia": "维基百科 - 搜索和获取 Wikipedia 文章内容，进行百科知识查询",
}

def _import_third_party_tools(silent: bool = False) -> dict:
    """延迟导入第三方工具，避免全量导入导致包缺失时报错"""
    tools = {}

    def _log(msg):
        if not silent:
            print(msg)

    try:
        from langchain_experimental.tools.python.tool import PythonREPLTool
        tools["python_repl"] = PythonREPLTool()
        tools["python_repl"].description = THIRD_PARTY_TOOL_DESCRIPTIONS["Python_REPL"]
        _log("Python_REPL tool loaded")
    except Exception as e:
        _log(f"PythonREPLTool unavailable: {type(e).__name__}")

    try:
        from langchain_community.tools.arxiv.tool import ArxivQueryRun
        tools["arxiv"] = ArxivQueryRun()
        tools["arxiv"].description = THIRD_PARTY_TOOL_DESCRIPTIONS["arxiv"]
        _log("Arxiv tool loaded")
    except Exception as e:
        _log(f"ArxivQueryRun unavailable: {type(e).__name__}")

    try:
        from langchain_community.tools.ddg_search.tool import DuckDuckGoSearchRun
        tools["ddg_search"] = DuckDuckGoSearchRun()
        tools["ddg_search"].description = THIRD_PARTY_TOOL_DESCRIPTIONS["ddg_search"]
        _log("DuckDuckGo search tool loaded")
    except Exception as e:
        _log(f"DuckDuckGoSearchRun unavailable: {type(e).__name__}")

    try:
        from langchain_community.tools.wikipedia.tool import WikipediaQueryRun
        from langchain_community.utilities.wikipedia import WikipediaAPIWrapper
        wikipedia_client = import_module("wikipedia")
        tools["wikipedia"] = WikipediaQueryRun(
            api_wrapper=WikipediaAPIWrapper(wiki_client=wikipedia_client),
        )
        tools["wikipedia"].description = THIRD_PARTY_TOOL_DESCRIPTIONS["wikipedia"]
        _log("Wikipedia tool loaded")
    except Exception as e:
        _log(f"WikipediaQueryRun unavailable: {type(e).__name__}")

    return tools


# ============ 本地技能导入 ============

def _get_local_tools() -> List[BaseTool]:
    """获取本地内置技能"""
    from dp_engine.wiki_system import WikiFileSystem
    from dp_engine.analyzer import apply_filter
    from dp_engine.formula import calculate
    from langchain_core.tools import tool

    @tool
    def apply_butterworth_filter(
        values: list[float],
        cutoff_hz: float,
        sampling_rate_hz: float,
        order: int = 4,
        filter_type: str = "lowpass",
    ) -> dict[str, object]:
        """对内存数值数组执行四阶巴特沃斯低通或高通滤波；不读写文件。"""
        try:
            clean_values = _validate_numeric_sequence(
                values, field_name="values", minimum=16,
            )
            if isinstance(cutoff_hz, bool) or not math.isfinite(float(cutoff_hz)):
                raise _SafeToolInputError("cutoff_hz 必须是有限实数")
            if isinstance(sampling_rate_hz, bool) or not math.isfinite(float(sampling_rate_hz)):
                raise _SafeToolInputError("sampling_rate_hz 必须是有限实数")
            if sampling_rate_hz <= 0:
                raise _SafeToolInputError("sampling_rate_hz 必须大于 0")
            if cutoff_hz <= 0 or cutoff_hz >= sampling_rate_hz / 2:
                raise _SafeToolInputError("cutoff_hz 必须位于 (0, Nyquist) 区间")
            if order != 4:
                raise _SafeToolInputError("当前底层滤波器只支持 order=4")
            if filter_type not in {"lowpass", "highpass"}:
                raise _SafeToolInputError("filter_type 仅支持 lowpass 或 highpass")

            filtered = apply_filter(
                clean_values,
                filter_type,
                float(cutoff_hz),
                float(sampling_rate_hz),
            )
            return {"ok": True, "data": _bounded_numeric_payload(filtered)}
        except _SafeToolInputError as e:
            return _error_result("invalid_arguments", str(e))
        except Exception:
            return _error_result("tool_exception", "滤波计算失败")

    @tool
    def execute_custom_formula(
        columns: dict[str, list[float]],
        formula_str: str,
        params: dict[str, float] | None = None,
    ) -> dict[str, object]:
        """使用受限公式引擎计算内存数值列；不接受路径且不读写文件。"""
        try:
            if not isinstance(columns, dict) or not columns:
                raise _SafeToolInputError("columns 必须是非空数值列对象")
            if len(columns) > MAX_FORMULA_COLUMNS:
                raise _SafeToolInputError(
                    f"columns 最多包含 {MAX_FORMULA_COLUMNS} 列"
                )
            if not isinstance(formula_str, str) or not formula_str.strip():
                raise _SafeToolInputError("formula_str 不能为空")
            if len(formula_str) > MAX_FORMULA_LENGTH:
                raise _SafeToolInputError(
                    f"formula_str 最长 {MAX_FORMULA_LENGTH} 字符"
                )
            if _SAFE_FORMULA_PATTERN.fullmatch(formula_str) is None:
                raise _SafeToolInputError("formula_str 包含不允许的字符")

            clean_columns: dict[str, list[float] | np.ndarray] = {}
            expected_length: int | None = None
            for name, values in columns.items():
                _validate_identifier(name, field_name="column name")
                clean = _validate_numeric_sequence(values, field_name=f"columns.{name}")
                if expected_length is None:
                    expected_length = len(clean)
                elif len(clean) != expected_length:
                    raise _SafeToolInputError("所有数值列长度必须一致")
                clean_columns[name] = clean

            raw_params = params or {}
            if not isinstance(raw_params, dict):
                raise _SafeToolInputError("params 必须是 JSON object")
            if len(raw_params) > MAX_FORMULA_PARAMS:
                raise _SafeToolInputError(
                    f"params 最多包含 {MAX_FORMULA_PARAMS} 项"
                )
            clean_params: dict[str, float | dict] = {}
            for name, value in raw_params.items():
                _validate_identifier(name, field_name="param name")
                if isinstance(value, bool) or not isinstance(value, (int, float)):
                    raise _SafeToolInputError("params 只能包含有限实数")
                number = float(value)
                if not math.isfinite(number):
                    raise _SafeToolInputError("params 只能包含有限实数")
                clean_params[name] = number

            result = calculate(formula_str, clean_columns, clean_params)
            if not result or all(value is None for value in result):
                return _error_result("calculation_failed", "公式未产生有效数值")
            return {"ok": True, "data": _bounded_numeric_payload(result)}
        except _SafeToolInputError as e:
            return _error_result("invalid_arguments", str(e))
        except Exception:
            return _error_result("tool_exception", "公式计算失败")

    @tool
    def read_wiki_page(page_name: str) -> str:
        """读取本地知识库指定页面内容"""
        wiki = WikiFileSystem()
        return wiki.read_wiki_page(page_name)

    @tool
    def write_wiki_page(page_name: str, content: str) -> str:
        """新建或更新本地知识库页面"""
        wiki = WikiFileSystem()
        return wiki.write_wiki_page(page_name, content, author="Agent")

    @tool
    def list_wiki_pages() -> str:
        """列出本地知识库中所有页面"""
        wiki = WikiFileSystem()
        return wiki.list_pages()

    @tool
    def search_wiki_pages(keyword: str) -> str:
        """在本地知识库中搜索关键词"""
        wiki = WikiFileSystem()
        return wiki.search_pages(keyword)

    @tool
    def semantic_search_wiki(query: str, top_k: int = 3) -> str:
        """语义向量搜索知识库 — 按语义相似度查找最相关的历史案例页面"""
        wiki = WikiFileSystem()
        return wiki.semantic_search(query, top_k)

    return [
        apply_butterworth_filter,
        execute_custom_formula,
        read_wiki_page,
        write_wiki_page,
        list_wiki_pages,
        search_wiki_pages,
        semantic_search_wiki,
    ]


# ============ 统一获取接口 ============

_third_party_cache: Optional[dict] = None


def get_third_party_tools() -> dict:
    """获取已缓存的第三方工具"""
    global _third_party_cache
    if _third_party_cache is None:
        _third_party_cache = _import_third_party_tools(silent=True)
    return _third_party_cache


def get_all_agent_tools() -> List[BaseTool]:
    """显式获取全部工具；包含网络、写入和代码执行工具，禁止默认注入。"""
    all_tools = []

    # 添加第三方工具
    third_party = get_third_party_tools()
    for tool_obj in third_party.values():
        if tool_obj is not None:
            all_tools.append(tool_obj)

    # 添加本地工具
    local_tools = _get_local_tools()
    all_tools.extend(local_tools)

    return all_tools


def get_default_agent_tools() -> List[BaseTool]:
    """按精确名称返回已通过安全目录审核的纯内存默认工具。"""
    local_by_name = {tool_obj.name: tool_obj for tool_obj in _get_local_tools()}
    missing = [name for name in DEFAULT_AGENT_TOOL_NAMES if name not in local_by_name]
    if missing:
        raise RuntimeError(f"默认 Agent 工具缺失: {', '.join(missing)}")
    return [local_by_name[name] for name in DEFAULT_AGENT_TOOL_NAMES]


def get_tool_safety_catalog() -> tuple[ToolSafetyProfile, ...]:
    """返回稳定、只读的内置工具安全目录快照。"""
    return tuple(TOOL_SAFETY_CATALOG.values())


def get_tools_by_category(category: str) -> List[BaseTool]:
    """
    按类别获取技能

    Args:
        category: "default", "all", "third_party", "local", "code", "search", "wiki"
    """
    if category == "default":
        return get_default_agent_tools()

    if category == "all":
        return get_all_agent_tools()

    if category == "third_party":
        return [t for t in get_third_party_tools().values() if t is not None]

    if category == "local":
        return _get_local_tools()

    if category == "code":
        tools: List[BaseTool] = []
        python_repl = get_third_party_tools().get("python_repl")
        if python_repl is not None:
            tools.append(python_repl)
        tools.extend(get_default_agent_tools())
        return tools

    if category == "search":
        third_party = get_third_party_tools()
        return [
            third_party[name]
            for name in ("arxiv", "ddg_search", "wikipedia")
            if third_party.get(name) is not None
        ]

    if category == "wiki":
        return [t for t in get_all_agent_tools() if "wiki" in t.name.lower()]

    return []


def get_tool_names() -> dict:
    """获取所有技能名称按类别分组"""
    all_tools = get_all_agent_tools()
    return {
        "all": [t.name for t in all_tools],
        "default": list(DEFAULT_AGENT_TOOL_NAMES),
        "third_party": [t.name for t in get_third_party_tools().values() if t is not None],
        "local": [t.name for t in _get_local_tools()],
    }


# ============ Debug Entry ============

if __name__ == "__main__":
    print("=== Agent Skill Hub Debug ===")

    print("Loading third-party tools...")
    third_party = get_third_party_tools()

    print("Loading local tools...")
    local_tools = _get_local_tools()
    print(f"Local tools: {[t.name for t in local_tools]}")

    print("All available tools:")
    names = get_tool_names()
    print(f"  Third-party: {names['third_party']}")
    print(f"  Local: {names['local']}")
    print(f"  Total: {len(names['all'])} tools")
