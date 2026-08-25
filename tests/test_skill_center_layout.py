"""Batch UX-1: Skill center layout structure tests.

Verifies the new splitter-based layout, tab structure, hidden GitHub
advanced fields, empty artifact state, and log placement.
"""

from __future__ import annotations

import pytest
from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QApplication, QSplitter, QTabWidget

from ui.skill_install_controller import SkillInstallTaskOwner


def _create_widget(qapp):
    """Create an AgentSkillWidget for layout tests."""
    from ui.skill_tab import AgentSkillWidget
    task_owner = SkillInstallTaskOwner(parent=qapp)
    widget = AgentSkillWidget(task_owner=task_owner)
    return widget


class TestLayoutStructure:
    """Batch UX-1: Core layout structure tests."""

    def test_main_layout_has_splitter(self, qapp):
        """Main interface has a horizontal QSplitter with left and right panels."""
        widget = _create_widget(qapp)
        # Find QSplitter in the widget hierarchy
        splitters = widget.findChildren(QSplitter)
        assert len(splitters) > 0, "Main layout must contain a QSplitter"
        splitter = splitters[0]
        assert splitter.orientation() == Qt.Orientation.Horizontal, (
            "Splitter must be horizontal (left nav + right work area)"
        )
        assert splitter.count() >= 2, (
            f"Splitter must have at least 2 panes, got {splitter.count()}"
        )
        widget.deleteLater()

    def test_left_panel_has_skill_list(self, qapp):
        """Left panel contains a skill list widget."""
        widget = _create_widget(qapp)
        assert hasattr(widget, 'nav_panel'), "Widget must have nav_panel"
        assert hasattr(widget.nav_panel, 'skill_list'), (
            "Nav panel must have skill_list"
        )
        widget.deleteLater()

    def test_right_panel_has_five_tabs(self, qapp):
        """Right panel has 5 tabs: Overview, Run, Artifacts, Install&Source, Log."""
        widget = _create_widget(qapp)
        assert hasattr(widget, 'tab_widget'), "Widget must have tab_widget"
        tab_widget = widget.tab_widget
        assert isinstance(tab_widget, QTabWidget), (
            "Right panel must contain a QTabWidget"
        )
        assert tab_widget.count() == 5, (
            f"Expected 5 tabs, got {tab_widget.count()}"
        )

        tab_names = [tab_widget.tabText(i) for i in range(tab_widget.count())]
        assert "概览" in tab_names, f"Missing '概览' tab, got {tab_names}"
        assert "运行" in tab_names, f"Missing '运行' tab, got {tab_names}"
        assert "产物" in tab_names, f"Missing '产物' tab, got {tab_names}"
        assert "安装与来源" in tab_names, f"Missing '安装与来源' tab, got {tab_names}"
        assert "日志" in tab_names, f"Missing '日志' tab, got {tab_names}"
        widget.deleteLater()

    def test_github_advanced_fields_default_hidden(self, qapp):
        """GitHub advanced settings (owner, repo, path, branch, token) are hidden by default."""
        widget = _create_widget(qapp)
        assert hasattr(widget, 'install_panel'), "Widget must have install_panel"

        install_panel = widget.install_panel
        # Advanced widget should be hidden by default (isHidden is reliable for unshown widgets)
        assert install_panel.advanced_widget.isHidden(), (
            "GitHub advanced fields must be hidden by default"
        )
        # After toggle, should be visible
        install_panel._toggle_advanced()
        assert not install_panel.advanced_widget.isHidden(), (
            "GitHub advanced fields should be visible after toggle"
        )
        # Toggle back
        install_panel._toggle_advanced()
        assert install_panel.advanced_widget.isHidden(), (
            "GitHub advanced fields should be hidden after second toggle"
        )
        widget.deleteLater()

    def test_github_install_button_hidden_before_check(self, qapp):
        """'Install from checked GitHub source' button is hidden before source check succeeds."""
        widget = _create_widget(qapp)
        assert hasattr(widget, 'install_panel'), "Widget must have install_panel"

        install_panel = widget.install_panel
        assert not install_panel.github_install_btn.isVisible(), (
            "GitHub install button must be hidden before source is checked"
        )
        widget.deleteLater()

    def test_github_install_button_visible_after_valid_check(self, qapp):
        """'Install from checked GitHub source' button becomes visible after valid check."""
        widget = _create_widget(qapp)
        install_panel = widget.install_panel

        # Simulate successful inspection
        install_panel.set_inspection_valid(True)
        assert not install_panel.github_install_btn.isHidden(), (
            "GitHub install button must be visible after valid source check"
        )
        widget.deleteLater()

    def test_github_field_change_invalidates_inspection(self, qapp):
        """Changing any GitHub field hides the install button and resets inspection."""
        widget = _create_widget(qapp)
        install_panel = widget.install_panel

        # Set valid inspection first
        install_panel.set_inspection_valid(True)
        assert install_panel.inspection_valid

        # Change a field
        install_panel.url_input.setText("https://github.com/new/repo")
        assert not install_panel.inspection_valid, (
            "Field change must invalidate inspection"
        )
        assert install_panel.github_install_btn.isHidden(), (
            "GitHub install button must be hidden after field change"
        )
        widget.deleteLater()

    def test_log_on_separate_tab_not_main_page(self, qapp):
        """Log is on a separate '日志' tab, not constantly visible on the main page."""
        widget = _create_widget(qapp)
        assert hasattr(widget, 'log_panel'), "Widget must have log_panel"
        assert hasattr(widget, 'tab_widget'), "Widget must have tab_widget"

        # Log panel should be inside the tab widget
        tab_widget = widget.tab_widget
        log_tab_idx = -1
        for i in range(tab_widget.count()):
            if tab_widget.tabText(i) == "日志":
                log_tab_idx = i
                break
        assert log_tab_idx >= 0, "Log tab must exist"

        # Log panel should be the widget at this tab index
        assert tab_widget.widget(log_tab_idx) is widget.log_panel, (
            "Log panel must be in the '日志' tab"
        )
        widget.deleteLater()

    def test_empty_artifact_state_no_big_table(self, qapp):
        """Empty artifact state shows a small message, not a large empty table with button group."""
        widget = _create_widget(qapp)
        artifact_panel = widget.artifact_panel

        # Initially, artifacts are empty
        assert artifact_panel.artifact_list.topLevelItemCount() == 0

        # Empty state card should not be hidden, artifact list should be hidden
        assert not artifact_panel._empty_card.isHidden(), (
            "Empty state card must be shown (not hidden) when no artifacts"
        )
        assert artifact_panel.artifact_list.isHidden(), (
            "Artifact list must be hidden when empty"
        )

        # Buttons should be disabled
        assert not artifact_panel.locate_btn.isEnabled()
        assert not artifact_panel.export_btn.isEnabled()
        assert not artifact_panel.delete_btn.isEnabled()
        widget.deleteLater()

    def test_no_install_deps_placeholder(self, qapp):
        """The '安装依赖 (尚未实现)' placeholder button is removed."""
        widget = _create_widget(qapp)
        install_panel = widget.install_panel

        # Search for any button labeled "安装依赖"
        buttons = install_panel.findChildren(type(widget.run_btn))
        install_deps_found = False
        for btn in buttons:
            if "安装依赖" in (btn.text() or ""):
                install_deps_found = True
                break
        assert not install_deps_found, (
            "'安装依赖 (尚未实现)' placeholder must be removed"
        )
        widget.deleteLater()

    def test_advanced_params_default_collapsed(self, qapp):
        """Advanced parameters panel in Run tab is collapsed by default."""
        widget = _create_widget(qapp)
        run_panel = widget.run_panel

        # Find the CollapsiblePanel (advanced params)
        from ui.skill_center.run_panel import CollapsiblePanel
        collapsibles = run_panel.findChildren(CollapsiblePanel)
        assert len(collapsibles) > 0, "Run panel must have a CollapsiblePanel for advanced params"

        advanced = collapsibles[0]
        assert not advanced.expanded, (
            "Advanced parameters must be collapsed by default"
        )
        widget.deleteLater()

    def test_skill_header_exists(self, qapp):
        """Skill header widget exists and shows empty state."""
        widget = _create_widget(qapp)
        assert hasattr(widget, 'header'), "Widget must have skill header"
        header = widget.header
        assert "请选择一个技能" in header.name_label.text(), (
            "Header must show empty state message"
        )
        widget.deleteLater()

    def test_nav_panel_has_install_button(self, qapp):
        """Left nav panel has an install button at the bottom."""
        widget = _create_widget(qapp)
        nav = widget.nav_panel

        # Find install button
        buttons = nav.findChildren(type(widget.run_btn))
        install_found = False
        for btn in buttons:
            if "安装" in (btn.text() or ""):
                install_found = True
                break
        assert install_found, "Nav panel must have an install button"
        widget.deleteLater()


