"""报告生成引擎 — 多智能体分步生成

设计原则:
1. Schema-driven：AI 只输出符合 Pydantic Schema 的 JSON
2. 分块生成：每章单独调用 AI，降低幻觉
3. 无状态：所有输入通过参数传递，不持有内部状态
4. 解耦：通过 generate_fn 回调接入任意 LLM 后端
5. 结构化输出 (Phase 2)：Section 级别用 Pydantic 校验替换 json.loads fallback
"""

from __future__ import annotations

import json
import os
import re
from typing import Any, Callable, Dict, List, Optional

from pydantic import ValidationError

from core.report_models import PPTReport, PPTSlide, WordReport, WordSection
from core.ai_errors import ReportSchemaError

# Schema 注入 — 运行时构建
from core.ai_client import build_schema_prompt

_WORD_SCHEMA_CACHE: str = ''
_PPT_SCHEMA_CACHE: str = ''


def _get_word_schema() -> str:
    global _WORD_SCHEMA_CACHE
    if not _WORD_SCHEMA_CACHE:
        _WORD_SCHEMA_CACHE = build_schema_prompt([WordSection])
    return _WORD_SCHEMA_CACHE


def _get_ppt_schema() -> str:
    global _PPT_SCHEMA_CACHE
    if not _PPT_SCHEMA_CACHE:
        _PPT_SCHEMA_CACHE = build_schema_prompt([PPTSlide])
    return _PPT_SCHEMA_CACHE


# ═══════════════════════════════════════════════════════════════════
# Prompt 模板
# ═══════════════════════════════════════════════════════════════════

# Agent 工具模式 System Prompt（追加到用户 prompt 之前）
SECTION_TOOLS_SYSTEM_PROMPT = (
    "你是一个报告写作专家。在撰写报告时，你可以调用以下工具来获取数据图表：\n"
    "- generate_sensor_plot: 生成传感器趋势图或分布直方图。\n"
    "  工具返回图片文件名的字符串，请用 [INSERT_IMAGE: 文件名.png] 格式插入报告中。\n\n"
    "你必须严格按照指定的 JSON Schema 格式输出，不要包含任何额外文字说明。"
)

OUTLINE_PROMPT = """你是一个专业的技术报告写作专家。请根据以下信息，生成一份结构化的报告大纲。

报告类型: {report_type}

{context}

要求：
- 输出 Markdown 格式大纲
- 第一行为报告标题：# 标题
- 后续每行为章节标题：## 章节名
- 每个章节下列出 2-4 个关键论点，用 - 开头
- Word 报告：5-8 章，每章可详述
- PPT 报告：8-10 页，每页 2-4 个要点
- 语言：中文
- 只输出大纲，不要额外说明"""

SECTION_PROMPT_WORD = """你是一位工程技术报告撰写专家。请根据以下大纲章节，撰写该章节的完整正文内容。

报告标题: {report_title}
当前章节: {section_heading}
该章节关键论点:
{key_points}

项目资料上下文:
{project_context}

你必须输出一个合法的 JSON 对象，对应一个 ReportSection：
{word_schema}

要求：
- 正文段落每段 100-300 字，专业、数据驱动
- 图表引用使用 [INSERT_IMAGE: 文件名.png] 格式
- 语言：中文
- 只输出 JSON 对象（不是数组），不要额外文字"""

SECTION_PROMPT_PPT = """你是一位技术汇报演示专家。请根据以下大纲章节，生成该页幻灯片内容。

演示标题: {report_title}
当前幻灯片: {section_heading}
关键论点:
{key_points}

项目资料上下文:
{project_context}

你必须输出一个合法的 JSON 对象，对应一个 SlideContent：
{ppt_schema}

要求：
- 要点**最多 4 条**，每条**不超过 20 字**
- speaker_notes 可写 50-200 字详细论述
- 语言：中文
- 只输出 JSON 对象，不要额外文字"""


# ═══════════════════════════════════════════════════════════════════
# 工具函数
# ═══════════════════════════════════════════════════════════════════


def _read_file(path: str, max_chars: int = 8000) -> str:
    """安全读取文本文件，限制最大字符数."""
    if not path or not os.path.isfile(path):
        return ''
    try:
        with open(path, 'r', encoding='utf-8') as f:
            return f.read(max_chars)
    except Exception as e:
        print(f'[report_engine] 读取文件失败 {path}: {e}')
        return ''


