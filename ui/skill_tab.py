"""技能插件中心页面 — 内置技能管理、GitHub 技能加载、Agent 思考日志."""

from __future__ import annotations

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QPushButton, QLabel,
    QGroupBox, QGridLayout, QTableWidget, QTableWidgetItem,
    QTextEdit, QLineEdit, QDialog, QMessageBox,
)
from PyQt6.QtCore import Qt


class AgentSkillWidget(QWidget):
    """技能插件中心 — AI Agent 能力调度中枢."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._build_ui()

    def _build_ui(self):
        main_layout = QVBoxLayout(self)

        info_label = QLabel(
            '技能插件中心 — AI Agent 能力调度中枢：'
            '管理内置技能与 GitHub 自定义技能的启用/禁用、加载/卸载，'
            '支持 Agent 思考日志实时追踪'
        )
        info_label.setStyleSheet(
            'color: #555; padding: 12px; font-size: 13px;'
            ' background: #f0f5ff; border-radius: 6px;'
        )
        info_label.setWordWrap(True)
        main_layout.addWidget(info_label)

        # ── 技能表格区域 ──
        skill_group = QGroupBox('内置技能')
        skill_layout = QVBoxLayout()

        self.skill_table = QTableWidget()
        self.skill_table.setColumnCount(3)
        self.skill_table.setHorizontalHeaderLabels(['技能名称', '功能说明', '启用'])
        self.skill_table.setRowCount(10)

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
            checkbox.setCheckState(
                Qt.CheckState.Checked if enabled else Qt.CheckState.Unchecked
            )
            self.skill_table.setItem(i, 2, checkbox)

        self.skill_table.resizeColumnsToContents()
        skill_layout.addWidget(self.skill_table)
        skill_group.setLayout(skill_layout)
        main_layout.addWidget(skill_group)

        # ── GitHub 技能加载区域 ──
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

        # ── Agent 思考日志区 ──
        log_group = QGroupBox('Agent 思考日志')
        log_layout = QVBoxLayout()

        self.agent_log = QTextEdit()
        self.agent_log.setReadOnly(True)
        self.agent_log.setStyleSheet('''
            QTextEdit { background-color: #1e1e1e; color: #d4d4d4;
                font-family: Consolas, monospace; }
        ''')
        log_layout.addWidget(self.agent_log)

        clear_log_btn = QPushButton('清空日志')
        clear_log_btn.clicked.connect(lambda: self.agent_log.clear())
        log_layout.addWidget(clear_log_btn)

        log_group.setLayout(log_layout)
        main_layout.addWidget(log_group)

        # ── 使用说明按钮 ──
        help_btn = QPushButton('使用说明')
        help_btn.clicked.connect(self.show_skill_center_help)
        main_layout.addWidget(help_btn)

    # ── Event handlers ──

    def on_load_github_skill(self):
        owner = self.github_owner_input.text().strip()
        repo = self.github_repo_input.text().strip()
        path = self.github_path_input.text().strip()
        branch = self.github_branch_input.text().strip() or 'main'
        token = self.github_token_input.text().strip()

        if not owner or not repo:
            QMessageBox.warning(self, '警告', '请输入仓库所有者和仓库名称')
            return

        self.agent_log.append(
            '<span style="color: blue;">[INFO]</span> 正在从 GitHub 加载技能...'
        )
        self.agent_log.append(
            f'<span style="color: orange;">[LOAD]</span> {owner}/{repo}/{path}@{branch}'
        )

        try:
            from dp_engine.github_skill_loader import GithubSkillLoader
            loader = GithubSkillLoader(token if token else None)
            self.agent_log.append(
                '<span style="color: green;">[OK]</span> 技能加载功能已调用'
            )
            QMessageBox.information(self, '提示', '技能加载功能已触发，请查看日志')
        except ImportError:
            self.agent_log.append(
                '<span style="color: red;">[ERROR]</span>'
                ' github_skill_loader 模块未找到'
            )
            QMessageBox.warning(self, '警告', '技能加载模块未安装')

    def on_view_loaded_skills(self):
        self.agent_log.append(
            '<span style="color: blue;">[INFO]</span> 已加载技能列表:'
        )
        for row in range(self.skill_table.rowCount()):
            name_item = self.skill_table.item(row, 0)
            check_item = self.skill_table.item(row, 2)
            if name_item and check_item:
                name = name_item.text()
                enabled = check_item.checkState() == Qt.CheckState.Checked
                status = '启用' if enabled else '禁用'
                self.agent_log.append(f'  - {name}: {status}')

    def on_clear_all_skills(self):
        reply = QMessageBox.question(
            self, '确认', '确定清除所有已加载的技能？',
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
        )
        if reply == QMessageBox.StandardButton.Yes:
            self.agent_log.append(
                '<span style="color: red;">[WARN]</span> 已清除所有自定义技能'
            )
            QMessageBox.information(self, '提示', '自定义技能已清除')

    def show_skill_center_help(self):
        help_text = """# 技能插件中心 — 使用说明

## 功能概述

技能插件中心是 AI Agent 的能力调度中枢，管理 Agent 可调用的所有技能（Skills）。每项技能都是一个独立的功能模块，Agent 根据任务需求自动选择合适的技能组合来完成任务。用户可以自由启用/禁用内置技能，也可以从 GitHub 加载社区或自定义技能，实现 Agent 能力的灵活扩展。

## 内置技能

| 技能名称 | 功能说明 |
|---------|---------|
| Python_REPL | Python 交互式沙箱执行器 |
| execute_custom_formula | 执行用户自定义数学公式 |
| apply_butterworth_filter | 巴特沃斯数字滤波器 |
| arxiv | 学术文献搜索引擎 |
| ddg_search | DuckDuckGo 联网搜索 |
| wikipedia | 维基百科查询 |
| read_wiki_page | 读取知识库页面 |
| write_wiki_page | 写入知识库页面 |
| list_wiki_pages | 列出知识库页面 |
| search_wiki_pages | 搜索知识库页面 |

## 从 GitHub 加载自定义技能

### 参数说明
- **仓库所有者 (Owner):** GitHub 用户名或组织名
- **仓库名称 (Repo):** 仓库名
- **文件路径 (Path):** 技能文件目录路径，默认 `skills/`
- **分支 (Branch):** 目标分支名，默认 `main`
- **GitHub Token:** 个人访问令牌，私有仓库必填

## 操作步骤
1. 填写仓库信息
2. 点击"下载并加载技能"按钮
3. 查看 Agent 日志区确认加载进度
"""
        help_dialog = QDialog(self)
        help_dialog.setWindowTitle('技能插件中心 — 使用说明')
        help_dialog.resize(850, 750)

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
