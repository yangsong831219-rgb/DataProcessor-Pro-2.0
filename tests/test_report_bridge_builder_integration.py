"""Tests for Builder integration with Report Bridge assets — Batch 3.3.1B.

Covers:
  - Word: no-bridge unchanged, IMAGE/TABLE/TEXT insertion, order, security
  - PPT: no-bridge unchanged, IMAGE/TEXT pages, TABLE_SOURCE rejection
  - project_dir compatibility
  - Bridge isolation from fuzzy search
  - Final DOCX/PPTX re-openable
"""

from __future__ import annotations

import io
import os
import struct
import uuid
import zlib
from pathlib import Path

import pytest
from docx import Document

from dp_engine.report_bridge.models import (
    PreparedReportAsset,
    ReportAssetRole,
)
from dp_engine.report_bridge.adapters import BridgeAdapterError
from dp_engine.report_builder.models import (
    WordReport, WordSection, WordTable,
    PPTReport, PPTSlide,
)
from dp_engine.skills.runtime_models import RuntimeArtifact


# ── Helpers ──

def _uid() -> str:
    return uuid.uuid4().hex


def _make_png_bytes(width: int = 10, height: int = 10) -> bytes:
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


def _make_image_asset(
    order: int = 0,
    filename: str = "00_test.png",
    display_name: str = "Test Image",
) -> PreparedReportAsset:
    return PreparedReportAsset(
        authoritative_artifact=_make_artifact(media_type="image/png", display_name=display_name),
        role=ReportAssetRole.IMAGE,
        order=order,
        managed_filename=filename,
        parsed_payload=None,
    )


def _make_table_asset(
    order: int = 0,
    filename: str = "01_data.csv",
    display_name: str = "Test Table",
) -> PreparedReportAsset:
    return PreparedReportAsset(
        authoritative_artifact=_make_artifact(media_type="text/csv", display_name=display_name),
        role=ReportAssetRole.TABLE_SOURCE,
        order=order,
        managed_filename=filename,
        parsed_payload=(("ColA", "ColB"), ("1", "2"), ("3", "4")),
    )


def _make_text_asset(
    order: int = 0,
    filename: str = "02_text.txt",
    text: str = "Sample text.",
    display_name: str = "Test Text",
) -> PreparedReportAsset:
    return PreparedReportAsset(
        authoritative_artifact=_make_artifact(media_type="text/plain", display_name=display_name),
        role=ReportAssetRole.TEXT_SOURCE,
        order=order,
        managed_filename=filename,
        parsed_payload=text,
    )


# ── Fixtures ──

@pytest.fixture
def tmp_output_dir(tmp_path: Path) -> Path:
    out = tmp_path / "output"
    out.mkdir()
    return out


@pytest.fixture
def project_dir(tmp_path: Path) -> Path:
    pd = tmp_path / "project"
    pd.mkdir()
    return pd


@pytest.fixture
def bridge_workspace(tmp_path: Path) -> Path:
    ws = tmp_path / "bridge_ws"
    ws.mkdir()
    return ws


@pytest.fixture
def png_in_bridge(bridge_workspace: Path) -> Path:
    p = bridge_workspace / "00_test.png"
    p.write_bytes(_make_png_bytes())
    return p


@pytest.fixture
def csv_in_bridge(bridge_workspace: Path) -> Path:
    p = bridge_workspace / "01_data.csv"
    p.write_text("a,b\n1,2\n", encoding="utf-8")
    return p


@pytest.fixture
def txt_in_bridge(bridge_workspace: Path) -> Path:
    p = bridge_workspace / "02_text.txt"
    p.write_text("Hello world\n", encoding="utf-8")
    return p


# ═══════════════════════════════════════════════════
# Word Builder — no Bridge assets unchanged
# ═══════════════════════════════════════════════════

