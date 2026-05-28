"""Report flow controller — Human-in-the-loop 多智能体装配线。

流程: 生成大纲 → 用户确认 → 逐步扩写每章 → 渲染输出
严禁 AI 直接操作 docx/pptx；AI 只输出 JSON，渲染器只消费 JSON。
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Callable, Optional

from .models import (
    GenerationEvent,
    OutlineSection,
    PPTReport,
    PPTSlide,
    ReportOutline,
    WordReport,
    WordSection,
    WordTable,
)
from .word_builder import WordBuilder
from .ppt_builder import PPTBuilder


# ═══════════════════════════════════════════════════════════════════
# Prompt 模板
# ═══════════════════════════════════════════════════════════════════

OUTLINE_PROMPT_WORD = """你是一个专业的工程技术报告撰写专家。请根据以下需求文件，生成一份深度技术报告的**大纲**。

要求：
- 输出 JSON 格式：{"title": "报告标题", "sections": [{"heading": "章节标题", "key_points": ["论点1", "论点2"]}]}
- 章节数量: 5-8 章
- 每个章节 3-5 个关键论点
- 逻辑清晰，覆盖技术背景、方法、数据分析和结论

需求文件内容:
{requirements}

请只输出 JSON，不要添加额外文字。"""

OUTLINE_PROMPT_PPT = """你是一个专业的工程技术汇报演示专家。请根据以下需求文件，生成一份 PPT 汇报的**大纲**。

要求：
- 输出 JSON 格式：{"title": "演示标题", "sections": [{"heading": "幻灯片标题", "key_points": ["要点1", "要点2"]}]}
- 幻灯片数量: 8-12 页
- 每页 2-4 个核心要点
- 遵循"空间极简原则"：每页只讲一件事
- 逻辑: 背景 → 方法 → 关键发现 → 结论

需求文件内容:
{requirements}

请只输出 JSON，不要添加额外文字。"""

EXPAND_WORD_SECTION_PROMPT = """你是一个专业工程技术报告撰写专家。请为以下章节撰写完整内容。

报告标题: {title}
当前章节: {heading}

大纲关键论点:
{key_points}

{context_sections}

要求：
1. 输出 JSON 格式，见下方 Schema
2. 正文段落详细论述，保留所有技术细节
3. 如有需要，使用 [INSERT_IMAGE: 描述.png] 标记图表插入位置
4. 如有表格数据，填入 tables 字段

JSON Schema:
{{
  "heading": "章节标题",
  "content_paragraphs": ["段落1", "段落2", ...],
  "image_anchors": ["[INSERT_IMAGE: xxx.png]"],
  "tables": [{{"caption": "表x-x: 标题", "headers": ["列1", "列2"], "rows": [["值1", "值2"]]}}]
}}

请只输出 JSON，不要添加额外文字。"""

EXPAND_PPT_SLIDE_PROMPT = """你是一个专业的 PPT 汇报演示专家。请为以下幻灯片撰写内容。

报告标题: {title}
幻灯片标题: {heading}

核心要点:
{key_points}

{context_sections}

**空间极简原则**：
- 每页最多 4 个 bullet points
- 每条 bullet ≤ 20 字，提炼核心观点
- 具体数据、公式、论证细节全部放入 speaker_notes

JSON Schema:
{{
  "slide_title": "标题",
  "bullet_points": ["要点1", "要点2", ...],
  "speaker_notes": "详细演讲备注，包含具体数据和论证",
  "image_anchor": "[INSERT_IMAGE: xxx.png]" 或 null
}}

