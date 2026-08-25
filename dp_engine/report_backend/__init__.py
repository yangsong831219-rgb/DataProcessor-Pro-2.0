"""Professional report backend discovery and isolated job contracts."""

from dp_engine.report_backend.catalog import ReportBackendCatalog
from dp_engine.report_backend.models import (
    ALLOWED_REPORT_BACKEND_CAPABILITIES,
    REPORT_BACKEND_CONTRACT_VERSION,
    REPORT_BACKEND_EXTENSION_KEYS,
    REPORT_BACKEND_GENERATE_ENTRYPOINT,
    SUPPORTED_REPORT_ARTIFACT_TYPES,
    SUPPORTED_TEMPLATE_MODES,
    ReportBackendDescriptor,
    ReportBackendIssue,
    extract_report_backend_template_modes,
    validate_report_backend_manifest,
)
from dp_engine.report_backend.job import (
    REPORT_JOB_SCHEMA_VERSION,
    ReportBackendAssetInput,
    ReportBackendJobV1,
)

__all__ = [
    "ALLOWED_REPORT_BACKEND_CAPABILITIES",
    "REPORT_BACKEND_CONTRACT_VERSION",
    "REPORT_BACKEND_EXTENSION_KEYS",
    "REPORT_BACKEND_GENERATE_ENTRYPOINT",
    "REPORT_JOB_SCHEMA_VERSION",
    "SUPPORTED_REPORT_ARTIFACT_TYPES",
    "SUPPORTED_TEMPLATE_MODES",
    "ReportBackendCatalog",
    "ReportBackendDescriptor",
    "ReportBackendIssue",
    "ReportBackendAssetInput",
    "ReportBackendJobV1",
    "extract_report_backend_template_modes",
    "validate_report_backend_manifest",
]
