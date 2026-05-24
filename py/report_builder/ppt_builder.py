"""PowerPoint builder using python-pptx."""
import io
from typing import Optional

from pptx import Presentation
from pptx.util import Inches, Pt
from pptx.enum.text import PP_ALIGN

from .models import ReportSpec, Section, ContentBlock


class PPTSlideBuilder:
    """Builds PowerPoint (.pptx) slides from ReportSpec."""

    def build(self, spec: ReportSpec) -> bytes:
        """
        Generate a PowerPoint presentation from the given ReportSpec.

        Args:
            spec: The report specification containing title, author, date, and sections.

        Returns:
            Bytes representing the PowerPoint document.
        """
        prs = Presentation()

        # Title slide
        self._add_title_slide(prs, spec)

        # Content slides for each section
        for section in spec.sections:
            self._add_section_slide(prs, section)

        # Save to buffer
        buffer = io.BytesIO()
        prs.save(buffer)
        return buffer.getvalue()

    def _add_title_slide(self, prs: Presentation, spec: ReportSpec) -> None:
        """Add a title slide."""
        slide = prs.slides.add_slide(prs.slide_layouts[6])  # Blank layout

        # Title
        left = Inches(1)
        top = Inches(2)
        width = Inches(8)
        height = Inches(1.5)

        title_box = slide.shapes.add_textbox(left, top, width, height)
        title_frame = title_box.text_frame
        title_para = title_frame.paragraphs[0]
        title_para.text = spec.title
        title_para.font.size = Pt(44)
        title_para.alignment = PP_ALIGN.CENTER

        # Author and date
        subtitle_box = slide.shapes.add_textbox(left, Inches(4), width, Inches(1))
        subtitle_frame = subtitle_box.text_frame
        if spec.author:
            author_para = subtitle_frame.paragraphs[0]
            author_para.text = f'Author: {spec.author}'
            author_para.font.size = Pt(24)
            author_para.alignment = PP_ALIGN.CENTER

        if spec.date:
            date_box = slide.shapes.add_textbox(left, Inches(4.8), width, Inches(0.5))
            date_frame = date_box.text_frame
            date_para = date_frame.paragraphs[0]
            date_para.text = f'Date: {spec.date}'
            date_para.font.size = Pt(18)
            date_para.alignment = PP_ALIGN.CENTER

    def _add_section_slide(self, prs: Presentation, section: Section) -> None:
        """Add a section slide with its content blocks."""
        slide = prs.slides.add_slide(prs.slide_layouts[6])  # Blank layout

        # Section title
        title_box = slide.shapes.add_textbox(Inches(0.5), Inches(0.3), Inches(9), Inches(0.8))
        title_frame = title_box.text_frame
        title_para = title_frame.paragraphs[0]
        title_para.text = section.title
        title_para.font.size = Pt(32)
        title_para.font.bold = True

        # Content blocks
        y_offset = 1.3
        for block in section.blocks:
            y_offset = self._add_block(slide, block, y_offset)

    def _add_block(self, slide, block: ContentBlock, y_offset: float) -> float:
        """Add a content block and return the new y offset."""
        if block.type == 'text':
            text_box = slide.shapes.add_textbox(
                Inches(0.5), Inches(y_offset), Inches(9), Inches(0.5)
            )
            text_frame = text_box.text_frame
            text_para = text_frame.paragraphs[0]
            text_para.text = str(block.data)
            text_para.font.size = Pt(14)
            return y_offset + 0.5

        elif block.type == 'chart':
            # block.data is expected to be bytes (image content)
            if block.data:
                try:
                    slide.shapes.add_picture(
                        io.BytesIO(block.data),
                        Inches(1), Inches(y_offset),
                        width=Inches(8)
                    )
                    return y_offset + 4.5  # Approximate height of chart
                except Exception:
                    return y_offset

        elif block.type == 'table':
            # block.data is expected to be list of lists
            return self._add_table(slide, block.data, y_offset)

        elif block.type == 'code':
            text_box = slide.shapes.add_textbox(
                Inches(0.5), Inches(y_offset), Inches(9), Inches(0.5)
            )
            text_frame = text_box.text_frame
            text_para = text_frame.paragraphs[0]
            text_para.text = f'Code: {str(block.data)[:100]}...' if len(str(block.data)) > 100 else str(block.data)
            text_para.font.size = Pt(10)
            text_para.font.name = 'Courier New'
            return y_offset + 0.5

        return y_offset

    def _add_table(self, slide, table_data: list[list], y_offset: float) -> float:
        """Add a table and return the new y offset."""
        if not table_data or not table_data[0]:
            return y_offset

        rows = len(table_data)
        cols = len(table_data[0])

        # Add table
        table = slide.shapes.add_table(
            rows, cols,
            Inches(0.5), Inches(y_offset),
            Inches(9), Inches(min(rows * 0.4, 4))
        ).table

        for i, row in enumerate(table_data):
            for j, cell_value in enumerate(row):
                cell = table.cell(i, j)
                cell.text = str(cell_value) if cell_value is not None else ''

        return y_offset + min(rows * 0.4, 4) + 0.3