"""全局参数池对话框 — Dumb Component，仅发射信号，不直接修改状态.

Signals:
    param_updated(name, entry_dict): 用户添加/修改参数时发射
    param_deleted(name):             用户删除参数时发射
"""

from __future__ import annotations

import re
from typing import Any, Dict, Optional

from PyQt6.QtCore import QRegularExpression, Qt, pyqtSignal
from PyQt6.QtGui import QRegularExpressionValidator
from PyQt6.QtWidgets import (
    QAbstractItemView, QDialog, QFormLayout, QGroupBox,
    QHBoxLayout, QHeaderView, QLabel, QLineEdit,
    QMessageBox, QPushButton, QTableWidget, QTableWidgetItem,
    QVBoxLayout, QWidget,
)

from core.models import GlobalParameter

# Python 保留关键字 — 禁止用作参数名
_PYTHON_KEYWORDS: set[str] = {
    'False', 'None', 'True', 'and', 'as', 'assert', 'async', 'await',
    'break', 'class', 'continue', 'def', 'del', 'elif', 'else', 'except',
    'finally', 'for', 'from', 'global', 'if', 'import', 'in', 'is',
    'lambda', 'nonlocal', 'not', 'or', 'pass', 'raise', 'return',
    'try', 'while', 'with', 'yield',
}


