"""Batch UX-1: Skill center interaction tests.

Verifies skill selection flows, button state transitions,
detail dialog structure, parameter toggling, and lifecycle safety.
"""

from __future__ import annotations

import pytest
from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QApplication, QMessageBox, QTabWidget
from unittest.mock import patch

from ui.skill_install_controller import SkillInstallTaskOwner


def _create_widget(qapp):
    """Create an AgentSkillWidget for interaction tests."""
    from ui.skill_tab import AgentSkillWidget
    task_owner = SkillInstallTaskOwner(parent=qapp)
    widget = AgentSkillWidget(task_owner=task_owner)
    return widget


class TestSkillSelection:
    """Skill selection updates header, overview, and panel states."""

    def test_header_empty_state(self, qapp):
        """Header shows empty state when no skill is selected."""
        widget = _create_widget(qapp)
        assert "请选择一个技能" in widget.header.name_label.text()
        assert not widget.header.run_btn.isVisible()
        assert not widget.header.more_btn.isVisible()
        widget.deleteLater()

    def test_header_updated_with_skill_data(self, qapp):
        """Header shows skill info when data is set."""
        widget = _create_widget(qapp)
        data = {
            "skill_id": "my-skill",
            "name": "My Skill",
            "version": "2.0.0",
            "enabled": True,
            "skill_type": "executable",
            "description": "Does something useful",
            "has_run_entrypoint": True,
        }
        widget.header.set_skill_data(data)
        assert "My Skill" in widget.header.name_label.text()
        assert not widget.header.run_btn.isHidden()
        assert not widget.header.more_btn.isHidden()
        assert not widget.header.status_badge.isHidden()
        widget.deleteLater()

    def test_skill_switch_clears_artifacts(self, qapp):
        """Switching skills clears the artifact state."""
        widget = _create_widget(qapp)

        # Populate artifact list first
        from dp_engine.skills.runtime_models import RuntimeArtifact
        art = RuntimeArtifact(
            relative_path="test.png",
            size_bytes=1000,
            sha256=None,
            artifact_schema_version=1,
            artifact_id="art-1",
            display_name="test.png",
            storage_relpath="s/t/art_test.png",
            media_type="image/png",
            kind="chart",
            created_at="2026-01-01T00:00:00Z",
            skill_id="old-skill",
            version="1.0.0",
            task_id="old-task",
        )
        widget._artifact_skill_id = "old-skill"
        widget._artifact_task_id = "old-task"
        widget._populate_artifact_list((art,))
        assert widget.artifact_panel.has_items

        # Simulate skill selection change
        widget._on_skill_selection_changed()
        assert not widget.artifact_panel.has_items
        assert widget._artifact_skill_id is None
        assert widget._artifact_task_id is None
        widget.deleteLater()


class TestRunButtonState:
    """Run button enable/disable logic in new layout."""

    def test_run_button_disabled_initially(self, qapp):
        """Run button starts disabled when no skill is selected."""
        widget = _create_widget(qapp)
        assert not widget.run_btn.isEnabled()
        widget.deleteLater()

    def test_run_button_synced_between_header_and_panel(self, qapp):
        """Run button state is synced between header and run panel."""
        widget = _create_widget(qapp)
        # Both start disabled
        assert not widget.run_panel.run_btn.isEnabled()
        assert not widget.header.run_btn.isEnabled()
        widget.deleteLater()

    def test_running_state_hides_run_shows_stop(self, qapp):
        """During running, run button is hidden and stop button is shown."""
        widget = _create_widget(qapp)

        # Simulate running
        widget.run_panel.set_running_state(True)
        assert widget.run_panel.run_btn.isHidden()
        assert not widget.run_panel.stop_btn.isHidden()

        # Simulate stopped
        widget.run_panel.set_running_state(False)
        widget.deleteLater()

    def test_header_running_state_toggles_buttons(self, qapp):
        """Header toggles between run and stop buttons during running."""
        widget = _create_widget(qapp)

        # Set skill data with run entrypoint
        data = {
            "skill_id": "s",
            "name": "S",
            "version": "1.0",
            "enabled": True,
            "skill_type": "executable",
            "description": "",
            "has_run_entrypoint": True,
        }
        widget.header.set_skill_data(data)

        # Simulate running
        widget.header.set_running_state(True)
        assert not widget.header.stop_btn.isHidden()
        assert widget.header.run_btn.isHidden()

        # Simulate stopped
        widget.header.set_running_state(False)
        assert not widget.header.run_btn.isHidden()
        assert widget.header.stop_btn.isHidden()
        widget.deleteLater()


