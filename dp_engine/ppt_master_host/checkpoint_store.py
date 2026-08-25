"""Content-addressed Host checkpoints for resumable PPT Master authoring."""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import tempfile
from pathlib import Path
from typing import TYPE_CHECKING, Literal

from pydantic import BaseModel, ConfigDict, Field

from utils.app_paths import get_app_data_root

if TYPE_CHECKING:
    from dp_engine.report_provider.ppt_master import PptMasterAuthoredSlide


class _StrictCheckpointModel(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        str_strip_whitespace=True,
    )


class SlideCheckpointKey(_StrictCheckpointModel):
    """Every input that can change one authored page."""

    schema_version: Literal[1] = 1
    planning_context_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    deck_contract_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    toolchain_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    authoring_contract_version: Literal[2] = 2
    authoring_model_identity: str = Field(min_length=1, max_length=300)
    slide_input_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    method_receipt_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")

    @property
    def digest(self) -> str:
        return _model_sha256(self)


class SlideCheckpointReceipt(_StrictCheckpointModel):
    schema_version: Literal[1] = 1
    key: SlideCheckpointKey
    slide_id: str = Field(min_length=1, max_length=96)
    sequence: int = Field(ge=1, le=40)
    used_asset_ids: tuple[str, ...] = Field(default_factory=tuple, max_length=2)
    svg_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    notes_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


class SlideCheckpointStore:
    """Deep Module atomically publishing and verifying authored-page receipts."""

    def __init__(self, root: str | Path) -> None:
        candidate = Path(root).expanduser().resolve(strict=False)
        if candidate == Path(candidate.anchor):
            raise ValueError("slide checkpoint root must not be a filesystem root")
        self._root = candidate

    def load(self, key: SlideCheckpointKey) -> PptMasterAuthoredSlide | None:
        directory = self._entry(key)
        receipt_path = directory / "receipt.json"
        svg_path = directory / "slide.svg"
        notes_path = directory / "notes.md"
        try:
            receipt = SlideCheckpointReceipt.model_validate_json(
                receipt_path.read_text(encoding="utf-8")
            )
            svg_bytes = svg_path.read_bytes()
            notes_bytes = notes_path.read_bytes()
        except (OSError, UnicodeError, ValueError):
            return None
        if (
            receipt.key != key
            or hashlib.sha256(svg_bytes).hexdigest() != receipt.svg_sha256
            or hashlib.sha256(notes_bytes).hexdigest() != receipt.notes_sha256
        ):
            return None
        from dp_engine.report_provider.ppt_master import PptMasterAuthoredSlide

        try:
            return PptMasterAuthoredSlide(
                slide_id=receipt.slide_id,
                sequence=receipt.sequence,
                svg_text=svg_bytes.decode("utf-8"),
                speaker_notes_markdown=notes_bytes.decode("utf-8"),
                used_asset_ids=receipt.used_asset_ids,
            )
        except (UnicodeError, ValueError):
            return None

    def publish(
        self,
        key: SlideCheckpointKey,
        slide: PptMasterAuthoredSlide,
    ) -> SlideCheckpointReceipt:
        self._root.mkdir(parents=True, exist_ok=True)
        target = self._entry(key)
        existing = self.load(key)
        if existing is not None:
            return self._receipt(key, existing)
        staging_parent = self._root / ".staging"
        staging_parent.mkdir(exist_ok=True)
        staging = Path(tempfile.mkdtemp(prefix=f"{key.digest[:16]}-", dir=staging_parent))
        try:
            receipt = self._receipt(key, slide)
            _write_bytes(staging / "slide.svg", slide.svg_text.encode("utf-8"))
            _write_bytes(
                staging / "notes.md",
                slide.speaker_notes_markdown.encode("utf-8"),
            )
            _write_bytes(
                staging / "receipt.json",
                _canonical_json(receipt.model_dump(mode="json")).encode("utf-8"),
            )
            try:
                staging.rename(target)
            except FileExistsError:
                pass
            return receipt
        finally:
            shutil.rmtree(staging, ignore_errors=True)

    def _entry(self, key: SlideCheckpointKey) -> Path:
        digest = key.digest
        if re.fullmatch(r"[0-9a-f]{64}", digest) is None:  # pragma: no cover
            raise ValueError("invalid checkpoint digest")
        return self._root / digest

    @staticmethod
    def _receipt(
        key: SlideCheckpointKey,
        slide: PptMasterAuthoredSlide,
    ) -> SlideCheckpointReceipt:
        return SlideCheckpointReceipt(
            key=key,
            slide_id=slide.slide_id,
            sequence=slide.sequence,
            used_asset_ids=slide.used_asset_ids,
            svg_sha256=hashlib.sha256(slide.svg_text.encode("utf-8")).hexdigest(),
            notes_sha256=hashlib.sha256(
                slide.speaker_notes_markdown.encode("utf-8")
            ).hexdigest(),
        )


def _write_bytes(path: Path, value: bytes) -> None:
    with path.open("xb") as output:
        output.write(value)
        output.flush()
        os.fsync(output.fileno())


def _canonical_json(value: object) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def _model_sha256(model: BaseModel) -> str:
    return hashlib.sha256(
        _canonical_json(model.model_dump(mode="json", exclude_none=True)).encode("utf-8")
    ).hexdigest()


def get_default_slide_checkpoint_root() -> Path:
    return get_app_data_root() / "toolchains" / "ppt-master" / "slide-checkpoints"
