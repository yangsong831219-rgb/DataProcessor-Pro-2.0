"""Report Builder package for generating Word and PPT reports."""

from .models import (
    ALLOWED_PROJECT_EXTENSIONS,
    ALLOWED_REQUIREMENT_EXTENSIONS,
    ALLOWED_TEMPLATE_EXTENSIONS_PPT,
    ALLOWED_TEMPLATE_EXTENSIONS_WORD,
    ContentBlock,
    FileItem,
    GenerationEvent,
    OutlineSection,
    PPTReport,
    PPTSlide,
    ReportConfig,
    ReportOutline,
    ReportSpec,
    Section,
    WordReport,
    WordSection,
    WordTable,
)

__all__ = [
    # New structured models
    'ReportOutline',
    'OutlineSection',
    'WordReport',
    'WordSection',
    'WordTable',
    'PPTReport',
    'PPTSlide',
    'GenerationEvent',
    # Legacy (kept for compatibility)
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