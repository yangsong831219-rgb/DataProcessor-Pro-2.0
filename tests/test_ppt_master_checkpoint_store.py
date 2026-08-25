"""Content-addressed slide checkpoint and Provider resume tests."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

from dp_engine.ppt_master_host import SlideCheckpointKey, SlideCheckpointStore
from dp_engine.report_provider import PptMasterAuthoredSlide, PptMasterReportRenderProvider
from dp_engine.report_provider import PptMasterReportProviderError
from tests.test_ppt_master_report_provider import (
    _FakeAuthorer,
    _FakeRunner,
    _render_request,
    _snapshot,
)


def _key() -> SlideCheckpointKey:
    return SlideCheckpointKey(
        planning_context_sha256="a" * 64,
        deck_contract_sha256="c" * 64,
        toolchain_sha256="f" * 64,
        authoring_model_identity="fixture-authorer",
        slide_input_sha256="d" * 64,
        method_receipt_sha256="e" * 64,
    )


def test_checkpoint_store_rejects_tampered_payload(tmp_path: Path) -> None:
    store = SlideCheckpointStore(tmp_path / "checkpoints")
    slide = PptMasterAuthoredSlide(
        slide_id="slide-1",
        sequence=1,
        svg_text='<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1280 720"/>',
        speaker_notes_markdown="notes",
    )
    store.publish(_key(), slide)
    entry = tmp_path / "checkpoints" / _key().digest
    (entry / "slide.svg").write_text("tampered", encoding="utf-8")

    assert store.load(_key()) is None


def test_provider_reuses_pages_but_repeats_controlled_quality_and_export(
    tmp_path: Path,
) -> None:
    store = SlideCheckpointStore(tmp_path / "checkpoints")
    snapshot = _snapshot()

    first_events: list[str] = []
    first_root = tmp_path / "first"
    first_root.mkdir()
    first_request = _render_request(first_root)
    first_runner = _FakeRunner(tmp_path / "runs", first_events)
    first_authorer = _FakeAuthorer(first_events)
    first_provider = PptMasterReportRenderProvider(
        planning_snapshot=snapshot,
        runner=first_runner,
        authoring_adapter=first_authorer,
        checkpoint_store=store,
        id_factory=lambda: "firsttoken",
    )
    first_provider.render(first_request)
    assert first_events.count("author:slide:1") == 1
    assert first_events.count("author:slide:2") == 1
    assert first_events.count("author:slide:3") == 1

    second_events: list[str] = []
    second_root = tmp_path / "second"
    second_root.mkdir()
    second_request = _render_request(second_root)
    # Output location is intentionally different; report inputs remain identical.
    second_request = replace(
        second_request,
        structured_report=first_request.structured_report,
        assets=tuple(
            replace(asset, source_path=first_request.assets[index].source_path)
            for index, asset in enumerate(second_request.assets)
        ),
    )
    second_runner = _FakeRunner(tmp_path / "runs", second_events)
    second_authorer = _FakeAuthorer(second_events)
    second_provider = PptMasterReportRenderProvider(
        planning_snapshot=snapshot,
        runner=second_runner,
        authoring_adapter=second_authorer,
        checkpoint_store=store,
        id_factory=lambda: "secondtoken",
    )

    second_provider.render(second_request)

    assert all(not event.startswith("author:slide:") for event in second_events)
    assert "run:project_init" in second_events
    assert "run:svg_quality_check:first-page" in second_events
    assert "run:svg_quality_check:final" in second_events
    assert "run:svg_to_pptx" in second_events


def test_failed_final_quality_does_not_publish_page_checkpoints(
    tmp_path: Path,
) -> None:
    root = tmp_path / "failed"
    root.mkdir()
    request = _render_request(root)
    events: list[str] = []
    store = SlideCheckpointStore(tmp_path / "checkpoints")
    provider = PptMasterReportRenderProvider(
        planning_snapshot=_snapshot(),
        runner=_FakeRunner(
            tmp_path / "runs",
            events,
            fail_key="svg_quality_check:final",
        ),
        authoring_adapter=_FakeAuthorer(events),
        checkpoint_store=store,
        id_factory=lambda: "failedtoken",
    )

    try:
        provider.render(request)
    except PptMasterReportProviderError:
        pass
    else:  # pragma: no cover - failure is the fixture contract
        raise AssertionError("fixture final quality failure was not raised")

    entries = [
        path for path in (tmp_path / "checkpoints").glob("*")
        if path.name != ".staging"
    ]
    assert entries == []


def test_changing_one_source_slide_invalidates_only_that_page(
    tmp_path: Path,
) -> None:
    store = SlideCheckpointStore(tmp_path / "checkpoints")
    snapshot = _snapshot()
    first_root = tmp_path / "first-page-change"
    first_root.mkdir()
    first_request = _render_request(first_root)
    first_events: list[str] = []
    PptMasterReportRenderProvider(
        planning_snapshot=snapshot,
        runner=_FakeRunner(tmp_path / "runs", first_events),
        authoring_adapter=_FakeAuthorer(first_events),
        checkpoint_store=store,
        id_factory=lambda: "changefirst",
    ).render(first_request)

    changed_root = tmp_path / "second-page-change"
    changed_root.mkdir()
    changed_request = _render_request(changed_root)
    changed_report = dict(first_request.structured_report)
    changed_slides = [dict(slide) for slide in changed_report["slides"]]
    changed_slides[2]["slide_title"] = "Changed decision evidence"
    changed_report["slides"] = changed_slides
    changed_request = replace(
        changed_request,
        structured_report=changed_report,
        assets=tuple(
            replace(asset, source_path=first_request.assets[index].source_path)
            for index, asset in enumerate(changed_request.assets)
        ),
    )
    changed_events: list[str] = []
    PptMasterReportRenderProvider(
        planning_snapshot=snapshot,
        runner=_FakeRunner(tmp_path / "runs", changed_events),
        authoring_adapter=_FakeAuthorer(changed_events),
        checkpoint_store=store,
        id_factory=lambda: "changesecond",
    ).render(changed_request)

    assert "author:slide:1" not in changed_events
    assert "author:slide:2" not in changed_events
    assert changed_events.count("author:slide:3") == 1
