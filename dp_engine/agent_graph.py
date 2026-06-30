"""DataProcessor Pro 单引擎智能体 (LangGraph ReAct)

基于 LangGraph 的单主脑 ReAct 拓扑，将 Wiki 工具与数据处理算法绑定。
"""

import os
import json
from typing import TypedDict, Annotated, List
from langchain_core.messages import BaseMessage, HumanMessage, AIMessage
from langgraph.graph import StateGraph, END
from langchain_core.tools import tool
from langchain_openai import ChatOpenAI

from dp_engine.wiki_system import WikiFileSystem
from dp_engine.analyzer import apply_filter
from dp_engine.formula import calculate


# ============ 全局状态定义 ============

class AgentState(TypedDict):
    """Agent 全局状态"""
    messages: Annotated[List[BaseMessage], lambda x, y: x + y]
    current_csv_path: str
    execution_logs: List[str]


# ============ 工具箱配置 ============

@tool
def apply_butterworth_filter(file_path: str, cutoff_hz: float, order: int = 4, filter_type: str = "lowpass") -> str:
    """
    应用巴特沃斯滤波器进行物理降噪。

    Args:
        file_path: CSV 数据文件路径
        cutoff_hz: 截止频率 (Hz)
        order: 滤波器阶数，默认 4
        filter_type: 滤波器类型 ('lowpass', 'highpass', 'bandpass')

    Returns:
        执行状态的描述字符串
    """
    try:
        import pandas as pd
        df = pd.read_csv(file_path)
        if 'data' not in df.columns:
            return "Error: CSV 文件缺少 'data' 列"

        sampling_rate = 1000.0
        filtered = apply_filter(df['data'].tolist(), filter_type, cutoff_hz, sampling_rate)
        df['filtered'] = filtered

        output_path = file_path.replace('.csv', '_filtered.csv')
        df.to_csv(output_path, index=False)
        return f"Filter applied: {filter_type} cutoff={cutoff_hz}Hz order={order}. Output: {output_path}"
    except Exception as e:
        return f"Error applying filter: {str(e)}"


@tool
def execute_custom_formula(file_path: str, formula_str: str, params: dict = None) -> str:
    """
    执行自定义公式列计算。

    Args:
        file_path: CSV 数据文件路径
        formula_str: 公式字符串，例如 "([strain] * 2.5) + [offset]"
        params: 参数字典，例如 {"offset": 0.5}

    Returns:
        执行状态的描述字符串
    """
    try:
        import pandas as pd
        df = pd.read_csv(file_path)
        params = params or {}

        columns = {col: df[col].tolist() for col in df.columns if df[col].dtype in ['float64', 'int64']}
        result = calculate(formula_str, columns, params)

        result_col = f"formula_result"
        df[result_col] = result

        output_path = file_path.replace('.csv', '_formula.csv')
        df.to_csv(output_path, index=False)
        return f"Formula executed: {formula_str}. Output: {output_path}"
    except Exception as e:
        return f"Error executing formula: {str(e)}"


@tool
def read_wiki_page(page_name: str) -> str:
    """
    读取本地指定的知识库文档。

    Args:
        page_name: 页面名称

    Returns:
        页面内容的 Markdown 字符串
    """
    wiki = WikiFileSystem()
    return wiki.read_wiki_page(page_name)


@tool
def write_wiki_page(page_name: str, content: str) -> str:
    """
    新建或更新知识库文档。

    Args:
        page_name: 页面名称
        content: Markdown 格式的页面内容

    Returns:
        操作结果的描述字符串
    """
    wiki = WikiFileSystem()
    return wiki.write_wiki_page(page_name, content, author="DeepSeek-Agent")


@tool
def read_wiki_map() -> str:
    """
    读取知识库索引表。

    Returns:
        wiki_map.json 的 JSON 字符串内容
    """
    wiki = WikiFileSystem()
    return wiki.read_wiki_map()


