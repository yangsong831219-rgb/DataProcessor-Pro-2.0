"""Public report-render Provider API."""

from .builtin import (
    BUILTIN_PROVIDER_ID,
    BUILTIN_PROVIDER_VERSION,
    BuiltinReportRenderProvider,
)
from .models import (
    ReportProviderOption,
    ReportProviderProvenance,
    ReportRenderAsset,
    ReportRenderProvider,
    ReportRenderRequest,
    ReportRenderResult,
)
from .orchestrator import ReportRenderOrchestrator
from .ppt_master import (
    PPT_MASTER_PROVIDER_ID,
    PPT_MASTER_PROVIDER_VERSION,
    PptMasterAuthoredSlide,
    PptMasterAuthoringAdapter,
    PptMasterAuthoringAsset,
    PptMasterAuthoringContext,
    PptMasterAuthoringTemplate,
    PptMasterControlledRunner,
    PptMasterProjectSpec,
    PptMasterReportProviderError,
    PptMasterReportRenderProvider,
    PptMasterSlideAuthoringRequest,
)
from .skill_backend import (
    ReportProviderController,
    ReportProviderExecutionError,
    ReportProviderSelectionError,
    SkillReportRenderProvider,
)

__all__ = [
    "BUILTIN_PROVIDER_ID",
    "BUILTIN_PROVIDER_VERSION",
    "BuiltinReportRenderProvider",
    "PPT_MASTER_PROVIDER_ID",
    "PPT_MASTER_PROVIDER_VERSION",
    "PptMasterAuthoredSlide",
    "PptMasterAuthoringAdapter",
    "PptMasterAuthoringAsset",
    "PptMasterAuthoringContext",
    "PptMasterAuthoringTemplate",
    "PptMasterControlledRunner",
    "PptMasterProjectSpec",
    "PptMasterReportProviderError",
    "PptMasterReportRenderProvider",
    "PptMasterSlideAuthoringRequest",
    "ReportProviderController",
    "ReportProviderExecutionError",
    "ReportProviderOption",
    "ReportProviderProvenance",
    "ReportProviderSelectionError",
    "ReportRenderAsset",
    "ReportRenderOrchestrator",
    "ReportRenderProvider",
    "ReportRenderRequest",
    "ReportRenderResult",
    "SkillReportRenderProvider",
]