class TestMoreMenu:
    """More menu (⋮) in skill header."""

    def test_more_menu_contains_expected_actions(self, qapp):
        """More menu contains: set active, toggle enable, details, help, uninstall."""
        widget = _create_widget(qapp)
        menu = widget.header._more_menu
        actions = [a.text() for a in menu.actions() if a.text()]
        assert "设置为活动版本" in actions
        assert "启用 / 禁用" in actions
        assert "查看技能详情" in actions
        assert "使用说明" in actions
        assert "卸载当前版本" in actions
        widget.deleteLater()

    def test_more_menu_hidden_when_no_skill(self, qapp):
        """More menu button is hidden when no skill is selected."""
        widget = _create_widget(qapp)
        assert not widget.header.more_btn.isVisible()
        widget.deleteLater()

    def test_more_menu_visible_when_skill_selected(self, qapp):
        """More menu button is visible when skill is selected."""
        widget = _create_widget(qapp)
        data = {
            "skill_id": "s",
            "name": "S",
            "version": "1.0",
            "enabled": True,
            "skill_type": "executable",
            "description": "",
            "has_run_entrypoint": False,
        }
        widget.header.set_skill_data(data)
        assert not widget.header.more_btn.isHidden()
        widget.deleteLater()


class TestHeaderEmptyStateControls:
    """Batch UX-1-R: Header controls visibility when no skill selected."""

    def test_no_skill_hides_header_controls(self, qapp):
        """Prove that version, description, badge, run, stop and more menu
        are NOT visible when no skill is selected."""
        widget = _create_widget(qapp)
        header = widget.header

        # Empty state: all skill-info controls must be hidden
        assert header.version_label.isHidden(), (
            "Version label must be hidden when no skill selected"
        )
        assert header.description_label.isHidden(), (
            "Description label must be hidden when no skill selected"
        )
        assert header.status_badge.isHidden(), (
            "Status badge must be hidden when no skill selected"
        )
        assert header.type_label.isHidden(), (
            "Type label must be hidden when no skill selected"
        )
        assert header.run_btn.isHidden(), (
            "Run button must be hidden when no skill selected"
        )
        assert header.stop_btn.isHidden(), (
            "Stop button must be hidden when no skill selected"
        )
        assert header.more_btn.isHidden(), (
            "More menu button must be hidden when no skill selected"
        )

        # Empty-state widgets must be shown (not hidden)
        assert not header._empty_subtitle.isHidden(), (
            "Empty subtitle must be shown when no skill selected"
        )
        assert not header._empty_install_btn.isHidden(), (
            "Install button must be shown when no skill selected"
        )
        assert "请选择一个技能" in header.name_label.text(), (
            "Header must show empty state title"
        )

        widget.deleteLater()

    def test_header_controls_restored_with_skill(self, qapp):
        """Prove that version, description, badge, type labels reappear
        when a valid skill is selected."""
        widget = _create_widget(qapp)
        header = widget.header

        data = {
            "skill_id": "test",
            "name": "Test Skill",
            "version": "1.0",
            "enabled": True,
            "skill_type": "executable",
            "description": "A test",
            "has_run_entrypoint": True,
        }
        header.set_skill_data(data)

        # Skill-info controls shown (not hidden)
        assert not header.version_label.isHidden()
        assert not header.description_label.isHidden()
        assert not header.status_badge.isHidden()
        assert not header.type_label.isHidden()
        assert not header.run_btn.isHidden()
        assert not header.more_btn.isHidden()

        # Empty-state widgets hidden
        assert header._empty_subtitle.isHidden()
        assert header._empty_install_btn.isHidden()

        widget.deleteLater()


