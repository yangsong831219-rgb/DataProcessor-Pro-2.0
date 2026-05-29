"""项目资料管理页面 — 项目列表、文件树、项目管理."""

from __future__ import annotations

import os

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QPushButton, QLabel,
    QGroupBox, QListWidget, QTreeWidget, QTreeWidgetItem,
    QSplitter, QFormLayout,
)
from PyQt6.QtCore import Qt, pyqtSignal


class ProjectManagerWidget(QWidget):
    """项目资料管理页面 — 跨报告模块共享的项目内容管理."""

    project_selected = pyqtSignal(str)             # project name
    new_project_requested = pyqtSignal()
    delete_project_requested = pyqtSignal(str)     # project name
    open_project_folder_requested = pyqtSignal()

    add_folder_requested = pyqtSignal()
    add_file_requested = pyqtSignal()
    delete_item_requested = pyqtSignal()
    open_item_requested = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._build_ui()

    def _build_ui(self):
        self.setStyleSheet("background-color: #f0f2f5;")
        main_layout = QHBoxLayout(self)
        main_layout.setContentsMargins(20, 20, 20, 20)
        main_layout.setSpacing(14)

        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.setHandleWidth(8)
        splitter.setStyleSheet("""
            QSplitter::handle {
                background: linear-gradient(to bottom, #e0e0e0, #c0c0c0);
            }
            QSplitter::handle:hover {
                background: linear-gradient(to bottom, #1890ff, #40a9ff);
            }
        """)

        # ═══════════════════════════════════════════
        # Left panel — project list
        # ═══════════════════════════════════════════
        left_panel = QWidget()
        left_panel.setMinimumWidth(280)
        left_layout = QVBoxLayout(left_panel)
        left_layout.setContentsMargins(0, 0, 0, 0)
        left_layout.setSpacing(12)

        project_list_group = QGroupBox("项目列表")
        project_list_group.setStyleSheet("""
            QGroupBox {
                border: 1px solid #e0e0e0;
                border-radius: 8px;
                font-weight: bold;
                color: #722ed1;
                padding: 10px;
                padding-top: 22px;
                background: white;
            }
            QGroupBox::title {
                subcontrol-origin: margin;
                left: 12px;
                padding: 0 6px;
            }
        """)
        project_list_inner = QVBoxLayout(project_list_group)
        project_list_inner.setSpacing(10)

        self.project_list_widget = QListWidget()
        self.project_list_widget.setStyleSheet("""
            QListWidget {
                border: 1px solid #ebebeb;
                border-radius: 4px;
                background: #fafafa;
            }
            QListWidget::item {
                padding: 12px;
                border-bottom: 1px solid #f5f5f5;
            }
            QListWidget::item:hover {
                background: #e6f4ff;
            }
            QListWidget::item:selected {
                background: #722ed1;
                color: white;
            }
        """)
        self.project_list_widget.itemClicked.connect(
            lambda item: self.project_selected.emit(item.text())
        )
        project_list_inner.addWidget(self.project_list_widget)

        # Project action buttons
        project_btn_layout = QHBoxLayout()
        project_btn_layout.setSpacing(8)

        new_project_btn = QPushButton("新建项目")
        new_project_btn.setStyleSheet("""
            QPushButton {
                background-color: #52c41a;
                color: white;
                border: none;
                border-radius: 4px;
                padding: 10px 12px;
                font-weight: bold;
            }
            QPushButton:hover {
                background-color: #73d13d;
            }
        """)
        new_project_btn.clicked.connect(self.new_project_requested.emit)
        project_btn_layout.addWidget(new_project_btn)

        delete_project_btn = QPushButton("删除项目")
        delete_project_btn.setStyleSheet("""
            QPushButton {
                background-color: #ff4d4f;
                color: white;
                border: none;
                border-radius: 4px;
                padding: 10px 12px;
                font-weight: bold;
            }
            QPushButton:hover {
                background-color: #ff7875;
            }
        """)
        delete_project_btn.clicked.connect(self._on_delete_project)
        project_btn_layout.addWidget(delete_project_btn)

        open_project_btn = QPushButton("打开项目")
        open_project_btn.setStyleSheet("""
            QPushButton {
                background-color: #1890ff;
                color: white;
                border: none;
                border-radius: 4px;
                padding: 10px 12px;
                font-weight: bold;
            }
            QPushButton:hover {
                background-color: #40a9ff;
            }
        """)
        open_project_btn.clicked.connect(self.open_project_folder_requested.emit)
        project_btn_layout.addWidget(open_project_btn)

        project_list_inner.addLayout(project_btn_layout)
        left_layout.addWidget(project_list_group)

        # Statistics
        stats_group = QGroupBox("项目统计")
        stats_group.setStyleSheet("""
            QGroupBox {
                border: 1px solid #e0e0e0;
                border-radius: 8px;
                font-weight: bold;
                color: #1890ff;
                padding: 10px;
                padding-top: 22px;
                background: white;
            }
            QGroupBox::title {
                subcontrol-origin: margin;
                left: 12px;
                padding: 0 6px;
            }
        """)
        stats_layout = QFormLayout(stats_group)
        stats_layout.setSpacing(8)

        self.project_stats_label = QLabel("项目数: 0")
        stats_layout.addRow("项目数:", self.project_stats_label)

        self.project_folders_label = QLabel("子文件夹: 0")
        stats_layout.addRow("子文件夹:", self.project_folders_label)

        self.project_files_count_label = QLabel("文件数: 0")
        stats_layout.addRow("文件数:", self.project_files_count_label)

        left_layout.addWidget(stats_group)

        # ═══════════════════════════════════════════
        # Right panel — project content
        # ═══════════════════════════════════════════
        right_panel = QWidget()
        right_layout = QVBoxLayout(right_panel)
        right_layout.setContentsMargins(0, 0, 0, 0)
        right_layout.setSpacing(14)

        content_group = QGroupBox("项目内容")
        content_group.setStyleSheet("""
            QGroupBox {
                border: 1px solid #e0e0e0;
                border-radius: 8px;
                font-weight: bold;
                color: #1890ff;
                padding: 10px;
                padding-top: 22px;
                background: white;
            }
            QGroupBox::title {
                subcontrol-origin: margin;
                left: 12px;
                padding: 0 6px;
            }
        """)
        content_inner = QVBoxLayout(content_group)
        content_inner.setSpacing(10)

        self.current_project_label = QLabel("请选择一个项目")
        self.current_project_label.setStyleSheet("""
            color: #333;
            font-size: 15px;
            font-weight: bold;
            padding: 8px;
            background: #f5f5f5;
            border-radius: 4px;
        """)
        content_inner.addWidget(self.current_project_label)

        self.project_tree_widget = QTreeWidget()
        self.project_tree_widget.setHeaderLabels(["名称", "类型", "大小"])
        self.project_tree_widget.setStyleSheet("""
            QTreeWidget {
                border: 1px solid #ebebeb;
                border-radius: 4px;
                background: white;
            }
            QTreeWidget::item {
                padding: 8px;
            }
            QTreeWidget::item:hover {
                background: #e6f4ff;
            }
            QTreeWidget::item:selected {
                background: #1890ff;
                color: white;
            }
        """)
        self.project_tree_widget.setColumnWidth(0, 200)
        self.project_tree_widget.setColumnWidth(1, 80)
        content_inner.addWidget(self.project_tree_widget, stretch=1)

        # File action buttons
        file_btn_layout = QHBoxLayout()
        file_btn_layout.setSpacing(8)

        add_folder_btn = QPushButton("新建文件夹")
        add_folder_btn.setStyleSheet("""
            QPushButton {
                background-color: #52c41a;
                color: white;
                border: none;
                border-radius: 4px;
                padding: 10px 14px;
                font-weight: bold;
            }
            QPushButton:hover {
                background-color: #73d13d;
            }
        """)
        add_folder_btn.clicked.connect(self.add_folder_requested.emit)
        file_btn_layout.addWidget(add_folder_btn)

        add_file_btn = QPushButton("添加文件")
        add_file_btn.setStyleSheet("""
            QPushButton {
                background-color: #1890ff;
                color: white;
                border: none;
                border-radius: 4px;
                padding: 10px 14px;
                font-weight: bold;
            }
            QPushButton:hover {
                background-color: #40a9ff;
            }
        """)
        add_file_btn.clicked.connect(self.add_file_requested.emit)
        file_btn_layout.addWidget(add_file_btn)

        delete_item_btn = QPushButton("删除选中")
        delete_item_btn.setStyleSheet("""
            QPushButton {
                background-color: #ff4d4f;
                color: white;
                border: none;
                border-radius: 4px;
                padding: 10px 14px;
                font-weight: bold;
            }
            QPushButton:hover {
                background-color: #ff7875;
            }
        """)
        delete_item_btn.clicked.connect(self.delete_item_requested.emit)
        file_btn_layout.addWidget(delete_item_btn)

        open_item_btn = QPushButton("打开文件")
        open_item_btn.setStyleSheet("""
            QPushButton {
                background-color: #faad14;
                color: white;
                border: none;
                border-radius: 4px;
                padding: 10px 14px;
                font-weight: bold;
            }
            QPushButton:hover {
                background-color: #ffc53d;
            }
        """)
        open_item_btn.clicked.connect(self.open_item_requested.emit)
        file_btn_layout.addWidget(open_item_btn)

        content_inner.addLayout(file_btn_layout)
        right_layout.addWidget(content_group)

        splitter.addWidget(left_panel)
        splitter.addWidget(right_panel)
        splitter.setStretchFactor(0, 280)
        splitter.setStretchFactor(1, 720)

        main_layout.addWidget(splitter)

    # ── Project list ──

    def set_project_list(self, items: list[str]):
        """设置项目列表."""
        self.project_list_widget.clear()
        self.project_list_widget.addItems(items)

    def set_stats(self, project_count: int, folder_count: int, file_count: int):
        """更新项目统计信息."""
        self.project_stats_label.setText(f"项目数: {project_count}")
        self.project_folders_label.setText(f"子文件夹: {folder_count}")
        self.project_files_count_label.setText(f"文件数: {file_count}")

    # ── Current project ──

    def set_current_project_label(self, name: str, exists: bool = True):
        """更新当前项目标签."""
        if exists:
            self.current_project_label.setText(f"当前项目: {name}")
            self.current_project_label.setStyleSheet("""
                color: #1890ff;
                font-size: 15px;
                font-weight: bold;
                padding: 8px;
                background: #e6f4ff;
                border-radius: 4px;
            """)
        else:
            self.current_project_label.setText("请选择一个项目")
            self.current_project_label.setStyleSheet("""
                color: #333;
                font-size: 15px;
                font-weight: bold;
                padding: 8px;
                background: #f5f5f5;
                border-radius: 4px;
            """)

    # ── Project tree ──

    def clear_tree(self):
        """清空项目文件树."""
        self.project_tree_widget.clear()

    def load_project_tree(self, root_path: str):
        """从文件系统递归加载项目目录树.

        此方法仅处理 UI 展示（树形控件填充），属于展示逻辑。
        业务层（主窗口）负责判断路径合法性。
        """
        self.project_tree_widget.clear()
        if not root_path or not os.path.exists(root_path):
            return
        try:
            root = QTreeWidgetItem(
                self.project_tree_widget,
                [os.path.basename(root_path), "文件夹", "-"],
            )
            self._load_folder_to_tree(root_path, root)
            self.project_tree_widget.expandAll()
        except Exception as e:
            print(f"加载项目树失败: {e}")

    def _load_folder_to_tree(self, folder_path: str, parent_item: QTreeWidgetItem):
        """递归加载文件夹内容到树."""
        try:
            for item_name in sorted(os.listdir(folder_path)):
                item_path = os.path.join(folder_path, item_name)
                if os.path.isdir(item_path):
                    folder_item = QTreeWidgetItem(parent_item, [item_name, "文件夹", "-"])
                    self._load_folder_to_tree(item_path, folder_item)
                else:
                    size = os.path.getsize(item_path)
                    size_str = self._format_size(size)
                    QTreeWidgetItem(parent_item, [item_name, "文件", size_str])
        except Exception as e:
            print(f"加载文件夹失败: {e}")

    @staticmethod
    def _format_size(size: float) -> str:
        for unit in ["B", "KB", "MB", "GB"]:
            if size < 1024.0:
                return f"{size:.1f} {unit}"
            size /= 1024.0
        return f"{size:.1f} TB"

    # ── Delete relay ──

    def _on_delete_project(self):
        selected = self.project_list_widget.currentItem()
        if selected:
            self.delete_project_requested.emit(selected.text())
