"""传感器配置标签页 — FBG 和传感器 CRUD、计算与导出."""

from __future__ import annotations

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QPushButton, QLabel,
    QGroupBox, QSpinBox, QTableWidget, QTableWidgetItem,
    QLineEdit, QComboBox,
)
from PyQt6.QtCore import pyqtSignal


class SensorTabWidget(QWidget):
    """传感器配置标签页 — FBG 定义 + 传感器定义 + 计算预览."""

    fbg_add_requested = pyqtSignal()
    fbg_edit_requested = pyqtSignal(int)       # row index
    fbg_delete_requested = pyqtSignal(int)     # row index

    sensor_add_requested = pyqtSignal()
    sensor_edit_requested = pyqtSignal(int)    # row index
    sensor_delete_requested = pyqtSignal(int)  # row index
    sensor_copy_requested = pyqtSignal(int)    # row index

    calculate_requested = pyqtSignal(int)      # ref_row
    save_sensor_requested = pyqtSignal(str, str)  # filename, format
    export_sensor_requested = pyqtSignal()
    global_params_requested = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._build_ui()

    def _build_ui(self):
        layout = QVBoxLayout()

        info_label = QLabel("配置FBG光纤光栅和传感器参数")
        layout.addWidget(info_label)

        # ── Reference row ──
        ref_layout = QHBoxLayout()
        ref_layout.addWidget(QLabel("初始值行 (参考行):"))
        self.ref_row_spin = QSpinBox()
        self.ref_row_spin.setRange(0, 9999)
        self.ref_row_spin.setValue(0)
        self.ref_row_spin.setPrefix("第 ")
        self.ref_row_spin.setSuffix(" 行")
        ref_layout.addWidget(self.ref_row_spin)
        ref_layout.addWidget(QLabel("(波长减初始值再乘系数得到物理量)"))
        ref_layout.addStretch()
        layout.addLayout(ref_layout)

        # ── FBG + Sensor horizontal split ──
        h_layout = QHBoxLayout()

        # ── Left: FBG definition ──
        fbg_group = QGroupBox("FBG定义 (光纤光栅)")
        fbg_layout = QVBoxLayout()

        self.fbg_table = QTableWidget()
        self.fbg_table.setColumnCount(4)
        self.fbg_table.setHorizontalHeaderLabels(["ID", "波长列", "波长下限", "波长上限"])
        fbg_layout.addWidget(self.fbg_table)

        fbg_btn_layout = QHBoxLayout()
        self.add_fbg_btn = QPushButton("添加FBG")
        self.add_fbg_btn.clicked.connect(lambda: self.fbg_add_requested.emit())
        fbg_btn_layout.addWidget(self.add_fbg_btn)

        self.edit_fbg_btn = QPushButton("编辑FBG")
        self.edit_fbg_btn.clicked.connect(self._on_edit_fbg)
        fbg_btn_layout.addWidget(self.edit_fbg_btn)

        self.delete_fbg_btn = QPushButton("删除FBG")
        self.delete_fbg_btn.clicked.connect(self._on_delete_fbg)
        fbg_btn_layout.addWidget(self.delete_fbg_btn)

        fbg_layout.addLayout(fbg_btn_layout)
        fbg_group.setLayout(fbg_layout)
        h_layout.addWidget(fbg_group)

        # ── Right: Sensor definition ──
        sensor_group = QGroupBox("传感器定义")
        sensor_layout = QVBoxLayout()

        self.sensor_table = QTableWidget()
        self.sensor_table.setColumnCount(6)
        self.sensor_table.setHorizontalHeaderLabels(["ID", "物理位置", "类型", "公式", "常量", "启用"])
        sensor_layout.addWidget(self.sensor_table)

        sensor_btn_layout = QHBoxLayout()
        self.add_sensor_btn = QPushButton("添加传感器")
        self.add_sensor_btn.clicked.connect(lambda: self.sensor_add_requested.emit())
        sensor_btn_layout.addWidget(self.add_sensor_btn)

        self.edit_sensor_btn = QPushButton("编辑传感器")
        self.edit_sensor_btn.clicked.connect(self._on_edit_sensor)
        sensor_btn_layout.addWidget(self.edit_sensor_btn)

        self.delete_sensor_btn = QPushButton("删除传感器")
        self.delete_sensor_btn.clicked.connect(self._on_delete_sensor)
        sensor_btn_layout.addWidget(self.delete_sensor_btn)

        self.copy_sensor_btn = QPushButton("复制传感器")
        self.copy_sensor_btn.clicked.connect(self._on_copy_sensor)
        sensor_btn_layout.addWidget(self.copy_sensor_btn)

        sensor_btn_layout.addStretch()

        self.global_params_btn = QPushButton("⚙️ 全局参数与变量池")
        self.global_params_btn.setStyleSheet("""
            QPushButton {
                background: #722ed1; color: white; border: none; border-radius: 4px;
                padding: 8px 18px; font-size: 13px; font-weight: bold;
            }
            QPushButton:hover { background: #9254de; }
        """)
        self.global_params_btn.clicked.connect(lambda: self.global_params_requested.emit())
        sensor_btn_layout.addWidget(self.global_params_btn)

        sensor_layout.addLayout(sensor_btn_layout)
        sensor_group.setLayout(sensor_layout)
        h_layout.addWidget(sensor_group)

        layout.addLayout(h_layout)

        # ── Calculate / Export buttons ──
        calc_layout = QHBoxLayout()
        self.calculate_sensors_btn = QPushButton("计算所有传感器")
        self.calculate_sensors_btn.clicked.connect(self._on_calculate)
        calc_layout.addWidget(self.calculate_sensors_btn)

        self.export_sensor_data_btn = QPushButton("导出传感器数据")
        self.export_sensor_data_btn.clicked.connect(self.export_sensor_requested.emit)
        calc_layout.addWidget(self.export_sensor_data_btn)

        layout.addLayout(calc_layout)

        # ── Result preview ──
        layout.addWidget(QLabel("传感器数据预览:"))
        self.sensor_result_table = QTableWidget()
        self.sensor_result_table.setMaximumHeight(200)
        layout.addWidget(self.sensor_result_table)

        # ── Save row ──
        save_layout = QHBoxLayout()
        save_layout.addWidget(QLabel("文件名:"))
        self.sensor_filename_input = QLineEdit()
        self.sensor_filename_input.setText("sensor_data")
        save_layout.addWidget(self.sensor_filename_input)

        self.sensor_format_combo = QComboBox()
        self.sensor_format_combo.addItems(["csv", "txt", "xlsx"])
        save_layout.addWidget(self.sensor_format_combo)

        self.save_sensor_btn = QPushButton("保存传感器数据")
        self.save_sensor_btn.clicked.connect(self._on_save_sensor)
        save_layout.addWidget(self.save_sensor_btn)

        save_layout.addStretch()
        layout.addLayout(save_layout)

        layout.addStretch()
        self.setLayout(layout)

    # ── Internal signal relays ──

    def _on_edit_fbg(self):
        row = self.fbg_table.currentRow()
        if row >= 0:
            self.fbg_edit_requested.emit(row)

    def _on_delete_fbg(self):
        row = self.fbg_table.currentRow()
        if row >= 0:
            self.fbg_delete_requested.emit(row)

    def _on_edit_sensor(self):
        row = self.sensor_table.currentRow()
        if row >= 0:
            self.sensor_edit_requested.emit(row)

    def _on_delete_sensor(self):
        row = self.sensor_table.currentRow()
        if row >= 0:
            self.sensor_delete_requested.emit(row)

    def _on_copy_sensor(self):
        row = self.sensor_table.currentRow()
        if row >= 0:
            self.sensor_copy_requested.emit(row)

    def _on_calculate(self):
        self.calculate_requested.emit(self.ref_row_spin.value())

    def _on_save_sensor(self):
        name = self.sensor_filename_input.text().strip()
        fmt = self.sensor_format_combo.currentText()
        self.save_sensor_requested.emit(name, fmt)

    # ── Public setters ──

    def set_fbg_list(self, fbgs):
        """用 FBG 列表填充 FBG 表格."""
        self.fbg_table.setRowCount(len(fbgs))
        for i, fbg in enumerate(fbgs):
            self.fbg_table.setItem(i, 0, QTableWidgetItem(fbg.id))
            self.fbg_table.setItem(i, 1, QTableWidgetItem(fbg.channel))
            self.fbg_table.setItem(i, 2, QTableWidgetItem(str(fbg.wavelength_min or "")))
            self.fbg_table.setItem(i, 3, QTableWidgetItem(str(fbg.wavelength_max or "")))
        self.fbg_table.resizeColumnsToContents()

    def set_sensor_list(self, sensors):
        """用传感器列表填充传感器表格."""
        self.sensor_table.setRowCount(len(sensors))
        for i, sensor in enumerate(sensors):
            self.sensor_table.setItem(i, 0, QTableWidgetItem(sensor.id))
            self.sensor_table.setItem(i, 1, QTableWidgetItem(sensor.location))
            self.sensor_table.setItem(i, 2, QTableWidgetItem(sensor.get_name()))
            # formula column
            if sensor.sensor_type == "decoupling" and sensor.decoupling_config:
                cfg = sensor.decoupling_config
                formula_str = f"矩阵解耦: {cfg['fbg1']}/{cfg['fbg2']}"
            else:
                formula_str = sensor.formula
            self.sensor_table.setItem(i, 3, QTableWidgetItem(formula_str))
            # constants column
            if sensor.sensor_type == "decoupling" and sensor.decoupling_config:
                cfg = sensor.decoupling_config
                const_str = (
                    f"Ke1={cfg['Ke1']:.2f}, Ke2={cfg['Ke2']:.2f}, "
                    f"KT1={cfg['KT1']:.2f}, KT2={cfg['KT2']:.2f}"
                )
            else:
                const_str = ", ".join(f"{k}={v:.2f}" for k, v in sensor.constants.items())
            self.sensor_table.setItem(i, 4, QTableWidgetItem(const_str))
            self.sensor_table.setItem(i, 5, QTableWidgetItem("是" if sensor.active else "否"))
        self.sensor_table.resizeColumnsToContents()

    def set_result_preview(self, results, df, max_rows=100):
        """用计算结果填充传感器数据预览表."""
        if df is None:
            return
        n_sensors = len(results)
        n_preview_rows = min(max_rows, len(df))

        self.sensor_result_table.setColumnCount(n_sensors + 1)
        self.sensor_result_table.setRowCount(n_preview_rows)

        time_col = "时间" if "时间" in df.columns else df.columns[0]
        headers = ["时间"] + list(results.keys())
        self.sensor_result_table.setHorizontalHeaderLabels(headers)

        for i in range(n_preview_rows):
            t = df[time_col].values[i]
            self.sensor_result_table.setItem(i, 0, QTableWidgetItem(str(t)))
            for j, sensor_id in enumerate(results.keys()):
                vals = results[sensor_id]
                val = vals[i] if i < len(vals) else None
                if val is not None:
                    self.sensor_result_table.setItem(i, j + 1, QTableWidgetItem(f"{val:.2f}"))
                else:
                    self.sensor_result_table.setItem(i, j + 1, QTableWidgetItem("N/A"))

        self.sensor_result_table.resizeColumnsToContents()
