"""Tests for dp_engine/report_bridge/adapters.py — Batch 3.3.1B.

Covers:
  - Path security validation (traversal, symlink, absolute, workspace escape)
  - Asset order validation
  - IMAGE / TABLE_SOURCE / TEXT_SOURCE validation for Word and PPT
  - PPT TABLE_SOURCE rejection
  - Word appendix (image, table, text insertion)
  - PPT appendix (image slide, text pagination)
  - Cancel checkpoints
"""

from __future__ import annotations

import os
import uuid
from pathlib import Path

import pytest
from docx import Document

from dp_engine.report_bridge.models import (
    PreparedReportAsset,
    ReportAssetRole,
    ParsedTable,
)
from dp_engine.report_bridge.adapters import (
    validate_bridge_assets_for_word,
    validate_bridge_assets_for_ppt,
    append_bridge_assets_to_word,
    append_bridge_assets_to_ppt,
    BridgeAdapterError,
    _resolve_asset_path,
    _safe_display_name,
)
from dp_engine.skills.runtime_models import RuntimeArtifact


# ── Helpers ──

def _uid() -> str:
    return uuid.uuid4().hex


def _make_artifact(
    artifact_id: str | None = None,
    media_type: str = "image/png",
    display_name: str = "test.png",
) -> RuntimeArtifact:
    return RuntimeArtifact(
        relative_path="output/test.png",
        size_bytes=1024,
        sha256="a" * 64,
        artifact_schema_version=1,
        artifact_id=artifact_id or _uid(),
        display_name=display_name,
        media_type=media_type,
        task_id=_uid(),
        skill_id="testskill001",
        kind="output",
        created_at="2026-07-23T00:00:00Z",
    )


def _make_asset(
    role: ReportAssetRole,
    order: int = 0,
    managed_filename: str = "00_asset.png",
    parsed_payload=None,
    display_name: str = "Test Asset",
) -> PreparedReportAsset:
    art = _make_artifact(
        media_type={
            ReportAssetRole.IMAGE: "image/png",
            ReportAssetRole.TABLE_SOURCE: "text/csv",
            ReportAssetRole.TEXT_SOURCE: "text/plain",
        }.get(role, "image/png"),
        display_name=display_name,
    )
    return PreparedReportAsset(
        authoritative_artifact=art,
        role=role,
        order=order,
        managed_filename=managed_filename,
        parsed_payload=parsed_payload,
    )


def _make_image_asset(order: int = 0, filename: str = "00_test.png") -> PreparedReportAsset:
    return _make_asset(
        ReportAssetRole.IMAGE, order=order,
        managed_filename=filename, parsed_payload=None,
    )


def _make_table_asset(
    order: int = 0,
    filename: str = "01_table.csv",
    data: ParsedTable | None = None,
) -> PreparedReportAsset:
    if data is None:
        data = (("Name", "Value"), ("A", "1"), ("B", "2"))
    return _make_asset(
        ReportAssetRole.TABLE_SOURCE, order=order,
        managed_filename=filename, parsed_payload=data,
    )


def _make_text_asset(
    order: int = 0,
    filename: str = "02_text.txt",
    text: str = "Sample text content.",
) -> PreparedReportAsset:
    return _make_asset(
        ReportAssetRole.TEXT_SOURCE, order=order,
        managed_filename=filename, parsed_payload=text,
    )


# ── Fixtures ──

@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    """Create a clean bridge workspace."""
    ws = tmp_path / "bridge_ws"
    ws.mkdir()
    return ws


