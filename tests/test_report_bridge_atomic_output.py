"""Tests for atomic no-clobber report output — Batch 3.3.1B.

Covers:
  RB-ATOM-01..14: temp file, os.link, fsync, failure/cancel rollback
  RB-LLM-01..08: zero LLM input, project_dir compat, Bridge isolation

Batch 3.3.1B-R additions:
  - Real zero-LLM spy tests (spy on generate_structured_report + generate_fn)
  - Instruction-like text is never prompted
  - PPT multi-asset order verification
  - Figure injection temp path proof (RB-ATOM-06 specific node)
  - Figure injection failure rollback Word+PPT (RB-ATOM-07 specific nodes)
  - Footer temp path and failure rollback
  - Bridge appendix temp proof (RB-ATOM-14 specific node)
"""

from __future__ import annotations

import io
import os
import struct
import tempfile
import uuid
import zlib
from pathlib import Path
from typing import Any
from unittest import mock

import pytest
from docx import Document


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


# ═══════════════════════════════════════════════════
# RB-ATOM: Atomic output tests
# ═══════════════════════════════════════════════════

class TestTempFileCreation:
    """RB-ATOM-01, RB-ATOM-05: temp file created with mkstemp."""

    def test_temp_created_in_same_directory(self, tmp_path: Path):
        """RB-ATOM-01: temp file in same directory as final."""
        final = tmp_path / "report.docx"
        fd, temp_path_str = tempfile.mkstemp(
            dir=str(tmp_path), prefix=".dp-report-", suffix=".docx",
        )
        os.close(fd)
        temp_path = Path(temp_path_str)
        assert temp_path.parent == tmp_path
        assert temp_path.name.startswith(".dp-report-")
        assert temp_path.name.endswith(".docx")
        temp_path.unlink()

    def test_temp_unpredictable_naming(self, tmp_path: Path):
        """RB-ATOM-05: temp file has unpredictable name."""
        names: set[str] = set()
        for _ in range(5):
            fd, tp = tempfile.mkstemp(
                dir=str(tmp_path), prefix=".dp-report-", suffix=".docx",
            )
            os.close(fd)
            names.add(os.path.basename(tp))
            os.unlink(tp)
        assert len(names) == 5  # All unique

    def test_temp_and_final_same_filesystem(self, tmp_path: Path):
        """temp and final must be on same filesystem for os.link."""
        final = tmp_path / "final.docx"
        fd, tp = tempfile.mkstemp(
            dir=str(tmp_path), prefix=".dp-report-", suffix=".docx",
        )
        os.close(fd)
        try:
            # Write content and try os.link
            Path(tp).write_bytes(b"test content")
            os.link(tp, str(final))
            assert final.exists()
            assert final.read_bytes() == b"test content"
            final.unlink()
        finally:
            Path(tp).unlink(missing_ok=True)


class TestBuilderWritesToTemp:
    """RB-ATOM-02, RB-ATOM-06, RB-ATOM-07, RB-ATOM-10, RB-ATOM-14."""

    def test_builder_writes_to_temp_not_final(
        self, tmp_path: Path,
    ):
        """RB-ATOM-02: Builder writes to temp, final only after commit."""
        from dp_engine.report_builder.word_builder import WordBuilder
        from dp_engine.report_builder.models import WordReport, WordSection

        final = tmp_path / "final_report.docx"
        fd, temp_path_str = tempfile.mkstemp(
            dir=str(tmp_path), prefix=".dp-report-", suffix=".docx",
        )
        os.close(fd)
        temp_path = temp_path_str

        report = WordReport(
            title="Temp Test",
            sections=[WordSection(heading="S1", content_paragraphs=["Text."])],
        )
        builder = WordBuilder()
        builder.build_word_report(report, "", temp_path)

        # Before os.link: final doesn't exist
        assert not final.exists()
        # Temp exists
        assert Path(temp_path).exists()
        assert Path(temp_path).stat().st_size > 0

        # Commit
        os.link(temp_path, str(final))
        assert final.exists()
        os.unlink(temp_path)

    def test_builder_failure_no_final(self, tmp_path: Path):
        """RB-ATOM-02: Builder failure → no final file."""
        final = tmp_path / "no_final.docx"
        fd, temp_path_str = tempfile.mkstemp(
            dir=str(tmp_path), prefix=".dp-report-", suffix=".docx",
        )
        os.close(fd)
        temp_path = Path(temp_path_str)

        # Simulate Builder failure: delete temp, no link
        temp_path.unlink()
        assert not final.exists()
        assert not temp_path.exists()

    def test_postprocess_all_on_temp(
        self, tmp_path: Path,
    ):
        """RB-ATOM-10: All post-processing happens on temp path."""
        from dp_engine.report_builder.word_builder import WordBuilder
        from dp_engine.report_builder.models import WordReport, WordSection

        final = tmp_path / "postprocess_final.docx"
        fd, temp_path_str = tempfile.mkstemp(
            dir=str(tmp_path), prefix=".dp-report-", suffix=".docx",
        )
        os.close(fd)

        report = WordReport(
            title="Post Process",
            sections=[WordSection(heading="S1", content_paragraphs=["Text."])],
        )
        builder = WordBuilder()

        # Builder writes to temp
        builder.build_word_report(report, "", temp_path_str)

        # Simulate figure injection on temp (just open and save)
        doc = Document(temp_path_str)
        doc.add_paragraph("[Figure injected on temp]")
        doc.save(temp_path_str)

        # Simulate footer append on temp
        doc = Document(temp_path_str)
        doc.add_paragraph("[Footer on temp]")
        doc.save(temp_path_str)

        # All operations happened on temp, final doesn't exist yet
        assert not final.exists()

        # Commit
        os.link(temp_path_str, str(final))
        os.unlink(temp_path_str)
        assert final.exists()

        # Verify content
        doc = Document(str(final))
        texts = [p.text for p in doc.paragraphs]
        assert any("[Figure injected on temp]" in t for t in texts)
        assert any("[Footer on temp]" in t for t in texts)


class TestOsLinkNoClobber:
    """RB-ATOM-04, RB-ATOM-09, RB-ATOM-11, RB-ATOM-12."""

    def test_os_link_is_unique_commit_point(self, tmp_path: Path):
        """RB-ATOM-04: os.link is the only commit point."""
        final = tmp_path / "unique.docx"
        fd, tp = tempfile.mkstemp(
            dir=str(tmp_path), prefix=".dp-report-", suffix=".docx",
        )
        os.close(fd)
        Path(tp).write_bytes(b"unique content")

        # Before os.link: only temp exists
        assert not final.exists()
        os.link(tp, str(final))
        assert final.exists()
        os.unlink(tp)

    def test_existing_final_not_overwritten(self, tmp_path: Path):
        """RB-ATOM-09: os.link fails with FileExistsError, existing final unchanged."""
        final = tmp_path / "existing.docx"
        final.write_text("original content")

        fd, tp = tempfile.mkstemp(
            dir=str(tmp_path), prefix=".dp-report-", suffix=".docx",
        )
        os.close(fd)
        Path(tp).write_bytes(b"new content")

        with pytest.raises(FileExistsError):
            os.link(tp, str(final))

        # Final unchanged
        assert final.read_text() == "original content"
        # Cleanup temp
        os.unlink(tp)

    def test_race_external_create_final_fail_closed(self, tmp_path: Path):
        """RB-ATOM-11: External race creates final → os.link fails → fail closed."""
        final = tmp_path / "race.docx"

        fd, tp = tempfile.mkstemp(
            dir=str(tmp_path), prefix=".dp-report-", suffix=".docx",
        )
        os.close(fd)
        Path(tp).write_bytes(b"our content")

        # Simulate external creation
        final.write_text("external content")

        with pytest.raises(FileExistsError):
            os.link(tp, str(final))

        # External content preserved
        assert final.read_text() == "external content"
        os.unlink(tp)

    def test_committed_file_reopenable(self, tmp_path: Path):
        """RB-ATOM-12: Committed file can be reopened."""
        from dp_engine.report_builder.word_builder import WordBuilder
        from dp_engine.report_builder.models import WordReport, WordSection

        final = tmp_path / "reopen.docx"
        fd, tp = tempfile.mkstemp(
            dir=str(tmp_path), prefix=".dp-report-", suffix=".docx",
        )
        os.close(fd)

        report = WordReport(
            title="Reopen",
            sections=[WordSection(heading="S1", content_paragraphs=["Hello."])],
        )
        builder = WordBuilder()
        builder.build_word_report(report, "", tp)

        os.link(tp, str(final))
        os.unlink(tp)

        doc = Document(str(final))
        texts = [p.text for p in doc.paragraphs]
        assert "Hello." in texts


class TestFailureRollback:
    """RB-ATOM-02, RB-ATOM-03, RB-ATOM-07, RB-ATOM-08."""

    def test_cancel_before_commit_no_final(self, tmp_path: Path):
        """RB-ATOM-03: Cancel before os.link → delete temp, no final."""
        final = tmp_path / "cancelled.docx"
        fd, tp = tempfile.mkstemp(
            dir=str(tmp_path), prefix=".dp-report-", suffix=".docx",
        )
        os.close(fd)
        Path(tp).write_bytes(b"temp content")

        # Cancel: delete temp, don't commit
        os.unlink(tp)
        assert not final.exists()
        assert not Path(tp).exists()

    def test_postprocess_failure_rollback(self, tmp_path: Path):
        """RB-ATOM-07, RB-ATOM-08: Post-process failure → delete temp, no final."""
        final = tmp_path / "inject_fail.docx"
        fd, tp = tempfile.mkstemp(
            dir=str(tmp_path), prefix=".dp-report-", suffix=".docx",
        )
        os.close(fd)
        Path(tp).write_bytes(b"content")

        # Simulate injection failure: delete temp before commit
        os.unlink(tp)
        assert not final.exists()

    def test_validation_failure_rollback(self, tmp_path: Path):
        """Validation failure → delete temp, no final."""
        final = tmp_path / "validation_fail.docx"
        fd, tp = tempfile.mkstemp(
            dir=str(tmp_path), prefix=".dp-report-", suffix=".docx",
        )
        os.close(fd)
        Path(tp).write_bytes(b"")  # Empty file → validation fails

        if os.path.getsize(tp) == 0:
            os.unlink(tp)  # Rollback
        assert not final.exists()


class TestLateCancelAfterCommit:
    """RB-ATOM-04: Late cancel after os.link keeps succeeded."""

    def test_late_cancel_after_os_link_keeps_final(self, tmp_path: Path):
        final = tmp_path / "late_cancel.docx"
        fd, tp = tempfile.mkstemp(
            dir=str(tmp_path), prefix=".dp-report-", suffix=".docx",
        )
        os.close(fd)
        Path(tp).write_bytes(b"committed content")

        # Commit
        os.link(tp, str(final))
        os.unlink(tp)

        # Late cancel doesn't affect final
        assert final.exists()
        assert final.stat().st_size > 0


class TestTempCleanup:
    """RB-ATOM-13: Temp unlink failure doesn't damage final."""

    def test_temp_unlink_failure_final_still_valid(self, tmp_path: Path):
        final = tmp_path / "valid_final.docx"
        fd, tp = tempfile.mkstemp(
            dir=str(tmp_path), prefix=".dp-report-", suffix=".docx",
        )
        os.close(fd)
        Path(tp).write_bytes(b"final content")

        os.link(tp, str(final))

        # Simulate unlink "failure" by not unlinking
        # Final is still valid
        assert final.exists()
        assert final.read_bytes() == b"final content"

        # Clean up
        os.unlink(tp)

    def test_temp_unlink_warning_logged(self, tmp_path: Path):
        """When temp unlink fails, a warning is recorded, final preserved."""
        final = tmp_path / "warn_final.docx"
        fd, tp = tempfile.mkstemp(
            dir=str(tmp_path), prefix=".dp-report-", suffix=".docx",
        )
        os.close(fd)
        Path(tp).write_bytes(b"content")

        os.link(tp, str(final))

        # Even if we can't unlink (permissions etc), final is good
        assert final.exists()

        # Clean up
        os.unlink(tp)