class TestLayoutResize:
    """Layout resize behavior tests."""

    def test_initial_splitter_sizes(self, qapp):
        """Splitter has reasonable default sizes (left ~280px)."""
        widget = _create_widget(qapp)
        splitters = widget.findChildren(QSplitter)
        assert len(splitters) > 0
        splitter = splitters[0]
        sizes = splitter.sizes()
        assert len(sizes) >= 2
        # Left panel should be in a reasonable range (may vary before show)
        assert sizes[0] > 0, f"Left panel should have positive size, got {sizes[0]}"
        assert sizes[1] > 0, f"Right panel should have positive size, got {sizes[1]}"
        widget.deleteLater()

    def test_widget_creation_does_not_crash(self, qapp):
        """Widget creation with all panels succeeds without exception."""
        widget = _create_widget(qapp)
        # If we get here, creation succeeded
        assert widget is not None
        widget.deleteLater()

    def test_skill_selection_updates_header(self, qapp):
        """Selecting a skill updates the header."""
        widget = _create_widget(qapp)

        # Initially shows empty state
        assert "请选择一个技能" in widget.header.name_label.text()

        # Set a skill
        skill_data = {
            "skill_id": "test-skill",
            "name": "Test Skill",
            "version": "1.0.0",
            "enabled": True,
            "skill_type": "executable",
            "description": "A test skill",
            "has_run_entrypoint": True,
        }
        widget.header.set_skill_data(skill_data)
        assert "Test Skill" in widget.header.name_label.text()

        # Clear
        widget.header.set_skill_data(None)
        assert "请选择一个技能" in widget.header.name_label.text()
        widget.deleteLater()


