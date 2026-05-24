"""Word document builder using python-docx."""
import io
from typing import Optional

from docx import Document
from docx.shared import Inches, Pt
from docx.enum.text import WD_ALIGN_PARAGRAPH

from .models import ReportSpec, Section, ContentBlock


class WordBuilder:
    """Builds Word (.docx) documents from ReportSpec."""

    def build(self, spec: ReportSpec) -> bytes:
        """
        Generate a Word document from the given ReportSpec.

        Args:
            spec: The report specification containing title, author, date, and sections.

        Returns:
            Bytes representing the Word document.
        """
        doc = Document()

        # Title
        title = doc.add_heading(spec.title, 0)
        title.alignment = WD_ALIGN_PARAGRAPH.CENTER

        # Author and date
        if spec.author:
            author_para = doc.add_paragraph(f'Author: {spec.author}')
            author_para.alignment = WD_ALIGN_PARAGRAPH.CENTER
        if spec.date:
            date_para = doc.add_paragraph(f'Date: {spec.date}')
            date_para.alignment = WD_ALIGN_PARAGRAPH.CENTER

        doc.add_paragraph()  # spacing

        # Sections
        for section in spec.sections:
            self._add_section(doc, section)

        # Save to buffer
        buffer = io.BytesIO()
        doc.save(buffer)
        return buffer.getvalue()

    def _add_section(self, doc: Document, section: Section) -> None:
        """Add a section with its blocks to the document."""
        # Section title
        doc.add_heading(section.title, level=1)

        # Content blocks
        for block in section.blocks:
            self._add_block(doc, block)

    def _add_block(self, doc: Document, block: ContentBlock) -> None:
        """Add a content block to the document based on its type."""
        if block.type == 'text':
            doc.add_paragraph(str(block.data))
        elif block.type == 'chart':
            # block.data is expected to be bytes (image content)
            if block.data:
                doc.add_picture(io.BytesIO(block.data), width=Inches(5.5))
        elif block.type == 'table':
            # block.data is expected to be list of lists
            self._add_table(doc, block.data)
        elif block.type == 'code':
            # Add as formatted code block
            code_para = doc.add_paragraph()
            code_run = code_para.add_run(str(block.data))
            code_run.font.name = 'Courier New'
            code_run.font.size = Pt(10)

    def _add_table(self, doc: Document, table_data: list[list]) -> None:
        """Add a table to the document."""
        if not table_data or not table_data[0]:
            return

        rows = len(table_data)
        cols = len(table_data[0])
        table = doc.add_table(rows=rows, cols=cols)

        for i, row in enumerate(table_data):
            for j, cell in enumerate(row):
                table.cell(i, j).text = str(cell) if cell is not None else ''