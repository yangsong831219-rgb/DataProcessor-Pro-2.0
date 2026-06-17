"""DataProcessor Pro 多智能体审查系统 (CrewAI 架构)

基于 LangGraph 实现的三角色 Agent 团队：
- Agent 1: 数据科学家 (Data Scientist)
- Agent 2: 独立审查员 (Data Auditor)
- Agent 3: 首席传感专家 (Chief Scientist)

团队协作模式：
用户指令 → 数据科学家(分析) → 审查员(审查) → [循环:有问题打回重算]
                                         ↓ (通过)
                                   首席专家(诊断+报告) → 最终报告
"""

import os
from typing import TypedDict, Annotated, List, Literal, Optional, Callable
from langchain_core.messages import BaseMessage, HumanMessage, AIMessage, SystemMessage
from langchain_core.tools import tool
from langchain_core.language_models import BaseChatModel
from langgraph.graph import StateGraph, END
from langgraph.prebuilt import ToolNode
from langchain_openai import ChatOpenAI

from py.wiki_system import WikiFileSystem
from py.analyzer import apply_filter
from py.formula import calculate


# ============ JSON 输出 Schema（首席专家报告） ============

CHIEF_REPORT_JSON_SCHEMA = {
    "type": "object",
    "properties": {
        "diagnosis_summary": {
            "type": "object",
            "properties": {
                "data_type": {"type": "string", "enum": ["fiber_optic", "general"]},
                "template_name": {"type": "string"},
                "data_quality": {"type": "string", "enum": ["good", "fair", "poor"]},
                "anomaly_count": {"type": "integer"},
                "overall_assessment": {"type": "string"},
            }
        },
        "data_quality_assessment": {
            "type": "object",
            "properties": {
                "completeness": {"type": "string"},
                "consistency": {"type": "string"},
                "anomaly_patterns": {"type": "array", "items": {"type": "string"}},
                "recommendations": {"type": "array", "items": {"type": "string"}}
            }
        },
        "sensor_analysis": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "sensor_id": {"type": "string"},
                    "status": {"type": "string", "enum": ["normal", "warning", "critical"]},
                    "statistics": {
                        "type": "object",
                        "properties": {
                            "mean": {"type": "number"},
                            "std": {"type": "number"},
                            "min": {"type": "number"},
                            "max": {"type": "number"}
                        }
                    },
                    "findings": {"type": "string"},
                    "suggestions": {"type": "string"}
                }
            }
        },
        "physical_diagnosis": {
            "type": "object",
            "properties": {
                "phenomenon": {"type": "string"},
                "possible_causes": {"type": "array", "items": {"type": "string"}},
                "severity": {"type": "string", "enum": ["low", "medium", "high"]},
                "recommended_actions": {"type": "array", "items": {"type": "string"}}
            }
        }
    }
}

_JSON_SCHEMA_STR = __import__('json').dumps(CHIEF_REPORT_JSON_SCHEMA, ensure_ascii=False, indent=2)


# ============ 数据上下文构建器 ============