请只输出 JSON，不要添加额外文字。"""


# ═══════════════════════════════════════════════════════════════════
# 控制器
# ═══════════════════════════════════════════════════════════════════

class ReportFlowController:
    """报告生成流程控制器 — Human-in-the-loop 架构。"""

    def __init__(self):
        self.word_builder = WordBuilder()
        self.ppt_builder = PPTBuilder()
        self._section_cache: dict[int, object] = {}

    # ═══════════════════════════════════════════════════════════════
    # Step 1: 生成大纲 (10 秒级)
    # ═══════════════════════════════════════════════════════════════

    def generate_outline(
        self,
        generate_fn: Callable[[str], str],
        requirements: str,
        report_type: str = "word",
        title_hint: str = "",
    ) -> tuple[ReportOutline, str]:
        """AI 生成报告大纲。

        Args:
            generate_fn: LLM 调用函数 (prompt) -> response_text
            requirements: 需求文件 / 项目资料文本
            report_type: "word" 或 "ppt"
            title_hint: 可选的标题提示

        Returns:
            (ReportOutline, raw_response) 元组
        """
        template = OUTLINE_PROMPT_WORD if report_type == "word" else OUTLINE_PROMPT_PPT
        prompt = template.format(requirements=requirements[:6000])
        if title_hint:
            prompt += f"\n\n建议标题: {title_hint}"

        raw = generate_fn(prompt)
        outline = self._parse_outline(raw, report_type, title_hint)
        return outline, raw

    def _parse_outline(self, raw: str, report_type: str, title_hint: str = "") -> ReportOutline:
        """解析 LLM 返回的大纲 JSON，带容错处理。"""
        try:
            # 尝试提取 JSON 块
            json_match = None
            for pattern in [r'```json\s*([\s\S]*?)\s*```', r'\{[\s\S]*\}']:
                import re
                m = re.search(pattern, raw)
                if m:
                    json_match = m.group(1) if '```' in pattern else m.group(0)
                    break

            if json_match:
                data = json.loads(json_match)
            else:
                data = json.loads(raw)

            sections = [
                OutlineSection(
                    heading=s.get("heading", s.get("title", "")),
                    key_points=s.get("key_points", s.get("points", [])),
                )
                for s in data.get("sections", [])
            ]
            return ReportOutline(
                title=data.get("title", title_hint or "Untitled"),
                report_type=report_type,
                sections=sections,
            )
        except (json.JSONDecodeError, KeyError) as e:
            print(f"[FlowController] 大纲解析失败: {e}，使用降级方案")
            # 降级：按双换行分段
            lines = [l.strip() for l in raw.split("\n") if l.strip() and not l.startswith("```")]
            return ReportOutline(
                title=title_hint or "Report Outline",
                report_type=report_type,
                sections=[OutlineSection(heading=l[:80], key_points=[]) for l in lines[:10]],
            )

    # ═══════════════════════════════════════════════════════════════
    # Step 2: 分步扩写 (每章单独调用 LLM)
    # ═══════════════════════════════════════════════════════════════

    def expand_section(
        self,
        generate_fn: Callable[[str], str],
        outline: ReportOutline,
        section_index: int,
        project_context: str = "",
        previous_sections: list[object] | None = None,
    ) -> tuple[object, dict]:
        """扩写大纲中的单个章节。

        Args:
            generate_fn: LLM 调用函数
            outline: 已确认的大纲
            section_index: 章节索引 (0-based)
            project_context: 项目资料全文 (供 LLM 检索引用)
            previous_sections: 前面已生成的章节（提供上下文连贯性）

        Returns:
            (WordSection 或 PPTSlide, raw_json_dict)
        """
        section = outline.sections[section_index]
        is_word = outline.report_type == "word"

        # 构建上下文
        context = ""
        if previous_sections:
            prev_headings = [
                (s.heading if hasattr(s, 'heading') else s.slide_title)
                for s in previous_sections[-2:]
            ]
            context = "前面章节: " + " → ".join(prev_headings) + "\n"
        if project_context:
            context += f"参考数据:\n{project_context[:2000]}\n"

        template = EXPAND_WORD_SECTION_PROMPT if is_word else EXPAND_PPT_SLIDE_PROMPT
        prompt = template.format(
            title=outline.title,
            heading=section.heading,
            key_points="\n".join(f"- {p}" for p in section.key_points),
            context_sections=context,
        )

        raw = generate_fn(prompt)

        # 解析 JSON
        try:
            import re
            m = re.search(r'```json\s*([\s\S]*?)\s*```', raw)
            data = json.loads(m.group(1) if m else raw)
        except (json.JSONDecodeError, AttributeError):
            # 降级：用原文构造基本结构
            data = {"heading": section.heading, "content_paragraphs": [raw], "image_anchors": []}

        if is_word:
            result = WordSection(
                heading=data.get("heading", section.heading),
                content_paragraphs=data.get("content_paragraphs", []),
                image_anchors=data.get("image_anchors", []),
                tables=[WordTable.from_dict(t) for t in data.get("tables", [])],
            )
            self._section_cache[section_index] = result
        else:
            result = PPTSlide(
                slide_title=data.get("slide_title", section.heading),
                bullet_points=data.get("bullet_points", [])[:4],
                speaker_notes=data.get("speaker_notes", ""),
                image_anchor=data.get("image_anchor"),
            )
            self._section_cache[section_index] = result

        return result, data

    # ═══════════════════════════════════════════════════════════════
    # Step 3: 组装完整报告
    # ═══════════════════════════════════════════════════════════════

    def build_word_report(self, outline: ReportOutline, author: str = "") -> WordReport:
        """从缓存中的章节组装 WordReport。"""
        sections = []
        for i in range(len(outline.sections)):
            cached = self._section_cache.get(i)
            if isinstance(cached, WordSection):
                sections.append(cached)
        return WordReport(
            title=outline.title,
            author=author or "DataProcessor Pro",
            date=datetime.now().strftime("%Y-%m-%d"),
            sections=sections,
        )

    def build_ppt_report(self, outline: ReportOutline, author: str = "") -> PPTReport:
        """从缓存中的幻灯片组装 PPTReport。"""
        slides = []
        for i in range(len(outline.sections)):
            cached = self._section_cache.get(i)
            if isinstance(cached, PPTSlide):
                slides.append(cached)
        return PPTReport(
            title=outline.title,
            author=author or "DataProcessor Pro",
            date=datetime.now().strftime("%Y-%m-%d"),
            slides=slides,
        )

    # ═══════════════════════════════════════════════════════════════
    # 全流程: Outline → Expand → Render
    # ═══════════════════════════════════════════════════════════════

    def run_full_flow(
        self,
        generate_fn: Callable[[str], str],
        requirements: str,
        report_type: str = "word",
        author: str = "",
        title_hint: str = "",
        project_context: str = "",
        progress_callback: Callable[[GenerationEvent], None] | None = None,
    ) -> bytes:
        """执行完整报告生成流程。

        1. 生成大纲
        2. 逐章节扩写
        3. 渲染输出

        Args:
            generate_fn: LLM 调用函数
            requirements: 需求文件文本
            report_type: "word" 或 "ppt"
            author: 报告作者
            title_hint: 标题提示
            project_context: 项目资料数据
            progress_callback: 进度回调

        Returns:
            报告文件 (.docx 或 .pptx) 的二进制内容
        """
        self._section_cache.clear()
        self._emit(progress_callback, GenerationEvent("outline", "正在生成大纲...", 0, 0))

        outline, _ = self.generate_outline(generate_fn, requirements, report_type, title_hint)
        n = len(outline.sections)
        self._emit(progress_callback, GenerationEvent("outline", f"大纲生成完成，共 {n} 个章节", n, n))

        expanded = []
        for i, section in enumerate(outline.sections):
            self._emit(progress_callback, GenerationEvent(
                "section", f"正在撰写第 {i+1}/{n} 节: {section.heading}...", i + 1, n,
            ))
            result, _ = self.expand_section(
                generate_fn, outline, i, project_context, expanded,
            )
            expanded.append(result)
            self._emit(progress_callback, GenerationEvent(
                "section", f"第 {i+1}/{n} 节完成: {section.heading}", i + 1, n,
            ))

        self._emit(progress_callback, GenerationEvent("render", "正在渲染报告文档...", n, n))

        if report_type == "word":
            report = self.build_word_report(outline, author)
            doc_bytes = self.word_builder.build(report)
        else:
            report = self.build_ppt_report(outline, author)
            doc_bytes = self.ppt_builder.build(report)

        self._emit(progress_callback, GenerationEvent("done", "报告生成完毕！", n, n))
        return doc_bytes

    def _emit(self, cb, event: GenerationEvent):
        if cb:
            try:
                cb(event)
            except Exception:
                pass
