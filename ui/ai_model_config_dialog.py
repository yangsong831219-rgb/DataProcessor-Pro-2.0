"""AI 模型配置对话框 — 在线/本地后端 + 模型管理"""

from __future__ import annotations

from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit,
    QListWidget, QPushButton, QMessageBox, QGroupBox, QFrame,
    QWidget,
)
from PyQt6.QtCore import Qt

# ═══════════════════════════════════════════════════════════════════
# QSS 常量
# ═══════════════════════════════════════════════════════════════════

_DIALOG_QSS = "QDialog { background-color: #f0f2f5; }"

_GROUP_QSS = (
    "QGroupBox {"
    "  font-size: 13px; font-weight: bold; color: #333;"
    "  border: 1px solid #e8e8e8; border-radius: 8px;"
    "  margin-top: 12px; padding: 16px 12px 12px 12px;"
    "  background-color: white;"
    "}"
    "QGroupBox::title {"
    "  subcontrol-origin: margin; subcontrol-position: top left;"
    "  padding: 0 8px; left: 12px;"
    "}"
)

_INPUT_QSS = (
    "QLineEdit {"
    "  border: 1px solid #d9d9d9; border-radius: 4px;"
    "  padding: 8px 12px; font-size: 13px; background: white;"
    "  min-height: 22px;"
    "}"
    "QLineEdit:focus { border-color: #1890ff; }"
)

_BTN_PRIMARY = (
    "QPushButton { background-color: #1890ff; color: white; border: none;"
    "  border-radius: 4px; padding: 8px 16px; font-weight: bold; font-size: 13px; }"
    "QPushButton:hover { background-color: #40a9ff; }"
)

_BTN_SUCCESS = (
    "QPushButton { background-color: #52c41a; color: white; border: none;"
    "  border-radius: 4px; padding: 8px 16px; font-weight: bold; font-size: 13px; }"
    "QPushButton:hover { background-color: #73d13d; }"
)

_BTN_WARNING = (
    "QPushButton { background-color: #faad14; color: white; border: none;"
    "  border-radius: 4px; padding: 8px 16px; font-weight: bold; font-size: 13px; }"
    "QPushButton:hover { background-color: #ffc53d; }"
)

_BTN_DANGER = (
    "QPushButton { background-color: #ff4d4f; color: white; border: none;"
    "  border-radius: 4px; padding: 8px 16px; font-weight: bold; font-size: 13px; }"
    "QPushButton:hover { background-color: #ff7875; }"
)

_BTN_DEFAULT = (
    "QPushButton { background-color: #f0f0f0; color: #333; border: 1px solid #d9d9d9;"
    "  border-radius: 4px; padding: 8px 16px; font-size: 13px; }"
    "QPushButton:hover { background-color: #e6e6e6; }"
)

_LABEL_QSS = "font-size: 13px; color: #555;"

_STATUS_BAR_QSS = (
    "font-size: 14px; font-weight: bold; padding: 8px 14px;"
    "background: white; border: 1px solid #e8e8e8; border-radius: 6px;"
)


