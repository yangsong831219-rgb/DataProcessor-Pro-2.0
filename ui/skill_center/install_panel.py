"""技能插件中心 — 安装与来源标签页 (Batch UX-1).

本地安装 (Card A) 和 GitHub 来源检查 (Card B)。
"""

from __future__ import annotations

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel,
    QPushButton, QLineEdit, QFrame, QScrollArea,
    QProgressBar, QGridLayout,
)
from ui.skill_center.style import STYLE


class InstallPanel(QWidget):
    """安装与来源标签 — 本地安装 + GitHub 来源."""

    install_from_dir_requested = pyqtSignal()
    install_from_zip_requested = pyqtSignal()
    inspect_source_requested = pyqtSignal()
    install_from_github_requested = pyqtSignal()
    cancel_install_requested = pyqtSignal()
    field_changed = pyqtSignal()  # GitHub fields modified → reset inspection

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._inspection_valid: bool = False
        self._build_ui()

    def _build_ui(self) -> None:
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(0, 0, 0, 0)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setStyleSheet("QScrollArea { background: transparent; }")

        content = QWidget()
        content_layout = QVBoxLayout(content)
        content_layout.setContentsMargins(0, 0, 0, 0)
        content_layout.setSpacing(STYLE.SPACE_MD)

        # ── Card A: Local install ──
        local_card = QFrame()
        local_card.setStyleSheet(STYLE.CARD_STYLE)
        local_card_layout = QVBoxLayout(local_card)
        local_card_layout.setContentsMargins(
            STYLE.SPACE_LG, STYLE.SPACE_MD, STYLE.SPACE_LG, STYLE.SPACE_MD,
        )
        local_card_layout.setSpacing(STYLE.SPACE_SM)

        local_title = QLabel("本地安装")
        local_title.setStyleSheet(
            f"color: {STYLE.TEXT_PRIMARY}; font-size: {STYLE.FONT_SM}px;"
            f" font-weight: 600; border: none;"
        )
        local_card_layout.addWidget(local_title)

        local_desc = QLabel("从本地目录或 ZIP 压缩包安装技能")
        local_desc.setStyleSheet(
            f"color: {STYLE.TEXT_MUTED}; font-size: {STYLE.FONT_XS}px;"
        )
        local_card_layout.addWidget(local_desc)

        local_btn_row = QHBoxLayout()
        dir_btn = QPushButton("从文件夹安装...")
        dir_btn.setStyleSheet(STYLE.BTN_SECONDARY_STYLE)
        dir_btn.clicked.connect(self.install_from_dir_requested.emit)
        local_btn_row.addWidget(dir_btn)

        zip_btn = QPushButton("从 ZIP 安装...")
        zip_btn.setStyleSheet(STYLE.BTN_SECONDARY_STYLE)
        zip_btn.clicked.connect(self.install_from_zip_requested.emit)
        local_btn_row.addWidget(zip_btn)

        local_btn_row.addStretch()
        local_card_layout.addLayout(local_btn_row)

        content_layout.addWidget(local_card)

        # ── Card B: GitHub source ──
        github_card = QFrame()
        github_card.setStyleSheet(STYLE.CARD_STYLE)
        github_card_layout = QVBoxLayout(github_card)
        github_card_layout.setContentsMargins(
            STYLE.SPACE_LG, STYLE.SPACE_MD, STYLE.SPACE_LG, STYLE.SPACE_MD,
        )
        github_card_layout.setSpacing(STYLE.SPACE_SM)

        github_title = QLabel("GitHub 来源")
        github_title.setStyleSheet(
            f"color: {STYLE.TEXT_PRIMARY}; font-size: {STYLE.FONT_SM}px;"
            f" font-weight: 600; border: none;"
        )
        github_card_layout.addWidget(github_title)

        # Archive URL (always visible)
        url_label = QLabel("归档 URL 或仓库地址")
        url_label.setStyleSheet(
            f"color: {STYLE.TEXT_SECONDARY}; font-size: {STYLE.FONT_XS}px;"
        )
        github_card_layout.addWidget(url_label)

        self.url_input = QLineEdit()
        self.url_input.setPlaceholderText(
            "https://github.com/owner/repo/archive/ref.zip"
        )
        self.url_input.setStyleSheet(f"""
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
        self.url_input.textChanged.connect(self._on_field_changed)
        github_card_layout.addWidget(self.url_input)

        # Check source button (always visible)
        self.inspect_btn = QPushButton("检查来源")
        self.inspect_btn.setStyleSheet(f"""
            QPushButton {{
                color: {STYLE.PRIMARY};
                border: 1px solid {STYLE.PRIMARY};
                border-radius: {STYLE.RADIUS_SM}px;
                padding: 6px 16px;
                background: transparent;
            }}
            QPushButton:hover {{
                background: #EFF6FF;
            }}
            QPushButton:disabled {{
                color: {STYLE.TEXT_MUTED};
                border-color: {STYLE.BORDER};
            }}
        """)
        self.inspect_btn.clicked.connect(self.inspect_source_requested.emit)
        github_card_layout.addWidget(self.inspect_btn)

        # ── Advanced settings toggle ──
        self.advanced_toggle = QPushButton("▸ 高级设置")
        self.advanced_toggle.setStyleSheet(f"""
            QPushButton {{
                color: {STYLE.TEXT_SECONDARY};
                border: none;
                padding: 4px 0;
                text-align: left;
                font-size: {STYLE.FONT_XS}px;
                background: transparent;
            }}
            QPushButton:hover {{
                color: {STYLE.PRIMARY};
            }}
        """)
        self.advanced_toggle.clicked.connect(self._toggle_advanced)
        github_card_layout.addWidget(self.advanced_toggle)

        # ── Advanced fields (hidden by default) ──
        self.advanced_widget = QWidget()
        self.advanced_widget.setVisible(False)
        advanced_layout = QGridLayout(self.advanced_widget)
        advanced_layout.setContentsMargins(0, 0, 0, 0)
        advanced_layout.setSpacing(STYLE.SPACE_SM)

        advanced_layout.addWidget(QLabel("仓库所有者:"), 0, 0)
        self.owner_input = QLineEdit()
        self.owner_input.textChanged.connect(self._on_field_changed)
        advanced_layout.addWidget(self.owner_input, 0, 1)

        advanced_layout.addWidget(QLabel("仓库名称:"), 1, 0)
        self.repo_input = QLineEdit()
        self.repo_input.textChanged.connect(self._on_field_changed)
        advanced_layout.addWidget(self.repo_input, 1, 1)

        advanced_layout.addWidget(QLabel("仓库内路径:"), 2, 0)
        self.path_input = QLineEdit()
        self.path_input.setText("skills/")
        self.path_input.textChanged.connect(self._on_field_changed)
        advanced_layout.addWidget(self.path_input, 2, 1)

        advanced_layout.addWidget(QLabel("分支:"), 3, 0)
        self.branch_input = QLineEdit()
        self.branch_input.setText("main")
        self.branch_input.textChanged.connect(self._on_field_changed)
        advanced_layout.addWidget(self.branch_input, 3, 1)

        advanced_layout.addWidget(QLabel("GitHub Token:"), 4, 0)
        self.token_input = QLineEdit()
        self.token_input.setEchoMode(QLineEdit.EchoMode.Password)
        self.token_input.setPlaceholderText("可选")
        self.token_input.textChanged.connect(self._on_field_changed)
        advanced_layout.addWidget(self.token_input, 4, 1)

        github_card_layout.addWidget(self.advanced_widget)

        # ── Install from checked GitHub source ──
        self.github_install_btn = QPushButton("从已检查 GitHub 来源安装")
        self.github_install_btn.setStyleSheet(f"""
            QPushButton {{
                color: {STYLE.SUCCESS};
                border: 1px solid {STYLE.SUCCESS};
                border-radius: {STYLE.RADIUS_SM}px;
                padding: 8px 16px;
                background: transparent;
                font-weight: 500;
            }}
            QPushButton:hover {{
                background: #F0FDF4;
            }}
            QPushButton:disabled {{
                color: {STYLE.TEXT_MUTED};
                border-color: {STYLE.BORDER};
            }}
        """)
        self.github_install_btn.setVisible(False)
        self.github_install_btn.clicked.connect(
            self.install_from_github_requested.emit
        )
        github_card_layout.addWidget(self.github_install_btn)

        content_layout.addWidget(github_card)

        # ── Progress section ──
        self.progress_bar = QProgressBar()
        self.progress_bar.setVisible(False)
        self.progress_bar.setStyleSheet(f"""
            QProgressBar {{
                border: 1px solid {STYLE.BORDER};
                border-radius: {STYLE.RADIUS_SM}px;
                background: {STYLE.BG_PAGE};
                text-align: center;
            }}
            QProgressBar::chunk {{
                background-color: {STYLE.PRIMARY};
                border-radius: 3px;
            }}
        """)
        content_layout.addWidget(self.progress_bar)

        self.progress_label = QLabel("")
        self.progress_label.setStyleSheet(
            f"color: {STYLE.TEXT_SECONDARY}; font-size: {STYLE.FONT_SM}px;"
        )
        self.progress_label.setVisible(False)
        content_layout.addWidget(self.progress_label)

        # ── Cancel button ──
        self.cancel_btn = QPushButton("取消安装")
        self.cancel_btn.setStyleSheet(STYLE.BTN_DANGER_STYLE)
        self.cancel_btn.setEnabled(False)
        self.cancel_btn.setVisible(False)
        self.cancel_btn.clicked.connect(self.cancel_install_requested.emit)
        content_layout.addWidget(self.cancel_btn)

        content_layout.addStretch()

        scroll.setWidget(content)
        main_layout.addWidget(scroll, 1)

    def _toggle_advanced(self) -> None:
        expanded = self.advanced_widget.isHidden()
        self.advanced_widget.setVisible(expanded)
        arrow = "▾" if expanded else "▸"
        self.advanced_toggle.setText(f"{arrow} 高级设置")

    def _on_field_changed(self) -> None:
        """Any GitHub field change invalidates inspection."""
        if self._inspection_valid:
            self._inspection_valid = False
            self.github_install_btn.setVisible(False)
        self.field_changed.emit()

    # ── Public API ──

    @property
    def advanced_visible(self) -> bool:
        return not self.advanced_widget.isHidden()

    def set_inspection_valid(self, valid: bool) -> None:
        self._inspection_valid = valid
        self.github_install_btn.setVisible(valid)

    @property
    def inspection_valid(self) -> bool:
        return self._inspection_valid

    def get_github_fields(self) -> dict:
        """Get all GitHub-related field values."""
        return {
            "url": self.url_input.text().strip(),
            "owner": self.owner_input.text().strip(),
            "repo": self.repo_input.text().strip(),
            "path": self.path_input.text().strip(),
            "branch": self.branch_input.text().strip() or "main",
            "token": self.token_input.text().strip() or None,
        }

    def set_inspect_button_text(self, text: str) -> None:
        self.inspect_btn.setText(text)

    def set_inspect_button_enabled(self, enabled: bool) -> None:
        self.inspect_btn.setEnabled(enabled)

    def set_install_buttons_enabled(self, enabled: bool) -> None:
        """Enable/disable local install buttons and GitHub install."""
        # Local buttons are in the local card - find them
        pass  # Managed by main widget

    def show_progress(self, visible: bool) -> None:
        self.progress_bar.setVisible(visible)

    def update_progress(self, value: int, maximum: int) -> None:
        self.progress_bar.setMaximum(maximum)
        self.progress_bar.setValue(value)

    def set_progress_label(self, text: str) -> None:
        self.progress_label.setText(text)
        self.progress_label.setVisible(bool(text))

    def set_cancel_enabled(self, enabled: bool) -> None:
        self.cancel_btn.setEnabled(enabled)
        self.cancel_btn.setVisible(enabled)
