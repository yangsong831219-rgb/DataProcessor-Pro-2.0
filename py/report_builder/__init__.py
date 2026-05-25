"""Report Builder package for generating Word and PPT reports."""

from .models import (
    ALLOWED_PROJECT_EXTENSIONS,
    ALLOWED_REQUIREMENT_EXTENSIONS,
    ALLOWED_TEMPLATE_EXTENSIONS_PPT,
    ALLOWED_TEMPLATE_EXTENSIONS_WORD,
    ContentBlock,
    FileItem,
    ReportConfig,
    ReportSpec,
    Section,
)

__all__ = [
    'ContentBlock',
    'Section',
    'ReportSpec',
    'FileItem',
    'ReportConfig',
    'ALLOWED_REQUIREMENT_EXTENSIONS',
    'ALLOWED_TEMPLATE_EXTENSIONS_WORD',
    'ALLOWED_TEMPLATE_EXTENSIONS_PPT',
    'ALLOWED_PROJECT_EXTENSIONS',
]