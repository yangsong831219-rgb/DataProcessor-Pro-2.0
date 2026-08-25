"""Concrete Host AI authoring Adapter for PPT Master.

The Adapter owns model access through ``AIClient`` and returns only in-memory,
path-free project contracts and complete SVG pages.  Filesystem staging and all
third-party execution remain the PPT Master Provider's responsibility.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any, Protocol, TypeVar

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from core.ai_errors import AIClientError, AIClientTimeoutError, ReportSchemaError

from .controlled_runner import PptxStructure
from .deck_contract import CompiledDeckContract, DeckContractCompiler
from .planning import PlanningPhase


_MAX_ASSET_REFERENCE_CORRECTIVE_RETRIES = 1

if TYPE_CHECKING:
    from dp_engine.report_provider.ppt_master import (
        PptMasterAuthoredSlide,
        PptMasterAuthoringContext,
        PptMasterProjectSpec,
        PptMasterSlideAuthoringRequest,
    )


AuthoringModelT = TypeVar("AuthoringModelT", bound=BaseModel)


class StructuredAuthoringModel(Protocol):
    """Narrow structured-generation Interface used by the authoring Adapter."""

    @property
    def identity(self) -> str: ...

    def generate_structured(
        self,
        *,
        prompt: str,
        schema: type[AuthoringModelT],
        system_prompt: str,
        max_tokens: int,
    ) -> AuthoringModelT: ...


class _AIClientLike(Protocol):
    model_name: str

    @property
    def backend(self) -> str: ...

    def generate_structured(
        self,
        prompt: str,
        schema: type[BaseModel],
        system_prompt: str = "",
        temperature: float = 0.3,
        max_tokens: int = 2_048,
        *,
        max_schema_retries: int = 1,
    ) -> dict[str, Any]: ...


class HostAuthoringError(RuntimeError):
    """A concrete Host authoring operation failed before tool execution."""

    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(message)


class HostAIClientAuthoringModel:
    """Adapter keeping credentials and model routing inside Host ``AIClient``."""

    def __init__(self, client: _AIClientLike | None = None) -> None:
        if client is None:
            from core.ai_client import AIClient

            client = AIClient.get_instance()
        self._client = client

    @property
    def identity(self) -> str:
        return f"{self._client.backend}:{self._client.model_name}"

    def generate_structured(
        self,
        *,
        prompt: str,
        schema: type[AuthoringModelT],
        system_prompt: str,
        max_tokens: int,
    ) -> AuthoringModelT:
        try:
            data = self._client.generate_structured(
                prompt=prompt,
                schema=schema,
                system_prompt=system_prompt,
                temperature=0.2,
                max_tokens=max_tokens,
                max_schema_retries=1,
            )
            return schema.model_validate(data)
        except AIClientTimeoutError as error:
            raise HostAuthoringError(
                "model_timeout",
                "Host structured authoring model timed out",
            ) from error
        except (ReportSchemaError, ValidationError) as error:
            raise HostAuthoringError(
                "model_schema_invalid",
                f"Host structured authoring model returned invalid schema: "
                f"{type(error).__name__}",
            ) from error
        except AIClientError as error:
            raise HostAuthoringError(
                "model_failed",
                f"Host structured authoring model failed: {type(error).__name__}",
            ) from error


class _AuthoredSvgPayload(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        str_strip_whitespace=True,
    )

    svg_text: str = Field(min_length=100, max_length=4 * 1024 * 1024)
    speaker_notes_markdown: str = Field(default="", max_length=64 * 1024)

    @field_validator("svg_text")
    @classmethod
    def _validate_svg_contract(cls, value: str) -> str:
        """Route invalid model SVG through the current-slide schema retry."""
        # Lazy import avoids reversing the Provider/Host module import boundary
        # during application startup. The Provider repeats this validation as
        # the independent fail-closed staging gate.
        from dp_engine.report_provider.ppt_master import (
            _sanitize_svg_for_ppt_master,
            _validate_svg,
        )

        sanitized = _sanitize_svg_for_ppt_master(value)
        _validate_svg(sanitized)
        return value


_AUTHORING_SYSTEM_PROMPT = """You are the Host SVG presentation author for
PPT Master 2.7.0 flat project format. Return only the requested JSON schema.
Author exactly one complete, well-formed SVG slide with viewBox="0 0 1280 720".