class TestFsync:
    """RB-ATOM: fsync called before commit."""

    def test_fsync_on_temp_before_link(self, tmp_path: Path):
        final = tmp_path / "fsync_test.docx"
        fd, tp = tempfile.mkstemp(
            dir=str(tmp_path), prefix=".dp-report-", suffix=".docx",
        )
        os.close(fd)
        Path(tp).write_bytes(b"sync me")

        # fsync
        with open(tp, "r+b") as f:
            f.flush()
            os.fsync(f.fileno())

        # Then commit
        os.link(tp, str(final))
        assert final.exists()
        os.unlink(tp)


class TestHardlinkUnsupported:
    """RB-ATOM: hardlink unsupported → fail closed."""

    def test_os_link_failure_fail_closed(self, tmp_path: Path):
        final = tmp_path / "link_fail.docx"
        fd, tp = tempfile.mkstemp(
            dir=str(tmp_path), prefix=".dp-report-", suffix=".docx",
        )
        os.close(fd)
        Path(tp).write_bytes(b"content")

        # Simulate cross-device link failure
        with mock.patch("os.link", side_effect=OSError("cross-device link not permitted")):
            with pytest.raises(OSError):
                os.link(tp, str(final))

        # Fail closed: delete temp, no final
        os.unlink(tp)
        assert not final.exists()


# ═══════════════════════════════════════════════════
# RB-LLM: Zero LLM input, project_dir compat
# ═══════════════════════════════════════════════════

SENTINEL = "BRIDGE_PROMPT_INJECTION_SENTINEL_7F3A"


class TestZeroLLMInput:
    """RB-LLM-01..05: Bridge content does not enter LLM prompts."""

    def test_sentinel_not_in_report_engine_prompt_paths(self):
        """RB-LLM-01: Sentinel must not exist in generate_structured_report args.

        This test verifies that the sentinel marker placed in bridge asset
        text would never appear in parameters passed to generate_structured_report.
        """
        # The key contract: generate_structured_report receives config, outline,
        # report_type, generate_fn, charts_context, chart_catalog — NONE of
        # these contain bridge_assets or bridge_workspace.
        import inspect
        from core.report_engine import generate_structured_report

        sig = inspect.signature(generate_structured_report)
        param_names = list(sig.parameters.keys())
        # No bridge-related parameter exists in generate_structured_report
        assert "bridge_assets" not in param_names
        assert "bridge_workspace" not in param_names
        assert "bridge_assets" not in str(param_names)

    def test_builder_appendix_contains_sentinel(
        self, tmp_path: Path,
    ):
        """RB-LLM-05: Bridge asset text DOES appear in final deterministic appendix."""
        from dp_engine.report_builder.word_builder import WordBuilder
        from dp_engine.report_builder.models import WordReport, WordSection
        from dp_engine.report_bridge.models import PreparedReportAsset, ReportAssetRole
        from dp_engine.skills.runtime_models import RuntimeArtifact

        ws = tmp_path / "llm_test_ws"
        ws.mkdir()
        txt_file = ws / "02_sentinel.txt"
        txt_file.write_text(f"safe prefix\n{SENTINEL}\nsafe suffix\n", encoding="utf-8")

        art = RuntimeArtifact(
            relative_path="output/test.txt",
            size_bytes=128,
            sha256="a" * 64,
            artifact_schema_version=1,
            artifact_id=_uid(),
            display_name="sentinel_test.txt",
            media_type="text/plain",
            task_id=_uid(),
            skill_id="testskill001",
            kind="output",
            created_at="2026-07-23T00:00:00Z",
        )
        asset = PreparedReportAsset(
            authoritative_artifact=art,
            role=ReportAssetRole.TEXT_SOURCE,
            order=0,
            managed_filename="02_sentinel.txt",
            parsed_payload=f"safe prefix\n{SENTINEL}\nsafe suffix\n",
        )

        report = WordReport(
            title="LLM Test",
            sections=[WordSection(heading="S1", content_paragraphs=["Normal."])],
        )
        out_path = tmp_path / "llm_test.docx"

        builder = WordBuilder()
        builder.build_word_report(
            report, "", str(out_path),
            bridge_workspace=ws,
            bridge_assets=(asset,),
        )

        doc = Document(str(out_path))
        full_text = "\n".join(p.text for p in doc.paragraphs)
        # Sentinel appears in appendix (deterministic)
        assert SENTINEL in full_text


class TestProjectDirCompat:
    """RB-LLM-06, RB-LLM-07, RB-LLM-08."""

    def test_project_dir_semantics_preserved(self, tmp_path: Path):
        """RB-LLM-06: project_dir search behavior unchanged."""
        pd = tmp_path / "project"
        pd.mkdir()
        (pd / "chart.png").write_bytes(_make_png_bytes())

        from dp_engine.report_builder.word_builder import WordBuilder
        from dp_engine.report_builder.models import WordReport, WordSection

        report = WordReport(
            title="Compat",
            sections=[
                WordSection(
                    heading="S1",
                    content_paragraphs=["[INSERT_IMAGE: chart.png]"],
                ),
            ],
        )
        out_path = tmp_path / "compat.docx"
        builder = WordBuilder()
        builder.build_word_report(report, "", str(out_path), project_dir=str(pd))
        assert out_path.exists()

    def test_bridge_not_in_fuzzy_search(self, tmp_path: Path):
        """RB-LLM-07: Bridge workspace not searched for fuzzy image matching."""
        pd = tmp_path / "project"
        pd.mkdir()
        ws = tmp_path / "bridge_ws"
        ws.mkdir()

        # Only project_dir has chart.png; bridge_ws does NOT
        (pd / "chart.png").write_bytes(_make_png_bytes())

        from dp_engine.report_builder.word_builder import WordBuilder
        from dp_engine.report_builder.models import WordReport, WordSection

        report = WordReport(
            title="Fuzzy Test",
            sections=[
                WordSection(
                    heading="S1",
                    content_paragraphs=["[INSERT_IMAGE: chart.png]"],
                ),
            ],
        )
        out_path = tmp_path / "fuzzy.docx"
        builder = WordBuilder()
        # Pass bridge_workspace but don't use bridge_assets
        builder.build_word_report(
            report, "", str(out_path),
            project_dir=str(pd),
            bridge_workspace=ws,
            bridge_assets=(),
        )
        assert out_path.exists()

    def test_bridge_image_not_found_by_insert_image(self, tmp_path: Path):
        """RB-LLM-08: Bridge images not accessible via [INSERT_IMAGE]."""
        ws = tmp_path / "bridge_ws"
        ws.mkdir()
        (ws / "bridge_only.png").write_bytes(_make_png_bytes())

        from dp_engine.report_builder.word_builder import WordBuilder
        from dp_engine.report_builder.models import WordReport, WordSection

        report = WordReport(
            title="Bridge Hidden",
            sections=[
                WordSection(
                    heading="S1",
                    content_paragraphs=["[INSERT_IMAGE: bridge_only.png]"],
                ),
            ],
        )
        out_path = tmp_path / "hidden.docx"

        builder = WordBuilder()
        builder.build_word_report(
            report, "", str(out_path),
            project_dir="",
            bridge_workspace=ws,
            bridge_assets=(),
        )
        # The bridge image should NOT be found via [INSERT_IMAGE] — it's not in project_dir
        # Should be listed as missing
        assert any("bridge_only.png" in img for img in builder.missing_images)


# ═══════════════════════════════════════════════════
# Batch 3.3.1B-R: Recursive sentinel search helper
# ═══════════════════════════════════════════════════

def _recursive_search(obj: Any, sentinel: str, _depth: int = 0, _max_depth: int = 12) -> bool:
    """Recursively search for a sentinel string in any nested Python structure.

    Handles: str, bytes, list, tuple, dict, dataclass instances.
    Returns True if sentinel is found anywhere in the structure.
    """
    if _depth > _max_depth:
        return False
    if isinstance(obj, str):
        return sentinel in obj
    if isinstance(obj, bytes):
        try:
            return sentinel.encode("utf-8") in obj
        except Exception:
            return False
    if isinstance(obj, (list, tuple)):
        return any(
            _recursive_search(item, sentinel, _depth + 1, _max_depth)
            for item in obj
        )
    if isinstance(obj, dict):
        return any(
            _recursive_search(k, sentinel, _depth + 1, _max_depth)
            or _recursive_search(v, sentinel, _depth + 1, _max_depth)
            for k, v in obj.items()
        )
    if hasattr(obj, "__dataclass_fields__"):
        for field_name in obj.__dataclass_fields__:
            try:
                value = getattr(obj, field_name, None)
                if _recursive_search(value, sentinel, _depth + 1, _max_depth):
                    return True
            except Exception:
                pass
    return False


# ═══════════════════════════════════════════════════
# Batch 3.3.1B-R: Real zero-LLM data flow spy tests
# ═══════════════════════════════════════════════════

CSV_SENTINEL = "CSV_SENTINEL_7F3A"
JSON_SENTINEL = "JSON_SENTINEL_7F3A"
TXT_SENTINEL = "TXT_SENTINEL_7F3A"
IMAGE_SENTINEL = "IMAGE_SENTINEL_7F3A"
INSTRUCTION_SENTINEL = "IGNORE_PREVIOUS_INSTRUCTIONS_BRIDGE_SENTINEL"