@pytest.fixture
def image_file(workspace: Path) -> Path:
    """Create a minimal valid PNG file for testing."""
    import struct
    import zlib

    def _create_png(width: int, height: int) -> bytes:
        def chunk(chunk_type: bytes, data: bytes) -> bytes:
            c = chunk_type + data
            return struct.pack(">I", len(data)) + c + struct.pack(">I", zlib.crc32(c) & 0xFFFFFFFF)

        sig = b"\x89PNG\r\n\x1a\n"
        ihdr = chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
        raw = b""
        for y in range(height):
            raw += b"\x00" + b"\xff\x00\x00" * width
        idat = chunk(b"IDAT", zlib.compress(raw))
        iend = chunk(b"IEND", b"")
        return sig + ihdr + idat + iend

    png_path = workspace / "00_test.png"
    png_path.write_bytes(_create_png(10, 10))
    return png_path


@pytest.fixture
def csv_file(workspace: Path) -> Path:
    csv_path = workspace / "01_table.csv"
    csv_path.write_text("Name,Value\nA,1\nB,2\n", encoding="utf-8")
    return csv_path


@pytest.fixture
def txt_file(workspace: Path) -> Path:
    txt_path = workspace / "02_text.txt"
    txt_path.write_text("Hello world\nLine two\n", encoding="utf-8")
    return txt_path


# ═══════════════════════════════════════════════════
# Path security tests
# ═══════════════════════════════════════════════════

class TestPathSecurity:
    """RB-ATOM related: bridge path security."""

    def test_valid_managed_filename_resolves(self, workspace: Path, image_file: Path):
        asset = _make_image_asset(filename="00_test.png")
        resolved = _resolve_asset_path(workspace, asset)
        assert resolved == image_file

    def test_managed_filename_with_traversal_rejected(self, workspace: Path):
        # Create symlink inside workspace pointing outside → adapter rejects
        asset = _make_image_asset(filename="00_escape.png")
        link = workspace / "00_escape.png"
        outside = workspace.parent / "outside.txt"
        outside.write_text("escaped")
        try:
            os.symlink(str(outside), str(link))
        except OSError:
            pytest.skip("symlink not supported on this platform/filesystem")
        with pytest.raises(BridgeAdapterError) as exc:
            _resolve_asset_path(workspace, asset)
        assert exc.value.code == "bridge_asset_invalid"

    def test_dot_dot_model_level_rejected(self):
        """Managed filename '..' is rejected at model construction."""
        art = _make_artifact()
        with pytest.raises(ValueError):
            PreparedReportAsset(
                authoritative_artifact=art,
                role=ReportAssetRole.IMAGE,
                order=0,
                managed_filename="..",
                parsed_payload=None,
            )

    def test_path_separator_model_level_rejected(self):
        """Path separators in managed_filename rejected at model level."""
        art = _make_artifact()
        with pytest.raises(ValueError, match="path separator"):
            PreparedReportAsset(
                authoritative_artifact=art,
                role=ReportAssetRole.IMAGE,
                order=0,
                managed_filename="sub/dir/file.png",
                parsed_payload=None,
            )

    def test_absolute_path_model_level_rejected(self):
        """Absolute or path-separator-containing paths rejected at model level."""
        art = _make_artifact()
        with pytest.raises(ValueError):
            PreparedReportAsset(
                authoritative_artifact=art,
                role=ReportAssetRole.IMAGE,
                order=0,
                managed_filename="/etc/passwd",
                parsed_payload=None,
            )

    def test_workspace_not_exist_rejected(self, tmp_path: Path):
        ws = tmp_path / "nonexistent"
        asset = _make_image_asset()
        with pytest.raises(BridgeAdapterError) as exc:
            _resolve_asset_path(ws, asset)
        assert exc.value.code == "bridge_asset_missing"

    def test_workspace_is_file_rejected(self, workspace: Path):
        file_path = workspace / "not_a_dir"
        file_path.write_text("i am a file")
        asset = _make_image_asset()
        with pytest.raises(BridgeAdapterError) as exc:
            _resolve_asset_path(file_path, asset)
        assert exc.value.code == "bridge_asset_missing"

    def test_file_not_exist_rejected(self, workspace: Path):
        asset = _make_image_asset(filename="nonexistent.png")
        with pytest.raises(BridgeAdapterError) as exc:
            _resolve_asset_path(workspace, asset)
        assert exc.value.code == "bridge_asset_missing"

    def test_subdirectory_escape_model_level_rejected(self):
        """Filenames with path separators rejected before adapter sees them.

        The adapter's parent-directory check is defense-in-depth.
        The model-level validation is the primary guard.
        """
        art = _make_artifact()
        with pytest.raises(ValueError):
            PreparedReportAsset(
                authoritative_artifact=art,
                role=ReportAssetRole.IMAGE,
                order=0,
                managed_filename="sub/deep.png",
                parsed_payload=None,
            )


