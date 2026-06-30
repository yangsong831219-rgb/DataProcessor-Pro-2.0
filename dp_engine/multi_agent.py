"""DataProcessor Pro 多智能体顾问系统 (Phase 6 — 线性单遍, 输入预算安全).

基于 LangGraph 实现的三角色 Agent 团队：
- Agent 1: 数据科学家 (Data Scientist) — 详报
- Agent 2: 审核顾问 (Data Advisor) — 审查意见，不裁决
- Agent 3: 首席传感专家 (Chief Scientist) — 精炼综合，产出结构化诊断报告

协作模式（线性，无回环）：
用户指令 → 数据科学家(详报) → 审核顾问(意见) → 首席专家(精炼综合) → 最终报告

Phase 6 变更：
- 输入预算: chief 不灌 DS 全文，只给摘要 + 审查意见（均在预算内）
- max_tokens 按上下文窗口安全计算，不再用 8192 撞 -c 8192
- 截断不崩: chief 触发 AIClientTruncationError → 优雅降级，仍出富报告
- 精炼综合: chief prompt 明确 "只做综合判断，不复述推导细节"
"""

from __future__ import annotations

import json
import re
import time
from typing import Any, TypedDict, Annotated

from langchain_core.messages import BaseMessage, HumanMessage, AIMessage
from langgraph.graph import StateGraph, END

# Phase 6: 截断降级 / 超时需要此异常类型
from core.ai_errors import AIClientTimeoutError, AIClientTruncationError


# ═══════════════════════════════════════════════════════════════════════
# JSON Schema (chief 仍产出结构化报告供 UI 卡片渲染)
# ═══════════════════════════════════════════════════════════════════════

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
            },
        },
        "data_quality_assessment": {
            "type": "object",
            "properties": {
                "completeness": {"type": "string"},
                "consistency": {"type": "string"},
                "anomaly_patterns": {"type": "array", "items": {"type": "string"}},
                "recommendations": {"type": "array", "items": {"type": "string"}},
            },
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
                            "mean": {"type": "number"}, "std": {"type": "number"},
                            "min": {"type": "number"}, "max": {"type": "number"},
                        },
                    },
                    "findings": {"type": "string"},
                    "suggestions": {"type": "string"},
                },
            },
        },
        "physical_diagnosis": {
            "type": "object",
            "properties": {
                "phenomenon": {"type": "string"},
                "possible_causes": {"type": "array", "items": {"type": "string"}},
                "severity": {"type": "string", "enum": ["low", "medium", "high"]},
                "recommended_actions": {"type": "array", "items": {"type": "string"}},
            },
        },
    },
}

_JSON_SCHEMA_STR = json.dumps(CHIEF_REPORT_JSON_SCHEMA, ensure_ascii=False, indent=2)


# ═══════════════════════════════════════════════════════════════════════
# LLM 调用辅助 (线程安全: 每次调用取 AIClient 单例)
# ═══════════════════════════════════════════════════════════════════════


def _invoke_llm(system_prompt: str, user_prompt: str,
                max_tokens: int = 0) -> str:
    """通过 AIClient 同步调用 LLM (enable_thinking=False)。

    max_tokens=0 → 动态按 ctx − prompt − margin 拉满可用空间。
    max_tokens>0 → 在"调用前最终prompt"上重算 available，钳到 ≤ available。
    ★ 关键：不在中间态 prompt 上提前算 max_tokens；
      始终在 _invoke_llm 真正调用前、用此刻实际的完整 prompt 重算。
    """
    from core.ai_client import AIClient
    ai = AIClient.get_instance()
    prompt_est = ai._estimate_tokens(system_prompt) + ai._estimate_tokens(user_prompt)
    ctx = ai._get_context_size()
    # ★ 在最终 prompt 上重算真实可用输出空间
    real_max = ai._safe_max_tokens(system_prompt, user_prompt, ctx)
    if max_tokens > 0:
        # 调用方传了值(可能是早期中间 prompt 算的旧值) → 钳到不超过真实 available
        max_tokens = min(max_tokens, real_max)
    else:
        max_tokens = real_max
    print(f"[DIAG] multi_agent._invoke_llm: backend={ai.backend}, "
          f"model={ai.model_name}, ctx={ctx}, max_tokens={max_tokens}, "
          f"prompt_est_tokens={prompt_est}, available={ctx - prompt_est - 512}, "
          f"real_max={real_max}")
    try:
        return ai.generate(
            prompt=user_prompt,
            system_prompt=system_prompt,
            temperature=0.3,
            max_tokens=max_tokens,
            enable_thinking=False,
        )
    except AIClientTimeoutError:
        # 本地慢模型: 单轮生成可能超过读超时, 不自动重试 (避免重触发雪上加霜)
        _be = ai.backend
        _mt = max_tokens
        _to_s = (_mt / 9.0) if _be == 'local' else (_mt / 50.0)  # 粗略估计所需时间
        raise AIClientTimeoutError(
            f"本地模型生成超时（已等待约 {_to_s:.0f} 秒后客户端断开）。"
            f"这通常是单轮输出过长（max_tokens={_mt}）或本地推理过慢导致。"
            f"可在 AI 配置中调高「本地超时(read_timeout_s)」，或减少诊断上下文。"
            f"\n（后端: {_be}, 模型: {ai.model_name}）"
        )


