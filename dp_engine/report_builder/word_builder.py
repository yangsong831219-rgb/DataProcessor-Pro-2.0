# pyright: reportGeneralTypeIssues=false
# python-docx stub: Document() 被误识为函数签名而非类构造器，运行时正确
"""Word document builder — 消费 WordReport JSON，渲染 .docx。

只读取结构化数据，严禁在此文件中调用任何 LLM。
[INSERT_IMAGE: filename] 标签自动从项目资料目录插入图片。
markdown 片段 (#/##/###/####/**/- /1.) → Word 格式渲染。
"""

from __future__ import annotations

import io
import os
import re
from pathlib import Path
from typing import Optional

from docx import Document
from docx.shared import Inches, Pt, RGBColor, Cm
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml.ns import qn

from .models import WordReport, WordSection, WordTable


# 图片插入标签格式
IMAGE_ANCHOR_RE = re.compile(r'\[INSERT_IMAGE:\s*([^\]]+)\]')

# 段落内嵌图片标签 — re.split 专用模式
IMAGE_PATTERN = r"\[INSERT_IMAGE:\s*(.+?)\]"

# markdown 模式
_MD_H1 = re.compile(r'^#\s+(.+)$')
_MD_H2 = re.compile(r'^##\s+(.+)$')
_MD_H3 = re.compile(r'^###\s+(.+)$')
_MD_H4 = re.compile(r'^####\s+(.+)$')
_MD_BOLD = re.compile(r'\*\*(.+?)\*\*')
_MD_UL = re.compile(r'^[-*]\s+(.+)$')
_MD_OL = re.compile(r'^\d+\.\s+(.+)$')
# _MD_TABLE_SEP removed — replaced by _is_md_table_sep_cell()  # table header separator

# ── 章标题去重 ──

def _normalize_heading(text: str) -> str:
    """归一化标题文本：去序号(一、1.１.)、标点、空白，取核心文字。"""
    import re as _re
    t = _re.sub(r'^[#\s]+', '', text.strip())
    t = _re.sub(r'^[\d一二三四五六七八九十百]+[\.\、\)\．]\s*', '', t)
    t = _re.sub(r'^[\(（][^\)）]*[\)）]\s*', '', t)
    return t.strip().lower()


# ── LaTeX → Word 公式 (OMML) ──

_LATEX_DISPLAY = re.compile(r'(?:(?<!\\)\$\$|\\\[)(.+?)(?:(?<!\\)\$\$|\\\])', re.DOTALL)
_LATEX_INLINE = re.compile(r'(?:(?<!\\)\$|\\\()(.+?)(?:(?<!\\)\$|\\\))')
_LATEX_KEYWORDS = {
    r'\frac': 'f', r'\sqrt': 'rad', r'\bar': 'bar',
    r'\hat': 'hat', r'\ddot': 'ddot',
}

# ── Markdown 表格 → Word 表格 ──

_MD_TABLE_ROW = re.compile(r'^\s*\|(.+)\|\s*$')
_MD_TABLE_SEP = re.compile(r'^\s*\|?\s*:?-{2,}:?\s*\|')


def _is_md_table_sep_cell(cell: str) -> bool:
    """cell 是否为分隔行 (--- 或 :---: 等)。"""
    return bool(re.match(r'^:?-{2,}:?$', cell.strip()))


def _extract_md_tables(text: str) -> list[tuple[int, int, list[list[str]]]]:
    """提取 markdown 表格块 (start_pos, end_pos, rows)。含粗体/多列/全角标点。"""
    results = []
    lines = text.split('\n')
    i = 0
    while i < len(lines):
        start = i
        rows = []
        has_sep = False
        while i < len(lines):
            ln = lines[i].strip()
            if not ln.startswith('|'):
                break
            cells = [c.strip() for c in ln.strip('|').split('|')]
            if len(cells) < 2:
                break
            if all(_is_md_table_sep_cell(c) for c in cells if c):
                has_sep = True
                i += 1; continue
            rows.append(cells); i += 1
        if len(rows) >= 2 and has_sep:
            start_pos = sum(len(l)+1 for l in lines[:start])
            end_pos = sum(len(l)+1 for l in lines[:i])
            results.append((start_pos, end_pos, rows))
        else:
            i = start + 1
    return results