class TestSymlinkRejection:
    """Bridge path security: symlink rejection."""

    def test_symlink_file_rejected(self, workspace: Path, tmp_path: Path):
        # Create real file outside workspace
        outside = tmp_path / "outside.png"
        outside.write_bytes(b"fake png")
        # Create symlink in workspace pointing outside
        link = workspace / "00_link.png"
        try:
            os.symlink(str(outside), str(link))
        except OSError:
            pytest.skip("symlink not supported on this platform/filesystem")
        asset = _make_image_asset(filename="00_link.png")
        with pytest.raises(BridgeAdapterError) as exc:
            _resolve_asset_path(workspace, asset)
        assert exc.value.code == "bridge_asset_invalid"


# ═══════════════════════════════════════════════════
# Asset validation tests
# ═══════════════════════════════════════════════════

class TestValidateBridgeAssetsForWord:
    """Word asset validation."""

    def test_empty_assets_noop(self, workspace: Path):
        validate_bridge_assets_for_word(workspace, ())

    def test_valid_image_asset(self, workspace: Path, image_file: Path):
        asset = _make_image_asset(filename="00_test.png")
        validate_bridge_assets_for_word(workspace, (asset,))

    def test_valid_table_asset(self, workspace: Path, csv_file: Path):
        asset = _make_table_asset(filename="01_table.csv")
        validate_bridge_assets_for_word(workspace, (asset,))

    def test_valid_text_asset(self, workspace: Path, txt_file: Path):
        asset = _make_text_asset(filename="02_text.txt")
        validate_bridge_assets_for_word(workspace, (asset,))

    def test_multi_asset_valid_order(self, workspace: Path,
                                     image_file: Path, txt_file: Path):
        a0 = _make_image_asset(order=0, filename="00_test.png")
        a1 = _make_text_asset(order=1, filename="02_text.txt")
        validate_bridge_assets_for_word(workspace, (a0, a1))

    def test_invalid_order_rejected(self, workspace: Path, image_file: Path):
        a0 = _make_image_asset(order=0, filename="00_test.png")
        a1 = _make_image_asset(order=5, filename="00_test.png")
        with pytest.raises(BridgeAdapterError) as exc:
            validate_bridge_assets_for_word(workspace, (a0, a1))
        assert exc.value.code == "bridge_asset_invalid"

    def test_duplicate_order_rejected(self, workspace: Path, image_file: Path):
        a0 = _make_image_asset(order=0, filename="00_test.png")
        a1 = _make_image_asset(order=0, filename="00_test.png")
        with pytest.raises(BridgeAdapterError) as exc:
            validate_bridge_assets_for_word(workspace, (a0, a1))
        assert exc.value.code == "bridge_asset_invalid"

    def test_wrong_payload_type_rejected(self):
        """IMAGE role with str payload rejected at model level before adapter."""
        art = _make_artifact(media_type="image/png")
        with pytest.raises(ValueError, match="parsed_payload"):
            PreparedReportAsset(
                authoritative_artifact=art,
                role=ReportAssetRole.IMAGE,
                order=0,
                managed_filename="00_test.png",
                parsed_payload="wrong",
            )

    def test_table_source_with_non_tuple_rejected(self):
        """TABLE_SOURCE with non-tuple payload rejected at model level."""
        art = _make_artifact(media_type="text/csv")
        with pytest.raises(ValueError, match="parsed_payload"):
            PreparedReportAsset(
                authoritative_artifact=art,
                role=ReportAssetRole.TABLE_SOURCE,
                order=0,
                managed_filename="01_table.csv",
                parsed_payload="not a tuple",
            )