def _extract_advisory_text(raw: str) -> str:
    """从 Advisor 输出中提取审查意见（极度宽松：纯文本直通）。

    不像旧版 _parse_audit_json 那样要求 JSON 结构。
    若输出是 JSON 也原样保留；若是 markdown/纯文本，直接返回。
    仅过滤掉纯空的退化情况。
    """
    if not raw or not raw.strip():
        return ""  # 空响应 → 空建议，不抛异常
    return raw.strip()


# ═══════════════════════════════════════════════════════════════════════
# Token 预算估算与输入裁剪 (Phase 6: 防截断崩溃)
# ═══════════════════════════════════════════════════════════════════════

# 默认上下文窗口 (保守取 8k — 多数本地模型上限)
def _get_ctx_tokens() -> int:
    """读取当前后端上下文窗口大小 (token)。"""
    try:
        from core.ai_client import AIClient
        return AIClient.get_instance()._get_context_size()
    except Exception:
        return 8192  # 降级默认

_SAFE_CTX_TOKENS = 8192  # fallback — 实际调用 _safe_max_tokens 时会 override
# 输出预留 margin (system prompt + overhead)
_CTX_MARGIN = 600


def _estimate_tokens(text: str) -> int:
    """基于字符数粗略估算 token 数。

    中文 ≈ 1.5 char/token, 英文/代码 ≈ 3.5 char/token。
    使用保守估计: 整体 ~2.5 char/token (混合文本)。
    """
    if not text:
        return 0
    # 分别统计 CJK 和 ASCII 字符
    cjk = sum(1 for c in text if '一' <= c <= '鿿' or '　' <= c <= '〿')
    ascii_chars = len(text) - cjk
    # CJK ~1.5 char/token, ASCII ~3.5 char/token
    return max(1, int(cjk / 1.5 + ascii_chars / 3.5))


def _trim_report_for_budget(report: str, max_input_tokens: int) -> str:
    """将报告裁剪到指定 token 预算内。

    策略：头尾保留 (header + conclusion)，中间截断。
    - 前 60% budget 给开头 (通常含结论/摘要)
    - 后 40% budget 给结尾 (通常含建议/总结)
    - 中间插入截断标记

    若已在预算内则原样返回。
    """
    if not report:
        return report
    estimated = _estimate_tokens(report)
    if estimated <= max_input_tokens:
        return report

    # 按字符比例粗略裁剪 (同比例 ≈ 同 token 比)
    ratio = max_input_tokens / estimated
    target_len = max(200, int(len(report) * ratio * 0.9))  # 留 10% 缓冲

    head_ratio = 0.6
    head_len = int(target_len * head_ratio)
    tail_len = target_len - head_len

    head = report[:head_len]
    tail = report[-tail_len:] if tail_len > 0 else ""
    cut_note = "\n\n…[中间段已裁剪以控制输入长度]…\n\n"

    return head + cut_note + tail


