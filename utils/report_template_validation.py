"""报告模板的类型、可打开性与页面规格预校验。"""

from __future__ import annotations

import os


def _file_name_and_extension(template_path: str) -> tuple[str, str]:
    file_name = os.path.basename(template_path)
    return file_name, os.path.splitext(file_name)[1].lower()


def validate_word_template(template_path: str) -> str | None:
    """返回 None 表示可用，否则返回面向用户的中文错误。"""
    if not template_path or not os.path.isfile(template_path):
        return None
    file_name, extension = _file_name_and_extension(template_path)
    if extension != ".docx":
        return (
            f"模板文件「{file_name}」不是 .docx 格式"
            f"（扩展名为 {extension or '无'}，可能为旧 .doc 格式或其它报告模板）。\n"
            "请选择扩展名为 .docx 的有效 Word 模板，或清空模板使用默认样式。"
        )
    try:
        from docx import Document

        Document(template_path)
    except Exception as exc:
        return (
            f"模板文件「{file_name}」无法作为 Word 模板打开：{exc}\n"
            "请选择有效的 .docx 文件，或清空模板使用默认样式。"
        )
    return None


def validate_ppt_template(template_path: str) -> str | None:
    """校验 PPTX 包与演示页面尺寸，拒绝把海报模板误作幻灯片模板。"""
    if not template_path or not os.path.isfile(template_path):
        return None
    file_name, extension = _file_name_and_extension(template_path)
    if extension != ".pptx":
        return (
            f"模板文件「{file_name}」不是 .pptx 格式"
            f"（扩展名为 {extension or '无'}）。\n"
            "请选择扩展名为 .pptx 的有效演示模板，或清空模板使用默认样式。"
        )
    try:
        from pptx import Presentation

        presentation = Presentation(template_path)
        width_inches = float(presentation.slide_width or 0) / 914400.0
        height_inches = float(presentation.slide_height or 0) / 914400.0
    except Exception as exc:
        return (
            f"模板文件「{file_name}」无法作为 PowerPoint 模板打开：{exc}\n"
            "请选择有效的 .pptx 文件，或清空模板使用默认样式。"
        )
    if width_inches <= 0.0 or height_inches <= 0.0:
        return f"模板文件「{file_name}」页面尺寸无效。"
    if width_inches > 20.0 or height_inches > 20.0:
        return (
            f"模板文件「{file_name}」页面尺寸为 "
            f"{width_inches:.1f}×{height_inches:.1f} 英寸，属于海报/打印版式，"
            "不适合作为演示报告模板。请选择普通 16:9 或 4:3 PPTX 模板。"
        )
    aspect_ratio = width_inches / height_inches
    if not 1.1 <= aspect_ratio <= 2.2:
        return (
            f"模板文件「{file_name}」宽高比为 {aspect_ratio:.2f}，"
            "不是当前报告生成器支持的横向演示版式。"
            "请选择普通 16:9、16:10 或 4:3 PPTX 模板。"
        )
    return None


def validate_report_template(template_path: str, report_type: str) -> str | None:
    """按报告类型调用对应校验器，避免 PPTX 被误送入 Word 校验链路。"""
    if report_type == "ppt":
        return validate_ppt_template(template_path)
    return validate_word_template(template_path)