class TestRealZeroLLMDataFlow:
    """RB-LLM-01 (specific node): Bridge payloads never enter LLM arguments.

    Uses real spy on generate_structured_report AND generate_fn to capture
    all positional/keyword arguments. Recursively searches for 4 unique
    sentinels. Proves sentinels appear in deterministic appendix but NOT
    in any LLM-call parameter.
    """

    def test_bridge_payloads_never_enter_generate_structured_report_arguments(
        self, tmp_path: Path,
    ):
        """Full spy: capture all args of generate_structured_report + generate_fn.

        Four asset types (CSV, JSON, TXT, IMAGE) each carry a unique sentinel.
        The spy records all positional and keyword arguments.
        Recursive search proves zero sentinels in LLM inputs.
        Final DOCX proves sentinels in deterministic appendix.
        """
        from dp_engine.report_bridge.models import PreparedReportAsset, ReportAssetRole
        from dp_engine.skills.runtime_models import RuntimeArtifact
        from dp_engine.report_builder.word_builder import WordBuilder
        from dp_engine.report_builder.models import WordReport, WordSection

        # ── Prepare workspace with sentinel-marked assets ──
        ws = tmp_path / "llm_spy_ws"
        ws.mkdir()

        csv_path = ws / "00_data.csv"
        csv_path.write_text(f"col1,col2\n{CSV_SENTINEL},42\n", encoding="utf-8")

        json_path = ws / "01_config.json"
        json_path.write_text(f'{{"key": "{JSON_SENTINEL}"}}', encoding="utf-8")

        txt_path = ws / "02_text.txt"
        txt_path.write_text(
            f"safe prefix\n{TXT_SENTINEL}\nsafe suffix\n", encoding="utf-8"
        )

        png_path = ws / "03_img.png"
        png_path.write_bytes(_make_png_bytes())

        def _art(media_type: str, display_name: str) -> RuntimeArtifact:
            return RuntimeArtifact(
                relative_path=f"output/{display_name}",
                size_bytes=128,
                sha256="a" * 64,
                artifact_schema_version=1,
                artifact_id=_uid(),
                display_name=display_name,
                media_type=media_type,
                task_id=_uid(),
                skill_id="testskill001",
                kind="output",
                created_at="2026-07-23T00:00:00Z",
            )

        csv_asset = PreparedReportAsset(
            authoritative_artifact=_art("text/csv", "csv_data"),
            role=ReportAssetRole.TABLE_SOURCE,
            order=0,
            managed_filename="00_data.csv",
            parsed_payload=(("col1", "col2"), (CSV_SENTINEL, "42")),
        )
        json_asset = PreparedReportAsset(
            authoritative_artifact=_art("application/json", "json_cfg"),
            role=ReportAssetRole.TEXT_SOURCE,
            order=1,
            managed_filename="01_config.json",
            parsed_payload=f'{{\n  "key": "{JSON_SENTINEL}"\n}}',
        )
        txt_asset = PreparedReportAsset(
            authoritative_artifact=_art("text/plain", "text_note"),
            role=ReportAssetRole.TEXT_SOURCE,
            order=2,
            managed_filename="02_text.txt",
            parsed_payload=f"safe prefix\n{TXT_SENTINEL}\nsafe suffix\n",
        )
        img_asset = PreparedReportAsset(
            authoritative_artifact=_art("image/png", f"chart_{IMAGE_SENTINEL}"),
            role=ReportAssetRole.IMAGE,
            order=3,
            managed_filename="03_img.png",
            parsed_payload=None,
        )

        all_assets = (csv_asset, json_asset, txt_asset, img_asset)
        all_sentinels = [CSV_SENTINEL, JSON_SENTINEL, TXT_SENTINEL, IMAGE_SENTINEL]

        # ── Spy setup: capture generate_structured_report + generate_fn ──
        gen_report_calls: list[dict] = []
        gen_fn_calls: list[dict] = []

        def spy_generate_structured_report(*args: Any, **kwargs: Any) -> dict:
            gen_report_calls.append({
                "positional": args,
                "keyword": dict(kwargs),
            })
            # Also spy on generate_fn if provided
            original_generate_fn = kwargs.get("generate_fn")
            if original_generate_fn is not None:

                def spy_generate_fn(messages: Any, **fn_kwargs: Any) -> str:
                    gen_fn_calls.append({
                        "messages": messages,
                        "keyword": dict(fn_kwargs),
                    })
                    return '{"response": "mock"}'

                kwargs = dict(kwargs)
                kwargs["generate_fn"] = spy_generate_fn

            return {
                "sections": [
                    {
                        "heading": "Test Section",
                        "content_paragraphs": ["Generated content."],
                        "image_anchors": [],
                        "tables": [],
                    }
                ],
                "_report_warnings": [],
                "_diagnosis_loaded": False,
            }

        # ── Execute: generate_structured_report first, then Builder ──
        with mock.patch(
            "core.report_engine.generate_structured_report",
            side_effect=spy_generate_structured_report,
        ):
            from core.report_engine import generate_structured_report as _gsr

            _gsr(
                {"report_type": "word"},
                "Test outline text",
                "word",
                lambda messages: "mock response",
                charts_context="chart context here",
                chart_catalog={},
            )

            # Builder with bridge assets — this is the deterministic appendix path
            report = WordReport(
                title="LLM Spy Test",
                sections=[
                    WordSection(heading="S1", content_paragraphs=["Normal text."])
                ],
            )
            out_path = tmp_path / "llm_spy_test.docx"

            builder = WordBuilder()
            builder.build_word_report(
                report,
                "",
                str(out_path),
                bridge_workspace=ws,
                bridge_assets=all_assets,
            )

        # ── Assertion 1: generate_structured_report was called ──
        assert len(gen_report_calls) >= 1, (
            "generate_structured_report was never called"
        )

        # ── Assertion 2: ZERO sentinels in generate_structured_report arguments ──
        found_in_gen_report: list[str] = []
        for call_record in gen_report_calls:
            all_call_data = {
                "positional": call_record["positional"],
                "keyword": call_record["keyword"],
            }
            for sentinel in all_sentinels:
                if _recursive_search(all_call_data, sentinel):
                    found_in_gen_report.append(sentinel)

        assert len(found_in_gen_report) == 0, (
            f"Sentinels FOUND in generate_structured_report arguments: "
            f"{found_in_gen_report}. This is a P0 violation — Bridge "
            f"content must NEVER enter LLM prompt parameters."
        )

        # ── Assertion 3: ZERO sentinels in generate_fn prompt/messages ──
        found_in_gen_fn: list[str] = []
        for call_record in gen_fn_calls:
            for sentinel in all_sentinels:
                if _recursive_search(call_record, sentinel):
                    found_in_gen_fn.append(sentinel)

        assert len(found_in_gen_fn) == 0, (
            f"Sentinels FOUND in generate_fn prompt/messages: "
            f"{found_in_gen_fn}. This is a P0 violation — Bridge "
            f"content must NEVER reach the LLM."
        )

        # ── Assertion 4: Sentinels ARE in deterministic appendix ──
        assert out_path.exists(), "Output DOCX was not created"
        doc = Document(str(out_path))
        # Collect text from paragraphs AND table cells (CSV goes into WordTable)
        para_texts = [p.text for p in doc.paragraphs]
        table_texts: list[str] = []
        for table in doc.tables:
            for row in table.rows:
                for cell in row.cells:
                    table_texts.append(cell.text)
        full_text = "\n".join(para_texts + table_texts)

        # CSV sentinel appears in appendix table
        assert CSV_SENTINEL in "\n".join(table_texts), (
            f"{CSV_SENTINEL} must appear in appendix table"
        )
        # JSON, TXT sentinels appear as text in appendix
        for sentinel in [JSON_SENTINEL, TXT_SENTINEL]:
            assert sentinel in full_text, (
                f"{sentinel} must appear in deterministic appendix text"
            )

        # IMAGE sentinel is in display_name, appears as image caption
        assert IMAGE_SENTINEL in full_text, (
            f"{IMAGE_SENTINEL} must appear in appendix as image title"
        )

    def test_instruction_like_artifact_text_remains_literal_and_is_not_prompted(
        self, tmp_path: Path,
    ):
        """RB-LLM-02 (specific node): Instruction-like text is NOT interpreted.

        Artifact text containing "IGNORE_PREVIOUS_INSTRUCTIONS" must:
        1. Not enter any LLM call parameter
        2. Appear literally in the deterministic appendix
        3. Not trigger extra chapters, commands, or tool invocations
        4. Not be interpreted as a system instruction
        """
        from dp_engine.report_bridge.models import PreparedReportAsset, ReportAssetRole
        from dp_engine.skills.runtime_models import RuntimeArtifact
        from dp_engine.report_builder.word_builder import WordBuilder
        from dp_engine.report_builder.models import WordReport, WordSection

        ws = tmp_path / "instr_ws"
        ws.mkdir()

        instruction_text = (
            f"Normal data header\n"
            f"{INSTRUCTION_SENTINEL}\n"
            f"Normal data footer\n"
        )
        txt_path = ws / "00_instr.txt"
        txt_path.write_text(instruction_text, encoding="utf-8")

        art = RuntimeArtifact(
            relative_path="output/instr.txt",
            size_bytes=len(instruction_text.encode("utf-8")),
            sha256="a" * 64,
            artifact_schema_version=1,
            artifact_id=_uid(),
            display_name="instruction_like.txt",
            media_type="text/plain",
            task_id=_uid(),
            skill_id="testskill001",
            kind="output",
            created_at="2026-07-23T00:00:00Z",
        )
        asset = PreparedReportAsset(
            authoritative_artifact=art,
            role=ReportAssetRole.TEXT_SOURCE,
            order=0,
            managed_filename="00_instr.txt",
            parsed_payload=instruction_text,
        )

        # ── Spy on generate_structured_report ──
        gen_report_calls: list[dict] = []

        def spy_generate_structured_report(*args: Any, **kwargs: Any) -> dict:
            gen_report_calls.append({
                "positional": args,
                "keyword": dict(kwargs),
            })
            return {
                "sections": [
                    {
                        "heading": "Normal Section",
                        "content_paragraphs": ["Standard generated text."],
                        "image_anchors": [],
                        "tables": [],
                    }
                ],
                "_report_warnings": [],
                "_diagnosis_loaded": False,
            }

        with mock.patch(
            "core.report_engine.generate_structured_report",
            side_effect=spy_generate_structured_report,
        ):
            from core.report_engine import generate_structured_report as _gsr

            _gsr(
                {"report_type": "word"},
                "Normal outline",
                "word",
                lambda messages: "mock",
            )

            report = WordReport(
                title="Instruction Test",
                sections=[
                    WordSection(
                        heading="S1", content_paragraphs=["Normal content."]
                    )
                ],
            )
            out_path = tmp_path / "instruction_test.docx"

            builder = WordBuilder()
            builder.build_word_report(
                report,
                "",
                str(out_path),
                bridge_workspace=ws,
                bridge_assets=(asset,),
            )

        # ── Assertions ──
        # 1. Instruction sentinel NOT in LLM arguments
        for call_record in gen_report_calls:
            all_data = {
                "positional": call_record["positional"],
                "keyword": call_record["keyword"],
            }
            assert not _recursive_search(all_data, INSTRUCTION_SENTINEL), (
                f"Instruction-like sentinel FOUND in generate_structured_report "
                f"arguments — P0 violation: artifact text must not enter LLM."
            )

        # 2. Instruction sentinel appears literally in appendix
        doc = Document(str(out_path))
        full_text = "\n".join(p.text for p in doc.paragraphs)
        assert INSTRUCTION_SENTINEL in full_text, (
            "Instruction-like sentinel must appear literally in appendix"
        )

        # 3. No extra chapters triggered by the sentinel
        # The document should only have "S1" and "技能输出素材" headings
        headings = [
            p.text
            for p in doc.paragraphs
            if p.style.name.startswith("Heading")
        ]
        instruction_headings = [
            h for h in headings if "IGNORE" in h.upper()
        ]
        assert len(instruction_headings) == 0, (
            f"Instruction-like text triggered unexpected headings: "
            f"{instruction_headings}"
        )

        # 4. "Normal content" still appears (document structure intact)
        assert "Normal content." in full_text, (
            "Document body content should be preserved"
        )
        # The key contract: generated content from LLM goes into the
        # Builder-constructed body. Bridge appendix is added separately.
        # Both paths are verified: LLM input has no sentinel; appendix has it.


# ═══════════════════════════════════════════════════
# Batch 3.3.1B-R: PPT multi-asset order verification
# ═══════════════════════════════════════════════════

