"""Tests for WordBuilder."""
import pytest
import io

from docx import Document

from report_builder.models import ReportSpec, Section, ContentBlock
from report_builder.word_builder import WordBuilder


class TestWordBuilder:
    """Test suite for WordBuilder."""

    def test_build_empty_spec(self):
        """Test building a minimal report spec."""
        spec = ReportSpec(
            title='Test Report',
            author='Test Author',
            date='2024-01-01',
            sections=[]
        )

        builder = WordBuilder()
        result = builder.build(spec)

        assert isinstance(result, bytes)
        assert len(result) > 0

        # Verify can open as docx
        doc = Document(io.BytesIO(result))
        assert doc is not None

    def test_build_with_text_content(self):
        """Test building a spec with text blocks."""
        spec = ReportSpec(
            title='Data Analysis Report',
            author='John Doe',
            date='2024-01-15',
            sections=[
                Section(
                    title='Overview',
                    blocks=[
                        ContentBlock(type='text', data='This is the overview text.')
                    ]
                )
            ]
        )

        builder = WordBuilder()
        result = builder.build(spec)

        doc = Document(io.BytesIO(result))

        # Check title
        assert any(p.text == 'Data Analysis Report' for p in doc.paragraphs)

        # Check section title
        assert any(p.text == 'Overview' for p in doc.paragraphs)

        # Check content
        assert any('overview' in p.text.lower() for p in doc.paragraphs)

    def test_build_with_table(self):
        """Test building a spec with table data."""
        table_data = [
            ['Name', 'Value', 'Status'],
            ['Item 1', '100', 'Active'],
            ['Item 2', '200', 'Inactive'],
        ]

        spec = ReportSpec(
            title='Report with Table',
            author='Jane Doe',
            date='2024-01-20',
            sections=[
                Section(
                    title='Data',
                    blocks=[
                        ContentBlock(type='table', data=table_data)
                    ]
                )
            ]
        )

        builder = WordBuilder()
        result = builder.build(spec)

        doc = Document(io.BytesIO(result))

        # Find the table
        tables = doc.tables
        assert len(tables) == 1

        table = tables[0]
        assert len(table.rows) == 3
        assert len(table.columns) == 3

        # Check first row
        assert table.cell(0, 0).text == 'Name'
        assert table.cell(1, 0).text == 'Item 1'

    def test_build_with_multiple_sections(self):
        """Test building a spec with multiple sections."""
        spec = ReportSpec(
            title='Multi-Section Report',
            author='Tester',
            date='2024-02-01',
            sections=[
                Section(
                    title='Section 1',
                    blocks=[
                        ContentBlock(type='text', data='Content for section 1')
                    ]
                ),
                Section(
                    title='Section 2',
                    blocks=[
                        ContentBlock(type='text', data='Content for section 2')
                    ]
                ),
            ]
        )

        builder = WordBuilder()
        result = builder.build(spec)

        doc = Document(io.BytesIO(result))
        texts = [p.text for p in doc.paragraphs]

        assert 'Section 1' in texts
        assert 'Section 2' in texts
        assert 'Content for section 1' in texts
        assert 'Content for section 2' in texts

    def test_build_with_code_block(self):
        """Test building a spec with code blocks."""
        spec = ReportSpec(
            title='Code Report',
            author='Dev',
            date='2024-03-01',
            sections=[
                Section(
                    title='Code Sample',
                    blocks=[
                        ContentBlock(type='code', data='print("Hello World")')
                    ]
                )
            ]
        )

        builder = WordBuilder()
        result = builder.build(spec)

        doc = Document(io.BytesIO(result))

        # Code block should be present
        assert any('print' in p.text for p in doc.paragraphs)

    def test_docx_structure(self):
        """Test that the generated docx has proper structure."""
        spec = ReportSpec(
            title='Structure Test',
            author='QA',
            date='2024-04-01',
            sections=[
                Section(
                    title='Test Section',
                    blocks=[
                        ContentBlock(type='text', data='Test content')
                    ]
                )
            ]
        )

        builder = WordBuilder()
        result = builder.build(spec)

        # Should be valid ZIP (docx is a zip)
        assert result[:4] == b'PK\x03\x04'


if __name__ == '__main__':
    pytest.main([__file__, '-v'])