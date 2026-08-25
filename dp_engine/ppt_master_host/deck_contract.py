"""Compile one confirmed Planning Snapshot into every PPT Master deck contract.

The Module is the single source of truth for project Markdown, model-facing
authoring constraints, and deterministic Host preflight rules.  Keeping those
outputs behind one Interface prevents design/spec/prompt drift.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
from collections import Counter
from typing import Any, Literal
from xml.etree import ElementTree

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .planning import PlanningPhase, TemplateMode


_FONT_SIZE_TOLERANCE_PX = 2.0


class _StrictContractModel(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        str_strip_whitespace=True,
    )


class CompiledTypographyRoles(_StrictContractModel):
    """Named recurring typography roles shared by every contract output."""

    title: int = Field(ge=1, le=128)
    key_metric: int = Field(ge=1, le=128)
    subtitle: int = Field(ge=1, le=128)
    body: int = Field(ge=1, le=128)
    caption: int = Field(ge=1, le=128)

    @model_validator(mode="after")
    def _validate_hierarchy(self) -> CompiledTypographyRoles:
        if not (
            self.title >= self.key_metric >= self.subtitle >= self.body
            > self.caption
        ):
            raise ValueError("compiled typography roles must preserve hierarchy")
        return self

    @property
    def allowed_sizes(self) -> tuple[int, ...]:
        return tuple(sorted(set(self.model_dump(mode="python").values())))


class TypographyPreflightIssue(_StrictContractModel):
    """One undeclared recurring font-size observed by Host preflight."""

    font_size_px: float = Field(gt=0, le=256)
    occurrence_count: int = Field(ge=1)


class CompiledDeckContract(_StrictContractModel):
    """All synchronized artifacts emitted by the Deck Contract Compiler."""

    schema_version: Literal[1] = 1
    design_spec_markdown: str = Field(min_length=1)
    spec_lock_markdown: str = Field(min_length=1)
    typography: CompiledTypographyRoles
    title_font: str = Field(min_length=1, max_length=100)
    body_font: str = Field(min_length=1, max_length=100)
    code_font: str = Field(min_length=1, max_length=100)
    structure_mode: Literal["flat"] = "flat"

    @property
    def sha256(self) -> str:
        encoded = json.dumps(
            self.model_dump(mode="json"),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()

    def authoring_constraints(self) -> dict[str, object]:
        """Return the exact model-facing subset of the compiled contract."""
        return {
            "contract_sha256": self.sha256,
            "canvas": {
                "view_box": "0 0 1280 720",
                "format": "ppt169",
                "safe_margin_px": {"horizontal": 64, "vertical": 48},
            },
            "typography": {
                "roles_px": self.typography.model_dump(mode="json"),
                "allowed_recurring_sizes_px": list(self.typography.allowed_sizes),
                "size_tolerance_px": _FONT_SIZE_TOLERANCE_PX,
                "title_family": self.title_font,
                "body_family": self.body_font,
                "code_family": self.code_font,
                "rule": (
                    "Use a named role size for every recurring text treatment. "
                    "Do not invent recurring font sizes."
                ),
            },
        }

    def preflight_svg_typography(
        self,
        svg_text: str,
    ) -> tuple[TypographyPreflightIssue, ...]:
        """Find sizes outside every compiled role band in one complete SVG."""
        counts = _effective_text_size_counts(svg_text)
        anchors = tuple(float(value) for value in self.typography.allowed_sizes)
        issues = []
        for value, count in sorted(counts.items()):
            if count <= 2:
                continue
            if any(abs(value - anchor) <= _FONT_SIZE_TOLERANCE_PX for anchor in anchors):
                continue
            issues.append(TypographyPreflightIssue(
                font_size_px=value,
                occurrence_count=count,
            ))
        return tuple(issues)


class DeckContractCompiler:
    """Deep Module compiling all deck contracts from one confirmed context."""

    def compile(self, context: Any) -> CompiledDeckContract:
        snapshot = context.planning_snapshot
        if snapshot.phase != PlanningPhase.PLAN_CONFIRMED:
            raise ValueError("deck contracts require a confirmed Planning Snapshot")
        design = snapshot.design
        slides = snapshot.slides
        if design is None or slides is None:
            raise ValueError("confirmed design and slide intents are required")
        if design.structure_mode != "flat":
            raise ValueError("Host SVG authoring requires flat PPTX structure")
        typography = design.typography
        roles = _compile_typography_roles(
            title=typography.title_size_px,
            body=typography.body_size_px,
            caption=typography.caption_size_px,
        )
        return CompiledDeckContract(
            design_spec_markdown=_build_design_spec(context, roles),
            spec_lock_markdown=_build_spec_lock(context, roles),
            typography=roles,
            title_font=typography.title_font,
            body_font=typography.body_font,
            code_font=typography.monospace_font,
        )


def _compile_typography_roles(
    *,
    title: int,
    body: int,
    caption: int,
) -> CompiledTypographyRoles:
    subtitle = min(title, max(body + 4, 24))
    key_metric = min(title, max(subtitle, title - 8))
    return CompiledTypographyRoles(
        title=title,
        key_metric=key_metric,
        subtitle=subtitle,
        body=body,
        caption=caption,
    )


def _build_design_spec(
    context: Any,
    roles: CompiledTypographyRoles,
) -> str:
    snapshot = context.planning_snapshot
    request = snapshot.request
    design = snapshot.design
    slides = snapshot.slides
    if design is None or slides is None:  # pragma: no cover - compiler gate
        raise ValueError("design spec unavailable")

    palette = design.palette
    typography = design.typography
    lines = [
        "<!-- ppt-master-schema: design-spec/v1 -->",
        f"# {_md(request.report_title)} - Design Spec",
        "",
        "## I. Project Information",
        "",
        "| Item | Value |",
        "| --- | --- |",
        f"| Project Name | {_table(request.report_title)} |",
        "| Canvas Format | PowerPoint 16:9 (1280x720) |",
        f"| Page Count | {len(slides.slides)} |",
        f"| Target Audience | {_table(request.audience)} |",
        f"| Communication Intent | {_table(design.communication.objective)} |",
        f"| Desired Audience Outcome | {_table(design.communication.audience_success)} |",
        f"| Core Message / Ask / Action | {_table(request.objective)} |",
        "| Delivery Context | Formal technical review |",
        "| Artifact Afterlife | Editable PowerPoint and archived evidence |",
        "| Reading Mode | Presenter-led with standalone review support |",
        f"| Content Strategy | {_table(design.communication.content_divergence)} |",
        f"| Design Style | {_table(design.visual_style)} |",
        f"| Formula Policy | {_table(design.formula_policy)} |",
        "| AI Image Acquisition Path | not applicable; source assets only |",
        "| Generation Mode | native_svg |",
        f"| Spec Refinement | {_table(design.refine_spec)} |",
        "| Created Date | Host Planning Snapshot |",
        "",
        "## II. Canvas Specification",
        "",
        "| Property | Value |",
        "| --- | --- |",
        "| Format | PowerPoint 16:9 |",
        "| Dimensions | 1280 x 720 px |",
        "| viewBox | `0 0 1280 720` |",
        "| Margins | 64 px horizontal, 48 px vertical safe area |",
        "| Content Area | x=64..1216, y=96..660 |",
        "",
        "## III. Visual Theme",
        "",
        "### Theme Style",
        "",
        f"- **Mode**: {design.density}",
        f"- **Visual style**: {_md(design.visual_style)}",
        f"- **Theme**: {_md(design.chart_style)}",
        f"- **Tone**: {_md(design.communication.tone)}",
        "",
        "### Color Scheme",
        "",
        "| Role | HEX | Purpose |",
        "| --- | --- | --- |",
        f"| Background | {palette.background} | slide canvas |",
        f"| Primary | {palette.primary} | hierarchy and key evidence |",
        f"| Accent | {palette.accent} | decision emphasis |",
        f"| Body text | {palette.text} | readable content |",
        "",
        "## IV. Typography System",
        "",
        "### Font Plan",
        "",
        "| Role | Chinese | English | Fallback tail |",
        "| --- | --- | --- | --- |",
        f"| Title | {_table(typography.title_font)} | {_table(typography.title_font)} | sans-serif |",
        f"| Body | {_table(typography.body_font)} | {_table(typography.body_font)} | sans-serif |",
        f"| Emphasis | {_table(typography.title_font)} | {_table(typography.title_font)} | sans-serif |",
        f"| Code | {_table(typography.monospace_font)} | {_table(typography.monospace_font)} | monospace |",
        "",
        f"- Title: {_md(typography.title_font)}",
        f"- Body: {_md(typography.body_font)}",
        f"- Emphasis: {_md(typography.title_font)}",
        f"- Code: {_md(typography.monospace_font)}",
        "",
        "### Font Size Hierarchy",
        "",
        "| Named role | Size | Usage |",
        "| --- | --- | --- |",
        f"| title | {roles.title} px | page title |",
        f"| key_metric | {roles.key_metric} px | recurring numeric conclusion |",
        f"| subtitle | {roles.subtitle} px | section or panel heading |",
        f"| body | {roles.body} px | normal explanatory text |",
        f"| caption | {roles.caption} px | annotation and footnote |",
        "",
        "Only these named role sizes may recur across generated pages.",
        "",
        "## V. Layout Principles",
        "",
        "### Page Structure",
        "",
        "- **Header area**: conclusion-led title and optional section marker",
        "- **Content area**: one dominant evidence composition per slide",
        "- **Footer area**: page number and concise provenance",
        "",
        "### Spacing Specification",
        "",
        "| Element | Current Project |",
        "| --- | --- |",
        "| Safe margin | 64 px |",
        "| Content block gap | 24 px |",
        "| Icon-text gap | 12 px |",
        "",
        "## VI. Icon Usage Specification",
        "",
        "| Purpose | Icon Path | Page |",
        "| --- | --- | --- |",
        "| No decorative icon dependency | not applicable | all |",
        "",
        "## VIII. Image Resource List",
        "",
        "| Filename | Dimensions | Ratio | Purpose | Type | Layout pattern | Crop Policy | Acquire Via | Status | Reference | text_policy | page_role |",
        "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    if context.assets:
        for asset in context.assets:
            lines.append(
                f"| {asset.project_filename} | source | source | "
                f"{_table(asset.semantic_label)} | source | contain | no crop | "
                f"host | ready | {asset.asset_id} | none | evidence |"
            )
    else:
        lines.append(
            "| none | not applicable | not applicable | no source images | "
            "source | none | none | host | ready | none | none | none |"
        )
    lines.extend(["", "## IX. Content Outline", ""])
    for slide in slides.slides:
        lines.extend([
            f"### Part {slide.sequence}: {_md(slide.section_id)}",
            "",
            f"#### Slide {slide.sequence:02d} - {_md(slide.title)}",
            "",
            f"- **Audience move**: {_md(slide.message)}",
            f"- **Layout**: {slide.layout.value}",
            f"- **Title**: {_md(slide.title)}",
            f"- **Core message**: {_md(slide.message)}",
            f"- **Content**: {_md('; '.join(slide.content_points))}",
            "",
        ])
    lines.extend([
        "## X. Speaker Notes Requirements",
        "",
        "- **Filename**: match each SVG filename under `notes/`",
        "- **Content**: explain evidence, uncertainty, and the intended decision",
        "",
    ])
    return "\n".join(lines)


def _build_spec_lock(
    context: Any,
    roles: CompiledTypographyRoles,
) -> str:
    snapshot = context.planning_snapshot
    request = snapshot.request
    design = snapshot.design
    slides = snapshot.slides
    if design is None or slides is None:  # pragma: no cover - compiler gate
        raise ValueError("spec lock unavailable")
    typography = design.typography
    lines = [
        "<!-- ppt-master-schema: spec-lock/v1 -->",
        "# Execution Lock",
        "",
        "## canvas",
        "- viewBox: 0 0 1280 720",
        "- format: ppt169",
        "",
        "## communication",
        f"- audience: {_data(request.audience)}",
        f"- objective: {_data(request.objective)}",
        f"- core_message: {_data(slides.deck_title)}",
        "- consumption_mode: presentation",
        "",
        "## mode",
        "- mode: briefing",
        "",
        "## visual_style",
        f"- visual_style: {_data(design.visual_style)}",
        "",
        "## colors",
        f"- bg: {design.palette.background}",
        f"- primary: {design.palette.primary}",
        f"- accent: {design.palette.accent}",
        f"- text: {design.palette.text}",
        "",
        "## typography",
        f"- font_family: {_data(typography.body_font)}, sans-serif",
        f"- title_family: {_data(typography.title_font)}, sans-serif",
        f"- body_family: {_data(typography.body_font)}, sans-serif",
        f"- code_family: {_data(typography.monospace_font)}, monospace",
        f"- title: {roles.title}",
        f"- key_metric: {roles.key_metric}",
        f"- subtitle: {roles.subtitle}",
        f"- body: {roles.body}",
        f"- caption: {roles.caption}",
        "",
        "## icons",
        "- library: none",
        "- inventory: none",
        "",
        "## page_rhythm",
    ]
    for slide in slides.slides:
        rhythm = "anchor" if slide.sequence in {1, len(slides.slides)} else "breathing"
        lines.append(f"- P{slide.sequence:02d}: {rhythm}")
    lines.extend([
        "",
        "## pptx_structure",
        "- mode: flat",
    ])
    if request.template_mode == TemplateMode.VALIDATED_WORKSPACE:
        lines.append("- template_reuse_scope: style")
    lines.extend([
        "",
        "## forbidden",
        "- `mask`, `<style>`, `class`, external CSS, `<foreignObject>`, "
        "`textPath`, `@font-face`, `<animate*>`, `<set>`, `<script>` / event "
        "attributes, `<iframe>`, and external URLs",
        "- HTML named entities in text; use raw Unicode and escape XML reserved characters",
        "",
    ])
    return "\n".join(lines)


def _effective_text_size_counts(svg_text: str) -> Counter[float]:
    try:
        root = ElementTree.fromstring(svg_text)
    except ElementTree.ParseError:
        return Counter()
    parents = {
        id(child): parent
        for parent in root.iter()
        for child in parent
    }
    counts: Counter[float] = Counter()
    for text in root.iter():
        if text.tag.rsplit("}", 1)[-1].casefold() != "text":
            continue
        object_sizes: set[float] = set()
        for node in text.iter():
            if not "".join(node.itertext()).strip():
                continue
            raw = _inherited_attribute(node, parents, "font-size")
            if raw is None:
                continue
            match = re.fullmatch(r"([0-9]+(?:\.[0-9]+)?)(?:px)?", raw.strip(), re.I)
            if match is None:
                continue
            value = float(match.group(1))
            if math.isfinite(value) and value > 0:
                object_sizes.add(value)
        counts.update(object_sizes)
    return counts


def _inherited_attribute(
    node: ElementTree.Element,
    parents: dict[int, ElementTree.Element],
    name: str,
) -> str | None:
    current: ElementTree.Element | None = node
    while current is not None:
        value = current.attrib.get(name)
        if value:
            return value
        current = parents.get(id(current))
    return None


def _md(value: object) -> str:
    return " ".join(str(value).replace("\x00", "").split())


def _table(value: object) -> str:
    return _md(value).replace("|", "\\|")


def _data(value: object) -> str:
    return _md(value).replace("`", "'")
