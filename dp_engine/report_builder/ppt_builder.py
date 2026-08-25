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
from typing import Callable, Optional

from pptx import Presentation
from pptx.util import Inches, Pt, Emu
from pptx.enum.text import MSO_ANCHOR, MSO_AUTO_SIZE, PP_ALIGN
from pptx.enum.shapes import MSO_SHAPE, PP_PLACEHOLDER
from pptx.dml.color import RGBColor

from .models import PPTReport, PPTSlide
from utils.report_template_preparation import load_prepared_template_profile


IMAGE_ANCHOR_RE = re.compile(r'\[INSERT_IMAGE:\s*([^\]]+)\]')

# 段落内嵌图片标签 — re.split / re.sub 专用
IMAGE_PATTERN = r"\[INSERT_IMAGE:\s*(.+?)\]"

# 每页 Bullet 数量硬限制
MAX_BULLETS_PER_SLIDE = 4

_DEFAULT_NAVY = RGBColor(0x0B, 0x1F, 0x33)
_DEFAULT_TEAL = RGBColor(0x1D, 0xA7, 0xA1)
_DEFAULT_TEXT = RGBColor(0x2C, 0x3A, 0x4B)
_DEFAULT_MUTED = RGBColor(0x6B, 0x78, 0x88)
_DEFAULT_CANVAS = RGBColor(0xF7, 0xF9, 0xFC)

_TITLE_PLACEHOLDERS = {
    PP_PLACEHOLDER.TITLE,
    PP_PLACEHOLDER.CENTER_TITLE,
    PP_PLACEHOLDER.VERTICAL_TITLE,
}
_BODY_PLACEHOLDERS = {
    PP_PLACEHOLDER.BODY,
    PP_PLACEHOLDER.OBJECT,
    PP_PLACEHOLDER.VERTICAL_BODY,
    PP_PLACEHOLDER.VERTICAL_OBJECT,
}
_PICTURE_PLACEHOLDERS = {
    PP_PLACEHOLDER.PICTURE,
    PP_PLACEHOLDER.BITMAP,
}