class AIModelConfigDialog(QDialog):
    """AI 模型配置对话框 — 管理在线/本地后端与模型条目"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle('AI 模型配置')
        self.setMinimumWidth(700)
        self.setMinimumHeight(520)
        self.setStyleSheet(_DIALOG_QSS)
        self.parent_window = parent

        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(24, 20, 24, 20)
        main_layout.setSpacing(16)

        # ═══════════════════════════════════════════════════════════
        # 状态条 — 当前后端 / 模型
        # ═══════════════════════════════════════════════════════════
        self.status_bar = QLabel()
        self.status_bar.setStyleSheet(_STATUS_BAR_QSS)
        main_layout.addWidget(self.status_bar)

        # ═══════════════════════════════════════════════════════════
        # 本地后端配置区 (默认隐藏)
        # ═══════════════════════════════════════════════════════════
        self.local_group = QGroupBox("本地后端配置 (llama.cpp / Ollama)")
        self.local_group.setStyleSheet(_GROUP_QSS)
        local_layout = QVBoxLayout(self.local_group)
        local_layout.setSpacing(10)

        self.local_url = QLineEdit()
        self._add_field(local_layout, "服务地址:", self.local_url,
                        "http://127.0.0.1:8080/v1")
        self.local_model = QLineEdit()
        self._add_field(local_layout, "模型名称:", self.local_model,
                        "qwen3.5-9b")

        health_row = QHBoxLayout()
        health_btn = QPushButton("检测服务")
        health_btn.setStyleSheet(_BTN_WARNING)
        health_btn.clicked.connect(self._on_local_health_check)
        health_row.addWidget(health_btn)
        self.local_health_label = QLabel("")
        self.local_health_label.setStyleSheet("font-size: 12px;")
        health_row.addWidget(self.local_health_label)
        health_row.addStretch()
        local_layout.addLayout(health_row)

        self.local_group.setVisible(False)
        main_layout.addWidget(self.local_group)

        # ═══════════════════════════════════════════════════════════
        # 在线模型列表
        # ═══════════════════════════════════════════════════════════
        self.online_group = QGroupBox("在线模型配置")
        self.online_group.setStyleSheet(_GROUP_QSS)
        online_layout = QHBoxLayout(self.online_group)

        self.model_list = QListWidget()
        self.model_list.setMinimumWidth(180)
        self.model_list.setStyleSheet(
            "QListWidget { border: 1px solid #e8e8e8; border-radius: 4px;"
            "  background: white; font-size: 13px; }"
            "QListWidget::item { padding: 8px 12px; min-height: 24px; }"
            "QListWidget::item:hover { background: #f0f2f5; }"
            "QListWidget::item:selected { background: #e6f7ff; color: #333; }"
        )
        self.model_list.itemClicked.connect(self._on_model_item_clicked)
        online_layout.addWidget(self.model_list)

        form = QWidget()
        form_layout = QVBoxLayout(form)
        form_layout.setSpacing(10)

        self.name_input = QLineEdit()
        self._add_field(form_layout, "配置名称:", self.name_input, "给这个配置起个名字")
        self.url_input = QLineEdit()
        self._add_field(form_layout, "API 地址:", self.url_input, "https://api.deepseek.com/v1")
        self.key_input = QLineEdit()
        self._add_field(form_layout, "API Key:", self.key_input, "sk-...")
        self.key_input.setEchoMode(QLineEdit.EchoMode.Password)
        self.model_input = QLineEdit()
        self._add_field(form_layout, "模型名称:", self.model_input, "deepseek-v4-pro")
        self.sys_input = QLineEdit()
        self._add_field(form_layout, "系统提示:", self.sys_input, "你是一个...")

        online_layout.addWidget(form, stretch=1)
        main_layout.addWidget(self.online_group)

        # ═══════════════════════════════════════════════════════════
        # 按钮行
        # ═══════════════════════════════════════════════════════════
        btn_row = QHBoxLayout()
        btn_row.setSpacing(10)

        add_btn = QPushButton("新建")
        add_btn.setStyleSheet(_BTN_SUCCESS)
        add_btn.clicked.connect(self._on_add_model)
        btn_row.addWidget(add_btn)

        save_btn = QPushButton("保存")
        save_btn.setStyleSheet(_BTN_PRIMARY)
        save_btn.clicked.connect(self._on_save_model)
        btn_row.addWidget(save_btn)

        save_local_btn = QPushButton("保存本地配置")
        save_local_btn.setStyleSheet(_BTN_PRIMARY)
        save_local_btn.clicked.connect(self._on_save_local)
        btn_row.addWidget(save_local_btn)

        test_btn = QPushButton("测试")
        test_btn.setStyleSheet(_BTN_WARNING)
        test_btn.clicked.connect(self._on_test_model)
        btn_row.addWidget(test_btn)

        delete_btn = QPushButton("删除")
        delete_btn.setStyleSheet(_BTN_DANGER)
        delete_btn.clicked.connect(self._on_delete_model)
        btn_row.addWidget(delete_btn)

        close_btn = QPushButton("关闭")
        close_btn.setStyleSheet(_BTN_DEFAULT)
        close_btn.clicked.connect(self.close)
        btn_row.addWidget(close_btn)

        main_layout.addLayout(btn_row)
        self.setLayout(main_layout)

        self._load_configs()

    def _add_field(self, layout, label_text: str, input_widget: QLineEdit, placeholder: str):
        row = QHBoxLayout()
        row.setSpacing(8)
        lbl = QLabel(label_text)
        lbl.setStyleSheet(_LABEL_QSS)
        lbl.setFixedWidth(70)
        row.addWidget(lbl)
        input_widget.setPlaceholderText(placeholder)
        input_widget.setStyleSheet(_INPUT_QSS)
        row.addWidget(input_widget, stretch=1)
        layout.addLayout(row)

    # ═══════════════════════════════════════════════════════════════
    # 加载 / 刷新
    # ═══════════════════════════════════════════════════════════════

    def _load_configs(self):
        """加载所有配置并刷新 UI."""
        self._load_online_models()
        self._load_local_config()
        self._update_status_bar()

    def _load_online_models(self):
        self.model_list.clear()
        cfg = self._get_parent_config()
        for name in cfg:
            if not name.startswith('_') and isinstance(cfg[name], dict) and 'base_url' in cfg[name]:
                self.model_list.addItem(name)

    def _load_local_config(self):
        cfg = self._get_parent_config()
        local = cfg.get('_local', {}) or {}
        self.local_url.setText(str(local.get('base_url', 'http://127.0.0.1:8080/v1')))
        self.local_model.setText(str(local.get('model_name', 'qwen3.5-9b')))

    def _update_status_bar(self):
        from core.ai_client import AIClient
        backend = AIClient.get_backend()
        if backend == 'local':
            lc = AIClient.get_local_config()
            mname = lc.get('model_name', 'qwen3.5-9b')
            self.status_bar.setText(f"● 本地 · {mname}")
            self.status_bar.setStyleSheet(_STATUS_BAR_QSS + "color: #52c41a;")
            self.local_group.setVisible(True)
        else:
            mname = "未知"
            if self.model_list.currentItem():
                mname = self.model_list.currentItem().text()
            self.status_bar.setText(f"● 在线 · {mname}")
            self.status_bar.setStyleSheet(_STATUS_BAR_QSS + "color: #1890ff;")
            self.local_group.setVisible(False)

    def _get_parent_config(self) -> dict:
        if not hasattr(self.parent_window, 'ai_models_config'):
            self.parent_window.ai_models_config = {}
        return self.parent_window.ai_models_config

    # ═══════════════════════════════════════════════════════════════
    # 交互
    # ═══════════════════════════════════════════════════════════════

    def _on_model_item_clicked(self, item):
        name = item.text()
        cfg = self._get_parent_config()
        if name in cfg:
            c = cfg[name]
            self.name_input.setText(name)
            self.url_input.setText(c.get('base_url', ''))
            self.key_input.setText(c.get('api_key', ''))
            self.model_input.setText(c.get('model_name', ''))
            self.sys_input.setText(c.get('system_prompt', ''))
            self._update_status_bar()

    def _on_add_model(self):
        self.name_input.clear()
        self.url_input.clear()
        self.key_input.clear()
        self.model_input.clear()
        self.sys_input.clear()
        self.name_input.setFocus()

    def _on_save_model(self):
        name = self.name_input.text().strip()
        if not name:
            QMessageBox.warning(self, '警告', '请输入配置名称')
            return
        config = {
            'base_url': self.url_input.text().strip(),
            'api_key': self.key_input.text().strip(),
            'model_name': self.model_input.text().strip(),
            'system_prompt': self.sys_input.text().strip(),
        }
        cfg = self._get_parent_config()
        cfg[name] = config
        self.parent_window.save_ai_models_config()
        if hasattr(self.parent_window, 'refresh_ai_model_selector'):
            self.parent_window.refresh_ai_model_selector()
        self._load_online_models()
        self._update_status_bar()
        QMessageBox.information(self, '成功', '在线模型配置已保存')

    def _on_save_local(self):
        import json, os
        cfg_path = os.path.join(
            os.path.dirname(os.path.dirname(__file__)), 'ai_models_config.json'
        )
        cfg = {}
        if os.path.exists(cfg_path):
            with open(cfg_path, 'r', encoding='utf-8') as f:
                cfg = json.load(f)
        cfg.setdefault('_local', {})
        cfg['_local']['base_url'] = self.local_url.text().strip() or 'http://127.0.0.1:8080/v1'
        cfg['_local']['model_name'] = self.local_model.text().strip() or 'qwen3.5-9b'
        with open(cfg_path, 'w', encoding='utf-8') as f:
            json.dump(cfg, f, ensure_ascii=False, indent=2)

        from core.ai_client import AIClient
        AIClient._instance = None
        self._update_status_bar()
        QMessageBox.information(self, '成功', '本地后端配置已保存')

    def _on_local_health_check(self):
        import requests
        url = self.local_url.text().strip()
        if not url:
            self.local_health_label.setText("请填写服务地址")
            return
        host = url.removesuffix('/v1').removesuffix('/')
        self.local_health_label.setText("检测中...")
        try:
            r = requests.get(f'{host}/health', timeout=5)
            if r.status_code == 200:
                self.local_health_label.setText("服务正常")
                self.local_health_label.setStyleSheet("color: #52c41a; font-weight: bold; font-size: 12px;")
                return
        except Exception:
            pass
        try:
            r = requests.get(f'{host}/api/tags', timeout=5)
            if r.status_code == 200:
                self.local_health_label.setText("Ollama 正常")
                self.local_health_label.setStyleSheet("color: #52c41a; font-weight: bold; font-size: 12px;")
                return
        except Exception:
            pass
        self.local_health_label.setText("不可达")
        self.local_health_label.setStyleSheet("color: #ff4d4f; font-weight: bold; font-size: 12px;")

    def _on_test_model(self):
        from core.ai_client import AIClient
        AIClient._instance = None
        ai = AIClient.get_instance()

        if ai.backend == 'local':
            try:
                ai.health_check(timeout=5.0)
                QMessageBox.information(self, '成功',
                    f"本地服务正常\n模型: {ai.model_name}\n地址: {ai.base_url}")
                return
            except Exception as e:
                QMessageBox.warning(self, '错误', f"本地服务不可达: {e}")
                return

        api_url = self.url_input.text().strip()
        api_key = self.key_input.text().strip()
        model_name = self.model_input.text().strip()
        if not api_url or not model_name:
            QMessageBox.warning(self, '警告', '请填写API地址和模型名称')
            return
        try:
            import requests
            headers = {'Authorization': f'Bearer {api_key}',
                       'Content-Type': 'application/json'} if api_key else {'Content-Type': 'application/json'}
            api_url = api_url.rstrip('/')
            if not api_url.endswith('/v1'):
                api_url += '/v1'
            data = {'model': model_name, 'messages': [{'role': 'user', 'content': 'Say "OK".'}], 'max_tokens': 10}
            r = requests.post(f'{api_url}/chat/completions', headers=headers, json=data, timeout=30)
            if r.status_code == 200:
                QMessageBox.information(self, '成功', '在线模型测试成功!')
            else:
                QMessageBox.warning(self, '错误', f'测试失败: {r.status_code}\n{r.text[:200]}')
        except Exception as e:
            QMessageBox.warning(self, '错误', f'测试失败: {e}')

    def _on_delete_model(self):
        name = self.name_input.text().strip()
        if not name:
            QMessageBox.warning(self, '警告', '请先选择一个要删除的配置')
            return
        reply = QMessageBox.question(self, '确认', f'确定要删除"{name}"吗?')
        if reply == QMessageBox.StandardButton.Yes:
            cfg = self._get_parent_config()
            if name in cfg:
                del cfg[name]
                self.parent_window.save_ai_models_config()
                if hasattr(self.parent_window, 'refresh_ai_model_selector'):
                    self.parent_window.refresh_ai_model_selector()
                self._load_online_models()
                self._on_add_model()
                self._update_status_bar()
                QMessageBox.information(self, '成功', '已删除')

    def close(self):
        super().accept()
