"""_inject_figures_to_pptx — manifest 直插 PPT 附录 slide，零 LLM 依赖。

覆盖:
- manifest 非空 → pptx 每张图一个 slide，含 picture shape + caption
- 空 manifest → 不崩、不追加空 slide
- manifest 为 None → 安全返回
- png_path 使用绝对路径、不依赖 project_dir 拼路径
- Word 路径 (_inject_figures_by_reference) 不受影响
"""

from __future__ import annotations

import os
import tempfile
from types import SimpleNamespace

import numpy as np
import pytest


class _FakeManifest:
    """模拟 FigureManifest — 可迭代(SimpleNamespace 不支持 dunder 实例属性)."""

    def __init__(self, figures: list[SimpleNamespace]):
        self._figures = figures
        self.count = len(figures)

    def __iter__(self):
        return iter(self._figures)


@pytest.fixture
def sample_png(tmp_path):
    """创建一个最小 PNG 文件供测试。"""
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt

    fig, ax = plt.subplots(figsize=(4, 3))
    ax.plot([1, 2, 3], [1, 4, 9])
    png_path = tmp_path / "chart_1.png"
    fig.savefig(str(png_path), dpi=72)
    plt.close(fig)
    return str(png_path)


@pytest.fixture
def multi_pngs(tmp_path):
    """创建两个 PNG 文件。"""
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt

    paths = []
    for i in range(2):
        fig, ax = plt.subplots(figsize=(4, 3))
        ax.bar([1, 2, 3], [1, 4, 9])
        p = tmp_path / f"chart_{i}.png"
        fig.savefig(str(p), dpi=72)
        plt.close(fig)
        paths.append(str(p))
    return paths


@pytest.fixture
def fake_manifest(sample_png):
    """构造最小 FigureManifest (含 1 张图)."""
    rf = SimpleNamespace(
        fig_id="test_1",
        fig_no=1,
        section="测试",
        title="测试图表",
        key_stat="R2=0.99",
        png_path=sample_png,
        caption="图1 测试图表",
    )
    return _FakeManifest([rf])


@pytest.fixture
def fake_manifest_two(multi_pngs):
    """构造 FigureManifest (含 2 张图)."""
    figs = [
        SimpleNamespace(
            fig_id=f"test_{i}",
            fig_no=i + 1,
            section="测试",
            title=f"图表{i+1}",
            key_stat=f"stat_{i}",
            png_path=multi_pngs[i],
            caption=f"图{i+1} 图表{i+1}",
        )
        for i in range(2)
    ]
    return _FakeManifest(figs)


@pytest.fixture
def empty_pptx(tmp_path):
    """创建空白 pptx 文件。"""
    from pptx import Presentation

    prs = Presentation()
    # 加一页占位 slide (模拟正常报告已有内容)
    prs.slides.add_slide(prs.slide_layouts[0])
    path = tmp_path / "test.pptx"
    prs.save(str(path))
    return str(path)


class TestInjectFiguresToPptx:
    """核心: manifest 有图 → pptx 含 picture shape + caption."""

    def test_single_figure_added(self, empty_pptx, fake_manifest, tmp_path):
        from main import _inject_figures_to_pptx

        warnings: list[str] = []
        _inject_figures_to_pptx(empty_pptx, fake_manifest, warnings)
        assert not warnings  # 无错误

        # 重新打开验证
        from pptx import Presentation
        prs = Presentation(empty_pptx)
        # 原有 1 slide + 1 新 slide = 2
        assert len(prs.slides) == 2

        # 新 slide 应含 picture shape
        new_slide = prs.slides[1]
        picture_shapes = [
            s for s in new_slide.shapes if s.shape_type == 13
        ]  # MSO_SHAPE_TYPE.PICTURE = 13
        assert len(picture_shapes) == 1, "应有一个 picture shape"

        # caption 文本框
        text_shapes = [s for s in new_slide.shapes if s.has_text_frame]
        caption_texts = [
            s.text_frame.text for s in text_shapes if s.text_frame.text
        ]
        assert any("图1" in t for t in caption_texts), "caption 应包含 图1"

    def test_two_figures_two_slides(self, empty_pptx, fake_manifest_two, tmp_path):
        from main import _inject_figures_to_pptx

        warnings: list[str] = []
        _inject_figures_to_pptx(empty_pptx, fake_manifest_two, warnings)
        assert not warnings

        from pptx import Presentation
        prs = Presentation(empty_pptx)
        # 原有 1 + 新 2 = 3 slides
        assert len(prs.slides) == 3

        # 每张新 slide 都有 picture
        for i in (1, 2):
            shapes = prs.slides[i].shapes
            pics = [s for s in shapes if s.shape_type == 13]
            assert len(pics) == 1, f"slide {i} 应有 1 张图"

    def test_png_path_used_directly(self, empty_pptx, fake_manifest, sample_png):
        """png_path 是绝对路径，不依赖 project_dir."""
        from main import _inject_figures_to_pptx

        # sample_png 在 tmp_path 下，manifest 直接引用
        warnings: list[str] = []
        _inject_figures_to_pptx(empty_pptx, fake_manifest, warnings)
        assert not warnings
        # 打开 pptx 确认 picture 存在
        from pptx import Presentation
        prs = Presentation(empty_pptx)
        pics = [s for s in prs.slides[1].shapes if s.shape_type == 13]
        assert len(pics) == 1

    def test_empty_manifest_noop(self, empty_pptx):
        """空 manifest → 不追加 slide、不崩溃."""
        from main import _inject_figures_to_pptx

        empty_manifest = _FakeManifest([])
        warnings: list[str] = []
        _inject_figures_to_pptx(empty_pptx, empty_manifest, warnings)
        assert not warnings

        from pptx import Presentation
        prs = Presentation(empty_pptx)
        assert len(prs.slides) == 1  # 仅占位 slide

    def test_none_manifest_noop(self, empty_pptx):
        """manifest=None → 安全返回."""
        from main import _inject_figures_to_pptx

        warnings: list[str] = []
        _inject_figures_to_pptx(empty_pptx, None, warnings)
        assert not warnings

    def test_nonexistent_png_warns_not_crashes(self, empty_pptx, tmp_path):
        """PNG 路径不存在 → 警告但继续."""
        from main import _inject_figures_to_pptx

        bad_rf = SimpleNamespace(
            fig_id="bad", fig_no=1, section="x", title="bad",
            key_stat="none", png_path="/nonexistent/path.png",
            caption="图1 bad",
        )
        bad_manifest = _FakeManifest([bad_rf])
        warnings: list[str] = []
        _inject_figures_to_pptx(empty_pptx, bad_manifest, warnings)
        assert len(warnings) >= 1
        assert "失败" in warnings[0]