class TestWordBuilderNoBridge:
    """RB-LLM-06: No bridge assets → output unchanged."""

    def test_output_unchanged_without_bridge(
        self, tmp_output_dir: Path, project_dir: Path,
    ):
        from dp_engine.report_builder.word_builder import WordBuilder

        report = WordReport(
            title="Test Report",
            sections=[
                WordSection(
                    heading="Section 1",
                    content_paragraphs=["Paragraph one."],
                ),
            ],
        )
        out_path = tmp_output_dir / "test.docx"

        builder = WordBuilder()
        builder.build_word_report(
            report, "", str(out_path), project_dir=str(project_dir),
        )
        assert out_path.exists()
        doc = Document(str(out_path))
        texts = [p.text for p in doc.paragraphs]
        assert "技能输出素材" not in texts
        assert "Section 1" in texts

    def test_no_bridge_params_still_works(
        self, tmp_output_dir: Path, project_dir: Path,
    ):
        """Existing callers without bridge params continue to work."""
        from dp_engine.report_builder.word_builder import WordBuilder

        report = WordReport(
            title="Legacy Report",
            sections=[
                WordSection(heading="S1", content_paragraphs=["Text."]),
            ],
        )
        out_path = tmp_output_dir / "legacy.docx"

        builder = WordBuilder()
        # Call with only existing positional args
        builder.build_word_report(report, "", str(out_path), str(project_dir))
        assert out_path.exists()


# ═══════════════════════════════════════════════════
# Word Builder — Bridge assets
# ═══════════════════════════════════════════════════

class TestWordBuilderWithBridge:
    """Word Builder with Bridge asset appendix."""

    def test_image_asset_appended(
        self, tmp_output_dir: Path, bridge_workspace: Path,
        png_in_bridge: Path,
    ):
        from dp_engine.report_builder.word_builder import WordBuilder

        report = WordReport(
            title="Report with Image",
            sections=[
                WordSection(heading="S1", content_paragraphs=["Text."]),
            ],
        )
        out_path = tmp_output_dir / "with_image.docx"
        asset = _make_image_asset(filename="00_test.png")

        builder = WordBuilder()
        builder.build_word_report(
            report, "", str(out_path),
            bridge_workspace=bridge_workspace,
            bridge_assets=(asset,),
        )
        assert out_path.exists()
        doc = Document(str(out_path))
        texts = [p.text for p in doc.paragraphs]
        assert "技能输出素材" in texts

    def test_table_asset_appended(
        self, tmp_output_dir: Path, bridge_workspace: Path,
        csv_in_bridge: Path,
    ):
        from dp_engine.report_builder.word_builder import WordBuilder

        report = WordReport(
            title="Report with Table",
            sections=[
                WordSection(heading="S1", content_paragraphs=["Text."]),
            ],
        )
        out_path = tmp_output_dir / "with_table.docx"
        asset = _make_table_asset(filename="01_data.csv")

        builder = WordBuilder()
        builder.build_word_report(
            report, "", str(out_path),
            bridge_workspace=bridge_workspace,
            bridge_assets=(asset,),
        )
        assert out_path.exists()
        doc = Document(str(out_path))
        assert len(doc.tables) >= 1

    def test_text_asset_appended(
        self, tmp_output_dir: Path, bridge_workspace: Path,
        txt_in_bridge: Path,
    ):
        from dp_engine.report_builder.word_builder import WordBuilder

        report = WordReport(
            title="Report with Text",
            sections=[
                WordSection(heading="S1", content_paragraphs=["Text."]),
            ],
        )
        out_path = tmp_output_dir / "with_text.docx"
        asset = _make_text_asset(filename="02_text.txt", text="Bridge text content")

        builder = WordBuilder()
        builder.build_word_report(
            report, "", str(out_path),
            bridge_workspace=bridge_workspace,
            bridge_assets=(asset,),
        )
        assert out_path.exists()
        doc = Document(str(out_path))
        texts = [p.text for p in doc.paragraphs]
        assert "Bridge text content" in texts

    def test_multi_asset_by_order(
        self, tmp_output_dir: Path, bridge_workspace: Path,
        png_in_bridge: Path, txt_in_bridge: Path,
    ):
        from dp_engine.report_builder.word_builder import WordBuilder

        report = WordReport(
            title="Multi Asset",
            sections=[WordSection(heading="S1", content_paragraphs=["Text."])],
        )
        out_path = tmp_output_dir / "multi.docx"
        a0 = _make_image_asset(order=0, filename="00_test.png")
        a1 = _make_text_asset(order=1, filename="02_text.txt", text="SECOND")

        builder = WordBuilder()
        builder.build_word_report(
            report, "", str(out_path),
            bridge_workspace=bridge_workspace,
            bridge_assets=(a0, a1),
        )
        assert out_path.exists()

    def test_final_docx_reopenable(
        self, tmp_output_dir: Path, bridge_workspace: Path,
        png_in_bridge: Path,
    ):
        from dp_engine.report_builder.word_builder import WordBuilder

        report = WordReport(
            title="Reopen Test",
            sections=[WordSection(heading="S1", content_paragraphs=["Text."])],
        )
        out_path = tmp_output_dir / "reopen.docx"
        asset = _make_image_asset(filename="00_test.png")

        builder = WordBuilder()
        builder.build_word_report(
            report, "", str(out_path),
            bridge_workspace=bridge_workspace,
            bridge_assets=(asset,),
        )
        # Must re-open without error
        doc = Document(str(out_path))
        assert doc is not None


