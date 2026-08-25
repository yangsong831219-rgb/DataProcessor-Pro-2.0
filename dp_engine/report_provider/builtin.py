"""Builtin report-render Provider backed by the existing deterministic Builders."""

from __future__ import annotations

from dp_engine.report_builder.models import PPTReport, WordReport
from dp_engine.report_builder.ppt_builder import PPTBuilder
from dp_engine.report_builder.word_builder import WordBuilder

from .models import (
    ReportProviderProvenance,
    ReportRenderRequest,
    ReportRenderResult,
)


BUILTIN_PROVIDER_ID = "builtin.report_builder"
BUILTIN_PROVIDER_VERSION = "1.0"


class BuiltinReportRenderProvider:
    """Adapter that preserves the pre-Provider Word/PPT Builder calls."""

    @property
    def provenance(self) -> ReportProviderProvenance:
        return ReportProviderProvenance(
            provider_id=BUILTIN_PROVIDER_ID,
            provider_version=BUILTIN_PROVIDER_VERSION,
        )

    def render(self, request: ReportRenderRequest) -> ReportRenderResult:
        structured_report = dict(request.structured_report)

        if request.report_type == "ppt":
            builder = PPTBuilder()
            rendered_path = builder.build_ppt_report(
                PPTReport.from_dict(structured_report),
                request.template_path,
                request.output_path,
                project_dir=request.project_dir,
                bridge_workspace=request.bridge_workspace,
                bridge_assets=request.bridge_assets,
                cancel_check=request.cancel_check,
            )
            return ReportRenderResult(
                output_path=rendered_path,
                provenance=self.provenance,
                warnings=tuple(builder.warnings),
            )

        builder = WordBuilder()
        rendered_path = builder.build_word_report(
            WordReport.from_dict(structured_report),
            request.template_path,
            request.output_path,
            project_dir=request.project_dir,
            bridge_workspace=request.bridge_workspace,
            bridge_assets=request.bridge_assets,
            cancel_check=request.cancel_check,
        )
        return ReportRenderResult(
            output_path=rendered_path,
            provenance=self.provenance,
            missing_images=tuple(builder.missing_images),
        )
