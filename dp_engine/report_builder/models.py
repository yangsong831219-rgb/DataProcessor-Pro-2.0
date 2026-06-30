"""Report data models — 结构化 JSON 桥接层.

AI 只输出这些模型的 JSON，渲染器只消费这些 JSON。
双方通过 Schema 契约解耦，严禁 AI 直接操作 docx/pptx 二进制文件。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal, Optional


# ═══════════════════════════════════════════════════════════════════
# 大纲层 — 人机确认用 (Human-in-the-loop)
# ═══════════════════════════════════════════════════════════════════

@dataclass
class OutlineSection:
    """大纲中的一个章节 — 用户审核的最小单元."""
    heading: str                            # "第三章 温度补偿分析"
    key_points: list[str] = field(default_factory=list)  # 核心论点列表


@dataclass
class ReportOutline:
    """报告大纲 — AI 生成后展示给用户确认/修改."""
    title: str
    report_type: Literal["word", "ppt"] = "word"
    sections: list[OutlineSection] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "title": self.title,
            "report_type": self.report_type,
            "sections": [{"heading": s.heading, "key_points": s.key_points}
                         for s in self.sections],
        }

    @classmethod
    def from_dict(cls, d: dict) -> "ReportOutline":
        return cls(
            title=d.get("title", ""),
            report_type=d.get("report_type", "word"),
            sections=[OutlineSection(**s) for s in d.get("sections", [])],
        )

    def to_markdown(self) -> str:
        """渲染为用户可读的大纲文本."""
        lines = [f"# 报告大纲: {self.title}\n"]
        for i, s in enumerate(self.sections, 1):
            lines.append(f"## {i}. {s.heading}")
            for pt in s.key_points:
                lines.append(f"- {pt}")
            lines.append("")
        return "\n".join(lines)


# ═══════════════════════════════════════════════════════════════════
# Word 渲染模型 — 长文本论述 + 图表锚点
# ═══════════════════════════════════════════════════════════════════

@dataclass
class WordTable:
    """Word 报告中的表格."""
    caption: str                            # "表3-1: 各传感器应变峰值对比"
    headers: list[str] = field(default_factory=list)
    rows: list[list[str | float]] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {"caption": self.caption, "headers": self.headers,
                "rows": self.rows}

    @classmethod
    def from_dict(cls, d: dict) -> "WordTable":
        return cls(caption=d.get("caption", ""), headers=d.get("headers", []),
                   rows=d.get("rows", []))


@dataclass
class WordSection:
    """Word 报告的一个章节."""
    heading: str                            # "3.1 温度补偿原理"
    content_paragraphs: list[str] = field(default_factory=list)  # 正文段落
    image_anchors: list[str] = field(default_factory=list)       # ["[INSERT_IMAGE: chart.png]", ...]
    tables: list[WordTable] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "heading": self.heading,
            "content_paragraphs": self.content_paragraphs,
            "image_anchors": self.image_anchors,
            "tables": [t.to_dict() for t in self.tables],
        }

    @classmethod
    def from_dict(cls, d: dict) -> "WordSection":
        return cls(
            heading=d.get("heading", ""),
            content_paragraphs=d.get("content_paragraphs", []),
            image_anchors=d.get("image_anchors", []),
            tables=[WordTable.from_dict(t) for t in d.get("tables", [])],
        )


@dataclass
class WordReport:
    """Word 报告完整结构 — word_builder.py 的唯一输入."""
    title: str
    author: str = "DataProcessor Pro"
    date: str = ""
    sections: list[WordSection] = field(default_factory=list)
    template_path: Optional[str] = None  # 可选 .docx 模板

    def to_dict(self) -> dict:
        return {
            "title": self.title,
            "author": self.author,
            "date": self.date,
            "template_path": self.template_path,
            "sections": [s.to_dict() for s in self.sections],
        }

    @classmethod
    def from_dict(cls, d: dict) -> "WordReport":
        return cls(
            title=d.get("title", ""),
            author=d.get("author", "DataProcessor Pro"),
            date=d.get("date", ""),
            template_path=d.get("template_path"),
            sections=[WordSection.from_dict(s) for s in d.get("sections", [])],
        )


# ═══════════════════════════════════════════════════════════════════
# PPT 渲染模型 — 空间极简原则
# ═══════════════════════════════════════════════════════════════════

@dataclass
class PPTSlide:
    """PPT 中的一页幻灯片."""
    slide_title: str                        # "应变监测结论"
    bullet_points: list[str] = field(default_factory=list)  # ≤4条，每条≤20字
    speaker_notes: str = ""                 # 演讲备注 (放具体数据)
    image_anchor: Optional[str] = None      # 最多一张图

    def to_dict(self) -> dict:
        return {
            "slide_title": self.slide_title,
            "bullet_points": self.bullet_points,
            "speaker_notes": self.speaker_notes,
            "image_anchor": self.image_anchor,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "PPTSlide":
        return cls(
            slide_title=d.get("slide_title", ""),
            bullet_points=d.get("bullet_points", []),
            speaker_notes=d.get("speaker_notes", ""),
            image_anchor=d.get("image_anchor"),
        )


@dataclass
class PPTReport:
    """PPT 报告完整结构 — ppt_builder.py 的唯一输入."""
    title: str
    author: str = "DataProcessor Pro"
    date: str = ""
    slides: list[PPTSlide] = field(default_factory=list)
    template_path: Optional[str] = None

    def to_dict(self) -> dict:
        return {
            "title": self.title,
            "author": self.author,
            "date": self.date,
            "template_path": self.template_path,
            "slides": [s.to_dict() for s in self.slides],
        }

    @classmethod
    def from_dict(cls, d: dict) -> "PPTReport":
        return cls(
            title=d.get("title", ""),
            author=d.get("author", "DataProcessor Pro"),
            date=d.get("date", ""),
            template_path=d.get("template_path"),
            slides=[PPTSlide.from_dict(s) for s in d.get("slides", [])],
        )


# ═══════════════════════════════════════════════════════════════════
# 生成进度事件 — 用于 UI 日志终端
# ═══════════════════════════════════════════════════════════════════

@dataclass
class GenerationEvent:
    """报告生成过程中的进度事件."""
    stage: Literal["outline", "section", "render", "done", "error"]
    message: str
    section_index: Optional[int] = None
    section_total: Optional[int] = None


# ═══════════════════════════════════════════════════════════════════
# 旧版兼容 (保留原有类名，标记为 deprecated)
# ═══════════════════════════════════════════════════════════════════

@dataclass
class ContentBlock:
    """[deprecated] 使用 WordSection / PPTSlide 替代."""
    type: Literal['text', 'chart', 'table', 'code']
    data: object


@dataclass
class Section:
    """[deprecated] 使用 WordSection 替代."""
    title: str
    blocks: list[ContentBlock] = field(default_factory=list)


@dataclass
class ReportSpec:
    """[deprecated] 使用 WordReport / PPTReport 替代."""
    title: str
    author: str
    date: str
    sections: list[Section] = field(default_factory=list)


@dataclass
class FileItem:
    """Represents a file selected for report generation."""
    id: str
    name: str
    path: str
    type: Literal['requirement', 'template', 'project']


@dataclass
class ReportConfig:
    """Configuration for a report (Word or PPT)."""
    title: str
    author: str = 'DataProcessor Pro'
    date: str = ''
    requirement_files: list[FileItem] = field(default_factory=list)
    template_files: list[FileItem] = field(default_factory=list)
    project_files: list[FileItem] = field(default_factory=list)
    last_save_path: Optional[str] = None


# Allowed file extensions
ALLOWED_REQUIREMENT_EXTENSIONS = ['.txt', '.md']
ALLOWED_TEMPLATE_EXTENSIONS_WORD = ['.docx', '.doc']
ALLOWED_TEMPLATE_EXTENSIONS_PPT = ['.pptx']
ALLOWED_PROJECT_EXTENSIONS = ['.doc', '.docx', '.xlsx', '.pdf', '.png', '.jpg', '.csv', '.txt']