# ═══════════════════════════════════════════════════
# Word Builder — project_dir / bridge_workspace isolation
# ═══════════════════════════════════════════════════

class TestProjectDirBridgeIsolation:
    """RB-LLM-06, RB-LLM-07: project_dir and bridge_workspace independent."""

    def test_same_filename_different_dirs_isolated(
        self, tmp_output_dir: Path, project_dir: Path,
        bridge_workspace: Path,
    ):
        """Same filename in both dirs — bridge appendix uses bridge_workspace only."""
        # Place same-named file in project_dir
        (project_dir / "shared.png").write_bytes(_make_png_bytes())
        # Place same-named file in bridge_workspace
        (bridge_workspace / "00_shared.png").write_bytes(_make_png_bytes())

        from dp_engine.report_builder.word_builder import WordBuilder

        report = WordReport(
            title="Isolation Test",
            sections=[WordSection(heading="S1", content_paragraphs=["Text."])],
        )
        out_path = tmp_output_dir / "isolation.docx"
        asset = _make_image_asset(filename="00_shared.png")

        builder = WordBuilder()
        builder.build_word_report(
            report, "", str(out_path),
            project_dir=str(project_dir),
            bridge_workspace=bridge_workspace,
            bridge_assets=(asset,),
        )
        assert out_path.exists()

    def test_project_dir_search_unchanged(
        self, tmp_output_dir: Path, project_dir: Path,
    ):
        """project_dir continues to work for [INSERT_IMAGE]."""
        (project_dir / "chart.png").write_bytes(_make_png_bytes())

        from dp_engine.report_builder.word_builder import WordBuilder

        report = WordReport(
            title="Search Test",
            sections=[
                WordSection(
                    heading="S1",
                    content_paragraphs=["See chart below."],
                    image_anchors=["[INSERT_IMAGE: chart.png]"],
                ),
            ],
        )
        out_path = tmp_output_dir / "search.docx"

        builder = WordBuilder()
        builder.build_word_report(
            report, "", str(out_path), project_dir=str(project_dir),
        )
        assert out_path.exists()


# ═══════════════════════════════════════════════════
# PPT Builder — no Bridge assets unchanged
# ═══════════════════════════════════════════════════

class TestPPTBuilderNoBridge:
    """PPT: no bridge assets → output unchanged."""

    def test_output_unchanged_without_bridge(self, tmp_output_dir: Path):
        from dp_engine.report_builder.ppt_builder import PPTBuilder

        report = PPTReport(
            title="Test PPT",
            slides=[PPTSlide(slide_title="Slide 1", bullet_points=["Point A"])],
        )
        out_path = tmp_output_dir / "test.pptx"

        builder = PPTBuilder()
        builder.build_ppt_report(report, "", str(out_path))
        assert out_path.exists()
        from pptx import Presentation
        prs = Presentation(str(out_path))
        # 1 title slide + 1 content slide = 2
        assert len(prs.slides) == 2

    def test_no_bridge_params_still_works(self, tmp_output_dir: Path):
        from dp_engine.report_builder.ppt_builder import PPTBuilder

        report = PPTReport(
            title="Legacy PPT",
            slides=[PPTSlide(slide_title="S1", bullet_points=["Point."])],
        )
        out_path = tmp_output_dir / "legacy.pptx"

        builder = PPTBuilder()
        builder.build_ppt_report(report, "", str(out_path))
        assert out_path.exists()


