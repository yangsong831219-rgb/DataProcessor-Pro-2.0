"""统一报告生成工作台 — Dumb Component，纯信号驱动.

仅负责布局和用户交互，不直接调用大模型或读写文件。
所有业务逻辑通过信号委托给 Controller（main.py）。

Layout:
    QSplitter (水平)
    ├── 左面板 (~30%): 报告类型、文件选取、操作按钮
    └── 右面板 (~70%): 大纲编辑器 (QTextEdit)

Signals:
    outline_requested(config):    用户点击"生成大纲"时发射
    full_report_requested(config, outline_text): 用户点击"生成完整报告"时发射
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QButtonGroup, QDialog, QFileDialog, QGroupBox, QHBoxLayout,
    QLabel, QListWidget, QListWidgetItem, QPushButton, QRadioButton,
    QSplitter, QTextBrowser, QTextEdit, QVBoxLayout, QWidget,
)


# ═══════════════════════════════════════════════
# 样式常量
# ═══════════════════════════════════════════════

CARD = """
    QGroupBox {
        border: 1px solid #e0e0e0; border-radius: 8px;
        font-weight: bold; padding: 14px; padding-top: 24px;
        background: white; margin-top: 6px;
    }
    QGroupBox::title {
        subcontrol-origin: margin; left: 12px; padding: 0 6px;
        color: #333;
    }
