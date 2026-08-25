"""Host-owned planning contracts and state machine for PPT Master reports.

This Module owns structured model calls and immutable planning transitions.  It
does not write files, import the PPT Master toolchain, or execute any command.
"""

from __future__ import annotations

import hashlib
import json
import os
import sys
from collections.abc import Callable, Mapping
from enum import StrEnum
from pathlib import Path
from typing import Any, Literal, Protocol, TypeVar, cast

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    ValidationError,
    create_model,
    field_validator,
    model_validator,
)

from core.ai_errors import AIClientError, AIClientTimeoutError, ReportSchemaError


_ID_PATTERN = r"^[^/\\\s\x00-\x1f]{1,96}$"
_HEX_COLOR_PATTERN = r"^#[0-9A-Fa-f]{6}$"


class PlanningPhase(StrEnum):
    NEW = "new"
    OUTLINE_READY = "outline_ready"
    OUTLINE_CONFIRMED = "outline_confirmed"
    DESIGN_READY = "design_ready"
    DESIGN_CONFIRMED = "design_confirmed"
    SLIDES_READY = "slides_ready"
    PLAN_CONFIRMED = "plan_confirmed"
    CANCELLED = "cancelled"


class PlanningAssetKind(StrEnum):
    CHART = "chart"
    TABLE = "table"
    IMAGE = "image"
    DIAGRAM = "diagram"


class TemplateMode(StrEnum):
    FREE_DESIGN = "free_design"
    VALIDATED_WORKSPACE = "validated_workspace"


class SlideLayout(StrEnum):
    TITLE = "title"
    SECTION = "section"
    DATA_FOCUS = "data_focus"
    COMPARISON = "comparison"
    PROCESS = "process"
    TWO_COLUMN = "two_column"
    CONCLUSION = "conclusion"


class ConfirmationStage(StrEnum):
    OUTLINE = "outline"
    DESIGN = "design"
    PLAN = "plan"


class _StrictPlanningModel(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        str_strip_whitespace=True,
    )


class PlanningAsset(_StrictPlanningModel):
    asset_id: str = Field(pattern=_ID_PATTERN)
    kind: PlanningAssetKind
    semantic_label: str = Field(min_length=1, max_length=200)
    summary: str = Field(min_length=1, max_length=1_000)
    required: bool = False


class PlanningRequest(_StrictPlanningModel):
    request_id: str = Field(pattern=_ID_PATTERN)
    report_title: str = Field(min_length=1, max_length=300)
    objective: str = Field(min_length=1, max_length=2_000)
    audience: str = Field(min_length=1, max_length=500)
    source_context: str = Field(min_length=1, max_length=60_000)
    requested_slide_count: int = Field(ge=3, le=40)
    language: Literal["zh-CN", "en-US"] = "zh-CN"
    template_mode: TemplateMode = TemplateMode.FREE_DESIGN
    template_summary: str = Field(default="", max_length=4_000)
    assets: tuple[PlanningAsset, ...] = Field(default_factory=tuple, max_length=80)

    @model_validator(mode="after")
    def _validate_assets(self) -> PlanningRequest:
        asset_ids = [asset.asset_id for asset in self.assets]
        if len(asset_ids) != len(set(asset_ids)):
            raise ValueError("planning asset IDs must be unique")
        if (
            self.template_mode == TemplateMode.VALIDATED_WORKSPACE
            and not self.template_summary
        ):
            raise ValueError("validated workspace mode requires template_summary")
        return self