def _render_latex_display(doc, expr: str) -> None:
    """渲染 display LaTeX 公式为居中图片。"""
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    import io as _io
    try:
        fig, ax = plt.subplots(figsize=(6, 0.6))
        ax.text(0.5, 0.5, f'${expr}$', transform=ax.transAxes,
                fontsize=12, ha='center', va='center')
        ax.axis('off')
        buf = _io.BytesIO()
        fig.savefig(buf, format='png', dpi=150, bbox_inches='tight')
        plt.close(fig)
        buf.seek(0)
        p = doc.add_paragraph()
        run = p.add_run()
        run.add_picture(buf, width=Inches(4))
        p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    except Exception:
        doc.add_paragraph(f'[公式: {expr}]')


def _latex_inline_to_unicode(expr: str) -> str:
    """将常见 LaTeX inline 转 Unicode 近似字符。"""
    mapping = {
        r'\alpha': 'α', r'\beta': 'β', r'\gamma': 'γ', r'\delta': 'δ',
        r'\epsilon': 'ε', r'\varepsilon': 'ε',
        r'\mu': 'μ', r'\sigma': 'σ', r'\Sigma': 'Σ',
        r'\lambda': 'λ', r'\Delta': 'Δ', r'\Lambda': 'Λ',
        r'\pi': 'π', r'\theta': 'θ', r'\omega': 'ω', r'\Omega': 'Ω',
        r'\phi': 'φ', r'\tau': 'τ', r'\eta': 'η', r'\xi': 'ξ',
        r'\partial': '∂', r'\infty': '∞', r'\approx': '≈',
        r'\cdot': '·', r'\times': '×', r'\pm': '±', r'\mp': '∓',
        r'\leq': '≤', r'\geq': '≥', r'\neq': '≠',
        r'\rightarrow': '→', r'\leftarrow': '←',
        r'\text': '', r'\,': ' ', r'\;': ' ', r'\!': '',
        '^T': 'ᵀ', r'^\circ': '°', r'\degree': '°',
    }
    result = expr.strip()
    # 处理下标 _x → Unicode subscript
    result = re.sub(r'_(\w)', lambda m: {
        '0':'₀','1':'₁','2':'₂','3':'₃','4':'₄','5':'₅','6':'₆','7':'₇','8':'₈','9':'₉',
        'a':'ₐ','e':'ₑ','i':'ᵢ','o':'ₒ','u':'ᵤ',
        'x':'ₓ','s':'ₛ','t':'ₜ','n':'ₙ','m':'ₘ','k':'ₖ',
        'p':'ₚ','c':'𝒸','r':'ᵣ',
    }.get(m.group(1), m.group(1)), result)
    # 处理上标 ^x → Unicode superscript
    result = re.sub(r'\^(\w)', lambda m: {
        '0':'⁰','1':'¹','2':'²','3':'³','4':'⁴','5':'⁵','6':'⁶','7':'⁷','8':'⁸','9':'⁹',
        'a':'ᵃ','e':'ᵉ','i':'ⁱ','o':'ᵒ','u':'ᵘ',
        'x':'ˣ','n':'ⁿ','T':'ᵀ',
    }.get(m.group(1), m.group(1)), result)
    # 替换关键词
    for k, v in mapping.items():
        result = result.replace(k, v)
    # 剥掉花括号
    result = result.replace('{', '').replace('}', '')
    # 清理裸 \\frac... → "[分数]"
    result = re.sub(r'\\frac\{[^}]*\}\{[^}]*\}', '[分数]', result)
    result = re.sub(r'\\sqrt(\[.*?\])?\{([^}]*)\}', r'√(\2)', result)
    return result


def _render_markdown_with_latex(builder: 'WordBuilder', doc, text: str,
                                project_dir: str = '',
                                heading_offset: int = 0) -> None:
    """渲染 markdown 文本，预处理 LaTeX 公式 → display 图片 / inline Unicode。"""
    if not text:
        return
    parts_disp = []
    last = 0
    for m in _LATEX_DISPLAY.finditer(text):
        if last < m.start():
            parts_disp.append(('text', text[last:m.start()]))
        parts_disp.append(('display', m.group(1).strip()))
        last = m.end()
    if last < len(text):
        parts_disp.append(('text', text[last:]))

    for typ, content in parts_disp:
        if typ == 'display':
            _render_latex_display(doc, content)
        else:
            parts_inl = []
            last2 = 0
            for m in _LATEX_INLINE.finditer(content):
                if last2 < m.start():
                    parts_inl.append(content[last2:m.start()])
                parts_inl.append(_latex_inline_to_unicode(m.group(1)))
                last2 = m.end()
            if last2 < len(content):
                parts_inl.append(content[last2:])
            cleaned = ''.join(parts_inl)

            if '\\' not in cleaned or not cleaned.strip():
                builder._add_rich_paragraph(doc, cleaned, project_dir, heading_offset)
            else:
                for line in cleaned.split('\n'):
                    builder._render_one_md_line(doc, line, project_dir, heading_offset)