REQUIRED — every authored SVG MUST follow these rules exactly:

1. NO <style> element anywhere. Define all appearance through inline
   presentation attributes (fill, font-family, font-size, font-weight,
   text-anchor, stroke, stroke-width, opacity).
2. NO class attribute on any element. Every visual property is an inline
   attribute directly on the element.
3. NO id attribute used for CSS selectors. id is allowed only as a
   page-level group anchor (e.g. <g id="section-title">) for animation
   config; never pair it with <style>.
4. NO <clipPath> element. Do not define clip-paths.
5. NO clip-path attribute on <g> elements. clip-path is allowed only on
   <image> when used with data-pptx-crop="1".
6. NO <foreignObject>, <script>, <iframe>, <object>, <textPath>,
   <animate*>, <set>, @font-face, mask, event attributes (on*), or
   external URL references.
7. Use ONLY inline styling: <text fill="#E2E8F0" font-family="Microsoft YaHei"
   font-size="36" font-weight="700">...</text>. Repeat attributes on each
   element; do not rely on inheritance from a parent <g>.
8. Text content uses raw Unicode (Chinese characters, —, °, ±, µ) directly.
   Escape XML reserved characters (&amp; &lt; &gt; &quot; &apos;) but never
   use HTML named entities like &nbsp; &mdash; &copy;.
9. Image references use ONLY the exact ../images/<project_filename> form
   provided in approved_assets. Never invent asset IDs or filenames.
10. Every visible element stays inside the 1280×720 canvas. Use safe margins
     (≥64px horizontal, ≥48px vertical from edges).
11. Treat Host structural metadata as mandatory: set a canonical
    data-pptx-page-role on the root <svg>; give every element with
    data-pptx-role a unique stable id; and give every visible direct-root <g>
    a data-pptx-bounds="x y width height" subcanvas that contains all of its
    visible descendants.

