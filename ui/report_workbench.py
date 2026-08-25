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

from pathlib import Path
from typing import Any, Dict, List, Optional

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QButtonGroup, QDialog, QFileDialog, QGroupBox, QHBoxLayout,
    QLabel, QListWidget, QListWidgetItem, QMessageBox, QPushButton,
    QRadioButton, QComboBox, QScrollArea, QSplitter, QTextBrowser,
    QTextEdit, QVBoxLayout, QWidget,
)

from dp_engine.report_provider import (
    BUILTIN_PROVIDER_ID,
    BUILTIN_PROVIDER_VERSION,
    PPT_MASTER_PROVIDER_ID,
    ReportProviderOption,
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
2. 点击**检测/标准化并载入模板**（可选）— 自动检测 .docx/.pptx，
   可用模板直接载入，样例模板自动标准化，不兼容模板明确拒绝
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
    cancel_requested = pyqtSignal()                 # 用户点击"取消生成"
    report_provider_filter_changed = pyqtSignal(str, str)
    report_inputs_changed = pyqtSignal()
    ppt_master_planning_requested = pyqtSignal(str, dict)

    # Batch 3.3.2: Bridge signals
    bridge_clear_requested = pyqtSignal()           # 用户点击"清除素材"
    bridge_cancel_prepare_requested = pyqtSignal()  # 用户点击"取消准备"

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._req_file_path: str = ''
        self._template_file_path: str = ''
        self._template_source_path: str = ''
        self._template_status: str = ''
        self._template_result_summary: str = ''
        self._diagnosis_record: dict | None = None  # 从已存诊断 JSON 加载
        self._ppt_master_next_action: str = ''
        self._ppt_master_plan_ready = False

        # Batch 3.3.2: Bridge state
        self._bridge_status: str = ""  # PREPARING, READY, FAILED, CANCELLED, SUCCEEDED
        self._bridge_request_id: str = ""
        self._bridge_generation: int = 0
        self._bridge_assets: tuple = ()  # tuple[ReportAssetSummary, ...]

        self._build_ui()
        self.set_report_provider_options((ReportProviderOption(
            provider_id=BUILTIN_PROVIDER_ID,
            provider_version=BUILTIN_PROVIDER_VERSION,
            display_name='内置标准生成器',
            is_builtin=True,
        ),))

    # ── Public API ──

    def set_outline_text(self, text: str) -> None:
        """由控制器调用，将 AI 生成的大纲填入编辑器."""
        self.outline_editor.setPlainText(text)
        # 仅在诊断已加载时解锁"生成完整报告"按钮
        if self._diagnosis_record is not None and not self._is_ppt_master_selected():
            self.full_report_btn.setEnabled(True)

    def get_outline_text(self) -> str:
        return self.outline_editor.toPlainText().strip()

    def get_config(self) -> Dict[str, Any]:
        """收集左侧面板所有配置项，返回字典."""
        provider = self._selected_report_provider()
        return {
            'report_type': 'ppt' if self.ppt_radio.isChecked() else 'word',
            'req_file': self._req_file_path,
            'template_file': self._template_file_path,
            'project_files': self._get_project_file_paths(),
            '_diagnosis_record': self._diagnosis_record,
            'report_provider': {
                'provider_id': provider['provider_id'],
                'provider_version': provider['provider_version'],
            },
        }

    def current_report_provider_filter(self) -> tuple[str, str]:
        """Return the output artifact type and normalized template mode."""
        artifact_type = 'pptx' if self.ppt_radio.isChecked() else 'docx'
        template_mode = 'normalized' if self._template_file_path else 'none'
        return artifact_type, template_mode

    def notify_report_template_changed(self) -> None:
        """Ask the Controller to refilter Providers after template changes."""
        self.report_provider_filter_changed.emit(
            *self.current_report_provider_filter()
        )

    def set_report_provider_options(
        self,
        options: tuple[ReportProviderOption, ...],
    ) -> None:
        """Replace compatible choices without silently replacing a selection."""
        previous = self._selected_report_provider(allow_empty=True)
        previous_key = (
            previous.get('provider_id'),
            previous.get('provider_version'),
        ) if previous else None

        self.report_provider_combo.blockSignals(True)
        self.report_provider_combo.clear()
        selected_index = -1
        for option in options:
            data = {
                'provider_id': option.provider_id,
                'provider_version': option.provider_version,
                'display_name': option.display_name,
                'available': True,
                'is_builtin': option.is_builtin,
            }
            label = (
                f'{option.display_name} '
                f'({option.provider_id}@{option.provider_version})'
            )
            self.report_provider_combo.addItem(label, data)
            if previous_key == (option.provider_id, option.provider_version):
                selected_index = self.report_provider_combo.count() - 1

        if (
            previous
            and previous_key is not None
            and selected_index < 0
            and previous.get('provider_id') != BUILTIN_PROVIDER_ID
        ):
            stale = dict(previous)
            stale['available'] = False
            self.report_provider_combo.addItem(
                f"{stale.get('display_name') or stale['provider_id']} "
                f"({stale['provider_id']}@{stale['provider_version']})"
                '（当前不可用）',
                stale,
            )
            selected_index = self.report_provider_combo.count() - 1

        if selected_index < 0 and self.report_provider_combo.count() > 0:
            selected_index = 0
        self.report_provider_combo.setCurrentIndex(selected_index)
        self.report_provider_combo.blockSignals(False)
        self._update_report_provider_hint()
        self._sync_ppt_master_mode()

    def is_report_provider_selection_available(self) -> bool:
        """True only for one current compatible choice."""
        return bool(self._selected_report_provider().get('available'))

    def _selected_report_provider(
        self,
        *,
        allow_empty: bool = False,
    ) -> dict[str, Any]:
        data = self.report_provider_combo.currentData()
        if isinstance(data, dict):
            return dict(data)
        if allow_empty:
            return {}
        return {
            'provider_id': BUILTIN_PROVIDER_ID,
            'provider_version': BUILTIN_PROVIDER_VERSION,
            'display_name': '内置标准生成器',
            'available': True,
            'is_builtin': True,
        }

    def _update_report_provider_hint(self, *_args: object) -> None:
        current = self._selected_report_provider()
        if not current.get('available'):
            self.report_provider_hint.setText(
                '已选择的报告后端当前不可用；请重新选择，系统不会静默回退。'
            )
            self.report_provider_hint.setStyleSheet(
                'color: #cf1322; font-size: 11px;'
            )
            return
        professional_count = sum(
            1
            for index in range(self.report_provider_combo.count())
            if not bool(
                (self.report_provider_combo.itemData(index) or {}).get(
                    'is_builtin', False
                )
            )
            and bool(
                (self.report_provider_combo.itemData(index) or {}).get(
                    'available', False
                )
            )
        )
        if professional_count:
            self.report_provider_hint.setText(
                f'已检测到 {professional_count} 个兼容专业后端；失败时不会回退。'
            )
            self.report_provider_hint.setStyleSheet(
                'color: #237804; font-size: 11px;'
            )
        else:
            self.report_provider_hint.setText(
                '未检测到兼容的专业后端；当前可使用内置标准生成器。'
            )
            self.report_provider_hint.setStyleSheet(
                'color: #8c8c8c; font-size: 11px;'
            )

    def _is_ppt_master_selected(self) -> bool:
        return (
            self._selected_report_provider().get('provider_id')
            == PPT_MASTER_PROVIDER_ID
        )

    def _on_report_provider_changed(self, *_args: object) -> None:
        self._update_report_provider_hint()
        self._sync_ppt_master_mode()
        self.report_inputs_changed.emit()

    def _sync_ppt_master_mode(self) -> None:
        host_mode = self._is_ppt_master_selected()
        self.ppt_master_group.setVisible(host_mode)
        if host_mode:
            self.outline_header.setText('PPT Master 方案预览 (分阶段确认)')
            self.outline_hint.setText(
                '此处显示结构化大纲、设计契约和逐页图片位置；'
                '内容由确认指纹保护，不可直接编辑。'
            )
            self.outline_editor.setReadOnly(True)
            self.outline_btn.setText('🧭 生成/重新生成 PPT Master 方案')
            self.full_report_btn.setText('🚀 生成已确认的 PPT Master 报告')
            self.full_report_btn.setEnabled(
                self._ppt_master_plan_ready
                and self._diagnosis_record is not None
            )
        else:
            self.outline_header.setText('报告大纲 (可编辑)')
            self.outline_hint.setText(
                '点击「生成/预览报告大纲」后，AI 生成的大纲显示在此。'
                '你可直接编辑、增删章节、修改标题。'
            )
            self.outline_editor.setReadOnly(False)
            self.outline_btn.setText('📝 生成/预览报告大纲')
            self.full_report_btn.setText('🚀 基于大纲生成完整报告')
            self.full_report_btn.setEnabled(
                self._diagnosis_record is not None
                and bool(self.get_outline_text())
            )

    def _select_builtin_provider(self) -> None:
        for index in range(self.report_provider_combo.count()):
            data = self.report_provider_combo.itemData(index) or {}
            if data.get('provider_id') == BUILTIN_PROVIDER_ID:
                self.report_provider_combo.setCurrentIndex(index)
                return

    def set_full_report_ready(self, ready: bool) -> None:
        """控制器在外围判断是否可以激活完整报告按钮."""
        self.full_report_btn.setEnabled(ready)

    def set_ppt_master_workflow_view(
        self,
        *,
        status_text: str,
        preview_markdown: str,
        next_action: str = '',
        next_action_label: str = '',
        ready_for_authoring: bool = False,
        valid_for_current_inputs: bool = True,
    ) -> None:
        """Display one path-free Host workflow view model."""
        self._ppt_master_next_action = next_action
        self._ppt_master_plan_ready = bool(
            ready_for_authoring and valid_for_current_inputs
        )
        self.ppt_master_status_label.setText(status_text)
        self.ppt_master_status_label.setStyleSheet(
            ('color: #237804;' if self._ppt_master_plan_ready else 'color: #ad6800;')
            + ' font-size: 11px; font-weight: bold;'
        )
        self.ppt_master_confirm_btn.setText(
            next_action_label or '当前阶段无需确认'
        )
        self.ppt_master_confirm_btn.setEnabled(bool(next_action))
        self.outline_editor.setPlainText(preview_markdown)
        self.outline_editor.setReadOnly(True)
        self.full_report_btn.setEnabled(
            self._ppt_master_plan_ready and self._diagnosis_record is not None
        )

    def invalidate_ppt_master_workflow(self, reason: str) -> None:
        """Make an existing Host plan visibly unusable after input changes."""
        self._ppt_master_next_action = ''
        self._ppt_master_plan_ready = False
        self.ppt_master_status_label.setText(
            f'规划已失效：{reason}。请重新生成 PPT Master 方案。'
        )
        self.ppt_master_status_label.setStyleSheet(
            'color: #cf1322; font-size: 11px; font-weight: bold;'
        )
        self.ppt_master_confirm_btn.setText('请重新生成方案')
        self.ppt_master_confirm_btn.setEnabled(False)
        self.full_report_btn.setEnabled(False)

    def reset_ui(self) -> None:
        """清空表单和编辑器，恢复初始状态."""
        self.outline_editor.clear()
        self.full_report_btn.setEnabled(False)
        self._req_file_path = ''
        self._template_file_path = ''
        self._template_source_path = ''
        self._template_status = ''
        self._template_result_summary = ''
        self._ppt_master_next_action = ''
        self._ppt_master_plan_ready = False
        self._req_file_label.setText('需求文件: 未选择')
        self._req_file_label.setStyleSheet('color: #888; font-size: 12px;')
        self._tmpl_file_label.setText('模板文件: 未选择')
        self._tmpl_file_label.setStyleSheet('color: #888; font-size: 12px;')
        self._tmpl_file_label.setToolTip('')
        self.view_template_result_btn.setEnabled(False)
        self._proj_file_list.clear()
        self._sync_ppt_master_mode()

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
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff,
        )
        scroll.setVerticalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAsNeeded,
        )

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
        self.word_radio.toggled.connect(
            self._on_report_type_toggled
        )
        self.ppt_radio.toggled.connect(
            self._on_report_type_toggled
        )
        type_layout.addWidget(self.word_radio)
        type_layout.addWidget(self.ppt_radio)
        type_group.setLayout(type_layout)
        layout.addWidget(type_group)

        # ── 报告生成后端 ──
        provider_group = QGroupBox('报告生成后端')
        provider_group.setStyleSheet(CARD)
        provider_layout = QVBoxLayout()
        self.report_provider_combo = QComboBox()
        self.report_provider_combo.setToolTip(
            '仅显示与当前 Word/PPT 格式及模板模式兼容的后端。'
        )
        self.report_provider_combo.currentIndexChanged.connect(
            self._on_report_provider_changed
        )
        provider_layout.addWidget(self.report_provider_combo)
        self.report_provider_hint = QLabel('正在检测兼容后端...')
        self.report_provider_hint.setWordWrap(True)
        self.report_provider_hint.setStyleSheet(
            'color: #8c8c8c; font-size: 11px;'
        )
        provider_layout.addWidget(self.report_provider_hint)
        provider_group.setLayout(provider_layout)
        layout.addWidget(provider_group)

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

        req_row = QHBoxLayout()
        req_btn = QPushButton('选择需求文件...')
        req_btn.setStyleSheet(BTN_SECONDARY)
        req_btn.clicked.connect(self._on_select_req_file)
        req_row.addWidget(req_btn)
        clear_req_btn = QPushButton('✕ 清除')
        clear_req_btn.setStyleSheet(BTN_SECONDARY)
        clear_req_btn.clicked.connect(self._on_clear_req_file)
        req_row.addWidget(clear_req_btn)
        req_row.addStretch()
        file_layout.addLayout(req_row)

        # 模板文件
        self._tmpl_file_label = QLabel('模板文件: 未选择')
        self._tmpl_file_label.setStyleSheet('color: #888; font-size: 12px;')
        self._tmpl_file_label.setWordWrap(True)
        file_layout.addWidget(self._tmpl_file_label)

        self.template_prepare_hint = QLabel(
            'Word/PPT 模板检测与标准化：保留样式，清除样例内容，'
            '不兼容模板会明确提示。'
        )
        self.template_prepare_hint.setWordWrap(True)
        self.template_prepare_hint.setStyleSheet(
            'color: #595959; font-size: 11px; '
            'background: #f6ffed; border: 1px solid #b7eb8f; '
            'border-radius: 4px; padding: 6px;'
        )
        file_layout.addWidget(self.template_prepare_hint)

        self.template_prepare_btn = QPushButton(
            '🔍 检测/标准化并载入模板...'
        )
        self.template_prepare_btn.setStyleSheet(BTN_SECONDARY)
        self.template_prepare_btn.clicked.connect(
            self._on_select_template_file
        )
        file_layout.addWidget(self.template_prepare_btn)

        tmpl_result_row = QHBoxLayout()
        self.view_template_result_btn = QPushButton('查看模板检测结果')
        self.view_template_result_btn.setStyleSheet(BTN_SECONDARY)
        self.view_template_result_btn.setEnabled(False)
        self.view_template_result_btn.clicked.connect(
            self._on_view_template_result
        )
        tmpl_result_row.addWidget(self.view_template_result_btn)
        clear_tmpl_btn = QPushButton('✕ 清除')
        clear_tmpl_btn.setStyleSheet(BTN_SECONDARY)
        clear_tmpl_btn.clicked.connect(self._on_clear_template_file)
        tmpl_result_row.addWidget(clear_tmpl_btn)
        tmpl_result_row.addStretch()
        file_layout.addLayout(tmpl_result_row)

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

        # ★ 拦截 Delete 键 — 删除关联资料选中项
        self._proj_file_list.keyPressEvent = self._proj_list_key_press

        proj_btn_row = QHBoxLayout()
        add_proj_btn = QPushButton('添加文件到资料列表')
        add_proj_btn.setStyleSheet(BTN_SECONDARY)
        add_proj_btn.clicked.connect(self._on_add_project_file)
        proj_btn_row.addWidget(add_proj_btn)
        remove_proj_btn = QPushButton('移除选中资料')
        remove_proj_btn.setStyleSheet(BTN_SECONDARY)
        remove_proj_btn.clicked.connect(self._on_remove_project_files)
        proj_btn_row.addWidget(remove_proj_btn)
        proj_btn_row.addStretch()
        file_layout.addLayout(proj_btn_row)

        file_group.setLayout(file_layout)
        layout.addWidget(file_group)

        # ── Bridge 素材摘要 (Batch 3.3.2) ──
        self._bridge_group = QGroupBox('技能输出素材')
        self._bridge_group.setStyleSheet(CARD)
        bridge_layout = QVBoxLayout()
        bridge_layout.setSpacing(6)

        self._bridge_status_label = QLabel('状态: 未发送')
        self._bridge_status_label.setStyleSheet(
            'color: #888; font-size: 12px; font-weight: bold;'
        )
        self._bridge_status_label.setWordWrap(True)
        bridge_layout.addWidget(self._bridge_status_label)

        self._bridge_asset_list = QListWidget()
        self._bridge_asset_list.setStyleSheet("""
            QListWidget {
                border: 1px solid #e0e0e0; border-radius: 4px;
                background: white; min-height: 60px; max-height: 200px;
            }
            QListWidget::item { padding: 4px 8px; }
        """)
        self._bridge_asset_list.setSelectionMode(
            QListWidget.SelectionMode.NoSelection
        )
        bridge_layout.addWidget(self._bridge_asset_list)

        self._bridge_info_label = QLabel('')
        self._bridge_info_label.setStyleSheet(
            'color: #888; font-size: 11px;'
        )
        self._bridge_info_label.setWordWrap(True)
        bridge_layout.addWidget(self._bridge_info_label)

        bridge_btn_row = QHBoxLayout()
        self._bridge_cancel_prepare_btn = QPushButton('⏹ 取消准备')
        self._bridge_cancel_prepare_btn.setStyleSheet(
            'QPushButton { background: #ff4d4f; color: white; padding: 6px 12px; '
            'border-radius: 4px; font-weight: bold; font-size: 12px; }'
        )
        self._bridge_cancel_prepare_btn.clicked.connect(
            self.bridge_cancel_prepare_requested.emit
        )
        self._bridge_cancel_prepare_btn.hide()
        bridge_btn_row.addWidget(self._bridge_cancel_prepare_btn)

        self._bridge_clear_btn = QPushButton('🗑 清除素材')
        self._bridge_clear_btn.setStyleSheet(BTN_SECONDARY)
        self._bridge_clear_btn.clicked.connect(
            self.bridge_clear_requested.emit
        )
        self._bridge_clear_btn.hide()
        bridge_btn_row.addWidget(self._bridge_clear_btn)

        bridge_btn_row.addStretch()
        bridge_layout.addLayout(bridge_btn_row)

        self._bridge_group.setLayout(bridge_layout)
        self._bridge_group.setVisible(False)  # Hidden until first bridge result
        layout.addWidget(self._bridge_group)

        # ── 操作按钮 ──
        btn_group = QGroupBox('操作')
        btn_group.setStyleSheet(CARD)
        btn_layout = QVBoxLayout()
        btn_layout.setSpacing(8)

        self.outline_btn = QPushButton('📝 生成/预览报告大纲')
        self.outline_btn.setStyleSheet(BTN_PRIMARY)
        self.outline_btn.clicked.connect(self._on_outline_requested)
        btn_layout.addWidget(self.outline_btn)

        self.ppt_master_group = QGroupBox('PPT Master 分阶段确认')
        self.ppt_master_group.setStyleSheet(
            'QGroupBox { border: 1px solid #91caff; border-radius: 5px; '
            'padding: 8px; padding-top: 20px; color: #0958d9; }'
        )
        ppt_master_layout = QVBoxLayout()
        self.ppt_master_status_label = QLabel(
            '尚未生成方案。大纲、设计、逐页计划将分别确认。'
        )
        self.ppt_master_status_label.setWordWrap(True)
        self.ppt_master_status_label.setStyleSheet(
            'color: #595959; font-size: 11px;'
        )
        ppt_master_layout.addWidget(self.ppt_master_status_label)
        self.ppt_master_confirm_btn = QPushButton('等待生成方案')
        self.ppt_master_confirm_btn.setStyleSheet(BTN_PRIMARY)
        self.ppt_master_confirm_btn.setEnabled(False)
        self.ppt_master_confirm_btn.clicked.connect(
            self._on_ppt_master_planning_requested
        )
        ppt_master_layout.addWidget(self.ppt_master_confirm_btn)
        self.ppt_master_fallback_btn = QPushButton('明确改用内置标准生成器')
        self.ppt_master_fallback_btn.setStyleSheet(BTN_SECONDARY)
        self.ppt_master_fallback_btn.setToolTip(
            '仅在你主动点击后切换；PPT Master 失败不会自动回退。'
        )
        self.ppt_master_fallback_btn.clicked.connect(
            self._select_builtin_provider
        )
        ppt_master_layout.addWidget(self.ppt_master_fallback_btn)
        self.ppt_master_group.setLayout(ppt_master_layout)
        self.ppt_master_group.hide()
        btn_layout.addWidget(self.ppt_master_group)

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

        self.cancel_btn = QPushButton('⏹ 取消生成')
        self.cancel_btn.setStyleSheet(
            'QPushButton { background: #ff4d4f; color: white; padding: 8px 16px; '
            'border-radius: 4px; font-weight: bold; }'
        )
        self.cancel_btn.hide()
        self.cancel_btn.clicked.connect(self._on_cancel_requested)
        btn_layout.addWidget(self.cancel_btn)

        help_btn = QPushButton('📖 编写说明')
        help_btn.setStyleSheet(BTN_SECONDARY)
        help_btn.clicked.connect(self._show_help)
        btn_layout.addWidget(help_btn)

        btn_group.setLayout(btn_layout)
        layout.addWidget(btn_group)

        layout.addStretch()
        panel.setLayout(layout)
        scroll.setWidget(panel)
        return scroll

    def _build_right_panel(self) -> QWidget:
        panel = QWidget()
        layout = QVBoxLayout()
        layout.setContentsMargins(14, 14, 14, 14)
        layout.setSpacing(8)

        self.outline_header = QLabel('报告大纲 (可编辑)')
        self.outline_header.setStyleSheet(
            'font-size: 15px; font-weight: bold; color: #333; padding: 2px 0;'
        )
        layout.addWidget(self.outline_header)

        self.outline_hint = QLabel(
            '点击「生成/预览报告大纲」后，AI 生成的大纲显示在此。'
            '你可直接编辑、增删章节、修改标题。'
        )
        self.outline_hint.setStyleSheet('color: #888; font-size: 12px;')
        self.outline_hint.setWordWrap(True)
        layout.addWidget(self.outline_hint)

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
            self.report_inputs_changed.emit()

    def _on_select_template_file(self) -> None:
        filt = (
            '报告模板 (*.docx *.pptx);;'
            'Word 模板 (*.docx);;'
            'PowerPoint 模板 (*.pptx);;'
            'All Files (*.*)'
        )
        path, _ = QFileDialog.getOpenFileName(
            self,
            '选择要检测和标准化的模板文件',
            '',
            filt,
        )
        if path:
            # 即时执行“检测 → 标准化 → 缓存”；源文件永不原地修改。
            import os as _os
            fname = _os.path.basename(path)
            from utils.report_template_preparation import prepare_report_template

            suffix = Path(path).suffix.lower()
            if suffix == '.pptx':
                report_type = 'ppt'
                self.ppt_radio.setChecked(True)
            elif suffix == '.docx':
                report_type = 'word'
                self.word_radio.setChecked(True)
            else:
                self._template_result_summary = (
                    f'模板: {fname}\n状态: 不支持的文件类型\n'
                    '当前支持: Word .docx、PowerPoint .pptx'
                )
                self.view_template_result_btn.setEnabled(True)
                QMessageBox.warning(
                    self,
                    f'模板「{fname}」不可用',
                    '当前仅支持 .docx Word 模板和 .pptx PowerPoint 模板。',
                )
                return
            try:
                result = prepare_report_template(path, report_type)
            except Exception as exc:
                self._template_result_summary = (
                    f'模板: {fname}\n状态: 处理失败\n错误: {exc}'
                )
                self.view_template_result_btn.setEnabled(True)
                QMessageBox.warning(
                    self,
                    f'模板「{fname}」处理失败',
                    f'模板检测或标准化过程中发生错误：{exc}',
                )
                return
            status_text = {
                'direct': '直接可用',
                'normalized': '已自动标准化',
                'rejected': '不可用',
            }.get(result.status, result.status)
            summary_lines = [
                f'模板类型: {"PPT" if report_type == "ppt" else "Word"}',
                f'源模板: {path}',
                f'检测状态: {status_text}',
                f'兼容评分: {result.score}',
            ]
            if result.usable_path:
                summary_lines.append(f'生成使用: {result.usable_path}')
            if result.reasons:
                summary_lines.append('处理说明:')
                summary_lines.extend(f'• {item}' for item in result.reasons)
            if result.warnings:
                summary_lines.append('注意事项:')
                summary_lines.extend(f'• {item}' for item in result.warnings)
            self._template_result_summary = '\n'.join(summary_lines)
            self.view_template_result_btn.setEnabled(True)
            if not result.accepted or result.usable_path is None:
                QMessageBox.warning(
                    self,
                    f'模板「{fname}」不可用',
                    '\n'.join(result.reasons) or '模板未通过准入检查。',
                )
                return

            self._template_source_path = path
            self._template_file_path = result.usable_path
            self._template_status = result.status
            self._tmpl_file_label.setToolTip(
                f'源模板: {path}\n生成使用: {result.usable_path}'
            )
            if result.status == 'normalized':
                self._tmpl_file_label.setText(
                    f'模板: {fname}（已自动标准化，评分 {result.score}）'
                )
                self._tmpl_file_label.setStyleSheet(
                    'color: #d46b08; font-size: 12px; font-weight: bold;'
                )
                detail = '\n'.join((*result.reasons, *result.warnings))
                QMessageBox.information(
                    self,
                    '模板已自动标准化',
                    detail or '模板已转换为报告生成器可稳定使用的格式。',
                )
            else:
                self._tmpl_file_label.setText(
                    f'模板: {fname}（直接可用，评分 {result.score}）'
                )
                self._tmpl_file_label.setStyleSheet(
                    'color: #389e0d; font-size: 12px; font-weight: bold;'
                )
            self.notify_report_template_changed()
            self.report_inputs_changed.emit()

    def _on_view_template_result(self) -> None:
        """再次查看最近一次模板检测、标准化与缓存结果。"""
        if not self._template_result_summary:
            return
        QMessageBox.information(
            self,
            '模板检测与标准化结果',
            self._template_result_summary,
        )

    def _on_add_project_file(self) -> None:
        paths, _ = QFileDialog.getOpenFileNames(
            self, '添加项目资料', '',
            'All Files (*.*);;CSV (*.csv);;Excel (*.xlsx);;Text (*.txt *.md)',
        )
        for path in paths:
            item = QListWidgetItem(path)
            item.setToolTip(path)
            self._proj_file_list.addItem(item)
        if paths:
            self.report_inputs_changed.emit()

    def _on_remove_project_files(self) -> None:
        """删除关联资料列表中选中的项 (倒序删，避免索引错位)。"""
        selected = self._proj_file_list.selectedItems()
        if not selected:
            return
        rows = sorted((self._proj_file_list.row(item) for item in selected), reverse=True)
        for row in rows:
            self._proj_file_list.takeItem(row)
        self.report_inputs_changed.emit()

    def _proj_list_key_press(self, event) -> None:
        """拦截 Delete 键 — 删除关联资料选中项。"""
        from PyQt6.QtCore import Qt as _Qt
        from PyQt6.QtGui import QKeyEvent
        if event.key() == _Qt.Key.Key_Delete:
            self._on_remove_project_files()
        else:
            QListWidget.keyPressEvent(self._proj_file_list, event)

    def _on_clear_req_file(self) -> None:
        """清除需求文件。"""
        self._req_file_path = ''
        self._req_file_label.setText('需求文件: 未选择')
        self._req_file_label.setStyleSheet('color: #888; font-size: 12px;')
        self.report_inputs_changed.emit()

    def _on_clear_template_file(self) -> None:
        """清除模板文件。"""
        self._template_file_path = ''
        self._template_source_path = ''
        self._template_status = ''
        self._template_result_summary = ''
        self._tmpl_file_label.setText('模板文件: 未选择')
        self._tmpl_file_label.setStyleSheet('color: #888; font-size: 12px;')
        self._tmpl_file_label.setToolTip('')
        self.view_template_result_btn.setEnabled(False)
        self.notify_report_template_changed()
        self.report_inputs_changed.emit()

    def _on_report_type_toggled(self, checked: bool) -> None:
        if checked:
            self.report_provider_filter_changed.emit(
                *self.current_report_provider_filter()
            )
            self.report_inputs_changed.emit()

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
            from core.report_engine import count_sensors
            ts = rec.get('timestamp', '?')
            sensors = count_sensors(rec)
            self.diag_loaded_label.setText(f'诊断数据: {ts} ({sensors} 传感器)')
            self.diag_loaded_label.setStyleSheet('color: #52c41a; font-weight: bold; font-size: 11px;')
        else:
            self.diag_loaded_label.setText('诊断数据: (未加载)')
            self.diag_loaded_label.setStyleSheet('color: #888; font-size: 11px;')
        self.report_inputs_changed.emit()

    def _on_load_diagnosis(self) -> None:
        if not self._get_project_file_paths():
            QMessageBox.warning(
                self, '缺少项目关联资料',
                '请先添加项目关联资料，再加载诊断记录。')
            return
        self.load_diagnosis_requested.emit()

    def _on_outline_requested(self) -> None:
        if not self._get_project_file_paths():
            QMessageBox.warning(
                self, '缺少项目关联资料',
                '请先添加项目关联资料，再生成报告大纲。')
            return
        if self._diagnosis_record is None:
            QMessageBox.warning(
                self, '未加载诊断数据',
                '请先点击「📋 从已存诊断加载」载入诊断记录，再生成报告大纲。\n\n'
                '说明：未加载诊断时，大纲将偏离 FBG 光纤传感领域，产生无用的 IT 运维模板。')
            return
        self.outline_requested.emit(self.get_config())

    def _on_full_report_requested(self) -> None:
        if not self._get_project_file_paths():
            QMessageBox.warning(
                self, '缺少项目关联资料',
                '请先添加项目关联资料，再生成完整报告。')
            return
        if self._diagnosis_record is None:
            QMessageBox.warning(
                self, '未加载诊断数据',
                '请先点击「📋 从已存诊断加载」载入诊断记录，再生成完整报告。')
            return
        if not self.is_report_provider_selection_available():
            QMessageBox.warning(
                self,
                '报告后端不可用',
                '当前明确选择的报告后端已不可用。请重新选择后再生成；'
                '系统不会静默回退到内置后端。',
            )
            return
        if self._is_ppt_master_selected() and not self._ppt_master_plan_ready:
            QMessageBox.warning(
                self,
                'PPT Master 方案未确认',
                '请依次确认叙事大纲、视觉设计和逐页计划，再生成报告。',
            )
            return
        outline = self.outline_editor.toPlainText().strip()
        if not outline:
            outline = '(空大纲 — 将使用默认模板)'
        self.full_report_requested.emit(self.get_config(), outline)

    def _on_ppt_master_planning_requested(self) -> None:
        if not self._ppt_master_next_action:
            return
        self.ppt_master_planning_requested.emit(
            self._ppt_master_next_action,
            self.get_config(),
        )

    def _on_cancel_requested(self) -> None:
        self.cancel_requested.emit()

    def set_generation_running(self, running: bool) -> None:
        """显示/隐藏取消按钮，锁住/解锁生成按钮."""
        if running:
            self.cancel_btn.show()
            self.full_report_btn.setEnabled(False)
            self.outline_btn.setEnabled(False)
            self.report_provider_combo.setEnabled(False)
        else:
            self.cancel_btn.hide()
            self.full_report_btn.setEnabled(
                self._ppt_master_plan_ready
                if self._is_ppt_master_selected()
                else self._diagnosis_record is not None
            )
            self.outline_btn.setEnabled(True)
            self.report_provider_combo.setEnabled(True)

    # ── Bridge public API (Batch 3.3.2) ──

    def set_bridge_status(self, status: str, assets: tuple = (),
                          safe_error: str = "", info_text: str = "",
                          request_id: str = "", generation: int = 0) -> None:
        """Receive a bridge status update. Path-free — only ReportAssetSummary.

        Args:
            status: PREPARING, READY, FAILED, CANCELLED, SUCCEEDED
            assets: tuple of ReportAssetSummary (safe for display)
            safe_error: safe error message for FAILED
            info_text: auxiliary info text
            request_id: opaque request id for claim/discard
            generation: monotonic generation number
        """
        self._bridge_status = status
        self._bridge_assets = assets
        self._bridge_request_id = request_id
        self._bridge_generation = generation

        # Show the bridge group
        self._bridge_group.setVisible(True)

        # Clear asset list
        self._bridge_asset_list.clear()

        # Status-specific UI
        if status == "preparing":
            self._bridge_status_label.setText('状态: ⏳ 正在准备素材...')
            self._bridge_status_label.setStyleSheet(
                'color: #1890ff; font-size: 12px; font-weight: bold;'
            )
            self._bridge_cancel_prepare_btn.show()
            self._bridge_clear_btn.hide()
            self._bridge_info_label.setText('')

        elif status == "ready":
            # Show asset summaries
            count = len(assets)
            self._bridge_status_label.setText(
                f'状态: ✅ 素材准备完成 ({count} 项)'
            )
            self._bridge_status_label.setStyleSheet(
                'color: #52c41a; font-size: 12px; font-weight: bold;'
            )
            self._bridge_cancel_prepare_btn.hide()
            self._bridge_clear_btn.show()

            # Populate asset list (path-free)
            for a in assets:
                role_text = {
                    "image": "图片", "table_source": "表格",
                    "text_source": "文本",
                }.get(getattr(a, 'role', ''), getattr(a, 'role', '?'))
                size_str = f"{getattr(a, 'size_bytes', 0) / 1024:.1f} KB" if getattr(a, 'size_bytes', 0) > 0 else "—"
                text = (f"#{getattr(a, 'order', 0)} [{role_text}] "
                        f"{getattr(a, 'display_name', '?')} ({size_str})")
                from PyQt6.QtWidgets import QListWidgetItem
                item = QListWidgetItem(text)
                self._bridge_asset_list.addItem(item)

            total_size = sum(getattr(a, 'size_bytes', 0) for a in assets)
            self._bridge_info_label.setText(
                f'素材数量: {count}  总大小: {total_size / 1024:.1f} KB'
                if total_size > 0 else f'素材数量: {count}'
            )

        elif status == "failed":
            self._bridge_status_label.setText('状态: ❌ 素材准备失败')
            self._bridge_status_label.setStyleSheet(
                'color: #ff4d4f; font-size: 12px; font-weight: bold;'
            )
            self._bridge_cancel_prepare_btn.hide()
            self._bridge_clear_btn.hide()
            if safe_error:
                self._bridge_info_label.setText(f'错误: {safe_error}')
                self._bridge_info_label.setStyleSheet(
                    'color: #ff4d4f; font-size: 11px;'
                )

        elif status == "cancelled":
            self._bridge_status_label.setText('状态: ⏹ 已取消')
            self._bridge_status_label.setStyleSheet(
                'color: #888; font-size: 12px; font-weight: bold;'
            )
            self._bridge_cancel_prepare_btn.hide()
            self._bridge_clear_btn.hide()
            self._bridge_info_label.setText('')

        elif status == "succeeded":
            count = len(assets)
            self._bridge_status_label.setText(
                f'状态: 🎉 素材已用于报告 ({count} 项)'
            )
            self._bridge_status_label.setStyleSheet(
                'color: #52c41a; font-size: 12px; font-weight: bold;'
            )
            self._bridge_cancel_prepare_btn.hide()
            self._bridge_clear_btn.hide()

            for a in assets:
                role_text = {
                    "image": "图片", "table_source": "表格",
                    "text_source": "文本",
                }.get(getattr(a, 'role', ''), getattr(a, 'role', '?'))
                text = (f"#{getattr(a, 'order', 0)} [{role_text}] "
                        f"{getattr(a, 'display_name', '?')}")
                from PyQt6.QtWidgets import QListWidgetItem
                item = QListWidgetItem(text)
                self._bridge_asset_list.addItem(item)

        elif status == "released" or status == "":
            # Hide bridge group on release/clear
            self._bridge_group.setVisible(False)
            self._bridge_status = ""
            self._bridge_assets = ()
            self._bridge_request_id = ""
            self._bridge_generation = 0
        self.report_inputs_changed.emit()

    def get_bridge_claim_info(self) -> dict:
        """Return bridge claim info for report generation (Batch 3.3.2).

        Returns dict with request_id, generation if READY, else empty.
        """
        if self._bridge_status == "ready" and self._bridge_request_id:
            return {
                "request_id": self._bridge_request_id,
                "generation": self._bridge_generation,
            }
        return {}

    def has_ready_bridge_assets(self) -> bool:
        """Check if bridge has READY assets for report inclusion."""
        return self._bridge_status == "ready" and len(self._bridge_assets) > 0

    def get_bridge_assets_for_report(self) -> tuple:
        """Get bridge assets for report. Returns empty tuple if not READY."""
        if self._bridge_status == "ready":
            return self._bridge_assets
        return ()

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