class TestDetailsDialog:
    """Skill details dialog structure."""

    def test_details_dialog_has_four_tabs(self, qapp):
        """Details dialog contains Manifest, Dependencies, Capabilities, Permissions tabs."""
        from ui.skill_center.details_dialog import DetailsDialog

        data = {
            "skill_id": "test",
            "name": "Test",
            "version": "1.0.0",
            "manifest": {"name": "Test", "version": "1.0.0"},
            "dependencies": ["dep1"],
            "capabilities": ["cap1"],
            "permissions": ["perm1"],
        }
        dlg = DetailsDialog(data)
        tabs = dlg.findChildren(QTabWidget)
        assert len(tabs) > 0
        tab_widget = tabs[0]
        assert tab_widget.count() == 4
        tab_names = [tab_widget.tabText(i) for i in range(tab_widget.count())]
        assert "Manifest" in tab_names
        assert "依赖" in tab_names
        assert "能力" in tab_names
        assert "权限" in tab_names
        dlg.close()

    def test_details_dialog_readonly(self, qapp):
        """All text areas in details dialog are read-only."""
        from ui.skill_center.details_dialog import DetailsDialog
        from PyQt6.QtWidgets import QTextEdit

        data = {
            "skill_id": "test",
            "name": "Test",
            "version": "1.0.0",
            "manifest": {},
            "dependencies": [],
            "capabilities": [],
            "permissions": [],
        }
        dlg = DetailsDialog(data)
        text_edits = dlg.findChildren(QTextEdit)
        for te in text_edits:
            assert te.isReadOnly(), (
                "All text areas in details dialog must be read-only"
            )
        dlg.close()


class TestAdvancedToggle:
    """Collapsible panels behavior."""

    def test_advanced_params_toggle(self, qapp):
        """Advanced parameters can be toggled expanded/collapsed."""
        widget = _create_widget(qapp)
        from ui.skill_center.run_panel import CollapsiblePanel
        collapsibles = widget.run_panel.findChildren(CollapsiblePanel)
        assert len(collapsibles) > 0
        advanced = collapsibles[0]

        assert not advanced.expanded
        advanced._toggle()
        assert advanced.expanded
        advanced._toggle()
        assert not advanced.expanded
        widget.deleteLater()

    def test_github_advanced_toggle(self, qapp):
        """GitHub advanced settings can be toggled."""
        widget = _create_widget(qapp)
        install = widget.install_panel

        assert install.advanced_widget.isHidden()
        install._toggle_advanced()
        assert not install.advanced_widget.isHidden()
        install._toggle_advanced()
        assert install.advanced_widget.isHidden()
        widget.deleteLater()


class TestNavigateToInstall:
    """Navigate to install tab from nav panel."""

    def test_install_button_in_nav_switches_tab(self, qapp):
        """Clicking 'Install' in nav panel switches to the Install tab."""
        widget = _create_widget(qapp)

        # Switch away from install tab
        widget.tab_widget.setCurrentIndex(0)
        assert widget.tab_widget.tabText(widget.tab_widget.currentIndex()) != "安装与来源"

        # Click install in nav
        widget._on_nav_install_requested()
        assert widget.tab_widget.tabText(widget.tab_widget.currentIndex()) == "安装与来源"
        widget.deleteLater()


class TestBackwardCompatibility:
    """Ensure attribute aliases work for existing test compatibility."""

    def test_registry_table_exists(self, qapp):
        """Hidden registry_table still exists for backward compat."""
        widget = _create_widget(qapp)
        assert hasattr(widget, 'registry_table')
        assert widget.registry_table is not None
        widget.deleteLater()

    def test_all_backward_attrs_exist(self, qapp):
        """All backward-compatible attributes are accessible."""
        widget = _create_widget(qapp)
        attr_names = [
            'run_btn', 'run_params_input', 'run_result_display',
            'artifact_list', 'artifact_locate_btn', 'artifact_export_btn',
            'artifact_delete_btn', 'artifact_count_label', 'artifact_status_label',
            'cancel_run_btn', 'inspect_btn', 'install_github_btn',
            'install_progress', 'install_status_label', 'agent_log',
            'healthcheck_btn', 'cancel_healthcheck_btn',
            'github_owner_input', 'github_repo_input', 'github_path_input',
            'github_branch_input', 'github_url_input', 'github_token_input',
            'install_dir_btn', 'install_zip_btn', 'download_install_btn',
            'cancel_install_btn',
        ]
        for name in attr_names:
            assert hasattr(widget, name), f"Missing backward-compat attribute: {name}"
            assert getattr(widget, name) is not None, (
                f"Backward-compat attribute {name} is None"
            )
        widget.deleteLater()

    def test_registry_table_select_row(self, qapp):
        """Hidden registry_table.selectRow() works for test compat."""
        widget = _create_widget(qapp)
        # Should not crash
        widget.registry_table.selectRow(-1)
        widget.registry_table.selectRow(0)
        widget.deleteLater()