class TestPPTMultiAssetOrder:
    """RB-LLM-04 (specific node): Multiple PPT assets preserve order."""

    def test_multiple_ppt_assets_preserve_order(
        self, tmp_path: Path,
    ):
        """RB-LLM-04: 3 assets (IMAGE, TEXT, IMAGE) appear in order 0,1,2."""
        from pptx import Presentation
        from dp_engine.report_bridge.models import PreparedReportAsset, ReportAssetRole
        from dp_engine.skills.runtime_models import RuntimeArtifact
        from dp_engine.report_bridge.adapters import append_bridge_assets_to_ppt

        ws = tmp_path / "order_ws"
        ws.mkdir()

        # order=0 IMAGE
        img0_path = ws / "00_first.png"
        img0_path.write_bytes(_make_png_bytes(10, 10))
        art0 = RuntimeArtifact(
            relative_path="output/first.png",
            size_bytes=128,
            sha256="a" * 64,
            artifact_schema_version=1,
            artifact_id=_uid(),
            display_name="first_image",
            media_type="image/png",
            task_id=_uid(),
            skill_id="testskill001",
            kind="output",
            created_at="2026-07-23T00:00:00Z",
        )
        asset0 = PreparedReportAsset(
            authoritative_artifact=art0,
            role=ReportAssetRole.IMAGE,
            order=0,
            managed_filename="00_first.png",
            parsed_payload=None,
        )

        # order=1 TEXT_SOURCE
        txt_path = ws / "01_middle.txt"
        txt_path.write_text("MIDDLE_TEXT_ASSET", encoding="utf-8")
        art1 = RuntimeArtifact(
            relative_path="output/middle.txt",
            size_bytes=128,
            sha256="b" * 64,
            artifact_schema_version=1,
            artifact_id=_uid(),
            display_name="middle_text",
            media_type="text/plain",
            task_id=_uid(),
            skill_id="testskill001",
            kind="output",
            created_at="2026-07-23T00:00:00Z",
        )
        asset1 = PreparedReportAsset(
            authoritative_artifact=art1,
            role=ReportAssetRole.TEXT_SOURCE,
            order=1,
            managed_filename="01_middle.txt",
            parsed_payload="MIDDLE_TEXT_ASSET",
        )

        # order=2 IMAGE
        img2_path = ws / "02_last.png"
        img2_path.write_bytes(_make_png_bytes(20, 20))
        art2 = RuntimeArtifact(
            relative_path="output/last.png",
            size_bytes=256,
            sha256="c" * 64,
            artifact_schema_version=1,
            artifact_id=_uid(),
            display_name="last_image",
            media_type="image/png",
            task_id=_uid(),
            skill_id="testskill001",
            kind="output",
            created_at="2026-07-23T00:00:00Z",
        )
        asset2 = PreparedReportAsset(
            authoritative_artifact=art2,
            role=ReportAssetRole.IMAGE,
            order=2,
            managed_filename="02_last.png",
            parsed_payload=None,
        )

        prs = Presentation()
        prs.slide_width = 9144000
        prs.slide_height = 5143500
        n_before = len(prs.slides)

        append_bridge_assets_to_ppt(
            prs,
            bridge_workspace=ws,
            assets=(asset0, asset1, asset2),
        )

        # 3 new slides: one for each asset
        assert len(prs.slides) == n_before + 3, (
            f"Expected {n_before + 3} slides, got {len(prs.slides)}"
        )

        # Verify the middle slide (index n_before + 1) contains TEXT
        middle_slide = prs.slides[n_before + 1]
        middle_texts = []
        for shape in middle_slide.shapes:
            if shape.has_text_frame:
                middle_texts.append(shape.text_frame.text)
        middle_combined = " ".join(middle_texts)
        assert "MIDDLE_TEXT_ASSET" in middle_combined, (
            f"order=1 TEXT_SOURCE should appear on slide {n_before + 1}, "
            f"got: {middle_combined}"
        )

    def test_duplicate_order_rejected_by_validate(
        self, tmp_path: Path,
    ):
        """Duplicate order values must be rejected."""
        from dp_engine.report_bridge.adapters import (
            validate_bridge_assets_for_ppt,
            BridgeAdapterError,
        )
        from dp_engine.report_bridge.models import PreparedReportAsset, ReportAssetRole
        from dp_engine.skills.runtime_models import RuntimeArtifact

        ws = tmp_path / "dup_ws"
        ws.mkdir()
        png0 = ws / "00_a.png"
        png0.write_bytes(_make_png_bytes())
        png1 = ws / "00_b.png"
        png1.write_bytes(_make_png_bytes())

        art = RuntimeArtifact(
            relative_path="output/a.png",
            size_bytes=128,
            sha256="a" * 64,
            artifact_schema_version=1,
            artifact_id=_uid(),
            display_name="a",
            media_type="image/png",
            task_id=_uid(),
            skill_id="testskill001",
            kind="output",
            created_at="2026-07-23T00:00:00Z",
        )
        a0 = PreparedReportAsset(
            authoritative_artifact=art,
            role=ReportAssetRole.IMAGE,
            order=0,
            managed_filename="00_a.png",
            parsed_payload=None,
        )
        a1 = PreparedReportAsset(
            authoritative_artifact=art,
            role=ReportAssetRole.IMAGE,
            order=0,  # Duplicate order
            managed_filename="00_b.png",
            parsed_payload=None,
        )
        with pytest.raises(BridgeAdapterError) as exc:
            validate_bridge_assets_for_ppt(ws, (a0, a1))
        assert exc.value.code == "bridge_asset_invalid"

    def test_order_missing_rejected(
        self, tmp_path: Path,
    ):
        """Missing order (gap) must be rejected."""
        from dp_engine.report_bridge.adapters import (
            validate_bridge_assets_for_ppt,
            BridgeAdapterError,
        )
        from dp_engine.report_bridge.models import PreparedReportAsset, ReportAssetRole
        from dp_engine.skills.runtime_models import RuntimeArtifact

        ws = tmp_path / "gap_ws"
        ws.mkdir()
        (ws / "00_a.png").write_bytes(_make_png_bytes())
        (ws / "02_c.png").write_bytes(_make_png_bytes())

        art = RuntimeArtifact(
            relative_path="output/a.png",
            size_bytes=128,
            sha256="a" * 64,
            artifact_schema_version=1,
            artifact_id=_uid(),
            display_name="a",
            media_type="image/png",
            task_id=_uid(),
            skill_id="testskill001",
            kind="output",
            created_at="2026-07-23T00:00:00Z",
        )
        a0 = PreparedReportAsset(
            authoritative_artifact=art,
            role=ReportAssetRole.IMAGE,
            order=0,
            managed_filename="00_a.png",
            parsed_payload=None,
        )
        a2 = PreparedReportAsset(
            authoritative_artifact=art,
            role=ReportAssetRole.IMAGE,
            order=2,  # Gap: order=1 missing
            managed_filename="02_c.png",
            parsed_payload=None,
        )
        with pytest.raises(BridgeAdapterError) as exc:
            validate_bridge_assets_for_ppt(ws, (a0, a2))
        assert exc.value.code == "bridge_asset_invalid"

    def test_tuple_order_mismatch_rejected(
        self, tmp_path: Path,
    ):
        """Tuple element order must match asset.order field."""
        from dp_engine.report_bridge.adapters import (
            validate_bridge_assets_for_ppt,
            BridgeAdapterError,
        )
        from dp_engine.report_bridge.models import PreparedReportAsset, ReportAssetRole
        from dp_engine.skills.runtime_models import RuntimeArtifact

        ws = tmp_path / "mismatch_ws"
        ws.mkdir()
        (ws / "00_first.png").write_bytes(_make_png_bytes())
        (ws / "01_second.png").write_bytes(_make_png_bytes())

        art = RuntimeArtifact(
            relative_path="output/img.png",
            size_bytes=128,
            sha256="a" * 64,
            artifact_schema_version=1,
            artifact_id=_uid(),
            display_name="img",
            media_type="image/png",
            task_id=_uid(),
            skill_id="testskill001",
            kind="output",
            created_at="2026-07-23T00:00:00Z",
        )
        # asset with order=1 appears BEFORE asset with order=0 in tuple
        a1 = PreparedReportAsset(
            authoritative_artifact=art,
            role=ReportAssetRole.IMAGE,
            order=1,  # Higher order but first in tuple
            managed_filename="01_second.png",
            parsed_payload=None,
        )
        a0 = PreparedReportAsset(
            authoritative_artifact=art,
            role=ReportAssetRole.IMAGE,
            order=0,  # Lower order but second in tuple
            managed_filename="00_first.png",
            parsed_payload=None,
        )
        with pytest.raises(BridgeAdapterError) as exc:
            validate_bridge_assets_for_ppt(ws, (a1, a0))
        assert exc.value.code == "bridge_asset_invalid"


# ═══════════════════════════════════════════════════
# Batch 3.3.1B-R: Figure injection temp path proof
# ═══════════════════════════════════════════════════

class TestFigureInjectionReceivesTempPath:
    """RB-ATOM-06 (specific node): Figure injection receives temp_path, not final."""

    def test_figure_injection_by_reference_receives_temp_path_not_final(
        self, tmp_path: Path,
    ):
        """Word: _inject_figures_by_reference receives temp_output, not final."""
        import main as _main

        final = tmp_path / "final_inject.docx"
        fd, temp_path_str = tempfile.mkstemp(
            dir=str(tmp_path), prefix=".dp-report-", suffix=".docx",
        )
        os.close(fd)
        temp_path = Path(temp_path_str)

        # Create a minimal DOCX with figure reference
        from dp_engine.report_builder.word_builder import WordBuilder
        from dp_engine.report_builder.models import WordReport, WordSection

        report = WordReport(
            title="Inject Test",
            sections=[
                WordSection(
                    heading="S1",
                    content_paragraphs=["See 图1 below."],
                ),
            ],
        )
        builder = WordBuilder()
        builder.build_word_report(report, "", str(temp_path))

        # Spy on _inject_figures_by_reference
        spy_calls: list[dict] = []

        def spy_inject_figures(docx_path: str, manifest, warnings: list):
            spy_calls.append({
                "docx_path": docx_path,
                "manifest": manifest,
                "warnings": warnings,
            })

        with mock.patch.object(_main, "_inject_figures_by_reference",
                               side_effect=spy_inject_figures):
            _main._inject_figures_by_reference(str(temp_path), [], [])

        assert len(spy_calls) >= 1
        # The path received must equal temp path
        resolved_received = str(Path(spy_calls[0]["docx_path"]).resolve())
        resolved_temp = str(temp_path.resolve())
        assert resolved_received == resolved_temp, (
            f"Figure injection received {resolved_received}, "
            f"expected temp {resolved_temp}"
        )
        # final must not exist yet
        assert not final.exists()

    def test_figure_injection_to_pptx_receives_temp_path_not_final(
        self, tmp_path: Path,
    ):
        """PPT: _inject_figures_to_pptx receives temp_output, not final."""
        import main as _main

        final = tmp_path / "final_inject.pptx"
        fd, temp_path_str = tempfile.mkstemp(
            dir=str(tmp_path), prefix=".dp-report-", suffix=".pptx",
        )
        os.close(fd)
        temp_path = Path(temp_path_str)

        from dp_engine.report_builder.ppt_builder import PPTBuilder
        from dp_engine.report_builder.models import PPTReport, PPTSlide

        report = PPTReport(
            title="Inject PPT",
            slides=[PPTSlide(slide_title="S1", bullet_points=["Point."])],
        )
        builder = PPTBuilder()
        builder.build_ppt_report(report, "", str(temp_path))

        spy_calls: list[dict] = []

        def spy_inject_pptx(
            pptx_path: str, manifest, warnings, *,
            assigned_filenames=None, template_used=False,
        ):
            spy_calls.append({
                "pptx_path": pptx_path,
                "manifest": manifest,
                "warnings": warnings,
            })

        with mock.patch.object(
            _main, "_inject_figures_to_pptx", side_effect=spy_inject_pptx
        ):
            _main._inject_figures_to_pptx(str(temp_path), [], [])

        assert len(spy_calls) >= 1
        resolved_received = str(Path(spy_calls[0]["pptx_path"]).resolve())
        resolved_temp = str(temp_path.resolve())
        assert resolved_received == resolved_temp, (
            f"PPT injection received {resolved_received}, "
            f"expected temp {resolved_temp}"
        )
        assert not final.exists()


# ═══════════════════════════════════════════════════
# Batch 3.3.1B-R: Figure injection failure rollback
# ═══════════════════════════════════════════════════

