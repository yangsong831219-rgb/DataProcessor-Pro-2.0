# DataProcessor Pro - Main Application
# PyQt6-based offline data analysis software

import sys
import json
from pathlib import Path
from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QTableWidget, QTableWidgetItem, QPushButton, QLabel, QFileDialog,
    QMessageBox, QTabWidget, QMenuBar, QMenu, QToolBar, QStatusBar,
    QDialog, QListWidget, QListWidgetItem, QAbstractItemView, QLineEdit,
    QComboBox, QGroupBox, QFormLayout, QCheckBox, QSpinBox, QDoubleSpinBox,
    QTextEdit, QSplitter, QGridLayout, QStackedWidget
)
from PyQt6.QtCore import Qt, QTimer, QSettings
from PyQt6.QtGui import QAction, QIcon

import pandas as pd
import numpy as np
from scipy import signal
from scipy.fft import fft
from docx import Document
from docx.shared import Inches, Pt
from docx.enum.text import WD_ALIGN_PARAGRAPH
import io

# Matplotlib for charts
import matplotlib
matplotlib.use('Agg')  # Use non-interactive backend
from matplotlib.figure import Figure
from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg
from matplotlib.backends.backend_qt import NavigationToolbar2QT
import matplotlib.pyplot as plt

# 设置 matplotlib 默认中文字体
plt.rcParams['font.sans-serif'] = ['SimHei', 'Microsoft YaHei', 'Arial Unicode MS']
plt.rcParams['axes.unicode_minus'] = False


# ============ Data Templates ============

class DataTemplate:
    def __init__(self, id, name, file_format, delimiter='\t', skip_rows=1, columns=None):
        self.id = id
        self.name = name
        self.file_format = file_format
        self.delimiter = delimiter
        self.skip_rows = skip_rows
        self.columns = columns or []

    def to_dict(self):
        return {
            'id': self.id,
            'name': self.name,
            'file_format': self.file_format,
            'delimiter': self.delimiter,
            'skip_rows': self.skip_rows,
            'columns': [{'name': c['name'], 'comment': c.get('comment', ''), 'data_type': c.get('data_type', 'numeric')} for c in self.columns]
        }


DEFAULT_TEMPLATES = [
    DataTemplate('enlight_type', 'ENLIGHT (光纤传感)', 'enlight', '\t', 104, [
        {'name': '时间', 'comment': '时间戳', 'data_type': 'time'},
    ]),
]


# ============ Data Cleaning ============

class CleaningRule:
    def __init__(self, name, rule_type, enabled=True, threshold=None, fill_method='linear', fill_value=None):
        self.name = name
        self.rule_type = rule_type  # 'adjacent_diff', 'nan', 'range', 'negative', 'zero'
        self.enabled = enabled
        self.threshold = threshold  # For adjacent_diff: max allowed difference
        self.min_value = None     # For range: min value
        self.max_value = None       # For range: max value
        self.fill_method = fill_method  # 'linear', 'mean', 'forward', 'custom'
        self.fill_value = fill_value


def detect_anomalies(df, rules):
    """检测异常值"""
    result = df.copy()

    # Add anomaly columns
    for col in result.columns:
        result[f'{col}_anomaly'] = False

    for col in result.columns:
        if pd.api.types.is_numeric_dtype(result[col]):
            for rule in rules:
                if not rule.enabled:
                    continue

                if rule.rule_type == 'adjacent_diff' and rule.threshold is not None:
                    # Check if difference between adjacent values exceeds threshold
                    diff = result[col].diff().abs()
                    mask = diff > rule.threshold
                    result.loc[mask, f'{col}_anomaly'] = True

                elif rule.rule_type == 'nan':
                    # Check for NaN values
                    mask = result[col].isna()
                    result.loc[mask, f'{col}_anomaly'] = True

                elif rule.rule_type == 'range':
                    if rule.min_value is not None and rule.max_value is not None:
                        mask = (result[col] < rule.min_value) | (result[col] > rule.max_value)
                        result.loc[mask, f'{col}_anomaly'] = True

                elif rule.rule_type == 'negative':
                    mask = result[col] < 0
                    result.loc[mask, f'{col}_anomaly'] = True

                elif rule.rule_type == 'zero':
                    mask = result[col] == 0
                    result.loc[mask, f'{col}_anomaly'] = True

    return result


def fill_missing(df, rules):
    """填充缺失值（基于规则填充）"""
    result = df.copy()
    for col in result.columns:
        if pd.api.types.is_numeric_dtype(result[col]):
            # Find the fill method for this column
            fill_method = 'linear'  # default
            fill_value = None

            for rule in rules:
                if rule.enabled and rule.rule_type == 'adjacent_diff':
                    fill_method = rule.fill_method
                    fill_value = rule.fill_value
                    break

            if fill_method == 'linear':
                result[col] = result[col].interpolate()
            elif fill_method == 'mean':
                result[col] = result[col].fillna(result[col].mean())
            elif fill_method == 'forward':
                result[col] = result[col].ffill()
            elif fill_method == 'backward':
                result[col] = result[col].bfill()
            elif fill_method == 'custom' and fill_value is not None:
                result[col] = result[col].fillna(fill_value)

    return result


def clean_data(df, rules):
    """完整清洗流程"""
    df = detect_anomalies(df, rules)
    df = fill_missing(df, rules)
    return df


# ============ Sensor System ============

class FBG:
    """FBG光纤光栅定义 - 对应一列原始波长数据"""
    def __init__(self, id, channel, wavelength_min=None, wavelength_max=None):
        self.id = id              # FBG ID，如 "W1", "波长1"
        self.channel = channel    # 对应的波长列名
        self.wavelength_min = wavelength_min  # 波长下限（用于校验）
        self.wavelength_max = wavelength_max  # 波长上限（用于校验）

    def is_valid(self, wavelength):
        """判断波长是否在有效范围内"""
        if pd.isna(wavelength):
            return False
        if self.wavelength_min is not None and self.wavelength_max is not None:
            return self.wavelength_min <= wavelength <= self.wavelength_max
        return True


class Sensor:
    """传感器定义"""
    TYPE_PARAMS = {
        'strain': {
            'name': '应变',
            'unit': 'με',
            'need_temp_compensation': True,
        },
        'temperature': {
            'name': '温度',
            'unit': '°C',
            'need_temp_compensation': False,
        },
        'displacement': {
            'name': '位移',
            'unit': 'mm',
            'need_temp_compensation': False,
        },
        'inclination': {
            'name': '倾角',
            'unit': '°',
            'need_temp_compensation': False,
        },
        'pressure': {
            'name': '压力',
            'unit': 'MPa',
            'need_temp_compensation': False,
        },
    }

    def __init__(self, id, sensor_type, formula=None, constants=None, active=True):
        self.id = id
        self.sensor_type = sensor_type
        # 公式: "W1 * k1 - W2 * k2"  (W1, W2 是FBG的ID，运算时自动变为 波长差值)
        self.formula = formula
        self.constants = constants or {}
        self.active = active

    def get_unit(self):
        return self.TYPE_PARAMS.get(self.sensor_type, {}).get('unit', '')

    def get_name(self):
        return self.TYPE_PARAMS.get(self.sensor_type, {}).get('name', self.sensor_type)


class SensorSystem:
    """传感器系统管理器"""
    def __init__(self):
        self.fbgs = []        # FBG列表
        self.sensors = []     # 传感器列表
        self.reference_row = 0  # 参考行索引（初始值所在行）

    def set_reference_row(self, row_index):
        """设置参考行索引"""
        self.reference_row = row_index

    def add_fbg(self, fbg):
        self.fbgs.append(fbg)

    def add_sensor(self, sensor):
        self.sensors.append(sensor)

    def calculate(self, df, fbg_columns):
        """根据原始波长数据计算所有传感器值

        计算逻辑：
        1. 获取参考行的初始波长值
        2. 对每一行：物理量 = (当前波长 - 初始波长) × 系数
        3. 如果公式涉及多列，则分别计算后组合
        """
        results = {}

        if len(df) == 0:
            return results

        ref_row = self.reference_row if self.reference_row < len(df) else 0

        # 构建列数据字典
        column_data = {}
        for col in df.columns:
            column_data[col] = df[col].values

        # 计算每个FBG的初始值（参考行）和差值
        fbg_initial = {}  # 初始波长值
        fbg_delta = {}    # 每行的波长差值 (W - W0)

        for fbg in self.fbgs:
            if fbg.channel in column_data:
                col_values = column_data[fbg.channel]
                if ref_row < len(col_values):
                    initial = col_values[ref_row]
                else:
                    initial = col_values[0]
                fbg_initial[fbg.id] = initial

                # 计算每行与初始值的差值
                delta = []
                for w in col_values:
                    if pd.isna(w) or pd.isna(initial):
                        delta.append(None)
                    else:
                        delta.append(w - initial)
                fbg_delta[fbg.id] = delta

        # 计算每个传感器
        for sensor in self.sensors:
            if not sensor.active:
                continue

            try:
                result = self._evaluate_sensor_formula(
                    sensor.formula,
                    sensor.constants,
                    fbg_delta,
                    len(df)
                )
                results[sensor.id] = result
            except Exception as e:
                print(f"计算传感器 {sensor.id} 失败: {e}")
                import traceback
                traceback.print_exc()
                results[sensor.id] = [None] * len(df)

        return results

    def _evaluate_sensor_formula(self, formula, constants, fbg_delta, n_rows):
        """计算传感器公式

        公式示例：
        - "W1 * k1"  单传感器应变
        - "W1 * k1 - W2 * k2"  温补应变 (W1应变, W2温度)
        - "W1 * k1 + W2 * k2"  多传感器组合

        W1, W2 等会被替换为该列的波长差值 (W - W0)
        """
        # 替换常量（大小写不敏感）
        expr = formula
        for name, value in constants.items():
            # 替换小写版本
            expr = expr.replace(name, f'({value})')
            # 同时替换大写版本
            expr = expr.replace(name.upper(), f'({value})')

        # 检查公式中是否有未替换的常量（大小写不敏感）
        import re
        remaining_consts = re.findall(r'[kK]\d+', expr)
        if remaining_consts:
            print(f"警告: 公式中常量 {remaining_consts} 未定义，将导致计算错误")

        # 计算每行
        result = []
        for i in range(n_rows):
            row_vars = {}
            for fbg_id, delta in fbg_delta.items():
                row_vars[fbg_id] = delta[i] if i < len(delta) else None

            try:
                val = eval(expr, {"__builtins__": {}}, row_vars)
                result.append(val)
            except Exception as e:
                print(f"行 {i} 计算失败: {expr} -> {e}")
                result.append(None)

        return result


# ============ Data Analysis ============

def time_domain_analysis(data):
    """时域分析"""
    arr = np.array(data)
    return {
        'mean': float(np.mean(arr)),
        'max': float(np.max(arr)),
        'min': float(np.min(arr)),
        'rms': float(np.sqrt(np.mean(arr**2))),
        'std': float(np.std(arr)),
    }


