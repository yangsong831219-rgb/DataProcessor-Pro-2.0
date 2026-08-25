"""技能插件中心页面 — Batch UX-1 模块化重构.

将原来单页堆叠布局拆分为:
- 左侧: 技能导航面板 (搜索、过滤、列表、安装按钮)
- 右侧: Header + 5 个标签页 (概览、运行、产物、安装与来源、日志)

本批仅重构 UI 信息架构和布局，不改 Runtime / Artifact / Installer /
Registry / Report Engine 或 Builder 合同。
"""

from __future__ import annotations

import logging
from pathlib import Path

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QPushButton, QLabel,
    QGroupBox, QGridLayout, QTableWidget, QTableWidgetItem,
    QTextEdit, QLineEdit, QDialog, QMessageBox, QHeaderView,
    QCheckBox, QFileDialog, QProgressBar, QDialogButtonBox,
    QFormLayout, QComboBox, QTreeWidget, QTreeWidgetItem,
    QSplitter, QTabWidget, QFrame,
)

logger = logging.getLogger(__name__)

# ── Data directory for skill registry ──


def _get_skills_data_dir() -> Path:
    """Return the skills data directory (user app data, NOT project root)."""
    from utils.app_paths import get_skills_root
    return get_skills_root()


def _get_registry_path() -> Path:
    """Return the path to the skill registry JSON file."""
    return _get_skills_data_dir() / "registry.json"


def _get_installed_dir() -> Path:
    """Return the managed installed skills directory."""
    return _get_skills_data_dir() / "installed"


def _get_old_skill_registry_paths() -> list[Path]:
    """Return paths to old (in-repo) skill registry files for migration."""
    import os as _os
    project_root = Path(_os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))
    old_skills_dir = project_root / "readings_profiles" / "skills"
    return [
        old_skills_dir / "skill_registry.json",
        old_skills_dir / "registry.json",
    ]


def _run_skill_data_migration() -> str:
    """Run one-time migration from old in-repo paths to app data.

    Returns a human-readable message about what happened.
    Safe to call multiple times — only migrates once.
    """
    from utils.app_paths import get_skills_root, get_skills_paths
    from dp_engine.skills.migrator import SkillDataMigrator

    new_paths = get_skills_paths()
    old_paths = _get_old_skill_registry_paths()
    old_skill_root = _get_skills_data_dir_old()

    migrator = SkillDataMigrator(
        old_paths=old_paths,
        new_registry_path=new_paths.registry_file,
        new_installed_dir=new_paths.installed_dir,
        old_skill_root=old_skill_root,
        migration_marker_path=get_skills_root() / "migration-v1.json",
    )
    result = migrator.migrate_if_needed()
    return result.message


def _get_skills_data_dir_old() -> Path:
    """Return the OLD in-repo skills dir (for migration source)."""
    import os as _os
    project_root = Path(_os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))
    return project_root / "readings_profiles" / "skills"


# ── Widget ──