def build_context_block(data_context: dict | None = None) -> str:
    """将数据上下文 dict 序列化为自然语言上下文块，注入 Agent 系统提示词。

    Args:
        data_context: 来自 ui/ai_diagnosis.py _build_data_context() 的输出

    Returns:
        格式化的上下文文本（含数据类型防火墙、清洗统计、模板信息）
    """
    if not data_context:
        return ""

    data_type = data_context.get("data_type", "unknown")
    is_fiber = data_type == "fiber_optic"
    template_name = data_context.get("template_name", "(无模板)")
    cleaning = data_context.get("cleaning_summary", "(未清洗)")
    rows = data_context.get("data_rows", 0)
    cols = data_context.get("data_columns", 0)

    type_label = "光纤光栅传感器数据（基于波长差 W1~W8 进行物理量转换）" if is_fiber else "通用数据（外部已算好，直接读取标注列）"

    lines = [
        "=== 当前数据上下文（自动注入） ===",
        f"模板: {template_name}",
        f"数据类型: {type_label}",
        f"数据规模: {rows} 行 × {cols} 列",
    ]

    if is_fiber:
        lines.extend([
            "数据类型防火墙规则:",
            "  - 基于波长差计算物理量，关注 FBG 波长漂移趋势",
            "  - 判断滤波截止频率是否合理（避免波形畸变）",
            "  - 检查基线回零是否准确",
        ])
    else:
        lines.extend([
            "数据类型防火墙规则:",
            "  - 不进行波长差到物理量的转换计算",
            "  - 从暗号标注列（annotated_columns）直接读取物理量含义",
            "  - 关注数据完整性、一致性、异常模式",
        ])

    lines.append(f"清洗概况: {cleaning}")

    stats = data_context.get("numeric_stats", [])
    if stats:
        lines.append("数值列统计:")
        for s in stats:
            lines.append(
                f"  {s['col']}: 有效={s['count']}, 缺失={s['missing']}, "
                f"均值={s['mean']}, 标准差={s['std']}, 范围=[{s['min']}, {s['max']}]"
            )

    sensors = data_context.get("sensor_results_summary", [])
    if sensors:
        lines.append("传感器结果:")
        for s in sensors:
            lines.append(
                f"  {s['id']}: 有效={s['valid_count']}/{s['total_count']}, "
                f"均值={s['mean']}, 范围=[{s['min']}, {s['max']}]"
            )

    annotated = data_context.get("annotated_columns", [])
    if annotated:
        lines.append(f"暗号标注列: {annotated}")

    return '\n'.join(lines)


# ============ 角色系统提示词（含上下文注入点） ============

DATA_SCIENTIST_PROMPT_TPL = """你是一名资深 Python 数据科学家，专门从事传感器数据分析。

{data_context}

你的职责：
1. 读取 CSV 数据文件，进行降噪和特征提取
2. 应用巴特沃斯滤波器进行物理降噪
3. 执行自定义公式计算
4. 输出清晰的数据处理报告和技术备忘录

你拥有以下工具：
- apply_butterworth_filter: 巴特沃斯滤波降噪
- execute_custom_formula: 自定义公式列计算
- read_wiki_page: 读取知识库页面
- write_wiki_page: 写入知识库页面

工作流程：
1. 接收数据文件路径和分析需求
2. 进行数据清洗和滤波处理
3. 提取波形特征
4. 生成技术备忘录（包含处理步骤和清洗前后特征比对）

你的输出应该是专业的数据分析报告。

注意：请根据上方"数据类型"选择正确的处理路径：
- 光纤数据 → 基于波长差计算，关注 FBG 漂移
- 通用数据 → 直接从标注列读取，不做波长转换
"""


DATA_AUDITOR_PROMPT_TPL = """你是一名严格的实验数据质量控制官（QC），专门审查数据分析结果的合理性。

{data_context}

你的职责：
1. 审查数据科学家处理后的数据是否合理
2. 检查滤波是否过度导致真实信号失真
3. 检查基线回零是否判断准确
4. 检查异常值处理是否得当

你只有数据和执行日志的只读权限，不能直接修改数据。

审查标准：
- 滤波截止频率设置是否合理（不能过低导致波形畸变）
- 异常值判断是否符合物理规律
- 数据特征提取是否完整
- 计算公式是否正确

根据数据类型采用不同的审查重点：
- 光纤数据：重点审查波长漂移趋势、应变/温度耦合、滤波参数
- 通用数据：重点审查标注列一致性、缺失值处理、异常分布

=== 输出格式（严格 JSON） ===
你必须仅输出以下格式的 JSON 对象，不要加任何 markdown 包裹或额外文字：

{audit_schema}

字段说明：
- verdict: "pass" 表示审查通过，"reject" 表示驳回需修正
- reasons: 审查理由列表（至少 1 条）
- required_fixes: 若驳回，给出数据科学家必须修正的具体事项（每条≤50字）；若通过则为空数组

注意：只输出 JSON 对象，一行都不能多。"""


