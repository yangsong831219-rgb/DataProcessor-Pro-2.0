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
from core.ai_errors import (
    AIClientError, AIClientNotConfiguredError, AIClientTruncationError,
    ReportSchemaError,
)

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
    "- generate_sensor_plot: 生成传感器趋势图或分布直方图。\n\n"
    "图表将由代码自动注入到报告中，不要在 JSON 中输出 image_anchors 字段。\n\n"
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

{charts_context}

正文用 Markdown 格式输出本节的完整内容。要求：
- 本节的章标题已由代码生成，你不需要再输出章标题
- 子标题从 ## 起（不要用 # 开头——# 是章标题级，你的是子节）
- 可用 **加粗**、- 列表、1. 编号列表
- 可输出 Markdown 表格（| 列1 | 列2 |），需有 |---|---| 分隔行
- {chart_instruction}
- 语言：中文。只输出 Markdown 正文，不要 JSON、不要额外说明、不要代码围栏。"""

_CHART_INSTRUCTION_HAS = (
    "图表由代码自动注入。你可引用以下已存在的图号："
    "{charts_context}。只引用列表中已存在的图N，禁止编造图号、禁止输出图片文件名。"
)
_CHART_INSTRUCTION_NONE = (
    "本次无可用图表数据。禁止引用任何图N、禁止写「如图/如图N所示」、禁止编造图号或图片文件名。"
)

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


def _read_file_with_warnings(path: str, max_chars: int = 8000) -> tuple[str, list[str]]:
    """安全读取文件内容 + 警告列表。

    路由:
    - .docx → python-docx 提取段落+表格文字
    - .txt/.md/.json/.csv → utf-8 文本读
    - 其他扩展名 → 可见警告"不支持的格式"，不返回内容
    - 所有失败路径均进 warnings 列表，绝不静默返回 ''。

    Args:
        path: 文件路径
        max_chars: 最大字符数 (超限截断)

    Returns:
        (content, warnings)
        content: 提取的文本 (可为空字符串，表示无可用内容但非错误)
        warnings: 失败/截断警告列表，每项为一句话描述
    """
    import re
    warnings: list[str] = []
    fname = os.path.basename(path) if path else "?"

    if not path or not os.path.isfile(path):
        if path:
            warnings.append(f"文件不存在: {fname}")
        return "", warnings

    ext = os.path.splitext(path)[1].lower()

    # ── .docx: python-docx 提取段落 + 表格 ──
    if ext == '.docx':
        try:
            from docx import Document
            doc = Document(path)
            parts: list[str] = []
            for para in doc.paragraphs:
                if para.text.strip():
                    parts.append(para.text.strip())
            for table in doc.tables:
                for row in table.rows:
                    cells = [cell.text.strip() for cell in row.cells]
                    parts.append(" | ".join(cells))
            text = "\n".join(parts)
            if len(text) > max_chars:
                text = text[:max_chars - 15] + "\n(已截断)"
            return text, warnings
        except Exception as e:
            warnings.append(f"读入失败: {fname} — {e}")
            return "", warnings

    # ── 文本类: utf-8 读 ──
    if ext in ('.txt', '.md', '.json', '.csv', '.xml', '.yaml', '.yml', '.log', '.py'):
        try:
            with open(path, 'r', encoding='utf-8') as f:
                text = f.read(max_chars + 500)
            if len(text) > max_chars:
                text = text[:max_chars - 15] + "\n(已截断)"
            return text, warnings
        except UnicodeDecodeError as e:
            warnings.append(
                f"读入失败: {fname} — 编码不兼容 (UTF-8 解码失败, "
                f"文件可能为 GBK/其他编码, 暂不支持)"  # GBK 风险挂账
            )
            return "", warnings
        except Exception as e:
            warnings.append(f"读入失败: {fname} — {e}")
            return "", warnings

    # ── 默认: 不支持的格式, 可见警告 ──
    warnings.append(f"读入失败: {fname} — 不支持的格式 ({ext or '无扩展名'})")
    return "", warnings


def _read_file(path: str, max_chars: int = 8000) -> str:
    """[deprecated] 使用 _read_file_with_warnings() 替代。

    仅保留用于向后兼容旧测试，新代码请走 _read_file_with_warnings。
    """
    content, _ = _read_file_with_warnings(path, max_chars)
    return content


def _build_context(config: dict) -> tuple[str, list[str]]:
    """从配置字典构建 AI 上下文。

    Returns:
        (context_text, failed_files) — failed_files 每项为一句警告描述
    """
    parts: list[str] = []
    warnings: list[str] = []

    req_file = config.get('req_file', '')
    if req_file:
        content, w = _read_file_with_warnings(req_file)
        if content:
            parts.append(f'## 需求文件内容\n\n{content}')
        warnings.extend(w)

    project_files = config.get('project_files', [])
    if project_files:
        for pf in project_files[:5]:  # 最多 5 个文件
            content, w = _read_file_with_warnings(pf, max_chars=4000)
            if content:
                fname = os.path.basename(pf)
                parts.append(f'## 项目资料: {fname}\n\n{content}')
            warnings.extend(w)

    # ── 诊断产物注入 (来自已存 DiagnosisRecord JSON) ──
    diag_rec = config.get('_diagnosis_record')
    diagnosis_loaded = bool(diag_rec)
    if diag_rec and isinstance(diag_rec, dict):
        summary = _build_diagnosis_summary(diag_rec)
        if summary:
            parts.append(summary)
    else:
        # 空诊断 → 可见告知, 不进 LLM 兜底套话
        parts.append(
            '\n'.join([
                '# 诊断数据',
                '',
                '本次生成未加载诊断数据。',
                '',
                '（说明：若需要包含诊断结论，请先在「报告生成工作台」中点击'
                '「从已存诊断加载」载入诊断记录，再重新生成报告。）',
            ])
        )

    if not parts:
        parts.append('（未提供具体需求文件，请根据通用传感器数据分析场景生成报告）')

    return '\n\n---\n\n'.join(parts), warnings


def get_sensor_analysis(rec: dict) -> list[dict]:
    """从诊断记录中提取 sensor_analysis 列表。

    兼容 schema 1.0 (diagnosis_json 顶层) 与 1.1 (ai_diagnosis.diagnosis_json)。
    返回 sensor_analysis 列表（可能为空），绝不抛异常。
    """
    ai_d = rec.get('ai_diagnosis') or {}
    diag = ai_d.get('diagnosis_json') if ai_d else rec.get('diagnosis_json')
    if not diag or not isinstance(diag, dict):
        return []
    sa = diag.get('sensor_analysis', [])
    return sa if isinstance(sa, list) else []


def _get_diagnosis_json(rec: dict) -> dict:
    """从诊断记录中提取 diagnosis_json (兼容 v1.0/v1.1)。"""
    ai_d = rec.get('ai_diagnosis') or {}
    diag = ai_d.get('diagnosis_json') if ai_d else rec.get('diagnosis_json')
    if not diag or not isinstance(diag, dict):
        return {}
    return diag


def count_sensors(rec: dict) -> int:
    """从诊断记录中计算传感器数 (兼容 v1.0/v1.1)。"""
    return len(get_sensor_analysis(rec))


def summarize_diagnosis_record(rec: dict) -> dict:
    """加载弹窗与报告侧共用的诊断摘要事实（单一事实源 SSOT）。

    优先级与 _build_diagnosis_summary 完全一致：
    优先 multi_agent.chief_structured，降级 ai_diagnosis.diagnosis_json。

    Returns:
        dict: 对外公开字段 (供加载弹窗等直接消费) +
              中间产物字段 (供 _build_diagnosis_summary 复用，避免重复解析)
            ── 公开字段 ──
            'sensor_count': int,
            'source_label': str,           # '多智能体专家组诊断' / '单专家 AI 诊断' / '诊断结果暂缺'
            'has_multi_agent': bool,        # chief_structured 实质非空 (不看废弃 report 字段)
            'kb_count': int,
            ── 中间产物 (消费者: _build_diagnosis_summary) ──
            'chief_structured': dict|None,  # multi_agent.chief_structured 原文 (含 physical_diagnosis / diagnosis_summary / data_quality_assessment)
            'multi_agent': dict,            # multi_agent 子 dict (含 data_scientist_text / audit_advisory)
            'sensor_analysis': list[dict],  # 已选的 sensor_analysis 列表
    """
    ma = rec.get('multi_agent') or {}
    chief = ma.get('chief_structured') if isinstance(ma, dict) else None
    using_multi = bool(chief and isinstance(chief, dict) and chief)

    if using_multi:
        sa = chief.get('sensor_analysis', [])
    else:
        sa = get_sensor_analysis(rec)

    sensor_count = len(sa) if isinstance(sa, list) else 0
    kb_count = len(rec.get('kb_hits', []))

    if using_multi:
        source_label = '多智能体专家组诊断'
    elif sa:
        source_label = '单专家 AI 诊断'
    else:
        source_label = '诊断结果暂缺'

    return {
        # 公开字段
        'sensor_count': sensor_count,
        'source_label': source_label,
        'has_multi_agent': using_multi,
        'kb_count': kb_count,
        # 中间产物 — 供 _build_diagnosis_summary 复用
        'chief_structured': chief if using_multi else None,
        'multi_agent': ma,
        'sensor_analysis': sa,
    }


def _build_diagnosis_summary(rec: dict, max_chars: int = 2500) -> str:
    """从 DiagnosisRecord 构建紧凑诊断摘要 (供报告引擎注入 project_context)。

    优先 multi_agent.chief_structured（多智能体专家组），
    无有效多智能体时降级 ai_diagnosis.diagnosis_json（单专家 AI），降级有可见标注。

    内部调用 summarize_diagnosis_record() 取得共用事实，确保与加载弹窗一致。
    """
    s = summarize_diagnosis_record(rec)
    using_multi: bool = s['has_multi_agent']  # type: ignore[assignment]
    source_label: str = s['source_label']  # type: ignore[assignment]
    sa: list = s['sensor_analysis']  # type: ignore[assignment]
    chief: dict | None = s['chief_structured']  # type: ignore[assignment]
    ma: dict = s['multi_agent']  # type: ignore[assignment]

    if using_multi and chief is not None:
        pd_data = chief.get('physical_diagnosis', {}) or {}
        ds = chief.get('diagnosis_summary', {}) or {}
        dq = chief.get('data_quality_assessment', {}) or {}
        ds_text = ma.get('data_scientist_text', '')
        advisory = ma.get('audit_advisory', '')
    else:
        source_label = '单专家 AI 诊断（多智能体结果暂缺）'
        pd_data = _get_diagnosis_json(rec).get('physical_diagnosis', {}) or {}
        ds = {}
        dq = {}
        ds_text = ''
        advisory = ''

    lines = [f'# 传感器诊断数据（来源: {source_label}）']

    # ── 数据质量概览（多智能体特有）──
    if ds:
        lines.append('\n## 数据质量概览')
        if ds.get('overall_assessment'):
            lines.append(f'综合评估: {ds["overall_assessment"]}')
        if ds.get('data_quality'):
            lines.append(f'数据质量: {ds["data_quality"]}')
        if ds.get('anomaly_count') is not None:
            lines.append(f'异常点数: {ds["anomaly_count"]}')

    if dq:
        if not ds:
            lines.append('\n## 数据质量评估')
        if dq.get('completeness'):
            lines.append(f'完整性: {dq["completeness"]}')
        if dq.get('consistency'):
            lines.append(f'一致性: {dq["consistency"]}')
        patterns = dq.get('anomaly_patterns', [])
        if patterns:
            lines.append('异常模式:')
            for p in patterns[:3]:
                lines.append(f'  - {p}')
        recs = dq.get('recommendations', [])
        if recs:
            lines.append('质量建议:')
            for r in recs[:3]:
                lines.append(f'  - {r}')

    # ── 每传感器结论 ──
    if sa:
        lines.append('\n## 传感器诊断结论')
        for s in sa:
            if not isinstance(s, dict):
                continue
            sid = s.get('sensor_id', '?')
            status = s.get('status', '?')
            findings = s.get('findings', '')
            suggestions = s.get('suggestions', '')
            lines.append(f'- {sid} ({status}): {findings}')
            if suggestions:
                lines.append(f'  建议: {suggestions}')

    # ── KB 命中规则要点 ──
    kb_hits = rec.get('kb_hits', [])
    if kb_hits:
        lines.append('\n## 诊断规则命中')
        for h in kb_hits:
            objs = h.get('objects') or []
            obj_str = ', '.join(objs) if isinstance(objs, list) else str(objs) if objs else ''
            lines.append(f'- {h["id"]} [{h["severity"]}]: {h["meaning"]}')
            if obj_str:
                lines.append(f'  适用: {obj_str}')
            rec_str = h.get('recommendation', '')
            if rec_str:
                lines.append(f'  建议: {rec_str}')

    # ── 物理诊断 ──
    if pd_data and pd_data.get('phenomenon'):
        lines.append('\n## 物理诊断')
        lines.append(f'现象: {pd_data.get("phenomenon", "")}')
        for c in pd_data.get('possible_causes', [])[:3]:
            lines.append(f'  - {c}')

    # ── 多智能体专属: 数据科学家详报 ──
    if ds_text:
        text_section = ds_text[:600] if len(ds_text) > 600 else ds_text
        lines.append(f'\n## 数据科学家详细分析\n{text_section}')

    # ── 多智能体专属: 审核顾问意见 ──
    if advisory:
        adv_section = advisory[:400] if len(advisory) > 400 else advisory
        lines.append(f'\n## 审核顾问意见\n{adv_section}')

    text = '\n'.join(lines)
    if len(text) > max_chars:
        text = text[:max_chars - 20] + '\n(诊断摘要已截断)'
    return text


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


def _salvage_paragraphs(raw: str) -> str | None:
    """从损坏的 AI 原始输出中尽力提取可读段落文本。

    - 取出 "paragraphs" 数组中的字符串
    - 去掉 JSON 转义和 ```json 围栏
    - 取出任何看起来像中文段落的连续文本
    返回单个合并字符串或 None。
    """
    if not raw:
        return None
    # 1) 尝试提取 "paragraphs" 数组内容
    m = re.search(r'"paragraphs"\s*:\s*\[([\]]*)\]', raw, re.DOTALL)
    if m:
        arr = m.group(1)
        # 匹配 "..." 字符串
        texts: list[str] = []
        # 匹配 JSON 字符串: "content" 或 "containing escaped chars"
        idx2 = 0
        while idx2 < len(arr):
            q = arr.find('"', idx2)
            if q < 0:
                break
            e2 = q + 1
            while e2 < len(arr):
                if arr[e2] == '\\' and e2 + 1 < len(arr):
                    e2 += 2
                elif arr[e2] == '"':
                    txt = arr[q + 1:e2]
                    texts.append(txt.replace('\\"', '"').replace('\\n', '\n'))
                    idx2 = e2 + 1
                    break
                else:
                    e2 += 1
            else:
                break
        if texts:
            # 去掉转义引号
            cleaned = [t.replace('\\"', '"').replace('\\n', '\n') for t in texts]
            return '\n\n'.join(cleaned[:8])  # 最多 8 段
    # 2) 去掉 ```json 围栏
    cleaned = re.sub(r'```(?:json)?\s*', '', raw)
    cleaned = re.sub(r'\s*```', '', cleaned)
    # 3) 尝试提取看起来像中文段落的连续文本
    blocks = re.findall(
        r'([一-鿿][一-鿿\w\d，。、；：“”‘’！？…—\s]{30,})',
        cleaned,
    )
    if blocks:
        return '\n\n'.join(blocks[:5])
    return None


