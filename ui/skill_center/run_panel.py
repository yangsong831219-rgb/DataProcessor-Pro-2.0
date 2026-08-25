"""技能插件中心 — 运行标签页 (Batch UX-1).

参数输入、运行控制按钮和运行结果展示。
"""

from __future__ import annotations

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel,
    QPushButton, QLineEdit, QTextEdit, QFrame,
    QScrollArea,
)
from ui.skill_center.style import STYLE


class CollapsiblePanel(QFrame):
    """A collapsible panel with a toggle header."""

    def __init__(self, title: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._expanded = False
        self._title = title
        self._build_ui()

    def _build_ui(self) -> None:
        self._main_layout = QVBoxLayout(self)
        self._main_layout.setContentsMargins(0, 0, 0, 0)
        self._main_layout.setSpacing(0)

        # Toggle button
        self._toggle_btn = QPushButton(f"▸ {self._title}")
        self._toggle_btn.setStyleSheet(f"""
            QPushButton {{
                color: {STYLE.TEXT_SECONDARY};
                border: none;
                padding: {STYLE.SPACE_SM}px 0;
                text-align: left;
                font-size: {STYLE.FONT_SM}px;
                background: transparent;
            }}
            QPushButton:hover {{
                color: {STYLE.PRIMARY};
            }}
        """)
        self._toggle_btn.clicked.connect(self._toggle)
        self._main_layout.addWidget(self._toggle_btn)

        # Content area
        self._content = QWidget()
        self._content.setVisible(False)
        self._content_layout = QVBoxLayout(self._content)
        self._content_layout.setContentsMargins(0, 0, 0, 0)
        self._main_layout.addWidget(self._content)

    def _toggle(self) -> None:
        self._expanded = not self._expanded
        self._content.setVisible(self._expanded)
        arrow = "▾" if self._expanded else "▸"
        self._toggle_btn.setText(f"{arrow} {self._title}")

    def content_layout(self) -> QVBoxLayout:
        return self._content_layout

    @property
    def expanded(self) -> bool:
        return self._expanded


class RunPanel(QWidget):
    """运行标签 — 参数表单、运行控制和结果."""

    run_requested = pyqtSignal()
    stop_requested = pyqtSignal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
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

        # ── Parameters card ──
        params_card = QFrame()
        params_card.setStyleSheet(STYLE.CARD_STYLE)
        params_card_layout = QVBoxLayout(params_card)
        params_card_layout.setContentsMargins(
            STYLE.SPACE_LG, STYLE.SPACE_MD, STYLE.SPACE_LG, STYLE.SPACE_MD,
        )

        params_title = QLabel("参数")
        params_title.setStyleSheet(
            f"color: {STYLE.TEXT_PRIMARY}; font-size: {STYLE.FONT_SM}px;"
            f" font-weight: 600; border: none;"
        )
        params_card_layout.addWidget(params_title)

        params_desc = QLabel("输入 JSON 对象格式的参数，留空表示 {}")
        params_desc.setStyleSheet(
            f"color: {STYLE.TEXT_MUTED}; font-size: {STYLE.FONT_XS}px;"
        )
        params_card_layout.addWidget(params_desc)

        self.params_input = QLineEdit()
        self.params_input.setPlaceholderText('{"key": "value"}')
        self.params_input.setStyleSheet(f"""
            QLineEdit {{
                border: 1px solid {STYLE.BORDER};
                border-radius: {STYLE.RADIUS_SM}px;
                padding: 8px 10px;
                font-size: {STYLE.FONT_SM}px;
                background: {STYLE.BG_PAGE};
            }}
            QLineEdit:focus {{
                border-color: {STYLE.PRIMARY};
            }}
        """)
        params_card_layout.addWidget(self.params_input)

        content_layout.addWidget(params_card)

        # ── Advanced parameters (collapsible) ──
        advanced = CollapsiblePanel("高级参数")
        advanced.setStyleSheet(STYLE.CARD_STYLE)
        advanced_layout = advanced.content_layout()

        json_label = QLabel("原始 JSON 编辑器")
        json_label.setStyleSheet(
            f"color: {STYLE.TEXT_SECONDARY}; font-size: {STYLE.FONT_XS}px;"
        )
        advanced_layout.addWidget(json_label)

        self.json_editor = QTextEdit()
        self.json_editor.setPlaceholderText('{\n  "key": "value"\n}')
        self.json_editor.setMaximumHeight(150)
        self.json_editor.setStyleSheet(f"""
            QTextEdit {{
                border: 1px solid {STYLE.BORDER};
                border-radius: {STYLE.RADIUS_SM}px;
                font-family: Consolas, monospace;
                font-size: {STYLE.FONT_SM}px;
                background: {STYLE.BG_PAGE};
            }}
        """)
        advanced_layout.addWidget(self.json_editor)

        sync_btn = QPushButton("应用 JSON 到参数输入框")
        sync_btn.setStyleSheet(f"""
            QPushButton {{
                color: {STYLE.PRIMARY};
                border: none;
                padding: 4px 0;
                font-size: {STYLE.FONT_XS}px;
                background: transparent;
            }}
            QPushButton:hover {{
                text-decoration: underline;
            }}
        """)
        sync_btn.clicked.connect(self._on_sync_json)
        advanced_layout.addWidget(sync_btn)

        content_layout.addWidget(advanced)

        # ── Run controls card ──
        controls_card = QFrame()
        controls_card.setStyleSheet(STYLE.CARD_STYLE)
        controls_card_layout = QHBoxLayout(controls_card)
        controls_card_layout.setContentsMargins(
            STYLE.SPACE_LG, STYLE.SPACE_MD, STYLE.SPACE_LG, STYLE.SPACE_MD,
        )

        self.run_btn = QPushButton("运行")
        self.run_btn.setStyleSheet(STYLE.BTN_PRIMARY_STYLE)
        self.run_btn.setMinimumHeight(STYLE.BTN_HEIGHT_LG)
        self.run_btn.setEnabled(False)
        self.run_btn.setToolTip("请先在左侧列表中选择一个包含 run 入口的技能")
        self.run_btn.clicked.connect(self.run_requested.emit)
        controls_card_layout.addWidget(self.run_btn)

        self.stop_btn = QPushButton("■ 停止")
        self.stop_btn.setStyleSheet(f"""
            QPushButton {{
                background-color: {STYLE.DANGER};
                color: white;
                border: none;
                border-radius: {STYLE.RADIUS_SM}px;
                padding: 8px 16px;
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
        self.stop_btn.setEnabled(False)
        self.stop_btn.setVisible(False)
        self.stop_btn.clicked.connect(self.stop_requested.emit)
        controls_card_layout.addWidget(self.stop_btn)

        controls_card_layout.addStretch()
        content_layout.addWidget(controls_card)

        # ── Results card ──
        results_card = QFrame()
        results_card.setStyleSheet(STYLE.CARD_STYLE)
        results_card_layout = QVBoxLayout(results_card)
        results_card_layout.setContentsMargins(
            STYLE.SPACE_LG, STYLE.SPACE_MD, STYLE.SPACE_LG, STYLE.SPACE_MD,
        )

        results_title = QLabel("运行结果")
        results_title.setStyleSheet(
            f"color: {STYLE.TEXT_PRIMARY}; font-size: {STYLE.FONT_SM}px;"
            f" font-weight: 600; border: none;"
        )
        results_card_layout.addWidget(results_title)

        self.result_display = QTextEdit()
        self.result_display.setReadOnly(True)
        self.result_display.setPlaceholderText("运行结果将在此显示...")
        self.result_display.setStyleSheet(f"""
            QTextEdit {{
                border: 1px solid {STYLE.BORDER};
                border-radius: {STYLE.RADIUS_SM}px;
                background: {STYLE.BG_PAGE};
                font-family: Consolas, monospace;
                font-size: {STYLE.FONT_SM}px;
                padding: 8px;
            }}
        """)
        self.result_display.setMinimumHeight(180)
        results_card_layout.addWidget(self.result_display, 1)

        content_layout.addWidget(results_card, 1)

        scroll.setWidget(content)
        main_layout.addWidget(scroll, 1)

    def _on_sync_json(self) -> None:
        """Copy JSON editor content to params input."""
        text = self.json_editor.toPlainText().strip()
        if text:
            # Compress to single line for the QLineEdit
            import json
            try:
                parsed = json.loads(text)
                self.params_input.setText(json.dumps(parsed, ensure_ascii=False))
            except json.JSONDecodeError:
                self.params_input.setText(text)

    # ── Public API ──

    def set_run_enabled(self, enabled: bool) -> None:
        self.run_btn.setEnabled(enabled)

    def set_running_state(self, running: bool) -> None:
        """Toggle between run/stop UI."""
        if running:
            self.run_btn.setVisible(False)
            self.stop_btn.setVisible(True)
            self.stop_btn.setEnabled(True)
        else:
            self.run_btn.setVisible(True)
            self.stop_btn.setVisible(False)

    def set_stop_enabled(self, enabled: bool) -> None:
        self.stop_btn.setEnabled(enabled)

    def get_params_text(self) -> str:
        return self.params_input.text().strip()

    def set_result_html(self, html: str) -> None:
        self.result_display.setHtml(html)