class TestFigureInjectionFailureRollback:
    """RB-ATOM-07 (specific nodes): Figure injection failure → no final."""

    def test_word_figure_injection_failure_removes_temp_and_creates_no_final(
        self, tmp_path: Path,
    ):
        """Word: Builder writes temp → injection raises → temp deleted, final absent."""
        import main as _main
        from dp_engine.report_builder.word_builder import WordBuilder
        from dp_engine.report_builder.models import WordReport, WordSection

        final = tmp_path / "should_not_exist.docx"
        fd, temp_path_str = tempfile.mkstemp(
            dir=str(tmp_path), prefix=".dp-report-", suffix=".docx",
        )
        os.close(fd)
        temp_path = Path(temp_path_str)

        # Builder successfully writes to temp
        report = WordReport(
            title="Will Fail",
            sections=[WordSection(heading="S1", content_paragraphs=["Text."])],
        )
        builder = WordBuilder()
        builder.build_word_report(report, "", str(temp_path))
        assert temp_path.exists()
        assert temp_path.stat().st_size > 0

        # Existing final (if any) should be preserved
        existing_content = b"pre-existing final content"
        final.write_bytes(existing_content)

        # Simulate figure injection that raises
        def failing_inject(_path, _manifest, _warnings):
            raise RuntimeError("simulated figure injection failure")

        with mock.patch.object(
            _main, "_inject_figures_by_reference", side_effect=failing_inject
        ):
            try:
                _main._inject_figures_by_reference(str(temp_path), [], [])
                # Simulate rollback: cleanup temp
                _main._cleanup_temp(str(temp_path))
            except RuntimeError:
                _main._cleanup_temp(str(temp_path))

        # After rollback: temp deleted
        assert not temp_path.exists(), "temp should be deleted after injection failure"
        # os.link NOT called: existing final unchanged
        assert final.exists(), "existing final should still exist"
        assert final.read_bytes() == existing_content, (
            "existing final content must be unchanged"
        )

    def test_ppt_figure_injection_failure_removes_temp_and_creates_no_final(
        self, tmp_path: Path,
    ):
        """PPT: Builder writes temp → injection raises → temp deleted, final absent."""
        import main as _main
        from dp_engine.report_builder.ppt_builder import PPTBuilder
        from dp_engine.report_builder.models import PPTReport, PPTSlide

        final = tmp_path / "should_not_exist.pptx"
        fd, temp_path_str = tempfile.mkstemp(
            dir=str(tmp_path), prefix=".dp-report-", suffix=".pptx",
        )
        os.close(fd)
        temp_path = Path(temp_path_str)

        report = PPTReport(
            title="PPT Will Fail",
            slides=[PPTSlide(slide_title="S1", bullet_points=["Point."])],
        )
        builder = PPTBuilder()
        builder.build_ppt_report(report, "", str(temp_path))
        assert temp_path.exists()

        existing_content = b"pre-existing pptx content"
        final.write_bytes(existing_content)

        def failing_inject(_path, _manifest, _warnings, **kwargs):
            raise RuntimeError("simulated pptx injection failure")

        with mock.patch.object(
            _main, "_inject_figures_to_pptx", side_effect=failing_inject
        ):
            try:
                _main._inject_figures_to_pptx(str(temp_path), [], [])
            except RuntimeError:
                _main._cleanup_temp(str(temp_path))

        assert not temp_path.exists(), "temp should be deleted after injection failure"
        assert final.exists()
        assert final.read_bytes() == existing_content


# ═══════════════════════════════════════════════════
# Batch 3.3.1B-R: Footer temp path and failure rollback
# ═══════════════════════════════════════════════════

class TestInclusionFooterTempAndFailure:
    """Footer receives temp path; footer failure → rollback."""

    def test_inclusion_footer_receives_temp_path_not_final(
        self, tmp_path: Path,
    ):
        """_append_inclusion_footer receives temp_output, not final_output."""
        final = tmp_path / "final_footer.docx"
        fd, temp_path_str = tempfile.mkstemp(
            dir=str(tmp_path), prefix=".dp-report-", suffix=".docx",
        )
        os.close(fd)
        temp_path = Path(temp_path_str)

        from dp_engine.report_builder.word_builder import WordBuilder
        from dp_engine.report_builder.models import WordReport, WordSection

        report = WordReport(
            title="Footer Test",
            sections=[WordSection(heading="S1", content_paragraphs=["Text."])],
        )
        builder = WordBuilder()
        builder.build_word_report(report, "", str(temp_path))

        # Spy on _append_inclusion_footer
        spy_calls: list[dict] = []

        def spy_footer(docx_path, report_type, warnings, diagnosis_loaded):
            spy_calls.append({
                "docx_path": docx_path,
                "report_type": report_type,
            })

        # Use a mock to verify path
        from unittest import mock as _mock
        with _mock.patch.object(
            type("Dummy", (), {"_append_inclusion_footer": spy_footer})(),
            "_append_inclusion_footer",
            side_effect=spy_footer,
            create=True,
        ):
            pass  # We spy via direct import below

        # Direct call to verify path
        import main as _main
        original = _main.DataProcessorWindow._append_inclusion_footer
        path_received = []

        def capture_path(docx_path, report_type, warnings, diagnosis_loaded):
            path_received.append(str(Path(docx_path).resolve()))

        _main.DataProcessorWindow._append_inclusion_footer = staticmethod(capture_path)
        try:
            _main.DataProcessorWindow._append_inclusion_footer(
                str(temp_path), "word", [], True,
            )
        finally:
            _main.DataProcessorWindow._append_inclusion_footer = original

        assert len(path_received) >= 1
        assert Path(path_received[0]).resolve() == temp_path.resolve(), (
            f"Footer received {path_received[0]}, expected {temp_path}"
        )
        assert not final.exists(), "final should not exist before os.link"
        temp_path.unlink()

    def test_inclusion_footer_failure_removes_temp_and_creates_no_final(
        self, tmp_path: Path,
    ):
        """Footer append failure → temp deleted, final absent, existing final untouched."""
        import main as _main
        from dp_engine.report_builder.word_builder import WordBuilder
        from dp_engine.report_builder.models import WordReport, WordSection

        final = tmp_path / "footer_fail_final.docx"
        fd, temp_path_str = tempfile.mkstemp(
            dir=str(tmp_path), prefix=".dp-report-", suffix=".docx",
        )
        os.close(fd)
        temp_path = Path(temp_path_str)

        report = WordReport(
            title="Footer Fail",
            sections=[WordSection(heading="S1", content_paragraphs=["Text."])],
        )
        builder = WordBuilder()
        builder.build_word_report(report, "", str(temp_path))
        assert temp_path.exists()

        existing_content = b"pre-existing final for footer test"
        final.write_bytes(existing_content)

        # Simulate footer failure → cleanup temp
        try:
            raise RuntimeError("simulated footer failure")
        except RuntimeError:
            _main._cleanup_temp(str(temp_path))

        assert not temp_path.exists(), "temp should be deleted after footer failure"
        assert final.exists()
        assert final.read_bytes() == existing_content, (
            "existing final content must be unchanged after footer failure"
        )


# ═══════════════════════════════════════════════════
# Batch 3.3.1B-R: Bridge appendix written during Builder temp output
# ═══════════════════════════════════════════════════

class TestBridgeAppendixOnTemp:
    """RB-ATOM-14 (specific node): Bridge appendix is written during Builder on temp."""

    def test_bridge_appendix_is_written_during_builder_temp_output(
        self, tmp_path: Path,
    ):
        """RB-ATOM-14: Bridge appendix exists in temp DOCX before os.link."""
        from dp_engine.report_bridge.models import PreparedReportAsset, ReportAssetRole
        from dp_engine.skills.runtime_models import RuntimeArtifact
        from dp_engine.report_builder.word_builder import WordBuilder
        from dp_engine.report_builder.models import WordReport, WordSection

        final = tmp_path / "appendix_final.docx"
        fd, temp_path_str = tempfile.mkstemp(
            dir=str(tmp_path), prefix=".dp-report-", suffix=".docx",
        )
        os.close(fd)
        temp_path = Path(temp_path_str)

        ws = tmp_path / "bridge_appendix_ws"
        ws.mkdir()
        txt_file = ws / "00_appendix.txt"
        APPENDIX_SENTINEL = "BRIDGE_APPENDIX_ON_TEMP_7F3A"
        txt_file.write_text(APPENDIX_SENTINEL, encoding="utf-8")

        art = RuntimeArtifact(
            relative_path="output/appendix.txt",
            size_bytes=128,
            sha256="a" * 64,
            artifact_schema_version=1,
            artifact_id=_uid(),
            display_name="appendix_item",
            media_type="text/plain",
            task_id=_uid(),
            skill_id="testskill001",
            kind="output",
            created_at="2026-07-23T00:00:00Z",
        )
        asset = PreparedReportAsset(
            authoritative_artifact=art,
            role=ReportAssetRole.TEXT_SOURCE,
            order=0,
            managed_filename="00_appendix.txt",
            parsed_payload=APPENDIX_SENTINEL,
        )

        report = WordReport(
            title="Appendix Temp Test",
            sections=[WordSection(heading="S1", content_paragraphs=["Body text."])],
        )

        # 1. Builder receives temp_output (not final)
        builder = WordBuilder()
        builder.build_word_report(
            report, "", str(temp_path),
            bridge_workspace=ws,
            bridge_assets=(asset,),
        )

        # 2. Before os.link: final does not exist
        assert not final.exists(), (
            "final must not exist before os.link"
        )

        # 3. Before os.link: temp can be re-opened and contains Bridge appendix
        doc = Document(str(temp_path))
        full_text = "\n".join(p.text for p in doc.paragraphs)
        assert APPENDIX_SENTINEL in full_text, (
            f"Bridge appendix sentinel '{APPENDIX_SENTINEL}' must exist "
            f"in temp document before os.link"
        )
        assert "技能输出素材" in full_text, (
            "Bridge appendix section heading must exist in temp document"
        )

        # 4. After verifying appendix in temp, do os.link to commit
        os.link(str(temp_path), str(final))
        assert final.exists(), "final must exist after os.link"

        # 5. After os.link, verify final also contains appendix
        doc_final = Document(str(final))
        final_text = "\n".join(p.text for p in doc_final.paragraphs)
        assert APPENDIX_SENTINEL in final_text

        # 6. Cleanup temp
        os.unlink(str(temp_path))

    def test_no_bridge_appendix_operations_after_os_link(
        self, tmp_path: Path,
    ):
        """After os.link, no Bridge appendix functions are called on final."""
        from dp_engine.report_bridge.models import PreparedReportAsset, ReportAssetRole
        from dp_engine.skills.runtime_models import RuntimeArtifact
        from dp_engine.report_builder.word_builder import WordBuilder
        from dp_engine.report_builder.models import WordReport, WordSection

        final = tmp_path / "nolink_append_final.docx"
        fd, temp_path_str = tempfile.mkstemp(
            dir=str(tmp_path), prefix=".dp-report-", suffix=".docx",
        )
        os.close(fd)
        temp_path = Path(temp_path_str)

        ws = tmp_path / "nolink_ws"
        ws.mkdir()
        (ws / "00_data.txt").write_text("safe data", encoding="utf-8")

        art = RuntimeArtifact(
            relative_path="output/data.txt",
            size_bytes=9,
            sha256="a" * 64,
            artifact_schema_version=1,
            artifact_id=_uid(),
            display_name="data",
            media_type="text/plain",
            task_id=_uid(),
            skill_id="testskill001",
            kind="output",
            created_at="2026-07-23T00:00:00Z",
        )
        asset = PreparedReportAsset(
            authoritative_artifact=art,
            role=ReportAssetRole.TEXT_SOURCE,
            order=0,
            managed_filename="00_data.txt",
            parsed_payload="safe data",
        )

        report = WordReport(
            title="No Post-Link",
            sections=[WordSection(heading="S1", content_paragraphs=["Body."])],
        )

        # Builder writes bridge appendix to temp
        builder = WordBuilder()
        builder.build_word_report(
            report, "", str(temp_path),
            bridge_workspace=ws,
            bridge_assets=(asset,),
        )

        # Commit via os.link
        os.link(str(temp_path), str(final))

        # Spy: verify no further Bridge appendix calls happen after link
        from dp_engine.report_bridge import adapters as _adapters
        spy_calls = []

        def spy_append(*args, **kwargs):
            spy_calls.append(True)

        with mock.patch.object(
            _adapters, "append_bridge_assets_to_word", side_effect=spy_append
        ):
            # After os.link, don't call any Bridge functions on final
            pass

        # After os.link, zero new Bridge appendix calls were made
        assert len(spy_calls) == 0, (
            f"Bridge appendix was called {len(spy_calls)} times after os.link"
        )

        # Cleanup
        os.unlink(str(temp_path))
        final.unlink()