CHIEF_SCIENTIST_PROMPT_TPL = """你是光纤光栅传感器研发总负责人，拥有深厚的材料力学背景。

{data_context}

你的职责：
1. 结合本地知识库，将数字转化为物理诊断结论
2. 分析材料力学行为（如 NOA 81 胶水与 PI 光纤的界面滑移）
3. 撰写最终诊断报告
4. 更新知识库

你拥有以下工具：
- read_wiki_page: 读取本地知识库（如封装工艺规范）
- write_wiki_page: 将新发现写入知识库
- list_wiki_pages: 列出所有知识库页面
- search_wiki_pages: 搜索知识库

工作流程：
1. 读取数据科学家的分析结果和审查员的审查意见
2. 读取相关知识库内容（如 NOA81 封装工艺）
3. 根据数据类型选择诊断路径：
   - 光纤数据 → 分析波长漂移、应变/温度耦合、界面滑移
   - 通用数据 → 分析数据完整性、趋势变化、异常成因
4. 生成最终诊断报告（严格 JSON 格式）
5. 将新发现更新到知识库

=== 输出要求 ===
你必须严格以 JSON 格式输出最终诊断报告，遵循以下 schema（输出纯 JSON，不要 markdown 包裹，不要多余文字）：

{json_schema}

注意：全部用中文输出。"""


# ============ 工具定义 ============

@tool
def apply_butterworth_filter(file_path: str, cutoff_hz: float, order: int = 4, filter_type: str = "lowpass") -> str:
    """应用巴特沃斯滤波器进行物理降噪"""
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
    """执行自定义公式列计算"""
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
    """读取本地知识库文档"""
    wiki = WikiFileSystem()
    return wiki.read_wiki_page(page_name)


@tool
def write_wiki_page(page_name: str, content: str) -> str:
    """新建或更新知识库文档"""
    wiki = WikiFileSystem()
    return wiki.write_wiki_page(page_name, content, author="Chief-Scientist")


@tool
def list_wiki_pages() -> str:
    """列出所有知识库页面"""
    wiki = WikiFileSystem()
    return wiki.list_pages()


@tool
def search_wiki_pages(keyword: str) -> str:
    """在知识库中搜索关键词"""
    wiki = WikiFileSystem()
    return wiki.search_pages(keyword)


# 数据科学家工具（可读写数据和处理）
DATA_SCIENTIST_TOOLS = [
    apply_butterworth_filter,
    execute_custom_formula,
    read_wiki_page,
    write_wiki_page,
]

# 审查员工具（只读）
AUDITOR_TOOLS = [
    read_wiki_page,
    list_wiki_pages,
    search_wiki_pages,
]

# 首席专家工具（读写+诊断）
CHIEF_SCIENTIST_TOOLS = [
    read_wiki_page,
    write_wiki_page,
    list_wiki_pages,
    search_wiki_pages,
]


# ============ Audit 裁决 Schema ============

class AuditVerdict(TypedDict):
    """结构化审查裁决 — 替代字符串匹配 "通过/驳回"。

    字段:
        verdict: "pass" | "reject"
        reasons: 具体理由列表
        required_fixes: 要求数据科学家修正的具体事项（驳回时必填）
    """
    verdict: Literal["pass", "reject"]
    reasons: list[str]
    required_fixes: list[str]