def _safe_max_tokens(system_prompt: str, user_prompt: str,
                     requested: int,
                     ctx_tokens: int | None = None,
                     margin: int = _CTX_MARGIN) -> int:
    """安全计算 max_tokens: 确保 prompt 估算 + max_tokens + margin ≤ ctx。

    ctx_tokens=None → 从 AIClient 实时读取上下文窗口大小。
    """
    if ctx_tokens is None:
        ctx_tokens = _get_ctx_tokens()
    prompt_est = _estimate_tokens(system_prompt) + _estimate_tokens(user_prompt)
    available = ctx_tokens - prompt_est - margin
    return max(512, min(requested, available))


# ═══════════════════════════════════════════════════════════════════════
# 数据上下文构建器
# ═══════════════════════════════════════════════════════════════════════


def build_context_block(data_context: dict | None = None) -> str:
    if not data_context:
        return ""

    data_type = data_context.get("data_type", "unknown")
    is_fiber = data_type == "fiber_optic"
    template_name = data_context.get("template_name", "(无模板)")
    cleaning = data_context.get("cleaning_summary", "(未清洗)")
    rows = data_context.get("data_rows", 0)
    cols = data_context.get("data_columns", 0)

    type_label = ("光纤光栅传感器数据（基于波长差 W1~W8 进行物理量转换）"
                  if is_fiber else "通用数据（外部已算好，直接读取标注列）")

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


# ═══════════════════════════════════════════════════════════════════════
# 系统提示词模板 (Phase 5: Advisor 顾问式, Chief 综合式)
# ═══════════════════════════════════════════════════════════════════════

DATA_SCIENTIST_PROMPT_TPL = """你是一名资深 Python 数据科学家，专门从事传感器数据分析。

{data_context}

你的职责：
1. 接收数据文件和分析需求
2. 进行数据清洗和滤波处理
3. 提取波形特征
4. 生成技术备忘录（包含处理步骤和清洗前后特征比对）

注意：请根据上方"数据类型"选择正确的处理路径：
- 光纤数据 → 基于波长差计算，关注 FBG 漂移
- 通用数据 → 直接从标注列读取，不做波长转换

=== 成品化约束（绝对纪律） ===
- 严禁向读者发问（如"请确认""待确认后执行""以便生成"等），必须直接给出分析结论。
- 有不确定 → 写"分析口径/假设说明："陈述前提后照常给结论，不挂起。
- 严禁臆造数值：缺标定系数/灵敏度/阈值等 → 明示"未提供，无法计算"，不得假设≈1500pm/≈3%等任何虚构数字。
- 时间戳歧义按数据清洗口径陈述，正文不残留"(2035？)"等问号。
- 禁止 ASCII 字符画：不得用 | / \\ - _ 等字符拼绘趋势图/曲线/坐标轴/示意图。如需图表用文字描述或数据表表达，注明"由软件绘图模块出图"。

你的输出应该是专业的数据分析报告。"""


DATA_ADVISOR_PROMPT_TPL = """你是一名经验丰富的实验数据审查顾问，专门为数据分析报告提供建设性反馈。

{data_context}

你的职责：
- 审查数据科学家的分析报告，找出潜在问题、风险、遗漏和需补充验证之处
- 提出具体的改进建议，供首席专家在撰写最终诊断报告时参考
- 评估分析方法的合理性、物理可解释性

根据数据类型调整审查重点：
- 光纤数据：重点关注波长漂移趋势、应变/温度耦合分析、滤波参数选择
- 通用数据：重点关注标注列一致性、缺失值影响、异常分布合理性

【重要】你的角色是顾问，不是审判者——你只提供意见和建议供首席参考，不做"通过/否决"决定。
输出格式不限：可以是要点列表、段落文字、或结构化的观察记录。
用中文输出。"""


CHIEF_SCIENTIST_PROMPT_TPL = """你是光纤光栅传感器研发总负责人，拥有深厚的材料力学背景。

{data_context}

你的职责：
1. 综合数据科学家的技术分析报告和审核顾问的审查意见
2. 对审核顾问指出的问题逐一回应：接受修正、补充数据、或解释风险可控
3. 将数字转化为物理诊断结论
4. 分析材料力学行为（如 NOA 81 胶水与 PI 光纤的界面滑移）
5. 根据数据类型选择诊断路径：
   - 光纤数据 → 分析波长漂移、应变/温度耦合、界面滑移
   - 通用数据 → 分析数据完整性、趋势变化、异常成因

=== 输出要求 ===
你必须严格以 JSON 格式输出最终诊断报告，遵循以下 schema（输出纯 JSON，不要 markdown 包裹，不要多余文字）：

{json_schema}

注意：全部用中文输出。你需要对审核顾问的意见做出实质性回应，不要忽略它们。"""