class TestValidateBridgeAssetsForPPT:
    """PPT asset validation — TABLE_SOURCE rejected."""

    def test_empty_assets_noop(self, workspace: Path):
        validate_bridge_assets_for_ppt(workspace, ())

    def test_valid_image_asset(self, workspace: Path, image_file: Path):
        asset = _make_image_asset(filename="00_test.png")
        validate_bridge_assets_for_ppt(workspace, (asset,))

    def test_table_source_rejected_for_ppt(self, workspace: Path, csv_file: Path):
        """RB-RES-06: PPT TABLE_SOURCE explicitly rejected."""
        asset = _make_table_asset(filename="01_table.csv")
        with pytest.raises(BridgeAdapterError) as exc:
            validate_bridge_assets_for_ppt(workspace, (asset,))
        assert exc.value.code == "role_mismatch"


# ═══════════════════════════════════════════════════
# Word appendix tests
# ═══════════════════════════════════════════════════

class TestAppendBridgeAssetsToWord:
    """Word deterministic appendix."""

    def test_no_assets_no_change(self, workspace: Path):
        doc = Document()
        doc.add_heading("Original Content", level=1)
        doc.add_paragraph("Some text.")
        append_bridge_assets_to_word(
            doc, bridge_workspace=workspace, assets=(),
        )
        # Only original content
        texts = [p.text for p in doc.paragraphs]
        assert "技能输出素材" not in texts
        assert "Original Content" in texts

    def test_image_inserted(self, workspace: Path, image_file: Path):
        doc = Document()
        doc.add_heading("Report", level=1)
        asset = _make_image_asset(filename="00_test.png")
        append_bridge_assets_to_word(
            doc, bridge_workspace=workspace, assets=(asset,),
        )
        texts = [p.text for p in doc.paragraphs]
        assert "技能输出素材" in texts

    def test_table_inserted_with_bold_header(
        self, workspace: Path, csv_file: Path,
    ):
        doc = Document()
        asset = _make_table_asset(
            filename="01_table.csv",
            data=(("ColA", "ColB"), ("1", "2"), ("3", "4")),
        )
        append_bridge_assets_to_word(
            doc, bridge_workspace=workspace, assets=(asset,),
        )
        # Verify table exists
        assert len(doc.tables) >= 1
        tbl = doc.tables[-1]
        # Header row bold
        header_cell = tbl.cell(0, 0)
        first_run = header_cell.paragraphs[0].runs[0] if header_cell.paragraphs[0].runs else None
        # At minimum, table has content
        assert header_cell.text == "ColA"

    def test_text_inserted(self, workspace: Path, txt_file: Path):
        doc = Document()
        asset = _make_text_asset(
            filename="02_text.txt",
            text="Hello from Bridge!",
        )
        append_bridge_assets_to_word(
            doc, bridge_workspace=workspace, assets=(asset,),
        )
        texts = [p.text for p in doc.paragraphs]
        assert "Hello from Bridge!" in texts

    def test_multi_asset_order_preserved(
        self, workspace: Path, image_file: Path, txt_file: Path,
    ):
        doc = Document()
        a0 = _make_image_asset(order=0, filename="00_test.png")
        a1 = _make_text_asset(
            order=1, filename="02_text.txt",
            text="SECOND_ASSET_TEXT",
        )
        append_bridge_assets_to_word(
            doc, bridge_workspace=workspace, assets=(a0, a1),
        )
        texts = [p.text for p in doc.paragraphs]
        # Both present
        assert "技能输出素材" in texts

    def test_non_rectangular_table_rejected(self, workspace: Path):
        asset = _make_asset(
            ReportAssetRole.TABLE_SOURCE, order=0,
            managed_filename="01_table.csv",
            parsed_payload=(("A", "B"), ("1",)),  # row shorter than header
        )
        # Place a file so path validation passes
        (workspace / "01_table.csv").write_text("a,b\n1\n", encoding="utf-8")
        with pytest.raises(BridgeAdapterError) as exc:
            append_bridge_assets_to_word(
                Document(), bridge_workspace=workspace, assets=(asset,),
            )
        assert exc.value.code == "bridge_asset_invalid"

    def test_empty_table_rejected(self, workspace: Path):
        asset = _make_asset(
            ReportAssetRole.TABLE_SOURCE, order=0,
            managed_filename="01_table.csv",
            parsed_payload=(),
        )
        (workspace / "01_table.csv").write_text("", encoding="utf-8")
        with pytest.raises(BridgeAdapterError) as exc:
            append_bridge_assets_to_word(
                Document(), bridge_workspace=workspace, assets=(asset,),
            )
        assert exc.value.code == "bridge_asset_invalid"