def _build_context(config: dict) -> str:
    """从配置字典构建 AI 上下文."""
    parts: List[str] = []

    req_file = config.get('req_file', '')
    if req_file:
        content = _read_file(req_file)
        if content:
            parts.append(f'## 需求文件内容\n\n{content}')

    project_files = config.get('project_files', [])
    if project_files:
        for pf in project_files[:5]:  # 最多 5 个文件
            content = _read_file(pf, max_chars=4000)
            if content:
                fname = os.path.basename(pf)
                parts.append(f'## 项目资料: {fname}\n\n{content}')

    if not parts:
        parts.append('（未提供具体需求文件，请根据通用传感器数据分析场景生成报告）')

    return '\n\n---\n\n'.join(parts)


def _parse_outline_markdown(md: str) -> str:
    """清理和规范化 AI 返回的大纲 Markdown."""
    lines = md.strip().split('\n')
    cleaned: List[str] = []
    for line in lines:
        stripped = line.strip()
        if stripped:
            cleaned.append(stripped)
    return '\n'.join(cleaned)


def _extract_sections_from_outline(outline: str) -> List[Dict[str, Any]]:
    """从大纲 Markdown 提取章节列表."""
    sections: List[Dict[str, Any]] = []
    lines = outline.strip().split('\n')
    current_heading = ''
    current_points: List[str] = []

    for line in lines:
        line = line.strip()
        if not line:
            continue
        if line.startswith('## '):
            if current_heading:
                sections.append({
                    'heading': current_heading,
                    'key_points': current_points,
                })
            current_heading = line[3:].strip()
            current_points = []
        elif line.startswith('# ') and not current_heading:
            # 标题行，跳过
            continue
        elif line.startswith('- ') and current_heading:
            current_points.append(line[2:].strip())

    if current_heading:
        sections.append({
            'heading': current_heading,
            'key_points': current_points,
        })

    return sections


def _extract_json(text: str) -> str:
    """从 AI 回复中提取第一个 JSON 块（去除 ```json 包裹等）."""
    # 尝试 ```json ... ``` 格式
    m = re.search(r'```(?:json)?\s*([\s\S]*?)\s*```', text)
    if m:
        return m.group(1).strip()
    # 尝试直接 JSON 对象/数组
    m = re.search(r'(\{[\s\S]*\}|\[[\s\S]*\])', text)
    if m:
        return m.group(1).strip()
    return text.strip()


def _missing_fields_from_error(e: ValidationError) -> list[str]:
    """从 Pydantic ValidationError 提取缺失字段名列表。"""
    missing: list[str] = []
    for err in e.errors():
        if err.get("type") == "missing":
            loc = err.get("loc", ("?",))
            missing.append(str(loc[0]))
    return missing


def _type_errors_from_error(e: ValidationError) -> list[str]:
    """从 Pydantic ValidationError 提取类型错误描述列表。"""
    type_errs: list[str] = []
    for err in e.errors():
        if err.get("type") != "missing":
            loc = ".".join(str(x) for x in err.get("loc", ("?",)))
            type_errs.append(f"{loc}: {err.get('msg', '')}")
    return type_errs


# ═══════════════════════════════════════════════════════════════════
# 核心 API
# ═══════════════════════════════════════════════════════════════════


def generate_outline(config: dict, generate_fn: Callable[..., str]) -> str:
    """生成报告大纲。

    Args:
        config: 见 ReportWorkbenchWidget.get_config() 的输出
            - report_type: 'word' | 'ppt'
            - req_file: 需求文件路径
            - project_files: 项目资料路径列表
        generate_fn: LLM 调用函数，签名 (prompt: str) -> str

    Returns:
        Markdown 格式大纲文本
    """
    report_type = config.get('report_type', 'word')
    context = _build_context(config)

    prompt = OUTLINE_PROMPT.format(
        report_type='PPT 演示汇报' if report_type == 'ppt' else 'Word 深度技术报告',
        context=context,
    )

    raw = generate_fn(prompt)
    return _parse_outline_markdown(raw)


