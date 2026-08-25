"""Professional report backend Adapter and explicit selection controller."""

from __future__ import annotations

from pathlib import Path
from typing import Protocol

from dp_engine.report_backend.catalog import ReportBackendCatalog
from dp_engine.report_backend.job import (
    ReportBackendAssetInput,
    ReportBackendJobV1,
)
from dp_engine.skills.registry import SkillRegistry
from dp_engine.skills.runtime_artifacts import ArtifactStore
from dp_engine.skills.runtime_models import SkillRuntimeResponse
from dp_engine.skills.runtime_service import SkillRuntimeService

from .builtin import (
    BUILTIN_PROVIDER_ID,
    BUILTIN_PROVIDER_VERSION,
    BuiltinReportRenderProvider,
)
from .models import (
    ReportProviderOption,
    ReportProviderProvenance,
    ReportRenderProvider,
    ReportRenderRequest,
    ReportRenderResult,
)


REPORT_BACKEND_RENDER_TIMEOUT_SECONDS = 180.0


class _ReportRuntime(Protocol):
    def run_report_backend(
        self,
        skill_id: str,
        version: str,
        job: ReportBackendJobV1,
        *,
        timeout_seconds: float | None = None,
    ) -> SkillRuntimeResponse: ...

    def cancel(self) -> None: ...


class _ReportArtifactStore(Protocol):
    def export(
        self,
        skill_id: str,
        task_id: str,
        artifact_id: str,
        target: Path,
        *,
        overwrite: bool = False,
    ) -> None: ...


class ReportProviderSelectionError(RuntimeError):
    """The explicitly selected Provider is not currently executable."""


class ReportProviderExecutionError(RuntimeError):
    """Safe, stage-labelled failure from one selected professional Provider."""

    def __init__(
        self,
        provenance: ReportProviderProvenance,
        *,
        status: str,
        stage: str,
        error_type: str,
    ) -> None:
        self.provenance = provenance
        self.status = status
        self.stage = stage
        self.error_type = error_type
        super().__init__(
            "报告后端 "
            f"{provenance.provider_id}@{provenance.provider_version} "
            f"执行失败；status={status}；stage={stage}；"
            f"error={error_type}。未回退到其他后端。"
        )


class SkillReportRenderProvider:
    """Adapter from the report Provider Interface to isolated SkillRuntime."""

    def __init__(
        self,
        *,
        provenance: ReportProviderProvenance,
        runtime: _ReportRuntime,
        artifact_store: _ReportArtifactStore,
    ) -> None:
        self._provenance = provenance
        self._runtime = runtime
        self._artifact_store = artifact_store

    @property
    def provenance(self) -> ReportProviderProvenance:
        return self._provenance

    def cancel(self) -> None:
        """Cancel the active isolated Runtime operation, if any."""
        self._runtime.cancel()

    def render(self, request: ReportRenderRequest) -> ReportRenderResult:
        artifact_type = "pptx" if request.report_type == "ppt" else "docx"
        try:
            template = (
                Path(request.template_path).resolve(strict=False)
                if request.template_path
                else None
            )
            job = ReportBackendJobV1(
                report_type=request.report_type,
                output_artifact_type=artifact_type,
                report=dict(request.structured_report),
                normalized_template_path=template,
                assets=tuple(
                    ReportBackendAssetInput(
                        host_id=asset.host_id,
                        source_path=asset.source_path,
                        media_type=asset.media_type,
                        semantic_label=asset.semantic_label,
                        target=asset.target,
                    )
                    for asset in request.assets
                ),
                required_figure_ids=request.required_figure_ids,
                supplementary_figure_ids=request.supplementary_figure_ids,
                inclusion_summary=dict(request.inclusion_summary),
            )
        except Exception as error:
            raise ReportProviderExecutionError(
                self.provenance,
                status="preflight_failed",
                stage="job_prepare",
                error_type=type(error).__name__,
            ) from error
        try:
            response = self._runtime.run_report_backend(
                self.provenance.provider_id,
                self.provenance.provider_version,
                job,
                timeout_seconds=REPORT_BACKEND_RENDER_TIMEOUT_SECONDS,
            )
        except Exception as error:
            raise ReportProviderExecutionError(
                self.provenance,
                status="preflight_failed",
                stage="runtime_preflight",
                error_type=type(error).__name__,
            ) from error

        if not response.success:
            stage = "runtime"
            error_type = "ReportBackendFailure"
            if response.error is not None:
                error_type = response.error.error_type or error_type
                raw_stage = response.error.detail.get("stage")
                if isinstance(raw_stage, str) and raw_stage.strip():
                    stage = raw_stage.strip()
            raise ReportProviderExecutionError(
                self.provenance,
                status=response.status,
                stage=stage,
                error_type=error_type,
            )

        if len(response.artifacts) != 1:
            raise ReportProviderExecutionError(
                self.provenance,
                status="protocol_error",
                stage="artifact_publish",
                error_type="ArtifactCountMismatch",
            )

        artifact = response.artifacts[0]
        try:
            self._artifact_store.export(
                self.provenance.provider_id,
                response.task_id,
                artifact.artifact_id,
                Path(request.output_path),
                overwrite=True,
            )
        except Exception as error:
            raise ReportProviderExecutionError(
                self.provenance,
                status="failed",
                stage="artifact_consume",
                error_type=type(error).__name__,
            ) from error

        return ReportRenderResult(
            output_path=request.output_path,
            provenance=self.provenance,
            warnings=response.warnings,
            requires_host_postprocessing=False,
        )


