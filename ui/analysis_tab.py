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

    _UNIT_MAP = {
        '应变': '（με）',
        '温度': '（℃）',
        '位移': '（mm）',
        '挠度': '（mm）',
        '拉力': '（kN）',
        '应力': '（MPa）',
        '频率': '（Hz）',
        '索力': '（kN）',
    }

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._current_data = None
        self._annotated_cols = None  # 有暗号的列名集合
        self._sensor_results = {}
        self._sensor_system = None
        self._annotation_mode = False  # 标注数据（其它数据）使用物理量路径
        self._current_plot_df = None
        self._setup_ui()
        self.analysis_sensor_list.itemChanged.connect(self._on_sensor_item_changed)

    @staticmethod
    def _strip_unit_suffix(name: str) -> str:
        """剥离列名尾部单位后缀，例如 'A1_应变(με)' → 'A1_应变'"""
        return _re.sub(r'[\(（].*?[\)）]', '', name).strip()

    @staticmethod
    def get_best_match_column(target_col: str, dataframe: 'pd.DataFrame') -> str | None:
        """双向去括号模糊匹配列名 — 容错防御层。

        1. 清洗 target_col 和所有 DataFrame 列名（剥离括号及单位）
        2. 依次尝试：完全一致 → 双向包含 → 返回 None
        """
        clean_target = _re.sub(r'[\(（].*?[\)）]', '', str(target_col)).strip()
        for actual_col in dataframe.columns:
            clean_actual = _re.sub(r'[\(（].*?[\)）]', '', str(actual_col)).strip()
            if clean_target == clean_actual or clean_target in clean_actual or clean_actual in clean_target:
                return actual_col
        return None

    @property
    def _is_fiber_data(self):
        """判断当前数据是否为光纤光栅数据。

        两重判定，必须与当前加载的数据文件严格绑定：
          1. 列名含 FBG / W\d+ / ENLIG / 光纤传感 （标准 FBG 列名）
          2. 主窗口当前模板名含"光纤"或"ENLIGHT" （暗号重命名后列名丢失前缀）

        注意：绝不检查 sensor_system.fbgs —— 上一轮光纤遗留的 FBG 定义
        会导致其它数据被误判为光纤数据（回归错误根源）。
        """
        if self._current_data is None:
            return False
        # ── 判定 1：列名匹配 ──
        for c in self._current_data.columns:
            name = str(c)
            if 'FBG' in name or 'ENLIG' in name or '光纤传感' in name:
                return True
            if _re.match(r'^W\d+$', name):
                return True
        # ── 判定 2：当前加载模板名 ──
        main_win = self.window()
        template = getattr(main_win, 'current_template', None)
        if template and hasattr(template, 'name'):
            tname = str(template.name)
            if '光纤' in tname or 'ENLIGHT' in tname:
                return True
        return False

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
        self.compare_result.setMaximumHeight(120)
        self.compare_result.setPlaceholderText('对比结果将显示在这里...')
        compare_layout.addWidget(self.compare_result)

        compare_group.setLayout(compare_layout)
        stats_layout.addWidget(compare_group)

        stats_group.setLayout(stats_layout)
        layout.addWidget(stats_group)

        layout.addStretch()

    # ── 依赖注入 (由主窗口调用) ──

    def set_current_data(self, df, annotated_cols=None):
        self._current_data = df
        self._annotated_cols = annotated_cols

        if not self._is_fiber_data:
            # ═══════════ 其它数据：锁定下拉框为"物理量" ═══════════
            self.data_source_combo.blockSignals(True)
            self.data_source_combo.setCurrentText('物理量')
            self.data_source_combo.setEnabled(False)
            self.data_source_combo.blockSignals(False)
            self._annotation_mode = True
            self._refresh_data_column_list()
            return

        # ═══════════ 光纤光栅数据：恢复切换能力 ═══════════
        self.data_source_combo.setEnabled(True)
        if annotated_cols:
            self._annotation_mode = True
            self.data_source_combo.setCurrentText('物理量')
        else:
            self._annotation_mode = False
            if self.data_source_combo.currentText() == '原始数据':
                self._refresh_data_column_list()

    def set_sensor_system(self, system):
        self._sensor_system = system

    def set_sensor_results(self, results: dict):
        self._sensor_results = results

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

    @staticmethod
    def _extract_core_name(name: str) -> str:
        """用关键词包含匹配提取核心物理量名称。

        遍历 unit_map 的 key，若 key 存在于列名中则直接命中。
        例如: 'A1应变-光纤1' → '应变', 'A2温度-2' → '温度'
        """
        if not name or not isinstance(name, str):
            return name or ''
        for key in AnalysisTabWidget._UNIT_MAP:
            if key in name:
                return key
        # 兜底：取连字符前的部分
        return name.split('-')[0].strip() if name.strip() else name

    def _update_chart_multi_columns(self, data_cols, time_data):
        self.ax.clear()
        max_points = 1000
        plot_df = self._current_plot_df
        if plot_df is None:
            return

        start_idx = self.range_start.value()
        end_idx = min(self.range_end.value(), len(plot_df) - 1)

        if time_data:
            time_range = list(time_data)[start_idx:end_idx + 1]
        else:
            time_range = list(range(start_idx, end_idx + 1))

        if len(time_range) > 10:
            tick_indices = np.linspace(0, len(time_range) - 1, 10, dtype=int)
            tick_labels = [self._format_time(time_range[i]) for i in tick_indices]
        else:
            tick_labels = None

        colors = ['b-', 'g-', 'r-', 'c-', 'm-', 'y-', 'k-', 'orange']
        for i, col in enumerate(data_cols):
            actual_key = self.get_best_match_column(col, plot_df)
            if actual_key is None:
                print(f"[analysis_tab] 双向清洗仍找不到列: '{col}', 当前可用列: {list(plot_df.columns)}")
                continue
            col_data = pd.to_numeric(plot_df[actual_key], errors='coerce').values[start_idx:end_idx + 1]
            plot_data = [float(v) if pd.notna(v) else None for v in col_data]

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

        # Y轴标记和图表名称
        if self._is_fiber_data and self.data_source_combo.currentText() == '原始数据':
            # 光纤光栅 - 原始波长数据（分支 A）
            self.ax.set_ylabel('波长差（nm）')
            self.ax.set_title('波长差时程曲线图')
        else:
            # 非光纤数据 或 光纤物理量模式：用原始列名做关键词匹配
            col_alias = data_cols[0] if data_cols else ''
            unit_map = AnalysisTabWidget._UNIT_MAP
            core_name = str(col_alias).split('-')[0].strip()
            unit = ''
            for key, val in unit_map.items():
                if key in str(col_alias):
                    core_name = key
                    unit = val
                    break
            self.ax.set_ylabel(f'{core_name}{unit}')
            self.ax.set_title(f'{core_name}时程曲线')
        self.ax.grid(True, alpha=0.3)
        self.ax.legend(loc='upper right', fontsize=8)
        self.chart_figure.tight_layout()
        self.chart_canvas.draw()

    def _update_chart_multi_sensors(self, sensor_ids, time_range):
        self.ax.clear()
        max_points = 1000
        colors = ['b-', 'g-', 'r-', 'c-', 'm-', 'y-', 'k-', 'orange']
        sensor_results = self._get_sensor_results()

        for i, sensor_id in enumerate(sensor_ids):
            if sensor_id not in sensor_results:
                print(f"[analysis_tab] 找不到传感器: '{sensor_id}', 当前可用: {list(sensor_results.keys())}")
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
            col_alias = sensor_ids[0]
            unit_map = AnalysisTabWidget._UNIT_MAP
            core_name = str(col_alias).split('-')[0].strip()
            unit = ''
            for key, val in unit_map.items():
                if key in str(col_alias):
                    core_name = key
                    unit = val
                    break
            self.ax.set_ylabel(f'{core_name}{unit}')
            self.ax.set_title(f'{core_name}时程曲线')

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
        _label_map = {
            'max': '最大值', 'min': '最小值', 'mean': '平均值', 'std': '标准差',
            'peak_to_peak': '峰峰值', 'rms': '均方根', 'skew': '偏度', 'kurtosis': '峰度',
        }
        for key, value in stats.items():
            label_name = _label_map.get(key, key)
            self.stats_labels[key].setText(f'{label_name}: {value:.4f}')

    def _calculate_statistics_for_sensors(self, sensor_ids, start_idx, end_idx):
        if not sensor_ids:
            return
        sensor_results = self._get_sensor_results()

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

    def _get_checked_items(self):
        """从分析传感器列表中获取所有勾选项的真实数据值。"""
        selected = []
        for i in range(self.analysis_sensor_list.count()):
            item = self.analysis_sensor_list.item(i)
            role = item.data(Qt.ItemDataRole.UserRole)
            if role == '__select_all__' or role is None:
                continue
            if item.checkState() == Qt.CheckState.Checked:
                selected.append(role)
        return selected

    def _get_current_plot_data(self):
        """根据当前文件类型和数据源模式返回路由后的绘图数据。

        返回: (data_df, time_col_name, selected_cols, is_sensor_mode)
        """
        if self._current_data is None:
            return None, '', [], False

        # ═════════════════════════════════════════════════════
        # 第一层：文件类型判断（基于当前数据的列名，而非持久状态）
        # ═════════════════════════════════════════════════════
        if self._is_fiber_data:
            # ═══════════ 光纤光栅数据 ═══════════
            ds = self.data_source_combo.currentText()
            if ds == '原始数据':
                # 分支 A：光纤 → 原始波长数据（固化波长差）
                selected = self._get_checked_items()
                # 任务 1：剥离单位后缀，匹配原生列名
                selected = [self._strip_unit_suffix(s) for s in selected]
                if not selected:
                    selected = [c for c in self._current_data.columns
                                if c != '时间' and '计数' not in str(c)
                                and not str(c).startswith('CH')
                                and pd.api.types.is_numeric_dtype(self._current_data[c])]
                if not selected:
                    return None, '', [], False
                tc = '时间' if '时间' in self._current_data.columns else ''
                # 任务 3：安全切片防呆 — 只保留 DataFrame 真实存在的列
                valid_selected = [c for c in selected if c in self._current_data.columns]
                if not valid_selected:
                    return None, '', [], False
                selected = valid_selected
                cols = ([tc] + selected) if tc else selected
                df = self._current_data[cols].copy()
                for c in selected:
                    col_vals = df[c].values
                    ref = next((float(v) for v in col_vals if pd.notna(v)), None)
                    if ref is not None:
                        df[c] = [float(v) - ref if pd.notna(v) else None
                                for v in col_vals]
                return df, tc, selected, False
            else:
                # 分支 B：光纤 → 传感器计算结果
                sensor_results = getattr(self.window(), 'sensor_results', {})
                if not sensor_results:
                    return None, '', [], True
                selected = self._get_checked_items()
                if not selected:
                    # 传感器列表为空 → 尝试从 sensor_results 自动重建
                    self.refresh_analysis_sensors()
                    selected = self._get_checked_items()
                    if not selected:
                        return None, '', [], True
                tc = '时间' if '时间' in self._current_data.columns else self._current_data.columns[0]
                ts = self._current_data[tc].reset_index(drop=True)
                rows = len(ts)
                d = {tc: ts}
                for sid in selected:
                    if sid in sensor_results:
                        v = list(sensor_results[sid])
                        if len(v) >= rows:
                            d[sid] = v[:rows]
                        else:
                            d[sid] = v + [float('nan')] * (rows - len(v))
                return pd.DataFrame(d), tc, selected, True
        else:
            # ═══════════ 其它数据（txt/csv/Excel）═══════════
            # 分支 C：只使用用户勾选的暗号列，绝不自动探测
            selected = self._get_checked_items()
            # 任务 1：剥离单位后缀，匹配原生列名
            selected = [self._strip_unit_suffix(s) for s in selected]
            if not selected:
                return None, '', [], False
            tc = '时间' if '时间' in self._current_data.columns else ''
            # 任务 3：安全切片防呆
            valid_selected = [c for c in selected if c in self._current_data.columns]
            if not valid_selected:
                return None, '', [], False
            cols = ([tc] + valid_selected) if tc else valid_selected
            return self._current_data[cols].copy(), tc, valid_selected, False

    def run_analysis(self, silent=False):
        current_data = self._current_data
        if current_data is None:
            if not silent:
                QMessageBox.warning(self, '警告', '请先加载数据')
            return

        try:
            plot_df, time_col, selected_cols, is_sensor_mode = self._get_current_plot_data()
            self._current_plot_df = plot_df
            if plot_df is not None and selected_cols:
                for c in selected_cols:
                    if c in plot_df.columns:
                        plot_df[c] = pd.to_numeric(plot_df[c], errors='coerce')

            if plot_df is None or not selected_cols:
                if not silent:
                    msg = ('请先选择至少一个数据列'
                           if self.data_source_combo.currentText() == '原始数据'
                           else '请先在光纤公式配置中计算传感器数据')
                    QMessageBox.warning(self, '警告', msg)
                return

            start_idx = self.range_start.value()
            end_idx = min(self.range_end.value(), len(plot_df) - 1)
            if start_idx >= len(plot_df):
                if not silent:
                    QMessageBox.warning(self, '警告', '起始索引超出数据范围')
                return

            if time_col:
                time_data = list(plot_df[time_col].values)
            else:
                time_data = list(range(len(plot_df)))
            time_range = time_data[start_idx:end_idx + 1]

            if is_sensor_mode:
                self._update_chart_multi_sensors(selected_cols, time_range)
                self._calculate_statistics_for_sensors(selected_cols, start_idx, end_idx)
            else:
                self._update_chart_multi_columns(selected_cols, time_range)
                # 原始数据模式也填充统计/对比下拉
                if selected_cols:
                    self.stats_curve_combo.clear()
                    self.compare_combo1.clear()
                    self.compare_combo2.clear()
                    self.stats_curve_combo.addItems(selected_cols)
                    self.compare_combo1.addItems(selected_cols)
                    self.compare_combo2.addItems(selected_cols)
                    first_data = plot_df[selected_cols[0]].values[start_idx:end_idx + 1]
                    self._calculate_statistics(first_data, selected_cols[0])

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

        【光纤 + 原始数据】：直接使用 _annotated_cols 暗号名列表（已重命名到 DataFrame），
        绕过 dtype 检测和成员匹配的脆弱链条。
        无暗号时退化为显示所有数值列。
        """
        self.analysis_sensor_list.clear()
        if self._current_data is None:
            self.analysis_sensor_list.setEnabled(False)
            return

        if self._annotated_cols:
            # 光纤暗号标注列 = rename 后的 DataFrame 列名，直接使用（过滤时间戳）
            data_cols = [name for name in self._annotated_cols
                         if name and '时间戳' not in name]
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

    def _get_sensor_results(self):
        """获取传感器计算结果，从顶层主窗口获取（绕过 PyQt parent 层级陷阱）。"""
        return getattr(self.window(), 'sensor_results', {})

    def refresh_analysis_sensors(self):
        sensor_results = self._get_sensor_results()
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
        # 非光纤数据时下拉框已禁用，忽略任何残留切换事件
        if not self.data_source_combo.isEnabled():
            return
        if self.data_source_combo.currentText() == '物理量':
            self.refresh_analysis_sensors()
            self.stats_curve_combo.clear()
            self.compare_combo1.clear()
            self.compare_combo2.clear()
            sensor_results = self._get_sensor_results()
            if sensor_results:
                ids = list(sensor_results.keys())
                self.stats_curve_combo.addItems(ids)
                self.compare_combo1.addItems(ids)
                self.compare_combo2.addItems(ids)
        else:
            # 切换到原始数据：自动填充数据列列表
            self._refresh_data_column_list()
            # 同时填充统计/对比下拉
            self.stats_curve_combo.clear()
            self.compare_combo1.clear()
            self.compare_combo2.clear()
            data_cols = []
            for i in range(self.analysis_sensor_list.count()):
                item = self.analysis_sensor_list.item(i)
                role = item.data(Qt.ItemDataRole.UserRole)
                if role == '__select_all__' or role is None:
                    continue
                data_cols.append(role)
            if data_cols:
                self.stats_curve_combo.addItems(data_cols)
                self.compare_combo1.addItems(data_cols)
                self.compare_combo2.addItems(data_cols)

    def _on_stats_curve_changed(self):
        selected = self.stats_curve_combo.currentText()
        if not selected:
            return
        actual_key = self.get_best_match_column(selected, self._current_plot_df) \
            if self._current_plot_df is not None else None
        if actual_key is None:
            if self._current_plot_df is not None:
                print(f"[analysis_tab] 统计找不到列: '{selected}', 当前可用列: {list(self._current_plot_df.columns)}")
            return
        start_idx = self.range_start.value()
        end_idx = min(self.range_end.value(), len(self._current_plot_df) - 1)
        data = self._current_plot_df[actual_key].values[start_idx:end_idx + 1]
        self._calculate_statistics(data, selected)

    def _compare_curves(self):
        curve1 = self.compare_combo1.currentText()
        curve2 = self.compare_combo2.currentText()

        if not curve1 or not curve2:
            QMessageBox.warning(self, '警告', '请选择两条曲线进行对比')
            return

        if curve1 == curve2:
            QMessageBox.warning(self, '选择错误', '请选择两条不同的曲线进行对比！')
            return

        if self._current_plot_df is None:
            QMessageBox.warning(self, '警告', '请先执行分析')
            return

        c1_key = self.get_best_match_column(curve1, self._current_plot_df)
        c2_key = self.get_best_match_column(curve2, self._current_plot_df)
        if c1_key is None or c2_key is None:
            QMessageBox.warning(self, '警告', '未找到对应数据列，请刷新选择')
            return

        # 强制同步"应用范围"切片（与图表保持绝对一致）
        start_idx = self.range_start.value()
        end_idx = self.range_end.value()
        plot_df = self._current_plot_df.iloc[start_idx:end_idx].copy()

        # 提取子集并执行联合防错位清洗
        subset = plot_df[[c1_key, c2_key]].copy()
        subset[c1_key] = pd.to_numeric(subset[c1_key], errors='coerce')
        subset[c2_key] = pd.to_numeric(subset[c2_key], errors='coerce')

        # 联合 Drop：确保留下的每一行两条曲线同时有值
        subset = subset.dropna(how='any')

        if subset.empty:
            QMessageBox.warning(self, '警告', '所选范围内无可对比的有效数据')
            return

        s1 = subset[c1_key]
        s2 = subset[c2_key]

        # 【核心修复】：窗口首点归零 — 减去各自首个有效值，消除静态偏置
        s1_zeroed = s1 - s1.iloc[0]
        s2_zeroed = s2 - s2.iloc[0]

        # 基于相对变化量计算真实误差
        diff = s1_zeroed - s2_zeroed
        max_err = diff.abs().max()
        mae = diff.abs().mean()
        rmse = np.sqrt((diff ** 2).mean())

        # 皮尔逊系数（基于归零后数据，含除零保护）
        std1, std2 = s1_zeroed.std(), s2_zeroed.std()
        if pd.isna(std1) or pd.isna(std2) or std1 == 0 or std2 == 0:
            corr = 0.0
        else:
            corr = s1_zeroed.corr(s2_zeroed)

        # 调试日志
        print(f"--- 对比调试信息 ({curve1} vs {curve2}) ---")
        print(f"原始起点差值: {s1.iloc[0] - s2.iloc[0]:.4f}")
        print(f"归零后 Max Err: {max_err:.4f}, RMSE: {rmse:.4f}, Corr: {corr:.4f}")
        print("---------------------------------")

        html = (
            '<div style="font-family: Arial, sans-serif; font-size: 13px; padding: 5px;">'
            f'<h4 style="margin-top: 0; color: #333;">曲线对比: <b>{curve1}</b> vs <b>{curve2}</b></h4>'
            '<hr style="border: 0; border-top: 1px solid #ccc; margin-bottom: 10px;">'
            '<table width="100%" cellpadding="5" cellspacing="0">'
            '<tr>'
            f'<td width="25%">绝对误差 (Max): <br><b><span style="color: #d9363e; font-size: 14px;">{max_err:.4f}</span></b></td>'
            f'<td width="25%">平均绝对误差 (MAE): <br><b><span style="color: #1890ff; font-size: 14px;">{mae:.4f}</span></b></td>'
            f'<td width="25%">均方根误差 (RMSE): <br><b><span style="color: #1890ff; font-size: 14px;">{rmse:.4f}</span></b></td>'
            f'<td width="25%">相关系数 (Pearson): <br><b><span style="color: #52c41a; font-size: 14px;">{corr:.4f}</span></b></td>'
            '</tr>'
            '</table>'
            '</div>'
        )
        self.compare_result.setHtml(html)