class TestSkillSelectionRedirect:
    """Batch UX-1-E: Skill selection / deselection redirects correctly."""

    def _make_skill_data(self) -> dict:
        """Build a complete mock skill data dict."""
        return {
            "skill_id": "test-skill",
            "name": "Test Skill",
            "version": "1.0.0",
            "enabled": True,
            "skill_type": "executable",
            "description": "A test skill",
            "has_run_entrypoint": True,
            "entrypoints": {"run": "run.py", "healthcheck": "check.py"},
            "permissions": ["read", "write"],
            "capabilities": ["data-processing"],
            "dependencies": ["python>=3.10"],
            "artifact_types": ["csv", "png"],
            "is_active": False,
            "health_status": "unavailable",
            "install_path": "/test/path",
            "installed_at": "2026-01-01T00:00:00Z",
            "manifest": {"name": "Test Skill", "version": "1.0.0"},
        }

    def test_select_skill_enables_and_opens_overview(self, qapp):
        """Prove that selecting a valid skill enables Overview/Run/Artifact
        tabs and navigates to the Overview tab."""
        widget = _create_widget(qapp)
        tw = widget.tab_widget

        # Ensure we're not already on overview
        for idx in range(tw.count()):
            if tw.tabText(idx) == "安装与来源":
                tw.setCurrentIndex(idx)
                break

        data = self._make_skill_data()

        # Monkey-patch _build_skill_data to return our mock data
        original = widget._build_skill_data
        widget._build_skill_data = lambda: data
        try:
            widget._sync_all_panels()
        finally:
            widget._build_skill_data = original

        # Overview, run, artifact must be enabled
        for idx in range(tw.count()):
            tab_text = tw.tabText(idx)
            if tab_text in ("概览", "运行", "产物"):
                assert tw.isTabEnabled(idx), (
                    f"Tab '{tab_text}' must be enabled after skill selection"
                )

        # Current tab must be overview
        overview_idx = -1
        for idx in range(tw.count()):
            if tw.tabText(idx) == "概览":
                overview_idx = idx
                break
        assert tw.currentIndex() == overview_idx, (
            f"After skill selection, current tab must be Overview, "
            f"got '{tw.tabText(tw.currentIndex())}'"
        )

        # Header must show skill data
        assert data["name"] in widget.header.name_label.text(), (
            "Header must display the selected skill name"
        )

        widget.deleteLater()

    def test_selection_removed_redirects_to_install(self, qapp):
        """Prove that when the current selection is cleared (uninstall,
        registry empty), the UI redirects to Install & Source tab."""
        widget = _create_widget(qapp)
        tw = widget.tab_widget

        # Phase 1: simulate having a skill selected
        data = self._make_skill_data()
        widget.header.set_skill_data(data)
        widget.overview_panel.set_skill_data(data)
        for idx in range(tw.count()):
            tw.setTabEnabled(idx, True)
        # Switch to overview
        for idx in range(tw.count()):
            if tw.tabText(idx) == "概览":
                tw.setCurrentIndex(idx)
                break
        assert tw.tabText(tw.currentIndex()) == "概览"
        assert tw.isTabEnabled(tw.currentIndex())

        # Phase 2: selection removed — _build_skill_data returns None
        # Call _sync_all_panels directly (no registry → data is None)
        widget._sync_all_panels()

        # Must redirect to install
        install_idx = -1
        for idx in range(tw.count()):
            if tw.tabText(idx) == "安装与来源":
                install_idx = idx
                break
        assert tw.currentIndex() == install_idx, (
            f"After selection removal, must be on Install tab, "
            f"got '{tw.tabText(tw.currentIndex())}'"
        )

        # Overview, run, artifact must be disabled
        for idx in range(tw.count()):
            tab_text = tw.tabText(idx)
            if tab_text in ("概览", "运行", "产物"):
                assert not tw.isTabEnabled(idx), (
                    f"Tab '{tab_text}' must be disabled after selection removed"
                )

        # Current tab must be enabled
        assert tw.isTabEnabled(tw.currentIndex()) is True, (
            "Current tab must always be enabled"
        )

        widget.deleteLater()


