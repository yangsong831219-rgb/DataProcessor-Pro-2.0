# pyright: reportGeneralTypeIssues=false
# python-docx stub: Document() 被误识为函数签名而非类构造器，运行时正确
"""Word document builder — 消费 WordReport JSON，渲染 .docx。

只读取结构化数据，严禁在此文件中调用任何 LLM。
[INSERT_IMAGE: filename] 标签自动从项目资料目录插入图片。
"""

from __future__ import annotations

import io
import os
import re
from pathlib import Path
from typing import Optional

from docx import Document
from docx.shared import Inches, Pt, RGBColor
from docx.enum.text import WD_ALIGN_PARAGRAPH

from .models import WordReport, WordSection, WordTable


# 图片插入标签格式
IMAGE_ANCHOR_RE = re.compile(r'\[INSERT_IMAGE:\s*([^\]]+)\]')

# 段落内嵌图片标签 — re.split 专用模式
IMAGE_PATTERN = r"\[INSERT_IMAGE:\s*(.+?)\]"


class WordBuilder:
    """从 WordReport 结构体构建 .docx 文档。"""

    # 默认项目资料搜索目录（相对于项目根目录）
    DEFAULT_ASSET_DIRS = ["wiki_vault/assets", "wiki_vault/pages", "project_files"]

    def __init__(self, asset_search_paths: list[str] | None = None):
        """
        Args:
            asset_search_paths: 图片搜索目录列表，默认使用 DEFAULT_ASSET_DIRS
        """
        self.asset_paths = asset_search_paths or self.DEFAULT_ASSET_DIRS
        self._project_root = Path(__file__).parent.parent.parent

    # ── 主入口 ──

    def build(self, report: WordReport) -> bytes:
        """从 WordReport 构建 .docx 文档。

        Args:
            report: WordReport 结构化数据

        Returns:
            .docx 文件二进制内容
        """
        if report.template_path and Path(report.template_path).exists():
            doc = self._load_template(report.template_path)
        else:
            doc = Document()

        self._add_title_page(doc, report)
        for section in report.sections:
            self._add_section(doc, section)

        buffer = io.BytesIO()
        doc.save(buffer)
        return buffer.getvalue()

    def build_from_json(self, json_data: dict) -> bytes:
        """从 JSON 字典构建文档（便捷入口）。"""
        report = WordReport.from_dict(json_data)
        return self.build(report)

    # ── 模板加载 ──

    def _load_template(self, template_path: str) -> Document:
        """加载模板文件，做占位符替换。"""
        doc = Document(template_path)
        return doc

    # ── 标题页 ──

    def _add_title_page(self, doc: Document, report: WordReport) -> None:
        title = doc.add_heading(report.title, 0)
        title.alignment = WD_ALIGN_PARAGRAPH.CENTER

        if report.author:
            p = doc.add_paragraph(f'Author: {report.author}')
            p.alignment = WD_ALIGN_PARAGRAPH.CENTER
        if report.date:
            p = doc.add_paragraph(f'Date: {report.date}')
            p.alignment = WD_ALIGN_PARAGRAPH.CENTER

        doc.add_paragraph()

    # ── 章节渲染 ──

    def _add_section(self, doc: Document, section: WordSection, project_dir: str = '') -> None:
        """渲染一个章节：标题 + 段落（图文混排）+ 图片 + 表格。"""
        doc.add_heading(section.heading, level=1)

        for para_text in section.content_paragraphs:
            self._add_paragraph_with_images(doc, para_text, project_dir=project_dir)

        # 显式声明的图片锚点
        for anchor in section.image_anchors:
            filename = self._extract_image_filename(anchor)
            if filename:
                self._insert_image(doc, filename, project_dir=project_dir)

        for table in section.tables:
            self._add_table(doc, table)

    # ── 段落 + 内联图片 ──

    def _add_paragraph_with_images(self, doc: Document, text: str, project_dir: str = '') -> None:
        """处理段落中可能嵌入的 [INSERT_IMAGE: ...] 标签。

        优先通过 project_dir 拼接图片绝对路径；
        文件缺失时以红色粗体占位文本提醒，绝不崩溃。
        """
        if not re.search(IMAGE_PATTERN, text):
            # 无图片标签，直接写段落
            doc.add_paragraph(text)
            return

        # 有图片标签：re.split 逐段处理
        parts = re.split(IMAGE_PATTERN, text)
        para = doc.add_paragraph()
        for i, part in enumerate(parts):
            if i % 2 == 1:
                # 奇数索引 = 图片文件名
                filename = part.strip()
                img_path = os.path.join(project_dir, filename) if project_dir else ''
                if img_path and os.path.isfile(img_path):
                    try:
                        run = para.add_run()
                        run.add_picture(img_path, width=Inches(5.0))
                    except Exception:
                        run = para.add_run(f'[图片插入失败: {filename}]')
                        run.font.color.rgb = RGBColor(0xFF, 0x00, 0x00)
                        run.font.bold = True
                else:
                    run = para.add_run(f'[图表文件丢失: {filename}]')
                    run.font.color.rgb = RGBColor(0xFF, 0x00, 0x00)
                    run.font.bold = True
            elif part.strip():
                para.add_run(part.strip())

        if not para.runs:
            p = para._element
            p.getparent().remove(p)

    def _extract_image_filename(self, text: str) -> str | None:
        m = IMAGE_ANCHOR_RE.search(text)
        return m.group(1).strip() if m else None

    # ── 图片插入 ──

    def _insert_image(self, doc: Document, filename: str, project_dir: str = '') -> bool:
        """在文档末尾插入一张图片。

        优先使用 project_dir 拼接，降级到 _resolve_image_path 搜索。
        """
        img_path = None
        if project_dir:
            candidate = os.path.join(project_dir, filename)
            if os.path.isfile(candidate):
                img_path = candidate
        if not img_path:
            img_path = self._resolve_image_path(filename)
        if img_path:
            try:
                doc.add_picture(str(img_path), width=Inches(5.5))
                last_para = doc.paragraphs[-1] if doc.paragraphs else None
                if last_para:
                    last_para.alignment = WD_ALIGN_PARAGRAPH.CENTER
                return True
            except Exception as e:
                p = doc.add_paragraph(f'[图片加载失败: {filename} — {e}]')
                for r in p.runs:
                    r.font.color.rgb = RGBColor(0xFF, 0x00, 0x00)
        else:
            p = doc.add_paragraph(f'[图片未找到: {filename}]')
            for r in p.runs:
                r.font.color.rgb = RGBColor(0xFF, 0x00, 0x00)
        return False

    def _insert_image_inline(self, para, filename: str) -> bool:
        """在段落内 Run 中插入图片。"""
        img_path = self._resolve_image_path(filename)
        if img_path:
            try:
                run = para.add_run()
                run.add_picture(str(img_path), width=Inches(5.0))
                return True
            except Exception:
                run = para.add_run(f'[图片待插入: {filename}]')
                run.font.color.rgb = RGBColor(0xFF, 0x00, 0x00)
                run.font.bold = True
        else:
            run = para.add_run(f'[图片待插入: {filename}]')
            run.font.color.rgb = RGBColor(0xFF, 0x00, 0x00)
            run.font.bold = True
        return False

    def _resolve_image_path(self, filename: str) -> Path | None:
        """在项目资料目录中搜索图片文件。"""
        # 绝对路径
        p = Path(filename)
        if p.is_absolute() and p.exists():
            return p

        # 搜索已知的 assets 目录
        for rel_dir in self.asset_paths:
            search_dir = self._project_root / rel_dir
            if search_dir.exists():
                for ext in ('', '.png', '.jpg', '.jpeg', '.gif', '.bmp'):
                    candidate = search_dir / f"{filename}{ext}" if not filename.endswith(ext) else search_dir / filename
                    if candidate.exists():
                        return candidate
                # 递归搜索一层
                for child in search_dir.iterdir():
                    if child.is_dir():
                        for ext in ('', '.png', '.jpg', '.jpeg', '.gif', '.bmp'):
                            c = child / f"{filename}{ext}" if not filename.endswith(ext) else child / filename
                            if c.exists():
                                return c
        return None

    # ── 表格渲染 ──

    def _add_table(self, doc: Document, table: WordTable) -> None:
        """渲染 WordTable 为 docx 表格。"""
        if table.caption:
            p = doc.add_paragraph(table.caption)
            p.runs[0].bold = True if p.runs else None

        if not table.rows:
            return

        ncols = len(table.headers) if table.headers else max(len(r) for r in table.rows)
        nrows = len(table.rows) + (1 if table.headers else 0)

        tbl = doc.add_table(rows=nrows, cols=ncols, style='Table Grid')

        # 表头
        if table.headers:
            for j, hdr in enumerate(table.headers):
                cell = tbl.cell(0, j)
                cell.text = str(hdr)
                for p in cell.paragraphs:
                    for run in p.runs:
                        run.bold = True

        # 数据行
        for i, row in enumerate(table.rows):
            for j, val in enumerate(row):
                if j < ncols:
                    tbl.cell(i + (1 if table.headers else 0), j).text = str(val) if val is not None else ''

        doc.add_paragraph()  # 表后间距

    # ── 文件级入口（Task 2: 模板占位符 + 图片锚点红色提示） ──

    PLACEHOLDER_RE = re.compile(r'\{\{(\w+)\}\}')

    def build_word_report(
        self,
        report_data: WordReport,
        template_path: str,
        output_path: str,
        project_dir: str = '',
    ) -> str:
        """从 WordReport 加载模板并渲染到文件.

        Args:
            report_data: WordReport 结构化数据
            template_path: .docx 模板路径
            output_path: 输出 .docx 路径
            project_dir: 项目根目录（用于拼接图片绝对路径）

        Returns:
            output_path (便于链式调用)
        """
        # 1. 加载模板
        doc = Document(template_path)

        # 2. 模板占位符替换
        self._replace_placeholders(doc, report_data)

        # 3. 标题页
        self._add_title_page(doc, report_data)

        # 4. 章节渲染
        for section in report_data.sections:
            self._add_section(doc, section, project_dir=project_dir)

        # 5. 保存
        doc.save(output_path)
        return output_path

    def _replace_placeholders(self, doc: Document, report: WordReport) -> None:
        """遍历模板中所有段落，替换 {{Placeholder}} 占位符."""
        mapping = {
            'Report_Title': report.title,
            'Report_Author': report.author,
            'Report_Date': report.date,
        }
        for para in doc.paragraphs:
            for run in para.runs:
                original = run.text
                if '{{' not in original:
                    continue
                replaced = self.PLACEHOLDER_RE.sub(
                    lambda m: mapping.get(m.group(1), m.group(0)),
                    original,
                )
                if replaced != original:
                    run.text = replaced

    # ── 向后兼容 ──

    def build_legacy(self, spec) -> bytes:
        """从旧版 ReportSpec 构建文档（向后兼容）。"""
        from .models import ReportSpec
        if not isinstance(spec, ReportSpec):
            raise TypeError("Expected ReportSpec")

        report = WordReport(
            title=spec.title,
            author=spec.author,
            date=spec.date,
            sections=[
                WordSection(
                    heading=s.title,
                    content_paragraphs=[
                        str(b.data) for b in s.blocks if b.type == 'text'
                    ],
                )
                for s in spec.sections
            ],
        )
        return self.build(report)