# ═══════════════════════════════════════════════════════════════════════
# 状态定义 (Phase 5: 移除 rejection_count)
# ═══════════════════════════════════════════════════════════════════════


class MultiAgentState(TypedDict, total=False):
    messages: Annotated[list[BaseMessage], lambda x, y: x + y]
    current_csv_path: str
    execution_logs: list[str]
    data_scientist_report: str | None
    audit_advisory: str | None
    chief_scientist_report: str | None
    chief_truncated: bool


# ═══════════════════════════════════════════════════════════════════════
# 进度回调 (由 Thread 注入)
# ═══════════════════════════════════════════════════════════════════════

ProgressCallback = Any  # callable(str) → None


# ═══════════════════════════════════════════════════════════════════════
# 节点工厂 (AIClient-backed, Phase 5 线性)
# ═══════════════════════════════════════════════════════════════════════


def _make_nodes(data_context: dict | None = None,
                progress: ProgressCallback = None):
    """创建三个线性节点，全部经 AIClient (enable_thinking=False)。

    Phase 6 输入预算：
    - advisor: DS 详报裁剪到 ~2000 token 预算
    - chief: DS 摘要(1500 token) + 审查意见(800 token)，精炼综合
    - chief max_tokens 不超过 ctx - prompt估算 - margin，上限 4096
    """
    ctx_block = build_context_block(data_context)

    ds_prompt = DATA_SCIENTIST_PROMPT_TPL.format(data_context=ctx_block)
    advisor_prompt = DATA_ADVISOR_PROMPT_TPL.format(data_context=ctx_block)
    chief_prompt = CHIEF_SCIENTIST_PROMPT_TPL.format(
        data_context=ctx_block, json_schema=_JSON_SCHEMA_STR,
    )

    def _emit(msg: str) -> None:
        if progress and callable(progress):
            try:
                progress(msg)
            except Exception:
                pass

    # ── 节点 1: 数据科学家 ──
    def data_scientist_node(state: MultiAgentState) -> MultiAgentState:
        _emit("数据科学家分析中…")
        t0 = time.time()

        raw = _invoke_llm(
            system_prompt=ds_prompt,
            user_prompt=str(state.get("messages", [HumanMessage(content="分析数据")])[-1].content),
            max_tokens=0,  # 0=动态: _safe_max_tokens 按 ctx−prompt−margin 计算
        )
        elapsed = time.time() - t0

        new_logs = list(state.get("execution_logs", [])) + [
            f"[DataScientist] {len(raw)} chars in {elapsed:.0f}s"
        ]
        _emit(f"数据科学家完成 ({elapsed:.0f}s, {len(raw)} chars)")

        return {
            "messages": [AIMessage(content=raw)],
            "data_scientist_report": raw,
            "current_csv_path": state.get("current_csv_path", ""),
            "execution_logs": new_logs,
            "audit_advisory": state.get("audit_advisory"),
            "chief_scientist_report": state.get("chief_scientist_report"),
        }

    # ── 节点 2: 审核顾问 (顾问式 — 不裁决, 输入裁剪) ──
    def advisor_node(state: MultiAgentState) -> MultiAgentState:
        _emit("审核员复核中…")
        t0 = time.time()

        ds_report = state.get("data_scientist_report") or "无报告"
        # Phase 6: 裁剪 DS 报告到 ~2000 token 预算 (避免超 ctx)
        ds_trimmed = _trim_report_for_budget(ds_report, 2000)
        if len(ds_trimmed) < len(ds_report):
            _emit("审核员 DS 输入已裁剪以适应窗口")
        advisory_prompt = (
            f"请审查以下数据科学家的工作成果，给出你的审查意见：\n\n{ds_trimmed}\n\n"
            "请指出发现的问题、风险、遗漏之处，以及需补充验证的内容。"
            "\n\n注意：你是顾问，只提建议供首席参考，不做出 pass/reject 决定。"
        )

        # Advisor 输出动态 — 由 _invoke_llm 在最终 prompt 上重算
        raw = _invoke_llm(advisor_prompt, advisory_prompt, max_tokens=0)
        advisory = _extract_advisory_text(raw)
        elapsed = time.time() - t0

        new_logs = list(state.get("execution_logs", [])) + [
            f"[Advisor] {len(raw)} chars in {elapsed:.0f}s"
        ]
        _emit(f"审核员完成 ({elapsed:.0f}s, {len(raw)} chars)")

        # 注入审查意见到 messages (供 chief 读取)
        new_messages = list(state.get("messages", [])) + [
            AIMessage(content=f"【审核顾问意见】\n{advisory}" if advisory else "【审核顾问意见】\n(未产出具体意见)")
        ]

        return {
            "messages": new_messages,
            "audit_advisory": advisory,
            "current_csv_path": state.get("current_csv_path", ""),
            "execution_logs": new_logs,
            "data_scientist_report": state.get("data_scientist_report"),
            "chief_scientist_report": state.get("chief_scientist_report"),
        }

    # ── 节点 3: 首席专家 (精炼综合 + 截断降级) ──
    def chief_scientist_node(state: MultiAgentState) -> MultiAgentState:
        _emit("首席综合中…")
        t0 = time.time()

        ds_text = state.get("data_scientist_report") or "无报告"
        advisory = state.get("audit_advisory") or ""

        # Phase 6: 只喂结论摘要(不灌全文) — 裁剪 DS 到 800 token + 审查意见到 400 token
        # chief 只需知道关键发现和建议, 细节已在前序节点保留
        ds_trimmed = _trim_report_for_budget(ds_text, 800)
        adv_trimmed = _trim_report_for_budget(advisory, 400)
        if len(ds_trimmed) < len(ds_text):
            _emit("首席 DS 输入已裁剪以适应窗口")
        if len(adv_trimmed) < len(advisory):
            _emit("首席 审查意见已裁剪以适应窗口")

        diagnosis_prompt = (
            f"你是首席传感专家，请基于以下材料输出精炼综合诊断（严格 JSON）：\n\n"
            f"【数据科学家结论/要点（摘要）】\n{ds_trimmed}\n\n"
            f"【审核顾问审查意见】\n{adv_trimmed if adv_trimmed else '(无)'}\n\n"
            "要求：\n"
            "1. 只做综合判断、简明扼要 — 不要复述数据科学家的推导细节（细节已单独保留）\n"
            "2. 对审核顾问指出的问题做实质性回应\n"
            "3. 将数值转化为物理诊断结论\n"
            "4. 严格按照 JSON schema 输出，只输出 JSON，不要 markdown 包裹，不要多余文字"
        )

        # ★ max_tokens 由 _invoke_llm 在最终 prompt 上重算，此处不再提前算
        _p_est = _estimate_tokens(chief_prompt) + _estimate_tokens(diagnosis_prompt)
        _avail = _get_ctx_tokens() - _p_est - _CTX_MARGIN
        print(f"[DIAG] chief_scientist_node: prompt_est={_p_est}, "
              f"available={_avail}, "
              f"ctx={_get_ctx_tokens()}, margin={_CTX_MARGIN}, "
              f"input_budget=(DS≤800+ADV≤400 tokens)")
        truncation_occurred = False
        raw = ""

        try:
            raw = _invoke_llm(chief_prompt, diagnosis_prompt, max_tokens=0)
        except AIClientTruncationError as e:
            truncation_occurred = True
            _emit("首席输出截断, 降级保底")
            # 尝试取出部分内容
            partial = getattr(e, 'partial_content', '') or ''
            if partial and partial.strip():
                raw = partial.strip()
            else:
                raw = ""

        elapsed = time.time() - t0
        new_logs = list(state.get("execution_logs", [])) + [
            f"[Chief] {len(raw)} chars in {elapsed:.0f}s"
            + (", TRUNCATED" if truncation_occurred else "")
        ]

        if truncation_occurred:
            _emit(f"首席完成 (截断降级, {len(raw)} chars, {elapsed:.0f}s)")
        else:
            _emit(f"首席专家完成 ({elapsed:.0f}s, {len(raw)} chars)")

        return {
            "messages": [AIMessage(content=raw)],
            "chief_scientist_report": raw,
            "chief_truncated": truncation_occurred,
            "current_csv_path": state.get("current_csv_path", ""),
            "execution_logs": new_logs,
            "data_scientist_report": state.get("data_scientist_report"),
            "audit_advisory": state.get("audit_advisory"),
        }

    return data_scientist_node, advisor_node, chief_scientist_node