class ReportProviderController:
    """Fresh-registry discovery and fail-closed construction for one Provider."""

    def __init__(self, registry_path: Path, installed_dir: Path) -> None:
        self._registry_path = registry_path
        self._installed_dir = installed_dir

    def list_options(
        self,
        *,
        report_type: str,
        template_mode: str,
    ) -> tuple[ReportProviderOption, ...]:
        artifact_type = _artifact_type(report_type)
        registry = self._load_registry()
        external = ReportBackendCatalog(registry).compatible_backends(
            artifact_type,
            template_mode,
        )
        return (
            ReportProviderOption(
                provider_id=BUILTIN_PROVIDER_ID,
                provider_version=BUILTIN_PROVIDER_VERSION,
                display_name="内置标准生成器",
                is_builtin=True,
            ),
            *(
                ReportProviderOption(
                    provider_id=item.skill_id,
                    provider_version=item.version,
                    display_name=item.name,
                    is_builtin=False,
                )
                for item in external
            ),
        )

    def create_provider(
        self,
        *,
        provider_id: str,
        provider_version: str,
        report_type: str,
        template_mode: str,
    ) -> ReportRenderProvider:
        if provider_id == BUILTIN_PROVIDER_ID:
            if provider_version != BUILTIN_PROVIDER_VERSION:
                raise ReportProviderSelectionError(
                    "内置报告后端版本不匹配，未执行生成。"
                )
            return BuiltinReportRenderProvider()

        artifact_type = _artifact_type(report_type)
        registry = self._load_registry()
        descriptors = ReportBackendCatalog(registry).list_backends(
            artifact_type=artifact_type,
            template_mode=template_mode,
        )
        selected = next(
            (
                item
                for item in descriptors
                if item.skill_id == provider_id
                and item.version == provider_version
            ),
            None,
        )
        if selected is None:
            raise ReportProviderSelectionError(
                f"报告后端 {provider_id}@{provider_version} 未安装，未回退。"
            )
        if not selected.compatible:
            reasons = ",".join(selected.issue_codes) or "INCOMPATIBLE"
            raise ReportProviderSelectionError(
                f"报告后端 {provider_id}@{provider_version} 当前不可用"
                f"（{reasons}），未回退。"
            )

        runtime = SkillRuntimeService(registry, self._installed_dir)
        artifact_store = ArtifactStore(
            _artifact_root=self._installed_dir.parent / "artifacts"
        )
        return SkillReportRenderProvider(
            provenance=ReportProviderProvenance(
                selected.skill_id,
                selected.version,
            ),
            runtime=runtime,
            artifact_store=artifact_store,
        )

    def _load_registry(self) -> SkillRegistry:
        registry = SkillRegistry(self._registry_path)
        registry.load()
        return registry


def _artifact_type(report_type: str) -> str:
    if report_type == "ppt":
        return "pptx"
    if report_type == "word":
        return "docx"
    raise ValueError(f"unsupported report type: {report_type!r}")