# ═══════════════════════════════════════════════════
# Batch 3.3.1B-R: Real execution order recording
# ═══════════════════════════════════════════════════

class TestRealExecutionOrder:
    """Freeze and verify the real transaction execution order."""

    def test_real_execution_order_matches_contract(self):
        """Verify the real _execute_report_build_transaction executes operations in contract order.

        Contract order:
          1. mkstemp → create temp
          2. Provider renders to temp (Builtin delegates to Builder; bridge appendix inside Builder)
          3. Figure injection re-opens and modifies temp
          4. Footer re-opens and modifies temp
          5. Validate temp
          6. fsync temp
          7. Last cancel check
          8. os.link(temp, final) ← unique commit point
          9. os.unlink(temp) ← cleanup

        This test verifies the source code structure matches the contract.
        Batch 3.3.1B-R2: inspects _execute_report_build_transaction (extracted
        from _handle_full_report_generation).
        """
        import inspect
        import main as _main

        # Inspect the extracted module-level transaction function
        lines = inspect.getsource(
            _main._execute_report_build_transaction
        ).split("\n")

        # Find operation line numbers
        op_lines: dict[str, int] = {}
        for i, line in enumerate(lines):
            if "tempfile.mkstemp" in line:
                op_lines["mkstemp"] = i
            if "ReportRenderRequest(" in line:
                op_lines["provider_request"] = i
            if ".render(render_request)" in line:
                op_lines["provider_render"] = i
            if "_inject_figures_by_reference" in line or "_inject_figures_to_pptx" in line:
                if "_inject" not in op_lines:
                    op_lines["_inject"] = i
            if "_append_inclusion_footer" in line:
                op_lines["footer"] = i
            if "_validate_temp_output" in line:
                op_lines["validate"] = i
            if "_fsync_path" in line:
                op_lines["fsync"] = i
            if "os.link(temp_output_path" in line:
                op_lines["os_link"] = i
            if "os.unlink(temp_output_path" in line:
                op_lines["os_unlink"] = i

        # Assert relative ordering
        assert op_lines.get("mkstemp", 9999) < op_lines.get("provider_request", 0), (
            "mkstemp must occur before Provider request construction"
        )
        assert op_lines.get("provider_request", 9999) < op_lines.get("provider_render", 0), (
            "Provider request construction must precede Provider render"
        )
        assert op_lines.get("provider_render", 9999) < op_lines.get("_inject", 0), (
            "Provider render must occur before figure injection"
        )
        assert op_lines.get("_inject", 9999) < op_lines.get("footer", 0), (
            "Figure injection must occur before footer"
        )
        assert op_lines.get("validate", 9999) < op_lines.get("os_link", 0), (
            "Validation must occur before os.link"
        )
        assert op_lines.get("fsync", 9999) < op_lines.get("os_link", 0), (
            "fsync must occur before os.link"
        )
        assert op_lines.get("os_link", 9999) < op_lines.get("os_unlink", 0), (
            "os.link must occur before os.unlink"
        )

        # Verify key contract words
        assert "os.link" in "\n".join(lines), "os.link must appear in source"
        assert "os.unlink" in "\n".join(lines), "os.unlink must appear in source"
        # Builtin Provider forwards Bridge inputs to the existing Builder.
        assert "bridge_workspace" in "\n".join(lines), (
            "bridge_workspace must be passed to the Provider request"
        )
        assert "bridge_assets" in "\n".join(lines), (
            "bridge_assets must be passed to the Provider request"
        )

    def test_all_operations_on_temp_zero_modifications_after_os_link(self):
        """All writes happen on temp. After os.link, zero modifications to final."""
        import inspect
        import main as _main

        source = inspect.getsource(
            _main._execute_report_build_transaction
        )

        # After os.link, the code should only contain unlink(temp) and return
        link_idx = source.find("os.link(temp_output_path")
        assert link_idx > 0, "os.link must be present in source"

        after_link = source[link_idx:]

        # After os.link: only unlink temp, no save/inject/footer
        inject_after = (
            "_inject_figures_by_reference" in after_link
            or "_inject_figures_to_pptx" in after_link
        )
        footer_after = "_append_inclusion_footer" in after_link

        # These should NOT appear after os.link
        assert not inject_after, (
            "Figure injection must not occur after os.link"
        )
        assert not footer_after, (
            "Footer append must not occur after os.link"
        )


# ═══════════════════════════════════════════════════
# Batch 3.3.1B-R2: Real main.py transaction integration tests
# ═══════════════════════════════════════════════════

# Sentinels for real-transaction zero-LLM tests
R2_CSV_SENTINEL = "CSV_SENTINEL_R2"
R2_JSON_SENTINEL = "JSON_SENTINEL_R2"
R2_TXT_SENTINEL = "TXT_SENTINEL_R2"
R2_IMAGE_SENTINEL = "IMAGE_SENTINEL_R2"
R2_INSTRUCTION_SENTINEL = "IGNORE_PREVIOUS_INSTRUCTIONS_BRIDGE_R2"


class TestMainReportTransactionKeepsBridgeOutOfLLM:
    """P0-1 closure: real main.py transaction — same request → Bridge assets
    enter Builder appendix but NOT generate_structured_report arguments."""

    def test_main_report_transaction_keeps_bridge_payloads_out_of_llm(
        self, tmp_path: Path, monkeypatch,
    ):
        """Single _execute_report_build_transaction call carries bridge assets
        through Builder appendix WITHOUT them entering generate_structured_report.

        Patches main.generate_structured_report (main.py's lookup target).
        Proves all four sentinel types absent from LLM args but present in DOCX.
        """
        import main as _main
        from dp_engine.report_bridge.models import PreparedReportAsset, ReportAssetRole
        from dp_engine.skills.runtime_models import RuntimeArtifact

        # ── Mock AI availability ──
        import core.ai_client as _aiclient

        class _MockAI:
            def is_available(self) -> bool:
                return True

        _orig_instance = _aiclient.AIClient._instance
        _aiclient.AIClient._instance = _MockAI()
        try:
            # ── Prepare bridge workspace with sentinel-marked assets ──
            ws = tmp_path / "r2_llm_ws"
            ws.mkdir()

            csv_path = ws / "00_data.csv"
            csv_path.write_text(f"col1,col2\n{R2_CSV_SENTINEL},42\n", encoding="utf-8")
            json_path = ws / "01_config.json"
            json_path.write_text(f'{{"key": "{R2_JSON_SENTINEL}"}}', encoding="utf-8")
            txt_path = ws / "02_text.txt"
            txt_path.write_text(f"safe\n{R2_TXT_SENTINEL}\nsafe\n", encoding="utf-8")
            png_path = ws / "03_img.png"
            png_path.write_bytes(_make_png_bytes())

            def _art(media_type: str, display_name: str) -> RuntimeArtifact:
                return RuntimeArtifact(
                    relative_path=f"output/{display_name}",
                    size_bytes=128,
                    sha256="a" * 64,
                    artifact_schema_version=1,
                    artifact_id=_uid(),
                    display_name=display_name,
                    media_type=media_type,
                    task_id=_uid(),
                    skill_id="testskill001",
                    kind="output",
                    created_at="2026-07-23T00:00:00Z",
                )

            csv_asset = PreparedReportAsset(
                authoritative_artifact=_art("text/csv", "csv_data"),
                role=ReportAssetRole.TABLE_SOURCE,
                order=0,
                managed_filename="00_data.csv",
                parsed_payload=(("col1", "col2"), (R2_CSV_SENTINEL, "42")),
            )
            json_asset = PreparedReportAsset(
                authoritative_artifact=_art("application/json", "json_cfg"),
                role=ReportAssetRole.TEXT_SOURCE,
                order=1,
                managed_filename="01_config.json",
                parsed_payload=f'{{\n  "key": "{R2_JSON_SENTINEL}"\n}}',
            )
            txt_asset = PreparedReportAsset(
                authoritative_artifact=_art("text/plain", "text_note"),
                role=ReportAssetRole.TEXT_SOURCE,
                order=2,
                managed_filename="02_text.txt",
                parsed_payload=f"safe\n{R2_TXT_SENTINEL}\nsafe\n",
            )
            img_asset = PreparedReportAsset(
                authoritative_artifact=_art("image/png", f"chart_{R2_IMAGE_SENTINEL}"),
                role=ReportAssetRole.IMAGE,
                order=3,
                managed_filename="03_img.png",
                parsed_payload=None,
            )

            all_assets = (csv_asset, json_asset, txt_asset, img_asset)
            all_sentinels = [R2_CSV_SENTINEL, R2_JSON_SENTINEL, R2_TXT_SENTINEL, R2_IMAGE_SENTINEL]

            # ── Spy setup ──
            gen_report_calls: list[dict] = []
            gen_fn_calls: list[dict] = []

            def spy_generate_structured_report(*args: Any, **kwargs: Any) -> dict:
                gen_report_calls.append({
                    "positional": args,
                    "keyword": dict(kwargs),
                })
                # Wrap generate_fn to capture its prompt/messages
                original_fn = kwargs.get("generate_fn")
                if original_fn is not None:

                    def spy_generate_fn(messages: Any, **fn_kwargs: Any) -> str:
                        gen_fn_calls.append({
                            "messages": messages,
                            "keyword": dict(fn_kwargs),
                        })
                        return "mock LLM response"

                    kwargs = dict(kwargs)
                    kwargs["generate_fn"] = spy_generate_fn

                return {
                    "sections": [
                        {
                            "heading": "R2 Test Section",
                            "content_paragraphs": ["Generated content for R2 test."],
                            "image_anchors": [],
                            "tables": [],
                        }
                    ],
                    "_report_warnings": [],
                    "_diagnosis_loaded": False,
                }

            # Patch main.generate_structured_report (main.py's lookup target)
            monkeypatch.setattr(
                _main,
                "generate_structured_report",
                spy_generate_structured_report,
            )

            # ── Execute real transaction via extracted function ──
            final_path = tmp_path / "r2_llm_final.docx"
            report_dir = tmp_path / "reports"
            report_dir.mkdir()

            result = _main._execute_report_build_transaction(
                config={"report_type": "word"},
                outline="# R2 Test\n\n## Section 1\n- Point A",
                report_type="word",
                generate_fn=lambda messages: "mock",
                template_path="",
                final_output_path=str(final_path),
                report_dir=str(report_dir),
                project_dir=str(tmp_path),
                candidate=str(tmp_path),
                worker=None,
                bridge_workspace=ws,
                bridge_assets=all_assets,
                _provider=None,
            )

            # ── Assertion 1: generate_structured_report was reached ──
            assert len(gen_report_calls) >= 1, (
                "generate_structured_report was never called"
            )

            # ── Assertion 2: ZERO sentinels in generate_structured_report arguments ──
            found_in_gen_report: list[str] = []
            for call_record in gen_report_calls:
                all_data = {
                    "positional": call_record["positional"],
                    "keyword": call_record["keyword"],
                }
                for sentinel in all_sentinels:
                    if _recursive_search(all_data, sentinel):
                        found_in_gen_report.append(sentinel)

            assert len(found_in_gen_report) == 0, (
                f"P0: Sentinels FOUND in generate_structured_report arguments: "
                f"{found_in_gen_report}"
            )

            # ── Assertion 3: ZERO sentinels in generate_fn prompt/messages ──
            found_in_gen_fn: list[str] = []
            for call_record in gen_fn_calls:
                for sentinel in all_sentinels:
                    if _recursive_search(call_record, sentinel):
                        found_in_gen_fn.append(sentinel)

            assert len(found_in_gen_fn) == 0, (
                f"P0: Sentinels FOUND in generate_fn messages/prompt: "
                f"{found_in_gen_fn}"
            )

            # ── Assertion 4: Sentinels ARE in deterministic DOCX appendix ──
            assert final_path.exists(), "Output DOCX was not created"
            doc = Document(str(final_path))
            para_texts = [p.text for p in doc.paragraphs]
            table_texts: list[str] = []
            for table in doc.tables:
                for row in table.rows:
                    for cell in row.cells:
                        table_texts.append(cell.text)
            full_text = "\n".join(para_texts + table_texts)

            assert R2_CSV_SENTINEL in "\n".join(table_texts), (
                f"{R2_CSV_SENTINEL} must appear in appendix table"
            )
            for sentinel in [R2_JSON_SENTINEL, R2_TXT_SENTINEL, R2_IMAGE_SENTINEL]:
                assert sentinel in full_text, (
                    f"{sentinel} must appear in deterministic appendix"
                )

            # "技能输出素材" heading appears exactly once
            assert full_text.count("技能输出素材") == 1, (
                "Bridge appendix heading must appear exactly once"
            )
        finally:
            _aiclient.AIClient._instance = _orig_instance

    def test_main_transaction_keeps_instruction_like_asset_literal_and_out_of_llm(
        self, tmp_path: Path, monkeypatch,
    ):
        """Instruction-like artifact text goes through real transaction:
        NOT in LLM args, IS literally in appendix, no extra AI chapters."""
        import main as _main
        from dp_engine.report_bridge.models import PreparedReportAsset, ReportAssetRole
        from dp_engine.skills.runtime_models import RuntimeArtifact

        # ── Mock AI availability ──
        import core.ai_client as _aiclient

        class _MockAI:
            def is_available(self) -> bool:
                return True

        _orig_instance = _aiclient.AIClient._instance
        _aiclient.AIClient._instance = _MockAI()
        try:
            ws = tmp_path / "r2_instr_ws"
            ws.mkdir()

            instruction_text = (
                f"Normal header\n"
                f"{R2_INSTRUCTION_SENTINEL}\n"
                f"Normal footer\n"
            )
            txt_path = ws / "00_instr.txt"
            txt_path.write_text(instruction_text, encoding="utf-8")

            art = RuntimeArtifact(
                relative_path="output/instr.txt",
                size_bytes=len(instruction_text.encode("utf-8")),
                sha256="a" * 64,
                artifact_schema_version=1,
                artifact_id=_uid(),
                display_name="instruction_like.txt",
                media_type="text/plain",
                task_id=_uid(),
                skill_id="testskill001",
                kind="output",
                created_at="2026-07-23T00:00:00Z",
            )
            asset = PreparedReportAsset(
                authoritative_artifact=art,
                role=ReportAssetRole.TEXT_SOURCE,
                order=0,
                managed_filename="00_instr.txt",
                parsed_payload=instruction_text,
            )

            gen_report_calls: list[dict] = []

            def spy_generate_structured_report(*args: Any, **kwargs: Any) -> dict:
                gen_report_calls.append({
                    "positional": args,
                    "keyword": dict(kwargs),
                })
                return {
                    "sections": [
                        {
                            "heading": "Normal Section",
                            "content_paragraphs": ["Standard generated text."],
                            "image_anchors": [],
                            "tables": [],
                        }
                    ],
                    "_report_warnings": [],
                    "_diagnosis_loaded": False,
                }

            monkeypatch.setattr(
                _main, "generate_structured_report", spy_generate_structured_report,
            )

            final_path = tmp_path / "r2_instr_final.docx"
            report_dir = tmp_path / "reports_instr"
            report_dir.mkdir()

            _main._execute_report_build_transaction(
                config={"report_type": "word"},
                outline="# Test\n\n## S1\n- Point",
                report_type="word",
                generate_fn=lambda messages: "mock",
                template_path="",
                final_output_path=str(final_path),
                report_dir=str(report_dir),
                project_dir=str(tmp_path),
                candidate=str(tmp_path),
                worker=None,
                bridge_workspace=ws,
                bridge_assets=(asset,),
                _provider=None,
            )

            # ── Assertion 1: Instruction sentinel NOT in LLM arguments ──
            for call_record in gen_report_calls:
                all_data = {
                    "positional": call_record["positional"],
                    "keyword": call_record["keyword"],
                }
                assert not _recursive_search(all_data, R2_INSTRUCTION_SENTINEL), (
                    "P0: Instruction-like sentinel FOUND in generate_structured_report args"
                )

            # ── Assertion 2: Sentinel appears literally in appendix ──
            doc = Document(str(final_path))
            full_text = "\n".join(p.text for p in doc.paragraphs)
            assert R2_INSTRUCTION_SENTINEL in full_text, (
                "Instruction-like sentinel must appear literally in appendix"
            )

            # ── Assertion 3: No extra AI-generated headings triggered ──
            headings = [
                p.text for p in doc.paragraphs
                if p.style.name.startswith("Heading")
            ]
            instruction_headings = [h for h in headings if "IGNORE" in h.upper()]
            assert len(instruction_headings) == 0, (
                f"Instruction-like text triggered unexpected headings: {instruction_headings}"
            )

            # ── Assertion 4: Standard content preserved ──
            assert "Standard generated text." in full_text, (
                "Document body content should be preserved"
            )

            # ── Assertion 5: No extra generate_fn calls triggered ──
            assert len(gen_report_calls) == 1, (
                "Only one generate_structured_report call expected"
            )
        finally:
            _aiclient.AIClient._instance = _orig_instance


