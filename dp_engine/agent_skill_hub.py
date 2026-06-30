"""Agent Skill Hub - 第三方生态接入模块

集成全球顶级开源 Agent 技能库，为 DataProcessor Pro 2.0 智能体系统扩充能力。

核心第三方技能：
- PythonREPLTool: Agent 自主编写并运行 Python 代码（沙箱执行）
- ArxivQueryRun: 学术文献搜索
- DuckDuckGoSearchRun: 联网搜索

本地技能：
- Wiki 读写
- 巴特沃斯滤波
- 自定义公式计算
"""

from typing import List, Optional
from langchain_core.tools import BaseTool

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
    except ImportError as e:
        _log(f"PythonREPLTool import failed: {e}")

    try:
        from langchain_community.tools.arxiv.tool import ArxivQueryRun
        tools["arxiv"] = ArxivQueryRun()
        tools["arxiv"].description = THIRD_PARTY_TOOL_DESCRIPTIONS["arxiv"]
        _log("Arxiv tool loaded")
    except ImportError as e:
        _log(f"ArxivQueryRun import failed: {e}")

    try:
        from langchain_community.tools.ddg_search.tool import DuckDuckGoSearchRun
        tools["ddg_search"] = DuckDuckGoSearchRun()
        tools["ddg_search"].description = THIRD_PARTY_TOOL_DESCRIPTIONS["ddg_search"]
        _log("DuckDuckGo search tool loaded")
    except ImportError as e:
        _log(f"DuckDuckGoSearchRun import failed: {e}")

    try:
        from langchain_community.tools.wikipedia.tool import WikipediaQueryRun
        tools["wikipedia"] = WikipediaQueryRun()
        tools["wikipedia"].description = THIRD_PARTY_TOOL_DESCRIPTIONS["wikipedia"]
        _log("Wikipedia tool loaded")
    except ImportError as e:
        _log(f"WikipediaQueryRun import failed: {e}")
    except Exception as e:
        _log(f"WikipediaQueryRun init failed: {e}")

    return tools


# ============ 本地技能导入 ============

def _get_local_tools() -> List[BaseTool]:
    """获取本地内置技能"""
    from dp_engine.wiki_system import WikiFileSystem
    from dp_engine.analyzer import apply_filter
    from dp_engine.formula import calculate
    from langchain_core.tools import tool

    @tool
    def apply_butterworth_filter(file_path: str, cutoff_hz: float, order: int = 4, filter_type: str = "lowpass") -> str:
        """应用巴特沃斯滤波器进行物理降噪，处理传感器时序数据"""
        try:
            import pandas as pd
            df = pd.read_csv(file_path)
            if 'data' not in df.columns:
                return "错误: CSV 文件缺少 'data' 列"
            sampling_rate = 1000.0
            filtered = apply_filter(df['data'].tolist(), filter_type, cutoff_hz, sampling_rate)
            df['filtered'] = filtered
            output_path = file_path.replace('.csv', '_filtered.csv')
            df.to_csv(output_path, index=False)
            return f"滤波已应用: {filter_type} 截止频率={cutoff_hz}Hz 阶数={order}。输出文件: {output_path}"
        except Exception as e:
            return f"滤波应用错误: {str(e)}"

    @tool
    def execute_custom_formula(file_path: str, formula_str: str, params: str = "{}") -> str:
        """执行自定义公式进行列计算，支持数学函数"""
        try:
            import pandas as pd
            import json
            df = pd.read_csv(file_path)
            params_dict = json.loads(params) if params else {}
            columns = {col: df[col].tolist() for col in df.columns if df[col].dtype in ['float64', 'int64']}
            result = calculate(formula_str, columns, params_dict)
            result_col = "formula_result"
            df[result_col] = result
            output_path = file_path.replace('.csv', '_formula.csv')
            df.to_csv(output_path, index=False)
            return f"公式已执行: {formula_str}。输出文件: {output_path}"
        except Exception as e:
            return f"公式执行错误: {str(e)}"

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
    """获取所有可用的 Agent 技能（第三方 + 本地），用于一键注入给 CrewAI"""
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


def get_tools_by_category(category: str) -> List[BaseTool]:
    """
    按类别获取技能

    Args:
        category: "all", "third_party", "local", "code", "search", "wiki"
    """
    if category == "all":
        return get_all_agent_tools()

    if category == "third_party":
        return [t for t in get_third_party_tools().values() if t is not None]

    if category == "local":
        return _get_local_tools()

    if category == "code":
        return [t for t in get_all_agent_tools() if t.name in ["PythonREPLTool", "apply_butterworth_filter", "execute_custom_formula"]]

    if category == "search":
        return [t for t in get_third_party_tools().values() if t is not None and any(x in t.name.lower() for x in ["arxiv", "ddg", "wikipedia", "wiki", "search"])]

    if category == "wiki":
        return [t for t in get_all_agent_tools() if "wiki" in t.name.lower()]

    return []


def get_tool_names() -> dict:
    """获取所有技能名称按类别分组"""
    all_tools = get_all_agent_tools()
    return {
        "all": [t.name for t in all_tools],
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