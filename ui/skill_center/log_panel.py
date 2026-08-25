"""技能插件中心 — 日志标签页 (Batch UX-1).

紧凑工具栏 + 全高日志区域。
"""

from __future__ import annotations

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel,
    QPushButton, QTextEdit, QCheckBox, QApplication,
)
from ui.skill_center.style import STYLE


class LogPanel(QWidget):
    """日志标签 — 操作日志查看器."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._auto_scroll = True
        self._build_ui()

    def _build_ui(self) -> None:
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(STYLE.SPACE_SM)

        # ── Toolbar ──
        toolbar = QHBoxLayout()
        toolbar.setSpacing(STYLE.SPACE_SM)

        copy_btn = QPushButton("复制日志")
        copy_btn.setStyleSheet(f"""
            QPushButton {{
                color: {STYLE.TEXT_SECONDARY};
                border: 1px solid {STYLE.BORDER};
                border-radius: {STYLE.RADIUS_SM}px;
                padding: 4px 12px;
                font-size: {STYLE.FONT_XS}px;
                background: {STYLE.BG_CARD};
            }}
            QPushButton:hover {{
                background: {STYLE.BG_PAGE};
            }}
        """)
        copy_btn.clicked.connect(self._on_copy)
        toolbar.addWidget(copy_btn)

        clear_btn = QPushButton("清空日志")
        clear_btn.setStyleSheet(f"""
            QPushButton {{
                color: {STYLE.TEXT_SECONDARY};
                border: 1px solid {STYLE.BORDER};
                border-radius: {STYLE.RADIUS_SM}px;
                padding: 4px 12px;
                font-size: {STYLE.FONT_XS}px;
                background: {STYLE.BG_CARD};
            }}
            QPushButton:hover {{
                background: {STYLE.BG_PAGE};
            }}
        """)
        clear_btn.clicked.connect(self._on_clear)
        toolbar.addWidget(clear_btn)

        self.auto_scroll_cb = QCheckBox("自动滚动")
        self.auto_scroll_cb.setChecked(True)
        self.auto_scroll_cb.setStyleSheet(
            f"color: {STYLE.TEXT_SECONDARY}; font-size: {STYLE.FONT_XS}px;"
        )
        self.auto_scroll_cb.toggled.connect(self._on_auto_scroll)
        toolbar.addWidget(self.auto_scroll_cb)

        toolbar.addStretch()
        main_layout.addLayout(toolbar)

        # ── Log area ──
        self.log_edit = QTextEdit()
        self.log_edit.setReadOnly(True)
        self.log_edit.setPlaceholderText("暂无日志")
        self.log_edit.setStyleSheet(f"""
            QTextEdit {{
                background-color: #1e1e1e;
                color: #d4d4d4;
                font-family: Consolas, monospace;
                font-size: {STYLE.FONT_SM}px;
                border: 1px solid {STYLE.BORDER};
                border-radius: {STYLE.RADIUS_SM}px;
                padding: 8px;
            }}
        """)
        main_layout.addWidget(self.log_edit, 1)

    def _on_copy(self) -> None:
        """Copy all log content to clipboard."""
        text = self.log_edit.toPlainText()
        if text:
            clipboard = QApplication.clipboard()
            if clipboard is not None:
                clipboard.setText(text)

    def _on_clear(self) -> None:
        """Clear the log."""
        self.log_edit.clear()

    def _on_auto_scroll(self, checked: bool) -> None:
        """Toggle auto-scroll."""
        self._auto_scroll = checked

    # ── Public API ──

    def append_info(self, msg: str) -> None:
        self._append('INFO', msg, 'blue')

    def append_success(self, msg: str) -> None:
        self._append('OK', msg, 'green')

    def append_warn(self, msg: str) -> None:
        self._append('WARN', msg, 'orange')

    def append_error(self, msg: str) -> None:
        self._append('ERROR', msg, 'red')

    def _append(self, level: str, msg: str, color: str) -> None:
        """Append a formatted log entry."""
        html = (
            f'<span style="color: {color};">[{level}]</span>'
            f' {msg}'
        )
        self.log_edit.append(html)
        if self._auto_scroll:
            scrollbar = self.log_edit.verticalScrollBar()
            if scrollbar is not None:
                scrollbar.setValue(scrollbar.maximum())

    @property
    def auto_scroll(self) -> bool:
        return self._auto_scroll
