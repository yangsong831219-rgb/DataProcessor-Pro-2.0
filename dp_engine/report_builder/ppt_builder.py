# pyright: reportGeneralTypeIssues=false
# python-pptx stub: Presentation() 被误识为函数签名而非类构造器，运行时正确
"""PowerPoint builder — 消费 PPTReport JSON，渲染 .pptx。

空间极简原则：每页 ≤4 bullet points，具体数据放入 Speaker Notes。
[INSERT_IMAGE: filename] 标签自动插入图片。
"""

from __future__ import annotations

import io
import os
import re
from pathlib import Path
from typing import Optional

from pptx import Presentation
from pptx.util import Inches, Pt, Emu
from pptx.enum.text import PP_ALIGN
from pptx.dml.color import RGBColor

from .models import PPTReport, PPTSlide


IMAGE_ANCHOR_RE = re.compile(r'\[INSERT_IMAGE:\s*([^\]]+)\]')

# 段落内嵌图片标签 — re.split / re.sub 专用
IMAGE_PATTERN = r"\[INSERT_IMAGE:\s*(.+?)\]"

# 每页 Bullet 数量硬限制
MAX_BULLETS_PER_SLIDE = 4


class PPTBuilder:
    """从 PPTReport 结构体构建 .pptx 演示文稿。"""

    DEFAULT_ASSET_DIRS = ["wiki_vault/assets", "wiki_vault/pages", "project_files"]

    def __init__(self, asset_search_paths: list[str] | None = None):
        self.asset_paths = asset_search_paths or self.DEFAULT_ASSET_DIRS
        self._project_root = Path(__file__).parent.parent.parent

    # ── 主入口 ──

    def build(self, report: PPTReport) -> bytes:
        """从 PPTReport 构建 .pptx 文件。

        Args:
            report: PPTReport 结构化数据

        Returns:
            .pptx 文件二进制内容
        """
        if report.template_path and Path(report.template_path).exists():
            prs = Presentation(report.template_path)
        else:
            prs = Presentation()
            # 默认 16:9
            prs.slide_width = Inches(13.333)
            prs.slide_height = Inches(7.5)

        self._add_title_slide(prs, report)

        for slide_data in report.slides:
            self._add_content_slide(prs, slide_data)

        buffer = io.BytesIO()
        prs.save(buffer)
        return buffer.getvalue()

    def build_from_json(self, json_data: dict) -> bytes:
        """从 JSON 字典构建演示文稿（便捷入口）。"""
        report = PPTReport.from_dict(json_data)
        return self.build(report)

    # ── 标题页 ──

    def _add_title_slide(self, prs: Presentation, report: PPTReport) -> None:
        slide = prs.slides.add_slide(prs.slide_layouts[6])

        # 标题
        tb = slide.shapes.add_textbox(Inches(1.5), Inches(2.5), Inches(10), Inches(1.5))
        tf = tb.text_frame
        tf.word_wrap = True
        p = tf.paragraphs[0]
        p.text = report.title
        p.font.size = Pt(40)
        p.font.bold = True
        p.alignment = PP_ALIGN.CENTER

        # 副标题 (作者 + 日期)
        sub = slide.shapes.add_textbox(Inches(1.5), Inches(4.2), Inches(10), Inches(1))
        stf = sub.text_frame
        stf.word_wrap = True
        sp = stf.paragraphs[0]
        parts = []
        if report.author:
            parts.append(report.author)
        if report.date:
            parts.append(report.date)
        sp.text = '  |  '.join(parts)
        sp.font.size = Pt(20)
        sp.font.color.rgb = RGBColor(0x66, 0x66, 0x66)
        sp.alignment = PP_ALIGN.CENTER

    # ── 内容页 ──

    def _get_content_layout(self, prs: Presentation):
        """安全获取最佳正文版式.

        优先模板中预设的版式 1 或 2（通常为标题+内容），
        降级到空白版式，确保不因缺少版式崩溃.
        """
        layouts = prs.slide_layouts
        for idx in (1, 2, 0, len(layouts) - 1):
            try:
                candidate = layouts[idx]
                if candidate is not None:
                    return candidate
            except (IndexError, AttributeError):
                continue
        return None

    def _add_content_slide(self, prs: Presentation, slide_data: PPTSlide, project_dir: str = '') -> None:
        layout = self._get_content_layout(prs)
        slide = prs.slides.add_slide(layout) if layout else prs.slides.add_slide(prs.slide_layouts[0])

        # --- 标题 ---
        tb = slide.shapes.add_textbox(Inches(0.8), Inches(0.4), Inches(11.5), Inches(0.9))
        tf = tb.text_frame
        tf.word_wrap = True
        p = tf.paragraphs[0]
        p.text = slide_data.slide_title
        p.font.size = Pt(32)
        p.font.bold = True
        p.font.color.rgb = RGBColor(0x18, 0x90, 0xFF)

        # 标题下划线
        line = slide.shapes.add_shape(
            1,  # MSO_SHAPE.RECTANGLE
            Inches(0.8), Inches(1.25), Inches(3), Pt(3)
        )
        line.fill.solid()
        line.fill.fore_color.rgb = RGBColor(0x18, 0x90, 0xFF)
        line.line.fill.background()

        # ── Bullet Points + 图片处理 ──
        raw_bullets = slide_data.bullet_points[:MAX_BULLETS_PER_SLIDE]
        cleaned_bullets: list[str] = []
        missing_images: list[str] = []

        for point in raw_bullets:
            m = re.search(IMAGE_PATTERN, point)
            if m:
                filename = m.group(1).strip()
                # 从 bullet 文字中删除 [INSERT_IMAGE: ...] 标签
                clean_point = re.sub(IMAGE_PATTERN, '', point).strip()
                if clean_point:
                    cleaned_bullets.append(clean_point)
                # 尝试插入图片
                self._try_insert_slide_image(slide, filename, project_dir, missing_images)
            else:
                cleaned_bullets.append(point)

        # 原有的 image_anchor 字段也一并处理
        if slide_data.image_anchor:
            img_filename = self._extract_image_filename(slide_data.image_anchor)
            if img_filename:
                self._try_insert_slide_image(slide, img_filename, project_dir, missing_images)

        # 渲染清理后的 bullet points
        bullet_box = slide.shapes.add_textbox(Inches(1.0), Inches(1.5), Inches(7), Inches(4.0))
        bf = bullet_box.text_frame
        bf.word_wrap = True

        for i, point in enumerate(cleaned_bullets):
            if i == 0:
                bp = bf.paragraphs[0]
            else:
                bp = bf.add_paragraph()
            bp.text = f"• {point}"
            bp.font.size = Pt(20)
            bp.space_after = Pt(12)

        # --- Speaker Notes (含缺失图片提示) ---
        notes_parts: list[str] = []
        if slide_data.speaker_notes:
            notes_parts.append(slide_data.speaker_notes)
        for fn in missing_images:
            notes_parts.append(f'[图片缺失: {fn}]')
        if notes_parts:
            notes_slide = slide.notes_slide
            notes_slide.notes_text_frame.text = '\n'.join(notes_parts)

    def _try_insert_slide_image(self, slide, filename: str, project_dir: str, missing_images: list[str]) -> None:
        """尝试在幻灯片右侧插入图片，失败时记录到 missing_images。"""
        img_path = os.path.join(project_dir, filename) if project_dir else ''
        if img_path and os.path.isfile(img_path):
            try:
                slide.shapes.add_picture(
                    img_path,
                    Inches(8.5), Inches(1.6),
                    width=Inches(4.2),
                )
                return
            except Exception:
                pass
        # 无 project_dir 时回退到原有搜索逻辑
        if not project_dir:
            if self._add_slide_image(slide, filename):
                return
        missing_images.append(filename)

    def _add_slide_image(self, slide, filename: str) -> bool:
        img_path = self._resolve_image_path(filename)
        if img_path:
            try:
                slide.shapes.add_picture(
                    str(img_path),
                    Inches(8.5), Inches(1.6),
                    width=Inches(4.2),
                )
                return True
            except Exception:
                pass
        return False

    def _extract_image_filename(self, text: str) -> str | None:
        m = IMAGE_ANCHOR_RE.search(text)
        return m.group(1).strip() if m else (text.strip() or None)

    def _resolve_image_path(self, filename: str) -> Path | None:
        p = Path(filename)
        if p.is_absolute() and p.exists():
            return p
        for rel_dir in self.asset_paths:
            search_dir = self._project_root / rel_dir
            if search_dir.exists():
                for ext in ('', '.png', '.jpg', '.jpeg', '.gif', '.bmp'):
                    candidate = search_dir / f"{filename}{ext}" if not filename.endswith(ext) else search_dir / filename
                    if candidate.exists():
                        return candidate
                for child in search_dir.iterdir():
                    if child.is_dir():
                        for ext in ('', '.png', '.jpg', '.jpeg', '.gif', '.bmp'):
                            c = child / f"{filename}{ext}" if not filename.endswith(ext) else child / filename
                            if c.exists():
                                return c
        return None

    # ── 文件级入口（Task 3: 模板占位符 + 标题页修改 + 内容页 + 备注） ──

    def build_ppt_report(
        self,
        report_data: PPTReport,
        template_path: str,
        output_path: str,
        project_dir: str = '',
    ) -> str:
        """从 PPTReport 加载模板并渲染到文件.

        模板 Slide 0 被视为标题页，将其标题修改为 report_data.title。
        之后每页基于模板正文版式新建幻灯片。

        Args:
            report_data: PPTReport 结构化数据
            template_path: .pptx 模板路径
            output_path: 输出 .pptx 路径
            project_dir: 项目根目录（用于拼接图片绝对路径）

        Returns:
            output_path (便于链式调用)
        """
        # ── 模板加载 guard — 与 build() line 54-57 / word_builder.py:567 同构 ──
        if template_path and Path(template_path).exists():
            prs = Presentation(template_path)
        else:
            prs = Presentation()
            # 默认 16:9
            prs.slide_width = Inches(13.333)
            prs.slide_height = Inches(7.5)

        # 1. 修改首页（Slide 0）标题（无模板时 prs.slides 为空 → _update_title_slide 直接 return）
        self._update_title_slide(prs, report_data)

        # 2. 删除模板已有的内容页（保留标题页即可；无模板时 prs.slides 为空 → 不进入循环）
        while len(prs.slides) > 1:
            rId = prs.slides._sldIdLst[-1].get(
                '{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id'
            )
            prs.part.drop_rel(rId)
            prs.slides._sldIdLst.remove(prs.slides._sldIdLst[-1])

        # 3. 渲染内容页
        for slide_data in report_data.slides:
            self._add_content_slide(prs, slide_data, project_dir=project_dir)

        # 4. 保存
        prs.save(output_path)
        return output_path

    def _update_title_slide(self, prs: Presentation, report: PPTReport) -> None:
        """修改模板首页的标题占位符文本."""
        if len(prs.slides) == 0:
            return
        slide = prs.slides[0]
        # 遍历所有形状，寻找标题占位符
        for shape in slide.shapes:
            if hasattr(shape, 'text_frame'):
                # 尝试替换 {{Report_Title}} 占位符
                for para in shape.text_frame.paragraphs:
                    for run in para.runs:
                        if '{{Report_Title}}' in run.text:
                            run.text = run.text.replace('{{Report_Title}}', report.title)
                        if '{{Report_Author}}' in run.text:
                            run.text = run.text.replace('{{Report_Author}}', report.author)
                        if '{{Report_Date}}' in run.text:
                            run.text = run.text.replace('{{Report_Date}}', report.date)

    # ── 向后兼容 ──

    def build_legacy(self, spec) -> bytes:
        """从旧版 ReportSpec 构建 PPT（向后兼容）。"""
        from .models import ReportSpec
        if not isinstance(spec, ReportSpec):
            raise TypeError("Expected ReportSpec")

        report = PPTReport(
            title=spec.title,
            author=spec.author,
            date=spec.date,
            slides=[
                PPTSlide(
                    slide_title=s.title,
                    bullet_points=[
                        str(b.data)[:40] for b in s.blocks if b.type == 'text'
                    ][:MAX_BULLETS_PER_SLIDE],
                )
                for s in spec.sections
            ],
        )
        return self.build(report)
