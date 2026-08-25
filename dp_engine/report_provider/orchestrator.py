"""Report-render Provider selection and result attestation."""

from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path

from .builtin import BUILTIN_PROVIDER_ID, BuiltinReportRenderProvider
from .models import ReportRenderProvider, ReportRenderRequest, ReportRenderResult


class ReportRenderOrchestrator:
    """Select one Provider and attest its identity and output destination."""

    def __init__(
        self,
        providers: Iterable[ReportRenderProvider] | None = None,
        *,
        default_provider_id: str = BUILTIN_PROVIDER_ID,
    ) -> None:
        selected_providers = (
            tuple(providers)
            if providers is not None
            else (BuiltinReportRenderProvider(),)
        )
        provider_map: dict[str, ReportRenderProvider] = {}
        for provider in selected_providers:
            provider_id = provider.provenance.provider_id
            if provider_id in provider_map:
                raise ValueError(
                    f"duplicate report render provider_id: {provider_id}"
                )
            provider_map[provider_id] = provider
        if default_provider_id not in provider_map:
            raise ValueError(
                "default report render provider is not registered: "
                f"{default_provider_id}"
            )
        self._providers = provider_map
        self._default_provider_id = default_provider_id

    def render(
        self,
        request: ReportRenderRequest,
        *,
        provider_id: str | None = None,
    ) -> ReportRenderResult:
        selected_id = provider_id or self._default_provider_id
        try:
            provider = self._providers[selected_id]
        except KeyError as error:
            raise LookupError(
                f"unknown report render provider: {selected_id}"
            ) from error

        declared_provenance = provider.provenance
        result = provider.render(request)
        if result.provenance != declared_provenance:
            raise RuntimeError("report render provider provenance mismatch")
        if self._resolved(result.output_path) != self._resolved(request.output_path):
            raise RuntimeError("report render provider output path mismatch")
        return result

    @staticmethod
    def _resolved(path: str) -> Path:
        return Path(path).resolve(strict=False)