def _strip_phantom_figrefs(text: str) -> tuple[str, list[str]]:
    """剥掉正文中的幻觉图引用(无图场景)，返回(stripped_text, detected_refs)。
    处理格式: 图N、(图N)、(如图N所示)、(图N…)、（图N）、如图N所示 等。
    """
    import re as _re
    found: list[str] = []
    patterns = [
        _re.compile(r'[（(]\s*图\d+\s*[）)]'),
        _re.compile(r'[（(]\s*如图\s*\d+\s*所示[）)]'),
        _re.compile(r'如图\s*\d+\s*所示'),
        _re.compile(r'图\d+'),
    ]
    cleaned = text
    for pat in patterns:
        matches = pat.findall(cleaned)
        found.extend(matches)
        cleaned = pat.sub('', cleaned)
    # 清理多余空白
    cleaned = _re.sub(r'[（(]\s*[）)]', '', cleaned)
    cleaned = _re.sub(r'\n{3,}', '\n\n', cleaned)
    return cleaned.strip(), found


def _extract_json(text: str) -> str:
    """从 AI 回复中提取第一个 JSON 块（去除 ```json 包裹等）。

    策略：
    1. 优先取 markdown 代码块 (```json … ```)
    2. 用平衡括号匹配提取第一个完整 JSON 对象 (非贪婪，防跨块过捕获)
    3. 降级为裸文本
    """
    # 1) markdown code fence
    m = re.search(r'```(?:json)?\s*([\s\S]*?)\s*```', text)
    if m:
        return m.group(1).strip()

    # 2) balanced brace extraction (非贪婪匹配第一个完整 {…})
    start = text.find('{')
    if start >= 0:
        depth = 0
        for i in range(start, len(text)):
            ch = text[i]
            if ch == '{':
                depth += 1
            elif ch == '}':
                depth -= 1
                if depth == 0:
                    return text[start:i + 1].strip()

    # 3) fallback: raw text
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
    context, _ = _build_context(config)

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
    tools_config: Optional[dict] = None,  # deprecated: 图由代码生成
    progress_callback: Callable[[dict], None] | None = None,
    cancel_check: Callable[[], bool] | None = None,
    charts_context: str = "",
) -> dict:
    """基于大纲分块生成结构化报告。

    逐章调用 AI，每章生成 markdown 段落（不再用 JSON Schema）。
    图由代码从真实 provider 数据生成，LLM 只按「图N」引用。

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

    project_context, report_warnings = _build_context(config)

    # 空诊断 → 记录供产物品展示
    diag_rec = config.get('_diagnosis_record')
    diagnosis_loaded = bool(diag_rec)

    if report_type == 'word':
        result = _generate_word_markdown_report(
            report_title, sections, project_context,
            generate_fn, charts_context,
            progress_callback=progress_callback,
            cancel_check=cancel_check,
        )
    else:
        result = _generate_ppt_report(report_title, sections, project_context,
                                       generate_fn, tools_config)

    # 合并: _build_context 的 warns + 段降级的 warns (from _generate_word_report)
    gen_warnings = result.pop('_report_warnings', [])
    result['_report_warnings'] = report_warnings + gen_warnings
    result['_diagnosis_loaded'] = diagnosis_loaded
    return result


def _generate_word_markdown_report(
    title: str,
    sections: List[Dict[str, Any]],
    project_context: str,
    generate_fn: Callable[..., str],
    charts_context: str = "",
    progress_callback: Callable[[dict], None] | None = None,
    cancel_check: Callable[[], bool] | None = None,
) -> dict:
    """逐章生成 Word 报告 — markdown 段落，无 JSON，无工具调用。

    每节：generate_fn(prompt) → markdown 文本 → WordSection(单段)。
    图由代码从真实 provider 生成，LLM 只按「图N」引用。
    空响应 → 重试 1 次 → 仍空 → 干净占位 + 警告。
    """
    import re as _re2
    import logging as _logging
    _log = _logging.getLogger(__name__)
    word_sections: List[WordSection] = []
    degradation_warnings: list[str] = []

    has_charts = bool(charts_context and charts_context.strip()
                     and charts_context != '(无图表)')
    chart_instruction = (
        _CHART_INSTRUCTION_HAS.format(charts_context=charts_context)
        if has_charts
        else _CHART_INSTRUCTION_NONE
    )

    total = len(sections)
    for i, sec in enumerate(sections):
        if cancel_check and cancel_check():
            degradation_warnings.append(f"生成已被取消（已完成 {i}/{total} 节）")
            break

        heading = sec['heading']
        if progress_callback:
            progress_callback({
                'stage': 'section', 'current': i + 1,
                'total': total, 'heading': heading,
            })
        key_points = sec.get('key_points', [])

        prompt = SECTION_PROMPT_WORD.format(
            report_title=title,
            section_heading=heading,
            key_points='\n'.join(f'- {p}' for p in key_points) if key_points else '（无明确论点）',
            project_context=project_context,
            charts_context=charts_context if charts_context else '',
            chart_instruction=chart_instruction,
        )

        raw = None
        for attempt in range(2):
            try:
                raw = generate_fn(prompt)
                if raw and raw.strip():
                    break
            except AIClientError as e_raw:
                if attempt == 0:
                    _log.warning("Section '%s' AI retry (attempt %d): %s", heading, attempt, e_raw)
                    continue
                degradation_warnings.append(
                    f"第{i+1}节「{heading}」AI 生成失败（{type(e_raw).__name__}）— 已降级为占位"
                )
                raw = f"（本节生成异常，已降级）"
                break

        if not raw or not raw.strip():
            degradation_warnings.append(
                f"第{i+1}节「{heading}」AI 返回空内容 — 已降级为占位"
            )
            raw = "（本节生成异常，已降级）"

        # 无图兜底: 代码剥掉 LLM 偏要输出的 图N 引用 + 记警告
        if not has_charts and raw:
            stripped, phantom = _strip_phantom_figrefs(raw)
            if phantom:
                degradation_warnings.append(
                    f"第{i+1}节「{heading}」正文含幻觉图引用(已剥): "
                    + ", ".join(phantom[:5])
                )
            raw = stripped

        section = WordSection(
            heading=heading,
            paragraphs=[raw.strip()],
        )
        word_sections.append(section)

    report = WordReport(title=title, sections=word_sections)
    result = report.to_builder_dict()
    if degradation_warnings:
        result.setdefault('_report_warnings', []).extend(degradation_warnings)
    return result


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
            if not ai.is_available():
                raise AIClientNotConfiguredError(
                    "AI 模型未配置，无法生成含图表的完整报告。"
                    "请先在「AI 模型配置」中完成配置并连接。"
                ) from None
            messages = [
                {'role': 'system', 'content': SECTION_TOOLS_SYSTEM_PROMPT},
                {'role': 'user', 'content': prompt},
            ]
            raw = ai.generate_with_tools(
                messages,
                tools_config['definitions'],
                tools_config['executable_map'],
                enable_thinking=False,  # 报告路径走非思考模式
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