@tool
def update_wiki_map(page_name: str, path: str, tags: List[str], summary: str) -> str:
    """
    更新知识库索引表。

    Args:
        page_name: 页面名称
        path: 页面文件路径
        tags: 标签列表
        summary: 页面摘要

    Returns:
        操作结果的描述字符串
    """
    wiki = WikiFileSystem()
    return wiki.update_wiki_map(page_name, path, tags, summary)


@tool
def list_wiki_pages() -> str:
    """
    列出所有知识库页面。

    Returns:
        页面列表的格式化字符串
    """
    wiki = WikiFileSystem()
    return wiki.list_pages()


@tool
def search_wiki_pages(keyword: str) -> str:
    """
    在知识库中搜索关键词。

    Args:
        keyword: 搜索关键词

    Returns:
        匹配的页面列表
    """
    wiki = WikiFileSystem()
    return wiki.search_pages(keyword)


# 工具列表
TOOLS = [
    apply_butterworth_filter,
    execute_custom_formula,
    read_wiki_page,
    write_wiki_page,
    read_wiki_map,
    update_wiki_map,
    list_wiki_pages,
    search_wiki_pages,
]


# ============ LangGraph 拓扑 ============

def should_continue(state: AgentState) -> str:
    """判断是否继续工具调用循环"""
    last_message = state["messages"][-1]
    if hasattr(last_message, "tool_calls") and last_message.tool_calls:
        return "call_tools"
    return "end"


def call_deepseek_node(state: AgentState, llm=None) -> AgentState:
    """调用 DeepSeek LLM 主脑"""
    if llm is None:
        api_key = os.environ.get("DEEPSEEK_API_KEY", "")
        api_base = os.environ.get("DEEPSEEK_API_BASE", "https://api.deepseek.com")

        if not api_key:
            from langchain_community.chat_models import FakeListChatModel
            llm = FakeListChatModel(
                responses=["我已经收到您的请求。当前处于离线模式，无法连接 DeepSeek API。请检查 API 配置。"]
            )
        else:
            from langchain_openai import ChatOpenAI
            llm = ChatOpenAI(
                model="deepseek-chat",
                api_key=api_key,
                base_url=api_base,
                streaming=True,
            )

    from langgraph.prebuilt import ToolNode
    tool_node = ToolNode(TOOLS)

    response = llm.invoke(state["messages"])
    return {
        "messages": [response],
        "current_csv_path": state.get("current_csv_path", ""),
        "execution_logs": state.get("execution_logs", []),
    }


def create_agent_graph():
    """创建 Agent 工作流图"""
    workflow = StateGraph(AgentState)

    workflow.add_node("llm_brain", call_deepseek_node)
    workflow.add_node("action_tools", lambda state: state)

    workflow.set_entry_point("llm_brain")

    workflow.add_conditional_edges(
        "llm_brain",
        should_continue,
        {
            "call_tools": "action_tools",
            "end": END
        }
    )

    workflow.add_edge("action_tools", "llm_brain")

    return workflow.compile()


def run_agent(user_input: str, csv_path: str = None):
    """
    运行单引擎 Agent

    Args:
        user_input: 用户输入的指令
        csv_path: 当前处理的 CSV 文件路径

    Returns:
        Agent 的最终响应
    """
    graph = create_agent_graph()

    initial_state = AgentState(
        messages=[HumanMessage(content=user_input)],
        current_csv_path=csv_path or "",
        execution_logs=[]
    )

    result = graph.invoke(initial_state)
    return result["messages"][-1].content


if __name__ == "__main__":
    print("=== DataProcessor Pro 单引擎智能体 ===")
    print("请配置 DEEPSEEK_API_KEY 环境变量以启用在线模式")
    print()

    test_input = "请读取 wiki_map.json 并列出所有页面"
    print(f"测试输入: {test_input}")
    print()

    result = run_agent(test_input)
    print("Agent 响应:")
    print(result)