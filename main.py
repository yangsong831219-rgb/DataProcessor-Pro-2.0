# DataProcessor Pro - Main Application
# PyQt6-based offline data analysis software

import sys
import os
import json
import re
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
from ui.fbg_edit_dialog import FBGEditDialog
from ui.sensor_edit_dialog import SensorEditDialog
from ui.ai_model_config_dialog import AIModelConfigDialog
from ui.report_worker import ReportWorker
from ui.skill_tab import AgentSkillWidget
from ui.compare_tab import CompareTabWidget
from ui.calibration_tab import CalibrationTabWidget

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

        # 传感器列表初始为空，由用户通过 UI 添加
        # （旧默认传感器 '应变1'/'温度1' 已移除，避免干扰用户配置）

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

        # Calibration tab (传感器标定)
        self.calibration_tab_widget = CalibrationTabWidget()
        self.central_widget.addTab(self.calibration_tab_widget, '传感器标定')

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
        """从数据文件列名自动识别FBG传感器并填充FBG定义表。

        检测逻辑委托给 utils.column_utils.detect_fbg_columns。
        """
        from utils.column_utils import detect_fbg_columns

        fbg_cols = detect_fbg_columns(df)
        if not fbg_cols:
            return 0

        self.sensor_system.fbgs.clear()
        for i, col in enumerate(fbg_cols):
            self.sensor_system.add_fbg(FBG(f'W{i+1}', col, 1520, 1590))

        self.sensor_tab_widget.set_fbg_list(self.sensor_system.fbgs)
        print(f"[FBG] 注册 {len(fbg_cols)} 个: {[(f.id, f.channel) for f in self.sensor_system.fbgs]}")
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
                cleaned = self._clean_sensor_keys(results)
                self.sensor_results = cleaned
                self.state = self.state.with_analysis_results(cleaned)
                self.sensor_tab_widget.set_result_preview(cleaned, analysis_df)
                self.analysis_tab_widget.set_sensor_results(cleaned)
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
            cleaned = self._clean_sensor_keys(results)

            n_preview_rows = min(100, len(analysis_df))
            self.sensor_tab_widget.set_result_preview(cleaned, analysis_df)
            self.sensor_results = cleaned
            self.state = self.state.with_analysis_results(cleaned)
            self.analysis_tab_widget.set_sensor_results(cleaned)
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
                cleaned = self._clean_sensor_keys(results)

                export_df = pd.DataFrame()
                export_df[time_col] = analysis_df[time_col]
                for sensor_id, values in cleaned.items():
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
                cleaned = self._clean_sensor_keys(results)

                export_df = pd.DataFrame()
                export_df[time_col] = analysis_df[time_col]
                for sensor_id, values in cleaned.items():
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
        self.report_workbench_widget.load_diagnosis_requested.connect(
            self._handle_load_diagnosis
        )
        return self.report_workbench_widget

    # ═══════════════════════════════════════════════
    # 后台 Worker (QThread)
    # ═══════════════════════════════════════════════


    # ═══════════════════════════════════════════════
    # 大纲生成 — 后台 AI 调用
    # ═══════════════════════════════════════════════

    def _get_generate_fn(self):
        """获取 LLM 生成回调 — 统一入口 AIClient (按 backend 路由 online/local).

        AIClient 不可用时返回 mock 降级（明确提示"未配置"）。
        """
        from core.ai_client import AIClient
        ai = AIClient.get_instance()
        if ai.is_available():
            return ai.get_generate_fn(enable_thinking=False)

        # 降级：无 AI 配置时返回 mock 函数（用户在 UI 看到模板内容）
        print("[主窗口] AI 未配置，大纲/报告将使用模拟降级")
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

        self._outline_worker = ReportWorker(
            generate_outline,
            args=(config, generate_fn),
        )
        self._outline_worker.finished.connect(_on_outline_done)
        self._outline_worker.error.connect(_on_outline_error)
        self._outline_worker.start()

    # ═══════════════════════════════════════════════
    # 从已存诊断加载 — 浏览 diagnoses/ 目录
    # ═══════════════════════════════════════════════

    def _handle_load_diagnosis(self) -> None:
        """弹出对话框，列出 wiki_vault/diagnoses/ 中的诊断 JSON，用户选择后加载。"""
        from PyQt6.QtWidgets import QDialog, QVBoxLayout, QListWidget, QPushButton, QHBoxLayout, QLabel
        from py.wiki_system import WikiFileSystem

        wiki = WikiFileSystem()
        diags = wiki.list_diagnoses()
        if not diags:
            QMessageBox.information(self, '提示', '项目资料库中尚无已存诊断结果。\n请先在 AI 诊断页运行诊断并保存。')
            return

        dlg = QDialog(self)
        dlg.setWindowTitle('从已存诊断加载')
        dlg.setMinimumWidth(600)
        layout = QVBoxLayout(dlg)

        layout.addWidget(QLabel('选择一份已保存的诊断结果:'))

        lst = QListWidget()
        for d in diags:
            item_text = f"{d['name']}  ({d['size']//1024}KB)"
            lst.addItem(item_text)
        layout.addWidget(lst)

        btn_row = QHBoxLayout()
        load_btn = QPushButton('加载')
        load_btn.setStyleSheet(
            'QPushButton { background: #722ed1; color: white; padding: 8px 16px; '
            'border-radius: 4px; font-weight: bold; }'
        )
        cancel_btn = QPushButton('取消')

        def on_load():
            row = lst.currentRow()
            if row < 0 or row >= len(diags):
                return
            rec = wiki.read_diagnosis(diags[row]['name'])
            if rec:
                sv = str(rec.get('schema_version', '1.0'))
                if sv not in ('1.0', '1.1'):
                    QMessageBox.warning(self, '版本不兼容',
                        f"该记录 schema 版本为 {sv}，当前只支持 1.0 / 1.1")
                    return
                self.report_workbench_widget.set_diagnosis_record(rec)
                # 兼容 1.0 / 1.1 两种 schema
                ai_d = rec.get('ai_diagnosis', {}).get('diagnosis_json') or rec.get('diagnosis_json') or {}
                sensors = len(ai_d.get('sensor_analysis', []) if isinstance(ai_d, dict) else [])
                kb_count = len(rec.get('kb_hits', []))
                ma_present = '有' if rec.get('multi_agent', {}).get('report') else '无'
                QMessageBox.information(self, '成功',
                    f"已加载诊断记录 v{sv}\n时间: {rec.get('timestamp')}\n"
                    f"传感器数: {sensors}\n"
                    f"KB 规则: {kb_count} 条\n"
                    f"多智能体报告: {ma_present}")
                dlg.accept()

        load_btn.clicked.connect(on_load)
        cancel_btn.clicked.connect(dlg.reject)
        btn_row.addWidget(load_btn)
        btn_row.addWidget(cancel_btn)
        layout.addLayout(btn_row)
        dlg.exec()

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

        self._report_worker = ReportWorker(_build_and_save)
        self._report_worker.finished.connect(_on_report_done)
        self._report_worker.error.connect(_on_report_error)
        self._report_worker.start()

    def create_ai_diagnosis_page(self):
        """AI诊断页面"""
        self.ai_diagnosis_widget = AiDiagnosisWidget(self)
        return self.ai_diagnosis_widget

    # ═══════════════════════════════════════════════════════════
    # 项目资料库 — 三级目录 (Phase 3)
    # ═══════════════════════════════════════════════════════════

    LIBRARY_FOLDER_NAME = "项目资料库"

    @staticmethod
    def _get_project_library_dir(projects_dir: str) -> str:
        """返回项目资料库一级容器路径: <projects_dir>/项目资料库/。"""
        return os.path.join(projects_dir, DataProcessorWindow.LIBRARY_FOLDER_NAME)

    @staticmethod
    def get_software_root_dir() -> str:
        """软件根目录 (main.py 所在目录)。"""
        return os.path.dirname(os.path.abspath(__file__))

    def get_project_library_dir(self) -> str:
        """SSOT: 返回实际存在的项目资料库路径。

        确定性：根植软件根目录/<项目资料库>/。与项目资料管理页同一解析。
        不存在时创建并返回。
        """
        root = DataProcessorWindow.get_software_root_dir()
        lib_dir = os.path.join(root, DataProcessorWindow.LIBRARY_FOLDER_NAME)
        os.makedirs(lib_dir, exist_ok=True)
        return lib_dir

    def _ensure_library_exists(self, projects_dir: str) -> str:
        """确保项目资料库目录存在，返回其路径。"""
        lib_dir = self._get_project_library_dir(projects_dir)
        os.makedirs(lib_dir, exist_ok=True)
        return lib_dir

    def _migrate_projects_to_library(self, projects_dir: str, known_projects: list[str]) -> int:
        """将 <projects_dir>/ 根目录下已登记的项目迁移到 项目资料库/ 下。

        幂等: 已在库下的跳过；未登记的文件夹不动（避免误伤）。
        返回迁移数量。
        """
        import shutil
        lib_dir = self._ensure_library_exists(projects_dir)
        migrated = 0
        for proj_name in known_projects:
            old_path = os.path.join(projects_dir, proj_name)
            new_path = os.path.join(lib_dir, proj_name)
            if not os.path.isdir(old_path):
                continue  # 项目不在旧位置
            if os.path.exists(new_path):
                continue  # 已在库下，跳过
            try:
                shutil.move(old_path, new_path)
                migrated += 1
            except Exception as e:
                print(f"迁移项目 '{proj_name}' 失败: {e}")
        return migrated

    def load_project_list(self):
        """加载项目列表 (从软件根/项目资料库/ 扫描)。"""
        lib_dir = self.get_project_library_dir()
        items: list[str] = []
        if os.path.isdir(lib_dir):
            try:
                for entry in os.listdir(lib_dir):
                    if os.path.isdir(os.path.join(lib_dir, entry)):
                        items.append(entry)
            except Exception as e:
                print(f"加载项目列表失败: {e}")
        self.project_tab_widget.set_project_list(items)

    def on_project_selected(self, project_name: str):
        """选择项目时加载内容"""
        lib_dir = self.get_project_library_dir()
        self.current_project_path = os.path.join(lib_dir, project_name)
        self.project_tab_widget.set_current_project_label(project_name, exists=True)
        self.project_tab_widget.load_project_tree(self.current_project_path)

    def on_new_project(self):
        """新建项目 — 直接在软件根/项目资料库/下创建。"""
        # 输入项目名称
        project_name, ok = QInputDialog.getText(self, '新建项目', '请输入项目名称:')
        if not ok or not project_name.strip():
            return

        project_name = project_name.strip()
        lib_dir = self.get_project_library_dir()
        project_path = os.path.join(lib_dir, project_name)

        if os.path.exists(project_path):
            QMessageBox.warning(self, '警告', '项目已存在!')
            return

        try:
            # 创建项目文件夹 (二级目录)
            os.makedirs(project_path)

            # 创建默认子文件夹 (三级目录: 图片/图纸/数据/方案/总结/视频/其它)
            default_folders = ['图片', '图纸', '数据', '方案', '总结', '视频', '其它']
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

        try:
            import shutil
            lib_dir = self.get_project_library_dir()
            shutil.rmtree(os.path.join(lib_dir, project_name))
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
        lib_dir = self.get_project_library_dir()
        project_path = os.path.join(lib_dir, project_name)
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
        """自动检测数据格式并创建模板。委托给 utils.file_parser。"""
        from utils.file_parser import auto_detect_template as _adt
        return _adt(file_path)
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
        """判断一行数据是真实数据还是表头。委托给 utils.column_utils.is_data_row。"""
        from utils.column_utils import is_data_row
        return is_data_row(parts)

    def _load_file_header_lines(self, file_path, skip_rows):
        """读取文件的格式头行。委托给 utils.file_parser。"""
        from utils.file_parser import read_file_header_lines

        self.file_header_lines = read_file_header_lines(file_path, skip_rows)
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
        """找到第一个包含有效时间戳的数据行索引。委托给 utils.dataframe_utils。"""
        from utils.dataframe_utils import find_first_timestamp_row
        return find_first_timestamp_row(df)

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
        """持久化自定义模板到JSON文件。委托给 utils.file_parser.persist_custom_template。"""
        from utils.file_parser import persist_custom_template

        templates_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "templates")
        persist_custom_template(template, templates_dir)

    @staticmethod
    def load_custom_templates():
        """启动时加载持久化的自定义模板。委托给 utils.file_parser.load_custom_templates。"""
        from utils.file_parser import load_custom_templates as _load

        templates_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "templates")
        _load(templates_dir, DEFAULT_TEMPLATES, DataTemplate)

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
        """将标注数据列转换为 sensor_results 格式。委托给 utils.annotation_utils。"""
        from utils.annotation_utils import build_sensor_results_dict

        sensor_results = build_sensor_results_dict(analysis_df, annotated_cols)
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
        """数据加载后在首行插入暗号备注行，供用户填写/确认暗号。

        规则：
          - 已有暗号行（含'时间戳'）→ 不插入
          - 新暗号行按列类型填充：
            · Timestamp → '时间戳'
            · 波长列 → 'wN-类型-位置'
            · 其它列 → 留空
          - 暗号行逐列对齐 df.columns，杜绝错位
        """
        try:
            from utils.annotation_utils import (
                is_wave_col,
                build_annotation_row,
                insert_blank_row,
                apply_annotation_row,
            )

            if self.current_data is None or self.current_data.empty:
                return
            df = self.current_data

            # ── 1. 检测是否已有暗号行 ──
            existing_signal_row = None
            for idx in range(min(100, len(df))):
                for val in df.iloc[idx]:
                    s = str(val).strip().strip("'\"'\"'\"")
                    if '时间戳' in s:
                        existing_signal_row = idx
                        break
                if existing_signal_row is not None:
                    break

            template = getattr(self, 'current_template', None)
            file_fmt = getattr(template, 'file_format', '') if template else ''

            # ── 2. 无暗号行 → 插入空白行 ──
            if existing_signal_row is None:
                new_df, dtypes = insert_blank_row(df)
                self._annotation_orig_dtypes = dtypes
                self.current_data = new_df
                annotation_row = 0
            else:
                annotation_row = existing_signal_row

            # ── 3. 暗号行填充 — 委托给 annotation_utils ──
            data_row = annotation_row + 1
            if data_row >= len(self.current_data):
                return

            ann = build_annotation_row(
                self.current_data.iloc[data_row:], file_format=file_fmt,
            )
            apply_annotation_row(
                self.current_data, ann, annotation_row, self._annotation_orig_dtypes,
            )
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
        from utils.annotation_utils import extract_annotation_info

        if self.current_data is None or self.current_data.empty:
            return None, {}, None
        return extract_annotation_info(self.current_data)

    def is_fiber_data(self) -> bool:
        """公开 getter：判定当前数据是否为光纤光栅数据。

        消除 ai_diagnosis 与 analysis_tab 的重复判定逻辑。
        列名匹配 FBG/ENLIGHT/光纤传感/W\d+ 或模板名含"光纤"/"ENLIGHT"。
        """
        import re as _re
        if self.current_data is None:
            return False
        for c in self.current_data.columns:
            name = str(c)
            if 'FBG' in name or 'ENLIG' in name or '光纤传感' in name:
                return True
            if _re.match(r'^W\d+$', name):
                return True
        if self.current_template and hasattr(self.current_template, 'name'):
            tname = str(self.current_template.name)
            if '光纤' in tname or 'ENLIGHT' in tname:
                return True
        return False

    def get_timestamp_column(self) -> str | None:
        """返回检测到的时间戳列名，或 None。

        优先：detector/template 记录的 timestamp_col；
        否则启发式：datetime 类型 → 列名含时间/time/timestamp/(h)。
        """
        import pandas as pd
        if self.current_data is None:
            return None
        # 1. 优先从 template/detector 读
        if self.current_template and hasattr(self.current_template, 'timestamp_col'):
            tc = getattr(self.current_template, 'timestamp_col', None)
            if tc and str(tc) in self.current_data.columns:
                return str(tc)
        # 2. datetime 类型
        for c in self.current_data.columns:
            if pd.api.types.is_datetime64_any_dtype(self.current_data[c]):
                return str(c)
        # 3. 列名匹配
        for c in self.current_data.columns:
            cn = str(c).lower()
            if any(kw in cn for kw in ('时间', 'time', 'timestamp', '(h)')):
                return str(c)
        return None

    # ══════════════════════════════════════════════════════════
    # 根据暗号标注获取清洗后的分析数据
    # ══════════════════════════════════════════════════════════

    def _get_analysis_data(self):
        """如果存在暗号行，返回跳过暗号行及之前行、并重命名列的数据。

        Returns:
            (cleaned_df, time_col_name) — 无暗号时返回 (self.current_data, '时间')
        """
        from utils.annotation_utils import get_analysis_data

        if self.current_data is None:
            return self.current_data, "时间", []
        return get_analysis_data(self.current_data)

    @staticmethod
    def _clean_sensor_keys(results: dict) -> dict:
        """剥离结果字典键中的中英文括号及单位后缀。委托给 utils.column_utils.clean_dict_keys。"""
        from utils.column_utils import clean_dict_keys
        return clean_dict_keys(results)

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

            # ── 落盘结构化 anomaly_info 到 SSOT（供 AI 诊断/报告读取） ──
            struct_anomaly: dict[str, dict] = {}
            for ac in anomaly_cols:
                count = int(df[ac].sum())
                if count > 0:
                    orig_col = str(ac).replace('_anomaly', '')
                    indices = df.index[df[ac] == True].tolist()
                    struct_anomaly[orig_col] = {
                        'count': count,
                        'indices': indices[:20],
                        'total_indices': len(indices),
                    }
            self._anomaly_info = struct_anomaly
            self.cleaning_tab_widget._anomaly_info = struct_anomaly
            self.cleaning_tab_widget._cleaning_has_run = True  # 独立标志

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