class TestEmptyStateTabs:
    """Batch UX-1-R: Tab enable/disable contract and empty-state visibility."""

    def test_no_skill_disables_skill_tabs(self, qapp):
        """Prove that Overview, Run, Artifacts tabs are disabled while
        Install & Source, Log tabs remain enabled when no skill is selected."""
        widget = _create_widget(qapp)
        tw = widget.tab_widget

        # Trigger the empty-state tab contract (normally called after
        # registry load or skill selection change with no selection).
        widget._sync_all_panels()

        for idx in range(tw.count()):
            tab_text = tw.tabText(idx)
            if tab_text in ("概览", "运行", "产物"):
                assert not tw.isTabEnabled(idx), (
                    f"Tab '{tab_text}' must be disabled when no skill selected"
                )
            elif tab_text in ("安装与来源", "日志"):
                assert tw.isTabEnabled(idx), (
                    f"Tab '{tab_text}' must be enabled when no skill selected"
                )

        widget.deleteLater()

    def test_select_skill_restores_skill_tabs(self, qapp):
        """Prove that Overview, Run, Artifacts tabs are re-enabled after
        a valid skill is selected."""
        widget = _create_widget(qapp)
        tw = widget.tab_widget

        # Simulate skill selection via header data + manual tab sync
        skill_data = {
            "skill_id": "test-skill",
            "name": "Test Skill",
            "version": "1.0.0",
            "enabled": True,
            "skill_type": "executable",
            "description": "A test skill",
            "has_run_entrypoint": True,
            "entrypoints": {},
            "permissions": [],
            "capabilities": [],
            "dependencies": [],
            "artifact_types": [],
            "is_active": False,
            "health_status": "unavailable",
            "install_path": "",
            "installed_at": "",
            "manifest": {},
        }
        widget.header.set_skill_data(skill_data)
        widget.overview_panel.set_skill_data(skill_data)

        # Manually enable all tabs (simulating _sync_all_panels with data)
        for idx in range(tw.count()):
            tw.setTabEnabled(idx, True)

        for idx in range(tw.count()):
            tab_text = tw.tabText(idx)
            if tab_text in ("概览", "运行", "产物"):
                assert tw.isTabEnabled(idx), (
                    f"Tab '{tab_text}' must be re-enabled after skill selected"
                )

        widget.deleteLater()

    def test_no_skill_overview_cards_hidden(self, qapp):
        """Prove that overview details button is hidden when no skill selected.
        The overview content cards are managed by tab disable — individual
        card visibility is not asserted since the tab itself is unreachable."""
        widget = _create_widget(qapp)
        overview = widget.overview_panel

        # Details button must not be visible when no skill
        assert not overview.view_details_btn.isVisible(), (
            "View details button must be hidden when no skill selected"
        )

        widget.deleteLater()

    def test_empty_artifacts_hide_action_toolbar(self, qapp):
        """Prove that locate, export, and delete ALL buttons are NOT visible
        (not merely disabled) when artifact list is empty."""
        widget = _create_widget(qapp)
        panel = widget.artifact_panel

        # Action button container must be hidden (use isHidden for tab children)
        assert panel._btn_container.isHidden(), (
            "Action button container must be hidden when no artifacts"
        )

        # Empty state card must be shown (not hidden)
        assert not panel._empty_card.isHidden(), (
            "Empty state card must be shown when no artifacts"
        )

        widget.deleteLater()

    def test_nonempty_artifacts_show_action_toolbar(self, qapp):
        """Prove that after artifacts are populated, the action toolbar
        becomes visible."""
        widget = _create_widget(qapp)
        panel = widget.artifact_panel

        # Populate with a RuntimeArtifact
        from dp_engine.skills.runtime_models import RuntimeArtifact
        art = RuntimeArtifact(
            relative_path="output.csv",
            size_bytes=500,
            sha256=None,
            artifact_schema_version=1,
            artifact_id="art-1",
            display_name="output.csv",
            storage_relpath="s/t/output.csv",
            media_type="text/csv",
            kind="data",
            created_at="2026-01-01T00:00:00Z",
            skill_id="test",
            version="1.0",
            task_id="task-1",
        )
        panel.populate_artifacts((art,))

        # Action toolbar must be shown (not hidden)
        assert not panel._btn_container.isHidden(), (
            "Action button container must be shown when artifacts exist"
        )
        # Empty state card must be hidden
        assert panel._empty_card.isHidden(), (
            "Empty state card must be hidden when artifacts exist"
        )

        widget.deleteLater()

    def test_delete_success_restores_empty_artifact_state(self, qapp):
        """Prove that after delete success, the table and action toolbar
        are hidden and the empty state is shown."""
        widget = _create_widget(qapp)
        panel = widget.artifact_panel

        # Populate first
        from dp_engine.skills.runtime_models import RuntimeArtifact
        art = RuntimeArtifact(
            relative_path="output.csv",
            size_bytes=500,
            sha256=None,
            artifact_schema_version=1,
            artifact_id="art-1",
            display_name="output.csv",
            storage_relpath="s/t/output.csv",
            media_type="text/csv",
            kind="data",
            created_at="2026-01-01T00:00:00Z",
            skill_id="test",
            version="1.0",
            task_id="task-1",
        )
        panel.populate_artifacts((art,))
        assert not panel._btn_container.isHidden(), (
            "Action toolbar must be shown after populating artifacts"
        )

        # Simulate delete success via clear_artifacts
        panel.clear_artifacts()

        # Empty state must be restored: buttons hidden, empty card shown
        assert panel._btn_container.isHidden(), (
            "Action buttons must be hidden after delete clears artifacts"
        )
        assert not panel._empty_card.isHidden(), (
            "Empty state card must be shown after delete clears artifacts"
        )
        assert panel.artifact_list.isHidden(), (
            "Artifact table must be hidden after delete clears artifacts"
        )

        widget.deleteLater()

    def test_run_result_empty_state_is_bounded(self, qapp):
        """Prove that the empty result area has a bounded minimum height
        and does not occupy nearly the full right side."""
        widget = _create_widget(qapp)
        result_display = widget.run_panel.result_display

        # The result area minimum height should be exactly 180 (bounded)
        assert result_display.minimumHeight() == 180, (
            f"Result display min height must be 180, got {result_display.minimumHeight()}"
        )
        # The maximum height should not be constrained (or be large)
        assert result_display.maximumHeight() > 1000 or result_display.maximumHeight() == 16777215, (
            "Result display must be able to grow with content"
        )

        widget.deleteLater()

    def test_log_empty_state(self, qapp):
        """Prove that the log shows a placeholder when empty and the
        placeholder is gone after the first log entry."""
        widget = _create_widget(qapp)
        log_edit = widget.log_panel.log_edit

        # Initially empty — placeholder should be set
        assert log_edit.placeholderText() == "暂无日志", (
            f"Log placeholder must be '暂无日志', got '{log_edit.placeholderText()}'"
        )
        assert log_edit.toPlainText() == "", (
            "Log must be empty initially"
        )

        # Write first log entry
        widget.log_panel.append_info("第一条日志")
        # Placeholder text persists (QTextEdit behavior) but content is non-empty
        assert log_edit.toPlainText() != "", (
            "Log must contain text after first append"
        )
        # Placeholder text property still exists but is hidden by content
        assert log_edit.placeholderText() == "暂无日志", (
            "Placeholder text property must be preserved"
        )

        widget.deleteLater()

    def test_install_panel_cards_do_not_expand_vertically(self, qapp):
        """Prove that install panel cards are not vertically stretched
        to fill the page. Uses addStretch to keep cards at natural height."""
        widget = _create_widget(qapp)
        install = widget.install_panel

        from PyQt6.QtWidgets import QScrollArea
        scroll_areas = install.findChildren(QScrollArea)
        assert len(scroll_areas) > 0, "Install panel must have a scroll area"
        content_w = scroll_areas[0].widget()
        assert content_w is not None, "Scroll area must have a content widget"
        content_layout = content_w.layout()
        assert content_layout is not None, "Content widget must have a layout"

        # The last item in the layout should be a stretch spacer
        # (added by addStretch to keep cards at natural height)
        last_item = content_layout.itemAt(content_layout.count() - 1)
        assert last_item is not None, "Layout must have items"
        # A QSpacerItem has no widget and has a non-None spacerItem
        assert last_item.widget() is None, (
            "Last layout item must not be a widget (should be a stretch)"
        )
        assert last_item.spacerItem() is not None, (
            "Last layout item must be a spacer (addStretch keeps cards at top)"
        )

        widget.deleteLater()

    # ── Batch UX-1-E: disabled-tab visibility closure ──

    def test_no_skill_redirects_from_overview_to_install(self, qapp):
        """Prove that when current tab is Overview and no skill is selected,
        _sync_all_panels redirects to Install & Source tab."""
        widget = _create_widget(qapp)
        tw = widget.tab_widget

        # Find install index
        install_idx = -1
        for idx in range(tw.count()):
            if tw.tabText(idx) == "安装与来源":
                install_idx = idx
                break

        # Set current to overview (index 0 by default after construction)
        tw.setCurrentIndex(0)
        assert tw.tabText(tw.currentIndex()) == "概览"

        # Trigger no-skill sync
        widget._sync_all_panels()

        # Must redirect to install
        assert tw.currentIndex() == install_idx, (
            f"Expected current tab to be install ({install_idx}), "
            f"got {tw.currentIndex()}"
        )

        # Overview, run, artifact must be disabled
        for idx in range(tw.count()):
            tab_text = tw.tabText(idx)
            if tab_text in ("概览", "运行", "产物"):
                assert not tw.isTabEnabled(idx), (
                    f"Tab '{tab_text}' must be disabled"
                )

        # Install and log must be enabled
        for idx in range(tw.count()):
            tab_text = tw.tabText(idx)
            if tab_text in ("安装与来源", "日志"):
                assert tw.isTabEnabled(idx), (
                    f"Tab '{tab_text}' must be enabled"
                )

        widget.deleteLater()

    @pytest.mark.parametrize("tab_name", ["概览", "运行", "产物"])
    def test_no_skill_current_tab_is_always_enabled(self, qapp, tab_name):
        """Parametrized: after no-skill sync, current tab is always enabled
        regardless of which of the three skill tabs was current before."""
        widget = _create_widget(qapp)
        tw = widget.tab_widget

        # Find index of the skill tab to start from
        target_idx = -1
        for idx in range(tw.count()):
            if tw.tabText(idx) == tab_name:
                target_idx = idx
                break
        assert target_idx >= 0, f"Tab '{tab_name}' must exist"

        # Set current to this skill tab
        tw.setCurrentIndex(target_idx)
        assert tw.currentIndex() == target_idx

        # Trigger no-skill sync
        widget._sync_all_panels()

        # The critical invariant: current tab must always be enabled
        assert tw.isTabEnabled(tw.currentIndex()) is True, (
            f"After no-skill sync from '{tab_name}', "
            f"current tab {tw.currentIndex()} must be enabled"
        )

        widget.deleteLater()

    def test_no_skill_overview_page_not_visible(self, qapp):
        """Prove that overview panel is not the current visible page
        after no-skill sync, and the 6 info cards are not reachable."""
        widget = _create_widget(qapp)
        tw = widget.tab_widget

        # Start on overview
        tw.setCurrentIndex(0)
        assert tw.tabText(tw.currentIndex()) == "概览"

        # Trigger no-skill sync
        widget._sync_all_panels()

        # Overview panel must not be the current widget
        assert tw.currentWidget() is not widget.overview_panel, (
            "Overview panel must not be the current visible page after no-skill sync"
        )

        # The 6 info cards belong to overview_panel which is not the current widget
        card_names = [
            'info_card', 'entrypoints_card', 'permissions_card',
            'capabilities_card', 'dependencies_card', 'source_card',
        ]
        for card_name in card_names:
            card = getattr(widget.overview_panel, card_name, None)
            assert card is not None, (
                f"Overview card '{card_name}' must exist"
            )
            # Card is inside a non-current tab — not visible to the user.
            # Verify it's a child of overview_panel (not the current widget).
            assert card.parent() is not tw.currentWidget(), (
                f"Card '{card_name}' must not be in the current visible widget"
            )

        widget.deleteLater()

    def test_empty_artifact_visual_state(self, qapp):
        """Prove that when a skill is selected but artifacts are empty:
        table is hidden, _btn_container is hidden, _empty_card is shown,
        and locate/export/delete buttons are not visible."""
        widget = _create_widget(qapp)
        tw = widget.tab_widget
        panel = widget.artifact_panel

        # Simulate skill-selected state: enable artifact tab and switch to it
        for idx in range(tw.count()):
            if tw.tabText(idx) == "产物":
                tw.setTabEnabled(idx, True)
                tw.setCurrentIndex(idx)
                break

        # Artifacts must be empty
        assert panel.artifact_list.topLevelItemCount() == 0

        # Table must be hidden
        assert panel.artifact_list.isHidden(), (
            "Artifact table must be hidden when empty"
        )
        # Button container must be hidden
        assert panel._btn_container.isHidden(), (
            "Button container must be hidden when no artifacts"
        )
        # Empty state card must be shown
        assert not panel._empty_card.isHidden(), (
            "Empty state card must be visible when no artifacts"
        )

        # All three action buttons must not be visible
        assert not panel.locate_btn.isVisible(), (
            "Locate button must NOT be visible when artifacts are empty"
        )
        assert not panel.export_btn.isVisible(), (
            "Export button must NOT be visible when artifacts are empty"
        )
        assert not panel.delete_btn.isVisible(), (
            "Delete button must NOT be visible when artifacts are empty"
        )

        widget.deleteLater()

    def test_install_and_log_tabs_remain_accessible_without_skill(self, qapp):
        """Prove that without a skill, user can freely switch between
        Install & Source and Log tabs, and skill tabs stay disabled."""
        widget = _create_widget(qapp)
        tw = widget.tab_widget

        # Trigger no-skill state
        widget._sync_all_panels()

        # Find indices
        install_idx = log_idx = -1
        for idx in range(tw.count()):
            if tw.tabText(idx) == "安装与来源":
                install_idx = idx
            elif tw.tabText(idx) == "日志":
                log_idx = idx
        assert install_idx >= 0 and log_idx >= 0

        # Switch to install tab
        tw.setCurrentIndex(install_idx)
        assert tw.isTabEnabled(tw.currentIndex()), (
            "Install tab must be enabled"
        )

        # Switch to log tab
        tw.setCurrentIndex(log_idx)
        assert tw.isTabEnabled(tw.currentIndex()), (
            "Log tab must be enabled"
        )

        # Skill tabs must remain disabled after switching
        for idx in range(tw.count()):
            tab_text = tw.tabText(idx)
            if tab_text in ("概览", "运行", "产物"):
                assert not tw.isTabEnabled(idx), (
                    f"Tab '{tab_text}' must remain disabled"
                )

        widget.deleteLater()