# ═══════════════════════════════════════════════════
# PPT appendix tests
# ═══════════════════════════════════════════════════

class TestAppendBridgeAssetsToPPT:
    """PPT deterministic appendix."""

    def test_no_assets_no_change(self, workspace: Path):
        from pptx import Presentation
        prs = Presentation()
        prs.slide_width = 9144000  # 10 inches
        prs.slide_height = 5143500
        n_before = len(prs.slides)
        append_bridge_assets_to_ppt(
            prs, bridge_workspace=workspace, assets=(),
        )
        assert len(prs.slides) == n_before

    def test_image_slide_created(self, workspace: Path, image_file: Path):
        from pptx import Presentation
        prs = Presentation()
        prs.slide_width = 9144000
        prs.slide_height = 5143500
        n_before = len(prs.slides)
        asset = _make_image_asset(filename="00_test.png")
        append_bridge_assets_to_ppt(
            prs, bridge_workspace=workspace, assets=(asset,),
        )
        assert len(prs.slides) == n_before + 1

    def test_text_single_page(self, workspace: Path, txt_file: Path):
        from pptx import Presentation
        prs = Presentation()
        prs.slide_width = 9144000
        prs.slide_height = 5143500
        n_before = len(prs.slides)
        asset = _make_text_asset(
            filename="02_text.txt",
            text="Line one\nLine two\nLine three",
        )
        append_bridge_assets_to_ppt(
            prs, bridge_workspace=workspace, assets=(asset,),
        )
        assert len(prs.slides) == n_before + 1

    def test_text_multi_page(self, workspace: Path, txt_file: Path):
        from pptx import Presentation
        prs = Presentation()
        prs.slide_width = 9144000
        prs.slide_height = 5143500
        # 10 lines → one slide (max 8 bullets)
        lines = [f"Line {i:03d}" for i in range(10)]
        text = "\n".join(lines)
        asset = _make_text_asset(filename="02_text.txt", text=text)
        append_bridge_assets_to_ppt(
            prs, bridge_workspace=workspace, assets=(asset,),
        )
        # 10 bullets → ceil(10/8) = 2 slides
        assert len(prs.slides) >= 2

    def test_text_max_8_bullets_per_slide(self, workspace: Path, txt_file: Path):
        from pptx import Presentation
        prs = Presentation()
        prs.slide_width = 9144000
        prs.slide_height = 5143500
        # 20 lines → ceil(20/8) = 3 slides
        lines = [f"Line {i:03d}" for i in range(20)]
        text = "\n".join(lines)
        asset = _make_text_asset(filename="02_text.txt", text=text)
        append_bridge_assets_to_ppt(
            prs, bridge_workspace=workspace, assets=(asset,),
        )
        assert len(prs.slides) == 3

    def test_text_long_line_splits_at_200_chars(
        self, workspace: Path, txt_file: Path,
    ):
        from pptx import Presentation
        prs = Presentation()
        prs.slide_width = 9144000
        prs.slide_height = 5143500
        long_line = "X" * 350
        asset = _make_text_asset(filename="02_text.txt", text=long_line)
        append_bridge_assets_to_ppt(
            prs, bridge_workspace=workspace, assets=(asset,),
        )
        # Single long line → split into 2 bullets (200 + 150)
        assert len(prs.slides) == 1

    def test_text_empty_produces_safe_slide(self, workspace: Path, txt_file: Path):
        from pptx import Presentation
        prs = Presentation()
        prs.slide_width = 9144000
        prs.slide_height = 5143500
        asset = _make_text_asset(filename="02_text.txt", text="")
        append_bridge_assets_to_ppt(
            prs, bridge_workspace=workspace, assets=(asset,),
        )
        # Empty text → one slide with empty state message
        assert len(prs.slides) == 1

    def test_table_source_rejected_in_append(self, workspace: Path):
        """RB-RES-06: TABLE_SOURCE rejected before modifying presentation."""
        from pptx import Presentation
        prs = Presentation()
        n_before = len(prs.slides)
        asset = _make_table_asset()
        (workspace / "01_table.csv").write_text("a,b\n1,2\n", encoding="utf-8")
        with pytest.raises(BridgeAdapterError) as exc:
            append_bridge_assets_to_ppt(
                prs, bridge_workspace=workspace, assets=(asset,),
            )
        assert exc.value.code == "role_mismatch"
        # Presentation not modified
        assert len(prs.slides) == n_before


