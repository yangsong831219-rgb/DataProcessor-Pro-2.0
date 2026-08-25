"""AI 诊断页面 — 从 main.py DataProcessorWindow 抽离的独立 QWidget

包含：模型管理、流式推理、多智能体诊断、禅模式、AI 聊天
通过 parent() 链访问主窗口的共享资源。
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
    QApplication, QCheckBox, QComboBox, QDialog, QFileDialog,
    QFrame, QGroupBox, QHBoxLayout, QLabel, QLineEdit,
    QListWidget, QMessageBox, QPushButton, QSizePolicy, QSplitter,
    QStackedWidget, QTextEdit, QVBoxLayout, QWidget,
)


# ═══════════════════════════════════════════════════════════════════
# 统一流式推理线程 — 封装 AIClient.generate_stream()
# ═══════════════════════════════════════════════════════════════════


class AIClientStreamThread(QThread):
    """基于 AIClient.generate_stream() 的流式推理线程。

    替代旧的 OnlineLlamaGenerateThread，统一使用 AIClient 后端路由。
    支持：流式 token 发射 / TTFB 测速 / 取消。

    Signals:
        token_received(str): 逐 token 发射（经批处理）
        finished(str): 完成时发射完整响应
        error(str): 错误时发射错误消息
        latency_tested(int): 连接延迟（毫秒，health check 往返），-1 表示失败
    """

    token_received = pyqtSignal(str)
    finished = pyqtSignal(str)
    error = pyqtSignal(str)
    latency_tested = pyqtSignal(int)

    def __init__(
        self,
        prompt: str = "",
        system_prompt: str = "",
        temperature: float = 0.7,
        max_tokens: int = 2048,
        *,
        enable_thinking: bool = False,
        latency_mode: bool = False,
    ) -> None:
        super().__init__()
        self.prompt = prompt
        self.system_prompt = system_prompt
        self.temperature = temperature
        self.max_tokens = max_tokens
        self.enable_thinking = enable_thinking
        self.latency_mode = latency_mode
        self._cancelled = False

    def cancel(self) -> None:
        """请求取消 — 设标志 + 关闭底层 HTTP 流（释放 llama-server slot）。"""
        self._cancelled = True
        from core.ai_client import AIClient
        AIClient.get_instance().cancel_current_stream()  # 关闭连接，server 停止生成

    def run(self) -> None:
        if self.latency_mode:
            self._run_latency_test()
        else:
            self._run_stream()

    def _run_latency_test(self) -> None:
        """轻量 TTFB 测速 — 通过 AIClient.health_check() 测量网络延迟。

        不触发 GPU 推理，仅测量 DNS+TCP+TLS+HTTP 往返。
        """
        from core.ai_client import AIClient
        t0 = time.time()
        try:
            ai = AIClient.get_instance()
            ai.health_check(timeout=5.0)
            elapsed = int((time.time() - t0) * 1000)
            self.latency_tested.emit(elapsed)
        except Exception:
            self.latency_tested.emit(-1)

    def _run_stream(self) -> None:
        from core.ai_client import AIClient
        from core.ai_errors import AIClientError, AIClientTruncationError
        try:
            ai = AIClient.get_instance()
            full_response = ""
            buffer = ""

            # ── 思考模式 auto-scale：Qwen3.5 thinking 变体消耗大 ──
            _mt = self.max_tokens
            if self.enable_thinking and _mt < 8192:
                _mt = 8192

            for token in ai.generate_stream(
                prompt=self.prompt,
                system_prompt=self.system_prompt,
                temperature=self.temperature,
                max_tokens=self.max_tokens,
                enable_thinking=self.enable_thinking,
            ):
                if self._cancelled:
                    if full_response:
                        self.finished.emit(full_response + "\n[已取消]")
                    else:
                        self.error.emit("生成已取消")
                    return

                full_response += token
                buffer += token
                # 批处理：3 字或标点就发射（减少信号开销）
                if len(buffer) >= 3 or any(p in buffer for p in ['\n', '。', '！', '？', '，', '.', '!', '?']):
                    self.token_received.emit(buffer)
                    buffer = ""

            # 清空残余缓冲
            if buffer:
                self.token_received.emit(buffer)

            if full_response and len(full_response.strip()) > 0:
                self.finished.emit(full_response)
            else:
                self.error.emit("模型未返回有效响应")

        except AIClientError as e:
            self.error.emit(str(e.message) if hasattr(e, 'message') else str(e))
        except Exception as e:
            self.error.emit(f"AI 请求失败: {e}")


# ═══════════════════════════════════════════════════════════════════
# 共享 QSS — QComboBox 统一样式
# ═══════════════════════════════════════════════════════════════════

_COMBO_QSS = (
    "QComboBox {"
    "  border: 1px solid #d9d9d9; border-radius: 4px;"
    "  padding: 6px 12px; background: white;"
    "  font-size: 13px; color: #333;"
    "  min-height: 22px;"
    "}"
    "QComboBox:hover { border-color: #40a9ff; }"
    "QComboBox:focus { border-color: #1890ff; }"
    "QComboBox::drop-down {"
    "  subcontrol-origin: padding; subcontrol-position: top right;"
    "  width: 24px; border-left: 1px solid #d9d9d9;"
    "  border-top-right-radius: 3px; border-bottom-right-radius: 3px;"
    "}"
    "QComboBox QAbstractItemView {"
    "  border: 1px solid #d9d9d9; background: white;"
    "  selection-background-color: #e6f7ff; selection-color: #333;"
    "  outline: none; padding: 4px; min-height: 28px;"
    "  font-size: 13px;"
    "}"
    "QComboBox QAbstractItemView::item {"
    "  min-height: 30px; padding: 6px 12px;"
    "}"
    "QComboBox QAbstractItemView::item:hover {"
    "  background-color: #f0f2f5;"
    "}"
    "QComboBox QAbstractItemView::item:selected {"
    "  background-color: #e6f7ff; color: #333;"
    "}"
)

# 双结果切换按钮样式
_TOGGLE_ON_QSS = (
    "QPushButton { padding: 7px 14px; border: 2px solid #1890ff; border-radius: 6px;"
    "  background: #1890ff; color: white; font-weight: bold; font-size: 12px; }"
    "QPushButton:hover { background: #40a9ff; }"
)
_TOGGLE_OFF_QSS = (
    "QPushButton { padding: 7px 14px; border: 1px solid #d9d9d9; border-radius: 6px;"
    "  background: white; color: #666; font-size: 12px; }"
    "QPushButton:hover { border-color: #1890ff; color: #1890ff; }"
)


# ═══════════════════════════════════════════════════════════════════
# AiDiagnosisWidget
# ═══════════════════════════════════════════════════════════════════


class AiDiagnosisWidget(QWidget):
    """AI 诊断页面 — 40:60 双栏仪表盘布局

    通过 parent() 链自动查找主窗口的 online_llm_config、共享资源等。
    """

    # Signals
    status_changed = pyqtSignal(str, str)  # (text, color)
    diagnosis_complete = pyqtSignal(str)   # result text

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.ai_req_files: List[str] = []
        self._source_checkboxes: dict[str, tuple[QCheckBox, Any]] = {}  # name → (checkbox, provider)
        self._ai_models_config: Dict[str, dict] = {}
        self._online_thread: Optional[QThread] = None

        # 双诊断结果分开保留
        self._ai_result: dict | None = None        # {json, raw} | None
        self._multi_result: dict | None = None      # {chief_structured, data_scientist_text, audit_advisory, logs} | None
        self._external_files: list[str] = []         # 外部提交的数据文件路径
        self._active_result = "ai"                  # "ai" | "multi" — 当前显示哪份

        self._diagnosis_record: dict | None = None  # 供保存和报告引擎消费
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

        self.ai_latency_label = QLabel("连接: -- ms")
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

        # ── 数据源选择区 ──
        layout.addWidget(self._build_source_group())
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
        lst.setMaximumHeight(80)  # 限制垂直空间，让出给数据源按钮
        lst.setStyleSheet("""
            QListWidget { border: 1px solid #ebebeb; border-radius: 4px; background: white; }
            QListWidget::item { padding: 8px; border-bottom: 1px solid #f5f5f5; }
            QListWidget::item:selected { background: #e6f4ff; }
        """)
        for f in files:
            lst.addItem(f)
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

        # ── 后端开关行 (在线/本地) ──
        be_row = QHBoxLayout()
        be_row.setSpacing(8)
        be_row.addWidget(QLabel("后端:"))
        self.backend_combo = QComboBox()
        self.backend_combo.addItems(["在线 (DeepSeek)", "本地 (llama.cpp/Ollama)"])
        self.backend_combo.setMinimumHeight(36)
        self.backend_combo.setStyleSheet(_COMBO_QSS)
        self.backend_combo.currentIndexChanged.connect(self._on_backend_changed)
        be_row.addWidget(self.backend_combo, stretch=1)
        layout.addLayout(be_row)

        # ── 模型选择行 ──
        row = QHBoxLayout()
        row.setSpacing(8)
        row.addWidget(QLabel("模型:"))
        self.ai_model_combo = QComboBox()
        self.ai_model_combo.setMinimumHeight(36)
        self.ai_model_combo.setStyleSheet(_COMBO_QSS)
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

    def _build_source_group(self) -> QGroupBox:
        """数据源复选框 — 勾选哪些模块的数据注入 prompt。"""
        from core.data_providers import get_all_providers

        group = self._st_group("数据源（注入诊断上下文）", "#722ed1")
        # 允许 group 随内容垂直扩展 (防按钮叠压)
        group.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        layout = QVBoxLayout(group)
        layout.setSpacing(6)

        hint = QLabel("勾选已运行、有数据的模块，其摘要将注入诊断 prompt")
        hint.setStyleSheet("color: #888; font-size: 11px;")
        layout.addWidget(hint)

        providers = get_all_providers()
        main_win = self._find_main()
        for p in providers:
            cb = QCheckBox(p.display_name)
            cb.setStyleSheet("font-size: 13px; padding: 2px 0;")
            self._sync_checkbox_state(cb, p, main_win)
            layout.addWidget(cb)
            self._source_checkboxes[p.display_name] = (cb, p)

        # ── 外部文件状态标签 ──
        self._external_files_label = QLabel("未加载外部文件")
        self._external_files_label.setStyleSheet("color: #888; font-size: 11px;")
        layout.addWidget(self._external_files_label)

        # ── 提交外部数据文件按钮 (竖排, 各占独立行) ──
        self.submit_external_btn = QPushButton("提交外部数据文件")
        self.submit_external_btn.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.submit_external_btn.setMinimumHeight(38)
        self.submit_external_btn.setStyleSheet(
            "QPushButton { background-color: #722ed1; color: white; border: none; "
            "border-radius: 4px; padding: 9px 16px; font-weight: bold; font-size: 13px; }"
            "QPushButton:hover { background-color: #9254de; }"
        )
        self.submit_external_btn.clicked.connect(self._on_submit_external_files)
        layout.addWidget(self.submit_external_btn)
        # 按钮间 spacer 确保独立行 (6px)
        layout.addSpacing(6)

        self.clear_external_btn = QPushButton("清除")
        self.clear_external_btn.setSizePolicy(
            QSizePolicy.Policy.Minimum, QSizePolicy.Policy.Fixed)
        self.clear_external_btn.setMinimumHeight(36)
        self.clear_external_btn.setStyleSheet(
            "QPushButton { background: #fff; border: 1px solid #d9d9d9; "
            "border-radius: 4px; padding: 8px 16px; font-size: 12px; }"
            "QPushButton:hover { border-color: #ff4d4f; color: #ff4d4f; }"
        )
        self.clear_external_btn.clicked.connect(self._on_clear_external_files)
        layout.addWidget(self.clear_external_btn)

        return group

    def _sync_checkbox_state(self, cb: QCheckBox, provider: Any, main_win: Any) -> None:
        """将单个复选框的状态同步到 provider.is_available()。"""
        available = provider.is_available(main_win) if main_win else False
        cb.setEnabled(available)
        if not available:
            hint = getattr(provider, 'disabled_hint', '') or '暂不可用'
            cb.setText(f"{provider.display_name} ({hint})")
            cb.setToolTip(hint)
            cb.setChecked(False)  # 不可用时取消勾选
        else:
            cb.setText(provider.display_name)
            cb.setToolTip("")

    def _refresh_source_checkboxes(self) -> None:
        """重评所有 provider.is_available()，刷新复选框状态。

        在页面首次显示、标签切换、或数据/标定/清洗/对比变更时调用。
        """
        from core.data_providers import get_all_providers
        main_win = self._find_main()
        providers = get_all_providers()
        for p in providers:
            entry = self._source_checkboxes.get(p.display_name)
            if entry is None:
                continue
            cb, _p = entry
            self._sync_checkbox_state(cb, p, main_win)

    def showEvent(self, event: Any) -> None:
        """页面变为可见时刷新 provider 复选框状态。"""
        super().showEvent(event)
        self._refresh_source_checkboxes()

    def _build_action_group(self) -> QGroupBox:
        group = self._st_group("执行诊断", "#52c41a")
        layout = QVBoxLayout(group)
        layout.setSpacing(10)
        layout.addWidget(self._make_large_btn('数据分析专家诊断模式', '#1890ff', '#40a9ff', self._run_diagnosis))
        layout.addWidget(self._make_large_btn('专家组诊断模式', '#52c41a', '#73d13d', self._run_multi_agent))
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

        # ── 双结果切换按钮 ──
        self.result_toggle_ai = QPushButton("AI诊断结果")
        self.result_toggle_ai.setEnabled(False)
        self.result_toggle_ai.setStyleSheet(_TOGGLE_ON_QSS)
        self.result_toggle_ai.clicked.connect(self._switch_to_ai_result)
        layout.addWidget(self.result_toggle_ai)

        self.result_toggle_multi = QPushButton("专家组诊断结果")
        self.result_toggle_multi.setEnabled(False)
        self.result_toggle_multi.setStyleSheet(_TOGGLE_OFF_QSS)
        self.result_toggle_multi.clicked.connect(self._switch_to_multi_result)
        layout.addWidget(self.result_toggle_multi)

        clr = QPushButton("清除内容")
        clr.setStyleSheet(
            "QPushButton { padding: 7px 18px; border:1px solid #d9d9d9; "
            "border-radius:6px; background:#fff; }"
            "QPushButton:hover { border-color:#ff4d4f; color:#ff4d4f; }"
        )
        clr.clicked.connect(self.ai_diagnosis_result.clear)
        layout.addWidget(clr)
        layout.addStretch()

        self.save_ai_btn = QPushButton("保存AI诊断")
        self.save_ai_btn.setStyleSheet(_TOGGLE_OFF_QSS)
        self.save_ai_btn.setEnabled(self._ai_result is not None)
        self.save_ai_btn.clicked.connect(self._save_ai)
        layout.addWidget(self.save_ai_btn)

        self.save_multi_btn = QPushButton("保存专家组诊断")
        self.save_multi_btn.setStyleSheet(_TOGGLE_OFF_QSS)
        self.save_multi_btn.setEnabled(self._multi_result is not None)
        self.save_multi_btn.clicked.connect(self._save_multi)
        layout.addWidget(self.save_multi_btn)

        self.save_to_project_btn = QPushButton("保存诊断到项目")
        self.save_to_project_btn.setStyleSheet(
            "QPushButton { background-color: #722ed1; color: white; padding: 8px 12px; "
            "border-radius: 4px; font-weight: bold; }"
            "QPushButton:hover { background-color: #9254de; }"
        )
        self.save_to_project_btn.clicked.connect(self._save_diagnosis_to_project)
        layout.addWidget(self.save_to_project_btn)

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
    # 文件管理 (仅需求文件)
    # ═══════════════════════════════════════════════

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
                    raw = json.load(f)
                # 仅保留模型条目（跳过 _ 前缀的系统键，如 _backend / _local）
                self._ai_models_config = {
                    k: v for k, v in raw.items()
                    if not k.startswith('_') and isinstance(v, dict) and 'base_url' in v
                }
            self._refresh_model_selector()
            self._auto_connect()
        except Exception as e:
            print(f"加载AI模型配置失败: {e}")

    def _save_models_config(self) -> None:
        try:
            cfg_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'ai_models_config.json')
            # 读-改-写：保留文件中的系统键（_backend / _local / _comment）
            existing: dict = {}
            if os.path.exists(cfg_path):
                with open(cfg_path, 'r', encoding='utf-8') as f:
                    existing = json.load(f)
            # 覆盖模型条目，保留系统键
            for k, v in existing.items():
                if k.startswith('_'):
                    self._ai_models_config[k] = v  # 回填系统键用于写出
            with open(cfg_path, 'w', encoding='utf-8') as f:
                json.dump(self._ai_models_config, f, ensure_ascii=False, indent=2)
            # 写完后移除系统键，保持 _ai_models_config 纯净
            for k in list(self._ai_models_config.keys()):
                if k.startswith('_'):
                    del self._ai_models_config[k]
        except Exception as e:
            print(f"保存AI模型配置失败: {e}")

    def _refresh_model_selector(self) -> None:
        self.ai_model_combo.blockSignals(True)
        self.ai_model_combo.clear()

        from core.ai_client import AIClient
        backend = AIClient.get_backend()

        if backend == 'local':
            lc = AIClient.get_local_config()
            local_name = lc.get('model_name', 'qwen3.5-9b')
            self.ai_model_combo.addItem(local_name)
            self.ai_model_combo.setToolTip(
                f"lama.cpp/Ollama — {lc.get('base_url', 'http://127.0.0.1:8080/v1')}"
            )
        else:
            # 在线：列所有非 _ 前缀的模型
            model_names = [k for k in self._ai_models_config if not k.startswith('_')]
            if not model_names:
                self.ai_model_combo.addItem('(未配置模型)')
                self.ai_model_combo.blockSignals(False)
                return
            self.ai_model_combo.addItems(model_names)

        self.ai_model_combo.blockSignals(False)

    def _auto_connect(self) -> None:
        # 同步后端 UI
        self._sync_backend_ui()

        # 自动连接：本地后端无需额外操作，仅在线需 api_key
        from core.ai_client import AIClient
        if AIClient.get_instance().backend == 'local':
            self._set_status("● 本地模式", '#52c41a', '#f6ffed')
            if hasattr(self, 'ai_model_combo') and self._ai_models_config:
                self._online_config = {
                    'api_key': 'local', 'temperature': 0.7,
                    'max_tokens': 4096,
                    'system_prompt': '你是一个专业的设备与系统 AI 诊断专家。',
                }
            self._auto_test_latency()
            return

        if self._ai_models_config:
            model_names = [k for k in self._ai_models_config if not k.startswith('_')]
            if model_names:
                first_name = model_names[0]
                config = self._ai_models_config[first_name]
                self._connect_with_config(config)
                # 启动后自动测试延迟
                if hasattr(self, '_online_config') and self._online_config:
                    self._auto_test_latency()

    def _auto_test_latency(self) -> None:
        """启动后自动测延迟 — 后台线程 TTFB，经 AIClient.health_check() 轻量探针。"""
        if not hasattr(self, '_online_config') or not self._online_config:
            return
        self._set_latency('-- ms')
        try:
            self._test_thread = AIClientStreamThread(latency_mode=True)
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
        from core.ai_client import AIClient
        name = self.ai_model_combo.currentText()

        if AIClient.get_backend() == 'local':
            # local: 用 _local 配置
            lc = AIClient.get_local_config()
            self._connect_with_config({
                'api_key': '', 'base_url': lc['base_url'],
                'model_name': lc['model_name'],
                'temperature': 0.7, 'max_tokens': 4096,
                'system_prompt': lc.get('system_prompt', ''),
            })
        elif name in self._ai_models_config:
            self._connect_with_config(self._ai_models_config[name])

    def _on_model_config(self) -> None:
        main_win = self._find_main()
        if not main_win:
            return
        try:
            from ui.ai_model_config_dialog import AIModelConfigDialog
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
        """从模型配置连接 — 存储配置副本用于 UI 状态，同时写回 AIClient 单例。"""
        api_key = config.get('api_key', '')
        base_url = config.get('base_url', '')
        model_name = config.get('model_name', '')

        from core.ai_client import AIClient
        ai = AIClient.get_instance()

        # local 后端不需 api_key，online 仍需
        if ai.backend != 'local' and not api_key:
            self._set_status("● 未配置API密钥", '#ff4d4f', '#fff1f0')
            return

        try:
            self._online_config = {
                'api_key': api_key, 'base_url': base_url,
                'model_name': model_name, 'temperature': config.get('temperature', 0.7),
                'max_tokens': config.get('max_tokens', 2048),
                'system_prompt': config.get('system_prompt', ''),
            }
            # ★ 就地写回单例 — 统一诊断/报告配置源 (不 reset _instance)
            if ai.backend != 'local':
                ai.configure_online(
                    api_key, base_url, model_name,
                    max_tokens=int(config.get('max_tokens', 4096)),
                    temperature=float(config.get('temperature', 0.3)),
                )
            self._set_status("● 已连接", '#52c41a', '#f6ffed')
            self._set_latency('-- ms')
            print(f"[AI诊断] 已连接模型: {model_name} (backend={ai.backend})")
        except Exception as e:
            self._set_status(f"● 连接失败: {e}", '#ff4d4f', '#fff1f0')

    def _on_test_model(self) -> None:
        """测试模型连通性 — 经 AIClientStreamThread 发送简 prompt，测延迟。"""
        if not hasattr(self, '_online_config') or not self._online_config:
            self._warn(self, '请先连接模型')
            return
        self._set_status("● 测试中...", '#faad14', '#fffbe6')
        QApplication.processEvents()
        try:
            t0 = time.time()
            self._test_thread = AIClientStreamThread(
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

    # ═══════════════════════════════════════════════════════════
    # 后端切换
    # ═══════════════════════════════════════════════════════════

    def _on_backend_changed(self, index: int) -> None:
        """用户切换后端 → 写回 SSOT + 刷新模型列表 + 健康检查."""
        backend = 'online' if index == 0 else 'local'
        self._set_backend_config(backend)
        self._refresh_model_selector()  # 按后端过滤模型列表

        if backend == 'local':
            self._set_status("● 检测中...", '#faad14', '#fffbe6')
            QApplication.processEvents()
            try:
                from core.ai_client import AIClient
                AIClient._instance = None
                ai = AIClient.get_instance()
                ai.health_check(timeout=5.0)
                lc = AIClient.get_local_config()
                mname = lc.get('model_name', 'qwen3.5-9b')
                self._set_status(f"● 本地 · {mname}", '#52c41a', '#f6ffed')
                self._online_config = {
                    'api_key': 'local', 'base_url': ai.base_url,
                    'model_name': mname,
                    'temperature': 0.7, 'max_tokens': 4096,
                    'system_prompt': '你是一个专业的设备与系统 AI 诊断专家。',
                }
                self._auto_test_latency()
            except Exception as e:
                msg = str(getattr(e, 'message', str(e)))
                self._set_status("● 本地服务未启动", '#ff4d4f', '#fff1f0')
                print(f"[AI诊断] 本地后端 health check 失败: {msg}")
        else:
            self._set_status("● 在线模式", '#1890ff', '#f6ffed')

    def _set_backend_config(self, backend: str) -> None:
        """写入 _backend 到 SSOT (ai_models_config.json)."""
        import json, os
        cfg_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'ai_models_config.json')
        cfg: dict = {}
        if os.path.exists(cfg_path):
            with open(cfg_path, 'r', encoding='utf-8') as f:
                cfg = json.load(f)
        cfg['_backend'] = backend
        cfg.setdefault('_local', {})
        with open(cfg_path, 'w', encoding='utf-8') as f:
            json.dump(cfg, f, ensure_ascii=False, indent=2)
        # 强制重置 AIClient 单例，下次调用读新配置
        from core.ai_client import AIClient
        AIClient._instance = None

    def _sync_backend_ui(self) -> None:
        """从 SSOT 同步后端下拉框状态."""
        from core.ai_client import AIClient
        try:
            be = AIClient.get_backend()
            self.backend_combo.blockSignals(True)
            self.backend_combo.setCurrentIndex(0 if be == 'online' else 1)
            self.backend_combo.blockSignals(False)
        except Exception:
            pass

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
        from core.ai_client import AIClient
        if not AIClient.get_instance().is_available():
            self._warn(self, '请先配置或连接AI模型')
            return

        ctx = self._build_data_context()
        # Guard: 内部 data 或任一已勾选 provider 可用 (含外部数据文件) 即放行
        has_internal = bool(ctx.get("has_data"))
        has_external = bool(self._external_files)
        if ctx.get("error") or (not has_internal and not has_external):
            self._warn(self, '请先加载数据文件')
            return

        # 构建增强版系统提示词（含数据类型防火墙、模板状态、清洗统计、JSON schema）
        system_prompt = self._build_system_prompt(ctx)
        # 用户消息 = 纯数据上下文（不含 schema 约束，schema 在系统提示词中）
        user_prompt = self._build_context_text(ctx)

        # ★ max_tokens 动态计算: 按 ctx − prompt − margin 拉满可用空间
        from core.ai_client import AIClient
        _ai = AIClient.get_instance()
        _prompt_est = _ai._estimate_tokens(system_prompt) + _ai._estimate_tokens(user_prompt)
        _ctx = _ai._get_context_size()
        _mt = _ai._safe_max_tokens(system_prompt, user_prompt, _ctx)  # 拉满: 等于 available_for_output
        print(f"[DIAG] _run_diagnosis: backend={_ai.backend}, model={_ai.model_name}, "
              f"ctx={_ctx}, max_tokens={_mt}, prompt_est_tokens={_prompt_est}, "
              f"available_for_output={_ctx - _prompt_est - 512}")

        self._ai_result = None
        self._update_result_buttons()
        self.ai_diagnosis_result.clear()
        self.ai_terminal_title.setText("AI 诊断结果 (生成中...)")
        QApplication.processEvents()

        try:
            self._diagnosis_thread = AIClientStreamThread(
                prompt=user_prompt,
                system_prompt=system_prompt,
                temperature=self._online_config.get('temperature', 0.7) if hasattr(self, '_online_config') and self._online_config else 0.7,
                max_tokens=_mt,
                enable_thinking=False,  # 诊断默认非思考（完整答案；想推理可切换）
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
        # ★ DIAG: 检查 thinking 链是否出现在正式内容中
        _think_count = (result or '').count('<think>')
        _think_end_count = (result or '').count('</think>')
        print(f"[DIAG] _on_diag_done: result_len={len(result or '')}, "
              f"think_tags=<think>{_think_count} </think>{_think_end_count}, "
              f"result_head={(result or '')[:200]!r}")
        # 尝试解析 JSON 并渲染结构化报告
        parsed = self._extract_json(result)
        if parsed:
            try:
                kb_hits = self._get_kb_hits()
                html = self._render_chief_conclusions_html(parsed, kb_hits, kind="ai")
                self.ai_diagnosis_result.setHtml(html)
            except Exception:
                self.ai_diagnosis_result.setPlainText(result)
        else:
            self.ai_diagnosis_result.setPlainText(result)
        self.ai_terminal_title.setText("AI 诊断结果")
        self.diagnosis_complete.emit(result)

        # ── 留存 AI 诊断结果 (不覆盖多智能体结果) ──
        self._ai_result = {"json": parsed, "raw": result}
        self._active_result = "ai"
        self._update_result_buttons()

        # ── 留存 DiagnosisRecord (供保存和报告引擎消费) ──
        self._diagnosis_record = self._build_diagnosis_record()
        if hasattr(self, 'save_analysis_btn'):
            self.save_analysis_btn.setEnabled(self._ai_result is not None or self._multi_result is not None)  # pyright: ignore[reportAttributeAccessIssue] # legacy compat

    def _build_diagnosis_record(self) -> dict:
        """构建稳定 schema 的诊断记录（与 ③c 报告引擎的契约）。

        schema_version 1.2: 支持 ai_diagnosis、multi_agent 与 chart_data。
        """
        from datetime import datetime
        from core.ai_client import AIClient

        ai = AIClient.get_instance()
        rec: dict[str, Any] = {
            "schema_version": "1.2",
            "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "backend": ai.backend,
            "model": ai.model_name,
            "selected_sources": self._get_selected_source_names(),
        }

        # 数据源摘要快照 (provider summaries)
        try:
            main_win = self._find_main()
            if main_win:
                rec["data_source_snapshot"] = self._get_enabled_provider_summaries(main_win)
            else:
                rec["data_source_snapshot"] = []
        except Exception:
            rec["data_source_snapshot"] = []

        # KB 命中规则
        rec["kb_hits"] = self._get_kb_hits()

        # ── AI 诊断段 ──
        ai_res = self._ai_result or {}
        rec["ai_diagnosis"] = {
            "diagnosis_json": ai_res.get("json"),
            "diagnosis_raw": (ai_res.get("raw") or "")[:5000],
        }

        # ── ChartData 段 (1.2): 统一画图数据束 ──
        try:
            from core.chart_bundle import ChartBundle
            bundle = ChartBundle.from_providers(main_win)
            print(f"[DIAG] _build_diagnosis_record ChartBundle.from_providers: "
                  f"series={len(bundle.series)}, time_h={len(bundle.time_h)}, "
                  f"phys_series_n={len(bundle.phys_series)}, "
                   f"series_delta_n={len(bundle.series_delta)}, "
                   f"calib_sensors_n={len(bundle.calib_sensors)}, "
                   f"temp_regressions_n={len(bundle.temp_regressions)}, "
                   f"phaseb_diagnostics_n={len(bundle.phaseb_diagnostics)}, "
                   f"tables(grade={bool(bundle.grade_table_md)},"
                   f"cleaning={bool(bundle.cleaning_table_md)},"
                   f"anomaly={bool(bundle.anomaly_table_md)},"
                  f"ke={bool(bundle.ke_table_md)},"
                  f"dec={bool(bundle.decoupling_table_md)})")
            if (bundle.series or bundle.compare_sources or bundle.calib_sensors
                    or bundle.phys_series or bundle.series_delta
                    or bundle.temp_regressions or bundle.phaseb_diagnostics
                    or bundle.cleaning_table_md
                    or bundle.grade_table_md or bundle.anomaly_table_md
                    or bundle.ke_table_md or bundle.decoupling_table_md):
                rec["chart_data"] = bundle.to_dict()
                print(f"[DIAG] _build_diagnosis_record: chart_data written to record")
            else:
                print(f"[DIAG] _build_diagnosis_record: chart_data EMPTY — skipped")
        except Exception:
            import traceback
            print(f"[DIAG] _build_diagnosis_record ChartBundle section FAILED")
            traceback.print_exc()

        # ── 多智能体诊断段 (Phase 6: 线性顾问式 + 截断标记) ──
        ma_res = self._multi_result or {}
        rec["multi_agent"] = {
            "chief_structured": ma_res.get("chief_structured"),
            "data_scientist_text": ma_res.get("data_scientist_text", ""),
            "audit_advisory": ma_res.get("audit_advisory", ""),
            "logs": ma_res.get("logs", []),
            "chief_truncated": ma_res.get("chief_truncated", False),
            # 向后兼容旧字段 (旧版 record 加载时用)
            "report": "",  # 不再预拼接 markdown
            "raw_json": {},  # 不再存原始 dict
        }

        return rec

    def _save_diagnosis_to_project(self) -> None:
        """保存诊断结果到项目：JSON→数据/、图PNG→图片/。"""
        if not self._ai_result and not self._multi_result:
            self._warn(self, "暂无诊断结果可保存")
            return

        # ★ 完整性守卫：截断/失败诊断 → 提示用户，不静默保存
        is_truncated = False
        fail_reasons: list[str] = []
        if self._multi_result:
            if self._multi_result.get("chief_truncated"):
                is_truncated = True
                fail_reasons.append("专家组诊断输出被截断（内容不完整）")
            ds = self._multi_result.get("data_scientist_text", "") or ""
            if not ds.strip():
                fail_reasons.append("数据科学家未产出有效分析")
            chief = self._multi_result.get("chief_structured") or {}
            if not chief or not isinstance(chief, dict) or not chief.get("diagnosis_summary"):
                fail_reasons.append("首席专家未产出结构化诊断结论")
        if self._ai_result:
            parsed = self._ai_result.get("json") or {}
            if not parsed or not isinstance(parsed, dict):
                is_truncated = True
                fail_reasons.append("AI 诊断未产出有效 JSON 结果")
        if is_truncated or fail_reasons:
            reasons_text = "\n".join(f"• {r}" for r in fail_reasons) if fail_reasons else ""
            msg = f"当前诊断可能不完整:\n{reasons_text}\n\n确定仍要保存？"
            reply = QMessageBox.question(self, "诊断结果不完整", msg,
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
            if reply != QMessageBox.StandardButton.Yes:
                return

        # ① 构建诊断记录
        kind = "ai" if self._ai_result else "multi"
        rec = self._build_diagnosis_record()
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")

        # ② 选项目
        main_win = self._find_main()
        if not main_win:
            self._warn(self, "无法定位主窗口")
            return

        # 列出已有项目
        proj_dir = getattr(main_win, 'get_project_library_dir', lambda: '')()
        if proj_dir:
            import os as _os
            entries = sorted([
                d for d in _os.listdir(proj_dir)
                if _os.path.isdir(_os.path.join(proj_dir, d))
            ])
        else:
            entries = []

        dlg = QDialog(self)
        dlg.setWindowTitle("保存诊断到项目")
        dlg.setMinimumWidth(450)
        lay = QVBoxLayout(dlg)
        lay.addWidget(QLabel("选择项目文件夹："))

        lst = QListWidget()
        if entries:
            lst.addItems(entries)
        else:
            lst.addItem("(暂无项目 — 将在下方新建)")
        lst.setCurrentRow(0)
        lay.addWidget(lst)

        name_row = QHBoxLayout()
        name_row.addWidget(QLabel("新项目名(可选):"))
        name_input = QLineEdit()
        name_row.addWidget(name_input)
        lay.addLayout(name_row)

        btn_row = QHBoxLayout()
        ok_btn = QPushButton("保存到此项目")
        ok_btn.setStyleSheet("QPushButton { background:#722ed1; color:white; padding:8px 16px; border-radius:4px; font-weight:bold; }")
        cancel_btn = QPushButton("取消")
        btn_row.addWidget(ok_btn)
        btn_row.addWidget(cancel_btn)
        lay.addLayout(btn_row)

        def on_ok():
            import os as _os, shutil
            import numpy as np
            sel = lst.currentItem().text() if lst.currentItem() and entries else ""
            new_name = name_input.text().strip()
            if new_name:
                sel = new_name  # 新建
            if not sel:
                self._warn(self, "请选择或输入项目名")
                return

            target = proj_dir and _os.path.join(proj_dir, sel)
            if not target:
                target = _os.path.join(_os.path.dirname(_os.path.dirname(__file__)), '项目资料库', sel)
            data_dir = _os.path.join(target, '数据', '诊断记录')
            img_dir = _os.path.join(target, '图片')
            _os.makedirs(data_dir, exist_ok=True)  # 包含 诊断记录 子目录
            _os.makedirs(img_dir, exist_ok=True)

            # ── record_id 单一来源: ts (now()#2, 无横杠, 无冒号) ──
            record_id = ts  # ts = "%Y%m%d_%H%M%S", 无需 replace

            # ── 出图到 数据/诊断记录/<记录ID>/charts/ (母本B 甲结构) ──
            from core.chart_store import build_chart_store, chart_manifest_to_dict
            cd = rec.get('chart_data', {}) or {}
            charts_dir = _os.path.join(data_dir, record_id, 'charts')
            wrn: list[str] = []
            chart_manifest = build_chart_store(cd, charts_dir, wrn)
            produced = sum(1 for e in chart_manifest if e.produced)

            # ── 写 rec 持久化字段 → 必须在 json.dump 之前 ──
            rec["record_id"] = record_id
            rec["chart_manifest"] = chart_manifest_to_dict(chart_manifest)

            # ── 写 JSON ──
            json_path = _os.path.join(data_dir, f"诊断记录_{ts}.json")
            with open(json_path, 'w', encoding='utf-8') as f:
                json.dump(rec, f, ensure_ascii=False, indent=2)

            msg = f"已保存到 {sel}:\n  JSON: {_os.path.basename(json_path)}\n  PNG: {produced} 张"
            if wrn:
                msg += f"\n  警告: {'; '.join(wrn[:3])}"
            QMessageBox.information(self, "保存成功", msg)
            dlg.accept()

        ok_btn.clicked.connect(on_ok)
        cancel_btn.clicked.connect(dlg.reject)
        dlg.exec()

    def _get_selected_source_names(self) -> list[str]:
        """返回当前勾选的数据源名称列表。"""
        return [
            name for name, (cb, _p) in self._source_checkboxes.items()
            if cb.isChecked()
        ]

    def _get_kb_hits(self) -> list[dict]:
        """返回当前 KB 命中规则的全文列表 (id/severity/objects/meaning/mechanism/recommendation)。

        设计决策 (③b 收尾): 规则全文存入 JSON，使报告引擎可仅凭该 JSON 重建诊断上下文，
        不再依赖运行时 KB。KB 仅做检索，不做持久来源。
        """
        try:
            from core.diagnosis_kb import get_kb
            kb = get_kb()
            # 复用 _build_kb_rag_context 里已计算的 retrieve
            # ——但 retrieve 的 hits 对象在 assember 调用后丢弃了。
            # 这里重新调用 retrieve 得到原始 KBRule 对象
            sensor_data, multisource_data, cleaning_data = self._build_kb_inputs()
            hits = kb.retrieve(sensor_data, multisource_data, cleaning_data)
            return [
                {
                    "id": rule.id,
                    "severity": rule.severity,
                    "objects": (objs if objs else None),
                    "meaning": rule.meaning,
                    "mechanism": rule.mechanism,
                    "recommendation": rule.recommendation,
                }
                for rule, objs in hits
            ]
        except Exception:
            return []

    def _build_kb_inputs(self) -> tuple[dict, list[dict] | None, dict | None]:
        """构建 KB 检索所需的三个输入（与 _build_kb_rag_context 同源）。"""
        import importlib
        main_win = self._find_main()
        if not main_win:
            return {}, None, None

        # sensor_data
        sensor_data: dict[str, dict] = {}
        ct = getattr(main_win, 'calibration_tab_widget', None)
        tp = getattr(ct, 'temp_page', None) if ct else None
        if tp:
            pb = getattr(tp, '_phase_b_state', {}) or {}
            decoupling = pb.get('decoupling_results', {}) or {}
            compensation = pb.get('compensation', {}) or {}
            ke_table = pb.get('ke_table', {})

            for s_name in set(list(decoupling.keys()) + list(compensation.keys())):
                entry: dict = {}
                dec_d = decoupling.get(s_name, {})
                if isinstance(dec_d, dict):
                    entry['e_std'] = dec_d.get('e_std')
                    entry['e_mean'] = dec_d.get('e_mean')
                    entry['e_range'] = dec_d.get('e_range')
                    entry['is_single_grating'] = bool(dec_d.get('single_grating', False))
                comp_d = compensation.get(s_name, {})
                if isinstance(comp_d, dict):
                    metrics = comp_d.get('metrics')
                    if metrics is not None and not isinstance(metrics, (int, float, str)):
                        entry['residual_sigma_pct_fs'] = getattr(metrics, 'residual_sigma_pct_fs', None)
                        entry['hysteresis_max_pct_fs'] = getattr(metrics, 'hysteresis_max_pct_fs', None)
                        entry['repeatability_pct_fs'] = getattr(metrics, 'repeatability_pct_fs', None)
                        entry['temp_sensitivity_max'] = getattr(metrics, 'temp_sensitivity_max', None)
                        entry['fs'] = getattr(metrics, 'fs', 1000)
                        entry['low_confidence'] = getattr(metrics, 'low_confidence', False)
                        entry['comp_form'] = getattr(metrics, 'comp_form', '')
                    grade = comp_d.get('grade')
                    if grade is not None and not isinstance(grade, (int, float, str)):
                        entry['grade'] = getattr(grade, 'grade', '?')
                        entry['passed'] = bool(getattr(grade, 'passed', False))
                        entry['reasons'] = getattr(grade, 'reasons', []) or []
                ke = ke_table.get(s_name, {})
                if isinstance(ke, dict):
                    entry['Ke1'] = ke.get('Ke1')
                    entry['Ke2'] = ke.get('Ke2')
                if entry:
                    sensor_data[s_name] = entry

        # multisource_data
        multisource_data: list[dict] = []
        cw = getattr(main_win, 'compare_tab_widget', None)
        if cw:
            lc = getattr(cw, '_last_comparison', None)
            if lc and isinstance(lc, dict):
                for p in lc.get('pairs', []):
                    if isinstance(p, dict):
                        multisource_data.append({
                            'corr': p.get('corr'), 'mae': p.get('mae'),
                            'rmse': p.get('rmse'), 'max_dev': p.get('max_error'),
                            'fs': p.get('fs', 1000),
                            'device_a': p.get('device_a', '?'),
                            'device_b': p.get('device_b', '?'),
                        })

        # cleaning_data
        cleaning_data: dict = {}
        clw = getattr(main_win, 'cleaning_tab_widget', None)
        if clw:
            ai = getattr(clw, '_anomaly_info', {}) or {}
            cleaning_data['per_column_count'] = {k: v.get('count', 0) for k, v in ai.items()} if ai else {}
            cleaning_data['cleaning_has_run'] = bool(getattr(clw, '_cleaning_has_run', False))
            try:
                cleaning_data['fill_method'] = clw.get_config().get('fill_method', 'linear')
            except Exception:
                cleaning_data['fill_method'] = None

        return sensor_data, multisource_data or None, cleaning_data or None

    # ═══════════════════════════════════════════════════════════
    # 保存分析结果到项目资料库
    # ═══════════════════════════════════════════════════════════

    # _on_save_analysis 已移除 — 保存由 保存AI诊断/保存多智能体诊断 两个按钮承担


    @staticmethod
    def _append_sensor_section(lines: list[str], diag: dict) -> None:
        """从 diagnosis_json 提取传感器分析段（共用 helper）。"""
        sa = diag.get("sensor_analysis", [])
        if sa:
            lines.append("## 传感器诊断结论")
            lines.append("")
            for s in sa:
                sid = s.get("sensor_id", "?")
                status = s.get("status", "?")
                findings = s.get("findings", "")
                suggestions = s.get("suggestions", "")
                lines.append(f"- **{sid}** ({status}): {findings}")
                if suggestions:
                    lines.append(f"  → {suggestions}")
            lines.append("")

    @pyqtSlot(str)
    def _on_diag_error(self, err: str) -> None:
        self.ai_diagnosis_result.setPlainText(f'诊断失败: {err}')
        self.ai_terminal_title.setText("AI 诊断结果 (失败)")

    def _run_multi_agent(self) -> None:
        from core.ai_client import AIClient
        if not AIClient.get_instance().is_available():
            self._warn(self, '请先配置或连接AI模型')
            return

        main_win = self._find_main()
        csv_path = None
        if main_win:
            csv_path = getattr(main_win, 'sampled_file_path', None)

        ctx = self._build_data_context()
        context_text = self._build_context_text(ctx)
        user_input = f"请分析以下传感器数据:\n\n{context_text}\n\nCSV文件路径: {csv_path or '未指定'}"

        # ★ DIAG: 专家组诊断截断追踪 — 固定本地后端，打印 max_tokens / prompt 占用
        from core.ai_client import AIClient as _AIC
        _ai = _AIC.get_instance()
        _prompt_est = _ai._estimate_tokens('') + _ai._estimate_tokens(user_input)
        _ctx = _ai._get_context_size()
        print(f"[DIAG] _run_multi_agent: backend={_ai.backend}, model={_ai.model_name}, "
              f"ctx={_ctx}, prompt_est_tokens={_prompt_est}, "
              f"available_for_output={_ctx - _prompt_est - 512}, "
              f"max_tokens_from_online_config={self._online_config.get('max_tokens', 4096) if hasattr(self, '_online_config') and self._online_config else 'N/A'}")

        self.ai_diagnosis_result.clear()
        self.ai_terminal_title.setText("专家组诊断 (运行中...)")
        QApplication.processEvents()

        try:
            from dp_engine.multi_agent import MultiAgentThread
            self._multi_thread = MultiAgentThread(
                user_input=user_input,
                csv_path=csv_path,
                data_context=ctx,
            )
            self._multi_thread.progress.connect(self._on_multi_progress)
            self._multi_thread.finished.connect(self._on_multi_done)
            self._multi_thread.error.connect(self._on_multi_error)
            self._multi_thread.start()
        except Exception as e:
            self.ai_diagnosis_result.setPlainText(f'专家组诊断启动失败: {e}')

    @pyqtSlot(str)
    def _on_multi_progress(self, msg: str) -> None:
        """后台进度 → 更新标题栏，不阻塞输出区。"""
        self.ai_terminal_title.setText(f"专家组诊断 ({msg})")

    @pyqtSlot(dict)
    def _on_multi_done(self, result: dict) -> None:
        # Phase 6: {chief_report, data_scientist_text, audit_advisory, execution_logs, chief_truncated}
        chief_raw = result.get("chief_report", "") or ""
        chief_structured = self._extract_json(chief_raw)
        ds_text = result.get("data_scientist_text", "") or ""
        advisory = result.get("audit_advisory") or ""
        chief_truncated = bool(result.get("chief_truncated", False))

        # ── 一次性定型 (SSOT: 显示/切换/Word 共读) ──
        self._multi_result = {
            "chief_structured": chief_structured,
            "data_scientist_text": ds_text,
            "audit_advisory": advisory,
            "logs": result.get("execution_logs", []),
            "chief_truncated": chief_truncated,
        }
        self._active_result = "multi"
        self._update_result_buttons()
        # 使用同一份定型结果渲染显示
        self._switch_to_multi_result()

    @pyqtSlot(str)
    def _on_multi_error(self, err: str) -> None:
        self.ai_diagnosis_result.setPlainText(f'专家组诊断失败: {err}')
        self.ai_terminal_title.setText("专家组诊断 (失败)")

    # ═══════════════════════════════════════════════
    # 数据类型防火墙判定（与 analysis_tab 逻辑一致）
    # ═══════════════════════════════════════════════

    @staticmethod
    @staticmethod
    def _check_is_fiber_data(data, main_win) -> bool:
        """判断当前数据是否为光纤光栅数据 — 委托到 main_win.is_fiber_data()。"""
        if hasattr(main_win, 'is_fiber_data'):
            return bool(main_win.is_fiber_data())
        # 降级：无 getter 时内联判定
        if data is None:
            return False
        for c in data.columns:
            name = str(c)
            if 'FBG' in name or 'ENLIG' in name or '光纤传感' in name:
                return True
            import re as _re
            if _re.match(r'^W\d+$', name):
                return True
        return False

    # ═══════════════════════════════════════════════
    # 数据上下文构建（provider 驱动）
    # ═══════════════════════════════════════════════

    def _get_enabled_provider_summaries(self, main_win: Any) -> list[str]:
        """遍历勾选且可用的 provider，收集其 get_summary() 文本。"""
        from core.data_providers import get_all_providers
        summaries: list[str] = []
        providers = get_all_providers()
        for p in providers:
            entry = self._source_checkboxes.get(p.display_name)
            cb = entry[0] if entry else None
            if cb is None or not cb.isChecked():
                continue
            available = p.is_available(main_win)
            if available:
                try:
                    s = p.get_summary(main_win, budget_chars=2000)
                    if s.strip():
                        summaries.append(s)
                except Exception:
                    summaries.append(f"【{p.display_name}】读取失败")
            else:
                # 勾选但不可用 → 显式标注（理论上 UI 已禁用，但 double-check）
                summaries.append(f"【{p.display_name}】未运行/无最新结果")
        return summaries

    def _build_data_context(self) -> dict:
        """构建当前数据上下文的完整结构化摘要，返回 dict 供系统提示词和数据上下文使用。

        除原有统计外，注入启用 provider 的摘要文本到 provider_summaries 列表。
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
            ctx["provider_summaries"] = self._get_enabled_provider_summaries(main_win)
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
        numeric_cols = [
            c for c in data.select_dtypes(include=['number']).columns
            if not str(c).endswith('_anomaly')  # ★ 过滤清洗产生的异常标记列，只统计真实数据列
        ]
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

        # ── 清洗统计 ──
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

        # ── 传感器结果 ──
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

        # ── 暗号标注列 ──
        try:
            annotated_cols = main_win.get_annotated_columns()
            if annotated_cols:
                ctx["annotated_columns"] = list(annotated_cols)
        except Exception:
            pass

        # ── Provider 数据源摘要 ──
        ctx["provider_summaries"] = self._get_enabled_provider_summaries(main_win)

        return ctx

    def _build_context_text(self, ctx: dict) -> str:
        """将结构化上下文 dict 转为纯文本描述，供 user prompt 使用。

        注: 数据文件模板/规模/列名/统计已由 DataFileProvider 接管，
        旧内联重复段已删除。此处仅保留数据文件以外模块的上下文。
        """
        if not ctx or ctx.get("error"):
            return "当前无数据。"

        lines: list[str] = []

        # ── 外部数据提示 ──
        if not ctx.get("has_data"):
            has_external = bool(self._external_files)
            if has_external:
                lines.append(
                    "注意：本次诊断使用外部（未经本软件处理）数据文件，"
                    "不套用内部模块的口径与模板。数据上下文见下方模块摘要。")
            else:
                return "当前无数据。"

        # ── 数据类型提示（防火墙） ──
        if ctx.get("has_data"):
            lines.append(f"数据类型提示: {ctx.get('data_type_label', '未知')}")

        # ── 暗号标注列 ──
        annotated = ctx.get("annotated_columns", [])
        if annotated:
            lines.append(f"\n暗号标注列: {annotated}")

        # ── Provider 数据源摘要（含数据文件 / 清洗 / 分析 / 标定） ──
        provider_summaries = ctx.get("provider_summaries", []) or []
        if provider_summaries:
            lines.append("\n=== 模块数据源摘要 ===")
            for ps in provider_summaries:
                lines.append(ps)

        # 需求文件
        if self.ai_req_files:
            for rf in self.ai_req_files:
                try:
                    with open(rf, 'r', encoding='utf-8') as f:
                        content = f.read()[:2000]
                        lines.append(f"\n--- 需求文件: {rf} ---\n{content}")
                except Exception:
                    pass

        # ── KB 诊断规则 (Phase 3) ──
        kb_rag = self._build_kb_rag_context()
        if kb_rag:
            lines.append("\n" + kb_rag)

        return '\n'.join(lines)

    def _build_kb_rag_context(self) -> str:
        """从标定/对比/清洗数据组装 KB 触发数据并检索规则."""
        try:
            from core.diagnosis_kb import get_kb
            kb = get_kb()
        except Exception:
            return ""

        main_win = self._find_main()
        if not main_win:
            return ""

        # ── 覆盖 σ 阈值 (从 grade_thresholds) ──
        ct = getattr(main_win, 'calibration_tab_widget', None)
        tp = getattr(ct, 'temp_page', None) if ct else None
        if tp:
            pb_state = getattr(tp, '_phase_b_state', {}) or {}
            gt = pb_state.get('grade_thresholds', None)
            if gt:
                kb.load_constants_from_thresholds(gt)

        sensor_data, multisource_data, cleaning_data = self._build_kb_inputs()
        hits = kb.retrieve(
            sensor_data=sensor_data,
            multisource_data=multisource_data,
            cleaning_data=cleaning_data,
        )
        return kb.assemble_rag_prompt(hits, budget_chars=2500)

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
            "=== 成品化约束（绝对纪律） ===\n"
            "- 严禁向读者发问（如\"请确认\"\"待确认后执行\"\"以便生成\"等），必须直接给出分析结论。\n"
            "- 有不确定 → 写\"分析口径/假设说明：\"陈述前提后照常给结论，不挂起。\n"
            "- 严禁臆造数值：缺标定系数/灵敏度/阈值等 → 明示\"未提供，无法计算\"，不得假设≈1500pm/≈3%等任何虚构数字。\n"
            "- 时间戳歧义按数据清洗口径陈述，正文不残留\"(2035？)\"等问号。\n"
            "- 禁止 ASCII 字符画：不得用 | / \\\\ - _ 等字符拼绘趋势图/曲线/坐标轴/示意图。如需图表用文字描述或数据表表达，注明\"由软件绘图模块出图\"。\n"
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

    # ═══════════════════════════════════════════════════════
    # LaTeX → Unicode 清洗器 (去 $ + 符号映射 + 降级)
    # ═══════════════════════════════════════════════════════

    _LATEX_MAP: dict[str, str] = {
        r'\mu': 'μ', r'\sigma': 'σ', r'\Delta': 'Δ', r'\lambda': 'λ',
        r'\epsilon': 'ε', r'\varepsilon': 'ε', r'\times': '×', r'\pm': '±',
        r'\le': '≤', r'\ge': '≥', r'\alpha': 'α', r'\beta': 'β',
        r'\gamma': 'γ', r'\theta': 'θ', r'\delta': 'δ', r'\phi': 'φ',
        r'\omega': 'ω', r'\pi': 'π', r'\frac': '/',
        r'\sqrt': '√', r'\sum': 'Σ', r'\int': '∫', r'\prod': 'Π',
        r'\approx': '≈', r'\neq': '≠', r'\equiv': '≡', r'\propto': '∝',
        r'\partial': '∂', r'\infty': '∞', r'\nabla': '∇',
        r'\cdot': '·', r'\ldots': '…', r'\cdots': '⋯',
        r'\hat': '', r'\bar': '', r'\tilde': '',  # accents → strip prefix
        r'\text': '', r'\mathrm': '', r'\mathbf': '',
        r'\degree': '°', r'\%': '%',
    }

    @staticmethod
    def _clean_latex_text(text: str) -> str:
        """清洗 LaTeX 标记：去 $ 包裹 + 符号映射 + 上下标转 HTML 标签。

        规则:
        - $...$ 内: 符号映射 + ^/_{...} → <sup>/<sub> HTML 标签
        - $...$ 外: 原样保留 (文件名 _10pct 不动)
        - 去 $$/$$ 包裹
        - 清除残余 LaTeX 命令
        - 修复相邻 Unicode 希腊字母间的多余空格 (Δ λ → Δλ)
        """
        if not text:
            return text

        import re as _latex_re

        # ── 辅助: 处理 $...$ 内部内容 ──
        def _process_math_block(math_content: str) -> str:
            t = math_content
            # 符号映射 (按长度降序)
            for cmd, unicode_char in sorted(
                AiDiagnosisWidget._LATEX_MAP.items(), key=lambda x: -len(x[0])
            ):
                t = t.replace(cmd, unicode_char)
            # 上标: ^{...} → <sup>...</sup>, ^(\d) → <sup>\1</sup>
            t = _latex_re.sub(
                r'\^\{([^}]+)\}',
                lambda m: '<sup>' + m.group(1) + '</sup>',
                t)
            t = _latex_re.sub(
                r'\^(\d)',
                lambda m: '<sup>' + m.group(1) + '</sup>',
                t)
            # 下标: _{...} → <sub>...</sub>, _(\d) → <sub>\1</sub>
            t = _latex_re.sub(
                r'_\{([^}]+)\}',
                lambda m: '<sub>' + m.group(1) + '</sub>',
                t)
            t = _latex_re.sub(
                r'_(\d)',
                lambda m: '<sub>' + m.group(1) + '</sub>',
                t)
            # 去 \displaystyle, \bigl, \left, \right 等
            for cmd2 in [
                r'\displaystyle', r'\bigl', r'\bigr', r'\left', r'\right',
                r'\langle', r'\rangle', r'\quad', r'\qquad',
            ]:
                t = t.replace(cmd2, '')
            # 残余 \cmd{arg} → arg
            t = _latex_re.sub(r'\\[a-zA-Z]+\{([^}]*)\}', r'\1', t)
            # 残余孤立 \cmd → 空
            t = _latex_re.sub(r'\\[a-zA-Z]+', '', t)
            return t.strip()

        # 1) $$...$$ 块级公式 → 内部处理
        t = _latex_re.sub(
            r'\$\$(.+?)\$\$',
            lambda m: _process_math_block(m.group(1)),
            text, flags=_latex_re.DOTALL)

        # 2) $...$ 内联公式 → 内部处理
        t = _latex_re.sub(
            r'\$(.+?)\$',
            lambda m: _process_math_block(m.group(1)),
            t)

        # 3) 全局: 清除孤立 LaTeX 命令 (不在 $ 内的残余)
        for cmd2 in [
            r'\displaystyle', r'\bigl', r'\bigr', r'\left', r'\right',
            r'\langle', r'\rangle', r'\quad', r'\qquad',
        ]:
            t = t.replace(cmd2, '')
        # 残余 \cmd{arg} → arg
        t = _latex_re.sub(r'\\[a-zA-Z]+\{([^}]*)\}', r'\1', t)
        # 残余孤立 \cmd → 空
        t = _latex_re.sub(r'\\[a-zA-Z]+', '', t)

        # 4) 修复相邻 Unicode 希腊/数学符号间的多余空格
        # Δ λ → Δλ, α β → αβ, Δ T → ΔT
        _greek_math = 'ΔλμσαβγδϵεθηφωπΣΠΩ∇∂∞'
        t = _latex_re.sub(
            rf'([{_greek_math}])\s+([{_greek_math}])',
            r'\1\2', t)

        # 5) 清理多余空白
        t = _latex_re.sub(r' {2,}', ' ', t)
        return t.strip()

    # ═══════════════════════════════════════════════════════
    # 表格样式 helper — HTML 三线表
    # ═══════════════════════════════════════════════════════

    _table_counter: int = 0

    @classmethod
    def _next_table_id(cls) -> int:
        cls._table_counter += 1
        return cls._table_counter

    @staticmethod
    def _render_table_html(headers: list[str], rows: list[list[str]],
                           caption: str = "", table_id: int = 0) -> str:
        """渲染专业化 HTML 表格：三线表 + 表头底纹 + 数字右对齐。"""
        parts: list[str] = []
        tid = table_id or 0
        if caption and tid:
            parts.append(
                f'<p style="text-align:center;font-weight:bold;'
                f'margin:12px 0 4px 0;font-size:13px;">'
                f'表{tid} {caption}</p>'
            )
        parts.append(
            '<table style="width:100%;border-collapse:collapse;'
            'margin:6px 0;font-size:12px;'
            'border-top:2px solid #333;border-bottom:2px solid #333;">'
        )
        # 表头
        parts.append('<tr style="background:#e6eef5;font-weight:bold;">')
        for h in headers:
            parts.append(
                f'<th style="padding:6px 10px;border-bottom:1.5px solid #333;'
                f'text-align:left;">{h}</th>')
        parts.append('</tr>')
        # 数据行
        for row in rows:
            parts.append('<tr>')
            for ci, cell in enumerate(row):
                # 尝试检测数字列 → 右对齐
                is_num = False
                try:
                    float(str(cell).replace(',', '').replace('%', '').strip())
                    is_num = True
                except (ValueError, TypeError):
                    pass
                align = 'text-align:right;' if is_num else ''
                parts.append(
                    f'<td style="padding:5px 10px;border-bottom:1px solid #ddd;{align}">'
                    f'{cell}</td>')
            parts.append('</tr>')
        parts.append('</table>')
        return '\n'.join(parts)

    # ═══════════════════════════════════════════════════════
    # HTML 转义辅助: 保留 <sup>/<sub> 等标签
    # ═══════════════════════════════════════════════════════

    @staticmethod
    def _escape_html_keep_sup_sub(text: str) -> str:
        """HTML-escape 文本但保留 <sup>/<sub> 标签。"""
        import html as _html_mod
        import re as _split_re
        parts = _split_re.split(
            r'(<(?:sup|sub)[^>]*>.*?</(?:sup|sub)>)', text)
        result = []
        for part in parts:
            if part.startswith('<sup>') or part.startswith('<sub>'):
                result.append(part)
            else:
                result.append(_html_mod.escape(part))
        return ''.join(result)

    # ═══════════════════════════════════════════════════════
    # Markdown → HTML 轻量渲染器 (Phase 7 enhanced)
    # ═══════════════════════════════════════════════════════

    @staticmethod
    def _render_markdown_to_html(text: str) -> str:
        """将 markdown 转换为格式化 HTML — 两端同源渲染。

        支持: #..##### 标题、**粗**/*斜*/`code`、-/*/+ 列表(多级缩进)、
        > 引用、--- 分割、|表格|。无裸 markdown 符号残留。
        """
        if not text or not text.strip():
            return ""

        import html as _html_mod
        # LaTeX 清洗
        clean_text = AiDiagnosisWidget._clean_latex_text(text)
        lines = clean_text.split('\n')
        html_parts: list[str] = []
        i = 0

        # ── 内联行处理器 ──
        def _inline_html(s: str) -> str:
            # 先保留 <sup>/<sub> 再 escape 其余
            s = AiDiagnosisWidget._escape_html_keep_sup_sub(s)
            # **bold**
            s = re.sub(r'\*\*(.+?)\*\*', r'<b>\1</b>', s)
            # *italic* (not overlapping with **)
            s = re.sub(r'(?<!\*)\*([^*\n]+?)\*(?!\*)', r'<i>\1</i>', s)
            # `code`
            s = re.sub(r'`([^`]+?)`',
                       r'<code style="background:#f5f5f5;padding:1px 4px;'
                       r'border-radius:2px;font-family:monospace;">\1</code>', s)
            return s

        while i < len(lines):
            line = lines[i]

            # 空行
            if not line.strip():
                # 连续空行 → 跳过
                i += 1
                continue

            # ── 代码围栏: ``` 或 ~~~ ──
            fence_match = re.match(r'^(`{3,}|~{3,})\s*(\S*)\s*$', line)
            if fence_match:
                fence_char = fence_match.group(1)[0]  # ` or ~
                # 收集围栏内代码行
                code_lines: list[str] = []
                i += 1
                while i < len(lines):
                    if lines[i].strip().startswith(fence_char * 3):
                        i += 1  # 跳过闭合围栏
                        break
                    code_lines.append(_html_mod.escape(lines[i]))
                    i += 1
                if code_lines:
                    html_parts.append(
                        '<pre style="background:#f5f5f5;border:1px solid #e0e0e0;'
                        'border-radius:4px;padding:10px 14px;margin:8px 0;'
                        'font-size:12px;font-family:Consolas,monospace;'
                        'white-space:pre-wrap;line-height:1.5;'
                        'overflow-x:auto;">'
                        f'<code>{"<br>".join(code_lines)}</code></pre>'
                    )
                continue

            # ── 分隔线: --- / *** / ___ ──
            if re.match(r'^[\-\*\_]{3,}\s*$', line):
                html_parts.append(
                    '<hr style="border:none;border-top:1px solid #ddd;'
                    'margin:12px 0;">')
                i += 1
                continue

            # ── 引用: > text ──
            if line.startswith('>'):
                quote_lines = []
                while i < len(lines) and lines[i].startswith('>'):
                    qt = re.sub(r'^>\s?', '', lines[i])
                    quote_lines.append(_inline_html(qt))
                    i += 1
                html_parts.append(
                    f'<blockquote style="background:#f5f5f5;'
                    f'border-left:3px solid #bbb;padding:8px 14px;'
                    f'margin:8px 0;font-size:12px;color:#555;'
                    f'font-style:italic;">{"<br>".join(quote_lines)}</blockquote>'
                )
                continue

            # ── 表格 ──
            if line.strip().startswith('|') and line.strip().endswith('|'):
                table_lines = [line]
                j = i + 1
                while j < len(lines) and lines[j].strip().startswith('|'):
                    table_lines.append(lines[j])
                    j += 1
                data_rows = [
                    ln for ln in table_lines
                    if not all(c in '|-: ' for c in ln.strip())
                ]
                if data_rows:
                    headers_list = [
                        c.strip() for c in data_rows[0].strip().strip('|').split('|')]
                    rows_list = [
                        [c.strip() for c in r.strip().strip('|').split('|')]
                        for r in data_rows[1:]
                    ]
                    # LaTeX 清洗各单元格
                    headers_list = [
                        AiDiagnosisWidget._clean_latex_text(h) for h in headers_list]
                    rows_list = [
                        [AiDiagnosisWidget._clean_latex_text(c) for c in r]
                        for r in rows_list]
                    html_parts.append(
                        AiDiagnosisWidget._render_table_html(
                            headers_list, rows_list))
                i = j
                continue

            # ── 标题: ##### / #### / ### / ## / # ──
            for n, (pat, tag, fs) in enumerate([
                (r'^#####\s+(.+)', 'h6', 12),
                (r'^####\s+(.+)', 'h5', 13),
                (r'^###\s+(.+)', 'h4', 14),
                (r'^##\s+(.+)', 'h3', 15),
                (r'^#\s+(.+)', 'h2', 17),
            ]):
                m = re.match(pat, line)
                if m:
                    if n <= 1:
                        # #####/#### → 小标题(bold paragraph fallback)
                        html_parts.append(
                            f'<p style="font-size:13px;font-weight:700;'
                            f'color:#333;margin:8px 0 2px 0;">'
                            f'{_html_mod.escape(m.group(1))}</p>')
                    else:
                        border = 'border-bottom:1px solid #e8e8e8;padding-bottom:4px;' if n == 4 else ''
                        html_parts.append(
                            f'<{tag} style="font-size:{fs}px;color:#1a1a1a;'
                            f'margin:12px 0 6px 0;{border}">'
                            f'{_html_mod.escape(m.group(1))}</{tag}>')
                    i += 1
                    break
            else:
                # ── 列表 (多级缩进): - / * / +，含多个空格 ──
                list_match = re.match(r'^(\s*)[\-\*\+]\s+(.+)', line)
                if list_match:
                    indent = len(list_match.group(1))
                    list_items: list[tuple[int, str]] = []
                    while i < len(lines):
                        lm = re.match(r'^(\s*)[\-\*\+]\s+(.+)', lines[i])
                        if not lm:
                            # 检查是否是列表项续行(缩进文本不以 -/*/+ 开头)
                            if list_items and lines[i].strip() and not re.match(
                                r'^(\s*)[\-\*\+]\s+', lines[i]) and not lines[i].strip().startswith('#'):
                                # 续行追加到上一项
                                prev_indent, prev_text = list_items[-1]
                                list_items[-1] = (prev_indent, prev_text + ' ' + lines[i].strip())
                                i += 1
                                continue
                            break
                        cur_indent = len(lm.group(1))
                        # 去 LaTeX
                        item_text = _inline_html(
                            AiDiagnosisWidget._clean_latex_text(lm.group(2)))
                        list_items.append((cur_indent, item_text))
                        i += 1
                    # 渲染单级或嵌套 ul
                    if list_items:
                        out: list[str] = []
                        prev_indent = None
                        _ul_open = 0
                        for it_indent, it_text in list_items:
                            if prev_indent is None or it_indent > prev_indent:
                                out.append('<ul style="margin:2px 0;padding-left:20px;font-size:13px;">')
                                _ul_open += 1
                            elif it_indent < prev_indent:
                                for _ in range(min(prev_indent - it_indent, _ul_open)):
                                    out.append('</ul>')
                                    _ul_open -= 1
                            out.append(f'<li>{it_text}</li>')
                            prev_indent = it_indent
                        for _ in range(_ul_open):
                            out.append('</ul>')
                        html_parts.append('\n'.join(out))
                    continue
                else:
                    # 普通段落
                    html_parts.append(
                        f'<p style="margin:4px 0;font-size:13px;'
                        f'line-height:1.6;">{_inline_html(line)}</p>')
                    i += 1

        return '\n'.join(html_parts)

    # ═══════════════════════════════════════════════════════
    # 首席专家综合结论 → HTML (显示)
    # ═══════════════════════════════════════════════════════

    @staticmethod
    def _render_chief_conclusions_html(data: dict, kb_hits: list | None = None,
                                       kind: str = "multi") -> str:
        """将 chief structured JSON + KB 命中规则渲染为 HTML。

        Args:
            kind: "multi" → 一、首席专家综合结论; "ai" → 诊断摘要
        """
        import html as _html_mod
        _clean = AiDiagnosisWidget._clean_latex_text
        html_parts: list[str] = []
        kb_hits = kb_hits or []

        # ── 节标题 ──
        section_title = '一、首席专家综合结论' if kind == 'multi' else '诊断摘要'
        html_parts.append(
            '<div style="background:linear-gradient(135deg,#1a365d 0%,#2b6cb0 100%);'
            'color:white;padding:14px 18px;border-radius:8px 8px 0 0;margin-top:16px;">'
            f'<h2 style="margin:0;font-size:18px;">{_html_mod.escape(section_title)}</h2>'
            '</div>'
            '<div style="border:2px solid #2b6cb0;border-top:none;'
            'border-radius:0 0 8px 8px;padding:16px 18px;margin-bottom:12px;">'
        )

        # ── 数据质量评级 ──
        summary = data.get('diagnosis_summary', {})
        quality = summary.get('data_quality', 'unknown')
        quality_colors = {'good': '#52c41a', 'fair': '#faad14', 'poor': '#ff4d4f'}
        quality_labels = {'good': '优', 'fair': '良', 'poor': '差'}
        q_color = quality_colors.get(quality, '#999')
        q_label = quality_labels.get(quality, quality.upper())

        html_parts.append(
            f'<div style="display:flex;align-items:center;gap:16px;margin-bottom:12px;'
            f'flex-wrap:wrap;">'
            f'<div>'
            f'<span style="font-size:13px;color:#666;">数据质量评级</span><br>'
            f'<span style="display:inline-block;background:{q_color};color:white;'
            f'padding:3px 14px;border-radius:12px;font-size:15px;font-weight:bold;'
            f'margin-top:2px;">{q_label}</span>'
            f'</div>'
            f'<div>'
            f'<span style="font-size:13px;color:#666;">数据类型</span><br>'
            f'<span style="font-size:14px;font-weight:bold;">{_html_mod.escape(_clean(str(summary.get("data_type","?"))))}</span>'
            f'</div>'
            f'<div>'
            f'<span style="font-size:13px;color:#666;">异常点数</span><br>'
            f'<span style="font-size:14px;font-weight:bold;">{summary.get("anomaly_count","?")}</span>'
            f'</div>'
            f'<div>'
            f'<span style="font-size:13px;color:#666;">模板</span><br>'
            f'<span style="font-size:14px;">{_html_mod.escape(_clean(str(summary.get("template_name","?"))))}</span>'
            f'</div>'
            f'</div>'
        )

        # ── 总体研判 ──
        overall = summary.get('overall_assessment', '')
        if overall:
            html_parts.append(
                f'<div style="background:#f0f5ff;border-left:4px solid #1890ff;'
                f'padding:12px 16px;margin-bottom:12px;border-radius:0 4px 4px 0;">'
                f'<strong style="font-size:14px;color:#1a365d;">🔍 总体研判</strong><br>'
                f'<span style="font-size:13px;line-height:1.6;">{_html_mod.escape(_clean(overall))}</span>'
                f'</div>'
            )

        # ── 传感器分级结论 (表) ──
        sensors = data.get('sensor_analysis', [])
        if sensors:
            html_parts.append(
                '<h3 style="font-size:14px;color:#333;margin:12px 0 6px 0;">'
                '📡 传感器分级结论</h3>'
            )
            headers = ['传感器', '状态', '均值', '范围', '发现', '建议']
            rows = []
            s_colors = {'normal': '#52c41a', 'warning': '#faad14', 'critical': '#ff4d4f'}
            for s in sensors:
                status = s.get('status', 'normal')
                stats = s.get('statistics', {}) or {}
                rows.append([
                    _clean(str(s.get('sensor_id', '?'))),
                    status,
                    str(stats.get('mean', '?')),
                    "[{}]".format('-'.join([str(stats.get(k, '?')) for k in ('min', 'max')])),
                    _clean(str(s.get('findings', '')))[:60],
                    _clean(str(s.get('suggestions', '')))[:60],
                ])
            html_parts.append(
                AiDiagnosisWidget._render_table_html(headers, rows))

        # ── 物理诊断要点 ──
        phys = data.get('physical_diagnosis', {})
        if phys and phys.get('phenomenon'):
            severity = phys.get('severity', 'low')
            sev_colors = {'low': '#52c41a', 'medium': '#faad14', 'high': '#ff4d4f'}
            html_parts.append(
                '<h3 style="font-size:14px;color:#333;margin:12px 0 6px 0;">'
                '🔬 物理诊断要点 '
                f'<span style="background:{sev_colors.get(severity,"#999")};color:white;'
                f'padding:1px 8px;border-radius:8px;font-size:11px;">{severity}</span>'
                '</h3>'
            )
            html_parts.append(
                f'<div style="background:#fffbe6;border:1px solid #ffe58f;'
                f'padding:8px 12px;border-radius:4px;margin:4px 0;font-size:12px;">'
                f'<strong>现象: </strong>{_html_mod.escape(_clean(str(phys.get("phenomenon",""))))}</div>'
            )
            causes = phys.get('possible_causes', [])
            if causes:
                html_parts.append('<ul style="margin:4px 0;font-size:12px;color:#555;">')
                for c in causes:
                    html_parts.append(f'<li>可能原因: {_html_mod.escape(_clean(str(c)))}</li>')
                html_parts.append('</ul>')
            actions = phys.get('recommended_actions', [])
            if actions:
                html_parts.append('<div style="font-size:12px;margin-top:4px;"><strong>建议措施:</strong></div>')
                for a in actions:
                    html_parts.append(
                        f'<div style="background:#f6ffed;border:1px solid #b7eb8f;'
                        f'padding:4px 10px;border-radius:4px;margin:2px 0;font-size:12px;">'
                        f'→ {_html_mod.escape(_clean(str(a)))}</div>'
                    )

        # ── 核心建议 ──
        dq = data.get('data_quality_assessment', {})
        recs = dq.get('recommendations', [])
        if recs:
            html_parts.append(
                '<h3 style="font-size:14px;color:#333;margin:12px 0 6px 0;">'
                '💡 核心建议</h3>'
            )
            for r in recs:
                html_parts.append(
                    f'<div style="background:#f6ffed;border:1px solid #b7eb8f;'
                    f'padding:4px 10px;border-radius:4px;margin:2px 0;font-size:12px;">'
                    f'→ {_html_mod.escape(_clean(str(r)))}</div>'
                )

        # ── 命中诊断规则 ──
        if kb_hits:
            html_parts.append(
                '<h3 style="font-size:14px;color:#333;margin:12px 0 6px 0;">'
                '📋 命中诊断规则</h3>'
            )
            headers = ['规则', '要点', '建议']
            rows = []
            for h in kb_hits:
                rows.append([
                    "{} [{}]".format(h.get("id", "?"), h.get("severity", "?")),
                    _clean(str(h.get("meaning", "")))[:120],
                    _clean(str(h.get("recommendation", "")))[:120],
                ])
            html_parts.append(
                AiDiagnosisWidget._render_table_html(headers, rows))

        html_parts.append('</div>')  # 闭合首席结论外框
        return '\n'.join(html_parts)


    # ═══════════════════════════════════════════════════════
    # 外部数据文件提交 / 清除
    # ═══════════════════════════════════════════════════════

    def _on_submit_external_files(self) -> None:
        """弹出文件对话框，用户选择外部数据文件加载。"""
        from PyQt6.QtWidgets import QFileDialog
        files, _ = QFileDialog.getOpenFileNames(
            self, '提交外部数据文件', '',
            '数据文件 (*.csv *.xlsx *.xls *.txt *.json *.md);;所有文件 (*)',
        )
        if not files:
            return
        self._external_files = list(files)
        self._refresh_external_files_ui()
        self._refresh_source_checkboxes()

    def _on_clear_external_files(self) -> None:
        """清空已加载的外部文件。"""
        self._external_files = []
        self._refresh_external_files_ui()
        self._refresh_source_checkboxes()

    def _refresh_external_files_ui(self) -> None:
        """更新外部文件状态标签；加载时自动勾选并启用对应 provider。"""
        if not self._external_files:
            self._external_files_label.setText("未加载外部文件")
            self._external_files_label.setStyleSheet("color: #888; font-size: 11px;")
        else:
            names = ', '.join(
                os.path.basename(f) for f in self._external_files[:5])
            more = f" +{len(self._external_files) - 5}" if len(self._external_files) > 5 else ""
            self._external_files_label.setText(f"已加载: {names}{more}")
            self._external_files_label.setStyleSheet("color: #52c41a; font-size: 11px; font-weight: bold;")
            # 自动勾选并启用「外部数据文件」复选框
            entry = self._source_checkboxes.get("外部数据文件")
            if entry is not None:
                cb, _p = entry
                cb.setEnabled(True)
                cb.setChecked(True)
                cb.setText("外部数据文件")

    # ═══════════════════════════════════════════════════════
    # 保存 (AI诊断 / 多智能体诊断 — 各自 Word + JSON)
    # ═══════════════════════════════════════════════════════

    def _save_ai(self) -> None:
        self._save_diagnosis("ai")

    def _save_multi(self) -> None:
        self._save_diagnosis("multi")

    def _save_diagnosis(self, kind: str) -> None:
        """保存一套诊断结果：Word(.docx) + JSON。"""
        if kind == "ai" and not self._ai_result:
            self._warn(self, '暂无 AI 诊断结果')
            return
        if kind == "multi" and not self._multi_result:
            self._warn(self, '暂无专家组诊断结果')
            return

        rec = self._build_diagnosis_record()
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        prefix = "AI诊断" if kind == "ai" else "多智能体诊断"

        # ── 默认目录: 项目资料库/ (SSOT: main_win.get_project_library_dir()) ──
        default_dir = ""
        try:
            main_win = self._find_main()
            if main_win and hasattr(main_win, 'get_project_library_dir'):
                default_dir = main_win.get_project_library_dir() or ""
        except Exception:
            pass

        path, _ = QFileDialog.getSaveFileName(
            self, f'保存{prefix}',
            os.path.join(default_dir, f'{prefix}_{ts}.docx') if default_dir else f'{prefix}_{ts}.docx',
            'Word 文档 (*.docx)',
        )
        if not path:
            return
        try:
            self._build_word_document(rec, kind, path)
            json_path = path.replace('.docx', '.json')
            with open(json_path, 'w', encoding='utf-8') as f:
                json.dump(rec, f, ensure_ascii=False, indent=2)
            QMessageBox.information(self, '保存成功',
                f'Word: {os.path.basename(path)}\nJSON: {os.path.basename(json_path)}')
        except Exception as e:
            QMessageBox.warning(self, '保存失败', str(e))

    # ═══════════════════════════════════════════════════════
    # 表格样式 helper — docx 三线表
    # ═══════════════════════════════════════════════════════

    _docx_table_counter: int = 0

    @classmethod
    def _next_docx_table_id(cls) -> int:
        cls._docx_table_counter += 1
        return cls._docx_table_counter

    @staticmethod
    def _add_styled_table_docx(doc: Any, headers: list[str],
                               rows: list[list[str]],
                               caption: str = "", table_id: int = 0) -> Any:
        """添加专业化三线表：表头底纹+加粗、数字右对齐、可选表号表题。"""
        from docx.shared import Pt, RGBColor, Cm
        from docx.enum.text import WD_ALIGN_PARAGRAPH
        from docx.enum.table import WD_TABLE_ALIGNMENT
        from docx.oxml.ns import qn
        from docx.oxml import OxmlElement

        if not headers:
            return None

        # 表题
        tid = table_id or 0
        if caption and tid:
            cp = doc.add_paragraph()
            cp.alignment = WD_ALIGN_PARAGRAPH.CENTER
            run = cp.add_run(f'表{tid} {caption}')
            run.font.bold = True
            run.font.size = Pt(10)
            cp.paragraph_format.space_after = Pt(2)

        num_rows = len(rows) + 1
        num_cols = len(headers)
        t = doc.add_table(rows=num_rows, cols=num_cols)
        t.style = 'Table Grid'
        t.alignment = WD_TABLE_ALIGNMENT.CENTER

        # 表头底纹 + 加粗
        for ci, h in enumerate(headers):
            cell = t.cell(0, ci)
            cell.text = ''
            p = cell.paragraphs[0]
            run = p.add_run(h)
            run.font.bold = True
            run.font.size = Pt(9)
            # 底纹
            shading = cell._element.get_or_add_tcPr().makeelement(
                qn('w:shd'), {
                    qn('w:fill'): '1a365d',
                    qn('w:val'): 'clear',
                })
            cell._element.get_or_add_tcPr().append(shading)
            run.font.color.rgb = RGBColor(0xFF, 0xFF, 0xFF)

        # 数据行
        for ri, row in enumerate(rows):
            for ci, cell_text in enumerate(row):
                if ci >= num_cols:
                    break
                cell = t.cell(ri + 1, ci)
                cell.text = ''
                p = cell.paragraphs[0]
                run = p.add_run(str(cell_text))
                run.font.size = Pt(9)
                # 数字列右对齐
                try:
                    float(str(cell_text).replace(',', '').replace('%', '').strip())
                    p.alignment = WD_ALIGN_PARAGRAPH.RIGHT
                except (ValueError, TypeError):
                    pass
                # 交替行底色
                if ri % 2 == 1:
                    shading = cell._element.get_or_add_tcPr().makeelement(
                        qn('w:shd'), {
                            qn('w:fill'): 'f0f4f8',
                            qn('w:val'): 'clear',
                        })
                    cell._element.get_or_add_tcPr().append(shading)

        # 三线表上下粗线
        tbl = t._element
        tblPr = tbl.find(qn('w:tblPr'))
        if tblPr is None:
            tblPr = OxmlElement('w:tblPr')
            tbl.insert(0, tblPr)
        borders = tblPr.find(qn('w:tblBorders'))
        if borders is None:
            borders = OxmlElement('w:tblBorders')
            tblPr.append(borders)
        for edge, sz in [('top', '12'), ('bottom', '12'), ('insideH', '4')]:
            el = OxmlElement(f'w:{edge}')
            el.set(qn('w:val'), 'single')
            el.set(qn('w:sz'), sz)
            el.set(qn('w:color'), '333333')
            el.set(qn('w:space'), '0')
            borders.append(el)

        doc.add_paragraph()
        return t

    # ═══════════════════════════════════════════════════════
    # Markdown → docx 轻量渲染器 (Phase 7 enhanced)
    # ═══════════════════════════════════════════════════════

    @staticmethod
    def _render_markdown_blocks_to_docx(doc: Any, text: str) -> None:
        """将 markdown 渲染到 python-docx Document — 两端同源。

        支持: #..##### 标题、**粗**/*斜*/`code`、-/*/+ 列表(多级缩进)、
        > 引用、--- 分割、|表格|。
        """
        from docx.shared import Pt, RGBColor, Cm
        from docx.enum.text import WD_ALIGN_PARAGRAPH

        if not text or not text.strip():
            return

        # LaTeX 清洗
        clean_text = AiDiagnosisWidget._clean_latex_text(text)  # pyright: ignore[reportAttributeAccessIssue]  # cls-method
        lines = clean_text.split('\n')
        i = 0

        # ── 内联 runs 辅助: 处理 **bold** / *italic* / `code` / <sup>/<sub> ──
        def _add_inline_runs(para: Any, s: str) -> None:
            # 先处理 <sup>/<sub> → 拆段加 real formatting
            import re as _docx_re
            sup_sub_pattern = r'(<sup>.+?</sup>|<sub>.+?</sub>)'
            segments = _docx_re.split(sup_sub_pattern, s)
            for seg in segments:
                if seg.startswith('<sup>') and seg.endswith('</sup>'):
                    inner = seg[5:-6]
                    run = para.add_run(inner)
                    run.font.superscript = True
                elif seg.startswith('<sub>') and seg.endswith('</sub>'):
                    inner = seg[5:-6]
                    run = para.add_run(inner)
                    run.font.subscript = True
                else:
                    # split on **bold** and *italic* and `code`
                    pattern = r'(\*\*[^*]+?\*\*|\*[^*\n]+?\*|`[^`]+?`)'
                    parts = _docx_re.split(pattern, seg)
                    for part in parts:
                        if part.startswith('**') and part.endswith('**'):
                            run = para.add_run(part[2:-2])
                            run.font.bold = True
                        elif part.startswith('*') and part.endswith('*') and len(part) > 2:
                            run = para.add_run(part[1:-1])
                            run.font.italic = True
                        elif part.startswith('`') and part.endswith('`'):
                            run = para.add_run(part[1:-1])
                            run.font.name = 'Consolas'
                            run.font.size = Pt(9)
                        else:
                            para.add_run(part)

        while i < len(lines):
            line = lines[i]

            if not line.strip():
                i += 1
                continue

            # ── 代码围栏: ``` 或 ~~~ ──
            fence_match = re.match(r'^(`{3,}|~{3,})\s*(\S*)\s*$', line)
            if fence_match:
                fence_char = fence_match.group(1)[0]
                code_lines: list[str] = []
                i += 1
                while i < len(lines):
                    if lines[i].strip().startswith(fence_char * 3):
                        i += 1
                        break
                    code_lines.append(lines[i])
                    i += 1
                for ci, cl in enumerate(code_lines):
                    p = doc.add_paragraph()
                    # 等宽块浅灰底
                    pPr = p._element.get_or_add_pPr()
                    from docx.oxml.ns import qn as _code_qn
                    shd = pPr.makeelement(_code_qn('w:shd'), {
                        _code_qn('w:fill'): 'f5f5f5', _code_qn('w:val'): 'clear'})
                    pPr.append(shd)
                    p.paragraph_format.space_before = Pt(0)
                    p.paragraph_format.space_after = Pt(0)
                    p.paragraph_format.line_spacing = Pt(14)
                    run = p.add_run(cl)
                    run.font.name = 'Consolas'
                    run.font.size = Pt(9)
                if code_lines:
                    doc.add_paragraph()  # spacing after block
                continue

            # ── 分隔线 ──
            if re.match(r'^[\-\*\_]{3,}\s*$', line):
                p = doc.add_paragraph()
                p.paragraph_format.space_before = Pt(4)
                p.paragraph_format.space_after = Pt(4)
                # thin line via bottom border
                pPr = p._element.get_or_add_pPr()
                from docx.oxml.ns import qn
                pBdr = pPr.makeelement(qn('w:pBdr'), {})
                bottom = pBdr.makeelement(qn('w:bottom'), {
                    qn('w:val'): 'single', qn('w:sz'): '4',
                    qn('w:color'): 'cccccc', qn('w:space'): '1'
                })
                pBdr.append(bottom)
                pPr.append(pBdr)
                i += 1
                continue

            # ── 引用: > ──
            if line.startswith('>'):
                quote_lines = []
                while i < len(lines) and lines[i].startswith('>'):
                    quote_lines.append(re.sub(r'^>\s?', '', lines[i]))
                    i += 1
                for ql in quote_lines:
                    p = doc.add_paragraph()
                    p.paragraph_format.left_indent = Cm(1)
                    run = p.add_run(ql)
                    run.font.italic = True
                    run.font.color.rgb = RGBColor(0x66, 0x66, 0x66)
                    run.font.size = Pt(10)
                continue

            # ── 表格 ──
            if line.strip().startswith('|') and line.strip().endswith('|'):
                table_lines = [line]
                j = i + 1
                while j < len(lines) and lines[j].strip().startswith('|'):
                    table_lines.append(lines[j])
                    j += 1
                data_rows = [
                    ln for ln in table_lines
                    if not all(c in '|-: ' for c in ln.strip())
                ]
                if data_rows:
                    headers = [
                        AiDiagnosisWidget._clean_latex_text(c.strip())
                        for c in data_rows[0].strip().strip('|').split('|')]
                    rows = [
                        [AiDiagnosisWidget._clean_latex_text(c.strip())
                         for c in r.strip().strip('|').split('|')]
                        for r in data_rows[1:]
                    ] if len(data_rows) > 1 else []
                    AiDiagnosisWidget._add_styled_table_docx(
                        doc, headers, rows)
                i = j
                continue

            # ── 标题: #####..###/##/# ──
            heading_match = None
            heading_level = 1
            for _pat, _lvl in [
                (r'^#####\s+(.+)', 5),
                (r'^####\s+(.+)', 4),
                (r'^###\s+(.+)', 3),
                (r'^##\s+(.+)', 2),
                (r'^#\s+(.+)', 1),
            ]:
                m = re.match(_pat, line)
                if m:
                    heading_match = m
                    heading_level = _lvl
                    break
            if heading_match:
                heading_text = AiDiagnosisWidget._clean_latex_text(
                    heading_match.group(1))
                doc.add_heading(heading_text, level=heading_level)
                i += 1
                continue

            # ── 列表 (多级缩进) ──
            list_match = re.match(r'^(\s*)[\-\*\+]\s+(.+)', line)
            if list_match:
                while i < len(lines):
                    lm = re.match(r'^(\s*)[\-\*\+]\s+(.+)', lines[i])
                    if not lm:
                        if i < len(lines) and lines[i].strip() and not lines[i].strip().startswith('#'):
                            i += 1  # skip continuation
                            continue
                        break
                    indent = min(len(lm.group(1)) // 2, 2)  # 最多3级
                    item_text_bare = lm.group(2)
                    if indent == 0:
                        p = doc.add_paragraph(style='List Bullet')
                    else:
                        p = doc.add_paragraph(style='List Bullet 2')
                    _add_inline_runs(p, item_text_bare)
                    i += 1
                continue

            # ── 普通段落 ──
            p = doc.add_paragraph()
            _add_inline_runs(p, line)
            i += 1

    def _build_word_document(self, rec: dict, kind: str, output_path: str) -> None:
        """生成专业化 Word 文档 — 表号、三线表、文档头、页码、目录。

        Phase 7+: 排版规范化 — 表号表题、三线表、文档元信息、统一字体。
        """
        from docx import Document
        from docx.shared import Inches, Pt, Cm, RGBColor
        from docx.enum.text import WD_ALIGN_PARAGRAPH, WD_LINE_SPACING
        from docx.enum.table import WD_TABLE_ALIGNMENT
        from docx.oxml.ns import qn
        from docx.oxml import OxmlElement

        doc = Document()

        # ── 全局样式: 标题黑体/正文宋体 ──
        style = doc.styles['Normal']
        style.font.name = '宋体'
        style.font.size = Pt(10.5)
        style.paragraph_format.line_spacing = 1.5
        for i in range(1, 5):
            hs = doc.styles[f'Heading {i}']
            hs.font.name = '黑体'

        # ── 重置表计数器 ──
        AiDiagnosisWidget._docx_table_counter = 0

        # ── 封面/文档头 ──
        ts = rec.get('timestamp', '')
        report_title = f'{"AI 诊断报告" if kind == "ai" else "专家组诊断报告"}'
        doc.add_heading(report_title, level=0)

        # 元信息表格 (无边框)
        meta_table = doc.add_table(rows=5, cols=2)
        meta_table.style = 'Table Grid'
        meta_cells = [
            ('日期', ts[:16] if ts else 'N/A'),
            ('后端 / 模型', f'{rec.get("backend","?")} · {rec.get("model","?")}'),
            ('数据源', ', '.join(rec.get('selected_sources', ['未指定']))),
            ('出具方', '数据科学家 · 审核顾问 · 首席传感专家'),
            ('数据对象', f'{rec.get("data_source_snapshot",[""])[0][:60] if rec.get("data_source_snapshot") else "见正文"}'),
        ]
        for ri, (label, val) in enumerate(meta_cells):
            meta_table.cell(ri, 0).text = label
            meta_table.cell(ri, 1).text = str(val)[:120]
            for p in meta_table.cell(ri, 0).paragraphs:
                for run in p.runs:
                    run.font.bold = True
                    run.font.size = Pt(9)
            for p in meta_table.cell(ri, 1).paragraphs:
                for run in p.runs:
                    run.font.size = Pt(9)
        # 无边框
        for ri in range(5):
            for ci in range(2):
                tcPr = meta_table.cell(ri, ci)._element.get_or_add_tcPr()
                tcBorders = tcPr.makeelement(qn('w:tcBorders'), {})
                for edge in ['top', 'left', 'bottom', 'right']:
                    el = tcBorders.makeelement(qn(f'w:{edge}'), {
                        qn('w:val'): 'nil'})
                    tcBorders.append(el)
                tcPr.append(tcBorders)
        doc.add_paragraph()

        # ── 提取诊断 ──
        ai_d = rec.get('ai_diagnosis') or {}
        diag = ai_d.get('diagnosis_json') or {}
        ma = rec.get('multi_agent') or {}
        if not diag:
            diag = ma.get('chief_structured') or {}

        # ── 多体: 目录 ──
        chief_section_title = '一、首席专家综合结论' if kind == "multi" else '诊断摘要'
        if kind == "multi":
            doc.add_heading('目  录', level=1)
            toc_items = [
                '一、首席专家综合结论',
                '二、数据科学家详细分析',
                '三、审核顾问意见',
            ]
            for item in toc_items:
                p = doc.add_paragraph(item)
                p.paragraph_format.left_indent = Cm(1)
            doc.add_paragraph()
            doc.add_page_break()

        # ── 首席结论 / 诊断摘要 ──
        doc.add_heading(chief_section_title, level=1)

        # 数据质量评级
        if diag.get('diagnosis_summary'):
            s = diag['diagnosis_summary']
            doc.add_heading('1.1 数据质量评级', level=2)
            self._add_styled_table_docx(
                doc,
                ['指标', '值'],
                [[k, str(v)] for k, v in [
                    ('数据类型', s.get('data_type', '?')),
                    ('模板', s.get('template_name', '?')),
                    ('数据质量', s.get('data_quality', '?')),
                    ('异常点数', str(s.get('anomaly_count', '?'))),
                ]],
                caption='数据质量概况',
                table_id=self._next_docx_table_id(),
            )
            # 总体研判 → 移出表格，做高亮段落
            overall = s.get('overall_assessment', '')
            if overall:
                doc.add_heading('1.2 总体研判', level=2)
                p = doc.add_paragraph()
                run = p.add_run(overall[:500])
                run.font.size = Pt(10.5)
                p.paragraph_format.left_indent = Cm(0.5)
                # 引用框样式
                pPr = p._element.get_or_add_pPr()
                from docx.oxml.ns import qn as _qn3
                pBdr = pPr.makeelement(_qn3('w:pBdr'), {})
                left_border = pBdr.makeelement(_qn3('w:left'), {
                    _qn3('w:val'): 'single', _qn3('w:sz'): '12',
                    _qn3('w:color'): '1890ff', _qn3('w:space'): '8'})
                pBdr.append(left_border)
                pPr.append(pBdr)
                doc.add_paragraph()

        # 传感器分级结论
        sa = diag.get('sensor_analysis', [])
        if sa:
            doc.add_heading('1.3 传感器分级结论', level=2)
            headers = ['传感器', '状态', '均值', '范围', '发现', '建议']
            rows_data = []
            for s in sa:
                stats = s.get('statistics', {}) or {}
                rows_data.append([
                    str(s.get('sensor_id', '?')),
                    str(s.get('status', '?')),
                    f"{stats.get('mean', '?')}" if isinstance(
                        stats.get('mean'), (int, float)) else '?',
                    f"[{stats.get('min','?')}-{stats.get('max','?')}]"
                    if isinstance(stats.get('min'), (int, float)) else '?',
                    str(s.get('findings', ''))[:80],
                    str(s.get('suggestions', ''))[:80],
                ])
            self._add_styled_table_docx(
                doc, headers, rows_data,
                caption='传感器分析',
                table_id=self._next_docx_table_id(),
            )

        # 物理诊断要点
        pd = diag.get('physical_diagnosis', {})
        if pd and pd.get('phenomenon'):
            doc.add_heading('1.4 物理诊断要点', level=2)
            doc.add_paragraph(f"现象: {pd.get('phenomenon', '')}")
            for c in pd.get('possible_causes', []):
                doc.add_paragraph(f'  - {c}', style='List Bullet')
            for a in pd.get('recommended_actions', []):
                p = doc.add_paragraph()
                p.add_run(f'→ {a}').font.italic = True
            doc.add_paragraph()

        # 核心建议
        dq = diag.get('data_quality_assessment', {})
        recs = dq.get('recommendations', [])
        if recs:
            doc.add_heading('1.5 核心建议', level=2)
            for r in recs:
                p = doc.add_paragraph()
                p.add_run(f'→ {r}').font.italic = True
            doc.add_paragraph()

        # 命中诊断规则
        kh = rec.get('kb_hits', [])
        if kh:
            doc.add_heading('1.6 命中诊断规则', level=2)
            rows_data = [
                [f"{h['id']} [{h['severity']}]",
                 str(h.get('meaning', ''))[:120],
                 str(h.get('recommendation', ''))[:120]]
                for h in kh
            ]
            self._add_styled_table_docx(
                doc, ['规则', '要点', '建议'], rows_data,
                caption='KB 诊断规则命中',
                table_id=self._next_docx_table_id(),
            )

        # ── 多智能体段 ──
        if kind == "multi":
            # 截断警告
            if ma.get('chief_truncated'):
                p = doc.add_paragraph()
                run = p.add_run(
                    '⚠ 首席综合层输出被截断 — '
                    '下方数据科学家详报与审核顾问意见完整保留。')
                run.font.color.rgb = RGBColor(0x8c, 0x69, 0x00)
                run.font.size = Pt(10)
                run.font.italic = True
                doc.add_paragraph()

            # 二、数据科学家详细分析
            ds_text = ma.get('data_scientist_text', '')
            if ds_text:
                doc.add_heading('二、数据科学家详细分析', level=1)
                self._render_markdown_blocks_to_docx(doc, ds_text)
                doc.add_paragraph()

            # 三、审核顾问意见
            advisory = ma.get('audit_advisory', '')
            if advisory:
                doc.add_heading('三、审核顾问意见', level=1)
                self._render_markdown_blocks_to_docx(doc, advisory)
                doc.add_paragraph()

        # ── 数据汇总附表 (从 chart_data 提取全部结构化表) ──
        try:
            from core.chart_bundle import extract_four_tables
            cd = rec.get('chart_data', {}) or {}
            four_tables = extract_four_tables(cd)
            if four_tables:
                doc.add_heading('数据汇总附表', level=1)
                for tbl in four_tables:
                    self._add_styled_table_docx(
                        doc, tbl.headers, tbl.rows,
                        caption=tbl.heading,
                        table_id=self._next_docx_table_id(),
                    )
        except Exception:
            import traceback as _tb2
            print(f"[诊断docx] 数据汇总附表注入失败: {_tb2.format_exc()}")

        # ── 页码 ──
        for section in doc.sections:
            section.top_margin = Cm(2.5)
            section.bottom_margin = Cm(2.5)
            section.left_margin = Cm(2.5)
            section.right_margin = Cm(2.5)
            footer = section.footer
            footer.is_linked_to_previous = False
            fp = footer.paragraphs[0] if footer.paragraphs else footer.add_paragraph()
            fp.alignment = WD_ALIGN_PARAGRAPH.CENTER
            run = fp.add_run('— ')
            run.font.size = Pt(8)
            # 插入页码域
            fldChar1 = OxmlElement('w:fldChar')
            fldChar1.set(qn('w:fldCharType'), 'begin')
            run._element.append(fldChar1)
            instrText = OxmlElement('w:instrText')
            instrText.set(qn('xml:space'), 'preserve')
            instrText.text = ' PAGE '
            run._element.append(instrText)
            fldChar2 = OxmlElement('w:fldChar')
            fldChar2.set(qn('w:fldCharType'), 'end')
            run._element.append(fldChar2)
            run2 = fp.add_run(' —')
            run2.font.size = Pt(8)

        doc.save(output_path)

    # ═══════════════════════════════════════════════
    # AI 聊天
    # ═══════════════════════════════════════════════

    def _chat_query(self) -> None:
        text = self.ai_chat_input.text().strip()
        if not text:
            return
        from core.ai_client import AIClient
        if not AIClient.get_instance().is_available():
            self._warn(self, '请先配置或连接AI模型')
            return

        self.ai_chat_text.append(f'<b>用户:</b> {text}')
        self.ai_chat_input.clear()
        QApplication.processEvents()

        try:
            self._chat_thread = AIClientStreamThread(
                prompt=text,
                temperature=self._online_config.get('temperature', 0.7) if hasattr(self, '_online_config') and self._online_config else 0.7,
                max_tokens=self._online_config.get('max_tokens', 1024) if hasattr(self, '_online_config') and self._online_config else 1024,
                system_prompt=self._online_config.get('system_prompt', '') if hasattr(self, '_online_config') and self._online_config else '',
                enable_thinking=False,  # 聊天默认非思考（回复更直接）
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
        mr = self._multi_result or {}
        # Phase 5: 从定型字段重建报告 (向后兼容无 report 字段的旧版)
        if mr.get("report"):
            return mr["report"]  # 旧版 record 仍有 report
        ds = mr.get("data_scientist_text", "")
        advisory = mr.get("audit_advisory", "")
        chief = mr.get("chief_structured")
        chief_raw = json.dumps(chief, ensure_ascii=False, indent=2) if chief else ""
        parts = []
        if ds:
            parts.append(f"## 数据科学家详报\n\n{ds}")
        if advisory:
            parts.append(f"## 审核顾问意见\n\n{advisory}")
        if chief_raw:
            parts.append(f"## 首席专家综合报告\n\n{chief_raw}")
        return "\n\n---\n\n".join(parts) if parts else ""

    def _update_result_buttons(self) -> None:
        """根据当前存了什么结果，更新切换/保存按钮的启用/高亮状态。"""
        has_ai = self._ai_result is not None
        has_multi = self._multi_result is not None
        if hasattr(self, 'result_toggle_ai'):
            self.result_toggle_ai.setEnabled(has_ai)
        if hasattr(self, 'result_toggle_multi'):
            self.result_toggle_multi.setEnabled(has_multi)
        if hasattr(self, 'save_ai_btn'):
            self.save_ai_btn.setEnabled(has_ai)
        if hasattr(self, 'save_multi_btn'):
            self.save_multi_btn.setEnabled(has_multi)
        # 美化当前选中按钮
        active = self._active_result
        if hasattr(self, 'result_toggle_ai'):
            self.result_toggle_ai.setStyleSheet(_TOGGLE_ON_QSS if active == 'ai'
                                               else _TOGGLE_OFF_QSS)
        if hasattr(self, 'result_toggle_multi'):
            self.result_toggle_multi.setStyleSheet(_TOGGLE_ON_QSS if active == 'multi'
                                                   else _TOGGLE_OFF_QSS)

    def _switch_to_ai_result(self) -> None:
        self._active_result = "ai"
        self._update_result_buttons()
        ai = self._ai_result or {}
        if ai.get("json"):
            try:
                kb_hits = self._get_kb_hits()
                html = self._render_chief_conclusions_html(ai["json"], kb_hits, kind="ai")
                self.ai_diagnosis_result.setHtml(html)
                return
            except Exception:
                pass
        self.ai_diagnosis_result.setPlainText(ai.get("raw", ""))
        self.ai_terminal_title.setText("AI 诊断结果")

    def _switch_to_multi_result(self) -> None:
        self._active_result = "multi"
        self._update_result_buttons()
        mr = self._multi_result or {}

        # 优先用预解析的 chief_structured (SSOT)
        chief_structured = mr.get("chief_structured")
        if chief_structured:
            try:
                html_parts: list[str] = []

                # ── 文档标题 ──
                html_parts.append(
                    '<div style="background:linear-gradient(135deg,#0d2137 0%,#1a4971 100%);'
                    'color:white;padding:18px 24px;border-radius:8px;margin-bottom:8px;">'
                    '<h1 style="margin:0 0 4px 0;font-size:20px;">'
                    '专家组诊断报告</h1>'
                    '<div style="font-size:12px;opacity:0.85;">'
                    f'由 数据科学家 · 审核顾问 · 首席传感专家 联合出具'
                    '</div>'
                    '</div>'
                )

                # ── 截断警告 ──
                if mr.get("chief_truncated"):
                    html_parts.append(
                        '<div style="background:#fff7e6;border-left:4px solid #faad14;'
                        'padding:10px 14px;border-radius:0 4px 4px 0;margin-bottom:12px;'
                        'font-size:12px;color:#8c6900;">'
                        '<strong>⚠ 首席综合层输出被截断</strong> — '
                        '下方数据科学家详报与审核顾问意见完整保留。'
                        '</div>'
                    )

                # ── 一、首席专家综合结论 ──
                kb_hits = self._get_kb_hits()
                html_parts.append(
                    self._render_chief_conclusions_html(chief_structured, kb_hits, kind="multi"))

                # ── 二、数据科学家详细分析 ──
                ds_text = mr.get("data_scientist_text", "")
                if ds_text:
                    html_parts.append(
                        '<div style="margin-top:16px;">'
                        '<div style="background:#f0f5ff;padding:10px 16px;'
                        'border-left:4px solid #1890ff;border-radius:0 4px 4px 0;">'
                        '<h2 style="margin:0;font-size:16px;color:#1a365d;">'
                        '二、数据科学家详细分析</h2>'
                        '</div>'
                        '<div style="padding:8px 16px;font-size:13px;">'
                        f'{self._render_markdown_to_html(ds_text)}'
                        '</div>'
                        '</div>'
                    )

                # ── 三、审核顾问意见 ──
                advisory = mr.get("audit_advisory", "")
                if advisory:
                    html_parts.append(
                        '<div style="margin-top:16px;">'
                        '<div style="background:#f6ffed;padding:10px 16px;'
                        'border-left:4px solid #52c41a;border-radius:0 4px 4px 0;">'
                        '<h2 style="margin:0;font-size:16px;color:#389e0d;">'
                        '三、审核顾问意见</h2>'
                        '</div>'
                        '<div style="padding:8px 16px;font-size:13px;">'
                        f'{self._render_markdown_to_html(advisory)}'
                        '</div>'
                        '</div>'
                    )

                self.ai_diagnosis_result.setHtml('\n'.join(html_parts))
                return
            except Exception:
                pass

        # Fallback
        chief_raw = mr.get("raw_json", {}).get("chief_scientist_report",
                    mr.get("raw_json", {}).get("chief_report", ""))
        if chief_raw:
            self.ai_diagnosis_result.setMarkdown(str(chief_raw))
        else:
            self.ai_diagnosis_result.setPlainText(
                mr.get("data_scientist_text", "") or "(无多智能体结果)"
            )
        self.ai_terminal_title.setText("专家组诊断结果")