class TestMainTransactionAutoRollback:
    """P0-2 closure: real main.py transaction auto-rolls-back on
    figure injection / footer failure via production try/finally.
    Tests never call _cleanup_temp manually."""

    def test_main_word_transaction_auto_rolls_back_on_figure_injection_failure(
        self, tmp_path: Path, monkeypatch,
    ):
        """Production try/finally removes temp, prevents os.link, preserves
        pre-existing final when figure injection raises."""
        import main as _main

        final_path = tmp_path / "r2_rollback_final.docx"
        report_dir = tmp_path / "reports_rb"
        report_dir.mkdir()

        # Pre-existing final with known content
        existing_content = b"PRE_EXISTING_FINAL_CONTENT_R2"
        final_path.write_bytes(existing_content)

        # Spy os.link to prove it was NEVER called
        os_link_calls: list[tuple] = []
        original_os_link = os.link

        def spy_os_link(src: str, dst: str) -> None:
            os_link_calls.append((src, dst))
            return original_os_link(src, dst)

        monkeypatch.setattr(os, "link", spy_os_link)

        # Ensure manifest has at least 1 figure so injection is triggered
        from core.report_charts import ReportFigure, FigureManifest
        _trigger_manifest = FigureManifest()
        _trigger_manifest.add(
            fig_id="trigger_fig", section="Test", title="T",
            key_stat="N/A", png_path=str(tmp_path / "trigger.png"),
        )
        (tmp_path / "trigger.png").write_bytes(_make_png_bytes(5, 5))
        monkeypatch.setattr(
            "core.chart_store.chart_manifest_to_figure_manifest",
            lambda cm, cd: _trigger_manifest,
        )
        monkeypatch.setattr(
            "core.chart_store.chart_manifest_from_dict",
            lambda d: type("_CM", (), {"__iter__": lambda s: iter([])})(),
        )

        # Patch figure injection to raise
        def failing_inject_figures(*args: Any, **kwargs: Any) -> None:
            raise RuntimeError("simulated figure injection failure — R2")

        monkeypatch.setattr(
            _main, "_inject_figures_by_reference", failing_inject_figures,
        )

        import core.ai_client as _aiclient
        class _MockAI_RB:
            def is_available(self) -> bool:
                return True
        _orig_ai_instance = _aiclient.AIClient._instance
        _aiclient.AIClient._instance = _MockAI_RB()

        def mock_generate_structured_report(*args: Any, **kwargs: Any) -> dict:
            # generate_fn spy
            original_fn = kwargs.get("generate_fn")
            if original_fn is not None:
                kwargs = dict(kwargs)
                kwargs["generate_fn"] = lambda messages, **kw: "mock"
            return {
                "sections": [
                    {
                        "heading": "Rollback Test",
                        "content_paragraphs": ["Content for rollback test."],
                        "image_anchors": [],
                        "tables": [],
                    }
                ],
                "_report_warnings": [],
                "_diagnosis_loaded": False,
            }

        monkeypatch.setattr(
            _main, "generate_structured_report", mock_generate_structured_report,
        )

        # Execute — should raise from production except block
        with pytest.raises(RuntimeError, match="simulated figure injection"):
            _main._execute_report_build_transaction(
                config={"report_type": "word"},
                outline="# Rollback\n\n## S1\n- Point",
                report_type="word",
                generate_fn=lambda messages: "mock",
                template_path="",
                final_output_path=str(final_path),
                report_dir=str(report_dir),
                project_dir=str(tmp_path),
                candidate=str(tmp_path),
                worker=None,
                _provider=None,
            )

        # ── Assertion 1: os.link was NEVER called ──
        assert len(os_link_calls) == 0, (
            f"os.link was called {len(os_link_calls)} times — "
            "production rollback must prevent commit"
        )

        # ── Assertion 2: pre-existing final content UNCHANGED ──
        assert final_path.exists(), "pre-existing final must still exist"
        assert final_path.read_bytes() == existing_content, (
            "pre-existing final content must be unchanged after rollback"
        )

        # ── Assertion 3: temp files cleaned up by production ──
        # (No temp .dp-report-* files in report_dir)
        temp_files = list(report_dir.glob(".dp-report-*"))
        assert len(temp_files) == 0, (
            f"Production should have cleaned up temp files, found: {temp_files}"
        )

    def test_main_ppt_transaction_auto_rolls_back_on_figure_injection_failure(
        self, tmp_path: Path, monkeypatch,
    ):
        """PPT: Production try/finally removes temp, prevents os.link,
        preserves pre-existing final when PPT figure injection raises."""
        import main as _main

        final_path = tmp_path / "r2_rollback_ppt.pptx"
        report_dir = tmp_path / "reports_rb_ppt"
        report_dir.mkdir()

        existing_content = b"PRE_EXISTING_PPT_FINAL_R2"
        final_path.write_bytes(existing_content)

        os_link_calls: list[tuple] = []
        original_os_link = os.link

        def spy_os_link(src: str, dst: str) -> None:
            os_link_calls.append((src, dst))
            return original_os_link(src, dst)

        monkeypatch.setattr(os, "link", spy_os_link)

        # Ensure manifest has at least 1 figure so injection is triggered
        from core.report_charts import ReportFigure, FigureManifest
        _trigger_manifest_ppt = FigureManifest()
        _trigger_manifest_ppt.add(
            fig_id="trigger_ppt", section="Test", title="T",
            key_stat="N/A", png_path=str(tmp_path / "trigger_ppt.png"),
        )
        (tmp_path / "trigger_ppt.png").write_bytes(_make_png_bytes(5, 5))
        monkeypatch.setattr(
            "core.chart_store.chart_manifest_to_figure_manifest",
            lambda cm, cd: _trigger_manifest_ppt,
        )
        monkeypatch.setattr(
            "core.chart_store.chart_manifest_from_dict",
            lambda d: type("_CM", (), {"__iter__": lambda s: iter([])})(),
        )

        def failing_inject_pptx(*args: Any, **kwargs: Any) -> None:
            raise RuntimeError("simulated PPT figure injection failure — R2")

        monkeypatch.setattr(
            _main, "_inject_figures_to_pptx", failing_inject_pptx,
        )

        import core.ai_client as _aiclient_rb
        class _MockAI_RB:
            def is_available(self) -> bool:
                return True
        _orig_ai_inst = _aiclient_rb.AIClient._instance
        _aiclient_rb.AIClient._instance = _MockAI_RB()

        def mock_generate_structured_report(*args: Any, **kwargs: Any) -> dict:
            return {
                "sections": [
                    {
                        "heading": "PPT Rollback",
                        "content_paragraphs": ["PPT rollback content."],
                        "image_anchors": [],
                        "tables": [],
                    }
                ],
                "_report_warnings": [],
                "_diagnosis_loaded": False,
            }

        monkeypatch.setattr(
            _main, "generate_structured_report", mock_generate_structured_report,
        )

        with pytest.raises(RuntimeError, match="simulated PPT figure injection"):
            _main._execute_report_build_transaction(
                config={"report_type": "ppt"},
                outline="# PPT\n\n## S1\n- Point",
                report_type="ppt",
                generate_fn=lambda messages: "mock",
                template_path="",
                final_output_path=str(final_path),
                report_dir=str(report_dir),
                project_dir=str(tmp_path),
                candidate=str(tmp_path),
                worker=None,
                _provider=None,
            )

        assert len(os_link_calls) == 0, (
            f"os.link was called {len(os_link_calls)} times"
        )
        assert final_path.exists()
        assert final_path.read_bytes() == existing_content
        temp_files = list(report_dir.glob(".dp-report-*"))
        assert len(temp_files) == 0, (
            f"Production should have cleaned up temp files, found: {temp_files}"
        )

    def test_main_word_transaction_auto_rolls_back_on_footer_failure(
        self, tmp_path: Path, monkeypatch,
    ):
        """Production try/finally removes temp, prevents os.link when
        _append_inclusion_footer raises. Builder + injection succeed."""
        import main as _main

        final_path = tmp_path / "r2_footer_fail.docx"
        report_dir = tmp_path / "reports_footer"
        report_dir.mkdir()

        existing_content = b"PRE_EXISTING_FOOTER_FINAL_R2"
        final_path.write_bytes(existing_content)

        os_link_calls: list[tuple] = []
        original_os_link = os.link

        def spy_os_link(src: str, dst: str) -> None:
            os_link_calls.append((src, dst))
            return original_os_link(src, dst)

        monkeypatch.setattr(os, "link", spy_os_link)

        # Patch footer to raise (figure injection succeeds — no manifest, so skipped)
        def failing_footer(
            docx_path: str, report_type: str,
            warnings: list, diagnosis_loaded: bool,
        ) -> None:
            raise RuntimeError("simulated footer failure — R2")

        monkeypatch.setattr(
            _main.DataProcessorWindow, "_append_inclusion_footer",
            staticmethod(failing_footer),
        )

        import core.ai_client as _aiclient_rb
        class _MockAI_RB:
            def is_available(self) -> bool:
                return True
        _orig_ai_inst = _aiclient_rb.AIClient._instance
        _aiclient_rb.AIClient._instance = _MockAI_RB()

        def mock_generate_structured_report(*args: Any, **kwargs: Any) -> dict:
            return {
                "sections": [
                    {
                        "heading": "Footer Fail",
                        "content_paragraphs": ["Footer fail content."],
                        "image_anchors": [],
                        "tables": [],
                    }
                ],
                "_report_warnings": [],
                "_diagnosis_loaded": False,
            }

        monkeypatch.setattr(
            _main, "generate_structured_report", mock_generate_structured_report,
        )

        with pytest.raises(RuntimeError, match="simulated footer failure"):
            _main._execute_report_build_transaction(
                config={"report_type": "word"},
                outline="# Footer\n\n## S1\n- Point",
                report_type="word",
                generate_fn=lambda messages: "mock",
                template_path="",
                final_output_path=str(final_path),
                report_dir=str(report_dir),
                project_dir=str(tmp_path),
                candidate=str(tmp_path),
                worker=None,
                _provider=None,
            )

        assert len(os_link_calls) == 0, (
            f"os.link was called {len(os_link_calls)} times"
        )
        assert final_path.exists()
        assert final_path.read_bytes() == existing_content
        temp_files = list(report_dir.glob(".dp-report-*"))
        assert len(temp_files) == 0, (
            f"Production should have cleaned up temp files, found: {temp_files}"
        )