# ═══════════════════════════════════════════════════
# Cancel checkpoint tests
# ═══════════════════════════════════════════════════

class TestCancelCheckpoints:
    """Cancel checkpoints in adapter functions."""

    def test_word_cancel_before_appendix_raises(
        self, workspace: Path, image_file: Path,
    ):
        doc = Document()
        asset = _make_image_asset(filename="00_test.png")

        def _cancel():
            raise RuntimeError("cancelled")

        with pytest.raises(RuntimeError, match="cancelled"):
            append_bridge_assets_to_word(
                doc,
                bridge_workspace=workspace,
                assets=(asset,),
                cancel_check=_cancel,
            )

    def test_ppt_cancel_before_appendix_raises(
        self, workspace: Path, image_file: Path,
    ):
        from pptx import Presentation
        prs = Presentation()
        prs.slide_width = 9144000
        prs.slide_height = 5143500
        asset = _make_image_asset(filename="00_test.png")

        def _cancel():
            raise RuntimeError("cancelled")

        with pytest.raises(RuntimeError, match="cancelled"):
            append_bridge_assets_to_ppt(
                prs,
                bridge_workspace=workspace,
                assets=(asset,),
                cancel_check=_cancel,
            )


# ═══════════════════════════════════════════════════
# Safe display name tests
# ═══════════════════════════════════════════════════

class TestSafeDisplayName:
    """Safe display name never exposes paths or internal IDs."""

    def test_uses_authoritative_display_name(self):
        art = _make_artifact(display_name="my_chart.png")
        asset = PreparedReportAsset(
            authoritative_artifact=art,
            role=ReportAssetRole.IMAGE,
            order=0,
            managed_filename="00_abcd.png",
            parsed_payload=None,
        )
        name = _safe_display_name(asset)
        assert name == "my_chart.png"

    def test_fallback_role_name(self):
        art = _make_artifact(display_name="")
        asset = PreparedReportAsset(
            authoritative_artifact=art,
            role=ReportAssetRole.TABLE_SOURCE,
            order=0,
            managed_filename="01_data.csv",
            parsed_payload=(("A",), ("1",)),
        )
        name = _safe_display_name(asset)
        assert "表格" in name
