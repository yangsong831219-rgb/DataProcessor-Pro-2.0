"""FBG 编辑对话框"""

from __future__ import annotations

from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit,
    QComboBox, QDoubleSpinBox, QPushButton,
)

from core.models import FBG


class FBGEditDialog(QDialog):
    """FBG编辑对话框"""
    def __init__(self, parent=None, fbg=None):
        super().__init__(parent)
        self.setWindowTitle('编辑FBG' if fbg else '添加FBG')
        self.setMinimumWidth(400)
        self.fbg = fbg

        layout = QVBoxLayout()

        # FBG ID
        id_layout = QHBoxLayout()
        id_layout.addWidget(QLabel('FBG ID:'))
        self.id_input = QLineEdit()
        if fbg:
            self.id_input.setText(fbg.id)
        id_layout.addWidget(self.id_input)
        layout.addLayout(id_layout)

        # 波长列
        channel_layout = QHBoxLayout()
        channel_layout.addWidget(QLabel('波长列:'))
        self.channel_input = QComboBox()
        self.channel_input.addItems([f'波长{i}' for i in range(1, 9)])
        if fbg:
            self.channel_input.setCurrentText(fbg.channel)
        channel_layout.addWidget(self.channel_input)
        layout.addLayout(channel_layout)

        # 波长范围
        range_layout = QHBoxLayout()
        range_layout.addWidget(QLabel('波长下限:'))
        self.wl_min_input = QDoubleSpinBox()
        self.wl_min_input.setRange(0, 10000)
        self.wl_min_input.setDecimals(3)
        self.wl_min_input.setValue(fbg.wavelength_min if fbg else 1520.0)
        range_layout.addWidget(self.wl_min_input)

        range_layout.addWidget(QLabel('波长上限:'))
        self.wl_max_input = QDoubleSpinBox()
        self.wl_max_input.setRange(0, 10000)
        self.wl_max_input.setDecimals(3)
        self.wl_max_input.setValue(fbg.wavelength_max if fbg else 1590.0)
        range_layout.addWidget(self.wl_max_input)
        layout.addLayout(range_layout)

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

    def get_fbg(self):
        return FBG(
            self.id_input.text(),
            self.channel_input.currentText(),
            self.wl_min_input.value(),
            self.wl_max_input.value()
        )

