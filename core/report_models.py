"""报告中间态数据契约 — Pydantic Schema 定义

大模型 ↔ JSON Schema ↔ Pydantic ↔ 渲染器 (word_builder/ppt_builder)

遵循 "Schema-driven" 原则：AI 只输出符合 Schema 的 JSON，
渲染器只消费这些 JSON，双方通过 Schema 解耦。
"""

from __future__ import annotations

from typing import List, Optional

from pydantic import BaseModel, Field


# ═══════════════════════════════════════════════════════════════════
# Word 报告模型 — 长文本深度论述
# ═══════════════════════════════════════════════════════════════════


class WordSection(BaseModel):
    """Word 报告的一个章节."""

    heading: str = Field(description='章节标题，如 "3.1 温度补偿原理"')
    paragraphs: List[str] = Field(
        default_factory=list,
        description='正文段落列表，每段为纯文本。支持 Markdown 加粗 ** ** 标记',
    )
    image_anchors: List[str] = Field(
        default_factory=list,
        description='图片锚点列表，格式如 "[INSERT_IMAGE: chart1.png]"',
    )


class WordReport(BaseModel):
    """Word 报告完整结构 — 渲染器的唯一输入."""

    title: str = Field(description='报告标题')
    author: str = Field(default='DataProcessor Pro', description='报告作者')
    date: str = Field(default='', description='报告日期')
    sections: List[WordSection] = Field(
        default_factory=list,
        description='章节列表，按顺序渲染',
    )

    def to_builder_dict(self) -> dict:
        """转换为 word_builder 兼容的字典格式."""
        return {
            'title': self.title,
            'author': self.author,
            'date': self.date,
            'template_path': None,
            'sections': [
                {
                    'heading': s.heading,
                    'content_paragraphs': s.paragraphs,
                    'image_anchors': s.image_anchors,
                    'tables': [],
                }
                for s in self.sections
            ],
        }


# ═══════════════════════════════════════════════════════════════════
# PPT 报告模型 — 空间极简原则
# ═══════════════════════════════════════════════════════════════════


class PPTSlide(BaseModel):
    """PPT 中的一页幻灯片."""

    slide_title: str = Field(description='幻灯片标题')
    bullet_points: List[str] = Field(
        default_factory=list,
        description='要点列表。空间极简：最多 4 条，每条不超过 20 字',
        max_length=4,
    )
    speaker_notes: str = Field(
        default='',
        description='演讲备注 — 存放详细数据论述，不出现在幻灯片上',
    )
    image_anchor: Optional[str] = Field(
        default=None,
        description='可选图片锚点，每页最多一张图。如 "[INSERT_IMAGE: chart1.png]"',
    )


class PPTReport(BaseModel):
    """PPT 演示文稿完整结构 — 渲染器的唯一输入."""

    title: str = Field(description='演示标题')
    author: str = Field(default='DataProcessor Pro', description='作者')
    date: str = Field(default='', description='日期')
    slides: List[PPTSlide] = Field(
        default_factory=list,
        description='幻灯片列表，按顺序渲染',
    )

    def to_builder_dict(self) -> dict:
        """转换为 ppt_builder 兼容的字典格式."""
        return {
            'title': self.title,
            'author': self.author,
            'date': self.date,
            'template_path': None,
            'slides': [
                {
                    'slide_title': s.slide_title,
                    'bullet_points': s.bullet_points,
                    'speaker_notes': s.speaker_notes,
                    'image_anchor': s.image_anchor,
                }
                for s in self.slides
            ],
        }
