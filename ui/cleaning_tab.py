"""数据清洗标签页 — 异常检测与填充配置."""

from __future__ import annotations

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QPushButton, QLabel,
    QGroupBox, QCheckBox, QDoubleSpinBox, QComboBox, QTextEdit,
)
from PyQt6.QtCore import pyqtSignal


class CleaningTabWidget(QWidget):
    """数据清洗标签页 — 异常检测规则与填充方式配置."""

    apply_cleaning_requested = pyqtSignal(dict)  # cleaning config dict

    def __init__(self, parent=None):
        super().__init__(parent)
        self._build_ui()

    def _build_ui(self):
        layout = QVBoxLayout()

        info_label = QLabel("配置数据清洗规则，然后点击\"应用清洗\"按钮处理数据")
        layout.addWidget(info_label)

        # ── Rule 1: Adjacent Difference Detection ──
        rule1_group = QGroupBox("规则1: 相邻数据差值检测")
        rule1_layout = QVBoxLayout()

        self.adjacent_enabled = QCheckBox("启用相邻数据差值检测")
        self.adjacent_enabled.setChecked(True)
        rule1_layout.addWidget(self.adjacent_enabled)

        adj_layout = QHBoxLayout()
        adj_layout.addWidget(QLabel("差值阈值:"))
        self.diff_threshold = QDoubleSpinBox()
        self.diff_threshold.setRange(0, 1e9)
        self.diff_threshold.setValue(1.0)
        self.diff_threshold.setDecimals(4)
        adj_layout.addWidget(self.diff_threshold)
        adj_layout.addWidget(QLabel("(超过此值判断为异常)"))
        adj_layout.addStretch()
        rule1_layout.addLayout(adj_layout)
        rule1_group.setLayout(rule1_layout)
        layout.addWidget(rule1_group)

        # ── Rule 2: NaN Detection ──
        rule2_group = QGroupBox("规则2: 缺失值检测")
        rule2_layout = QVBoxLayout()

        self.nan_enabled = QCheckBox("启用缺失值(NaN)检测")
        self.nan_enabled.setChecked(True)
        rule2_layout.addWidget(self.nan_enabled)

        nan_layout = QHBoxLayout()
        nan_layout.addWidget(QLabel("(数据为空或NaN时判断为异常)"))
        nan_layout.addStretch()
        rule2_layout.addLayout(nan_layout)
        rule2_group.setLayout(rule2_layout)
        layout.addWidget(rule2_group)

        # ── Fill Method ──
        fill_group = QGroupBox("填充方式")
        fill_layout = QHBoxLayout()

        fill_layout.addWidget(QLabel("选择填充方式:"))
        self.fill_method_combo = QComboBox()
        self.fill_method_combo.addItems(["linear", "mean", "forward", "backward", "custom"])
        fill_layout.addWidget(self.fill_method_combo)

        self.custom_fill_value = QDoubleSpinBox()
        self.custom_fill_value.setRange(-1e9, 1e9)
        self.custom_fill_value.setValue(0)
        self.custom_fill_value.setDecimals(4)
        fill_layout.addWidget(QLabel("自定义值:"))
        fill_layout.addWidget(self.custom_fill_value)
        fill_layout.addStretch()
        fill_group.setLayout(fill_layout)
        layout.addWidget(fill_group)

        # ── Apply button ──
        apply_btn = QPushButton("应用清洗")
        apply_btn.clicked.connect(self._on_apply)
        layout.addWidget(apply_btn)

        # ── Result display ──
        self.cleaning_result = QTextEdit()
        self.cleaning_result.setReadOnly(True)
        self.cleaning_result.setMaximumHeight(100)
        layout.addWidget(QLabel("异常检测结果:"))
        layout.addWidget(self.cleaning_result)

        layout.addStretch()
        self.setLayout(layout)

    def _on_apply(self):
        config = self.get_config()
        self.apply_cleaning_requested.emit(config)

    def get_config(self) -> dict:
        """读取当前 UI 中的清洗参数."""
        return {
            "adjacent_enabled": self.adjacent_enabled.isChecked(),
            "diff_threshold": self.diff_threshold.value(),
            "nan_enabled": self.nan_enabled.isChecked(),
            "fill_method": self.fill_method_combo.currentText(),
            "fill_value": self.custom_fill_value.value(),
        }

    def set_result_text(self, text: str):
        self.cleaning_result.setText(text)

    def set_config(self, config: dict):
        """从字典恢复 UI 状态（用于加载配置）."""
        self.adjacent_enabled.setChecked(config.get('adjacent_enabled', True))
        self.diff_threshold.setValue(config.get('diff_threshold', 1.0))
        self.nan_enabled.setChecked(config.get('nan_enabled', True))
        idx = self.fill_method_combo.findText(config.get('fill_method', 'linear'))
        if idx >= 0:
            self.fill_method_combo.setCurrentIndex(idx)
        self.custom_fill_value.setValue(config.get('fill_value', config.get('custom_fill_value', 0)))