class AgentSkillWidget(QWidget):
    """技能插件中心 — 技能来源检查、安装、卸载与本地注册表管理."""

    # Signal emitted when the installed skills list changes
    registry_changed = pyqtSignal()

    # Batch 3.3.2: Bridge asset selection signal
    send_to_report_requested = pyqtSignal(list)  # list[ReportArtifactSelection]

    def __init__(
        self,
        parent: QWidget | None = None,
        task_owner: object | None = None,
    ) -> None:
        super().__init__(parent)
        self._registry_path = _get_registry_path()
        self._registry = None  # SkillRegistry, lazy-loaded
        self._closing = False
        self._task_owner = task_owner  # SkillInstallTaskOwner from main window

        # Qt async controller for source inspection
        from ui.skill_source_controller import SkillSourceInspectController
        self._source_controller = SkillSourceInspectController(parent=self)
        self._source_controller.result_ready.connect(self._on_inspect_result)
        self._source_controller.running_changed.connect(
            self._on_source_running_changed
        )

        # Qt async controller for install/uninstall
        from ui.skill_install_controller import SkillInstallController
        self._install_controller = SkillInstallController(
            parent=self,
            task_owner=self._task_owner,  # type: ignore[arg-type]
        )
        self._install_controller.result_ready.connect(
            self._on_install_finished
        )
        self._install_controller.error_occurred.connect(
            self._on_install_failed
        )
        self._install_controller.running_changed.connect(
            self._on_install_running_changed
        )
        self._install_controller.progress_changed.connect(
            self._on_download_progress
        )
        self._install_controller.stage_changed.connect(
            self._on_install_stage_changed
        )

        # Register controller with TaskOwner (Batch 2.3)
        if self._task_owner is not None and hasattr(self._task_owner, 'add_controller'):
            self._task_owner.add_controller(self._install_controller)  # type: ignore[arg-type]

        # Qt async controller for runtime healthcheck (Batch 3.0)
        from ui.skill_runtime_controller import SkillRuntimeController
        self._runtime_controller = SkillRuntimeController(
            registry_path=self._registry_path,
            installed_dir=_get_installed_dir(),
            parent=self,
        )
        self._runtime_controller.result_ready.connect(
            self._on_runtime_result
        )
        self._runtime_controller.error_occurred.connect(
            self._on_runtime_error
        )
        self._runtime_controller.running_changed.connect(
            self._on_runtime_running_changed
        )
        # Batch 3.2.2: artifact operation results
        self._runtime_controller.artifact_result_ready.connect(
            self._on_artifact_result
        )

        # Register runtime controller with TaskOwner (Batch 3.0)
        if self._task_owner is not None and hasattr(self._task_owner, 'add_controller'):
            self._task_owner.add_controller(self._runtime_controller)  # type: ignore[arg-type]

        # Last inspection result for GitHub install
        self._last_inspection: object | None = None

        # Runtime operation tracking (Batch 3.1.2)
        self._active_operation: str | None = None  # "healthcheck", "run", or None
        self._run_generation: int = 0  # Incremented each run start for lifecycle safety

        # Artifact UI state (Batch 3.2.2)
        self._artifact_skill_id: str | None = None
        self._artifact_task_id: str | None = None
        self._artifact_op_active: bool = False

        # Bridge selection state (Batch 3.3.2)
        self._bridge_selection_count: int = 0
        self._bridge_selection_max: int = 12

        # Initial sync gate (Batch UX-1-F): first _sync_all_panels() during
        # construction only redirects; showEvent triggers full tab-disable sync.
        self._initial_sync_done: bool = False

        self._build_ui()

    # ── Bridge coordinator passthrough (Batch 3.3.2) ──

    def set_bridge_coordinator(self, coordinator: object) -> None:
        """Pass the application-level ArtifactOperationCoordinator to the
        runtime controller for artifact operation mutual exclusion."""
        if hasattr(self, '_runtime_controller'):
            self._runtime_controller.set_coordinator(coordinator)

    # ── Lifecycle ──

    def closeEvent(self, event) -> None:  # pyright: ignore[reportIncompatibleMethodOverride]
        """Handle widget close (Batch 2.3 + Batch 3.0).

        Download phase: abort reply, delete .part, do NOT start InstallWorker.
        Install phase: set cancel token, disconnect UI signals, transfer
        controller ownership to TaskOwner (if available). The TaskOwner
        keeps the worker alive until it finishes naturally.
        Runtime phase: cancel healthcheck, transfer runtime controller.
        Widget destruction does NOT affect the worker object.
        """
        self._closing = True

        # Cancel source inspection
        self._source_controller.cancel()

        # Disconnect UI signals from install controller
        ctrl = self._install_controller
        if ctrl is not None:
            try:
                ctrl.result_ready.disconnect(self._on_install_finished)
            except (TypeError, RuntimeError):
                pass
            try:
                ctrl.error_occurred.disconnect(self._on_install_failed)
            except (TypeError, RuntimeError):
                pass
            try:
                ctrl.progress_changed.disconnect(self._on_download_progress)
            except (TypeError, RuntimeError):
                pass
            try:
                ctrl.stage_changed.disconnect(self._on_install_stage_changed)
            except (TypeError, RuntimeError):
                pass
            try:
                ctrl.running_changed.disconnect(self._on_install_running_changed)
            except (TypeError, RuntimeError):
                pass

            # Cancel ongoing operations (aborts downloads, sets cancel token)
            ctrl.cancel()

            # Transfer controller ownership to application-level TaskOwner.
            if self._task_owner is not None and hasattr(self._task_owner, 'add_controller'):
                self._task_owner.add_controller(ctrl)  # type: ignore[arg-type]

        # Cancel and transfer runtime controller (Batch 3.0 + 3.1.2 + 3.2.2)
        rctrl = self._runtime_controller
        if rctrl is not None:
            try:
                rctrl.result_ready.disconnect(self._on_runtime_result)
            except (TypeError, RuntimeError):
                pass
            try:
                rctrl.error_occurred.disconnect(self._on_runtime_error)
            except (TypeError, RuntimeError):
                pass
            try:
                rctrl.running_changed.disconnect(self._on_runtime_running_changed)
            except (TypeError, RuntimeError):
                pass
            try:
                rctrl.artifact_result_ready.disconnect(self._on_artifact_result)
            except (TypeError, RuntimeError):
                pass
            rctrl.cancel()
            rctrl.close()
            if self._task_owner is not None and hasattr(self._task_owner, 'add_controller'):
                self._task_owner.add_controller(rctrl)  # type: ignore[arg-type]

        super().closeEvent(event)

    def showEvent(self, event) -> None:  # pyright: ignore[reportIncompatibleMethodOverride]
        """First-show synchronisation (Batch UX-1-F).

        The initial _sync_all_panels() during _build_ui() only redirects
        the current tab (install if empty registry) but does NOT disable
        tabs.  showEvent triggers a full sync so the tab enable/disable
        contract is enforced before the user sees the widget.
        """
        super().showEvent(event)
        self._sync_all_panels()

    # ── UI Construction (Batch UX-1: splitter + tabs) ──

    def _build_ui(self) -> None:
        from ui.skill_center.style import STYLE
        from ui.skill_center.nav_panel import SkillNavPanel
        from ui.skill_center.skill_header import SkillHeader
        from ui.skill_center.overview_panel import OverviewPanel
        from ui.skill_center.run_panel import RunPanel
        from ui.skill_center.artifact_panel import ArtifactPanel
        from ui.skill_center.install_panel import InstallPanel
        from ui.skill_center.log_panel import LogPanel

        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(0)

        # ── Page subtitle ──
        subtitle = QLabel("管理、运行和安装本地技能")
        subtitle.setStyleSheet(
            f"color: {STYLE.TEXT_SECONDARY}; font-size: {STYLE.FONT_SM}px;"
            f" padding: 6px {STYLE.PAGE_MARGIN}px; background: transparent;"
        )
        main_layout.addWidget(subtitle)

        # ── Hidden registry table (data model, backward compat) ──
        self.registry_table = QTableWidget()
        self.registry_table.setColumnCount(8)
        self.registry_table.setHorizontalHeaderLabels([
            "技能ID", "名称", "版本", "类型", "产物", "活动", "启用", "健康状态"
        ])
        self.registry_table.setSelectionBehavior(
            QTableWidget.SelectionBehavior.SelectRows
        )
        self.registry_table.setSelectionMode(
            QTableWidget.SelectionMode.SingleSelection
        )
        self.registry_table.itemSelectionChanged.connect(
            self._on_skill_selection_changed
        )
        self.registry_table.setVisible(False)  # hidden data model

        # ── Dummy widgets for backward compat ──
        self.healthcheck_btn = QPushButton()
        self.healthcheck_btn.setVisible(False)
        self.cancel_healthcheck_btn = QPushButton()
        self.cancel_healthcheck_btn.setVisible(False)
        self.install_dir_btn = QPushButton()
        self.install_dir_btn.setVisible(False)
        self.install_zip_btn = QPushButton()
        self.install_zip_btn.setVisible(False)
        self.cancel_install_btn = QPushButton()
        self.cancel_install_btn.setVisible(False)
        self.download_install_btn = QPushButton()
        self.download_install_btn.setVisible(False)

        # ── Main splitter ──
        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.setHandleWidth(1)

        # Left: Navigation panel
        self.nav_panel = SkillNavPanel()
        self.nav_panel.skill_selected.connect(self._on_nav_skill_selected)
        self.nav_panel.install_requested.connect(self._on_nav_install_requested)
        splitter.addWidget(self.nav_panel)

        # Right: Work area
        right_widget = QWidget()
        right_layout = QVBoxLayout(right_widget)
        right_layout.setContentsMargins(STYLE.SPACE_MD, 0, STYLE.PAGE_MARGIN, STYLE.PAGE_MARGIN)
        right_layout.setSpacing(STYLE.SPACE_SM)

        # ── Skill header ──
        self.header = SkillHeader()
        self.header.run_requested.connect(self._on_run_start)
        self.header.stop_requested.connect(self._on_run_cancel)
        self.header.set_active_requested.connect(self._on_set_active_version)
        self.header.toggle_enabled_requested.connect(self._on_toggle_selected)
        self.header.view_details_requested.connect(self._on_view_details)
        self.header.uninstall_requested.connect(self._on_uninstall_selected)
        self.header.help_requested.connect(self._show_help)
        self.header.install_requested.connect(self._on_nav_install_requested)
        right_layout.addWidget(self.header)

        # ── Tab widget ──
        self.tab_widget = QTabWidget()
        self.tab_widget.setStyleSheet(STYLE.TAB_STYLE)

        self.overview_panel = OverviewPanel()
        self.overview_panel.view_details_requested.connect(self._on_view_details)
        self.tab_widget.addTab(self.overview_panel, "概览")

        self.run_panel = RunPanel()
        self.run_panel.run_requested.connect(self._on_run_start)
        self.run_panel.stop_requested.connect(self._on_run_cancel)
        self.run_panel.params_input.textChanged.connect(self._on_run_params_changed)
        self.tab_widget.addTab(self.run_panel, "运行")

        self.artifact_panel = ArtifactPanel()
        self.artifact_panel.artifact_list.itemSelectionChanged.connect(
            self._on_artifact_selection_changed
        )
        self.artifact_panel.locate_requested.connect(self._on_artifact_locate)
        self.artifact_panel.export_requested.connect(self._on_artifact_export)
        self.artifact_panel.delete_requested.connect(self._on_artifact_delete)
        # Batch 3.3.2: Bridge multi-select
        self.artifact_panel.bridge_check_changed.connect(self._on_bridge_check_changed)
        self.artifact_panel.send_to_report_requested.connect(self._on_send_to_report)
        self.tab_widget.addTab(self.artifact_panel, "产物")

        self.install_panel = InstallPanel()
        self.install_panel.install_from_dir_requested.connect(
            self._on_install_from_directory
        )
        self.install_panel.install_from_zip_requested.connect(
            self._on_install_from_zip
        )
        self.install_panel.inspect_source_requested.connect(
            self._on_inspect_source
        )
        self.install_panel.install_from_github_requested.connect(
            self._on_install_from_github
        )
        self.install_panel.cancel_install_requested.connect(
            self._on_cancel_install
        )
        self.tab_widget.addTab(self.install_panel, "安装与来源")

        self.log_panel = LogPanel()
        self.tab_widget.addTab(self.log_panel, "日志")

        right_layout.addWidget(self.tab_widget, 1)

        splitter.addWidget(right_widget)
        splitter.setSizes([280, 900])

        main_layout.addWidget(splitter, 1)

        # ── Backward-compatible attribute aliases for tests ──
        self.run_btn = self.run_panel.run_btn
        self.run_params_input = self.run_panel.params_input
        self.run_result_display = self.run_panel.result_display
        self.artifact_list = self.artifact_panel.artifact_list
        self.artifact_locate_btn = self.artifact_panel.locate_btn
        self.artifact_export_btn = self.artifact_panel.export_btn
        self.artifact_delete_btn = self.artifact_panel.delete_btn
        self.artifact_count_label = self.artifact_panel.count_label
        self.artifact_status_label = self.artifact_panel.status_label
        self.send_to_report_btn = self.artifact_panel.send_to_report_btn  # Batch 3.3.2
        self.bridge_count_label = self.artifact_panel.bridge_count_label  # Batch 3.3.2
        self.cancel_run_btn = self.run_panel.stop_btn
        self.inspect_btn = self.install_panel.inspect_btn
        self.install_github_btn = self.install_panel.github_install_btn
        self.install_progress = self.install_panel.progress_bar
        self.install_status_label = self.install_panel.progress_label
        self.agent_log = self.log_panel.log_edit
        # GitHub field aliases
        self.github_owner_input = self.install_panel.owner_input
        self.github_repo_input = self.install_panel.repo_input
        self.github_path_input = self.install_panel.path_input
        self.github_branch_input = self.install_panel.branch_input
        self.github_url_input = self.install_panel.url_input
        self.github_token_input = self.install_panel.token_input

        # ── Initial tab redirect (Batch UX-1-F) ──
        # After all panels, tabs, signals, and backward-compat aliases
        # are fully constructed, redirect to "安装与来源" if the registry
        # is empty.  Tab disabling is deferred to showEvent so that
        # artifact-panel button logic remains testable before show.
        self._initial_sync_done = False
        self._sync_all_panels()

    # ── Registry helpers ──

    def _ensure_registry(self):
        """Lazy-load the SkillRegistry. Runs data migration on first access."""
        if self._registry is not None:
            return

        # Run one-time migration from old in-repo paths to app data
        try:
            migration_msg = _run_skill_data_migration()
            if "migrated" in migration_msg.lower() and "no old" not in migration_msg.lower():
                self._log_info(f"数据迁移: {migration_msg}")
        except Exception as e:
            self._log_warn(f"数据迁移未执行 (非致命): {e}")

        from dp_engine.skills.registry import SkillRegistry
        self._registry_path.parent.mkdir(parents=True, exist_ok=True)
        self._registry = SkillRegistry(self._registry_path)
        try:
            self._registry.load()
        except Exception as e:
            self._log_error(f"加载注册表失败: {e}")

    def _on_skill_selection_changed(self) -> None:
        """Handle registry table selection change.

        Clears artifact state (skill changed), syncs all panels,
        and refreshes run button.
        """
        self._clear_artifact_state()
        self._sync_all_panels()

    def _refresh_registry_table(self) -> None:
        """Rebuild the registry table from current SkillRegistry state."""
        self._ensure_registry()
        skills = self._registry.list_skills() if self._registry else []
        self.registry_table.setRowCount(len(skills))

        nav_skills: list[dict] = []

        for i, skill in enumerate(skills):
            self.registry_table.setItem(i, 0, QTableWidgetItem(skill.skill_id))
            self.registry_table.setItem(i, 1, QTableWidgetItem(skill.manifest.name))
            self.registry_table.setItem(i, 2, QTableWidgetItem(skill.version))
            self.registry_table.setItem(i, 3, QTableWidgetItem(skill.manifest.skill_type))
            self.registry_table.setItem(i, 4, QTableWidgetItem(
                ", ".join(skill.manifest.artifact_types)
                if skill.manifest.artifact_types else "—"
            ))

            # Active indicator
            active = self._registry.get_active(skill.skill_id) if self._registry else None
            is_active = (
                active is not None
                and active.skill_id == skill.skill_id
                and active.version == skill.version
            )
            active_item = QTableWidgetItem("★ 活动" if is_active else "")
            if is_active:
                active_item.setForeground(Qt.GlobalColor.darkGreen)
            self.registry_table.setItem(i, 5, active_item)

            # Enabled checkbox
            enabled_cb = QCheckBox()
            enabled_cb.setChecked(skill.enabled)
            enabled_cb.setEnabled(False)
            self.registry_table.setCellWidget(i, 6, enabled_cb)

            # Health status
            health_text = skill.health_status
            health_item = QTableWidgetItem(health_text)
            if health_text == "healthy":
                health_item.setForeground(Qt.GlobalColor.darkGreen)
            elif health_text == "unhealthy":
                health_item.setForeground(Qt.GlobalColor.red)
            elif health_text in ("unavailable", "not_supported"):
                health_item.setForeground(Qt.GlobalColor.gray)
            elif health_text in ("timeout", "crashed", "protocol_error"):
                health_item.setForeground(Qt.GlobalColor.darkRed)
            elif health_text in ("dependency_missing", "permission_denied"):
                health_item.setForeground(Qt.GlobalColor.darkYellow)
            elif health_text == "checking":
                health_item.setForeground(Qt.GlobalColor.blue)
            elif health_text == "cancelled":
                health_item.setForeground(Qt.GlobalColor.darkGray)
            self.registry_table.setItem(i, 7, health_item)

            # Build nav panel entry
            nav_skills.append({
                "skill_id": skill.skill_id,
                "name": skill.manifest.name,
                "version": skill.version,
                "enabled": skill.enabled,
                "skill_type": skill.manifest.skill_type,
                "is_active": is_active,
            })

        if len(skills) == 0:
            self.registry_table.setRowCount(1)
            empty_item = QTableWidgetItem("(暂无已注册技能)")
            empty_item.setFlags(Qt.ItemFlag.NoItemFlags)
            self.registry_table.setItem(0, 0, empty_item)
            self.registry_table.setSpan(0, 0, 1, 8)

        # Populate nav panel
        self.nav_panel.set_skills(nav_skills)

        # Synchronise tab state: disable skill tabs if nothing is selected
        self._sync_all_panels()

    def _get_selected_skill(self) -> tuple[str, str] | None:
        """Get the (skill_id, version) of the currently selected row."""
        current_row = self.registry_table.currentRow()
        if self._registry is None:
            return None
        skills = self._registry.list_skills()
        if current_row < 0 or current_row >= len(skills):
            return None
        skill = skills[current_row]
        return (skill.skill_id, skill.version)

    # ── Registry event handlers ──

    def _on_refresh_registry(self) -> None:
        self._ensure_registry()
        if self._registry is None:
            return
        try:
            self._registry.load()
            self._refresh_registry_table()
            self._log_info(f"注册表已刷新，共 {len(self._registry)} 个技能")
        except Exception as e:
            self._log_error(f"刷新注册表失败: {e}")
            QMessageBox.warning(self, "错误", f"刷新注册表失败:\n{e}")

    def _on_toggle_selected(self) -> None:
        self._ensure_registry()
        if self._registry is None:
            return

        selected = self._get_selected_skill()
        if selected is None:
            QMessageBox.information(self, "提示", "请先在表格中选中一个技能")
            return

        skill_id, version = selected
        skill = self._registry.get(skill_id, version)
        if skill is None:
            return

        new_enabled = not skill.enabled
        try:
            self._registry.set_enabled(skill_id, new_enabled, version)
            self._registry.save()
            self._refresh_registry_table()
            self.registry_changed.emit()
            self._log_info(
                f"技能 '{skill_id}@{version}' "
                f"已{'启用' if new_enabled else '禁用'}"
            )
        except Exception as e:
            self._log_error(f"切换失败: {e}")
            QMessageBox.warning(self, "错误", f"操作失败:\n{e}")

    def _on_set_active_version(self) -> None:
        self._ensure_registry()
        if self._registry is None:
            return

        selected = self._get_selected_skill()
        if selected is None:
            QMessageBox.information(self, "提示", "请先在表格中选中一个技能")
            return

        skill_id, version = selected

        # Check if already active
        active = self._registry.get_active(skill_id)
        if active is not None and active.version == version:
            QMessageBox.information(
                self, "提示",
                f"'{skill_id}@{version}' 已是活动版本。"
            )
            return

        try:
            self._registry.set_active_version(skill_id, version)
            self._registry.save()
            self._refresh_registry_table()
            self.registry_changed.emit()
            self._log_info(
                f"已将 '{skill_id}@{version}' 设为活动版本"
            )
        except Exception as e:
            self._log_error(f"设置活动版本失败: {e}")
            QMessageBox.warning(self, "错误", f"操作失败:\n{e}")

    def _on_uninstall_selected(self) -> None:
        self._ensure_registry()
        if self._registry is None:
            return

        selected = self._get_selected_skill()
        if selected is None:
            QMessageBox.information(self, "提示", "请先在表格中选中一个技能")
            return

        skill_id, version = selected

        # Confirm
        reply = QMessageBox.question(
            self,
            "确认卸载",
            f"确定要卸载技能 '{skill_id}@{version}' 吗？\n\n"
            f"此操作将删除已安装的技能文件。\n"
            f"如果这是活动版本，活动槽将被清空。",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return

        self._log_info(f"正在卸载 '{skill_id}@{version}'...")
        self._install_controller.start_uninstall(skill_id, version)

    def _on_view_manifest(self) -> None:
        self._ensure_registry()
        registry = self._registry
        if registry is None:
            return
        selected = self._get_selected_skill()
        if selected is None:
            QMessageBox.information(self, "提示", "请先在表格中选中一个技能")
            return

        skill_id, version = selected
        skill = registry.get(skill_id, version)
        if skill is None:
            return

        import json
        manifest_json = json.dumps(
            skill.manifest.to_dict(),
            ensure_ascii=False,
            indent=2,
        )
        dlg = QDialog(self)
        dlg.setWindowTitle(f"Manifest: {skill_id}@{version}")
        dlg.resize(700, 500)
        layout = QVBoxLayout()
        text = QTextEdit()
        text.setReadOnly(True)
        text.setPlainText(manifest_json)
        layout.addWidget(text)
        btn = QPushButton("关闭")
        btn.clicked.connect(dlg.close)
        layout.addWidget(btn)
        dlg.setLayout(layout)
        dlg.exec()

    def _on_view_dependencies(self) -> None:
        self._ensure_registry()
        registry = self._registry
        if registry is None:
            return
        selected = self._get_selected_skill()
        if selected is None:
            QMessageBox.information(self, "提示", "请先在表格中选中一个技能")
            return

        skill_id, version = selected
        skill = registry.get(skill_id, version)
        if skill is None:
            return

        deps = skill.manifest.dependencies
        if not deps:
            QMessageBox.information(
                self, "依赖声明",
                f"技能 '{skill_id}@{version}' 未声明任何依赖。"
            )
            return

        deps_text = "\n".join(f"  - {d}" for d in deps)
        QMessageBox.information(
            self, "依赖声明",
            f"技能 '{skill_id}@{version}' 声明了以下依赖：\n\n"
            f"{deps_text}\n\n"
            f"注意：当前版本不会自动安装这些依赖。"
        )

    def _on_view_capabilities(self) -> None:
        self._ensure_registry()
        registry = self._registry
        if registry is None:
            return
        selected = self._get_selected_skill()
        if selected is None:
            QMessageBox.information(self, "提示", "请先在表格中选中一个技能")
            return

        skill_id, version = selected
        skill = registry.get(skill_id, version)
        if skill is None:
            return

        caps = skill.manifest.capabilities
        if not caps:
            QMessageBox.information(
                self, "能力声明",
                f"技能 '{skill_id}@{version}' 未声明任何能力。"
            )
            return

        caps_text = "\n".join(f"  - {c}" for c in caps)
        QMessageBox.information(
            self, "能力声明",
            f"技能 '{skill_id}@{version}' 声明了以下能力：\n\n"
            f"{caps_text}\n\n"
            f"注意：当前版本不会授予这些能力。"
        )

    # ── Source inspection ──

    def _on_inspect_source(self) -> None:
        """Start a source inspection via the async controller."""
        owner = self.github_owner_input.text().strip()
        repo = self.github_repo_input.text().strip()
        path = self.github_path_input.text().strip()
        branch = self.github_branch_input.text().strip() or "main"
        token = self.github_token_input.text().strip() or None

        if not owner or not repo:
            QMessageBox.warning(self, "警告", "请输入仓库所有者和仓库名称")
            return

        from dp_engine.github_skill_source import build_skill_md_url

        skill_md_url = build_skill_md_url(owner, repo, path, branch)

        self._log_info(f"正在检查来源: {owner}/{repo}/{path}@{branch}")
        self._log_info(f"目标 URL: {skill_md_url}")

        self._source_controller.start(
            owner=owner, repo=repo, path=path, branch=branch, token=token,
        )

    def _on_download_and_install(self) -> None:
        """Download and install from a GitHub archive URL."""
        url = self.github_url_input.text().strip()
        if not url:
            QMessageBox.warning(self, "警告", "请输入 GitHub 归档 URL")
            return

        from ui.skill_install_controller import validate_github_url_for_ui
        is_valid, error_msg = validate_github_url_for_ui(url)
        if not is_valid:
            QMessageBox.warning(self, "URL 格式错误", error_msg)
            return

        token = self.github_token_input.text().strip() or None

        self._log_info(f"正在下载并安装: {url}")
        self._install_controller.start_download_and_install(url, token=token)

    # ── Install event handlers ──

    def _on_install_from_directory(self) -> None:
        """Install from a local directory."""
        directory = QFileDialog.getExistingDirectory(
            self, "选择技能目录"
        )
        if not directory:
            return

        self._log_info(f"正在从目录安装: {directory}")
        self._set_install_buttons_enabled(False)
        self._install_controller.start_install_from_directory(directory)

    def _on_install_from_zip(self) -> None:
        """Install from a local ZIP file."""
        zip_path, _ = QFileDialog.getOpenFileName(
            self, "选择技能 ZIP 包",
            "", "ZIP 文件 (*.zip)"
        )
        if not zip_path:
            return

        self._log_info(f"正在从 ZIP 安装: {zip_path}")
        self._set_install_buttons_enabled(False)
        self._install_controller.start_install_from_zip(zip_path)

    def _on_install_from_github(self) -> None:
        """Install from a previously inspected GitHub source."""
        if self._last_inspection is None:
            QMessageBox.information(
                self, "提示",
                "请先在「GitHub 技能来源检查」中检查一个有效的技能来源"
            )
            return

        # Build URL from last inspection fields
        owner = self.github_owner_input.text().strip()
        repo = self.github_repo_input.text().strip()
        branch = self.github_branch_input.text().strip() or "main"

        url = f"https://github.com/{owner}/{repo}/archive/{branch}.zip"

        self._log_info(f"正在从已检查来源安装: {owner}/{repo}")
        self._set_install_buttons_enabled(False)
        token = self.github_token_input.text().strip() or None
        self._install_controller.start_download_and_install(url, token=token)

    def _on_cancel_install(self) -> None:
        """Cancel the current install operation."""
        self._log_info("正在取消操作...")
        self._install_controller.cancel()

    # ── Controller signal handlers ──

    def _on_source_running_changed(self, running: bool) -> None:
        """Update button state based on controller running state."""
        try:
            if running:
                self.install_panel.set_inspect_button_text("检查中...")
                self.install_panel.set_inspect_button_enabled(False)
            else:
                self.install_panel.set_inspect_button_text("检查来源")
                self.install_panel.set_inspect_button_enabled(True)
        except RuntimeError:
            pass

    def _on_inspect_result(self, result: object) -> None:
        """Receive inspection result from controller."""
        if self._closing:
            return

        from dp_engine.skills.models import SkillSourceInspectionResult

        if not isinstance(result, SkillSourceInspectionResult):
            self._log_error(
                f"内部错误: controller 返回了意外类型 {type(result).__name__}"
            )
            return

        repo_label = result.repository_name or "(未知)"

        if not result.source_reachable:
            self._log_error(
                f"来源不可达 [{result.error_code}]: {result.message}"
            )
            QMessageBox.warning(
                self, "来源检查失败",
                f"无法连接到技能来源:\n{result.message}\n\n"
                f"URL: {result.source_url}\n仓库: {repo_label}\n"
                f"错误码: {result.error_code or '未知'}"
            )
            self._last_inspection = None
            self.install_github_btn.setEnabled(False)
            self.install_github_btn.setVisible(False)
            self.install_panel.set_inspection_valid(False)
            return

        if not result.is_viable_skill_source:
            self._log_warn(
                f"来源不可用 [{result.error_code}]: {result.message}"
            )
            QMessageBox.warning(
                self, "来源检查完成",
                f"仓库 {repo_label} 可以访问，但未找到有效的 SKILL.md。\n\n"
                f"{result.message}\n\n"
                f"请确认仓库结构和路径是否正确。"
            )
            self._last_inspection = None
            self.install_github_btn.setEnabled(False)
            self.install_github_btn.setVisible(False)
            self.install_panel.set_inspection_valid(False)
            return

        # All checks passed — enable GitHub install
        self._last_inspection = result
        self.install_github_btn.setEnabled(True)
        self.install_github_btn.setVisible(True)
        self.install_panel.set_inspection_valid(True)
        self.install_github_btn.setToolTip(
            f"从 {repo_label} 下载并安装技能"
        )

        self._log_success(f"来源可用: {result.message}")
        if result.warnings:
            for w in result.warnings:
                self._log_warn(f"警告: {w}")

        QMessageBox.information(
            self, "来源检查完成",
            f"检测到有效技能来源: {repo_label}\n"
            f"SKILL.md 已找到，YAML Front Matter 可解析。\n"
            f"{result.message}\n\n"
            f"点击「从已检查 GitHub 来源安装」可下载并安装该技能。"
        )

    def _on_install_finished(self, result: object) -> None:
        """Handle install/uninstall completion."""
        if self._closing:
            return
        self._set_install_buttons_enabled(True)
        self.install_panel.show_progress(False)
        self.install_panel.set_progress_label("")
        self.install_panel.set_cancel_enabled(False)

        from dp_engine.skills.package_models import (
            SkillInstallResult,
            SkillUninstallResult,
        )

        if isinstance(result, SkillInstallResult):
            if result.success:
                self._log_success(
                    f"安装成功: {result.skill_id}@{result.version}\n"
                    f"路径: {result.install_path}\n"
                    f"包校验: {result.package_checksum}"
                )
                if result.warnings:
                    for w in result.warnings:
                        self._log_warn(f"警告: {w}")
                QMessageBox.information(
                    self, "安装完成",
                    f"技能包已安装并注册，但当前尚不能执行。\n\n"
                    f"技能: {result.skill_id}@{result.version}\n"
                    f"路径: {result.install_path}\n\n"
                    f"{result.message}"
                )
            else:
                self._log_error(f"安装失败: {result.message}")
                QMessageBox.warning(
                    self, "安装失败",
                    f"技能包安装失败:\n\n{result.message}"
                )

        elif isinstance(result, SkillUninstallResult):
            if result.success:
                self._log_success(
                    f"卸载成功: {result.skill_id}@{result.version}"
                )
                QMessageBox.information(
                    self, "卸载完成",
                    f"技能 '{result.skill_id}@{result.version}' 已卸载。"
                )
            else:
                self._log_error(f"卸载失败: {result.message}")
                QMessageBox.warning(
                    self, "卸载失败",
                    f"卸载失败:\n\n{result.message}"
                )

        # Refresh registry
        if self._registry is not None:
            try:
                self._registry.load()
            except Exception:
                pass
        self._refresh_registry_table()
        self.registry_changed.emit()

    def _on_install_failed(self, error_msg: str) -> None:
        """Handle install failure."""
        if self._closing:
            return
        self._set_install_buttons_enabled(True)
        self.install_panel.show_progress(False)
        self.install_panel.set_progress_label("")
        self.install_panel.set_cancel_enabled(False)

        self._log_error(f"操作失败: {error_msg}")
        QMessageBox.warning(
            self, "操作失败",
            f"操作未能完成:\n\n{error_msg}"
        )

        # Refresh just in case partial state
        try:
            if self._registry is not None:
                self._registry.load()
        except Exception:
            pass
        self._refresh_registry_table()

    def _on_install_running_changed(self, running: bool) -> None:
        """Update UI when install starts/stops."""
        if self._closing:
            return
        if running:
            self.install_panel.show_progress(True)
            self.install_panel.set_progress_label("")
            self.install_panel.set_cancel_enabled(True)
        else:
            self._set_install_buttons_enabled(True)

    def _on_download_progress(self, downloaded: int, total: int) -> None:
        """Update progress bar for download."""
        self.install_panel.show_progress(True)
        if total > 0:
            self.install_panel.update_progress(downloaded, total)
            self.install_panel.set_progress_label(
                f"下载中: {downloaded / (1024*1024):.1f} MB"
                + f" / {total / (1024*1024):.1f} MB"
            )
        else:
            self.install_panel.update_progress(0, 0)
            self.install_panel.set_progress_label(
                f"下载中: {downloaded / (1024*1024):.1f} MB"
            )

    def _on_install_stage_changed(self, stage: str) -> None:
        """Update status label for install stage."""
        stage_labels = {
            "preparing": "准备中...",
            "downloading": "下载中...",
            "copying_source": "复制源文件...",
            "checking_archive": "检查归档...",
            "extracting": "解压中...",
            "locating_skill_root": "定位技能根目录...",
            "validating_manifest": "验证 Manifest...",
            "hashing_files": "计算文件哈希...",
            "committing_files": "提交文件...",
            "updating_registry": "更新注册表...",
            "completed": "完成",
            "rolling_back": "回滚中...",
            "cancelled": "已取消",
            "failed": "失败",
        }
        label = stage_labels.get(stage, stage)
        self.install_panel.set_progress_label(f"阶段: {label}")
        self._log_info(f"[安装] {label}")

    # ── Runtime dispatcher handlers (Batch 3.1.2) ──

    def _on_runtime_result(self, response: object) -> None:
        """Dispatch runtime result to healthcheck or run handler."""
        if self._closing:
            return
        operation = getattr(response, 'operation', 'healthcheck')
        if operation == 'run':
            self._on_run_result(response)
        else:
            self._on_healthcheck_result(response)

    def _on_runtime_error(self, error_type: str, message: str) -> None:
        """Dispatch runtime error to healthcheck or run handler."""
        if self._closing:
            return
        # Use _active_operation to determine which handler to call
        if self._active_operation == 'run':
            self._on_run_error(error_type, message)
        else:
            self._on_healthcheck_error(error_type, message)

    def _on_runtime_running_changed(self, running: bool) -> None:
        """Update UI when any runtime or artifact operation starts/stops."""
        if self._closing:
            return
        if running:
            self.run_panel.set_running_state(True)
            self.run_panel.set_run_enabled(False)
            self.header.set_running_state(True)
            self.header.set_stop_enabled(True)
            self.run_params_input.setReadOnly(True)
            self._refresh_artifact_buttons()
        else:
            self._active_operation = None
            self.run_panel.set_running_state(False)
            self.header.set_running_state(False)
            self.run_params_input.setReadOnly(False)
            self._refresh_run_button_state()
            self._refresh_artifact_buttons()

    # ── Run event handlers (Batch 3.1.2) ──

    def _on_run_params_changed(self) -> None:
        """Validate params input and refresh button state."""
        self._refresh_run_button_state()

    def _on_run_start(self) -> None:
        """Validate parameters and start a run via the runtime controller."""
        if self._closing:
            return

        selected = self._get_selected_skill()
        if selected is None:
            QMessageBox.information(self, "提示", "请先在表格中选中一个技能")
            return

        skill_id, version = selected

        # Validate JSON params
        params_text = self.run_params_input.text().strip()
        if not params_text:
            params = {}
        else:
            import json
            try:
                parsed = json.loads(params_text)
            except json.JSONDecodeError as e:
                self._log_error(f"参数 JSON 解析失败: {e}")
                self.run_result_display.setHtml(
                    '<span style="color: red;">'
                    f'<b>参数格式错误:</b> {e}'
                    '</span>'
                )
                self.run_params_input.setFocus()
                return
            if not isinstance(parsed, dict):
                self._log_error("参数必须是 JSON 对象，不能是数组、字符串、数字或布尔值")
                self.run_result_display.setHtml(
                    '<span style="color: red;">'
                    '<b>参数格式错误:</b> 顶层必须是 JSON 对象 (&lbrace;&rbrace;)，'
                    '不能是数组、字符串、数字或布尔值'
                    '</span>'
                )
                self.run_params_input.setFocus()
                return
            params = parsed

        # Validate params values are JSON-safe (no Path, set, bytes, etc.)
        for key, value in params.items():
            if not isinstance(key, str):
                self.run_result_display.setHtml(
                    '<span style="color: red;">'
                    f'<b>参数格式错误:</b> 键必须是字符串'
                    '</span>'
                )
                self.run_params_input.setFocus()
                return
            if isinstance(value, (bytes, bytearray, set, frozenset)):
                self.run_result_display.setHtml(
                    '<span style="color: red;">'
                    f'<b>参数格式错误:</b> 键 "{key}" 的值类型不支持 '
                    f'({type(value).__name__})'
                    '</span>'
                )
                self.run_params_input.setFocus()
                return

        # Start run
        self._run_generation += 1
        gen = self._run_generation
        self._active_operation = 'run'

        # Clear previous run's artifacts (Batch 3.2.2)
        self._clear_artifact_state()

        started = self._runtime_controller.start_run(
            skill_id, version, params,
        )
        if not started:
            self._log_warn("运行未启动: 已有任务正在执行")
            self.run_result_display.setHtml(
                '<span style="color: orange;">已有任务正在执行，请等待完成或取消后再试</span>'
            )
            self._active_operation = None
            return

        self._log_info(f"开始运行技能: {skill_id}@{version}")
        self.run_result_display.setHtml(
            '<span style="color: blue;">运行中...</span>'
        )
        # Clear old healthcheck result area
        self._refresh_run_button_state()

    def _on_run_cancel(self) -> None:
        """Cancel the running operation."""
        self._log_info("正在取消运行...")
        self._runtime_controller.cancel()

    def _on_run_result(self, response: object) -> None:
        """Handle run completion (Batch 3.1.2)."""
        import json

        if self._closing:
            return

        status = getattr(response, 'status', 'unknown')
        message = getattr(response, 'message', '')
        duration_ms = getattr(response, 'duration_ms', 0)
        success = getattr(response, 'success', False)
        result = getattr(response, 'result', None)
        error_info = getattr(response, 'error', None)

        if status == "succeeded":
            # Business result display
            result_str = ""
            if result is not None and isinstance(result, dict):
                result_str = json.dumps(result, ensure_ascii=False, indent=2)
            elif result is not None:
                result_str = str(result)

            # Check for business-negative (ok=false)
            biz_ok = result.get("ok") if isinstance(result, dict) else None
            if biz_ok is False:
                # Business-negative: Runtime succeeded, business result is negative
                self._log_warn(
                    f"运行完成: 业务结果 ok=false (耗时 {duration_ms}ms)"
                )
                self.run_result_display.setHtml(
                    '<div style="color: #d48806;">'
                    '<b>Runtime 执行成功</b><br>'
                    f'status=succeeded &nbsp; success=True<br>'
                    f'耗时: {duration_ms}ms<br>'
                    '<b>业务结果: ok=false</b><br>'
                    f'<pre style="background:#fff7e6;padding:8px;border-radius:4px;'
                    f'max-height:200px;overflow:auto;">'
                    f'{result_str}'
                    f'</pre>'
                    '</div>'
                )
            else:
                self._log_success(
                    f"运行成功 (耗时 {duration_ms}ms)"
                )
                self.run_result_display.setHtml(
                    '<div style="color: green;">'
                    '<b>Runtime 执行成功</b><br>'
                    f'status=succeeded &nbsp; success=True<br>'
                    f'耗时: {duration_ms}ms<br>'
                    f'<pre style="background:#f6ffed;padding:8px;border-radius:4px;'
                    f'max-height:200px;overflow:auto;">'
                    f'{result_str}'
                    f'</pre>'
                    '</div>'
                )

        elif status == "failed":
            error_msg = ""
            if error_info is not None:
                error_msg = getattr(error_info, 'message', '')
            self._log_error(f"运行失败: {error_msg or message}")
            self.run_result_display.setHtml(
                '<div style="color: red;">'
                '<b>运行失败 (failed)</b><br>'
                f'success=False<br>'
                f'耗时: {duration_ms}ms<br>'
                f'<b>错误:</b> {error_msg or message}'
                '</div>'
            )

        elif status == "permission_denied":
            error_msg = getattr(error_info, 'message', '') if error_info else ''
            self._log_error(f"权限拒绝: {error_msg or message}")
            self.run_result_display.setHtml(
                '<div style="color: red;">'
                '<b>权限拒绝 (permission_denied)</b><br>'
                f'success=False<br>'
                f'<b>原因:</b> {error_msg or message}'
                '</div>'
            )

        elif status == "timeout":
            self._log_error(f"运行超时: {message}")
            self.run_result_display.setHtml(
                '<div style="color: #d4380d;">'
                '<b>运行超时 (timeout)</b><br>'
                f'success=False<br>'
                f'耗时: {duration_ms}ms<br>'
                f'{message}'
                '</div>'
            )

        elif status == "cancelled":
            self._log_warn("运行已取消")
            self.run_result_display.setHtml(
                '<div style="color: #8c8c8c;">'
                '<b>已取消 (cancelled)</b><br>'
                f'success=False<br>'
                f'耗时: {duration_ms}ms'
                '</div>'
            )

        elif status == "crashed":
            error_msg = getattr(error_info, 'message', '') if error_info else ''
            self._log_error(f"运行崩溃: {error_msg or message}")
            self.run_result_display.setHtml(
                '<div style="color: red;">'
                '<b>运行崩溃 (crashed)</b><br>'
                f'success=False<br>'
                f'<b>错误:</b> {error_msg or message}'
                '</div>'
            )

        elif status == "protocol_error":
            error_msg = getattr(error_info, 'message', '') if error_info else ''
            self._log_error(f"协议错误: {error_msg or message}")
            self.run_result_display.setHtml(
                '<div style="color: red;">'
                '<b>协议错误 (protocol_error)</b><br>'
                f'success=False<br>'
                f'<b>错误:</b> {error_msg or message}'
                '</div>'
            )

        else:
            self._log_info(f"运行完成: {status} — {message}")
            self.run_result_display.setHtml(
                '<div>'
                f'<b>运行完成: {status}</b><br>'
                f'success={success}<br>'
                f'{message}'
                '</div>'
            )

        # ── Artifact handling (Batch 3.2.2) ──
        response_artifacts = getattr(response, 'artifacts', ())
        response_operation = getattr(response, 'operation', 'healthcheck')

        # Only populate artifacts for "run" operations
        if response_operation == "run":
            if status == "succeeded" and response_artifacts:
                # Populate artifact list from response
                self._artifact_skill_id = getattr(response, 'task_id', '') and (
                    selected[0] if (selected := self._get_selected_skill()) else ""
                ) or ""
                # Get skill_id from the selected skill
                current_selection = self._get_selected_skill()
                if current_selection is not None:
                    self._artifact_skill_id = current_selection[0]
                self._artifact_task_id = getattr(response, 'task_id', '')
                self._populate_artifact_list(response_artifacts)
            elif status == "succeeded" and not response_artifacts:
                # Succeeded but no artifacts
                if self._get_selected_skill() is not None:
                    self._artifact_skill_id = self._get_selected_skill()[0]  # type: ignore[index]
                self._artifact_task_id = getattr(response, 'task_id', '')
                self._clear_artifact_state()
            else:
                # Non-succeeded: clear artifacts
                self._clear_artifact_state()
        # Healthcheck artifacts must not populate the run artifact list.
        # Only operation="run" results populate the artifact list.

    def _on_run_error(self, error_type: str, message: str) -> None:
        """Handle a run error from the controller."""
        if self._closing:
            return
        self.run_result_display.setHtml(
            '<div style="color: red;">'
            '<b>运行错误</b><br>'
            f'[{error_type}] {message}'
            '</div>'
        )
        self._log_error(f"运行错误 [{error_type}]: {message}")

    # ── Artifact UI handlers (Batch 3.2.2) ──

    def _clear_artifact_state(self) -> None:
        """Clear artifact list, owner state, and selection."""
        self.artifact_panel.clear_artifacts()
        self._artifact_skill_id = None
        self._artifact_task_id = None
        self._bridge_selection_count = 0
        self.artifact_panel.update_bridge_selection_ui(0, self._bridge_selection_max)
        self._refresh_artifact_buttons()

    def _populate_artifact_list(self, artifacts: tuple) -> None:
        """Populate the artifact tree widget from RuntimeArtifact objects."""
        if not artifacts:
            self.artifact_panel.show_no_artifacts_message()
            self._refresh_artifact_buttons()
            return

        self.artifact_panel.populate_artifacts(artifacts)
        self._refresh_artifact_buttons()

    def _get_selected_artifact_info(self) -> dict | None:
        """Get the owner info dict for the currently selected artifact row."""
        return self.artifact_panel.get_selected_info()

    def _on_artifact_selection_changed(self) -> None:
        """Enable/disable artifact action buttons based on selection."""
        self._refresh_artifact_buttons()

    def _refresh_artifact_buttons(self) -> None:
        """Centralized artifact button enable logic.

        Locate/Export: enabled when a valid artifact is selected AND
        no runtime or artifact operation is active AND widget is alive.
        Delete: enabled when artifact list is non-empty AND no operation
        is active AND widget is alive.
        """
        if self._closing:
            self.artifact_panel.set_buttons_enabled(False, False, False)
            return

        runtime_running = self._runtime_controller.is_running
        artifact_running = self._artifact_op_active

        if runtime_running or artifact_running:
            self.artifact_panel.set_buttons_enabled(False, False, False)
            return

        # Locate/Export: require a valid selection
        info = self._get_selected_artifact_info()
        has_selection = (
            info is not None
            and bool(info.get("artifact_id"))
            and bool(info.get("skill_id"))
            and bool(info.get("task_id"))
        )

        # Delete: requires non-empty list and valid owner
        has_items = self.artifact_panel.has_items
        has_owner = (
            self._artifact_skill_id is not None
            and self._artifact_task_id is not None
        )

        self.artifact_panel.set_buttons_enabled(
            locate=has_selection,
            export_=has_selection,
            delete=(has_items and has_owner),
        )

    def _on_artifact_locate(self) -> None:
        """Handle locate button click."""
        info = self._get_selected_artifact_info()
        if info is None:
            return
        skill_id = info.get("skill_id", "")
        task_id = info.get("task_id", "")
        artifact_id = info.get("artifact_id", "")
        if not skill_id or not task_id or not artifact_id:
            return

        self._artifact_op_active = True
        self._refresh_artifact_buttons()
        self._refresh_run_button_state()
        self.artifact_panel.set_status("正在定位文件...")

        started = self._runtime_controller.start_artifact_locate(
            skill_id, task_id, artifact_id,
        )
        if not started:
            self._artifact_op_active = False
            self._refresh_artifact_buttons()
            self._refresh_run_button_state()
            self.artifact_panel.set_status("")
            self._log_warn("无法启动定位操作: 已有任务正在执行")

    def _on_artifact_export(self) -> None:
        """Handle export button click with file dialog."""
        info = self._get_selected_artifact_info()
        if info is None:
            return
        skill_id = info.get("skill_id", "")
        task_id = info.get("task_id", "")
        artifact_id = info.get("artifact_id", "")
        if not skill_id or not task_id or not artifact_id:
            return

        # Get the display name for the default filename
        selected = self.artifact_list.selectedItems()
        default_name = ""
        if selected:
            default_name = selected[0].text(0) or "exported_file"

        target, _ = QFileDialog.getSaveFileName(
            self, "另存为...", default_name,
        )
        if not target:
            return  # User cancelled

        target_path = Path(target)

        # Freeze whether the target existed at confirmation time
        target_existed = target_path.exists()

        if target_existed:
            reply = QMessageBox.question(
                self, "确认覆盖",
                f"文件 '{target_path.name}' 已存在，是否覆盖？",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if reply != QMessageBox.StandardButton.Yes:
                return  # User declined overwrite

        # overwrite is True only when the target existed at confirmation
        # time AND the user explicitly agreed to overwrite
        overwrite = target_existed

        self._artifact_op_active = True
        self._refresh_artifact_buttons()
        self._refresh_run_button_state()
        self.artifact_panel.set_status("正在导出文件...")

        started = self._runtime_controller.start_artifact_export(
            skill_id, task_id, artifact_id,
            target=target_path,
            overwrite=overwrite,
        )
        if not started:
            self._artifact_op_active = False
            self._refresh_artifact_buttons()
            self._refresh_run_button_state()
            self.artifact_panel.set_status("")
            self._log_warn("无法启动导出操作: 已有任务正在执行")

    def _on_artifact_delete(self) -> None:
        """Handle delete button click with confirmation."""
        if self._artifact_skill_id is None or self._artifact_task_id is None:
            return

        reply = QMessageBox.warning(
            self, "确认删除",
            "将删除本次运行生成的全部文件，此操作不可撤销。\n\n确定要继续吗？",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return  # User cancelled

        skill_id = self._artifact_skill_id
        task_id = self._artifact_task_id

        self._artifact_op_active = True
        self._refresh_artifact_buttons()
        self._refresh_run_button_state()
        self.artifact_panel.set_status("正在删除文件...")

        started = self._runtime_controller.start_artifact_delete_task(
            skill_id, task_id,
        )
        if not started:
            self._artifact_op_active = False
            self._refresh_artifact_buttons()
            self._refresh_run_button_state()
            self.artifact_panel.set_status("")
            self._log_warn("无法启动删除操作: 已有任务正在执行")

    def _on_artifact_result(
        self,
        operation: str,
        success: bool,
        safe_message: str,
        generation: int,
    ) -> None:
        """Handle artifact operation result from controller."""
        if self._closing:
            return

        self._artifact_op_active = False

        if success:
            self.artifact_panel.set_status(safe_message, is_error=False)
            self._log_success(f"[Artifact] {safe_message}")

            if operation == "delete_task":
                self._clear_artifact_state()
        else:
            self.artifact_panel.set_status(safe_message, is_error=True)
            self._log_warn(f"[Artifact] {safe_message}")

        self._refresh_artifact_buttons()
        self._refresh_run_button_state()

    # ── Bridge selection handlers (Batch 3.3.2) ──

    # Media type → role UI convenience mapping (NOT security裁决)
    _MEDIA_TYPE_TO_ROLE: dict[str, str] = {
        "image/png": "image",
        "image/jpeg": "image",
        "text/csv": "table_source",
        "application/json": "text_source",
        "text/plain": "text_source",
    }

    _SUPPORTED_BRIDGE_MEDIA_TYPES: frozenset[str] = frozenset({
        "image/png", "image/jpeg", "text/csv",
        "application/json", "text/plain",
    })

    def _on_bridge_check_changed(self) -> None:
        """Handle checkbox toggle — update count and button state."""
        if self._closing:
            return
        count = self.artifact_panel.get_checked_count()
        max_count = self._bridge_selection_max

        # Reject >12 with fixed tooltip, don't silently truncate
        if count > max_count:
            self.artifact_panel.bridge_count_label.setStyleSheet(
                "color: #ff4d4f; font-size: 12px;"
            )
            self.artifact_panel.bridge_count_label.setToolTip(
                f"最多选择 {max_count} 个素材，无法选择第 {count} 项"
            )
            self.artifact_panel.send_to_report_btn.setEnabled(False)
            return

        self._bridge_selection_count = count
        self.artifact_panel.update_bridge_selection_ui(count, max_count)

    def _on_send_to_report(self) -> None:
        """Build selection list and emit send_to_report_requested signal."""
        if self._closing:
            return

        # Validate owner
        if self._artifact_skill_id is None or self._artifact_task_id is None:
            self._log_warn("无法发送到报告: 缺少 skill_id 或 task_id")
            return

        checked = self.artifact_panel.get_checked_artifacts()
        count = len(checked)
        if count < 1 or count > self._bridge_selection_max:
            return  # Button should already be disabled

        # Validate media types and build selections
        from dp_engine.report_bridge.models import ReportArtifactSelection, ReportAssetRole

        selections: list = []
        for order_idx, info in enumerate(checked):
            media_type = info.get("media_type", "")
            if media_type not in self._SUPPORTED_BRIDGE_MEDIA_TYPES:
                self._log_warn(
                    f"不支持的媒体类型: {media_type}，"
                    f"artifact_id={info.get('artifact_id', '?')}"
                )
                return  # Don't send if any unsupported type

            role_str = self._MEDIA_TYPE_TO_ROLE.get(media_type, "")
            try:
                role = ReportAssetRole(role_str)
            except ValueError:
                self._log_warn(f"无法映射角色: media_type={media_type}")
                return

            artifact_id = info.get("artifact_id", "")
            if not artifact_id:
                return

            selection = ReportArtifactSelection(
                schema_version=1,
                skill_id=self._artifact_skill_id,
                task_id=self._artifact_task_id,
                artifact_id=artifact_id,
                role=role,
                order=order_idx,
                display_name_hint=info.get("display_name", ""),
            )
            selections.append(selection)

        self.send_to_report_requested.emit(selections)

    # ── Run button state ──

    def _refresh_run_button_state(self) -> None:
        """Centralized Run button enable logic (Batch 3.1.2).

        Only enables the Run button when:
        - A skill is selected
        - The skill is installed and has a valid version
        - The manifest has a run entrypoint
        - No healthcheck or run task is running
        - The widget is not closing
        """
        if self._closing:
            self.run_btn.setEnabled(False)
            self.run_btn.setToolTip("页面正在关闭")
            self.header.set_run_enabled(False)
            return

        # Must have a selected skill
        selected = self._get_selected_skill()
        if selected is None:
            self.run_btn.setEnabled(False)
            self.run_btn.setToolTip("请先选中一个技能")
            self.header.set_run_enabled(False)
            return

        skill_id, version = selected

        # Must have registry loaded
        if self._registry is None:
            self.run_btn.setEnabled(False)
            self.run_btn.setToolTip("注册表未加载")
            self.header.set_run_enabled(False)
            return

        skill = self._registry.get(skill_id, version)
        if skill is None:
            self.run_btn.setEnabled(False)
            self.run_btn.setToolTip("技能未在注册表中找到")
            self.header.set_run_enabled(False)
            return

        # Must have run entrypoint
        run_ep = skill.manifest.entrypoints.get("run")
        if not run_ep:
            self.run_btn.setEnabled(False)
            self.run_btn.setToolTip(f"技能 '{skill_id}' 未声明 run 入口")
            self.header.set_run_enabled(False)
            return

        # Must not have any running task (runtime or artifact)
        if self._runtime_controller.is_running or self._artifact_op_active:
            self.run_btn.setEnabled(False)
            self.run_btn.setToolTip("已有任务正在运行")
            self.header.set_run_enabled(False)
            return

        # Parameter validation — only basic JSON syntax check
        params_text = self.run_params_input.text().strip()
        if params_text:
            import json
            try:
                parsed = json.loads(params_text)
                if not isinstance(parsed, dict):
                    self.run_btn.setEnabled(False)
                    self.run_btn.setToolTip("参数必须是 JSON 对象")
                    self.header.set_run_enabled(False)
                    return
            except json.JSONDecodeError:
                self.run_btn.setEnabled(False)
                self.run_btn.setToolTip("参数 JSON 格式无效")
                self.header.set_run_enabled(False)
                return

        # All conditions met
        self.run_btn.setEnabled(True)
        self.run_btn.setToolTip(f"运行 {skill_id}@{version}")
        self.header.set_run_enabled(True)

    # ── Healthcheck event handlers (Batch 3.0) ──

    def _on_healthcheck_start(self) -> None:
        """Start healthcheck for the selected skill."""
        self._ensure_registry()
        if self._registry is None:
            return

        selected = self._get_selected_skill()
        if selected is None:
            QMessageBox.information(self, "提示", "请先在表格中选中一个技能")
            return

        skill_id, version = selected
        skill = self._registry.get(skill_id, version)
        if skill is None:
            return

        # Check if manifest has healthcheck entrypoint
        healthcheck_ep = skill.manifest.entrypoints.get("healthcheck")
        if not healthcheck_ep:
            QMessageBox.information(
                self, "不支持健康检查",
                f"技能 '{skill_id}@{version}' 的 Manifest 中未声明 "
                f"healthcheck entrypoint，无法运行健康检查。"
            )
            return

        self._log_info(f"开始健康检查: {skill_id}@{version}")
        self._active_operation = 'healthcheck'
        self.healthcheck_btn.setEnabled(False)
        self.cancel_healthcheck_btn.setEnabled(True)

        self._runtime_controller.start_healthcheck(skill_id, version)

    def _on_healthcheck_cancel(self) -> None:
        """Cancel the running healthcheck."""
        self._log_info("正在取消健康检查...")
        self._runtime_controller.cancel()

    def _on_healthcheck_result(self, response: object) -> None:
        """Handle healthcheck completion."""
        self.healthcheck_btn.setEnabled(True)
        self.cancel_healthcheck_btn.setEnabled(False)

        if not hasattr(response, 'status'):
            return

        status = getattr(response, 'status', 'unknown')
        message = getattr(response, 'message', '')
        duration_ms = getattr(response, 'duration_ms', 0)

        if status == "healthy":
            self._log_success(
                f"健康检查通过: {message} (耗时 {duration_ms}ms)"
            )
        elif status == "unhealthy":
            self._log_error(
                f"健康检查未通过: {message} (耗时 {duration_ms}ms)"
            )
        elif status == "dependency_missing":
            self._log_warn(
                f"依赖缺失: {message}"
            )
            QMessageBox.warning(
                self, "依赖缺失",
                f"技能依赖未满足:\n\n{message}\n\n"
                f"系统不会自动安装依赖。请手动安装后重新检查。"
            )
        elif status == "not_supported":
            self._log_info(f"不支持健康检查: {message}")
        elif status == "timeout":
            self._log_error(f"健康检查超时: {message}")
        elif status == "cancelled":
            self._log_warn(f"健康检查已取消")
        elif status == "crashed":
            self._log_error(f"健康检查崩溃: {message}")
        elif status == "protocol_error":
            self._log_error(f"协议错误: {message}")
        elif status == "permission_denied":
            self._log_error(f"权限拒绝: {message}")
        else:
            self._log_info(f"健康检查完成: {status} — {message}")

        # Refresh registry table to show updated health status
        self._refresh_registry_table()
        self.registry_changed.emit()

    def _on_healthcheck_error(self, error_type: str, message: str) -> None:
        """Handle healthcheck error."""
        self.healthcheck_btn.setEnabled(True)
        self.cancel_healthcheck_btn.setEnabled(False)

        self._log_error(f"健康检查错误 [{error_type}]: {message}")
        QMessageBox.warning(
            self, "健康检查失败",
            f"健康检查执行失败:\n\n[{error_type}] {message}"
        )

        # Refresh in case partial state
        self._refresh_registry_table()

    def _on_view_runtime_log(self) -> None:
        """View the runtime log for the last healthcheck (placeholder)."""
        QMessageBox.information(
            self, "运行日志",
            "运行日志功能将在后续批次完善。\n"
            "日志文件位于: <应用数据>/skills/runtime/<task-id>/"
        )

    # ── UI helpers ──

    def _set_install_buttons_enabled(self, enabled: bool) -> None:
        """Enable or disable install-related buttons."""
        # Local install buttons (dummy) and download button (dummy)
        # The install panel manages its own button states

        # GitHub install button only if we have a valid inspection
        if enabled and self._last_inspection is not None:
            self.install_github_btn.setEnabled(True)
            self.install_github_btn.setVisible(True)
        elif not enabled:
            self.install_github_btn.setEnabled(False)
        else:
            valid = self._last_inspection is not None
            self.install_github_btn.setEnabled(valid)
            self.install_github_btn.setVisible(valid)

    # ── Panel synchronization (Batch UX-1) ──

    def _on_nav_skill_selected(self, skill_id: str, version: str) -> None:
        """Handle skill selection from the nav panel list."""
        if self._registry is None:
            return
        skills = self._registry.list_skills()
        for i, skill in enumerate(skills):
            if skill.skill_id == skill_id and skill.version == version:
                self.registry_table.blockSignals(True)
                self.registry_table.selectRow(i)
                self.registry_table.blockSignals(False)
                self._on_skill_selection_changed()
                return

    def _on_nav_install_requested(self) -> None:
        """Switch to install tab when install button is clicked in nav."""
        for i in range(self.tab_widget.count()):
            if self.tab_widget.tabText(i) == "安装与来源":
                self.tab_widget.setCurrentIndex(i)
                break

    def _on_view_details(self) -> None:
        """Open the skill details dialog."""
        from ui.skill_center.details_dialog import DetailsDialog
        data = self._build_skill_data()
        if data is None:
            QMessageBox.information(self, "提示", "请先在左侧列表中选择一个技能")
            return
        dlg = DetailsDialog(data, parent=self)
        dlg.exec()

    def _build_skill_data(self) -> dict | None:
        """Build a data dict for the currently selected skill."""
        self._ensure_registry()
        selected = self._get_selected_skill()
        if selected is None:
            return None
        skill_id, version = selected
        if self._registry is None:
            return None
        skill = self._registry.get(skill_id, version)
        if skill is None:
            return None

        active = self._registry.get_active(skill_id)
        is_active = (
            active is not None
            and active.skill_id == skill_id
            and active.version == version
        )

        return {
            "skill_id": skill.skill_id,
            "name": skill.manifest.name,
            "version": skill.version,
            "skill_type": skill.manifest.skill_type,
            "enabled": skill.enabled,
            "is_active": is_active,
            "description": skill.manifest.description,
            "entrypoints": skill.manifest.entrypoints,
            "permissions": getattr(skill.manifest, 'permissions', []) or [],
            "capabilities": skill.manifest.capabilities,
            "dependencies": skill.manifest.dependencies,
            "artifact_types": skill.manifest.artifact_types,
            "has_run_entrypoint": bool(skill.manifest.entrypoints.get("run")),
            "health_status": skill.health_status,
            "install_path": skill.install_path,
            "installed_at": skill.installed_at,
            "manifest": skill.manifest.to_dict(),
        }

    def _sync_all_panels(self) -> None:
        """Sync header, overview, nav, tab state and button states for current selection.

        Batch UX-1-E: redirects current tab away from disabled tabs BEFORE
        disabling them, so the visible page is never a disabled tab.

        Batch UX-1-F: on first launch (empty registry_table, registry loaded,
        no selection), auto-select the first installed skill so the user
        lands on Overview instead of Install & Source.
        """
        # ── First-launch auto-select (Batch UX-1-F) ──
        self._ensure_registry()
        if self._registry is not None and self._get_selected_skill() is None:
            skills = self._registry.list_skills()
            if skills:
                # Ensure table has enough rows for selectRow to succeed,
                # then auto-select the first skill without firing signals.
                self.registry_table.setRowCount(len(skills))
                self.registry_table.blockSignals(True)
                self.registry_table.selectRow(0)
                self.registry_table.blockSignals(False)

        data = self._build_skill_data()
        self.header.set_skill_data(data)
        self.overview_panel.set_skill_data(data)

        # ── Resolve tab indices once ──
        overview_idx = run_idx = artifact_idx = install_idx = log_idx = -1
        for idx in range(self.tab_widget.count()):
            tab_text = self.tab_widget.tabText(idx)
            if tab_text == "概览":
                overview_idx = idx
            elif tab_text == "运行":
                run_idx = idx
            elif tab_text == "产物":
                artifact_idx = idx
            elif tab_text == "安装与来源":
                install_idx = idx
            elif tab_text == "日志":
                log_idx = idx

        # ── Tab enable/disable + redirect contract (Batch UX-1-R + UX-1-E + UX-1-F) ──
        if data is None:
            # No skill selected: redirect away from disabled tabs FIRST,
            # then disable them.  Current tab must always be enabled.
            current = self.tab_widget.currentIndex()
            if current in (overview_idx, run_idx, artifact_idx):
                if install_idx >= 0:
                    self.tab_widget.setCurrentIndex(install_idx)

            # Batch UX-1-F: during construction (_initial_sync_done=False),
            # only redirect but do NOT disable tabs.  Tab disable is deferred
            # to showEvent so that artifact button logic remains testable
            # before the widget is first shown.
            if self._initial_sync_done:
                for idx in (overview_idx, run_idx, artifact_idx):
                    if idx >= 0:
                        self.tab_widget.setTabEnabled(idx, False)
                for idx in (install_idx, log_idx):
                    if idx >= 0:
                        self.tab_widget.setTabEnabled(idx, True)
        else:
            # Skill selected: enable all tabs, then navigate to overview
            for idx in range(self.tab_widget.count()):
                self.tab_widget.setTabEnabled(idx, True)
            if overview_idx >= 0:
                self.tab_widget.setCurrentIndex(overview_idx)

        # Mark initial construction sync as done AFTER the first pass
        # so subsequent calls (showEvent, selection changes) apply full state.
        if not self._initial_sync_done:
            self._initial_sync_done = True

        self._refresh_run_button_state()

    # ── Logging helpers ──

    def _log_info(self, msg: str) -> None:
        self.log_panel.append_info(msg)

    def _log_success(self, msg: str) -> None:
        self.log_panel.append_success(msg)

    def _log_warn(self, msg: str) -> None:
        self.log_panel.append_warn(msg)

    def _log_error(self, msg: str) -> None:
        self.log_panel.append_error(msg)

    # ── Help ──

    def _show_help(self) -> None:
        help_text = """# 技能插件中心 — 使用说明

## 当前已实现

* **GitHub 技能来源检查** — 输入仓库信息，检查 SKILL.md 是否存在
* **技能安装** — 从本地目录、本地 ZIP 或 GitHub 下载安装技能包
* **技能卸载** — 卸载已安装的技能版本
* **本地注册表管理** — 查看、启用/禁用、设置活动版本
* **Manifest 查看** — 查看技能元数据、依赖声明、能力声明
* **技能运行时 (SkillRuntime)** — 支持普通 run、结果回传、超时/取消/错误处理和产物 (Artifact) 管理
* **报告桥接 (Report Bridge)** — 支持将技能产物送入 Word/PPT 报告工作台
* **固定内置 Agent 工具** — AI Agent 仅调用经过审核的固定内置工具

## 尚未实现 (后续批次)

* **安装技能自动成为 Agent 工具** — 尚未实现；安装、启用技能不会自动把它注入 AI Agent
* **PPT Master/report_backend** — 专业报告生成后端尚未内置或自动接入

## 操作说明

### 安装技能
1. 本地目录: 点击「从本地目录安装」
2. 本地 ZIP: 点击「从本地 ZIP 安装」
3. GitHub: 先「检查技能来源」，确认后点击「从已检查 GitHub 来源安装」
   或直接在归档 URL 框中输入 https://github.com/owner/repo/archive/ref.zip
   点击「下载并安装」

### 管理已注册技能
1. 点击「刷新本地注册表」查看已注册的技能
2. 选中某行后可: 启用/禁用、设为活动版本、卸载、查看详情
3. 「unavailable」只是合法健康状态之一；SkillRuntime 已实现，健康状态与运行时是否存在是两回事
4. 技能声明 healthcheck 入口点时，应以健康检查结果确认其真实可用状态

### 运行技能与使用产物
1. 只有已启用、已选中且声明 run 入口点的技能才能运行
2. 运行参数必须是有效 JSON；运行结果、错误和取消状态会显示在运行页与日志页
3. 技能发布的产物可在「产物」页管理，并可选择发送到报告工作台
"""
        help_dialog = QDialog(self)
        help_dialog.setWindowTitle("技能插件中心 — 使用说明")
        help_dialog.resize(850, 650)

        layout = QVBoxLayout()
        text_edit = QTextEdit()
        text_edit.setReadOnly(True)
        text_edit.setPlainText(help_text)
        layout.addWidget(text_edit)

        close_btn = QPushButton("关闭")
        close_btn.clicked.connect(help_dialog.close)
        layout.addWidget(close_btn)

        help_dialog.setLayout(layout)
        help_dialog.exec()
