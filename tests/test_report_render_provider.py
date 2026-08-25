"""Batch 3.5.3 report render Provider dispatch and builtin parity tests."""

from __future__ import annotations

import inspect
from pathlib import Path
from zipfile import ZipFile

import pytest

from dp_engine.report_builder.models import PPTReport, WordReport
from dp_engine.report_builder.ppt_builder import PPTBuilder
from dp_engine.report_builder.word_builder import WordBuilder
from dp_engine.report_provider import (
    BUILTIN_PROVIDER_ID,
    BUILTIN_PROVIDER_VERSION,
    BuiltinReportRenderProvider,
    ReportProviderProvenance,
    ReportRenderOrchestrator,
    ReportRenderRequest,
    ReportRenderResult,
)


WORD_DATA = {
    "title": "Provider 等价性 Word 报告",
    "author": "Batch 3.5.3",
    "date": "2026-08-06",
    "sections": [
        {
            "heading": "结论",
            "content_paragraphs": ["内置渲染路径保持不变。"],
            "image_anchors": [],
            "tables": [],
        }
    ],
}

PPT_DATA = {
    "title": "Provider 等价性 PPT 报告",
    "author": "Batch 3.5.3",
    "date": "2026-08-06",
    "slides": [
        {
            "slide_title": "结论",
            "bullet_points": ["内置渲染路径保持不变", "外层事务保持原子提交"],
            "speaker_notes": "Batch 3.5.3 parity",
            "image_anchor": None,
        }
    ],
}


def _ooxml_structure(path: Path, package_root: str) -> dict[str, bytes]:
    """Return deterministic OOXML parts owned by the document body."""
    with ZipFile(path) as archive:
        return {
            name: archive.read(name)
            for name in sorted(archive.namelist())
            if name.startswith(package_root)
            and (name.endswith(".xml") or name.endswith(".rels"))
        }


def _request(
    report_type: str,
    structured_report: dict,
    output_path: Path,
    *,
    cancel_check=None,
) -> ReportRenderRequest:
    return ReportRenderRequest(
        report_type=report_type,
        structured_report=structured_report,
        template_path="",
        output_path=str(output_path),
        project_dir=str(output_path.parent),
        cancel_check=cancel_check,
    )


def test_builtin_word_provider_is_structurally_equivalent_to_direct_builder(
    tmp_path: Path,
) -> None:
    direct_path = tmp_path / "direct.docx"
    provider_path = tmp_path / "provider.docx"

    WordBuilder().build_word_report(
        WordReport.from_dict(WORD_DATA),
        "",
        str(direct_path),
        project_dir=str(tmp_path),
    )
    result = BuiltinReportRenderProvider().render(
        _request("word", WORD_DATA, provider_path)
    )

    assert _ooxml_structure(provider_path, "word/") == _ooxml_structure(
        direct_path, "word/"
    )
    assert result.output_path == str(provider_path)
    assert result.provenance == ReportProviderProvenance(
        provider_id=BUILTIN_PROVIDER_ID,
        provider_version=BUILTIN_PROVIDER_VERSION,
    )


def test_builtin_ppt_provider_is_structurally_equivalent_to_direct_builder(
    tmp_path: Path,
) -> None:
    direct_path = tmp_path / "direct.pptx"
    provider_path = tmp_path / "provider.pptx"

    PPTBuilder().build_ppt_report(
        PPTReport.from_dict(PPT_DATA),
        "",
        str(direct_path),
        project_dir=str(tmp_path),
    )
    result = BuiltinReportRenderProvider().render(
        _request("ppt", PPT_DATA, provider_path)
    )

    assert _ooxml_structure(provider_path, "ppt/") == _ooxml_structure(
        direct_path, "ppt/"
    )
    assert result.output_path == str(provider_path)
    assert result.provenance.provider_id == BUILTIN_PROVIDER_ID


def test_builtin_word_provider_preserves_missing_image_warning(
    tmp_path: Path,
) -> None:
    data = {
        **WORD_DATA,
        "sections": [
            {
                **WORD_DATA["sections"][0],
                "image_anchors": [
                    "[INSERT_IMAGE: missing-provider-image.png]"
                ],
            }
        ],
    }

    result = BuiltinReportRenderProvider().render(
        _request("word", data, tmp_path / "missing.docx")
    )

    assert result.missing_images == ("missing-provider-image.png",)


@pytest.mark.parametrize(
    ("report_type", "structured_report", "suffix"),
    [("word", WORD_DATA, ".docx"), ("ppt", PPT_DATA, ".pptx")],
)
def test_builtin_provider_preserves_builder_cancellation(
    tmp_path: Path,
    report_type: str,
    structured_report: dict,
    suffix: str,
) -> None:
    output = tmp_path / f"cancelled{suffix}"

    def cancel() -> None:
        raise RuntimeError("provider render cancelled")

    with pytest.raises(RuntimeError, match="provider render cancelled"):
        BuiltinReportRenderProvider().render(
            _request(
                report_type,
                structured_report,
                output,
                cancel_check=cancel,
            )
        )

    assert not output.exists()


def test_orchestrator_defaults_to_builtin_and_rejects_unknown_provider(
    tmp_path: Path,
) -> None:
    orchestrator = ReportRenderOrchestrator()
    request = _request("word", WORD_DATA, tmp_path / "default.docx")

    result = orchestrator.render(request)

    assert result.provenance.provider_id == BUILTIN_PROVIDER_ID
    with pytest.raises(LookupError, match="unknown report render provider"):
        orchestrator.render(request, provider_id="not-installed")


def test_orchestrator_fails_closed_on_provider_provenance_mismatch(
    tmp_path: Path,
) -> None:
    declared = ReportProviderProvenance("declared-provider", "1.2.3")

    class MismatchedProvider:
        provenance = declared

        def render(self, request: ReportRenderRequest) -> ReportRenderResult:
            Path(request.output_path).write_bytes(b"not-a-real-document")
            return ReportRenderResult(
                output_path=request.output_path,
                provenance=ReportProviderProvenance("spoofed-provider", "9.9.9"),
            )

    orchestrator = ReportRenderOrchestrator(
        providers=(MismatchedProvider(),),
        default_provider_id=declared.provider_id,
    )

    with pytest.raises(RuntimeError, match="provenance mismatch"):
        orchestrator.render(
            _request("word", WORD_DATA, tmp_path / "spoofed.docx")
        )


def test_main_transaction_dispatches_render_through_provider_seam() -> None:
    import main

    source = inspect.getsource(main._execute_report_build_transaction)

    assert "ReportRenderRequest(" in source
    assert ".render(render_request)" in source
    assert "build_word_report(" not in source
    assert "build_ppt_report(" not in source
