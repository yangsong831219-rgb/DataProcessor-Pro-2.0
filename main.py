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
from PyQt6.QtCore import Qt, QThread, QTimer, QSettings, pyqtSignal
from PyQt6.QtGui import QAction, QIcon

import pandas as pd
import numpy as np
from docx import Document

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

# ============ Utility Module Imports ============
from utils.file_parser import parse_file, detect_format, detect_header, try_read_csv
from utils.data_cleaning import clean_data, detect_anomalies, fill_missing

# ============ Backend Module Imports ============
from py.wiki_system import WikiFileSystem
from py.multi_agent import run_multi_agent, MultiAgentState

# ============ UI Module Imports ============
from ui.report_workbench import ReportWorkbenchWidget
from ui.ai_diagnosis import AiDiagnosisWidget
from ui.analysis_tab import AnalysisTabWidget
from ui.global_parameter_dialog import GlobalParameterDialog
from ui.data_tab import DataTabWidget
from ui.cleaning_tab import CleaningTabWidget
from ui.sensor_tab import SensorTabWidget
from ui.project_tab import ProjectManagerWidget
from ui.wiki_tab import WikiTabWidget
from ui.clipper_tab import WebClipperWidget
from ui.skill_tab import AgentSkillWidget
from ui.compare_tab import CompareTabWidget

# ============ Report Generation ============
from core.report_engine import generate_outline, generate_structured_report

# ============ Core Data Models (SSOT) ============
from core.models import (
    DataTemplate, CleaningRule, FBG, GlobalParameter, Sensor, SensorSystem,
    DEFAULT_TEMPLATES,
)

# ═══════════════════════════════════════════════
# 报告输出目录
# ═══════════════════════════════════════════════

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
REPORT_OUTPUT_DIR = os.path.join(BASE_DIR, 'output_reports')

# ============ App State (SSOT) ============
from state.app_state import AppState

# ============ Main Window ============

class DataProcessorWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        # 确保报告输出目录存在
        os.makedirs(REPORT_OUTPUT_DIR, exist_ok=True)
        self.current_data = None
        self.current_columns = []
        self.sampled_data = None
        self.file_header_lines = []
        self.sampled_file_path = None
        self.current_template = None
        self.sensor_results = {}  # 存储计算后的传感器物理量
        self.cleaning_rules = [
            CleaningRule(name='数值范围', rule_type='range', enabled=True, min_value=0, max_value=100, fill_method='linear'),
            CleaningRule(name='负数检测', rule_type='negative', enabled=True, fill_method='forward'),
        ]
        self.sensor_system = SensorSystem()
        self.init_sensor_system()
        
        # SSOT: 全局应用状态
        self.state = AppState()
        self.state = self.state.with_fbgs(self.sensor_system.fbgs)
        self.state = self.state.with_sensors(self.sensor_system.sensors)
        # 全局参数 → SSOT
        initial_gp = {
            name: GlobalParameter(
                name=name,
                value=v if isinstance(v, (int, float)) else v.get('value', 0),
                unit=v.get('unit', '') if isinstance(v, dict) else '',
                description=v.get('description', '') if isinstance(v, dict) else '',
            )
            for name, v in self.sensor_system.global_parameters.items()
        }
        self.state = self.state.with_global_parameters(initial_gp)

        # 传感器系统

        # Ollama AI客户端
        try:
            from py.ollama_client import OllamaClient
            self.ollama_client = OllamaClient()
        except ImportError:
            self.ollama_client = None

        self.init_ui()

        # AI诊断Widget延迟加载模型配置

    # ── AI诊断兼容性代理 (AiDiagnosisWidget 独立管理模型配置) ──

    @property
    def ai_model_combo(self):
        if hasattr(self, 'ai_diagnosis_widget'):
            return self.ai_diagnosis_widget.ai_model_combo
        return None

    @property
    def ai_models_config(self):
        if hasattr(self, 'ai_diagnosis_widget'):
            return self.ai_diagnosis_widget._ai_models_config
        return {}

    def load_ai_models_config(self):
        pass

    def save_ai_models_config(self):
        if hasattr(self, 'ai_diagnosis_widget'):
            self.ai_diagnosis_widget._save_models_config()

    # ── App 本地设置持久化 ──

    @staticmethod
    def _get_app_settings_path() -> str:
        return os.path.join(os.path.dirname(os.path.abspath(__file__)), '.app_settings.json')

    @classmethod
    def _load_app_settings(cls) -> dict:
        path = cls._get_app_settings_path()
        if os.path.exists(path):
            try:
                with open(path, 'r', encoding='utf-8') as f:
                    return json.load(f)
            except Exception:
                pass
        return {}

    @classmethod
    def _save_app_setting(cls, key: str, value) -> None:
        settings = cls._load_app_settings()
        settings[key] = value
        path = cls._get_app_settings_path()
        with open(path, 'w', encoding='utf-8') as f:
            json.dump(settings, f, ensure_ascii=False, indent=2)

    def refresh_ai_model_selector(self):
        if hasattr(self, 'ai_diagnosis_widget'):
            self.ai_diagnosis_widget.refresh_model_selector()

    # ── 数据分析兼容性代理 (AnalysisTabWidget 独立管理图表控件) ──

    @property
    def data_source_combo(self):
        if hasattr(self, 'analysis_tab_widget'):
            return self.analysis_tab_widget.data_source_combo
        return None

    @property
    def range_type_combo(self):
        if hasattr(self, 'analysis_tab_widget'):
            return self.analysis_tab_widget.range_type_combo
        return None

    @property
    def range_start(self):
        if hasattr(self, 'analysis_tab_widget'):
            return self.analysis_tab_widget.range_start
        return None

    @property
    def range_end(self):
        if hasattr(self, 'analysis_tab_widget'):
            return self.analysis_tab_widget.range_end
        return None

    def refresh_analysis_sensors(self):
        if hasattr(self, 'analysis_tab_widget'):
            self.analysis_tab_widget.refresh_analysis_sensors()

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
        self.data_tab_widget = DataTabWidget()
        self.central_widget.addTab(self.data_tab_widget, '数据文件')

        # Cleaning tab
        self.cleaning_tab_widget = CleaningTabWidget()
        self.central_widget.addTab(self.cleaning_tab_widget, '数据清洗')

        # Sensor tab
        self.sensor_tab_widget = SensorTabWidget()
        self.central_widget.addTab(self.sensor_tab_widget, '光纤公式配置')

        # Analysis tab
        self.analysis_tab = QWidget()
        self.create_analysis_tab()
        self.central_widget.addTab(self.analysis_tab, '数据分析')

        # Compare tab (多源数据对比)
        self.compare_tab_widget = CompareTabWidget()
        self.central_widget.addTab(self.compare_tab_widget, '多源对比')

        # Report tab
        self.report_tab = QWidget()
        self.create_report_tab()
        self.central_widget.addTab(self.report_tab, '成果输出与报告')

        # ── Signal bindings ──
        self.data_tab_widget.open_file_requested.connect(self.open_file)
        self.data_tab_widget.clear_data_requested.connect(self.clear_data)
        self.data_tab_widget.sample_data_requested.connect(self.sample_data)
        self.data_tab_widget.save_data_requested.connect(self.save_sampled_data)
        self.data_tab_widget.save_template_requested.connect(self.save_template_to_file)

        # ── 表格编辑 → 底层 DataFrame 双向同步 ──
        self.data_tab_widget.data_cell_edited.connect(self._on_data_table_cell_edited)

        self.cleaning_tab_widget.apply_cleaning_requested.connect(
            lambda c: self.apply_cleaning(config=c)
        )

        self.sensor_tab_widget.fbg_add_requested.connect(self.add_fbg)
        self.sensor_tab_widget.fbg_edit_requested.connect(
            lambda row: self.edit_fbg(row)
        )
        self.sensor_tab_widget.fbg_delete_requested.connect(
            lambda row: self.delete_fbg(row)
        )
        self.sensor_tab_widget.sensor_add_requested.connect(self.add_sensor)
        self.sensor_tab_widget.sensor_edit_requested.connect(
            lambda row: self.edit_sensor(row)
        )
        self.sensor_tab_widget.sensor_delete_requested.connect(
            lambda row: self.delete_sensor(row)
        )
        self.sensor_tab_widget.sensor_copy_requested.connect(
            lambda row: self.copy_sensor(row)
        )
        self.sensor_tab_widget.calculate_requested.connect(
            lambda ref_row: self.calculate_sensors(ref_row)
        )
        self.sensor_tab_widget.save_sensor_requested.connect(
            lambda name, fmt: self.save_sensor_data(name, fmt)
        )
        self.sensor_tab_widget.export_sensor_requested.connect(self.export_sensor_data)
        self.sensor_tab_widget.global_params_requested.connect(self.open_global_parameter_dialog)

        self.project_tab_widget.project_selected.connect(self.on_project_selected)
        self.project_tab_widget.new_project_requested.connect(self.on_new_project)
        self.project_tab_widget.delete_project_requested.connect(self.on_delete_project)
        self.project_tab_widget.open_project_folder_requested.connect(self.on_open_project_folder)
        self.project_tab_widget.add_folder_requested.connect(self.on_add_folder_to_project)
        self.project_tab_widget.add_file_requested.connect(self.on_add_file_to_project)
        self.project_tab_widget.delete_item_requested.connect(self.on_delete_project_item)
        self.project_tab_widget.open_item_requested.connect(self.on_open_project_item)

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

    def add_fbg(self):
        """添加FBG"""
        dialog = FBGEditDialog(self)
        if dialog.exec():
            fbg = dialog.get_fbg()
            self.sensor_system.add_fbg(fbg)
            self.sensor_tab_widget.set_fbg_list(self.sensor_system.fbgs)

    def edit_fbg(self, row: int):
        """编辑选中FBG"""
        if 0 <= row < len(self.sensor_system.fbgs):
            fbg = self.sensor_system.fbgs[row]
            dialog = FBGEditDialog(self, fbg)
            if dialog.exec():
                self.sensor_system.fbgs[row] = dialog.get_fbg()
                self.sensor_tab_widget.set_fbg_list(self.sensor_system.fbgs)

    def delete_fbg(self, row: int):
        """删除选中FBG"""
        if 0 <= row < len(self.sensor_system.fbgs):
            self.sensor_system.fbgs.pop(row)
            self.sensor_tab_widget.set_fbg_list(self.sensor_system.fbgs)

    def _auto_populate_fbgs(self, df):
        """从数据文件列名自动识别FBG传感器并填充FBG定义表。"""
        if df is None or df.empty:
            return 0

        fbg_cols = [str(c) for c in df.columns if str(c).upper().startswith('FBG_')]
        if not fbg_cols:
            fbg_cols = [str(c) for c in df.columns
                       if '波长' in str(c) or 'wavelength' in str(c).lower()]
        if not fbg_cols:
            w_digit_cols = [str(c) for c in df.columns
                           if str(c).upper().startswith('W') and str(c)[1:].isdigit()]
            if len(w_digit_cols) >= 2:
                fbg_cols = w_digit_cols
        if not fbg_cols:
            return 0

        self.sensor_system.fbgs.clear()
        for i, col in enumerate(fbg_cols):
            self.sensor_system.add_fbg(FBG(f'W{i+1}', col, 1520, 1590))

        self.sensor_tab_widget.set_fbg_list(self.sensor_system.fbgs)
        print(f"[FBG自动识别] 从数据文件识别到 {len(fbg_cols)} 个FBG: {fbg_cols}")
        return len(fbg_cols)

    def add_sensor(self):
        """添加传感器"""
        dialog = SensorEditDialog(self)
        if dialog.exec():
            sensor = dialog.get_sensor()
            self.sensor_system.add_sensor(sensor)
            self.sensor_tab_widget.set_sensor_list(self.sensor_system.sensors)

    def edit_sensor(self, row: int):
        """编辑选中传感器"""
        if 0 <= row < len(self.sensor_system.sensors):
            sensor = self.sensor_system.sensors[row]
            dialog = SensorEditDialog(self, sensor)
            if dialog.exec():
                self.sensor_system.sensors[row] = dialog.get_sensor()
                self.sensor_tab_widget.set_sensor_list(self.sensor_system.sensors)

    def delete_sensor(self, row: int):
        """删除选中传感器"""
        if 0 <= row < len(self.sensor_system.sensors):
            self.sensor_system.sensors.pop(row)
            self.sensor_tab_widget.set_sensor_list(self.sensor_system.sensors)

    def copy_sensor(self, row: int):
        """复制选中传感器，自动递增ID和公式中的W编号"""
        if 0 <= row < len(self.sensor_system.sensors):
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
            self.sensor_tab_widget.set_sensor_list(self.sensor_system.sensors)

    def open_global_parameter_dialog(self):
        dialog = GlobalParameterDialog(self)
        dialog.set_parameters(self.state.global_parameters)

        def _on_param_updated(name: str, entry: dict):
            # 更新 AppState (SSOT)
            gp = GlobalParameter(
                name=name,
                value=entry.get('value', 0),
                unit=entry.get('unit', ''),
                description=entry.get('description', ''),
            )
            new_params = dict(self.state.global_parameters)
            new_params[name] = gp
            self.state = self.state.with_global_parameters(new_params)

            # 同步到 SensorSystem（公式计算引擎使用）
            self.sensor_system.global_parameters[name] = {
                'value': entry.get('value', 0),
                'unit': entry.get('unit', ''),
                'description': entry.get('description', ''),
            }

            # 自动重算
            self._recalc_after_global_param_update()

        def _on_param_deleted(name: str):
            new_params = dict(self.state.global_parameters)
            new_params.pop(name, None)
            self.state = self.state.with_global_parameters(new_params)
            self.sensor_system.global_parameters.pop(name, None)
            self._recalc_after_global_param_update()

        dialog.param_updated.connect(_on_param_updated)
        dialog.param_deleted.connect(_on_param_deleted)

        if dialog.exec() == QDialog.DialogCode.Accepted:
            self.status_bar.showMessage('全局参数已更新')
        else:
            self.status_bar.showMessage('全局参数已关闭')

    def _recalc_after_global_param_update(self):
        """全局参数变更后自动重新计算传感器."""
        if self.current_data is not None and self.sensor_system.sensors:
            try:
                analysis_df, _, _ = self._get_analysis_data()
                if analysis_df is None or analysis_df.empty:
                    return
                results = self.sensor_system.calculate(analysis_df, self.current_columns)
                self.sensor_results = results
                self.state = self.state.with_analysis_results(results)
                self.sensor_tab_widget.set_result_preview(results, analysis_df)
                self.analysis_tab_widget.set_sensor_results(results)
                self.refresh_analysis_sensors()
                self.status_bar.showMessage('全局参数已更新，传感器已重新计算')
            except Exception as e:
                print(f'全局参数更新后重算失败: {e}')

    def calculate_sensors(self, ref_row: int = 0):
        """计算所有传感器"""
        if self.current_data is None:
            QMessageBox.warning(self, '警告', '请先加载数据')
            return

        try:
            self.sensor_system.set_reference_row(ref_row)

            # 使用跳过暗号行后的清洗数据
            analysis_df, _, _ = self._get_analysis_data()
            if analysis_df is None or analysis_df.empty:
                QMessageBox.warning(self, '警告', '有效数据为空')
                return

            results = self.sensor_system.calculate(analysis_df, self.current_columns)

            n_preview_rows = min(100, len(analysis_df))
            self.sensor_tab_widget.set_result_preview(results, analysis_df)
            self.sensor_results = results
            self.state = self.state.with_analysis_results(results)
            self.analysis_tab_widget.set_sensor_results(results)
            self.refresh_analysis_sensors()
            self.status_bar.showMessage(f'传感器计算完成，预览显示前{n_preview_rows}行')

        except Exception as e:
            import traceback
            QMessageBox.critical(self, '错误', f'计算失败: {str(e)}\n\n{traceback.format_exc()}')

    def save_sensor_data(self, filename: str = '', fmt: str = 'csv'):
        """保存传感器数据到指定路径"""
        if self.current_data is None:
            QMessageBox.warning(self, '警告', '请先加载数据')
            return

        if not filename:
            QMessageBox.warning(self, '警告', '请输入文件名')
            return

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
                ref_row = self.sensor_tab_widget.ref_row_spin.value()
                self.sensor_system.set_reference_row(ref_row)
                analysis_df, time_col, _ = self._get_analysis_data()
                if analysis_df is None or analysis_df.empty:
                    QMessageBox.warning(self, '警告', '有效数据为空')
                    return
                results = self.sensor_system.calculate(analysis_df, self.current_columns)

                export_df = pd.DataFrame()
                export_df[time_col] = analysis_df[time_col]
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
                analysis_df, time_col, _ = self._get_analysis_data()
                if analysis_df is None or analysis_df.empty:
                    QMessageBox.warning(self, '警告', '有效数据为空')
                    return
                results = self.sensor_system.calculate(analysis_df, self.current_columns)

                export_df = pd.DataFrame()
                export_df[time_col] = analysis_df[time_col]
                for sensor_id, values in results.items():
                    export_df[sensor_id] = values

                export_df.to_csv(file_path, index=False)
                QMessageBox.information(self, '成功', f'数据已导出到: {file_path}')
            except Exception as e:
                QMessageBox.critical(self, '错误', f'导出失败: {str(e)}')

    def create_analysis_tab(self):
        """数据分析选项卡"""
        self.analysis_tab_widget = AnalysisTabWidget(self)
        layout = QVBoxLayout()
        layout.addWidget(self.analysis_tab_widget)
        self.analysis_tab.setLayout(layout)

    def create_report_tab(self):
        # 成果输出与报告Tab
        main_layout = QHBoxLayout()

        # 左侧：功能选项列表
        left_panel = QWidget()
        left_layout = QVBoxLayout()
        left_layout.setContentsMargins(0, 0, 0, 0)

        # 成果输出与报告标题
        title_label = QLabel('成果输出与报告')
        title_label.setStyleSheet('font-size: 16px; font-weight: bold; padding: 5px;')
        left_layout.addWidget(title_label)

        # 功能按钮列表 (已移除项目内容，合并到项目资料管理)
        self.info_menu_list = QListWidget()
        self.info_menu_list.setMaximumWidth(180)
        self.info_menu_list.addItem('项目资料管理')
        self.info_menu_list.addItem('报告生成工作台')
        self.info_menu_list.addItem('AI诊断')
        self.info_menu_list.addItem('网络剪藏')
        self.info_menu_list.addItem('知识库管理')
        self.info_menu_list.addItem('技能插件中心')
        self.info_menu_list.currentRowChanged.connect(self.on_info_menu_changed)
        left_layout.addWidget(self.info_menu_list)

        left_panel.setLayout(left_layout)
        main_layout.addWidget(left_panel)

        # 右侧：内容面板
        self.report_content_stack = QStackedWidget()
        main_layout.addWidget(self.report_content_stack, 1)


        # 项目资料管理页面
        self.project_tab_widget = ProjectManagerWidget()
        self.report_content_stack.addWidget(self.project_tab_widget)
        self.load_project_list()

        # 报告生成工作台 (合并 Word + PPT)
        report_workbench = self.create_report_workbench_page()
        self.report_content_stack.addWidget(report_workbench)

        # AI诊断页面
        ai_diagnosis_page = self.create_ai_diagnosis_page()
        self.report_content_stack.addWidget(ai_diagnosis_page)

        # 网络剪藏页面
        self.clipper_tab_widget = WebClipperWidget()
        self.report_content_stack.addWidget(self.clipper_tab_widget)

        # 知识库管理页面
        self.wiki_tab_widget = WikiTabWidget()
        self.report_content_stack.addWidget(self.wiki_tab_widget)

        # 技能插件中心页面
        self.skill_tab_widget = AgentSkillWidget()
        self.report_content_stack.addWidget(self.skill_tab_widget)

        self.report_tab.setLayout(main_layout)

        # 加载已保存的 Cookie
        saved_cookie = self._load_app_settings().get('zhihu_cookie', '')
        self.clipper_tab_widget.set_cookie(saved_cookie)
        self.clipper_tab_widget.cookie_changed.connect(
            lambda c: self._save_app_setting('zhihu_cookie', c)
        )
        self.clipper_tab_widget.wiki_page_saved.connect(
            self.wiki_tab_widget.refresh_wiki_pages
        )

        self.analysis_tab_widget.set_sensor_system(self.sensor_system)
        # 分析页"刷新"按钮 → 重新从暗号标注获取分析数据
        self.analysis_tab_widget.data_refresh_requested.connect(self._on_analysis_refresh_requested)

    def create_report_workbench_page(self):
        """统一的报告生成工作台 (合并 Word + PPT)"""
        self.report_workbench_widget = ReportWorkbenchWidget(self)
        # 信号绑定
        self.report_workbench_widget.outline_requested.connect(
            self._handle_outline_generation
        )
        self.report_workbench_widget.full_report_requested.connect(
            self._handle_full_report_generation
        )
        return self.report_workbench_widget

    # ═══════════════════════════════════════════════
    # 后台 Worker (QThread)
    # ═══════════════════════════════════════════════

    class _ReportWorker(QThread):
        """通用后台 Worker — 在子线程执行耗时的 AI/渲染任务."""

        finished = pyqtSignal(object)
        error = pyqtSignal(str)

        def __init__(self, work_fn, args=None, kwargs=None):
            super().__init__()
            self._work_fn = work_fn
            self._args = args or ()
            self._kwargs = kwargs or {}

        def run(self):
            try:
                result = self._work_fn(*self._args, **self._kwargs)
                self.finished.emit(result)
            except Exception as e:
                import traceback
                self.error.emit(f'{e}\n{traceback.format_exc()}')

    # ═══════════════════════════════════════════════
    # 大纲生成 — 后台 AI 调用
    # ═══════════════════════════════════════════════

    def _get_generate_fn(self):
        """获取 LLM 生成回调.

        优先级: AIClient (DeepSeek) > OllamaClient > 模拟降级.
        """
        from core.ai_client import AIClient
        ai = AIClient.get_instance()
        if ai.is_available():
            return ai.generate
        if self.ollama_client and self.ollama_client.is_available():
            return self.ollama_client.generate
        # 降级: 返回模拟生成函数
        def _fallback(prompt: str) -> str:
            return (
                '# 传感器数据分析报告\n\n'
                '## 数据概述\n'
                '- 数据来源与采集方式\n'
                '- 传感器布设方案\n\n'
                '## 数据质量评估\n'
                '- 异常值检测结果\n'
                '- 缺失值统计\n\n'
                '## 物理量分析\n'
                '- 应变分析\n'
                '- 温度分析\n\n'
                '## 结论与建议\n'
                '- 主要发现\n'
                '- 后续工作建议\n'
            )
        return _fallback

    def _handle_outline_generation(self, config: dict):
        """后台生成大纲，完成后填入编辑器."""
        generate_fn = self._get_generate_fn()
        self.report_workbench_widget.outline_btn.setEnabled(False)
        self.report_workbench_widget.outline_btn.setText('⏳ 正在生成大纲...')
        self.status_bar.showMessage('正在生成大纲...')

        def _on_outline_done(markdown: str):
            self.report_workbench_widget.set_outline_text(markdown)
            self.report_workbench_widget.outline_btn.setEnabled(True)
            self.report_workbench_widget.outline_btn.setText('📝 生成/预览报告大纲')
            self.status_bar.showMessage('大纲生成完成')

        def _on_outline_error(msg: str):
            self.report_workbench_widget.outline_btn.setEnabled(True)
            self.report_workbench_widget.outline_btn.setText('📝 生成/预览报告大纲')
            self.status_bar.showMessage('大纲生成失败')
            QMessageBox.critical(self, '大纲生成失败', msg)

        self._outline_worker = self._ReportWorker(
            generate_outline,
            args=(config, generate_fn),
        )
        self._outline_worker.finished.connect(_on_outline_done)
        self._outline_worker.error.connect(_on_outline_error)
        self._outline_worker.start()

    # ═══════════════════════════════════════════════
    # 完整报告生成 — 后台 AI + 渲染
    # ═══════════════════════════════════════════════

    def _handle_full_report_generation(self, config: dict, outline: str):
        """后台生成结构化报告 + 渲染保存 + 自动打开输出目录."""
        generate_fn = self._get_generate_fn()
        self.report_workbench_widget.full_report_btn.setEnabled(False)
        self.report_workbench_widget.outline_btn.setEnabled(False)
        self.status_bar.showMessage('正在生成完整报告...')

        report_type = config.get('report_type', 'word')
        ext = '.pptx' if report_type == 'ppt' else '.docx'
        type_label = 'PPT演示' if report_type == 'ppt' else 'Word报告'

        # 构建输出路径: output_reports/项目名_报告类型_时间戳.ext
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        req_file = config.get('req_file', '')
        project_name = os.path.splitext(os.path.basename(req_file))[0] if req_file else '数据分析报告'
        filename = f'{project_name}_{type_label}_{timestamp}{ext}'
        output_path = os.path.join(REPORT_OUTPUT_DIR, filename)

        template_path = config.get('template_file', '')

        # 项目根目录（用于拼接图片绝对路径）
        project_dir = os.path.dirname(os.path.abspath(req_file)) if req_file else ''

        # ── Agentic 工具配置（Function Calling） ──
        from core.tools.chart_tool import (
            GENERATE_SENSOR_PLOT_TOOL,
            generate_sensor_plot,
        )

        state_data = self.state.to_dict()
        tools_config = {
            'definitions': [GENERATE_SENSOR_PLOT_TOOL],
            'executable_map': {
                'generate_sensor_plot': lambda sid, pt: generate_sensor_plot(
                    sensor_id=sid,
                    plot_type=pt,
                    project_dir=project_dir,
                    state_data=state_data,
                ),
            },
        }

        def _build_and_save():
            # 1. AI 生成结构化数据
            builder_data = generate_structured_report(
                config, outline, report_type, generate_fn,
                tools_config=tools_config,
            )
            # 2. 调用文件级渲染器直接保存到 output_path
            if report_type == 'ppt':
                from py.report_builder.ppt_builder import PPTBuilder
                from py.report_builder.models import PPTReport
                report = PPTReport.from_dict(builder_data)
                builder = PPTBuilder()
                builder.build_ppt_report(report, template_path, output_path, project_dir=project_dir)
            else:
                from py.report_builder.word_builder import WordBuilder
                from py.report_builder.models import WordReport
                report = WordReport.from_dict(builder_data)
                builder = WordBuilder()
                builder.build_word_report(report, template_path, output_path, project_dir=project_dir)
            return output_path

        def _on_report_done(path: str):
            self.report_workbench_widget.full_report_btn.setEnabled(True)
            self.report_workbench_widget.outline_btn.setEnabled(True)
            self.status_bar.showMessage(f'报告已保存: {os.path.basename(path)}')

            QMessageBox.information(self, '生成成功', f'报告已保存至:\n{path}')
            # 自动打开输出目录
            try:
                os.startfile(REPORT_OUTPUT_DIR)
            except Exception:
                pass

        def _on_report_error(msg: str):
            self.report_workbench_widget.full_report_btn.setEnabled(True)
            self.report_workbench_widget.outline_btn.setEnabled(True)
            self.status_bar.showMessage('报告生成失败')
            QMessageBox.critical(self, '报告生成失败', msg)

        self._report_worker = self._ReportWorker(_build_and_save)
        self._report_worker.finished.connect(_on_report_done)
        self._report_worker.error.connect(_on_report_error)
        self._report_worker.start()

    def create_ai_diagnosis_page(self):
        """AI诊断页面"""
        self.ai_diagnosis_widget = AiDiagnosisWidget(self)
        return self.ai_diagnosis_widget

    def load_project_list(self):
        """加载项目列表"""
        settings = QSettings('DataProcessor', 'Pro')
        projects_dir = settings.value('projects_directory', '')

        items = []
        if projects_dir and os.path.exists(projects_dir):
            try:
                for entry in os.listdir(projects_dir):
                    if os.path.isdir(os.path.join(projects_dir, entry)):
                        items.append(entry)
            except Exception as e:
                print(f"加载项目列表失败: {e}")

        self.project_tab_widget.set_project_list(items)

    def on_project_selected(self, project_name: str):
        """选择项目时加载内容"""
        settings = QSettings('DataProcessor', 'Pro')
        projects_dir = settings.value('projects_directory', '')

        if not projects_dir:
            return

        self.current_project_path = os.path.join(projects_dir, project_name)
        self.project_tab_widget.set_current_project_label(project_name, exists=True)
        self.project_tab_widget.load_project_tree(self.current_project_path)

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

    def on_delete_project(self, project_name: str):
        """删除项目"""
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

        try:
            import shutil
            shutil.rmtree(os.path.join(projects_dir, project_name))
            self.load_project_list()
            self.project_tab_widget.clear_tree()
            self.project_tab_widget.set_current_project_label("")
            QMessageBox.information(self, '成功', f'项目 "{project_name}" 已删除')
        except Exception as e:
            QMessageBox.warning(self, '错误', f'删除项目失败: {str(e)}')

    def on_open_project_folder(self):
        """用资源管理器打开项目文件夹"""
        current_item = self.project_tab_widget.project_list_widget.currentItem()
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

        folder_path = os.path.join(self.current_project_path, folder_name.strip())
        if os.path.exists(folder_path):
            QMessageBox.warning(self, '警告', '文件夹已存在!')
            return

        try:
            os.makedirs(folder_path)
            self.project_tab_widget.load_project_tree(self.current_project_path)
            QMessageBox.information(self, '成功', f'文件夹 "{folder_name.strip()}" 创建成功!')
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

            tree = self.project_tab_widget.project_tree_widget
            current_item = tree.currentItem()
            target_dir = self.current_project_path
            if current_item and current_item.text(1) == "文件夹":
                target_dir = os.path.join(self.current_project_path, current_item.text(0))

            import shutil
            for fp in file_paths:
                shutil.copy2(fp, os.path.join(target_dir, os.path.basename(fp)))

            self.project_tab_widget.load_project_tree(self.current_project_path)
            QMessageBox.information(self, '成功', f'已添加 {len(file_paths)} 个文件!')
        except Exception as e:
            QMessageBox.warning(self, '错误', f'添加文件失败: {str(e)}')

    def on_delete_project_item(self):
        """删除选中的文件或文件夹"""
        tree = self.project_tab_widget.project_tree_widget
        current_item = tree.currentItem()
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

            self.project_tab_widget.load_project_tree(self.current_project_path)
            QMessageBox.information(self, '成功', f'"{item_name}" 已删除')
        except Exception as e:
            QMessageBox.warning(self, '错误', f'删除失败: {str(e)}')

    def on_open_project_item(self):
        """打开选中的文件"""
        current_item = self.project_tab_widget.project_tree_widget.currentItem()
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
        self.project_tab_widget.set_project_list([])
        self.project_tab_widget.clear_tree()
        self.project_tab_widget.set_current_project_label("")

    # ============ [wiki_tab.py] 知识库管理页面已提取 ============

    # ============ [clipper_tab.py] 网络剪藏页面已提取 ============

    # ============ [skill_tab.py] 技能插件中心已提取 ============

    def on_info_menu_changed(self, row):
        """切换成果输出功能页面"""
        self.report_content_stack.setCurrentIndex(row)

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
                'global_parameters': {
                    name: {'value': p.value, 'unit': p.unit, 'description': p.description}
                    for name, p in self.state.global_parameters.items()
                },
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

            # ---- 数据文件模块 ----
            config['data_tab'] = {
                'file_path': self.sampled_file_path,
                'template_id': self.current_template.id if self.current_template else None,
                'template_name': self.current_template.name if self.current_template else None,
                'file_header_lines': self.file_header_lines,
                'current_columns': self.current_columns,
            }

            # ---- 数据清洗模块 ----
            config['cleaning_tab'] = self.cleaning_tab_widget.get_config()

            # ---- 数据分析模块 ----
            # 保存传感器计算结果（从 SSOT 读取）
            serializable_results = {}
            for key, values in self.state.analysis_tab.sensor_results.items():
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
            result_count = len(self.state.analysis_tab.sensor_results)
            QMessageBox.information(self, '成功',
                f'配置已保存到:\n{file_path}\n\n'
                f'  FBG: {len(config["fbgs"])} 个\n'
                f'  传感器: {len(config["sensors"])} 个\n'
                f'  全局参数: {len(config["global_parameters"])} 个\n'
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

            fbg_count, sensor_count = 0, 0
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

            # 加载全局参数 (SSOT)，旧配置自动迁移
            if 'global_parameters' in config:
                raw = config['global_parameters']
                self.sensor_system.global_parameters = raw
                # 同步到 AppState
                restored_gp = {}
                for name, entry in raw.items():
                    restored_gp[name] = GlobalParameter(
                        name=name,
                        value=entry.get('value', 0) if isinstance(entry, dict) else float(entry),
                        unit=entry.get('unit', '') if isinstance(entry, dict) else '',
                        description=entry.get('description', '') if isinstance(entry, dict) else '',
                    )
                self.state = self.state.with_global_parameters(restored_gp)
            else:
                # 自动迁移: 扫描所有传感器，提取同名同值的共用常量
                const_usage = {}  # name -> [(value, sensor_id), ...]
                for s in config.get('sensors', []):
                    for name, value in s.get('constants', {}).items():
                        const_usage.setdefault(name, []).append((value, s['id']))
                migrated = {}
                for name, entries in const_usage.items():
                    if len(entries) >= 2:
                        values = [v for v, _ in entries]
                        if len(set(values)) == 1:
                            migrated[name] = values[0]
                if migrated:
                    print(f"[迁移] 从传感器局部常量提取全局参数: {migrated}")
                self.sensor_system.global_parameters = migrated
                # 迁移结果同步到 AppState
                restored_gp = {}
                for name, value in migrated.items():
                    restored_gp[name] = GlobalParameter(
                        name=name, value=value if isinstance(value, (int, float)) else 0,
                        unit='', description='',
                    )
                self.state = self.state.with_global_parameters(restored_gp)

            for sensor_data in config.get('sensors', []):
                sensor_constants = sensor_data.get('constants', {})
                # 从局部常量中剔除已迁移到全局的参数
                if self.sensor_system.global_parameters:
                    sensor_constants = {
                        k: v for k, v in sensor_constants.items()
                        if k not in self.sensor_system.global_parameters
                    }
                sensor = Sensor(
                    sensor_data['id'],
                    sensor_data['sensor_type'],
                    sensor_data.get('formula'),
                    sensor_constants,
                    sensor_data.get('active', True),
                    sensor_data.get('decoupling_config'),
                    sensor_data.get('location', ''),
                )
                self.sensor_system.add_sensor(sensor)
                sensor_count += 1

            self.sensor_tab_widget.set_fbg_list(self.sensor_system.fbgs)
            self.sensor_tab_widget.set_sensor_list(self.sensor_system.sensors)
            messages.append(f'FBG: {fbg_count} 个, 传感器: {sensor_count} 个')

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
                    self._insert_annotation_row_if_timestamp_exists()
                    self.update_data_table()
                    if fbg_count == 0:
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
                    self.cleaning_tab_widget.set_config(cleaning_tab)
                    cleaning_restored = True
                    messages.append('清洗规则: 已恢复')
                except Exception as e:
                    messages.append(f'清洗规则恢复失败: {str(e)}')

            # 数据文件和清洗规则都恢复后，自动应用清洗
            if data_loaded and cleaning_restored:
                try:
                    self.apply_cleaning(silent=True)
                    messages.append('数据清洗: 已自动应用')
                except Exception:
                    pass

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

                    # 恢复传感器计算结果 (同步到 SSOT)
                    saved_results = analysis_tab.get('sensor_results', {})
                    if saved_results:
                        restored_results = {}
                        for key, values in saved_results.items():
                            restored_results[key] = [np.nan if v is None else v for v in values]
                        self.sensor_results = restored_results
                        self.state = self.state.with_analysis_results(restored_results)
                        if self.current_data is not None:
                            analysis_df, _, _ = self._get_analysis_data()
                            self.sensor_tab_widget.set_result_preview(restored_results, analysis_df)
                        analysis_restored = True
                        messages.append(f'分析结果: {len(restored_results)} 个列')
                    else:
                        self.sensor_results = {}
                        self.state = self.state.with_analysis_results({})
                except Exception as e:
                    messages.append(f'分析状态恢复失败: {str(e)}')
                    self.sensor_results = {}
                    self.state = self.state.with_analysis_results({})
            else:
                self.sensor_results = {}

            # 更新分析页面传感器列表
            if hasattr(self, 'analysis_tab_widget'):
                self.refresh_analysis_sensors()

            # ---- 切换到数据文件页展示结果 ----
            if data_loaded:
                self.central_widget.setCurrentWidget(self.data_tab_widget)
            else:
                self.central_widget.setCurrentWidget(self.sensor_tab_widget)

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

        self.sensor_tab_widget.set_fbg_list([])
        self.sensor_tab_widget.set_sensor_list([])
        self.load_project_list()

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
                # 其它数据：自定义模板（非光纤格式）
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

        # 记录用户选择的数据类型
        selected_file_type = self.file_type_combo.currentText()

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
                self._insert_annotation_row_if_timestamp_exists()
                self.update_data_table()
                if selected_file_type == '光纤光栅数据':
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
            self._insert_annotation_row_if_timestamp_exists()
            self.update_data_table()
            if selected_file_type == '光纤光栅数据':
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
        """读取文件的格式头行，最多读取前500行以定位数据起始行"""
        try:
            for enc in ('utf-8', 'gbk', 'latin-1'):
                try:
                    with open(file_path, 'r', encoding=enc) as f:
                        head_lines = []
                        for _ in range(500):
                            line = f.readline()
                            if not line:
                                break
                            head_lines.append(line)
                    break
                except UnicodeDecodeError:
                    continue

            data_start = skip_rows
            for i, line in enumerate(head_lines):
                if 'Timestamp' in line and ('# CH' in line or 'CH' in line):
                    data_start = i + 1
                    break

            self.file_header_lines = head_lines[:data_start] if data_start > 0 else []
        except Exception:
            self.file_header_lines = []

    def clear_data(self):
        self.current_data = None
        self.current_columns = []
        self.sampled_data = None
        self.file_header_lines = []
        self.sampled_file_path = None
        self.current_template = None
        self.data_tab_widget.update_data_table(None)
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

    MAX_DISPLAY_ROWS = 1000

    def _on_analysis_refresh_requested(self):
        """分析页点击"刷新"时：重新从暗号标注获取分析数据并推送到列选择列表。"""
        analysis_df, _, annotated_cols = self._get_analysis_data()
        if analysis_df is not None and not analysis_df.empty:
            # 有 FBG 传感器时保留真实计算结果，不覆盖
            has_fbg = bool(self.sensor_system.fbgs)
            if annotated_cols and not has_fbg:
                self._push_annotation_sensor_results(analysis_df, annotated_cols)
            elif not has_fbg:
                self.analysis_tab_widget.set_sensor_results({})
            self.analysis_tab_widget.set_current_data(analysis_df, annotated_cols)

    def _push_annotation_sensor_results(self, analysis_df, annotated_cols):
        """将标注数据列转换为 sensor_results 格式，供通用数据使用。"""
        sensor_results = {}
        for col in annotated_cols:
            if col in analysis_df.columns and pd.api.types.is_numeric_dtype(analysis_df[col]):
                sensor_results[col] = analysis_df[col].tolist()
        if sensor_results:
            self.analysis_tab_widget.set_sensor_results(sensor_results)

    def update_data_table(self):
        if self.current_data is None:
            self.data_tab_widget.update_data_table(None)
            return
        # 数据表格始终显示完整数据（含暗号行）
        self.data_tab_widget.update_data_table(self.current_data, self.MAX_DISPLAY_ROWS)
        # 分析模块接收清洗后的数据（跳过暗号行 + 重命名列）并填充列选择列表
        if hasattr(self, 'analysis_tab_widget'):
            analysis_df, _, annotated_cols = self._get_analysis_data()
            # 有 FBG 传感器时保留真实计算结果，不覆盖（通用数据才推标注列）
            has_fbg = bool(self.sensor_system.fbgs)
            if annotated_cols and not has_fbg:
                self._push_annotation_sensor_results(analysis_df, annotated_cols)
            elif not has_fbg:
                self.analysis_tab_widget.set_sensor_results({})
            self.analysis_tab_widget.set_current_data(analysis_df, annotated_cols)

    # ══════════════════════════════════════════════════════════
    # 暗号行自动插入（数据加载时触发）
    # ══════════════════════════════════════════════════════════

    def _insert_annotation_row_if_timestamp_exists(self):
        """数据加载后在首行插入空白备注行，供用户填写暗号。

        规则：
          - 如果数据中已有包含'时间戳'的暗号行 → 不做任何操作
          - 否则 → 在第 0 行插入一行空白备注行
          - 如果当前使用了有效模板，自动根据模板列定义填充暗号
        """
        try:
            if self.current_data is None or self.current_data.empty:
                return
            df = self.current_data
            # 先检查是否已有暗号行（扫描前100行，包含'时间戳'的标记行）
            for idx in range(min(100, len(df))):
                for val in df.iloc[idx]:
                    s = str(val).strip().strip("'\"'\"'\"")
                    if '时间戳' in s:
                        # 已有暗号行，不再重复插入
                        return
            # 没有暗号行 → 在第 0 行插入空白行
            # 用 np.nan 而非 '' 防止 numeric 列被污染为 object dtype
            self._annotation_orig_dtypes = df.dtypes.to_dict()
            blank_vals = [np.nan] * len(df.columns)
            blank_row = pd.DataFrame([blank_vals], columns=df.columns)
            self.current_data = pd.concat(
                [blank_row, df],
                ignore_index=True,
            )
            # 恢复原始 dtypes（整数列含 NaN → 降级为 float64）
            for col, dtype in self._annotation_orig_dtypes.items():
                try:
                    self.current_data[col] = self.current_data[col].astype(dtype)
                except (ValueError, TypeError):
                    if 'int' in str(dtype):
                        self.current_data[col] = self.current_data[col].astype('float64')
                    # 其他无法恢复的类型保持 pd.concat 自动推断的结果

            # ── 智能模板继承：如果有模板列定义，自动填充暗号行 ──
            template = getattr(self, 'current_template', None)
            if template and hasattr(template, 'columns') and template.columns:
                for col_idx, tc in enumerate(template.columns):
                    if col_idx >= len(self.current_data.columns):
                        break
                    data_type = tc.get('data_type', '').strip().lower()
                    if data_type == 'time':
                        col_name = self.current_data.columns[col_idx]
                        self.current_data[col_name] = self.current_data[col_name].astype(object)
                        self.current_data.iloc[0, col_idx] = "'时间戳'"
                    elif data_type and data_type != 'none':
                        ann_text = tc.get('comment', '').strip() or tc.get('name', '').strip()
                        if ann_text:
                            col_name = self.current_data.columns[col_idx]
                            self.current_data[col_name] = self.current_data[col_name].astype(object)
                            self.current_data.iloc[0, col_idx] = f"'{ann_text}'"
        except Exception as e:
            print(f'[暗号行插入失败] {e}')

    # ══════════════════════════════════════════════════════════
    # 表格编辑双向同步（用户编辑 UI → 写回 self.current_data）
    # ══════════════════════════════════════════════════════════

    def _on_data_table_cell_edited(self, row: int, col: int, text: str):
        """用户在数据表格编辑单元格后，将新值写回底层 DataFrame。"""
        if self.current_data is None or not (0 <= row < len(self.current_data)):
            return
        try:
            self.current_data.iat[row, col] = text
        except (ValueError, TypeError):
            # 字符串写入 numeric 列 → 将该列转为 object 再写入
            col_name = self.current_data.columns[col]
            self.current_data[col_name] = self.current_data[col_name].astype(object)
            self.current_data.iat[row, col] = text
        except Exception as e:
            print(f'[表格回写失败] row={row}, col={col}: {e}')

    # ══════════════════════════════════════════════════════════
    # 被动暗号标注识别（通用工具方法）
    # ══════════════════════════════════════════════════════════

    def get_annotated_columns(self):
        """扫描 current_data 寻找暗号行，被动识别列角色。

        查找包含'时间戳'的标记行，解析每列的含义：
          - '时间戳' → 时间列
          - '温度'、"应力"等引号包裹 → 数据列，引号内为自定义列名
          - 无特殊标记 → 无关列（丢弃）

        Returns:
            (time_col_idx, data_cols, signal_row_idx)
            - time_col_idx:  时间戳列的整数索引，None 表示未找到
            - data_cols:     {列索引: 自定义列名} 字典
            - signal_row_idx: 暗号行在 DataFrame 中的行号，None 表示未找到
        """
        import re as _re

        if self.current_data is None or self.current_data.empty:
            return None, {}, None

        df = self.current_data
        signal_row_idx = None

        # ── 1. 定位暗号行 ──
        for idx in range(len(df)):
            for val in df.iloc[idx]:
                s = str(val).strip().strip("'"'"'"")
                if '时间戳' in s:
                    signal_row_idx = idx
                    break
            if signal_row_idx is not None:
                break

        if signal_row_idx is None:
            return None, {}, None

        # ── 2. 解析暗号行的列含义 ──
        signal_row = df.iloc[signal_row_idx]
        QUOTED_RE = _re.compile(r"^[\'\"‘’“”](.*?)[\'\"‘’“”]$")
        time_col_idx = None
        data_cols: dict = {}

        for j in range(len(df.columns)):
            col_name = df.columns[j]
            val = str(signal_row[col_name]).strip()
            cleaned = val.strip("'\"‘’“”").strip()

            if '时间戳' in cleaned:
                time_col_idx = j

            elif QUOTED_RE.match(val):
                m = QUOTED_RE.match(val)
                custom_name = m.group(1).strip()
                data_cols[j] = custom_name

        return time_col_idx, data_cols, signal_row_idx

    # ══════════════════════════════════════════════════════════
    # 根据暗号标注获取清洗后的分析数据
    # ══════════════════════════════════════════════════════════

    def _get_analysis_data(self):
        """如果存在暗号行，返回跳过暗号行及之前行、并重命名列的数据。

        Returns:
            (cleaned_df, time_col_name) — 无暗号时返回 (self.current_data, '时间')
        """
        time_col_idx, data_cols, signal_row_idx = self.get_annotated_columns()

        if signal_row_idx is None or self.current_data is None:
            return self.current_data, '时间', []

        df = self.current_data
        # 跳过暗号行及之前的所有行
        data_df = df.iloc[signal_row_idx + 1:].copy()
        data_df.reset_index(drop=True, inplace=True)

        # 恢复数值列 dtype：注解行（插入或文件自带）可能已将列污染为 object
        # 对每个 object 列尝试 to_numeric，若 80%+ 非空值可转换则保留
        for col in data_df.columns:
            if data_df[col].dtype == object:
                converted = pd.to_numeric(data_df[col], errors='coerce')
                before = data_df[col].notna().sum()
                after = converted.notna().sum()
                if before > 0 and after / before >= 0.8:
                    data_df[col] = converted

        # 确定时间列名
        if time_col_idx is not None:
            time_col_name = str(df.columns[time_col_idx])
        else:
            time_col_name = '时间'
            if time_col_name not in data_df.columns and len(data_df.columns) > 0:
                time_col_name = str(data_df.columns[0])

        # 重命名数据列（如果用户给了自定义名称）
        rename_map = {}
        if data_cols:
            for col_idx, custom_name in data_cols.items():
                if col_idx < len(df.columns):
                    rename_map[df.columns[col_idx]] = custom_name
        # 将时间列统一命名为 '时间'，方便下游（run_analysis）识别
        if time_col_idx is not None:
            orig_time_name = str(df.columns[time_col_idx])
            if orig_time_name not in rename_map:  # 未被用户重命名
                rename_map[orig_time_name] = '时间'
                time_col_name = '时间'
            else:
                time_col_name = rename_map[orig_time_name]
        if rename_map:
            data_df = data_df.rename(columns=rename_map)

        # 返回有暗号标注的列名集合，用于分析页筛选列选择列表
        # 使用 list 保持列在数据文件中的先后顺序
        annotated_cols = list(data_cols.values()) if data_cols else []

        return data_df, time_col_name, annotated_cols


    # ============ Cleaning Operations ============

    def apply_cleaning(self, silent=False, config=None):
        if self.current_data is None:
            if not silent:
                QMessageBox.warning(self, '警告', '请先加载数据')
            return False

        if config is None:
            config = self.cleaning_tab_widget.get_config()

        rules = []

        if config.get('adjacent_enabled'):
            rules.append(CleaningRule(
                name='相邻差值',
                rule_type='adjacent_diff',
                enabled=True,
                threshold=config.get('diff_threshold', 1.0),
                fill_method=config.get('fill_method', 'linear'),
                fill_value=config.get('fill_value', 0) if config.get('fill_method') == 'custom' else None,
            ))

        if config.get('nan_enabled'):
            rules.append(CleaningRule(
                name='缺失值',
                rule_type='nan',
                enabled=True,
                fill_method=config.get('fill_method', 'linear'),
                fill_value=config.get('fill_value', 0) if config.get('fill_method') == 'custom' else None,
            ))

        try:
            col_pattern = None
            if self.current_template:
                fmt = getattr(self.current_template, 'file_format', '')
                if fmt == 'enlight':
                    col_pattern = r'^波长'
                elif fmt == 'fiber_custom':
                    col_pattern = r'^FBG'
            df = clean_data(self.current_data, rules, column_pattern=col_pattern)
            self.current_data = df
            self.update_data_table()

            anomaly_cols = [col for col in df.columns if col.endswith('_anomaly')]
            total_anomalies = sum(df[col].sum() for col in anomaly_cols)

            self.cleaning_tab_widget.set_result_text(
                f'检测到 {total_anomalies} 个异常数据点\n'
                f'异常列: {[col.replace("_anomaly", "") for col in anomaly_cols]}'
            )

            if not silent:
                self.status_bar.showMessage(f'数据清洗完成，发现 {total_anomalies} 个异常')
            return True

        except Exception as e:
            import traceback
            if not silent:
                QMessageBox.critical(self, '错误', f'清洗失败: {str(e)}\n\n{traceback.format_exc()}')
            return False

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
        self.central_widget.setCurrentIndex(5)  # Switch to report tab
        QMessageBox.information(self, '提示', '请在成果输出与报告页面选择相应功能生成报告')

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