# ═══════════════════════════════════════════════════
# PPT Builder — Bridge assets
# ═══════════════════════════════════════════════════

class TestPPTBuilderWithBridge:
    """PPT Builder with Bridge asset pages."""

    def test_image_asset_page_created(
        self, tmp_output_dir: Path, bridge_workspace: Path,
    ):
        (bridge_workspace / "00_img.png").write_bytes(_make_png_bytes())
        from dp_engine.report_builder.ppt_builder import PPTBuilder

        report = PPTReport(
            title="PPT with Image",
            slides=[PPTSlide(slide_title="S1", bullet_points=["Point."])],
        )
        out_path = tmp_output_dir / "with_image.pptx"
        asset = _make_image_asset(filename="00_img.png")

        builder = PPTBuilder()
        builder.build_ppt_report(
            report, "", str(out_path),
            bridge_workspace=bridge_workspace,
            bridge_assets=(asset,),
        )
        assert out_path.exists()
        from pptx import Presentation
        prs = Presentation(str(out_path))
        # 1 title + 1 content + 1 bridge image = 3
        assert len(prs.slides) == 3

    def test_text_asset_pages_created(
        self, tmp_output_dir: Path, bridge_workspace: Path,
    ):
        (bridge_workspace / "02_text.txt").write_text("content\n", encoding="utf-8")
        from dp_engine.report_builder.ppt_builder import PPTBuilder

        report = PPTReport(
            title="PPT with Text",
            slides=[PPTSlide(slide_title="S1", bullet_points=["Point."])],
        )
        out_path = tmp_output_dir / "with_text.pptx"
        asset = _make_text_asset(
            filename="02_text.txt",
            text="Line one\nLine two\nLine three",
        )

        builder = PPTBuilder()
        builder.build_ppt_report(
            report, "", str(out_path),
            bridge_workspace=bridge_workspace,
            bridge_assets=(asset,),
        )
        assert out_path.exists()

    def test_table_source_rejected_role_mismatch(
        self, tmp_output_dir: Path, bridge_workspace: Path,
    ):
        """RB-RES-06: TABLE_SOURCE rejected with role_mismatch for PPT."""
        (bridge_workspace / "01_data.csv").write_text("a,b\n1,2\n", encoding="utf-8")
        from dp_engine.report_builder.ppt_builder import PPTBuilder

        report = PPTReport(
            title="PPT with Table",
            slides=[PPTSlide(slide_title="S1", bullet_points=["Point."])],
        )
        out_path = tmp_output_dir / "should_not_exist.pptx"
        asset = _make_table_asset(filename="01_data.csv")

        builder = PPTBuilder()
        with pytest.raises(BridgeAdapterError) as exc:
            builder.build_ppt_report(
                report, "", str(out_path),
                bridge_workspace=bridge_workspace,
                bridge_assets=(asset,),
            )
        assert exc.value.code == "role_mismatch"
        # File should not be created
        assert not out_path.exists()

    def test_final_pptx_reopenable(
        self, tmp_output_dir: Path, bridge_workspace: Path,
    ):
        (bridge_workspace / "00_img.png").write_bytes(_make_png_bytes())
        from dp_engine.report_builder.ppt_builder import PPTBuilder

        report = PPTReport(
            title="Reopen PPT",
            slides=[PPTSlide(slide_title="S1", bullet_points=["Point."])],
        )
        out_path = tmp_output_dir / "reopen.pptx"
        asset = _make_image_asset(filename="00_img.png")

        builder = PPTBuilder()
        builder.build_ppt_report(
            report, "", str(out_path),
            bridge_workspace=bridge_workspace,
            bridge_assets=(asset,),
        )
        from pptx import Presentation
        prs = Presentation(str(out_path))
        assert prs is not None
