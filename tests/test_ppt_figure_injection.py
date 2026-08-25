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
import shutil
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

    def test_two_related_figures_share_one_evidence_slide(
        self, empty_pptx, fake_manifest_two, tmp_path
    ):
        from main import _inject_figures_to_pptx

        warnings: list[str] = []
        _inject_figures_to_pptx(empty_pptx, fake_manifest_two, warnings)
        assert not warnings

        from pptx import Presentation
        prs = Presentation(empty_pptx)
        # 同模块两张图合并为一页证据对比，避免附件式一图一页。
        assert len(prs.slides) == 2
        pics = [shape for shape in prs.slides[1].shapes if shape.shape_type == 13]
        assert len(pics) == 2

    def test_temperature_phase_a_and_b_use_separate_evidence_slides(
        self, empty_pptx, multi_pngs
    ):
        """阶段 A 回归和阶段 B 诊断语义不同，不能为了省页数硬拼成一页。"""
        from main import _inject_figures_to_pptx

        figures = [
            SimpleNamespace(
                fig_id="calib_tempA_A1",
                fig_no=1,
                section="temperature_calib",
                title="A1 阶段 A 温度系数回归",
                key_stat="R²=0.99",
                png_path=multi_pngs[0],
                caption="图1 阶段 A 温度系数回归",
            ),
            SimpleNamespace(
                fig_id="calib_tempB_A1",
                fig_no=2,
                section="temperature_calib",
                title="A1 阶段 B 诊断四联图",
                key_stat="grade=优",
                png_path=multi_pngs[1],
                caption="图2 阶段 B 诊断四联图",
            ),
        ]
        warnings: list[str] = []

        _inject_figures_to_pptx(
            empty_pptx,
            _FakeManifest(figures),
            warnings,
        )

        assert not warnings
        from pptx import Presentation

        prs = Presentation(empty_pptx)
        evidence_picture_counts = [
            sum(shape.shape_type == 13 for shape in slide.shapes)
            for slide in list(prs.slides)[1:]
        ]
        assert evidence_picture_counts == [1, 1]

    def test_temperature_evidence_prefers_slide_title_over_cross_reference(
        self, multi_pngs, tmp_path
    ):
        """阶段 A 正文即使提到阶段 B，也不能吸走阶段 B 证据页。"""
        from pptx import Presentation

        prs = Presentation()
        for title, body in (
            ("封面", ""),
            (
                "阶段 A 回归验证温度灵敏度",
                "阶段 A 图与阶段 B 诊断图分开呈现",
            ),
            ("阶段 B 诊断揭示补偿后性能", "补偿残差与评级"),
        ):
            slide = prs.slides.add_slide(prs.slide_layouts[1])
            slide.shapes.title.text = title
            body_shape = slide.placeholders[1]
            body_shape.text = body
        pptx_path = tmp_path / "temperature-sections.pptx"
        prs.save(pptx_path)

        figures = [
            SimpleNamespace(
                fig_id="calib_tempA_A1",
                fig_no=1,
                section="temperature_calib",
                title="A1 阶段 A 温度系数回归",
                key_stat="R²=0.99",
                png_path=multi_pngs[0],
                caption="图1 阶段 A 温度系数回归",
            ),
            SimpleNamespace(
                fig_id="calib_tempB_A1",
                fig_no=2,
                section="temperature_calib",
                title="A1 阶段 B 诊断四联图",
                key_stat="grade=优",
                png_path=multi_pngs[1],
                caption="图2 阶段 B 诊断四联图",
            ),
        ]
        warnings: list[str] = []

        from main import _inject_figures_to_pptx

        _inject_figures_to_pptx(
            str(pptx_path),
            _FakeManifest(figures),
            warnings,
        )

        assert not warnings
        updated = Presentation(str(pptx_path))
        picture_indexes = [
            index
            for index, slide in enumerate(updated.slides)
            if any(shape.shape_type == 13 for shape in slide.shapes)
        ]
        assert picture_indexes == [2, 4]

    def test_phase_b_raw_compensated_pages_never_mix_sensors(
        self, empty_pptx, multi_pngs, tmp_path
    ):
        """某一版已被模型选中后，兜底页也不能把 A1 补偿图和 A2 原始图拼在一起。"""
        copied_paths: list[str] = []
        for index in range(4):
            target = tmp_path / f"phaseb_{index}.png"
            shutil.copyfile(multi_pngs[index % 2], target)
            copied_paths.append(str(target))
        specs = (
            ("A1", "raw", "原始（补偿前）"),
            ("A1", "compensated", "补偿后"),
            ("A2", "raw", "原始（补偿前）"),
            ("A2", "compensated", "补偿后"),
        )
        figures = [
            SimpleNamespace(
                fig_id=f"phaseb_diagnostic_{sensor}_{variant}",
                fig_no=index + 1,
                section="temperature_calib",
                title=f"{sensor} 阶段 B {label}诊断四联图",
                key_stat="n=100",
                png_path=copied_paths[index],
                caption=f"图{index + 1} {sensor} 阶段 B {label}诊断四联图",
            )
            for index, (sensor, variant, label) in enumerate(specs)
        ]
        warnings: list[str] = []

        from main import _inject_figures_to_pptx

        _inject_figures_to_pptx(
            empty_pptx,
            _FakeManifest(figures),
            warnings,
            assigned_filenames={os.path.basename(copied_paths[0])},
        )

        assert not warnings
        from pptx import Presentation

        prs = Presentation(empty_pptx)
        for slide in list(prs.slides)[1:]:
            captions = " ".join(
                shape.text
                for shape in slide.shapes
                if getattr(shape, "has_text_frame", False)
            )
            assert not ("A1" in captions and "A2" in captions)

    def test_calibration_evidence_follows_domain_order(
        self, sample_png, tmp_path
    ):
        """同一标定章节内固定为阶段 A→阶段 B 传感器顺序→应变标定。"""
        from pptx import Presentation

        prs = Presentation()
        for title in (
            "封面",
            "标定致命缺陷：迟滞超标并触碰废品拦截线",
            "总结",
        ):
            slide = prs.slides.add_slide(prs.slide_layouts[5])
            slide.shapes.title.text = title
        pptx_path = tmp_path / "calibration-order.pptx"
        prs.save(pptx_path)

        figures = [
            SimpleNamespace(
                fig_id="phaseb_A2_raw",
                fig_no=1,
                section="temperature_calib",
                title="A2 阶段 B 原始（补偿前）诊断四联图",
                key_stat="",
                png_path=sample_png,
                caption="图1 A2 原始",
            ),
            SimpleNamespace(
                fig_id="phaseb_A1_raw",
                fig_no=2,
                section="temperature_calib",
                title="A1 阶段 B 原始（补偿前）诊断四联图",
                key_stat="",
                png_path=sample_png,
                caption="图2 A1 原始",
            ),
            SimpleNamespace(
                fig_id="tempA",
                fig_no=3,
                section="temperature_calib",
                title="阶段 A 温度系数回归",
                key_stat="",
                png_path=sample_png,
                caption="图3 阶段 A",
            ),
            SimpleNamespace(
                fig_id="strain_A1",
                fig_no=4,
                section="strain_calib",
                title="A1 应变标定曲线",
                key_stat="",
                png_path=sample_png,
                caption="图4 应变标定",
            ),
        ]
        warnings: list[str] = []

        from main import _inject_figures_to_pptx

        _inject_figures_to_pptx(
            str(pptx_path),
            _FakeManifest(figures),
            warnings,
        )

        assert not warnings
        updated = Presentation(str(pptx_path))
        evidence_titles = [
            next(
                (
                    shape.text.strip()
                    for shape in slide.shapes
                    if getattr(shape, "has_text_frame", False)
                    and shape.text.strip()
                ),
                "",
            )
            for slide in list(updated.slides)[2:-1]
        ]
        assert evidence_titles == [
            "温度标定｜阶段 A 回归证据",
            "温度标定｜A1 阶段 B 补偿前后对比",
            "温度标定｜A2 阶段 B 补偿前后对比",
            "应变标定｜曲线证据",
        ]

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

    def test_figure_evidence_slide_is_inserted_after_matching_content(
        self, sample_png, tmp_path
    ):
        """补充证据页必须靠近对应章节，不能统一追加到总结之后。"""
        from pptx import Presentation

        prs = Presentation()
        for title in ("封面", "数据分析", "多源对比与相关性", "总结与决策"):
            slide = prs.slides.add_slide(prs.slide_layouts[5])
            slide.shapes.title.text = title
        pptx_path = tmp_path / "sectioned.pptx"
        prs.save(pptx_path)

        figure = SimpleNamespace(
            fig_id="compare_ol",
            fig_no=1,
            section="compare",
            title="多源对比时程",
            key_stat="4源",
            png_path=sample_png,
            caption="图1 多源对比时程",
        )
        warnings: list[str] = []

        from main import _inject_figures_to_pptx

        _inject_figures_to_pptx(
            str(pptx_path),
            _FakeManifest([figure]),
            warnings,
        )

        updated = Presentation(str(pptx_path))
        titles = [
            slide.shapes.title.text if slide.shapes.title is not None else ""
            for slide in updated.slides
        ]
        picture_indexes = [
            index
            for index, slide in enumerate(updated.slides)
            if any(shape.shape_type == 13 for shape in slide.shapes)
        ]
        assert picture_indexes == [3]
        assert titles[2] == "多源对比与相关性"
        assert titles[4] == "总结与决策"

    def test_cover_title_token_cannot_absorb_evidence_slide(
        self, sample_png, tmp_path
    ):
        """封面含“应变”等图题词时，证据仍须放在匹配正文之后。"""
        from pptx import Presentation

        prs = Presentation()
        cover = prs.slides.add_slide(prs.slide_layouts[5])
        cover.shapes.title.text = "光纤光栅应变传感器诊断报告"
        evidence_section = prs.slides.add_slide(prs.slide_layouts[1])
        evidence_section.shapes.title.text = "立即执行三项排查，锁定异常根因"
        evidence_section.placeholders[1].text = "补充四路同窗对比与相关性证据"
        summary = prs.slides.add_slide(prs.slide_layouts[5])
        summary.shapes.title.text = "总结与决策"
        pptx_path = tmp_path / "cover-token-collision.pptx"
        prs.save(pptx_path)

        figure = SimpleNamespace(
            fig_id="compare_strain_scatter",
            fig_no=1,
            section="compare",
            title="应变-光纤1与应变片相关散点",
            key_stat="r=0.46",
            png_path=sample_png,
            caption="图1 应变通道相关散点",
        )
        warnings: list[str] = []

        from main import _inject_figures_to_pptx

        _inject_figures_to_pptx(
            str(pptx_path),
            _FakeManifest([figure]),
            warnings,
        )

        assert not warnings
        updated = Presentation(str(pptx_path))
        picture_indexes = [
            index
            for index, slide in enumerate(updated.slides)
            if any(shape.shape_type == 13 for shape in slide.shapes)
        ]
        assert picture_indexes == [2]
