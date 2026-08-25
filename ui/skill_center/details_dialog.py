"""技能插件中心 — 技能详情对话框 (Batch UX-1).

只读窗口，内含 Manifest、依赖、能力、权限四个标签页。
"""

from __future__ import annotations

import json

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel,
    QPushButton, QTextEdit, QTabWidget, QWidget,
)
from ui.skill_center.style import STYLE


class DetailsDialog(QDialog):
    """只读技能详情窗口 — Manifest / 依赖 / 能力 / 权限."""

    def __init__(
        self,
        skill_data: dict,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._skill_data = skill_data
        self._build_ui()

    def _build_ui(self) -> None:
        skill_id = self._skill_data.get("skill_id", "")
        version = self._skill_data.get("version", "")
        name = self._skill_data.get("name", skill_id)
        self.setWindowTitle(f"技能详情: {name} v{version}")
        self.resize(750, 550)
        self.setMinimumSize(600, 400)

        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(
            STYLE.SPACE_LG, STYLE.SPACE_LG, STYLE.SPACE_LG, STYLE.SPACE_LG,
        )
        main_layout.setSpacing(STYLE.SPACE_MD)

        # Header
        header_label = QLabel(f"{name}  v{version}")
        header_label.setStyleSheet(
            f"color: {STYLE.TEXT_PRIMARY}; font-size: {STYLE.FONT_LG}px;"
            f" font-weight: 600;"
        )
        main_layout.addWidget(header_label)

        # Tabs
        tabs = QTabWidget()
        tabs.setStyleSheet(STYLE.TAB_STYLE)

        # Manifest tab
        manifest_tab = QWidget()
        manifest_layout = QVBoxLayout(manifest_tab)
        manifest_text = QTextEdit()
        manifest_text.setReadOnly(True)
        manifest_json = json.dumps(
            self._skill_data.get("manifest", {}),
            ensure_ascii=False, indent=2,
        )
        manifest_text.setPlainText(manifest_json)
        manifest_text.setStyleSheet(f"""
            QTextEdit {{
                font-family: Consolas, monospace;
                font-size: {STYLE.FONT_SM}px;
                border: 1px solid {STYLE.BORDER};
                border-radius: {STYLE.RADIUS_SM}px;
                background: {STYLE.BG_PAGE};
            }}
        """)
        manifest_layout.addWidget(manifest_text)
        tabs.addTab(manifest_tab, "Manifest")

        # Dependencies tab
        deps_tab = QWidget()
        deps_layout = QVBoxLayout(deps_tab)
        deps_text = QTextEdit()
        deps_text.setReadOnly(True)
        deps = self._skill_data.get("dependencies", [])
        if deps:
            deps_content = "\n".join(f"  - {d}" for d in deps)
        else:
            deps_content = "（未声明依赖）"
        deps_text.setPlainText(deps_content)
        deps_text.setStyleSheet(f"""
            QTextEdit {{
                font-family: Consolas, monospace;
                font-size: {STYLE.FONT_SM}px;
                border: 1px solid {STYLE.BORDER};
                border-radius: {STYLE.RADIUS_SM}px;
                background: {STYLE.BG_PAGE};
            }}
        """)
        deps_layout.addWidget(deps_text)
        tabs.addTab(deps_tab, "依赖")

        # Capabilities tab
        caps_tab = QWidget()
        caps_layout = QVBoxLayout(caps_tab)
        caps_text = QTextEdit()
        caps_text.setReadOnly(True)
        caps = self._skill_data.get("capabilities", [])
        if caps:
            caps_content = "\n".join(f"  - {c}" for c in caps)
        else:
            caps_content = "（未声明能力）"
        caps_text.setPlainText(caps_content)
        caps_text.setStyleSheet(f"""
            QTextEdit {{
                font-family: Consolas, monospace;
                font-size: {STYLE.FONT_SM}px;
                border: 1px solid {STYLE.BORDER};
                border-radius: {STYLE.RADIUS_SM}px;
                background: {STYLE.BG_PAGE};
            }}
        """)
        caps_layout.addWidget(caps_text)
        tabs.addTab(caps_tab, "能力")

        # Permissions tab
        perms_tab = QWidget()
        perms_layout = QVBoxLayout(perms_tab)
        perms_text = QTextEdit()
        perms_text.setReadOnly(True)
        perms = self._skill_data.get("permissions", [])
        if perms:
            perms_content = "\n".join(f"  - {p}" for p in perms)
        else:
            perms_content = "（未声明权限）"
        perms_text.setPlainText(perms_content)
        perms_text.setStyleSheet(f"""
            QTextEdit {{
                font-family: Consolas, monospace;
                font-size: {STYLE.FONT_SM}px;
                border: 1px solid {STYLE.BORDER};
                border-radius: {STYLE.RADIUS_SM}px;
                background: {STYLE.BG_PAGE};
            }}
        """)
        perms_layout.addWidget(perms_text)
        tabs.addTab(perms_tab, "权限")

        main_layout.addWidget(tabs, 1)

        # Close button
        btn_row = QHBoxLayout()
        btn_row.addStretch()
        close_btn = QPushButton("关闭")
        close_btn.setStyleSheet(STYLE.BTN_SECONDARY_STYLE)
        close_btn.clicked.connect(self.close)
        btn_row.addWidget(close_btn)
        main_layout.addLayout(btn_row)
