"""AI 诊断页面 — 从 main.py DataProcessorWindow 抽离的独立 QWidget

包含：模型管理、流式推理、多智能体诊断、禅模式、AI 聊天
通过 parent() 链访问主窗口的 ollama_client 等共享资源。
"""

from __future__ import annotations

import json
import os
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
        """启动后自动测延迟 — 无弹窗，纯后台线程，结果只更新标签."""
        if not hasattr(self, '_online_config') or not self._online_config.get('api_key'):
            return
        self._set_latency('-- ms')
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
            self._test_thread.finished.connect(
                lambda r: self._set_latency(f"{int((time.time() - t0) * 1000)} ms")
            )
            self._test_thread.error.connect(lambda e: print(f"[AI诊断] 启动延迟测试失败: {e}"))
            self._test_thread.start()
        except Exception as e:
            print(f"[AI诊断] 启动延迟测试异常: {e}")

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

        context = self._build_data_context()
        if not context:
            self._warn(self, '请先加载数据文件')
            return

        prompt = f"""作为数据分析专家，请对以下传感器数据进行分析诊断：

{context}

请从以下几个维度进行分析：
1. 数据质量评估
2. 异常模式识别
3. 传感器性能评估
4. 数据处理建议
5. 总体结论

请使用中文，输出结构化的诊断报告。"""

        self.ai_diagnosis_result.clear()
        self.ai_terminal_title.setText("AI 诊断结果 (生成中...)")
        QApplication.processEvents()

        try:
            from py.online_llm_thread import OnlineLlamaGenerateThread
            self._diagnosis_thread = OnlineLlamaGenerateThread(
                api_key=self._online_config['api_key'],
                base_url=self._online_config['base_url'],
                model_name=self._online_config['model_name'],
                prompt=prompt,
                temperature=self._online_config.get('temperature', 0.7),
                max_tokens=self._online_config.get('max_tokens', 2048),
                system_prompt=self._online_config.get('system_prompt', ''),
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

        context = self._build_data_context()
        user_input = f"""请分析以下传感器数据:\n\n{context}\n\nCSV文件路径: {csv_path or '未指定'}"""

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
            )
            report = (
                f"## 数据科学家报告\n\n{result.get('data_scientist_report', '无')}\n\n"
                f"---\n\n## 审查结果: {result.get('audit_result', '未知')}\n\n"
                f"---\n\n## 首席专家报告\n\n{result.get('chief_scientist_report', '无')}\n\n"
                f"---\n\n## 最终报告\n\n{result.get('final_report', '无')}"
            )
            self._multi_report = report
            self.ai_diagnosis_result.setMarkdown(report)
            self.ai_terminal_title.setText("多智能体诊断结果")
        except Exception as e:
            self.ai_diagnosis_result.setPlainText(f'多智能体诊断失败: {e}')
            self.ai_terminal_title.setText("多智能体诊断 (失败)")

    def _build_data_context(self) -> str:
        """构建发送给AI的数据上下文摘要"""
        main_win = self._find_main()
        if not main_win:
            return ""

        parts = []
        # 基础数据信息
        data = getattr(main_win, 'current_data', None)
        if data is not None and not data.empty:
            parts.append(f"数据规模: {len(data)} 行 × {len(data.columns)} 列")
            parts.append(f"列名: {', '.join(str(c) for c in data.columns)}")
            parts.append(f"数据预览 (前5行):\n{data.head(5).to_string()}")

        # 传感器结果
        sensor_results = getattr(main_win, 'sensor_results', {})
        if sensor_results:
            parts.append("\n传感器计算结果:")
            for sid, vals in sensor_results.items():
                clean = [v for v in vals if v is not None]
                if clean:
                    parts.append(f"  {sid}: 有效值={len(clean)}/{len(vals)}, "
                               f"范围=[{min(clean):.4f}, {max(clean):.4f}]")

        # 需求文件
        if self.ai_req_files:
            parts.append("\n需求文件内容:")
            for rf in self.ai_req_files:
                try:
                    with open(rf, 'r', encoding='utf-8') as f:
                        parts.append(f.read()[:2000])
                except Exception:
                    pass

        return '\n'.join(parts) if parts else ""

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
