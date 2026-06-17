"""AI 模型配置对话框"""

from __future__ import annotations

from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit,
    QListWidget, QPushButton, QMessageBox,
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