def _render_md_table_as_word(doc, rows):
    """渲染为 Word 表格 (Table Grid 样式, 单元格内 **bold** + LaTeX inline 支持)。"""
    import matplotlib
    matplotlib.use('Agg')
    if not rows:
        return
    n_rows, n_cols = len(rows), max(len(r) for r in rows)
    tbl = doc.add_table(rows=n_rows, cols=n_cols)
    tbl.style = 'Table Grid'
    for ri, row in enumerate(rows):
        for ci, val in enumerate(row):
            # LaTeX inline → Unicode 先行
            val_clean = _latex_inline_to_unicode(val) if '\\' in val else val
            cell = tbl.cell(ri, ci)
            cell.text = ''
            p = cell.paragraphs[0]
            parts = _MD_BOLD.split(val_clean)
            for pi, part in enumerate(parts):
                if not part:
                    continue
                run = p.add_run(part)
                if ri == 0 or pi % 2 == 1:
                    run.bold = True
    doc.add_paragraph()





def _strip_duplicate_chapter_heading(body: str, chapter_title: str) -> str:
    """若正文体首行为与章标题相同/高度相似的标题 → 剥离。"""
    if not body or not chapter_title:
        return body
    lines = body.split('\n')
    if not lines:
        return body
    first = lines[0].strip()
    # 只处理首行是 heading 的情况
    if not re.match(r'^#{1,6}\s', first):
        return body
    # 剥掉 # 号
    h_text = re.sub(r'^#+\s*', '', first)
    norm_h = _normalize_heading(h_text)
    norm_c = _normalize_heading(chapter_title)
    if norm_h == norm_c or (len(norm_h) > 3 and len(norm_c) > 3 and
                             (norm_h in norm_c or norm_c in norm_h)):
        return '\n'.join(lines[1:])
    return body


