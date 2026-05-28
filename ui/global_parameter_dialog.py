"""Global Parameter Manager Dialog — SSOT variable pool for sensor formulas."""

import re
from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QTableWidget, QTableWidgetItem,
    QPushButton, QLabel, QLineEdit, QDoubleSpinBox, QGroupBox,
    QFormLayout, QMessageBox, QHeaderView, QAbstractItemView, QWidget,
)
from PyQt6.QtCore import Qt


class GlobalParameterDialog(QDialog):
    def __init__(self, parent=None, sensor_system=None):
        super().__init__(parent)
        self.setWindowTitle("全局参数与变量池")
        self.setMinimumSize(700, 520)
        self.sensor_system = sensor_system
        self._editing_name = None

        self._build_ui()
        self._refresh_table()

    def _build_ui(self):
        layout = QVBoxLayout()
        layout.setSpacing(12)

        # ── Header ──
        header = QLabel("全局参数管理 — 所有传感器公式共享的变量池 (SSOT)")
        header.setStyleSheet("font-size: 15px; font-weight: bold; color: #1890ff; padding: 4px 0;")
        layout.addWidget(header)

        desc = QLabel("在此定义的参数会自动注入到所有传感器公式的计算上下文中。局部常量可覆盖同名全局参数。")
        desc.setStyleSheet("color: #666; font-size: 12px; margin-bottom: 4px;")
        desc.setWordWrap(True)
        layout.addWidget(desc)

        # ── Table ──
        self.table = QTableWidget()
        self.table.setColumnCount(4)
        self.table.setHorizontalHeaderLabels(["参数名", "数值", "单位", "参数说明"])
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.setAlternatingRowColors(True)
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        self.table.setStyleSheet("""
            QTableWidget {
                border: 1px solid #e0e0e0; border-radius: 6px;
                background: white; gridline-color: #f0f0f0;
            }
            QTableWidget::item { padding: 6px 10px; }
            QTableWidget::item:selected { background: #e6f7ff; color: #333; }
            QHeaderView::section {
                background: #fafafa; border: none; border-bottom: 2px solid #e0e0e0;
                padding: 8px 10px; font-weight: bold; color: #555;
            }
        """)
        self.table.itemSelectionChanged.connect(self._on_selection_changed)
        layout.addWidget(self.table, stretch=1)

        # ── Form area ──
        form_group = QGroupBox("参数详情")
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
        form_layout.setSpacing(10)

        self.name_input = QLineEdit()
        self.name_input.setPlaceholderText("合法变量名，如 k1, alpha, coeff_strain")
        self.name_input.setStyleSheet("padding: 6px 10px; border: 1px solid #d9d9d9; border-radius: 4px;")
        form_layout.addRow("参数名:", self.name_input)

        self.value_input = QDoubleSpinBox()
        self.value_input.setRange(-1e10, 1e10)
        self.value_input.setDecimals(2)
        self.value_input.setValue(1.0)
        self.value_input.setStyleSheet("padding: 6px 10px; border: 1px solid #d9d9d9; border-radius: 4px;")
        form_layout.addRow("数值:", self.value_input)

        self.unit_input = QLineEdit()
        self.unit_input.setPlaceholderText("如 pm/με, °C, mm")
        self.unit_input.setStyleSheet("padding: 6px 10px; border: 1px solid #d9d9d9; border-radius: 4px;")
        form_layout.addRow("单位:", self.unit_input)

        self.desc_input = QLineEdit()
        self.desc_input.setPlaceholderText("如 默认应变系数")
        self.desc_input.setStyleSheet("padding: 6px 10px; border: 1px solid #d9d9d9; border-radius: 4px;")
        form_layout.addRow("参数说明:", self.desc_input)

        form_outer.addLayout(form_layout)

        # ── Action buttons ──
        btn_row = QHBoxLayout()
        btn_row.setSpacing(10)

        btn_primary = """
            QPushButton {
                background: #1890ff; color: white; border: none; border-radius: 4px;
                padding: 8px 20px; font-size: 13px; font-weight: bold;
            }
            QPushButton:hover { background: #40a9ff; }
            QPushButton:disabled { background: #d9d9d9; color: #999; }
        """
        btn_danger = """
            QPushButton {
                background: white; color: #ff4d4f; border: 1px solid #ffccc7; border-radius: 4px;
                padding: 8px 20px; font-size: 13px;
            }
            QPushButton:hover { color: white; background: #ff4d4f; border-color: #ff4d4f; }
            QPushButton:disabled { color: #ccc; border-color: #eee; }
        """
        btn_default = """
            QPushButton {
                background: white; color: #555; border: 1px solid #d9d9d9; border-radius: 4px;
                padding: 8px 16px; font-size: 13px;
            }
            QPushButton:hover { color: #1890ff; border-color: #1890ff; }
        """

        self.save_btn = QPushButton("保存 / 更新")
        self.save_btn.setStyleSheet(btn_primary)
        self.save_btn.clicked.connect(self._save_param)
        btn_row.addWidget(self.save_btn)

        self.delete_btn = QPushButton("删除")
        self.delete_btn.setStyleSheet(btn_danger)
        self.delete_btn.setEnabled(False)
        self.delete_btn.clicked.connect(self._delete_param)
        btn_row.addWidget(self.delete_btn)

        self.clear_btn = QPushButton("清空表单")
        self.clear_btn.setStyleSheet(btn_default)
        self.clear_btn.clicked.connect(self._clear_form)
        btn_row.addWidget(self.clear_btn)

        btn_row.addStretch()
        form_outer.addLayout(btn_row)
        layout.addWidget(form_group)

        self.setLayout(layout)

    # ── Data ──

    def _get_params(self) -> dict:
        if self.sensor_system and hasattr(self.sensor_system, 'global_parameters'):
            return self.sensor_system.global_parameters
        return {}

    def _refresh_table(self):
        params = self._get_params()
        self.table.setRowCount(len(params))
        for row, (name, entry) in enumerate(sorted(params.items())):
            value = entry if isinstance(entry, (int, float)) else entry.get("value", 0) if isinstance(entry, dict) else 0
            unit = entry.get("unit", "") if isinstance(entry, dict) else ""
            desc = entry.get("description", "") if isinstance(entry, dict) else ""
            self.table.setItem(row, 0, QTableWidgetItem(name))
            self.table.setItem(row, 1, QTableWidgetItem(f'{value:.2f}'))
            self.table.setItem(row, 2, QTableWidgetItem(str(unit)))
            self.table.setItem(row, 3, QTableWidgetItem(str(desc)))
        self.table.resizeColumnsToContents()

    # ── Slots ──

    def _on_selection_changed(self):
        selected = self.table.selectedItems()
        if not selected:
            self.delete_btn.setEnabled(False)
            return
        row = selected[0].row()
        name = self.table.item(row, 0).text()
        params = self._get_params()
        entry = params.get(name, {})
        value = entry if isinstance(entry, (int, float)) else entry.get("value", 0) if isinstance(entry, dict) else 0
        unit = entry.get("unit", "") if isinstance(entry, dict) else ""
        desc = entry.get("description", "") if isinstance(entry, dict) else ""

        self._editing_name = name
        self.name_input.setText(name)
        self.value_input.setValue(float(value))
        self.unit_input.setText(str(unit))
        self.desc_input.setText(str(desc))
        self.delete_btn.setEnabled(True)

    def _clear_form(self):
        self._editing_name = None
        self.name_input.clear()
        self.value_input.setValue(1.0)
        self.unit_input.clear()
        self.desc_input.clear()
        self.table.clearSelection()
        self.delete_btn.setEnabled(False)

    def _save_param(self):
        name = self.name_input.text().strip()
        if not name:
            QMessageBox.warning(self, "参数名不能为空", "请输入参数名。")
            return
        if not re.match(r'^[a-zA-Z_][a-zA-Z0-9_]*$', name):
            QMessageBox.warning(self, "非法参数名",
                                f"'{name}' 不是合法的变量名。请使用英文+数字+下划线，且不能以数字开头。")
            return
        if self._editing_name and name != self._editing_name:
            params = self._get_params()
            if name in params:
                QMessageBox.warning(self, "参数名冲突", f"参数 '{name}' 已存在。")
                return

        value = self.value_input.value()
        unit = self.unit_input.text().strip()
        desc = self.desc_input.text().strip()
        entry = {
            "value": value,
            "unit": unit,
            "description": desc,
        }

        params = self._get_params()
        if self._editing_name and self._editing_name != name:
            params.pop(self._editing_name, None)
        params[name] = entry

        self._editing_name = None
        self._clear_form()
        self._refresh_table()

    def _delete_param(self):
        name = self._editing_name
        if not name:
            return
        params = self._get_params()
        if name not in params:
            return

        # 检查是否有传感器在使用此参数
        if self.sensor_system and hasattr(self.sensor_system, 'sensors'):
            users = []
            for s in self.sensor_system.sensors:
                if s.formula and re.search(r'\b' + re.escape(name) + r'\b', s.formula):
                    users.append(s.id)
            if users:
                reply = QMessageBox.question(
                    self, "确认删除",
                    f"参数 '{name}' 正被以下传感器使用:\n{', '.join(users)}\n\n确定要删除吗？",
                    QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                    QMessageBox.StandardButton.No,
                )
                if reply != QMessageBox.StandardButton.Yes:
                    return

        del params[name]
        self._editing_name = None
        self._clear_form()
        self._refresh_table()
