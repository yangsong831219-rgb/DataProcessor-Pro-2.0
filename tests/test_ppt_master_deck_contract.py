"""Deck Contract Compiler regression tests."""

from __future__ import annotations

from pathlib import Path

from dp_engine.ppt_master_host import DeckContractCompiler
from tests.test_ppt_master_report_provider import _provider, _render_request


def _context(tmp_path: Path):
    request = _render_request(tmp_path)
    provider, _runner, _authorer, _events = _provider(tmp_path)
    return provider._prepare_request(  # pyright: ignore[reportPrivateUsage]
        provider._planning_snapshot,  # pyright: ignore[reportPrivateUsage]
        request,
    ).context


def test_compiler_keeps_markdown_prompt_and_preflight_typography_in_sync(
    tmp_path: Path,
) -> None:
    compiled = DeckContractCompiler().compile(_context(tmp_path))

    roles = compiled.typography.model_dump(mode="json")
    for role, size in roles.items():
        assert f"| {role} | {size} px |" in compiled.design_spec_markdown
        assert f"- {role}: {size}" in compiled.spec_lock_markdown
    constraints = compiled.authoring_constraints()
    typography = constraints["typography"]
    assert isinstance(typography, dict)
    assert typography["roles_px"] == roles
    assert set(typography["allowed_recurring_sizes_px"]) == set(roles.values())


def test_compiler_preflight_rejects_undeclared_recurring_size(
    tmp_path: Path,
) -> None:
    compiled = DeckContractCompiler().compile(_context(tmp_path))
    svg = (
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1280 720">'
        '<text x="80" y="80" font-size="10">A</text>'
        '<text x="80" y="120" font-size="10">B</text>'
        '<text x="80" y="160" font-size="10">C</text>'
        '</svg>'
    )

    issues = compiled.preflight_svg_typography(svg)

    assert len(issues) == 1
    assert issues[0].font_size_px == 10
    assert issues[0].occurrence_count == 3


def test_compiler_preflight_accepts_all_named_role_sizes(tmp_path: Path) -> None:
    compiled = DeckContractCompiler().compile(_context(tmp_path))
    texts = "".join(
        f'<text x="80" y="{80 + index * 40}" font-size="{size}">{role}</text>'
        for index, (role, size) in enumerate(
            compiled.typography.model_dump(mode="json").items()
        )
    )
    svg = f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1280 720">{texts}</svg>'

    assert compiled.preflight_svg_typography(svg) == ()
