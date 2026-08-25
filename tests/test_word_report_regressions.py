"""Word 报告缺图与复杂公式渲染回归测试。"""

from __future__ import annotations

import zipfile

from docx import Document

from dp_engine.report_builder.models import WordReport, WordSection
from dp_engine.report_builder.word_builder import WordBuilder


def _media_count(docx_path) -> int:
    with zipfile.ZipFile(docx_path) as archive:
        return sum(
            1 for name in archive.namelist()
            if name.startswith("word/media/") and not name.endswith("/")
        )


def test_complex_matrix_and_cases_do_not_become_raw_latex_images(tmp_path):
    """mathtext 不支持的环境必须降级为可读原生文本，不能把源码画进 PNG。"""
    report = WordReport(
        title="公式回归",
        sections=[
            WordSection(
                heading="复杂公式",
                content_paragraphs=[
                    r"""$$\begin{bmatrix} \epsilon \\ \Delta T \end{bmatrix}
                    = \begin{bmatrix} K_1 & S_1 \\ K_2 & S_2 \end{bmatrix}^{-1}
                    \begin{bmatrix} \Delta\lambda_1 \\ \Delta\lambda_2 \end{bmatrix}$$""",
                    r"""$$f(x)=\begin{cases}x^2, & x \geq 0 \\ -x, & x < 0\end{cases}$$""",
                ],
            )
        ],
    )
    output = tmp_path / "complex-formula.docx"

    WordBuilder().build_word_report(report, "", str(output))

    doc = Document(str(output))
    visible_text = "\n".join(p.text for p in doc.paragraphs)
    assert r"\begin" not in visible_text
    assert r"\Delta" not in visible_text
    assert "ε" in visible_text
    assert "Δλ" in visible_text
    assert _media_count(output) == 0


def test_supported_simple_display_formula_still_renders_as_equation_image(tmp_path):
    """受 mathtext 支持的简单公式继续走清晰公式图路径。"""
    report = WordReport(
        title="简单公式回归",
        sections=[
            WordSection(
                heading="简单公式",
                content_paragraphs=[r"$$\epsilon = K \cdot \Delta\lambda$$"],
            )
        ],
    )
    output = tmp_path / "simple-formula.docx"

    WordBuilder().build_word_report(report, "", str(output))

    assert _media_count(output) == 1
