"""Report flow controller orchestrating AI generation, template rendering, and output."""
from typing import Optional, Callable
from datetime import datetime

from .models import ReportSpec, Section, ContentBlock
from .template_engine import TemplateEngine
from .word_builder import WordBuilder
from .ppt_builder import PPTSlideBuilder


class ReportFlowController:
    """Orchestrates the report generation flow: AI → Template → Word/PPT output."""

    def __init__(self):
        self.template_engine = TemplateEngine()
        self.word_builder = WordBuilder()
        self.ppt_builder = PPTSlideBuilder()

    def create_report_spec(
        self,
        title: str,
        author: str,
        sections: list[Section],
        date: Optional[str] = None
    ) -> ReportSpec:
        """
        Create a ReportSpec with the given parameters.

        Args:
            title: Report title.
            author: Report author.
            sections: List of sections.
            date: Optional date string. Defaults to current date.

        Returns:
            A new ReportSpec instance.
        """
        if date is None:
            date = datetime.now().strftime('%Y-%m-%d')

        return ReportSpec(
            title=title,
            author=author,
            date=date,
            sections=sections
        )

    def build_word_report(
        self,
        spec: ReportSpec,
        template_path: Optional[str] = None
    ) -> bytes:
        """
        Build a Word report from the spec, optionally using a template.

        Args:
            spec: The report specification.
            template_path: Optional path to a Word template.

        Returns:
            Bytes representing the Word document.
        """
        # Apply template if provided
        if template_path:
            spec = self.template_engine.render(template_path, spec)

        # Build the Word document
        return self.word_builder.build(spec)

    def build_ppt_report(
        self,
        spec: ReportSpec,
        template_path: Optional[str] = None
    ) -> bytes:
        """
        Build a PowerPoint report from the spec, optionally using a template.

        Args:
            spec: The report specification.
            template_path: Optional path to a PowerPoint template.

        Returns:
            Bytes representing the PowerPoint document.
        """
        # Apply template if provided
        if template_path:
            spec = self.template_engine.render(template_path, spec)

        # Build the PowerPoint
        return self.ppt_builder.build(spec)

    def generate_with_ai_content(
        self,
        title: str,
        author: str,
        ai_content: list[str],
        charts: list[bytes],
        tables: list[list],
        report_type: str = 'word'
    ) -> bytes:
        """
        Generate a report with AI-generated content.

        Args:
            title: Report title.
            author: Report author.
            ai_content: List of text content from AI.
            charts: List of chart images as bytes.
            tables: List of table data (list of lists).
            report_type: 'word' or 'ppt'.

        Returns:
            Bytes representing the generated report.
        """
        # Build sections from AI content
        sections = []

        # Section 1: Overview (AI text content)
        if ai_content:
            overview_blocks = [ContentBlock(type='text', data=content) for content in ai_content]
            sections.append(Section(title='Overview', blocks=overview_blocks))

        # Section 2: Charts
        if charts:
            chart_blocks = [ContentBlock(type='chart', data=chart) for chart in charts]
            sections.append(Section(title='Analysis Charts', blocks=chart_blocks))

        # Section 3: Data Tables
        if tables:
            table_blocks = [ContentBlock(type='table', data=table) for table in tables]
            sections.append(Section(title='Data Tables', blocks=table_blocks))

        # Create spec
        spec = self.create_report_spec(title, author, sections)

        # Build report
        if report_type == 'word':
            return self.build_word_report(spec)
        else:
            return self.build_ppt_report(spec)