# ═══════════════════════════════════════════════════════════════════════
# 图构建 (Phase 5: 线性, 无回环)
# ═══════════════════════════════════════════════════════════════════════


def create_multi_agent_graph(data_context: dict | None = None,
                             progress: ProgressCallback = None):
    """构建线性多智能体图: START → DS → Advisor → Chief → END。

    不再需要 should_continue_workflow / 条件边 / 驳回 / 重跑。
    """
    ds_node, adv_node, chief_node = _make_nodes(data_context, progress)

    workflow = StateGraph(MultiAgentState)
    workflow.add_node("data_scientist", ds_node)
    workflow.add_node("advisor", adv_node)
    workflow.add_node("chief_scientist", chief_node)

    workflow.set_entry_point("data_scientist")
    workflow.add_edge("data_scientist", "advisor")
    workflow.add_edge("advisor", "chief_scientist")
    workflow.add_edge("chief_scientist", END)

    return workflow.compile()


# ═══════════════════════════════════════════════════════════════════════
# 运行入口 (Phase 5: 简化返回结构)
# ═══════════════════════════════════════════════════════════════════════


def run_multi_agent(
    user_input: str,
    csv_path: str | None = None,
    data_context: dict | None = None,
    progress: ProgressCallback = None,
) -> dict:
    """运行多智能体顾问系统 (Phase 5: 线性单遍)。

    Args:
        user_input: 用户的分析需求
        csv_path: 可选的 CSV 数据路径
        data_context: 来自 _build_data_context() 的结构化上下文
        progress: 可选的进度回调 callable(str)

    Returns:
        dict: {
            chief_report: str,              # 首席综合报告 raw str (含结构化 JSON)
            data_scientist_text: str,        # 数据科学家详报 (完整)
            audit_advisory: str | None,      # 审核顾问审查意见 (自由文本)
            execution_logs: list[str],       # 各步日志
            chief_truncated: bool,           # 首席输出是否被截断 (降级保底标记)
        }
    """
    graph = create_multi_agent_graph(data_context, progress)

    initial_state = MultiAgentState(
        messages=[HumanMessage(content=user_input)],
        current_csv_path=csv_path or "",
        execution_logs=[],
        data_scientist_report=None,
        audit_advisory=None,
        chief_scientist_report=None,
        chief_truncated=False,
    )

    result = graph.invoke(initial_state, config={"recursion_limit": 50})

    return {
        "chief_report": result.get("chief_scientist_report", ""),
        "data_scientist_text": result.get("data_scientist_report", ""),
        "audit_advisory": result.get("audit_advisory"),
        "execution_logs": result.get("execution_logs", []),
        "chief_truncated": result.get("chief_truncated", False),
    }