class GlobalParameterDialog(QDialog):
    """全局参数池编辑器

    笨组件原则: 不直接修改 AppState 或 SensorSystem。
    所有变更通过信号通知控制器，由控制器决定如何持久化。
    """

    param_updated = pyqtSignal(str, dict)  # (name, {'value':float,'unit':str,'description':str})
    param_deleted = pyqtSignal(str)        # (name)

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._params: Dict[str, GlobalParameter] = {}
        self._editing_name: Optional[str] = None
        self._dirty: bool = False  # 是否有未提交的修改

        self.setWindowTitle('全局参数与常量管理器')
        self.setMinimumSize(750, 560)
        self._build_ui()
        self._setup_validator()

    # ── Public API ──

    def set_parameters(self, params: Dict[str, GlobalParameter]) -> None:
        """注入当前全局参数（控制器调用）."""
        self._params = dict(params)
        self._dirty = False
        self._refresh_table()

    def get_parameters(self) -> Dict[str, GlobalParameter]:
        """返回当前编辑中的参数快照."""
        return dict(self._params)

    # ── UI 构建 ──

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setSpacing(12)

        # Header
        header = QLabel('全局参数池 — SSOT 变量中心')
        header.setStyleSheet(
            'font-size: 16px; font-weight: bold; color: #722ed1; padding: 4px 0;'
        )
        layout.addWidget(header)

        desc = QLabel(
            '在此定义的参数自动注入所有传感器公式的计算命名空间。'
            '传感器局部常量可覆盖同名全局参数。'
        )
        desc.setStyleSheet('color: #666; font-size: 12px;')
        desc.setWordWrap(True)
        layout.addWidget(desc)

        # ── 参数表格 ──
        self.table = QTableWidget()
        self.table.setColumnCount(4)
        self.table.setHorizontalHeaderLabels(['参数名', '数值', '单位', '说明'])
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.setAlternatingRowColors(True)
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.horizontalHeader().setSectionResizeMode(
            0, QHeaderView.ResizeMode.ResizeToContents
        )
        self.table.horizontalHeader().setSectionResizeMode(
            1, QHeaderView.ResizeMode.ResizeToContents
        )
        self.table.setStyleSheet("""
            QTableWidget {
                border: 1px solid #e0e0e0; border-radius: 6px;
                background: white; gridline-color: #f0f0f0;
            }
            QTableWidget::item { padding: 6px 10px; }
            QTableWidget::item:selected { background: #f0f0ff; color: #333; }
            QHeaderView::section {
                background: #fafafa; border: none;
                border-bottom: 2px solid #e0e0e0;
                padding: 8px 10px; font-weight: bold; color: #555;
            }
        """)
        self.table.itemSelectionChanged.connect(self._on_selection_changed)
        layout.addWidget(self.table, stretch=1)

        # ── 编辑表单 ──
        form_group = QGroupBox('参数编辑')
        form_group.setStyleSheet("""
            QGroupBox {
                border: 1px solid #e0e0e0; border-radius: 8px;
                font-weight: bold; color: #333; padding: 16px; padding-top: 28px;
                background: #fafbfc;
            }
            QGroupBox::title { subcontrol-origin: margin; left: 14px; padding: 0 8px; }
        """)
        form_outer = QVBoxLayout(form_group)
        form_layout = QFormLayout()
        form_layout.setSpacing(8)

        self.name_input = QLineEdit()
        self.name_input.setPlaceholderText('合法变量名，如 K_strain, alpha, temp_coeff')
        self.name_input.setStyleSheet(
            'padding: 6px 10px; border: 1px solid #d9d9d9; border-radius: 4px;'
        )
        self.name_input.textChanged.connect(self._on_name_changed)
        form_layout.addRow('变量名:', self.name_input)

        self.name_error_label = QLabel('')
        self.name_error_label.setStyleSheet('color: #ff4d4f; font-size: 11px;')
        self.name_error_label.setWordWrap(True)
        self.name_error_label.hide()
        form_layout.addRow('', self.name_error_label)

        self.value_input = QLineEdit()
        self.value_input.setPlaceholderText('如 1000.0, 0.85')
        self.value_input.setText('1.0')
        self.value_input.setStyleSheet(
            'padding: 6px 10px; border: 1px solid #d9d9d9; border-radius: 4px;'
        )
        form_layout.addRow('数值:', self.value_input)

        self.unit_input = QLineEdit()
        self.unit_input.setPlaceholderText('如 pm/με, °C, mm')
        self.unit_input.setStyleSheet(
            'padding: 6px 10px; border: 1px solid #d9d9d9; border-radius: 4px;'
        )
        form_layout.addRow('单位:', self.unit_input)

        self.desc_input = QLineEdit()
        self.desc_input.setPlaceholderText('如 默认应变灵敏系数')
        self.desc_input.setStyleSheet(
            'padding: 6px 10px; border: 1px solid #d9d9d9; border-radius: 4px;'
        )
        form_layout.addRow('说明:', self.desc_input)

        form_outer.addLayout(form_layout)

        # ── 按钮行 ──
        btn_row = QHBoxLayout()
        btn_row.setSpacing(10)

        btn_primary = """
            QPushButton {
                background: #722ed1; color: white; border: none; border-radius: 4px;
                padding: 8px 20px; font-size: 13px; font-weight: bold;
            }
            QPushButton:hover { background: #9254de; }
            QPushButton:disabled { background: #d9d9d9; color: #999; }
        """
        btn_danger = """
            QPushButton {
                background: white; color: #ff4d4f;
                border: 1px solid #ffccc7; border-radius: 4px;
                padding: 8px 20px; font-size: 13px;
            }
            QPushButton:hover { color: white; background: #ff4d4f; border-color: #ff4d4f; }
            QPushButton:disabled { color: #ccc; border-color: #eee; }
        """
        btn_default = """
            QPushButton {
                background: white; color: #555;
                border: 1px solid #d9d9d9; border-radius: 4px;
                padding: 8px 16px; font-size: 13px;
            }
            QPushButton:hover { color: #722ed1; border-color: #722ed1; }
        """

        self.save_btn = QPushButton('添加 / 更新')
        self.save_btn.setStyleSheet(btn_primary)
        self.save_btn.setEnabled(False)
        self.save_btn.clicked.connect(self._on_save)
        btn_row.addWidget(self.save_btn)

        self.delete_btn = QPushButton('删除')
        self.delete_btn.setStyleSheet(btn_danger)
        self.delete_btn.setEnabled(False)
        self.delete_btn.clicked.connect(self._on_delete)
        btn_row.addWidget(self.delete_btn)

        self.clear_btn = QPushButton('清空表单')
        self.clear_btn.setStyleSheet(btn_default)
        self.clear_btn.clicked.connect(self._clear_form)
        btn_row.addWidget(self.clear_btn)

        btn_row.addStretch()
        form_outer.addLayout(btn_row)
        layout.addWidget(form_group)

    def _setup_validator(self) -> None:
        """合法 Python 变量名: 字母/下划线开头，后接字母/数字/下划线."""
        regex = QRegularExpression(r'^[a-zA-Z_][a-zA-Z0-9_]*$')
        validator = QRegularExpressionValidator(regex)
        self.name_input.setValidator(validator)

    # ── 表格刷新 ──

    def _refresh_table(self) -> None:
        self.table.setRowCount(len(self._params))
        for row, (name, p) in enumerate(sorted(self._params.items())):
            self.table.setItem(row, 0, QTableWidgetItem(name))
            self.table.setItem(row, 1, QTableWidgetItem(f'{p.value}'))
            self.table.setItem(row, 2, QTableWidgetItem(p.unit))
            self.table.setItem(row, 3, QTableWidgetItem(p.description))
        self.table.resizeColumnsToContents()

    # ── 表单校验 ──

    def _on_name_changed(self, text: str) -> None:
        """实时校验变量名并启用/禁用保存按钮."""
        error = self._validate_name(text)
        if error:
            self.name_error_label.setText(error)
            self.name_error_label.show()
            self.save_btn.setEnabled(False)
        else:
            self.name_error_label.hide()
            self.save_btn.setEnabled(True)

    def _validate_name(self, name: str) -> Optional[str]:
        if not name:
            return '参数名不能为空'
        if name in _PYTHON_KEYWORDS:
            return f"'{name}' 是 Python 保留关键字，禁止使用"
        if not re.match(r'^[a-zA-Z_][a-zA-Z0-9_]*$', name):
            return '非法变量名: 必须以字母或下划线开头，仅含字母、数字、下划线'
        if self._editing_name and name != self._editing_name and name in self._params:
            return f"参数 '{name}' 已存在"
        return None

    def _parse_value(self, text: str) -> Optional[float]:
        try:
            return float(text.strip())
        except (ValueError, TypeError):
            return None

    # ── 选择行 → 填充表单 ──

    def _on_selection_changed(self) -> None:
        selected = self.table.selectedItems()
        if not selected:
            self.delete_btn.setEnabled(False)
            return
        row = selected[0].row()
        name = self.table.item(row, 0).text()
        p = self._params.get(name)
        if not p:
            return

        self._editing_name = name
        # 临时断开信号避免触发校验
        self.name_input.blockSignals(True)
        self.name_input.setText(name)
        self.name_input.blockSignals(False)
        self.value_input.setText(str(p.value))
        self.unit_input.setText(p.unit)
        self.desc_input.setText(p.description)
        self.name_error_label.hide()
        self.save_btn.setEnabled(True)
        self.delete_btn.setEnabled(True)

    # ── 表单操作 ──

    def _clear_form(self) -> None:
        self._editing_name = None
        self.name_input.blockSignals(True)
        self.name_input.clear()
        self.name_input.blockSignals(False)
        self.value_input.setText('1.0')
        self.unit_input.clear()
        self.desc_input.clear()
        self.name_error_label.hide()
        self.save_btn.setEnabled(False)
        self.delete_btn.setEnabled(False)
        self.table.clearSelection()

    def _on_save(self) -> None:
        name = self.name_input.text().strip()
        if not name:
            QMessageBox.warning(self, '校验失败', '参数名不能为空')
            return

        err = self._validate_name(name)
        if err:
            QMessageBox.warning(self, '校验失败', err)
            return

        value = self._parse_value(self.value_input.text())
        if value is None:
            QMessageBox.warning(self, '校验失败', '数值必须为合法数字')
            return

        unit = self.unit_input.text().strip()
        desc = self.desc_input.text().strip()

        # 如果是重命名，删掉旧键
        if self._editing_name and self._editing_name != name:
            self._params.pop(self._editing_name, None)
            self.param_deleted.emit(self._editing_name)

        # 更新本地副本
        param = GlobalParameter(name=name, value=value, unit=unit, description=desc)
        self._params[name] = param

        # 发射信号 — 通知控制器
        self.param_updated.emit(name, {
            'value': value,
            'unit': unit,
            'description': desc,
        })

        self._dirty = True
        self._clear_form()
        self._refresh_table()

    def _on_delete(self) -> None:
        name = self._editing_name
        if not name or name not in self._params:
            return

        reply = QMessageBox.question(
            self, '确认删除',
            f"确定要删除全局参数 '{name}' 吗？\n"
            '删除后使用该参数的传感器公式将无法解析此变量。',
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return

        self._params.pop(name, None)
        self.param_deleted.emit(name)
        self._dirty = True
        self._clear_form()
        self._refresh_table()