class TestRegistryReloadTransition:
    """Batch UX-1-F: Registry reload from non-empty to empty state."""

    def test_registry_reload_from_nonempty_to_empty_redirects_to_install(
        self, qapp, tmp_path,
    ):
        """Start with one skill → overview.  Clear registry → install
        tab with skill tabs disabled."""
        import json
        from unittest.mock import patch
        from dp_engine.skills.registry import SkillRegistry
        from dp_engine.skills.manifest_parser import parse_skill_manifest
        from dp_engine.skills.models import InstalledSkill
        from tests.fixtures.runtime_fixtures import create_minimal_skill_package
        from ui.skill_install_controller import SkillInstallTaskOwner

        # ── Phase 0: prepare registry with one skill ──
        registry_path = tmp_path / "skills_data" / "registry.json"
        installed_dir = tmp_path / "skills_data" / "installed"
        registry_path.parent.mkdir(parents=True, exist_ok=True)
        installed_dir.mkdir(parents=True, exist_ok=True)

        skill_dir = create_minimal_skill_package(
            installed_dir, "reload-skill", "1.0.0", healthy=True,
            healthcheck_message="reload ok",
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

        # ── Phase 1: build widget with one skill ──
        import ui.skill_tab as st
        with patch.object(st, '_get_registry_path', return_value=registry_path), \
             patch.object(st, '_get_installed_dir', return_value=installed_dir), \
             patch('ui.skill_tab._run_skill_data_migration',
                   return_value="No old data found."):
            task_owner = SkillInstallTaskOwner(parent=qapp)
            widget = st.AgentSkillWidget(task_owner=task_owner)

        QApplication.processEvents()

        tw = widget.tab_widget
        overview_idx = install_idx = -1
        for idx in range(tw.count()):
            if tw.tabText(idx) == "概览":
                overview_idx = idx
            elif tw.tabText(idx) == "安装与来源":
                install_idx = idx

        # Contract 1a: with skill → overview
        assert tw.currentIndex() == overview_idx, (
            f"With skill, must be on Overview, got '{tw.tabText(tw.currentIndex())}'"
        )
        for idx in range(tw.count()):
            assert tw.isTabEnabled(idx), (
                f"All tabs must be enabled when skill present"
            )

        # ── Phase 2: overwrite registry with empty data ──
        registry_path.write_text(json.dumps({
            "_schema_version": 1,
            "_updated_at": "2026-01-02T00:00:00Z",
            "active_versions": {},
            "skills": {},
        }, ensure_ascii=False), encoding="utf-8")

        widget._registry.load()
        widget._refresh_registry_table()
        QApplication.processEvents()

        # Contract 2a: redirect to install
        assert tw.currentIndex() == install_idx, (
            f"After registry cleared, must redirect to install, "
            f"got '{tw.tabText(tw.currentIndex())}'"
        )

        # Contract 2b: skill tabs disabled
        for idx in range(tw.count()):
            tab_text = tw.tabText(idx)
            if tab_text in ("概览", "运行", "产物"):
                assert not tw.isTabEnabled(idx), (
                    f"Tab '{tab_text}' must be disabled after registry emptied"
                )

        # Contract 2c: current tab enabled
        assert tw.isTabEnabled(tw.currentIndex()) is True, (
            "Current tab must be enabled"
        )

        # Contract 2d: install and log enabled
        for idx in range(tw.count()):
            tab_text = tw.tabText(idx)
            if tab_text in ("安装与来源", "日志"):
                assert tw.isTabEnabled(idx), (
                    f"Tab '{tab_text}' must remain enabled"
                )

        widget.deleteLater()
