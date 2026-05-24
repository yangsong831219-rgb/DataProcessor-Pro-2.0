"""Data models for report generation."""
from dataclasses import dataclass, field
from typing import Any


@dataclass
class ContentBlock:
    """A single content block within a section."""
    type: str  # 'text', 'chart', 'table', 'code'
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