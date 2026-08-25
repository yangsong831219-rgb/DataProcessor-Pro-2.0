"""技能插件中心 — 产物标签页 (Batch UX-1).

展示运行产物列表，提供定位、另存为和删除操作。
"""

from __future__ import annotations

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel,
    QPushButton, QTreeWidget, QTreeWidgetItem,
    QHeaderView, QFrame,
)
from ui.skill_center.style import STYLE


class ArtifactPanel(QWidget):
    """产物标签 — 文件列表与操作 (Batch 3.3.2: bridge multi-select checkboxes)."""

    locate_requested = pyqtSignal()
    export_requested = pyqtSignal()
    delete_requested = pyqtSignal()
    bridge_check_changed = pyqtSignal()  # Batch 3.3.2: emitted when any checkbox toggles
    send_to_report_requested = pyqtSignal()  # Batch 3.3.2

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._build_ui()

    def _build_ui(self) -> None:
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(STYLE.SPACE_MD)

        # ── Status label ──
        self.count_label = QLabel("暂无产物")
        self.count_label.setStyleSheet(
            f"color: {STYLE.TEXT_SECONDARY}; font-size: {STYLE.FONT_SM}px;"
        )
        main_layout.addWidget(self.count_label)

        # ── Artifact list (tree widget) with bridge checkboxes ──
        self.artifact_list = QTreeWidget()
        self.artifact_list.setColumnCount(4)
        self.artifact_list.setHeaderLabels(["选择", "名称", "类型", "大小"])
        self.artifact_list.setRootIsDecorated(False)
        self.artifact_list.setIndentation(0)
        header = self.artifact_list.header()
        if header is not None:
            header.setSectionResizeMode(0, QHeaderView.ResizeMode.Fixed)
            header.resizeSection(0, 50)  # checkbox column
            header.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
            header.setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
            header.setSectionResizeMode(3, QHeaderView.ResizeMode.ResizeToContents)
        self.artifact_list.setSelectionMode(
            QTreeWidget.SelectionMode.SingleSelection
        )
        self.artifact_list.setAlternatingRowColors(True)
        self.artifact_list.setStyleSheet(f"""
            QTreeWidget {{
                border: 1px solid {STYLE.BORDER};
                border-radius: {STYLE.RADIUS_SM}px;
                background: {STYLE.BG_CARD};
                font-size: {STYLE.FONT_SM}px;
            }}
            QTreeWidget::item {{
                padding: 4px 0;
            }}
            QHeaderView::section {{
                background: {STYLE.BG_PAGE};
                padding: 6px 8px;
                border: none;
                border-bottom: 1px solid {STYLE.BORDER};
                font-size: {STYLE.FONT_SM}px;
            }}
            QTreeWidget::indicator {{
                width: 16px;
                height: 16px;
            }}
            QTreeWidget::indicator:unchecked {{
                background-color: #ffffff;
                border: 2px solid #7f8c9a;
                border-radius: 3px;
            }}
            QTreeWidget::indicator:checked {{
                background-color: #722ed1;
                border: 2px solid #531dab;
                border-radius: 3px;
            }}
        """)
        self.artifact_list.itemSelectionChanged.connect(
            self._on_selection_changed
        )
        self.artifact_list.setVisible(False)  # Hidden by default (empty state)
        main_layout.addWidget(self.artifact_list, 1)

        # ── Action buttons (hidden when empty) ──
        self._btn_container = QWidget()
        self._btn_container.setVisible(False)
        btn_row = QHBoxLayout(self._btn_container)
        btn_row.setContentsMargins(0, 0, 0, 0)
        btn_row.setSpacing(STYLE.SPACE_SM)

        self.locate_btn = QPushButton("定位")
        self.locate_btn.setStyleSheet(STYLE.BTN_SECONDARY_STYLE)
        self.locate_btn.setToolTip("在文件管理器中定位选中文件")
        self.locate_btn.setEnabled(False)
        self.locate_btn.clicked.connect(self.locate_requested.emit)
        btn_row.addWidget(self.locate_btn)

        self.export_btn = QPushButton("另存为...")
        self.export_btn.setStyleSheet(STYLE.BTN_SECONDARY_STYLE)
        self.export_btn.setToolTip("将选中文件导出到指定位置")
        self.export_btn.setEnabled(False)
        self.export_btn.clicked.connect(self.export_requested.emit)
        btn_row.addWidget(self.export_btn)

        self.delete_btn = QPushButton("删除本次运行全部文件")
        self.delete_btn.setStyleSheet(STYLE.BTN_DANGER_STYLE)
        self.delete_btn.setToolTip("删除本次运行生成的全部文件")
        self.delete_btn.setEnabled(False)
        self.delete_btn.clicked.connect(self.delete_requested.emit)
        btn_row.addWidget(self.delete_btn)

        btn_row.addStretch()

        # ── Bridge selection (Batch 3.3.2) ──
        self.bridge_count_label = QLabel("已选: 0")
        self.bridge_count_label.setStyleSheet(
            f"color: {STYLE.TEXT_SECONDARY}; font-size: {STYLE.FONT_SM}px;"
        )
        btn_row.addWidget(self.bridge_count_label)

        self.send_to_report_btn = QPushButton("📤 发送到报告")
        self.send_to_report_btn.setStyleSheet(
            "QPushButton { background: #722ed1; color: white; border: none; "
            "border-radius: 4px; padding: 8px 16px; font-size: 12px; font-weight: bold; }"
            "QPushButton:hover { background: #9254de; }"
            "QPushButton:disabled { background: #d9d9d9; color: #999; }"
        )
        self.send_to_report_btn.setToolTip("将勾选的产物作为报告素材发送到报告工作台")
        self.send_to_report_btn.setEnabled(False)
        self.send_to_report_btn.clicked.connect(self.send_to_report_requested.emit)
        btn_row.addWidget(self.send_to_report_btn)

        main_layout.addWidget(self._btn_container)

        # ── Status label ──
        self.status_label = QLabel("")
        self.status_label.setStyleSheet(
            f"color: {STYLE.TEXT_SECONDARY}; font-size: {STYLE.FONT_SM}px;"
        )
        main_layout.addWidget(self.status_label)

        # ── Empty state card (shown when no artifacts) ──
        self._empty_card = QFrame()
        self._empty_card.setStyleSheet(f"""
            QFrame {{
                {STYLE.CARD_STYLE}
                padding: 0;
            }}
        """)
        self._empty_card.setMaximumHeight(200)
        empty_card_layout = QVBoxLayout(self._empty_card)
        empty_card_layout.setContentsMargins(
            STYLE.SPACE_LG, STYLE.SPACE_LG, STYLE.SPACE_LG, STYLE.SPACE_LG,
        )
        empty_card_layout.setSpacing(STYLE.SPACE_SM)

        empty_title = QLabel("本次运行尚未生成文件")
        empty_title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        empty_title.setStyleSheet(
            f"color: {STYLE.TEXT_PRIMARY}; font-size: {STYLE.FONT_MD}px;"
            f" font-weight: 500; border: none;"
        )
        empty_card_layout.addWidget(empty_title)

        empty_hint = QLabel("运行支持文件输出的技能后，文件会显示在这里。")
        empty_hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
        empty_hint.setStyleSheet(
            f"color: {STYLE.TEXT_MUTED}; font-size: {STYLE.FONT_SM}px;"
            f" border: none;"
        )
        empty_card_layout.addWidget(empty_hint)

        empty_card_layout.addStretch()
        self._empty_card.setVisible(True)
        main_layout.addWidget(self._empty_card)

        # Keep backward-compat alias
        self.empty_label = QLabel("本次运行尚未生成文件")
        self.empty_label.setVisible(False)

    def _on_selection_changed(self) -> None:
        """Forward selection state to owner via signals (handled by main widget)."""
        pass

    # ── Public API ──

    def clear_artifacts(self) -> None:
        """Clear the artifact list and show empty state."""
        self.artifact_list.clear()
        self.count_label.setText("暂无产物")
        self._show_empty_state()
        self._refresh_buttons()

    def show_no_artifacts_message(self) -> None:
        """Show 'no files generated' state."""
        self.artifact_list.clear()
        self.count_label.setText("本次运行尚未生成文件")
        self._show_empty_state()
        self._refresh_buttons()

    def populate_artifacts(self, artifacts: tuple) -> None:
        """Populate the tree widget from RuntimeArtifact objects."""
        self.artifact_list.clear()
        if not artifacts:
            self.show_no_artifacts_message()
            return

        self.count_label.setText(f"运行产物 ({len(artifacts)} 个)")
        for art in artifacts:
            display_name = (
                getattr(art, 'display_name', '')
                or getattr(art, 'relative_path', '')
            )
            kind = getattr(art, 'kind', '')
            media_type = getattr(art, 'media_type', '')
            type_str = kind or media_type or "—"
            size_bytes = getattr(art, 'size_bytes', 0)
            if size_bytes < 1024:
                size_str = f"{size_bytes} B"
            elif size_bytes < 1024 * 1024:
                size_str = f"{size_bytes / 1024:.1f} KB"
            else:
                size_str = f"{size_bytes / (1024 * 1024):.1f} MB"

            item = QTreeWidgetItem()
            # Column 0: checkbox (Batch 3.3.2)
            item.setCheckState(0, Qt.CheckState.Unchecked)
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setText(1, display_name)
            item.setText(2, type_str)
            item.setText(3, size_str)
            item.setData(1, Qt.ItemDataRole.UserRole, {
                "artifact_id": getattr(art, 'artifact_id', ''),
                "skill_id": getattr(art, 'skill_id', ''),
                "task_id": getattr(art, 'task_id', ''),
                "media_type": getattr(art, 'media_type', ''),
                "display_name": display_name,
                "size_bytes": size_bytes,
            })
            # Wire checkbox changes to signal (Batch 3.3.2)
            self.artifact_list.addTopLevelItem(item)

        self._hide_empty_state()
        self._refresh_buttons()
        # Connect itemChanged for checkbox detection (one-shot after populate)
        try:
            self.artifact_list.itemChanged.disconnect()
        except (TypeError, RuntimeError):
            pass
        self.artifact_list.itemChanged.connect(self._on_item_check_changed)

    def _show_empty_state(self) -> None:
        self.artifact_list.setVisible(False)
        self._btn_container.setVisible(False)
        self._empty_card.setVisible(True)

    def _hide_empty_state(self) -> None:
        self.artifact_list.setVisible(True)
        self._btn_container.setVisible(True)
        self._empty_card.setVisible(False)

    def get_selected_info(self) -> dict | None:
        """Get the owner info dict for the currently selected artifact."""
        selected = self.artifact_list.selectedItems()
        if not selected:
            return None
        data = selected[0].data(1, Qt.ItemDataRole.UserRole)
        if not isinstance(data, dict):
            return None
        return data

    # ── Bridge multi-select (Batch 3.3.2) ──

    def _on_item_check_changed(self, item: QTreeWidgetItem, column: int) -> None:
        """Handle checkbox toggle — emit bridge_check_changed."""
        if column == 0:
            self.bridge_check_changed.emit()

    def get_checked_artifacts(self) -> list[dict]:
        """Return list of info dicts for checked artifacts (visible row order)."""
        result: list[dict] = []
        for i in range(self.artifact_list.topLevelItemCount()):
            item = self.artifact_list.topLevelItem(i)
            if item is None:
                continue
            if item.checkState(0) == Qt.CheckState.Checked:
                data = item.data(1, Qt.ItemDataRole.UserRole)
                if isinstance(data, dict):
                    result.append(data)
        return result

    def get_checked_count(self) -> int:
        """Return the number of checked artifacts."""
        count = 0
        for i in range(self.artifact_list.topLevelItemCount()):
            item = self.artifact_list.topLevelItem(i)
            if item is not None and item.checkState(0) == Qt.CheckState.Checked:
                count += 1
        return count

    def clear_all_checkboxes(self) -> None:
        """Uncheck all artifact checkboxes."""
        try:
            self.artifact_list.itemChanged.disconnect()
        except (TypeError, RuntimeError):
            pass
        for i in range(self.artifact_list.topLevelItemCount()):
            item = self.artifact_list.topLevelItem(i)
            if item is not None:
                item.setCheckState(0, Qt.CheckState.Unchecked)
        self.artifact_list.itemChanged.connect(self._on_item_check_changed)
        self.bridge_check_changed.emit()

    def update_bridge_selection_ui(self, count: int, max_count: int = 12) -> None:
        """Update bridge selection count label and send button state (Batch 3.3.2)."""
        self.bridge_count_label.setText(f"已选: {count}")
        has_selection = 1 <= count <= max_count
        self.send_to_report_btn.setEnabled(has_selection)
        if count >= 13:
            self.bridge_count_label.setToolTip(
                f"最多选择 {max_count} 个素材，当前已选 {count} 个"
            )

    def set_buttons_enabled(self, locate: bool, export_: bool, delete: bool) -> None:
        """Set enable state for all three artifact buttons."""
        self.locate_btn.setEnabled(locate)
        self.export_btn.setEnabled(export_)
        self.delete_btn.setEnabled(delete)

    def set_status(self, text: str, is_error: bool = False) -> None:
        self.status_label.setText(text)
        if is_error:
            self.status_label.setStyleSheet(
                f"color: {STYLE.DANGER}; font-size: {STYLE.FONT_SM}px;"
            )
        else:
            self.status_label.setStyleSheet(
                f"color: {STYLE.TEXT_SECONDARY}; font-size: {STYLE.FONT_SM}px;"
            )

    def _refresh_buttons(self) -> None:
        """Called after list changes — actual logic is in main widget."""
        pass

    @property
    def has_items(self) -> bool:
        return self.artifact_list.topLevelItemCount() > 0