def generate_structured_report(
    config: dict,
    outline_text: str,
    report_type: str,
    generate_fn: Callable[..., str],
    tools_config: Optional[dict] = None,
) -> dict:
    """基于大纲分块生成结构化报告。

    逐章调用 AI，每章生成 JSON，最后组装为完整 Pydantic 模型。
    当 tools_config 提供时，启用 Agentic 工具调用（Function Calling），
    AI 可在生成过程中动态调用 generate_sensor_plot 等本地工具。

    Args:
        config: 完整配置字典
        outline_text: 用户确认/编辑后的大纲 Markdown
        report_type: 'word' | 'ppt'
        generate_fn: LLM 调用函数
        tools_config: 工具配置（启用工具调用时提供）
            - definitions: OpenAI 工具定义列表
            - executable_map: {函数名: 可执行函数} 映射

    Returns:
        WordReport 或 PPTReport 的 to_builder_dict() 结果
    """
    sections = _extract_sections_from_outline(outline_text)

    # 从大纲第一行提取标题
    title_line = outline_text.strip().split('\n')[0]
    report_title = title_line.replace('# ', '').strip() if title_line.startswith('# ') else '数据分析报告'

    project_context = _build_context(config)

    if report_type == 'word':
        return _generate_word_report(report_title, sections, project_context, generate_fn, tools_config)
    else:
        return _generate_ppt_report(report_title, sections, project_context, generate_fn, tools_config)


def _generate_word_report(
    title: str,
    sections: List[Dict[str, Any]],
    project_context: str,
    generate_fn: Callable[..., str],
    tools_config: Optional[dict] = None,
) -> dict:
    """逐章生成 Word 报告（支持 Agentic 工具调用）。

    每节输出由 Pydantic WordSection 校验；校验失败抛 ReportSchemaError，绝不静默降级。
    """
    word_sections: List[WordSection] = []

    for i, sec in enumerate(sections):
        heading = sec['heading']
        key_points = sec.get('key_points', [])

        prompt = SECTION_PROMPT_WORD.format(
            report_title=title,
            section_heading=heading,
            key_points='\n'.join(f'- {p}' for p in key_points) if key_points else '（无明确论点）',
            project_context=project_context,
            word_schema=_get_word_schema(),
        )

        if tools_config:
            from core.ai_client import AIClient
            ai = AIClient.get_instance()
            messages = [
                {'role': 'system', 'content': SECTION_TOOLS_SYSTEM_PROMPT},
                {'role': 'user', 'content': prompt},
            ]
            raw = ai.generate_with_tools(
                messages,
                tools_config['definitions'],
                tools_config['executable_map'],
            )
        else:
            raw = generate_fn(prompt)

        json_str = _extract_json(raw)

        try:
            section = WordSection.model_validate_json(json_str)
        except ValidationError as e:
            raise ReportSchemaError(
                f"第 {i+1} 节 '{heading}' AI 输出与 WordSection Schema 不匹配",
                raw_text=raw,
                missing_fields=_missing_fields_from_error(e),
                type_errors=_type_errors_from_error(e),
            ) from e

        word_sections.append(section)

    report = WordReport(
        title=title,
        sections=word_sections,
    )
    return report.to_builder_dict()


def _generate_ppt_report(
    title: str,
    sections: List[Dict[str, Any]],
    project_context: str,
    generate_fn: Callable[..., str],
    tools_config: Optional[dict] = None,
) -> dict:
    """逐页生成 PPT 报告（支持 Agentic 工具调用）。

    每页输出由 Pydantic PPTSlide 校验；校验失败抛 ReportSchemaError，绝不静默降级。
    """
    slides: List[PPTSlide] = []

    for i, sec in enumerate(sections):
        heading = sec['heading']
        key_points = sec.get('key_points', [])

        prompt = SECTION_PROMPT_PPT.format(
            report_title=title,
            section_heading=heading,
            key_points='\n'.join(f'- {p}' for p in key_points) if key_points else '（无明确论点）',
            project_context=project_context,
            ppt_schema=_get_ppt_schema(),
        )

        if tools_config:
            from core.ai_client import AIClient
            ai = AIClient.get_instance()
            messages = [
                {'role': 'system', 'content': SECTION_TOOLS_SYSTEM_PROMPT},
                {'role': 'user', 'content': prompt},
            ]
            raw = ai.generate_with_tools(
                messages,
                tools_config['definitions'],
                tools_config['executable_map'],
            )
        else:
            raw = generate_fn(prompt)

        json_str = _extract_json(raw)

        try:
            slide = PPTSlide.model_validate_json(json_str)
        except ValidationError as e:
            raise ReportSchemaError(
                f"第 {i+1} 页 '{heading}' AI 输出与 PPTSlide Schema 不匹配",
                raw_text=raw,
                missing_fields=_missing_fields_from_error(e),
                type_errors=_type_errors_from_error(e),
            ) from e

        slides.append(slide)

    report = PPTReport(
        title=title,
        slides=slides,
    )
    return report.to_builder_dict()