AUDIT_JSON_SCHEMA = {
    "type": "object",
    "properties": {
        "verdict": {"type": "string", "enum": ["pass", "reject"]},
        "reasons": {"type": "array", "items": {"type": "string"}},
        "required_fixes": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["verdict", "reasons", "required_fixes"],
}
_AUDIT_SCHEMA_STR = "{\n  \"verdict\": \"pass\",\n  \"reasons\": [\"理由1\", \"理由2\"],\n  \"required_fixes\": []\n}"


# ============ 状态定义 ============

class MultiAgentState(TypedDict):
    """多智能体系统全局状态"""
    messages: Annotated[List[BaseMessage], lambda x, y: x + y]
    current_csv_path: str
    execution_logs: List[str]
    audit_result: Optional[dict]  # AuditVerdict 结构化 dict（Phase 3: 不再用字符串）
    data_scientist_report: Optional[str]  # 数据科学家报告
    chief_scientist_report: Optional[str]  # 首席专家报告
    rejection_count: int  # 驳回次数
    task_status: str  # pending/data_processing/auditing/diagnosis/failed/complete


# ============ LLM 工厂 ============

def create_llm(api_key: str, base_url: str, model_name: str) -> BaseChatModel:
    """使用显式参数创建 LLM 实例（与主应用 AI 诊断共用配置）"""
    if not api_key:
        raise ValueError("API Key 未配置，请先在 AI 模型配置中设置")
    return ChatOpenAI(
        model=model_name,
        api_key=api_key,
        base_url=base_url,
        streaming=True,
    )


# ============ 节点工厂（上下文感知版） ============

def _make_nodes(llm: BaseChatModel, data_context: dict | None = None):
    """创建节点函数，捕获共享的 LLM 实例。

    Args:
        llm: 大语言模型实例
        data_context: 来自 ui/ai_diagnosis.py _build_data_context() 的结构化上下文，
                      用于动态注入数据类型防火墙规则、清洗统计、模板信息。
    """
    ctx_block = build_context_block(data_context)

    # 预格式化各角色的系统提示词，注入上下文块
    ds_prompt = DATA_SCIENTIST_PROMPT_TPL.format(data_context=ctx_block)
    auditor_prompt = DATA_AUDITOR_PROMPT_TPL.format(
        data_context=ctx_block, audit_schema=_AUDIT_SCHEMA_STR,
    )
    chief_prompt = CHIEF_SCIENTIST_PROMPT_TPL.format(
        data_context=ctx_block,
        json_schema=_JSON_SCHEMA_STR,
    )

    def data_scientist_node(state: MultiAgentState) -> MultiAgentState:
        rej_count = state.get("rejection_count", 0)
        system_msg = SystemMessage(content=ds_prompt)
        messages = [system_msg] + state.get("messages", [])
        response = llm.invoke(messages)
        log_entry = (
            f"[DataScientist] input_msgs={len(state.get('messages',[]))} "
            f"ouput_len={len(str(response.content))} rejection_count={rej_count}"
        )
        new_logs = list(state.get("execution_logs", [])) + [log_entry]
        return {
            "messages": [response],
            "task_status": "data_processing",
            "data_scientist_report": str(response.content),
            "current_csv_path": state.get("current_csv_path", ""),
            "execution_logs": new_logs,
            "audit_result": state.get("audit_result"),
            "chief_scientist_report": state.get("chief_scientist_report"),
            "rejection_count": rej_count,
        }

    def auditor_node(state: MultiAgentState) -> MultiAgentState:
        import json, re

        audit_user_prompt = f"""请审查以下数据科学家的工作成果：

{state.get('data_scientist_report', '无报告')}

请：
1. 结合上方数据上下文判断处理路径是否正确（光纤 vs 通用）
2. 检查滤波截止频率是否合理
3. 检查数据处理是否符合物理规律
4. 检查是否有异常遗漏

请严格按照系统提示词中的 JSON 格式输出审查结果（verdict + reasons + required_fixes）。"""

        messages = [SystemMessage(content=auditor_prompt), HumanMessage(content=audit_user_prompt)]
        response = llm.invoke(messages)
        raw = str(response.content)

        # ── Phase 3: 结构化裁决，杜绝字符串匹配 ──
        verdict_dict: dict | None = None
        parse_error: str | None = None
        try:
            # 提取 JSON 块
            m = re.search(r'\{[\s\S]*\}', raw)
            json_str = m.group(0) if m else raw
            verdict_dict = json.loads(json_str)
            # 校验必填字段
            if verdict_dict.get("verdict") not in ("pass", "reject"):
                parse_error = f"verdict 字段非法: {verdict_dict.get('verdict')!r}"
                verdict_dict = None
            if not isinstance(verdict_dict.get("reasons"), list) or not verdict_dict["reasons"]:
                parse_error = "reasons 缺失或非数组"
                verdict_dict = None
        except (json.JSONDecodeError, AttributeError, KeyError) as e:
            parse_error = str(e)

        if verdict_dict is None:
            # 裁决 JSON 非法 → 抛错，绝不默认通过
            raise ValueError(
                f"Auditor 未产出合法 AuditVerdict JSON：{parse_error}\n"
                f"原始输出（前 500 字）: {raw[:500]}"
            )

        verdict = verdict_dict["verdict"]
        reasons = verdict_dict.get("reasons", [])
        required_fixes = verdict_dict.get("required_fixes", [])
        is_approved = (verdict == "pass")
        new_rejection_count = state.get("rejection_count", 0) + (0 if is_approved else 1)

        # ── 结构化执行日志 ──
        log_entry = (
            f"[Auditor] verdict={verdict} rejection_count={new_rejection_count} "
            f"reasons={reasons[:3]} "
            + (f"fixes={required_fixes[:3]}" if not is_approved else "")
        )
        new_logs = list(state.get("execution_logs", [])) + [log_entry]

        # ── 驳回时把 required_fixes 注入下一轮 data_scientist 的输入 ──
        new_messages = list(state.get("messages", [])) + [response]
        if not is_approved and required_fixes and new_rejection_count < 3:
            fix_text = "【审查员驳回 —— 请按以下修正要求重新处理】\n" + "\n".join(
                f"- {f}" for f in required_fixes
            )
            new_messages.append(HumanMessage(content=fix_text))

        return {
            "messages": new_messages,
            "task_status": "auditing",
            "audit_result": verdict_dict,
            "rejection_count": new_rejection_count,
            "current_csv_path": state.get("current_csv_path", ""),
            "execution_logs": new_logs,
            "data_scientist_report": state.get("data_scientist_report"),
            "chief_scientist_report": state.get("chief_scientist_report"),
        }

    def chief_scientist_node(state: MultiAgentState) -> MultiAgentState:
        audit = state.get("audit_result") or {}
        reasons_str = "\n".join(f"- {r}" for r in audit.get("reasons", [])) if isinstance(audit, dict) else "（无结构化裁决）"
        diagnosis_prompt = f"""基于以下材料，进行深度物理诊断并生成严格 JSON 格式的最终报告：

数据科学家报告：
{state.get('data_scientist_report', '无')}

审查员裁决 ({audit.get('verdict', '?')}):
{reasons_str}

请：
1. 结合知识库进行物理诊断
2. 分析材料力学行为
3. 根据数据类型选择诊断路径（光纤 → 波长/应变/温度；通用 → 趋势/异常/完整性）
4. 严格按照上方系统提示词中的 JSON schema 输出报告
"""

        messages = [SystemMessage(content=chief_prompt), HumanMessage(content=diagnosis_prompt)]
        response = llm.invoke(messages)

        return {
            "messages": [response],
            "task_status": "diagnosis",
            "chief_scientist_report": str(response.content),
            "current_csv_path": state.get("current_csv_path", ""),
            "execution_logs": state.get("execution_logs", []),
            "audit_result": state.get("audit_result"),
            "data_scientist_report": state.get("data_scientist_report"),
            "rejection_count": state.get("rejection_count", 0),
        }

    return data_scientist_node, auditor_node, chief_scientist_node


def should_continue_workflow(state: MultiAgentState) -> str:
    """判断工作流走向 — Phase 3: 基于结构化 AuditVerdict 确定性路由。

    - pass → chief_scientist
    - reject 且 count < 3 → data_scientist（required_fixes 已注入 messages）
    - reject 且 count >= 3 → 显式失败终态（task_status="failed"，不再兜圈子）
    - auditor 产出非法 JSON → 已在 auditor_node 内部抛 ValueError
    """
    task_status = state.get("task_status", "")
    rejection_count = state.get("rejection_count", 0)
    audit_result = state.get("audit_result") or {}

    # ── 安全阀：驳回 ≥3 次 → 显式失败终态 ──
    if rejection_count >= 3:
        all_reasons: list[str] = []
        all_fixes: list[str] = []
        # 汇总历次 reasons + required_fixes（从 audit_result 链中收集）
        verdict = audit_result if isinstance(audit_result, dict) else {}
        all_reasons = list(verdict.get("reasons", []))
        all_fixes = list(verdict.get("required_fixes", []))
        # 写入终态日志
        # （execution_logs 由 auditor_node 写入，这里不再重复写 state — 只在返回时设 task_status）
        return "failed"

    # ── 从 data_scientist 节点出来 → 进入 auditor ──
    if task_status == "data_processing":
        return "auditor"

    # ── 从 auditor 节点出来 → 基于结构化裁决路由 ──
    if task_status == "auditing":
        verdict = audit_result.get("verdict") if isinstance(audit_result, dict) else None
        if verdict == "pass":
            return "chief_scientist"
        if verdict == "reject":
            return "data_scientist"
        # 裁决不明确 → 按 reject 处理（安全侧）
        return "data_scientist"

    # ── 从 chief_scientist 出来，结束 ──
    if task_status == "diagnosis":
        return END

    return END


# ============ 创建工作流图 ============

def create_multi_agent_graph(llm: BaseChatModel, data_context: dict | None = None):
    """创建多智能体工作流

    Args:
        llm: 大语言模型实例
        data_context: 可选的结构化数据上下文（模板、清洗统计、数据类型等）
    """
    ds_node, aud_node, chief_node = _make_nodes(llm, data_context)

    workflow = StateGraph(MultiAgentState)

    workflow.add_node("data_scientist", ds_node)
    workflow.add_node("auditor", aud_node)
    workflow.add_node("chief_scientist", chief_node)

    workflow.set_entry_point("data_scientist")

    workflow.add_conditional_edges(
        "data_scientist",
        should_continue_workflow,
        {"auditor": "auditor", "chief_scientist": "chief_scientist", "failed": END, END: END}
    )

    workflow.add_conditional_edges(
        "auditor",
        should_continue_workflow,
        {"chief_scientist": "chief_scientist", "data_scientist": "data_scientist", "failed": END, END: END}
    )

    workflow.add_edge("chief_scientist", END)

    return workflow.compile()


# ============ 运行入口 ============

def run_multi_agent(
    user_input: str,
    api_key: str = "",
    base_url: str = "",
    model_name: str = "deepseek-chat",
    csv_path: str = None,
    data_context: dict | None = None,
) -> dict:
    """
    运行多智能体审查系统

    Args:
        user_input: 用户的分析需求
        api_key: API 密钥（与主应用 AI 诊断共用）
        base_url: API 地址
        model_name: 模型名称
        csv_path: 可选的 CSV 数据路径
        data_context: 结构化数据上下文 dict（来自 ui/ai_diagnosis.py _build_data_context()），
                      自动注入所有 Agent 系统提示词，含数据类型防火墙、模板信息、清洗统计

    Returns:
        dict: 包含各阶段报告和最终诊断结果
    """
    llm = create_llm(api_key, base_url, model_name)
    graph = create_multi_agent_graph(llm, data_context)

    initial_state = MultiAgentState(
        messages=[HumanMessage(content=user_input)],
        current_csv_path=csv_path or "",
        execution_logs=[],
        audit_result=None,
        data_scientist_report=None,
        chief_scientist_report=None,
        rejection_count=0,
        task_status="pending"
    )

    result = graph.invoke(initial_state, config={"recursion_limit": 50})

    task_status = result.get("task_status", "complete")
    audit = result.get("audit_result") or {}
    return {
        "data_scientist_report": result.get("data_scientist_report", ""),
        "audit_result": audit,
        "chief_scientist_report": result.get("chief_scientist_report", ""),
        "final_report": result.get("messages", [{}])[-1].content if result.get("messages") else "",
        "task_status": task_status,
        "rejection_count": result.get("rejection_count", 0),
        "execution_logs": result.get("execution_logs", []),
        # 显式失败标志
        "is_failed": task_status == "failed",
    }


if __name__ == "__main__":
    print("=== DataProcessor Pro 多智能体审查系统 ===")
    api_key = os.environ.get("DEEPSEEK_API_KEY", "")
    api_base = os.environ.get("DEEPSEEK_API_BASE", "https://api.deepseek.com")
    if not api_key:
        print("请设置 DEEPSEEK_API_KEY 环境变量或通过 UI 配置模型")
    else:
        result = run_multi_agent(
            "请分析当前数据文件中的噪声并生成处理报告",
            api_key=api_key,
            base_url=api_base,
        )
        print("数据科学家报告:", result["data_scientist_report"][:200], "...")
        print("审查结果:", result["audit_result"])
        print("首席专家报告:", result["chief_scientist_report"][:200], "...")
