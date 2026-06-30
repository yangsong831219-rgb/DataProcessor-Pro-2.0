"""数据文件标签页 — 文件操作与数据预览."""

from __future__ import annotations

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QPushButton, QLabel, QTableWidget,
    QTableWidgetItem,
)
from PyQt6.QtCore import pyqtSignal


class DataTabWidget(QWidget):
    """数据文件标签页 — 文件操作与数据预览."""

    open_file_requested = pyqtSignal()
    clear_data_requested = pyqtSignal()
    sample_data_requested = pyqtSignal()
    save_data_requested = pyqtSignal()
    save_template_requested = pyqtSignal()
    data_cell_edited = pyqtSignal(int, int, str)  # row, col, new_text

    def __init__(self, parent=None):
        super().__init__(parent)
        self._build_ui()
        self.data_table.itemChanged.connect(self._on_cell_changed)

    def _build_ui(self):
        layout = QVBoxLayout()

        # Button bar
        btn_layout = QHBoxLayout()
        self.open_file_btn = QPushButton("打开文件")
        self.open_file_btn.clicked.connect(self.open_file_requested.emit)
        btn_layout.addWidget(self.open_file_btn)

        self.clear_data_btn = QPushButton("清除数据")
        self.clear_data_btn.clicked.connect(self.clear_data_requested.emit)
        btn_layout.addWidget(self.clear_data_btn)

        self.sample_data_btn = QPushButton("抽取数据")
        self.sample_data_btn.clicked.connect(self.sample_data_requested.emit)
        btn_layout.addWidget(self.sample_data_btn)

        self.save_sampled_btn = QPushButton("保存数据")
        self.save_sampled_btn.clicked.connect(self.save_data_requested.emit)
        btn_layout.addWidget(self.save_sampled_btn)

        self.save_template_btn = QPushButton("保存模板")
        self.save_template_btn.clicked.connect(self.save_template_requested.emit)
        btn_layout.addWidget(self.save_template_btn)

        btn_layout.addStretch()
        layout.addLayout(btn_layout)

        # Row count info label
        self.data_info_label = QLabel("")
        self.data_info_label.setStyleSheet("color: #666; font-size: 12px; padding: 2px 0;")
        layout.addWidget(self.data_info_label)

        # Data table
        self.data_table = QTableWidget()
        self.data_table.setAlternatingRowColors(True)
        layout.addWidget(self.data_table)

        self.setLayout(layout)

    def update_data_table(self, df, max_display_rows=1000, annotation=None):
        """用 DataFrame 数据填充表格.

        Args:
            df: 数据 DataFrame（可能已含暗号行在 row 0）
            max_display_rows: 最大显示行数
            annotation: 可选暗号字典 {列名: 暗号字符串}，非空时 row 0 灰底斜体
        """
        if df is None:
            self.data_table.setRowCount(0)
            self.data_table.setColumnCount(0)
            self.data_info_label.setText("")
            return

        # 阻断 itemChanged 信号，防止批量填充时触发回写
        self.data_table.blockSignals(True)
        self.data_table.setUpdatesEnabled(False)

        total_rows = len(df)
        has_annot = bool(annotation)
        display_rows = min(total_rows, max_display_rows)
        cols = [str(c) for c in df.columns]

        self.data_table.setRowCount(display_rows)
        self.data_table.setColumnCount(len(cols))
        self.data_table.setHorizontalHeaderLabels(cols)

        for j, col_name in enumerate(cols):
            try:
                col_values = df[col_name].values[:display_rows]
            except KeyError:
                # 防御性回退：parse_file 可能未完全洗脱列名类型，
                # 尝试 int 转换或直接按列位置索引，确保绝不崩溃
                try:
                    col_values = df[int(col_name)].values[:display_rows]
                except (KeyError, ValueError, IndexError):
                    col_values = df.iloc[:, j].values[:display_rows]
            for i in range(display_rows):
                item = QTableWidgetItem(str(col_values[i]))
                self.data_table.setItem(i, j, item)

        # ── 暗号行视觉区分: row 0 灰底 + 斜体 ──
        if has_annot and display_rows > 0:
            from PyQt6.QtGui import QFont, QColor, QBrush
            italic_font = QFont()
            italic_font.setItalic(True)
            gray_bg = QBrush(QColor("#f0f0f0"))
            for j in range(len(cols)):
                item = self.data_table.item(0, j)
                if item is not None:
                    item.setFont(italic_font)
                    item.setBackground(gray_bg)

        self.data_table.setUpdatesEnabled(True)
        self.data_table.blockSignals(False)
        self.data_table.resizeColumnsToContents()

        # 行计数不含暗号行
        data_rows = total_rows - 1 if has_annot else total_rows
        if total_rows > max_display_rows:
            self.data_info_label.setText(
                f"显示前 {max_display_rows:,} 行 / 共 {data_rows:,} 行"
            )
        else:
            self.data_info_label.setText(f"共 {data_rows:,} 行")

    def _on_cell_changed(self, item):
        """用户编辑表格单元格后，发射信号让 main.py 同步回 DataFrame."""
        row = item.row()
        col = item.column()
        text = item.text()
        self.data_cell_edited.emit(row, col, text)
