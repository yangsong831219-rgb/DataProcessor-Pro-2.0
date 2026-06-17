"""AI 诊断页面 — 从 main.py DataProcessorWindow 抽离的独立 QWidget

包含：模型管理、流式推理、多智能体诊断、禅模式、AI 聊天
通过 parent() 链访问主窗口的 ollama_client 等共享资源。
"""

from __future__ import annotations

import json
import os
import re
import time
from datetime import datetime
from typing import Any, Dict, List, Optional

from PyQt6.QtCore import Qt, QThread, pyqtSignal, pyqtSlot
from PyQt6.QtWidgets import (
    QApplication, QComboBox, QDialog, QFileDialog, QFrame,
    QGroupBox, QHBoxLayout, QLabel, QLineEdit, QListWidget,
    QMessageBox, QPushButton, QSplitter, QStackedWidget,
    QTextEdit, QVBoxLayout, QWidget,
)


class AiDiagnosisWidget(QWidget):
    """AI 诊断页面 — 40:60 双栏仪表盘布局

    通过 parent() 链自动查找主窗口的 ollama_client、online_llm_config 等。
    """

    # Signals
    status_changed = pyqtSignal(str, str)  # (text, color)
    diagnosis_complete = pyqtSignal(str)   # result text

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.ai_proj_files: List[str] = []
        self.ai_req_files: List[str] = []
        self._ai_models_config: Dict[str, dict] = {}
        self._online_thread: Optional[QThread] = None
        self._multi_report: str = ""
        self._diagnosis_json_response: dict | None = None
        self._diagnosis_raw_response: str = ""
        self._build_ui()

    # ═══════════════════════════════════════════════
    # UI 构造
    # ═══════════════════════════════════════════════

    def _build_ui(self) -> None:
        self.setStyleSheet("background-color: #f0f2f5;")
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(20, 20, 20, 20)
        main_layout.setSpacing(14)

        main_layout.addWidget(self._build_header())

        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.setHandleWidth(8)
        splitter.setStyleSheet("""
            QSplitter::handle { background: linear-gradient(to bottom, #e0e0e0, #c0c0c0); }
            QSplitter::handle:hover { background: linear-gradient(to bottom, #1890ff, #40a9ff); }
        """)
        splitter.addWidget(self._build_left_panel())
        splitter.addWidget(self._build_right_panel())
        splitter.setStretchFactor(0, 40)
        splitter.setStretchFactor(1, 60)
        main_layout.addWidget(splitter)

        # 加载模型配置
        self._load_models_config()

    def _build_header(self) -> QWidget:
        w = QWidget()
        layout = QHBoxLayout(w)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(16)

        title = QLabel("AI 数据诊断中心")
        title.setStyleSheet("font-size: 20px; font-weight: bold; color: #333;")
        layout.addWidget(title)
        layout.addStretch()

        self.ai_status_label = QLabel("● 未连接")
        self.ai_status_label.setStyleSheet(
            "color: #ff4d4f; font-weight: bold; font-size: 13px; "
            "padding: 6px 14px; background: #fff1f0; border-radius: 4px;"
        )
        layout.addWidget(self.ai_status_label)

        self.ai_latency_label = QLabel("延迟: -- ms")
        self.ai_latency_label.setStyleSheet(
            "color: #666; font-size: 12px; padding: 6px 14px; "
            "background: #f5f5f5; border-radius: 4px;"
        )
        layout.addWidget(self.ai_latency_label)
        return w

    def _build_left_panel(self) -> QWidget:
        panel = QWidget()
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(12)

        # ── 项目资料区 ──
        layout.addWidget(self._make_file_group(
            "项目资料", "#722ed1", self.ai_proj_files,
            self._on_add_proj_file, self._on_edit_proj_file, self._on_del_proj_file,
        ))
        # ── 需求文件区 ──
        layout.addWidget(self._make_file_group(
            "需求文件", "#faad14", self.ai_req_files,
            self._on_add_req_file, self._on_edit_req_file, self._on_del_req_file,
        ))
        # ── 模型配置区 ──
        layout.addWidget(self._build_model_group())
        # ── 操作按钮区 ──
        layout.addWidget(self._build_action_group())
        return panel

    def _make_file_group(self, title: str, accent: str, files: List[str],
                         on_add, on_edit, on_delete) -> QGroupBox:
        group = self._st_group(title, accent)
        g_layout = QVBoxLayout(group)
        g_layout.setSpacing(10)
        info = QLabel("管理项目相关文件资料" if "项目" in title else "管理需求分析相关文件")
        info.setStyleSheet("color: #666; font-size: 11px; font-weight: normal;")
        g_layout.addWidget(info)
        lst = QListWidget()
        lst.setStyleSheet("""
            QListWidget { border: 1px solid #ebebeb; border-radius: 4px; background: white; }
            QListWidget::item { padding: 8px; border-bottom: 1px solid #f5f5f5; }
            QListWidget::item:selected { background: #e6f4ff; }
        """)
        for f in files:
            lst.addItem(f)
        # store list widget ref
        if "项目" in title:
            self._proj_list = lst
        else:
            self._req_list = lst
        g_layout.addWidget(lst)
        g_layout.addLayout(self._tri_btn_row("添加文件", on_add,
                                              "编辑文件", on_edit,
                                              "移除", on_delete))
        return group

    def _build_model_group(self) -> QGroupBox:
        group = self._st_group("AI模型配置", "#1890ff")
        layout = QVBoxLayout(group)
        layout.setSpacing(10)
        row = QHBoxLayout()
        row.setSpacing(8)
        row.addWidget(QLabel("模型:"))
        self.ai_model_combo = QComboBox()
        self.ai_model_combo.setStyleSheet(
            "QComboBox { border: 1px solid #d9d9d9; border-radius: 4px; "
            "padding: 8px 12px; background: white; }"
            "QComboBox:focus { border-color: #1890ff; }"
        )
        row.addWidget(self.ai_model_combo, stretch=1)
        config_btn = QPushButton("配置")
        config_btn.setStyleSheet(
            "QPushButton { background-color: #722ed1; color: white; border: none; "
            "border-radius: 4px; padding: 8px 14px; font-weight: bold; }"
            "QPushButton:hover { background-color: #9254de; }"
        )
        config_btn.clicked.connect(self._on_model_config)
        row.addWidget(config_btn)
        layout.addLayout(row)

        # 连接/测试/断开按钮行
        btn_row = QHBoxLayout()
        btn_row.setSpacing(8)
        btn_row.addWidget(self._make_btn("连接模型", '#1890ff', '#40a9ff', self._on_connect_model))
        btn_row.addWidget(self._make_btn("测试模型", '#52c41a', '#73d13d', self._on_test_model))
        btn_row.addWidget(self._make_btn("断开模型", '#ff4d4f', '#ff7875', self._on_disconnect_model))
        layout.addLayout(btn_row)
        return group

    def _build_action_group(self) -> QGroupBox:
        group = self._st_group("执行诊断", "#52c41a")
        layout = QVBoxLayout(group)
        layout.setSpacing(10)
        layout.addWidget(self._make_large_btn('运行AI诊断', '#1890ff', '#40a9ff', self._run_diagnosis))
        layout.addWidget(self._make_large_btn('运行多智能体诊断', '#52c41a', '#73d13d', self._run_multi_agent))
        return group

    def _build_right_panel(self) -> QWidget:
        panel = QWidget()
        self._right_panel_layout = QVBoxLayout(panel)
        self._right_panel_layout.setContentsMargins(0, 0, 0, 0)
        self._right_panel_layout.setSpacing(0)

        self.ai_terminal_panel = QFrame()
        self.ai_terminal_panel.setStyleSheet(
            "QFrame { border: 1px solid #d0d5dd; border-radius: 12px; background: white; }"
        )
        t_layout = QVBoxLayout(self.ai_terminal_panel)
        t_layout.setContentsMargins(0, 0, 0, 0)
        t_layout.setSpacing(0)

        t_layout.addWidget(self._build_title_bar())
        t_layout.addWidget(self._build_content_splitter(), stretch=1)
        t_layout.addWidget(self._build_bottom_bar())

        self._right_panel_layout.addWidget(self.ai_terminal_panel)
        return panel

    def _build_title_bar(self) -> QWidget:
        self.ai_title_bar = QWidget()
        self.ai_title_bar.setStyleSheet(
            "QWidget { background: qlineargradient(x1:0,y1:0,x2:1,y2:0,"
            "stop:0 #f8f9fb, stop:1 #ffffff); border-top-left-radius: 12px; "
            "border-top-right-radius: 12px; border-bottom: 1px solid #e8eaed; }"
        )
        layout = QHBoxLayout(self.ai_title_bar)
        layout.setContentsMargins(16, 10, 16, 10)
        layout.setSpacing(10)
        self.ai_terminal_title = QLabel("AI 诊断结果")
        self.ai_terminal_title.setStyleSheet(
            "font-size: 15px; font-weight: bold; color: #1a1a2e; background: transparent; border: none;"
        )
        layout.addWidget(self.ai_terminal_title)
        layout.addStretch()

        self.ai_zen_enter_btn = QPushButton("🔍 沉浸全屏")
        self.ai_zen_enter_btn.setStyleSheet(
            "QPushButton { background: transparent; color: #666; border: 1px solid #d9d9d9; "
            "border-radius: 6px; padding: 6px 14px; font-size: 12px; }"
            "QPushButton:hover { border-color: #1890ff; color: #1890ff; background: #e6f4ff; }"
        )
        self.ai_zen_enter_btn.clicked.connect(self._enter_zen_mode)
        layout.addWidget(self.ai_zen_enter_btn)
        return self.ai_title_bar

    def _build_content_splitter(self) -> QSplitter:
        self.ai_content_splitter = QSplitter(Qt.Orientation.Vertical)
        self.ai_content_splitter.setHandleWidth(6)
        self.ai_content_splitter.setChildrenCollapsible(False)
        self.ai_content_splitter.setStyleSheet(
            "QSplitter::handle { background: #e8eaed; border-radius: 2px; }"
            "QSplitter::handle:hover { background: #1890ff; }"
        )

        # 上半: AI 交互问答区
        chat_w = QWidget()
        chat_w.setStyleSheet("background: transparent;")
        chat_layout = QVBoxLayout(chat_w)
        chat_layout.setContentsMargins(12, 8, 12, 8)
        chat_layout.setSpacing(8)
        chat_layout.addWidget(QLabel("💬 交互问答"))
        self.ai_chat_text = QTextEdit()
        self.ai_chat_text.setReadOnly(True)
        self.ai_chat_text.setStyleSheet(
            "QTextEdit { border: 1px solid #ebebeb; border-radius: 6px; "
            "padding: 10px; background: #fafafa; font-size: 12px; }"
        )
        chat_layout.addWidget(self.ai_chat_text, stretch=1)
        input_row = QHBoxLayout()
        input_row.setSpacing(8)
        self.ai_chat_input = QLineEdit()
        self.ai_chat_input.setPlaceholderText("输入您的问题...")
        self.ai_chat_input.setStyleSheet(
            "QLineEdit { border: 1px solid #d9d9d9; border-radius: 6px; padding: 10px 14px; }"
            "QLineEdit:focus { border-color: #1890ff; }"
        )
        self.ai_chat_input.returnPressed.connect(self._chat_query)
        input_row.addWidget(self.ai_chat_input, stretch=1)
        input_row.addWidget(self._make_btn("发送", '#1890ff', '#40a9ff', self._chat_query))
        clr = QPushButton("清除")
        clr.setStyleSheet(
            "QPushButton { padding: 10px 14px; border:1px solid #d9d9d9; "
            "border-radius:6px; background:#fff; }"
            "QPushButton:hover { border-color:#ff4d4f; color:#ff4d4f; }"
        )
        clr.clicked.connect(self.ai_chat_text.clear)
        input_row.addWidget(clr)
        chat_layout.addLayout(input_row)
        self.ai_content_splitter.addWidget(chat_w)

        # 下半: 诊断结果展示区
        result_w = QWidget()
        result_w.setStyleSheet("background: transparent;")
        result_layout = QVBoxLayout(result_w)
        result_layout.setContentsMargins(12, 8, 12, 12)
        result_layout.setSpacing(8)
        result_layout.addWidget(QLabel("📊 诊断结果"))
        self.ai_diagnosis_result = QTextEdit()
        self.ai_diagnosis_result.setReadOnly(True)
        self.ai_diagnosis_result.setPlaceholderText(
            "AI诊断结果将显示在这里...\n\n运行常规诊断或多智能体诊断后，结果将在此区域展示。"
        )
        self.ai_diagnosis_result.setStyleSheet(
            "QTextEdit { border: 1px solid #ebebeb; border-radius: 6px; "
            "padding: 14px; font-size: 13px; line-height: 1.8; background: #fefefe; }"
        )
        result_layout.addWidget(self.ai_diagnosis_result, stretch=1)
        self.ai_content_splitter.addWidget(result_w)
        self.ai_content_splitter.setStretchFactor(0, 30)
        self.ai_content_splitter.setStretchFactor(1, 70)
        return self.ai_content_splitter

    def _build_bottom_bar(self) -> QWidget:
        bar = QWidget()
        bar.setStyleSheet(
            "QWidget { background: #fafbfc; border-bottom-left-radius: 12px; "
            "border-bottom-right-radius: 12px; border-top: 1px solid #e8eaed; }"
        )
        layout = QHBoxLayout(bar)
        layout.setContentsMargins(16, 8, 16, 8)
        layout.setSpacing(8)
        clr = QPushButton("清除内容")
        clr.setStyleSheet(
            "QPushButton { padding: 7px 18px; border:1px solid #d9d9d9; "
            "border-radius:6px; background:#fff; }"
            "QPushButton:hover { border-color:#ff4d4f; color:#ff4d4f; }"
        )
        clr.clicked.connect(self.ai_diagnosis_result.clear)
        layout.addWidget(clr)
        layout.addStretch()
        save = QPushButton("保存结果")
        save.setStyleSheet(
            "QPushButton { padding: 7px 18px; border:1px solid #1890ff; "
            "border-radius:6px; background:#1890ff; color:white; font-weight:bold; }"
            "QPushButton:hover { background:#40a9ff; }"
        )
        save.clicked.connect(self._save_result)
        layout.addWidget(save)
        return bar

    # ═══════════════════════════════════════════════
    # 静态样式辅助
    # ═══════════════════════════════════════════════

    @staticmethod
    def _st_group(title: str, accent: str) -> QGroupBox:
        g = QGroupBox(title)
        g.setStyleSheet(f"""
            QGroupBox {{ border:1px solid #e0e0e0; border-radius:8px; font-weight:bold;
                color:{accent}; padding:10px; padding-top:22px; background:white; }}
            QGroupBox::title {{ subcontrol-origin:margin; left:12px; padding:0 6px; }}
        """)
        return g

    @staticmethod
    def _make_btn(text: str, bg: str, hover: str, handler) -> QPushButton:
        btn = QPushButton(text)
        btn.setStyleSheet(f"""
            QPushButton {{ background-color:{bg}; color:white; border:none;
                border-radius:4px; padding:8px 12px; font-weight:bold; }}
            QPushButton:hover {{ background-color:{hover}; }}
        """)
        btn.clicked.connect(handler)
        return btn

    @staticmethod
    def _make_large_btn(text: str, bg: str, hover: str, handler) -> QPushButton:
        btn = QPushButton(text)
        btn.setMinimumHeight(42)
        btn.setStyleSheet(f"""
            QPushButton {{ background-color:{bg}; color:white; border:none;
                border-radius:6px; padding:12px 20px; font-weight:bold; font-size:14px; }}
            QPushButton:hover {{ background-color:{hover}; }}
        """)
        btn.clicked.connect(handler)
        return btn

    @staticmethod
    def _tri_btn_row(t1: str, h1, t2: str, h2, t3: str, h3) -> QHBoxLayout:
        row = QHBoxLayout()
        row.setSpacing(8)
        for text, handler, color in [(t1, h1, '#52c41a'), (t2, h2, '#1890ff'), (t3, h3, '#ff4d4f')]:
            btn = QPushButton(text)
            btn.setStyleSheet(f"""
                QPushButton {{ background-color:{color}; color:white; border:none;
                    border-radius:4px; padding:8px 12px; font-weight:bold; }}
                QPushButton:hover {{ opacity:0.85; }}
            """)
            btn.clicked.connect(handler)
            row.addWidget(btn)
        return row

    @staticmethod
    def _warn(parent, msg: str) -> None:
        QMessageBox.warning(parent, '警告', msg)

    # ═══════════════════════════════════════════════
    # 文件管理
    # ═══════════════════════════════════════════════

    def _on_add_proj_file(self) -> None:
        paths, _ = QFileDialog.getOpenFileNames(self, '选择项目文件', '',
                                                'Documents (*.doc *.docx *.pdf);;All Files (*)')
        for p in paths:
            if p not in self.ai_proj_files:
                self.ai_proj_files.append(p)
                self._proj_list.addItem(p)

    def _on_edit_proj_file(self) -> None:
        item = self._proj_list.currentItem()
        if item:
            path = item.text()
            if os.path.exists(path):
                os.startfile(path)
            else:
                self._warn(self, '文件不存在')

    def _on_del_proj_file(self) -> None:
        item = self._proj_list.currentItem()
        if item:
            p = item.text()
            if p in self.ai_proj_files:
                self.ai_proj_files.remove(p)
            self._proj_list.takeItem(self._proj_list.row(item))

    def _on_add_req_file(self) -> None:
        paths, _ = QFileDialog.getOpenFileNames(self, '选择需求文件', '',
                                                'Text Files (*.txt *.md);;All Files (*)')
        for p in paths:
            if p not in self.ai_req_files:
                self.ai_req_files.append(p)
                self._req_list.addItem(p)

    def _on_edit_req_file(self) -> None:
        item = self._req_list.currentItem()
        if item:
            path = item.text()
            if os.path.exists(path):
                os.startfile(path)
            else:
                self._warn(self, '文件不存在')

    def _on_del_req_file(self) -> None:
        item = self._req_list.currentItem()
        if item:
            p = item.text()
            if p in self.ai_req_files:
                self.ai_req_files.remove(p)
            self._req_list.takeItem(self._req_list.row(item))

    # ═══════════════════════════════════════════════
    # 模型管理
    # ═══════════════════════════════════════════════

    def _load_models_config(self) -> None:
        try:
            cfg_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'ai_models_config.json')
            if os.path.exists(cfg_path):
                with open(cfg_path, 'r', encoding='utf-8') as f:
                    self._ai_models_config = json.load(f)
            self._refresh_model_selector()
            self._auto_connect()
        except Exception as e:
            print(f"加载AI模型配置失败: {e}")

    def _save_models_config(self) -> None:
        try:
            cfg_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'ai_models_config.json')
            with open(cfg_path, 'w', encoding='utf-8') as f:
                json.dump(self._ai_models_config, f, ensure_ascii=False, indent=2)
        except Exception as e:
            print(f"保存AI模型配置失败: {e}")

    def _refresh_model_selector(self) -> None:
        self.ai_model_combo.clear()
        if not self._ai_models_config:
            self.ai_model_combo.addItem('(未配置模型)')
            return
        self.ai_model_combo.addItems(list(self._ai_models_config.keys()))
        self.ai_model_combo.currentIndexChanged.connect(self._on_model_selected)

    def _auto_connect(self) -> None:
        if self._ai_models_config:
            first_name = list(self._ai_models_config.keys())[0]
            config = self._ai_models_config[first_name]
            if config.get('api_key'):
                self._connect_with_config(config)
                # 启动后自动测试延迟（后台线程，不阻塞 UI）
                self._auto_test_latency()

    def _auto_test_latency(self) -> None:
        """启动后自动测延迟 — 基于后台线程 TTFB 信号，前端只渲染结果。"""
        if not hasattr(self, '_online_config') or not self._online_config.get('api_key'):
            return
        self._set_latency('-- ms')
        try:
            from py.online_llm_thread import OnlineLlamaGenerateThread
            self._test_thread = OnlineLlamaGenerateThread(
                api_key=self._online_config['api_key'],
                base_url=self._online_config['base_url'],
                model_name=self._online_config['model_name'],
                prompt='你好',
                temperature=0.3,
                max_tokens=1,
                system_prompt='',
                latency_mode=True,  # 启用纯延迟测试模式
            )
            # 后台线程在 run() 内部计时并发射 latency_tested 信号
            self._test_thread.latency_tested.connect(self._on_latency_result)
            self._test_thread.error.connect(lambda e: print(f"[AI诊断] 启动延迟测试失败: {e}"))
            self._test_thread.start()
        except Exception as e:
            print(f"[AI诊断] 启动延迟测试异常: {e}")

    def _on_latency_result(self, ms: int) -> None:
        """后台线程返回的 TTFB 毫秒数 — 仅渲染，不参与任何计时逻辑。"""
        if ms < 0:
            self._set_latency('-- ms')
            return
        self._set_latency(f"{ms} ms")

    def _on_model_selected(self, index: int) -> None:
        name = self.ai_model_combo.currentText()
        if name in self._ai_models_config:
            self._connect_with_config(self._ai_models_config[name])

    def _on_model_config(self) -> None:
        main_win = self._find_main()
        if not main_win:
            return
        try:
            from main import AIModelConfigDialog
            dlg = AIModelConfigDialog(main_win)
            dlg.exec()
            self._load_models_config()
        except ImportError:
            self._warn(self, '无法加载模型配置对话框')

    def _on_connect_model(self) -> None:
        name = self.ai_model_combo.currentText()
        if name and name in self._ai_models_config:
            self._connect_with_config(self._ai_models_config[name])
        else:
            self._warn(self, '请先配置AI模型')

    def _connect_with_config(self, config: dict) -> None:
        api_key = config.get('api_key', '')
        base_url = config.get('base_url', '')
        model_name = config.get('model_name', '')
        if not api_key:
            self._set_status("● 未配置API密钥", '#ff4d4f', '#fff1f0')
            return
        try:
            from py.online_llm_thread import OnlineLlamaGenerateThread
            self._online_config = {
                'api_key': api_key, 'base_url': base_url,
                'model_name': model_name, 'temperature': config.get('temperature', 0.7),
                'max_tokens': config.get('max_tokens', 2048),
                'system_prompt': config.get('system_prompt', ''),
            }
            self._set_status("● 已连接", '#52c41a', '#f6ffed')
            self._set_latency('-- ms')
            print(f"[AI诊断] 已连接模型: {model_name}")
        except Exception as e:
            self._set_status(f"● 连接失败: {e}", '#ff4d4f', '#fff1f0')

    def _on_test_model(self) -> None:
        if not hasattr(self, '_online_config') or not self._online_config.get('api_key'):
            self._warn(self, '请先连接模型')
            return
        self._set_status("● 测试中...", '#faad14', '#fffbe6')
        QApplication.processEvents()
        try:
            from py.online_llm_thread import OnlineLlamaGenerateThread
            t0 = time.time()
            self._test_thread = OnlineLlamaGenerateThread(
                api_key=self._online_config['api_key'],
                base_url=self._online_config['base_url'],
                model_name=self._online_config['model_name'],
                prompt='你好，请用一句话介绍自己。',
                temperature=0.3,
                max_tokens=64,
                system_prompt='',
            )
            self._test_thread.finished.connect(lambda r: self._on_test_done(r, t0))
            self._test_thread.error.connect(lambda e: self._on_test_error(str(e)))
            self._test_thread.start()
        except Exception as e:
            self._set_status(f"● 测试失败: {e}", '#ff4d4f', '#fff1f0')

    def _on_test_done(self, result: str, t0: float) -> None:
        elapsed = int((time.time() - t0) * 1000)
        self._set_latency(f"{elapsed} ms")
        self._set_status("● 已连接", '#52c41a', '#f6ffed')
        QMessageBox.information(self, '测试成功', f'延迟: {elapsed}ms\n\n响应: {result[:120]}')

    def _on_test_error(self, err: str) -> None:
        self._set_status("● 测试失败", '#ff4d4f', '#fff1f0')
        self._warn(self, f'模型测试失败: {err}')

    def _on_disconnect_model(self) -> None:
        if hasattr(self, '_online_config'):
            del self._online_config
        self._set_status("● 未连接", '#ff4d4f', '#fff1f0')
        self._set_latency('-- ms')

    def _set_status(self, text: str, color: str, bg: str = '#fff1f0') -> None:
        self.ai_status_label.setText(text)
        self.ai_status_label.setStyleSheet(
            f"color: {color}; font-weight: bold; font-size: 13px; "
            f"padding: 6px 14px; background: {bg}; border-radius: 4px;"
        )
        self.status_changed.emit(text, color)

    def _set_latency(self, text: str) -> None:
        self.ai_latency_label.setText(f"延迟: {text}")
        # 根据延迟值动态更新颜色
        if text == '-- ms':
            color, bg = '#999', '#f5f5f5'
        else:
            try:
                ms = int(text.replace('ms', '').strip())
                if ms < 300:
                    color, bg = '#52c41a', '#f6ffed'
                elif ms < 800:
                    color, bg = '#faad14', '#fffbe6'
                else:
                    color, bg = '#ff4d4f', '#fff1f0'
            except ValueError:
                color, bg = '#999', '#f5f5f5'
        self.ai_latency_label.setStyleSheet(
            f"color: {color}; font-size: 12px; padding: 6px 14px; "
            f"background: {bg}; border-radius: 4px;"
        )

    # ═══════════════════════════════════════════════
    # AI 诊断
    # ═══════════════════════════════════════════════

    def _run_diagnosis(self) -> None:
        if not hasattr(self, '_online_config') or not self._online_config.get('api_key'):
            self._warn(self, '请先连接AI模型')
            return

        ctx = self._build_data_context()
        if ctx.get("error") or not ctx.get("has_data"):
            self._warn(self, '请先加载数据文件')
            return

        # 构建增强版系统提示词（含数据类型防火墙、模板状态、清洗统计、JSON schema）
        system_prompt = self._build_system_prompt(ctx)
        # 用户消息 = 纯数据上下文（不含 schema 约束，schema 在系统提示词中）
        user_prompt = self._build_context_text(ctx)

        self._diagnosis_json_response = None
        self._diagnosis_raw_response = ""
        self.ai_diagnosis_result.clear()
        self.ai_terminal_title.setText("AI 诊断结果 (生成中...)")
        QApplication.processEvents()

        try:
            from py.online_llm_thread import OnlineLlamaGenerateThread
            self._diagnosis_thread = OnlineLlamaGenerateThread(
                api_key=self._online_config['api_key'],
                base_url=self._online_config['base_url'],
                model_name=self._online_config['model_name'],
                prompt=user_prompt,
                temperature=self._online_config.get('temperature', 0.7),
                max_tokens=self._online_config.get('max_tokens', 4096),
                system_prompt=system_prompt,
            )
            self._diagnosis_thread.token_received.connect(self._on_diag_token)
            self._diagnosis_thread.finished.connect(self._on_diag_done)
            self._diagnosis_thread.error.connect(self._on_diag_error)
            self._diagnosis_thread.start()
        except Exception as e:
            self.ai_diagnosis_result.setPlainText(f'诊断启动失败: {e}')

    @pyqtSlot(str)
    def _on_diag_token(self, token: str) -> None:
        cursor = self.ai_diagnosis_result.textCursor()
        cursor.movePosition(cursor.MoveOperation.End)
        cursor.insertText(token)
        self.ai_diagnosis_result.ensureCursorVisible()

    @pyqtSlot(str)
    def _on_diag_done(self, result: str) -> None:
        self._diagnosis_raw_response = result
        # 尝试解析 JSON 并渲染结构化报告
        parsed = self._extract_json(result)
        if parsed:
            try:
                html = self._render_structured_report(parsed)
                self.ai_diagnosis_result.setHtml(html)
                self._diagnosis_json_response = parsed
            except Exception:
                self.ai_diagnosis_result.setPlainText(result)
        else:
            self.ai_diagnosis_result.setPlainText(result)
        self.ai_terminal_title.setText("AI 诊断结果")
        self.diagnosis_complete.emit(result)

    @pyqtSlot(str)
    def _on_diag_error(self, err: str) -> None:
        self.ai_diagnosis_result.setPlainText(f'诊断失败: {err}')
        self.ai_terminal_title.setText("AI 诊断结果 (失败)")

    def _run_multi_agent(self) -> None:
        if not hasattr(self, '_online_config') or not self._online_config.get('api_key'):
            self._warn(self, '请先连接AI模型')
            return

        main_win = self._find_main()
        csv_path = None
        if main_win:
            csv_path = getattr(main_win, 'sampled_file_path', None)

        ctx = self._build_data_context()
        context_text = self._build_context_text(ctx)
        user_input = f"请分析以下传感器数据:\n\n{context_text}\n\nCSV文件路径: {csv_path or '未指定'}"

        self.ai_diagnosis_result.clear()
        self.ai_terminal_title.setText("多智能体诊断 (运行中...)")
        QApplication.processEvents()

        try:
            from py.multi_agent import run_multi_agent
            result = run_multi_agent(
                user_input=user_input,
                api_key=self._online_config['api_key'],
                base_url=self._online_config['base_url'],
                model_name=self._online_config['model_name'],
                csv_path=csv_path,
                data_context=ctx,
            )
            # Phase 3: audit_result 是结构化 dict
            audit = result.get('audit_result') or {}
            if isinstance(audit, dict):
                verdict = audit.get('verdict', '?')
                reasons = '\n'.join(f"- {r}" for r in audit.get('reasons', []))
                fixes = '\n'.join(f"- {f}" for f in audit.get('required_fixes', []))
                audit_str = f"裁决: {verdict}\n理由:\n{reasons}"
                if fixes:
                    audit_str += f"\n修正要求:\n{fixes}"
                if result.get('is_failed'):
                    audit_str = f"⚠️ 多智能体审查未通过（驳回 {result.get('rejection_count', 0)} 次）\n{audit_str}"
            else:
                audit_str = str(audit)
            report = (
                f"## 数据科学家报告\n\n{result.get('data_scientist_report', '无')}\n\n"
                f"---\n\n## 审查结果\n\n{audit_str}\n\n"
                f"---\n\n## 首席专家报告\n\n{result.get('chief_scientist_report', '无')}\n\n"
                f"---\n\n## 最终报告\n\n{result.get('final_report', '无')}"
            )
            self._multi_report = report
            self.ai_diagnosis_result.setMarkdown(report)
            self.ai_terminal_title.setText("多智能体诊断结果")
        except Exception as e:
            self.ai_diagnosis_result.setPlainText(f'多智能体诊断失败: {e}')
            self.ai_terminal_title.setText("多智能体诊断 (失败)")

    # ═══════════════════════════════════════════════
    # 数据类型防火墙判定（与 analysis_tab 逻辑一致）
    # ═══════════════════════════════════════════════

    @staticmethod
    def _check_is_fiber_data(data, main_win) -> bool:
        """判断当前数据是否为光纤光栅数据（双保险判定，与 analysis_tab._is_fiber_data 保持一致）"""
        if data is None:
            return False
        # 判定 1：列名匹配
        for c in data.columns:
            name = str(c)
            if 'FBG' in name or 'ENLIG' in name or '光纤传感' in name:
                return True
            if re.match(r'^W\d+$', name):
                return True
        # 判定 2：模板名
        template = getattr(main_win, 'current_template', None)
        if template and hasattr(template, 'name'):
            tname = str(template.name)
            if '光纤' in tname or 'ENLIGHT' in tname:
                return True
        return False

    # ═══════════════════════════════════════════════
    # 数据上下文构建（增强版）
    # ═══════════════════════════════════════════════

    def _build_data_context(self) -> dict:
        """构建当前数据上下文的完整结构化摘要，返回 dict 供系统提示词和数据上下文使用。

        包含：模板状态、数据规模、数据类型（光纤/通用）、清洗统计、传感器/标注列汇总。
        """
        main_win = self._find_main()
        if not main_win:
            return {"error": "未找到主窗口"}

        ctx: dict[str, Any] = {}

        # ── 模板与数据类型 ──
        template = getattr(main_win, 'current_template', None)
        if template:
            ctx["template_name"] = str(getattr(template, 'name', ''))
            ctx["template_id"] = str(getattr(template, 'id', ''))
            ctx["file_format"] = str(getattr(template, 'file_format', ''))
        else:
            ctx["template_name"] = "(无模板)"

        data = getattr(main_win, 'current_data', None)
        if data is None or data.empty:
            ctx["has_data"] = False
            return ctx

        ctx["has_data"] = True
        ctx["data_rows"] = len(data)
        ctx["data_columns"] = len(data.columns)
        ctx["column_names"] = [str(c) for c in data.columns]

        # ── 数据类型防火墙 ──
        is_fiber = self._check_is_fiber_data(data, main_win)
        ctx["data_type"] = "fiber_optic" if is_fiber else "general"
        ctx["data_type_label"] = "光纤光栅传感器数据" if is_fiber else "通用数据（TXT/CSV）"

        # ── 数值列统计摘要 ──
        numeric_cols = data.select_dtypes(include=['number']).columns.tolist()
        if numeric_cols:
            stats_rows = []
            for c in numeric_cols:
                col_data = data[c].dropna()
                if len(col_data) > 0:
                    stats_rows.append({
                        "col": str(c),
                        "count": int(len(col_data)),
                        "missing": int(data[c].isna().sum()),
                        "mean": round(float(col_data.mean()), 4),
                        "std": round(float(col_data.std()), 4),
                        "min": round(float(col_data.min()), 4),
                        "max": round(float(col_data.max()), 4),
                    })
            ctx["numeric_stats"] = stats_rows

        # ── 清洗统计（从 cleaning_tab_widget 获取结果文本） ──
        cleaning_text = None
        cleaning_tab = getattr(main_win, 'cleaning_tab_widget', None)
        if cleaning_tab:
            try:
                ct = cleaning_tab.cleaning_result.toPlainText().strip()
                if ct:
                    cleaning_text = ct
            except Exception:
                pass
        ctx["cleaning_summary"] = cleaning_text or "(未执行数据清洗)"

        # ── 传感器结果（光纤数据） ──
        sensor_results = getattr(main_win, 'sensor_results', {})
        if sensor_results:
            ctx["sensor_results_summary"] = []
            for sid, vals in sensor_results.items():
                clean = [v for v in vals if v is not None]
                if clean:
                    ctx["sensor_results_summary"].append({
                        "id": str(sid),
                        "valid_count": len(clean),
                        "total_count": len(vals),
                        "min": round(float(min(clean)), 4),
                        "max": round(float(max(clean)), 4),
                        "mean": round(float(sum(clean) / len(clean)), 4),
                    })

        # ── 暗号标注列（通用数据） ──
        try:
            annotated_cols = main_win.get_annotated_columns()
            if annotated_cols:
                ctx["annotated_columns"] = list(annotated_cols)
        except Exception:
            pass

        return ctx

    def _build_context_text(self, ctx: dict) -> str:
        """将结构化上下文 dict 转为纯文本描述，供 user prompt 使用。"""
        if not ctx or ctx.get("error") or not ctx.get("has_data"):
            return "当前无数据。"

        lines = []
        lines.append(f"数据文件模板: {ctx.get('template_name', '未知')}")
        lines.append(f"数据类型: {ctx.get('data_type_label', '未知')}")
        lines.append(f"数据规模: {ctx.get('data_rows', 0)} 行 x {ctx.get('data_columns', 0)} 列")
        lines.append(f"列名: {', '.join(ctx.get('column_names', []))}")

        stats = ctx.get("numeric_stats", [])
        if stats:
            lines.append("\n--- 数值列统计 ---")
            for s in stats:
                lines.append(
                    f"  {s['col']}: 有效={s['count']}, 缺失={s['missing']}, "
                    f"均值={s['mean']}, 标准差={s['std']}, "
                    f"范围=[{s['min']}, {s['max']}]"
                )

        lines.append(f"\n清洗情况: {ctx.get('cleaning_summary', '未清洗')}")

        sensors = ctx.get("sensor_results_summary", [])
        if sensors:
            lines.append("\n传感器计算结果:")
            for s in sensors:
                lines.append(
                    f"  {s['id']}: 有效={s['valid_count']}/{s['total_count']}, "
                    f"均值={s['mean']}, 范围=[{s['min']}, {s['max']}]"
                )

        annotated = ctx.get("annotated_columns", [])
        if annotated:
            lines.append(f"\n暗号标注列: {annotated}")

        # 需求文件
        if self.ai_req_files:
            for rf in self.ai_req_files:
                try:
                    with open(rf, 'r', encoding='utf-8') as f:
                        content = f.read()[:2000]
                        lines.append(f"\n--- 需求文件: {rf} ---\n{content}")
                except Exception:
                    pass

        return '\n'.join(lines)

    # ═══════════════════════════════════════════════
    # 系统提示词构建（核心增强）
    # ═══════════════════════════════════════════════

    DIAGNOSIS_JSON_SCHEMA = {
        "type": "object",
        "properties": {
            "diagnosis_summary": {
                "type": "object",
                "properties": {
                    "data_type": {"type": "string", "enum": ["fiber_optic", "general"]},
                    "template_name": {"type": "string"},
                    "data_quality": {"type": "string", "enum": ["good", "fair", "poor"]},
                    "anomaly_count": {"type": "integer"},
                    "overall_assessment": {"type": "string"},
                    "assessment_en": {"type": "string"}
                }
            },
            "data_quality_assessment": {
                "type": "object",
                "properties": {
                    "completeness": {"type": "string"},
                    "consistency": {"type": "string"},
                    "anomaly_patterns": {"type": "array", "items": {"type": "string"}},
                    "recommendations": {"type": "array", "items": {"type": "string"}}
                }
            },
            "sensor_analysis": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "sensor_id": {"type": "string"},
                        "status": {"type": "string", "enum": ["normal", "warning", "critical"]},
                        "statistics": {
                            "type": "object",
                            "properties": {
                                "mean": {"type": "number"},
                                "std": {"type": "number"},
                                "min": {"type": "number"},
                                "max": {"type": "number"}
                            }
                        },
                        "findings": {"type": "string"},
                        "suggestions": {"type": "string"}
                    }
                }
            },
            "physical_diagnosis": {
                "type": "object",
                "properties": {
                    "phenomenon": {"type": "string"},
                    "possible_causes": {"type": "array", "items": {"type": "string"}},
                    "severity": {"type": "string", "enum": ["low", "medium", "high"]},
                    "recommended_actions": {"type": "array", "items": {"type": "string"}}
                }
            },
            "raw_data_preview": {
                "type": "object",
                "properties": {
                    "suggested_filter": {"type": "string"},
                    "suggested_formula": {"type": "string"},
                    "notes": {"type": "string"}
                }
            }
        }
    }

    def _build_system_prompt(self, ctx: dict) -> str:
        """构建带上下文约束的系统提示词。

        关键设计：
          - 数据类型防火墙（光纤 vs 通用）直接注入 system prompt 顶层规则
          - 模板状态、清洗报警动态序列化进上下文块
          - 严格 JSON 输出 schema
        """
        data_type = ctx.get("data_type", "general")
        template_name = ctx.get("template_name", "(无模板)")
        cleaning_summary = ctx.get("cleaning_summary", "(未清洗)")
        is_fiber = data_type == "fiber_optic"

        fiber_rule = (
            "当前数据为【光纤光栅传感器数据】，基于波长差（W1~W8）进行物理量转换。\n"
            "  - 关注 FBG 传感器的波长漂移趋势与应变/温度耦合效应\n"
            "  - 判断滤波截止频率是否合理（过低会导致波形畸变）\n"
            "  - 检查基线回零是否准确\n"
            "  - 评估 NOA 81 胶水与 PI 光纤的界面滑移风险"
        ) if is_fiber else (
            "当前数据为【通用数据（TXT/CSV）】，属于外部已算好的数据。\n"
            "  - 直接从标注列（annotated_columns）读取物理量含义\n"
            "  - 不进行波长差到物理量的转换计算\n"
            "  - 关注数据本身的完整性、一致性和异常模式\n"
            "  - 提供后续数据处理建议"
        )

        return (
            "你是一名专业的结构健康监测（SHM）诊断专家，精通传感器数据分析、异常诊断与物理机理分析。\n"
            "\n"
            "=== 当前数据上下文 ===\n"
            f"模板: {template_name}\n"
            f"数据类型: {'光纤光栅' if is_fiber else '通用'}\n"
            f"清洗概况: {cleaning_summary}\n"
            "\n"
            "=== 数据类型防火墙规则（绝对约束） ===\n"
            f"{fiber_rule}\n"
            "\n"
            "=== 分析维度 ===\n"
            "1. 数据质量评估 — 完整性、一致性、异常密度\n"
            "2. 异常模式识别 — 离群点、趋势突变、周期性异常\n"
            "3. 传感器性能评估 — 漂移程度、信噪比、基线稳定性\n"
            "4. 物理诊断 — 可能的材料力学行为解释\n"
            "5. 处理建议 — 滤波参数推荐、清洗策略优化、后续关注点\n"
            "\n"
            "=== 输出要求 ===\n"
            "你必须严格以 JSON 对象返回诊断结果，遵循以下 schema（输出纯 JSON，不要 markdown 包裹，不要多余文字）：\n"
            f"{json.dumps(self.DIAGNOSIS_JSON_SCHEMA, ensure_ascii=False, indent=2)}\n"
            "\n"
            "注意：assessment_en 字段用英文撰写一句话总结（便于国际化仪表盘展示），其余字段全部用中文。"
        )

    # ═══════════════════════════════════════════════
    # JSON 解析 & 结构化渲染
    # ═══════════════════════════════════════════════

    @staticmethod
    def _extract_json(text: str) -> dict | None:
        """从模型输出中提取第一个 JSON 对象。

        优先尝试直接解析，失败则用正则提取 {...} 或 ```json ... ```。
        """
        # 先尝试直接解析
        text = text.strip()
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            pass

        # 尝试提取 markdown 代码块中的 JSON
        m = re.search(r'```(?:json)?\s*\n?(.*?)\n?```', text, re.DOTALL)
        if m:
            try:
                return json.loads(m.group(1).strip())
            except json.JSONDecodeError:
                pass

        # 尝试提取最外层大括号
        brace_start = text.find('{')
        if brace_start >= 0:
            depth = 0
            for i in range(brace_start, len(text)):
                if text[i] == '{':
                    depth += 1
                elif text[i] == '}':
                    depth -= 1
                    if depth == 0:
                        try:
                            return json.loads(text[brace_start:i + 1])
                        except json.JSONDecodeError:
                            break
        return None

    def _render_structured_report(self, data: dict) -> str:
        """将解析后的 JSON 诊断报告渲染为格式化的 HTML。"""
        html_parts = []

        # ── 标题 ──
        summary = data.get('diagnosis_summary', {})
        quality = summary.get('data_quality', 'unknown')
        quality_colors = {'good': '#52c41a', 'fair': '#faad14', 'poor': '#ff4d4f'}
        q_color = quality_colors.get(quality, '#999')

        html_parts.append(
            f'<div style="background: linear-gradient(135deg, #667eea 0%, #764ba2 100%); '
            f'color: white; padding: 16px 20px; border-radius: 8px; margin-bottom: 16px;">'
            f'<h2 style="margin: 0; font-size: 18px;">诊断报告</h2>'
            f'<div style="margin-top: 8px; font-size: 13px;">'
            f'数据类型: {summary.get("data_type", "N/A")} &nbsp;|&nbsp; '
            f'模板: {summary.get("template_name", "N/A")}'
            f'</div>'
            f'<div style="margin-top: 4px;">'
            f'<span style="background: {q_color}; padding: 2px 10px; border-radius: 10px; '
            f'font-size: 12px; font-weight: bold;">数据质量: {quality.upper()}</span>'
            f' &nbsp; 异常点数: {summary.get("anomaly_count", "N/A")}'
            f'</div>'
            f'</div>'
        )

        # ── 总体评价 ──
        overall = summary.get('overall_assessment', '')
        if overall:
            html_parts.append(
                f'<div style="background: #f0f5ff; border-left: 4px solid #1890ff; '
                f'padding: 12px 16px; margin-bottom: 12px; border-radius: 0 4px 4px 0;">'
                f'<strong>总体评价</strong><br>{overall}'
                f'</div>'
            )

        # ── 数据质量 ──
        quality = data.get('data_quality_assessment', {})
        if quality:
            html_parts.append('<div style="margin-bottom: 16px;">')
            html_parts.append('<h3 style="font-size: 15px; color: #333; margin-bottom: 8px;">数据质量评估</h3>')
            html_parts.append(
                f'<table style="width: 100%; border-collapse: collapse; font-size: 13px;">'
                f'<tr><td style="padding: 6px 12px; color: #666; width: 100px;">完整性</td>'
                f'<td style="padding: 6px 12px;">{quality.get("completeness", "")}</td></tr>'
                f'<tr style="background: #fafafa;"><td style="padding: 6px 12px; color: #666;">一致性</td>'
                f'<td style="padding: 6px 12px;">{quality.get("consistency", "")}</td></tr>'
                f'</table>'
            )
            patterns = quality.get('anomaly_patterns', [])
            if patterns:
                html_parts.append('<div style="margin-top: 8px;"><strong>异常模式:</strong></div>')
                for p in patterns:
                    html_parts.append(
                        f'<div style="background: #fff2f0; border: 1px solid #ffccc7; '
                        f'padding: 6px 12px; border-radius: 4px; margin: 4px 0; font-size: 12px;">'
                        f'{p}</div>'
                    )
            recs = quality.get('recommendations', [])
            if recs:
                html_parts.append('<div style="margin-top: 8px;"><strong>建议:</strong></div>')
                for r in recs:
                    html_parts.append(
                        f'<div style="background: #f6ffed; border: 1px solid #b7eb8f; '
                        f'padding: 6px 12px; border-radius: 4px; margin: 4px 0; font-size: 12px;">'
                        f'{r}</div>'
                    )
            html_parts.append('</div>')

        # ── 传感器分析 ──
        sensors = data.get('sensor_analysis', [])
        if sensors:
            html_parts.append('<div style="margin-bottom: 16px;">')
            html_parts.append('<h3 style="font-size: 15px; color: #333; margin-bottom: 8px;">传感器分析</h3>')
            for s in sensors:
                status = s.get('status', 'normal')
                s_colors = {'normal': '#52c41a', 'warning': '#faad14', 'critical': '#ff4d4f'}
                s_color = s_colors.get(status, '#999')
                html_parts.append(
                    f'<div style="background: #fafafa; border: 1px solid #e8e8e8; '
                    f'border-radius: 6px; padding: 10px 14px; margin: 6px 0;">'
                    f'<div style="display: flex; justify-content: space-between; align-items: center;">'
                    f'<strong>{s.get("sensor_id", "")}</strong>'
                    f'<span style="background: {s_color}; color: white; padding: 1px 8px; '
                    f'border-radius: 8px; font-size: 11px;">{status}</span>'
                    f'</div>'
                    f'<div style="font-size: 12px; color: #666; margin-top: 4px;">'
                    f'均值={s.get("statistics", {}).get("mean", "N/A")}, '
                    f'标准差={s.get("statistics", {}).get("std", "N/A")}, '
                    f'范围=[{s.get("statistics", {}).get("min", "N/A")}, '
                    f'{s.get("statistics", {}).get("max", "N/A")}]'
                    f'</div>'
                )
                findings = s.get('findings', '')
                suggestions = s.get('suggestions', '')
                if findings:
                    html_parts.append(f'<div style="font-size: 12px; margin-top: 4px;">发现: {findings}</div>')
                if suggestions:
                    html_parts.append(f'<div style="font-size: 12px; color: #1890ff;">建议: {suggestions}</div>')
                html_parts.append('</div>')
            html_parts.append('</div>')

        # ── 物理诊断 ──
        phys = data.get('physical_diagnosis', {})
        if phys:
            severity = phys.get('severity', 'low')
            sev_colors = {'low': '#52c41a', 'medium': '#faad14', 'high': '#ff4d4f'}
            sev_color = sev_colors.get(severity, '#999')
            html_parts.append('<div style="margin-bottom: 16px;">')
            html_parts.append(
                f'<h3 style="font-size: 15px; color: #333; margin-bottom: 8px;">'
                f'物理诊断 '
                f'<span style="background: {sev_color}; color: white; padding: 1px 8px; '
                f'border-radius: 8px; font-size: 11px; vertical-align: middle;">{severity}</span>'
                f'</h3>'
            )
            phenomenon = phys.get('phenomenon', '')
            if phenomenon:
                html_parts.append(
                    f'<div style="background: #fffbe6; border: 1px solid #ffe58f; '
                    f'padding: 10px 14px; border-radius: 6px; margin: 6px 0; font-size: 13px;">'
                    f'{phenomenon}</div>'
                )
            causes = phys.get('possible_causes', [])
            if causes:
                html_parts.append('<div style="margin-top: 6px;"><strong>可能原因:</strong></div>')
                for c in causes:
                    html_parts.append(
                        f'<div style="padding: 4px 0 4px 16px; font-size: 12px; color: #555;">'
                        f'- {c}</div>'
                    )
            actions = phys.get('recommended_actions', [])
            if actions:
                html_parts.append('<div style="margin-top: 8px;"><strong>建议措施:</strong></div>')
                for a in actions:
                    html_parts.append(
                        f'<div style="background: #f6ffed; border: 1px solid #b7eb8f; '
                        f'padding: 6px 12px; border-radius: 4px; margin: 4px 0; font-size: 12px;">'
                        f'{a}</div>'
                    )
            html_parts.append('</div>')

        return ''.join(html_parts)

    # ═══════════════════════════════════════════════
    # 保存结果
    # ═══════════════════════════════════════════════

    def _save_result(self) -> None:
        content = self.ai_diagnosis_result.toPlainText() or self.ai_diagnosis_result.toMarkdown()
        if not content.strip():
            self._warn(self, '没有可保存的诊断结果')
            return
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        path, _ = QFileDialog.getSaveFileName(
            self, '保存诊断结果', f'diagnosis_{ts}.md',
            'Markdown (*.md);;Text (*.txt);;All Files (*)'
        )
        if path:
            with open(path, 'w', encoding='utf-8') as f:
                f.write(content)
            QMessageBox.information(self, '成功', f'结果已保存至: {path}')

    # ═══════════════════════════════════════════════
    # AI 聊天
    # ═══════════════════════════════════════════════

    def _chat_query(self) -> None:
        text = self.ai_chat_input.text().strip()
        if not text:
            return
        if not hasattr(self, '_online_config') or not self._online_config.get('api_key'):
            self._warn(self, '请先连接AI模型')
            return

        self.ai_chat_text.append(f'<b>用户:</b> {text}')
        self.ai_chat_input.clear()
        QApplication.processEvents()

        try:
            from py.online_llm_thread import OnlineLlamaGenerateThread
            self._chat_thread = OnlineLlamaGenerateThread(
                api_key=self._online_config['api_key'],
                base_url=self._online_config['base_url'],
                model_name=self._online_config['model_name'],
                prompt=text,
                temperature=self._online_config.get('temperature', 0.7),
                max_tokens=self._online_config.get('max_tokens', 1024),
                system_prompt=self._online_config.get('system_prompt', ''),
            )
            self._chat_thread.token_received.connect(self._on_chat_token)
            self._chat_thread.finished.connect(self._on_chat_done)
            self._chat_thread.error.connect(self._on_chat_error)
            self.ai_chat_text.append('<b>AI:</b> ')
            self._chat_thread.start()
        except Exception as e:
            self.ai_chat_text.append(f'<span style="color:red">错误: {e}</span>')

    @pyqtSlot(str)
    def _on_chat_token(self, token: str) -> None:
        cursor = self.ai_chat_text.textCursor()
        cursor.movePosition(cursor.MoveOperation.End)
        cursor.insertText(token)
        self.ai_chat_text.ensureCursorVisible()

    @pyqtSlot(str)
    def _on_chat_done(self, _result: str) -> None:
        self.ai_chat_text.append('')  # newline after response

    @pyqtSlot(str)
    def _on_chat_error(self, err: str) -> None:
        self.ai_chat_text.append(f'<span style="color:red">错误: {err}</span>')

    # ═══════════════════════════════════════════════
    # 禅模式 (Zen Mode)
    # ═══════════════════════════════════════════════

    def _enter_zen_mode(self) -> None:
        """全屏沉浸模式 — 将 AI 终端面板提升为全屏浮层"""
        main_win = self._find_main()
        if not main_win:
            return

        # 从右侧面板布局中取出终端面板（不删除，只是移除）
        self._right_panel_layout.removeWidget(self.ai_terminal_panel)

        # 创建全屏浮层
        self._zen_overlay = QWidget(main_win)
        self._zen_overlay.setStyleSheet("background: rgba(0,0,0,0.85);")
        self._zen_overlay.setGeometry(main_win.centralWidget().geometry())
        self._zen_overlay.show()

        overlay_layout = QVBoxLayout(self._zen_overlay)
        overlay_layout.setContentsMargins(40, 20, 40, 20)

        # 标题栏
        bar = QWidget()
        bar_layout = QHBoxLayout(bar)
        bar_layout.setContentsMargins(0, 0, 0, 0)
        title_label = QLabel("🔍 沉浸全屏诊断")
        title_label.setStyleSheet("color: white; font-size: 16px; font-weight: bold; background: transparent;")
        bar_layout.addWidget(title_label)
        bar_layout.addStretch()
        exit_btn = QPushButton("↩️ 退出全屏")
        exit_btn.setStyleSheet(
            "QPushButton { background: transparent; color: white; border: 1px solid #fff; "
            "border-radius: 6px; padding: 8px 18px; }"
            "QPushButton:hover { background: rgba(255,255,255,0.2); }"
        )
        exit_btn.clicked.connect(self._exit_zen_mode)
        bar_layout.addWidget(exit_btn)
        overlay_layout.addWidget(bar)

        # 移动终端面板到浮层
        self.ai_terminal_panel.setParent(self._zen_overlay)
        overlay_layout.addWidget(self.ai_terminal_panel, stretch=1)
        self.ai_zen_enter_btn.setVisible(False)

    def _exit_zen_mode(self) -> None:
        """退出全屏沉浸模式，恢复终端面板到右侧面板"""
        if not hasattr(self, '_zen_overlay'):
            return

        # 先从 overlay 中取出终端面板再删除 overlay
        self._zen_overlay.layout().removeWidget(self.ai_terminal_panel)
        self.ai_terminal_panel.setParent(None)

        self._zen_overlay.hide()
        self._zen_overlay.deleteLater()
        del self._zen_overlay

        # 将终端面板放回右侧面板布局
        self.ai_terminal_panel.setParent(self)
        self._right_panel_layout.addWidget(self.ai_terminal_panel)

        self.ai_zen_enter_btn.setVisible(True)

    # ═══════════════════════════════════════════════
    # 辅助
    # ═══════════════════════════════════════════════

    def _find_main(self):
        """沿 parent 链查找主窗口"""
        p = self.parent()
        while p is not None:
            if hasattr(p, 'sampled_file_path'):
                return p
            p = p.parent()
        return None

    def refresh_model_selector(self) -> None:
        """外部调用：刷新模型列表"""
        self._load_models_config()

    def get_multi_report(self) -> str:
        return self._multi_report
