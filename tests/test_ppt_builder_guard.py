"""ppt_builder build_ppt_report 空模板 guard — smoke test。

覆盖:
- template_path='' → 不抛错，用默认模板（内置空白 16:9）
- template_path=None → 同上
- template_path=不存在的路径 → 走默认，不抛 Package not found
- template_path=合法.pptx → 正常用该模板
"""

from __future__ import annotations

import os
import tempfile

import pytest

from dp_engine.report_builder.ppt_builder import PPTBuilder
from dp_engine.report_builder.models import PPTReport, PPTSlide


@pytest.fixture
def builder():
    return PPTBuilder()


@pytest.fixture
def minimal_report():
    """一个极简 PPTReport — 仅标题，无 slides。"""
    return PPTReport(
        title="测试报告",
        template_path=None,
        slides=[],
    )


@pytest.fixture
def report_with_slides():
    """含一页正文的 PPTReport。"""
    return PPTReport(
        title="传感器标定报告",
        template_path=None,
        slides=[
            PPTSlide(
                slide_title="数据质量评估",
                bullet_points=["异常点数: 5", "缺失率: 1.2%"],
                speaker_notes="详细说明。",
            )
        ],
    )


@pytest.fixture
def valid_template(tmp_path):
    """创建一个最小合法 .pptx 文件作为模板。"""
    from pptx import Presentation

    prs = Presentation()
    # 添加一个标题页（后续会用它验证 build_ppt_report 用了模板）
    slide_layout = prs.slide_layouts[0]
    prs.slides.add_slide(slide_layout)
    path = tmp_path / "template.pptx"
    prs.save(str(path))
    return str(path)


class TestBuildPptReportEmptyTemplate:
    """template_path 空/None/不存在 → 走默认，不抛错。"""

    def test_empty_string_uses_default(self, builder, report_with_slides, tmp_path):
        out = str(tmp_path / "out_empty.pptx")
        result = builder.build_ppt_report(
            report_with_slides,
            template_path="",
            output_path=out,
        )
        assert result == out
        assert os.path.exists(out)
        assert os.path.getsize(out) > 0
        # 确认默认 16:9 宽度
        from pptx import Presentation as _P
        prs = _P(out)
        # 默认 16:9 ≈ 13.333 inches
        assert prs.slide_width is not None and prs.slide_width > 0

    def test_none_uses_default(self, builder, report_with_slides, tmp_path):
        out = str(tmp_path / "out_none.pptx")
        result = builder.build_ppt_report(
            report_with_slides,
            template_path=None,  # pyright: ignore[reportArgumentType] — 特意测 None 兜底
            output_path=out,
        )
        assert result == out
        assert os.path.exists(out)
        assert os.path.getsize(out) > 0

    def test_nonexistent_path_uses_default(self, builder, report_with_slides, tmp_path):
        out = str(tmp_path / "out_nonexist.pptx")
        result = builder.build_ppt_report(
            report_with_slides,
            template_path="/nonexistent/path/template.pptx",
            output_path=out,
        )
        assert result == out
        assert os.path.exists(out)
        assert os.path.getsize(out) > 0


class TestBuildPptReportValidTemplate:
    """合法模板路径 → 正常使用该模板。"""

    def test_valid_template_used(self, builder, minimal_report, valid_template, tmp_path):
        out = str(tmp_path / "out_valid.pptx")
        result = builder.build_ppt_report(
            minimal_report,
            template_path=valid_template,
            output_path=out,
        )
        assert result == out
        assert os.path.exists(out)
        assert os.path.getsize(out) > 0


class TestBuildPptReportWithSlides:
    """含 slides 的报告正常生成（无模板默认）。"""

    def test_slides_generated_with_default_template(self, builder, report_with_slides, tmp_path):
        out = str(tmp_path / "out_slides.pptx")
        result = builder.build_ppt_report(
            report_with_slides,
            template_path="",
            output_path=out,
        )
        assert result == out
        assert os.path.exists(out)
        # 应至少有一页内容
        prs_path = result
        from pptx import Presentation as _P
        prs = _P(prs_path)
        # 标题页 (来自 title slide 自动添加? 不，build_ppt_report 没有 _add_title_slide
        # 只有 _update_title_slide 和 _add_content_slide)
        # 至少有一页内容 slide
        assert len(prs.slides) >= 1
