"""Stable report-render Provider contracts.

The Provider seam owns only the conversion from structured report data to a
temporary OOXML document.  Validation and atomic commit remain Host concerns.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol, runtime_checkable


_REPORT_IMAGE_MEDIA_TYPES = frozenset({"image/png", "image/jpeg"})


@dataclass(frozen=True, slots=True)
class ReportProviderOption:
    """One report backend choice safe to display and persist in UI config."""

    provider_id: str
    provider_version: str
    display_name: str
    is_builtin: bool = False

    def __post_init__(self) -> None:
        if not self.provider_id.strip():
            raise ValueError("report provider option id must be non-empty")
        if not self.provider_version.strip():
            raise ValueError("report provider option version must be non-empty")
        if not self.display_name.strip():
            raise ValueError("report provider option name must be non-empty")


@dataclass(frozen=True, slots=True)
class ReportRenderAsset:
    """Provider-neutral approved image with its semantic placement intent."""

    host_id: str
    source_path: Path
    media_type: str
    semantic_label: str
    target: str

    def __post_init__(self) -> None:
        if not self.host_id.strip():
            raise ValueError("report render asset host_id must be non-empty")
        if not self.source_path.is_absolute():
            raise ValueError("report render asset source_path must be absolute")
        if self.media_type not in _REPORT_IMAGE_MEDIA_TYPES:
            raise ValueError("report render asset media_type must be PNG or JPEG")
        if not self.semantic_label.strip():
            raise ValueError("report render asset semantic_label must be non-empty")
        if not self.target.strip():
            raise ValueError("report render asset target must be non-empty")


@dataclass(frozen=True, slots=True)
class ReportProviderProvenance:
    """Identity of the Provider that rendered a report artifact."""

    provider_id: str
    provider_version: str

    def __post_init__(self) -> None:
        if not self.provider_id.strip():
            raise ValueError("report render provider_id must be non-empty")
        if not self.provider_version.strip():
            raise ValueError("report render provider_version must be non-empty")

    def to_dict(self) -> dict[str, str]:
        return {
            "provider_id": self.provider_id,
            "provider_version": self.provider_version,
        }


@dataclass(frozen=True, slots=True)
class ReportRenderRequest:
    """Host-owned inputs for rendering one temporary report document."""

    report_type: str
    structured_report: Mapping[str, Any]
    template_path: str
    output_path: str
    project_dir: str = ""
    bridge_workspace: Path | None = None
    bridge_assets: tuple[object, ...] = field(default_factory=tuple)
    assets: tuple[ReportRenderAsset, ...] = field(default_factory=tuple)
    required_figure_ids: tuple[str, ...] = field(default_factory=tuple)
    supplementary_figure_ids: tuple[str, ...] = field(default_factory=tuple)
    inclusion_summary: Mapping[str, object] = field(default_factory=dict)
    cancel_check: Callable[[], None] | None = None

    def __post_init__(self) -> None:
        if self.report_type not in {"word", "ppt"}:
            raise ValueError(
                f"unsupported report render type: {self.report_type!r}"
            )
        if not self.output_path:
            raise ValueError("report render output_path must be non-empty")
        asset_ids = tuple(asset.host_id for asset in self.assets)
        if len(asset_ids) != len(set(asset_ids)):
            raise ValueError("report render asset host_id values must be unique")
        requested_ids = (
            set(self.required_figure_ids)
            | set(self.supplementary_figure_ids)
        )
        if requested_ids - set(asset_ids):
            raise ValueError("report render figure IDs must reference assets")
        if set(self.required_figure_ids) & set(self.supplementary_figure_ids):
            raise ValueError(
                "report render figures cannot be required and supplementary"
            )


@dataclass(frozen=True, slots=True)
class ReportRenderResult:
    """Provider result returned before Host validation and atomic commit."""

    output_path: str
    provenance: ReportProviderProvenance
    warnings: tuple[str, ...] = field(default_factory=tuple)
    missing_images: tuple[str, ...] = field(default_factory=tuple)
    requires_host_postprocessing: bool = True


@runtime_checkable
class ReportRenderProvider(Protocol):
    """Implementation contract for structured-report OOXML renderers."""

    @property
    def provenance(self) -> ReportProviderProvenance:
        """Return the Provider identity declared before execution."""
        ...

    def render(self, request: ReportRenderRequest) -> ReportRenderResult:
        """Render exactly one temporary OOXML document."""
        ...
