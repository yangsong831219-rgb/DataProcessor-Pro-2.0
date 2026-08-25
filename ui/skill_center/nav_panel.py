"""技能插件中心 — 左侧技能导航面板 (Batch UX-1).

包含搜索框、状态过滤、技能列表和底部安装按钮。
"""

from __future__ import annotations

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit,
    QListWidget, QListWidgetItem, QPushButton, QComboBox,
    QFrame,
)
from ui.skill_center.style import STYLE


class SkillNavPanel(QWidget):
    """左侧技能导航面板 — 搜索、过滤、列表、安装入口."""

    skill_selected = pyqtSignal(str, str)   # skill_id, version
    install_requested = pyqtSignal()
    filter_changed = pyqtSignal(str)        # "all", "enabled", "disabled"

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("skillNavPanel")
        self._all_skills: list[dict] = []
        self._filter: str = "all"
        self._build_ui()

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # Container with card style
        container = QFrame()
        container.setStyleSheet(STYLE.LEFT_PANEL_STYLE)
        container_layout = QVBoxLayout(container)
        container_layout.setContentsMargins(
            STYLE.SPACE_MD, STYLE.SPACE_MD, STYLE.SPACE_MD, STYLE.SPACE_SM,
        )
        container_layout.setSpacing(STYLE.SPACE_SM)

        # Title
        title = QLabel("技能插件")
        title.setStyleSheet(
            f"color: {STYLE.TEXT_PRIMARY}; font-size: {STYLE.FONT_LG}px;"
            f" font-weight: 700; padding: {STYLE.SPACE_SM}px 0;"
        )
        container_layout.addWidget(title)

        # Search box
        self.search_input = QLineEdit()
        self.search_input.setPlaceholderText("搜索技能...")
        self.search_input.setClearButtonEnabled(True)
        self.search_input.setStyleSheet(f"""
            QLineEdit {{
                border: 1px solid {STYLE.BORDER};
                border-radius: {STYLE.RADIUS_SM}px;
                padding: 6px 10px;
                font-size: {STYLE.FONT_SM}px;
                background: {STYLE.BG_PAGE};
            }}
            QLineEdit:focus {{
                border-color: {STYLE.PRIMARY};
            }}
        """)
        self.search_input.textChanged.connect(self._on_search_changed)
        container_layout.addWidget(self.search_input)

        # Status filter
        self.filter_combo = QComboBox()
        self.filter_combo.addItems(["全部", "已启用", "已禁用"])
        self.filter_combo.setStyleSheet(f"""
            QComboBox {{
                border: 1px solid {STYLE.BORDER};
                border-radius: {STYLE.RADIUS_SM}px;
                padding: 5px 8px;
                font-size: {STYLE.FONT_SM}px;
                background: {STYLE.BG_CARD};
            }}
        """)
        self.filter_combo.currentIndexChanged.connect(self._on_filter_changed)
        container_layout.addWidget(self.filter_combo)

        # Skill list
        self.skill_list = QListWidget()
        self.skill_list.setStyleSheet(f"""
            QListWidget {{
                border: 1px solid {STYLE.BORDER};
                border-radius: {STYLE.RADIUS_SM}px;
                background: {STYLE.BG_PAGE};
                font-size: {STYLE.FONT_SM}px;
                outline: none;
            }}
            QListWidget::item {{
                padding: {STYLE.SPACE_SM}px {STYLE.SPACE_MD}px;
                border-bottom: 1px solid {STYLE.BORDER};
            }}
            QListWidget::item:selected {{
                background: #DBEAFE;
                color: {STYLE.TEXT_PRIMARY};
            }}
            QListWidget::item:hover {{
                background: #EFF6FF;
            }}
        """)
        self.skill_list.currentItemChanged.connect(self._on_item_changed)
        container_layout.addWidget(self.skill_list, 1)

        # Install button at bottom
        btn_row = QHBoxLayout()
        install_btn = QPushButton("+ 安装技能")
        install_btn.setStyleSheet(STYLE.BTN_PRIMARY_STYLE)
        install_btn.setMinimumHeight(STYLE.BTN_HEIGHT_LG)
        install_btn.clicked.connect(self.install_requested.emit)
        btn_row.addWidget(install_btn, 1)
        container_layout.addLayout(btn_row)

        layout.addWidget(container, 1)

    # ── Public API ──

    def set_skills(self, skills: list[dict]) -> None:
        """Set the skill list data.

        Each dict: {skill_id, name, version, enabled, skill_type, is_active}
        """
        self._all_skills = skills
        self._apply_filter_and_search()

    def _apply_filter_and_search(self) -> None:
        """Re-filter and re-populate the list widget."""
        search_text = self.search_input.text().strip().lower()
        self.skill_list.blockSignals(True)
        self.skill_list.clear()

        for skill in self._all_skills:
            # Filter by status
            if self._filter == "enabled" and not skill.get("enabled", True):
                continue
            if self._filter == "disabled" and skill.get("enabled", True):
                continue

            # Filter by search
            name = skill.get("name", "")
            skill_id = skill.get("skill_id", "")
            if search_text:
                if search_text not in name.lower() and search_text not in skill_id.lower():
                    continue

            item = QListWidgetItem()
            item.setData(Qt.ItemDataRole.UserRole, skill)

            # Build display text
            name_text = name or skill_id
            version_text = skill.get("version", "")
            is_active = skill.get("is_active", False)
            prefix = "★ " if is_active else ""
            display_text = f"{prefix}{name_text}"
            if version_text:
                display_text += f"  v{version_text}"

            item.setText(display_text)

            # Type tag via tooltip
            skill_type = skill.get("skill_type", "")
            enabled = skill.get("enabled", True)
            if skill_type:
                item.setToolTip(f"{name_text} ({skill_type}) — {'已启用' if enabled else '已禁用'}")

            self.skill_list.addItem(item)

        self.skill_list.blockSignals(False)

    # ── Handlers ──

    def _on_search_changed(self, _text: str) -> None:
        self._apply_filter_and_search()

    def _on_filter_changed(self, index: int) -> None:
        filters = ["all", "enabled", "disabled"]
        self._filter = filters[index] if 0 <= index < len(filters) else "all"
        self.filter_changed.emit(self._filter)
        self._apply_filter_and_search()

    def _on_item_changed(self, current: QListWidgetItem | None, _previous: QListWidgetItem | None) -> None:
        if current is None:
            return
        skill_data = current.data(Qt.ItemDataRole.UserRole)
        if isinstance(skill_data, dict):
            skill_id = skill_data.get("skill_id", "")
            version = skill_data.get("version", "")
            self.skill_selected.emit(skill_id, version)

    def select_skill(self, skill_id: str, version: str) -> None:
        """Programmatically select a skill in the list."""
        for i in range(self.skill_list.count()):
            item = self.skill_list.item(i)
            if item is None:
                continue
            data = item.data(Qt.ItemDataRole.UserRole)
            if isinstance(data, dict):
                if data.get("skill_id") == skill_id and data.get("version") == version:
                    self.skill_list.setCurrentItem(item)
                    return