"""

BTN_PRIMARY = """
    QPushButton {
        background: #1890ff; color: white; border: none; border-radius: 4px;
        padding: 10px 20px; font-size: 13px; font-weight: bold;
    }
    QPushButton:hover { background: #40a9ff; }
    QPushButton:disabled { background: #d9d9d9; color: #999; }
"""

BTN_ACCENT = """
    QPushButton {
        background: #52c41a; color: white; border: none; border-radius: 4px;
        padding: 10px 20px; font-size: 13px; font-weight: bold;
    }
    QPushButton:hover { background: #73d13d; }
    QPushButton:disabled { background: #d9d9d9; color: #999; }
"""

BTN_SECONDARY = """
    QPushButton {
        background: white; color: #555;
        border: 1px solid #d9d9d9; border-radius: 4px;
        padding: 8px 16px; font-size: 12px;
    }
    QPushButton:hover { color: #1890ff; border-color: #1890ff; }
"""

HELP_TEXT = """# 报告生成工作台 — 使用说明

## 操作流程

### 第一步：配置
1. 选择**需求文件**（可选）— 描述报告覆盖内容的文档
2. 选择**模板文件**（可选）— .docx 或 .pptx 模板
3. 在**项目资料列表**中添加相关资料（可选）— 作为 AI 检索上下文
4. 切换报告类型 — [Word 深度报告] 或 [PPT 演示汇报]

### 第二步：生成大纲
点击 [📝 生成/预览报告大纲] → 右侧编辑器显示大纲 → 可手动编辑修改

### 第三步：确认与生成
确认大纲无误后点击 [🚀 基于大纲生成完整报告] → AI 逐章扩写

## Word vs PPT

| 特性 | Word | PPT |
|------|------|-----|
| 风格 | 长文本深度论述 | 极简 bullet 要点 |
| 每节要点 | 不限 | ≤4 条 |
| 图表 | 正文表格 | Speaker Notes |
| 场景 | 技术报告 | 领导汇报 |
"""


class ReportWorkbenchWidget(QWidget):
    """统一报告生成工作台 — 纯 UI 组件，零业务逻辑."""

    outline_requested = pyqtSignal(dict)          # 左侧配置快照
    full_report_requested = pyqtSignal(dict, str)  # 配置 + 右侧大纲文本
    load_diagnosis_requested = pyqtSignal()         # 用户点击"从已存诊断加载"

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._req_file_path: str = ''
        self._template_file_path: str = ''
        self._diagnosis_record: dict | None = None  # 从已存诊断 JSON 加载
        self._build_ui()

    # ── Public API ──

    def set_outline_text(self, text: str) -> None:
        """由控制器调用，将 AI 生成的大纲填入编辑器."""
        self.outline_editor.setPlainText(text)
        self.full_report_btn.setEnabled(True)

    def get_outline_text(self) -> str:
        return self.outline_editor.toPlainText().strip()

    def get_config(self) -> Dict[str, Any]:
        """收集左侧面板所有配置项，返回字典."""
        return {
            'report_type': 'ppt' if self.ppt_radio.isChecked() else 'word',
            'req_file': self._req_file_path,
            'template_file': self._template_file_path,
            'project_files': self._get_project_file_paths(),
            '_diagnosis_record': self._diagnosis_record,
        }

    def set_full_report_ready(self, ready: bool) -> None:
        """控制器在外围判断是否可以激活完整报告按钮."""
        self.full_report_btn.setEnabled(ready)

    def reset_ui(self) -> None:
        """清空表单和编辑器，恢复初始状态."""
        self.outline_editor.clear()
        self.full_report_btn.setEnabled(False)
        self._req_file_path = ''
        self._template_file_path = ''
        self._req_file_label.setText('需求文件: 未选择')
        self._req_file_label.setStyleSheet('color: #888; font-size: 12px;')
        self._tmpl_file_label.setText('模板文件: 未选择')
        self._tmpl_file_label.setStyleSheet('color: #888; font-size: 12px;')
        self._proj_file_list.clear()

    # ── UI 构建 ──

    def _build_ui(self) -> None:
        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.setHandleWidth(6)

        splitter.addWidget(self._build_left_panel())
        splitter.addWidget(self._build_right_panel())
        splitter.setStretchFactor(0, 30)
        splitter.setStretchFactor(1, 70)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(splitter)

    def _build_left_panel(self) -> QWidget:
        panel = QWidget()
        panel.setStyleSheet('background: #fafbfc;')
        layout = QVBoxLayout()
        layout.setContentsMargins(14, 14, 14, 14)
        layout.setSpacing(10)

        # ── 标题 ──
        header = QLabel('报告生成工作台')
        header.setStyleSheet('font-size: 16px; font-weight: bold; color: #333; padding: 2px 0;')
        layout.addWidget(header)

        # ── 报告类型 ──
        type_group = QGroupBox('报告类型')
        type_group.setStyleSheet(CARD)
        type_layout = QVBoxLayout()
        self.type_group = QButtonGroup(self)
        self.word_radio = QRadioButton('🔘 Word 深度报告')
        self.ppt_radio = QRadioButton('⚪ PPT 演示汇报')
        self.type_group.addButton(self.word_radio, 0)
        self.type_group.addButton(self.ppt_radio, 1)
        self.word_radio.setChecked(True)
        type_layout.addWidget(self.word_radio)
        type_layout.addWidget(self.ppt_radio)
        type_group.setLayout(type_layout)
        layout.addWidget(type_group)

        # ── 文件选取 ──
        file_group = QGroupBox('资料选取')
        file_group.setStyleSheet(CARD)
        file_layout = QVBoxLayout()
        file_layout.setSpacing(6)

        # 需求文件
        self._req_file_label = QLabel('需求文件: 未选择')
        self._req_file_label.setStyleSheet('color: #888; font-size: 12px;')
        self._req_file_label.setWordWrap(True)
        file_layout.addWidget(self._req_file_label)

        req_btn = QPushButton('选择需求文件...')
        req_btn.setStyleSheet(BTN_SECONDARY)
        req_btn.clicked.connect(self._on_select_req_file)
        file_layout.addWidget(req_btn)

        # 模板文件
        self._tmpl_file_label = QLabel('模板文件: 未选择')
        self._tmpl_file_label.setStyleSheet('color: #888; font-size: 12px;')
        self._tmpl_file_label.setWordWrap(True)
        file_layout.addWidget(self._tmpl_file_label)

        tmpl_btn = QPushButton('选择模板文件...')
        tmpl_btn.setStyleSheet(BTN_SECONDARY)
        tmpl_btn.clicked.connect(self._on_select_template_file)
        file_layout.addWidget(tmpl_btn)

        # 项目资料列表
        file_layout.addWidget(QLabel('项目关联资料 (多选):'))
        self._proj_file_list = QListWidget()
        self._proj_file_list.setSelectionMode(
            QListWidget.SelectionMode.MultiSelection
        )
        self._proj_file_list.setStyleSheet("""
            QListWidget {
                border: 1px solid #e0e0e0; border-radius: 4px;
                background: white; min-height: 80px;
            }
            QListWidget::item { padding: 4px 8px; }
            QListWidget::item:selected { background: #e6f7ff; color: #333; }
        """)
        file_layout.addWidget(self._proj_file_list)

        add_proj_btn = QPushButton('添加文件到资料列表')
        add_proj_btn.setStyleSheet(BTN_SECONDARY)
        add_proj_btn.clicked.connect(self._on_add_project_file)
        file_layout.addWidget(add_proj_btn)

        file_group.setLayout(file_layout)
        layout.addWidget(file_group)

        # ── 操作按钮 ──
        btn_group = QGroupBox('操作')
        btn_group.setStyleSheet(CARD)
        btn_layout = QVBoxLayout()
        btn_layout.setSpacing(8)

        self.outline_btn = QPushButton('📝 生成/预览报告大纲')
        self.outline_btn.setStyleSheet(BTN_PRIMARY)
        self.outline_btn.clicked.connect(self._on_outline_requested)
        btn_layout.addWidget(self.outline_btn)

        self.load_diag_btn = QPushButton('📋 从已存诊断加载')
        self.load_diag_btn.setStyleSheet(
            "QPushButton { background: #722ed1; color: white; border: none; border-radius: 4px; "
            "padding: 8px 16px; font-size: 12px; font-weight: bold; }"
            "QPushButton:hover { background: #9254de; }"
        )
        self.load_diag_btn.clicked.connect(self._on_load_diagnosis)
        btn_layout.addWidget(self.load_diag_btn)

        self.diag_loaded_label = QLabel('诊断数据: (未加载)')
        self.diag_loaded_label.setStyleSheet('color: #888; font-size: 11px; padding: 2px 0;')
        btn_layout.addWidget(self.diag_loaded_label)

        self.full_report_btn = QPushButton('🚀 基于大纲生成完整报告')
        self.full_report_btn.setStyleSheet(BTN_ACCENT)
        self.full_report_btn.setEnabled(False)
        self.full_report_btn.clicked.connect(self._on_full_report_requested)
        btn_layout.addWidget(self.full_report_btn)

        help_btn = QPushButton('📖 编写说明')
        help_btn.setStyleSheet(BTN_SECONDARY)
        help_btn.clicked.connect(self._show_help)
        btn_layout.addWidget(help_btn)

        btn_group.setLayout(btn_layout)
        layout.addWidget(btn_group)

        layout.addStretch()
        panel.setLayout(layout)
        return panel

    def _build_right_panel(self) -> QWidget:
        panel = QWidget()
        layout = QVBoxLayout()
        layout.setContentsMargins(14, 14, 14, 14)
        layout.setSpacing(8)

        header = QLabel('报告大纲 (可编辑)')
        header.setStyleSheet(
            'font-size: 15px; font-weight: bold; color: #333; padding: 2px 0;'
        )
        layout.addWidget(header)

        hint = QLabel(
            '点击「生成/预览报告大纲」后，AI 生成的大纲显示在此。'
            '你可直接编辑、增删章节、修改标题。'
        )
        hint.setStyleSheet('color: #888; font-size: 12px;')
        hint.setWordWrap(True)
        layout.addWidget(hint)

        self.outline_editor = QTextEdit()
        self.outline_editor.setPlaceholderText(
            '点击「📝 生成/预览报告大纲」生成大纲...\n'
            '生成后可直接在此编辑修改。'
        )
        self.outline_editor.setStyleSheet("""
            QTextEdit {
                border: 1px solid #e0e0e0; border-radius: 6px;
                padding: 12px; font-size: 13px; background: white;
                line-height: 1.7;
            }
        """)
        layout.addWidget(self.outline_editor, stretch=1)

        panel.setLayout(layout)
        return panel

    # ── 文件选择（纯 UI 操作，不读取文件内容） ──

    def _on_select_req_file(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, '选择需求文件', '',
            'Text Files (*.txt *.md);;All Files (*.*)',
        )
        if path:
            self._req_file_path = path
            self._req_file_label.setText(f'需求文件: {path}')
            self._req_file_label.setStyleSheet('color: #333; font-size: 12px;')

    def _on_select_template_file(self) -> None:
        filt = (
            'PowerPoint (*.pptx);;All Files (*.*)'
            if self.ppt_radio.isChecked()
            else 'Word (*.docx *.doc);;All Files (*.*)'
        )
        path, _ = QFileDialog.getOpenFileName(self, '选择模板文件', '', filt)
        if path:
            self._template_file_path = path
            self._tmpl_file_label.setText(f'模板: {path}')
            self._tmpl_file_label.setStyleSheet('color: #333; font-size: 12px;')

    def _on_add_project_file(self) -> None:
        paths, _ = QFileDialog.getOpenFileNames(
            self, '添加项目资料', '',
            'All Files (*.*);;CSV (*.csv);;Excel (*.xlsx);;Text (*.txt *.md)',
        )
        for path in paths:
            item = QListWidgetItem(path)
            item.setToolTip(path)
            self._proj_file_list.addItem(item)

    def _get_project_file_paths(self) -> List[str]:
        return [
            self._proj_file_list.item(i).text()
            for i in range(self._proj_file_list.count())
        ]

    # ── 信号发射 ──

    def set_diagnosis_record(self, rec: dict | None) -> None:
        """由控制器调用，设置或清空已加载的诊断记录。"""
        self._diagnosis_record = rec
        if rec:
            ts = rec.get('timestamp', '?')
            sensors = len(rec.get('diagnosis_json', {}).get('sensor_analysis', []))
            self.diag_loaded_label.setText(f'诊断数据: {ts} ({sensors} 传感器)')
            self.diag_loaded_label.setStyleSheet('color: #52c41a; font-weight: bold; font-size: 11px;')
        else:
            self.diag_loaded_label.setText('诊断数据: (未加载)')
            self.diag_loaded_label.setStyleSheet('color: #888; font-size: 11px;')

    def _on_load_diagnosis(self) -> None:
        self.load_diagnosis_requested.emit()

    def _on_outline_requested(self) -> None:
        self.outline_requested.emit(self.get_config())

    def _on_full_report_requested(self) -> None:
        outline = self.outline_editor.toPlainText().strip()
        if not outline:
            outline = '(空大纲 — 将使用默认模板)'
        self.full_report_requested.emit(self.get_config(), outline)

    # ── 帮助 ──

    def _show_help(self) -> None:
        dlg = QDialog(self)
        dlg.setWindowTitle('报告生成工作台 — 使用说明')
        dlg.setMinimumSize(650, 550)
        dlg_layout = QVBoxLayout()

        browser = QTextBrowser()
        browser.setMarkdown(HELP_TEXT)
        browser.setStyleSheet('font-size: 13px; padding: 10px;')
        dlg_layout.addWidget(browser)

        close_btn = QPushButton('关闭')
        close_btn.setStyleSheet(BTN_SECONDARY)
        close_btn.clicked.connect(dlg.close)
        dlg_layout.addWidget(close_btn)

        dlg.setLayout(dlg_layout)
        dlg.exec()