class PPTBuilder:
    """从 PPTReport 结构体构建 .pptx 演示文稿。"""

    DEFAULT_ASSET_DIRS = ["wiki_vault/assets", "wiki_vault/pages", "project_files"]

    def __init__(self, asset_search_paths: list[str] | None = None):
        self.asset_paths = asset_search_paths or self.DEFAULT_ASSET_DIRS
        self._project_root = Path(__file__).parent.parent.parent
        self.warnings: list[str] = []
        self._using_template = False
        self._template_profile: dict[str, object] | None = None

    def _warn_once(self, message: str) -> None:
        if message not in self.warnings:
            self.warnings.append(message)

    # ── 主入口 ──

    def build(self, report: PPTReport) -> bytes:
        """从 PPTReport 构建 .pptx 文件。

        Args:
            report: PPTReport 结构化数据

        Returns:
            .pptx 文件二进制内容
        """
        self.warnings.clear()
        has_template = bool(report.template_path and Path(report.template_path).exists())
        self._using_template = has_template
        self._template_profile = (
            load_prepared_template_profile(str(report.template_path))
            if has_template and report.template_path
            else None
        )
        if has_template:
            prs = Presentation(report.template_path)
        else:
            prs = Presentation()
            # 默认 16:9
            prs.slide_width = Inches(13.333)
            prs.slide_height = Inches(7.5)

        if has_template and len(prs.slides) > 0:
            self._update_title_slide(prs, report)
            self._remove_template_content_slides(prs)
        else:
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
        slide = prs.slides.add_slide(self._get_title_layout(prs))
        slide_w = int(prs.slide_width)
        slide_h = int(prs.slide_height)
        if not self._using_template:
            background = slide.background.fill
            background.solid()
            background.fore_color.rgb = _DEFAULT_NAVY
            accent = slide.shapes.add_shape(
                MSO_SHAPE.RECTANGLE,
                Emu(int(slide_w * 0.07)),
                Emu(int(slide_h * 0.25)),
                Emu(int(slide_w * 0.012)),
                Emu(int(slide_h * 0.34)),
            )
            accent.fill.solid()
            accent.fill.fore_color.rgb = _DEFAULT_TEAL
            accent.line.fill.background()
            eyebrow = slide.shapes.add_textbox(
                Emu(int(slide_w * 0.10)),
                Emu(int(slide_h * 0.15)),
                Emu(int(slide_w * 0.80)),
                Emu(int(slide_h * 0.06)),
            )
            eyebrow_p = eyebrow.text_frame.paragraphs[0]
            eyebrow_p.text = "DATAPROCESSOR PRO · SENSOR DIAGNOSTICS"
            eyebrow_p.font.size = Pt(14)
            eyebrow_p.font.bold = True
            eyebrow_p.font.color.rgb = _DEFAULT_TEAL

        title_shape = (
            None
            if self._profile_contract() == "safe-textboxes"
            else slide.shapes.title
        )
        if title_shape is not None and title_shape.has_text_frame:
            self._set_placeholder_text(title_shape, report.title)
            if not self._using_template:
                title_shape.left = Emu(int(slide_w * 0.10))
                title_shape.top = Emu(int(slide_h * 0.27))
                title_shape.width = Emu(int(slide_w * 0.80))
                title_shape.height = Emu(int(slide_h * 0.27))
                title_shape.text_frame.word_wrap = True
                title_shape.text_frame.vertical_anchor = MSO_ANCHOR.MIDDLE
                title_shape.text_frame.paragraphs[0].font.size = Pt(50)
                title_shape.text_frame.paragraphs[0].font.bold = True
                title_shape.text_frame.paragraphs[0].font.color.rgb = RGBColor(
                    0xFF, 0xFF, 0xFF
                )
                title_shape.text_frame.paragraphs[0].alignment = PP_ALIGN.LEFT
        else:
            tb = slide.shapes.add_textbox(
                Emu(int(slide_w * 0.10)), Emu(int(slide_h * 0.27)),
                Emu(int(slide_w * 0.80)), Emu(int(slide_h * 0.27)),
            )
            tf = tb.text_frame
            tf.word_wrap = True
            tf.vertical_anchor = MSO_ANCHOR.TOP
            p = tf.paragraphs[0]
            p.text = report.title
            p.font.size = Pt(50)
            p.font.bold = True
            p.alignment = PP_ALIGN.LEFT
            if not self._using_template:
                p.font.color.rgb = RGBColor(0xFF, 0xFF, 0xFF)

        parts = []
        if report.author:
            parts.append(report.author)
        if report.date:
            parts.append(report.date)
        subtitle = '  |  '.join(parts)
        subtitle_shape = self._find_placeholder(slide, {PP_PLACEHOLDER.SUBTITLE})
        if subtitle_shape is not None and subtitle_shape.has_text_frame:
            self._set_placeholder_text(subtitle_shape, subtitle)
            if not self._using_template:
                subtitle_shape.left = Emu(int(slide_w * 0.10))
                subtitle_shape.top = Emu(int(slide_h * 0.60))
                subtitle_shape.width = Emu(int(slide_w * 0.80))
                subtitle_shape.height = Emu(int(slide_h * 0.08))
                subtitle_p = subtitle_shape.text_frame.paragraphs[0]
                subtitle_p.font.size = Pt(18)
                subtitle_p.font.color.rgb = RGBColor(0xC8, 0xD4, 0xE3)
                subtitle_p.alignment = PP_ALIGN.LEFT
        elif subtitle:
            sub = slide.shapes.add_textbox(
                Emu(int(slide_w * 0.10)), Emu(int(slide_h * 0.60)),
                Emu(int(slide_w * 0.80)), Emu(int(slide_h * 0.08)),
            )
            stf = sub.text_frame
            stf.word_wrap = True
            sp = stf.paragraphs[0]
            sp.text = subtitle
            sp.font.size = Pt(18)
            sp.font.color.rgb = (
                RGBColor(0xC8, 0xD4, 0xE3)
                if not self._using_template
                else RGBColor(0x66, 0x66, 0x66)
            )
            sp.alignment = PP_ALIGN.LEFT

    def _get_title_layout(self, prs: Presentation):
        layouts = list(prs.slide_layouts)
        if not layouts:
            raise ValueError("PPT 模板不含任何可用版式")
        preferred = self._profile_layout(layouts, "title_layout_index")
        if preferred is not None:
            return preferred

        def _score(layout) -> int:
            types = self._layout_placeholder_types(layout)
            score = 0
            if types & _TITLE_PLACEHOLDERS:
                score += 10
            if PP_PLACEHOLDER.SUBTITLE in types:
                score += 5
            name = str(getattr(layout, "name", "")).lower()
            if "title" in name or "标题" in name:
                score += 2
            return score

        return max(layouts, key=_score)

    # ── 内容页 ──

    @staticmethod
    def _layout_placeholder_types(layout) -> set[PP_PLACEHOLDER]:
        result: set[PP_PLACEHOLDER] = set()
        for placeholder in layout.placeholders:
            try:
                result.add(placeholder.placeholder_format.type)
            except (AttributeError, ValueError):
                continue
        return result

    def _get_content_layout(self, prs: Presentation, *, wants_image: bool = False):
        """按占位符能力选择正文版式，不再依赖脆弱的固定版式下标。"""
        layouts = list(prs.slide_layouts)
        if not layouts:
            raise ValueError("PPT 模板不含任何可用版式")
        preferred = self._profile_layout(layouts, "content_layout_index")
        if preferred is not None:
            selected = preferred
        else:
            selected = None

        def _score(layout) -> int:
            types = self._layout_placeholder_types(layout)
            has_title = bool(types & _TITLE_PLACEHOLDERS)
            has_body = bool(types & _BODY_PLACEHOLDERS)
            has_picture = bool(types & _PICTURE_PLACEHOLDERS)
            score = (10 if has_body else 0) + (5 if has_title else 0)
            if wants_image:
                # 图表需要保持完整纵横比，不能仅因模板有“照片占位符”就选中
                # Picture with Caption 等会把标题放到底部、正文压成零高度的版式。
                score += 2 if has_picture and has_body else 0
                score -= 5 if has_picture and not has_body else 0
            elif has_picture:
                score -= 2
            name = str(getattr(layout, "name", "")).lower()
            if any(token in name for token in ("content", "正文", "内容", "text")):
                score += 2
            if any(token in name for token in ("title slide", "标题幻灯片")):
                score -= 4
            if "title" in name and not has_body:
                score -= 3
            if "slide" in name and "title" not in name:
                score += 1
            return score

        if selected is None:
            selected = max(layouts, key=_score)
        selected_types = self._layout_placeholder_types(selected)
        if (
            not selected_types & _BODY_PLACEHOLDERS
            and self._profile_contract() != "safe-textboxes"
        ):
            self._warn_once(
                "所选 PPT 模板缺少可编辑正文占位符；已继承母版/版式背景并使用安全文本框，"
                "无法保证复刻模板样例页的全部排版。"
            )
        return selected

    def _add_content_slide(self, prs: Presentation, slide_data: PPTSlide, project_dir: str = '') -> None:
        # ── 先解析内容和图片，再据此选择模板版式 ──
        raw_bullets = slide_data.bullet_points[:MAX_BULLETS_PER_SLIDE]
        cleaned_bullets: list[str] = []
        requested_images: list[str] = []

        for point in raw_bullets:
            m = re.search(IMAGE_PATTERN, point)
            if m:
                filename = m.group(1).strip()
                requested_images.append(filename)
                clean_point = re.sub(IMAGE_PATTERN, '', point).strip()
                if clean_point:
                    cleaned_bullets.append(clean_point)
            else:
                cleaned_bullets.append(point)

        if slide_data.image_anchor:
            img_filename = self._extract_image_filename(slide_data.image_anchor)
            if img_filename:
                requested_images.append(img_filename)

        image_path: Path | None = None
        missing_images: list[str] = []
        for filename in requested_images:
            resolved = self._resolve_requested_image(filename, project_dir)
            if resolved is not None and image_path is None:
                image_path = resolved
            elif resolved is None:
                missing_images.append(filename)
            else:
                self._warn_once(
                    f"幻灯片「{slide_data.slide_title}」包含多张图片，只插入第一张；"
                    f"其余图片请拆分到独立页面。"
                )

        layout = self._get_content_layout(prs, wants_image=image_path is not None)
        slide = prs.slides.add_slide(layout)
        slide_w = int(prs.slide_width)
        slide_h = int(prs.slide_height)
        if not self._using_template:
            self._apply_default_content_canvas(slide, prs)
        if self._profile_contract() == "safe-textboxes":
            self._remove_placeholders(slide, _BODY_PLACEHOLDERS)

        # --- 标题：优先填模板标题占位符 ---
        title_shape = slide.shapes.title
        if title_shape is not None and title_shape.has_text_frame:
            # 第三方模板可能把标题占位符挂在画布边缘甚至画布外。
            # 保留其字体/颜色/段落样式，只把几何区域归一到安全标题带。
            if not self._preserve_native_geometry(title_shape, prs, role="title"):
                title_shape.left = Emu(int(slide_w * 0.06))
                title_shape.top = Emu(int(slide_h * 0.04))
                title_shape.width = Emu(int(slide_w * 0.88))
                title_shape.height = Emu(int(slide_h * 0.12))
            self._set_placeholder_text(title_shape, slide_data.slide_title)
            if not self._using_template:
                title_paragraph = title_shape.text_frame.paragraphs[0]
                title_paragraph.font.size = Pt(35)
                title_paragraph.font.bold = True
                title_paragraph.font.color.rgb = _DEFAULT_NAVY
                title_shape.text_frame.auto_size = MSO_AUTO_SIZE.TEXT_TO_FIT_SHAPE
        else:
            tb = slide.shapes.add_textbox(
                Emu(int(slide_w * 0.06)), Emu(int(slide_h * 0.05)),
                Emu(int(slide_w * 0.88)), Emu(int(slide_h * 0.12)),
            )
            tf = tb.text_frame
            tf.word_wrap = True
            tf.vertical_anchor = MSO_ANCHOR.TOP
            p = tf.paragraphs[0]
            p.text = slide_data.slide_title
            p.font.size = Pt(35)
            p.font.bold = True
            p.font.color.rgb = _DEFAULT_NAVY
            self._apply_profile_paragraph_style(p, "title_style")

        # --- 正文：优先填模板正文占位符 ---
        body_shape = (
            None
            if self._profile_contract() == "safe-textboxes"
            else self._find_placeholder(slide, _BODY_PLACEHOLDERS)
        )
        if body_shape is not None and body_shape.has_text_frame:
            # 一些第三方模板的 BODY 占位符高度为 0，或位于标题上方。
            # 保留其字体/项目符号样式，但把几何区域约束到安全正文区。
            if not self._preserve_native_geometry(body_shape, prs, role="body"):
                body_shape.left = Emu(int(slide_w * 0.075))
                body_shape.top = Emu(int(slide_h * 0.22))
                body_shape.width = Emu(
                    int(slide_w * (0.47 if image_path is not None else 0.84))
                )
                body_shape.height = Emu(int(slide_h * 0.62))
            self._set_bullet_text(body_shape.text_frame, cleaned_bullets, template_placeholder=True)
            if not self._using_template:
                for index, paragraph in enumerate(body_shape.text_frame.paragraphs):
                    paragraph.font.size = Pt(23)
                    paragraph.font.color.rgb = (
                        _DEFAULT_TEAL if index == 0 else _DEFAULT_TEXT
                    )
                    paragraph.font.bold = index == 0
                    paragraph.space_after = Pt(20)
                    paragraph.line_spacing = 1.15
        else:
            body_width = 0.47 if image_path is not None else 0.84
            bullet_box = slide.shapes.add_textbox(
                Emu(int(slide_w * 0.075)), Emu(int(slide_h * 0.22)),
                Emu(int(slide_w * body_width)), Emu(int(slide_h * 0.62)),
            )
            self._set_bullet_text(
                bullet_box.text_frame, cleaned_bullets, template_placeholder=False,
            )
            bullet_box.text_frame.vertical_anchor = MSO_ANCHOR.TOP
            for paragraph in bullet_box.text_frame.paragraphs:
                if self._using_template:
                    self._apply_profile_paragraph_style(paragraph, "body_style")
                else:
                    paragraph.font.size = Pt(23)
                    paragraph.font.color.rgb = _DEFAULT_TEXT
                    paragraph.space_after = Pt(20)
                    paragraph.line_spacing = 1.15

        if image_path is not None:
            self._insert_content_image(slide, prs, image_path, body_shape)

        # --- Speaker Notes (含缺失图片提示) ---
        notes_parts: list[str] = []
        if slide_data.speaker_notes:
            notes_parts.append(slide_data.speaker_notes)
        for fn in missing_images:
            notes_parts.append(f'[图片缺失: {fn}]')
        if notes_parts:
            notes_slide = slide.notes_slide
            notes_slide.notes_text_frame.text = '\n'.join(notes_parts)
        for filename in missing_images:
            self._warn_once(f"PPT 图片缺失: {filename}")

    def _apply_default_content_canvas(self, slide, prs: Presentation) -> None:
        """无模板时应用正式技术汇报的稳定视觉令牌。"""
        slide_w = int(prs.slide_width)
        slide_h = int(prs.slide_height)
        background = slide.background.fill
        background.solid()
        background.fore_color.rgb = _DEFAULT_CANVAS

        top_rule = slide.shapes.add_shape(
            MSO_SHAPE.RECTANGLE,
            Emu(0),
            Emu(0),
            Emu(slide_w),
            Emu(int(slide_h * 0.022)),
        )
        top_rule.fill.solid()
        top_rule.fill.fore_color.rgb = _DEFAULT_TEAL
        top_rule.line.fill.background()

        rail = slide.shapes.add_shape(
            MSO_SHAPE.RECTANGLE,
            Emu(int(slide_w * 0.055)),
            Emu(int(slide_h * 0.225)),
            Emu(int(slide_w * 0.008)),
            Emu(int(slide_h * 0.48)),
        )
        rail.fill.solid()
        rail.fill.fore_color.rgb = _DEFAULT_TEAL
        rail.line.fill.background()

        footer = slide.shapes.add_textbox(
            Emu(int(slide_w * 0.06)),
            Emu(int(slide_h * 0.925)),
            Emu(int(slide_w * 0.88)),
            Emu(int(slide_h * 0.035)),
        )
        footer_p = footer.text_frame.paragraphs[0]
        footer_p.text = f"DATAPROCESSOR PRO   |   {len(prs.slides):02d}"
        footer_p.font.size = Pt(10)
        footer_p.font.color.rgb = _DEFAULT_MUTED
        footer_p.alignment = PP_ALIGN.RIGHT

    @staticmethod
    def _set_placeholder_text(shape, text: str) -> None:
        text_frame = shape.text_frame
        text_frame.clear()
        text_frame.paragraphs[0].text = text

    @staticmethod
    def _set_bullet_text(text_frame, points: list[str], *, template_placeholder: bool) -> None:
        text_frame.clear()
        text_frame.word_wrap = True
        if not points:
            return
        for index, point in enumerate(points):
            paragraph = text_frame.paragraphs[0] if index == 0 else text_frame.add_paragraph()
            paragraph.text = point if template_placeholder else f"• {point}"
            paragraph.level = 0
            if not template_placeholder:
                paragraph.font.size = Pt(20)
                paragraph.space_after = Pt(12)

    @staticmethod
    def _find_placeholder(slide, placeholder_types: set[PP_PLACEHOLDER]):
        for placeholder in slide.placeholders:
            try:
                if placeholder.placeholder_format.type in placeholder_types:
                    return placeholder
            except (AttributeError, ValueError):
                continue
        return None

    @staticmethod
    def _remove_placeholders(slide, placeholder_types: set[PP_PLACEHOLDER]) -> None:
        for placeholder in list(slide.placeholders):
            try:
                if placeholder.placeholder_format.type in placeholder_types:
                    placeholder._element.getparent().remove(placeholder._element)
            except (AttributeError, ValueError):
                continue

    def _resolve_requested_image(self, filename: str, project_dir: str) -> Path | None:
        path = Path(filename)
        if path.is_absolute() and path.is_file():
            return path
        if project_dir:
            candidate = Path(project_dir) / filename
            if candidate.is_file():
                return candidate
        return self._resolve_image_path(filename)

    def _insert_content_image(
        self,
        slide,
        prs: Presentation,
        image_path: Path,
        body_shape=None,
    ) -> None:
        picture_placeholder = self._find_placeholder(slide, _PICTURE_PLACEHOLDERS)
        if (
            picture_placeholder is not None
            and hasattr(picture_placeholder, "insert_picture")
            and self._picture_placeholder_is_safe(picture_placeholder, body_shape, prs, image_path)
        ):
            try:
                picture_placeholder.insert_picture(str(image_path))
                return
            except Exception as exc:
                self._warn_once(f"模板图片占位符填充失败，已使用安全图片区：{exc}")

        slide_w = int(prs.slide_width)
        slide_h = int(prs.slide_height)
        left = int(slide_w * 0.57)
        top = int(slide_h * 0.22)
        max_width = int(slide_w * 0.37)
        max_height = int(slide_h * 0.60)
        try:
            from PIL import Image

            with Image.open(image_path) as image:
                image_width, image_height = image.size
            scale = min(max_width / image_width, max_height / image_height)
            width = int(image_width * scale)
            height = int(image_height * scale)
        except Exception:
            width = max_width
            height = max_height
        slide.shapes.add_picture(
            str(image_path),
            Emu(left + max(0, (max_width - width) // 2)),
            Emu(top + max(0, (max_height - height) // 2)),
            width=Emu(width),
            height=Emu(height),
        )

    @staticmethod
    def _picture_placeholder_is_safe(
        picture_placeholder,
        body_shape,
        prs: Presentation,
        image_path: Path,
    ) -> bool:
        """仅使用不会裁坏图表、也不会压住正文的模板图片占位符。"""
        slide_w = max(1, int(prs.slide_width))
        if int(picture_placeholder.left) < int(slide_w * 0.55):
            return False
        if int(picture_placeholder.width) <= 0 or int(picture_placeholder.height) <= 0:
            return False
        if body_shape is not None:
            body_right = int(body_shape.left) + int(body_shape.width)
            if body_right > int(picture_placeholder.left):
                return False
        try:
            from PIL import Image

            with Image.open(image_path) as image:
                image_ratio = image.width / max(1, image.height)
            placeholder_ratio = (
                int(picture_placeholder.width) / max(1, int(picture_placeholder.height))
            )
            ratio_delta = placeholder_ratio / max(0.01, image_ratio)
            return 0.75 <= ratio_delta <= 1.33
        except Exception:
            return False

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

    @staticmethod
    def _remove_template_content_slides(prs: Presentation) -> None:
        """保留模板封面，删除其余示例页；新页继续引用原母版和版式。"""
        while len(prs.slides) > 1:
            slide_id = prs.slides._sldIdLst[-1]
            relationship_id = slide_id.get(
                '{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id'
            )
            if relationship_id:
                prs.part.drop_rel(relationship_id)
            prs.slides._sldIdLst.remove(slide_id)

    # ── 文件级入口（Task 3: 模板占位符 + 标题页修改 + 内容页 + 备注） ──

    def build_ppt_report(
        self,
        report_data: PPTReport,
        template_path: str,
        output_path: str,
        project_dir: str = '',
        *,
        bridge_workspace: Path | None = None,
        bridge_assets: tuple = (),
        cancel_check: Callable[[], None] | None = None,
    ) -> str:
        """从 PPTReport 加载模板并渲染到文件.

        模板 Slide 0 被视为标题页，将其标题修改为 report_data.title。
        之后每页基于模板正文版式新建幻灯片。

        Args:
            report_data: PPTReport 结构化数据
            template_path: .pptx 模板路径
            output_path: 输出 .pptx 路径
            project_dir: 项目根目录（用于拼接图片绝对路径）
            bridge_workspace: Bridge workspace 目录（可选）
            bridge_assets: PreparedReportAsset 元组（可选）
            cancel_check: 取消检查回调（可选）

        Returns:
            output_path (便于链式调用)
        """
        self.warnings.clear()
        has_template = bool(template_path and Path(template_path).exists())
        self._using_template = has_template
        self._template_profile = (
            load_prepared_template_profile(template_path)
            if has_template
            else None
        )
        if has_template:
            prs = Presentation(template_path)
        else:
            prs = Presentation()
            # 默认 16:9
            prs.slide_width = Inches(13.333)
            prs.slide_height = Inches(7.5)

        # Cancel checkpoint: before builder
        if cancel_check is not None:
            cancel_check()

        # 1. 有模板保留并填充封面；无模板必须显式创建封面。
        if has_template and len(prs.slides) > 0:
            self._update_title_slide(prs, report_data)
            self._remove_template_content_slides(prs)
        else:
            self._add_title_slide(prs, report_data)

        # 2. 渲染内容页
        for slide_data in report_data.slides:
            # Cancel checkpoint: before each slide
            if cancel_check is not None:
                cancel_check()
            self._add_content_slide(prs, slide_data, project_dir=project_dir)

        # 3. Bridge appendix (deterministic, after LLM content, before save)
        if bridge_workspace is not None and bridge_assets:
            from dp_engine.report_bridge.adapters import (
                validate_bridge_assets_for_ppt,
                append_bridge_assets_to_ppt,
            )
            # Cancel checkpoint: before bridge appendix
            if cancel_check is not None:
                cancel_check()
            # TABLE_SOURCE rejection happens here (before any slide modification)
            validate_bridge_assets_for_ppt(bridge_workspace, bridge_assets)
            append_bridge_assets_to_ppt(
                prs,
                bridge_workspace=bridge_workspace,
                assets=bridge_assets,
                cancel_check=cancel_check,
            )

        # 4. 保存
        prs.save(output_path)
        return output_path

    def _profile_layout(self, layouts: list, key: str):
        if self._template_profile is None:
            return None
        value = self._template_profile.get(key)
        if not isinstance(value, int) or isinstance(value, bool):
            return None
        if 0 <= value < len(layouts):
            return layouts[value]
        return None

    def _profile_contract(self) -> str:
        if self._template_profile is None:
            return ""
        value = self._template_profile.get("content_contract")
        return value if isinstance(value, str) else ""

    def _preserve_native_geometry(self, shape, prs: Presentation, *, role: str) -> bool:
        contract = self._profile_contract()
        allow_partial_title = role == "title" and contract == "safe-textboxes"
        if contract != "native-placeholders" and not allow_partial_title:
            return False
        slide_width = max(1, int(prs.slide_width))
        slide_height = max(1, int(prs.slide_height))
        left = int(shape.left)
        top = int(shape.top)
        width = int(shape.width)
        height = int(shape.height)
        if left < 0 or top < 0 or width <= 0 or height <= 0:
            return False
        if left + width > slide_width or top + height > slide_height:
            return False
        if width < slide_width * 0.2 or height < slide_height * 0.04:
            return False
        if role == "title":
            return top < slide_height * 0.45
        return top < slide_height * 0.9 and height >= slide_height * 0.1

    def _apply_profile_paragraph_style(self, paragraph, key: str) -> None:
        from pptx.enum.dml import MSO_THEME_COLOR

        if self._template_profile is None:
            return
        raw_style = self._template_profile.get(key)
        if not isinstance(raw_style, dict):
            return
        size = raw_style.get("font_size_pt")
        if isinstance(size, (int, float)) and not isinstance(size, bool) and size > 0:
            paragraph.font.size = Pt(float(size))
        bold = raw_style.get("bold")
        if isinstance(bold, bool):
            paragraph.font.bold = bold
        name = raw_style.get("font_name")
        if isinstance(name, str) and name:
            paragraph.font.name = name
        color = raw_style.get("color_rgb")
        if isinstance(color, str) and re.fullmatch(r"[0-9A-Fa-f]{6}", color):
            paragraph.font.color.rgb = RGBColor.from_string(color)
        theme_color = raw_style.get("theme_color")
        if isinstance(theme_color, int) and not isinstance(theme_color, bool):
            try:
                paragraph.font.color.theme_color = MSO_THEME_COLOR(theme_color)
            except ValueError:
                pass

    def _update_title_slide(self, prs: Presentation, report: PPTReport) -> None:
        """填充模板封面；既支持显式标记，也支持标准标题/副标题占位符。"""
        if len(prs.slides) == 0:
            return
        slide = prs.slides[0]
        title_replaced = False
        author_replaced = False
        date_replaced = False
        title_target = None
        for shape in slide.shapes:
            if getattr(shape, 'has_text_frame', False):
                original_shape_text = shape.text
                shape_title_replaced = False
                for para in shape.text_frame.paragraphs:
                    for run in para.runs:
                        original = run.text
                        for token in ("{{Report_Title}}", "{{title}}"):
                            if token in run.text:
                                run.text = run.text.replace(token, report.title)
                                title_replaced = True
                                shape_title_replaced = True
                        for token in ("{{Report_Author}}", "{{author}}"):
                            if token in run.text:
                                run.text = run.text.replace(token, report.author)
                                author_replaced = True
                        for token in ("{{Report_Date}}", "{{date}}"):
                            if token in run.text:
                                run.text = run.text.replace(token, report.date)
                                date_replaced = True
                        if original != run.text:
                            continue
                if shape_title_replaced:
                    self._fit_replaced_title(shape, original_shape_text, report.title)
                    title_target = shape

        title_shape = slide.shapes.title
        if not title_replaced and title_shape is not None and title_shape.has_text_frame:
            self._replace_title_shape_text(title_shape, report.title)
            title_replaced = True
            title_target = title_shape

        subtitle_shape = self._find_placeholder(slide, {PP_PLACEHOLDER.SUBTITLE})
        subtitle = "  |  ".join(part for part in (report.author, report.date) if part)
        if subtitle and subtitle_shape is not None and subtitle_shape.has_text_frame:
            self._replace_shape_text_preserving_style(subtitle_shape, subtitle)
            author_replaced = True
            date_replaced = True

        if not title_replaced:
            candidate = self._select_title_text_shape(slide, prs)
            if candidate is not None:
                self._replace_title_shape_text(candidate, report.title)
                title_replaced = True
                title_target = candidate

        text_shapes = [
            shape for shape in slide.shapes
            if getattr(shape, "has_text_frame", False)
            and shape.text.strip()
            and shape is not title_target
        ]
        if not author_replaced and report.author:
            author_shape = next(
                (
                    shape for shape in text_shapes
                    if re.search(
                        r'author|company name|your name|汇报人|作者|单位',
                        shape.text.lower(),
                    )
                ),
                None,
            )
            if author_shape is None:
                author_shape = next(
                    (
                        shape for shape in text_shapes
                        if "university name" in shape.text.lower()
                    ),
                    None,
                )
            if author_shape is not None:
                original_author_text = author_shape.text.lower()
                if (
                    report.date
                    and re.search(r'20xx|date|year|日期|时间', original_author_text)
                ):
                    self._replace_shape_text_preserving_style(author_shape, subtitle)
                    date_replaced = True
                else:
                    self._replace_shape_text_preserving_style(author_shape, report.author)
                author_replaced = True

        if not date_replaced and report.date:
            date_shape = next(
                (
                    shape for shape in text_shapes
                    if re.search(r'20xx|date|year|日期|时间', shape.text.lower())
                ),
                None,
            )
            if date_shape is not None:
                self._replace_shape_text_preserving_style(date_shape, report.date)
                date_replaced = True

        for shape in text_shapes:
            lowered = shape.text.lower()
            if subtitle and re.search(
                r'insert (?:the )?sub ?title|subtitle|general dynamic ppt template|副标题',
                lowered,
            ):
                replacement = "" if author_replaced and date_replaced else subtitle
                self._replace_shape_text_preserving_style(shape, replacement)
            elif "university name" in lowered and shape.text != report.author:
                self._replace_shape_text_preserving_style(shape, "")

        if not title_replaced:
            self._warn_once("PPT 模板封面未找到可编辑标题对象，已保留模板原封面文字。")

    @staticmethod
    def _replace_shape_text_preserving_style(shape, text: str) -> None:
        first_run = None
        for paragraph in shape.text_frame.paragraphs:
            for run in paragraph.runs:
                if first_run is None:
                    first_run = run
                    run.text = text
                else:
                    run.text = ""
        if first_run is None:
            shape.text_frame.text = text
        for paragraph in list(shape.text_frame.paragraphs)[1:]:
            paragraph.text = ""

    @classmethod
    def _replace_title_shape_text(cls, shape, text: str) -> None:
        original_text = shape.text
        cls._replace_shape_text_preserving_style(shape, text)
        cls._fit_replaced_title(shape, original_text, text)

    @staticmethod
    def _fit_replaced_title(shape, original_text: str, new_text: str) -> None:
        """长标题按模板原文字宽缩小字号，保留颜色、字体和对齐方式。"""
        shape.text_frame.word_wrap = True
        shape.text_frame.auto_size = MSO_AUTO_SIZE.TEXT_TO_FIT_SHAPE
        font_sizes = [
            float(run.font.size.pt)
            for paragraph in shape.text_frame.paragraphs
            for run in paragraph.runs
            if run.font.size is not None
        ]
        if not font_sizes or not new_text:
            return

        def _visual_units(text: str) -> float:
            return sum(
                1.0 if ord(char) > 127 else (0.55 if char.isalnum() else 0.35)
                for char in text
                if not char.isspace()
            )

        original_units = max(1.0, _visual_units(original_text))
        new_units = max(1.0, _visual_units(new_text))
        if new_units <= original_units:
            return
        target_size = max(24.0, max(font_sizes) * original_units / new_units)
        first_paragraph = shape.text_frame.paragraphs[0]
        for run in first_paragraph.runs:
            run.font.size = Pt(target_size)

    @staticmethod
    def _select_title_text_shape(slide, prs: Presentation):
        slide_w = max(1, int(prs.slide_width))
        slide_h = max(1, int(prs.slide_height))
        candidates = []
        for shape in slide.shapes:
            if not getattr(shape, "has_text_frame", False) or not shape.text.strip():
                continue
            font_points = [
                float(run.font.size.pt)
                for paragraph in shape.text_frame.paragraphs
                for run in paragraph.runs
                if run.font.size is not None
            ]
            max_font = max(font_points, default=0.0)
            top_bonus = 1.0 - min(1.0, float(shape.top) / slide_h)
            area_ratio = float(shape.width * shape.height) / float(slide_w * slide_h)
            text_penalty = min(2.0, len(shape.text) / 120.0)
            lowered = shape.text.strip().lower()
            semantic_bonus = (
                10.0 if re.search(
                    r'title|report|presentation|thesis|workshop|research|汇报|报告|模板',
                    lowered,
                ) else 0.0
            )
            placeholder_penalty = (
                100.0 if re.fullmatch(r'(?:20xx|19xx|date|year|\d{4})', lowered)
                else 0.0
            )
            score = (
                max_font + top_bonus * 8.0 + area_ratio * 4.0
                + semantic_bonus - text_penalty - placeholder_penalty
            )
            candidates.append((score, shape))
        return max(candidates, key=lambda item: item[0])[1] if candidates else None

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