class OutlineSection(_StrictPlanningModel):
    section_id: str = Field(pattern=_ID_PATTERN)
    title: str = Field(min_length=1, max_length=200)
    purpose: str = Field(min_length=1, max_length=500)
    key_messages: tuple[str, ...] = Field(min_length=1, max_length=5)
    allocated_slides: int = Field(ge=1, le=10)
    candidate_asset_ids: tuple[str, ...] = Field(default_factory=tuple, max_length=8)

    @field_validator("key_messages")
    @classmethod
    def _validate_key_messages(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if any(not item or len(item) > 300 for item in value):
            raise ValueError("outline key messages must be 1-300 characters")
        return value

    @field_validator("candidate_asset_ids")
    @classmethod
    def _validate_candidate_assets(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if len(value) != len(set(value)):
            raise ValueError("outline candidate asset IDs must be unique")
        return value


class OutlinePlan(_StrictPlanningModel):
    deck_title: str = Field(min_length=1, max_length=300)
    narrative_arc: str = Field(min_length=1, max_length=1_000)
    sections: tuple[OutlineSection, ...] = Field(min_length=1, max_length=12)

    @model_validator(mode="after")
    def _validate_section_ids(self) -> OutlinePlan:
        section_ids = [section.section_id for section in self.sections]
        if len(section_ids) != len(set(section_ids)):
            raise ValueError("outline section IDs must be unique")
        return self


class CommunicationContract(_StrictPlanningModel):
    objective: str = Field(min_length=1, max_length=500)
    audience_success: str = Field(min_length=1, max_length=500)
    tone: str = Field(min_length=1, max_length=200)
    content_divergence: Literal["faithful", "selective", "transformative"]
    canvas: Literal["16:9"] = "16:9"


class ColorPalette(_StrictPlanningModel):
    primary: str = Field(pattern=_HEX_COLOR_PATTERN)
    secondary: str = Field(pattern=_HEX_COLOR_PATTERN)
    accent: str = Field(pattern=_HEX_COLOR_PATTERN)
    background: str = Field(pattern=_HEX_COLOR_PATTERN)
    text: str = Field(pattern=_HEX_COLOR_PATTERN)

    @model_validator(mode="after")
    def _validate_contrast_roles(self) -> ColorPalette:
        if self.background.lower() == self.text.lower():
            raise ValueError("design background and text colors must differ")
        return self


class TypographySpec(_StrictPlanningModel):
    title_font: str = Field(min_length=1, max_length=100)
    body_font: str = Field(min_length=1, max_length=100)
    monospace_font: str = Field(min_length=1, max_length=100)
    title_size_px: int = Field(ge=28, le=64)
    body_size_px: int = Field(ge=16, le=32)
    caption_size_px: int = Field(ge=12, le=24)

    @model_validator(mode="after")
    def _validate_hierarchy(self) -> TypographySpec:
        if not self.title_size_px > self.body_size_px > self.caption_size_px:
            raise ValueError("typography sizes must form title > body > caption")
        return self


class DesignContract(_StrictPlanningModel):
    communication: CommunicationContract
    template_strategy: TemplateMode
    visual_style: str = Field(min_length=1, max_length=500)
    palette: ColorPalette
    typography: TypographySpec
    density: Literal["sparse", "balanced", "dense"]
    image_usage: Literal["none", "source_only", "host_generated", "mixed"]
    chart_style: str = Field(min_length=1, max_length=500)
    generation_mode: Literal["native_svg"] = "native_svg"
    structure_mode: Literal["flat", "structured"] = "flat"
    formula_policy: Literal["none", "native_text", "rendered_formula"] = "none"
    refine_spec: str = Field(min_length=1, max_length=1_000)
    layout_rules: tuple[str, ...] = Field(min_length=1, max_length=8)
    accessibility_rules: tuple[str, ...] = Field(min_length=1, max_length=8)

    @field_validator("layout_rules", "accessibility_rules")
    @classmethod
    def _validate_rules(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if any(not item or len(item) > 300 for item in value):
            raise ValueError("design rules must be 1-300 characters")
        return value


class SlideIntent(_StrictPlanningModel):
    slide_id: str = Field(pattern=_ID_PATTERN)
    section_id: str = Field(pattern=_ID_PATTERN)
    sequence: int = Field(ge=1, le=40)
    title: str = Field(min_length=1, max_length=200)
    message: str = Field(min_length=1, max_length=500)
    layout: SlideLayout
    content_points: tuple[str, ...] = Field(min_length=1, max_length=5)
    asset_ids: tuple[str, ...] = Field(default_factory=tuple, max_length=2)
    visual_brief: str = Field(min_length=1, max_length=1_000)
    speaker_notes_intent: str = Field(default="", max_length=1_000)

    @field_validator("content_points")
    @classmethod
    def _validate_content_points(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if any(not item or len(item) > 300 for item in value):
            raise ValueError("slide content points must be 1-300 characters")
        return value

    @field_validator("asset_ids")
    @classmethod
    def _validate_slide_assets(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if len(value) != len(set(value)):
            raise ValueError("slide asset IDs must be unique")
        return value


class SlideIntentPlan(_StrictPlanningModel):
    deck_title: str = Field(min_length=1, max_length=300)
    slides: tuple[SlideIntent, ...] = Field(min_length=3, max_length=40)

    @model_validator(mode="after")
    def _validate_sequence(self) -> SlideIntentPlan:
        slide_ids = [slide.slide_id for slide in self.slides]
        if len(slide_ids) != len(set(slide_ids)):
            raise ValueError("slide IDs must be unique")
        sequence = [slide.sequence for slide in self.slides]
        if sequence != list(range(1, len(self.slides) + 1)):
            raise ValueError("slide sequence must be ordered and contiguous from 1")
        return self


class _SlideIntentSlotPlanBase(_StrictPlanningModel):
    """Internal lightweight envelope for exact Host page-slot generation."""

    deck_title: str = Field(min_length=1, max_length=300)
    slides: tuple[SlideIntent, ...]

    @model_validator(mode="before")
    @classmethod
    def _accept_planning_model(cls, value: object) -> object:
        if isinstance(value, BaseModel):
            return value.model_dump(mode="python")
        return value


def _build_slide_intent_schema(
    outline: OutlinePlan,
) -> type[_SlideIntentSlotPlanBase]:
    """Build the exact page-slot schema for one confirmed Outline.

    The static :class:`SlideIntentPlan` can only express a broad 3..40 page
    range.  It cannot encode request-specific arithmetic such as "exactly 14
    pages" or "the next 3 pages belong to this section".  A model can therefore
    return structurally valid JSON that violates the confirmed page budget.

    The generated schema keeps the public ``slides`` array shape and one shared
    SlideIntent definition, while setting request-specific min/max item counts.
    Pydantic therefore rejects extra or missing pages before semantic checks.
    Sequence and section membership are compiled from the confirmed Host
    schedule after generation rather than delegated to the model.
    """
    slot_count = sum(section.allocated_slides for section in outline.sections)
    field_definitions: dict[str, Any] = {
        "slides": (
            tuple[SlideIntent, ...],
            Field(min_length=slot_count, max_length=slot_count),
        )
    }
    schema = create_model(
        "SlideIntentPlan",
        __base__=_SlideIntentSlotPlanBase,
        **field_definitions,
    )
    return cast(type[_SlideIntentSlotPlanBase], schema)


def _slide_slot_contract(outline: OutlinePlan) -> str:
    """Render the confirmed sequence-to-section schedule for model prompts."""
    lines: list[str] = []
    sequence = 1
    for section in outline.sections:
        for _ in range(section.allocated_slides):
            lines.append(
                f"  slot {sequence:02d}: sequence={sequence}, "
                f"section_id={section.section_id} ({section.title})"
            )
            sequence += 1
    return "\n".join(lines)


def _compile_slide_intent_slots(
    outline: OutlinePlan,
    generated: _SlideIntentSlotPlanBase,
) -> SlideIntentPlan:
    """Compile model-authored content into the confirmed Host page slots."""
    section_schedule: list[str] = []
    for section in outline.sections:
        section_schedule.extend([section.section_id] * section.allocated_slides)
    compiled = tuple(
        slide.model_copy(
            update={"sequence": sequence, "section_id": section_id}
        )
        for sequence, (slide, section_id) in enumerate(
            zip(generated.slides, section_schedule),
            start=1,
        )
    )
    return SlideIntentPlan(deck_title=generated.deck_title, slides=compiled)


class PlanningConfirmation(_StrictPlanningModel):
    stage: ConfirmationStage
    actor: Literal["user"] = "user"
    artifact_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


def _model_fingerprint(model: BaseModel) -> str:
    canonical = json.dumps(
        model.model_dump(mode="json", exclude_none=True),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


_PHASE_RANK: Mapping[PlanningPhase, int] = {
    PlanningPhase.NEW: 0,
    PlanningPhase.OUTLINE_READY: 1,
    PlanningPhase.OUTLINE_CONFIRMED: 2,
    PlanningPhase.DESIGN_READY: 3,
    PlanningPhase.DESIGN_CONFIRMED: 4,
    PlanningPhase.SLIDES_READY: 5,
    PlanningPhase.PLAN_CONFIRMED: 6,
}


class PlanningSnapshot(_StrictPlanningModel):
    schema_version: Literal[1] = 1
    request: PlanningRequest
    phase: PlanningPhase
    revision: int = Field(ge=0)
    model_identity: str = Field(min_length=1, max_length=300)
    outline: OutlinePlan | None = None
    design: DesignContract | None = None
    slides: SlideIntentPlan | None = None
    outline_confirmation: PlanningConfirmation | None = None
    design_confirmation: PlanningConfirmation | None = None
    plan_confirmation: PlanningConfirmation | None = None
    cancelled_from: PlanningPhase | None = None
    cancellation_reason: str = Field(default="", max_length=500)

    @property
    def ready_for_authoring(self) -> bool:
        return self.phase == PlanningPhase.PLAN_CONFIRMED

    @model_validator(mode="after")
    def _validate_snapshot(self) -> PlanningSnapshot:
        phase = self.phase
        if phase == PlanningPhase.CANCELLED:
            if (
                self.cancelled_from is None
                or self.cancelled_from in {
                    PlanningPhase.CANCELLED,
                    PlanningPhase.PLAN_CONFIRMED,
                }
                or not self.cancellation_reason
            ):
                raise ValueError("cancelled snapshot requires resumable phase and reason")
            phase = self.cancelled_from
        elif self.cancelled_from is not None or self.cancellation_reason:
            raise ValueError("active snapshot cannot retain cancellation metadata")

        rank = _PHASE_RANK.get(phase)
        if rank is None:
            raise ValueError(f"unsupported planning phase: {phase}")
        self._require_ranked_field(rank, 1, self.outline, "outline")
        self._require_ranked_field(
            rank,
            2,
            self.outline_confirmation,
            "outline_confirmation",
        )
        self._require_ranked_field(rank, 3, self.design, "design")
        self._require_ranked_field(
            rank,
            4,
            self.design_confirmation,
            "design_confirmation",
        )
        self._require_ranked_field(rank, 5, self.slides, "slides")
        self._require_ranked_field(
            rank,
            6,
            self.plan_confirmation,
            "plan_confirmation",
        )

        if self.outline is not None:
            _validate_outline_for_request(self.request, self.outline)
        if self.design is not None:
            _validate_design_for_request(self.request, self.design)
        if self.slides is not None:
            if self.outline is None:
                raise ValueError("slide plan requires outline")
            _validate_slides_for_request(self.request, self.outline, self.slides)

        self._validate_confirmation(
            self.outline_confirmation,
            ConfirmationStage.OUTLINE,
            self.outline,
        )
        self._validate_confirmation(
            self.design_confirmation,
            ConfirmationStage.DESIGN,
            self.design,
        )
        self._validate_confirmation(
            self.plan_confirmation,
            ConfirmationStage.PLAN,
            self.slides,
        )
        return self

    @staticmethod
    def _require_ranked_field(
        rank: int,
        threshold: int,
        value: object | None,
        field: str,
    ) -> None:
        if rank >= threshold and value is None:
            raise ValueError(f"planning phase requires {field}")
        if rank < threshold and value is not None:
            raise ValueError(f"planning phase cannot contain {field}")

    @staticmethod
    def _validate_confirmation(
        confirmation: PlanningConfirmation | None,
        stage: ConfirmationStage,
        artifact: BaseModel | None,
    ) -> None:
        if confirmation is None:
            return
        if artifact is None or confirmation.stage != stage:
            raise ValueError(f"invalid {stage.value} confirmation")
        if confirmation.artifact_sha256 != _model_fingerprint(artifact):
            raise ValueError(f"{stage.value} confirmation fingerprint mismatch")


class GenerateOutline(_StrictPlanningModel):
    action: Literal["generate_outline"] = "generate_outline"


class ConfirmOutline(_StrictPlanningModel):
    action: Literal["confirm_outline"] = "confirm_outline"
    actor: Literal["user"] = "user"
    outline: OutlinePlan | None = None


class GenerateDesign(_StrictPlanningModel):
    action: Literal["generate_design"] = "generate_design"


class ConfirmDesign(_StrictPlanningModel):
    action: Literal["confirm_design"] = "confirm_design"
    actor: Literal["user"] = "user"
    design: DesignContract | None = None


class GenerateSlides(_StrictPlanningModel):
    action: Literal["generate_slides"] = "generate_slides"


class ConfirmPlan(_StrictPlanningModel):
    action: Literal["confirm_plan"] = "confirm_plan"
    actor: Literal["user"] = "user"
    slides: SlideIntentPlan | None = None


class CancelPlanning(_StrictPlanningModel):
    action: Literal["cancel"] = "cancel"
    reason: str = Field(default="user requested cancellation", min_length=1, max_length=500)


class ResumePlanning(_StrictPlanningModel):
    action: Literal["resume"] = "resume"


PlanningCommand = (
    GenerateOutline
    | ConfirmOutline
    | GenerateDesign
    | ConfirmDesign
    | GenerateSlides
    | ConfirmPlan
    | CancelPlanning
    | ResumePlanning
)


class HostPlanningError(RuntimeError):
    """A named Planning State Machine operation failed."""

    def __init__(self, code: str, phase: PlanningPhase, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.phase = phase


class HostPlanningTransitionError(HostPlanningError):
    """A command is not legal for the current stable phase."""


class HostPlanningModelError(HostPlanningError):
    """The Host model Adapter failed or returned invalid structured data."""


PlanningModelT = TypeVar("PlanningModelT", bound=BaseModel)


class StructuredPlanningModel(Protocol):
    @property
    def identity(self) -> str: ...

    def generate_structured(
        self,
        *,
        prompt: str,
        schema: type[PlanningModelT],
        system_prompt: str,
        max_tokens: int,
        max_schema_retries: int = 1,
    ) -> PlanningModelT: ...


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


class HostAIClientPlanningAdapter:
    """Adapter that keeps all model credentials inside the Host AIClient."""

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
        schema: type[PlanningModelT],
        system_prompt: str,
        max_tokens: int,
        max_schema_retries: int = 1,
    ) -> PlanningModelT:
        try:
            data = self._client.generate_structured(
                prompt=prompt,
                schema=schema,
                system_prompt=system_prompt,
                temperature=0.3,
                max_tokens=max_tokens,
                max_schema_retries=max_schema_retries,
            )
            return schema.model_validate(data)
        except AIClientTimeoutError as error:
            raise HostPlanningModelError(
                "model_timeout",
                PlanningPhase.NEW,
                "Host structured planning model timed out; no schema retry was "
                "attempted. Retry when the service is stable or explicitly select "
                "the built-in backend.",
            ) from error
        except (ReportSchemaError, ValidationError) as error:
            raise HostPlanningModelError(
                "model_schema_invalid",
                PlanningPhase.NEW,
                f"Host structured planning model returned invalid schema: "
                f"{type(error).__name__}",
            ) from error
        except AIClientError as error:
            raise HostPlanningModelError(
                "model_failed",
                PlanningPhase.NEW,
                f"Host structured planning model failed: {type(error).__name__}",
            ) from error


_OUTLINE_SYSTEM_PROMPT = """You are the Host presentation strategist.
Return only the requested JSON schema. Build a decision-first technical story,
allocate exactly the requested slide count, use only supplied asset IDs, and do
not claim confirmation. Do not output SVG, PowerPoint, commands, or file paths.

CRITICAL — REQUIRED ASSET COVERAGE:
Every asset with required=true in the planning request MUST appear in exactly
one section's candidate_asset_ids.  You may optionally include supplementary
assets, but REQUIRED assets must never be omitted.  Count them explicitly
before responding: the number of required asset IDs allocated across all
sections MUST equal the number of assets with required=true in the request.

CRITICAL — SECTION CANDIDATE LIMIT:
Each Outline section may contain at MOST 8 candidate_asset_ids (enforced by
OutlineSection schema).  Do NOT place more than 8 assets in one section —
Pydantic will reject the entire OutlinePlan before any downstream validator
can run.

If a semantic topic contains more than 8 assets, you MUST split it into
multiple semantically coherent sections.  For example, a "温度补偿分析" topic
with 12 diagnostic assets must become at least two sections:
  - section: temperature_diagnosis_part_1 (≤8 candidates)
  - section: temperature_diagnosis_part_2 (≤8 candidates)

Each split section must still satisfy the slide capacity constraint below.

CRITICAL — SECTION SLIDE CAPACITY:
Each slide can carry at MOST 2 assets (enforced by SlideIntent schema).
Therefore, for each section:
  allocated_slides MUST be >= ceil(required_assets_in_section / 2)

For example, a section with 6 Required assets needs at least 3 slides
(because 6 assets / 2 per slide = 3 slides minimum).

Before responding, verify FOR EVERY SECTION:
  len(candidate_asset_ids) <= 8
  required_asset_count <= allocated_slides × 2

If any section violates either rule, restructure sections or redistribute
the page budget.  The total allocated_slides across ALL sections MUST still
equal the requested_slide_count."""

_DESIGN_SYSTEM_PROMPT = """You are the Host presentation design strategist.
Return only the requested JSON schema. Produce a coherent communication,
visual, typography, accessibility, and production contract for a professional
technical deck. Respect the confirmed outline and template mode. The current
Host SVG pipeline requires generation_mode=native_svg and structure_mode=flat;
validated templates provide style guidance, not object mirroring. Do not claim
user confirmation and do not execute or describe tool commands."""

_SLIDES_SYSTEM_PROMPT = """You are the Host slide-intent strategist.
Return only the requested JSON schema. Create one conclusion-led message per
slide, contiguous sequence numbers, exact section allocations, and semantic
asset placement. Use only supplied asset IDs and place every required asset
exactly once. Do not create SVG/PPTX or claim user confirmation.

CRITICAL — EXACT SLIDE COUNT:
You MUST return EXACTLY as many slides as specified in the planning
request's requested_slide_count field.  The host validator will reject
any plan whose slide count does not equal this number.  Do NOT add or
remove slides — the count is a hard contract.
Before responding, verify: len(slides) == requested_slide_count.

CRITICAL — REQUIRED ASSET COVERAGE:
Every asset with required=true in the planning request MUST appear on at
least one slide.  Do NOT skip or omit required assets — even if you think
they are visually similar to other assets.  The host validator will reject
any plan that omits a required asset.

CRITICAL — SECTION ASSET SCOPING:
Each slide belongs to exactly one outline section (slide.section_id).  A
slide's asset_ids MUST only reference assets listed in that section's
candidate_asset_ids.  Cross-section asset usage is FORBIDDEN and will cause
validation failure.  Before responding, verify: for every slide, every
asset_id ∈ the corresponding section's candidate_asset_ids.

CONSTRAINT PRIORITY: If you cannot satisfy all constraints within the
allocated slide count:
1. EXACT SLIDE COUNT — never change the number of slides.
2. REQUIRED ASSET COVERAGE — do not skip required assets.
3. SECTION SCOPING — place each asset in its correct section.
If still impossible, satisfy higher-priority rules first."""

# ── Dynamic per-slide capacity appendix (read from schema metadata, not hardcoded) ──
_MAX_ASSETS_PER_SLIDE = 0
for _meta in SlideIntent.model_fields["asset_ids"].metadata:
    if hasattr(_meta, "max_length"):
        _MAX_ASSETS_PER_SLIDE = _meta.max_length
        break
if _MAX_ASSETS_PER_SLIDE == 0:
    _MAX_ASSETS_PER_SLIDE = 2  # defensive fallback

_SLIDES_CAPACITY_APPENDIX = f"""
CRITICAL — PER-SLIDE ASSET CAPACITY:
EVERY slide: len(asset_ids) <= {_MAX_ASSETS_PER_SLIDE}
No slide may contain more than {_MAX_ASSETS_PER_SLIDE} asset IDs.
The Pydantic schema enforces max_length={_MAX_ASSETS_PER_SLIDE} on every
slide's asset_ids field.  If you place {_MAX_ASSETS_PER_SLIDE + 1} or more
assets on a single slide, the ENTIRE SlideIntentPlan will be rejected.

Before responding, verify FOR EVERY SLIDE:
  len(slide.asset_ids) <= {_MAX_ASSETS_PER_SLIDE}"""


class HostPlanningStateMachine:
    """Apply explicit commands to immutable Planning Snapshots."""

    def __init__(
        self,
        model: StructuredPlanningModel,
        *,
        cancel_check: Callable[[], bool] | None = None,
    ) -> None:
        if not model.identity.strip():
            raise ValueError("planning model identity must be non-empty")
        self._model = model
        self._cancel_check = cancel_check

    def start(self, request: PlanningRequest) -> PlanningSnapshot:
        return PlanningSnapshot(
            request=request,
            phase=PlanningPhase.NEW,
            revision=0,
            model_identity=self._model.identity,
        )

    @staticmethod
    def serialize(snapshot: PlanningSnapshot) -> str:
        return snapshot.model_dump_json(exclude_none=True)

    @staticmethod
    def restore(serialized: str) -> PlanningSnapshot:
        return PlanningSnapshot.model_validate_json(serialized)

    def apply(
        self,
        snapshot: PlanningSnapshot,
        command: PlanningCommand,
    ) -> PlanningSnapshot:
        if snapshot.model_identity != self._model.identity:
            raise HostPlanningTransitionError(
                "model_identity_mismatch",
                snapshot.phase,
                "Planning Snapshot belongs to a different Host model Adapter",
            )
        if isinstance(command, ResumePlanning):
            return self._resume(snapshot)
        if snapshot.phase == PlanningPhase.CANCELLED:
            raise HostPlanningTransitionError(
                "snapshot_cancelled",
                snapshot.phase,
                "Cancelled Planning Snapshot must be resumed before other commands",
            )
        if isinstance(command, CancelPlanning):
            return self._cancel(snapshot, command.reason)
        if isinstance(command, GenerateOutline):
            return self._generate_outline(snapshot)
        if isinstance(command, ConfirmOutline):
            return self._confirm_outline(snapshot, command)
        if isinstance(command, GenerateDesign):
            return self._generate_design(snapshot)
        if isinstance(command, ConfirmDesign):
            return self._confirm_design(snapshot, command)
        if isinstance(command, GenerateSlides):
            return self._generate_slides(snapshot)
        if isinstance(command, ConfirmPlan):
            return self._confirm_plan(snapshot, command)
        raise HostPlanningTransitionError(
            "unknown_command",
            snapshot.phase,
            f"Unsupported planning command: {type(command).__name__}",
        )

    def _generate_outline(self, snapshot: PlanningSnapshot) -> PlanningSnapshot:
        self._require_phase(snapshot, PlanningPhase.NEW, "generate outline")
        cancelled = self._cancel_if_requested(snapshot)
        if cancelled is not None:
            return cancelled

        _MAX_CORRECTIVE_RETRIES = 2
        last_missing: list[str] = []
        last_capacity_violations: str = ""  # capacity error detail for corrective retry
        last_schema_error: str = ""  # Pydantic schema error detail for corrective retry

        for attempt in range(_MAX_CORRECTIVE_RETRIES + 1):
            if attempt > 0 and self._is_cancel_requested():
                return self._cancel(
                    snapshot, "cancellation requested before outline retry"
                )

            # ── Prompt selection ──
            if attempt == 0:
                prompt = _outline_prompt(snapshot.request)
            elif last_schema_error:
                # Pydantic schema failure (e.g. candidate_asset_ids too long)
                prompt = _outline_schema_corrective_prompt(
                    snapshot.request, last_schema_error,
                )
            elif last_capacity_violations:
                prompt = _outline_capacity_corrective_prompt(
                    snapshot.request, last_capacity_violations,
                )
            else:
                prompt = _outline_corrective_prompt(
                    snapshot.request, last_missing
                )

            # ── Model call with schema-failure catch ──
            try:
                outline = self._call_model(
                    snapshot,
                    schema=OutlinePlan,
                    prompt=prompt,
                    system_prompt=_OUTLINE_SYSTEM_PROMPT,
                    max_tokens=4_096,
                )
            except HostPlanningModelError as model_error:
                if model_error.code != "model_schema_invalid":
                    raise
                # ── Pydantic schema failure: OutlinePlan not constructable ──
                # This catches cases like candidate_asset_ids max_length
                # violation where the model produces valid JSON but Pydantic
                # rejects it before any semantic validator can run.
                schema_detail = _extract_schema_error_detail_from_exc(model_error)
                if self._is_cancel_requested():
                    return self._cancel(
                        snapshot, "cancellation requested after schema failure"
                    )
                last_schema_error = schema_detail
                last_missing = []
                last_capacity_violations = ""
                if attempt < _MAX_CORRECTIVE_RETRIES:
                    continue  # corrective retry with schema error detail
                raise HostPlanningModelError(
                    "outline_schema_exhausted",
                    snapshot.phase,
                    f"Outline schema validation failed after "
                    f"{_MAX_CORRECTIVE_RETRIES + 1} attempts. "
                    f"Last error: {schema_detail}",
                ) from model_error

            if self._is_cancel_requested():
                return self._cancel(
                    snapshot, "cancellation requested after outline call"
                )

            try:
                _validate_outline_for_request(snapshot.request, outline)
            except ValueError as error:
                error_msg = str(error)
                # ── Missing required assets ──
                if "omits required assets" in error_msg:
                    last_missing = _parse_missing_asset_ids(error_msg)
                    last_capacity_violations = ""
                    last_schema_error = ""
                    if attempt < _MAX_CORRECTIVE_RETRIES and last_missing:
                        continue  # corrective retry for missing assets
                    raise HostPlanningModelError(
                        "outline_required_assets_missing",
                        snapshot.phase,
                        f"Outline still omits required assets after "
                        f"{_MAX_CORRECTIVE_RETRIES} corrective retries: "
                        f"{sorted(last_missing)}",
                    ) from error
                # ── Section capacity infeasible ──
                if "capacity infeasible" in error_msg:
                    rebalanced = _rebalance_outline_slide_allocations(
                        snapshot.request,
                        outline,
                    )
                    if rebalanced is not None:
                        _validate_outline_for_request(snapshot.request, rebalanced)
                        return self._transition(
                            snapshot,
                            phase=PlanningPhase.OUTLINE_READY,
                            outline=rebalanced,
                        )
                    last_capacity_violations = error_msg
                    last_missing = []
                    last_schema_error = ""
                    if attempt < _MAX_CORRECTIVE_RETRIES:
                        continue  # corrective retry for capacity
                    raise HostPlanningModelError(
                        "outline_capacity_infeasible",
                        snapshot.phase,
                        f"Outline section capacity still infeasible after "
                        f"{_MAX_CORRECTIVE_RETRIES} corrective retries: "
                        f"{error_msg}",
                    ) from error
                # ── Non-retryable error ──
                raise HostPlanningModelError(
                    "model_contract_invalid",
                    snapshot.phase,
                    f"Host model returned an invalid outline: {error}",
                ) from error

            # Validation passed
            return self._transition(
                snapshot,
                phase=PlanningPhase.OUTLINE_READY,
                outline=outline,
            )

        # Should not reach here — defensive
        raise HostPlanningModelError(
            "outline_retry_exhausted",
            snapshot.phase,
            "Outline generation failed after all corrective retries",
        )

    def _confirm_outline(
        self,
        snapshot: PlanningSnapshot,
        command: ConfirmOutline,
    ) -> PlanningSnapshot:
        self._require_phase(snapshot, PlanningPhase.OUTLINE_READY, "confirm outline")
        outline = command.outline or snapshot.outline
        if outline is None:
            raise HostPlanningTransitionError(
                "missing_outline",
                snapshot.phase,
                "Outline confirmation requires an outline",
            )
        self._validate_user_confirmation(
            snapshot,
            "outline",
            lambda: _validate_outline_for_request(snapshot.request, outline),
        )
        confirmation = PlanningConfirmation(
            stage=ConfirmationStage.OUTLINE,
            actor=command.actor,
            artifact_sha256=_model_fingerprint(outline),
        )
        return self._transition(
            snapshot,
            phase=PlanningPhase.OUTLINE_CONFIRMED,
            outline=outline,
            outline_confirmation=confirmation,
        )

    def _generate_design(self, snapshot: PlanningSnapshot) -> PlanningSnapshot:
        self._require_phase(snapshot, PlanningPhase.OUTLINE_CONFIRMED, "generate design")
        cancelled = self._cancel_if_requested(snapshot)
        if cancelled is not None:
            return cancelled
        if snapshot.outline is None:
            raise HostPlanningTransitionError(
                "missing_outline",
                snapshot.phase,
                "Design generation requires confirmed outline",
            )
        design = self._call_model(
            snapshot,
            schema=DesignContract,
            prompt=_design_prompt(snapshot.request, snapshot.outline),
            system_prompt=_DESIGN_SYSTEM_PROMPT,
            max_tokens=4_096,
        )
        if self._is_cancel_requested():
            return self._cancel(snapshot, "cancellation requested after design call")
        self._validate_generated_artifact(
            snapshot,
            "design contract",
            lambda: _validate_design_for_request(snapshot.request, design),
        )
        return self._transition(
            snapshot,
            phase=PlanningPhase.DESIGN_READY,
            design=design,
        )

    def _confirm_design(
        self,
        snapshot: PlanningSnapshot,
        command: ConfirmDesign,
    ) -> PlanningSnapshot:
        self._require_phase(snapshot, PlanningPhase.DESIGN_READY, "confirm design")
        design = command.design or snapshot.design
        if design is None:
            raise HostPlanningTransitionError(
                "missing_design",
                snapshot.phase,
                "Design confirmation requires a design contract",
            )
        self._validate_user_confirmation(
            snapshot,
            "design contract",
            lambda: _validate_design_for_request(snapshot.request, design),
        )
        confirmation = PlanningConfirmation(
            stage=ConfirmationStage.DESIGN,
            actor=command.actor,
            artifact_sha256=_model_fingerprint(design),
        )
        return self._transition(
            snapshot,
            phase=PlanningPhase.DESIGN_CONFIRMED,
            design=design,
            design_confirmation=confirmation,
        )

    def _generate_slides(self, snapshot: PlanningSnapshot) -> PlanningSnapshot:
        self._require_phase(snapshot, PlanningPhase.DESIGN_CONFIRMED, "generate slides")
        cancelled = self._cancel_if_requested(snapshot)
        if cancelled is not None:
            return cancelled
        outline = snapshot.outline
        design = snapshot.design
        if outline is None or design is None:
            raise HostPlanningTransitionError(
                "missing_design_inputs",
                snapshot.phase,
                "Slide generation requires confirmed outline and design",
            )

        # ── Pre-flight: constraint feasibility ──
        feasible, reason = _check_slide_constraint_feasibility(
            snapshot.request, outline,
        )
        if not feasible:
            raise HostPlanningModelError(
                "slides_constraint_infeasible",
                snapshot.phase,
                f"Confirmed outline constraints are not satisfiable: {reason}",
            )

        # ── Single retry owner: Host owns the full SlideIntent lifecycle ──
        _MAX_CORRECTIVE_RETRIES = 2
        model_calls: list[SlidePlanViolations | None] = []  # attempt ledger
        _schema_errors: list[str] = []  # error details for schema-failed attempts
        slide_slot_schema = _build_slide_intent_schema(outline)

        for attempt in range(_MAX_CORRECTIVE_RETRIES + 1):
            if attempt > 0 and self._is_cancel_requested():
                return self._cancel(
                    snapshot, "cancellation requested before slide retry"
                )

            # ── Prompt selection ──
            if attempt == 0:
                _prompt_type = "initial"
                prompt = _slides_prompt(snapshot.request, outline, design)
            else:
                # Unified corrective prompt with COMPLETE violation set
                last_violations = model_calls[-1]
                if last_violations is None:
                    # Schema failure — no violations to report, do schema retry
                    _prompt_type = "schema_corrective"
                    _error_detail = _schema_errors[-1] if _schema_errors else ""
                    prompt = _slides_schema_corrective_prompt(
                        snapshot.request, outline, design,
                        error_detail=_error_detail,
                    )
                else:
                    _prompt_type = "semantic_corrective"
                    prompt = _unified_slides_corrective_prompt(
                        snapshot.request, outline, design, last_violations,
                    )

            # ── Model call (Host is sole retry owner — no AIClient schema retries) ──
            try:
                generated_slots = self._call_model(
                    snapshot,
                    schema=slide_slot_schema,
                    prompt=prompt,
                    system_prompt=_SLIDES_SYSTEM_PROMPT + _SLIDES_CAPACITY_APPENDIX,
                    max_tokens=8_192,
                    max_schema_retries=0,  # Host owns retry lifecycle
                )
                slides = _compile_slide_intent_slots(outline, generated_slots)
            except HostPlanningModelError as model_error:
                # ── Capture error detail for next corrective prompt ──
                _err_detail = _extract_schema_error_detail_from_exc(model_error)
                _schema_errors.append(_err_detail)

                model_calls.append(None)  # schema failure — no violations
                if attempt < _MAX_CORRECTIVE_RETRIES:
                    continue
                raise HostPlanningModelError(
                    "slides_schema_failure",
                    snapshot.phase,
                    f"Slide-intent structured generation failed after "
                    f"{_MAX_CORRECTIVE_RETRIES + 1} attempts. "
                    f"Last error: {model_error}",
                ) from model_error

            if self._is_cancel_requested():
                return self._cancel(
                    snapshot, "cancellation requested after slide call"
                )

            # ── Aggregated semantic validation ──
            violations = _collect_slide_violations(
                snapshot.request, outline, slides,
            )
            model_calls.append(violations)

            if violations.is_valid:
                # All constraints satisfied simultaneously
                return self._transition(
                    snapshot,
                    phase=PlanningPhase.SLIDES_READY,
                    slides=slides,
                )

            # ── Check for non-retryable violations ──
            if violations.deck_title_mismatch or (
                violations.unknown_sections and not violations.cross_section_assets
                and not violations.missing_required_assets
            ):
                # Deck title or purely unknown sections are not retryable
                raise HostPlanningModelError(
                    "model_contract_invalid",
                    snapshot.phase,
                    f"Host model returned an invalid slide-intent plan: "
                    f"{violations.summary()}",
                )

            if attempt < _MAX_CORRECTIVE_RETRIES:
                continue

            # ── Retries exhausted ──
            raise HostPlanningModelError(
                "slides_semantic_exhausted",
                snapshot.phase,
                f"Slide-intent plan still invalid after "
                f"{_MAX_CORRECTIVE_RETRIES} corrective retries. "
                f"{violations.summary()}",
            )

        # Should not reach here — defensive
        raise HostPlanningModelError(
            "slides_retry_exhausted",
            snapshot.phase,
            "Slide generation failed after all corrective retries",
        )

    def _confirm_plan(
        self,
        snapshot: PlanningSnapshot,
        command: ConfirmPlan,
    ) -> PlanningSnapshot:
        self._require_phase(snapshot, PlanningPhase.SLIDES_READY, "confirm plan")
        slides = command.slides or snapshot.slides
        outline = snapshot.outline
        if slides is None or outline is None:
            raise HostPlanningTransitionError(
                "missing_slide_plan",
                snapshot.phase,
                "Plan confirmation requires slide intents and outline",
            )
        self._validate_user_confirmation(
            snapshot,
            "slide-intent plan",
            lambda: _validate_slides_for_request(
                snapshot.request,
                outline,
                slides,
            ),
        )
        confirmation = PlanningConfirmation(
            stage=ConfirmationStage.PLAN,
            actor=command.actor,
            artifact_sha256=_model_fingerprint(slides),
        )
        return self._transition(
            snapshot,
            phase=PlanningPhase.PLAN_CONFIRMED,
            slides=slides,
            plan_confirmation=confirmation,
        )

    def _cancel(
        self,
        snapshot: PlanningSnapshot,
        reason: str,
    ) -> PlanningSnapshot:
        if snapshot.phase in {PlanningPhase.CANCELLED, PlanningPhase.PLAN_CONFIRMED}:
            raise HostPlanningTransitionError(
                "cancel_not_allowed",
                snapshot.phase,
                f"Planning cannot be cancelled from {snapshot.phase.value}",
            )
        return self._transition(
            snapshot,
            phase=PlanningPhase.CANCELLED,
            cancelled_from=snapshot.phase,
            cancellation_reason=reason,
        )

    def _resume(self, snapshot: PlanningSnapshot) -> PlanningSnapshot:
        if snapshot.phase != PlanningPhase.CANCELLED or snapshot.cancelled_from is None:
            raise HostPlanningTransitionError(
                "resume_not_allowed",
                snapshot.phase,
                "Only a cancelled Planning Snapshot can be resumed",
            )
        return self._transition(
            snapshot,
            phase=snapshot.cancelled_from,
            cancelled_from=None,
            cancellation_reason="",
        )

    def _cancel_if_requested(
        self,
        snapshot: PlanningSnapshot,
    ) -> PlanningSnapshot | None:
        if self._is_cancel_requested():
            return self._cancel(snapshot, "cancellation requested before model call")
        return None

    def _is_cancel_requested(self) -> bool:
        return self._cancel_check is not None and self._cancel_check()

    def _call_model(
        self,
        snapshot: PlanningSnapshot,
        *,
        schema: type[PlanningModelT],
        prompt: str,
        system_prompt: str,
        max_tokens: int,
        max_schema_retries: int = 1,
    ) -> PlanningModelT:
        try:
            generated = self._model.generate_structured(
                prompt=prompt,
                schema=schema,
                system_prompt=system_prompt,
                max_tokens=max_tokens,
                max_schema_retries=max_schema_retries,
            )
            return schema.model_validate(generated)
        except HostPlanningModelError as error:
            if error.phase == snapshot.phase:
                raise
            raise HostPlanningModelError(
                error.code,
                snapshot.phase,
                str(error),
            ) from error
        except ValidationError as error:
            raise HostPlanningModelError(
                "model_schema_invalid",
                snapshot.phase,
                f"Host model returned invalid {schema.__name__}",
            ) from error

    @staticmethod
    def _validate_generated_artifact(
        snapshot: PlanningSnapshot,
        artifact_name: str,
        validate: Callable[[], None],
    ) -> None:
        try:
            validate()
        except ValueError as error:
            raise HostPlanningModelError(
                "model_contract_invalid",
                snapshot.phase,
                f"Host model returned an invalid {artifact_name}: {error}",
            ) from error

    @staticmethod
    def _validate_user_confirmation(
        snapshot: PlanningSnapshot,
        artifact_name: str,
        validate: Callable[[], None],
    ) -> None:
        try:
            validate()
        except ValueError as error:
            raise HostPlanningTransitionError(
                "confirmation_invalid",
                snapshot.phase,
                f"User-confirmed {artifact_name} is invalid: {error}",
            ) from error

    @staticmethod
    def _require_phase(
        snapshot: PlanningSnapshot,
        expected: PlanningPhase,
        operation: str,
    ) -> None:
        if snapshot.phase != expected:
            raise HostPlanningTransitionError(
                "illegal_transition",
                snapshot.phase,
                f"Cannot {operation} from {snapshot.phase.value}; "
                f"expected {expected.value}",
            )

    @staticmethod
    def _transition(
        snapshot: PlanningSnapshot,
        *,
        phase: PlanningPhase,
        **updates: object,
    ) -> PlanningSnapshot:
        data = snapshot.model_dump(mode="python")
        data.update(updates)
        data["phase"] = phase
        data["revision"] = snapshot.revision + 1
        return PlanningSnapshot.model_validate(data)


def _validate_outline_for_request(
    request: PlanningRequest,
    outline: OutlinePlan,
) -> None:
    allocated = sum(section.allocated_slides for section in outline.sections)
    if allocated != request.requested_slide_count:
        raise ValueError(
            "outline allocated slide count must equal requested_slide_count"
        )
    known_assets = {asset.asset_id for asset in request.assets}
    required_assets = {
        asset.asset_id for asset in request.assets if asset.required
    }
    planned_assets: list[str] = []
    for section in outline.sections:
        unknown = set(section.candidate_asset_ids) - known_assets
        if unknown:
            raise ValueError(f"outline references unknown assets: {sorted(unknown)}")
        planned_assets.extend(section.candidate_asset_ids)
    if len(planned_assets) != len(set(planned_assets)):
        raise ValueError("outline assets cannot be assigned to multiple sections")
    missing_required = required_assets - set(planned_assets)
    if missing_required:
        raise ValueError(
            f"outline omits required assets: {sorted(missing_required)}"
        )
    # ── Section asset capacity ──
    feasible, reason = _check_slide_constraint_feasibility(request, outline)
    if not feasible:
        raise ValueError(reason)


def _validate_design_for_request(
    request: PlanningRequest,
    design: DesignContract,
) -> None:
    if design.template_strategy != request.template_mode:
        raise ValueError("design template_strategy must match planning request")
    if design.structure_mode != "flat":
        raise ValueError("Host SVG workflow currently requires flat structure_mode")


# ═══════════════════════════════════════════════════════════════════
# SlidePlanViolations — aggregated semantic violation representation
# ═══════════════════════════════════════════════════════════════════


class SlidePlanViolations:
    """All detectable semantic violations in a SlideIntentPlan, collected in one
    pass instead of fail-fast.  Used internally by the Host retry loop to give
    the model a COMPLETE picture of what must be fixed, preventing whack-a-mole.
    """

    def __init__(self) -> None:
        self.count_mismatch: tuple[int, int] | None = None  # (actual, requested)
        self.deck_title_mismatch: tuple[str, str] | None = None  # (actual, expected)
        self.unknown_sections: list[str] = []
        self.unknown_assets: list[str] = []
        self.cross_section_assets: dict[str, str] = {}  # asset_id → owning section
        self.section_count_mismatches: dict[str, tuple[int, int]] = {}  # section → (actual, allocated)
        self.duplicate_assets: list[str] = []
        self.missing_required_assets: list[str] = []

        # Set when a dependency prevents computing downstream checks
        self.skipped_checks: list[str] = []

    @property
    def is_valid(self) -> bool:
        return (
            self.count_mismatch is None
            and self.deck_title_mismatch is None
            and not self.unknown_sections
            and not self.unknown_assets
            and not self.cross_section_assets
            and not self.section_count_mismatches
            and not self.duplicate_assets
            and not self.missing_required_assets
        )

    def summary(self) -> str:
        parts: list[str] = []
        if self.count_mismatch:
            actual, req = self.count_mismatch
            parts.append(f"Slide count mismatch: got {actual}, need {req}")
        if self.deck_title_mismatch:
            actual, expected = self.deck_title_mismatch
            parts.append(f"Deck title mismatch: got '{actual}', expected '{expected}'")
        if self.unknown_sections:
            parts.append(f"Unknown sections: {sorted(self.unknown_sections)}")
        if self.unknown_assets:
            parts.append(f"Unknown assets: {sorted(self.unknown_assets)}")
        if self.cross_section_assets:
            items = [f"{aid}→{sec}" for aid, sec in sorted(self.cross_section_assets.items())]
            parts.append(f"Cross-section assets: {items}")
        if self.section_count_mismatches:
            items = [f"{sid}:{act}/{alloc}" for sid, (act, alloc) in sorted(self.section_count_mismatches.items())]
            parts.append(f"Section count mismatches: {items}")
        if self.duplicate_assets:
            parts.append(f"Duplicate assets: {sorted(self.duplicate_assets)}")
        if self.missing_required_assets:
            parts.append(f"Missing required assets: {sorted(self.missing_required_assets)}")
        if self.skipped_checks:
            parts.append(f"Skipped checks (dependency unsafe): {self.skipped_checks}")
        return "; ".join(parts) if parts else "no violations"


def _collect_slide_violations(
    request: PlanningRequest,
    outline: OutlinePlan,
    slides: SlideIntentPlan,
) -> SlidePlanViolations:
    """Collect ALL detectable semantic violations in one pass.

    Unlike the fail-fast _validate_slides_for_request(), this gathers every
    violation that can be safely computed given the input state.  Checks that
    depend on prior legality (e.g. cross-section when sections are unknown)
    are skipped with an explicit marker rather than crashing.
    """
    v = SlidePlanViolations()

    # ── 1. Count ──
    if len(slides.slides) != request.requested_slide_count:
        v.count_mismatch = (len(slides.slides), request.requested_slide_count)

    # ── 2. Deck title ──
    if slides.deck_title != outline.deck_title:
        v.deck_title_mismatch = (slides.deck_title, outline.deck_title)

    # ── Build lookup tables ──
    known_assets = {asset.asset_id for asset in request.assets}
    required_assets = {asset.asset_id for asset in request.assets if asset.required}
    sections = {section.section_id: section for section in outline.sections}
    section_counts: dict[str, int] = {sid: 0 for sid in sections}
    placed_assets: list[str] = []
    unknown_sections_found = False

    # ── 3–6. Per-slide checks ──
    for slide in slides.slides:
        # 3. Unknown section
        section = sections.get(slide.section_id)
        if section is None:
            v.unknown_sections.append(slide.section_id)
            unknown_sections_found = True
        else:
            section_counts[slide.section_id] += 1

        # 4. Unknown assets
        unknown = sorted(set(slide.asset_ids) - known_assets)
        if unknown:
            v.unknown_assets.extend(unknown)

        # 5. Cross-section (only safe when section is known)
        if section is not None:
            outside = sorted(set(slide.asset_ids) - set(section.candidate_asset_ids))
            for asset_id in outside:
                # Find owning section
                owner = _find_owning_section(outline, asset_id)
                if asset_id not in v.cross_section_assets:
                    v.cross_section_assets[asset_id] = owner

        # 6. Track placed assets (for duplicate + coverage)
        placed_assets.extend(slide.asset_ids)

    # ── 7. Section count mismatches (only safe when no unknown sections) ──
    if not unknown_sections_found:
        for section_id, section in sections.items():
            if section_counts[section_id] != section.allocated_slides:
                v.section_count_mismatches[section_id] = (
                    section_counts[section_id],
                    section.allocated_slides,
                )
    else:
        v.skipped_checks.append("section_count")

    # ── 8. Duplicate assets ──
    seen: dict[str, int] = {}
    for aid in placed_assets:
        seen[aid] = seen.get(aid, 0) + 1
    for aid, count in seen.items():
        if count > 1:
            v.duplicate_assets.append(aid)

    # ── 9. Missing required (safe regardless of other violations) ──
    missing = sorted(required_assets - set(placed_assets))
    if missing:
        v.missing_required_assets = missing

    # Deduplicate
    v.unknown_sections = sorted(set(v.unknown_sections))
    v.unknown_assets = sorted(set(v.unknown_assets))
    v.duplicate_assets = sorted(set(v.duplicate_assets))

    return v


def _get_max_assets_per_slide() -> int:
    """Extract max_length constraint from SlideIntent.asset_ids Field metadata.

    This is the SINGLE SOURCE OF TRUTH for per-slide asset capacity.
    Any change to SlideIntent.asset_ids max_length automatically propagates
    to all capacity validators without code duplication.
    """
    for meta in SlideIntent.model_fields["asset_ids"].metadata:
        if hasattr(meta, "max_length"):
            return meta.max_length
    return 2  # defensive fallback — should never be reached


def _get_max_candidates_per_section() -> int:
    """Extract max_length constraint from OutlineSection.candidate_asset_ids Field metadata.

    This is the SINGLE SOURCE OF TRUTH for per-section candidate capacity.
    Any change to OutlineSection.candidate_asset_ids max_length automatically
    propagates to all validators and prompts without code duplication.
    """
    for meta in OutlineSection.model_fields["candidate_asset_ids"].metadata:
        if hasattr(meta, "max_length"):
            return meta.max_length
    return 8  # defensive fallback — should never be reached


def _rebalance_outline_slide_allocations(
    request: PlanningRequest,
    outline: OutlinePlan,
) -> OutlinePlan | None:
    """Repair section page arithmetic without changing outline semantics.

    The model owns section boundaries, messages, and asset ownership.  The Host
    owns the exact numeric invariants that follow from those choices.  When the
    total page budget is already correct and donor sections have spare pages,
    move the minimum number of pages needed to make every section capable of
    carrying its Required assets.  Return ``None`` when no such redistribution
    exists so the normal bounded corrective-model path remains fail-closed.
    """
    allocations = [section.allocated_slides for section in outline.sections]
    if sum(allocations) != request.requested_slide_count:
        return None

    required_ids = {asset.asset_id for asset in request.assets if asset.required}
    max_per_slide = _get_max_assets_per_slide()
    minimums: list[int] = []
    for section in outline.sections:
        required_count = sum(
            asset_id in required_ids
            for asset_id in section.candidate_asset_ids
        )
        capacity_minimum = (
            required_count + max_per_slide - 1
        ) // max_per_slide
        minimums.append(max(1, capacity_minimum))

    receivers = [
        index
        for index, (allocated, minimum) in enumerate(zip(allocations, minimums))
        if allocated < minimum
    ]
    if not receivers:
        return outline

    # Largest surplus first minimises the number of sections whose page budget
    # changes.  Stable sorting preserves the model's narrative order for ties.
    donors = sorted(
        (
            index
            for index, (allocated, minimum) in enumerate(zip(allocations, minimums))
            if allocated > minimum
        ),
        key=lambda index: allocations[index] - minimums[index],
        reverse=True,
    )

    repaired_allocations = list(allocations)
    for receiver in receivers:
        remaining = minimums[receiver] - repaired_allocations[receiver]
        for donor in donors:
            available = repaired_allocations[donor] - minimums[donor]
            if available <= 0:
                continue
            moved = min(available, remaining)
            repaired_allocations[donor] -= moved
            repaired_allocations[receiver] += moved
            remaining -= moved
            if remaining == 0:
                break
        if remaining:
            return None

    repaired_sections: list[OutlineSection] = []
    for section, allocated_slides in zip(
        outline.sections,
        repaired_allocations,
    ):
        section_data = section.model_dump(mode="python")
        section_data["allocated_slides"] = allocated_slides
        repaired_sections.append(OutlineSection.model_validate(section_data))

    outline_data = outline.model_dump(mode="python")
    outline_data["sections"] = tuple(repaired_sections)
    repaired = OutlinePlan.model_validate(outline_data)
    feasible, _reason = _check_slide_constraint_feasibility(request, repaired)
    return repaired if feasible else None


def _check_slide_constraint_feasibility(
    request: PlanningRequest,
    outline: OutlinePlan,
) -> tuple[bool, str]:
    """Pre-flight: verify the confirmed Outline constraints are mathematically
    satisfiable BEFORE calling the model.

    Returns (feasible, reason).  If not feasible, no amount of retries can fix
    the SlideIntentPlan — the Outline itself must be repaired.
    """
    # 1. Total allocated slides must equal requested count
    total = sum(s.allocated_slides for s in outline.sections)
    if total != request.requested_slide_count:
        return False, (
            f"Outline allocates {total} slides but request expects "
            f"{request.requested_slide_count}"
        )

    # 2. Each required asset must belong to exactly one section
    required_ids = {a.asset_id for a in request.assets if a.required}
    asset_owners: dict[str, str] = {}
    for section in outline.sections:
        for aid in section.candidate_asset_ids:
            if aid in required_ids:
                if aid in asset_owners:
                    return False, (
                        f"Required asset {aid} appears in multiple sections: "
                        f"{asset_owners[aid]} and {section.section_id}"
                    )
                asset_owners[aid] = section.section_id

    # 3. No required asset may be unowned
    unowned = sorted(required_ids - set(asset_owners))
    if unowned:
        return False, f"Required assets not in any section: {unowned}"

    # 4. Each section with required assets must have at least 1 slide
    for section in outline.sections:
        section_required = [aid for aid in section.candidate_asset_ids if aid in required_ids]
        if section_required and section.allocated_slides < 1:
            return False, (
                f"Section {section.section_id} has {len(section_required)} "
                f"required assets but 0 allocated slides"
            )

    # 5. Section asset capacity — each section must have enough slides to
    #    physically accommodate all its Required assets given the per-slide
    #    asset limit enforced by SlideIntent.asset_ids max_length.
    max_per_slide = _get_max_assets_per_slide()
    capacity_violations: list[str] = []
    for section in outline.sections:
        section_required = [
            aid for aid in section.candidate_asset_ids if aid in required_ids
        ]
        if not section_required:
            continue
        required_count = len(section_required)
        capacity = section.allocated_slides * max_per_slide
        if required_count > capacity:
            # ceil division: minimum slides needed
            min_needed = (required_count + max_per_slide - 1) // max_per_slide
            capacity_violations.append(
                f"{section.section_id} ({section.title}): "
                f"required={required_count}, allocated={section.allocated_slides} slides, "
                f"capacity={capacity} (max {max_per_slide}/slide), "
                f"minimum slides needed={min_needed}"
            )

    if capacity_violations:
        return False, (
            f"Section asset capacity infeasible — "
            f"each slide can carry at most {max_per_slide} assets. "
            f"Violations: {'; '.join(capacity_violations)}"
        )

    return True, "feasible"


# Keep the fail-fast validator for user-confirmation path (ConfirmPlan)
def _validate_slides_for_request(
    request: PlanningRequest,
    outline: OutlinePlan,
    slides: SlideIntentPlan,
) -> None:
    v = _collect_slide_violations(request, outline, slides)
    if v.is_valid:
        return
    raise ValueError(v.summary())


def _unified_slides_corrective_prompt(
    request: PlanningRequest,
    outline: OutlinePlan,
    design: DesignContract,
    violations: SlidePlanViolations,
) -> str:
    """One corrective prompt carrying the COMPLETE violation set.

    This is the single corrective prompt for ALL SlideIntent semantic errors.
    It includes the asset ownership table, section slide budgets, and every
    detected violation so the model can fix everything in one pass.
    """
    # ── Section slide budget ──
    budget_lines: list[str] = []
    for section in outline.sections:
        section_required = [
            a.asset_id for a in request.assets
            if a.required and a.asset_id in section.candidate_asset_ids
        ]
        budget_lines.append(
            f"  {section.section_id} ({section.title}): "
            f"{section.allocated_slides} slides, "
            f"allowed assets = {list(section.candidate_asset_ids)}"
            + (f", REQUIRED = {section_required}" if section_required else "")
        )
    budget_text = "\n".join(budget_lines)

    # ── Required Asset Ownership Table ──
    required_assets = [a for a in request.assets if a.required]
    ownership_lines: list[str] = []
    for asset in required_assets:
        owner = _find_owning_section(outline, asset.asset_id)
        ownership_lines.append(
            f"  {asset.asset_id}  →  section {owner}  ({asset.semantic_label})"
        )
    ownership_text = "\n".join(ownership_lines)
    slot_contract = _slide_slot_contract(outline)

    # ── Violation detail ──
    violation_parts: list[str] = []

    if violations.count_mismatch:
        actual, req = violations.count_mismatch
        violation_parts.append(
            f"A. EXACT SLIDE COUNT: You returned {actual} slides. "
            f"You MUST return EXACTLY {req} slides.  "
            f"Use the section budget below to allocate them correctly."
        )
    else:
        violation_parts.append(
            f"A. EXACT SLIDE COUNT: ✓ satisfied ({request.requested_slide_count} slides). "
            f"Do NOT change the count."
        )

    violation_parts.append(
        "B. SECTION SLIDE BUDGET (confirmed outline):\n" + budget_text
    )

    if violations.missing_required_assets:
        missing_detail: list[str] = []
        for aid in sorted(violations.missing_required_assets):
            owner = _find_owning_section(outline, aid)
            label = next((a.semantic_label for a in required_assets if a.asset_id == aid), "?")
            missing_detail.append(f"  - {aid}  ({label})  → belongs to section {owner}")
        violation_parts.append(
            "C. MISSING REQUIRED ASSETS — these MUST appear on at least one slide:\n"
            + "\n".join(missing_detail)
        )
    else:
        violation_parts.append("C. REQUIRED COVERAGE: ✓ all required assets covered.")

    if violations.cross_section_assets:
        cross_detail: list[str] = []
        for aid, owner in sorted(violations.cross_section_assets.items()):
            cross_detail.append(f"  - {aid}  → belongs to section {owner}")
        violation_parts.append(
            "D. CROSS-SECTION ASSET ERRORS — these assets appear on slides in "
            "the WRONG section:\n" + "\n".join(cross_detail)
            + "\n\nFix: Place each asset ONLY on slides whose section_id "
            "matches the owning section in the ownership table above."
        )
    else:
        violation_parts.append("D. SECTION SCOPING: ✓ no cross-section violations.")

    if violations.section_count_mismatches:
        sc_detail: list[str] = []
        for sid, (act, alloc) in sorted(violations.section_count_mismatches.items()):
            sc_detail.append(f"  - {sid}: got {act} slides, allocated {alloc}")
        violation_parts.append(
            "E. SECTION COUNT MISMATCHES:\n" + "\n".join(sc_detail)
        )

    if violations.duplicate_assets:
        violation_parts.append(
            f"F. DUPLICATE ASSETS (appear on multiple slides): "
            f"{sorted(violations.duplicate_assets)}"
        )

    if violations.unknown_sections:
        violation_parts.append(
            f"G. UNKNOWN SECTIONS: {sorted(violations.unknown_sections)}"
        )
    if violations.unknown_assets:
        violation_parts.append(
            f"H. UNKNOWN ASSETS: {sorted(violations.unknown_assets)}"
        )

    violation_body = "\n\n".join(violation_parts)

    return (
        "YOUR PREVIOUS SLIDE-INTENT PLAN WAS INVALID.  "
        "You MUST fix ALL violations listed below in ONE new plan.\n\n"
        "HARD CONTRACT — ALL of the following must be true SIMULTANEOUSLY:\n\n"
        f"{violation_body}\n\n"
        "IMMUTABLE SLIDE SLOT CONTRACT (one slide per line, exact order):\n"
        f"{slot_contract}\n\n"
        "REQUIRED ASSET OWNERSHIP TABLE (from confirmed Outline — do NOT guess):\n"
        f"{ownership_text}\n\n"
        "REGENERATE the COMPLETE SlideIntentPlan as raw JSON.  "
        "Verify EVERY constraint above before responding.  "
        "Do NOT modify the confirmed Outline or Design.\n\n"
        "REQUEST:\n"
        + _slides_request_summary(request)
        + "\nCONFIRMED_OUTLINE:\n"
        + outline.model_dump_json(exclude_none=True)
        + "\nCONFIRMED_DESIGN:\n"
        + design.model_dump_json(exclude_none=True)
    )


def _outline_prompt(request: PlanningRequest) -> str:
    required_assets = [a for a in request.assets if a.required]
    required_checklist = "\n".join(
        f"- {a.asset_id}  ({a.semantic_label})"
        for a in required_assets
    )
    max_per_slide = _get_max_assets_per_slide()
    max_candidates = _get_max_candidates_per_section()
    return (
        "Create the structured outline for this Host planning request.\n\n"
        "CRITICAL: The following REQUIRED assets MUST each appear in exactly "
        f"one section's candidate_asset_ids ({len(required_assets)} total):\n"
        f"{required_checklist}\n\n"
        "CRITICAL — SECTION CANDIDATE LIMIT: Each section may contain at MOST "
        f"{max_candidates} candidate_asset_ids.  If a semantic topic has more "
        f"than {max_candidates} assets, split it into multiple coherent "
        "sections.  The total allocated_slides across all sections MUST still "
        f"equal {request.requested_slide_count}.\n\n"
        "CRITICAL — SECTION SLIDE CAPACITY: Each slide can carry at MOST "
        f"{max_per_slide} assets.  For every section, verify:\n"
        f"  allocated_slides >= ceil(required_assets_in_section / {max_per_slide})\n\n"
        f"For example, a section with 6 Required assets needs at least "
        f"ceil(6/{max_per_slide}) = {(6 + max_per_slide - 1) // max_per_slide} slides.\n\n"
        "PLANNING REQUEST:\n"
        + request.model_dump_json(exclude_none=True)
    )


def _outline_capacity_corrective_prompt(
    request: PlanningRequest,
    capacity_error: str,
) -> str:
    """Build a corrective prompt when section asset capacity is infeasible.

    The capacity error string contains per-section details:
    required count, allocated slides, capacity, and minimum slides needed.
    This prompt tells the model to redistribute page allocations while
    keeping the total equal to requested_slide_count.
    """
    max_per_slide = _get_max_assets_per_slide()
    max_candidates = _get_max_candidates_per_section()

    # Build a section-by-section capacity table
    required_ids = {a.asset_id for a in request.assets if a.required}
    section_table_lines: list[str] = []
    for a in request.assets:
        if a.required:
            section_table_lines.append(
                f"  {a.asset_id}  ({a.semantic_label})"
            )
    asset_checklist = "\n".join(section_table_lines)

    return (
        "YOUR PREVIOUS OUTLINE HAD SECTION CAPACITY VIOLATIONS. "
        "You MUST correct this.\n\n"
        f"THE CONSTRAINTS (BOTH must be satisfied):\n"
        f"1. Each section may contain at MOST {max_candidates} "
        "candidate_asset_ids (enforced by OutlineSection schema).\n"
        f"2. Each slide can carry at MOST {max_per_slide} "
        "assets (enforced by SlideIntent schema).\n\n"
        f"THE RULE: For every section:\n"
        f"  len(candidate_asset_ids) <= {max_candidates}\n"
        f"  allocated_slides >= ceil(required_asset_count / {max_per_slide})\n\n"
        f"THE FIX: Redistribute your {request.requested_slide_count}-slide "
        "budget across sections so that EVERY section satisfies BOTH "
        "constraints.  If a section has too many candidates, split it into "
        "multiple sections.\n\n"
        "CRITICAL — DO NOT CHANGE:\n"
        f"- Total slides MUST still equal {request.requested_slide_count}\n"
        "- Required asset identity — every required asset is still required\n"
        "- Section content purpose — only adjust page counts or split sections\n\n"
        "APPROACH:\n"
        "1. Identify sections with insufficient capacity or too many candidates\n"
        "2. Increase allocated_slides or split sections as needed\n"
        "3. Reduce allocated_slides from sections with excess capacity\n"
        f"4. Verify sum(allocation) == {request.requested_slide_count}\n"
        f"5. Verify every section has <= {max_candidates} candidates\n\n"
        "PREVIOUS CAPACITY ERRORS:\n"
        f"{capacity_error}\n\n"
        f"ALL {len(required_ids)} REQUIRED ASSETS (must all be covered):\n"
        f"{asset_checklist}\n\n"
        "REGENERATE the COMPLETE outline. "
        "Verify all constraints before responding.\n\n"
        "PLANNING REQUEST:\n"
        + request.model_dump_json(exclude_none=True)
    )


def _outline_schema_corrective_prompt(
    request: PlanningRequest,
    schema_error_detail: str,
) -> str:
    """Build a corrective prompt when Pydantic schema validation rejects the Outline.

    This handles cases where the OutlinePlan cannot even be constructed — e.g.,
    candidate_asset_ids max_length exceeded (12 > 8).  Unlike capacity violations
    (which are detected after successful OutlinePlan construction), schema
    failures prevent the OutlinePlan from existing at all.

    Args:
        request: The planning request.
        schema_error_detail: Sanitized error detail from Pydantic/ValidationError,
            e.g. "sections.2.candidate_asset_ids: max 8 items, got 12".
    """
    max_candidates = _get_max_candidates_per_section()
    max_per_slide = _get_max_assets_per_slide()
    required_assets = [a for a in request.assets if a.required]
    required_checklist = "\n".join(
        f"- {a.asset_id}  ({a.semantic_label})"
        for a in required_assets
    )

    return (
        "YOUR PREVIOUS OUTLINE WAS REJECTED BY SCHEMA VALIDATION. "
        "You MUST correct the structural error and regenerate.\n\n"
        f"SCHEMA ERROR:\n"
        f"  {schema_error_detail}\n\n"
        f"HARD CONSTRAINTS — ALL must be satisfied simultaneously:\n\n"
        f"1. SECTION CANDIDATE LIMIT: Each section may contain at MOST "
        f"{max_candidates} candidate_asset_ids.  If you have more than "
        f"{max_candidates} assets in one semantic topic, you MUST split it "
        "into multiple semantically coherent sections.  Do NOT drop assets — "
        "just distribute them across more sections.\n\n"
        f"2. SECTION SLIDE CAPACITY: Each slide can carry at MOST "
        f"{max_per_slide} assets.  For every section:\n"
        f"   allocated_slides >= ceil(required_assets_in_section / {max_per_slide})\n\n"
        f"3. TOTAL SLIDES: sum(allocated_slides) MUST equal "
        f"{request.requested_slide_count}.  Do NOT change the total.\n\n"
        f"4. REQUIRED COVERAGE: All {len(required_assets)} required assets "
        "MUST appear in exactly one section's candidate_asset_ids:\n"
        f"{required_checklist}\n\n"
        "THE FIX:\n"
        "- If any section has more than 8 candidate_asset_ids, split it into "
        "multiple sections (e.g. temperature_part_1, temperature_part_2).\n"
        "- Verify every section satisfies both the candidate limit AND the "
        "slide capacity constraint.\n"
        "- Keep total allocated_slides == "
        f"{request.requested_slide_count}.\n"
        "- Do NOT drop required assets.\n\n"
        "REGENERATE the COMPLETE outline as valid JSON. "
        "Verify ALL constraints before responding.\n\n"
        "PLANNING REQUEST:\n"
        + request.model_dump_json(exclude_none=True)
    )


def _parse_missing_asset_ids(error_message: str) -> list[str]:
    """从 'outline omits required assets: [...]' 错误消息中提取缺失 ID 列表."""
    import re

    match = re.search(r"omits required assets:\s*\[([^\]]+)\]", error_message)
    if not match:
        return []
    # 提取引号内的字符串: 'id1', 'id2' → [id1, id2]
    ids: list[str] = []
    for raw in re.findall(r"'([^']+)'", match.group(1)):
        ids.append(raw)
    return ids


def _extract_schema_error_detail_from_exc(error: HostPlanningModelError) -> str:
    """Walk the FULL exception chain to extract structured schema error details.

    Priority:
    1. ReportSchemaError.validation_errors (new structured field)
    2. Any node with errors() (Pydantic ValidationError) — walked recursively
    3. ReportSchemaError missing_fields / type_errors (legacy)
    4. Fallback to generic message

    The error chain can be deeply nested:
      HostPlanningModelError (outer, from _call_model phase mismatch)
        → HostPlanningModelError (inner, from adapter)
          → ReportSchemaError
            → ValidationError
    """
    parts: list[str] = []

    # ── Priority 1+2: Walk the FULL __cause__ chain for structured details ──
    node: BaseException | None = error
    while node is not None:
        # 1a) ReportSchemaError.validation_errors (new structured field — highest priority)
        if hasattr(node, 'validation_errors'):
            ve_list: list[dict[str, object]] = list(getattr(node, 'validation_errors', []) or [])
            if ve_list:
                # Use validation_errors exclusively if available — it is the richest source
                for ve in ve_list[:8]:
                    loc = str(ve.get('loc', '?'))
                    vtype = str(ve.get('type', '?'))
                    vmsg = str(ve.get('msg', ''))[:120]
                    ctx = ve.get('ctx')
                    ctx_suffix = ""
                    if isinstance(ctx, dict):
                        max_len = ctx.get('max_length')
                        actual = ctx.get('actual_length')
                        if max_len is not None and actual is not None:
                            ctx_suffix = f" [max={max_len}, got={actual}]"
                        elif max_len is not None:
                            ctx_suffix = f" [max={max_len}]"
                    parts.append(f"{loc}: {vtype} — {vmsg}{ctx_suffix}")
                break  # validation_errors is the richest source; stop walking

        # 1b) Legacy ReportSchemaError fields (used only if no validation_errors found)
        if hasattr(node, 'missing_fields'):
            mf = list(getattr(node, 'missing_fields', []) or [])
            if mf:
                parts.append(f"missing_fields: {mf}")
        if hasattr(node, 'type_errors'):
            te = list(getattr(node, 'type_errors', []) or [])
            if te:
                parts.append(f"type_errors: {te[:5]}")

        # 2) Pydantic ValidationError with errors() — walkable node
        if hasattr(node, 'errors') and callable(getattr(node, 'errors', None)):
            try:
                errs = node.errors()  # type: ignore[union-attr]
                for err in errs[:8]:
                    loc = '.'.join(str(x) for x in err.get('loc', ()))
                    etype = err.get('type', '?')
                    emsg = str(err.get('msg', ''))[:120]
                    ctx = err.get('ctx')
                    ctx_suffix = ""
                    if isinstance(ctx, dict):
                        max_len = ctx.get('max_length')
                        actual = ctx.get('actual_length')
                        if max_len is not None and actual is not None:
                            ctx_suffix = f" [max={max_len}, got={actual}]"
                        elif max_len is not None:
                            ctx_suffix = f" [max={max_len}]"
                    parts.append(f"{loc}: {etype} — {emsg}{ctx_suffix}")
            except Exception:
                pass

        node = getattr(node, '__cause__', None)

    # ── Priority 3: Fallback to generic message ──
    if not parts:
        return str(error)

    return ' | '.join(parts)


def _slides_request_summary(request: PlanningRequest) -> str:
    """Return request JSON suitable for SlideIntent prompts — WITHOUT source_context.

    The source_context can be up to 60 000 chars and is only needed for Outline
    generation.  By the SlideIntent phase the confirmed Outline already encodes
    all structural knowledge.  Including the full context in every prompt
    (including corrective retries) bloats the prompt and degrades JSON quality.
    """
    return request.model_dump_json(
        exclude_none=True,
        exclude={'source_context'},
    )


def _outline_corrective_prompt(
    request: PlanningRequest,
    missing_ids: list[str],
) -> str:
    """构建纠正提示 — 明确告诉模型哪些 required asset 在上一次响应中被遗漏."""
    missing_assets = [a for a in request.assets if a.asset_id in missing_ids]
    missing_checklist = "\n".join(
        f"- {a.asset_id}  ({a.semantic_label})"
        for a in missing_assets
    )
    max_per_slide = _get_max_assets_per_slide()
    max_candidates = _get_max_candidates_per_section()
    return (
        "YOUR PREVIOUS OUTLINE OMITTED REQUIRED ASSETS. "
        "You MUST correct this.\n\n"
        f"The following {len(missing_ids)} required asset(s) were NOT included "
        "in any section's candidate_asset_ids in your previous response:\n"
        f"{missing_checklist}\n\n"
        "REQUIRED FIX: Generate a new, complete outline where EVERY required "
        "asset (including those listed above) appears in exactly one section's "
        "candidate_asset_ids.  Do NOT omit any required asset.\n\n"
        f"REMINDER — BOTH CONSTRAINTS:\n"
        f"1. Each section may contain at MOST {max_candidates} "
        "candidate_asset_ids.\n"
        f"2. Each slide can carry at MOST {max_per_slide} assets.  "
        "When adding missing assets to a section, verify that section has "
        "enough allocated_slides.  If not, redistribute the page budget "
        f"while keeping total slides = {request.requested_slide_count}.\n"
        "If adding assets would push a section over the candidate limit, "
        "split it into multiple sections.\n\n"
        "PLANNING REQUEST:\n"
        + request.model_dump_json(exclude_none=True)
    )


def _design_prompt(request: PlanningRequest, outline: OutlinePlan) -> str:
    return (
        "Create the design contract for the confirmed outline.\nREQUEST:\n"
        + request.model_dump_json(exclude_none=True)
        + "\nCONFIRMED_OUTLINE:\n"
        + outline.model_dump_json(exclude_none=True)
    )


def _slides_prompt(
    request: PlanningRequest,
    outline: OutlinePlan,
    design: DesignContract,
) -> str:
    section_allowlist_parts: list[str] = []
    for section in outline.sections:
        allowed = list(section.candidate_asset_ids)
        section_allowlist_parts.append(
            f"  {section.section_id} ({section.title}): "
            f"ALLOWED = {allowed if allowed else '[none]'}"
        )
    allowlist_text = "\n".join(section_allowlist_parts)

    # ── Required asset checklist ──
    required_assets = [a for a in request.assets if a.required]
    required_by_section: dict[str, list[str]] = {}
    for section in outline.sections:
        for asset_id in section.candidate_asset_ids:
            if asset_id in {a.asset_id for a in required_assets}:
                required_by_section.setdefault(section.section_id, []).append(asset_id)

    required_checklist_parts: list[str] = []
    for section in outline.sections:
        ids = required_by_section.get(section.section_id, [])
        label = f"  {section.section_id} ({section.title}):"
        if ids:
            required_checklist_parts.append(f"{label} REQUIRED = {ids}")
        else:
            required_checklist_parts.append(f"{label} (no required assets)")
    required_checklist = "\n".join(required_checklist_parts)

    # ── Section capacity table ──
    max_per_slide = _get_max_assets_per_slide()
    capacity_lines: list[str] = []
    for section in outline.sections:
        all_assets = list(section.candidate_asset_ids)
        req_in_section = required_by_section.get(section.section_id, [])
        capacity_lines.append(
            f"  {section.section_id} ({section.title}): "
            f"{section.allocated_slides} slides, "
            f"capacity = {section.allocated_slides * max_per_slide} assets max, "
            f"assets to cover = {all_assets}"
            + (f", REQUIRED = {req_in_section}" if req_in_section else "")
        )
    capacity_table = "\n".join(capacity_lines)
    slot_contract = _slide_slot_contract(outline)

    return (
        "Create the final slide-intent plan.\n\n"
        f"CRITICAL — EXACT SLIDE COUNT:\n"
        f"You MUST return EXACTLY {request.requested_slide_count} slides "
        f"(len(slides) == {request.requested_slide_count}).  "
        f"Do NOT add or remove slides — {request.requested_slide_count} is a "
        f"hard contract that the host validator enforces.\n\n"
        "CRITICAL - IMMUTABLE SLIDE SLOT CONTRACT:\n"
        "Return one slide object per slot below in this exact order. Do not add, "
        "remove, reorder, or move a slot to another section.\n"
        f"{slot_contract}\n\n"
        f"CRITICAL — PER-SLIDE ASSET CAPACITY (HARD SCHEMA LIMIT):\n"
        f"Every slide: len(asset_ids) <= {max_per_slide}.  "
        f"No slide may contain more than {max_per_slide} asset IDs.  "
        f"The Pydantic schema enforces max_length={max_per_slide} on every "
        f"slide's asset_ids field.  If you place {max_per_slide + 1} or more "
        f"assets on a single slide, the ENTIRE SlideIntentPlan will be rejected.\n\n"
        "CRITICAL — REQUIRED ASSET COVERAGE:\n"
        f"The following {len(required_assets)} REQUIRED assets MUST each appear "
        "on at least one slide in the correct section.  Do NOT skip or omit "
        "any required asset — even if you think it is visually similar to "
        "another chart.  Each required asset belongs to exactly one section "
        "per the confirmed outline.\n\n"
        "REQUIRED ASSETS BY SECTION:\n"
        f"{required_checklist}\n\n"
        "SECTION CAPACITY TABLE (from confirmed outline):\n"
        f"{capacity_table}\n\n"
        "CRITICAL — SECTION ASSET SCOPING:\n"
        "Each slide's asset_ids MUST be a subset of its own section's allowed "
        "assets.  Cross-section asset usage is FORBIDDEN.\n\n"
        "CONSTRAINT PRIORITY: If you cannot satisfy all constraints, follow "
        "this order:\n"
        "1. EXACT SLIDE COUNT — never change the number of slides.\n"
        "2. PER-SLIDE CAPACITY — at most 1 or 2 assets per slide based on type.\n"
        "3. REQUIRED COVERAGE — do not skip required assets.\n"
        "4. SECTION SCOPING — place each asset in its correct section.\n\n"
        "STRATEGY FOR SECTIONS WITH MORE ASSETS THAN SLIDES:\n"
        "If a section has more assets than (allocated_slides × {max_per_slide}), "
        "you MUST split assets across slides within the section.  "
        "For example, with 2 slides and 4 assets in a section, place 2 assets "
        "on each slide.  Never put more than {max_per_slide} assets on any slide.\n\n"
        "SECTION ALLOWED ASSETS:\n"
        f"{allowlist_text}\n\n"
        "REQUEST:\n"
        + _slides_request_summary(request)
        + "\nCONFIRMED_OUTLINE:\n"
        + outline.model_dump_json(exclude_none=True)
        + "\nCONFIRMED_DESIGN:\n"
        + design.model_dump_json(exclude_none=True)
    )


def _parse_cross_section_assets(error_message: str) -> list[str]:
    """Extract cross-section asset IDs from validation error messages."""
    import re

    match = re.search(
        r"outside its outline section:\s*\[([^\]]+)\]",
        error_message,
    )
    if not match:
        return []
    ids: list[str] = []
    for raw in re.findall(r"'([^']+)'", match.group(1)):
        ids.append(raw)
    return ids


def _slides_corrective_prompt(
    request: PlanningRequest,
    outline: OutlinePlan,
    design: DesignContract,
    cross_section_ids: list[str],
) -> str:
    """Build a corrective prompt that maps each illegal asset to its owning section."""
    # Build asset → owning section mapping
    asset_section_map: dict[str, str] = {}
    for section in outline.sections:
        for asset_id in section.candidate_asset_ids:
            asset_section_map[asset_id] = section.section_id

    illegal_list: list[str] = []
    for asset_id in cross_section_ids:
        owner = asset_section_map.get(asset_id, "unknown")
        illegal_list.append(
            f"  - {asset_id}  → belongs to section {owner}"
        )
    illegal_text = "\n".join(illegal_list)

    # Rebuild section allowlist
    section_allowlist_parts: list[str] = []
    for section in outline.sections:
        allowed = list(section.candidate_asset_ids)
        section_allowlist_parts.append(
            f"  {section.section_id} ({section.title}): "
            f"ALLOWED = {allowed if allowed else '[none]'}"
        )
    allowlist_text = "\n".join(section_allowlist_parts)

    return (
        "YOUR PREVIOUS SLIDE-INTENT PLAN WAS INVALID.  You MUST correct this.\n\n"
        f"The following asset(s) were placed on slides in the WRONG section:\n"
        f"{illegal_text}\n\n"
        "Each asset MUST only appear on a slide whose section_id matches the "
        "section that owns that asset.  Cross-section placement is FORBIDDEN.\n\n"
        "SECTION ALLOWED ASSETS (repeated for clarity):\n"
        f"{allowlist_text}\n\n"
        "REQUIRED FIX: Generate a new SlideIntentPlan where every slide's "
        "asset_ids are a subset of that slide's section's allowed assets.\n\n"
        "REQUEST:\n"
        + request.model_dump_json(exclude_none=True)
        + "\nCONFIRMED_OUTLINE:\n"
        + outline.model_dump_json(exclude_none=True)
        + "\nCONFIRMED_DESIGN:\n"
        + design.model_dump_json(exclude_none=True)
    )


def _slides_schema_corrective_prompt(
    request: PlanningRequest,
    outline: OutlinePlan,
    design: DesignContract,
    *,
    error_detail: str = "",
) -> str:
    """Build a corrective prompt for raw JSON syntax / schema conformance failures.

    Unlike _slides_corrective_prompt (cross-section assets), this targets
    structured-output failures: malformed JSON, missing fields, wrong types,
    markdown fences, or extraneous text.

    Args:
        error_detail: Specific error information from the previous attempt
                      (e.g. missing fields, type errors, JSON parse error).
    """
    section_allowlist_parts: list[str] = []
    for section in outline.sections:
        allowed = list(section.candidate_asset_ids)
        section_allowlist_parts.append(
            f"  {section.section_id} ({section.title}): "
            f"ALLOWED = {allowed if allowed else '[none]'}"
        )
    allowlist_text = "\n".join(section_allowlist_parts)

    _error_block = ""
    if error_detail:
        _error_block = (
            f"\nSPECIFIC SCHEMA VIOLATION FROM YOUR PREVIOUS ATTEMPT:\n"
            f"  {error_detail}\n"
            f"\nThis means your response was REJECTED by the Pydantic schema "
            f"validator.  The error above tells you EXACTLY which field failed "
            f"and why.  Fix THAT SPECIFIC FIELD before regenerating.\n"
        )
    else:
        _error_block = (
            "\nYour previous response was REJECTED by the schema validator.  "
            "Check that every field conforms to the expected types and limits.\n"
        )

    # ── Section capacity table ──
    max_per_slide = _get_max_assets_per_slide()
    capacity_lines: list[str] = []
    for section in outline.sections:
        capacity_lines.append(
            f"  {section.section_id} ({section.title}): "
            f"{section.allocated_slides} slides, "
            f"capacity = {section.allocated_slides * max_per_slide} assets max, "
            f"assets = {list(section.candidate_asset_ids)}"
        )
    capacity_table = "\n".join(capacity_lines)
    slot_contract = _slide_slot_contract(outline)

    return (
        "YOUR PREVIOUS RESPONSE WAS NOT VALID JSON OR DID NOT MATCH "
        "THE REQUIRED SCHEMA.  You MUST correct this.\n\n"
        f"{_error_block}\n"
        f"HARD PER-SLIDE ASSET LIMIT:\n"
        f"Every slide: len(asset_ids) <= {max_per_slide}.  "
        f"The schema enforces max_length={max_per_slide} on asset_ids.  "
        f"If you placed more than {max_per_slide} assets on a single slide, "
        f"that is why validation failed.  Split them across multiple slides "
        f"within the same section.\n\n"
        "CRITICAL — OUTPUT FORMAT REQUIREMENTS:\n"
        "1. Output RAW JSON ONLY.  Do NOT wrap in ``` fences.\n"
        "2. No markdown, no prose, no shell commands, no code snippets.\n"
        "3. Your ENTIRE response must be a single JSON object starting with "
        "{{ and ending with }}.\n"
        "4. Every field in the SlideIntentPlan schema MUST be present.\n"
        "5. slide_id and section_id must be alphanumeric identifiers "
        "(no spaces, no shell commands, no file paths).\n"
        f"6. You MUST return EXACTLY {request.requested_slide_count} slides "
        f"(len(slides) == {request.requested_slide_count}).\n\n"
        "IMMUTABLE SLIDE SLOT CONTRACT (one slide per line, exact order):\n"
        f"{slot_contract}\n\n"
        "SECTION CAPACITY TABLE (from confirmed outline):\n"
        f"{capacity_table}\n\n"
        "REQUIRED FIX: Generate a NEW, COMPLETE SlideIntentPlan as raw JSON. "
        "Use only the allowed assets per section listed below.\n\n"
        "SECTION ALLOWED ASSETS:\n"
        f"{allowlist_text}\n\n"
        "REQUEST:\n"
        + _slides_request_summary(request)
        + "\nCONFIRMED_OUTLINE:\n"
        + outline.model_dump_json(exclude_none=True)
        + "\nCONFIRMED_DESIGN:\n"
        + design.model_dump_json(exclude_none=True)
    )


def _slides_semantic_corrective_prompt(
    request: PlanningRequest,
    outline: OutlinePlan,
    design: DesignContract,
    *,
    cross_section_ids: list[str] | None = None,
    missing_required_ids: list[str] | None = None,
) -> str:
    """Build a unified corrective prompt that addresses ALL semantic errors.

    Covers both cross-section asset placement and missing required assets
    in a single prompt so the model sees the full constraint set.
    """
    cross_ids = cross_section_ids or []
    missing_ids = missing_required_ids or []

    # ── Section allowlist ──
    section_allowlist_parts: list[str] = []
    for section in outline.sections:
        allowed = list(section.candidate_asset_ids)
        section_allowlist_parts.append(
            f"  {section.section_id} ({section.title}): "
            f"ALLOWED = {allowed if allowed else '[none]'}"
        )
    allowlist_text = "\n".join(section_allowlist_parts)

    # ── Required asset checklist by section ──
    required_assets = [a for a in request.assets if a.required]
    required_ids_set = {a.asset_id for a in required_assets}
    required_by_section: dict[str, list[str]] = {}
    for section in outline.sections:
        for asset_id in section.candidate_asset_ids:
            if asset_id in required_ids_set:
                required_by_section.setdefault(section.section_id, []).append(asset_id)
    required_checklist_parts: list[str] = []
    for section in outline.sections:
        ids = required_by_section.get(section.section_id, [])
        label = f"  {section.section_id} ({section.title}):"
        if ids:
            required_checklist_parts.append(f"{label} REQUIRED = {ids}")
        else:
            required_checklist_parts.append(f"{label} (no required assets)")
    required_checklist = "\n".join(required_checklist_parts)

    # ── Cross-section error detail ──
    cross_section_text = ""
    if cross_ids:
        asset_section_map: dict[str, str] = {}
        for section in outline.sections:
            for asset_id in section.candidate_asset_ids:
                asset_section_map[asset_id] = section.section_id
        cross_parts: list[str] = []
        for asset_id in cross_ids:
            owner = asset_section_map.get(asset_id, "unknown")
            cross_parts.append(f"  - {asset_id}  → belongs to section {owner}")
        cross_section_text = (
            "CROSS-SECTION ASSET ERRORS:\n"
            "The following assets were placed on slides in the WRONG section:\n"
            + "\n".join(cross_parts)
            + "\n\nFix: Place each asset ONLY on slides whose section_id "
            "matches the owning section listed above."
        )

    # ── Missing required error detail ──
    missing_text = ""
    if missing_ids:
        missing_parts: list[str] = []
        for asset_id in missing_ids:
            matching = [a for a in required_assets if a.asset_id == asset_id]
            label = matching[0].semantic_label if matching else "?"
            owner = _find_owning_section(outline, asset_id)
            missing_parts.append(
                f"  - {asset_id}  ({label})  → belongs to section {owner}"
            )
        missing_text = (
            "MISSING REQUIRED ASSETS:\n"
            "The following REQUIRED assets were NOT placed on any slide. "
            "You MUST add them:\n"
            + "\n".join(missing_parts)
            + "\n\nFix: Add each missing REQUIRED asset to at least one slide "
            "in its owning section.  Do NOT delete other required assets."
        )

    error_sections: list[str] = []
    if missing_text:
        error_sections.append(missing_text)
    if cross_section_text:
        error_sections.append(cross_section_text)
    error_body = "\n\n".join(error_sections)

    return (
        "YOUR PREVIOUS SLIDE-INTENT PLAN WAS INVALID.  You MUST correct this.\n\n"
        "HARD CONSTRAINTS (ALL must be satisfied):\n"
        f"0. EXACT SLIDE COUNT: You MUST return EXACTLY "
        f"{request.requested_slide_count} slides.  "
        f"Do NOT add or remove slides while fixing other errors.  "
        f"The host validator enforces len(slides) == "
        f"{request.requested_slide_count}.\n"
        "1. REQUIRED COVERAGE: Every required asset MUST appear on at least "
        "one slide.  Do NOT skip or omit any required asset.\n"
        "2. SECTION SCOPING: Each slide's asset_ids MUST be a subset of its "
        "section's allowed assets.  Cross-section placement is FORBIDDEN.\n"
        "3. CONSTRAINT PRIORITY: If you cannot satisfy all constraints, follow "
        "this order — (0) exact count, (1) required coverage, "
        "(2) section scoping.\n\n"
        f"REQUIRED ASSETS BY SECTION:\n"
        f"{required_checklist}\n\n"
        f"{error_body}\n\n"
        "SECTION ALLOWED ASSETS (reference):\n"
        f"{allowlist_text}\n\n"
        "REQUIRED FIX: Generate a COMPLETE new SlideIntentPlan that satisfies "
        "ALL hard constraints above.  Verify before responding:\n"
        "- Every required asset appears at least once → ✅\n"
        "- No cross-section asset usage → ✅\n\n"
        "REQUEST:\n"
        + _slides_request_summary(request)
        + "\nCONFIRMED_OUTLINE:\n"
        + outline.model_dump_json(exclude_none=True)
        + "\nCONFIRMED_DESIGN:\n"
        + design.model_dump_json(exclude_none=True)
    )


def _find_owning_section(outline: OutlinePlan, asset_id: str) -> str:
    """Return the section_id that owns a given asset, or 'unknown'."""
    for section in outline.sections:
        if asset_id in section.candidate_asset_ids:
            return section.section_id
    return "unknown"


# ═══════════════════════════════════════════════════════════════════════════════
# Deterministic fingerprint utilities (retained for test parity)
# ═══════════════════════════════════════════════════════════════════════════════

def _canonical_fingerprint(obj: BaseModel, *, label: str = "") -> str:
    """Deterministic SHA-256 of a Pydantic model's sanitized JSON dump.

    - canonical sort_keys=True
    - source_context body replaced with SHA+length
    - no secrets in output
    """
    data = obj.model_dump(mode="json", exclude_none=True)
    _sanitize_source_context(data)
    canonical = json.dumps(data, sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _sanitize_source_context(data: dict) -> None:
    """Replace source_context body with SHA-256 + length (recursive)."""
    if not isinstance(data, dict):
        return
    if "source_context" in data and isinstance(data["source_context"], str):
        raw = data["source_context"]
        data["source_context"] = (
            f"[REDACTED:sha256={hashlib.sha256(raw.encode()).hexdigest()[:16]}"
            f" len={len(raw)}]"
        )
    for _v in data.values():
        if isinstance(_v, dict):
            _sanitize_source_context(_v)
        elif isinstance(_v, list):
            for _item in _v:
                if isinstance(_item, dict):
                    _sanitize_source_context(_item)


def _forensic_file_sha_prefix(path: str | Path) -> str:
    """Return a short source fingerprint without exposing the source path."""
    candidate = Path(path)
    try:
        return hashlib.sha256(candidate.read_bytes()).hexdigest()[:12]
    except OSError:
        return "unavailable"


def _forensic_slide_signature(
    snapshot: PlanningSnapshot,
    *,
    system_prompt: str,
    prompt: str,
    schema: type[BaseModel],
) -> str:
    """Build the deterministic, secret-free SlideIntent parity receipt.

    The receipt intentionally contains only hashes, lengths, basenames and
    planning metadata.  It is safe to print during GUI/non-GUI parity audits
    because neither prompt bodies nor absolute paths are included.
    """
    project_root = Path(__file__).resolve().parents[2]
    schema_text = json.dumps(
        schema.model_json_schema(),
        sort_keys=True,
        ensure_ascii=False,
        separators=(",", ":"),
    )
    identity = str(snapshot.model_identity or "unknown")
    backend, separator, model_id = identity.partition(":")
    if not separator:
        model_id = identity

    source_context = snapshot.request.source_context
    source_context_in_prompt = bool(
        source_context and source_context in prompt
    )
    section_capacity_table_present = (
        "SECTION SLIDE BUDGET" in prompt
        or "REQUIRED ASSETS BY SECTION" in prompt
    )
    count_contract_present = (
        "EXACT SLIDE COUNT" in prompt
        and str(snapshot.request.requested_slide_count) in prompt
    )

    values = (
        ("pid", str(os.getpid())),
        ("python", Path(sys.executable).name),
        ("planning_py_sha12", _forensic_file_sha_prefix(__file__)),
        (
            "ai_client_py_sha12",
            _forensic_file_sha_prefix(project_root / "core" / "ai_client.py"),
        ),
        (
            "ai_errors_py_sha12",
            _forensic_file_sha_prefix(project_root / "core" / "ai_errors.py"),
        ),
        ("backend", backend or "unknown"),
        ("model_id", model_id or "unknown"),
        ("retry_owner", "Host"),
        ("max_physical_calls", "3"),
        ("max_schema_retries", "0"),
        ("max_assets_per_slide", str(_get_max_assets_per_slide())),
        (
            "requested_slide_count",
            str(snapshot.request.requested_slide_count),
        ),
        ("system_prompt_len", str(len(system_prompt))),
        (
            "system_prompt_sha12",
            hashlib.sha256(system_prompt.encode("utf-8")).hexdigest()[:12],
        ),
        ("user_prompt_len", str(len(prompt))),
        (
            "user_prompt_sha12",
            hashlib.sha256(prompt.encode("utf-8")).hexdigest()[:12],
        ),
        ("json_schema_len", str(len(schema_text))),
        (
            "json_schema_sha12",
            hashlib.sha256(schema_text.encode("utf-8")).hexdigest()[:12],
        ),
        (
            "capacity_appendix_present",
            str(_SLIDES_CAPACITY_APPENDIX.strip() in system_prompt),
        ),
        ("source_context_in_prompt", str(source_context_in_prompt)),
        (
            "section_capacity_table_present",
            str(section_capacity_table_present),
        ),
        ("count_contract_present", str(count_contract_present)),
        ("planning_request_sha12", _canonical_fingerprint(snapshot.request)[:12]),
        (
            "outline_sha12",
            _canonical_fingerprint(snapshot.outline)[:12]
            if snapshot.outline is not None else "none",
        ),
        (
            "design_sha12",
            _canonical_fingerprint(snapshot.design)[:12]
            if snapshot.design is not None else "none",
        ),
    )
    return "\n".join(
        ["[FORENSIC][GUI_SLIDES_SIGNATURE]"]
        + [f"{key} = {value}" for key, value in values]
    )


# ═══════════════════════════════════════════════════════════════════════════════
# END OF FILE
# ═══════════════════════════════════════════════════════════════════════════════