Compose professional technical-report pages with visible hierarchy, legible
Chinese text, the confirmed design palette and typography, and clear evidence
presentation per the slide intent."""


class HostAIPptMasterAuthoringAdapter:
    """Concrete Host Authoring Adapter consumed by the PPT Master Provider."""

    def __init__(
        self,
        model: StructuredAuthoringModel | None = None,
        *,
        contract_compiler: DeckContractCompiler | None = None,
    ) -> None:
        self._model = model or HostAIClientAuthoringModel()
        self._contract_compiler = contract_compiler or DeckContractCompiler()
        self._compiled_contracts: dict[str, CompiledDeckContract] = {}

    @property
    def model_identity(self) -> str:
        return self._model.identity

    def create_project_spec(
        self,
        context: PptMasterAuthoringContext,
    ) -> PptMasterProjectSpec:
        snapshot = context.planning_snapshot
        if snapshot.phase != PlanningPhase.PLAN_CONFIRMED:
            raise HostAuthoringError(
                "planning_not_confirmed",
                "Project contracts require a confirmed Planning Snapshot",
            )
        try:
            compiled = self._contract_compiler.compile(context)
        except ValueError as error:
            code = (
                "structured_mode_unavailable"
                if "flat PPTX structure" in str(error)
                else "planning_artifact_missing"
            )
            raise HostAuthoringError(code, str(error)) from error
        self._compiled_contracts[context.render_request_sha256] = compiled
        from dp_engine.report_provider.ppt_master import PptMasterProjectSpec

        return PptMasterProjectSpec(
            design_spec_markdown=compiled.design_spec_markdown,
            spec_lock_markdown=compiled.spec_lock_markdown,
            structure=PptxStructure.FLAT,
        )

    def author_slide(
        self,
        context: PptMasterAuthoringContext,
        request: PptMasterSlideAuthoringRequest,
    ) -> PptMasterAuthoredSlide:
        snapshot = context.planning_snapshot
        design = snapshot.design
        if design is None:
            raise HostAuthoringError(
                "design_missing",
                "Slide authoring requires a confirmed design contract",
            )
        compiled = self._compiled_contracts.get(context.render_request_sha256)
        if compiled is None:
            try:
                compiled = self._contract_compiler.compile(context)
            except ValueError as error:
                raise HostAuthoringError(
                    "planning_artifact_missing",
                    str(error),
                ) from error
            self._compiled_contracts[context.render_request_sha256] = compiled
        asset_by_id = {asset.asset_id: asset for asset in context.assets}
        planned_assets = []
        for asset_id in request.intent.asset_ids:
            asset = asset_by_id.get(asset_id)
            if asset is None:
                raise HostAuthoringError(
                    "asset_plan_invalid",
                    f"Planned authoring asset is unavailable: {asset_id}",
                )
            planned_assets.append({
                "asset_id": asset.asset_id,
                "project_filename": asset.project_filename,
                "semantic_label": asset.semantic_label,
                "target": asset.target,
            })

        report_data = json.loads(context.structured_report_json)
        report_slides = report_data.get("slides", [])
        source_slide = (
            report_slides[request.intent.sequence - 1]
            if isinstance(report_slides, list)
            and len(report_slides) >= request.intent.sequence
            else {}
        )
        prompt_payload = {
            "schema_version": 1,
            "deck_title": snapshot.request.report_title,
            "language": snapshot.request.language,
            "design": design.model_dump(mode="json"),
            "deck_contract": compiled.authoring_constraints(),
            "template_summary": snapshot.request.template_summary,
            "slide_intent": request.intent.model_dump(mode="json"),
            "source_slide": source_slide,
            "svg_filename": request.svg_filename,
            "method_receipt": (
                request.method_receipt.model_dump(mode="json")
                if request.method_receipt is not None else None
            ),
            "repair_receipt": (
                request.repair_receipt.model_dump(mode="json")
                if request.repair_receipt is not None else None
            ),
            "previous_svg_text": request.previous_svg_text,
            "approved_assets": planned_assets,
            "asset_reference_contract": {
                "mandatory_project_filenames": [
                    asset["project_filename"] for asset in planned_assets
                ],
                "require_exact_filename_set": True,
                "allow_other_project_images": False,
            },
            "svg_compliance": {
                "canvas": "viewBox 0 0 1280 720 ppt169",
                "forbidden_elements": [
                    "style", "foreignObject", "script", "iframe", "object",
                    "textPath", "clipPath", "animate", "animateTransform",
                    "animateMotion", "set", "mask",
                ],
                "forbidden_attributes": [
                    "class", "clip-path (on <g> only; allowed on <image>)",
                ],
                "forbidden_patterns": [
                    "@font-face", "@import", "url() with external ref",
                    "HTML named entities (&nbsp; &mdash; &copy; etc.)",
                ],
                "required_style": "inline presentation attributes only",
                "font_rule": "Use fill/font-family/font-size/font-weight inline on each element",
                "image_rule": "exact ../images/<approved_project_filename> only",
            },
        }
        base_prompt = (
            (
                "Repair this single confirmed slide from its structured Quality "
                "Receipt. Return a complete replacement SVG.\n"
                if request.repair_receipt is not None
                else "Author this single confirmed slide.\n"
            )
            + json.dumps(
                prompt_payload,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            )
        )
        if request.repair_receipt is not None:
            base_prompt += (
                "\nRepair monotonicity is mandatory: resolve the listed hard "
                "errors without introducing any new quality error rule. Preserve "
                "all already-valid semantic IDs, page role, module bounds, asset "
                "references, and visible content unless a listed error requires "
                "that exact element to change."
            )
        expected_filenames = {
            str(asset["project_filename"])
            for asset in planned_assets
        }
        prompt = base_prompt
        payload: _AuthoredSvgPayload | None = None
        for attempt in range(_MAX_ASSET_REFERENCE_CORRECTIVE_RETRIES + 1):
            authored = self._model.generate_structured(
                prompt=prompt,
                schema=_AuthoredSvgPayload,
                system_prompt=_AUTHORING_SYSTEM_PROMPT,
                max_tokens=16_384,
            )
            payload = _AuthoredSvgPayload.model_validate(authored)
            typography_issues = compiled.preflight_svg_typography(payload.svg_text)
            if typography_issues:
                if attempt >= _MAX_ASSET_REFERENCE_CORRECTIVE_RETRIES:
                    raise HostAuthoringError(
                        "svg_typography_contract_mismatch",
                        "Host authored SVG used undeclared recurring typography sizes",
                    )
                prompt = _typography_corrective_prompt(
                    base_prompt=base_prompt,
                    compiled=compiled,
                    issues=typography_issues,
                )
                continue
            referenced_filenames = _svg_referenced_filenames(payload.svg_text)
            if referenced_filenames == expected_filenames:
                break
            if attempt >= _MAX_ASSET_REFERENCE_CORRECTIVE_RETRIES:
                raise HostAuthoringError(
                    "svg_asset_reference_mismatch",
                    "Host authored SVG did not reference the exact planned asset set",
                )
            prompt = _asset_reference_corrective_prompt(
                base_prompt=base_prompt,
                expected_filenames=expected_filenames,
                referenced_filenames=referenced_filenames,
            )
        if payload is None:  # pragma: no cover - loop always executes
            raise HostAuthoringError(
                "svg_asset_reference_mismatch",
                "Host authored SVG asset validation did not execute",
            )
        from dp_engine.report_provider.ppt_master import PptMasterAuthoredSlide

        return PptMasterAuthoredSlide(
            slide_id=request.intent.slide_id,
            sequence=request.intent.sequence,
            svg_text=payload.svg_text,
            speaker_notes_markdown=payload.speaker_notes_markdown,
            used_asset_ids=request.intent.asset_ids,
        )


def _svg_referenced_filenames(svg_text: str) -> set[str]:
    """Return the Provider-equivalent image reference set for one model SVG."""
    from dp_engine.report_provider.ppt_master import (
        _sanitize_svg_for_ppt_master,
        _validate_svg,
    )

    return _validate_svg(_sanitize_svg_for_ppt_master(svg_text))


def _asset_reference_corrective_prompt(
    *,
    base_prompt: str,
    expected_filenames: set[str],
    referenced_filenames: set[str],
) -> str:
    missing = sorted(expected_filenames - referenced_filenames)
    unexpected = sorted(referenced_filenames - expected_filenames)
    return (
        base_prompt
        + "\n\nCORRECTIVE RETRY — YOUR PREVIOUS SVG USED THE WRONG IMAGE SET.\n"
        + "Regenerate the COMPLETE SVG page. Do not return a patch.\n"
        + "The set of ../images/<filename> references MUST equal the mandatory "
        + "filename set exactly.\n"
        + "MANDATORY_FILENAMES="
        + json.dumps(sorted(expected_filenames), ensure_ascii=False)
        + "\nPREVIOUS_REFERENCED_FILENAMES="
        + json.dumps(sorted(referenced_filenames), ensure_ascii=False)
        + "\nMISSING_FILENAMES="
        + json.dumps(missing, ensure_ascii=False)
        + "\nUNEXPECTED_FILENAMES="
        + json.dumps(unexpected, ensure_ascii=False)
        + "\nUse no other file-backed images. Preserve the confirmed slide intent."
    )


def _typography_corrective_prompt(
    *,
    base_prompt: str,
    compiled: CompiledDeckContract,
    issues: tuple[object, ...],
) -> str:
    issue_data = [
        issue.model_dump(mode="json")  # type: ignore[attr-defined]
        for issue in issues
    ]
    return (
        base_prompt
        + "\n\nCORRECTIVE RETRY — TYPOGRAPHY CONTRACT VIOLATION.\n"
        + "Regenerate the COMPLETE SVG page. Do not return a patch.\n"
        + "Every recurring text treatment MUST use one named typography role.\n"
        + "NAMED_ROLES_PX="
        + json.dumps(
            compiled.typography.model_dump(mode="json"),
            ensure_ascii=False,
            sort_keys=True,
        )
        + "\nPREVIOUS_UNDECLARED_SIZES="
        + json.dumps(issue_data, ensure_ascii=False, sort_keys=True)
        + "\nPreserve content, layout intent, and the exact planned asset set."
    )
