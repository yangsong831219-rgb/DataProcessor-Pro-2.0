"""Data models for report generation."""
from dataclasses import dataclass, field
from typing import Any, Literal, Optional


@dataclass
class ContentBlock:
    """A single content block within a section."""
    type: Literal['text', 'chart', 'table', 'code']
    data: Any


@dataclass
class Section:
    """A section within a report."""
    title: str
    blocks: list[ContentBlock] = field(default_factory=list)


@dataclass
class ReportSpec:
    """Complete report specification."""
    title: str
    author: str
    date: str
    sections: list[Section] = field(default_factory=list)


@dataclass
class FileItem:
    """Represents a file selected for report generation."""
    id: str
    name: str
    path: str
    type: Literal['requirement', 'template', 'project']


@dataclass
class ReportConfig:
    """Configuration for a report (Word or PPT)."""
    title: str
    author: str = 'DataProcessor Pro'
    date: str = ''
    requirement_files: list[FileItem] = field(default_factory=list)
    template_files: list[FileItem] = field(default_factory=list)
    project_files: list[FileItem] = field(default_factory=list)
    last_save_path: Optional[str] = None

# Allowed file extensions
ALLOWED_REQUIREMENT_EXTENSIONS = ['.txt', '.md']
ALLOWED_TEMPLATE_EXTENSIONS_WORD = ['.docx', '.doc']
ALLOWED_TEMPLATE_EXTENSIONS_PPT = ['.pptx']
ALLOWED_PROJECT_EXTENSIONS = ['.doc', '.docx', '.xlsx', '.pdf', '.png', '.jpg', '.csv', '.txt']