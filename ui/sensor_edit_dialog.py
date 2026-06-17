"""传感器编辑对话框"""

from __future__ import annotations

from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit,
    QComboBox, QDoubleSpinBox, QPushButton, QCheckBox, QGroupBox,
)

from core.models import Sensor

class SensorEditDialog(QDialog):
    """传感器编辑对话框"""
    def __init__(self, parent=None, sensor=None):
        super().__init__(parent)
        self.setWindowTitle('编辑传感器' if sensor else '添加传感器')
        self.setMinimumWidth(550)
        self.sensor = sensor
        self.parent_window = parent

        layout = QVBoxLayout()

        # Sensor ID
        id_layout = QHBoxLayout()
        id_layout.addWidget(QLabel('传感器ID:'))
        self.id_input = QLineEdit()
        if sensor:
            self.id_input.setText(sensor.id)
        id_layout.addWidget(self.id_input)
        layout.addLayout(id_layout)

        # Physical location
        loc_layout = QHBoxLayout()
        loc_layout.addWidget(QLabel('物理位置:'))
        self.location_input = QLineEdit()
        if sensor:
            self.location_input.setText(sensor.location)
        loc_layout.addWidget(self.location_input)
        layout.addLayout(loc_layout)

        # Sensor type
        type_layout = QHBoxLayout()
        type_layout.addWidget(QLabel('传感器类型:'))
        self.type_combo = QComboBox()
        self.type_combo.addItems([
            'strain: 应变',
            'temperature: 温度',
            'displacement: 位移',
            'inclination: 倾角',
            'pressure: 压力',
            'decoupling: 双参量解耦 (Dual-Decoupling)',
        ])
        if sensor:
            for i in range(self.type_combo.count()):
                text = self.type_combo.itemText(i)
                if text.startswith(sensor.sensor_type):
                    self.type_combo.setCurrentIndex(i)
                    break
        self.type_combo.currentTextChanged.connect(self._on_type_changed)
        type_layout.addWidget(self.type_combo)
        layout.addLayout(type_layout)

        # Formula (hidden for decoupling)
        self.expr_layout = QHBoxLayout()
        self.expr_label = QLabel('公式:')
        self.expr_layout.addWidget(self.expr_label)
        self.expr_input = QLineEdit()
        if sensor and sensor.formula:
            self.expr_input.setText(sensor.formula)
        else:
            self.expr_input.setText('W1 * k1')
        self.expr_input.textChanged.connect(self._on_formula_changed)
        self.expr_layout.addWidget(self.expr_input)
        layout.addLayout(self.expr_layout)

        # 全局参数提示 (常量由全局变量池统一管理)
        self.const_group = QGroupBox('全局参数 (自动注入)')
        const_layout = QVBoxLayout()

        self.global_params_hint = QLabel('')
        self.global_params_hint.setStyleSheet(
            'color: #1890ff; font-size: 12px; padding: 8px; background: #e6f7ff; '
            'border: 1px solid #91d5ff; border-radius: 4px;'
        )
        self.global_params_hint.setWordWrap(True)
        self._update_global_hint()
        const_layout.addWidget(self.global_params_hint)

        self.const_group.setLayout(const_layout)
        layout.addWidget(self.const_group)

        # --- Decoupling matrix panel (hidden by default) ---
        self.decoupling_panel = QGroupBox('解耦配置矩阵')
        dec_layout = QVBoxLayout()

        # FBG selection row
        fbg_row = QHBoxLayout()
        fbg_row.addWidget(QLabel('FBG1 (λ1):'))
        self.fbg1_combo = QComboBox()
        fbg_row.addWidget(self.fbg1_combo)
        fbg_row.addSpacing(20)
        fbg_row.addWidget(QLabel('FBG2 (λ2):'))
        self.fbg2_combo = QComboBox()
        fbg_row.addWidget(self.fbg2_combo)
        fbg_row.addStretch()
        dec_layout.addLayout(fbg_row)

        # Populate FBG dropdowns from parent's sensor system
        if self.parent_window and hasattr(self.parent_window, 'sensor_system'):
            for fbg in self.parent_window.sensor_system.fbgs:
                self.fbg1_combo.addItem(fbg.id)
                self.fbg2_combo.addItem(fbg.id)

        # Matrix header labels
        header_row = QHBoxLayout()
        header_row.addWidget(QLabel(''))
        header_row.addStretch()
        header_row.addWidget(QLabel('应变系数 (pm/με)'))
        header_row.addSpacing(20)
        header_row.addWidget(QLabel('温度系数 (pm/°C)'))
        header_row.addStretch()
        dec_layout.addLayout(header_row)

        # FBG1 row: Ke1, KT1
        row1 = QHBoxLayout()
        row1.addWidget(QLabel('FBG1 λ1:'))
        row1.addStretch()
        self.ke1_input = QDoubleSpinBox()
        self.ke1_input.setDecimals(4)
        self.ke1_input.setSingleStep(0.01)
        self.ke1_input.setRange(-1e6, 1e6)
        self.ke1_input.setValue(1.2)
        row1.addWidget(self.ke1_input)
        row1.addSpacing(20)
        self.kt1_input = QDoubleSpinBox()
        self.kt1_input.setDecimals(4)
        self.kt1_input.setSingleStep(0.01)
        self.kt1_input.setRange(-1e6, 1e6)
        self.kt1_input.setValue(10.0)
        row1.addWidget(self.kt1_input)
        row1.addStretch()
        dec_layout.addLayout(row1)

        # FBG2 row: Ke2, KT2
        row2 = QHBoxLayout()
        row2.addWidget(QLabel('FBG2 λ2:'))
        row2.addStretch()
        self.ke2_input = QDoubleSpinBox()
        self.ke2_input.setDecimals(4)
        self.ke2_input.setSingleStep(0.01)
        self.ke2_input.setRange(-1e6, 1e6)
        self.ke2_input.setValue(1.0)
        row2.addWidget(self.ke2_input)
        row2.addSpacing(20)
        self.kt2_input = QDoubleSpinBox()
        self.kt2_input.setDecimals(4)
        self.kt2_input.setSingleStep(0.01)
        self.kt2_input.setRange(-1e6, 1e6)
        self.kt2_input.setValue(8.0)
        row2.addWidget(self.kt2_input)
        row2.addStretch()
        dec_layout.addLayout(row2)

        self.decoupling_panel.setLayout(dec_layout)
        layout.addWidget(self.decoupling_panel)

        # Load existing decoupling config if editing
        if sensor and sensor.decoupling_config:
            cfg = sensor.decoupling_config
            idx = self.fbg1_combo.findText(cfg.get('fbg1', ''))
            if idx >= 0:
                self.fbg1_combo.setCurrentIndex(idx)
            idx = self.fbg2_combo.findText(cfg.get('fbg2', ''))
            if idx >= 0:
                self.fbg2_combo.setCurrentIndex(idx)
            self.ke1_input.setValue(cfg.get('Ke1', 1.2))
            self.ke2_input.setValue(cfg.get('Ke2', 1.0))
            self.kt1_input.setValue(cfg.get('KT1', 10.0))
            self.kt2_input.setValue(cfg.get('KT2', 8.0))

        # Active checkbox
        self.active_check = QCheckBox('启用')
        self.active_check.setChecked(sensor.active if sensor else True)
        layout.addWidget(self.active_check)

        # Initial visibility
        self._on_type_changed(self.type_combo.currentText())

        # Buttons
        btn_layout = QHBoxLayout()
        ok_btn = QPushButton('确定')
        ok_btn.clicked.connect(self.accept)
        btn_layout.addWidget(ok_btn)

        cancel_btn = QPushButton('取消')
        cancel_btn.clicked.connect(self.reject)
        btn_layout.addWidget(cancel_btn)
        layout.addLayout(btn_layout)

        self.setLayout(layout)

    def _update_global_hint(self):
        """更新全局参数提示标签"""
        if not self.parent_window or not hasattr(self.parent_window, 'sensor_system'):
            self.global_params_hint.setVisible(False)
            return
        gp = self.parent_window.sensor_system.global_parameters
        if not gp:
            self.global_params_hint.setText('[全局参数池为空]')
            self.global_params_hint.setVisible(True)
            return
        def _fmt(num):
            return f'{num:.2f}'
        items = ', '.join(
            f'{k}={_fmt(v.get("value", 0) if isinstance(v, dict) else v)}'
            for k, v in gp.items()
        )
        self.global_params_hint.setText(f'全局参数 (自动注入): {items}')
        self.global_params_hint.setVisible(True)

    def _on_type_changed(self, text):
        """当传感器类型改变时切换表单显示"""
        is_decoupling = text.startswith('decoupling')
        self.expr_label.setVisible(not is_decoupling)
        self.expr_input.setVisible(not is_decoupling)
        self.const_group.setVisible(not is_decoupling)
        self.decoupling_panel.setVisible(is_decoupling)

    def _on_formula_changed(self, text):
        """公式文本变化时刷新全局参数提示"""
        self._update_global_hint()

    def get_sensor(self):
        sensor_type = self.type_combo.currentText().split(':')[0]
        if sensor_type == 'decoupling':
            config = {
                'fbg1': self.fbg1_combo.currentText(),
                'fbg2': self.fbg2_combo.currentText(),
                'Ke1': self.ke1_input.value(),
                'Ke2': self.ke2_input.value(),
                'KT1': self.kt1_input.value(),
                'KT2': self.kt2_input.value(),
            }
            return Sensor(
                self.id_input.text(),
                sensor_type,
                formula='MatrixDecoupling',
                constants={},
                active=self.active_check.isChecked(),
                decoupling_config=config,
                location=self.location_input.text(),
            )
        else:
            return Sensor(
                self.id_input.text(),
                sensor_type,
                self.expr_input.text(),
                {},  # 常量由全局参数池统一注入，传感器不再持有局部常量
                self.active_check.isChecked(),
                location=self.location_input.text(),
            )