class WordBuilder:
    """从 WordReport 结构体构建 .docx 文档。"""

    DEFAULT_ASSET_DIRS = ["wiki_vault/assets", "wiki_vault/pages", "project_files"]

    def __init__(self, asset_search_paths: list[str] | None = None):
        self.asset_paths = asset_search_paths or self.DEFAULT_ASSET_DIRS
        self._project_root = Path(__file__).parent.parent.parent
        self.missing_images: list[str] = []

    # ── 主入口 ──

    def build(self, report: WordReport) -> bytes:
        doc = Document()
        self._apply_styles(doc)
        if report.template_path and Path(report.template_path).exists():
            doc = self._load_template(report.template_path)

        self._add_title_page(doc, report)
        for section in report.sections:
            self._add_section(doc, section)

        buffer = io.BytesIO()
        doc.save(buffer)
        return buffer.getvalue()

    def build_from_json(self, json_data: dict) -> bytes:
        report = WordReport.from_dict(json_data)
        return self.build(report)

    # ── 版式 ──

    def _apply_styles(self, doc: Document) -> None:
        """统一正文字体、字号、行距、页边距。"""
        sections = doc.sections
        for sec in sections:
            sec.top_margin = Cm(2.54)
            sec.bottom_margin = Cm(2.54)
            sec.left_margin = Cm(2.54)
            sec.right_margin = Cm(2.54)

        style = doc.styles['Normal']
        font = style.font
        font.name = 'Microsoft YaHei'
        font.size = Pt(11)
        style.element.rPr.rFonts.set(qn('w:eastAsia'), 'Microsoft YaHei')
        pf = style.paragraph_format
        pf.space_before = Pt(6)
        pf.space_after = Pt(4)
        pf.line_spacing = 1.15

    # ── 模板加载 ──

    def _load_template(self, template_path: str) -> Document:
        doc = Document(template_path)
        return doc

    # ── 标题页 ──

    def _add_title_page(self, doc: Document, report: WordReport) -> None:
        title = doc.add_heading(report.title, 0)
        title.alignment = WD_ALIGN_PARAGRAPH.CENTER
        for run in title.runs:
            run.font.name = 'SimHei'
            run._element.rPr.rFonts.set(qn('w:eastAsia'), 'SimHei')

        if report.author:
            p = doc.add_paragraph(f'Author: {report.author}')
            p.alignment = WD_ALIGN_PARAGRAPH.CENTER
        if report.date:
            p = doc.add_paragraph(f'Date: {report.date}')
            p.alignment = WD_ALIGN_PARAGRAPH.CENTER

        doc.add_paragraph()

    # ── 章节渲染 ──

    def _add_section(self, doc: Document, section: WordSection, project_dir: str = '') -> None:
        heading = doc.add_heading(section.heading, level=1)
        for run in heading.runs:
            run.font.name = 'SimHei'
            run._element.rPr.rFonts.set(qn('w:eastAsia'), 'SimHei')

        for para_text in section.content_paragraphs:
            # 章标题去重: 正文体首个标题行若与章标题相同 → 剥离
            cleaned = _strip_duplicate_chapter_heading(para_text, section.heading)
            _render_markdown_with_latex(self, doc, cleaned, project_dir=project_dir,
                                             heading_offset=(
                                                 2 - self._find_min_heading_level(cleaned)
                                                 if self._find_min_heading_level(cleaned) > 0
                                                 else 1))

        for anchor in section.image_anchors:
            filename = self._extract_image_filename(anchor)
            if filename:
                self._insert_image(doc, filename, project_dir=project_dir)

        for table in section.tables:
            self._add_table(doc, table)

    # ── markdown → Word 渲染 ──

    def _add_rich_paragraph(self, doc: Document, text: str,
                            project_dir: str = '',
                            heading_offset: int = 0) -> None:
        """处理段落中的 markdown + 表格，渲染为 Word 元素。

        表格块（连续 `| … |` 行）→ Word 表格；其余 → IMAGE→Heading→List→Bold。
        heading_offset: 标题级偏移 (1 = #→H2)
        """
        if not text:
            return

        # ── Markdown 表格检测 ──
        table_blocks = _extract_md_tables(text)
        cursor = 0
        for tb_start, tb_end, md_table in table_blocks:
            # 渲染表格之前的文本
            pre = text[cursor:tb_start].strip()
            if pre:
                for line in pre.split('\n'):
                    self._render_one_md_line(doc, line, project_dir, heading_offset)
            # 渲染表格为 Word 表格
            _render_md_table_as_word(doc, md_table)
            cursor = tb_end
        # 尾部文本
        tail = text[cursor:].strip()
        if tail:
            for line in tail.split('\n'):
                self._render_one_md_line(doc, line, project_dir, heading_offset)

    def _find_min_heading_level(self, text: str) -> int:
        """找出文本中使用的最大 # 号 (最深级数字最大)，返回最小级数。
        如文本用了 ## 和 ### → min=2。无标题 → 返回 0。
        """
        import re as _re2
        levels = set()
        for line in text.split('\n'):
            m = _re2.match(r'^(#{1,6})\s', line.strip())
            if m:
                levels.add(len(m.group(1)))
        return min(levels) if levels else 0

    def _render_one_md_line(self, doc: Document, line: str,
                            project_dir: str, heading_offset: int) -> None:
        """渲染单行 markdown：IMAGE / Heading / List / Bold 段落。"""
        if not line.strip():
            return
        if re.search(IMAGE_PATTERN, line):
            self._add_paragraph_with_images(doc, line, project_dir=project_dir)
            return

        # Heading
        for pattern, level in [(_MD_H1, 1), (_MD_H2, 2), (_MD_H3, 3), (_MD_H4, 4)]:
            m = pattern.match(line)
            if m:
                lvl = min(level + heading_offset, 6)
                h = doc.add_heading(m.group(1), level=lvl)
                for run in h.runs:
                    run.font.name = 'SimHei'
                    run._element.rPr.rFonts.set(qn('w:eastAsia'), 'SimHei')
                return

        m_ul = _MD_UL.match(line)
        if m_ul:
            p = doc.add_paragraph(style='List Bullet')
            self._add_md_runs(p, m_ul.group(1))
            return
        m_ol = _MD_OL.match(line)
        if m_ol:
            p = doc.add_paragraph(style='List Number')
            self._add_md_runs(p, m_ol.group(1))
            return

        p = doc.add_paragraph()
        self._add_md_runs(p, line)

    def _add_md_runs(self, para, text: str) -> None:
        """向段落添加带 **bold** 处理的 run。"""
        parts = _MD_BOLD.split(text)
        for i, part in enumerate(parts):
            if not part:
                continue
            if i % 2 == 1:
                run = para.add_run(part)
                run.bold = True
            else:
                para.add_run(part)

    # ── 表格渲染 (bold 支持) ──

    def _add_table(self, doc: Document, table: WordTable) -> None:
        if not table.rows:
            return
        rows_count = len(table.rows)
        cols_count = max((len(r) for r in table.rows), default=1)
        if table.headers:
            cols_count = max(cols_count, len(table.headers))

        tbl = doc.add_table(rows=rows_count + (1 if table.headers else 0),
                            cols=cols_count)
        tbl.style = 'Light Grid Accent 1'

        start_row = 0
        if table.headers:
            for ci, h in enumerate(table.headers):
                cell = tbl.cell(0, ci)
                cell.text = str(h)
                for p in cell.paragraphs:
                    for r in p.runs:
                        r.bold = True
            start_row = 1

        for ri, row_data in enumerate(table.rows):
            for ci, val in enumerate(row_data):
                cell = tbl.cell(ri + start_row, ci)
                cell.text = ''  # clear
                p = cell.paragraphs[0]
                self._add_md_runs(p, str(val) if val is not None else '')

    # ── 段落 + 内联图片 (保留，_add_rich_paragraph 的降级路径) ──

    def _add_paragraph_with_images(self, doc: Document, text: str,
                                   project_dir: str = '') -> None:
        if not re.search(IMAGE_PATTERN, text):
            doc.add_paragraph(text)
            return

        parts = re.split(IMAGE_PATTERN, text)
        para = doc.add_paragraph()
        for i, part in enumerate(parts):
            if i % 2 == 1:
                filename = part.strip()
                img_path = os.path.join(project_dir, filename) if project_dir else ''
                if img_path and os.path.isfile(img_path):
                    try:
                        run = para.add_run()
                        run.add_picture(img_path, width=Inches(5.0))
                    except Exception:
                        self.missing_images.append(filename)
                        run = para.add_run(f'[图片插入失败: {filename}]')
                        run.font.color.rgb = RGBColor(0xFF, 0x00, 0x00)
                else:
                    self.missing_images.append(filename)
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

    def _insert_image(self, doc: Document, filename: str,
                      project_dir: str = '') -> bool:
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
            except Exception:
                self.missing_images.append(filename)
                p = doc.add_paragraph(f'[图片加载失败: {filename}]')
                for r in p.runs:
                    r.font.color.rgb = RGBColor(0xFF, 0x00, 0x00)
        else:
            self.missing_images.append(filename)
            p = doc.add_paragraph(f'[图片未找到: {filename}]')
            for r in p.runs:
                r.font.color.rgb = RGBColor(0xFF, 0x00, 0x00)
        return False

    def _insert_image_inline(self, para, filename: str) -> bool:
        img_path = self._resolve_image_path(filename)
        if img_path:
            try:
                run = para.add_run()
                run.add_picture(str(img_path), width=Inches(5.0))
                return True
            except Exception:
                return False
        return False

    def _resolve_image_path(self, filename: str) -> Path | None:
        for ad in self.asset_paths:
            search_dir = self._project_root / ad
            candidate = search_dir / filename
            if candidate.is_file():
                return candidate
        return None

    # ── build_word_report — 供 main.py 调用 ──

    def build_word_report(
        self,
        report_data: WordReport,
        template_path: str,
        output_path: str,
        project_dir: str = '',
    ) -> str:
        # 1. Load template
        doc = Document(template_path) if template_path else Document()
        self._apply_styles(doc)

        # 2. Template placeholder replacement
        self._replace_placeholders(doc, report_data)

        # 3. Title page
        self._add_title_page(doc, report_data)

        # 4. Sections
        for section in report_data.sections:
            self._add_section(doc, section, project_dir=project_dir)

        # 5. Save
        os.makedirs(os.path.dirname(output_path) or '.', exist_ok=True)
        doc.save(output_path)
        return output_path

    def _replace_placeholders(self, doc: Document, report: WordReport) -> None:
        """替换模板中的占位符 {{...}}"""
        replacements = {
            'title': report.title or '',
            'author': report.author or '',
            'date': report.date or '',
        }
        for para in doc.paragraphs:
            for key, val in replacements.items():
                placeholder = f'{{{{{key}}}}}'
                if placeholder in para.text:
                    for run in para.runs:
                        if placeholder in (run.text or ''):
                            run.text = run.text.replace(placeholder, str(val))
