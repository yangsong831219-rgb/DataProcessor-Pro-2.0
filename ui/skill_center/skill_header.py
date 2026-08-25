"""技能插件中心 — 技能头部组件 (Batch UX-1).

显示选中技能的摘要信息和主要操作按钮。
"""

from __future__ import annotations

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QWidget, QHBoxLayout, QVBoxLayout, QLabel,
    QPushButton, QMenu, QFrame,
)
from ui.skill_center.style import STYLE


class SkillHeader(QWidget):
    """技能头部 — 名称、版本、状态、运行/更多操作."""

    run_requested = pyqtSignal()
    stop_requested = pyqtSignal()
    set_active_requested = pyqtSignal()
    toggle_enabled_requested = pyqtSignal()
    view_details_requested = pyqtSignal()
    uninstall_requested = pyqtSignal()
    help_requested = pyqtSignal()
    install_requested = pyqtSignal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._skill_data: dict | None = None
        self._is_running: bool = False
        self._build_ui()

    def _build_ui(self) -> None:
        main_layout = QHBoxLayout(self)
        main_layout.setContentsMargins(0, 0, 0, 0)

        # Card container
        card = QFrame()
        card.setStyleSheet(f"""
            QFrame {{
                {STYLE.CARD_STYLE}
                padding: 0;
            }}
        """)
        card_layout = QHBoxLayout(card)
        card_layout.setContentsMargins(
            STYLE.SPACE_LG, STYLE.SPACE_MD, STYLE.SPACE_LG, STYLE.SPACE_MD,
        )

        # Left: skill info
        info_layout = QVBoxLayout()
        info_layout.setSpacing(STYLE.SPACE_XS)

        self.name_label = QLabel("请选择一个技能")
        self.name_label.setStyleSheet(
            f"color: {STYLE.TEXT_PRIMARY}; font-size: {STYLE.FONT_LG}px;"
            f" font-weight: 600;"
        )
        info_layout.addWidget(self.name_label)

        self.description_label = QLabel("")
        self.description_label.setStyleSheet(
            f"color: {STYLE.TEXT_SECONDARY}; font-size: {STYLE.FONT_SM}px;"
        )
        self.description_label.setWordWrap(True)
        self.description_label.setMaximumHeight(32)
        self.description_label.setVisible(False)
        info_layout.addWidget(self.description_label)

        # Badge row (hidden when no skill)
        self._badge_row = QHBoxLayout()
        self._badge_row.setSpacing(STYLE.SPACE_SM)

        self.version_label = QLabel("")
        self.version_label.setStyleSheet(
            f"color: {STYLE.TEXT_MUTED}; font-size: {STYLE.FONT_XS}px;"
        )
        self.version_label.setVisible(False)
        self._badge_row.addWidget(self.version_label)

        self.status_badge = QLabel("")
        self.status_badge.setVisible(False)
        self._badge_row.addWidget(self.status_badge)

        self.type_label = QLabel("")
        self.type_label.setStyleSheet(
            f"color: {STYLE.TEXT_MUTED}; font-size: {STYLE.FONT_XS}px;"
            f" background: {STYLE.BG_PAGE}; border-radius: 4px; padding: 2px 8px;"
        )
        self.type_label.setVisible(False)
        self._badge_row.addWidget(self.type_label)

        self._badge_row.addStretch()
        info_layout.addLayout(self._badge_row)

        # Empty-state subtitle + install button (shown when no skill)
        self._empty_subtitle = QLabel(
            '从左侧列表选择一个已安装技能，或前往"安装与来源"安装新技能。'
        )
        self._empty_subtitle.setStyleSheet(
            f"color: {STYLE.TEXT_SECONDARY}; font-size: {STYLE.FONT_SM}px;"
        )
        self._empty_subtitle.setWordWrap(True)
        self._empty_subtitle.setVisible(True)
        info_layout.addWidget(self._empty_subtitle)

        self._empty_install_btn = QPushButton("安装技能")
        self._empty_install_btn.setStyleSheet(f"""
            QPushButton {{
                color: {STYLE.PRIMARY};
                border: 1px solid {STYLE.PRIMARY};
                border-radius: {STYLE.RADIUS_SM}px;
                padding: 6px 16px;
                background: transparent;
                font-size: {STYLE.FONT_SM}px;
            }}
            QPushButton:hover {{
                background: #EFF6FF;
            }}
        """)
        self._empty_install_btn.setMinimumHeight(STYLE.BTN_HEIGHT)
        self._empty_install_btn.clicked.connect(self.install_requested.emit)
        self._empty_install_btn.setVisible(True)
        info_layout.addWidget(self._empty_install_btn)

        card_layout.addLayout(info_layout, 1)

        # Right: action buttons
        action_layout = QHBoxLayout()
        action_layout.setSpacing(STYLE.SPACE_SM)

        self.run_btn = QPushButton("▶ 运行技能")
        self.run_btn.setStyleSheet(STYLE.BTN_PRIMARY_STYLE)
        self.run_btn.setMinimumHeight(STYLE.BTN_HEIGHT_LG)
        self.run_btn.clicked.connect(self.run_requested.emit)
        self.run_btn.setVisible(False)
        self.run_btn.setEnabled(False)
        action_layout.addWidget(self.run_btn)

        self.stop_btn = QPushButton("■ 停止")
        self.stop_btn.setStyleSheet(f"""
            QPushButton {{
                background-color: {STYLE.DANGER};
                color: white;
                border: none;
                border-radius: {STYLE.RADIUS_SM}px;
                padding: 6px 16px;
                font-weight: 500;
            }}
            QPushButton:hover {{
                background-color: {STYLE.DANGER_HOVER};
            }}
            QPushButton:disabled {{
                background-color: #FCA5A5;
            }}
        """)
        self.stop_btn.setMinimumHeight(STYLE.BTN_HEIGHT_LG)
        self.stop_btn.clicked.connect(self.stop_requested.emit)
        self.stop_btn.setVisible(False)
        action_layout.addWidget(self.stop_btn)

        # More menu
        self.more_btn = QPushButton("⋮")
        self.more_btn.setStyleSheet(f"""
            QPushButton {{
                color: {STYLE.TEXT_SECONDARY};
                border: 1px solid {STYLE.BORDER};
                border-radius: {STYLE.RADIUS_SM}px;
                padding: 6px 12px;
                font-size: 18px;
                font-weight: 700;
                background: {STYLE.BG_CARD};
            }}
            QPushButton:hover {{
                background: {STYLE.BG_PAGE};
                border-color: {STYLE.TEXT_MUTED};
            }}
        """)
        self.more_btn.setMinimumHeight(STYLE.BTN_HEIGHT_LG)
        self.more_btn.setToolTip("更多操作")
        self.more_btn.setVisible(False)
        self._build_more_menu()
        action_layout.addWidget(self.more_btn)

        card_layout.addLayout(action_layout)
        main_layout.addWidget(card, 1)

    def _build_more_menu(self) -> None:
        """Build the popup menu for the more button."""
        self._more_menu = QMenu(self)

        action = self._more_menu.addAction("设置为活动版本")
        if action is not None:
            action.triggered.connect(self.set_active_requested.emit)

        action = self._more_menu.addAction("启用 / 禁用")
        if action is not None:
            action.triggered.connect(self.toggle_enabled_requested.emit)

        self._more_menu.addSeparator()

        action = self._more_menu.addAction("查看技能详情")
        if action is not None:
            action.triggered.connect(self.view_details_requested.emit)

        self._more_menu.addSeparator()

        action = self._more_menu.addAction("使用说明")
        if action is not None:
            action.triggered.connect(self.help_requested.emit)

        self._more_menu.addSeparator()

        action = self._more_menu.addAction("卸载当前版本")
        if action is not None:
            action.triggered.connect(self.uninstall_requested.emit)

        self.more_btn.setMenu(self._more_menu)

    # ── Public API ──

    def set_skill_data(self, data: dict | None) -> None:
        """Update header with skill data or clear to empty state."""
        self._skill_data = data
        if data is None:
            # ── Empty state: compact header ──
            self.name_label.setText("请选择一个技能")
            # Hide all skill-info widgets (not just clear text)
            self.description_label.setVisible(False)
            self.version_label.setVisible(False)
            self.status_badge.setVisible(False)
            self.type_label.setVisible(False)
            self.run_btn.setVisible(False)
            self.stop_btn.setVisible(False)
            self.more_btn.setVisible(False)
            # Show empty-state subtitle and install button
            self._empty_subtitle.setVisible(True)
            self._empty_install_btn.setVisible(True)
        else:
            # ── Skill selected: restore skill-info widgets ──
            name = data.get("name", data.get("skill_id", ""))
            self.name_label.setText(name)
            self.description_label.setText(data.get("description", ""))
            self.description_label.setVisible(True)
            self.version_label.setText(f"v{data.get('version', '')}")
            self.version_label.setVisible(True)

            enabled = data.get("enabled", True)
            if enabled:
                self.status_badge.setText("已启用")
                self.status_badge.setStyleSheet(STYLE.BADGE_ENABLED)
            else:
                self.status_badge.setText("已禁用")
                self.status_badge.setStyleSheet(STYLE.BADGE_DISABLED)
            self.status_badge.setVisible(True)

            skill_type = data.get("skill_type", "")
            self.type_label.setText(skill_type if skill_type else "")
            self.type_label.setVisible(True)

            has_run = data.get("has_run_entrypoint", False)
            self.run_btn.setVisible(has_run)
            self.more_btn.setVisible(True)
            self.stop_btn.setVisible(False)
            # Hide empty-state widgets
            self._empty_subtitle.setVisible(False)
            self._empty_install_btn.setVisible(False)

    def set_running_state(self, running: bool) -> None:
        """Toggle between run and stop button visibility."""
        self._is_running = running
        if running:
            self.run_btn.setVisible(False)
            self.stop_btn.setVisible(True)
        else:
            has_run = (
                self._skill_data is not None
                and self._skill_data.get("has_run_entrypoint", False)
            )
            self.run_btn.setVisible(has_run)
            self.stop_btn.setVisible(False)

    def set_run_enabled(self, enabled: bool) -> None:
        """Enable or disable the run button."""
        self.run_btn.setEnabled(enabled)

    def set_stop_enabled(self, enabled: bool) -> None:
        """Enable or disable the stop button."""
        self.stop_btn.setEnabled(enabled)
