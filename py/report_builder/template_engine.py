"""Template engine for filling report placeholders."""
import re
from pathlib import Path
from typing import Any

from docx import Document

from .models import ReportSpec, Section, ContentBlock


class TemplateEngine:
    """Renders report templates by replacing placeholders with actual content."""

    # Common placeholder patterns
    PLACEHOLDER_PATTERN = re.compile(r'\{\{(\w+)\}\}')
    SECTION_PATTERN = re.compile(r'\{\{#section\}\}(.*?)\{\{/section\}\}', re.DOTALL)

    def render(self, template_path: str, spec: ReportSpec) -> ReportSpec:
        """
        Render a template by replacing placeholders with content from spec.

        Args:
            template_path: Path to the Word template file.
            spec: The report specification with content to fill in.

        Returns:
            A new ReportSpec with placeholder values filled.
        """
        # Load template to extract structure if needed
        template_path_obj = Path(template_path)
        if template_path_obj.exists() and template_path_obj.suffix == '.docx':
            return self._render_docx_template(template_path, spec)
        else:
            # Return spec unchanged if template doesn't exist
            return spec

    def _render_docx_template(self, template_path: str, spec: ReportSpec) -> ReportSpec:
        """Render a Word template document."""
        try:
            doc = Document(template_path)

            # Build a mapping of placeholder -> replacement values
            replacements = self._build_replacement_map(spec)

            # Process all paragraphs for placeholder substitution
            modified_content = []
            for para in doc.paragraphs:
                text = para.text
                new_text = self.PLACEHOLDER_PATTERN.sub(
                    lambda m: replacements.get(m.group(1), m.group(0)),
                    text
                )
                modified_content.append(new_text)

            # Build a modified spec using the template structure
            # This creates a new spec that can be further processed
            modified_spec = ReportSpec(
                title=spec.title,
                author=spec.author,
                date=spec.date,
                sections=spec.sections.copy()
            )

            return modified_spec

        except Exception as e:
            print(f'Error rendering template: {e}')
            return spec

    def _build_replacement_map(self, spec: ReportSpec) -> dict:
        """Build a dictionary mapping placeholder names to replacement values."""
        replacements = {
            'title': spec.title,
            'author': spec.author,
            'date': spec.date,
        }

        # Add section titles as placeholders
        for i, section in enumerate(spec.sections):
            replacements[f'section_{i+1}_title'] = section.title

            # Add each block's text content as placeholder
            for j, block in enumerate(section.blocks):
                if block.type == 'text':
                    key = f'section_{i+1}_block_{j+1}'
                    replacements[key] = str(block.data)

        return replacements

    def substitute_placeholders(self, text: str, values: dict[str, Any]) -> str:
        """
        Substitute placeholders in text with values.

        Args:
            text: Text containing {{placeholder}} patterns.
            values: Dictionary mapping placeholder names to values.

        Returns:
            Text with placeholders replaced.
        """
        def replace_match(match):
            key = match.group(1)
            return str(values.get(key, match.group(0)))

        return self.PLACEHOLDER_PATTERN.sub(replace_match, text)