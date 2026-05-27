# DataProcessor Pro - Main Application
# PyQt6-based offline data analysis software

import sys
import os
import json
from pathlib import Path
from datetime import datetime
from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QTableWidget, QTableWidgetItem, QPushButton, QLabel, QFileDialog,
    QMessageBox, QTabWidget, QMenuBar, QMenu, QToolBar, QStatusBar,
    QDialog, QListWidget, QListWidgetItem, QAbstractItemView, QLineEdit,
    QComboBox, QGroupBox, QFormLayout, QCheckBox, QSpinBox, QDoubleSpinBox,
    QTextEdit, QSplitter, QGridLayout, QStackedWidget, QProgressBar, QFrame,
    QInputDialog, QTreeWidget, QTreeWidgetItem
)
from PyQt6.QtCore import Qt, QTimer, QSettings
from PyQt6.QtGui import QAction, QIcon

import pandas as pd
import numpy as np
import csv
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

# ============ Backend Module Imports ============
from py.wiki_system import WikiFileSystem
from py.multi_agent import run_multi_agent, MultiAgentState


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
    DataTemplate('csv_type', 'CSV 文件', 'csv', ',', 0, []),
    DataTemplate('txt_type', 'TXT 文件', 'txt', '\t', 0, []),
    DataTemplate('xlsx_type', 'Excel 文件', 'xlsx', ',', 0, []),
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
        'decoupling': {
            'name': '双参量解耦',
            'unit': 'με / °C',
            'need_temp_compensation': False,
        },
        'strain_cal': {
            'name': '应变标定',
            'unit': 'με',
            'need_temp_compensation': False,
        },
        'temp_cal': {
            'name': '温度标定',
            'unit': '°C',
            'need_temp_compensation': False,
        },
    }

    def __init__(self, id, sensor_type, formula=None, constants=None, active=True, decoupling_config=None, location=''):
        self.id = id
        self.sensor_type = sensor_type
        self.formula = formula
        self.constants = constants or {}
        self.active = active
        self.decoupling_config = decoupling_config
        self.location = location

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

        # 自动匹配FBG通道到实际数据列（波长类列）
        wavelength_cols = [c for c in df.columns
                          if '波长' in str(c) or 'wavelength' in str(c).lower()
                          or str(c).upper().startswith('FBG')
                          or str(c).upper().startswith('W') and str(c)[1:].isdigit()]
        if not wavelength_cols:
            # 找不到明确的波长列时，使用数值列（排除时间列）
            import pandas.api.types as ptypes
            wavelength_cols = [c for c in df.columns
                              if '时间' not in str(c) and 'timestamp' not in str(c).lower()
                              and ptypes.is_numeric_dtype(df[c])]

        fbg_initial = {}
        fbg_delta = {}

        print("=" * 60)
        print("[诊断] FBG通道匹配:")
        print(f"  DataFrame 列: {list(df.columns)}")
        print(f"  检测到的波长列: {wavelength_cols}")
        print(f"  已注册 FBG: {[(f.id, f.channel) for f in self.fbgs]}")

        for i, fbg in enumerate(self.fbgs):
            # 先尝试按channel精确匹配
            if fbg.channel in column_data:
                matched_col = fbg.channel
            elif i < len(wavelength_cols):
                # channel不匹配时，按FBG索引自动分配波长列
                matched_col = wavelength_cols[i]
                fbg.channel = str(matched_col)
            else:
                print(f"  FBG {fbg.id}: 未匹配到任何列!")
                continue

            col_values = column_data[matched_col]
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

            # 同时用 channel（列名）作为别名，兼容公式直接引用列名
            if str(matched_col) != str(fbg.id):
                fbg_delta[str(matched_col)] = delta

            # 诊断输出
            valid_deltas = [d for d in delta if d is not None][:5]
            print(f"  FBG {fbg.id} → 列 '{matched_col}' | 初始波长: {initial:.6f} | 前5个差值(nm): {[f'{d:.6f}' if d is not None else 'None' for d in valid_deltas]}")
        print("=" * 60)

        # 兜底：如果 fbg_delta 为空（无已注册 FBG 或通道全不匹配），
        # 自动从数据列中检测 FBG_ 前缀列，直接构建 fbg_delta
        if not fbg_delta:
            auto_cols = [str(c) for c in df.columns if str(c).upper().startswith('FBG_')]
            if auto_cols:
                print(f"[自动兜底] fbg_delta 为空，从数据列自动构建: {auto_cols}")
                for col in auto_cols:
                    col_values = column_data[col]
                    if ref_row < len(col_values):
                        initial = col_values[ref_row]
                    else:
                        initial = col_values[0]
                    delta = []
                    for w in col_values:
                        if pd.isna(w) or pd.isna(initial):
                            delta.append(None)
                        else:
                            delta.append(w - initial)
                    fbg_delta[col] = delta
                    # 同时用简名 W1, W2, ... 作为别名，兼容使用 W1 的公式
                    idx = auto_cols.index(col) + 1
                    fbg_delta[f'W{idx}'] = delta
                    print(f"  列 '{col}' → fbg_delta['{col}'] + fbg_delta['W{idx}']")

        # 计算每个传感器
        for sensor in self.sensors:
            if not sensor.active:
                continue

            try:
                if sensor.sensor_type == 'decoupling' and sensor.decoupling_config:
                    strain_r, temp_r = self._evaluate_decoupling(
                        sensor.decoupling_config, fbg_delta, len(df)
                    )
                    results[f'{sensor.id}_应变(με)'] = strain_r
                    results[f'{sensor.id}_温度(°C)'] = temp_r
                else:
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

        W1, W2 等会被替换为该列的波长差值 (W - W0)
        """
        import re

        # 用正则按单词边界替换常量（避免 k1 误匹配 k10）
        expr = formula
        for name, value in constants.items():
            expr = re.sub(r'\b' + re.escape(name) + r'\b', f'({value})', expr)

        # 检查公式中是否有未定义的常量
        unresolved_k = re.findall(r'\b[kK]\d+\b', expr)
        if unresolved_k:
            print(f"警告: 公式中常量 {unresolved_k} 未定义，默认按 1.0 计算。请在传感器配置中添加常量。")
            for k_var in set(unresolved_k):
                expr = re.sub(r'\b' + re.escape(k_var) + r'\b', '(1.0)', expr)

        # 计算每行
        result = []
        for i in range(n_rows):
            row_vars = {}
            for fbg_id, delta in fbg_delta.items():
                v = delta[i] if i < len(delta) else None
                if v is None:
                    row_vars[fbg_id] = float('nan')
                else:
                    row_vars[fbg_id] = v

            try:
                val = eval(expr, {"__builtins__": {}}, row_vars)
                # NaN 或 inf 统一存为 None (表格显示 N/A)
                if val is None or (isinstance(val, float) and (val != val or abs(val) == float('inf'))):
                    result.append(None)
                else:
                    result.append(val)
            except Exception as e:
                print(f"行 {i} 计算失败: {expr} -> {e}")
                result.append(None)

        return result

    def _evaluate_decoupling(self, config, fbg_delta, n_rows):
        """双参量解耦：矢量化矩阵求解应变和温度

        方程:
          Δλ1 = Ke1 * Δε + KT1 * ΔT
          Δλ2 = Ke2 * Δε + KT2 * ΔT

        求解:
          D = Ke1*KT2 - Ke2*KT1
          Δε = (Δλ1*KT2 - Δλ2*KT1) / D
          ΔT = (Δλ2*Ke1 - Δλ1*Ke2) / D

        波长差值从 nm 转换为 pm (×1000) 以避免浮点精度丢失。
        """
        import numpy as np

        Ke1 = float(config['Ke1'])
        Ke2 = float(config['Ke2'])
        KT1 = float(config['KT1'])
        KT2 = float(config['KT2'])

        # 获取波长差值，nm → pm (×1000)
        delta_source1 = fbg_delta.get(config['fbg1'], [0.0] * n_rows)
        delta_source2 = fbg_delta.get(config['fbg2'], [0.0] * n_rows)

        d1 = np.array([v if v is not None else np.nan for v in delta_source1], dtype=np.float64) * 1000.0
        d2 = np.array([v if v is not None else np.nan for v in delta_source2], dtype=np.float64) * 1000.0

        # 行列式
        D = Ke1 * KT2 - Ke2 * KT1

        if abs(D) < 1e-9:
            print(f"[解耦] 传感器行列式接近零 (D={D:.6e})，矩阵不可逆。请确认两个FBG的应变/温度系数有差异。")
            return (np.full(n_rows, np.nan).tolist(), np.full(n_rows, np.nan).tolist())

        # 矢量化求解
        strain = (d1 * KT2 - d2 * KT1) / D
        temp = (d2 * Ke1 - d1 * Ke2) / D

        return (strain.tolist(), temp.tolist())


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

def _detect_format(file_path, encoding, template_skip_rows):
    """从文件前50KB样本中检测分隔符和数据起始行"""
    common_delimiters = [',', '\t', ';', '|']
    try:
        with open(file_path, 'r', encoding=encoding) as f:
            sample = f.read(51200)
    except UnicodeDecodeError:
        return None, template_skip_rows

    lines = sample.split('\n')
    if len(lines) < 3:
        return None, template_skip_rows

    # 第一步：跳过模板指定的行数
    effective_lines = lines[template_skip_rows:]
    if len(effective_lines) < 2:
        return None, template_skip_rows

    # 第二步：用csv.Sniffer在有效行上检测
    try:
        dialect = csv.Sniffer().sniff('\n'.join(effective_lines[:50]), delimiters=''.join(common_delimiters))
        return dialect.delimiter, template_skip_rows
    except Exception:
        pass

    # 第三步：手工检测 — 找每行列数最一致且>1的分隔符，同时找到数据真正开始的行
    best_delim, best_score, best_skip = None, 0, template_skip_rows
    for d in common_delimiters:
        for offset, line in enumerate(effective_lines):
            if not line.strip():
                continue
            # 从这一行开始，检查后续行的列数一致性
            segment = [l for l in effective_lines[offset:offset + 50] if l.strip()]
            if len(segment) < 10:
                continue
            counts = [len(l.split(d)) for l in segment]
            if max(counts) <= 1:
                continue
            mode = max(set(counts), key=counts.count)
            consistency = counts.count(mode)
            if consistency > best_score and mode > 1:
                best_score = consistency
                best_delim = d
                best_skip = template_skip_rows + offset

    return best_delim, best_skip


def _detect_header(file_path, delimiter, skip_rows, encoding):
    """检测跳过skip_rows后的第一行是表头还是数据

    返回: 0 (是表头) 或 None (是数据)
    """
    import re
    try:
        with open(file_path, 'r', encoding=encoding) as f:
            for i, line in enumerate(f):
                if i < skip_rows:
                    continue
                parts = line.strip().split(delimiter)
                if len(parts) < 2:
                    continue
                data_count = 0
                header_count = 0
                for p in parts:
                    p = p.strip()
                    if not p:
                        continue
                    try:
                        float(p)
                        data_count += 1
                        continue
                    except ValueError:
                        pass
                    if re.match(r'^\d{2,4}[/-]\d{1,2}[/-]\d{1,2}', p):
                        data_count += 1
                        continue
                    if re.match(r'^[A-Za-z_]', p) or '波长' in p or '时间' in p or '温度' in p or '应变' in p:
                        header_count += 1
                        continue
                    data_count += 1
                total = data_count + header_count
                if total > 0:
                    ratio = data_count / total if total > 0 else 0
                    result = None if ratio >= 0.5 else 0
                    print(f"DEBUG _detect_header: parts[:5]={parts[:5]}, data_ratio={ratio:.2f}, has_header={result is not None}")
                    return result
                break
    except Exception as e:
        print(f"DEBUG _detect_header error: {e}")
    return None


def _try_read_csv(file_path, delimiter, skip_rows, encoding, header='infer'):
    """读取CSV/TXT，自动检测分隔符和skip_rows

    header: None = 无表头, 0 = 第一行为表头, 'infer' = 自动检测, 'detect' = 智能检测
    """
    # 从小样本检测格式（只读50KB）
    detected_delim, detected_skip = _detect_format(file_path, encoding, skip_rows)
    if detected_delim:
        delimiter = detected_delim
    # 仅当无表头时才使用自动检测的 skip_rows；有表头时强制使用模板的 skip_rows
    if header in (None, 'infer') and detected_skip:
        skip_rows = detected_skip

    if not delimiter:
        delimiter = ','

    if header == 'infer':
        header = None
    elif header == 'detect':
        header = _detect_header(file_path, delimiter, skip_rows, encoding)

    print(f"DEBUG _try_read_csv: delimiter={delimiter}, skip_rows={skip_rows}, header={header}")

    # 用检测到的参数读全文件，仅一次
    for enc in (encoding, 'gbk', 'latin-1'):
        try:
            df = pd.read_csv(file_path, delimiter=delimiter, skiprows=skip_rows,
                            header=header, encoding=enc, on_bad_lines='skip', low_memory=False)
            if df.shape[1] > 1 or len(df) > 0:
                for col in df.columns:
                    try:
                        df[col] = pd.to_numeric(df[col])
                    except (ValueError, TypeError):
                        pass
                print(f"DEBUG _try_read_csv result: columns={list(df.columns)[:5]}... (共{len(df.columns)}列), first_row={df.iloc[0].tolist()[:3]}...")
                return df, delimiter
        except UnicodeDecodeError:
            continue
        except pd.errors.ParserError:
            break

    raise pd.errors.ParserError(f"无法解析文件 '{file_path}'，请检查文件格式或手动指定分隔符和跳过的行数")


def parse_file(file_path, template):
    """根据模板解析数据文件"""
    print(f"DEBUG: template type={type(template)}, file_format={getattr(template, 'file_format', 'NOT_FOUND')}")
    # 如果template没有file_format属性，使用基于扩展名的检测
    if not hasattr(template, 'file_format'):
        # 通过文件扩展名判断类型
        ext = file_path.lower().split('.')[-1]
        if ext == 'csv':
            template.file_format = 'csv'
        elif ext in ('xlsx', 'xls'):
            template.file_format = 'xlsx'
        else:
            template.file_format = 'txt'

    if template.file_format in ('csv', 'txt', 'fiber_custom'):
        # 检测跳过元数据后的第一行是否为真实表头
        has_header = _detect_header(file_path, template.delimiter, template.skip_rows, 'utf-8')
        # 有表头 → 强制 header=0；无表头 → header=None 避免丢失数据行
        use_header = 0 if has_header is not None else None
        print(f"DEBUG parse_file: file_format={template.file_format}, has_header={has_header is not None}, use_header={use_header}, skip_rows={template.skip_rows}")
        df, detected_delim = _try_read_csv(file_path, template.delimiter, template.skip_rows, 'utf-8', header=use_header)
        # 无表头时用模板列名；有表头时也用模板列名覆盖确保一致性
        if hasattr(template, 'columns') and template.columns:
            col_names = [c['name'] for c in template.columns]
            if len(col_names) == len(df.columns):
                df.columns = col_names
                print(f"DEBUG parse_file: 应用模板列名 {col_names}")
        return df
    elif template.file_format in ('xlsx', 'xls'):
        df = pd.read_excel(file_path, header=None)
        return df
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

    # 如果有模板定义的列信息，则应用
    if hasattr(template, 'columns') and template.columns:
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
        self.sampled_data = None
        self.file_header_lines = []
        self.sampled_file_path = None
        self.current_template = None
        self.sensor_results = {}  # 存储计算后的传感器物理量
        self.cleaning_rules = [
            CleaningRule('数值范围', 'range', True, 0, 100, 'linear'),
            CleaningRule('负数检测', 'negative', True, fill_method='forward'),
        ]
        # 项目文件列表 (全局配置保存/加载使用)
        self.project_files_list = QListWidget()
        self.project_files_list.setVisible(False)

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

        # 加载AI模型配置
        self.load_ai_models_config()

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

        self.sample_data_btn = QPushButton('抽取数据')
        self.sample_data_btn.clicked.connect(self.sample_data)
        btn_layout.addWidget(self.sample_data_btn)

        self.save_sampled_btn = QPushButton('保存数据')
        self.save_sampled_btn.clicked.connect(self.save_sampled_data)
        btn_layout.addWidget(self.save_sampled_btn)

        self.save_template_btn = QPushButton('保存模板')
        self.save_template_btn.clicked.connect(self.save_template_to_file)
        btn_layout.addWidget(self.save_template_btn)

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
        self.sensor_table.setColumnCount(6)
        self.sensor_table.setHorizontalHeaderLabels(['ID', '物理位置', '类型', '公式', '常量', '启用'])
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

    def _auto_populate_fbgs(self, df):
        """从数据文件列名自动识别FBG传感器并填充FBG定义表。

        识别规则：表头以 FBG_ 开头的列 (如 FBG_A1, FBG_B1)。
        ID 按序填充为 W1, W2, ..., 波长列映射到实际列名。
        所有 FBG 波长范围统一设为 1520-1590 nm。
        """
        if df is None or df.empty:
            return 0

        fbg_cols = [str(c) for c in df.columns if str(c).upper().startswith('FBG_')]
        if not fbg_cols:
            return 0

        # 清除现有FBG定义，用自动识别的替换
        self.sensor_system.fbgs.clear()
        for i, col in enumerate(fbg_cols):
            self.sensor_system.add_fbg(FBG(f'W{i+1}', col, 1520, 1590))

        self.refresh_fbg_table()

        # 切换到光纤公式配置页，提示用户可以继续添加传感器
        print(f"[FBG自动识别] 从数据文件识别到 {len(fbg_cols)} 个FBG: {fbg_cols}")
        return len(fbg_cols)

    def refresh_sensor_table(self):
        """刷新传感器表格"""
        self.sensor_table.setRowCount(len(self.sensor_system.sensors))
        for i, sensor in enumerate(self.sensor_system.sensors):
            self.sensor_table.setItem(i, 0, QTableWidgetItem(sensor.id))
            self.sensor_table.setItem(i, 1, QTableWidgetItem(sensor.location))
            self.sensor_table.setItem(i, 2, QTableWidgetItem(sensor.get_name()))
            # 公式列：解耦型显示矩阵配置，普通型显示公式
            if sensor.sensor_type == 'decoupling' and sensor.decoupling_config:
                cfg = sensor.decoupling_config
                formula_str = f'矩阵解耦: {cfg["fbg1"]}/{cfg["fbg2"]}'
            else:
                formula_str = sensor.formula
            self.sensor_table.setItem(i, 3, QTableWidgetItem(formula_str))
            # 常量列：解耦型显示四个系数，普通型显示常量
            if sensor.sensor_type == 'decoupling' and sensor.decoupling_config:
                cfg = sensor.decoupling_config
                const_str = f'Ke1={cfg["Ke1"]:.2f}, Ke2={cfg["Ke2"]:.2f}, KT1={cfg["KT1"]:.2f}, KT2={cfg["KT2"]:.2f}'
            else:
                const_str = ', '.join([f"{k}={v:.2f}" for k, v in sensor.constants.items()])
            self.sensor_table.setItem(i, 4, QTableWidgetItem(const_str))
            self.sensor_table.setItem(i, 5, QTableWidgetItem('是' if sensor.active else '否'))
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

        # 功能按钮列表 (已移除项目内容，合并到项目资料管理)
        self.info_menu_list = QListWidget()
        self.info_menu_list.setMaximumWidth(180)
        self.info_menu_list.addItem('项目资料管理')
        self.info_menu_list.addItem('Word报告生成')
        self.info_menu_list.addItem('PPT报告生成')
        self.info_menu_list.addItem('AI诊断')
        self.info_menu_list.addItem('知识库管理')
        self.info_menu_list.addItem('技能插件中心')
        self.info_menu_list.currentRowChanged.connect(self.on_info_menu_changed)
        left_layout.addWidget(self.info_menu_list)

        left_panel.setLayout(left_layout)
        main_layout.addWidget(left_panel)

        # 右侧：内容面板
        self.report_content_stack = QStackedWidget()
        main_layout.addWidget(self.report_content_stack, 1)


        # 项目资料管理页面 (整合了项目内容功能)
        project_manage_page = self.create_project_manage_page()
        self.report_content_stack.addWidget(project_manage_page)

        # Word报告生成页面
        word_report_page = self.create_word_report_page()
        self.report_content_stack.addWidget(word_report_page)

        # PPT报告生成页面
        ppt_report_page = self.create_ppt_report_page()
        self.report_content_stack.addWidget(ppt_report_page)

        # AI诊断页面
        ai_diagnosis_page = self.create_ai_diagnosis_page()
        self.report_content_stack.addWidget(ai_diagnosis_page)

        # 知识库管理页面
        wiki_page = self.create_wiki_page()
        self.report_content_stack.addWidget(wiki_page)

        # 技能插件中心页面
        skill_center_page = self.create_skill_center_page()
        self.report_content_stack.addWidget(skill_center_page)

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
        """Word报告生成页面 - 双栏仪表盘布局"""
        page = QWidget()
        page.setStyleSheet("background-color: #f0f2f5;")
        main_layout = QHBoxLayout(page)
        main_layout.setContentsMargins(15, 15, 15, 15)
        main_layout.setSpacing(12)

        # ========== 创建水平分割器 (40:60) ==========
        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.setHandleWidth(8)
        splitter.setStyleSheet("""
            QSplitter::handle {
                background: linear-gradient(to bottom, #e0e0e0, #c0c0c0);
            }
            QSplitter::handle:hover {
                background: linear-gradient(to bottom, #1890ff, #40a9ff);
            }
        """)

        # =============================================
        # 左栏 (40%) - 控制台：配置与生成
        # =============================================
        left_panel = QWidget()
        left_layout = QVBoxLayout(left_panel)
        left_layout.setContentsMargins(0, 0, 0, 0)
        left_layout.setSpacing(12)

        # ----- 报告配置区域 -----
        config_group = QGroupBox("报告配置")
        config_group.setStyleSheet("""
            QGroupBox {
                border: 1px solid #e0e0e0;
                border-radius: 8px;
                font-weight: bold;
                color: #1890ff;
                padding: 10px;
                padding-top: 22px;
                background: white;
            }
            QGroupBox::title {
                subcontrol-origin: margin;
                left: 12px;
                padding: 0 6px;
            }
        """)
        config_layout = QVBoxLayout(config_group)
        config_layout.setSpacing(10)

        # 报告标题行
        title_row = QHBoxLayout()
        title_row.setSpacing(8)

        title_label = QLabel("报告标题:")
        title_label.setStyleSheet("font-weight: bold; color: #333;")
        title_row.addWidget(title_label)

        self.word_report_title = QLabel('未设置标题')
        self.word_report_title.setStyleSheet('color: #333; font-weight: bold; padding: 6px 10px; background: #f5f5f5; border-radius: 4px;')
        title_row.addWidget(self.word_report_title, stretch=1)

        edit_title_btn = QPushButton('编辑')
        edit_title_btn.setStyleSheet("""
            QPushButton {
                background-color: #722ed1;
                color: white;
                border: none;
                border-radius: 4px;
                padding: 6px 12px;
                font-weight: bold;
            }
            QPushButton:hover {
                background-color: #9254de;
            }
        """)
        edit_title_btn.clicked.connect(self.on_edit_word_report_title)
        title_row.addWidget(edit_title_btn)
        config_layout.addLayout(title_row)

        # 操作按钮行
        btn_row = QHBoxLayout()
        btn_row.setSpacing(8)

        req_btn = QPushButton('需求配置')
        req_btn.setStyleSheet("""
            QPushButton {
                background-color: #1890ff;
                color: white;
                border: none;
                border-radius: 4px;
                padding: 10px 14px;
                font-weight: bold;
            }
            QPushButton:hover {
                background-color: #40a9ff;
            }
        """)
        req_btn.clicked.connect(self.on_word_requirement_config)
        btn_row.addWidget(req_btn)

        template_btn = QPushButton('模板操作')
        template_btn.setStyleSheet("""
            QPushButton {
                background-color: #1890ff;
                color: white;
                border: none;
                border-radius: 4px;
                padding: 10px 14px;
                font-weight: bold;
            }
            QPushButton:hover {
                background-color: #40a9ff;
            }
        """)
        template_btn.clicked.connect(self.on_word_template_operation)
        btn_row.addWidget(template_btn)

        project_btn = QPushButton('项目文件')
        project_btn.setStyleSheet("""
            QPushButton {
                background-color: #1890ff;
                color: white;
                border: none;
                border-radius: 4px;
                padding: 10px 14px;
                font-weight: bold;
            }
            QPushButton:hover {
                background-color: #40a9ff;
            }
        """)
        project_btn.clicked.connect(self.on_word_project_files)
        btn_row.addWidget(project_btn)
        config_layout.addLayout(btn_row)
        left_layout.addWidget(config_group)

        # ----- 文件生成区域 -----
        gen_group = QGroupBox("Word文件生成")
        gen_group.setStyleSheet("""
            QGroupBox {
                border: 1px solid #e0e0e0;
                border-radius: 8px;
                font-weight: bold;
                color: #52c41a;
                padding: 10px;
                padding-top: 22px;
                background: white;
            }
            QGroupBox::title {
                subcontrol-origin: margin;
                left: 12px;
                padding: 0 6px;
            }
        """)
        gen_layout = QVBoxLayout(gen_group)
        gen_layout.setSpacing(10)

        # 状态显示
        self.word_gen_status = QLabel('● 就绪')
        self.word_gen_status.setStyleSheet('color: #666; font-weight: bold; padding: 8px; background: #f5f5f5; border-radius: 4px;')
        gen_layout.addWidget(self.word_gen_status)

        # 生成和保存按钮行 - 紧凑排列，不拉伸
        gen_btn_row = QHBoxLayout()
        gen_btn_row.setSpacing(10)

        generate_btn = QPushButton('生成报告')
        generate_btn.setMinimumHeight(44)
        generate_btn.setStyleSheet("""
            QPushButton {
                background-color: #52c41a;
                color: white;
                border: none;
                border-radius: 6px;
                padding: 12px 24px;
                font-weight: bold;
                font-size: 14px;
            }
            QPushButton:hover {
                background-color: #73d13d;
            }
        """)
        generate_btn.clicked.connect(self.on_generate_word_report)
        gen_btn_row.addWidget(generate_btn)

        save_btn = QPushButton('保存报告')
        save_btn.setMinimumHeight(44)
        save_btn.setStyleSheet("""
            QPushButton {
                background-color: #1890ff;
                color: white;
                border: none;
                border-radius: 6px;
                padding: 12px 24px;
                font-weight: bold;
                font-size: 14px;
            }
            QPushButton:hover {
                background-color: #40a9ff;
            }
        """)
        save_btn.clicked.connect(self.on_save_word_report)
        gen_btn_row.addWidget(save_btn)

        gen_btn_row.addStretch()  # 防止按钮拉伸
        gen_layout.addLayout(gen_btn_row)
        left_layout.addWidget(gen_group)

        # 左栏底部添加伸缩因子，将上方内容向上推
        left_layout.addStretch(1)

        # =============================================
        # 右栏 (60%) - 工作区：AI交互
        # =============================================
        right_panel = QWidget()
        right_layout = QVBoxLayout(right_panel)
        right_layout.setContentsMargins(0, 0, 0, 0)
        right_layout.setSpacing(12)

        # ----- AI交互区域 -----
        chat_group = QGroupBox("AI交互")
        chat_group.setStyleSheet("""
            QGroupBox {
                border: 1px solid #e0e0e0;
                border-radius: 8px;
                font-weight: bold;
                color: #faad14;
                padding: 10px;
                padding-top: 22px;
                background: white;
            }
            QGroupBox::title {
                subcontrol-origin: margin;
                left: 12px;
                padding: 0 6px;
            }
        """)
        chat_layout = QVBoxLayout(chat_group)
        chat_layout.setSpacing(10)

        # 输入文本框
        self.word_ai_chat_input = QTextEdit()
        self.word_ai_chat_input.setMaximumHeight(80)
        self.word_ai_chat_input.setPlaceholderText('输入您的指令或回复AI的提问...')
        self.word_ai_chat_input.setStyleSheet("""
            QTextEdit {
                border: 1px solid #ebebeb;
                border-radius: 4px;
                padding: 10px;
                font-size: 13px;
            }
            QTextEdit:focus {
                border-color: #faad14;
            }
        """)
        chat_layout.addWidget(self.word_ai_chat_input)

        # 发送和清除按钮行
        chat_btn_row = QHBoxLayout()
        chat_btn_row.setSpacing(10)

        send_btn = QPushButton('发送')
        send_btn.setStyleSheet("""
            QPushButton {
                background-color: #1890ff;
                color: white;
                border: none;
                border-radius: 4px;
                padding: 10px 24px;
                font-weight: bold;
            }
            QPushButton:hover {
                background-color: #40a9ff;
            }
        """)
        send_btn.clicked.connect(self.on_word_ai_send)
        chat_btn_row.addWidget(send_btn)

        clear_btn = QPushButton('清除')
        clear_btn.setStyleSheet("""
            QPushButton {
                background-color: #ff4d4f;
                color: white;
                border: none;
                border-radius: 4px;
                padding: 10px 24px;
                font-weight: bold;
            }
            QPushButton:hover {
                background-color: #ff7875;
            }
        """)
        clear_btn.clicked.connect(lambda: self.word_ai_chat_input.clear())
        chat_btn_row.addWidget(clear_btn)

        chat_btn_row.addStretch()  # 保持按钮紧凑
        chat_layout.addLayout(chat_btn_row)

        # AI交互日志 - 占据最大空间
        self.word_ai_result = QTextEdit()
        self.word_ai_result.setReadOnly(True)
        self.word_ai_result.setPlaceholderText('AI交互日志...\n\n提示：您可以在这里查看AI的处理过程和结果。')
        self.word_ai_result.setStyleSheet("""
            QTextEdit {
                border: 1px solid #ebebeb;
                border-radius: 4px;
                padding: 12px;
                font-size: 12px;
                line-height: 1.6;
                background: #fafafa;
            }
        """)
        chat_layout.addWidget(self.word_ai_result, stretch=1)  # 最大的伸缩因子
        right_layout.addWidget(chat_group)

        # 添加到分割器
        splitter.addWidget(left_panel)
        splitter.addWidget(right_panel)
        splitter.setStretchFactor(0, 40)
        splitter.setStretchFactor(1, 60)

        main_layout.addWidget(splitter)

        return page
    def create_ppt_report_page(self):
        """PPT报告生成页面 - 双栏仪表盘布局"""
        page = QWidget()
        page.setStyleSheet("background-color: #f0f2f5;")
        main_layout = QHBoxLayout(page)
        main_layout.setContentsMargins(15, 15, 15, 15)
        main_layout.setSpacing(12)

        # ========== 创建水平分割器 (40:60) ==========
        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.setHandleWidth(8)
        splitter.setStyleSheet("""
            QSplitter::handle {
                background: linear-gradient(to bottom, #e0e0e0, #c0c0c0);
            }
            QSplitter::handle:hover {
                background: linear-gradient(to bottom, #1890ff, #40a9ff);
            }
        """)

        # =============================================
        # 左栏 (40%) - 控制台：配置与生成
        # =============================================
        left_panel = QWidget()
        left_layout = QVBoxLayout(left_panel)
        left_layout.setContentsMargins(0, 0, 0, 0)
        left_layout.setSpacing(12)

        # ----- 报告配置区域 -----
        config_group = QGroupBox("报告配置")
        config_group.setStyleSheet("""
            QGroupBox {
                border: 1px solid #e0e0e0;
                border-radius: 8px;
                font-weight: bold;
                color: #1890ff;
                padding: 10px;
                padding-top: 22px;
                background: white;
            }
            QGroupBox::title {
                subcontrol-origin: margin;
                left: 12px;
                padding: 0 6px;
            }
        """)
        config_layout = QVBoxLayout(config_group)
        config_layout.setSpacing(10)

        # 报告标题行
        title_row = QHBoxLayout()
        title_row.setSpacing(8)

        title_label = QLabel("报告标题:")
        title_label.setStyleSheet("font-weight: bold; color: #333;")
        title_row.addWidget(title_label)

        self.ppt_report_title = QLabel('未设置标题')
        self.ppt_report_title.setStyleSheet('color: #333; font-weight: bold; padding: 6px 10px; background: #f5f5f5; border-radius: 4px;')
        title_row.addWidget(self.ppt_report_title, stretch=1)

        edit_title_btn = QPushButton('编辑')
        edit_title_btn.setStyleSheet("""
            QPushButton {
                background-color: #722ed1;
                color: white;
                border: none;
                border-radius: 4px;
                padding: 6px 12px;
                font-weight: bold;
            }
            QPushButton:hover {
                background-color: #9254de;
            }
        """)
        edit_title_btn.clicked.connect(self.on_edit_ppt_report_title)
        title_row.addWidget(edit_title_btn)
        config_layout.addLayout(title_row)

        # 操作按钮行
        btn_row = QHBoxLayout()
        btn_row.setSpacing(8)

        req_btn = QPushButton('需求配置')
        req_btn.setStyleSheet("""
            QPushButton {
                background-color: #1890ff;
                color: white;
                border: none;
                border-radius: 4px;
                padding: 10px 14px;
                font-weight: bold;
            }
            QPushButton:hover {
                background-color: #40a9ff;
            }
        """)
        req_btn.clicked.connect(self.on_ppt_requirement_config)
        btn_row.addWidget(req_btn)

        template_btn = QPushButton('模板操作')
        template_btn.setStyleSheet("""
            QPushButton {
                background-color: #1890ff;
                color: white;
                border: none;
                border-radius: 4px;
                padding: 10px 14px;
                font-weight: bold;
            }
            QPushButton:hover {
                background-color: #40a9ff;
            }
        """)
        template_btn.clicked.connect(self.on_ppt_template_operation)
        btn_row.addWidget(template_btn)

        project_btn = QPushButton('项目文件')
        project_btn.setStyleSheet("""
            QPushButton {
                background-color: #1890ff;
                color: white;
                border: none;
                border-radius: 4px;
                padding: 10px 14px;
                font-weight: bold;
            }
            QPushButton:hover {
                background-color: #40a9ff;
            }
        """)
        project_btn.clicked.connect(self.on_ppt_project_files)
        btn_row.addWidget(project_btn)
        config_layout.addLayout(btn_row)
        left_layout.addWidget(config_group)

        # ----- 文件生成区域 -----
        gen_group = QGroupBox("PPT文件生成")
        gen_group.setStyleSheet("""
            QGroupBox {
                border: 1px solid #e0e0e0;
                border-radius: 8px;
                font-weight: bold;
                color: #52c41a;
                padding: 10px;
                padding-top: 22px;
                background: white;
            }
            QGroupBox::title {
                subcontrol-origin: margin;
                left: 12px;
                padding: 0 6px;
            }
        """)
        gen_layout = QVBoxLayout(gen_group)
        gen_layout.setSpacing(10)

        # 状态显示
        self.ppt_gen_status = QLabel('● 就绪')
        self.ppt_gen_status.setStyleSheet('color: #666; font-weight: bold; padding: 8px; background: #f5f5f5; border-radius: 4px;')
        gen_layout.addWidget(self.ppt_gen_status)

        # 生成和保存按钮行 - 紧凑排列，不拉伸
        gen_btn_row = QHBoxLayout()
        gen_btn_row.setSpacing(10)

        generate_btn = QPushButton('生成报告')
        generate_btn.setMinimumHeight(44)
        generate_btn.setStyleSheet("""
            QPushButton {
                background-color: #52c41a;
                color: white;
                border: none;
                border-radius: 6px;
                padding: 12px 24px;
                font-weight: bold;
                font-size: 14px;
            }
            QPushButton:hover {
                background-color: #73d13d;
            }
        """)
        generate_btn.clicked.connect(self.on_generate_ppt_report)
        gen_btn_row.addWidget(generate_btn)

        save_btn = QPushButton('保存报告')
        save_btn.setMinimumHeight(44)
        save_btn.setStyleSheet("""
            QPushButton {
                background-color: #1890ff;
                color: white;
                border: none;
                border-radius: 6px;
                padding: 12px 24px;
                font-weight: bold;
                font-size: 14px;
            }
            QPushButton:hover {
                background-color: #40a9ff;
            }
        """)
        save_btn.clicked.connect(self.on_save_ppt_report)
        gen_btn_row.addWidget(save_btn)

        gen_btn_row.addStretch()  # 防止按钮拉伸
        gen_layout.addLayout(gen_btn_row)
        left_layout.addWidget(gen_group)

        # 左栏底部添加伸缩因子，将上方内容向上推
        left_layout.addStretch(1)

        # =============================================
        # 右栏 (60%) - 工作区：AI交互
        # =============================================
        right_panel = QWidget()
        right_layout = QVBoxLayout(right_panel)
        right_layout.setContentsMargins(0, 0, 0, 0)
        right_layout.setSpacing(12)

        # ----- AI交互区域 -----
        chat_group = QGroupBox("AI交互")
        chat_group.setStyleSheet("""
            QGroupBox {
                border: 1px solid #e0e0e0;
                border-radius: 8px;
                font-weight: bold;
                color: #faad14;
                padding: 10px;
                padding-top: 22px;
                background: white;
            }
            QGroupBox::title {
                subcontrol-origin: margin;
                left: 12px;
                padding: 0 6px;
            }
        """)
        chat_layout = QVBoxLayout(chat_group)
        chat_layout.setSpacing(10)

        # 输入文本框
        self.ppt_ai_chat_input = QTextEdit()
        self.ppt_ai_chat_input.setMaximumHeight(80)
        self.ppt_ai_chat_input.setPlaceholderText('输入您的指令或回复AI的提问...')
        self.ppt_ai_chat_input.setStyleSheet("""
            QTextEdit {
                border: 1px solid #ebebeb;
                border-radius: 4px;
                padding: 10px;
                font-size: 13px;
            }
            QTextEdit:focus {
                border-color: #faad14;
            }
        """)
        chat_layout.addWidget(self.ppt_ai_chat_input)

        # 发送和清除按钮行
        chat_btn_row = QHBoxLayout()
        chat_btn_row.setSpacing(10)

        send_btn = QPushButton('发送')
        send_btn.setStyleSheet("""
            QPushButton {
                background-color: #1890ff;
                color: white;
                border: none;
                border-radius: 4px;
                padding: 10px 24px;
                font-weight: bold;
            }
            QPushButton:hover {
                background-color: #40a9ff;
            }
        """)
        send_btn.clicked.connect(self.on_ppt_ai_send)
        chat_btn_row.addWidget(send_btn)

        clear_btn = QPushButton('清除')
        clear_btn.setStyleSheet("""
            QPushButton {
                background-color: #ff4d4f;
                color: white;
                border: none;
                border-radius: 4px;
                padding: 10px 24px;
                font-weight: bold;
            }
            QPushButton:hover {
                background-color: #ff7875;
            }
        """)
        clear_btn.clicked.connect(lambda: self.ppt_ai_chat_input.clear())
        chat_btn_row.addWidget(clear_btn)

        chat_btn_row.addStretch()  # 保持按钮紧凑
        chat_layout.addLayout(chat_btn_row)

        # AI交互日志 - 占据最大空间
        self.ppt_ai_result = QTextEdit()
        self.ppt_ai_result.setReadOnly(True)
        self.ppt_ai_result.setPlaceholderText('AI交互日志...\n\n提示：您可以在这里查看AI的处理过程和结果。')
        self.ppt_ai_result.setStyleSheet("""
            QTextEdit {
                border: 1px solid #ebebeb;
                border-radius: 4px;
                padding: 12px;
                font-size: 12px;
                line-height: 1.6;
                background: #fafafa;
            }
        """)
        chat_layout.addWidget(self.ppt_ai_result, stretch=1)  # 最大的伸缩因子
        right_layout.addWidget(chat_group)

        # 添加到分割器
        splitter.addWidget(left_panel)
        splitter.addWidget(right_panel)
        splitter.setStretchFactor(0, 40)
        splitter.setStretchFactor(1, 60)

        main_layout.addWidget(splitter)

        return page

    def create_ai_diagnosis_page(self):
        """AI诊断页面 - 40:60双栏仪表盘布局"""
        page = QWidget()
        page.setStyleSheet("background-color: #f0f2f5;")
        main_layout = QVBoxLayout(page)
        main_layout.setContentsMargins(20, 20, 20, 20)
        main_layout.setSpacing(14)

        # ========== 页面标题栏 ==========
        self.ai_page_header = QWidget()
        header_layout = QHBoxLayout(self.ai_page_header)
        header_layout.setContentsMargins(0, 0, 0, 0)
        header_layout.setSpacing(16)

        title = QLabel("AI 数据诊断中心")
        title.setStyleSheet("font-size: 20px; font-weight: bold; color: #333;")
        header_layout.addWidget(title)
        header_layout.addStretch()

        # 状态指示器
        self.ai_status_label = QLabel("● 未连接")
        self.ai_status_label.setStyleSheet("""
            color: #ff4d4f;
            font-weight: bold;
            font-size: 13px;
            padding: 6px 14px;
            background: #fff1f0;
            border-radius: 4px;
        """)
        header_layout.addWidget(self.ai_status_label)

        self.ai_latency_label = QLabel("延迟: -- ms")
        self.ai_latency_label.setStyleSheet("""
            color: #666;
            font-size: 12px;
            padding: 6px 14px;
            background: #f5f5f5;
            border-radius: 4px;
        """)
        header_layout.addWidget(self.ai_latency_label)

        main_layout.addWidget(self.ai_page_header)

        # ========== 创建水平分割器 (40:60) ==========
        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.setHandleWidth(8)
        splitter.setStyleSheet("""
            QSplitter::handle {
                background: linear-gradient(to bottom, #e0e0e0, #c0c0c0);
            }
            QSplitter::handle:hover {
                background: linear-gradient(to bottom, #1890ff, #40a9ff);
            }
        """)

        # =============================================
        # 左侧面板 (40%) - 配置区域
        # =============================================
        self.ai_left_panel = QWidget()
        left_layout = QVBoxLayout(self.ai_left_panel)
        left_layout.setContentsMargins(0, 0, 0, 0)
        left_layout.setSpacing(12)

        # ----- 项目资料区 -----
        project_group = QGroupBox("项目资料")
        project_group.setStyleSheet("""
            QGroupBox {
                border: 1px solid #e0e0e0;
                border-radius: 8px;
                font-weight: bold;
                color: #722ed1;
                padding: 10px;
                padding-top: 22px;
                background: white;
            }
            QGroupBox::title {
                subcontrol-origin: margin;
                left: 12px;
                padding: 0 6px;
            }
        """)
        project_layout = QVBoxLayout(project_group)
        project_layout.setSpacing(10)

        # 项目资料说明
        project_info = QLabel("管理项目相关文件资料")
        project_info.setStyleSheet("color: #666; font-size: 11px; font-weight: normal;")
        project_layout.addWidget(project_info)

        # 项目资料列表
        self.ai_proj_files_list = QListWidget()
        self.ai_proj_files_list.setStyleSheet("""
            QListWidget {
                border: 1px solid #ebebeb;
                border-radius: 4px;
                background: white;
            }
            QListWidget::item {
                padding: 8px;
                border-bottom: 1px solid #f5f5f5;
            }
            QListWidget::item:selected {
                background: #e6f4ff;
            }
        """)
        project_layout.addWidget(self.ai_proj_files_list)

        # 项目资料按钮行
        proj_btn_layout = QHBoxLayout()
        proj_btn_layout.setSpacing(8)

        add_proj_btn = QPushButton("添加文件")
        add_proj_btn.setStyleSheet("""
            QPushButton {
                background-color: #52c41a;
                color: white;
                border: none;
                border-radius: 4px;
                padding: 8px 12px;
                font-weight: bold;
            }
            QPushButton:hover {
                background-color: #73d13d;
            }
        """)
        add_proj_btn.clicked.connect(self.on_ai_add_project_file)
        proj_btn_layout.addWidget(add_proj_btn)

        edit_proj_btn = QPushButton("编辑文件")
        edit_proj_btn.setStyleSheet("""
            QPushButton {
                background-color: #1890ff;
                color: white;
                border: none;
                border-radius: 4px;
                padding: 8px 12px;
                font-weight: bold;
            }
            QPushButton:hover {
                background-color: #40a9ff;
            }
        """)
        edit_proj_btn.clicked.connect(self.on_ai_edit_project_file)
        proj_btn_layout.addWidget(edit_proj_btn)

        remove_proj_btn = QPushButton("移除")
        remove_proj_btn.setStyleSheet("""
            QPushButton {
                background-color: #ff4d4f;
                color: white;
                border: none;
                border-radius: 4px;
                padding: 8px 12px;
                font-weight: bold;
            }
            QPushButton:hover {
                background-color: #ff7875;
            }
        """)
        remove_proj_btn.clicked.connect(self.on_ai_delete_project_file)
        proj_btn_layout.addWidget(remove_proj_btn)
        project_layout.addLayout(proj_btn_layout)
        left_layout.addWidget(project_group)

        # ----- 需求文件区 -----
        req_group = QGroupBox("需求文件")
        req_group.setStyleSheet("""
            QGroupBox {
                border: 1px solid #e0e0e0;
                border-radius: 8px;
                font-weight: bold;
                color: #faad14;
                padding: 10px;
                padding-top: 22px;
                background: white;
            }
            QGroupBox::title {
                subcontrol-origin: margin;
                left: 12px;
                padding: 0 6px;
            }
        """)
        req_layout = QVBoxLayout(req_group)
        req_layout.setSpacing(10)

        # 需求文件说明
        req_info = QLabel("管理需求分析相关文件")
        req_info.setStyleSheet("color: #666; font-size: 11px; font-weight: normal;")
        req_layout.addWidget(req_info)

        self.ai_req_list = QListWidget()
        self.ai_req_list.setStyleSheet("""
            QListWidget {
                border: 1px solid #ebebeb;
                border-radius: 4px;
                background: white;
            }
            QListWidget::item {
                padding: 8px;
                border-bottom: 1px solid #f5f5f5;
            }
            QListWidget::item:selected {
                background: #e6f4ff;
            }
        """)
        req_layout.addWidget(self.ai_req_list)

        # 需求文件按钮行
        req_btn_layout = QHBoxLayout()
        req_btn_layout.setSpacing(8)

        add_req_btn = QPushButton("添加文件")
        add_req_btn.setStyleSheet("""
            QPushButton {
                background-color: #52c41a;
                color: white;
                border: none;
                border-radius: 4px;
                padding: 8px 12px;
                font-weight: bold;
            }
            QPushButton:hover {
                background-color: #73d13d;
            }
        """)
        add_req_btn.clicked.connect(self.on_ai_add_requirement_file)
        req_btn_layout.addWidget(add_req_btn)

        edit_req_btn = QPushButton("编辑文件")
        edit_req_btn.setStyleSheet("""
            QPushButton {
                background-color: #1890ff;
                color: white;
                border: none;
                border-radius: 4px;
                padding: 8px 12px;
                font-weight: bold;
            }
            QPushButton:hover {
                background-color: #40a9ff;
            }
        """)
        edit_req_btn.clicked.connect(self.on_ai_edit_requirement_file)
        req_btn_layout.addWidget(edit_req_btn)

        remove_req_btn = QPushButton("移除")
        remove_req_btn.setStyleSheet("""
            QPushButton {
                background-color: #ff4d4f;
                color: white;
                border: none;
                border-radius: 4px;
                padding: 8px 12px;
                font-weight: bold;
            }
            QPushButton:hover {
                background-color: #ff7875;
            }
        """)
        remove_req_btn.clicked.connect(self.on_ai_delete_requirement_file)
        req_btn_layout.addWidget(remove_req_btn)
        req_layout.addLayout(req_btn_layout)
        left_layout.addWidget(req_group)

        # ----- 模型配置区 -----
        model_group = QGroupBox("AI模型配置")
        model_group.setStyleSheet("""
            QGroupBox {
                border: 1px solid #e0e0e0;
                border-radius: 8px;
                font-weight: bold;
                color: #1890ff;
                padding: 10px;
                padding-top: 22px;
                background: white;
            }
            QGroupBox::title {
                subcontrol-origin: margin;
                left: 12px;
                padding: 0 6px;
            }
        """)
        model_layout = QVBoxLayout(model_group)
        model_layout.setSpacing(10)

        model_row = QHBoxLayout()
        model_row.setSpacing(8)

        model_row.addWidget(QLabel("模型:"))

        self.ai_model_combo = QComboBox()
        self.ai_model_combo.setStyleSheet("""
            QComboBox {
                border: 1px solid #d9d9d9;
                border-radius: 4px;
                padding: 8px 12px;
                background: white;
            }
            QComboBox:focus {
                border-color: #1890ff;
            }
        """)
        model_row.addWidget(self.ai_model_combo, stretch=1)

        config_btn = QPushButton("配置")
        config_btn.setStyleSheet("""
            QPushButton {
                background-color: #722ed1;
                color: white;
                border: none;
                border-radius: 4px;
                padding: 8px 14px;
                font-weight: bold;
            }
            QPushButton:hover {
                background-color: #9254de;
            }
        """)
        config_btn.clicked.connect(self.on_ai_model_config)
        model_row.addWidget(config_btn)
        model_layout.addLayout(model_row)
        left_layout.addWidget(model_group)

        # ----- 操作按钮区 (紧凑排列，不拉伸) -----
        btn_group = QGroupBox("执行诊断")
        btn_group.setStyleSheet("""
            QGroupBox {
                border: 1px solid #e0e0e0;
                border-radius: 8px;
                font-weight: bold;
                color: #52c41a;
                padding: 10px;
                padding-top: 22px;
                background: white;
            }
            QGroupBox::title {
                subcontrol-origin: margin;
                left: 12px;
                padding: 0 6px;
            }
        """)
        btn_layout = QVBoxLayout(btn_group)
        btn_layout.setSpacing(10)

        run_btn = QPushButton('运行AI诊断')
        run_btn.setMinimumHeight(42)
        run_btn.setStyleSheet("""
            QPushButton {
                background-color: #1890ff;
                color: white;
                border: none;
                border-radius: 6px;
                padding: 12px 20px;
                font-weight: bold;
                font-size: 14px;
            }
            QPushButton:hover {
                background-color: #40a9ff;
            }
        """)
        run_btn.clicked.connect(self.run_ai_diagnosis)
        btn_layout.addWidget(run_btn)

        multi_btn = QPushButton('运行多智能体诊断')
        multi_btn.setMinimumHeight(42)
        multi_btn.setStyleSheet("""
            QPushButton {
                background-color: #52c41a;
                color: white;
                border: none;
                border-radius: 6px;
                padding: 12px 20px;
                font-weight: bold;
                font-size: 14px;
            }
            QPushButton:hover {
                background-color: #73d13d;
            }
        """)
        multi_btn.clicked.connect(self.run_multi_agent_diagnosis)
        btn_layout.addWidget(multi_btn)

        left_layout.addWidget(btn_group)

        # =============================================
        # 右侧面板 (60%) - 综合智能终端面板
        # =============================================
        right_panel = QWidget()
        right_layout = QVBoxLayout(right_panel)
        right_layout.setContentsMargins(0, 0, 0, 0)
        right_layout.setSpacing(0)

        # ----- 综合智能终端面板 (统一容器) -----
        self.ai_terminal_panel = QFrame()
        self.ai_terminal_panel.setStyleSheet("""
            QFrame {
                border: 1px solid #d0d5dd;
                border-radius: 12px;
                background: white;
            }
        """)
        terminal_layout = QVBoxLayout(self.ai_terminal_panel)
        terminal_layout.setContentsMargins(0, 0, 0, 0)
        terminal_layout.setSpacing(0)

        # -- 动态标题栏 --
        self.ai_title_bar = QWidget()
        self.ai_title_bar.setStyleSheet("""
            QWidget {
                background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
                    stop:0 #f8f9fb, stop:1 #ffffff);
                border-top-left-radius: 12px;
                border-top-right-radius: 12px;
                border-bottom: 1px solid #e8eaed;
            }
        """)
        title_bar_layout = QHBoxLayout(self.ai_title_bar)
        title_bar_layout.setContentsMargins(16, 10, 16, 10)
        title_bar_layout.setSpacing(10)

        self.ai_terminal_title = QLabel("AI 诊断结果")
        self.ai_terminal_title.setStyleSheet("""
            font-size: 15px;
            font-weight: bold;
            color: #1a1a2e;
            background: transparent;
            border: none;
        """)
        title_bar_layout.addWidget(self.ai_terminal_title)
        title_bar_layout.addStretch()

        # Zen Mode 按钮 (右上角)
        self.ai_zen_enter_btn = QPushButton("🔍 沉浸全屏")
        self.ai_zen_enter_btn.setStyleSheet("""
            QPushButton {
                background: transparent;
                color: #666;
                border: 1px solid #d9d9d9;
                border-radius: 6px;
                padding: 6px 14px;
                font-size: 12px;
            }
            QPushButton:hover {
                border-color: #1890ff;
                color: #1890ff;
                background: #e6f4ff;
            }
        """)
        self.ai_zen_enter_btn.clicked.connect(self._enter_zen_mode)
        title_bar_layout.addWidget(self.ai_zen_enter_btn)

        self.ai_zen_exit_btn = QPushButton("↩️ 返回视图")
        self.ai_zen_exit_btn.setStyleSheet("""
            QPushButton {
                background: transparent;
                color: #1890ff;
                border: 1px solid #1890ff;
                border-radius: 6px;
                padding: 6px 14px;
                font-size: 12px;
            }
            QPushButton:hover {
                background: #e6f4ff;
            }
        """)
        self.ai_zen_exit_btn.clicked.connect(self._exit_zen_mode)
        self.ai_zen_exit_btn.setVisible(False)
        title_bar_layout.addWidget(self.ai_zen_exit_btn)

        terminal_layout.addWidget(self.ai_title_bar)

        # -- QSplitter 动态分割 (问答区 / 结果区) --
        self.ai_content_splitter = QSplitter(Qt.Orientation.Vertical)
        self.ai_content_splitter.setHandleWidth(6)
        self.ai_content_splitter.setChildrenCollapsible(False)
        self.ai_content_splitter.setStyleSheet("""
            QSplitter::handle {
                background: #e8eaed;
                border-radius: 2px;
            }
            QSplitter::handle:hover {
                background: #1890ff;
            }
        """)

        # 上半部分: AI 交互问答区
        self.ai_chat_widget = QWidget()
        self.ai_chat_widget.setStyleSheet("background: transparent;")
        chat_layout = QVBoxLayout(self.ai_chat_widget)
        chat_layout.setContentsMargins(12, 8, 12, 8)
        chat_layout.setSpacing(8)

        chat_label = QLabel("💬 交互问答")
        chat_label.setStyleSheet("font-size: 13px; font-weight: bold; color: #555; background: transparent;")
        chat_layout.addWidget(chat_label)

        self.ai_chat_text = QTextEdit()
        self.ai_chat_text.setReadOnly(True)
        self.ai_chat_text.setStyleSheet("""
            QTextEdit {
                border: 1px solid #ebebeb;
                border-radius: 6px;
                padding: 10px;
                background: #fafafa;
                font-size: 12px;
            }
        """)
        chat_layout.addWidget(self.ai_chat_text, stretch=1)

        chat_input_layout = QHBoxLayout()
        chat_input_layout.setSpacing(8)

        self.ai_chat_input = QLineEdit()
        self.ai_chat_input.setPlaceholderText("输入您的问题...")
        self.ai_chat_input.setStyleSheet("""
            QLineEdit {
                border: 1px solid #d9d9d9;
                border-radius: 6px;
                padding: 10px 14px;
            }
            QLineEdit:focus {
                border-color: #1890ff;
            }
        """)
        self.ai_chat_input.returnPressed.connect(self.ai_chat_query)
        chat_input_layout.addWidget(self.ai_chat_input, stretch=1)

        send_btn = QPushButton("发送")
        send_btn.setStyleSheet("""
            QPushButton {
                background-color: #1890ff;
                color: white;
                border: none;
                border-radius: 6px;
                padding: 10px 20px;
                font-weight: bold;
            }
            QPushButton:hover {
                background-color: #40a9ff;
            }
        """)
        send_btn.clicked.connect(self.ai_chat_query)
        chat_input_layout.addWidget(send_btn)

        clear_chat_btn = QPushButton("清除")
        clear_chat_btn.setStyleSheet("""
            QPushButton {
                padding: 10px 14px;
                border: 1px solid #d9d9d9;
                border-radius: 6px;
                background: #fff;
            }
            QPushButton:hover {
                border-color: #ff4d4f;
                color: #ff4d4f;
            }
        """)
        clear_chat_btn.clicked.connect(self.ai_chat_text.clear)
        chat_input_layout.addWidget(clear_chat_btn)

        chat_layout.addLayout(chat_input_layout)
        self.ai_content_splitter.addWidget(self.ai_chat_widget)

        # 下半部分: 诊断结果展示区
        result_widget = QWidget()
        result_widget.setStyleSheet("background: transparent;")
        result_layout = QVBoxLayout(result_widget)
        result_layout.setContentsMargins(12, 8, 12, 12)
        result_layout.setSpacing(8)

        result_label = QLabel("📊 诊断结果")
        result_label.setStyleSheet("font-size: 13px; font-weight: bold; color: #555; background: transparent;")
        result_layout.addWidget(result_label)

        self.ai_diagnosis_result = QTextEdit()
        self.ai_diagnosis_result.setReadOnly(True)
        self.ai_diagnosis_result.setPlaceholderText("AI诊断结果将显示在这里...\n\n运行常规诊断或多智能体诊断后，结果将在此区域展示。")
        self.ai_diagnosis_result.setStyleSheet("""
            QTextEdit {
                border: 1px solid #ebebeb;
                border-radius: 6px;
                padding: 14px;
                font-size: 13px;
                line-height: 1.8;
                background: #fefefe;
            }
        """)
        result_layout.addWidget(self.ai_diagnosis_result, stretch=1)
        self.ai_content_splitter.addWidget(result_widget)

        # 设置初始比例: 问答 30% / 结果 70%
        self.ai_content_splitter.setStretchFactor(0, 30)
        self.ai_content_splitter.setStretchFactor(1, 70)

        terminal_layout.addWidget(self.ai_content_splitter, stretch=1)

        # -- 底部按钮栏 --
        self.ai_bottom_bar = QWidget()
        self.ai_bottom_bar.setStyleSheet("""
            QWidget {
                background: #fafbfc;
                border-bottom-left-radius: 12px;
                border-bottom-right-radius: 12px;
                border-top: 1px solid #e8eaed;
            }
        """)
        bottom_layout = QHBoxLayout(self.ai_bottom_bar)
        bottom_layout.setContentsMargins(16, 8, 16, 8)
        bottom_layout.setSpacing(8)

        clear_diag_btn = QPushButton("清除内容")
        clear_diag_btn.setStyleSheet("""
            QPushButton {
                padding: 7px 18px;
                border: 1px solid #d9d9d9;
                border-radius: 6px;
                background: #fff;
            }
            QPushButton:hover {
                border-color: #ff4d4f;
                color: #ff4d4f;
            }
        """)
        clear_diag_btn.clicked.connect(self.ai_diagnosis_result.clear)
        bottom_layout.addWidget(clear_diag_btn)

        bottom_layout.addStretch()

        save_diag_btn = QPushButton("保存结果")
        save_diag_btn.setStyleSheet("""
            QPushButton {
                padding: 7px 18px;
                border: 1px solid #1890ff;
                border-radius: 6px;
                background: #1890ff;
                color: white;
                font-weight: bold;
            }
            QPushButton:hover {
                background: #40a9ff;
            }
        """)
        save_diag_btn.clicked.connect(self._save_diagnosis_result)
        bottom_layout.addWidget(save_diag_btn)

        terminal_layout.addWidget(self.ai_bottom_bar)

        right_layout.addWidget(self.ai_terminal_panel)

        # 添加到分割器
        splitter.addWidget(self.ai_left_panel)
        splitter.addWidget(right_panel)
        splitter.setStretchFactor(0, 40)
        splitter.setStretchFactor(1, 60)

        main_layout.addWidget(splitter)

        # 加载AI模型配置
        self.load_ai_models_config()

        return page

    def create_project_manage_page(self):
        """项目资料管理页面 - 整合项目内容管理"""
        page = QWidget()
        page.setStyleSheet("background-color: #f0f2f5;")
        main_layout = QHBoxLayout(page)
        main_layout.setContentsMargins(20, 20, 20, 20)
        main_layout.setSpacing(14)

        # ========== 创建水平分割器 ==========
        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.setHandleWidth(8)
        splitter.setStyleSheet("""
            QSplitter::handle {
                background: linear-gradient(to bottom, #e0e0e0, #c0c0c0);
            }
            QSplitter::handle:hover {
                background: linear-gradient(to bottom, #1890ff, #40a9ff);
            }
        """)

        # =============================================
        # 左侧面板 - 项目列表
        # =============================================
        left_panel = QWidget()
        left_panel.setMinimumWidth(280)
        left_layout = QVBoxLayout(left_panel)
        left_layout.setContentsMargins(0, 0, 0, 0)
        left_layout.setSpacing(12)

        # 项目列表容器
        project_list_group = QGroupBox("项目列表")
        project_list_group.setStyleSheet("""
            QGroupBox {
                border: 1px solid #e0e0e0;
                border-radius: 8px;
                font-weight: bold;
                color: #722ed1;
                padding: 10px;
                padding-top: 22px;
                background: white;
            }
            QGroupBox::title {
                subcontrol-origin: margin;
                left: 12px;
                padding: 0 6px;
            }
        """)
        project_list_inner = QVBoxLayout(project_list_group)
        project_list_inner.setSpacing(10)

        # 项目列表
        self.project_list_widget = QListWidget()
        self.project_list_widget.setStyleSheet("""
            QListWidget {
                border: 1px solid #ebebeb;
                border-radius: 4px;
                background: #fafafa;
            }
            QListWidget::item {
                padding: 12px;
                border-bottom: 1px solid #f5f5f5;
            }
            QListWidget::item:hover {
                background: #e6f4ff;
            }
            QListWidget::item:selected {
                background: #722ed1;
                color: white;
            }
        """)
        self.project_list_widget.itemClicked.connect(self.on_project_selected)
        project_list_inner.addWidget(self.project_list_widget)

        # 项目操作按钮行
        project_btn_layout = QHBoxLayout()
        project_btn_layout.setSpacing(8)

        new_project_btn = QPushButton("新建项目")
        new_project_btn.setStyleSheet("""
            QPushButton {
                background-color: #52c41a;
                color: white;
                border: none;
                border-radius: 4px;
                padding: 10px 12px;
                font-weight: bold;
            }
            QPushButton:hover {
                background-color: #73d13d;
            }
        """)
        new_project_btn.clicked.connect(self.on_new_project)
        project_btn_layout.addWidget(new_project_btn)

        delete_project_btn = QPushButton("删除项目")
        delete_project_btn.setStyleSheet("""
            QPushButton {
                background-color: #ff4d4f;
                color: white;
                border: none;
                border-radius: 4px;
                padding: 10px 12px;
                font-weight: bold;
            }
            QPushButton:hover {
                background-color: #ff7875;
            }
        """)
        delete_project_btn.clicked.connect(self.on_delete_project)
        project_btn_layout.addWidget(delete_project_btn)

        open_project_btn = QPushButton("打开项目")
        open_project_btn.setStyleSheet("""
            QPushButton {
                background-color: #1890ff;
                color: white;
                border: none;
                border-radius: 4px;
                padding: 10px 12px;
                font-weight: bold;
            }
            QPushButton:hover {
                background-color: #40a9ff;
            }
        """)
        open_project_btn.clicked.connect(self.on_open_project_folder)
        project_btn_layout.addWidget(open_project_btn)

        project_list_inner.addLayout(project_btn_layout)
        left_layout.addWidget(project_list_group)

        # 统计信息
        stats_group = QGroupBox("项目统计")
        stats_group.setStyleSheet("""
            QGroupBox {
                border: 1px solid #e0e0e0;
                border-radius: 8px;
                font-weight: bold;
                color: #1890ff;
                padding: 10px;
                padding-top: 22px;
                background: white;
            }
            QGroupBox::title {
                subcontrol-origin: margin;
                left: 12px;
                padding: 0 6px;
            }
        """)
        stats_layout = QFormLayout(stats_group)
        stats_layout.setSpacing(8)

        self.project_stats_label = QLabel("项目数: 0")
        stats_layout.addRow("项目数:", self.project_stats_label)

        self.project_folders_label = QLabel("子文件夹: 0")
        stats_layout.addRow("子文件夹:", self.project_folders_label)

        self.project_files_count_label = QLabel("文件数: 0")
        stats_layout.addRow("文件数:", self.project_files_count_label)

        left_layout.addWidget(stats_group)

        # =============================================
        # 右侧面板 - 项目内容
        # =============================================
        right_panel = QWidget()
        right_layout = QVBoxLayout(right_panel)
        right_layout.setContentsMargins(0, 0, 0, 0)
        right_layout.setSpacing(14)

        # 项目内容容器
        content_group = QGroupBox("项目内容")
        content_group.setStyleSheet("""
            QGroupBox {
                border: 1px solid #e0e0e0;
                border-radius: 8px;
                font-weight: bold;
                color: #1890ff;
                padding: 10px;
                padding-top: 22px;
                background: white;
            }
            QGroupBox::title {
                subcontrol-origin: margin;
                left: 12px;
                padding: 0 6px;
            }
        """)
        content_inner = QVBoxLayout(content_group)
        content_inner.setSpacing(10)

        # 当前项目标题
        self.current_project_label = QLabel("请选择一个项目")
        self.current_project_label.setStyleSheet("""
            color: #333;
            font-size: 15px;
            font-weight: bold;
            padding: 8px;
            background: #f5f5f5;
            border-radius: 4px;
        """)
        content_inner.addWidget(self.current_project_label)

        # 文件/文件夹树形列表
        self.project_tree_widget = QTreeWidget()
        self.project_tree_widget.setHeaderLabels(["名称", "类型", "大小"])
        self.project_tree_widget.setStyleSheet("""
            QTreeWidget {
                border: 1px solid #ebebeb;
                border-radius: 4px;
                background: white;
            }
            QTreeWidget::item {
                padding: 8px;
            }
            QTreeWidget::item:hover {
                background: #e6f4ff;
            }
            QTreeWidget::item:selected {
                background: #1890ff;
                color: white;
            }
        """)
        self.project_tree_widget.setColumnWidth(0, 200)
        self.project_tree_widget.setColumnWidth(1, 80)
        content_inner.addWidget(self.project_tree_widget, stretch=1)

        # 文件操作按钮行
        file_btn_layout = QHBoxLayout()
        file_btn_layout.setSpacing(8)

        add_folder_btn = QPushButton("新建文件夹")
        add_folder_btn.setStyleSheet("""
            QPushButton {
                background-color: #52c41a;
                color: white;
                border: none;
                border-radius: 4px;
                padding: 10px 14px;
                font-weight: bold;
            }
            QPushButton:hover {
                background-color: #73d13d;
            }
        """)
        add_folder_btn.clicked.connect(self.on_add_folder_to_project)
        file_btn_layout.addWidget(add_folder_btn)

        add_file_btn = QPushButton("添加文件")
        add_file_btn.setStyleSheet("""
            QPushButton {
                background-color: #1890ff;
                color: white;
                border: none;
                border-radius: 4px;
                padding: 10px 14px;
                font-weight: bold;
            }
            add_file_btn:hover {
                background-color: #40a9ff;
            }
        """)
        add_file_btn.clicked.connect(self.on_add_file_to_project)
        file_btn_layout.addWidget(add_file_btn)

        delete_item_btn = QPushButton("删除选中")
        delete_item_btn.setStyleSheet("""
            QPushButton {
                background-color: #ff4d4f;
                color: white;
                border: none;
                border-radius: 4px;
                padding: 10px 14px;
                font-weight: bold;
            }
            delete_item_btn:hover {
                background-color: #ff7875;
            }
        """)
        delete_item_btn.clicked.connect(self.on_delete_project_item)
        file_btn_layout.addWidget(delete_item_btn)

        open_item_btn = QPushButton("打开文件")
        open_item_btn.setStyleSheet("""
            QPushButton {
                background-color: #faad14;
                color: white;
                border: none;
                border-radius: 4px;
                padding: 10px 14px;
                font-weight: bold;
            }
            open_item_btn:hover {
                background-color: #ffc53d;
            }
        """)
        open_item_btn.clicked.connect(self.on_open_project_item)
        file_btn_layout.addWidget(open_item_btn)

        content_inner.addLayout(file_btn_layout)
        right_layout.addWidget(content_group)

        # 添加到分割器
        splitter.addWidget(left_panel)
        splitter.addWidget(right_panel)
        splitter.setStretchFactor(0, 280)
        splitter.setStretchFactor(1, 720)

        main_layout.addWidget(splitter)

        # 初始化项目列表
        self.load_project_list()

        return page

    def load_project_list(self):
        """加载项目列表"""
        self.project_list_widget.clear()
        settings = QSettings('DataProcessor', 'Pro')
        projects_dir = settings.value('projects_directory', '')

        if not projects_dir or not os.path.exists(projects_dir):
            return

        try:
            for item in os.listdir(projects_dir):
                item_path = os.path.join(projects_dir, item)
                if os.path.isdir(item_path):
                    self.project_list_widget.addItem(item)
        except Exception as e:
            print(f"加载项目列表失败: {e}")

    def on_project_selected(self, item):
        """选择项目时加载内容"""
        project_name = item.text()
        settings = QSettings('DataProcessor', 'Pro')
        projects_dir = settings.value('projects_directory', '')

        if not projects_dir:
            return

        self.current_project_path = os.path.join(projects_dir, project_name)
        self.current_project_label.setText(f"当前项目: {project_name}")
        self.current_project_label.setStyleSheet("""
            color: #1890ff;
            font-size: 15px;
            font-weight: bold;
            padding: 8px;
            background: #e6f4ff;
            border-radius: 4px;
        """)

        # 加载项目内容到树形列表
        self.load_project_tree()

    def load_project_tree(self):
        """加载项目内容到树形列表"""
        self.project_tree_widget.clear()

        if not hasattr(self, 'current_project_path') or not os.path.exists(self.current_project_path):
            return

        try:
            root = QTreeWidgetItem(self.project_tree_widget, [os.path.basename(self.current_project_path), "文件夹", "-"])
            self._load_folder_to_tree(self.current_project_path, root)
            self.project_tree_widget.expandAll()
        except Exception as e:
            print(f"加载项目内容失败: {e}")

    def _load_folder_to_tree(self, folder_path, parent_item):
        """递归加载文件夹到树形列表"""
        try:
            for item_name in sorted(os.listdir(folder_path)):
                item_path = os.path.join(folder_path, item_name)
                if os.path.isdir(item_path):
                    folder_item = QTreeWidgetItem(parent_item, [item_name, "文件夹", "-"])
                    self._load_folder_to_tree(item_path, folder_item)
                else:
                    size = os.path.getsize(item_path)
                    size_str = self._format_size(size)
                    QTreeWidgetItem(parent_item, [item_name, "文件", size_str])
        except Exception as e:
            print(f"加载文件夹失败: {e}")

    def _format_size(self, size):
        """格式化文件大小"""
        for unit in ['B', 'KB', 'MB', 'GB']:
            if size < 1024.0:
                return f"{size:.1f} {unit}"
            size /= 1024.0
        return f"{size:.1f} TB"

    def on_new_project(self):
        """新建项目"""
        settings = QSettings('DataProcessor', 'Pro')
        projects_dir = settings.value('projects_directory', '')

        if not projects_dir or not os.path.exists(projects_dir):
            # 选择项目根目录
            dir_path = QFileDialog.getExistingDirectory(
                self, '选择项目存储目录', '.',
                QFileDialog.Option.ShowDirsOnly
            )
            if not dir_path:
                return
            projects_dir = dir_path
            settings.setValue('projects_directory', projects_dir)

        # 输入项目名称
        project_name, ok = QInputDialog.getText(self, '新建项目', '请输入项目名称:')
        if not ok or not project_name.strip():
            return

        project_name = project_name.strip()
        project_path = os.path.join(projects_dir, project_name)

        if os.path.exists(project_path):
            QMessageBox.warning(self, '警告', '项目已存在!')
            return

        try:
            # 创建项目文件夹
            os.makedirs(project_path)

            # 创建默认子文件夹
            default_folders = ['方案', '数据', '图片', '视频', '总结', '图纸', '其它']
            for folder in default_folders:
                os.makedirs(os.path.join(project_path, folder))

            # 创建README文件
            readme_path = os.path.join(project_path, '项目说明.txt')
            with open(readme_path, 'w', encoding='utf-8') as f:
                f.write(f"项目名称: {project_name}\n")
                f.write(f"创建时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n\n")
                f.write("项目文件夹结构:\n")
                for folder in default_folders:
                    f.write(f"- {folder}/\n")

            self.load_project_list()
            QMessageBox.information(self, '成功', f'项目 "{project_name}" 创建成功!')
        except Exception as e:
            QMessageBox.warning(self, '错误', f'创建项目失败: {str(e)}')

    def on_delete_project(self):
        """删除项目"""
        current_item = self.project_list_widget.currentItem()
        if not current_item:
            QMessageBox.warning(self, '提示', '请先选择要删除的项目')
            return

        project_name = current_item.text()
        reply = QMessageBox.question(
            self, '确认删除',
            f'确定删除项目 "{project_name}" 吗?\n此操作不可恢复!',
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
        )

        if reply != QMessageBox.StandardButton.Yes:
            return

        settings = QSettings('DataProcessor', 'Pro')
        projects_dir = settings.value('projects_directory', '')

        if not projects_dir:
            return

        project_path = os.path.join(projects_dir, project_name)

        try:
            import shutil
            shutil.rmtree(project_path)
            self.load_project_list()
            self.project_tree_widget.clear()
            self.current_project_label.setText("请选择一个项目")
            self.current_project_label.setStyleSheet("""
                color: #333;
                font-size: 15px;
                font-weight: bold;
                padding: 8px;
                background: #f5f5f5;
                border-radius: 4px;
            """)
            QMessageBox.information(self, '成功', f'项目 "{project_name}" 已删除')
        except Exception as e:
            QMessageBox.warning(self, '错误', f'删除项目失败: {str(e)}')

    def on_open_project_folder(self):
        """用资源管理器打开项目文件夹"""
        current_item = self.project_list_widget.currentItem()
        if not current_item:
            QMessageBox.warning(self, '提示', '请先选择要打开的项目')
            return

        project_name = current_item.text()
        settings = QSettings('DataProcessor', 'Pro')
        projects_dir = settings.value('projects_directory', '')

        if not projects_dir:
            return

        project_path = os.path.join(projects_dir, project_name)

        if os.path.exists(project_path):
            os.startfile(project_path)

    def on_add_folder_to_project(self):
        """在项目中新建文件夹"""
        if not hasattr(self, 'current_project_path') or not os.path.exists(self.current_project_path):
            QMessageBox.warning(self, '提示', '请先选择项目')
            return

        folder_name, ok = QInputDialog.getText(self, '新建文件夹', '请输入文件夹名称:')
        if not ok or not folder_name.strip():
            return

        folder_name = folder_name.strip()
        folder_path = os.path.join(self.current_project_path, folder_name)

        if os.path.exists(folder_path):
            QMessageBox.warning(self, '警告', '文件夹已存在!')
            return

        try:
            os.makedirs(folder_path)
            self.load_project_tree()
            QMessageBox.information(self, '成功', f'文件夹 "{folder_name}" 创建成功!')
        except Exception as e:
            QMessageBox.warning(self, '错误', f'创建文件夹失败: {str(e)}')

    def on_add_file_to_project(self):
        """在项目中添加文件"""
        if not hasattr(self, 'current_project_path') or not os.path.exists(self.current_project_path):
            QMessageBox.warning(self, '提示', '请先选择项目')
            return

        settings = QSettings('DataProcessor', 'Pro')
        last_dir = settings.value('project_file_last_dir', self.current_project_path)

        file_paths, _ = QFileDialog.getOpenFileNames(
            self, '选择要添加的文件', last_dir,
            '所有文件 (*.*);;文档 (*.doc *.docx *.pdf *.txt);;图片 (*.png *.jpg *.jpeg *.gif);;视频 (*.mp4 *.avi *.mov)'
        )

        if not file_paths:
            return

        try:
            settings.setValue('project_file_last_dir', os.path.dirname(file_paths[0]))

            # 获取当前选中的节点
            current_item = self.project_tree_widget.currentItem()
            target_dir = self.current_project_path

            if current_item:
                item_type = current_item.text(1)
                if item_type == "文件夹":
                    target_dir = os.path.join(self.current_project_path, current_item.text(0))

            # 复制文件
            import shutil
            for file_path in file_paths:
                file_name = os.path.basename(file_path)
                dest_path = os.path.join(target_dir, file_name)
                shutil.copy2(file_path, dest_path)

            self.load_project_tree()
            QMessageBox.information(self, '成功', f'已添加 {len(file_paths)} 个文件!')
        except Exception as e:
            QMessageBox.warning(self, '错误', f'添加文件失败: {str(e)}')

    def on_delete_project_item(self):
        """删除选中的文件或文件夹"""
        current_item = self.project_tree_widget.currentItem()
        if not current_item:
            QMessageBox.warning(self, '提示', '请先选择要删除的内容')
            return

        item_name = current_item.text(0)
        item_type = current_item.text(1)

        if item_type == "文件夹" and item_name == os.path.basename(self.current_project_path):
            QMessageBox.warning(self, '警告', '不能删除项目根目录')
            return

        reply = QMessageBox.question(
            self, '确认删除',
            f'确定删除 {"文件夹" if item_type == "文件夹" else "文件"} "{item_name}" 吗?\n此操作不可恢复!',
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
        )

        if reply != QMessageBox.StandardButton.Yes:
            return

        try:
            item_path = os.path.join(self.current_project_path, item_name)
            if os.path.isdir(item_path):
                import shutil
                shutil.rmtree(item_path)
            else:
                os.remove(item_path)

            self.load_project_tree()
            QMessageBox.information(self, '成功', f'"{item_name}" 已删除')
        except Exception as e:
            QMessageBox.warning(self, '错误', f'删除失败: {str(e)}')

    def on_open_project_item(self):
        """打开选中的文件"""
        current_item = self.project_tree_widget.currentItem()
        if not current_item:
            QMessageBox.warning(self, '提示', '请先选择要打开的文件')
            return

        item_name = current_item.text(0)
        item_type = current_item.text(1)

        if item_type == "文件夹":
            item_path = os.path.join(self.current_project_path, item_name)
            os.startfile(item_path)
        else:
            item_path = os.path.join(self.current_project_path, item_name)
            if os.path.exists(item_path):
                os.startfile(item_path)
            else:
                QMessageBox.warning(self, '错误', '文件不存在')

    def refresh_project_files(self):
        """刷新项目文件列表"""
        self.load_project_list()

    def clear_all_project_files(self):
        """清空所有项目文件"""
        self.project_list_widget.clear()
        self.project_tree_widget.clear()
        self.current_project_label.setText("请选择一个项目")

    # ============ 知识库管理页面 ============
    def create_wiki_page(self):
        """知识库管理页面 - 250:850分割器布局"""
        page = QWidget()
        main_layout = QHBoxLayout(page)
        main_layout.setContentsMargins(20, 20, 20, 20)
        main_layout.setSpacing(14)

        # 创建水平分割器 [250, 850]
        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.setHandleWidth(8)
        splitter.setStyleSheet("""
            QSplitter::handle {
                background: linear-gradient(to bottom, #e0e0e0, #c0c0c0);
            }
            QSplitter::handle:hover {
                background: linear-gradient(to bottom, #1890ff, #40a9ff);
            }
        """)

        # =============================================
        # 左侧面板 (250px) - Wiki列表
        # =============================================
        left_panel = QWidget()
        left_panel.setMinimumWidth(250)
        left_layout = QVBoxLayout(left_panel)
        left_layout.setContentsMargins(0, 0, 0, 0)
        left_layout.setSpacing(12)

        # ----- 页面列表容器 -----
        list_group = QGroupBox("知识库页面")
        list_group.setStyleSheet("""
            QGroupBox {
                border: 1px solid #e0e0e0;
                border-radius: 8px;
                font-weight: bold;
                color: #1890ff;
                padding: 10px;
                padding-top: 22px;
                background: white;
            }
            QGroupBox::title {
                subcontrol-origin: margin;
                left: 12px;
                padding: 0 6px;
            }
        """)
        list_inner = QVBoxLayout(list_group)
        list_inner.setSpacing(10)

        # 页面列表
        self.wiki_page_list = QListWidget()
        self.wiki_page_list.setStyleSheet("""
            QListWidget {
                border: 1px solid #ebebeb;
                border-radius: 4px;
                background: #fafafa;
            }
            QListWidget::item {
                padding: 10px 12px;
                border-bottom: 1px solid #f5f5f5;
            }
            QListWidget::item:hover {
                background: #e6f4ff;
            }
            QListWidget::item:selected {
                background: #1890ff;
                color: white;
            }
        """)
        self.wiki_page_list.itemClicked.connect(self.on_wiki_page_clicked)
        list_inner.addWidget(self.wiki_page_list)

        # ----- 搜索区域 -----
        search_layout = QHBoxLayout()
        search_layout.setSpacing(8)

        self.wiki_search_input = QLineEdit()
        self.wiki_search_input.setPlaceholderText("搜索关键词...")
        self.wiki_search_input.setStyleSheet("""
            QLineEdit {
                border: 1px solid #d9d9d9;
                border-radius: 4px;
                padding: 8px 10px;
                background: white;
            }
            QLineEdit:focus {
                border-color: #1890ff;
            }
        """)
        search_layout.addWidget(self.wiki_search_input, stretch=1)

        wiki_search_btn = QPushButton("搜索")
        wiki_search_btn.setStyleSheet("""
            QPushButton {
                background-color: #1890ff;
                color: white;
                border: none;
                border-radius: 4px;
                padding: 8px 14px;
                font-weight: bold;
            }
            QPushButton:hover {
                background-color: #40a9ff;
            }
        """)
        wiki_search_btn.clicked.connect(self.on_wiki_search)
        search_layout.addWidget(wiki_search_btn)
        list_inner.addLayout(search_layout)

        # 搜索结果列表
        self.wiki_search_result = QListWidget()
        self.wiki_search_result.setMaximumHeight(90)
        self.wiki_search_result.setStyleSheet("""
            QListWidget {
                border: 1px solid #fff3cd;
                border-radius: 4px;
                background: #fffbe6;
            }
            QListWidget::item {
                padding: 6px;
                color: #ad6800;
            }
        """)
        self.wiki_search_result.itemClicked.connect(self.on_wiki_search_result_clicked)
        list_inner.addWidget(self.wiki_search_result)

        # ----- 新建/删除按钮行 -----
        action_layout = QHBoxLayout()
        action_layout.setSpacing(10)

        new_page_btn = QPushButton("新建")
        new_page_btn.setStyleSheet("""
            QPushButton {
                background-color: #52c41a;
                color: white;
                border: none;
                border-radius: 4px;
                padding: 10px 16px;
                font-weight: bold;
            }
            QPushButton:hover {
                background-color: #73d13d;
            }
        """)
        new_page_btn.clicked.connect(self.on_wiki_new_page)
        action_layout.addWidget(new_page_btn)

        del_page_btn = QPushButton("删除")
        del_page_btn.setStyleSheet("""
            QPushButton {
                background-color: #ff4d4f;
                color: white;
                border: none;
                border-radius: 4px;
                padding: 10px 16px;
                font-weight: bold;
            }
            QPushButton:hover {
                background-color: #ff7875;
            }
        """)
        del_page_btn.clicked.connect(self.on_wiki_delete_page)
        action_layout.addWidget(del_page_btn)
        list_inner.addLayout(action_layout)

        # ----- 上传/说明按钮行 -----
        extra_layout = QHBoxLayout()
        extra_layout.setSpacing(10)

        upload_btn = QPushButton("上传文件")
        upload_btn.setStyleSheet("""
            QPushButton {
                background-color: #faad14;
                color: white;
                border: none;
                border-radius: 4px;
                padding: 10px 14px;
                font-weight: bold;
            }
            QPushButton:hover {
                background-color: #ffc53d;
            }
        """)
        upload_btn.clicked.connect(self.on_wiki_upload_file)
        extra_layout.addWidget(upload_btn)

        help_btn = QPushButton("说明")
        help_btn.setStyleSheet("""
            QPushButton {
                background-color: #722ed1;
                color: white;
                border: none;
                border-radius: 4px;
                padding: 10px 14px;
                font-weight: bold;
            }
            QPushButton:hover {
                background-color: #9254de;
            }
        """)
        help_btn.clicked.connect(self.show_wiki_help)
        extra_layout.addWidget(help_btn)
        list_inner.addLayout(extra_layout)

        left_layout.addWidget(list_group)

        # =============================================
        # 右侧面板 - 编辑器
        # =============================================
        right_panel = QWidget()
        right_layout = QVBoxLayout(right_panel)
        right_layout.setContentsMargins(0, 0, 0, 0)
        right_layout.setSpacing(14)

        # 编辑器容器
        editor_group = QGroupBox("编辑内容")
        editor_group.setStyleSheet("""
            QGroupBox {
                border: 1px solid #e0e0e0;
                border-radius: 8px;
                font-weight: bold;
                color: #1890ff;
                padding: 12px;
                padding-top: 22px;
                background: white;
            }
            QGroupBox::title {
                subcontrol-origin: margin;
                left: 12px;
                padding: 0 6px;
            }
        """)
        editor_inner = QVBoxLayout(editor_group)
        editor_inner.setSpacing(12)

        # ----- 标题行 -----
        title_layout = QHBoxLayout()
        title_layout.setSpacing(12)

        title_label = QLabel("页面标题:")
        title_label.setStyleSheet("font-weight: bold; color: #333;")
        title_layout.addWidget(title_label)

        self.wiki_title_input = QLineEdit()
        self.wiki_title_input.setPlaceholderText("输入页面标题...")
        self.wiki_title_input.setStyleSheet("""
            QLineEdit {
                border: 1px solid #d9d9d9;
                border-radius: 4px;
                padding: 10px 14px;
                font-size: 14px;
                font-weight: bold;
            }
            QLineEdit:focus {
                border-color: #1890ff;
            }
        """)
        self.wiki_title_input.textChanged.connect(self.on_wiki_title_changed)
        title_layout.addWidget(self.wiki_title_input, stretch=1)

        save_wiki_btn = QPushButton("保存页面")
        save_wiki_btn.setStyleSheet("""
            QPushButton {
                background-color: #1890ff;
                color: white;
                border: none;
                border-radius: 4px;
                padding: 10px 22px;
                font-weight: bold;
            }
            QPushButton:hover {
                background-color: #40a9ff;
            }
        """)
        save_wiki_btn.clicked.connect(self.on_wiki_save)
        title_layout.addWidget(save_wiki_btn)
        editor_inner.addLayout(title_layout)

        # ----- 内容编辑器 -----
        content_label = QLabel("页面内容 (支持Markdown)")
        content_label.setStyleSheet("font-weight: bold; color: #333;")
        editor_inner.addWidget(content_label)

        self.wiki_content = QTextEdit()
        self.wiki_content.setPlaceholderText("""使用Markdown格式编写内容...

# 一级标题
## 二级标题
- 列表项
```python
代码块
```
**粗体** *斜体*
""")
        self.wiki_content.setStyleSheet("""
            QTextEdit {
                border: 1px solid #d9d9d9;
                border-radius: 6px;
                padding: 14px;
                font-family: 'Consolas', monospace;
                font-size: 13px;
                line-height: 1.7;
            }
            QTextEdit:focus {
                border-color: #1890ff;
            }
        """)
        editor_inner.addWidget(self.wiki_content, stretch=1)

        right_layout.addWidget(editor_group)

        # 添加到分割器
        splitter.addWidget(left_panel)
        splitter.addWidget(right_panel)
        splitter.setSizes([250, 850])

        main_layout.addWidget(splitter)

        # 初始化Wiki系统
        self.wiki_fs = WikiFileSystem()
        self.refresh_wiki_pages()

        return page

    def refresh_wiki_pages(self):
        """刷新Wiki页面列表"""
        self.wiki_page_list.clear()
        pages = self.wiki_fs.list_pages()
        # 解析页面列表，提取页面名
        for line in pages.split('\n'):
            if line.startswith('## '):
                page_name = line[3:].strip()
                self.wiki_page_list.addItem(page_name)

    def on_wiki_page_clicked(self, item):
        """点击Wiki页面时加载内容"""
        page_name = item.text()
        content = self.wiki_fs.read_wiki_page(page_name)
        if content and not content.startswith('Error:'):
            self.wiki_title_input.setText(page_name)
            self.wiki_content.setPlainText(content)
        else:
            self.wiki_title_input.setText(page_name)
            self.wiki_content.clear()

    def on_wiki_new_page(self):
        """新建Wiki页面"""
        self.wiki_title_input.clear()
        self.wiki_content.clear()
        self.wiki_title_input.setFocus()

    def on_wiki_delete_page(self):
        """删除Wiki页面"""
        current_item = self.wiki_page_list.currentItem()
        if current_item:
            page_name = current_item.text()
            reply = QMessageBox.question(self, '确认', f'确定删除页面 "{page_name}"？',
                                         QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
            if reply == QMessageBox.StandardButton.Yes:
                self.wiki_fs.delete_wiki_page(page_name)
                self.refresh_wiki_pages()
                self.wiki_title_input.clear()
                self.wiki_content.clear()

    def on_wiki_save(self):
        """保存Wiki页面"""
        page_name = self.wiki_title_input.text().strip()
        if not page_name:
            QMessageBox.warning(self, '警告', '请输入页面标题')
            return

        content = self.wiki_content.toPlainText()
        result = self.wiki_fs.write_wiki_page(page_name, content)
        self.refresh_wiki_pages()
        QMessageBox.information(self, '成功', result)

    def on_wiki_search(self):
        """搜索Wiki页面"""
        keyword = self.wiki_search_input.text().strip()
        if not keyword:
            return
        self.wiki_search_result.clear()
        results = self.wiki_fs.search_pages(keyword)
        if results:
            for line in results.split('\n'):
                if line.strip():
                    self.wiki_search_result.addItem(line.strip())

    def on_wiki_search_result_clicked(self, item):
        """点击搜索结果"""
        page_name = item.text()
        content = self.wiki_fs.read_wiki_page(page_name)
        if content and not content.startswith('Error:'):
            self.wiki_title_input.setText(page_name)
            self.wiki_content.setPlainText(content)

    def on_wiki_title_changed(self, text):
        """标题变化"""
        pass

    def on_wiki_upload_file(self):
        """上传文件到知识库"""
        last_dir = QSettings('DataProcessor', 'Pro').value('wiki_upload_last_dir', '')
        file_path, _ = QFileDialog.getOpenFileName(self, '选择文件', last_dir, 'All Files (*)')
        if file_path:
            QSettings('DataProcessor', 'Pro').setValue('wiki_upload_last_dir', os.path.dirname(file_path))
            result = self.wiki_fs.upload_file_to_wiki(file_path)
            self.refresh_wiki_pages()
            QMessageBox.information(self, '成功', result)

    def show_wiki_help(self):
        """显示知识库使用说明"""
        help_text = """# 知识库使用说明

## 功能概述
知识库是一个基于本地文件系统的文档管理系统，支持 Markdown 格式存储。

## 基本操作

### 查看页面
1. 在左侧列表点击页面名称
2. 右侧将显示页面内容

### 新建页面
1. 点击"新建"按钮
2. 输入页面标题
3. 编辑内容
4. 点击"保存页面"

### 编辑页面
1. 从列表选择页面
2. 修改标题或内容
3. 点击"保存页面"保存修改

### 删除页面
1. 从列表选择页面
2. 点击"删除"按钮
3. 确认删除

### 搜索
1. 在搜索框输入关键词
2. 点击"搜索"按钮
3. 结果显示在下方

### 上传文件
1. 点击"上传文件"按钮
2. 选择要上传的文件
3. 文件将自动添加到知识库

## 存储位置
知识库文件存储在: wiki_vault/pages/
索引文件: wiki_vault/wiki_map.json
"""
        help_dialog = QDialog(self)
        help_dialog.setWindowTitle('知识库使用说明')
        help_dialog.resize(700, 600)

        layout = QVBoxLayout()
        text_edit = QTextEdit()
        text_edit.setReadOnly(True)
        text_edit.setPlainText(help_text)
        layout.addWidget(text_edit)

        close_btn = QPushButton('关闭')
        close_btn.clicked.connect(help_dialog.close)
        layout.addWidget(close_btn)

        help_dialog.setLayout(layout)
        help_dialog.exec()

    # ============ 技能插件中心页面 ============
    def create_skill_center_page(self):
        """技能插件中心页面"""
        page = QWidget()
        main_layout = QVBoxLayout()

        # 说明标签
        info_label = QLabel('技能插件中心：管理AI Agent技能，支持从GitHub加载自定义技能')
        info_label.setStyleSheet('color: #666; padding: 10px;')
        main_layout.addWidget(info_label)

        # 技能表格区域
        skill_group = QGroupBox('内置技能')
        skill_layout = QVBoxLayout()

        self.skill_table = QTableWidget()
        self.skill_table.setColumnCount(3)
        self.skill_table.setHorizontalHeaderLabels(['技能名称', '功能说明', '启用'])
        self.skill_table.setRowCount(10)

        # 内置技能列表
        built_in_skills = [
            ('Python_REPL', 'Python沙箱执行器', True),
            ('arxiv', '学术文献搜索', False),
            ('ddg_search', '联网搜索', False),
            ('wikipedia', '维基百科', False),
            ('apply_butterworth_filter', '巴特沃斯滤波', True),
            ('execute_custom_formula', '自定义公式', True),
            ('read_wiki_page', 'Wiki读', True),
            ('write_wiki_page', 'Wiki写', True),
            ('list_wiki_pages', 'Wiki列表', True),
            ('search_wiki_pages', 'Wiki搜索', True),
        ]

        for i, (name, desc, enabled) in enumerate(built_in_skills):
            self.skill_table.setItem(i, 0, QTableWidgetItem(name))
            self.skill_table.setItem(i, 1, QTableWidgetItem(desc))
            checkbox = QTableWidgetItem()
            checkbox.setCheckState(Qt.CheckState.Checked if enabled else Qt.CheckState.Unchecked)
            self.skill_table.setItem(i, 2, checkbox)

        self.skill_table.resizeColumnsToContents()
        skill_layout.addWidget(self.skill_table)
        skill_group.setLayout(skill_layout)
        main_layout.addWidget(skill_group)

        # GitHub技能加载区域
        github_group = QGroupBox('GitHub技能加载')
        github_layout = QGridLayout()

        github_layout.addWidget(QLabel('仓库所有者:'), 0, 0)
        self.github_owner_input = QLineEdit()
        github_layout.addWidget(self.github_owner_input, 0, 1)

        github_layout.addWidget(QLabel('仓库名称:'), 0, 2)
        self.github_repo_input = QLineEdit()
        github_layout.addWidget(self.github_repo_input, 0, 3)

        github_layout.addWidget(QLabel('文件路径:'), 1, 0)
        self.github_path_input = QLineEdit()
        self.github_path_input.setText('skills/')
        github_layout.addWidget(self.github_path_input, 1, 1)

        github_layout.addWidget(QLabel('分支:'), 1, 2)
        self.github_branch_input = QLineEdit()
        self.github_branch_input.setText('main')
        github_layout.addWidget(self.github_branch_input, 1, 3)

        github_layout.addWidget(QLabel('GitHub Token:'), 2, 0)
        self.github_token_input = QLineEdit()
        self.github_token_input.setEchoMode(QLineEdit.EchoMode.Password)
        github_layout.addWidget(self.github_token_input, 2, 1, 1, 3)

        btn_row = QHBoxLayout()
        load_skill_btn = QPushButton('下载并加载技能')
        load_skill_btn.setStyleSheet('background-color: #52c41a; color: white;')
        load_skill_btn.clicked.connect(self.on_load_github_skill)
        btn_row.addWidget(load_skill_btn)

        view_loaded_btn = QPushButton('查看已加载')
        view_loaded_btn.setStyleSheet('background-color: #1890ff; color: white;')
        view_loaded_btn.clicked.connect(self.on_view_loaded_skills)
        btn_row.addWidget(view_loaded_btn)

        clear_all_btn = QPushButton('清除全部')
        clear_all_btn.setStyleSheet('background-color: #ff4d4f; color: white;')
        clear_all_btn.clicked.connect(self.on_clear_all_skills)
        btn_row.addWidget(clear_all_btn)

        github_layout.addLayout(btn_row, 3, 0, 1, 4)

        github_group.setLayout(github_layout)
        main_layout.addWidget(github_group)

        # Agent思考日志区
        log_group = QGroupBox('Agent 思考日志')
        log_layout = QVBoxLayout()

        self.agent_log = QTextEdit()
        self.agent_log.setReadOnly(True)
        self.agent_log.setStyleSheet('''
            QTextEdit { background-color: #1e1e1e; color: #d4d4d4; font-family: Consolas, monospace; }
        ''')
        log_layout.addWidget(self.agent_log)

        clear_log_btn = QPushButton('清空日志')
        clear_log_btn.clicked.connect(lambda: self.agent_log.clear())
        log_layout.addWidget(clear_log_btn)

        log_group.setLayout(log_layout)
        main_layout.addWidget(log_group)

        # 使用说明按钮
        help_btn = QPushButton('使用说明')
        help_btn.clicked.connect(self.show_skill_center_help)
        main_layout.addWidget(help_btn)

        page.setLayout(main_layout)
        return page

    def on_load_github_skill(self):
        """从GitHub加载技能"""
        owner = self.github_owner_input.text().strip()
        repo = self.github_repo_input.text().strip()
        path = self.github_path_input.text().strip()
        branch = self.github_branch_input.text().strip() or 'main'
        token = self.github_token_input.text().strip()

        if not owner or not repo:
            QMessageBox.warning(self, '警告', '请输入仓库所有者和仓库名称')
            return

        self.agent_log.append(f'<span style="color: blue;">[INFO]</span> 正在从 GitHub 加载技能...')
        self.agent_log.append(f'<span style="color: orange;">[LOAD]</span> {owner}/{repo}/{path}@{branch}')

        # 使用GitHub loader如果可用
        try:
            from py.github_skill_loader import GithubSkillLoader
            loader = GithubSkillLoader(token if token else None)
            self.agent_log.append(f'<span style="color: green;">[OK]</span> 技能加载功能已调用')
            QMessageBox.information(self, '提示', '技能加载功能已触发，请查看日志')
        except ImportError:
            self.agent_log.append(f'<span style="color: red;">[ERROR]</span> github_skill_loader 模块未找到')
            QMessageBox.warning(self, '警告', '技能加载模块未安装')

    def on_view_loaded_skills(self):
        """查看已加载的技能"""
        self.agent_log.append('<span style="color: blue;">[INFO]</span> 已加载技能列表:')
        # 显示内置技能状态
        for row in range(self.skill_table.rowCount()):
            name_item = self.skill_table.item(row, 0)
            check_item = self.skill_table.item(row, 2)
            if name_item and check_item:
                name = name_item.text()
                enabled = check_item.checkState() == Qt.CheckState.Checked
                status = '启用' if enabled else '禁用'
                self.agent_log.append(f'  - {name}: {status}')

    def on_clear_all_skills(self):
        """清除所有技能"""
        reply = QMessageBox.question(self, '确认', '确定清除所有已加载的技能？',
                                     QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
        if reply == QMessageBox.StandardButton.Yes:
            self.agent_log.append('<span style="color: red;">[WARN]</span> 已清除所有自定义技能')
            QMessageBox.information(self, '提示', '自定义技能已清除')

    def show_skill_center_help(self):
        """显示技能中心使用说明"""
        help_text = """# 技能插件中心使用说明

## 功能概述
技能插件中心允许你管理和加载AI Agent技能，包括内置技能和自定义GitHub技能。

## 内置技能

| 技能名称 | 功能说明 |
|---------|---------|
| Python_REPL | Python沙箱执行器 |
| arxiv | 学术文献搜索 |
| ddg_search | 联网搜索 |
| wikipedia | 维基百科 |
| apply_butterworth_filter | 巴特沃斯滤波 |
| execute_custom_formula | 自定义公式 |
| read_wiki_page | Wiki读 |
| write_wiki_page | Wiki写 |
| list_wiki_pages | Wiki列表 |
| search_wiki_pages | Wiki搜索 |

## 从GitHub加载技能

1. 填写仓库信息：
   - 仓库所有者
   - 仓库名称
   - 文件路径（如 skills/）
   - 分支（默认 main）
   - GitHub Token（可选，私有仓库需要）

2. 点击"下载并加载技能"

3. 查看已加载技能列表

## Agent日志颜色说明

- 绿色：代码执行
- 蓝色：搜索操作
- 橙色：AI思考
- 紫色：Wiki操作
- 红色：错误
"""
        help_dialog = QDialog(self)
        help_dialog.setWindowTitle('技能插件中心使用说明')
        help_dialog.resize(800, 650)

        layout = QVBoxLayout()
        text_edit = QTextEdit()
        text_edit.setReadOnly(True)
        text_edit.setPlainText(help_text)
        layout.addWidget(text_edit)

        close_btn = QPushButton('关闭')
        close_btn.clicked.connect(help_dialog.close)
        layout.addWidget(close_btn)

        help_dialog.setLayout(layout)
        help_dialog.exec()

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
            self.ai_req_list.addItem(path)

    def clear_requirement_files(self):
        """清除需求文件列表"""
        self.ai_req_list.clear()

    # ========== AI诊断页面的分析配置方法 ==========

    def on_ai_add_requirement_file(self):
        """AI诊断页添加需求文件 - 记住上次打开的目录"""
        settings = QSettings('DataProcessor', 'Pro')
        last_dir = settings.value('ai_req_last_dir', '')

        file_paths, _ = QFileDialog.getOpenFileNames(
            self, '选择需求文件', last_dir if last_dir else '.',
            '文本文件 (*.txt *.md);;所有文件 (*.*)'
        )

        if file_paths:
            # 保存当前目录供下次使用
            settings.setValue('ai_req_last_dir', os.path.dirname(file_paths[0]))

            # 初始化需求文件列表
            if not hasattr(self, 'ai_requirement_files'):
                self.ai_requirement_files = []

            # 添加文件到列表
            for path in file_paths:
                if path not in self.ai_requirement_files:
                    self.ai_requirement_files.append(path)
                    self.ai_req_list.addItem(path)

    def on_ai_delete_requirement_file(self):
        """AI诊断页删除需求文件"""
        current_item = self.ai_req_list.currentItem()
        if current_item:
            path = current_item.text()
            if hasattr(self, 'ai_requirement_files') and path in self.ai_requirement_files:
                self.ai_requirement_files.remove(path)
            self.ai_req_list.takeItem(self.ai_req_list.row(current_item))

    def on_ai_edit_requirement_file(self):
        """AI诊断页编辑需求文件 - 用系统默认程序打开"""
        current_item = self.ai_req_list.currentItem()
        if current_item:
            file_path = current_item.text()
            if os.path.exists(file_path):
                os.startfile(file_path)
            else:
                QMessageBox.warning(self, '警告', '文件不存在')

    def on_ai_add_project_file(self):
        """AI诊断页添加项目资料 - 记住上次打开的目录"""
        settings = QSettings('DataProcessor', 'Pro')
        last_dir = settings.value('ai_proj_last_dir', '')

        file_paths, _ = QFileDialog.getOpenFileNames(
            self, '选择项目资料', last_dir if last_dir else '.',
            '文档 (*.doc *.docx *.pdf);;Excel (*.xlsx *.xls);;图片 (*.png *.jpg *.jpeg);;文本 (*.txt *.csv);;所有文件 (*.*)'
        )

        if file_paths:
            # 保存当前目录供下次使用
            settings.setValue('ai_proj_last_dir', os.path.dirname(file_paths[0]))

            # 初始化项目文件列表
            if not hasattr(self, 'ai_project_files'):
                self.ai_project_files = []

            # 添加文件到列表
            for path in file_paths:
                if path not in self.ai_project_files:
                    self.ai_project_files.append(path)
                    self.ai_proj_files_list.addItem(path)

    def on_ai_delete_project_file(self):
        """AI诊断页删除项目资料"""
        current_item = self.ai_proj_files_list.currentItem()
        if current_item:
            path = current_item.text()
            if hasattr(self, 'ai_project_files') and path in self.ai_project_files:
                self.ai_project_files.remove(path)
            self.ai_proj_files_list.takeItem(self.ai_proj_files_list.row(current_item))

    def on_ai_edit_project_file(self):
        """AI诊断页编辑项目资料 - 用系统默认程序打开"""
        current_item = self.ai_proj_files_list.currentItem()
        if current_item:
            file_path = current_item.text()
            if os.path.exists(file_path):
                os.startfile(file_path)
            else:
                QMessageBox.warning(self, '警告', '文件不存在')

    # ========== AI模型连接和管理方法 ==========

    def on_ai_model_selected(self, index):
        """AI模型选择改变"""
        if index >= 0 and hasattr(self, 'ai_models_config'):
            model_name = self.ai_model_combo.currentText()
            if model_name in self.ai_models_config:
                config = self.ai_models_config[model_name]
                self.ai_api_url_display.setText(config.get('base_url', '-'))
                self.ai_model_name_display.setText(config.get('model_name', '-'))

    def on_ai_model_config(self):
        """打开AI模型配置对话框"""
        dialog = AIModelConfigDialog(self)
        dialog.exec()

    def load_ai_models_config(self):
        """加载AI模型配置"""
        config_path = 'ai_models_config.json'
        if os.path.exists(config_path):
            try:
                with open(config_path, 'r', encoding='utf-8') as f:
                    self.ai_models_config = json.load(f)
            except:
                self.ai_models_config = {}
        else:
            self.ai_models_config = {}
        self.refresh_ai_model_selector()

        # 自动连接第一个模型
        if self.ai_models_config:
            first_model = list(self.ai_models_config.keys())[0]
            self.ai_model_combo.setCurrentText(first_model)
            QTimer.singleShot(500, self.auto_connect_model)

    def auto_connect_model(self):
        """自动连接模型"""
        model_name = self.ai_model_combo.currentText()
        if model_name and model_name in self.ai_models_config:
            self.on_ai_connect_model()

    def save_ai_models_config(self):
        """保存AI模型配置"""
        config_path = 'ai_models_config.json'
        try:
            with open(config_path, 'w', encoding='utf-8') as f:
                json.dump(self.ai_models_config, f, ensure_ascii=False, indent=2)
        except Exception as e:
            print(f"保存模型配置失败: {e}")

    def refresh_ai_model_selector(self):
        """刷新模型选择器"""
        self.ai_model_combo.clear()
        for name in self.ai_models_config:
            self.ai_model_combo.addItem(name)

    def on_ai_connect_model(self):
        """连接AI模型"""
        model_name = self.ai_model_combo.currentText()
        if not model_name or model_name not in self.ai_models_config:
            QMessageBox.warning(self, '警告', '请先选择或配置一个模型')
            return

        config = self.ai_models_config[model_name]
        api_url = config.get('base_url', '')
        api_key = config.get('api_key', '')
        model = config.get('model_name', '')

        self.ai_status_label.setText('● 连接中...')
        self.ai_status_label.setStyleSheet('color: #faad14; font-weight: bold; font-size: 13px; padding: 6px 14px; background: #fffbe6; border-radius: 4px;')
        QApplication.processEvents()

        try:
            import requests
            import time
            headers = {'Authorization': f'Bearer {api_key}'} if api_key else {}

            # 标准化URL
            api_url = api_url.rstrip('/')
            if not api_url.endswith('/v1'):
                api_url = api_url + '/v1'

            # 测量延迟
            start_time = time.time()
            response = requests.get(
                f'{api_url}/models',
                headers=headers,
                timeout=10
            )
            latency = (time.time() - start_time) * 1000  # 转换为毫秒

            if response.status_code == 200:
                self.ai_status_label.setText('● 已连接')
                self.ai_status_label.setStyleSheet('color: #52c41a; font-weight: bold; font-size: 13px; padding: 6px 14px; background: #f6ffed; border-radius: 4px;')
                self.ai_latency_label.setText(f'延迟: {latency:.0f}ms')
                self.ai_latency_label.setStyleSheet('color: #52c41a; font-size: 12px; padding: 6px 14px; background: #f6ffed; border-radius: 4px;')
                self.current_model_config = config
            else:
                raise Exception(f'连接失败: {response.status_code}')
        except Exception as e:
            self.ai_status_label.setText('● 连接失败')
            self.ai_status_label.setStyleSheet('color: #ff4d4f; font-weight: bold; font-size: 13px; padding: 6px 14px; background: #fff1f0; border-radius: 4px;')
            self.ai_latency_label.setText('延迟: 失败')
            self.ai_latency_label.setStyleSheet('color: #ff4d4f; font-size: 12px; padding: 6px 14px; background: #fff1f0; border-radius: 4px;')
            QMessageBox.warning(self, '错误', f'连接失败: {str(e)}')

    def on_ai_test_model(self):
        """测试AI模型"""
        model_name = self.ai_model_combo.currentText()
        if not model_name or model_name not in self.ai_models_config:
            QMessageBox.warning(self, '警告', '请先选择或配置一个模型')
            return

        config = self.ai_models_config[model_name]
        api_url = config.get('base_url', '')
        api_key = config.get('api_key', '')
        model = config.get('model_name', '')

        self.ai_status_label.setText('● 测试中...')
        self.ai_status_label.setStyleSheet('color: #faad14; font-weight: bold; font-size: 13px; padding: 6px 14px; background: #fffbe6; border-radius: 4px;')
        QApplication.processEvents()

        try:
            import requests
            import time
            headers = {
                'Authorization': f'Bearer {api_key}',
                'Content-Type': 'application/json'
            } if api_key else {'Content-Type': 'application/json'}

            # 标准化URL
            api_url = api_url.rstrip('/')
            if not api_url.endswith('/v1'):
                api_url = api_url + '/v1'

            data = {
                'model': model,
                'messages': [{'role': 'user', 'content': 'Say "OK" in one word.'}],
                'max_tokens': 10
            }

            start_time = time.time()
            response = requests.post(
                f'{api_url}/chat/completions',
                headers=headers,
                json=data,
                timeout=30
            )
            latency = (time.time() - start_time) * 1000  # 转换为毫秒

            if response.status_code == 200:
                result = response.json()
                usage = result.get('usage', {})
                prompt_tokens = usage.get('prompt_tokens', 0)
                completion_tokens = usage.get('completion_tokens', 0)
                total_tokens = usage.get('total_tokens', 0)

                self.ai_status_label.setText('● 测试成功')
                self.ai_status_label.setStyleSheet('color: #52c41a; font-weight: bold; font-size: 13px; padding: 6px 14px; background: #f6ffed; border-radius: 4px;')
                self.ai_latency_label.setText(f'延迟: {latency:.0f}ms')
                self.ai_latency_label.setStyleSheet('color: #52c41a; font-size: 12px; padding: 6px 14px; background: #f6ffed; border-radius: 4px;')

                QMessageBox.information(self, '成功',
                    f'模型测试成功!\n输入Token: {prompt_tokens}\n输出Token: {completion_tokens}\n总Token: {total_tokens}\n延迟: {latency:.0f}ms')
            else:
                raise Exception(f'测试失败: {response.status_code} - {response.text[:200]}')
        except Exception as e:
            self.ai_status_label.setText('● 测试失败')
            self.ai_status_label.setStyleSheet('color: #ff4d4f; font-weight: bold; font-size: 13px; padding: 6px 14px; background: #fff1f0; border-radius: 4px;')
            self.ai_latency_label.setText('延迟: 失败')
            self.ai_latency_label.setStyleSheet('color: #ff4d4f; font-size: 12px; padding: 6px 14px; background: #fff1f0; border-radius: 4px;')
            QMessageBox.warning(self, '错误', f'测试失败: {str(e)}')

    def on_ai_disconnect_model(self):
        """断开AI模型连接"""
        self.ai_status_label.setText('● 未连接')
        self.ai_status_label.setStyleSheet('color: #ff4d4f; font-weight: bold; font-size: 13px; padding: 6px 14px; background: #fff1f0; border-radius: 4px;')
        self.ai_latency_label.setText('延迟: -- ms')
        self.ai_latency_label.setStyleSheet('color: #666; font-size: 12px; padding: 6px 14px; background: #f5f5f5; border-radius: 4px;')
        if hasattr(self, 'current_model_config'):
            delattr(self, 'current_model_config')

    def read_requirement_files(self) -> str:
        """读取所有需求文件的内容"""
        content_parts = []
        for i in range(self.ai_req_list.count()):
            file_path = self.ai_req_list.item(i).text()
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

    def _build_ai_data_context(self) -> str:
        """构建AI诊断所需的完整数据上下文（模板+列映射+传感器+原始数据）"""
        parts = []

        # 1. 模板信息
        if self.current_template:
            t = self.current_template
            parts.append(f"【数据加载模板】")
            parts.append(f"- 模板名称: {getattr(t, 'name', '未知')}")
            parts.append(f"- 文件格式: {getattr(t, 'file_format', '未知')}")
            parts.append(f"- 分隔符: {repr(getattr(t, 'delimiter', '未知'))}")
            parts.append(f"- 跳过行数: {getattr(t, 'skip_rows', 0)}")

            if hasattr(t, 'columns') and t.columns:
                parts.append(f"- 列定义 ({len(t.columns)}列):")
                for i, col in enumerate(t.columns):
                    parts.append(f"  列{i}: name={col.get('name','')}, type={col.get('data_type','')}, desc={col.get('comment','')}")
        else:
            parts.append("【数据加载模板】未关联模板")

        # 2. 原始数据列信息
        if self.current_data is not None:
            df = self.current_data
            parts.append(f"\n【原始数据概览】")
            parts.append(f"- 总行数: {len(df)}, 总列数: {len(df.columns)}")
            parts.append(f"- 列名: {list(df.columns)}")

            # 每列的前3个值和统计
            parts.append(f"- 每列数据预览:")
            for col in df.columns:
                col_series = df[col]
                try:
                    numeric = pd.to_numeric(col_series, errors='coerce')
                    valid = numeric.dropna()
                    if len(valid) > 0:
                        parts.append(f"  [{col}] 前3值={valid.head(3).tolist()}, min={valid.min():.4f}, max={valid.max():.4f}, mean={valid.mean():.4f}, 有效数={len(valid)}")
                    else:
                        parts.append(f"  [{col}] 前3值={col_series.head(3).tolist()} (非数值)")
                except Exception:
                    parts.append(f"  [{col}] 前3值={col_series.head(3).tolist()} (非数值)")

        # 3. FBG通道信息
        if hasattr(self, 'sensor_system') and self.sensor_system:
            ss = self.sensor_system
            if ss.fbgs:
                parts.append(f"\n【FBG通道定义】({len(ss.fbgs)}个)")
                for fbg in ss.fbgs:
                    parts.append(f"- {fbg.id}: channel={fbg.channel}, 参考行={ss.reference_row}")

        # 4. 传感器定义
        if hasattr(self, 'sensor_system') and self.sensor_system:
            ss = self.sensor_system
            if ss.sensors:
                parts.append(f"\n【传感器定义】({len(ss.sensors)}个)")
                for s in ss.sensors:
                    parts.append(f"- {s.id}: type={s.sensor_type}, formula={s.formula}, active={s.active}")
                    if s.constants:
                        parts.append(f"  常量: {s.constants}")
                    if s.decoupling_config:
                        parts.append(f"  解耦配置: {s.decoupling_config}")
                    if s.location:
                        parts.append(f"  物理位置: {s.location}")

        # 5. 传感器计算结果
        if self.sensor_results:
            parts.append(f"\n【传感器计算结果】({len(self.sensor_results)}个结果列)")
            for key, values in self.sensor_results.items():
                arr = np.array([v for v in values if v is not None and not (isinstance(v, float) and np.isnan(v))])
                if len(arr) > 0:
                    parts.append(f"- {key}: count={len(arr)}, min={arr.min():.4f}, max={arr.max():.4f}, mean={arr.mean():.4f}, std={arr.std():.4f}")
                else:
                    parts.append(f"- {key}: 无有效数据")

        return '\n'.join(parts)

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
            elif self.current_data is not None:
                numeric_cols = self.current_data.select_dtypes(include=[np.number])
                if numeric_cols.empty:
                    QMessageBox.warning(self, '警告', '数据中没有数值列')
                    return
                arr = numeric_cols.values.flatten()
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

            # 设置终端面板标题
            self.ai_terminal_title.setText("AI 诊断结果")
            req_content = self.read_requirement_files()

            # 构建完整诊断上下文（模板+列映射+FBG+传感器+原始数据）
            data_context = self._build_ai_data_context()

            prompt = f"""你是一个结构健康监测专家。以下是加载数据的完整上下文，请根据这些信息进行数据分析。

{data_context}

【分析需求】
请根据以上数据上下文进行以下分析："""

            if req_content:
                prompt += f"\n\n用户提供的需求文件内容：\n{req_content}\n\n请结合需求文件中的要求进行针对性分析。"

            prompt += """

分析要点：
1. 数据整体状态评估（正常/异常/需关注）— 结合模板配置判断数据加载是否正确
2. 各FBG通道/传感器的数据趋势（稳定/波动/上升/下降）— 逐通道分析
3. 异常点或离群值检测 — 注意区分真实异常和数据采集噪声
4. 传感器计算结果的物理意义 — 应变με、温度°C、位移等量纲是否合理
5. 如果配置了解耦传感器，评估应变/温度分离结果的可信度

请以结构化格式回复，逐项说明。如有不清楚的地方，请明确提问。"""

            self.ai_diagnosis_result.setText('正在分析，请稍候...')
            self.ai_diagnosis_stream = ""  # 用于流式接收

            # 使用配置的在线模型
            model_name = self.ai_model_combo.currentText()
            if not model_name or model_name not in self.ai_models_config:
                self.ai_diagnosis_result.setText('请先在模型配置中选择一个模型')
                return

            config = self.ai_models_config[model_name]
            api_url = config.get('base_url', '')
            api_key = config.get('api_key', '')
            model = config.get('model_name', '')
            system_prompt = config.get('system_prompt', '')

            from py.online_llm_thread import OnlineLlamaGenerateThread
            self.ollama_gen_thread = OnlineLlamaGenerateThread(
                prompt=prompt,
                api_key=api_key,
                base_url=api_url,
                model_name=model,
                system_prompt=system_prompt
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
        """诊断完成 — 保存对话上下文供后续交互使用"""
        if result and len(result) >= 5:
            self.ai_diagnosis_result.setText(result)
            # 保存完整的诊断上下文和AI回复，供后续交互问答引用
            self.ai_conversation_history = []
            self.ai_conversation_history.append({
                'role': 'system',
                'content': f'以下是数据诊断上下文:\n{self._build_ai_data_context()}'
            })
            self.ai_conversation_history.append({
                'role': 'assistant',
                'content': result
            })
            self.ai_chat_text.clear()
            self.ai_chat_text.append(f'AI: {result[:200]}...\n\n---\nAI诊断完成。如果AI在诊断中向您提问，请在下方输入框回复。')
        else:
            self.ai_diagnosis_result.setText("AI诊断失败，请尝试其他模型")

    def _on_diagnosis_error(self, err_msg):
        """诊断出错"""
        self.ai_diagnosis_result.setText(f'诊断失败: {err_msg}')

    def run_multi_agent_diagnosis(self):
        """运行多智能体诊断 (CrewAI/LangGraph workflow)"""
        if self.current_data is None or self.current_data.empty:
            QMessageBox.warning(self, '警告', '请先加载数据')
            return

        # 设置终端面板标题
        self.ai_terminal_title.setText("多智能体诊断")

        # 获取数据统计信息
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
                    'peak_to_peak': float(np.max(arr) - np.min(arr)),
                    'rms': float(np.sqrt(np.mean(arr ** 2))),
                    'count': len(arr)
                }
        elif self.current_data is not None:
            numeric_cols = self.current_data.select_dtypes(include=[np.number])
            if numeric_cols.empty:
                QMessageBox.warning(self, '警告', '数据中没有数值列')
                return
            arr = numeric_cols.values.flatten()
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

        if len(all_data) < 10:
            self.ai_diagnosis_result.setText('数据不足以进行诊断分析')
            return

        # 显示运行中状态
        self.ai_diagnosis_result.setText('正在运行多智能体诊断...\n数据科学家正在分析数据...\n请稍候...')
        QApplication.processEvents()

        try:
            # 构建用户输入
            sensor_id = list(self.sensor_results.keys())[0] if self.sensor_results else 'Sensor1'
            user_input = f"""分析传感器 {sensor_id} 的测量数据。

数据统计:
- 数据点数: {stats.get('count', len(all_data))}
- 最大值: {stats.get('max', 0):.4f}
- 最小值: {stats.get('min', 0):.4f}
- 平均值: {stats.get('mean', 0):.4f}
- 标准差: {stats.get('std', 0):.4f}
- 峰峰值: {stats.get('peak_to_peak', 0):.4f}
- RMS: {stats.get('rms', 0):.4f}

请进行数据分析和诊断。"""

            # 获取当前AI模型配置
            model_name = self.ai_model_combo.currentText()
            config = self.ai_models_config.get(model_name, {})
            api_key = config.get('api_key', '')
            base_url = config.get('base_url', '')
            model = config.get('model_name', 'deepseek-chat')

            if not api_key:
                self.ai_diagnosis_result.setText('请先在 AI 模型配置中设置 API Key 并连接模型')
                return

            # 运行多智能体工作流
            result = run_multi_agent(
                user_input,
                api_key=api_key,
                base_url=base_url,
                model_name=model,
            )

            # 显示结果
            output = []
            output.append('=' * 50)
            output.append('数据科学家报告:')
            output.append('=' * 50)
            output.append(result.get('data_scientist_report', '无'))
            output.append('')
            output.append('=' * 50)
            output.append('审查员意见:')
            output.append('=' * 50)
            output.append(result.get('audit_result', '无'))
            output.append('')
            output.append('=' * 50)
            output.append('首席专家诊断:')
            output.append('=' * 50)
            output.append(result.get('chief_scientist_report', '无'))

            self.ai_diagnosis_result.setText('\n'.join(output))

        except Exception as e:
            self.ai_diagnosis_result.setText(f'多智能体诊断出错: {str(e)}')
            import traceback
            traceback.print_exc()

    def _save_diagnosis_result(self):
        """保存AI诊断结果到文件"""
        content = self.ai_diagnosis_result.toPlainText().strip()
        if not content:
            QMessageBox.warning(self, '警告', '诊断结果为空，无需保存')
            return
        settings = QSettings('DataProcessor', 'Pro')
        last_dir = settings.value('ai_save_last_dir', '')
        file_path, _ = QFileDialog.getSaveFileName(
            self, '保存AI诊断结果', last_dir if last_dir else 'ai_diagnosis_result.txt',
            '文本文件 (*.txt);;Markdown (*.md);;所有文件 (*.*)'
        )
        if file_path:
            settings.setValue('ai_save_last_dir', os.path.dirname(file_path))
            with open(file_path, 'w', encoding='utf-8') as f:
                f.write(content)
            QMessageBox.information(self, '成功', f'诊断结果已保存到:\n{file_path}')

    def _save_multi_report(self):
        """保存多智能体诊断报告到文件"""
        content = self.ai_diagnosis_result.toPlainText().strip()
        if not content:
            QMessageBox.warning(self, '警告', '诊断报告为空，无需保存')
            return
        settings = QSettings('DataProcessor', 'Pro')
        last_dir = settings.value('ai_save_last_dir', '')
        file_path, _ = QFileDialog.getSaveFileName(
            self, '保存多智能体诊断报告', last_dir if last_dir else 'multi_agent_report.txt',
            '文本文件 (*.txt);;Markdown (*.md);;所有文件 (*.*)'
        )
        if file_path:
            settings.setValue('ai_save_last_dir', os.path.dirname(file_path))
            with open(file_path, 'w', encoding='utf-8') as f:
                f.write(content)
            QMessageBox.information(self, '成功', f'报告已保存到:\n{file_path}')

    def _enter_zen_mode(self):
        """进入沉浸全屏模式 — 隐藏导航、标题栏、聊天区，结果区独占窗口"""
        self._zen_saved_state = {}

        # 隐藏左侧导航菜单 (report_tab 的 info_menu_list 的父容器)
        if hasattr(self, 'info_menu_list'):
            nav_parent = self.info_menu_list.parent()
            if nav_parent and nav_parent.isVisible():
                self._zen_saved_state['nav_panel'] = nav_parent
                nav_parent.setVisible(False)

        # 隐藏AI诊断页左侧配置面板（项目资料、需求文件、模型配置、按钮）
        if hasattr(self, 'ai_left_panel') and self.ai_left_panel.isVisible():
            self._zen_saved_state['left_panel'] = self.ai_left_panel
            self.ai_left_panel.setVisible(False)

        # 隐藏AI诊断页的页面标题栏（页面顶部标题+状态指示器）
        if hasattr(self, 'ai_page_header') and self.ai_page_header.isVisible():
            self._zen_saved_state['page_header'] = self.ai_page_header
            self.ai_page_header.setVisible(False)

        # 隐藏底部按钮栏
        if self.ai_bottom_bar.isVisible():
            self._zen_saved_state['bottom_bar'] = self.ai_bottom_bar
            self.ai_bottom_bar.setVisible(False)

        # 隐藏交互问答区 (splitter 上半部分)
        if self.ai_chat_widget.isVisible():
            self._zen_saved_state['chat_widget'] = self.ai_chat_widget
            self.ai_chat_widget.setVisible(False)

        # 切换按钮：标题栏保留作为退出导航条
        self.ai_zen_enter_btn.setVisible(False)
        self.ai_zen_exit_btn.setVisible(True)

    def _exit_zen_mode(self):
        """退出沉浸全屏模式 — 恢复所有隐藏的组件"""
        for key in ('nav_panel', 'left_panel', 'page_header', 'bottom_bar', 'chat_widget'):
            widget = self._zen_saved_state.pop(key, None)
            if widget:
                widget.setVisible(True)

        # 切换按钮
        self.ai_zen_enter_btn.setVisible(True)
        self.ai_zen_exit_btn.setVisible(False)

    def ai_chat_query(self):
        """AI交互问答"""
        if not self.ollama_client:
            QMessageBox.warning(self, '警告', 'Ollama客户端未初始化')
            return

        if not self.ollama_client.is_available():
            QMessageBox.warning(self, '警告', '请先启动Ollama服务')
            return

        user_question = self.ai_chat_input.text().strip()
        if not user_question:
            QMessageBox.warning(self, '警告', '请输入问题')
            return

        try:
            # 构建对话上下文：包含数据上下文 + 之前的诊断结果 + 历史问答
            if not hasattr(self, 'ai_conversation_history') or not self.ai_conversation_history:
                # 尚无诊断上下文，用当前数据构建基础上下文
                data_context = self._build_ai_data_context()
                self.ai_conversation_history = [{
                    'role': 'system',
                    'content': f'以下是数据上下文:\n{data_context}'
                }]

            # 构建完整对话历史
            history_text = '\n\n'.join(
                f"[{h['role']}]: {h['content'][:2000]}" for h in self.ai_conversation_history
            )

            full_prompt = f"""{history_text}

[user]: {user_question}

请基于以上完整对话历史和数据分析上下文回答用户问题。如果之前的诊断中有需要确认的问题，请在这里回答用户。如有不清楚的请继续提问。回复简洁明了，不超过300字。"""

            self.ai_chat_text.setText('正在处理，请稍候...')
            self.ai_chat_stream = ""
            self.ai_chat_user_q = user_question

            model_name = self.ai_model_combo.currentText()
            if not model_name or model_name not in self.ai_models_config:
                self.ai_chat_text.setText('请先在模型配置中选择一个模型')
                return

            config = self.ai_models_config[model_name]
            api_url = config.get('base_url', '')
            api_key = config.get('api_key', '')
            model = config.get('model_name', '')
            system_prompt = config.get('system_prompt', '')

            from py.online_llm_thread import OnlineLlamaGenerateThread
            self.ollama_chat_thread = OnlineLlamaGenerateThread(
                prompt=full_prompt,
                api_key=api_key,
                base_url=api_url,
                model_name=model,
                system_prompt=system_prompt
            )
            self.ollama_chat_thread.token_received.connect(self._on_chat_token)
            self.ollama_chat_thread.finished.connect(self._on_chat_finished)
            self.ollama_chat_thread.error.connect(self._on_chat_error)
            self.ollama_chat_thread.start()

        except Exception as e:
            self.ai_chat_text.setText(f'查询失败: {str(e)}')

    def _on_chat_token(self, token):
        """接收聊天流式token"""
        if not hasattr(self, 'ai_chat_stream'):
            self.ai_chat_stream = ""
        self.ai_chat_stream += token
        self.ai_chat_text.setText(f'正在处理，请稍候...\n\n{self.ai_chat_stream}')

    def _on_chat_finished(self, result):
        """聊天完成 — 追加到对话历史和显示"""
        if result and len(result) >= 5:
            user_q = getattr(self, 'ai_chat_user_q', '')
            # 追加到对话历史
            if hasattr(self, 'ai_conversation_history'):
                self.ai_conversation_history.append({'role': 'user', 'content': user_q})
                self.ai_conversation_history.append({'role': 'assistant', 'content': result})
            # 更新显示
            current = self.ai_chat_text.toPlainText()
            if current.startswith('正在处理'):
                parts = current.split('\n\n', 1)
                if len(parts) > 1:
                    current = parts[1]
            if current:
                self.ai_chat_text.append('\n---\n')
            self.ai_chat_text.append(f'用户: {user_q}')
            self.ai_chat_text.append(f'AI: {result}')
            self.ai_chat_input.clear()
        else:
            self.ai_chat_text.setText('AI未返回有效响应，请尝试其他问题')

    def _on_chat_error(self, err_msg):
        """聊天出错"""
        self.ai_chat_text.setText(f'查询失败: {err_msg}')

    def clear_ai_chat(self):
        """清除对话"""
        self.ai_chat_text.clear()
        self.ai_chat_input.clear()
        # 清除聊天历史（滑动窗口）
        if hasattr(self, 'chat_history'):
            self.chat_history = []

    # ============ Word模板操作 ============
    def select_word_template(self):
        """选择Word模板"""
        last_dir = QSettings('DataProcessor', 'Pro').value('word_template_last_dir', '')
        file_path, _ = QFileDialog.getOpenFileName(
            self, '选择Word模板', last_dir, 'Word Documents (*.docx);;All Files (*)'
        )
        if file_path:
            QSettings('DataProcessor', 'Pro').setValue('word_template_last_dir', os.path.dirname(file_path))
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

            model_name = self.ai_model_combo.currentText()
            if not model_name or model_name not in self.ai_models_config:
                self.word_template_analysis.setText('请先在模型配置中选择一个模型')
                return

            config = self.ai_models_config[model_name]
            from py.online_llm_thread import OnlineLlamaGenerateThread
            self.word_template_thread = OnlineLlamaGenerateThread(
                prompt=prompt,
                api_key=config.get('api_key', ''),
                base_url=config.get('base_url', ''),
                model_name=config.get('model_name', ''),
                system_prompt=config.get('system_prompt', '')
            )
            self.word_template_thread.finished.connect(lambda r: self.word_template_analysis.setText(r if r else 'AI分析失败'))
            self.word_template_thread.error.connect(lambda e: self.word_template_analysis.setText(f'分析失败: {e}'))
            self.word_template_thread.start()
        except Exception as e:
            self.word_template_analysis.setText(f'分析失败: {str(e)}')

    # ============ PPT模板操作 ============
    def select_ppt_template(self):
        """选择PPT模板"""
        last_dir = QSettings('DataProcessor', 'Pro').value('ppt_template_last_dir', '')
        file_path, _ = QFileDialog.getOpenFileName(
            self, '选择PPT模板', last_dir, 'PowerPoint Files (*.pptx);;All Files (*)'
        )
        if file_path:
            QSettings('DataProcessor', 'Pro').setValue('ppt_template_last_dir', os.path.dirname(file_path))
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
        last_dir = QSettings('DataProcessor', 'Pro').value('project_file_last_dir', '')
        file_paths, _ = QFileDialog.getOpenFileNames(
            self, '添加项目文件', last_dir, 'All Files (*);;Text Files (*.txt);;Image Files (*.png *.jpg *.jpeg);;Word Files (*.docx);;PDF Files (*.pdf)'
        )
        if file_paths:
            QSettings('DataProcessor', 'Pro').setValue('project_file_last_dir', os.path.dirname(file_paths[0]))
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

        model_name = self.ai_model_combo.currentText()
        if not model_name or model_name not in self.ai_models_config:
            self.project_content_analysis.setText('请先在模型配置中选择一个模型')
            return

        config = self.ai_models_config[model_name]
        from py.online_llm_thread import OnlineLlamaGenerateThread
        self.project_content_thread = OnlineLlamaGenerateThread(
            prompt=prompt,
            api_key=config.get('api_key', ''),
            base_url=config.get('base_url', ''),
            model_name=config.get('model_name', ''),
            system_prompt=config.get('system_prompt', '')
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

    # ============ 全局配置操作 ============

    def save_config(self):
        """保存当前配置到文件（四个模块完整状态）"""
        file_path, _ = QFileDialog.getSaveFileName(
            self, '保存配置', '', 'JSON Files (*.json)'
        )
        if not file_path:
            return

        try:
            import json
            import numpy as np

            config = {
                'version': '2.0',
                'fbgs': [],
                'sensors': [],
                'project_files': [],
                'data_tab': {},
                'cleaning_tab': {},
                'analysis_tab': {},
            }

            # ---- 光纤公式配置模块 ----
            for fbg in self.sensor_system.fbgs:
                config['fbgs'].append({
                    'id': fbg.id,
                    'channel': fbg.channel,
                    'wavelength_min': fbg.wavelength_min,
                    'wavelength_max': fbg.wavelength_max,
                })

            for sensor in self.sensor_system.sensors:
                config['sensors'].append({
                    'id': sensor.id,
                    'sensor_type': sensor.sensor_type,
                    'formula': sensor.formula,
                    'constants': sensor.constants,
                    'active': sensor.active,
                    'decoupling_config': sensor.decoupling_config,
                    'location': sensor.location,
                })

            # ---- 项目文件 ----
            for i in range(self.project_files_list.count()):
                config['project_files'].append(self.project_files_list.item(i).text())

            # ---- 数据文件模块 ----
            config['data_tab'] = {
                'file_path': self.sampled_file_path,
                'template_id': self.current_template.id if self.current_template else None,
                'template_name': self.current_template.name if self.current_template else None,
                'file_header_lines': self.file_header_lines,
                'current_columns': self.current_columns,
            }

            # ---- 数据清洗模块 ----
            config['cleaning_tab'] = {
                'adjacent_enabled': self.adjacent_enabled.isChecked(),
                'diff_threshold': self.diff_threshold.value(),
                'nan_enabled': self.nan_enabled.isChecked(),
                'fill_method': self.fill_method_combo.currentText(),
                'custom_fill_value': self.custom_fill_value.value(),
            }

            # ---- 数据分析模块 ----
            # 保存传感器计算结果（处理 NaN）
            serializable_results = {}
            for key, values in self.sensor_results.items():
                arr = np.array(list(values), dtype=np.float64)
                arr[np.isnan(arr)] = None  # NaN -> null for JSON
                serializable_results[key] = [None if v is None else float(v) for v in arr]

            config['analysis_tab'] = {
                'data_source': self.data_source_combo.currentText(),
                'range_type': self.range_type_combo.currentText(),
                'range_start': self.range_start.value(),
                'range_end': self.range_end.value(),
                'sensor_results': serializable_results,
            }

            with open(file_path, 'w', encoding='utf-8') as f:
                json.dump(config, f, ensure_ascii=False, indent=2)

            # 统计摘要
            data_info = f'数据文件: {os.path.basename(self.sampled_file_path) if self.sampled_file_path else "无"}'
            result_count = len(self.sensor_results)
            QMessageBox.information(self, '成功',
                f'配置已保存到:\n{file_path}\n\n'
                f'  FBG: {len(config["fbgs"])} 个\n'
                f'  传感器: {len(config["sensors"])} 个\n'
                f'  {data_info}\n'
                f'  清洗规则: 已保存\n'
                f'  分析结果: {result_count} 个结果列')

        except Exception as e:
            import traceback
            QMessageBox.critical(self, '错误', f'保存配置失败: {str(e)}\n\n{traceback.format_exc()}')

    def load_config(self):
        """从文件加载配置（四个模块完整恢复）"""
        last_dir = QSettings('DataProcessor', 'Pro').value('config_load_last_dir', '')
        file_path, _ = QFileDialog.getOpenFileName(
            self, '读取配置', last_dir, 'JSON Files (*.json);;All Files (*)'
        )
        if file_path:
            QSettings('DataProcessor', 'Pro').setValue('config_load_last_dir', os.path.dirname(file_path))
        if not file_path:
            return

        try:
            import json
            import numpy as np
            with open(file_path, 'r', encoding='utf-8') as f:
                config = json.load(f)

            fbg_count, sensor_count, file_count = 0, 0, 0
            data_loaded, cleaning_restored, analysis_restored = False, False, False
            messages = []

            # ---- 光纤公式配置模块 ----
            self.sensor_system.fbgs.clear()
            for fbg_data in config.get('fbgs', []):
                wl_min = fbg_data.get('wavelength_min', fbg_data.get('k1'))
                wl_max = fbg_data.get('wavelength_max', fbg_data.get('k2'))
                fbg = FBG(fbg_data['id'], fbg_data['channel'], wl_min, wl_max)
                self.sensor_system.add_fbg(fbg)
                fbg_count += 1

            self.sensor_system.sensors.clear()
            for sensor_data in config.get('sensors', []):
                sensor = Sensor(
                    sensor_data['id'],
                    sensor_data['sensor_type'],
                    sensor_data.get('formula'),
                    sensor_data.get('constants', {}),
                    sensor_data.get('active', True),
                    sensor_data.get('decoupling_config'),
                    sensor_data.get('location', ''),
                )
                self.sensor_system.add_sensor(sensor)
                sensor_count += 1

            self.refresh_fbg_table()
            self.refresh_sensor_table()
            messages.append(f'FBG: {fbg_count} 个, 传感器: {sensor_count} 个')

            # ---- 项目文件 ----
            self.project_files_list.clear()
            for path in config.get('project_files', []):
                self.project_files_list.addItem(path)
                file_count += 1
            self.refresh_project_files()
            if file_count:
                messages.append(f'项目文件: {file_count} 个')

            # ---- 数据文件模块 ----
            data_tab = config.get('data_tab', {})
            data_path = data_tab.get('file_path') if data_tab else None
            if data_path and os.path.exists(data_path):
                try:
                    template_id = data_tab.get('template_id')
                    # 查找模板（内置 + 自定义）
                    DataProcessorWindow.load_custom_templates()
                    template = None
                    for t in DEFAULT_TEMPLATES:
                        if t.id == template_id:
                            template = t
                            break
                    if not template:
                        template = self.auto_detect_template(data_path)
                    if not template:
                        template = DataTemplate('custom_txt', '自定义文本', 'txt', '\t', 0, [])

                    df = parse_file(data_path, template)
                    self.current_data = df
                    self.sampled_file_path = data_path
                    self.current_template = template
                    self.file_header_lines = data_tab.get('file_header_lines', [])
                    self.current_columns = data_tab.get('current_columns', [str(c) for c in df.columns])
                    self.update_data_table()
                    self._auto_populate_fbgs(df)
                    self.add_recent_file(data_path, template.id)
                    data_loaded = True
                    messages.append(f'数据文件: {os.path.basename(data_path)} ({len(df)} 行)')
                except Exception as e:
                    messages.append(f'数据文件恢复失败: {str(e)}')
            elif data_path:
                messages.append(f'数据文件不存在: {os.path.basename(data_path)}')

            # ---- 数据清洗模块 ----
            cleaning_tab = config.get('cleaning_tab', {})
            if cleaning_tab:
                try:
                    self.adjacent_enabled.setChecked(cleaning_tab.get('adjacent_enabled', True))
                    self.diff_threshold.setValue(cleaning_tab.get('diff_threshold', 1.0))
                    self.nan_enabled.setChecked(cleaning_tab.get('nan_enabled', True))
                    idx = self.fill_method_combo.findText(cleaning_tab.get('fill_method', 'linear'))
                    if idx >= 0:
                        self.fill_method_combo.setCurrentIndex(idx)
                    self.custom_fill_value.setValue(cleaning_tab.get('custom_fill_value', 0))
                    cleaning_restored = True
                    messages.append('清洗规则: 已恢复')
                except Exception as e:
                    messages.append(f'清洗规则恢复失败: {str(e)}')

            # ---- 数据分析模块 ----
            analysis_tab = config.get('analysis_tab', {})
            if analysis_tab:
                try:
                    ds = analysis_tab.get('data_source', '原始数据')
                    idx = self.data_source_combo.findText(ds)
                    if idx >= 0:
                        self.data_source_combo.setCurrentIndex(idx)
                    rt = analysis_tab.get('range_type', '序号范围')
                    idx = self.range_type_combo.findText(rt)
                    if idx >= 0:
                        self.range_type_combo.setCurrentIndex(idx)
                    self.range_start.setValue(analysis_tab.get('range_start', 0))
                    if data_loaded and self.current_data is not None:
                        self.range_end.setValue(min(analysis_tab.get('range_end', 999999), len(self.current_data) - 1))

                    # 恢复传感器计算结果
                    saved_results = analysis_tab.get('sensor_results', {})
                    if saved_results:
                        restored_results = {}
                        for key, values in saved_results.items():
                            restored_results[key] = [np.nan if v is None else v for v in values]
                        self.sensor_results = restored_results
                        analysis_restored = True
                        messages.append(f'分析结果: {len(restored_results)} 个列')
                    else:
                        self.sensor_results = {}
                except Exception as e:
                    messages.append(f'分析状态恢复失败: {str(e)}')
                    self.sensor_results = {}
            else:
                self.sensor_results = {}

            # 更新分析页面传感器列表
            if hasattr(self, 'analysis_sensor_list'):
                self.refresh_analysis_sensors()

            # ---- 切换到数据文件页展示结果 ----
            if data_loaded:
                self.central_widget.setCurrentWidget(self.data_tab)
            else:
                self.central_widget.setCurrentWidget(self.sensor_tab)

            QMessageBox.information(self, '成功',
                f'配置已加载:\n' + '\n'.join(f'  {m}' for m in messages) +
                f'\n\n来自: {file_path}')

        except Exception as e:
            import traceback
            QMessageBox.critical(self, '错误', f'加载配置失败: {str(e)}\n\n{traceback.format_exc()}')

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
            last_dir = QSettings('DataProcessor', 'Pro').value('open_file_last_dir', '')
            sample_path, _ = QFileDialog.getOpenFileName(
                self, '选择数据文件', last_dir,
                '数据文件 (*.txt *.csv *.xlsx);;所有文件 (*.*)'
            )
            if not sample_path:
                return

            QSettings('DataProcessor', 'Pro').setValue('open_file_last_dir', os.path.dirname(sample_path))

            # 根据文件扩展名确定格式和分隔符
            ext = sample_path.lower().split('.')[-1]
            if ext == 'csv':
                selected_template = DataTemplate('custom_csv', '自定义CSV', 'csv', ',', 0, [])
            elif ext == 'xlsx':
                selected_template = DataTemplate('custom_xlsx', '自定义Excel', 'xlsx', ',', 0, [])
            else:
                selected_template = DataTemplate('custom_txt', '自定义文本', 'txt', '\t', 0, [])

            # 尝试自动检测更精确的格式（如ENLIGHT）
            detected = self.auto_detect_template(sample_path)
            if detected:
                selected_template = detected

            print(f"DEBUG: Created template: id={selected_template.id}, name={selected_template.name}, file_format={getattr(selected_template, 'file_format', 'NOT_FOUND')}")

            # 直接加载文件
            try:
                # 确保切换到数据文件标签页
                if hasattr(self, 'central_widget'):
                    self.central_widget.setCurrentIndex(0)
                df = parse_file(sample_path, selected_template)
                self.current_data = df
                self.sampled_file_path = sample_path
                self.current_template = selected_template
                self._load_file_header_lines(sample_path, selected_template.skip_rows)
                if hasattr(selected_template, 'columns') and selected_template.columns:
                    self.current_columns = [col['name'] for col in selected_template.columns]
                else:
                    self.current_columns = [str(c) for c in df.columns]
                self.update_data_table()
                self._auto_populate_fbgs(df)
                self.add_recent_file(sample_path, selected_template.id)
                self.status_bar.showMessage(f'已加载 {len(df)} 行数据')
                QMessageBox.information(self, '成功', f'成功加载 {len(df)} 行数据')
            except Exception as e:
                import traceback
                error_msg = f"加载文件失败: {str(e)}\n\n详细信息:\n{traceback.format_exc()}"
                QMessageBox.critical(self, '错误', error_msg)
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
            last_dir = QSettings('DataProcessor', 'Pro').value('open_file_last_dir', '')
            file_path, _ = QFileDialog.getOpenFileName(self, '选择数据文件', last_dir, file_filter)

        if not file_path:
            return

        QSettings('DataProcessor', 'Pro').setValue('open_file_last_dir', os.path.dirname(file_path))

        try:
            df = parse_file(file_path, selected_template)
            self.current_data = df
            self.sampled_file_path = file_path
            self.current_template = selected_template
            self._load_file_header_lines(file_path, selected_template.skip_rows)
            self.current_columns = [col['name'] for col in selected_template.columns]
            self.update_data_table()
            self._auto_populate_fbgs(df)
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
                lines = f.readlines()[:200]

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

            # 尝试通用格式检测：扫描找到第一个多列行
            delimiter = '\t'
            for line in lines:
                if ',' in line and '\t' not in line:
                    delimiter = ','
                    break

            header_line_idx = -1
            header_parts = None
            for i, line in enumerate(lines):
                parts = line.strip().split(delimiter)
                if len(parts) > 1:
                    header_line_idx = i
                    header_parts = parts
                    break

            if header_parts is None:
                return None

            is_data = self._is_data_row(header_parts)
            is_fiber = False

            if is_data:
                # 无表头：生成合成列名
                columns = []
                import re
                for i, h in enumerate(header_parts):
                    h = h.strip()
                    if not h:
                        continue
                    if i == 0 and re.match(r'^\d{2,4}[/-]', h):
                        columns.append({'name': '时间', 'comment': '时间戳', 'data_type': 'time'})
                    else:
                        columns.append({'name': f'列{i+1}', 'comment': '', 'data_type': 'numeric'})
                # 检查是否可能是光纤文件（通过后续行判断）
                for line in lines[header_line_idx+1:]:
                    parts2 = line.strip().split(delimiter)
                    if len(parts2) > 1:
                        try:
                            float(parts2[1].strip())
                            is_fiber = len(parts2) > 2  # 多列数值 → 可能是光纤
                        except ValueError:
                            pass
                        break
                if is_fiber:
                    # 重新生成光纤列名
                    for c in columns:
                        if c['name'] != '时间' and c['name'].startswith('列'):
                            idx = int(c['name'][1:]) - 1
                            c['name'] = f'波长{idx}'
                            c['comment'] = f'FBG 波长{idx}'
                            c['data_type'] = 'wavelength'
                skip_rows = header_line_idx
            else:
                # 有表头：使用文件列名
                columns = []
                for col_name in header_parts:
                    col_name = col_name.strip()
                    if not col_name:
                        continue
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
                skip_rows = header_line_idx + 1

            has_wavelength = any(
                '波长' in c['name'] or 'wavelength' in c['name'].lower()
                or c['name'].upper().startswith('FBG')
                or (c['name'].upper().startswith('W') and c['name'][1:].isdigit())
                for c in columns
            )
            if has_wavelength:
                is_fiber = True
            template_type = 'fiber_custom' if is_fiber else 'csv'

            return DataTemplate(
                f'{template_type}_custom',
                f'自定义{"光纤" if is_fiber else "数据"}模板',
                template_type,
                delimiter,
                skip_rows,
                columns
            )
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

    @staticmethod
    def _is_data_row(parts):
        """判断一行数据是真实数据还是表头

        返回: True=数据行, False=表头行
        """
        import re
        data_count = 0
        header_count = 0
        for p in parts:
            p = p.strip()
            if not p:
                continue
            try:
                float(p)
                data_count += 1
                continue
            except ValueError:
                pass
            if re.match(r'^\d{2,4}[/-]\d{1,2}[/-]\d{1,2}', p):
                data_count += 1
                continue
            if re.match(r'^[A-Za-z_]', p) or '波长' in p or '时间' in p or '温度' in p or '应变' in p:
                header_count += 1
                continue
            data_count += 1
        total = data_count + header_count
        if total > 0:
            return data_count / total >= 0.5
        return True

    def _load_file_header_lines(self, file_path, skip_rows):
        """读取文件的格式头行（数据行之前的所有行，包括列头行）"""
        try:
            for enc in ('utf-8', 'gbk', 'latin-1'):
                try:
                    with open(file_path, 'r', encoding=enc) as f:
                        lines = f.readlines()
                    break
                except UnicodeDecodeError:
                    continue

            # 查找数据起始行：包含Timestamp的行之后的行
            data_start = skip_rows
            for i, line in enumerate(lines):
                if 'Timestamp' in line and ('# CH' in line or 'CH' in line):
                    data_start = i + 1  # 数据从header下一行开始
                    break

            # 格式头 = 数据起始行之前的所有行
            self.file_header_lines = lines[:data_start] if data_start > 0 else []
        except Exception:
            self.file_header_lines = []

    def clear_data(self):
        self.current_data = None
        self.current_columns = []
        self.sampled_data = None
        self.file_header_lines = []
        self.sampled_file_path = None
        self.current_template = None
        self.data_table.setRowCount(0)
        self.data_table.setColumnCount(0)
        self.status_bar.showMessage('数据已清除')

    def _find_first_timestamp_row(self, df):
        """找到第一个包含有效时间戳的数据行索引"""
        time_col = None
        for col in df.columns:
            col_lower = str(col).lower()
            if '时间' in col_lower or 'time' in col_lower or 'timestamp' in col_lower:
                time_col = col
                break
        if time_col is None:
            return 0  # 没有时间列，从第一行开始

        for idx in range(len(df)):
            val = df.iloc[idx][time_col]
            if pd.notna(val) and str(val).strip() != '':
                try:
                    pd.to_numeric(val)
                    return idx
                except (ValueError, TypeError):
                    # 可能是时间字符串，也算有效
                    return idx
        return 0

    def sample_data(self):
        """按等间隔抽取数据"""
        if self.current_data is None:
            QMessageBox.warning(self, '警告', '没有数据可抽取')
            return

        # 获取抽样间隔
        interval, ok = QInputDialog.getInt(
            self, '抽取数据', '输入抽样间隔 N (每N行抽取1行):',
            10, 2, 1000000, 1
        )
        if not ok:
            return

        df = self.current_data

        # 找到数据起始行（第一个有效时间戳行）
        data_start = self._find_first_timestamp_row(df)

        # 从数据起始行开始等间隔抽取
        data_df = df.iloc[data_start:]
        indices = list(range(0, len(data_df), interval))
        sampled = data_df.iloc[indices].reset_index(drop=True)

        self.sampled_data = sampled

        QMessageBox.information(
            self, '抽取完成',
            f'原始数据: {len(df)} 行 (数据从第{data_start + 1}行开始)\n'
            f'抽取间隔: 每{interval}行取1行\n'
            f'抽取结果: {len(sampled)} 行\n'
            f'格式头: {data_start} 行已保留'
        )

    def save_sampled_data(self):
        """保存数据（优先保存抽样数据），保持原始文件格式"""
        data_to_save = self.sampled_data if self.sampled_data is not None else self.current_data
        if data_to_save is None:
            QMessageBox.warning(self, '警告', '没有数据可保存')
            return

        template = self.current_template
        fmt = template.file_format if template and hasattr(template, 'file_format') else 'csv'
        delim = template.delimiter if template and hasattr(template, 'delimiter') else ','

        # 文件过滤器
        if fmt in ('enlight', 'txt', 'fiber_custom'):
            file_filter = 'Text Files (*.txt);;CSV Files (*.csv);;Excel Files (*.xlsx)'
        elif fmt == 'csv':
            file_filter = 'CSV Files (*.csv);;Text Files (*.txt);;Excel Files (*.xlsx)'
        else:
            file_filter = 'Excel Files (*.xlsx);;CSV Files (*.csv)'

        file_path, _ = QFileDialog.getSaveFileName(self, '保存数据', '', file_filter)
        if not file_path:
            return

        try:
            # 确定输出格式
            if file_path.endswith('.xlsx'):
                data_to_save.to_excel(file_path, index=False)
            else:
                with open(file_path, 'w', encoding='utf-8') as f:
                    # 写入格式头（如果有）
                    if self.file_header_lines:
                        for line in self.file_header_lines:
                            f.write(line)
                            if not line.endswith('\n'):
                                f.write('\n')
                    # 写入数据（格式头已包含列头，不再重复写入header）
                    write_header = len(self.file_header_lines) == 0
                    data_to_save.to_csv(f, sep=delim, index=False, header=write_header)

            self.sampled_data = None
            self.file_header_lines = []
            QMessageBox.information(self, '成功', f'数据已保存到:\n{file_path}')
        except Exception as e:
            QMessageBox.critical(self, '错误', f'保存失败: {str(e)}')

    def save_template_to_file(self):
        """将当前文件的解析参数保存为可复用模板"""
        if self.current_data is None:
            QMessageBox.warning(self, '警告', '请先打开文件')
            return

        # 输入模板名称
        name, ok = QInputDialog.getText(self, '保存模板', '输入模板名称:')
        if not ok or not name.strip():
            return

        name = name.strip()

        template = self.current_template
        if template is None:
            QMessageBox.warning(self, '警告', '当前文件没有关联模板')
            return

        # 从原始文件读取真实格式信息
        file_format = template.file_format if hasattr(template, 'file_format') else 'txt'
        delimiter = template.delimiter if hasattr(template, 'delimiter') else '\t'
        skip_rows = template.skip_rows if hasattr(template, 'skip_rows') else 0
        raw_headers = None

        # 尝试从原始文件头行获取真实的列名
        if self.sampled_file_path and os.path.exists(self.sampled_file_path):
            try:
                with open(self.sampled_file_path, 'r', encoding='utf-8') as f:
                    raw_lines = f.readlines()

                # 检测ENLIGHT/光纤格式
                is_fiber = False
                header_line_idx = -1
                for i, line in enumerate(raw_lines):
                    if 'Timestamp' in line and ('# CH' in line or 'CH' in line):
                        file_format = 'enlight'
                        is_fiber = True
                        header_line_idx = i
                        skip_rows = i + 1
                        delimiter = '\t'
                        break

                # 找到含有列名的行（header行）
                if header_line_idx < 0:
                    for i, line in enumerate(raw_lines):
                        parts = line.strip().split(delimiter)
                        if len(parts) > 1:
                            # 智能判断该行是表头还是数据
                            is_data_row = self._is_data_row(parts)
                            header_line_idx = i
                            if is_data_row:
                                # 无表头文件：此行为数据，不跳过
                                skip_rows = i
                            else:
                                # 有表头文件：此行为表头，跳过
                                skip_rows = i + 1
                            # 检查是否为光纤传感文件
                            line_lower = line.lower()
                            if 'timestamp' in line_lower or any(
                                'fbg' in p.lower() or '波长' in p or 'wavelength' in p.lower()
                                or (p.upper().startswith('W') and p[1:].isdigit())
                                for p in parts
                            ):
                                file_format = 'fiber_custom'
                                is_fiber = True
                            break

                # 从header行获取列名
                if header_line_idx >= 0:
                    raw_headers = raw_lines[header_line_idx].strip().split(delimiter)
            except Exception:
                pass

        # 构建列定义
        columns = []
        is_data = self._is_data_row(raw_headers) if raw_headers else True

        if raw_headers and len(raw_headers) > 0 and not is_data:
            # 真实表头：使用文件中的列名
            w_index = 1
            for h in raw_headers:
                h = h.strip()
                if not h:
                    continue
                h_lower = h.lower()
                if 'timestamp' in h_lower or '时间' in h:
                    columns.append({'name': h, 'comment': '时间戳', 'data_type': 'time'})
                elif 'wavelength' in h_lower or '波长' in h_lower or 'fbg' in h_lower or (h_lower.startswith('w') and h[1:].isdigit()):
                    columns.append({'name': h, 'comment': f'FBG {h}', 'data_type': 'wavelength'})
                elif 'ch' in h_lower and ('计数' in h or 'count' in h_lower):
                    columns.append({'name': h, 'comment': h, 'data_type': 'numeric'})
                elif '应变' in h or 'strain' in h_lower:
                    columns.append({'name': h, 'comment': h, 'data_type': 'strain'})
                elif '温度' in h or 'temperature' in h_lower:
                    columns.append({'name': h, 'comment': h, 'data_type': 'temperature'})
                else:
                    columns.append({'name': h, 'comment': h, 'data_type': 'numeric'})
        elif raw_headers and len(raw_headers) > 0 and is_data:
            # 无表头文件：生成合成列名
            import re
            for i, h in enumerate(raw_headers):
                h = h.strip()
                if not h:
                    continue
                if i == 0 and re.match(r'^\d{2,4}[/-]', h):
                    columns.append({'name': '时间', 'comment': '时间戳', 'data_type': 'time'})
                elif i == 0:
                    columns.append({'name': f'列{i+1}', 'comment': '', 'data_type': 'numeric'})
                else:
                    if is_fiber:
                        columns.append({'name': f'波长{i}', 'comment': f'FBG 波长{i}', 'data_type': 'wavelength'})
                    else:
                        columns.append({'name': f'列{i+1}', 'comment': '', 'data_type': 'numeric'})
        elif hasattr(template, 'columns') and template.columns:
            columns = template.columns
        else:
            for col in self.current_data.columns:
                col_str = str(col)
                col_lower = col_str.lower()
                if '波长' in col_str or 'wavelength' in col_lower:
                    columns.append({'name': col_str, 'comment': col_str, 'data_type': 'wavelength'})
                elif '时间' in col_str or 'timestamp' in col_lower:
                    columns.append({'name': col_str, 'comment': col_str, 'data_type': 'time'})
                elif '应变' in col_str or 'strain' in col_lower:
                    columns.append({'name': col_str, 'comment': col_str, 'data_type': 'strain'})
                elif '温度' in col_str or 'temperature' in col_lower:
                    columns.append({'name': col_str, 'comment': col_str, 'data_type': 'temperature'})
                else:
                    columns.append({'name': col_str, 'comment': col_str, 'data_type': 'numeric'})

        # 如果文件格式不是光纤类型但包含FBG/波长列，自动升级为fiber_custom
        if file_format not in ('enlight', 'fiber_custom'):
            has_fbg = any(
                'fbg' in c.get('name', '').lower() or
                '波长' in c.get('name', '') or
                c.get('data_type') == 'wavelength'
                for c in columns
            )
            if has_fbg:
                file_format = 'fiber_custom'

        new_template = DataTemplate(
            f'custom_{name}',
            name,
            file_format,
            delimiter,
            skip_rows,
            columns
        )

        # 保存到 DEFAULT_TEMPLATES 和持久化文件
        self._persist_custom_template(new_template)
        DEFAULT_TEMPLATES.append(new_template)

        QMessageBox.information(self, '成功', f'模板 "{name}" 已保存，下次打开文件时可在模板列表中选择')

    def _persist_custom_template(self, template):
        """持久化自定义模板到JSON文件"""
        import json
        templates_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'templates')
        os.makedirs(templates_dir, exist_ok=True)
        templates_file = os.path.join(templates_dir, 'custom_templates.json')

        existing = []
        if os.path.exists(templates_file):
            try:
                with open(templates_file, 'r', encoding='utf-8') as f:
                    existing = json.load(f)
            except Exception:
                pass

        existing.append(template.to_dict())

        with open(templates_file, 'w', encoding='utf-8') as f:
            json.dump(existing, f, ensure_ascii=False, indent=2)

    @staticmethod
    def load_custom_templates():
        """启动时加载持久化的自定义模板"""
        import json
        templates_file = os.path.join(
            os.path.dirname(os.path.abspath(__file__)), 'templates', 'custom_templates.json'
        )
        if not os.path.exists(templates_file):
            return

        try:
            with open(templates_file, 'r', encoding='utf-8') as f:
                data = json.load(f)
            for item in data:
                template = DataTemplate(
                    item.get('id', ''),
                    item.get('name', ''),
                    item.get('file_format', 'txt'),
                    item.get('delimiter', '\t'),
                    item.get('skip_rows', 0),
                    item.get('columns', [])
                )
                # 避免重复添加
                if not any(t.id == template.id for t in DEFAULT_TEMPLATES):
                    DEFAULT_TEMPLATES.append(template)
        except Exception:
            pass

    def update_data_table(self):
        if self.current_data is None:
            return

        # 确保data_table存在
        if not hasattr(self, 'data_table') or self.data_table is None:
            # 创建data_table
            self.data_table = QTableWidget()
            self.data_table.setAlternatingRowColors(True)
            # 添加到数据标签页
            if hasattr(self, 'data_tab') and self.data_tab.layout():
                self.data_tab.layout().addWidget(self.data_table)

        df = self.current_data
        self.data_table.setRowCount(len(df))
        self.data_table.setColumnCount(len(df.columns))
        self.data_table.setHorizontalHeaderLabels([str(c) for c in df.columns])

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

    def _on_ollama_status_result(self, is_available):
        """Ollama状态检测结果 - 已弃用，使用新的AI模型区域"""
        pass  # 不再使用，保留接口避免错误

    def _on_ollama_models_list(self, models):
        """收到模型列表（不再使用）"""
        pass

    def select_local_model(self):
        """选择本地GGUF模型文件（不再使用）"""
        pass

    def unload_local_model(self):
        """卸载当前模型（不再使用）"""
        pass

    def check_ollama_status(self):
        """检查本地模型状态（不再使用）"""
        pass

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
            elif self.current_data is not None:
                numeric_cols = self.current_data.select_dtypes(include=[np.number])
                if not numeric_cols.empty:
                    arr = numeric_cols.values.flatten()
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

            # 构建完整诊断上下文
            data_context = self._build_ai_data_context()

            prompt = f"""You are a structural health monitoring expert. Analyze the following data context.

{data_context}

Based on the above data context, provide:
1. Overall data status assessment (normal/abnormal/needs attention)
2. Per-channel/sensor trend analysis
3. Anomaly or outlier detection
4. Physical interpretation of sensor results (strain, temperature, displacement)
5. If decoupling sensors are configured, assess the credibility of strain/temperature separation

Keep response concise under 300 words."""

            from py.online_llm_thread import OnlineLlamaGenerateThread
            model_name = self.ai_model_combo.currentText()
            if not model_name or model_name not in self.ai_models_config:
                self.ai_diagnosis_result.setText('请先在模型配置中选择一个模型')
                self.ai_diagnosis_btn.setEnabled(True)
                return
            config = self.ai_models_config[model_name]
            self.ollama_diag_thread = OnlineLlamaGenerateThread(
                prompt=prompt,
                api_key=config.get('api_key', ''),
                base_url=config.get('base_url', ''),
                model_name=config.get('model_name', ''),
                system_prompt=config.get('system_prompt', '')
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
            # 构建完整数据上下文
            data_context = self._build_ai_data_context()
            full_prompt = f"""你是一个结构健康监测专家。以下是你正在分析的数据的完整上下文。

{data_context}

用户提问：{user_prompt}

请基于以上数据上下文回答用户问题。如有不清楚的地方，请明确提问。"""

            self.custom_query_result.setText('正在处理，请稍候...')
            self.custom_query_stream = ""

            model_name = self.ai_model_combo.currentText()
            if not model_name or model_name not in self.ai_models_config:
                self.custom_query_result.setText('请先在模型配置中选择一个模型')
                return
            config = self.ai_models_config[model_name]
            from py.online_llm_thread import OnlineLlamaGenerateThread
            self.custom_query_thread = OnlineLlamaGenerateThread(
                prompt=full_prompt,
                api_key=config.get('api_key', ''),
                base_url=config.get('base_url', ''),
                model_name=config.get('model_name', ''),
                system_prompt=config.get('system_prompt', '')
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

    # ========== Word报告页面 - 对话框和辅助方法 ==========

    def on_edit_word_report_title(self):
        """编辑报告标题"""
        dialog = QDialog(self)
        dialog.setWindowTitle('编辑报告标题')
        dialog.resize(500, 150)

        layout = QVBoxLayout()
        layout.addWidget(QLabel('请输入报告标题:'))

        title_input = QLineEdit()
        title_input.setText(self.word_report_title.text() if self.word_report_title.text() != '未设置标题' else '')
        layout.addWidget(title_input)

        btn_row = QHBoxLayout()
        ok_btn = QPushButton('保存')
        ok_btn.setStyleSheet('background-color: #52c41a; color: white;')
        ok_btn.clicked.connect(lambda: self._save_word_title(title_input.text(), dialog))
        btn_row.addWidget(ok_btn)

        cancel_btn = QPushButton('取消')
        cancel_btn.clicked.connect(dialog.close)
        btn_row.addWidget(cancel_btn)
        btn_row.addStretch()
        layout.addLayout(btn_row)

        dialog.setLayout(layout)
        dialog.exec()

    def _save_word_title(self, title: str, dialog: QDialog):
        if title.strip():
            self.word_report_title.setText(title.strip())
        dialog.close()

    def on_word_requirement_config(self):
        """Word需求配置对话框"""
        dialog = QDialog(self)
        dialog.setWindowTitle('需求配置 - Word报告')
        dialog.resize(700, 500)

        layout = QVBoxLayout()

        btn_row = QHBoxLayout()
        add_btn = QPushButton('添加需求文件')
        add_btn.setStyleSheet('background-color: #52c41a; color: white;')
        add_btn.clicked.connect(lambda: self._add_word_requirement_file(dialog))
        btn_row.addWidget(add_btn)

        delete_btn = QPushButton('删除需求文件')
        delete_btn.setStyleSheet('background-color: #ff4d4f; color: white;')
        delete_btn.clicked.connect(lambda: self._delete_word_requirement_file(dialog))
        btn_row.addWidget(delete_btn)

        edit_btn = QPushButton('编辑需求文件')
        edit_btn.setStyleSheet('background-color: #1890ff; color: white;')
        edit_btn.clicked.connect(lambda: self._edit_word_requirement_file(dialog))
        btn_row.addWidget(edit_btn)

        layout.addLayout(btn_row)

        self.word_req_list = QListWidget()
        layout.addWidget(self.word_req_list)

        if hasattr(self, 'word_requirement_files'):
            for f in self.word_requirement_files:
                self.word_req_list.addItem(f)

        close_btn = QPushButton('关闭')
        close_btn.clicked.connect(dialog.close)
        layout.addWidget(close_btn)

        dialog.setLayout(layout)
        dialog.exec()

    def _add_word_requirement_file(self, dialog: QDialog):
        settings = QSettings('DataProcessor', 'Pro')
        last_dir = settings.value('word_req_last_dir', '')

        file_paths, _ = QFileDialog.getOpenFileNames(
            dialog, '选择需求文件', last_dir,
            'Text Files (*.txt *.md);;All Files (*)'
        )

        if file_paths:
            settings.setValue('word_req_last_dir', os.path.dirname(file_paths[0]))

            if not hasattr(self, 'word_requirement_files'):
                self.word_requirement_files = []

            for path in file_paths:
                if path not in self.word_requirement_files:
                    self.word_requirement_files.append(path)
                    self.word_req_list.addItem(path)

    def _delete_word_requirement_file(self, dialog: QDialog):
        current_item = self.word_req_list.currentItem()
        if current_item and hasattr(self, 'word_requirement_files'):
            path = current_item.text()
            if path in self.word_requirement_files:
                self.word_requirement_files.remove(path)
                self.word_req_list.takeItem(self.word_req_list.row(current_item))

    def _edit_word_requirement_file(self, dialog: QDialog):
        current_item = self.word_req_list.currentItem()
        if current_item:
            file_path = current_item.text()
            if os.path.exists(file_path):
                os.startfile(file_path)
            else:
                QMessageBox.warning(dialog, '警告', '文件不存在')

    def on_word_template_operation(self):
        """Word模板操作对话框"""
        dialog = QDialog(self)
        dialog.setWindowTitle('模板操作 - Word报告')
        dialog.resize(700, 500)

        layout = QVBoxLayout()

        btn_row = QHBoxLayout()
        add_btn = QPushButton('添加模板')
        add_btn.setStyleSheet('background-color: #52c41a; color: white;')
        add_btn.clicked.connect(lambda: self._add_word_template(dialog))
        btn_row.addWidget(add_btn)

        delete_btn = QPushButton('删除模板')
        delete_btn.setStyleSheet('background-color: #ff4d4f; color: white;')
        delete_btn.clicked.connect(lambda: self._delete_word_template(dialog))
        btn_row.addWidget(delete_btn)

        edit_btn = QPushButton('编辑模板')
        edit_btn.setStyleSheet('background-color: #1890ff; color: white;')
        edit_btn.clicked.connect(lambda: self._edit_word_template(dialog))
        btn_row.addWidget(edit_btn)

        layout.addLayout(btn_row)

        self.word_template_list = QListWidget()
        layout.addWidget(self.word_template_list)

        if hasattr(self, 'word_templates'):
            for t in self.word_templates:
                self.word_template_list.addItem(t)

        close_btn = QPushButton('关闭')
        close_btn.clicked.connect(dialog.close)
        layout.addWidget(close_btn)

        dialog.setLayout(layout)
        dialog.exec()

    def _add_word_template(self, dialog: QDialog):
        settings = QSettings('DataProcessor', 'Pro')
        last_dir = settings.value('word_template_last_dir', '')

        file_path, _ = QFileDialog.getOpenFileName(
            dialog, '选择Word模板', last_dir,
            '文档文件 (*.txt *.csv *.xlsx *.docx *.doc);;所有文件 (*.*)'
        )

        if file_path:
            settings.setValue('word_template_last_dir', os.path.dirname(file_path))

            if not hasattr(self, 'word_templates'):
                self.word_templates = []

            if file_path not in self.word_templates:
                self.word_templates.append(file_path)
                self.word_template_list.addItem(file_path)

    def _delete_word_template(self, dialog: QDialog):
        current_item = self.word_template_list.currentItem()
        if current_item and hasattr(self, 'word_templates'):
            path = current_item.text()
            if path in self.word_templates:
                self.word_templates.remove(path)
                self.word_template_list.takeItem(self.word_template_list.row(current_item))

    def _edit_word_template(self, dialog: QDialog):
        current_item = self.word_template_list.currentItem()
        if current_item:
            file_path = current_item.text()
            if os.path.exists(file_path):
                os.startfile(file_path)
            else:
                QMessageBox.warning(dialog, '警告', '文件不存在')

    def on_word_project_files(self):
        """Word项目文件对话框"""
        dialog = QDialog(self)
        dialog.setWindowTitle('项目文件 - Word报告')
        dialog.resize(700, 500)

        layout = QVBoxLayout()

        btn_row = QHBoxLayout()
        add_btn = QPushButton('添加项目资料')
        add_btn.setStyleSheet('background-color: #52c41a; color: white;')
        add_btn.clicked.connect(lambda: self._add_word_project_file(dialog))
        btn_row.addWidget(add_btn)

        delete_btn = QPushButton('删除项目资料')
        delete_btn.setStyleSheet('background-color: #ff4d4f; color: white;')
        delete_btn.clicked.connect(lambda: self._delete_word_project_file(dialog))
        btn_row.addWidget(delete_btn)

        edit_btn = QPushButton('编辑项目资料')
        edit_btn.setStyleSheet('background-color: #1890ff; color: white;')
        edit_btn.clicked.connect(lambda: self._edit_word_project_file(dialog))
        btn_row.addWidget(edit_btn)

        layout.addLayout(btn_row)

        self.word_project_list = QListWidget()
        layout.addWidget(self.word_project_list)

        if hasattr(self, 'word_project_files'):
            for f in self.word_project_files:
                self.word_project_list.addItem(f)

        close_btn = QPushButton('关闭')
        close_btn.clicked.connect(dialog.close)
        layout.addWidget(close_btn)

        dialog.setLayout(layout)
        dialog.exec()

    def _add_word_project_file(self, dialog: QDialog):
        settings = QSettings('DataProcessor', 'Pro')
        last_dir = settings.value('word_project_last_dir', '')

        file_paths, _ = QFileDialog.getOpenFileNames(
            dialog, '选择项目资料', last_dir,
            'Documents (*.doc *.docx *.pdf);;Excel (*.xlsx *.xls);;Images (*.png *.jpg *.jpeg);;Text (*.txt *.csv);;All Files (*)'
        )

        if file_paths:
            settings.setValue('word_project_last_dir', os.path.dirname(file_paths[0]))

            if not hasattr(self, 'word_project_files'):
                self.word_project_files = []

            for path in file_paths:
                if path not in self.word_project_files:
                    self.word_project_files.append(path)
                    self.word_project_list.addItem(path)

    def _delete_word_project_file(self, dialog: QDialog):
        current_item = self.word_project_list.currentItem()
        if current_item and hasattr(self, 'word_project_files'):
            path = current_item.text()
            if path in self.word_project_files:
                self.word_project_files.remove(path)
                self.word_project_list.takeItem(self.word_project_list.row(current_item))

    def _edit_word_project_file(self, dialog: QDialog):
        current_item = self.word_project_list.currentItem()
        if current_item:
            file_path = current_item.text()
            if os.path.exists(file_path):
                os.startfile(file_path)
            else:
                QMessageBox.warning(dialog, '警告', '文件不存在')

    def on_generate_word_report(self):
        """生成Word报告"""
        if not hasattr(self, 'word_requirement_files') or not self.word_requirement_files:
            QMessageBox.warning(self, '警告', '请先配置需求文件')
            return

        if not hasattr(self, 'word_templates') or not self.word_templates:
            QMessageBox.warning(self, '警告', '请先配置模板文件')
            return

        if not hasattr(self, 'word_project_files') or not self.word_project_files:
            QMessageBox.warning(self, '警告', '请先配置项目资料')
            return

        self.word_gen_status.setText('状态: 正在生成Word报告...')
        self.word_gen_status.setStyleSheet('color: #faad14; font-weight: bold; padding: 5px;')
        QApplication.processEvents()

        try:
            req_contents = []
            for req_file in self.word_requirement_files:
                try:
                    with open(req_file, 'r', encoding='utf-8') as f:
                        req_contents.append(f.read())
                except Exception as e:
                    print(f"读取需求文件失败 {req_file}: {e}")

            template_path = self.word_templates[0]

            project_contents = []
            for proj_file in self.word_project_files:
                ext = os.path.splitext(proj_file)[1].lower()
                try:
                    if ext in ['.txt', '.csv']:
                        with open(proj_file, 'r', encoding='utf-8') as f:
                            project_contents.append(f.read())
                    elif ext in ['.doc', '.docx']:
                        from docx import Document
                        doc = Document(proj_file)
                        project_contents.append('\n'.join([p.text for p in doc.paragraphs]))
                    elif ext in ['.xlsx', '.xls']:
                        import openpyxl
                        wb = openpyxl.load_workbook(proj_file, data_only=True)
                        content = []
                        for sheet in wb.sheetnames:
                            ws = wb[sheet]
                            for row in ws.iter_rows(values_only=True):
                                row_content = [str(c) if c else '' for c in row]
                                content.append(' | '.join(row_content))
                        project_contents.append('\n'.join(content))
                except Exception as e:
                    print(f"读取项目资料失败 {proj_file}: {e}")

            title = self.word_report_title.text() if self.word_report_title.text() != '未设置标题' else '数据分析报告'
            prompt = f"""请根据以下信息生成Word格式的数据分析报告。

报告标题: {title}

需求配置:
{chr(10).join(req_contents)}

模板文件: {template_path}

项目资料:
{chr(10).join(project_contents)}

请生成完整的Word报告内容。"""

            if hasattr(self, 'ollama_client') and self.ollama_client and self.ollama_client.is_available():
                result = self.ollama_client.generate(prompt)
                if result:
                    self.word_gen_status.setText('状态: 已生成Word报告')
                    self.word_gen_status.setStyleSheet('color: #52c41a; font-weight: bold; padding: 5px;')
                    self.word_ai_result.setPlainText('AI生成结果:\n' + result)
                else:
                    self.word_gen_status.setText('状态: Word报告生成失败')
                    self.word_gen_status.setStyleSheet('color: #ff4d4f; font-weight: bold; padding: 5px;')
            else:
                self.word_gen_status.setText('状态: AI不可用，请配置Ollama服务')
                self.word_gen_status.setStyleSheet('color: #faad14; font-weight: bold; padding: 5px;')

        except Exception as e:
            self.word_gen_status.setText(f'状态: Word报告生成失败 - {str(e)}')
            self.word_gen_status.setStyleSheet('color: #ff4d4f; font-weight: bold; padding: 5px;')
            import traceback
            traceback.print_exc()

    def on_save_word_report(self):
        """保存Word报告"""
        settings = QSettings('DataProcessor', 'Pro')
        last_dir = settings.value('word_report_save_dir', '')

        file_path, _ = QFileDialog.getSaveFileName(
            self, '保存Word报告', last_dir,
            'Word Documents (*.docx)'
        )

        if file_path:
            settings.setValue('word_report_save_dir', os.path.dirname(file_path))

            content = self.word_ai_result.toPlainText()
            if content and 'AI生成结果:' in content:
                content = content.split('AI生成结果:')[1]

            try:
                from docx import Document
                doc = Document()
                doc.add_heading(self.word_report_title.text() if self.word_report_title.text() != '未设置标题' else '数据分析报告', 0)
                doc.add_paragraph(content)
                doc.save(file_path)
                QMessageBox.information(self, '成功', f'报告已保存至: {file_path}')
            except Exception as e:
                QMessageBox.warning(self, '警告', f'保存失败: {str(e)}')

    def on_word_ai_send(self):
        """Word AI交互发送"""
        user_input = self.word_ai_chat_input.toPlainText().strip()
        if not user_input:
            return

        self.word_ai_result.append(f'用户: {user_input}')
        self.word_ai_chat_input.clear()

        if any(keyword in user_input for keyword in ['确认', '继续', '好的', '是', 'ok', 'yes']):
            self.word_ai_result.append('AI: 收到确认，继续生成报告...')
        else:
            self.word_ai_result.append('AI: 请提供更多信息以便继续生成报告。')

    # ========== PPT报告页面 - 对话框和辅助方法 ==========

    def on_edit_ppt_report_title(self):
        """编辑PPT报告标题"""
        dialog = QDialog(self)
        dialog.setWindowTitle('编辑报告标题')
        dialog.resize(500, 150)

        layout = QVBoxLayout()
        layout.addWidget(QLabel('请输入报告标题:'))

        title_input = QLineEdit()
        title_input.setText(self.ppt_report_title.text() if self.ppt_report_title.text() != '未设置标题' else '')
        layout.addWidget(title_input)

        btn_row = QHBoxLayout()
        ok_btn = QPushButton('保存')
        ok_btn.setStyleSheet('background-color: #52c41a; color: white;')
        ok_btn.clicked.connect(lambda: self._save_ppt_title(title_input.text(), dialog))
        btn_row.addWidget(ok_btn)

        cancel_btn = QPushButton('取消')
        cancel_btn.clicked.connect(dialog.close)
        btn_row.addWidget(cancel_btn)
        btn_row.addStretch()
        layout.addLayout(btn_row)

        dialog.setLayout(layout)
        dialog.exec()

    def _save_ppt_title(self, title: str, dialog: QDialog):
        if title.strip():
            self.ppt_report_title.setText(title.strip())
        dialog.close()

    def on_ppt_requirement_config(self):
        """PPT需求配置对话框"""
        dialog = QDialog(self)
        dialog.setWindowTitle('需求配置 - PPT报告')
        dialog.resize(700, 500)

        layout = QVBoxLayout()

        btn_row = QHBoxLayout()
        add_btn = QPushButton('添加需求文件')
        add_btn.setStyleSheet('background-color: #52c41a; color: white;')
        add_btn.clicked.connect(lambda: self._add_ppt_requirement_file(dialog))
        btn_row.addWidget(add_btn)

        delete_btn = QPushButton('删除需求文件')
        delete_btn.setStyleSheet('background-color: #ff4d4f; color: white;')
        delete_btn.clicked.connect(lambda: self._delete_ppt_requirement_file(dialog))
        btn_row.addWidget(delete_btn)

        edit_btn = QPushButton('编辑需求文件')
        edit_btn.setStyleSheet('background-color: #1890ff; color: white;')
        edit_btn.clicked.connect(lambda: self._edit_ppt_requirement_file(dialog))
        btn_row.addWidget(edit_btn)

        layout.addLayout(btn_row)

        self.ppt_req_list = QListWidget()
        layout.addWidget(self.ppt_req_list)

        if hasattr(self, 'ppt_requirement_files'):
            for f in self.ppt_requirement_files:
                self.ppt_req_list.addItem(f)

        close_btn = QPushButton('关闭')
        close_btn.clicked.connect(dialog.close)
        layout.addWidget(close_btn)

        dialog.setLayout(layout)
        dialog.exec()

    def _add_ppt_requirement_file(self, dialog: QDialog):
        settings = QSettings('DataProcessor', 'Pro')
        last_dir = settings.value('ppt_req_last_dir', '')

        file_paths, _ = QFileDialog.getOpenFileNames(
            dialog, '选择需求文件', last_dir,
            'Text Files (*.txt *.md);;All Files (*)'
        )

        if file_paths:
            settings.setValue('ppt_req_last_dir', os.path.dirname(file_paths[0]))

            if not hasattr(self, 'ppt_requirement_files'):
                self.ppt_requirement_files = []

            for path in file_paths:
                if path not in self.ppt_requirement_files:
                    self.ppt_requirement_files.append(path)
                    self.ppt_req_list.addItem(path)

    def _delete_ppt_requirement_file(self, dialog: QDialog):
        current_item = self.ppt_req_list.currentItem()
        if current_item and hasattr(self, 'ppt_requirement_files'):
            path = current_item.text()
            if path in self.ppt_requirement_files:
                self.ppt_requirement_files.remove(path)
                self.ppt_req_list.takeItem(self.ppt_req_list.row(current_item))

    def _edit_ppt_requirement_file(self, dialog: QDialog):
        current_item = self.ppt_req_list.currentItem()
        if current_item:
            file_path = current_item.text()
            if os.path.exists(file_path):
                os.startfile(file_path)
            else:
                QMessageBox.warning(dialog, '警告', '文件不存在')

    def on_ppt_template_operation(self):
        """PPT模板操作对话框"""
        dialog = QDialog(self)
        dialog.setWindowTitle('模板操作 - PPT报告')
        dialog.resize(700, 500)

        layout = QVBoxLayout()

        btn_row = QHBoxLayout()
        add_btn = QPushButton('添加模板')
        add_btn.setStyleSheet('background-color: #52c41a; color: white;')
        add_btn.clicked.connect(lambda: self._add_ppt_template(dialog))
        btn_row.addWidget(add_btn)

        delete_btn = QPushButton('删除模板')
        delete_btn.setStyleSheet('background-color: #ff4d4f; color: white;')
        delete_btn.clicked.connect(lambda: self._delete_ppt_template(dialog))
        btn_row.addWidget(delete_btn)

        edit_btn = QPushButton('编辑模板')
        edit_btn.setStyleSheet('background-color: #1890ff; color: white;')
        edit_btn.clicked.connect(lambda: self._edit_ppt_template(dialog))
        btn_row.addWidget(edit_btn)

        layout.addLayout(btn_row)

        self.ppt_template_list = QListWidget()
        layout.addWidget(self.ppt_template_list)

        if hasattr(self, 'ppt_templates'):
            for t in self.ppt_templates:
                self.ppt_template_list.addItem(t)

        close_btn = QPushButton('关闭')
        close_btn.clicked.connect(dialog.close)
        layout.addWidget(close_btn)

        dialog.setLayout(layout)
        dialog.exec()

    def _add_ppt_template(self, dialog: QDialog):
        settings = QSettings('DataProcessor', 'Pro')
        last_dir = settings.value('ppt_template_last_dir', '')

        file_path, _ = QFileDialog.getOpenFileName(
            dialog, '选择PPT模板', last_dir,
            '文档文件 (*.txt *.csv *.xlsx *.pptx);;所有文件 (*.*)'
        )

        if file_path:
            settings.setValue('ppt_template_last_dir', os.path.dirname(file_path))

            if not hasattr(self, 'ppt_templates'):
                self.ppt_templates = []

            if file_path not in self.ppt_templates:
                self.ppt_templates.append(file_path)
                self.ppt_template_list.addItem(file_path)

    def _delete_ppt_template(self, dialog: QDialog):
        current_item = self.ppt_template_list.currentItem()
        if current_item and hasattr(self, 'ppt_templates'):
            path = current_item.text()
            if path in self.ppt_templates:
                self.ppt_templates.remove(path)
                self.ppt_template_list.takeItem(self.ppt_template_list.row(current_item))

    def _edit_ppt_template(self, dialog: QDialog):
        current_item = self.ppt_template_list.currentItem()
        if current_item:
            file_path = current_item.text()
            if os.path.exists(file_path):
                os.startfile(file_path)
            else:
                QMessageBox.warning(dialog, '警告', '文件不存在')

    def on_ppt_project_files(self):
        """PPT项目文件对话框"""
        dialog = QDialog(self)
        dialog.setWindowTitle('项目文件 - PPT报告')
        dialog.resize(700, 500)

        layout = QVBoxLayout()

        btn_row = QHBoxLayout()
        add_btn = QPushButton('添加项目资料')
        add_btn.setStyleSheet('background-color: #52c41a; color: white;')
        add_btn.clicked.connect(lambda: self._add_ppt_project_file(dialog))
        btn_row.addWidget(add_btn)

        delete_btn = QPushButton('删除项目资料')
        delete_btn.setStyleSheet('background-color: #ff4d4f; color: white;')
        delete_btn.clicked.connect(lambda: self._delete_ppt_project_file(dialog))
        btn_row.addWidget(delete_btn)

        edit_btn = QPushButton('编辑项目资料')
        edit_btn.setStyleSheet('background-color: #1890ff; color: white;')
        edit_btn.clicked.connect(lambda: self._edit_ppt_project_file(dialog))
        btn_row.addWidget(edit_btn)

        layout.addLayout(btn_row)

        self.ppt_project_list = QListWidget()
        layout.addWidget(self.ppt_project_list)

        if hasattr(self, 'ppt_project_files'):
            for f in self.ppt_project_files:
                self.ppt_project_list.addItem(f)

        close_btn = QPushButton('关闭')
        close_btn.clicked.connect(dialog.close)
        layout.addWidget(close_btn)

        dialog.setLayout(layout)
        dialog.exec()

    def _add_ppt_project_file(self, dialog: QDialog):
        settings = QSettings('DataProcessor', 'Pro')
        last_dir = settings.value('ppt_project_last_dir', '')

        file_paths, _ = QFileDialog.getOpenFileNames(
            dialog, '选择项目资料', last_dir,
            'Documents (*.doc *.docx *.pdf);;Excel (*.xlsx *.xls);;Images (*.png *.jpg *.jpeg);;Text (*.txt *.csv);;All Files (*)'
        )

        if file_paths:
            settings.setValue('ppt_project_last_dir', os.path.dirname(file_paths[0]))

            if not hasattr(self, 'ppt_project_files'):
                self.ppt_project_files = []

            for path in file_paths:
                if path not in self.ppt_project_files:
                    self.ppt_project_files.append(path)
                    self.ppt_project_list.addItem(path)

    def _delete_ppt_project_file(self, dialog: QDialog):
        current_item = self.ppt_project_list.currentItem()
        if current_item and hasattr(self, 'ppt_project_files'):
            path = current_item.text()
            if path in self.ppt_project_files:
                self.ppt_project_files.remove(path)
                self.ppt_project_list.takeItem(self.ppt_project_list.row(current_item))

    def _edit_ppt_project_file(self, dialog: QDialog):
        current_item = self.ppt_project_list.currentItem()
        if current_item:
            file_path = current_item.text()
            if os.path.exists(file_path):
                os.startfile(file_path)
            else:
                QMessageBox.warning(dialog, '警告', '文件不存在')

    def on_generate_ppt_report(self):
        """生成PPT报告"""
        if not hasattr(self, 'ppt_requirement_files') or not self.ppt_requirement_files:
            QMessageBox.warning(self, '警告', '请先配置需求文件')
            return

        if not hasattr(self, 'ppt_templates') or not self.ppt_templates:
            QMessageBox.warning(self, '警告', '请先配置模板文件')
            return

        if not hasattr(self, 'ppt_project_files') or not self.ppt_project_files:
            QMessageBox.warning(self, '警告', '请先配置项目资料')
            return

        self.ppt_gen_status.setText('状态: 正在生成PPT报告...')
        self.ppt_gen_status.setStyleSheet('color: #faad14; font-weight: bold; padding: 5px;')
        QApplication.processEvents()

        try:
            req_contents = []
            for req_file in self.ppt_requirement_files:
                try:
                    with open(req_file, 'r', encoding='utf-8') as f:
                        req_contents.append(f.read())
                except Exception as e:
                    print(f"读取需求文件失败 {req_file}: {e}")

            template_path = self.ppt_templates[0]

            project_contents = []
            for proj_file in self.ppt_project_files:
                ext = os.path.splitext(proj_file)[1].lower()
                try:
                    if ext in ['.txt', '.csv']:
                        with open(proj_file, 'r', encoding='utf-8') as f:
                            project_contents.append(f.read())
                    elif ext in ['.doc', '.docx']:
                        from docx import Document
                        doc = Document(proj_file)
                        project_contents.append('\n'.join([p.text for p in doc.paragraphs]))
                    elif ext in ['.xlsx', '.xls']:
                        import openpyxl
                        wb = openpyxl.load_workbook(proj_file, data_only=True)
                        content = []
                        for sheet in wb.sheetnames:
                            ws = wb[sheet]
                            for row in ws.iter_rows(values_only=True):
                                row_content = [str(c) if c else '' for c in row]
                                content.append(' | '.join(row_content))
                        project_contents.append('\n'.join(content))
                except Exception as e:
                    print(f"读取项目资料失败 {proj_file}: {e}")

            title = self.ppt_report_title.text() if self.ppt_report_title.text() != '未设置标题' else '数据分析报告'
            prompt = f"""请根据以下信息生成PPT格式的数据分析报告。

报告标题: {title}

需求配置:
{chr(10).join(req_contents)}

模板文件: {template_path}

项目资料:
{chr(10).join(project_contents)}

请生成完整的PPT报告内容，包括幻灯片结构和大纲。"""

            if hasattr(self, 'ollama_client') and self.ollama_client and self.ollama_client.is_available():
                result = self.ollama_client.generate(prompt)
                if result:
                    self.ppt_gen_status.setText('状态: 已生成PPT报告')
                    self.ppt_gen_status.setStyleSheet('color: #52c41a; font-weight: bold; padding: 5px;')
                    self.ppt_ai_result.setPlainText('AI生成结果:\n' + result)
                else:
                    self.ppt_gen_status.setText('状态: PPT报告生成失败')
                    self.ppt_gen_status.setStyleSheet('color: #ff4d4f; font-weight: bold; padding: 5px;')
            else:
                self.ppt_gen_status.setText('状态: AI不可用，请配置Ollama服务')
                self.ppt_gen_status.setStyleSheet('color: #faad14; font-weight: bold; padding: 5px;')

        except Exception as e:
            self.ppt_gen_status.setText(f'状态: PPT报告生成失败 - {str(e)}')
            self.ppt_gen_status.setStyleSheet('color: #ff4d4f; font-weight: bold; padding: 5px;')
            import traceback
            traceback.print_exc()

    def on_save_ppt_report(self):
        """保存PPT报告"""
        settings = QSettings('DataProcessor', 'Pro')
        last_dir = settings.value('ppt_report_save_dir', '')

        file_path, _ = QFileDialog.getSaveFileName(
            self, '保存PPT报告', last_dir,
            'PowerPoint (*.pptx)'
        )

        if file_path:
            settings.setValue('ppt_report_save_dir', os.path.dirname(file_path))

            content = self.ppt_ai_result.toPlainText()
            if content and 'AI生成结果:' in content:
                content = content.split('AI生成结果:')[1]

            try:
                from pptx import Presentation
                prs = Presentation()
                title_slide_layout = prs.slide_layouts[0]
                title = title_slide_layout.shapes.title
                subtitle = title_slide_layout.placeholders[1]
                title.text = self.ppt_report_title.text() if self.ppt_report_title.text() != '未设置标题' else '数据分析报告'
                subtitle.text = content
                prs.save(file_path)
                QMessageBox.information(self, '成功', f'报告已保存至: {file_path}')
            except Exception as e:
                QMessageBox.warning(self, '警告', f'保存失败: {str(e)}')

    def on_ppt_ai_send(self):
        """PPT AI交互发送"""
        user_input = self.ppt_ai_chat_input.toPlainText().strip()
        if not user_input:
            return

        self.ppt_ai_result.append(f'用户: {user_input}')
        self.ppt_ai_chat_input.clear()

        if any(keyword in user_input for keyword in ['确认', '继续', '好的', '是', 'ok', 'yes']):
            self.ppt_ai_result.append('AI: 收到确认，继续生成报告...')
        else:
            self.ppt_ai_result.append('AI: 请提供更多信息以便继续生成报告。')


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
            'strain_cal: 应变标定',
            'temperature: 温度',
            'temp_cal: 温度标定',
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

        # Constants (hidden for decoupling)
        self.const_group = QGroupBox('常量')
        const_layout = QVBoxLayout()

        self.const_inputs = {}
        if sensor and sensor.constants:
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

        add_const_btn = QPushButton('添加常量')
        add_const_btn.clicked.connect(self.add_constant)
        const_layout.addWidget(add_const_btn)
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

    def _on_type_changed(self, text):
        """当传感器类型改变时切换表单显示"""
        is_decoupling = text.startswith('decoupling')
        self.expr_label.setVisible(not is_decoupling)
        self.expr_input.setVisible(not is_decoupling)
        self.const_group.setVisible(not is_decoupling)
        self.decoupling_panel.setVisible(is_decoupling)

    def _on_formula_changed(self, text):
        """当公式文本变化时，自动检测 k 变量并创建常量输入框"""
        import re
        k_vars = set(re.findall(r'\b[kK]\d+\b', text))
        # 标准化为小写
        needed = set()
        for v in k_vars:
            needed.add(v.lower())

        # 为缺失的常量自动创建输入框
        for name in sorted(needed):
            if name not in self.const_inputs:
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
            constants = {name: input_field.value() for name, input_field in self.const_inputs.items()}
            return Sensor(
                self.id_input.text(),
                sensor_type,
                self.expr_input.text(),
                constants,
                self.active_check.isChecked(),
                location=self.location_input.text(),
            )


class AIModelConfigDialog(QDialog):
    """AI模型配置对话框"""
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle('AI模型配置')
        self.setMinimumWidth(600)
        self.parent_window = parent

        layout = QVBoxLayout()

        # 模型列表
        list_layout = QHBoxLayout()
        self.model_list = QListWidget()
        self.model_list.setMinimumWidth(200)
        self.model_list.itemClicked.connect(self.on_model_item_clicked)
        list_layout.addWidget(self.model_list)

        # 模型配置表单
        form_layout = QVBoxLayout()

        # 配置名称
        name_row = QHBoxLayout()
        name_row.addWidget(QLabel('配置名称:'))
        self.name_input = QLineEdit()
        self.name_input.setPlaceholderText('给这个配置起个名字')
        name_row.addWidget(self.name_input)
        form_layout.addLayout(name_row)

        # API地址
        url_row = QHBoxLayout()
        url_row.addWidget(QLabel('API地址:'))
        self.url_input = QLineEdit()
        self.url_input.setPlaceholderText('https://api.openai.com/v1')
        url_row.addWidget(self.url_input)
        form_layout.addLayout(url_row)

        # API Key
        key_row = QHBoxLayout()
        key_row.addWidget(QLabel('API Key:'))
        self.key_input = QLineEdit()
        self.key_input.setEchoMode(QLineEdit.EchoMode.Password)
        self.key_input.setPlaceholderText('sk-...')
        key_row.addWidget(self.key_input)
        form_layout.addLayout(key_row)

        # 模型名称
        model_row = QHBoxLayout()
        model_row.addWidget(QLabel('模型名称:'))
        self.model_input = QLineEdit()
        self.model_input.setPlaceholderText('gpt-4o, qwen2.5, deepseek-chat等')
        model_row.addWidget(self.model_input)
        form_layout.addLayout(model_row)

        # 系统提示词
        sys_row = QHBoxLayout()
        sys_row.addWidget(QLabel('系统提示:'))
        self.sys_input = QLineEdit()
        self.sys_input.setPlaceholderText('你是一个...')
        sys_row.addWidget(self.sys_input)
        form_layout.addLayout(sys_row)

        list_layout.addLayout(form_layout)
        layout.addLayout(list_layout)

        # 按钮行
        btn_layout = QHBoxLayout()

        add_btn = QPushButton('新建')
        add_btn.setStyleSheet('background-color: #52c41a; color: white;')
        add_btn.clicked.connect(self.on_add_model)
        btn_layout.addWidget(add_btn)

        save_btn = QPushButton('保存')
        save_btn.setStyleSheet('background-color: #1890ff; color: white;')
        save_btn.clicked.connect(self.on_save_model)
        btn_layout.addWidget(save_btn)

        test_btn = QPushButton('测试')
        test_btn.setStyleSheet('background-color: #faad14; color: white;')
        test_btn.clicked.connect(self.on_test_model)
        btn_layout.addWidget(test_btn)

        delete_btn = QPushButton('删除')
        delete_btn.setStyleSheet('background-color: #ff4d4f; color: white;')
        delete_btn.clicked.connect(self.on_delete_model)
        btn_layout.addWidget(delete_btn)

        close_btn = QPushButton('关闭')
        close_btn.clicked.connect(self.close)
        btn_layout.addWidget(close_btn)

        layout.addLayout(btn_layout)

        self.setLayout(layout)
        self.load_models()

    def load_models(self):
        """加载模型列表"""
        self.model_list.clear()
        if hasattr(self.parent_window, 'ai_models_config'):
            for name in self.parent_window.ai_models_config:
                self.model_list.addItem(name)

    def on_model_item_clicked(self, item):
        """选择模型"""
        model_name = item.text()
        if hasattr(self.parent_window, 'ai_models_config') and model_name in self.parent_window.ai_models_config:
            config = self.parent_window.ai_models_config[model_name]
            self.name_input.setText(model_name)
            self.url_input.setText(config.get('base_url', ''))
            self.key_input.setText(config.get('api_key', ''))
            self.model_input.setText(config.get('model_name', ''))
            self.sys_input.setText(config.get('system_prompt', ''))

    def on_add_model(self):
        """新建模型配置"""
        self.name_input.clear()
        self.url_input.clear()
        self.key_input.clear()
        self.model_input.clear()
        self.sys_input.clear()
        self.name_input.setFocus()

    def on_save_model(self):
        """保存模型配置"""
        name = self.name_input.text().strip()
        if not name:
            QMessageBox.warning(self, '警告', '请输入配置名称')
            return

        config = {
            'base_url': self.url_input.text().strip(),
            'api_key': self.key_input.text().strip(),
            'model_name': self.model_input.text().strip(),
            'system_prompt': self.sys_input.text().strip()
        }

        if not hasattr(self.parent_window, 'ai_models_config'):
            self.parent_window.ai_models_config = {}

        self.parent_window.ai_models_config[name] = config
        self.parent_window.save_ai_models_config()
        self.parent_window.refresh_ai_model_selector()
        self.load_models()
        QMessageBox.information(self, '成功', '模型配置已保存')

    def on_test_model(self):
        """测试模型连接"""
        api_url = self.url_input.text().strip()
        api_key = self.key_input.text().strip()
        model_name = self.model_input.text().strip()

        if not api_url or not model_name:
            QMessageBox.warning(self, '警告', '请填写API地址和模型名称')
            return

        try:
            import requests
            headers = {
                'Authorization': f'Bearer {api_key}',
                'Content-Type': 'application/json'
            } if api_key else {'Content-Type': 'application/json'}

            # 标准化URL，确保正确拼接chat/completions
            api_url = api_url.rstrip('/')
            if not api_url.endswith('/v1'):
                api_url = api_url + '/v1'

            data = {
                'model': model_name,
                'messages': [{'role': 'user', 'content': 'Say "OK" in one word.'}],
                'max_tokens': 10
            }
            response = requests.post(
                f'{api_url}/chat/completions',
                headers=headers,
                json=data,
                timeout=30
            )
            if response.status_code == 200:
                QMessageBox.information(self, '成功', '模型测试成功!')
                # 更新主窗口状态指示灯
                if hasattr(self.parent_window, 'ai_status_label'):
                    self.parent_window.ai_status_label.setStyleSheet('color: #52c41a; font-size: 20px;')
                if hasattr(self.parent_window, 'ai_status_label'):
                    self.parent_window.ai_status_label.setText('测试成功')
                    self.parent_window.ai_status_label.setStyleSheet('color: #52c41a;')
            else:
                QMessageBox.warning(self, '错误', f'测试失败: {response.status_code}\n{response.text[:200]}')
        except Exception as e:
            QMessageBox.warning(self, '错误', f'测试失败: {str(e)}')

    def on_delete_model(self):
        """删除模型配置"""
        name = self.name_input.text().strip()
        if not name:
            QMessageBox.warning(self, '警告', '请先选择一个要删除的配置')
            return

        reply = QMessageBox.question(self, '确认', f'确定要删除配置"{name}"吗?')
        if reply == QMessageBox.StandardButton.Yes:
            if hasattr(self.parent_window, 'ai_models_config') and name in self.parent_window.ai_models_config:
                del self.parent_window.ai_models_config[name]
                self.parent_window.save_ai_models_config()
                self.parent_window.refresh_ai_model_selector()
                self.load_models()
                self.on_add_model()
                QMessageBox.information(self, '成功', '配置已删除')

    def close(self):
        """关闭对话框"""
        super().accept()


# ============ Application Entry ============

def main():
    app = QApplication(sys.argv)
    app.setStyle('Fusion')

    # 加载自定义模板
    DataProcessorWindow.load_custom_templates()

    window = DataProcessorWindow()
    window.show()

    sys.exit(app.exec())

if __name__ == '__main__':
    main()