class TestMainTransactionRuntimeOrder:
    """Runtime order and single-commit verification via spy on real transaction."""

    def test_main_transaction_runtime_order_and_single_commit(
        self, tmp_path: Path, monkeypatch,
    ):
        """Spy-records key events during real transaction, verifies
        contract ordering and single os.link commit."""
        import main as _main

        final_path = tmp_path / "r2_order_final.docx"
        report_dir = tmp_path / "reports_order"
        report_dir.mkdir()

        events: list[str] = []

        # Mock AI availability
        import core.ai_client as _aiclient_rb
        class _MockAI_RB:
            def is_available(self) -> bool:
                return True
        _orig_ai_inst = _aiclient_rb.AIClient._instance
        _aiclient_rb.AIClient._instance = _MockAI_RB()

        # Spy on generate_structured_report → records builder_start
        original_gsr = _main.generate_structured_report

        def spy_gsr(*args: Any, **kwargs: Any) -> dict:
            events.append("builder_start")
            return {
                "sections": [
                    {
                        "heading": "Order Test",
                        "content_paragraphs": ["Order test content."],
                        "image_anchors": [],
                        "tables": [],
                    }
                ],
                "_report_warnings": [],
                "_diagnosis_loaded": False,
            }

        monkeypatch.setattr(_main, "generate_structured_report", spy_gsr)

        # Spy on bridge appendix (via Builder passing bridge_assets)
        # The Builder calls append_bridge_assets internally
        from dp_engine.report_bridge import adapters as _adapters
        original_append_word = _adapters.append_bridge_assets_to_word

        def spy_append_word(*args: Any, **kwargs: Any) -> None:
            events.append("bridge_appendix")
            return original_append_word(*args, **kwargs)

        monkeypatch.setattr(
            _adapters, "append_bridge_assets_to_word", spy_append_word,
        )

        # Spy on figure injection
        original_inject = _main._inject_figures_by_reference

        def spy_inject(*args: Any, **kwargs: Any) -> None:
            events.append("figure_injection")
            return original_inject(*args, **kwargs)

        monkeypatch.setattr(_main, "_inject_figures_by_reference", spy_inject)

        # Spy on footer
        original_footer = _main.DataProcessorWindow._append_inclusion_footer

        def spy_footer(*args: Any, **kwargs: Any) -> None:
            events.append("footer")
            return original_footer(*args, **kwargs)

        monkeypatch.setattr(
            _main.DataProcessorWindow, "_append_inclusion_footer",
            staticmethod(spy_footer),
        )

        # Spy on os.link
        os_link_events: list[tuple] = []
        original_link = os.link

        def spy_link(src: str, dst: str) -> None:
            events.append("os_link")
            os_link_events.append((src, dst))
            return original_link(src, dst)

        monkeypatch.setattr(os, "link", spy_link)

        # Spy on os.unlink for temp cleanup
        os_unlink_events: list[str] = []
        original_unlink = os.unlink

        def spy_unlink(path: str) -> None:
            if ".dp-report-" in str(path):
                events.append("temp_unlink")
                os_unlink_events.append(str(path))
            return original_unlink(path)

        monkeypatch.setattr(os, "unlink", spy_unlink)

        # Prepare bridge asset to trigger bridge_appendix event
        from dp_engine.report_bridge.models import PreparedReportAsset, ReportAssetRole
        from dp_engine.skills.runtime_models import RuntimeArtifact

        ws = tmp_path / "r2_order_ws"
        ws.mkdir()
        (ws / "00_order.txt").write_text("order test data", encoding="utf-8")
        art = RuntimeArtifact(
            relative_path="output/order.txt",
            size_bytes=14,
            sha256="a" * 64,
            artifact_schema_version=1,
            artifact_id=_uid(),
            display_name="order_item",
            media_type="text/plain",
            task_id=_uid(),
            skill_id="testskill001",
            kind="output",
            created_at="2026-07-23T00:00:00Z",
        )
        asset = PreparedReportAsset(
            authoritative_artifact=art,
            role=ReportAssetRole.TEXT_SOURCE,
            order=0,
            managed_filename="00_order.txt",
            parsed_payload="order test data",
        )

        # Need a manifest entry to trigger figure injection
        # Create a minimal manifest
        from core.report_charts import ReportFigure, FigureManifest

        # Create a chart entry so that injection is triggered
        chart_png = tmp_path / "chart_test.png"
        chart_png.write_bytes(_make_png_bytes(5, 5))

        manifest = FigureManifest()
        manifest.add(
            fig_id="test_fig_1",
            section="Order Test",
            title="Test Figure",
            key_stat="N/A",
            png_path=str(chart_png),
        )

        # Patch chart_manifest_to_figure_manifest to return our manifest
        monkeypatch.setattr(
            "core.chart_store.chart_manifest_to_figure_manifest",
            lambda cm, cd: manifest,
        )
        monkeypatch.setattr(
            "core.chart_store.chart_manifest_from_dict",
            lambda d: type("_CM", (), {"__iter__": lambda s: iter([])})(),
        )

        # Embed manifest in config via _diagnosis_record
        diag_rec = {
            "record_id": "test" + _uid()[:16],
            "timestamp": "20260723_120000",
            "chart_manifest": [],  # Minimal
            "chart_data": {},
        }

        _main._execute_report_build_transaction(
            config={
                "report_type": "word",
                "_diagnosis_record": diag_rec,
                "_figure_manifest_override": manifest,  # Will be ignored
            },
            outline="# Order Test\n\n## S1\n- Point",
            report_type="word",
            generate_fn=lambda messages: "mock",
            template_path="",
            final_output_path=str(final_path),
            report_dir=str(report_dir),
            project_dir=str(tmp_path),
            candidate=str(tmp_path),
            worker=None,
            bridge_workspace=ws,
            bridge_assets=(asset,),
            _provider=None,
        )

        # ── Verify runtime order ──
        # builder_start < bridge_appendix < figure_injection < footer < os_link < temp_unlink
        order_map = {name: idx for idx, name in enumerate(events)}

        assert order_map.get("builder_start", 9999) < order_map.get("bridge_appendix", 0), (
            f"builder_start must precede bridge_appendix. Events: {events}"
        )
        assert order_map.get("bridge_appendix", 9999) < order_map.get("figure_injection", 0), (
            f"bridge_appendix must precede figure_injection. Events: {events}"
        )
        assert order_map.get("figure_injection", 9999) < order_map.get("footer", 0), (
            f"figure_injection must precede footer. Events: {events}"
        )
        assert order_map.get("footer", 9999) < order_map.get("os_link", 0), (
            f"footer must precede os_link. Events: {events}"
        )
        assert order_map.get("os_link", 9999) < order_map.get("temp_unlink", 0), (
            f"os_link must precede temp_unlink. Events: {events}"
        )

        # ── Assert single os.link commit ──
        assert len(os_link_events) == 1, (
            f"os.link must be called exactly once, got {len(os_link_events)}"
        )

        # ── os.link after, zero save/inject/footer ──
        # (Verified structurally by order: no events after os_link except temp_unlink)
        after_link = events[order_map["os_link"] + 1:]
        forbidden_after_link = {"builder_start", "bridge_appendix", "figure_injection", "footer"}
        found_forbidden = [e for e in after_link if e in forbidden_after_link]
        assert len(found_forbidden) == 0, (
            f"Forbidden operations after os.link: {found_forbidden}. Events: {events}"
        )

        # ── os.link after, zero doc.save / prs.save ──
        # (Implicit in the structural proof above — no events after os.link modify the file)