# ═══════════════════════════════════════════════════════════════════════
# QThread worker (Phase 5: 不变, 仍 emit dict)
# ═══════════════════════════════════════════════════════════════════════

import sys as _sys
if "PyQt6" in _sys.modules or True:  # always available for type hints
    try:
        from PyQt6.QtCore import QThread, pyqtSignal

        class MultiAgentThread(QThread):
            """多智能体顾问 QThread — 不阻塞 UI。

            Signals:
                progress(str): 当前节点
                finished(dict): 完成时发射 {chief_report, data_scientist_text, audit_advisory, ...}
                error(str): 错误消息
            """

            progress = pyqtSignal(str)
            finished = pyqtSignal(dict)
            error = pyqtSignal(str)

            def __init__(self, user_input: str, csv_path: str | None = None,
                         data_context: dict | None = None,
                         parent=None):
                super().__init__(parent)
                self.user_input = user_input
                self.csv_path = csv_path
                self.data_context = data_context

            def run(self) -> None:
                try:
                    result = run_multi_agent(
                        user_input=self.user_input,
                        csv_path=self.csv_path,
                        data_context=self.data_context,
                        progress=lambda msg: self.progress.emit(msg),
                    )
                    self.finished.emit(result)
                except Exception as e:
                    import traceback
                    self.error.emit(f"{e}\n{traceback.format_exc()}")

    except ImportError:
        pass  # headless test — no Qt available