def frequency_domain_analysis(data, sampling_rate=1000.0):
    """频域分析 - FFT"""
    arr = np.array(data)
    n = len(arr)
    freqs = np.fft.fftfreq(n, 1/sampling_rate)
    fft_vals = np.fft.fft(arr)
    magnitudes = np.abs(fft_vals[:n//2])
    return {
        'frequencies': freqs[:n//2].tolist(),
        'magnitudes': magnitudes.tolist(),
        'dominant_freq': float(freqs[np.argmax(magnitudes)]),
    }


def apply_filter(data, filter_type, cutoff, sampling_rate):
    """滤波处理"""
    nyquist = sampling_rate / 2
    if filter_type == 'lowpass':
        b, a = signal.butter(4, cutoff/nyquist, btype='low')
    elif filter_type == 'highpass':
        b, a = signal.butter(4, cutoff/nyquist, btype='high')
    elif filter_type == 'bandpass':
        b, a = signal.butter(4, [cutoff[0]/nyquist, cutoff[1]/nyquist], btype='band')
    else:
        return data
    return signal.filtfilt(b, a, data).tolist()


# ============ Report Generation ============

def generate_report(data_summary, charts, tables, config):
    """生成 Word 报告"""
    doc = Document()
    title = doc.add_heading(config.get('title', '数据分析报告'), 0)
    title.alignment = WD_ALIGN_PARAGRAPH.CENTER

    doc.add_heading('数据概要', level=1)
    for key, value in data_summary.items():
        doc.add_paragraph(f'{key}: {value}')

    doc.add_heading('分析图表', level=1)
    for chart_bytes in charts:
        doc.add_picture(io.BytesIO(chart_bytes), width=Inches(5.5))

    doc.add_heading('数据表格', level=1)
    for table_data in tables:
        table = doc.add_table(rows=len(table_data), cols=len(table_data[0]))
        for i, row in enumerate(table_data):
            for j, cell in enumerate(row):
                table.cell(i, j).text = str(cell)

    buffer = io.BytesIO()
    doc.save(buffer)
    return buffer.getvalue()


# ============ File Parser ============

def parse_file(file_path, template):
    """根据模板解析数据文件"""
    if template.file_format == 'csv':
        df = pd.read_csv(file_path, delimiter=template.delimiter,
                        skiprows=template.skip_rows, header=None)
    elif template.file_format == 'txt':
        df = pd.read_csv(file_path, delimiter=template.delimiter,
                        skiprows=template.skip_rows, header=None, encoding='gbk')
    elif template.file_format in ('xlsx', 'xls'):
        df = pd.read_excel(file_path, header=None)
    elif template.file_format == 'enlight':
        # ENLIGHT format: skip metadata, use header, handle variable columns
        with open(file_path, 'r', encoding='utf-8') as f:
            lines = f.readlines()

        # Find header line (contains "Timestamp")
        header_line = None
        data_start = 0
        for i, line in enumerate(lines):
            if 'Timestamp' in line and '# CH' in line:
                header_line = i
                data_start = i + 1
                break

        if header_line is None:
            raise ValueError("无法找到数据头行")

        # Parse header
        headers = lines[header_line].strip().split('\t')

        # Parse data
        data_rows = []
        for line in lines[data_start:]:
            if not line.strip():
                continue
            parts = line.strip().split('\t')
            # Only keep the first 17 columns (Timestamp + CH1-CH16 counts)
            # Then append wavelength values that exist
            data_rows.append(parts[:17] + parts[17:17+8])

        # Create DataFrame
        df = pd.DataFrame(data_rows)
        df.columns = ['时间'] + [f'CH{i}计数' for i in range(1, 17)] + [f'波长{i}' for i in range(1, 9)]

        # Convert numeric columns
        for col in df.columns:
            try:
                df[col] = pd.to_numeric(df[col])
            except:
                pass

        return df

    if template.columns:
        df.columns = [col['name'] for col in template.columns]

    return df


# ENLIGHT template
ENLIGHT_TEMPLATE = DataTemplate(
    'enlight_type',
    'ENLIGHT (光纤传感)',
    'enlight',  # special format
    '\t',
    104,  # skip first 104 lines
    [
        {'name': '时间', 'comment': '时间戳', 'data_type': 'time'},
    ]
)


# ============ Main Window ============

class DataProcessorWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.current_data = None
        self.current_columns = []
        self.sensor_results = {}  # 存储计算后的传感器物理量
        self.cleaning_rules = [
            CleaningRule('数值范围', 'range', True, 0, 100, 'linear'),
            CleaningRule('负数检测', 'negative', True, fill_method='forward'),
        ]
        # 传感器系统
        self.sensor_system = SensorSystem()
        self.init_sensor_system()

        # Ollama AI客户端
        try:
            from py.ollama_client import OllamaClient
            self.ollama_client = OllamaClient()
        except ImportError:
            self.ollama_client = None

        self.init_ui()

    def init_sensor_system(self):
        """初始化传感器系统"""
        # 添加默认FBG（根据ENLIGHT数据格式）
        # FBG(id, channel, wavelength_min, wavelength_max)
        self.sensor_system.add_fbg(FBG('W1', '波长1', 1520, 1590))
        self.sensor_system.add_fbg(FBG('W2', '波长2', 1520, 1590))
        self.sensor_system.add_fbg(FBG('W3', '波长3', 1520, 1590))
        self.sensor_system.add_fbg(FBG('W4', '波长4', 1520, 1590))
        self.sensor_system.add_fbg(FBG('W5', '波长5', 1520, 1590))
        self.sensor_system.add_fbg(FBG('W6', '波长6', 1520, 1590))
        self.sensor_system.add_fbg(FBG('W7', '波长7', 1520, 1590))
        self.sensor_system.add_fbg(FBG('W8', '波长8', 1520, 1590))

        # 添加默认传感器
        # 公式: "W1 * k1" 表示 波长差值乘以系数k1
        self.sensor_system.add_sensor(Sensor(
            '应变1', 'strain',
            'W1 * k1',
            {'k1': 1000.0}
        ))
        self.sensor_system.add_sensor(Sensor(
            '温度1', 'temperature',
            'W1 * k2',
            {'k2': 1e-6}
        ))

    def init_ui(self):
        self.setWindowTitle('DataProcessor Pro - 数据分析软件')
        self.setGeometry(100, 100, 1400, 900)

        # Create menu bar
        self.create_menu_bar()

        # Create central widget with tabs
        self.central_widget = QTabWidget()
        self.setCentralWidget(self.central_widget)

        # Data tab
        self.data_tab = QWidget()
        self.create_data_tab()
        self.central_widget.addTab(self.data_tab, '数据文件')

        # Cleaning tab
        self.cleaning_tab = QWidget()
        self.create_cleaning_tab()
        self.central_widget.addTab(self.cleaning_tab, '数据清洗')

        # Sensor tab
        self.sensor_tab = QWidget()
        self.create_sensor_tab()
        self.central_widget.addTab(self.sensor_tab, '光纤公式配置')

        # Analysis tab
        self.analysis_tab = QWidget()
        self.create_analysis_tab()
        self.central_widget.addTab(self.analysis_tab, '数据分析')

        # Report tab
        self.report_tab = QWidget()
        self.create_report_tab()
        self.central_widget.addTab(self.report_tab, '信息整合')

        # Status bar
        self.status_bar = QStatusBar()
        self.setStatusBar(self.status_bar)
        self.status_bar.showMessage('就绪')

    def create_menu_bar(self):
        menubar = self.menuBar()

        # 全局配置 menu
        config_menu = menubar.addMenu('全局配置')

        save_config_action = QAction('保存配置', self)
        save_config_action.setShortcut('Ctrl+Shift+S')
        save_config_action.triggered.connect(self.save_config)
        config_menu.addAction(save_config_action)

        load_config_action = QAction('读取配置', self)
        load_config_action.triggered.connect(self.load_config)
        config_menu.addAction(load_config_action)

        config_menu.addSeparator()

        reset_config_action = QAction('重置配置', self)
        reset_config_action.triggered.connect(self.reset_config)
        config_menu.addAction(reset_config_action)

        # File menu
        file_menu = menubar.addMenu('文件')

        open_action = QAction('打开文件...', self)
        open_action.setShortcut('Ctrl+O')
        open_action.triggered.connect(self.open_file)
        file_menu.addAction(open_action)

        save_action = QAction('保存数据...', self)
        save_action.setShortcut('Ctrl+S')
        save_action.triggered.connect(self.save_data)
        file_menu.addAction(save_action)

        file_menu.addSeparator()

        export_action = QAction('导出报告...', self)
        export_action.triggered.connect(self.export_report)
        file_menu.addAction(export_action)

        file_menu.addSeparator()

        exit_action = QAction('退出', self)
        exit_action.setShortcut('Ctrl+Q')
        exit_action.triggered.connect(self.close)
        file_menu.addAction(exit_action)

    def create_data_tab(self):
        layout = QVBoxLayout()

        # Button bar
        btn_layout = QHBoxLayout()
        self.open_file_btn = QPushButton('打开文件')
        self.open_file_btn.clicked.connect(self.open_file)
        btn_layout.addWidget(self.open_file_btn)

        self.clear_data_btn = QPushButton('清除数据')
        self.clear_data_btn.clicked.connect(self.clear_data)
        btn_layout.addWidget(self.clear_data_btn)

        btn_layout.addStretch()
        layout.addLayout(btn_layout)

        # Data table
        self.data_table = QTableWidget()
        self.data_table.setAlternatingRowColors(True)
        layout.addWidget(self.data_table)

        self.data_tab.setLayout(layout)

    def create_cleaning_tab(self):
        layout = QVBoxLayout()

        info_label = QLabel('配置数据清洗规则，然后点击"应用清洗"按钮处理数据')
        layout.addWidget(info_label)

        # Rule 1: Adjacent Difference Detection
        rule1_group = QGroupBox('规则1: 相邻数据差值检测')
        rule1_layout = QVBoxLayout()

        self.adjacent_enabled = QCheckBox('启用相邻数据差值检测')
        self.adjacent_enabled.setChecked(True)
        rule1_layout.addWidget(self.adjacent_enabled)

        adj_layout = QHBoxLayout()
        adj_layout.addWidget(QLabel('差值阈值:'))
        self.diff_threshold = QDoubleSpinBox()
        self.diff_threshold.setRange(0, 1e9)
        self.diff_threshold.setValue(1.0)
        self.diff_threshold.setDecimals(4)
        adj_layout.addWidget(self.diff_threshold)

        adj_layout.addWidget(QLabel('(超过此值判断为异常)'))
        adj_layout.addStretch()
        rule1_layout.addLayout(adj_layout)
        rule1_group.setLayout(rule1_layout)
        layout.addWidget(rule1_group)

        # Rule 2: NaN Detection
        rule2_group = QGroupBox('规则2: 缺失值检测')
        rule2_layout = QVBoxLayout()

        self.nan_enabled = QCheckBox('启用缺失值(NaN)检测')
        self.nan_enabled.setChecked(True)
        rule2_layout.addWidget(self.nan_enabled)

        nan_layout = QHBoxLayout()
        nan_layout.addWidget(QLabel('(数据为空或NaN时判断为异常)'))
        nan_layout.addStretch()
        rule2_layout.addLayout(nan_layout)
        rule2_group.setLayout(rule2_layout)
        layout.addWidget(rule2_group)

        # Fill Method
        fill_group = QGroupBox('填充方式')
        fill_layout = QHBoxLayout()

        fill_layout.addWidget(QLabel('选择填充方式:'))
        self.fill_method_combo = QComboBox()
        self.fill_method_combo.addItems(['linear', 'mean', 'forward', 'backward', 'custom'])
        fill_layout.addWidget(self.fill_method_combo)

        self.custom_fill_value = QDoubleSpinBox()
        self.custom_fill_value.setRange(-1e9, 1e9)
        self.custom_fill_value.setValue(0)
        self.custom_fill_value.setDecimals(4)
        fill_layout.addWidget(QLabel('自定义值:'))
        fill_layout.addWidget(self.custom_fill_value)
        fill_layout.addStretch()
        fill_group.setLayout(fill_layout)
        layout.addWidget(fill_group)

        # Apply button
        apply_btn = QPushButton('应用清洗')
        apply_btn.clicked.connect(self.apply_cleaning)
        layout.addWidget(apply_btn)

        # Result display
        self.cleaning_result = QTextEdit()
        self.cleaning_result.setReadOnly(True)
        self.cleaning_result.setMaximumHeight(100)
        layout.addWidget(QLabel('异常检测结果:'))
        layout.addWidget(self.cleaning_result)

        layout.addStretch()
        self.cleaning_tab.setLayout(layout)

    def create_sensor_tab(self):
        """创建传感器配置标签页"""
        layout = QVBoxLayout()

        info_label = QLabel('配置FBG光纤光栅和传感器参数')
        layout.addWidget(info_label)

        # 参考行设置
        ref_layout = QHBoxLayout()
        ref_layout.addWidget(QLabel('初始值行 (参考行):'))
        self.ref_row_spin = QSpinBox()
        self.ref_row_spin.setRange(0, 9999)
        self.ref_row_spin.setValue(0)
        self.ref_row_spin.setPrefix('第 ')
        self.ref_row_spin.setSuffix(' 行')
        ref_layout.addWidget(self.ref_row_spin)
        ref_layout.addWidget(QLabel('(波长减初始值再乘系数得到物理量)'))
        ref_layout.addStretch()
        layout.addLayout(ref_layout)

        # FBG列表和传感器列表的水平布局
        h_layout = QHBoxLayout()

        # 左侧：FBG定义
        fbg_group = QGroupBox('FBG定义 (光纤光栅)')
        fbg_layout = QVBoxLayout()

        # FBG表格
        self.fbg_table = QTableWidget()
        self.fbg_table.setColumnCount(4)
        self.fbg_table.setHorizontalHeaderLabels(['ID', '波长列', '波长下限', '波长上限'])
        self.fbg_table.setRowCount(len(self.sensor_system.fbgs))
        for i, fbg in enumerate(self.sensor_system.fbgs):
            self.fbg_table.setItem(i, 0, QTableWidgetItem(fbg.id))
            self.fbg_table.setItem(i, 1, QTableWidgetItem(fbg.channel))
            self.fbg_table.setItem(i, 2, QTableWidgetItem(str(fbg.wavelength_min or '')))
            self.fbg_table.setItem(i, 3, QTableWidgetItem(str(fbg.wavelength_max or '')))
        self.fbg_table.resizeColumnsToContents()
        fbg_layout.addWidget(self.fbg_table)

        # FBG操作按钮
        fbg_btn_layout = QHBoxLayout()
        self.add_fbg_btn = QPushButton('添加FBG')
        self.add_fbg_btn.clicked.connect(self.add_fbg)
        fbg_btn_layout.addWidget(self.add_fbg_btn)

        self.edit_fbg_btn = QPushButton('编辑FBG')
        self.edit_fbg_btn.clicked.connect(self.edit_fbg)
        fbg_btn_layout.addWidget(self.edit_fbg_btn)

        self.delete_fbg_btn = QPushButton('删除FBG')
        self.delete_fbg_btn.clicked.connect(self.delete_fbg)
        fbg_btn_layout.addWidget(self.delete_fbg_btn)

        fbg_layout.addLayout(fbg_btn_layout)
        fbg_group.setLayout(fbg_layout)
        h_layout.addWidget(fbg_group)

        # 右侧：传感器定义
        sensor_group = QGroupBox('传感器定义')
        sensor_layout = QVBoxLayout()

        # 传感器表格
        self.sensor_table = QTableWidget()
        self.sensor_table.setColumnCount(5)
        self.sensor_table.setHorizontalHeaderLabels(['ID', '类型', '公式', '常量', '启用'])
        self.sensor_table.setRowCount(len(self.sensor_system.sensors))
        for i, sensor in enumerate(self.sensor_system.sensors):
            self.sensor_table.setItem(i, 0, QTableWidgetItem(sensor.id))
            self.sensor_table.setItem(i, 1, QTableWidgetItem(sensor.get_name()))
            self.sensor_table.setItem(i, 2, QTableWidgetItem(sensor.formula))
            const_str = ', '.join([f"{k}={v:.2f}" for k, v in sensor.constants.items()])
            self.sensor_table.setItem(i, 3, QTableWidgetItem(const_str))
            self.sensor_table.setItem(i, 4, QTableWidgetItem('是' if sensor.active else '否'))
        self.sensor_table.resizeColumnsToContents()
        sensor_layout.addWidget(self.sensor_table)

        # 传感器操作按钮
        sensor_btn_layout = QHBoxLayout()
        self.add_sensor_btn = QPushButton('添加传感器')
        self.add_sensor_btn.clicked.connect(self.add_sensor)
        sensor_btn_layout.addWidget(self.add_sensor_btn)

        self.edit_sensor_btn = QPushButton('编辑传感器')
        self.edit_sensor_btn.clicked.connect(self.edit_sensor)
        sensor_btn_layout.addWidget(self.edit_sensor_btn)

        self.delete_sensor_btn = QPushButton('删除传感器')
        self.delete_sensor_btn.clicked.connect(self.delete_sensor)
        sensor_btn_layout.addWidget(self.delete_sensor_btn)

        self.copy_sensor_btn = QPushButton('复制传感器')
        self.copy_sensor_btn.clicked.connect(self.copy_sensor)
        sensor_btn_layout.addWidget(self.copy_sensor_btn)

        sensor_layout.addLayout(sensor_btn_layout)
        sensor_group.setLayout(sensor_layout)
        h_layout.addWidget(sensor_group)

        layout.addLayout(h_layout)

        # 计算按钮
        calc_layout = QHBoxLayout()
        self.calculate_sensors_btn = QPushButton('计算所有传感器')
        self.calculate_sensors_btn.clicked.connect(self.calculate_sensors)
        calc_layout.addWidget(self.calculate_sensors_btn)

        self.export_sensor_data_btn = QPushButton('导出传感器数据')
        self.export_sensor_data_btn.clicked.connect(self.export_sensor_data)
        calc_layout.addWidget(self.export_sensor_data_btn)

        layout.addLayout(calc_layout)

        # 计算结果预览
        layout.addWidget(QLabel('传感器数据预览:'))
        self.sensor_result_table = QTableWidget()
        self.sensor_result_table.setMaximumHeight(200)
        layout.addWidget(self.sensor_result_table)

        # 保存选项
        save_layout = QHBoxLayout()
        save_layout.addWidget(QLabel('文件名:'))
        self.sensor_filename_input = QLineEdit()
        self.sensor_filename_input.setText('sensor_data')
        save_layout.addWidget(self.sensor_filename_input)

        self.sensor_format_combo = QComboBox()
        self.sensor_format_combo.addItems(['csv', 'txt', 'xlsx'])
        save_layout.addWidget(self.sensor_format_combo)

        self.save_sensor_btn = QPushButton('保存传感器数据')
        self.save_sensor_btn.clicked.connect(self.save_sensor_data)
        save_layout.addWidget(self.save_sensor_btn)

        save_layout.addStretch()
        layout.addLayout(save_layout)

        layout.addStretch()
        self.sensor_tab.setLayout(layout)

    def add_fbg(self):
        """添加FBG"""
        dialog = FBGEditDialog(self)
        if dialog.exec():
            fbg = dialog.get_fbg()
            self.sensor_system.add_fbg(fbg)
            self.refresh_fbg_table()

    def edit_fbg(self):
        """编辑选中FBG"""
        row = self.fbg_table.currentRow()
        if row >= 0 and row < len(self.sensor_system.fbgs):
            fbg = self.sensor_system.fbgs[row]
            dialog = FBGEditDialog(self, fbg)
            if dialog.exec():
                self.sensor_system.fbgs[row] = dialog.get_fbg()
                self.refresh_fbg_table()

    def delete_fbg(self):
        """删除选中FBG"""
        row = self.fbg_table.currentRow()
        if row >= 0 and row < len(self.sensor_system.fbgs):
            self.sensor_system.fbgs.pop(row)
            self.refresh_fbg_table()

    def refresh_fbg_table(self):
        """刷新FBG表格"""
        self.fbg_table.setRowCount(len(self.sensor_system.fbgs))
        for i, fbg in enumerate(self.sensor_system.fbgs):
            self.fbg_table.setItem(i, 0, QTableWidgetItem(fbg.id))
            self.fbg_table.setItem(i, 1, QTableWidgetItem(fbg.channel))
            self.fbg_table.setItem(i, 2, QTableWidgetItem(str(fbg.wavelength_min or '')))
            self.fbg_table.setItem(i, 3, QTableWidgetItem(str(fbg.wavelength_max or '')))
        self.fbg_table.resizeColumnsToContents()

    def refresh_sensor_table(self):
        """刷新传感器表格"""
        self.sensor_table.setRowCount(len(self.sensor_system.sensors))
        for i, sensor in enumerate(self.sensor_system.sensors):
            self.sensor_table.setItem(i, 0, QTableWidgetItem(sensor.id))
            self.sensor_table.setItem(i, 1, QTableWidgetItem(sensor.get_name()))
            self.sensor_table.setItem(i, 2, QTableWidgetItem(sensor.formula))
            const_str = ', '.join([f"{k}={v:.2f}" for k, v in sensor.constants.items()])
            self.sensor_table.setItem(i, 3, QTableWidgetItem(const_str))
            self.sensor_table.setItem(i, 4, QTableWidgetItem('是' if sensor.active else '否'))
        self.sensor_table.resizeColumnsToContents()

    def add_sensor(self):
        """添加传感器"""
        dialog = SensorEditDialog(self)
        if dialog.exec():
            sensor = dialog.get_sensor()
            self.sensor_system.add_sensor(sensor)
            self.refresh_sensor_table()

    def edit_sensor(self):
        """编辑选中传感器"""
        row = self.sensor_table.currentRow()
        if row >= 0 and row < len(self.sensor_system.sensors):
            sensor = self.sensor_system.sensors[row]
            dialog = SensorEditDialog(self, sensor)
            if dialog.exec():
                self.sensor_system.sensors[row] = dialog.get_sensor()
                self.refresh_sensor_table()

    def delete_sensor(self):
        """删除选中传感器"""
        row = self.sensor_table.currentRow()
        if row >= 0 and row < len(self.sensor_system.sensors):
            self.sensor_system.sensors.pop(row)
            self.refresh_sensor_table()

    def copy_sensor(self):
        """复制选中传感器，自动递增ID和公式中的W编号"""
        row = self.sensor_table.currentRow()
        if row >= 0 and row < len(self.sensor_system.sensors):
            original = self.sensor_system.sensors[row]

            # 从原始ID提取数字，创建新ID
            # 例如: "传感器1" -> "传感器2", "传感器A1" -> "传感器A2"
            import re
            id_match = re.search(r'(\d+)$', original.id)
            if id_match:
                base_id = original.id[:id_match.start()]
                old_num = int(id_match.group(1))
                new_num = old_num + 1
                new_id = f'{base_id}{new_num}'
            else:
                new_id = original.id + '_copy'
                old_num = 0
                new_num = 1

            # 替换公式中的W编号：W1->W2, W2->W3, etc.
            # 如果原公式使用W{old_num}则改为W{new_num}
            new_formula = original.formula
            if old_num > 0:
                # 替换公式中的 W{old_num} 为 W{new_num}
                new_formula = re.sub(rf'\bW{old_num}\b', f'W{new_num}', original.formula)

            copied = Sensor(
                new_id,
                original.sensor_type,
                new_formula,
                dict(original.constants),  # 深拷贝常量字典
                original.active
            )
            self.sensor_system.add_sensor(copied)
            self.refresh_sensor_table()

    def calculate_sensors(self):
        """计算所有传感器"""
        if self.current_data is None:
            QMessageBox.warning(self, '警告', '请先加载数据')
            return

        try:
            # 设置参考行
            ref_row = self.ref_row_spin.value()
            self.sensor_system.set_reference_row(ref_row)

            results = self.sensor_system.calculate(self.current_data, self.current_columns)

            # 预览显示：所有传感器结果，每列最多100行
            n_sensors = len(results)
            n_preview_rows = min(100, len(self.current_data))

            self.sensor_result_table.setColumnCount(n_sensors + 1)
            self.sensor_result_table.setRowCount(n_preview_rows)

            # 时间列作为第一列
            time_col = '时间' if '时间' in self.current_data.columns else self.current_data.columns[0]
            headers = ['时间'] + list(results.keys())
            self.sensor_result_table.setHorizontalHeaderLabels(headers)

            # 填充数据
            for i in range(n_preview_rows):
                # 时间列
                t = self.current_data[time_col].values[i]
                self.sensor_result_table.setItem(i, 0, QTableWidgetItem(str(t)))
                # 各传感器列
                for j, sensor_id in enumerate(results.keys()):
                    val = results[sensor_id][i] if i < len(results[sensor_id]) else None
                    if val is not None:
                        self.sensor_result_table.setItem(i, j + 1, QTableWidgetItem(f'{val:.2f}'))
                    else:
                        self.sensor_result_table.setItem(i, j + 1, QTableWidgetItem('N/A'))

            self.sensor_result_table.resizeColumnsToContents()
            self.sensor_results = results  # 保存计算结果
            self.status_bar.showMessage(f'传感器计算完成，预览显示前{n_preview_rows}行')

        except Exception as e:
            import traceback
            QMessageBox.critical(self, '错误', f'计算失败: {str(e)}\n\n{traceback.format_exc()}')

    def save_sensor_data(self):
        """保存传感器数据到指定路径"""
        if self.current_data is None:
            QMessageBox.warning(self, '警告', '请先加载数据')
            return

        filename = self.sensor_filename_input.text().strip()
        if not filename:
            QMessageBox.warning(self, '警告', '请输入文件名')
            return

        fmt = self.sensor_format_combo.currentText()
        if fmt == 'csv':
            file_filter = 'CSV Files (*.csv)'
            ext = '.csv'
        elif fmt == 'txt':
            file_filter = 'Text Files (*.txt)'
            ext = '.txt'
        else:
            file_filter = 'Excel Files (*.xlsx)'
            ext = '.xlsx'

        file_path, _ = QFileDialog.getSaveFileName(
            self, '保存传感器数据',
            filename + ext,
            file_filter
        )
        if file_path:
            try:
                ref_row = self.ref_row_spin.value()
                self.sensor_system.set_reference_row(ref_row)
                results = self.sensor_system.calculate(self.current_data, self.current_columns)

                time_col = '时间' if '时间' in self.current_data.columns else self.current_data.columns[0]

                export_df = pd.DataFrame()
                export_df['时间'] = self.current_data[time_col]
                for sensor_id, values in results.items():
                    export_df[sensor_id] = values

                if fmt == 'csv':
                    export_df.to_csv(file_path, index=False, encoding='utf-8-sig')
                elif fmt == 'txt':
                    export_df.to_csv(file_path, index=False, sep='\t', encoding='utf-8-sig')
                else:
                    export_df.to_excel(file_path, index=False)

                QMessageBox.information(self, '成功', f'传感器数据已保存到:\n{file_path}')
            except Exception as e:
                QMessageBox.critical(self, '错误', f'保存失败: {str(e)}')

    def export_sensor_data(self):
        """导出传感器数据"""
        if self.current_data is None:
            QMessageBox.warning(self, '警告', '请先计算传感器数据')
            return

        file_path, _ = QFileDialog.getSaveFileName(
            self, '导出传感器数据', '', 'CSV Files (*.csv)'
        )
        if file_path:
            try:
                results = self.sensor_system.calculate(self.current_data, self.current_columns)
                time_col = '时间' if '时间' in self.current_data.columns else self.current_data.columns[0]

                export_df = pd.DataFrame()
                export_df['时间'] = self.current_data[time_col]
                for sensor_id, values in results.items():
                    export_df[sensor_id] = values

                export_df.to_csv(file_path, index=False)
                QMessageBox.information(self, '成功', f'数据已导出到: {file_path}')
            except Exception as e:
                QMessageBox.critical(self, '错误', f'导出失败: {str(e)}')

    def create_analysis_tab(self):
        layout = QVBoxLayout()

        # ============ 数据源选择 ============
        source_layout = QHBoxLayout()
        source_layout.addWidget(QLabel('数据源:'))
        self.data_source_combo = QComboBox()
        self.data_source_combo.addItems(['原始数据', '物理量'])
        self.data_source_combo.currentTextChanged.connect(self.on_data_source_changed)
        source_layout.addWidget(self.data_source_combo)

        self.analysis_sensor_list = QListWidget()
        self.analysis_sensor_list.setEnabled(False)
        self.analysis_sensor_list.setMaximumHeight(120)
        self.analysis_sensor_list.setSelectionMode(QListWidget.SelectionMode.NoSelection)
        source_layout.addWidget(self.analysis_sensor_list)

        self.refresh_sensors_btn = QPushButton('刷新')
        self.refresh_sensors_btn.clicked.connect(self.refresh_analysis_sensors)
        source_layout.addWidget(self.refresh_sensors_btn)

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

        self.apply_range_btn = QPushButton('应用范围')
        self.apply_range_btn.clicked.connect(self.run_analysis)
        range_layout.addWidget(self.apply_range_btn)

        range_layout.addStretch()
        layout.addLayout(range_layout)

        # ============ 图表显示区域 ============
        self.chart_figure = Figure(figsize=(8, 4))
        self.chart_canvas = FigureCanvasQTAgg(self.chart_figure)

        chart_layout = QVBoxLayout()
        self.chart_widget = QWidget()
        self.chart_widget.setLayout(chart_layout)
        self.chart_widget.setMinimumHeight(300)

        # 使用matplotlib内置工具栏（支持框选放大、拖动平移等功能）
        self.chart_toolbar = NavigationToolbar2QT(self.chart_canvas, self.chart_widget)
        self.chart_toolbar.setWindowTitle('图表工具')

        chart_layout.addWidget(self.chart_toolbar)
        chart_layout.addWidget(self.chart_canvas)

        self.ax = self.chart_figure.add_subplot(111)  # 初始化Axes

        layout.addWidget(self.chart_widget)

        # ============ 统计信息显示 ============
        stats_group = QGroupBox('统计信息')
        stats_layout = QVBoxLayout()

        # 曲线选择
        select_layout = QHBoxLayout()
        select_layout.addWidget(QLabel('选择曲线:'))
        self.stats_curve_combo = QComboBox()
        self.stats_curve_combo.currentTextChanged.connect(self.on_stats_curve_changed)
        select_layout.addWidget(self.stats_curve_combo)

        select_layout.addStretch()
        stats_layout.addLayout(select_layout)

        # 统计指标网格
        stats_grid = QGridLayout()
        self.stats_labels = {}
        stats_items = [
            ('最大值', 'max'),
            ('最小值', 'min'),
            ('平均值', 'mean'),
            ('标准差', 'std'),
            ('峰峰值', 'peak_to_peak'),
            ('有效值', 'rms'),
            ('偏度', 'skew'),
            ('峰度', 'kurtosis'),
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
        self.compare_btn.clicked.connect(self.compare_curves)
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
        self.export_chart_btn.clicked.connect(self.export_chart)
        btn_layout.addWidget(self.export_chart_btn)

        btn_layout.addStretch()
        layout.addLayout(btn_layout)

        # 初始化图表（在create_analysis_tab时已经初始化）

        layout.addStretch()
        self.analysis_tab.setLayout(layout)

    def create_report_tab(self):
        # 信息整合Tab - 新设计
        main_layout = QHBoxLayout()

        # 左侧：功能选项列表
        left_panel = QWidget()
        left_layout = QVBoxLayout()
        left_layout.setContentsMargins(0, 0, 0, 0)

        # 信息整合标题
        title_label = QLabel('信息整合')
        title_label.setStyleSheet('font-size: 16px; font-weight: bold; padding: 5px;')
        left_layout.addWidget(title_label)

        # 功能按钮列表
        self.info_menu_list = QListWidget()
        self.info_menu_list.setMaximumWidth(180)
        self.info_menu_list.addItem('Word模板')
        self.info_menu_list.addItem('PPT模板')
        self.info_menu_list.addItem('项目内容')
        self.info_menu_list.addItem('Word报告生成')
        self.info_menu_list.addItem('PPT报告生成')
        self.info_menu_list.addItem('AI诊断')
        self.info_menu_list.addItem('项目资料管理')
        self.info_menu_list.currentRowChanged.connect(self.on_info_menu_changed)
        left_layout.addWidget(self.info_menu_list)

        left_panel.setLayout(left_layout)
        main_layout.addWidget(left_panel)

        # 右侧：内容面板
        self.report_content_stack = QStackedWidget()
        main_layout.addWidget(self.report_content_stack, 1)

        # Word模板页面
        word_template_page = self.create_word_template_page()
        self.report_content_stack.addWidget(word_template_page)

        # PPT模板页面
        ppt_template_page = self.create_ppt_template_page()
        self.report_content_stack.addWidget(ppt_template_page)

        # 项目内容页面
        project_content_page = self.create_project_content_page()
        self.report_content_stack.addWidget(project_content_page)

        # Word报告生成页面
        word_report_page = self.create_word_report_page()
        self.report_content_stack.addWidget(word_report_page)

        # PPT报告生成页面
        ppt_report_page = self.create_ppt_report_page()
        self.report_content_stack.addWidget(ppt_report_page)

        # AI诊断页面
        ai_diagnosis_page = self.create_ai_diagnosis_page()
        self.report_content_stack.addWidget(ai_diagnosis_page)

        # 项目资料管理页面
        project_manage_page = self.create_project_manage_page()
        self.report_content_stack.addWidget(project_manage_page)

        self.report_tab.setLayout(main_layout)

        # 检查Ollama状态
        QTimer.singleShot(500, self.check_ollama_status)

    def create_word_template_page(self):
        """Word模板配置页面"""
        page = QWidget()
        layout = QVBoxLayout()

        # 说明标签
        info_label = QLabel('Word模板：导入参考模板，主要用于参考报告框架、格式、排版等')
        info_label.setStyleSheet('color: #666; padding: 10px;')
        layout.addWidget(info_label)

        # 当前模板状态
        status_group = QGroupBox('当前模板状态')
        status_layout = QFormLayout()

        self.word_template_path = QLabel('未选择模板')
        self.word_template_path.setStyleSheet('color: #999;')
        status_layout.addRow('模板路径:', self.word_template_path)

        self.word_template_preview = QTextEdit()
        self.word_template_preview.setReadOnly(True)
        self.word_template_preview.setMaximumHeight(150)
        self.word_template_preview.setPlaceholderText('模板预览将显示在这里...')
        status_layout.addRow('模板内容:', self.word_template_preview)

        status_group.setLayout(status_layout)
        layout.addWidget(status_group)

        # 操作按钮
        btn_layout = QHBoxLayout()
        select_btn = QPushButton('选择Word模板')
        select_btn.clicked.connect(self.select_word_template)
        btn_layout.addWidget(select_btn)

        clear_btn = QPushButton('清除模板')
        clear_btn.clicked.connect(self.clear_word_template)
        btn_layout.addWidget(clear_btn)

        layout.addLayout(btn_layout)

        # AI模板分析结果
        ai_group = QGroupBox('AI模板分析结果')
        ai_layout = QVBoxLayout()

        self.word_template_analysis = QTextEdit()
        self.word_template_analysis.setReadOnly(True)
        self.word_template_analysis.setPlaceholderText('AI分析结果将显示在这里...')
        ai_layout.addWidget(self.word_template_analysis)

        analyze_btn = QPushButton('AI分析模板特征')
        analyze_btn.clicked.connect(self.analyze_word_template)
        ai_layout.addWidget(analyze_btn)

        ai_group.setLayout(ai_layout)
        layout.addWidget(ai_group)

        layout.addStretch()
        page.setLayout(layout)
        return page

    def create_ppt_template_page(self):
        """PPT模板配置页面"""
        page = QWidget()
        layout = QVBoxLayout()

        info_label = QLabel('PPT模板：导入参考模板，主要用于参考报告框架、格式、排版等')
        info_label.setStyleSheet('color: #666; padding: 10px;')
        layout.addWidget(info_label)

        status_group = QGroupBox('当前模板状态')
        status_layout = QFormLayout()

        self.ppt_template_path = QLabel('未选择模板')
        self.ppt_template_path.setStyleSheet('color: #999;')
        status_layout.addRow('模板路径:', self.ppt_template_path)

        self.ppt_template_preview = QTextEdit()
        self.ppt_template_preview.setReadOnly(True)
        self.ppt_template_preview.setMaximumHeight(150)
        self.ppt_template_preview.setPlaceholderText('模板预览将显示在这里...')
        status_layout.addRow('模板内容:', self.ppt_template_preview)

        status_group.setLayout(status_layout)
        layout.addWidget(status_group)

        btn_layout = QHBoxLayout()
        select_btn = QPushButton('选择PPT模板')
        select_btn.clicked.connect(self.select_ppt_template)
        btn_layout.addWidget(select_btn)

        clear_btn = QPushButton('清除模板')
        clear_btn.clicked.connect(self.clear_ppt_template)
        btn_layout.addWidget(clear_btn)

        layout.addLayout(btn_layout)

        ai_group = QGroupBox('AI模板分析结果')
        ai_layout = QVBoxLayout()

        self.ppt_template_analysis = QTextEdit()
        self.ppt_template_analysis.setReadOnly(True)
        self.ppt_template_analysis.setPlaceholderText('AI分析结果将显示在这里...')
        ai_layout.addWidget(self.ppt_template_analysis)

        analyze_btn = QPushButton('AI分析模板特征')
        analyze_btn.clicked.connect(self.analyze_ppt_template)
        ai_layout.addWidget(analyze_btn)

        ai_group.setLayout(ai_layout)
        layout.addWidget(ai_group)

        layout.addStretch()
        page.setLayout(layout)
        return page

    def create_project_content_page(self):
        """项目内容配置页面"""
        page = QWidget()
        layout = QVBoxLayout()

        info_label = QLabel('项目内容：导入要生成项目的各种资料，包括文字、图片、图纸等')
        info_label.setStyleSheet('color: #666; padding: 10px;')
        layout.addWidget(info_label)

        # 项目资料列表
        project_group = QGroupBox('项目资料')
        project_layout = QVBoxLayout()

        self.project_files_list = QListWidget()
        self.project_files_list.setMaximumHeight(200)
        project_layout.addWidget(self.project_files_list)

        btn_layout = QHBoxLayout()
        add_file_btn = QPushButton('添加文件')
        add_file_btn.clicked.connect(self.add_project_file)
        btn_layout.addWidget(add_file_btn)

        add_folder_btn = QPushButton('添加文件夹')
        add_folder_btn.clicked.connect(self.add_project_folder)
        btn_layout.addWidget(add_folder_btn)

        remove_btn = QPushButton('移除选中')
        remove_btn.clicked.connect(self.remove_project_file)
        btn_layout.addWidget(remove_btn)

        project_layout.addLayout(btn_layout)
        project_group.setLayout(project_layout)
        layout.addWidget(project_group)

        # AI资料分析
        ai_group = QGroupBox('AI资料分析')
        ai_layout = QVBoxLayout()

        self.project_content_analysis = QTextEdit()
        self.project_content_analysis.setReadOnly(True)
        self.project_content_analysis.setPlaceholderText('AI分析项目资料结果将显示在这里...')
        ai_layout.addWidget(self.project_content_analysis)

        analyze_btn = QPushButton('AI分析项目资料')
        analyze_btn.clicked.connect(self.analyze_project_content)
        ai_layout.addWidget(analyze_btn)

        ai_group.setLayout(ai_layout)
        layout.addWidget(ai_group)

        layout.addStretch()
        page.setLayout(layout)
        return page

    def create_word_report_page(self):
        """Word报告生成页面"""
        page = QWidget()
        layout = QVBoxLayout()

        info_label = QLabel('Word报告生成：根据模板和项目资料生成Word报告')
        info_label.setStyleSheet('color: #666; padding: 10px;')
        layout.addWidget(info_label)

        # 配置区域
        config_group = QGroupBox('报告配置')
        config_layout = QFormLayout()

        self.word_report_title = QLineEdit()
        self.word_report_title.setText('数据分析报告')
        config_layout.addRow('报告标题:', self.word_report_title)

        self.word_report_author = QLineEdit()
        self.word_report_author.setText('DataProcessor Pro')
        config_layout.addRow('作者:', self.word_report_author)

        config_group.setLayout(config_layout)
        layout.addWidget(config_group)

        # 当前状态
        status_group = QGroupBox('生成状态')
        status_layout = QVBoxLayout()

        self.word_gen_template_status = QLabel('模板: 未选择')
        status_layout.addWidget(self.word_gen_template_status)

        self.word_gen_project_status = QLabel('项目资料: 未添加')
        status_layout.addWidget(self.word_gen_project_status)

        self.word_gen_data_status = QLabel('数据: 未加载')
        status_layout.addWidget(self.word_gen_data_status)

        status_group.setLayout(status_layout)
        layout.addWidget(status_group)

        # 操作按钮
        btn_layout = QHBoxLayout()
        preview_btn = QPushButton('预览AI生成内容')
        preview_btn.clicked.connect(self.preview_word_report)
        btn_layout.addWidget(preview_btn)

        generate_btn = QPushButton('生成Word报告')
        generate_btn.setStyleSheet('background-color: #1890ff; color: white;')
        generate_btn.clicked.connect(self.generate_word_report)
        btn_layout.addWidget(generate_btn)

        layout.addLayout(btn_layout)

        layout.addStretch()
        page.setLayout(layout)
        return page

    def create_ppt_report_page(self):
        """PPT报告生成页面"""
        page = QWidget()
        layout = QVBoxLayout()

        info_label = QLabel('PPT报告生成：根据模板和项目资料生成PPT报告')
        info_label.setStyleSheet('color: #666; padding: 10px;')
        layout.addWidget(info_label)

        config_group = QGroupBox('报告配置')
        config_layout = QFormLayout()

        self.ppt_report_title = QLineEdit()
        self.ppt_report_title.setText('数据分析报告')
        config_layout.addRow('报告标题:', self.ppt_report_title)

        self.ppt_report_author = QLineEdit()
        self.ppt_report_author.setText('DataProcessor Pro')
        config_layout.addRow('作者:', self.ppt_report_author)

        config_group.setLayout(config_layout)
        layout.addWidget(config_group)

        status_group = QGroupBox('生成状态')
        status_layout = QVBoxLayout()

        self.ppt_gen_template_status = QLabel('模板: 未选择')
        status_layout.addWidget(self.ppt_gen_template_status)

        self.ppt_gen_project_status = QLabel('项目资料: 未添加')
        status_layout.addWidget(self.ppt_gen_project_status)

        self.ppt_gen_data_status = QLabel('数据: 未加载')
        status_layout.addWidget(self.ppt_gen_data_status)

        status_group.setLayout(status_layout)
        layout.addWidget(status_group)

        btn_layout = QHBoxLayout()
        preview_btn = QPushButton('预览AI生成内容')
        preview_btn.clicked.connect(self.preview_ppt_report)
        btn_layout.addWidget(preview_btn)

        generate_btn = QPushButton('生成PPT报告')
        generate_btn.setStyleSheet('background-color: #1890ff; color: white;')
        generate_btn.clicked.connect(self.generate_ppt_report)
        btn_layout.addWidget(generate_btn)

        layout.addLayout(btn_layout)
        layout.addStretch()
        page.setLayout(layout)
        return page

    def create_ai_diagnosis_page(self):
        """AI诊断页面 - 独立的AI诊断分析界面"""
        page = QWidget()
        layout = QVBoxLayout()

        info_label = QLabel('AI诊断：基于物理量数据和需求配置进行智能分析')
        info_label.setStyleSheet('color: #666; padding: 10px;')
        layout.addWidget(info_label)

        # 需求配置区域
        config_group = QGroupBox('需求配置')
        config_layout = QHBoxLayout()

        self.ai_req_files_list = QListWidget()
        self.ai_req_files_list.setMaximumHeight(80)
        config_layout.addWidget(self.ai_req_files_list)

        req_btn_layout = QVBoxLayout()
        select_req_btn = QPushButton('选择需求文件')
        select_req_btn.clicked.connect(self.select_requirement_files)
        req_btn_layout.addWidget(select_req_btn)

        clear_req_btn = QPushButton('清除')
        clear_req_btn.clicked.connect(self.clear_requirement_files)
        req_btn_layout.addWidget(clear_req_btn)

        config_layout.addLayout(req_btn_layout)
        config_group.setLayout(config_layout)
        layout.addWidget(config_group)

        # 模型选择 - 本地GGUF文件
        model_group = QGroupBox('本地模型')
        model_layout = QHBoxLayout()

        model_layout.addWidget(QLabel('模型文件:'))
        self.ai_model_path = QLineEdit()
        self.ai_model_path.setPlaceholderText('选择GGUF模型文件路径...')
        self.ai_model_path.setMinimumWidth(200)
        model_layout.addWidget(self.ai_model_path)

        select_model_btn = QPushButton('浏览...')
        select_model_btn.clicked.connect(self.select_local_model)
        model_layout.addWidget(select_model_btn)

        self.ai_status_label = QLabel('状态: 未加载')
        model_layout.addWidget(self.ai_status_label)

        unload_btn = QPushButton('卸载模型')
        unload_btn.clicked.connect(self.unload_local_model)
        model_layout.addWidget(unload_btn)
        self.ollama_toggle_btn = unload_btn

        model_group.setLayout(model_layout)
        layout.addWidget(model_group)

        # 诊断结果区域
        result_group = QGroupBox('AI诊断结果')
        result_layout = QVBoxLayout()

        self.ai_diagnosis_result = QTextEdit()
        self.ai_diagnosis_result.setReadOnly(True)
        self.ai_diagnosis_result.setMinimumHeight(150)
        self.ai_diagnosis_result.setPlaceholderText('AI诊断结果将显示在这里...')
        result_layout.addWidget(self.ai_diagnosis_result)

        result_btn_layout = QHBoxLayout()
        run_diagnosis_btn = QPushButton('运行AI诊断')
        run_diagnosis_btn.setStyleSheet('background-color: #1890ff; color: white;')
        run_diagnosis_btn.clicked.connect(self.run_ai_diagnosis)
        result_btn_layout.addWidget(run_diagnosis_btn)

        clear_result_btn = QPushButton('清除结果')
        clear_result_btn.clicked.connect(lambda: self.ai_diagnosis_result.clear())
        result_btn_layout.addWidget(clear_result_btn)

        result_layout.addLayout(result_btn_layout)
        result_group.setLayout(result_layout)
        layout.addWidget(result_group)

        # 交互问答区域
        chat_group = QGroupBox('AI交互问答')
        chat_layout = QVBoxLayout()

        self.ai_chat_input = QTextEdit()
        self.ai_chat_input.setMaximumHeight(60)
        self.ai_chat_input.setPlaceholderText('输入您的问题，例如：数据异常的可能原因是什么？')
        chat_layout.addWidget(self.ai_chat_input)

        chat_btn_layout = QHBoxLayout()
        ask_btn = QPushButton('向AI提问')
        ask_btn.clicked.connect(self.ai_chat_query)
        chat_btn_layout.addWidget(ask_btn)

        clear_chat_btn = QPushButton('清除对话')
        clear_chat_btn.clicked.connect(self.clear_ai_chat)
        chat_btn_layout.addWidget(clear_chat_btn)

        chat_layout.addLayout(chat_btn_layout)

        self.ai_chat_result = QTextEdit()
        self.ai_chat_result.setReadOnly(True)
        self.ai_chat_result.setMinimumHeight(100)
        self.ai_chat_result.setPlaceholderText('AI回答将显示在这里...')
        chat_layout.addWidget(self.ai_chat_result)

        chat_group.setLayout(chat_layout)
        layout.addWidget(chat_group)

        layout.addStretch()
        page.setLayout(layout)
        return page

    def create_project_manage_page(self):
        """项目资料管理页面"""
        page = QWidget()
        layout = QVBoxLayout()

        info_label = QLabel('项目资料管理：管理已导入的项目资料')
        info_label.setStyleSheet('color: #666; padding: 10px;')
        layout.addWidget(info_label)

        # 资料统计
        stats_group = QGroupBox('资料统计')
        stats_layout = QFormLayout()

        self.project_stats_files = QLabel('文件数: 0')
        stats_layout.addRow('文件数:', self.project_stats_files)

        self.project_stats_size = QLabel('总大小: 0 KB')
        stats_layout.addRow('总大小:', self.project_stats_size)

        stats_group.setLayout(stats_layout)
        layout.addWidget(stats_group)

        # 资料列表
        files_group = QGroupBox('资料列表')
        files_layout = QVBoxLayout()

        self.all_project_files_list = QListWidget()
        files_layout.addWidget(self.all_project_files_list)

        btn_layout = QHBoxLayout()
        refresh_btn = QPushButton('刷新')
        refresh_btn.clicked.connect(self.refresh_project_files)
        btn_layout.addWidget(refresh_btn)

        clear_btn = QPushButton('清空全部')
        clear_btn.clicked.connect(self.clear_all_project_files)
        btn_layout.addWidget(clear_btn)

        files_layout.addLayout(btn_layout)
        files_group.setLayout(files_layout)
        layout.addWidget(files_group)

        layout.addStretch()
        page.setLayout(layout)
        return page

    def on_info_menu_changed(self, row):
        """切换信息整合功能页面"""
        self.report_content_stack.setCurrentIndex(row)

    # ============ AI诊断页面相关方法 ============

    def select_requirement_files(self):
        """选择需求配置文件"""
        file_paths, _ = QFileDialog.getOpenFileNames(
            self, '选择需求文件', '',
            'All Files (*);;Text Files (*.txt);;Word Documents (*.docx)'
        )
        for path in file_paths:
            self.ai_req_files_list.addItem(path)

    def clear_requirement_files(self):
        """清除需求文件列表"""
        self.ai_req_files_list.clear()

    def read_requirement_files(self) -> str:
        """读取所有需求文件的内容"""
        content_parts = []
        for i in range(self.ai_req_files_list.count()):
            file_path = self.ai_req_files_list.item(i).text()
            try:
                if file_path.endswith('.txt'):
                    with open(file_path, 'r', encoding='utf-8') as f:
                        content_parts.append(f.read())
                elif file_path.endswith('.docx'):
                    from docx import Document
                    doc = Document(file_path)
                    content_parts.append('\n'.join([p.text for p in doc.paragraphs if p.text]))
            except Exception as e:
                print(f"读取文件失败 {file_path}: {e}")
        return '\n\n'.join(content_parts) if content_parts else ''

    def run_ai_diagnosis(self):
        """运行AI诊断"""
        if not self.ollama_client:
            QMessageBox.warning(self, '警告', 'Ollama客户端未初始化')
            return

        if not self.ollama_client.is_available():
            QMessageBox.warning(self, '警告', '请先启动Ollama服务')
            return

        if self.current_data is None or self.current_data.empty:
            QMessageBox.warning(self, '警告', '请先加载数据')
            return

        try:
            # 获取物理量数据
            all_data = []
            stats = {}
            if self.sensor_results:
                sensor_id = list(self.sensor_results.keys())[0]
                data = list(self.sensor_results[sensor_id])
                arr = np.array(data)
                arr = arr[~np.isnan(arr)]
                if len(arr) > 10:
                    all_data = arr.tolist()
                    stats = {
                        'max': float(np.max(arr)),
                        'min': float(np.min(arr)),
                        'mean': float(np.mean(arr)),
                        'std': float(np.std(arr)),
                        'peak_to_peak': float(np.max(arr) - np.min(arr)),
                        'rms': float(np.sqrt(np.mean(arr ** 2))),
                        'count': len(arr)
                    }
            else:
                arr = self.current_data.select_dtypes(include=[np.number]).values
                arr = arr[~np.isnan(arr)]
                if len(arr) > 10:
                    all_data = arr.tolist()
                    stats = {
                        'max': float(np.max(arr)),
                        'min': float(np.min(arr)),
                        'mean': float(np.mean(arr)),
                        'std': float(np.std(arr)),
                        'peak_to_peak': float(np.max(arr) - np.min(arr)),
                        'rms': float(np.sqrt(np.mean(arr ** 2))),
                        'count': len(arr)
                    }

            if len(all_data) < 10:
                self.ai_diagnosis_result.setText('数据不足以进行诊断分析')
                return

            # 读取需求文件
            req_content = self.read_requirement_files()

            # 构建完整提示词
            sample_count = min(50, len(all_data))
            sample_str = ', '.join([f'{v:.2f}' for v in all_data[:sample_count]])

            prompt = f"""你是一个结构健康监测专家。请根据以下信息进行数据分析：

1. 物理量数据：
- 数据点数：{stats.get('count', 0)}
- 样本值(前{sample_count}个)：[{sample_str}]
- 统计：最大值={stats.get('max', 0):.2f}, 最小值={stats.get('min', 0):.2f}, 均值={stats.get('mean', 0):.2f}, 标准差={stats.get('std', 0):.2f}, 峰峰值={stats.get('peak_to_peak', 0):.2f}, RMS={stats.get('rms', 0):.2f}"""

            if req_content:
                prompt += f"\n\n2. 需求配置内容：\n{req_content}"

            prompt += """

请进行以下分析：
1. 数据整体状态评估（正常/异常/需关注）
2. 数据趋势判断（稳定/波动/上升/下降）
3. 异常点或离群值检测
4. 数据质量评估

如有不清楚的地方，请通过对话框向我确认。回复简洁明了，不超过300字。"""

            self.ai_diagnosis_result.setText('正在分析，请稍候...')
            self.ai_diagnosis_stream = ""  # 用于流式接收

            # 使用子线程调用本地模型
            from py.llama_generate_thread import LlamaGenerateThread
            model_path = self.ai_model_path.text()
            if not model_path:
                self.ai_diagnosis_result.setText('请先选择GGUF模型文件')
                return
            self.ollama_gen_thread = LlamaGenerateThread(
                model_path=model_path,
                prompt=prompt
            )
            self.ollama_gen_thread.token_received.connect(self._on_diagnosis_token)
            self.ollama_gen_thread.finished.connect(self._on_diagnosis_finished)
            self.ollama_gen_thread.error.connect(self._on_diagnosis_error)
            self.ollama_gen_thread.start()

        except Exception as e:
            self.ai_diagnosis_result.setText(f'诊断失败: {str(e)}')

    def _on_diagnosis_token(self, token):
        """接收流式token"""
        if not hasattr(self, 'ai_diagnosis_stream'):
            self.ai_diagnosis_stream = ""
        self.ai_diagnosis_stream += token
        self.ai_diagnosis_result.setText(self.ai_diagnosis_stream)

    def _on_diagnosis_finished(self, result):
        """诊断完成"""
        if result and len(result) >= 5:
            self.ai_diagnosis_result.setText(result)
        else:
            self.ai_diagnosis_result.setText("AI诊断失败，请尝试其他模型")

    def _on_diagnosis_error(self, err_msg):
        """诊断出错"""
        self.ai_diagnosis_result.setText(f'诊断失败: {err_msg}')

    def ai_chat_query(self):
        """AI交互问答"""
        if not self.ollama_client:
            QMessageBox.warning(self, '警告', 'Ollama客户端未初始化')
            return

        if not self.ollama_client.is_available():
            QMessageBox.warning(self, '警告', '请先启动Ollama服务')
            return

        user_question = self.ai_chat_input.toPlainText().strip()
        if not user_question:
            QMessageBox.warning(self, '警告', '请输入问题')
            return

        try:
            # 获取物理量数据
            stats = {}
            all_data = []
            if self.sensor_results:
                sensor_id = list(self.sensor_results.keys())[0]
                data = list(self.sensor_results[sensor_id])
                arr = np.array(data)
                arr = arr[~np.isnan(arr)]
                if len(arr) > 10:
                    all_data = arr.tolist()
                    stats = {
                        'max': float(np.max(arr)),
                        'min': float(np.min(arr)),
                        'mean': float(np.mean(arr)),
                        'std': float(np.std(arr)),
                        'count': len(arr)
                    }
            else:
                arr = self.current_data.select_dtypes(include=[np.number]).values
                arr = arr[~np.isnan(arr)]
                if len(arr) > 10:
                    all_data = arr.tolist()
                    stats = {
                        'max': float(np.max(arr)),
                        'min': float(np.min(arr)),
                        'mean': float(np.mean(arr)),
                        'std': float(np.std(arr)),
                        'count': len(arr)
                    }

            # 读取需求文件
            req_content = self.read_requirement_files()

            # 构建提示词
            context = f"当前数据分析上下文：\n- 数据点数：{stats.get('count', 0)}\n- 统计：最大值={stats.get('max', 0):.2f}, 最小值={stats.get('min', 0):.2f}, 均值={stats.get('mean', 0):.2f}, 标准差={stats.get('std', 0):.2f}\n"
            if req_content:
                context += f"- 需求配置：{req_content[:500]}...\n"
            if all_data:
                sample_str = ', '.join([f'{v:.2f}' for v in all_data[:20]])
                context += f"- 数据样本(前20个)：[{sample_str}]\n"

            full_prompt = f"""{context}

用户问题：{user_question}

请基于以上数据分析回答用户问题。如有不清楚的请通过对话框向我确认。回复简洁明了，不超过200字。"""

            self.ai_chat_result.setText('正在处理，请稍候...')
            self.ai_chat_stream = ""
            self.ai_chat_user_q = user_question

            from py.llama_generate_thread import LlamaGenerateThread
            model_path = self.ai_model_path.text()
            if not model_path:
                self.ai_chat_result.setText('请先选择GGUF模型文件')
                return
            self.ollama_chat_thread = LlamaGenerateThread(
                model_path=model_path,
                prompt=full_prompt
            )
            self.ollama_chat_thread.token_received.connect(self._on_chat_token)
            self.ollama_chat_thread.finished.connect(self._on_chat_finished)
            self.ollama_chat_thread.error.connect(self._on_chat_error)
            self.ollama_chat_thread.start()

        except Exception as e:
            self.ai_chat_result.setText(f'查询失败: {str(e)}')

    def _on_chat_token(self, token):
        """接收聊天流式token"""
        if not hasattr(self, 'ai_chat_stream'):
            self.ai_chat_stream = ""
        self.ai_chat_stream += token
        self.ai_chat_result.setText(f'正在处理，请稍候...\n\n{self.ai_chat_stream}')

    def _on_chat_finished(self, result):
        """聊天完成"""
        if result and len(result) >= 5:
            current = self.ai_chat_result.toPlainText()
            # 移除"正在处理"前缀
            if current.startswith('正在处理'):
                parts = current.split('\n\n', 1)
                if len(parts) > 1:
                    current = parts[1]
            if current:
                self.ai_chat_result.append('\n---\n')
            self.ai_chat_result.append(f'用户: {getattr(self, "ai_chat_user_q", "")}')
            self.ai_chat_result.append(f'AI: {result}')
            self.ai_chat_input.clear()
        else:
            self.ai_chat_result.setText('AI未返回有效响应，请尝试其他问题')

    def _on_chat_error(self, err_msg):
        """聊天出错"""
        self.ai_chat_result.setText(f'查询失败: {err_msg}')

    def clear_ai_chat(self):
        """清除对话"""
        self.ai_chat_result.clear()
        self.ai_chat_input.clear()
        # 清除聊天历史（滑动窗口）
        if hasattr(self, 'chat_history'):
            self.chat_history = []

    def on_ollama_model_changed(self, model_name):
        """Ollama模型切换"""
        if self.ollama_client:
            self.ollama_client.model = model_name
            # 如果服务已连接，刷新状态
            if self.ollama_client.is_available():
                self.ai_status_label.setText(f'Ollama状态: 已连接 ({model_name})')
                self.ai_status_label.setStyleSheet('color: green;')

    def refresh_ollama_models(self):
        """刷新Ollama可用模型列表"""
        if self.ollama_client and self.ollama_client.is_available():
            try:
                import requests
                response = requests.get('http://localhost:11434/api/tags', timeout=2)
                if response.status_code == 200:
                    models = response.json().get('models', [])
                    model_list = [m['name'] for m in models]
                    if model_list:
                        current = self.ai_model_combo.currentText()
                        self.ai_model_combo.clear()
                        self.ai_model_combo.addItems(model_list)
                        if current in model_list:
                            self.ai_model_combo.setCurrentText(current)
            except:
                pass

    # ============ Word模板操作 ============
    def select_word_template(self):
        """选择Word模板"""
        file_path, _ = QFileDialog.getOpenFileName(
            self, '选择Word模板', '', 'Word Documents (*.docx);;All Files (*)'
        )
        if file_path:
            self.word_template_path.setText(file_path)
            self.word_template_path.setStyleSheet('color: #333;')
            # 预览模板内容
            try:
                doc = Document(file_path)
                preview = '\n'.join([p.text for p in doc.paragraphs if p.text])
                self.word_template_preview.setText(preview[:2000] if preview else '模板无文本内容')
            except Exception as e:
                self.word_template_preview.setText(f'预览失败: {str(e)}')

    def clear_word_template(self):
        """清除Word模板"""
        self.word_template_path.setText('未选择模板')
        self.word_template_path.setStyleSheet('color: #999;')
        self.word_template_preview.clear()
        self.word_template_analysis.clear()

    def analyze_word_template(self):
        """AI分析Word模板特征"""
        template_path = self.word_template_path.text()
        if template_path == '未选择模板':
            QMessageBox.warning(self, '警告', '请先选择Word模板')
            return

        if not self.ollama_client or not self.ollama_client.is_available():
            QMessageBox.warning(self, '警告', '请先启动Ollama服务')
            return

        try:
            doc = Document(template_path)
            content = '\n'.join([p.text for p in doc.paragraphs if p.text])
            prompt = f"""Analyze this Word template features:
1. Document structure (heading levels, chapter organization)
2. Format style (paragraph spacing, font styles, alignment)
3. Content layout (use of tables, images, lists)
4. Language style (formality, use of professional terms)

Template content:
{content[:3000]}

Please summarize these features briefly so I can generate new reports based on this template."""
            self.word_template_analysis.setText('正在分析，请稍候...')

            from py.llama_generate_thread import LlamaGenerateThread
            model_path = self.ai_model_path.text()
            if not model_path:
                self.word_template_analysis.setText('请先选择GGUF模型文件')
                return
            self.word_template_thread = LlamaGenerateThread(
                model_path=model_path,
                prompt=prompt
            )
            self.word_template_thread.finished.connect(lambda r: self.word_template_analysis.setText(r if r else 'AI分析失败'))
            self.word_template_thread.error.connect(lambda e: self.word_template_analysis.setText(f'分析失败: {e}'))
            self.word_template_thread.start()
        except Exception as e:
            self.word_template_analysis.setText(f'分析失败: {str(e)}')

    # ============ PPT模板操作 ============
    def select_ppt_template(self):
        """选择PPT模板"""
        file_path, _ = QFileDialog.getOpenFileName(
            self, '选择PPT模板', '', 'PowerPoint Files (*.pptx);;All Files (*)'
        )
        if file_path:
            self.ppt_template_path.setText(file_path)
            self.ppt_template_path.setStyleSheet('color: #333;')
            self.ppt_template_preview.setText('PPT模板预览功能需要额外库支持，请手动查看原文件')

    def clear_ppt_template(self):
        """清除PPT模板"""
        self.ppt_template_path.setText('未选择模板')
        self.ppt_template_path.setStyleSheet('color: #999;')
        self.ppt_template_preview.clear()
        self.ppt_template_analysis.clear()

    def analyze_ppt_template(self):
        """AI分析PPT模板特征"""
        template_path = self.ppt_template_path.text()
        if template_path == '未选择模板':
            QMessageBox.warning(self, '警告', '请先选择PPT模板')
            return

        if not self.ollama_client or not self.ollama_client.is_available():
            QMessageBox.warning(self, '警告', '请先启动Ollama服务')
            return

        self.ppt_template_analysis.setText('PPT模板分析需要手动描述结构。提示：描述幻灯片数量、每页的标题和内容布局、配色风格等。')

    # ============ 项目内容操作 ============
    def add_project_file(self):
        """添加项目文件"""
        file_paths, _ = QFileDialog.getOpenFileNames(
            self, '添加项目文件', '', 'All Files (*);;Text Files (*.txt);;Image Files (*.png *.jpg *.jpeg);;Word Files (*.docx);;PDF Files (*.pdf)'
        )
        for path in file_paths:
            self.project_files_list.addItem(path)

    def add_project_folder(self):
        """添加项目文件夹"""
        folder = QFileDialog.getExistingDirectory(self, '选择项目文件夹')
        if folder:
            self.project_files_list.addItem(f'[文件夹] {folder}')

    def remove_project_file(self):
        """移除选中的项目文件"""
        for item in self.project_files_list.selectedItems():
            row = self.project_files_list.row(item)
            self.project_files_list.takeItem(row)

    def analyze_project_content(self):
        """AI分析项目资料"""
        count = self.project_files_list.count()
        if count == 0:
            QMessageBox.warning(self, '警告', '请先添加项目资料')
            return

        if not self.ollama_client or not self.ollama_client.is_available():
            QMessageBox.warning(self, '警告', '请先启动Ollama服务')
            return

        files = []
        for i in range(count):
            files.append(self.project_files_list.item(i).text())

        prompt = f"""Analyze these project files:
{chr(10).join(files[:20])}

Please summarize:
1. File type distribution
2. Main content areas
3. Key materials for the report

Only analyze the file list, don't read specific content."""
        self.project_content_analysis.setText('正在分析，请稍候...')

        from py.llama_generate_thread import LlamaGenerateThread
        model_path = self.ai_model_path.text()
        if not model_path:
            self.project_content_analysis.setText('请先选择GGUF模型文件')
            return
        self.project_content_thread = LlamaGenerateThread(
            model_path=model_path,
            prompt=prompt
        )
        self.project_content_thread.finished.connect(lambda r: self.project_content_analysis.setText(r if r else 'AI分析失败'))
        self.project_content_thread.error.connect(lambda e: self.project_content_analysis.setText(f'分析失败: {e}'))
        self.project_content_thread.start()

    # ============ Word报告生成 ============
    def preview_word_report(self):
        """预览Word报告AI生成内容"""
        QMessageBox.information(self, '提示', '请先选择Word模板和项目内容，然后点击"生成Word报告"')

    def generate_word_report(self):
        """生成Word报告"""
        if self.current_data is None or self.current_data.empty:
            QMessageBox.warning(self, '警告', '请先加载数据')
            return

        if not self.ollama_client or not self.ollama_client.is_available():
            QMessageBox.warning(self, '警告', '请先启动Ollama服务')
            return

        try:
            # 构建报告内容
            data_info = {
                'rows': len(self.current_data),
                'cols': len(self.current_data.columns),
                'columns': ', '.join(self.current_data.columns),
            }

            stats = {}
            if self.sensor_results:
                sensor_id = list(self.sensor_results.keys())[0]
                arr = np.array(list(self.sensor_results[sensor_id]))
                arr = arr[~np.isnan(arr)]
                if len(arr) > 0:
                    stats = {
                        'max': np.max(arr), 'min': np.min(arr), 'mean': np.mean(arr),
                        'std': np.std(arr), 'peak_to_peak': np.max(arr) - np.min(arr),
                        'rms': np.sqrt(np.mean(arr ** 2)),
                    }

            # 生成报告摘要
            summary = self.ollama_client.generate_report_summary(data_info, stats)

            # 创建Word文档
            doc = Document()
            doc.add_heading(self.word_report_title.text(), 0)

            # AI诊断摘要
            ai_diagnosis = self.ai_diagnosis_result.toPlainText()
            if ai_diagnosis and len(ai_diagnosis) > 10:
                doc.add_heading('AI智能诊断摘要', level=1)
                doc.add_paragraph(ai_diagnosis)

            # 数据概要
            doc.add_heading('数据概要', level=1)
            for key, value in data_info.items():
                doc.add_paragraph(f'{key}: {value}')

            # 统计摘要
            if stats:
                doc.add_heading('统计分析', level=1)
                for key, value in stats.items():
                    doc.add_paragraph(f'{key}: {value:.4f}')

            # AI生成的报告摘要
            if summary:
                doc.add_heading('AI报告摘要', level=1)
                doc.add_paragraph(summary)

            # 保存
            file_path, _ = QFileDialog.getSaveFileName(
                self, '保存Word报告', '', 'Word Documents (*.docx)'
            )
            if file_path:
                doc.save(file_path)
                QMessageBox.information(self, '成功', f'报告已保存到: {file_path}')

        except Exception as e:
            QMessageBox.critical(self, '错误', f'生成报告失败: {str(e)}')

    # ============ PPT报告生成 ============
    def preview_ppt_report(self):
        """预览PPT报告AI生成内容"""
        QMessageBox.information(self, '提示', 'PPT报告生成功能开发中')

    def generate_ppt_report(self):
        """生成PPT报告"""
        QMessageBox.information(self, '提示', 'PPT报告生成功能开发中，需要python-pptx库支持')

    # ============ 项目资料管理 ============
    def refresh_project_files(self):
        """刷新项目文件列表"""
        count = self.project_files_list.count()
        self.project_stats_files.setText(f'文件数: {count}')

    def clear_all_project_files(self):
        """清空所有项目文件"""
        reply = QMessageBox.question(self, '确认', '确定要清空所有项目文件吗？',
                                      QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
        if reply == QMessageBox.StandardButton.Yes:
            self.project_files_list.clear()
            self.refresh_project_files()

    # ============ 全局配置操作 ============

    def save_config(self):
        """保存当前配置到文件"""
        file_path, _ = QFileDialog.getSaveFileName(
            self, '保存配置', '', 'JSON Files (*.json)'
        )
        if not file_path:
            return

        try:
            config = {
                'version': '1.0',
                'fbgs': [],
                'sensors': [],
                'project_files': [],
                'current_data_path': None,
                'sensor_results': None,
            }

            # 保存FBG配置
            for fbg in self.sensor_system.fbgs:
                config['fbgs'].append({
                    'id': fbg.id,
                    'channel': fbg.channel,
                    'k1': fbg.k1,
                    'k2': fbg.k2,
                })

            # 保存传感器配置
            for sensor in self.sensor_system.sensors:
                config['sensors'].append({
                    'id': sensor.id,
                    'sensor_type': sensor.sensor_type,
                    'formula': sensor.formula,
                    'constants': sensor.constants,
                    'active': sensor.active,
                })

            # 保存项目文件列表
            for i in range(self.project_files_list.count()):
                config['project_files'].append(self.project_files_list.item(i).text())

            import json
            with open(file_path, 'w', encoding='utf-8') as f:
                json.dump(config, f, ensure_ascii=False, indent=2)

            QMessageBox.information(self, '成功', f'配置已保存到:\n{file_path}')

        except Exception as e:
            QMessageBox.critical(self, '错误', f'保存配置失败: {str(e)}')

    def load_config(self):
        """从文件加载配置"""
        file_path, _ = QFileDialog.getOpenFileName(
            self, '读取配置', '', 'JSON Files (*.json);;All Files (*)'
        )
        if not file_path:
            return

        try:
            import json
            with open(file_path, 'r', encoding='utf-8') as f:
                config = json.load(f)

            # 加载FBG配置
            self.sensor_system.fbgs.clear()
            for fbg_data in config.get('fbgs', []):
                fbg = FBG(fbg_data['id'], fbg_data['channel'], fbg_data['k1'], fbg_data['k2'])
                self.sensor_system.add_fbg(fbg)

            # 加载传感器配置
            self.sensor_system.sensors.clear()
            for sensor_data in config.get('sensors', []):
                sensor = Sensor(
                    sensor_data['id'],
                    sensor_data['sensor_type'],
                    sensor_data['formula'],
                    sensor_data['constants'],
                    sensor_data['active']
                )
                self.sensor_system.add_sensor(sensor)

            # 加载项目文件列表
            self.project_files_list.clear()
            for path in config.get('project_files', []):
                self.project_files_list.addItem(path)

            self.refresh_fbg_table()
            self.refresh_sensor_table()
            self.refresh_project_files()

            QMessageBox.information(self, '成功', f'配置已从:\n{file_path}')

        except Exception as e:
            QMessageBox.critical(self, '错误', f'加载配置失败: {str(e)}')

    def reset_config(self):
        """重置所有配置"""
        reply = QMessageBox.question(self, '确认', '确定要重置所有配置吗？\n这将清除所有传感器和项目文件设置。',
                                      QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
        if reply != QMessageBox.StandardButton.Yes:
            return

        self.sensor_system.fbgs.clear()
        self.sensor_system.sensors.clear()
        self.project_files_list.clear()

        self.refresh_fbg_table()
        self.refresh_sensor_table()
        self.refresh_project_files()

        QMessageBox.information(self, '成功', '配置已重置')

    # ============ File Operations ============

    MAX_RECENT_FILES = 5

    def get_recent_files(self, template_id=None):
        """获取最近打开的文件路径列表，可按模板筛选"""
        key = f'dataprocessor_recent_files_{template_id}' if template_id else 'dataprocessor_recent_files'
        recent = []
        stored = QSettings().value(key)
        if stored:
            recent = stored
        return recent

    def add_recent_file(self, file_path, template_id=None):
        """添加文件路径到最近列表"""
        key = f'dataprocessor_recent_files_{template_id}' if template_id else 'dataprocessor_recent_files'
        recent = self.get_recent_files(template_id)
        if file_path in recent:
            recent.remove(file_path)
        recent.insert(0, file_path)
        recent = recent[:self.MAX_RECENT_FILES]
        QSettings().setValue(key, recent)

    def open_file(self):
        # 两级菜单：光纤光栅数据 / 其它数据
        main_dialog = QDialog(self)
        main_dialog.setWindowTitle('选择数据类型')
        main_dialog.setMinimumWidth(400)
        layout = QVBoxLayout()

        # 类型选择
        type_layout = QHBoxLayout()
        type_layout.addWidget(QLabel('数据类型:'))
        self.file_type_combo = QComboBox()
        self.file_type_combo.addItems(['光纤光栅数据', '其它数据'])
        type_layout.addWidget(self.file_type_combo)
        layout.addLayout(type_layout)

        # 模板列表和历史路径
        list_layout = QHBoxLayout()

        # 左侧：模板列表
        left_layout = QVBoxLayout()
        left_layout.addWidget(QLabel('选择模板:'))
        self.template_list = QListWidget()
        left_layout.addWidget(self.template_list)

        # 右侧：最近打开
        right_layout = QVBoxLayout()
        right_layout.addWidget(QLabel('最近打开:'))
        self.recent_combo = QComboBox()
        self.recent_combo.setEditable(True)
        self.recent_combo.addItem('选择新路径...')
        right_layout.addWidget(self.recent_combo)

        list_layout.addLayout(left_layout)
        list_layout.addLayout(right_layout)
        layout.addLayout(list_layout)

        # 按钮
        btn_layout = QHBoxLayout()
        ok_btn = QPushButton('确认')
        ok_btn.clicked.connect(main_dialog.accept)
        btn_layout.addWidget(ok_btn)
        cancel_btn = QPushButton('取消')
        cancel_btn.clicked.connect(main_dialog.reject)
        btn_layout.addWidget(cancel_btn)
        layout.addLayout(btn_layout)

        main_dialog.setLayout(layout)

        # 根据数据类型更新模板列表
        def update_templates():
            file_type = self.file_type_combo.currentText()
            self.template_list.clear()
            self.recent_combo.clear()
            self.recent_combo.addItem('选择新路径...')

            if file_type == '光纤光栅数据':
                # 光纤光栅数据：ENLIGHT模板 + 自定义模板
                fiber_templates = [t for t in DEFAULT_TEMPLATES if t.file_format in ('enlight', 'fiber_custom')]
                for t in fiber_templates:
                    self.template_list.addItem(t.name)
                # 添加"添加新模板"选项
                self.template_list.addItem('+ 添加新模板...')
            else:
                # 其它数据：通用模板 + 自定义模板
                other_templates = [t for t in DEFAULT_TEMPLATES if t.file_format not in ('enlight', 'fiber_custom')]
                for t in other_templates:
                    self.template_list.addItem(t.name)
                # 添加"添加新模板"选项
                self.template_list.addItem('+ 添加新模板...')

        # 根据选择更新历史路径
        def update_recent_files():
            selected = self.template_list.currentItem()
            if not selected:
                return
            template_name = selected.text()
            if template_name.startswith('+'):
                return

            # 查找对应模板
            for t in DEFAULT_TEMPLATES:
                if t.name == template_name:
                    template_id = t.id
                    recent_files = self.get_recent_files(template_id)
                    self.recent_combo.clear()
                    self.recent_combo.addItem('选择新路径...')
                    for path in recent_files:
                        self.recent_combo.addItem(path)
                    break

        self.file_type_combo.currentTextChanged.connect(update_templates)
        self.template_list.itemSelectionChanged.connect(update_recent_files)
        update_templates()

        if main_dialog.exec() != QDialog.DialogCode.Accepted:
            return

        # 获取选择的模板名称
        selected_item = self.template_list.currentItem()
        if not selected_item:
            return
        template_name = selected_item.text()

        # 如果选择"添加新模板"
        if template_name.startswith('+'):
            QMessageBox.information(self, '提示', '请选择一个样本文件，软件将自动识别列含义')
            sample_path, _ = QFileDialog.getOpenFileName(self, '选择样本文件', '', 'All Files (*)')
            if not sample_path:
                return

            # 自动检测模板
            detected_template = self.auto_detect_template(sample_path)
            if detected_template:
                self.save_custom_template(detected_template)
                QMessageBox.information(self, '成功', f'模板已保存: {detected_template.name}')
                update_templates()
                return
            else:
                QMessageBox.warning(self, '警告', '无法自动识别数据格式')
                return

        # 查找对应模板
        selected_template = None
        for t in DEFAULT_TEMPLATES:
            if t.name == template_name:
                selected_template = t
                break

        if not selected_template:
            QMessageBox.warning(self, '警告', '未找到对应模板')
            return

        # 获取文件路径
        selected_path = self.recent_combo.currentText()
        recent_files = self.get_recent_files(selected_template.id)
        if selected_path == '选择新路径...' or selected_path not in recent_files:
            file_path = None
        else:
            file_path = selected_path

        # 打开文件对话框
        if selected_template.file_format == 'enlight':
            file_filter = 'ENLIGHT Files (*.txt);;All Files (*)'
        else:
            file_filter = {
                'txt': 'Text Files (*.txt)',
                'csv': 'CSV Files (*.csv)',
                'xlsx': 'Excel Files (*.xlsx *.xls)',
            }.get(selected_template.file_format, 'All Files (*)')

        if not file_path:
            file_path, _ = QFileDialog.getOpenFileName(self, '选择数据文件', '', file_filter)

        if not file_path:
            return

        try:
            df = parse_file(file_path, selected_template)
            self.current_data = df
            self.current_columns = [col['name'] for col in selected_template.columns]
            self.update_data_table()
            self.add_recent_file(file_path, selected_template.id)
            self.status_bar.showMessage(f'已加载 {len(df)} 行数据')
            QMessageBox.information(self, '成功', f'成功加载 {len(df)} 行数据')
        except Exception as e:
            import traceback
            error_msg = f"加载文件失败: {str(e)}\n\n详细信息:\n{traceback.format_exc()}"
            QMessageBox.critical(self, '错误', error_msg)

    def auto_detect_template(self, file_path):
        """自动检测数据格式并创建模板"""
        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                lines = f.readlines()[:20]

            if not lines:
                return None

            # 检查是否是ENLIGHT格式
            has_timestamp = any('Timestamp' in line for line in lines)
            has_ch = any('# CH' in line for line in lines)

            if has_timestamp and has_ch:
                for i, line in enumerate(lines):
                    if 'Timestamp' in line and '# CH' in line:
                        headers = line.strip().split('\t')
                        columns = []
                        for h in headers:
                            h = h.strip()
                            if 'Timestamp' in h:
                                columns.append({'name': '时间', 'comment': '时间戳', 'data_type': 'time'})
                            elif 'CH' in h and '计数' in h:
                                columns.append({'name': h, 'comment': h, 'data_type': 'numeric'})
                        return DataTemplate('enlight_type', 'ENLIGHT (光纤传感)', 'enlight', '\t', 104, columns)

            # 尝试通用格式检测
            delimiter = '\t'
            first_line = lines[0]
            if ',' in first_line and '\t' not in first_line:
                delimiter = ','

            parts = first_line.strip().split(delimiter)
            if len(parts) > 1:
                columns = []
                for i, col_name in enumerate(parts):
                    col_name = col_name.strip()
                    data_type = 'numeric'
                    if '时间' in col_name or 'time' in col_name.lower():
                        data_type = 'time'
                    elif '温度' in col_name:
                        data_type = 'temperature'
                    elif '波长' in col_name or 'wavelength' in col_name.lower():
                        data_type = 'wavelength'
                    elif '应变' in col_name or 'strain' in col_name.lower():
                        data_type = 'strain'
                    columns.append({'name': col_name, 'comment': col_name, 'data_type': data_type})

                has_wavelength = any('波长' in c['name'] for c in columns)
                template_type = 'fiber_custom' if has_wavelength else 'csv'

                return DataTemplate(
                    f'{template_type}_custom',
                    f'自定义{"光纤" if has_wavelength else "数据"}模板',
                    template_type,
                    delimiter,
                    0,
                    columns
                )

            return None
        except Exception as e:
            print(f"Auto detect error: {e}")
            return None

    def save_custom_template(self, template):
        """保存自定义模板"""
        DEFAULT_TEMPLATES.append(template)

    def load_test_data(self):
        """Load built-in test data"""
        data = [
            {'时间': 0, '应变1': 0.0, '应变2': 0.0, '位移': 0.000, '压力': 101325},
            {'时间': 0.1, '应变1': 5.2, '应变2': 3.8, '位移': 0.052, '压力': 101328},
            {'时间': 0.2, '应变1': 10.5, '应变2': 7.9, '位移': 0.105, '压力': 101330},
            {'时间': 0.3, '应变1': 15.9, '应变2': 12.1, '位移': 0.159, '压力': 101333},
            {'时间': 0.4, '应变1': 21.4, '应变2': 16.4, '位移': 0.214, '压力': 101335},
            {'时间': 0.5, '应变1': 27.0, '应变2': 20.8, '位移': 0.270, '压力': 101338},
            {'时间': 0.6, '应变1': 32.7, '应变2': 25.3, '位移': 0.327, '压力': 101340},
            {'时间': 0.7, '应变1': 38.5, '应变2': 30.0, '位移': 0.385, '压力': 101343},
            {'时间': 0.8, '应变1': 44.4, '应变2': 34.8, '位移': 0.444, '压力': 101345},
            {'时间': 0.9, '应变1': 50.4, '应变2': 39.7, '位移': 0.504, '压力': 101348},
            {'时间': 1.0, '应变1': 56.5, '应变2': 44.8, '位移': 0.565, '压力': 101350},
            {'时间': 1.1, '应变1': 62.7, '应变2': 50.0, '位移': 0.627, '压力': 101353},
            {'时间': 1.2, '应变1': 69.0, '应变2': 55.3, '位移': 0.690, '压力': 101355},
            {'时间': 1.3, '应变1': 75.4, '应变2': 60.7, '位移': 0.754, '压力': 101358},
            {'时间': 1.4, '应变1': 81.9, '应变2': 66.3, '位移': 0.819, '压力': 101360},
            {'时间': 1.5, '应变1': 88.5, '应变2': 72.0, '位移': 0.885, '压力': 101363},
            {'时间': 1.6, '应变1': 95.2, '应变2': 77.8, '位移': 0.952, '压力': 101365},
            {'时间': 1.7, '应变1': 102.0, '应变2': 83.7, '位移': 1.020, '压力': 101368},
            {'时间': 1.8, '应变1': 108.9, '应变2': 89.8, '位移': 1.089, '压力': 101370},
            {'时间': 1.9, '应变1': 115.9, '应变2': 95.9, '位移': 1.159, '压力': 101373},
            {'时间': 2.0, '应变1': 123.0, '应变2': 102.2, '位移': 1.230, '压力': 101375},
        ]

        df = pd.DataFrame(data)
        self.current_data = df
        self.current_columns = list(data[0].keys())
        self.update_data_table()
        self.status_bar.showMessage(f'已加载 {len(df)} 行测试数据')
        QMessageBox.information(self, '成功', f'已加载 {len(df)} 行测试数据')

    def clear_data(self):
        self.current_data = None
        self.current_columns = []
        self.data_table.setRowCount(0)
        self.data_table.setColumnCount(0)
        self.status_bar.showMessage('数据已清除')

    def update_data_table(self):
        if self.current_data is None:
            return

        df = self.current_data
        self.data_table.setRowCount(len(df))
        self.data_table.setColumnCount(len(df.columns))
        self.data_table.setHorizontalHeaderLabels(list(df.columns))

        for i, row in df.iterrows():
            for j, value in enumerate(row):
                item = QTableWidgetItem(str(value))
                self.data_table.setItem(i, j, item)

        self.data_table.resizeColumnsToContents()

    # ============ Cleaning Operations ============

    def apply_cleaning(self):
        if self.current_data is None:
            QMessageBox.warning(self, '警告', '请先加载数据')
            return

        # Build rules based on UI settings
        rules = []

        # Rule 1: Adjacent difference detection
        if self.adjacent_enabled.isChecked():
            rules.append(CleaningRule(
                '相邻差值',
                'adjacent_diff',
                True,
                self.diff_threshold.value(),
                self.fill_method_combo.currentText(),
                self.custom_fill_value.value() if self.fill_method_combo.currentText() == 'custom' else None
            ))

        # Rule 2: NaN detection
        if self.nan_enabled.isChecked():
            rules.append(CleaningRule(
                '缺失值',
                'nan',
                True,
                None,
                self.fill_method_combo.currentText(),
                self.custom_fill_value.value() if self.fill_method_combo.currentText() == 'custom' else None
            ))

        try:
            df = clean_data(self.current_data, rules)
            self.current_data = df
            self.update_data_table()

            # Count anomalies
            anomaly_cols = [col for col in df.columns if col.endswith('_anomaly')]
            total_anomalies = sum(df[col].sum() for col in anomaly_cols)

            self.cleaning_result.setText(f'检测到 {total_anomalies} 个异常数据点\n'
                                         f'异常列: {[col.replace("_anomaly", "") for col in anomaly_cols]}')

            self.status_bar.showMessage(f'数据清洗完成，发现 {total_anomalies} 个异常')
            QMessageBox.information(self, '成功', f'数据清洗完成，发现 {total_anomalies} 个异常')

        except Exception as e:
            import traceback
            QMessageBox.critical(self, '错误', f'清洗失败: {str(e)}\n\n{traceback.format_exc()}')

    # ============ Analysis Operations ============

    def run_analysis(self):
        """执行分析并更新图表"""
        if self.current_data is None:
            QMessageBox.warning(self, '警告', '请先加载数据')
            return

        try:
            data_source = self.data_source_combo.currentText()

            if data_source == '物理量':
                if not self.sensor_results:
                    QMessageBox.warning(self, '警告', '请先在光纤公式配置中计算传感器数据')
                    return

                # 获取所有选中的传感器
                selected_sensors = []
                for i in range(self.analysis_sensor_list.count()):
                    item = self.analysis_sensor_list.item(i)
                    if item.checkState() == Qt.CheckState.Checked and item.data(Qt.ItemDataRole.UserRole) != '__select_all__':
                        sensor_id = item.data(Qt.ItemDataRole.UserRole)
                        selected_sensors.append(sensor_id)

                if not selected_sensors:
                    QMessageBox.warning(self, '警告', '请选择至少一个传感器')
                    return

                # 获取时间列
                time_col = '时间' if '时间' in self.current_data.columns else self.current_data.columns[0]
                time_data = list(self.current_data[time_col].values)

                # 应用范围筛选
                start_idx = self.range_start.value()
                end_idx = min(self.range_end.value(), len(self.current_data) - 1)
                if start_idx >= len(self.current_data):
                    QMessageBox.warning(self, '警告', '起始索引超出数据范围')
                    return

                time_range = time_data[start_idx:end_idx + 1] if time_data else None

                # 更新图表（多传感器）
                self.update_chart_multi_sensors(selected_sensors, time_range)
                self.calculate_statistics_for_sensors(selected_sensors, start_idx, end_idx)

            else:
                # 原始数据：只显示波长变化的数据列
                # 排除时间列、计数列(CH1计数、CH2计数等)
                data_cols = [c for c in self.current_data.columns
                            if c != '时间'
                            and '计数' not in c
                            and not c.startswith('CH')]
                if not data_cols:
                    QMessageBox.warning(self, '警告', '没有可用的数据列')
                    return

                # 获取时间列
                time_col = '时间' if '时间' in self.current_data.columns else None
                if time_col:
                    time_data = list(self.current_data[time_col].values)
                else:
                    time_data = list(range(len(self.current_data)))

                # 应用范围筛选
                start_idx = self.range_start.value()
                end_idx = min(self.range_end.value(), len(self.current_data) - 1)
                if start_idx >= len(self.current_data):
                    QMessageBox.warning(self, '警告', '起始索引超出数据范围')
                    return

                time_range = time_data[start_idx:end_idx + 1]

                # 更新图表（多列原始数据）
                self.update_chart_multi_columns(data_cols, time_range, 'nm')

            self.status_bar.showMessage(f'分析完成')

        except Exception as e:
            import traceback
            QMessageBox.critical(self, '错误', f'分析失败: {str(e)}\n\n{traceback.format_exc()}')

    def update_chart_with_data(self, data, time_data, unit, sensor_id=None):
        """更新图表数据"""
        self.ax.clear()

        # 数据降采样以提高绘图性能
        max_points = 1000
        plot_data = data
        plot_time = time_data

        if len(data) > max_points:
            indices = np.linspace(0, len(data) - 1, max_points, dtype=int)
            plot_data = [data[i] for i in indices]
            if time_data and len(time_data) == len(data):
                plot_time = [time_data[i] for i in indices]

        if plot_time and len(plot_time) == len(plot_data):
            # 格式化时间显示
            def format_time(t):
                # 处理 pandas Timestamp 和 numpy datetime64
                if hasattr(t, 'strftime'):
                    # Timestamp 对象
                    return t.strftime('%H:%M:%S')
                elif hasattr(t, 'item'):
                    # numpy datetime64
                    try:
                        return pd.Timestamp(t).strftime('%H:%M:%S')
                    except:
                        pass
                elif isinstance(t, str):
                    # 字符串
                    if ' ' in t:
                        t = t.split(' ')[-1]
                    if '.' in t:
                        t = t.split('.')[0]
                    return t
                return str(t)

            self.ax.plot(plot_time, plot_data, 'b-', linewidth=1)
            self.ax.set_xlabel('时间')

            # 限制x轴刻度数量为10个
            if len(plot_time) > 10:
                tick_indices = np.linspace(0, len(plot_time) - 1, 10, dtype=int)
                self.ax.set_xticks([plot_time[i] for i in tick_indices])
                self.ax.set_xticklabels([format_time(plot_time[i]) for i in tick_indices], rotation=45, ha='right')
            if plot_time:
                self.ax.set_xlim([plot_time[0], plot_time[-1]])
        else:
            self.ax.plot(plot_data, 'b-', linewidth=1)
            self.ax.set_xlabel('序号')

        self.ax.set_ylabel(unit if unit else '物理量')
        self.ax.grid(True, alpha=0.3)

        # 添加标题
        title = f'{sensor_id} 物理量' if sensor_id else '数据曲线'
        self.ax.set_title(title)

        self.chart_figure.tight_layout()
        self.chart_canvas.draw()

    def update_chart_multi_columns(self, data_cols, time_data, unit):
        """更新图表 - 显示多列原始数据"""
        self.ax.clear()

        # 准备绘图数据
        max_points = 1000
        n_cols = len(data_cols)

        # 获取时间范围（应用范围筛选）
        start_idx = self.range_start.value()
        end_idx = min(self.range_end.value(), len(self.current_data) - 1)

        # 获取筛选后的时间数据
        if time_data:
            time_range = time_data[start_idx:end_idx + 1]
        else:
            time_range = list(range(start_idx, end_idx + 1))

        # 格式化时间显示
        def format_time(t):
            if hasattr(t, 'strftime'):
                return t.strftime('%H:%M:%S')
            elif hasattr(t, 'item'):
                try:
                    return pd.Timestamp(t).strftime('%H:%M:%S')
                except:
                    pass
            elif isinstance(t, str):
                if ' ' in t:
                    t = t.split(' ')[-1]
                if '.' in t:
                    t = t.split('.')[0]
                return t
            return str(t)

        # 格式化时间标签
        if len(time_range) > 10:
            tick_indices = np.linspace(0, len(time_range) - 1, 10, dtype=int)
            tick_labels = [format_time(time_range[i]) for i in tick_indices]
        else:
            tick_labels = None

        # 为每列数据绘制 - 显示波长差值（相对于第一行有效波长）
        colors = ['b-', 'g-', 'r-', 'c-', 'm-', 'y-', 'k-', 'orange']
        for i, col in enumerate(data_cols):
            col_data = self.current_data[col].values[start_idx:end_idx + 1]

            # 获取第一行有效波长作为基准
            ref_value = None
            for v in col_data:
                if pd.notna(v):
                    ref_value = v
                    break

            # 计算波长差值
            if ref_value is not None:
                plot_data = [v - ref_value if pd.notna(v) else None for v in col_data]
            else:
                plot_data = list(col_data)

            # 降采样
            if len(plot_data) > max_points:
                indices = np.linspace(0, len(plot_data) - 1, max_points, dtype=int)
                plot_data_sampled = [plot_data[j] for j in indices]
                plot_time = [time_range[j] for j in indices]
            else:
                plot_data_sampled = plot_data
                plot_time = time_range

            color = colors[i % len(colors)]
            self.ax.plot(plot_time, plot_data_sampled, color, linewidth=1, label=col)

        # 设置坐标轴范围
        if time_range:
            self.ax.set_xlim([time_range[0], time_range[-1]])

        # 设置坐标轴
        self.ax.set_xlabel('时间')
        if tick_labels:
            # 使用实际时间值作为刻度位置
            tick_positions = [time_range[i] for i in tick_indices]
            self.ax.set_xticks(tick_positions)
            self.ax.set_xticklabels(tick_labels, rotation=45, ha='right')

        self.ax.set_ylabel(f'波长差值 ({unit})')
        self.ax.grid(True, alpha=0.3)
        self.ax.legend(loc='upper right', fontsize=8)
        self.ax.set_title('原始数据 - 波长差值曲线')

        self.chart_figure.tight_layout()
        self.chart_canvas.draw()

    def calculate_statistics(self, data, sensor_id=None):
        """计算并显示统计值"""
        arr = np.array(data)
        arr = arr[~np.isnan(arr)]  # 去除NaN

        if len(arr) == 0:
            return

        stats = {
            '最大值': np.max(arr),
            '最小值': np.min(arr),
            '平均值': np.mean(arr),
            '标准差': np.std(arr),
            '峰峰值': np.max(arr) - np.min(arr),
            '有效值': np.sqrt(np.mean(arr ** 2)),
            '偏度': float(np.mean(((arr - arr.mean()) / arr.std()) ** 3)) if arr.std() > 0 else 0,
            '峰度': float(np.mean(((arr - arr.mean()) / arr.std()) ** 4)) if arr.std() > 0 else 0,
        }

        labels = {'最大值': 'max', '最小值': 'min', '平均值': 'mean', '标准差': 'std',
                  '峰峰值': 'peak_to_peak', '有效值': 'rms', '偏度': 'skew', '峰度': 'kurtosis'}

        for key, value in stats.items():
            self.stats_labels[labels[key]].setText(f'{key}: {value:.4f}')

    def get_sensor_display_name(self, sensor_id):
        """获取传感器的显示名称（中文名）"""
        for sensor in self.sensor_system.sensors:
            if sensor.id == sensor_id:
                return sensor.get_name()
        return sensor_id

    def update_chart_multi_sensors(self, sensor_ids, time_range):
        """更新图表 - 显示多个传感器的物理量数据"""
        self.ax.clear()

        max_points = 1000
        colors = ['b-', 'g-', 'r-', 'c-', 'm-', 'y-', 'k-', 'orange']

        # 格式化时间显示
        def format_time(t):
            if hasattr(t, 'strftime'):
                return t.strftime('%H:%M:%S')
            elif hasattr(t, 'item'):
                try:
                    return pd.Timestamp(t).strftime('%H:%M:%S')
                except:
                    pass
            elif isinstance(t, str):
                if ' ' in t:
                    t = t.split(' ')[-1]
                if '.' in t:
                    t = t.split('.')[0]
                return t
            return str(t)

        for i, sensor_id in enumerate(sensor_ids):
            if sensor_id not in self.sensor_results:
                continue

            data = list(self.sensor_results[sensor_id])
            unit = self.get_sensor_unit(sensor_id)

            # 应用范围筛选
            start_idx = self.range_start.value()
            end_idx = min(self.range_end.value(), len(data) - 1)
            data_range = data[start_idx:end_idx + 1]

            # 降采样
            if len(data_range) > max_points:
                indices = np.linspace(0, len(data_range) - 1, max_points, dtype=int)
                plot_data = [data_range[j] for j in indices]
                plot_time = [time_range[j] for j in indices] if time_range else list(range(len(data_range)))
            else:
                plot_data = data_range
                plot_time = time_range if time_range else list(range(len(data_range)))

            color = colors[i % len(colors)]
            self.ax.plot(plot_time, plot_data, color, linewidth=1, label=sensor_id)

        # 设置坐标轴范围
        if time_range:
            self.ax.set_xlim([time_range[0], time_range[-1]])

            # X轴标签，最多10个
            if len(time_range) > 10:
                tick_indices = np.linspace(0, len(time_range) - 1, 10, dtype=int)
                tick_positions = [time_range[j] for j in tick_indices]
                tick_labels = [format_time(time_range[j]) for j in tick_indices]
                self.ax.set_xticks(tick_positions)
                self.ax.set_xticklabels(tick_labels, rotation=45, ha='right')
            elif len(time_range) > 0:
                tick_labels = [format_time(t) for t in time_range]
                self.ax.set_xticks(range(len(time_range)))
                self.ax.set_xticklabels(tick_labels, rotation=45, ha='right')

        # 设置Y轴标签和图表标题
        if sensor_ids:
            first_sensor_id = sensor_ids[0]
            unit = self.get_sensor_unit(first_sensor_id)
            display_name = self.get_sensor_display_name(first_sensor_id)
            self.ax.set_ylabel(f'{display_name} ({unit})')

            # 图表标题：根据Y轴物理量名称 + "时程曲线"
            self.ax.set_title(f'{display_name}时程曲线')

        self.ax.set_xlabel('时间')
        self.ax.grid(True, alpha=0.3)
        self.ax.legend(loc='upper right', fontsize=8)

        self.chart_figure.tight_layout()
        self.chart_canvas.draw()

    def calculate_statistics_for_sensors(self, sensor_ids, start_idx, end_idx):
        """为多个传感器计算统计值（显示第一个传感器的统计）"""
        if not sensor_ids:
            return

        # 更新曲线选择下拉框
        self.stats_curve_combo.clear()
        self.compare_combo1.clear()
        self.compare_combo2.clear()
        self.stats_curve_combo.addItems(sensor_ids)
        self.compare_combo1.addItems(sensor_ids)
        self.compare_combo2.addItems(sensor_ids)

        # 显示第一个选中传感器的统计信息
        sensor_id = sensor_ids[0]
        if sensor_id in self.sensor_results:
            data = list(self.sensor_results[sensor_id])[start_idx:end_idx + 1]
            self.calculate_statistics(data, sensor_id)

    def refresh_analysis_sensors(self):
        """刷新分析模块的传感器选择列表"""
        self.analysis_sensor_list.clear()
        if self.sensor_results:
            # 添加全选框
            select_all_item = QListWidgetItem('【全选】')
            select_all_item.setFlags(select_all_item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            select_all_item.setCheckState(Qt.CheckState.Unchecked)
            select_all_item.setData(Qt.ItemDataRole.UserRole, '__select_all__')
            self.analysis_sensor_list.addItem(select_all_item)

            # 添加各个传感器选项
            for sensor_id in self.sensor_results.keys():
                item = QListWidgetItem(sensor_id)
                item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
                item.setCheckState(Qt.CheckState.Unchecked)
                item.setData(Qt.ItemDataRole.UserRole, sensor_id)
                self.analysis_sensor_list.addItem(item)

            self.analysis_sensor_list.setEnabled(True)
            self.analysis_sensor_list.itemChanged.connect(self.on_sensor_item_changed)
        else:
            self.analysis_sensor_list.setEnabled(False)

    def on_stats_curve_changed(self):
        """统计曲线选择变化时更新统计信息"""
        selected_sensor = self.stats_curve_combo.currentText()
        if not selected_sensor or selected_sensor not in self.sensor_results:
            return

        start_idx = self.range_start.value()
        end_idx = min(self.range_end.value(), len(self.sensor_results[selected_sensor]) - 1)
        data = list(self.sensor_results[selected_sensor])[start_idx:end_idx + 1]
        self.calculate_statistics(data, selected_sensor)

    def compare_curves(self):
        """对比两条曲线的统计信息"""
        curve1 = self.compare_combo1.currentText()
        curve2 = self.compare_combo2.currentText()

        if not curve1 or not curve2:
            QMessageBox.warning(self, '警告', '请选择两条曲线进行对比')
            return

        if curve1 not in self.sensor_results or curve2 not in self.sensor_results:
            QMessageBox.warning(self, '警告', '请确保两条曲线都已计算')
            return

        start_idx = self.range_start.value()
        end_idx = min(self.range_end.value(), len(self.sensor_results[curve1]) - 1)
        end_idx = min(end_idx, len(self.sensor_results[curve2]) - 1)

        data1 = np.array(list(self.sensor_results[curve1])[start_idx:end_idx + 1])
        data2 = np.array(list(self.sensor_results[curve2])[start_idx:end_idx + 1])

        # 计算差值
        diff = data1 - data2

        result = f'曲线对比: {curve1} vs {curve2}\n'
        result += f'{"="*30}\n'
        result += f'最大差值: {np.max(diff):.4f}\n'
        result += f'最小差值: {np.min(diff):.4f}\n'
        result += f'平均差值: {np.mean(diff):.4f}\n'
        result += f'标准差: {np.std(diff):.4f}\n'
        result += f'峰峰值: {np.max(diff) - np.min(diff):.4f}'

        self.compare_result.setText(result)

    def on_sensor_item_changed(self, item):
        """处理传感器勾选状态变化"""
        if item.data(Qt.ItemDataRole.UserRole) == '__select_all__':
            select_all_state = item.checkState()
            for i in range(self.analysis_sensor_list.count()):
                other_item = self.analysis_sensor_list.item(i)
                if other_item.data(Qt.ItemDataRole.UserRole) != '__select_all__':
                    other_item.setCheckState(select_all_state)

    def on_data_source_changed(self):
        """数据源切换时更新传感器选择"""
        if self.data_source_combo.currentText() == '物理量':
            self.refresh_analysis_sensors()
            # 更新曲线选择下拉框
            self.stats_curve_combo.clear()
            self.compare_combo1.clear()
            self.compare_combo2.clear()
            if self.sensor_results:
                sensor_ids = list(self.sensor_results.keys())
                self.stats_curve_combo.addItems(sensor_ids)
                self.compare_combo1.addItems(sensor_ids)
                self.compare_combo2.addItems(sensor_ids)

    def reset_zoom(self):
        """重置缩放"""
        self.ax.autoscale_view()
        self.chart_canvas.draw()

    def zoom_chart(self, factor):
        """缩放图表"""
        xlim = self.ax.get_xlim()
        ylim = self.ax.get_ylim()

        x_center = (xlim[0] + xlim[1]) / 2
        y_center = (ylim[0] + ylim[1]) / 2

        x_range = (xlim[1] - xlim[0]) * factor / 2
        y_range = (ylim[1] - ylim[0]) * factor / 2

        self.ax.set_xlim([x_center - x_range, x_center + x_range])
        self.ax.set_ylim([y_center - y_range, y_center + y_range])
        self.chart_canvas.draw()

    def pan_chart(self):
        """平移图表"""
        if self.btn_pan.isChecked():
            self.chart_canvas.mpl_connect('motion_notify_event', self.on_pan)
        else:
            self.chart_canvas.mpl_disconnect('motion_notify_event')

    def on_pan(self, event):
        """处理平移事件"""
        if event.button == 1:  # left button
            dx = event.x - getattr(self, 'last_x', event.x)
            dy = event.y - getattr(self, 'last_y', event.y)
            xlim = self.ax.get_xlim()
            ylim = self.ax.get_ylim()
            self.ax.set_xlim([xlim[0] - dx, xlim[1] - dx])
            self.ax.set_ylim([ylim[0] - dy, ylim[1] - dy])
            self.chart_canvas.draw()
        self.last_x = event.x
        self.last_y = event.y

    def export_chart(self):
        """导出图表"""
        file_path, _ = QFileDialog.getSaveFileName(
            self, '导出图表', '', 'PNG Files (*.png);;PDF Files (*.pdf)'
        )
        if file_path:
            self.chart_figure.savefig(file_path, dpi=300, bbox_inches='tight')
            QMessageBox.information(self, '成功', f'图表已保存到:\n{file_path}')

    def get_sensor_unit(self, sensor_id):
        """获取传感器单位"""
        for sensor in self.sensor_system.sensors:
            if sensor.id == sensor_id:
                return sensor.get_unit()
        return ''

    # ============ Report Operations ============

    def generate_report(self):
        if self.current_data is None:
            QMessageBox.warning(self, '警告', '请先加载数据')
            return

        try:
            data_summary = {
                '行数': len(self.current_data),
                '列数': len(self.current_data.columns),
                '列名': ', '.join(self.current_data.columns),
            }

            config = {
                'title': self.report_title.text(),
                'author': self.report_author.text(),
            }

            doc = Document()
            doc.add_heading(config['title'], 0)

            # AI诊断摘要（如果可用）
            ai_diagnosis = self.ai_diagnosis_result.toPlainText()
            if ai_diagnosis and len(ai_diagnosis) > 10:
                doc.add_heading('AI智能诊断摘要', level=1)
                doc.add_paragraph(ai_diagnosis)

            doc.add_heading('数据概要', level=1)
            for key, value in data_summary.items():
                doc.add_paragraph(f'{key}: {value}')

            doc.add_heading('数据表格', level=1)
            table = doc.add_table(rows=min(100, len(self.current_data) + 1),
                                 cols=len(self.current_data.columns))
            # Header
            for j, col in enumerate(self.current_data.columns):
                table.cell(0, j).text = str(col)
            # Data
            for i in range(min(99, len(self.current_data))):
                for j in range(len(self.current_data.columns)):
                    table.cell(i + 1, j).text = str(self.current_data.iloc[i, j])

            # Save
            file_path, _ = QFileDialog.getSaveFileName(
                self, '保存报告', '', 'Word Documents (*.docx)'
            )
            if file_path:
                doc.save(file_path)
                QMessageBox.information(self, '成功', f'报告已保存到: {file_path}')

        except Exception as e:
            QMessageBox.critical(self, '错误', f'生成报告失败: {str(e)}')

    def check_ollama_status(self):
        """检查Ollama服务状态 - 异步"""
        from py.ollama_client import OllamaStatusThread
        self.ollama_status_thread = OllamaStatusThread(self.ollama_client.base_url)
        self.ollama_status_thread.status_result.connect(self._on_ollama_status_result)
        self.ollama_status_thread.models_list.connect(self._on_ollama_models_list)
        self.ollama_status_thread.start()

    def _on_ollama_status_result(self, is_available):
        """Ollama状态检测结果"""
        if is_available:
            model_name = self.ollama_client.model
            self.ai_status_label.setText(f'Ollama状态: 已连接 ({model_name})')
            self.ai_status_label.setStyleSheet('color: green;')
            self.ollama_toggle_btn.setText('停止服务')
            self.ollama_toggle_btn.setChecked(True)
        else:
            self.ai_status_label.setText('Ollama状态: 未连接')
            self.ai_status_label.setStyleSheet('color: red;')
            self.ollama_toggle_btn.setText('启动服务')
            self.ollama_toggle_btn.setChecked(False)

    def _on_ollama_models_list(self, models):
        """收到模型列表"""
        if models:
            current = self.ai_model_combo.currentText()
            self.ai_model_combo.clear()
            self.ai_model_combo.addItems(models)
            if current in models:
                self.ai_model_combo.setCurrentText(current)

    def select_local_model(self):
        """选择本地GGUF模型文件"""
        from PyQt6.QtWidgets import QFileDialog
        file_path, _ = QFileDialog.getOpenFileName(
            self, '选择GGUF模型文件', '', 'GGUF Files (*.gguf);;All Files (*)'
        )
        if file_path:
            self.ai_model_path.setText(file_path)
            self.ai_model_path.setStyleSheet('color: #333;')

            # 更新状态
            from py.local_llm_manager import get_global_llm_manager
            llm = get_global_llm_manager()
            if llm.load_model(file_path):
                self.ai_status_label.setText(f'状态: 已加载 ({file_path.split("/")[-1]})')
                self.ai_status_label.setStyleSheet('color: green;')
            else:
                self.ai_status_label.setText('状态: 加载失败')
                self.ai_status_label.setStyleSheet('color: red;')

    def unload_local_model(self):
        """卸载当前模型"""
        from py.local_llm_manager import get_global_llm_manager
        llm = get_global_llm_manager()
        if llm.unload_model():
            self.ai_status_label.setText('状态: 未加载')
            self.ai_status_label.setStyleSheet('color: gray;')
        else:
            self.ai_status_label.setText('状态: 卸载失败')
            self.ai_status_label.setStyleSheet('color: orange;')

    def check_ollama_status(self):
        """检查本地模型状态"""
        from py.local_llm_manager import get_global_llm_manager
        llm = get_global_llm_manager()
        if llm.is_model_loaded():
            model_name = llm.get_current_model_path().split("/")[-1]
            self.ai_status_label.setText(f'状态: 已加载 ({model_name})')
            self.ai_status_label.setStyleSheet('color: green;')
        else:
            self.ai_status_label.setText('状态: 未加载')
            self.ai_status_label.setStyleSheet('color: gray;')

    def _on_ollama_status_result(self, is_available):
        """Ollama状态检测结果"""
        pass  # 不再使用

    def _on_ollama_models_list(self, models):
        """收到模型列表"""
        pass  # 不再使用

    def on_ollama_model_changed(self, model_name):
        """模型切换（不再使用，保留接口）"""
        pass

    def refresh_ollama_models(self):
        """刷新模型列表（不再使用）"""
        pass

    def generate_ai_diagnosis(self):
        """生成AI诊断摘要 - 分析计算后的全部物理量数据"""
        if not self.ollama_client:
            QMessageBox.warning(self, '警告', 'Ollama客户端未初始化')
            return

        if not self.ollama_client.is_available():
            QMessageBox.warning(self, '警告', 'Ollama服务未连接，请确保服务已启动')
            return

        if self.current_data is None or self.current_data.empty:
            QMessageBox.warning(self, '警告', '请先加载数据')
            return

        try:
            self.ai_diagnosis_btn.setEnabled(False)
            self.ai_diagnosis_result.setText('正在生成诊断摘要，请稍候...')

            # 使用计算后的物理量数据进行分析
            all_data = []
            stats = {}
            sensor_id = "原始数据"
            if self.sensor_results:
                sensor_id = list(self.sensor_results.keys())[0]
                data = list(self.sensor_results[sensor_id])
                arr = np.array(data)
                arr = arr[~np.isnan(arr)]

                if len(arr) > 10:
                    all_data = arr.tolist()
                    stats = {
                        'max': float(np.max(arr)),
                        'min': float(np.min(arr)),
                        'mean': float(np.mean(arr)),
                        'std': float(np.std(arr)),
                        'peak_to_peak': float(np.max(arr) - np.min(arr)),
                        'rms': float(np.sqrt(np.mean(arr ** 2))),
                        'skew': float(np.mean(((arr - arr.mean()) / arr.std()) ** 3)) if arr.std() > 0 else 0,
                        'kurtosis': float(np.mean(((arr - arr.mean()) / arr.std()) ** 4)) if arr.std() > 0 else 0,
                        'count': len(arr)
                    }
            else:
                arr = self.current_data.select_dtypes(include=[np.number]).values
                arr = arr[~np.isnan(arr)]
                if len(arr) > 10:
                    all_data = arr.tolist()
                    stats = {
                        'max': float(np.max(arr)),
                        'min': float(np.min(arr)),
                        'mean': float(np.mean(arr)),
                        'std': float(np.std(arr)),
                        'peak_to_peak': float(np.max(arr) - np.min(arr)),
                        'rms': float(np.sqrt(np.mean(arr ** 2))),
                        'count': len(arr)
                    }

            if len(all_data) < 10:
                self.ai_diagnosis_result.setText('数据不足以进行诊断分析')
                self.ai_diagnosis_btn.setEnabled(True)
                return

            # 构建提示词
            sample_count = min(50, len(all_data))
            sample_str = ', '.join([f'{v:.2f}' for v in all_data[:sample_count]])
            prompt = f"""Analyze {sensor_id} physical measurement data:
- Data count: {stats.get('count', 0)}
- Sample values (first {sample_count}): [{sample_str}]
- Stats: max={stats.get('max', 0):.2f}, min={stats.get('min', 0):.2f}, mean={stats.get('mean', 0):.2f}, std={stats.get('std', 0):.2f}, peak_to_peak={stats.get('peak_to_peak', 0):.2f}, RMS={stats.get('rms', 0):.2f}

Please analyze this time series data and identify:
1. Overall trend (stable, increasing, decreasing, fluctuating)
2. Any anomalies or outliers
3. Data quality assessment

If anything is unclear, ask me to clarify via dialog. Keep response concise under 200 words."""

            from py.llama_generate_thread import LlamaGenerateThread
            model_path = self.ai_model_path.text()
            if not model_path:
                self.ai_diagnosis_result.setText('请先选择GGUF模型文件')
                self.ai_diagnosis_btn.setEnabled(True)
                return
            self.ollama_diag_thread = LlamaGenerateThread(
                model_path=model_path,
                prompt=prompt
            )
            self.ollama_diag_thread.finished.connect(self._on_gen_diagnosis_finished)
            self.ollama_diag_thread.error.connect(self._on_gen_diagnosis_error)
            self.ollama_diag_thread.start()

        except Exception as e:
            self.ai_diagnosis_result.setText(f'诊断生成失败: {str(e)}')
            self.ai_diagnosis_btn.setEnabled(True)

    def _on_gen_diagnosis_finished(self, result):
        """AI诊断生成完成"""
        self.ai_diagnosis_result.setText(result if result and len(result) >= 5 else "AI诊断生成失败（模型响应为空，请尝试其他模型）")
        self.ai_diagnosis_btn.setEnabled(True)

    def _on_gen_diagnosis_error(self, err_msg):
        """AI诊断生成错误"""
        self.ai_diagnosis_result.setText(f'诊断生成失败: {err_msg}')
        self.ai_diagnosis_btn.setEnabled(True)

    def custom_ai_query(self):
        """根据用户自定义提示词进行AI查询"""
        if not self.ollama_client:
            QMessageBox.warning(self, '警告', 'Ollama客户端未初始化')
            return

        if not self.ollama_client.is_available():
            QMessageBox.warning(self, '警告', 'Ollama服务未连接，请确保服务已启动')
            return

        if self.current_data is None or self.current_data.empty:
            QMessageBox.warning(self, '警告', '请先加载数据')
            return

        user_prompt = self.custom_prompt_input.toPlainText().strip()
        if not user_prompt:
            QMessageBox.warning(self, '警告', '请输入提示词')
            return

        try:
            # 构建包含全部物理量数据的完整提示词
            stats = {}
            all_data = None
            if self.sensor_results:
                sensor_id = list(self.sensor_results.keys())[0]
                data = list(self.sensor_results[sensor_id])
                arr = np.array(data)
                arr = arr[~np.isnan(arr)]
                if len(arr) > 10:
                    stats = {
                        'max': float(np.max(arr)),
                        'min': float(np.min(arr)),
                        'mean': float(np.mean(arr)),
                        'std': float(np.std(arr)),
                        'peak_to_peak': float(np.max(arr) - np.min(arr)),
                        'rms': float(np.sqrt(np.mean(arr ** 2))),
                        'count': len(arr)
                    }
                    all_data = arr.tolist()
            else:
                arr = self.current_data.select_dtypes(include=[np.number]).values
                arr = arr[~np.isnan(arr)]
                if len(arr) > 10:
                    stats = {
                        'max': float(np.max(arr)),
                        'min': float(np.min(arr)),
                        'mean': float(np.mean(arr)),
                        'std': float(np.std(arr)),
                        'peak_to_peak': float(np.max(arr) - np.min(arr)),
                        'rms': float(np.sqrt(np.mean(arr ** 2))),
                        'count': len(arr)
                    }
                    all_data = arr.tolist()

            # 构建提示词上下文
            if all_data:
                sample_count = min(30, len(all_data))
                sample_str = ', '.join([f'{v:.2f}' for v in all_data[:sample_count]])
                context = f"""传感器物理量数据：
- 数据点数：{stats.get('count', 0)}
- 样本值(前{sample_count}个)：[{sample_str}]
- 统计：最大值={stats.get('max', 0):.2f}, 最小值={stats.get('min', 0):.2f}, 均值={stats.get('mean', 0):.2f}, 标准差={stats.get('std', 0):.2f}

用户问题：{user_prompt}

如有不清楚的地方，请通过对话框向我确认。"""
            else:
                context = f"数据分析统计：最大值={stats.get('max', 0):.2f}, 最小值={stats.get('min', 0):.2f}, 均值={stats.get('mean', 0):.2f}, 标准差={stats.get('std', 0):.2f}。\n\n用户问题：{user_prompt}\n\n如有不清楚的地方，请通过对话框向我确认。"
            full_prompt = f"Context: {context}\n\n请基于以上数据回答用户问题，回答简洁明了，不超过200字。如有不清楚的请通过对话框向我确认。"

            self.custom_query_result.setText('正在处理，请稍候...')
            self.custom_query_stream = ""

            from py.llama_generate_thread import LlamaGenerateThread
            model_path = self.ai_model_path.text()
            if not model_path:
                self.custom_query_result.setText('请先选择GGUF模型文件')
                return
            self.custom_query_thread = LlamaGenerateThread(
                model_path=model_path,
                prompt=full_prompt
            )
            self.custom_query_thread.token_received.connect(self._on_custom_query_token)
            self.custom_query_thread.finished.connect(self._on_custom_query_finished)
            self.custom_query_thread.error.connect(self._on_custom_query_error)
            self.custom_query_thread.start()

        except Exception as e:
            self.custom_query_result.setText(f'查询失败: {str(e)}')

    def _on_custom_query_token(self, token):
        if not hasattr(self, 'custom_query_stream'):
            self.custom_query_stream = ""
        self.custom_query_stream += token
        self.custom_query_result.setText(f'正在处理...\n\n{self.custom_query_stream[:500]}')

    def _on_custom_query_finished(self, result):
        if result and len(result) > 5:
            self.custom_query_result.setText(result[:500])
        else:
            self.custom_query_result.setText('模型未返回有效响应，请尝试其他问题')

    def _on_custom_query_error(self, err_msg):
        self.custom_query_result.setText(f'查询失败: {err_msg}')

    def save_data(self):
        if self.current_data is None:
            QMessageBox.warning(self, '警告', '没有数据可保存')
            return

        file_path, _ = QFileDialog.getSaveFileName(
            self, '保存数据', '', 'CSV Files (*.csv);;Excel Files (*.xlsx)'
        )

        if file_path:
            try:
                if file_path.endswith('.xlsx'):
                    self.current_data.to_excel(file_path, index=False)
                else:
                    self.current_data.to_csv(file_path, index=False)
                QMessageBox.information(self, '成功', f'数据已保存到: {file_path}')
            except Exception as e:
                QMessageBox.critical(self, '错误', f'保存失败: {str(e)}')

    def export_report(self):
        """导出报告 - 跳转到信息整合页面"""
        self.central_widget.setCurrentIndex(4)  # Switch to report tab
        QMessageBox.information(self, '提示', '请在信息整合页面选择相应功能生成报告')


# ============ Dialogs ============

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


class SensorEditDialog(QDialog):
    """传感器编辑对话框"""
    def __init__(self, parent=None, sensor=None):
        super().__init__(parent)
        self.setWindowTitle('编辑传感器' if sensor else '添加传感器')
        self.setMinimumWidth(500)
        self.sensor = sensor

        layout = QVBoxLayout()

        # Sensor ID
        id_layout = QHBoxLayout()
        id_layout.addWidget(QLabel('传感器ID:'))
        self.id_input = QLineEdit()
        if sensor:
            self.id_input.setText(sensor.id)
        id_layout.addWidget(self.id_input)
        layout.addLayout(id_layout)

        # Sensor type
        type_layout = QHBoxLayout()
        type_layout.addWidget(QLabel('传感器类型:'))
        self.type_combo = QComboBox()
        self.type_combo.addItems([
            'strain: 应变',
            'temperature: 温度',
            'displacement: 位移',
            'inclination: 倾角',
            'pressure: 压力'
        ])
        if sensor:
            for i in range(self.type_combo.count()):
                text = self.type_combo.itemText(i)
                if text.startswith(sensor.sensor_type):
                    self.type_combo.setCurrentIndex(i)
                    break
        type_layout.addWidget(self.type_combo)
        layout.addLayout(type_layout)

        # Formula
        expr_layout = QHBoxLayout()
        expr_layout.addWidget(QLabel('公式:'))
        self.expr_input = QLineEdit()
        if sensor:
            self.expr_input.setText(sensor.formula)
        else:
            self.expr_input.setText('W1 * k1')
        expr_layout.addWidget(self.expr_input)
        layout.addLayout(expr_layout)

        # Constants
        self.const_group = QGroupBox('常量')
        const_layout = QVBoxLayout()

        self.const_inputs = {}
        if sensor:
            for name, value in sensor.constants.items():
                row = QHBoxLayout()
                row.addWidget(QLabel(f'{name}:'))
                input_field = QDoubleSpinBox()
                input_field.setRange(-1e10, 1e10)
                input_field.setDecimals(2)
                input_field.setValue(value)
                row.addWidget(input_field)
                self.const_inputs[name] = input_field
                const_layout.addLayout(row)

        # Add constant button
        add_const_btn = QPushButton('添加常量')
        add_const_btn.clicked.connect(self.add_constant)
        const_layout.addWidget(add_const_btn)
        self.const_group.setLayout(const_layout)
        layout.addWidget(self.const_group)

        # Active checkbox
        self.active_check = QCheckBox('启用')
        self.active_check.setChecked(sensor.active if sensor else True)
        layout.addWidget(self.active_check)

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

    def add_constant(self):
        name = f'k{len(self.const_inputs) + 1}'
        row = QHBoxLayout()
        name_label = QLabel(f'{name}:')
        name_label.setMinimumWidth(40)
        row.addWidget(name_label)

        value_input = QDoubleSpinBox()
        value_input.setRange(-1e10, 1e10)
        value_input.setDecimals(2)
        value_input.setValue(1.0)
        row.addWidget(value_input)
        self.const_inputs[name] = value_input

        const_layout = self.const_group.layout()
        const_layout.insertLayout(const_layout.count() - 1, row)

    def get_sensor(self):
        sensor_type = self.type_combo.currentText().split(':')[0]
        constants = {name: input_field.value() for name, input_field in self.const_inputs.items()}
        return Sensor(
            self.id_input.text(),
            sensor_type,
            self.expr_input.text(),
            constants,
            self.active_check.isChecked()
        )


# ============ Application Entry ============

def main():
    app = QApplication(sys.argv)
    app.setStyle('Fusion')

    window = DataProcessorWindow()
    window.show()

    sys.exit(app.exec())


if __name__ == '__main__':
    main()