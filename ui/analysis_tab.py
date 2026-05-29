"""数据分析选项卡 — 从 main.py DataProcessorWindow 抽离的独立 QWidget

包含：图表显示、统计信息、曲线对比、数据源/范围选择
通过 parent() 链访问主窗口的 current_data、sensor_system、sensor_results。
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import re as _re
import numpy as np
import pandas as pd
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QGridLayout, QGroupBox,
    QPushButton, QLabel, QComboBox, QSpinBox, QListWidget, QListWidgetItem,
    QTextEdit, QFileDialog, QMessageBox, QDialog,
)
from PyQt6.QtCore import Qt, pyqtSignal
from matplotlib.figure import Figure
from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg
from matplotlib.backends.backend_qt import NavigationToolbar2QT

if TYPE_CHECKING:
    from typing import Optional


class AnalysisTabWidget(QWidget):
    """数据分析选项卡 — 图表、统计、曲线对比"""

    status_message_requested = pyqtSignal(str)
    data_refresh_requested = pyqtSignal()

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._current_data = None
        self._annotated_cols = None  # 有暗号的列名集合
        self._sensor_results = {}
        self._sensor_system = None
        self._annotation_mode = False  # 标注数据（其它数据）使用物理量路径
        self._setup_ui()
        self.analysis_sensor_list.itemChanged.connect(self._on_sensor_item_changed)

    # ── UI 构建 ──

    def _setup_ui(self) -> None:
        layout = QVBoxLayout(self)

        # ============ 数据源选择 ============
        source_layout = QHBoxLayout()
        source_layout.addWidget(QLabel('数据源:'))
        self.data_source_combo = QComboBox()
        self.data_source_combo.addItems(['原始数据', '物理量'])
        self.data_source_combo.currentTextChanged.connect(self._on_data_source_changed)
        source_layout.addWidget(self.data_source_combo)

        self.analysis_sensor_list = QListWidget()
        self.analysis_sensor_list.setEnabled(False)
        self.analysis_sensor_list.setMaximumHeight(120)
        self.analysis_sensor_list.setSelectionMode(QListWidget.SelectionMode.NoSelection)
        source_layout.addWidget(self.analysis_sensor_list)

        refresh_btn = QPushButton('刷新')
        refresh_btn.clicked.connect(self._on_refresh_clicked)
        source_layout.addWidget(refresh_btn)

        source_layout.addStretch()
        layout.addLayout(source_layout)

        # ============ 数据范围选择 ============
        range_layout = QHBoxLayout()
        range_layout.addWidget(QLabel('数据范围:'))

        self.range_type_combo = QComboBox()
        self.range_type_combo.addItems(['序号范围', '时间范围'])
        range_layout.addWidget(self.range_type_combo)

        self.range_start = QSpinBox()
        self.range_start.setPrefix('从 ')
        self.range_start.setSuffix(' 起')
        self.range_start.setRange(0, 999999)
        self.range_start.setValue(0)
        range_layout.addWidget(self.range_start)

        self.range_end = QSpinBox()
        self.range_end.setPrefix('到 ')
        self.range_end.setSuffix(' 止')
        self.range_end.setRange(1, 999999)
        self.range_end.setValue(999999)
        range_layout.addWidget(self.range_end)

        apply_btn = QPushButton('应用范围')
        apply_btn.clicked.connect(self.run_analysis)
        range_layout.addWidget(apply_btn)

        range_layout.addStretch()
        layout.addLayout(range_layout)

        # ============ 图表显示区域 ============
        self.chart_figure = Figure(figsize=(8, 4))
        self.chart_canvas = FigureCanvasQTAgg(self.chart_figure)

        self.chart_container = QWidget()
        self.chart_container_layout = QVBoxLayout(self.chart_container)
        self.chart_container.setMinimumHeight(300)

        # 工具栏行：Matplotlib 工具栏 + 弹性空间 + 全屏按钮
        toolbar_row = QHBoxLayout()
        self.chart_toolbar = NavigationToolbar2QT(self.chart_canvas, self.chart_container)
        self.chart_toolbar.setWindowTitle('图表工具')
        toolbar_row.addWidget(self.chart_toolbar)
        toolbar_row.addStretch()

        self.fullscreen_btn = QPushButton('⛶ 全屏查看')
        self.fullscreen_btn.setStyleSheet(
            "QPushButton { color: #1890ff; border: 1px solid #1890ff;"
            " border-radius: 3px; padding: 4px 12px; }"
            "QPushButton:hover { background-color: #e6f7ff; }"
        )
        self.fullscreen_btn.clicked.connect(self._toggle_fullscreen)
        toolbar_row.addWidget(self.fullscreen_btn)

        self.chart_container_layout.addLayout(toolbar_row)
        self.chart_container_layout.addWidget(self.chart_canvas)

        self.ax = self.chart_figure.add_subplot(111)
        layout.addWidget(self.chart_container)

        # ============ 统计信息显示 ============
        stats_group = QGroupBox('统计信息')
        stats_layout = QVBoxLayout()

        select_layout = QHBoxLayout()
        select_layout.addWidget(QLabel('选择曲线:'))
        self.stats_curve_combo = QComboBox()
        self.stats_curve_combo.currentTextChanged.connect(self._on_stats_curve_changed)
        select_layout.addWidget(self.stats_curve_combo)
        select_layout.addStretch()
        stats_layout.addLayout(select_layout)

        stats_grid = QGridLayout()
        self.stats_labels = {}
        stats_items = [
            ('最大值', 'max'), ('最小值', 'min'), ('平均值', 'mean'), ('标准差', 'std'),
            ('峰峰值', 'peak_to_peak'), ('有效值', 'rms'), ('偏度', 'skew'), ('峰度', 'kurtosis'),
        ]
        for row, (label, key) in enumerate(stats_items):
            lbl = QLabel(f'<b>{label}:</b> --')
            lbl.setStyleSheet('color: #333; padding: 2px;')
            stats_grid.addWidget(lbl, row // 4, row % 4)
            self.stats_labels[key] = lbl
        stats_layout.addLayout(stats_grid)

        # 曲线对比区域
        compare_group = QGroupBox('曲线对比')
        compare_layout = QVBoxLayout()

        compare_select = QHBoxLayout()
        compare_select.addWidget(QLabel('曲线1:'))
        self.compare_combo1 = QComboBox()
        compare_select.addWidget(self.compare_combo1)
        compare_select.addWidget(QLabel('曲线2:'))
        self.compare_combo2 = QComboBox()
        compare_select.addWidget(self.compare_combo2)
        compare_layout.addLayout(compare_select)

        self.compare_btn = QPushButton('对比统计')
        self.compare_btn.clicked.connect(self._compare_curves)
        compare_layout.addWidget(self.compare_btn)

        self.compare_result = QTextEdit()
        self.compare_result.setReadOnly(True)
        self.compare_result.setMaximumHeight(80)
        self.compare_result.setPlaceholderText('对比结果将显示在这里...')
        compare_layout.addWidget(self.compare_result)

        compare_group.setLayout(compare_layout)
        stats_layout.addWidget(compare_group)

        stats_group.setLayout(stats_layout)
        layout.addWidget(stats_group)

        # ============ 分析操作按钮 ============
        btn_layout = QHBoxLayout()
        self.run_analysis_btn = QPushButton('执行分析')
        self.run_analysis_btn.clicked.connect(self.run_analysis)
        btn_layout.addWidget(self.run_analysis_btn)

        self.export_chart_btn = QPushButton('导出图表')
        self.export_chart_btn.clicked.connect(self._export_chart)
        btn_layout.addWidget(self.export_chart_btn)

        btn_layout.addStretch()
        layout.addLayout(btn_layout)

        layout.addStretch()

    # ── 依赖注入 (由主窗口调用) ──

    def set_current_data(self, df, annotated_cols=None):
        self._current_data = df
        self._annotated_cols = annotated_cols
        if annotated_cols:
            # 标注数据 → 切换到物理量路径，显示列名为传感器
            self._annotation_mode = True
            self.data_source_combo.setCurrentText('物理量')
        else:
            self._annotation_mode = False
            # 原始数据模式下自动刷新数据列列表
            if self.data_source_combo.currentText() == '原始数据':
                self._refresh_data_column_list()

    def set_sensor_system(self, system):
        self._sensor_system = system

    def set_sensor_results(self, results: dict):
        self._sensor_results = results
        # 物理量模式下立即刷新传感器列表
        if self.data_source_combo.currentText() == '物理量':
            self.refresh_analysis_sensors()

    # ── 传感器信息辅助 ──

    @staticmethod
    def _parse_result_key(sensor_id: str):
        """从传感器结果键中提取 (显示名称, 单位)。

        处理两种格式：
        - 普通传感器: sensor_id 直接查询 Sensor 对象
        - 解耦传感器: "S1_应变(με)" → ("应变", "με")
        """
        import re
        m = re.match(r'^(.+)_(应变|温度)\((.+)\)$', sensor_id)
        if m:
            return m.group(2), m.group(3)
        return sensor_id, ''

    def _get_sensor_unit(self, sensor_id: str) -> str:
        display, unit = self._parse_result_key(sensor_id)
        if unit:
            return unit
        ss = self._sensor_system
        if ss:
            for sensor in ss.sensors:
                if sensor.id == sensor_id:
                    return sensor.get_unit()
        return ''

    def _get_sensor_display_name(self, sensor_id: str) -> str:
        display, unit = self._parse_result_key(sensor_id)
        if unit:
            return display
        ss = self._sensor_system
        if ss:
            for sensor in ss.sensors:
                if sensor.id == sensor_id:
                    return sensor.get_name()
        return sensor_id

    # ── 图表绘制 ──

    @staticmethod
    def _format_time(t) -> str:
        if hasattr(t, 'strftime'):
            return t.strftime('%H:%M:%S')
        elif hasattr(t, 'item'):
            try:
                return pd.Timestamp(t).strftime('%H:%M:%S')
            except Exception:
                pass
        elif isinstance(t, str):
            if ' ' in t:
                t = t.split(' ')[-1]
            if '.' in t:
                t = t.split('.')[0]
            return t
        return str(t)

    def _update_chart_with_data(self, data, time_data, unit, sensor_id=None):
        self.ax.clear()
        max_points = 1000
        plot_data = list(data)
        plot_time = list(time_data) if time_data else []

        if len(plot_data) > max_points:
            indices = np.linspace(0, len(plot_data) - 1, max_points, dtype=int)
            plot_data = [plot_data[i] for i in indices]
            if plot_time and len(plot_time) == len(data):
                plot_time = [plot_time[i] for i in indices]

        if plot_time and len(plot_time) == len(plot_data):
            self.ax.plot(plot_time, plot_data, 'b-', linewidth=1)
            self.ax.set_xlabel('时间')
            if len(plot_time) > 10:
                tick_indices = np.linspace(0, len(plot_time) - 1, 10, dtype=int)
                self.ax.set_xticks([plot_time[i] for i in tick_indices])
                self.ax.set_xticklabels([self._format_time(plot_time[i]) for i in tick_indices],
                                        rotation=45, ha='right')
            if plot_time:
                self.ax.set_xlim([plot_time[0], plot_time[-1]])
        else:
            self.ax.plot(plot_data, 'b-', linewidth=1)
            self.ax.set_xlabel('序号')

        self.ax.set_ylabel(unit if unit else '物理量')
        self.ax.grid(True, alpha=0.3)
        title = f'{sensor_id} 物理量' if sensor_id else '数据曲线'
        self.ax.set_title(title)
        self.chart_figure.tight_layout()
        self.chart_canvas.draw()

    @staticmethod
    def _clean_col_name(name: str) -> str:
        """移除列名中的数字，保留中文和字母。"""
        return _re.sub(r'\d+', '', name).strip()

    def _update_chart_multi_columns(self, data_cols, time_data):
        self.ax.clear()
        max_points = 1000
        current_data = self._current_data
        if current_data is None:
            return

        start_idx = self.range_start.value()
        end_idx = min(self.range_end.value(), len(current_data) - 1)

        if time_data:
            time_range = list(time_data)[start_idx:end_idx + 1]
        else:
            time_range = list(range(start_idx, end_idx + 1))

        if len(time_range) > 10:
            tick_indices = np.linspace(0, len(time_range) - 1, 10, dtype=int)
            tick_labels = [self._format_time(time_range[i]) for i in tick_indices]
        else:
            tick_labels = None

        # 清理列名（移除数字），用于标题/Y轴
        display_name = self._clean_col_name(data_cols[0]) if data_cols else '数据'

        colors = ['b-', 'g-', 'r-', 'c-', 'm-', 'y-', 'k-', 'orange']
        for i, col in enumerate(data_cols):
            col_data = current_data[col].values[start_idx:end_idx + 1]

            ref_value = None
            for v in col_data:
                if pd.notna(v):
                    ref_value = float(v)
                    break

            if ref_value is not None:
                plot_data = [float(v) - ref_value if pd.notna(v) else None
                            for v in col_data]
            else:
                plot_data = list(col_data)

            if len(plot_data) > max_points:
                indices = np.linspace(0, len(plot_data) - 1, max_points, dtype=int)
                plot_data_sampled = [plot_data[j] for j in indices]
                plot_time = [time_range[j] for j in indices]
            else:
                plot_data_sampled = plot_data
                plot_time = time_range

            color = colors[i % len(colors)]
            self.ax.plot(plot_time, plot_data_sampled, color, linewidth=1,
                         label=col)

        if time_range:
            self.ax.set_xlim([time_range[0], time_range[-1]])

        self.ax.set_xlabel('时间')
        if tick_labels:
            tick_positions = [time_range[i] for i in tick_indices]
            self.ax.set_xticks(tick_positions)
            self.ax.set_xticklabels(tick_labels, rotation=45, ha='right')

        # Y轴标记和图表名称 —— 使用清理后的列名
        self.ax.set_ylabel(f'{display_name}（με）')
        self.ax.grid(True, alpha=0.3)
        self.ax.legend(loc='upper right', fontsize=8)
        self.ax.set_title(f'{display_name}时程曲线')
        self.chart_figure.tight_layout()
        self.chart_canvas.draw()

    def _update_chart_multi_sensors(self, sensor_ids, time_range):
        self.ax.clear()
        max_points = 1000
        colors = ['b-', 'g-', 'r-', 'c-', 'm-', 'y-', 'k-', 'orange']
        sensor_results = self._sensor_results

        for i, sensor_id in enumerate(sensor_ids):
            if sensor_id not in sensor_results:
                continue

            data = list(sensor_results[sensor_id])
            start_idx = self.range_start.value()
            end_idx = min(self.range_end.value(), len(data) - 1)
            data_range = data[start_idx:end_idx + 1]

            if len(data_range) > max_points:
                indices = np.linspace(0, len(data_range) - 1, max_points, dtype=int)
                plot_data = [data_range[j] for j in indices]
                plot_time = [time_range[j] for j in indices] if time_range else list(range(len(data_range)))
            else:
                plot_data = data_range
                plot_time = time_range if time_range else list(range(len(data_range)))

            color = colors[i % len(colors)]
            self.ax.plot(plot_time, plot_data, color, linewidth=1, label=sensor_id)

        if time_range:
            self.ax.set_xlim([time_range[0], time_range[-1]])
            if len(time_range) > 10:
                tick_indices = np.linspace(0, len(time_range) - 1, 10, dtype=int)
                tick_positions = [time_range[j] for j in tick_indices]
                tick_labels = [self._format_time(time_range[j]) for j in tick_indices]
                self.ax.set_xticks(tick_positions)
                self.ax.set_xticklabels(tick_labels, rotation=45, ha='right')
            elif len(time_range) > 0:
                tick_labels = [self._format_time(t) for t in time_range]
                self.ax.set_xticks(range(len(time_range)))
                self.ax.set_xticklabels(tick_labels, rotation=45, ha='right')

        if sensor_ids:
            if self._annotation_mode:
                # 其它数据：使用暗号名称，移除数字，单位 με
                display_name = self._clean_col_name(sensor_ids[0])
                self.ax.set_ylabel(f'{display_name}（με）')
                self.ax.set_title(f'{display_name}时程曲线')
            else:
                first_id = sensor_ids[0]
                unit = self._get_sensor_unit(first_id)
                display_name = self._get_sensor_display_name(first_id)
                self.ax.set_ylabel(f'{display_name} ({unit})')
                self.ax.set_title(f'{display_name}时程曲线')

        self.ax.set_xlabel('时间')
        self.ax.grid(True, alpha=0.3)
        self.ax.legend(loc='upper right', fontsize=8)
        self.chart_figure.tight_layout()
        self.chart_canvas.draw()

    # ── 全屏查看 ──

    def _toggle_fullscreen(self):
        """将图表移动到一个全屏 QDialog 中查看，关闭时自动恢复原位。"""
        if getattr(self, '_fullscreen_dialog', None) is not None:
            return  # 已有全屏窗口，防止重复
        dialog = QDialog(self)
        dialog.setWindowTitle('图表全屏查看')
        dialog.setWindowFlags(
            Qt.WindowType.Window | Qt.WindowType.MaximizeUsingFullscreenGeometryHint
        )
        dialog.setLayout(QVBoxLayout(dialog))
        self._fullscreen_dialog = dialog  # 防止 GC

        # 将工具栏和画布移入对话框
        self.chart_toolbar.setParent(dialog)
        self.chart_canvas.setParent(dialog)
        dialog.layout().addWidget(self.chart_toolbar)
        dialog.layout().addWidget(self.chart_canvas)

        self.fullscreen_btn.setVisible(False)

        # 退出全屏按钮
        exit_btn = QPushButton('退出全屏')
        exit_btn.setStyleSheet(
            "QPushButton { color: #ff4d4f; border: 1px solid #ff4d4f;"
            " border-radius: 3px; padding: 6px 16px; font-size: 13px; }"
            "QPushButton:hover { background-color: #fff2f0; }"
        )
        exit_btn.clicked.connect(dialog.close)
        dialog.layout().addWidget(exit_btn)

        def restore():
            """对话框关闭时，将控件移回原始容器。"""
            self.chart_toolbar.setParent(self.chart_container)
            self.chart_canvas.setParent(self.chart_container)
            # 将工具栏放回 toolbar_row（首位），画布追加到容器末尾
            toolbar_row = self.chart_container_layout.itemAt(0)
            if toolbar_row and toolbar_row.layout():
                toolbar_row.layout().insertWidget(0, self.chart_toolbar)
            self.chart_container_layout.addWidget(self.chart_canvas)
            self.fullscreen_btn.setVisible(True)
            self._fullscreen_dialog = None

        dialog.finished.connect(restore)
        dialog.showMaximized()

    # ── 统计计算 ──

    def _calculate_statistics(self, data, sensor_id=None):
        arr = np.array(data, dtype=np.float64)
        arr = arr[~np.isnan(arr)]
        if len(arr) == 0:
            return

        stats = {
            'max': np.max(arr), 'min': np.min(arr), 'mean': np.mean(arr),
            'std': np.std(arr), 'peak_to_peak': np.max(arr) - np.min(arr),
            'rms': np.sqrt(np.mean(arr ** 2)),
            'skew': float(np.mean(((arr - arr.mean()) / arr.std()) ** 3)) if arr.std() > 0 else 0,
            'kurtosis': float(np.mean(((arr - arr.mean()) / arr.std()) ** 4)) if arr.std() > 0 else 0,
        }
        for key, value in stats.items():
            self.stats_labels[key].setText(f'{key}: {value:.4f}')

    def _calculate_statistics_for_sensors(self, sensor_ids, start_idx, end_idx):
        if not sensor_ids:
            return
        sensor_results = self._sensor_results

        self.stats_curve_combo.clear()
        self.compare_combo1.clear()
        self.compare_combo2.clear()
        self.stats_curve_combo.addItems(sensor_ids)
        self.compare_combo1.addItems(sensor_ids)
        self.compare_combo2.addItems(sensor_ids)

        first_id = sensor_ids[0]
        if first_id in sensor_results:
            data = list(sensor_results[first_id])[start_idx:end_idx + 1]
            self._calculate_statistics(data, first_id)

    # ── 事件处理 ──

    def run_analysis(self, silent=False):
        current_data = self._current_data
        if current_data is None:
            if not silent:
                QMessageBox.warning(self, '警告', '请先加载数据')
            return

        try:
            data_source = self.data_source_combo.currentText()

            if data_source == '物理量':
                sensor_results = self._sensor_results
                if not sensor_results:
                    if not silent:
                        QMessageBox.warning(self, '警告', '请先在光纤公式配置中计算传感器数据')
                    return

                selected_sensors = []
                for i in range(self.analysis_sensor_list.count()):
                    item = self.analysis_sensor_list.item(i)
                    if item.checkState() == Qt.CheckState.Checked and \
                       item.data(Qt.ItemDataRole.UserRole) != '__select_all__':
                        selected_sensors.append(item.data(Qt.ItemDataRole.UserRole))

                if not selected_sensors:
                    if not silent:
                        QMessageBox.warning(self, '警告', '请选择至少一个传感器')
                    return

                time_col = '时间' if '时间' in current_data.columns else current_data.columns[0]
                time_data = list(current_data[time_col].values)

                start_idx = self.range_start.value()
                end_idx = min(self.range_end.value(), len(current_data) - 1)
                if start_idx >= len(current_data):
                    if not silent:
                        QMessageBox.warning(self, '警告', '起始索引超出数据范围')
                    return

                time_range = time_data[start_idx:end_idx + 1] if len(time_data) > 0 else None
                self._update_chart_multi_sensors(selected_sensors, time_range)
                self._calculate_statistics_for_sensors(selected_sensors, start_idx, end_idx)

            else:
                # 从复选框列表读取用户选择的数据列
                data_cols = []
                for i in range(self.analysis_sensor_list.count()):
                    item = self.analysis_sensor_list.item(i)
                    role = item.data(Qt.ItemDataRole.UserRole)
                    if role == '__select_all__' or role is None:
                        continue
                    if item.checkState() == Qt.CheckState.Checked:
                        data_cols.append(role)
                # 如果列表为空，回退到自动检测
                if not data_cols:
                    data_cols = [c for c in current_data.columns
                                if c != '时间' and '计数' not in str(c)
                                and not str(c).startswith('CH')
                                and pd.api.types.is_numeric_dtype(current_data[c])]
                if not data_cols:
                    if not silent:
                        QMessageBox.warning(self, '警告', '没有可用的数据列')
                    return

                time_col = '时间' if '时间' in current_data.columns else None
                if time_col:
                    time_data = list(current_data[time_col].values)
                else:
                    time_data = list(range(len(current_data)))

                start_idx = self.range_start.value()
                end_idx = min(self.range_end.value(), len(current_data) - 1)
                if start_idx >= len(current_data):
                    if not silent:
                        QMessageBox.warning(self, '警告', '起始索引超出数据范围')
                    return

                time_range = time_data[start_idx:end_idx + 1]
                self._update_chart_multi_columns(data_cols, time_range)

            self.status_message_requested.emit('分析完成')

        except Exception as e:
            import traceback
            QMessageBox.critical(self, '错误', f'分析失败: {str(e)}\n\n{traceback.format_exc()}')

    def _on_refresh_clicked(self):
        """点击刷新按钮：重新读取暗号标注后刷新数据和分析。"""
        # 通知 main.py 重新推送带暗号标注的分析数据
        self.data_refresh_requested.emit()

    def _refresh_data_column_list(self):
        """原始数据模式下，用当前数据的数值列填充分析传感器列表（复选框）。

        只显示有暗号标注的列（self._annotated_cols）；无暗号时显示所有数值列。
        """
        self.analysis_sensor_list.clear()
        if self._current_data is None:
            self.analysis_sensor_list.setEnabled(False)
            return

        if self._annotated_cols:
            data_cols = [c for c in self._current_data.columns
                         if c in self._annotated_cols
                         and pd.api.types.is_numeric_dtype(self._current_data[c])]
        else:
            data_cols = [c for c in self._current_data.columns
                         if c != '时间'
                         and pd.api.types.is_numeric_dtype(self._current_data[c])]
        if not data_cols:
            self.analysis_sensor_list.setEnabled(False)
            return

        select_all_item = QListWidgetItem('【全选】')
        select_all_item.setFlags(select_all_item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
        select_all_item.setCheckState(Qt.CheckState.Checked)
        select_all_item.setData(Qt.ItemDataRole.UserRole, '__select_all__')
        self.analysis_sensor_list.addItem(select_all_item)

        for col in data_cols:
            item = QListWidgetItem(col)
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(Qt.CheckState.Checked)
            item.setData(Qt.ItemDataRole.UserRole, col)
            self.analysis_sensor_list.addItem(item)

        self.analysis_sensor_list.setEnabled(True)

    def refresh_analysis_sensors(self):
        sensor_results = self._sensor_results
        self.analysis_sensor_list.clear()
        if sensor_results:
            select_all_item = QListWidgetItem('【全选】')
            select_all_item.setFlags(select_all_item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            select_all_item.setCheckState(Qt.CheckState.Unchecked)
            select_all_item.setData(Qt.ItemDataRole.UserRole, '__select_all__')
            self.analysis_sensor_list.addItem(select_all_item)

            for sensor_id in sensor_results:
                item = QListWidgetItem(sensor_id)
                item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
                item.setCheckState(Qt.CheckState.Unchecked)
                item.setData(Qt.ItemDataRole.UserRole, sensor_id)
                self.analysis_sensor_list.addItem(item)

            self.analysis_sensor_list.setEnabled(True)
        else:
            self.analysis_sensor_list.setEnabled(False)

    def _on_sensor_item_changed(self, item):
        if item.data(Qt.ItemDataRole.UserRole) == '__select_all__':
            state = item.checkState()
            for i in range(self.analysis_sensor_list.count()):
                other = self.analysis_sensor_list.item(i)
                if other.data(Qt.ItemDataRole.UserRole) != '__select_all__':
                    other.setCheckState(state)

    def _on_data_source_changed(self):
        if self.data_source_combo.currentText() == '物理量':
            self.refresh_analysis_sensors()
            self.stats_curve_combo.clear()
            self.compare_combo1.clear()
            self.compare_combo2.clear()
            sensor_results = self._sensor_results
            if sensor_results:
                ids = list(sensor_results.keys())
                self.stats_curve_combo.addItems(ids)
                self.compare_combo1.addItems(ids)
        else:
            # 切换到原始数据：自动填充数据列列表
            self._refresh_data_column_list()

    def _on_stats_curve_changed(self):
        sensor_results = self._sensor_results
        selected = self.stats_curve_combo.currentText()
        if not selected or selected not in sensor_results:
            return
        start_idx = self.range_start.value()
        end_idx = min(self.range_end.value(), len(sensor_results[selected]) - 1)
        data = list(sensor_results[selected])[start_idx:end_idx + 1]
        self._calculate_statistics(data, selected)

    def _compare_curves(self):
        sensor_results = self._sensor_results
        curve1 = self.compare_combo1.currentText()
        curve2 = self.compare_combo2.currentText()

        if not curve1 or not curve2:
            QMessageBox.warning(self, '警告', '请选择两条曲线进行对比')
            return
        if curve1 not in sensor_results or curve2 not in sensor_results:
            QMessageBox.warning(self, '警告', '请确保两条曲线都已计算')
            return

        start_idx = self.range_start.value()
        end_idx = min(self.range_end.value(), len(sensor_results[curve1]) - 1)
        end_idx = min(end_idx, len(sensor_results[curve2]) - 1)

        d1 = np.array(list(sensor_results[curve1])[start_idx:end_idx + 1])
        d2 = np.array(list(sensor_results[curve2])[start_idx:end_idx + 1])
        diff = d1 - d2

        result = (
            f'曲线对比: {curve1} vs {curve2}\n'
            f'{"=" * 30}\n'
            f'最大差值: {np.max(diff):.4f}\n'
            f'最小差值: {np.min(diff):.4f}\n'
            f'平均差值: {np.mean(diff):.4f}\n'
            f'标准差: {np.std(diff):.4f}\n'
            f'峰峰值: {np.max(diff) - np.min(diff):.4f}'
        )
        self.compare_result.setText(result)

    def _export_chart(self):
        file_path, _ = QFileDialog.getSaveFileName(
            self, '导出图表', '', 'PNG Files (*.png);;PDF Files (*.pdf)'
        )
        if file_path:
            self.chart_figure.savefig(file_path, dpi=300, bbox_inches='tight')
            QMessageBox.information(self, '成功', f'图表已保存到:\n{file_path}')
