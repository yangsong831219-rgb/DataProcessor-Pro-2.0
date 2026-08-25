"""技能插件中心 — 概览标签页 (Batch UX-1).

显示技能的元数据摘要：版本、入口点、权限、能力、依赖、安装来源、运行状态。
"""

from __future__ import annotations

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel,
    QPushButton, QFrame, QGridLayout, QScrollArea,
)
from ui.skill_center.style import STYLE


class OverviewPanel(QWidget):
    """概览标签 — 技能元数据摘要."""

    view_details_requested = pyqtSignal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._skill_data: dict | None = None
        self._build_ui()

    def _build_ui(self) -> None:
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(0, 0, 0, 0)

        # Scroll area
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setStyleSheet(f"QScrollArea {{ background: transparent; }}")

        content = QWidget()
        content_layout = QVBoxLayout(content)
        content_layout.setContentsMargins(0, 0, 0, 0)
        content_layout.setSpacing(STYLE.SPACE_MD)

        # ── Info card ──
        self.info_card, info_card_layout = self._make_card("技能信息")
        self.info_grid = QGridLayout()
        self.info_grid.setSpacing(STYLE.SPACE_SM)
        info_card_layout.addLayout(self.info_grid)
        content_layout.addWidget(self.info_card)

        # ── Entrypoints card ──
        self.entrypoints_card, entrypoints_card_layout = self._make_card("入口点")
        self.entrypoints_layout = QVBoxLayout()
        self.entrypoints_layout.setSpacing(STYLE.SPACE_XS)
        entrypoints_card_layout.addLayout(self.entrypoints_layout)
        content_layout.addWidget(self.entrypoints_card)

        # ── Permissions card ──
        self.permissions_card, perms_card_layout = self._make_card("权限")
        self.permissions_layout = QVBoxLayout()
        self.permissions_layout.setSpacing(STYLE.SPACE_XS)
        perms_card_layout.addLayout(self.permissions_layout)
        content_layout.addWidget(self.permissions_card)

        # ── Capabilities card ──
        self.capabilities_card, caps_card_layout = self._make_card("能力")
        self.capabilities_layout = QVBoxLayout()
        self.capabilities_layout.setSpacing(STYLE.SPACE_XS)
        caps_card_layout.addLayout(self.capabilities_layout)
        content_layout.addWidget(self.capabilities_card)

        # ── Dependencies card ──
        self.dependencies_card, deps_card_layout = self._make_card("依赖")
        self.dependencies_layout = QVBoxLayout()
        self.dependencies_layout.setSpacing(STYLE.SPACE_XS)
        deps_card_layout.addLayout(self.dependencies_layout)
        content_layout.addWidget(self.dependencies_card)

        # ── Install source card ──
        self.source_card, source_card_layout = self._make_card("安装来源")
        self.source_layout = QVBoxLayout()
        self.source_layout.setSpacing(STYLE.SPACE_XS)
        source_card_layout.addLayout(self.source_layout)
        content_layout.addWidget(self.source_card)

        # ── View details button ──
        self.view_details_btn = QPushButton("查看技能详情")
        self.view_details_btn.setStyleSheet(f"""
            QPushButton {{
                color: {STYLE.PRIMARY};
                border: 1px solid {STYLE.PRIMARY};
                border-radius: {STYLE.RADIUS_SM}px;
                padding: 8px 16px;
                background: transparent;
            }}
            QPushButton:hover {{
                background: #EFF6FF;
            }}
        """)
        self.view_details_btn.clicked.connect(self.view_details_requested.emit)
        self.view_details_btn.setVisible(False)
        content_layout.addWidget(self.view_details_btn)

        content_layout.addStretch()

        scroll.setWidget(content)
        main_layout.addWidget(scroll, 1)

    @staticmethod
    def _make_card(title: str) -> tuple[QFrame, QVBoxLayout]:
        """Create a card with a title label. Returns (frame, child_layout)."""
        card = QFrame()
        card.setStyleSheet(STYLE.CARD_STYLE)
        card_layout = QVBoxLayout(card)
        card_layout.setContentsMargins(
            STYLE.SPACE_LG, STYLE.SPACE_MD, STYLE.SPACE_LG, STYLE.SPACE_MD,
        )
        card_layout.setSpacing(STYLE.SPACE_SM)

        title_label = QLabel(title)
        title_label.setStyleSheet(
            f"color: {STYLE.TEXT_PRIMARY}; font-size: {STYLE.FONT_SM}px;"
            f" font-weight: 600; border: none;"
        )
        card_layout.addWidget(title_label)
        return card, card_layout

    # ── Helpers ──

    @staticmethod
    def _make_info_row(label: str, value: str) -> tuple[QLabel, QLabel]:
        """Create a label-value pair styled for info grid."""
        lbl = QLabel(label)
        lbl.setStyleSheet(f"color: {STYLE.TEXT_SECONDARY}; font-size: {STYLE.FONT_SM}px;")
        val = QLabel(value)
        val.setStyleSheet(f"color: {STYLE.TEXT_PRIMARY}; font-size: {STYLE.FONT_SM}px;")
        val.setWordWrap(True)
        val.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
        )
        return lbl, val

    def set_skill_data(self, data: dict | None) -> None:
        """Populate overview with skill metadata."""
        self._skill_data = data

        # Clear all layouts
        self._clear_grid(self.info_grid)
        self._clear_layout(self.entrypoints_layout)
        self._clear_layout(self.permissions_layout)
        self._clear_layout(self.capabilities_layout)
        self._clear_layout(self.dependencies_layout)
        self._clear_layout(self.source_layout)

        if data is None:
            self.view_details_btn.setVisible(False)
            return

        self.view_details_btn.setVisible(True)

        # ── Info grid ──
        info_items = [
            ("技能ID", data.get("skill_id", "—")),
            ("名称", data.get("name", "—")),
            ("版本", f"v{data.get('version', '—')}"),
            ("类型", data.get("skill_type", "—")),
            ("状态", "已启用" if data.get("enabled", True) else "已禁用"),
            ("活动版本", "是 ★" if data.get("is_active", False) else "否"),
            ("运行状态", data.get("health_status", "—")),
            ("说明", data.get("description", "—")),
        ]
        for row, (lbl_text, val_text) in enumerate(info_items):
            lbl, val = self._make_info_row(lbl_text, val_text)
            self.info_grid.addWidget(lbl, row, 0)
            self.info_grid.addWidget(val, row, 1)

        # ── Entrypoints ──
        entrypoints = data.get("entrypoints", {})
        if entrypoints:
            for ep_name, ep_path in entrypoints.items():
                ep_label = QLabel(f"{ep_name}: {ep_path}")
                ep_label.setStyleSheet(
                    f"color: {STYLE.TEXT_PRIMARY}; font-size: {STYLE.FONT_SM}px;"
                )
                self.entrypoints_layout.addWidget(ep_label)
        else:
            self._add_empty_label(self.entrypoints_layout, "无入口点声明")

        # ── Permissions ──
        permissions = data.get("permissions", [])
        if permissions:
            for perm in permissions:
                perm_label = QLabel(f"  • {perm}")
                perm_label.setStyleSheet(
                    f"color: {STYLE.TEXT_PRIMARY}; font-size: {STYLE.FONT_SM}px;"
                )
                self.permissions_layout.addWidget(perm_label)
        else:
            self._add_empty_label(self.permissions_layout, "无权限声明")

        # ── Capabilities ──
        capabilities = data.get("capabilities", [])
        if capabilities:
            for cap in capabilities:
                cap_label = QLabel(f"  • {cap}")
                cap_label.setStyleSheet(
                    f"color: {STYLE.TEXT_PRIMARY}; font-size: {STYLE.FONT_SM}px;"
                )
                self.capabilities_layout.addWidget(cap_label)
        else:
            self._add_empty_label(self.capabilities_layout, "无能力声明")

        # ── Dependencies ──
        dependencies = data.get("dependencies", [])
        if dependencies:
            for dep in dependencies:
                dep_label = QLabel(f"  • {dep}")
                dep_label.setStyleSheet(
                    f"color: {STYLE.TEXT_PRIMARY}; font-size: {STYLE.FONT_SM}px;"
                )
                self.dependencies_layout.addWidget(dep_label)
        else:
            self._add_empty_label(self.dependencies_layout, "无依赖声明")

        # ── Install source ──
        install_path = data.get("install_path", "")
        installed_at = data.get("installed_at", "")
        if install_path:
            path_label = QLabel(f"路径: {install_path}")
            path_label.setStyleSheet(
                f"color: {STYLE.TEXT_PRIMARY}; font-size: {STYLE.FONT_SM}px;"
            )
            path_label.setWordWrap(True)
            self.source_layout.addWidget(path_label)
        if installed_at:
            time_label = QLabel(f"安装时间: {installed_at}")
            time_label.setStyleSheet(
                f"color: {STYLE.TEXT_MUTED}; font-size: {STYLE.FONT_XS}px;"
            )
            self.source_layout.addWidget(time_label)
        if not install_path and not installed_at:
            self._add_empty_label(self.source_layout, "无安装来源信息")

    @staticmethod
    def _add_empty_label(layout: QVBoxLayout, text: str) -> None:
        empty = QLabel(text)
        empty.setStyleSheet(f"color: {STYLE.TEXT_MUTED}; font-size: {STYLE.FONT_SM}px;")
        layout.addWidget(empty)

    @staticmethod
    def _clear_grid(grid: QGridLayout) -> None:
        """Remove all widgets from a grid layout."""
        while grid.count():
            item = grid.takeAt(0)
            if item is not None:
                w = item.widget()
                if w is not None:
                    w.deleteLater()

    @staticmethod
    def _clear_layout(layout: QVBoxLayout) -> None:
        """Remove all widgets from a vertical layout."""
        while layout.count():
            item = layout.takeAt(0)
            if item is not None:
                w = item.widget()
                if w is not None:
                    w.deleteLater()