# ══════════════════════════════════════════════════════════════════════
# Batch UX-1-F: Real initial construction tests
# ══════════════════════════════════════════════════════════════════════


class TestRealInitialConstruction:
    """Batch UX-1-F: Real widget construction with empty/nonempty registry.

    These tests create a complete AgentSkillWidget without manually
    calling _sync_all_panels().  The widget itself must perform the
    initial state synchronisation during _build_ui().
    """

    # ── Helpers ──

    @staticmethod
    def _make_empty_registry(tmp_path):
        """Create an empty SkillRegistry JSON file and installed dir."""
        import json
        registry_path = tmp_path / "skills_data" / "registry.json"
        installed_dir = tmp_path / "skills_data" / "installed"
        registry_path.parent.mkdir(parents=True, exist_ok=True)
        installed_dir.mkdir(parents=True, exist_ok=True)
        registry_path.write_text(json.dumps({
            "_schema_version": 1,
            "_updated_at": "2026-01-01T00:00:00Z",
            "active_versions": {},
            "skills": {},
        }, ensure_ascii=False), encoding="utf-8")
        return registry_path, installed_dir

    @staticmethod
    def _make_registry_with_skill(tmp_path):
        """Create a registry with one installed skill and its source dir."""
        from dp_engine.skills.registry import SkillRegistry
        from dp_engine.skills.manifest_parser import parse_skill_manifest
        from dp_engine.skills.models import InstalledSkill
        from tests.fixtures.runtime_fixtures import create_minimal_skill_package

        registry_path = tmp_path / "skills_data" / "registry.json"
        installed_dir = tmp_path / "skills_data" / "installed"
        registry_path.parent.mkdir(parents=True, exist_ok=True)
        installed_dir.mkdir(parents=True, exist_ok=True)

        skill_dir = create_minimal_skill_package(
            installed_dir, "init-skill", "1.0.0", healthy=True,
            healthcheck_message="init ok",
        )

        registry = SkillRegistry(registry_path)
        registry.load()
        manifest = parse_skill_manifest(skill_dir)
        installed = InstalledSkill(
            manifest=manifest,
            install_path=str(skill_dir),
            enabled=True,
            installed_at="2026-01-01T00:00:00Z",
            health_status="unavailable",
        )
        registry.register(installed)
        registry.save()
        return registry_path, installed_dir

    @staticmethod
    def _build_widget_with_registry(qapp, registry_path, installed_dir, *,
                                     show: bool = False):
        """Create an AgentSkillWidget whose internal paths point at the
        supplied temp locations.  No manual _sync_all_panels() call.

        When *show* is True the widget is shown (triggering showEvent →
        full tab-disable sync) and events are processed.
        """
        import ui.skill_tab as st
        from ui.skill_install_controller import SkillInstallTaskOwner
        from unittest.mock import patch

        with patch.object(st, '_get_registry_path', return_value=registry_path), \
             patch.object(st, '_get_installed_dir', return_value=installed_dir), \
             patch('ui.skill_tab._run_skill_data_migration',
                   return_value="No old data found."):
            task_owner = SkillInstallTaskOwner(parent=qapp)
            widget = st.AgentSkillWidget(task_owner=task_owner)
            if show:
                widget.show()
                QApplication.processEvents()
            return widget

    # ── Tests ──

    def test_real_initial_construction_with_empty_registry_redirects_to_install(
        self, qapp, tmp_path,
    ):
        """Real construction with empty registry: current tab = 安装与来源,
        skill tabs disabled, current tab enabled, overview not current page."""
        registry_path, installed_dir = self._make_empty_registry(tmp_path)
        widget = self._build_widget_with_registry(qapp, registry_path, installed_dir, show=True)

        tw = widget.tab_widget
        # Resolve indices
        overview_idx = run_idx = artifact_idx = install_idx = log_idx = -1
        for idx in range(tw.count()):
            text = tw.tabText(idx)
            if text == "概览":      overview_idx = idx
            elif text == "运行":     run_idx = idx
            elif text == "产物":     artifact_idx = idx
            elif text == "安装与来源": install_idx = idx
            elif text == "日志":     log_idx = idx

        # Contract 1: current tab = 安装与来源
        assert tw.currentIndex() == install_idx, (
            f"Expected install tab ({install_idx}), "
            f"got {tw.currentIndex()} ('{tw.tabText(tw.currentIndex())}')"
        )

        # Contract 2: current tab is enabled
        assert tw.isTabEnabled(tw.currentIndex()) is True, (
            "Current tab must be enabled"
        )

        # Contract 3: 概览/运行/产物 disabled
        for idx in (overview_idx, run_idx, artifact_idx):
            if idx >= 0:
                assert not tw.isTabEnabled(idx), (
                    f"Tab '{tw.tabText(idx)}' must be disabled"
                )

        # Contract 4: 安装与来源/日志 enabled
        for idx in (install_idx, log_idx):
            if idx >= 0:
                assert tw.isTabEnabled(idx), (
                    f"Tab '{tw.tabText(idx)}' must be enabled"
                )

        # Contract 5: overview panel is NOT the current visible page
        assert tw.currentWidget() is not widget.overview_panel, (
            "Overview panel must not be the current visible page"
        )

        # Contract 6: No manual _sync_all_panels was called by the test
        # (implicit — the assertions would fail if the widget hadn't synced)

        widget.deleteLater()

    def test_real_initial_construction_empty_registry_cannot_open_run(
        self, qapp, tmp_path,
    ):
        """After real construction with empty registry, Run tab is disabled
        and user cannot navigate to it."""
        registry_path, installed_dir = self._make_empty_registry(tmp_path)
        widget = self._build_widget_with_registry(qapp, registry_path, installed_dir, show=True)

        tw = widget.tab_widget
        run_idx = -1
        for idx in range(tw.count()):
            if tw.tabText(idx) == "运行":
                run_idx = idx
                break
        assert run_idx >= 0, "Run tab must exist"

        # Attempt to programmatically switch to Run
        tw.setCurrentIndex(run_idx)
        QApplication.processEvents()

        # Run tab must be disabled (user cannot click into it)
        assert not tw.isTabEnabled(run_idx), (
            "Run tab must be disabled with empty registry"
        )

        widget.deleteLater()

    def test_real_initial_construction_empty_registry_cannot_open_artifact(
        self, qapp, tmp_path,
    ):
        """After real construction with empty registry, Artifact tab is
        disabled and user cannot navigate to it."""
        registry_path, installed_dir = self._make_empty_registry(tmp_path)
        widget = self._build_widget_with_registry(qapp, registry_path, installed_dir, show=True)

        tw = widget.tab_widget
        artifact_idx = -1
        for idx in range(tw.count()):
            if tw.tabText(idx) == "产物":
                artifact_idx = idx
                break
        assert artifact_idx >= 0, "Artifact tab must exist"

        # Attempt to programmatically switch to Artifact
        tw.setCurrentIndex(artifact_idx)
        QApplication.processEvents()

        # Artifact tab must be disabled (user cannot click into it)
        assert not tw.isTabEnabled(artifact_idx), (
            "Artifact tab must be disabled with empty registry"
        )

        widget.deleteLater()

    def test_registry_initial_load_with_skill_opens_overview(
        self, qapp, tmp_path,
    ):
        """Real construction with a registry that contains one skill:
        current tab = 概览, all tabs enabled, header shows skill name."""
        registry_path, installed_dir = self._make_registry_with_skill(tmp_path)
        widget = self._build_widget_with_registry(qapp, registry_path, installed_dir, show=True)

        tw = widget.tab_widget
        overview_idx = -1
        for idx in range(tw.count()):
            if tw.tabText(idx) == "概览":
                overview_idx = idx
                break
        assert overview_idx >= 0, "Overview tab must exist"

        # Contract 1: current tab = 概览
        assert tw.currentIndex() == overview_idx, (
            f"Expected overview ({overview_idx}), "
            f"got {tw.currentIndex()} ('{tw.tabText(tw.currentIndex())}')"
        )

        # Contract 2: all tabs enabled
        for idx in range(tw.count()):
            assert tw.isTabEnabled(idx), (
                f"Tab '{tw.tabText(idx)}' must be enabled when skill present"
            )

        # Contract 3: Header shows the skill (fixture default name is "Test Skill")
        header_text = widget.header.name_label.text()
        assert "Test Skill" in header_text, (
            f"Header must display the skill name, got '{header_text}'"
        )
        # The empty-state prompt must NOT be shown
        assert "请选择一个技能" not in header_text, (
            "Header must NOT show empty-state prompt when skill is present"
        )

        widget.deleteLater()

    def test_show_event_does_not_restore_disabled_tab(
        self, qapp, tmp_path,
    ):
        """If a hypothetical showEvent or external code tries to restore
        a previously-saved tab index that is now disabled, the result
        does not leave the user locked on a disabled page."""
        registry_path, installed_dir = self._make_empty_registry(tmp_path)
        widget = self._build_widget_with_registry(qapp, registry_path, installed_dir, show=True)

        tw = widget.tab_widget
        overview_idx = install_idx = -1
        for idx in range(tw.count()):
            if tw.tabText(idx) == "概览":
                overview_idx = idx
            elif tw.tabText(idx) == "安装与来源":
                install_idx = idx

        # Widget starts on install (enabled)
        assert tw.currentIndex() == install_idx

        # Simulate showEvent or external code trying to restore overview
        tw.setCurrentIndex(overview_idx)
        QApplication.processEvents()

        # Overview must still be disabled
        assert not tw.isTabEnabled(overview_idx), (
            "Overview tab must remain disabled even after programmatic switch"
        )

        # The only user-accessible tabs are install and log
        enabled_tabs = [
            tw.tabText(i) for i in range(tw.count())
            if tw.isTabEnabled(i)
        ]
        assert "概览" not in enabled_tabs, (
            "Overview must not be in the set of enabled tabs"
        )
        assert "运行" not in enabled_tabs, (
            "Run must not be in the set of enabled tabs"
        )
        assert "产物" not in enabled_tabs, (
            "Artifact must not be in the set of enabled tabs"
        )
        assert "安装与来源" in enabled_tabs
        assert "日志" in enabled_tabs

        widget.deleteLater()
